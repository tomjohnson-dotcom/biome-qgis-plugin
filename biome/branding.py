# branding.py
# Theme + logo management for the final map sidebar.
#
# A "theme" is a small named bundle of three colours plus a logo file:
#     header     – fill of the title bar + north-arrow body
#     titleText  – colour of the title text sitting on the header bar
#     bodyText   – colour of all text on the chalk panel (date, created,
#                  reviewed, legend label, footnotes)
#     logo       – absolute path to a PNG/JPG/SVG logo, or "" for none
#
# Themes are persisted in QSettings so they survive QGIS restarts and can be
# created, edited, switched, or deleted at any time from the Branding dialog.
# One theme is always marked "active"; that is the one the map exporter uses.

import os

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtGui import QColor, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QColorDialog, QFileDialog, QInputDialog,
    QDialogButtonBox, QGroupBox, QWidget, QMessageBox,
)

# ── Settings keys ─────────────────────────────────────────────────────────────
_K_NAMES  = "BiomeMapper/themeNames"     # list[str]
_K_ACTIVE = "BiomeMapper/activeTheme"    # str
_K_THEME  = "BiomeMapper/theme/{name}/{field}"

_FIELDS = ("header", "titleText", "bodyText", "logo", "trimLogo", "footnote",
           "logoScale")

# Default starting point — ROAVR gold with the bundled ROAVR logo.
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOGO = os.path.join(_PLUGIN_DIR, "roavr_logo.png")

DEFAULT_THEME = {
    "header":    "#C99B2E",   # ROAVR gold (richer/saturated), matched to logo
    "titleText": "#251C06",   # dark espresso — legible on gold
    "bodyText":  "#2A2417",   # warm near-black body text
    "logo":      _DEFAULT_LOGO if os.path.exists(_DEFAULT_LOGO) else "",
    "trimLogo":  "true",      # ROAVR PNG has wide transparent margins to trim
    # States the MEASUREMENT basis, not the display CRS: distances/areas are
    # computed in OSGB36 / EPSG:27700 (or ellipsoidally), and the old
    # "EPSG:3857" label — really just the basemap tile CRS — misread as the
    # measurement CRS.
    "footnote":  "OSGB36 / EPSG:27700 · Generated with Biome",
    # Logo band as a fraction of the sidebar width (0.25–1.0). 0.55 reads as a
    # footer signature; the Scale/Date block and the legend take the freed
    # room. Adjustable per theme from the "Logo size" slider.
    "logoScale": "0.55",
}


# ── Store ─────────────────────────────────────────────────────────────────────

def _settings():
    return QSettings()


def theme_names():
    """Return the list of saved theme names (may be empty)."""
    s = _settings()
    names = s.value(_K_NAMES, [])
    if isinstance(names, str):          # QSettings collapses 1-item lists
        names = [names]
    return list(names) if names else []


def has_any_theme():
    return bool(theme_names())


def get_theme(name):
    """Return a theme dict for *name*, falling back to DEFAULT_THEME values
    for any missing field."""
    s = _settings()
    out = dict(DEFAULT_THEME)
    for f in _FIELDS:
        v = s.value(_K_THEME.format(name=name, field=f), None)
        if v not in (None, ""):
            out[f] = v
    # Migration: themes saved before 2026-07 carried the then-default
    # "EPSG:3857 · Generated with Biome" footnote — really the basemap tile
    # CRS, which misread as the measurement CRS. Treat that exact legacy
    # default as unset so old themes pick up the corrected default; a
    # user-customised footnote is untouched.
    if out.get("footnote") == "EPSG:3857 · Generated with Biome":
        out["footnote"] = DEFAULT_THEME["footnote"]
    out["name"] = name
    out["trimLogo"] = str(out.get("trimLogo", "false")).lower() in ("true", "1", "yes")
    try:
        out["logoScale"] = max(0.25, min(1.0, float(out.get("logoScale") or 0.55)))
    except (TypeError, ValueError):
        out["logoScale"] = 0.55
    return out


def render_theme_preview(theme, height=420, title="BASELINE HABITATS"):
    """The real sidebar rendered for `theme` as a QPixmap `height` px tall —
    the live preview in the branding dialog, so a logo-size or colour change
    shows exactly what the map will print."""
    import tempfile
    from qgis.PyQt.QtGui import QImage, QPainter
    from qgis.PyQt.QtCore import QRectF
    from qgis.PyQt.QtSvg import QSvgRenderer
    from . import panel_renderer as pr

    t = dict(theme)
    t["trimLogo"] = str(t.get("trimLogo", "false")).lower() in ("true", "1", "yes")
    meta = {
        "title":     title,
        "subtitle":  "Client name",
        "ref_line":  "Project reference",
        "date_text": "01 January 2026",
        "scale_text": "1:2,500",
        "created":   "TJ",
        "approved":  "AB",
        "show_footnote": True,
    }
    fd, path = tempfile.mkstemp(suffix="_biome_preview.svg")
    os.close(fd)
    try:
        svg_path, _ = pr.render_panel(t, meta, _PLUGIN_DIR, out_path=path)
        w = int(round(height * 702.0 / 2100.0))
        img = QImage(w * 2, height * 2, QImage.Format.Format_ARGB32)
        img.fill(QColor("#F7F8F6"))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(svg_path).render(p, QRectF(0, 0, w * 2, height * 2))
        # Stand-in legend rows so the gap reads as a legend, not empty space.
        from qgis.PyQt.QtGui import QFont, QPen
        p.setPen(QPen(QColor(t.get("bodyText", "#1A2420"))))
        y = int(2 * height * 0.205)
        for lbl, col in (("Grassland", "#C9E2A5"), ("Woodland", "#6A9C4E"),
                         ("Scrub", "#A9C07A"), ("Standing water", "#7FB7E0"),
                         ("Hedgerow", "#3F7F3F")):
            sw = int(w * 2 * 0.09)
            p.fillRect(int(w * 2 * 0.06), y - sw + 6, sw, sw, QColor(col))
            p.setFont(QFont("Inter", int(height * 0.028) or 7))
            p.drawText(int(w * 2 * 0.06) + sw + 12, y, lbl)
            y += int(sw * 1.6)
        p.end()
        pm = QPixmap.fromImage(img.scaled(
            w, height, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        return pm
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def save_theme(name, theme):
    """Create or overwrite a theme and register its name."""
    s = _settings()
    for f in _FIELDS:
        s.setValue(_K_THEME.format(name=name, field=f), theme.get(f, DEFAULT_THEME[f]))
    names = theme_names()
    if name not in names:
        names.append(name)
        s.setValue(_K_NAMES, names)


def delete_theme(name):
    s = _settings()
    for f in _FIELDS:
        s.remove(_K_THEME.format(name=name, field=f))
    names = [n for n in theme_names() if n != name]
    s.setValue(_K_NAMES, names)
    if active_theme_name() == name:
        s.setValue(_K_ACTIVE, names[0] if names else "")


def active_theme_name():
    return _settings().value(_K_ACTIVE, "") or ""


def set_active(name):
    _settings().setValue(_K_ACTIVE, name)


def active_theme():
    """Return the active theme dict, or None if none exists yet."""
    name = active_theme_name()
    names = theme_names()
    if not names:
        return None
    if name not in names:
        name = names[0]
        set_active(name)
    return get_theme(name)


def ensure_bootstrap(parent=None):
    """First-run helper. If no themes exist, create a default one and prompt
    the user once to pick colours / logo. Returns the active theme dict.
    Safe to call before every export."""
    if has_any_theme():
        return active_theme()
    # Seed a starter theme so the export always has something to use, even if
    # the user cancels the dialog.
    save_theme("Default", dict(DEFAULT_THEME))
    set_active("Default")
    dlg = BrandingDialog(parent, first_run=True)
    dlg.exec()
    return active_theme()


# ── Dialog ────────────────────────────────────────────────────────────────────

class _Swatch(QPushButton):
    """A colour button that opens QColorDialog and remembers its hex value."""
    def __init__(self, hex_value, parent=None):
        super().__init__(parent)
        self._hex = hex_value
        self.setFixedSize(40, 24)
        self.clicked.connect(self._pick)
        self._refresh()

    def _refresh(self):
        self.setStyleSheet(
            f"background:{self._hex}; border:1px solid #888; border-radius:3px;"
        )

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._hex), self, "Pick colour")
        if c.isValid():
            self._hex = c.name().upper()
            self._refresh()
            if callable(getattr(self, "on_change", None)):
                self.on_change()

    def hex(self):
        return self._hex

    def set_hex(self, value):
        self._hex = value
        self._refresh()


class BrandingDialog(QDialog):
    """Create / edit / switch themes and set the logo. Re-openable any time."""

    def __init__(self, parent=None, first_run=False):
        super().__init__(parent)
        self.setWindowTitle("Customise Map")
        self.setMinimumWidth(420)
        self._build_ui(first_run)
        self._load_into_editor(active_theme_name() or (theme_names() or [""])[0])

    # -- UI ------------------------------------------------------------------
    def _build_ui(self, first_run):
        root = QVBoxLayout()

        if first_run:
            intro = QLabel(
                "Set up how your final maps look. You can change this or add "
                "more themes any time from the Branding button."
            )
            intro.setWordWrap(True)
            root.addWidget(intro)

        # Active-theme selector ------------------------------------------------
        sel = QGroupBox("Theme")
        sl = QHBoxLayout()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(theme_names())
        self.theme_combo.currentTextChanged.connect(self._load_into_editor)
        new_btn = QPushButton("New…")
        new_btn.clicked.connect(self._new_theme)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_theme)
        sl.addWidget(self.theme_combo, 1)
        sl.addWidget(new_btn)
        sl.addWidget(del_btn)
        sel.setLayout(sl)
        root.addWidget(sel)

        # Colour editor --------------------------------------------------------
        cg = QGroupBox("Colours")
        cl = QFormLayout()
        self.sw_header = _Swatch(DEFAULT_THEME["header"])
        self.sw_title  = _Swatch(DEFAULT_THEME["titleText"])
        self.sw_body   = _Swatch(DEFAULT_THEME["bodyText"])
        for sw in (self.sw_header, self.sw_title, self.sw_body):
            sw.on_change = self._update_preview
        cl.addRow("Header bar:",  self.sw_header)
        cl.addRow("Title text:",  self.sw_title)
        cl.addRow("Body text:",   self.sw_body)
        cg.setLayout(cl)
        root.addWidget(cg)

        # Logo -----------------------------------------------------------------
        lg = QGroupBox("Logo")
        ll = QHBoxLayout()
        self.logo_edit = QLineEdit()
        self.logo_edit.setPlaceholderText("No logo selected")
        self.logo_edit.setReadOnly(True)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_logo)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: (self.logo_edit.clear(), self._update_preview()))
        ll.addWidget(self.logo_edit, 1)
        ll.addWidget(browse)
        ll.addWidget(clear)
        lg.setLayout(ll)
        root.addWidget(lg)

        from qgis.PyQt.QtWidgets import QCheckBox, QSlider
        self.trim_check = QCheckBox(
            "Trim transparent / white margins so the logo fills its band")
        self.trim_check.toggled.connect(lambda _c: self._update_preview())
        root.addWidget(self.trim_check)

        # Logo size: fraction of the sidebar width the logo band may use.
        # Scale/Date and the legend reflow around it, so the preview on the
        # right is the real panel at the chosen size.
        sz_row = QHBoxLayout()
        self.size_label = QLabel("Logo size: 55%")
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(25, 100)
        self.size_slider.setSingleStep(5)
        self.size_slider.setPageStep(10)
        self.size_slider.setValue(55)
        self.size_slider.setToolTip(
            "How much of the sidebar width the logo may fill. Smaller logos "
            "pull the Scale/Date block down and give the legend more room.")
        self.size_slider.valueChanged.connect(self._on_size_changed)
        sz_row.addWidget(self.size_label)
        sz_row.addWidget(self.size_slider, 1)
        root.addLayout(sz_row)

        # Footnote ------------------------------------------------------------
        fg = QGroupBox("Footnote")
        fl = QHBoxLayout()
        self.footnote_edit = QLineEdit()
        self.footnote_edit.setPlaceholderText("e.g. OSGB36 / EPSG:27700 · Generated with Biome")
        fl.addWidget(self.footnote_edit, 1)
        fg.setLayout(fl)
        root.addWidget(fg)
        hint = QLabel("Shown when the footnote is enabled on the export window.")
        hint.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(hint)

        # Live preview — the real sidebar at the chosen colours / logo size ----
        pv_box = QGroupBox("Preview (map sidebar)")
        pv_l = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_h = 400
        self.preview.setMinimumHeight(self._preview_h + 8)
        self.preview.setStyleSheet("border:1px solid #ccc; border-radius:4px; background:#E8EBE6;")
        pv_l.addStretch(1)
        pv_l.addWidget(self.preview)
        pv_l.addStretch(1)
        pv_box.setLayout(pv_l)
        root.addWidget(pv_box)

        # Buttons --------------------------------------------------------------
        bb = QDialogButtonBox()
        save_btn = bb.addButton("Save && Use", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        save_btn.clicked.connect(self._save_and_use)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self.setLayout(root)

    # -- helpers -------------------------------------------------------------
    def _current_editor_theme(self):
        return {
            "header":    self.sw_header.hex(),
            "titleText": self.sw_title.hex(),
            "bodyText":  self.sw_body.hex(),
            "logo":      self.logo_edit.text().strip(),
            "trimLogo":  "true" if self.trim_check.isChecked() else "false",
            "footnote":  self.footnote_edit.text().strip(),
            "logoScale": f"{self.size_slider.value() / 100.0:.2f}",
        }

    def _load_into_editor(self, name):
        if not name:
            return
        t = get_theme(name)
        self._loading = True
        try:
            self.sw_header.set_hex(t["header"])
            self.sw_title.set_hex(t["titleText"])
            self.sw_body.set_hex(t["bodyText"])
            self.logo_edit.setText(t["logo"])
            self.trim_check.setChecked(bool(t.get("trimLogo")))
            self.footnote_edit.setText(t.get("footnote", ""))
            self.size_slider.setValue(int(round(float(t.get("logoScale", 0.55)) * 100)))
            self.size_label.setText(f"Logo size: {self.size_slider.value()}%")
        finally:
            self._loading = False
        self._update_preview()

    def _on_size_changed(self, value):
        self.size_label.setText(f"Logo size: {value}%")
        if not getattr(self, "_loading", False):
            self._update_preview()

    def _new_theme(self):
        name, ok = QInputDialog.getText(self, "New theme", "Theme name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in theme_names():
            QMessageBox.warning(self, "Name in use", "A theme with that name already exists.")
            return
        starter = dict(DEFAULT_THEME)
        starter["logo"] = ""          # user adds their own
        starter["trimLogo"] = "false"  # only trim when they opt in
        save_theme(name, starter)
        self.theme_combo.addItem(name)
        self.theme_combo.setCurrentText(name)

    def _delete_theme(self):
        name = self.theme_combo.currentText()
        if not name:
            return
        if len(theme_names()) <= 1:
            QMessageBox.information(self, "Keep one theme",
                                    "At least one theme must remain.")
            return
        delete_theme(name)
        self.theme_combo.removeItem(self.theme_combo.currentIndex())

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose logo", "",
            "Images (*.png *.jpg *.jpeg *.svg)")
        if path:
            self.logo_edit.setText(path)
            self._update_preview()

    def _update_preview(self):
        """Render the real sidebar for the theme being edited (colours, logo,
        logo size); falls back to a header swatch if the renderer fails."""
        if getattr(self, "_loading", False):
            return
        t = self._current_editor_theme()
        try:
            self.preview.setPixmap(render_theme_preview(t, self._preview_h))
            return
        except Exception:
            pass
        pm = QPixmap(360, 80)
        pm.fill(QColor("#F7F8F6"))
        from qgis.PyQt.QtGui import QPainter, QFont
        p = QPainter(pm)
        p.fillRect(0, 0, 360, 40, QColor(t["header"]))
        p.setPen(QColor(t["titleText"]))
        f = QFont("Inter", 11, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(12, 26, "BASELINE HABITATS")
        p.setPen(QColor(t["bodyText"]))
        p.setFont(QFont("Inter", 9))
        p.drawText(12, 62, "Created by · Date · Reviewed by")
        p.end()
        self.preview.setPixmap(pm)

    def _save_and_use(self):
        name = self.theme_combo.currentText().strip()
        if not name:
            name = "Default"
            self.theme_combo.addItem(name)
            self.theme_combo.setCurrentText(name)
        save_theme(name, self._current_editor_theme())
        set_active(name)
        self.accept()
