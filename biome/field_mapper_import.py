"""
field_mapper_import.py
Ingests a Biome Field Mapper survey back into the project.

Handles everything the app can capture:

  * ecological features — points, LINES and AREAS. The previous importer read
    ``coords[0], coords[1]`` as a lon/lat pair, which for a LineString is two
    coordinate *lists*, so every line and polygon drawn in the field was
    silently counted as "skipped".
  * habitat parcels — area / hedgerow / watercourse / trees, with the metric
    attributes the app collects (condition, strategic significance,
    irreplaceable flag, channel and riparian encroachment).
  * condition assessments — auto-linked to the baseline parcel they sit inside
    when that is unambiguous, otherwise parked in their own layer for manual
    linking.
  * GCN pond HSI records.
  * photos — from a .zip, or carried inline when the phone pushes straight to
    QGIS.

Accepts both the v3 payload and the older ``{feature_type, label, photo,
timestamp}`` properties, so surveys exported by an earlier build still import.
"""

import base64
import os
import uuid as _uuid

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsField, QgsGeometry, QgsPointXY, QgsProject, QgsVectorFileWriter,
    QgsVectorLayer, QgsWkbTypes,
)

try:
    from qgis.PyQt.QtCore import QMetaType
    _STR = QMetaType.Type.QString
    _DBL = QMetaType.Type.Double
    _INT = QMetaType.Type.Int
except Exception:                                     # pragma: no cover - Qt5
    from qgis.PyQt.QtCore import QVariant
    _STR, _DBL, _INT = QVariant.String, QVariant.Double, QVariant.Int

from .gridref_dialog import (
    LAYER_NAME, LINE_LAYER_NAME, POLY_LAYER_NAME, LAYER_CRS,
)
from .layer_manager import (
    DEFAULT_STRATEGIC_SIGNIFICANCE, tree_size_from_sub_type,
    tree_location_from_sub_type,
)

TREE_TYPES = {
    "Small Urban Tree", "Medium Urban Tree", "Large Urban Tree",
    "Very Large Urban Tree", "Small Rural Tree", "Medium Rural Tree",
    "Large Rural Tree", "Very Large Rural Tree",
}

CONDITION_LAYER_NAME = "Field Condition Assessments"
HSI_LAYER_NAME = "Pond HSI (Field)"
FIELD_GROUP = "Field Survey"

# Habitat module -> LayerManager key. Field surveys record the BASELINE only;
# a proposed habitat is a design decision, not an observation.
_HABITAT_LAYER_KEY = {
    "area": "baseline_area",
    "hedgerow": "baseline_hedgerow",
    "watercourse": "baseline_water",
    "trees": "baseline_trees",
}

_ECO_LAYER_SPEC = {
    "point": (LAYER_NAME, "eco_points", "Point"),
    "line": (LINE_LAYER_NAME, "eco_lines", "LineString"),
    "area": (POLY_LAYER_NAME, "eco_polygons", "Polygon"),
}

_ECO_FIELDS = [
    ("label", _STR), ("gridref", _STR), ("ftype", _STR), ("uuid", _STR),
    ("photo_path", _STR), ("field_survey_id", _STR), ("source", _STR),
    ("fov_color", _STR), ("bearing", _DBL), ("hide_label", _INT),
    ("label_curved", _INT),
]

_CONDITION_FIELDS = [
    ("uuid", _STR), ("sub_habitat_type", _STR), ("habitat_type", _STR),
    ("condition", _STR), ("condition_detail", _STR), ("report", _STR),
    ("surveyor", _STR), ("recorded", _STR), ("gridref", _STR),
    ("photo_path", _STR), ("linked", _STR),
]

_HSI_FIELDS = [
    ("uuid", _STR), ("pond_name", _STR), ("hsi_score", _DBL),
    ("hsi_band", _STR), ("hsi_detail", _STR), ("surveyor", _STR),
    ("recorded", _STR), ("gridref", _STR), ("photo_path", _STR),
]


# ── small helpers ────────────────────────────────────────────────────────────

def _wgs84():
    """WGS84 forced to lon/lat axis order — GeoJSON is always [lon, lat]."""
    crs = QgsCoordinateReferenceSystem()
    crs.createFromProj("+proj=longlat +datum=WGS84 +no_defs")
    if not crs.isValid():
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
    return crs


def _set(feat, layer, name, value):
    idx = layer.fields().indexFromName(name)
    if idx >= 0:
        feat.setAttribute(idx, value)


def _gpkg_path():
    proj_path = QgsProject.instance().fileName()
    if not proj_path:
        return None
    proj_dir = os.path.dirname(proj_path)
    proj_name = os.path.splitext(os.path.basename(proj_path))[0]
    return os.path.join(proj_dir, "%s_bng_data.gpkg" % proj_name)


def _gpkg_has_table(gpkg, table):
    probe = QgsVectorLayer("%s|layername=%s" % (gpkg, table), "probe", "ogr")
    return probe.isValid()


def _geometry_from_geojson(geom_dict):
    """Build a WGS84 QgsGeometry from a GeoJSON geometry dict.

    Returns (geometry, kind) where kind is 'point' | 'line' | 'area', or
    (None, None) when the geometry is missing or malformed.
    """
    if not isinstance(geom_dict, dict):
        return None, None
    gtype = str(geom_dict.get("type") or "")
    coords = geom_dict.get("coordinates")
    if not coords:
        return None, None
    try:
        if gtype == "Point":
            return QgsGeometry.fromPointXY(
                QgsPointXY(float(coords[0]), float(coords[1]))), "point"
        if gtype == "LineString":
            pts = [QgsPointXY(float(c[0]), float(c[1])) for c in coords]
            if len(pts) < 2:
                return None, None
            return QgsGeometry.fromPolylineXY(pts), "line"
        if gtype == "Polygon":
            rings = []
            for ring in coords:
                pts = [QgsPointXY(float(c[0]), float(c[1])) for c in ring]
                if len(pts) >= 3:
                    rings.append(pts)
            if not rings:
                return None, None
            return QgsGeometry.fromPolygonXY(rings), "area"
        if gtype == "MultiPolygon":
            polys = []
            for poly in coords:
                rings = []
                for ring in poly:
                    pts = [QgsPointXY(float(c[0]), float(c[1])) for c in ring]
                    if len(pts) >= 3:
                        rings.append(pts)
                if rings:
                    polys.append(rings)
            if not polys:
                return None, None
            return QgsGeometry.fromMultiPolygonXY(polys), "area"
        if gtype == "MultiLineString":
            lines = []
            for line in coords:
                pts = [QgsPointXY(float(c[0]), float(c[1])) for c in line]
                if len(pts) >= 2:
                    lines.append(pts)
            if not lines:
                return None, None
            return QgsGeometry.fromMultiPolylineXY(lines), "line"
    except (TypeError, ValueError, IndexError):
        return None, None
    return None, None


def _kind_of(props, geom_kind):
    """Classify a feature, tolerating the pre-v3 export that had no kind."""
    kind = str(props.get("biome_kind") or "").strip()
    if kind in ("eco", "habitat", "condition", "hsi"):
        return kind
    ftype = str(props.get("feature_type") or "")
    if ftype in TREE_TYPES:
        return "habitat"
    return "eco"


def _module_of(props, geom_kind):
    mod = str(props.get("module") or "").strip()
    if mod in _HABITAT_LAYER_KEY:
        return mod
    # Pre-v3 exports only ever carried trees as habitats.
    if str(props.get("feature_type") or "") in TREE_TYPES:
        return "trees"
    return {"point": "trees", "line": "hedgerow", "area": "area"}.get(geom_kind, "area")


# ── layer plumbing ───────────────────────────────────────────────────────────

def _ensure_group(name):
    root = QgsProject.instance().layerTreeRoot()
    grp = root.findGroup(name)
    if grp is None:
        grp = root.insertGroup(0, name)
    return grp


def _open_or_create_gpkg_layer(display_name, table, wkb, fields, group_name):
    """Return a GPKG-backed layer, creating the table and adding any missing
    fields. Falls back to a memory layer when the project is unsaved so a
    survey is never refused just because the user has not saved yet."""
    proj = QgsProject.instance()

    for lyr in proj.mapLayers().values():
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            continue
        src = lyr.dataProvider().dataSourceUri() or ""
        if lyr.name() == display_name or ("layername=%s" % table) in src:
            _add_missing_fields(lyr, fields)
            return lyr

    gpkg = _gpkg_path()
    lyr = None
    if gpkg:
        if not os.path.exists(gpkg) or not _gpkg_has_table(gpkg, table):
            mem = QgsVectorLayer("%s?crs=%s" % (wkb, LAYER_CRS.authid()),
                                 display_name, "memory")
            mem.dataProvider().addAttributes([QgsField(n, t) for n, t in fields])
            mem.updateFields()
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = table
            if os.path.exists(gpkg):
                opts.actionOnExistingFile = \
                    QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            err, msg, _a, _b = QgsVectorFileWriter.writeAsVectorFormatV3(
                mem, gpkg, proj.transformContext(), opts)
            if err != QgsVectorFileWriter.WriterError.NoError:
                lyr = None
            del mem
        if lyr is None:
            candidate = QgsVectorLayer("%s|layername=%s" % (gpkg, table),
                                       display_name, "ogr")
            if candidate.isValid():
                lyr = candidate

    if lyr is None:
        lyr = QgsVectorLayer("%s?crs=%s" % (wkb, LAYER_CRS.authid()),
                             display_name, "memory")
        lyr.dataProvider().addAttributes([QgsField(n, t) for n, t in fields])
        lyr.updateFields()
        # Tag so the scratch-layer machinery persists it into the project GPKG
        # when the user eventually saves.
        lyr.setCustomProperty("biome_scratch", 1)
        lyr.setCustomProperty("biome_gpkg_table", table)

    _add_missing_fields(lyr, fields)
    proj.addMapLayer(lyr, False)
    group = _ensure_group(group_name)
    node = group.addLayer(lyr)
    if node is not None:
        node.setItemVisibilityChecked(True)
    group.setExpanded(True)
    return lyr


def _add_missing_fields(layer, fields):
    existing = {f.name() for f in layer.fields()}
    to_add = [QgsField(n, t) for n, t in fields if n not in existing]
    if not to_add:
        return
    layer.startEditing()
    layer.dataProvider().addAttributes(to_add)
    layer.updateFields()
    # An uncommitted edit session leaves the layer dirty, and removing a dirty
    # layer later crashes QGIS — always resolve it.
    if not layer.commitChanges():
        layer.rollBack()


# ── photos ───────────────────────────────────────────────────────────────────

def _photo_dir():
    proj_path = QgsProject.instance().fileName()
    base = os.path.dirname(proj_path) if proj_path else os.path.expanduser("~")
    out = os.path.join(base, "field_photos")
    try:
        os.makedirs(out, exist_ok=True)
    except OSError:
        return None
    return out


def _write_inline_photos(photo_map):
    """Write base64 data-URL photos to disk. Returns {name: absolute path}."""
    if not photo_map:
        return {}
    out_dir = _photo_dir()
    if not out_dir:
        return {}
    written = {}
    for name, data_url in photo_map.items():
        try:
            raw = data_url.split(",", 1)[1] if "," in data_url else data_url
            blob = base64.b64decode(raw)
        except Exception:
            continue
        safe = os.path.basename(str(name)) or "photo.jpg"
        dest = os.path.join(out_dir, safe)
        stem, ext = os.path.splitext(dest)
        n = 1
        while os.path.exists(dest):
            n += 1
            dest = "%s_%d%s" % (stem, n, ext)
        try:
            with open(dest, "wb") as fh:
                fh.write(blob)
            written[name] = os.path.normpath(dest)
        except OSError:
            continue
    return written


def _resolve_photos(props, survey_dir, inline_paths):
    """Return a ';'-joined list of on-disk photo paths for this feature."""
    names = []
    joined = str(props.get("photos") or "").strip()
    if joined:
        names = [n for n in joined.split("|") if n]
    single = str(props.get("photo") or "").strip()
    if single and single not in names:
        names.append(single)

    paths = []
    for n in names:
        if n in inline_paths:
            paths.append(inline_paths[n])
            continue
        if survey_dir:
            candidate = os.path.join(survey_dir, n)
            if os.path.isfile(candidate):
                paths.append(os.path.normpath(candidate))
    return ";".join(paths)


# ── writers ──────────────────────────────────────────────────────────────────

def _write_eco(layer, geom, props, photo_path, survey_id):
    feat = QgsFeature(layer.fields())
    feat.setGeometry(geom)
    _set(feat, layer, "label", props.get("label") or "")
    _set(feat, layer, "gridref", props.get("gridref") or "")
    _set(feat, layer, "ftype", props.get("feature_type") or "")
    _set(feat, layer, "uuid", props.get("uuid") or str(_uuid.uuid4()))
    _set(feat, layer, "photo_path", photo_path)
    _set(feat, layer, "field_survey_id", survey_id)
    _set(feat, layer, "source", "field_survey")
    ok, _added = layer.dataProvider().addFeatures([feat])
    return bool(ok)


def _write_habitat(layer, geom, props, photo_path, survey_id, module):
    sub = str(props.get("sub_habitat_type")
              or props.get("feature_type") or "").strip()
    hab_type = str(props.get("habitat_type") or "").strip()
    if module == "trees" and not hab_type:
        hab_type = "Individual Trees"

    notes = str(props.get("notes") or props.get("label") or "")
    report = str(props.get("condition_report") or "")
    if report:
        notes = (notes + "\n\n" + report) if notes.strip() else report

    condition = str(props.get("condition") or "")
    retention = "Retained" if module in ("trees", "hedgerow", "watercourse") else ""

    feat = QgsFeature(layer.fields())
    feat.setGeometry(geom)

    _set(feat, layer, "habitat_type", hab_type)
    _set(feat, layer, "sub_habitat_type", sub)
    _set(feat, layer, "habitat_key", module)
    _set(feat, layer, "condition", condition)
    _set(feat, layer, "condition_detail", props.get("condition_detail") or "")
    _set(feat, layer, "retention", retention)
    _set(feat, layer, "phase", "baseline")
    _set(feat, layer, "source", "field_survey")
    _set(feat, layer, "user_comments", notes)
    _set(feat, layer, "habitat_ref", props.get("habitat_ref") or "")
    _set(feat, layer, "photo_path", photo_path)
    _set(feat, layer, "field_survey_id", survey_id)

    # A blank strategic significance zeroes the parcel in the metric, and a
    # blank irreplaceable flag raises "Confirm irreplaceable habitat status" —
    # so fall back to the same defaults the desktop form uses.
    _set(feat, layer, "strategic_significance",
         props.get("strategic_significance") or DEFAULT_STRATEGIC_SIGNIFICANCE)
    _set(feat, layer, "irreplaceable_habitat",
         props.get("irreplaceable_habitat") or "No")

    if module == "watercourse":
        _set(feat, layer, "watercourse_encroachment",
             props.get("watercourse_encroachment") or "")
        _set(feat, layer, "riparian_encroachment_l",
             props.get("riparian_encroachment_l") or "")
        _set(feat, layer, "riparian_encroachment_r",
             props.get("riparian_encroachment_r") or "")

    # QML symbology fields — these drive the renderers, so they must be set
    # alongside the lower-case values they mirror.
    _set(feat, layer, "Baseline Condition", condition)
    _set(feat, layer, "Retention Category", retention)
    if module == "area":
        _set(feat, layer, "Baseline Habitat Type", sub)
        _set(feat, layer, "Proposed Habitat Type", sub)
    elif module == "hedgerow":
        _set(feat, layer, "Baseline Hedge Type", sub)
        _set(feat, layer, "Proposed Hedge Type", sub)
    elif module == "watercourse":
        _set(feat, layer, "Baseline River Type", sub)
        _set(feat, layer, "Proposed River Type", sub)
    elif module == "trees":
        size = str(props.get("tree_size") or "") or tree_size_from_sub_type(sub)
        loc = str(props.get("tree_location") or "") or tree_location_from_sub_type(sub)
        _set(feat, layer, "Baseline Habitat Type", sub)
        _set(feat, layer, "Proposed Habitat Type", sub)
        _set(feat, layer, "Category", "Existing")
        _set(feat, layer, "Baseline Tree Size", size)
        _set(feat, layer, "Baseline Rural or Urban Tree", loc)
        _set(feat, layer, "Proposed Tree Size", size)
        _set(feat, layer, "Proposed Rural or Urban Tree", loc)

    ok, _added = layer.dataProvider().addFeatures([feat])
    return bool(ok)


def _try_link_condition(props, wgs_geom):
    """Apply a standalone assessment to the baseline parcel it sits inside.

    Deliberately conservative: it must be the ONLY baseline feature of that
    exact sub-habitat type containing the pin, and it must not already carry
    an assessment. Anything else is left for the user to link by hand, because
    silently overwriting a desk-assessed condition would be worse than not
    linking at all. Returns a description of the link, or None.
    """
    sub = str(props.get("sub_habitat_type") or "").strip()
    if not sub or wgs_geom is None:
        return None

    from .layer_manager import LAYER_NAMES
    proj = QgsProject.instance()
    src = _wgs84()
    matches = []

    for key in ("baseline_area", "baseline_hedgerow", "baseline_water", "baseline_trees"):
        name = LAYER_NAMES.get(key)
        for lyr in proj.mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            if lyr.name() != name:
                continue
            if lyr.fields().indexFromName("sub_habitat_type") < 0:
                continue
            probe = QgsGeometry(wgs_geom)
            try:
                probe.transform(QgsCoordinateTransform(src, lyr.crs(), proj))
            except Exception:
                continue
            # Only an area parcel can contain a point; for linear and point
            # layers fall back to a tight proximity test.
            is_area = QgsWkbTypes.geometryType(lyr.wkbType()) == \
                QgsWkbTypes.GeometryType.PolygonGeometry
            for feat in lyr.getFeatures():
                if str(feat["sub_habitat_type"] or "").strip() != sub:
                    continue
                g = feat.geometry()
                if g is None or g.isEmpty():
                    continue
                hit = g.contains(probe) if is_area else (g.distance(probe) <= 5.0)
                if hit:
                    matches.append((lyr, feat))

    if len(matches) != 1:
        return None
    lyr, feat = matches[0]

    ci = lyr.fields().indexFromName("condition_detail")
    if ci >= 0 and str(feat["condition_detail"] or "").strip():
        return None                       # already assessed — do not overwrite

    grade = str(props.get("condition") or "")
    detail = str(props.get("condition_detail") or "")
    report = str(props.get("condition_report") or "")

    lyr.startEditing()
    gi = lyr.fields().indexFromName("condition")
    if grade and gi >= 0:
        lyr.changeAttributeValue(feat.id(), gi, grade)
    qi = lyr.fields().indexFromName("Baseline Condition")
    if grade and qi >= 0:
        lyr.changeAttributeValue(feat.id(), qi, grade)
    if ci >= 0:
        lyr.changeAttributeValue(feat.id(), ci, detail)
    ui = lyr.fields().indexFromName("user_comments")
    if report and ui >= 0:
        marker = "Condition Assessment:"
        cur = str(feat["user_comments"] or "")
        if marker in cur:
            cur = cur[:cur.rfind(marker)].rstrip()
        merged = (cur + "\n\n" + report) if cur.strip() else report
        lyr.changeAttributeValue(feat.id(), ui, merged)
    if not lyr.commitChanges():
        lyr.rollBack()
        return None
    return "%s (%s)" % (sub, lyr.name())


# ── entry point ──────────────────────────────────────────────────────────────

def import_survey(panel, data, survey_dir=None, photo_map=None):
    """Import a decoded survey FeatureCollection.

    ``panel`` is the BNGMainPanel (for layer_manager and iface).
    ``survey_dir`` is where a .zip's photo files were extracted.
    ``photo_map`` is {filename: data-URL} for a direct phone push.

    Returns a summary dict; the caller presents it.
    """
    features = data.get("features") or []
    meta = data.get("biome") or {}
    survey_id = str(meta.get("exported") or "")

    inline_paths = _write_inline_photos(photo_map or data.get("photos"))

    proj = QgsProject.instance()
    src_crs = _wgs84()
    xform_cache = {}

    def xform_to(crs):
        key = crs.authid() or crs.toWkt()
        if key not in xform_cache:
            xform_cache[key] = QgsCoordinateTransform(src_crs, crs, proj)
        return xform_cache[key]

    summary = {
        "eco": 0, "habitat": 0, "condition": 0, "condition_linked": [],
        "hsi": 0, "photos": 0, "skipped": 0, "errors": [],
    }
    touched = []

    for feat_json in features:
        props = feat_json.get("properties") or {}
        wgs_geom, geom_kind = _geometry_from_geojson(feat_json.get("geometry"))
        kind = _kind_of(props, geom_kind)
        photo_path = _resolve_photos(props, survey_dir, inline_paths)
        if photo_path:
            summary["photos"] += len(photo_path.split(";"))
        sid = str(props.get("timestamp") or survey_id)

        try:
            if kind == "condition":
                linked = _try_link_condition(props, wgs_geom)
                if linked:
                    summary["condition_linked"].append(linked)
                    summary["condition"] += 1
                    continue
                lyr = _open_or_create_gpkg_layer(
                    CONDITION_LAYER_NAME, "field_condition", "Point",
                    _CONDITION_FIELDS, FIELD_GROUP)
                f = QgsFeature(lyr.fields())
                if wgs_geom is not None and geom_kind == "point":
                    g = QgsGeometry(wgs_geom)
                    g.transform(xform_to(lyr.crs()))
                    f.setGeometry(g)
                _set(f, lyr, "uuid", props.get("uuid") or str(_uuid.uuid4()))
                _set(f, lyr, "sub_habitat_type", props.get("sub_habitat_type") or "")
                _set(f, lyr, "habitat_type", props.get("habitat_type") or "")
                _set(f, lyr, "condition", props.get("condition") or "")
                _set(f, lyr, "condition_detail", props.get("condition_detail") or "")
                _set(f, lyr, "report", props.get("condition_report") or "")
                _set(f, lyr, "surveyor", props.get("surveyor") or "")
                _set(f, lyr, "recorded", props.get("timestamp") or "")
                _set(f, lyr, "gridref", props.get("gridref") or "")
                _set(f, lyr, "photo_path", photo_path)
                _set(f, lyr, "linked", "no")
                ok, _a = lyr.dataProvider().addFeatures([f])
                if ok:
                    summary["condition"] += 1
                    touched.append(lyr)
                else:
                    summary["skipped"] += 1
                continue

            if kind == "hsi":
                lyr = _open_or_create_gpkg_layer(
                    HSI_LAYER_NAME, "field_pond_hsi", "Point",
                    _HSI_FIELDS, FIELD_GROUP)
                f = QgsFeature(lyr.fields())
                if wgs_geom is not None and geom_kind == "point":
                    g = QgsGeometry(wgs_geom)
                    g.transform(xform_to(lyr.crs()))
                    f.setGeometry(g)
                _set(f, lyr, "uuid", props.get("uuid") or str(_uuid.uuid4()))
                _set(f, lyr, "pond_name", props.get("pond_name") or "")
                score = props.get("hsi_score")
                _set(f, lyr, "hsi_score", float(score) if score not in ("", None) else None)
                _set(f, lyr, "hsi_band", props.get("hsi_band") or "")
                _set(f, lyr, "hsi_detail", props.get("hsi_detail") or "")
                _set(f, lyr, "surveyor", props.get("surveyor") or "")
                _set(f, lyr, "recorded", props.get("timestamp") or "")
                _set(f, lyr, "gridref", props.get("gridref") or "")
                _set(f, lyr, "photo_path", photo_path)
                ok, _a = lyr.dataProvider().addFeatures([f])
                if ok:
                    summary["hsi"] += 1
                    touched.append(lyr)
                else:
                    summary["skipped"] += 1
                continue

            # Everything below needs geometry.
            if wgs_geom is None:
                summary["skipped"] += 1
                continue

            if kind == "habitat":
                module = _module_of(props, geom_kind)
                layer_key = _HABITAT_LAYER_KEY.get(module)
                lyr = panel.layer_manager.get_or_create_layer(layer_key)
                if lyr is None or not lyr.isValid():
                    summary["skipped"] += 1
                    summary["errors"].append("no layer for %s" % module)
                    continue
                g = QgsGeometry(wgs_geom)
                g.transform(xform_to(lyr.crs()))
                if _write_habitat(lyr, g, props, photo_path, sid, module):
                    summary["habitat"] += 1
                    touched.append(lyr)
                else:
                    summary["skipped"] += 1
                continue

            # Ecological feature.
            spec = _ECO_LAYER_SPEC.get(geom_kind)
            if spec is None:
                summary["skipped"] += 1
                continue
            display, table, wkb = spec
            lyr = _open_or_create_gpkg_layer(display, table, wkb,
                                             _ECO_FIELDS, "Ecological Features")
            if lyr is None or not lyr.isValid():
                summary["skipped"] += 1
                continue
            g = QgsGeometry(wgs_geom)
            g.transform(xform_to(lyr.crs()))
            if _write_eco(lyr, g, props, photo_path, sid):
                summary["eco"] += 1
                touched.append(lyr)
            else:
                summary["skipped"] += 1

        except Exception as exc:                       # keep going on one bad row
            summary["skipped"] += 1
            summary["errors"].append("%s: %s" % (props.get("feature_type") or kind, exc))

    # ── refresh ──────────────────────────────────────────────────────────
    seen = set()
    for lyr in touched:
        if lyr.id() in seen:
            continue
        seen.add(lyr.id())
        try:
            lyr.dataProvider().reloadData()
            lyr.updateExtents()
            lyr.triggerRepaint()
        except Exception:
            pass
        node = proj.layerTreeRoot().findLayer(lyr.id())
        if node is not None:
            node.setItemVisibilityChecked(True)

    _style_eco_layers(panel, seen)
    return summary


def _style_eco_layers(panel, layer_ids):
    """Apply the placer's categorized renderer to any eco layer we wrote to."""
    proj = QgsProject.instance()
    eco_names = {LAYER_NAME, LINE_LAYER_NAME, POLY_LAYER_NAME}
    targets = [l for l in proj.mapLayers().values()
               if l.id() in layer_ids and l.name() in eco_names]
    if not targets:
        return
    try:
        dlg = getattr(panel, "_eco_dlg", None)
        if dlg is None:
            from .gridref_dialog import GridRefDialog
            dlg = GridRefDialog(panel.iface, panel.iface.mainWindow())
            panel._eco_dlg = dlg
        for lyr in targets:
            dlg._apply_style(lyr)
    except Exception:
        # Styling is cosmetic; imported data must never be lost to it.
        pass


def summary_text(summary):
    """Human-readable result for the message box."""
    lines = []
    if summary["habitat"]:
        lines.append("• %d habitat parcel(s) → Baseline Habitats" % summary["habitat"])
    if summary["eco"]:
        lines.append("• %d ecological feature(s)" % summary["eco"])
    if summary["condition"]:
        n_linked = len(summary["condition_linked"])
        lines.append("• %d condition assessment(s)%s" % (
            summary["condition"],
            (" — %d linked automatically" % n_linked) if n_linked else ""))
    if summary["hsi"]:
        lines.append("• %d pond HSI record(s)" % summary["hsi"])
    if summary["photos"]:
        lines.append("• %d photo(s) linked" % summary["photos"])
    if summary["skipped"]:
        lines.append("• %d skipped" % summary["skipped"])
    if not lines:
        lines.append("Nothing was imported.")
    if summary["condition_linked"]:
        lines.append("")
        lines.append("Linked to: " + ", ".join(summary["condition_linked"][:6]))
    if summary["errors"]:
        lines.append("")
        lines.append("Problems:")
        lines.extend("  " + e for e in summary["errors"][:6])
    return "\n".join(lines)


def message_level(summary):
    if summary["errors"] or summary["skipped"]:
        return Qgis.MessageLevel.Warning
    return Qgis.MessageLevel.Success
