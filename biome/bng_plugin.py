# bng_plugin.py

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.core import Qgis
from qgis.core import QgsProject
import os


# ── TEMP shutdown diagnostics ────────────────────────────────────────────
# QGIS (3 and 4) sometimes takes a very long time to close and occasionally
# needs a force-quit. Every step of the exit sequence is timestamped into
# biome_shutdown.log next to the plugin so the slow step is identifiable
# from the gap between lines. Remove once the hang is diagnosed.
_SHUTDOWN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "biome_shutdown.log")


def _slog(msg):
    try:
        import datetime
        with open(_SHUTDOWN_LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                msg))
    except Exception:
        pass


class BNGPlugin:

    def __init__(self, iface):
        self.iface  = iface
        self.panel  = None
        self.action = None
        self.mode_action = None
        self._loading_project = False   # True while a project file is being read
        self._persisting_scratch = False  # guard against projectSaved re-entrancy
        # Becomes True a short moment after QGIS finishes launching. The
        # automatic project QGIS creates at startup always emits its
        # newProjectCreated within this window, so we ignore it; a project the
        # user creates from the Welcome page afterwards arrives later and is
        # honoured. (Project ▸ New / Ctrl+N go through a QAction instead and
        # don't depend on this gate.)
        self._startup_settled = False
        self._settle_scheduled = False
        # Timestamp of the last new-project trigger, to debounce the two hooks
        # (the QAction and newProjectCreated both fire for Project ▸ New).
        self._last_new_trigger = 0.0
        # True while the QGIS 4 welcome screen is being polled for dismissal,
        # so _startup_auto_open never stacks a second watcher.
        self._welcome_watching = False
        # True once the application is actually quitting (set on aboutToQuit,
        # which fires before plugin unload). Lets unload() tell a real QGIS
        # exit apart from a plugin reload, and skip work that only matters
        # when the UI lives on.
        self._app_quitting = False

    def initGui(self):
        # On the first Biome run in a new QGIS profile, offer to bring across
        # only Biome-owned preferences from an earlier profile. This runs
        # before any first-run defaults are read or written.
        try:
            from .preference_migration import maybe_migrate
            maybe_migrate(self.iface.mainWindow())
        except Exception:
            pass

        # TEMP (QGIS 4 shakedown): tee every Python error QGIS surfaces (it
        # routes them all through qgis.utils.showException) to biome_live.log
        # next to the plugin, so problems can be read live from outside QGIS.
        # Remove once the port is signed off.
        try:
            import datetime as _dtm
            import traceback as _tbm
            import qgis.utils as _qutils
            _live_path = os.path.join(os.path.dirname(__file__), "biome_live.log")
            def _live_write(text):
                try:
                    with open(_live_path, "a", encoding="utf-8") as f:
                        f.write("%s %s\n" % (
                            _dtm.datetime.now().strftime("%H:%M:%S"), text))
                except Exception:
                    pass
            if not getattr(_qutils, "_biome_live_hooked", False):
                _orig_show = _qutils.showException
                def _show_and_log(etype, value, tb, *args, **kwargs):
                    try:
                        _live_write("PYTHON ERROR:\n" + "".join(
                            _tbm.format_exception(etype, value, tb)))
                    except Exception:
                        pass
                    return _orig_show(etype, value, tb, *args, **kwargs)
                _qutils.showException = _show_and_log
                _qutils._biome_live_hooked = True
            _live_write("=== Biome initGui (live error log armed) ===")
        except Exception:
            pass

        icon_path = os.path.join(os.path.dirname(__file__), 'icon.png')
        self.action = QAction(QIcon(icon_path), 'Biome', self.iface.mainWindow())
        self.action.triggered.connect(self.toggle_panel)
        self.iface.addPluginToMenu('Biome', self.action)
        self.iface.addToolBarIcon(self.action)

        # ── Biome mode: app-wide moss & chalk restyle ────────────────────
        try:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage("Biome v2.5 loaded (stylised EPSL icons + filter labels)", "Biome", Qgis.MessageLevel.Info)
        except Exception:
            pass
        # Follow the OS light/dark scheme (QGIS 4 / Qt 6.5+; no-op on Qt 5).
        # QGIS 4 mirrors the OS theme with no in-app opt-out, so Biome does too:
        # the panel, tooltips and still-banner flip live via theme listeners.
        try:
            from . import theme, biome_mode as _bm
            # When the mode flips, also re-apply Biome Mode's app-wide restyle
            # if it's on, so the whole-QGIS theme tracks light/dark too.
            theme.add_theme_listener(
                lambda: _bm.reapply_style_if_enabled(self.iface))
            theme.install_dark_restyler()   # dark-remap Biome widgets on show
            theme.install_os_scheme_follow()
            # TEMP diagnostic (shakedown): log what the scheme detection sees a
            # moment after startup, so a "panel stayed light" report is provable.
            def _diag_theme():
                try:
                    from qgis.PyQt.QtGui import QGuiApplication, QPalette
                    from qgis.PyQt.QtWidgets import QApplication
                    sch = QGuiApplication.styleHints().colorScheme()
                    lit = QApplication.instance().palette().color(
                        QPalette.ColorRole.Window).lightness()
                    _live_write(f"THEME diag: colorScheme={sch} windowLightness={lit} "
                                f"os_is_dark={theme.os_is_dark()} mode={theme.current_mode()}")
                except Exception as _e:
                    _live_write(f"THEME diag failed: {_e}")
            QTimer.singleShot(3000, _diag_theme)
        except Exception:
            pass

        from . import biome_mode
        self.mode_action = QAction('Biome Mode', self.iface.mainWindow())
        self.mode_action.setCheckable(True)
        # Biome mode is ON by default: the first time the plugin runs (no saved
        # preference yet) it enables itself. After that the user's explicit
        # on/off choice is remembered and respected.
        first_run = not biome_mode.is_set()
        self._first_run_workspace_pending = first_run
        # setChecked BEFORE connecting toggled, so this doesn't fire enable().
        self.mode_action.setChecked(biome_mode.is_enabled() or first_run)
        self.mode_action.toggled.connect(self._on_biome_mode_toggled)
        self.iface.addPluginToMenu('Biome', self.mode_action)
        def _timed_mode_apply(fn, label):
            # TEMP load diagnostics: the app-wide restyle repolishes every
            # widget (same cost class as the old slow-close bug) — time it.
            import time as _t
            t0 = _t.perf_counter()
            fn(self.iface)
            _slog("startup: %s took %.1fs" % (label, _t.perf_counter() - t0))
        if first_run:
            # Defer past launch so the snapshot captures fully-loaded toolbars.
            # A fresh install should also make both parts of the Biome workspace
            # discoverable: enable() shows the compact Layers panel. The main
            # sidebar is opened by _startup_auto_open once QGIS's welcome UI is
            # clear, avoiding any project scaffolding during launch.
            def _enable_first_run_workspace():
                _timed_mode_apply(
                    biome_mode.enable, "biome mode enable (app restyle)")
            QTimer.singleShot(1200, _enable_first_run_workspace)
        elif biome_mode.is_enabled():
            # Defer past launch so late-restored toolbars are swept too.
            QTimer.singleShot(1200, lambda: _timed_mode_apply(
                biome_mode.apply_startup, "biome mode apply_startup (app restyle)"))

        # ── Suppress datum-transform dialog at the earliest possible moment ──
        # Must be done synchronously here (NOT deferred) so the settings are
        # committed before QGIS reads any project file.  QSettings.sync() forces
        # the Windows registry write to flush immediately, preventing the race
        # where QgisApp::askUserForDatumTransform reads a stale value and then
        # crashes on a null CRS string inside QString::arg during readProjectFile.
        try:
            from qgis.PyQt.QtCore import QSettings
            s = QSettings()
            s.setValue("/projections/unknownCrsBehavior", 0)
            s.setValue("/projections/defaultBehavior", "useDefault")
            s.setValue("/projections/promptWhenMultipleTransformsExist", False)
            s.setValue("projections/promptWhenMultipleTransformsExist", False)
            s.sync()   # force immediate flush to registry — critical on Windows
            # The 'Select Transformation for <layer>' dialog's REAL gate is the
            # App-SECTION QgsSettings key ([app] projections\… in the profile
            # ini) — QGIS ships a global default of TRUE for it, and the plain
            # [projections] writes above never overrode that, so the dialog
            # still appeared whenever a project's transform context lacked a
            # CRS pair (e.g. QGIS 4 saves opened in QGIS 3).
            from qgis.core import QgsSettings
            qs = QgsSettings()
            qs.setValue("projections/promptWhenMultipleTransformsExist",
                        False, section=QgsSettings.Section.App)
            qs.sync()
        except Exception:
            pass

        # Keep the curved FOV labels (Emergence Survey Layout) sized to the
        # current zoom so they never degrade to straight text that overruns the
        # symbol. Rebuilt SYNCHRONOUSLY the instant the scale changes — before the
        # canvas renders — so the render uses the correctly-sized arc and there's
        # no stale straight version to overwrite it (a debounced rebuild lagged
        # the render and caused a curve→straight flicker). A no-op (and never
        # creates a layer) when the project has no field-of-view cones.
        try:
            self._fov_arc_slot = lambda *_: self._rebuild_fov_arcs()
            self.iface.mapCanvas().scaleChanged.connect(self._fov_arc_slot)
        except Exception:
            pass

        # TEMP shutdown diagnostics (see _slog above): timestamp the exit
        # sequence and snapshot what could be keeping QGIS alive.
        try:
            from qgis.PyQt.QtWidgets import QApplication
            QApplication.instance().aboutToQuit.connect(self._log_about_to_quit)
        except Exception:
            pass
        try:
            import atexit
            atexit.register(_slog, "atexit: python finalising — shutdown "
                                   "reached the very end")
        except Exception:
            pass
        try:   # keep the diagnostic log from growing unbounded
            if os.path.getsize(_SHUTDOWN_LOG) > 512_000:
                os.remove(_SHUTDOWN_LOG)
        except Exception:
            pass
        _slog("=== session start (initGui), QGIS %s ===" % Qgis.QGIS_VERSION)

        # Repoint stale OS Zoomstack references in old project files at the
        # current install as they are read, so opening an old save never
        # raises the "Handle Unavailable Layers" dialog. Must be registered
        # before any project is read (including the startup-restored one).
        try:
            from . import zoomstack
            zoomstack.install_path_preprocessor()
        except Exception:
            pass

        # Track when a project file is being loaded so we ignore the cleared signal
        QgsProject.instance().readProject.connect(self._on_read_project_start)

        # Freeze the canvas while a project is cleared (crash-prevention on
        # open). Does NOT decide new-project scaffolding — that's driven by
        # newProjectCreated below.
        QgsProject.instance().cleared.connect(self._on_project_cleared)

        # The plugin "starts up" (panel + base maps + layers) only when the
        # user creates a NEW project. Two complementary, reliable hooks:
        #
        #  1. Project ▸ New, the New toolbar button and Ctrl+N all invoke the
        #     actionNewProject QAction. Its triggered() signal only fires on a
        #     real user invocation — never for QGIS's automatic startup
        #     project — so it is safe to act on immediately.
        try:
            new_action = self.iface.actionNewProject()
            if new_action:
                new_action.triggered.connect(self._on_new_project_action)
        except Exception:
            pass

        #  2. The Welcome page's "New Empty Project" tile calls fileNew()
        #     directly (not the QAction), so it only emits newProjectCreated —
        #     the same signal QGIS fires for its own startup project. We honour
        #     it only once startup has settled (see _startup_settled); the
        #     automatic startup project always emits within that window, while
        #     a user clicking the tile arrives afterwards.
        try:
            self.iface.newProjectCreated.connect(self._on_new_project_created)
        except Exception:
            pass

        # Connect to the Open Project action so we can set _loading_project = True
        # BEFORE QgsProject.cleared fires — this is the only way to tell cleared
        # apart from a File → New in time to freeze the canvas before readProjectFile.
        try:
            open_action = self.iface.actionOpenProject()
            if open_action:
                open_action.triggered.connect(self._on_open_project_action)
        except Exception:
            pass

        # Arm the Welcome-page hook a short moment after QGIS finishes starting
        # up. initializationCompleted fires at the end of launch; the fallback
        # timer covers the plugin being enabled at runtime (init already done).
        try:
            self.iface.initializationCompleted.connect(self._on_init_complete)
        except Exception:
            pass
        QTimer.singleShot(3000, self._on_init_complete)

        # Run post-save format compatibility checks (both .qgs and .qgz supported).
        QgsProject.instance().projectSaved.connect(self._on_project_saved)

        # Persist client name and project number inside the QGIS project file.
        QgsProject.instance().readProject.connect(self._on_read_project_meta)
        QgsProject.instance().writeProject.connect(self._on_write_project_meta)

        # Do NOT auto-open the panel or scaffold the project at startup —
        # QGIS should load up exactly as normal, with no Biome layers or base
        # maps injected into the startup project (which previously dirtied it
        # and triggered a "save the current project?" prompt). The plugin only
        # "starts up" when the user creates a NEW project. The one exception:
        # if QGIS restored an existing Biome project at launch, re-open the
        # panel for it once the event loop is idle.
        QTimer.singleShot(0, self._maybe_open_for_existing_project)

        # Add a "Copy to Clipboard" action to every layout designer window
        # (QGIS's own Export as Image can only write to a file).
        try:
            from . import layout_clipboard
            layout_clipboard.install(self.iface)
        except Exception:
            pass

        # Snap the designer's Ctrl+scroll fine zoom (Move item content tool) to
        # round printed scales — 1:170/180/190 rather than 1:178/188/198.
        try:
            from . import layout_fine_zoom
            layout_fine_zoom.install(self.iface)
        except Exception:
            pass

        # Pin a real measurement ellipsoid on whatever project is current.
        # A project reporting ellipsoid "NONE" measures planar — on the
        # EPSG:3857 habitat layers that inflates areas ~2.6x at UK latitudes
        # (also breaks $area/$length label expressions, which follow the
        # project ellipsoid). Deferred internally; only sets when "NONE".
        try:
            from .config import ensure_project_ellipsoid
            ensure_project_ellipsoid()
        except Exception:
            pass

    def _on_biome_mode_toggled(self, checked):
        from . import biome_mode
        try:
            if checked:
                biome_mode.enable(self.iface)
                self.iface.messageBar().pushMessage(
                    "Biome", "Biome mode on — toolbars hidden, theme applied. "
                    "Toggle off any time from the Plugins ▸ Biome menu.",
                    level=Qgis.MessageLevel.Info, duration=6)
            else:
                biome_mode.disable(self.iface)
                self.iface.messageBar().pushMessage(
                    "Biome", "Biome mode off — your previous layout is back.",
                    level=Qgis.MessageLevel.Info, duration=4)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Biome mode error: {e}", level=Qgis.MessageLevel.Critical, duration=0)

    def _rebuild_fov_arcs(self):
        """Resize the curved FOV-label arcs to the current zoom (see initGui).
        Runs synchronously on scaleChanged; a re-entrancy guard keeps a layer
        edit from re-triggering it."""
        if getattr(self, "_fov_busy", False):
            return
        self._fov_busy = True
        try:
            from . import fov_label_arcs as FLA
            from qgis.PyQt.QtCore import QSettings
            icon_mm = float(QSettings().value("biome/eco_icon_mm", 6) or 6)
            FLA.refresh(self.iface.mapCanvas(), icon_mm)
        except Exception:
            pass
        finally:
            self._fov_busy = False

    def _log_about_to_quit(self):
        """Fires when the event loop is about to stop — before plugin unload.
        Marks the session as quitting (so unload() can skip UI-restore work
        nobody will see) and logs what could keep QGIS alive at exit."""
        self._app_quitting = True
        _slog("aboutToQuit fired")
        try:
            from qgis.core import QgsApplication
            tasks = QgsApplication.taskManager().activeTasks()
            _slog("  active QgsTasks: %d  %s"
                  % (len(tasks), [t.description() for t in tasks]))
        except Exception as e:
            _slog("  QgsTask snapshot failed: %r" % e)
        try:
            import threading
            _slog("  python threads: %s"
                  % [(t.name, "daemon" if t.daemon else "NON-DAEMON")
                     for t in threading.enumerate()])
        except Exception:
            pass
        try:
            proc = getattr(self.panel, "_fm_tunnel_proc", None) \
                if self.panel is not None else None
            srv = getattr(self.panel, "_fm_server", None) \
                if self.panel is not None else None
            _slog("  field mapper: tunnel_proc=%s http_server=%s"
                  % (proc is not None, srv is not None))
        except Exception:
            pass

    def _kill_field_mapper_processes(self):
        """Terminate the field-mapper tunnel + http server — the only resources
        that outlive the process (an orphaned cloudflared.exe locks the plugin
        dir). Everything else Qt/QGIS reclaims itself. Safe on any path."""
        panel = self.panel
        if panel is None:
            return
        try:
            import threading
            srv = getattr(panel, '_fm_server', None)
            if srv:
                _slog("unload: shutting down field-mapper http server")
                threading.Thread(target=srv.shutdown, daemon=True).start()
        except Exception:
            pass
        try:
            proc = getattr(panel, '_fm_tunnel_proc', None)
            if proc:
                _slog("unload: terminating cloudflared tunnel (waits up to 3s)")
                proc.terminate()
                proc.wait(timeout=3)
                _slog("unload: tunnel terminated")
        except Exception:
            try:
                if proc:
                    proc.kill()
                    _slog("unload: tunnel killed after wait timeout")
            except Exception:
                pass

    def unload(self):
        _slog("unload: start")
        # ── Real QGIS exit: minimal, SAFE teardown ────────────────────────
        # By the time aboutToQuit has fired the application is tearing its C++
        # objects down. Disconnecting signals, poking the map canvas / project,
        # or removing dock widgets then touches half-destroyed objects and
        # SEGFAULTS — a native crash Python try/except cannot catch (this is
        # what crashed Biome on shutdown). The OS reclaims all of that anyway,
        # so on a real quit do ONLY the external-process cleanup and return.
        # A plugin RELOAD (no aboutToQuit) still runs the full teardown below.
        if self._app_quitting:
            _slog("unload: QGIS quitting — minimal teardown (skip Qt cleanup)")
            self._kill_field_mapper_processes()
            _slog("unload: end (quit path)")
            return
        try:
            from . import zoomstack
            zoomstack.remove_path_preprocessor()
        except Exception:
            pass
        try:
            from . import layout_clipboard
            layout_clipboard.uninstall(self.iface)
        except Exception:
            pass
        try:
            from . import layout_fine_zoom
            layout_fine_zoom.uninstall(self.iface)
        except Exception:
            pass
        # Undo Biome mode visuals (keeps the setting so it returns on reload).
        try:
            self.iface.mapCanvas().scaleChanged.disconnect(self._fov_arc_slot)
        except Exception:
            pass
        # Reload path only (the quit path returned above): restore the
        # pre-Biome stylesheet/palette so the reloaded plugin re-snapshots clean.
        try:
            from . import biome_mode
            biome_mode.restore_for_unload(self.iface)
            _slog("unload: biome mode restored (plugin reload path)")
        except Exception:
            pass
        try:
            if self.mode_action:
                self.iface.removePluginMenu('Biome', self.mode_action)
        except Exception:
            pass
        try:
            new_action = self.iface.actionNewProject()
            if new_action:
                new_action.triggered.disconnect(self._on_new_project_action)
        except Exception:
            pass
        try:
            self.iface.newProjectCreated.disconnect(self._on_new_project_created)
        except Exception:
            pass
        try:
            self.iface.initializationCompleted.disconnect(self._on_init_complete)
        except Exception:
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_cleared)
        except Exception:
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_read_project_start)
        except Exception:
            pass
        try:
            QgsProject.instance().projectSaved.disconnect(self._on_project_saved)
        except Exception:
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_read_project_meta)
        except Exception:
            pass
        try:
            QgsProject.instance().writeProject.disconnect(self._on_write_project_meta)
        except Exception:
            pass
        _slog("unload: signals disconnected")
        self.iface.removePluginMenu('Biome', self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.panel is not None:
            # Remove transform dialog suppressor
            try:
                from qgis.PyQt.QtWidgets import QApplication
                if hasattr(self.panel, '_transform_suppressor'):
                    QApplication.instance().removeEventFilter(
                        self.panel._transform_suppressor)
            except Exception:
                pass

            # Kill tunnel + server BEFORE deleteLater — on Windows an open
            # cloudflared.exe locks the plugin dir ("Failed to remove the
            # directory" on uninstall).
            self._kill_field_mapper_processes()

            _slog("unload: removing panel dock")
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None
        _slog("unload: end")

    def _on_read_project_meta(self, *args):
        """Client name / project number are read on demand straight from the
        project (map_exporter._SESSION is a project-backed view keyed on the
        same 'clientName' / 'projectNumber' entries), so there is nothing to
        copy into module state on project open.

        The one thing we DO fix up here is the measurement ellipsoid: an old
        save (or a profile whose measure/ellipsoid setting is unset) can carry
        ellipsoid "NONE", which silently makes every measurement planar in the
        EPSG:3857 layer CRS (~2.6x area inflation at UK latitudes).
        ensure_project_ellipsoid defers to the next event-loop tick (never
        mutate the project mid-readProjectFile) and restores the dirty flag,
        so a freshly opened project is not re-dirtied."""
        try:
            from .config import ensure_project_ellipsoid
            ensure_project_ellipsoid()
        except Exception:
            pass

        # Live Claude Edit is a profile setting, so re-apply it to the layers
        # this project just brought in. Deferred for the same reason as the
        # ellipsoid fix — never touch layers mid-readProjectFile.
        try:
            from qgis.PyQt.QtCore import QTimer
            from . import live_edit
            if live_edit.is_enabled():
                QTimer.singleShot(0, live_edit.apply)
        except Exception:
            pass


    def _on_write_project_meta(self, *args):
        """Save current _SESSION client/proj_no into the QGIS project file,
        and ensure the OSTN15 transform context is written so reopening
        the project never triggers the QGIS 3.44 Windows crash caused by
        an empty <transformContext/> element firing projectCrsChanged with
        an uninitialised CRS during readProjectFile."""
        from .map_exporter import _SESSION
        project = QgsProject.instance()
        project.writeEntry("BiomeMapper", "clientName",    _SESSION.get("client",  ""))
        project.writeEntry("BiomeMapper", "projectNumber", _SESSION.get("proj_no", ""))

        # Populate the transform context before the project XML is written.
        # An empty <transformContext/> causes a fatal crash on reopen — QGIS
        # fires askUserForDatumTransform during readProjectFile on a half-ready
        # CRS → QBitArray null-init → access violation (QGIS 3.44 / Windows).
        # setTransformContext is safe here because we are in writeProject, not
        # readProjectFile, so no CRS objects are being constructed mid-read.
        try:
            from qgis.core import (
                QgsCoordinateReferenceSystem, QgsDatumTransform,
            )
            ctx   = project.transformContext()
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
            project.setTransformContext(ctx)
        except Exception:
            pass

    def _on_project_saved(self):
        """Delegate format check to the panel if it exists."""
        if self._loading_project:
            return
        # Migrate any 'just looking' (memory) layers into the project GeoPackage
        # so saving captures everything that was placed before the project had
        # a file path. Guarded against re-entrancy because the migration rewrites
        # the project, which fires projectSaved again.
        if not self._persisting_scratch:
            self._persisting_scratch = True
            import time as _time
            _t0 = _time.perf_counter()
            _slog("projectSaved: post-save work start")
            try:
                rewrite = False
                # Save As / rename first, so the scratch migration below
                # writes into the NEW GeoPackage.
                try:
                    rewrite = self._sync_gpkg_after_save_as()
                except Exception:
                    pass
                try:
                    rewrite = bool(self._persist_scratch_layers()) or rewrite
                except Exception:
                    pass
                _slog("projectSaved: gpkg sync/migration done (%.1fs)"
                      % (_time.perf_counter() - _t0))
                if rewrite:
                    # Rewrite the project so the .qgz/.qgs records the GeoPackage
                    # data sources. The resulting projectSaved is guarded above.
                    try:
                        QgsProject.instance().write()
                    except Exception:
                        pass
                    _slog("projectSaved: project rewrite done (%.1fs)"
                          % (_time.perf_counter() - _t0))
                # Last (after the final write): remove session-only scratch
                # layers from the file on disk so the project never reopens
                # with empty desk-study / buffer / overlay layers.
                try:
                    self._strip_session_layers_from_file()
                except Exception:
                    pass
                _slog("projectSaved: post-save work end (%.1fs total)"
                      % (_time.perf_counter() - _t0))
            finally:
                self._persisting_scratch = False
        if self.panel is not None:
            try:
                self.panel._check_save_format()
            except Exception:
                pass

    def _project_gpkg_path(self):
        """Path of the project GeoPackage, or None if the project is unsaved."""
        fn = QgsProject.instance().fileName()
        if not fn:
            return None
        project_dir  = os.path.dirname(fn)
        project_name = os.path.splitext(os.path.basename(fn))[0]
        return os.path.join(project_dir, f"{project_name}_bng_data.gpkg")

    def _persist_scratch_layers(self):
        """Write every tagged memory ('just looking') layer into the project
        GeoPackage and repoint it there. Returns the number of layers migrated
        so the caller can rewrite the project file once at the end."""
        import re
        from qgis.core import (
            QgsProject, QgsVectorLayer, QgsVectorFileWriter, QgsDataProvider,
            Qgis,
        )
        proj = QgsProject.instance()
        gpkg = self._project_gpkg_path()
        if not gpkg:
            return 0

        scratch_layers = []
        for layer in list(proj.mapLayers().values()):
            if not isinstance(layer, QgsVectorLayer):
                continue
            if str(layer.customProperty("biome_scratch", "")) != "1":
                continue
            table = layer.customProperty("biome_gpkg_table", "")
            if table:
                scratch_layers.append((layer, table))

        if not scratch_layers:
            return 0

        migrated = 0
        failed = []
        for layer, table in scratch_layers:
            try:
                # A subset filter (measure-sketch visibility uses '$id != N')
                # would make the writer skip the hidden features — clear it for
                # the write and re-apply it (fid-remapped) afterwards.
                subset = layer.subsetString() or ""
                if subset:
                    layer.setSubsetString("")
                # Snapshot fids before the write. The GeoPackage assigns fresh
                # sequential rowids, so every feature comes back with a NEW
                # fid; the panel's global undo stack still holds the memory-
                # provider fids and would otherwise delete the wrong feature
                # after this save. Both the memory provider and the writer
                # iterate in ascending-fid order, so pairing the two iteration
                # orders gives the old→new mapping.
                old_fids = [f.id() for f in layer.getFeatures()]
                opts = QgsVectorFileWriter.SaveVectorOptions()
                opts.driverName   = "GPKG"
                opts.layerName    = table
                opts.fileEncoding = "UTF-8"
                opts.actionOnExistingFile = (
                    QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
                    if os.path.exists(gpkg)
                    else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
                )
                err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
                    layer, gpkg, proj.transformContext(), opts,
                )
                if err != QgsVectorFileWriter.WriterError.NoError:
                    if subset:
                        layer.setSubsetString(subset)
                    failed.append(layer.name())
                    continue
                # Repoint the SAME layer object to the GeoPackage table. This
                # keeps its id, style and position in the layer tree, so future
                # edits are written straight to disk.
                uri = f"{gpkg}|layername={table}"
                layer.setDataSource(uri, layer.name(), "ogr",
                                    QgsDataProvider.ProviderOptions())
                if not layer.isValid():
                    failed.append(layer.name())
                    continue
                # Old→new fid pairing. On a count mismatch no reliable pairing
                # exists — use an empty map so stale references are dropped
                # instead of risking a wrong-feature undo/toggle later.
                try:
                    new_fids = [f.id() for f in layer.getFeatures()]
                    fid_map = (dict(zip(old_fids, new_fids))
                               if len(old_fids) == len(new_fids) else {})
                except Exception:
                    fid_map = {}
                # Remap this layer's undo entries onto the new fids.
                if self.panel is not None and hasattr(self.panel, "remap_undo_fids"):
                    try:
                        self.panel.remap_undo_fids(layer.id(), fid_map)
                    except Exception:
                        pass
                # Re-apply the visibility filter with the new fids.
                if subset:
                    try:
                        layer.setSubsetString(re.sub(
                            r"\$id\s*!=\s*(\d+)",
                            lambda m: f"$id != {fid_map.get(int(m.group(1)), int(m.group(1)))}",
                            subset))
                    except Exception:
                        pass
                layer.removeCustomProperty("biome_scratch")
                layer.removeCustomProperty("skipMemoryLayersCheck")
                layer.triggerRepaint()
                migrated += 1
            except Exception:
                failed.append(layer.name())
                continue

        if migrated and self.panel is not None:
            # An open Measurements screen caches fids per sketch row; refresh
            # it from the migrated layers so visibility toggles / deletes hit
            # the right features.
            try:
                from .measurements import MeasurementsWidget
                for w in self.panel.findChildren(MeasurementsWidget):
                    w._rebuild_sketches_from_layers()
                    w._refresh_measure_list()
            except Exception:
                pass

        if failed:
            try:
                self.iface.messageBar().pushMessage(
                    "Biome",
                    "Could not save {} into the project GeoPackage — that data is "
                    "NOT on disk and will be lost when QGIS closes. Close anything "
                    "locking '{}' (sync tools, other apps), then save again."
                    .format(", ".join(f"'{n}'" for n in failed),
                            os.path.basename(gpkg)),
                    level=Qgis.MessageLevel.Critical, duration=0)
            except Exception:
                pass
        return migrated

    def _sync_gpkg_after_save_as(self):
        """After Save As / rename, biome layers still read the previous
        <oldname>_bng_data.gpkg. Copy that GeoPackage next to the new project
        file and repoint every layer, so the saved project stays self-contained
        instead of silently depending on the old project's data file.
        Returns True if any layer was repointed (caller rewrites the project)."""
        import shutil
        from qgis.core import QgsProject, QgsVectorLayer, QgsDataProvider, Qgis
        proj = QgsProject.instance()
        new_gpkg = self._project_gpkg_path()
        if not new_gpkg:
            return False

        def _norm(p):
            return os.path.normcase(os.path.normpath(p))

        stale = []   # (layer, old_gpkg_path, table)
        for layer in list(proj.mapLayers().values()):
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.providerType() != "ogr":
                continue
            src = layer.source()
            if "|layername=" not in src:
                continue
            path = src.split("|")[0]
            if not path.lower().endswith("_bng_data.gpkg"):
                continue
            if _norm(path) == _norm(new_gpkg):
                continue
            table = src.split("|layername=")[-1].split("|")[0]
            stale.append((layer, path, table))
        if not stale:
            return False

        old_paths = {_norm(p) for _, p, _ in stale}
        if len(old_paths) > 1:
            # More than one source data file — no safe automatic merge.
            try:
                self.iface.messageBar().pushMessage(
                    "Biome",
                    "This project reads from more than one biome data file, so it "
                    "was not repointed after Save As. Its layers still read from "
                    "the original location.",
                    level=Qgis.MessageLevel.Warning, duration=0)
            except Exception:
                pass
            return False

        old_gpkg = stale[0][1]
        if not os.path.exists(old_gpkg):
            return False
        if os.path.exists(new_gpkg):
            # Never clobber an existing data file — it may belong to another
            # project saved under this name.
            try:
                self.iface.messageBar().pushMessage(
                    "Biome",
                    f"'{os.path.basename(new_gpkg)}' already exists, so the habitat "
                    f"data was not copied across. This project's layers still read "
                    f"from '{old_gpkg}' — keep that file, or move it manually.",
                    level=Qgis.MessageLevel.Warning, duration=0)
            except Exception:
                pass
            return False
        try:
            shutil.copy2(old_gpkg, new_gpkg)
        except Exception:
            try:
                self.iface.messageBar().pushMessage(
                    "Biome",
                    f"Could not copy the biome data file to '{new_gpkg}'. This "
                    f"project's layers still read from '{old_gpkg}'.",
                    level=Qgis.MessageLevel.Critical, duration=0)
            except Exception:
                pass
            return False

        repointed = False
        for layer, _, table in stale:
            try:
                # The file was copied byte-for-byte, so fids are unchanged —
                # only the visibility filter needs carrying across the repoint.
                subset = layer.subsetString() or ""
                layer.setDataSource(f"{new_gpkg}|layername={table}", layer.name(),
                                    "ogr", QgsDataProvider.ProviderOptions())
                if layer.isValid():
                    if subset:
                        layer.setSubsetString(subset)
                    layer.triggerRepaint()
                    repointed = True
            except Exception:
                continue
        return repointed

    def _session_only_layer_ids(self):
        """Ids of throwaway session layers: memory layers carrying the
        save-warning silencer but NOT tagged for GeoPackage migration —
        fetched desk-study data, buffers, outlines, FOV label arcs. These are
        re-fetched or rebuilt on demand, never persisted."""
        from qgis.core import QgsProject, QgsVectorLayer
        ids = set()
        for lid, layer in QgsProject.instance().mapLayers().items():
            try:
                # Explicitly-tagged session layers of ANY provider — the
                # desk-study WMS overlays (IRZ, HMLR property boundaries)
                # aren't memory layers, so the rules below can't catch them.
                if str(layer.customProperty("biome_session_only", "")).lower() \
                        in ("1", "true"):
                    ids.add(lid)
                    continue
                if not isinstance(layer, QgsVectorLayer):
                    continue
                if layer.providerType() != "memory":
                    continue
                if str(layer.customProperty("skipMemoryLayersCheck", "")).lower() \
                        not in ("1", "true"):
                    continue
                if str(layer.customProperty("biome_scratch", "")) == "1":
                    continue
                if layer.customProperty("biome_gpkg_table", ""):
                    continue
                ids.add(lid)
            except Exception:
                continue
        return ids

    @staticmethod
    def _strip_project_xml(data, ids):
        """Remove the given layer ids from project XML bytes: their <maplayer>
        definitions, layer-tree entries (pruning groups this empties), drawing
        order and custom-order references. Returns (new_bytes, removed_count).
        Uses QDom so the DOCTYPE and everything else round-trips untouched."""
        from qgis.PyQt.QtXml import QDomDocument
        doc = QDomDocument()
        res = doc.setContent(bytes(data))
        ok = res[0] if isinstance(res, tuple) else res
        if not ok:
            return data, 0

        def _elements(tag):
            nl = doc.elementsByTagName(tag)
            return [nl.at(i).toElement() for i in range(nl.count())]

        def _has_layer_children(grp):
            child = grp.firstChildElement()
            while not child.isNull():
                if child.tagName() in ("layer-tree-layer", "layer-tree-group"):
                    return True
                child = child.nextSiblingElement()
            return False

        removed = 0
        # <projectlayers>/<maplayer> definitions
        for el in _elements("maplayer"):
            if el.firstChildElement("id").text() in ids:
                el.parentNode().removeChild(el)
                removed += 1
        # layer-tree entries; prune only groups this strip leaves empty, and
        # never the root layer-tree-group (its parent is <qgis>, not a group)
        for el in _elements("layer-tree-layer"):
            if el.attribute("id") not in ids:
                continue
            parent = el.parentNode().toElement()
            el.parentNode().removeChild(el)
            removed += 1
            while (not parent.isNull()
                   and parent.tagName() == "layer-tree-group"
                   and parent.parentNode().toElement().tagName() == "layer-tree-group"
                   and not _has_layer_children(parent)):
                grandparent = parent.parentNode().toElement()
                grandparent.removeChild(parent)
                parent = grandparent
        # drawing order + legend custom order
        for lo in _elements("layerorder"):
            child = lo.firstChildElement("layer")
            doomed = []
            while not child.isNull():
                if child.attribute("id") in ids:
                    doomed.append(child)
                child = child.nextSiblingElement("layer")
            for c in doomed:
                lo.removeChild(c)
        for co in _elements("custom-order"):
            child = co.firstChildElement("item")
            doomed = []
            while not child.isNull():
                if child.text() in ids:
                    doomed.append(child)
                child = child.nextSiblingElement("item")
            for c in doomed:
                co.removeChild(c)

        if not removed:
            return data, 0
        return bytes(doc.toByteArray()), removed

    def _strip_session_layers_from_file(self):
        """Rewrite the just-saved project file WITHOUT the session-only scratch
        layers. QGIS cannot store memory-layer features, so leaving them in the
        file only produces confusing empty desk-study/buffer layers on reopen.
        Operates purely on the file on disk — the live session is untouched."""
        import zipfile
        ids = self._session_only_layer_ids()
        # The OS Zoomstack basemap is session-only too: persisting it baked in
        # machine/date-specific GeoPackage paths that broke on reopen ("Handle
        # Unavailable Layers"), and it reloads in one click from the panel.
        try:
            from . import zoomstack
            ids |= zoomstack.project_zoomstack_layer_ids()
        except Exception:
            pass
        if not ids:
            return
        path = QgsProject.instance().fileName()
        if not path or not os.path.exists(path):
            return

        if path.lower().endswith(".qgz"):
            with zipfile.ZipFile(path, "r") as zin:
                names = zin.namelist()
                qgs_name = next(
                    (n for n in names if n.lower().endswith(".qgs")), None)
                if not qgs_name:
                    return
                new_data, removed = self._strip_project_xml(
                    zin.read(qgs_name), ids)
                if not removed:
                    return
                others = [(n, zin.read(n)) for n in names if n != qgs_name]
            tmp = path + ".biome_tmp"
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                zout.writestr(qgs_name, new_data)
                for n, d in others:
                    zout.writestr(n, d)
            os.replace(tmp, path)
        else:
            with open(path, "rb") as f:
                data = f.read()
            new_data, removed = self._strip_project_xml(data, ids)
            if not removed:
                return
            tmp = path + ".biome_tmp"
            with open(tmp, "wb") as f:
                f.write(new_data)
            os.replace(tmp, path)

    def _cleanup_stale_session_layers(self):
        """Remove empty session-only scratch layers that saves from older
        plugin builds embedded in the project (QGIS reopens memory layers
        empty), plus any groups this leaves empty. Heals existing save files;
        _strip_session_layers_from_file keeps new saves clean."""
        from qgis.core import QgsProject, QgsVectorLayer
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        doomed = []
        for lid, layer in list(proj.mapLayers().items()):
            try:
                # Tagged session-only layers (any provider) — always
                # re-fetchable desk-study overlays, never project data.
                if str(layer.customProperty("biome_session_only", "")).lower() \
                        in ("1", "true"):
                    doomed.append(lid)
                    continue
                # Saves that predate the tag embedded the two desk-study WMS
                # overlays — match those two by provider + name.
                if layer.providerType() == "wms" and (
                        layer.name().startswith("SSSI IRZ")
                        or layer.name() == "Property Boundaries (HMLR INSPIRE)"):
                    doomed.append(lid)
                    continue
                if not isinstance(layer, QgsVectorLayer):
                    continue
                if layer.providerType() != "memory":
                    continue
                if str(layer.customProperty("skipMemoryLayersCheck", "")).lower() \
                        not in ("1", "true"):
                    continue
                if str(layer.customProperty("biome_scratch", "")) == "1":
                    continue
                if layer.customProperty("biome_gpkg_table", ""):
                    continue
                if layer.featureCount() != 0:   # never touch actual data
                    continue
                doomed.append(lid)
            except Exception:
                continue
        if not doomed:
            return
        parents = []
        for lid in doomed:
            node = root.findLayer(lid)
            if node is not None and node.parent() is not None:
                parents.append(node.parent())
        for lid in doomed:
            proj.removeMapLayer(lid)
        # Prune only groups the removal emptied — never pre-existing empty
        # user groups, and never the root.
        for grp in parents:
            try:
                while (grp is not None and grp.parent() is not None
                       and len(grp.children()) == 0):
                    parent = grp.parent()
                    parent.removeChildNode(grp)
                    grp = parent
            except Exception:
                continue

    def _on_open_project_action(self, *args):
        """Triggered when the user clicks File → Open Project, BEFORE the file
        dialog appears and BEFORE QgsProject.cleared fires.  Sets the loading
        flag early so _on_project_cleared knows to keep the canvas frozen."""
        self._loading_project = True
        # Safety: reset flag if no project actually loads within 30 seconds
        QTimer.singleShot(30000, self._safety_reset_loading_flag)

    def _safety_reset_loading_flag(self):
        """Fallback: if the user cancelled the open dialog the flag stays True
        forever otherwise.  Only reset if no actual load completed."""
        if self._loading_project:
            self._loading_project = False
            try:
                self.iface.mapCanvas().freeze(False)
                self.iface.mapCanvas().refresh()
            except Exception:
                pass

    def _on_read_project_start(self, *args):
        """Fired during readProjectFile (confusingly named — not before).
        Marks that a project load is in progress."""
        self._loading_project = True
        QTimer.singleShot(500, self._reset_loading_flag)

    def _reset_loading_flag(self):
        self._loading_project = False
        # Unfreeze the canvas now that all layers are fully initialised
        try:
            self.iface.mapCanvas().freeze(False)
            self.iface.mapCanvas().refresh()
        except Exception:
            pass
        # Drop empty session-only layers embedded by older saves (desk-study
        # data, buffers — all re-fetched on demand, never persisted).
        try:
            self._cleanup_stale_session_layers()
        except Exception:
            pass
        # Same for Zoomstack basemap layers an old save embedded that came
        # back broken (data moved / not installed here) — the basemap is
        # session-only and reloads from the panel.
        try:
            from . import zoomstack
            zoomstack.cleanup_broken_project_layers()
        except Exception:
            pass
        # Heal the ecological feature layers the same way a placement does.
        # Scheduled (not called) so it fires AFTER the synchronous panel
        # refresh below — nothing later in the open sequence can undo it.
        # Saved FOV cones/icons/labels used to stay invisible on reopen until
        # the user placed another feature; placement 'fixed' them via
        # layer.reload() + restyle + the curved-arc rebuild, so run that heal
        # once the project is fully open (both the panel-open and panel-closed
        # paths below).
        try:
            QTimer.singleShot(0, self._post_open_eco_refresh)
        except Exception:
            pass
        if self.panel is None:
            # A project was opened while the plugin panel was closed. If it is
            # a Biome project, open the panel for it — its __init__ restores
            # the project's groups, styling and redline. Otherwise stay
            # dormant so QGIS behaves normally.
            if self._is_biome_project():
                self._open_panel()
            return
        if self.panel is not None:
            import time as _t
            t0 = _t.perf_counter()
            try:
                from .designated_sites import _suppress_datum_dialog
                _suppress_datum_dialog()
                self.panel.layer_manager.ensure_tree_structure()
                self.panel._initialise_all_layers()
                self.panel.layer_manager.collapse_eco_group()
                self.panel._refresh_balance_widget()
                self.panel._refresh_bng_widget()
                self.panel._refresh_layers_widget()
                # Recompute redline area + centroid so the plugin recognises an
                # existing redline on reopen. Without this, _centroid_bng is unset
                # and every redline-dependent tool (desk study, GCN, TPO, buffers,
                # LPA finder) behaves as if no redline exists until one is redrawn.
                self.panel._refresh_desk_study_stats()
            except Exception:
                pass
            _slog("open: post-read panel refresh took %.1fs"
                  % (_t.perf_counter() - t0))

    def _post_open_eco_refresh(self):
        """Post-open heal for the ecological feature layers: reload their
        providers (defends against any GeoPackage write during the open
        sequence staling the OGR cache) and repaint, then rebuild the curved
        FOV-label arc layer — it is session-only (stripped from every save),
        and its only other triggers are scaleChanged and feature placement, so
        a project reopened at its saved scale showed no curved labels until the
        next placement. Never raises."""
        from qgis.core import QgsProject, QgsVectorLayer
        eco_names = {"Ecological Features", "Ecological Linear Features",
                     "Ecological Area Features", "Ecological Buffer Zones"}
        for layer in QgsProject.instance().mapLayers().values():
            try:
                if (isinstance(layer, QgsVectorLayer)
                        and layer.name() in eco_names
                        and layer.providerType() == "ogr"):
                    layer.reload()
                    layer.triggerRepaint()
            except Exception:
                continue
        self._rebuild_fov_arcs()

    def _on_project_cleared(self):
        """Called when QGIS clears the project — fires before readProjectFile
        when opening/restoring a file, and on new projects.

        We freeze the map canvas here so the signal chain:
            QgsProject::crsChanged → QgisApp::projectCrsChanged
              → QgsMapLayer::crs() on uninitialised layer → crash (QGIS 3.44)
        never reaches the uninitialised layers. The canvas is unfrozen in
        _reset_loading_flag once a project load completes, or immediately here
        for a new/blank project (nothing to crash on).

        This handler does NOT decide scaffolding — that is driven by
        _on_new_project_created (iface.newProjectCreated), which fires for
        every new-project route including the Welcome page's "New Empty
        Project" tile.
        """
        _slog("project cleared")
        try:
            self.iface.mapCanvas().freeze(True)
        except Exception:
            pass

        if self._loading_project:
            # Opening a saved file — stay frozen until _reset_loading_flag
            return

        # New or blank project — nothing to crash on; unfreeze now.
        try:
            self.iface.mapCanvas().freeze(False)
        except Exception:
            pass

    def _on_new_project_action(self, *args):
        """Project ▸ New, the New toolbar button, or Ctrl+N. This QAction is
        only triggered by a real user action, never by QGIS's startup project,
        so we start the plugin immediately."""
        self._trigger_new_project()

    def _on_new_project_created(self, *args):
        """Welcome page 'New Empty Project' tile (and any other route that calls
        fileNew() without the QAction). This also fires for QGIS's automatic
        startup project, so we only honour it once startup has settled."""
        if not self._startup_settled:
            # Emitted during/just after QGIS launch — that's QGIS's own
            # automatic project, not a user action. Leave it untouched.
            return
        self._trigger_new_project()

    def _trigger_new_project(self):
        """Start the plugin for a user-created new project. Debounced so the
        two hooks (the QAction and newProjectCreated, which both fire for
        Project ▸ New) don't scaffold twice for the same project."""
        import time
        now = time.monotonic()
        if now - self._last_new_trigger < 1.5:
            return
        self._last_new_trigger = now
        # New blank projects inherit the profile's measure/ellipsoid setting,
        # which can be unset → ellipsoid "NONE" → planar-3857 measurements.
        try:
            from .config import ensure_project_ellipsoid
            ensure_project_ellipsoid()
        except Exception:
            pass
        from .map_exporter import _SESSION
        _SESSION["client"]  = ""
        _SESSION["proj_no"] = ""
        if self.panel is not None:
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None
        # Defer slightly so QGIS finishes setting up the new blank project
        # before we add base maps and layer groups.
        QTimer.singleShot(50, self._start_for_new_project)

    def _on_init_complete(self, *args):
        """QGIS has finished launching (or the plugin was enabled at runtime).
        Arm the Welcome-page hook a short moment later — long enough that QGIS's
        own automatic startup project (which emits newProjectCreated right
        around launch) has already passed, but well before a user could read
        the Welcome page and click a tile."""
        if self._settle_scheduled:
            return
        # The 3s fallback timer can land here while QGIS is still launching
        # (a first run builds font/shader caches and can take far longer);
        # settling then would let the startup project's newProjectCreated
        # through as if the user created it. Re-arm until the main window is
        # actually up — initializationCompleted still short-circuits this.
        try:
            if not self.iface.mainWindow().isVisible():
                QTimer.singleShot(1500, self._on_init_complete)
                return
        except Exception:
            pass
        self._settle_scheduled = True
        QTimer.singleShot(1200, self._mark_startup_settled)

    def _mark_startup_settled(self):
        self._startup_settled = True
        # Biome-on-launch: open the panel ready-to-map on a blank start instead
        # of staying dormant. Runs at settle time — well after launch, so the
        # canvas/CRS machinery is fully up (the old crash window is closed).
        try:
            self._startup_auto_open()
        except Exception:
            pass

    def _startup_auto_open(self):
        """On a blank launch (no project restored), open the panel and scaffold
        the startup project like a new one, so QGIS opens ready to map. A
        restored project is left to the readProject handling, which knows
        whether it's a Biome project.

        While a QGIS welcome UI is showing, stay dormant: adding ANY layer
        makes QgisApp switch the central widget to the canvas and hide the
        welcome UI (QgisApp::showMapCanvas — the QGIS 4 'dialog' is really an
        overlay in the central widget, same as QGIS 3's page), so starting
        behind it is impossible. QGIS 3's page needs no watcher — its only
        exits (New Empty Project tile / recent project / Project menu) all
        fire the existing new/open hooks. QGIS 4's welcome screen CAN be
        dismissed with nothing chosen, so it is watched and the plugin starts
        the moment it closes."""
        if self.panel is not None or self._loading_project:
            return
        from qgis.core import QgsProject
        if QgsProject.instance().fileName():
            # On the very first install, show the requested sidebar even when
            # QGIS restored a non-Biome project. Do not scaffold or alter that
            # project; simply expose the dock.
            if getattr(self, "_first_run_workspace_pending", False):
                self._open_panel()
                self._first_run_workspace_pending = False
            return
        welcome = self._welcome_widget()
        if welcome is not None:
            try:
                if "WelcomeScreen" in welcome.metaObject().className():
                    self._watch_welcome_screen(welcome)
            except Exception:
                pass
            return
        self._open_panel()
        self._first_run_workspace_pending = False
        if self.panel is not None and \
                not getattr(self.panel, "_project_initialised", False):
            try:
                self.panel.activate_for_new_project()
            except Exception:
                pass
        # The scaffold alone must never make QGIS nag "save changes?" when the
        # user opens a project next — everything it added is recreated on
        # demand. Deferred: the scaffold's framing finishes via 0-timers.
        def _mark_clean():
            try:
                if not QgsProject.instance().fileName():
                    QgsProject.instance().setDirty(False)
            except Exception:
                pass
        QTimer.singleShot(1000, _mark_clean)

    def _watch_welcome_screen(self, widget):
        """Poll the QGIS 4 welcome screen until it disappears, then re-run the
        blank-launch auto-open. The delay after dismissal lets a project chosen
        FROM the screen begin loading first, so the guards (_loading_project /
        fileName / panel) stop the blank startup project being scaffolded
        underneath a project that is about to replace it."""
        if self._welcome_watching:
            return
        self._welcome_watching = True
        from qgis.PyQt import sip

        def tick():
            try:
                showing = (not sip.isdeleted(widget)) and widget.isVisible()
            except Exception:
                showing = False
            if showing:
                QTimer.singleShot(400, tick)
                return
            self._welcome_watching = False
            QTimer.singleShot(1200, self._startup_auto_open)

        QTimer.singleShot(400, tick)

    def _welcome_widget(self):
        """The QGIS welcome UI if it is currently visible, else None.

        QGIS 4 floats a QML welcome dialog (class QgsWelcomeScreen, hosted in
        a QQuickWidget window) above the main window; QGIS 3 embeds a
        QgsWelcomePage in the central widget stack. Neither class is in the
        stable API, so both are found by class name."""
        from qgis.PyQt.QtWidgets import QApplication, QWidget
        for w in QApplication.topLevelWidgets():
            try:
                if "WelcomeScreen" in w.metaObject().className() \
                        and w.isVisible():
                    return w
            except Exception:
                continue
        try:
            mw = self.iface.mainWindow()
            for w in mw.findChildren(QWidget):
                cls = w.metaObject().className()
                if ("WelcomeScreen" in cls or cls == "QgsWelcomePage") \
                        and w.isVisible():
                    return w
        except Exception:
            pass
        return None

    def _start_for_new_project(self):
        """Open the panel and scaffold a freshly-created blank project: base
        maps plus the habitat / ecological-feature layer-tree groups."""
        # Guard: opening a saved file also clears + recreates the project, so
        # the newProjectCreated hook can land here mid-open. If a project file
        # is (now) loaded, treat it as an existing project — scaffolding it as
        # new would add a second set of base maps on top of the saved ones.
        from qgis.core import QgsProject
        if QgsProject.instance().fileName():
            self._open_panel()
            if self.panel is not None:
                try:
                    self.panel.activate_for_existing_project()
                except Exception:
                    pass
            return
        self._open_panel()
        if self.panel is not None:
            try:
                self.panel.activate_for_new_project()
            except Exception:
                pass

    def _is_biome_project(self):
        """Heuristic: does the currently-loaded project belong to Biome?

        True if it carries our saved metadata entry or its companion
        GeoPackage exists alongside the project file. Used to decide whether
        to re-open the panel for a project QGIS loaded on its own."""
        proj = QgsProject.instance()
        if not proj.fileName():
            return False
        try:
            _, ok = proj.readEntry("BiomeMapper", "clientName", "")
            if ok:
                return True
        except Exception:
            pass
        gpkg = self._project_gpkg_path()
        return bool(gpkg and os.path.exists(gpkg))

    def _maybe_open_for_existing_project(self):
        """If QGIS started with an existing Biome project already loaded, open
        the panel for it (its __init__ restores the project's setup). Otherwise
        stay dormant so QGIS looks completely normal."""
        if self.panel is None and self._is_biome_project():
            self._open_panel()

    def _open_panel(self):
        import time as _t
        t0 = _t.perf_counter()
        from .main_panel import BNGMainPanel
        dt = _t.perf_counter() - t0
        if dt > 0.05:   # first import of the module tree only
            _slog("startup: main_panel module import took %.1fs" % dt)
        if self.panel is None:
            t0 = _t.perf_counter()
            self.panel = BNGMainPanel(self.iface)
            _slog("startup: panel construction took %.1fs"
                  % (_t.perf_counter() - t0))
            self.iface.mainWindow().addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.panel)
            try:
                from .width_debug import _log as _wlog
                _wlog(f"plugin: addDockWidget done, dock w={self.panel.width()}")
            except Exception:
                _wlog = None
            # Force the dock to its preferred width — without this Qt sizes it
            # to the widest content size-hint, which is far too wide.
            try:
                self.iface.mainWindow().resizeDocks(
                    [self.panel],
                    [getattr(self.panel, "_preferred_width", 400)],
                    Qt.Orientation.Horizontal,
                )
                if _wlog:
                    _wlog(f"plugin: resizeDocks applied, dock w={self.panel.width()}")
            except Exception:
                pass
        self.panel.show()
        self.panel.raise_()
        # In Biome mode, keep the plugin's own layer panel sitting above the
        # main panel rather than letting Qt tab the two together.
        try:
            from . import biome_mode
            if biome_mode.is_enabled():
                biome_mode.notify_main_panel_docked(self.iface, self.panel)
        except Exception:
            pass

    def toggle_panel(self):
        if self.panel is None:
            self._open_panel()
            # Opening the plugin manually on a blank, unsaved project is an
            # explicit intent to start mapping — scaffold it like a new project
            # so the base maps and layers appear.
            if self.panel is not None and not QgsProject.instance().fileName() \
                    and not getattr(self.panel, "_project_initialised", False):
                try:
                    self.panel.activate_for_new_project()
                except Exception:
                    pass
            return
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.panel.show()
            self.panel.raise_()

