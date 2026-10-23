# designated_sites.py
"""
Designated sites support for Biome.

Two capabilities:
  1. WMS overlay toggles  — fast tile display, no attribute data
  2. WFS 2km report       — fetches real geometries, computes area /
                            centroid grid ref / distance & direction
                            from the redline centroid

England data from Defra's environment.data.gov.uk DSP platform (confirmed
live). Scottish sites (see region.py) switch to NatureScot's GeoServer WFS
(ogc.nature.scot) and the Scottish Government ProtectedSites ArcGIS service
(maps.gov.scot) for National Scenic Areas / National Parks; priority habitats
come from the NatureScot Habitat Map of Scotland (HabMoS) with an EUNIS →
priority-habitat mapping (HABMOS_PRIORITY_MAP). NatureScot publishes no
licence locations, so EPSL is England-only.
"""

from qgis.core import QgsRasterLayer, QgsProject
from . import region as _region
from .layer_manager import REDLINE_NAME_ALIASES


def _search_radius(key, default=2000):
    """Configured desk study search radius (metres) for `key`, or `default` if
    the config module is unavailable. See config.DESK_SEARCH_RADII."""
    try:
        from .config import get_search_radius
        return get_search_radius(key)
    except Exception:
        return default


def _suppress_datum_dialog():
    """Pre-register OSTN15 on the project transform context so QGIS never
    shows the 'Select Transformation' dialog when BNG (EPSG:27700) layers
    are reprojected to the canvas CRS (EPSG:3857).  Safe to call repeatedly.

    IMPORTANT: never call proj.setTransformContext() while QGIS is inside
    QgsProject::readProjectFile — doing so triggers transformContextChanged →
    projectCrsChanged → QgsMapLayer::crs() on a half-initialised layer →
    access violation on Windows (QGIS 3.44).  We therefore defer the actual
    setTransformContext call to the next idle event-loop tick via QTimer.
    """
    def _apply():
        try:
            from qgis.core import (
                QgsCoordinateReferenceSystem, QgsDatumTransform,
            )
            from qgis.PyQt.QtCore import QSettings
            s = QSettings()
            s.setValue("projections/promptWhenMultipleTransformsExist", False)
            s.sync()  # force flush to registry on Windows
            # The dialog's real gate is the App-SECTION QgsSettings key
            # ([app] projections\… in the profile ini; global default TRUE) —
            # the plain write above never overrode it.
            from qgis.core import QgsSettings
            qs = QgsSettings()
            qs.setValue("projections/promptWhenMultipleTransformsExist",
                        False, section=QgsSettings.Section.App)
            qs.sync()
            proj  = QgsProject.instance()
            ctx   = proj.transformContext()
            bng   = QgsCoordinateReferenceSystem("EPSG:27700")
            merc  = QgsCoordinateReferenceSystem("EPSG:3857")
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            for src, dst in [(bng, merc), (bng, wgs84), (merc, bng), (wgs84, bng)]:
                ops = QgsDatumTransform.operations(src, dst)
                chosen = next(
                    (op for op in ops if op.id == 7952 or "OSTN15" in op.name.upper()),
                    ops[0] if ops else None,
                )
                if chosen:
                    ctx.addSourceDestinationDatumTransform(src, dst, chosen)
            proj.setTransformContext(ctx)
        except Exception:
            pass

    try:
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(0, _apply)
    except Exception:
        _apply()

# ---------------------------------------------------------------------------
# Layer catalogue
# ---------------------------------------------------------------------------
# Each entry: (key, display_label, wms_slug, wms_layer_name,
#              wfs_typename, name_field, area_field, area_is_ha,
#              colour_hex)
#
# area_is_ha: True  → field value is already in hectares
#             False → field value is m² (divide by 10000)
#
_DS_BASE_WMS = "https://environment.data.gov.uk/spatialdata/{slug}/wms"
_DS_BASE_WFS = "https://environment.data.gov.uk/spatialdata/{slug}/wfs"

# Confirmed area fields from live API inspection (March 2026):
#   SSSI        → measure     (ha)   shape_area is NULL
#   SAC         → sac_area    (ha)   area field is polygon m² not designation area
#   SPA         → spa_area    (ha)   area field is 0/null
#   Ramsar      → area        (ha)   shape_area is NULL
#   NNR         → measure     (ha)
#   LNR         → measure     (ha)   shape_area is NULL
#   AONB        → stat_area   (km², string) shape_area/measure both null
#   Ancient WD  → area        (ha)
#   Nat Parks   → measure     (ha)
#
# Tuple: (key, label, wms_slug, wms_layer, wfs_typename,
#          name_field, area_field, area_is_ha, colour)
# pattern field: "outline" — transparent fill, coloured outline only
#                "hatch"   — diagonal line hatch, transparent fill
#                "solid"   — solid semi-transparent fill
#         key, label, wms_slug, wms_layer, wfs_typename,
#         name_field, area_field, area_is_ha, fill_hex, outline_hex, pattern
DESIGNATED_LAYERS = [
    ("sssi",      "SSSI",           "sites-of-special-scientific-interest-england", "Sites_of_Special_Scientific_Interest_England", "Sites_of_Special_Scientific_Interest_England", "name",     "measure",   True,  None,      "#00BCD4", "hatch"),
    ("sac",       "SAC",            "special-areas-of-conservation-england",        "Special_Areas_of_Conservation_England",        "Special_Areas_of_Conservation_England",        "sac_name", "sac_area",  True,  None,      "#762A83", "hatch"),
    ("spa",       "SPA",            "special-protection-areas-england",             "Special_Protection_Areas_England",             "Special_Protection_Areas_England",             "spa_name", "spa_area",  True,  None,      "#2166AC", "outline"),
    ("ramsar",    "Ramsar",         "ramsar-england",                               "Ramsar_England",                               "Ramsar_England",                               "name",     "area",      True,  "#A6CEE3", "#1F78B4", "hatch"),
    ("nnr",       "NNR",            "national-nature-reserves-england",             "National_Nature_Reserves_England",             "National_Nature_Reserves_England",             "name",     "measure",   True,  None,      "#FFD92F", "outline"),
    ("lnr",       "LNR",            "local-nature-reserves-england",                "Local_Nature_Reserves_England",                "Local_Nature_Reserves_England",                "name",     "measure",   True,  None,      "#7B3294", "outline"),
    ("aonb",      "AONB",           "areas-of-outstanding-natural-beauty-england",  "Areas_of_Outstanding_Natural_Beauty_England",  "Areas_of_Outstanding_Natural_Beauty_England",  "name",     "stat_area", True,  "#CBB8DE", "#604579", "solid"),
    ("nat_parks", "National Parks", "national-parks-england",                       "National_Parks_England",                       "National_Parks_England",                       "name",     "measure",   True,  None,      "#1A9850", "outline"),
]


def _ds_area_hectares(key, area_field, raw_area, area_is_ha):
    """Normalise a designated-site source area to hectares.

    Natural England's AONB ``stat_area`` is labelled generically but is stored
    in square kilometres (for example High Weald is 1461.72, matching a
    geometry area of about 1461.74 km²). Other configured fields are hectares
    when ``area_is_ha`` is true, or square metres otherwise.
    """
    try:
        value = float(str(raw_area or 0).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0
    if key == "aonb" and area_field == "stat_area":
        return value * 100.0       # km² → ha
    return value if area_is_ha else value / 10000.0

# ── Scotland ────────────────────────────────────────────────────────────────
# Same 11-slot tuple and the SAME keys as England (so the desk-study buttons,
# caches and constraint buffers are keyed identically), but slot 2 names the
# SOURCE instead of a DEFRA slug:
#   "naturescot" → WFS 2.0 at ogc.nature.scot, slot 3 = WMS layer, slot 4 =
#                  WFS typename (GeoJSON out, EPSG:27700, verified Sept 2026:
#                  fields NAME / SITE_HA on every protectedareas layer)
#   "scotgov"    → ArcGIS REST ProtectedSites MapServer at maps.gov.scot,
#                  slot 3 = WMS layer name(s), slot 4 = layer id(s) (comma
#                  separated; National Parks are two layers), area field is
#                  the literal "st_area(shape)" in m².
# The "aonb" slot carries National Scenic Areas — Scotland's only national
# landscape designation and the AONB equivalent.
_NS_WFS_PA  = "https://ogc.nature.scot/geoserver/protectedareas/wfs"
_NS_WMS_PA  = "https://ogc.nature.scot/geoserver/protectedareas/wms"
_NS_WFS_HS  = "https://ogc.nature.scot/geoserver/habitatsandspecies/wfs"
_SG_REST_PS = "https://maps.gov.scot/server/rest/services/ScotGov/ProtectedSites/MapServer"

DESIGNATED_LAYERS_SCOTLAND = [
    ("sssi",      "SSSI",           "naturescot", "sssi",   "protectedareas:sssi",   "NAME", "SITE_HA", True, None,      "#00BCD4", "hatch"),
    ("sac",       "SAC",            "naturescot", "sac",    "protectedareas:sac",    "NAME", "SITE_HA", True, None,      "#762A83", "hatch"),
    ("spa",       "SPA",            "naturescot", "spa",    "protectedareas:spa",    "NAME", "SITE_HA", True, None,      "#2166AC", "outline"),
    ("ramsar",    "Ramsar",         "naturescot", "ramsar", "protectedareas:ramsar", "NAME", "SITE_HA", True, "#A6CEE3", "#1F78B4", "hatch"),
    ("nnr",       "NNR",            "naturescot", "nnr",    "protectedareas:nnr",    "NAME", "SITE_HA", True, None,      "#FFD92F", "outline"),
    ("lnr",       "LNR",            "naturescot", "lnr",    "protectedareas:lnr",    "NAME", "SITE_HA", True, None,      "#7B3294", "outline"),
    ("aonb",      "NSA",            "scotgov",    "NationalScenicAreas", "3",        "nsaname", "st_area(shape)", False, "#CBB8DE", "#604579", "solid"),
    ("nat_parks", "National Parks", "scotgov",    "CairngormsNationalPark,LochLomondTrossachsNationalPark", "0,1",
                                                                                     "npname",  "st_area(shape)", False, None, "#1A9850", "outline"),
]


def active_designated_layers():
    """The catalogue for the site's region (see region.active())."""
    return DESIGNATED_LAYERS_SCOTLAND if _region.is_scotland() else DESIGNATED_LAYERS


def _ds_entry(key):
    """Catalogue entry for `key` in the ACTIVE region (None if unknown)."""
    return next((e for e in active_designated_layers() if e[0] == key), None)


def _ds_entry_any(key):
    """Catalogue entry for `key` in the active region, falling back to the
    other region — for cleanups that must find a layer whatever region it
    was loaded under."""
    return (_ds_entry(key)
            or next((e for e in DESIGNATED_LAYERS if e[0] == key), None)
            or next((e for e in DESIGNATED_LAYERS_SCOTLAND if e[0] == key), None))


def ds_label(key):
    """Display label for a designation key in the active region ("AONB" in
    England, "NSA" in Scotland)."""
    e = _ds_entry(key)
    return e[1] if e else key.upper()


def all_ds_labels():
    """Every designated-site layer name either region can produce — for the
    layer-name matching in map exports, the info tool and buffer naming."""
    return ({e[1] for e in DESIGNATED_LAYERS}
            | {e[1] for e in DESIGNATED_LAYERS_SCOTLAND})


def _esri_polygon_to_geojson(rings):
    """Esri ring list → GeoJSON-style ("MultiPolygon", coords). Esri marks
    outer rings clockwise and holes anti-clockwise; each outer ring starts a
    new polygon and the holes that follow it belong to it."""
    polys = []
    for ring in rings or []:
        if len(ring) < 4:
            continue
        s = 0.0
        for i in range(len(ring) - 1):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[i + 1][0], ring[i + 1][1]
            s += (x2 - x1) * (y2 + y1)
        clockwise = s > 0
        if clockwise or not polys:
            polys.append([ring])
        else:
            polys[-1].append(ring)
    return {"type": "MultiPolygon", "coordinates": polys}


def _fetch_esri_polygons(layer_url, bbox_xy, out_fields="*", timeout=30):
    """Paginated ArcGIS REST query (envelope in EPSG:27700) returning raw
    esri features. Pure HTTP — worker-thread safe. Raises on failure."""
    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode
    feats, offset = [], 0
    while True:
        params = urlencode({
            "where":             "1=1",
            "geometry":          bbox_xy,
            "geometryType":      "esriGeometryEnvelope",
            "inSR":              "27700",
            "spatialRel":        "esriSpatialRelIntersects",
            "outFields":         out_fields,
            "returnGeometry":    "true",
            "outSR":             "27700",
            "resultOffset":      offset,
            "resultRecordCount": 1000,
            "f":                 "json",
        })
        data = json.loads(urlopen(f"{layer_url}/query?{params}", timeout=timeout)
                          .read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "ArcGIS error"))
        page = data.get("features", []) or []
        feats.extend(page)
        if not data.get("exceededTransferLimit", False) or not page:
            break
        offset += len(page)
    return feats

# ---------------------------------------------------------------------------
# WMS overlay helpers
# ---------------------------------------------------------------------------

def _ds_wms_uri(slug, layer_name):
    url = _DS_BASE_WMS.format(slug=slug)
    return (
        f"contextualWMSLegend=0"
        f"&crs=EPSG:3857"
        f"&dpiMode=7"
        f"&featureCount=10"
        f"&format=image/png"
        f"&layers={layer_name}"
        f"&styles="
        f"&url={url}"
    )


def _fetch_ds_features(key, bbox_param):
    """Raw WFS features for a designation `key` within `bbox_param`, cached for
    the current search area in _DS_FEATURE_CACHE.

    The overlay loader and the 2 km report both go through here, so a polygon
    set is downloaded ONCE per redline and then reused by the other — and a
    hide→show that has to rebuild a layer never hits the network again.  Cleared
    by clear_ds_cache() when the redline (and thus the 2 km area) changes.

    Returns (features, ok).  ok=False signals a network error; that result is
    NOT cached, so a later call retries.  Pure HTTP — safe on worker threads
    (each key is fetched by at most one caller per operation).
    """
    cached = _DS_FEATURE_CACHE.get(key)
    if cached is not None:
        return cached, True

    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode

    entry = _ds_entry(key)
    if entry is None:
        return [], True
    source, wfs_typename = entry[2], entry[4]

    if source == "scotgov":
        # Scottish Government ArcGIS REST (NSA / National Parks): one or more
        # layer ids, esri JSON → GeoJSON-shaped so the callers stay generic.
        bbox_xy = ",".join(bbox_param.split(",")[:4])
        feats = []
        try:
            for lid in wfs_typename.split(","):
                for f in _fetch_esri_polygons(f"{_SG_REST_PS}/{lid.strip()}", bbox_xy):
                    feats.append({
                        "properties": f.get("attributes", {}) or {},
                        "geometry":   _esri_polygon_to_geojson(
                            (f.get("geometry") or {}).get("rings", [])),
                    })
        except Exception:
            return [], False
        _DS_FEATURE_CACHE[key] = feats
        return feats, True

    if source == "naturescot":
        wfs_url = _NS_WFS_PA
    else:
        wfs_url = _DS_BASE_WFS.format(slug=source)
    params  = urlencode({
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    wfs_typename,
        "BBOX":         bbox_param,
        "SRSNAME":      "EPSG:27700",
        "outputFormat": "application/json",
        "count":        "500",
    })
    try:
        resp  = urlopen(f"{wfs_url}?{params}", timeout=20)
        feats = json.loads(resp.read().decode("utf-8")).get("features", []) or []
    except Exception:
        return [], False

    _DS_FEATURE_CACHE[key] = feats
    return feats, True


def fetch_designated_geojson(key, easting, northing, bbox_param=None):
    """Cached HTTP fetch — no QGIS objects. Safe to call from worker threads.

    Returns (key, features_list) on success, or (key, None) if there are no
    features or an error occurred.  Results are cached per search area (see
    _fetch_ds_features) so the overlay and the report share a single download.

    bbox_param: optional pre-computed bbox string "xmin,ymin,xmax,ymax,EPSG:27700".
    If not supplied, falls back to centroid ±2km square.
    """
    if bbox_param is None:
        RADIUS = _search_radius("designated_sites")
        xmin, ymin = easting - RADIUS, northing - RADIUS
        xmax, ymax = easting + RADIUS, northing + RADIUS
        bbox_param = f"{xmin},{ymin},{xmax},{ymax},EPSG:27700"

    feats, _ok = _fetch_ds_features(key, bbox_param)
    return key, (feats if feats else None)


def load_designated_layer(key, easting=None, northing=None, redline_geom=None,
                          features=None):
    """Fetch and display a designated site layer within 2km of the redline edge
    (or centroid if no redline available).

    On the first call the layer is fetched via WFS and stored in _DS_LAYER_CACHE.
    On subsequent calls within the same session the cached layer is simply made
    visible again — no network request is made.

    If `features` (a list of WFS GeoJSON feature dicts) is supplied, it is used
    directly and NO network request is made here.  This lets the "Show All"
    path do a single parallel pre-fetch on worker threads and skip a second,
    UI-blocking download on the main thread.
    """
    import json
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
        QgsPointXY, QgsSingleSymbolRenderer, QgsFillSymbol,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    entry = _ds_entry(key)
    if entry is None:
        return False, f"Unknown key: {key}"

    (key, label, wms_slug, wms_layer, wfs_typename,
     name_field, area_field, area_is_ha, fill_hex, outline_hex, pattern) = entry

    # ── Return cached layer if still alive in the project ─────────────────
    # (A layer cached under the OTHER region's label — "AONB" vs "NSA" — is
    # dropped so the region switch never re-shows stale data.)
    cached = _DS_LAYER_CACHE.get(key)
    if cached is not None and _ds_layer_in_project(cached):
        try:
            if " ".join(cached.name().split()) != label:
                QgsProject.instance().removeMapLayer(cached.id())
                _DS_LAYER_CACHE.pop(key, None)
                cached = None
        except RuntimeError:
            cached = None
    if cached is not None and _ds_layer_in_project(cached):
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(cached.id())
        if node is not None:
            node.setItemVisibilityChecked(True)
        grp = _get_or_create_ds_group(root)
        # Re-parent if somehow the node is not inside the DS group
        if node is None:
            grp.addLayer(cached).setItemVisibilityChecked(True)
        grp.setItemVisibilityChecked(True)
        return True, label

    # ── First load ────────────────────────────────────────────────────────
    # Remove any stale instance (legacy DS_key name or label name)
    for old_name in (label, f"DS_{key}"):
        for old in QgsProject.instance().mapLayersByName(old_name):
            QgsProject.instance().removeMapLayer(old.id())
    _DS_LAYER_CACHE.pop(key, None)

    # Need a centroid to query / post-filter against the search buffer
    if easting is None or northing is None:
        return False, "no_centroid"

    RADIUS = _search_radius("designated_sites")
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)

    # Reuse a caller-supplied pre-fetch when available; otherwise fetch via WFS
    # now (single-layer toggle path).  Skipping the download here is what keeps
    # the "Show All" path off the main thread.
    if features is None:
        bb = search_buffer.boundingBox()
        bbox_param = f"{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()},EPSG:27700"
        features, ok = _fetch_ds_features(key, bbox_param)
        if not ok:
            return False, "WFS fetch failed"

    if not features:
        return False, "no_features"

    lyr = QgsVectorLayer(
        f"Polygon?crs=EPSG:27700"
        f"&field=name:string(200)"
        f"&field=area_ha:double",
        label, "memory"
    )
    dp = lyr.dataProvider()
    qfeats = []

    for feat in features:
        props = feat.get("properties", {})
        geom  = feat.get("geometry",   {})
        feat_name = str(props.get(name_field, "") or "").strip()
        raw_area  = props.get(area_field) or 0
        area_ha = _ds_area_hectares(key, area_field, raw_area, area_is_ha)

        gtype  = geom.get("type", "")
        coords = geom.get("coordinates", [])
        if not coords:
            continue

        def _rings(poly_coords):
            return [[QgsPointXY(c[0], c[1]) for c in ring] for ring in poly_coords]

        if gtype == "Polygon":
            qg = QgsGeometry.fromPolygonXY(_rings(coords))
        elif gtype == "MultiPolygon":
            qg = QgsGeometry.fromMultiPolygonXY([_rings(p) for p in coords])
        else:
            continue

        if qg.isEmpty():
            continue

        f = QgsFeature(lyr.fields())
        f.setGeometry(qg)
        f.setAttributes([feat_name, round(area_ha, 2)])
        qfeats.append(f)

    if not qfeats:
        return False, "no_features"

    # Post-filter: keep only features whose geometry actually intersects the
    # 2km search buffer. Large AONB/National Park polygons often cover the
    # bbox but their boundary may be nowhere near the site. (search_buffer was
    # already built above.)
    qfeats = [f for f in qfeats if f.geometry().intersects(search_buffer)]

    if not qfeats:
        return False, "no_features"

    dp.addFeatures(qfeats)
    lyr.updateExtents()

    # Style — categorized by site name so each site appears in the legend.
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsRendererCategory,
                            QgsLinePatternFillSymbolLayer, QgsSimpleFillSymbolLayer,
                            QgsSymbolLayerUtils)

    def _make_ds_symbol(outline_hex, fill_hex, ds_pattern):
        """Build a QgsFillSymbol for a designated site layer.

        Patterns:
          "outline" — transparent fill, coloured outline only
          "hatch"   — diagonal line hatch in outline colour, transparent base
          "solid"   — solid fill in fill_hex at 80 alpha, outlined in outline_hex
        """
        if ds_pattern == "outline":
            return QgsFillSymbol.createSimple({
                "color":         "0,0,0,0",       # fully transparent
                "outline_color": outline_hex,
                "outline_width": "0.7",
                "outline_style": "solid",
            })
        elif ds_pattern == "hatch":
            # Base: transparent fill with coloured outline
            sym = QgsFillSymbol.createSimple({
                "color":         "0,0,0,0",
                "outline_color": outline_hex,
                "outline_width": "0.7",
                "outline_style": "solid",
            })
            # Add diagonal line-pattern fill layer on top
            hatch = QgsLinePatternFillSymbolLayer()
            hatch.setColor(QColor(outline_hex))
            hatch.setLineWidth(0.35)
            hatch.setDistance(3.0)
            hatch.setLineAngle(45.0)
            sym.appendSymbolLayer(hatch)
            return sym
        else:  # "solid"
            c = QColor(fill_hex if fill_hex else outline_hex)
            c.setAlpha(90 if key == "aonb" else 80)
            return QgsFillSymbol.createSimple({
                "color":         c.name(QColor.NameFormat.HexArgb),
                "outline_color": outline_hex,
                "outline_width": "1.0" if key == "aonb" else "0.6",
                "outline_style": "solid",
            })

    site_names = sorted({f.attribute("name") for f in lyr.getFeatures()
                         if f.attribute("name")})

    categories = []
    for site_name in site_names:
        sym = _make_ds_symbol(outline_hex, fill_hex, pattern)
        categories.append(QgsRendererCategory(site_name, sym, site_name))

    fallback_sym = _make_ds_symbol(outline_hex, fill_hex, pattern)
    categories.append(QgsRendererCategory("", fallback_sym, label))

    renderer = QgsCategorizedSymbolRenderer("name", categories)
    lyr.setRenderer(renderer)
    lyr.setOpacity(0.85)

    # Labels — site name
    pal = QgsPalLayerSettings()
    pal.enabled   = True
    pal.fieldName = "name"
    if key == "aonb":
        # Large national landscape polygons often contain the whole map while
        # their boundary lies beyond it. Label the actual perimeter only: when
        # no edge is visible, the legend and tint communicate the designation
        # without dropping a misleading centroid label inside the Red Line.
        try:
            pal.placement = QgsPalLayerSettings.Placement.PerimeterCurved
        except AttributeError:
            pal.placement = QgsPalLayerSettings.PerimeterCurved
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor(outline_hex))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    root = QgsProject.instance().layerTreeRoot()
    grp  = _get_or_create_ds_group(root)
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)
    grp.addLayer(lyr).setItemVisibilityChecked(True)
    grp.setItemVisibilityChecked(True)

    # Store in cache for instant re-display on subsequent toggles
    _DS_LAYER_CACHE[key] = lyr

    return True, label


def remove_designated_layer(key):
    """Hide the DS layer for this key (keep it cached for instant re-display).

    The layer stays in the QGIS project and _DS_LAYER_CACHE; its layer-tree
    node is simply unchecked so it disappears from the canvas.
    """
    from qgis.core import QgsProject
    cached = _DS_LAYER_CACHE.get(key)
    if cached is not None and _ds_layer_in_project(cached):
        node = QgsProject.instance().layerTreeRoot().findLayer(cached.id())
        if node is not None:
            node.setItemVisibilityChecked(False)
        return
    # Fallback: remove any layer by name (legacy or uncached) — both regions'
    # labels, since the layer may have been loaded before a region switch.
    labels = {e[1] for e in (DESIGNATED_LAYERS + DESIGNATED_LAYERS_SCOTLAND)
              if e[0] == key}
    for name in sorted(labels) + [f"DS_{key}"]:
        for lyr in QgsProject.instance().mapLayersByName(name):
            QgsProject.instance().removeMapLayer(lyr.id())


def _get_or_create_ds_group(root):
    grp = root.findGroup("Designated Sites")
    if grp is None:
        bm = root.findGroup("Base Maps")
        idx = root.children().index(bm) if bm is not None else 0
        grp = root.insertGroup(idx, "Designated Sites")
        grp.setExpanded(False)
    return grp


# ---------------------------------------------------------------------------
# Designated site layer cache
# ---------------------------------------------------------------------------
# Keyed by designation key (e.g. "sssi").  Stores the QgsVectorLayer object
# so that toggling off and back on skips the WFS fetch and just shows/hides
# the existing layer.  Cleared when a new redline is drawn.

_DS_LAYER_CACHE: dict = {}   # key → QgsVectorLayer

# Raw WFS features per designation key for the CURRENT search area.  Shared by
# the overlay loader and the 2 km report so the polygons download once per
# redline; also lets a hide→show rebuild a layer without re-fetching.
_DS_FEATURE_CACHE: dict = {}   # key → list[raw WFS feature dict]


def clear_ds_cache():
    """Discard cached DS layers and feature data (call when redline changes)."""
    global _DS_LAYER_CACHE
    _DS_LAYER_CACHE.clear()
    _DS_FEATURE_CACHE.clear()
    _AW_LAYER_CACHE.clear()
    _HABMOS_CACHE.clear()


def _ds_layer_in_project(lyr):
    """Return True if lyr is still a live layer registered in the project.

    The cached wrapper may outlive its underlying C++ QgsVectorLayer (e.g.
    the user deleted the layer manually); calling .id() on it then raises
    RuntimeError. Treat that case as 'not in project'.
    """
    from qgis.core import QgsProject
    try:
        return lyr.id() in QgsProject.instance().mapLayers()
    except RuntimeError:
        return False


def adopt_project_layers():
    """Seed the DS / Ancient Woodland layer caches from layers already in the
    project — a reopened save file or a plugin reload mid-session — so the
    next toggle reuses them instead of deleting and re-fetching. Names are
    matched with whitespace collapsed because legend label wrapping in older
    builds could leave a newline inside a saved layer's name."""
    from qgis.core import QgsProject

    def _flat(s):
        return " ".join((s or "").split())

    by_name = {}
    for lyr in QgsProject.instance().mapLayers().values():
        by_name.setdefault(_flat(lyr.name()), lyr)

    # Active region's labels first so "NSA" wins over "AONB" in Scotland when
    # (oddly) both are present; the other region's label is adopted only if
    # the active one is absent.
    other = (DESIGNATED_LAYERS if _region.is_scotland()
             else DESIGNATED_LAYERS_SCOTLAND)
    for entry in list(active_designated_layers()) + list(other):
        key, label = entry[0], entry[1]
        cached = _DS_LAYER_CACHE.get(key)
        if (cached is None or not _ds_layer_in_project(cached)) and label in by_name:
            _DS_LAYER_CACHE[key] = by_name[label]

    cached = _AW_LAYER_CACHE.get("ancient_wood")
    if ((cached is None or not _ds_layer_in_project(cached))
            and _flat(_AW_LAYER_NAME) in by_name):
        _AW_LAYER_CACHE["ancient_wood"] = by_name[_flat(_AW_LAYER_NAME)]


def _fetch_ds_layer(entry, bbox_param, easting, northing, redline_geom,
                    search_buffer, radius=2000):
    """Fetch and process a single designated site WFS layer.
    Pure network + data I/O — safe to run in a thread.
    QGIS geometry construction (QgsPointXY etc.) happens here but that is
    thread-safe for memory layers; only canvas/project operations must be
    on the main thread.

    `radius` (the configured designated-sites search radius, metres) is read on
    the main thread by the caller and passed in so no QgsSettings access happens
    on the worker thread.
    """
    (key, label, wms_slug, wms_layer, wfs_typename,
     name_field, area_field, area_is_ha, fill_hex, outline_hex, pattern) = entry

    RADIUS = radius

    # Shared cache: if the overlay already pulled this layer for the current
    # redline, reuse it; otherwise fetch once and populate it for next time.
    features, _ok = _fetch_ds_features(key, bbox_param)

    layer_results = []
    for feat in features:
        props     = feat.get("properties", {})
        geom      = feat.get("geometry",   {})
        feat_name = str(props.get(name_field, "Unknown")).strip()

        raw_area = props.get(area_field) or 0
        area_ha = _ds_area_hectares(key, area_field, raw_area, area_is_ha)

        cx, cy = _geom_centroid(geom)

        feat_geom = _geojson_rings_to_qgsgeom(geom)

        # Pre-filter: skip features whose geometry doesn't intersect the true
        # 2km buffer polygon — eliminates bbox corner features without a GEOS
        # distance call.
        if not feat_geom.isEmpty() and not search_buffer.isEmpty():
            if not search_buffer.intersects(feat_geom):
                continue

        dist, direction = _redline_distance_and_direction(
            redline_geom, feat_geom, cx, cy
        )

        if dist > RADIUS:
            continue

        try:
            from .gridref_utils import osgb_en_to_gridref
            centroid_gr = osgb_en_to_gridref(int(cx), int(cy), 6)
        except Exception:
            centroid_gr = f"{int(cx)},{int(cy)}"

        layer_results.append({
            "type":        label,
            "name":        feat_name,
            "area_ha":     round(area_ha, 2),
            "centroid_gr": centroid_gr,
            "distance_m":  dist,
            "direction":   direction,
        })

    return layer_results


def query_designated_sites_2km(easting, northing, redline_geom=None):
    """
    Query all 9 designated site layers for features within 2 km of (easting, northing).

    All 9 WFS requests are fired concurrently (ThreadPoolExecutor) so total
    time is approximately the slowest single request rather than the sum of all.

    If redline_geom (QgsGeometry, BNG) is supplied, distance is the true minimum
    edge-to-edge distance from the redline boundary to each feature boundary using
    QgsGeometry.distance() (GEOS). Otherwise falls back to centroid-to-centroid.

    Returns a list of dicts:
        {
          'type':        str   — e.g. "SSSI"
          'name':        str
          'area_ha':     float — area in hectares (rounded to 2 dp)
          'centroid_gr': str   — BNG grid reference of feature centroid
          'distance_m':  int   — edge-to-edge distance in metres (0 = overlap)
          'direction':   str   — cardinal direction e.g. "NE", "SW"
        }

    Results are sorted by distance ascending. Where the source publishes one
    designation as several component features, only the nearest component is
    returned for that designation.
    Errors for individual layers are silently skipped (network may be down).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    RADIUS = _search_radius("designated_sites")

    # Build the true search buffer from the redline edge (or centroid circle).
    # WFS only accepts a BBOX, so we use the buffer's bounding box for the request
    # and then filter results with buffer.intersects() in _fetch_ds_layer.
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bb = search_buffer.boundingBox()
    bbox_param = f"{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()},EPSG:27700"

    results = []

    # Fire all 9 WFS requests in parallel — ~9x faster than sequential
    with ThreadPoolExecutor(max_workers=9) as pool:
        futures = {
            pool.submit(_fetch_ds_layer, entry, bbox_param, easting, northing, redline_geom, search_buffer, RADIUS): entry
            for entry in active_designated_layers()
        }
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception:
                pass  # individual layer errors are non-fatal

    results.sort(key=lambda r: r["distance_m"])

    # Natural England and NatureScot can publish separate WFS features for the
    # component polygons of a single designation.  The report is a site list,
    # not a component-feature list, so retain the first (nearest) result for
    # each designation.  Include the designation type in the key because the
    # same named site may legitimately be both, for example, an SAC and SSSI.
    nearest_by_designation = {}
    for result in results:
        identity = (
            " ".join(str(result.get("type", "")).split()).casefold(),
            " ".join(str(result.get("name", "")).split()).casefold(),
        )
        nearest_by_designation.setdefault(identity, result)

    return list(nearest_by_designation.values())


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _build_search_buffer(easting, northing, redline_geom, radius=2000, segments=72):
    """Return a QgsGeometry polygon that is exactly `radius` metres from the
    redline EDGE (or a circle from the centroid if redline_geom is unavailable).
    This is used as the true search area — a proper circular buffer, not a box.
    """
    import math
    from qgis.core import QgsGeometry, QgsPointXY

    if redline_geom and not redline_geom.isEmpty():
        return redline_geom.buffer(radius, segments=segments)

    # Fallback: circle from centroid
    ring = [
        QgsPointXY(
            easting  + radius * math.cos(2 * math.pi * i / segments),
            northing + radius * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments + 1)
    ]
    return QgsGeometry.fromPolygonXY([ring])


def _buffer_to_esri_rings(buffer_geom):
    """Serialise a QgsGeometry polygon/multipolygon to an esriJSON rings list
    suitable for use as geometry= in an ArcGIS REST query with
    geometryType=esriGeometryPolygon.  Returns a list of rings (each ring is a
    list of [x, y] pairs).
    """
    if buffer_geom is None or buffer_geom.isEmpty():
        return []
    # asPolygon() returns list-of-rings for Polygon, asMultiPolygon() for Multi
    try:
        polys = buffer_geom.asMultiPolygon()
    except Exception:
        polys = None
    if not polys:
        try:
            poly = buffer_geom.asPolygon()
            polys = [poly] if poly else []
        except Exception:
            polys = []
    rings = []
    for poly in polys:
        for ring in poly:
            rings.append([[pt.x(), pt.y()] for pt in ring])
    return rings


def _geojson_rings_to_qgsgeom(geom):
    """Convert a GeoJSON geometry dict (Polygon/MultiPolygon) to QgsGeometry (BNG)."""
    from qgis.core import QgsGeometry, QgsPointXY
    gtype  = geom.get("type", "")
    coords = geom.get("coordinates", [])

    def _rings(poly_coords):
        return [[QgsPointXY(x, y) for x, y in ring] for ring in poly_coords]

    if gtype == "Polygon":
        return QgsGeometry.fromPolygonXY(_rings(coords))
    elif gtype == "MultiPolygon":
        return QgsGeometry.fromMultiPolygonXY([_rings(poly) for poly in coords])
    return QgsGeometry()


def _esri_rings_to_qgsgeom(geom):
    """Convert an esriJSON geometry dict (rings) to QgsGeometry (BNG).

    esriJSON uses winding order to distinguish outer rings from holes:
      - Outer rings are clockwise (positive signed area in BNG coords)
      - Holes are counter-clockwise (negative signed area)

    Builds a proper multipolygon so separate IRZ zone parts render correctly.
    """
    from qgis.core import QgsGeometry, QgsPointXY

    rings = geom.get("rings", [])
    if not rings:
        return QgsGeometry()

    def _signed_area(ring):
        n = len(ring)
        if n < 3:
            return 0.0
        a = 0.0
        for i in range(n):
            j = (i + 1) % n
            a += ring[i][0] * ring[j][1]
            a -= ring[j][0] * ring[i][1]
        return a  # positive = CW = outer ring in BNG

    qrings = [[QgsPointXY(pt[0], pt[1]) for pt in ring] for ring in rings]
    areas  = [_signed_area(ring) for ring in rings]

    outer_idx = [i for i, a in enumerate(areas) if a >= 0]
    hole_idx  = [i for i, a in enumerate(areas) if a < 0]

    if not outer_idx:
        outer_idx = list(range(len(rings)))
        hole_idx  = []

    polys = []
    for oi in outer_idx:
        poly = [qrings[oi]] + [qrings[hi] for hi in hole_idx]
        polys.append(poly)

    if len(polys) == 1:
        return QgsGeometry.fromPolygonXY(polys[0])
    return QgsGeometry.fromMultiPolygonXY(polys)


def _esri_point_to_qgsgeom(geom):
    """Convert an esriJSON point {x, y} to QgsGeometry."""
    from qgis.core import QgsGeometry, QgsPointXY
    x = geom.get("x", 0) or 0
    y = geom.get("y", 0) or 0
    return QgsGeometry.fromPointXY(QgsPointXY(x, y))


def _geom_centroid(geom):
    """Return (cx, cy) centroid of a GeoJSON geometry (Polygon or MultiPolygon).
    Used only for grid-ref calculation — distance uses QgsGeometry.distance()."""
    gtype  = geom.get("type", "")
    coords = geom.get("coordinates", [])
    all_x, all_y = [], []

    def _collect(rings):
        if rings:
            for x, y in rings[0]:
                all_x.append(x)
                all_y.append(y)

    if gtype == "Polygon":
        _collect(coords)
    elif gtype == "MultiPolygon":
        for poly in coords:
            _collect(poly)

    if not all_x:
        return 0.0, 0.0
    return sum(all_x) / len(all_x), sum(all_y) / len(all_y)


def _cardinal(dx, dy):
    """Return 8-point cardinal direction string for a vector (dx, dy)."""
    import math
    angle   = math.degrees(math.atan2(dy, dx))
    bearing = (90 - angle) % 360
    dirs    = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx     = int((bearing + 22.5) / 45) % 8
    return dirs[idx]


def _redline_distance_and_direction(redline_geom, feature_geom, feat_cx, feat_cy):
    """
    Compute the true minimum edge-to-edge distance from the redline boundary to
    a feature geometry using QgsGeometry.distance() (GEOS under the hood).

    Direction is computed from the redline centroid to the feature centroid —
    this gives the correct bearing regardless of how the minimum-distance point
    is oriented.

    Args:
        redline_geom:  QgsGeometry of the redline polygon (BNG)
        feature_geom:  QgsGeometry of the designated site / habitat / EPSL (BNG)
        feat_cx/cy:    feature centroid coordinates (for direction calculation)

    Returns:
        (distance_m, direction_str)
        distance_m = 0 if feature overlaps or touches the redline
    """
    if redline_geom is None or redline_geom.isEmpty()        or feature_geom is None or feature_geom.isEmpty():
        # Fallback: centroid-to-centroid
        import math
        rl_c = redline_geom.centroid().asPoint() if redline_geom and not redline_geom.isEmpty() else None
        if rl_c:
            dx = feat_cx - rl_c.x()
            dy = feat_cy - rl_c.y()
        else:
            dx, dy = 0, 0
        dist = math.sqrt(dx * dx + dy * dy)
        return round(dist), _cardinal(dx, dy)

    dist = redline_geom.distance(feature_geom)

    # Direction: from redline centroid to feature centroid
    rl_c = redline_geom.centroid().asPoint()
    dx   = feat_cx - rl_c.x()
    dy   = feat_cy - rl_c.y()
    return round(max(dist, 0)), _cardinal(dx, dy)


# ---------------------------------------------------------------------------
# Priority Habitat Inventory — 2 km query
# ---------------------------------------------------------------------------

_PHI_BASE = (
    "https://services.arcgis.com/JJzESW51TqeY9uat/arcgis/rest/services"
    # Natural England republished the feature layer at service layer 1;
    # layer 0 now returns "requested layer was not found". Ancient Woodland
    # was unaffected because it is supplied by a separate WFS.
    "/Priority_Habitats_Inventory_England/FeatureServer/1/query"
)

# ``NMHAB`` is not a habitat type.  Natural England uses it where no main
# habitat could be assigned to the polygon, while ``AddHabs`` records the
# habitat(s) that may occur within it.  Decode those codes before report rows
# are grouped so the table, closest-per-type selection, and summary counts all
# use the informative attribution instead of treating NMHAB as a sixth type.
_PHI_ADDITIONAL_HABITAT_NAMES = {
    "BLBOG": "Blanket bog",
    "CALAM": "Calaminarian grassland",
    "CFPGM": "Coastal and floodplain grazing marsh",
    "CSDUN": "Coastal sand dunes",
    "CVSHI": "Coastal vegetated shingle",
    "DWOOD": "Deciduous woodland",
    "GQSIG": "Good quality semi improved grassland",
    "LCGRA": "Lowland calcareous grassland",
    "LDAGR": "Lowland dry acid grassland",
    "LFENS": "Lowland fens",
    "LHEAT": "Lowland heathland",
    "LMEAD": "Lowland meadows",
    "LPAVE": "Limestone pavement",
    "LRBOG": "Lowland raised bog",
    "MCSLP": "Maritime cliff and slope",
    "MUDFL": "Mudflats",
    "PMGRP": "Purple moor grass and rush pastures",
    "PONDS": "Ponds",
    "RBEDS": "Reedbeds",
    "SALTM": "Coastal saltmarsh",
    "SLAGO": "Saline lagoons",
    "TORCH": "Traditional orchard",
    "UCGRA": "Upland calcareous grassland",
    "UFFSW": "Upland flushes, fens and swamps",
    "UHEAT": "Upland heathland",
    "UHMEA": "Upland hay meadow",
}


def _phi_report_habitat(main_habitat, habitat_code, additional_habitats):
    """Return the habitat label/code that a PHI report should display.

    For ordinary polygons the main habitat is unchanged.  For NMHAB polygons,
    decode ``AddHabs`` and show a slash-separated mosaic where more than one
    additional habitat is supplied.  Unknown future codes remain visible
    rather than being silently discarded.
    """
    main_habitat = str(main_habitat or "").strip()
    habitat_code = str(habitat_code or "").strip()
    additional_habitats = str(additional_habitats or "").strip()

    is_no_main = (
        habitat_code.upper() == "NMHAB"
        or main_habitat.casefold() == "no main habitat but additional habitats present"
    )
    if not is_no_main or not additional_habitats:
        return main_habitat, habitat_code

    codes = []
    for raw_code in additional_habitats.replace(";", ",").split(","):
        code = raw_code.strip().upper()
        if code and code not in codes:
            codes.append(code)
    if not codes:
        return main_habitat, habitat_code

    labels = [_PHI_ADDITIONAL_HABITAT_NAMES.get(code, code) for code in codes]
    return " / ".join(labels), ", ".join(codes)

# ── Scotland: Habitat Map of Scotland (HabMoS) ──────────────────────────────
# Scotland has no Priority Habitats Inventory. HabMoS (NatureScot, OGL) is a
# full EUNIS-coded land-cover composite (NVC conversions, NWSS, NFI, OS
# VectorMap, saltmarsh/dune surveys…). Only polygons whose EUNIS code maps
# to a Scottish Biodiversity List / UK BAP priority habitat are treated as
# "priority"; everything else (improved grassland, plantations, buildings,
# arable, generic "G1"/"C1" land cover…) is dropped. The mapping keys are
# EUNIS code prefixes; the most specific (longest) match wins, and an exact
# match beats a prefix. Habitat labels reuse the England PHI names where the
# habitat is the same thing so colours and group buttons carry across.
_HABMOS_TYPENAME = "habitatsandspecies:habmos"
HABMOS_PRIORITY_MAP = [
    # (eunis_prefix, group_key, habitat_label)
    ("G1.21",  "woodland",  "Wet woodland"),                        # H91E0 alluvial forests
    ("G1.41",  "woodland",  "Wet woodland"),                        # alder swamp woods
    ("G1.51",  "woodland",  "Wet woodland"),                        # Sphagnum birch woods
    ("F9.2",   "woodland",  "Wet woodland"),                        # willow carr
    ("G1.8",   "woodland",  "Upland oakwood"),                      # H91A0 old sessile oak, Q. robur-Betula
    ("G1.91",  "woodland",  "Upland birchwoods"),
    ("G1.A11", "woodland",  "Lowland mixed deciduous woodland"),    # Atlantic Quercus w/ bluebell
    ("G1.A2",  "woodland",  "Upland mixed ashwoods"),               # H9180 Tilio-Acerion, Fraxinus woods
    ("G3.4",   "woodland",  "Native pine woodlands"),               # H91C0 Caledonian forest
    ("E1.26",  "grassland", "Lowland calcareous grassland"),        # H6210
    ("E1.72#", "grassland", "Species-rich Nardus grassland"),       # H6230
    ("E2.21",  "grassland", "Lowland meadows"),                     # Atlantic hay meadows
    ("E2.112", "grassland", "Lowland meadows"),                     # Cynosurus-Centaurea pastures
    ("E4.12",  "grassland", "Upland calcareous grassland"),         # H6170
    ("E5.59",  "grassland", "Tall herb communities"),               # H6430
    ("D1.1",   "heath_bog", "Lowland raised bog"),
    ("D1.2",   "heath_bog", "Blanket bog"),                         # H7130
    ("F4.11",  "heath_bog", "Wet heath"),                           # H4010
    ("F4.13",  "heath_bog", "Wet heath"),
    ("F4.2",   "heath_bog", "Dry heath"),                           # H4030
    ("F2.25",  "heath_bog", "Mountain heaths and willow scrub"),    # H4060
    ("F2.1",   "heath_bog", "Mountain heaths and willow scrub"),    # H4080 montane willow
    ("E4.32",  "heath_bog", "Mountain heaths and willow scrub"),    # H6150 siliceous alpine
    ("E4.21",  "heath_bog", "Mountain heaths and willow scrub"),
    ("E3.41",  "heath_bog", "Purple moor grass and rush pastures"),
    ("E3.42",  "heath_bog", "Purple moor grass and rush pastures"),
    ("E3.51",  "heath_bog", "Purple moor grass and rush pastures"), # H6410 Molinia meadows
    ("C3.2",   "wetland",   "Reedbeds"),
    ("C3.21",  "wetland",   "Reedbeds"),
    ("C3.23",  "wetland",   "Upland flushes fens and swamps"),
    ("C3.24",  "wetland",   "Upland flushes fens and swamps"),
    ("C3.25",  "wetland",   "Upland flushes fens and swamps"),
    ("C3.26",  "wetland",   "Upland flushes fens and swamps"),
    ("C3.27",  "wetland",   "Upland flushes fens and swamps"),
    ("C3.29",  "wetland",   "Upland flushes fens and swamps"),
    ("D2.2",   "wetland",   "Upland flushes fens and swamps"),      # poor fens, spring mires
    ("D2.3",   "wetland",   "Upland flushes fens and swamps"),      # H7140 transition mires
    ("D4.1",   "wetland",   "Lowland fens"),                        # H7230 alkaline fens, H7220 springs
    ("D4.2",   "wetland",   "Upland flushes fens and swamps"),      # H7240 alpine pioneer formations
    ("C1.1#",  "wetland",   "Oligotrophic and dystrophic lakes"),   # H3110/H3130 (part)
    ("C1.2#",  "wetland",   "Mesotrophic lakes"),                   # H3130/H3140
    ("C1.3#",  "wetland",   "Eutrophic standing waters"),           # H3150
    ("C1.4#",  "wetland",   "Oligotrophic and dystrophic lakes"),   # H3160
    ("E3.44",  "wetland",   "Coastal and floodplain grazing marsh"),
    ("A2.5",   "coastal",   "Coastal saltmarsh"),
    ("A2.3",   "coastal",   "Mudflats"),
    ("B1.3",   "coastal",   "Coastal sand dunes"),
    ("B1.4",   "coastal",   "Coastal sand dunes"),
    ("B1.5",   "coastal",   "Coastal sand dunes"),
    ("B1.6",   "coastal",   "Coastal sand dunes"),
    ("B1.8",   "coastal",   "Coastal sand dunes"),
    ("B2.4",   "coastal",   "Coastal vegetated shingle"),
    ("B2.12",  "coastal",   "Coastal vegetated shingle"),
    ("B3.3",   "coastal",   "Maritime cliff and slope"),
    ("H2.",    "rock_urban", "Inland rock outcrop and scree habitats"),
    ("H3.",    "rock_urban", "Inland rock outcrop and scree habitats"),
]
_HABMOS_CACHE = {}     # bbox string → classified esri-like features (per redline)


def _habmos_clean_code(code):
    """HabMoS codes carry stray non-ASCII markers ('A2.54�', 'C1.2#1�');
    keep only the EUNIS characters."""
    return "".join(ch for ch in str(code or "") if ch.isalnum() or ch in ".#")


def habmos_classify(code):
    """(group_key, habitat_label) for an EUNIS code, or None if it is not a
    priority habitat under HABMOS_PRIORITY_MAP."""
    code = _habmos_clean_code(code)
    if not code:
        return None
    best = None
    for prefix, group, label in HABMOS_PRIORITY_MAP:
        if code == prefix:
            return group, label
        if code.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, group, label)
    return (best[1], best[2]) if best else None


def _habmos_features(bbox):
    """All priority-classified HabMoS polygons intersecting `bbox`
    ("xmin,ymin,xmax,ymax", EPSG:27700), as esri-like feature dicts
    ({"attributes": {MainHabs, HabCodes, AreaHa, FeatDesc, _group},
      "geometry": {"rings": [...]}}) so the PHI loaders and the 2 km report
    consume them exactly like Natural England's service. Multipolygons are
    split into one feature per part (the loaders build a single polygon from
    all rings) with the mapped HAB_AREA apportioned by part area.

    Pure HTTP + arithmetic — worker-thread safe. Cached per bbox until the
    redline changes (clear_ds_cache). Raises RuntimeError on failure."""
    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode

    cached = _HABMOS_CACHE.get(bbox)
    if cached is not None:
        return cached

    xmin, ymin, xmax, ymax = [float(v) for v in bbox.split(",")[:4]]
    prefixes = sorted({p for p, _g, _l in HABMOS_PRIORITY_MAP})
    likes = " OR ".join(f"HABITAT_CODE LIKE '{p}%'" for p in prefixes)
    cql = (f"BBOX(the_geom,{xmin},{ymin},{xmax},{ymax},'EPSG:27700') "
           f"AND ({likes})")

    raw, start, page = [], 0, 5000
    while True:
        params = urlencode({
            "service":      "WFS",
            "version":      "2.0.0",
            "request":      "GetFeature",
            "typeNames":    _HABMOS_TYPENAME,
            "outputFormat": "application/json",
            "srsName":      "EPSG:27700",
            "count":        str(page),
            "startIndex":   str(start),
            "cql_filter":   cql,
        })
        try:
            data = json.loads(urlopen(f"{_NS_WFS_HS}?{params}", timeout=60)
                              .read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"HabMoS query failed: {exc}")
        feats = data.get("features", []) or []
        raw.extend(feats)
        matched = data.get("numberMatched") or data.get("totalFeatures")
        if len(feats) < page or (isinstance(matched, int) and len(raw) >= matched):
            break
        start += len(feats)

    def _ring_area(ring):
        a = 0.0
        for i in range(len(ring) - 1):
            a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        return abs(a) / 2.0

    out = []
    for f in raw:
        props = f.get("properties", {}) or {}
        cls = habmos_classify(props.get("HABITAT_CODE"))
        if cls is None:
            continue
        group, label = cls
        geom = f.get("geometry") or {}
        gtype, coords = geom.get("type", ""), geom.get("coordinates", [])
        polys = [coords] if gtype == "Polygon" else (coords if gtype == "MultiPolygon" else [])
        polys = [p for p in polys if p and p[0]]
        if not polys:
            continue
        try:
            total_ha = float(props.get("HAB_AREA") or 0)
        except (TypeError, ValueError):
            total_ha = 0.0
        part_m2 = [max(_ring_area(p[0]) - sum(_ring_area(h) for h in p[1:]), 0.0)
                   for p in polys]
        sum_m2 = sum(part_m2) or 1.0
        code = _habmos_clean_code(props.get("HABITAT_CODE"))
        hab_name = str(props.get("HABITAT_NAME") or "").strip()
        src = str(props.get("HABMOS_SOURCE") or "").strip()
        desc = hab_name + (f" · {src}" if src else "")
        if str(props.get("MOSAIC") or "").upper() == "Y":
            desc += " (mosaic)"
        for p, m2 in zip(polys, part_m2):
            out.append({
                "attributes": {
                    "MainHabs": label,
                    "HabCodes": code,
                    "AreaHa":   round(total_ha * m2 / sum_m2, 4) if len(polys) > 1 else total_ha,
                    "FeatDesc": desc[:200],
                    "AddHabs":  "",
                    "_group":   group,
                },
                "geometry": {"rings": [[[pt[0], pt[1]] for pt in ring] for ring in p]},
            })
    _HABMOS_CACHE[bbox] = out
    return out


def query_priority_habitats_2km(easting, northing, redline_geom=None, group_nearby=False):
    """
    Query the Natural England Priority Habitats Inventory within 2 km.

    If redline_geom (QgsGeometry, BNG) is supplied, distance is the true minimum
    edge-to-edge distance using QgsGeometry.distance() (GEOS).

    If group_nearby is True, polygons of the same habitat type whose boundaries
    are within 5m of each other are merged into a single row (contiguous patch
    grouping). Each merged patch has a summed area and a centroid/distance based
    on the combined geometry.

    Returns a list of dicts:
        {
          'habitat':     str   — MainHabs value e.g. "Lowland meadows"
          'hab_code':    str   — HabCodes e.g. "LOWMEA"
          'description': str   — FeatDesc (may be empty)
          'add_habs':    str   — AddHabs (may be empty)
          'area_ha':     float
          'centroid_gr': str   — 6-figure BNG grid ref of feature centroid
          'distance_m':  int   — edge-to-edge distance in metres (0 = overlap)
          'direction':   str   — 8-point cardinal
        }

    Sorted by distance ascending, then habitat name.
    """
    import json
    import math
    from urllib.request import urlopen
    from urllib.parse import urlencode

    RADIUS = _search_radius("priority_habitats")

    # Build true search buffer from redline edge (or centroid circle).
    # Use the buffer's bounding box for the API request (more reliable with
    # esriGeometryEnvelope), then apply search_buffer.intersects() post-fetch
    # to clip results to the true circular buffer, not the box corners.
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bb   = search_buffer.boundingBox()
    bbox = f"{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()}"

    all_features = []
    offset = 0
    page   = 2000

    # Scotland: one HabMoS fetch (already priority-classified) replaces the
    # paginated Natural England query below.
    scot = _region.for_point(easting, northing) == "scotland"
    if scot:
        all_features = _habmos_features(bbox)

    while not scot:
        params = urlencode({
            "where":             "1=1",
            "geometry":          bbox,
            "geometryType":      "esriGeometryEnvelope",
            "inSR":              "27700",
            "spatialRel":        "esriSpatialRelIntersects",
            "outFields":         "MainHabs,HabCodes,FeatDesc,AddHabs,AreaHa",
            "returnGeometry":    "true",
            "outSR":             "27700",
            "resultOffset":      offset,
            "resultRecordCount": page,
            "f":                 "json",
        })
        try:
            resp = urlopen(f"{_PHI_BASE}?{params}", timeout=30)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"PHI query failed: {exc}")

        if "error" in data:
            raise RuntimeError(data["error"].get("message", "API error"))

        features = data.get("features", [])
        all_features.extend(features)

        if not data.get("exceededTransferLimit", False) or not features:
            break
        offset += len(features)

    # ── Pass 1: filter features and collect raw polygons per habitat type ────
    # Each entry: (feat_geom, area_ha, hab_code, description, add_habs)
    from collections import defaultdict
    raw_by_habitat = defaultdict(list)

    for feat in all_features:
        attrs = feat.get("attributes", {})
        geom  = feat.get("geometry",   {})

        habitat     = str(attrs.get("MainHabs",  "") or "").strip()
        hab_code    = str(attrs.get("HabCodes",  "") or "").strip()
        description = str(attrs.get("FeatDesc",  "") or "").strip()
        add_habs    = str(attrs.get("AddHabs",   "") or "").strip()
        habitat, hab_code = _phi_report_habitat(habitat, hab_code, add_habs)
        try:
            area_ha = float(attrs.get("AreaHa") or 0)
        except (TypeError, ValueError):
            area_ha = 0.0

        feat_geom = _esri_rings_to_qgsgeom(geom)

        # Pre-filter: must intersect the true 2km buffer
        if feat_geom.isEmpty():
            continue
        if not search_buffer.isEmpty() and not search_buffer.intersects(feat_geom):
            continue

        # Strict 2km edge-to-edge distance check
        dist, direction = _redline_distance_and_direction(
            redline_geom, feat_geom, *_esri_centroid(geom)
        )
        if dist > RADIUS:
            continue

        raw_by_habitat[habitat].append({
            "geom":        feat_geom,
            "area_ha":     area_ha,
            "hab_code":    hab_code,
            "description": description,
            "add_habs":    add_habs,
            "dist":        dist,
            "direction":   direction,
        })

    results = []

    if not group_nearby:
        # ── No grouping: one row per polygon ─────────────────────────────────
        for habitat, polys in raw_by_habitat.items():
            for p in polys:
                cx, cy = p["geom"].centroid().asPoint().x(), p["geom"].centroid().asPoint().y()
                try:
                    from .gridref_utils import osgb_en_to_gridref
                    centroid_gr = osgb_en_to_gridref(int(cx), int(cy), 6)
                except Exception:
                    centroid_gr = f"{int(cx)},{int(cy)}"
                results.append({
                    "habitat":     habitat,
                    "hab_code":    p["hab_code"],
                    "description": p["description"],
                    "add_habs":    p["add_habs"],
                    "area_ha":     round(p["area_ha"], 2),
                    "centroid_gr": centroid_gr,
                    "distance_m":  p["dist"],
                    "direction":   p["direction"],
                    "geom":        p["geom"],
                })

    else:
        # ── Pass 2: group polygons of the same habitat that are within 5m of
        # each other into contiguous patches using union-find on a 2.5m expanded
        # geometry. Two polygons are "neighbours" if their 2.5m buffers intersect
        # — meaning the gap between them is ≤ 5m. Each connected component
        # becomes one row with summed area and merged centroid/distance.
        SNAP = 2.5  # half of 5m tolerance

        for habitat, polys in raw_by_habitat.items():
            n = len(polys)
            if n == 1:
                # Single polygon — no grouping needed
                p = polys[0]
                cx, cy = p["geom"].centroid().asPoint().x(), p["geom"].centroid().asPoint().y()
                try:
                    from .gridref_utils import osgb_en_to_gridref
                    centroid_gr = osgb_en_to_gridref(int(cx), int(cy), 6)
                except Exception:
                    centroid_gr = f"{int(cx)},{int(cy)}"
                results.append({
                    "habitat":     habitat,
                    "hab_code":    p["hab_code"],
                    "description": p["description"],
                    "add_habs":    p["add_habs"],
                    "area_ha":     round(p["area_ha"], 2),
                    "centroid_gr": centroid_gr,
                    "distance_m":  p["dist"],
                    "direction":   p["direction"],
                    "geom":        p["geom"],
                })
                continue

            # Build 2.5m expanded geometries for adjacency testing
            expanded = [p["geom"].buffer(SNAP, segments=4) for p in polys]

            # Union-find
            parent = list(range(n))

            def _find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def _union(a, b):
                ra, rb = _find(a), _find(b)
                if ra != rb:
                    parent[rb] = ra

            for i in range(n):
                for j in range(i + 1, n):
                    if expanded[i].intersects(expanded[j]):
                        _union(i, j)

            # Collect members of each component
            from collections import defaultdict as _dd
            components = _dd(list)
            for i in range(n):
                components[_find(i)].append(i)

            # Build one result row per component
            for members in components.values():
                group_polys = [polys[i] for i in members]

                # Merge geometries for centroid and distance of the combined patch.
                # Buffer out by SNAP before unioning so gaps ≤ 5m are closed into
                # a single continuous shape, then buffer back in by the same amount
                # to restore approximately the original polygon edges.
                from qgis.core import QgsGeometry
                buffered_out = [p["geom"].buffer(SNAP, segments=8) for p in group_polys]
                union_out    = QgsGeometry.unaryUnion(buffered_out)
                merged       = union_out.buffer(-SNAP, segments=8) if not union_out.isEmpty() else QgsGeometry()
                if merged.isEmpty():
                    merged = QgsGeometry.unaryUnion([p["geom"] for p in group_polys])
                if merged.isEmpty():
                    merged = group_polys[0]["geom"]

                merged_centroid = merged.centroid().asPoint()
                cx, cy = merged_centroid.x(), merged_centroid.y()

                # Re-compute distance/direction for the merged patch
                patch_dist, patch_dir = _redline_distance_and_direction(
                    redline_geom, merged, cx, cy
                )

                try:
                    from .gridref_utils import osgb_en_to_gridref
                    centroid_gr = osgb_en_to_gridref(int(cx), int(cy), 6)
                except Exception:
                    centroid_gr = f"{int(cx)},{int(cy)}"

                # Sum areas; pick the most informative description
                total_area = sum(p["area_ha"] for p in group_polys)
                desc = next((p["description"] for p in group_polys if p["description"]), "")
                add  = next((p["add_habs"]    for p in group_polys if p["add_habs"]),    "")
                hab_code = group_polys[0]["hab_code"]

                results.append({
                    "habitat":     habitat,
                    "hab_code":    hab_code,
                    "description": desc,
                    "add_habs":    add,
                    "area_ha":     round(total_area, 2),
                    "centroid_gr": centroid_gr,
                    "distance_m":  patch_dist,
                    "direction":   patch_dir,
                    "geom":        merged,
                })

    results.sort(key=lambda r: (r["distance_m"], r["habitat"]))
    return results


def _esri_centroid(geom):
    """Centroid of an esriJSON polygon (rings list of [x,y] pairs)."""
    rings = geom.get("rings", [])
    all_x, all_y = [], []
    for ring in rings[:1]:   # outer ring only
        for pt in ring:
            all_x.append(pt[0])
            all_y.append(pt[1])
    if not all_x:
        return 0.0, 0.0
    return sum(all_x) / len(all_x), sum(all_y) / len(all_y)


# ---------------------------------------------------------------------------
# 2 km buffer layer
# ---------------------------------------------------------------------------

_BUFFER_LAYER_NAME = "Search Buffer"


def add_2km_buffer(easting, northing, redline_geom=None, radius=None):
    """
    Add the manual "Search Buffer" ring as a memory layer.

    This is a purely visual aid showing how far the user has searched. Its radius
    is INDEPENDENT of the desk study search radii and is set only by the user
    (right-click the Search buffer button). When `radius` is None the persisted
    buffer radius is used (config.get_buffer_radius, default 2000 m).

    If redline_geom (QgsGeometry, EPSG:27700) is supplied, the buffer is computed
    from the redline EDGE using QgsGeometry.buffer() — the correct distance from
    the boundary, not from the centroid.

    If redline_geom is None, falls back to a circle from (easting, northing).
    Replaces any existing buffer layer of the same name.
    Returns (True, layer) on success, (False, reason) on failure.
    """
    # Suppress the datum-transform dialog before any BNG layer is added
    _suppress_datum_dialog()
    import math
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsSingleSymbolRenderer, QgsLineSymbol,
    )

    # Manual visual ring — the user sets this radius (right-click the Search
    # buffer button); it does NOT auto-follow the desk study search radii. The
    # desk study maps frame themselves from each search's own configured radius,
    # independently of this layer (see map_exporter._fit_to_search_area).
    if radius is None:
        try:
            from .config import get_buffer_radius
            radius = get_buffer_radius()
        except Exception:
            radius = 2000
    RADIUS = radius

    # Remove previous buffer if present
    for old in QgsProject.instance().mapLayersByName(_BUFFER_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    if redline_geom and not redline_geom.isEmpty():
        # True edge buffer — RADIUS metres from the redline boundary
        geom = redline_geom.buffer(RADIUS, segments=72)
    else:
        # Fallback: simple circle from centroid
        N_SIDES = 72
        ring = []
        for i in range(N_SIDES + 1):
            angle = 2 * math.pi * i / N_SIDES
            ring.append(QgsPointXY(
                easting  + RADIUS * math.cos(angle),
                northing + RADIUS * math.sin(angle),
            ))
        geom = QgsGeometry.fromPolygonXY([ring])

    lyr = QgsVectorLayer("Polygon?crs=EPSG:27700", _BUFFER_LAYER_NAME, "memory")
    dp  = lyr.dataProvider()
    f   = QgsFeature(lyr.fields())
    f.setGeometry(geom)
    dp.addFeatures([f])
    lyr.updateExtents()

    # Style — solid red outline with pale yellow semi-transparent fill, matching MAGIC site check buffer
    from qgis.core import QgsFillSymbol
    fill_sym = QgsFillSymbol.createSimple({
        "color":         "255,255,180,40",   # pale yellow, ~16% opacity
        "outline_color": "#e8000d",           # solid red, matching MAGIC buffer line
        "outline_width": "1.0",
        "outline_style": "solid",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(fill_sym))
    lyr.setOpacity(1.0)

    # Place buffer just below the redline boundary (position 0) — above all
    # other layers.  Find the redline node and insert immediately after it;
    # fall back to position 1 if the redline isn't in the root.
    root = QgsProject.instance().layerTreeRoot()
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)

    redline_layers = []
    for _redline_name in REDLINE_NAME_ALIASES:
        redline_layers.extend(
            QgsProject.instance().mapLayersByName(_redline_name))
    insert_idx = 1  # default: second from top
    if redline_layers:
        redline_node = root.findLayer(redline_layers[0].id())
        if redline_node is not None and redline_node.parent() == root:
            insert_idx = root.children().index(redline_node) + 1

    root.insertLayer(insert_idx, lyr)

    return True, lyr


def remove_2km_buffer():
    """Remove the 2 km buffer layer if present."""
    for lyr in QgsProject.instance().mapLayersByName(_BUFFER_LAYER_NAME):
        QgsProject.instance().removeMapLayer(lyr.id())


# ---------------------------------------------------------------------------
# Constraint buffers — per-feature / whole-layer rings around designated sites
# and priority habitats
# ---------------------------------------------------------------------------
# Distinct from the manual "Search Buffer" ring above (which buffers the
# REDLINE). These buffer an individual designated-site / priority-habitat
# feature (or a whole such layer) to show a zone of influence / consultation
# distance.
#
# Data model: ONE buffer layer per source TYPE, e.g. "Buffer - SSSI". Within it
# features are categorized by distance so the legend shows each radius as its
# own key ("SSSI - 250 m"). Buffering a second geometry of the SAME type and
# SAME radius unions it into that distance's polygon (overlap merge); a
# different radius coexists as its own legend category. So SSSI merges with
# SSSI but stays separate from SAC, exactly as the two live in different layers.
#
# Geometry is EPSG:27700 (BNG) — a true metric projected CRS, so radius_m is
# real metres and a plain QgsGeometry.buffer() is correct (the 3857 canvas
# distortion rule does NOT apply here; same as _build_search_buffer above).

_BUFFER_GROUP_NAME = "Constraint Buffers"

# type_label -> {band_label(str): {"geom": QgsGeometry, "radius": int}}.
# The band_label is the legend key: for whole-layer / unnamed buffers it is
# auto ("SSSI — 250 m"); for a NAMED single-site buffer it is the user's name.
# Buffers sharing a band_label are unioned (overlap merge); different band =
# its own legend category. Session-only; cleared on redline reset.
_BUFFER_STATE: dict = {}


def _buffer_layer_name(type_label):
    return "Buffer - " + type_label


def _fmt_distance(metres):
    """'250 m', '2 km', '1.5 km' for a buffer legend label."""
    if metres < 1000:
        return f"{metres} m"
    if metres % 1000 == 0:
        return f"{metres // 1000} km"
    return f"{metres / 1000:.1f} km"


def _buffer_band_label(type_label, radius_m):
    return f"{type_label} — {_fmt_distance(radius_m)}"


def _constraint_type_and_colour(source_layer_name):
    """Map a loaded designated-site / priority-habitat layer NAME to the
    (type_label, colour_hex) used to name and colour its buffer.

    'Priority Habitat - Woodland' -> ('Woodland', <group colour>) so the buffer
    layer is 'Buffer - Woodland'. Designated-site layers are named by their
    label already ('SSSI'), so they pass straight through."""
    name = " ".join((source_layer_name or "").split())

    for entry in DESIGNATED_LAYERS + DESIGNATED_LAYERS_SCOTLAND:
        if entry[1] == name:
            return entry[1], entry[9]          # label, outline_hex

    if name == _AW_LAYER_NAME:
        return "Ancient Woodland", _AW_COLOUR

    prefix = "Priority Habitat - "
    if name.startswith(prefix):
        label = name[len(prefix):]
        for g in PHI_HABITAT_GROUPS:
            if g[1] == label:
                return label, g[3]             # group label, colour
        return label, _AW_COLOUR

    return name, "#666666"


def _make_buffer_symbol(colour):
    """Bold dashed outline in the source type's colour with a light coloured
    fill, so a buffer ring is clearly visible on the canvas and shows its colour
    in the legend swatch, while still reading as a zone (not the solid site)."""
    from qgis.core import QgsFillSymbol
    from qgis.PyQt.QtGui import QColor
    c = QColor(colour)
    c.setAlpha(55)                      # ~22% fill — colours the legend swatch
    return QgsFillSymbol.createSimple({
        "color":         c.name(QColor.NameFormat.HexArgb),
        "outline_color": colour,        # full-opacity coloured edge
        "outline_width": "1.0",
        "outline_style": "dash",
    })


def _get_or_create_buffer_group(root):
    grp = root.findGroup(_BUFFER_GROUP_NAME)
    if grp is None:
        # Top of the tree so the (mostly outline) rings are never hidden under
        # solid constraint fills.
        grp = root.insertGroup(0, _BUFFER_GROUP_NAME)
        grp.setExpanded(True)
    return grp


def _rebuild_buffer_layer(type_label, colour):
    """(Re)build the 'Buffer - <type>' memory layer from _BUFFER_STATE — one
    feature and one legend category per distance band."""
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature,
        QgsCategorizedSymbolRenderer, QgsRendererCategory,
    )

    acc        = _BUFFER_STATE.get(type_label, {})
    layer_name = _buffer_layer_name(type_label)

    existing = QgsProject.instance().mapLayersByName(layer_name)
    if existing:
        lyr = existing[0]
    else:
        lyr = QgsVectorLayer(
            "Polygon?crs=EPSG:27700&field=radius:integer&field=band:string(80)",
            layer_name, "memory")
        lyr.setCustomProperty("skipMemoryLayersCheck", 1)
        # Never persisted into the saved QGIS project (see session-only strip).
        lyr.setCustomProperty("biome_session_only", 1)
        QgsProject.instance().addMapLayer(lyr, False)
        root = QgsProject.instance().layerTreeRoot()
        grp  = _get_or_create_buffer_group(root)
        grp.addLayer(lyr).setItemVisibilityChecked(True)
        grp.setItemVisibilityChecked(True)

    dp  = lyr.dataProvider()
    old = [f.id() for f in lyr.getFeatures()]
    if old:
        dp.deleteFeatures(old)

    feats = []
    for band in sorted(acc):
        entry = acc[band]
        geom  = entry.get("geom")
        if geom is None or geom.isEmpty():
            continue
        f = QgsFeature(lyr.fields())
        f.setGeometry(geom)
        f.setAttributes([int(entry.get("radius", 0)), band])
        feats.append(f)
    dp.addFeatures(feats)
    lyr.updateExtents()

    # Categorize on the STRING band field (matches the proven designated-site
    # renderer pattern — integer category values can fail to match the field).
    cats = []
    for band in sorted(acc):
        cats.append(QgsRendererCategory(band, _make_buffer_symbol(colour), band))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("band", cats))
    lyr.setOpacity(1.0)
    lyr.triggerRepaint()
    try:
        lyr.emitStyleChanged()          # nudge the legend to redraw on rebuilds
    except Exception:
        pass
    return lyr


def add_constraint_buffer(source_layer_name, source_geom, radius_m,
                          colour=None, name=None):
    """Buffer a designated-site / priority-habitat geometry by `radius_m` metres
    and add it to the 'Constraint Buffers' group.

    `source_geom` is a QgsGeometry in EPSG:27700 (BNG). `name`, when given (used
    for a NAMED single-site buffer), becomes the legend band label; otherwise the
    band is auto ("<type> — <distance>"). Buffers sharing a band label are unioned
    (overlap merge); a different band coexists as its own legend category on the
    same 'Buffer - <type>' layer.

    Returns (True, layer) or (False, reason)."""
    _suppress_datum_dialog()
    if source_geom is None or source_geom.isEmpty():
        return False, "no_geometry"
    try:
        radius_m = int(round(float(radius_m)))
    except (TypeError, ValueError):
        return False, "bad_radius"
    if radius_m <= 0:
        return False, "bad_radius"

    buffered = source_geom.buffer(radius_m, segments=72)
    if buffered is None or buffered.isEmpty():
        return False, "buffer_failed"

    type_label, type_colour = _constraint_type_and_colour(source_layer_name)
    colour = colour or type_colour
    band   = (name.strip() if (name and name.strip())
              else _buffer_band_label(type_label, radius_m))

    acc  = _BUFFER_STATE.setdefault(type_label, {})
    prev = acc.get(band)
    if prev is not None and prev.get("geom") is not None and not prev["geom"].isEmpty():
        merged = prev["geom"].combine(buffered)      # GEOS union
        geom = merged if (merged is not None and not merged.isEmpty()) else buffered
    else:
        geom = buffered
    acc[band] = {"geom": geom, "radius": radius_m}

    lyr = _rebuild_buffer_layer(type_label, colour)
    return True, lyr


def add_layer_buffer(source_layer, radius_m, colour=None):
    """Whole-layer buffer: union every feature in `source_layer`, buffer the
    union once, and add it as a single ring for that type. `source_layer` is a
    QgsVectorLayer in EPSG:27700 (all designated-site / priority-habitat layers
    are). Returns (True, layer) or (False, reason)."""
    from qgis.core import QgsGeometry
    if source_layer is None:
        return False, "no_layer"
    geoms = [f.geometry() for f in source_layer.getFeatures()
             if f.geometry() is not None and not f.geometry().isEmpty()]
    if not geoms:
        return False, "no_features"
    union = QgsGeometry.unaryUnion(geoms)
    if union is None or union.isEmpty():
        union = geoms[0]
    return add_constraint_buffer(source_layer.name(), union, radius_m, colour)


def remove_constraint_buffer(type_label):
    """Remove one type's buffer layer and forget its accumulated geometry."""
    from qgis.core import QgsProject
    _BUFFER_STATE.pop(type_label, None)
    for lyr in QgsProject.instance().mapLayersByName(_buffer_layer_name(type_label)):
        QgsProject.instance().removeMapLayer(lyr.id())


def remove_all_constraint_buffers():
    """Remove every constraint-buffer layer and the group. Call on redline reset."""
    from qgis.core import QgsProject
    _BUFFER_STATE.clear()
    root = QgsProject.instance().layerTreeRoot()
    grp  = root.findGroup(_BUFFER_GROUP_NAME)
    if grp is not None:
        for child in list(grp.findLayers()):
            lyr = child.layer()
            if lyr is not None:
                QgsProject.instance().removeMapLayer(lyr.id())
        parent = grp.parent()
        if parent is not None:
            parent.removeChildNode(grp)


def active_constraint_buffers():
    """Return {type_label: sorted list of band labels} for buffers currently set
    — lets the UI show/refresh what's buffered and offer per-type removal."""
    return {t: sorted(bands) for t, bands in _BUFFER_STATE.items() if bands}


def constraint_buffer_geometry(type_label):
    """Union of every band for a type's buffer (its full covered area), or None
    if that type has no buffer. Used by the 'what's within?' analysis."""
    from qgis.core import QgsGeometry
    acc   = _BUFFER_STATE.get(type_label) or {}
    geoms = [e.get("geom") for e in acc.values()
             if e.get("geom") is not None and not e["geom"].isEmpty()]
    if not geoms:
        return None
    u = QgsGeometry.unaryUnion(geoms)
    return u if (u is not None and not u.isEmpty()) else geoms[0]


# ---------------------------------------------------------------------------
# EPSL — European Protected Species Licences (MAGIC / Landmark ArcGIS)
# ---------------------------------------------------------------------------

# Species code → full name lookup (confirmed from live MAGIC data, March 2026)
EPSL_SPECIES_CODES = {
    # Bats
    "ALC":    "Alcathoe's bat",
    "ALCA":   "Alcathoe's bat",
    "BARB":   "Barbastelle",
    "BECH":   "Bechstein's bat",
    "BLE":    "Brown long-eared bat",
    "BRAN":   "Brandt's bat",
    "C-PIP":  "Common pipistrelle",
    "DAUB":   "Daubenton's bat",
    "G-HORSE":"Greater horseshoe bat",
    "G-MOU":  "Greater mouse-eared bat",
    "GLE":    "Grey long-eared bat",
    "L-HORSE":"Lesser horseshoe bat",
    "LEIS":   "Leisler's bat",
    "N-PIP":  "Nathusius' pipistrelle",
    "NATT":   "Natterer's bat",
    "NOCT":   "Noctule",
    "S-PIP":  "Soprano pipistrelle",
    "SER":    "Serotine",
    "WHISK":  "Whiskered bat",
    # Non-bat (some entries already use full names in the data)
    "Dormouse":                   "Hazel dormouse",
    "Hazel or common dormouse":   "Hazel dormouse",
    "Great Crested Newt":         "Great crested newt",
    "Great crested newt":         "Great crested newt",
}


def expand_species_codes(species_str):
    """Expand semicolon/comma-separated species codes to full names.

    e.g. "C-PIP;S-PIP;BLE" → "Common pipistrelle; Soprano pipistrelle; Brown long-eared bat"
    Already-full names (GCN, Dormouse variants) pass through cleanly.
    """
    if not species_str:
        return ""
    parts = [p.strip() for p in species_str.replace(",", ";").split(";") if p.strip()]
    return "; ".join(EPSL_SPECIES_CODES.get(p, p) for p in parts)


# Severity-ordered impact verbs, used to collapse the six MAGIC layer-209
# Yes/No flags into the single "Impact" phrase of a protected-species
# licence table (e.g. "Resting place damaged & destroyed").
_IMPACT_VERB_RANK = {"affected": 1, "damaged": 2, "destroyed": 3}


def _impact_flag_set(value):
    """True if a MAGIC habitat-impact flag reads as an affirmative."""
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")


def epsl_impact_summary(row, blank="Not specified"):
    """Collapse an EPSL record's six habitat-impact flags into one phrase.

    Mirrors the hand-written "Impact" column of a previous-licences table,
    e.g. "Breeding site affected & damaged", "Resting place damaged &
    destroyed", "Resting place & hibernation site destroyed".

    Rule: within a feature, ticked outcomes are joined in severity order
    (affected → damaged → destroyed) with " & ", the feature noun stated
    once. A hibernation-site "affected" flag that co-occurs with a damaged
    or destroyed resting place (or, failing that, breeding place) is merged
    under that shared, more-severe verb — so resting destroyed + hibernation
    affected → "Resting place & hibernation site destroyed". Features that
    share an identical outcome are likewise merged under one verb. Returns
    `blank` when no flag is set.

    `row` is a dict as returned by query_epsl_2km (keys affects_br,
    damage_bre, destroy_br, damage_res, destroy_re, affects_hi).
    """
    def _verbs(*pairs):
        vs = [verb for flag, verb in pairs if _impact_flag_set(row.get(flag))]
        return sorted(vs, key=lambda v: _IMPACT_VERB_RANK.get(v, 0))

    bre = _verbs(("affects_br", "affected"),
                 ("damage_bre", "damaged"),
                 ("destroy_br", "destroyed"))
    res = _verbs(("damage_res", "damaged"),
                 ("destroy_re", "destroyed"))
    hib = _impact_flag_set(row.get("affects_hi"))

    # Each group is [verbs tuple, [feature nouns]], built in feature order.
    groups = []
    if bre:
        groups.append([tuple(bre), ["Breeding site"]])
    if res:
        groups.append([tuple(res), ["Resting place"]])

    # Hibernation promotion: fold the softer "affected" into the resting
    # place (preferred) or breeding place, adopting its more-severe verb.
    if hib:
        host = next((g for g in groups if g[1] == ["Resting place"]), None) \
            or next((g for g in groups if g[1] == ["Breeding site"]), None)
        if host is not None:
            host[1].append("Hibernation site")
        else:
            groups.append([("affected",), ["Hibernation site"]])

    if not groups:
        return blank

    # Merge any remaining groups that share an identical outcome.
    merged = []
    for verbs, nouns in groups:
        existing = next((m for m in merged if m[0] == verbs), None)
        if existing is not None:
            existing[1].extend(nouns)
        else:
            merged.append([verbs, list(nouns)])

    def _render(verbs, nouns):
        noun_txt = nouns[0] + "".join(
            " & " + n[0].lower() + n[1:] for n in nouns[1:])
        return f"{noun_txt} {' & '.join(verbs)}"

    return "; ".join(_render(v, n) for v, n in merged)


# Landmark migrated this service from gisdata.landmarkcloud.co.uk/MAGIC/.../209
# (now stopped) to gis.landmarkcloud.co.uk/Magic/.../285. Same dataset, same
# fields — the EPSL points are layer 285 "Granted European Protected Species
# Applications (England)", confirmed against MAGIC's own config.json (June 2026).
_EPSL_QUERY_URL = (
    "https://gis.landmarkcloud.co.uk/arcgis/rest/services"
    "/Magic/HabitatsAndSpecies/MapServer/285/query"
)
_EPSL_LAYER_NAME = "EPSL Locations"


def query_epsl_2km(easting, northing, redline_geom=None):
    """
    Query granted European Protected Species Licence (EPSL) locations
    within 2 km of (easting, northing).

    Source: MAGIC / Landmark ArcGIS — HabitatsAndSpecies MapServer layer 285.
    Public access, no auth required. Returns BNG point geometry.

    Returns list of dicts:
        {
          'case_ref':    str   — licence reference e.g. "EPSM2010-2288"
          'species_gr':  str   — species group e.g. "Bat"
          'species':     str   — species detail e.g. "C-PIP;S-PIP"
          'county':      str   — site county
          'start_date':  str   — "DD/MM/YYYY" or "" if null
          'end_date':    str   — "DD/MM/YYYY" or "" if null
          'easting':     int
          'northing':    int
          'distance_m':  int   — edge-to-edge from redline boundary (0 = overlap)
          'direction':   str   — 8-point cardinal
          'grid_ref':    str   — 6-figure BNG of licence point
          'affects_br':  str   — Does licence impact on a breeding site (Yes/No/"")
          'damage_bre':  str   — Does licence allow damage of breeding site (Yes/No/"")
          'damage_res':  str   — Does licence allow damage of a resting place (Yes/No/"")
          'destroy_br':  str   — Does licence allow destruction of breeding site (Yes/No/"")
          'destroy_re':  str   — Does licence allow destruction of a resting place (Yes/No/"")
          'affects_hi':  str   — Does licence impact on a hibernation site (Yes/No/"")
        }
    Sorted by distance ascending.
    """
    import json
    import math
    from datetime import datetime, timezone
    from urllib.request import urlopen
    from urllib.parse import urlencode

    RADIUS = _search_radius("epsl")

    # Scotland: NatureScot publishes no licence locations (see region.py) —
    # the desk study explains this before calling; return nothing here so no
    # caller ever plots English MAGIC points around a Scottish site.
    if _region.for_point(easting, northing) == "scotland":
        return []

    # Build true search buffer from redline edge (or centroid circle).
    # Use the buffer's bounding box for the API request (Landmark ArcGIS endpoint
    # is more reliable with esriGeometryEnvelope), then apply search_buffer.intersects()
    # post-fetch to clip results to the true circular buffer, not the box corners.
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bb   = search_buffer.boundingBox()
    bbox = f"{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()}"

    all_features = []
    offset = 0
    page   = 1000

    while True:
        params = urlencode({
            "where":             "1=1",
            "geometry":          bbox,
            "geometryType":      "esriGeometryEnvelope",
            "inSR":              "27700",
            "spatialRel":        "esriSpatialRelIntersects",
            "outFields":         "CASE_REF,SPECIES_GR,SPECIES_DE,SITE_COUNT,LICENCE_ST,LICENCE_EN,AFFECTS_BR,DAMAGE_BRE,DAMAGE_RES,DESTROY_BR,DESTROY_RE,AFFECTS_HI",
            "returnGeometry":    "true",
            "outSR":             "27700",
            "resultOffset":      offset,
            "resultRecordCount": page,
            "f":                 "json",
        })
        try:
            resp = urlopen(f"{_EPSL_QUERY_URL}?{params}", timeout=20)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"EPSL query failed: {exc}")

        if "error" in data:
            raise RuntimeError(data["error"].get("message", "API error"))

        features = data.get("features", [])
        all_features.extend(features)

        if not data.get("exceededTransferLimit", False) or not features:
            break
        offset += len(features)

    results = []

    for feat in all_features:
        attrs = feat.get("attributes", {})
        geom  = feat.get("geometry",   {})

        fx = geom.get("x", 0) or 0
        fy = geom.get("y", 0) or 0

        def _ts_to_date(ts):
            if ts is None:
                return ""
            try:
                return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%d/%m/%Y")
            except Exception:
                return ""

        try:
            from .gridref_utils import osgb_en_to_gridref
            gr = osgb_en_to_gridref(int(fx), int(fy), 6)
        except Exception:
            gr = f"{int(fx)},{int(fy)}"

        # EPSL is a point — distance from redline boundary to the point
        feat_geom = _esri_point_to_qgsgeom(geom)

        # Pre-filter: skip points outside the true 2km buffer polygon.
        if not feat_geom.isEmpty() and not search_buffer.isEmpty():
            if not search_buffer.intersects(feat_geom):
                continue

        dist, direction = _redline_distance_and_direction(redline_geom, feat_geom, fx, fy)

        # Enforce strict 2km threshold on the actual edge-to-edge distance
        if dist > RADIUS:
            continue

        results.append({
            "case_ref":   str(attrs.get("CASE_REF",   "") or "").strip(),
            "species_gr": str(attrs.get("SPECIES_GR", "") or "").strip(),
            "species":    expand_species_codes(str(attrs.get("SPECIES_DE", "") or "").strip()),
            "county":     str(attrs.get("SITE_COUNT", "") or "").strip(),
            "start_date": _ts_to_date(attrs.get("LICENCE_ST")),
            "end_date":   _ts_to_date(attrs.get("LICENCE_EN")),
            "easting":    int(fx),
            "northing":   int(fy),
            "distance_m": dist,
            "direction":  direction,
            "grid_ref":   gr,
            # Habitat impact fields — from MAGIC layer 285
            "affects_br": str(attrs.get("AFFECTS_BR", "") or "").strip(),
            "damage_bre": str(attrs.get("DAMAGE_BRE", "") or "").strip(),
            "damage_res": str(attrs.get("DAMAGE_RES", "") or "").strip(),
            "destroy_br": str(attrs.get("DESTROY_BR", "") or "").strip(),
            "destroy_re": str(attrs.get("DESTROY_RE", "") or "").strip(),
            "affects_hi": str(attrs.get("AFFECTS_HI", "") or "").strip(),
        })

    results.sort(key=lambda r: r["distance_m"])
    return results


def load_epsl_layer(easting, northing, redline_geom=None):
    """
    Fetch EPSL points within 2 km and add them as a styled memory layer.
    Returns (ok, layer_or_reason).
    """
    import math
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsSingleSymbolRenderer, QgsMarkerSymbol,
        QgsField,
    )
    from qgis.PyQt.QtCore import QVariant

    # Remove previous
    for old in QgsProject.instance().mapLayersByName(_EPSL_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    if _region.for_point(easting, northing) == "scotland":
        return False, "scotland"

    try:
        records = query_epsl_2km(easting, northing, redline_geom)
    except Exception as exc:
        return False, str(exc)

    # Keep an empty, styled EPSL layer when the search finds no records.  The
    # print-map legend uses its renderer as the key, making an empty map
    # unambiguous: users can see which symbol would represent a licence
    # location and therefore understand that none were found.
    no_results = not records

    lyr = QgsVectorLayer(
        "Point?crs=EPSG:27700"
        "&field=case_ref:string(30)"
        "&field=species_gr:string(50)"
        "&field=species:string(100)"
        "&field=county:string(50)"
        "&field=start_date:string(12)"
        "&field=end_date:string(12)"
        "&field=distance_m:integer"
        "&field=direction:string(3)"
        "&field=grid_ref:string(10)"
        "&field=affects_br:string(10)"
        "&field=damage_bre:string(10)"
        "&field=damage_res:string(10)"
        "&field=destroy_br:string(10)"
        "&field=destroy_re:string(10)"
        "&field=affects_hi:string(10)"
        "&field=icon_key:string(50)"
        "&field=icon_label:string(100)",
        _EPSL_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    feats = []
    for r in records:
        f = QgsFeature(lyr.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["easting"], r["northing"])))
        icon_key, icon_label = _epsl_icon_identity(r["species_gr"], r["species"])
        f.setAttributes([
            r["case_ref"], r["species_gr"], r["species"],
            r["county"], r["start_date"], r["end_date"],
            r["distance_m"], r["direction"], r["grid_ref"],
            r.get("affects_br", ""), r.get("damage_bre", ""),
            r.get("damage_res", ""), r.get("destroy_br", ""),
            r.get("destroy_re", ""), r.get("affects_hi", ""),
            icon_key, icon_label,
        ])
        feats.append(f)
    dp.addFeatures(feats)
    lyr.updateExtents()

    # Species-detail icons are the default EPSL presentation.  Alternative
    # standard-star and recent/historic styles remain available from the UI.
    apply_epsl_emoji_renderer(lyr)

    # Labels — species with semicolons replaced by newlines for compact display
    from qgis.core import (
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor
    pal = QgsPalLayerSettings()
    pal.enabled      = True
    pal.isExpression = True
    pal.fieldName    = "replace(\"species\", '; ', '\n')"
    # Place the label around the marker, measured from the icon's bounding box,
    # so the text never sits on top of the species icon.
    pal.placement    = QgsPalLayerSettings.Placement.OrderedPositionsAroundPoint
    pal.offsetType   = QgsPalLayerSettings.OffsetType.FromSymbolBounds
    pal.dist         = 2.0
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor("#8A4B12"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    # Add above base maps
    root = QgsProject.instance().layerTreeRoot()
    bm   = root.findGroup("Base Maps")
    idx  = root.children().index(bm) if bm is not None else 0
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)
    root.insertLayer(idx, lyr)

    if no_results:
        lyr.setCustomProperty("biome_empty_epsl_search", 1)
        return False, "no_results"
    return True, lyr


# Emoji per species group — shown when emoji mode is on
EPSL_SPECIES_EMOJI = {
    "Bat":                    "🦇",
    "Great Crested Newt":     "🦎",
    "Dormouse":               "🐭",
    "Otter":                  "🦦",
    "Water Vole":             "🐀",
    "White-clawed Crayfish":  "🦀",
    "Sand Lizard":            "🦎",
    "Natterjack Toad":        "🐸",
    "Marsh Fritillary":       "🦋",
    "Plant":                  "🌿",
}
_EPSL_EMOJI_FALLBACK = "⭐"

# Species-detail identities used by the SVG renderer.  MAGIC's broad groups
# (for example "Other Mammal") are not specific enough for map symbols, so the
# renderer derives these keys from SPECIES_DE and uses the group only as a
# fallback.  A single licence may name several bats; those deliberately retain
# the generic bat symbol rather than implying one species.
EPSL_ICON_SPECS = {
    "bat":                    ("bat.svg", "Bat"),
    "great_crested_newt":     ("great_crested_newt.svg", "Great crested newt"),
    "natterjack_toad":        ("natterjack_toad.svg", "Natterjack toad"),
    "hazel_dormouse":         ("dormouse.svg", "Hazel dormouse"),
    "otter":                  ("otter.svg", "Otter"),
    "sand_lizard":            ("sand_lizard.svg", "Sand lizard"),
    "smooth_snake":           ("smooth_snake.svg", "Smooth snake"),
    "mixed_reptiles":         ("mixed_reptiles.svg", "Sand lizard and smooth snake"),
    "harbour_porpoise":       ("harbour_porpoise.svg", "Harbour porpoise"),
    "large_blue_butterfly":   ("large_blue_butterfly.svg", "Large blue butterfly"),
    "floating_water_plantain":("plant.svg", "Floating-leaved water-plantain"),
    "plant":                  ("plant.svg", "Plant"),
    # Retained for compatibility if these groups reappear in the service.
    "water_vole":             ("water_vole.svg", "Water vole"),
    "white_clawed_crayfish":  ("white_clawed_crayfish.svg", "White-clawed crayfish"),
    "marsh_fritillary":       ("marsh_fritillary.svg", "Marsh fritillary"),
}
_EPSL_SVG_FALLBACK = "_fallback.svg"


def _epsl_normalise_species(value):
    """Return a stable comparison form for inconsistent MAGIC text values."""
    return " ".join(str(value or "").replace("–", "-").strip().lower().split())


def _epsl_icon_identity(species_group, species_detail):
    """Return ``(renderer key, legend label)`` for one EPSL record.

    Detail is intentionally considered before the broad group because MAGIC
    stores dormouse and otter records under ``Other Mammal`` and GCN / smooth
    snake records under broad amphibian and reptile groups.
    """
    detail = _epsl_normalise_species(species_detail)
    group = _epsl_normalise_species(species_group)
    parts = {_epsl_normalise_species(p) for p in detail.split(";") if p.strip()}

    detail_keys = {
        "great crested newt": "great_crested_newt",
        "natterjack toad": "natterjack_toad",
        "dormouse": "hazel_dormouse",
        "hazel dormouse": "hazel_dormouse",
        "hazel or common dormouse": "hazel_dormouse",
        "otter": "otter",
        "common otter": "otter",
        "sand lizard": "sand_lizard",
        "smooth snake": "smooth_snake",
        "harbour porpoise": "harbour_porpoise",
        "large blue butterfly": "large_blue_butterfly",
        "floating-leaved water plantain": "floating_water_plantain",
        "floating-leaved water-plantain": "floating_water_plantain",
        "water vole": "water_vole",
        "white-clawed crayfish": "white_clawed_crayfish",
        "marsh fritillary": "marsh_fritillary",
    }

    if parts == {"sand lizard", "smooth snake"}:
        return "mixed_reptiles", EPSL_ICON_SPECS["mixed_reptiles"][1]
    if len(parts) == 1:
        key = detail_keys.get(next(iter(parts)))
        if key:
            return key, EPSL_ICON_SPECS[key][1]

    if "bat" in group:
        return "bat", EPSL_ICON_SPECS["bat"][1]

    group_keys = {
        "plant": "plant",
        "water vole": "water_vole",
        "white-clawed crayfish": "white_clawed_crayfish",
        "marsh fritillary": "marsh_fritillary",
    }
    key = group_keys.get(group)
    if key:
        return key, EPSL_ICON_SPECS[key][1]

    label = str(species_group or "Other species").strip() or "Other species"
    return "other:" + group, label


def _epsl_svg_path(fname):
    """Absolute path to a Symbols/epsl/<fname> icon, or None if missing."""
    import os
    base = os.path.dirname(__file__)
    p = os.path.join(base, "Symbols", "epsl", fname)
    return p if os.path.exists(p) else None


def _epsl_svg_marker_symbol(fname, size_mm=6.5):
    """Build a QgsMarkerSymbol backed by an SVG marker layer for the given
    icon filename. Falls back to a neutral grey star marker if the SVG is
    missing or the SVG marker layer can't be created."""
    from qgis.core import QgsMarkerSymbol
    path = _epsl_svg_path(fname) or _epsl_svg_path(_EPSL_SVG_FALLBACK)
    sym = QgsMarkerSymbol.createSimple({})
    try:
        from qgis.core import QgsSvgMarkerSymbolLayer
        if path:
            sl = QgsSvgMarkerSymbolLayer(path)
            sl.setSize(size_mm)
            sym.changeSymbolLayer(0, sl)
            return sym
    except Exception:
        pass
    return QgsMarkerSymbol.createSimple({
        "name": "star", "color": "#607078",
        "outline_color": "#3F4D53", "outline_width": "0.4", "size": "5.5",
    })


# EPSL bat test — identical rule to the report / _epsl_feature_matches
# (species group text contains "bat", case-insensitive). Used to compose the
# Bats filter with whichever Style renderer is active.
_EPSL_BAT_EXPR = "lower(\"species_gr\") LIKE '%bat%'"


def _epsl_invisible_marker():
    """A zero-size, fully transparent marker — used to hide filtered-out EPSL
    points while keeping them in the layer."""
    from qgis.core import QgsMarkerSymbol
    return QgsMarkerSymbol.createSimple({
        "name": "circle", "size": "0",
        "color": "#00000000", "outline_width": "0",
    })


def _epsl_hidden_else_rule():
    """An ELSE rule that draws nothing — appended to the rule-based EPSL
    renderers so non-matching (non-bat) points disappear under the filter."""
    from qgis.core import QgsRuleBasedRenderer
    rule = QgsRuleBasedRenderer.Rule(_epsl_invisible_marker())
    rule.setIsElse(True)
    return rule


def apply_epsl_star_renderer(lyr, bats_only=False):
    """Apply the default orange star marker renderer to the EPSL layer.

    When `bats_only` is set the star is wrapped in a rule-based renderer that
    hides non-bat points, so the Star style composes with the Bats filter.
    """
    from qgis.core import QgsMarkerSymbol
    star = QgsMarkerSymbol.createSimple({
        "name":          "star",
        "color":         "#9A6700",
        "outline_color": "#8A4B12",
        "outline_width": "0.4",
        "size":          "6",
    })
    if not bats_only:
        from qgis.core import QgsSingleSymbolRenderer
        lyr.setRenderer(QgsSingleSymbolRenderer(star))
    else:
        from qgis.core import QgsRuleBasedRenderer
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(QgsRuleBasedRenderer.Rule(
            star.clone(), label="Bats", filterExp=_EPSL_BAT_EXPR))
        root.appendChild(_epsl_hidden_else_rule())
        lyr.setRenderer(QgsRuleBasedRenderer(root))
    lyr.triggerRepaint()


def apply_epsl_recent_historic_renderer(lyr, bats_only=False):
    """Render EPSL points using the latest ten inclusive calendar years.

    Uses QgsRuleBasedRenderer so expressions are evaluated per-feature.
    Recent  = current year and preceding nine years, or blank → orange star
    Historic = before that ten-year window                    → grey star
    When `bats_only` is set both rules are ANDed with the bat test and a
    hidden ELSE rule drops non-bat points, so the Recent/Historic style
    composes with the Bats filter.
    """
    from qgis.core import (
        QgsRuleBasedRenderer, QgsMarkerSymbol,
    )
    from datetime import datetime, timezone

    current_year = datetime.now(tz=timezone.utc).year
    cutoff_year = current_year - 9
    cutoff_str  = f"01/01/{cutoff_year}"  # DD/MM/YYYY as stored in layer

    recent_expr = (
        f"\"start_date\" = '' OR "
        f"to_date(\"start_date\", 'dd/MM/yyyy') >= "
        f"to_date('{cutoff_str}', 'dd/MM/yyyy')"
    )
    historic_expr = f"NOT ({recent_expr})"
    if bats_only:
        recent_expr   = f"({recent_expr}) AND {_EPSL_BAT_EXPR}"
        historic_expr = f"({historic_expr}) AND {_EPSL_BAT_EXPR}"

    recent_sym = QgsMarkerSymbol.createSimple({
        "name":          "star",
        "color":         "#9A6700",
        "outline_color": "#8A4B12",
        "outline_width": "0.4",
        "size":          "6",
    })
    historic_sym = QgsMarkerSymbol.createSimple({
        "name":          "star",
        "color":         "#78909c",
        "outline_color": "#455a64",
        "outline_width": "0.4",
        "size":          "5",
    })

    root_rule = QgsRuleBasedRenderer.Rule(None)

    recent_rule = QgsRuleBasedRenderer.Rule(
        recent_sym.clone(),
        label=f"Recent licences · {cutoff_year}–{current_year}",
        description=(f"Licences started during the latest ten calendar years "
                     f"({cutoff_year}–{current_year})"),
        filterExp=recent_expr,
    )
    historic_rule = QgsRuleBasedRenderer.Rule(
        historic_sym.clone(),
        label=f"Historic licences · before {cutoff_year}",
        description=f"Licences started before {cutoff_year}",
        filterExp=historic_expr,
    )

    root_rule.appendChild(recent_rule)
    root_rule.appendChild(historic_rule)
    if bats_only:
        root_rule.appendChild(_epsl_hidden_else_rule())

    renderer = QgsRuleBasedRenderer(root_rule)
    lyr.setRenderer(renderer)
    lyr.triggerRepaint()


def apply_epsl_emoji_renderer(lyr, bats_only=False):
    """Apply species-detail SVG icons to the EPSL layer.

    Species details drive the icon and legend label; the broad MAGIC group is
    used only when no recognised detail is available.  Only identities present
    in the current result set become renderer categories.  (Name retained for
    backward compatibility with existing callers.)
    When `bats_only` is set, non-bat species categories are drawn with an
    invisible marker so the Stylised-icons style composes with the Bats filter.
    """
    from qgis.core import (
        QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsField,
    )
    from qgis.PyQt.QtCore import QVariant

    # Upgrade EPSL memory layers created before icon_key/icon_label were added,
    # so changing display mode after a plugin reload does not require a re-fetch.
    provider = lyr.dataProvider()
    missing = []
    if lyr.fields().indexFromName("icon_key") < 0:
        missing.append(QgsField("icon_key", QVariant.String, len=50))
    if lyr.fields().indexFromName("icon_label") < 0:
        missing.append(QgsField("icon_label", QVariant.String, len=100))
    if missing:
        provider.addAttributes(missing)
        lyr.updateFields()

    key_idx = lyr.fields().indexFromName("icon_key")
    label_idx = lyr.fields().indexFromName("icon_label")
    updates = {}
    present = {}
    for f in lyr.getFeatures():
        key, label = _epsl_icon_identity(f["species_gr"], f["species"])
        present[key] = label
        if f[key_idx] != key or f[label_idx] != label:
            updates[f.id()] = {key_idx: key, label_idx: label}
    if updates:
        provider.changeAttributeValues(updates)

    categories = []
    for key, label in sorted(present.items(), key=lambda item: item[1].lower()):
        if bats_only and key != "bat":
            # Do not add filtered species as invisible categories: QGIS still
            # prints their labels in the layout legend even though their map
            # symbols are transparent.  An absent category also leaves those
            # unmatched features unrendered.
            continue
        fname = EPSL_ICON_SPECS.get(key, (_EPSL_SVG_FALLBACK, label))[0]
        sym = _epsl_svg_marker_symbol(fname)
        categories.append(QgsRendererCategory(key, sym, label))

    # Keep an informative key when an empty search is exported.
    if not categories and not bats_only:
        sym = _epsl_svg_marker_symbol(_EPSL_SVG_FALLBACK)
        categories.append(QgsRendererCategory("", sym, "EPSL location"))

    renderer = QgsCategorizedSymbolRenderer("icon_key", categories)
    lyr.setRenderer(renderer)
    lyr.triggerRepaint()


def _emoji_marker_symbol(emoji: str):
    """Create a QgsMarkerSymbol that renders a single emoji character."""
    from qgis.core import QgsMarkerSymbol
    from qgis.PyQt.QtGui import QColor

    # Font marker: use the system emoji font at a readable size
    props = {
        "chr":              emoji,
        "font":             "Segoe UI Emoji,Apple Color Emoji,Noto Color Emoji",
        "size":             "8",
        "size_unit":        "Point",
        "color":            "0,0,0,255",
        "outline_color":    "255,255,255,0",
        "outline_width":    "0",
        "angle":            "0",
        "offset":           "0,0",
    }
    sym = QgsMarkerSymbol.createSimple({})  # start blank
    # Replace the default simple marker with a font marker layer
    try:
        from qgis.core import QgsFontMarkerSymbolLayer
        fl = QgsFontMarkerSymbolLayer.create(props)
        if fl:
            sym.deleteSymbolLayer(0)
            sym.appendSymbolLayer(fl)
    except Exception:
        # Fallback: plain circle if font marker unavailable
        sym = QgsMarkerSymbol.createSimple({
            "name": "circle", "color": "#9A6700",
            "outline_color": "#8A4B12", "size": "4",
        })
    return sym


def remove_epsl_layer():
    """Hide the EPSL layer (keep in project for instant re-display)."""
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    for name in (_EPSL_LAYER_NAME, "EPSL Distance Lines"):
        for lyr in QgsProject.instance().mapLayersByName(name):
            node = root.findLayer(lyr.id())
            if node is not None:
                node.setItemVisibilityChecked(False)


def set_epsl_label_mode(mode):
    """Update the EPSL point layer label field live without reloading.

    mode: "licence"  → case_ref only
          "species"  → species (expanded full names)
          "both"     → "case_ref: species" via expression
    """
    from qgis.core import (
        QgsProject, QgsPalLayerSettings,
        QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor
    lyrs = QgsProject.instance().mapLayersByName(_EPSL_LAYER_NAME)
    if not lyrs:
        return
    lyr = lyrs[0]

    pal = QgsPalLayerSettings()
    pal.enabled = True

    if mode == "off":
        lyr.setLabelsEnabled(False)
        lyr.emitStyleChanged()
        lyr.triggerRepaint()
        try:
            from qgis.utils import iface as _iface
            if _iface:
                _iface.mapCanvas().refresh()
        except Exception:
            pass
        return

    if mode == "licence":
        pal.isExpression = False
        pal.fieldName    = "case_ref"
    elif mode == "species":
        pal.isExpression = True
        pal.fieldName    = "replace(\"species\", '; ', '\n')"
    else:  # "both"
        pal.isExpression = True
        pal.fieldName    = "concat(\"case_ref\", '\n', replace(\"species\", '; ', '\n'))"

    # Place the label around the marker, measured from the icon's bounding box,
    # so the text never sits on top of the species icon.
    pal.placement  = QgsPalLayerSettings.Placement.OrderedPositionsAroundPoint
    pal.offsetType = QgsPalLayerSettings.OffsetType.FromSymbolBounds
    pal.dist       = 2.0

    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor("#8A4B12"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)

    # Briefly disable labels to bust the QGIS label cache, then re-apply
    lyr.setLabelsEnabled(False)
    lyr.triggerRepaint()

    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)
    lyr.emitStyleChanged()
    lyr.triggerRepaint()
    try:
        from qgis.utils import iface as _iface
        if _iface:
            _iface.mapCanvas().refresh()
    except Exception:
        pass


_EPSL_LINES_LAYER_NAME = "EPSL Distance Lines"


def _nest_desk_study_layer(primary_layer, child_layer, group_name,
                           child_index=1):
    """Place a desk-study source and its helper layer in one collapsed group.

    QGIS layers cannot themselves own child tree nodes, so a same-named group
    provides the familiar dropdown header used elsewhere in Biome. Existing
    layer nodes are cloned into the group, preserving visibility and styling.
    """
    from qgis.core import QgsProject
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    primary_node = root.findLayer(primary_layer.id()) if primary_layer else None
    group = root.findGroup(group_name)

    if group is None:
        parent = primary_node.parent() if primary_node is not None else root
        if parent is None:
            parent = root
        if primary_node is not None:
            idx = parent.children().index(primary_node)
        else:
            bm = root.findGroup("Base Maps")
            idx = root.children().index(bm) if parent is root and bm else 0
        group = parent.insertGroup(idx, group_name)

    if primary_node is not None and primary_node.parent() is not group:
        old_parent = primary_node.parent()
        group.insertChildNode(0, primary_node.clone())
        old_parent.removeChildNode(primary_node)

    child_node = root.findLayer(child_layer.id())
    insert_at = min(child_index, len(group.children()))
    if child_node is None:
        project.addMapLayer(child_layer, False)
        group.insertLayer(insert_at, child_layer)
    elif child_node.parent() is not group:
        old_parent = child_node.parent()
        group.insertChildNode(insert_at, child_node.clone())
        old_parent.removeChildNode(child_node)
    group.setExpanded(False)
    return group


def organise_desk_study_distance_layers():
    """Heal loose distance-line rows from projects saved by older builds."""
    from qgis.core import QgsProject
    project = QgsProject.instance()
    pairs = (
        (_EPSL_LAYER_NAME, _EPSL_LINES_LAYER_NAME),
        (_TPO_LAYER_NAME, _TPO_LINES_LAYER_NAME),
        (_LWS_LAYER_NAME, "LWS Distance Lines"),
    )
    for primary_name, line_name in pairs:
        primaries = project.mapLayersByName(primary_name)
        lines = project.mapLayersByName(line_name)
        if not primaries or not lines:
            continue
        group = _nest_desk_study_layer(
            primaries[0], lines[0], primary_name)
        if primary_name == _TPO_LAYER_NAME:
            for zone in project.mapLayersByName(_TPO_ZONE_LAYER_NAME):
                node = project.layerTreeRoot().findLayer(zone.id())
                if node is not None and node.parent() is not group:
                    old_parent = node.parent()
                    group.insertChildNode(2, node.clone())
                    old_parent.removeChildNode(node)


def load_epsl_distance_lines(redline_geom):
    """Build a line layer connecting the nearest point on the redline boundary
    to each EPSL point, labelled with the distance in metres.

    redline_geom: QgsGeometry of the redline boundary in EPSG:27700 (BNG).

    The layer is inserted above the EPSL points layer so lines render beneath
    the markers.  Returns (ok, layer_or_reason).
    """
    import math
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsLineSymbol,
        QgsSingleSymbolRenderer, QgsField,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtCore import QVariant
    from qgis.PyQt.QtGui import QColor

    # Remove any previous instance
    for old in QgsProject.instance().mapLayersByName(_EPSL_LINES_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    # Retrieve EPSL points from the existing layer
    epsl_lyrs = QgsProject.instance().mapLayersByName(_EPSL_LAYER_NAME)
    if not epsl_lyrs:
        return False, "no_epsl_layer"

    epsl_lyr = epsl_lyrs[0]
    epsl_feats = list(epsl_lyr.getFeatures())
    if not epsl_feats:
        return False, "no_features"

    if redline_geom is None or redline_geom.isEmpty():
        return False, "no_redline"

    # Build line features
    lyr = QgsVectorLayer(
        "LineString?crs=EPSG:27700"
        "&field=case_ref:string(30)"
        "&field=distance_m:integer"
        "&field=distance_label:string(20)",
        _EPSL_LINES_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    line_feats = []

    for feat in epsl_feats:
        epsl_geom = feat.geometry()
        if epsl_geom is None or epsl_geom.isEmpty():
            continue

        epsl_pt = epsl_geom.asPoint()

        # Find the nearest point on the redline boundary to this EPSL point
        nearest = redline_geom.nearestPoint(epsl_geom)
        if nearest is None or nearest.isEmpty():
            # Fallback: use redline centroid
            nearest = redline_geom.centroid()

        near_pt = nearest.asPoint()

        dist_m = int(round(redline_geom.distance(epsl_geom)))
        if dist_m < 1:
            dist_label = "On/within redline"
        elif dist_m < 1000:
            dist_label = f"{dist_m} m"
        else:
            dist_label = f"{dist_m / 1000:.2f} km"

        case_ref = feat["case_ref"] or ""

        line_geom = QgsGeometry.fromPolylineXY([
            QgsPointXY(near_pt.x(), near_pt.y()),
            QgsPointXY(epsl_pt.x(), epsl_pt.y()),
        ])

        lf = QgsFeature(lyr.fields())
        lf.setGeometry(line_geom)
        lf.setAttributes([case_ref, dist_m, dist_label])
        line_feats.append(lf)

    if not line_feats:
        return False, "no_lines"

    dp.addFeatures(line_feats)
    lyr.updateExtents()

    # Style — dashed dark orange line
    sym = QgsLineSymbol.createSimple({
        "line_color":  "#9A6700",
        "line_width":  "0.4",
        "line_style":  "dash",
        "capstyle":    "round",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    # Label lines with the distance, placed at the midpoint
    pal = QgsPalLayerSettings()
    pal.enabled        = True
    pal.fieldName      = "distance_label"
    pal.placement      = QgsPalLayerSettings.Placement.Line
    pal.placementFlags = QgsPalLayerSettings.AboveLine
    fmt = QgsTextFormat()
    fmt.setSize(6.5)
    fmt.setColor(QColor("#8A4B12"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    # One collapsed header instead of two loose top-level layers.
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    _nest_desk_study_layer(epsl_lyr, lyr, _EPSL_LAYER_NAME)

    return True, lyr


def remove_epsl_distance_lines():
    from qgis.core import QgsProject
    for lyr in QgsProject.instance().mapLayersByName(_EPSL_LINES_LAYER_NAME):
        QgsProject.instance().removeMapLayer(lyr.id())


# ---------------------------------------------------------------------------
# PHI toggle layers — per-habitat polygon display
# ---------------------------------------------------------------------------

# PHI habitat groups — each group maps to one button in the UI.
# Toggling a group loads/hides ALL constituent habitat types as a single layer.
# Tuple: (group_key, label, emoji, fill_colour_hex, hover_hex, [habitat_names])
PHI_HABITAT_GROUPS = [
    ("mixed", "Additional / mixed habitats", "◩", "#8A918C", "#F0F2EF", []),
    ("woodland", "Woodland", "🌳", "#556B2F", "#E9F4EC", [
        "Deciduous woodland",
        "Traditional orchard",
        "Wood-pasture and parkland",
    ]),
    ("grassland", "Grassland", "🌿", "#984EA3", "#f3e8f9", [
        "Lowland meadows",
        "Lowland calcareous grassland",
        "Lowland dry acid grassland",
        "Good quality semi improved grassland",
        "Upland hay meadow",
        "Upland calcareous grassland",
        "Calaminarian grassland",
        "Limestone pavement",
    ]),
    ("heath_bog", "Heath & Bog", "🟤", "#8C510A", "#FBF4E4", [
        "Lowland heathland",
        "Upland heathland",
        "Blanket bog",
        "Lowland raised bog",
        "Fragmented heath",
        "Grass moorland",
        "Mountain heaths and willow scrub",
        "Purple moor grass and rush pastures",
    ]),
    ("wetland", "Wetland & Fen", "💧", "#F4A3C4", "#fce4ec", [
        "Lowland fens",
        "Upland flushes fens and swamps",
        "Reedbeds",
        "Ponds",
        "Lakes",
        "Rivers",
        "Coastal and floodplain grazing marsh",
    ]),
    ("coastal", "Coastal", "🌊", "#3288BD", "#e1f5fe", [
        "Coastal sand dunes",
        "Coastal saltmarsh",
        "Coastal vegetated shingle",
        "Maritime cliff and slope",
        "Mudflats",
        "Saline lagoons",
    ]),
    ("rock_urban", "Rock & Urban", "🪨", "#999999", "#F4F6F2", [
        "Inland rock outcrop and scree habitats",
        "Open mosaic habitats on previously developed land",
    ]),
]

# Flat list for backwards compat (report function still iterates all types)
PHI_HABITAT_TYPES = [
    (hab, colour, hover)
    for (_, _label, _emoji, colour, hover, habs) in PHI_HABITAT_GROUPS
    for hab in habs
]

# Per-habitat fill colours — matched to the MAGIC / DEFRA reference colour table.
PHI_HABITAT_COLOURS = {
    # Woodland
    "Ancient Woodland":                                      "#556B2F",  # Ancient Semi-natural Woodland
    "Deciduous woodland":                                    "#78B578",  # Deciduous Woodland
    "Traditional orchard":                                   "#66A61E",  # Traditional Orchards
    "Wood-pasture and parkland":                             "#A6D96A",  # Coastal Vegetated Shingle (reuse)
    # Grassland
    "Lowland meadows":                                       "#F781BF",  # Lowland Meadows
    "Lowland calcareous grassland":                          "#E78AC3",  # Lowland calcareous grassland (table)
    "Lowland dry acid grassland":                            "#FDC086",  # Coastal & Floodplain Grazing Marsh tone
    "Good quality semi improved grassland":                  "#984EA3",  # Purple Moor Grass & Rush Pasture
    "Upland hay meadow":                                     "#E78AC3",  # Upland Hay Meadows
    "Upland calcareous grassland":                           "#FDC086",  # C&FP Grazing Marsh family
    "Calaminarian grassland":                                "#F46D43",  # Coastal Sand Dunes
    "Limestone pavement":                                    "#F46D43",  # warm orange (inland rock)
    "Coastal and floodplain grazing marsh":                  "#FDC086",  # Coastal & Floodplain Grazing Marsh
    "Purple moor grass and rush pastures":                   "#984EA3",  # Purple Moor Grass & Rush Pasture
    # Heath & Bog
    "Lowland heathland":                                     "#8C6D31",  # Lowland Heathland
    "Upland heathland":                                      "#8C6D31",  # same heathland brown
    "Blanket bog":                                           "#8C510A",  # Blanket Bog
    "Lowland raised bog":                                    "#D53E4F",  # Lowland Raised Bog
    "Fragmented heath":                                      "#8C6D31",  # heathland family
    "Grass moorland":                                        "#8C510A",  # bog/moorland brown
    "Mountain heaths and willow scrub":                      "#8C6D31",  # heathland family
    # Wetland & Fen
    "Lowland fens":                                          "#F4A3C4",  # Lowland Fen
    "Upland flushes fens and swamps":                        "#A6D854",  # Upland Flushes Fens & Swamps
    "Reedbeds":                                              "#66BD63",  # Coastal Saltmarsh green
    "Ponds":                                                 "#A6CEE3",  # Ramsar pale blue
    "Lakes":                                                 "#A6CEE3",  # same as Ponds
    # Coastal
    "Coastal saltmarsh":                                     "#66BD63",  # Coastal Saltmarsh
    "Coastal sand dunes":                                    "#F46D43",  # Coastal Sand Dunes
    "Coastal vegetated shingle":                             "#A6D96A",  # Coastal Vegetated Shingle
    "Maritime cliff and slope":                              "#3288BD",  # Maritime Cliffs and Slopes
    "Mudflats":                                              "#8C510A",  # Mudflats
    "Saline lagoons":                                        "#E78AC3",  # Saline Lagoons
    # Rivers
    "Rivers":                                                "#00BFC4",  # Priority Habitat Labels – Rivers
    "Reedbeds":                                              "#377EB8",  # Priority Habitat Labels – Reedbeds
    # Rock & Urban
    "Inland rock outcrop and scree habitats":                "#999999",  # Open Mosaic Habitat grey
    "Open mosaic habitats on previously developed land":     "#999999",  # Open Mosaic Habitat grey
    # Scotland (HabMoS → HABMOS_PRIORITY_MAP labels not in the England list)
    "Wet woodland":                                          "#3F7F6F",
    "Upland oakwood":                                        "#4E7D3A",
    "Upland birchwoods":                                     "#8FBC8F",
    "Lowland mixed deciduous woodland":                      "#78B578",
    "Upland mixed ashwoods":                                 "#5F9E5A",
    "Native pine woodlands":                                 "#2F5F3A",
    "Species-rich Nardus grassland":                         "#B084C9",
    "Tall herb communities":                                 "#C8A2C8",
    "Wet heath":                                             "#A0522D",
    "Dry heath":                                             "#C27C4A",
    "Oligotrophic and dystrophic lakes":                     "#87CEEB",
    "Mesotrophic lakes":                                     "#6BAED6",
    "Eutrophic standing waters":                             "#4292C6",
}

_PHI_GROUP_NAME = "Priority Habitats"

# ---------------------------------------------------------------------------
# Ancient Woodland — separate WFS dataset, displayed in Priority Habitats group
# ---------------------------------------------------------------------------
_AW_LAYER_NAME  = "Priority Habitat - Ancient Woodland"
_AW_COLOUR      = "#556B2F"  # MAGIC Ancient Semi-Natural Woodland
_AW_WFS_SLUG    = "ancient-woodland-england"
_AW_TYPENAME    = "Ancient_Woodland_England"
_AW_LAYER_CACHE = {}   # holds the QgsVectorLayer between toggles


def _aw_wfs_request():
    """(wfs_url, typename) for the active region's Ancient Woodland
    Inventory: Natural England's WFS in England, NatureScot's `awi` layer
    (habitatsandspecies workspace) in Scotland."""
    if _region.is_scotland():
        return _NS_WFS_HS, "habitatsandspecies:awi"
    return _DS_BASE_WFS.format(slug=_AW_WFS_SLUG), _AW_TYPENAME


def _aw_props(props):
    """(name, area_ha) from a raw AW feature, whichever region it came from.
    NatureScot's inventory rarely carries a site name, so the ANTIQUITY class
    ("Ancient (of semi-natural origin)", "Long-established (of plantation
    origin)", "Other (on Roy map)") stands in — it is also the value a report
    should quote."""
    props = props or {}
    if "HECTARE" in props or "ANTIQUITY" in props:
        name = (str(props.get("SITE_NAME") or "").strip()
                or str(props.get("ANTIQUITY") or "").strip()
                or "Ancient Woodland")
        raw = props.get("HECTARE")
    else:
        name = str(props.get("name", "") or "").strip()
        raw = props.get("area")
    try:
        area_ha = float(str(raw or 0).replace(",", "").strip())
    except (TypeError, ValueError):
        area_ha = 0.0
    return name, area_ha


def fetch_ancient_woodland_geojson(bbox_param):
    """Pure HTTP fetch of Ancient Woodland polygons within `bbox_param`.

    No QGIS objects — safe to call from worker threads.  `bbox_param` is a WFS
    BBOX string "xmin,ymin,xmax,ymax,EPSG:27700".  Returns a features list, or
    None on empty/error.  load_ancient_woodland_layer builds the QGIS layer.
    """
    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode

    wfs_url, _aw_type = _aw_wfs_request()
    params = urlencode({
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    _aw_type,
        "BBOX":         bbox_param,
        "SRSNAME":      "EPSG:27700",
        "outputFormat": "application/json",
        "count":        "500",
    })
    try:
        resp = urlopen(f"{wfs_url}?{params}", timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    feats = data.get("features", [])
    return feats if feats else None


def load_ancient_woodland_layer(easting, northing, redline_geom=None,
                                features=None):
    """Fetch Ancient Woodland polygons within 2 km via the NE WFS and display
    them inside the Priority Habitats layer group."""
    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsFillSymbol, QgsCategorizedSymbolRenderer, QgsRendererCategory,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    # Return cached layer if it is still alive and registered in the project.
    # The cached object may be a stale Python wrapper around a QgsVectorLayer
    # whose underlying C++ object QGIS has already destroyed (e.g. the user
    # deleted the layer manually). Touching .id() on such an object raises
    # RuntimeError, so guard the access and drop the stale entry if so.
    cached = _AW_LAYER_CACHE.get("ancient_wood")
    if cached is not None:
        try:
            cached_id = cached.id()
            still_registered = cached_id in QgsProject.instance().mapLayers()
        except RuntimeError:
            still_registered = False
        if still_registered:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(cached_id)
            if node is not None:
                node.setItemVisibilityChecked(True)
                grp = root.findGroup(_PHI_GROUP_NAME)
                if grp is not None:
                    grp.setItemVisibilityChecked(True)
                return True, cached
        # Stale or no longer in the tree — discard and fall through to refetch.
        _AW_LAYER_CACHE.pop("ancient_wood", None)

    if easting is None or northing is None:
        return False, "no_centroid"

    # Ancient woodland shares the Priority Habitats radius — the two render in
    # the same map group, so they search the same distance.
    RADIUS = _search_radius("priority_habitats")
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)

    # Reuse caller-supplied pre-fetch when available (parallel "Show All"
    # path); otherwise fetch now (single Ancient Woodland toggle path).
    if features is None:
        bb = search_buffer.boundingBox()
        bbox_param = f"{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()},EPSG:27700"

        wfs_url, _aw_type = _aw_wfs_request()
        params = urlencode({
            "SERVICE":      "WFS",
            "VERSION":      "2.0.0",
            "REQUEST":      "GetFeature",
            "TYPENAMES":    _aw_type,
            "BBOX":         bbox_param,
            "SRSNAME":      "EPSG:27700",
            "outputFormat": "application/json",
            "count":        "500",
        })
        try:
            resp = urlopen(f"{wfs_url}?{params}", timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return False, f"WFS fetch failed: {exc}"

        features = data.get("features", [])

    if not features:
        return False, "no_features"

    lyr = QgsVectorLayer(
        "Polygon?crs=EPSG:27700"
        "&field=name:string(200)"
        "&field=area_ha:double",
        _AW_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    qfeats = []
    for feat in features:
        props = feat.get("properties", {})
        geom  = feat.get("geometry",   {})
        feat_name, area_ha = _aw_props(props)

        gtype  = geom.get("type", "")
        coords = geom.get("coordinates", [])
        if not coords:
            continue

        def _rings(poly_coords):
            return [[QgsPointXY(c[0], c[1]) for c in ring] for ring in poly_coords]

        if gtype == "Polygon":
            qg = QgsGeometry.fromPolygonXY(_rings(coords))
        elif gtype == "MultiPolygon":
            qg = QgsGeometry.fromMultiPolygonXY([_rings(p) for p in coords])
        else:
            continue

        if qg.isEmpty():
            continue
        f = QgsFeature(lyr.fields())
        f.setGeometry(qg)
        f.setAttributes([feat_name, round(area_ha, 2)])
        qfeats.append(f)

    # Post-filter to true 2km buffer
    qfeats = [f for f in qfeats if f.geometry().intersects(search_buffer)]
    if not qfeats:
        return False, "no_features"

    dp.addFeatures(qfeats)
    lyr.updateExtents()

    # Style
    fill_col = QColor(_AW_COLOUR)
    fill_col.setAlpha(80)
    site_names = sorted({f.attribute("name") for f in lyr.getFeatures() if f.attribute("name")})
    categories = []
    for site_name in site_names:
        sym = QgsFillSymbol.createSimple({
            "color":         fill_col.name(QColor.NameFormat.HexArgb),
            "outline_color": _AW_COLOUR,
            "outline_width": "0.6",
            "outline_style": "solid",
        })
        categories.append(QgsRendererCategory(site_name, sym, site_name))
    fallback = QgsFillSymbol.createSimple({
        "color":         fill_col.name(QColor.NameFormat.HexArgb),
        "outline_color": _AW_COLOUR,
        "outline_width": "0.6",
        "outline_style": "solid",
    })
    categories.append(QgsRendererCategory("", fallback, _AW_LAYER_NAME))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("name", categories))
    lyr.setOpacity(0.85)

    # Labels
    pal = QgsPalLayerSettings()
    pal.enabled   = True
    pal.fieldName = "name"
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor(_AW_COLOUR))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    # Add to Priority Habitats group
    root = QgsProject.instance().layerTreeRoot()
    grp  = root.findGroup(_PHI_GROUP_NAME)
    if grp is None:
        bm  = root.findGroup("Base Maps")
        idx = root.children().index(bm) if bm is not None else 0
        grp = root.insertGroup(idx, _PHI_GROUP_NAME)
        grp.setExpanded(False)

    # Remove any previous Ancient Woodland layer(s) before adding the new one,
    # so repeated desk-study runs don't stack duplicate copies in the project
    # (which then appear twice in the map legend). Match on a whitespace-
    # collapsed name, not mapLayersByName: legend label wrapping in earlier
    # builds leaked a newline into the layer name, and an exact-name lookup
    # misses that copy.
    for old in list(QgsProject.instance().mapLayers().values()):
        if " ".join(old.name().split()) == _AW_LAYER_NAME:
            QgsProject.instance().removeMapLayer(old.id())

    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)
    grp.addLayer(lyr).setItemVisibilityChecked(True)
    grp.setItemVisibilityChecked(True)

    _AW_LAYER_CACHE["ancient_wood"] = lyr
    return True, lyr


def hide_ancient_woodland_layer():
    """Hide the Ancient Woodland layer (keep cached for instant re-display)."""
    from qgis.core import QgsProject
    cached = _AW_LAYER_CACHE.get("ancient_wood")
    if cached is None:
        return
    try:
        cached_id = cached.id()
    except RuntimeError:
        # Underlying C++ layer already destroyed — drop the stale wrapper.
        _AW_LAYER_CACHE.pop("ancient_wood", None)
        return
    node = QgsProject.instance().layerTreeRoot().findLayer(cached_id)
    if node is not None:
        node.setItemVisibilityChecked(False)


def query_ancient_woodland_2km(easting, northing, redline_geom=None):
    """Query Ancient Woodland polygons within 2 km via the NE WFS.

    Returns a list of dicts matching the same schema as query_priority_habitats_2km:
        {
          'habitat':     str   — always "Ancient Woodland"
          'hab_code':    str   — always "AW"
          'description': str   — woodland name
          'add_habs':    str   — ""
          'area_ha':     float
          'centroid_gr': str   — 6-figure BNG grid ref
          'distance_m':  int   — edge-to-edge distance in metres (0 = overlap)
          'direction':   str   — 8-point cardinal
        }
    Sorted by distance ascending.
    """
    import json
    import math
    from urllib.request import urlopen
    from urllib.parse import urlencode

    # Ancient woodland shares the Priority Habitats radius (same map group).
    RADIUS = _search_radius("priority_habitats")
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bb = search_buffer.boundingBox()
    bbox_param = f"{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()},EPSG:27700"

    wfs_url, _aw_type = _aw_wfs_request()
    params = urlencode({
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    _aw_type,
        "BBOX":         bbox_param,
        "SRSNAME":      "EPSG:27700",
        "outputFormat": "application/json",
        "count":        "500",
    })
    try:
        resp = urlopen(f"{wfs_url}?{params}", timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Ancient Woodland WFS failed: {exc}")

    features = data.get("features", [])
    if not features:
        return []

    from qgis.core import QgsGeometry, QgsPointXY

    def _rings(poly_coords):
        return [[QgsPointXY(c[0], c[1]) for c in ring] for ring in poly_coords]

    def _direction(dx, dy):
        angle = math.degrees(math.atan2(dy, dx))
        dirs  = ["E","NE","N","NW","W","SW","S","SE"]
        return dirs[round(angle / 45) % 8]

    try:
        from .gridref_utils import osgb_en_to_gridref
    except Exception:
        osgb_en_to_gridref = None

    results = []
    for feat in features:
        props  = feat.get("properties", {})
        geom_j = feat.get("geometry", {})
        gtype  = geom_j.get("type", "")
        coords = geom_j.get("coordinates", [])
        if not coords:
            continue
        if gtype == "Polygon":
            qg = QgsGeometry.fromPolygonXY(_rings(coords))
        elif gtype == "MultiPolygon":
            qg = QgsGeometry.fromMultiPolygonXY([_rings(p) for p in coords])
        else:
            continue
        if qg.isEmpty() or not qg.intersects(search_buffer):
            continue

        name, area_ha = _aw_props(props)

        # Distance (edge-to-edge, 0 if overlapping)
        dist_m = max(0, int(qg.distance(search_buffer) if not qg.intersects(search_buffer)
                            else 0))
        if redline_geom and not redline_geom.isNull():
            dist_m = max(0, int(qg.distance(redline_geom)))

        centroid = qg.centroid().asPoint()
        cx, cy   = centroid.x(), centroid.y()
        dx, dy   = cx - easting, cy - northing
        direction = _direction(dx, dy)

        gr = ""
        if osgb_en_to_gridref:
            try:
                gr = osgb_en_to_gridref(int(cx), int(cy), 6)
            except Exception:
                pass

        results.append({
            "habitat":     "Ancient Woodland",
            "hab_code":    "AW",
            "description": name,
            "add_habs":    "",
            "area_ha":     round(area_ha, 4),
            "centroid_gr": gr,
            "distance_m":  dist_m,
            "direction":   direction,
        })

    results.sort(key=lambda r: r["distance_m"])
    return results


def _aw_layer_cache():
    """Expose cache for clearing on redline reset."""
    return _AW_LAYER_CACHE


def _phi_layer_name(group_key):
    """Return a human-readable layer name using the group label."""
    entry = next((g for g in PHI_HABITAT_GROUPS if g[0] == group_key), None)
    if entry:
        return "Priority Habitat - " + entry[1]
    return "Priority Habitat - " + group_key


def fetch_phi_group_geojson(group_key, bbox):
    """Pure HTTP fetch of all PHI polygons for a habitat GROUP within `bbox`.

    No QGIS objects — safe to call from worker threads.  `bbox` is an esri
    envelope string "xmin,ymin,xmax,ymax" in EPSG:27700.  Returns
    (group_key, features_list) on success, or (group_key, None) on empty/error.
    Layer construction happens on the main thread in load_phi_group_layer.
    """
    try:
        all_features = _fetch_phi_group_features(group_key, bbox)
    except Exception:
        return group_key, None
    return group_key, (all_features if all_features else None)


def _fetch_phi_group_features(group_key, bbox):
    """Raw esri-like features for one habitat group within `bbox` — Natural
    England's PHI service in England, the classified HabMoS pull in Scotland.
    Raises on network / API failure; returns [] when nothing is there."""
    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode

    group_entry = next((g for g in PHI_HABITAT_GROUPS if g[0] == group_key), None)
    if group_entry is None:
        raise RuntimeError("Unknown group: " + group_key)
    habitats = group_entry[5]

    if _region.is_scotland():
        return [f for f in _habmos_features(bbox)
                if f["attributes"].get("_group") == group_key]

    if group_key == "mixed":
        # Only true multi-attribution NMHAB polygons belong beneath the mixed
        # heading.  A single AddHabs code is shown with that habitat's normal
        # group instead.
        where = (
            "MainHabs = 'No main habitat but additional habitats present' "
            "AND AddHabs LIKE '%,%'"
        )
    else:
        main_habitat_terms = [
            "MainHabs LIKE '%" + habitat + "%'" for habitat in habitats
        ]
        names_to_codes = {
            name: code for code, name in _PHI_ADDITIONAL_HABITAT_NAMES.items()
        }
        single_additional_codes = [
            names_to_codes[habitat]
            for habitat in habitats
            if habitat in names_to_codes
        ]
        where_parts = ["(" + " OR ".join(main_habitat_terms) + ")"]
        if single_additional_codes:
            # Exact equality deliberately excludes comma-separated mosaics.
            # Those are fetched only by the dedicated mixed group above.
            single_terms = " OR ".join(
                "AddHabs = '" + code + "'" for code in single_additional_codes
            )
            where_parts.append(
                "(MainHabs = 'No main habitat but additional habitats present' "
                "AND (" + single_terms + "))"
            )
        where = "(" + " OR ".join(where_parts) + ")"

    all_features = []
    offset = 0
    while True:
        params = urlencode({
            "where":             where,
            "geometry":          bbox,
            "geometryType":      "esriGeometryEnvelope",
            "inSR":              "27700",
            "spatialRel":        "esriSpatialRelIntersects",
            "outFields":         "MainHabs,HabCodes,AreaHa,FeatDesc,AddHabs",
            "returnGeometry":    "true",
            "outSR":             "27700",
            "resultOffset":      offset,
            "resultRecordCount": 2000,
            "f":                 "json",
        })
        try:
            resp = urlopen(_PHI_BASE + "?" + params, timeout=30)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"PHI fetch failed: {exc}")
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "API error"))
        page = data.get("features", [])
        all_features.extend(page)
        if not data.get("exceededTransferLimit", False) or not page:
            break
        offset += len(page)

    return all_features


def load_phi_group_layer(group_key, easting, northing, redline_geom=None,
                         features=None):
    """
    Fetch and display PHI polygons for a habitat GROUP within 2 km.

    Builds a single OR WHERE clause covering all constituent habitat types
    so one layer represents e.g. all Woodland or all Coastal habitats.
    If the layer already exists it is made visible rather than re-fetched.

    Returns (True, layer) on success or (False, reason_str) on failure.
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsFillSymbol,
        QgsCategorizedSymbolRenderer, QgsRendererCategory,
    )
    from qgis.PyQt.QtGui import QColor

    group_entry = next((g for g in PHI_HABITAT_GROUPS if g[0] == group_key), None)
    if group_entry is None:
        return False, "Unknown group: " + group_key

    _key, label, emoji, colour, hover, habitats = group_entry
    layer_name = _phi_layer_name(group_key)

    # If already loaded, just show it.  Layers created before AddHabs support
    # are rebuilt so a plugin reload cannot leave the old generic NMHAB legend
    # cached in the project.
    existing = QgsProject.instance().mapLayersByName(layer_name)
    if existing:
        current = existing[0]
        if current.customProperty("biome_phi_attribution_version", 0) == 4:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(current.id())
            if node is not None:
                node.setItemVisibilityChecked(True)
            grp = root.findGroup(_PHI_GROUP_NAME)
            if grp is not None:
                grp.setItemVisibilityChecked(True)
            return True, current
        for stale in existing:
            QgsProject.instance().removeMapLayer(stale.id())

    # Use redline buffer bbox (matches the report query area exactly).
    # search_buffer is also needed for the post-filter below, so build it
    # regardless of whether we fetch or reuse caller-supplied features.
    search_buffer = _build_search_buffer(easting, northing, redline_geom,
                                         radius=_search_radius("priority_habitats"))

    # Reuse caller-supplied pre-fetch when available (parallel "Show All"
    # path); otherwise fetch + paginate now (single-group toggle path).
    if features is None:
        bb = search_buffer.boundingBox()
        bbox = f"{bb.xMinimum()},{bb.yMinimum()},{bb.xMaximum()},{bb.yMaximum()}"

        try:
            features = _fetch_phi_group_features(group_key, bbox)
        except Exception as exc:
            return False, "Fetch failed: " + str(exc)

    all_features = features or []
    if not all_features:
        return False, "no_features"

    lyr = QgsVectorLayer(
        "Polygon?crs=EPSG:27700"
        "&field=MainHabs:string(255)"
        "&field=HabCodes:string(100)"
        "&field=AreaHa:double"
        "&field=FeatDesc:string(200)"
        "&field=AddHabs:string(100)",
        layer_name, "memory"
    )
    dp = lyr.dataProvider()
    qfeats = []
    for feat in all_features:
        attrs = feat.get("attributes", {})
        geom  = feat.get("geometry",   {})
        rings = geom.get("rings", [])
        if not rings:
            continue
        qrings = [[QgsPointXY(pt[0], pt[1]) for pt in ring] for ring in rings]
        g = QgsGeometry.fromPolygonXY(qrings)
        if g.isEmpty():
            continue
        main_habitat = str(attrs.get("MainHabs", "") or "").strip()
        habitat_code = str(attrs.get("HabCodes", "") or "").strip()
        additional_habitats = str(attrs.get("AddHabs", "") or "").strip()
        display_habitat, display_code = _phi_report_habitat(
            main_habitat, habitat_code, additional_habitats
        )
        f = QgsFeature(lyr.fields())
        f.setGeometry(g)
        f.setAttributes([
            display_habitat,
            display_code,
            float(attrs.get("AreaHa",  0) or 0),
            str(attrs.get("FeatDesc", "") or ""),
            additional_habitats,
        ])
        qfeats.append(f)

    if not qfeats:
        return False, "no_features"

    # Post-filter: discard features whose geometry doesn't actually intersect
    # the 2km search buffer — catches large habitat polygons that overlap the
    # bbox bounding box but whose boundary is outside the true search area.
    # (search_buffer was already built above.)
    qfeats = [f for f in qfeats if f.geometry().intersects(search_buffer)]

    if not qfeats:
        return False, "no_features"

    dp.addFeatures(qfeats)
    lyr.updateExtents()

    # Build a categorized renderer: one category per named habitat sub-type,
    # each coloured with the official Natural England / DEFRA MAGIC service
    # colour. Habitat names the group list doesn't know (the Scottish HabMoS
    # labels — "Native pine woodlands", "Wet heath"…) get their own category
    # too, so they key in the legend instead of collapsing into "Other".
    present = []
    for f in qfeats:
        v = f.attribute("MainHabs")
        if v and v not in habitats and v not in present:
            present.append(v)
    categories = []
    for hab_name in list(habitats) + present:
        # Additional/mixed polygons use a neutral colour so they are not
        # mistaken for polygons with a confirmed main habitat classification.
        hex_col = (colour if group_key == "mixed"
                   else PHI_HABITAT_COLOURS.get(hab_name, colour))
        fill_col = QColor(hex_col)
        fill_col.setAlpha(180)
        outline_col = QColor(hex_col).darker(130).name()
        sym = QgsFillSymbol.createSimple({
            "color":         fill_col.name(QColor.NameFormat.HexArgb),
            "outline_color": outline_col,
            "outline_width": "0.4",
            "outline_style": "solid",
        })
        categories.append(QgsRendererCategory(hab_name, sym, hab_name))

    # Catch-all for any value not explicitly listed
    fallback_col = QColor(colour)
    fallback_col.setAlpha(120)
    fallback_sym = QgsFillSymbol.createSimple({
        "color":         fallback_col.name(QColor.NameFormat.HexArgb),
        "outline_color": colour,
        "outline_width": "0.4",
        "outline_style": "solid",
    })
    categories.append(QgsRendererCategory("", fallback_sym, "Other"))

    renderer = QgsCategorizedSymbolRenderer("MainHabs", categories)
    lyr.setRenderer(renderer)
    lyr.setOpacity(0.85)

    root = QgsProject.instance().layerTreeRoot()
    grp  = root.findGroup(_PHI_GROUP_NAME)
    if grp is None:
        bm  = root.findGroup("Base Maps")
        idx = root.children().index(bm) if bm is not None else 0
        grp = root.insertGroup(idx, _PHI_GROUP_NAME)
        grp.setExpanded(False)

    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    lyr.setCustomProperty("biome_phi_attribution_version", 4)
    QgsProject.instance().addMapLayer(lyr, False)
    grp.addLayer(lyr).setItemVisibilityChecked(True)
    grp.setItemVisibilityChecked(True)
    return True, lyr


def hide_phi_group_layer(group_key):
    """Hide (not delete) the polygon layer for a habitat group."""
    from qgis.core import QgsProject
    layer_name = _phi_layer_name(group_key)
    for lyr in QgsProject.instance().mapLayersByName(layer_name):
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(lyr.id())
        if node is not None:
            node.setItemVisibilityChecked(False)


# Keep old name as alias for backward compat
def remove_phi_habitat_layer(habitat):
    hide_phi_group_layer(habitat)


# Keep old name as alias for backward compat
def load_phi_habitat_layer(habitat, easting, northing):
    return load_phi_group_layer(habitat, easting, northing)


# ---------------------------------------------------------------------------
# Tree Preservation Orders (TPO) — planning.data.gov.uk REST API
# ---------------------------------------------------------------------------

# TPO search radius. Individual protected trees and woodland/area/group zones
# are only an arboricultural constraint when very close to the site, so the TPO
# search is tightened to 200 m (conservation areas remain a 2 km search).
_TPO_RADIUS_M = 200

_TPO_LAYER_NAME          = "Tree Preservation Orders"
_TPO_LINES_LAYER_NAME    = "TPO Distance Lines"
_TPO_ZONE_LAYER_NAME     = "TPO Zones"
_CONSERVATION_LAYER_NAME = "Conservation Areas"

_TPO_API_BASE = "https://www.planning.data.gov.uk/entity.json"


def _parse_wkt_point(wkt):
    """Parse a WKT POINT string like 'POINT (-0.123 51.456)' → (lon, lat) floats, or None."""
    import re
    if not wkt or not isinstance(wkt, str):
        return None
    m = re.search(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)", wkt, re.IGNORECASE)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _planning_bbox_wkt(easting, northing, redline_geom, radius_m):
    """WGS84 POLYGON WKT covering the search-buffer bounding box — the shared
    `geometry=` parameter for every planning.data.gov.uk query.

    Must run on the main thread: it uses a QgsCoordinateTransform against the
    project.  Pre-compute this here, then hand the resulting string to
    fetch_planning_entities() on a worker thread.
    """
    from qgis.core import (
        QgsPointXY, QgsCoordinateTransform,
        QgsCoordinateReferenceSystem, QgsProject,
    )
    search_buffer = _build_search_buffer(easting, northing, redline_geom, radius_m)
    bb     = search_buffer.boundingBox()
    bng    = QgsCoordinateReferenceSystem("EPSG:27700")
    wgs    = QgsCoordinateReferenceSystem("EPSG:4326")
    to_wgs = QgsCoordinateTransform(bng, wgs, QgsProject.instance())
    corners = [
        QgsPointXY(bb.xMinimum(), bb.yMinimum()),
        QgsPointXY(bb.xMaximum(), bb.yMinimum()),
        QgsPointXY(bb.xMaximum(), bb.yMaximum()),
        QgsPointXY(bb.xMinimum(), bb.yMaximum()),
    ]
    cw = [to_wgs.transform(c) for c in corners]
    min_lon = min(c.x() for c in cw); max_lon = max(c.x() for c in cw)
    min_lat = min(c.y() for c in cw); max_lat = max(c.y() for c in cw)
    wkt = (
        f"POLYGON(("
        f"{min_lon:.6f} {min_lat:.6f},{max_lon:.6f} {min_lat:.6f},"
        f"{max_lon:.6f} {max_lat:.6f},{min_lon:.6f} {max_lat:.6f},"
        f"{min_lon:.6f} {min_lat:.6f}))"
    )
    # Remember the BNG box behind this WKT so the Scottish (Spatial Hub WFS)
    # fetch can query in EPSG:27700 on a worker thread without a transform.
    _BNG_BBOX_FOR_WKT[wkt] = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
    return wkt


_BNG_BBOX_FOR_WKT = {}     # planning bbox WKT (WGS84) → (xmin, ymin, xmax, ymax) BNG


# ── Scotland: Spatial Hub TPOs ──────────────────────────────────────────────
# The Improvement Service's national "Tree Preservation Orders - Scotland"
# dataset (points + polygons, OGL, updated monthly) is served from a
# GeoServer that only answers with a registered user's authkey
# (config.get_spatialhub_authkey). Layer names and attribute schemas are not
# documented, so they are DISCOVERED per key: GetCapabilities lists the
# sh_tpo feature types, DescribeFeatureType tells points from polygons, and
# attributes are matched by name pattern into the planning.data.gov.uk
# entity shape the TPO query/report code already understands (with the BNG
# geometry attached under "_bng" / "_bng_geojson" so no WGS84 round-trip).
_SH_WFS_TPO   = "https://geo.spatialhub.scot/geoserver/sh_tpo/wfs"
_SH_TYPE_CACHE = {}      # authkey → {"tree": typename|None, "tree-preservation-zone": typename|None}
SPATIALHUB_TPO_URL = "https://data.spatialhub.scot/dataset/tree_preservation_orders-is"


_SH_WFS_ROOT  = "https://geo.spatialhub.scot/geoserver/wfs"


def _sh_get(params, authkey, timeout=40, base=None):
    import urllib.error
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode
    q = dict(params)
    q["authkey"] = authkey
    req = Request(f"{base or _SH_WFS_TPO}?{urlencode(q)}",
                  headers={"User-Agent": "BiomeQGISPlugin/1.0"})
    try:
        return urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(
                "Spatial Hub Scotland rejected the authkey (HTTP "
                f"{exc.code}) — check it in Biome settings.")
        raise RuntimeError(f"Spatial Hub Scotland request failed: HTTP {exc.code}")


def _sh_discover_tpo_types(authkey):
    """{"tree": <point typename>, "tree-preservation-zone": <polygon typename>}
    for the sh_tpo workspace, cached per key. Raises on failure."""
    import re
    import json
    cached = _SH_TYPE_CACHE.get(authkey)
    if cached:
        return cached

    def _typenames(base):
        caps = _sh_get({"service": "WFS", "version": "2.0.0",
                        "request": "GetCapabilities"}, authkey, base=base)
        return [n.strip() for n in
                re.findall(r"<(?:\w+:)?Name>\s*([^<\s]+)\s*</(?:\w+:)?Name>", caps)
                if ":" in n]

    # The sh_tpo workspace first (known layers: sh_tpo:pub_tpopnt points +
    # a polygon twin); fall back to the whole server filtered by name.
    names = _typenames(_SH_WFS_TPO)
    if not any(re.search(r"tpo|tree", n, re.I) for n in names):
        names = [n for n in _typenames(_SH_WFS_ROOT)
                 if re.search(r"tpo|tree_?pres", n, re.I)]
    names = [n for n in names if re.search(r"tpo|tree", n, re.I)] or names

    def _geom_kind(n):
        """'point' / 'polygon' / None — schema first, then a sample feature
        (GeoServer often declares a generic GeometryPropertyType)."""
        try:
            desc = _sh_get({"service": "WFS", "version": "2.0.0",
                            "request": "DescribeFeatureType", "typeNames": n}, authkey)
        except Exception:
            desc = ""
        if re.search(r"Point", desc) and not re.search(r"Surface|Polygon", desc):
            return "point"
        if re.search(r"Surface|Polygon", desc) and not re.search(r"Point", desc):
            return "polygon"
        try:
            body = _sh_get({"service": "WFS", "version": "2.0.0",
                            "request": "GetFeature", "typeNames": n, "count": "1",
                            "outputFormat": "application/json"}, authkey)
            feats = json.loads(body).get("features") or []
            gt = (feats[0].get("geometry") or {}).get("type", "") if feats else ""
            if gt in ("Point", "MultiPoint"):
                return "point"
            if gt in ("Polygon", "MultiPolygon"):
                return "polygon"
        except Exception:
            pass
        # Last resort: the layer name itself
        if re.search(r"pnt|point", n, re.I):
            return "point"
        if re.search(r"poly|area|zone", n, re.I):
            return "polygon"
        return None

    found = {"tree": None, "tree-preservation-zone": None}
    seen = []
    for n in names:
        kind = _geom_kind(n)
        seen.append(f"{n} ({kind or 'unknown'})")
        if kind == "point" and not found["tree"]:
            found["tree"] = n
        elif kind == "polygon" and not found["tree-preservation-zone"]:
            found["tree-preservation-zone"] = n
    if not any(found.values()):
        raise RuntimeError(
            "Spatial Hub Scotland: key accepted but no TPO point/polygon layer "
            "could be identified. Layers seen: "
            + (", ".join(seen) if seen else "none"))
    _SH_TYPE_CACHE[authkey] = found
    return found


def _sh_pick(props, *patterns):
    """First non-empty property whose NAME matches one of the regex
    patterns (tried in order) — schema-agnostic attribute matching."""
    import re
    for pat in patterns:
        for k, v in props.items():
            if v not in (None, "") and re.search(pat, str(k), re.I):
                return str(v).strip()
    return ""


def _sh_feature_to_entity(feat, dataset):
    """A Spatial Hub GeoJSON feature → planning.data.gov.uk-style entity."""
    props = feat.get("properties") or {}
    geom  = feat.get("geometry") or {}
    species = _sh_pick(props, r"species")
    address = _sh_pick(props, r"address|location|street|site_add")
    name    = _sh_pick(props, r"^name$|tpo_name|site_name|title", r"desc")
    ref     = _sh_pick(props, r"^(tpo_)?ref", r"reference", r"tpo_no|tpo_num|order_no|order_num|number",
                       r"^id$|objectid|gid")
    order   = _sh_pick(props, r"tpo_name|order_name|tpo_title|^tpo$", r"^order")
    status  = _sh_pick(props, r"status")
    ent = {
        "reference":                    ref,
        "tree-preservation-order":      order or ref,
        "name":                         name,
        "description":                  _sh_pick(props, r"desc"),
        "notes":                        _sh_pick(props, r"notes|comment|remark"),
        "tree-species":                 species,
        "tree-species-list":            species,
        "address-text":                 address,
        "tree-preservation-zone-type":  _sh_pick(props, r"tpo_type|type|category|class"),
        "start-date":                   _sh_pick(props, r"date_made|made|confirm|start|date"),
        "entry-date":                   "",
        "end-date":                     ("revoked" if status and
                                         __import__("re").search(r"revok|cancel|lapsed|expired|withdrawn",
                                                                 status, __import__("re").I) else ""),
        "organisation":                 _sh_pick(props, r"local_auth|^la$|council|authority|la_name"),
    }
    gtype, coords = geom.get("type", ""), geom.get("coordinates") or []
    if dataset == "tree":
        pt = None
        if gtype == "Point":
            pt = coords
        elif gtype == "MultiPoint" and coords:
            pt = coords[0]
        if not pt:
            return None
        ent["_bng"] = (float(pt[0]), float(pt[1]))
    else:
        if gtype not in ("Polygon", "MultiPolygon") or not coords:
            return None
        ent["_bng_geojson"] = geom
    return ent


def _fetch_spatialhub_tpo(dataset, bng_bbox):
    """Spatial Hub Scotland TPO points ("tree") or polygons
    ("tree-preservation-zone") intersecting `bng_bbox` (xmin, ymin, xmax,
    ymax, EPSG:27700), as planning-style entity dicts. Pure HTTP — worker
    thread safe. Raises RuntimeError (no key / bad key / network)."""
    import json
    from .config import get_spatialhub_authkey
    authkey = get_spatialhub_authkey()
    if not authkey:
        raise RuntimeError("Spatial Hub Scotland authkey not set — add it in Biome settings.")
    typename = _sh_discover_tpo_types(authkey).get(dataset)
    if not typename:
        return []
    xmin, ymin, xmax, ymax = bng_bbox
    out, start, page = [], 0, 2000
    while True:
        body = _sh_get({
            "service":      "WFS",
            "version":      "2.0.0",
            "request":      "GetFeature",
            "typeNames":    typename,
            "bbox":         f"{xmin},{ymin},{xmax},{ymax},EPSG:27700",
            "srsName":      "EPSG:27700",
            "outputFormat": "application/json",
            "count":        str(page),
            "startIndex":   str(start),
        }, authkey, timeout=60)
        try:
            data = json.loads(body)
        except Exception:
            raise RuntimeError("Spatial Hub Scotland returned an unexpected response (not GeoJSON)")
        feats = data.get("features", []) or []
        for f in feats:
            ent = _sh_feature_to_entity(f, dataset)
            if ent is not None:
                out.append(ent)
        if len(feats) < page:
            break
        start += len(feats)
    return out


def fetch_planning_entities(dataset, bbox_wkt):
    """Pure HTTP, paginated fetch of planning.data.gov.uk entities for `dataset`
    whose geometry intersects `bbox_wkt` (a WGS84 POLYGON WKT string).

    No QGIS objects — safe to call from worker threads.  Returns a list of raw
    entity dicts (possibly empty).  Raises RuntimeError on a network failure so
    callers can tell a real error apart from a genuinely empty result.
    """
    import json
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode

    # Scotland: the national planning.data.gov.uk datasets stop at the
    # border — TPO points / zones come from Spatial Hub Scotland instead
    # (conservation areas stay England-only for now).
    if (dataset in ("tree", "tree-preservation-zone")
            and _region.is_scotland()):
        bng_bbox = _BNG_BBOX_FOR_WKT.get(bbox_wkt)
        if bng_bbox is None:
            raise RuntimeError("Spatial Hub query needs a BNG bbox (build it via _planning_bbox_wkt)")
        return _fetch_spatialhub_tpo(dataset, bng_bbox)

    all_entities = []
    page, offset = 100, 0
    while True:
        params = urlencode({
            "dataset":           dataset,
            "geometry":          bbox_wkt,
            "geometry_relation": "intersects",
            "limit":             page,
            "offset":            offset,
        })
        req = Request(f"{_TPO_API_BASE}?{params}",
                      headers={"User-Agent": "BiomeQGISPlugin/1.0"})
        try:
            resp = urlopen(req, timeout=30)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"{dataset} query failed: {exc}")
        entities = data.get("entities", [])
        all_entities.extend(entities)
        count = data.get("count", 0)
        if not entities or offset + len(entities) >= count:
            break
        offset += len(entities)
    return all_entities


def query_tpo_2km(easting, northing, redline_geom=None, entities=None):
    """
    Query individual protected trees (TPO) within 200 m of the redline boundary.

    Source: planning.data.gov.uk — dataset=tree
    Follows the same pattern as query_designated_sites_2km:
      1. Build a true 200 m buffer from the redline EDGE (not centroid).
      2. Use the buffer bounding box (converted to WGS84) for the API BBOX query.
      3. Post-filter results with search_buffer.intersects() for the true boundary.

    Returns list of dicts:
        {
          'reference':    str   — tree reference
          'tpo_ref':      str   — parent TPO order reference
          'species':      str   — tree species if available
          'address':      str   — address text if available
          'organisation': str
          'entry_date':   str   — "YYYY-MM-DD" or ""
          'start_date':   str   — "YYYY-MM-DD" or ""
          'easting':      int   — BNG
          'northing':     int   — BNG
          'distance_m':   int
          'direction':    str
          'grid_ref':     str
        }
    Sorted by distance ascending.
    """
    from qgis.core import (
        QgsPointXY, QgsCoordinateTransform,
        QgsCoordinateReferenceSystem, QgsProject,
    )

    RADIUS = _search_radius("tpo", _TPO_RADIUS_M)

    # True buffer from the redline EDGE — needed for the post-filter below
    # and for the API bbox (when we fetch here).
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bng = QgsCoordinateReferenceSystem("EPSG:27700")
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    wgs_to_bng = QgsCoordinateTransform(wgs, bng, QgsProject.instance())

    # Reuse a caller-supplied pre-fetch when available (parallel TPO toggle);
    # otherwise fetch the 'tree' points now (report path / single use).
    if entities is None:
        bbox_wkt = _planning_bbox_wkt(easting, northing, redline_geom, RADIUS)
        entities = fetch_planning_entities("tree", bbox_wkt)

    all_entities = entities

    # ── Post-filter and compute distances ──────────────────────────────────────
    results = []

    for ent in all_entities:
        # Skip end-dated entities — the API returns revoked/removed records
        # too (2026 survey: 1.3% of the national 'tree' dataset), which must
        # not be reported as live TPO constraints.
        if str(ent.get("end-date") or "").strip():
            continue
        if ent.get("_bng"):
            # Spatial Hub Scotland entities arrive already in BNG
            fx, fy = int(round(ent["_bng"][0])), int(round(ent["_bng"][1]))
        else:
            # Parse the WKT point — "POINT (lon lat)"
            point_wkt = ent.get("point") or ""
            parsed = _parse_wkt_point(point_wkt)
            if not parsed:
                continue
            fx_wgs, fy_wgs = parsed

            # Convert WGS84 → BNG
            try:
                bng_pt = wgs_to_bng.transform(QgsPointXY(float(fx_wgs), float(fy_wgs)))
                fx, fy = int(round(bng_pt.x())), int(round(bng_pt.y()))
            except Exception:
                continue

        feat_geom = _esri_point_to_qgsgeom({"x": fx, "y": fy})

        # Post-filter: must intersect the true 2 km buffer from redline edge
        if not feat_geom.isEmpty() and not search_buffer.isEmpty():
            if not search_buffer.intersects(feat_geom):
                continue

        dist, direction = _redline_distance_and_direction(redline_geom, feat_geom, fx, fy)
        if dist > RADIUS:
            continue

        try:
            from .gridref_utils import osgb_en_to_gridref
            gr = osgb_en_to_gridref(fx, fy, 6)
        except Exception:
            gr = f"{fx},{fy}"

        name = str(ent.get("name") or "").strip()
        # A felled-date without an end-date is a live record of a tree that no
        # longer stands — surface that rather than listing it like the rest.
        felled = str(ent.get("felled-date") or "").strip()
        if felled:
            name = f"{name} (felled {felled})".strip()

        results.append({
            "reference":    str(ent.get("reference")               or "").strip(),
            "name":         name,
            "tpo_ref":      str(ent.get("tree-preservation-order") or "").strip(),
            "species":      str(ent.get("tree-species")            or "").strip(),
            # 'address-text' is the spec field; a few councils publish
            # 'address' instead — fall back to it.
            "address":      str(ent.get("address-text") or ent.get("address") or "").strip(),
            "entry_date":   str(ent.get("entry-date")              or "").strip(),
            "start_date":   str(ent.get("start-date")              or "").strip(),
            "easting":      fx,
            "northing":     fy,
            "distance_m":   dist,
            "direction":    direction,
            "grid_ref":     gr,
        })

    results.sort(key=lambda r: r["distance_m"])
    return results


def load_tpo_layer(easting, northing, redline_geom=None, entities=None):
    """
    Fetch TPO points within 200 m and add them as a styled memory layer.
    Returns (ok, layer_or_reason).

    If `entities` (raw planning.data.gov.uk 'tree' entity dicts) is supplied,
    no network request is made — the parallel TPO toggle pre-fetches them on a
    worker thread so this main-thread call only builds the layer.
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsSingleSymbolRenderer, QgsMarkerSymbol,
        QgsField,
    )
    from qgis.PyQt.QtCore import QVariant

    for old in QgsProject.instance().mapLayersByName(_TPO_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    try:
        records = query_tpo_2km(easting, northing, redline_geom, entities=entities)
    except Exception as exc:
        return False, str(exc)

    # Retain a styled zero-feature point layer when no individual TPO records
    # are returned. The print-map legend can then still show the TPO location
    # symbol, making an icon-free map explicitly mean that none were found.
    no_results = not records

    lyr = QgsVectorLayer(
        "Point?crs=EPSG:27700"
        "&field=reference:string(50)"
        "&field=name:string(255)"
        "&field=tpo_ref:string(50)"
        "&field=species:string(100)"
        "&field=address:string(200)"
        "&field=entry_date:string(12)"
        "&field=start_date:string(12)"
        "&field=distance_m:integer"
        "&field=direction:string(3)"
        "&field=grid_ref:string(10)",
        _TPO_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    feats = []
    for r in records:
        f = QgsFeature(lyr.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["easting"], r["northing"])))
        f.setAttributes([
            r["reference"], r["name"], r["tpo_ref"], r["species"], r["address"],
            r["entry_date"], r["start_date"],
            r["distance_m"], r["direction"], r["grid_ref"],
        ])
        feats.append(f)
    dp.addFeatures(feats)
    lyr.updateExtents()

    # Style — green tree marker
    sym = QgsMarkerSymbol.createSimple({
        "name":          "circle",
        "color":         "#1F7A4D",
        "outline_color": "#175E3B",
        "outline_width": "0.4",
        "size":          "5",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    # Labels — reference
    from qgis.core import (
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor
    pal = QgsPalLayerSettings()
    pal.enabled      = True
    pal.isExpression = False
    pal.fieldName    = "reference"
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor("#175E3B"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    root = QgsProject.instance().layerTreeRoot()
    bm   = root.findGroup("Base Maps")
    idx  = root.children().index(bm) if bm is not None else 0
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)
    root.insertLayer(idx, lyr)

    if no_results:
        lyr.setCustomProperty("biome_empty_tpo_search", 1)
        return False, "no_results"
    return True, lyr


def remove_tpo_layer():
    """Hide the TPO layer (keep in project for instant re-display)."""
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    for name in (_TPO_LAYER_NAME, _TPO_LINES_LAYER_NAME, _TPO_ZONE_LAYER_NAME):
        for lyr in QgsProject.instance().mapLayersByName(name):
            node = root.findLayer(lyr.id())
            if node is not None:
                node.setItemVisibilityChecked(False)


def load_tpo_distance_lines(redline_geom):
    """Build distance lines from the redline to each TPO point."""
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsLineSymbol, QgsSingleSymbolRenderer,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    for old in QgsProject.instance().mapLayersByName(_TPO_LINES_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    tpo_lyrs = QgsProject.instance().mapLayersByName(_TPO_LAYER_NAME)
    if not tpo_lyrs:
        return False, "no_tpo_layer"

    tpo_lyr   = tpo_lyrs[0]
    tpo_feats = list(tpo_lyr.getFeatures())
    if not tpo_feats:
        return False, "no_features"

    if redline_geom is None or redline_geom.isEmpty():
        return False, "no_redline"

    lyr = QgsVectorLayer(
        "LineString?crs=EPSG:27700"
        "&field=reference:string(50)"
        "&field=distance_m:integer"
        "&field=distance_label:string(20)",
        _TPO_LINES_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    line_feats = []

    for feat in tpo_feats:
        tpo_geom = feat.geometry()
        if tpo_geom is None or tpo_geom.isEmpty():
            continue
        tpo_pt  = tpo_geom.asPoint()
        nearest = redline_geom.nearestPoint(tpo_geom)
        if nearest is None or nearest.isEmpty():
            nearest = redline_geom.centroid()
        near_pt = nearest.asPoint()
        dist_m  = int(round(redline_geom.distance(tpo_geom)))
        dist_label = ("On/within redline" if dist_m < 1
                      else (f"{dist_m} m" if dist_m < 1000
                            else f"{dist_m / 1000:.2f} km"))
        reference = feat["reference"] or ""
        line_geom = QgsGeometry.fromPolylineXY([
            QgsPointXY(near_pt.x(), near_pt.y()),
            QgsPointXY(tpo_pt.x(),  tpo_pt.y()),
        ])
        lf = QgsFeature(lyr.fields())
        lf.setGeometry(line_geom)
        lf.setAttributes([reference, dist_m, dist_label])
        line_feats.append(lf)

    if not line_feats:
        return False, "no_lines"

    dp.addFeatures(line_feats)
    lyr.updateExtents()

    sym = QgsLineSymbol.createSimple({
        "line_color": "#1F7A4D",
        "line_width": "0.4",
        "line_style": "dash",
        "capstyle":   "round",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    pal = QgsPalLayerSettings()
    pal.enabled        = True
    pal.fieldName      = "distance_label"
    pal.placement      = QgsPalLayerSettings.Placement.Line
    pal.placementFlags = QgsPalLayerSettings.AboveLine
    fmt = QgsTextFormat()
    fmt.setSize(6.5)
    fmt.setColor(QColor("#175E3B"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    tpo_group = _nest_desk_study_layer(tpo_lyr, lyr, _TPO_LAYER_NAME)
    # The polygon zones are part of the same TPO dataset and should not remain
    # as a separate top-level row once the source dropdown exists.
    for zone in QgsProject.instance().mapLayersByName(_TPO_ZONE_LAYER_NAME):
        zone_node = QgsProject.instance().layerTreeRoot().findLayer(zone.id())
        if zone_node is not None and zone_node.parent() is not tpo_group:
            old_parent = zone_node.parent()
            tpo_group.insertChildNode(2, zone_node.clone())
            old_parent.removeChildNode(zone_node)

    return True, lyr


def remove_tpo_distance_lines():
    from qgis.core import QgsProject
    for lyr in QgsProject.instance().mapLayersByName(_TPO_LINES_LAYER_NAME):
        QgsProject.instance().removeMapLayer(lyr.id())


# ---------------------------------------------------------------------------
# TPO zones (woodland / area / group polygons) and Conservation Areas
# Both are polygon datasets on planning.data.gov.uk queried with the same
# bbox-intersects pattern as the TPO points, but the geometry is a WKT polygon.
# ---------------------------------------------------------------------------

def _query_planning_polygons_2km(dataset, easting, northing, redline_geom,
                                 extract, radius_m=2000, entities=None):
    """
    Generic polygon query against planning.data.gov.uk entity.json.

    Fetches every entity of ``dataset`` whose geometry intersects the
    ``radius_m`` buffer of the redline edge (default 2 km), parses the WKT
    polygon (WGS84 → BNG), post-filters to the true circular buffer, and
    returns a list of record dicts.  ``extract(ent)`` supplies the
    dataset-specific attribute fields; this helper adds the common geometry
    fields: geometry_wkt (BNG), area_ha, distance_m, direction, grid_ref.
    Sorted by distance ascending.
    """
    from qgis.core import (
        QgsGeometry, QgsPointXY, QgsCoordinateTransform,
        QgsCoordinateReferenceSystem, QgsProject,
    )

    RADIUS = radius_m
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bng    = QgsCoordinateReferenceSystem("EPSG:27700")
    wgs    = QgsCoordinateReferenceSystem("EPSG:4326")
    to_bng = QgsCoordinateTransform(wgs, bng, QgsProject.instance())

    # Reuse a caller-supplied pre-fetch when available (parallel TPO toggle);
    # otherwise fetch the polygons now (report path / conservation areas).
    if entities is None:
        bbox_wkt = _planning_bbox_wkt(easting, northing, redline_geom, RADIUS)
        entities = fetch_planning_entities(dataset, bbox_wkt)

    all_entities = entities

    results = []
    for ent in all_entities:
        # Skip end-dated (revoked/historic) entities — see query_tpo_2km.
        if str(ent.get("end-date") or "").strip():
            continue
        if ent.get("_bng_geojson"):
            # Spatial Hub Scotland polygons arrive already in BNG
            geom = _geojson_rings_to_qgsgeom(ent["_bng_geojson"])
            if geom is None or geom.isEmpty():
                continue
        else:
            wkt = ent.get("geometry") or ""
            if not wkt:
                continue
            geom = QgsGeometry.fromWkt(wkt)
            if geom is None or geom.isEmpty():
                continue
            try:
                geom.transform(to_bng)
            except Exception:
                continue

        # True circular-buffer post-filter (bbox corners extend past 2 km)
        if not search_buffer.isEmpty() and not search_buffer.intersects(geom):
            continue

        c = geom.centroid().asPoint()
        fx, fy = int(round(c.x())), int(round(c.y()))
        dist, direction = _redline_distance_and_direction(redline_geom, geom, fx, fy)
        if dist > RADIUS:
            continue

        try:
            from .gridref_utils import osgb_en_to_gridref
            gr = osgb_en_to_gridref(fx, fy, 6)
        except Exception:
            gr = f"{fx},{fy}"

        rec = {
            "geometry_wkt": geom.asWkt(),
            "area_ha":      round(geom.area() / 10000.0, 2),
            "distance_m":   dist,
            "direction":    direction,
            "grid_ref":     gr,
        }
        rec.update(extract(ent))
        results.append(rec)

    results.sort(key=lambda r: r["distance_m"])
    return results



# tree-preservation-zone-type is free-text in practice: most councils publish
# group/area/woodland but a few emit internal codes ("T", "T001", "Unconfirmed")
# — only whitelisted values become a user-visible label.
_TPO_ZONE_TYPE_LABELS = {
    "group":          "Group TPO",
    "area":           "Area TPO",
    "woodland":       "Woodland TPO",
    "individual":     "Individual tree TPO",
    "individual tpo": "Individual tree TPO",
}


def query_tpo_zones_2km(easting, northing, redline_geom=None, entities=None):
    """
    TPO zones — the woodland / area / group TPO polygons that the points-only
    'tree' dataset does not contain.  Source: planning.data.gov.uk dataset
    'tree-preservation-zone'.  Searched within 200 m (see _TPO_RADIUS_M), the
    same radius as the individual TPO points.  Returns record dicts (see
    _query_planning_polygons_2km) with extra fields: reference, tpo_ref, name,
    notes, description, species, address, zone_type, entry_date, start_date.

    Field coverage varies wildly by council (2026 survey of all 82 publishers:
    zone-type 77% of entities, start-date 68%, address 8%, species 5%) — every
    field here may legitimately be empty, so consumers must treat them as
    optional extras.
    """
    def _extract(ent):
        zt_raw = str(ent.get("tree-preservation-zone-type") or "").strip()
        return {
            "reference":   str(ent.get("reference")               or "").strip(),
            "tpo_ref":     str(ent.get("tree-preservation-order") or "").strip(),
            "name":        str(ent.get("name")                    or "").strip(),
            "notes":       str(ent.get("notes")                   or "").strip(),
            "description": str(ent.get("description")             or "").strip(),
            "species":     str(ent.get("tree-species-list")       or "").strip(),
            "address":     str(ent.get("address-text")            or "").strip(),
            "zone_type":   _TPO_ZONE_TYPE_LABELS.get(zt_raw.lower(), ""),
            "entry_date":  str(ent.get("entry-date")              or "").strip(),
            "start_date":  str(ent.get("start-date")              or "").strip(),
        }
    return _query_planning_polygons_2km(
        "tree-preservation-zone", easting, northing, redline_geom, _extract,
        radius_m=_search_radius("tpo", _TPO_RADIUS_M), entities=entities)


def query_conservation_areas_2km(easting, northing, redline_geom=None):
    """
    Conservation areas within 2 km.  Trees in a conservation area are protected
    (six weeks' written notice required before works), so this is a related
    tree-protection constraint.  Source: planning.data.gov.uk dataset
    'conservation-area'.  Extra fields: reference, name, entry_date.
    """
    def _extract(ent):
        return {
            "reference":  str(ent.get("reference")  or "").strip(),
            "name":       str(ent.get("name")       or "").strip(),
            "entry_date": str(ent.get("entry-date") or "").strip(),
        }
    return _query_planning_polygons_2km(
        "conservation-area", easting, northing, redline_geom, _extract,
        radius_m=_search_radius("conservation_areas"))


def load_tpo_zones_layer(easting, northing, redline_geom=None, entities=None):
    """
    Fetch TPO zone polygons within 200 m and add them as a styled memory layer.
    Returns (ok, layer_or_reason); reason 'no_results' when none found.

    If `entities` (raw 'tree-preservation-zone' entity dicts) is supplied, no
    network request is made — see load_tpo_layer.
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsProject,
        QgsFillSymbol, QgsSingleSymbolRenderer,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    for old in QgsProject.instance().mapLayersByName(_TPO_ZONE_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    try:
        records = query_tpo_zones_2km(easting, northing, redline_geom, entities=entities)
    except Exception as exc:
        return False, str(exc)
    if not records:
        return False, "no_results"

    lyr = QgsVectorLayer(
        "MultiPolygon?crs=EPSG:27700"
        "&field=reference:string(50)"
        "&field=tpo_ref:string(80)"
        "&field=name:string(255)"
        "&field=notes:string(255)"
        "&field=zone_type:string(30)"
        "&field=species:string(254)"
        "&field=address:string(254)"
        "&field=area_ha:double"
        "&field=entry_date:string(12)"
        "&field=start_date:string(12)"
        "&field=distance_m:integer"
        "&field=direction:string(3)"
        "&field=grid_ref:string(10)",
        _TPO_ZONE_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    feats = []
    for r in records:
        g = QgsGeometry.fromWkt(r["geometry_wkt"])
        if g is None or g.isEmpty():
            continue
        f = QgsFeature(lyr.fields())
        f.setGeometry(g)
        f.setAttributes([
            r["reference"], r["tpo_ref"], r["name"], r["notes"],
            r["zone_type"], r["species"], r["address"],
            r["area_ha"], r["entry_date"], r["start_date"],
            r["distance_m"], r["direction"], r["grid_ref"],
        ])
        feats.append(f)
    dp.addFeatures(feats)
    lyr.updateExtents()

    sym = QgsFillSymbol.createSimple({
        "color":         "52,154,77,60",     # semi-transparent green fill
        "outline_color": "#175E3B",
        "outline_width": "0.5",
        "style":         "solid",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    pal = QgsPalLayerSettings()
    pal.enabled      = True
    pal.isExpression = False
    pal.fieldName    = "tpo_ref"
    fmt = QgsTextFormat(); fmt.setSize(7); fmt.setColor(QColor("#0E3D26"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf); pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    root = QgsProject.instance().layerTreeRoot()
    bm   = root.findGroup("Base Maps")
    idx  = root.children().index(bm) if bm is not None else 0
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)
    root.insertLayer(idx, lyr)
    return True, lyr


def load_conservation_areas_layer(easting, northing, redline_geom=None):
    """
    Fetch conservation-area polygons within 2 km as a styled memory layer.
    Returns (ok, layer_or_reason); reason 'no_results' when none found.
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsProject,
        QgsFillSymbol, QgsSingleSymbolRenderer,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    for old in QgsProject.instance().mapLayersByName(_CONSERVATION_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    try:
        records = query_conservation_areas_2km(easting, northing, redline_geom)
    except Exception as exc:
        return False, str(exc)
    if not records:
        return False, "no_results"

    lyr = QgsVectorLayer(
        "MultiPolygon?crs=EPSG:27700"
        "&field=reference:string(50)"
        "&field=name:string(255)"
        "&field=area_ha:double"
        "&field=entry_date:string(12)"
        "&field=distance_m:integer"
        "&field=direction:string(3)"
        "&field=grid_ref:string(10)",
        _CONSERVATION_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    feats = []
    for r in records:
        g = QgsGeometry.fromWkt(r["geometry_wkt"])
        if g is None or g.isEmpty():
            continue
        f = QgsFeature(lyr.fields())
        f.setGeometry(g)
        f.setAttributes([
            r["reference"], r["name"], r["area_ha"], r["entry_date"],
            r["distance_m"], r["direction"], r["grid_ref"],
        ])
        feats.append(f)
    dp.addFeatures(feats)
    lyr.updateExtents()

    sym = QgsFillSymbol.createSimple({
        "color":         "154,103,0,45",    # transparent amber fill
        "outline_color": "#9A6700",
        "outline_width": "0.6",
        "outline_style": "dash",
        "style":         "solid",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    pal = QgsPalLayerSettings()
    pal.enabled      = True
    pal.isExpression = False
    pal.fieldName    = "name"
    fmt = QgsTextFormat(); fmt.setSize(7); fmt.setColor(QColor("#6E4A00"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf); pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    root = QgsProject.instance().layerTreeRoot()
    bm   = root.findGroup("Base Maps")
    idx  = root.children().index(bm) if bm is not None else 0
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)
    root.insertLayer(idx, lyr)
    return True, lyr


def remove_conservation_areas_layer():
    """Hide the conservation-areas layer (keep in project for re-display)."""
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    for lyr in QgsProject.instance().mapLayersByName(_CONSERVATION_LAYER_NAME):
        node = root.findLayer(lyr.id())
        if node is not None:
            node.setItemVisibilityChecked(False)



# ---------------------------------------------------------------------------
# Standing Water Bodies — 500m from redline edge
# ---------------------------------------------------------------------------
# Uses the OS Features API (WFS) with the Zoomstack_Surfacewater typeName.
# This is OS open data — free tier, no API key required.
# It maps every surface water polygon OS has recorded, including all ponds.
# ---------------------------------------------------------------------------

_SW_LAYER_NAME = "Mapped Water-body Outlines"
_SW_LEGACY_LAYER_NAMES = ("Standing Water Bodies",)
_SW_GPKG_TABLE = "known_water_outlines"
_SW_WFS_URL    = "https://api.os.uk/features/v1/wfs"
_SW_TYPENAME   = "Zoomstack_Surfacewater"
_SW_NGD_URL    = ("https://api.os.uk/features/ngd/ofa/v1/collections/"
                  "wtr-fts-water-3/items")
_SW_RESPONSE_CACHE = {}


def load_standing_water_layer(easting, northing, redline_geom=None, api_key="",
                              ngd_api_key=""):
    """Fetch and display all surface water polygons within 500m of the redline edge.

    OS NGD is used when its separate optional key is configured.  The existing
    free OS, OSM, PHI and HabMoS sources always remain available.

    Returns (True, layer) on success, (False, reason_str) on failure.
    """
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
        QgsPointXY, QgsSingleSymbolRenderer, QgsSymbol,
        QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    )
    from qgis.PyQt.QtGui import QColor

    RADIUS = _search_radius("standing_water", 500)

    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bb = search_buffer.boundingBox()

    lyr = QgsVectorLayer(
        "Polygon?crs=EPSG:27700&field=source:string(20)&field=type:string(80)",
        _SW_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    qfeats = []
    seen_geoms = []  # higher-priority sources win where polygons overlap

    def _is_duplicate(qg):
        """Treat substantially coincident polygons as the same water body.

        Compare against the smaller polygon, rather than only the incoming
        one, so source order and small differences in shoreline capture do not
        affect de-duplication.
        """
        try:
            qarea = qg.area()
            if qarea <= 0:
                return False
            for existing in seen_geoms:
                earea = existing.area()
                smaller = min(qarea, earea)
                if smaller > 0 and qg.intersection(existing).area() > smaller * 0.5:
                    return True
        except Exception:
            return False
        return False

    # ── 1. OS Features API ────────────────────────────────────────────────────
    bbox_os = (
        f"{bb.xMinimum()},{bb.yMinimum()},"
        f"{bb.xMaximum()},{bb.yMaximum()},"
        f"EPSG:27700"
    )
    base_url = f"{_SW_WFS_URL}?key={api_key}" if api_key else _SW_WFS_URL
    sep      = "&" if api_key else "?"
    os_url   = base_url + sep + urlencode({
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    _SW_TYPENAME,
        "BBOX":         bbox_os,
        "srsName":      "EPSG:27700",
        "outputFormat": "geojson",
        "count":        "500",
    })

    # Prepare the compact OSM query up front. ``out geom`` embeds coordinates
    # directly in each way and avoids downloading a second, often much larger,
    # recursive set of node records.
    crs_bng = QgsCoordinateReferenceSystem("EPSG:27700")
    crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    xform = QgsCoordinateTransform(crs_bng, crs_wgs, QgsProject.instance())
    sw = xform.transform(QgsPointXY(bb.xMinimum(), bb.yMinimum()))
    ne = xform.transform(QgsPointXY(bb.xMaximum(), bb.yMaximum()))
    ob = f"({sw.y()},{sw.x()},{ne.y()},{ne.x()})"

    # NGD's bounding box defaults to CRS84 (longitude, latitude).  Request the
    # returned polygons in BNG so they can be merged without reprojection loss.
    ngd_descriptions = (
        "'Buried Open Reservoir'", "'Buried Open Reservoir With Solar Panels'",
        "'Buried Open Water Tank'", "'Cooling Pond'", "'Dew Pond'", "'Moat'",
        "'Open Reservoir'", "'Open Reservoir And Reeds'",
        "'Open Reservoir With Solar Panels'", "'Open Tank Reservoir'",
        "'Open Tank Reservoir With Solar Panels'", "'Open Water Tank'",
        "'Oyster Pit'", "'Paddling Pool'", "'Settling Pond'",
        "'Static Water As Source Of Watercourse'", "'Still Water'",
        "'Still Water With Solar Panels'", "'Swimming Pool'",
    )
    ngd_params = {
        "bbox": f"{sw.x()},{sw.y()},{ne.x()},{ne.y()}",
        "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
        "limit": "100",
        # Filtering before pagination avoids paying to retrieve nearby river
        # pieces that this standing-water tool would immediately discard.
        "filter": "description IN (" + ",".join(ngd_descriptions) + ")",
    }
    overpass_query = (
        f"[out:json][timeout:10];("
        f'way["natural"="water"]{ob};'
        f'way["water"~"pond|lake|reservoir|basin"]{ob};'
        f");out tags geom;"
    )
    osm_req = Request(
        "https://overpass-api.de/api/interpreter",
        data=urlencode({"data": overpass_query}).encode(),
        headers={"User-Agent": "BiomeMapper/1.0"},
    )
    priority_url = _PHI_BASE + "?" + urlencode({
        "where": (
            "MainHabs LIKE '%Ponds%' OR MainHabs LIKE '%Lakes%' "
            "OR (MainHabs = 'No main habitat but additional habitats present' "
            "AND AddHabs = 'PONDS')"),
        "geometry": (
            f"{bb.xMinimum()},{bb.yMinimum()},"
            f"{bb.xMaximum()},{bb.yMaximum()}"),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "27700",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "MainHabs,HabCodes,FeatDesc",
        "returnGeometry": "true",
        "outSR": "27700",
        "resultRecordCount": "2000",
        "f": "json",
    })

    # Network requests are independent. Running them together caps the normal
    # wait at the slower source instead of OS timeout + Overpass timeout.
    # Include non-reversible in-process key fingerprints so replacing a bad key
    # retries the source, without retaining the credential in the cache key.
    cache_key = (hash(api_key), hash(ngd_api_key)) + tuple(round(v, 1) for v in (
        bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()))
    fetched = _SW_RESPONSE_CACHE.get(cache_key)
    if fetched is None:
        fetched = {}

        def _fetch_json(request):
            with urlopen(request, timeout=12) as response:
                return json.loads(response.read().decode("utf-8"))

        def _fetch_ngd_pages():
            """Fetch all matching pages, capped defensively at 1,000 bodies."""
            combined = {"type": "FeatureCollection", "features": []}
            offset = 0
            while offset < 1000:
                page_params = dict(ngd_params)
                page_params["offset"] = str(offset)
                req = Request(_SW_NGD_URL + "?" + urlencode(page_params), headers={
                    "key": ngd_api_key,
                    "Accept": "application/geo+json",
                    "User-Agent": "BiomeMapper/1.0",
                })
                page = _fetch_json(req)
                features = page.get("features", [])
                combined["features"].extend(features)
                if len(features) < 100:
                    break
                offset += 100
            return combined

        requests = {
            "os": Request(os_url, headers={"User-Agent": "BiomeMapper/1.0"}),
            "osm": osm_req,
            "priority": Request(
                priority_url, headers={"User-Agent": "BiomeMapper/1.0"}),
        }
        with ThreadPoolExecutor(max_workers=len(requests) + bool(ngd_api_key)) as pool:
            pending = {pool.submit(_fetch_json, request): name
                       for name, request in requests.items()}
            if ngd_api_key:
                pending[pool.submit(_fetch_ngd_pages)] = "ngd"
            for future in as_completed(pending):
                try:
                    fetched[pending[future]] = future.result()
                except Exception:
                    pass
        if fetched:
            _SW_RESPONSE_CACHE[cache_key] = fetched

    def _rings(poly):
        return [[QgsPointXY(c[0], c[1]) for c in ring] for ring in poly]

    def _polygon_geometry(geom):
        coords = geom.get("coordinates", [])
        if not coords:
            return None
        if geom.get("type") == "Polygon":
            return QgsGeometry.fromPolygonXY(_rings(coords))
        if geom.get("type") == "MultiPolygon":
            return QgsGeometry.fromMultiPolygonXY([_rings(p) for p in coords])
        return None

    # ── 1. Premium OS NGD standing-water polygons (optional) ────────────────
    # Water v3 also contains rivers and other flowing water.  Keep only the
    # descriptions which represent discrete standing-water bodies.
    ngd_standing_types = {
        "buried open reservoir", "buried open reservoir with solar panels",
        "buried open water tank", "cooling pond", "dew pond", "moat",
        "open reservoir", "open reservoir and reeds",
        "open reservoir with solar panels", "open tank reservoir",
        "open tank reservoir with solar panels", "open water tank",
        "oyster pit", "paddling pool", "settling pond",
        "static water as source of watercourse", "still water",
        "still water with solar panels", "swimming pool",
    }
    try:
        for feat in fetched.get("ngd", {}).get("features", []):
            props = feat.get("properties", {}) or {}
            description = str(props.get("description") or "").strip()
            if description.lower() not in ngd_standing_types:
                continue
            qg = _polygon_geometry(feat.get("geometry", {}) or {})
            if (qg is None or qg.isEmpty() or
                    not search_buffer.intersects(qg) or _is_duplicate(qg)):
                continue
            qf = QgsFeature(lyr.fields())
            qf.setGeometry(qg)
            qf.setAttributes(["OS NGD", description[:80]])
            qfeats.append(qf)
            seen_geoms.append(qg)
    except Exception:
        pass  # Invalid/missing NGD access must never break the free search

    # ── 2. OS Open Zoomstack surface water ──────────────────────────────────
    try:
        os_data = fetched.get("os", {})
        for feat in os_data.get("features", []):
            geom   = feat.get("geometry", {})
            props  = feat.get("properties", {})
            gtype  = geom.get("type", "")
            coords = geom.get("coordinates", [])
            if not coords:
                continue

            qg = _polygon_geometry(geom)
            if qg is None:
                continue

            if (qg.isEmpty() or not search_buffer.intersects(qg)
                    or _is_duplicate(qg)):
                continue

            qf = QgsFeature(lyr.fields())
            qf.setGeometry(qg)
            qf.setAttributes(["OS", str(props.get("type", "") or "")])
            qfeats.append(qf)
            seen_geoms.append(qg)
    except Exception:
        pass  # OS query failed — continue with OSM only

    # ── 3. OSM Overpass API — catches small ponds OS misses ─────────────────
    try:
        osm_data = fetched.get("osm", {})
        xform_rev = QgsCoordinateTransform(crs_wgs, crs_bng, QgsProject.instance())

        for elem in osm_data.get("elements", []):
            if elem["type"] != "way":
                continue
            geometry = elem.get("geometry", [])
            if len(geometry) < 4:
                continue
            try:
                pts_bng = [xform_rev.transform(
                    QgsPointXY(point["lon"], point["lat"]))
                    for point in geometry]
            except Exception:
                continue

            qg = QgsGeometry.fromPolygonXY([pts_bng])
            if qg.isEmpty() or not search_buffer.intersects(qg):
                continue

            # Skip if substantially overlaps an existing OS feature
            if _is_duplicate(qg):
                continue

            tags       = elem.get("tags", {})
            water_type = tags.get("water", tags.get("natural", "water"))
            qf = QgsFeature(lyr.fields())
            qf.setGeometry(qg)
            qf.setAttributes(["OSM", water_type])
            qfeats.append(qf)
            seen_geoms.append(qg)

    except Exception:
        pass  # OSM query failed — use OS data only

    # ── 4. Natural England PHI pond/lake polygons (England, free) ────────────
    # These are the same polygon classes available under the Priority Habitats
    # Wetland & Fen button, reused here rather than adding surveyed pond points
    # or creating a duplicate project layer.
    if _region.for_point(easting, northing) != "scotland":
        try:
            for item in fetched.get("priority", {}).get("features", []):
                qg = _esri_rings_to_qgsgeom(
                    item.get("geometry", {}) or {})
                if qg.isEmpty() or not search_buffer.intersects(qg):
                    continue
                if _is_duplicate(qg):
                    continue
                attrs = item.get("attributes", {}) or {}
                water_type = str(attrs.get("MainHabs") or "Priority pond")
                qf = QgsFeature(lyr.fields())
                qf.setGeometry(qg)
                qf.setAttributes(["Natural England PHI", water_type[:80]])
                qfeats.append(qf)
                seen_geoms.append(qg)
        except Exception:
            pass

    # ── 5. Scotland: Habitat Map of Scotland standing waters (EUNIS C1) ─────
    # NatureScot's freshwater polygons — lochs, lochans and ponds — as a third
    # source, de-duplicated against whatever OS / OSM already supplied.
    if _region.for_point(easting, northing) == "scotland":
        try:
            cql = (f"BBOX(the_geom,{bb.xMinimum()},{bb.yMinimum()},"
                   f"{bb.xMaximum()},{bb.yMaximum()},'EPSG:27700') "
                   f"AND HABITAT_CODE LIKE 'C1%'")
            hq = urlencode({
                "service":      "WFS",
                "version":      "2.0.0",
                "request":      "GetFeature",
                "typeNames":    _HABMOS_TYPENAME,
                "outputFormat": "application/json",
                "srsName":      "EPSG:27700",
                "count":        "2000",
                "cql_filter":   cql,
            })
            hdata = json.loads(urlopen(f"{_NS_WFS_HS}?{hq}", timeout=15)
                               .read().decode("utf-8"))

            def _hrings(poly):
                return [[QgsPointXY(c[0], c[1]) for c in ring] for ring in poly]

            for feat in hdata.get("features", []):
                geom   = feat.get("geometry", {}) or {}
                props  = feat.get("properties", {}) or {}
                gtype  = geom.get("type", "")
                coords = geom.get("coordinates", [])
                if not coords:
                    continue
                if gtype == "Polygon":
                    qg = QgsGeometry.fromPolygonXY(_hrings(coords))
                elif gtype == "MultiPolygon":
                    qg = QgsGeometry.fromMultiPolygonXY([_hrings(p) for p in coords])
                else:
                    continue
                if qg.isEmpty() or not search_buffer.intersects(qg):
                    continue
                if _is_duplicate(qg):
                    continue
                hab = str(props.get("HABITAT_NAME") or "Standing water")[:80]
                qf = QgsFeature(lyr.fields())
                qf.setGeometry(qg)
                qf.setAttributes(["HabMoS", hab])
                qfeats.append(qf)
                seen_geoms.append(qg)
        except Exception:
            pass  # HabMoS query failed — OS / OSM data only

    if not qfeats:
        return False, "no_features"

    dp.addFeatures(qfeats)
    lyr.updateExtents()

    _style_standing_water_layer(lyr)

    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    lyr.setCustomProperty("biome_scratch", "1")
    lyr.setCustomProperty("biome_gpkg_table", _SW_GPKG_TABLE)
    project = QgsProject.instance()
    project.addMapLayer(lyr, False)
    root = project.layerTreeRoot()
    from .pond_mapper import pond_group
    pond_group(root, create=True).insertLayer(0, lyr)

    return True, lyr


def _style_standing_water_layer(layer):
    """Apply the standard mapped-water polygon style."""
    from qgis.core import QgsSingleSymbolRenderer, QgsSymbol
    from qgis.PyQt.QtGui import QColor
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    sl = symbol.symbolLayer(0)
    sl.setColor(QColor(100, 180, 255, 160))
    sl.setStrokeColor(QColor(30, 100, 200, 255))
    sl.setStrokeWidth(0.4)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def organise_standing_water_layers():
    """Move legacy/root water polygons into the shared standing-water group."""
    from qgis.core import QgsProject, QgsWkbTypes
    from .pond_mapper import pond_group

    project = QgsProject.instance()
    root = project.layerTreeRoot()
    group = pond_group(root, create=False)
    candidates = []
    for layer in project.mapLayers().values():
        if (QgsWkbTypes.geometryType(layer.wkbType())
                != QgsWkbTypes.GeometryType.PolygonGeometry):
            continue
        if (layer.name() == _SW_LAYER_NAME
                or layer.name() in _SW_LEGACY_LAYER_NAMES
                or str(layer.customProperty("biome_gpkg_table", ""))
                == _SW_GPKG_TABLE):
            candidates.append(layer)
    if not candidates:
        return None
    if group is None:
        group = pond_group(root, create=True)
    for layer in candidates:
        layer.setName(_SW_LAYER_NAME)
        layer.setCustomProperty("biome_gpkg_table", _SW_GPKG_TABLE)
        if layer.providerType() == "memory":
            layer.setCustomProperty("skipMemoryLayersCheck", 1)
            layer.setCustomProperty("biome_scratch", "1")
        node = root.findLayer(layer.id())
        if node is not None and node.parent() is not group:
            visible = node.isVisible()
            node.parent().removeChildNode(node)
            new_node = group.addLayer(layer)
            new_node.setItemVisibilityChecked(visible)
    return candidates[0]


def recover_persisted_standing_water_layer(gpkg_path):
    """Reconnect saved mapped water outlines without making another API call."""
    import os
    from qgis.core import QgsDataProvider, QgsProject, QgsVectorLayer
    from .pond_mapper import pond_group

    existing = organise_standing_water_layers()
    if existing is not None and existing.isValid() and existing.featureCount() > 0:
        return existing
    if not gpkg_path or not os.path.isfile(gpkg_path):
        return existing
    uri = f"{gpkg_path}|layername={_SW_GPKG_TABLE}"
    probe = QgsVectorLayer(uri, _SW_LAYER_NAME, "ogr")
    if not probe.isValid():
        return existing
    project = QgsProject.instance()
    layer = existing
    if layer is not None and layer.providerType() == "memory":
        layer.setDataSource(uri, _SW_LAYER_NAME, "ogr",
                            QgsDataProvider.ProviderOptions())
        if not layer.isValid():
            layer = None
    if layer is None:
        layer = probe
        project.addMapLayer(layer, False)
        pond_group(project.layerTreeRoot(), create=True).insertLayer(0, layer)
    layer.setName(_SW_LAYER_NAME)
    layer.removeCustomProperty("biome_scratch")
    layer.removeCustomProperty("skipMemoryLayersCheck")
    layer.setCustomProperty("biome_gpkg_table", _SW_GPKG_TABLE)
    _style_standing_water_layer(layer)
    organise_standing_water_layers()
    return layer


def remove_standing_water_layer():
    """Hide the standing water layer (keep in project for instant re-display)."""
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    names = (_SW_LAYER_NAME,) + _SW_LEGACY_LAYER_NAMES
    for name in names:
        for lyr in QgsProject.instance().mapLayersByName(name):
            node = root.findLayer(lyr.id())
            if node is not None:
                node.setItemVisibilityChecked(False)


# ---------------------------------------------------------------------------
# Local Wildlife Sites (LWS) — manually entered, stored in QGIS memory layer
# ---------------------------------------------------------------------------
# LWS data is not available online. The user enters:
#   - Site name
#   - Grid reference (BNG, e.g. "TQ 443 806")
#   - Area (ha)
# The tool converts the grid ref to a point, places it on the map, computes
# direction & distance from the redline, and generates a table for report use.
# ---------------------------------------------------------------------------

_LWS_LAYER_NAME  = "Local Wildlife Sites"
_LWS_IMPORTED_LAYER_NAME = "Local Wildlife Sites (Imported)"
_LWS_COLOUR      = "#00897b"   # teal-green

_LWS_DESIGNATION_COLOURS = {
    "Site of Metropolitan Importance": "#5B2C83",
    "Site of Borough Importance, Grade I": "#814AA5",
    "Site of Borough Importance, Grade II": "#8171BD",
    "Site of Local Importance": "#14887A",
}


def apply_imported_lws_style(layer):
    """Style imported LWS polygons by site name for a useful map legend."""
    from qgis.core import (
        QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsRendererCategory,
    )
    if layer is None or not layer.isValid():
        return
    sites = {}
    for feat in layer.getFeatures():
        name = str(feat["name"] or "").strip()
        designation = str(feat["designation"] or "").strip()
        key = name or designation or "Local Wildlife Site"
        sites.setdefault(key, (designation, bool(name)))
    categories = []
    for site_name, (designation, has_name) in sorted(sites.items()):
        colour = _LWS_DESIGNATION_COLOURS.get(designation, _LWS_COLOUR)
        symbol = QgsFillSymbol.createSimple({
            "color": "0,0,0,0",
            "style": "no",
            "outline_color": colour,
            "outline_width": "0.85",
        })
        # Imported datasets normally provide a name. For a genuinely unnamed
        # record the category value remains blank but its descriptive fallback
        # is still shown in the key.
        value = site_name if has_name else ""
        categories.append(QgsRendererCategory(value, symbol, site_name))
    layer.setRenderer(QgsCategorizedSymbolRenderer("name", categories))
    layer.triggerRepaint()


def import_lws_file(path, redline_geom=None, within_2km=True):
    """Copy polygon LWS data into a project-owned layer."""
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature, QgsField, QgsGeometry,
        QgsCoordinateReferenceSystem, QgsCoordinateTransform,
        QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsFillSymbol,
        QgsWkbTypes, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtCore import QVariant
    from qgis.PyQt.QtGui import QColor
    source = QgsVectorLayer(path, "LWS import", "ogr")
    if not source.isValid():
        return False, "The selected file could not be opened as a spatial dataset."
    if QgsWkbTypes.geometryType(source.wkbType()) != QgsWkbTypes.GeometryType.PolygonGeometry:
        return False, "The selected layer does not contain polygon boundaries."
    target_crs = QgsCoordinateReferenceSystem("EPSG:27700")
    transform = None
    if source.crs().isValid() and source.crs() != target_crs:
        transform = QgsCoordinateTransform(source.crs(), target_crs,
            QgsProject.instance().transformContext())
    search_geom = None
    if within_2km:
        if redline_geom is None or redline_geom.isEmpty():
            return False, "A site boundary is required to filter Local Wildlife Sites within 2 km."
        search_geom = redline_geom.buffer(2000.0, 48)
    for old in QgsProject.instance().mapLayersByName(_LWS_IMPORTED_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())
    layer = QgsVectorLayer("MultiPolygon?crs=EPSG:27700", _LWS_IMPORTED_LAYER_NAME, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([
        QgsField("site_ref", QVariant.String, len=100),
        QgsField("name", QVariant.String, len=250),
        QgsField("designation", QVariant.String, len=250),
        QgsField("status", QVariant.String, len=100),
        QgsField("area_ha", QVariant.Double),
        QgsField("notification", QVariant.String, len=50),
        QgsField("citation_update", QVariant.String, len=50),
        QgsField("ownership", QVariant.String, len=500),
        QgsField("summary", QVariant.String, len=4000),
        QgsField("habitats", QVariant.String, len=4000),
        QgsField("feature_notes", QVariant.String, len=4000),
        QgsField("source_file", QVariant.String, len=500),
    ])
    layer.updateFields()
    source_names = {f.name().lower().replace("_", ""): f.name() for f in source.fields()}
    def value(feat, *candidates):
        for candidate in candidates:
            actual = source_names.get(candidate.lower().replace("_", ""))
            if actual:
                val = feat[actual]
                return "" if val is None else val
        return ""
    def text_value(feat, *candidates):
        """Return provider values as stable text (not PyQt object reprs)."""
        val = value(feat, *candidates)
        if hasattr(val, "toString"):
            try:
                return val.toString("yyyy-MM-dd")
            except TypeError:
                return val.toString()
        return str(val)
    copied, excluded = [], 0
    for src in source.getFeatures():
        geom = QgsGeometry(src.geometry())
        if geom.isNull() or geom.isEmpty():
            continue
        if transform is not None:
            try: geom.transform(transform)
            except Exception: continue
        if search_geom is not None and not geom.intersects(search_geom):
            excluded += 1
            continue
        if QgsWkbTypes.isSingleType(geom.wkbType()):
            geom.convertToMultiType()
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttributes([
            str(value(src, "SiteReference", "site_ref", "reference")),
            str(value(src, "Name", "SiteName", "site_name")),
            str(value(src, "SiteDesignation", "designation")),
            str(value(src, "SiteStatus", "status")),
            float(value(src, "AreaHA", "area_ha", "area") or 0),
            text_value(src, "NotificationDate", "notification"),
            text_value(src, "CitationLastUpdateDate", "citation_update"),
            str(value(src, "Ownership", "owner")),
            str(value(src, "SiteSummary", "summary", "citation")),
            str(value(src, "Habitats", "habitat")),
            str(value(src, "FeatureNotes", "notes")), str(path),
        ])
        copied.append(feat)
    if not copied:
        return False, "No Local Wildlife Site boundaries were found within the selected extent."
    added_ok, added_features = provider.addFeatures(copied)
    layer.updateExtents()
    if not added_ok or layer.featureCount() == 0:
        errors = "; ".join(provider.errors())
        detail = f" ({errors})" if errors else ""
        return False, "The Local Wildlife Site boundaries could not be added to the map" + detail
    layer.setCustomProperty("skipMemoryLayersCheck", 1)
    layer.setCustomProperty("biome_scratch", 1)
    layer.setCustomProperty("biome_gpkg_table", "local_wildlife_sites_imported")
    layer.setCustomProperty("biome_lws_import", 1)
    layer.setCustomProperty("biome_lws_filter", "2km" if within_2km else "all")
    layer.setCustomProperty("biome_lws_source", str(path))
    apply_imported_lws_style(layer)
    labels = QgsPalLayerSettings()
    labels.enabled = True
    labels.fieldName = "name"
    labels.placement = QgsPalLayerSettings.Placement.OverPoint
    labels.centroidInside = True
    text_format = QgsTextFormat()
    text_format.setSize(8)
    text_format.setColor(QColor("#17352f"))
    text_buffer = QgsTextBufferSettings()
    text_buffer.setEnabled(True)
    text_buffer.setSize(1.0)
    text_buffer.setColor(QColor("white"))
    text_format.setBuffer(text_buffer)
    labels.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(labels))
    layer.setLabelsEnabled(True)
    QgsProject.instance().addMapLayer(layer, False)
    root = QgsProject.instance().layerTreeRoot()
    base = root.findGroup("Base Maps")
    idx = root.children().index(base) if base is not None else 0
    root.insertLayer(idx, layer)
    QgsProject.instance().setDirty(True)
    return True, {"layer": layer, "included": len(copied), "excluded": excluded}


def _lws_layer_or_create():
    """Return (or create) the LWS memory layer."""
    from qgis.core import QgsProject, QgsVectorLayer
    existing = QgsProject.instance().mapLayersByName(_LWS_LAYER_NAME)
    if existing:
        return existing[0]

    lyr = QgsVectorLayer(
        "Point?crs=EPSG:27700"
        "&field=name:string(200)"
        "&field=area_ha:double"
        "&field=grid_ref:string(20)",
        _LWS_LAYER_NAME, "memory"
    )
    _style_lws_layer(lyr)
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)

    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    bm   = root.findGroup("Base Maps")
    idx  = root.children().index(bm) if bm is not None else 0
    root.insertLayer(idx, lyr)

    return lyr


def _style_lws_layer(lyr):
    """Apply styled marker + labels to the LWS layer."""
    from qgis.core import (
        QgsMarkerSymbol, QgsSingleSymbolRenderer,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    sym = QgsMarkerSymbol.createSimple({
        "name":          "diamond",
        "color":         _LWS_COLOUR,
        "outline_color": "#004d40",
        "outline_width": "0.5",
        "size":          "6",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    pal = QgsPalLayerSettings()
    pal.enabled   = True
    pal.fieldName = "name"
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor(_LWS_COLOUR))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)


def add_lws_site(name, grid_ref, area_ha):
    """
    Add a single LWS site to the memory layer.

    Args:
        name:     str  — site name
        grid_ref: str  — BNG grid reference e.g. "TQ 443 806"
        area_ha:  float — area in hectares

    Returns:
        (True, feature_id) on success, (False, error_str) on failure.
    """
    from .gridref_utils import gridref_to_osgb_en, GridRefError
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

    try:
        e, n = gridref_to_osgb_en(grid_ref)
    except GridRefError as exc:
        return False, str(exc)

    lyr = _lws_layer_or_create()
    dp  = lyr.dataProvider()

    feat = QgsFeature(lyr.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(e, n)))
    feat.setAttributes([name.strip(), float(area_ha), grid_ref.strip().upper()])
    ok, new_feats = dp.addFeatures([feat])
    lyr.updateExtents()
    lyr.triggerRepaint()

    if ok and new_feats:
        return True, new_feats[0].id()
    return False, "Failed to add feature"


def remove_lws_site(feature_id):
    """Remove a single LWS site by its feature id."""
    from qgis.core import QgsProject
    existing = QgsProject.instance().mapLayersByName(_LWS_LAYER_NAME)
    if not existing:
        return
    lyr = existing[0]
    lyr.dataProvider().deleteFeatures([feature_id])
    lyr.updateExtents()
    lyr.triggerRepaint()


def clear_lws_sites():
    """Remove all LWS sites (and the layer itself)."""
    from qgis.core import QgsProject
    for lyr in QgsProject.instance().mapLayersByName(_LWS_LAYER_NAME):
        QgsProject.instance().removeMapLayer(lyr.id())
    for lyr in QgsProject.instance().mapLayersByName(_LWS_IMPORTED_LAYER_NAME):
        QgsProject.instance().removeMapLayer(lyr.id())


def get_lws_sites_with_distances(redline_geom=None):
    """
    Return all stored LWS sites with distance and direction from the redline.

    Returns a list of dicts:
        {
          'name':       str
          'grid_ref':   str
          'area_ha':    float
          'distance_m': int   — 0 if site centroid is within/on redline
          'direction':  str   — 8-point cardinal
        }
    Sorted by distance ascending.
    """
    from qgis.core import QgsProject, QgsWkbTypes

    existing = (QgsProject.instance().mapLayersByName(_LWS_LAYER_NAME)
                + QgsProject.instance().mapLayersByName(_LWS_IMPORTED_LAYER_NAME))
    if not existing:
        return []

    feats = [feat for lyr in existing for feat in lyr.getFeatures()]
    if not feats:
        return []

    results = []
    for feat in feats:
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue

        pt_geom = (geom if QgsWkbTypes.geometryType(geom.wkbType()) ==
                   QgsWkbTypes.GeometryType.PointGeometry else geom.centroid())
        pt = pt_geom.asPoint()
        dist, direction = _redline_distance_and_direction(
            redline_geom, geom, pt.x(), pt.y()
        )
        results.append({
            "name":       str(feat["name"] or "").strip(),
            "grid_ref":   (str(feat["grid_ref"] or "").strip()
                           if feat.fields().indexOf("grid_ref") >= 0 else ""),
            "area_ha":    float(feat["area_ha"] or 0),
            "distance_m": dist,
            "direction":  direction,
            "feature_id": feat.id(),
        })

    results.sort(key=lambda r: r["distance_m"])
    return results


def load_lws_distance_lines(redline_geom):
    """Build dashed distance lines from the redline to each LWS point."""
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsLineSymbol, QgsSingleSymbolRenderer,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    _LINES_NAME = "LWS Distance Lines"

    for old in QgsProject.instance().mapLayersByName(_LINES_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    lws_lyrs = QgsProject.instance().mapLayersByName(_LWS_LAYER_NAME)
    if not lws_lyrs:
        return False, "no_lws_layer"

    lws_lyr  = lws_lyrs[0]
    lws_feats = list(lws_lyr.getFeatures())
    if not lws_feats:
        return False, "no_features"

    if redline_geom is None or redline_geom.isEmpty():
        return False, "no_redline"

    lyr = QgsVectorLayer(
        "LineString?crs=EPSG:27700"
        "&field=name:string(200)"
        "&field=distance_m:integer"
        "&field=distance_label:string(20)",
        _LINES_NAME, "memory"
    )
    dp = lyr.dataProvider()
    line_feats = []

    for feat in lws_feats:
        lws_geom = feat.geometry()
        if lws_geom is None or lws_geom.isEmpty():
            continue
        lws_pt  = lws_geom.asPoint()
        nearest = redline_geom.nearestPoint(lws_geom)
        if nearest is None or nearest.isEmpty():
            nearest = redline_geom.centroid()
        near_pt = nearest.asPoint()
        dist_m  = int(round(redline_geom.distance(lws_geom)))
        dist_label = ("On/within redline" if dist_m < 1
                      else (f"{dist_m} m" if dist_m < 1000
                            else f"{dist_m / 1000:.2f} km"))
        line_geom = QgsGeometry.fromPolylineXY([
            QgsPointXY(near_pt.x(), near_pt.y()),
            QgsPointXY(lws_pt.x(),  lws_pt.y()),
        ])
        lf = QgsFeature(lyr.fields())
        lf.setGeometry(line_geom)
        lf.setAttributes([str(feat["name"] or ""), dist_m, dist_label])
        line_feats.append(lf)

    if not line_feats:
        return False, "no_lines"

    dp.addFeatures(line_feats)
    lyr.updateExtents()

    sym = QgsLineSymbol.createSimple({
        "line_color": _LWS_COLOUR,
        "line_width": "0.4",
        "line_style": "dash",
        "capstyle":   "round",
    })
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    pal = QgsPalLayerSettings()
    pal.enabled        = True
    pal.fieldName      = "distance_label"
    pal.placement      = QgsPalLayerSettings.Placement.Line
    pal.placementFlags = QgsPalLayerSettings.AboveLine
    fmt = QgsTextFormat()
    fmt.setSize(6.5)
    fmt.setColor(QColor(_LWS_COLOUR))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)

    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    _nest_desk_study_layer(lws_lyr, lyr, _LWS_LAYER_NAME)

    return True, lyr


def remove_lws_distance_lines():
    from qgis.core import QgsProject
    for lyr in QgsProject.instance().mapLayersByName("LWS Distance Lines"):
        QgsProject.instance().removeMapLayer(lyr.id())


# ===========================================================================
# TPO council portal fallback — shown when planning.data.gov.uk returns no
# results for a given area.
# ===========================================================================

TPO_COUNCIL_PORTALS = {
    # --- London ---
    "barnet": "https://open.barnet.gov.uk/dataset/e5nge/tree-preservation-orders-schedule-items",
    "haringey": "https://haringey.gov.uk/environment/trees/tree-preservation-orders-tpos",
    "islington": "https://www.islington.gov.uk/planning/trees-and-landscape/protected-trees",
    "lambeth": "https://www.lambeth.gov.uk/planning-building-control/trees/trees-private-property/tree-preservation-orders-tpos",
    "hammersmith and fulham": "https://www.lbhf.gov.uk/planning/trees-and-tree-preservation-orders",
    "tower hamlets": "https://www.towerhamlets.gov.uk/lgnl/planning_and_building_control/conservation_and_urban_design/tree_management.aspx",
    "richmond upon thames": "https://www.richmond.gov.uk/services/planning/planning_policy/conservation/trees_and_hedges",
    "kingston upon thames": "https://www.kingston.gov.uk/trees-hedges/tree-preservation-orders",
    "merton": "https://www.merton.gov.uk/planning/trees-and-hedges",
    "sutton": "https://www.sutton.gov.uk/planning-building-control/trees-hedges/tree-preservation-orders",
    "croydon": "https://www.croydon.gov.uk/planning-and-regeneration/trees-and-hedgerows/tree-preservation-orders",
    "bromley": "https://www.bromley.gov.uk/planning-building/trees-landscape",
    "lewisham": "https://lewisham.gov.uk/myservices/planning/advice/trees-and-hedges/tree-preservation-orders",
    "greenwich": "https://www.royalgreenwich.gov.uk/info/200088/planning_and_development/370/trees_and_hedges",
    "bexley": "https://www.bexley.gov.uk/services/planning-and-building-control/trees-and-hedges",
    "havering": "https://www.havering.gov.uk/info/20005/planning/380/trees_and_hedges/2",
    "barking and dagenham": "https://www.lbbd.gov.uk/planning/trees-and-hedges",
    "redbridge": "https://www.redbridge.gov.uk/planning/trees-and-hedges/",
    "waltham forest": "https://www.walthamforest.gov.uk/planning-and-building-control/planning/trees-and-hedges",
    "hackney": "https://hackney.gov.uk/trees",
    "newham": "https://www.newham.gov.uk/planning-development/trees-hedges",
    "southwark": "https://www.southwark.gov.uk/planning-and-building-control/trees-and-landscaping/tree-preservation-orders",
    "wandsworth": "https://www.wandsworth.gov.uk/planning-and-building-control/trees-and-hedges/tree-preservation-orders/",
    "kensington and chelsea": "https://www.rbkc.gov.uk/planning-and-building-control/trees",
    "westminster": "https://www.westminster.gov.uk/planning-and-building/trees-and-hedgerows",
    "camden": "https://www.camden.gov.uk/trees",
    "ealing": "https://www.ealing.gov.uk/info/201220/trees/1272/tree_preservation_orders",
    "hounslow": "https://www.hounslow.gov.uk/info/20015/planning/1068/trees_and_hedges",
    "hillingdon": "https://www.hillingdon.gov.uk/trees",
    "harrow": "https://www.harrow.gov.uk/planning-building-control/trees-hedgerows",
    "brent": "https://www.brent.gov.uk/planning-and-building-control/trees-and-hedges",
    "enfield": "https://new.enfield.gov.uk/services/planning/trees-and-hedges/",
    "hertsmere": "https://www.hertsmere.gov.uk/Planning--Building-Control/Trees-and-Hedges/",
    "city of london": "https://www.cityoflondon.gov.uk/services/planning/planning-applications/tree-preservation-orders",

    # --- South East ---
    "adur": "https://www.adur-worthing.gov.uk/planning-policy/conservation-and-heritage/trees/tpo-register/",
    "worthing": "https://www.adur-worthing.gov.uk/planning-policy/conservation-and-heritage/trees/tpo-register/",
    "arun": "https://www.arun.gov.uk/tpo/",
    "ashford": "https://www.ashford.gov.uk/planning-and-building-control/trees-and-hedges/tree-preservation-orders/",
    "bournemouth christchurch and poole": "https://www.bcpcouncil.gov.uk/leisure-culture-and-local-heritage/parks-and-open-spaces/trees/tree-preservation-orders-tpos",
    "bracknell forest": "https://www.bracknell-forest.gov.uk/parks-and-countryside/trees/protected-trees",
    "brighton and hove": "https://www.brighton-hove.gov.uk/libraries-leisure-and-arts/parks-and-green-spaces/tree-preservation-orders",
    "buckinghamshire": "https://www.buckinghamshire.gov.uk/planning-and-building-control/trees-and-hedges/find-out-if-a-tree-is-protected/",
    "chichester": "https://www.chichester.gov.uk/treepreservation",
    "crawley": "https://crawley.gov.uk/planning/design-and-conservation/tree-preservation-orders-tpo",
    "dover": "https://www.dover.gov.uk/Planning/Conservation/Trees/Tree-Preservation-Orders-TPOs.aspx",
    "east hampshire": "https://www.easthants.gov.uk/planning-services/heritage-and-trees/tree-preservation-order-tpo",
    "eastbourne": "https://www.lewes-eastbourne.gov.uk/article/1549/Protected-trees",
    "fareham": "https://www.fareham.gov.uk/planning/trees/mapinfo.aspx",
    "folkestone and hythe": "https://www.folkestone-hythe.gov.uk/planning/trees",
    "horsham": "https://www.horsham.gov.uk/planning/protected-trees/check-if-a-tree-is-protected",
    "isle of wight": "https://www.iow.gov.uk/Residents/Environment-Planning-and-Waste/Planning-and-Building-Control/Planning/Trees-and-Hedges",
    "lewes": "https://www.lewes-eastbourne.gov.uk/article/1549/Protected-trees",
    "maidstone": "https://www.maidstone.gov.uk/home/primary-services/planning-and-building/primary-areas/trees-and-hedgerows",
    "medway": "https://www.medway.gov.uk/info/200149/planning/380/trees_and_hedges",
    "mid sussex": "https://www.midsussex.gov.uk/planning-building/trees-and-hedgerows/tree-preservation-order-tpo-map/",
    "new forest": "https://www.newforest.gov.uk/article/2642/Tree-Preservation-Orders",
    "oxford": "https://www.oxford.gov.uk/conservation-areas/tree-preservation-order-conservation-area-locations",
    "oxfordshire": "https://www.oxford.gov.uk/conservation-areas/tree-preservation-order-conservation-area-locations",
    "reading": "https://www.reading.gov.uk/environment/trees-and-hedges/tree-preservation-orders/",
    "rother": "https://www.rother.gov.uk/planning-and-building-control/conservation-and-design/natural-environment/protected-trees/",
    "rushmoor": "https://www.rushmoor.gov.uk/tpo",
    "sevenoaks": "https://www.sevenoaks.gov.uk/info/20003/planning/376/trees_and_hedgerows",
    "slough": "https://www.slough.gov.uk/planning-and-building-control/trees",
    "southampton": "https://www.southampton.gov.uk/environment/trees-grass-hedges-and-leaves/protected/tpo-map/",
    "surrey heath": "https://www.surreyheath.gov.uk/planning-and-building-control/trees/protected-trees",
    "swale": "https://www.swale.gov.uk/planning-and-building-control/planning/trees-and-hedges/",
    "tandridge": "https://www.tandridge.gov.uk/Planning/Trees-and-hedges/Tree-preservation-orders",
    "test valley": "https://www.testvalley.gov.uk/planning-and-environment/trees-and-hedgerows",
    "thanet": "https://www.thanet.gov.uk/info-pages/tree-preservation-orders/",
    "tonbridge and malling": "https://www.tmbc.gov.uk/services/planning-and-development/planning/localview/protected-trees",
    "tunbridge wells": "https://tunbridgewells.gov.uk/planning/trees/protected-trees",
    "wealden": "https://www.wealden.gov.uk/planning-and-building-control/trees-and-hedges/protected-trees/",
    "west berkshire": "https://www.westberks.gov.uk/planning-trees-and-hedgerows",
    "winchester": "https://www.winchester.gov.uk/planning/trees",
    "windsor and maidenhead": "https://www.rbwm.gov.uk/home/planning-and-building-control/trees-and-woodlands",
    "woking": "https://www.woking.gov.uk/planning/treesandhedgerows",
    "wokingham": "https://www.wokingham.gov.uk/planning-and-building/planning/trees-and-hedgerows/",

    # --- South West ---
    "bath and north east somerset": "https://www.bathnes.gov.uk/tree-preservation-orders-tpos",
    "bristol": "https://www.bristol.gov.uk/residents/planning-and-building-regulations/tree-works-and-preservation/tree-preservation-orders",
    "cornwall": "https://www.cornwall.gov.uk/planning-and-building-control/trees-and-hedges/tree-preservation-orders/",
    "east devon": "https://www.eastdevon.gov.uk/planning/planning-policy/our-plans-and-policies/trees-and-landscape/tree-preservation-orders/",
    "exeter": "https://www.exeter.gov.uk/planning-services/trees-and-hedgerows/tree-preservation-orders/",
    "gloucester": "https://www.gloucester.gov.uk/planning/trees-and-hedges/",
    "mid devon": "https://www.middevon.gov.uk/residents/planning/tree-preservation-orders-or-trees-within-conservation-areas-and-hedgerows/",
    "north devon": "https://www.northdevon.gov.uk/planning/trees-and-hedgerows/tree-preservation-orders/",
    "plymouth": "https://www.plymouth.gov.uk/tree-preservation-orders-tpos",
    "south gloucestershire": "https://www.southglos.gov.uk/planning/trees-and-landscape/tree-preservation-orders/",
    "south hams": "https://www.southhams.gov.uk/trees/protected-trees/tree-preservation-orders-tpos",
    "tewkesbury": "https://tewkesbury.gov.uk/digital-planning-data/data-downloads-tree-preservation-order-tpo-zones/",
    "torbay": "https://www.torbay.gov.uk/planning/planning-general/trees/tree-preservation-orders/",
    "torridge": "https://www.torridge.gov.uk/article/21717/Trees-and-Tree-Preservation-Orders",
    "west devon": "https://www.westdevon.gov.uk/trees/protected-trees/tree-preservation-orders-tpos",
    "wiltshire": "https://www.wiltshire.gov.uk/planning-trees-hedgerows-tpos",

    # --- East of England ---
    "brentwood": "https://www.brentwood.gov.uk/tree-preservation-orders-tpos",
    "broxbourne": "https://www.broxbourne.gov.uk/planning/trees-and-hedgerows",
    "cambridge": "https://www.cambridge.gov.uk/content/trees-and-tree-preservation-orders",
    "chelmsford": "https://www.chelmsford.gov.uk/planning/trees-and-hedges/tree-preservation-orders/",
    "east cambridgeshire": "https://www.eastcambs.gov.uk/planning/trees-and-hedgerows",
    "east hertfordshire": "https://www.eastherts.gov.uk/planning-and-building-control/trees-and-hedges/tree-preservation-orders",
    "hertsmere": "https://www.hertsmere.gov.uk/Planning--Building-Control/Trees-and-Hedges/",
    "kings lynn and west norfolk": "https://www.west-norfolk.gov.uk/homepage/327/tree_preservation_orders",
    "norwich": "https://www.norwich.gov.uk/info/20157/planning_applications/596/trees_and_hedgerows",
    "north norfolk": "https://www.north-norfolk.gov.uk/planning/trees-and-hedges/",
    "south norfolk": "https://www.southnorfolk.gov.uk/planning/trees-and-hedgerows/",
    "south cambridgeshire": "https://www.scambs.gov.uk/planning/trees-and-hedgerows/tree-preservation-orders/",
    "southend-on-sea": "https://www.southend.gov.uk/planning/trees-hedges",
    "st albans": "https://www.stalbans.gov.uk/planning/trees-and-hedgerows",
    "stevenage": "https://www.stevenage.gov.uk/planning-building-control/trees-and-hedgerows",
    "three rivers": "https://www.threerivers.gov.uk/egcl-page/trees-and-hedgerows",
    "watford": "https://www.watford.gov.uk/info/20009/trees_and_hedgerows",
    "welwyn hatfield": "https://www.welhat.gov.uk/article/1375/Tree-Preservation-Orders",

    # --- East Midlands ---
    "broxtowe": "https://www.broxtowe.gov.uk/for-you/parks-and-nature-conservation/trees-and-hedges/tree-preservation-orders/",
    "charnwood": "https://www.charnwood.gov.uk/pages/tree_preservation_orders",
    "corby": "https://www.northnorthants.gov.uk/conservation-and-protection/tree-protection-and-high-hedges/protected-trees-mapping-and-documents",
    "daventry": "https://www.westnorthants.gov.uk/tree-and-hedgerow-protection/find-out-if-tree-protected",
    "derby": "https://www.derby.gov.uk/environment-and-planning/planning/trees-and-hedges/",
    "derbyshire dales": "https://www.derbyshiredales.gov.uk/planning-a-building/trees-and-hedgerows",
    "east northamptonshire": "https://www.northnorthants.gov.uk/conservation-and-protection/tree-protection-and-high-hedges/protected-trees-mapping-and-documents",
    "erewash": "https://www.erewash.gov.uk/index.aspx?articleid=9386",
    "gedling": "https://www.gedling.gov.uk/resident/planning/trees/",
    "harborough": "https://www.harborough.gov.uk/tpo",
    "hinckley and bosworth": "https://www.hinckley-bosworth.gov.uk/tpomap",
    "kettering": "https://www.northnorthants.gov.uk/conservation-and-protection/tree-protection-and-high-hedges/protected-trees-mapping-and-documents",
    "leicester": "https://www.leicester.gov.uk/your-council/policies-plans-and-strategies/planning/heritage-and-conservation/trees-and-hedgerows/",
    "lincoln": "https://www.lincoln.gov.uk/planning/trees-hedgerows",
    "lincolnshire": "https://www.lincolnshire.gov.uk/planning/trees-hedgerows",
    "melton": "https://www.melton.gov.uk/trees",
    "north kesteven": "https://www.n-kesteven.gov.uk/planning-building/planning/tree-protection-hedges/map-tree-preservation-orders-conservation-areas",
    "north northamptonshire": "https://www.northnorthants.gov.uk/conservation-and-protection/tree-protection-and-high-hedges/protected-trees-mapping-and-documents",
    "northampton": "https://www.westnorthants.gov.uk/tree-and-hedgerow-protection/find-out-if-tree-protected",
    "nottingham": "https://www.nottinghamcity.gov.uk/planning/trees-and-hedgerows/",
    "rushcliffe": "https://www.rushcliffe.gov.uk/planning/planningadvice/treesandhedges/",
    "south kesteven": "https://www.southkesteven.gov.uk/index.aspx?articleid=5059",
    "south northamptonshire": "https://www.westnorthants.gov.uk/tree-and-hedgerow-protection/find-out-if-tree-protected",
    "wellingborough": "https://www.northnorthants.gov.uk/conservation-and-protection/tree-protection-and-high-hedges/protected-trees-mapping-and-documents",
    "west lindsey": "https://www.west-lindsey.gov.uk/planning-and-building/trees-and-hedgerows",
    "west northamptonshire": "https://www.westnorthants.gov.uk/tree-and-hedgerow-protection/find-out-if-tree-protected",

    # --- West Midlands ---
    "birmingham": "https://www.birmingham.gov.uk/info/20040/trees_and_tree_preservation_orders",
    "bromsgrove": "https://www.bromsgrove.gov.uk/planning/trees-and-hedgerows/",
    "cannock chase": "https://www.cannockchasedc.gov.uk/residents/planning/trees-hedges",
    "coventry": "https://www.coventry.gov.uk/heritage-ecology-trees/tree-preservation-orders-tpos",
    "dudley": "https://www.dudley.gov.uk/residents/planning/trees-and-hedgerows/",
    "east staffordshire": "https://www.eaststaffsbc.gov.uk/planning/trees-and-hedgerows",
    "herefordshire": "https://www.herefordshire.gov.uk/planning-1/trees-hedgerows-1",
    "lichfield": "https://www.lichfielddc.gov.uk/planning/trees-hedgerows",
    "malvern hills": "https://www.malvernhills.gov.uk/planning/trees-and-hedgerows/tree-preservation-orders/",
    "north warwickshire": "https://www.northwarks.gov.uk/info/20013/planning/1048/trees_and_hedgerows",
    "nuneaton and bedworth": "https://www.nuneatonandbedworth.gov.uk/planning/trees-and-hedgerows/",
    "redditch": "https://www.redditch.gov.uk/planning/trees-and-hedgerows/",
    "rugby": "https://www.rugby.gov.uk/info/20024/planning/304/trees_and_hedgerows",
    "sandwell": "https://www.sandwell.gov.uk/planning-and-building/trees-and-hedges/",
    "shropshire": "https://www.shropshire.gov.uk/environment/trees-and-hedgerows/tree-preservation-orders/",
    "solihull": "https://www.solihull.gov.uk/planning-and-building-control/trees-and-hedges",
    "south staffordshire": "https://www.sstaffs.gov.uk/planning/trees-and-hedgerows.cfm",
    "stafford": "https://www.staffordbc.gov.uk/tree-preservation-orders",
    "staffordshire moorlands": "https://www.staffsmoorlands.gov.uk/article/1400/Trees-and-hedgerows",
    "stratford-on-avon": "https://www.stratford.gov.uk/planning/trees-and-hedgerows.cfm",
    "tamworth": "https://www.tamworth.gov.uk/planning/trees",
    "telford and wrekin": "https://www.telford.gov.uk/info/20469/trees_and_hedges",
    "walsall": "https://www.walsall.gov.uk/planning/trees-and-hedgerows/",
    "warwick": "https://www.warwickdc.gov.uk/info/20025/planning/359/trees_and_hedgerows",
    "wolverhampton": "https://www.wolverhampton.gov.uk/planning/trees-and-hedgerows/",
    "worcester": "https://www.worcester.gov.uk/planning/trees-and-hedgerows/tree-preservation-orders/",
    "wychavon": "https://www.wychavon.gov.uk/planning/trees-and-hedgerows/tree-preservation-orders",
    "wyre forest": "https://www.wyreforestdc.gov.uk/planning/trees-and-hedgerows.aspx",

    # --- North West ---
    "blackburn with darwen": "https://www.blackburn.gov.uk/planning/trees-and-hedgerows",
    "blackpool": "https://www.blackpool.gov.uk/Residents/Planning-environment-and-community/Planning/Trees-and-hedges/Tree-preservation-orders.aspx",
    "cheshire east": "https://www.cheshireeast.gov.uk/environment/heritage_natural_environment/trees,_woodlands_and_hedges/tree-preservation-orders.aspx",
    "cheshire west and chester": "https://www.cheshirewestandchester.gov.uk/residents/planning-and-building-control/trees-and-hedgerows",
    "chorley": "https://www.chorley.gov.uk/planning/trees-and-hedges",
    "fylde": "https://www.fylde.gov.uk/resident/planning/trees-and-hedgerows/",
    "halton": "https://www.halton.gov.uk/info/1/planning/104/trees_and_hedgerows",
    "knowsley": "https://www.knowsley.gov.uk/residents/planning-and-building-control/trees-and-hedgerows",
    "lake district": "https://www.lakedistrict.gov.uk/planning/tree_preservation/tpomap",
    "lancaster": "https://www.lancaster.gov.uk/planning/trees-and-hedgerows/",
    "liverpool": "https://www.liverpool.gov.uk/planning-and-building-control/trees-hedges-and-conservation/tree-preservation-orders/",
    "manchester": "https://www.manchester.gov.uk/info/508/tree_preservation_orders/1424/tree_preservation_orders_and_trees_in_conservation_areas",
    "oldham": "https://www.oldham.gov.uk/info/200413/planning_development/2000/trees_and_hedges",
    "pendle": "https://www.pendle.gov.uk/planning/trees",
    "preston": "https://www.preston.gov.uk/article/1133/Tree-preservation-orders-TPOs",
    "ribble valley": "https://www.ribblevalley.gov.uk/downloads/download/263/list-of-tree-preservation-orders-tpo",
    "rochdale": "https://www.rochdale.gov.uk/planning-and-building-control/planning-advice/trees-and-hedgerows",
    "rossendale": "https://www.rossendale.gov.uk/planning/trees",
    "salford": "https://www.salford.gov.uk/planning/trees-and-hedges/",
    "sefton": "https://www.sefton.gov.uk/planning/trees-and-hedges/",
    "south lakeland": "https://www.westmorland.gov.uk/planning/trees-and-hedgerows/",
    "south ribble": "https://www.southribble.gov.uk/planning/trees-and-hedges/tree-preservation-orders/",
    "st helens": "https://www.sthelens.gov.uk/planning/trees",
    "stockport": "https://www.stockport.gov.uk/tree-preservation/check-if-there-is-an-existing-tree-preservation-order",
    "trafford": "https://www.trafford.gov.uk/residents/planning/trees-and-hedgerows/tree-preservation-orders.aspx",
    "warrington": "https://www.warrington.gov.uk/planning/trees-and-hedges",
    "west lancashire": "https://www.westlancs.gov.uk/planning/trees-and-hedgerows/",
    "wigan": "https://www.wigan.gov.uk/Resident/Planning/Trees.aspx",
    "wirral": "https://www.wirral.gov.uk/planning-and-building/tree-preservation/tree-preservation-orders",
    "wyre": "https://www.wyre.gov.uk/planning/trees",

    # --- Yorkshire & Humber ---
    "barnsley": "https://www.barnsley.gov.uk/services/parks-and-open-spaces/tree-preservation-orders/",
    "bradford": "https://www.bradford.gov.uk/planning-and-building-control/trees-and-hedgerows/tree-preservation-orders/",
    "calderdale": "https://dataworks.calderdale.gov.uk/dataset/tree-preservation-orders-233pj",
    "craven": "https://www.cravendc.gov.uk/planning/trees-and-hedgerows/",
    "doncaster": "https://www.doncaster.gov.uk/services/planning-and-regeneration/trees-and-hedges",
    "east riding of yorkshire": "https://www.eastriding.gov.uk/planning/planning-policy/landscape/tree-preservation-orders/",
    "hambleton": "https://www.hambleton.gov.uk/planning/trees-and-hedgerows/",
    "harrogate": "https://www.northyorks.gov.uk/planning/trees-and-hedgerows",
    "hull": "https://www.hull.gov.uk/planning-and-regeneration/trees-and-hedges/tree-preservation-orders",
    "kirklees": "https://www.kirklees.gov.uk/beta/trees-and-hedgerows/tree-preservation-orders.aspx",
    "leeds": "https://www.leeds.gov.uk/planning/trees-and-hedgerows/tree-preservation-orders",
    "north yorkshire": "https://www.northyorks.gov.uk/planning/trees-and-hedgerows",
    "richmond (north yorkshire)": "https://www.northyorks.gov.uk/planning/trees-and-hedgerows",
    "rotherham": "https://www.rotherham.gov.uk/planning/trees-and-hedgerows",
    "ryedale": "https://www.northyorks.gov.uk/planning/trees-and-hedgerows",
    "scarborough": "https://www.northyorks.gov.uk/planning/trees-and-hedgerows",
    "selby": "https://www.northyorks.gov.uk/planning/trees-and-hedgerows",
    "sheffield": "https://www.sheffield.gov.uk/planning/trees-hedgerows-landscape/tree-preservation-orders",
    "wakefield": "https://www.wakefield.gov.uk/planning/heritage-conservation-and-tree-preservation/tree-preservation-orders",
    "york": "https://www.york.gov.uk/TreePreservationOrders",

    # --- North East ---
    "county durham": "https://www.durham.gov.uk/planning/trees-and-hedgerows/",
    "darlington": "https://www.darlington.gov.uk/planning/trees-and-hedgerows/",
    "gateshead": "https://www.gateshead.gov.uk/article/2774/Tree-preservation-orders",
    "hartlepool": "https://www.hartlepool.gov.uk/info/200044/planning/1057/trees_and_hedgerows",
    "middlesbrough": "https://www.middlesbrough.gov.uk/planning-and-development/trees-and-hedgerows",
    "newcastle upon tyne": "https://www.newcastle.gov.uk/services/planning-and-buildings/planning-policy/trees-and-hedgerows",
    "north tyneside": "https://www.northtyneside.gov.uk/planning-and-buildings/planning/trees-and-hedgerows",
    "northumberland": "https://www.northumberland.gov.uk/Planning/Trees.aspx",
    "redcar and cleveland": "https://www.redcar-cleveland.gov.uk/planning-and-buildings/planning/trees-and-hedgerows",
    "south tyneside": "https://www.southtyneside.gov.uk/planning/trees-and-hedgerows/",
    "stockton-on-tees": "https://www.stockton.gov.uk/planning/trees-and-hedgerows",
    "sunderland": "https://www.sunderland.gov.uk/planning/trees-and-hedgerows",

    # --- National Parks ---
    "south downs": "https://www.southdowns.gov.uk/planning-applications/general-enquiries/tree-conservation-areas/",
    "south downs national park": "https://www.southdowns.gov.uk/planning-applications/general-enquiries/tree-conservation-areas/",
    "lake district national park": "https://www.lakedistrict.gov.uk/planning/tree_preservation/tpomap",
    "peak district": "https://www.peakdistrict.gov.uk/planning/trees-and-hedges",
    "peak district national park": "https://www.peakdistrict.gov.uk/planning/trees-and-hedges",
    "dartmoor": "https://www.dartmoor.gov.uk/planning/trees-and-hedgerows",
    "dartmoor national park": "https://www.dartmoor.gov.uk/planning/trees-and-hedgerows",
    "exmoor": "https://www.exmoor-nationalpark.gov.uk/planning/trees-and-hedgerows",
    "exmoor national park": "https://www.exmoor-nationalpark.gov.uk/planning/trees-and-hedgerows",
    "new forest national park": "https://www.newforest.gov.uk/article/2642/Tree-Preservation-Orders",
    "yorkshire dales": "https://www.yorkshiredales.org.uk/planning/trees-and-woodland/",
    "yorkshire dales national park": "https://www.yorkshiredales.org.uk/planning/trees-and-woodland/",
    "north york moors": "https://www.northyorkmoors.org.uk/planning/trees-and-hedgerows/",
    "north york moors national park": "https://www.northyorkmoors.org.uk/planning/trees-and-hedgerows/",
    "brecon beacons": "https://www.beacons-npa.gov.uk/planning/trees-and-woodland/",
}

def _norm_lpa(name):
    """
    Normalise an LPA name for matching.

    Lowercases, strips the planning.data.gov.uk ' LPA' suffix and common
    council-type descriptors, and unifies punctuation/'&' so that the many
    name variants from different data sources collapse onto one key, e.g.
    "King's Lynn and West Norfolk" / "Kings Lynn & West Norfolk Borough
    Council" → "kings lynn and west norfolk".
    """
    s = (name or "").strip().lower()
    if s.endswith(" lpa"):
        s = s[:-4]
    s = s.replace("&", " and ")
    s = s.replace("'", "").replace("’", "")   # king's -> kings (matches table)
    for ch in (",", ".", "(", ")", "-", "/"):
        s = s.replace(ch, " ")
    s = " ".join(s.split())
    for suf in (" metropolitan borough council", " borough council",
                " district council", " city council", " county council",
                " council", " borough", " district"):
        if s.endswith(suf):
            s = s[:-len(suf)]
            break
    return " ".join(s.split())


# Alias normalisations — extra name variants → canonical normalised key.
# Keys and values are in _norm_lpa() space.
_LPA_ALIASES = {
    "bournemouth": "bournemouth christchurch and poole",
    "christchurch": "bournemouth christchurch and poole",
    "poole": "bournemouth christchurch and poole",
    "city of york": "york",
    "city of london corporation": "city of london",
    "kings lynn": "kings lynn and west norfolk",
}

# Generic descriptor words that may differ between an LPA's official name and
# its table key without changing which authority is meant.  A distinctive
# qualifier (e.g. "south", "upon") is NOT generic, so it blocks a fuzzy match.
_GENERIC_WORDS = {"city", "of", "the", "royal", "greater", "county",
                  "council", "borough", "district", "metropolitan"}

# Pre-normalised view of the portal table: norm_key → (display_key, url).
_NORM_PORTALS = {_norm_lpa(k): (k, v) for k, v in TPO_COUNCIL_PORTALS.items()}

# Absolute last-resort fallback (only when no LPA could be resolved at all).
_TPO_FALLBACK_URL = "https://www.planningportal.co.uk/find-your-local-planning-authority"

_LPA_API_BASE = "https://www.planning.data.gov.uk/entity.json"


def _tpo_search_url(council_name):
    """A council-scoped web search for the council's TPO register/map."""
    from urllib.parse import quote_plus
    q = quote_plus(f"{council_name} tree preservation order map")
    return f"https://www.google.com/search?q={q}"


def get_lpa_for_bng(easting, northing):
    """
    Resolve the Local Planning Authority for a BNG (EPSG:27700) coordinate.

    Primary source: point-in-polygon against the planning.data.gov.uk
    'local-planning-authority' dataset — the authoritative statutory LPA
    boundary.  Unlike a postcode lookup it has gap-free coverage, so it
    resolves rural / off-grid sites that have no nearby postcode (the common
    case for ecology sites).  Within a National Park it returns the National
    Park Authority, which is the correct TPO authority there.

    Fallback: postcodes.io reverse geocode (admin_district), with a widened
    search radius, used only if the planning.data.gov.uk lookup fails.

    Returns (name, reference):
      name      -- LPA name e.g. 'Lambeth' (without the dataset ' LPA'
                   suffix), or None
      reference -- stable LPA code e.g. 'E60000195', or None
    """
    import json
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode
    from qgis.core import (
        QgsCoordinateReferenceSystem, QgsCoordinateTransform,
        QgsPointXY, QgsProject,
    )

    # BNG → WGS84
    try:
        bng = QgsCoordinateReferenceSystem("EPSG:27700")
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        xf  = QgsCoordinateTransform(bng, wgs, QgsProject.instance())
        pt  = xf.transform(QgsPointXY(easting, northing))
        lon, lat = pt.x(), pt.y()
    except Exception:
        return None, None

    # --- Primary: planning.data.gov.uk point-in-polygon -------------------
    try:
        params = urlencode({
            "dataset":           "local-planning-authority",
            "geometry":          f"POINT({lon:.6f} {lat:.6f})",
            "geometry_relation": "intersects",
            "limit":             10,
        })
        req = Request(f"{_LPA_API_BASE}?{params}",
                      headers={"User-Agent": "BiomeQGISPlugin/1.0"})
        with urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        entities = [e for e in data.get("entities", [])
                    if (e.get("name") or "").strip()]
        if entities:
            # If the API ever returns overlapping polygons, prefer the
            # National Park Authority — it is the TPO authority within a park.
            chosen = next(
                (e for e in entities
                 if "national park" in (e.get("name") or "").lower()),
                entities[0],
            )
            name = chosen["name"].strip()
            ref  = (chosen.get("reference") or "").strip()
            if name.lower().endswith(" lpa"):
                name = name[:-4].strip()
            if name:
                return name, (ref or None)
    except Exception:
        pass

    # --- Fallback: postcodes.io reverse geocode (widened radius) ----------
    try:
        params = urlencode({"lon": f"{lon:.6f}", "lat": f"{lat:.6f}",
                            "limit": 1, "radius": 2000})
        req = Request(f"https://api.postcodes.io/postcodes?{params}",
                      headers={"User-Agent": "BiomeQGISPlugin/1.0"})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        if data.get("status") == 200 and data.get("result"):
            lpa = (data["result"][0].get("admin_district") or "").strip()
            if lpa:
                return lpa, None
    except Exception:
        pass

    return None, None


def get_tpo_council_portal(lpa_name, lpa_ref=None):
    """
    Resolve the best TPO portal for an LPA.

    Resolution order: exact normalised match → alias → guarded whole-word
    containment → council-scoped web search.  The old loose character-substring
    match has been removed because it mis-routed councils (e.g. Hart →
    Hartlepool, South Oxfordshire → Oxford).

    Returns (display_name, url, is_direct):
      display_name -- human-readable council name
      url          -- best URL we have
      is_direct    -- True  = a specific council TPO page (table match)
                      False = a council-scoped search, or (no LPA resolved)
                              the generic Planning Portal LPA finder
    """
    if not lpa_name:
        return "your local council", _TPO_FALLBACK_URL, False

    display = lpa_name.strip()
    norm = _norm_lpa(lpa_name)
    norm = _LPA_ALIASES.get(norm, norm)

    # 1. Exact normalised match
    hit = _NORM_PORTALS.get(norm)
    if hit:
        return display, hit[1], True

    # 2. Allow only generic descriptor words to differ, e.g. "City of Bristol"
    #    ↔ "Bristol" or "Royal Borough of Greenwich" ↔ "Greenwich".  Any
    #    distinctive qualifier blocks the match, so "South Oxfordshire" no
    #    longer resolves to "Oxford".  A National-Park query must not match a
    #    non-park council.  Best (most shared words) wins, order-independent.
    q_words = set(norm.split())
    q_is_np = "national park" in norm
    best = None  # (score, url)
    for nkey, (orig, url) in _NORM_PORTALS.items():
        k_words = set(nkey.split())
        if (q_words ^ k_words) - _GENERIC_WORDS:
            continue
        if q_is_np != ("national park" in nkey):
            continue
        shared = [w for w in (q_words & k_words)
                  if len(w) >= 4 and w not in _GENERIC_WORDS]
        if not shared:
            continue
        if best is None or len(shared) > best[0]:
            best = (len(shared), url)
    if best:
        return display, best[1], True

    # 3. Council-scoped search — always more useful here than the generic
    #    LPA finder, and resilient to councils restructuring their websites.
    return display, _tpo_search_url(display), False


# ===========================================================================
# SSSI Impact Risk Zones
# ===========================================================================

_IRZ_WFS_SLUG     = "sssi-impact-risk-zones-england"
_IRZ_WFS_TYPENAME = "SSSI_Impact_Risk_Zones_England"   # kept for reference; not used
_IRZ_LAYER_NAME   = "SSSI Impact Risk Zones"
_IRZ_HIGHLIGHT_LAYER_NAME = "SSSI IRZ — Intersecting Zones"

# ArcGIS FeatureServer — same NE org as GCN risk zones (confirmed working)
_IRZ_FS_BASE = (
    "https://services.arcgis.com/JJzESW51TqeY9uat/arcgis/rest/services"
    "/SSSI_Impact_Risk_Zones_England/FeatureServer/0/query"
)

_IRZ_TOOL_WFS = "https://irz.geodata.org.uk/IRZ/wfs"

# DSP WMS — authoritative source, same as MAGIC uses
_IRZ_WMS_URL  = "https://environment.data.gov.uk/spatialdata/sssi-impact-risk-zones-england/wms"
_IRZ_WMS_LAYER = "ne_sssi_impact_risk_zo"

_IRZ_WFS_TYPENAMES = [
    "irz:irzones",
    "irz:zones",
    "irz:irz_zones",
    "irz:sssi_irz",
    "ms:irz",
    "irz",
]


def query_sssi_irz_wms(easting, northing):
    """
    Query the DSP WMS GetFeatureInfo at a point — same approach as MAGIC.
    Returns irzurl, field values, and raw response for debugging.
    """
    import json
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode

    buf  = 50
    bbox = f"{easting-buf},{northing-buf},{easting+buf},{northing+buf}"

    base_params = {
        "SERVICE":       "WMS",
        "VERSION":       "1.3.0",
        "REQUEST":       "GetFeatureInfo",
        "LAYERS":        _IRZ_WMS_LAYER,
        "QUERY_LAYERS":  _IRZ_WMS_LAYER,
        "CRS":           "EPSG:27700",
        "BBOX":          bbox,
        "WIDTH":         "10",
        "HEIGHT":        "10",
        "I":             "5",
        "J":             "5",
        "FEATURE_COUNT": "5",
    }

    raw_text = ""

    # Try JSON first, then XML, then plain text
    for fmt in ("application/json", "text/xml", "text/plain"):
        try:
            params = urlencode({**base_params, "INFO_FORMAT": fmt})
            req    = Request(
                f"{_IRZ_WMS_URL}?{params}",
                headers={"User-Agent": "BiomeQGISPlugin/1.0"}
            )
            resp     = urlopen(req, timeout=20)
            raw_text = resp.read().decode("utf-8", errors="replace")

            # Log full response for debugging
            try:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage(
                    f"IRZ WMS GetFeatureInfo [{fmt}] response:\n{raw_text[:2000]}",
                    "Biome IRZ", Qgis.MessageLevel.Info
                )
            except Exception:
                pass

            if fmt == "application/json":
                data     = json.loads(raw_text)
                features = data.get("features", [])
                if features:
                    return _parse_irz_wms_features(features)
                # Empty features — try XML
                continue

            elif "text/xml" in fmt or raw_text.strip().startswith("<"):
                return _parse_irz_wms_xml(raw_text, easting, northing)

            else:
                return _parse_irz_wms_text(raw_text, easting, northing)

        except Exception as exc:
            try:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage(
                    f"IRZ WMS [{fmt}] error: {exc}", "Biome IRZ", Qgis.MessageLevel.Warning
                )
            except Exception:
                pass
            continue

    return {"irzurl": None, "fields": {}, "no_zone": True, "raw_text": raw_text}


def _parse_irz_wms_features(features):
    """Parse GeoJSON features from WMS GetFeatureInfo JSON response."""
    PRIORITY = {"consult": 3, "threshold": 2, "unlikely": 1, None: 0}
    merged_fields = {}
    irzurl = None

    for feat in features:
        props       = feat.get("properties", {}) or {}
        props_lower = {k.lower(): v for k, v in props.items()}

        url_val = (
            props.get("IRZURL") or props.get("irzurl") or
            props.get("IRZUrl") or props.get("Irzurl") or
            props_lower.get("irzurl") or ""
        )
        if url_val and not irzurl:
            irzurl = str(url_val).strip()

        for fname, _ in IRZ_FIELDS:
            val = str(props.get(fname) or props_lower.get(fname.lower()) or "").strip()
            existing_pri = PRIORITY.get(_irz_consult_level(merged_fields.get(fname, "")), 0)
            if PRIORITY.get(_irz_consult_level(val), 0) > existing_pri:
                merged_fields[fname] = val

    return {"irzurl": irzurl, "fields": merged_fields, "raw": features}


def _parse_irz_wms_xml(xml_text, easting, northing):
    """Parse XML/GML response from WMS GetFeatureInfo."""
    import re
    PRIORITY = {"consult": 3, "threshold": 2, "unlikely": 1, None: 0}
    merged_fields = {}
    irzurl = None

    # Extract IRZURL
    url_match = re.search(r'(?:IRZURL|irzurl|IRZUrl)["\s:>]*([^<"\s&]+irz\.geodata[^<"\s&]*)', xml_text, re.IGNORECASE)
    if url_match:
        irzurl = url_match.group(1).strip()

    # Extract field values — try both attribute and element forms
    for fname, _ in IRZ_FIELDS:
        # Match <FIELDNAME>value</FIELDNAME> or FIELDNAME="value"
        patterns = [
            rf'<[^>]*{re.escape(fname)}[^>]*>([^<]+)<',
            rf'{re.escape(fname)}["\s:=>]+([^<"\n&]+)',
        ]
        for pat in patterns:
            m = re.search(pat, xml_text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                if val and PRIORITY.get(_irz_consult_level(val), 0) > PRIORITY.get(_irz_consult_level(merged_fields.get(fname, "")), 0):
                    merged_fields[fname] = val
                break

    if not merged_fields and not irzurl:
        return {"irzurl": None, "fields": {}, "no_zone": True, "raw_text": xml_text[:500]}

    return {"irzurl": irzurl, "fields": merged_fields, "raw_text": xml_text[:500]}


def _parse_irz_wms_text(text, easting, northing):
    """Parse plain text response from WMS GetFeatureInfo."""
    import re
    PRIORITY = {"consult": 3, "threshold": 2, "unlikely": 1, None: 0}
    merged_fields = {}
    irzurl = None

    url_match = re.search(r'(https?://irz\.geodata[^\s\'"<>]+)', text)
    if url_match:
        irzurl = url_match.group(1).strip()

    for fname, _ in IRZ_FIELDS:
        m = re.search(rf'{re.escape(fname)}\s*[=:]\s*(.+)', text, re.IGNORECASE)
        if m:
            val = m.group(1).strip().strip('"\'')
            if PRIORITY.get(_irz_consult_level(val), 0) > PRIORITY.get(_irz_consult_level(merged_fields.get(fname, "")), 0):
                merged_fields[fname] = val

    return {"irzurl": irzurl, "fields": merged_fields, "raw_text": text[:500]}


def get_irz_tool_code(easting, northing):
    """
    Query the IRZ tool's own WFS at irz.geodata.org.uk to get the correct
    internal irzcode for a point location.

    The IRZ tool uses consolidated summary polygons whose PostGIS IDs are
    completely separate from the ArcGIS FeatureServer OBJECTIDs. This
    function queries the authoritative source directly.

    Returns a list of (irzcode, centroid_e, centroid_n) tuples, or [] on failure.
    """
    import json
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode

    # Try GetCapabilities first to discover the correct typename
    try:
        caps_req = Request(
            f"{_IRZ_TOOL_WFS}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities",
            headers={"User-Agent": "BiomeQGISPlugin/1.0"}
        )
        caps_resp = urlopen(caps_req, timeout=8)
        caps_xml  = caps_resp.read().decode("utf-8", errors="ignore")
        # Extract first FeatureType Name from capabilities XML
        import re
        names = re.findall(r'<Name>([^<]+)</Name>', caps_xml)
        # Filter to likely zone typenames
        typename = next(
            (n for n in names if "irz" in n.lower() or "zone" in n.lower()),
            names[0] if names else None
        )
    except Exception:
        typename = None

    typenames_to_try = (
        [typename] + _IRZ_WFS_TYPENAMES if typename else _IRZ_WFS_TYPENAMES
    )

    # Use a small BBOX around the centroid to find containing zones
    buf = 10
    bbox_param = (
        f"{easting - buf},{northing - buf},"
        f"{easting + buf},{northing + buf},EPSG:27700"
    )

    for tn in typenames_to_try:
        params = urlencode({
            "SERVICE":      "WFS",
            "VERSION":      "2.0.0",
            "REQUEST":      "GetFeature",
            "TYPENAMES":    tn,
            "BBOX":         bbox_param,
            "SRSNAME":      "EPSG:27700",
            "outputFormat": "application/json",
            "count":        20,
        })
        try:
            req  = Request(
                f"{_IRZ_TOOL_WFS}?{params}",
                headers={"User-Agent": "BiomeQGISPlugin/1.0"}
            )
            resp = urlopen(req, timeout=8)
            data = json.loads(resp.read().decode("utf-8"))

            # Skip error responses
            if "error" in data or not data.get("features"):
                continue

            results = []
            for feat in data.get("features", []):
                # Feature ID is the irzcode
                fid  = feat.get("id", "")
                geom = feat.get("geometry", {})
                if geom.get("type") in ("Polygon", "MultiPolygon"):
                    coords_list = (
                        geom["coordinates"][0]
                        if geom["type"] == "Polygon"
                        else geom["coordinates"][0][0]
                    )
                    if coords_list:
                        cx = int(sum(c[0] for c in coords_list) / len(coords_list))
                        cy = int(sum(c[1] for c in coords_list) / len(coords_list))
                    else:
                        cx, cy = easting, northing
                else:
                    cx, cy = easting, northing
                results.append((fid, cx, cy))

            if results:
                return results

        except Exception:
            continue

    return []

# All IRZ attribute fields — confirmed from NE FeatureServer layer metadata
# (Wokingham/Newcastle-under-Lyme LPA mirrors of the NE hosted service, 2024-26)
IRZ_FIELDS = [
    ("AllConsult",  "All Development"),
    ("AIRPORT",     "Airport / Aviation"),
    ("Infrastruc",  "Infrastructure"),
    ("WindSolar",   "Wind / Solar"),
    ("QUARRY",      "Quarrying"),
    ("MinOilGas",   "Minerals / Oil / Gas"),
    ("RuralNonRe",  "Rural Non-Residential"),
    ("Residentia",  "Residential"),
    ("RuralResid",  "Rural Residential"),
    ("AirPolluti",  "Air Pollution"),
    ("Combustion",  "Combustion (>20 MW)"),
    ("Waste",       "Waste Management"),
    ("Compost",     "Composting"),
    ("Discharge",   "Effluent Discharge"),
    ("Water_Sply",  "Water Supply"),
]

_IRZ_FIELD_NAMES = [f for f, _ in IRZ_FIELDS]

# NE IRZ tool development type dropdown — exact wording from irz.geodata.org.uk
# Maps display label → field name in IRZ_FIELDS
NE_DEV_TYPES = [
    ("-- Select development type --",                   None),
    ("Airport",                                          "AIRPORT"),
    ("Infrastructure (roads, rail, pipelines, pylons)",  "Infrastruc"),
    ("Wind or Solar",                                    "WindSolar"),
    ("Quarry",                                           "QUARRY"),
    ("Minerals or Oil and Gas",                          "MinOilGas"),
    ("Rural non-residential development",                "RuralNonRe"),
    ("Residential development",                          "Residentia"),
    ("Rural residential development",                    "RuralResid"),
    ("Air pollution (pig/poultry, industrial process)",  "AirPolluti"),
    ("Combustion (>20MW energy input)",                  "Combustion"),
    ("Waste",                                            "Waste"),
    ("Composting",                                       "Compost"),
    ("Discharge to water",                               "Discharge"),
    ("Water supply, drainage or abstraction",            "Water_Sply"),
    ("All development types",                            "AllConsult"),
]


def _irz_consult_level(value):
    """Classify a field value: 'consult', 'threshold', 'unlikely', or None."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    if not v:
        return None
    if "unlikely" in v or "not required" in v:
        return "unlikely"
    if "consult" in v or "natural england" in v or "seek advice" in v:
        return "consult"
    return "threshold"


def query_sssi_irz(easting, northing, redline_geom=None):
    """
    Query SSSI Impact Risk Zone polygons relevant to the site.

    Two queries, matching NE's own IRZ tool behaviour:
      1. Point query (centroid) — zones that CONTAIN the point, regardless
         of how large the zone is. This is what irz.geodata.org.uk does.
      2. Redline BBOX intersect — catches additional zones for large redlines
         that straddle a zone boundary. Non-fatal if it fails.

    Results are deduplicated by OBJECTID.
    """
    import json
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode

    def _fetch(params_dict):
        req  = Request(
            f"{_IRZ_FS_BASE}?{urlencode(params_dict)}",
            headers={"User-Agent": "BiomeQGISPlugin/1.0"}
        )
        resp = urlopen(req, timeout=25)
        data = json.loads(resp.read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(
                f"IRZ API error: {data['error'].get('message', str(data['error']))}"
            )
        return data.get("features", [])

    all_features = {}  # OBJECTID → feature, deduplicated

    # ── Query 1: point-in-zone ────────────────────────────────────────────
    point_json = json.dumps({
        "x": easting, "y": northing,
        "spatialReference": {"wkid": 27700},
    })
    feats = _fetch({
        "where":             "1=1",
        "geometry":          point_json,
        "geometryType":      "esriGeometryPoint",
        "spatialRel":        "esriSpatialRelIntersects",
        "inSR":              "27700",
        "outFields":         "*",
        "returnGeometry":    "true",
        "outSR":             "27700",
        "f":                 "json",
        "resultRecordCount": 500,
    })
    for f in feats:
        oid = f.get("attributes", {}).get("OBJECTID") or id(f)
        all_features[oid] = f

    # ── Query 2: redline BBOX intersect ───────────────────────────────────
    if redline_geom and not redline_geom.isEmpty():
        bb = redline_geom.boundingBox()
        env_json = json.dumps({
            "xmin": bb.xMinimum(), "ymin": bb.yMinimum(),
            "xmax": bb.xMaximum(), "ymax": bb.yMaximum(),
            "spatialReference": {"wkid": 27700},
        })
        try:
            feats2 = _fetch({
                "where":             "1=1",
                "geometry":          env_json,
                "geometryType":      "esriGeometryEnvelope",
                "spatialRel":        "esriSpatialRelIntersects",
                "inSR":              "27700",
                "outFields":         "*",
                "returnGeometry":    "true",
                "outSR":             "27700",
                "f":                 "json",
                "resultRecordCount": 500,
            })
            for f in feats2:
                oid = f.get("attributes", {}).get("OBJECTID") or id(f)
                all_features[oid] = f
        except Exception:
            pass  # Non-fatal — point query already ran

    if not all_features:
        return []

    results = []
    for i, feat in enumerate(all_features.values()):
        attrs    = feat.get("attributes", {}) or {}
        geom_raw = feat.get("geometry", {}) or {}

        feat_geom = _esri_rings_to_qgsgeom(geom_raw)
        if feat_geom.isEmpty():
            continue

        # Log all field names on first record so we can see what's available
        if i == 0:
            try:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage(
                    f"IRZ ArcGIS fields: {list(attrs.keys())}",
                    "Biome IRZ", Qgis.MessageLevel.Info
                )
            except Exception:
                pass

        sssi_name = (
            attrs.get("FEATURE_NAME") or attrs.get("Feature_Name") or
            attrs.get("SSSI_Name")    or attrs.get("sssi_name") or
            attrs.get("Name")         or attrs.get("NAME") or ""
        ).strip()

        # Extract IRZURL — the correct link to irz.geodata.org.uk for this zone
        attrs_lower = {k.lower(): v for k, v in attrs.items()}
        irzurl = (
            attrs.get("IRZURL") or attrs.get("IRZUrl") or
            attrs.get("irzurl") or attrs_lower.get("irzurl") or ""
        )
        if irzurl:
            irzurl = str(irzurl).strip()

        field_values = {}
        for field_name, _ in IRZ_FIELDS:
            val = (
                attrs.get(field_name) or
                attrs_lower.get(field_name.lower()) or ""
            )
            field_values[field_name] = str(val).strip() if val else ""

        consult_types   = []
        threshold_types = []
        for field_name, label in IRZ_FIELDS:
            level = _irz_consult_level(field_values.get(field_name, ""))
            if level == "consult":
                consult_types.append(label)
            elif level == "threshold":
                threshold_types.append(label)

        results.append({
            "sssi_name":       sssi_name,
            "fields":          field_values,
            "consult_types":   consult_types,
            "threshold_types": threshold_types,
            "geom":            feat_geom,
            "objectid":        attrs.get("OBJECTID") or attrs.get("objectid") or "",
            "centroid":        feat_geom.centroid().asPoint(),
            "irzurl":          irzurl,
        })

    return results


def query_sssi_units_condition(easting, northing, redline_geom=None):
    """
    Query SSSI Units within 2 km and return condition summary per SSSI name.

    Returns a dict:  {sssi_name: {'favourable': n, 'unfav_recovering': n,
                                   'unfav_declining': n, 'destroyed': n,
                                   'total': n, 'area_ha': float}}
    """
    import json
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode

    RADIUS = _search_radius("sssi_units")
    search_buffer = _build_search_buffer(easting, northing, redline_geom, RADIUS)
    bb = search_buffer.boundingBox()
    bbox_param = (
        f"{bb.xMinimum()},{bb.yMinimum()},"
        f"{bb.xMaximum()},{bb.yMaximum()},EPSG:27700"
    )

    wfs_url = _DS_BASE_WFS.format(slug="sites-of-special-scientific-interest-units-england")
    params = urlencode({
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    "Sites_of_Special_Scientific_Interest_Units_England",
        "BBOX":         bbox_param,
        "SRSNAME":      "EPSG:27700",
        "outputFormat": "application/json",
    })

    try:
        req  = Request(f"{wfs_url}?{params}",
                       headers={"User-Agent": "BiomeQGISPlugin/1.0"})
        resp = urlopen(req, timeout=25)
        data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}  # Non-fatal — condition data is supplementary

    condition_map = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {}) or {}
        props_l = {k.lower(): v for k, v in props.items()}

        # SSSI name field
        sssi_name = (
            props_l.get("sssi_name") or
            props_l.get("name") or ""
        ).strip()
        if not sssi_name:
            continue

        geom_json = feat.get("geometry", {}) or {}
        feat_geom = _geojson_rings_to_qgsgeom(geom_json)
        if not feat_geom.isEmpty() and not search_buffer.intersects(feat_geom):
            continue

        # Condition field — various naming conventions in NE data
        condition_raw = (
            props_l.get("condition") or
            props_l.get("conditionn") or
            props_l.get("unit_cond") or
            props_l.get("cond") or ""
        ).strip().lower()

        try:
            area_ha = float(props_l.get("area") or props_l.get("unitarea") or 0)
        except (TypeError, ValueError):
            area_ha = 0.0

        if sssi_name not in condition_map:
            condition_map[sssi_name] = {
                "favourable": 0, "unfav_recovering": 0,
                "unfav_declining": 0, "destroyed": 0,
                "not_assessed": 0, "total": 0, "area_ha": 0.0,
            }

        c = condition_map[sssi_name]
        c["total"]   += 1
        c["area_ha"] += area_ha

        if "favourable" in condition_raw and "un" not in condition_raw:
            c["favourable"] += 1
        elif "recovering" in condition_raw:
            c["unfav_recovering"] += 1
        elif "declining" in condition_raw:
            c["unfav_declining"] += 1
        elif "destroy" in condition_raw or "part" in condition_raw:
            c["destroyed"] += 1
        else:
            c["not_assessed"] += 1

    return condition_map


def load_irz_layer(easting, northing, redline_geom=None):
    """
    Fetch IRZ polygons that intersect the redline and load them as two memory
    layers on the QGIS canvas:

      1. A full WMS overlay layer (the DSP WMS for all IRZ zones, styled lightly)
      2. A highlighted memory layer showing only the intersecting zones in orange

    Returns (ok, records_list_or_error_string).
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsProject,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings, QgsRasterLayer,
    )
    from qgis.PyQt.QtGui import QColor

    # Remove previous highlight layer
    for old in QgsProject.instance().mapLayersByName(_IRZ_HIGHLIGHT_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    try:
        records = query_sssi_irz(easting, northing, redline_geom)
    except Exception as exc:
        return False, str(exc)

    if not records:
        return False, "no_results"

    # Build highlight memory layer — polygon with orange semi-transparent fill
    lyr = QgsVectorLayer(
        "Polygon?crs=EPSG:27700"
        "&field=sssi_name:string(200)"
        "&field=consult_for:string(500)"
        "&field=threshold_for:string(500)",
        _IRZ_HIGHLIGHT_LAYER_NAME, "memory"
    )
    dp = lyr.dataProvider()
    feats = []
    for i, r in enumerate(records):
        f = QgsFeature(lyr.fields())
        f.setGeometry(r["geom"])
        name = r["sssi_name"] or f"Zone {i+1}"
        f.setAttributes([
            name,
            ", ".join(r["consult_types"]),
            ", ".join(r["threshold_types"]),
        ])
        feats.append(f)
    dp.addFeatures(feats)
    lyr.updateExtents()

    # Categorized renderer — one distinct colour per SSSI name so zones are
    # immediately visually distinguishable on the canvas.
    from qgis.core import (
        QgsCategorizedSymbolRenderer, QgsRendererCategory,
        QgsFillSymbol,
    )
    from qgis.PyQt.QtGui import QColor as _QColor

    # Colour palette — lighter semi-transparent fills so underlying map shows through
    _PALETTE = [
        ("#FF980040", "#9A6700"),   # orange
        ("#2196F340", "#234A6E"),   # blue
        ("#4CAF5040", "#175E3B"),   # green
        ("#9C27B040", "#4a148c"),   # purple
        ("#F4433640", "#8F2A23"),   # red
        ("#00BCD440", "#006064"),   # cyan
        ("#FFEB3B50", "#B07D00"),   # yellow
        ("#79554840", "#3e2723"),   # brown
    ]

    unique_names = list(dict.fromkeys(
        r["sssi_name"] or f"Zone {i+1}" for i, r in enumerate(records)
    ))

    categories = []
    for i, name in enumerate(unique_names):
        fill_hex, outline_hex = _PALETTE[i % len(_PALETTE)]
        sym = QgsFillSymbol.createSimple({
            "color":         fill_hex,
            "outline_color": outline_hex,
            "outline_width": "0.6",
            "outline_style": "solid",
        })
        categories.append(QgsRendererCategory(name, sym, name))

    # Catch-all for empty/unmatched names
    default_sym = QgsFillSymbol.createSimple({
        "color":         "#FF980040",
        "outline_color": "#9A6700",
        "outline_width": "0.6",
        "outline_style": "solid",
    })
    categories.append(QgsRendererCategory("", default_sym, "Unknown"))

    renderer = QgsCategorizedSymbolRenderer("sssi_name", categories)
    lyr.setRenderer(renderer)

    # No labels on the canvas — SSSI names shown in the report/legend only
    lyr.setLabelsEnabled(False)

    # Insert above basemaps but below redline
    root = QgsProject.instance().layerTreeRoot()
    bm   = root.findGroup("Base Maps")
    idx  = root.children().index(bm) if bm is not None else 0
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    QgsProject.instance().addMapLayer(lyr, False)
    root.insertLayer(idx, lyr)

    # Also add the DSP WMS overlay for context (all IRZ zones visible)
    _load_irz_wms_overlay()

    return True, records


def _load_irz_wms_overlay():
    """Add the IRZ WMS tile layer for background context (all zones, not just
    those intersecting the redline)."""
    from qgis.core import QgsRasterLayer, QgsProject
    WMS_LAYER_NAME = "SSSI IRZ — WMS Overlay"
    # Remove stale instance
    for old in QgsProject.instance().mapLayersByName(WMS_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    wms_url = _DS_BASE_WMS.format(slug=_IRZ_WFS_SLUG)
    uri = (
        f"crs=EPSG:27700&format=image/png&layers=ne_sssi_impact_risk_zo"
        f"&styles=&url={wms_url}&version=1.3.0"
    )
    lyr = QgsRasterLayer(uri, WMS_LAYER_NAME, "wms")
    if not lyr.isValid():
        return  # Non-fatal — highlight layer is enough
    # Desk-study overlays are never persisted (see bng_plugin's session-only
    # strip) — WMS layers need this explicit tag since they aren't memory.
    lyr.setCustomProperty("biome_session_only", 1)

    root = QgsProject.instance().layerTreeRoot()
    bm   = root.findGroup("Base Maps")
    idx  = root.children().index(bm) if bm is not None else 0
    QgsProject.instance().addMapLayer(lyr, False)
    root.insertLayer(idx + 1, lyr)   # just above basemap, below highlight


def remove_irz_layers():
    """Hide both IRZ layers (keep in project for instant re-display)."""
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    for name in (_IRZ_HIGHLIGHT_LAYER_NAME, "SSSI IRZ — WMS Overlay"):
        for lyr in QgsProject.instance().mapLayersByName(name):
            node = root.findLayer(lyr.id())
            if node is not None:
                node.setItemVisibilityChecked(False)
