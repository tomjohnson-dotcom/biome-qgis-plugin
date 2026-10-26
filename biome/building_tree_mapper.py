# building_tree_mapper.py
"""
Buildings & Trees Visualiser for Biome.

Self-contained illustration layers that are deliberately kept OUTSIDE the BNG
habitat system (LayerManager / LAYER_NAMES / HABITAT_FIELDS).  Nothing here
feeds any calculation, summary, balance check or metric export — these layers
exist purely to make maps showing where buildings and trees sit on a site.

Provides:
  - A polygon layer for buildings, auto-numbered B1, B2, B3 …, each building a
    different colour (categorised renderer → each colour shows in the legend).
  - A point layer for trees, auto-numbered T1, T2, T3 …, all one green colour,
    differentiated on the map by their labels.
  - Helpers used by the map exporter for the Building Map and Tree Map.

All geometry is stored in EPSG:27700 (British National Grid).  Layers are
memory-backed, mirroring the Pond Mapper.
"""

BUILDING_LAYER_NAME = "Buildings (Map Only)"
TREE_LAYER_NAME     = "Trees (Map Only)"
TREE_BUFFER_LAYER_NAME = "Tree Buffers (Map Only)"
TREE_RPA_LAYER_NAME = "Tree RPAs (Map Only)"
TREE_CEZ_LAYER_NAME = "Construction Exclusion Zones (Map Only)"
TREE_RAZ_LAYER_NAME = "Restricted Activity Zones (Map Only)"
_BT_GROUP_NAME      = "Buildings and Trees"
_OLD_BT_GROUP_NAMES = ("Site Plan Visualiser", "Building & Tree Mapper")
_BUILDINGS_GROUP_NAME = "Buildings"
_TREES_GROUP_NAME = "Trees"

TREE_COLOUR = "#1F7A4D"   # forest green — clear and map-friendly
TREE_REMOVED_COLOUR = "#C62828"

# Qualitative palette cycled through as buildings are placed; chosen to be
# easy to tell apart against aerial/OS basemaps.
BUILDING_PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080",
    "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000",
    "#ffd8b1", "#000075", "#a9a9a9", "#e6beff", "#ffe119",
]


def building_colour_for(index):
    """Return the palette colour (hex) for the index-th building (0-based)."""
    return BUILDING_PALETTE[index % len(BUILDING_PALETTE)]


def _next_building_number(lyr):
    """Return the next unused automatic B-number for *lyr*.

    Using the feature count caused duplicate references after an individual
    building was removed.  Continue from the highest automatic B-reference
    instead, while leaving user-supplied names alone.
    """
    import re
    highest = 0
    for feat in lyr.getFeatures():
        match = re.fullmatch(r"B(\d+)", str(feat["building_id"] or "").strip(),
                             re.IGNORECASE)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _tag_for_persistence(lyr, table):
    """Tag a memory-backed map-only layer so the generic migrator in
    bng_plugin._persist_scratch_layers writes it into the project GeoPackage on
    save (and so QGIS stops warning that it is a throwaway scratch layer). The
    layer is repointed to OGR there, preserving its id, style and tree position,
    so placed buildings/trees survive closing and reopening the project.

    No-op once the layer is already disk-backed (provider != memory) — e.g. a
    project reopened after a previous save, where re-tagging would wrongly mark
    it for migration again.
    """
    if lyr.providerType() != "memory":
        return
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)
    lyr.setCustomProperty("biome_scratch", 1)
    lyr.setCustomProperty("biome_gpkg_table", table)


# ── Group / tree placement ──────────────────────────────────────────────────

def _ensure_group():
    from qgis.core import QgsLayerTreeGroup, QgsProject
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    grp = root.findGroup(_BT_GROUP_NAME)
    if grp is None:
        # Preserve existing projects and their layer ordering when the tool's
        # former group names are encountered.
        for old_name in _OLD_BT_GROUP_NAMES:
            grp = root.findGroup(old_name)
            if grp is not None:
                grp.setName(_BT_GROUP_NAME)
                break
    if grp is None:
        bm  = root.findGroup("Base Maps")
        idx = root.children().index(bm) if bm is not None else 0
        grp = root.insertGroup(idx, _BT_GROUP_NAME)

    def _direct_group(name):
        for child in grp.children():
            if isinstance(child, QgsLayerTreeGroup) and child.name() == name:
                return child
        return grp.addGroup(name)

    buildings_group = _direct_group(_BUILDINGS_GROUP_NAME)
    trees_group = _direct_group(_TREES_GROUP_NAME)

    def _move_named_layers(names, target):
        for name in names:
            for layer in project.mapLayersByName(name):
                node = root.findLayer(layer.id())
                if node is None or node.parent() is target:
                    continue
                visible = node.isVisible()
                parent = node.parent()
                # Add the replacement node first. QGIS 4.2 can invalidate the
                # Python QgsVectorLayer wrapper when its final tree node is
                # removed, even though the project still owns the layer.
                new_node = target.addLayer(layer)
                new_node.setItemVisibilityChecked(visible)
                if parent is not None:
                    parent.removeChildNode(node)

    _move_named_layers((BUILDING_LAYER_NAME,), buildings_group)
    _move_named_layers((
        TREE_LAYER_NAME, TREE_BUFFER_LAYER_NAME, TREE_RPA_LAYER_NAME,
        TREE_CEZ_LAYER_NAME, TREE_RAZ_LAYER_NAME,
    ), trees_group)
    return grp


def _buildings_group():
    from qgis.core import QgsLayerTreeGroup
    grp = _ensure_group()
    for child in grp.children():
        if isinstance(child, QgsLayerTreeGroup) and child.name() == _BUILDINGS_GROUP_NAME:
            return child
    return grp.addGroup(_BUILDINGS_GROUP_NAME)


def _trees_group():
    from qgis.core import QgsLayerTreeGroup
    grp = _ensure_group()
    for child in grp.children():
        if isinstance(child, QgsLayerTreeGroup) and child.name() == _TREES_GROUP_NAME:
            return child
    return grp.addGroup(_TREES_GROUP_NAME)


def _label_format(colour_hex, size=8.0):
    from qgis.core import QgsTextFormat, QgsTextBufferSettings
    from qgis.PyQt.QtGui import QColor
    fmt = QgsTextFormat()
    fmt.setSize(size)
    fmt.setColor(QColor(colour_hex))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.9)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    return fmt


# ── Building layer ──────────────────────────────────────────────────────────

def get_or_create_building_layer():
    """Return the buildings polygon layer, creating it (and the group) if absent."""
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsCategorizedSymbolRenderer,
    )

    project = QgsProject.instance()
    for lyr in project.mapLayersByName(BUILDING_LAYER_NAME):
        if lyr.geometryType() == 2:      # Polygon
            _ensure_group()
            _tag_for_persistence(lyr, "buildings_map_only")
            _ensure_building_fields(lyr)
            _configure_building_labels(lyr)
            rebuild_building_renderer(lyr)
            return lyr

    lyr = QgsVectorLayer(
        "Polygon?crs=EPSG:27700"
        "&field=building_id:string(10)"
        "&field=building_label:string(120)"
        "&field=colour:string(9)",
        BUILDING_LAYER_NAME, "memory",
    )
    lyr.setRenderer(QgsCategorizedSymbolRenderer("building_id", []))

    _configure_building_labels(lyr)
    lyr.setCustomProperty("biome_map_only", 1)   # tag: never counts in BNG
    _tag_for_persistence(lyr, "buildings_map_only")

    project.addMapLayer(lyr, False)
    _buildings_group().insertLayer(0, lyr)
    return lyr


def _ensure_building_fields(lyr):
    """Upgrade older building layers with a long, user-editable label field."""
    from qgis.core import QgsField
    from qgis.PyQt.QtCore import QVariant
    if lyr.fields().indexFromName("building_label") < 0:
        lyr.dataProvider().addAttributes([
            QgsField("building_label", QVariant.String, len=120)])
        lyr.updateFields()
    label_idx = lyr.fields().indexFromName("building_label")
    id_idx = lyr.fields().indexFromName("building_id")
    updates = {}
    for feat in lyr.getFeatures():
        if not str(feat[label_idx] or "").strip():
            updates[feat.id()] = {label_idx: str(feat[id_idx] or "")}
    if updates:
        lyr.dataProvider().changeAttributeValues(updates)


def _configure_building_labels(lyr):
    from qgis.core import (
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
    )
    pal = QgsPalLayerSettings()
    pal.enabled = True
    pal.fieldName = "building_label"
    pal.setFormat(_label_format("#222222", 9.0))
    try:
        pal.placement = QgsPalLayerSettings.PredefinedPointPosition.OverPoint
    except Exception:
        pass
    try:
        pal.displayAll = True
    except Exception:
        pass
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)


def _building_fill_symbol(colour_hex):
    from qgis.core import QgsFillSymbol
    from qgis.PyQt.QtGui import QColor
    fill = QColor(colour_hex)
    fill.setAlpha(150)
    return QgsFillSymbol.createSimple({
        "color":         fill.name(QColor.NameFormat.HexArgb),
        "outline_color": "#333333",
        "outline_width": "0.4",
    })


def rebuild_building_renderer(lyr=None):
    """Rebuild the categorised renderer so every building_id keeps its own
    colour and legend entry.  Each building's colour is stored on the feature
    so it is stable across rebuilds."""
    from qgis.core import (
        QgsProject, QgsCategorizedSymbolRenderer, QgsRendererCategory,
    )
    if lyr is None:
        lyrs = QgsProject.instance().mapLayersByName(BUILDING_LAYER_NAME)
        if not lyrs:
            return
        lyr = lyrs[0]

    rows = sorted(
        [(str(f["building_label"] or f["building_id"]), f["colour"])
         for f in lyr.getFeatures() if f["building_id"]],
        key=lambda r: _id_sort_key(r[0]),
    )
    categories = []
    for bid, colour in rows:
        colour = colour or building_colour_for(len(categories))
        categories.append(
            QgsRendererCategory(bid, _building_fill_symbol(colour), bid)
        )
    lyr.setRenderer(QgsCategorizedSymbolRenderer("building_label", categories))
    lyr.triggerRepaint()


def assign_next_building(lyr, fid):
    """Assign building_id + colour to a freshly-digitised feature *fid* that is
    already in the layer's edit buffer (the polygon-draw path).  The caller is
    responsible for committing and rebuilding the renderer.  Returns
    (building_id, colour)."""
    number = _next_building_number(lyr)
    bid    = "B%d" % number
    colour = building_colour_for(number - 1)
    i_id  = lyr.fields().indexOf("building_id")
    i_label = lyr.fields().indexOf("building_label")
    i_col = lyr.fields().indexOf("colour")
    lyr.changeAttributeValue(fid, i_id, bid)
    lyr.changeAttributeValue(fid, i_label, bid)
    lyr.changeAttributeValue(fid, i_col, colour)
    return bid, colour


def add_building(geom_27700):
    """Add a building polygon (QgsGeometry in BNG). Returns (building_id, colour)."""
    from qgis.core import QgsFeature

    lyr = get_or_create_building_layer()
    number = _next_building_number(lyr)
    bid    = "B%d" % number
    colour = building_colour_for(number - 1)

    feat = QgsFeature(lyr.fields())
    feat.setGeometry(geom_27700)
    # Set by field name because upgraded legacy layers append building_label
    # after colour, while newly-created layers define it before colour.
    feat.setAttribute("building_id", bid)
    feat.setAttribute("building_label", bid)
    feat.setAttribute("colour", colour)

    lyr.startEditing()
    lyr.addFeature(feat)
    lyr.commitChanges()

    rebuild_building_renderer(lyr)
    return bid, colour


def update_building_management(changes, removed_fids=(), colour_changes=None):
    """Rename, recolour and remove buildings, then refresh their map style.

    ``changes`` maps feature id to its new display reference.  References are
    stored in ``building_label``; ``colour_changes`` optionally maps feature
    ids to HTML colours. Both fields drive the map labels and legend categories.
    """
    from qgis.core import QgsProject
    layers = QgsProject.instance().mapLayersByName(BUILDING_LAYER_NAME)
    if not layers:
        return False
    lyr = layers[0]
    _ensure_building_fields(lyr)
    label_idx = lyr.fields().indexFromName("building_label")
    colour_idx = lyr.fields().indexFromName("colour")
    if label_idx < 0 or colour_idx < 0:
        return False
    removed = {int(fid) for fid in removed_fids}
    updates = {
        int(fid): {label_idx: str(label or "").strip()[:120]}
        for fid, label in changes.items() if int(fid) not in removed
    }
    from qgis.PyQt.QtGui import QColor
    for fid, colour in (colour_changes or {}).items():
        fid = int(fid)
        parsed = QColor(str(colour or ""))
        if fid not in removed and parsed.isValid():
            updates.setdefault(fid, {})[colour_idx] = parsed.name()
    if not lyr.isEditable():
        lyr.startEditing()
    for fid in removed:
        lyr.deleteFeature(fid)
    for fid, attrs in updates.items():
        for field_idx, value in attrs.items():
            lyr.changeAttributeValue(fid, field_idx, value)
    if not lyr.commitChanges():
        lyr.rollBack()
        return False
    rebuild_building_renderer(lyr)
    return True


# ── Tree layer ──────────────────────────────────────────────────────────────

def get_or_create_tree_layer():
    """Return the trees point layer, creating it (and the group) if absent."""
    from qgis.core import QgsProject, QgsVectorLayer

    project = QgsProject.instance()
    for lyr in project.mapLayersByName(TREE_LAYER_NAME):
        if lyr.geometryType() == 0:      # Point
            _ensure_group()
            _tag_for_persistence(lyr, "trees_map_only")
            _ensure_tree_fields(lyr)
            _configure_tree_labels(lyr)
            rebuild_tree_renderer(lyr)
            return lyr

    lyr = QgsVectorLayer(
        "Point?crs=EPSG:27700"
        "&field=tree_id:string(10)"
        "&field=tree_label:string(120)"
        "&field=status:string(12)"
        "&field=buffer_m:double"
        "&field=rpa_m:double",
        TREE_LAYER_NAME, "memory",
    )
    rebuild_tree_renderer(lyr)

    _configure_tree_labels(lyr)
    lyr.setCustomProperty("biome_map_only", 1)   # tag: never counts in BNG
    _tag_for_persistence(lyr, "trees_map_only")

    project.addMapLayer(lyr, False)
    _trees_group().insertLayer(0, lyr)
    return lyr


def _ensure_tree_fields(lyr):
    """Upgrade tree layers created by older plugin versions in place."""
    from qgis.core import QgsField
    from qgis.PyQt.QtCore import QVariant

    provider = lyr.dataProvider()
    missing = []
    if lyr.fields().indexFromName("tree_label") < 0:
        missing.append(QgsField("tree_label", QVariant.String, len=120))
    if lyr.fields().indexFromName("status") < 0:
        missing.append(QgsField("status", QVariant.String, len=12))
    if lyr.fields().indexFromName("buffer_m") < 0:
        missing.append(QgsField("buffer_m", QVariant.Double))
    if lyr.fields().indexFromName("rpa_m") < 0:
        missing.append(QgsField("rpa_m", QVariant.Double))
    if missing:
        provider.addAttributes(missing)
        lyr.updateFields()

    status_idx = lyr.fields().indexFromName("status")
    buffer_idx = lyr.fields().indexFromName("buffer_m")
    rpa_idx = lyr.fields().indexFromName("rpa_m")
    label_idx = lyr.fields().indexFromName("tree_label")
    tree_id_idx = lyr.fields().indexFromName("tree_id")
    updates = {}
    for feat in lyr.getFeatures():
        row = {}
        if not str(feat[label_idx] or "").strip():
            row[label_idx] = str(feat[tree_id_idx] or "")
        if not str(feat[status_idx] or "").strip():
            row[status_idx] = "Retained"
        if feat[buffer_idx] in (None, ""):
            row[buffer_idx] = 0.0
        if feat[rpa_idx] in (None, ""):
            row[rpa_idx] = 0.0
        if row:
            updates[feat.id()] = row
    if updates:
        provider.changeAttributeValues(updates)


def _configure_tree_labels(lyr):
    """Label trees from the long display field rather than the short ID."""
    from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling
    pal = QgsPalLayerSettings()
    pal.enabled = True
    pal.fieldName = "tree_label"
    pal.setFormat(_label_format("#333333", 8.0))
    try:
        pal.placement = QgsPalLayerSettings.Placement.AroundPoint
        pal.dist = 1.2
    except Exception:
        pass
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)


def _tree_marker(colour, outline):
    from qgis.core import QgsMarkerSymbol
    return QgsMarkerSymbol.createSimple({
        "name": "circle", "color": colour, "outline_color": outline,
        "outline_width": "0.3", "size": "3.0",
    })


def rebuild_tree_renderer(lyr=None):
    """Render retained trees green and trees marked for removal red."""
    from qgis.core import (
        QgsProject, QgsCategorizedSymbolRenderer, QgsRendererCategory,
    )
    if lyr is None:
        layers = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
        if not layers:
            return
        lyr = layers[0]
    _ensure_tree_fields(lyr)
    categories = [
        QgsRendererCategory(
            "Retained", _tree_marker(TREE_COLOUR, "#1b4d1f"), "Retained trees"),
        QgsRendererCategory(
            "Removed", _tree_marker(TREE_REMOVED_COLOUR, "#7F1D1D"), "Trees for removal"),
    ]
    lyr.setRenderer(QgsCategorizedSymbolRenderer("status", categories))
    lyr.triggerRepaint()


def _buffer_fill_symbol(fill_hex, outline_hex):
    from qgis.core import QgsFillSymbol
    from qgis.PyQt.QtGui import QColor
    fill = QColor(fill_hex)
    fill.setAlpha(42)
    return QgsFillSymbol.createSimple({
        "color": fill.name(QColor.NameFormat.HexArgb),
        "outline_color": outline_hex,
        "outline_width": "0.55",
        "outline_style": "dash",
    })


def _rpa_fill_symbol():
    """Magenta RPA outline, following standard arboricultural plan styling."""
    from qgis.core import QgsFillSymbol
    return QgsFillSymbol.createSimple({
        "color": "255,0,255,10",
        "outline_color": "#E000E0",
        "outline_width": "0.55",
        "outline_style": "solid",
    })


def _redline_geometry_27700():
    """Return the combined red-line geometry transformed to EPSG:27700."""
    from qgis.core import (
        QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
        QgsProject,
    )
    from .layer_manager import REDLINE_NAME_ALIASES

    project = QgsProject.instance()
    destination = QgsCoordinateReferenceSystem("EPSG:27700")
    geometries = []
    for name in REDLINE_NAME_ALIASES:
        layers = project.mapLayersByName(name)
        if not layers:
            continue
        layer = layers[0]
        transform = None
        if layer.crs() != destination:
            transform = QgsCoordinateTransform(
                layer.crs(), destination, project.transformContext())
        for feature in layer.getFeatures():
            geometry = QgsGeometry(feature.geometry())
            if geometry.isEmpty():
                continue
            if transform is not None:
                geometry.transform(transform)
            geometries.append(geometry)
        break
    return QgsGeometry.unaryUnion(geometries) if geometries else None


def _clip_zone_geometry(geometry, layer):
    """Clip a derived zone when its persistent crop option is enabled."""
    try:
        crop = int(layer.customProperty("biome_crop_to_redline", 0)) == 1
    except (TypeError, ValueError):
        crop = False
    if not crop:
        return geometry
    redline = _redline_geometry_27700()
    if redline is None or redline.isEmpty():
        return geometry
    return geometry.intersection(redline)


def rebuild_tree_buffers(tree_layer=None):
    """Rebuild the map-only polygon layer for trees with a positive buffer."""
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature,
        QgsCategorizedSymbolRenderer, QgsRendererCategory,
    )

    project = QgsProject.instance()
    if tree_layer is None:
        trees = project.mapLayersByName(TREE_LAYER_NAME)
        if not trees:
            return None
        tree_layer = trees[0]
    _ensure_tree_fields(tree_layer)

    existing = project.mapLayersByName(TREE_BUFFER_LAYER_NAME)
    if existing:
        buffers = existing[0]
        if buffers.isEditable():
            buffers.rollBack()
        ids = [f.id() for f in buffers.getFeatures()]
        if ids:
            buffers.dataProvider().deleteFeatures(ids)
    else:
        buffers = QgsVectorLayer(
            "Polygon?crs=EPSG:27700"
            "&field=tree_id:string(10)"
            "&field=status:string(12)"
            "&field=buffer_m:double",
            TREE_BUFFER_LAYER_NAME, "memory",
        )
        buffers.setCustomProperty("biome_map_only", 1)
        _tag_for_persistence(buffers, "tree_buffers_map_only")
        project.addMapLayer(buffers, False)
        _trees_group().insertLayer(1, buffers)

    feats = []
    for tree in tree_layer.getFeatures():
        try:
            radius = float(tree["buffer_m"] or 0)
        except (TypeError, ValueError):
            radius = 0.0
        if (radius <= 0 or tree.geometry().isEmpty()
                or str(tree["status"] or "Retained") != "Retained"):
            continue
        feat = QgsFeature(buffers.fields())
        geometry = _clip_zone_geometry(
            tree.geometry().buffer(radius, 48), buffers)
        if geometry.isEmpty():
            continue
        feat.setGeometry(geometry)
        feat.setAttributes([
            str(tree["tree_id"] or ""),
            str(tree["status"] or "Retained"),
            radius,
        ])
        feats.append(feat)
    if feats:
        buffers.dataProvider().addFeatures(feats)
    buffers.updateExtents()
    categories = [
        QgsRendererCategory(
            "Retained", _buffer_fill_symbol(TREE_COLOUR, TREE_COLOUR),
            "Retained tree buffers"),
        QgsRendererCategory(
            "Removed", _buffer_fill_symbol(TREE_REMOVED_COLOUR, TREE_REMOVED_COLOUR),
            "Removed tree buffers"),
    ]
    buffers.setRenderer(QgsCategorizedSymbolRenderer("status", categories))
    buffers.triggerRepaint()
    return buffers


def rebuild_tree_rpas(tree_layer=None):
    """Rebuild the map-only root protection area layer."""
    from qgis.core import (
        QgsProject, QgsVectorLayer, QgsFeature,
        QgsSingleSymbolRenderer,
    )

    project = QgsProject.instance()
    if tree_layer is None:
        trees = project.mapLayersByName(TREE_LAYER_NAME)
        if not trees:
            return None
        tree_layer = trees[0]
    _ensure_tree_fields(tree_layer)

    existing = project.mapLayersByName(TREE_RPA_LAYER_NAME)
    if existing:
        rpas = existing[0]
        if rpas.isEditable():
            rpas.rollBack()
        ids = [f.id() for f in rpas.getFeatures()]
        if ids:
            rpas.dataProvider().deleteFeatures(ids)
    else:
        rpas = QgsVectorLayer(
            "Polygon?crs=EPSG:27700"
            "&field=tree_id:string(10)"
            "&field=status:string(12)"
            "&field=rpa_m:double",
            TREE_RPA_LAYER_NAME, "memory",
        )
        rpas.setCustomProperty("biome_map_only", 1)
        _tag_for_persistence(rpas, "tree_rpas_map_only")
        project.addMapLayer(rpas, False)
        _trees_group().insertLayer(1, rpas)

    feats = []
    for tree in tree_layer.getFeatures():
        try:
            radius = float(tree["rpa_m"] or 0)
        except (TypeError, ValueError):
            radius = 0.0
        if (radius <= 0 or tree.geometry().isEmpty()
                or str(tree["status"] or "Retained") != "Retained"):
            continue
        feat = QgsFeature(rpas.fields())
        geometry = _clip_zone_geometry(
            tree.geometry().buffer(radius, 48), rpas)
        if geometry.isEmpty():
            continue
        feat.setGeometry(geometry)
        feat.setAttributes([
            str(tree["tree_id"] or ""),
            str(tree["status"] or "Retained"),
            radius,
        ])
        feats.append(feat)
    if feats:
        rpas.dataProvider().addFeatures(feats)
    rpas.updateExtents()
    rpas.setRenderer(QgsSingleSymbolRenderer(_rpa_fill_symbol()))
    rpas.triggerRepaint()
    return rpas


def _zone_fill_symbol(fill_colour, hatch_angles, hatch_colour,
                      outline_colour):
    """Semi-transparent construction overlay with one or more hatch angles."""
    from qgis.core import QgsFillSymbol, QgsLinePatternFillSymbolLayer
    from qgis.PyQt.QtGui import QColor

    fill = QColor(fill_colour)
    fill.setAlpha(24)
    symbol = QgsFillSymbol.createSimple({
        "color": fill.name(QColor.NameFormat.HexArgb),
        "outline_color": outline_colour, "outline_width": "0.5",
        "outline_style": "solid",
    })
    for angle in hatch_angles:
        hatch = QgsLinePatternFillSymbolLayer()
        hatch_color = QColor(hatch_colour)
        hatch_color.setAlpha(145)
        hatch.setColor(hatch_color)
        hatch.setLineWidth(0.28)
        hatch.setDistance(5.5)
        hatch.setLineAngle(float(angle))
        symbol.appendSymbolLayer(hatch)
    return symbol


def _rebuild_tree_zone(source_field, layer_name, table_name, fill_colour,
                       hatch_angles, hatch_colour, outline_colour,
                       exclude_field=None):
    """Build a retained-tree CEZ/RAZ layer without replacing raw areas."""
    from qgis.core import (
        QgsFeature, QgsProject, QgsSingleSymbolRenderer, QgsVectorLayer,
    )
    project = QgsProject.instance()
    trees = project.mapLayersByName(TREE_LAYER_NAME)
    if not trees:
        return None
    tree_layer = trees[0]
    _ensure_tree_fields(tree_layer)

    existing = project.mapLayersByName(layer_name)
    if existing:
        zone = existing[0]
        if zone.isEditable():
            zone.rollBack()
        ids = [feature.id() for feature in zone.getFeatures()]
        if ids:
            zone.dataProvider().deleteFeatures(ids)
    else:
        zone = QgsVectorLayer(
            "Polygon?crs=EPSG:27700&field=tree_id:string(10)&field=radius_m:double",
            layer_name, "memory",
        )
        zone.setCustomProperty("biome_map_only", 1)
        _tag_for_persistence(zone, table_name)
        project.addMapLayer(zone, False)
        node = _trees_group().insertLayer(1, zone)
        node.setItemVisibilityChecked(False)

    exclusion = None
    if exclude_field:
        exclusion_parts = []
        for tree in tree_layer.getFeatures():
            try:
                exclusion_radius = float(tree[exclude_field] or 0)
            except (TypeError, ValueError):
                exclusion_radius = 0.0
            if (exclusion_radius > 0 and not tree.geometry().isEmpty()
                    and str(tree["status"] or "Retained") == "Retained"):
                exclusion_parts.append(
                    tree.geometry().buffer(exclusion_radius, 48))
        if exclusion_parts:
            from qgis.core import QgsGeometry
            exclusion = QgsGeometry.unaryUnion(exclusion_parts)

    features = []
    for tree in tree_layer.getFeatures():
        try:
            radius = float(tree[source_field] or 0)
        except (TypeError, ValueError):
            radius = 0.0
        if (radius <= 0 or tree.geometry().isEmpty()
                or str(tree["status"] or "Retained") != "Retained"):
            continue
        geometry = _clip_zone_geometry(tree.geometry().buffer(radius, 48), zone)
        if exclusion is not None and not exclusion.isEmpty():
            geometry = geometry.difference(exclusion)
        if geometry.isEmpty():
            continue
        feature = QgsFeature(zone.fields())
        feature.setGeometry(geometry)
        feature.setAttributes([str(tree["tree_id"] or ""), radius])
        features.append(feature)
    if features:
        zone.dataProvider().addFeatures(features)
    zone.updateExtents()
    zone.setRenderer(QgsSingleSymbolRenderer(
        _zone_fill_symbol(
            fill_colour, hatch_angles, hatch_colour, outline_colour)))
    zone.triggerRepaint()
    return zone


def rebuild_construction_exclusion_zones():
    return _rebuild_tree_zone(
        "buffer_m", TREE_CEZ_LAYER_NAME, "tree_cez_map_only",
        "#FFD600", (45.0,), "#202020", "#202020")


def rebuild_restricted_activity_zones():
    return _rebuild_tree_zone(
        "rpa_m", TREE_RAZ_LAYER_NAME, "tree_raz_map_only",
        "#FF5A1F", (0.0, 90.0), "#8B1A0E", "#B42318",
        exclude_field="buffer_m")


def update_tree_management(changes):
    """Apply tree attributes/coordinates and rebuild map presentation.

    ``changes`` maps feature id to
    ``(tree_label, status, easting, northing, buffer_m, rpa_m)``. Coordinates are
    British National Grid metres, matching the layer's native EPSG:27700 CRS.
    """
    from qgis.core import QgsGeometry, QgsPointXY, QgsProject
    layers = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
    if not layers:
        return False
    lyr = layers[0]
    _ensure_tree_fields(lyr)
    label_idx = lyr.fields().indexFromName("tree_label")
    status_idx = lyr.fields().indexFromName("status")
    buffer_idx = lyr.fields().indexFromName("buffer_m")
    rpa_idx = lyr.fields().indexFromName("rpa_m")
    updates = {}
    geometry_updates = {}
    for fid, values in changes.items():
        tree_label, status, easting, northing, radius, rpa_radius = values
        current = lyr.getFeature(int(fid))
        tree_label = str(tree_label or "").strip()[:120]
        if not tree_label:
            tree_label = str(current[label_idx] or "")
        status_text = str(status or "").strip().casefold()
        if status_text.startswith("remov"):
            status = "Removed"
        elif status_text.startswith("retain"):
            status = "Retained"
        else:
            status = str(current[status_idx] or "Retained")
        try:
            radius = max(0.0, float(radius or 0))
        except (TypeError, ValueError):
            radius = 0.0
        try:
            rpa_radius = max(0.0, float(rpa_radius or 0))
        except (TypeError, ValueError):
            rpa_radius = 0.0
        updates[int(fid)] = {
            label_idx: tree_label, status_idx: status,
            buffer_idx: radius, rpa_idx: rpa_radius,
        }
        try:
            easting = float(easting)
            northing = float(northing)
            geometry_updates[int(fid)] = QgsGeometry.fromPointXY(
                QgsPointXY(easting, northing))
        except (TypeError, ValueError):
            pass
    if updates:
        lyr.dataProvider().changeAttributeValues(updates)
    if geometry_updates:
        lyr.dataProvider().changeGeometryValues(geometry_updates)
    rebuild_tree_renderer(lyr)
    rebuild_tree_buffers(lyr)
    rebuild_tree_rpas(lyr)
    if QgsProject.instance().mapLayersByName(TREE_CEZ_LAYER_NAME):
        rebuild_construction_exclusion_zones()
    if QgsProject.instance().mapLayersByName(TREE_RAZ_LAYER_NAME):
        rebuild_restricted_activity_zones()
    return True


def add_tree(point_27700):
    """Add a tree point (QgsPointXY in BNG). Returns the tree_id string."""
    from qgis.core import QgsFeature, QgsGeometry

    lyr = get_or_create_tree_layer()
    count = sum(1 for _ in lyr.getFeatures())
    tid   = "T%d" % (count + 1)

    feat = QgsFeature(lyr.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(point_27700))
    feat.setAttribute("tree_id", tid)
    feat.setAttribute("tree_label", tid)
    feat.setAttribute("status", "Retained")
    feat.setAttribute("buffer_m", 0.0)
    feat.setAttribute("rpa_m", 0.0)

    lyr.startEditing()
    lyr.addFeature(feat)
    lyr.commitChanges()
    lyr.triggerRepaint()
    return tid


def add_trees_bulk(items):
    """Add many trees in one edit session (used by the CSV importer).

    `items` is an iterable of ``(QgsPointXY in EPSG:27700, label_or_None)``.
    A tree with no label is auto-numbered ``T<n>``, continuing from the highest
    existing ``T<n>`` so an import never collides with hand-placed trees; a
    supplied label is used verbatim (trimmed to the field width).  Returns the
    number of trees added.
    """
    from qgis.core import QgsFeature, QgsGeometry
    import re

    lyr = get_or_create_tree_layer()

    # Continue auto-numbering from the current highest "T<n>" id so imported and
    # clicked trees share one sequence.
    next_num = 1
    for f in lyr.getFeatures():
        tid = f["tree_id"]
        if tid and re.fullmatch(r"T\d+", str(tid)):
            next_num = max(next_num, int(str(tid)[1:]) + 1)

    feats = []
    for pt, label in items:
        label = (str(label).strip() if label else "")
        if label:
            tid = label[:10]
            display_label = label[:120]
        else:
            tid = "T%d" % next_num
            next_num += 1
            display_label = tid
        feat = QgsFeature(lyr.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(pt))
        feat.setAttribute("tree_id", tid)
        feat.setAttribute("tree_label", display_label)
        feat.setAttribute("status", "Retained")
        feat.setAttribute("buffer_m", 0.0)
        feat.setAttribute("rpa_m", 0.0)
        feats.append(feat)

    if not feats:
        return 0
    lyr.startEditing()
    lyr.addFeatures(feats)
    lyr.commitChanges()
    lyr.triggerRepaint()
    return len(feats)


def add_tree_records(records):
    """Create spreadsheet-supplied trees and return their feature ids.

    Each record is ``(display_label, easting, northing)`` in EPSG:27700.
    Status and area values are applied afterwards by ``update_tree_management``.
    """
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY
    import re

    lyr = get_or_create_tree_layer()
    existing_ids = {feat.id() for feat in lyr.getFeatures()}
    next_num = 1
    for feat in lyr.getFeatures():
        tid = str(feat["tree_id"] or "")
        if re.fullmatch(r"T\d+", tid):
            next_num = max(next_num, int(tid[1:]) + 1)

    features = []
    for label, easting, northing in records:
        tid = "T%d" % next_num
        next_num += 1
        display_label = str(label or "").strip()[:120] or tid
        feat = QgsFeature(lyr.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(
            QgsPointXY(float(easting), float(northing))))
        feat.setAttribute("tree_id", tid)
        feat.setAttribute("tree_label", display_label)
        feat.setAttribute("status", "Retained")
        feat.setAttribute("buffer_m", 0.0)
        feat.setAttribute("rpa_m", 0.0)
        features.append(feat)

    if not features:
        return []
    lyr.startEditing()
    lyr.addFeatures(features)
    lyr.commitChanges()
    return sorted(
        feat.id() for feat in lyr.getFeatures()
        if feat.id() not in existing_ids
    )


# ── Housekeeping ──────────────────────────────────────────────────────────────

def _id_sort_key(id_str):
    """Sort B2 before B10 (numeric, not lexicographic)."""
    try:
        return int("".join(ch for ch in id_str if ch.isdigit()) or 0)
    except ValueError:
        return 0


def _scrub_layout_refs(project, lyr):
    """Drop every reference to *lyr* from print layouts BEFORE the layer is
    deleted.  The map exporter pins the layer set on its map items
    (setKeepLayerSet) and builds legends with autoUpdateModel(False), so
    neither reacts to the layer's removal on its own — an open layout
    designer re-rendering against the dead layer has hard-crashed QGIS
    (native, no Python traceback; seen 2026-07-30 clearing the availability
    layer with its map layout open)."""
    try:
        from qgis.core import QgsLayoutItemMap, QgsLayoutItemLegend
        for layout in project.layoutManager().printLayouts():
            for item in layout.items():
                try:
                    if isinstance(item, QgsLayoutItemMap):
                        layers = item.layers()
                        kept = [l for l in layers
                                if l is not None and l.id() != lyr.id()]
                        if len(kept) != len(layers):
                            item.setLayers(kept)
                            item.invalidateCache()
                    elif isinstance(item, QgsLayoutItemLegend):
                        node = item.model().rootGroup().findLayer(lyr.id())
                        if node is not None and node.parent() is not None:
                            node.parent().removeChildNode(node)
                            item.refresh()
                except Exception:
                    pass
    except Exception:
        pass


def _safe_remove_layer(project, lyr):
    """Remove one layer with the two guards every Map-Only removal needs:
    1. END any edit session first (commit; roll back if the commit fails).
       Removing an editable-with-changes layer makes QGIS pop its own
       "save changes?" prompt and then roll back DURING removal — an access
       violation on QGIS 4.2.0 (crash report 2026-07-30, Clear button).
    2. Scrub pinned layout map-item/legend references (see _scrub_layout_refs).
    """
    try:
        if lyr.isEditable():
            if not lyr.commitChanges():
                lyr.rollBack()
    except Exception:
        pass
    _scrub_layout_refs(project, lyr)
    root = project.layerTreeRoot()
    node = root.findLayer(lyr.id())
    if node is not None and node.parent():
        node.parent().removeChildNode(node)
    project.removeMapLayer(lyr.id())


def _remove_layer(name):
    from qgis.core import QgsProject
    project = QgsProject.instance()
    for lyr in project.mapLayersByName(name):
        _safe_remove_layer(project, lyr)


def clear_buildings():
    _remove_layer(BUILDING_LAYER_NAME)
    _cleanup_group()


def clear_trees():
    _remove_layer(TREE_LAYER_NAME)
    _remove_layer(TREE_BUFFER_LAYER_NAME)
    _remove_layer(TREE_RPA_LAYER_NAME)
    _remove_layer(TREE_CEZ_LAYER_NAME)
    _remove_layer(TREE_RAZ_LAYER_NAME)
    _cleanup_group()


def _cleanup_group():
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    grp = root.findGroup(_BT_GROUP_NAME)
    if grp is not None and not grp.findLayers():
        root.removeChildNode(grp)


def has_buildings():
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(BUILDING_LAYER_NAME)
    return bool(lyrs) and any(True for _ in lyrs[0].getFeatures())


def has_trees():
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
    return bool(lyrs) and any(True for _ in lyrs[0].getFeatures())


# ── Counts + undo-last (drive the placer's live count / Undo buttons) ─────────

def count_buildings():
    """Number of placed buildings (0 if the layer doesn't exist yet)."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(BUILDING_LAYER_NAME)
    if not lyrs:
        return 0
    return sum(1 for f in lyrs[0].getFeatures() if f["building_id"])


def count_trees():
    """Number of placed trees (0 if the layer doesn't exist yet)."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
    if not lyrs:
        return 0
    return sum(1 for _ in lyrs[0].getFeatures())


def _last_feature_by_id(lyr, id_field):
    """The feature with the highest numeric id (B3 beats B10's predecessor etc.),
    i.e. the most recently placed.  Returns the QgsFeature or None."""
    best, best_key = None, -1
    for f in lyr.getFeatures():
        v = f[id_field]
        if not v:
            continue
        k = _id_sort_key(v)
        if k >= best_key:
            best_key, best = k, f
    return best


def peek_last_building():
    """(layer, fid) of the building remove_last_building() would delete, or
    None. Lets the caller sync the panel's global undo stack first."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(BUILDING_LAYER_NAME)
    if not lyrs:
        return None
    feat = _last_feature_by_id(lyrs[0], "building_id")
    return (lyrs[0], feat.id()) if feat is not None else None


def peek_last_tree():
    """(layer, fid) of the tree remove_last_tree() would delete, or None."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
    if not lyrs:
        return None
    feat = _last_feature_by_id(lyrs[0], "tree_id")
    return (lyrs[0], feat.id()) if feat is not None else None


def remove_last_building():
    """Delete the most recently placed building and re-colour the legend.
    Returns the removed building_id, or None if there was nothing to remove."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(BUILDING_LAYER_NAME)
    if not lyrs:
        return None
    lyr  = lyrs[0]
    feat = _last_feature_by_id(lyr, "building_id")
    if feat is None:
        return None
    bid = feat["building_id"]
    if not lyr.isEditable():
        lyr.startEditing()
    lyr.deleteFeature(feat.id())
    lyr.commitChanges()
    rebuild_building_renderer(lyr)
    lyr.triggerRepaint()
    return bid


def remove_last_tree():
    """Delete the most recently placed tree.  Returns the removed tree_id, or
    None if there was nothing to remove."""
    from qgis.core import QgsProject
    lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
    if not lyrs:
        return None
    lyr  = lyrs[0]
    feat = _last_feature_by_id(lyr, "tree_id")
    if feat is None:
        return None
    tid = feat["tree_id"]
    if not lyr.isEditable():
        lyr.startEditing()
    lyr.deleteFeature(feat.id())
    lyr.commitChanges()
    rebuild_tree_buffers(lyr)
    rebuild_tree_rpas(lyr)
    if QgsProject.instance().mapLayersByName(TREE_CEZ_LAYER_NAME):
        rebuild_construction_exclusion_zones()
    if QgsProject.instance().mapLayersByName(TREE_RAZ_LAYER_NAME):
        rebuild_restricted_activity_zones()
    lyr.triggerRepaint()
    return tid
