# -*- coding: utf-8 -*-
"""
live_edit.py
============

**Live Claude Edit** — keeps the Biome layers on an auto-refresh timer so that
edits written straight into the project GeoPackage by something outside QGIS
appear on the canvas without a manual reload.

Why it is needed
----------------
QGIS reads a layer's features through its provider and caches them for
rendering.  An outside process can write to ``<project>_bng_data.gpkg`` quite
happily — GDAL takes the SQLite lock, commits, releases — but the running
canvas keeps drawing its cached copy until something tells it to re-read.
``Qgis.AutoRefreshMode.ReloadData`` does exactly that on a timer.

Scope and safety
----------------
* Only Biome's own layers are touched (``layer_manager.LAYER_NAMES``), never
  basemaps or anything the user has added.
* The setting lives in the QGIS profile, so it survives plugin updates, and is
  re-applied whenever a project is opened.
* It is OFF by default.  Auto-refresh re-renders on a timer, which is a
  distraction while digitising and pointless when nothing external is writing.
* It never touches the QGIS project file. A project file is read once at open and
  rewritten wholesale on save, so an outside edit to it is invisible at best
  and silently overwritten at worst.  Only the GeoPackage is safe to edit
  underneath a running QGIS.

A layer that is mid-edit in QGIS is skipped — reloading it would throw away
the user's uncommitted work.
"""

from qgis.core import Qgis, QgsProject, QgsSettings, QgsVectorLayer

from .layer_manager import LAYER_NAMES

_KEY_ON = "biome/live_claude_edit/enabled"
_KEY_SECS = "biome/live_claude_edit/interval"

DEFAULT_SECS = 5
MIN_SECS = 2
MAX_SECS = 60


# ── settings ────────────────────────────────────────────────────────────────

def is_enabled():
    return QgsSettings().value(_KEY_ON, False, type=bool)


def set_enabled(on):
    QgsSettings().setValue(_KEY_ON, bool(on))


def interval_seconds():
    try:
        secs = int(QgsSettings().value(_KEY_SECS, DEFAULT_SECS))
    except (TypeError, ValueError):
        secs = DEFAULT_SECS
    return max(MIN_SECS, min(MAX_SECS, secs))


def set_interval_seconds(secs):
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        secs = DEFAULT_SECS
    QgsSettings().setValue(_KEY_SECS, max(MIN_SECS, min(MAX_SECS, secs)))


# ── layer plumbing ──────────────────────────────────────────────────────────

def biome_layers(project=None):
    """Biome's own vector layers in the project, by layer name."""
    project = project or QgsProject.instance()
    wanted = set(LAYER_NAMES.values())
    out = []
    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer):
            continue
        if layer.name() in wanted:
            out.append(layer)
    return out


def _apply_to_layer(layer, on, secs):
    """Set (or clear) auto-refresh on one layer. Returns True if it took."""
    # Never reload a layer with uncommitted edits — it would discard them.
    try:
        if on and layer.isEditable():
            return False
    except Exception:
        pass
    try:
        layer.setAutoRefreshInterval(int(secs) * 1000 if on else 0)
    except Exception:
        return False
    # QGIS 3.34+ and 4.x: mode enum. ReloadData re-reads the provider, which is
    # the whole point — RedrawOnly would just repaint the stale cache.
    try:
        layer.setAutoRefreshMode(Qgis.AutoRefreshMode.ReloadData if on
                                 else Qgis.AutoRefreshMode.Disabled)
        return True
    except Exception:
        pass
    try:                                     # QGIS 3.22-3.32 fallback
        layer.setAutoRefreshEnabled(bool(on))
        return True
    except Exception:
        return False


def apply(project=None, force=None):
    """Push the current setting onto every Biome layer.

    ``force`` overrides the stored setting (used by the dialog so the effect is
    visible before the user closes it). Returns the number of layers changed.
    """
    on = is_enabled() if force is None else bool(force)
    secs = interval_seconds()
    n = 0
    for layer in biome_layers(project):
        if _apply_to_layer(layer, on, secs):
            n += 1
    return n


# ── status line for the settings cog ────────────────────────────────────────

def status_text():
    if not is_enabled():
        return "Off — external edits need a manual layer reload."
    n = len(biome_layers())
    secs = interval_seconds()
    if not n:
        return f"On, every {secs}s — no Biome layers in this project yet."
    return (f"On — {n} Biome layer{'s' if n != 1 else ''} re-read from the "
            f"GeoPackage every {secs}s.")
