# layout_fine_zoom.py
"""Snap the layout designer's fine content zoom to round scale values.

With the "Move item content" tool active, Ctrl+scroll over a map item is
QGIS's fine zoom. It is MULTIPLICATIVE — the factor is
``1 + (qgis/zoom_factor - 1) / 20``, i.e. 5% a notch at the default setting —
so it lands wherever the arithmetic falls: 178 -> 188 -> 198 -> 208. The step
also appears to "ramp" as you zoom in, which is the same thing seen from the
other end: 5% of 200 is 10, but 5% of 100 is only 5.

This module keeps that ~5% feel and simply snaps the RESULT to the next round
denominator, so the printed scale reads 1:170, 1:180, 1:190 instead. The
increment follows a 1-2-5 ladder (10s below 200, 20s to 500, 50s to 1000, then
100s, ...), which holds the per-notch change near 5% at every magnitude —
that ramping is what keeps the tool feeling the same as it does now.

Two details that matter:

* The value snapped is the TRUE ground denominator, measured exactly the way
  the sidebar's live "Scale @ A4" label measures it (extent mid-line
  transformed to EPSG:27700, per frame mm). Snapping ``map_item.scale()``
  instead would snap the PROJECTED EPSG:3857 denominator, which is ~1.6x the
  ground scale at UK latitudes — the sheet would still print an odd number.
* The point under the cursor is held still. ``set_true_scale`` zooms about the
  item centre, so the extent is translated afterwards to put the cursor's
  ground position back under the cursor, matching how the tool behaves today.

Installed from BNGPlugin.initGui via install(iface); hooks
iface.layoutDesignerOpened so designers opened later get it too. Already-hooked
detection uses the module-level _filters list, NOT a window property — module
state resets on plugin reload, so a designer that survives a reload gets
re-hooked by the fresh module, whereas a dynamic property would survive and
permanently block it. (Same reasoning as layout_clipboard.)
"""

import math

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.core import QgsLayoutItemMap, QgsRectangle

_iface = None
_installed = False
_filters = []          # [(viewport, _FineZoomFilter)]

# Below this the snapping is pointless (and a 1-2-5 step would go sub-integer).
_MIN_DENOM = 5.0


def _wheel_pos(event):
    """Wheel position as a QPoint on both bindings: Qt6 renamed
    QWheelEvent.pos() to position() (and returns a QPointF)."""
    try:
        return event.position().toPoint()
    except AttributeError:
        return event.pos()


def nice_step(denom):
    """The round increment to snap to at scale 1:*denom*.

    A 1-2-5 ladder keyed off the magnitude, so every landing value is a round
    number and each notch stays roughly 5% — the same feel as QGIS's own fine
    zoom. 178 -> step 10 (170/180/190), 452 -> step 20, 1250 -> step 100.
    """
    if denom <= 0:
        return 1.0
    d = math.floor(math.log10(denom))
    mant = denom / (10.0 ** d)
    base = 1.0 if mant < 2.0 else (2.0 if mant < 5.0 else 5.0)
    return max(1.0, base * (10.0 ** (d - 1)))


def snapped_target(denom, zoom_in):
    """The next round denominator from *denom*, or None if there is none.

    Always moves at least one increment, even when *denom* is already exactly
    on a multiple — the epsilons stop float noise (180 arriving as
    179.99999997) from making a notch a no-op or a double step."""
    if denom <= 0:
        return None
    step = nice_step(denom)
    q = denom / step
    if zoom_in:
        n = math.ceil(q - 1e-6) - 1          # zoom IN -> smaller denominator
    else:
        n = math.floor(q + 1e-6) + 1
    target = n * step
    if target < _MIN_DENOM:
        return None
    return target


def _is_move_content_tool(tool):
    """Whether *tool* is the designer's "Move item content" tool. Matched by
    type where the binding exposes it, else by class name — the tool's visible
    name is translated, so it can't be matched on that."""
    if tool is None:
        return False
    try:
        from qgis.gui import QgsLayoutViewToolMoveItemContent
        return isinstance(tool, QgsLayoutViewToolMoveItemContent)
    except Exception:
        return type(tool).__name__ == "QgsLayoutViewToolMoveItemContent"


def _map_item_at(view, pos):
    """The map item under viewport point *pos*, or None."""
    try:
        layout = view.layout()
        if layout is None:
            return None
        item = layout.layoutItemAt(view.mapToScene(pos), True)
        return item if isinstance(item, QgsLayoutItemMap) else None
    except Exception:
        return None


def _cursor_fractions(map_item, view, pos):
    """Where the cursor sits inside the map frame, as (fx, fy) in 0..1 from the
    frame's top-left. Used to hold that ground position still across the zoom."""
    try:
        local = map_item.mapFromScene(view.mapToScene(pos))
        rect = map_item.rect()
        w, h = rect.width(), rect.height()
        if w <= 0 or h <= 0:
            return 0.5, 0.5
        fx = min(1.0, max(0.0, (local.x() - rect.x()) / w))
        fy = min(1.0, max(0.0, (local.y() - rect.y()) / h))
        return fx, fy
    except Exception:
        return 0.5, 0.5


def zoom_to_next_round_scale(map_item, zoom_in, fx=0.5, fy=0.5):
    """Zoom *map_item* to the next round TRUE ground scale, holding the point
    at fraction (fx, fy) of the frame still. Returns the new denominator, or
    None if nothing was changed (caller then lets QGIS handle the event)."""
    from . import final_layout
    try:
        cur = final_layout._bng_scale_denominator(map_item)
        if cur <= 0:
            return None
        target = snapped_target(cur, zoom_in)
        if target is None:
            return None

        before = map_item.extent()
        # Ground position currently under the cursor.
        anchor_x = before.xMinimum() + fx * before.width()
        anchor_y = before.yMaximum() - fy * before.height()

        final_layout.set_true_scale(map_item, target)

        after = map_item.extent()
        if after is None or after.isEmpty():
            return None
        # Put the anchor back under the cursor. The rect keeps the post-zoom
        # width/height, so it still matches the frame aspect exactly and
        # zoomToExtent only recentres — it cannot alter the scale we just set.
        w, h = after.width(), after.height()
        x0 = anchor_x - fx * w
        y1 = anchor_y + fy * h
        map_item.zoomToExtent(QgsRectangle(x0, y1 - h, x0 + w, y1))
        return target
    except Exception:
        return None


class _FineZoomFilter(QObject):
    """Intercepts Ctrl+wheel on one designer view's viewport."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view

    def eventFilter(self, obj, event):
        try:
            if event.type() != QEvent.Type.Wheel:
                return False
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                return False
            view = self._view
            if view is None or sip.isdeleted(view):
                return False
            if not _is_move_content_tool(view.tool()):
                return False
            delta = event.angleDelta().y()
            if not delta:
                return False
            pos = _wheel_pos(event)
            map_item = _map_item_at(view, pos)
            if map_item is None:
                return False
            fx, fy = _cursor_fractions(map_item, view, pos)
            if zoom_to_next_round_scale(map_item, delta > 0, fx, fy) is None:
                return False          # nothing snapped — let QGIS zoom normally
            return True               # consumed
        except Exception:
            return False


def _hook_designer(designer):
    try:
        view = designer.view()
    except Exception:
        return
    if view is None or sip.isdeleted(view):
        return
    try:
        viewport = view.viewport()
    except Exception:
        return
    if viewport is None:
        return
    for vp, _f in _filters:
        if vp is viewport:
            return                    # already hooked
    filt = _FineZoomFilter(view)
    viewport.installEventFilter(filt)
    _filters.append((viewport, filt))


def install(iface):
    """Hook layoutDesignerOpened and retrofit any designers already open."""
    global _iface, _installed
    _iface = iface
    if not _installed:
        try:
            iface.layoutDesignerOpened.connect(_hook_designer)
            _installed = True
        except Exception:
            pass
    try:
        for designer in iface.openLayoutDesigners():
            _hook_designer(designer)
    except Exception:
        pass


def uninstall(iface):
    """Disconnect the hook and remove the filters from live designer views."""
    global _installed
    if _installed:
        try:
            iface.layoutDesignerOpened.disconnect(_hook_designer)
        except Exception:
            pass
        _installed = False
    for viewport, filt in _filters:
        try:
            if viewport is not None and not sip.isdeleted(viewport):
                viewport.removeEventFilter(filt)
        except Exception:
            pass
    _filters.clear()
