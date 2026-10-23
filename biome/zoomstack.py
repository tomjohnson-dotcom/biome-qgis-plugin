# zoomstack.py
"""
OS Open Zoomstack — offline Ordnance Survey basemap for Biome.

Downloads the free OS Open Zoomstack GeoPackage (whole of GB, EPSG:27700)
via the keyless OS Downloads API, plus the official OS QGIS stylesheets,
and loads it as a styled basemap group.

Storage is OUTSIDE the plugin folder — in the QGIS user profile at
    <profile>/biome_basemaps/
so removing / reinstalling the plugin never deletes the ~12 GB download.
The folder can be changed by the user; the choice is remembered in
QgsSettings under "biome/zoomstack_dir".

Sizes (per OS technical spec): ~3.8 GB zipped download, ~11.8 GB unzipped.
Licence: Open Government Licence — free for any use, including client
reports, with OS attribution.
"""

import os
import json
import shutil
import zipfile

from qgis.PyQt.QtCore import QObject, QUrl, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QProgressDialog, QMessageBox, QFrame, QWidget,
)
from qgis.core import (
    QgsApplication, QgsSettings, QgsProject, QgsVectorLayer,
    QgsMessageLog, Qgis, QgsTask, QgsFileDownloader,
    QgsBlockingNetworkRequest, QgsProviderRegistry,
)
from qgis.PyQt.QtNetwork import QNetworkRequest

ZOOMSTACK_GROUP   = "OS Zoomstack (offline)"
_SETTINGS_DIR_KEY = "biome/zoomstack_dir"
_DOWNLOADS_API    = "https://api.os.uk/downloads/v1/products/OpenZoomstack/downloads"
# Fallback if the API listing can't be reached (URL format is stable)
_FALLBACK_URL     = (_DOWNLOADS_API + "?area=GB&format=GeoPackage&redirect")
_STYLES_ZIP_URL   = ("https://codeload.github.com/OrdnanceSurvey/"
                     "OS-Open-Zoomstack-Stylesheets/zip/refs/heads/master")

# GeoPackage layer draw order, bottom -> top (per OS cartography)
_LAYER_ORDER = [
    "land", "urban_areas", "sites", "national_parks", "greenspace",
    "woodland", "surfacewater", "waterlines", "foreshore", "contours",
    "boundaries", "etl", "rail", "roads_local", "roads_regional",
    "roads_national", "district_buildings", "local_buildings",
    "railway_stations", "airports", "names",
]

_LOG = "Biome"


# ---------------------------------------------------------------------------
# Storage location (persistent across plugin reinstalls)
# ---------------------------------------------------------------------------
def default_storage_dir() -> str:
    """Folder in the QGIS user profile — NOT inside python/plugins, so it
    survives plugin removal and reinstallation."""
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "biome_basemaps")


def storage_dir() -> str:
    """The folder holding (or destined to hold) the Zoomstack data.

    Data-first: a location that actually contains the ~12 GB download always
    wins, so a stale saved path (e.g. an old build's plugins/biome_data) or a
    fresh profile (e.g. first run under QGIS 4) can't trigger a re-download
    while a sibling profile already has the data. Only when nothing has data
    does the saved path / default act as the download destination."""
    s = QgsSettings()
    path = s.value(_SETTINGS_DIR_KEY, "", type=str)
    if path and _dir_has_zoomstack(path):
        return path
    default = default_storage_dir()
    if _dir_has_zoomstack(default):
        return default
    legacy = _legacy_storage_dir()
    if legacy:
        # Persisted, so the probe only runs until data exists somewhere.
        set_storage_dir(legacy)
        return legacy
    return path if path else default


def _dir_has_zoomstack(d) -> bool:
    if not d or not os.path.isdir(d):
        return False
    for root, _dirs, files in os.walk(d):
        for f in files:
            if f.lower().endswith(".gpkg") and "zoomstack" in f.lower():
                return True
    return False


def _legacy_storage_dir():
    """An older install's data folder holding the Zoomstack download — e.g. the
    QGIS3 profile's when running under QGIS 4 — or None."""
    try:
        prof = os.path.normpath(QgsApplication.qgisSettingsDirPath())
    except Exception:
        return None
    parts = prof.split(os.sep)
    for i, part in enumerate(parts):
        if part not in ("QGIS3", "QGIS4"):
            continue
        for alt in ("QGIS3", "QGIS4"):
            if alt == part:
                continue
            sib_prof = os.sep.join(parts[:i] + [alt] + parts[i + 1:])
            # A custom folder recorded in that profile's settings first, then
            # its default data folders (roavr_basemaps = pre-rename builds).
            candidates = [_ini_zoomstack_dir(sib_prof)]
            candidates += [os.path.join(sib_prof, n)
                           for n in ("biome_basemaps", "roavr_basemaps")]
            for d in candidates:
                if d and _dir_has_zoomstack(d):
                    return d
        break
    return None


def _ini_zoomstack_dir(profile_dir):
    """biome/zoomstack_dir from another profile's settings ini, or None."""
    import configparser
    for ini_name in ("QGIS3.ini", "QGIS4.ini"):
        ini = os.path.join(profile_dir, "QGIS", ini_name)
        if not os.path.exists(ini):
            continue
        try:
            cp = configparser.RawConfigParser()
            cp.read(ini, encoding="utf-8")
            val = cp.get("biome", "zoomstack_dir", fallback="")
            if val:
                return val
        except Exception:
            continue
    return None


def set_storage_dir(path: str):
    QgsSettings().setValue(_SETTINGS_DIR_KEY, path)


def find_gpkg():
    """Return the path of an installed Zoomstack GeoPackage, or None."""
    d = storage_dir()
    if not os.path.isdir(d):
        return None
    for root, _dirs, files in os.walk(d):
        for f in files:
            if f.lower().endswith(".gpkg") and "zoomstack" in f.lower():
                return os.path.join(root, f)
    return None


def is_installed() -> bool:
    return find_gpkg() is not None


DATA_ZIP_NAME = "os_open_zoomstack_gpkg.zip"


def data_zip_path() -> str:
    """Where the OS data zip is saved during download."""
    return os.path.join(storage_dir(), DATA_ZIP_NAME)


def have_complete_data_zip() -> bool:
    """True if a fully-downloaded data zip is already sitting on disk.

    zipfile.is_zipfile() reads the End-Of-Central-Directory record at the
    tail of the file, so a half-finished / truncated download fails this
    check cheaply — without scanning the whole ~4 GB. The size guard rules
    out stray small files."""
    p = data_zip_path()
    try:
        return (os.path.isfile(p)
                and os.path.getsize(p) > 1_000_000_000
                and zipfile.is_zipfile(p))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session-only handling — the basemap is never persisted in project files
# ---------------------------------------------------------------------------
# The 21 Zoomstack layers used to be written into the QGIS project like any other
# layer, pointing at wherever the GeoPackage lived at save time (often as a
# relative path, or an old build's roavr_basemaps / plugins/biome_data
# folder). Move the project or the data and every reopen threw the "Handle
# Unavailable Layers" dialog. The basemap is re-loadable in one click, so:
# new saves are stripped of it (bng_plugin._strip_session_layers_from_file),
# broken copies in old saves are removed after load, and a path preprocessor
# repoints old saves' stale references at the current install so the dialog
# never appears while the data exists.

def is_zoomstack_source(src) -> bool:
    """True if an ogr source string reads from a Zoomstack GeoPackage."""
    if not src:
        return False
    base = os.path.basename(str(src).split("|")[0]).lower()
    return base.endswith(".gpkg") and "zoomstack" in base


def project_zoomstack_layer_ids():
    """Ids of every Zoomstack layer in the current project (valid or not)."""
    ids = set()
    for lid, lyr in QgsProject.instance().mapLayers().items():
        try:
            if is_zoomstack_source(lyr.source()):
                ids.add(lid)
        except Exception:
            continue
    return ids


def cleanup_broken_project_layers() -> int:
    """Remove Zoomstack layers that reopened broken from an old save (data
    moved / not installed on this machine), pruning any groups this empties.
    The basemap is session-only now — reload it from the panel when wanted.
    Returns the number of layers removed."""
    proj = QgsProject.instance()
    root = proj.layerTreeRoot()
    doomed = []
    for lid, lyr in list(proj.mapLayers().items()):
        try:
            if not lyr.isValid() and is_zoomstack_source(lyr.source()):
                doomed.append(lid)
        except Exception:
            continue
    if not doomed:
        return 0
    parents = []
    for lid in doomed:
        node = root.findLayer(lid)
        if node is not None and node.parent() is not None:
            parents.append(node.parent())
    for lid in doomed:
        proj.removeMapLayer(lid)
    # Prune only groups the removal emptied — never pre-existing empty user
    # groups, and never the root.
    for grp in parents:
        try:
            while (grp is not None and grp.parent() is not None
                   and len(grp.children()) == 0):
                parent = grp.parent()
                parent.removeChildNode(grp)
                grp = parent
        except Exception:
            continue
    QgsMessageLog.logMessage(
        f"Zoomstack: removed {len(doomed)} broken basemap layer(s) from an "
        "old save (the basemap is no longer stored in projects)",
        _LOG, Qgis.MessageLevel.Info)
    return len(doomed)


_pp_id = None          # QgsPathResolver preprocessor handle
_pp_gpkg = None        # cached find_gpkg() result (21 layers per project read)


def _cached_gpkg():
    global _pp_gpkg
    if _pp_gpkg and os.path.isfile(_pp_gpkg):
        return _pp_gpkg
    _pp_gpkg = find_gpkg()
    return _pp_gpkg


def _path_preprocessor(path):
    """Rewrite any Zoomstack GeoPackage reference read from a project file to
    the currently-installed copy, keeping the |layername=… suffix. Old saves
    then load cleanly instead of raising 'Handle Unavailable Layers'. Leaves
    the path untouched when no install exists."""
    try:
        file_part, sep, rest = str(path).partition("|")
        if not is_zoomstack_source(file_part):
            return path
        gpkg = _cached_gpkg()
        if not gpkg:
            return path
        if os.path.normcase(os.path.normpath(file_part)) == \
                os.path.normcase(os.path.normpath(gpkg)):
            return path
        return gpkg + sep + rest
    except Exception:
        return path


def install_path_preprocessor():
    """Register the stale-path fixer with QgsPathResolver (idempotent)."""
    global _pp_id
    if _pp_id is not None:
        return
    try:
        from qgis.core import QgsPathResolver
        _pp_id = QgsPathResolver.setPathPreprocessor(_path_preprocessor)
    except Exception:
        _pp_id = None


def remove_path_preprocessor():
    global _pp_id
    if _pp_id is None:
        return
    try:
        from qgis.core import QgsPathResolver
        QgsPathResolver.removePathPreprocessor(_pp_id)
    except Exception:
        pass
    _pp_id = None


# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------
def _norm(name: str) -> str:
    return (name.lower().replace(" ", "").replace("_", "")
                .replace("-", "").replace("oszoomstack", ""))


def _find_qml_map(style_pref: str = "outdoor"):
    """Walk the stylesheets folder and build {normalised layer name: qml path}.
    Prefers QMLs whose path mentions the requested style (e.g. 'outdoor')."""
    base = os.path.join(storage_dir(), "stylesheets")
    if not os.path.isdir(base):
        return {}
    preferred, fallback = {}, {}
    for root, _dirs, files in os.walk(base):
        for f in files:
            if not f.lower().endswith(".qml"):
                continue
            key  = _norm(os.path.splitext(f)[0])
            path = os.path.join(root, f)
            if style_pref in root.lower():
                preferred.setdefault(key, path)
            else:
                fallback.setdefault(key, path)
    merged = dict(fallback)
    merged.update(preferred)
    return merged


def _bundled_symbols_dir():
    """Folder of OS Zoomstack marker SVGs shipped inside the plugin, so the
    railway-station / airport / underground markers always resolve even if the
    downloaded stylesheets don't include them."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "zoomstack_symbols")


def _register_svg_paths():
    """Add the bundled symbol folder and any extracted OS symbol folders to
    QGIS's SVG search paths so the QML styles can find their marker symbols."""
    svg_dirs = set()
    bundled = _bundled_symbols_dir()
    if os.path.isdir(bundled):
        svg_dirs.add(bundled)
    base = os.path.join(storage_dir(), "stylesheets")
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            if any(f.lower().endswith(".svg") for f in files):
                svg_dirs.add(root)
    if not svg_dirs:
        return
    s = QgsSettings()
    current = s.value("svg/searchPathsForSVG") or []
    if isinstance(current, str):
        current = [current]
    changed = False
    for d in sorted(svg_dirs):
        if d not in current:
            current.append(d)
            changed = True
    if changed:
        s.setValue("svg/searchPathsForSVG", current)
    # Also push into the RUNNING session — QgsApplication caches svgPaths at
    # startup, so a settings write alone won't resolve markers until restart.
    try:
        from qgis.core import QgsApplication
        live = list(QgsApplication.svgPaths())
        added = False
        for d in sorted(svg_dirs):
            if d not in live:
                live.append(d)
                added = True
        if added:
            QgsApplication.setSvgPaths(live)
    except Exception:
        pass


def _svg_basename_index():
    """Map every available SVG's lowercase filename to its full path (bundled
    symbols first, then the downloaded stylesheets), so markers pointing at a
    stale/absolute path can be relocated by filename."""
    idx = {}
    for base in (_bundled_symbols_dir(),
                 os.path.join(storage_dir(), "stylesheets")):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".svg"):
                    idx.setdefault(f.lower(), os.path.join(root, f))
    return idx


def _load_named_style_with_font(lyr, qml_path):
    """Load a Zoomstack QML style, substituting the OS stylesheets' hardcoded
    'Source Sans Pro' label font — which isn't installed on most systems and
    triggers a "Font … not available" warning on every basemap load — for the
    bundled, already-registered 'Inter' family. Keeps typography consistent and
    silences the warning. Falls back to a plain load on any problem."""
    try:
        with open(qml_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        try:
            lyr.loadNamedStyle(qml_path)
        except Exception:
            pass
        return
    if "Source Sans Pro" not in text:
        lyr.loadNamedStyle(qml_path)
        return
    text = text.replace("Source Sans Pro", "Inter")
    try:
        try:
            from qgis.PyQt.QtXml import QDomDocument
        except ImportError:
            from qgis.PyQt.QtXml import QDomDocument
        doc = QDomDocument()
        if doc.setContent(text):
            lyr.importNamedStyle(doc)
        else:
            lyr.loadNamedStyle(qml_path)
    except Exception:
        lyr.loadNamedStyle(qml_path)


def _repair_layer_svg_markers(layer, svg_index):
    """Fix the '?' markers: for every SVG marker whose file can't be found,
    relocate it by filename within the stylesheet folder, or fall back to a
    simple marker so the point still renders cleanly."""
    from qgis.core import (QgsRenderContext, QgsSvgMarkerSymbolLayer,
                           QgsSimpleMarkerSymbolLayer)
    from qgis.PyQt.QtGui import QColor
    r = layer.renderer()
    if r is None:
        return
    try:
        symbols = r.symbols(QgsRenderContext())
    except Exception:
        return
    touched = False
    for sym in symbols:
        for i in range(sym.symbolLayerCount()):
            sl = sym.symbolLayer(i)
            if not isinstance(sl, QgsSvgMarkerSymbolLayer):
                continue
            p = sl.path() or ""
            if p and os.path.isfile(p):
                continue
            cand = svg_index.get(os.path.basename(p).lower()) if p else None
            if cand:
                sl.setPath(cand)
                touched = True
            else:
                repl = QgsSimpleMarkerSymbolLayer()
                try:
                    repl.setSize(sl.size() or 2.0)
                    col = sl.fillColor() if hasattr(sl, "fillColor") else sl.color()
                    repl.setColor(col if (col and col.isValid()) else QColor("#5A5A5A"))
                    repl.setStrokeColor(QColor("#FFFFFF"))
                    repl.setStrokeWidth(0.2)
                except Exception:
                    pass
                sym.changeSymbolLayer(i, repl)
                touched = True
    if touched:
        layer.triggerRepaint()


def _gpkg_layer_names(gpkg_path):
    """List sublayer names inside the GeoPackage."""
    try:
        md  = QgsProviderRegistry.instance().providerMetadata("ogr")
        return [s.name() for s in md.querySublayers(gpkg_path)]
    except Exception:
        try:
            from osgeo import ogr
            ds = ogr.Open(gpkg_path)
            return [ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())]
        except Exception as exc:
            QgsMessageLog.logMessage(f"Zoomstack: could not list layers: {exc}",
                                     _LOG, Qgis.MessageLevel.Warning)
            return []


# ---------------------------------------------------------------------------
# Per-category feature visibility (hide roads, airports, labels, …)
# ---------------------------------------------------------------------------
# The user-facing toggles group the 21 GeoPackage layers into friendly
# categories. Hidden categories persist in QgsSettings (global — the basemap
# is session-only and never saved into projects) and re-apply on every load.
# NOTE: no '&' in labels — QCheckBox would treat it as a mnemonic.
_HIDDEN_CATS_KEY = "biome/zoomstack_hidden_features"

# (key, checkbox label, {_norm()'d GeoPackage layer names}) in display order.
# 'land' is deliberately absent: it is the base fill the map stands on.
FEATURE_CATEGORIES = [
    ("roads",      "Roads",                    {"roadslocal", "roadsregional",
                                                "roadsnational"}),
    ("railways",   "Railways",                 {"rail", "railwaystations"}),
    ("airports",   "Airports",                 {"airports"}),
    ("buildings",  "Buildings",                {"localbuildings",
                                                "districtbuildings"}),
    ("power",      "Power lines",              {"etl"}),
    ("names",      "Place names / labels",     {"names"}),
    ("contours",   "Contours",                 {"contours"}),
    ("boundaries", "Admin boundaries",         {"boundaries"}),
    ("water",      "Water",                    {"surfacewater", "waterlines",
                                                "foreshore"}),
    ("sites",      "Sites (airfields, ports…)", {"sites"}),
    ("green",      "Green space / woodland",   {"greenspace", "woodland",
                                                "nationalparks"}),
    ("urban",      "Urban areas",              {"urbanareas"}),
]


def hidden_categories() -> set:
    """Category keys the user has switched off, from QgsSettings."""
    raw = QgsSettings().value(_HIDDEN_CATS_KEY, "", type=str)
    valid = {k for k, _l, _n in FEATURE_CATEGORIES}
    return {k for k in raw.split(",") if k in valid}


def set_hidden_categories(keys):
    QgsSettings().setValue(_HIDDEN_CATS_KEY, ",".join(sorted(keys)))


def _category_of(layer_name):
    """Category key a Zoomstack layer belongs to, or None (e.g. 'land')."""
    n = _norm(layer_name)
    for key, _label, names in FEATURE_CATEGORIES:
        if n in names:
            return key
    return None


def apply_category_visibility(group):
    """Set every layer node in a loaded Zoomstack group to its category's
    saved visibility (fresh loads arrive all-visible)."""
    hidden = hidden_categories()
    for node in group.findLayers():
        cat = _category_of(node.name())
        if cat is not None:
            node.setItemVisibilityChecked(cat not in hidden)


def _set_category_visible_live(key, visible):
    """Show/hide one category on the currently-loaded basemap (no-op when the
    basemap isn't in the project)."""
    root = QgsProject.instance().layerTreeRoot()
    grp = root.findGroup(ZOOMSTACK_GROUP)
    if grp is None:
        return
    for node in grp.findLayers():
        if _category_of(node.name()) == key:
            node.setItemVisibilityChecked(visible)


class _FeaturePopover(QFrame):
    """Click-away popover of per-category checkboxes for the offline basemap.
    Toggles apply to the canvas immediately and persist via QgsSettings, so
    they hold across sessions (the basemap itself is session-only)."""

    def __init__(self, iface=None):
        from qgis.PyQt.QtWidgets import QCheckBox, QGraphicsDropShadowEffect
        from qgis.PyQt.QtGui import QColor, QCursor
        from . import theme
        super().__init__(None, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._iface = iface
        self._boxes = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 12)   # room for the drop shadow
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("zsFeatCard")
        card.setStyleSheet(theme.dock_qss() + f"""
QFrame#zsFeatCard {{
    background: {theme.SURFACE};
    border: 1px solid {theme.BORDER_STRONG};
    border-radius: 10px;
}}
QFrame#zsFeatHeader {{
    background: {theme.BANNER_TOP};
    border: none; border-radius: 9px;
}}
QLabel#zsFeatTitle {{
    color: #FFFFFF; font-size: 13px; font-weight: 600;
    font-family: {theme.FONT_SEMIBOLD}; background: transparent;
}}
QLabel#zsFeatSub {{
    color: #BFD0C3; font-size: 10px; background: transparent;
}}
QLabel#zsFeatSection {{
    color: {theme.ACCENT_DARK}; font-size: 9px; font-weight: 600;
    font-family: {theme.FONT_MEDIUM}; letter-spacing: 1.2px;
    background: transparent; padding: 5px 2px 1px 2px;
}}
QLabel#zsFeatNote {{
    color: {theme.FAINT}; font-size: 10px; background: transparent;
}}
QCheckBox {{
    font-size: 11px; color: {theme.INK}; background: {theme.CARD};
    border: 1px solid {theme.BORDER}; border-radius: 6px;
    padding: 5px 7px; spacing: 8px;
}}
QCheckBox:hover {{ background: {theme.ACCENT_SOFT}; border-color: {theme.BORDER_STRONG}; }}
QPushButton#zsFeatAll {{
    background: {theme.CARD}; border: 1px solid {theme.BORDER_STRONG};
    border-radius: 6px; color: {theme.ACCENT_DARK};
    font-size: 10px; font-family: {theme.FONT_MEDIUM}; font-weight: 500;
    padding: 6px 10px;
}}
QPushButton#zsFeatAll:hover {{ background: {theme.ACCENT_SOFT}; border-color: {theme.ACCENT}; }}
""")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        header = QFrame()
        header.setObjectName("zsFeatHeader")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(13, 11, 13, 10)
        hh.setSpacing(9)
        ico = QLabel()
        ico.setPixmap(theme.icon("map", "#D5E5D8", 18).pixmap(18, 18))
        ico.setStyleSheet("background:transparent; border:none;")
        hh.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
        htext = QWidget()
        htext.setStyleSheet("background:transparent;")
        htv = QVBoxLayout(htext)
        htv.setContentsMargins(0, 0, 0, 0)
        htv.setSpacing(1)
        title = QLabel("Offline map detail")
        title.setObjectName("zsFeatTitle")
        sub = QLabel("Choose what appears on the OS basemap")
        sub.setObjectName("zsFeatSub")
        htv.addWidget(title)
        htv.addWidget(sub)
        hh.addWidget(htext, 1)
        v.addWidget(header)

        body = QWidget()
        body.setStyleSheet("background:transparent;")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(12, 8, 12, 11)
        bv.setSpacing(4)
        v.addWidget(body)

        hand = QCursor(Qt.CursorShape.PointingHandCursor)
        hidden = hidden_categories()
        by_key = {key: (label, names) for key, label, names in FEATURE_CATEGORIES}
        sections = [
            ("TRANSPORT", ("roads", "railways", "airports")),
            ("BUILT ENVIRONMENT", ("buildings", "power", "urban")),
            ("LANDSCAPE & CONTEXT",
             ("water", "green", "sites", "contours", "boundaries", "names")),
        ]
        for section, keys in sections:
            sec = QLabel(section)
            sec.setObjectName("zsFeatSection")
            bv.addWidget(sec)
            for key in keys:
                label, _names = by_key[key]
                cb = QCheckBox(label)
                cb.setChecked(key not in hidden)
                cb.setCursor(hand)
                cb.toggled.connect(lambda on, k=key: self._on_toggle(k, on))
                bv.addWidget(cb)
                self._boxes[key] = cb

        all_btn = QPushButton("Show everything")
        all_btn.setObjectName("zsFeatAll")
        all_btn.setCursor(hand)
        all_btn.clicked.connect(self._show_all)
        bv.addWidget(all_btn)

        note = QLabel("Changes save automatically for future projects.")
        note.setObjectName("zsFeatNote")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bv.addWidget(note)

    def _on_toggle(self, key, visible):
        hidden = hidden_categories()
        (hidden.discard if visible else hidden.add)(key)
        set_hidden_categories(hidden)
        _set_category_visible_live(key, visible)
        self._refresh_canvas()

    def _show_all(self):
        set_hidden_categories(set())
        for cb in self._boxes.values():
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        root = QgsProject.instance().layerTreeRoot()
        grp = root.findGroup(ZOOMSTACK_GROUP)
        if grp is not None:
            for node in grp.findLayers():
                if _category_of(node.name()) is not None:
                    node.setItemVisibilityChecked(True)
        self._refresh_canvas()

    def _refresh_canvas(self):
        canvas = None
        if self._iface is not None:
            try:
                canvas = self._iface.mapCanvas()
            except Exception:
                canvas = None
        if canvas is None:
            try:
                from qgis.utils import iface as _iface
                canvas = _iface.mapCanvas() if _iface else None
            except Exception:
                canvas = None
        if canvas is not None:
            canvas.refresh()


def show_feature_popover(anchor_widget, iface=None):
    """Open the feature-visibility popover just below `anchor_widget` (the
    basemap bar's Offline button), clamped to the screen. Returns the popover;
    the caller should keep a reference so it isn't garbage-collected."""
    from qgis.PyQt.QtCore import QPoint
    pop = _FeaturePopover(iface=iface)
    pop.adjustSize()
    if anchor_widget is not None:
        # -10/-8 cancels the shadow margins so the card edge, not the shadow,
        # aligns with the button.
        pos = anchor_widget.mapToGlobal(
            QPoint(-10, anchor_widget.height() - 8 + 4))
        try:
            scr = anchor_widget.screen().availableGeometry()
            x = max(scr.left() + 4,
                    min(pos.x(), scr.right() - pop.width() - 4))
            y = pos.y()
            if y + pop.height() > scr.bottom() - 4:
                y = (anchor_widget.mapToGlobal(QPoint(0, 0)).y()
                     - pop.height() + 8 - 4)
            pos = QPoint(x, y)
        except Exception:
            pass
        pop.move(pos)
    pop.show()
    return pop


# ---------------------------------------------------------------------------
# Loading into the project
# ---------------------------------------------------------------------------
def load_zoomstack_basemap(visible=True, style_pref="outdoor"):
    """Add (or reveal) the styled Zoomstack basemap group under 'Base Maps'.
    Returns the layer-tree group on success, None on failure."""
    gpkg = find_gpkg()
    if not gpkg:
        return None

    root = QgsProject.instance().layerTreeRoot()
    grp  = root.findGroup(ZOOMSTACK_GROUP)
    if grp is not None:                       # already loaded — just show it
        grp.setItemVisibilityChecked(visible)
        return grp

    names = _gpkg_layer_names(gpkg)
    if not names:
        return None

    # Sort into OS draw order (unknown layers go on top, below 'names')
    def order_key(n):
        k = _norm(n)
        for i, ref in enumerate(_LAYER_ORDER):
            if _norm(ref) == k:
                return i
        return len(_LAYER_ORDER) - 1.5        # just under the labels layer
    names.sort(key=order_key)

    _register_svg_paths()
    qml_map = _find_qml_map(style_pref)
    svg_index = _svg_basename_index()

    bm_group = root.findGroup("Base Maps")
    if bm_group is None:
        bm_group = root.addGroup("Base Maps")
    grp = bm_group.addGroup(ZOOMSTACK_GROUP)  # bottom of Base Maps
    grp.setExpanded(False)

    added = 0
    # insert top-most first so layer panel order matches draw order
    for name in reversed(names):
        uri = f"{gpkg}|layername={name}"
        lyr = QgsVectorLayer(uri, name, "ogr")
        if not lyr.isValid():
            QgsMessageLog.logMessage(f"Zoomstack: layer '{name}' failed to load",
                                     _LOG, Qgis.MessageLevel.Warning)
            continue
        qml = qml_map.get(_norm(name))
        if qml:
            _load_named_style_with_font(lyr, qml)
            _repair_layer_svg_markers(lyr, svg_index)
            lyr.triggerRepaint()
        QgsProject.instance().addMapLayer(lyr, False)
        grp.addLayer(lyr)
        added += 1

    if added == 0:
        bm_group.removeChildNode(grp)
        return None

    apply_category_visibility(grp)   # user's saved hide-list (roads, labels…)
    grp.setItemVisibilityChecked(visible)
    QgsMessageLog.logMessage(
        f"Zoomstack: loaded {added} layers from {gpkg}", _LOG, Qgis.MessageLevel.Info)
    return grp


def repair_basemap_symbols():
    """Fix the '?' markers on an already-loaded OS Zoomstack basemap in place
    (no reload). Registers the SVG paths, then relocates/repairs every layer's
    markers. Returns the number of layers repaired."""
    _register_svg_paths()
    svg_index = _svg_basename_index()
    root = QgsProject.instance().layerTreeRoot()
    grp = root.findGroup(ZOOMSTACK_GROUP)
    if grp is None:
        return 0
    n = 0
    for node in grp.findLayers():
        lyr = node.layer()
        if lyr is not None:
            _repair_layer_svg_markers(lyr, svg_index)
            lyr.triggerRepaint()
            n += 1
    try:
        from qgis.utils import iface
        iface.mapCanvas().refreshAllLayers()
    except Exception:
        pass
    return n


# ---------------------------------------------------------------------------
# Download + install
# ---------------------------------------------------------------------------
def _fetch_download_info():
    """Ask the (keyless) OS Downloads API for the GeoPackage URL and size.
    Returns (url, size_bytes) — falls back to a hardcoded URL on failure."""
    try:
        req = QgsBlockingNetworkRequest()
        err = req.get(QNetworkRequest(QUrl(_DOWNLOADS_API)))
        if err == QgsBlockingNetworkRequest.ErrorCode.NoError:
            data = json.loads(bytes(req.reply().content()).decode("utf-8"))
            for item in data:
                if str(item.get("format", "")).lower().startswith("geopackage"):
                    url = item.get("url") or item.get("URL") or _FALLBACK_URL
                    if "redirect" not in url:
                        url += ("&" if "?" in url else "?") + "redirect"
                    return url, int(item.get("size", 0))
    except Exception as exc:
        QgsMessageLog.logMessage(f"Zoomstack: API listing failed ({exc}); "
                                 "using fallback URL", _LOG, Qgis.MessageLevel.Warning)
    return _FALLBACK_URL, 0


def _extract_zip_task(task, zip_path, dest_dir, only_exts=None):
    """Runs in a background QgsTask: extract zip with progress."""
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        if only_exts:
            members = [m for m in members
                       if os.path.splitext(m.filename)[1].lower() in only_exts]
        total = max(1, len(members))
        for i, m in enumerate(members):
            if task and task.isCanceled():
                return False
            zf.extract(m, dest_dir)
            if task:
                task.setProgress(i * 100.0 / total)
    return True


class ZoomstackInstaller(QObject):
    """Orchestrates: confirm dialog -> download data zip -> download styles
    -> background extraction -> cleanup. Emits finished(ok, message)."""

    finished = pyqtSignal(bool, str)

    def __init__(self, parent_widget):
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._downloader = None
        self._progress = None
        self._task = None

    # ── public entry point ────────────────────────────────────────────
    def start(self):
        if is_installed():
            self.finished.emit(True, "already_installed")
            return

        # Resume an interrupted install: the big data zip is already on disk
        # but was never unpacked (e.g. the optional styles download failed, or
        # QGIS closed mid-install). Don't pull another ~4 GB — offer to just
        # extract what's already here.
        if have_complete_data_zip():
            self._data_zip = data_zip_path()
            resp = QMessageBox.question(
                self._parent, "OS Zoomstack",
                "An OS Zoomstack download is already on disk but hasn't been "
                "unpacked yet.\n\nExtract the existing file now instead of "
                "downloading it again (~4 GB)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
            if resp == QMessageBox.StandardButton.Yes:
                # Re-enter the pipeline at the post-data-download step: it
                # fetches the (small, optional) styles and then extracts.
                self._on_data_downloaded(self._data_zip)
                return
            # otherwise fall through to a normal (re-)download

        if not self._confirm_dialog():
            self.finished.emit(False, "cancelled")
            return

        dest = storage_dir()
        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as exc:
            QMessageBox.critical(self._parent, "OS Zoomstack",
                                 f"Cannot create folder:\n{dest}\n\n{exc}")
            self.finished.emit(False, "mkdir_failed")
            return

        # Disk space: ~3.8 GB zip + ~11.8 GB extracted
        try:
            free_gb = shutil.disk_usage(dest).free / 1e9
            if free_gb < 17:
                resp = QMessageBox.warning(
                    self._parent, "Low disk space",
                    f"Only {free_gb:.0f} GB free at:\n{dest}\n\n"
                    "The download needs ~16 GB during installation "
                    "(~12 GB once finished). Continue anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if resp != QMessageBox.StandardButton.Yes:
                    self.finished.emit(False, "cancelled")
                    return
        except Exception:
            pass

        url, size = _fetch_download_info()
        self._data_zip = os.path.join(dest, "os_open_zoomstack_gpkg.zip")
        self._download(url, self._data_zip, size,
                       "Downloading OS Open Zoomstack (~3.8 GB)…",
                       self._on_data_downloaded)

    # ── confirm / settings dialog ─────────────────────────────────────
    def _confirm_dialog(self) -> bool:
        dlg = QDialog(self._parent)
        dlg.setWindowTitle("Download OS Zoomstack basemap")
        vl = QVBoxLayout(dlg)

        info = QLabel(
            "<b>OS Open Zoomstack</b> — official Ordnance Survey vector "
            "basemap of the whole of Great Britain (EPSG:27700).<br><br>"
            "• Download: <b>~3.8 GB</b> (one-off; ~11.8 GB on disk)<br>"
            "• Works fully offline, razor-sharp at every zoom & in print<br>"
            "• Open Government Licence — free to use in client reports<br>"
            "• OS styles applied automatically<br><br>"
            "It is stored <i>outside</i> the plugin, so reinstalling or "
            "updating Biome will <b>not</b> require a re-download."
        )
        info.setWordWrap(True)
        vl.addWidget(info)

        path_row = QHBoxLayout()
        self._path_lbl = QLabel(storage_dir())
        self._path_lbl.setStyleSheet("color:#1F4A37; font-size:10px;")
        self._path_lbl.setWordWrap(True)
        change = QPushButton("Change folder…")
        change.setFixedHeight(24)

        def pick():
            d = QFileDialog.getExistingDirectory(
                dlg, "Choose storage folder", storage_dir())
            if d:
                set_storage_dir(d)
                self._path_lbl.setText(d)
        change.clicked.connect(pick)
        path_row.addWidget(QLabel("Store in:"))
        path_row.addWidget(self._path_lbl, 1)
        path_row.addWidget(change)
        vl.addLayout(path_row)

        btn_row = QHBoxLayout()
        ok = QPushButton("Download")
        ok.setDefault(True)
        cancel = QPushButton("Cancel")
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        vl.addLayout(btn_row)

        return dlg.exec() == QDialog.DialogCode.Accepted

    # ── download machinery ────────────────────────────────────────────
    def _download(self, url, out_path, expected_size, label, on_done,
                  optional=False):
        self._progress = QProgressDialog(label, "Cancel", 0, 100, self._parent)
        self._progress.setWindowTitle("OS Zoomstack")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setValue(0)

        dl = QgsFileDownloader(QUrl(url), out_path, delayStart=True)
        self._downloader = dl

        def on_progress(received, total):
            tot = total if total > 0 else expected_size
            if tot > 0:
                self._progress.setValue(min(99, int(received * 100 / tot)))
            self._progress.setLabelText(
                f"{label}\n{received/1e9:.2f} GB"
                + (f" of {tot/1e9:.2f} GB" if tot > 0 else "") + " received")

        def on_error(errors):
            self._close_progress()
            if optional:
                # An optional component (e.g. the OS stylesheets) failed to
                # download. Log it and carry on so the expensive data zip we
                # already have still gets extracted and installed, rather than
                # throwing the whole install away over cosmetic styles.
                QgsMessageLog.logMessage(
                    "Zoomstack: optional download failed ("
                    + "; ".join(errors) + "); continuing without it",
                    _LOG, Qgis.MessageLevel.Warning)
                on_done(out_path)
                return
            QMessageBox.critical(self._parent, "OS Zoomstack",
                                 "Download failed:\n" + "\n".join(errors))
            self.finished.emit(False, "download_failed")

        def on_completed(*_args):
            self._close_progress()
            on_done(out_path)

        dl.downloadProgress.connect(on_progress)
        dl.downloadError.connect(on_error)
        dl.downloadCompleted.connect(on_completed)
        dl.downloadCanceled.connect(
            lambda: (self._close_progress(),
                     self.finished.emit(False, "cancelled")))
        self._progress.canceled.connect(dl.cancelDownload)
        dl.startDownload()

    def _close_progress(self):
        if self._progress is not None:
            self._progress.blockSignals(True)
            self._progress.close()
            self._progress = None

    # ── pipeline steps ───────────────────────────────────────────────
    def _on_data_downloaded(self, zip_path):
        # Best-effort grab of the official QML stylesheets + SVG symbols
        self._styles_zip = os.path.join(storage_dir(), "zoomstack_styles.zip")
        self._download(_STYLES_ZIP_URL, self._styles_zip, 0,
                       "Downloading OS stylesheets…",
                       self._on_styles_downloaded, optional=True)

    def _on_styles_downloaded(self, _path):
        self._extract_all()

    def _extract_all(self):
        dest       = storage_dir()
        data_zip   = self._data_zip
        styles_zip = self._styles_zip

        def work(task):
            ok = _extract_zip_task(task, data_zip, dest)
            if not ok:
                return False
            try:
                _extract_zip_task(None, styles_zip,
                                  os.path.join(dest, "stylesheets"),
                                  only_exts={".qml", ".svg"})
            except Exception as exc:       # styles are optional
                QgsMessageLog.logMessage(
                    f"Zoomstack: stylesheet extract failed ({exc})",
                    _LOG, Qgis.MessageLevel.Warning)
            for p in (data_zip, styles_zip):
                try:
                    os.remove(p)
                except Exception:
                    pass
            return True

        def done(exception, ok=None):
            # ok MUST default: QgsTask.fromFunction's wrapper omits the result
            # argument whenever work() returns a falsy value (our False failure
            # path) — a two-arg signature dies with a swallowed TypeError.
            if exception or not ok:
                msg = str(exception) if exception else "extraction cancelled"
                QMessageBox.critical(self._parent, "OS Zoomstack",
                                     f"Extraction failed:\n{msg}")
                self.finished.emit(False, "extract_failed")
                return
            if not is_installed():
                QMessageBox.critical(
                    self._parent, "OS Zoomstack",
                    "Extraction finished but no GeoPackage was found.")
                self.finished.emit(False, "gpkg_missing")
                return
            self.finished.emit(True, "installed")

        self._task = QgsTask.fromFunction(
            "Extracting OS Zoomstack (~12 GB)…", work, on_finished=done)
        QgsApplication.taskManager().addTask(self._task)
