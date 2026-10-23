# live_measure.py
"""Floating measurement label that tracks the cursor while digitising with the
NATIVE QGIS add-feature tool (Quick Measure sketches, habitat-form lines).

The native capture tool offers no hook into its in-progress vertex list, so
the label runs off a canvas event filter that mirrors the clicks: left click
adds a vertex, right click finishes/aborts the shape, Backspace/Delete undoes
the last vertex, Escape abandons. The mirrored points are an estimate — the
capture tool may snap a click elsewhere — which is fine for a live readout;
the committed feature is always measured properly afterwards.

Custom QgsMapTool subclasses should NOT use this: they own canvasMoveEvent
(with QgsMapMouseEvent, where .pos() is safe) and can update a label directly.
"""

from qgis.PyQt.QtCore import Qt, QObject, QEvent
from qgis.PyQt.QtWidgets import QLabel
from qgis.core import QgsDistanceArea, QgsGeometry, QgsPointXY, QgsProject

from .biome_layer_panel import _evt_pos
from .config import measurement_ellipsoid as _ellipsoid
from . import theme


def _default_fmt_area(area_m2):
    if area_m2 >= 10_000:
        return f"{area_m2 / 10_000:,.4f} ha"
    return f"{area_m2:,.1f} m²"


def install_live_measure(iface, geom_type, fmt_area=None):
    """Attach the live label to the map canvas and return the filter object.

    ``geom_type`` is "polygon" or "line"; ``fmt_area`` optionally overrides
    the polygon area format (e.g. the Quick Measure m²/ha unit toggle).
    Pass the returned object to :func:`remove_live_measure` when the draw
    session ends. Callers keep exactly one installed at a time.
    """
    if fmt_area is None:
        fmt_area = _default_fmt_area

    canvas   = iface.mapCanvas()
    viewport = canvas.viewport()

    live_lbl = QLabel(viewport)
    live_lbl.setStyleSheet(
        "background:%s; color:%s; border-radius:5px;"
        " padding:3px 8px; font-family:%s; font-size:11px; font-weight:600;"
        % (theme.INK, theme.SURFACE, theme.FONT_SEMIBOLD)
    )
    live_lbl.hide()
    live_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    tracked = {"pts": []}

    da = QgsDistanceArea()
    canvas_crs = canvas.mapSettings().destinationCrs()
    da.setSourceCrs(canvas_crs, QgsProject.instance().transformContext())
    da.setEllipsoid(_ellipsoid())

    def _to_map(x, y):
        return canvas.mapSettings().mapToPixel().toMapCoordinates(x, y)

    def _measure(pts):
        try:
            if geom_type == "polygon" and len(pts) >= 3:
                closed = pts + [pts[0]]
                geom = QgsGeometry.fromPolygonXY([closed])
                return fmt_area(da.measureArea(geom))
            elif len(pts) >= 2:
                # Lines always; a polygon with only one clicked vertex
                # reads as a running segment length until it has an area.
                geom = QgsGeometry.fromPolylineXY(pts)
                length = da.measureLength(geom)
                return f"{length:,.1f} m"
        except Exception:
            pass
        return None

    class _LiveFilter(QObject):
        def eventFilter(self, obj, event):
            try:
                if event.type() == QEvent.Type.MouseButtonPress:
                    p = _evt_pos(event)
                    if event.button() == Qt.MouseButton.LeftButton:
                        mp = _to_map(p.x(), p.y())
                        tracked["pts"].append(QgsPointXY(mp.x(), mp.y()))
                    elif event.button() == Qt.MouseButton.RightButton:
                        # Right-click ends the capture either way.
                        tracked["pts"] = []
                        live_lbl.hide()

                elif event.type() == QEvent.Type.MouseMove:
                    pts = tracked["pts"]
                    if not pts:
                        return False
                    p = _evt_pos(event)
                    mp = _to_map(p.x(), p.y())
                    live_pts = pts + [QgsPointXY(mp.x(), mp.y())]
                    txt = _measure(live_pts)
                    if txt:
                        live_lbl.setText(txt)
                        live_lbl.adjustSize()
                        # Position label 16px right and above cursor
                        x = p.x() + 16
                        y = p.y() - live_lbl.height() - 4
                        if x + live_lbl.width() > viewport.width():
                            x = p.x() - live_lbl.width() - 8
                        if y < 0:
                            y = p.y() + 16
                        live_lbl.move(x, y)
                        live_lbl.show()
                        live_lbl.raise_()
                    else:
                        live_lbl.hide()

                elif event.type() == QEvent.Type.KeyPress:
                    # Mirror the capture tool: Backspace/Delete undoes the
                    # last vertex, Escape abandons the shape.
                    key = event.key()
                    if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                        if tracked["pts"]:
                            tracked["pts"].pop()
                        if not tracked["pts"]:
                            live_lbl.hide()
                    elif key == Qt.Key.Key_Escape:
                        tracked["pts"] = []
                        live_lbl.hide()

                elif event.type() == QEvent.Type.Leave:
                    live_lbl.hide()
            except Exception:
                pass
            return False

    f = _LiveFilter(canvas)
    f._live_lbl = live_lbl   # keep label alive
    # Mouse events arrive on the viewport; key events on the canvas itself.
    viewport.installEventFilter(f)
    canvas.installEventFilter(f)
    return f


def remove_live_measure(iface, live_filter):
    """Detach a filter returned by :func:`install_live_measure`."""
    if live_filter is None:
        return
    canvas = iface.mapCanvas()
    canvas.viewport().removeEventFilter(live_filter)
    canvas.removeEventFilter(live_filter)
    lbl = getattr(live_filter, "_live_lbl", None)
    if lbl is not None:
        lbl.hide()
        lbl.deleteLater()
