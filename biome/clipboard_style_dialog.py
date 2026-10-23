# -*- coding: utf-8 -*-
"""Settings dialog for the desk-study report copy / paste style.

Opened from the main-panel settings cog. Edits the style (borders, row
fills, header, padding) with a live preview, persisted via
clipboard_utils.save_style().
"""
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox, QColorDialog, QFrame,
    QTableWidget, QTableWidgetItem, QWidget, QAbstractItemView, QAbstractSpinBox,
    QFontComboBox,
)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import Qt

from . import clipboard_utils as CU

_SAMPLE = [
    ["Habitat", "Approx. Closest Distance (km)"],
    ["Good quality semi improved grassland", "0.07 km E"],
    ["Deciduous woodland", "0.09 km SW"],
    ["Traditional orchard", "0.20 km NE"],
]


class _ColourButton(QPushButton):
    """A swatch button; click to pick a colour."""

    def __init__(self, colour, on_change, parent=None):
        super().__init__(parent)
        self._colour = colour
        self._on_change = on_change
        self.setFixedSize(56, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        self._refresh()

    def colour(self):
        return self._colour

    def setColour(self, c):
        self._colour = c
        self._refresh()

    def _refresh(self):
        self.setStyleSheet(
            "QPushButton{background:%s;border:1px solid #9AA39C;border-radius:4px;}"
            "QPushButton:disabled{border-style:dashed;}" % self._colour)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._colour), self, "Pick a colour")
        if c.isValid():
            self._colour = c.name()
            self._refresh()
            self._on_change()


class _FontChoice(QWidget):
    """A "Custom" checkbox + font-family picker + size spin. When the box is
    unchecked the value is ("", 0) meaning "inherit the document font"."""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._on = QCheckBox("Custom")
        self._combo = QFontComboBox()
        self._combo.setFixedWidth(178)
        self._size = QSpinBox()
        self._size.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self._size.setRange(6, 72)
        self._size.setValue(11)
        self._size.setSuffix(" pt")
        self._size.setFixedWidth(90)
        self._size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._on)
        lay.addWidget(self._combo)
        lay.addWidget(self._size)
        lay.addStretch()
        self._on.toggled.connect(self._refresh)
        self._on.toggled.connect(lambda *_: on_change())
        self._combo.currentFontChanged.connect(lambda *_: on_change())
        self._size.valueChanged.connect(lambda *_: on_change())
        self._refresh()

    def _refresh(self):
        en = self._on.isChecked()
        self._combo.setEnabled(en)
        self._size.setEnabled(en)

    def setEnabled(self, en):        # gate the whole control (e.g. header off)
        super().setEnabled(en)

    def value(self):
        if not self._on.isChecked():
            return "", 0
        return self._combo.currentFont().family(), self._size.value()

    def setValue(self, family, pt):
        self._on.setChecked(bool(family))
        if family:
            self._combo.setCurrentFont(QFont(family))
        if pt:
            try:
                self._size.setValue(int(round(float(pt))))
            except Exception:
                pass
        self._refresh()


def _import_summary(parsed):
    """A short human description of what an import picked up."""
    bits = []
    if parsed.get("header_on"):
        h = []
        if parsed.get("header_bg"):
            h.append("fill")
        if parsed.get("header_bold"):
            h.append("bold")
        if parsed.get("header_font"):
            h.append(parsed["header_font"])
        bits.append("header" + (" (" + ", ".join(h) + ")" if h else ""))
    fm = parsed.get("fill_mode")
    bits.append({"alternating": "banded rows", "single": "single fill",
                 "none": "no row fill"}.get(fm, "rows"))
    bits.append("cell borders" if parsed.get("border_on") else "no borders")
    if parsed.get("body_font"):
        bits.append("cell font " + parsed["body_font"])
    return ", ".join(bits)


def _spin(double=False):
    sb = QDoubleSpinBox() if double else QSpinBox()
    sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)   # show + / - buttons
    sb.setFixedWidth(104)
    sb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sb.setSuffix(" pt")
    return sb


def _heading(text):
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#1F4A37;font-size:11px;font-weight:bold;")
    return lbl


class ClipboardStyleDialog(QDialog):
    """Edit + live-preview the report copy/paste style; Save persists it."""

    def __init__(self, parent=None, tables=None):
        super().__init__(parent)
        self.setWindowTitle("Copy / paste style")
        self.setMinimumWidth(520)
        self._tables = list(tables or [])
        self._saved_style = CU.load_style()
        self._style = dict(self._saved_style)
        self._syncing = False
        self._build()
        self._syncing = True
        self._sync_from_style()
        self._syncing = False
        self._refresh_enabled()
        self._update_preview()
        self._apply_to_tables()      # immediate WYSIWYG on the real table

    # ---------------------------------------------------------------- build
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(14)

        intro = QLabel(
            "How report tables look when copied into your Google Docs report. "
            "The preview below updates live as you adjust the settings.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#54635A;font-size:11px;")
        root.addWidget(intro)

        # ── Match a pasted table ───────────────────────────────────────────
        imp = QFrame()
        imp.setObjectName("importCard")
        imp.setStyleSheet(
            "QFrame#importCard{background:#F3F7F4;border:1px solid #D9E4DC;"
            "border-radius:6px;}")
        il = QVBoxLayout(imp)
        il.setContentsMargins(12, 10, 12, 10)
        il.setSpacing(6)
        irow = QHBoxLayout()
        irow.setSpacing(10)
        icap = QLabel(
            "Copy a table from anywhere — Google Docs, Word, Excel, a web "
            "page — then match its look automatically:")
        icap.setWordWrap(True)
        icap.setStyleSheet(
            "color:#33413A;font-size:11px;background:transparent;border:none;")
        irow.addWidget(icap, 1)
        imp_btn = QPushButton("Paste table style")
        imp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        imp_btn.setStyleSheet(
            "QPushButton{background:#1F7A4D;color:white;border:none;"
            "border-radius:4px;padding:6px 14px;}"
            "QPushButton:hover{background:#176039;}")
        imp_btn.clicked.connect(self._import_from_clipboard)
        irow.addWidget(imp_btn, 0, Qt.AlignmentFlag.AlignTop)
        il.addLayout(irow)
        self._import_status = QLabel("")
        self._import_status.setWordWrap(True)
        self._import_status.setStyleSheet(
            "color:#54635A;font-size:11px;background:transparent;border:none;")
        self._import_status.setVisible(False)
        il.addWidget(self._import_status)
        root.addWidget(imp)

        # ── Borders ────────────────────────────────────────────────────────
        root.addWidget(_heading("Borders"))
        brow = QHBoxLayout()
        brow.setSpacing(12)
        self._border_on = QCheckBox("Cell borders")
        self._border_on.toggled.connect(self._on_change)
        brow.addWidget(self._border_on)
        self._border_colour = _ColourButton(self._style["border_colour"], self._on_change)
        brow.addWidget(self._border_colour)
        self._border_w = _spin(double=True)
        self._border_w.setRange(0.25, 5.0)
        self._border_w.setSingleStep(0.25)
        self._border_w.valueChanged.connect(self._on_change)
        brow.addWidget(self._border_w)
        brow.addStretch()
        root.addLayout(brow)

        # ── Rows ───────────────────────────────────────────────────────────
        root.addWidget(_heading("Rows"))
        frow = QHBoxLayout()
        frow.setSpacing(12)
        frow.addWidget(QLabel("Fill"))
        self._fill_mode = QComboBox()
        self._fill_mode.addItems(["Alternating", "Single colour", "None"])
        self._fill_mode.setFixedWidth(150)
        self._fill_mode.currentIndexChanged.connect(self._on_change)
        frow.addWidget(self._fill_mode)
        frow.addSpacing(14)
        frow.addWidget(QLabel("1st"))
        self._bg1 = _ColourButton(self._style["bg_1"], self._on_change)
        frow.addWidget(self._bg1)
        self._bg2_lbl = QLabel("2nd")
        frow.addWidget(self._bg2_lbl)
        self._bg2 = _ColourButton(self._style["bg_2"], self._on_change)
        frow.addWidget(self._bg2)
        frow.addStretch()
        root.addLayout(frow)

        # ── Padding ────────────────────────────────────────────────────────
        prow = QHBoxLayout()
        prow.setSpacing(12)
        prow.addWidget(QLabel("Padding"))
        prow.addWidget(QLabel("vertical"))
        self._pad_v = _spin()
        self._pad_v.setRange(0, 30)
        self._pad_v.valueChanged.connect(self._on_change)
        prow.addWidget(self._pad_v)
        prow.addSpacing(10)
        prow.addWidget(QLabel("horizontal"))
        self._pad_h = _spin()
        self._pad_h.setRange(0, 30)
        self._pad_h.valueChanged.connect(self._on_change)
        prow.addWidget(self._pad_h)
        prow.addStretch()
        root.addLayout(prow)

        # ── Cell text (body font + colour) ─────────────────────────────────
        trow = QHBoxLayout()
        trow.setSpacing(12)
        trow.addWidget(QLabel("Cell text"))
        self._body_font = _FontChoice(self._on_change)
        trow.addWidget(self._body_font)
        self._body_fg_on = QCheckBox("Colour")
        self._body_fg_on.toggled.connect(self._on_change)
        trow.addWidget(self._body_fg_on)
        self._body_fg = _ColourButton(self._style.get("body_fg") or "#1A2420",
                                      self._on_change)
        trow.addWidget(self._body_fg)
        trow.addStretch()
        root.addLayout(trow)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#D9DED7;")
        root.addWidget(line)

        # ── Header ─────────────────────────────────────────────────────────
        root.addWidget(_heading("Header row  (applied only when the header is copied)"))
        hrow = QHBoxLayout()
        hrow.setSpacing(12)
        self._header_on = QCheckBox("Style header")
        self._header_on.toggled.connect(self._on_change)
        hrow.addWidget(self._header_on)
        self._header_bold = QCheckBox("Bold")
        self._header_bold.toggled.connect(self._on_change)
        hrow.addWidget(self._header_bold)
        hrow.addSpacing(14)
        hrow.addWidget(QLabel("Fill"))
        self._header_bg = _ColourButton(self._style["header_bg"], self._on_change)
        hrow.addWidget(self._header_bg)
        hrow.addWidget(QLabel("Text"))
        self._header_fg = _ColourButton(self._style["header_fg"], self._on_change)
        hrow.addWidget(self._header_fg)
        hrow.addStretch()
        root.addLayout(hrow)

        hfrow = QHBoxLayout()
        hfrow.setSpacing(12)
        hfrow.addWidget(QLabel("Header font"))
        self._header_font = _FontChoice(self._on_change)
        hfrow.addWidget(self._header_font)
        hfrow.addStretch()
        root.addLayout(hfrow)

        # ── Preview ────────────────────────────────────────────────────────
        pv = QLabel("Preview  (top row = header)")
        pv.setStyleSheet("color:#54635A;font-size:11px;font-weight:bold;")
        root.addWidget(pv)
        self._preview = QTableWidget()
        self._preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._preview.horizontalHeader().setVisible(False)
        self._preview.verticalHeader().setVisible(False)
        self._preview.setFixedHeight(150)
        root.addWidget(self._preview)

        # ── Buttons ────────────────────────────────────────────────────────
        btns = QHBoxLayout()
        reset = QPushButton("Reset to default")
        reset.clicked.connect(self._reset)
        btns.addWidget(reset)
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(
            "QPushButton{background:#1F7A4D;color:white;border:none;"
            "border-radius:4px;padding:6px 18px;}"
            "QPushButton:hover{background:#176039;}")
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    # ------------------------------------------------------------- state I/O
    def _sync_from_style(self):
        s = self._style
        self._border_on.setChecked(bool(s["border_on"]))
        self._border_colour.setColour(s["border_colour"])
        self._border_w.setValue(float(s["border_width_pt"]))
        self._fill_mode.setCurrentIndex(
            {"alternating": 0, "single": 1, "none": 2}.get(s["fill_mode"], 0))
        self._bg1.setColour(s["bg_1"])
        self._bg2.setColour(s["bg_2"])
        self._pad_v.setValue(int(s["pad_v_pt"]))
        self._pad_h.setValue(int(s["pad_h_pt"]))
        self._header_on.setChecked(bool(s["header_on"]))
        self._header_bold.setChecked(bool(s["header_bold"]))
        self._header_bg.setColour(s["header_bg"])
        self._header_fg.setColour(s["header_fg"])
        self._header_font.setValue(s.get("header_font", ""), s.get("header_font_pt", 0))
        self._body_font.setValue(s.get("body_font", ""), s.get("body_font_pt", 0))
        body_fg = s.get("body_fg") or ""
        self._body_fg_on.setChecked(bool(body_fg))
        if body_fg:
            self._body_fg.setColour(body_fg)

    def _collect(self):
        idx = self._fill_mode.currentIndex()
        hf_fam, hf_pt = self._header_font.value()
        bf_fam, bf_pt = self._body_font.value()
        self._style.update({
            "border_on": self._border_on.isChecked(),
            "border_colour": self._border_colour.colour(),
            "border_width_pt": round(self._border_w.value(), 3),
            "fill_mode": ["alternating", "single", "none"][idx],
            "bg_1": self._bg1.colour(),
            "bg_2": self._bg2.colour(),
            "pad_v_pt": self._pad_v.value(),
            "pad_h_pt": self._pad_h.value(),
            "header_on": self._header_on.isChecked(),
            "header_bold": self._header_bold.isChecked(),
            "header_bg": self._header_bg.colour(),
            "header_fg": self._header_fg.colour(),
            "header_font": hf_fam,
            "header_font_pt": hf_pt,
            "body_font": bf_fam,
            "body_font_pt": bf_pt,
            "body_fg": self._body_fg.colour() if self._body_fg_on.isChecked() else "",
        })

    def _refresh_enabled(self):
        s = self._style
        self._border_colour.setEnabled(s["border_on"])
        self._border_w.setEnabled(s["border_on"])
        alt = s["fill_mode"] == "alternating"
        self._bg1.setEnabled(s["fill_mode"] in ("alternating", "single"))
        self._bg2.setEnabled(alt)
        self._bg2_lbl.setEnabled(alt)
        self._header_bg.setEnabled(s["header_on"])
        self._header_fg.setEnabled(s["header_on"])
        self._header_bold.setEnabled(s["header_on"])
        self._header_font.setEnabled(s["header_on"])
        self._body_fg.setEnabled(self._body_fg_on.isChecked())

    def _on_change(self, *_):
        if self._syncing:
            return
        self._collect()
        self._refresh_enabled()
        self._update_preview()
        self._apply_to_tables()

    # -------------------------------------------------------- live + preview
    def _apply_to_tables(self):
        for t in self._tables:
            try:
                CU.apply_table_style(t, self._style)
            except Exception:
                pass

    def _update_preview(self):
        s = self._style
        tbl = self._preview
        tbl.clear()
        tbl.setColumnCount(2)
        tbl.setRowCount(len(_SAMPLE))
        border = s["border_colour"] if s["border_on"] else "#E3E7E1"
        tbl.setShowGrid(bool(s["border_on"]))
        tbl.setStyleSheet(
            "QTableWidget{background:#ffffff;gridline-color:%s;border:1px solid %s;}"
            % (border, border))
        body_i = 0
        for ri, row in enumerate(_SAMPLE):
            is_header = s["header_on"] and ri == 0
            if is_header:
                bg, fg = s["header_bg"], s["header_fg"]
                fam, pt = s.get("header_font", ""), s.get("header_font_pt", 0)
            else:
                fg = s.get("body_fg") or "#1A2420"
                fam, pt = s.get("body_font", ""), s.get("body_font_pt", 0)
                mode = s["fill_mode"]
                if mode == "none":
                    bg = "#ffffff"
                elif mode == "single":
                    bg = s["bg_1"]
                else:
                    bg = s["bg_1"] if body_i % 2 == 0 else s["bg_2"]
                body_i += 1
            fnt = QFont()
            if fam:
                fnt.setFamily(fam)
            if pt:
                try:
                    fnt.setPointSizeF(float(pt))
                except Exception:
                    pass
            if is_header and s["header_bold"]:
                fnt.setBold(True)
            for ci, val in enumerate(row):
                it = QTableWidgetItem(val)
                it.setBackground(QColor(bg))
                it.setForeground(QColor(fg))
                it.setFont(fnt)
                it.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                tbl.setItem(ri, ci, it)
            tbl.setRowHeight(ri, 28)
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)

    # --------------------------------------------------------------- import
    def _import_from_clipboard(self):
        """Read the table currently on the clipboard and adopt its styling."""
        def _fail(msg):
            self._import_status.setText(msg)
            self._import_status.setStyleSheet(
                "color:#B00020;font-size:11px;background:transparent;border:none;")
            self._import_status.setVisible(True)

        html_text = ""
        try:
            html_text = CU.get_clipboard_html()
        except Exception:
            html_text = ""
        if not html_text:
            _fail("Nothing to read — copy a table (from Docs, Word, Excel, a "
                  "web page) first, then click again.")
            return
        try:
            parsed = CU.parse_table_style(html_text)
        except Exception:
            parsed = None
        if not parsed:
            _fail("Couldn't find a styled table on the clipboard. Select the "
                  "table's cells and copy, then try again.")
            return

        self._style.update(parsed)
        self._syncing = True
        self._sync_from_style()
        self._syncing = False
        self._on_change()          # refresh enabled state, preview + live table
        self._import_status.setText("Matched  ·  " + _import_summary(parsed))
        self._import_status.setStyleSheet(
            "color:#1F7A4D;font-size:11px;background:transparent;border:none;")
        self._import_status.setVisible(True)

    # --------------------------------------------------------------- actions
    def _reset(self):
        self._style = dict(CU.DEFAULT_STYLE)
        self._syncing = True
        self._sync_from_style()
        self._syncing = False
        self._on_change()

    def _save(self):
        self._collect()
        CU.save_style(self._style)
        self.accept()

    def reject(self):
        # discard unsaved tweaks -> restore the previously-saved look
        for t in self._tables:
            try:
                CU.apply_table_style(t, self._saved_style)
            except Exception:
                pass
        super().reject()
