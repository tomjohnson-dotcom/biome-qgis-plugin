# -*- coding: utf-8 -*-
"""
tree_csv_import.py
==================

Import trees from a CSV / delimited text file into the *Trees (Map Only)* layer
of the Building & Tree Mapper.

The whole point is that you can hand it almost any surveyor's export and it
figures the coordinates out for itself:

  * delimiter (comma / semicolon / tab / pipe) and encoding are sniffed;
  * the coordinate columns are auto-detected, first from the header names, then
    from the *values* themselves — big integers ⇒ BNG eastings/northings, small
    signed decimals ⇒ lat/lon, ``SP 123 456``-style strings ⇒ grid reference;
  * a one-glance confirm dialog shows what was detected with a live preview, so
    the only time you touch a dropdown is when it guessed wrong;
  * before committing, the points are sanity-checked against Great Britain (and
    your redline, if drawn) — if they'd land in the North Sea it says so instead
    of silently dropping dots in the ocean.

Trees imported here are illustration only — exactly like clicked trees, they are
NOT part of any BNG calculation, balance check or summary.

Coordinates must use a dot decimal separator (``-1.857``); decimal-comma values
are not interpreted as decimals.
"""

import csv
import io

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QCursor, QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QFrame, QPushButton, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog)

from . import theme
from .gridref_utils import gridref_to_osgb_en, GridRefError


# ── Great Britain bounds in EPSG:27700, used for the sanity check ────────────
GB_E = (0.0, 700000.0)
GB_N = (0.0, 1300000.0)


def in_gb(e, n):
    """True if (easting, northing) plausibly sits within Great Britain (BNG)."""
    return GB_E[0] <= e <= GB_E[1] and GB_N[0] <= n <= GB_N[1]


# ── Header-name aliases (normalised: lower-case, alphanumerics only) ─────────
_ALIASES = {
    "easting":  {"easting", "eastings", "east", "eastingm", "xcoord", "x",
                 "bnge", "oseasting", "eastingbng", "gridx", "xosgb", "xeast"},
    "northing": {"northing", "northings", "north", "northingm", "ycoord", "y",
                 "bngn", "osnorthing", "northingbng", "gridy", "yosgb", "ynorth"},
    "lat":      {"lat", "latitude", "latdd", "latitudedd", "ylat"},
    "lon":      {"lon", "long", "lng", "longitude", "londd", "longitudedd", "xlon"},
    "gridref":  {"gridref", "gridreference", "ngr", "osgridref", "osgb",
                 "grid", "nationalgridref", "nationalgridreference", "gridrefosgb"},
    "label":    {"ref", "reference", "id", "fid", "treeid", "treeno", "treenum",
                 "treenumber", "treeref", "number", "no", "name", "tag",
                 "label", "tree", "treetag"},
}


def _norm(s):
    out = []
    for ch in (s or "").lower():
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


# ── File reading ─────────────────────────────────────────────────────────────

def read_table(path):
    """Read a delimited text file → ``(headers, rows)``.

    Sniffs the delimiter (``, ; \\t |``) and tolerates UTF-8 (with or without
    BOM), Windows-1252 or Latin-1.  ``headers`` is a list of column titles; if
    the first line is all-numeric it is treated as data and synthetic
    ``Column N`` headers are returned.  ``rows`` is a list of equal-length
    lists of strings.
    """
    raw = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with io.open(path, "r", encoding=enc, newline="") as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, OSError):
            continue
    if raw is None:
        raise IOError("Could not read the file.")

    sample = "\n".join(raw.splitlines()[:20])
    delim = ","
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except Exception:
        first = (raw.splitlines() or [""])[0]
        delim = max(",;\t|", key=lambda d: first.count(d))

    reader = csv.reader(io.StringIO(raw), delimiter=delim)
    all_rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not all_rows:
        return [], []

    width = max(len(r) for r in all_rows)
    all_rows = [list(r) + [""] * (width - len(r)) for r in all_rows]

    header = all_rows[0]
    if header and all(_is_number(c) for c in header if (c or "").strip()):
        headers = ["Column %d" % (i + 1) for i in range(width)]
        rows = all_rows
    else:
        headers = [((h or "").strip() or "Column %d" % (i + 1))
                   for i, h in enumerate(header)]
        rows = all_rows[1:]
    return headers, rows


def _is_number(s):
    try:
        float((s or "").replace(",", "").strip())
        return True
    except ValueError:
        return False


# ── Value parsing ─────────────────────────────────────────────────────────────

def _num(s):
    """Parse a coordinate value. Strips spaces and thousands commas; requires a
    dot decimal separator. Raises ValueError on blank/non-numeric."""
    s = (s or "").strip().replace(" ", "").replace(",", "")
    if not s:
        raise ValueError("blank")
    return float(s)


def _cell(row, idx):
    return row[idx] if (idx is not None and 0 <= idx < len(row)) else ""


def _sample(rows, idx, n=40):
    out = []
    for r in rows:
        v = _cell(r, idx).strip()
        if v:
            out.append(v)
        if len(out) >= n:
            break
    return out


def _floats(values):
    """Parse a list of strings to floats, or None if any is non-numeric."""
    fs = []
    for v in values:
        try:
            fs.append(_num(v))
        except ValueError:
            return None
    return fs


def _roles_for(fs):
    """Which coordinate roles a numeric column's value-range is consistent with.
    Lat and BNG ranges never overlap, so a column is rarely ambiguous between
    lat/lon and easting/northing."""
    if not fs:
        return set()
    lo, hi = min(fs), max(fs)
    roles = set()
    if 49.0 <= lo and hi <= 61.5:
        roles.add("lat")
    if -11.0 <= lo and hi <= 3.0 and lo < 2.0:
        roles.add("lon")
    if 0 <= lo and hi <= GB_E[1] and hi > 1000:
        roles.add("easting")
    if 0 <= lo and hi <= GB_N[1] and hi > 1000:
        roles.add("northing")
    return roles


def _is_gridref(v):
    try:
        gridref_to_osgb_en(v)
        return True
    except Exception:
        return False


def _column_is_gridref(rows, idx):
    sample = _sample(rows, idx)
    if not sample:
        return False
    good = sum(1 for v in sample if _is_gridref(v))
    return good >= max(1, int(0.7 * len(sample)))


# ── Detection ────────────────────────────────────────────────────────────────

def detect(headers, rows):
    """Return a mapping dict describing how to read coordinates from `rows`.

    Keys: ``mode`` ('en' | 'latlon' | 'gridref' | None), the relevant
    ``*_col`` indices, ``label_col`` (or None), ``confidence`` and ``headers``.
    """
    n = len(headers)
    norm = [_norm(h) for h in headers]
    col_floats = [_floats(_sample(rows, i)) for i in range(n)]
    col_roles = [_roles_for(f) for f in col_floats]

    m = dict(mode=None, easting_col=None, northing_col=None,
             lat_col=None, lon_col=None, gridref_col=None,
             label_col=None, confidence="none", headers=list(headers))

    for i, h in enumerate(norm):
        if h in _ALIASES["label"]:
            m["label_col"] = i
            break

    def header_pick(role):
        # Prefer a header alias whose values agree with the role (or are blank);
        # otherwise trust the header label even if values look odd.
        for i, h in enumerate(norm):
            if h in _ALIASES[role] and (not col_roles[i] or role in col_roles[i]):
                return i
        for i, h in enumerate(norm):
            if h in _ALIASES[role]:
                return i
        return None

    e, north = header_pick("easting"), header_pick("northing")
    la, lo = header_pick("lat"), header_pick("lon")
    gr = header_pick("gridref")

    if e is not None and north is not None and e != north:
        m.update(mode="en", easting_col=e, northing_col=north, confidence="high")
    elif la is not None and lo is not None and la != lo:
        m.update(mode="latlon", lat_col=la, lon_col=lo, confidence="high")
    elif gr is not None:
        conf = "high" if _column_is_gridref(rows, gr) else "medium"
        m.update(mode="gridref", gridref_col=gr, confidence=conf)
    else:
        _value_fallback(m, rows, col_floats, col_roles)
    return m


def _value_fallback(m, rows, col_floats, col_roles):
    """Detect coordinates with no usable headers, purely from the values."""
    lat_c = next((i for i, r in enumerate(col_roles) if r == {"lat"}), None)
    lon_c = next((i for i, r in enumerate(col_roles) if r == {"lon"}), None)
    if lat_c is not None and lon_c is not None:
        m.update(mode="latlon", lat_col=lat_c, lon_col=lon_c, confidence="medium")
        return

    for i in range(len(col_roles)):
        if _column_is_gridref(rows, i):
            m.update(mode="gridref", gridref_col=i, confidence="medium")
            return

    bng = [i for i, r in enumerate(col_roles)
           if ("easting" in r or "northing" in r)]
    if len(bng) >= 2:
        a, b = bng[0], bng[1]
        a_big = max(col_floats[a]) > 700000
        b_big = max(col_floats[b]) > 700000
        if a_big and not b_big:
            m.update(mode="en", easting_col=b, northing_col=a, confidence="medium")
        elif b_big and not a_big:
            m.update(mode="en", easting_col=a, northing_col=b, confidence="medium")
        else:
            # Both plausible as either — assume first=E, second=N but flag it.
            m.update(mode="en", easting_col=a, northing_col=b, confidence="low")


# ── Row → point ──────────────────────────────────────────────────────────────

def _wgs84_to_bng():
    from qgis.core import (
        QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject)
    src = QgsCoordinateReferenceSystem("EPSG:4326")
    dst = QgsCoordinateReferenceSystem("EPSG:27700")
    return QgsCoordinateTransform(src, dst, QgsProject.instance())


def build_points(rows, mapping):
    """Turn each row into a result dict using `mapping`.

    Each dict: ``row`` (index), ``label`` (str|None), ``e``/``n`` (floats|None),
    ``pt`` (QgsPointXY in EPSG:27700 | None), ``ok`` (bool), ``reason`` (str).
    """
    from qgis.core import QgsPointXY
    mode = mapping.get("mode")
    lab = mapping.get("label_col")
    xform = _wgs84_to_bng() if mode == "latlon" else None
    out = []
    for idx, r in enumerate(rows):
        label = None
        if lab is not None and lab < len(r):
            label = (r[lab] or "").strip() or None
        e = n = None
        reason = ""
        try:
            if mode == "en":
                e = _num(_cell(r, mapping["easting_col"]))
                n = _num(_cell(r, mapping["northing_col"]))
            elif mode == "latlon":
                lat = _num(_cell(r, mapping["lat_col"]))
                lon = _num(_cell(r, mapping["lon_col"]))
                p = xform.transform(QgsPointXY(lon, lat))
                e, n = p.x(), p.y()
            elif mode == "gridref":
                e, n = gridref_to_osgb_en(_cell(r, mapping["gridref_col"]))
            else:
                reason = "set the coordinate columns"
        except GridRefError:
            reason = "invalid grid reference"
        except (ValueError, TypeError):
            reason = "missing or non-numeric coordinates"
        except Exception:
            reason = "couldn't read coordinates"
        ok = (e is not None and n is not None)
        out.append(dict(row=idx, label=label, e=e, n=n,
                        pt=(QgsPointXY(e, n) if ok else None),
                        ok=ok, reason=("" if ok else (reason or "skipped"))))
    return out


# ── Dialog ───────────────────────────────────────────────────────────────────

_MODES = [("Eastings / Northings (BNG)", "en"),
          ("Latitude / Longitude", "latlon"),
          ("Grid reference", "gridref")]

_PREVIEW_ROWS = 12


class TreeCsvImportDialog(QDialog):
    """Confirm-and-import dialog. After ``exec()`` returns Accepted, read
    ``imported_count`` for the number of trees placed."""

    def __init__(self, path, redline_geom=None, parent=None):
        super().__init__(parent)
        self._path = path
        self._redline = redline_geom
        self.imported_count = 0
        self._results = []

        self._headers, self._rows = read_table(path)
        self._mapping = detect(self._headers, self._rows)

        self.setWindowTitle("Import trees from CSV")
        self.setStyleSheet(
            "QDialog { background:%s; }"
            " QLabel, QPushButton, QComboBox { font-family:%s; }"
            " QComboBox { background:%s; border:1px solid %s; border-radius:6px;"
            " padding:4px 8px; min-height:18px; }"
            " QComboBox:focus { border-color:%s; }"
            % (theme.SURFACE, theme.FONT, theme.CARD, theme.BORDER_STRONG,
               theme.ACCENT))
        self.setMinimumSize(560, 580)
        self.resize(660, 720)

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
        body.addWidget(self._build_file_card())
        body.addWidget(self._build_mapping_card())
        body.addWidget(self._build_preview_card())
        body.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._build_footer())

        self._populate_col_combos()
        self._sync_widgets_from_mapping()
        self._refresh()

    # ---- chrome ----------------------------------------------------------
    def _build_header(self):
        w = QWidget()
        w.setStyleSheet("background:%s;" % theme.ACCENT_DARK)
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(1)
        t = QLabel("Import trees from CSV")
        t.setStyleSheet("color:white; font-family:%s; font-size:15px; font-weight:600;"
                        " background:transparent;" % theme.FONT_SEMIBOLD)
        s = QLabel("Coordinates are detected automatically — check the preview, then "
                   "import. Map illustration only; not part of any BNG calculation.")
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

    def _title(self, text):
        l = QLabel(text)
        l.setStyleSheet("font-family:%s; font-size:13px; font-weight:600; color:%s;"
                        " background:transparent;" % (theme.FONT_SEMIBOLD, theme.INK))
        return l

    def _card(self):
        w = QWidget()
        w.setObjectName("tcCard")
        w.setStyleSheet("#tcCard { background:%s; border:1px solid %s; border-radius:8px; }"
                        % (theme.CARD, theme.BORDER))
        return w

    # ---- file card -------------------------------------------------------
    def _build_file_card(self):
        import os
        card = self._card()
        h = QHBoxLayout(card)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(1)
        self._file_lbl = QLabel(os.path.basename(self._path))
        self._file_lbl.setStyleSheet(
            "font-family:%s; font-size:12px; font-weight:600; color:%s;"
            " background:transparent;" % (theme.FONT_SEMIBOLD, theme.INK))
        self._file_sub = self._caption("%d data rows" % len(self._rows))
        col.addWidget(self._file_lbl)
        col.addWidget(self._file_sub)
        h.addLayout(col, 1)
        change = QPushButton("Change file…")
        change.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        change.setStyleSheet(theme.btn_secondary())
        change.clicked.connect(self._change_file)
        h.addWidget(change)
        return card

    # ---- mapping card ----------------------------------------------------
    def _build_mapping_card(self):
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 11, 12, 12)
        v.setSpacing(8)
        v.addWidget(self._title("Coordinate columns"))

        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(7)

        self._mode_combo = QComboBox()
        for label, key in _MODES:
            self._mode_combo.addItem(label, key)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        g.addWidget(self._caption("Coordinate type"), 0, 0)
        g.addWidget(self._mode_combo, 0, 1)

        # Eastings / Northings row (with a Swap button)
        self._e_combo = QComboBox()
        self._n_combo = QComboBox()
        self._e_combo.currentIndexChanged.connect(self._on_widget_changed)
        self._n_combo.currentIndexChanged.connect(self._on_widget_changed)
        swap = QPushButton("Swap")
        swap.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        swap.setStyleSheet(theme.btn_secondary())
        swap.setFixedWidth(64)
        swap.setToolTip("Swap the Easting and Northing columns")
        swap.clicked.connect(self._swap_en)
        self._en_row = QWidget()
        en_h = QHBoxLayout(self._en_row)
        en_h.setContentsMargins(0, 0, 0, 0)
        en_h.setSpacing(6)
        en_h.addWidget(self._labelled("E", self._e_combo), 1)
        en_h.addWidget(self._labelled("N", self._n_combo), 1)
        en_h.addWidget(swap)
        g.addWidget(self._en_row, 1, 0, 1, 2)

        # Lat / Lon row
        self._lat_combo = QComboBox()
        self._lon_combo = QComboBox()
        self._lat_combo.currentIndexChanged.connect(self._on_widget_changed)
        self._lon_combo.currentIndexChanged.connect(self._on_widget_changed)
        self._latlon_row = QWidget()
        ll_h = QHBoxLayout(self._latlon_row)
        ll_h.setContentsMargins(0, 0, 0, 0)
        ll_h.setSpacing(6)
        ll_h.addWidget(self._labelled("Lat", self._lat_combo), 1)
        ll_h.addWidget(self._labelled("Lon", self._lon_combo), 1)
        g.addWidget(self._latlon_row, 2, 0, 1, 2)

        # Grid reference row
        self._gr_combo = QComboBox()
        self._gr_combo.currentIndexChanged.connect(self._on_widget_changed)
        self._gr_row = QWidget()
        gr_h = QHBoxLayout(self._gr_row)
        gr_h.setContentsMargins(0, 0, 0, 0)
        gr_h.setSpacing(6)
        gr_h.addWidget(self._labelled("Grid ref", self._gr_combo), 1)
        g.addWidget(self._gr_row, 3, 0, 1, 2)

        # Label / ref column (optional)
        self._label_combo = QComboBox()
        self._label_combo.currentIndexChanged.connect(self._on_widget_changed)
        g.addWidget(self._caption("Tree label (optional)"), 4, 0)
        g.addWidget(self._label_combo, 4, 1)

        g.setColumnStretch(1, 1)
        v.addLayout(g)

        self._detect_lbl = self._caption("")
        self._detect_lbl.setWordWrap(True)
        v.addWidget(self._detect_lbl)
        return card

    def _labelled(self, text, combo):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        l = QLabel(text)
        l.setStyleSheet("color:%s; font-size:11px; font-family:%s; background:transparent;"
                        % (theme.MUTED, theme.FONT_MEDIUM))
        l.setMinimumWidth(20)
        h.addWidget(l)
        h.addWidget(combo, 1)
        return w

    # ---- preview card ----------------------------------------------------
    def _build_preview_card(self):
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(7)
        v.addWidget(self._title("Preview"))

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Tree", "Easting", "Northing", "Status"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setStyleSheet(
            "QTableWidget { background:%s; border:1px solid %s; border-radius:6px;"
            " gridline-color:%s; font-size:12px; }"
            " QHeaderView::section { background:%s; color:%s; border:none;"
            " border-bottom:1px solid %s; padding:5px 8px; font-family:%s;"
            " font-weight:600; }"
            " QTableWidget::item { padding:4px 8px; }"
            % (theme.CARD, theme.BORDER, theme.BORDER, theme.SUNKEN, theme.MUTED,
               theme.BORDER_STRONG, theme.FONT_SEMIBOLD))
        self._table.setShowGrid(True)
        self._table.horizontalHeader().setHighlightSections(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setMinimumHeight(200)
        v.addWidget(self._table)

        self._sanity = QLabel("")
        self._sanity.setWordWrap(True)
        self._sanity.setStyleSheet(
            "background:%s; color:%s; border:1px solid %s; border-radius:7px;"
            " padding:7px 9px; font-size:11px;"
            % (theme.SUNKEN, theme.MUTED, theme.BORDER))
        v.addWidget(self._sanity)
        return card

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

        cancel = QPushButton("Cancel")
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.setStyleSheet(theme.btn_secondary())
        cancel.clicked.connect(self.reject)

        self._import_btn = QPushButton("Import")
        self._import_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._import_btn.setStyleSheet(theme.btn_primary())
        self._import_btn.clicked.connect(self._do_import)

        h.addWidget(cancel)
        h.addWidget(self._import_btn)
        return bar

    # ---- combo population / sync ----------------------------------------
    def _populate_col_combos(self):
        """Fill the column dropdowns from the current headers."""
        for combo in (self._e_combo, self._n_combo, self._lat_combo,
                      self._lon_combo, self._gr_combo, self._label_combo):
            combo.blockSignals(True)
            combo.clear()
        for combo in (self._e_combo, self._n_combo, self._lat_combo,
                      self._lon_combo, self._gr_combo):
            for i, h in enumerate(self._headers):
                combo.addItem(h, i)
        self._label_combo.addItem("Auto-number (T1, T2 …)", -1)
        for i, h in enumerate(self._headers):
            self._label_combo.addItem(h, i)
        for combo in (self._e_combo, self._n_combo, self._lat_combo,
                      self._lon_combo, self._gr_combo, self._label_combo):
            combo.blockSignals(False)

    def _set_combo(self, combo, value, default=0):
        idx = combo.findData(value if value is not None else default)
        if idx < 0:
            idx = combo.findData(default)
        combo.setCurrentIndex(max(0, idx))

    def _sync_widgets_from_mapping(self):
        """Push the detected mapping onto the widgets (signals blocked), then
        read it straight back so widgets and mapping always agree."""
        m = self._mapping
        ncols = len(self._headers)
        second = 1 if ncols > 1 else 0
        widgets = (self._mode_combo, self._e_combo, self._n_combo,
                   self._lat_combo, self._lon_combo, self._gr_combo,
                   self._label_combo)
        for w in widgets:
            w.blockSignals(True)

        mode = m.get("mode") or "en"
        mi = self._mode_combo.findData(mode)
        self._mode_combo.setCurrentIndex(max(0, mi))
        self._set_combo(self._e_combo, m.get("easting_col"), 0)
        self._set_combo(self._n_combo, m.get("northing_col"), second)
        self._set_combo(self._lat_combo, m.get("lat_col"), 0)
        self._set_combo(self._lon_combo, m.get("lon_col"), second)
        self._set_combo(self._gr_combo, m.get("gridref_col"), 0)
        self._set_combo(self._label_combo,
                        m.get("label_col") if m.get("label_col") is not None else -1, -1)

        for w in widgets:
            w.blockSignals(False)

        self._update_mode_rows()
        self._read_widgets_into_mapping()

    def _read_widgets_into_mapping(self):
        m = self._mapping
        m["mode"] = self._mode_combo.currentData()
        m["easting_col"] = self._e_combo.currentData()
        m["northing_col"] = self._n_combo.currentData()
        m["lat_col"] = self._lat_combo.currentData()
        m["lon_col"] = self._lon_combo.currentData()
        m["gridref_col"] = self._gr_combo.currentData()
        lab = self._label_combo.currentData()
        m["label_col"] = None if (lab is None or lab == -1) else lab

    def _update_mode_rows(self):
        mode = self._mode_combo.currentData()
        self._en_row.setVisible(mode == "en")
        self._latlon_row.setVisible(mode == "latlon")
        self._gr_row.setVisible(mode == "gridref")

    # ---- events ----------------------------------------------------------
    def _on_mode_changed(self, *_):
        # Manual mode change clears the auto-detected confidence note.
        self._mapping["confidence"] = "manual"
        self._update_mode_rows()
        self._read_widgets_into_mapping()
        self._refresh()

    def _on_widget_changed(self, *_):
        self._read_widgets_into_mapping()
        self._refresh()

    def _swap_en(self):
        e = self._e_combo.currentData()
        n = self._n_combo.currentData()
        self._e_combo.blockSignals(True)
        self._n_combo.blockSignals(True)
        self._set_combo(self._e_combo, n)
        self._set_combo(self._n_combo, e)
        self._e_combo.blockSignals(False)
        self._n_combo.blockSignals(False)
        self._read_widgets_into_mapping()
        self._refresh()

    def _change_file(self):
        import os
        path, _ = QFileDialog.getOpenFileName(
            self, "Import trees from CSV", os.path.dirname(self._path),
            "CSV / text files (*.csv *.txt *.tsv);;All files (*)")
        if not path:
            return
        try:
            self._headers, self._rows = read_table(path)
        except Exception:
            return
        self._path = path
        self._mapping = detect(self._headers, self._rows)
        self._file_lbl.setText(os.path.basename(path))
        self._file_sub.setText("%d data rows" % len(self._rows))
        self._populate_col_combos()
        self._sync_widgets_from_mapping()
        self._refresh()

    # ---- core refresh ----------------------------------------------------
    def _refresh(self):
        self._results = build_points(self._rows, self._mapping)
        valid = [r for r in self._results if r["ok"]]
        skipped = len(self._results) - len(valid)
        outside = [r for r in valid if not in_gb(r["e"], r["n"])]

        self._fill_preview()
        self._set_detect_note()
        self._set_sanity(valid, skipped, outside)

        n = len(valid)
        self._import_btn.setText("Import %d tree%s" % (n, "" if n == 1 else "s"))
        self._import_btn.setEnabled(n > 0)
        bits = []
        if valid:
            bits.append("%d ready" % len(valid))
        if skipped:
            bits.append("%d skipped" % skipped)
        self._status.setText(" · ".join(bits) if bits else "Nothing to import yet.")

    def _set_detect_note(self):
        conf = self._mapping.get("confidence")
        notes = {
            "high": "Columns detected automatically from the file.",
            "medium": "Columns detected from the data — worth a glance.",
            "low": "Best guess — please check the Easting and Northing are the "
                   "right way round.",
            "none": "Couldn't detect the coordinate columns — choose them above.",
            "manual": "",
        }
        self._detect_lbl.setText(notes.get(conf, ""))
        self._detect_lbl.setVisible(bool(notes.get(conf, "")))

    def _fill_preview(self):
        rows = self._results[:_PREVIEW_ROWS]
        self._table.setRowCount(len(rows))
        seq = self._auto_sequence_start()
        auto = seq
        for vis, r in enumerate(rows):
            if r["label"]:
                tree = r["label"]
            elif r["ok"]:
                tree = "T%d" % auto
                auto += 1
            else:
                tree = "—"
            e = "%.0f" % r["e"] if r["e"] is not None else "—"
            n = "%.0f" % r["n"] if r["n"] is not None else "—"
            status = "OK" if r["ok"] else r["reason"]
            cells = [tree, e, n, status]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 3:
                    if r["ok"]:
                        item.setForeground(QColor(theme.GOOD))
                    else:
                        item.setForeground(QColor(theme.BAD))
                else:
                    item.setForeground(QColor(theme.INK))
                self._table.setItem(vis, c, item)
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

    def _auto_sequence_start(self):
        """The next T-number the importer would assign, for a faithful preview."""
        try:
            import re
            from qgis.core import QgsProject
            from .building_tree_mapper import TREE_LAYER_NAME
            lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
            nxt = 1
            if lyrs:
                for f in lyrs[0].getFeatures():
                    tid = f["tree_id"]
                    if tid and re.fullmatch(r"T\d+", str(tid)):
                        nxt = max(nxt, int(str(tid)[1:]) + 1)
            return nxt
        except Exception:
            return 1

    def _set_sanity(self, valid, skipped, outside):
        def paint(kind, text):
            colours = {"good": (theme.GOOD, theme.GOOD_BG),
                       "warn": (theme.WARN, theme.WARN_BG),
                       "bad": (theme.BAD, theme.BAD_BG)}
            fg, bg = colours[kind]
            self._sanity.setStyleSheet(
                "background:%s; color:%s; border:1px solid %s; border-radius:7px;"
                " padding:7px 9px; font-size:11px;" % (bg, fg, fg))
            self._sanity.setText(text)

        if not valid:
            paint("bad", "No rows have usable coordinates yet. Pick the coordinate "
                         "type and columns above.")
            return

        if outside:
            extra = ""
            if self._mapping.get("mode") == "en" and self._would_swap_help(outside):
                extra = " They look swapped — try the Swap button."
            paint("bad", "%d of %d points fall outside Great Britain — the columns "
                         "may be the wrong way round, or the coordinate type may be "
                         "wrong.%s" % (len(outside), len(valid), extra))
            return

        near = self._count_near_redline(valid)
        if near is not None and near == 0:
            paint("warn", "All %d points are more than 5 km from your redline — "
                          "check this is the right file for this site." % len(valid))
            return

        msg = "All %d points fall within Great Britain" % len(valid)
        if near is not None:
            msg += " · %d near your redline" % near
        if skipped:
            msg += " · %d row%s skipped (no coordinates)" % (skipped, "" if skipped == 1 else "s")
        msg += "."
        paint("good", msg)

    def _would_swap_help(self, outside):
        """True if swapping E/N would bring most out-of-GB points back in."""
        better = 0
        for r in outside:
            if in_gb(r["n"], r["e"]):
                better += 1
        return better >= max(1, int(0.6 * len(outside)))

    def _count_near_redline(self, valid):
        """How many valid points sit within 5 km of the redline bbox, or None if
        there is no redline to compare against."""
        geom = self._redline
        if geom is None:
            return None
        try:
            if geom.isEmpty():
                return None
            bbox = geom.boundingBox()
            bbox.grow(5000)
            return sum(1 for r in valid if bbox.contains(r["pt"]))
        except Exception:
            return None

    # ---- import ----------------------------------------------------------
    def _do_import(self):
        items = [(r["pt"], r["label"]) for r in self._results if r["ok"]]
        if not items:
            return
        try:
            from .building_tree_mapper import add_trees_bulk
            self.imported_count = add_trees_bulk(items)
        except Exception:
            import traceback
            traceback.print_exc()
            self.imported_count = 0
        self.accept()
