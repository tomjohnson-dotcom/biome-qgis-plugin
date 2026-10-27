# map_exporter.py
# Loads ROAVR_Template.qpt exactly as-is.
# Populates text labels, sets layers, fixes scalebar segments, hides basemap in legend.

import os
import re
import traceback
from datetime import date

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QComboBox, QDialogButtonBox, QGroupBox, QCheckBox,
    QScrollArea, QWidget, QLabel, QDateEdit, QFrame,
)
from qgis.PyQt.QtCore import Qt, QTimer, QSettings, QDate
from qgis.PyQt.QtXml import QDomDocument

from qgis.core import (
    QgsProject, QgsPrintLayout, QgsReadWriteContext,
    QgsLayoutItemMap, QgsLayoutItemLabel,
    QgsLayoutItemLegend, QgsLayoutItemScaleBar,
    QgsLegendRenderer, QgsLegendStyle,
    QgsLayerTree, QgsLayerTreeLayer, QgsLayerTreeGroup,
    QgsCoordinateTransform, QgsVectorLayer,
    QgsMessageLog, Qgis,
)

from . import config as _config
from .layer_manager import is_redline_name, REDLINE_NAME_ALIASES

AUTO_TITLES = {
    "Baseline Habitats":          "Baseline Habitat Survey Map",
    "Proposed Habitats":          "Proposed Habitats Map",
    "De Minimis Assessment":      "De Minimis Habitat Assessment Map",
    "PRF and Target Note Map":    "PRF and Target Note Map",
    "PRA Map":                    "Preliminary Roost Assessment Map",
    "Ecological Enhancement Map": "Ecological Enhancement Map",
    "Emergence Survey Layout":    "Emergence Survey Layout",
    "Building Map":               "Building Location Map",
    "Tree Map":                   "Tree Location Map",
    "Building & Tree Map":        "Building & Tree Location Map",
    "Enhancement Availability Map": "Enhancement Availability Map",
    "Custom":                     "Habitat Survey Map",
}

# Session-only title memory for the Create Final Map dialog: once a map is
# created with an edited title, that title comes back pre-filled for the same
# map type until QGIS restarts (or the plugin reloads). Deliberately module
# state, not QSettings — reverting to the default on restart is the intended
# behaviour. Only stored on OK, never on Cancel.
_SESSION_TITLES = {}

# ── Persistent title overrides (settings cog → "Map titles & headings") ──────
# The user can permanently reword any shipped default title. Layering, lowest
# to highest: shipped default → persistent override → the session-only edits
# (_SESSION_TITLES above / the desk buttons' right-click rename).
_TITLE_OVERRIDE_GROUP = "BiomeMapper/titleOverride"       # final-map defaults
_DESK_TITLE_GROUP     = "BiomeMapper/deskTitleOverride"   # desk map titles
_DESK_LEGEND_GROUP    = "BiomeMapper/deskLegendOverride"  # desk legend headings


def get_title_override(group, key):
    """The user's persistent override for one title, or '' when unset."""
    try:
        return str(QSettings().value(f"{group}/{key}", "") or "").strip()
    except Exception:
        return ""


def set_title_override(group, key, text):
    """Store one persistent title override; blank (or the shipped default,
    which callers pass as '') removes it."""
    try:
        s = QSettings()
        text = (text or "").strip()
        if text:
            s.setValue(f"{group}/{key}", text)
        else:
            s.remove(f"{group}/{key}")
    except Exception:
        pass


def default_map_title(mt):
    """Create Final Map's default title for a map type: the user's persistent
    override when set, else the shipped AUTO_TITLES default."""
    return get_title_override(_TITLE_OVERRIDE_GROUP, mt) or AUTO_TITLES.get(mt, mt)


def _regional_desk_default(map_type):
    """Shipped title that depends on the site's country: the priority-habitat
    map is drawn from the Habitat Map of Scotland for a Scottish site, so it
    must not be headed "Priority Habitats" there. None = no regional
    variant, use the ordinary shipped default."""
    if map_type == "Priority Habitats Map":
        try:
            from . import region
            if region.is_scotland():
                return region.phi_map_title()
        except Exception:
            pass
    return None


def desk_map_title(map_type):
    """A desk study map's short title (names the layout / export file): the
    user's persistent override when set, else the shipped DESK_TITLES default
    (or its regional variant for a Scottish site)."""
    return (get_title_override(_DESK_TITLE_GROUP, map_type)
            or _regional_desk_default(map_type)
            or DESK_TITLES.get(map_type, map_type))


def desk_panel_title(map_type):
    """A desk study map's printed sheet title. A persistent override replaces
    BOTH the short and the printed title (mirroring the session rename)."""
    o = get_title_override(_DESK_TITLE_GROUP, map_type)
    return (o or _regional_desk_default(map_type)
            or DESK_PANEL_TITLES.get(map_type, DESK_TITLES.get(map_type, map_type)))


def desk_legend_heading(map_type, default):
    """The legend section heading printed on a desk study map."""
    return (get_title_override(_DESK_LEGEND_GROUP, map_type)
            or _regional_desk_default(map_type) or default)


def _final_map_name(title):
    """Layout name for a finished map: no 'Biome –' prefix, always ends 'Map'.

    Titles already ending in 'Map' or 'Layout' are left untouched so we never
    produce '… Map Map' or '… Layout Map'."""
    name = (title or "").strip()
    low  = name.lower()
    if not (low.endswith("map") or low.endswith("layout")):
        name = f"{name} Map"
    return name


# Building & Tree Mapper map types — these keep the redline beneath their
# features in the render order (legend still lists the redline first).
BT_MAP_TYPES = {"Building Map", "Tree Map", "Building & Tree Map",
                "Enhancement Availability Map"}

# Ecological maps — every ecological feature renders ABOVE the boundary lines
# (only the basemap stays at the bottom); legend still lists the redline first.
ECO_MAP_TYPES = {"PRF and Target Note Map", "Ecological Enhancement Map",
                 "Emergence Survey Layout", "PRA Map"}

# Feature types shown on each specialist map
PRF_TARGET_FTYPES    = {"PRF", "Water Body", "Bird Nest", "Target Note"}
ECO_ENHANCE_FTYPES   = {"Bat Box", "Bird Box", "Bee Brick", "Insect Box",
                         "Wildlife Gap", "Building", "Herptile Refuge",
                         "Temporary Fencing"}


def _blend_hex(fg_hex, bg_hex, alpha):
    """Opaque QColor equal to *fg_hex* drawn at *alpha* over *bg_hex*."""
    from qgis.PyQt.QtGui import QColor
    try:
        fg, bg = QColor(fg_hex), QColor(bg_hex)
        r = round(bg.red()   * (1 - alpha) + fg.red()   * alpha)
        g = round(bg.green() * (1 - alpha) + fg.green() * alpha)
        b = round(bg.blue()  * (1 - alpha) + fg.blue()  * alpha)
        return QColor(r, g, b)
    except Exception:
        return QColor(fg_hex)


def _add_dynamic_scale_label(layout, map_item, theme, panel_meta):
    """Place a live "1:X" label over the sidebar's "Scale @ A4" value cell,
    showing the TRUE ground-scale denominator.

    A native Numeric scale bar was used here previously, but it renders
    map_item.scale() — the PROJECTED-unit denominator, which on the EPSG:3857
    map overstates the printed scale ~1.6x at UK latitudes (a true 1:776 map
    read "1:1,250").

    The label's text is a native [% %] expression that QGIS evaluates at
    every paint (the standard dynamic-scale-label mechanism), so the value
    tracks content zoom in the layout designer with no Python in the loop and
    survives project save/reopen. It computes the ground denominator as
    @map_scale x (ground width / projected width), where ground width is the
    extent's mid-line transformed to EPSG:27700 — true-scale at UK latitudes
    and the CRS the reports quote distances in. An extentChanged hook
    additionally forces a refresh so the cell repaints the moment the map is
    zoomed. Returns the item or None (the cell then just shows the caption
    with no value)."""
    try:
        from . import panel_renderer as pr
        box = pr.scale_value_box_mm(theme, panel_meta)
        if not box:
            return None
        from qgis.core import (QgsLayoutItemLabel, QgsLayoutPoint,
                               QgsLayoutSize, QgsUnitTypes)
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtGui import QFont
        _mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters

        ratio = QgsLayoutItemLabel(layout)
        ratio.setId("Scale Ratio")
        map_id = map_item.id() or "Map 1"
        expr = (
            "with_variable('mv', item_variables('{mid}'), "
            "with_variable('e', map_get(@mv, 'map_extent'), "
            "'1:' || format_number("
            "map_get(@mv, 'map_scale') "
            "* length(transform(make_line("
            "make_point(x_min(@e), y(centroid(@e))), "
            "make_point(x_max(@e), y(centroid(@e)))), "
            "map_get(@mv, 'map_crs'), 'EPSG:27700')) "
            "/ map_get(@mv, 'map_extent_width'), 0)))"
        ).format(mid=map_id)
        ratio.setText("[%" + expr + "%]")
        f = QFont("Inter")
        f.setPointSizeF(7.6)
        f.setWeight(QFont.Weight.Medium)
        ratio.setFont(f)
        # Match the muted look of the other metadata values exactly: those are
        # body colour at 0.55 opacity over the panel background, so pre-blend
        # to an opaque equivalent.
        body = (theme or {}).get("bodyText", "#1A2420")
        ratio.setFontColor(_blend_hex(body, "#F7F8F6", 0.55))
        try:
            ratio.setMargin(0.0)
        except Exception:
            pass
        try:
            ratio.setHAlign(Qt.AlignmentFlag.AlignLeft)
            ratio.setVAlign(Qt.AlignmentFlag.AlignVCenter)
        except Exception:
            pass
        ratio.setBackgroundEnabled(False)
        ratio.setFrameEnabled(False)
        layout.addLayoutItem(ratio)
        x, y, w, h = box
        ratio.attemptMove(QgsLayoutPoint(x, y, _mm))
        ratio.attemptResize(QgsLayoutSize(w, h, _mm))

        # The expression re-evaluates whenever the label paints; this hook
        # forces that repaint the moment the map content is zoomed, rather
        # than waiting for the next designer refresh. Same pattern as
        # final_layout.bind_scalebar_autofit — the Qt connection owns the
        # closure, so it lives (and dies) with the map item.
        def _refresh():
            try:
                ratio.refresh()
            except RuntimeError:
                pass        # layout was deleted; the connection dies with it
        try:
            map_item.extentChanged.connect(_refresh)
        except Exception:
            pass
        return ratio
    except Exception:
        QgsMessageLog.logMessage(
            f"Scale label error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Warning)
        return None


# Survey-position feature types for the Emergence Survey Layout.
# Point markers live in "Ecological Features"; their field-of-view wedges
# live in "Ecological Area Features".
EMERGENCE_POINT_FTYPES = {"Surveyor", "Observer Station", "Camera",
                          "Thermal Camera", "Static Bat Detector"}
EMERGENCE_AREA_FTYPES  = {"Surveyor Field of View", "Observer Field of View",
                          "Camera Field of View", "Thermal Camera Field of View"}

ECO_LAYER_NAMES = {"Ecological Features", "Ecological Linear Features"}
ECO_AREA_LAYER_NAME = "Ecological Area Features"

SCALEBAR_BOX_MM = 48.4688
MAP_W_MM        = 226.786  # page width left of the sidebar (built in final_layout.py)
BASEMAP_NAMES   = {"Google Hybrid", "Google Satellite", "OS Maps (Raster)", "OS Outdoor", "ESRI World Imagery"}

# Imagery credit printed on every exported map (the "Imagery" metadata row).
# The tile providers all require attribution on a published figure, so this is
# derived from the basemap actually on the map rather than left to the user.
# Kept short on purpose: the panel's value column fits roughly 22 characters
# before it would run past the sidebar margin.
BASEMAP_ATTRIBUTION = {
    "Google Hybrid":      "© Google {year}",
    "Google Satellite":   "© Google {year}",
    "ESRI World Imagery": "© Esri, Maxar {year}",
    "OS Maps (Raster)":   "© Crown copyright {year}",
    "OS Outdoor":         "© Crown copyright {year}",
}


def _imagery_credit(layers):
    """The attribution string for the basemap a map is actually drawn over, or
    '' when there is none. Only the TOPMOST basemap is credited — they stack, so
    that is the one the reader can see."""
    year = date.today().year
    for lyr in layers or []:
        try:
            name = lyr.name()
        except Exception:
            continue
        if name in BASEMAP_ATTRIBUTION:
            return BASEMAP_ATTRIBUTION[name].format(year=year)
        if _is_basemap_layer(lyr):
            return ""   # a basemap we have no credit wording for — say nothing
    return ""

SETTINGS_KEY_CREATED  = "BiomeMapper/createdBy"
SETTINGS_KEY_STATS    = "BiomeMapper/showStats"
SETTINGS_KEY_AREA     = "BiomeMapper/showArea"
SETTINGS_KEY_UNITS    = "BiomeMapper/showUnits"

# ── Per-project memory ────────────────────────────────────────────────────────
# Job-specific Project Information (Client / Project No. — the same two values
# the Metric exporter shows as Applicant / Project name) is stored in the QGIS
# *project* via readEntry/writeEntry, NOT in module state. That means it:
#   • survives reloading the plugin (the project outlives the plugin's Python,
#     which is why the old module-dict version was wiped on every reload),
#   • is written into the QGIS project and restored when it is reopened,
#   • is kept separate per project.
# Prepared By deliberately does not live here — it stays in QSettings so a name
# carries across every project (see SETTINGS_KEY_CREATED).
_PROJ_SCOPE = "BiomeMapper"


def project_value(key, default=""):
    """Read a per-project Biome value (survives plugin reload + project save/reopen)."""
    try:
        val, _ok = QgsProject.instance().readEntry(_PROJ_SCOPE, key, default)
        return val if val else default
    except Exception:
        return default


def set_project_value(key, value):
    """Store a per-project Biome value. Callers guard blanks where they must not
    overwrite a previously remembered name."""
    try:
        QgsProject.instance().writeEntry(_PROJ_SCOPE, key, value or "")
    except Exception:
        pass


class _ProjectBackedSession:
    """Dict-like view over the per-project Client / Project-No. entries, shared
    with the Metric exporter (where they appear as Applicant / Project name).
    Replaces the old volatile module dict so the values live in the project
    rather than only the current QGIS run; the .get / [] interface is unchanged
    so existing call sites — and bng_plugin's write-project hook, which uses the
    same 'clientName' / 'projectNumber' keys — keep working."""
    _KEYMAP = {"client": "clientName", "proj_no": "projectNumber"}

    def get(self, key, default=""):
        return project_value(self._KEYMAP.get(key, key), default)

    def __getitem__(self, key):
        return self.get(key, "")

    def __setitem__(self, key, value):
        set_project_value(self._KEYMAP.get(key, key), value)


_SESSION = _ProjectBackedSession()


# ── Dialog ────────────────────────────────────────────────────────────────────

class MapExportDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map Builder")
        self.setMinimumWidth(380)
        self._settings = QSettings()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        tg = QGroupBox("Map Type")
        tl = QVBoxLayout()
        self.map_type_combo = QComboBox()
        self.map_type_combo.addItems([
            "Baseline Habitats",
            "Proposed Habitats",
            "De Minimis Assessment",
            "PRF and Target Note Map",
            "PRA Map",
            "Ecological Enhancement Map",
            "Emergence Survey Layout",
            "Custom",
        ])
        self.map_type_combo.currentTextChanged.connect(self._on_type_changed)
        tl.addWidget(self.map_type_combo)
        tg.setLayout(tl)
        layout.addWidget(tg)

        ig = QGroupBox("Project Information")
        fl = QFormLayout()
        self.title_edit    = QLineEdit()
        self.client_edit   = QLineEdit()
        self.proj_no_edit  = QLineEdit()
        self.created_edit  = QLineEdit()

        # Pre-fill all remembered values
        self.client_edit.setText(  _SESSION.get("client",  ""))
        self.proj_no_edit.setText( _SESSION.get("proj_no", ""))
        self.created_edit.setText( self._settings.value(SETTINGS_KEY_CREATED,  ""))

        fl.addRow("Map Title:",    self.title_edit)
        fl.addRow("Client Name:", self.client_edit)
        fl.addRow("Project No.:", self.proj_no_edit)
        fl.addRow("Prepared By:", self.created_edit)
        ig.setLayout(fl)
        layout.addWidget(ig)

        # Optional aerial-imagery capture date. This is deliberately opt-in per
        # map because tiled basemaps may be mosaics with no single reliable date.
        self.imagery_date_check = QCheckBox("Show imagery date on map")
        self.imagery_date_edit = QLineEdit()
        self.imagery_date_edit.setPlaceholderText("e.g. June 2022 or Various dates")
        self.imagery_date_edit.setEnabled(False)
        self.imagery_date_check.toggled.connect(self.imagery_date_edit.setEnabled)
        imagery_row = QHBoxLayout()
        imagery_row.addWidget(self.imagery_date_check)
        imagery_row.addWidget(self.imagery_date_edit, 1)
        layout.addLayout(imagery_row)

        # ── Survey date(s) (Emergence Survey Layout only) ──────────────
        # The emergence map records when the survey was actually walked, so the
        # date is entered by hand rather than auto-filled with today's date. A
        # single survey setup may be repeated over more than one day, so up to
        # three dates can be supplied; each ticked date prints on its own line.
        self.date_group = QGroupBox("Survey Date(s)")
        dg = QVBoxLayout()
        dg.setSpacing(3)
        self._date_rows = []          # [(QCheckBox, QDateEdit)]
        for i in range(3):
            drow = QHBoxLayout()
            chk  = QCheckBox("Date %d" % (i + 1))
            chk.setChecked(i == 0)    # first date on by default
            de   = QDateEdit()
            de.setCalendarPopup(True)
            de.setDisplayFormat(_config.qt_date_format())
            de.setDate(QDate.currentDate())
            de.setEnabled(i == 0)
            chk.toggled.connect(de.setEnabled)
            drow.addWidget(chk)
            drow.addWidget(de, 1)
            dg.addLayout(drow)
            self._date_rows.append((chk, de))
        self.date_group.setLayout(dg)
        self.date_group.setVisible(False)
        layout.addWidget(self.date_group)

        # ── Redline option (Baseline and PRF maps only) ───────────────
        self.redline_check = QCheckBox("Include Red Line Boundary on map")
        self.redline_check.setChecked(False)
        self.redline_check.setStyleSheet("padding: 4px 2px;")
        layout.addWidget(self.redline_check)

        # ── Blue Line Boundary option (all types except Custom) ────────────
        self.site_boundary_check = QCheckBox("Include Blue Line Boundary on map")
        self.site_boundary_check.setChecked(False)
        self.site_boundary_check.setStyleSheet("padding: 4px 2px;")
        layout.addWidget(self.site_boundary_check)

        # ── Print scale (standard planning scales) ─────────────────────
        # Councils require location plans at 1:1250 or 1:2500 and site/block
        # plans at 1:500 / 1:200 / 1:100. These are TRUE ground scales:
        # final_layout.set_true_scale corrects for the EPSG:3857 projection,
        # so the printed sheet really is 1:1250 on the ground. "Fit to site"
        # keeps the automatic redline framing. Deliberately NOT persisted —
        # a sticky fixed scale could silently crop the next site's map.
        sg = QGroupBox("Print Scale @ A4")
        sl = QVBoxLayout()
        self.scale_combo = QComboBox()
        self.scale_combo.addItem("Fit to site (automatic)", 0)
        for d, note in ((100,   "block plan"),
                        (200,   "block plan"),
                        (500,   "site plan"),
                        (1250,  "location plan"),
                        (2500,  "location plan"),
                        (5000,  ""),
                        (10000, "")):
            lbl = f"1:{d:,}" + (f"   ({note})" if note else "")
            self.scale_combo.addItem(lbl, d)
        self.scale_combo.setToolTip(
            "Fix the exported map at a standard planning print scale — a true "
            "ground scale at A4, as councils require for location plans "
            "(1:1250 / 1:2500) and site plans (1:500). The map stays centred "
            "on the site. 'Fit to site' frames the redline automatically as "
            "before.")
        sl.addWidget(self.scale_combo)
        sg.setLayout(sl)
        layout.addWidget(sg)

        # ── Sidebar footnote option (the 'EPSG · Generated with Biome' line) ─
        self.footnote_check = QCheckBox("Show projection / Biome footnote")
        # Opt in for each map.  Do not carry a previous map's choice into the
        # next export: the unobtrusive default is a clean, unbranded footer.
        self.footnote_check.setChecked(False)
        self.footnote_check.setStyleSheet("padding: 4px 2px;")
        layout.addWidget(self.footnote_check)

        # ── Habitat stats options (Baseline / Proposed maps only) ──────────
        # Area and BNG units can be toggled independently.
        self.area_check = QCheckBox("Add area to each habitat in the key")
        self.area_check.setToolTip(
            "Appends each habitat's area (or length) to its legend entry.")
        self.area_check.setChecked(
            self._settings.value(SETTINGS_KEY_AREA, "false") in (True, "true"))
        self.area_check.setStyleSheet("padding: 4px 2px;")
        layout.addWidget(self.area_check)

        self.units_check = QCheckBox("Add BNG units to each habitat in the key")
        self.units_check.setToolTip(
            "Appends each habitat's BNG units to its legend entry. "
            "Figures match the Dashboard tab exactly.")
        self.units_check.setChecked(
            self._settings.value(SETTINGS_KEY_UNITS, "false") in (True, "true"))
        self.units_check.setStyleSheet("padding: 4px 2px;")
        layout.addWidget(self.units_check)

        # ── Georeferenced background (drone / aerial) ─────────────────
        # Any georeferenced raster the user has added (e.g. a drone shot to
        # improve a site with poor satellite imagery) can be laid over the
        # basemap on the exported map. Individually selectable when there is
        # more than one. Hidden entirely when the project has none, and on
        # Custom maps (the checklist there already lists every layer).
        self.georef_group = QGroupBox("Include georeferenced layer")
        self.georef_group.setToolTip(
            "Overlay a georeferenced raster (e.g. a drone shot) on the map, "
            "above the satellite basemap. Tick the ones to include.")
        gl = QVBoxLayout()
        gl.setSpacing(3)
        self._georef_checks_layout = QVBoxLayout()
        self._georef_checks_layout.setSpacing(2)
        self._georef_checks_layout.setContentsMargins(2, 2, 2, 2)
        gl.addLayout(self._georef_checks_layout)
        self.georef_group.setLayout(gl)
        self.georef_group.setVisible(False)
        layout.addWidget(self.georef_group)
        self._georef_checkboxes = []   # [(checkbox, layer), ...]

        # ── Custom map layer selection ────────────────────────────────
        self.custom_layers_group = QGroupBox("Layers to Include")
        cl = QVBoxLayout()
        cl.setSpacing(3)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(200)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self._layer_checks_layout = QVBoxLayout(inner)
        self._layer_checks_layout.setSpacing(2)
        self._layer_checks_layout.setContentsMargins(2, 2, 2, 2)
        scroll.setWidget(inner)
        cl.addWidget(scroll)
        self.custom_layers_group.setLayout(cl)
        self.custom_layers_group.setVisible(False)
        layout.addWidget(self.custom_layers_group)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.setLayout(layout)
        self._on_type_changed(self.map_type_combo.currentText())

    # Map types that offer the redline option (opt-in)
    _REDLINE_MAP_TYPES = {"PRF and Target Note Map", "PRA Map",
                          "Ecological Enhancement Map",
                          "Emergence Survey Layout", "Building Map", "Tree Map",
                          "Building & Tree Map", "Enhancement Availability Map"}
    # Map types that always include redline (no checkbox needed)
    _ALWAYS_REDLINE_TYPES = {"Baseline Habitats", "Proposed Habitats", "De Minimis Assessment"}

    def _on_type_changed(self, mt):
        self.title_edit.setText(_SESSION_TITLES.get(mt) or default_map_title(mt))
        # Survey date entry — Emergence Survey Layout only
        self.date_group.setVisible(mt == "Emergence Survey Layout")
        # Show checkbox only for PRF and Eco Enhancement maps
        self.redline_check.setVisible(mt in self._REDLINE_MAP_TYPES)
        if mt not in self._REDLINE_MAP_TYPES:
            self.redline_check.setChecked(False)
        # Show site boundary checkbox for all types except Custom
        # (Custom already exposes full layer control via the checklist)
        is_custom = (mt == "Custom")
        self.site_boundary_check.setVisible(not is_custom)
        if is_custom:
            self.site_boundary_check.setChecked(False)
        # Show layer checklist only for Custom
        self.custom_layers_group.setVisible(is_custom)
        if is_custom:
            self._populate_layer_checks()
        # Georeferenced-raster overlay: offered on every non-Custom type that
        # has a map (Custom already lists these in its checklist). Hidden when
        # the project contains no georeferenced rasters.
        self._populate_georef_checks()
        self.georef_group.setVisible(
            not is_custom and bool(self._georef_checkboxes))
        # Habitat stats only make sense for Baseline / Proposed habitat maps.
        stats_ok = mt in ("Baseline Habitats", "Proposed Habitats")
        self.area_check.setVisible(stats_ok)
        self.units_check.setVisible(stats_ok)
        if not stats_ok:
            self.area_check.setChecked(False)
            self.units_check.setChecked(False)

    def _populate_georef_checks(self):
        """Fill the georeferenced-raster checklist. One checkbox per detected
        georef layer; OFF by default — a drone overlay is opt-in per export,
        not automatically on the map."""
        from qgis.core import QgsProject
        while self._georef_checks_layout.count():
            item = self._georef_checks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._georef_checkboxes = []
        project = QgsProject.instance()
        for lyr in get_georef_layers(project):
            cb = QCheckBox(lyr.name())
            cb.setChecked(False)
            cb.setStyleSheet("font-size:11px;")
            self._georef_checks_layout.addWidget(cb)
            self._georef_checkboxes.append((cb, lyr))

    def _populate_layer_checks(self):
        """Populate layer checklist. Groups get a checkbox that toggles all children."""
        from qgis.core import QgsProject, QgsLayerTreeLayer, QgsLayerTreeGroup
        while self._layer_checks_layout.count():
            item = self._layer_checks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._layer_checkboxes = []  # [(QCheckBox, layer)]

        root = QgsProject.instance().layerTreeRoot()
        # Use the canvas's actual layer set rather than only each tree node's
        # local checkbox. This respects hidden parent groups and makes Custom
        # open with exactly the non-basemap layers currently being viewed.
        visible_ids = None
        try:
            from qgis.utils import iface as qgis_iface
            if qgis_iface is not None:
                visible_ids = {
                    layer.id() for layer in qgis_iface.mapCanvas().layers()
                    if layer is not None
                }
        except Exception:
            visible_ids = None

        def _walk(node, indent=0):
            if isinstance(node, QgsLayerTreeLayer):
                lyr = node.layer()
                if lyr and not _is_basemap_layer(lyr):
                    cb = QCheckBox("  " * indent + lyr.name())
                    cb.setChecked(
                        lyr.id() in visible_ids
                        if visible_ids is not None else node.isVisible())
                    cb.setStyleSheet("font-size:11px;")
                    self._layer_checks_layout.addWidget(cb)
                    self._layer_checkboxes.append((cb, lyr))
            elif isinstance(node, QgsLayerTreeGroup) and node != root:
                # Record index before adding children
                start_idx = len(self._layer_checkboxes)
                # Add group checkbox
                any_vis = (
                    any(c.layerId() in visible_ids for c in node.findLayers())
                    if visible_ids is not None
                    else any(c.isVisible() for c in node.findLayers()))
                cb_grp = QCheckBox("  " * indent + node.name())
                cb_grp.setChecked(any_vis)
                cb_grp.setStyleSheet("font-size:11px; font-weight:500; color:#175E3B;")
                self._layer_checks_layout.addWidget(cb_grp)
                # Walk children first to get their checkboxes
                for child in node.children():
                    _walk(child, indent + 1)
                # Wire group toggle to all children added in this group
                end_idx = len(self._layer_checkboxes)
                child_cbs = [cb for cb, _ in self._layer_checkboxes[start_idx:end_idx]]
                cb_grp.toggled.connect(lambda checked, cbs=child_cbs:
                    [cb.setChecked(checked) for cb in cbs])

        for child in root.children():
            _walk(child)

    def values(self):
        mt       = self.map_type_combo.currentText()
        client   = self.client_edit.text().strip()
        proj_no  = self.proj_no_edit.text().strip()
        created  = self.created_edit.text().strip()

        # Client and project no — remembered per-project (written into the QGIS project
        # via the project-backed _SESSION). Blank is not stored, so it never
        # wipes a previously entered value.
        if client:
            _SESSION["client"]  = client
        if proj_no:
            _SESSION["proj_no"] = proj_no
        # Prepared By — persist permanently
        if created:
            self._settings.setValue(SETTINGS_KEY_CREATED,  created)
        # Footnote toggle — intentionally applies only to this map.  The next
        # map starts unticked so branding is always an explicit choice.
        show_footnote = self.footnote_check.isChecked()
        # Habitat stats toggles — persist permanently
        show_area  = self.area_check.isChecked()
        show_units = self.units_check.isChecked()
        self._settings.setValue(SETTINGS_KEY_AREA,
                                "true" if show_area else "false")
        self._settings.setValue(SETTINGS_KEY_UNITS,
                                "true" if show_units else "false")

        # Baseline and Proposed always include redline
        # PRF and Eco Enhancement include it only if the checkbox is ticked
        if mt in self._ALWAYS_REDLINE_TYPES:
            include_redline = True
        else:
            include_redline = self.redline_check.isChecked()

        # Collect user-selected layers for Custom map
        custom_layers = None
        if mt == "Custom" and hasattr(self, "_layer_checkboxes"):
            custom_layers = [lyr for cb, lyr in self._layer_checkboxes if cb.isChecked()]

        # Ticked georeferenced rasters to overlay on the basemap (non-Custom;
        # Custom controls these through its own checklist).
        georef_layers = []
        if mt != "Custom":
            georef_layers = [lyr for cb, lyr in self._georef_checkboxes
                             if cb.isChecked()]

        # Title — remembered per map type for the rest of the QGIS session, so
        # re-running the same map keeps the user's edited title. Clearing the
        # field (or typing the default back) forgets the override. "Default"
        # here already includes any persistent reword from the settings cog.
        title = self.title_edit.text().strip() or default_map_title(mt)
        if title != default_map_title(mt):
            _SESSION_TITLES[mt] = title
        else:
            _SESSION_TITLES.pop(mt, None)

        # Survey date(s) — Emergence Survey Layout only. Each ticked row is
        # formatted to match the app's date style and prints on its own line.
        survey_dates = []
        if mt == "Emergence Survey Layout":
            for chk, de in self._date_rows:
                if chk.isChecked():
                    qd = de.date()
                    survey_dates.append(
                        _config.format_date(date(qd.year(), qd.month(), qd.day())))

        return {
            "map_type":               mt,
            "title":                  title,
            "client":                 client,
            "proj_no":                proj_no,
            "created":                created,
            "imagery_date":           (self.imagery_date_edit.text().strip()
                                        if self.imagery_date_check.isChecked()
                                        else ""),
            "include_redline":        include_redline,
            "include_site_boundary":  self.site_boundary_check.isChecked(),
            # 0 / None = automatic redline fit; otherwise a TRUE ground
            # denominator (1250 = a 1:1250 location plan).
            "fixed_scale":            self.scale_combo.currentData() or None,
            "show_footnote":          show_footnote,
            "show_area":              show_area,
            "show_units":             show_units,
            "custom_layers":          custom_layers,
            "georef_layers":          georef_layers,
            "survey_dates":           survey_dates,
        }


# ── Builder ───────────────────────────────────────────────────────────────────

def build_layout(iface, meta):
    try:
        _build(iface, meta)
    except Exception as e:
        QgsMessageLog.logMessage(f"Map error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Critical)
        iface.messageBar().pushMessage(
            "Biome", f"Map error: {e}", level=Qgis.MessageLevel.Critical, duration=0
        )


def _build(iface, meta):
    project    = QgsProject.instance()
    manager    = project.layoutManager()
    plugin_dir = os.path.dirname(os.path.abspath(__file__))

    # ── Active theme (first run prompts once; editable any time) ──────
    from . import branding, final_layout
    from . import panel_renderer as pr
    theme = branding.ensure_bootstrap(iface.mainWindow()) or dict(branding.DEFAULT_THEME)

    today = _config.format_date()
    is_emergence = meta.get("map_type") == "Emergence Survey Layout"

    # ── Fresh layout, built in code (no .qpt) ─────────────────────────
    lname    = _final_map_name(meta['title'])
    existing = manager.layoutByName(lname)
    if existing:
        manager.removeLayout(existing)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(lname)

    items    = final_layout.build(layout, project)
    map_item = items["map"]
    panel    = items["panel"]
    sb       = items["scalebar"]
    legend   = items["legend"]

    # ── Set map layers ────────────────────────────────────────────────
    mt = meta["map_type"]
    restore_map = {}
    renderer_restore = {}
    if mt == "Baseline Habitats":
        layers = _get_layers(project, "Baseline")
    elif mt == "Proposed Habitats":
        layers = _get_layers(project, "Proposed")
    elif mt == "De Minimis Assessment":
        layers = _get_deminimis_layers(project)
    elif mt == "PRF and Target Note Map":
        layers, restore_map = _get_eco_map_layers(project, PRF_TARGET_FTYPES)
    elif mt == "PRA Map":
        layers, restore_map = _get_pra_layers(project)
    elif mt == "Ecological Enhancement Map":
        layers, restore_map = _get_eco_map_layers(project, ECO_ENHANCE_FTYPES)
    elif mt == "Emergence Survey Layout":
        layers, restore_map, renderer_restore = _get_emergence_layers(project)
    elif mt == "Building Map":
        from .building_tree_mapper import BUILDING_LAYER_NAME
        layers = _get_bt_layers(project, BUILDING_LAYER_NAME)
    elif mt == "Tree Map":
        from .building_tree_mapper import (
            TREE_BUFFER_LAYER_NAME, TREE_CEZ_LAYER_NAME, TREE_LAYER_NAME,
            TREE_RAZ_LAYER_NAME, TREE_RPA_LAYER_NAME,
        )
        layers = _get_bt_layers(
            project,
            [TREE_LAYER_NAME, TREE_RPA_LAYER_NAME, TREE_BUFFER_LAYER_NAME,
             TREE_CEZ_LAYER_NAME, TREE_RAZ_LAYER_NAME],
        )
    elif mt == "Building & Tree Map":
        from .building_tree_mapper import (
            BUILDING_LAYER_NAME, TREE_BUFFER_LAYER_NAME, TREE_CEZ_LAYER_NAME,
            TREE_LAYER_NAME, TREE_RAZ_LAYER_NAME, TREE_RPA_LAYER_NAME,
        )
        layers = _get_bt_layers(
            project,
            [TREE_LAYER_NAME, TREE_RPA_LAYER_NAME, TREE_BUFFER_LAYER_NAME,
             TREE_CEZ_LAYER_NAME, TREE_RAZ_LAYER_NAME, BUILDING_LAYER_NAME],
        )
    elif mt == "Enhancement Availability Map":
        from .curtilage_mapper import AVAIL_LAYER_NAME
        layers = _get_bt_layers(project, AVAIL_LAYER_NAME)
    else:
        # Custom — use the layers the user explicitly selected in the dialog
        selected = meta.get("custom_layers") or []
        basemap  = _get_visible_basemaps(project)
        layers   = selected + basemap

    # Remove redline unless the user opted in
    # (Custom maps skip this — the user controls layers explicitly via checklist)
    if mt != "Custom" and not meta.get("include_redline", True):
        layers = [l for l in layers if not is_redline_name(l.name())]

    # Remove site boundary unless the user ticked the checkbox
    # (Custom maps skip this — the user controls layers explicitly via checklist)
    if mt != "Custom" and not meta.get("include_site_boundary", False):
        layers = [l for l in layers if l.name() != "Blue Line Boundary"]

    map_item = None
    for item in layout.items():
        if isinstance(item, QgsLayoutItemMap):
            map_item = item
            break

    if map_item and layers:
        # Custom preserves layer-tree order; building/tree and ecological maps
        # render all their features above the redline; habitat maps split the
        # point/linear habitats above the redline and the areas below it.
        if mt == "Custom":
            ordered = layers
        elif mt in BT_MAP_TYPES:
            ordered = _order_bt_layers(layers)
        elif mt in ECO_MAP_TYPES:
            ordered = _order_eco_layers(layers)
        else:
            ordered = _order_layers(layers)
        # Insert any user-chosen georeferenced rasters (drone shots) just ABOVE
        # the basemap: they improve the aerial backdrop but must stay beneath
        # every habitat/redline/feature layer. De-dup in case one is already in
        # the set. Layout map item renders index-0 on top, so "above basemap"
        # means immediately before the basemap layers at the tail.
        georef = [g for g in (meta.get("georef_layers") or []) if g not in ordered]
        if georef:
            basemaps = [l for l in ordered if _is_basemap_layer(l)]
            if basemaps:
                cut = ordered.index(basemaps[0])
                ordered = ordered[:cut] + georef + ordered[cut:]
            else:
                ordered = ordered + georef
        map_item.setLayers(ordered)
        map_item.setKeepLayerSet(True)
        map_item.setDrawAnnotations(False)
        canvas = iface.mapCanvas()
        _configure_map_item(map_item, canvas, project, ordered)

        # ── Auto-fit the map to the redline boundary extent ───────────
        # Falls back to the canvas extent (set above) when no redline layer
        # is present in the project.
        _fit_to_redline(map_item, project, ordered)

        # Guarantee the map frame still fills the page exactly. Even with
        # zoomToExtent some builds can nudge the frame, so pin it last.
        try:
            from qgis.PyQt.QtCore import QRectF
            _pg = layout.pageCollection().page(0).pageSize()
            _PW, _PH = _pg.width(), _pg.height()
            if _PW < _PH:
                _PW, _PH = max(_PW, _PH), min(_PW, _PH)
            map_item.attemptSetSceneRect(QRectF(0.0, 0.0, _PW - pr.PANEL_W_MM, _PH))
        except Exception:
            pass

        # ── Fixed planning print scale (e.g. 1:1250 location plan) ─────
        # Applied AFTER the redline fit (keeps the map centred on the site)
        # and after the frame pin (so the scale maths sees the final frame
        # size). fixed_scale is a TRUE ground denominator; set_true_scale
        # corrects the 3857 projected scale so the sheet really prints at it.
        if meta.get("fixed_scale"):
            final_layout.set_true_scale(map_item, meta["fixed_scale"])

        # Emergence: the cone refs are CURVED labels carried on a generated arc
        # layer (the wedges' own labels are suppressed). Rebuild those arcs at the
        # LAYOUT scale — sized exactly from the map item's extent/frame so the
        # curved text fits the page — and lay the invisible-line arc layer on top
        # so the refs render in the export. Excluded from the legend below.
        if is_emergence:
            try:
                from . import fov_label_arcs as FLA
                from qgis.PyQt.QtCore import QSettings
                _ext = map_item.extent()
                _frame_mm = map_item.rect().width() or 1.0
                _upm = _ext.width() / _frame_mm
                _icon_mm = float(QSettings().value("biome/eco_icon_mm", 6) or 6)
                _arc = FLA.refresh(icon_mm=_icon_mm, units_per_mm=_upm)
                if _arc is not None:
                    map_item.setLayers([_arc] + list(map_item.layers()))
            except Exception:
                pass

    # ── Render themed sidebar panel (after extent → scale is known) ───
    panel_meta = None
    if map_item:
        panel_meta = dict(meta)
        if is_emergence:
            # Survey date(s) entered by hand — the map records when the survey
            # was walked, not when it was drawn. The first date fills the usual
            # "Date" cell; with two or more, build explicit metadata rows so the
            # extra dates stack on their own lines under it rather than
            # overflowing a single cell.
            survey_dates = [d for d in (meta.get("survey_dates") or []) if d]
            panel_meta["date_text"] = survey_dates[0] if survey_dates else ""
            if len(survey_dates) >= 2:
                rows  = [("Date", survey_dates[0])]
                rows += [("", d) for d in survey_dates[1:]]
                rows += [("Scale @ A4", ""),
                         ("Prepared by", meta.get("created", ""))]
                if meta.get("imagery_date"):
                    rows.append(("Imagery date", meta["imagery_date"]))
                panel_meta["rows"] = rows
        else:
            panel_meta["date_text"] = today
        # The scale ratio is drawn by a live layout label (TRUE ground scale,
        # re-computed on zoom — see _add_dynamic_scale_label) — leave the SVG
        # value blank (the caption "Scale @ A4" still renders) to avoid a
        # frozen value beneath it.
        panel_meta["scale_text"] = ""
        panel_meta["subtitle"]   = meta.get("client", "")
        panel_meta["ref_line"]   = (f"Project {meta['proj_no']}"
                                    if meta.get("proj_no") else "")
        panel_meta["show_footnote"] = meta.get("show_footnote", True)
        # Credit the imagery the map is drawn over (required by every tile
        # provider on a published figure).
        panel_meta["attribution"] = _imagery_credit(map_item.layers())
        # The "u" suffix only appears when BNG units are tagged on, so only
        # explain it beside the LEGEND heading in that case.
        if meta.get("show_units") and mt in ("Baseline Habitats",
                                             "Proposed Habitats"):
            panel_meta["legend_note"] = "u = BNG units"

        svg_path, leg_rect = pr.render_panel(theme, panel_meta, plugin_dir)
        panel.setPicturePath(svg_path)
        # Reposition the native legend into the gap the panel left for it,
        # which moves with the (dynamic) header height.
        if legend and leg_rect:
            from qgis.core import QgsLayoutPoint, QgsLayoutSize, QgsUnitTypes
            _mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters
            lx, ly, lw, lh = leg_rect
            legend.attemptMove(QgsLayoutPoint(lx, ly, _mm))
            legend.attemptResize(QgsLayoutSize(lw, lh, _mm))

    # ── Optional per-habitat stats, tagged onto the legend labels ─────
    # (Baseline / Proposed only). Figures come straight from the Summary
    # tab's classifiers so the numbers match exactly.
    stat_figs = None
    show_area  = bool(meta.get("show_area"))
    show_units = bool(meta.get("show_units"))
    if (show_area or show_units) and mt in ("Baseline Habitats", "Proposed Habitats"):
        try:
            from . import map_stats
            phase = "Baseline" if mt == "Baseline Habitats" else "Proposed"
            stat_figs = map_stats.habitat_figures(iface, phase)
        except Exception:
            QgsMessageLog.logMessage(
                f"Stats error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Warning)
            stat_figs = None
        # Fallback: if the net classifier produced nothing, read figures
        # directly from the layers actually on the map so the legend still
        # shows each habitat's area + units.
        if not stat_figs and map_item:
            try:
                from . import map_stats
                stat_figs = map_stats.direct_figures(map_item.layers())
            except Exception:
                QgsMessageLog.logMessage(
                    f"Direct stats error:\n{traceback.format_exc()}",
                    "Biome", Qgis.MessageLevel.Warning)

    # ── Scalebar: link + fit to panel width ───────────────────────────
    if sb and map_item:
        sb.setLinkedMap(map_item)
        final_layout.fit_scalebar(sb, map_item, target_mm=pr.PANEL_W_MM - 22.0, max_mm=pr.PANEL_W_MM - 8.0)
        final_layout.bind_scalebar_autofit(sb, map_item, target_mm=pr.PANEL_W_MM - 22.0, max_mm=pr.PANEL_W_MM - 8.0)

    # ── Trees: bake per-status area + units onto the rule labels so the
    #    Retained / Newly planted entries each report their own figures
    #    (Summary-accurate). Done before the legend reads the renderer. ──
    # This runs on EVERY habitat generation — not only when a stat box is
    # ticked — because trees bake their figures into the rule-based renderer,
    # which persists on the project layer. If it were skipped when both boxes
    # are off, figures baked on a previous "units on" run would linger in the
    # key; with both flags off, _bake_tree_figures strips the labels back bare.
    if mt in ("Baseline Habitats", "Proposed Habitats") and map_item:
        phase = "Baseline" if mt == "Baseline Habitats" else "Proposed"
        from qgis.core import QgsWkbTypes as _Wkb
        for _lyr in map_item.layers():
            try:
                if (_lyr and _lyr.isValid()
                        and _lyr.fields().indexOf("sub_habitat_type") >= 0
                        and _Wkb.geometryType(_lyr.wkbType()) == _Wkb.PointGeometry):
                    _bake_tree_figures(_lyr, phase, show_area, show_units)
            except Exception:
                pass

    # ── Legend: hide basemap layers ───────────────────────────────────

    if legend and map_item:
        legend.setLinkedMap(map_item)
        legend.setAutoUpdateModel(False)

        # Rebuild legend root from the map's actual layer set so there are
        # no duplicates and the structure is clean.
        root = legend.model().rootGroup()

        # Clear everything
        for child in root.children()[:]:
            root.removeChildNode(child)

        # Re-add only the non-basemap layers from the map item's layer set,
        # deduped by layer id AND by display name. The name dedup guards against
        # duplicate layers (e.g. a project reopened into a transient state) that
        # would otherwise repeat a heading like "Area Habitats" several times.
        seen = set()
        seen_names = set()
        # Legend order is independent of render order: keep the redline at the
        # very top of the key on every map, even where it renders beneath the
        # other layers (e.g. the building/tree maps). The emergence map also
        # lifts its building/tree context layers above the survey positions.
        if mt == "Emergence Survey Layout":
            legend_layers = _emergence_legend_order(map_item.layers())
        elif mt == "PRA Map":
            legend_layers = _pra_legend_order(map_item.layers())
        else:
            legend_layers = _redline_first(map_item.layers())
        _georef_ids = {g.id() for g in (meta.get("georef_layers") or [])}
        for lyr in legend_layers:
            if lyr.name() in BASEMAP_NAMES:
                continue
            if lyr.name() == "Ecological FOV Labels":
                continue   # invisible arc carrier for curved FOV refs — no swatch
            if _is_basemap_layer(lyr):
                continue
            if lyr.id() in _georef_ids:
                continue   # georeferenced background raster — no legend swatch
            if lyr.id() in seen:
                continue
            if lyr.name() in seen_names:
                continue
            seen.add(lyr.id())
            seen_names.add(lyr.name())
            if lyr.name() == "Local Wildlife Sites (Imported)":
                # Imported LWS data carries the real site name in its `name`
                # field. Categorise by that for the Custom Map key instead of
                # showing only the generic designation class.
                from .designated_sites import apply_imported_lws_style
                apply_imported_lws_style(lyr)
            node = QgsLayerTreeLayer(lyr)
            # "(Map Only)" is a canvas-management hint (layer drawn on the map
            # but excluded from exports of features) — meaningless to a map
            # reader, so drop it from the printed legend heading. Cosmetic
            # rename only: detach from the layer name first. Match the suffix
            # case-/whitespace-insensitively so it still strips if the layer
            # name drifts (e.g. after GeoPackage persistence round-trips).
            disp = re.sub(r"\s*\(\s*map only\s*\)\s*$", "", lyr.name(),
                          flags=re.IGNORECASE).strip()
            disp = re.sub(r"\s*\(\s*imported\s*\)\s*$", "", disp,
                          flags=re.IGNORECASE).strip()
            # Use conventional UK planning terminology in the printed key.
            # Keep the internal layer names unchanged because plugin logic and
            # saved projects identify the layers by those stable names.
            if disp == "Redline Boundary":
                disp = "Red Line Boundary"
            elif disp == "Blue Line Boundary":
                disp = "Blue Line Boundary"
            if disp and disp != lyr.name():
                try:
                    node.setUseLayerName(False)
                except AttributeError:
                    pass
                node.setName(disp)
            root.addChildNode(node)

        # For eco maps flatten any remaining group headings
        if mt in ("PRF and Target Note Map", "Ecological Enhancement Map",
                  "Emergence Survey Layout", "PRA Map"):
            _flatten_legend_groups(root)

        # For de minimis map, group the legend into Baseline, Proposed, Loss
        if mt == "De Minimis Assessment":
            _build_deminimis_legend(root)

        # Show only the symbol categories actually present in the map extent,
        # not every category defined in each layer's categorized style.
        try:
            legend.setLegendFilterByMapEnabled(True)
        except Exception:
            pass
        try:
            legend.updateLegend()
        except Exception:
            pass

        # Render embedded newlines (from label wrapping) as actual line breaks.
        # This MUST be set whether or not the stats pass runs — otherwise a
        # wrapped long name collapses back onto one line (and runs off the
        # panel) whenever the area/units boxes are off, while the height
        # estimate still counts it as wrapped and over-shrinks the font.
        try:
            legend.setWrapString("\n")
        except Exception:
            pass

        # Measure the rows that will actually render (present categories only),
        # while the labels are still raw category values, so the font tier can
        # be picked below without resizing the item. Pass how many figure
        # lines the stats pass will append so wrapped + figure lines are
        # counted. Both column widths are measured for EVERY map type, so the
        # fitter can compare the two layouts on equal terms; the label passes
        # below then wrap at whichever width the chosen layout allows. (In a
        # two-column key the "area · units" figure is split onto separate
        # lines — see _tag_legend_with_stats — hence the split count there.)
        # When figures are requested, the bare (no-figure) variants are
        # measured too: they are the fitter's last resort for a key so busy
        # that not even the two-column minimum font fits it with figures on.
        _fig_on = bool(stat_figs)
        _leg_entry_lines, _n_leg_headings, _leg_breakdown = _measure_habitat_legend(
            legend, 1 if _fig_on else 0, map_item)
        _leg_entry_lines_2col, _, _ = _measure_habitat_legend(
            legend,
            ((1 if show_area else 0) + (1 if show_units else 0))
            if _fig_on else 0,
            map_item, max_chars=_LEGEND_WRAP_2COL)
        # NOTE: when no figures are requested the bare lists must be the SAME
        # objects — _fit_legend_layout detects "figures in play" by identity.
        _leg_bare_1col, _leg_bare_2col = _leg_entry_lines, _leg_entry_lines_2col
        if _fig_on:
            _leg_bare_1col, _, _ = _measure_habitat_legend(legend, 0, map_item)
            _leg_bare_2col, _, _ = _measure_habitat_legend(
                legend, 0, map_item, max_chars=_LEGEND_WRAP_2COL)

        # ── Auto-fit the key: choose layout + font tier from the estimates ──
        # Decision only at this point — the styling is applied after the label
        # passes below, but the column count must be known FIRST so those
        # passes wrap labels at the width the chosen layout allows. Applies to
        # EVERY map type: give the key the whole gap down to where the
        # metadata can still sit above the footer, pick the largest font tier
        # (12 pt max → 5 pt min) whose estimated height fits — so a sparse key
        # scales up and a busy one scales down — split into two columns only
        # when even the most compact tier still overruns, and drop the
        # area/unit figures only when not even that fits. Heights are
        # ESTIMATED from the visible row count, NOT adjustBoxSize, which is
        # unreliable here: the legend filters by map at render time, so a
        # headless measure sizes to every (unfiltered) category and balloons
        # the item off-page.
        _leg_cols, _leg_tier, _leg_est_h = 1, None, None
        _leg_figs_dropped = False
        _leg_ly_mm = _leg_avail_mm = _leg_limit_mm = None
        if panel is not None:
            try:
                ly_mm = leg_rect[1] if leg_rect else 30.0
                try:
                    limit_mm = pr.legend_bottom_limit_mm(theme, panel_meta)
                except Exception:
                    limit_mm = None
                room_mm = ((limit_mm - ly_mm) if limit_mm
                           else (leg_rect[3] if leg_rect else 86.0))
                (_leg_cols, _leg_tier, _leg_est_h,
                 _leg_figs_dropped) = _fit_legend_layout(
                    _leg_entry_lines, _leg_entry_lines_2col,
                    _leg_bare_1col, _leg_bare_2col, _n_leg_headings,
                    room_mm - 2.0)
                _leg_ly_mm, _leg_avail_mm, _leg_limit_mm = (
                    ly_mm, room_mm - 2.0, limit_mm)
            except Exception:
                pass

        # Figures dropped: the key only fits as bare habitat names. Strip the
        # figures the tree bake wrote into the rule labels (BEFORE the phase-
        # strip pass stores per-node overrides built from them), skip the
        # stats tagging below, retract the "u = BNG units" note, and tell the
        # user their area/units boxes were overridden rather than fail silently.
        if _leg_figs_dropped:
            stat_figs = None
            if panel_meta is not None:
                panel_meta.pop("legend_note", None)
            if mt in ("Baseline Habitats", "Proposed Habitats") and map_item:
                phase = "Baseline" if mt == "Baseline Habitats" else "Proposed"
                from qgis.core import QgsWkbTypes as _Wkb
                for _lyr in map_item.layers():
                    try:
                        if (_lyr and _lyr.isValid()
                                and _lyr.fields().indexOf("sub_habitat_type") >= 0
                                and _Wkb.geometryType(_lyr.wkbType())
                                    == _Wkb.PointGeometry):
                            _bake_tree_figures(_lyr, phase, False, False)
                    except Exception:
                        pass
                try:
                    legend.updateLegend()
                except Exception:
                    pass
            try:
                iface.messageBar().pushMessage(
                    "Biome",
                    "Legend too long for the sidebar — the habitat area/unit "
                    "figures were left off the key so every habitat still fits.",
                    level=Qgis.MessageLevel.Info, duration=8)
            except Exception:
                pass

        # The map title already states Baseline vs Proposed, so drop that phase
        # word from the legend's layer headings and category labels first,
        # wrapping them at the chosen layout's width as it goes.
        if mt in ("Baseline Habitats", "Proposed Habitats"):
            _strip_phase_from_legend(
                legend,
                max_chars=(_LEGEND_WRAP_2COL if _leg_cols > 1
                           else _LEGEND_WRAP_1COL))

        # Tag each legend label with its habitat's area + BNG units last, so the
        # persisted overrides are the final word on each entry's label.
        if stat_figs:
            _tag_legend_with_stats(legend, stat_figs, show_area, show_units,
                                   narrow=_leg_cols > 1)

        # Style symbol-less rule-group rows ("Retained" / "Newly planted") as
        # headings so they read as titles above their symbol entries.
        _style_rule_group_headings(legend)

        # Apply the chosen tier + columns, then reflow the metadata block to
        # just below the (now-sized) legend.
        if panel is not None and _leg_tier is not None:
            # Column state is independent of font/symbol styling. Apply it in
            # its own guarded call so a version-specific styling API failure
            # cannot leave QGIS's two-column default in place.
            _apply_legend_columns(legend, _leg_cols)
            try:
                _apply_habitat_legend_tier(legend, _leg_tier)
                # Tree markers must keep their Small<Medium<Large size ratios
                # under the tier's flat symbol-size cap.
                _scale_marker_legend_patches(
                    legend, _HABITAT_LEGEND_TIERS[_leg_tier][4])
                panel_meta["legend_bottom_au"] = (
                    (_leg_ly_mm + _leg_est_h) * pr._U_PER_MM)
                new_svg, _ = pr.render_panel(theme, panel_meta, plugin_dir)
                panel.setPicturePath(new_svg)
                _legend_fit_debug(_leg_entry_lines, _n_leg_headings,
                                  _leg_ly_mm, _leg_avail_mm, _leg_tier,
                                  _leg_est_h, _leg_limit_mm, _leg_breakdown,
                                  cols=_leg_cols, dropped=_leg_figs_dropped)
            except Exception:
                pass

        # Wrap long labels for the map types that don't go through the phase-
        # strip pass (which already wraps), at the width the chosen column count
        # allows — so the rendered line count is the one the fit was based on.
        # Deliberately outside the fit's try block: even if the auto-fit
        # bailed, long names must still be wrapped or they run off the panel.
        if mt not in ("Baseline Habitats", "Proposed Habitats"):
            _wrap_legend_labels(
                legend,
                _LEGEND_WRAP_2COL if _leg_cols > 1 else _LEGEND_WRAP_1COL)

        # LAST: strip "(Map Only)" from headings (survives flatten + tier).
        _strip_map_only_headings(root)

    # ── Register and open ─────────────────────────────────────────────
    # Live ground-true scale label keeps the ratio in sync with zoom.
    if map_item and panel_meta:
        _add_dynamic_scale_label(layout, map_item, theme, panel_meta)
    manager.addLayout(layout)
    # Restore any renderers / subset strings we temporarily applied to eco
    # layers (renderer first, so the live layer never repaints all features
    # through the cone-only legend renderer).
    if renderer_restore:
        _restore_renderers(renderer_restore)
    if restore_map:
        _restore_subset_strings(restore_map)
    QTimer.singleShot(200, lambda: iface.openLayoutDesigner(layout))


# ── Legend helpers ────────────────────────────────────────────────────────────

def _tree_fig_line(entry, show_area, show_units):
    parts = []
    if show_area:
        parts.append(f"{entry['size']:,.3f} {entry.get('unit', 'ha')}")
    if show_units:
        parts.append(f"{entry['units']:,.2f} u")
    return " · ".join(parts)


def _strip_phase_word(s):
    for ph in ("Existing ", "Proposed ", "Baseline "):
        if s.startswith(ph):
            return s[len(ph):].strip()
    return s


def _bake_tree_figures(layer, phase, show_area, show_units):
    """Write each tree rule's own area + BNG units into its label, so the
    Retained group reports only retained trees' figures and the Newly planted
    group only newly-planted trees' (matching the Summary). Operates on the
    layer's rule-based renderer, which both the printed legend and the QGIS
    Layers panel read from — and which persists on the project layer, so when
    both show_area and show_units are False this instead strips any previously
    baked figures back off the labels. Also drops the redundant phase word from
    tree labels. Safe no-op for non rule-based renderers."""
    try:
        from qgis.core import QgsRuleBasedRenderer
        from . import map_stats
        r = layer.renderer()
        if not isinstance(r, QgsRuleBasedRenderer):
            return
        # With neither figure requested we only need to strip any figures baked
        # on a previous run, so skip the stats query and let every leaf rule
        # fall back to its bare base label below.
        want_fig = bool(show_area or show_units)
        figs = map_stats.tree_legend_figures(layer, phase) if want_fig else {}
        proposed = (phase == "Proposed")

        def _walk(rule, parent_label):
            for child in rule.children():
                kids = child.children()
                if kids:
                    _walk(child, child.label())
                    continue
                base = _strip_phase_word(child.label().split("\n")[0]).strip()
                if proposed and parent_label:
                    key = f"{parent_label}|{base}"
                else:
                    key = base
                entry = figs.get(key)
                # Re-bake from the base label each time so toggles/regeneration
                # don't stack figures.
                if entry:
                    fig = _tree_fig_line(entry, show_area, show_units)
                    child.setLabel(f"{base}\n{fig}" if fig else base)
                else:
                    child.setLabel(base)

        _walk(r.rootRule(), None)
        try:
            layer.triggerRepaint()
            layer.emitStyleChanged()
        except Exception:
            pass
    except Exception:
        QgsMessageLog.logMessage(
            f"Tree figure bake error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Warning)


def _strip_phase_from_legend(legend, max_chars=None):
    """Remove the "Baseline"/"Proposed"/"Existing" phase word from a legend's
    layer headings and category labels.

    The map title already states which phase the map shows, so repeating it on
    every legend heading ("Baseline - Area Habitats") and on every tree entry
    ("Proposed Large Urban Tree") is redundant. This strips a leading phase word
    (and an optional " - " / " " separator) from both, leaving e.g.
    "Area Habitats" and "Large Urban Tree". Habitat category labels have no such
    prefix and are left untouched. Labels are wrapped at *max_chars* — the
    caller passes the width of the column layout the fitter chose, so a
    two-column key gets narrower lines.
    """
    if max_chars is None:
        max_chars = _LEGEND_WRAP_1COL
    try:
        from qgis.core import QgsLayerTreeLayer, QgsMapLayerLegendUtils
        from qgis.PyQt.QtCore import Qt as _Qt
        model = legend.model()
        root  = model.rootGroup()

        def _strip(text):
            s = str(text or "")
            for phase in ("Baseline", "Proposed", "Existing"):
                for sep in (" - ", " – ", " — ", " "):
                    pre = phase + sep
                    if s.startswith(pre):
                        return s[len(pre):].lstrip()
            return s

        for tree_lyr in root.findLayers():
            if not isinstance(tree_lyr, QgsLayerTreeLayer):
                continue
            # Heading: override the displayed layer name (keeps the underlying
            # layer name intact for everything else).
            try:
                name = tree_lyr.name()
                stripped = _strip(name)
                if stripped and stripped != name:
                    # Detach the node label from the layer first — with the
                    # default useLayerName=True, setName() writes through to
                    # the project layer even on the legend's cloned tree.
                    try:
                        tree_lyr.setUseLayerName(False)
                    except AttributeError:
                        pass
                    tree_lyr.setName(stripped)
            except Exception:
                pass
            # Category / rule labels under this layer: strip the phase word and
            # wrap long names so they don't run off the panel edge. Wrapping has
            # to happen here (runs on every habitat map) rather than only in the
            # stats pass, otherwise a long habitat name overflows whenever the
            # area/units boxes are unticked. Use the indexed util (stored on the
            # layer-tree node) NOT node.setUserLabel: the transient node label is
            # dropped when the legend regenerates its nodes (e.g. when the font
            # tier is applied later), which left the name unwrapped at render.
            changed = False
            for idx, node in enumerate(model.layerLegendNodes(tree_lyr)):
                label = node.data(_Qt.ItemDataRole.DisplayRole) or ""
                stripped = _strip(label)
                wrapped = _wrap_legend_label(stripped, max_chars)
                if wrapped and wrapped != label:
                    try:
                        QgsMapLayerLegendUtils.setLegendNodeUserLabel(
                            tree_lyr, idx, wrapped)
                        changed = True
                    except Exception:
                        node.setUserLabel(wrapped)
            if changed:
                try:
                    model.refreshLayerLegend(tree_lyr)
                except Exception:
                    pass
        try:
            legend.adjustBoxSize()
        except Exception:
            pass
    except Exception:
        from qgis.core import QgsMessageLog, Qgis
        import traceback as _tb
        QgsMessageLog.logMessage(
            f"Legend phase-strip error:\n{_tb.format_exc()}", "Biome", Qgis.MessageLevel.Warning)


def _style_rule_group_headings(legend):
    """Render symbol-less rule-group rows (e.g. the tree status headings
    "Retained" / "Newly planted") as titles. The legend renderer offers no
    per-row font control — these rows share the symbol-label style with the
    entries beneath them — so uppercase them to read as headings."""
    try:
        from qgis.core import QgsMapLayerLegendUtils
        from qgis.PyQt.QtCore import Qt as _Qt
        model = legend.model()
        for tree_lyr in model.rootGroup().findLayers():
            nodes = model.layerLegendNodes(tree_lyr)
            changed = False
            for idx, node in enumerate(nodes):
                try:
                    has_symbol = node.symbol() is not None
                except AttributeError:
                    has_symbol = True
                if has_symbol:
                    continue
                label = str(node.data(_Qt.ItemDataRole.DisplayRole) or "")
                upper = label.upper()
                if label and upper != label:
                    try:
                        QgsMapLayerLegendUtils.setLegendNodeUserLabel(
                            tree_lyr, idx, upper)
                        changed = True
                    except Exception:
                        node.setUserLabel(upper)
            if changed:
                model.refreshLayerLegend(tree_lyr)
    except Exception:
        pass


# Font / symbol / spacing tiers for the map legend, applied to EVERY map type.
# The fitter picks the largest tier (index 0 = biggest) whose estimated height
# fits the sidebar gap, so a sparse key scales UP and a busy one scales DOWN.
# Label font runs 12 pt (max) → 5 pt (min) in 0.5 pt steps; the symbol swatch
# and inter-row spacing scale with the font. Each tuple is
# (label_pt, subgroup_pt, group_pt, sym_w_mm, sym_h_mm, space_mm).
_HABITAT_LEGEND_TIERS = [
    (fs, fs + 0.5, fs + 1.0, round(fs * 0.7, 1), round(fs * 0.45, 1),
     round(max(0.4, fs * 0.16), 1))
    for fs in (12.0, 11.5, 11.0, 10.5, 10.0, 9.5, 9.0, 8.5, 8.0, 7.5, 7.0,
               6.5, 6.0, 5.5, 5.0)
]


def _apply_habitat_legend_tier(legend, tier):
    """Restyle the habitat legend to compression *tier* (0 = default look,
    higher = more compact): smaller label/heading fonts, symbol swatches and
    spacing, so a long key shrinks enough to leave room for the Date/Scale
    block below it. No-op on any error."""
    try:
        from qgis.core import QgsLegendStyle
        from qgis.PyQt.QtGui import QFont
        label_pt, sub_pt, grp_pt, sw, sh, space = _HABITAT_LEGEND_TIERS[tier]
        for role, pt, weight in (
            (QgsLegendStyle.Style.SymbolLabel, label_pt, QFont.Weight.Normal),
            (QgsLegendStyle.Style.Subgroup,    sub_pt,   QFont.Weight.Bold),
            (QgsLegendStyle.Style.Group,       grp_pt,   QFont.Weight.Bold),
        ):
            st = legend.style(role)
            f  = st.font()
            f.setFamily("Inter")
            f.setPointSizeF(pt)
            f.setWeight(weight)
            st.setFont(f)
            legend.setStyle(role, st)
        try:
            legend.setSymbolWidth(sw)
            legend.setSymbolHeight(sh)
        except Exception:
            pass
        # setSymbolHeight only sizes FILL/LINE swatches — a marker symbol is
        # drawn at its own size, so the 6 mm ecological point icons ignored the
        # tier entirely and a key of them (a PRA map's PRFs) overran the
        # metadata block no matter how far the fonts shrank. Capping the symbol
        # size makes markers scale with the tier like everything else, which is
        # also what the height estimate below already assumes.
        try:
            legend.setMaximumSymbolSize(sh)
        except (AttributeError, TypeError):
            pass
        if space is not None:
            try:
                legend.setBoxSpace(space)
                legend.setSymbolSpace(space)
            except Exception:
                pass
    except Exception:
        pass


def _scale_marker_legend_patches(legend, sym_h_mm):
    """Preserve relative marker sizes in the legend key. The tier's
    setMaximumSymbolSize cap squashes every marker above it to the SAME size,
    so the Small/Medium/Large tree circles rendered identically. For any layer
    whose legend mixes marker sizes, give each node a CUSTOM symbol: a clone
    of its true symbol scaled so the largest fits the tier's symbol height
    (markers ignore per-node patch sizes, so the clone-and-resize is the only
    per-node control). Uniform-size marker keys (e.g. a PRA map's PRF icons)
    are left to the cap."""
    try:
        from qgis.core import QgsMapLayerLegendUtils, QgsMarkerSymbol
        model = legend.model()
        for tree_lyr in model.rootGroup().findLayers():
            nodes = model.layerLegendNodes(tree_lyr)
            sizes = {}
            for idx, node in enumerate(nodes):
                try:
                    sym = node.symbol()
                except AttributeError:
                    sym = None
                if isinstance(sym, QgsMarkerSymbol):
                    try:
                        sizes[idx] = (float(sym.size()), sym)
                    except Exception:
                        pass
            if len(sizes) < 2:
                continue
            vals = [s for s, _ in sizes.values()]
            biggest = max(vals)
            if biggest <= 0 or biggest - min(vals) < 0.01:
                continue
            scale = min(1.0, float(sym_h_mm) / biggest)
            for idx, (sz, sym) in sizes.items():
                scaled = sym.clone()
                scaled.setSize(max(1.2, round(sz * scale, 2)))
                QgsMapLayerLegendUtils.setLegendNodeCustomSymbol(
                    tree_lyr, idx, scaled)
            model.refreshLayerLegend(tree_lyr)
    except Exception:
        pass


def _predicted_label_lines(raw, max_chars=26):
    """Number of lines a legend label will occupy once its first line is
    phase-stripped and wrapped at *max_chars* — matching what
    `_strip_phase_from_legend` / the stats pass actually do. Any extra lines
    already embedded in the label (e.g. a baked tree figure) are preserved."""
    parts = str(raw or "").split("\n")
    first = _strip_phase_word(parts[0]).strip()
    wrapped = _wrap_legend_label(first, max_chars)
    return (wrapped.count("\n") + 1) + (len(parts) - 1)


def _legend_entry_key(raw):
    """De-dup key for a legend entry: its phase-stripped first-line label, case-
    folded. Used so repeated categories (the same habitat on many features),
    which the rendered legend shows once, are counted once."""
    return _strip_phase_word(str(raw or "").split("\n")[0]).strip().lower()


def _present_rule_labels(layer, map_item=None):
    """For a tree layer (rule-based renderer keyed on 'sub_habitat_type'), the
    set of `_legend_entry_key`s for size classes that actually have a feature —
    so empty classes defined in the style but absent on site aren't counted as
    legend rows (filter-by-map hides them). Returns None when the field is
    absent, so the caller falls back to counting every rule. When *map_item* is
    given, only features inside its extent count (matches filter-by-map)."""
    try:
        if layer is None or layer.fields().indexOf("sub_habitat_type") < 0:
            return None
        present = set()
        for feat in layer.getFeatures(_extent_request(layer, map_item)):
            v = feat["sub_habitat_type"]
            if v:
                present.add(_legend_entry_key(v))
        return present
    except Exception:
        return None


def _wrap_legend_labels(legend, max_chars=26):
    """Wrap every category/rule legend label at *max_chars* so long names don't
    run off the panel. Idempotent: labels already containing a newline (e.g. a
    tagged figure or an already-wrapped name) are left untouched. Used for the
    map types that don't pass through `_strip_phase_from_legend` (which wraps
    on its own), so the rendered line count matches the height estimate.
    Uses the indexed util (persists on the layer-tree node) not node.setUserLabel,
    so the wrap survives the legend regenerating its nodes when the font tier is
    applied afterwards."""
    from qgis.core import QgsLayerTreeLayer, QgsMapLayerLegendUtils
    from qgis.PyQt.QtCore import Qt as _Qt
    try:
        model = legend.model()
        for tree_lyr in model.rootGroup().findLayers():
            if not isinstance(tree_lyr, QgsLayerTreeLayer):
                continue
            changed = False
            for idx, node in enumerate(model.layerLegendNodes(tree_lyr)):
                label = node.data(_Qt.ItemDataRole.DisplayRole) or ""
                wrapped = _wrap_legend_label(str(label), max_chars)
                if wrapped != label:
                    try:
                        QgsMapLayerLegendUtils.setLegendNodeUserLabel(
                            tree_lyr, idx, wrapped)
                        changed = True
                    except Exception:
                        node.setUserLabel(wrapped)
            if changed:
                try:
                    model.refreshLayerLegend(tree_lyr)
                except Exception:
                    pass
    except Exception:
        pass


# Label wrap width (characters) for a one- and a two-column key. The two-column
# figure is roughly half because each column gets half the panel gap's width.
_LEGEND_WRAP_1COL = 26
_LEGEND_WRAP_2COL = 13


def _measure_habitat_legend(legend, fig_lines, map_item=None,
                            max_chars=_LEGEND_WRAP_1COL):
    """Return (entry_line_counts, n_headings, breakdown) for the habitat legend,
    counting only the categories that will actually render — de-duplicated by
    label, because the legend aggregates repeated categories into one row.
    (breakdown is a per-layer list of debug strings.)
    entry_line_counts has one item per visible entry: the rows it will occupy
    after phase-stripping + wrapping (plus *fig_lines* extra lines for
    categorized/area habitats whose figure is appended later — 1 in a wide
    key, one line per figure in a narrow one). Categorized layers also
    contribute a heading; a single-symbol layer is one inline row; a rule-based
    layer (trees) is a heading plus its rule rows (figures already baked into
    those labels). Call while labels are still raw category values so the
    present-value match is reliable; because wrapping always uses the same
    width, the predicted line counts match the final render regardless of the
    font tier chosen."""
    from qgis.core import QgsLayerTreeLayer, QgsSingleSymbolRenderer
    from qgis.PyQt.QtCore import Qt as _Qt
    entry_lines, n_headings = [], 0
    breakdown = []
    try:
        model = legend.model()
        for tree_lyr in model.rootGroup().findLayers():
            if not isinstance(tree_lyr, QgsLayerTreeLayer):
                continue
            lyr = tree_lyr.layer()
            nodes = model.layerLegendNodes(tree_lyr)
            present = _category_values_present(lyr, map_item) if lyr else None
            rend = lyr.renderer() if lyr else None
            lname = (lyr.name() if lyr else tree_lyr.name()) or "?"
            if present is not None:
                # Categorized: heading + present categories, DE-DUPLICATED by
                # label. The rendered legend aggregates repeated categories
                # (the same habitat on many features) into one row, so counting
                # every node over-states the height and over-shrinks the font.
                n_headings += 1
                seen = set()
                raw_labels = []
                # Display labels are not guaranteed to equal categorized
                # renderer values.  Ecological Buffer Zones, for example,
                # stores a numeric distance but displays "50 m buffer".  Use
                # the renderer category value for the presence test, while
                # retaining the friendly node label for wrapping/measurement.
                # This mirrors `_apply_legend_style`; comparing the display
                # label directly made the fitter count the buffer heading but
                # none of its visible rows, producing a cramped key.
                categories = []
                try:
                    categories = list(rend.categories())
                except Exception:
                    pass
                for idx, node in enumerate(nodes):
                    label = node.data(_Qt.ItemDataRole.DisplayRole) or ""
                    raw_labels.append(str(label))
                    if idx < len(categories):
                        value = categories[idx].value()
                        cat_val = value if value is not None else ""
                    else:
                        cat_val = "" if label in ("Other", lyr.name()) else label
                    if cat_val not in present:
                        continue
                    key = _legend_entry_key(label)
                    if key in seen:
                        continue
                    seen.add(key)
                    entry_lines.append(
                        _predicted_label_lines(label, max_chars) + fig_lines)
                if not seen:
                    entry_lines.append(1)
                breakdown.append(f"CAT '{lname}': nodes={len(nodes)} "
                                 f"present={len(present)} kept={len(seen)} "
                                 f"labels={raw_labels}")
            elif isinstance(rend, QgsSingleSymbolRenderer):
                # Single symbol (e.g. Redline): inline row, never figure-tagged.
                entry_lines.append(
                    _predicted_label_lines(tree_lyr.name()
                                           or (lyr.name() if lyr else ""),
                                           max_chars))
                breakdown.append(f"SINGLE '{lname}': nodes={len(nodes)}")
            else:
                # Rule-based (trees): heading + rule rows. The renderer defines
                # every size class (Small/Medium/Large/Very Large × Urban/Rural),
                # but filter-by-map only shows classes that actually have a
                # feature on site, so count only those — otherwise the empty
                # classes inflate the height and crush the font. De-duped too.
                n_headings += 1
                present_rules = _present_rule_labels(lyr, map_item) if lyr else None
                seen = set()
                raw_labels = []
                for node in nodes:
                    raw = node.data(_Qt.ItemDataRole.DisplayRole) or ""
                    raw_labels.append(str(raw))
                    key = _legend_entry_key(raw)
                    if present_rules is not None and key not in present_rules:
                        continue
                    if key in seen:
                        continue
                    seen.add(key)
                    entry_lines.append(_predicted_label_lines(raw, max_chars))
                if not seen:
                    entry_lines.append(1)
                breakdown.append(f"RULE '{lname}': nodes={len(nodes)} "
                                 f"present={present_rules} kept={len(seen)} "
                                 f"labels={raw_labels}")
    except Exception:
        pass
    return entry_lines, n_headings, breakdown


def _habitat_legend_height_mm(entry_lines, n_headings, tier, columns=1):
    """Estimated rendered height (mm) of the habitat legend at *tier*. Estimate
    only — deliberately not adjustBoxSize, which sizes to the unfiltered
    category list headless and balloons the item off-page. *entry_lines* is the
    per-entry line count from `_measure_habitat_legend`, so wrapped names and
    figure lines are counted."""
    # Re-calibrated 2026-07-29 by rendering the legend ALONE at 300 dpi and
    # measuring its ink height for 4/8/12/16/20-entry keys (1 and 3 headings,
    # 1 and 2 columns) across the tiers. The previous figures tracked well at
    # the big font tiers but ran up to 23% LOW at the compressed ones (20
    # entries at 5.5 pt: 106 mm real vs 86 mm estimated). That is the dangerous
    # direction — the metadata block is positioned at the ESTIMATED legend
    # bottom, so an under-estimate drops Date/Scale straight on top of the key,
    # which is exactly how the PRA map's icon-heavy key first showed the bug.
    # The measurements fit row_h = max(sym_h, lines x label_pt x 1.42) + 2.6 mm
    # to within a few tenths at every tier; the padding is font-independent,
    # which is why the old font-scaled spacing term drifted. Verified over 40
    # rendered cases: 0 under-estimates, worst ratio 0.91, mean 0.79 (i.e. it
    # now errs ~20% high, which only costs a little font size).
    ROW_PAD_MM = 2.6      # per-row padding QGIS adds; does NOT scale with font
    LINE_FACTOR = 1.42    # rendered line height / point size
    mm_per_pt = 0.3528
    label_pt, sub_pt, grp_pt, _sw, sym_h, space = _HABITAT_LEGEND_TIERS[tier]
    sp = space if space is not None else 1.8
    line = label_pt * mm_per_pt * LINE_FACTOR
    head_h = grp_pt * mm_per_pt * 1.2 + sp
    rows = sum(max(sym_h, n * line) + ROW_PAD_MM for n in entry_lines)
    # Columns split the ENTRY rows between them; the layer headings are counted
    # in full because QGIS repeats a heading in each column its layer spills
    # into, so this stays on the safe (over-estimating) side.
    return rows / max(1, columns) + n_headings * head_h + 3.0


def _fit_habitat_legend_tier(entry_lines, n_headings, available_mm, columns=1):
    """Pick the largest `_HABITAT_LEGEND_TIERS` index (0 = default look) whose
    estimated height fits *available_mm*; the most compact tier if none do."""
    for tier in range(len(_HABITAT_LEGEND_TIERS)):
        if _habitat_legend_height_mm(entry_lines, n_headings, tier,
                                     columns) <= available_mm:
            return tier
    return len(_HABITAT_LEGEND_TIERS) - 1


def _fit_legend_layout(entry_lines_1col, entry_lines_2col,
                       bare_lines_1col, bare_lines_2col, n_headings,
                       available_mm):
    """Choose (columns, tier, estimated_height_mm, figures_dropped) for the key.

    Layouts are tried in preference order and the first whose estimate fits
    wins: one column, two columns, then — only when figures are in play and
    nothing above fits — the same two layouts with the area/unit figure lines
    dropped (the user's habitat names outrank the supplementary figures, but
    the figures outrank the column count). A single column is always preferred
    — it reads better and matches every map the plugin shipped before; two
    columns roughly halve the height and buy back the largest font tier that
    then fits. `entry_lines_2col` is measured against the narrower two-column
    wrap, so labels that need an extra line there are counted. The bare lists
    are IDENTITY-compared against the figure ones: pass the same objects when
    no figures are requested and the drop rungs are skipped. If nothing fits,
    the layout with the smallest estimate wins (ties prefer the earlier rung),
    so the overflow past the metadata block is the least possible."""
    ladder = [(1, entry_lines_1col, False), (2, entry_lines_2col, False)]
    if bare_lines_1col is not entry_lines_1col:
        ladder += [(1, bare_lines_1col, True), (2, bare_lines_2col, True)]
    tried = []
    for cols, entry_lines, dropped in ladder:
        tier = _fit_habitat_legend_tier(entry_lines, n_headings, available_mm,
                                        columns=cols)
        est = _habitat_legend_height_mm(entry_lines, n_headings, tier,
                                        columns=cols)
        if est <= available_mm:
            return cols, tier, est, dropped
        tried.append((cols, tier, est, dropped))
    return min(tried, key=lambda r: r[2])


def _apply_legend_columns(legend, columns):
    """Lay the key out in *columns*, splitting a long layer's entries across
    them. Explicitly restore all column settings for a one-column key so a
    reused/default QGIS legend cannot retain a compressed split layout."""
    try:
        columns = max(1, int(columns))
        legend.setColumnCount(columns)
        legend.setSplitLayer(columns > 1)
        legend.setEqualColumnWidth(columns > 1)
    except Exception:
        pass


def _legend_fit_debug(entry_lines, n_headings, ly_mm, avail_mm, tier, est_h,
                      limit_mm, breakdown=None, cols=1, dropped=False):
    """TEMP: append the legend-fit decision to biome_debug.log so the tier
    calibration can be checked against the real render. Remove once tuned."""
    try:
        import os as _os
        ests = [round(_habitat_legend_height_mm(entry_lines, n_headings, t), 1)
                for t in range(len(_HABITAT_LEGEND_TIERS))]
        line = (f"[legend-fit] entries={len(entry_lines)} lines={entry_lines} "
                f"(sum={sum(entry_lines)}) heads={n_headings} ly={ly_mm:.1f} "
                f"avail={avail_mm:.1f} limit={limit_mm} cols={cols} tier={tier} "
                f"dropped={dropped} est_h={est_h:.1f} per_tier={ests}")
        with open(_os.path.join(_os.path.dirname(__file__), "biome_debug.log"),
                  "a", encoding="utf-8") as f:
            f.write(line + "\n")
            for b in (breakdown or []):
                f.write("    " + b + "\n")
    except Exception:
        pass


def _tag_legend_with_stats(legend, figs, show_area=True, show_units=True,
                           narrow=False):
    """Append each habitat's area + indicative units onto its legend label.

    figs is {sub_habitat_type: {"size", "units", "unit"}} from map_stats. The
    legend's symbol-category nodes carry the sub-habitat name as their display
    label, so we match on that. Nodes with no matching figure are left as-is.
    With *narrow* (a two-column key) the "area · units" pair goes on separate
    lines instead — joined it is wider than the column — matching the extra
    line the height measure counted for that layout.
    """
    try:
        from . import map_stats
        from qgis.core import QgsLayerTreeLayer, QgsMapLayerLegendUtils
        from qgis.PyQt.QtCore import Qt as _Qt
        model = legend.model()
        root  = model.rootGroup()

        # Let the legend honour embedded newlines so the stat figure can sit on
        # its own line beneath the habitat name rather than running off the side.
        try:
            legend.setWrapString("\n")
        except Exception:
            pass

        def _norm(s):
            return " ".join(str(s or "").replace("\n", " ").split()).strip()

        def _strip_key(s):
            for sep in (" – ", " — ", " - "):
                if sep in s:
                    return s.split(sep, 1)[1].strip()
            return s

        def _strip_phase(s):
            # Tree sub_habitat_type values carry a phase prefix (e.g. "Proposed
            # Small Urban Tree") while the legend labels are stripped to "Small
            # Urban Tree", so index both sides without the phase word.
            for ph in ("Existing ", "Proposed ", "Baseline "):
                if s.startswith(ph):
                    return s[len(ph):].strip()
            return s

        # Build a lookup keyed by normalised label. Each figure is indexed under
        # both its exact name and (for trees) its phase-stripped name, summing
        # entries that share a key so e.g. retained + newly-planted trees of one
        # size report a combined figure on each entry.
        def _accum(d, k, e):
            cur = d.get(k)
            if cur is None:
                d[k] = {"size": float(e.get("size", 0.0)),
                        "units": float(e.get("units", 0.0)),
                        "unit": e.get("unit", "ha")}
            else:
                cur["size"]  += float(e.get("size", 0.0))
                cur["units"] += float(e.get("units", 0.0))

        match = {}
        for k, e in figs.items():
            nk = _norm(k)
            _accum(match, nk, e)
            pk = _norm(_strip_phase(nk))
            if pk and pk != nk:
                _accum(match, pk, e)

        def _fmt(e):
            parts = []
            if show_area:
                parts.append(f"{e['size']:,.3f} {e.get('unit', 'ha')}")
            if show_units:
                parts.append(f"{e['units']:,.2f} u")
            return ("\n" if narrow else " · ").join(parts)

        for tree_lyr in root.findLayers():
            if not isinstance(tree_lyr, QgsLayerTreeLayer):
                continue
            nodes = model.layerLegendNodes(tree_lyr)
            changed = False
            for idx, node in enumerate(nodes):
                label = node.data(_Qt.ItemDataRole.DisplayRole) or ""
                norm_label = _norm(label)
                entry = (match.get(norm_label)
                         or match.get(_norm(_strip_key(norm_label)))
                         or match.get(_norm(_strip_phase(norm_label))))
                if not entry:
                    continue
                fig_line = _fmt(entry)
                if not fig_line or "·" in str(label):
                    continue
                wrapped_name = _wrap_legend_label(
                    str(label),
                    max_chars=_LEGEND_WRAP_2COL if narrow else _LEGEND_WRAP_1COL)
                new_label = f"{wrapped_name}\n{fig_line}"
                # Store the override at the layer-tree level so it survives the
                # model's filter-by-map refresh (setting it on the transient node
                # alone does not persist into the rendered legend).
                try:
                    QgsMapLayerLegendUtils.setLegendNodeUserLabel(
                        tree_lyr, idx, new_label)
                    changed = True
                except Exception:
                    node.setUserLabel(new_label)
            if changed:
                try:
                    model.refreshLayerLegend(tree_lyr)
                except Exception:
                    pass
        try:
            legend.adjustBoxSize()
        except Exception:
            pass
    except Exception:
        from qgis.core import QgsMessageLog, Qgis
        import traceback as _tb
        QgsMessageLog.logMessage(
            f"Legend stats tag error:\n{_tb.format_exc()}", "Biome", Qgis.MessageLevel.Warning)


def _fit_to_redline(map_item, project, layers, margin_frac=0.06):
    """Zoom the map item to the redline boundary's extent (plus a small margin),
    reprojected into the map item's CRS. No-op (keeps the canvas extent already
    set) if no 'Redline Boundary' layer is present or it has no features."""
    from qgis.core import QgsRectangle, QgsCoordinateTransform

    redline = None
    for lyr in (layers or []):
        if is_redline_name(lyr.name()):
            redline = lyr
            break
    if redline is None:
        for _redline_name in REDLINE_NAME_ALIASES:
            for lyr in project.mapLayersByName(_redline_name):
                redline = lyr
                break
            if redline is not None:
                break
    if redline is None:
        return False

    try:
        ext = redline.extent()
        if ext.isEmpty():
            return False
        src_crs = redline.crs()
        map_crs = map_item.crs()
        if map_crs.isValid() and src_crs.isValid() and \
                map_crs.authid() != src_crs.authid():
            tr  = QgsCoordinateTransform(src_crs, map_crs, project)
            ext = tr.transformBoundingBox(ext)

        # Pad, then match the map frame's aspect ratio so nothing is clipped.
        mw = max(ext.width(),  1e-6)
        mh = max(ext.height(), 1e-6)
        ext = QgsRectangle(
            ext.xMinimum() - mw * margin_frac,
            ext.yMinimum() - mh * margin_frac,
            ext.xMaximum() + mw * margin_frac,
            ext.yMaximum() + mh * margin_frac,
        )
        frame_ratio = MAP_W_MM / 210.0
        ew, eh = ext.width(), ext.height()
        if ew / eh < frame_ratio:               # too tall — widen
            target_w = eh * frame_ratio
            cx = ext.center().x()
            ext.setXMinimum(cx - target_w / 2.0)
            ext.setXMaximum(cx + target_w / 2.0)
        else:                                    # too wide — heighten
            target_h = ew / frame_ratio
            cy = ext.center().y()
            ext.setYMinimum(cy - target_h / 2.0)
            ext.setYMaximum(cy + target_h / 2.0)

        map_item.zoomToExtent(ext)
        return True
    except Exception:
        return False


def _configure_map_item(map_item, canvas, project, layers):
    """Set extent on the layout map item by reprojecting the canvas extent
    into the map item's existing CRS (EPSG:3857 from the template).

    The map item CRS is set to EPSG:3857 in final_layout.build().
    We must NOT override it — just reproject the canvas extent (BNG) into
    3857 so the tile layers receive valid Web Mercator coordinates.
    """
    canvas_crs = canvas.mapSettings().destinationCrs()
    canvas_ext = canvas.extent()
    map_crs    = map_item.crs()

    if map_crs.isValid() and map_crs.authid() != canvas_crs.authid():
        try:
            tr  = QgsCoordinateTransform(canvas_crs, map_crs, project)
            ext = tr.transformBoundingBox(canvas_ext)
            map_item.zoomToExtent(ext)   # keeps the frame size (setExtent resizes it)
            return
        except Exception:
            pass

    map_item.zoomToExtent(canvas_ext)


# Group-name fragments that mark a layer-tree group as a basemap container.
# OS Zoomstack (offline) is a group of many vector sublayers, unlike the
# single-layer web basemaps, so we must pull in *all* of its visible sublayers.
_BASEMAP_GROUP_HINTS = ("base map", "basemap", "background", "zoomstack",
                        "ordnance survey", "os offline", "os maps",
                        "satellite", "imagery", "aerial")


def _iter_groups(node):
    from qgis.core import QgsLayerTreeGroup
    for child in node.children():
        if isinstance(child, QgsLayerTreeGroup):
            yield child
            for g in _iter_groups(child):
                yield g


def _is_basemap_group(g):
    n = (g.name() or "").lower()
    return any(h in n for h in _BASEMAP_GROUP_HINTS)


def _basemap_context_layers(project):
    """Every visible layer making up the background basemap: all visible
    sublayers of any recognised basemap group (OS Zoomstack, Base Maps, …)
    plus any single named basemap layer, in tree order (top → bottom)."""
    root = project.layerTreeRoot()
    out, seen = [], set()
    for g in _iter_groups(root):
        if _is_basemap_group(g):
            for node in g.findLayers():
                lyr = node.layer()
                if lyr and lyr.id() not in seen and _node_visible_recursive(node):
                    out.append(lyr)
                    seen.add(lyr.id())
    for lyr in project.mapLayers().values():
        if lyr.name() in BASEMAP_NAMES and lyr.id() not in seen:
            node = root.findLayer(lyr.id())
            if node is not None and node.isVisible():
                out.append(lyr)
                seen.add(lyr.id())
    return out


def _is_basemap_layer(lyr):
    """Return True if a layer is a basemap — by known name, or by being inside
    any recognised basemap group (Base Maps, OS Zoomstack, etc.)."""
    if lyr.name() in BASEMAP_NAMES:
        return True
    root = QgsProject.instance().layerTreeRoot()
    for g in _iter_groups(root):
        if _is_basemap_group(g):
            for node in g.findLayers():
                if node.layer() and node.layer().id() == lyr.id():
                    return True
    return False


# Raster name fragments that are NOT drone/aerial background imagery — the same
# exclusions the Georeferencer opacity slider uses (property-boundary rasters).
_NON_GEOREF_RASTER_FRAGMENTS = (
    "property boundaries", "hmlr", "inspire", "land registry",
    "mastermap", "cadastral", "title register",
)


def get_georef_layers(project):
    """User-supplied georeferenced background rasters (e.g. a drone shot).

    Defined exactly as the panel's opacity slider does: a raster layer that is
    NOT one of the plugin's own group layers, NOT a recognised basemap, and NOT
    a property-boundary raster. Returned in layer-tree order (top first)."""
    from qgis.core import QgsRasterLayer
    root = project.layerTreeRoot()

    plugin_ids = set()
    for grp_name in ("Base Maps", "Baseline Habitats", "Proposed Habitats",
                     "Ecological Features"):
        grp = root.findGroup(grp_name)
        if grp is not None:
            for node in grp.findLayers():
                if node.layer() is not None:
                    plugin_ids.add(node.layer().id())

    out = []
    # Walk the layer tree so the result is in the user's stacking order.
    for node in root.findLayers():
        lyr = node.layer()
        if not isinstance(lyr, QgsRasterLayer):
            continue
        if lyr.id() in plugin_ids:
            continue
        if _is_basemap_layer(lyr):
            continue
        name = lyr.name().lower()
        if any(frag in name for frag in _NON_GEOREF_RASTER_FRAGMENTS):
            continue
        if lyr not in out:
            out.append(lyr)
    return out


def _get_visible_basemaps(project):
    """Return all currently visible basemap layers regardless of their name."""
    from qgis.core import QgsRasterLayer
    root = project.layerTreeRoot()
    basemaps = []

    # Check known-name basemaps first
    for lyr in project.mapLayers().values():
        node = root.findLayer(lyr.id())
        if lyr.name() in BASEMAP_NAMES and node is not None and node.isVisible():
            basemaps.append(lyr)

    # Also check anything inside a Base Maps group
    for group_name in ("Base Maps", "Basemaps", "Background"):
        grp = root.findGroup(group_name)
        if grp is not None:
            for node in grp.findLayers():
                lyr = node.layer()
                if lyr and node.isVisible() and lyr not in basemaps:
                    basemaps.append(lyr)

    return basemaps


def _remove_basemap_from_legend(group):
    """Recursively remove basemap layers and empty groups from legend."""
    for child in group.children()[:]:  # copy list since we modify it
        if isinstance(child, QgsLayerTreeLayer):
            if child.layer() and child.layer().name() in BASEMAP_NAMES:
                group.removeChildNode(child)
        elif isinstance(child, QgsLayerTreeGroup):
            if child.name() in ("Base Maps", "Basemaps", "Background"):
                group.removeChildNode(child)
            else:
                _remove_basemap_from_legend(child)
                # Remove group if now empty
                if not child.children():
                    group.removeChildNode(child)


def _strip_map_only_headings(root):
    """Strip a trailing '(Map Only)' from every legend layer-heading, in place.
    Run as the LAST legend step: the per-node rename done while building the
    legend is lost when `_flatten_legend_groups` clones the nodes, and the tier
    restyle can refresh them — so the rename has to happen after all of that."""
    from qgis.core import QgsLayerTreeLayer, QgsLayerTreeGroup
    def _walk(node):
        for ch in node.children():
            if isinstance(ch, QgsLayerTreeLayer):
                nm   = ch.name()
                disp = re.sub(r"\s*\(\s*map only\s*\)\s*$", "", nm,
                              flags=re.IGNORECASE).strip()
                if disp and disp != nm:
                    try:
                        ch.setUseLayerName(False)   # avoid renaming the layer
                    except AttributeError:
                        pass
                    ch.setName(disp)
            elif isinstance(ch, QgsLayerTreeGroup):
                _walk(ch)
    try:
        _walk(root)
    except Exception:
        pass


def _flatten_legend_groups(root):
    """Promote all QgsLayerTreeLayer nodes to the root, removing all group
    wrapper nodes.  This eliminates 'Ecological Features', 'Point Features'
    etc. heading text from the legend, leaving only the layer symbol entries."""
    # Collect all layer nodes depth-first
    layer_nodes = []

    def _collect(node):
        if isinstance(node, QgsLayerTreeLayer):
            layer_nodes.append(node.clone())
        elif isinstance(node, QgsLayerTreeGroup):
            for child in node.children():
                _collect(child)

    for child in root.children():
        _collect(child)

    # Clear the root
    for child in root.children()[:]:
        root.removeChildNode(child)

    # Re-add as flat list
    for node in layer_nodes:
        root.addChildNode(node)


# ── Layer helpers ─────────────────────────────────────────────────────────────

def _get_layers(project, prefix):
    """Return layers for a habitat map in correct render order.

    Layers are collected by walking the layer tree group so their order
    matches the panel exactly: Trees (top) → Hedgerow → Watercourse → Area.
    Within QgsLayoutItemMap, the first item in the list renders ON TOP, so
    we must walk the tree top-to-bottom and pass the list as-is.
    """
    root = project.layerTreeRoot()
    group_name = prefix + " Habitats"   # "Baseline Habitats" or "Proposed Habitats"
    habitat = []
    redline = []
    basemap = []

    # Walk the habitat group recursively (handles sub-groups)
    group = root.findGroup(group_name)
    if group is not None:
        def _collect_from_group(node):
            if isinstance(node, QgsLayerTreeLayer):
                lyr = node.layer()
                if lyr:
                    habitat.append(lyr)
            elif isinstance(node, QgsLayerTreeGroup):
                for child in node.children():
                    _collect_from_group(child)
        for child in group.children():
            _collect_from_group(child)

    # Fallback: if group not found, collect by name prefix in tree order
    if not habitat:
        def _walk(node):
            if isinstance(node, QgsLayerTreeLayer):
                lyr = node.layer()
                if lyr and lyr.name().startswith(prefix + " -"):
                    habitat.append(lyr)
            elif isinstance(node, QgsLayerTreeGroup):
                for child in node.children():
                    _walk(child)
        _walk(root)

    # Redline and basemap collected separately
    for lyr in project.mapLayers().values():
        n    = lyr.name()
        node = root.findLayer(lyr.id())
        if is_redline_name(n):
            redline.append(lyr)
        elif n == "Blue Line Boundary":
            redline.append(lyr)   # grouped with redline so it renders above habitat

    basemap = _get_visible_basemaps(project)
    return redline + habitat + basemap


def _order_layers(layers):
    """Sort a mixed layer list into render order for the layout map item.

    Layout map item renders index-0 ON TOP. Point and linear habitats sit ABOVE
    the boundary lines so features on the redline aren't sliced by it; area
    habitats sit BELOW the redline so the boundary stays crisp over the fills:
      Trees / points        (index 0 — on top)
      Hedgerow / lines
      Watercourse / lines
      Redline Boundary
      Blue Line Boundary
      Area habitats
      Basemap               (index last — at the bottom)

    Habitat layers are identified by name token; the legend still lists the
    redline first regardless of this order — see _redline_first.
    """
    # Habitats rendered ABOVE the boundary lines (points + linears), in this
    # top-to-bottom order. Area habitats — and anything unrecognised — render
    # BELOW the boundary so the redline reads cleanly over the fills.
    _ABOVE = ["trees", "hedgerow", "water"]

    def _above_key(lyr):
        n = lyr.name().lower()
        for i, token in enumerate(_ABOVE):
            if token in n:
                return i
        return None

    bm  = [l for l in layers if l.name() in BASEMAP_NAMES]
    rl  = [l for l in layers if is_redline_name(l.name())]
    bl  = [l for l in layers if l.name() == "Blue Line Boundary"]
    hab = [l for l in layers if l not in bm and l not in rl and l not in bl]

    above = sorted((l for l in hab if _above_key(l) is not None), key=_above_key)
    below = [l for l in hab if _above_key(l) is None]   # area habitats + others

    # Render order (index-0 on top)
    return above + rl + bl + below + bm


def _order_bt_layers(layers):
    """Render order for the building/tree maps. Index-0 renders ON TOP, so —
    unlike the habitat maps — the boundary lines sit UNDER the buildings and
    trees, so features placed on the boundary aren't hidden beneath the redline:
      Trees (points)        (index 0 — on top)
      Buildings (polygons)
      Redline Boundary
      Blue Line Boundary
      Availability areas    (area wash — the redline stays crisp above it)
      Basemap               (index last — at the bottom)
    The legend still lists the redline first — see _redline_first."""
    bm    = [l for l in layers if l.name() in BASEMAP_NAMES]
    rl    = [l for l in layers if is_redline_name(l.name())]
    bl    = [l for l in layers if l.name() == "Blue Line Boundary"]
    rest  = [l for l in layers if l not in bm and l not in rl and l not in bl]
    avail = [l for l in rest if "availability" in l.name().lower()]
    trees = [l for l in rest if l not in avail and "tree" in l.name().lower()]
    bldgs = [l for l in rest if l not in avail and l not in trees]
    return trees + bldgs + rl + bl + avail + bm


def _order_eco_layers(layers):
    """Render order for the ecological maps (PRF, Enhancement, Emergence): every
    ecological feature renders ABOVE the boundary lines so nothing on the redline
    is hidden; only the basemap stays at the bottom. Features are stacked points
    → lines → polygons so small markers are never buried under a wedge or fill.
    (Legend still lists the redline first — see _redline_first.)
      Eco points            (index 0 — on top)
      Eco lines
      Eco polygons
      Redline Boundary
      Blue Line Boundary
      Basemap               (index last — at the bottom)
    """
    bm   = [l for l in layers if l.name() in BASEMAP_NAMES]
    rl   = [l for l in layers if is_redline_name(l.name())]
    bl   = [l for l in layers if l.name() == "Blue Line Boundary"]
    rest = [l for l in layers if l not in bm and l not in rl and l not in bl]

    def _geom_key(lyr):
        try:
            return int(lyr.geometryType())   # 0 point, 1 line, 2 polygon
        except Exception:
            return 3
    rest_sorted = sorted(rest, key=_geom_key)   # stable: keeps order within a type
    return rest_sorted + rl + bl + bm


def _redline_first(layers):
    """Pin the boundary pair to the top of every printed legend.

    The Blue Line Boundary always sits immediately below the Redline Boundary,
    independent of either layer's map-render position.
    """
    rl   = [l for l in layers if is_redline_name(l.name())]
    bl   = [l for l in layers if l.name() == "Blue Line Boundary"]
    rest = [l for l in layers
            if not is_redline_name(l.name()) and l.name() != "Blue Line Boundary"]
    return rl + bl + rest


def _emergence_legend_order(layers):
    """Legend order for the Emergence Survey Layout, independent of render order
    (the survey markers still draw on top): the printed key lists the redline
    first (when present), then the Building & Tree Mapper context layers, then
    the survey-position features and their field-of-view wedges. Trees list
    above buildings, mirroring the building/tree map."""
    from .building_tree_mapper import BUILDING_LAYER_NAME, TREE_LAYER_NAME
    bt_names = {TREE_LAYER_NAME, BUILDING_LAYER_NAME}
    rl    = [l for l in layers if is_redline_name(l.name())]
    bl    = [l for l in layers if l.name() == "Blue Line Boundary"]
    trees = [l for l in layers if l.name() == TREE_LAYER_NAME]
    bldgs = [l for l in layers if l.name() == BUILDING_LAYER_NAME]
    rest  = [l for l in layers
             if not is_redline_name(l.name()) and l.name() != "Blue Line Boundary"
             and l.name() not in bt_names]
    return rl + bl + trees + bldgs + rest


def _pra_legend_order(layers):
    """Legend order for the PRA map, independent of render order (the PRFs still
    draw on top). The printed key reads outside-in — redline, then the mapped
    buildings and trees the features sit on, then the roost voids / runs / point
    PRFs, which is how a reader works through the figure."""
    from .building_tree_mapper import BUILDING_LAYER_NAME, TREE_LAYER_NAME
    order = ["Redline Boundary", "Blue Line Boundary",
             BUILDING_LAYER_NAME, TREE_LAYER_NAME,
             ECO_AREA_LAYER_NAME, "Ecological Linear Features",
             "Ecological Features"]
    ranked = {name: i for i, name in enumerate(order)}
    return sorted(layers, key=lambda l: ranked.get(l.name(), len(order)))


def _get_pra_layers(project):
    """Return (layers, restore_map) for the PRA (Preliminary Roost Assessment)
    map: the potential-roost-feature points, the runs mapped as lines (hanging
    tiles, eaves gaps), the loft/void outlines and the suggested surveyor
    position arrows — plus any Building & Tree Mapper features as on-site
    context. Everything else ecological (habitats, species records, enhancements)
    is filtered out by a temporary subset string, restored after the build."""
    from .gridref_dialog import (PRA_POINT_FTYPES, PRA_LINE_FTYPES,
                                 PRA_AREA_FTYPES)

    def _subset(ftypes):
        return '"ftype" IN (%s)' % ", ".join(f"'{f}'" for f in sorted(ftypes))

    wanted = {
        "Ecological Features":        _subset(PRA_POINT_FTYPES),
        "Ecological Linear Features": _subset(PRA_LINE_FTYPES),
        ECO_AREA_LAYER_NAME:          _subset(PRA_AREA_FTYPES),
    }

    result_layers = []
    restore_map   = {}
    redline       = []
    found         = {}

    for lyr in project.mapLayers().values():
        n = lyr.name()
        if is_redline_name(n) or n == "Blue Line Boundary":
            redline.append(lyr)
        elif _is_basemap_layer(lyr):
            pass  # collected separately below
        elif n in wanted and isinstance(lyr, QgsVectorLayer):
            found[n] = lyr

    # Points render above lines, above the void outlines (index 0 = top).
    for n in ("Ecological Features", "Ecological Linear Features",
              ECO_AREA_LAYER_NAME):
        lyr = found.get(n)
        if lyr is None:
            continue
        restore_map[lyr] = lyr.subsetString()
        lyr.setSubsetString(wanted[n])
        result_layers.append(lyr)

    # Building & Tree Mapper context, included only when actually populated so an
    # empty layer leaves no stray legend heading (mirrors the emergence layout).
    from .building_tree_mapper import BUILDING_LAYER_NAME, TREE_LAYER_NAME
    for nm in (TREE_LAYER_NAME, BUILDING_LAYER_NAME):
        for lyr in project.mapLayersByName(nm):
            if isinstance(lyr, QgsVectorLayer) and lyr.featureCount() > 0:
                result_layers.append(lyr)
            break

    return redline + result_layers + _get_visible_basemaps(project), restore_map


def _get_bt_layers(project, layer_names):
    """Layers for the Building / Tree / combined maps: the mapper layer(s) plus
    redline, blue line and basemaps for context (redline/blue line trimmed later
    by the include flags). `layer_names` may be a single name or a list."""
    if isinstance(layer_names, str):
        layer_names = [layer_names]
    layers = []
    for nm in layer_names:
        for l in project.mapLayersByName(nm):
            if nm in {
                "Tree Buffers (Map Only)",
                "Tree RPAs (Map Only)",
                "Construction Exclusion Zones (Map Only)",
                "Restricted Activity Zones (Map Only)",
            }:
                node = project.layerTreeRoot().findLayer(l.id())
                if node is not None and not node.isVisible():
                    break
            layers.append(l)
            break
    for name in (*REDLINE_NAME_ALIASES, "Blue Line Boundary"):
        for l in project.mapLayersByName(name):
            if l not in layers:
                layers.append(l)
            break
    layers += _get_visible_basemaps(project)
    return layers


def _get_visible_layers(project):
    """Return all currently visible layers in render order (top → bottom).
    Uses canvas.layers() — the definitive list of what QGIS is actually
    rendering — then splits basemaps to the bottom."""
    from qgis.utils import iface
    non_basemap = []
    basemap     = []
    seen        = set()
    for lyr in iface.mapCanvas().layers():
        if lyr is None or lyr.id() in seen:
            continue
        seen.add(lyr.id())
        if _is_basemap_layer(lyr):
            basemap.append(lyr)
        else:
            non_basemap.append(lyr)
    return non_basemap + basemap


def _get_eco_map_layers(project, ftypes: set):
    """Return (layers, restore_map) for specialist ecological maps."""
    root = project.layerTreeRoot()
    result_layers = []
    restore_map = {}
    redline, basemap = [], []

    ftype_list = ", ".join(f"'{f}'" for f in sorted(ftypes))
    subset     = f'"ftype" IN ({ftype_list})'

    for lyr in project.mapLayers().values():
        n    = lyr.name()
        node = root.findLayer(lyr.id())
        if is_redline_name(n):
            redline.append(lyr)
        elif n == "Blue Line Boundary":
            redline.append(lyr)   # render above habitat, below redline
        elif _is_basemap_layer(lyr):
            pass  # collected separately below
        elif n in ECO_LAYER_NAMES and isinstance(lyr, QgsVectorLayer):
            restore_map[lyr] = lyr.subsetString()
            lyr.setSubsetString(subset)
            result_layers.append(lyr)

    basemap = _get_visible_basemaps(project)
    return redline + result_layers + basemap, restore_map


def _get_emergence_layers(project):
    """Return (layers, restore_map) for the Emergence Survey Layout.

    Shows the survey-position features — the point markers (Surveyor,
    Observer Station, Camera, Static Bat Detector) from "Ecological Features"
    and their field-of-view wedges from "Ecological Area Features" — plus any
    buildings and trees placed with the Building & Tree Mapper, included as
    on-site context (they carry no subset filter, so need no restore).

    Other ecological features (species records, field signs, enhancements,
    habitats) are deliberately excluded. The basemap currently on the canvas
    is included for context but is hidden from the legend by the caller.
    """
    root = project.layerTreeRoot()
    result_layers    = []
    restore_map      = {}
    renderer_restore = {}
    redline          = []

    point_list   = ", ".join(f"'{f}'" for f in sorted(EMERGENCE_POINT_FTYPES))
    point_subset = f'"ftype" IN ({point_list})'
    area_list    = ", ".join(f"'{f}'" for f in sorted(EMERGENCE_AREA_FTYPES))
    area_subset  = f'"ftype" IN ({area_list})'

    point_lyr = None
    area_lyr  = None
    for lyr in project.mapLayers().values():
        n = lyr.name()
        if is_redline_name(n):
            redline.append(lyr)
        elif n == "Blue Line Boundary":
            redline.append(lyr)   # render above features, below redline
        elif _is_basemap_layer(lyr):
            pass  # collected separately below
        elif n == "Ecological Features" and isinstance(lyr, QgsVectorLayer):
            point_lyr = lyr
        elif n == ECO_AREA_LAYER_NAME and isinstance(lyr, QgsVectorLayer):
            area_lyr = lyr

    # Apply temporary subset filters (restored after the layout is built).
    # Point markers render ON TOP of their field-of-view wedges, so the
    # point layer is added first (index 0 = top in a layout map item).
    if point_lyr is not None:
        restore_map[point_lyr] = point_lyr.subsetString()
        point_lyr.setSubsetString(point_subset)
        result_layers.append(point_lyr)
    if area_lyr is not None:
        restore_map[area_lyr] = area_lyr.subsetString()
        area_lyr.setSubsetString(area_subset)
        result_layers.append(area_lyr)
        # NB: a per-cone legend renderer used to be swapped in here to list each
        # cone ("Camera-01") separately, but it is restored before the layout
        # renders — so the export always showed the per-TYPE entries ("Camera
        # Field of View") anyway, while the swap made the legend auto-fitter count
        # every cone (9+) and crush the font to fit. Disabled: the per-type key is
        # what actually renders, and the measurement now matches it. (Re-enable
        # only alongside a fix that keeps the per-cone renderer through render.)

    # Building & Tree Mapper context layers (illustration only — never filtered
    # and excluded from the BNG system). Include each only when it actually
    # holds mapped features, so a layer that was created then emptied leaves no
    # stray legend heading. Geometry sorting (see _order_eco_layers) renders the
    # tree points alongside the survey markers and the building polygons beneath
    # the field-of-view wedges; the legend order is handled separately so they
    # still list above the survey positions.
    from .building_tree_mapper import BUILDING_LAYER_NAME, TREE_LAYER_NAME
    for nm in (TREE_LAYER_NAME, BUILDING_LAYER_NAME):
        for lyr in project.mapLayersByName(nm):
            if isinstance(lyr, QgsVectorLayer) and lyr.featureCount() > 0:
                result_layers.append(lyr)
            break

    basemap = _get_visible_basemaps(project)
    return redline + result_layers + basemap, restore_map, renderer_restore


def _restore_subset_strings(restore_map: dict):
    """Restore subset strings on eco layers after the layout has been built."""
    for lyr, old_subset in restore_map.items():
        try:
            lyr.setSubsetString(old_subset)
        except Exception:
            pass


def _restore_renderers(renderer_restore: dict):
    """Put back renderers temporarily swapped for the layout build (the
    Emergence Survey Layout's per-cone field-of-view legend renderer)."""
    for lyr, rend in renderer_restore.items():
        if rend is None:
            continue
        try:
            lyr.setRenderer(rend.clone())
            lyr.triggerRepaint()
        except Exception:
            pass


def _cone_legend_sort_key(feat):
    """Order cones for the legend by type then number (Camera-01, Camera-02, …,
    Surveyor-01), falling back to a plain label / ftype sort."""
    try:
        label = (feat.attribute("label") or "").strip()
    except Exception:
        label = ""
    m = re.match(r"^(.*?)[-\s]?(\d+)$", label)
    if m:
        return (m.group(1).strip().lower(), int(m.group(2)), "")
    try:
        ftype = feat.attribute("ftype") or ""
    except Exception:
        ftype = ""
    return (str(ftype).lower(), 0, label.lower())


def _recolour_cone_symbol(symbol, hex_colour):
    """Bake a single cone's colour into a cloned per-type wedge symbol so the
    legend swatch shows that cone's actual colour. Clears the data-defined
    fill/stroke overrides (which fall back to the type default in a feature-less
    legend swatch), then sets the fill — keeping its opacity — and a darker
    stroke to the cone's palette colour. A blank colour leaves the type default
    in place (legacy and custom cones that carry no fov_color)."""
    from qgis.core import QgsSimpleFillSymbolLayer, QgsProperty, QgsSymbolLayer
    from qgis.PyQt.QtGui import QColor
    for i in range(symbol.symbolLayerCount()):
        sl = symbol.symbolLayer(i)
        if not isinstance(sl, QgsSimpleFillSymbolLayer):
            continue  # leave the apex station-icon (geometry generator) alone
        try:
            sl.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyFillColor, QgsProperty())
            sl.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeColor, QgsProperty())
        except Exception:
            pass
        if hex_colour:
            fill = QColor(hex_colour)
            if fill.isValid():
                a = sl.fillColor().alphaF()
                fill.setAlphaF(a if a > 0 else 0.18)
                sl.setFillColor(fill)
                sl.setStrokeColor(QColor(hex_colour).darker(160))
        break


def _apply_cone_legend_renderer(area_lyr):
    """Give the field-of-view-wedge layer a renderer that lists EACH placed cone
    as its own legend entry — its palette colour + on-map label ("Camera-01") —
    so the Emergence Survey Layout key reads as a roster of survey positions
    rather than one swatch per type. Returns the original renderer (cloned) for
    restoration after the layout is built, or None if nothing was swapped.

    Categorises by the `label` field (one category per cone; the category value
    equals its display label so the legend-fit measurement still matches) and
    clones each cone's per-type symbol so the apex station icon and cone opacity
    survive, then bakes the feature's own colour in statically. Call AFTER the
    area subset is applied so only the survey-position cones are listed."""
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory
    base = area_lyr.renderer()
    if not isinstance(base, QgsCategorizedSymbolRenderer):
        return None
    flds = area_lyr.fields()
    if flds.indexOf("label") < 0:
        return None
    has_colour = flds.indexOf("fov_color") >= 0
    # Per-type template symbols carry the apex station icon + the cone opacity.
    # CLONE each immediately: base.categories() hands back temporary category
    # copies that own (and free) their symbols, so a borrowed symbol() pointer
    # dangles once the list is gone — cloning later then crashes (access
    # violation). The clone is Python-owned and lives until this function ends.
    tmpl = {}
    for cat in base.categories():
        sym = cat.symbol()
        if sym is not None:
            tmpl[cat.value()] = sym.clone()
    if not tmpl:
        return None
    any_sym = next(iter(tmpl.values()))

    cats, seen = [], set()
    for f in sorted(area_lyr.getFeatures(), key=_cone_legend_sort_key):
        label = (f.attribute("label") or "").strip()
        if label in seen:           # duplicate / blank label → one row only
            continue
        seen.add(label)
        ftype  = f.attribute("ftype")
        colour = (f.attribute("fov_color") or "").strip() if has_colour else ""
        sym = tmpl.get(ftype, any_sym).clone()
        _recolour_cone_symbol(sym, colour)
        cats.append(QgsRendererCategory(label, sym, label))
    if not cats:
        return None
    original = base.clone()
    area_lyr.setRenderer(QgsCategorizedSymbolRenderer("label", cats))
    return original


def _wrap_legend_label(text, max_chars=24):
    """Wrap a legend label at word boundaries so it fits inside the legend panel.

    Lines longer than max_chars are broken at the last space before the limit.
    The wrapped lines are joined with the HTML line-break that QGIS legend labels
    honour when the label is treated as plain text with embedded newlines.
    Labels that already contain a line break are returned unchanged.
    """
    if "\n" in text or len(text) <= max_chars:
        return text
    words = text.split(" ")
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def _fit_chars(point_size, usable_mm):
    """Approximate how many characters of *point_size* text fit in *usable_mm*
    of legend width (average glyph width ≈ 0.6 × point size)."""
    char_mm = point_size * 0.3528 * 0.6
    return max(12, int(usable_mm / char_mm))


def _extent_request(layer, map_item):
    """A QgsFeatureRequest limited to *map_item*'s extent (transformed into the
    layer's CRS), so a headless count matches what filter-by-map will render.
    Returns an unfiltered request when no map item / on any error."""
    from qgis.core import QgsFeatureRequest
    req = QgsFeatureRequest()
    if map_item is None:
        return req
    try:
        from qgis.core import QgsCoordinateTransform, QgsProject
        ext  = map_item.extent()
        mcrs = map_item.crs()
        if mcrs != layer.crs():
            tr  = QgsCoordinateTransform(mcrs, layer.crs(), QgsProject.instance())
            ext = tr.transformBoundingBox(ext)
        req.setFilterRect(ext)
    except Exception:
        pass
    return req


def _category_values_present(layer, map_item=None):
    """Return the set of category field values that have at least one feature.
    When *map_item* is given, only features inside its extent count — matching
    the legend's filter-by-map, so the auto-fitter doesn't size for off-map
    entries that never render.

    Works for QgsCategorizedSymbolRenderer layers.  Returns None for other
    renderer types (caller should not filter those layers).
    """
    from qgis.core import QgsCategorizedSymbolRenderer
    renderer = layer.renderer()
    if not isinstance(renderer, QgsCategorizedSymbolRenderer):
        return None
    field = renderer.classAttribute()
    present = set()
    for feat in layer.getFeatures(_extent_request(layer, map_item)):
        val = feat[field]
        present.add(val if val is not None else "")
    return present


def _category_values_active(layer):
    """Category field values whose category is CHECKED (not turned off by the
    user in the Layers panel) — a categorized renderer tracks this per category
    via renderState(). Returns None for non-categorized layers (no filtering).
    The rendered map already respects renderState; this lets the legend match."""
    from qgis.core import QgsCategorizedSymbolRenderer
    renderer = layer.renderer()
    if not isinstance(renderer, QgsCategorizedSymbolRenderer):
        return None
    active = set()
    for cat in renderer.categories():
        try:
            on = cat.renderState()
        except Exception:
            on = True
        if on:
            active.add(cat.value() if cat.value() is not None else "")
    return active


def _apply_legend_style(legend_item, root, model, specific_lyrs, support_lyrs,
                        map_type, force_small=False):
    """Apply styling, filter empty categories, wrap labels, and strip suffixes.
    Shared by both the Pond Map manual path and the prune path for other types.
    Pass force_small=True to drop straight to the most compact size tier (used
    when a first pass produced a legend too tall for the sidebar).
    """
    from qgis.core import QgsMapLayerLegendUtils, QgsLayerTreeGroup

    legend_item.setWrapString("\n")

    total_nodes = sum(1 for _ in root.findLayers())
    if force_small or total_nodes > 25:
        label_pt, sym_mm, space_mm = 5.5, 3.5, 0.5
        grp_pt, sub_pt = 7.0, 6.0
    elif total_nodes > 15:
        label_pt, sym_mm, space_mm = 6.5, 4.0, 0.8
        grp_pt, sub_pt = 8.0, 7.0
    else:
        label_pt, sym_mm, space_mm = 7.0, 5.0, 1.5
        grp_pt, sub_pt = 9.0, 7.5

    body_style = legend_item.style(QgsLegendStyle.Style.SymbolLabel)
    body_font  = body_style.font()
    body_font.setPointSizeF(label_pt)
    body_font.setFamily("Inter")
    body_style.setFont(body_font)
    legend_item.setStyle(QgsLegendStyle.Style.SymbolLabel, body_style)
    # Group / subgroup headings scale with the same tier so long headings
    # like "Priority Habitat - Ancient Woodland" stay inside the sidebar.
    for style_key, pt in ((QgsLegendStyle.Style.Group, grp_pt),
                          (QgsLegendStyle.Style.Subgroup, sub_pt)):
        st = legend_item.style(style_key)
        f  = st.font()
        f.setPointSizeF(pt)
        f.setFamily("Inter")
        st.setFont(f)
        legend_item.setStyle(style_key, st)
    legend_item.setSymbolHeight(sym_mm)
    legend_item.setSymbolWidth(sym_mm)
    # Point-marker icons ignore setSymbolHeight (it only sizes fill/line
    # swatches — same defect the PRA legend hit), so the compact tiers must
    # also cap the symbol size or icon-heavy keys don't compress at all.
    if force_small or total_nodes > 15:
        try:
            legend_item.setMaximumSymbolSize(sym_mm)
        except Exception:
            pass
    try:
        legend_item.setBoxSpace(space_mm)
        legend_item.setSymbolSpace(space_mm)
    except Exception:
        pass

    # Wrap widths derived from the fonts actually in use. The sidebar gap is
    # ~62 mm wide; symbol labels lose ~12 mm to indent + swatch, layer-name
    # headings ~6 mm, group headings ~4 mm.
    lab_chars = _fit_chars(label_pt, 48.0)
    sub_chars = _fit_chars(sub_pt, 54.0)
    grp_chars = _fit_chars(grp_pt, 56.0)

    # Per-layer: filter empty categories and wrap long labels. Skipped on a
    # force_small restyle pass — filtering/collapsing already ran, and user
    # labels set by the collapse step would no longer match category values.
    for lyr in ([] if force_small else specific_lyrs):
        tree_lyr = root.findLayer(lyr.id())
        if tree_lyr is None:
            continue
        present_vals = _category_values_present(lyr)
        active_vals  = _category_values_active(lyr)
        legend_nodes = model.layerLegendNodes(tree_lyr)
        # A categorized renderer's display label is not necessarily its stored
        # value. EPSL icons, for example, display "Hazel dormouse" while the
        # feature/category value is ``hazel_dormouse``. Comparing the label to
        # the feature values hid every new EPSL icon from the print legend.
        # Legend nodes follow the renderer category order at this point, so use
        # the real category value for presence/render-state filtering and keep
        # the friendly label solely for display.
        renderer_categories = None
        if present_vals is not None:
            try:
                from qgis.core import QgsCategorizedSymbolRenderer
                renderer = lyr.renderer()
                if isinstance(renderer, QgsCategorizedSymbolRenderer):
                    renderer_categories = renderer.categories()
            except Exception:
                renderer_categories = None
        # Filter categories that are empty OR turned off by the user FIRST:
        # refreshLayerLegend rebuilds the legend nodes and discards any user
        # labels already set on them, so the label wrapping below must run on the
        # refreshed nodes — wrapping before filtering left long labels unwrapped
        # and overflowing the sidebar.
        if present_vals is not None:
            hidden_indices = []
            for idx, node in enumerate(legend_nodes):
                label = node.data(Qt.ItemDataRole.DisplayRole) or ""
                if renderer_categories is not None and idx < len(renderer_categories):
                    value = renderer_categories[idx].value()
                    cat_val = value if value is not None else ""
                else:
                    cat_val = "" if label in ("Other", lyr.name()) else label
                empty = cat_val not in present_vals
                off   = active_vals is not None and cat_val not in active_vals
                if empty or off:
                    hidden_indices.append(idx)
            if hidden_indices:
                visible_indices = [i for i in range(len(legend_nodes))
                                   if i not in hidden_indices]
                QgsMapLayerLegendUtils.setLegendNodeOrder(tree_lyr, visible_indices)
                model.refreshLayerLegend(tree_lyr)
                legend_nodes = model.layerLegendNodes(tree_lyr)
        for node in legend_nodes:
            label = node.data(Qt.ItemDataRole.DisplayRole) or ""
            wrapped = _wrap_legend_label(label, lab_chars)
            if wrapped != label:
                node.setUserLabel(wrapped)

    # Collapse: >5 entries in one layer → single summary row
    GROUP_THRESHOLD = 5
    if (not force_small
            and map_type in ("Designated Sites Map", "Priority Habitats Map")):
        noun = "sites" if map_type == "Designated Sites Map" else "habitats"
        for lyr in specific_lyrs:
            # Constraint-buffer layers carry named distance bands, not sites —
            # never collapse them into an "N sites" summary.
            if lyr.name().startswith("Buffer - "):
                continue
            tree_lyr = root.findLayer(lyr.id())
            if tree_lyr is None:
                continue
            legend_nodes = model.layerLegendNodes(tree_lyr)
            if len(legend_nodes) <= GROUP_THRESHOLD:
                continue
            # Strip "Priority Habitat - " prefix for a cleaner label
            display_name = lyr.name()
            if display_name.startswith("Priority Habitat - "):
                display_name = display_name[len("Priority Habitat - "):]
            collapsed_label = f"{display_name} ({len(legend_nodes)} {noun})"
            QgsMapLayerLegendUtils.setLegendNodeOrder(tree_lyr, [0])
            model.refreshLayerLegend(tree_lyr)
            remaining = model.layerLegendNodes(tree_lyr)
            if remaining:
                remaining[0].setUserLabel(_wrap_legend_label(collapsed_label,
                                                             lab_chars))

    # Wrap support layer labels
    for lyr in support_lyrs:
        tree_lyr = root.findLayer(lyr.id())
        if tree_lyr is None:
            continue
        for node in model.layerLegendNodes(tree_lyr):
            label = node.data(Qt.ItemDataRole.DisplayRole) or ""
            wrapped = _wrap_legend_label(label, lab_chars)
            if wrapped != label:
                node.setUserLabel(wrapped)

    # Strip "(2 km)" suffixes from layer names, then wrap any layer-name
    # heading that would still overrun the sidebar.
    from qgis.core import QgsLegendRenderer
    for node in root.findLayers():
        # EPSL: the panel title already names the dataset, so hide the
        # "EPSL Locations" layer heading entirely and let the species symbols
        # stand alone. Hidden drops the heading text but keeps the symbol /
        # category swatches beneath it.
        if (map_type == "EPSL Map" and node.layer()
                and node.layer().name() == _EPSL_LAYER_NAME):
            QgsLegendRenderer.setNodeLegendStyle(node, QgsLegendStyle.Style.Hidden)
            continue
        lyr_name = node.name() or ""
        cleaned = (lyr_name.replace(" (2 km)", "")
                           .replace(" (2km)", "").strip())
        wrapped = _wrap_legend_label(cleaned, sub_chars)
        if wrapped != lyr_name:
            # Detach the node label from the layer first — with the default
            # useLayerName=True, setName() writes through to the project
            # layer even on the legend's cloned tree. That leak renamed
            # "Priority Habitat - Ancient Woodland" with an embedded newline,
            # which broke every later name-based lookup/dedupe and stacked a
            # duplicate copy of the layer on the next desk-study run.
            try:
                node.setUseLayerName(False)
            except AttributeError:
                pass
            node.setName(wrapped)

    # Wrap group headings the same way (recursively).
    def _wrap_groups(group):
        for child in group.children():
            if isinstance(child, QgsLayerTreeGroup):
                name = child.name() or ""
                wrapped = _wrap_legend_label(name, grp_chars)
                if wrapped != name:
                    child.setName(wrapped)
                _wrap_groups(child)
    _wrap_groups(root)


def _desk_legend_two_columns(legend, col_w_mm):
    """Split a desk-study key into two columns — used when even the most
    compact font tier overruns the sidebar in one column (e.g. a Pond Map with
    15+ water bodies). Entry labels, layer names and group headings are all
    re-wrapped for the narrower column first so the split doesn't push the key
    wider than the sidebar. Runs after the force_small pass, so the wrap
    widths assume that tier's fonts."""
    label_pt, sub_pt, grp_pt = 5.5, 6.0, 7.0   # force_small tier
    lab_chars = _fit_chars(label_pt, max(14.0, col_w_mm - 9.0))
    sub_chars = _fit_chars(sub_pt,   max(16.0, col_w_mm - 4.0))
    grp_chars = _fit_chars(grp_pt,   max(16.0, col_w_mm - 2.0))

    model = legend.model()
    root  = model.rootGroup()
    for tree_lyr in root.findLayers():
        for node in model.layerLegendNodes(tree_lyr):
            label   = node.data(Qt.ItemDataRole.DisplayRole) or ""
            wrapped = _wrap_legend_label(_collapse_ws(label), lab_chars)
            if wrapped != label:
                node.setUserLabel(wrapped)
        name    = tree_lyr.name() or ""
        wrapped = _wrap_legend_label(_collapse_ws(name), sub_chars)
        if wrapped != name:
            try:
                tree_lyr.setUseLayerName(False)
            except AttributeError:
                pass
            tree_lyr.setName(wrapped)

    def _wrap_groups(group):
        for child in group.children():
            if isinstance(child, QgsLayerTreeGroup):
                name    = child.name() or ""
                wrapped = _wrap_legend_label(_collapse_ws(name), grp_chars)
                if wrapped != name:
                    child.setName(wrapped)
                _wrap_groups(child)
    _wrap_groups(root)

    legend.setColumnCount(2)
    legend.setSplitLayer(True)
    legend.setEqualColumnWidth(True)


def _pond_map_label_override(map_item, layers):
    """Exported Pond Maps label each pond with just its ID — the sidebar list
    already carries the distances, so "SWB-02 (35 m)" on the map said it
    twice. Done as a layout-map style override: the LIVE canvas deliberately
    keeps the full ID + distance label, only the print item renders the
    trimmed one."""
    try:
        from qgis.core import QgsMapLayerStyle
        lyr = next((l for l in layers if l.name() == _POND_LAYER_NAME), None)
        if lyr is None or lyr.labeling() is None:
            return
        original = lyr.labeling().clone()
        try:
            trimmed = lyr.labeling().clone()
            pal = trimmed.settings()
            if lyr.fields().indexOf("gcn_status") >= 0:
                pal.fieldName = (
                    "concat(\"pond_id\", CASE WHEN \"gcn_status\" IN "
                    "('P','A','NS') THEN concat(' [', \"gcn_status\", ']') "
                    "ELSE '' END)")
                pal.isExpression = True
            else:
                pal.fieldName = "pond_id"
                pal.isExpression = False
            trimmed.setSettings(pal)
            # Momentary swap purely to capture the style snapshot; the
            # canvas labeling is restored in the finally below.
            lyr.setLabeling(trimmed)
            override = QgsMapLayerStyle()
            override.readFromLayer(lyr)
        finally:
            lyr.setLabeling(original)
        ov = dict(map_item.layerStyleOverrides() or {})
        ov[lyr.id()] = override.xmlData()
        map_item.setLayerStyleOverrides(ov)
        map_item.setKeepLayerStyles(True)
    except Exception:
        QgsMessageLog.logMessage(
            f"Pond label override error:\n{traceback.format_exc()}",
            "Biome", Qgis.MessageLevel.Warning)


def _add_pond_list_label(layout, leg_rect, top_mm, limit_mm, theme,
                         include_status=False):
    """Render the per-pond distance list ("SWB-01 (140 m)" …) as plain text
    beneath the Pond Map legend. The legend itself shows the water-body icon
    ONCE with a count; this list carries the detail, so a pond-rich site no
    longer repeats an identical swatch 18+ times down the sidebar.

    Fits by dropping through font sizes, then splitting into two columns, and
    as a last resort truncating with a "+ N more" line (every pond stays
    individually labelled on the map itself). Plain-text labels only — the
    HTML label mode depends on WebKit, which QGIS 4/Qt6 does not ship.
    Returns the list's bottom edge (page mm), or None if there are no ponds."""
    import math
    from qgis.core import (QgsProject, QgsLayoutItemLabel, QgsLayoutPoint,
                           QgsLayoutSize, QgsUnitTypes)
    from qgis.PyQt.QtGui import QFont, QColor

    lyrs = QgsProject.instance().mapLayersByName(_POND_LAYER_NAME)
    if not lyrs:
        return None
    rows = sorted(
        (f["pond_id"], f["distance_label"] or "")
        for f in lyrs[0].getFeatures() if f["pond_id"]
    )
    if not rows:
        return None
    status_by_id = {}
    assessment_by_id = {}
    if include_status:
        try:
            import json
            raw, ok = QgsProject.instance().readEntry(
                "Biome", "gcn_hsi_ponds_json", "")
            records = json.loads(raw) if ok and raw else []
            short_status = {
                "not_assessed": "Not assessed",
                "hsi_required": "Surveyed / HSI pending",
                "dry": "Dry at survey",
                "inaccessible": "Inaccessible / linked",
                "unsuitable_desktop": "Not suitable habitat",
                "not_linked": "Not considered linked",
            }
            for record in records if isinstance(records, list) else []:
                pond_id = str(record.get("mapped_id") or "")
                if not pond_id:
                    continue
                status = str(record.get("status") or "not_assessed")
                if status == "hsi_complete" and record.get("hsi") is not None:
                    detail = "HSI %.2f — %s" % (
                        float(record["hsi"]), record.get("band") or "")
                else:
                    detail = short_status.get(status, "Not assessed")
                status_by_id[pond_id] = detail
                assessment_by_id[pond_id] = {
                    "status": status,
                    "hsi": record.get("hsi"),
                    "band": str(record.get("band") or ""),
                }
        except Exception:
            status_by_id = {}
    gcn_class_by_id = {}
    if lyrs[0].fields().indexOf("gcn_status") >= 0:
        gcn_class_by_id = {
            str(f["pond_id"]): str(f["gcn_status"] or "").strip().upper()
            for f in lyrs[0].getFeatures() if f["pond_id"]
        }
    texts = []
    for pid, distance in rows:
        status = gcn_class_by_id.get(str(pid), "")
        status_suffix = f" [{status}]" if status in ("P", "A", "NS") else ""
        first = ((f"{pid}{status_suffix} ({distance})") if distance
                 else f"{pid}{status_suffix}")
        detail = status_by_id.get(str(pid))
        texts.append(first + ("\n  " + detail if detail else ""))

    lx, ly, lw = leg_rect[0], leg_rect[1], leg_rect[2]
    top = (top_mm if top_mm is not None else ly) + 2.0
    if limit_mm is None:
        limit_mm = 150.0     # safe fallback: clear of the footer/logo band
    avail = limit_mm - top
    if avail <= 6.0:
        return None

    def _line_h(pt):
        # Compact leading preserves room for two-line assessment entries
        # without forcing the text down to an unreadably small point size.
        # QGIS/Qt can render a slightly taller line box than the nominal font
        # height, particularly in PDF exports.  The previous 1.05 multiplier
        # clipped the final SWB row, so retain normal readable leading and let
        # the fitter switch to two columns sooner when necessary.
        return pt * 0.3528 * 1.20

    # Assessment lists are grouped by outcome. Each group uses a bold heading
    # and one or more regular lines of pond IDs; HSI groups also show the
    # numerical score for each pond. This is substantially more legible than
    # repeating a two-line status block for every marker.
    if include_status:
        grouped = []

        def _wrap_tokens(tokens, limit=34):
            lines, current = [], ""
            for token in tokens:
                candidate = token if not current else current + ", " + token
                if current and len(candidate) > limit:
                    lines.append(current)
                    current = token
                else:
                    current = candidate
            if current:
                lines.append(current)
            return lines or ["—"]

        # HSI ponds are grouped by suitability band, with scores retained.
        band_order = ["Excellent", "Good", "Average", "Below average", "Poor"]
        band_colours = {
            "Excellent": "#0F5C39",
            "Good": "#2E7D32",
            "Average": "#D39A00",
            "Below average": "#D97706",
            "Poor": "#C0392B",
        }
        for band in band_order:
            tokens = []
            for pond_id, _distance in rows:
                assessment = assessment_by_id.get(str(pond_id), {})
                if (assessment.get("status") == "hsi_complete"
                        and assessment.get("band") == band
                        and assessment.get("hsi") is not None):
                    tokens.append("%s — %.2f" % (
                        pond_id, float(assessment["hsi"])))
            if tokens:
                grouped.append(("HSI assessed — %s" % band,
                                _wrap_tokens(tokens, 30),
                                band_colours[band]))

        status_groups = [
            ("dry", "Dry at survey"),
            ("inaccessible", "Inaccessible / potentially linked"),
            ("unsuitable_desktop", "Not suitable habitat"),
            ("not_linked", "Not considered linked"),
            ("hsi_required", "Surveyed — HSI pending"),
            ("not_assessed", "Not assessed"),
        ]
        for code, heading in status_groups:
            ids = [str(pond_id) for pond_id, _distance in rows
                   if assessment_by_id.get(str(pond_id), {}).get(
                       "status", "not_assessed") == code]
            if ids:
                grouped.append((heading, _wrap_tokens(ids), None))

        pt = 8.5
        for candidate in (10.0, 9.5, 9.0, 8.5):
            line_count = sum(1 + len(lines) for _heading, lines, _colour in grouped)
            group_gaps = max(0, len(grouped) - 1) * 3.0
            heading_gaps = len(grouped) * 0.9
            if line_count * _line_h(candidate) + group_gaps + heading_gaps <= avail:
                pt = candidate
                break
        body = QColor((theme or {}).get("bodyText", "#2A2417"))
        _mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters
        y = top

        def _pond_line(text, bold, indent, item_id, colour=None):
            nonlocal y
            label = QgsLayoutItemLabel(layout)
            label.setId(item_id)
            label.setText(text)
            font = QFont("Inter")
            font.setPointSizeF(pt)
            font.setBold(bold)
            label.setFont(font)
            label.setFontColor(QColor(colour) if colour else body)
            try:
                label.setMargin(0.0)
                label.setHAlign(Qt.AlignmentFlag.AlignLeft)
                label.setVAlign(Qt.AlignmentFlag.AlignTop)
            except Exception:
                pass
            label.setBackgroundEnabled(False)
            label.setFrameEnabled(False)
            layout.addLayoutItem(label)
            label.attemptMove(QgsLayoutPoint(lx + indent, y, _mm))
            label.attemptResize(QgsLayoutSize(lw - indent - 1.0,
                                              _line_h(pt) + 0.5, _mm))
            y += _line_h(pt)

        for index, (heading, detail_lines, dot_colour) in enumerate(grouped):
            gap = 3.0 if index else 0.0
            if y + gap + 0.9 + (1 + len(detail_lines)) * _line_h(pt) > limit_mm:
                _pond_line("Additional pond statuses omitted", False, 1.0,
                           "Pond List More")
                break
            if index:
                y += gap
            _pond_line(heading, True, 1.0,
                       f"Pond Status Heading {index + 1}", dot_colour)
            y += 0.9
            for line_index, line in enumerate(detail_lines):
                _pond_line(line, False, 3.0,
                           f"Pond Status Detail {index + 1}-{line_index + 1}")
        return y

    # Largest FONT first, splitting into two columns to afford it — two
    # readable columns beat one column of tiny text (a 5.5 pt single-column
    # list shipped once and read too small). The width guard keeps the
    # longest row inside its column so nothing wraps or clips.
    choice = None
    for pt in (10.0, 9.5, 9.0, 8.5):
        widest_mm = max(len(line) for text in texts for line in text.splitlines()) * pt * 0.3528 * 0.58
        for cols in ((1,) if include_status else (1, 2)):
            n_rows = math.ceil(sum(t.count("\n") + 1 for t in texts) / cols)
            if (n_rows * _line_h(pt) <= avail
                    and widest_mm <= lw / cols - 2.0):
                choice = (cols, pt, len(texts))
                break
        if choice:
            break
    if choice is None:
        pt = 8.5
        per_col = max(2, int(avail // _line_h(pt)))
        fallback_cols = 1 if include_status else 2
        if include_status:
            selected = []
            used_lines = 0
            for text in texts:
                needed = text.count("\n") + 1
                if used_lines + needed > per_col - 1:
                    break
                selected.append(text)
                used_lines += needed
            shown = len(selected)
            texts = selected + [f"+ {len(rows) - shown} more (see map)"]
        else:
            shown = min(len(texts), per_col * fallback_cols - 1)
            texts = texts[:shown] + ([f"+ {len(rows) - shown} more (see map)"]
                                      if shown < len(rows) else [])
        choice = (fallback_cols, pt, len(texts))
    cols, pt, n_shown = choice

    per_col = math.ceil(n_shown / cols)
    col_w = lw / cols
    body = QColor((theme or {}).get("bodyText", "#2A2417"))
    bottom = top
    for c in range(cols):
        chunk = texts[c * per_col:(c + 1) * per_col]
        if not chunk:
            continue
        lab = QgsLayoutItemLabel(layout)
        lab.setId(f"Pond Distances {c + 1}")
        lab.setText("\n".join(chunk))
        f = QFont("Inter")
        f.setPointSizeF(pt)
        lab.setFont(f)
        lab.setFontColor(body)
        try:
            lab.setMargin(0.0)
        except Exception:
            pass
        try:
            lab.setHAlign(Qt.AlignmentFlag.AlignLeft)
            lab.setVAlign(Qt.AlignmentFlag.AlignTop)
        except Exception:
            pass
        lab.setBackgroundEnabled(False)
        lab.setFrameEnabled(False)
        layout.addLayoutItem(lab)
        _mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters
        lab.attemptMove(QgsLayoutPoint(lx + 1.0 + c * col_w, top, _mm))
        # Include a full lower safety margin: QgsLayoutItemLabel otherwise
        # clips the descenders/baseline of the final row on some QGIS builds.
        h = sum(text.count("\n") + 1 for text in chunk) * _line_h(pt) + 2.0
        lab.attemptResize(QgsLayoutSize(col_w - 2.0, h, _mm))
        bottom = max(bottom, top + h)
    return bottom


def _add_empty_desk_status_label(layout, leg_rect, top_mm, text, theme):
    """Add one restrained plain-text row beneath an otherwise empty key."""
    if not text:
        return None
    from qgis.core import (QgsLayoutItemLabel, QgsLayoutPoint, QgsLayoutSize,
                           QgsUnitTypes)
    from qgis.PyQt.QtGui import QFont, QColor

    lx, ly, lw = leg_rect[0], leg_rect[1], leg_rect[2]
    top = (top_mm if top_mm is not None else ly) + 2.0
    lab = QgsLayoutItemLabel(layout)
    lab.setId("Desk Study Empty Status")
    lab.setText(text)
    font = QFont("Inter")
    font.setPointSizeF(7.0)
    lab.setFont(font)
    lab.setFontColor(QColor((theme or {}).get("bodyText", "#2A2417")))
    try:
        lab.setMargin(0.0)
        lab.setHAlign(Qt.AlignmentFlag.AlignLeft)
        lab.setVAlign(Qt.AlignmentFlag.AlignTop)
    except Exception:
        pass
    lab.setBackgroundEnabled(False)
    lab.setFrameEnabled(False)
    layout.addLayoutItem(lab)
    _mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters
    lab.attemptMove(QgsLayoutPoint(lx + 1.0, top, _mm))
    lab.attemptResize(QgsLayoutSize(lw - 2.0, 9.0, _mm))
    return top + 9.0


def _add_pond_gcn_key(layout, leg_rect, top_mm, theme):
    """Explain Natural England's P/A/NS pond classifications when used."""
    from qgis.core import (QgsLayoutItemLabel, QgsLayoutPoint, QgsLayoutSize,
                           QgsProject, QgsUnitTypes)
    from qgis.PyQt.QtGui import QFont, QColor

    layers = QgsProject.instance().mapLayersByName(_POND_LAYER_NAME)
    if not layers or layers[0].fields().indexOf("gcn_status") < 0:
        return None
    statuses = {
        str(feature["gcn_status"] or "").strip().upper()
        for feature in layers[0].getFeatures()
    }
    if not statuses.intersection({"P", "A", "NS"}):
        return None
    lx, ly, lw = leg_rect[0], leg_rect[1], leg_rect[2]
    top = (top_mm if top_mm is not None else ly) + 3.0
    body = QColor((theme or {}).get("bodyText", "#2A2417"))
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters

    heading = QgsLayoutItemLabel(layout)
    heading.setId("Pond GCN Classification Heading")
    heading.setText("GCN survey status")
    heading_font = QFont("Inter")
    heading_font.setPointSizeF(8.0)
    heading_font.setBold(True)
    heading.setFont(heading_font)
    heading.setFontColor(body)
    heading.setBackgroundEnabled(False)
    heading.setFrameEnabled(False)
    layout.addLayoutItem(heading)
    heading.attemptMove(QgsLayoutPoint(lx + 1.0, top, mm))
    heading.attemptResize(QgsLayoutSize(lw - 2.0, 4.5, mm))

    definitions = QgsLayoutItemLabel(layout)
    definitions.setId("Pond GCN Classification Definitions")
    definitions.setText(
        "P — GCN present\nA — GCN absent\nNS — No survey")
    body_font = QFont("Inter")
    body_font.setPointSizeF(7.5)
    definitions.setFont(body_font)
    definitions.setFontColor(body)
    definitions.setBackgroundEnabled(False)
    definitions.setFrameEnabled(False)
    layout.addLayoutItem(definitions)
    definitions.attemptMove(QgsLayoutPoint(lx + 1.0, top + 4.5, mm))
    definitions.attemptResize(QgsLayoutSize(lw - 2.0, 13.0, mm))
    return top + 17.5


def _collapse_ws(name):
    """Collapse all whitespace (incl. legend-wrap newlines) to single spaces."""
    return " ".join((name or "").split())


def _canon_legend_name(name):
    """Canonical layer name for duplicate detection in the legend: radius
    suffix stripped and whitespace collapsed, matching how names are displayed
    after _apply_legend_style has cleaned them."""
    name = (name or "").replace(" (2 km)", "").replace(" (2km)", "")
    return _collapse_ws(name)


def _remove_duplicate_desk_layers(project):
    """Remove stacked duplicate copies of fetched desk-study layers from the
    project, keeping the most recently added copy of each.

    Earlier builds leaked the legend's wrapped label (with an embedded
    newline) back into project layer names via QgsLayerTreeLayer.setName(),
    so the exact-name cleanups in designated_sites missed the renamed copy
    and re-added the layer — e.g. two "Priority Habitat - Ancient Woodland"
    layers. Match on whitespace-collapsed names instead, and repair the
    surviving layer's name so exact-name lookups work again. Only fetched
    desk-study layers (PHI/GCN prefixes, designated-site labels) are touched.
    """
    from .designated_sites import all_ds_labels
    ds_labels = all_ds_labels()

    def _is_desk_layer(canon):
        return (canon.startswith(_PHI_LAYER_PREFIX)
                or canon.startswith(_GCN_LAYER_PREFIX)
                or canon in ds_labels)

    by_name = {}
    for node in project.layerTreeRoot().findLayers():   # tree order = age order
        lyr = node.layer()
        if lyr is None:
            continue
        canon = _collapse_ws(lyr.name())
        if _is_desk_layer(canon):
            by_name.setdefault(canon, []).append(lyr)

    for canon, lyrs in by_name.items():
        keep = lyrs[-1]
        for lyr in lyrs[:-1]:
            project.removeMapLayer(lyr.id())
        repaired = _collapse_ws(keep.name())
        if repaired != keep.name():
            keep.setName(repaired)


def _legend_bottom_mm(legend):
    """Measure a legend's true bottom edge (page mm), defensively.

    ``adjustBoxSize()`` is a NO-OP until the legend item has painted once:
    QgsLayoutItemLegend defers size-to-contents until it knows the render DPI
    (``mInitialMapScaleCalculated``), and this code runs headless before the
    layout designer first renders. Without priming, the "measurement" is just
    the placeholder rect the legend was given at build time, and a tall legend
    renders far below it — under the Scale/Date block. So: force one
    off-screen paint (which sets the DPI/scale state and triggers the real
    resize-to-contents), then measure, then add a small safety margin
    (~1.5 label rows). The Scale/Date block is capped above the footer, so
    the margin can only lower it, never clip the footer. Returns the bottom
    edge in page mm, or None."""
    try:
        legend.invalidateCache()
    except Exception:
        pass
    try:
        from qgis.PyQt.QtGui import QImage, QPainter
        from qgis.PyQt.QtWidgets import QStyleOptionGraphicsItem
        img = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        legend.paint(p, QStyleOptionGraphicsItem(), None)
        p.end()
    except Exception:
        pass
    try:
        legend.adjustBoxSize()
    except Exception:
        pass
    try:
        pos = legend.positionWithUnits()
        sz  = legend.sizeWithUnits()
    except Exception:
        return None
    try:
        from qgis.core import QgsLegendStyle
        lbl_pt = legend.style(QgsLegendStyle.Style.SymbolLabel).font().pointSizeF() or 7.0
    except Exception:
        lbl_pt = 7.0
    margin_mm = max(5.0, lbl_pt * (25.4 / 72.0) * 2.2)
    return pos.y() + sz.height() + margin_mm


def _build_desk_study_legend(legend_item, layers, map_type):
    """Build the legend model for a desk study map.

    For most map types: prunes the auto-populated tree (caller has already run
    setAutoUpdateModel(True) + updateLegend()) to keep only relevant layers.

    For Pond Map: builds manually from scratch by reading the Water Body layer's
    categorized renderer directly — updateLegend() cannot reliably populate a
    user-placed memory layer's categories because they are built dynamically.
    """
    from qgis.core import (
        QgsLayerTreeLayer, QgsLayerTreeGroup,
        QgsProject, QgsMapLayerLegendUtils,
    )

    from .designated_sites import (
        all_ds_labels, apply_imported_lws_style,
        PHI_HABITAT_GROUPS as _PHG)
    # Keep the dedicated LWS Map consistent with the Custom Map: show the
    # actual imported site names while retaining designation-based colours.
    for _layer in layers:
        if (_layer is not None
                and _layer.name() == "Local Wildlife Sites (Imported)"):
            apply_imported_lws_style(_layer)
    _DS_LABELS = all_ds_labels()      # both regions' labels (AONB and NSA)
    # Constraint-buffer layers are named "Buffer - <type>"; include each map's
    # own buffers by exact name so a Woodland buffer never leaks onto the DS map.
    _DS_BUFFER_NAMES  = {"Buffer - " + lbl for lbl in _DS_LABELS}
    _PHI_BUFFER_NAMES = ({"Buffer - " + g[1] for g in _PHG}
                         | {"Buffer - Ancient Woodland"})

    if map_type == "Designated Sites Map":
        keep_names = _DS_LABELS | _DS_BUFFER_NAMES
        keep_prefix = ()
        section_title = "Designated Sites"
    elif map_type == "Priority Habitats Map":
        keep_names = _PHI_BUFFER_NAMES
        keep_prefix = (_PHI_LAYER_PREFIX,)
        section_title = "Priority Habitats"
    elif map_type == "EPSL Map":
        keep_names = set()
        keep_prefix = (_EPSL_LAYER_NAME,)
        # No section heading: the gold panel title already reads "European
        # Protected Species Licences". The "EPSL Locations" layer heading is
        # likewise hidden in _apply_legend_style, leaving only the species
        # symbol key in the legend.
        section_title = None
    elif map_type == "TPO Map":
        keep_names = {_TPO_ZONE_NAME}
        keep_prefix = (_TPO_LAYER_NAME,)
        section_title = "Tree Preservation\nOrders"
    elif map_type == "Conservation Areas Map":
        keep_names = {_CONSERVATION_NAME}
        keep_prefix = ()
        section_title = "Conservation Areas"
    elif map_type == "GCN Risk Zones Map":
        keep_names = {_EDNA_LAYER_NAME}   # eDNA ponds keyed alongside the zones when visible
        keep_prefix = (_GCN_LAYER_PREFIX,)
        section_title = "GCN Risk Zones"
    elif map_type == "SSSI IRZ Map":
        keep_names = set()
        keep_prefix = (_IRZ_HIGHLIGHT_NAME, _IRZ_WMS_NAME)
        section_title = "SSSI Impact Risk Zones"
    elif map_type == "Pond Map":
        keep_names = {_POND_LAYER_NAME}
        keep_prefix = ()
        section_title = "Standing Water Bodies"
    elif map_type == "LWS Map":
        keep_names = {_LWS_LAYER_NAME, "Local Wildlife Sites (Imported)"}
        keep_prefix = ()
        section_title = "Local Wildlife Sites"
    else:
        keep_names = set()
        keep_prefix = ()
        section_title = None

    # Maps whose design shows a heading can have it reworded from the settings
    # cog; maps that deliberately have none (EPSL) stay heading-less.
    if section_title is not None:
        section_title = desk_legend_heading(map_type, section_title)

    # Boundary context is pinned to the top. Distance lines are conditional:
    # they belong only to their relevant map and only when effectively visible
    # (the layer and every ancestor group are checked).
    conditional_lines = {
        "EPSL Map": {_EPSL_LINES_NAME},
        "Pond Map": {_POND_LINES_NAME},
        "TPO Map": {_TPO_LINES_NAME},
        "LWS Map": {_LWS_LINES_NAME},
    }.get(map_type, set())

    basemap_ids = set()
    bm_grp = QgsProject.instance().layerTreeRoot().findGroup("Base Maps")
    if bm_grp is not None:
        for node in bm_grp.findLayers():
            if node.layer():
                basemap_ids.add(node.layer().id())

    model = legend_item.model()
    root  = model.rootGroup()

    # ══════════════════════════════════════════════════════════════════════════
    # POND MAP: build from scratch using the renderer's categories directly.
    # This is necessary because the Water Body layer is a user-placed memory
    # layer — updateLegend() does not reliably resolve its categorized renderer
    # nodes at print-layout build time.
    # ══════════════════════════════════════════════════════════════════════════
    if map_type == "Pond Map":
        # Clear whatever is there
        for child in root.children()[:]:
            root.removeChildNode(child)

        water_body_lyr = next(
            (lyr for lyr in layers if lyr.name() == _POND_LAYER_NAME), None
        )
        line_lyrs = [lyr for lyr in layers
                     if lyr.name() in conditional_lines]
        support_lyrs = [lyr for lyr in layers if _is_desk_support_layer(lyr.name())]

        # Boundary context (redline / 2 km buffer / redline buffer rings) at the TOP
        for lyr in support_lyrs:
            root.addLayer(lyr)
            tree_lyr = root.findLayer(lyr.id())
            if tree_lyr is not None:
                model.refreshLayerLegend(tree_lyr)

        # Section group for water bodies, beneath the boundary context
        if water_body_lyr:
            grp = root.addGroup(section_title)
            grp.addLayer(water_body_lyr)
            tree_lyr = grp.findLayer(water_body_lyr.id())

            # ICON ONCE: a single swatch row with the count. The per-pond
            # distances render as a plain text list beneath the legend
            # (_add_pond_list_label) — one swatch per pond repeated the same
            # icon 18+ times and overflowed the sidebar on pond-rich sites.
            from qgis.core import QgsCategorizedSymbolRenderer
            renderer = water_body_lyr.renderer()
            if isinstance(renderer, QgsCategorizedSymbolRenderer) and tree_lyr is not None:
                model.refreshLayerLegend(tree_lyr)
                legend_nodes = model.layerLegendNodes(tree_lyr)
                if legend_nodes:
                    n = len(legend_nodes)
                    QgsMapLayerLegendUtils.setLegendNodeOrder(tree_lyr, [0])
                    model.refreshLayerLegend(tree_lyr)
                    remaining = model.layerLegendNodes(tree_lyr)
                    if remaining:
                        if water_body_lyr.featureCount() == 0:
                            remaining[0].setUserLabel(
                                "Standing water body\n"
                                "None identified within the search area")
                        else:
                            remaining[0].setUserLabel(
                                "Water body" if n == 1 else f"{n} water bodies")
                    # The group heading + count row say it all — hide the
                    # "Water Body" layer heading between them.
                    QgsLegendRenderer.setNodeLegendStyle(
                        tree_lyr, QgsLegendStyle.Style.Hidden)

        # A visible distance-line layer gets one ordinary key row. Hidden
        # lines never enter `layers`, so no stale line symbol can survive.
        for line_lyr in line_lyrs:
            root.addLayer(line_lyr)
            tree_lyr = root.findLayer(line_lyr.id())
            if tree_lyr is not None:
                model.refreshLayerLegend(tree_lyr)

        # Styling then exit — skip the prune path below
        _apply_legend_style(legend_item, root, model, [], support_lyrs, map_type)
        # Keep the single water-body icon at swatch scale rather than the 8 mm
        # it renders at on the map.
        try:
            legend_item.setMaximumSymbolSize(5.0)
        except Exception:
            pass
        legend_item.adjustBoxSize()
        return

    # ══════════════════════════════════════════════════════════════════════════
    # GCN RISK ZONES MAP: build from scratch like the Pond Map. The risk-zone
    # polygons and the eDNA pond points are categorised memory layers whose
    # per-category swatches the auto-populated tree can't resolve; build each
    # one's nodes straight from the renderer and drop categories with no data.
    # (We filter empty categories by VALUE here — _apply_legend_style filters by
    # display label, which only works when label == value as it does for the
    # Red/Amber/Green zones but not for the eDNA present/absent/inconclusive.)
    # ══════════════════════════════════════════════════════════════════════════
    if map_type == "GCN Risk Zones Map":
        # QgsLegendRenderer / QgsLegendStyle come from the module-level import
        # (top of file). Do NOT re-import them locally here: a local import
        # anywhere in this function makes the name function-local for the WHOLE
        # function, so the prune path below — which never enters this branch —
        # then hits "UnboundLocalError: ... 'QgsLegendStyle' ..." (e.g. on an
        # LWS / TPO / Conservation Areas map). Only the names NOT available at
        # module level are imported locally.
        from qgis.core import (
            QgsCategorizedSymbolRenderer, QgsMapLayerLegendUtils,
        )

        zone_lyrs = [l for l in layers if l is not None
                     and l.name().startswith(_GCN_LAYER_PREFIX)]
        edna_lyrs = [l for l in layers if l is not None
                     and l.name() == _EDNA_LAYER_NAME]
        support_lyrs = [l for l in layers if l is not None
                        and _is_desk_support_layer(l.name())]
        flat_section = " ".join(section_title.split()).lower()

        def _cat_swatches(tl, lyr):
            """Per-category swatches straight from the renderer, keeping only
            categories that have at least one feature in this extent (filtered
            by VALUE, since our labels differ from the GCN_occ values)."""
            renderer = lyr.renderer()
            if not isinstance(renderer, QgsCategorizedSymbolRenderer):
                return
            model.refreshLayerLegend(tl)
            present = _category_values_present(lyr)
            keep = [i for i, c in enumerate(renderer.categories())
                    if present is None or c.value() in present]
            if keep:
                QgsMapLayerLegendUtils.setLegendNodeOrder(tl, keep)
            model.refreshLayerLegend(tl)

        for child in root.children()[:]:
            root.removeChildNode(child)

        # Boundary context (redline / 2 km buffer / rings) at the TOP
        for lyr in support_lyrs:
            root.addLayer(lyr)
            tl = root.findLayer(lyr.id())
            if tl is not None:
                model.refreshLayerLegend(tl)

        # Risk-zone polygons under the "GCN Risk Zones" section heading — the
        # group is created ONLY when a risk-zone county layer is actually loaded,
        # so the heading never appears when only eDNA ponds are shown.
        if zone_lyrs:
            grp = root.addGroup(section_title)
            for lyr in zone_lyrs:
                grp.addLayer(lyr)
                tl = grp.findLayer(lyr.id())
                if tl is None:
                    continue
                _cat_swatches(tl, lyr)
                heading = lyr.name()           # "GCN Risk Zones — Kent" → "Kent"
                if heading.startswith(_GCN_LAYER_PREFIX):
                    heading = heading[len(_GCN_LAYER_PREFIX):].lstrip(" -–—:")
                if (not heading
                        or " ".join(heading.split()).lower() == flat_section):
                    QgsLegendRenderer.setNodeLegendStyle(tl, QgsLegendStyle.Style.Hidden)
                elif heading != lyr.name():
                    try:
                        tl.setUseLayerName(False)
                    except AttributeError:
                        pass
                    tl.setName(heading)

        # eDNA pond surveys — always its OWN top-level entry (not a risk zone),
        # so the "GCN Risk Zones" heading is never shown for eDNA alone.
        for lyr in edna_lyrs:
            root.addLayer(lyr)
            tl = root.findLayer(lyr.id())
            if tl is None:
                continue
            _cat_swatches(tl, lyr)
            heading = lyr.name().replace(" (2 km)", "").strip()
            if heading and heading != lyr.name():
                try:
                    tl.setUseLayerName(False)
                except AttributeError:
                    pass
                tl.setName(heading)

        # User-placed water bodies — only in `layers` when their node was
        # visible on the canvas. ICON ONCE with a count, same as the Pond Map:
        # the categorized renderer has one identical icon per pond and a
        # per-pond swatch list would overflow the sidebar on pond-rich sites.
        pond_lyrs = [l for l in layers if l is not None
                     and l.name() == _POND_LAYER_NAME]
        for lyr in pond_lyrs:
            root.addLayer(lyr)
            tl = root.findLayer(lyr.id())
            if tl is None:
                continue
            model.refreshLayerLegend(tl)
            if isinstance(lyr.renderer(), QgsCategorizedSymbolRenderer):
                n = len(model.layerLegendNodes(tl))
                if n:
                    QgsMapLayerLegendUtils.setLegendNodeOrder(tl, [0])
                    model.refreshLayerLegend(tl)
                    remaining = model.layerLegendNodes(tl)
                    if remaining:
                        remaining[0].setUserLabel(
                            "Placed water body" if n == 1
                            else f"{n} placed water bodies")
                    # The count row says it all — hide the "Water Body"
                    # layer heading above it.
                    QgsLegendRenderer.setNodeLegendStyle(
                        tl, QgsLegendStyle.Style.Hidden)
        if pond_lyrs:
            # The pond icon renders ~8 mm on the map — cap the legend swatch
            # (same fix as the Pond Map; zone polygon swatches are smaller
            # than the cap and unaffected).
            try:
                legend_item.setMaximumSymbolSize(5.0)
            except Exception:
                pass

        # specific_lyrs=[] → skip _apply_legend_style's own (label-based)
        # category filter, same as the Pond path; we filtered by value above.
        _apply_legend_style(legend_item, root, model, [], support_lyrs, map_type)
        legend_item.adjustBoxSize()
        return

    # ══════════════════════════════════════════════════════════════════════════
    # ALL OTHER MAP TYPES: prune the auto-populated tree
    # ══════════════════════════════════════════════════════════════════════════

    def _should_keep(lyr):
        if lyr is None:
            return False
        nm = lyr.name()
        # Redline buffer helper layers (boundary line / curved label) never
        # appear in the key — only the ring polygon does.
        if _is_redline_buffer_layer(nm) and not _is_redline_buffer_legend_layer(nm):
            return False
        proj_node = QgsProject.instance().layerTreeRoot().findLayer(lyr.id())
        visible = proj_node is not None and _node_visible_recursive(proj_node)
        if _is_desk_support_layer(nm):
            return visible
        if nm in conditional_lines:
            return visible
        if nm in {_EPSL_LINES_NAME, _POND_LINES_NAME,
                  _TPO_LINES_NAME, _LWS_LINES_NAME}:
            return False
        if nm in BASEMAP_NAMES or lyr.id() in basemap_ids:
            return False
        # A layer the user has switched OFF (its own checkbox, or a parent group
        # such as a whole habitat group) is hidden on the map — keep it off the
        # legend too. Support/boundary layers are handled above and never reach
        # here, so this only gates the data layers.
        if keep_names and nm in keep_names:
            return visible
        if keep_prefix and any(nm.startswith(p) for p in keep_prefix):
            return visible
        if not keep_names and not keep_prefix:
            return visible
        return False

    # ── Step 1: prune unwanted layer nodes (and empty groups) from the tree ──
    def _prune(group):
        for child in group.children()[:]:
            if isinstance(child, QgsLayerTreeGroup):
                _prune(child)
                if not child.children():
                    group.removeChildNode(child)
            elif isinstance(child, QgsLayerTreeLayer):
                if not _should_keep(child.layer()):
                    group.removeChildNode(child)

    _prune(root)

    # Drop duplicate layer nodes that share a name — e.g. a second copy of the
    # Ancient Woodland WFS layer left in the project after repeated desk-study
    # runs. Compare canonical names (whitespace collapsed, radius suffix
    # stripped): a historical rename leak could leave one copy with an
    # embedded newline in its name, and after styling both copies display
    # identically. Keep the first.
    _seen_legend_names = set()
    for node in list(root.findLayers()):
        lyr = node.layer()
        nm = _canon_legend_name(lyr.name() if lyr else node.name())
        if nm in _seen_legend_names:
            parent = node.parent()
            if parent is not None:
                parent.removeChildNode(node)
        else:
            _seen_legend_names.add(nm)

    # Collect what survived
    specific_lyrs = [
        n.layer() for n in root.findLayers()
        if n.layer() and not _is_desk_support_layer(n.layer().name())
    ]
    support_lyrs = [
        n.layer() for n in root.findLayers()
        if n.layer() and _is_desk_support_layer(n.layer().name())
    ]

    # An empty EPSL search deliberately retains a styled zero-feature layer.
    # Keep its marker in the key and label it explicitly, so an icon-free map
    # clearly communicates "no licence locations found" rather than looking as
    # though the licence layer/key was omitted accidentally.
    if map_type == "EPSL Map":
        for lyr in specific_lyrs:
            if lyr.name() != _EPSL_LAYER_NAME or lyr.featureCount() != 0:
                continue
            tree_lyr = root.findLayer(lyr.id())
            if tree_lyr is None:
                continue
            model.refreshLayerLegend(tree_lyr)
            nodes = model.layerLegendNodes(tree_lyr)
            if nodes:
                nodes[0].setUserLabel("None found within 2 km")

    if map_type == "TPO Map":
        for lyr in specific_lyrs:
            if lyr.name() != _TPO_LAYER_NAME or lyr.featureCount() != 0:
                continue
            tree_lyr = root.findLayer(lyr.id())
            if tree_lyr is None:
                continue
            model.refreshLayerLegend(tree_lyr)
            nodes = model.layerLegendNodes(tree_lyr)
            if nodes:
                nodes[0].setUserLabel("TPO location — none found")

    # ── Step 2: move specific layers under a section group heading ────────────
    if section_title and specific_lyrs:
        nodes_to_group = [
            n for n in root.findLayers()
            if n.layer() and not _is_desk_support_layer(n.layer().name())
        ]
        if nodes_to_group:
            # QgsLegendRenderer resolves to the module-level import (see the
            # note in the GCN branch above — re-importing it locally would make
            # it function-local and risk an UnboundLocalError on other paths).
            # The section heading already names the dataset, so drop the
            # repeated prefix from each layer heading ("Priority Habitat -
            # Ancient Woodland" → "Ancient Woodland") and hide headings that
            # would only repeat the section title (e.g. the single "Tree
            # Preservation Orders" layer under its own section).
            heading_strip = {
                "Priority Habitats Map": (_PHI_LAYER_PREFIX,),
                "GCN Risk Zones Map":    (_GCN_LAYER_PREFIX,),
                "SSSI IRZ Map":          ("SSSI IRZ",),
            }.get(map_type, ())
            flat_section = " ".join(section_title.split()).lower()
            grp = root.addGroup(section_title)
            for node in nodes_to_group:
                clone = node.clone()
                grp.addChildNode(clone)
                node.parent().removeChildNode(node)
                heading = ((clone.name() or "").replace(" (2 km)", "")
                           .replace(" (2km)", "").strip())
                heading = re.sub(
                    r"\s*\(\s*imported\s*\)\s*$", "", heading,
                    flags=re.IGNORECASE).strip()
                for p in heading_strip:
                    if heading.startswith(p) and len(heading) > len(p):
                        heading = heading[len(p):].lstrip(" -–—:")
                        break
                if (not heading
                        or " ".join(heading.split()).lower() == flat_section):
                    QgsLegendRenderer.setNodeLegendStyle(
                        clone, QgsLegendStyle.Style.Hidden)
                elif heading != clone.name():
                    try:
                        clone.setUseLayerName(False)
                    except AttributeError:
                        pass
                    clone.setName(heading)

    # On map types with no section heading (e.g. EPSL) the data features were
    # left in the root above the support layers; pin the boundary context
    # (redline / 2 km buffer / redline buffer rings) to the TOP of the key.
    if not section_title:
        support_nodes = [c for c in root.children()
                         if isinstance(c, QgsLayerTreeLayer) and c.layer()
                         and _is_desk_support_layer(c.layer().name())]
        for offset, node in enumerate(support_nodes):
            clone = node.clone()
            root.insertChildNode(offset, clone)
            root.removeChildNode(node)

    _apply_legend_style(legend_item, root, model, specific_lyrs, support_lyrs, map_type)
    legend_item.adjustBoxSize()



# ── Desk Study Map Exporter ───────────────────────────────────────────────────

# Layer name prefixes / exact names for each desk study map type
_DS_LAYER_NAMES = {
    "SSSI":             "DS_sssi",
    "SAC":              "DS_sac",
    "SPA":              "DS_spa",
    "Ramsar":           "DS_ramsar",
    "NNR":              "DS_nnr",
    "LNR":              "DS_lnr",
    "AONB":             "DS_aonb",
    "National Parks":   "DS_nat_parks",
}

_PHI_LAYER_PREFIX  = "Priority Habitat - "  # renamed from "PHI - " to match designated_sites.py
_EPSL_LAYER_NAME   = "EPSL Locations"
_EPSL_LINES_NAME   = "EPSL Distance Lines"
_TPO_LAYER_NAME    = "Tree Preservation Orders"
_TPO_LINES_NAME    = "TPO Distance Lines"
_TPO_ZONE_NAME     = "TPO Zones"
_CONSERVATION_NAME = "Conservation Areas"
_GCN_LAYER_PREFIX  = "GCN Risk Zones"        # covers "GCN Risk Zones — Kent" etc.
_GCN_GROUP_NAME    = "GCN Risk Zones"
_EDNA_LAYER_NAME   = "GCN eDNA Pond Surveys"  # national NE eDNA ponds; rides on the GCN map
_POND_LAYER_NAME   = "Water Body"
_POND_LINES_NAME   = "Pond Distance Lines"
_POND_GROUP_NAME   = "Standing Water Bodies"
_POND_OUTLINES_NAME = "Mapped Water-body Outlines"
_LEGACY_POND_OUTLINES_NAME = "Standing Water Bodies"
_LWS_LAYER_NAME    = "Local Wildlife Sites"
_LWS_IMPORTED_LAYER_NAME = "Local Wildlife Sites (Imported)"
_LWS_LINES_NAME    = "LWS Distance Lines"
_IRZ_HIGHLIGHT_NAME = "SSSI IRZ — Intersecting Zones"
_IRZ_WMS_NAME       = "SSSI IRZ — WMS Overlay"

# Maps each GCN service name suffix (after "GCN Risk Zones — ") to the
# corresponding county logo filename bundled in the plugin directory.
_GCN_COUNTY_LOGO_MAP = {
    "Cambridgeshire":                                          "NE_GCN_Cambridgeshire.png",
    "Cheshire":                                                "NE_GCN_Cheshire.png",
    "Cumbria":                                                 "NE_GCN_Cumbria.png",
    "Derbyshire":                                              "NE_GCN_Derbyshire.png",
    "Dorset":                                                  "NE_GCN_Dorset.png",
    "Essex":                                                   "NE_GCN_Essex.png",
    "Greater Manchester":                                      "NE_GCN_Greater_Manchester.png",
    "Hertfordshire":                                           "NE_GCN_Hertfordshire.png",
    "Kent":                                                    "NE_GCN_Kent.png",
    "Lancashire":                                              "NE_GCN_Lancashire.png",
    "Leicestershire Rutland Rushcliffe and South Kesteven":    "NE_GCN_Leicestershire_Rutland_Rushcliffe_South_Kesteven.png",
    "Norfolk and Suffolk":                                     "NE_GCN_Norfolk_and_Suffolk.png",
    "North East Yorkshire":                                    "NE_GCN_North_East_Yorkshire.png",
    "North Somerset and South Gloucestershire":                "NE_GCN_North_Somerset_South_Gloucestershire.png",
    "North West Yorkshire":                                    "NE_GCN_North_West_Yorkshire.png",
    "Northumberland Durham Tyne and Wear Tees Valley":         "NE_GCN_Northumberland_Durham_Tyne_Wear_Tees_Valley.png",
    "Shropshire":                                              "NE_GCN_Shropshire.png",
    "Somerset":                                                "NE_GCN_Somerset.png",
    "South East Yorkshire":                                    "NE_GCN_South_East_Yorkshire.png",
    "Swindon and Wiltshire":                                   "NE_GCN_Swindon_and_Wiltshire.png",
    "Telford":                                                 "NE_GCN_Telford.png",
}


def _gcn_county_logo_path(plugin_dir):
    """Return the absolute path to the county logo PNG for the currently loaded
    GCN Risk Zones layer, or None if no GCN layer is loaded / no logo matches.

    The loaded layer name follows the pattern  "GCN Risk Zones — <County Name>"
    (set by base_layers._gcn_display_name).  We strip the prefix, normalise the
    underscored service-name suffix into a spaced display name, then look it up
    in _GCN_COUNTY_LOGO_MAP.
    """
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    grp  = root.findGroup(_GCN_GROUP_NAME)
    if grp is None:
        return None

    for node in grp.findLayers():
        lyr = node.layer()
        if lyr and lyr.name().startswith("GCN Risk Zones"):
            # Strip "GCN Risk Zones — " prefix (em-dash with spaces)
            county_raw = lyr.name().replace("GCN Risk Zones \u2014 ", "").strip()
            # Normalise underscores → spaces in case the name was never pretty-printed
            county_key = county_raw.replace("_", " ")
            logo_file = _GCN_COUNTY_LOGO_MAP.get(county_key)
            if logo_file:
                return os.path.join(plugin_dir, logo_file).replace("\\", "/")
    return None
_DS_GROUP_NAME     = "Designated Sites"
_PHI_GROUP_NAME    = "Priority Habitats"
_BUFFER_LAYER_NAME = "Search Buffer"
_REDLINE_NAME      = "Redline Boundary"


def _node_visible_recursive(node):
    """Return True only if the node AND every ancestor group are checked.

    QgsLayerTreeNode.isVisible() / itemVisibilityChecked() report only the
    node's own checkbox. itemVisibilityCheckedRecursive() would do the full
    walk, but it is not available in all QGIS versions, so walk the parent
    chain manually for compatibility.
    """
    cur = node
    while cur is not None:
        # isVisible() returns the node's own checkbox state on every version.
        try:
            checked = cur.isVisible()
        except Exception:
            checked = True
        if not checked:
            return False
        cur = cur.parent()
    return True


def _visible_layers_in_group(root, group_name):
    """Walk a named layer-tree group and return all visible layers within it.

    Uses _node_visible_recursive() so a layer is only treated as visible
    when the layer node AND every ancestor group are checked. This matters
    for the Ancient Woodland layer, which shares the Priority Habitats group
    with the other habitat layers — a plain isVisible() check can report it
    visible even after it has been toggled off.
    """
    layers = []
    grp = root.findGroup(group_name)
    if grp is None:
        return layers
    for node in grp.findLayers():
        if _node_visible_recursive(node) and node.layer():
            layers.append(node.layer())
    return layers


def _is_redline_buffer_layer(name):
    """True for any layer in the user's 'Redline Boundary Buffers' group — the
    ring polygon ('Redline Boundary 50m Buffer'), its boundary line, or its
    curved distance label. Excludes the redline itself."""
    return name.startswith("Redline Boundary ") and "Buffer" in name


def _is_redline_buffer_legend_layer(name):
    """Only the ring polygon belongs in a legend — not its (Boundary) line or
    (Label) text helper layers."""
    return (_is_redline_buffer_layer(name)
            and not name.endswith("(Boundary)")
            and not name.endswith("(Label)"))


def _is_desk_support_layer(name):
    """Boundary-context layers pinned to the TOP of a desk study legend (and
    rendered BENEATH the data features): the redline, the 2 km search buffer,
    and the user's redline buffer rings."""
    return (name in (_REDLINE_NAME, _BUFFER_LAYER_NAME)
            or _is_redline_buffer_legend_layer(name))


def _constraint_buffer_layers(root, kind):
    """Visible constraint-buffer layers for a map. DS buffers belong on the
    Designated Sites map, habitat buffers on the Priority Habitats map — matched
    by exact 'Buffer - <type>' name so neither leaks onto the other's map. They
    live in their own 'Constraint Buffers' group, not the DS/PHI group, so
    _get_desk_study_layers has to pull them in explicitly."""
    from .designated_sites import (
        _BUFFER_GROUP_NAME, all_ds_labels,
        PHI_HABITAT_GROUPS as _PHG)
    grp = root.findGroup(_BUFFER_GROUP_NAME)
    if grp is None:
        return []
    if kind == "ds":
        names = {"Buffer - " + lbl for lbl in all_ds_labels()}
    else:
        names = {"Buffer - " + g[1] for g in _PHG} | {"Buffer - Ancient Woodland"}
    out = []
    for node in grp.findLayers():
        lyr = node.layer()
        if lyr is not None and lyr.name() in names and _node_visible_recursive(node):
            out.append(lyr)
    return out


def _get_desk_study_layers(project, map_type):
    """Return ordered layer list for a desk study map.

    Layers always include: the type-specific layers, redline, 2km buffer,
    and the active basemap.  Basemap is included in the map canvas set but
    excluded from the legend by _build_desk_study_legend.

    The data features render ABOVE the boundary context (redline, 2km search
    buffer, and — when its group is visible — the redline buffer rings); only
    the basemap stays at the bottom.
    """
    root = project.layerTreeRoot()
    specific, redline, buffer_lyr = [], [], []

    # Full basemap context — every visible sublayer of the basemap group(s)
    # (e.g. the whole OS Zoomstack), so the render keeps its detail.
    basemap = _basemap_context_layers(project)

    # Redline and search buffer follow effective tree visibility. Exported maps
    # and their legends must never resurrect context the user switched off.
    for lyr in project.mapLayers().values():
        n = lyr.name()
        if n not in (_REDLINE_NAME, _BUFFER_LAYER_NAME):
            continue
        node = root.findLayer(lyr.id())
        if node is None or not _node_visible_recursive(node):
            continue
        if n == _REDLINE_NAME:
            redline.append(lyr)
        else:
            buffer_lyr.append(lyr)

    # User's redline buffer rings — boundary context beneath the data features
    # (above the basemap), included when the 'Redline Boundary Buffers' group is
    # visible. Distance-line and curved-label helper layers ride along on the
    # map; the legend keeps only the ring polygon (see _build_desk_study_legend).
    redline_buffers = []
    rb_grp = root.findGroup("Redline Boundary Buffers")
    if rb_grp is not None and _node_visible_recursive(rb_grp):
        for node in rb_grp.findLayers():
            if node.layer() and _node_visible_recursive(node):
                redline_buffers.append(node.layer())

    # Type-specific layers — walk the correct group first
    if map_type == "Designated Sites Map":
        specific = _visible_layers_in_group(root, _DS_GROUP_NAME)

    elif map_type == "Priority Habitats Map":
        specific = _visible_layers_in_group(root, _PHI_GROUP_NAME)

    elif map_type == "EPSL Map":
        for lyr in project.mapLayers().values():
            if lyr.name() in (_EPSL_LAYER_NAME, _EPSL_LINES_NAME):
                node = root.findLayer(lyr.id())
                empty_epsl = (
                    lyr.name() == _EPSL_LAYER_NAME
                    and lyr.featureCount() == 0
                    and bool(lyr.customProperty(
                        "biome_empty_epsl_search", 0)))
                if node is not None and (
                        _node_visible_recursive(node) or empty_epsl):
                    specific.append(lyr)
        # Ensure lines render beneath points: sort so lines come last in specific
        specific.sort(key=lambda l: 0 if l.name() == _EPSL_LINES_NAME else 1)

    elif map_type == "TPO Map":
        for lyr in project.mapLayers().values():
            if lyr.name() in (_TPO_LAYER_NAME, _TPO_LINES_NAME, _TPO_ZONE_NAME):
                node = root.findLayer(lyr.id())
                if node is not None and _node_visible_recursive(node):
                    specific.append(lyr)
        # Draw order (top→bottom): lines, tree points, then zone polygons last
        # so the semi-transparent zone fills sit beneath the points and lines.
        _tpo_order = {_TPO_LINES_NAME: 0, _TPO_LAYER_NAME: 1, _TPO_ZONE_NAME: 2}
        specific.sort(key=lambda l: _tpo_order.get(l.name(), 2))

    elif map_type == "Conservation Areas Map":
        for lyr in project.mapLayers().values():
            if lyr.name() == _CONSERVATION_NAME:
                node = root.findLayer(lyr.id())
                if node is not None and _node_visible_recursive(node):
                    specific.append(lyr)

    elif map_type == "SSSI IRZ Map":
        for lyr in project.mapLayers().values():
            if lyr.name() in (_IRZ_HIGHLIGHT_NAME, _IRZ_WMS_NAME):
                node = root.findLayer(lyr.id())
                if node is not None and _node_visible_recursive(node):
                    specific.append(lyr)
        # Also include any visible SSSI boundary layer from the DS group
        ds_grp = root.findGroup(_DS_GROUP_NAME)
        if ds_grp is not None:
            for node in ds_grp.findLayers():
                if _node_visible_recursive(node) and node.layer():
                    if "SSSI" in node.layer().name() or "sssi" in node.layer().name():
                        specific.append(node.layer())
        # WMS beneath highlight layer beneath SSSI boundary
        specific.sort(key=lambda l: (
            0 if l.name() == _IRZ_WMS_NAME else
            1 if l.name() == _IRZ_HIGHLIGHT_NAME else 2
        ))

    elif map_type == "Pond Map":
        for lyr in project.mapLayers().values():
            if lyr.name() in (_POND_LAYER_NAME, _POND_LINES_NAME,
                               _POND_OUTLINES_NAME,
                               _LEGACY_POND_OUTLINES_NAME):
                node = root.findLayer(lyr.id())
                if node is not None and _node_visible_recursive(node):
                    specific.append(lyr)
        # Mapped water-body polygons at bottom, lines above, pond points on top.
        # Keep accepting the former layer name so older projects export their
        # saved outlines too.
        def _pond_sort_key(l):
            if l.name() == _POND_LAYER_NAME: return 0
            if l.name() == _POND_LINES_NAME: return 1
            if l.name() in (_POND_OUTLINES_NAME,
                             _LEGACY_POND_OUTLINES_NAME): return 2
            return 3
        specific.sort(key=_pond_sort_key)
        # (Redline buffer rings are added as boundary context for every desk
        # study map — see redline_buffers above — so they sit beneath the data.)

    elif map_type == "LWS Map":
        for lyr in project.mapLayers().values():
            if lyr.name() in (_LWS_LAYER_NAME, _LWS_IMPORTED_LAYER_NAME,
                              _LWS_LINES_NAME):
                node = root.findLayer(lyr.id())
                if node is not None and _node_visible_recursive(node):
                    specific.append(lyr)
        # Lines beneath points
        specific.sort(key=lambda l: 0 if l.name() == _LWS_LINES_NAME else 1)

    elif map_type == "GCN Risk Zones Map":
        # Risk-zone polygons (one per county), the national eDNA pond points,
        # and any user-placed water bodies, each only when the user has them
        # visible. Use effective (recursive) visibility so a layer hidden via
        # its group is excluded from the map AND legend.
        for lyr in project.mapLayers().values():
            n = lyr.name()
            if (n.startswith(_GCN_LAYER_PREFIX) or n == _EDNA_LAYER_NAME
                    or n == _POND_LAYER_NAME):
                node = root.findLayer(lyr.id())
                if node is not None and _node_visible_recursive(node):
                    specific.append(lyr)
        # Points render ON TOP of the semi-transparent zone polygons, with the
        # user-placed water bodies above the eDNA survey points.
        specific.sort(key=lambda l: (0 if l.name() == _POND_LAYER_NAME else
                                     1 if l.name() == _EDNA_LAYER_NAME else 2))

    # Fallback: group empty — scan all layers by name prefix/label
    if not specific:
        from .designated_sites import all_ds_labels
        _DS_LABELS = all_ds_labels()
        prefix_map = {
            "Priority Habitats Map": _PHI_LAYER_PREFIX,
            "GCN Risk Zones Map":    _GCN_LAYER_PREFIX,
        }
        prefix = prefix_map.get(map_type)
        if map_type == "Designated Sites Map":
            for lyr in project.mapLayers().values():
                node = root.findLayer(lyr.id())
                if (node is not None and _node_visible_recursive(node)
                        and lyr.name() in _DS_LABELS):
                    specific.append(lyr)
        elif prefix:
            for lyr in project.mapLayers().values():
                node = root.findLayer(lyr.id())
                if (node is not None and _node_visible_recursive(node)
                        and lyr.name().startswith(prefix)):
                    specific.append(lyr)

    # Constraint buffers ride on the matching map (DS buffers → Designated Sites
    # map, habitat buffers → Priority Habitats map). Appended to the END of the
    # data block so the site/habitat polygons stay on top; the ring extends
    # beyond them and stays visible regardless.
    if map_type == "Designated Sites Map":
        specific += _constraint_buffer_layers(root, "ds")
    elif map_type == "Priority Habitats Map":
        specific += _constraint_buffer_layers(root, "phi")

    # Fallback basemap
    if not basemap:
        for lyr in project.mapLayers().values():
            if lyr.name() in BASEMAP_NAMES:
                node = root.findLayer(lyr.id())
                if node is not None and _node_visible_recursive(node):
                    basemap.append(lyr)
                    break

    # Render order (index-0 on top): data features → redline → redline buffer
    # rings → 2 km search buffer → basemap (bottom).
    return specific + redline + redline_buffers + buffer_lyr + basemap


def build_desk_study_layout(iface, map_type, meta):
    """Build and open a desk study print layout.

    map_type: "Designated Sites Map" | "Priority Habitats Map" | "EPSL Map"
              | "GCN Risk Zones Map" | "Pond Map"
    meta:     dict with title, client, proj_no, created, approved
    """
    try:
        _build_desk_study(iface, map_type, meta)
    except Exception as e:
        import traceback
        QgsMessageLog.logMessage(f"Desk study map error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Critical)
        iface.messageBar().pushMessage(
            "Biome", f"Map error: {e}", level=Qgis.MessageLevel.Critical, duration=0
        )


# ── Desk study map type mappings ──────────────────────────────────────────────
DESK_TITLES = {
    "Designated Sites Map":  "Designated Sites",
    "Priority Habitats Map": "Priority Habitats",
    "EPSL Map":              "European Protected Species Licences",
    "TPO Map":               "Tree Preservation Orders",
    "Conservation Areas Map": "Conservation Areas",
    "GCN Risk Zones Map":    "Great Crested Newt Risk Zones",
    "SSSI IRZ Map":          "SSSI Impact Risk Zones",
    "Pond Map":              "Standing Water Bodies",
    "LWS Map":               "Local Wildlife Sites",
}

# Printed sheet title overrides — used only where the on-map title should read
# differently from the shorter name that titles the layout / export file.
DESK_PANEL_TITLES = {
    "Designated Sites Map": "Statutorily Designated Sites",
}

# Shipped legend section headings per desk map — mirrors the literals in the
# desk-legend builder's map_type chain; used by the settings cog's titles
# editor for its placeholders. EPSL has none by design (the gold panel title
# already names the dataset) and so is deliberately absent.
DESK_LEGEND_DEFAULTS = {
    "Designated Sites Map":   "Designated Sites",
    "Priority Habitats Map":  "Priority Habitats",
    "TPO Map":                "Tree Preservation\nOrders",
    "Conservation Areas Map": "Conservation Areas",
    "GCN Risk Zones Map":     "GCN Risk Zones",
    "SSSI IRZ Map":           "SSSI Impact Risk Zones",
    "Pond Map":               "Standing Water Bodies",
    "LWS Map":                "Local Wildlife Sites",
}
DESK_RADIUS_M    = {"Pond Map": 500, "TPO Map": 200}  # built-in fallbacks; others 2000 m
_DESK_DEFRA_MAPS = {"Designated Sites Map", "Priority Habitats Map",
                    "EPSL Map", "SSSI IRZ Map"}
# Desk maps whose data switches to Scottish services for a Scottish site
# (region.py). EPSL and SSSI IRZ have no Scottish source and stay DEFRA.
_DESK_REGIONAL_MAPS = {"Designated Sites Map", "Priority Habitats Map", "TPO Map"}
# Source-mark logo file (in the plugin folder) per Scottish desk map.
_SCOT_DESK_LOGOS = {
    "Designated Sites Map":  "naturescot_logo.png",
    "Priority Habitats Map": "naturescot_logo.png",
    "TPO Map":               "spatialhub_logo.png",
}


def _desk_map_is_scottish(map_type):
    if map_type not in _DESK_REGIONAL_MAPS:
        return False
    try:
        from . import region
        return region.is_scotland()
    except Exception:
        return False


def _desk_source_rows(map_type):
    """Extra ("Source", …) rows for the desk-map side panel. England maps
    carry the DEFRA logo as their source mark; a Scottish map has no logo for
    its sources, so it names them in the panel."""
    # The data-source card now names the source on every desk map, so the
    # extra panel rows would duplicate it.
    if True:
        return []
    if map_type == "Designated Sites Map":
        return [("Source", "NatureScot / Scottish Govt")]
    if map_type == "TPO Map":
        return [("Source", "Spatial Hub Scotland")]
    return [("Source", "NatureScot HabMoS")]


def _desk_radius_for(map_type):
    """The radius (metres) a desk study map should frame — the user-configured
    search radius for that map's data search, falling back to the built-in
    default. Intersect-based maps (GCN risk zones, SSSI IRZ, LWS) have no
    distance search, so they use DESK_RADIUS_M's default too."""
    try:
        from .config import get_radius_for_map_type
        r = get_radius_for_map_type(map_type)
        if r:
            return r
    except Exception:
        pass
    return DESK_RADIUS_M.get(map_type, 2000)
DESK_HEADER_GOLD = "#C99B2E"             # main ROAVR logo colour

# Desk-map header colour per DATA SOURCE (sampled from the live sites,
# 2026-09-06, Tom's picks). Maps drawn from ROAVR's own data (Pond, LWS)
# return None and keep the active Customise theme's header. Title text and
# the north arrow flip to white automatically via panel_renderer._contrast.
DESK_SOURCE_HEADER = {
    "magic":       "#78A4AD",   # MAGIC navbar teal (magic.defra.gov.uk)
    "planning":    "#1D70B8",   # GOV.UK blue (planning.data.gov.uk)
    "ne_gcn":      "#00A33B",   # Defra / Natural England GOV.UK brand green
    "naturescot":  "#2B546E",   # NatureScot heading / link blue (nature.scot)
    "spatialhub":  "#03243D",   # Spatial Hub Scotland brand navy
}


def _desk_header_colour(map_type):
    """Header colour for a desk study map, keyed on the map's data source
    (mirrors _desk_logo's branching, but does NOT depend on the logo file
    being present — the source is the same either way). None → use the
    active theme's header (ROAVR-data maps)."""
    if _desk_map_is_scottish(map_type):
        return DESK_SOURCE_HEADER["spatialhub" if map_type == "TPO Map"
                                  else "naturescot"]
    if map_type in _DESK_DEFRA_MAPS:
        return DESK_SOURCE_HEADER["magic"]
    if map_type in ("TPO Map", "Conservation Areas Map"):
        return DESK_SOURCE_HEADER["planning"]
    if map_type == "GCN Risk Zones Map":
        return DESK_SOURCE_HEADER["ne_gcn"]
    return None


# Per-map dataset line for the source card caption.
_DESK_SOURCE_DATASET = {
    "Designated Sites Map":   "Designated sites",
    "Priority Habitats Map":  "Priority Habitat Inventory",
    "EPSL Map":               "Protected species licences",
    "SSSI IRZ Map":           "SSSI Impact Risk Zones",
    "TPO Map":                "Tree Preservation Orders",
    "Conservation Areas Map": "Conservation Areas",
    "GCN Risk Zones Map":     "GCN District Level Licensing",
}


def _gcn_county_name():
    """The county a loaded GCN Risk Zones layer covers (display form), or ""."""
    try:
        from qgis.core import QgsProject
        grp = QgsProject.instance().layerTreeRoot().findGroup(_GCN_GROUP_NAME)
        if grp is None:
            return ""
        for node in grp.findLayers():
            lyr = node.layer()
            if lyr and lyr.name().startswith("GCN Risk Zones"):
                return (lyr.name().replace("GCN Risk Zones \u2014 ", "")
                        .strip().replace("_", " "))
    except Exception:
        pass
    return ""


def _desk_source_card(map_type, header_col, plugin_dir):
    """The data-source card for a desk study map (see
    panel_renderer._source_card_fragment), or None for maps drawn from
    ROAVR's own data (Pond, LWS), which keep the free theme-logo band.

    Returned dict carries "logo"/"trim" (popped by the caller into the theme)
    plus the card fields. Colour link to the banner: MAGIC sits on the header
    colour itself (its wordmark is dark on the site's teal navbar); every
    other source gets a light tint of the header colour, a keyline in it and
    the caption set in it — full-colour third-party marks keep their own
    colours. The GOV.UK and Natural England marks are authored in their
    brand colour, which IS the header colour for those maps."""
    from . import panel_renderer as pr
    dataset = _DESK_SOURCE_DATASET.get(map_type, "")

    def tinted(logo, title, lines, trim=True):
        return {"logo": logo, "trim": trim,
                "fill": pr.tint(header_col), "keyline": header_col,
                "ink": header_col, "title": title, "lines": lines}

    if _desk_map_is_scottish(map_type):
        if map_type == "TPO Map":
            return tinted(os.path.join(plugin_dir, "spatialhub_logo.png"),
                          "Spatial Hub Scotland", [dataset])
        if map_type == "Priority Habitats Map":
            return tinted(os.path.join(plugin_dir, "naturescot_logo.png"),
                          "NatureScot", ["Habitat Map of Scotland"])
        return tinted(os.path.join(plugin_dir, "naturescot_logo.png"),
                      "NatureScot", [dataset, "Scottish Government NSA / National Parks"])
    if map_type in _DESK_DEFRA_MAPS:
        return {"logo": os.path.join(plugin_dir, "magic_logo.svg"), "trim": True,
                "fill": header_col, "keyline": None, "ink": None,
                "title": "MAGIC", "lines": ["Defra / Natural England", dataset]}
    if map_type in ("TPO Map", "Conservation Areas Map"):
        return tinted(os.path.join(plugin_dir, "planning_data_logo.svg"),
                      "Planning Data", ["planning.data.gov.uk", dataset])
    if map_type == "GCN Risk Zones Map":
        county = _gcn_county_name()
        return tinted(os.path.join(plugin_dir, "natural_england_logo.svg"),
                      "Natural England", [dataset] + ([county] if county else []))
    return None


def _desk_logo(map_type, plugin_dir):
    """Return (logo_path, trim) for a desk study map's single logo.

    DEFRA / planning.gov / NE-county logos are fixed data-source marks.
    Pond and LWS maps use the active theme's (ROAVR) logo so they track
    whatever the final map is set to. (A bespoke LERC logo for LWS is a
    future enhancement; ROAVR is the fallback for now.)

    A Scottish site's designated-sites / priority-habitats maps are drawn
    from NatureScot and Scottish Government data, so the DEFRA mark would be
    wrong: they take the theme logo and the panel names the source instead
    (see _desk_source_rows)."""
    # Scottish sites: the Scottish source marks — NatureScot for designated
    # sites / Habitat Map of Scotland, Spatial Hub Scotland for TPOs — when
    # the logo files are present; otherwise the theme logo (the panel's
    # Source row still names the source).
    if _desk_map_is_scottish(map_type):
        p = os.path.join(plugin_dir, _SCOT_DESK_LOGOS.get(map_type, ""))
        if os.path.isfile(p):
            return p, True
    elif map_type in _DESK_DEFRA_MAPS:
        return os.path.join(plugin_dir, "DEFRA_logo.png"), True
    if map_type in ("TPO Map", "Conservation Areas Map"):
        p = os.path.join(plugin_dir, "planning_data_logo.png")
        return (p if os.path.isfile(p)
                else os.path.join(plugin_dir, "DEFRA_logo.png")), True
    if map_type == "GCN Risk Zones Map":
        c = _gcn_county_logo_path(plugin_dir)
        return (c if (c and os.path.isfile(c))
                else os.path.join(plugin_dir, "DEFRA_logo.png")), True
    # Pond Map and LWS Map → active theme (ROAVR) logo.
    try:
        from . import branding
        th = branding.active_theme() or dict(branding.DEFAULT_THEME)
        return th.get("logo", ""), bool(th.get("trimLogo"))
    except Exception:
        return os.path.join(plugin_dir, "roavr_logo.png"), True


def _fit_to_search_area(map_item, project, layers, radius_m, margin_frac=0.04):
    """Zoom the map to the data-search area: the redline buffered by radius_m,
    aspect-matched to the frame. Falls back to the union of the data layers
    when there is no redline. The buffer is applied in British National Grid
    (EPSG:27700, a true metric CRS) — NOT the redline's own CRS, which is
    usually EPSG:3857 where a metre is not a ground metre — so the framed
    radius is honoured on the ground; a large redline still yields a search
    area wider than the nominal radius."""
    from qgis.core import (
        QgsRectangle, QgsCoordinateTransform, QgsCoordinateReferenceSystem,
    )

    map_crs = map_item.crs()

    def _reproj(extent, src_crs):
        if (src_crs and src_crs.isValid() and map_crs.isValid()
                and src_crs.authid() != map_crs.authid()):
            return QgsCoordinateTransform(src_crs, map_crs, project) \
                .transformBoundingBox(extent)
        return QgsRectangle(extent)

    base = None
    margin = margin_frac

    redline = None
    for lyr in (layers or []):
        if lyr.name() == _REDLINE_NAME:
            redline = lyr
            break
    if redline is None:
        for lyr in project.mapLayersByName(_REDLINE_NAME):
            redline = lyr
            break

    # 1. Preferred: the redline boundary buffered by radius_m. This is the
    #    authoritative search area and honours whatever radius the search used
    #    (including a user-customised one), so the frame always includes the full
    #    search distance — NOT a stale "Search Buffer" ring that may have been
    #    drawn at a different radius. The buffer MUST be applied in a true metric
    #    CRS: the redline layer is usually EPSG:3857 (Web Mercator), whose
    #    "metres" are inflated ~1/cos(latitude) (≈1.6× in the UK), so buffering
    #    radius_m in those units covers only ~radius_m·cos(lat) ground metres —
    #    cropping the search area. Buffer in British National Grid (EPSG:27700)
    #    instead, then reproject the result into the map CRS.
    if redline is not None:
        try:
            if not redline.extent().isEmpty():
                bng = QgsCoordinateReferenceSystem("EPSG:27700")
                e = redline.extent()
                if (redline.crs().isValid() and bng.isValid()
                        and redline.crs().authid() != bng.authid()):
                    e = QgsCoordinateTransform(redline.crs(), bng, project) \
                        .transformBoundingBox(e)
                e = QgsRectangle(e.xMinimum() - radius_m, e.yMinimum() - radius_m,
                                 e.xMaximum() + radius_m, e.yMaximum() + radius_m)
                base = _reproj(e, bng)
                margin = 0.02
        except Exception:
            base = None

    # 2. No usable redline — frame the search-buffer ring if the map carries one.
    if base is None:
        for lyr in (layers or []):
            if lyr.name() == _BUFFER_LAYER_NAME:
                try:
                    if not lyr.extent().isEmpty():
                        base = _reproj(lyr.extent(), lyr.crs())
                        margin = 0.02          # breathing room around the ring
                except Exception:
                    base = None
                break

    # 3. Last resort — the union of the non-basemap data layers.
    if base is None:
        for lyr in (layers or []):
            try:
                if _is_basemap_layer(lyr):
                    continue
                e = lyr.extent()
                if e.isEmpty():
                    continue
                if lyr.crs().isValid() and map_crs.isValid() and \
                        lyr.crs().authid() != map_crs.authid():
                    e = QgsCoordinateTransform(lyr.crs(), map_crs, project) \
                        .transformBoundingBox(e)
                if base is None:
                    base = QgsRectangle(e)
                else:
                    base.combineExtentWith(e)
            except Exception:
                continue
    if base is None or base.isEmpty():
        return False

    mw, mh = max(base.width(), 1e-6), max(base.height(), 1e-6)
    base = QgsRectangle(base.xMinimum() - mw * margin,
                        base.yMinimum() - mh * margin,
                        base.xMaximum() + mw * margin,
                        base.yMaximum() + mh * margin)
    try:
        fw, fh = map_item.rect().width(), map_item.rect().height()
        frame_ratio = (fw / fh) if fh else (MAP_W_MM / 210.0)
    except Exception:
        frame_ratio = MAP_W_MM / 210.0
    ew, eh = base.width(), base.height()
    if ew / eh < frame_ratio:
        tw = eh * frame_ratio
        cx = base.center().x()
        base.setXMinimum(cx - tw / 2.0)
        base.setXMaximum(cx + tw / 2.0)
    else:
        th = ew / frame_ratio
        cy = base.center().y()
        base.setYMinimum(cy - th / 2.0)
        base.setYMaximum(cy + th / 2.0)

    map_item.zoomToExtent(base)
    return True


def _build_desk_study(iface, map_type, meta):
    import os
    from qgis.core import QgsProject, QgsPrintLayout
    from qgis.PyQt.QtCore import QTimer, QRectF
    from . import final_layout
    from . import panel_renderer as pr

    project    = QgsProject.instance()
    manager    = project.layoutManager()
    plugin_dir = os.path.dirname(os.path.abspath(__file__))

    # A session-only rename (meta["title"], set by right-clicking the map button)
    # overrides both the layout name and the printed sheet title. Beneath it,
    # desk_map_title/desk_panel_title fold in any persistent reword from the
    # settings cog before falling back to the shipped defaults.
    override     = (meta.get("title") or "").strip()
    title        = override or desk_map_title(map_type)
    panel_title  = override or desk_panel_title(map_type)  # printed sheet title
    radius       = _desk_radius_for(map_type)
    if radius < 1000:
        radius_label = f"{radius} m"
    elif radius % 1000 == 0:
        radius_label = f"{radius // 1000} km"
    else:
        radius_label = f"{radius / 1000:.1f} km"

    # Fresh code-built layout (same engine + page sizing as the final map).
    lname    = _final_map_name(title)
    existing = manager.layoutByName(lname)
    if existing:
        manager.removeLayout(existing)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(lname)

    items    = final_layout.build(layout, project)
    map_item = items["map"]
    panel    = items["panel"]
    legend   = items["legend"]
    sb       = items["scalebar"]

    # Render in the canvas/project CRS so the loaded basemap (e.g. OS offline
    # in EPSG:27700) draws — forcing EPSG:3857 leaves an OS raster blank.
    try:
        map_item.setCrs(iface.mapCanvas().mapSettings().destinationCrs())
    except Exception:
        pass

    # ── Layers + fit to the search area ───────────────────────────────────
    # Heal duplicate fetched layers (and newline-damaged names) left in the
    # project by earlier builds, before the legend mirrors the layer tree.
    _remove_duplicate_desk_layers(project)
    layers = _get_desk_study_layers(project, map_type)
    if map_item and layers:
        map_item.setLayers(layers)        # already correctly ordered
        map_item.setKeepLayerSet(True)
        map_item.setDrawAnnotations(False)
        if map_type == "Pond Map":
            # ID-only labels on the print map; distances live in the sidebar
            # list. The live canvas keeps ID + distance.
            _pond_map_label_override(map_item, layers)
        _fit_to_search_area(map_item, project, layers, radius)
        try:
            pg = layout.pageCollection().page(0).pageSize()
            PW, PH = pg.width(), pg.height()
            if PW < PH:
                PW, PH = max(PW, PH), min(PW, PH)
            map_item.attemptSetSceneRect(QRectF(0.0, 0.0, PW - pr.PANEL_W_MM, PH))
        except Exception:
            pass

    # ── Desk theme: header colour follows the DATA SOURCE's brand colour
    #    (DESK_SOURCE_HEADER); ROAVR-data maps (Pond, LWS) follow the final
    #    map's Customise theme. Text auto contrast-checked; single logo per
    #    map type. ──────────────────────────────────────────────────────────
    header_col = _desk_header_colour(map_type)
    if not header_col:
        header_col = DESK_HEADER_GOLD
        try:
            from . import branding
            header_col = (branding.active_theme() or {}).get("header") or DESK_HEADER_GOLD
        except Exception:
            pass
    logo_path, trim = _desk_logo(map_type, plugin_dir)
    # Third-party sources get the uniform source card (own mark + caption,
    # colour-linked to the header); ROAVR-data maps keep the theme logo band.
    source_card = _desk_source_card(map_type, header_col, plugin_dir)
    if source_card:
        logo_path = source_card.pop("logo")
        trim = source_card.pop("trim")
    # The logo band size follows the active branding theme, so the DEFRA /
    # theme logo on a desk map shrinks with the "Logo size" setting too.
    try:
        from . import branding as _br
        logo_scale = (_br.active_theme() or {}).get("logoScale") or pr.LOGO_SCALE
    except Exception:
        logo_scale = pr.LOGO_SCALE
    theme = {
        "header":    header_col,
        "titleText": pr._contrast(header_col),
        "bodyText":  "#2A2417",
        "logo":      logo_path,
        "trimLogo":  trim,
        "logoScale": logo_scale,
        "sourceCard": source_card,
    }

    # ── Minimal panel: title, radius subtitle, scale, date, legend ────────
    today      = _config.format_date()
    panel_meta = {
        "title":        panel_title,
        "subtitle":     f"Search radius: {radius_label}",
        "ref_line":     "",
        "date_text":    today,
        # Blank value — a live layout label renders the TRUE ground-scale
        # ratio (see _add_dynamic_scale_label), tracking zoom in the designer.
        "scale_text":   "",
        "rows":         ([("Scale @ A4", ""), ("Date", today)]
                         + _desk_source_rows(map_type)),
        "show_footnote": False,
        "attribution":  _imagery_credit(map_item.layers() if map_item else []),
    }
    # First pass — gives the legend gap anchor (top of the gap).
    svg_path, leg_rect = pr.render_panel(theme, panel_meta, plugin_dir)

    # ── Legend: anchor in the gap, then build with per-type compression ───
    legend_bottom_mm = None
    if legend:
        from qgis.core import QgsLayoutPoint, QgsUnitTypes
        _mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters
        if leg_rect:
            lx, ly, _lw, _lh = leg_rect
            legend.attemptMove(QgsLayoutPoint(lx, ly, _mm))
        legend.setLinkedMap(map_item)
        try:
            legend.setLegendFilterByMapEnabled(False)
        except Exception:
            pass
        if map_type == "Pond Map":
            legend.setAutoUpdateModel(False)
        else:
            legend.setAutoUpdateModel(True)
            legend.updateLegend()
            legend.setAutoUpdateModel(False)
        _build_desk_study_legend(legend, layers, map_type)
        # Measure the legend's actual bottom (with a cache-refresh + safety
        # margin so a legend that renders taller than adjustBoxSize reported
        # doesn't get overlapped by the Scale/Date block).
        legend_bottom_mm = _legend_bottom_mm(legend)

        # If the legend is too tall to leave room for Scale/Date above the
        # footer/logo, restyle it at the most compact tier and re-measure.
        try:
            limit_mm = pr.legend_bottom_limit_mm(theme, panel_meta)
        except Exception:
            limit_mm = None
        if (legend_bottom_mm is not None and limit_mm is not None
                and legend_bottom_mm > limit_mm):
            try:
                model = legend.model()
                lroot = model.rootGroup()
                spec = [n.layer() for n in lroot.findLayers()
                        if n.layer() and not _is_desk_support_layer(n.layer().name())]
                supp = [n.layer() for n in lroot.findLayers()
                        if n.layer() and _is_desk_support_layer(n.layer().name())]
                _apply_legend_style(legend, lroot, model, spec, supp,
                                    map_type, force_small=True)
                legend_bottom_mm = _legend_bottom_mm(legend)
            except Exception:
                pass

        # Even the smallest tier can't hold a long key (a Pond Map with 15+
        # water bodies) in one column — split it into two, re-wrapping the
        # labels for the narrower column width.
        if (legend_bottom_mm is not None and limit_mm is not None
                and legend_bottom_mm > limit_mm):
            try:
                lw = leg_rect[2] if leg_rect else pr.PANEL_W_MM - 8.0
                _desk_legend_two_columns(legend, lw / 2.0)
                legend_bottom_mm = _legend_bottom_mm(legend)
                # A key that ended up wider than the page is no better than a
                # tall one — undo the split and let the next fallback run.
                pos, sz = legend.positionWithUnits(), legend.sizeWithUnits()
                if pos.x() + sz.width() > pr.PAGE_W_MM - 1.0:
                    legend.setColumnCount(1)
                    legend.setSplitLayer(False)
                    legend_bottom_mm = _legend_bottom_mm(legend)
            except Exception:
                pass

        # Pond Map: the legend shows the water-body icon once with a count;
        # the per-pond distances go in a plain text list below it, sized to
        # stop above the metadata block.
        if map_type == "Pond Map" and leg_rect:
            try:
                list_bottom = _add_pond_list_label(
                    layout, leg_rect, legend_bottom_mm, limit_mm, theme,
                    bool((meta or {}).get("include_pond_hsi_status", False)))
                if list_bottom is not None:
                    legend_bottom_mm = max(legend_bottom_mm or 0.0, list_bottom)
                gcn_key_bottom = _add_pond_gcn_key(
                    layout, leg_rect, legend_bottom_mm, theme)
                if gcn_key_bottom is not None:
                    legend_bottom_mm = max(
                        legend_bottom_mm or 0.0, gcn_key_bottom)
            except Exception:
                QgsMessageLog.logMessage(
                    f"Pond list error:\n{traceback.format_exc()}",
                    "Biome", Qgis.MessageLevel.Warning)

        # Multi-category searches should not list every possible absent
        # category. One quiet status line beneath the surviving boundary key is
        # clearer and cannot be mistaken for mapped legend classes.
        empty_status = (meta or {}).get("empty_status")
        if empty_status and leg_rect:
            try:
                status_bottom = _add_empty_desk_status_label(
                    layout, leg_rect, legend_bottom_mm, empty_status, theme)
                if status_bottom is not None:
                    legend_bottom_mm = max(legend_bottom_mm or 0.0,
                                           status_bottom)
            except Exception:
                QgsMessageLog.logMessage(
                    f"Empty desk-map status error:\n{traceback.format_exc()}",
                    "Biome", Qgis.MessageLevel.Warning)

    # Second pass — push Scale/Date to just below the (now-sized) legend.
    if legend_bottom_mm is not None:
        panel_meta["legend_bottom_au"] = legend_bottom_mm * pr._U_PER_MM
        svg_path, _ = pr.render_panel(theme, panel_meta, plugin_dir)
    if panel:
        panel.setPicturePath(svg_path)

    # ── Scale bar: 2-segment box, fit to panel width ──────────────────────
    if sb and map_item:
        sb.setLinkedMap(map_item)
        final_layout.fit_scalebar(sb, map_item, target_mm=pr.PANEL_W_MM - 22.0, max_mm=pr.PANEL_W_MM - 8.0)
        final_layout.bind_scalebar_autofit(sb, map_item, target_mm=pr.PANEL_W_MM - 22.0, max_mm=pr.PANEL_W_MM - 8.0)

    # Live ground-true scale label keeps the ratio in sync with zoom.
    if map_item:
        _add_dynamic_scale_label(layout, map_item, theme, panel_meta)

    manager.addLayout(layout)
    QTimer.singleShot(200, lambda: iface.openLayoutDesigner(layout))


# ── De minimis helpers ────────────────────────────────────────────────────────

DEMINIMIS_LOSS_LAYER = "⚠ Habitat Loss Areas"

def _get_deminimis_layers(project):
    """
    Return the ordered layer list for a de minimis assessment map.
    Includes: loss layer (top), baseline area + hedgerow + trees,
    proposed area + hedgerow + trees, redline, basemap.
    """
    root    = project.layerTreeRoot()
    loss    = []
    bl      = []
    prop    = []
    redline = []

    # Loss layer — sits on top
    for lyr in project.mapLayers().values():
        if lyr.name() == DEMINIMIS_LOSS_LAYER:
            loss.append(lyr)

    # Baseline layers — walk group in tree order
    for group_name in ("Baseline Habitats", "Baseline - Area Habitats",
                       "Baseline - Hedgerow Units", "Baseline - Watercourse Habitat",
                       "Baseline - Individual Trees"):
        grp = root.findGroup(group_name)
        if grp is not None:
            for node in grp.findLayers():
                lyr = node.layer()
                if lyr and lyr not in bl:
                    bl.append(lyr)
        else:
            for lyr in project.mapLayers().values():
                if lyr.name() == group_name and lyr not in bl:
                    bl.append(lyr)

    # Fallback: collect by name prefix from tree
    if not bl:
        def _walk_bl(node):
            if isinstance(node, QgsLayerTreeLayer):
                lyr = node.layer()
                if lyr and "Baseline" in lyr.name() and lyr not in bl:
                    bl.append(lyr)
            elif isinstance(node, QgsLayerTreeGroup):
                for child in node.children():
                    _walk_bl(child)
        _walk_bl(root)

    # Proposed layers
    for group_name in ("Proposed Habitats", "Proposed - Area Habitats",
                       "Proposed - Hedgerow Units", "Proposed - Watercourse Habitat",
                       "Proposed - Individual Trees"):
        grp = root.findGroup(group_name)
        if grp is not None:
            for node in grp.findLayers():
                lyr = node.layer()
                if lyr and lyr not in prop:
                    prop.append(lyr)
        else:
            for lyr in project.mapLayers().values():
                if lyr.name() == group_name and lyr not in prop:
                    prop.append(lyr)

    if not prop:
        def _walk_pr(node):
            if isinstance(node, QgsLayerTreeLayer):
                lyr = node.layer()
                if lyr and "Proposed" in lyr.name() and lyr not in prop:
                    prop.append(lyr)
            elif isinstance(node, QgsLayerTreeGroup):
                for child in node.children():
                    _walk_pr(child)
        _walk_pr(root)

    # Redline
    for lyr in project.mapLayers().values():
        if is_redline_name(lyr.name()):
            redline.append(lyr)

    basemap = _get_visible_basemaps(project)

    # Render order: loss on top, then baseline, then proposed, redline, basemap
    return redline + loss + bl + prop + basemap


def _build_deminimis_legend(root):
    """
    Organise the de minimis legend into three groups:
    Habitat Loss, Baseline Habitats, Proposed Habitats.
    Basemap layers are already excluded by the caller.
    """
    # Collect all current layer nodes
    all_nodes = []
    def _collect(node):
        if isinstance(node, QgsLayerTreeLayer):
            lyr = node.layer()
            if lyr:
                all_nodes.append((lyr.name(), node.clone()))
        elif isinstance(node, QgsLayerTreeGroup):
            for child in node.children():
                _collect(child)
    for child in root.children():
        _collect(child)

    # Clear root
    for child in root.children()[:]:
        root.removeChildNode(child)

    # Bucket into groups
    loss_nodes     = [(n, nd) for n, nd in all_nodes if DEMINIMIS_LOSS_LAYER in n]
    baseline_nodes = [(n, nd) for n, nd in all_nodes if "Baseline" in n]
    proposed_nodes = [(n, nd) for n, nd in all_nodes if "Proposed" in n]
    other_nodes    = [(n, nd) for n, nd in all_nodes
                      if DEMINIMIS_LOSS_LAYER not in n
                      and "Baseline" not in n
                      and "Proposed" not in n]

    def _add_group(label, nodes):
        if not nodes:
            return
        # Add nodes directly without a named group wrapper —
        # the layer names themselves serve as the legend headers
        for _, nd in nodes:
            root.addChildNode(nd)

    # Redline first, then habitat groups
    redline_nodes = [(n, nd) for n, nd in other_nodes if "Redline" in n]
    non_redline   = [(n, nd) for n, nd in other_nodes if "Redline" not in n]

    for _, nd in redline_nodes:
        root.addChildNode(nd)
    _add_group("Habitat Loss", loss_nodes)
    _add_group("Baseline Habitats", baseline_nodes)
    _add_group("Proposed Habitats", proposed_nodes)
    for _, nd in non_redline:
        root.addChildNode(nd)
