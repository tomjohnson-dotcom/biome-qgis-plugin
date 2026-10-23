# region.py — which country's data services a site should use.
#
# The desk study's live sources are England-only (DEFRA / Natural England WFS,
# the PHI ArcGIS service, MAGIC's EPSL layer). A redline in Scotland gets
# nothing back from any of them. This module decides, from the redline
# centroid, whether the site is in Scotland so designated_sites can switch to
# NatureScot / Scottish Government services, and the desk study UI, reports
# and map exports can switch their source labelling to match.
#
# Detection is OFFLINE: a simplified England-Scotland land border (ONS
# Countries December 2023, BUC generalisation, ~100 vertices in EPSG:27700)
# closed into a "Scotland side" polygon. Sites within a few hundred metres of
# the border may fall either way — the settings cog has a manual override
# ("Site region") for that, persisted in the QGIS profile.

_REGION_SETTING = "biome/region_mode"      # "auto" | "england" | "scotland"
REGION_MODES    = ("auto", "england", "scotland")

# Solway Firth (west) -> Marshall Meadows (east), EPSG:27700 metres.
_BORDER = [
    (310256,553413), (312387,556004), (314293,556992), (317610,554484), (317532,555953), (318308,555533),
    (319130,556327), (319031,555658), (319970,556936), (319804,558105), (318544,556828), (316211,558220),
    (315859,559195), (317770,561521), (319045,561664), (318805,561004), (321191,562743), (322966,562785),
    (326192,560297), (328388,559539), (330629,560000), (328404,560561), (328020,561622), (328738,561239),
    (328229,561673), (328731,561961), (327913,562388), (329913,561834), (328549,562934), (329287,562339),
    (328915,563119), (330605,563932), (332048,565948), (333011,565924), (332775,566716), (333307,567607),
    (332981,568818), (334533,571811), (332791,573038), (332959,573650), (338853,573175), (340286,574301),
    (340043,575366), (342837,576311), (343536,578179), (347321,581474), (347473,582972), (350069,583330),
    (355291,586767), (357678,592131), (359932,592345), (360029,594692), (361225,594942), (358993,596399),
    (360273,596630), (361401,598960), (363692,600458), (364622,602784), (367085,603343), (369956,606850),
    (373789,607291), (374780,606090), (376070,606192), (378704,608184), (378156,608840), (379188,609746),
    (378252,611762), (378847,612749), (380263,612584), (383607,615480), (385462,614972), (387772,616764),
    (388206,618713), (389634,619407), (387372,620179), (387294,621768), (385645,623996), (384939,629130),
    (381891,631889), (381875,634500), (380191,636365), (380666,637299), (379677,637002), (378944,637709),
    (380853,639321), (385346,638635), (384471,639874), (386356,641216), (386347,642512), (389590,645856),
    (388999,647266), (390641,647759), (391039,649552), (392713,649494), (393344,651843), (394725,652080),
    (394699,655531), (397937,657550),
]

# Closed polygon whose interior is Scotland. From the border's east end the
# edge runs due east (above all of Northumberland), up round the top of the
# grid and back down the west, then across the Solway / Irish Sea so that
# Galloway (south of the Solway coast of Cumbria) still tests as Scotland.
_SCOTLAND_RING = (
    [(0, 520000), (200000, 520000), (290000, 540000)]
    + _BORDER
    + [(800000, _BORDER[-1][1]), (800000, 1400000), (0, 1400000)]
)

_SITE = {"en": None}       # last redline centroid the desk study told us about


def in_scotland(easting, northing):
    """Pure point-in-polygon test against the Scotland-side polygon (no
    settings, no override). Coordinates in EPSG:27700."""
    try:
        x, y = float(easting), float(northing)
    except (TypeError, ValueError):
        return False
    inside = False
    ring = _SCOTLAND_RING
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def region_mode():
    """The user's override: "auto" (default), "england" or "scotland"."""
    try:
        from qgis.core import QgsSettings
        val = str(QgsSettings().value(_REGION_SETTING, "auto") or "auto").lower()
        return val if val in REGION_MODES else "auto"
    except Exception:
        return "auto"


def set_region_mode(mode):
    from qgis.core import QgsSettings
    s = QgsSettings()
    mode = (mode or "auto").lower()
    if mode in REGION_MODES and mode != "auto":
        s.setValue(_REGION_SETTING, mode)
    else:
        s.remove(_REGION_SETTING)


def set_site(easting, northing):
    """Remember the current redline centroid (EPSG:27700). Called by the desk
    study whenever it refreshes its site stats; None clears it."""
    if easting is None or northing is None:
        _SITE["en"] = None
    else:
        _SITE["en"] = (float(easting), float(northing))


def _project_site():
    """Fallback centroid from the project's redline layer when the desk study
    hasn't reported one yet (e.g. a map export straight after opening)."""
    try:
        # Never touch the project from a worker thread (the desk-study
        # fetches call is_scotland() off the main thread; they always have a
        # set_site() centroid first, so this fallback is main-thread only).
        from qgis.PyQt.QtCore import QThread, QCoreApplication
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            return None
        from qgis.core import (QgsProject, QgsGeometry,
                               QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform)
        from .layer_manager import LAYER_NAMES
        for lyr in QgsProject.instance().mapLayersByName(LAYER_NAMES["redline"]):
            geoms = [f.geometry() for f in lyr.getFeatures()
                     if f.geometry() and not f.geometry().isEmpty()]
            if not geoms:
                continue
            c = QgsGeometry.unaryUnion(geoms).centroid().asPoint()
            bng = QgsCoordinateReferenceSystem("EPSG:27700")
            if lyr.crs() != bng:
                c = QgsCoordinateTransform(lyr.crs(), bng,
                                           QgsProject.instance()).transform(c)
            return (c.x(), c.y())
    except Exception:
        pass
    return None


def detected():
    """"scotland" / "england" from the site centroid alone, or None when no
    site is known."""
    en = _SITE["en"] or _project_site()
    if en is None:
        return None
    return "scotland" if in_scotland(*en) else "england"


def active():
    """The region the data services should use right now: the override if
    set, else the detected region, else England."""
    mode = region_mode()
    if mode != "auto":
        return mode
    return detected() or "england"


def for_point(easting, northing):
    """Region for an explicit point (honours the override)."""
    mode = region_mode()
    if mode != "auto":
        return mode
    return "scotland" if in_scotland(easting, northing) else "england"


def is_scotland():
    return active() == "scotland"


def status_text():
    """One-line summary for the settings cog row."""
    mode = region_mode()
    det = detected()
    det_txt = {"scotland": "Scotland", "england": "England"}.get(det)
    if mode == "auto":
        if det_txt:
            return f"Auto · this site is in {det_txt}"
        return "Auto · detected from the redline (no redline yet)"
    forced = "Scotland" if mode == "scotland" else "England"
    warn = ""
    if det_txt and det_txt != forced:
        warn = f" (redline looks like {det_txt})"
    return f"Forced to {forced}{warn}"


# ---------------------------------------------------------------------------
# Source labelling — what the reports, footnotes and maps should say
# ---------------------------------------------------------------------------

def ds_source_name():
    return "NatureScot / Scottish Government" if is_scotland() else "DEFRA MAGIC"


def phi_source_name():
    """Short source label for the priority-habitat report header."""
    return "NatureScot Habitat Map of Scotland" if is_scotland() else "Natural England PHI"


def phi_footnote():
    if is_scotland():
        return ("*Data from the NatureScot Habitat Map of Scotland (HabMoS), "
                "EUNIS-coded habitats mapped to Scottish Biodiversity List "
                "priority habitats; Ancient Woodland Inventory (NatureScot).")
    return "*Data from Natural England Priority Habitats Inventory / DEFRA MAGIC."


def aonb_label():
    """The landscape designation that fills the AONB slot: National Scenic
    Areas in Scotland."""
    return "NSA" if is_scotland() else "AONB"


def phi_report_label():
    return "Habitat Map of Scotland report" if is_scotland() else "Priority habitats report"


def phi_map_label():
    return "Habitat Map of Scotland map" if is_scotland() else "Priority habitats map"


def phi_map_title():
    """Printed title / legend heading for the priority-habitats desk map."""
    return "Habitat Map of Scotland" if is_scotland() else "Priority Habitats"


def tpo_source_name():
    if is_scotland():
        return "Spatial Hub Scotland (Improvement Service) — Open Government Licence v3.0"
    return "planning.data.gov.uk (MHCLG) — Open Government Licence v3.0"


# ── External map deep-link: MAGIC (England) / Scotland's environment map ──
# Scotland's environment map (Scottish Government / SEPA / NatureScot layers)
# accepts ?extent=xmin,ymin,xmax,ymax in EPSG:27700 (integers) — read from
# its own share-URL builder (sewebmap/js/map/map.js, loadCreateURLtext).
SEWEBMAP_BASE = "https://map.environment.gov.scot/sewebmap/"
MAGIC_BASE    = "https://magic.defra.gov.uk/MagicMap.html"


def magic_label():
    return "Scotland's Environment map" if is_scotland() else "MAGIC Map"


def magic_tooltip():
    if is_scotland():
        return ("Open Scotland's environment map (Scottish Government, SEPA and "
                "NatureScot layers) — with a redline present, frames the map on "
                "the site")
    return ("Open MAGIC (Multi-Agency Geographic Information for the "
            "Countryside) — with a redline present, frames the map on the site")


def magic_url(bbox=None):
    """Deep link for the active region. `bbox` = (xmin, ymin, xmax, ymax) in
    EPSG:27700, or None for the service's default view."""
    if is_scotland():
        if bbox:
            xmin, ymin, xmax, ymax = [int(round(v)) for v in bbox]
            return f"{SEWEBMAP_BASE}?extent={xmin},{ymin},{xmax},{ymax}"
        return SEWEBMAP_BASE
    if bbox:
        xmin, ymin, xmax, ymax = bbox
        # NB: the leading '?&' (not just '?') matches Defra's documented
        # examples and is required — MAGIC's parser splits location.search on
        # '&', so a param straight after '?' is read as '?box' and ignored.
        return f"{MAGIC_BASE}?&box={xmin:.0f}:{ymin:.0f}:{xmax:.0f}:{ymax:.0f}"
    return MAGIC_BASE


EPSL_SCOTLAND_TITLE = "EPSL — not available in Scotland"
EPSL_SCOTLAND_NOTE = (
    "Species licences in Scotland are issued by NatureScot, which does not "
    "publish licence locations. Its open licensing data is a monthly count "
    "of licences by type and species, with no site, grid reference or local "
    "authority, so a licence search around the redline is not possible.\n\n"
    "For a Scottish site, cite NatureScot's Summary of Licensing Data and "
    "local records-centre data (e.g. NBN Atlas) instead."
)
EPSL_SCOTLAND_URL = "https://www.nature.scot/doc/summary-licensing-data"
