# biome_mode.py — "Biome mode": restyle the whole QGIS window to match the
# plugin's moss & chalk identity and hide the chrome a Biome workflow never
# touches (toolbars, Browser/Layers panels).
#
# Design rules:
#   * Polite. The user's previous state (toolbar/panel visibility, app
#     stylesheet, font) is snapshotted into QgsSettings when the mode is first
#     enabled, and restored exactly on disable. The snapshot survives QGIS
#     restarts, because QGIS itself persists our hidden toolbars as "hidden" —
#     re-snapshotting at startup would capture that and make disable a no-op.
#   * Reversible. disable() always works, even after a restart or reinstall.
#   * Conservative. The map canvas and the dock's own stylesheet are left
#     alone (widget-level stylesheets override the app stylesheet anyway).

import json

from qgis.PyQt.QtWidgets import (
    QApplication, QToolBar, QDockWidget, QStyleFactory, QWidget, QSizePolicy,
)
from qgis.PyQt.QtGui import QFont, QPalette, QColor
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.core import QgsSettings

from . import theme

KEY_ENABLED  = "biome/mode_enabled"
KEY_SNAPSHOT = "biome/mode_snapshot"

# Panels a Biome workflow doesn't use. The native Layers panel is hidden too:
# Biome mode replaces it with the plugin's own compact layer panel (see
# biome_layer_panel.py), so its pre-Biome visibility is snapshotted and restored
# on disable like the others.
HIDE_DOCKS = ("Browser", "Browser2", "LayerOrder", "Layers")

# The plugin's own layer panel, created while Biome mode is on. Module-level so
# its lifecycle follows the mode (enable / startup / disable / unload), not the
# main Biome panel — the mode can be on without the main panel being open. It is
# a floating overlay on the map canvas (not a dock), so it stays only as tall as
# its layers with the map visible all around it.
_layer_panel = None


# ── App-wide stylesheet ──────────────────────────────────────────────────────

def app_qss():
    """Whole-application stylesheet built from the same tokens as the dock.

    Covers the menu bar, menus, dialogs, status bar, item views, inputs and
    scrollbars so QGIS chrome matches the panel. Deliberately does not style
    check/radio indicators (native ones are clearer) or the map canvas.
    """
    t = theme
    chev        = t._qss_icon("chevron-down", t.MUTED)
    chev_up     = t._qss_icon("chevron-up", t.MUTED)
    chev_dim    = t._qss_icon("chevron-down", t.BORDER_STRONG)
    chev_up_dim = t._qss_icon("chevron-up", t.BORDER_STRONG)
    return f"""
QMainWindow, QDialog, QWidget#QgsMessageLogViewer {{
    background: {t.SURFACE};
}}
QMainWindow::separator {{
    background: {t.BORDER}; width: 3px; height: 3px;
}}

QMenuBar {{
    background: {t.HEADER};
    color: white;
    border-bottom: 2px solid {t.LICHEN};
    font-family: {t.FONT_MEDIUM};
    padding: 1px 4px;
}}
QMenuBar::item {{
    background: transparent; color: white; padding: 5px 10px; border-radius: 5px;
}}
QMenuBar::item:selected {{ background: rgba(255,255,255,0.14); color: white; }}
QMenuBar::item:pressed  {{ background: rgba(255,255,255,0.22); }}

QMenu {{
    background: {t.CARD};
    color: {t.INK};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 24px 5px 12px; border-radius: 5px;
}}
QMenu::item:selected  {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT_DARK}; }}
QMenu::item:disabled  {{ color: {t.FAINT}; }}
QMenu::separator {{
    height: 1px; background: {t.BORDER}; margin: 4px 8px;
}}

QStatusBar {{
    background: {t.SURFACE};
    color: {t.MUTED};
    border-top: 2px solid {t.LICHEN};
}}
QStatusBar QLabel {{ color: {t.MUTED}; }}

QDockWidget {{
    color: {t.ACCENT_DARK};
    titlebar-close-icon: none;
}}
QDockWidget::title {{
    background: {t.ACCENT_SOFT};
    border-bottom: 1px solid {t.BORDER_STRONG};
    padding: 5px 8px;
    text-align: left;
}}

/* Tooltip fallback colours. With this app stylesheet active, QGIS's Qt build
   ignores both this rule and the tooltip palette — theme._install_tip_filter()
   is what actually forces tooltips dark, per-instance, at show time. Kept for
   builds that DO honour the rule. No border/radius (those can drop the fill). */
QToolTip {{
    background-color: {t.INK}; color: {t.CARD};
    padding: 5px 9px; font-size: 12px;
}}

QDialog QPushButton, QMessageBox QPushButton {{
    background: {t.CARD};
    color: {t.INK};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 14px;
    min-height: 18px;
}}
QDialog QPushButton:hover, QMessageBox QPushButton:hover {{
    background: {t.ACCENT_SOFT}; border-color: {t.ACCENT};
}}
QDialog QPushButton:default, QMessageBox QPushButton:default {{
    background: {t.ACCENT}; color: white; border-color: {t.ACCENT};
}}
QDialog QPushButton:default:hover, QMessageBox QPushButton:default:hover {{
    background: {t.ACCENT_DARK};
}}
QDialog QPushButton:disabled, QMessageBox QPushButton:disabled {{
    color: {t.FAINT}; background: {t.SUNKEN};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
    background: {t.CARD};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: 6px;
    padding: 3px 7px;
    selection-background-color: {t.ACCENT_SOFT};
    selection-color: {t.INK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {t.ACCENT};
}}
QComboBox QAbstractItemView {{
    background: {t.CARD};
    border: 1px solid {t.BORDER_STRONG};
    selection-background-color: {t.ACCENT_SOFT};
    selection-color: {t.INK};
}}

/* Styling a QSpinBox drops Qt's native up/down buttons, so they must be
   rebuilt explicitly or they render as blank slivers. */
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; margin: 2px 2px 0 0;
    border: none; border-radius: 4px; background: transparent;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; margin: 0 2px 2px 0;
    border: none; border-radius: 4px; background: transparent;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {t.ACCENT_SOFT};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({chev_up}); width: 11px; height: 11px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({chev}); width: 11px; height: 11px;
}}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off,
QDoubleSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:off {{
    image: url({chev_up_dim}); width: 11px; height: 11px;
}}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off,
QDoubleSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:off {{
    image: url({chev_dim}); width: 11px; height: 11px;
}}

QTreeView, QTableView, QListView {{
    background: {t.CARD};
    alternate-background-color: {t.SUNKEN};
    border: 1px solid {t.BORDER};
    selection-background-color: {t.ACCENT_SOFT};
    selection-color: {t.INK};
}}
QHeaderView::section {{
    background: {t.SUNKEN};
    color: {t.MUTED};
    border: none;
    border-right: 1px solid {t.BORDER};
    border-bottom: 1px solid {t.BORDER};
    padding: 4px 8px;
}}

QTabWidget::pane {{
    border: 1px solid {t.BORDER}; border-radius: 6px; background: {t.CARD};
}}
QTabBar::tab {{
    background: {t.SUNKEN}; color: {t.MUTED};
    padding: 6px 14px;
    border: 1px solid {t.BORDER}; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{ background: {t.CARD}; color: {t.INK}; }}
QTabBar::tab:hover:!selected {{ background: {t.ACCENT_SOFT}; }}

QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t.BORDER_STRONG}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.FAINT}; }}
QScrollBar:horizontal {{
    background: transparent; height: 9px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t.BORDER_STRONG}; border-radius: 4px; min-width: 32px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

QProgressBar {{
    background: {t.SUNKEN}; border: 1px solid {t.BORDER};
    border-radius: 6px; text-align: center; color: {t.INK};
}}
QProgressBar::chunk {{ background: {t.ACCENT}; border-radius: 5px; }}

QGroupBox {{
    border: 1px solid {t.BORDER}; border-radius: 8px;
    margin-top: 10px; padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: {t.MUTED};
}}

QToolBar {{
    background: {t.SURFACE};
    border: none; border-bottom: 1px solid {t.BORDER};
    spacing: 2px; padding: 2px;
}}
QToolBar::separator {{
    background: {t.BORDER}; width: 1px; margin: 4px 3px;
}}
QToolButton {{
    background: transparent; color: {t.INK};
    border: none; border-radius: 5px; padding: 3px;
}}
QToolButton:hover    {{ background: {t.ACCENT_SOFT}; }}
QToolButton:pressed  {{ background: {t.BORDER}; }}
QToolButton:checked  {{ background: {t.ACCENT_SOFT}; }}
QToolButton::menu-indicator {{ image: none; }}

QTreeView::item {{
    padding: 2px 2px; border-radius: 4px;
}}
QTreeView::item:hover:!selected {{ background: {t.SUNKEN}; }}
QTreeView::item:selected {{
    background: {t.ACCENT_SOFT}; color: {t.INK};
}}
QTreeView, QTableView, QListView {{ outline: none; }}

/* ── Layers panel ────────────────────────────────────────────────────────
   The Layers tree is a QgsLayerTreeView. These class-specific rules win over
   the generic QTreeView block above (greater selector specificity) and give
   the panel a deliberate Biome look: a flush chalk body, roomier rows, and a
   moss-on-mist selected state instead of the default highlight. They live in
   app_qss(), so they apply ONLY in Biome mode and are undone on disable with
   the rest of the application stylesheet. The symbology preview icons stay
   untouched — they're rendered from each layer's renderer, not from QSS. */
QgsLayerTreeView {{
    background: {t.CARD};
    border: none;
    padding: 2px 3px;
    selection-background-color: {t.ACCENT_SOFT};
    selection-color: {t.ACCENT_DARK};
    outline: none;
}}
QgsLayerTreeView::item {{
    padding: 3px 2px; border-radius: 5px; min-height: 22px;
}}
QgsLayerTreeView::item:hover:!selected {{ background: {t.SUNKEN}; }}
QgsLayerTreeView::item:selected {{
    background: {t.ACCENT_SOFT}; color: {t.ACCENT_DARK};
}}

QSlider::groove:horizontal {{
    height: 4px; background: {t.BORDER}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0;
    background: {t.ACCENT}; border: 2px solid white; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: {t.ACCENT}; border-radius: 2px;
}}

QgsMessageBar, QgsMessageBarItem {{
    background: {t.ACCENT_SOFT};
    border: 1px solid {t.ACCENT};
    border-radius: 7px;
    color: {t.ACCENT_DARK};
}}
QgsMessageBar QLabel {{ color: {t.ACCENT_DARK}; background: transparent; }}

QCheckBox, QRadioButton, QLabel {{ color: {t.INK}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.FAINT}; }}

QDockWidget::close-button, QDockWidget::float-button {{
    background: transparent; border: none; padding: 1px;
}}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
    background: {t.BORDER};
    border-radius: 4px;
}}

QMainWindow::separator:hover {{ background: {t.LICHEN}; }}
"""


# ── Application palette ──────────────────────────────────────────────────────
# The stylesheet covers widgets it names, but plenty of QGIS chrome (panel
# bodies, native-drawn controls, third-party plugin widgets) takes its colour
# from the application palette. Setting both is what makes the WHOLE window
# change colour.

_PALETTE_ROLES = (
    "Window", "WindowText", "Base", "AlternateBase", "Text",
    "Button", "ButtonText", "BrightText", "Highlight", "HighlightedText",
    "ToolTipBase", "ToolTipText", "Link", "PlaceholderText",
)


def _snapshot_palette(app):
    pal = app.palette()
    out = {}
    for name in _PALETTE_ROLES:
        role = getattr(QPalette, name, None)
        if role is not None:
            out[name] = pal.color(role).name()
    return out


def _restore_palette(app, snap):
    colors = (snap or {}).get("palette")
    if not colors:
        try:
            app.setPalette(app.style().standardPalette())
        except Exception:
            pass
        return
    pal = QPalette(app.palette())
    for name, hexv in colors.items():
        role = getattr(QPalette, name, None)
        if role is not None:
            pal.setColor(role, QColor(hexv))
    app.setPalette(pal)


def _biome_palette_map():
    """The Biome app palette as {QPalette.ColorRole: '#rrggbb'}. Single source
    for both the live QPalette (_biome_palette) and the QGIS 4 theme registry's
    palette.txt (biome_app_theme). Reads current theme tokens, so it follows
    light/dark."""
    t = theme
    R = QPalette.ColorRole
    return {
        R.Window:          t.SURFACE,
        R.WindowText:      t.INK,
        R.Base:            t.CARD,
        R.AlternateBase:   t.SUNKEN,
        R.Text:            t.INK,
        R.Button:          t.SURFACE,
        R.ButtonText:      t.INK,
        R.BrightText:      "#FFFFFF",
        R.Highlight:       t.ACCENT,
        R.HighlightedText: "#FFFFFF",
        R.ToolTipBase:     t.INK,
        R.ToolTipText:     t.CARD,
        R.Link:            t.ACCENT_DARK,
        R.PlaceholderText: t.FAINT,
    }


def _biome_palette(app):
    t = theme
    pal = QPalette(app.palette())
    for role, color in _biome_palette_map().items():
        try:
            pal.setColor(role, QColor(color))
        except Exception:
            pass
    # Disabled-group text is dimmed — not expressible in the registry's
    # palette.txt, so it's set here (and reinforced after setUITheme on QGIS 4).
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.FAINT))
    return pal


# ── Settings helpers ─────────────────────────────────────────────────────────

def _settings():
    return QgsSettings()


def is_enabled():
    try:
        return _settings().value(KEY_ENABLED, False, type=bool)
    except Exception:
        return False


def is_set():
    """True once the user has explicitly enabled or disabled Biome mode at least
    once. False on a fresh install — used to turn the mode on by default."""
    try:
        return _settings().contains(KEY_ENABLED)
    except Exception:
        return True


def _load_snapshot():
    raw = _settings().value(KEY_SNAPSHOT, "", type=str)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _save_snapshot(snap):
    _settings().setValue(KEY_SNAPSHOT, json.dumps(snap))


# ── Window introspection ─────────────────────────────────────────────────────

def _main_toolbars(iface):
    """Top-level toolbars of the main window, keyed by a stable name."""
    mw = iface.mainWindow()
    out = {}
    for tb in mw.findChildren(QToolBar):
        if tb.parent() is not mw:
            continue
        key = tb.objectName() or tb.windowTitle()
        if key:
            out[key] = tb
    return out


def _docks(iface):
    mw = iface.mainWindow()
    out = {}
    for d in mw.findChildren(QDockWidget):
        key = d.objectName()
        if key:
            out[key] = d
    return out


# ── Biome layer panel ────────────────────────────────────────────────────────
# Biome mode hides the native Layers panel and shows the plugin's own compact
# show/hide/reorder panel in its place — a floating overlay pinned to the
# top-left of the map canvas, so it is only as tall as its layers and the map
# stays visible all around it.

def layer_panel():
    """The live Biome layer panel, or None when the mode is off."""
    return _layer_panel


def _show_layer_panel(iface):
    global _layer_panel
    if _layer_panel is None:
        try:
            from .biome_layer_panel import BiomeLayerPanel
            canvas = iface.mapCanvas()
            _layer_panel = BiomeLayerPanel(iface, canvas)
            _layer_panel.attach(canvas)
        except Exception as exc:
            _layer_panel = None
            try:
                from qgis.core import QgsMessageLog, Qgis
                import traceback
                QgsMessageLog.logMessage(
                    "Biome layer panel failed: " + repr(exc) + "\n"
                    + traceback.format_exc(), "Biome", Qgis.MessageLevel.Warning)
            except Exception:
                pass
            return
    try:
        _layer_panel.attach(iface.mapCanvas())
        _layer_panel.refresh_now()
    except Exception:
        pass


def _hide_layer_panel(iface):
    global _layer_panel
    if _layer_panel is not None:
        try:
            _layer_panel.teardown()
            _layer_panel.setParent(None)
            _layer_panel.deleteLater()
        except Exception:
            pass
        _layer_panel = None
    # Make sure the native Layers panel comes back even if the snapshot
    # predates "Layers" being in HIDE_DOCKS.
    d = _docks(iface).get("Layers")
    if d is not None:
        d.setVisible(True)


def notify_main_panel_docked(iface, main_dock):
    """Called when the main Biome panel is (re)docked while the mode is on. The
    layer panel lives on the left and the main panel on the right, so they don't
    share an area — just re-pin the layer panel's fitted height in case the
    relayout disturbed it."""
    if _layer_panel is None:
        return
    try:
        _layer_panel.refresh_now()
    except Exception:
        pass


def refresh_layer_panel_stats():
    """Update the layer panel's stats footer — called when habitat data changes
    (features placed / edited / deleted). No-op when the mode is off."""
    if _layer_panel is None:
        return
    try:
        _layer_panel.refresh_footer()
    except Exception:
        pass


# ── Enable / disable ─────────────────────────────────────────────────────────

def enable(iface):
    """Turn Biome mode on, snapshotting the current state first.

    If a snapshot already exists (mode left on from a previous session),
    it is kept — that snapshot describes the user's true pre-Biome state.
    """
    app = QApplication.instance()

    if _load_snapshot() is None:
        font = app.font()
        snap = {
            "app_qss": app.styleSheet() or "",
            "app_style": app.style().objectName() or "",
            "font_family": font.family(),
            "font_point": font.pointSize(),
            "palette": _snapshot_palette(app),
            "toolbars": {k: tb.isVisible() for k, tb in _main_toolbars(iface).items()},
            "docks": {k: d.isVisible() for k, d in _docks(iface).items()
                      if k in HIDE_DOCKS},
        }
        _save_snapshot(snap)

    _settings().setValue(KEY_ENABLED, True)
    _apply(iface)
    _show_layer_panel(iface)

    # Toolbars from plugins that load late: sweep again shortly after.
    QTimer.singleShot(2500, lambda: _rehide(iface))


def disable(iface):
    """Restore the exact pre-Biome state and forget the snapshot."""
    app = QApplication.instance()
    snap = _load_snapshot() or {}

    _hide_layer_panel(iface)

    # QGIS 4: if the app style was delivered via the theme registry, reset the
    # UI-theme state to default first so QGIS's theme selector is consistent;
    # the snapshot restore below then re-applies any pre-Biome stylesheet.
    _clear_registry_theme()

    app.setStyleSheet(snap.get("app_qss", ""))
    _restore_palette(app, snap)

    style_name = snap.get("app_style")
    if style_name:
        try:
            st = QStyleFactory.create(style_name)
            if st is not None:
                app.setStyle(st)
        except Exception:
            pass

    fam = snap.get("font_family")
    if fam:
        pt = snap.get("font_point", -1)
        f = QFont(fam)
        if pt and pt > 0:
            f.setPointSize(pt)
        app.setFont(f)

    tb_state = snap.get("toolbars", {})
    for key, tb in _main_toolbars(iface).items():
        tb.setVisible(tb_state.get(key, True))

    dock_state = snap.get("docks", {})
    for key, d in _docks(iface).items():
        if key in dock_state:
            d.setVisible(dock_state[key])

    _settings().setValue(KEY_ENABLED, False)
    _settings().remove(KEY_SNAPSHOT)
    global _last_reapplied_mode
    _last_reapplied_mode = None


_last_reapplied_mode = None


def reapply_style_if_enabled(iface):
    """Re-apply only the app-wide palette + stylesheet (light/dark follows the
    theme tokens) when Biome mode is on. Called on an OS light/dark flip so the
    whole-QGIS restyle tracks the theme without re-snapshotting or touching
    toolbar/dock visibility. No-op when Biome mode is off, and skipped entirely
    when the mode hasn't actually changed — the app-wide restyle is the single
    most expensive part of a flip (QGIS repolishes every widget), so it must
    run at most once per flip."""
    global _last_reapplied_mode
    if not is_enabled():
        return
    if theme.current_mode() == _last_reapplied_mode:
        return
    app = QApplication.instance()
    if app is None:
        return
    import time as _t
    applied = False
    try:
        from . import biome_app_theme
        if biome_app_theme.is_available():
            t0 = _t.perf_counter()
            reg_ok = biome_app_theme.build_and_register(app_qss(), _biome_palette_map())
            theme._tlog("  biome_mode: build_and_register %.1fms" % ((_t.perf_counter() - t0) * 1000))
            if reg_ok:
                t0 = _t.perf_counter()
                applied = biome_app_theme.apply()
                theme._tlog("  biome_mode: setUITheme %.1fms" % ((_t.perf_counter() - t0) * 1000))
    except Exception:
        applied = False
    if not applied:
        t0 = _t.perf_counter()
        app.setStyleSheet(app_qss())
        theme._tlog("  biome_mode: app setStyleSheet %.1fms" % ((_t.perf_counter() - t0) * 1000))
        # Fallback path has no palette.txt, so the palette pass is required.
        t0 = _t.perf_counter()
        app.setPalette(_biome_palette(app))
        theme._tlog("  biome_mode: app setPalette %.1fms" % ((_t.perf_counter() - t0) * 1000))
    # Registry path: setUITheme already applied palette.txt. The old extra
    # setPalette "reinforcement" (only to dim disabled-group text, which
    # palette.txt can't express) queued a SECOND application-wide repolish
    # storm — tens of seconds of trailing repaints on a big session — for a
    # cosmetic nicety. Dropped from the flip path; :disabled QSS rules cover
    # the important cases.
    _last_reapplied_mode = theme.current_mode()


def _clear_registry_theme():
    """Reset the QGIS 4 UI theme to default if Biome was applied via the theme
    registry. No-op on QGIS 3. Does not unregister the theme (kept for re-enable
    within the session; unload removes it)."""
    try:
        from . import biome_app_theme
        if biome_app_theme.is_available():
            biome_app_theme.clear()
    except Exception:
        pass


def apply_startup(iface):
    """Re-apply on QGIS launch if the mode was left on (snapshot preserved)."""
    if is_enabled():
        _apply(iface)
        _show_layer_panel(iface)
        QTimer.singleShot(2500, lambda: _rehide(iface))


def restore_for_unload(iface):
    """Plugin is being unloaded: undo the visuals but keep the enabled flag
    and snapshot, so the mode comes straight back if the plugin is reloaded."""
    if not is_enabled():
        return
    _hide_layer_panel(iface)
    app = QApplication.instance()
    snap = _load_snapshot() or {}
    # QGIS 4: reset the UI theme to default and remove the Biome theme from the
    # registry (unload should leave no trace); re-enable re-registers it.
    _clear_registry_theme()
    try:
        from . import biome_app_theme
        biome_app_theme.unregister()
    except Exception:
        pass
    app.setStyleSheet(snap.get("app_qss", ""))
    _restore_palette(app, snap)

    style_name = snap.get("app_style")
    if style_name:
        try:
            st = QStyleFactory.create(style_name)
            if st is not None:
                app.setStyle(st)
        except Exception:
            pass
    fam = snap.get("font_family")
    if fam:
        f = QFont(fam)
        pt = snap.get("font_point", -1)
        if pt and pt > 0:
            f.setPointSize(pt)
        app.setFont(f)
    tb_state = snap.get("toolbars", {})
    for key, tb in _main_toolbars(iface).items():
        tb.setVisible(tb_state.get(key, True))
    dock_state = snap.get("docks", {})
    for key, d in _docks(iface).items():
        if key in dock_state:
            d.setVisible(dock_state[key])


# ── Internals ────────────────────────────────────────────────────────────────

def _apply(iface):
    app = QApplication.instance()

    theme._load_fonts()
    f = QFont("Inter")
    f.setPointSize(max(app.font().pointSize(), 9))
    app.setFont(f)

    # The native Windows style draws menus/toolbars with the OS theme engine
    # and IGNORES the application palette — that's why the window stayed grey.
    # Fusion respects the palette fully, so switch to it for the mode. The
    # previous style name is kept in the snapshot for exact restore. (Older
    # snapshots predate this key — backfill before switching.)
    cur_style = app.style().objectName() or ""
    if cur_style.lower() != "fusion":
        snap = _load_snapshot()
        if snap is not None and not snap.get("app_style"):
            snap["app_style"] = cur_style
            _save_snapshot(snap)
        try:
            st = QStyleFactory.create("Fusion")
            if st is not None:
                app.setStyle(st)
        except Exception:
            pass

    # Style first, then palette (reaches every widget), then the stylesheet.
    app.setPalette(_biome_palette(app))
    # QGIS 4: deliver the app stylesheet through the sanctioned theme registry
    # (appears in Settings ▸ Interface ▸ UI theme, fires themeChanged, and is
    # cleared cleanly with setUITheme("default")). Falls back to a raw
    # setStyleSheet if the registry route is unavailable or fails. QGIS 3 always
    # takes the setStyleSheet path.
    _applied_via_registry = False
    try:
        from . import biome_app_theme
        if biome_app_theme.is_available():
            if biome_app_theme.build_and_register(app_qss(), _biome_palette_map()):
                _applied_via_registry = biome_app_theme.apply()
                # setUITheme re-applies the theme palette; reinforce our fuller
                # palette (incl. disabled-group dimming) on top of it.
                if _applied_via_registry:
                    app.setPalette(_biome_palette(app))
    except Exception:
        _applied_via_registry = False
    if not _applied_via_registry:
        app.setStyleSheet(app_qss())

    docks = _docks(iface)
    # Backfill the snapshot for docks added to HIDE_DOCKS after it was first
    # taken (e.g. "Layers", which earlier builds kept visible) so disable()
    # still restores their true pre-Biome visibility instead of leaving them
    # hidden. A missing key is assumed to have been visible.
    snap = _load_snapshot()
    if snap is not None:
        dstate = snap.setdefault("docks", {})
        changed = False
        for key in HIDE_DOCKS:
            if key in docks and key not in dstate:
                dstate[key] = True
                changed = True
        if changed:
            _save_snapshot(snap)

    for tb in _main_toolbars(iface).values():
        tb.setVisible(False)
    for key, d in docks.items():
        if key in HIDE_DOCKS:
            d.setVisible(False)

    global _last_reapplied_mode
    _last_reapplied_mode = theme.current_mode()


def _rehide(iface):
    """Catch toolbars that appeared after enable (late-loading plugins).

    Any new toolbar is recorded into the snapshot as previously-visible so
    disable() brings it back, then hidden.
    """
    if not is_enabled():
        return
    snap = _load_snapshot()
    if snap is None:
        return
    changed = False
    for key, tb in _main_toolbars(iface).items():
        if key not in snap.get("toolbars", {}):
            snap.setdefault("toolbars", {})[key] = True
            changed = True
        if tb.isVisible():
            tb.setVisible(False)
    if changed:
        _save_snapshot(snap)
