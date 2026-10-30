# pond_mapper.py
"""
Pond Mapper support for Biome.

Provides:
  - A memory layer for placed ponds (Point, EPSG:27700)
  - Distance-ordered water-body IDs (SWB-01 is always the nearest)
  - Distance lines from the redline to each pond
  - Layer helpers used by the map exporter
"""

import os

# ── Layer names ────────────────────────────────────────────────────────────────
POND_LAYER_NAME       = "Water Body"
POND_LINES_LAYER_NAME = "Pond Distance Lines"
_POND_GROUP_NAME      = "Standing Water Bodies"
_LEGACY_POND_GROUP_NAMES = ("Pond Mapper",)
_POND_LABEL_SCOPE = "Biome"
_POND_LABEL_KEY = "pond_label_mode"
_POND_LABEL_MODES = {"both", "name", "distance", "off"}


def ensure_pond_fields(lyr):
    """Add fields introduced after older saved pond layers were created."""
    if lyr is None or lyr.fields().indexOf("gcn_status") >= 0:
        return True
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import QgsField
    was_editing = lyr.isEditable()
    if not was_editing and not lyr.startEditing():
        return False
    ok = bool(lyr.addAttribute(QgsField(
        "gcn_status", QVariant.String, len=4)))
    lyr.updateFields()
    if not was_editing:
        if ok:
            ok = bool(lyr.commitChanges())
        if not ok:
            lyr.rollBack()
    return ok


def pond_label_mode():
    """Return this project's saved marker-label mode."""
    from qgis.core import QgsProject
    mode, _ok = QgsProject.instance().readEntry(
        _POND_LABEL_SCOPE, _POND_LABEL_KEY, "both")
    mode = str(mode or "both").lower()
    return mode if mode in _POND_LABEL_MODES else "both"


def apply_pond_label_mode(lyr=None, mode=None, save=False):
    """Apply name/distance/both/off labels and optionally persist the choice."""
    from qgis.core import (QgsPalLayerSettings, QgsProject,
                           QgsVectorLayerSimpleLabeling)
    project = QgsProject.instance()
    mode = str(mode or pond_label_mode()).lower()
    if mode not in _POND_LABEL_MODES:
        mode = "both"
    if save:
        project.writeEntry(_POND_LABEL_SCOPE, _POND_LABEL_KEY, mode)
    if lyr is None:
        layers = project.mapLayersByName(POND_LAYER_NAME)
        lyr = layers[0] if layers else None
    if lyr is None:
        return mode
    ensure_pond_fields(lyr)
    if mode == "off":
        lyr.setLabelsEnabled(False)
        lyr.triggerRepaint()
        return mode
    labeling = lyr.labeling()
    pal = labeling.settings() if labeling is not None else QgsPalLayerSettings()
    pal.enabled = True
    if mode == "name":
        pal.fieldName = (
            "concat(\"pond_id\", CASE WHEN \"gcn_status\" IN "
            "('P','A','NS') THEN concat(' [', \"gcn_status\", ']') ELSE '' END)")
        pal.isExpression = True
    elif mode == "distance":
        pal.fieldName = (
            "concat(\"distance_label\", CASE WHEN \"gcn_status\" IN "
            "('P','A','NS') THEN concat(' [', \"gcn_status\", ']') ELSE '' END)")
        pal.isExpression = True
    else:
        pal.fieldName = (
            "concat(\"pond_id\", ' (', \"distance_label\", ')', "
            "CASE WHEN \"gcn_status\" IN ('P','A','NS') THEN "
            "concat(' [', \"gcn_status\", ']') ELSE '' END)")
        pal.isExpression = True
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)
    lyr.triggerRepaint()
    return mode


def pond_group(root, create=False):
    """Return the standing-water group, renaming the legacy group in place."""
    grp = root.findGroup(_POND_GROUP_NAME)
    if grp is None:
        for legacy_name in _LEGACY_POND_GROUP_NAMES:
            grp = root.findGroup(legacy_name)
            if grp is not None:
                grp.setName(_POND_GROUP_NAME)
                break
    if grp is None and create:
        bm = root.findGroup("Base Maps")
        idx = root.children().index(bm) if bm is not None else 0
        grp = root.insertGroup(idx, _POND_GROUP_NAME)
    return grp


def migrate_pond_group_name():
    """Upgrade the legacy group name in the current project, if present."""
    from qgis.core import QgsProject
    return pond_group(QgsProject.instance().layerTreeRoot(), create=False)


def _project_redline_bng():
    """Return the current project's red line geometry in EPSG:27700.

    Pond IDs are ordered by distance from this geometry.  Keeping the lookup
    here lets persisted pond data repair itself during project recovery,
    before the Desk Study panel has necessarily been built.
    """
    from qgis.core import (
        QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
        QgsProject,
    )

    project = QgsProject.instance()
    for lyr in project.mapLayers().values():
        if lyr.name() not in ("Redline Boundary", "Red Line Boundary"):
            continue
        feats = [f for f in lyr.getFeatures()
                 if f.geometry() is not None and not f.geometry().isEmpty()]
        if not feats:
            continue
        geom = QgsGeometry.unaryUnion([QgsGeometry(f.geometry()) for f in feats])
        if geom is None or geom.isEmpty():
            continue
        bng = QgsCoordinateReferenceSystem("EPSG:27700")
        if lyr.crs() != bng:
            geom.transform(QgsCoordinateTransform(
                lyr.crs(), bng, project.transformContext()))
        return geom
    return None


def _distance_label(distance_m):
    """Return the standard user-facing distance text for a pond marker."""
    if distance_m < 1:
        return "Within redline"
    if distance_m < 1000:
        return f"{distance_m} m"
    return f"{distance_m / 1000:.2f} km"


def renumber_ponds_by_distance(lyr=None, redline_geom=None):
    """Renumber water bodies nearest-first and refresh their stored distances.

    Returns a mapping from each previous ID to its new ID.  Tied distances keep
    their previous numeric order, which avoids labels swapping needlessly
    between sessions.  When a red line is unavailable, existing stored
    distances are used so legacy saved projects can still be normalised.
    """
    from qgis.core import QgsProject

    if lyr is None:
        candidates = [x for x in QgsProject.instance().mapLayersByName(
            POND_LAYER_NAME) if x.geometryType() == 0]
        if not candidates:
            return {}
        lyr = candidates[0]
    if not lyr.isValid() or lyr.featureCount() == 0:
        rebuild_pond_renderer(lyr)
        return {}
    ensure_pond_fields(lyr)

    if redline_geom is None:
        redline_geom = _project_redline_bng()

    def _old_number(value):
        try:
            return int(str(value or "").rsplit("-", 1)[-1])
        except (TypeError, ValueError):
            return 10 ** 9

    rows = []
    for feat in lyr.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if redline_geom is not None and not redline_geom.isEmpty():
            distance_m = int(round(redline_geom.distance(geom)))
        else:
            try:
                distance_m = int(feat["distance_m"] or 0)
            except (KeyError, TypeError, ValueError):
                distance_m = 0
        old_id = str(feat["pond_id"] or "")
        pt = geom.asPoint()
        rows.append((distance_m, _old_number(old_id), pt.x(), pt.y(),
                     feat.id(), old_id))

    # Once the user has explicitly reordered the manager list, keep that order
    # authoritative while continuing to refresh the measured distances.  New
    # ponds receive the next ID and therefore append naturally.
    manual_order = str(lyr.customProperty(
        "biome_manual_pond_order", "0")).lower() in ("1", "true")
    if manual_order:
        rows.sort(key=lambda row: (row[1], row[0], row[2], row[3], row[4]))
    else:
        rows.sort(key=lambda row: row[:5])
    if not rows:
        rebuild_pond_renderer(lyr)
        return {}

    pond_idx = lyr.fields().indexOf("pond_id")
    dist_idx = lyr.fields().indexOf("distance_m")
    label_idx = lyr.fields().indexOf("distance_label")
    grid_idx = lyr.fields().indexOf("grid_ref")
    if min(pond_idx, dist_idx, label_idx, grid_idx) < 0:
        rebuild_pond_renderer(lyr)
        return {}

    was_editing = lyr.isEditable()
    if not was_editing and not lyr.startEditing():
        return {}

    changed = False
    id_changes = {}
    for number, row in enumerate(rows, 1):
        distance_m, _old_num, _x, _y, fid, old_id = row
        new_id = f"SWB-{number:02d}"
        id_changes[old_id] = new_id
        label = _distance_label(distance_m)
        feat = lyr.getFeature(fid)
        if str(feat["pond_id"] or "") != new_id:
            lyr.changeAttributeValue(fid, pond_idx, new_id)
            changed = True
        try:
            old_distance = int(feat["distance_m"] or 0)
        except (TypeError, ValueError):
            old_distance = 0
        if old_distance != distance_m:
            lyr.changeAttributeValue(fid, dist_idx, distance_m)
            changed = True
        if str(feat["distance_label"] or "") != label:
            lyr.changeAttributeValue(fid, label_idx, label)
            changed = True
        try:
            from .gridref_utils import osgb_en_to_gridref
            grid_ref = osgb_en_to_gridref(int(_x), int(_y), 8)
        except Exception:
            grid_ref = str(feat["grid_ref"] or "")
        if str(feat["grid_ref"] or "") != grid_ref:
            lyr.changeAttributeValue(fid, grid_idx, grid_ref)
            changed = True

    if not was_editing:
        if not lyr.commitChanges():
            lyr.rollBack()
            return {}
        try:
            lyr.dataProvider().reloadData()
            lyr.reload()
        except Exception:
            pass
    if changed:
        lyr.triggerRepaint()
    rebuild_pond_renderer(lyr)
    return id_changes


def renumber_ponds_in_order(lyr, feature_ids):
    """Assign SWB references in an explicit user-defined feature order.

    Distances, grid references and geometries are deliberately untouched.  A
    temporary ID pass avoids transient duplicate values when two existing
    references exchange positions.
    """
    if lyr is None or not lyr.isValid():
        return False
    ensure_pond_fields(lyr)
    pond_idx = lyr.fields().indexOf("pond_id")
    if pond_idx < 0:
        return False

    existing = {int(f.id()) for f in lyr.getFeatures()}
    ordered = [int(fid) for fid in feature_ids if int(fid) in existing]
    if len(ordered) != len(existing) or len(set(ordered)) != len(existing):
        return False

    was_editing = lyr.isEditable()
    if not was_editing and not lyr.startEditing():
        return False

    ok = True
    for number, fid in enumerate(ordered, 1):
        ok = bool(lyr.changeAttributeValue(
            fid, pond_idx, f"__biome_swb_{number:04d}__")) and ok
    for number, fid in enumerate(ordered, 1):
        ok = bool(lyr.changeAttributeValue(
            fid, pond_idx, f"SWB-{number:02d}")) and ok

    if not was_editing:
        if ok:
            ok = bool(lyr.commitChanges())
        if not ok:
            lyr.rollBack()
    if not ok:
        return False

    lyr.setCustomProperty("biome_manual_pond_order", "1")
    try:
        lyr.dataProvider().reloadData()
        lyr.reload()
    except Exception:
        pass
    rebuild_pond_renderer(lyr)
    apply_pond_label_mode(lyr)
    lyr.triggerRepaint()
    return True


# ── Pond layer ─────────────────────────────────────────────────────────────────

def get_or_create_pond_layer():
    """Return the Ponds memory layer, creating it (and its group) if absent."""
    from qgis.core import (
        QgsProject, QgsVectorLayer,
        QgsMarkerSymbol, QgsSvgMarkerSymbolLayer,
        QgsCategorizedSymbolRenderer, QgsRendererCategory,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    project = QgsProject.instance()

    # Return existing layer if already in project
    existing = project.mapLayersByName(POND_LAYER_NAME)
    for lyr in existing:
        if lyr.geometryType() == 0:      # Point
            # Older projects may carry the pre-empty-state renderer (zero
            # categories), which makes QGIS print only the generic layer name.
            # Rebuild on access so the emblem/status key is immediately
            # available without requiring the user to place a pond first.
            renumber_ponds_by_distance(lyr, _project_redline_bng())
            apply_pond_label_mode(lyr)
            return lyr

    # Create fresh layer
    lyr = QgsVectorLayer(
        "Point?crs=EPSG:27700"
        "&field=pond_id:string(20)"
        "&field=distance_m:integer"
        "&field=distance_label:string(20)"
        "&field=grid_ref:string(12)"
        "&field=gcn_status:string(4)",
        POND_LAYER_NAME, "memory"
    )

    # A fully styled empty renderer is installed after the layer is registered
    # below.  Using the shared builder keeps fresh and reopened projects equal.
    renderer = QgsCategorizedSymbolRenderer("pond_id", [])
    lyr.setRenderer(renderer)

    # Label: pond_id + distance
    pal = QgsPalLayerSettings()
    pal.enabled      = True
    pal.fieldName    = "concat(\"pond_id\", ' (', \"distance_label\", ')')"
    pal.isExpression = True
    fmt = QgsTextFormat()
    fmt.setSize(7.5)
    fmt.setColor(QColor("#1F4A37"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)
    # Persist tier: the next project save migrates this layer into
    # <project>_bng_data.gpkg and repoints it there
    # (bng_plugin._persist_scratch_layers), so placed water bodies reopen
    # with the project. skipMemoryLayersCheck silences the scratch-layer
    # warning for the window before that first save.
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    lyr.setCustomProperty("biome_scratch", "1")
    lyr.setCustomProperty("biome_gpkg_table", "water_bodies")

    # Add to a dedicated group
    root = project.layerTreeRoot()
    grp = pond_group(root, create=True)

    project.addMapLayer(lyr, False)
    grp.insertLayer(0, lyr)
    rebuild_pond_renderer(lyr)
    apply_pond_label_mode(lyr)
    return lyr


def recover_persisted_pond_layer(gpkg_path):
    """Reconnect a saved ``water_bodies`` table after a project reload.

    Older or interrupted save cycles can leave an empty memory-layer entry in
    the project, or omit the layer entry altogether, even though the pond
    features were successfully written to the project GeoPackage.  Habitat
    recovery does not know about this Pond Mapper table, so heal it here.

    Returns the recovered/existing layer, or ``None`` when no saved pond table
    is available.
    """
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsDataProvider,
    )

    if not gpkg_path or not os.path.isfile(gpkg_path):
        return None

    uri = f"{gpkg_path}|layername=water_bodies"
    probe = QgsVectorLayer(uri, POND_LAYER_NAME, "ogr")
    if not probe.isValid():
        return None

    project = QgsProject.instance()
    pond_layer = None
    for lyr in project.mapLayersByName(POND_LAYER_NAME):
        if lyr.geometryType() != 0:
            continue
        source = lyr.source() or ""
        if (lyr.providerType() == "ogr"
                and "|layername=water_bodies" in source):
            pond_layer = lyr
            break
        if lyr.providerType() == "memory":
            lyr.setDataSource(uri, POND_LAYER_NAME, "ogr",
                              QgsDataProvider.ProviderOptions())
            if lyr.isValid():
                pond_layer = lyr
                break

    if pond_layer is None:
        pond_layer = probe
        root = project.layerTreeRoot()
        grp = pond_group(root, create=True)
        project.addMapLayer(pond_layer, False)
        grp.insertLayer(0, pond_layer)

    pond_layer.removeCustomProperty("biome_scratch")
    pond_layer.removeCustomProperty("skipMemoryLayersCheck")
    pond_layer.setCustomProperty("biome_gpkg_table", "water_bodies")
    # Migration for every existing project: old builds numbered water bodies
    # in placement order.  Recalculate their distances and rewrite IDs nearest
    # first as soon as the persisted table is recovered.
    renumber_ponds_by_distance(pond_layer, _project_redline_bng())
    apply_pond_label_mode(pond_layer)
    return pond_layer


def _make_pond_symbol():
    """Build the SVG marker symbol for a single pond category."""
    from qgis.core import QgsMarkerSymbol, QgsSvgMarkerSymbolLayer
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    svg_path   = os.path.join(plugin_dir, "Symbols", "water_body.svg")
    symbol = QgsMarkerSymbol.createSimple({})
    symbol.deleteSymbolLayer(0)
    if os.path.isfile(svg_path):
        svg_lyr = QgsSvgMarkerSymbolLayer(svg_path)
        svg_lyr.setSize(8.0)
        symbol.appendSymbolLayer(svg_lyr)
    else:
        from qgis.core import QgsSimpleMarkerSymbolLayer
        from qgis.PyQt.QtGui import QColor
        ml = QgsSimpleMarkerSymbolLayer()
        ml.setColor(QColor("#2a7fc4"))
        ml.setSize(6.0)
        symbol.appendSymbolLayer(ml)
    return symbol


def rebuild_pond_renderer(lyr=None):
    """Rebuild the categorized renderer so each pond_id gets its own legend entry.

    Call this after adding a new pond so the legend stays in sync.
    """
    from qgis.core import (
        QgsProject, QgsCategorizedSymbolRenderer, QgsRendererCategory,
    )
    if lyr is None:
        lyrs = QgsProject.instance().mapLayersByName(POND_LAYER_NAME)
        if not lyrs:
            return
        lyr = lyrs[0]

    # Read current ponds
    pond_rows = sorted(
        [(f["pond_id"], f["distance_label"]) for f in lyr.getFeatures()
         if f["pond_id"]],
        key=lambda r: r[0]
    )

    categories = []
    for pond_id, dist_label in pond_rows:
        legend_label = f"{pond_id}  ({dist_label})" if dist_label else pond_id
        categories.append(QgsRendererCategory(pond_id, _make_pond_symbol(), legend_label))

    # Keep the water-body emblem available to print-layout legends even when
    # the mapper contains no placed ponds.  The sentinel category can never
    # match a real feature and therefore draws nothing on the canvas; it is
    # purely an honest empty-result key for the exported Pond Map.
    if not categories:
        categories.append(QgsRendererCategory(
            "__none__", _make_pond_symbol(),
            "Standing water body\nNone identified within the search area"))

    renderer = QgsCategorizedSymbolRenderer("pond_id", categories)
    lyr.setRenderer(renderer)
    lyr.triggerRepaint()


def next_pond_id():
    """Return the next auto-numbered pond ID (e.g. 'SWB-03')."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(POND_LAYER_NAME)
    if not lyrs:
        return "SWB-01"
    lyr = lyrs[0]
    existing = {f["pond_id"] for f in lyr.getFeatures() if f["pond_id"]}
    n = 1
    while True:
        candidate = f"SWB-{n:02d}"
        if candidate not in existing:
            return candidate
        n += 1


def add_pond(point_27700, redline_geom=None):
    """Add a pond point at point_27700 (QgsPointXY, BNG).

    Computes distance to the redline if redline_geom is supplied.
    Returns the pond_id string.
    """
    from qgis.core import (
        QgsFeature, QgsGeometry, QgsPointXY,
    )
    from .gridref_utils import osgb_en_to_gridref

    lyr     = get_or_create_pond_layer()
    pond_id = next_pond_id()

    # Distance to redline
    point_geom = QgsGeometry.fromPointXY(point_27700)
    if redline_geom and not redline_geom.isEmpty():
        dist_m = int(round(redline_geom.distance(point_geom)))
    else:
        dist_m = 0

    dist_label = _distance_label(dist_m)

    try:
        gr = osgb_en_to_gridref(int(point_27700.x()), int(point_27700.y()), 8)
    except Exception:
        gr = ""

    feat = QgsFeature(lyr.fields())
    feat.setGeometry(point_geom)
    # By NAME, not position: once the layer has been persisted into the
    # project GeoPackage it gains a leading 'fid' field, so a positional
    # setAttributes silently mis-aligns and the commit drops the feature.
    feat["pond_id"]        = pond_id
    feat["distance_m"]     = dist_m
    feat["distance_label"] = dist_label
    feat["grid_ref"]       = gr

    lyr.startEditing()
    lyr.addFeature(feat)
    lyr.commitChanges()

    # Every placement can alter all IDs: the nearest marker is always SWB-01,
    # regardless of the order in which the user clicked the map.
    id_changes = renumber_ponds_by_distance(lyr, redline_geom)
    lyr.triggerRepaint()

    return id_changes.get(pond_id, pond_id)


def remove_pond_layer():
    """Remove the Water Body layer and clean up the Pond Mapper group if empty."""
    from qgis.core import QgsProject
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    for lyr in project.mapLayersByName(POND_LAYER_NAME):
        # Remove via the layer tree node first so QGIS doesn't leave a ghost
        # entry in the group — plain removeMapLayer can silently fail when the
        # layer lives inside a group.
        node = root.findLayer(lyr.id())
        if node is not None:
            parent = node.parent()
            parent.removeChildNode(node)
        project.removeMapLayer(lyr.id())

    # Remove the group entirely if it is now empty
    grp = pond_group(root, create=False)
    if grp is not None and not grp.findLayers():
        root.removeChildNode(grp)


# ── Distance lines ─────────────────────────────────────────────────────────────

def load_pond_distance_lines(redline_geom):
    """(Re)build the Pond Distance Lines layer from current pond features.

    Called after each pond is placed so the line layer stays in sync.
    """
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
        QgsSingleSymbolRenderer, QgsLineSymbol,
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
        QgsTextFormat, QgsTextBufferSettings,
    )
    from qgis.PyQt.QtGui import QColor

    # Remove previous
    for old in QgsProject.instance().mapLayersByName(POND_LINES_LAYER_NAME):
        QgsProject.instance().removeMapLayer(old.id())

    pond_lyrs = QgsProject.instance().mapLayersByName(POND_LAYER_NAME)
    if not pond_lyrs:
        return None

    pond_lyr  = pond_lyrs[0]
    pond_feats = list(pond_lyr.getFeatures())
    if not pond_feats or redline_geom is None or redline_geom.isEmpty():
        return None

    line_lyr = QgsVectorLayer(
        "LineString?crs=EPSG:27700"
        "&field=pond_id:string(20)"
        "&field=distance_label:string(20)",
        POND_LINES_LAYER_NAME, "memory"
    )
    dp = line_lyr.dataProvider()
    line_feats = []

    for feat in pond_feats:
        pond_geom = feat.geometry()
        if pond_geom is None or pond_geom.isEmpty():
            continue

        pond_pt = pond_geom.asPoint()
        nearest = redline_geom.nearestPoint(pond_geom)
        if nearest is None or nearest.isEmpty():
            nearest = redline_geom.centroid()
        near_pt = nearest.asPoint()

        dist_m = int(round(redline_geom.distance(pond_geom)))
        dist_label = _distance_label(dist_m)

        line_geom = QgsGeometry.fromPolylineXY([
            QgsPointXY(near_pt.x(), near_pt.y()),
            QgsPointXY(pond_pt.x(),  pond_pt.y()),
        ])

        lf = QgsFeature(line_lyr.fields())
        lf.setGeometry(line_geom)
        lf.setAttributes([feat["pond_id"] or "", dist_label])
        line_feats.append(lf)

    if not line_feats:
        return None

    dp.addFeatures(line_feats)
    line_lyr.updateExtents()

    sym = QgsLineSymbol.createSimple({
        "line_color": "#1F4A37",
        "line_width": "0.4",
        "line_style":  "dash",
        "capstyle":   "round",
    })
    line_lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    pal = QgsPalLayerSettings()
    pal.enabled        = True
    pal.fieldName      = "distance_label"
    pal.placement      = QgsPalLayerSettings.Placement.Line
    pal.placementFlags = QgsPalLayerSettings.AboveLine
    fmt = QgsTextFormat()
    fmt.setSize(6.5)
    fmt.setColor(QColor("#1F4A37"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    line_lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    line_lyr.setLabelsEnabled(True)
    line_lyr.setCustomProperty("skipMemoryLayersCheck", 1)   # session-only scratch
    # Unlike the ponds themselves, the lines are derived data — strip them
    # from saves (biome_session_only) and rebuild from the persisted ponds
    # on the next lines toggle after a reopen.
    line_lyr.setCustomProperty("biome_session_only", 1)

    root  = QgsProject.instance().layerTreeRoot()
    grp = pond_group(root, create=True)

    # Insert below pond points
    pond_node = root.findLayer(pond_lyrs[0].id())
    QgsProject.instance().addMapLayer(line_lyr, False)
    if pond_node is not None:
        parent = pond_node.parent()
        idx    = parent.children().index(pond_node) + 1
        parent.insertLayer(idx, line_lyr)
    else:
        grp.addLayer(line_lyr)
    grp.setExpanded(False)

    return line_lyr


def remove_pond_distance_lines():
    """Remove the Pond Distance Lines layer."""
    from qgis.core import QgsProject
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    for lyr in project.mapLayersByName(POND_LINES_LAYER_NAME):
        node = root.findLayer(lyr.id())
        if node is not None:
            node.parent().removeChildNode(node)
        project.removeMapLayer(lyr.id())


def set_pond_lines_visible(visible):
    """Show or hide the Pond Distance Lines layer."""
    from qgis.core import QgsProject
    for lyr in QgsProject.instance().mapLayersByName(POND_LINES_LAYER_NAME):
        node = QgsProject.instance().layerTreeRoot().findLayer(lyr.id())
        if node is not None:
            node.setItemVisibilityChecked(visible)
