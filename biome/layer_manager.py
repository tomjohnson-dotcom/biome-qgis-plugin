# layer_manager.py
import os
from qgis.core import (
    QgsVectorLayer, QgsVectorFileWriter, QgsField, QgsProject,
    QgsSingleSymbolRenderer, QgsFillSymbol,
    QgsCoordinateTransformContext, QgsWkbTypes,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
    QgsTextFormat, QgsTextBufferSettings,
    QgsMessageLog, Qgis, QgsLayerTreeGroup,
)
from qgis.PyQt.QtGui import QFont, QColor as _QColor
from qgis.PyQt.QtCore import QVariant
from .habitat_data import HABITAT_TYPES

# Use QMetaType (QGIS 3.38+) if available, else fall back to QVariant
try:
    from qgis.PyQt.QtCore import QMetaType
    _STR = QMetaType.Type.QString
except (ImportError, AttributeError):
    _STR = QVariant.String

def _f(name):
    """Return a QgsField(name, string-type) without deprecation warnings."""
    return QgsField(name, _STR)


LAYER_NAMES = {
    "redline":           "Redline Boundary",
    "site_boundary":     "Blue Line Boundary",
    "baseline_area":     "Baseline - Area Habitats",
    "baseline_water":    "Baseline - Watercourse Habitat",
    "baseline_hedgerow": "Baseline - Hedgerow Units",
    "baseline_trees":    "Baseline - Individual Trees",
    "proposed_area":     "Proposed - Area Habitats",
    "proposed_water":    "Proposed - Watercourse Habitat",
    "proposed_hedgerow": "Proposed - Hedgerow Units",
    "proposed_trees":    "Proposed - Individual Trees",
}

# The persisted/QGIS layer name remains unchanged for compatibility with saved
# projects and existing plugin logic. UI surfaces use the spaced display label,
# and lookup accepts either spelling before normalising to the canonical name.
REDLINE_INTERNAL_NAME = "Redline Boundary"
REDLINE_DISPLAY_NAME = "Red Line Boundary"
REDLINE_NAME_ALIASES = (REDLINE_INTERNAL_NAME, REDLINE_DISPLAY_NAME)


def display_layer_name(name):
    """Return the user-facing wording for a canonical Biome layer name."""
    if name == "Pond Mapper":
        return "Standing Water Bodies"
    return REDLINE_DISPLAY_NAME if name in REDLINE_NAME_ALIASES else name


def is_redline_name(name):
    return name in REDLINE_NAME_ALIASES

LAYER_GROUPS = {
    "redline":           None,
    "site_boundary":     None,
    "baseline_area":     "Baseline Habitats",
    "baseline_water":    "Baseline Habitats",
    "baseline_hedgerow": "Baseline Habitats",
    "baseline_trees":    "Baseline Habitats",
    "proposed_area":     "Proposed Habitats",
    "proposed_water":    "Proposed Habitats",
    "proposed_hedgerow": "Proposed Habitats",
    "proposed_trees":    "Proposed Habitats",
}

HABITAT_FIELDS = [
    # Internal keys
    _f("habitat_type"),
    _f("sub_habitat_type"),
    _f("habitat_key"),
    _f("condition"),
    _f("condition_detail"),   # serialised tick/score state, so an assessment can be
                              # reopened pre-filled and exported in full (survives saves)
    _f("retention"),
    _f("phase"),
    # BNG metric extra fields
    _f("strategic_significance"),
    _f("irreplaceable_habitat"),
    _f("watercourse_encroachment"),
    _f("riparian_encroachment_l"),
    _f("riparian_encroachment_r"),
    _f("user_comments"),
    _f("habitat_ref"),
    # Proposed habitat for enhanced/created features
    _f("prop_habitat_type"),
    _f("prop_sub_habitat_type"),
    _f("prop_condition"),
    # QML style attribute fields — names must match exactly what the .qml files use
    _f("Baseline Habitat Type"),
    _f("Proposed Habitat Type"),
    _f("Baseline Hedge Type"),
    _f("Proposed Hedge Type"),
    _f("Baseline River Type"),
    _f("Proposed River Type"),
    _f("Baseline Condition"),
    _f("Proposed Condition"),
    _f("Retention Category"),
    # Tree-specific QML style fields
    _f("Category"),
    _f("Baseline Tree Size"),
    _f("Baseline Rural or Urban Tree"),
    _f("Proposed Tree Size"),
    _f("Proposed Rural or Urban Tree"),
    # Field survey import fields
    _f("photo_path"),
    _f("field_survey_id"),
    _f("source"),
    # Pairing link: when a tree/hedgerow is placed as "Create Baseline +
    # Proposed", both twins share a pair_id (a uuid). Used by the tree/hedge
    # net classifier to decide Retained (both present), Lost (baseline only)
    # or Created (proposed only) — instead of net-quantity matching, which
    # silently drops the temporal discount on removed-and-replaced features.
    _f("pair_id"),
]

# Habitat area per tree by size class (ha) — statutory values from Table 15, User Guide
TREE_SIZE_AREAS = {
    "Small":      0.0040715,
    "Medium":     0.01628602,
    "Large":      0.03664354,
    "Very Large": 0.0764538,
}

STRATEGIC_SIGNIFICANCE_OPTIONS = [
    "",                                     # blank = not set
    "Area/compensation not in local strategy/ no local strategy",
    "Location ecologically desirable but not in local strategy",
    "Formally identified in local strategy",
]

# The metric's own default and the common case: not in a local strategy area.
# Forms default to this so the field is never left blank.
DEFAULT_STRATEGIC_SIGNIFICANCE = STRATEGIC_SIGNIFICANCE_OPTIONS[1]

# Channel encroachment (metric C-1 col M). Strings must match the metric's
# G-7 lookup byte-for-byte. "N/A - Culvert" is NOT offered here — it applies
# only to culvert reaches and is set/locked automatically by the form when the
# watercourse type is "Culvert".
WATERCOURSE_ENCROACHMENT_OPTIONS = [
    "",
    "No Encroachment",
    "Minor",
    "Major",
]

# Per-bank riparian severity. The two banks are combined into the metric's exact
# col-O string by bng_multipliers.canonical_riparian_lr at export/units time.
RIPARIAN_ENCROACHMENT_OPTIONS = [
    "",
    "No Encroachment",
    "Minor",
    "Moderate",
    "Major",
]


def tree_size_from_sub_type(sub_type):
    """Extract the size keyword from a tree sub habitat type string.

    E.g. "Existing Large Urban Tree" -> "Large"
         "Proposed Small Rural Tree"  -> "Small"
    Falls back to "Small" if no keyword is found.
    """
    s = (sub_type or "").lower()
    if "very large" in s:
        return "Very Large"
    if "large" in s:
        return "Large"
    if "medium" in s:
        return "Medium"
    if "small" in s:
        return "Small"
    return "Small"


def tree_location_from_sub_type(sub_type):
    """Extract 'Urban tree' or 'Rural tree' from a tree sub habitat type string."""
    s = (sub_type or "").lower()
    if "urban" in s:
        return "Urban tree"
    return "Rural tree"


GPKG_LAYER_NAMES = {
    "redline":           "redline_boundary",
    "site_boundary":     "site_boundary",
    "baseline_area":     "baseline_area_habitats",
    "baseline_water":    "baseline_watercourse",
    "baseline_hedgerow": "baseline_hedgerow",
    "baseline_trees":    "baseline_trees",
    "proposed_area":     "proposed_area_habitats",
    "proposed_water":    "proposed_watercourse",
    "proposed_hedgerow": "proposed_hedgerow",
    "proposed_trees":    "proposed_trees",
}

# Layer names used by older plugin builds (before the Baseline/Proposed split
# and the "Baseline - …" naming). Used only to identify leftover empty memory
# layers so they can be cleaned up after their data is recovered from the
# GeoPackage. Never used to create or write data.
LEGACY_LAYER_NAMES = {
    "Area Habitats", "Watercourse Habitat", "Hedgerow Units",
    "Individual Trees", "Redline Boundary", "Blue Line Boundary",
}


class LayerManager:

    def __init__(self, iface):
        self.iface = iface
        # When True, the user chose "Just looking" earlier in this session, so
        # subsequent place/draw actions create memory-backed scratch layers
        # without re-prompting. Shared across the panel and the eco-feature
        # dialog so the prompt only appears once per session.
        self.scratch_mode = False
        # Re-entrancy guard for heal_project_layers (recover + dedupe both add
        # and remove layers, which fire signals that could re-trigger a heal).
        self._healing = False

    # ------------------------------------------------------------------ #
    #  PUBLIC API                                                          #
    # ------------------------------------------------------------------ #

    def get_or_create_layer(self, layer_key):
        name = LAYER_NAMES[layer_key]
        existing = self._find_layer(name)
        if existing:
            self._ensure_pair_id(existing)
            return existing
        return self._create_layer(layer_key, name)

    def get_layer(self, layer_key):
        layer = self._find_layer(LAYER_NAMES[layer_key])
        if layer is not None:
            self._ensure_pair_id(layer)
        return layer

    @staticmethod
    def _ensure_pair_id(layer):
        """Add the pair_id field to an existing layer that pre-dates it.
        Safe to call repeatedly; no-op once the field exists."""
        try:
            if layer is None or not layer.isValid():
                return
            if layer.fields().indexOf("pair_id") >= 0:
                return
            # Only habitat layers carry the full schema; skip name-only layers.
            if layer.fields().indexOf("sub_habitat_type") < 0:
                return
            layer.dataProvider().addAttributes([_f("pair_id")])
            layer.updateFields()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  ORPHANED-DATA RECOVERY                                              #
    # ------------------------------------------------------------------ #

    def _gpkg_tables(self, gpkg_path):
        """Return the set of feature-table names present in the GeoPackage."""
        try:
            import osgeo.ogr as ogr
            ds = ogr.Open(gpkg_path)
            if ds is not None:
                names = {ds.GetLayerByIndex(i).GetName()
                         for i in range(ds.GetLayerCount())}
                ds = None
                return names
        except Exception:
            pass
        # Fall back to reading the GeoPackage as plain SQLite.
        try:
            import sqlite3
            con = sqlite3.connect(gpkg_path)
            rows = con.execute(
                "SELECT table_name FROM gpkg_contents WHERE data_type='features'"
            ).fetchall()
            con.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def recover_orphaned_gpkg_layers(self):
        """Reconnect habitat data that lives in the project GeoPackage but is
        not currently bound to a correctly-named, GeoPackage-backed layer.

        Heals projects whose layers were saved as in-memory ('scratch') layers:
        QGIS does not persist memory-layer features into a project, so on reopen
        those layers come back empty even though the data is safe in the
        GeoPackage. For each expected layer we either repoint a stale memory
        layer in place (keeping its id/style/tree position) or load the table
        afresh under the correct name. Returns the number of layers recovered.
        Safe no-op for healthy projects."""
        from qgis.core import QgsVectorLayer, QgsDataProvider
        gpkg = self.gpkg_path()
        if not gpkg or not os.path.exists(gpkg):
            return 0
        tables = self._gpkg_tables(gpkg)
        if not tables:
            return 0

        recovered = 0
        for layer_key, name in LAYER_NAMES.items():
            table = GPKG_LAYER_NAMES.get(layer_key)
            if not table or table not in tables:
                continue
            uri = f"{gpkg}|layername={table}"
            existing = self._find_layer(name)
            if existing is not None:
                # Already bound correctly to this GeoPackage table — leave it.
                if (existing.isValid()
                        and existing.providerType() == "ogr"
                        and table in existing.source()):
                    continue
                # Stale memory layer under the right name — repoint in place so
                # its id, style and layer-tree position are preserved.
                if existing.providerType() == "memory":
                    existing.setDataSource(uri, name, "ogr",
                                           QgsDataProvider.ProviderOptions())
                    if existing.isValid():
                        # Heal runs during project-open — avoid the crashing QML load.
                        self._apply_style(existing, layer_key, from_qml=False)
                        existing.setLabelsEnabled(False)
                        existing.removeCustomProperty("biome_scratch")
                        existing.removeCustomProperty("skipMemoryLayersCheck")
                        existing.triggerRepaint()
                        recovered += 1
                        continue
            # No correctly-named layer present — load the table afresh.
            layer = QgsVectorLayer(uri, name, "ogr")
            if not layer.isValid():
                continue
            # Heal runs during project-open — avoid the crashing QML load.
            self._apply_style(layer, layer_key, from_qml=False)
            layer.setLabelsEnabled(False)
            layer.setCustomProperty("skipMemoryLayersCheck", 1)
            QgsProject.instance().addMapLayer(layer, False)
            self._place_in_tree(layer, layer_key)
            recovered += 1

        if recovered:
            self._remove_stale_empty_memory_layers()
            QgsMessageLog.logMessage(
                f"Recovered {recovered} orphaned layer(s) from {os.path.basename(gpkg)}",
                "Biome", Qgis.MessageLevel.Info)
        return recovered

    def _remove_stale_empty_memory_layers(self):
        """Drop leftover empty in-memory biome layers (current or legacy names)
        once their data has been recovered from the GeoPackage, so the layer
        tree does not show duplicate or empty habitat layers."""
        from qgis.core import QgsVectorLayer
        known = set(LAYER_NAMES.values()) | LEGACY_LAYER_NAMES
        proj = QgsProject.instance()
        to_remove = []
        for layer in list(proj.mapLayers().values()):
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.providerType() != "memory":
                continue
            is_biome = (layer.name() in known
                        or str(layer.customProperty("biome_scratch", "")) == "1"
                        or bool(layer.customProperty("biome_gpkg_table", "")))
            if is_biome and layer.featureCount() == 0:
                to_remove.append(layer.id())
        for lid in to_remove:
            proj.removeMapLayer(lid)

    @staticmethod
    def _gpkg_table_of(layer):
        """Return the GeoPackage table a layer reads from, or None.

        Parses '…/foo.gpkg|layername=<table>' (ignoring any trailing
        |subset=… part). Memory layers and non-GPKG sources return None."""
        try:
            src = layer.source() if layer.isValid() else ""
        except Exception:
            return None
        if "|layername=" not in src:
            return None
        return src.split("|layername=")[-1].split("|")[0]

    def dedupe_biome_layers(self):
        """Collapse duplicate biome layers that read the same GeoPackage table
        down to a single canonical layer, and remove duplicate layer-tree nodes.

        Heals projects corrupted by historic save/recovery cycles that created
        more than one layer for a table (e.g. both 'Area Habitats' and
        'Baseline - Area Habitats', or two copies of the proposed layer), which
        otherwise show up as doubled entries in the map legend. Returns the
        number of duplicate layers removed."""
        from qgis.core import QgsVectorLayer
        proj = QgsProject.instance()
        # gpkg table -> (layer_key, canonical display name)
        table_to_key = {GPKG_LAYER_NAMES[k]: (k, LAYER_NAMES[k]) for k in LAYER_NAMES}

        by_table = {}   # table -> [layers]
        for layer in list(proj.mapLayers().values()):
            if not isinstance(layer, QgsVectorLayer):
                continue
            table = self._gpkg_table_of(layer)
            if table in table_to_key:
                by_table.setdefault(table, []).append(layer)

        removed = 0
        for table, layers in by_table.items():
            layer_key, canonical = table_to_key[table]
            # Prefer a layer already carrying the canonical name as the keeper.
            keeper = next((l for l in layers if l.name() == canonical), layers[0])
            if keeper.name() != canonical:
                keeper.setName(canonical)
            for l in layers:
                if l.id() == keeper.id():
                    continue
                proj.removeMapLayer(l.id())   # also drops its layer-tree nodes
                removed += 1

        self._dedupe_tree_nodes()
        if removed:
            QgsMessageLog.logMessage(
                f"Removed {removed} duplicate biome layer(s)", "Biome", Qgis.MessageLevel.Info)
        return removed

    def _dedupe_tree_nodes(self):
        """Ensure each layer id appears at most once in the layer tree, so a
        layer placed into the tree more than once is not drawn/legended twice."""
        root = QgsProject.instance().layerTreeRoot()
        seen = set()
        for node in list(root.findLayers()):
            lid = node.layerId()
            if lid in seen:
                parent = node.parent()
                if parent is not None:
                    parent.removeChildNode(node)
            else:
                seen.add(lid)

    # Canonical tree-marker diameters (mm) per size class — must match the
    # bundled Individual trees QMLs. Projects styled before a size change keep
    # the old renderer in their project, so the heal pass re-syncs live layers to
    # these values (pure setRenderer — safe on the project-open path).
    TREE_SYMBOL_SIZES_MM = {
        "Very Large": 9.8,
        "Large":      7.8,
        "Medium":     5.6,
        "Small":      3.0,
    }

    def sync_tree_symbol_sizes(self):
        """Bring both Individual Trees layers' rule-marker sizes in line with
        TREE_SYMBOL_SIZES_MM. Idempotent; leaves colours (e.g. greenified
        created trees) and rule labels untouched."""
        from qgis.core import QgsRuleBasedRenderer, QgsMarkerSymbol
        for layer_key in ("baseline_trees", "proposed_trees"):
            layer = self._find_layer(LAYER_NAMES[layer_key])
            if layer is None:
                continue
            renderer = layer.renderer()
            if not isinstance(renderer, QgsRuleBasedRenderer):
                continue
            clone = renderer.clone()
            changed = False

            def _walk(rule):
                nonlocal changed
                for child in rule.children():
                    if child.children():
                        _walk(child)
                        continue
                    # First label line only — baked area/units figures live on
                    # extra lines. "Very Large" must match before "Large".
                    label = (child.label() or "").split("\n")[0]
                    if "Tree" not in label:
                        continue
                    size_mm = None
                    for cls, mm in self.TREE_SYMBOL_SIZES_MM.items():
                        if cls in label:
                            size_mm = mm
                            break
                    sym = child.symbol()
                    if (size_mm is not None
                            and isinstance(sym, QgsMarkerSymbol)
                            and abs(sym.size() - size_mm) > 0.01):
                        sym.setSize(size_mm)
                        changed = True

            _walk(clone.rootRule())
            if changed:
                layer.setRenderer(clone)
                layer.triggerRepaint()

    def heal_project_layers(self):
        """Reconnect orphaned GeoPackage data and collapse duplicate layers /
        tree nodes, in one idempotent pass.

        This is the single entry point used by every project-open and
        late-layer-loaded path. It is safe to call repeatedly and re-entrant
        calls (triggered by the add/remove signals it fires itself) are
        ignored. Returns the number of layers changed (recovered + removed) so
        callers can decide whether a UI refresh is needed; 0 means the project
        was already clean."""
        if self._healing:
            return 0
        self._healing = True
        try:
            recovered = self.recover_orphaned_gpkg_layers()
            removed = self.dedupe_biome_layers()
            self.ensure_habitat_fields()
            promoted = self.promote_stray_rasters()
            self.sync_tree_symbol_sizes()
            return (recovered or 0) + (removed or 0) + (promoted or 0)
        except Exception:
            return 0
        finally:
            self._healing = False

    def ensure_habitat_fields(self):
        """Add any HABITAT_FIELDS missing from existing habitat layers, so a
        project saved by an older plugin version gains new columns (e.g.
        ``condition_detail``) on open. Habitat layers are identified by the
        ``sub_habitat_type`` field; idempotent and safe to call repeatedly."""
        changed = 0
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            names = set(lyr.fields().names())
            if "sub_habitat_type" not in names:
                continue
            missing = [fld for fld in HABITAT_FIELDS if fld.name() not in names]
            if missing:
                try:
                    lyr.dataProvider().addAttributes(missing)
                    lyr.updateFields()
                    changed += 1
                except Exception:
                    pass
        return changed

    def ensure_layers_in_tree(self):
        """After reopening a project, layers exist in the registry but may not
        be visible in the layer tree. Re-inserts any that are missing and
        restores the required redline/blue-line order."""
        self.ensure_tree_structure()
        root = QgsProject.instance().layerTreeRoot()
        for layer_key, name in LAYER_NAMES.items():
            layer = self._find_layer(name)
            if layer is None:
                continue
            # Check if this layer is already somewhere in the tree
            if root.findLayer(layer.id()) is not None:
                continue
            # Not in tree — place it in its correct group
            self._place_in_tree(layer, layer_key)
        self._ensure_boundary_layer_order()

    def _ensure_boundary_layer_order(self):
        """Keep the Blue Line Boundary immediately below the redline."""
        root = QgsProject.instance().layerTreeRoot()
        redline = self._find_layer(LAYER_NAMES["redline"])
        blue_line = self._find_layer(LAYER_NAMES["site_boundary"])
        if redline is None or blue_line is None:
            return
        red_node = root.findLayer(redline.id())
        blue_node = root.findLayer(blue_line.id())
        if red_node is None or blue_node is None:
            return
        target_parent = red_node.parent()
        siblings = list(target_parent.children())
        if (blue_node.parent() is target_parent
                and siblings.index(blue_node) == siblings.index(red_node) + 1):
            return

        # Clone before removing so visibility and other layer-tree properties
        # are retained when moving a node from an old project/group.
        moved_node = blue_node.clone()
        old_parent = blue_node.parent()
        old_parent.removeChildNode(blue_node)
        red_node = root.findLayer(redline.id())
        target_parent = red_node.parent()
        insert_at = list(target_parent.children()).index(red_node) + 1
        target_parent.insertChildNode(insert_at, moved_node)

    def ensure_tree_structure(self):
        root = QgsProject.instance().layerTreeRoot()
        # Heal duplicate core groups first. A PyQt6 quirk made EMPTY groups
        # falsy, so the find-or-create checks below used to re-create groups
        # that already existed; projects touched by that build carry doubles.
        # Merge each duplicate's children into the first occurrence and drop it.
        for _name in ("Ecological Features", "Baseline Habitats",
                      "Proposed Habitats", "Base Maps"):
            _same = [c for c in root.children()
                     if isinstance(c, QgsLayerTreeGroup) and c.name() == _name]
            _keep = _same[0] if _same else None
            for _extra in _same[1:]:
                for _child in list(_extra.children()):
                    _clone = _child.clone()
                    _keep.addChildNode(_clone)
                    _extra.removeChildNode(_child)
                root.removeChildNode(_extra)

        # Ensure both habitat groups exist, then enforce the order:
        #   Baseline Habitats  (top — rendered on top of proposed)
        #   Proposed Habitats  (below baseline)
        #   Base Maps          (bottom)
        #
        # We always reorder, not just on first creation, so that loading a
        # saved project with the wrong order gets corrected too.
        base_maps = root.findGroup("Base Maps")
        bm_idx = root.children().index(base_maps) if base_maps is not None else len(root.children())

        # Create missing groups (inserted just above Base Maps)
        if root.findGroup("Proposed Habitats") is None:
            root.insertGroup(bm_idx, "Proposed Habitats")
            bm_idx = root.children().index(base_maps) if base_maps is not None else len(root.children())
        if root.findGroup("Baseline Habitats") is None:
            root.insertGroup(bm_idx, "Baseline Habitats")

        # Now enforce group order regardless of how they got there.
        # Target: [..., Baseline Habitats, Proposed Habitats, Base Maps, ...]
        children = root.children()
        names = [c.name() for c in children]
        baseline_idx = next((i for i, n in enumerate(names) if n == "Baseline Habitats"), None)
        proposed_idx = next((i for i, n in enumerate(names) if n == "Proposed Habitats"), None)
        if baseline_idx is not None and proposed_idx is not None:
            if baseline_idx > proposed_idx:
                # Baseline is below Proposed — move Baseline above Proposed
                baseline_node = root.findGroup("Baseline Habitats")
                clone = baseline_node.clone()
                root.insertChildNode(proposed_idx, clone)
                root.removeChildNode(baseline_node)

        # Enforce layer order within each habitat group:
        #   Trees (index 0, top) → Hedgerow → Watercourse → Area (bottom)
        # This corrects any mis-ordering in saved projects.
        _LAYER_ORDER = ["trees", "hedgerow", "water", "area"]

        def _sort_key(node_name):
            n = node_name.lower()
            for i, token in enumerate(_LAYER_ORDER):
                if token in n:
                    return i
            return 99

        for group_name in ("Baseline Habitats", "Proposed Habitats"):
            group = root.findGroup(group_name)
            if group is None:
                continue
            layer_nodes = list(group.children())
            if len(layer_nodes) < 2:
                continue
            # Sort nodes by desired order
            sorted_nodes = sorted(layer_nodes, key=lambda n: _sort_key(n.name()))
            # Check if already in the right order
            if [n.name() for n in layer_nodes] == [n.name() for n in sorted_nodes]:
                continue
            # Re-insert in correct order: remove all then re-add sorted
            clones = [n.clone() for n in sorted_nodes]
            for node in layer_nodes:
                group.removeChildNode(node)
            for clone in clones:
                group.addChildNode(clone)

        # Ecological Features always sits at position 0 so it renders on top
        # of everything, including the Redline Boundary.
        eco_group = root.findGroup("Ecological Features")
        if eco_group is None:
            eco_group = root.insertGroup(0, "Ecological Features")
        # Ensure sub-groups exist in render order: Area below Points/Lines
        if eco_group.findGroup("Point Features") is None:
            eco_group.insertGroup(0, "Point Features")
        if eco_group.findGroup("Linear Features") is None:
            eco_group.insertGroup(1, "Linear Features")
        if eco_group.findGroup("Area Features") is None:
            eco_group.insertGroup(2, "Area Features")
        if eco_group.findGroup("Buffer Zones") is None:
            eco_group.insertGroup(3, "Buffer Zones")

    # Plugin groups that must never hold a stray raster. A georeferenced or
    # imported raster lands inside one of these when it happens to be the active
    # layer-tree node at load time (QGIS inserts new layers at the active node).
    # Such a raster is invisible in Biome's top-level-only layer panel — it is
    # hidden within the group's single eye toggle — so it gets lifted out onto
    # its own top-level node. "Base Maps" is included: its legitimate rasters
    # (the recognised basemaps) are protected by name via
    # _BASEMAP_RASTER_NAMES, so anything else found inside it is a stray.
    _RASTER_RESCUE_GROUPS = {
        "Base Maps", "Baseline Habitats", "Proposed Habitats",
        "Ecological Features",
    }

    # Basemap raster names that must stay in Base Maps, never relocated.
    _BASEMAP_RASTER_NAMES = {
        "Google Hybrid", "Google Satellite", "OS Maps (Raster)",
        "OS Outdoor", "ESRI World Imagery",
    }

    _GEOREF_NAME_PREFIX = "Georeferenced Layer"

    def autoname_georeferenced_layers(self, layers=None):
        """Give newly added local rasters stable, conventional display names.

        QGIS normally exposes the output filename, which is often long or
        cryptic. Local GDAL rasters handled by Biome are therefore named
        ``Georeferenced Layer 1``, ``Georeferenced Layer 2``, and so on. A
        custom property prevents later project heals from overwriting a name
        the user has subsequently edited themselves.
        """
        import re
        from qgis.core import QgsRasterLayer

        project = QgsProject.instance()
        all_layers = list(project.mapLayers().values())
        candidates = all_layers if layers is None else list(layers)
        pattern = re.compile(
            r"^" + re.escape(self._GEOREF_NAME_PREFIX) + r"\s+(\d+)$",
            re.IGNORECASE,
        )

        used = set()
        for layer in all_layers:
            match = pattern.match(layer.name() or "")
            if match:
                used.add(int(match.group(1)))

        next_number = max(used, default=0) + 1
        renamed = 0
        for layer in candidates:
            if (not isinstance(layer, QgsRasterLayer) or not layer.isValid()
                    or layer.name() in self._BASEMAP_RASTER_NAMES
                    or layer.providerType().lower() != "gdal"):
                continue
            if str(layer.customProperty(
                    "biome_georeferenced_auto_named", "")) == "1":
                continue
            match = pattern.match(layer.name() or "")
            if match:
                layer.setCustomProperty("biome_georeferenced_auto_named", "1")
                continue
            while next_number in used:
                next_number += 1
            layer.setName(f"{self._GEOREF_NAME_PREFIX} {next_number}")
            layer.setCustomProperty("biome_georeferenced_auto_named", "1")
            used.add(next_number)
            next_number += 1
            renamed += 1
        return renamed

    def promote_stray_rasters(self):
        """Place every georeferenced / imported raster as its OWN top-level
        layer directly UNDER the Proposed Habitats group (i.e. just above Base
        Maps), wherever QGIS first dropped it.

        The QGIS Georeferencer loads its output at the layer tree's active
        insertion point — sometimes *inside* Baseline/Proposed/Ecological
        Features or Base Maps (invisible in Biome's top-level-only layer
        panel), sometimes at the very TOP of the tree (rendering over the
        habitats). Either way, a drone/aerial raster is background imagery and
        belongs beneath the mapping: this moves it to a top-level node right
        below Proposed Habitats so it renders under the habitats and above the
        basemap. Recognised basemaps (by name) and user-made groups are left
        untouched.
        Returns the number of rasters moved; safe no-op when all are placed."""
        from qgis.core import QgsRasterLayer
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        renamed = self.autoname_georeferenced_layers(
            project.mapLayers().values())
        moved = 0
        for layer in list(project.mapLayers().values()):
            if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
                continue
            if layer.name() in self._BASEMAP_RASTER_NAMES:
                continue                        # a basemap — leave in Base Maps
            node = root.findLayer(layer.id())
            if node is None:
                continue
            parent = node.parent()
            if parent is None:
                continue

            top_level = parent.parent() is None
            if top_level:
                container_name = None
            else:
                # Walk up to the top-level ancestor group (parent is the root).
                # Only parent()/is-None checks — never an identity compare to
                # the root, which is unreliable across sip wrappers.
                top = parent
                while top.parent() is not None and top.parent().parent() is not None:
                    top = top.parent()
                container_name = top.name()
                if container_name not in self._RASTER_RESCUE_GROUPS:
                    continue                    # user-made group — leave alone

            base_maps = root.findGroup("Base Maps")
            children = root.children()
            desired_idx = (children.index(base_maps)
                           if base_maps is not None else len(children))

            # Already a top-level node sitting directly above Base Maps (= under
            # Proposed)? Then it's correctly placed — don't churn it every heal.
            if top_level:
                cur_idx = children.index(node)
                if cur_idx == desired_idx - 1 or (base_maps is None
                                                  and cur_idx == len(children) - 1):
                    continue

            # clone-then-remove is QGIS's move idiom: the map layer survives in
            # the registry (now referenced by the clone); only the tree node moves.
            root.insertChildNode(desired_idx, node.clone())
            parent.removeChildNode(node)
            moved += 1
        if moved or renamed:
            QgsMessageLog.logMessage(
                f"Prepared {moved + renamed} georeferenced raster update(s) "
                f"({renamed} named, {moved} repositioned)",
                "Biome", Qgis.MessageLevel.Info)
        return moved + renamed

    def place_layer_in_group(self, layer, group_name: str):
        """Add a layer to a named group, creating the group if needed.
        The group is inserted just above Base Maps (or at the top if no Base Maps)."""
        root = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(group_name)
        if group is None:
            base_maps = root.findGroup("Base Maps")
            idx = root.children().index(base_maps) if base_maps is not None else len(root.children())
            group = root.insertGroup(idx, group_name)
        # Only add if not already in this group
        existing_ids = {n.layerId() for n in group.findLayers()}
        if layer.id() not in existing_ids:
            group.addLayer(layer)
        _collapse_tree_except(root)

    def collapse_eco_group(self):
        """Collapse the Ecological Features group (and its sub-groups) in the
        layer panel. Called when a project is opened so the group starts
        collapsed like the other layer groups, regardless of the expanded
        state saved in the project file."""
        from qgis.core import QgsLayerTreeGroup
        root = QgsProject.instance().layerTreeRoot()
        eco = root.findGroup("Ecological Features")
        if eco is None:
            return
        eco.setExpanded(False)
        for sub in eco.children():
            if isinstance(sub, QgsLayerTreeGroup):
                sub.setExpanded(False)

    def gpkg_path(self):
        project_path = QgsProject.instance().fileName()
        if not project_path:
            return None
        project_dir  = os.path.dirname(project_path)
        project_name = os.path.splitext(os.path.basename(project_path))[0]
        return os.path.join(project_dir, f"{project_name}_bng_data.gpkg")

    # ------------------------------------------------------------------ #
    #  LAYER CREATION                                                      #
    # ------------------------------------------------------------------ #

    def _find_layer(self, name):
        project = QgsProject.instance()
        if name in REDLINE_NAME_ALIASES:
            for candidate in REDLINE_NAME_ALIASES:
                layers = project.mapLayersByName(candidate)
                if layers:
                    layer = layers[0]
                    if layer.name() != REDLINE_INTERNAL_NAME:
                        layer.setName(REDLINE_INTERNAL_NAME)
                    return layer
            return None
        layers = project.mapLayersByName(name)
        return layers[0] if layers else None

    def _create_layer(self, layer_key, name):
        gpkg = self.gpkg_path()
        if gpkg is None:
            # No saved project yet — create a memory-backed 'just looking'
            # layer instead of failing. It is tagged so it gets written into
            # the GeoPackage automatically when the project is saved.
            return self.create_scratch_layer(layer_key)
        return self._create_gpkg_layer(layer_key, name, gpkg)

    def create_scratch_layer(self, layer_key):
        """Create a memory-backed (scratch) layer for the given key.

        Used for 'just looking' sessions where the project has not been
        saved yet, so there is no GeoPackage to write to. The layer is
        styled and placed in the tree exactly like a normal layer, but it
        is held only in memory — it will NOT survive closing the project.
        If the layer already exists, the existing one is returned.
        """
        from qgis.core import QgsCoordinateReferenceSystem
        name = LAYER_NAMES[layer_key]
        existing = self._find_layer(name)
        if existing:
            return existing

        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem("EPSG:3857")
        geom_type_str = self._geom_type(layer_key)

        layer = self._build_schema_layer(layer_key, name, geom_type_str, crs.authid())
        self._apply_style(layer, layer_key)
        layer.setLabelsEnabled(False)
        layer.setCustomProperty("skipMemoryLayersCheck", 1)
        # Tag it so other code can tell this is a throwaway scratch layer.
        layer.setCustomProperty("biome_scratch", 1)
        # Record where it should be written when the project is saved, so the
        # 'just looking' work is migrated into the GeoPackage automatically.
        layer.setCustomProperty("biome_gpkg_table", GPKG_LAYER_NAMES[layer_key])
        layer.setCustomProperty("biome_layer_key", layer_key)
        QgsProject.instance().addMapLayer(layer, False)
        self._place_in_tree(layer, layer_key)
        return layer

    def _create_gpkg_layer(self, layer_key, name, gpkg_path):
        geom_type_str = self._geom_type(layer_key)
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if not crs.isValid():
            from qgis.core import QgsCoordinateReferenceSystem
            crs = QgsCoordinateReferenceSystem("EPSG:3857")

        gpkg_layer_name = GPKG_LAYER_NAMES[layer_key]

        if os.path.exists(gpkg_path):
            uri  = f"{gpkg_path}|layername={gpkg_layer_name}"
            test = QgsVectorLayer(uri, name, "ogr")
            if test.isValid():
                self._apply_style(test, layer_key)
                test.setLabelsEnabled(False)
                test.setCustomProperty("skipMemoryLayersCheck", 1)
                QgsProject.instance().addMapLayer(test, False)
                self._place_in_tree(test, layer_key)
                return test
            # Table absent from this GPKG — safe to create it
            # But if the table IS listed in the GPKG and just failed, never overwrite
            try:
                import osgeo.ogr as ogr
                ds = ogr.Open(gpkg_path)
                if ds is not None:
                    existing = [ds.GetLayerByIndex(i).GetName()
                                for i in range(ds.GetLayerCount())]
                    ds = None
                    if gpkg_layer_name in existing:
                        QgsMessageLog.logMessage(f"WARNING: table '{gpkg_layer_name}' exists "
                              f"but layer failed to load — refusing to overwrite: {gpkg_path}",
                              "Biome", Qgis.MessageLevel.Warning)
                        return None
            except ImportError:
                pass

        mem_layer = self._build_schema_layer(layer_key, name, geom_type_str, crs.authid())

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName   = "GPKG"
        options.layerName    = gpkg_layer_name
        options.fileEncoding = "UTF-8"
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            if os.path.exists(gpkg_path)
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        )

        error, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
            mem_layer, gpkg_path,
            QgsCoordinateTransformContext(), options,
        )

        if error != QgsVectorFileWriter.WriterError.NoError:
            QgsMessageLog.logMessage(f"GPKG write error for {name}: {msg}", "Biome", Qgis.MessageLevel.Warning)
            self._apply_style(mem_layer, layer_key)
            mem_layer.setLabelsEnabled(False)
            self._tag_fallback_scratch(mem_layer, layer_key, gpkg_layer_name)
            QgsProject.instance().addMapLayer(mem_layer, False)
            self._place_in_tree(mem_layer, layer_key)
            return mem_layer

        uri   = f"{gpkg_path}|layername={gpkg_layer_name}"
        layer = QgsVectorLayer(uri, name, "ogr")
        if not layer.isValid():
            QgsMessageLog.logMessage(f"Could not reload {name} from GPKG", "Biome", Qgis.MessageLevel.Warning)
            self._apply_style(mem_layer, layer_key)
            mem_layer.setLabelsEnabled(False)
            self._tag_fallback_scratch(mem_layer, layer_key, gpkg_layer_name)
            QgsProject.instance().addMapLayer(mem_layer, False)
            self._place_in_tree(mem_layer, layer_key)
            return mem_layer

        self._apply_style(layer, layer_key)
        layer.setLabelsEnabled(False)
        layer.setCustomProperty("skipMemoryLayersCheck", 1)
        QgsProject.instance().addMapLayer(layer, False)
        self._place_in_tree(layer, layer_key)
        # Writing this new table restructured the GeoPackage that other layers
        # (the Redline Boundary above all) already have open. Their OGR provider
        # caches are now stale, so they render empty until forced to reload —
        # this is why the redline vanishes when the first habitat is placed
        # after a save and only reappears once another layer edit triggers a
        # reload. Reload every sibling bound to the same file so a normal canvas
        # refresh paints them again.
        self._reload_gpkg_siblings(gpkg_path, exclude_id=layer.id())
        return layer

    def _tag_fallback_scratch(self, mem_layer, layer_key, gpkg_layer_name):
        """Tag a memory layer created because a GeoPackage write failed so the
        save-time migrator retries it — untagged it would silently never reach
        disk and everything drawn on it would vanish with the session."""
        mem_layer.setCustomProperty("skipMemoryLayersCheck", 1)
        mem_layer.setCustomProperty("biome_scratch", 1)
        mem_layer.setCustomProperty("biome_gpkg_table", gpkg_layer_name)
        mem_layer.setCustomProperty("biome_layer_key", layer_key)
        try:
            self.iface.messageBar().pushMessage(
                "Biome",
                f"'{mem_layer.name()}' could not be written to the project "
                "GeoPackage, so it is temporarily held in memory. Saving the "
                "project will retry writing it to disk.",
                level=Qgis.MessageLevel.Warning, duration=0)
        except Exception:
            pass

    def _reload_gpkg_siblings(self, gpkg_path, exclude_id=None):
        """Force every OGR layer reading from ``gpkg_path`` to re-read from disk.

        Adding a new table to a GeoPackage invalidates the provider cache of
        any layer already loaded from that same file. Calling reload() makes
        each one re-query the (now-modified) file so a subsequent canvas
        refresh renders its features instead of a blank stale cache."""
        try:
            target = os.path.normcase(os.path.abspath(gpkg_path))
        except Exception:
            return
        for other in list(QgsProject.instance().mapLayers().values()):
            if not isinstance(other, QgsVectorLayer):
                continue
            if exclude_id is not None and other.id() == exclude_id:
                continue
            try:
                if other.providerType() != "ogr":
                    continue
                src = other.source().split("|")[0]
                if os.path.normcase(os.path.abspath(src)) != target:
                    continue
                other.reload()
                other.triggerRepaint()
            except Exception:
                continue

    def _build_schema_layer(self, layer_key, name, geom_type_str, crs_authid):
        uri   = f"{geom_type_str}?crs={crs_authid}"
        layer = QgsVectorLayer(uri, name, "memory")
        prov  = layer.dataProvider()
        if layer_key == "redline":
            prov.addAttributes([_f("name")])
        elif layer_key == "site_boundary":
            prov.addAttributes([_f("name")])
        else:
            prov.addAttributes(HABITAT_FIELDS)
        layer.updateFields()
        return layer

    # ------------------------------------------------------------------ #
    #  TREE PLACEMENT                                                      #
    # ------------------------------------------------------------------ #

    def _place_in_tree(self, layer, layer_key):
        root       = QgsProject.instance().layerTreeRoot()
        group_name = LAYER_GROUPS.get(layer_key)
        if group_name is None:
            if layer_key == "site_boundary":
                redline = self._find_layer(LAYER_NAMES["redline"])
                red_node = (root.findLayer(redline.id())
                            if redline is not None else None)
                if red_node is not None:
                    parent = red_node.parent()
                    insert_at = list(parent.children()).index(red_node) + 1
                    parent.insertLayer(insert_at, layer)
                else:
                    root.insertLayer(0, layer)
            elif layer_key == "redline":
                blue_line = self._find_layer(LAYER_NAMES["site_boundary"])
                blue_node = (root.findLayer(blue_line.id())
                             if blue_line is not None else None)
                if blue_node is not None:
                    parent = blue_node.parent()
                    insert_at = list(parent.children()).index(blue_node)
                    parent.insertLayer(insert_at, layer)
                else:
                    root.insertLayer(0, layer)
            else:
                root.insertLayer(0, layer)
        else:
            group = root.findGroup(group_name)
            if group is None:
                base_maps = root.findGroup("Base Maps")
                idx = root.children().index(base_maps) if base_maps is not None else len(root.children())
                group = root.insertGroup(idx, group_name)
            # Desired order within the group (top → bottom in panel):
            #   Trees (0) → Hedgerow (1) → Watercourse (2) → Area (3)
            # We determine the correct insertion index by counting how many
            # higher-priority layers already exist in the group, so the new
            # layer always lands in the right slot regardless of draw order.
            _PRIORITY = {"trees": 0, "hedgerow": 1, "water": 2, "area": 3}
            if "trees" in layer_key:
                new_priority = 0
            elif "hedgerow" in layer_key:
                new_priority = 1
            elif "water" in layer_key:
                new_priority = 2
            else:
                new_priority = 3

            # Count siblings with higher priority (lower number) = insert after them
            existing = list(group.children())
            insert_idx = 0
            for sibling in existing:
                sname = sibling.name().lower()
                for token, pri in _PRIORITY.items():
                    if token in sname:
                        if pri < new_priority:
                            insert_idx += 1
                        break
            group.insertLayer(insert_idx, layer)
        # Collapse all groups except Ecological Features and Base Maps
        _collapse_tree_except(root)

    # ------------------------------------------------------------------ #
    #  GEOMETRY TYPE                                                       #
    # ------------------------------------------------------------------ #

    def _geom_type(self, layer_key):
        # Watercourse and hedgerow are both linear
        if "hedgerow" in layer_key or "water" in layer_key:
            return "LineString"
        if "trees" in layer_key:
            return "Point"
        return "Polygon"

    # ------------------------------------------------------------------ #
    #  STYLING                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_buildings_symbol():
        """Charcoal roof symbol for the display-only Buildings habitat."""
        from qgis.core import QgsLinePatternFillSymbolLayer
        symbol = QgsFillSymbol.createSimple({
            "color": "#555C59",
            "outline_color": "#303633",
            "outline_width": "0.45",
        })
        hatch = QgsLinePatternFillSymbolLayer.create({
            "angle": "45",
            "color": "#C7CDCA",
            "distance": "2.6",
            "distance_unit": "MM",
            "line_width": "0.24",
            "line_width_unit": "MM",
        })
        if hatch is not None:
            symbol.appendSymbolLayer(hatch)
        return symbol

    @staticmethod
    def _ensure_buildings_category(renderer):
        """Add the display-only Buildings category to an area renderer.

        The feature's QML field contains ``Buildings`` while its internal
        ``sub_habitat_type`` remains ``Developed land; sealed surface``. This
        keeps the map/legend distinction entirely separate from metric logic.
        """
        from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory
        if not isinstance(renderer, QgsCategorizedSymbolRenderer):
            return renderer
        try:
            field = str(renderer.classAttribute() or "")
        except Exception:
            field = ""
        if field not in ("Baseline Habitat Type", "Proposed Habitat Type"):
            return renderer
        if any(str(cat.value()).strip() == "Buildings"
               for cat in renderer.categories()):
            return renderer
        renderer.addCategory(QgsRendererCategory(
            "Buildings", LayerManager._build_buildings_symbol(), "Buildings"
        ))
        return renderer

    QML_STYLES = {
        "baseline_area":     "Habitats baseline.qml",
        "proposed_area":     "Habitats proposed.qml",
        "baseline_water":    "Watercourse baseline.qml",
        "proposed_water":    "Watercourse proposed.qml",
        "baseline_hedgerow": "Hedgerows baseline.qml",
        "proposed_hedgerow": "Hedgerow proposed.qml",
        "baseline_trees":    "Individual trees baseline.qml",
        "proposed_trees":    "Individual trees proposed.qml",
    }

    def _apply_style(self, layer, layer_key, from_qml=True):
        if layer_key == "redline":
            symbol = QgsFillSymbol.createSimple({
                "color":         "transparent",
                "outline_color": "red",
                "outline_width": "0.8",
            })
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            return

        if layer_key == "site_boundary":
            symbol = QgsFillSymbol.createSimple({
                "color":         "transparent",
                "outline_color": "#0066CC",
                "outline_width": "0.8",
            })
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            return

        # The bundled styles/<name>.qml files ARE the plugin's intended styling —
        # exactly what the habitat placer applies (categorised for area/hedge/
        # water, rule-based-on-retention for trees, etc.). loadNamedStyle on them
        # is SAFE when called from a user action (placing a feature, Reset) but
        # CRASHES QGIS 3.44.x when called during project-open/heal (the
        # QgsVectorLayer::readSymbology access violation from the original crash).
        # So callers on the open/heal path pass from_qml=False.
        if from_qml:
            qml_name = self.QML_STYLES.get(layer_key)
            if qml_name:
                qml_path = os.path.join(os.path.dirname(__file__), "styles", qml_name)
                if os.path.exists(qml_path):
                    msg, ok = layer.loadNamedStyle(qml_path)
                    if ok:
                        self._ensure_buildings_category(layer.renderer())
                        if layer_key == "proposed_trees":
                            r = layer.renderer().clone()
                            self._greenify_created_trees(r)
                            layer.setRenderer(r)
                        layer.triggerRepaint()
                        return
                    QgsMessageLog.logMessage(
                        f"QML load warning for {qml_name}: {msg}", "Biome", Qgis.MessageLevel.Warning)

        # Open/heal path (from_qml=False), or the QML was missing/failed: never
        # call loadNamedStyle here. QGIS already restores each layer's saved
        # symbology on reopen, so keep an existing real renderer untouched; only
        # genuinely unstyled layers get the in-process per-habitat fallback
        # (pure setRenderer — cannot crash). Reset rebuilds the true placer look
        # from the QML separately via build_builtin_renderer.
        from qgis.core import (QgsCategorizedSymbolRenderer,
                               QgsGraduatedSymbolRenderer, QgsRuleBasedRenderer)
        existing_renderer = layer.renderer()
        if isinstance(existing_renderer, (QgsCategorizedSymbolRenderer,
                                          QgsGraduatedSymbolRenderer,
                                          QgsRuleBasedRenderer)):
            self._ensure_buildings_category(existing_renderer)
            return
        self._apply_fallback_style(layer, layer_key)

    def _apply_fallback_style(self, layer, layer_key):
        from qgis.core import (QgsCategorizedSymbolRenderer, QgsRendererCategory,
                                QgsLineSymbol, QgsMarkerSymbol)
        from qgis.PyQt.QtGui import QColor
        geom        = self._geom_type(layer_key)
        is_proposed = layer_key.startswith("proposed")
        categories  = []
        area_field = ("Proposed Habitat Type" if is_proposed
                      else "Baseline Habitat Type")
        for hab_type, subtypes in HABITAT_TYPES.items():
            for sub_name, meta in subtypes.items():
                color = QColor(meta["color"])
                if is_proposed:
                    color.setAlpha(160)
                if geom == "Polygon":
                    if sub_name == "Buildings":
                        symbol = self._build_buildings_symbol()
                    else:
                        symbol = QgsFillSymbol.createSimple({
                            "color":         color.name(QColor.NameFormat.HexArgb),
                            "outline_color": "#444444",
                            "outline_width": "0.26",
                        })
                elif geom == "LineString":
                    symbol = QgsLineSymbol.createSimple({
                        "color": color.name(), "width": "0.8",
                    })
                else:
                    symbol = QgsMarkerSymbol.createSimple({
                        "color":         color.name(),
                        "size":          "3",
                        "outline_color": "#333333",
                    })
                value = sub_name if geom == "Polygon" else meta["key"]
                label = sub_name if geom == "Polygon" else f"{meta['key']} – {sub_name}"
                categories.append(QgsRendererCategory(value, symbol, label))
        renderer_field = area_field if geom == "Polygon" else "habitat_key"
        layer.setRenderer(QgsCategorizedSymbolRenderer(renderer_field, categories))

    # ------------------------------------------------------------------ #
    #  BUILT-IN STYLE (placer styling) — crash-safe rebuild from the QML   #
    # ------------------------------------------------------------------ #
    # The bundled styles/*.qml ARE the habitat placer's styling. loadNamedStyle
    # on them is only unsafe during the project-open window (the QGIS 3.44.x
    # readSymbology timing crash); on a user action (placing a feature, Reset)
    # it is fine — which is why the placer styles new layers correctly.
    # build_builtin_renderer loads the QML onto a throwaway, project-less memory
    # layer and returns a clone of the resulting renderer, which the caller
    # setRenderer()s onto the real layer. This reproduces the placer's EXACT
    # look for every type (categorised area/hedge/water, rule-based-on-retention
    # trees, both baseline and proposed) and works even when the live layer's
    # styling has drifted, because it always rebuilds straight from the QML.

    @staticmethod
    def build_builtin_renderer(layer_key, geom_str):
        """Return a clone of the renderer the bundled QML produces for
        `layer_key` — see build_builtin_style. None on any failure."""
        renderer, _opacity = LayerManager.build_builtin_style(layer_key, geom_str)
        return renderer

    @staticmethod
    def build_builtin_style(layer_key, geom_str):
        """Return (renderer_clone, layer_opacity) as the bundled QML produces
        for `layer_key`, by loading it onto a detached in-memory layer of
        geometry `geom_str` ('Point' / 'LineString' / 'Polygon').
        (None, 1.0) on any failure.

        The opacity matters: the habitat QMLs style with LAYER-level opacity
        (0.6), not symbol alpha, so callers that setRenderer() the result onto
        a real layer must also setOpacity() or the fills come out solid.

        The throwaway layer is given the full HABITAT_FIELDS schema first — this
        is the SAME context the habitat placer styles a new layer in, and the
        only context where loadNamedStyle is safe on these QMLs: loading a
        renderer keyed on a field (e.g. 'Baseline Habitat Type',
        'Retention Category') onto a field-LESS layer crashes QGIS 3.44.x."""
        qml_name = LayerManager.QML_STYLES.get(layer_key)
        if not qml_name:
            return None, 1.0
        qml_path = os.path.join(os.path.dirname(__file__), "styles", qml_name)
        if not os.path.exists(qml_path):
            return None, 1.0
        try:
            tmp = QgsVectorLayer(f"{geom_str}?crs=EPSG:3857",
                                 "biome_tmp_style", "memory")
            if not tmp.isValid():
                return None, 1.0
            tmp.dataProvider().addAttributes(HABITAT_FIELDS)
            tmp.updateFields()
            _msg, ok = tmp.loadNamedStyle(qml_path)
            renderer = tmp.renderer()
            if ok and renderer is not None:
                LayerManager._ensure_buildings_category(renderer)
                out = renderer.clone()
                if layer_key == "proposed_trees":
                    LayerManager._greenify_created_trees(out)
                return out, tmp.opacity()
        except Exception:
            pass
        return None, 1.0

    # Created trees are drawn with the SAME size/type icon shapes as
    # retained/enhanced trees but filled green, so they stand out on the map and
    # get their own "Created …" entries in the legend (user request).
    _CREATED_TREE_GREEN = "#2E7D32"

    @staticmethod
    def _greenify_created_trees(renderer):
        """In-place renderer surgery on the proposed-trees RuleRenderer: restrict
        each existing size/type rule to non-created trees and append a green
        clone of it for created trees (label 'Created …'). No-op if not a
        RuleRenderer or already split. Pure in-memory work — no QML parse, so it
        cannot trigger the loadNamedStyle crash. Returns `renderer`."""
        from qgis.core import QgsRuleBasedRenderer
        from qgis.PyQt.QtGui import QColor
        if not isinstance(renderer, QgsRuleBasedRenderer):
            return renderer
        root = renderer.rootRule()
        children = list(root.children())
        # Idempotent: our clones are the only rules labelled "Created …".
        if any((c.label() or "").startswith("Created ") for c in children):
            return renderer
        green = QColor(LayerManager._CREATED_TREE_GREEN)
        for child in children:
            base = child.filterExpression() or "TRUE"
            # Existing rule → everything EXCEPT created (keeps its current colour).
            child.setFilterExpression(
                f'({base}) AND "Retention Category" <> \'Created\'')
            # Green clone → created trees only, same icon shape.
            clone = child.clone()
            clone.setFilterExpression(
                f'({base}) AND "Retention Category" = \'Created\'')
            lbl = child.label() or "Tree"
            if lbl.startswith("Proposed "):
                lbl = lbl[len("Proposed "):]
            clone.setLabel("Created " + lbl)
            sym = clone.symbol()
            if sym is not None:
                for i in range(sym.symbolLayerCount()):
                    sl = sym.symbolLayer(i)
                    try:
                        existing = sl.color()
                        if existing.alpha() > 0:   # recolour the visible fill, keep transparent halo layers
                            g = QColor(green)
                            g.setAlpha(existing.alpha())
                            sl.setColor(g)
                    except Exception:
                        pass
            root.appendChild(clone)
        return renderer

    # ------------------------------------------------------------------ #
    #  LABEL CONFIGURATION (configured but disabled; enabled by           #
    #  Measurements dialog when Calculate is clicked)                     #
    # ------------------------------------------------------------------ #

    def configure_labels(self, layer, layer_key):
        """Set up label rules on the layer WITHOUT enabling them.
        Called once per layer so the config is ready for Measurements to activate."""
        geom = self._geom_type(layer_key)
        if geom == "Point":
            return

        pal = QgsPalLayerSettings()

        if geom == "Polygon":
            pal.fieldName = (
                "if($area >= 10000,"
                " to_string(round($area / 10000, 2)) || ' ha',"
                " to_string(round($area, 1)) || ' m²')"
            )
            pal.isExpression = True
            try:
                pal.placement = QgsPalLayerSettings.Placement.Horizontal
            except AttributeError:
                pal.placement = QgsPalLayerSettings.Placement.Horizontal
        else:
            # LineString — length in metres
            pal.fieldName   = "to_string(round($length, 1)) || ' m'"
            pal.isExpression = True
            try:
                pal.placement = QgsPalLayerSettings.Placement.Curved
            except AttributeError:
                pal.placement = QgsPalLayerSettings.Placement.Curved

        fmt  = QgsTextFormat()
        font = QFont("Inter", 8)
        font.setBold(True)
        fmt.setFont(font)
        fmt.setSize(8)
        fmt.setColor(_QColor("black"))

        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.0)
        buf.setColor(_QColor("white"))
        fmt.setBuffer(buf)

        pal.setFormat(fmt)
        pal.enabled = True

        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(False)  # OFF — Measurements enables them


# ── Module-level tree helper ──────────────────────────────────────────────────

_KEEP_EXPANDED = {"Base Maps"}


def _collapse_tree_except(root):
    """Collapse all top-level groups and bare layer nodes in the layer panel
    except those whose name is in _KEEP_EXPANDED."""
    from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer
    for child in root.children():
        if isinstance(child, QgsLayerTreeGroup):
            if child.name() in _KEEP_EXPANDED:
                child.setExpanded(True)
            else:
                child.setExpanded(False)
                for sub in child.children():
                    sub.setExpanded(False)
        elif isinstance(child, QgsLayerTreeLayer):
            child.setExpanded(False)
