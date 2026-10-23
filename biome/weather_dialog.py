# -*- coding: utf-8 -*-
"""
weather_dialog.py
=================

Pop-out *Survey Weather* checker, launched from the Desk Study tab. The surveyor
sets a date, time and location; the dialog fetches the past hourly weather from
Open-Meteo (see ``weather_data``) and fills the report's "weather conditions at
the time of survey" table — temperature (°C), wind (mph) and precipitation (mm)
for the hour of the survey.

Because the data is hourly, the survey hour is reported and the surrounding
hours (± 2 h) are shown alongside, so a time that falls between hours is still
accurate to within the hour. The three Detail cells are editable before copying,
and a one-click copy lands them in the Google Docs report table in house style.

Modelled on the GCN HSI dialog: themed header, scrollable card body, copy-to-
clipboard footer. Nothing is written to any layer.
"""

import os
import datetime as _dt

from qgis.PyQt.QtCore import Qt, QDate, QTime, QSettings
from qgis.PyQt.QtGui import QCursor, QColor, QDoubleValidator
from qgis.PyQt.QtWidgets import (
    QDialog, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QFrame, QPushButton, QLineEdit, QDateEdit, QTimeEdit,
    QApplication, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView)

from . import theme
from . import weather_data as W

# Condition rows of the report table, in template order.
CONDITIONS = ["Temperature", "Wind", "Precipitation"]


class WeatherDialog(QDialog):

    def __init__(self, parent=None, default_latlon=None):
        super().__init__(parent)
        self._default_latlon = default_latlon   # (lat, lon) from the redline, or None
        self._rows = None                        # last fetched 24 hourly rows
        self._survey_hour = None                 # snapped survey hour (0–23)

        self.setWindowTitle("Survey Weather")
        self.setStyleSheet(
            "QDialog { background:%s; }"
            " QLabel, QPushButton, QLineEdit, QDateEdit, QTimeEdit { font-family:%s; }"
            " QLineEdit, QDateEdit, QTimeEdit { background:%s; border:1px solid %s;"
            " border-radius:6px; padding:4px 8px; selection-background-color:%s;"
            " selection-color:%s; }"
            " QLineEdit:focus, QDateEdit:focus, QTimeEdit:focus { border-color:%s; }"
            % (theme.SURFACE, theme.FONT, theme.CARD, theme.BORDER_STRONG,
               theme.ACCENT_SOFT, theme.INK, theme.ACCENT))
        self.setMinimumSize(460, 600)
        self.resize(560, 780)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:%s; border:none; }" % theme.SURFACE)
        inner = QWidget()
        inner.setStyleSheet("background:%s;" % theme.SURFACE)
        body = QVBoxLayout(inner)
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(10)
        body.addWidget(self._build_inputs_card())
        body.addWidget(self._build_result_card())
        body.addWidget(self._build_window_card())
        body.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._build_footer())

        self._prefill_location()

    # ---- chrome ----------------------------------------------------------
    def _build_header(self):
        w = QWidget()
        w.setStyleSheet("background:%s;" % theme.ACCENT_DARK)
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(1)
        t = QLabel("Survey Weather")
        t.setStyleSheet("color:white; font-family:%s; font-size:15px; font-weight:600;"
                        " background:transparent;" % theme.FONT_SEMIBOLD)
        s = QLabel("Hourly conditions for the survey time — past dates or up to a week "
                   "ahead · data from Open-Meteo")
        s.setStyleSheet("color:#CFE3D6; font-size:11px; background:transparent;")
        s.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(s)
        return w

    def _caption(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:%s; font-size:11px; font-family:%s; background:transparent;"
                        % (theme.MUTED, theme.FONT_MEDIUM))
        return l

    def _card(self):
        w = QWidget()
        w.setObjectName("wxCard")
        w.setStyleSheet("#wxCard { background:%s; border:1px solid %s; border-radius:8px; }"
                        % (theme.CARD, theme.BORDER))
        return w

    def _title(self, text):
        l = QLabel(text)
        l.setStyleSheet("font-family:%s; font-size:13px; font-weight:600; color:%s;"
                        " background:transparent;" % (theme.FONT_SEMIBOLD, theme.INK))
        return l

    # ---- inputs ----------------------------------------------------------
    def _build_inputs_card(self):
        card = self._card()
        g = QGridLayout(card)
        g.setContentsMargins(12, 11, 12, 12)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(7)

        today = QDate.currentDate()
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        from .config import qt_date_format
        self._date_edit.setDisplayFormat(qt_date_format())
        self._date_edit.setDate(today)
        self._date_edit.setMaximumDate(today.addDays(7))  # past, or up to a week ahead (forecast)
        self._date_edit.setMinimumDate(QDate(1940, 1, 1))

        self._time_edit = QTimeEdit()
        self._time_edit.setDisplayFormat("HH':00'")   # hour only — readings are hourly
        self._time_edit.setTime(QTime(9, 0))
        # The dialog/dock QSS leaves the spin arrows blank, so style the +/-
        # stepper buttons explicitly (plus.svg / minus.svg) — same pattern as the
        # price/feature-placer spin boxes. Only the hour is editable; +/- steps it.
        _icons = os.path.join(os.path.dirname(__file__), "icons")
        _plus = os.path.join(_icons, "plus.svg").replace("\\", "/")
        _minus = os.path.join(_icons, "minus.svg").replace("\\", "/")
        self._time_edit.setStyleSheet(
            "QTimeEdit{padding-right:26px;}"
            "QTimeEdit::up-button, QTimeEdit::down-button{subcontrol-origin:border;"
            " width:22px; background:%s; border-left:1px solid %s;}" % (theme.SUNKEN, theme.BORDER_STRONG)
            + "QTimeEdit::up-button{subcontrol-position:top right; border-top-right-radius:6px;}"
            + "QTimeEdit::down-button{subcontrol-position:bottom right; border-bottom-right-radius:6px;}"
            + "QTimeEdit::up-button:hover, QTimeEdit::down-button:hover{background:%s;}" % theme.ACCENT_SOFT
            + "QTimeEdit::up-arrow{image:url(%s); width:10px; height:10px;}" % _plus
            + "QTimeEdit::down-arrow{image:url(%s); width:10px; height:10px;}" % _minus)

        self._lat_edit = QLineEdit()
        self._lat_edit.setPlaceholderText("e.g. 51.50713")
        self._lat_edit.setValidator(QDoubleValidator(-90.0, 90.0, 6, self))
        self._lon_edit = QLineEdit()
        self._lon_edit.setPlaceholderText("e.g. -0.12755")
        self._lon_edit.setValidator(QDoubleValidator(-180.0, 180.0, 6, self))

        self._pc_edit = QLineEdit()
        self._pc_edit.setPlaceholderText("e.g. SW1A 1AA")
        self._pc_edit.returnPressed.connect(self._find_postcode)
        pc_find = QPushButton("Find")
        pc_find.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        pc_find.setStyleSheet(theme.btn_secondary())
        pc_find.setFixedWidth(64)
        pc_find.clicked.connect(self._find_postcode)
        pc_row = QWidget()
        pc_hl = QHBoxLayout(pc_row)
        pc_hl.setContentsMargins(0, 0, 0, 0)
        pc_hl.setSpacing(6)
        pc_hl.addWidget(self._pc_edit, 1)
        pc_hl.addWidget(pc_find)

        self._loc_src = self._caption("Location not set")
        self._centroid_btn = QPushButton("Use redline centroid")
        self._centroid_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._centroid_btn.setStyleSheet(theme.btn_secondary())
        self._centroid_btn.clicked.connect(self._use_centroid)
        self._centroid_btn.setEnabled(self._default_latlon is not None)

        self._fetch_btn = QPushButton("Get weather")
        self._fetch_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fetch_btn.setStyleSheet(theme.btn_primary())
        self._fetch_btn.clicked.connect(self._fetch)

        g.addWidget(self._caption("Survey date"), 0, 0)
        g.addWidget(self._date_edit, 0, 1)
        g.addWidget(self._caption("Survey time"), 1, 0)
        g.addWidget(self._time_edit, 1, 1)
        g.addWidget(self._caption("Latitude"), 2, 0)
        g.addWidget(self._lat_edit, 2, 1)
        g.addWidget(self._caption("Longitude"), 3, 0)
        g.addWidget(self._lon_edit, 3, 1)
        g.addWidget(self._caption("Postcode"), 4, 0)
        g.addWidget(pc_row, 4, 1)
        g.addWidget(self._loc_src, 5, 1)
        g.addWidget(self._centroid_btn, 6, 1)
        g.addWidget(self._fetch_btn, 7, 0, 1, 2)
        g.setColumnStretch(1, 1)
        return card

    # ---- result table ----------------------------------------------------
    def _build_result_card(self):
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(7)
        v.addWidget(self._title("Weather conditions at the time of survey"))
        self._result_caption = self._caption("Set a date, time and location, then “Get weather”.")
        self._result_caption.setWordWrap(True)
        v.addWidget(self._result_caption)

        self._result_table = QTableWidget(3, 2)
        self._result_table.setHorizontalHeaderLabels(["Condition", "Detail"])
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self._result_table.setToolTip("Click a Detail cell to copy it — double-click to edit")
        self._result_table.cellClicked.connect(self._on_result_cell_clicked)
        self._style_table(self._result_table)
        hh = self._result_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for r, name in enumerate(CONDITIONS):
            label = QTableWidgetItem(name)
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)           # column 0 is read-only
            label.setForeground(QColor(theme.INK))
            self._result_table.setItem(r, 0, label)
            self._result_table.setItem(r, 1, QTableWidgetItem("—"))
        self._fit_table_height(self._result_table, 3)
        v.addWidget(self._result_table)

        self._source_lbl = self._caption("Source: —")
        self._source_lbl.setWordWrap(True)
        v.addWidget(self._source_lbl)
        return card

    # ---- ± 2 h window ----------------------------------------------------
    def _build_window_card(self):
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(7)
        v.addWidget(self._title("Hour by hour (survey hour ± 2 h)"))
        cap = self._caption("Data is hourly — the survey hour is highlighted. If your time "
                            "falls between hours, the reading is accurate to within the hour.")
        cap.setWordWrap(True)
        v.addWidget(cap)
        self._window_table = QTableWidget(0, 5)
        self._window_table.setHorizontalHeaderLabels(
            ["Time", "Temp", "Wind", "Rain", "Conditions"])
        self._window_table.verticalHeader().setVisible(False)
        self._window_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._window_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._window_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._style_table(self._window_table)
        hh = self._window_table.horizontalHeader()
        for c in range(4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._window_table)
        return card

    def _style_table(self, t):
        t.setStyleSheet(
            "QTableWidget { background:%s; border:1px solid %s; border-radius:6px;"
            " gridline-color:%s; font-size:12px; }"
            " QHeaderView::section { background:%s; color:%s; border:none;"
            " border-bottom:1px solid %s; padding:5px 8px; font-family:%s;"
            " font-weight:600; }"
            " QTableWidget::item { padding:4px 8px; }"
            % (theme.CARD, theme.BORDER, theme.BORDER, theme.SUNKEN, theme.MUTED,
               theme.BORDER_STRONG, theme.FONT_SEMIBOLD))
        t.setShowGrid(True)
        t.setWordWrap(False)
        t.horizontalHeader().setHighlightSections(False)

    def _fit_table_height(self, t, n_rows):
        h = t.horizontalHeader().height()
        for r in range(n_rows):
            h += t.rowHeight(r) if t.rowHeight(r) > 0 else 30
        t.setFixedHeight(h + 4)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # ---- footer ----------------------------------------------------------
    def _build_footer(self):
        bar = QWidget()
        bar.setStyleSheet("background:%s; border-top:1px solid %s;" % (theme.CARD, theme.BORDER))
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 9, 12, 9)
        h.setSpacing(8)
        self._status = QLabel("")
        self._status.setStyleSheet("color:%s; font-size:11px; background:transparent;" % theme.MUTED)
        self._status.setWordWrap(True)
        h.addWidget(self._status, 1)

        copy_vals = QPushButton("Copy values")
        copy_vals.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copy_vals.setStyleSheet(theme.btn_primary())
        copy_vals.setToolTip("Copy the three Detail values — paste into the Detail "
                             "column of the report table")
        copy_vals.clicked.connect(self._copy_for_report)

        copy_tbl = QPushButton("Copy table")
        copy_tbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copy_tbl.setStyleSheet(theme.btn_secondary())
        copy_tbl.setToolTip("Copy the whole Condition / Detail table (with header)")
        copy_tbl.clicked.connect(self._copy_full_table)

        close = QPushButton("Close")
        close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close.setStyleSheet(theme.btn_secondary())
        close.clicked.connect(self.accept)

        h.addWidget(copy_tbl)
        h.addWidget(copy_vals)
        h.addWidget(close)
        return bar

    # ---- location helpers ------------------------------------------------
    def _prefill_location(self):
        if self._default_latlon is not None:
            self._use_centroid()
            return
        # Fall back to the last location used in a previous session.
        s = QSettings()
        lat = s.value("biome/weather/lat", "", type=str)
        lon = s.value("biome/weather/lon", "", type=str)
        if lat and lon:
            self._lat_edit.setText(lat)
            self._lon_edit.setText(lon)
            self._loc_src.setText("Location: last used")
        else:
            self._loc_src.setText("Location: enter a postcode or coordinates")
            self._status.setText("Tip: draw a redline first and it will centre here "
                                 "automatically.")

    def _use_centroid(self):
        if self._default_latlon is None:
            return
        lat, lon = self._default_latlon
        self._lat_edit.setText("%.5f" % lat)
        self._lon_edit.setText("%.5f" % lon)
        self._loc_src.setText("Location: redline centroid")

    def _read_latlon(self):
        try:
            lat = float(self._lat_edit.text().strip())
            lon = float(self._lon_edit.text().strip())
        except ValueError:
            self._status.setText("Enter a valid latitude and longitude, or look up a postcode.")
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self._status.setText("Latitude must be −90…90 and longitude −180…180.")
            return None
        return lat, lon

    def _find_postcode(self):
        pc = self._pc_edit.text().strip()
        if not pc:
            self._status.setText("Type a postcode to look up.")
            return
        import json
        from urllib.request import urlopen, Request
        from urllib.parse import quote
        url = "https://api.postcodes.io/postcodes/%s" % quote(pc)
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            req = Request(url, headers={"User-Agent": W.USER_AGENT})
            with urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            data = None
        finally:
            QApplication.restoreOverrideCursor()
        res = (data or {}).get("result") if isinstance(data, dict) else None
        if not res or res.get("latitude") is None:
            self._status.setText("Couldn't find postcode “%s”. Check it, or enter "
                                 "coordinates directly." % pc)
            return
        self._lat_edit.setText("%.5f" % res["latitude"])
        self._lon_edit.setText("%.5f" % res["longitude"])
        self._loc_src.setText("Location: %s" % (res.get("postcode") or pc))
        self._status.setText("Found %s — now “Get weather”." % (res.get("postcode") or pc))

    # ---- fetch + fill ----------------------------------------------------
    def _fetch(self):
        latlon = self._read_latlon()
        if latlon is None:
            return
        lat, lon = latlon
        qd = self._date_edit.date()
        d = _dt.date(qd.year(), qd.month(), qd.day())
        qt = self._time_edit.time()
        hour = W.nearest_hour(qt.hour(), qt.minute())
        self._survey_hour = hour

        self._fetch_btn.setEnabled(False)
        self._status.setText("Fetching weather…")
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        QApplication.processEvents()
        try:
            rows, source, err = W.fetch_hours(lat, lon, d, timeout=10)
        finally:
            QApplication.restoreOverrideCursor()
            self._fetch_btn.setEnabled(True)

        if rows is None:
            self._status.setText("Weather lookup failed (%s). You can still type the "
                                 "values in by hand below." % (err or "no data"))
            return

        self._rows = rows
        # Persist the location for next time.
        s = QSettings()
        s.setValue("biome/weather/lat", "%.5f" % lat)
        s.setValue("biome/weather/lon", "%.5f" % lon)

        row = W.row_for_hour(rows, hour)
        details = W.report_details(row)
        for r, val in enumerate(details):
            self._result_table.item(r, 1).setText(val)

        time_str = "%02d:00" % hour
        from .config import qt_date_format
        self._result_caption.setText(
            "%s on %s · %.5f, %.5f" % (time_str, qd.toString(qt_date_format()), lat, lon))
        self._source_lbl.setText("Source: %s · licensed CC-BY 4.0 (credit Open-Meteo.com)"
                                 % source)
        self._fill_window(rows, hour)

        if not row or row.get("temp") is None:
            self._status.setText("No reading for %s (outside the available range — "
                                 "forecasts run ~7 days ahead). Edit the cells if needed."
                                 % time_str)
        else:
            self._status.setText("Filled for %s — click a Detail cell to copy it, "
                                 "double-click to edit." % time_str)

    def _fill_window(self, rows, hour):
        win = W.window(rows, hour, span=2)
        t = self._window_table
        t.setRowCount(len(win))
        for i, r in enumerate(win):
            cells = [
                r["time"] or ("%02d:00" % r["hour"]),
                "—" if r["temp"] is None else "%.1f °C" % r["temp"],
                "—" if r["wind"] is None else W.fmt_wind(r["wind"], r["gust"], r["dir"]),
                "—" if r["precip"] is None else "%.1f mm" % r["precip"],
                r["cond"] or "—",
            ]
            is_survey = (r["hour"] == hour)
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if is_survey:
                    item.setBackground(QColor(theme.ACCENT_SOFT))
                    item.setForeground(QColor(theme.ACCENT_DARK))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                else:
                    item.setForeground(QColor(theme.INK))
                t.setItem(i, c, item)
        self._fit_table_height(t, len(win))

    # ---- copy ------------------------------------------------------------
    def _on_result_cell_clicked(self, row, col):
        """Single click on a Detail cell copies its value to the clipboard."""
        if col != 1:
            return
        it = self._result_table.item(row, col)
        val = it.text().strip() if it else ""
        if val in ("", "—"):
            return
        QApplication.clipboard().setText(val)
        self._status.setText("Copied %s “%s” ✓  — double-click the cell to edit it."
                             % (CONDITIONS[row].lower(), val))

    def _detail_values(self):
        out = []
        for r in range(3):
            it = self._result_table.item(r, 1)
            out.append(it.text().strip() if it else "")
        return out

    def _copy(self, rows, header_rows):
        """Place `rows` on the clipboard in house style, falling back to plain text."""
        try:
            from .clipboard_utils import set_table_clipboard
            set_table_clipboard(rows, header_rows=header_rows)
        except Exception:
            tsv = "\n".join("\t".join(str(c) for c in row) for row in rows)
            QApplication.clipboard().setText(tsv)

    def _copy_for_report(self):
        vals = self._detail_values()
        if all(v in ("", "—") for v in vals):
            self._status.setText("Nothing to copy yet — “Get weather” first.")
            return
        self._copy([[v] for v in vals], header_rows=0)
        self._status.setText("Copied 3 values ✓  — click the Detail cell beside "
                             "“Temperature” in the report and paste.")

    def _copy_full_table(self):
        vals = self._detail_values()
        rows = [["Condition", "Detail"]]
        for name, val in zip(CONDITIONS, vals):
            rows.append([name, val])
        self._copy(rows, header_rows=1)
        self._status.setText("Copied the Condition / Detail table ✓")
