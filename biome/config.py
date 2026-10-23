# config.py
# Store your API keys here. Do not share this file publicly.

# Optional hardcoded fallback. PREFER the in-app key prompt (OS Outdoor
# basemap button) — that key is stored in your QGIS user profile and so
# survives plugin updates/reinstalls, whereas THIS file is overwritten on
# every plugin update.
OS_API_KEY = ""  # set your Ordnance Survey API key here (do not commit a real key)

# QgsSettings key under which the in-app OS Data Hub API key is persisted.
# QgsSettings lives in the QGIS user profile (outside python/plugins), so the
# key is retained when the plugin is deleted, updated or reinstalled.
_OS_KEY_SETTING = "biome/os_api_key"


def get_os_api_key():
    """Return the user's OS Data Hub API key, or '' if none is set.

    Preference order:
      1. QgsSettings "biome/os_api_key" — entered via the in-app prompt and
         stored in the QGIS user profile, so it survives plugin updates.
      2. The OS_API_KEY constant above (legacy hardcoded fallback).
    """
    val = ""
    try:
        from qgis.core import QgsSettings
        val = QgsSettings().value(_OS_KEY_SETTING, "", type=str) or ""
    except Exception:
        val = ""
    return (val or OS_API_KEY or "").strip()


def set_os_api_key(key):
    """Persist the OS Data Hub API key to the QGIS user profile (QgsSettings),
    outside the plugin folder so it survives plugin deletes/updates."""
    from qgis.core import QgsSettings
    QgsSettings().setValue(_OS_KEY_SETTING, (key or "").strip())


# A premium OS NGD Features key is kept separate from the OS Maps key.  This
# lets the standing-water search remain optional and means a user can enable
# either API without having to add both services to the same Data Hub project.
_OS_NGD_KEY_SETTING = "biome/os_ngd_api_key"


def get_os_ngd_api_key():
    """Return the optional OS NGD API - Features project key."""
    try:
        from qgis.core import QgsSettings
        return (QgsSettings().value(
            _OS_NGD_KEY_SETTING, "", type=str) or "").strip()
    except Exception:
        return ""


def set_os_ngd_api_key(key):
    """Persist the optional OS NGD Features key in the QGIS profile."""
    from qgis.core import QgsSettings
    QgsSettings().setValue(_OS_NGD_KEY_SETTING, (key or "").strip())


_OS_NGD_STYLE_SETTING = "biome/os_ngd_basemap_style"


def get_os_ngd_basemap_style():
    """Saved OS Premium basemap style: road, outdoor, light or blackwhite."""
    try:
        from qgis.core import QgsSettings
        value = (QgsSettings().value(
            _OS_NGD_STYLE_SETTING, "outdoor", type=str) or "outdoor").lower()
    except Exception:
        value = "outdoor"
    return value if value in {"road", "outdoor", "light", "blackwhite"} else "outdoor"


def set_os_ngd_basemap_style(style):
    from qgis.core import QgsSettings
    QgsSettings().setValue(_OS_NGD_STYLE_SETTING, (style or "outdoor").lower())


_BASEMAP_BUTTONS_SETTING = "biome/ui/enabled_basemap_buttons"
_BASEMAP_BUTTON_ORDER_SETTING = "biome/ui/basemap_button_order"
_BASEMAP_BUTTON_DEFAULTS = (
    "Google Hybrid", "Google Satellite", "__offline__",
    "ESRI World Imagery", "OS Outdoor", "OS Premium")
_BASEMAP_BUTTONS_ENABLED_BY_DEFAULT = (
    "Google Hybrid", "Google Satellite", "__offline__", "OS Premium")


def get_enabled_basemap_buttons():
    """Return the basemap selectors the user has chosen to show."""
    try:
        from qgis.core import QgsSettings
        raw = QgsSettings().value(_BASEMAP_BUTTONS_SETTING, None)
    except Exception:
        raw = None
    if raw is None:
        return set(_BASEMAP_BUTTONS_ENABLED_BY_DEFAULT)
    if isinstance(raw, str):
        values = [part for part in raw.split("|") if part]
    else:
        values = list(raw or [])
    return {value for value in values if value in _BASEMAP_BUTTON_DEFAULTS}


def set_enabled_basemap_buttons(values):
    """Save which basemap selectors are visible in Biome's map bars."""
    from qgis.core import QgsSettings
    allowed = [value for value in _BASEMAP_BUTTON_DEFAULTS if value in set(values)]
    QgsSettings().setValue(_BASEMAP_BUTTONS_SETTING, "|".join(allowed))


def get_basemap_button_order():
    """Return every basemap selector in the user's saved display order."""
    try:
        from qgis.core import QgsSettings
        raw = QgsSettings().value(_BASEMAP_BUTTON_ORDER_SETTING, "", type=str) or ""
    except Exception:
        raw = ""
    saved = [part for part in raw.split("|")
             if part in _BASEMAP_BUTTON_DEFAULTS]
    return saved + [value for value in _BASEMAP_BUTTON_DEFAULTS
                    if value not in saved]


def set_basemap_button_order(values):
    """Persist the basemap selector order in the QGIS user profile."""
    from qgis.core import QgsSettings
    ordered = []
    for value in values:
        if value in _BASEMAP_BUTTON_DEFAULTS and value not in ordered:
            ordered.append(value)
    ordered.extend(value for value in _BASEMAP_BUTTON_DEFAULTS
                   if value not in ordered)
    QgsSettings().setValue(_BASEMAP_BUTTON_ORDER_SETTING, "|".join(ordered))


# Google Maps Platform API key (Map Tiles API must be enabled on its project)
# — used by the 3D Spotlight export (earth3d_export.py) to load Google's
# Photorealistic 3D Tiles. Same persistence story as the OS key.
_GOOGLE_KEY_SETTING = "biome/google_maps_api_key"


def get_google_maps_api_key():
    """Return the user's Google Maps Platform API key, or '' if unset."""
    try:
        from qgis.core import QgsSettings
        val = QgsSettings().value(_GOOGLE_KEY_SETTING, "", type=str) or ""
    except Exception:
        val = ""
    return val.strip()


def set_google_maps_api_key(key):
    """Persist the Google Maps Platform API key to the QGIS user profile."""
    from qgis.core import QgsSettings
    QgsSettings().setValue(_GOOGLE_KEY_SETTING, (key or "").strip())


# Redline digitising aid.  Enabled by default so a redline drawn after habitat
# mapping can follow the outside edges of the existing Biome area polygons.
_REDLINE_SNAP_SETTING = "biome/redline_snap_to_habitats"


def get_redline_snap_to_habitats():
    """Whether redline capture snaps to existing habitat polygon edges."""
    try:
        from qgis.core import QgsSettings
        return QgsSettings().value(
            _REDLINE_SNAP_SETTING, True, type=bool)
    except Exception:
        return True


def set_redline_snap_to_habitats(enabled):
    """Persist the redline-to-habitat snapping preference."""
    from qgis.core import QgsSettings
    QgsSettings().setValue(_REDLINE_SNAP_SETTING, bool(enabled))


# Priority Habitats report presentation.  None means the user has not yet made
# a choice, so the report shows its one-time explanatory prompt.
_PHI_GROUP_NEARBY_SETTING = "biome/priority_habitats/group_nearby_polygons"


def get_phi_group_nearby():
    """Return True/False for the saved PHI grouping choice, or None if unset."""
    try:
        from qgis.core import QgsSettings
        settings = QgsSettings()
        if not settings.contains(_PHI_GROUP_NEARBY_SETTING):
            return None
        return settings.value(_PHI_GROUP_NEARBY_SETTING, False, type=bool)
    except Exception:
        return None


def set_phi_group_nearby(enabled):
    """Persist whether nearby same-habitat PHI polygons are grouped."""
    from qgis.core import QgsSettings
    QgsSettings().setValue(_PHI_GROUP_NEARBY_SETTING, bool(enabled))


# Spatial Hub Scotland (Improvement Service) GeoServer authkey — the national
# Scottish TPO dataset is Open Government Licence but its WFS only answers
# with a registered user's key (free account at data.spatialhub.scot → user
# profile → "Geoserver Authkey"). Same persistence story as the other keys.
_SPATIALHUB_KEY_SETTING = "biome/spatialhub_authkey"


def get_spatialhub_authkey():
    """Return the user's Spatial Hub Scotland authkey, or '' if unset."""
    try:
        from qgis.core import QgsSettings
        val = QgsSettings().value(_SPATIALHUB_KEY_SETTING, "", type=str) or ""
    except Exception:
        val = ""
    return val.strip()


def set_spatialhub_authkey(key):
    """Persist the Spatial Hub Scotland authkey to the QGIS user profile."""
    from qgis.core import QgsSettings
    QgsSettings().setValue(_SPATIALHUB_KEY_SETTING, (key or "").strip())


# ---------------------------------------------------------------------------
# Desk-study search distances
# ---------------------------------------------------------------------------
# Distance-based desk-study searches are linked to one shared search area or to
# one of the two reusable local redline buffers. The values persist in the QGIS
# user profile, so the live Desk Study controls, data queries, and map exports
# all use the same saved distances. Each entry: (key, label, default_metres,
# [desk-study maps that should frame this radius]). Searches that are
# intersect-based rather than
# distance-based (SSSI Impact Risk Zones, GCN risk zones, manually-entered Local
# Wildlife Sites) are deliberately absent. Ancient woodland is not listed either:
# it renders inside the Priority Habitats group on the map, so it shares the
# "priority_habitats" radius (see designated_sites.query_ancient_woodland_2km /
# load_ancient_woodland_layer).
DESK_SEARCH_RADII = [
    ("designated_sites",   "Statutory designated sites",          2000, ["Designated Sites Map"]),
    ("priority_habitats",  "Priority Habitats Inventory",         2000, ["Priority Habitats Map"]),
    ("epsl",               "European Protected Species Licences", 2000, ["EPSL Map"]),
    ("conservation_areas", "Conservation areas",                  2000, ["Conservation Areas Map"]),
    ("tpo",                "Tree Preservation Orders",             200, ["TPO Map"]),
    ("standing_water",     "Standing water bodies / ponds",        500, ["Pond Map"]),
    ("edna_ponds",         "GCN eDNA pond surveys",               2000, ["GCN Risk Zones Map"]),
]

_RADIUS_SETTING_FMT = "biome/desk_radius/{key}"
_RADIUS_DEFAULTS    = {k: d for (k, _l, d, _m) in DESK_SEARCH_RADII}
_RADIUS_BY_MAPTYPE  = {mt: k for (k, _l, _d, maps) in DESK_SEARCH_RADII for mt in maps}
SHARED_RADIUS_KEYS = {
    "designated_sites", "priority_habitats", "epsl",
    "conservation_areas", "edna_ponds", "sssi_units",
}
_LOCAL_RADIUS_SETTINGS = {
    "tpo": ("biome/local_search/tpo_outer", 200),
    "standing_water": ("biome/local_search/pond_outer", 500),
}
_POND_INNER_RADIUS_SETTING = "biome/local_search/pond_inner"
_POND_INNER_RADIUS_DEFAULT = 250


def get_search_radius(key):
    """Return the configured search radius (metres) for a desk study search key,
    falling back to its built-in default. Never returns a value <= 0.

    Safe to call from any thread (QgsSettings/QSettings reads are reentrant)."""
    if key in SHARED_RADIUS_KEYS:
        return get_buffer_radius()
    if key in _LOCAL_RADIUS_SETTINGS:
        setting_key, default = _LOCAL_RADIUS_SETTINGS[key]
        try:
            from qgis.core import QgsSettings
            raw = QgsSettings().value(setting_key, default)
            val = int(float(raw))
            return val if val > 0 else default
        except Exception:
            return default
    default = _RADIUS_DEFAULTS.get(key, 2000)
    try:
        from qgis.core import QgsSettings
        raw = QgsSettings().value(_RADIUS_SETTING_FMT.format(key=key), default)
        val = int(float(raw))
        return val if val > 0 else default
    except Exception:
        return default


def set_search_radius(key, metres):
    """Persist a search radius (metres) for a desk study search key. A value that
    is not a positive number clears the override, restoring the built-in
    default."""
    if key in SHARED_RADIUS_KEYS:
        set_buffer_radius(metres)
        return
    from qgis.core import QgsSettings
    s    = QgsSettings()
    if key in _LOCAL_RADIUS_SETTINGS:
        setting_key, _default = _LOCAL_RADIUS_SETTINGS[key]
        try:
            val = int(float(metres))
        except (TypeError, ValueError):
            val = 0
        if val > 0:
            s.setValue(setting_key, val)
        else:
            s.remove(setting_key)
        return
    skey = _RADIUS_SETTING_FMT.format(key=key)
    try:
        val = int(float(metres))
    except (TypeError, ValueError):
        val = 0
    if val > 0:
        s.setValue(skey, val)
    else:
        s.remove(skey)


def get_pond_inner_radius():
    """Return the inner standing-water search ring; defaults to 250 metres."""
    try:
        from qgis.core import QgsSettings
        raw = QgsSettings().value(
            _POND_INNER_RADIUS_SETTING, _POND_INNER_RADIUS_DEFAULT)
        val = int(float(raw))
        return val if val > 0 else _POND_INNER_RADIUS_DEFAULT
    except Exception:
        return _POND_INNER_RADIUS_DEFAULT


def set_pond_inner_radius(metres):
    """Persist the inner standing-water search ring in metres."""
    from qgis.core import QgsSettings
    try:
        val = int(float(metres))
    except (TypeError, ValueError):
        val = 0
    if val > 0:
        QgsSettings().setValue(_POND_INNER_RADIUS_SETTING, val)
    else:
        QgsSettings().remove(_POND_INNER_RADIUS_SETTING)


def reset_search_radii():
    """Restore the shared and local search distances to their defaults."""
    from qgis.core import QgsSettings
    s = QgsSettings()
    s.remove(_BUFFER_RADIUS_SETTING)
    s.remove(_POND_INNER_RADIUS_SETTING)
    for setting_key, _default in _LOCAL_RADIUS_SETTINGS.values():
        s.remove(setting_key)
    for key, _l, _d, _m in DESK_SEARCH_RADII:
        s.remove(_RADIUS_SETTING_FMT.format(key=key))


def get_radius_for_map_type(map_type):
    """Return the configured radius (metres) that a desk study MAP should frame,
    or None when the map has no distance-based search (e.g. GCN risk zones, LWS,
    SSSI IRZ)."""
    key = _RADIUS_BY_MAPTYPE.get(map_type)
    return None if key is None else get_search_radius(key)


# ---------------------------------------------------------------------------
# Shared desk-study search area
# ---------------------------------------------------------------------------
# One saved distance drives both the visible "Search Buffer" ring and every
# linked broad desk-study query. The ring can still be toggled manually, and is
# also shown automatically while a linked constraint layer is visible.
# Persisted per profile via QgsSettings.
_BUFFER_RADIUS_SETTING = "biome/search_buffer_radius"
_BUFFER_RADIUS_DEFAULT = 2000


def get_buffer_radius():
    """Return the shared desk-study search radius; defaults to 2000 metres."""
    try:
        from qgis.core import QgsSettings
        raw = QgsSettings().value(_BUFFER_RADIUS_SETTING, _BUFFER_RADIUS_DEFAULT)
        val = int(float(raw))
        return val if val > 0 else _BUFFER_RADIUS_DEFAULT
    except Exception:
        return _BUFFER_RADIUS_DEFAULT


def set_buffer_radius(metres):
    """Persist the shared desk-study search radius in metres."""
    from qgis.core import QgsSettings
    try:
        val = int(float(metres))
    except (TypeError, ValueError):
        return
    if val > 0:
        QgsSettings().setValue(_BUFFER_RADIUS_SETTING, val)


# ---------------------------------------------------------------------------
# Date format
# ---------------------------------------------------------------------------
# How user-facing dates read: printed final/desk maps, the emergence survey
# date picker, the weather checker and the GCN HSI form. Deliberately NOT
# applied to file-name stamps (sortable ISO), log timestamps, or the exported
# metric workbook (Excel parses dd/mm/yyyy; changing it could re-type cells).
# Persisted per-profile so a firm's house style survives plugin updates.
_DATE_FORMAT_SETTING = "biome/date_format"
_DATE_FORMAT_DEFAULT = "long"

# (key, strftime pattern, Qt date format) — the two patterns MUST stay in sync
# per row: strftime feeds printed text, the Qt pattern feeds QDate/QDateEdit.
DATE_FORMATS = (
    ("long",   "%d %B %Y", "dd MMMM yyyy"),   # 17 July 2026
    ("medium", "%d %b %Y", "dd MMM yyyy"),    # 17 Jul 2026
    ("slash",  "%d/%m/%Y", "dd/MM/yyyy"),     # 17/07/2026
    ("iso",    "%Y-%m-%d", "yyyy-MM-dd"),     # 2026-07-17
)


def date_format_key():
    """The chosen date style's key into DATE_FORMATS (default 'long')."""
    try:
        from qgis.core import QgsSettings
        val = QgsSettings().value(_DATE_FORMAT_SETTING, "", type=str) or ""
    except Exception:
        val = ""
    return val if val in {k for k, _s, _q in DATE_FORMATS} else _DATE_FORMAT_DEFAULT


def set_date_format_key(key):
    """Persist the chosen date style (one of the DATE_FORMATS keys)."""
    if key not in {k for k, _s, _q in DATE_FORMATS}:
        return
    from qgis.core import QgsSettings
    QgsSettings().setValue(_DATE_FORMAT_SETTING, key)


def date_strftime():
    """strftime pattern for the chosen date style."""
    key = date_format_key()
    return next(s for k, s, _q in DATE_FORMATS if k == key)


def qt_date_format():
    """Qt date-format string (QDate.toString / QDateEdit.setDisplayFormat)
    for the chosen date style."""
    key = date_format_key()
    return next(q for k, _s, q in DATE_FORMATS if k == key)


def format_date(d=None):
    """A date (default today) rendered in the user's chosen style."""
    from datetime import date as _date
    return (d or _date.today()).strftime(date_strftime())


# ---------------------------------------------------------------------------
# Measurement ellipsoid
# ---------------------------------------------------------------------------
# Every user-visible distance/area in Biome must be ellipsoidal (CLAUDE.md hard
# rule): the habitat layers are EPSG:3857, where planar measures are inflated
# ~1.6x (lengths) / ~2.6x (areas) at UK latitudes. QgsDistanceArea only
# measures ellipsoidally when given a REAL ellipsoid — and a project can
# report the literal string "NONE" (fresh/headless projects, or a profile
# whose measure/ellipsoid setting is unset). "NONE" is truthy, so
# `project.ellipsoid() or "GRS80"` style fallbacks never caught it.

_FALLBACK_ELLIPSOID = "EPSG:7030"   # the WGS84 ellipsoid


def measurement_ellipsoid():
    """Ellipsoid acronym for every QgsDistanceArea the plugin builds: the
    project's ellipsoid when a real one is set, else WGS84 (EPSG:7030).
    Never returns "NONE"/empty, so calc.setEllipsoid(measurement_ellipsoid())
    always yields ellipsoidal (ground-true) measurements."""
    try:
        from qgis.core import QgsProject
        ell = str(QgsProject.instance().ellipsoid() or "").strip()
        if ell and ell.upper() != "NONE":
            return ell
    except Exception:
        pass
    return _FALLBACK_ELLIPSOID


def ensure_project_ellipsoid():
    """Pin a real ellipsoid on the project if it reports "NONE", so QGIS's own
    measure tools and $area/$length label expressions also measure
    ellipsoidally (they follow the project ellipsoid, not the plugin's calcs).

    Deferred to the next event-loop tick — never mutate the project during
    readProjectFile (see designated_sites._suppress_datum_dialog for the crash
    that avoids) — and the dirty flag is restored so a freshly opened project
    doesn't prompt to save. A project that already carries a real ellipsoid is
    left untouched."""
    def _apply():
        try:
            from qgis.core import QgsProject
            proj = QgsProject.instance()
            ell = str(proj.ellipsoid() or "").strip()
            if ell and ell.upper() != "NONE":
                return
            was_dirty = proj.isDirty()
            proj.setEllipsoid(_FALLBACK_ELLIPSOID)
            if not was_dirty:
                proj.setDirty(False)
        except Exception:
            pass

    try:
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(0, _apply)
    except Exception:
        _apply()
