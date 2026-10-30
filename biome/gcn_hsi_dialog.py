# -*- coding: utf-8 -*-
"""
gcn_hsi_dialog.py
=================

Pop-out Great Crested Newt Habitat Suitability Index (HSI) calculator, launched
from the GCN section of the Desk Study tab. Modelled on the in-form Condition
assessment dialog: themed header, scrollable body, copy-to-clipboard footer.

The surveyor picks a band for the six categorical indices and types a field
measurement for the four "read off graph" indices (SI2/SI5/SI8/SI10). For those
four the SI is interpolated from the digitised guide curve (gcn_hsi_data) and a
live marker is drawn on a reproduction of the graph, with the original ARG UK
figure one click away. The HSI and Lee Brady suitability band update live. A
copy-paste report (and an optional multi-pond comparison table) feed a written
report — nothing is written to any layer.
"""

import json
import os

from qgis.PyQt.QtCore import Qt, QRectF, QPointF, QDate, QSize
from qgis.PyQt.QtGui import (QPixmap, QImage, QPainter, QPen, QBrush, QColor,
                             QFont, QCursor)
from qgis.PyQt.QtWidgets import (
    QDialog, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QFrame, QPushButton, QButtonGroup, QLineEdit, QSpinBox,
    QApplication, QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QFileDialog, QToolButton, QComboBox, QTabWidget)

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsPointXY, QgsProject)
from qgis.gui import QgsMapTool, QgsMapToolEmitPoint, QgsRubberBand

from . import theme
from . import gcn_hsi_data as G


# Suitability band -> (text colour, fill). Excellent is a deeper green than Good.
BAND_STYLE = {
    "Poor":          (theme.BAD,  theme.BAD_BG),
    "Below average": (theme.WARN, theme.WARN_BG),
    "Average":       (theme.INFO, theme.INFO_BG),
    "Good":          (theme.GOOD, theme.GOOD_BG),
    "Excellent":     ("#0F5C39",  "#D6ECDD"),
}

# Short measurement prompt shown to the left of each graph factor's input.
GRAPH_INPUT_LABEL = {
    "SI2": "Surface area", "SI5": "Perimeter shaded",
    "SI8": "Ponds within 1 km", "SI10": "Surface covered",
}

_RES_DIR = os.path.join(os.path.dirname(__file__), "icons", "gcn_hsi")
_PROJECT_SCOPE = "Biome"
_PROJECT_HSI_KEY = "gcn_hsi_ponds_json"

POND_STATUS_OPTIONS = [
    ("not_assessed", "Not assessed"),
    ("hsi_required", "Survey undertaken — complete HSI"),
    ("dry", "Dry at time of survey"),
    ("inaccessible", "Not accessible — potentially linked"),
    ("unsuitable_desktop", "Not considered suitable habitat"),
    ("not_linked", "Not considered linked"),
]
POND_STATUS_LABELS = dict(POND_STATUS_OPTIONS + [
    ("hsi_complete", "HSI completed"),
])


class _GridReferencePickTool(QgsMapToolEmitPoint):
    """One-click canvas tool used to populate the pond grid reference."""

    def __init__(self, canvas, on_point, on_cancel):
        super().__init__(canvas)
        self._on_point = on_point
        self._on_cancel = on_cancel
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._on_cancel()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._on_point(self.toMapCoordinates(event.pos()))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
        else:
            super().keyPressEvent(event)


class _PondAreaDrawTool(QgsMapTool):
    """Temporary polygon capture tool for measuring a pond footprint."""

    def __init__(self, canvas, on_finished, on_cancel):
        super().__init__(canvas)
        self._canvas = canvas
        self._on_finished = on_finished
        self._on_cancel = on_cancel
        self._points = []
        self._finished = False
        self._rubber = QgsRubberBand(canvas, Qgis.GeometryType.Polygon)
        self._rubber.setFillColor(QColor(31, 122, 77, 65))
        self._rubber.setStrokeColor(QColor("#175E3B"))
        self._rubber.setWidth(3)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def _redraw(self, hover=None):
        """Rebuild and explicitly repaint the live preview."""
        self._rubber.reset(Qgis.GeometryType.Polygon)
        preview = list(self._points)
        if hover is not None and self._points:
            preview.append(hover)
        for index, point in enumerate(preview):
            # update=True on the last point is important: without it QGIS can
            # retain a stale/partial rubber band until some unrelated repaint.
            self._rubber.addPoint(point, index == len(preview) - 1)
        if preview:
            self._rubber.show()

    def canvasReleaseEvent(self, event):
        if self._finished:
            return
        if event.button() == Qt.MouseButton.RightButton:
            if len(self._points) >= 3:
                self._finished = True
                geometry = QgsGeometry.fromPolygonXY([self._points])
                self._on_finished(geometry)
            else:
                self._on_cancel()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._points.append(self.toMapCoordinates(event.pos()))
            self._redraw()

    def canvasMoveEvent(self, event):
        if not self._finished:
            self._redraw(self.toMapCoordinates(event.pos()))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
        else:
            super().keyPressEvent(event)

    def clear(self):
        self._finished = True
        self._rubber.reset(Qgis.GeometryType.Polygon)


def _si_colours(si):
    """Traffic-light colours for an individual suitability index pill."""
    if si is None:
        return theme.MUTED, theme.SUNKEN
    if si >= 0.8:
        return theme.GOOD, theme.GOOD_BG
    if si >= 0.5:
        return theme.WARN, theme.WARN_BG
    return theme.BAD, theme.BAD_BG


def _pill_qss(fg, bg):
    return ("color:%s; background:%s; border:none; border-radius:9px; padding:2px 10px;"
            " font-family:%s; font-size:12px; font-weight:600; min-width:30px;"
            % (fg, bg, theme.FONT_SEMIBOLD))


# ── Live curve graph ─────────────────────────────────────────────────────────

class _CurveGraph(QWidget):
    """A compact reproduction of a guide SI curve with a live read-off marker.

    Draws the digitised curve (axes, gridlines, x/y labels) and, when a value is
    set, a dot at (measurement, SI) with dashed guides to both axes. Supports a
    log x-axis (SI8 pond density)."""

    def __init__(self, curve, xmin, xmax, xlabel, log=False, parent=None):
        super().__init__(parent)
        self._curve = curve
        self._xmin = float(xmin) if not log else float(xmin)
        self._xmax = float(xmax)
        self._xlabel = xlabel
        self._log = log
        self._value = None
        self._si = None
        self._omit = False
        self.setFixedHeight(150)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_marker(self, value, si, omit=False):
        self._value, self._si, self._omit = value, si, omit
        self.update()

    def _mapx(self, x, plot):
        if self._log:
            import math
            lo, hi = math.log10(self._xmin), math.log10(self._xmax)
            x = max(self._xmin, min(self._xmax, x))
            fx = (math.log10(x) - lo) / (hi - lo)
        else:
            fx = (x - self._xmin) / (self._xmax - self._xmin)
        fx = max(0.0, min(1.0, fx))
        return plot.left() + fx * plot.width()

    def _mapy(self, si, plot):
        si = max(0.0, min(1.0, si))
        return plot.bottom() - si * plot.height()

    def _xticks(self):
        if self._log:
            return [(0.1, "0.1"), (1, "1"), (10, "10")]
        xs = self._xmax
        return [(v, ("%g" % v)) for v in (0, xs * 0.25, xs * 0.5, xs * 0.75, xs)]

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(theme.CARD))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 6, 6)

        left, top, right, bottom = 34, 10, 12, 24
        plot = QRectF(left, top, w - left - right, h - top - bottom)

        f = QFont(); f.setPixelSize(9); p.setFont(f)
        # y gridlines + labels
        for yv in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
            yy = plot.bottom() - yv * plot.height()
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            p.setPen(QColor(theme.FAINT))
            p.drawText(QRectF(0, yy - 7, left - 4, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "%g" % yv)
        # x gridlines + labels
        for xv, lbl in self._xticks():
            xx = self._mapx(xv, plot)
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawLine(QPointF(xx, plot.top()), QPointF(xx, plot.bottom()))
            p.setPen(QColor(theme.FAINT))
            p.drawText(QRectF(xx - 26, plot.bottom() + 2, 52, 13),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, lbl)
        # axes
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1.3))
        p.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        # x axis title
        fb = QFont(); fb.setPixelSize(9); fb.setBold(True); p.setFont(fb)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(plot.left(), h - 13, plot.width(), 12),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._xlabel)
        # curve
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        pts = [QPointF(self._mapx(x, plot), self._mapy(si, plot)) for (x, si) in self._curve]
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        # marker / omit note
        if self._value is not None and self._si is not None:
            mx = self._mapx(self._value, plot)
            my = self._mapy(self._si, plot)
            p.setPen(QPen(QColor(theme.FAINT), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(mx, plot.bottom()), QPointF(mx, my))
            p.drawLine(QPointF(plot.left(), my), QPointF(mx, my))
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QBrush(QColor(theme.ACCENT_DARK)))
            p.drawEllipse(QPointF(mx, my), 4.5, 4.5)
            fl = QFont(); fl.setPixelSize(10); fl.setBold(True); p.setFont(fl)
            p.setPen(QColor(theme.ACCENT_DARK))
            tx = mx + 8 if mx + 56 < plot.right() else mx - 8 - 48
            ty = my - 16 if my - 16 > plot.top() else my + 4
            p.drawText(QRectF(tx, ty, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "SI %.2f" % self._si)
        elif self._omit:
            fo = QFont(); fo.setPixelSize(11); fo.setBold(True); p.setFont(fo)
            p.setPen(QColor(theme.MUTED))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Omitted (> 2000 m²)")
        p.end()


# ── Stepper spin box ─────────────────────────────────────────────────────────

class _StepSpin(QSpinBox):
    """Spin box with +/− buttons that snap to clean multiples of the step (5, 10,
    15 … or 50, 100, 150 …), while still accepting any typed value. Optionally
    starts 'unset' (— set —) so an untouched factor doesn't score; the pond-count
    box instead starts at 0 and steps by 1."""
    UNSET = -1

    def __init__(self, step, vmax, unit, unset=True, parent=None):
        super().__init__(parent)
        self._step = max(1, int(step))
        self._unset = unset
        self.setMaximum(int(vmax))
        self.setSingleStep(self._step)
        self.setSuffix(" " + unit)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if unset:
            self.setMinimum(self.UNSET)
            self.setSpecialValueText("— set —")
            self.setValue(self.UNSET)
        else:
            self.setMinimum(0)
            self.setValue(0)

    def stepBy(self, steps):
        step = self._step
        cur = self.value()
        if self._unset and cur == self.UNSET:
            if steps > 0:                       # first + from unset → one step
                self.setValue(min(self.maximum(), step))
            return
        if steps > 0:
            nxt = cur + step if cur % step == 0 else (cur // step + 1) * step
        else:
            if self._unset and cur == 0:        # − from 0 clears back to unset
                self.setValue(self.UNSET)
                return
            nxt = cur - step if cur % step == 0 else (cur // step) * step
        self.setValue(max(0, min(self.maximum(), nxt)))


# ── Dialog ───────────────────────────────────────────────────────────────────

class GcnHsiDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = {f["key"]: None for f in G.FACTORS}   # si | OMIT | None
        self._display = {}     # key -> human value string
        self._inputs = {}      # key -> category index or raw graph measurement
        self._si_str = {}      # key -> "1.00" | "n/a" | "—"
        self._cards = {}       # key -> widget refs
        self._ponds = []       # snapshots for the comparison table
        self._gridref_pick_tool = None
        self._area_draw_tool = None
        self._gridref_previous_tool = None
        self._area_previous_tool = None
        self._active_mapped_id = ""

        self.setWindowTitle("GCN Habitat Suitability Index")
        self.setStyleSheet(
            "QDialog { background:%s; }"
            " QLabel, QPushButton, QSpinBox, QLineEdit, QComboBox { font-family:%s; }"
            " QLineEdit, QSpinBox, QComboBox { background:%s; border:1px solid %s;"
            " border-radius:6px; padding:4px 8px; selection-background-color:%s; }"
            " QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color:%s; }"
            % (theme.SURFACE, theme.FONT, theme.CARD, theme.BORDER_STRONG,
               theme.ACCENT_SOFT, theme.ACCENT))
        self.setMinimumSize(850, 720)
        self.resize(1100, 960)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        hsi_scroll = QScrollArea()
        self._scroll = hsi_scroll
        self._hsi_scroll = hsi_scroll
        hsi_scroll.setWidgetResizable(True)
        hsi_scroll.setFrameShape(QFrame.Shape.NoFrame)
        hsi_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hsi_scroll.setStyleSheet("QScrollArea { background:%s; border:none; }" % theme.SURFACE)
        hsi_inner = QWidget()
        hsi_inner.setStyleSheet("background:%s;" % theme.SURFACE)
        body = QVBoxLayout(hsi_inner)
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(10)

        body.addWidget(self._build_pond_details())
        for f in G.FACTORS:
            body.addWidget(self._build_factor_card(f))
        body.addWidget(self._build_result_card())
        body.addWidget(self._build_save_pond_button())
        body.addStretch(1)
        hsi_scroll.setWidget(hsi_inner)

        compare_scroll = QScrollArea()
        compare_scroll.setWidgetResizable(True)
        compare_scroll.setFrameShape(QFrame.Shape.NoFrame)
        compare_scroll.setStyleSheet("QScrollArea { background:%s; border:none; }" % theme.SURFACE)
        compare_inner = QWidget()
        compare_inner.setStyleSheet("background:%s;" % theme.SURFACE)
        compare_body = QVBoxLayout(compare_inner)
        compare_body.setContentsMargins(20, 18, 20, 20)
        compare_body.setSpacing(12)
        compare_body.addWidget(self._build_compare_card())
        compare_body.addStretch(1)
        compare_scroll.setWidget(compare_inner)

        self._tabs.addTab(compare_scroll, "Compare ponds")
        self._tabs.addTab(hsi_scroll, "HSI assessment")
        outer.addWidget(self._tabs, 1)
        outer.addWidget(self._build_footer())
        self._load_saved_ponds()
        # Initialise graph factors that start with a value (pond count at 0); the
        # unset ones (value −1) stay shown as "—".
        for f in G.FACTORS:
            if f["type"] == "graph":
                self._on_graph(f, self._cards[f["key"]]["spin"].value())
        self._recompute()

    # ---- chrome ----------------------------------------------------------
    def _build_header(self):
        w = QWidget()
        w.setStyleSheet("background:%s;" % theme.ACCENT_DARK)
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(1)
        t = QLabel("Great Crested Newt — Habitat Suitability Index")
        t.setStyleSheet("color:white; font-family:%s; font-size:15px; font-weight:600;"
                        " background:transparent;" % theme.FONT_SEMIBOLD)
        s = QLabel("Geometric mean of ten suitability indices · ARG UK Advice Note 5 (2010)")
        s.setStyleSheet("color:#CFE3D6; font-size:11px; background:transparent;")
        s.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(s)
        return w

    def _build_pond_details(self):
        w = QWidget()
        w.setObjectName("hsiCard")
        w.setStyleSheet("#hsiCard { background:%s; border:1px solid %s; border-radius:8px; }"
                        % (theme.CARD, theme.BORDER))
        g = QGridLayout(w)
        g.setContentsMargins(12, 10, 12, 10)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Pond 1")
        self._name_edit.setText(self._suggest_next_pond_name())
        self._ref_edit = QLineEdit()
        self._ref_edit.setPlaceholderText("e.g. SK 1234 5678")
        self._ref_edit.editingFinished.connect(self._update_manual_distance)
        self._ref_pick_btn = QToolButton()
        self._ref_pick_btn.setText("⌖")
        self._ref_pick_btn.setFixedSize(32, 30)
        self._ref_pick_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._ref_pick_btn.setToolTip("Pick grid reference from the live map")
        self._ref_pick_btn.setAccessibleName("Pick grid reference from map")
        self._ref_pick_btn.setStyleSheet(
            "QToolButton { background:%s; color:%s; border:1px solid %s;"
            " border-radius:6px; font-size:18px; font-weight:600; }"
            "QToolButton:hover { background:%s; border-color:%s; }"
            "QToolButton:pressed { background:%s; }"
            % (theme.CARD, theme.ACCENT_DARK, theme.BORDER_STRONG,
               theme.ACCENT_SOFT, theme.ACCENT, theme.SUNKEN))
        self._ref_pick_btn.clicked.connect(self._start_gridref_pick)
        ref_row = QWidget()
        ref_row.setStyleSheet("background:transparent;")
        ref_layout = QHBoxLayout(ref_row)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        ref_layout.setSpacing(6)
        ref_layout.addWidget(self._ref_edit, 1)
        ref_layout.addWidget(self._ref_pick_btn)
        self._distance_edit = QLineEdit()
        self._distance_edit.setReadOnly(True)
        self._distance_edit.setPlaceholderText("Not available for a manual pond")
        self._date_edit = QLineEdit()
        from .config import qt_date_format
        self._date_edit.setText(QDate.currentDate().toString(qt_date_format()))
        g.addWidget(self._caption("Pond name"), 0, 0)
        g.addWidget(self._name_edit, 0, 1)
        g.addWidget(self._caption("Grid reference"), 1, 0)
        g.addWidget(ref_row, 1, 1)
        g.addWidget(self._caption("Distance from site"), 2, 0)
        g.addWidget(self._distance_edit, 2, 1)
        g.addWidget(self._caption("Date"), 3, 0)
        g.addWidget(self._date_edit, 3, 1)
        g.setColumnStretch(1, 1)
        return w

    def _mapped_pond_options(self):
        """Return Pond Mapper features without creating a new map layer."""
        ponds = []
        try:
            from .pond_mapper import POND_LAYER_NAME
            layers = [lyr for lyr in QgsProject.instance().mapLayersByName(POND_LAYER_NAME)
                      if lyr.geometryType() == 0]
            if not layers:
                return ponds
            layer = layers[0]
            for feature in layer.getFeatures():
                pond_id = str(feature["pond_id"] or "").strip()
                if not pond_id:
                    continue
                try:
                    distance_m = int(feature["distance_m"] or 0)
                except (TypeError, ValueError):
                    distance_m = 0
                distance_label = str(feature["distance_label"] or "").strip()
                if not distance_label:
                    distance_label = ("Within redline" if distance_m < 1 else
                                      ("%d m" % distance_m if distance_m < 1000 else
                                       "%.2f km" % (distance_m / 1000.0)))
                grid_ref = str(feature["grid_ref"] or "").strip()
                # Derive the display reference from the actual point every
                # time so legacy six-figure records immediately gain the new
                # eight-figure (10 m) precision.
                if feature.geometry() and not feature.geometry().isEmpty():
                    point = feature.geometry().asPoint()
                    if layer.crs().authid() != "EPSG:27700":
                        point = QgsCoordinateTransform(
                            layer.crs(), QgsCoordinateReferenceSystem("EPSG:27700"),
                            QgsProject.instance()).transform(point)
                    from .gridref_utils import osgb_en_to_gridref
                    grid_ref = osgb_en_to_gridref(int(point.x()), int(point.y()), digits=8)
                ponds.append({"pond_id": pond_id, "grid_ref": grid_ref,
                              "distance_m": distance_m,
                              "distance_label": distance_label})
        except Exception:
            return []
        return sorted(ponds, key=lambda pond: pond["pond_id"])

    def _suggest_next_pond_name(self):
        """Return the next unused SWB number across mapped and saved ponds."""
        import re
        names = [p["pond_id"] for p in self._mapped_pond_options()]
        try:
            raw, ok = QgsProject.instance().readEntry(
                _PROJECT_SCOPE, _PROJECT_HSI_KEY, "")
            records = json.loads(raw) if ok and raw else []
            names.extend(str(p.get("name") or "") for p in records
                         if isinstance(p, dict))
        except Exception:
            pass
        numbers = []
        for name in names:
            match = re.fullmatch(r"SWB-(\d+)", str(name).strip(), re.IGNORECASE)
            if match:
                numbers.append(int(match.group(1)))
        return "SWB-%02d" % ((max(numbers) + 1) if numbers else 1)

    def _start_manual_pond(self):
        self._active_mapped_id = ""
        self._ref_edit.setReadOnly(False)
        self._ref_pick_btn.setEnabled(True)
        self._name_edit.setText(self._suggest_next_pond_name())
        self._ref_edit.clear()
        self._distance_edit.clear()
        self._clear_hsi_form()
        self._hsi_scroll.verticalScrollBar().setValue(0)
        self._name_edit.setFocus()

    def _update_manual_distance(self):
        """Calculate redline-edge distance for a manually entered grid reference."""
        grid_ref = self._ref_edit.text().strip()
        if not grid_ref:
            self._distance_edit.clear()
            return
        try:
            from qgis.core import QgsGeometry, QgsPointXY
            from .gridref_utils import gridref_to_osgb_en
            from .pond_mapper import _project_redline_bng, _distance_label
            easting, northing = gridref_to_osgb_en(grid_ref)
            redline = _project_redline_bng()
            if redline is None or redline.isEmpty():
                self._distance_edit.setText("Redline not available")
                return
            point = QgsGeometry.fromPointXY(QgsPointXY(float(easting), float(northing)))
            distance_m = int(round(redline.distance(point)))
            self._distance_edit.setText(_distance_label(distance_m))
        except Exception:
            self._distance_edit.setText("Check grid reference")

    def _map_canvas(self):
        try:
            from qgis.utils import iface
            return iface.mapCanvas() if iface else None
        except Exception:
            return None

    def _start_gridref_pick(self):
        """Temporarily hide this modal dialog and take one point from the map."""
        canvas = self._map_canvas()
        if canvas is None:
            self._status.setText("The live map is not available.")
            return
        self._gridref_previous_tool = canvas.mapTool()
        self._gridref_pick_tool = _GridReferencePickTool(
            canvas, self._finish_gridref_pick, self._cancel_gridref_pick)
        canvas.setMapTool(self._gridref_pick_tool)
        self.hide()
        try:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "Pick pond location",
                "Click the live map to fill the grid reference. Right-click or press Esc to cancel.",
                level=Qgis.MessageLevel.Info, duration=0)
        except Exception:
            pass

    def _finish_gridref_pick(self, canvas_point):
        canvas = self._map_canvas()
        try:
            bng = QgsCoordinateReferenceSystem("EPSG:27700")
            source_crs = canvas.mapSettings().destinationCrs()
            point_bng = QgsCoordinateTransform(
                source_crs, bng, QgsProject.instance()).transform(canvas_point)
            from .gridref_utils import osgb_en_to_gridref
            grid_ref = osgb_en_to_gridref(
                int(point_bng.x()), int(point_bng.y()), digits=8)
            self._ref_edit.setText(grid_ref)
            self._update_manual_distance()
            self._status.setText("Grid reference picked from the live map: %s" % grid_ref)
        except Exception as ex:
            self._status.setText("Could not create a grid reference: %s" % ex)
        finally:
            self._end_gridref_pick()

    def _cancel_gridref_pick(self):
        self._end_gridref_pick()
        self._status.setText("Map pick cancelled.")

    def _end_gridref_pick(self, reopen=True):
        canvas = self._map_canvas()
        if canvas is not None and self._gridref_pick_tool is not None:
            try:
                if canvas.mapTool() is self._gridref_pick_tool:
                    if self._gridref_previous_tool is not None:
                        canvas.setMapTool(self._gridref_previous_tool)
                    else:
                        canvas.unsetMapTool(self._gridref_pick_tool)
            except Exception:
                pass
        self._gridref_pick_tool = None
        self._gridref_previous_tool = None
        try:
            from qgis.utils import iface
            iface.messageBar().clearWidgets()
        except Exception:
            pass
        if reopen:
            self.show()
            self.raise_()
            self.activateWindow()

    def _start_pond_area_draw(self):
        canvas = self._map_canvas()
        if canvas is None:
            self._status.setText("The live map is not available.")
            return
        self._area_previous_tool = canvas.mapTool()
        self._area_draw_tool = _PondAreaDrawTool(
            canvas, self._finish_pond_area_draw, self._cancel_pond_area_draw)
        canvas.setMapTool(self._area_draw_tool)
        self.hide()
        try:
            from qgis.utils import iface
            iface.messageBar().pushMessage(
                "Draw pond area",
                "Left-click around the pond boundary. Right-click to finish; Esc cancels.",
                level=Qgis.MessageLevel.Info, duration=0)
        except Exception:
            pass

    def _finish_pond_area_draw(self, geometry):
        canvas = self._map_canvas()
        try:
            measured = QgsGeometry(geometry)
            source_crs = canvas.mapSettings().destinationCrs()
            bng = QgsCoordinateReferenceSystem("EPSG:27700")
            if source_crs != bng:
                measured.transform(QgsCoordinateTransform(
                    source_crs, bng, QgsProject.instance()))
            area_m2 = max(1, int(round(measured.area())))
            spin = self._cards["SI2"]["spin"]
            spin.setValue(min(spin.maximum(), area_m2))
            self._status.setText("Pond area measured from map: %s m²" % format(area_m2, ","))
        except Exception as ex:
            self._status.setText("Could not measure the pond area: %s" % ex)
        finally:
            self._end_pond_area_draw()

    def _cancel_pond_area_draw(self):
        self._end_pond_area_draw()
        self._status.setText("Pond-area drawing cancelled.")

    def _end_pond_area_draw(self, reopen=True):
        canvas = self._map_canvas()
        tool = self._area_draw_tool
        if tool is not None:
            try:
                tool.clear()
            except Exception:
                pass
        if canvas is not None and tool is not None:
            try:
                if canvas.mapTool() is tool:
                    if self._area_previous_tool is not None:
                        canvas.setMapTool(self._area_previous_tool)
                    else:
                        canvas.unsetMapTool(tool)
            except Exception:
                pass
        self._area_draw_tool = None
        self._area_previous_tool = None
        try:
            from qgis.utils import iface
            iface.messageBar().clearWidgets()
        except Exception:
            pass
        if reopen:
            self.show()
            self.raise_()
            self.activateWindow()

    def done(self, result):
        self._end_gridref_pick(reopen=False)
        self._end_pond_area_draw(reopen=False)
        super().done(result)

    def _caption(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:%s; font-size:11px; font-family:%s; background:transparent;"
                        % (theme.MUTED, theme.FONT_MEDIUM))
        return l

    # ---- factor cards ----------------------------------------------------
    def _build_factor_card(self, factor):
        key = factor["key"]
        card = QWidget()
        card.setObjectName("hsiCard")
        card.setStyleSheet("#hsiCard { background:%s; border:1px solid %s; border-radius:8px; }"
                           % (theme.CARD, theme.BORDER))
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("%s · %s" % (key, factor["title"]))
        title.setStyleSheet("font-family:%s; font-size:13px; font-weight:600; color:%s;"
                            " background:transparent;" % (theme.FONT_SEMIBOLD, theme.INK))
        head.addWidget(title)
        if factor.get("desktop"):
            tag = QLabel("desktop")
            tag.setStyleSheet("color:%s; background:%s; border-radius:7px; padding:1px 7px;"
                              " font-size:10px;" % (theme.INFO, theme.INFO_BG))
            head.addWidget(tag)
        head.addStretch(1)
        pill = QLabel("SI —")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setStyleSheet(_pill_qss(theme.MUTED, theme.SUNKEN))
        head.addWidget(pill)
        v.addLayout(head)

        if factor.get("note"):
            note = QLabel(factor["note"])
            note.setWordWrap(True)
            note.setStyleSheet("color:%s; font-size:11px; background:transparent;" % theme.MUTED)
            v.addWidget(note)

        refs = {"pill": pill}
        self._cards[key] = refs
        if factor["type"] == "cat":
            self._build_cat_body(v, factor, refs)
            if factor.get("ref"):
                self._build_zone_reference(v, factor)
        else:
            self._build_graph_body(v, factor, refs)
        return card

    # Zone map + colour legend for SI1 (a desktop/map exercise). The guide's map
    # delineates the zones intricately (altitude-based), so we show it as-is with
    # a colour key rather than re-drawing — re-colouring the crude figure can't be
    # done without misrepresenting the boundaries.
    _ZONE_LEGEND = [
        (theme.GOOD, "Zone A — optimal", "1.00",
         "Core range — high probability of GCN occurrence (lowland England)."),
        (theme.WARN, "Zone B — marginal", "0.50",
         "Range margins — patchy, low probability of occurrence."),
        (theme.BAD, "Zone C — unsuitable", "0.01",
         "Mostly outside the recorded range — most of Scotland and the uplands."),
    ]

    def _build_zone_reference(self, layout, factor):
        hdr = QLabel(factor.get("ref_label", "Reference map"))
        hdr.setStyleSheet("color:%s; font-size:11px; font-family:%s; background:transparent;"
                          " padding-top:6px;" % (theme.MUTED, theme.FONT_MEDIUM))
        layout.addWidget(hdr)

        for fg, name, si, hint in self._ZONE_LEGEND:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(7)
            sw = QLabel()
            sw.setFixedSize(13, 13)
            sw.setStyleSheet("background:%s; border-radius:3px;" % fg)
            txt = QLabel("<b>%s</b> · SI %s — %s" % (name, si, hint))
            txt.setWordWrap(True)
            txt.setStyleSheet("color:%s; font-size:11px; background:transparent;" % theme.INK)
            rl.addWidget(sw, 0, Qt.AlignmentFlag.AlignTop)
            rl.addWidget(txt, 1)
            layout.addWidget(row)

        img = self._image_label(factor["ref"], 320)
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(img)
        holder.addStretch(1)
        layout.addLayout(holder)

        cap = QLabel("Find your site on the map and score the zone it falls in "
                     "(a desktop exercise). Map: Oldham et al. (2000). Zones have "
                     "no sharp boundaries — on a margin, take the middle value (B).")
        cap.setWordWrap(True)
        cap.setStyleSheet("color:%s; font-size:11px; background:transparent;" % theme.FAINT)
        layout.addWidget(cap)

    def _build_cat_body(self, layout, factor, refs):
        seg = ("QPushButton { font-size:12px; padding:6px 4px; border:1px solid %s;"
               " border-radius:6px; background:%s; color:%s; font-family:%s; }"
               " QPushButton:hover:!checked { background:%s; }"
               " QPushButton:checked { background:%s; color:white; border-color:%s;"
               " font-weight:600; }"
               % (theme.BORDER_STRONG, theme.CARD, theme.MUTED, theme.FONT_MEDIUM,
                  theme.ACCENT_SOFT, theme.ACCENT, theme.ACCENT))
        row = QHBoxLayout()
        row.setSpacing(5)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for i, (label, si, _help) in enumerate(factor["options"]):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setToolTip("SI %.2f — %s" % (si, _help))
            b.setStyleSheet(seg)
            b.clicked.connect(lambda _c=False, f=factor, idx=i: self._on_cat(f, idx))
            grp.addButton(b)
            row.addWidget(b, 1)
        layout.addLayout(row)
        helpl = QLabel("Choose a category above.")
        helpl.setWordWrap(True)
        helpl.setStyleSheet("color:%s; font-size:11px; background:transparent; padding-top:2px;"
                            % theme.FAINT)
        layout.addWidget(helpl)
        refs["help"] = helpl
        refs["group"] = grp

    def _build_graph_body(self, layout, factor, refs):
        key = factor["key"]
        irow = QHBoxLayout()
        irow.setSpacing(8)
        prompt = QLabel(GRAPH_INPUT_LABEL.get(key, factor["title"]))
        prompt.setStyleSheet("color:%s; font-size:12px; background:transparent;" % theme.INK)
        irow.addWidget(prompt)
        irow.addStretch(1)
        # Pond count starts at 0 and steps by 1; the others start unset and snap
        # to clean multiples of their step (and accept any typed value). Flanked by
        # real − / + buttons (native spin buttons can't show +/− once QSS-styled).
        spin = _StepSpin(factor.get("step", 1), factor.get("maxinput", 100000),
                         factor["unit"], unset=not factor.get("count"))
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedWidth(94)
        spin.valueChanged.connect(lambda v, f=factor: self._on_graph(f, v))
        bqss = ("QPushButton { background:%s; border:1px solid %s; border-radius:6px; }"
                " QPushButton:hover { background:%s; border-color:%s; }"
                " QPushButton:pressed { background:%s; }"
                % (theme.CARD, theme.BORDER_STRONG, theme.ACCENT_SOFT,
                   theme.ACCENT, theme.SUNKEN))
        stepper = QWidget()
        shl = QHBoxLayout(stepper)
        shl.setContentsMargins(0, 0, 0, 0)
        shl.setSpacing(4)
        for icon_name, delta in (("minus", -1), ("plus", 1)):
            b = QPushButton()
            b.setIcon(theme.icon(icon_name, theme.INK, 16))
            b.setIconSize(QSize(16, 16))
            b.setFixedSize(30, 30)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setStyleSheet(bqss)
            b.setAutoRepeat(True)
            b.setAutoRepeatInterval(120)
            b.clicked.connect(lambda _c=False, s=spin, d=delta: s.stepBy(d))
            if delta < 0:
                shl.addWidget(b); shl.addWidget(spin)
            else:
                shl.addWidget(b)
        irow.addWidget(stepper)
        if key == "SI2":
            draw = QPushButton("Draw pond area")
            draw.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            draw.setToolTip(
                "Temporarily draw the pond boundary on the live map and use its area")
            draw.setStyleSheet(bqss + " QPushButton { padding:5px 10px; }")
            draw.clicked.connect(self._start_pond_area_draw)
            irow.addWidget(draw)
        refs["spin"] = spin
        if factor.get("count"):
            dens = QLabel("= — / km²")
            dens.setStyleSheet("color:%s; font-size:11px; background:transparent; min-width:78px;"
                               % theme.MUTED)
            irow.addWidget(dens)
            refs["density"] = dens
        layout.addLayout(irow)

        graph = _CurveGraph(factor["curve"], factor["xmin"], factor["xmax"],
                            factor["xlabel"], log=factor.get("log", False))
        layout.addWidget(graph)
        refs["graph"] = graph

        self._add_disclosure(layout, factor["ref"], "ARG UK guide graph", expanded=False)
        if factor.get("ref2"):
            self._add_disclosure(layout, factor["ref2"], factor.get("ref2_label", "Reference"),
                                 expanded=True)

    # ---- reference-image disclosure --------------------------------------
    def _add_disclosure(self, layout, png, caption, expanded=False):
        btn = QPushButton(("▾  " if expanded else "▸  ") + caption)
        btn.setCheckable(True)
        btn.setChecked(expanded)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(
            "QPushButton { border:none; background:transparent; color:%s; font-size:11px;"
            " font-family:%s; text-align:left; padding:2px 0; }"
            " QPushButton:hover { color:%s; }"
            % (theme.ACCENT, theme.FONT_MEDIUM, theme.ACCENT_DARK))
        img = self._image_label(png, 280)
        img.setVisible(expanded)

        def _toggle(checked, _b=btn, _i=img, _cap=caption):
            _i.setVisible(checked)
            _b.setText(("▾  " if checked else "▸  ") + _cap)

        btn.toggled.connect(_toggle)
        layout.addWidget(btn)
        layout.addWidget(img)

    def _image_label(self, png, max_w):
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        path = os.path.join(_RES_DIR, png)
        pm = QPixmap(path)
        if pm.isNull():
            lbl.setText("(reference image unavailable)")
            lbl.setStyleSheet("color:%s; font-size:11px;" % theme.FAINT)
            return lbl
        target = max_w * 2
        if pm.width() > target:
            pm = pm.scaledToWidth(target, Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(2)
        lbl.setPixmap(pm)
        lbl.setStyleSheet("background:white; border:1px solid %s; border-radius:6px; padding:5px;"
                          % theme.BORDER)
        return lbl

    # ---- result + comparison --------------------------------------------
    def _build_result_card(self):
        w = QWidget()
        w.setObjectName("hsiResult")
        w.setStyleSheet("#hsiResult { background:%s; border:1px solid %s; border-radius:8px; }"
                        % (theme.CARD, theme.BORDER_STRONG))
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        cap = QLabel("HSI")
        cap.setStyleSheet("color:%s; font-size:12px; font-family:%s; background:transparent;"
                          % (theme.MUTED, theme.FONT_MEDIUM))
        self._hsi_big = QLabel("—")
        self._hsi_big.setStyleSheet("font-family:%s; font-size:42px; font-weight:600; color:%s;"
                                    " background:transparent;" % (theme.FONT_SEMIBOLD, theme.INK))
        self._band_pill = QLabel("")
        self._band_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(cap)
        top.addWidget(self._hsi_big)
        top.addStretch(1)
        top.addWidget(self._band_pill)
        v.addLayout(top)

        # band scale
        bands = QHBoxLayout()
        bands.setSpacing(4)
        for _thr, label, rng in G.BANDS:
            fg, bg = BAND_STYLE[label]
            chip = QLabel("%s\n%s" % (label, rng))
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setStyleSheet("color:%s; background:%s; border-radius:5px; padding:3px 4px;"
                               " font-size:10px; font-family:%s;" % (fg, bg, theme.FONT_MEDIUM))
            bands.addWidget(chip, 1)
        v.addLayout(bands)

        self._add_disclosure(v, "pond_occupancy.png",
                             "Pond occupancy by HSI band (ARG UK)", expanded=True)
        foot = QLabel("HSI indicates habitat suitability — it is not a substitute for a "
                      "newt survey. Higher scores are associated with greater occupancy.")
        foot.setWordWrap(True)
        foot.setStyleSheet("color:%s; font-size:11px; background:transparent;" % theme.FAINT)
        v.addWidget(foot)
        return w

    def _build_save_pond_button(self):
        save = QPushButton("Save pond")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.setMinimumHeight(42)
        save.setStyleSheet(self._btn_qss(filled=True))
        save.setToolTip("Save this completed assessment to Compare ponds")
        save.clicked.connect(self._add_pond)
        return save

    def _build_compare_card(self):
        w = QWidget()
        w.setObjectName("hsiCard")
        w.setStyleSheet("#hsiCard { background:%s; border:1px solid %s; border-radius:8px; }"
                        % (theme.CARD, theme.BORDER))
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(6)
        title = QLabel("Compare ponds")
        title.setStyleSheet("font-family:%s; font-size:13px; font-weight:600; color:%s;"
                            " background:transparent;" % (theme.FONT_SEMIBOLD, theme.INK))
        v.addWidget(title)
        desc = QLabel("All Pond Mapper water bodies are listed, including those without an HSI. "
                      "Set each pond's survey status here; choosing survey undertaken opens that pond above for scoring. "
                      "Use Edit to reopen and amend a completed assessment.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color:%s; font-size:11px; background:transparent;" % theme.MUTED)
        v.addWidget(desc)

        factor_headers = ["%s %s" % (f["key"], f["title"]) for f in G.FACTORS]
        self._table = QTableWidget(0, 17)
        self._table.setHorizontalHeaderLabels(
            ["Pond", "Grid ref", "Distance", "Status"] + factor_headers
            + ["HSI", "Suitability", "Edit"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # This is the pond worklist, not a small summary: give it enough room
        # to review roughly ten ponds and their statuses without constant
        # scrolling inside the table.
        self._table.setMinimumHeight(650)
        self._table.setMinimumWidth(1040)
        self._table.verticalHeader().setDefaultSectionSize(29)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, 17):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(3, 310)
        self._table.setStyleSheet(
            "QTableWidget { background:%s; border:1px solid %s; border-radius:6px;"
            " gridline-color:%s; font-size:12px; }"
            " QHeaderView::section { background:%s; color:%s; border:none;"
            " border-bottom:1px solid %s; padding:5px 8px; font-family:%s; font-size:11px; }"
            % (theme.CARD, theme.BORDER, theme.BORDER, theme.SUNKEN, theme.MUTED,
               theme.BORDER, theme.FONT_MEDIUM))
        v.addWidget(self._table)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        copyr = QPushButton("📋  Copy report")
        copyr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copyr.setStyleSheet(self._btn_qss(filled=True))
        copyr.setToolTip("Copy a report containing every pond and completed HSI")
        copyr.clicked.connect(self._copy_comparison_report)
        export = QPushButton("🖼  Export PNG")
        export.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        export.setStyleSheet(self._btn_qss())
        export.setToolTip("Export a single summary image containing all ponds")
        export.clicked.connect(self._export_comparison_png)
        copyt = QPushButton("Copy table")
        copyt.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copyt.setStyleSheet(self._btn_qss())
        copyt.clicked.connect(self._copy_table)
        clr = QPushButton("Clear")
        clr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clr.setStyleSheet(self._btn_qss())
        clr.clicked.connect(self._clear_table)
        btns.addWidget(copyr)
        btns.addWidget(export)
        btns.addStretch(1)
        btns.addWidget(copyt)
        btns.addWidget(clr)
        v.addLayout(btns)
        return w

    def _btn_qss(self, filled=False):
        if filled:
            return ("QPushButton { background:%s; color:white; border:none; border-radius:6px;"
                    " padding:7px 12px; font-size:12px; font-weight:600; font-family:%s; }"
                    " QPushButton:hover { background:%s; }"
                    % (theme.ACCENT, theme.FONT_SEMIBOLD, theme.ACCENT_DARK))
        return ("QPushButton { background:%s; color:%s; border:1px solid %s; border-radius:6px;"
                " padding:7px 12px; font-size:12px; font-family:%s; }"
                " QPushButton:hover { background:%s; }"
                % (theme.CARD, theme.INK, theme.BORDER_STRONG, theme.FONT_MEDIUM, theme.ACCENT_SOFT))

    def _build_footer(self):
        w = QWidget()
        w.setStyleSheet("background:#EFF2ED;")
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)
        self._status = QLabel("")
        self._status.setStyleSheet("font-size:12px; color:%s;" % theme.MUTED)
        close = QPushButton("Close")
        close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close.setStyleSheet(
            "QPushButton { background:%s; color:%s; border:1px solid %s; border-radius:5px;"
            " padding:8px 16px; font-size:12px; }"
            " QPushButton:hover { background:%s; }"
            % (theme.CARD, theme.INK, theme.BORDER_STRONG, theme.ACCENT_SOFT))
        close.clicked.connect(self.accept)
        h.addWidget(self._status, 1)
        h.addWidget(close)
        return w

    # ---- interaction -----------------------------------------------------
    def _set_pill(self, key, si, omit):
        pill = self._cards[key]["pill"]
        if omit:
            txt, (fg, bg) = "n/a", (theme.MUTED, theme.SUNKEN)
        elif si is None:
            txt, (fg, bg) = "—", (theme.MUTED, theme.SUNKEN)
        else:
            txt, (fg, bg) = "%.2f" % si, _si_colours(si)
        pill.setText("SI " + txt)
        pill.setStyleSheet(_pill_qss(fg, bg))

    def _on_cat(self, factor, idx):
        key = factor["key"]
        label, si, help_ = factor["options"][idx]
        self._state[key] = si
        self._display[key] = label
        self._inputs[key] = idx
        self._si_str[key] = "%.2f" % si
        self._set_pill(key, si, False)
        self._cards[key]["help"].setText("%s · SI %.2f — %s" % (label, si, help_))
        self._recompute()

    def _on_graph(self, factor, v):
        key = factor["key"]
        refs = self._cards[key]
        if v < 0:                                   # unset
            self._state[key] = None
            self._display[key] = None
            self._inputs[key] = None
            self._si_str[key] = "—"
            refs["graph"].set_marker(None, None)
            if "density" in refs:
                refs["density"].setText("= — / km²")
            self._set_pill(key, None, False)
            self._recompute()
            return
        si, omit = G.graph_si(factor, v)
        self._inputs[key] = int(v)
        if factor.get("count"):
            dens = G.density_for_count(v)
            refs["density"].setText("= %.2f / km²" % dens)
            refs["graph"].set_marker(dens, si, omit)
            self._display[key] = "%d pond%s (%.2f / km²)" % (v, "" if v == 1 else "s", dens)
        else:
            refs["graph"].set_marker(v, si, omit)
            self._display[key] = "%d %s%s" % (v, factor["unit"],
                                              " (>2000 — omitted)" if omit else "")
        if omit:
            self._state[key] = G.OMIT
            self._si_str[key] = "n/a"
            self._set_pill(key, None, True)
        else:
            self._state[key] = si
            self._si_str[key] = "%.2f" % si
            self._set_pill(key, si, False)
        self._recompute()

    def _recompute(self):
        hsi, n, complete = G.compute_hsi(self._state)
        resolved = sum(1 for f in G.FACTORS if self._state.get(f["key"]) is not None)
        if complete and hsi is not None:
            band = G.band_for(hsi)
            fg, bg = BAND_STYLE.get(band, (theme.INK, theme.SURFACE))
            self._hsi_big.setText("%.2f" % hsi)
            self._hsi_big.setStyleSheet("font-family:%s; font-size:42px; font-weight:600;"
                                        " color:%s; background:transparent;"
                                        % (theme.FONT_SEMIBOLD, fg))
            extra = " · SI2 omitted (9 indices)" if n == 9 else ""
            self._band_pill.setText("  %s%s  " % (band.upper(), extra))
            self._band_pill.setStyleSheet(
                "color:%s; background:%s; border-radius:11px; padding:5px 12px;"
                " font-family:%s; font-size:13px; font-weight:600;" % (fg, bg, theme.FONT_SEMIBOLD))
        else:
            self._hsi_big.setText("—")
            self._hsi_big.setStyleSheet("font-family:%s; font-size:42px; font-weight:600;"
                                        " color:%s; background:transparent;"
                                        % (theme.FONT_SEMIBOLD, theme.FAINT))
            self._band_pill.setText("  %d / 10 indices set  " % resolved)
            self._band_pill.setStyleSheet(
                "color:%s; background:%s; border-radius:11px; padding:5px 12px;"
                " font-family:%s; font-size:12px; font-weight:600;"
                % (theme.MUTED, theme.SUNKEN, theme.FONT_MEDIUM))

    # ---- report + table --------------------------------------------------
    def _report_text(self):
        hsi, n, complete = G.compute_hsi(self._state)
        name = self._name_edit.text().strip() or "(unnamed pond)"
        ref = self._ref_edit.text().strip() or "—"
        distance = self._distance_edit.text().strip() or "—"
        lines = [
            "Great Crested Newt — Habitat Suitability Index (HSI)",
            "Method: ARG UK Advice Note 5 (2010), after Oldham et al. (2000)",
            "",
            "Pond: %s    Grid ref: %s    Distance from site: %s"
            % (name, ref, distance),
            "",
        ]
        for f in G.FACTORS:
            val = self._display.get(f["key"]) or "—"
            sis = self._si_str.get(f["key"]) or "—"
            lines.append("%-5s %-21s %-28s %s" % (f["key"], f["title"], val, sis))
        lines.append("")
        if complete and hsi is not None:
            extra = "  (SI2 omitted — 9 indices)" if n == 9 else ""
            lines.append("HSI = %.2f%s    Pond suitability: %s"
                         % (hsi, extra, G.band_for(hsi).upper()))
        else:
            lines.append("HSI = —    (set all indices first)")
        return "\n".join(lines)

    def _copy_report(self):
        QApplication.clipboard().setText(self._report_text())
        hsi, _n, complete = G.compute_hsi(self._state)
        self._status.setText("Report copied to clipboard ✓" if complete
                             else "Copied (note: indices still incomplete)")

    def _add_pond(self):
        hsi, _n, complete = G.compute_hsi(self._state)
        if not complete or hsi is None:
            self._status.setText("Set all indices before adding the pond.")
            return
        name = self._name_edit.text().strip() or "(unnamed)"
        ref = self._ref_edit.text().strip() or "—"
        mapped_id = self._active_mapped_id
        distance_label = self._distance_edit.text().strip() or "—"
        distance_m = None
        if mapped_id:
            mapped = next((p for p in self._mapped_pond_options()
                           if p["pond_id"] == mapped_id), None)
            if mapped:
                distance_m = mapped["distance_m"]
        elif ref != "—":
            try:
                from qgis.core import QgsGeometry, QgsPointXY
                from .gridref_utils import gridref_to_osgb_en
                from .pond_mapper import _project_redline_bng
                easting, northing = gridref_to_osgb_en(ref)
                redline = _project_redline_bng()
                if redline is not None and not redline.isEmpty():
                    distance_m = int(round(redline.distance(
                        QgsGeometry.fromPointXY(QgsPointXY(float(easting), float(northing))))))
            except Exception:
                distance_m = None
        band = G.band_for(hsi)
        pond = {"name": name, "ref": ref, "hsi": hsi, "band": band,
                "state": dict(self._state), "mapped_id": mapped_id,
                "display": dict(self._display), "inputs": dict(self._inputs),
                "distance_m": distance_m, "distance_label": distance_label,
                "status": "hsi_complete"}

        # Treat a matching pond name or grid reference as the same assessment,
        # so pressing Save again updates it instead of creating duplicates.
        match = None
        for i, saved in enumerate(self._ponds):
            same_mapped = mapped_id and saved.get("mapped_id") == mapped_id
            same_name = name != "(unnamed)" and saved.get("name") == name
            # Six-figure grid references describe 100 m squares and are not
            # unique: several nearby ponds can legitimately share one. Mapped
            # ponds therefore match only by their stable Pond Mapper ID.
            if same_mapped or (not mapped_id and not saved.get("mapped_id") and same_name):
                match = i
                break
        if match is None:
            self._ponds.append(pond)
            action = "Saved"
        else:
            self._ponds[match] = pond
            action = "Updated"
        self._refresh_pond_table()
        if self._save_ponds():
            message = "%s “%s” in this project (%d pond%s saved)." % (
                action, name, len(self._ponds),
                "" if len(self._ponds) == 1 else "s")
            self._start_manual_pond()
            self._tabs.setCurrentIndex(0)
            self._status.setText(message)

    def _append_pond_row(self, pond):
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(pond["name"]))
        self._table.setItem(r, 1, QTableWidgetItem(pond["ref"]))
        self._table.setItem(r, 2, QTableWidgetItem(pond.get("distance_label") or "—"))
        status = str(pond.get("status") or "not_assessed")
        status_combo = QComboBox()
        options = list(POND_STATUS_OPTIONS)
        if status == "hsi_complete":
            options.insert(1, ("hsi_complete", "HSI completed"))
        for code, label in options:
            status_combo.addItem(label, code)
        idx = status_combo.findData(status)
        status_combo.setCurrentIndex(max(0, idx))
        status_combo.setToolTip("Set the survey outcome for this pond")
        status_combo.currentIndexChanged.connect(
            lambda _i, p=pond, c=status_combo: self._on_pond_status_changed(
                p, str(c.currentData() or "not_assessed")))
        self._table.setCellWidget(r, 3, status_combo)
        for offset, factor in enumerate(G.FACTORS, 4):
            self._table.setItem(
                r, offset, QTableWidgetItem(
                    self._assessment_text(pond, factor, include_si=True)))
        hsi = pond.get("hsi")
        self._table.setItem(r, 14, QTableWidgetItem("%.2f" % hsi if hsi is not None else "—"))
        band = pond.get("band") or "—"
        bitem = QTableWidgetItem(band)
        fg, _bg = BAND_STYLE.get(band, (theme.INK, theme.SURFACE))
        bitem.setForeground(QColor(fg))
        self._table.setItem(r, 15, bitem)
        edit = QPushButton("Edit")
        edit.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        edit.setStyleSheet(self._btn_qss())
        edit.setEnabled(hsi is not None)
        edit.setToolTip("Reopen and amend this pond's saved HSI assessment")
        edit.clicked.connect(lambda _checked=False, p=pond: self._edit_pond(p))
        self._table.setCellWidget(r, 16, edit)

    def _assessment_text(self, pond, factor, include_si=False):
        """Return the saved human assessment, reconstructing legacy records."""
        key = factor["key"]
        state = (pond.get("state") or {}).get(key)
        display = str((pond.get("display") or {}).get(key) or "").strip()
        if not display and factor["type"] == "cat" and isinstance(state, (int, float)):
            match = next((option for option in factor["options"]
                          if abs(float(option[1]) - float(state)) < 0.000001), None)
            display = match[0] if match else ""
        if not display and state == G.OMIT:
            display = "Not applicable"
        if not display and isinstance(state, (int, float)):
            display = "Recorded score"
        if not display:
            return "—"
        if include_si:
            score = "n/a" if state == G.OMIT else (
                "%.2f" % state if isinstance(state, (int, float)) else "—")
            return "%s (%s)" % (display, score)
        return display

    def _infer_graph_input(self, factor, target):
        """Recover the closest raw measurement from a legacy saved SI value."""
        if not isinstance(target, (int, float)):
            return None
        best_value, best_delta = None, float("inf")
        for value in range(0, int(factor.get("maxinput", 0)) + 1):
            score, omitted = G.graph_si(factor, value)
            if omitted or score is None:
                continue
            delta = abs(float(score) - float(target))
            if delta < best_delta:
                best_value, best_delta = value, delta
                if delta < 0.000001:
                    break
        return best_value

    def _edit_pond(self, pond):
        """Reload a completed saved assessment into the editable HSI form."""
        mapped_id = str(pond.get("mapped_id") or "")
        self._active_mapped_id = mapped_id
        self._ref_edit.setReadOnly(bool(mapped_id))
        self._ref_pick_btn.setEnabled(not bool(mapped_id))
        self._name_edit.setText(str(pond.get("name") or ""))
        self._ref_edit.setText(str(pond.get("ref") or ""))
        self._distance_edit.setText(str(pond.get("distance_label") or ""))
        self._clear_hsi_form()
        saved_state = pond.get("state") or {}
        saved_inputs = pond.get("inputs") or {}
        for factor in G.FACTORS:
            key = factor["key"]
            target = saved_state.get(key)
            if factor["type"] == "cat":
                idx = saved_inputs.get(key)
                if not isinstance(idx, int) or not (0 <= idx < len(factor["options"])):
                    idx = next((i for i, option in enumerate(factor["options"])
                                if isinstance(target, (int, float))
                                and abs(float(option[1]) - float(target)) < 0.000001), None)
                if idx is not None:
                    buttons = self._cards[key]["group"].buttons()
                    buttons[idx].setChecked(True)
                    self._on_cat(factor, idx)
            else:
                value = saved_inputs.get(key)
                if not isinstance(value, int):
                    value = self._infer_graph_input(factor, target)
                if value is not None:
                    self._cards[key]["spin"].setValue(value)
        self._tabs.setCurrentIndex(1)
        self._hsi_scroll.verticalScrollBar().setValue(0)
        self._status.setText("Editing the saved HSI assessment for %s." % pond.get("name", "this pond"))

    def _on_pond_status_changed(self, pond, status):
        pond["status"] = status
        if status != "hsi_complete":
            pond["hsi"] = None
            pond["band"] = "—"
            pond["state"] = {f["key"]: None for f in G.FACTORS}
            pond["display"] = {}
            pond["inputs"] = {}
        self._save_ponds()
        if status != "hsi_required":
            self._refresh_pond_table()
            return

        self._refresh_pond_table()
        mapped_id = str(pond.get("mapped_id") or "")
        if mapped_id:
            self._active_mapped_id = mapped_id
            self._ref_edit.setReadOnly(True)
            self._ref_pick_btn.setEnabled(False)
            live = next((p for p in self._mapped_pond_options()
                         if p["pond_id"] == mapped_id), None)
            self._name_edit.setText((live or {}).get("pond_id") or pond.get("name") or "")
            self._ref_edit.setText((live or {}).get("grid_ref") or pond.get("ref") or "")
            self._distance_edit.setText(
                (live or {}).get("distance_label") or pond.get("distance_label") or "")
        else:
            self._active_mapped_id = ""
            self._ref_edit.setReadOnly(False)
            self._ref_pick_btn.setEnabled(True)
            self._name_edit.setText(pond.get("name") or "")
            self._ref_edit.setText(pond.get("ref") or "")
            self._distance_edit.setText(pond.get("distance_label") or "")
        self._clear_hsi_form()
        self._tabs.setCurrentIndex(1)
        self._hsi_scroll.verticalScrollBar().setValue(0)
        self._name_edit.setFocus()
        self._status.setText("Complete the HSI for %s." % pond.get("name", "this pond"))

    def _clear_hsi_form(self):
        """Clear score controls before starting an HSI for another pond."""
        self._state = {f["key"]: None for f in G.FACTORS}
        self._display = {}
        self._inputs = {}
        self._si_str = {}
        for factor in G.FACTORS:
            refs = self._cards[factor["key"]]
            if factor["type"] == "cat":
                group = refs.get("group")
                if group is not None:
                    group.setExclusive(False)
                    for button in group.buttons():
                        button.setChecked(False)
                    group.setExclusive(True)
                refs["help"].setText("Choose a category above.")
                self._set_pill(factor["key"], None, False)
            else:
                spin = refs["spin"]
                spin.blockSignals(True)
                spin.setValue(0 if factor.get("count") else _StepSpin.UNSET)
                spin.blockSignals(False)
                self._on_graph(factor, spin.value())
        self._recompute()

    def _refresh_pond_table(self):
        self._table.setRowCount(0)
        for pond in self._ponds:
            self._append_pond_row(pond)

    def _save_ponds(self):
        """Store the comparison list inside the current QGIS project."""
        try:
            payload = json.dumps(self._ponds, ensure_ascii=False, separators=(",", ":"))
            project = QgsProject.instance()
            project.writeEntry(_PROJECT_SCOPE, _PROJECT_HSI_KEY, payload)
            project.setDirty(True)
            return True
        except Exception as ex:
            self._status.setText("Could not save the pond list to the project: %s" % ex)
            return False

    def _load_saved_ponds(self):
        """Merge saved assessments with every pond currently in Pond Mapper."""
        raw, ok = QgsProject.instance().readEntry(
            _PROJECT_SCOPE, _PROJECT_HSI_KEY, "")
        try:
            loaded = json.loads(raw) if ok and raw else []
            saved_ponds = []
            factor_keys = {f["key"] for f in G.FACTORS}
            mapped = self._mapped_pond_options()
            for item in loaded if isinstance(loaded, list) else []:
                if not isinstance(item, dict) or not isinstance(item.get("state"), dict):
                    continue
                state = {key: item["state"].get(key) for key in factor_keys}
                hsi, _n, complete = G.compute_hsi(state)
                if not complete:
                    hsi = None
                saved_ref = str(item.get("ref") or "—")
                saved_id = str(item.get("mapped_id") or "")
                if saved_id:
                    live = next((p for p in mapped
                                 if p["pond_id"] == saved_id), None)
                else:
                    # Legacy manual records had no mapped ID. A grid-reference
                    # fallback may reconnect one, but must never be used to
                    # deduplicate two current Pond Mapper features.
                    live = next((p for p in mapped
                                 if saved_ref != "—" and p["grid_ref"] == saved_ref), None)
                status = str(item.get("status") or
                             ("hsi_complete" if hsi is not None else "not_assessed"))
                saved_ponds.append({
                    "name": live["pond_id"] if live else str(item.get("name") or "(unnamed)"),
                    "ref": live["grid_ref"] if live else saved_ref,
                    "mapped_id": live["pond_id"] if live else saved_id,
                    "distance_m": live["distance_m"] if live else item.get("distance_m"),
                    "distance_label": (live["distance_label"] if live else
                                       str(item.get("distance_label") or "—")),
                    "hsi": hsi,
                    "band": G.band_for(hsi) if hsi is not None else "—",
                    "state": state,
                    "display": dict(item.get("display") or {}),
                    "inputs": dict(item.get("inputs") or {}),
                    "status": status,
                })

            # Add every mapped pond not already represented by a saved record.
            for live in mapped:
                existing = next((p for p in saved_ponds
                                 if p.get("mapped_id") == live["pond_id"]), None)
                if existing is not None:
                    continue
                saved_ponds.append({
                    "name": live["pond_id"],
                    "ref": live["grid_ref"] or "—",
                    "mapped_id": live["pond_id"],
                    "distance_m": live["distance_m"],
                    "distance_label": live["distance_label"],
                    "hsi": None,
                    "band": "—",
                    "state": {key: None for key in factor_keys},
                    "display": {},
                    "inputs": {},
                    "status": "not_assessed",
                })

            self._ponds = sorted(
                saved_ponds,
                key=lambda p: (p.get("distance_m") is None,
                               p.get("distance_m") or 0, p.get("name") or ""))
            self._refresh_pond_table()
            if self._ponds:
                self._save_ponds()
                self._status.setText("Loaded %d pond%s from this project."
                                     % (len(self._ponds), "" if len(self._ponds) == 1 else "s"))
        except Exception:
            self._ponds = []
            self._table.setRowCount(0)
            self._status.setText("The saved pond comparison list could not be read.")

    def _copy_table(self):
        if not self._ponds:
            self._status.setText("Add a pond to the table first.")
            return
        header = ["Pond", "Grid ref", "Distance from site", "Survey status"] + [
            "%s %s" % (f["key"], f["title"]) for f in G.FACTORS
        ] + ["HSI", "Suitability"]
        rows = ["\t".join(header)]
        for p in self._ponds:
            cells = [p["name"], p["ref"], p.get("distance_label") or "—",
                     POND_STATUS_LABELS.get(p.get("status"), "Not assessed")]
            for f in G.FACTORS:
                cells.append(self._assessment_text(p, f, include_si=True))
            cells.append("%.2f" % p["hsi"] if p.get("hsi") is not None else "")
            cells.append(p.get("band") if p.get("hsi") is not None else "")
            rows.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(rows))
        n = len(self._ponds)
        self._status.setText("Comparison table copied (%d pond%s) ✓" % (n, "" if n == 1 else "s"))

    def _copy_comparison_report(self):
        if not self._ponds:
            self._status.setText("There are no ponds to include in the report.")
            return
        lines = ["Great Crested Newt — Pond Assessment Summary", ""]
        for pond in self._ponds:
            status = POND_STATUS_LABELS.get(
                pond.get("status"), "Not assessed")
            lines.append("Pond: %s" % pond.get("name", "(unnamed)"))
            lines.append("Grid reference: %s    Distance from site: %s" % (
                pond.get("ref") or "—", pond.get("distance_label") or "—"))
            lines.append("Survey status: %s" % status)
            if pond.get("hsi") is not None:
                lines.append("HSI: %.2f    Pond suitability: %s" % (
                    pond["hsi"], str(pond.get("band") or "—").upper()))
                state = pond.get("state") or {}
                lines.append("Suitability indices: " + "; ".join(
                    "%s %s" % (factor["key"],
                                self._assessment_text(
                                    pond, factor, include_si=True))
                    for factor in G.FACTORS))
            lines.append("")
        QApplication.clipboard().setText("\n".join(lines).rstrip())
        self._status.setText("Multi-pond assessment report copied (%d ponds) ✓"
                             % len(self._ponds))

    def _export_comparison_png(self):
        if not self._ponds:
            self._status.setText("There are no ponds to export.")
            return
        base = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        default = os.path.join(base, "GCN_HSI_pond_summary.png")
        path, _f = QFileDialog.getSaveFileName(
            self, "Export pond assessment summary", default, "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"

        scale = 2
        width = 1200
        row_h = 142
        legend_h = 116
        height = 150 + row_h * len(self._ponds) + legend_h
        image = QImage(width * scale, height * scale, QImage.Format.Format_RGB32)
        image.fill(QColor(theme.SURFACE))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(scale, scale)

        painter.fillRect(0, 0, width, 82, QColor(theme.ACCENT_DARK))
        font = QFont(theme.FONT_SEMIBOLD); font.setPixelSize(22); font.setBold(True)
        painter.setFont(font); painter.setPen(QColor("white"))
        painter.drawText(QRectF(28, 15, width - 56, 34),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "Great Crested Newt — Pond Assessment Summary")
        font.setPixelSize(11); font.setBold(False); painter.setFont(font)
        painter.setPen(QColor("#CFE3D6"))
        painter.drawText(QRectF(28, 49, width - 56, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "%d ponds recorded in this QGIS project" % len(self._ponds))

        columns = [(28, 120, "Pond"), (148, 150, "Grid reference"),
                   (298, 145, "Distance"), (443, 405, "Survey status"),
                   (848, 100, "HSI"), (948, 224, "Suitability")]
        y = 98
        painter.fillRect(24, y, width - 48, 34, QColor(theme.SUNKEN))
        font.setPixelSize(11); font.setBold(True); painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        for x, w, heading in columns:
            painter.drawText(QRectF(x + 6, y, w - 12, 34),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             heading)
        y += 34
        font.setBold(False); painter.setFont(font)
        for index, pond in enumerate(self._ponds):
            bg = theme.CARD if index % 2 == 0 else theme.SURFACE
            painter.fillRect(24, y, width - 48, row_h, QColor(bg))
            painter.setPen(QColor(theme.BORDER))
            painter.drawLine(24, y + row_h, width - 24, y + row_h)
            status = POND_STATUS_LABELS.get(pond.get("status"), "Not assessed")
            values = [pond.get("name") or "—", pond.get("ref") or "—",
                      pond.get("distance_label") or "—", status,
                      "%.2f" % pond["hsi"] if pond.get("hsi") is not None else "—",
                      pond.get("band") if pond.get("hsi") is not None else "—"]
            font.setPixelSize(11); font.setBold(False); painter.setFont(font)
            painter.setPen(QColor(theme.INK))
            for (x, w, _heading), value in zip(columns, values):
                painter.drawText(QRectF(x + 6, y, w - 12, 38),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 str(value))

            # Two compact rows of five factors retain the actual assessment
            # choices (for example Zone A and Rarely dries), not only SI scores.
            factor_top = y + 42
            factor_w = (width - 56) / 5.0
            for factor_index, factor in enumerate(G.FACTORS):
                grid_row, grid_col = divmod(factor_index, 5)
                fx = 28 + grid_col * factor_w
                fy = factor_top + grid_row * 46
                font.setPixelSize(8); font.setBold(True); painter.setFont(font)
                painter.setPen(QColor(theme.FAINT))
                painter.drawText(QRectF(fx + 5, fy, factor_w - 10, 14),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 "%s · %s" % (factor["key"], factor["title"]))
                font.setPixelSize(9); font.setBold(False); painter.setFont(font)
                painter.setPen(QColor(theme.INK))
                painter.drawText(QRectF(fx + 5, fy + 14, factor_w - 10, 28),
                                 int(Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignTop
                                     | Qt.TextFlag.TextWordWrap),
                                 self._assessment_text(
                                     pond, factor, include_si=True))
            y += row_h

        # Explain the Lee Brady bands on the exported image itself so the
        # suitability labels remain meaningful when the PNG is viewed alone.
        y += 18
        font.setPixelSize(10); font.setBold(True); painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(QRectF(28, y, width - 56, 16),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "HSI SCORE CATEGORIES (LEE BRADY)")
        y += 22
        legend_x = 28
        legend_w = (width - 56) / len(G.BANDS)
        for index, (_threshold, label, score_range) in enumerate(G.BANDS):
            fg, bg = BAND_STYLE[label]
            x = legend_x + index * legend_w
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(QRectF(x + 2, y, legend_w - 4, 32), 6, 6)
            font.setPixelSize(10); font.setBold(True); painter.setFont(font)
            painter.setPen(QColor(fg))
            painter.drawText(QRectF(x + 8, y, legend_w - 16, 18),
                             Qt.AlignmentFlag.AlignCenter, label)
            font.setPixelSize(9); font.setBold(False); painter.setFont(font)
            painter.drawText(QRectF(x + 8, y + 16, legend_w - 16, 14),
                             Qt.AlignmentFlag.AlignCenter, score_range)
        y += 40
        font.setPixelSize(9); font.setBold(False); painter.setFont(font)
        painter.setPen(QColor(theme.FAINT))
        painter.drawText(QRectF(28, y, width - 56, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "HSI describes habitat suitability; it does not confirm great crested newt presence or absence.")
        painter.end()
        ok = image.save(path, "PNG")
        self._status.setText("Multi-pond summary PNG saved ✓" if ok
                             else "Could not save the summary PNG.")

    def _clear_table(self):
        self._ponds = []
        self._table.setRowCount(0)
        if self._save_ponds():
            self._status.setText("Saved comparison list cleared from this project.")

    # ---- PNG infographic export -----------------------------------------
    def _export_png(self):
        hsi, _n, complete = G.compute_hsi(self._state)
        if not complete or hsi is None:
            self._status.setText("Complete all indices before exporting.")
            return
        name = self._name_edit.text().strip() or "pond"
        safe = "".join(c if (c.isalnum() or c in " -_") else "_" for c in name).strip()
        safe = safe.replace(" ", "_") or "pond"
        base = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        default = os.path.join(base, "GCN_HSI_%s.png" % safe)
        path, _f = QFileDialog.getSaveFileName(self, "Export HSI infographic", default,
                                               "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        ok = self._render_infographic(scale=2).save(path, "PNG")
        self._status.setText("Infographic saved ✓" if ok else "Could not save image.")

    def _render_infographic(self, scale=2):
        """Paint a report-ready HSI summary card to a high-resolution QImage."""
        try:
            theme._load_fonts()
        except Exception:
            pass
        hsi, n, _complete = G.compute_hsi(self._state)
        band = G.band_for(hsi) or "—"
        bfg, bbg = BAND_STYLE.get(band, (theme.INK, theme.SURFACE))

        W, H = 820, 1040
        img = QImage(W * scale, H * scale, QImage.Format.Format_RGB32)
        img.fill(QColor(theme.SURFACE))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.scale(scale, scale)

        def setf(px, fam="Inter", w=QFont.Weight.Normal):
            f = QFont(fam); f.setPixelSize(px); f.setWeight(w)
            p.setFont(f)

        def txt(x, y, w, h, flags, s, color):
            p.setPen(QColor(color))
            p.drawText(QRectF(x, y, w, h), int(flags), s)

        def box(x, y, w, h, r, fill, border=None, bw=1.0):
            p.setPen(QPen(QColor(border), bw) if border else Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(fill)) if fill else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x, y, w, h), r, r)

        M = 34
        CW = W - 2 * M
        AL = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        # Header band
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.ACCENT_DARK))
        p.drawRect(QRectF(0, 0, W, 84))
        setf(20, "Inter SemiBold", QFont.Weight.DemiBold)
        txt(M, 12, CW, 32, AL, "Great Crested Newt — Habitat Suitability Index", "#FFFFFF")
        setf(11)
        txt(M, 48, CW, 20, AL, "Pond habitat assessment · ARG UK Advice Note 5 (2010), "
            "after Oldham et al. (2000)", "#CFE3D6")

        # Pond details
        y = 100
        name = self._name_edit.text().strip() or "(unnamed pond)"
        ref = self._ref_edit.text().strip() or "—"
        distance = self._distance_edit.text().strip() or "—"
        from .config import qt_date_format
        dt = self._date_edit.text().strip() or QDate.currentDate().toString(qt_date_format())
        cw = CW / 4.0
        for i, (lab, val) in enumerate([
                ("POND", name), ("GRID REFERENCE", ref),
                ("DISTANCE FROM SITE", distance), ("DATE", dt)]):
            x = M + i * cw
            setf(9, "Inter Medium", QFont.Weight.Medium)
            txt(x, y, cw, 15, AL, lab, theme.FAINT)
            setf(15, "Inter SemiBold", QFont.Weight.DemiBold)
            txt(x, y + 15, cw - 8, 24, AL, val, theme.INK)
        y += 56

        # Result card
        rh = 128
        box(M, y, CW, rh, 12, theme.CARD, theme.BORDER_STRONG, 1)
        setf(11, "Inter Medium", QFont.Weight.Medium)
        txt(M + 22, y + 18, 260, 16, AL, "HABITAT SUITABILITY INDEX", theme.MUTED)
        setf(60, "Inter SemiBold", QFont.Weight.DemiBold)
        txt(M + 18, y + 34, 230, 78, AL, "%.2f" % hsi, bfg)
        pw, ph = 296, 58
        px = M + CW - pw - 22; py = y + 24
        box(px, py, pw, ph, 12, bbg)
        setf(20, "Inter SemiBold", QFont.Weight.DemiBold)
        txt(px, py, pw, ph, Qt.AlignmentFlag.AlignCenter, band.upper(), bfg)
        setf(10)
        sub = "Geometric mean of %d indices%s" % (n, " · SI2 omitted" if n == 9 else "")
        txt(px, py + ph + 6, pw, 16, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, sub, theme.FAINT)
        y += rh + 20

        # Band scale
        setf(9, "Inter Medium", QFont.Weight.Medium)
        txt(M, y, CW, 14, AL, "HSI SCORE CATEGORIES (LEE BRADY)", theme.MUTED)
        y += 18
        sw = CW / 5.0; sh = 28
        for i, (_thr, lab, rng) in enumerate(G.BANDS):
            fg, bg = BAND_STYLE[lab]
            x = M + i * sw
            active = (lab == band)
            box(x + 2, y, sw - 4, sh, 6, bg, fg if active else None, 2.0 if active else 1.0)
            setf(10, "Inter SemiBold" if active else "Inter", QFont.Weight.DemiBold if active else QFont.Weight.Normal)
            txt(x, y, sw, sh, Qt.AlignmentFlag.AlignCenter, lab, fg)
            setf(8)
            txt(x, y + sh + 2, sw, 12, Qt.AlignmentFlag.AlignCenter, rng, theme.FAINT)
        y += sh + 30

        # SI rows
        setf(10, "Inter Medium", QFont.Weight.Medium)
        txt(M, y, CW, 16, AL, "SUITABILITY INDICES", theme.MUTED)
        y += 22
        rh2 = 56
        for f in G.FACTORS:
            key = f["key"]
            si = self._state.get(key)
            omit = (si == G.OMIT)
            val = self._display.get(key) or "—"
            sstr = self._si_str.get(key) or "—"
            box(M, y, 50, rh2 - 8, 7, theme.SUNKEN)
            setf(13, "Inter SemiBold", QFont.Weight.DemiBold)
            txt(M, y, 50, rh2 - 8, Qt.AlignmentFlag.AlignCenter, key, theme.MUTED)
            setf(14, "Inter Medium", QFont.Weight.Medium)
            txt(M + 62, y + 5, 300, 20, AL, f["title"], theme.INK)
            setf(11)
            txt(M + 62, y + 26, 330, 18, AL, val, theme.MUTED)
            bw = 200; bh = 12; bx = M + CW - 86 - bw; by = y + (rh2 - 8) / 2.0 - bh / 2.0
            box(bx, by, bw, bh, 6, theme.SUNKEN)
            if isinstance(si, float):
                box(bx, by, max(8.0, bw * si), bh, 6, _si_colours(si)[0])
            setf(16, "Inter SemiBold", QFont.Weight.DemiBold)
            scol = theme.MUTED if (si is None or omit) else _si_colours(si)[0]
            txt(M + CW - 80, y, 80, rh2 - 8, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "n/a" if omit else sstr, scol)
            y += rh2

        # Footer
        y += 4
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(M, y), QPointF(W - M, y))
        y += 10
        setf(9)
        txt(M, y, CW, 36, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            "HSI = (SI1 × … × SI10) ^ (1/n). A measure of habitat suitability for great crested "
            "newts — not a substitute for survey; higher scores are associated with greater "
            "pond occupancy.", theme.FAINT)
        p.end()
        return img
