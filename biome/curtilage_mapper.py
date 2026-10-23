# curtilage_mapper.py
"""
Enhancement Availability mapper for Biome.

Illustration-only sibling of building_tree_mapper.py: a single polygon layer
showing which parts of a scheme are residential curtilage or developed land
(unavailable for BNG enhancement) and which areas remain available.  Kept
deliberately OUTSIDE the BNG habitat system — nothing here feeds any
calculation, summary, balance check or metric export, and it must never
influence the Enhance tab's curtilage question.

Three ways areas arrive on the layer (the `source` field):
  - "drawn"    — residential-curtilage polygons digitised by hand;
  - "detected" — proposed-layer parcels auto-classified (gardens → curtilage,
                 sealed/built land → developed), replaced on every re-run;
  - "autofill" — the remainder of the redline marked available, replaced on
                 every re-run.

All geometry is stored in EPSG:27700 (British National Grid), matching the
Building & Tree Mapper layers.  The layer is memory-backed and persisted to
the project GeoPackage via the same scratch-layer tags.
"""

from .building_tree_mapper import (
    _tag_for_persistence, _ensure_group, _remove_layer, _cleanup_group,
)

AVAIL_LAYER_NAME = "Enhancement Availability (Map Only)"

CAT_CURTILAGE = "curtilage"
CAT_DEVELOPED = "developed"
CAT_AVAILABLE = "available"

SRC_DRAWN    = "drawn"
SRC_DETECTED = "detected"
SRC_AUTOFILL = "autofill"

# Proposed sub_habitat_type values marked "developed" (unavailable) by the
# detect scan — sealed/built land that cannot host any enhancement.
DEVELOPED_SUBTYPES = {
    "Developed land; sealed surface",
    "Artificial unvegetated, unsealed surface",
    "Built linear features",
}
# Garden parcels are ambiguous: on some schemes they ARE the new residential
# curtilage, on others they're just soft landscaping.  The UI asks the user
# (popup in Detect/Fill) and passes the answer to detect_from_proposal —
# never auto-classified (whole sites turned terracotta, Tom 2026-07-30).
GARDEN_SUBTYPES = {
    "Vegetated garden",
    "Unvegetated garden",
}

# Geometric-difference slivers below this ellipsoidal area are discarded.
_SLIVER_M2 = 0.5


# ── Layer ───────────────────────────────────────────────────────────────────

def get_or_create_availability_layer():
    """Return the availability polygon layer, creating it (and the group) if
    absent.  The layer is appended at the END of the Building & Tree Mapper
    group so it renders underneath buildings and trees on the canvas.

    MULTIPOLYGON, not Polygon: detect unions and fill remainders are routinely
    multipart, and committing a MultiPolygon into a single-part layer FAILS
    ("geometry type is not compatible") leaving the layer editable+dirty —
    which broke Fill and hard-crashed QGIS 4.2 when the dirty layer was
    removed (rollback-during-removal access violation, 2026-07-30)."""
    from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

    project = QgsProject.instance()
    for lyr in project.mapLayersByName(AVAIL_LAYER_NAME):
        if lyr.geometryType() == 2:      # Polygon
            if QgsWkbTypes.isSingleType(lyr.wkbType()):
                # Legacy single-part layer from the first release day —
                # replace it with a MultiPolygon twin carrying its features.
                return _upgrade_to_multi(project, lyr)
            _tag_for_persistence(lyr, "availability_map_only")
            return lyr

    lyr = QgsVectorLayer(
        "MultiPolygon?crs=EPSG:27700"
        "&field=category:string(12)"
        "&field=source:string(10)",
        AVAIL_LAYER_NAME, "memory",
    )
    apply_availability_style(lyr)
    lyr.setCustomProperty("biome_map_only", 1)   # tag: never counts in BNG
    _tag_for_persistence(lyr, "availability_map_only")

    project.addMapLayer(lyr, False)
    _ensure_group().addLayer(lyr)                # append → below buildings/trees
    return lyr


def _upgrade_to_multi(project, old_lyr):
    """Replace a legacy single-part availability layer with a MultiPolygon
    twin, copying its committed features.  The old layer is removed safely
    (edit session closed first) via building_tree_mapper helpers."""
    from qgis.core import QgsFeature
    from .building_tree_mapper import _safe_remove_layer

    rows = [(f["category"], f["source"], f.geometry().asWkt(20))
            for f in old_lyr.getFeatures()
            if f.geometry() is not None and not f.geometry().isNull()]
    _safe_remove_layer(project, old_lyr)
    _cleanup_group()

    lyr = get_or_create_availability_layer()   # recurses into the create path
    if rows:
        from qgis.core import QgsGeometry
        feats = []
        for cat, src, wkt in rows:
            g = QgsGeometry.fromWkt(wkt)
            if g is None or g.isNull() or g.isEmpty():
                continue
            g.convertToMultiType()
            f = QgsFeature(lyr.fields())
            f.setGeometry(g)
            f.setAttributes([cat, src])
            feats.append(f)
        if feats:
            lyr.startEditing()
            lyr.addFeatures(feats)
            _commit_or_rollback(lyr)
    return lyr


def apply_availability_style(lyr):
    """Rule-based renderer with one rule per category; the rule labels are
    exactly what prints in the map legend."""
    from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer, QgsRuleBasedRenderer
    from qgis.PyQt.QtGui import QColor
    try:
        root_rule = QgsRuleBasedRenderer.Rule(None)

        def make_rule(label, expr, fill_rgba, stroke_hex, hatch_angle=None,
                      stroke_width=0.5):
            symbol = QgsFillSymbol()
            solid = QgsSimpleFillSymbolLayer()
            solid.setFillColor(QColor(*fill_rgba))
            solid.setStrokeColor(QColor(stroke_hex))
            solid.setStrokeWidth(stroke_width)
            symbol.changeSymbolLayer(0, solid)
            if hatch_angle is not None:
                try:
                    from qgis.core import QgsLinePatternFillSymbolLayer
                    hatch = QgsLinePatternFillSymbolLayer()
                    hatch.setColor(QColor(stroke_hex))
                    hatch.setLineWidth(0.45)
                    hatch.setDistance(2.6)
                    hatch.setLineAngle(hatch_angle)
                    symbol.appendSymbolLayer(hatch)
                except Exception:
                    pass
            rule = QgsRuleBasedRenderer.Rule(symbol)
            rule.setLabel(label)
            rule.setFilterExpression(expr)
            return rule

        root_rule.appendChild(make_rule(
            "Residential curtilage", "\"category\" = 'curtilage'",
            (198, 74, 53, 45), "#B4552D", hatch_angle=45))
        root_rule.appendChild(make_rule(
            "Developed land (proposed)", "\"category\" = 'developed'",
            (117, 117, 117, 40), "#5F6368", hatch_angle=135))
        root_rule.appendChild(make_rule(
            "Available for enhancement", "\"category\" = 'available'",
            (116, 179, 110, 110), "#2E7D32", stroke_width=0.4))
        lyr.setRenderer(QgsRuleBasedRenderer(root_rule))
    except Exception:
        from qgis.core import QgsSingleSymbolRenderer
        symbol = QgsFillSymbol.createSimple({
            "color": "198,74,53,60", "outline_color": "#8E3B22",
            "outline_width": "0.5"})
        lyr.setRenderer(QgsSingleSymbolRenderer(symbol))
    lyr.triggerRepaint()


def assign_drawn_curtilage(lyr, fid):
    """Stamp category/source onto a freshly-digitised feature *fid* still in
    the layer's edit buffer (the freehand-draw path).  The caller commits."""
    i_cat = lyr.fields().indexOf("category")
    i_src = lyr.fields().indexOf("source")
    lyr.changeAttributeValue(fid, i_cat, CAT_CURTILAGE)
    lyr.changeAttributeValue(fid, i_src, SRC_DRAWN)


# ── Geometry helpers ────────────────────────────────────────────────────────

def _polygonal_only(geom):
    """Reduce *geom* to its polygonal parts as a valid Polygon / MultiPolygon.
    Module-level twin of HabitatFormScreen._polygonal_only — GEOS difference()
    against edge-sharing neighbours can return a GeometryCollection with
    dangling boundary lines, or a self-touching invalid polygon; either stored
    verbatim becomes a malformed feature.  Returns the original geom if
    nothing polygonal is found."""
    from qgis.core import QgsGeometry, QgsWkbTypes
    if geom is None or geom.isNull() or geom.isEmpty():
        return geom
    if not geom.isGeosValid():
        fixed = geom.makeValid()
        if fixed and not fixed.isNull() and not fixed.isEmpty():
            geom = fixed
    polys = []
    for g in geom.asGeometryCollection():
        if (g is None or g.isNull() or g.isEmpty()
                or QgsWkbTypes.geometryType(g.wkbType())
                != QgsWkbTypes.GeometryType.PolygonGeometry):
            continue
        if QgsWkbTypes.flatType(g.wkbType()) == QgsWkbTypes.Type.MultiPolygon:
            for ring_set in g.asMultiPolygon():
                polys.append(QgsGeometry.fromPolygonXY(ring_set))
        else:
            polys.append(QgsGeometry(g))
    if not polys:
        return geom
    if len(polys) == 1:
        return polys[0]
    return QgsGeometry.collectGeometry(polys)


def _safe_difference(a, b):
    """a − b with the defensive plumbing the habitat clip flow uses: WKT
    round-trip both operands (provider-read geoms can no-op difference() on a
    GEOS coordinate-space mismatch), and keep *a* whenever GEOS fails."""
    from qgis.core import QgsGeometry
    if a is None or a.isNull() or a.isEmpty():
        return a
    if b is None or b.isNull() or b.isEmpty():
        return a
    aw = QgsGeometry.fromWkt(a.asWkt(20))
    bw = QgsGeometry.fromWkt(b.asWkt(20))
    if aw.isNull() or bw.isNull():
        return a
    if not aw.intersects(bw):
        return a
    result = aw.difference(bw)
    if result is None or result.isNull():
        return a
    return result


def clip_to_redline(geom, redline_geom_27700):
    """Intersection of a drawn polygon with the redline (both EPSG:27700),
    cleaned via _polygonal_only.  Returns the clipped geometry, the original
    geometry when there is no redline to clip against, or None when nothing
    meaningful remains inside the boundary."""
    from qgis.core import QgsGeometry
    if redline_geom_27700 is None:
        return geom
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    a = QgsGeometry.fromWkt(geom.asWkt(20))
    b = QgsGeometry.fromWkt(redline_geom_27700.asWkt(20))
    if a.isNull() or b.isNull():
        return geom
    if not a.intersects(b):
        return None
    inter = a.intersection(b)
    if inter is None or inter.isNull() or inter.isEmpty():
        return None
    inter = _polygonal_only(inter)
    if inter is None or inter.isNull() or inter.isEmpty():
        return None
    try:
        if _availability_da().measureArea(inter) < _SLIVER_M2:
            return None
    except Exception:
        pass
    return inter


def _union_features(lyr, predicate):
    """Combined geometry of every feature matching *predicate(feat)* — a null
    QgsGeometry when none match."""
    from qgis.core import QgsGeometry
    combined = QgsGeometry()
    for f in lyr.getFeatures():
        try:
            if not predicate(f):
                continue
        except Exception:
            continue
        g = f.geometry()
        if g is None or g.isNull() or g.isEmpty():
            continue
        g = QgsGeometry(g)
        if not g.isGeosValid():
            fixed = g.makeValid()
            if fixed and not fixed.isNull() and not fixed.isEmpty():
                g = fixed
        combined = g if combined.isNull() else combined.combine(g)
    return combined


def _availability_da():
    """Ellipsoidal QgsDistanceArea for the availability layer's CRS (27700)."""
    from qgis.core import QgsDistanceArea, QgsProject, QgsCoordinateReferenceSystem
    from .config import measurement_ellipsoid
    da = QgsDistanceArea()
    da.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:27700"),
                    QgsProject.instance().transformContext())
    da.setEllipsoid(measurement_ellipsoid())
    return da


def _commit_or_rollback(lyr):
    """commitChanges that NEVER leaves the layer editable+dirty: a failed
    commit (e.g. incompatible geometry) is rolled back so QGIS can't later
    prompt to save — and then crash rolling back — while the layer is being
    removed.  Returns True when the commit stuck."""
    if lyr.commitChanges():
        return True
    try:
        print("Biome availability: commit failed, rolling back:",
              "; ".join(lyr.commitErrors()))
    except Exception:
        pass
    try:
        lyr.rollBack()
    except Exception:
        pass
    return False


def _replace_features(lyr, remove_fids, new_feats):
    """Delete *remove_fids* and add *new_feats* in one edit session.  All new
    geometries are promoted to MultiPolygon (the layer type).  Returns the
    committed fids of the added features (post-commit set difference —
    edit-buffer fids don't survive commit), or [] when the commit failed."""
    for f in new_feats:
        g = f.geometry()
        if g is not None and not g.isNull():
            g.convertToMultiType()
            f.setGeometry(g)
    pre = {f.id() for f in lyr.getFeatures()}
    if not lyr.isEditable():
        lyr.startEditing()
    if remove_fids:
        lyr.deleteFeatures(list(remove_fids))
    if new_feats:
        lyr.addFeatures(new_feats)
    if not _commit_or_rollback(lyr):
        return []
    lyr.triggerRepaint()
    pre -= set(remove_fids or [])
    return [f.id() for f in lyr.getFeatures() if f.id() not in pre]


# ── Detect from proposal ────────────────────────────────────────────────────

def count_proposal_gardens():
    """Number of garden parcels on the proposed layer (0 when no layer or no
    sub_habitat_type field).  Drives whether the UI asks the curtilage
    question at all."""
    from qgis.core import QgsProject
    prop_lyrs = QgsProject.instance().mapLayersByName("Proposed - Area Habitats")
    if not prop_lyrs:
        return 0
    prop = prop_lyrs[0]
    if prop.fields().indexOf("sub_habitat_type") < 0:
        return 0
    n = 0
    for feat in prop.getFeatures():
        try:
            sub = str(feat["sub_habitat_type"]) if feat["sub_habitat_type"] else ""
        except Exception:
            continue
        if sub in GARDEN_SUBTYPES:
            n += 1
    return n


def detect_from_proposal(gardens_are_curtilage=False):
    """Scan the proposed habitat layer and (re)build the auto-detected
    availability features: sealed/built parcels → developed, and — only when
    *gardens_are_curtilage* (the user's popup answer) — garden parcels →
    curtilage.  Hand-drawn curtilage always takes precedence (subtracted from
    both detected geometries), and detected curtilage is subtracted from
    detected developed so the hatches never overlap.

    Returns None when there is no proposed layer to scan; otherwise
    ``(removed_fids, added_fids, n_garden_parcels_marked, n_developed_parcels)``.
    Re-runnable: previous source='detected' features are always replaced —
    even on zero matches, so stale detections never outlive the proposal."""
    from qgis.core import (
        QgsProject, QgsFeature, QgsGeometry, QgsCoordinateTransform,
        QgsCoordinateReferenceSystem,
    )

    project = QgsProject.instance()
    prop_lyrs = project.mapLayersByName("Proposed - Area Habitats")
    if not prop_lyrs:
        return None
    prop = prop_lyrs[0]

    lyr = get_or_create_availability_layer()
    bng = QgsCoordinateReferenceSystem("EPSG:27700")
    xform = (QgsCoordinateTransform(prop.crs(), bng, project)
             if prop.crs() != bng else None)

    curt_geom, dev_geom = QgsGeometry(), QgsGeometry()
    n_curt = n_dev = 0
    if prop.fields().indexOf("sub_habitat_type") >= 0:
        for feat in prop.getFeatures():
            try:
                sub = str(feat["sub_habitat_type"]) if feat["sub_habitat_type"] else ""
            except Exception:
                continue
            if sub in DEVELOPED_SUBTYPES:
                bucket = "dev"
            elif gardens_are_curtilage and sub in GARDEN_SUBTYPES:
                bucket = "curt"
            else:
                continue
            g = feat.geometry()
            if g is None or g.isNull() or g.isEmpty():
                continue
            g = QgsGeometry(g)
            if xform is not None:
                try:
                    g.transform(xform)
                except Exception:
                    continue
            if not g.isGeosValid():
                fixed = g.makeValid()
                if fixed and not fixed.isNull() and not fixed.isEmpty():
                    g = fixed
            if bucket == "curt":
                n_curt += 1
                curt_geom = g if curt_geom.isNull() else curt_geom.combine(g)
            else:
                n_dev += 1
                dev_geom = g if dev_geom.isNull() else dev_geom.combine(g)

    # Precedence: drawn curtilage beats both detected sets; detected curtilage
    # beats detected developed (no overlapping hatches).
    drawn = _union_features(lyr, lambda f: f["source"] == SRC_DRAWN)
    curt_geom = _safe_difference(curt_geom, drawn)
    dev_geom  = _safe_difference(dev_geom, drawn)
    dev_geom  = _safe_difference(dev_geom, curt_geom)

    da = _availability_da()
    new_feats = []
    for geom, cat in ((curt_geom, CAT_CURTILAGE), (dev_geom, CAT_DEVELOPED)):
        if geom is None or geom.isNull() or geom.isEmpty():
            continue
        geom = _polygonal_only(geom)
        if geom is None or geom.isNull() or geom.isEmpty():
            continue
        try:
            if da.measureArea(geom) < _SLIVER_M2:
                continue
        except Exception:
            pass
        f = QgsFeature(lyr.fields())
        f.setGeometry(geom)
        f.setAttributes([cat, SRC_DETECTED])
        new_feats.append(f)

    removed = [f.id() for f in lyr.getFeatures() if f["source"] == SRC_DETECTED]
    added = _replace_features(lyr, removed, new_feats)
    return removed, added, n_curt, n_dev


# ── Fill remainder ──────────────────────────────────────────────────────────

def fill_remainder(redline_geom_27700):
    """(Re)build the source='autofill' feature: everything inside the redline
    not already curtilage or developed becomes 'Available for enhancement'.

    Returns ``(removed_fids, added_fids, area_ha)`` — an empty remainder is a
    valid outcome (fully-unavailable site) and yields no feature, 0.0 ha."""
    from qgis.core import QgsFeature, QgsGeometry

    lyr = get_or_create_availability_layer()
    removed = [f.id() for f in lyr.getFeatures() if f["source"] == SRC_AUTOFILL]

    avail = QgsGeometry(redline_geom_27700)
    unavailable = _union_features(lyr, lambda f: f["category"] != CAT_AVAILABLE)
    avail = _safe_difference(avail, unavailable)
    if avail is not None and not avail.isNull() and not avail.isEmpty():
        avail = _polygonal_only(avail)

    da = _availability_da()
    new_feats, area_ha = [], 0.0
    if avail is not None and not avail.isNull() and not avail.isEmpty():
        try:
            area_m2 = da.measureArea(avail)
        except Exception:
            area_m2 = 0.0
        if area_m2 >= _SLIVER_M2:
            f = QgsFeature(lyr.fields())
            f.setGeometry(avail)
            f.setAttributes([CAT_AVAILABLE, SRC_AUTOFILL])
            new_feats.append(f)
            area_ha = area_m2 / 10000.0

    added = _replace_features(lyr, removed, new_feats)
    if new_feats and not added:      # commit failed — nothing actually landed
        area_ha = 0.0
    return removed, added, area_ha


# ── Housekeeping / counts ───────────────────────────────────────────────────

def clear_availability():
    _remove_layer(AVAIL_LAYER_NAME)
    _cleanup_group()


def has_availability():
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(AVAIL_LAYER_NAME)
    return bool(lyrs) and any(True for _ in lyrs[0].getFeatures())


def has_autofill():
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(AVAIL_LAYER_NAME)
    if not lyrs:
        return False
    return any(f["source"] == SRC_AUTOFILL for f in lyrs[0].getFeatures())


def count_drawn():
    """Number of hand-drawn curtilage polygons (0 if the layer is absent)."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(AVAIL_LAYER_NAME)
    if not lyrs:
        return 0
    return sum(1 for f in lyrs[0].getFeatures() if f["source"] == SRC_DRAWN)


def available_area_ha():
    """Ellipsoidal hectares of everything marked available (0.0 when none)."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(AVAIL_LAYER_NAME)
    if not lyrs:
        return 0.0
    da = _availability_da()
    total = 0.0
    for f in lyrs[0].getFeatures():
        if f["category"] != CAT_AVAILABLE:
            continue
        g = f.geometry()
        if g is None or g.isNull() or g.isEmpty():
            continue
        try:
            total += da.measureArea(g)
        except Exception:
            pass
    return total / 10000.0


def _last_drawn_feature(lyr):
    """The most recently drawn curtilage feature = highest fid among
    source='drawn' (fid order is placement order; there is no id field)."""
    best, best_fid = None, -1
    for f in lyr.getFeatures():
        if f["source"] != SRC_DRAWN:
            continue
        if f.id() >= best_fid:
            best_fid, best = f.id(), f
    return best


def peek_last_drawn():
    """(layer, fid) of the polygon remove_last_drawn() would delete, or None."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(AVAIL_LAYER_NAME)
    if not lyrs:
        return None
    feat = _last_drawn_feature(lyrs[0])
    return (lyrs[0], feat.id()) if feat is not None else None


def remove_last_drawn():
    """Delete the most recently drawn curtilage polygon.  Returns True if one
    was removed."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(AVAIL_LAYER_NAME)
    if not lyrs:
        return False
    lyr = lyrs[0]
    feat = _last_drawn_feature(lyr)
    if feat is None:
        return False
    if not lyr.isEditable():
        lyr.startEditing()
    lyr.deleteFeature(feat.id())
    if not _commit_or_rollback(lyr):
        return False
    lyr.triggerRepaint()
    return True
