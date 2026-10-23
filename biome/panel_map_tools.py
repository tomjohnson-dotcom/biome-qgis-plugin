# panel_map_tools.py — map tools, redline-buffer dialog/helpers and the
# onboarding wizard, extracted verbatim from main_panel.py (2026-07-22 split).


from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QScrollArea, QDialog, QFormLayout, QSpinBox, QCheckBox, QDialogButtonBox, QMessageBox)
from qgis.PyQt.QtCore import (Qt, QSettings)
from qgis.PyQt.QtGui import (QCursor, QColor)
from qgis.core import Qgis
from qgis.core import (QgsProject, QgsWkbTypes, QgsFillSymbol, QgsLineSymbol,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat, QgsTextBufferSettings,
    QgsCoordinateTransform, QgsCoordinateReferenceSystem)
from qgis.gui import QgsMapToolEmitPoint
from . import theme
from .pond_mapper import (add_pond, load_pond_distance_lines, set_pond_lines_visible)


class _TreePlaceTool(QgsMapToolEmitPoint):
    """Click-to-place tree map tool for the Building & Tree Mapper.

    Each left-click adds a numbered tree point (T1, T2 …) in BNG.
    Right-click or Escape finishes the session.  These trees are for mapping
    only and never enter any BNG calculation.
    """

    def __init__(self, canvas, iface, main_panel):
        super().__init__(canvas)
        self.iface      = iface
        self.main_panel = main_panel
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.main_panel._bt_finish()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        from qgis.core import (
            QgsPointXY, QgsCoordinateTransform,
            QgsCoordinateReferenceSystem, QgsProject,
        )
        from .building_tree_mapper import add_tree

        canvas     = self.canvas()
        map_point  = self.toMapCoordinates(event.pos())
        canvas_crs = canvas.mapSettings().destinationCrs()
        bng        = QgsCoordinateReferenceSystem("EPSG:27700")
        if canvas_crs.authid() != "EPSG:27700":
            xform  = QgsCoordinateTransform(canvas_crs, bng, QgsProject.instance())
            pt_bng = xform.transform(map_point)
        else:
            pt_bng = QgsPointXY(map_point)

        tid = add_tree(pt_bng)
        # Register with global undo (highest fid among the matching tree_id —
        # ids can repeat after mid-stack undos, the newest rowid is ours).
        try:
            from .building_tree_mapper import get_or_create_tree_layer
            lyr = get_or_create_tree_layer()
            new_fids = [f.id() for f in lyr.getFeatures() if f["tree_id"] == tid]
            if new_fids:
                self.main_panel.push_undo(lyr, max(new_fids), None, None)
        except Exception:
            pass
        self.iface.mapCanvas().refresh()
        try:
            self.main_panel._bt_update_counts()
        except Exception:
            pass
        self.iface.messageBar().pushMessage(
            "Site Plan Visualiser", f"{tid} placed", level=Qgis.MessageLevel.Info, duration=1
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.main_panel._bt_finish()

    def deactivate(self):
        # Fires on EVERY switch away from this tool (QGIS toolbar, another
        # Biome tool, tab navigation) — reset the "Finish placing trees"
        # button + banner so they never outlive the tool. UI-only: switching
        # tools here (e.g. pan) would fight a cross-arm to a sibling tool
        # mid-setMapTool.
        try:
            self.main_panel._bt_tree_ui_reset()
        except Exception:
            pass
        super().deactivate()


class _PondPlaceTool(QgsMapToolEmitPoint):
    """Click-to-place pond map tool.

    Each left-click adds a numbered pond point in BNG, updates distance lines,
    and rebuilds the renderer so the legend stays in sync.
    Right-click or Escape exits the tool.
    """

    def __init__(self, canvas, iface, main_panel):
        super().__init__(canvas)
        self.iface      = iface
        self.main_panel = main_panel
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.main_panel._finish_pond_placing()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        from qgis.core import (
            QgsPointXY, QgsCoordinateTransform,
            QgsCoordinateReferenceSystem, QgsProject,
        )

        canvas    = self.canvas()
        map_point = self.toMapCoordinates(event.pos())
        canvas_crs = canvas.mapSettings().destinationCrs()
        bng        = QgsCoordinateReferenceSystem("EPSG:27700")

        if canvas_crs.authid() != "EPSG:27700":
            xform  = QgsCoordinateTransform(canvas_crs, bng, QgsProject.instance())
            pt_bng = xform.transform(map_point)
        else:
            pt_bng = QgsPointXY(map_point)

        redline_geom = self.main_panel._get_redline_geom()
        pond_id = add_pond(pt_bng, redline_geom)

        # Rebuild distance lines but keep hidden (lines off by default)
        load_pond_distance_lines(redline_geom)
        set_pond_lines_visible(False)

        # Enable lines button now that at least one pond exists, but keep off
        self.main_panel._pond_lines_btn.setEnabled(True)
        self.main_panel._pond_lines_btn.setChecked(False)
        try:
            self.main_panel._refresh_pond_controls()
        except Exception:
            pass

        self.iface.mapCanvas().refresh()
        self.iface.messageBar().pushMessage(
            "Standing Water Bodies", f"{pond_id} placed", level=Qgis.MessageLevel.Info, duration=2
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.main_panel._finish_pond_placing()

    def deactivate(self):
        # Fires on EVERY switch away from this tool (QGIS toolbar buttons,
        # another Biome tool, tab navigation) — reset the Place button and pop
        # the instruction bar so neither outlives the tool.
        try:
            self.main_panel._pond_ui_reset()
        except Exception:
            pass
        super().deactivate()


class _PondMoveTool(QgsMapToolEmitPoint):
    """Move one chosen SWB marker to the next left-clicked position."""

    def __init__(self, canvas, iface, main_panel, layer, fid):
        super().__init__(canvas)
        self.iface = iface
        self.main_panel = main_panel
        self.layer = layer
        self.fid = fid
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.iface.actionPan().trigger()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        from qgis.core import QgsGeometry, QgsPointXY
        from .pond_mapper import (POND_LINES_LAYER_NAME,
                                  renumber_ponds_by_distance)

        point = self.toMapCoordinates(event.pos())
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        if canvas_crs != self.layer.crs():
            point = QgsCoordinateTransform(
                canvas_crs, self.layer.crs(), QgsProject.instance()).transform(point)
        root = QgsProject.instance().layerTreeRoot()
        lines_visible = any(
            root.findLayer(line.id()) is not None
            and root.findLayer(line.id()).isVisible()
            for line in QgsProject.instance().mapLayersByName(POND_LINES_LAYER_NAME))

        if not self.layer.isEditable() and not self.layer.startEditing():
            self.iface.messageBar().pushWarning(
                "Standing Water Bodies", "The marker could not be moved.")
            self.iface.actionPan().trigger()
            return
        ok = self.layer.changeGeometry(
            self.fid, QgsGeometry.fromPointXY(QgsPointXY(point)))
        if not ok or not self.layer.commitChanges():
            self.layer.rollBack()
            self.iface.messageBar().pushWarning(
                "Standing Water Bodies", "The marker move could not be saved.")
            self.iface.actionPan().trigger()
            return
        redline = self.main_panel._get_redline_geom()
        renumber_ponds_by_distance(self.layer, redline)
        load_pond_distance_lines(redline)
        set_pond_lines_visible(lines_visible)
        self.layer.triggerRepaint()
        self.iface.mapCanvas().refresh()
        self.iface.messageBar().pushSuccess(
            "Standing Water Bodies", "Marker moved and distances updated.")
        self.iface.actionPan().trigger()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.iface.actionPan().trigger()

    def deactivate(self):
        try:
            self.main_panel._pond_move_ui_reset()
        except Exception:
            pass
        super().deactivate()


class _DeleteTool(QgsMapToolEmitPoint):
    """Click-to-delete map tool for all BNG and ecological layers.

    Searches every managed layer for the nearest feature within a pixel
    tolerance, deletes it, and triggers a repaint.
    """

    # All layer keys managed by the plugin (habitat layers via LayerManager)
    _LAYER_KEYS = [
        "baseline_area", "proposed_area",
        "baseline_water", "proposed_water",
        "baseline_hedgerow", "proposed_hedgerow",
        "baseline_trees", "proposed_trees",
        "redline",
    ]
    # Names of ecological feature layers (not managed via layer_key)
    _ECO_LAYER_NAMES = {"Ecological Features", "Ecological Linear Features", "Ecological Area Features", "Ecological Buffer Zones"}

    def __init__(self, canvas, iface, layer_manager, main_panel):
        super().__init__(canvas)
        self.iface         = iface
        self.layer_manager = layer_manager
        self.main_panel    = main_panel
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        from qgis.core import QgsGeometry, QgsPointXY, QgsFeatureRequest
        point  = self.toMapCoordinates(event.pos())
        canvas = self.canvas()

        # Build a search rectangle: 12 pixels in map units
        px_size = canvas.mapUnitsPerPixel() * 12
        from qgis.core import QgsRectangle
        search_rect = QgsRectangle(
            point.x() - px_size, point.y() - px_size,
            point.x() + px_size, point.y() + px_size,
        )

        best_layer = None
        best_fid   = None
        best_dist  = float("inf")
        click_geom = QgsGeometry.fromPointXY(QgsPointXY(point))

        # Search BNG habitat layers
        for key in self._LAYER_KEYS:
            layer = self.layer_manager.get_layer(key)   # never creates
            if layer is None or not layer.isValid() or layer.featureCount() == 0:
                continue
            req = QgsFeatureRequest().setFilterRect(search_rect)
            for feat in layer.getFeatures(req):
                geom = feat.geometry()
                if geom is None or geom.isNull():
                    continue
                dist = click_geom.distance(geom)
                if dist < best_dist:
                    best_dist  = dist
                    best_layer = layer
                    best_fid   = feat.id()

        # Search ecological feature layers
        from qgis.core import QgsProject, QgsVectorLayer
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            if lyr.name() not in self._ECO_LAYER_NAMES:
                continue
            if lyr.featureCount() == 0:
                continue
            req = QgsFeatureRequest().setFilterRect(search_rect)
            for feat in lyr.getFeatures(req):
                geom = feat.geometry()
                if geom is None or geom.isNull():
                    continue
                dist = click_geom.distance(geom)
                if dist < best_dist:
                    best_dist  = dist
                    best_layer = lyr
                    best_fid   = feat.id()

        if best_layer is None or best_fid is None:
            self.iface.messageBar().pushMessage(
                "Biome", "No feature found at click location.", level=Qgis.MessageLevel.Warning, duration=3
            )
            return

        # Delete the feature.
        # We use the data provider directly so no "active layer" selection is needed.
        # This bypasses QGIS's requirement that the layer be the active/selected layer.

        # If a linked tree/hedge twin, clear the survivor's pair_id first so it
        # reclassifies correctly after this delete.
        try:
            self.main_panel._orphan_pair_twin(best_layer, best_fid)
        except Exception:
            pass

        # Snapshot into bin BEFORE deletion so geometry/attrs are still readable
        self.main_panel._bin_feature(best_layer, best_fid)

        ok = False
        try:
            # Preferred: provider-level delete (works regardless of active layer)
            provider = best_layer.dataProvider()
            if provider.capabilities() & provider.DeleteFeatures:
                ok = provider.deleteFeatures([best_fid])
                best_layer.updateExtents()
            else:
                # Fallback: set active + edit mode
                self.iface.setActiveLayer(best_layer)
                best_layer.startEditing()
                ok = best_layer.deleteFeature(best_fid)
                best_layer.commitChanges()
        except Exception as exc:
            # Delete failed — remove the bin entry we just added
            if self.main_panel._deleted_bin:
                self.main_panel._deleted_bin.pop()
            self.iface.messageBar().pushMessage(
                "Biome", f"Delete error: {exc}", level=Qgis.MessageLevel.Critical, duration=5
            )
            return

        best_layer.triggerRepaint()
        canvas.refresh()

        if ok:
            self.iface.messageBar().pushMessage(
                "Biome — Delete Mode",
                f"Deleted from '{best_layer.name()}'.  Click another or press Escape to exit.",
                level=Qgis.MessageLevel.Warning, duration=0
            )
            # Refresh counts and summary widgets immediately
            if hasattr(self.main_panel, "_refresh_phase_screens"):
                self.main_panel._refresh_phase_screens()
            if hasattr(self.main_panel, "_refresh_balance_widget"):
                from qgis.PyQt.QtCore import QTimer
                QTimer.singleShot(0, self.main_panel._refresh_balance_widget)
            if hasattr(self.main_panel, "_refresh_bng_widget"):
                from qgis.PyQt.QtCore import QTimer
                QTimer.singleShot(0, self.main_panel._refresh_bng_widget)
        else:
            self.iface.messageBar().pushMessage(
                "Biome", "Failed to delete feature.", level=Qgis.MessageLevel.Critical, duration=4
            )

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._exit()
        else:
            super().keyReleaseEvent(event)

    def deactivate(self):
        self.iface.messageBar().clearWidgets()
        super().deactivate()

    def _exit(self):
        self.canvas().unsetMapTool(self)
        self.iface.actionPan().trigger()


# ─────────────────────────────────────────────────────────────────────────────
#  Redline Buffers — embedded dialog and runner
#  (Adapted from redline_buffers_plugin.py; runs directly from BNG Mapper
#   so it always uses the BNG plugin's own "Redline Boundary" layer and
#   never crashes on startup when no redline has been drawn yet.)
# ─────────────────────────────────────────────────────────────────────────────

class _RedLineBuffersDialog(QDialog):
    """Settings dialog for redline buffer creation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Local searches & buffers")
        self.setMinimumWidth(420)
        self._settings = QSettings()

        # One-time correction from the short-lived linked-buffer build, which
        # could leave the general control holding the TPO 200 m value. The
        # ordinary local-buffer default is the standing-water 250/500 m pair.
        if not self._to_bool(self._settings.value(
                "biome/local_buffers/standing_default_v1", False)):
            self._settings.setValue("RedLineBuffers/dist1", 250)
            self._settings.setValue("RedLineBuffers/dist2", 500)
            self._settings.setValue("RedLineBuffers/outerOnly", False)
            self._settings.setValue(
                "biome/local_buffers/standing_default_v1", True)

        layout = QVBoxLayout()
        form = QFormLayout()

        self.dist1 = QSpinBox()
        self.dist1.setRange(0, 500000)
        saved1 = int(self._settings.value("RedLineBuffers/dist1", 250))
        self.dist1.setValue(saved1 if saved1 != 2000 else 250)
        self.dist1.setSuffix(" m")

        self.dist2 = QSpinBox()
        self.dist2.setRange(0, 500000)
        saved2 = int(self._settings.value("RedLineBuffers/dist2", 500))
        self.dist2.setValue(saved2 if saved2 != 2000 else 500)
        self.dist2.setSuffix(" m")

        self.outer_only = QCheckBox("Outer buffer only (skip inner)")
        self.outer_only.setChecked(self._to_bool(
            self._settings.value("RedLineBuffers/outerOnly", False)))

        self.dashed_first = QCheckBox("Make inner buffer dashed")
        self.dashed_first.setChecked(self._to_bool(
            self._settings.value("RedLineBuffers/dashedFirst", True)))

        self.make_labels = QCheckBox("Create curved boundary label layers")
        self.make_labels.setChecked(self._to_bool(
            self._settings.value("RedLineBuffers/makeLabels", True)))

        self.delete_old = QCheckBox("Delete previously generated buffer layers")
        self.delete_old.setChecked(self._to_bool(
            self._settings.value("RedLineBuffers/deleteOld", True)))

        form.addRow("Inner buffer distance:", self.dist1)
        form.addRow("Outer buffer distance:", self.dist2)
        layout.addLayout(form)
        layout.addWidget(self.outer_only)
        layout.addWidget(self.dashed_first)
        layout.addWidget(self.make_labels)
        layout.addWidget(self.delete_old)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self.outer_only.toggled.connect(self._sync_outer_only)
        self._sync_outer_only()

    def _sync_outer_only(self):
        """Grey out the inner-buffer controls when only the outer ring is wanted."""
        only = self.outer_only.isChecked()
        self.dist1.setEnabled(not only)
        self.dashed_first.setEnabled(not only)

    @staticmethod
    def _to_bool(v):
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("1", "true", "yes", "y", "t")

    def get_values(self):
        return {
            "dist1":       int(self.dist1.value()),
            "dist2":       int(self.dist2.value()),
            "outerOnly":   bool(self.outer_only.isChecked()),
            "dashedFirst": bool(self.dashed_first.isChecked()),
            "makeLabels":  bool(self.make_labels.isChecked()),
            "deleteOld":   bool(self.delete_old.isChecked()),
        }

    def save_values(self, vals):
        s = self._settings
        s.setValue("RedLineBuffers/dist1",       vals["dist1"])
        s.setValue("RedLineBuffers/dist2",       vals["dist2"])
        s.setValue("RedLineBuffers/outerOnly",   vals["outerOnly"])
        s.setValue("RedLineBuffers/dashedFirst", vals["dashedFirst"])
        s.setValue("RedLineBuffers/makeLabels",  vals["makeLabels"])
        s.setValue("RedLineBuffers/deleteOld",   vals["deleteOld"])


def _run_redline_buffers(iface, redline_layer, vals):
    """Execute buffer creation using the supplied redline layer and settings.

    Builds the rings directly with in-memory QgsGeometry.buffer() (mirroring
    designated_sites.add_2km_buffer) instead of chaining ~10 processing.run()
    calls. The geometry work is identical, but this skips the Processing
    framework cold-start and per-algorithm overhead that made buffer creation
    slow to load.
    """
    from qgis.core import QgsGeometry, QgsVectorLayer, QgsFeature

    dist1        = vals["dist1"]
    dist2        = vals["dist2"]
    outer_only   = vals.get("outerOnly", False)
    dashed_first = vals["dashedFirst"]
    make_labels  = vals["makeLabels"]
    delete_old   = vals["deleteOld"]
    base_name    = redline_layer.name()   # "Redline Boundary"

    if outer_only:
        # The inner spinbox is greyed out but keeps its value — it must not
        # influence the outer distance (swapping here once turned 200 m
        # into the stale inner 250 m).
        rings = [(dist2, False)]
    else:
        # Keep distances ordered
        if dist2 < dist1:
            dist1, dist2 = dist2, dist1
        rings = [(dist1, dashed_first), (dist2, False)]

    # Optional extra rings remain available to deliberate callers. Automatic
    # TPO and standing-water presets are mutually exclusive.
    rings.extend(vals.get("extraDistances", []))
    by_distance = {}
    for distance, dashed in rings:
        by_distance[int(distance)] = bool(dashed)
    rings = sorted(by_distance.items())

    try:
        project = QgsProject.instance()

        # Remove previously generated layers if requested. Match ANY distance,
        # not just the current ones — earlier runs may have used different
        # values and their rings would otherwise linger.
        if delete_old:
            import re as _re
            pat = _re.compile(
                _re.escape(base_name)
                + r" \d+m Buffer( \(Boundary\)| \(Label\))?$")
            ids = [lid for lid, lyr in project.mapLayers().items()
                   if pat.fullmatch(lyr.name())]
            if ids:
                project.removeMapLayers(ids)

        # Merge all redline features into one geometry and reproject to BNG
        # (EPSG:27700) so buffer distances are true metres regardless of the
        # redline/project CRS — the redline layer is often EPSG:3857, where
        # buffering would otherwise produce a wildly wrong "whole globe" ring.
        feats = [f for f in redline_layer.getFeatures() if f.hasGeometry()]
        if not feats:
            raise ValueError("No Red Line Boundary geometry to buffer.")
        redline_geom = QgsGeometry(feats[0].geometry())
        for f in feats[1:]:
            redline_geom = redline_geom.combine(f.geometry())

        bng = QgsCoordinateReferenceSystem("EPSG:27700")
        if redline_layer.crs() != bng:
            xform = QgsCoordinateTransform(redline_layer.crs(), bng, project)
            redline_geom.transform(xform)

        for distance, dashed in rings:
            # Direct in-memory GEOS buffer — round caps/joins, 20 segments per
            # quarter circle (matches the old native:buffer parameters).
            buf_geom = redline_geom.buffer(distance, 20)
            buf_name = f"{base_name} {distance}m Buffer"

            # Ring layer is created in EPSG:27700; QGIS reprojects it on the fly
            # for display, exactly like the 2 km search buffer.
            buf = QgsVectorLayer("Polygon?crs=EPSG:27700", buf_name, "memory")
            bf  = QgsFeature(buf.fields())
            bf.setGeometry(buf_geom)
            buf.dataProvider().addFeatures([bf])
            buf.updateExtents()
            _style_buffer_polygon(buf, dashed=dashed)
            buf.setCustomProperty("skipMemoryLayersCheck", 1)
            project.addMapLayer(buf, False)
            _place_in_buffer_group(project, buf, "Redline Boundary Buffers")

            if make_labels:
                # Polygon outline as a (multi)line for the boundary + label.
                line_geom = buf_geom.convertToType(QgsWkbTypes.GeometryType.LineGeometry, True)

                boundary = QgsVectorLayer(
                    "MultiLineString?crs=EPSG:27700",
                    f"{buf_name} (Boundary)", "memory")
                lf = QgsFeature(boundary.fields())
                lf.setGeometry(line_geom)
                boundary.dataProvider().addFeatures([lf])
                boundary.updateExtents()
                _style_boundary_line(boundary, dashed=dashed)
                boundary.setCustomProperty("skipMemoryLayersCheck", 1)
                project.addMapLayer(boundary, False)
                _place_in_buffer_group(project, boundary, "Redline Boundary Buffers")

                labeled = _make_curved_label_layer(line_geom, buf_name)
                labeled.setName(f"{buf_name} (Label)")
                labeled.setCustomProperty("skipMemoryLayersCheck", 1)
                project.addMapLayer(labeled, False)
                _place_in_buffer_group(project, labeled, "Redline Boundary Buffers")

        if not vals.get("quiet", False):
            iface.messageBar().pushSuccess(
                "Biome — Redline Buffers", "Buffers created successfully."
            )

    except Exception as exc:
        QMessageBox.critical(
            iface.mainWindow(), "Redline Buffers — Error", str(exc)
        )


def _place_in_buffer_group(project, layer, group_name: str):
    """Place a layer into a named group in the layer tree, creating it if needed.
    The group is inserted just below the Redline Boundary layer (or at the top).
    All groups except Ecological Features and Base Maps are collapsed."""
    root = project.layerTreeRoot()
    group = root.findGroup(group_name)
    if group is None:
        rl_nodes = [n for n in root.children()
                    if hasattr(n, "layer") and n.layer()
                    and n.layer().name() == "Redline Boundary"]
        idx = root.children().index(rl_nodes[0]) + 1 if rl_nodes else 0
        group = root.insertGroup(idx, group_name)
    existing_ids = {n.layerId() for n in group.findLayers()}
    if layer.id() not in existing_ids:
        group.addLayer(layer)
    # Collapse all groups except Ecological Features and Base Maps
    from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer
    _KEEP = {"Base Maps"}
    for child in root.children():
        if isinstance(child, QgsLayerTreeGroup):
            child.setExpanded(child.name() in _KEEP)
            if child.name() not in _KEEP:
                for sub in child.children():
                    sub.setExpanded(False)
        elif isinstance(child, QgsLayerTreeLayer):
            child.setExpanded(False)




def _style_buffer_polygon(layer, dashed=False):
    props = {"color": "0,0,0,0", "outline_color": "0,0,0", "outline_width": "0.8"}
    if dashed:
        props["outline_style"] = "dash"
    layer.renderer().setSymbol(QgsFillSymbol.createSimple(props))
    layer.triggerRepaint()


def _style_boundary_line(layer, dashed=False):
    props = {"line_color": "0,0,0", "line_width": "0.8"}
    if dashed:
        props["line_style"] = "dash"
    layer.renderer().setSymbol(QgsLineSymbol.createSimple(props))
    layer.triggerRepaint()


def _make_curved_label_layer(line_geom, label_text):
    """Build an in-memory MultiLineString layer (EPSG:27700) carrying a single
    'label' attribute, with curved labelling enabled. Replaces a
    native:fieldcalculator processing call with a direct memory layer."""
    from qgis.core import QgsVectorLayer, QgsFeature, QgsField
    from qgis.PyQt.QtCore import QVariant

    labeled = QgsVectorLayer("MultiLineString?crs=EPSG:27700", "label", "memory")
    dp = labeled.dataProvider()
    dp.addAttributes([QgsField("label", QVariant.String)])
    labeled.updateFields()
    lf = QgsFeature(labeled.fields())
    lf.setGeometry(line_geom)
    lf.setAttribute("label", label_text)
    dp.addFeatures([lf])
    labeled.updateExtents()

    pal = QgsPalLayerSettings()
    pal.enabled = True
    pal.fieldName = "label"
    pal.placement = QgsPalLayerSettings.Placement.Curved

    fmt = QgsTextFormat()
    fmt.setColor(QColor(0, 0, 0))
    fmt.setSize(10)

    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buf)

    pal.setFormat(fmt)
    labeled.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    labeled.setLabelsEnabled(True)
    labeled.triggerRepaint()
    return labeled


# =============================================================================
#  Feature Info Tool — click a DS / PHI / EPSL / GCN feature to see its info
# =============================================================================

class _FeatureInfoTool(QgsMapToolEmitPoint):
    """Click any designated site, habitat, EPSL, GCN or pond feature on the map
    and show a popup with the same fields as the report tables.

    Searches all relevant memory/WMS layers by spatial proximity.
    Right-click or Escape exits back to the pan tool.
    """

    # Layer name prefixes / exact names we query
    _DS_PREFIX    = "DS_"
    _PHI_PREFIX   = "PHI - "
    _EPSL_NAME    = "EPSL Locations"
    _GCN_PREFIX   = "GCN Risk Zones"
    _POND_NAME    = "Water Body"
    _TPO_NAME     = "Tree Preservation Orders"
    _TPO_ZONE_NAME = "TPO Zones"
    _CONSERVATION_NAME = "Conservation Areas"

    @staticmethod
    def _ds_labels():
        """Return the set of human-readable DS layer names (e.g. {'SSSI', 'SAC', ...})."""
        try:
            from .designated_sites import all_ds_labels
            return all_ds_labels()       # England AND Scotland labels
        except Exception:
            return set()

    def __init__(self, canvas, iface, main_panel):
        super().__init__(canvas)
        self.iface      = iface
        self.main_panel = main_panel
        self.setCursor(QCursor(Qt.CursorShape.WhatsThisCursor))

    @staticmethod
    def _dbg(msg=""):
        """TEMP debug: append a line to biome_debug.log."""
        try:
            import os as _os
            with open(_os.path.join(_os.path.dirname(__file__), "biome_debug.log"),
                      "a", encoding="utf-8") as _f:
                _f.write("[infotool] " + str(msg) + "\n")
        except Exception:
            pass

    def canvasReleaseEvent(self, event):
        try:
            self._dbg("canvasReleaseEvent fired; button=%s" % event.button())
        except Exception:
            pass
        if event.button() == Qt.MouseButton.RightButton:
            self._exit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        try:
            self._do_click(event)
        except Exception:
            import traceback
            self._dbg("EXCEPTION in _do_click:\n" + traceback.format_exc())
            try:
                self.iface.messageBar().pushMessage(
                    "Biome", "Feature info error — see biome_debug.log.",
                    level=Qgis.MessageLevel.Critical, duration=4
                )
            except Exception:
                pass

    @staticmethod
    def _editable_layer_names():
        """Names of the layers whose features the user owns and can edit through
        the Manage-features property editor — so the info card offers an Edit
        button for these and stays read-only for everything else (desk study
        data). Pulled from the canonical constants so it tracks any renames."""
        names = set()
        try:
            from .layer_manager import LAYER_NAMES as _LM
            # Habitat layers are editable; redline / blue-line have no
            # per-feature properties, so they stay info-only.
            names |= {v for k, v in _LM.items()
                      if k not in ("redline", "site_boundary")}
        except Exception:
            pass
        try:
            from .gridref_dialog import (
                LAYER_NAME, LINE_LAYER_NAME, POLY_LAYER_NAME, BUFFER_LAYER_NAME)
            names |= {LAYER_NAME, LINE_LAYER_NAME, POLY_LAYER_NAME, BUFFER_LAYER_NAME}
        except Exception:
            pass
        return names

    def _do_click(self, event):
        self.identify_at_point(self.toMapCoordinates(event.pos()))

    def best_feature_at(self, point):
        """The single best feature across EVERY visible vector layer at a canvas
        map point. Score = (distance, editable-rank, area): nearest wins; on a
        tie the user's own editable feature is preferred, then the smaller (more
        specific) one. Distances/areas are measured in the canvas CRS so features
        from differently-projected layers compare fairly. Returns
        (score, layer, feature) or None. Shared by the info map-tool and the
        plan-mode right-click menu."""
        from qgis.core import (
            QgsGeometry, QgsPointXY, QgsFeatureRequest,
            QgsProject, QgsVectorLayer, QgsRectangle,
            QgsCoordinateTransform,
        )
        canvas     = self.canvas()
        px         = canvas.mapUnitsPerPixel() * 16
        search     = QgsRectangle(point.x()-px, point.y()-px,
                                  point.x()+px, point.y()+px)
        click_g    = QgsGeometry.fromPointXY(QgsPointXY(point))
        canvas_crs = canvas.mapSettings().destinationCrs()
        project    = QgsProject.instance()
        root       = project.layerTreeRoot()
        editable   = self._editable_layer_names()

        best = None          # (score_tuple, layer, feature)
        for lyr in project.mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            node = root.findLayer(lyr.id())
            if node is None or not node.isVisible():
                continue
            # A single layer with a dud CRS / broken provider must not abort the
            # whole click — skip it and keep searching the rest.
            try:
                lyr_crs = lyr.crs()
                if lyr_crs != canvas_crs:
                    s = QgsCoordinateTransform(
                        canvas_crs, lyr_crs, project).transformBoundingBox(search)
                else:
                    s = search
                edit_rank = 0 if lyr.name() in editable else 1
                for feat in lyr.getFeatures(QgsFeatureRequest().setFilterRect(s)):
                    g = feat.geometry()
                    if g is None or g.isNull():
                        continue
                    gc = QgsGeometry(g)
                    if lyr_crs != canvas_crs:
                        gc.transform(QgsCoordinateTransform(
                            lyr_crs, canvas_crs, project))
                    d = click_g.distance(gc)
                    try:
                        a = gc.area()
                    except Exception:
                        a = 0.0
                    score = (round(d, 6), edit_rank, a)
                    if best is None or score < best[0]:
                        best = (score, lyr, feat)
            except Exception:
                import traceback
                self._dbg("  layer %r skipped: %s"
                          % (lyr.name(),
                             traceback.format_exc().strip().splitlines()[-1]))
                continue
        return best

    def identify_at_point(self, point, quiet=False):
        """Find the best feature at a canvas point and show its info popup. When
        `quiet` and nothing is under the point, stay silent (used by the
        right-click menu, which only offers the action when a feature exists)."""
        from qgis.core import QgsCoordinateReferenceSystem
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        bng        = QgsCoordinateReferenceSystem("EPSG:27700")
        best = self.best_feature_at(point)
        if best is None:
            if not quiet:
                self.iface.messageBar().pushMessage(
                    "Biome",
                    "No feature here — click directly on a feature (a habitat, "
                    "designated site, EPSL point, etc.).",
                    level=Qgis.MessageLevel.Info, duration=3
                )
            return
        _, lyr, feat = best
        self._show_info_popup(lyr, feat, canvas_crs, bng)

    def _show_info_popup(self, lyr, feat, canvas_crs, bng):
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QFormLayout, QFrame, QPushButton, QScrollArea, QWidget,
        )
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtGui import QFont
        from qgis.core import (
            QgsCoordinateTransform, QgsProject, QgsGeometry,
        )

        n = lyr.name()
        fields = {f.name(): feat[f.name()] for f in lyr.fields()}
        editable = n in self._editable_layer_names()

        # Feature geometry reprojected to BNG once — reused for the redline
        # distance, the area/length readout and the centroid grid ref.
        feat_g_bng = QgsGeometry(feat.geometry())
        try:
            if lyr.crs().authid() != "EPSG:27700":
                feat_g_bng.transform(QgsCoordinateTransform(
                    lyr.crs(), bng, QgsProject.instance()))
        except Exception:
            pass

        # Distance from redline (reuse main_panel helper)
        dist_str = "—"
        try:
            rl_geom = self.main_panel._get_redline_geom()  # BNG
            if rl_geom and not rl_geom.isEmpty() and not feat_g_bng.isEmpty():
                dist_m = rl_geom.distance(feat_g_bng)
                dist_str = (f"{dist_m:.0f} m" if dist_m < 1000
                            else f"{dist_m / 1000:.2f} km")
        except Exception:
            pass

        # Grid ref of feature centroid in BNG
        gr_str = "—"
        try:
            c = feat_g_bng.centroid().asPoint()
            from .gridref_utils import osgb_en_to_gridref
            gr_str = osgb_en_to_gridref(int(c.x()), int(c.y()), 6)
        except Exception:
            pass

        # Build display rows depending on layer type
        rows = []
        if editable:
            rows = self._editable_rows(n, fields, gr_str, dist_str)
        elif n in self._ds_labels():
            rows = [
                ("Layer",         n),
                ("Name",          str(fields.get("name") or fields.get("sac_name") or
                                      fields.get("spa_name") or "—")),
                ("Centroid (GR)", gr_str),
                ("Dist. to redline", dist_str),
            ]
        elif n.startswith("PHI - "):
            area = fields.get("AreaHa") or fields.get("area_ha") or 0
            try:
                area = f"{float(area):.2f} ha"
            except Exception:
                area = "—"
            rows = [
                ("Habitat Type",   str(fields.get("MainHabs") or "—")),
                ("BAP Code",       str(fields.get("HabCodes")  or "—")),
                ("Area",           area),
                ("Description",    str(fields.get("FeatDesc")  or "—") or "—"),
                ("Centroid (GR)",  gr_str),
                ("Dist. to redline", dist_str),
            ]
        elif n == "EPSL Locations":
            rows = [
                ("Licence Ref",    str(fields.get("case_ref")   or "—")),
                ("Species Group",  str(fields.get("species_gr") or "—")),
                ("Species",        str(fields.get("species")    or "—")),
                ("County",         str(fields.get("county")     or "—")),
                ("Start Date",     str(fields.get("start_date") or "—")),
                ("End Date",       str(fields.get("end_date")   or "—")),
                ("Grid Ref",       str(fields.get("grid_ref")   or gr_str)),
                ("Dist. to redline", dist_str),
            ]
        elif n == self._TPO_NAME:
            rows = [
                ("Tree Ref",        str(fields.get("reference")  or "—")),
                ("TPO Order",       str(fields.get("tpo_ref")    or "—")),
                ("Name",            str(fields.get("name")       or "—")),
                ("Species",         str(fields.get("species")    or "—")),
                ("Address",         str(fields.get("address")    or "—")),
                ("Entry Date",      str(fields.get("entry_date") or "—")),
                ("Start Date",      str(fields.get("start_date") or "—")),
                ("Grid Ref",        str(fields.get("grid_ref")   or gr_str)),
                ("Dist. to redline", dist_str),
            ]
        elif n == self._TPO_ZONE_NAME:
            rows = [
                ("Type",             "TPO Zone (woodland / area / group)"),
                ("Zone Ref",         str(fields.get("reference")  or "—")),
                ("TPO Order",        str(fields.get("tpo_ref")    or "—")),
                ("Name",             str(fields.get("name")       or "—")),
                ("Notes",            str(fields.get("notes")      or "—")),
                ("Area (ha)",        str(fields.get("area_ha")    or "—")),
                ("Entry Date",       str(fields.get("entry_date") or "—")),
                ("Centroid (GR)",    str(fields.get("grid_ref")   or gr_str)),
                ("Dist. to redline", dist_str),
            ]
        elif n == self._CONSERVATION_NAME:
            rows = [
                ("Conservation Area", str(fields.get("name")       or "—")),
                ("Reference",         str(fields.get("reference")  or "—")),
                ("Area (ha)",         str(fields.get("area_ha")    or "—")),
                ("Entry Date",        str(fields.get("entry_date") or "—")),
                ("Centroid (GR)",     str(fields.get("grid_ref")   or gr_str)),
                ("Dist. to redline",  dist_str),
                ("Protection",        "Trees protected — 6 weeks' notice to LPA before works"),
            ]
        elif n.startswith("GCN Risk Zones"):
            rows = [
                ("Layer",          n),
                ("Risk Zone",      str(fields.get("Risk_Zone") or "—")),
                ("Area (ha)",      str(fields.get("AreaHa")    or "—")),
                ("Centroid (GR)",  gr_str),
                ("Dist. to redline", dist_str),
            ]
        elif n == self._POND_NAME:
            rows = [
                ("ID",               str(fields.get("pond_id")        or "—")),
                ("Grid Ref",         str(fields.get("grid_ref")        or gr_str)),
                ("Dist. to redline", str(fields.get("distance_label")  or dist_str)),
            ]
        else:
            rows = [(k, str(v)) for k, v in fields.items()]

        # Area (polygons) / length (lines) — slot it in just above the distance
        # row so every card reads identity → size → location consistently.
        al = self._fmt_area_length(feat_g_bng)
        if al is not None:
            insert_at = len(rows)
            for i, (k, _v) in enumerate(rows):
                if str(k).startswith("Dist"):
                    insert_at = i
                    break
            rows.insert(insert_at, al)

        # A friendly card title: the feature's own type/label for placed
        # features, otherwise the layer name.
        title = n
        if editable:
            for k in ("ftype", "label", "Baseline Habitat Type",
                      "Proposed Habitat Type", "Baseline Hedge Type",
                      "Proposed Hedge Type", "Baseline River Type",
                      "Proposed River Type", "sub_habitat_type", "habitat_type"):
                v = fields.get(k)
                if v is not None and str(v).strip() and str(v) != "NULL":
                    title = str(v).strip()
                    break

        # Dialog
        dlg = QDialog(self.iface.mainWindow())
        dlg.setWindowTitle(f"Feature Info — {title}")
        dlg.setMinimumWidth(360)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        # Header
        hdr = QLabel(title)
        hdr.setStyleSheet(
            "font-size:13px; font-weight:500; color:white;"
            "background:#175E3B; padding:8px 10px; border-radius:4px;"
        )
        layout.addWidget(hdr)

        # Fields
        fl = QFormLayout()
        fl.setSpacing(6)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for label, value in rows:
            lbl = QLabel(label + ":")
            lbl.setStyleSheet("font-size:11px; color:#54635A; font-weight:500;")
            val = QLabel(str(value) if value else "—")
            val.setStyleSheet("font-size:11px; color:#1A2420;")
            val.setWordWrap(True)
            fl.addRow(lbl, val)
        layout.addLayout(fl)

        # Footer — Edit (editable/placed features only) + Close
        hl = QHBoxLayout()
        hl.addStretch()

        _want_edit = {"go": False}
        if editable:
            edit_btn = QPushButton("Edit")
            edit_btn.setStyleSheet("""
                QPushButton {
                    background:#E9F4EC; color:#175E3B; border:1px solid #175E3B;
                    border-radius:4px; font-size:11px; padding:5px 18px;
                }
                QPushButton:hover { background:#D8ECDE; }
            """)
            def _go_edit():
                _want_edit["go"] = True
                dlg.accept()
            edit_btn.clicked.connect(_go_edit)
            hl.addWidget(edit_btn)

        # Buffer this feature — designated sites & priority habitats only. Uses
        # the already-reprojected BNG geometry so the buffer is true metres.
        _is_ds  = n in self._ds_labels()
        _is_phi = n.startswith("Priority Habitat - ")
        if (_is_ds or _is_phi) and not feat_g_bng.isEmpty():
            buf_btn = QPushButton("Buffer this feature…")
            buf_btn.setStyleSheet("""
                QPushButton {
                    background:#EEF6F0; color:#175E3B; border:1px solid #175E3B;
                    border-radius:4px; font-size:11px; padding:5px 14px;
                }
                QPushButton:hover { background:#DCEDE1; }
            """)
            _buf_geom = QgsGeometry(feat_g_bng)
            _buf_name = self.main_panel._feature_display_name(lyr, feat)
            buf_btn.clicked.connect(
                lambda _c=False, nm=n, g=_buf_geom, b=buf_btn, dn=_buf_name:
                    self.main_panel._feature_buffer_menu(nm, g, b, dn))
            hl.addWidget(buf_btn)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background:#175E3B; color:white; border:none;
                border-radius:4px; font-size:11px; padding:5px 18px;
            }
            QPushButton:hover { background:#1F7A4D; }
        """)
        close_btn.clicked.connect(dlg.accept)
        hl.addWidget(close_btn)
        layout.addLayout(hl)

        self._dbg("popup for %r (editable=%s, %d rows)" % (n, editable, len(rows)))
        dlg.exec()

        # Open the full property editor only after the card has closed, so the
        # two modal dialogs never stack.
        if _want_edit["go"]:
            try:
                self.main_panel.open_feature_editor(lyr, feat.id())
            except Exception:
                import traceback
                self._dbg("open_feature_editor failed:\n" + traceback.format_exc())

    @staticmethod
    def _editable_rows(n, fields, gr_str, dist_str):
        """Concise 'one row of the report' card for one of the user's own
        placed features — the Edit button reveals the full property editor."""
        def g(*keys):
            for k in keys:
                v = fields.get(k)
                if v is not None and str(v).strip() and str(v) != "NULL":
                    return str(v).strip()
            return "—"

        if n.startswith("Ecological"):
            gridref = g("gridref")
            return [
                ("Layer",            n),
                ("Feature type",     g("ftype")),
                ("Label",            g("label")),
                ("Grid ref",         gridref if gridref != "—" else gr_str),
                ("Dist. to redline", dist_str),
            ]

        # Habitat layers (Baseline/Proposed area / watercourse / hedge / trees)
        return [
            ("Layer",            n),
            ("Habitat",          g("Baseline Habitat Type", "Proposed Habitat Type",
                                   "Baseline Hedge Type", "Proposed Hedge Type",
                                   "Baseline River Type", "Proposed River Type",
                                   "Baseline Tree Size", "Proposed Tree Size",
                                   "sub_habitat_type", "habitat_type")),
            ("Condition",        g("Baseline Condition", "Proposed Condition", "condition")),
            ("Label",            g("label")),
            ("Grid ref",         gr_str),
            ("Dist. to redline", dist_str),
        ]

    @staticmethod
    def _fmt_area_length(geom_bng):
        """Return an ('Area'|'Length', value) row for a BNG geometry, or None
        for points. BNG units are metres, so area is m²/ha and length is m/km."""
        from qgis.core import QgsWkbTypes
        try:
            gt = QgsWkbTypes.geometryType(geom_bng.wkbType())
        except Exception:
            return None
        try:
            if gt == QgsWkbTypes.GeometryType.PolygonGeometry:
                a = geom_bng.area()
                ha = a / 10000.0
                if a >= 10000:
                    return ("Area", "%s ha  (%s m²)"
                            % (f"{ha:.4f}", f"{a:,.0f}"))
                return ("Area", "%s m²  (%s ha)"
                        % (f"{a:,.1f}", f"{ha:.4f}"))
            if gt == QgsWkbTypes.GeometryType.LineGeometry:
                L = geom_bng.length()
                if L >= 1000:
                    return ("Length", "%s km  (%s m)"
                            % (f"{L / 1000.0:.3f}", f"{L:,.1f}"))
                return ("Length", "%s m" % f"{L:,.1f}")
        except Exception:
            return None
        return None

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._exit()
        else:
            super().keyReleaseEvent(event)

    def deactivate(self):
        self.iface.messageBar().clearWidgets()
        super().deactivate()

    def _exit(self):
        self.canvas().unsetMapTool(self)
        self.iface.actionPan().trigger()


# =============================================================================
#  Redline Distance Measure Tool — click points, measure from redline boundary
# =============================================================================

class _RedlineMeasureTool(QgsMapToolEmitPoint):
    """Click points on the map. Shows the straight-line distance from the
    nearest point on the redline boundary to the clicked point.

    Each click adds a marker and shows cumulative distance from redline.
    Right-click or Escape clears and exits.
    """

    def __init__(self, canvas, iface, main_panel):
        super().__init__(canvas)
        self.iface      = iface
        self.main_panel = main_panel
        self._markers   = []
        self._rubberband = None
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._clear()
            self._exit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        from qgis.core import (
            QgsGeometry, QgsPointXY, QgsProject,
            QgsCoordinateTransform, QgsCoordinateReferenceSystem,
        )
        from qgis.gui import QgsVertexMarker

        canvas     = self.canvas()
        point      = self.toMapCoordinates(event.pos())
        canvas_crs = canvas.mapSettings().destinationCrs()
        bng        = QgsCoordinateReferenceSystem("EPSG:27700")

        # Add a marker at click point
        marker = QgsVertexMarker(canvas)
        marker.setCenter(point)
        marker.setColor(__import__('qgis.PyQt.QtGui', fromlist=['QColor']).QColor("#9A6700"))
        marker.setIconSize(10)
        marker.setIconType(QgsVertexMarker.IconType.ICON_CROSS)
        marker.setPenWidth(2)
        self._markers.append(marker)

        # Convert click point to BNG for distance calculation
        click_bng = QgsGeometry.fromPointXY(QgsPointXY(point))
        if canvas_crs.authid() != "EPSG:27700":
            xf = QgsCoordinateTransform(canvas_crs, bng, QgsProject.instance())
            click_bng.transform(xf)

        click_pt = click_bng.asPoint()

        # Distance from redline boundary
        rl_geom = self.main_panel._get_redline_geom()
        if rl_geom and not rl_geom.isEmpty():
            dist_m = rl_geom.distance(click_bng)
        else:
            dist_m = None

        # Grid ref of clicked point
        gr_str = ""
        try:
            from .gridref_utils import osgb_en_to_gridref
            gr_str = osgb_en_to_gridref(int(click_pt.x()), int(click_pt.y()), 6)
        except Exception:
            gr_str = f"E{int(click_pt.x())} N{int(click_pt.y())}"

        if dist_m is not None:
            if dist_m < 1000:
                dist_str = f"{dist_m:.0f} m"
            else:
                dist_str = f"{dist_m / 1000:.2f} km"
            msg = f"{gr_str}  |  Distance to redline: {dist_str}  |  Right-click or Esc to clear"
        else:
            msg = f"{gr_str}  |  No redline drawn yet  |  Right-click or Esc to clear"

        self.iface.messageBar().clearWidgets()
        self.iface.messageBar().pushMessage(
            "Biome Measure", msg, level=Qgis.MessageLevel.Info, duration=0
        )

    def _clear(self):
        canvas = self.canvas()
        for m in self._markers:
            canvas.scene().removeItem(m)
        self._markers.clear()
        if self._rubberband:
            self._rubberband.reset()
            self._rubberband = None
        self.iface.messageBar().clearWidgets()

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._clear()
            self._exit()
        else:
            super().keyReleaseEvent(event)

    def deactivate(self):
        self._clear()
        super().deactivate()

    def _exit(self):
        self.canvas().unsetMapTool(self)
        self.iface.actionPan().trigger()


# ── Onboarding Wizard ─────────────────────────────────────────────────────────

def _illustration_pixmap(name, w, h):
    """Full-colour QPixmap of illustrations/<name>.svg, fitted to a w×h box at
    the artwork's native 140:100 aspect and centred. Rendered oversized and
    DPR-tagged so it stays crisp. Unlike _svg_pixmap these scenes carry their
    own palette, so there is no currentColor tint."""
    import os
    from qgis.PyQt.QtGui import QPixmap, QPainter
    from qgis.PyQt.QtCore import QByteArray, QRectF
    from qgis.PyQt.QtSvg import QSvgRenderer
    path = os.path.join(os.path.dirname(__file__), "illustrations", name + ".svg")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return QPixmap()
    scale = 3
    pm = QPixmap(int(w * scale), int(h * scale))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    iw, ih = 140.0, 100.0
    s = min(w / iw, h / ih)
    dw, dh = iw * s, ih * s
    dx, dy = (w - dw) / 2.0, (h - dh) / 2.0
    QSvgRenderer(QByteArray(raw.encode("utf-8"))).render(
        p, QRectF(dx * scale, dy * scale, dw * scale, dh * scale))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


class _OnboardingWizard(QDialog):
    """First-run onboarding wizard shown once to new users."""

    STEPS = [
        {
            "img": "onb_welcome",
            "title": "Welcome to Biome",
            "chip": ("Quick tour", "accent"),
            "lead": "Biome maps habitats and calculates Biodiversity Net Gain "
                    "(BNG) for planning applications in England.",
            "bullets": [
                "This tour covers the whole workflow, from a blank project to "
                "an exported metric.",
                "It takes about a minute. Reopen it anytime from "
                "<b>Getting Started</b> on the home screen.",
            ],
            "tip": "Work in order: map the site, check the Dashboard, then export.",
        },
        {
            "img": "onb_save",
            "title": "Step 1 — Save your project",
            "chip": ("Project menu", "muted"),
            "lead": "Save the QGIS project before you draw anything.",
            "bullets": [
                "Use <b>Project → Save As…</b> and give the site a name.",
                "Biome stores habitat data in a GeoPackage (<b>.gpkg</b>) next to "
                "your project (<b>.qgs</b> or <b>.qgz</b>), so the project must exist on disk first.",
            ],
            "tip": "Keep one folder per site — the QGIS project and its .gpkg together.",
        },
        {
            "img": "onb_redline",
            "title": "Step 2 — Draw the Red Line Boundary",
            "chip": ("Map & Tools", "accent"),
            "lead": "Define the site boundary first — everything else keys off it.",
            "bullets": [
                "On <b>Map &amp; Tools</b>, click <b>Add Red Line Boundary</b>.",
                "<b>Left-click</b> to place each vertex; <b>right-click</b> to finish.",
                "The redline sets the site area and centroid, and clips habitats "
                "to the boundary.",
            ],
            "tip": "Turn on snapping to trace basemap features accurately.",
        },
        {
            "img": "onb_baseline",
            "title": "Step 3 — Map Baseline Habitats",
            "chip": ("Map & Tools", "accent"),
            "lead": "Record what is on the site today.",
            "bullets": [
                "Click <b>Baseline Habitats</b> and pick a type — Area, Hedgerow, "
                "Watercourse or Trees.",
                "Set the <b>type</b>, <b>condition</b> and <b>retention</b>, then "
                "<b>Draw Feature on Map</b>.",
                "Map every parcel inside the redline — leave <b>no gaps</b>.",
            ],
            "tip": "Adjacent parcels should share edges; snapping avoids slivers.",
        },
        {
            "img": "onb_proposed",
            "title": "Step 4 — Map Proposed Habitats",
            "chip": ("Map & Tools", "accent"),
            "lead": "Map what each area becomes after development.",
            "bullets": [
                "Click <b>Proposed Habitats</b> and draw the post-development "
                "habitats.",
                "With <b>Create Baseline + Proposed</b> on, drawing a baseline "
                "auto-creates a matching proposed feature — just update its type "
                "and condition.",
                "Mark each baseline feature <b>Lost</b>, <b>Retained</b> or "
                "<b>Enhanced</b>.",
            ],
            "tip": "Retained and Enhanced parcels keep their footprint; Lost ones "
                   "are replaced.",
        },
        {
            "img": "onb_condition",
            "title": "Step 5 — Condition & Enhancement",
            "chip": ("Habitat form", "accent"),
            "lead": "Condition drives the unit score, and enhancement is where "
                    "the gain comes from.",
            "bullets": [
                "Use the <b>Condition</b> assessment to score each habitat against "
                "its criteria.",
                "Raising condition, or moving to a higher-value habitat, creates "
                "the <b>uplift</b> that BNG rewards.",
                "Watercourses and hedgerows score on their own metrics.",
            ],
            "tip": "Small condition gains across many parcels often beat one big "
                   "type change.",
        },
        {
            "img": "onb_deskstudy",
            "title": "Step 6 — Desk Study",
            "chip": ("Desk Study tab", "accent"),
            "lead": "Pull in the spatial constraints around the site.",
            "bullets": [
                "Draw the <b>Redline</b>, then add the <b>Search buffer</b>.",
                "Expand <b>Constraints</b> to load designated sites (SSSI, SAC, "
                "SPA, Ramsar, AONB…), Priority Habitats, GCN risk zones, EPSL "
                "licences and water bodies.",
                "Use the <b>report</b> buttons to build distance tables for each "
                "type.",
            ],
            "tip": "The buffer defines your search area — set it before loading "
                   "constraints.",
        },
        {
            "img": "onb_export",
            "title": "Step 7 — Review & Export",
            "chip": ("Home screen", "good"),
            "lead": "Check the numbers, then produce your outputs.",
            "bullets": [
                "Open the <b>Summary</b> tab to review baseline vs proposed totals.",
                "Use <b>Manage Features</b> to edit, reshape, recolour or delete "
                "anything.",
                "From the home screen: <b>Create Final Map</b>, <b>Export Table</b>, "
                "<b>Blank Metric</b> or <b>Filled Metric</b> (pre-filled from your "
                "map).",
            ],
            "tip": "Reopen this tour anytime from <b>Getting Started</b>; the "
                   "<b>User Guide</b> has full detail.",
        },
    ]

    # Location-chip colours by kind (mirrors theme.chip without reaching into
    # its private table).
    _CHIP = {
        "accent": (theme.ACCENT_DARK, theme.ACCENT_SOFT),
        "good":   (theme.GOOD, theme.GOOD_BG),
        "info":   (theme.INFO, theme.INFO_BG),
        "muted":  (theme.MUTED, theme.SUNKEN),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Getting Started")
        self.setMinimumWidth(500)
        self.setMinimumHeight(580)
        self.resize(520, 600)
        self.setStyleSheet(f"background:{theme.SURFACE};")
        self._step = 0
        self._dots = []
        self._build()

    def _build(self):
        self._outer = QVBoxLayout()
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # Progress bar
        self._prog_bar = QWidget()
        self._prog_bar.setFixedHeight(4)
        self._prog_bar.setStyleSheet(f"background:{theme.BORDER};")
        self._prog_fill = QWidget(self._prog_bar)
        self._prog_fill.setStyleSheet(f"background:{theme.ACCENT};")
        self._prog_fill.setFixedHeight(4)
        self._outer.addWidget(self._prog_bar)

        # Hero illustration band
        self._hero = QLabel()
        self._hero.setFixedHeight(168)
        self._hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hero.setStyleSheet(f"background:{theme.ACCENT_SOFT};")
        self._outer.addWidget(self._hero)

        # Content area
        content = QWidget()
        content.setStyleSheet(f"background:{theme.SURFACE};")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(34, 22, 34, 12)
        cl.setSpacing(9)

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(
            f"font-family:{theme.FONT_SEMIBOLD}; font-size:17px; font-weight:600;"
            f" color:{theme.ACCENT_DARK}; background:transparent;")

        # Centred location chip
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.addStretch()
        self._chip_lbl = QLabel()
        self._chip_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip_row.addWidget(self._chip_lbl)
        chip_row.addStretch()

        # Body — scrollable so longer steps never clip
        self._body_lbl = QLabel()
        self._body_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._body_lbl.setStyleSheet(
            f"font-size:13px; color:{theme.MUTED}; background:transparent;")
        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_scroll.setStyleSheet("background:transparent;")
        body_scroll.setWidget(self._body_lbl)
        body_scroll.viewport().setStyleSheet("background:transparent;")

        # Clickable step dots
        dots_row = QHBoxLayout()
        dots_row.setContentsMargins(0, 4, 0, 0)
        dots_row.setSpacing(6)
        dots_row.addStretch()
        for i in range(len(self.STEPS)):
            d = QPushButton()
            d.setFlat(True)
            d.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            d.setFixedHeight(7)
            d.clicked.connect(lambda _=False, idx=i: self._goto(idx))
            self._dots.append(d)
            dots_row.addWidget(d)
        dots_row.addStretch()

        cl.addWidget(self._title_lbl)
        cl.addLayout(chip_row)
        cl.addSpacing(2)
        cl.addWidget(body_scroll, 1)
        cl.addLayout(dots_row)
        self._outer.addWidget(content, 1)

        # Button row
        btn_row = QWidget()
        btn_row.setStyleSheet(
            f"background:{theme.CARD}; border-top:1px solid {theme.BORDER};")
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(22, 12, 22, 12)
        bl.setSpacing(8)

        self._skip_btn = QPushButton("Skip tour")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.FAINT};"
            f" border:none; font-size:12px; }}"
            f"QPushButton:hover {{ color:{theme.MUTED}; }}")
        self._skip_btn.clicked.connect(self.reject)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.CARD}; color:{theme.ACCENT};"
            f" border:1px solid {theme.ACCENT}; border-radius:6px;"
            f" font-size:12px; padding:7px 16px; }}"
            f"QPushButton:hover {{ background:{theme.ACCENT_SOFT}; }}")
        self._back_btn.clicked.connect(self._prev)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.ACCENT_DARK}; color:white;"
            f" border:none; border-radius:6px; font-family:{theme.FONT_SEMIBOLD};"
            f" font-size:12px; font-weight:600; padding:7px 18px; }}"
            f"QPushButton:hover {{ background:{theme.ACCENT}; }}")
        self._next_btn.clicked.connect(self._next)

        bl.addWidget(self._skip_btn)
        bl.addStretch()
        bl.addWidget(self._back_btn)
        bl.addWidget(self._next_btn)
        self._outer.addWidget(btn_row)

        self.setLayout(self._outer)
        self._update()

    def _compose_body(self, step):
        """Build the rich-text body: lead line, action bullets, then a tip."""
        parts = [f'<div style="color:{theme.INK}; margin-bottom:9px;">'
                 f'{step["lead"]}</div>']
        bullets = step.get("bullets") or []
        if bullets:
            lis = "".join(
                f'<li style="margin-bottom:6px;">{b}</li>' for b in bullets)
            parts.append(f'<ul style="margin:0; -qt-list-indent:1;">{lis}</ul>')
        tip = step.get("tip")
        if tip:
            parts.append(
                f'<div style="margin-top:11px; color:{theme.MUTED};">'
                f'<b style="color:{theme.ACCENT_DARK};">Tip&nbsp;&nbsp;</b>'
                f'{tip}</div>')
        return "".join(parts)

    def _update(self):
        step = self.STEPS[self._step]
        self._hero.setPixmap(_illustration_pixmap(step["img"], 224, 152))
        self._title_lbl.setText(step["title"])

        chip = step.get("chip")
        if chip:
            text, kind = chip
            fg, bg = self._CHIP.get(kind, self._CHIP["muted"])
            self._chip_lbl.setText(text)
            self._chip_lbl.setStyleSheet(
                f"color:{fg}; background:{bg}; border:none; border-radius:9px;"
                f" padding:2px 11px; font-family:{theme.FONT_SEMIBOLD};"
                f" font-size:11px; font-weight:600;")
            self._chip_lbl.show()
        else:
            self._chip_lbl.hide()

        self._body_lbl.setText(self._compose_body(step))

        n = len(self.STEPS)
        for i, d in enumerate(self._dots):
            if i == self._step:
                d.setFixedWidth(18)
                d.setStyleSheet(
                    f"background:{theme.ACCENT}; border:none; border-radius:3px;")
            else:
                d.setFixedWidth(7)
                d.setStyleSheet(
                    f"background:{theme.BORDER_STRONG}; border:none;"
                    f" border-radius:3px;")

        self._back_btn.setVisible(self._step > 0)
        self._next_btn.setText(
            "Get started  ✓" if self._step == n - 1 else "Next →")
        frac = (self._step + 1) / n
        self._prog_fill.setFixedWidth(int(self.width() * frac))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        n = len(self.STEPS)
        frac = (self._step + 1) / n
        self._prog_fill.setFixedWidth(int(self.width() * frac))

    def _goto(self, i):
        if 0 <= i < len(self.STEPS) and i != self._step:
            self._step = i
            self._update()

    def _next(self):
        if self._step < len(self.STEPS) - 1:
            self._step += 1
            self._update()
        else:
            self.accept()

    def _prev(self):
        if self._step > 0:
            self._step -= 1
            self._update()
