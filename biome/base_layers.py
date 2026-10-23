# base_layers.py
# Base maps and GCN overlay.

import os

from qgis.core import (QgsRasterLayer, QgsVectorLayer, QgsVectorTileLayer,
                       QgsDataSourceUri, QgsProject, QgsMessageLog, Qgis)

# ---------------------------------------------------------------------------
# Base map URIs
# ---------------------------------------------------------------------------
def _google_satellite_uri():
    return (
        "crs=EPSG:3857&format&tilePixelRatio=1"
        "&type=xyz"
        "&url=https://mt1.google.com/vt/lyrs%3Ds%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D"
        "&zmax=19&zmin=0"
    )

def _google_hybrid_uri():
    return (
        "crs=EPSG:3857&format&tilePixelRatio=1"
        "&type=xyz"
        "&url=https://mt1.google.com/vt/lyrs%3Dy%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D"
        "&zmax=19&zmin=0"
    )

def _os_outdoor_uri(api_key):
    return (
        "type=xyz"
        f"&url=https://api.os.uk/maps/raster/v1/zxy/Outdoor_3857/%7Bz%7D/%7Bx%7D/%7By%7D.png?key={api_key}"
        "&zmax=20&zmin=7"
    )


def _os_ngd_tile_uri(api_key, style="outdoor"):
    """QGIS generic vector-tile URI for the OS NGD premium basemap."""
    style_path = {
        "outdoor": "3857",
        "road": "road-3857",
        "light": "light-3857",
        "blackwhite": "blackwhite-3857",
    }.get(style, "3857")
    uri = QgsDataSourceUri()
    uri.setParam("type", "xyz")
    uri.setParam(
        "url", "https://api.os.uk/maps/vector/ngd/ota/v1/collections/"
        f"ngd-base/tiles/3857/{{z}}/{{y}}/{{x}}?key={api_key}")
    uri.setParam("zmin", "6")
    uri.setParam("zmax", "19")
    uri.setParam(
        "styleUrl", "https://api.os.uk/maps/vector/ngd/ota/v1/collections/"
        f"ngd-base/styles/{style_path}")
    encoded = uri.encodedUri()
    return bytes(encoded).decode("utf-8") if not isinstance(encoded, str) else encoded

def _esri_world_imagery_uri():
    return (
        "type=xyz"
        "&url=https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D"
        "&zmax=23&zmin=0"
    )


# ---------------------------------------------------------------------------
# GCN Risk Zones — ArcGIS Online FeatureServer (Natural England)
#
# One FeatureServer per county, hosted at services.arcgis.com/JJzESW51TqeY9uat
# Bounding boxes (EPSG:27700) fetched live from each FeatureServer on 2026-03-17.
# The centroid of the redline boundary is tested against each bbox to find
# which county service to load — only that one service is loaded, giving a
# near-instant response vs the old approach of loading all 17 tile layers.
#
# A bbox is only a PRE-FILTER. Every box also covers big slices of neighbouring
# counties (Kent's takes in East Sussex and south London; Shropshire's contains
# all of Telford), so the actual "is this site in a DLL area?" decision is a
# point-in-polygon test against gcn_dll_coverage.json — the dissolved outline of
# each service's risk zones, built offline by _gen_gcn_coverage.py. Boxes alone
# decide only if that file is missing or unreadable.
#
# Zones (from attribute field "Zone" or "zone_type"):
#   Red    — key GCN populations, SSSIs for GCN
#   Amber  — main population centres, important connecting habitat
#   Green  — sparsely distributed GCN
#   White  — no GCN records (rare)
# ---------------------------------------------------------------------------
_GCN_BASE = "https://services.arcgis.com/JJzESW51TqeY9uat/ArcGIS/rest/services"
_GCN_GROUP_NAME = "GCN Risk Zones"

# Lookup table: (service_name, layer_id, xmin, ymin, xmax, ymax) all in EPSG:27700
# Bounding boxes verified live from FeatureServer/0?f=json (or /1 for N. Somerset)
_GCN_COUNTY_LOOKUP = [
    # service_name,                                            lid  xmin    ymin    xmax    ymax
    ("GCN_Risk_Zones_Cambridgeshire",                          0,  501879, 236168, 571833, 318014),
    ("GCN_Risk_Zones_Cheshire",                                0,  326067, 338994, 399851, 398359),
    ("GCN_Risk_Zones_Cumbria",                                 0,  293943, 463830, 389794, 588518),
    ("GCN_Risk_Zones_Derbyshire",                              0,  405535, 311054, 455677, 388396),
    ("GCN_Risk_Zones_Dorset",                                  0,  332353,  68218, 422554, 131347),
    ("GCN_Risk_Zones_Essex",                                   0,  536935, 175099, 626739, 246583),
    ("GCN_Risk_Zones_Greater_Manchester",                      0,  351662, 381165, 402526, 421038),
    ("GCN_Risk_Zones_Hertfordshire",                           0,  486503, 190027, 551263, 244066),
    ("GCN_Risk_Zones_Kent",                                    0,  542130, 116569, 640169, 179542),
    ("GCN_Risk_Zones_Lancashire",                              0,  330207, 398767, 397132, 482749),
    ("GCN_Risk_Zones_Leicestershire_Rutland_Rushcliffe_and_South_Kesteven",
                                                               0,  427293, 277438, 520977, 352191),
    ("GCN_Risk_Zones_Norfolk_and_Suffolk",                     0,  545677, 231125, 655989, 347460),
    ("GCN_Risk_Zones_North_East_Yorkshire",                    0,  419286, 414112, 516818, 528312),
    ("GCN_Risk_Zones_North_Somerset_and_South_Gloucestershire",1,  322424, 154991, 382608, 197735),
    ("GCN_Risk_Zones_North_West_Yorkshire",                    0,  363191, 408972, 454175, 516378),
    ("GCN_Risk_Zones_Northumberland_Durham_Tyne_and_Wear_Tees_Valley",
                                                               0,  356180, 506231, 454450, 657535),
    # Shropshire bbox corrected 2026-06: the original was hand-estimated and too
    # tight (cut ~23 km off the south, plus ~5 km N / ~4.5 km E), silently
    # excluding southern-Shropshire sites (Ludlow/Craven Arms). The values below
    # are a superset of the live FeatureServer extent (true: 315856 267597 384467 345278).
    ("GCN_Risk_Zones_Shropshire",                              0,  309000, 267500, 384500, 345300),
    ("GCN_Risk_Zones_Somerset",                                0,  300788, 102800, 383044, 159365),
    ("GCN_Risk_Zones_South_East_Yorkshire",                    0,  411860, 378555, 542314, 476833),
    ("GCN_Risk_Zones_Swindon_and_Wiltshire",                   0,  374505, 116176, 435914, 200506),
    ("GCN_Risk_Zones_Telford",                                 0,  355050, 301953, 379019, 325736),
]

# Display name derived from service name (for layer panel)
def _gcn_display_name(service_name):
    return ("GCN Risk Zones — "
            + service_name
              .replace("GCN_Risk_Zones_", "")
              .replace("_", " "))


_GCN_COVERAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "gcn_dll_coverage.json")
_gcn_coverage_cache = None   # {service_name: (layer_id, QgsGeometry, area_km2)}; {} = unusable


def _gcn_coverage():
    """Offline TRUE outlines of every DLL service — the dissolved risk-zone
    polygons in EPSG:27700 — read once from gcn_dll_coverage.json (regenerate
    with _gen_gcn_coverage.py). Returns {} (and logs) if the file is unusable,
    in which case callers fall back to the bounding boxes."""
    global _gcn_coverage_cache
    if _gcn_coverage_cache is None:
        cache = {}
        try:
            import json
            from qgis.core import QgsGeometry, QgsPointXY
            with open(_GCN_COVERAGE_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            for name, rec in data.get("services", {}).items():
                polys = [[[QgsPointXY(x, y) for x, y in ring] for ring in poly]
                         for poly in rec.get("polygons", [])]
                geom = QgsGeometry.fromMultiPolygonXY(polys)
                if geom.isEmpty():
                    continue
                cache[name] = (int(rec.get("layer_id", 0)), geom,
                               float(rec.get("area_km2", 0.0)))
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"GCN coverage outlines unavailable ({exc}) — falling back to bounding boxes",
                "Biome", Qgis.MessageLevel.Warning,
            )
            cache = {}
        _gcn_coverage_cache = cache
    return _gcn_coverage_cache


def _find_gcn_county(easting, northing):
    """Return the (service_name, layer_id) of the DLL service whose risk zones
    actually contain the given BNG point, or None when the point is outside
    every scheme.

    The bbox table only shortlists candidates; the decision is a point-in-polygon
    test against the offline outlines. Where outlines overlap (Shropshire's
    service also spans Telford & Wrekin) the SMALLEST containing service wins,
    i.e. the most specific scheme. A candidate with no outline on file (a
    service added to the table before the JSON was regenerated) counts as a
    bbox-only hit of lowest priority, so a new scheme is never silently ignored.
    """
    candidates = [
        (service_name, lid)
        for service_name, lid, xmin, ymin, xmax, ymax in _GCN_COUNTY_LOOKUP
        if xmin <= easting <= xmax and ymin <= northing <= ymax
    ]
    if not candidates:
        return None
    coverage = _gcn_coverage()
    if not coverage:
        return candidates[0]            # no outlines at all: legacy bbox behaviour

    from qgis.core import QgsGeometry, QgsPointXY
    pt = QgsGeometry.fromPointXY(QgsPointXY(easting, northing))
    hits = []
    for service_name, lid in candidates:
        rec = coverage.get(service_name)
        if rec is None:
            hits.append((float("inf"), service_name, lid))
        elif rec[1].contains(pt):
            hits.append((rec[2], service_name, lid))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    return hits[0][1], hits[0][2]


def gcn_coverage_outlines():
    """[(county_label, service_name, QgsGeometry)] true DLL outlines in
    EPSG:27700 for the coverage overlay — empty if the outline file is
    unavailable (the overlay then draws the bounding boxes instead)."""
    out = []
    for service_name, (_lid, geom, _area) in _gcn_coverage().items():
        label = service_name.replace("GCN_Risk_Zones_", "").replace("_", " ")
        out.append((label, service_name, geom))
    return out


def _style_gcn_layer(layer):
    """Apply Red/Amber/Green categorised styling to the GCN vector layer.

    Field confirmed from live FeatureServer API: 'Risk_Zone'
    Values confirmed: 'Red Zone', 'Amber Zone', 'Green Zone'
    Layer opacity set to 60% so underlying basemap remains visible.
    """
    from qgis.core import (
        QgsCategorizedSymbolRenderer, QgsRendererCategory,
        QgsFillSymbol,
    )
    from qgis.PyQt.QtGui import QColor

    # Confirmed zone values from FeatureServer API (all counties consistent)
    # Fill alpha 130/255 (~51%) gives good visibility without obscuring the basemap
    zone_styles = {
        "Red Zone":   ("#d32f2f", "#8F2A23", 140),
        "Amber Zone": ("#f57c00", "#9A6700", 130),
        "Green Zone": ("#1F7A4D", "#175E3B", 110),
    }

    categories = []
    for zone_val, (fill_hex, outline_hex, alpha) in zone_styles.items():
        fill_color = QColor(fill_hex)
        fill_color.setAlpha(alpha)
        sym = QgsFillSymbol.createSimple({
            "color":         fill_color.name(QColor.NameFormat.HexArgb),
            "outline_color": outline_hex,
            "outline_width": "0.5",
            "outline_style": "solid",
        })
        categories.append(QgsRendererCategory(zone_val, sym, zone_val))

    # Field name confirmed from live API across all 21 county services
    zone_field = "Risk_Zone"

    renderer = QgsCategorizedSymbolRenderer(zone_field, categories)
    layer.setRenderer(renderer)

    # Overall layer opacity — 65% lets the basemap show through
    try:
        layer.setOpacity(0.65)
    except Exception:
        pass

    layer.triggerRepaint()


def _fetch_gcn_features(service_name, layer_id):
    """Fetch all features from a GCN FeatureServer layer as a list of dicts.

    Uses paginated REST queries returning EsriJSON geometry in EPSG:27700.
    Returns a list of {geometry_rings, risk_zone} dicts, or raises on error.
    """
    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode

    base = f"{_GCN_BASE}/{service_name}/FeatureServer/{layer_id}/query"
    all_features = []
    offset = 0
    page   = 2000  # max record count for these services

    while True:
        params = urlencode({
            "where":           "1=1",
            "outFields":       "Risk_Zone",
            "returnGeometry":  "true",
            "outSR":           "27700",   # always get BNG coordinates
            "resultOffset":    offset,
            "resultRecordCount": page,
            "f":               "json",
        })
        resp = urlopen(f"{base}?{params}", timeout=30)
        data = json.loads(resp.read().decode("utf-8"))

        if "error" in data:
            raise RuntimeError(data["error"].get("message", "Unknown API error"))

        features = data.get("features", [])
        all_features.extend(features)

        if not data.get("exceededTransferLimit", False) or not features:
            break
        offset += len(features)

    return all_features


def load_gcn_for_point(easting, northing):
    """Load the GCN Risk Zone layer for the county containing (easting, northing).

    Fetches features directly from the FeatureServer REST API and builds a
    memory layer — avoids all arcgisfeatureserver provider reliability issues.

    Returns (True, layer_name) on success, (False, reason_string) on failure.
    """
    from qgis.core import (
        QgsMessageLog, Qgis, QgsFeature, QgsGeometry, QgsPointXY,
        QgsCoordinateReferenceSystem,
    )

    match = _find_gcn_county(easting, northing)
    if match is None:
        return False, "not_in_dll_area"

    service_name, layer_id = match
    display_name = _gcn_display_name(service_name)

    # Already loaded? Just make visible.
    existing = QgsProject.instance().mapLayersByName(display_name)
    if existing:
        root  = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(_GCN_GROUP_NAME)
        if group is not None:
            group.setItemVisibilityChecked(True)
        return True, display_name

    # ── Fetch features from REST API ─────────────────────────────────────
    try:
        raw_features = _fetch_gcn_features(service_name, layer_id)
    except Exception as exc:
        QgsMessageLog.logMessage(
            f"GCN fetch failed for {service_name}: {exc}",
            "Biome", Qgis.MessageLevel.Warning,
        )
        return False, "load_failed"

    if not raw_features:
        QgsMessageLog.logMessage(
            f"GCN: no features returned for {service_name}",
            "Biome", Qgis.MessageLevel.Warning,
        )
        return False, "load_failed"

    # ── Build memory layer ───────────────────────────────────────────────
    lyr = QgsVectorLayer(
        "Polygon?crs=EPSG:27700&field=Risk_Zone:string(20)",
        display_name, "memory",
    )
    dp = lyr.dataProvider()
    qgs_features = []

    for feat in raw_features:
        geom_data  = feat.get("geometry", {})
        risk_zone  = feat.get("attributes", {}).get("Risk_Zone", "")
        rings      = geom_data.get("rings", [])
        if not rings:
            continue

        # Build QgsGeometry from esriJSON rings
        # Each ring is a list of [x, y] pairs in BNG
        qgs_rings = []
        for ring in rings:
            qgs_rings.append([QgsPointXY(pt[0], pt[1]) for pt in ring])

        geom = QgsGeometry.fromPolygonXY(qgs_rings)
        if geom.isEmpty():
            continue

        f = QgsFeature(lyr.fields())
        f.setGeometry(geom)
        f.setAttribute("Risk_Zone", risk_zone)
        qgs_features.append(f)

    dp.addFeatures(qgs_features)
    lyr.updateExtents()

    _style_gcn_layer(lyr)

    # ── Add to project under the GCN group ───────────────────────────────
    root  = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(_GCN_GROUP_NAME)
    if group is None:
        bm_group   = root.findGroup("Base Maps")
        insert_idx = root.children().index(bm_group) if bm_group is not None else 0
        group = root.insertGroup(insert_idx, _GCN_GROUP_NAME)
        group.setExpanded(False)

    lyr.setCustomProperty("skipMemoryLayersCheck", 1)   # session-only scratch
    QgsProject.instance().addMapLayer(lyr, False)
    group.addLayer(lyr).setItemVisibilityChecked(True)
    group.setItemVisibilityChecked(True)
    group.setExpanded(False)

    return True, display_name


def remove_gcn_layers():
    """Hide (don't delete) all GCN layers."""
    root  = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(_GCN_GROUP_NAME)
    if group is not None:
        group.setItemVisibilityChecked(False)


# ---------------------------------------------------------------------------
# GCN eDNA Pond Surveys (England) — single national NE FeatureServer
#
# Natural England publishes every eDNA pond survey carried out for District
# Level Licensing as ONE national point layer (GCN present / absent /
# inconclusive). Unlike the risk zones it covers the whole of England, so it
# is useful even where a site sits outside the 21 DLL risk-zone areas. We load
# only the ponds within `radius_m` of the redline to keep it light, and the
# layer rides along on the GCN Risk Zones print layout when left visible.
# ---------------------------------------------------------------------------
_EDNA_SERVICE    = "GCN_eDNA_Pond_Surveys_for_DLL_England"
_EDNA_LAYER_ID   = 0
_EDNA_LAYER_NAME = "GCN eDNA Pond Surveys"
_EDNA_GROUP_NAME = "GCN eDNA Pond Surveys"


# Category keys + display labels for the eDNA layer, in draw order. Used by the
# styler and by the GCN-risk-zones-map "which results to include" dialog.
EDNA_CATEGORIES = [
    ("present",      "GCN present"),
    ("absent",       "GCN absent"),
    ("inconclusive", "Inconclusive (unconfirmed)"),
]


def _style_edna_layer(layer, selected=None):
    """Categorised point styling on GCN_occ: present (green, emphasised),
    absent (slate), inconclusive (amber).

    If `selected` is given (a subset of the category keys), only those
    categories are drawn — used to drop e.g. unconfirmed results from the GCN
    risk zones map. The legend follows the renderer, so map and key stay in sync.
    """
    from qgis.core import (
        QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsMarkerSymbol,
    )
    # White outlines so the points read clearly over satellite imagery; present
    # ponds (the key result) are a vivid green and a touch larger.
    cats_def = [
        ("present",      "GCN present",  "#16A34A", "3.2"),
        ("absent",       "GCN absent",   "#607D8B", "2.6"),
        ("inconclusive", "Inconclusive", "#F5A623", "2.6"),
    ]
    categories = []
    for val, label, fill, size in cats_def:
        if selected is not None and val not in selected:
            continue
        sym = QgsMarkerSymbol.createSimple({
            "name":          "circle",
            "color":         fill,
            "outline_color": "#FFFFFF",
            "outline_width": "0.6",
            "size":          size,
        })
        categories.append(QgsRendererCategory(val, sym, label))
    layer.setRenderer(QgsCategorizedSymbolRenderer("GCN_occ", categories))
    layer.triggerRepaint()


def set_edna_categories(selected):
    """Rebuild the loaded eDNA layer's renderer to show only the `selected`
    GCN_occ categories (subset of EDNA_CATEGORIES keys). Returns the layer, or
    None if the eDNA layer isn't loaded."""
    lyrs = QgsProject.instance().mapLayersByName(_EDNA_LAYER_NAME)
    if not lyrs:
        return None
    _style_edna_layer(lyrs[0], selected)
    return lyrs[0]


def _fetch_edna_ponds(xmin, ymin, xmax, ymax):
    """Fetch eDNA pond survey points intersecting a BNG envelope from the NE
    FeatureServer. Returns a list of EsriJSON feature dicts (point geometry in
    EPSG:27700) or raises on error."""
    import json
    from urllib.request import urlopen
    from urllib.parse import urlencode

    base = f"{_GCN_BASE}/{_EDNA_SERVICE}/FeatureServer/{_EDNA_LAYER_ID}/query"
    envelope = json.dumps({
        "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
        "spatialReference": {"wkid": 27700},
    })
    all_features = []
    offset = 0
    page   = 2000
    while True:
        params = urlencode({
            "where":             "1=1",
            "geometry":          envelope,
            "geometryType":      "esriGeometryEnvelope",
            "inSR":              "27700",
            "spatialRel":        "esriSpatialRelIntersects",
            "outFields":         "GCN_occ,HSI_Score,HSI_Cat,YearSurvey",
            "returnGeometry":    "true",
            "outSR":             "27700",
            "resultOffset":      offset,
            "resultRecordCount": page,
            "f":                 "json",
        })
        resp = urlopen(f"{base}?{params}", timeout=30)
        data = json.loads(resp.read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "Unknown API error"))
        feats = data.get("features", [])
        all_features.extend(feats)
        if not data.get("exceededTransferLimit", False) or not feats:
            break
        offset += len(feats)
    return all_features


def load_edna_ponds_for_geom(redline_geom, radius_m=None):
    """Load GCN eDNA pond survey points within `radius_m` of the redline.

    `redline_geom` is a QgsGeometry in EPSG:27700 (BNG). Points are fetched
    using the redline bounding box expanded by `radius_m`, then filtered
    precisely to those within `radius_m` of the redline geometry.

    When `radius_m` is None the configured "edna_ponds" desk study search radius
    is used (see config.DESK_SEARCH_RADII).

    Returns (True, layer_name) on success, (False, reason_string) on failure.
    """
    from qgis.core import (
        QgsMessageLog, Qgis, QgsFeature, QgsGeometry, QgsPointXY,
    )

    if radius_m is None:
        try:
            from .config import get_search_radius
            radius_m = get_search_radius("edna_ponds")
        except Exception:
            radius_m = 2000

    if redline_geom is None or redline_geom.isEmpty():
        return False, "no_redline"

    root = QgsProject.instance().layerTreeRoot()

    # Already loaded? Just make visible.
    existing = QgsProject.instance().mapLayersByName(_EDNA_LAYER_NAME)
    if existing:
        node = root.findLayer(existing[0].id())
        if node is not None:
            node.setItemVisibilityChecked(True)
        grp = root.findGroup(_EDNA_GROUP_NAME)
        if grp is not None:
            grp.setItemVisibilityChecked(True)
        return True, _EDNA_LAYER_NAME

    bbox = redline_geom.boundingBox()
    try:
        raw = _fetch_edna_ponds(
            bbox.xMinimum() - radius_m, bbox.yMinimum() - radius_m,
            bbox.xMaximum() + radius_m, bbox.yMaximum() + radius_m,
        )
    except Exception as exc:
        QgsMessageLog.logMessage(f"eDNA fetch failed: {exc}", "Biome", Qgis.MessageLevel.Warning)
        return False, "load_failed"

    lyr = QgsVectorLayer(
        "Point?crs=EPSG:27700"
        "&field=GCN_occ:string(20)&field=HSI_Cat:string(40)"
        "&field=HSI_Score:double&field=YearSurvey:string(10)",
        _EDNA_LAYER_NAME, "memory",
    )
    dp = lyr.dataProvider()
    feats = []
    for feat in raw:
        g = feat.get("geometry", {})
        if "x" not in g or "y" not in g:
            continue
        pgeom = QgsGeometry.fromPointXY(QgsPointXY(g["x"], g["y"]))
        if redline_geom.distance(pgeom) > radius_m:
            continue
        a = feat.get("attributes", {})
        f = QgsFeature(lyr.fields())
        f.setGeometry(pgeom)
        f.setAttribute("GCN_occ", (a.get("GCN_occ") or "").strip().lower())
        f.setAttribute("HSI_Cat", a.get("HSI_Cat") or "")
        hsi = a.get("HSI_Score")
        f.setAttribute("HSI_Score", float(hsi) if hsi not in (None, "") else None)
        f.setAttribute("YearSurvey", str(a.get("YearSurvey") or ""))
        feats.append(f)

    if not feats:
        return False, "none_within_radius"

    dp.addFeatures(feats)
    lyr.updateExtents()
    _style_edna_layer(lyr)

    # Add under a dedicated group above Base Maps (kept separate from the GCN
    # Risk Zones group so the risk-zone opacity slider and toggle stay decoupled).
    group = root.findGroup(_EDNA_GROUP_NAME)
    if group is None:
        bm_group   = root.findGroup("Base Maps")
        insert_idx = root.children().index(bm_group) if bm_group is not None else 0
        group = root.insertGroup(insert_idx, _EDNA_GROUP_NAME)
        group.setExpanded(False)

    lyr.setCustomProperty("skipMemoryLayersCheck", 1)   # session-only scratch
    QgsProject.instance().addMapLayer(lyr, False)
    group.addLayer(lyr).setItemVisibilityChecked(True)
    group.setItemVisibilityChecked(True)
    group.setExpanded(False)
    return True, _EDNA_LAYER_NAME


def remove_edna_layers():
    """Hide (don't delete) the eDNA pond survey layer."""
    root  = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(_EDNA_GROUP_NAME)
    if group is not None:
        group.setItemVisibilityChecked(False)
        return
    for lyr in QgsProject.instance().mapLayersByName(_EDNA_LAYER_NAME):
        node = root.findLayer(lyr.id())
        if node is not None:
            node.setItemVisibilityChecked(False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_layer_opacity(layer, opacity):
    try:
        layer.setOpacity(opacity)
    except AttributeError:
        pass


def _get_or_create_group(root, name, at_bottom=True, collapsed=True):
    group = root.findGroup(name)
    if group is not None:
        return group
    if at_bottom:
        group = root.addGroup(name)
    else:
        bm_group   = root.findGroup("Base Maps")
        insert_idx = root.children().index(bm_group) if bm_group is not None else len(root.children())
        group      = root.insertGroup(insert_idx, name)
    group.setExpanded(False)
    return group


def _ensure_raster_in_group(group, name, uri, provider="wms", visible=True, opacity=1.0):
    # Dedupe strictly by NAME. Comparing source strings is unreliable: QGIS
    # re-encodes WMS/XYZ URIs when saving a project, so a reopened project's
    # layer.source() never string-matches the raw URI we build — which used
    # to make this code remove + recreate layers and, when a project open
    # raced the scaffold, leave duplicate (often broken/italic) copies.
    # Rules now:
    #   • keep the first VALID layer with this name; remove every other copy
    #     (extra duplicates and invalid placeholders from a reopened project)
    #   • if no valid copy exists, remove the broken ones and recreate fresh
    proj = QgsProject.instance()
    existing = proj.mapLayersByName(name)
    keeper = next((l for l in existing if l.isValid()), None)
    for lyr in existing:
        if lyr is not keeper:
            proj.removeMapLayer(lyr.id())
    if keeper is not None:
        # Ensure the surviving copy actually has a node in the layer tree
        root = proj.layerTreeRoot()
        if root.findLayer(keeper.id()) is None:
            group.addLayer(keeper).setItemVisibilityChecked(visible)
        return
    layer = QgsRasterLayer(uri, name, provider)
    if not layer.isValid():
        QgsMessageLog.logMessage(f"Warning: could not load '{name}'.", "Biome", Qgis.MessageLevel.Warning)
        return
    _set_layer_opacity(layer, opacity)
    QgsProject.instance().addMapLayer(layer, False)
    group.addLayer(layer).setItemVisibilityChecked(visible)


def _ensure_os_ngd_in_group(group, api_key, style="outdoor", visible=False):
    """Create the styled OS NGD premium vector-tile basemap."""
    name = "OS Premium"
    layer = QgsVectorTileLayer(_os_ngd_tile_uri(api_key, style), name)
    if not layer.isValid():
        QgsMessageLog.logMessage(
            "Could not create the OS NGD premium vector-tile layer.",
            "Biome", Qgis.MessageLevel.Warning)
        return False
    try:
        _message, style_ok = layer.loadDefaultStyle()
        if not style_ok:
            QgsMessageLog.logMessage(
                "OS Premium loaded but its selected style could not be applied.",
                "Biome", Qgis.MessageLevel.Warning)
    except Exception as exc:
        QgsMessageLog.logMessage(
            f"OS Premium style could not be applied: {exc}",
            "Biome", Qgis.MessageLevel.Warning)
    QgsProject.instance().addMapLayer(layer, False)
    group.addLayer(layer).setItemVisibilityChecked(visible)
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_base_layers(api_key):
    root  = QgsProject.instance().layerTreeRoot()
    group = _get_or_create_group(root, "Base Maps", at_bottom=True, collapsed=False)
    # OS Outdoor needs a user-supplied OS Data Hub API key. Without one the WMS
    # tiles 403 and the layer is dead weight, so we only add it when a key is
    # present — the OS Outdoor basemap button prompts for and stores the key,
    # then calls ensure_os_outdoor() to add the layer on demand.
    if api_key:
        _ensure_raster_in_group(group, "OS Outdoor",     _os_outdoor_uri(api_key),       provider="wms", visible=False)
    _ensure_raster_in_group(group, "Google Hybrid",      _google_hybrid_uri(),           provider="wms", visible=True)
    _ensure_raster_in_group(group, "Google Satellite",   _google_satellite_uri(),        provider="wms", visible=False)
    _ensure_raster_in_group(group, "ESRI World Imagery", _esri_world_imagery_uri(),      provider="wms", visible=False)


def ensure_os_outdoor(api_key):
    """Add (or refresh) the OS Outdoor basemap, keyed with the given OS Data Hub
    API key. Called by the OS Outdoor basemap button once a key is available.

    Any existing OS Outdoor layer is removed first so a newly entered key always
    takes effect. Returns True if the layer is present afterwards, else False.
    """
    if not api_key:
        return False
    proj = QgsProject.instance()
    for lyr in proj.mapLayersByName("OS Outdoor"):
        proj.removeMapLayer(lyr.id())
    root  = proj.layerTreeRoot()
    group = _get_or_create_group(root, "Base Maps", at_bottom=True, collapsed=False)
    _ensure_raster_in_group(group, "OS Outdoor", _os_outdoor_uri(api_key), provider="wms", visible=False)
    return bool(proj.mapLayersByName("OS Outdoor"))


def ensure_os_ngd_premium(api_key, style="outdoor"):
    """Add or refresh the OS NGD premium vector-tile basemap."""
    if not api_key:
        return False
    proj = QgsProject.instance()
    was_visible = False
    root = proj.layerTreeRoot()
    for layer in list(proj.mapLayersByName("OS Premium")):
        node = root.findLayer(layer.id())
        was_visible = was_visible or (node is not None and node.isVisible())
        proj.removeMapLayer(layer.id())
    group = _get_or_create_group(root, "Base Maps", at_bottom=True, collapsed=False)
    return _ensure_os_ngd_in_group(
        group, api_key, style=style, visible=was_visible)
