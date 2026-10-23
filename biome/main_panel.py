# main_panel.py

import os

from qgis.PyQt.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QStackedWidget, QFrame, QLineEdit, QSizePolicy, QScrollArea, QGridLayout, QDialog,
    QMessageBox, QSlider, QListWidget, QToolButton, QMenu, QApplication)
from qgis.PyQt.QtCore import (Qt, QSettings, QTimer, QSize)
from qgis.PyQt.QtGui import QPixmap, QCursor, QColor, QFont
from qgis.core import Qgis
from qgis.core import (QgsProject, QgsPointXY, QgsRectangle, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, QgsGeometry)
from .layer_manager import LayerManager
from . import theme
from .base_layers import load_base_layers
from .designated_sites import (clear_ds_cache, add_2km_buffer)
from .council_maps import build_lnrs_deeplink
from .config import (get_os_api_key, get_os_ngd_api_key,
                     get_os_ngd_basemap_style, set_os_ngd_basemap_style,
                     get_enabled_basemap_buttons, get_basemap_button_order,
                     set_basemap_button_order)


from .panel_widgets import (_dbg, _build_no_gc, DUP_PAIR, _svg_pixmap, _svg_icon, _dot_pixmap,
    _banner, _swatch_for, DEFAULT_QUICK_HABITATS, _darken, _cond_chip, _ElidingLabel)
from .panel_map_tools import (_RedLineBuffersDialog, _run_redline_buffers, _OnboardingWizard)
from .desk_study_tab import DeskStudyMixin
from .summary_widgets import SummaryWidgetsMixin
from .settings_dialogs import SettingsDialogsMixin
from .building_tree_tab import BuildingTreeMixin
from .manage_screen import ManageScreenMixin


class _BasemapToolButton(QToolButton):
    """Segment button which distinguishes clicking from reordering by drag."""

    def __init__(self, key, move_callback, save_callback, parent=None):
        super().__init__(parent)
        self.basemap_key = key
        self._move_callback = move_callback
        self._save_callback = save_callback
        self._press_global = None
        self._dragging = False
        self._arrow_pressed = False
        self._arrow_callback = None

    def mousePressEvent(self, event):
        local_pos = (event.position().toPoint() if hasattr(event, "position")
                     else event.pos())
        if (self._arrow_callback is not None
                and event.button() == Qt.MouseButton.LeftButton
                and local_pos.x() >= self.width() - 17):
            self._arrow_pressed = True
            self._arrow_callback(self)
            event.accept()
            return
        self._press_global = (event.globalPosition().toPoint()
                              if hasattr(event, "globalPosition")
                              else event.globalPos())
        self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press_global is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            pos = (event.globalPosition().toPoint()
                   if hasattr(event, "globalPosition") else event.globalPos())
            if ((pos - self._press_global).manhattanLength()
                    >= QApplication.startDragDistance()):
                self._dragging = True
                self.setDown(False)
            if self._dragging:
                self._move_callback(self, pos)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._arrow_pressed:
            self._arrow_pressed = False
            event.accept()
            return
        if self._dragging:
            self._dragging = False
            self._press_global = None
            self.setDown(False)
            self._save_callback()
            event.accept()
            return
        self._press_global = None
        super().mouseReleaseEvent(event)


class BNGMainPanel(DeskStudyMixin, SummaryWidgetsMixin, SettingsDialogsMixin, BuildingTreeMixin, ManageScreenMixin, QDockWidget):

    def __init__(self, iface, parent=None):
        super().__init__('Biome', parent)
        self.setObjectName('BiomePanel')  # lets QGIS save/restore the dock size
        self.iface         = iface
        self.layer_manager = LayerManager(iface)
        self.setMinimumWidth(300)
        # Preferred dock width — without this Qt sizes the dock to the widest
        # content size-hint (~800px). The user can still drag it wider.
        self._preferred_width = 400
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)

        # Typography and base styling come from theme.dock_qss(), applied to
        # this dock only in _build_ui — the host application's font is left
        # alone so other plugins and QGIS itself render as the user expects.

        # Global undo stack — survives screen navigation
        # Each entry: (baseline_layer, baseline_fid, proposed_layer, proposed_fid_or_None)
        self._undo_stack = []
        # Global redo stack — session-only (not persisted). Each entry is a
        # list of capture dicts {lid, wkt, attrs} snapshotted by
        # _do_global_undo just before it deleted them; a new placement
        # (push_undo) invalidates it.
        self._redo_stack = []
        self._geocode_reply = None
        self._search_marker = None      # temporary canvas marker for search hits
        self._proposed_mapping = False  # when False, skip proposed prompts
        self._hover_highlight = None    # current QgsHighlight for manage-list hover
        self._georef_layer_ids = set()  # IDs of layers added by the georeferencer
        self._georef_expected_layer_name = None  # stem of the georef output filename

        # Summary tab cache: rebuilt only when habitat data has changed
        # since the last build, never merely because the tab was opened.
        self._summary_dirty = True

        # Deleted feature bin — session-only, does not persist or feed calculations
        # Each entry: {label, layer_name, layer_key, geom_wkt, attrs, deleted_at}
        self._deleted_bin = []

        # Recent habitats — keyed by phase ("baseline" / "proposed")
        # Each entry is a dict of form values snapshotted after a successful save.
        # Max 5 per phase; most recent first.
        self._recent_habitats = {"baseline": [], "proposed": []}

        # The habitat form the user is currently placing into. Used to ignore
        # post-placement navigation requests that arrive from a *stale* form —
        # e.g. a leftover drawing session whose map tool / event filter fired
        # after the user moved on. Without this guard a left-behind Proposed
        # session can yank the panel to the Proposed screen mid-way through
        # placing a baseline habitat.
        self._active_habitat_form = None

        # The Building & Tree Mapper screen widget while it is open, so go_back
        # can stop its placement tools before navigating away.
        self._bt_screen_widget = None

        # True once base maps / habitat groups have been scaffolded into the
        # current project, so we don't do it twice.
        self._project_initialised = False

        # Session-only desk-study map renames (right-click a map button). The
        # dict lives here so it survives even before the lazily-built desk-study
        # tab wires the buttons; it is never persisted, so it reverts on restart.
        self._map_name_overrides = {}
        self._build_ui()
        # ── TEMPORARY: width debugging — logs to Log Messages panel and
        #    ~/biome_width_debug.log. Remove once the widening bug is found.
        try:
            from . import width_debug
            width_debug.install(self)
        except Exception:
            pass
        self._install_transform_dialog_suppressor()

        # Re-run post-load setup whenever a project is opened.
        # This fires AFTER QgsProject::readProjectFile completes, avoiding the
        # QGIS 3.44 Windows crash caused by setDestinationCrs firing mid-read.
        QgsProject.instance().readProject.connect(self._on_project_read)
        # Heal the project whenever layers are registered. readProject fires
        # once, but QGIS can finish registering a project's saved layers across
        # several event-loop ticks; relying on a single fixed delay made the
        # cleanup land "every so often" instead of every open. Re-running the
        # (idempotent) heal on layersAdded — debounced — guarantees duplicates
        # are collapsed and orphaned data reconnected no matter when the layers
        # actually arrive.
        try:
            QgsProject.instance().layersAdded.connect(self._schedule_heal)
        except Exception:
            pass
        # Persist the undo stack into the project file on save so it survives
        # save → close → reopen (entries are re-validated on read). Connected
        # as a bound method — NOT a lambda — so PyQt auto-disconnects it when
        # this dock is destroyed; a receiver-less lambda outlives plugin
        # reloads and keeps writing the dead panel's stack on every save.
        try:
            QgsProject.instance().writeProject.connect(
                self._on_write_project_undo)
        except Exception:
            pass
        # Drop the stack when the project is closed/cleared via native QGIS
        # paths (File → New / Close) that never fire readProject — otherwise
        # the Undo button keeps counting entries from a project that's gone.
        try:
            QgsProject.instance().cleared.connect(self._on_project_cleared_undo)
        except Exception:
            pass
        # Plan-mode right-click menu on the map (feature info, buffer a
        # constraint feature, grid ref of the click point). Bound method so PyQt
        # auto-disconnects it when this dock is destroyed.
        try:
            self.iface.mapCanvas().contextMenuAboutToShow.connect(
                self._canvas_context_menu)
        except Exception:
            pass

        # Show onboarding wizard on first run
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(800, self._maybe_show_onboarding)
        self._open_layers_panel()
        self._install_pan_tool()
        self._mark_summary_dirty()
        self._install_os_maps_error_hook()

        # IMPORTANT: scaffolding (base maps + habitat/eco groups + layers) is
        # deliberately NOT done in __init__. On a blank QGIS startup project
        # this would mark the project "dirty" and make QGIS prompt
        # "Do you want to save the current project?" the moment it restores
        # the most-recent project or the user opens a saved file. Scaffolding
        # only happens when the user creates a NEW project, or is restored for
        # an existing project via _on_project_read. If this panel is created
        # while an existing project is already loaded (e.g. opened before the
        # panel existed), restore that project's setup now.
        if QgsProject.instance().fileName():
            self.activate_for_existing_project()

    def sizeHint(self):
        """Cap the dock's preferred width so QGIS doesn't open it at the
        widest content size-hint. Height is left to the default."""
        sz = super().sizeHint()
        sz.setWidth(self._preferred_width)
        return sz

    def minimumSizeHint(self):
        """Decouple the dock's minimum width from its content.

        Without this, any screen whose layout has a large minimum (summary
        tables, button grids) forces QGIS to expand the dock and prevents
        the user from dragging it back down. Every screen scrolls, so it is
        safe to let the user shrink the dock to the declared minimum.
        """
        from qgis.PyQt.QtCore import QSize
        return QSize(300, 200)

    def setup_project_layers(self, load_basemaps=True):
        """Scaffold the current project for Biome.

        Adds the habitat / ecological-feature layer-tree groups and wires up
        styling. When ``load_basemaps`` is True (a brand-new project) the base
        map layers are added too; for an existing project they are already
        saved in the file, so we skip them. Safe to call more than once — the
        underlying helpers only insert what is missing.
        """
        # TEMP load diagnostics: per-stage timings → biome_shutdown.log.
        import time as _t

        def _lg(msg, t0):
            try:
                from .bng_plugin import _slog
                _slog("scaffold: %s (%.1fs)" % (msg, _t.perf_counter() - t0))
            except Exception:
                pass

        t = _t.perf_counter()
        self._set_default_datum_transform()
        if load_basemaps:
            load_base_layers(get_os_api_key())
            _lg("base map layers added", t)
            t = _t.perf_counter()
        self.layer_manager.ensure_tree_structure()
        _lg("layer tree ensured", t)
        t = _t.perf_counter()
        self._initialise_all_layers()
        _lg("all layers initialised+styled", t)
        # Start the Ecological Features group collapsed, like the other groups.
        self.layer_manager.collapse_eco_group()
        self._project_initialised = True
        # The segmented basemap bars were built (and synced) before any basemap
        # layer existed, leaving every button grey — re-sync now the default
        # basemap (Google Hybrid) is loaded and visible.
        self._sync_basemap_buttons()

    def activate_for_new_project(self):
        """Start the plugin for a freshly-created (blank) project: load the
        base maps and create the layer-tree structure, then frame the UK.

        The canvas is frozen for the whole scaffold so the first paint the
        user sees is already the UK. The freeze must outlive this method:
        QGIS's layer-tree bridge reacts to the first layers with a QUEUED
        zoomToProjectExtent() — the whole world for global basemaps — which
        lands after this returns, so the framing + unfreeze run from a
        0-timer queued behind it (with a late backstop in case the bridge's
        zoom arrives even later)."""
        from qgis.PyQt.QtCore import QTimer
        self._undo_stack = []
        self._redo_stack = []
        self._sync_undo_btn()
        try:
            self.iface.mapCanvas().freeze(True)
        except Exception:
            pass
        try:
            self.setup_project_layers(load_basemaps=True)
        finally:
            QTimer.singleShot(0, self._finish_startup_frame)
            QTimer.singleShot(700, self._startup_frame_backstop)

    def activate_for_existing_project(self):
        """Restore the plugin for an already-loaded project: ensure groups and
        styling are correct (base maps are already in the saved project) and
        recognise any existing redline. Frozen + framed like
        activate_for_new_project, so the first paint is already the project
        frame instead of a world/stale view that jumps a second later."""
        from qgis.PyQt.QtCore import QTimer
        try:
            self.iface.mapCanvas().freeze(True)
        except Exception:
            pass
        try:
            self.setup_project_layers(load_basemaps=False)
        finally:
            QTimer.singleShot(0, self._finish_startup_frame)
            QTimer.singleShot(700, self._startup_frame_backstop)
        QTimer.singleShot(400, self._recognise_existing_redline)

    def _finish_startup_frame(self):
        """Frame the project/UK and unfreeze — runs from a 0-timer queued
        behind the layer-tree bridge's own queued world-zoom, so this framing
        wins and the canvas's first real paint is already framed."""
        try:
            self._zoom_to_uk(immediate=True)
        except Exception:
            pass
        try:
            canvas = self.iface.mapCanvas()
            canvas.freeze(False)
            canvas.refresh()
        except Exception:
            pass

    def _startup_frame_backstop(self):
        """Late guard: if the bridge's world-zoom still managed to land after
        the startup framing, pull the canvas back. A ~6,000 km-wide extent can
        only be the world view — no UK/site framing ever comes near it."""
        try:
            if self.iface.mapCanvas().extent().width() > 6_000_000:
                self._zoom_to_uk(immediate=True)
        except Exception:
            pass

    def _check_save_format(self):
        """Compatibility hook: Biome supports both .qgs and .qgz projects."""
        return

    def _install_os_maps_error_hook(self):
        """Watch QgsMessageLog for OS Maps tile access errors and show a
        friendly 'Zoom out to see OS Maps' notice in the message bar."""
        from qgis.core import QgsApplication
        self._os_maps_bar_msg = None   # track so we don't spam

        def _on_log_message(message, tag, level):
            # OS XYZ tile providers emit HTTP 403 errors into the QGIS message log
            if "forbidden" not in message.lower() and "access denied" not in message.lower():
                return
            # Only act when OS Maps is the active basemap
            from qgis.core import QgsProject
            root = QgsProject.instance().layerTreeRoot()
            os_layers = QgsProject.instance().mapLayersByName("OS Outdoor")
            if not os_layers:
                return
            node = root.findLayer(os_layers[0].id())
            if node is None or not node.isVisible():
                return
            # Avoid spamming — only show if not already visible
            bar = self.iface.messageBar()
            bar.clearWidgets()
            bar.pushMessage(
                "OS Maps",
                "Zoom out to see OS Maps — this zoom level requires a higher-tier API key.",
                level=Qgis.MessageLevel.Warning,   # Qgis.Warning (yellow)
                duration=6,
            )

        log = QgsApplication.messageLog()
        log.messageReceived.connect(_on_log_message)
        # Keep a reference so the connection isn't garbage-collected
        self._os_log_handler = _on_log_message

    # ------------------------------------------------------------------ #
    #  GLOBAL UNDO API  (called by HabitatFormScreen after each feature)  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_undo_entry(entry):
        """Normalise a stack entry (legacy 4/5/7-tuple, or persisted JSON list)
        to (b, bf, p, pf, extra_fids, extra_pairs, sigs, repairs).

        repairs (cookie-cutter placements) is None or a dict:
          {"mods":   [[lid, fid, before_wkt, after_wkt, sig_after], ...],
           "readds": [{"lid":..., "wkt":..., "attrs": {...}}, ...]}
        mods are neighbours whose geometry the punch shrank (restore
        before_wkt on undo); readds are neighbours it deleted outright."""
        b, bf, p, pf = entry[0], entry[1], entry[2], entry[3]
        extra = list(entry[4]) if len(entry) > 4 and entry[4] else []
        pairs = ([(x[0], x[1]) for x in entry[5]]
                 if len(entry) > 5 and entry[5] else [])
        sigs = entry[6] if len(entry) > 6 and isinstance(entry[6], dict) else {}
        repairs = entry[7] if len(entry) > 7 and isinstance(entry[7], dict) else None
        return b, bf, p, pf, extra, pairs, sigs, repairs

    def _feature_sig(self, layer, fid):
        """Identity fingerprint for an undo slot, re-verified at undo time so a
        recycled fid (GPKG reuses the max rowid after a delete) can never make
        undo remove a DIFFERENT feature that later inherited the number.

        Prefers the feature's `uuid` attribute (placer layers carry one, and it
        survives relocation/geometry edits); falls back to a geometry hash.
        Returns None when the feature can't be read — sig checks are then
        skipped rather than blocking the undo."""
        if layer is None or fid is None:
            return None
        if isinstance(layer, str):
            from qgis.core import QgsProject
            layer = QgsProject.instance().mapLayer(layer)
            if layer is None:
                return None
        try:
            feat = layer.getFeature(fid)
            if feat is None or not feat.isValid():
                return None
            idx = layer.fields().lookupField("uuid")
            if idx >= 0:
                val = feat.attribute(idx)
                if val not in (None, ""):
                    return "u:" + str(val)
            geom = feat.geometry()
            if geom is None or geom.isNull():
                return None
            import hashlib
            return "g:" + hashlib.md5(bytes(geom.asWkb())).hexdigest()[:16]
        except Exception:
            return None

    def push_undo(self, baseline_layer, baseline_fid, proposed_layer, proposed_fid,
                  extra_baseline_fids=None, extra_pairs=None, repairs=None):
        """Record one placed feature so it can be undone from anywhere.

        We store layer *ids* (stable strings) rather than layer *objects*.
        A stored QgsVectorLayer becomes a dangling pointer the moment a project
        is closed/reopened (QGIS destroys and recreates the C++ layer), which
        previously made undo crash or silently no-op. Resolving by id at undo
        time always finds the live layer — or cleanly skips if it's gone.

        extra_baseline_fids: when a feature is clipped to the redline it can
        split into several parts on the baseline layer. Pass the remaining part
        fids here so a single undo removes the whole placement rather than
        orphaning the extra parts.

        extra_pairs: (layer, fid) companions on OTHER layers that belong to the
        same placement — an FOV wedge polygon, a buffer zone — so one undo
        removes the whole placement rather than stranding the companions.

        repairs: damage a cookie-cutter placement did to NEIGHBOURING features
        so undo can put them back: {"mods": [[lid, fid, before_wkt, after_wkt],
        ...], "readds": [{"lid","wkt","attrs"}, ...]}. Each mod gets stamped
        with the neighbour's current (post-punch) identity sig so a later undo
        never overwrites a feature the user has since edited or replaced.
        """
        def _lid(layer):
            try:
                return layer.id() if layer is not None else None
            except Exception:
                return None
        extra = list(extra_baseline_fids or [])
        pairs = [(_lid(lyr), fid) for lyr, fid in (extra_pairs or [])]
        sigs = {
            "b": self._feature_sig(baseline_layer, baseline_fid),
            "p": self._feature_sig(proposed_layer, proposed_fid),
            "extra": [self._feature_sig(baseline_layer, x) for x in extra],
            "pairs": [self._feature_sig(lyr, fid)
                      for lyr, fid in (extra_pairs or [])],
        }
        rep = None
        if repairs and (repairs.get("mods") or repairs.get("readds")):
            mods = []
            for m in (repairs.get("mods") or []):
                lid, fid, before_wkt, after_wkt = m[0], m[1], m[2], m[3]
                mods.append([lid, fid, before_wkt, after_wkt,
                             self._feature_sig(lid, fid)])
            rep = {"mods": mods,
                   "readds": list(repairs.get("readds") or [])}
        self._undo_stack.append((_lid(baseline_layer), baseline_fid,
                                 _lid(proposed_layer), proposed_fid,
                                 extra, pairs, sigs, rep))
        # A fresh placement supersedes anything previously undone.
        self._redo_stack = []
        self._sync_undo_btn()

    # ── Manage tab: duplicate a feature into its baseline<->proposed twin ──────
    def _dup_button_allowed(self, layer, feat, key):
        """Whether a Manage-tab feature offers 'duplicate to the paired layer'.
        Only the 8 habitat keys, and only when the feature does NOT already have a
        twin in the paired layer:
          • trees / hedgerows / watercourses — a non-empty pair_id means it was
            placed as a pair (or linked via the 'same feature?' dialog); a second
            twin would double it.
          • areas — no pair_id (they're net-classified spatially), so detect the
            twin GEOMETRICALLY: if the footprint is already (near-)fully covered
            by the paired layer it's already twinned / re-mapped in the other
            phase. This is a read-only spatial check — it never writes anything or
            touches the net classifier."""
        if key not in DUP_PAIR:
            return False
        if key in ("baseline_area", "proposed_area"):
            try:
                target = self.layer_manager.get_layer(DUP_PAIR[key])
                if target is not None and target.isValid():
                    from .measurements import area_covered_fraction
                    if area_covered_fraction(feat.geometry(), target) >= 0.9:
                        return False   # already twinned / re-mapped in the other phase
            except Exception:
                pass
            return True
        try:
            pidx = layer.fields().indexOf("pair_id")
            if pidx >= 0 and str(feat["pair_id"] or "").strip():
                return False   # already linked / decided
        except Exception:
            pass
        return True

    def _build_duplicate_attrs(self, src_feat, src_key, target_layer, pair_token):
        """{field: value} for the duplicated twin: copy every shared field
        verbatim, then flip only the phase discriminators. Areas get NO
        pair_id/retention (spatial net decides their fate); trees/hedgerows/
        watercourses get the shared pair_token so the pair reads Retained."""
        flip_phase = "baseline" if src_key.startswith("proposed_") else "proposed"
        is_area = src_key in ("baseline_area", "proposed_area")
        is_tree = src_key in ("baseline_trees", "proposed_trees")
        tgt_names = {target_layer.fields().at(i).name()
                     for i in range(target_layer.fields().count())}
        src_fields = src_feat.fields()
        attrs = {}
        for i in range(src_fields.count()):
            name = src_fields.at(i).name()
            if name == "fid":                       # provider assigns the fid
                continue
            if name in tgt_names:
                attrs[name] = src_feat[name]         # verbatim copy
        attrs["phase"] = flip_phase                 # the sole non-tree discriminator
        if is_tree:
            attrs["Category"] = "Existing" if flip_phase == "baseline" else "Proposed"
        if is_area:
            attrs["retention"] = ""                 # net-classified — leave blank
            attrs["Retention Category"] = ""
            attrs["pair_id"] = ""
        else:
            attrs["retention"] = "Retained"         # linked twin = retained
            attrs["Retention Category"] = "Retained"
            attrs["pair_id"] = pair_token
        return attrs

    def _duplicate_to_paired(self, src_layer, fid, src_key):
        """Duplicate one placed feature into its baseline<->proposed twin layer.
        Areas: geometry+attribute copy, checked for same-class overlap first.
        Trees/hedgerows/watercourses: copy + a shared pair_id so the pair reads
        Retained. Geometry is copied verbatim (no re-clip — it already reflects
        whatever clipping the source had). Registers on the global undo stack."""
        import uuid
        from qgis.core import QgsFeature, QgsGeometry, QgsFeatureRequest
        from qgis.PyQt.QtWidgets import QMessageBox
        target_key = DUP_PAIR.get(src_key)
        if not target_key:
            return
        try:
            target_layer = self.layer_manager.get_or_create_layer(target_key)
        except Exception:
            target_layer = None
        if target_layer is None or not target_layer.isValid():
            return
        try:
            src_feat = next(src_layer.getFeatures(
                QgsFeatureRequest().setFilterFid(fid)))
        except Exception:
            return
        geom = src_feat.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            self.iface.messageBar().pushMessage(
                "BNG Mapper", "Can't duplicate — the feature has no geometry.",
                level=Qgis.MessageLevel.Warning, duration=6)
            return
        geom = QgsGeometry(geom)

        is_area = src_key in ("baseline_area", "proposed_area")
        # Overlap check — AREAS only (the helper returns False for lines/points,
        # so an area landing on a tree or hedge never triggers it).
        if is_area:
            try:
                from .measurements import overlaps_in_layer
                overlaps, ha, _f = overlaps_in_layer(geom, target_layer)
            except Exception:
                overlaps, ha = False, 0.0
            if overlaps:
                reply = QMessageBox.question(
                    self.iface.mainWindow(), "Overlapping area",
                    ("This area would overlap an existing habitat in the "
                     "“%s” layer by about %.3f ha. Overlapping polygons "
                     "double-count habitat area and inflate the BNG metric.\n\n"
                     "Duplicate anyway?" % (target_layer.name(), ha)),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return

        pair_token = "" if is_area else uuid.uuid4().hex
        attrs = self._build_duplicate_attrs(src_feat, src_key, target_layer, pair_token)

        new_feat = QgsFeature(target_layer.fields())
        new_feat.setGeometry(geom)
        for name, val in attrs.items():
            idx = target_layer.fields().indexOf(name)
            if idx >= 0:
                new_feat.setAttribute(idx, val)
        ok, added = target_layer.dataProvider().addFeatures([new_feat])
        if not ok or not added:
            self.iface.messageBar().pushMessage(
                "BNG Mapper", "Duplicate failed — could not add the feature.",
                level=Qgis.MessageLevel.Critical, duration=8)
            return
        new_fid = added[0].id()
        target_layer.reload(); target_layer.updateExtents(); target_layer.triggerRepaint()

        # Stamp the shared pair_id on the SOURCE too (trees/hedges/water only) so
        # the classifier reads baseline+proposed as one Retained feature.
        if pair_token:
            try:
                pidx = src_layer.fields().indexOf("pair_id")
                if pidx < 0:
                    LayerManager._ensure_pair_id(src_layer)
                    pidx = src_layer.fields().indexOf("pair_id")
                if pidx >= 0:
                    src_layer.dataProvider().changeAttributeValues(
                        {fid: {pidx: pair_token}})
                    src_layer.reload(); src_layer.triggerRepaint()
            except Exception:
                pass

        # Undo — the new feature is the only slot; the other is None. Pushed AFTER
        # commit so the identity signature matches the committed fid.
        try:
            if target_key.startswith("proposed_"):
                self.push_undo(None, None, target_layer, new_fid)
            else:
                self.push_undo(target_layer, new_fid, None, None)
        except Exception:
            pass

        self.iface.mapCanvas().refresh()
        self._manage_dirty = True
        self._schedule_manage_rebuild()

    # ── Manage tab: hedge/watercourse trimmer ──────────────────────────────
    def _arm_hedge_trimmer(self, layer, fid, key):
        """Arm the two-click trimmer map tool on one hedgerow/watercourse
        feature (Manage-tab scissors button). See hedge_trimmer.py."""
        from .hedge_trimmer import HedgeTrimTool
        # Only one Biome tool armed at a time.
        self._disarm_stranded_tools()
        tool = HedgeTrimTool(self.iface.mapCanvas(), self.iface, self,
                             layer, fid, key)
        if not tool.valid:
            self.iface.messageBar().pushMessage(
                "Hedge Trimmer",
                "This feature can't be trimmed — it must be a single-part line.",
                level=Qgis.MessageLevel.Warning, duration=5)
            return
        self._hedge_trim_tool = tool
        self.iface.mapCanvas().setMapTool(tool)

    def _finish_hedge_trimming(self):
        """Disarm the trimmer and restore the pan tool. Safe when not armed."""
        tool = getattr(self, "_hedge_trim_tool", None)
        if tool is not None and self.iface.mapCanvas().mapTool() is tool:
            self.iface.actionPan().trigger()   # deactivates → _trim_ui_reset
        self._trim_ui_reset()

    def _trim_ui_reset(self):
        """Forget the armed trimmer. Called from HedgeTrimTool.deactivate(),
        so picking ANY other map tool also clears the reference. Idempotent."""
        self._hedge_trim_tool = None

    def drop_undo_entries(self, removed_pairs):
        """Remove stack entries that reference any of the given (layer, fid)
        pairs. Called when features are removed by some other UI — e.g. the
        Feature Placer's own local Undo — so the global stack doesn't keep a
        stale entry that would burn a click (or, with fid recycling, target the
        wrong feature) later. Layers may be objects or id strings."""
        gone = set()
        for lyr, fid in (removed_pairs or []):
            try:
                lid = lyr if isinstance(lyr, str) else (
                    lyr.id() if lyr is not None else None)
            except Exception:
                lid = None
            if lid is not None and fid is not None:
                gone.add((lid, fid))
        if not gone:
            return
        kept = []
        for entry in self._undo_stack:
            b, bf, p, pf, extra, pairs, _sigs, _rep = self._parse_undo_entry(entry)
            refs = [(b, bf), (p, pf)] + [(b, x) for x in extra] + list(pairs)
            if any(r in gone for r in refs):
                continue
            kept.append(entry)
        if len(kept) != len(self._undo_stack):
            self._undo_stack = kept
            self._sync_undo_btn()

    def _sync_undo_btn(self):
        if not hasattr(self, "_undo_btn"):
            return
        n = len(self._undo_stack)
        self._undo_btn.setEnabled(n > 0)
        self._undo_btn.setText(f"Undo ({n})" if n else "Undo")
        rb = getattr(self, "_redo_btn", None)
        if rb is not None:
            m = len(getattr(self, "_redo_stack", None) or [])
            rb.setEnabled(m > 0)
            rb.setText(f"Redo ({m})" if m else "Redo")

    def remap_undo_fids(self, layer_id, fid_map):
        """Rewrite stack entries for `layer_id` after its features were
        renumbered (the scratch\u2192GeoPackage migration on first save assigns
        fresh sequential rowids). Without this, entries recorded before the
        save keep the memory-provider fids and a later undo deletes the WRONG
        feature \u2014 or nothing \u2014 from the migrated layer.

        A fid missing from `fid_map` means its feature no longer exists; that
        slot is cleared rather than left pointing at whichever feature now
        holds the number. Pass an empty map to safely drop all of a layer's
        fid-bearing entries (the flush fallback when no reliable mapping
        could be built).

        Identity sigs for remapped slots are recomputed from the migrated
        features (the WKB representation can change through the GeoPackage
        write, e.g. polygon promoted to multipolygon), so the sig check at
        undo time doesn't refuse a legitimately remapped entry."""
        remapped = []
        for entry in self._undo_stack:
            b, bf, p, pf, extra, pairs, sigs, rep = self._parse_undo_entry(entry)
            sigs = dict(sigs) if sigs else {}
            ex_sigs = list(sigs.get("extra") or [])
            ex_sigs += [None] * (len(extra) - len(ex_sigs))
            pr_sigs = list(sigs.get("pairs") or [])
            pr_sigs += [None] * (len(pairs) - len(pr_sigs))
            if b == layer_id:
                dead = bf is not None and bf not in fid_map
                bf = fid_map.get(bf) if bf is not None else None
                sigs["b"] = (self._feature_sig(layer_id, bf)
                             if bf is not None else None)
                new_extra, new_ex_sigs = [], []
                for x in extra:
                    if x in fid_map:
                        new_extra.append(fid_map[x])
                        new_ex_sigs.append(
                            self._feature_sig(layer_id, fid_map[x]))
                extra, ex_sigs = new_extra, new_ex_sigs
            else:
                dead = False
            if p == layer_id and pf is not None:
                pf = fid_map.get(pf)
                sigs["p"] = (self._feature_sig(layer_id, pf)
                             if pf is not None else None)
            new_pairs, new_pr_sigs = [], []
            for i, (lid, fid) in enumerate(pairs):
                if lid == layer_id and fid is not None:
                    if fid in fid_map:
                        new_pairs.append((lid, fid_map[fid]))
                        new_pr_sigs.append(
                            self._feature_sig(layer_id, fid_map[fid]))
                else:
                    new_pairs.append((lid, fid))
                    new_pr_sigs.append(pr_sigs[i])
            pairs, pr_sigs = new_pairs, new_pr_sigs
            # Remap cookie-cutter repair mods on the renumbered layer; a mod
            # whose fid vanished is dropped (its feature no longer exists).
            # readds carry no fids, so they pass through untouched.
            if rep:
                new_mods = []
                for m in (rep.get("mods") or []):
                    mlid, mfid = m[0], m[1]
                    if mlid == layer_id and mfid is not None:
                        if mfid not in fid_map:
                            continue
                        m = [mlid, fid_map[mfid], m[2], m[3],
                             self._feature_sig(layer_id, fid_map[mfid])]
                    new_mods.append(list(m))
                rep = {"mods": new_mods,
                       "readds": list(rep.get("readds") or [])}
                if not rep["mods"] and not rep["readds"]:
                    rep = None
            # If the primary baseline feature is gone and nothing else
            # remains to undo, drop the entry entirely.
            if dead and pf is None and not extra and not pairs and not rep:
                continue
            sigs["extra"], sigs["pairs"] = ex_sigs, pr_sigs
            remapped.append((b, bf, p, pf, extra, pairs, sigs, rep))
        self._undo_stack = remapped
        self._sync_undo_btn()

    def _do_global_undo(self):
        from qgis.core import QgsProject
        if not self._undo_stack:
            return

        entry = self._undo_stack[-1]
        (baseline_ref, baseline_fid, proposed_ref, proposed_fid,
         extra_fids, extra_pairs, sigs, repairs) = self._parse_undo_entry(entry)

        def _resolve(ref):
            """Return a live QgsVectorLayer from an id string or object, or None."""
            if ref is None:
                return None
            if isinstance(ref, str):
                return QgsProject.instance().mapLayer(ref)
            # Legacy object reference — verify it's still alive
            try:
                _ = ref.id()
                return ref
            except (RuntimeError, AttributeError):
                return None

        # Every (layer, fid, sig) this entry wants to remove, in delete order.
        _ex_sigs = list(sigs.get("extra") or [])
        _ex_sigs += [None] * (len(extra_fids) - len(_ex_sigs))
        _pr_sigs = list(sigs.get("pairs") or [])
        _pr_sigs += [None] * (len(extra_pairs) - len(_pr_sigs))
        slots = [(baseline_ref, baseline_fid, sigs.get("b")),
                 (proposed_ref, proposed_fid, sigs.get("p"))]
        slots += [(baseline_ref, xf, _ex_sigs[i])
                  for i, xf in enumerate(extra_fids)]
        slots += [(lid, fid, _pr_sigs[i])
                  for i, (lid, fid) in enumerate(extra_pairs)]

        # A target layer that is mid-edit with UNSAVED changes blocks the undo:
        # deleting through or around its edit buffer would have to either
        # commit or discard the user's pending work. Leave the entry on the
        # stack so pressing Undo again after the edit session ends still works.
        # (A clean open session — e.g. tree-placement mode between clicks — is
        # fine: the provider-level delete below leaves it running.)
        _guard_refs = [(_ref, _fid) for _ref, _fid, _sig in slots
                       if _fid is not None]
        # Cookie-cutter repairs touch neighbouring features on their own
        # layers — those layers must pass the same dirty-edit-session guard.
        if repairs:
            _guard_refs += [(m[0], m[1]) for m in (repairs.get("mods") or [])]
            _guard_refs += [((c or {}).get("lid"), -1)
                            for c in (repairs.get("readds") or [])]
        for _ref, _fid in _guard_refs:
            if _fid is None:
                continue
            _busy = _resolve(_ref)
            if _busy is not None and _busy.isEditable() and _busy.isModified():
                self.iface.messageBar().pushMessage(
                    "Biome",
                    f"Can't undo while “{_busy.name()}” has unsaved edits "
                    "— finish or cancel that edit session, then press Undo again.",
                    level=Qgis.MessageLevel.Warning, duration=5)
                return

        self._undo_stack.pop()
        self._sync_undo_btn()

        def _delete(ref, fid, sig=None):
            """Delete feature `fid` straight through the data provider — never
            via the layer's edit buffer. startEditing/commitChanges here had
            two side effects: an already-open edit session was committed and
            closed wholesale (breaking tree-placement mode, whose session must
            stay open between clicks), and commitChanges can re-fire
            featureAdded on some OGR backends, re-entering placement handlers.
            Never falls back to deleting an arbitrary 'last feature' — a wrong
            guess would silently destroy a feature the user wanted to keep. If
            the exact fid can't be removed, or the feature now at that fid is
            no longer the one that was recorded (sig mismatch — the fid was
            recycled by a later insert), leave the data untouched and
            report it."""
            if fid is None:
                return True   # nothing to delete for this slot (e.g. no twin)
            layer = _resolve(ref)
            if layer is None:
                return False  # layer gone (project closed) — silently skip
            try:
                feat = layer.getFeature(fid)
                if feat is None or not feat.isValid():
                    return False
                if sig and self._feature_sig(layer, fid) != sig:
                    return False
                ok = bool(layer.dataProvider().deleteFeatures([fid]))
            except Exception:
                return False
            if ok:
                layer.updateExtents()
                layer.triggerRepaint()
            return ok

        def _capture(ref, fid):
            """Snapshot a feature (layer id + WKT + attributes) just before
            deletion so _do_global_redo can re-add it. Fid-free — the provider
            assigns a fresh fid on re-add."""
            layer = _resolve(ref)
            if layer is None or fid is None:
                return None
            try:
                feat = layer.getFeature(fid)
                if feat is None or not feat.isValid():
                    return None
                geom = feat.geometry()
                attrs = {}
                for i, fld in enumerate(layer.fields()):
                    if fld.name().lower() == "fid":
                        continue
                    attrs[fld.name()] = feat.attribute(i)
                return {"lid": layer.id(),
                        "wkt": (geom.asWkt()
                                if geom is not None and not geom.isNull()
                                else None),
                        "attrs": attrs}
            except Exception:
                return None

        attempted = [(r, f, s) for r, f, s in slots if f is not None]
        removed_any = False
        redo_caps = []
        fixup_layers = []
        for _ref, _fid, _sig in attempted:
            cap = _capture(_ref, _fid)
            if _delete(_ref, _fid, _sig):
                removed_any = True
                if cap:
                    redo_caps.append(cap)
                lyr = _resolve(_ref)
                if lyr is not None:
                    fixup_layers.append(lyr)

        # ── Cookie-cutter repairs: put the punched neighbours back ────
        # mods get their pre-punch geometry restored (sig-checked so a
        # neighbour the user edited since is left alone); readds are
        # re-inserted whole. Redo captures record how to re-apply the punch.
        repaired_any = False
        redo_mods, redo_dels = [], []
        if repairs:
            from qgis.core import QgsGeometry, QgsFeature
            for m in (repairs.get("mods") or []):
                _m = list(m) + [None] * 5
                mlid, mfid, before_wkt, after_wkt, sig_after = _m[:5]
                lyr = _resolve(mlid)
                if lyr is None or mfid is None or not before_wkt:
                    continue
                try:
                    feat = lyr.getFeature(mfid)
                    if feat is None or not feat.isValid():
                        continue
                    if sig_after and self._feature_sig(lyr, mfid) != sig_after:
                        continue
                    g = QgsGeometry.fromWkt(before_wkt)
                    if g is None or g.isNull() or g.isEmpty():
                        continue
                    if lyr.dataProvider().changeGeometryValues({mfid: g}):
                        repaired_any = True
                        # sig recomputed AFTER the restore so redo can verify
                        # the feature is still in its restored state.
                        redo_mods.append([mlid, mfid, after_wkt,
                                          self._feature_sig(lyr, mfid)])
                        lyr.updateExtents()
                        lyr.triggerRepaint()
                        fixup_layers.append(lyr)
                except Exception:
                    continue
            for cap in (repairs.get("readds") or []):
                lyr = _resolve((cap or {}).get("lid"))
                if lyr is None:
                    continue
                try:
                    feat = QgsFeature(lyr.fields())
                    if cap.get("wkt"):
                        feat.setGeometry(QgsGeometry.fromWkt(cap["wkt"]))
                    for name, val in (cap.get("attrs") or {}).items():
                        idx = lyr.fields().lookupField(name)
                        if idx >= 0:
                            feat.setAttribute(idx, val)
                    ok, out = lyr.dataProvider().addFeatures([feat])
                    if ok and out:
                        repaired_any = True
                        nfid = out[0].id()
                        redo_dels.append([cap.get("lid"), nfid,
                                          self._feature_sig(lyr, nfid)])
                        lyr.updateExtents()
                        lyr.triggerRepaint()
                        fixup_layers.append(lyr)
                except Exception:
                    continue

        if redo_caps or redo_mods or redo_dels:
            self._redo_stack.append({"adds": redo_caps, "mods": redo_mods,
                                     "dels": redo_dels, "repairs": repairs})
            self._sync_undo_btn()

        self._post_undo_layer_fixups(fixup_layers)
        self.iface.mapCanvas().refresh()
        self._mark_summary_dirty()
        self._refresh_phase_screens()

        if self.header_label.text() == "Manage Features":
            self._open_manage_screen()

        if not attempted and not repaired_any:
            # An all-empty placeholder \u2014 a placement that landed entirely on
            # already-mapped ground and placed nothing. Say so instead of
            # silently eating the click.
            self.iface.messageBar().pushMessage(
                "Biome",
                "That action placed nothing, so there was nothing to remove "
                "\u2014 press Undo again for the feature before it.",
                level=Qgis.MessageLevel.Info, duration=4)
        elif not removed_any and not repaired_any:
            # Nothing could be removed: the feature was already deleted
            # elsewhere, its layer is gone, or the fid now belongs to a
            # different feature. Tell the user instead of leaving them
            # wondering why the map didn't change.
            self.iface.messageBar().pushMessage(
                "Biome",
                "Nothing to undo here \u2014 that feature was already removed "
                "or belongs to a project that's no longer open.",
                level=Qgis.MessageLevel.Info, duration=4)

    def _do_global_redo(self):
        """Re-add the placement most recently removed by _do_global_undo and
        put a rebuilt entry (fresh fids + sigs) back on the undo stack."""
        from qgis.core import QgsProject, QgsFeature, QgsGeometry
        if not getattr(self, "_redo_stack", None):
            return

        caps = self._redo_stack[-1]
        if isinstance(caps, dict):
            # Cookie-cutter-era capture: re-add the placement ("adds"),
            # re-apply the punch to neighbours ("mods"), re-delete the
            # neighbours undo brought back ("dels").
            adds = list(caps.get("adds") or [])
            r_mods = list(caps.get("mods") or [])
            r_dels = list(caps.get("dels") or [])
            entry_repairs = caps.get("repairs")
        else:   # legacy plain list of feature captures
            adds, r_mods, r_dels, entry_repairs = list(caps), [], [], None

        def _layer(lid):
            return QgsProject.instance().mapLayer(lid) if lid else None

        # Same dirty-edit-session guard as undo: adding under a session with
        # unsaved changes risks tangling with the user's pending work.
        _lids = [c.get("lid") for c in adds]
        _lids += [m[0] for m in r_mods] + [d[0] for d in r_dels]
        for _lid in _lids:
            lyr = _layer(_lid)
            if lyr is not None and lyr.isEditable() and lyr.isModified():
                self.iface.messageBar().pushMessage(
                    "Biome",
                    f"Can't redo while \u201c{lyr.name()}\u201d has unsaved edits "
                    "\u2014 finish or cancel that edit session, then press Redo again.",
                    level=Qgis.MessageLevel.Warning, duration=5)
                return

        self._redo_stack.pop()

        def _re_add(cap):
            """Re-insert a captured feature via the data provider.
            Returns (layer, new_fid) or None."""
            layer = _layer(cap.get("lid"))
            if layer is None:
                return None
            try:
                feat = QgsFeature(layer.fields())
                if cap.get("wkt"):
                    feat.setGeometry(QgsGeometry.fromWkt(cap["wkt"]))
                for name, val in (cap.get("attrs") or {}).items():
                    idx = layer.fields().lookupField(name)
                    if idx >= 0:
                        feat.setAttribute(idx, val)
                ok, out = layer.dataProvider().addFeatures([feat])
                if not ok or not out:
                    return None
                layer.updateExtents()
                layer.triggerRepaint()
                return (layer, out[0].id())
            except Exception:
                return None

        restored = [r for r in (_re_add(c) for c in adds) if r]
        fix_layers = [l for l, _ in restored]

        # \u2500\u2500 Re-apply the cookie-cutter punch to neighbours \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        repaired = False
        for m in r_mods:
            _m = list(m) + [None] * 4
            mlid, mfid, after_wkt, sig_restored = _m[:4]
            lyr = _layer(mlid)
            if lyr is None or mfid is None or not after_wkt:
                continue
            try:
                feat = lyr.getFeature(mfid)
                if feat is None or not feat.isValid():
                    continue
                if sig_restored and self._feature_sig(lyr, mfid) != sig_restored:
                    continue   # edited since the undo \u2014 leave it alone
                g = QgsGeometry.fromWkt(after_wkt)
                if g is None or g.isNull() or g.isEmpty():
                    continue
                if lyr.dataProvider().changeGeometryValues({mfid: g}):
                    repaired = True
                    lyr.updateExtents()
                    lyr.triggerRepaint()
                    fix_layers.append(lyr)
            except Exception:
                continue
        for d in r_dels:
            _d = list(d) + [None] * 3
            dlid, dfid, dsig = _d[:3]
            lyr = _layer(dlid)
            if lyr is None or dfid is None:
                continue
            try:
                feat = lyr.getFeature(dfid)
                if feat is None or not feat.isValid():
                    continue
                if dsig and self._feature_sig(lyr, dfid) != dsig:
                    continue
                if lyr.dataProvider().deleteFeatures([dfid]):
                    repaired = True
                    lyr.updateExtents()
                    lyr.triggerRepaint()
                    fix_layers.append(lyr)
            except Exception:
                continue

        # Rebuild the repairs slot with fresh post-punch sigs so the undo \u2194
        # redo cycle keeps working indefinitely.
        rebuilt_rep = None
        if entry_repairs:
            rb_mods = []
            for m in (entry_repairs.get("mods") or []):
                _m = list(m) + [None] * 5
                mlid, mfid, before_wkt, after_wkt, _s = _m[:5]
                rb_mods.append([mlid, mfid, before_wkt, after_wkt,
                                self._feature_sig(mlid, mfid)])
            rebuilt_rep = {"mods": rb_mods,
                           "readds": list(entry_repairs.get("readds") or [])}

        if restored:
            # Rebuild the matching undo entry \u2014 roles don't matter for
            # deletion, so the first feature is primary and the rest ride as
            # extra_pairs. Appended directly (NOT via push_undo) so this redo
            # doesn't wipe the rest of the redo stack.
            (l0, f0), rest = restored[0], restored[1:]
            sigs = {"b": self._feature_sig(l0, f0), "p": None, "extra": [],
                    "pairs": [self._feature_sig(l, f) for l, f in rest]}
            self._undo_stack.append(
                (l0.id(), f0, None, None, [],
                 [(l.id(), f) for l, f in rest], sigs, rebuilt_rep))
        elif repaired:
            # The placement itself couldn't come back but the punch was
            # re-applied \u2014 keep an entry so undo can still restore the
            # neighbours it changed.
            self._undo_stack.append(
                (None, None, None, None, [], [], {}, rebuilt_rep))

        self._sync_undo_btn()
        self._post_undo_layer_fixups(fix_layers)
        self.iface.mapCanvas().refresh()
        self._mark_summary_dirty()
        self._refresh_phase_screens()
        if self.header_label.text() == "Manage Features":
            self._open_manage_screen()

        if not restored and not repaired:
            self.iface.messageBar().pushMessage(
                "Biome",
                "Nothing to redo \u2014 the layer that feature belonged to is "
                "no longer available.",
                level=Qgis.MessageLevel.Info, duration=4)

    def _post_undo_layer_fixups(self, layers):
        """Layer-specific housekeeping that a provider-level delete/add skips.
        The Buildings (Map Only) legend bakes one renderer rule per building,
        so it must be rebuilt whenever undo/redo touches that layer; the B&T
        screen's count chips refresh too if that screen is built."""
        try:
            from .building_tree_mapper import (
                BUILDING_LAYER_NAME, rebuild_building_renderer)
        except Exception:
            return
        seen = set()
        touched_bt = False
        for layer in layers:
            try:
                if layer is None or layer.id() in seen:
                    continue
                seen.add(layer.id())
                if layer.name() == BUILDING_LAYER_NAME:
                    rebuild_building_renderer(layer)
                    touched_bt = True
                else:
                    from .building_tree_mapper import TREE_LAYER_NAME
                    if layer.name() == TREE_LAYER_NAME:
                        touched_bt = True
            except Exception:
                pass
        if touched_bt:
            try:
                self._bt_update_counts()
            except Exception:
                pass

    def drop_undo_entries_for_layer(self, layer):
        """Remove every undo entry and redo capture referencing `layer` \u2014
        called before a whole layer is deleted (B&T 'Clear all'), when each
        per-feature entry would otherwise become a click-burning dud."""
        try:
            lid = layer if isinstance(layer, str) else (
                layer.id() if layer is not None else None)
        except Exception:
            lid = None
        if lid is None:
            return
        kept = []
        for entry in self._undo_stack:
            b, bf, p, pf, extra, pairs, _sigs, _rep = self._parse_undo_entry(entry)
            lids = {b, p} | {l for l, _ in pairs}
            if _rep:
                lids |= {m[0] for m in (_rep.get("mods") or [])}
                lids |= {(c or {}).get("lid") for c in (_rep.get("readds") or [])}
            if lid in lids:
                continue
            kept.append(entry)

        def _redo_refs_layer(caps):
            if isinstance(caps, dict):
                if any((c or {}).get("lid") == lid
                       for c in (caps.get("adds") or [])):
                    return True
                if any(m[0] == lid for m in (caps.get("mods") or [])):
                    return True
                if any(d[0] == lid for d in (caps.get("dels") or [])):
                    return True
                rep = caps.get("repairs") or {}
                if any(m[0] == lid for m in (rep.get("mods") or [])):
                    return True
                return any((c or {}).get("lid") == lid
                           for c in (rep.get("readds") or []))
            return any(c.get("lid") == lid for c in caps)

        redo_kept = [caps for caps in (self._redo_stack or [])
                     if not _redo_refs_layer(caps)]
        if (len(kept) != len(self._undo_stack)
                or len(redo_kept) != len(self._redo_stack or [])):
            self._undo_stack = kept
            self._redo_stack = redo_kept
            self._sync_undo_btn()

    # ------------------------------------------------------------------ #
    #  UI                                                                  #
    # ------------------------------------------------------------------ #

    def _install_transform_dialog_suppressor(self):
        """Install an event filter that auto-accepts the 'Select CRS Transformation'
        prompt QGIS shows when loading layers, choosing the first (best) option.
        Does NOT suppress the Georeferencer's Transformation Settings dialog."""
        from qgis.PyQt.QtCore import QObject, QEvent, QTimer
        from qgis.PyQt.QtWidgets import QApplication, QDialog, QDialogButtonBox

        # Prefixes that indicate the CRS datum-selection prompt (suppress these).
        # QGIS appends context to the base title, e.g.
        # "Select Transformation for Search Buffer", so we match on prefix.
        _SUPPRESS_PREFIXES = (
            'Select Transformation',
            'Select CRS Transformation',
            'Datum Transformation',
        )

        # Titles that belong to the Georeferencer and must NOT be suppressed
        _GEOREF_TITLES = {
            'Transformation Settings',
            'Transformation settings',
        }

        class TransformDialogSuppressor(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.Show:
                    if isinstance(obj, QDialog):
                        title = obj.windowTitle()
                        # Suppress any CRS selection prompt (prefix match),
                        # but never touch the Georeferencer dialog
                        if (title not in _GEOREF_TITLES and
                                any(title.startswith(p) for p in _SUPPRESS_PREFIXES)):
                            QTimer.singleShot(0, obj.accept)
                return False

        self._transform_suppressor = TransformDialogSuppressor()
        QApplication.instance().installEventFilter(self._transform_suppressor)

    def _set_default_datum_transform(self):
        """Suppress the 'Select Transformation' dialog and pre-load OSTN15.

        Sets QGIS to always use the best available datum transform without
        asking, and explicitly registers OSTN15 (operation 7952) for all
        EPSG:27700 ↔ EPSG:3857/4326 pairs so property boundaries and other
        BNG data align accurately with satellite basemaps.

        IMPORTANT: proj.setTransformContext() must never be called while QGIS
        is inside QgsProject::readProjectFile.  It triggers
        transformContextChanged → projectCrsChanged → QgsMapLayer::crs() on a
        half-initialised layer → access violation on Windows (QGIS 3.44).
        All work that touches the transform context is therefore deferred to
        the next idle event-loop tick via QTimer.singleShot(0, ...).
        """
        from qgis.PyQt.QtCore import QTimer as _QTimer
        def _apply():
            try:
                from qgis.PyQt.QtCore import QSettings
                s = QSettings()
                s.setValue("/projections/unknownCrsBehavior", 0)
                s.setValue("/projections/defaultBehavior", "useDefault")
                s.setValue("/projections/promptWhenMultipleTransformsExist", False)
                s.setValue("projections/promptWhenMultipleTransformsExist", False)
                s.sync()  # force flush to registry before any CRS operations
                # The dialog's real gate is the App-SECTION QgsSettings key
                # ([app] projections\… in the profile ini; global default
                # TRUE) — the plain writes above never overrode it.
                from qgis.core import QgsSettings
                qs = QgsSettings()
                qs.setValue("projections/promptWhenMultipleTransformsExist",
                            False, section=QgsSettings.Section.App)
                qs.sync()

                from qgis.core import (
                    QgsCoordinateReferenceSystem,
                    QgsCoordinateTransformContext,
                    QgsProject,
                )
                proj = QgsProject.instance()
                ctx  = proj.transformContext()

                bng   = QgsCoordinateReferenceSystem("EPSG:27700")
                merc  = QgsCoordinateReferenceSystem("EPSG:3857")
                wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

                # Register the OSTN15 pipeline explicitly for every BNG pair.
                # NOTE: "OSTN15" appears only in the operation's proj string
                # (the EPSG *name* is "OSGB36 to ETRS89 (2)"), and the modern
                # PROJ-6 API is addCoordinateOperation with that proj string —
                # TransformDetails has no .id, and the deprecated
                # addSourceDestinationDatumTransform(int, int) form is a no-op
                # on current QGIS builds.
                try:
                    from qgis.core import QgsDatumTransform
                    for src, dst in [(bng, merc), (bng, wgs84), (merc, bng), (wgs84, bng)]:
                        ops = QgsDatumTransform.operations(src, dst)
                        chosen = next(
                            (op for op in ops if op.isAvailable and
                             ("OSTN15" in op.proj.upper()
                              or "OSTN15" in op.name.upper())),
                            None,
                        )
                        if chosen is None:
                            # Best available (list is accuracy-ordered)
                            chosen = next((op for op in ops if op.isAvailable),
                                          None)
                        if chosen is not None:
                            ctx.addCoordinateOperation(src, dst, chosen.proj)
                except Exception:
                    pass

                proj.setTransformContext(ctx)

            except Exception:
                pass

        _QTimer.singleShot(0, _apply)

    # ── Design tokens ─────────────────────────────────────────────────────
    _C_BG        = "#F7F8F6"   # page background
    _C_SURFACE   = "#FFFFFF"   # card surface
    _C_BORDER    = "#E3E7E1"   # subtle border
    _C_PRIMARY   = "#1F4A37"   # primary purple
    _C_PRIMARY_D = "#1F4A37"   # darker primary (hover)
    _C_PRIMARY_L = "#EAF2EC"   # light primary tint
    _C_ACCENT    = "#2E6B4F"   # accent violet
    _C_TEXT      = "#1A1A2E"   # primary text
    _C_TEXT_2    = "#54635A"   # secondary text
    _C_RED       = "#C62828"   # danger
    _C_RED_L     = "#FFEBEE"   # danger light
    _FONT        = "font-family: 'Inter Medium','Inter','Segoe UI',sans-serif;"

    def _reapply_theme(self):
        """Re-apply the dock stylesheet and tooltip palette in the current
        light/dark mode. Registered as a theme listener so an OS light/dark
        flip (QGIS 4) restyles the whole panel live; also re-tints the still
        banner and repaints. Safe to call any time."""
        # If this panel was destroyed (e.g. a plugin reload built a new one),
        # unregister so the stale listener doesn't fire on a dead widget.
        try:
            from qgis.PyQt import sip
            if sip.isdeleted(self):
                theme.remove_theme_listener(self._reapply_theme)
                return
        except Exception:
            pass
        import time as _t
        try:
            t0 = _t.perf_counter()
            self.setStyleSheet(theme.dock_qss())
            theme.style_tooltips()
            theme._tlog("  panel: dock_qss+tooltips %.1fms" % ((_t.perf_counter() - t0) * 1000))
        except Exception:
            pass
        # Remap (or restore) every already-built widget's baked-in palette hexes
        # for the new mode — the show-filter handles widgets built afterwards.
        try:
            from qgis.PyQt.QtWidgets import QWidget as _QW
            n = len(self.findChildren(_QW))
            t0 = _t.perf_counter()
            theme.restyle_widget_tree(self)
            theme._tlog("  panel: restyle_widget_tree %.1fms over %d widgets"
                        % ((_t.perf_counter() - t0) * 1000, n))
        except Exception:
            pass
        # Nudge the banner to repaint its still (scene-off) header in the new
        # palette; the living scene ignores the mode and keeps its day/night.
        try:
            if getattr(self, "_banner", None) is not None:
                self._banner.update()
        except Exception:
            pass

    def _build_ui(self):
        # TEMP diagnostic (dark-mode shakedown): record what the theme system
        # says at the moment the panel constructs its widgets.
        try:
            import datetime as _dtm
            _lp = os.path.join(os.path.dirname(__file__), "biome_live.log")
            with open(_lp, "a", encoding="utf-8") as _f:
                _f.write("%s PANEL BUILD: mode=%s SURFACE=%s INK=%s\n" % (
                    _dtm.datetime.now().strftime("%H:%M:%S"),
                    theme.current_mode(), theme.SURFACE, theme.INK))
        except Exception:
            pass
        # Theme: load bundled Inter, then style the whole dock
        theme._load_fonts()
        self.setStyleSheet(theme.dock_qss())
        theme.style_tooltips()   # opaque, readable tooltips even with dock-only styling
        # Restyle live when the OS (QGIS 4) flips light/dark. Reload-safe:
        # add_theme_listener de-dups, and the bound method compares equal.
        theme.add_theme_listener(self._reapply_theme)
        container   = QWidget()
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────
        # A painted living landscape (real sun/moon cycle + wildlife) that
        # carries the wordmark on the home screen; the label/back button
        # take over on sub-screens.
        from .banner_widget import LivingBanner, _H as _BANNER_H
        header = LivingBanner(latlon_provider=self._redline_centroid_wgs84)
        # No fixed height: the banner sizes to its rows (scene band + tab
        # strip) and paints the foreground grass down behind the tabs.
        self._banner = header
        header.on_dark_changed = self._apply_banner_chrome
        header.on_band_light_changed = self._style_tab_strip
        hl = QHBoxLayout()
        hl.setContentsMargins(10, 0, 12, 0)
        hl.setSpacing(8)

        self.back_btn = QPushButton(" Back")
        self.back_btn.setIcon(theme.icon("arrow-left", "#FFFFFF", 14))
        self.back_btn.setFixedHeight(28)
        self.back_btn.setMinimumWidth(64)
        self.back_btn.setStyleSheet(
            "QPushButton {"
            "  color: white; background: rgba(255,255,255,0.10);"
            "  border: 1px solid rgba(255,255,255,0.22); border-radius: 8px;"
            "  font-size: 11px; font-weight: 500; padding: 3px 10px;"
            "}"
            "QPushButton:hover { background: rgba(255,255,255,0.20); }"
        )
        self.back_btn.clicked.connect(self.go_back)
        self.back_btn.setVisible(False)

        # The wordmark is painted into the banner scene, so the label only
        # carries sub-screen titles (its text stays the navigation state —
        # go_back and the "Manage Features" check read it).
        self.header_label = QLabel("Biome")
        self.header_label.setStyleSheet(
            "color:white; font-size:14px; font-weight:500; letter-spacing:0.4px; font-family:'Inter Medium','Inter','Segoe UI',sans-serif;"
        )
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.header_label.setVisible(False)

        # Settings cog (top-right, by the banner) — OS Data Hub API key +
        # report-table copy/paste style.
        from qgis.PyQt.QtWidgets import QToolButton
        from qgis.PyQt.QtCore import QSize as _QSize
        self._settings_cog = QToolButton()
        self._settings_cog.setIcon(_svg_icon("settings", "#FFFFFF", 18))
        self._settings_cog.setIconSize(_QSize(18, 18))
        self._settings_cog.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_cog.setToolTip(
            "Biome settings — API key, report tables, map branding, Quick Habitats")
        self._settings_cog.setStyleSheet(
            "QToolButton{border:none;padding:4px;border-radius:6px;background:transparent;}"
            "QToolButton:hover{background:rgba(255,255,255,0.20);}")
        self._settings_cog.clicked.connect(self._open_settings_dialog)

        hl.addWidget(self.back_btn)
        hl.addWidget(self.header_label, 0)
        # Explicit stretch (not the label's) keeps the cog pinned right even
        # when the label is hidden on the home screen.
        hl.addStretch(1)
        hl.addWidget(self._settings_cog, 0, Qt.AlignmentFlag.AlignVCenter)

        # The chrome row (back/title/cog) stays pinned to the painted scene
        # band; the tab strip joins the banner's layout below it, so the
        # landscape extends down behind the tabs. Both rows need mouse
        # tracking so hover moves fall through to the banner underneath —
        # without it the hover-watch/click-to-bolt wildlife goes inert.
        _chrome = QWidget()
        _chrome.setMouseTracking(True)
        _chrome.setFixedHeight(_BANNER_H)
        _chrome.setLayout(hl)
        _hv = QVBoxLayout(header)
        _hv.setContentsMargins(0, 0, 0, 0)
        _hv.setSpacing(0)
        _hv.addWidget(_chrome)
        self._banner_vbox = _hv

        # The branded header IS the dock title bar — replaces the plain
        # native "Biome" strip. The dock can still be dragged by it.
        _title_w = QWidget()
        _tvl = QVBoxLayout(_title_w)
        _tvl.setContentsMargins(0, 0, 0, 0)
        _tvl.setSpacing(0)
        _tvl.addWidget(header)
        self.setTitleBarWidget(_title_w)

        # ── Tab bar (file-folder tabs — drag to reorder) ──────────────
        # A QTabBar (the pages themselves live in self._tab_stack) gives the
        # native file-tab shape plus drag-to-rearrange. Each tab carries its
        # logical page index in tabData, so reordering the strip never
        # desyncs it from the stack. Colours follow the plugin theme: the
        # active tab is chalk (theme.SURFACE) so it reads as pulled forward
        # into the content page; inactive tabs recede into the banner's
        # foreground grass, which paints on through behind them.
        from qgis.PyQt.QtWidgets import QTabBar
        self._tab_bar = QWidget()
        self._tab_bar.setMouseTracking(True)
        self._tab_bar.setStyleSheet("background: transparent;")
        tb_layout = QHBoxLayout()
        tb_layout.setContentsMargins(6, 5, 6, 0)   # no bottom margin: the
        tb_layout.setSpacing(0)                     # active tab meets the page

        self._tab_strip = QTabBar()
        self._tab_strip.setMovable(True)            # drag to rearrange
        self._tab_strip.setDrawBase(False)
        self._tab_strip.setExpanding(False)
        self._tab_strip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # ElideNone: Qt6 tab-size maths can come up a px short on fractional
        # DPI scaling, turning every label into "Map…" — never elide; a narrow
        # dock falls back to the scroll buttons enabled below instead.
        self._tab_strip.setElideMode(Qt.TextElideMode.ElideNone)
        # Don't let the strip's preferred width pin the dock wide; it elides
        # (and, at the extreme, scrolls) when the dock is narrowed.
        self._tab_strip.setUsesScrollButtons(True)
        self._tab_strip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        # Tab rects are sized from the WIDGET font while the labels render in
        # the stylesheet font; under Qt6 the two no longer agree, so every tab
        # came up a few px short and elided ("Map…"). Match them explicitly.
        _tab_font = QFont("Inter")
        _tab_font.setPixelSize(11)
        self._tab_strip.setFont(_tab_font)
        self._style_tab_strip(False)   # dark-grass styling to start; the
        # banner re-calls this whenever the ground behind the tabs flips
        # (snowy winter day ↔ grass) so the labels stay readable.

        from qgis.PyQt.QtCore import QSettings
        _show_enhance = QSettings().value(
            "biome/ui/show_enhance_tab", False, type=bool)
        # Double ampersand renders one literal '&' instead of creating a Qt mnemonic.
        _visible_tabs = [(0, "Map && Tools"), (1, "Desk Study"),
                         (2, "Dashboard"), (3, "Photos")]
        if _show_enhance:
            _visible_tabs.append((4, "Enhance"))
        for _logical, _label in _visible_tabs:
            _visual = self._tab_strip.addTab(_label)
            self._tab_strip.setTabData(_visual, _logical)
        self._current_logical_tab = 0
        # Connect AFTER the tabs exist (addTab on tab 0 fires currentChanged
        # before self._tab_stack is built) so the slot only runs on real use.
        self._tab_strip.currentChanged.connect(self._on_tab_strip_changed)

        tb_layout.addWidget(self._tab_strip, 0, Qt.AlignmentFlag.AlignBottom)
        tb_layout.addStretch()
        self._tab_bar.setLayout(tb_layout)
        # Into the banner, not the dock body: the strip rides on the painted
        # landscape, and hiding it on sub-screens shrinks the banner back to
        # the plain scene band.
        self._banner_vbox.addWidget(self._tab_bar)

        # ── Tab content stack ─────────────────────────────────────────
        self._tab_stack = QStackedWidget()
        # Ignore the pages' minimum/preferred widths when sizing the dock.
        # Each page is a scroll area with its horizontal scrollbar off, which
        # makes Qt bind the page's minimum width to its content — and the
        # stack takes the max over ALL tabs, locking the dock wide. With the
        # horizontal policy Ignored, the stack always follows the dock width
        # and the pages reflow/scroll instead.
        self._tab_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self._tab_stack, 1)

        # ── Dashboard tab content (geometry banner + dashboard) ───────
        summary_scroll = QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setFrameShape(QFrame.Shape.NoFrame)
        summary_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        summary_scroll.setStyleSheet("QScrollArea { background:#F7F8F6; }")
        summary_inner = QWidget()
        summary_inner.setStyleSheet("background:#F7F8F6;")
        # Never let a dashboard child's preferred width make the scroll-area
        # content wider than the dock (horizontal scrolling is intentionally
        # disabled). Cards must reflow within the viewport instead of being
        # painted and clipped beyond its right edge.
        summary_inner.setMinimumWidth(0)
        summary_inner.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Preferred)
        summary_layout = QVBoxLayout()
        summary_layout.setContentsMargins(10, 12, 10, 12)
        summary_layout.setSpacing(8)

        # Broken-geometry warning banner (hidden unless a parcel is invalid).
        self._summary_geo_holder = QWidget()
        _geo_h = QVBoxLayout(self._summary_geo_holder)
        _geo_h.setContentsMargins(0, 0, 0, 0)
        _geo_h.setSpacing(0)
        self._summary_geo_holder.setVisible(False)
        summary_layout.addWidget(self._summary_geo_holder)

        # One container: the persistent dashboard (hero + tiles + sections)
        # builds itself in here on first refresh and updates in place after.
        self._bng_container = QWidget()
        self._bng_container.setLayout(QVBoxLayout())
        self._bng_container.layout().setContentsMargins(0, 0, 0, 0)
        self._bng_container.layout().setSpacing(0)
        summary_layout.addWidget(self._bng_container)

        summary_layout.addStretch()
        summary_inner.setLayout(summary_layout)
        summary_scroll.setWidget(summary_inner)

        # ── Page stack (inside workflow tab) ─────────────────────────
        workflow_widget = QWidget()
        workflow_layout = QVBoxLayout()
        workflow_layout.setContentsMargins(0, 0, 0, 0)
        workflow_layout.setSpacing(0)

        self.stack = QStackedWidget()
        theme.install_fade(self.stack)
        workflow_layout.addWidget(self.stack, 1)

        # ── Persistent undo bar ───────────────────────────────────────
        # Visible on every workflow screen, so it's styled as a calm safety
        # net rather than a hazard — neutral, not the alarm red that's
        # reserved for genuinely destructive actions.
        undo_bar = QWidget()
        undo_bar.setStyleSheet("background:#F1F4F0; border-top:1px solid #DDE3DD;")
        ul = QHBoxLayout()
        ul.setContentsMargins(10, 6, 10, 6)
        ul.setSpacing(8)

        from qgis.PyQt.QtCore import QSize
        from qgis.PyQt.QtGui import QIcon

        _undo_qss = """
            QPushButton {
                background:#FFFFFF; color:#2A3D33;
                border:1px solid #C9D1C9; border-radius:7px;
                font-size:12px; font-weight:500; padding:0 14px 0 10px;
            }
            QPushButton:hover    { background:#EAF2EC; border-color:#9CB3A4; }
            QPushButton:pressed  { background:#DFE9E1; }
            QPushButton:disabled { background:#F1F4F0; color:#A6AEA5; border-color:#E3E7E1; }
        """

        def _bar_icon(name):
            icon = QIcon(_svg_pixmap(name, "#2A3D33", 16))
            icon.addPixmap(_svg_pixmap(name, "#A6AEA5", 16), QIcon.Mode.Disabled)
            return icon

        self._undo_btn = QPushButton("Undo")
        self._undo_btn.setIcon(_bar_icon("arrow-back-up"))
        self._undo_btn.setIconSize(QSize(16, 16))
        self._undo_btn.setMinimumHeight(32)
        self._undo_btn.setEnabled(False)
        self._undo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(self._undo_btn, "Remove the last placed feature from the map (Ctrl+Z)")
        self._undo_btn.setStyleSheet(_undo_qss)
        self._undo_btn.clicked.connect(self._do_global_undo)
        ul.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("Redo")
        self._redo_btn.setIcon(_bar_icon("arrow-forward-up"))
        self._redo_btn.setIconSize(QSize(16, 16))
        self._redo_btn.setMinimumHeight(32)
        self._redo_btn.setEnabled(False)
        self._redo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(self._redo_btn, "Put back the feature Undo just removed (Ctrl+Y)")
        self._redo_btn.setStyleSheet(_undo_qss)
        self._redo_btn.clicked.connect(self._do_global_redo)
        ul.addWidget(self._redo_btn)

        ul.addStretch(1)
        _undo_hint = QLabel("Ctrl+Z · Ctrl+Y")
        _undo_hint.setStyleSheet("color:#9AA39A; font-size:10px; background:transparent; border:none;")
        ul.addWidget(_undo_hint)
        undo_bar.setLayout(ul)
        workflow_layout.addWidget(undo_bar)

        # Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) anywhere in the panel trigger the
        # global undo / redo
        from qgis.PyQt.QtWidgets import QShortcut
        from qgis.PyQt.QtGui import QKeySequence
        self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self._undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._undo_shortcut.activated.connect(self._do_global_undo)
        self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
        self._redo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._redo_shortcut.activated.connect(self._do_global_redo)
        self._redo_shortcut2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        self._redo_shortcut2.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._redo_shortcut2.activated.connect(self._do_global_redo)

        workflow_widget.setLayout(workflow_layout)

        self._tab_stack.addWidget(workflow_widget)   # tab 0 — Workflow

        # ── Semi-lazy tabs ────────────────────────────────────────────
        # The Desk Study and Enhance tabs are the heavy builds, and neither
        # is the tab the panel opens on — so the panel appears as soon as
        # the Mapping tab is ready and these two build moments later on
        # short timers (staggered so each paints between builds). Clicking
        # one early, or any code path that needs its widgets, builds it
        # instantly via _ensure_tab_built. The timers beat every known
        # external toucher (the earliest, the post-open desk-stats refresh,
        # runs at +300–400ms).
        self._deferred_tabs = {1: self._build_desk_study_tab,
                               4: self._build_enhance_tab}
        self._tab_stack.addWidget(self._make_tab_placeholder())  # tab 1 — Desk Study

        self._tab_stack.addWidget(summary_scroll)    # tab 2 — Summary

        # Photos tab
        self._photos_container = QWidget()
        self._photos_container.setLayout(QVBoxLayout())
        self._photos_container.layout().setContentsMargins(0, 0, 0, 0)
        self._tab_stack.addWidget(self._photos_container)  # tab 3 — Photos

        self._tab_stack.addWidget(self._make_tab_placeholder())  # tab 4 — Enhance

        self._tab_stack.setCurrentIndex(0)
        # Use panel-owned timers rather than receiver-less lambdas. If the
        # panel is replaced while QGIS is loading/reloading a project, Qt will
        # destroy these timers with it instead of invoking stale Python code.
        self._desk_tab_build_timer = QTimer(self)
        self._desk_tab_build_timer.setSingleShot(True)
        self._desk_tab_build_timer.timeout.connect(self._build_deferred_desk_tab)
        self._desk_tab_build_timer.start(120)
        self._enhance_tab_build_timer = QTimer(self)
        self._enhance_tab_build_timer.setSingleShot(True)
        self._enhance_tab_build_timer.timeout.connect(self._build_deferred_enhance_tab)
        if _show_enhance:
            self._enhance_tab_build_timer.start(260)

        container.setLayout(root_layout)

        # ── Hint bar — shows button descriptions on hover ─────────────
        self._hint_bar = QLabel("")
        self._hint_bar.setFixedHeight(22)
        # CRITICAL: long hint texts (e.g. the OS Offline description) must not
        # set the dock's minimum width. Ignored = the label takes whatever
        # width the dock has; _wire_hint elides the text to fit.
        self._hint_bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._hint_bar.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._hint_bar.setStyleSheet(
            "background:#EAF2EC; color:#1F4A37; font-size:10px;"
            " padding:0 10px; border-top:1px solid #C9D1C9;"
        )
        root_layout.addWidget(self._hint_bar)

        self.setWidget(container)

        # ── Screens ───────────────────────────────────────────────────
        self._home_screen     = self._build_home_screen()
        self._baseline_screen = self._build_phase_screen("baseline")
        self._proposed_screen = self._build_phase_screen("proposed")

        self.stack.addWidget(self._home_screen)      # 0
        self.stack.addWidget(self._baseline_screen)  # 1
        self.stack.addWidget(self._proposed_screen)  # 2
        self.stack.setCurrentIndex(0)
        self._screen_history = []

        # Adopt any desk-study layers already in the project (reopened save
        # file, plugin reload): seed the layer caches so they are not
        # re-downloaded, and start each toggle button in the show/hide state
        # matching the canvas. Re-synced whenever another project is read.
        self._sync_desk_study_buttons_from_project()
        try:
            from qgis.core import QgsProject
            QgsProject.instance().readProject.connect(
                self._sync_desk_study_buttons_from_project)
            # visibilityChanged covers eye toggles, but deleting a layer emits
            # no visibility event. Re-read every desk-study control after the
            # project registry has removed one or more layers so individual and
            # Show/Hide All buttons cannot retain a stale "Hide" state.
            QgsProject.instance().layersRemoved.connect(
                self._sync_desk_study_buttons_from_project)
            # Keep the desk-study toggle buttons in step with the canvas no
            # matter who changes a layer's visibility — including the Biome
            # layer panel's eye toggles. visibilityChanged fires for any node
            # in the tree; the sync just re-reads the relevant layers, so it is
            # safe (and the buttons are clicked-, not toggled-, driven).
            QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
                self._sync_desk_study_buttons_from_project)
        except Exception:
            pass

    def _on_tab_strip_changed(self, visual_index):
        """QTabBar drag-reorders change a tab's position, not its page. Read
        the logical page index back from tabData (it travels with the tab on
        a move) and drive the content stack from that."""
        if visual_index < 0 or not hasattr(self, "_tab_stack"):
            return
        data = self._tab_strip.tabData(visual_index)
        logical = int(data) if data is not None else visual_index
        if logical == getattr(self, "_current_logical_tab", None):
            return   # a reorder left the same page selected — nothing to do
        self._switch_tab(logical)

    def _set_enhance_tab_visible(self, visible):
        """Show/hide the optional Enhance navigation tab without removing its
        page or any enhancement data. The preference is profile-wide."""
        from qgis.PyQt.QtCore import QSettings
        visible = bool(visible)
        QSettings().setValue("biome/ui/show_enhance_tab", visible)
        existing = -1
        for i in range(self._tab_strip.count()):
            if self._tab_strip.tabData(i) == 4:
                existing = i
                break
        if visible and existing < 0:
            i = self._tab_strip.addTab("Enhance")
            self._tab_strip.setTabData(i, 4)
            return
        if not visible and existing >= 0:
            if getattr(self, "_current_logical_tab", 0) == 4:
                self._switch_tab(0)
            self._tab_strip.removeTab(existing)

    def _build_deferred_desk_tab(self):
        self._ensure_tab_built(1)

    def _build_deferred_enhance_tab(self):
        self._ensure_tab_built(4)

    def _make_tab_placeholder(self):
        """Stand-in page for a semi-lazy tab — only ever visible if the user
        opens the tab inside the first fraction of a second."""
        w = QWidget()
        w.setStyleSheet("background:#F7F8F6;")
        lay = QVBoxLayout(w)
        lbl = QLabel("Loading…")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            "color:#9AA39A; font-size:12px; background:transparent;")
        lay.addWidget(lbl)
        return w

    def _build_enhance_tab(self):
        """Build the self-contained Enhancements tab (semi-lazy, tab 4)."""
        from .enhancements_tab import EnhancementsTab
        self._enhance_tab = EnhancementsTab(self)
        return self._enhance_tab

    def _ensure_tab_built(self, index):
        """Build a semi-lazy tab and swap it over its placeholder. Idempotent
        and instant when already built; called from the post-init timers, tab
        switches, and any code path that needs the tab's widgets early."""
        builder = getattr(self, "_deferred_tabs", {}).pop(index, None)
        if builder is None:
            return
        from qgis.PyQt import sip
        if sip.isdeleted(self):
            return
        import time as _t
        t0 = _t.perf_counter()
        try:
            real = builder()
        except Exception:
            self._deferred_tabs[index] = builder   # allow a retry
            raise
        placeholder = self._tab_stack.widget(index)
        showing = self._tab_stack.currentIndex() == index
        self._tab_stack.removeWidget(placeholder)
        placeholder.setParent(None)   # deleteLater alone leaves it painted
        placeholder.deleteLater()
        self._tab_stack.insertWidget(index, real)
        if showing:
            self._tab_stack.setCurrentIndex(index)
        if index == 1:
            # Catch up on the seeding/sync that was skipped while the desk
            # buttons didn't exist (reopened saves, early visibility flips).
            try:
                self._sync_desk_study_buttons_from_project()
            except Exception:
                pass
        try:
            from .bng_plugin import _slog
            _slog("startup: tab %d built semi-lazily (%.2fs)"
                  % (index, _t.perf_counter() - t0))
        except Exception:
            pass

    def _disarm_stranded_tools(self):
        """Finish/cancel any armed Biome map tool before navigation hides its
        owning screen. Without this the tool stays live on the canvas and the
        next click places/moves/deletes features for a screen the user has
        left (armed tree tool + tab switch → invisible tree per click, etc.).
        Mirrors what go_back does for the Mapping stack; every branch is a
        guarded no-op when its tool isn't armed."""
        # Habitat form drawing session (tree / continuous shape / single draw).
        try:
            from .habitat_form import HabitatFormScreen
            current = self.stack.currentWidget()
            if isinstance(current, HabitatFormScreen):
                if getattr(current, "_tree_mode", False):
                    current._finish_tree_session()
                elif getattr(current, "_shape_continuous", False):
                    current._finish_shape_session()
                else:
                    current.cancel_pending_draw()
        except Exception:
            pass
        # Building & Tree Mapper: a checked place button = an armed tool
        # (freehand building, shape stamp, or tree placer).
        try:
            if any(btn is not None and btn.isChecked()
                   for btn in (getattr(self, "_bt_build_btn", None),
                               getattr(self, "_bt_tree_btn", None),
                               getattr(self, "_bt_curt_btn", None))):
                self._bt_finish()
        except Exception:
            pass
        # Manage screen's click-to-move.
        try:
            self._abort_move_tool()
        except Exception:
            pass
        # Pond placement (Desk Study).
        try:
            tool = getattr(self, "_pond_tool", None)
            if tool is not None and self.iface.mapCanvas().mapTool() is tool:
                self._finish_pond_placing()
        except Exception:
            pass
        # Hedge/watercourse trimmer (Manage tab).
        try:
            self._finish_hedge_trimming()
        except Exception:
            pass

    def hideEvent(self, event):
        """Hiding the whole dock (close button, panel toggle) must also stop
        any armed placement/move tool — there is no visible UI left to stop
        it from. Spontaneous hides (QGIS window minimised) are skipped: the
        canvas is unreachable while minimised and the tool should still be
        armed on restore."""
        try:
            if not event.spontaneous():
                self._disarm_stranded_tools()
        except Exception:
            pass
        super().hideEvent(event)

    def _switch_tab(self, index):
        """Show a content page by logical index (0 Map & Tools … 4 Enhance) and
        run that tab's on-open refresh. The selected-tab look is owned by the
        QTabBar stylesheet, so this no longer restyles anything."""
        # Leaving a tab must never strand an armed map tool on the canvas.
        if index != getattr(self, "_current_logical_tab", None):
            self._disarm_stranded_tools()
        self._ensure_tab_built(index)
        self._current_logical_tab = index
        # Mirror the selection onto the strip so a programmatic switch also
        # moves the highlighted tab. No recursion: _on_tab_strip_changed
        # early-returns because _current_logical_tab already equals index. A
        # click lands on the matching tab, so setCurrentIndex is a no-op there.
        for _v in range(self._tab_strip.count()):
            if self._tab_strip.tabData(_v) == index:
                self._tab_strip.setCurrentIndex(_v)
                break
        self._tab_stack.setCurrentIndex(index)
        if index == 1:
            self._refresh_desk_study_stats()
        if index == 2 and getattr(self, "_summary_dirty", True):
            self._refresh_summary_now()
        if index == 3:
            self._refresh_photos_tab()
        if index == 4 and hasattr(self, "_enhance_tab"):
            # Recompute from the CURRENT habitats each time the tab is opened
            # (it does not refresh live as habitats are placed/deleted).
            self._invalidate_enh_cache()
            self._enhance_tab.refresh_onsite()

    def _build_home_screen(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # As-needed (not AlwaysOff): the cards reflow to fit a narrow dock, but
        # if the window is squeezed below what the content can shrink to, scroll
        # rather than silently clip the right edge of every card off-screen.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { background:#F7F8F6; }")

        screen = QWidget()
        screen.setStyleSheet("background:#F7F8F6;")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        # ── shared card/button helpers ─────────────────────────────────
        # #id-scoped so the card border doesn't cascade onto its child rows,
        # headers and labels (a bare border bleeds into every descendant QWidget).
        CARD_STYLE = (
            "QWidget#wfCard { background:white; border-radius:10px;"
            " border:1px solid #C6D2C8; }"
        )
        ROW_BTN_STYLE = (
            "QPushButton { background:transparent; border:none; border-radius:8px;"
            " text-align:left; padding:0 8px; color:#1A2420; font-size:12px; font-weight:500; }"
            "QPushButton:hover { background:#EAF2EC; }"
        )
        GO_BTN_STYLE = (
            "QPushButton { background:#1F4A37; color:white; border:none;"
            " border-radius:7px; font-size:11px; font-weight:500; padding:0 12px; }"
            "QPushButton:hover { background:#2E6B4F; }"
        )

        def _card():
            w = QWidget()
            w.setObjectName("wfCard")
            w.setStyleSheet(CARD_STYLE)
            return w, QVBoxLayout()

        def _card_header(text, dot_color):
            wrapper = QWidget()
            wrapper.setStyleSheet("background:transparent;")
            wl = QVBoxLayout()
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(0)
            h = QFrame()
            h.setObjectName("cardHdr")
            # #id-scoped so the rounded background doesn't cascade to the
            # dot/label. Top corners rounded to nest in the card's rounded top;
            # bottom square where it meets the white card body.
            h.setStyleSheet(
                "QFrame#cardHdr { background:#D3E0D7; border:none;"
                " border-top-left-radius:9px; border-top-right-radius:9px;"
                " border-bottom-left-radius:0; border-bottom-right-radius:0; }")
            hl = QHBoxLayout()
            hl.setContentsMargins(10, 6, 10, 6)
            hl.setSpacing(6)
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet("background:transparent; border:none;")
            dot.setPixmap(_dot_pixmap(dot_color, 9))
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color:#2C3A33; font-size:11px; font-weight:600;"
                " letter-spacing:1.2px; background:transparent; border:none;"
                " font-family:'Inter SemiBold','Inter Medium','Inter','Segoe UI',sans-serif;"
            )
            hl.addWidget(dot)
            hl.addWidget(lbl)
            hl.addStretch()
            h.setLayout(hl)
            wl.addWidget(h)
            wrapper.setLayout(wl)
            return wrapper

        def _row_btn(icon_name, text, callback, height=38, tooltip="",
                     icon_color="#1F4A37"):
            btn = QPushButton()
            btn.setMinimumHeight(height)
            btn.setStyleSheet(ROW_BTN_STYLE)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
            if tooltip:
                self._wire_hint(btn, tooltip)
            # Line-icon + label + chevron (all child widgets pass clicks through)
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(20, 20)
            icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            icon_lbl.setStyleSheet("background:transparent; border:none;")
            icon_lbl.setPixmap(_svg_pixmap(icon_name, icon_color, 18))
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text_lbl = QLabel(text)
            text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            text_lbl.setStyleSheet(
                "font-size:12px; color:#1A2420; font-weight:500;"
                " font-family:%s; background:transparent; border:none;" % theme.FONT_MEDIUM
            )
            chevron = QLabel()
            chevron.setFixedSize(16, 16)
            chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            chevron.setStyleSheet("background:transparent; border:none;")
            chevron.setPixmap(_svg_pixmap("chevron-right", "#B7BFB9", 16))
            row = QHBoxLayout()
            row.setContentsMargins(4, 0, 6, 0)
            row.setSpacing(9)
            row.addWidget(icon_lbl)
            row.addWidget(text_lbl, 1)
            row.addWidget(chevron)
            btn.setLayout(row)
            btn.clicked.connect(callback)
            return btn

        def _divider_line():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.HLine)
            f.setFixedHeight(1)
            f.setStyleSheet("background:#EAF2EC; border:none; margin:0 8px;")
            return f

        # ── NAVIGATE card ──────────────────────────────────────────────
        nav_card, _ = _card()
        nav_vl = QVBoxLayout()
        nav_vl.setContentsMargins(0, 0, 0, 0)
        nav_vl.setSpacing(0)
        nav_vl.addWidget(_card_header("NAVIGATE", "#2E6B4F"))

        # grid ref row
        nav_body = QWidget()
        nav_body.setStyleSheet("background:transparent;")
        nb = QVBoxLayout()
        nb.setContentsMargins(8, 4, 8, 6)
        nb.setSpacing(0)

        def _search_row(placeholder, ref_attr, connect_fn, go_tooltip=""):
            r = QHBoxLayout()
            r.setSpacing(4)
            inp = QLineEdit()
            inp.setPlaceholderText(placeholder)
            inp.setFixedHeight(30)
            inp.setStyleSheet(
                "QLineEdit { border:1px solid #C9D1C9; border-radius:7px;"
                " padding:0 9px; font-size:11px; background:white; color:#1A1A2E; }"
                "QLineEdit:focus { border-color:#2E6B4F; border-width:1.5px; }"
            )
            inp.returnPressed.connect(connect_fn)
            setattr(self, ref_attr, inp)
            go = QPushButton("Go")
            go.setFixedSize(36, 30)
            go.setStyleSheet(GO_BTN_STYLE)
            go.clicked.connect(connect_fn)
            if go_tooltip:
                self._wire_hint(go, go_tooltip)
            r.addWidget(inp, 1)
            r.addWidget(go)
            return r

        # ── Smart search: grid refs and addresses share one box ──────
        sr = QHBoxLayout()
        sr.setSpacing(6)
        smart_inp = QLineEdit()
        smart_inp.setPlaceholderText("Grid ref, address or postcode…")
        smart_inp.setFixedHeight(32)
        smart_inp.setStyleSheet(
            "QLineEdit { border:1px solid #C9D1C9; border-radius:7px;"
            " padding:0 10px; font-size:11px; background:white; color:#1A1A2E; }"
            "QLineEdit:focus { border-color:#2E6B4F; border-width:1.5px; }"
        )
        smart_inp.returnPressed.connect(self._smart_search)
        smart_inp.textEdited.connect(self._on_search_text_edited)

        # Debounced live suggestions while typing. Photon is used for these —
        # it's built for as-you-type lookups, whereas Nominatim's usage
        # policy forbids client-side autocomplete.
        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(400)
        self._suggest_timer.timeout.connect(self._run_live_suggest)

        # Both legacy handlers read from these attributes — point them at the
        # single shared input.
        self.grid_ref_input = smart_inp
        self.addr_input     = smart_inp
        go = QPushButton("→")
        go.setFixedSize(44, 32)
        go.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        go.setStyleSheet(
            "QPushButton { background:#1F4A37; color:white; border:none;"
            " border-radius:7px; font-size:16px; font-weight:500; padding:0; }"
            "QPushButton:hover { background:#2E6B4F; }"
        )
        go.clicked.connect(self._smart_search)
        self._wire_hint(go, "Zoom to a grid reference (e.g. SU 12345 67890), "
                            "address or postcode")
        sr.addWidget(smart_inp, 1)
        sr.addWidget(go)
        nb.addLayout(sr)
        self.grid_ref_status = QLabel("")
        self.grid_ref_status.setStyleSheet("color:#B3372F; font-size:10px;")
        self.grid_ref_status.setWordWrap(True)
        self.grid_ref_status.setVisible(False)
        nb.addWidget(self.grid_ref_status)

        self.addr_status = QLabel("")
        self.addr_status.setStyleSheet("color:#B3372F; font-size:10px;")
        self.addr_status.setWordWrap(True)
        self.addr_status.setVisible(False)
        nb.addWidget(self.addr_status)

        # Results picker — shown when a search returns several matches.
        # Stays visible after a pick so the user can try the other
        # candidates if the first one wasn't the right place.
        self.search_results_list = QListWidget()
        self.search_results_list.setVisible(False)
        self.search_results_list.setWordWrap(True)
        self.search_results_list.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.search_results_list.setStyleSheet(
            "QListWidget { border:1px solid #C9D1C9; border-radius:7px;"
            " background:white; font-size:11px; color:#1A1A2E; }"
            "QListWidget::item { padding:4px 8px; }"
            "QListWidget::item:hover { background:#EAF1EC; }"
            "QListWidget::item:selected { background:#2E6B4F; color:white; }"
        )
        self.search_results_list.itemClicked.connect(self._on_search_result_clicked)
        nb.addWidget(self.search_results_list)
        self._install_search_dismiss_filter()

        # ── Segmented basemap selector ────────────────────────────────
        nb.addSpacing(2)
        nb.addWidget(self._make_basemap_bar())
        self._sync_basemap_buttons()

        # ── Zoom shortcuts — side by side ─────────────────────────────
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(6)
        zoom_rl_btn = QPushButton("Zoom to Redline")
        zoom_rl_btn.setFixedHeight(28)
        zoom_rl_btn.setMinimumWidth(40)
        zoom_rl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(zoom_rl_btn, "Recenter and zoom the map to fit the Red Line Boundary")
        zoom_rl_btn.setStyleSheet("""
            QPushButton {
                background:white; color:#B3372F;
                border:1px solid #B3372F; border-radius:8px;
                font-size:11px; font-weight:500; padding:0 8px;
                font-family:'Inter Medium','Inter','Segoe UI',sans-serif;
            }
            QPushButton:hover { background:#FBEDEB; }
            QPushButton:pressed { background:#F4CFCB; }
        """)
        zoom_rl_btn.clicked.connect(self._zoom_to_redline)
        zoom_row.addWidget(zoom_rl_btn, 1)

        zoom_sb_btn = QPushButton("Zoom to Blue Line")
        zoom_sb_btn.setFixedHeight(28)
        zoom_sb_btn.setMinimumWidth(40)
        zoom_sb_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(zoom_sb_btn, "Recenter and zoom the map to fit the Blue Line Boundary")
        zoom_sb_btn.setStyleSheet("""
            QPushButton {
                background:white; color:#2F5D8A;
                border:1px solid #2F5D8A; border-radius:8px;
                font-size:11px; font-weight:500; padding:0 8px;
                font-family:'Inter Medium','Inter','Segoe UI',sans-serif;
            }
            QPushButton:hover { background:#EBF2F8; }
            QPushButton:pressed { background:#D7E5F0; }
        """)
        zoom_sb_btn.clicked.connect(self._zoom_to_site_boundary)
        zoom_row.addWidget(zoom_sb_btn, 1)
        nb.addSpacing(2)
        nb.addLayout(zoom_row)

        nav_body.setLayout(nb)
        nav_vl.addWidget(nav_body)
        nav_card.setLayout(nav_vl)
        layout.addWidget(nav_card)

        # ── PROJECT card ───────────────────────────────────────────────
        proj_card, _ = _card()
        proj_vl = QVBoxLayout()
        proj_vl.setContentsMargins(0, 0, 0, 0)
        proj_vl.setSpacing(0)
        proj_vl.addWidget(_card_header("PROJECT", "#B3372F"))
        pb = QWidget()
        pb.setStyleSheet("background:transparent;")
        pbl = QVBoxLayout()
        pbl.setContentsMargins(6, 4, 6, 6)
        pbl.setSpacing(2)
        pbl.addWidget(_row_btn("map-pin", "Add Red Line Boundary", self._add_redline_boundary,
                               icon_color="#B3372F",
                               tooltip="Draw the site boundary polygon on the map"))
        pbl.addWidget(_row_btn("polygon", "Add Blue Line Boundary", self._add_site_boundary,
                               icon_color="#185FA5",
                               tooltip="Draw the wider ownership/property boundary (blue line). Use when the applicant owns land beyond the redline application site."))
        pbl.addWidget(_divider_line())
        # Georeferencer button + opacity slider inline
        georef_row = QWidget()
        georef_row.setObjectName("georefRow")
        georef_row.setStyleSheet(
            "QWidget#georefRow { background: white; border-radius: 6px; }"
        )
        georef_row.setMinimumHeight(40)
        georef_hl = QHBoxLayout(georef_row)
        georef_hl.setContentsMargins(8, 5, 8, 5)
        georef_hl.setSpacing(6)

        # Icon + label as a clickable unit only
        georef_icon = QLabel()
        georef_icon.setFixedSize(20, 20)
        georef_icon.setStyleSheet("background:transparent; border:none;")
        georef_icon.setPixmap(_svg_pixmap("world", "#1F4A37", 18))
        georef_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        georef_icon.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(georef_icon, "Align an architect's plan or other image with the mapped site")
        georef_icon.mousePressEvent = lambda e: self._open_georeferencer()
        georef_hl.addWidget(georef_icon)

        georef_lbl = QLabel("Georeferencer")
        georef_lbl.setStyleSheet(
            "font-size:12px; color:#1A2420; font-weight:500; font-family:%s;"
            " background:transparent; border:none;" % theme.FONT_MEDIUM
        )
        georef_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(georef_lbl, "Align an architect's plan or other image with the mapped site")
        georef_lbl.mousePressEvent = lambda e: self._open_georeferencer()
        georef_hl.addWidget(georef_lbl)

        # Match the action affordance used by the two boundary buttons above:
        # the chevron makes it clear that Georeferencer opens another tool.
        georef_arrow = QLabel()
        georef_arrow.setFixedSize(16, 16)
        georef_arrow.setStyleSheet("background:transparent; border:none;")
        georef_arrow.setPixmap(_svg_pixmap("chevron-right", "#B7BFB9", 16))
        georef_arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        georef_arrow.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(
            georef_arrow,
            "Align an architect's plan or other image with the mapped site")
        georef_arrow.mousePressEvent = lambda e: self._open_georeferencer()
        georef_hl.addWidget(georef_arrow)

        # Separate the clickable action from the independent opacity control.
        georef_separator = QFrame()
        georef_separator.setFrameShape(QFrame.Shape.VLine)
        georef_separator.setFixedSize(1, 22)
        georef_separator.setStyleSheet(
            "background:#D8E0DA; border:none; margin:0px;")
        georef_hl.addSpacing(2)
        georef_hl.addWidget(georef_separator)
        georef_hl.addSpacing(2)

        # Opacity slider — wider and taller handle so it's easy to grab
        self._georef_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._georef_opacity_slider.setMinimum(10)
        self._georef_opacity_slider.setMaximum(100)
        self._georef_opacity_slider.setValue(100)
        # Was setFixedWidth(120): that hard width pinned this whole row ~326px
        # wide, and with the page's horizontal scrollbar disabled it clipped the
        # right of every card (go button, tabs, basemap) off on a narrow dock.
        # Let the slider flex — fill spare width, shrink to 56px so it goes slim.
        self._georef_opacity_slider.setMinimumWidth(56)
        self._georef_opacity_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._georef_opacity_slider.setFixedHeight(30)
        self._wire_hint(self._georef_opacity_slider, "Adjust georeferenced raster opacity")
        self._georef_opacity_slider.setStyleSheet("""
            QSlider { padding: 4px 0; }
            QSlider::groove:horizontal {
                height:6px; background:#D7E5F0; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#2F5D8A; border:none;
                width:16px; height:16px; margin:-5px 0;
                border-radius:8px;
            }
            QSlider::handle:horizontal:hover { background:#1976d2; }
            QSlider::sub-page:horizontal { background:#2F5D8A; border-radius:3px; }
        """)
        self._georef_opacity_slider.valueChanged.connect(self._on_georef_opacity_changed)
        georef_hl.addWidget(self._georef_opacity_slider)

        self._georef_opacity_val_lbl = QLabel("100%")
        self._georef_opacity_val_lbl.setStyleSheet(
            "font-size:10px; color:#54635A; min-width:32px; background:transparent; border:none;"
        )
        georef_hl.addWidget(self._georef_opacity_val_lbl)

        pbl.addWidget(georef_row)
        pb.setLayout(pbl)
        proj_vl.addWidget(pb)
        proj_card.setLayout(proj_vl)
        layout.addWidget(proj_card)

        # ── HABITAT MAPPING card ───────────────────────────────────────
        hab_card, _ = _card()
        hab_vl = QVBoxLayout()
        hab_vl.setContentsMargins(0, 0, 0, 0)
        hab_vl.setSpacing(0)
        hab_vl.addWidget(_card_header("HABITAT MAPPING", "#1F7A4D"))
        hb = QWidget()
        hb.setStyleSheet("background:transparent;")
        hbl = QVBoxLayout()
        hbl.setContentsMargins(6, 4, 6, 4)
        hbl.setSpacing(2)
        hbl.addWidget(_row_btn(
            "leaf", "Baseline Habitats",
            lambda: self._open_phase_screen("baseline", "Baseline Habitats"),
            tooltip="Draw and record baseline habitat polygons, lines and trees"
        ))
        hbl.addWidget(_divider_line())
        hbl.addWidget(_row_btn(
            "plant-2", "Proposed Habitats",
            lambda: self._open_phase_screen("proposed", "Proposed Habitats"),
            tooltip="Draw and record proposed habitat polygons, lines and trees"
        ))
        hbl.addWidget(_divider_line())

        # Proposed-together toggle — the WHOLE row is the click target, so it
        # behaves like the other menu rows instead of a fiddly side switch.
        tgl_row = QWidget()
        tgl_row.setObjectName("linkrow")
        tgl_row.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        tgl_row.setStyleSheet(
            "QWidget#linkrow { background:#EAF2EC; border:1px solid #2E6B4F;"
            " border-radius:8px; }"
            "QWidget#linkrow:hover { background:#DCEBDF; border-color:#1F7A4D; }")
        tgl_l = QHBoxLayout(tgl_row)
        tgl_l.setContentsMargins(12, 9, 12, 9)
        tgl_l.setSpacing(8)
        tgl_ico = QLabel()
        tgl_ico.setFixedSize(16, 16)
        tgl_ico.setStyleSheet("background:transparent; border:none;")
        tgl_ico.setPixmap(_svg_pixmap("git-compare", "#1F4A37", 15))
        tgl_l.addWidget(tgl_ico)
        tgl_lbl = QLabel("Create Baseline + Proposed")
        tgl_lbl.setStyleSheet(
            "color:#1F4A37; font-size:11px; font-weight:500; font-family:%s;"
            " background:transparent; border:none;" % theme.FONT_MEDIUM)
        # Wrap instead of forcing a ~309px-wide row (which clipped cards off the
        # right on a narrow dock) — the label folds to two lines when it must.
        tgl_lbl.setWordWrap(True)
        tgl_l.addWidget(tgl_lbl)
        tgl_l.addStretch()
        self._proposed_toggle_btn = QPushButton()
        self._proposed_toggle_btn.setFixedSize(56, 26)
        self._proposed_toggle_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._wire_hint(self._proposed_toggle_btn,
            "When ON, drawing a baseline habitat automatically creates "
            "a matching proposed habitat in the same location")
        self._proposed_toggle_btn.clicked.connect(self._toggle_proposed_mapping)
        tgl_l.addWidget(self._proposed_toggle_btn)
        # Clicking anywhere on the row toggles the mode — not just the pill.
        # (QLabels ignore mouse events, so presses bubble up to the row; the
        # pill consumes its own clicks and routes through the same handler.)
        tgl_row.mousePressEvent = lambda _e: self._toggle_proposed_mapping()
        self._wire_hint(tgl_row,
            "When ON, drawing a baseline habitat automatically creates "
            "a matching proposed habitat in the same location")
        self._update_proposed_toggle_style()
        hbl.addWidget(tgl_row)



        hbl.addWidget(_divider_line())
        hbl.addWidget(_row_btn("map-2", "Feature Mapper", self._open_ecological_features,
                               tooltip="Place ecological features on the map — ponds, PRF, fencing and more"))
        hbl.addWidget(_divider_line())
        hbl.addWidget(_row_btn("building", "Site Plan Visualiser", self._open_building_tree_mapper,
                               tooltip="Map buildings and trees for illustration only — "
                                       "these are NOT counted in any BNG calculation or summary"))
        hb.setLayout(hbl)
        hab_vl.addWidget(hb)
        hab_card.setLayout(hab_vl)
        layout.addWidget(hab_card)

        # ── TOOLS card ─────────────────────────────────────────────────
        tools_card, _ = _card()
        tools_vl = QVBoxLayout()
        tools_vl.setContentsMargins(0, 0, 0, 0)
        tools_vl.setSpacing(0)
        tools_vl.addWidget(_card_header("TOOLS", "#1F4A37"))
        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QGridLayout()
        tbl.setContentsMargins(6, 6, 6, 6)
        tbl.setSpacing(4)

        TOOL_BTN_STYLE = theme.btn_secondary()
        tools = [
            ("layers",     "Manage",        self._open_manage_screen,   "View, edit, reshape and delete placed features"),
            ("ruler",      "Measurements",  self._open_measurements,    "Measure distances and areas on the map"),
            ("map",        "Map Builder",   self._create_final_map,     "Create a print-ready survey or custom map"),
            ("table",      "Export Table",  self._export_table,          "Export all habitat data to an Excel reference table"),
            ("file",       "Blank Metric",  self._save_blank_metric,     "Save a blank copy of the statutory BNG metric"),
            ("file-chart", "Filled Metric", self._export_filled_metric,  "Export the statutory BNG metric pre-filled with your mapped habitats"),
            ("camera",     "Plan Annotator",  self._open_plan_annotator, "Place enhancement symbols on an imported elevation or plan and export it for a report"),
            ("device-floppy", "Save Project", self._save_project,        "Save the current QGIS project"),
        ]
        from qgis.PyQt.QtCore import QSize as _QSize
        for i, (icon_name, label, cb, tip) in enumerate(tools):
            btn = QPushButton(" " + label)
            btn.setIcon(theme.icon(icon_name, theme.ACCENT_DARK, 16))
            btn.setIconSize(_QSize(16, 16))
            btn.setStyleSheet(TOOL_BTN_STYLE)
            btn.setMinimumHeight(34)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self._wire_hint(btn, tip)
            btn.clicked.connect(cb)
            tbl.addWidget(btn, i // 2, i % 2)

        tb.setLayout(tbl)
        tools_vl.addWidget(tb)
        tools_card.setLayout(tools_vl)
        layout.addWidget(tools_card)

        # ── Footer ─────────────────────────────────────────────────────
        footer_w = QWidget()
        footer_w.setStyleSheet("background:transparent;")
        fl = QVBoxLayout()
        fl.setContentsMargins(0, 6, 0, 4)
        fl.setSpacing(4)

        logo_path = os.path.join(os.path.dirname(__file__), "biome_logo.png")
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if os.path.isfile(logo_path):
            pix = QPixmap(logo_path)
            logo_label.setPixmap(pix.scaledToWidth(160, Qt.TransformationMode.SmoothTransformation))
        logo_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        fl.addWidget(logo_label)

        author_lbl = QLabel("Biome  ·  by Tom Johnson")
        author_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_lbl.setStyleSheet("color:#717E75; font-size:10px;")
        fl.addWidget(author_lbl)

        guide_btn = QPushButton("  User Guide")
        guide_btn.setIcon(_svg_icon("book", "#2E6B4F", 16))
        guide_btn.setMinimumHeight(30)
        guide_btn.setStyleSheet("""
            QPushButton {
                background:transparent; color:#2E6B4F;
                border:1px solid #1F4A37; border-radius:6px;
                font-size:11px; font-weight:500; padding:4px 10px;
                font-family:'Inter Medium','Inter','Segoe UI',sans-serif;
            }
            QPushButton:hover { background:#F1F4F0; }
        """)
        guide_btn.clicked.connect(self._open_user_guide)
        fl.addWidget(guide_btn)

        start_btn = QPushButton("  Getting Started")
        start_btn.setIcon(_svg_icon("rocket", "#2E6B4F", 16))
        start_btn.setMinimumHeight(30)
        start_btn.setStyleSheet("""
            QPushButton {
                background:transparent; color:#2E6B4F;
                border:1px solid #1F4A37; border-radius:6px;
                font-size:11px; font-weight:500; padding:4px 10px;
                font-family:'Inter Medium','Inter','Segoe UI',sans-serif;
            }
            QPushButton:hover { background:#F1F4F0; }
        """)
        start_btn.clicked.connect(lambda: _OnboardingWizard(self.iface.mainWindow()).exec())
        fl.addWidget(start_btn)

        footer_w.setLayout(fl)
        layout.addWidget(footer_w)

        layout.addStretch()
        screen.setLayout(layout)
        scroll.setWidget(screen)
        return scroll

    # ── Legend swatches: render a habitat's REAL map symbol for a hub row ──
    _LEGEND_LAYER_KEY = {
        "baseline": {"area": "baseline_area", "watercourse": "baseline_water",
                     "hedgerow": "baseline_hedgerow", "trees": "baseline_trees"},
        "proposed": {"area": "proposed_area", "watercourse": "proposed_water",
                     "hedgerow": "proposed_hedgerow", "trees": "proposed_trees"},
    }
    _LEGEND_GEOM = {"area": "Polygon", "watercourse": "LineString",
                    "hedgerow": "LineString", "trees": "Point"}
    # Largest tree diameter (mm) = the Very Large graduated circle in the tree
    # QMLs; used to scale the swatch so the biggest tree just fills the cell.
    # Keep in sync with MAX_TREE_MM in scratchpad gen_tree_symbols.py.
    _TREE_SWATCH_MAX_MM = 8.4

    def _symbol_from_renderer(self, renderer, sub):
        """Pull the QgsSymbol matching sub-habitat `sub` out of a bundled
        renderer — the matching category for the categorised area/hedge/water
        renderers, or the matching size/type rule for the tree RuleRenderer.
        Returns a detached clone, or None when there is no match."""
        try:
            from qgis.core import (QgsCategorizedSymbolRenderer,
                                   QgsRuleBasedRenderer)
            if isinstance(renderer, QgsCategorizedSymbolRenderer):
                for cat in renderer.categories():
                    if str(cat.value()).strip() == sub:
                        s = cat.symbol()
                        return s.clone() if s is not None else None
            elif isinstance(renderer, QgsRuleBasedRenderer):
                for rule in renderer.rootRule().children():
                    lbl = (rule.label() or "").strip()
                    for pre in ("Existing ", "Proposed ", "Created "):
                        if lbl.startswith(pre):
                            lbl = lbl[len(pre):]
                            break
                    if lbl == sub:
                        s = rule.symbol()
                        return s.clone() if s is not None else None
        except Exception:
            pass
        return None

    def _legend_swatch_pixmap(self, sub_type, habitat_key, phase, size=24):
        """A true map-legend swatch for a hub row: the sub-type's actual symbol
        (polygon fill + pattern for area, a line for hedgerow/watercourse, a
        point marker for trees) rendered straight from the bundled QML renderer,
        so the list reads exactly like the map legend. Returns a QPixmap, or
        None to fall back to the flat colour swatch."""
        try:
            from qgis.core import QgsSymbolLayerUtils
            sub = (sub_type or "").strip()
            layer_key = self._LEGEND_LAYER_KEY.get(phase, {}).get(habitat_key)
            geom = self._LEGEND_GEOM.get(habitat_key)
            if not sub or not layer_key or not geom:
                return None
            # Renderers never change during a session — build each at most once.
            cache = getattr(self, "_legend_renderers", None)
            if cache is None:
                cache = self._legend_renderers = {}
            if layer_key not in cache:
                cache[layer_key] = self.layer_manager.build_builtin_renderer(
                    layer_key, geom)
            renderer = cache[layer_key]
            if renderer is None:
                return None
            symbol = self._symbol_from_renderer(renderer, sub)
            if symbol is None:
                return None
            # Tree size classes are distinguished by their RING COUNT (Small 0,
            # Medium 1, Large 2, Very Large 3) plus a core that grows a little —
            # see the tree QMLs. Trees get the full 38px cell (a 3-ring Very Large
            # needs the room); area/line swatches stay at 28. Both centre in the
            # same QLabel, so row names stay aligned.
            dpr = 2   # render at 2x for crisp swatches on hi-dpi displays
            render_px = 38 if geom == "Point" else 28
            # symbolPreviewPixmap draws a MARKER at its true mm size into the
            # pixmap; because we render into a dpr-scaled pixmap and then set the
            # device-pixel ratio, an un-scaled marker would display at HALF size
            # (fills and lines fill the box, so only markers are affected). Scale
            # every tree by one factor so the biggest (Very Large, ~11mm) just
            # fills the cell and the map size-ramp is preserved. (~96dpi preview.)
            if geom == "Point":
                try:
                    target = render_px * 0.94
                    f = (target * dpr) / (self._TREE_SWATCH_MAX_MM * 3.7795)
                    symbol.setSize(symbol.size() * f)
                except Exception:
                    pass
            pm = QgsSymbolLayerUtils.symbolPreviewPixmap(
                symbol, QSize(render_px * dpr, render_px * dpr))
            if pm is None or pm.isNull():
                return None
            # Round the area (polygon) swatches so a filled parcel reads as a
            # soft legend key, matching the flat-colour swatch's rounded corners.
            if geom == "Polygon":
                pm = self._round_pixmap(pm, 5, dpr)
            pm.setDevicePixelRatio(dpr)
            return pm
        except Exception:
            return None

    @staticmethod
    def _round_pixmap(pm, radius, dpr):
        """Return `pm` with rounded corners (radius in logical px). Operates in
        device pixels — call BEFORE setting the pixmap's device-pixel ratio."""
        from qgis.PyQt.QtGui import QPixmap, QPainter, QPainterPath
        from qgis.PyQt.QtCore import QRectF
        out = QPixmap(pm.size())
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            path = QPainterPath()
            r = radius * dpr
            path.addRoundedRect(QRectF(0, 0, pm.width(), pm.height()), r, r)
            p.setClipPath(path)
            p.drawPixmap(0, 0, pm)
        finally:
            p.end()
        return out

    # ── Quick Habitats (user-editable hub favourites) ─────────────────────
    _QH_SETTINGS_KEY = "biome/quick_habitats"

    def _load_quick_habitats(self):
        """The hub's Quick Habitats as an ordered list of {habitat_key, broad,
        sub} dicts — from the user's QSettings override, else the built-in
        DEFAULT_QUICK_HABITATS. Entries no longer in the catalogue are dropped
        so a renamed/removed habitat can never break the screen."""
        import json
        from .habitat_data import HABITAT_TYPES
        items = None
        try:
            raw = QSettings().value(self._QH_SETTINGS_KEY, None)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                items = [(d["habitat_key"], d["broad"], d["sub"]) for d in data]
        except Exception:
            items = None
        if items is None:
            items = list(DEFAULT_QUICK_HABITATS)
        valid = []
        for hk, broad, sub in items:
            grp = HABITAT_TYPES.get(broad)
            if isinstance(grp, dict) and sub in grp:
                valid.append({"habitat_key": hk, "broad": broad, "sub": sub})
        return valid

    def _save_quick_habitats(self, items):
        """Persist the Quick Habitats list (ordered {habitat_key, broad, sub}
        dicts) to the QGIS profile and rebuild the hub so it shows at once."""
        import json
        try:
            QSettings().setValue(self._QH_SETTINGS_KEY, json.dumps(
                [{"habitat_key": i["habitat_key"], "broad": i["broad"], "sub": i["sub"]}
                 for i in items]))
        except Exception:
            pass
        self._refresh_phase_screens()

    def _habitat_icon(self, sub, broad, habitat_key, phase="baseline"):
        """A QIcon for a habitat — its real legend swatch where one renders,
        else a rounded colour chip. Used by the Quick Habitats editor."""
        from qgis.PyQt.QtGui import (QIcon, QPixmap, QPainter, QColor,
                                     QBrush, QPen)
        pm = self._legend_swatch_pixmap(sub, habitat_key, phase)
        if pm is not None and not pm.isNull():
            return QIcon(pm)
        col = _swatch_for(sub, broad, habitat_key)
        px = QPixmap(22, 22)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setBrush(QBrush(QColor(col)))
            p.setPen(QPen(QColor(_darken(col, 0.55))))
            p.drawRoundedRect(2, 2, 18, 18, 4, 4)
        finally:
            p.end()
        return QIcon(px)

    def _open_quick_habitats_dialog(self):
        """Open the Quick Habitats editor (Biome settings → Quick Habitats)."""
        from .quick_habitats_dialog import QuickHabitatsDialog
        from .habitat_data import HABITAT_TYPES, HABITAT_SCREEN_TYPES
        gk = {}
        for hk, groups in HABITAT_SCREEN_TYPES.items():
            for g in groups:
                gk[g] = hk
        catalogue = []
        for broad, subs in HABITAT_TYPES.items():
            hk = gk.get(broad)
            if not hk:
                continue
            for sub in subs:
                catalogue.append({"habitat_key": hk, "broad": broad, "sub": sub})
        dlg = QuickHabitatsDialog(
            self,
            catalogue=catalogue,
            current=self._load_quick_habitats(),
            defaults=[{"habitat_key": hk, "broad": b, "sub": s}
                      for hk, b, s in DEFAULT_QUICK_HABITATS],
            icon_fn=lambda sub, broad, hk: self._habitat_icon(sub, broad, hk),
        )
        if dlg.exec():
            self._save_quick_habitats(dlg.result_items())

    def _build_phase_screen(self, phase):
        """Habitat category selector — shows placed categories first,
        then an '+ Add New Type' option to reveal all categories."""
        from qgis.core import QgsProject

        ALL_BTNS = {
            "baseline": [
                ("Area Habitats",       "area",        "#33691e", "#558b2f"),
                ("Watercourse Habitat",  "watercourse", "#234A6E", "#1F4A37"),
                ("Hedgerow Units",       "hedgerow",    "#9A6700", "#f4511e"),
                ("Individual Trees",     "trees",       "#1F4A37", "#2E6B4F"),
            ],
            "proposed": [
                ("Proposed Area Habitats",       "area",        "#175E3B", "#1F7A4D"),
                ("Proposed Watercourse Habitat",  "watercourse", "#006064", "#00838f"),
                ("Proposed Hedgerow Units",       "hedgerow",    "#8A4B12", "#e64a19"),
                ("Proposed Individual Trees",     "trees",       "#1F4A37", "#1F4A37"),
            ],
        }
        # Map hab_key -> layer_key
        key_map = {
            "baseline": {"area": "baseline_area", "watercourse": "baseline_water",
                         "hedgerow": "baseline_hedgerow", "trees": "baseline_trees"},
            "proposed": {"area": "proposed_area",  "watercourse": "proposed_water",
                         "hedgerow": "proposed_hedgerow", "trees": "proposed_trees"},
        }

        def _true_count(hab_key):
            """Count by iterating all features with an explicit unfiltered request.
            Uses QgsFeatureRequest with NoGeometry to avoid any canvas/scale
            filtering and guarantee every stored feature is counted regardless
            of current zoom or visibility."""
            from qgis.core import QgsFeatureRequest
            lkey = key_map[phase][hab_key]
            layer = self.layer_manager.get_layer(lkey)
            if layer is None or not layer.isValid():
                return 0
            req = QgsFeatureRequest()
            req.setNoAttributes()
            req.setFlags(QgsFeatureRequest.Flag.NoGeometry)
            return sum(1 for _ in layer.getFeatures(req))

        def _feature_count(hab_key):
            return _true_count(hab_key)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: #F7F8F6; }")

        screen = QWidget()
        screen.setStyleSheet("background: #F7F8F6;")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(8)

        _HAB_ICONS = {"area": "polygon", "watercourse": "droplet",
                      "hedgerow": "line", "trees": "tree"}
        _SHORT = {"area": "Area", "watercourse": "Watercourse",
                  "hedgerow": "Hedgerow", "trees": "Trees"}

        # ── Reusable chip + row builders ──────────────────────────────
        def _type_chip(hab_key, dark, is_placed, count):
            """A compact habitat-type chip: filled with its count when the type
            is already on the plan, outlined with a '+' when it isn't. Either
            way a click opens that type's form — the same action as before."""
            if is_placed:
                label = "  %s  ·  %d" % (_SHORT.get(hab_key, hab_key), count)
                # Filled in the type's own colour (Area green, Watercourse blue,
                # Hedgerow amber, Trees deep green) — same hue it shows when empty.
                css = ("QPushButton { background:%s; color:#FFFFFF; border:none;"
                       " border-radius:9px; padding:7px 12px; text-align:left;"
                       " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;"
                       " font-size:12px; font-weight:600; }"
                       " QPushButton:hover { background:%s; }" % (dark, _darken(dark)))
                ic = _svg_icon(_HAB_ICONS.get(hab_key), "white", 15)
            else:
                label = "  %s  +" % _SHORT.get(hab_key, hab_key)
                css = ("QPushButton { background:#FFFFFF; color:%s; border:1px solid %s;"
                       " border-radius:9px; padding:7px 12px; text-align:left;"
                       " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;"
                       " font-size:12px; font-weight:600; }"
                       " QPushButton:hover { background:#F3F7F2; }" % (dark, dark))
                ic = _svg_icon(_HAB_ICONS.get(hab_key), dark, 15)
            btn = QPushButton(label)
            btn.setIcon(ic)
            btn.setIconSize(QSize(15, 15))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setMinimumHeight(34)
            btn.setStyleSheet(css)
            # Fill the grid cell so all four chips are the same width (a tidy
            # 2x2 block) rather than each hugging its own label.
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(
                lambda checked, k=hab_key, p=phase: self._open_habitat_form(k, p))
            return btn

        def _habitat_row(sub, condition, swatch, on_click, retention="", swatch_pm=None):
            """One scannable quick/recent row: a legend swatch (the habitat's real
            map symbol — polygon fill/pattern, hedge/water line or tree point —
            when `swatch_pm` is supplied, else a flat colour fill), the (elided)
            habitat name, an optional retention note and a condition pill. The
            whole row is the click target."""
            btn = QPushButton()
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setMinimumHeight(52)
            btn.setStyleSheet(
                "QPushButton { background:#FFFFFF; border:1px solid #E3E7E1;"
                " border-radius:8px; text-align:left; }"
                " QPushButton:hover { background:#F3F7F2; border-color:#C9D1C9; }")
            h = QHBoxLayout(btn)
            h.setContentsMargins(11, 6, 11, 6)
            h.setSpacing(9)
            # Legend-key swatch. With a rendered symbol pixmap it IS the map
            # legend entry (fill+pattern / line / point); otherwise a flat colour
            # fill with a darkened hairline so pale/white fills stay visible.
            sw = QLabel()
            sw.setFixedSize(38, 38)
            sw.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            if swatch_pm is not None and not swatch_pm.isNull():
                sw.setPixmap(swatch_pm)
                sw.setAlignment(Qt.AlignmentFlag.AlignCenter)
                sw.setStyleSheet("background:transparent; border:none;")
            else:
                # Legend-true colour — must never be remapped by the dark-mode
                # restyler (some habitat colours coincide with theme tokens).
                sw.setProperty("biome_fixed_color", True)
                sw.setStyleSheet(
                    "background:%s; border:1px solid %s; border-radius:5px;"
                    % (swatch, _darken(swatch, 0.55)))
            h.addWidget(sw, 0)
            name = _ElidingLabel(sub)
            name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            name.setStyleSheet("background:transparent; border:none; color:#1A2420;"
                               " font-size:13px;"
                               " font-family:'Inter Medium','Inter','Segoe UI',sans-serif;")
            h.addWidget(name, 1)
            if retention:
                ret = QLabel(retention)
                ret.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                ret.setStyleSheet("background:transparent; border:none;"
                                  " color:#717E75; font-size:11px;")
                h.addWidget(ret, 0)
            pill = _cond_chip(condition)
            if pill:
                txt, fg, bg = pill
                cl = QLabel(txt)
                cl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                cl.setStyleSheet("background:%s; color:%s; border:none; border-radius:7px;"
                                 " padding:3px 8px; font-size:11px; font-weight:600;"
                                 " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;"
                                 % (bg, fg))
                h.addWidget(cl, 0)
            btn.clicked.connect(lambda checked: on_click())
            return btn

        # ── Habitat types on this plan (placed first, then add-new) ────
        counts = {b[1]: _feature_count(b[1]) for b in ALL_BTNS[phase]}
        any_placed = any(v > 0 for v in counts.values())
        ordered = sorted(ALL_BTNS[phase], key=lambda b: 0 if counts[b[1]] > 0 else 1)

        layout.addWidget(self._section_label("Habitat types"))
        chip_wrap = QWidget()
        chip_wrap.setStyleSheet("background:transparent;")
        cg = QGridLayout(chip_wrap)
        cg.setContentsMargins(2, 2, 2, 2)
        cg.setHorizontalSpacing(7)
        cg.setVerticalSpacing(7)
        for i, (label, hab_key, dark, light) in enumerate(ordered):
            cnt = counts[hab_key]
            cg.addWidget(_type_chip(hab_key, dark, cnt > 0, cnt), i // 2, i % 2)
        cg.setColumnStretch(0, 1)
        cg.setColumnStretch(1, 1)
        layout.addWidget(chip_wrap)

        # Onboarding hint while the plan is still empty.
        if not any_placed and phase == "baseline":
            layout.addWidget(_banner(
                "info-circle",
                "Pick a habitat type above, fill in the details, then click "
                "Draw Feature on Map to draw it on the canvas.\n\n"
                "Map every distinct habitat on the site — there should be no gaps "
                "within the redline boundary.",
                fg="#54635A", bg="#F1F4F0", font_px=11))
        elif not any_placed and phase == "proposed":
            layout.addWidget(_banner(
                "info-circle",
                "Map what each baseline habitat will become after development.\n\n"
                "If Create Baseline + Proposed is ON, a proposed feature is created "
                "automatically when you draw a baseline — just update its type and condition.",
                fg="#54635A", bg="#F1F4F0", font_px=11))

        # ── Favourite Habitats (permanent quick-fire buttons) ───────────
        # Each entry is (label, snapshot_base) — retention is injected at
        # click time based on whether _proposed_mapping is currently on.
        # ── Search box — a finder over the FULL habitat catalogue ─────
        layout.addWidget(self._divider())
        search = QLineEdit()
        search.setPlaceholderText("Search all habitats…")
        search.setClearButtonEnabled(True)
        search.addAction(_svg_icon("search", "#717E75", 16), QLineEdit.ActionPosition.LeadingPosition)
        search.setStyleSheet(
            "QLineEdit { background:#FFFFFF; border:1px solid #C9D1C9; border-radius:9px;"
            " padding:7px 10px; font-size:13px; }"
            " QLineEdit:focus { border:1px solid #2E6B4F; }")
        layout.addWidget(search)

        def _row_label_for(snap):
            """Display name for a snapshot — trees read size+location, every
            other type uses its sub-type."""
            if snap.get("habitat_key") == "trees":
                size = snap.get("tree_size", "")
                loc  = snap.get("tree_location", "")
                return f"{size} {loc} Tree" if size and loc else "Tree"
            return snap.get("sub_type", "")

        # ── Search results (built on demand; hidden until typing) ─────
        results_box = QWidget()
        results_box.setStyleSheet("background:transparent;")
        results_lay = QVBoxLayout(results_box)
        results_lay.setContentsMargins(0, 0, 0, 0)
        results_lay.setSpacing(6)
        results_box.setVisible(False)
        layout.addWidget(results_box)

        # Catalogue: every placeable sub-type across all broad groups, mapped to
        # its screen (area / hedgerow / watercourse / trees). Built once per
        # screen as plain data; only the matching ROWS are created per keystroke.
        from .habitat_data import HABITAT_TYPES, HABITAT_SCREEN_TYPES
        _group_key = {}
        for _hk, _groups in HABITAT_SCREEN_TYPES.items():
            for _g in _groups:
                _group_key[_g] = _hk
        _catalogue = []   # (sub_type, broad_group, habitat_key, swatch_colour)
        for _broad, _subs in HABITAT_TYPES.items():
            _hk = _group_key.get(_broad)
            if not _hk:
                continue
            for _sub in _subs:
                _catalogue.append((_sub, _broad, _hk, _swatch_for(_sub, _broad, _hk)))

        def _search_snapshot(sub, broad, habitat_key):
            """Prefill snapshot for a catalogue hit (trees split size+location)."""
            snap = {"phase": phase, "habitat_key": habitat_key, "hab_type": broad}
            if habitat_key == "trees":
                loc  = "Rural" if "Rural" in sub else "Urban"
                size = sub.replace(loc, "").replace("Tree", "").strip()
                snap.update({"tree_size": size, "tree_location": loc,
                             "prop_tree_size": size, "prop_tree_location": loc,
                             "clip_redline": False})
            else:
                snap.update({"sub_type": sub, "prop_type": broad, "prop_sub": sub,
                             "clip_redline": habitat_key == "area"})
            snap["retention"] = "Retained" if getattr(self, "_proposed_mapping", True) else ""
            return snap

        _RESULT_CAP = 40

        def _render_results(q):
            while results_lay.count():
                _it = results_lay.takeAt(0)
                _w = _it.widget()
                if _w is not None:
                    _w.deleteLater()
            matched = shown = 0
            for sub, broad, hk, swatch in _catalogue:
                if q not in sub.lower():
                    continue
                matched += 1
                if shown >= _RESULT_CAP:
                    continue

                def _open(s=sub, b=broad, k=hk):
                    self._open_habitat_form_prefilled(_search_snapshot(s, b, k))

                results_lay.addWidget(_habitat_row(
                    sub, "", swatch, _open, retention=broad,
                    swatch_pm=self._legend_swatch_pixmap(sub, hk, phase)))
                shown += 1
            if shown == 0:
                miss = QLabel("No habitats match your search.")
                miss.setStyleSheet("color:#717E75; font-size:12px; background:transparent;"
                                   " border:none; padding:6px 2px;")
                results_lay.addWidget(miss)
            elif matched > shown:
                more = QLabel("Showing the first %d of %d — keep typing to narrow." % (shown, matched))
                more.setStyleSheet("color:#717E75; font-size:11px; background:transparent;"
                                   " border:none; padding:4px 2px;")
                results_lay.addWidget(more)

        # ── Quick habitats (shown when not searching) ─────────────────
        quick_box = QWidget()
        quick_box.setStyleSheet("background:transparent;")
        quick_lay = QVBoxLayout(quick_box)
        quick_lay.setContentsMargins(0, 0, 0, 0)
        quick_lay.setSpacing(8)
        quick_lay.addWidget(self._section_label("Quick Habitats", icon="status-star"))
        from .habitat_data import condition_items_for
        for fav in self._load_quick_habitats():
            hab_key_f = fav["habitat_key"]
            broad     = fav["broad"]
            sub       = fav["sub"]
            # Build the same prefill a search hit would, so a favourite behaves
            # exactly like searching for it — but keep the old clip default
            # (everything except trees clips to the redline).
            fav_snap  = _search_snapshot(sub, broad, hab_key_f)
            fav_snap["clip_redline"] = hab_key_f != "trees"
            cond      = condition_items_for(sub)[1]   # target grade, for the chip
            swatch    = _swatch_for(sub, broad, hab_key_f)
            swatch_pm = self._legend_swatch_pixmap(sub, hab_key_f, phase)

            def _open_fav(snap=fav_snap):
                # Inject retention at click time from the current mapping toggle
                s = dict(snap)
                s["retention"] = "Retained" if getattr(self, "_proposed_mapping", True) else ""
                self._open_habitat_form_prefilled(s)

            quick_lay.addWidget(
                _habitat_row(sub, cond, swatch, _open_fav, swatch_pm=swatch_pm))
        layout.addWidget(quick_box)

        # ── Recent habitats (shown when not searching) ────────────────
        recent = self._recent_habitats.get(phase, [])
        recent_box = None
        if recent:
            recent_box = QWidget()
            recent_box.setStyleSheet("background:transparent;")
            recent_lay = QVBoxLayout(recent_box)
            recent_lay.setContentsMargins(0, 0, 0, 0)
            recent_lay.setSpacing(8)
            recent_lay.addWidget(self._section_label("Recent Habitats"))
            for snap in recent:
                hab_key = snap.get("habitat_key", "area")
                sub     = _row_label_for(snap)
                swatch  = _swatch_for(sub, snap.get("hab_type"), hab_key)

                def _open_recent(s=snap):
                    self._open_habitat_form_prefilled(s)

                recent_lay.addWidget(
                    _habitat_row(sub, snap.get("condition", ""), swatch,
                                 _open_recent, retention=snap.get("retention", ""),
                                 swatch_pm=self._legend_swatch_pixmap(sub, hab_key, phase)))
            layout.addWidget(recent_box)

        def _apply_filter(text):
            searching = bool(text.strip())
            quick_box.setVisible(not searching)
            if recent_box is not None:
                recent_box.setVisible(not searching)
            results_box.setVisible(searching)
            if searching:
                _render_results(text.strip().lower())
        search.textChanged.connect(_apply_filter)

        layout.addStretch()
        screen.setLayout(layout)
        scroll.setWidget(screen)
        return scroll

    # ------------------------------------------------------------------ #
    #  NAVIGATION                                                          #
    # ------------------------------------------------------------------ #

    def _set_header_title(self, title):
        """Route the header between the painted wordmark (home) and the
        plain title label (sub-screens). The label's text is still the
        navigation state even while hidden."""
        home = (title == "Biome")
        self.header_label.setText(title)
        self.header_label.setVisible(not home)
        if getattr(self, "_banner", None) is not None:
            self._banner.set_wordmark_visible(home)

    def _style_tab_strip(self, light_ground):
        """Restyle the banner tab strip for whatever ground the banner
        is painting behind it: bright labels over dark grass, ink
        labels over a snowy winter band. The banner calls this (via
        on_band_light_changed) whenever that flips, so 'Mapping' and
        'Desk Study' stay readable in every season."""
        if light_ground:
            idle = ("  color:rgba(26,36,32,0.88);"
                    "  background:rgba(20,40,30,0.10);"
                    "  border:1px solid rgba(20,40,30,0.30);")
            hover = ("QTabBar::tab:hover { color:#1A2420;"
                     " background:rgba(20,40,30,0.18); }")
        else:
            idle = ("  color:rgba(255,255,255,0.88);"
                    "  background:rgba(20,40,30,0.22);"
                    "  border:1px solid rgba(255,255,255,0.22);")
            hover = ("QTabBar::tab:hover { color:white;"
                     " background:rgba(20,40,30,0.34); }")
        self._tab_strip.setStyleSheet(
            "QTabBar { background:transparent; border:none; }"
            "QTabBar::tab {"
            + idle +
            "  border-bottom:none;"
            "  border-top-left-radius:7px; border-top-right-radius:7px;"
            # Five labels must fit a normal 300–340 px dock without leaving the
            # final tab half-hidden behind QTabBar's scroll arrow.
            "  padding:6px 5px; margin-right:1px; margin-top:3px;"
            "  font-family:'Inter','Segoe UI',sans-serif;"
            "  font-size:11px; font-weight:400;"
            "}"
            + hover +
            f"QTabBar::tab:selected {{"
            f"  color:{theme.INK}; background:{theme.SURFACE};"
            f"  border-color:{theme.SURFACE};"
            f"  margin-top:0px; padding-bottom:7px;"
            f"}}"
        )

    def _apply_banner_chrome(self, is_dark):
        """Flip the header chrome (title, back button, cog) between the
        light-sky and night-sky palettes as the banner crosses twilight."""
        fg = "#FFFFFF" if is_dark else "#1A2420"
        tint = "255,255,255" if is_dark else "0,0,0"
        self.header_label.setStyleSheet(
            f"color:{fg}; font-size:14px; font-weight:500; letter-spacing:0.4px;"
            " font-family:'Inter Medium','Inter','Segoe UI',sans-serif;"
        )
        self.back_btn.setIcon(theme.icon("arrow-left", fg, 14))
        self.back_btn.setStyleSheet(
            "QPushButton {"
            f"  color: {fg}; background: rgba({tint},0.10);"
            f"  border: 1px solid rgba({tint},0.25); border-radius: 8px;"
            "  font-size: 11px; font-weight: 500; padding: 3px 10px;"
            "}"
            f"QPushButton:hover {{ background: rgba({tint},0.20); }}"
        )
        self._settings_cog.setIcon(_svg_icon("settings", fg, 18))
        self._settings_cog.setStyleSheet(
            "QToolButton{border:none;padding:4px;border-radius:6px;background:transparent;}"
            f"QToolButton:hover{{background:rgba({tint},0.18);}}")

    def _navigate_to(self, index, title):
        self._screen_history.append(
            (self.stack.currentIndex(), self.header_label.text())
        )
        self.stack.setCurrentIndex(index)
        self._set_header_title(title)
        self.back_btn.setVisible(True)
        self._tab_bar.setVisible(False)

    def go_back(self):
        # If the current screen is a HabitatFormScreen with an active drawing
        # session, treat Back as "finish the session" rather than navigating
        # away raw. Leaving raw would strand the live map tool / event filter on
        # the canvas; a later click could then fire that stale session and yank
        # the panel to the wrong phase screen. Tree placing and shape-stamp both
        # run continuous sessions, so finish whichever is active.
        current = self.stack.currentWidget()
        from .habitat_form import HabitatFormScreen
        if isinstance(current, HabitatFormScreen):
            # Continuous sessions: finishing one already navigates back to the
            # phase screen (the same place Back goes from a form), so return.
            if getattr(current, "_tree_mode", False):
                current._finish_tree_session()
                return
            if getattr(current, "_shape_continuous", False):
                current._finish_shape_session()
                return
            # A single digitize/freehand draw is armed but nothing has been
            # placed yet. Cancel it so the half-drawn geometry and the live map
            # tool don't linger on the canvas, then fall through to the normal
            # Back navigation below.
            current.cancel_pending_draw()
            # Back abandons this form: release the active-placer slot so the
            # plan-mode canvas right-click menu (grid ref etc.) comes back.
            if getattr(self, "_active_habitat_form", None) is current:
                self._active_habitat_form = None
        elif current is getattr(self, "_bt_screen_widget", None):
            # Leaving the Building & Tree Mapper — stop any armed building/tree
            # placement tool so it doesn't stay live on the canvas after Back.
            self._bt_finish()
        # Leaving the Manage screen with a "Move feature" click pending: disarm
        # it so the next canvas click can't silently relocate the feature.
        self._abort_move_tool()
        # Leaving the Manage screen: run the deferred summary recompute once,
        # now that the balance/BNG/phase widgets will be visible again.
        self._flush_manage_changes()
        if self._screen_history:
            index, title = self._screen_history.pop()
            # If returning to a phase screen (slots 1 or 2), rebuild it so
            # the Recent Habitats section is always up to date.
            if index in (1, 2):
                phase = "baseline" if index == 1 else "proposed"
                screen = self._build_phase_screen(phase)
                old = self.stack.widget(index)
                self.stack.insertWidget(index, screen)
                self.stack.removeWidget(old)
                if old:
                    old.deleteLater()
            self.stack.setCurrentIndex(index)
            self._set_header_title(title)
            self.back_btn.setVisible(bool(self._screen_history))
            if not self._screen_history:
                self._tab_bar.setVisible(True)

    # ------------------------------------------------------------------ #
    #  RECENT HABITATS                                                     #
    # ------------------------------------------------------------------ #

    def push_recent_habitat(self, phase, snapshot):
        """Record a recently placed habitat so it appears as a quick-place
        button on the phase screen.  snapshot is a plain dict with keys:
          habitat_key, hab_type, sub_type, condition, retention,
          prop_type, prop_sub, prop_cond,
          strategic_sig, irreplaceable, hab_ref, comments,
          tree_size, tree_location, prop_tree_size, prop_tree_location
        Duplicates (same sub_type + condition + retention) are moved to the
        front rather than added again.  List is capped at 5."""
        recent = self._recent_habitats.setdefault(phase, [])
        # Dedup key
        def _key(s):
            return (s.get("sub_type", ""), s.get("condition", ""),
                    s.get("retention", ""))
        k = _key(snapshot)
        recent = [r for r in recent if _key(r) != k]  # remove existing duplicate
        recent.insert(0, snapshot)                      # most recent first
        self._recent_habitats[phase] = recent[:5]

    # ------------------------------------------------------------------ #
    #  ACTIONS                                                             #
    # ------------------------------------------------------------------ #

    def _zoom_to_redline(self):
        """Recenter and zoom the map canvas to fit the Redline Boundary."""
        from qgis.core import (
            QgsCoordinateTransform, QgsProject, QgsRectangle,
        )

        layer = self.layer_manager.get_layer("redline")
        if layer is None or layer.featureCount() == 0:
            self.iface.messageBar().pushMessage(
                "Biome",
                "No Red Line Boundary found — draw one first using "
                "'Add Red Line Boundary'.",
                level=Qgis.MessageLevel.Warning, duration=5,
            )
            return

        canvas     = self.iface.mapCanvas()
        canvas_crs = canvas.mapSettings().destinationCrs()
        layer_crs  = layer.crs()

        extent = layer.extent()
        if extent.isEmpty():
            return

        # Transform layer extent to canvas CRS if needed
        if layer_crs != canvas_crs:
            xform  = QgsCoordinateTransform(layer_crs, canvas_crs,
                                            QgsProject.instance())
            extent = xform.transformBoundingBox(extent)

        # Add 15% padding so the boundary isn't right at the edge
        pad = max(extent.width(), extent.height()) * 0.15
        extent.grow(max(pad, 50.0))

        canvas.setExtent(extent)
        canvas.refresh()

    def _smart_search(self):
        """Single search box: if the text parses as a British National Grid
        reference, zoom to it; otherwise treat it as an address/postcode."""
        from .grid_ref import grid_ref_to_osgb36
        text = self.grid_ref_input.text().strip()
        if not text:
            return
        self._suggest_timer.stop()
        self.grid_ref_status.setVisible(False)
        self.addr_status.setVisible(False)
        self.search_results_list.setVisible(False)
        if grid_ref_to_osgb36(text) is not None:
            self._zoom_to_grid_ref()
        else:
            self._zoom_to_address()

    def _on_search_text_edited(self, text):
        """Debounce keystrokes into a live suggestion lookup."""
        if len(text.strip()) < 3:
            self._suggest_timer.stop()
            self.search_results_list.setVisible(False)
            return
        self._suggest_timer.start()

    def _run_live_suggest(self):
        from .grid_ref import grid_ref_to_osgb36
        text = self.grid_ref_input.text().strip()
        # Grid refs zoom directly on Enter — no point suggesting for them.
        if len(text) < 3 or grid_ref_to_osgb36(text) is not None:
            return
        self._photon_search(text, live=True)

    def _zoom_to_grid_ref(self):
        from .grid_ref import grid_ref_to_osgb36
        from qgis.core import (
            QgsCoordinateReferenceSystem, QgsCoordinateTransform,
            QgsPointXY, QgsRectangle, QgsProject,
        )
        ref = self.grid_ref_input.text().strip()
        if not ref:
            return
        result = grid_ref_to_osgb36(ref)
        if result is None:
            self.grid_ref_status.setText(
                "Could not parse grid ref. Try: SU 12345 67890 or TQ300800"
            )
            self.grid_ref_status.setVisible(True)
            return
        self.grid_ref_status.setText("")
        self.grid_ref_status.setVisible(False)
        easting, northing = result
        src_crs   = QgsCoordinateReferenceSystem("EPSG:27700")
        dest_crs  = QgsCoordinateReferenceSystem("EPSG:3857")
        transform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
        pt        = transform.transform(QgsPointXY(easting, northing))
        offset    = 250
        extent    = QgsRectangle(
            pt.x() - offset, pt.y() - offset,
            pt.x() + offset, pt.y() + offset,
        )
        self._show_search_marker(pt)
        self.iface.mapCanvas().setExtent(extent)
        self.iface.mapCanvas().refresh()

    def _zoom_to_address(self):
        """Geocode a UK address, postcode, business or road name.

        Photon is the primary engine: it's typo-tolerant, POI-aware, dedupes
        road segments server-side and genuinely honours the canvas location
        bias. Nominatim's viewbox bias is near-useless for name queries (a
        business search with the map on Andover used to return 8 London
        branches), so it now only leads for postcode-shaped queries — where
        it's authoritative — and otherwise serves as the fallback for
        structured addresses Photon misses. Each engine falls back to the
        other exactly once, then gives up.

        UK house numbers are patchy in OSM, so "100 Staines Rd TW3 3LF"
        often matches only road segments — a marker on the street midpoint,
        possibly 1 km from the house. When the query embeds a full postcode
        and the geocoders only manage street-level hits, we zoom to the
        postcodes.io centroid instead (a postcode is ~a dozen houses). Bare
        postcodes skip the geocoders entirely and go straight there."""
        import re
        query = self.addr_input.text().strip()
        if not query:
            return

        self.search_results_list.setVisible(False)
        self.addr_status.setStyleSheet("color:#54635A; font-size:11px;")
        self.addr_status.setText("Searching…")
        self.addr_status.setVisible(True)

        pc_rx = r"[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}"
        m = re.search(rf"\b{pc_rx}\b", query)
        self._query_postcode = m.group(0) if m else None
        self._pc_tried = False

        if m and re.fullmatch(pc_rx, query):
            self._postcode_point_lookup(query, [], primary=True)
        else:
            self._photon_search(query)

    def _nominatim_search(self, query, fallback=False):
        """Nominatim lookup — primary for postcodes/structured addresses,
        fallback when Photon finds nothing. `fallback` stops the two engines
        bouncing the query back and forth."""
        self._abort_geocode_reply()
        self._geocode_is_fallback = fallback

        from qgis.PyQt.QtCore import QUrl, QUrlQuery
        from qgis.PyQt.QtNetwork import QNetworkRequest
        from qgis.core import QgsNetworkAccessManager

        params = QUrlQuery()
        params.addQueryItem("q",              query)
        params.addQueryItem("format",         "json")
        params.addQueryItem("limit",          "8")
        params.addQueryItem("countrycodes",   "gb")
        params.addQueryItem("addressdetails", "1")
        # Include points of interest (shops, businesses, amenities) rather
        # than favouring pure address matches.
        params.addQueryItem("layer",          "poi,address")

        # Bias ranking towards the current canvas view; bounded=0 keeps
        # distant matches available, just ranked lower.
        vb = self._canvas_viewbox_wgs84()
        if vb:
            params.addQueryItem("viewbox", vb)
            params.addQueryItem("bounded", "0")

        url = QUrl("https://nominatim.openstreetmap.org/search")
        url.setQuery(params)

        req = QNetworkRequest(url)
        req.setRawHeader(b"User-Agent", b"BiomeQGISPlugin/1.0")
        self._harden_geocode_request(req)

        nam   = QgsNetworkAccessManager.instance()
        reply = nam.get(req)
        self._geocode_reply = reply
        self._geocode_query = query
        # Bound receiver: Qt disconnects this automatically if the Biome panel
        # is destroyed. A lambda captured the reply wrapper and could outlive
        # either object, causing a native Qt 6 access violation.
        reply.finished.connect(self._on_geocode_reply)

    def _abort_geocode_reply(self):
        """Orphan any in-flight geocode request so a slow earlier search
        can't clobber the results of a newer one.

        Deliberately does NOT call reply.abort() or disconnect anything:
        aborting mid-transfer exercises Qt 6's HTTP/2-multiplexing and
        disk-cache teardown paths, implicated in two identical native
        heap-corruption crashes (0xc0000374, same ntdll offset) while
        searching on QGIS 4.2.0 (2026-07-22). The orphan finishes
        naturally; its handler sees it no longer matches _geocode_reply,
        returns early, and the handler's finally-clause deleteLater()s it."""
        self._geocode_reply = None

    def _harden_geocode_request(self, req):
        """Qt 6 defaults to HTTP/2 and writes cacheable responses to the
        QGIS disk cache — both crash-implicated (see _abort_geocode_reply).
        Geocode lookups are tiny one-shots; force plain HTTP/1.1, no cache
        write. No-ops harmlessly on Qt 5 (HTTP/2 is off by default there)."""
        from qgis.PyQt.QtNetwork import QNetworkRequest
        try:
            req.setAttribute(
                QNetworkRequest.Attribute.Http2AllowedAttribute, False)
            req.setAttribute(
                QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        except AttributeError:
            pass

    def _canvas_viewbox_wgs84(self):
        """Current canvas extent as a Nominatim viewbox string
        (lon,lat,lon,lat in WGS84), or None if it can't be computed."""
        try:
            canvas = self.iface.mapCanvas()
            ext    = canvas.extent()
            src    = canvas.mapSettings().destinationCrs()
            tr     = QgsCoordinateTransform(
                src, QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance())
            e = tr.transformBoundingBox(ext)
            if e.isEmpty() or e.width() <= 0 or e.height() <= 0:
                return None
            return (f"{e.xMinimum():.6f},{e.yMaximum():.6f},"
                    f"{e.xMaximum():.6f},{e.yMinimum():.6f}")
        except Exception:
            return None

    def _canvas_centre_wgs84(self):
        """Canvas centre as (lon, lat) in WGS84, or None."""
        try:
            canvas = self.iface.mapCanvas()
            c      = canvas.extent().center()
            src    = canvas.mapSettings().destinationCrs()
            tr     = QgsCoordinateTransform(
                src, QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance())
            p = tr.transform(c)
            return (p.x(), p.y())
        except Exception:
            return None

    def _on_geocode_reply(self):
        import json
        from qgis.PyQt.QtNetwork import QNetworkReply

        reply = self.sender()
        if reply is None:
            return

        try:
            if reply is not getattr(self, "_geocode_reply", None):
                return   # superseded by a newer search
            self._geocode_reply = None

            if reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.addr_status.setStyleSheet("color:#B3372F; font-size:11px;")
                self.addr_status.setText(f"Network error: {reply.errorString()}")
                self.addr_status.setVisible(True)
                return

            raw  = bytes(reply.readAll()).decode("utf-8", errors="replace")
            data = json.loads(raw)

            results = self._dedupe_geocode_results(
                [r for r in map(self._parse_nominatim_result, data) if r])[:8]

            pc = getattr(self, "_query_postcode", None)
            pc_fresh = pc and not getattr(self, "_pc_tried", False)

            if not results:
                if pc_fresh:
                    self._postcode_point_lookup(pc, [])
                elif getattr(self, "_geocode_is_fallback", False):
                    self._geocode_not_found()
                else:
                    # Query Nominatim couldn't place — Photon is
                    # typo-tolerant, so try it before reporting failure.
                    self._photon_search(getattr(self, "_geocode_query", ""),
                                        fallback=True)
                return

            if pc_fresh and all(r.get("vague") for r in results):
                # Only street/postcode-level hits for an address with a
                # postcode — the postcode centroid is closer to the house.
                self._postcode_point_lookup(pc, results)
                return

            self._present_geocode_results(results)

        except Exception as exc:
            self.addr_status.setStyleSheet("color:#B3372F; font-size:11px;")
            self.addr_status.setText(f"Error: {exc}")
            self.addr_status.setVisible(True)
        finally:
            reply.deleteLater()

    @staticmethod
    def _parse_nominatim_result(r):
        """Normalise a Nominatim JSON result to {label, lat, lon, bbox}.
        bbox is (south, north, west, east) in WGS84, or None."""
        try:
            lat = float(r["lat"])
            lon = float(r["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        label = r.get("display_name", "")
        short = ", ".join(p.strip() for p in label.split(",")[:3]) if label \
            else f"{lat:.4f}, {lon:.4f}"
        bbox = None
        bb = r.get("boundingbox")
        if bb and len(bb) == 4:
            try:
                bbox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
            except (TypeError, ValueError):
                bbox = None
        addr  = r.get("address") or {}
        vague = not addr.get("house_number") and (
            r.get("class") == "highway"
            or r.get("addresstype") in ("road", "postcode"))
        return {"label": short, "lat": lat, "lon": lon, "bbox": bbox,
                "vague": vague}

    def _photon_search(self, query, live=False, fallback=False):
        """Photon (photon.komoot.io) — the primary geocoder for business,
        road and place-name queries, and for live as-you-type suggestions
        (Nominatim's usage policy forbids autocomplete)."""
        if not query:
            if not live:
                self._geocode_not_found()
            return

        self._abort_geocode_reply()
        self._geocode_live = live
        self._geocode_is_fallback = fallback
        self._geocode_query = query

        from qgis.PyQt.QtCore import QUrl, QUrlQuery
        from qgis.PyQt.QtNetwork import QNetworkRequest
        from qgis.core import QgsNetworkAccessManager

        params = QUrlQuery()
        params.addQueryItem("q",     query)
        # Ask for far more than we show: non-GB hits are filtered client-side
        # (Photon has no countrycodes param), and with only 8 requested a
        # name that's famous abroad could starve the list to 0–1 GB results.
        params.addQueryItem("limit", "30")
        params.addQueryItem("lang",  "en")

        centre = self._canvas_centre_wgs84()
        if centre:
            params.addQueryItem("lon", f"{centre[0]:.6f}")
            params.addQueryItem("lat", f"{centre[1]:.6f}")
            # Without these the lon/lat bias is weak and distant-but-famous
            # matches outrank the branch round the corner.
            params.addQueryItem("zoom", "14")
            params.addQueryItem("location_bias_scale", "0.5")

        url = QUrl("https://photon.komoot.io/api")
        url.setQuery(params)

        req = QNetworkRequest(url)
        req.setRawHeader(b"User-Agent", b"BiomeQGISPlugin/1.0")
        self._harden_geocode_request(req)

        reply = QgsNetworkAccessManager.instance().get(req)
        self._geocode_reply = reply
        reply.finished.connect(self._on_photon_reply)

    def _on_photon_reply(self):
        import json
        from qgis.PyQt.QtNetwork import QNetworkReply

        reply = self.sender()
        if reply is None:
            return

        try:
            if reply is not getattr(self, "_geocode_reply", None):
                return
            self._geocode_reply = None
            live = getattr(self, "_geocode_live", False)

            if reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                if not live:
                    self._geocode_not_found()
                return

            raw  = bytes(reply.readAll()).decode("utf-8", errors="replace")
            data = json.loads(raw)

            results = self._dedupe_geocode_results(
                [r for r in map(self._parse_photon_feature,
                                data.get("features", [])) if r])[:8]

            pc = getattr(self, "_query_postcode", None)
            pc_fresh = (not live and pc
                        and not getattr(self, "_pc_tried", False))

            if not results:
                if live:
                    self.search_results_list.setVisible(False)
                elif getattr(self, "_geocode_is_fallback", False):
                    if pc_fresh:
                        self._postcode_point_lookup(pc, [])
                    else:
                        self._geocode_not_found()
                else:
                    # Photon drew a blank — Nominatim is stronger on
                    # structured "12 High Street, Andover" addresses.
                    self._nominatim_search(getattr(self, "_geocode_query", ""),
                                           fallback=True)
                return

            if pc_fresh and all(r.get("vague") for r in results):
                self._postcode_point_lookup(pc, results)
                return

            self._present_geocode_results(results, live=live)

        except Exception as exc:
            self.addr_status.setStyleSheet("color:#B3372F; font-size:11px;")
            self.addr_status.setText(f"Error: {exc}")
            self.addr_status.setVisible(True)
        finally:
            reply.deleteLater()

    @staticmethod
    def _parse_photon_feature(f):
        """Normalise a Photon GeoJSON feature to {label, lat, lon, bbox}.
        Drops non-UK results to match the Nominatim countrycodes filter."""
        props = f.get("properties") or {}
        if str(props.get("countrycode", "GB")).upper() != "GB":
            return None
        try:
            lon, lat = (float(v) for v in f["geometry"]["coordinates"][:2])
        except (KeyError, TypeError, ValueError):
            return None
        street = props.get("street")
        if street and props.get("housenumber"):
            street = f"{props['housenumber']} {street}"
        locality = (props.get("city") or props.get("district")
                    or props.get("town") or props.get("village")
                    or props.get("county"))
        parts = [props.get("name"), street, locality, props.get("postcode")]
        label = ", ".join(str(p) for p in parts if p) or f"{lat:.4f}, {lon:.4f}"
        bbox  = None
        ext   = props.get("extent")
        if ext and len(ext) == 4:
            try:
                # Photon extent order: [west, north, east, south]
                w, n, e, s = (float(v) for v in ext)
                bbox = (s, n, w, e)
            except (TypeError, ValueError):
                bbox = None
        vague = not props.get("housenumber") and (
            props.get("osm_key") == "highway"
            or props.get("osm_value") == "postcode")
        return {"label": label, "lat": lat, "lon": lon, "bbox": bbox,
                "vague": vague}

    def _postcode_point_lookup(self, postcode, alternates, primary=False):
        """Resolve a full UK postcode to its centroid via postcodes.io —
        authoritative and usually within tens of metres of the address,
        unlike a road-segment midpoint. `alternates` are the street-level
        geocoder hits to keep on offer in the picker; `primary` marks a
        bare-postcode search (its failure path retries the geocoders)."""
        self._abort_geocode_reply()
        self._pc_tried      = True
        self._pc_primary    = primary
        self._pc_alternates = alternates
        if primary:
            self._geocode_query = postcode

        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest
        from qgis.core import QgsNetworkAccessManager

        url = QUrl("https://api.postcodes.io/postcodes/"
                   + postcode.replace(" ", ""))
        req = QNetworkRequest(url)
        req.setRawHeader(b"User-Agent", b"BiomeQGISPlugin/1.0")
        self._harden_geocode_request(req)

        reply = QgsNetworkAccessManager.instance().get(req)
        self._geocode_reply = reply
        reply.finished.connect(self._on_postcode_point_reply)

    def _on_postcode_point_reply(self):
        import json
        from qgis.PyQt.QtNetwork import QNetworkReply

        reply = self.sender()
        if reply is None:
            return

        try:
            if reply is not getattr(self, "_geocode_reply", None):
                return
            self._geocode_reply = None
            alternates = getattr(self, "_pc_alternates", []) or []

            result = None
            if reply.error() == QNetworkReply.NetworkError.NoError:
                try:
                    raw = bytes(reply.readAll()).decode("utf-8",
                                                        errors="replace")
                    r   = json.loads(raw).get("result") or {}
                    lat, lon = r.get("latitude"), r.get("longitude")
                    if lat is not None and lon is not None:
                        result = {"label": (r.get("postcode")
                                            or "postcode centre"),
                                  "lat": float(lat), "lon": float(lon),
                                  "bbox": None}
                except (ValueError, TypeError):
                    result = None

            if result is None:
                # Unknown/terminated postcode or network hiccup — fall back
                # to whatever the geocoders had.
                if alternates:
                    self._present_geocode_results(alternates)
                elif getattr(self, "_pc_primary", False):
                    self._nominatim_search(getattr(self, "_geocode_query", ""),
                                           fallback=True)
                else:
                    self._geocode_not_found()
                return

            self._zoom_to_geocode_result(result)
            if alternates:
                # Keep the street matches available without re-zooming or
                # writing a status line (live=True skips both).
                self._present_geocode_results(alternates, live=True)

        except Exception as exc:
            self.addr_status.setStyleSheet("color:#B3372F; font-size:11px;")
            self.addr_status.setText(f"Error: {exc}")
            self.addr_status.setVisible(True)
        finally:
            reply.deleteLater()

    @staticmethod
    def _dedupe_geocode_results(results):
        """Collapse near-duplicate hits — geocoders return one result per OSM
        way segment, so a single road can fill the picker with identical rows.
        Same label within ~1 km keeps only the first (distinct same-name roads
        in different places keep distinct labels via their locality part)."""
        import math
        kept = []
        for r in results:
            dup = False
            for k in kept:
                if k["label"] != r["label"]:
                    continue
                dy = (k["lat"] - r["lat"]) * 110540.0
                dx = ((k["lon"] - r["lon"]) * 111320.0
                      * math.cos(math.radians(k["lat"])))
                if dx * dx + dy * dy < 1000.0 ** 2:
                    dup = True
                    break
            if not dup:
                kept.append(r)
        return kept

    def _geocode_not_found(self):
        self.addr_status.setStyleSheet("color:#B3372F; font-size:11px;")
        self.addr_status.setText("Location not found. Try a more specific search.")
        self.addr_status.setVisible(True)

    def _present_geocode_results(self, results, live=False):
        """Enter-search with one match → zoom straight there; otherwise show
        the picker. Live (as-you-type) results always go to the picker and
        leave the status line alone to avoid flicker while typing."""
        if not live and len(results) == 1:
            self._zoom_to_geocode_result(results[0])
            return
        self._geocode_results = results
        lst = self.search_results_list
        lst.clear()
        for r in results:
            lst.addItem(r["label"])
        lst.setFixedHeight(min(len(results), 6) * 30 + 8)
        lst.setVisible(True)
        if not live:
            self.addr_status.setStyleSheet("color:#54635A; font-size:11px;")
            self.addr_status.setText(f"{len(results)} matches — pick one:")
            self.addr_status.setVisible(True)

    def _on_search_result_clicked(self, item):
        idx     = self.search_results_list.row(item)
        results = getattr(self, "_geocode_results", [])
        if 0 <= idx < len(results):
            self._zoom_to_geocode_result(results[idx])

    def _zoom_to_geocode_result(self, r):
        src_crs  = QgsCoordinateReferenceSystem("EPSG:4326")
        dest_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        tr       = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
        pt       = tr.transform(QgsPointXY(r["lon"], r["lat"]))

        extent = None
        if r.get("bbox"):
            s, n, w, e = r["bbox"]
            sw     = tr.transform(QgsPointXY(w, s))
            ne     = tr.transform(QgsPointXY(e, n))
            extent = QgsRectangle(sw.x(), sw.y(), ne.x(), ne.y())

        # For specific addresses the bounding box often covers the whole
        # street or town — cap at 500 m so we always land at a useful
        # street-level zoom.
        MAX_EXTENT = 500
        if (extent is None or extent.width() < 200 or extent.height() < 200 or
                extent.width() > MAX_EXTENT or extent.height() > MAX_EXTENT):
            extent = QgsRectangle(
                pt.x() - 300, pt.y() - 300,
                pt.x() + 300, pt.y() + 300,
            )

        self._show_search_marker(pt)
        self.iface.mapCanvas().setExtent(extent)
        self.iface.mapCanvas().refresh()

        # No persistent address/place line on the main page (user preference) —
        # the map zoom + marker is the confirmation. Clear any prior status so it
        # doesn't linger as an empty gap.
        self.addr_status.setText("")
        self.addr_status.setVisible(False)

    def _show_search_marker(self, pt):
        """Drop a temporary marker at a search hit (pt in canvas/3857 coords).
        Cleared by the next click on the map canvas."""
        from qgis.gui import QgsVertexMarker
        self._remove_search_marker()
        canvas = self.iface.mapCanvas()
        m = QgsVertexMarker(canvas)
        m.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
        m.setColor(QColor("#B3372F"))
        try:
            m.setFillColor(QColor(227, 87, 70, 160))
        except AttributeError:
            pass   # setFillColor not available on older QGIS
        m.setIconSize(14)
        m.setPenWidth(3)
        m.setCenter(pt)
        self._search_marker = m

    def _remove_search_marker(self):
        m = self._search_marker
        self._search_marker = None
        if m is not None:
            try:
                self.iface.mapCanvas().scene().removeItem(m)
            except (RuntimeError, AttributeError):
                pass   # canvas or marker already torn down

    def _install_search_dismiss_filter(self):
        """App-wide mouse-press watcher: a click outside the search box or
        suggestion list closes the list; a click on the map canvas clears
        the temporary search marker."""
        if getattr(self, "_search_dismiss_filter", None) is not None:
            return
        from qgis.PyQt.QtCore import QObject, QEvent
        from qgis.PyQt.QtWidgets import QApplication

        panel = self

        class _SearchDismissFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.MouseButtonPress:
                    try:
                        panel._on_global_mouse_press(obj)
                    except RuntimeError:
                        pass   # panel being torn down
                return False

        self._search_dismiss_filter = _SearchDismissFilter()
        QApplication.instance().installEventFilter(self._search_dismiss_filter)

    def _on_global_mouse_press(self, obj):
        """Handle any mouse press in the app (via _SearchDismissFilter)."""
        if not isinstance(obj, QWidget):
            return

        # Close the suggestion list when the click lands outside it and
        # outside the search box.
        lst = getattr(self, "search_results_list", None)
        if lst is not None and lst.isVisible():
            w, inside = obj, False
            while w is not None:
                if w is lst or w is self.grid_ref_input:
                    inside = True
                    break
                w = w.parentWidget()
            if not inside:
                lst.setVisible(False)

        # A click on the map canvas dismisses the search marker.
        if self._search_marker is not None:
            canvas = self.iface.mapCanvas()
            w = obj
            while w is not None:
                if w is canvas:
                    self._remove_search_marker()
                    break
                w = w.parentWidget()

    def _zoom_to_uk(self, immediate=False):
        """Frame the canvas on the project's features, or the UK as a whole.
        ``immediate`` runs the zoom synchronously (callers freeze the canvas
        around the scaffold so the first paint is already framed); the default
        keeps the old 1s-deferred behaviour for mid-session callers."""
        from qgis.PyQt.QtCore import QTimer

        ECO_NAMES = {
            "Ecological Features", "Ecological Linear Features",
            "Ecological Area Features", "Ecological Buffer Zones",
        }

        def do_zoom():
            # SAFETY: never call setDestinationCrs while QGIS is still reading
            # a project file.  On QGIS 3.44 / Windows this triggers
            # projectCrsChanged → QgsMapLayer::crs() on a half-initialised
            # layer → access violation (fatal crash).
            # QgsProject.instance().isDirty() is False during a clean read, but
            # the reliable guard is to check whether the project filename has
            # been set yet (it is set before readProjectFile returns).
            proj = QgsProject.instance()
            try:
                reading = getattr(proj, "isCurrentlyLoading", lambda: False)()
            except Exception:
                reading = False
            if reading:
                # Project still loading — defer and retry
                QTimer.singleShot(500, do_zoom)
                return

            canvas = self.iface.mapCanvas()
            # Only force EPSG:3857 on a brand-new (empty) project.
            # If a project has already been read its CRS is intentional —
            # overwriting it here is what triggers the crash on reopen.
            if not proj.fileName():
                try:
                    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
                except Exception:
                    pass
            target_crs = QgsCoordinateReferenceSystem("EPSG:3857")

            HABITAT_NAMES = {
                "Baseline - Area Habitat", "Proposed - Area Habitat",
                "Baseline - Hedgerow Habitat", "Proposed - Hedgerow Habitat",
                "Baseline - Watercourse Habitat", "Proposed - Watercourse Habitat",
                "Baseline - Trees", "Proposed - Trees",
                "Redline Boundary",
            }

            combined = None
            for lyr in QgsProject.instance().mapLayers().values():
                if not hasattr(lyr, "featureCount") or lyr.featureCount() == 0:
                    continue
                if lyr.name() not in ECO_NAMES and lyr.name() not in HABITAT_NAMES:
                    continue
                try:
                    ext = lyr.extent()
                    if ext is None or ext.isEmpty():
                        continue
                    tr = QgsCoordinateTransform(lyr.crs(), target_crs, QgsProject.instance())
                    ext = tr.transformBoundingBox(ext)
                    if combined is None:
                        combined = QgsRectangle(ext)
                    else:
                        combined.combineExtentWith(ext)
                except Exception:
                    pass

            if combined is not None and not combined.isEmpty():
                pad = max(combined.width(), combined.height()) * 0.15
                combined.grow(max(pad, 50.0))
                canvas.setExtent(combined)
            else:
                canvas.setExtent(QgsRectangle(-700000, 6400000, 300000, 8000000))
            canvas.refresh()

        if immediate:
            do_zoom()
        else:
            QTimer.singleShot(1000, do_zoom)

    def _on_write_project_undo(self, _doc=None):
        """writeProject slot — persists the undo stack into the project file."""
        self._persist_undo_stack()

    def _on_project_cleared_undo(self):
        """cleared slot — the project (and every layer the stacks reference)
        is gone; an entry surviving here could only ever burn clicks."""
        if getattr(self, "_undo_stack", None) or getattr(self, "_redo_stack", None):
            self._undo_stack = []
            self._redo_stack = []
            self._sync_undo_btn()

    def _persist_undo_stack(self):
        """Write the current undo stack into the project so it survives a
        save → close → reopen. Entries are (layer_id, fid, layer_id, fid,
        [extra_fids], [extra_pairs], sigs); we store them as JSON. Restored
        entries are validated against live features on reopen, so a stale
        entry can never delete the wrong feature."""
        try:
            import json
            from qgis.core import QgsProject
            serial = []
            for entry in self._undo_stack:
                b, bf, p, pf, extra, pairs, sigs, rep = self._parse_undo_entry(entry)
                # Only persist entries whose layer refs are id strings — legacy
                # object refs can't be meaningfully reopened anyway.
                b = b if isinstance(b, str) else None
                p = p if isinstance(p, str) else None
                pr_sigs = list((sigs or {}).get("pairs") or [])
                pr_sigs += [None] * (len(pairs) - len(pr_sigs))
                keep = [(lid, fid, pr_sigs[i])
                        for i, (lid, fid) in enumerate(pairs)
                        if isinstance(lid, str)]
                out_sigs = {
                    "b": (sigs or {}).get("b"),
                    "p": (sigs or {}).get("p"),
                    "extra": list((sigs or {}).get("extra") or []),
                    "pairs": [s for _, _, s in keep],
                }
                # Cookie-cutter repairs are already JSON-safe (lids/wkts/sigs
                # are strings, fids ints, readd attrs sanitised at capture).
                serial.append([b, bf, p, pf, list(extra or []),
                               [[lid, fid] for lid, fid, _ in keep], out_sigs,
                               rep if isinstance(rep, dict) else None])
            QgsProject.instance().writeEntry(
                "Biome", "undo_stack", json.dumps(serial))
        except Exception:
            pass

    def _restore_undo_stack(self):
        """Reload a persisted undo stack, dropping entries whose features are
        no longer present (e.g. the GPKG was edited elsewhere)."""
        try:
            import json
            from qgis.core import QgsProject
            raw, ok = QgsProject.instance().readEntry("Biome", "undo_stack", "")
            if not ok or not raw:
                return
            data = json.loads(raw)
        except Exception:
            return

        def _alive(lid, fid, sig=None):
            if lid is None or fid is None:
                return fid is None  # empty slot is fine
            layer = QgsProject.instance().mapLayer(lid)
            if layer is None:
                return False
            try:
                feat = layer.getFeature(fid)
                if feat is None or not feat.isValid():
                    return False
            except Exception:
                return False
            # When an identity sig was recorded, the feature must still BE the
            # recorded one — a matching fid alone isn't enough if the GPKG was
            # edited outside QGIS and the number was recycled.
            if sig:
                return self._feature_sig(layer, fid) == sig
            return True

        restored = []
        for item in data:
            try:
                b, bf, p, pf, extra, pairs, sigs, rep = self._parse_undo_entry(item)
            except Exception:
                continue
            # Keep the entry only if its primary baseline feature still exists.
            # (Proposed twin / extra parts / cookie repairs are best-effort at
            # undo time — each is sig-checked before anything is touched.)
            if bf is not None and not _alive(b, bf, (sigs or {}).get("b")):
                continue
            restored.append((b, bf, p, pf, list(extra or []),
                             [tuple(x) for x in (pairs or [])], sigs or {},
                             rep if isinstance(rep, dict) else None))
        self._undo_stack = restored
        self._sync_undo_btn()

    def _schedule_heal(self, *args):
        """Debounce a project heal (recover orphaned data + collapse duplicates).

        Connected to QgsProject.layersAdded so that however QGIS staggers the
        registration of a project's saved layers, a single heal runs shortly
        after they settle. A burst of layer-adds coalesces into one pass."""
        from qgis.PyQt.QtCore import QTimer
        # Skip while a heal is already running — the layers it adds would
        # otherwise schedule a redundant pass mid-heal.
        if getattr(self.layer_manager, "_healing", False):
            return
        t = getattr(self, "_heal_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._run_scheduled_heal)
            self._heal_timer = t
        t.start(200)

    def _run_scheduled_heal(self):
        """Run the debounced heal; refresh the summary only if it changed
        anything, so a clean project incurs no extra UI work."""
        try:
            changed = self.layer_manager.heal_project_layers()
        except Exception:
            changed = 0
        try:
            from .pond_mapper import recover_persisted_pond_layer
            recover_persisted_pond_layer(self.layer_manager.gpkg_path())
        except Exception:
            pass
        try:
            from .designated_sites import recover_persisted_standing_water_layer
            recover_persisted_standing_water_layer(
                self.layer_manager.gpkg_path())
        except Exception:
            pass
        if changed:
            try:
                self.layer_manager.ensure_tree_structure()
            except Exception:
                pass
            self._mark_summary_dirty()

    def _on_project_read(self):
        """Fired by QgsProject.readProject after a project file is fully loaded.

        Defers all post-load UI refresh to after readProjectFile has returned
        and the event loop is idle.  Does NOT call setTransformContext —
        that is handled by bng_plugin._reset_loading_flag via _suppress_datum_dialog,
        which is itself deferred via QTimer.singleShot(0, ...).
        """
        from qgis.PyQt.QtCore import QTimer
        def _after_read():
            # A different project's features are now loaded. Drop the in-memory
            # stacks from the previous project, then restore THIS project's
            # persisted undo stack (validated against live features). Redo is
            # session-only and never crosses projects.
            self._undo_stack = []
            self._redo_stack = []
            self._sync_undo_btn()
            self._restore_undo_stack()
            # Discard cached DS / Ancient Woodland layers from any previously
            # open project — their underlying layers belong to the old project
            # and must not leak into this one.
            clear_ds_cache()
            # Constraint buffers are keyed to the old project's features — drop
            # both the layers and the accumulated geometry.
            try:
                from .designated_sites import remove_all_constraint_buffers
                remove_all_constraint_buffers()
            except Exception:
                pass
            # Heal base maps: remove duplicate copies and invalid (italic)
            # placeholders left from previous sessions, recreating any basemap
            # that has no working copy. load_base_layers is idempotent.
            import time as _t
            _t0 = _t.perf_counter()
            try:
                load_base_layers(get_os_api_key())
            except Exception:
                pass
            self._initialise_all_layers()
            self.layer_manager.ensure_tree_structure()
            # Start the Ecological Features group collapsed like the other
            # groups, regardless of the expanded state saved in the project.
            self.layer_manager.collapse_eco_group()
            # Reflect the reopened project's saved basemap visibility on the
            # segmented bars (they were synced before the project was read).
            self._sync_basemap_buttons()
            self._refresh_desk_study_stats()
            self._mark_summary_dirty()
            self._zoom_to_uk(immediate=True)
            try:
                from .bng_plugin import _slog
                _slog("open: _after_read heal+refresh took %.1fs"
                      % (_t.perf_counter() - _t0))
            except Exception:
                pass
        QTimer.singleShot(300, _after_read)

    def _initialise_all_layers(self):
        # Heal the project before binding signals / reading features: reconnect
        # any habitat data orphaned in the GeoPackage and collapse duplicate
        # layers / tree nodes. Idempotent — no-op when already clean.
        try:
            self.layer_manager.heal_project_layers()
        except Exception:
            pass
        # Standing-water data has its own GeoPackage table and is deliberately
        # outside LayerManager's habitat-layer catalogue. Recover it explicitly
        # so a stale/omitted memory-layer entry cannot hide saved ponds.
        try:
            from .pond_mapper import (migrate_pond_group_name,
                                      recover_persisted_pond_layer)
            migrate_pond_group_name()
            recover_persisted_pond_layer(self.layer_manager.gpkg_path())
        except Exception:
            pass
        try:
            from .designated_sites import recover_persisted_standing_water_layer
            recover_persisted_standing_water_layer(
                self.layer_manager.gpkg_path())
        except Exception:
            pass
        try:
            from .designated_sites import organise_desk_study_distance_layers
            organise_desk_study_distance_layers()
        except Exception:
            pass
        keys = [
            "redline",
            "site_boundary",
            "baseline_area",     "baseline_water",
            "baseline_hedgerow", "baseline_trees",
            "proposed_area",     "proposed_water",
            "proposed_hedgerow", "proposed_trees",
        ]
        for key in keys:
            layer = self.layer_manager.get_layer(key)   # read-only — don't create
            if layer is None:
                continue
            self._connect_layer_signals(layer)
            # Re-apply styling on project reload.  QGIS restores layers
            # from the GPKG but does NOT persist the in-memory renderer
            # set by _apply_style() (e.g. the red outline for the redline).
            # Without this the redline appears as a default grey polygon
            # after save → close → reopen.
            # from_qml=False: never load the legacy QML during project-open
            # (it crashes QGIS 3.44.x). Habitat layers keep QGIS's restored
            # styling; only the redline/blue-line get their programmatic look.
            self.layer_manager._apply_style(layer, key, from_qml=False)
            layer.triggerRepaint()
        # Ensure all existing layers are visible in the layer tree
        # (on reopen they're in the registry but may not be in the tree)
        self.layer_manager.ensure_layers_in_tree()

    def _recognise_existing_redline(self, _attempt=0):
        """Pick up a redline that was already present when this panel was created.

        Covers the case where the project (and its redline) loaded before the
        panel existed, so the readProject signal never reached this panel. Safe
        to call repeatedly — recomputing the stats is idempotent. Retries a few
        times in case the panel is created just before the layers finish loading."""
        from qgis.PyQt.QtCore import QTimer
        try:
            redline = self.layer_manager.get_layer("redline")
            if redline is None or redline.featureCount() == 0:
                # Layers may still be loading just after startup — retry briefly.
                if _attempt < 4:
                    self._redline_retry_attempt = _attempt + 1
                    timer = getattr(self, "_redline_retry_timer", None)
                    if timer is None:
                        timer = QTimer(self)
                        timer.setSingleShot(True)
                        timer.timeout.connect(self._run_redline_recognition_retry)
                        self._redline_retry_timer = timer
                    timer.start(500)
                return
            # Always repair nested redline features, including after Plugin
            # Reloader recreates the panel in a session whose centroid has
            # already been calculated.  The previous early return skipped the
            # repair in exactly that situation.
            converted = self._convert_nested_redlines_to_holes(redline)
            if converted:
                self.iface.messageBar().pushMessage(
                    "Biome",
                    f"Converted {converted} nested redline feature(s) to "
                    "cut-out islands.",
                    level=Qgis.MessageLevel.Success, duration=6)
            # Already recognised and no geometry repair was needed.
            if getattr(self, "_centroid_bng", None) and not converted:
                return
            self.layer_manager.ensure_tree_structure()
            self._initialise_all_layers()
            self._refresh_desk_study_stats()
            self._mark_summary_dirty()
        except Exception:
            pass

    @staticmethod
    def _convert_nested_redlines_to_holes(layer):
        """Convert committed redline features wholly inside another to holes.

        This also repairs nested redlines created by older plugin versions.
        Separate and merely overlapping application parcels are untouched.
        """
        try:
            features = [f for f in layer.getFeatures()
                        if f.geometry() is not None
                        and not f.geometry().isNull()
                        and not f.geometry().isEmpty()]
            if len(features) < 2:
                return 0
            # Small polygons are considered first and assigned to the smallest
            # containing outer polygon.
            features.sort(key=lambda f: f.geometry().area())
            working = {f.id(): QgsGeometry(f.geometry()) for f in features}
            deletes = []
            for inner in features:
                inner_fid = inner.id()
                if inner_fid in deletes:
                    continue
                inner_geom = working[inner_fid]
                containers = []
                for outer in features:
                    outer_fid = outer.id()
                    if outer_fid == inner_fid or outer_fid in deletes:
                        continue
                    outer_geom = working[outer_fid]
                    if (outer_geom.area() > inner_geom.area()
                            and outer_geom.contains(inner_geom)):
                        containers.append((outer_geom.area(), outer_fid))
                if not containers:
                    continue
                _area, outer_fid = min(containers, key=lambda x: x[0])
                cut = working[outer_fid].difference(inner_geom)
                if (cut is None or cut.isNull() or cut.isEmpty()
                        or not cut.isGeosValid()):
                    continue
                working[outer_fid] = cut
                deletes.append(inner_fid)
            if not deletes:
                return 0
            provider = layer.dataProvider()
            changed = {fid: geom for fid, geom in working.items()
                       if fid not in deletes
                       and any(f.id() == fid for f in features)}
            if not provider.changeGeometryValues(changed):
                return 0
            if not provider.deleteFeatures(deletes):
                return 0
            layer.updateExtents()
            layer.triggerRepaint()
            return len(deletes)
        except Exception:
            return 0

    def _run_redline_recognition_retry(self):
        self._recognise_existing_redline(
            getattr(self, "_redline_retry_attempt", 1))

    def _connect_layer_signals(self, layer):
        """Connect signals to auto-refresh balance and BNG widgets.

        editingStopped   — fires when the edit session ends (covers new features
                           committed via habitat_form).
        committedAttributeValuesChanges — fires when attributes are saved while
                           an edit session stays open (e.g. editing condition in
                           the Manage/Delete dialog or the attribute table).
        committedGeometriesChanges      — fires when geometry edits are saved mid-
                           session (e.g. moving/reshaping a polygon).
        """
        if layer is None:
            return

        def _safe_connect(signal, slot):
            try:
                signal.disconnect(slot)
            except Exception:
                pass
            signal.connect(slot)

        def _refresh_both(_=None, __=None):
            """Single slot used for all mid-session commit signals."""
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self._mark_summary_dirty)
            if layer.name() in ("Redline Boundary", "Blue Line Boundary"):
                QTimer.singleShot(0, self._refresh_desk_study_stats)
            if layer.name() in ("Redline Boundary", "Red Line Boundary"):
                # Distances, IDs, labels, and any visible distance-line layer
                # all depend on the red line.  Keep the nearest water body as
                # SWB-01 even if the boundary is later moved or redrawn.
                def _refresh_water_body_order():
                    try:
                        from qgis.core import QgsProject
                        from .pond_mapper import (
                            POND_LINES_LAYER_NAME, load_pond_distance_lines,
                            renumber_ponds_by_distance, set_pond_lines_visible,
                        )
                        redline_geom = self._get_redline_geom()
                        renumber_ponds_by_distance(
                            redline_geom=redline_geom)
                        line_layers = QgsProject.instance().mapLayersByName(
                            POND_LINES_LAYER_NAME)
                        if line_layers:
                            node = QgsProject.instance().layerTreeRoot().findLayer(
                                line_layers[0].id())
                            was_visible = bool(
                                node and node.itemVisibilityChecked())
                            load_pond_distance_lines(redline_geom)
                            set_pond_lines_visible(was_visible)
                        self.iface.mapCanvas().refresh()
                    except Exception:
                        pass
                QTimer.singleShot(0, _refresh_water_body_order)

        if not hasattr(self, "_layer_refresh_slots"):
            self._layer_refresh_slots = {}
        # Disconnect any previously stored slot for this layer before creating a new one
        if layer.id() in self._layer_refresh_slots:
            old = self._layer_refresh_slots[layer.id()]
            try:
                layer.committedAttributeValuesChanges.disconnect(old)
            except Exception:
                pass
            try:
                layer.committedGeometriesChanges.disconnect(old)
            except Exception:
                pass
        self._layer_refresh_slots[layer.id()] = _refresh_both

        _safe_connect(layer.editingStopped, self._refresh_balance_widget)
        _safe_connect(layer.editingStopped, self._refresh_bng_widget)
        _safe_connect(layer.editingStopped, self._refresh_layers_widget)
        _safe_connect(layer.committedAttributeValuesChanges, _refresh_both)
        _safe_connect(layer.committedGeometriesChanges, _refresh_both)

    def _open_layers_panel(self):
        # In Biome mode the native Layers panel is deliberately hidden and
        # replaced by the plugin's own layer panel — don't fight that here.
        try:
            from . import biome_mode
            if biome_mode.is_enabled():
                return
        except Exception:
            pass
        from qgis.PyQt.QtWidgets import QDockWidget as QDW
        panel = self.iface.mainWindow().findChild(QDW, "Layers")
        if panel:
            panel.setVisible(True)
            panel.raise_()

    def _uncollapse_layer(self, layer):
        """After a feature is committed, expand only the node for `layer`
        and its parent group. Everything else stays collapsed."""
        from qgis.core import QgsProject, QgsLayerTreeLayer, QgsLayerTreeGroup
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node is None:
            return
        # Expand the layer node itself
        node.setExpanded(True)
        # Expand its parent group (if any), collapse all other top-level groups
        parent = node.parent()
        for child in root.children():
            if isinstance(child, QgsLayerTreeGroup):
                if child is parent or (parent is not None and child.findLayer(layer.id()) is not None):
                    child.setExpanded(True)
                else:
                    child.setExpanded(False)
            elif isinstance(child, QgsLayerTreeLayer):
                child.setExpanded(child.layer() and child.layer().id() == layer.id())

    def _require_saved_project(self) -> str:
        """Check whether the project is saved and a GPKG path is available.

        Returns one of:
          "saved"   — project has a GPKG path; proceed normally.
          "scratch" — user chose to continue without saving ('just looking');
                      callers should create memory-backed scratch layers.
          "cancel"  — user dismissed the dialog; abort the operation.

        Offers an inline Save Now button so the user can save without
        dismissing, plus a 'Just looking' button for throwaway sessions.
        """
        if self.layer_manager.gpkg_path():
            return "saved"
        if self.layer_manager.scratch_mode:
            # User already chose "Just looking" this session — don't ask again.
            return "scratch"
        from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QLabel,
                                          QHBoxLayout, QPushButton)
        from qgis.PyQt.QtCore import Qt

        dlg = QDialog(self.iface.mainWindow())
        dlg.setWindowTitle("Save project first")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet("background:#FBF6E6;")

        vl = QVBoxLayout(dlg)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 16)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_svg_pixmap("device-floppy", "#B07D00", 34))
        icon_lbl.setStyleSheet("background:transparent;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(icon_lbl)

        msg = QLabel(
            "<b>Project not saved yet.</b><br><br>"
            "Habitats and features are stored in a GeoPackage alongside the "
            "project file. Save the project first so nothing is lost on exit."
            "<br><br>"
            "Save the QGIS project as either <b>.qgs</b> or <b>.qgz</b>.<br><br>"
            "Or, if you are just having a look, you can continue without "
            "saving — but anything you draw will be <b>lost when you close "
            "the project</b>."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#1A2420; font-size:11px; background:transparent;")
        vl.addWidget(msg)

        btns = QHBoxLayout()
        btns.setSpacing(8)

        save_btn = QPushButton("Save project now…")
        save_btn.setIcon(_svg_icon("device-floppy", "white", 15))
        save_btn.setIconSize(QSize(15, 15))
        save_btn.setStyleSheet(
            "QPushButton{background:#B07D00; color:white; border:none;"
            " border-radius:5px; padding:7px 16px; font-weight:500; font-size:11px;}"
            "QPushButton:hover{background:#9A6700;}"
        )
        scratch_btn = QPushButton("Just looking")
        scratch_btn.setIcon(_svg_icon("eye", "white", 15))
        scratch_btn.setIconSize(QSize(15, 15))
        scratch_btn.setToolTip(
            "Continue without saving. Layers are kept in memory only and "
            "are lost when the project is closed."
        )
        scratch_btn.setStyleSheet(
            "QPushButton{background:#5c6bc0; color:white; border:none;"
            " border-radius:5px; padding:7px 16px; font-weight:500; font-size:11px;}"
            "QPushButton:hover{background:#3949ab;}"
        )
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "QPushButton{background:#eee; color:#54635A; border:none;"
            " border-radius:5px; padding:7px 16px; font-size:11px;}"
            "QPushButton:hover{background:#DDE2DC;}"
        )

        btns.addWidget(save_btn)
        btns.addWidget(scratch_btn)
        btns.addWidget(cancel_btn)
        vl.addLayout(btns)

        result = ["cancel"]

        def _do_save():
            dlg.accept()  # dismiss our dialog first
            self.iface.actionSaveProjectAs().trigger()
            # After the native Save As completes, check if project now has a path
            if QgsProject.instance().fileName():
                result[0] = "saved"

        def _do_scratch():
            result[0] = "scratch"
            self.layer_manager.scratch_mode = True
            dlg.accept()

        save_btn.clicked.connect(_do_save)
        scratch_btn.clicked.connect(_do_scratch)
        cancel_btn.clicked.connect(dlg.reject)

        dlg.exec()
        return result[0]

    def _enable_boundary_habitat_snapping(self, include_redline=False):
        """Temporarily enable snapping for boundary capture.

        Habitat edges are included when the user's boundary-snapping
        preference is enabled.  Blue-line capture can additionally request
        the redline so a nearby ownership-boundary vertex follows the exact
        application-boundary edge.

        Returns a restoration callback.  The callback is idempotent and puts
        back the complete snapping configuration that was active beforehand.
        """
        from .config import get_redline_snap_to_habitats
        snap_to_habitats = get_redline_snap_to_habitats()
        if not snap_to_habitats and not include_redline:
            return lambda: None

        from qgis.core import (QgsProject, QgsSnappingConfig, QgsTolerance,
                               QgsWkbTypes)
        project = QgsProject.instance()
        previous = project.snappingConfig()
        cfg = QgsSnappingConfig()
        cfg.setEnabled(True)
        cfg.setMode(QgsSnappingConfig.SnappingMode.AdvancedConfiguration)
        try:
            setting = QgsSnappingConfig.IndividualLayerSettings()
            setting.setEnabled(True)
            try:
                # QGIS 4 expects the flag enum, not the legacy combined value.
                setting.setTypeFlag(
                    QgsSnappingConfig.SnappingTypes.Vertex |
                    QgsSnappingConfig.SnappingTypes.Segment)
            except (TypeError, AttributeError):
                setting.setTypeFlag(
                    QgsSnappingConfig.SnappingType.VertexAndSegment)
            setting.setTolerance(10)
            setting.setUnits(QgsTolerance.UnitType.Pixels)
        except (TypeError, AttributeError):
            setting = QgsSnappingConfig.IndividualLayerSettings(
                True, QgsSnappingConfig.SnappingType.VertexAndSegment,
                10, QgsTolerance.UnitType.Pixels)

        # Limit candidates to the intended Biome layers.  Including every
        # polygon layer would make desk-study and constraint layers
        # unexpectedly magnetic while a boundary is being drawn.
        target_keys = []
        if snap_to_habitats:
            target_keys.extend(("baseline_area", "proposed_area"))
        if include_redline:
            target_keys.append("redline")
        for key in target_keys:
            lyr = self.layer_manager.get_layer(key)
            if (lyr is not None and lyr.isValid()
                    and QgsWkbTypes.geometryType(lyr.wkbType())
                    == QgsWkbTypes.GeometryType.PolygonGeometry):
                cfg.setIndividualLayerSettings(lyr, setting)
        project.setSnappingConfig(cfg)

        state = {"restored": False}
        def restore():
            if state["restored"]:
                return
            state["restored"] = True
            project.setSnappingConfig(previous)
        return restore

    def _add_redline_boundary(self):
        save_state = self._require_saved_project()
        if save_state == "cancel":
            return
        from qgis.core import QgsEditFormConfig
        if save_state == "scratch":
            layer = self.layer_manager.create_scratch_layer("redline")
        else:
            layer = self.layer_manager.get_or_create_layer("redline")
        self.iface.setActiveLayer(layer)
        cfg = layer.editFormConfig()
        cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOn)
        layer.setEditFormConfig(cfg)
        layer.startEditing()

        restore_redline_snapping = self._enable_boundary_habitat_snapping()

        # Committed features present BEFORE this draw session, so the undo
        # registration below can identify exactly what this session added.
        # (max(fid) guessing registered the OLD boundary whenever the commit
        # failed — and Ctrl+Z would then delete the boundary the user kept.)
        pre_fids = {f.id() for f in layer.getFeatures()}

        # Drop a handler left over from a previous, cancelled draw on this
        # layer — it would otherwise fire on the next unrelated feature add.
        prev = getattr(self, "_redline_on_added", None)
        if prev is not None:
            try:
                layer.featureAdded.disconnect(prev)
            except Exception:
                pass
            self._redline_on_added = None

        def on_added(fid):
            # Disconnect immediately to prevent double-firing
            try:
                layer.featureAdded.disconnect(on_added)
            except Exception:
                pass
            self._redline_on_added = None
            # CRITICAL: defer commitChanges() to the next event loop tick.
            # featureAdded fires while QGIS is still inside its own
            # addFeature/commit pipeline (QgsVectorLayerEditBuffer).
            # Calling commitChanges() synchronously here re-enters that
            # pipeline and causes an access violation on Windows (QGIS 3.x).
            from qgis.PyQt.QtCore import QTimer
            def _do_commit():
                restore_redline_snapping()
                committed = True
                if layer.isEditable():
                    committed = layer.commitChanges()
                cfg2 = layer.editFormConfig()
                cfg2.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOff)
                layer.setEditFormConfig(cfg2)
                if not committed:
                    self.iface.messageBar().pushMessage(
                        "Biome",
                        "The redline couldn't be saved to the layer "
                        "(is the project file locked?). Please try again.",
                        level=Qgis.MessageLevel.Warning, duration=6)
                    return
                # Register with global undo so Ctrl+Z removes the redline —
                # exactly the features this session added, not a fid guess.
                added = [f.id() for f in layer.getFeatures()
                         if f.id() not in pre_fids]
                # Run the cut-out conversion after commit.  GeoPackage feature
                # ids and geometries are not reliably readable while QGIS is
                # still finalising the add-feature edit command.
                hole_repair = None
                if len(added) == 1:
                    try:
                        inner_fid = added[0]
                        inner_feat = layer.getFeature(inner_fid)
                        inner_geom = (QgsGeometry(inner_feat.geometry())
                                      if inner_feat and inner_feat.isValid()
                                      else None)
                        containers = []
                        if (inner_geom is not None and not inner_geom.isNull()
                                and not inner_geom.isEmpty()):
                            for outer_fid in pre_fids:
                                outer_feat = layer.getFeature(outer_fid)
                                if (outer_feat is None
                                        or not outer_feat.isValid()):
                                    continue
                                outer_geom = outer_feat.geometry()
                                if (outer_geom is not None
                                        and not outer_geom.isNull()
                                        and outer_geom.contains(inner_geom)):
                                    containers.append(
                                        (outer_geom.area(), outer_fid,
                                         QgsGeometry(outer_geom)))
                        if containers:
                            _area, outer_fid, before_geom = min(
                                containers, key=lambda x: x[0])
                            cut_geom = before_geom.difference(inner_geom)
                            provider = layer.dataProvider()
                            if (cut_geom is not None and not cut_geom.isNull()
                                    and not cut_geom.isEmpty()
                                    and cut_geom.isGeosValid()
                                    and provider.changeGeometryValues(
                                        {outer_fid: cut_geom})
                                    and provider.deleteFeatures([inner_fid])):
                                layer.updateExtents()
                                layer.triggerRepaint()
                                added = []
                                hole_repair = {
                                    "mods": [[layer.id(), outer_fid,
                                              before_geom.asWkt(),
                                              cut_geom.asWkt()]],
                                    "readds": [],
                                }
                    except Exception:
                        # Leave it as an ordinary separate parcel if the data
                        # provider cannot safely create an interior ring.
                        hole_repair = None
                if hole_repair:
                    # The inner temporary feature was consumed by the cut-out;
                    # undo restores the containing feature's original geometry.
                    self.push_undo(None, None, None, None,
                                   repairs=hole_repair)
                    self.iface.messageBar().pushMessage(
                        "Biome",
                        "Inner redline converted to a cut-out island.",
                        level=Qgis.MessageLevel.Success, duration=5)
                elif added:
                    self.push_undo(layer, added[0], None, None,
                                   extra_baseline_fids=added[1:])
                # Invalidate DS cache — new redline means new 2km search area
                clear_ds_cache()
                # New redline invalidates any constraint buffers built around the
                # old features — remove their layers and accumulated geometry.
                try:
                    from .designated_sites import remove_all_constraint_buffers
                    remove_all_constraint_buffers()
                except Exception:
                    pass
                # Reset all DS toggle buttons so they show as unloaded
                for btn in getattr(self, '_ds_btns', {}).values():
                    btn.setChecked(False)
                # Reset PHI and Ancient Woodland buttons
                for btn in getattr(self, '_phi_btns', {}).values():
                    btn.setChecked(False)
                if hasattr(self, '_aw_btn'):
                    self._aw_btn.setChecked(False)
                self.iface.mapCanvas().refresh()
                self._refresh_desk_study_stats()
                # After stats refresh, centroid is available — check GCN coverage
                QTimer.singleShot(100, self._check_gcn_coverage_warning)
                # Refresh 2km buffer if it was active (centroid has moved)
                if hasattr(self, '_buffer_2km_btn') and self._buffer_2km_btn.isChecked():
                    e2, n2 = self._centroid_bng if hasattr(self, '_centroid_bng') else (0, 0)
                    self._pending_buffer_center = (e2, n2)
                    timer = getattr(self, "_redline_buffer_refresh_timer", None)
                    if timer is None:
                        timer = QTimer(self)
                        timer.setSingleShot(True)
                        timer.timeout.connect(self._refresh_redline_buffer_safely)
                        self._redline_buffer_refresh_timer = timer
                    timer.start(150)
            QTimer.singleShot(0, _do_commit)

        self._redline_on_added = on_added
        layer.featureAdded.connect(on_added)
        self.iface.actionAddFeature().trigger()
        capture_tool = self.iface.mapCanvas().mapTool()
        # If the user abandons capture by changing tools before adding a
        # feature, restore their previous QGIS snapping configuration too.
        def _restore_on_tool_change(new_tool, old_tool):
            if old_tool is capture_tool and new_tool is not capture_tool:
                restore_redline_snapping()
                try:
                    self.iface.mapCanvas().mapToolSet.disconnect(
                        _restore_on_tool_change)
                except Exception:
                    pass
        try:
            self.iface.mapCanvas().mapToolSet.connect(_restore_on_tool_change)
        except Exception:
            pass
        self.iface.messageBar().pushMessage(
            "Biome", "Draw your redline boundary. Right-click to finish.",
            level=Qgis.MessageLevel.Info, duration=5,
        )

    def _refresh_redline_buffer_safely(self):
        """Refresh the active search buffer after a redline edit.

        Kept as a panel-owned timer slot so project changes or panel teardown
        cancel the pending call instead of leaving a receiver-less lambda.
        """
        try:
            easting, northing = getattr(
                self, "_pending_buffer_center", (0, 0))
            geom = self._get_redline_geom()
            if geom is not None and not geom.isEmpty():
                add_2km_buffer(easting, northing, geom)
        except Exception:
            pass

    def _add_site_boundary(self):
        """Draw the wider ownership boundary (Blue Line Boundary).

        Simpler than the redline — no buffer tools, no desk-study cache
        invalidation, no GCN checks.  Just draw → commit → done.
        """
        save_state = self._require_saved_project()
        if save_state == "cancel":
            return
        from qgis.core import QgsEditFormConfig
        if save_state == "scratch":
            layer = self.layer_manager.create_scratch_layer("site_boundary")
        else:
            layer = self.layer_manager.get_or_create_layer("site_boundary")
        self.iface.setActiveLayer(layer)
        cfg = layer.editFormConfig()
        cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOn)
        layer.setEditFormConfig(cfg)
        layer.startEditing()

        # Always include the redline as a snap target.  Habitat-edge snapping
        # remains governed by the user's Boundary snapping preference.
        restore_blueline_snapping = self._enable_boundary_habitat_snapping(
            include_redline=True)

        # See _add_redline_boundary: identify this session's additions by a
        # before/after fid diff rather than a max(fid) guess, and clear any
        # handler left over from a cancelled draw.
        pre_fids = {f.id() for f in layer.getFeatures()}
        prev = getattr(self, "_blueline_on_added", None)
        if prev is not None:
            try:
                layer.featureAdded.disconnect(prev)
            except Exception:
                pass
            self._blueline_on_added = None

        def on_added(fid):
            try:
                layer.featureAdded.disconnect(on_added)
            except Exception:
                pass
            self._blueline_on_added = None
            from qgis.PyQt.QtCore import QTimer
            def _do_commit():
                restore_blueline_snapping()
                committed = True
                if layer.isEditable():
                    committed = layer.commitChanges()
                cfg2 = layer.editFormConfig()
                cfg2.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOff)
                layer.setEditFormConfig(cfg2)
                if not committed:
                    self.iface.messageBar().pushMessage(
                        "Biome",
                        "The blue line boundary couldn't be saved to the "
                        "layer (is the project file locked?). Please try again.",
                        level=Qgis.MessageLevel.Warning, duration=6)
                    return
                added = [f.id() for f in layer.getFeatures()
                         if f.id() not in pre_fids]
                if added:
                    self.push_undo(layer, added[0], None, None,
                                   extra_baseline_fids=added[1:])
                self.iface.mapCanvas().refresh()
                self._refresh_desk_study_stats()
            QTimer.singleShot(0, _do_commit)

        self._blueline_on_added = on_added
        layer.featureAdded.connect(on_added)
        self.iface.actionAddFeature().trigger()
        capture_tool = self.iface.mapCanvas().mapTool()
        # Also restore snapping when an empty/cancelled blue-line capture is
        # abandoned by changing to another map tool.
        def _restore_on_tool_change(new_tool, old_tool):
            if old_tool is capture_tool and new_tool is not capture_tool:
                restore_blueline_snapping()
                try:
                    self.iface.mapCanvas().mapToolSet.disconnect(
                        _restore_on_tool_change)
                except Exception:
                    pass
        try:
            self.iface.mapCanvas().mapToolSet.connect(_restore_on_tool_change)
        except Exception:
            pass
        self.iface.messageBar().pushMessage(
            "Biome",
            "Draw your blue line boundary (wider ownership extent). Right-click to finish.",
            level=Qgis.MessageLevel.Info, duration=5,
        )

    def _zoom_to_site_boundary(self):
        """Recenter and zoom the map canvas to fit the Blue Line Boundary."""
        from qgis.core import QgsCoordinateTransform, QgsProject
        layer = self.layer_manager.get_layer("site_boundary")
        if layer is None or layer.featureCount() == 0:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "No Blue Line Boundary",
                "No Blue Line Boundary found — draw one first using "
                "'Add Blue Line Boundary'.",
            )
            return
        extent = layer.extent()
        src_crs = layer.crs()
        canvas  = self.iface.mapCanvas()
        dst_crs = canvas.mapSettings().destinationCrs()
        if src_crs != dst_crs:
            xform  = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
            extent = xform.transformBoundingBox(extent)
        extent.scale(1.2)
        canvas.setExtent(extent)
        canvas.refresh()

    def _open_habitat_form_prefilled(self, snapshot):
        """Open a HabitatFormScreen pre-populated from a recent-habitat snapshot."""
        import time
        _t0 = time.time()
        phase     = snapshot.get("phase", "baseline")
        hab_key   = snapshot.get("habitat_key", "area")
        if self._require_saved_project() == "cancel":
            return
        from .habitat_form import HabitatFormScreen
        titles = {
            "area":        "Area Habitats",
            "watercourse": "Watercourse Habitat",
            "hedgerow":    "Hedgerow Units",
            "trees":       "Individual Trees",
        }
        prefix = "Proposed " if phase == "proposed" else ""
        title  = prefix + titles.get(hab_key, hab_key)

        _t1 = time.time()
        form = _build_no_gc(lambda: HabitatFormScreen(
            iface=self.iface,
            layer_manager=self.layer_manager,
            habitat_key=hab_key,
            phase=phase,
            title=title,
            parent=self,
        ))
        _t2 = time.time()
        form._apply_snapshot(snapshot)
        _t3 = time.time()

        while self.stack.count() > 3:
            w = self.stack.widget(3)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.addWidget(form)
        self._active_habitat_form = form
        self._navigate_to(self.stack.count() - 1, title)
        _dbg("prefilled %s/%s: setup=%.0f construct=%.0f apply=%.0f nav=%.0f TOTAL=%.0fms"
             % (hab_key, phase, (_t1 - _t0) * 1000, (_t2 - _t1) * 1000,
                (_t3 - _t2) * 1000, (time.time() - _t3) * 1000, (time.time() - _t0) * 1000))

    def _open_habitat_form(self, habitat_key, phase):
        # "saved" → normal GPKG-backed layers; "scratch" → 'just looking',
        # memory-backed layers; "cancel" → user dismissed, do nothing.
        import time
        _t0 = time.time()
        if self._require_saved_project() == "cancel":
            return
        from .habitat_form import HabitatFormScreen
        titles = {
            "area":        "Area Habitats",
            "watercourse": "Watercourse Habitat",
            "hedgerow":    "Hedgerow Units",
            "trees":       "Individual Trees",
        }
        prefix = "Proposed " if phase == "proposed" else ""
        title  = prefix + titles[habitat_key]

        # Reverse mapping (draw a proposed area → auto-write a baseline
        # counterpart, asking the user what was there) was the old paired-area
        # workflow. Areas now derive retained / lost / enhanced from the spatial
        # net of independently-mapped baseline vs proposed polygons, so the
        # reverse flow — and its retention prompt — is disabled.
        _reverse = False
        _t1 = time.time()
        form = _build_no_gc(lambda: HabitatFormScreen(
            iface=self.iface,
            layer_manager=self.layer_manager,
            habitat_key=habitat_key,
            phase=phase,
            title=title,
            parent=self,          # gives form access to push_undo()
            proposed_leads_baseline=_reverse,
        ))
        _t2 = time.time()
        # Screens 0-2 are permanent; form screens start at index 3
        while self.stack.count() > 3:
            w = self.stack.widget(3)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.addWidget(form)
        self._active_habitat_form = form
        self._navigate_to(self.stack.count() - 1, title)
        _dbg("openform %s/%s: setup=%.0f construct=%.0f nav=%.0f TOTAL=%.0fms"
             % (habitat_key, phase, (_t1 - _t0) * 1000, (_t2 - _t1) * 1000,
                (time.time() - _t2) * 1000, (time.time() - _t0) * 1000))

    def _install_pan_tool(self):
        """Install the standard pan tool as the default map tool. Called once at
        startup. The old custom right-click-to-edit tool was removed — feature
        editing is done from the feature manager."""
        from qgis.gui import QgsMapToolPan
        from qgis.PyQt.QtCore import QTimer

        def install():
            canvas = self.iface.mapCanvas()
            self._pan_tool = QgsMapToolPan(canvas)
            canvas.setMapTool(self._pan_tool)

        # Small delay so the canvas is fully initialised before we set the tool.
        QTimer.singleShot(500, install)

    def _open_measurements(self):
        from .measurements import MeasurementsWidget
        # Remove any previous measurements screen (slot 3+)
        while self.stack.count() > 3:
            w = self.stack.widget(3)
            self.stack.removeWidget(w)
            w.deleteLater()
        screen = MeasurementsWidget(self.iface, parent=self)
        self.stack.addWidget(screen)
        self.stack.setCurrentIndex(0)
        self._screen_history.clear()
        self._navigate_to(self.stack.count() - 1, "Measurements")

    def _create_final_map(self):
        import traceback
        try:
            from .map_exporter import MapExportDialog, build_layout
            dlg = MapExportDialog(parent=self.iface.mainWindow())
            if dlg.exec() == QDialog.DialogCode.Accepted:
                vals = dlg.values()
                # Inject redline bbox + centroid so the layout can auto-scale
                _ce, _cn = None, None
                if hasattr(self, "_centroid_bng") and self._centroid_bng:
                    _ce, _cn = self._centroid_bng
                _rg = self._get_redline_geom()
                if _rg:
                    _bb = _rg.boundingBox()
                    vals["redline_bbox"] = (
                        _bb.xMinimum(), _bb.yMinimum(),
                        _bb.xMaximum(), _bb.yMaximum(),
                    )
                else:
                    vals["redline_bbox"] = None
                vals["centroid_e"] = _ce
                vals["centroid_n"] = _cn
                build_layout(self.iface, vals)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Map error: {e}", level=Qgis.MessageLevel.Critical, duration=0
            )
            traceback.print_exc()

    def _export_table(self):
        import traceback
        try:
            from .xlsm_exporter import TableExportDialog, run_export_table
            dlg = TableExportDialog(parent=self.iface.mainWindow())
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            save_path = dlg.save_path()
            if not save_path:
                self.iface.messageBar().pushMessage(
                    "Biome", "Please choose a save location for the table.", level=Qgis.MessageLevel.Warning, duration=5
                )
                return
            run_export_table(self.iface, save_path)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Table export error: {e}", level=Qgis.MessageLevel.Critical, duration=0
            )
            traceback.print_exc()

    def _save_project(self):
        """Save the current QGIS project.

        If the project already has a file, save in place via QGIS's own Save
        action. On a brand-new project (no path yet) route straight to Save As
        so the user can pick a location. Both .qgs and .qgz are supported; the
        habitat data remain in the companion GeoPackage. Always
        defer to QGIS's actions rather than setFileName+write() so we don't
        register a duplicate project window in QGIS's session state.
        """
        from qgis.core import QgsProject
        if QgsProject.instance().fileName():
            self.iface.actionSaveProject().trigger()
            self.iface.messageBar().pushMessage(
                "Biome", "Project saved.", level=Qgis.MessageLevel.Success, duration=3
            )
        else:
            self.iface.messageBar().pushMessage(
                "Biome",
                "Choose a name and location for the QGIS project (.qgs or .qgz).",
                level=Qgis.MessageLevel.Warning, duration=8,
            )
            self.iface.actionSaveProjectAs().trigger()

    def _save_blank_metric(self):
        import traceback
        try:
            from .xlsm_exporter import BlankMetricDialog, copy_blank_metric
            dlg = BlankMetricDialog(parent=self.iface.mainWindow())
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            save_path = dlg.save_path()
            if not save_path:
                self.iface.messageBar().pushMessage(
                    "Biome", "Please choose a save location for the blank metric.", level=Qgis.MessageLevel.Warning, duration=5
                )
                return
            copy_blank_metric(self.iface, save_path)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Failed to save blank metric: {e}", level=Qgis.MessageLevel.Critical, duration=0
            )
            traceback.print_exc()

    def _export_filled_metric(self):
        """Export the statutory metric pre-filled with mapped habitat data."""
        import traceback
        try:
            if not self._geometry_export_guard():
                return
            from .offsite_from_map import prepare_mapped_offsite_export
            proceed, includes_offsite = prepare_mapped_offsite_export(self.iface)
            if not proceed:
                return
            from .xlsm_exporter import MetricExportDialog, run_export_metric
            dlg = MetricExportDialog(
                parent=self.iface.mainWindow(),
                includes_offsite=includes_offsite)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            save_path = dlg.save_path()
            if not save_path:
                self.iface.messageBar().pushMessage(
                    "Biome", "Please choose a save location for the metric.", level=Qgis.MessageLevel.Warning, duration=5
                )
                return
            run_export_metric(self.iface, save_path, dlg.project_details())
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Metric export error: {e}", level=Qgis.MessageLevel.Critical, duration=0
            )
            traceback.print_exc()

    def export_recipe_to_metric(self, recipe):
        """Export the statutory metric pre-filled with the mapped scheme PLUS a
        chosen on-site Enhancements recipe overlaid. Separate from the main
        export button (which exports the mapped scheme only)."""
        import traceback
        try:
            if not self._geometry_export_guard():
                return
            from .offsite_from_map import prepare_mapped_offsite_export
            proceed, includes_offsite = prepare_mapped_offsite_export(self.iface)
            if not proceed:
                return
            from .xlsm_exporter import MetricExportDialog, run_export_metric
            dlg = MetricExportDialog(
                parent=self.iface.mainWindow(),
                includes_offsite=includes_offsite)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            save_path = dlg.save_path()
            if not save_path:
                self.iface.messageBar().pushMessage(
                    "Biome", "Please choose a save location for the metric.", level=Qgis.MessageLevel.Warning, duration=5)
                return
            run_export_metric(self.iface, save_path, dlg.project_details(), recipe=recipe)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Recipe metric export error: {e}", level=Qgis.MessageLevel.Critical, duration=0)
            traceback.print_exc()

    def export_all_recipes(self, recipes):
        """TEST helper — export EVERY recipe to its own metric in a chosen folder,
        each with a matching .log, so they can be bug-checked in one pass instead
        of one at a time. (No project-details prompt; Start tab left blank.)"""
        import os, shutil, traceback
        try:
            from .xlsm_exporter import run_export_metric, _XLOG_PATH
            from qgis.PyQt.QtWidgets import QFileDialog
            folder = QFileDialog.getExistingDirectory(
                self.iface.mainWindow(), "Choose a folder for the test recipe exports")
            if not folder:
                return
            from datetime import date as _date
            stamp = _date.today().strftime("%Y-%m-%d")
            n = 0
            for i, r in enumerate(recipes or [], start=1):
                safe = "".join(c if (c.isalnum() or c in " -_") else "_"
                               for c in (r.get("label") or "recipe")).strip()[:50]
                mod = (r.get("module") or "area")
                base = os.path.join(folder, "%s recipe_%02d %s_%s" % (stamp, i, mod, safe))
                run_export_metric(self.iface, base + ".xlsm", None, recipe=r)
                try:
                    shutil.copyfile(_XLOG_PATH, base + ".log")
                except Exception:
                    pass
                n += 1
            self.iface.messageBar().pushMessage(
                "Biome", "TEST: exported %d recipe metrics (+ .log each) to %s" % (n, folder),
                level=Qgis.MessageLevel.Success, duration=12)
        except Exception as e:
            self.iface.messageBar().pushMessage("Biome", f"Export-all error: {e}", level=Qgis.MessageLevel.Critical, duration=0)
            traceback.print_exc()

    def open_strategic_significance_check(self):
        """Open the site's Local Nature Recovery Strategy (LNRS) so the user can
        confirm whether the location is of strategic significance.

        Strategic significance is a strategy/map judgement, not a national point
        dataset, so we link to the right place to check rather than auto-deciding
        it. We resolve the LNRS area by point-in-polygon against Natural
        England's published "LNRS Areas (England)" layer and open, in order of
        preference:
          - the responsible authority's interactive LNRS map, framed on the site
            (priority layer on, where we have it on file);
          - the official published-strategy link carried in the NE dataset;
          - the national LNRS Areas explorer.
        Falls back to a general web search only if there's no redline to locate."""
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        bbox, centroid = self._redline_bbox_bng()
        if not centroid:
            from urllib.parse import quote_plus
            q = quote_plus("find Local Nature Recovery Strategy map England")
            QDesktopServices.openUrl(QUrl("https://www.google.com/search?q=%s" % q))
            try:
                self.iface.messageBar().pushMessage(
                    "Biome", "No redline to locate — opening a general Local "
                    "Nature Recovery Strategy search.", level=Qgis.MessageLevel.Warning, duration=6)
            except Exception:
                pass
            return

        e, n = centroid
        try:
            url, kind, area_name, authority = build_lnrs_deeplink(e, n, bbox, centroid)
        except Exception:
            url, kind, area_name, authority = (
                "https://naturalengland-defra.opendata.arcgis.com/datasets/"
                "Defra::local-nature-recovery-strategy-areas-england/explore",
                "national", "", "")

        where = (" for the %s LNRS area" % area_name) if area_name else ""
        if kind == "map":
            note = "Opening %s's LNRS map framed on your site%s." % (
                authority or "the responsible authority", where)
        elif kind == "published":
            note = ("Opening the published LNRS%s (%s) — locate your site to "
                    "check priority areas." % (where, authority or "responsible authority"))
        else:
            note = ("Opening the national LNRS Areas explorer%s — find your site "
                    "to see the responsible authority and its published strategy." % where)

        QDesktopServices.openUrl(QUrl(url))
        try:
            self.iface.messageBar().pushMessage("Biome", note, level=Qgis.MessageLevel.Info, duration=8)
        except Exception:
            pass

    def _apply_georeferencer_defaults_to_dialog(self, dialog):
        """Directly set the widgets inside an open Transformation Settings dialog.
        Works by finding the combo boxes by their current item texts, so it is
        robust to any internal QSettings key changes across QGIS versions.
        Target: Thin Plate Spline, EPSG:27700, Cubic resampling, Load in project.
        """
        try:
            from qgis.PyQt.QtWidgets import QComboBox, QCheckBox, QLineEdit
            from qgis.PyQt.QtCore import QTimer

            combos = dialog.findChildren(QComboBox)
            checks = dialog.findChildren(QCheckBox)

            # --- Transformation type combo ---
            # Find the combo that contains transformation type options
            # by looking for one that has "Linear" or "Thin Plate Spline" as items
            for combo in combos:
                items = [combo.itemText(i) for i in range(combo.count())]
                items_lower = [t.lower() for t in items]
                if any('linear' in t for t in items_lower) and any('spline' in t or 'tps' in t for t in items_lower):
                    # Find the Thin Plate Spline index
                    for i, t in enumerate(items_lower):
                        if 'spline' in t or 'tps' in t or 'thin' in t:
                            combo.setCurrentIndex(i)
                            break
                    break

            # --- Resampling method combo ---
            # Find the combo that has "Nearest" and "Cubic" options
            for combo in combos:
                items = [combo.itemText(i) for i in range(combo.count())]
                items_lower = [t.lower() for t in items]
                if any('nearest' in t for t in items_lower) and any('cubic' in t for t in items_lower):
                    for i, t in enumerate(items_lower):
                        # Match "Cubic" but NOT "Cubic Spline" (prefer plain Cubic)
                        if t.strip() == 'cubic' or (t.startswith('cubic') and 'spline' not in t):
                            combo.setCurrentIndex(i)
                            break
                    break

            # --- Target CRS → EPSG:27700 (British National Grid, METRES) ───
            # CRITICAL: the georeferencer MUST use a projected CRS in metres.
            # With a geographic CRS (EPSG:4326, degrees) the GCP solver is
            # numerically singular — the tiny inter-point differences (~1e-4°)
            # are swamped by the large base values (~57°) — and QGIS reports
            # "Transform is not solvable" even with perfectly-placed points.
            #
            # The Target CRS field is a QgsProjectionSelectionWidget, not a
            # plain combo/lineedit, so set it through its real API.
            try:
                from qgis.gui import QgsProjectionSelectionWidget
                from qgis.core import QgsCoordinateReferenceSystem
                bng = QgsCoordinateReferenceSystem("EPSG:27700")
                proj_widgets = dialog.findChildren(QgsProjectionSelectionWidget)
                for pw in proj_widgets:
                    try:
                        pw.setCrs(bng)
                    except Exception:
                        pass
            except Exception:
                pass

            # --- Output file: read the filename to know what layer name to expect ---
            # QGIS names the loaded layer after the file's stem (no extension).
            # We store it so the opacity slider can target it precisely.
            try:
                import os
                for le in dialog.findChildren(QLineEdit):
                    txt = le.text().strip()
                    if any(txt.lower().endswith(ext)
                           for ext in ('.tif', '.tiff', '.img',
                                       '.jpg', '.jpeg', '.png', '.vrt')):
                        stem = os.path.splitext(os.path.basename(txt))[0]
                        if stem:
                            self._georef_expected_layer_name = stem
                        break
            except Exception:
                pass

            # --- Checkboxes: Load in project = ON, Save GCPs = ON,
            #     Set target resolution = OFF (let QGIS compute it from the
            #     solved transform — the pre-filled -999999 vertical value is
            #     invalid and can break the output). ---
            for chk in checks:
                lbl = chk.text().lower()
                if 'load' in lbl and 'project' in lbl:
                    chk.setChecked(True)
                elif 'save' in lbl and 'gcp' in lbl:
                    chk.setChecked(True)
                elif 'target resolution' in lbl:
                    chk.setChecked(False)

        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f"Could not apply georeferencer defaults: {e}", "Biome", Qgis.MessageLevel.Warning
            )

    def _open_georeferencer(self):
        """Launch the QGIS Georeferencer tool.
        A short-lived timer watches for the Transformation Settings dialog to
        appear and immediately sets the rubber-sheet defaults on its widgets.
        Once the georeferenced raster is loaded, LayerManager.promote_stray_rasters
        (run by the debounced heal on layersAdded) lifts it onto its own
        top-level layer if QGIS dropped it inside a habitat group, so every
        georeferenced item is its own separate, visible layer.
        """
        try:
            from qgis.utils import iface
            from qgis.PyQt.QtWidgets import QApplication, QDialog
            from qgis.PyQt.QtCore import QTimer

            # Install a one-shot watcher: when the Transformation Settings dialog
            # appears, apply our defaults directly to its widgets.
            _self = self
            _attempts = [0]

            def _watch_for_transform_dialog():
                _attempts[0] += 1
                for widget in QApplication.topLevelWidgets():
                    if isinstance(widget, QDialog) and widget.isVisible():
                        title = widget.windowTitle()
                        if 'Transformation' in title and 'Settings' in title:
                            _self._apply_georeferencer_defaults_to_dialog(widget)
                            return  # done — don't reschedule
                # Keep polling for up to ~5 seconds (50 × 100 ms)
                if _attempts[0] < 50:
                    QTimer.singleShot(100, _watch_for_transform_dialog)

            QTimer.singleShot(200, _watch_for_transform_dialog)

            # Now trigger the georeferencer
            for action in iface.mainWindow().findChildren(
                    __import__('qgis.PyQt.QtWidgets', fromlist=['QAction']).QAction):
                if 'georeferenc' in action.text().lower():
                    action.trigger()
                    return

            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.information(
                iface.mainWindow(),
                "Georeferencer",
                "Could not find the Georeferencer action.\n\n"
                "Please enable it via: Layer → Georeferencer."
            )
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(f"Georeferencer open error: {e}", "Biome", Qgis.MessageLevel.Warning)

    def _on_redline_buffers_toggle(self, checked):
        """Left-click toggle for the redline buffers:
          unchecked → checked:    create/show buffers using the LAST-USED
                                  settings (no dialog)
          checked   → unchecked:  hide all buffer layers
        Right-click the button to change the buffer settings."""
        self._manual_local_buffers_enabled = bool(checked)
        self._active_local_buffer_preset = "manual" if checked else None
        if not checked:
            self._set_redline_buffers_visible(False)
            return
        # checked — build from the saved settings without prompting
        self._create_redline_buffers(vals=None)

    # (inner m, outer m, outer-only) — mirrors the Search buffer preset menu.
    _REDLINE_BUFFER_PRESETS = (
        (None, 200,  True),
        (250, 500,  False),
        (100, 250,  False),
        (200, 500,  False),
        (500, 1000, False),
        (None, 100,  True),
        (None, 500,  True),
    )

    def _redline_buffers_menu(self, pos):
        """Right-click menu on the redline buffers button — preset ring
        combinations plus the full settings dialog for anything custom."""
        from qgis.PyQt.QtWidgets import QMenu

        def _m(metres):
            return f"{metres} m" if metres < 1000 else f"{metres // 1000} km"

        # Current saved values, to tick the active preset.
        s = QSettings()
        cur1 = int(s.value("RedLineBuffers/dist1", 200))
        cur2 = int(s.value("RedLineBuffers/dist2", 500))
        cur_outer = _RedLineBuffersDialog._to_bool(
            s.value("RedLineBuffers/outerOnly", False))

        menu = QMenu(self)
        head = menu.addAction("Local searches & buffers")
        head.setEnabled(False)
        menu.addSeparator()
        for inner, outer, outer_only in self._REDLINE_BUFFER_PRESETS:
            if outer_only:
                label = f"{_m(outer)} — outer only"
                if outer == 200:
                    label += "  ★ TPO favourite"
                active = cur_outer and cur2 == outer
            else:
                label = f"{_m(inner)} inner + {_m(outer)} outer"
                if inner == 250 and outer == 500:
                    label += "  ★ standing-water body favourite"
                active = (not cur_outer) and cur1 == inner and cur2 == outer
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(active)
            act.triggered.connect(
                lambda _checked=False, i=inner, o=outer, oo=outer_only:
                    self._apply_redline_buffer_preset(i, o, oo))
        menu.addSeparator()
        act = menu.addAction("Buffer settings…")
        act.triggered.connect(self._edit_redline_buffers_settings)
        menu.exec(self._buffers_btn.mapToGlobal(pos))

    def _apply_redline_buffer_preset(self, inner, outer, outer_only):
        """Save a preset ring combination and (re)create the buffers straight
        away. Non-distance options (labels, dashed, delete-old) keep their
        saved values."""
        dlg = _RedLineBuffersDialog(self.iface.mainWindow())
        vals = dlg.get_values()
        if inner is not None:
            vals["dist1"] = int(inner)
        vals["dist2"] = int(outer)
        vals["outerOnly"] = bool(outer_only)
        dlg.save_values(vals)
        self._manual_local_buffers_enabled = True
        self._active_local_buffer_preset = "manual"
        if self._create_redline_buffers(vals=vals) and hasattr(self, '_buffers_btn'):
            self._buffers_btn.setChecked(True)
        if hasattr(self, "_update_search_area_button_labels"):
            self._update_search_area_button_labels()

    def _edit_redline_buffers_settings(self):
        """Open the local-buffers settings dialog; on accept, save the values
        and (re)create the buffers so the change applies immediately."""
        dlg = _RedLineBuffersDialog(self.iface.mainWindow())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.get_values()
        dlg.save_values(vals)
        self._manual_local_buffers_enabled = True
        self._active_local_buffer_preset = "manual"
        # Apply straight away and leave the button on.
        if self._create_redline_buffers(vals=vals) and hasattr(self, '_buffers_btn'):
            self._buffers_btn.setChecked(True)
        if hasattr(self, "_update_search_area_button_labels"):
            self._update_search_area_button_labels()

    def _set_redline_buffers_visible(self, visible):
        """Show or hide all layers in the 'Redline Boundary Buffers' group."""
        from qgis.core import QgsProject
        root = QgsProject.instance().layerTreeRoot()
        grp  = root.findGroup("Redline Boundary Buffers")
        if grp is not None:
            grp.setItemVisibilityChecked(visible)
            self.iface.mapCanvas().refresh()

    def _create_redline_buffers(self, vals=None):
        """Create the redline buffer layers. When `vals` is None the last saved
        settings are used (no dialog). Returns True on success. On any guard
        failure the buffers button is unchecked."""
        if self._require_saved_project() == "cancel":
            if hasattr(self, '_buffers_btn'):
                self._buffers_btn.setChecked(False)
            return False
        redline_layer = self.layer_manager.get_layer("redline")
        if redline_layer is None or redline_layer.featureCount() == 0:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Redline Buffers",
                "No Red Line Boundary features found.\n\n"
                "Please draw a Red Line Boundary first using the "
                "'Add Red Line Boundary' button, then run Redline Buffers.",
            )
            if hasattr(self, '_buffers_btn'):
                self._buffers_btn.setChecked(False)
            return False

        if vals is None:
            # Read the saved settings without showing the dialog.
            vals = _RedLineBuffersDialog(self.iface.mainWindow()).get_values()
        self._set_redline_buffers_visible(True)
        _run_redline_buffers(self.iface, redline_layer, vals)
        return True

    def _refresh_photos_tab(self):
        """Rebuild the Photos tab — shows all features with photo_path set."""
        from qgis.core import QgsProject, QgsFeatureRequest
        from qgis.PyQt.QtWidgets import (
            QScrollArea, QWidget, QVBoxLayout, QHBoxLayout,
            QLabel, QFrame, QPushButton, QSizePolicy,
        )
        from qgis.PyQt.QtGui import QPixmap, QColor
        from qgis.PyQt.QtCore import Qt, QEvent
        from qgis.gui import QgsHighlight
        import os

        iface = self.iface

        class PhotoCard(QWidget):
            """Card that highlights its feature on the map on hover."""
            def __init__(self, layer, fid, parent=None):
                super().__init__(parent)
                self._layer = layer
                self._fid   = fid
                self._hl    = None

            def enterEvent(self, e):
                self._highlight()
                super().enterEvent(e)

            def leaveEvent(self, e):
                self._clear()
                super().leaveEvent(e)

            def _highlight(self):
                self._clear()
                if not self._layer or not self._layer.isValid():
                    return
                req = QgsFeatureRequest().setFilterFid(self._fid)
                feats = list(self._layer.getFeatures(req))
                if not feats:
                    return
                hl = QgsHighlight(iface.mapCanvas(), feats[0], self._layer)
                hl.setColor(QColor(74, 222, 128))   # green accent
                hl.setWidth(4)
                hl.setFillColor(QColor(74, 222, 128, 60))
                hl.show()
                self._hl = hl
                # Pan canvas to feature without changing zoom
                geom = feats[0].geometry()
                if not geom.isEmpty():
                    centre = geom.centroid().asPoint()
                    canvas = iface.mapCanvas()
                    ext = canvas.extent()
                    canvas.setCenter(
                        canvas.mapSettings().layerToMapCoordinates(self._layer, centre)
                    )
                    canvas.refresh()

            def _clear(self):
                if self._hl:
                    self._hl.hide()
                    del self._hl
                    self._hl = None
                    iface.mapCanvas().refresh()

        # Clear existing content
        container = self._photos_container
        old_layout = container.layout()
        while old_layout.count():
            item = old_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # ── Import Field Survey button ─────────────────────────────────────
        import_btn = QPushButton("Import Field Survey")
        import_btn.setIcon(_svg_icon("device-mobile", "#1F4A37", 16))
        import_btn.setIconSize(QSize(16, 16))
        import_btn.setMinimumHeight(44)
        import_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        import_btn.setStyleSheet("""
            QPushButton {
                background:white; color:#1F4A37;
                border:1px solid #E3E7E1; border-radius:10px;
                font-size:12px; font-weight:500;
                padding:8px 6px; margin:10px 10px 4px 10px;
            }
            QPushButton:hover { background:#EAF2EC; border-color:#2E6B4F; }
            QPushButton:pressed { background:#2E6B4F; color:white; }
        """)
        import_btn.setToolTip("Import habitat data from a Biome Field Mapper export (.zip or .geojson)")
        import_btn.clicked.connect(self._import_field_survey)
        old_layout.addWidget(import_btn)

        # Build scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:#F7F8F6; }")

        inner = QWidget()
        inner.setStyleSheet("background:#F7F8F6;")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(8)

        def _hdr_lbl(text):
            l = QLabel(text)
            l.setStyleSheet("color:#1F4A37; font-size:13px; font-weight:500;"
                            " background:transparent; padding:4px 0 2px 0; font-family:'Inter Medium','Inter','Segoe UI',sans-serif;")
            return l

        def _meta_lbl(text):
            l = QLabel(text)
            l.setStyleSheet("color:#54635A; font-size:11px; background:transparent; font-family:'Inter Medium','Inter','Segoe UI',sans-serif;")
            l.setWordWrap(True)
            return l

        # Collect all layers that might have photo_path
        PHOTO_LAYERS = [
            ("baseline_trees",    "Baseline Trees"),
            ("proposed_trees",    "Proposed Trees"),
            ("baseline_area",     "Baseline Area"),
            ("proposed_area",     "Proposed Area"),
            ("baseline_hedgerow", "Baseline Hedgerow"),
            ("proposed_hedgerow", "Proposed Hedgerow"),
        ]

        # Also check eco layers
        eco_names = {"Ecological Features", "Ecological Linear Features",
                     "Ecological Area Features"}

        all_entries = []
        seen_paths = set()

        for key, layer_label in PHOTO_LAYERS:
            lyr = self.layer_manager.get_layer(key)
            if not lyr or not lyr.isValid():
                continue
            pidx = lyr.fields().indexFromName("photo_path")
            if pidx < 0:
                continue
            for feat in lyr.getFeatures():
                path = str(feat["photo_path"] or "").strip()
                if not path or path in seen_paths:
                    continue
                sub = ""
                sidx = feat.fieldNameIndex("sub_habitat_type")
                if sidx >= 0:
                    sub = str(feat["sub_habitat_type"] or "").strip()
                label = ""
                # Try label first, fall back to user_comments (trees use this)
                for lname in ("label", "user_comments"):
                    lidx = feat.fieldNameIndex(lname)
                    if lidx >= 0:
                        label = str(feat[lname] or "").strip()
                        if label:
                            break
                seen_paths.add(path)
                all_entries.append({
                    "layer": layer_label, "path": path,
                    "sub": sub, "label": label, "fid": feat.id(),
                    "layer_obj": lyr
                })

        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() in eco_names:
                pidx = lyr.fields().indexFromName("photo_path")
                if pidx < 0:
                    continue
                for feat in lyr.getFeatures():
                    path = str(feat["photo_path"] or "").strip()
                    if not path or path in seen_paths:
                        continue
                    ft = ""
                    # Eco layers use 'ftype', habitat layers use 'sub_habitat_type'
                    for fname in ("ftype", "feature_type", "sub_habitat_type"):
                        fidx = feat.fieldNameIndex(fname)
                        if fidx >= 0:
                            ft = str(feat[fname] or "").strip()
                            if ft:
                                break
                    label = ""
                    lidx = feat.fieldNameIndex("label")
                    if lidx >= 0:
                        label = str(feat["label"] or "").strip()
                    seen_paths.add(path)
                    all_entries.append({
                        "layer": lyr.name(), "path": path,
                        "sub": ft, "label": label, "fid": feat.id(),
                        "layer_obj": lyr
                    })

        if not all_entries:
            empty = QLabel("No photos linked to features yet.\n\n"
                           "Import a field survey with photos using\n"
                           "Import Field Survey above.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet("color:#98A29B; font-size:12px; padding:40px 20px;")
            layout.addWidget(empty)
        else:
            layout.addWidget(_banner("camera",
                f"{len(all_entries)} linked photo{'s' if len(all_entries)!=1 else ''}",
                fg="#1F4A37", bg="transparent", font_px=13, weight="500",
                margins=(0, 4, 0, 2), spacing=7))

            for entry in all_entries:
                card = PhotoCard(entry.get("layer_obj"), entry["fid"])
                card.setStyleSheet(
                    "background:white; border-radius:8px;"
                    " border:1px solid #E3E7E1;"
                )
                card.setCursor(Qt.CursorShape.PointingHandCursor)
                card_l = QVBoxLayout()
                card_l.setContentsMargins(0, 0, 0, 0)
                card_l.setSpacing(0)

                # Photo
                photo_lbl = QLabel()
                photo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                photo_lbl.setStyleSheet("background:#f0f0f0; border-radius:8px 8px 0 0;")
                photo_lbl.setFixedHeight(180)

                path = entry["path"]
                if os.path.isfile(path):
                    pix = QPixmap(path)
                    if not pix.isNull():
                        photo_lbl.setPixmap(
                            pix.scaled(280, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        )
                    else:
                        photo_lbl.setText("Could not load image")
                        photo_lbl.setStyleSheet("color:#B3372F; font-size:11px;"
                                                " background:#FBEDEB; border-radius:8px 8px 0 0;")
                else:
                    photo_lbl.setText(f"File not found:\n{os.path.basename(path)}")
                    photo_lbl.setStyleSheet("color:#717E75; font-size:10px; padding:8px;"
                                            " background:#F4F6F2; border-radius:8px 8px 0 0;")

                card_l.addWidget(photo_lbl)

                # Info row
                info = QWidget()
                info.setStyleSheet("background:transparent;")
                info_l = QVBoxLayout()
                info_l.setContentsMargins(10, 8, 10, 10)
                info_l.setSpacing(4)

                # Feature name as title
                title = entry["sub"] or "Unnamed feature"
                info_l.addWidget(_hdr_lbl(title))

                # Layer badge
                info_l.addWidget(_banner("map-pin", entry['layer'],
                    fg="#54635A", bg="transparent", font_px=11, size=13,
                    margins=(0, 0, 0, 0), spacing=5))

                # Note — shown prominently if present
                if entry["label"]:
                    note_lbl = QLabel(entry['label'])
                    note_lbl.setWordWrap(True)
                    note_lbl.setStyleSheet(
                        "color:#1A2420; font-size:12px; background:#F1F4F0;"
                        " border-radius:6px; padding:5px 8px; border:none;"
                    )
                    info_l.addWidget(note_lbl)

                # Photo filename
                info_l.addWidget(_banner("file", os.path.basename(path),
                    fg="#54635A", bg="transparent", font_px=11, size=13,
                    margins=(0, 0, 0, 0), spacing=5))

                info.setLayout(info_l)
                card_l.addWidget(info)
                card.setLayout(card_l)
                layout.addWidget(card)

        layout.addStretch()
        inner.setLayout(layout)
        scroll.setWidget(inner)
        old_layout.addWidget(scroll)

    def _import_field_survey(self):
        """Import a Biome Field Mapper export (.zip or .geojson).

        The heavy lifting lives in field_mapper_import — routing to habitat /
        ecological / condition / HSI layers, photo resolution and the metric
        attribute defaults.
        """
        import json, os, zipfile, tempfile
        from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
        from . import field_mapper_import as fmi

        if self._require_saved_project() == "cancel":
            return

        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "Import Field Survey",
            "",
            "Biome Field Survey (*.zip *.geojson *.json)"
        )
        if not path:
            return

        survey_dir = os.path.dirname(path)

        # ── Unzip if needed ───────────────────────────────────────────
        if path.lower().endswith('.zip'):
            try:
                tmp_dir = tempfile.mkdtemp(prefix='biome_import_')
                with zipfile.ZipFile(path, 'r') as z:
                    z.extractall(tmp_dir)
                geojson_path = None
                for root_dir, _dirs, files in os.walk(tmp_dir):
                    for fname in files:
                        if fname.lower().endswith(('.geojson', '.json')):
                            geojson_path = os.path.join(root_dir, fname)
                            survey_dir = root_dir  # photos sit next to the geojson
                            break
                    if geojson_path:
                        break
                if not geojson_path:
                    QMessageBox.warning(self.iface.mainWindow(), "Biome",
                                        "No GeoJSON found inside the zip.")
                    return
                path = geojson_path
            except Exception as e:
                QMessageBox.warning(self.iface.mainWindow(), "Biome",
                                    f"Could not open zip:\n{e}")
                return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self.iface.mainWindow(), "Biome",
                                f"Could not read GeoJSON:\n{e}")
            return

        if not data.get('features'):
            QMessageBox.information(self.iface.mainWindow(), "Biome",
                                    "No features found in file.")
            return

        summary = fmi.import_survey(self, data, survey_dir=survey_dir)
        self._after_field_survey_import(summary)

    def _after_field_survey_import(self, summary):
        """Zoom to what arrived and report it. Shared by the file import and
        the phone's direct push."""
        from qgis.PyQt.QtWidgets import QMessageBox
        from qgis.core import QgsProject
        from . import field_mapper_import as fmi

        # Zoom to the first layer that actually gained features.
        for key in ("baseline_area", "baseline_hedgerow", "baseline_water",
                    "baseline_trees"):
            lyr = self.layer_manager.get_layer(key)
            if lyr is not None and lyr.isValid() and lyr.featureCount():
                self.iface.setActiveLayer(lyr)
                self.iface.zoomToActiveLayer()
                break
        else:
            for lyr in QgsProject.instance().mapLayers().values():
                if lyr.name() == fmi.LAYER_NAME and lyr.isValid() and lyr.featureCount():
                    self.iface.setActiveLayer(lyr)
                    self.iface.zoomToActiveLayer()
                    break
        self.iface.mapCanvas().refresh()

        # Same deferred refresh trio the habitat form fires after a placement,
        # so imported parcels reach the balance, dashboard and phase screens.
        from qgis.PyQt.QtCore import QTimer
        for name in ("_refresh_balance_widget", "_refresh_bng_widget",
                     "_refresh_phase_screens"):
            fn = getattr(self, name, None)
            if fn is not None:
                QTimer.singleShot(0, fn)

        QMessageBox.information(self.iface.mainWindow(), "Biome — Import",
                                "Import complete:\n" + fmi.summary_text(summary))


    def _open_plan_annotator(self):
        """Open the non-georeferenced elevation/plan annotation workspace."""
        if not hasattr(self, "_plan_annotator_dlg") or self._plan_annotator_dlg is None:
            from .plan_annotator import PlanAnnotatorDialog
            self._plan_annotator_dlg = PlanAnnotatorDialog(self.iface.mainWindow())
        self._plan_annotator_dlg.show()
        self._plan_annotator_dlg.raise_()
        self._plan_annotator_dlg.activateWindow()

    def _open_ecological_features(self):
        """Open the Ecological Feature Placer dialog (singleton per panel).
        Always returns the dock to the home screen first so Back is unambiguous."""
        # Snap back to home screen so the history stack is clean
        self.stack.setCurrentIndex(0)
        self._set_header_title("Biome")
        self.back_btn.setVisible(False)
        self._tab_bar.setVisible(True)
        self._screen_history.clear()

        if not hasattr(self, "_eco_dlg") or self._eco_dlg is None:
            from .gridref_dialog import GridRefDialog
            self._eco_dlg = GridRefDialog(self.iface, self.iface.mainWindow())
            self._eco_dlg._main_panel = self  # bridge to global undo
        self._eco_dlg.show()
        self._eco_dlg.raise_()
        self._eco_dlg.activateWindow()

    def _switch_basemap(self, layer_name):
        """Make `layer_name` the only visible basemap; hide the others.

        Operates on EVERY layer carrying each basemap name — if duplicates
        exist (e.g. from an older project), all copies of the others are
        switched off, so the previous basemap can never linger visible.

        Hybrid and Satellite also repair a partially initialised first-run
        project by loading the standard base layers on demand."""
        from qgis.core import QgsProject
        from . import zoomstack as oszoom
        basemap_names = {"OS Outdoor", "OS Premium", "Google Hybrid",
                         "Google Satellite", "ESRI World Imagery"}
        project = QgsProject.instance()
        if layer_name in {"Google Hybrid", "Google Satellite"} \
                and not project.mapLayersByName(layer_name):
            load_base_layers(get_os_api_key())
        root = QgsProject.instance().layerTreeRoot()
        base_group = root.findGroup("Base Maps")
        if base_group is not None:
            base_group.setItemVisibilityChecked(True)
        zs_group = root.findGroup(oszoom.ZOOMSTACK_GROUP)
        if zs_group is not None:
            zs_group.setItemVisibilityChecked(layer_name == oszoom.ZOOMSTACK_GROUP)
        for name in basemap_names:
            on = (name == layer_name)
            for lyr in QgsProject.instance().mapLayersByName(name):
                node = root.findLayer(lyr.id())
                if node is not None:
                    node.setItemVisibilityChecked(on)
        self._sync_basemap_buttons()
        self.iface.mapCanvas().refresh()

    def _on_zoomstack_clicked(self):
        """Switch to the offline OS Zoomstack basemap, downloading and/or
        loading it first if needed. Data lives in the QGIS profile folder so
        plugin reinstalls never trigger a re-download."""
        from qgis.core import QgsProject
        from . import zoomstack as oszoom

        root = QgsProject.instance().layerTreeRoot()
        grp = root.findGroup(oszoom.ZOOMSTACK_GROUP)
        if grp is not None:
            if grp.isVisible():
                # Already the active basemap — a second click opens the
                # per-feature visibility popover instead of re-switching.
                self._sync_basemap_buttons()
                self._open_zoomstack_features(self.sender())
                return
            self._switch_basemap(oszoom.ZOOMSTACK_GROUP)
            return

        if oszoom.is_installed():
            grp = oszoom.load_zoomstack_basemap(visible=True)
            if grp is None:
                self.iface.messageBar().pushWarning(
                    "OS Zoomstack", "Could not load the downloaded basemap — "
                    "see the Biome log for details.")
                self._sync_basemap_buttons()
                return
            self._switch_basemap(oszoom.ZOOMSTACK_GROUP)
            return

        # Not installed yet — run the downloader
        self._zs_installer = oszoom.ZoomstackInstaller(self)

        def on_finished(ok, msg):
            if ok:
                grp = oszoom.load_zoomstack_basemap(visible=True)
                if grp is not None:
                    self._switch_basemap(oszoom.ZOOMSTACK_GROUP)
                    self.iface.messageBar().pushSuccess(
                        "OS Zoomstack", "Offline OS basemap installed and loaded.")
                else:
                    self.iface.messageBar().pushWarning(
                        "OS Zoomstack", "Downloaded, but the basemap failed to load.")
            elif msg not in ("cancelled",):
                self.iface.messageBar().pushWarning(
                    "OS Zoomstack", "Install did not complete.")
            self._sync_basemap_buttons()

        self._zs_installer.finished.connect(on_finished)
        self._zs_installer.start()
        self._sync_basemap_buttons()

    def _open_zoomstack_features(self, anchor_btn=None):
        """Popover of per-feature visibility toggles for the offline basemap
        (roads, railways, airports, labels…). Anchored under the Offline
        button; choices persist and re-apply on every basemap load."""
        from . import zoomstack as oszoom
        if not oszoom.is_installed():
            self.iface.messageBar().pushInfo(
                "OS Zoomstack",
                "Download the offline basemap first — click the Offline button.")
            return
        if anchor_btn is None:
            anchor_btn = self.sender()
        # Keep a reference: the popover is a parentless Qt.Popup window and
        # would be garbage-collected mid-display otherwise.
        self._zs_feature_popover = oszoom.show_feature_popover(
            anchor_btn, self.iface)

    def _on_os_outdoor_clicked(self):
        """OS Outdoor basemap — gated behind a validated OS Data Hub API key.

        If no key is stored yet, opens the settings dialog (which validates and
        stores the key); then adds the OS Outdoor layer and makes it active.
        """
        from . import base_layers as bl

        key = get_os_api_key()
        if not key:
            self._open_os_key_dialog()
            key = get_os_api_key()
            if not key:
                self._sync_basemap_buttons()   # cancelled — un-press the button
                return

        if not bl.ensure_os_outdoor(key):
            self.iface.messageBar().pushWarning(
                "OS Outdoor",
                "Could not load OS Outdoor with that key — open settings "
                "(cog, top right) to re-enter it.")
            self._sync_basemap_buttons()
            return
        self._switch_basemap("OS Outdoor")

    def _on_os_premium_clicked(self, anchor_btn=None):
        """Activate OS Premium, or reopen its choices when already active."""
        from qgis.core import QgsProject
        from . import base_layers as bl

        if anchor_btn is None:
            anchor_btn = self.sender()
        root = QgsProject.instance().layerTreeRoot()
        active = any(
            root.findLayer(layer.id()) is not None
            and root.findLayer(layer.id()).isVisible()
            for layer in QgsProject.instance().mapLayersByName("OS Premium"))
        if active:
            self._sync_basemap_buttons()
            self._open_os_premium_menu(anchor_btn)
            return

        key = get_os_ngd_api_key()
        if not key:
            self._open_os_ngd_key_dialog()
            key = get_os_ngd_api_key()
            if not key:
                self._sync_basemap_buttons()
                return
        if not bl.ensure_os_ngd_premium(key, get_os_ngd_basemap_style()):
            self.iface.messageBar().pushWarning(
                "OS Premium", "Could not load the premium basemap. Check that "
                "OS NGD API – Tiles is enabled for the saved project key.")
            self._sync_basemap_buttons()
            return
        self._switch_basemap("OS Premium")

    def _make_os_premium_menu(self, parent=None):
        """Build the light style/settings menu for an OS Premium split button."""
        from qgis.PyQt.QtWidgets import QMenu
        from . import base_layers as bl

        menu = QMenu(parent or self)
        menu.setTitle("OS Premium")
        menu.setStyleSheet(
            "QMenu{background:#FFFFFF;color:#20372B;border:1px solid #AEBDB3;"
            "padding:5px;}"
            "QMenu::item{background:transparent;color:#20372B;padding:7px 30px 7px 24px;"
            "border-radius:4px;}"
            "QMenu::item:selected{background:#E4F0E8;color:#153F2C;}"
            "QMenu::item:disabled{color:#89958E;}"
            "QMenu::separator{height:1px;background:#D9E1DB;margin:5px 8px;}"
            "QMenu::indicator:checked{width:8px;height:8px;background:#1F6A49;"
            "border:1px solid #15533A;border-radius:4px;}")
        current = get_os_ngd_basemap_style()
        choices = (("Road", "road"), ("Outdoor", "outdoor"),
                   ("Light", "light"), ("Black and White", "blackwhite"))
        for label, value in choices:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == current)

            def _choose(_checked=False, selected=value):
                set_os_ngd_basemap_style(selected)
                from qgis.core import QgsProject
                root = QgsProject.instance().layerTreeRoot()
                active = any(
                    root.findLayer(layer.id()) is not None
                    and root.findLayer(layer.id()).isVisible()
                    for layer in QgsProject.instance().mapLayersByName(
                        "OS Premium"))
                if not active:
                    return
                key = get_os_ngd_api_key()
                if key and bl.ensure_os_ngd_premium(key, selected):
                    self._switch_basemap("OS Premium")

            action.triggered.connect(_choose)
        menu.addSeparator()
        settings_action = menu.addAction("Premium API settings…")
        settings_action.triggered.connect(
            lambda: self._open_os_ngd_key_dialog())
        return menu

    def _open_os_premium_menu(self, anchor_btn=None):
        """Open the Premium menu without changing the active basemap."""
        from qgis.PyQt.QtCore import QPoint
        menu = self._make_os_premium_menu(anchor_btn)
        if anchor_btn is not None:
            menu.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))
        else:
            menu.exec()

    def _validate_os_key(self, key):
        """Check an OS Data Hub key against the OS Maps API.

        Returns (status, message):
          True  — accepted (HTTP 200);
          False — rejected (401/403) or an unexpected HTTP response;
          None  — couldn't reach OS to verify (offline/proxy); the caller may
                  store the key anyway and let it be tried later.
        """
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest
        from qgis.core import QgsBlockingNetworkRequest
        # A single low-zoom tile over England: 200 = key works, 401/403 = bad.
        url = f"https://api.os.uk/maps/raster/v1/zxy/Outdoor_3857/7/63/41.png?key={key}"
        try:
            blocking = QgsBlockingNetworkRequest()
            blocking.get(QNetworkRequest(QUrl(url)))
            reply  = blocking.reply()
            status = (reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                      if reply is not None else None)
        except Exception as exc:
            return None, f"Could not run validation ({exc})."
        if status == 200:
            return True, "Key valid — OS Outdoor is ready."
        if status in (401, 403):
            return False, (f"Key rejected by Ordnance Survey (HTTP {status}). Check the "
                           "key and that the OS Maps API is added to your Data Hub project.")
        if status is None:
            return None, "Couldn't reach Ordnance Survey to validate (offline/proxy?)."
        return False, f"Unexpected response from OS (HTTP {status})."

    def _make_basemap_bar(self):
        """One-row segmented basemap selector: Hybrid · Satellite · OS Outdoor
        · ESRI · Offline. Reusable — each instance registers its buttons so
        _sync_basemap_buttons keeps every bar in step."""
        if not hasattr(self, "_bm_bars"):
            self._bm_bars = []
            self._zs_btns = []
            self._basemap_selector_buttons = []
            self._basemap_bar_widgets = []
        bar = QWidget()
        bar.setObjectName("bmbar")
        bar.setFixedHeight(32)
        bar.setStyleSheet(
            "QWidget#bmbar { background:white; border:1px solid #C9D1C9; border-radius:8px; }"
            "QPushButton, QToolButton { background:transparent; color:#3D4B43; border:none;"
            " border-right:1px solid #EDF0EC; border-radius:0;"
            " font-size:11px; font-weight:500; padding:0 4px;"
            " font-family:'Inter Medium','Inter','Segoe UI',sans-serif; }"
            "QPushButton#seg_first, QToolButton#seg_first { border-top-left-radius:7px; border-bottom-left-radius:7px; }"
            "QPushButton#seg_last, QToolButton#seg_last  { border-right:none;"
            " border-top-right-radius:7px; border-bottom-right-radius:7px; }"
            "QPushButton:checked, QToolButton:checked { background:#1F4A37; color:white; }"
            "QPushButton:hover:!checked, QToolButton:hover:!checked { background:#EAF2EC; color:#1F4A37; }"
            "QToolButton::menu-button { border-left:1px solid #DDE5DF; width:15px; }"
            "QToolButton[splitActive='true']::menu-button { border-left:1px solid #527565; }"
            "QToolButton::menu-arrow { width:7px; height:7px; }"
        )
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(1, 1, 1, 1)
        hl.setSpacing(0)
        # Order: Hybrid · Satellite · Offline · ESRI · OS Outdoor · Premium.
        # OS Outdoor sits LAST because it needs a user-supplied OS Data Hub key
        # (prompted on first use); the keyless Offline (OS Open Zoomstack) takes
        # the prominent slot. Sentinel layer_name "__offline__" = Zoomstack button.
        segs = [
            ("Hybrid",     "Google Hybrid",      "Google Hybrid satellite + labels basemap"),
            ("Satellite",  "Google Satellite",   "Google Satellite imagery basemap"),
            ("Offline",    "__offline__",        "Offline Ordnance Survey basemap (OS Open Zoomstack). "
                                                 "First use downloads ~3.8 GB once."),
            ("ESRI",       "ESRI World Imagery", "ESRI World Imagery basemap"),
            ("OS Outdoor", "OS Outdoor",         "Ordnance Survey Outdoor basemap "
                                                 "(needs a free OS Data Hub API key — you'll be prompted)"),
            ("Premium",    "OS Premium",         "Detailed OS NGD premium vector basemap. "
                                                 "Use the arrow for styles and settings without switching maps."),
        ]
        by_name = {item[1]: item for item in segs}
        segs = [by_name[name] for name in get_basemap_button_order()
                if name in by_name]
        buttons = {}
        last = len(segs) - 1
        for i, (label, layer_name, tip) in enumerate(segs):
            btn = _BasemapToolButton(
                layer_name, self._move_basemap_button,
                self._save_basemap_button_order, bar)
            btn.setText(label)
            btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setMinimumWidth(30)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            if i == 0:
                btn.setObjectName("seg_first")
            elif i == last:
                btn.setObjectName("seg_last")
            self._wire_hint(btn, tip)
            if layer_name == "__offline__":
                btn.clicked.connect(self._on_zoomstack_clicked)
                btn.setMenu(QMenu(btn))
                btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                btn._arrow_callback = self._open_zoomstack_features
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda _pos, b=btn: self._open_zoomstack_features(b))
                self._zs_btns.append(btn)
            elif layer_name == "OS Outdoor":
                btn.clicked.connect(lambda checked: self._on_os_outdoor_clicked())
                buttons[layer_name] = btn
            elif layer_name == "OS Premium":
                btn.clicked.connect(
                    lambda checked, b=btn: self._on_os_premium_clicked(b))
                btn.setMenu(self._make_os_premium_menu(btn))
                btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda _pos, b=btn: self._open_os_premium_menu(b))
                buttons[layer_name] = btn
            else:
                btn.clicked.connect(lambda checked, n=layer_name: self._switch_basemap(n))
                buttons[layer_name] = btn
            hl.addWidget(btn, 1)
            self._basemap_selector_buttons.append((layer_name, btn))
        self._bm_bars.append(buttons)
        self._basemap_bar_widgets.append(bar)
        self._apply_basemap_button_visibility()
        return bar

    def _move_basemap_button(self, source, global_pos):
        """Reorder all basemap bars as a selector is dragged across one bar."""
        bar = source.parentWidget()
        layout = bar.layout() if bar is not None else None
        if layout is None:
            return
        local_x = bar.mapFromGlobal(global_pos).x()
        target = None
        after = False
        for i in range(layout.count()):
            candidate = layout.itemAt(i).widget()
            if candidate is None or candidate is source or not candidate.isVisible():
                continue
            rect = candidate.geometry()
            if local_x <= rect.right():
                target = candidate
                after = local_x > rect.center().x()
                break
        if target is None:
            visible = [layout.itemAt(i).widget() for i in range(layout.count())
                       if layout.itemAt(i).widget() is not None
                       and layout.itemAt(i).widget() is not source
                       and layout.itemAt(i).widget().isVisible()]
            if not visible:
                return
            target, after = visible[-1], True

        order = list(getattr(self, "_basemap_order", get_basemap_button_order()))
        source_key = source.basemap_key
        target_key = target.basemap_key
        order.remove(source_key)
        insert_at = order.index(target_key) + (1 if after else 0)
        order.insert(insert_at, source_key)
        if order == getattr(self, "_basemap_order", None):
            return
        self._basemap_order = order
        self._apply_basemap_button_order()

    def _apply_basemap_button_order(self):
        """Apply the current selector order to every Mapping/Desk Study bar."""
        order = list(getattr(self, "_basemap_order", get_basemap_button_order()))
        for bar in list(getattr(self, "_basemap_bar_widgets", [])):
            layout = bar.layout()
            widgets = {}
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if widget is not None:
                    widgets[getattr(widget, "basemap_key", "")] = widget
            for index, key in enumerate(order):
                if key in widgets:
                    layout.insertWidget(index, widgets[key], 1)
            visible = [widgets[key] for key in order
                       if key in widgets and widgets[key].isVisible()]
            for widget in widgets.values():
                widget.setObjectName("")
            if visible:
                visible[0].setObjectName("seg_first")
                visible[-1].setObjectName("seg_last")
            for widget in widgets.values():
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def _save_basemap_button_order(self):
        set_basemap_button_order(
            getattr(self, "_basemap_order", get_basemap_button_order()))

    def _apply_basemap_button_visibility(self):
        """Apply the saved basemap selector choices to every existing bar."""
        enabled = get_enabled_basemap_buttons()
        for layer_name, button in list(
                getattr(self, "_basemap_selector_buttons", [])):
            button.setVisible(layer_name in enabled)
        any_enabled = bool(enabled)
        for bar in list(getattr(self, "_basemap_bar_widgets", [])):
            bar.setVisible(any_enabled)
        self._basemap_order = get_basemap_button_order()
        self._apply_basemap_button_order()

    def _sync_basemap_buttons(self):
        """Update button checked states to reflect current layer visibility.
        Handles every registered basemap bar (Mapping + Desk Study)."""
        from qgis.core import QgsProject
        root = QgsProject.instance().layerTreeRoot()
        bars = list(getattr(self, "_bm_bars", []))
        if hasattr(self, "_bm_buttons"):
            bars.append(self._bm_buttons)
        for bar in bars:
            for name, btn in bar.items():
                visible = False
                for lyr in QgsProject.instance().mapLayersByName(name):
                    node = root.findLayer(lyr.id())
                    if node is not None and node.isVisible():
                        visible = True
                        break
                btn.blockSignals(True)
                btn.setChecked(visible)
                btn.setProperty("splitActive", "true" if visible else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
                btn.blockSignals(False)
        # Zoomstack offline basemap buttons
        zs_btns = list(getattr(self, "_zs_btns", []))
        if hasattr(self, "_zs_btn"):
            zs_btns.append(self._zs_btn)
        if zs_btns:
            from . import zoomstack as oszoom
            grp = root.findGroup(oszoom.ZOOMSTACK_GROUP)
            loaded    = grp is not None
            installed = loaded or oszoom.is_installed()
            active = loaded and grp.isVisible()
            for zb in zs_btns:
                zb.blockSignals(True)
                zb.setChecked(active)
                zb.setProperty("splitActive", "true" if active else "false")
                zb.style().unpolish(zb)
                zb.style().polish(zb)
                if active:
                    zb.setText("Offline")
                    zb.setToolTip("Use the arrow to choose which features show "
                                  "(roads, labels…)")
                elif installed:
                    zb.setText("Offline")
                    zb.setToolTip("Right-click to choose which features show")
                else:
                    zb.setText("Offline — download")
                    zb.setToolTip("Offline OS basemap — first use downloads ~3.8 GB")
                zb.blockSignals(False)

    def _open_phase_screen(self, phase, title):
        """Rebuild and navigate to the habitat category selector.
        Called each time so the placed-types list is always fresh."""
        screen = self._build_phase_screen(phase)
        # Slots 1 and 2 are the phase screens — replace them in-place
        slot = 1 if phase == "baseline" else 2
        old = self.stack.widget(slot)
        self.stack.insertWidget(slot, screen)
        self.stack.removeWidget(old)
        if old:
            old.deleteLater()
        self._navigate_to(slot, title)

    # ── Deleted feature bin ───────────────────────────────────────────────────

    def _bin_feature(self, layer, fid):
        """Snapshot a feature into the deleted bin before it is removed.
        Returns the snapshot dict, or None if the feature could not be read.
        """
        import json
        from datetime import datetime

        feat = layer.getFeature(fid)
        if not feat.isValid():
            return None

        geom = feat.geometry()
        geom_wkt = geom.asWkt() if geom and not geom.isNull() else ""

        # Snapshot all attributes as a plain dict
        attrs = {}
        for field in layer.fields():
            val = feat[field.name()]
            # Convert to JSON-safe types — QGIS NULL is not Python None
            try:
                if val is None or str(val) == "NULL":
                    attrs[field.name()] = None
                else:
                    attrs[field.name()] = str(val)
            except Exception:
                attrs[field.name()] = None

        # Build a human-readable label from the most meaningful field
        label = (
            attrs.get("sub_habitat_type")
            or attrs.get("ftype")
            or attrs.get("label")
            or f"Feature {fid}"
        )
        # Add condition/phase hint if present
        cond = attrs.get("condition", "")
        phase = attrs.get("phase", "")
        hint_parts = [p for p in [phase, cond] if p]
        if hint_parts:
            label = f"{label}  ({', '.join(hint_parts)})"

        # Determine the layer_key from GPKG_LAYER_NAMES reverse map
        from .layer_manager import GPKG_LAYER_NAMES, LAYER_NAMES
        layer_key = None
        for k, name in LAYER_NAMES.items():
            if layer.name() == name:
                layer_key = k
                break

        entry = {
            "label":      label,
            "layer_name": layer.name(),
            "layer_key":  layer_key,
            "geom_wkt":   geom_wkt,
            "attrs":      attrs,
            "deleted_at": datetime.now().strftime("%H:%M:%S"),
        }
        self._deleted_bin.append(entry)
        return entry

    def _restore_from_bin(self, idx):
        """Restore entry at idx from the bin back to its original layer."""
        import json
        from qgis.core import QgsFeature, QgsGeometry, QgsField
        from qgis.PyQt.QtCore import QVariant

        if idx >= len(self._deleted_bin):
            return

        entry = self._deleted_bin[idx]

        # Find the target layer
        layer = None
        if entry["layer_key"]:
            layer = self.layer_manager.get_layer(entry["layer_key"])
        if layer is None:
            lyrs = QgsProject.instance().mapLayersByName(entry["layer_name"])
            layer = lyrs[0] if lyrs else None
        if layer is None:
            QMessageBox.warning(
                self.iface.mainWindow(), "Restore",
                f"Could not find layer '{entry['layer_name']}' to restore into."
            )
            return

        feat = QgsFeature(layer.fields())

        # Restore geometry
        if entry["geom_wkt"]:
            feat.setGeometry(QgsGeometry.fromWkt(entry["geom_wkt"]))

        # Restore attributes
        for field in layer.fields():
            val = entry["attrs"].get(field.name())
            if val is not None:
                feat.setAttribute(field.name(), val)

        ok, _ = layer.dataProvider().addFeatures([feat])
        layer.updateExtents()
        layer.triggerRepaint()
        self.iface.mapCanvas().refresh()

        if ok:
            self._deleted_bin.pop(idx)
            self._mark_summary_dirty()
            self._refresh_phase_screens()
            self._open_manage_screen()
        else:
            QMessageBox.warning(
                self.iface.mainWindow(), "Restore", "Failed to restore feature."
            )

    # ── Building & Tree Mapper ─────────────────────────────────────────────
    def _orphan_pair_twin(self, layer, fid):
        """Before deleting a linked tree/hedge feature, find its twin in the
        opposite layer (shared pair_id) and clear that twin's pair_id, so the
        survivor reclassifies by presence (baseline-only -> Lost, proposed-only
        -> Created). No-op for non tree/hedge layers or unlinked features."""
        name = layer.name().lower()
        is_tree  = "tree" in name
        is_hedge = "hedge" in name
        if not (is_tree or is_hedge):
            return
        pidx = layer.fields().indexOf("pair_id")
        if pidx < 0:
            return
        try:
            feat = layer.getFeature(fid)
        except Exception:
            return
        pid = str(feat["pair_id"] or "").strip() if feat and feat.isValid() else ""
        if not pid or pid.startswith("solo:"):
            return   # unlinked or explicit-solo — nothing to orphan

        is_baseline = "baseline" in name
        if is_tree:
            other_key = "proposed_trees" if is_baseline else "baseline_trees"
        else:
            other_key = "proposed_hedgerow" if is_baseline else "baseline_hedgerow"
        other = self.layer_manager.get_layer(other_key)
        if other is None or not other.isValid():
            return
        oidx = other.fields().indexOf("pair_id")
        if oidx < 0:
            return
        for of in other.getFeatures():
            if str(of["pair_id"] or "").strip() == pid:
                try:
                    other.dataProvider().changeAttributeValues({of.id(): {oidx: ""}})
                    other.reload()
                    other.triggerRepaint()
                except Exception:
                    pass
                break

    def _tree_hedge_status(self, layer, feat):
        """Return ('Retained'|'Enhanced'|'Lost'|'Created'|'', tooltip) for a
        tree or hedgerow feature, based on the pair_id linking logic — the same
        rules the summary and export use.

        baseline + linked twin in proposed -> Retained (or Enhanced if uplift)
        baseline, no proposed twin            -> Lost
        proposed, no baseline twin            -> Created
        Non tree/hedge layers return ('', '')."""
        name = layer.name().lower()
        is_tree  = "tree" in name
        is_hedge = "hedge" in name
        if not (is_tree or is_hedge):
            return "", ""

        is_baseline = "baseline" in name
        # locate the opposite layer
        if is_tree:
            other_key = "proposed_trees" if is_baseline else "baseline_trees"
        else:
            other_key = "proposed_hedgerow" if is_baseline else "baseline_hedgerow"
        other = self.layer_manager.get_layer(other_key)

        pidx = layer.fields().indexOf("pair_id")
        pid = str(feat["pair_id"] or "").strip() if pidx >= 0 else ""

        def _cond(f, lyr):
            ci = lyr.fields().indexOf("condition")
            return str(f[ci] or "").strip() if ci >= 0 else ""

        # find a linked twin in the other layer (shared, non-solo pair_id)
        twin = None
        if pid and not pid.startswith("solo:") and other is not None and other.isValid():
            oidx = other.fields().indexOf("pair_id")
            if oidx >= 0:
                for of in other.getFeatures():
                    if str(of["pair_id"] or "").strip() == pid:
                        twin = of
                        break

        if twin is not None:
            # linked pair present on both sides
            order = ["Poor", "Fairly Poor", "Moderate", "Fairly Good", "Good"]
            bc = _cond(feat, layer) if is_baseline else _cond(twin, other)
            pc = _cond(twin, other) if is_baseline else _cond(feat, layer)
            if bc in order and pc in order and order.index(pc) > order.index(bc):
                return "Enhanced", "Linked baseline+proposed pair, condition uplift"
            return "Retained", "Linked baseline+proposed pair — retained, no discount"

        # no linked twin -> classified purely by which layer it's in
        if is_baseline:
            return "Lost", ("Baseline %s with no linked proposed twin — counts as Lost"
                            % ("tree" if is_tree else "hedgerow"))
        else:
            return "Created", ("Proposed %s with no linked baseline twin — counts as "
                               "Created (gets temporal discount)"
                               % ("tree" if is_tree else "hedgerow"))

    def open_feature_editor(self, layer, fid, only_fields=None):
        """Open the per-feature property editor for one feature (used by the
        Biome layer panel's incomplete-features flyout). `only_fields` shows a
        trimmed editor with just those rows. Builds the Manage screen once if
        needed to capture the editor, then refreshes stats."""
        if getattr(self, "_feature_editor", None) is None:
            try:
                self._build_manage_screen()   # defines self._feature_editor; widget discarded
            except Exception:
                pass
        fn = getattr(self, "_feature_editor", None)
        if fn is None:
            return False
        try:
            fn(layer, fid, only_fields)
        except Exception:
            return False
        self._mark_summary_dirty()
        return True

    def _open_user_guide(self):
        """Open the bundled user guide PDF using the OS default viewer."""
        import subprocess, sys
        guide_path = os.path.join(os.path.dirname(__file__), "biome_user_guide.pdf")
        if not os.path.isfile(guide_path):
            self.iface.messageBar().pushMessage(
                "Biome", "User guide not found.", level=Qgis.MessageLevel.Critical, duration=4
            )
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(guide_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", guide_path])
            else:
                subprocess.Popen(["xdg-open", guide_path])
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Could not open user guide: {e}", level=Qgis.MessageLevel.Critical, duration=6
            )

    def _toggle_proposed_mapping(self):
        self._proposed_mapping = not getattr(self, "_proposed_mapping", False)
        self._update_proposed_toggle_style()

    def _update_proposed_toggle_style(self):
        on = self._proposed_mapping
        if on:
            label, bg, color, border = "ON", "#1F4A37", "white", "#1F4A37"
        else:
            label, bg, color, border = "OFF", "white", "#54635A", "#C9D1C9"
        self._proposed_toggle_btn.setText(label)
        self._proposed_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 10px;
                font-size: 10px;
                font-weight: 500;
                padding: 0;
            }}
            QPushButton:hover {{ border-color: #2E6B4F; }}
        """)

    # ------------------------------------------------------------------ #
    #  WIDGET HELPERS                                                      #
    # ------------------------------------------------------------------ #

    def _big_button(self, text, dark_color, light_color, icon_name=None):
        btn = QPushButton(text)
        btn.setMinimumHeight(40)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if icon_name:
            from qgis.PyQt.QtCore import QSize
            btn.setIcon(_svg_icon(icon_name, "white", 18))
            btn.setIconSize(QSize(18, 18))
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {dark_color};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 500;
                padding: 0px 14px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {light_color};
            }}
            QPushButton:pressed {{
                background-color: {dark_color};
                padding-left: 16px;
            }}
        """)
        return btn

    def _section_label(self, text, icon=None):
        _css = ("color:#1F4A37; font-size:10px; font-weight:500; letter-spacing:1.2px;"
                " font-family:'Inter Medium','Inter','Segoe UI',sans-serif;"
                " background:transparent;")
        if icon:
            from qgis.PyQt.QtWidgets import QWidget, QHBoxLayout
            w = QWidget(); w.setStyleSheet("background:transparent;")
            h = QHBoxLayout(w); h.setContentsMargins(2, 4, 0, 2); h.setSpacing(6)
            ic = QLabel(); ic.setFixedSize(13, 13)
            ic.setStyleSheet("background:transparent; border:none;")
            ic.setPixmap(_svg_pixmap(icon, "#1F4A37", 13))
            lbl = QLabel(text.upper()); lbl.setStyleSheet(_css)
            h.addWidget(ic); h.addWidget(lbl); h.addStretch()
            return w
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(_css + " padding: 4px 0px 2px 2px;")
        return lbl

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #EAF2EC; border:none;")
        return line
