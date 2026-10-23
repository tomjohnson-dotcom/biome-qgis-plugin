# final_layout.py
# Builds the new final-map layout in code instead of from a .qpt. Same QGIS
# layout engine — just constructed directly so it stays maintainable and there
# is no fragile hand-authored XML to keep in sync.
#
# Four items only:
#   map      – native QgsLayoutItemMap, EPSG:3857, fills the left of the page
#   panel    – native QgsLayoutItemPicture pointing at the themed sidebar SVG
#   legend   – native QgsLayoutItemLegend, sits in the panel's transparent gap
#   scalebar – native QgsLayoutItemScaleBar, box style, bottom-left of the map
#
# The picture path is set later by the caller, after the map extent (and so the
# scale shown in the panel) is known.

from qgis.PyQt.QtCore import Qt, QRectF
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    QgsLayoutItemMap, QgsLayoutItemPicture, QgsLayoutItemLegend,
    QgsLayoutItemScaleBar, QgsLayoutSize, QgsLayoutPoint, QgsUnitTypes,
    QgsCoordinateReferenceSystem, QgsLayoutMeasurement, QgsLayoutItemPage,
)

from . import panel_renderer as pr

MM = QgsUnitTypes.LayoutUnit.LayoutMillimeters


def _force_landscape_page(layout):
    """Make page 0 a landscape A4 and return its actual (width, height) in mm.
    Tries several APIs because the orientation flag is unreliable across QGIS
    builds; the returned size is read back from the page so callers tile the
    *real* page and nothing can overflow."""
    pc = layout.pageCollection()
    page = pc.page(0)
    # Try the most explicit first, then named-size + orientation enum.
    try:
        page.setPageSize(QgsLayoutSize(pr.PAGE_W_MM, pr.PAGE_H_MM, MM))
    except Exception:
        pass
    try:
        sz = page.pageSize()
        if sz.width() < sz.height():
            page.setPageSize("A4", QgsLayoutItemPage.Orientation.Landscape)
    except Exception:
        pass
    try:
        pc.reflow()
    except Exception:
        pass
    sz = page.pageSize()
    w, h = sz.width(), sz.height()
    if w < h:                      # still portrait → use landscape numbers
        w, h = max(w, h), min(w, h)
    return page, w, h


def build(layout, project):
    """Construct the four layout items. Returns a dict of item references.
    Layers, extent, legend contents, scalebar segments and the panel picture
    are filled in by the caller."""

    # ── Page: force A4 landscape, then tile to the page's real size ───────
    page, PW, PH = _force_landscape_page(layout)
    map_w = PW - pr.PANEL_W_MM          # panel keeps its fixed width
    panel_x = map_w

    # ── Map ───────────────────────────────────────────────────────────────
    map_item = QgsLayoutItemMap(layout)
    map_item.setId("Map 1")
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeColor(QColor("#C9D1C9"))
    map_item.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, MM))
    map_item.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    layout.addLayoutItem(map_item)
    # attemptSetSceneRect sets position + size together, in layout (mm) units.
    map_item.attemptSetSceneRect(QRectF(0.0, 0.0, map_w, PH))

    # ── Panel picture (path set later) ────────────────────────────────────
    panel = QgsLayoutItemPicture(layout)
    panel.setId("Sidebar")
    panel.setFrameEnabled(False)
    panel.setBackgroundEnabled(False)
    # Stretch the SVG to exactly fill the panel rectangle.
    try:
        panel.setResizeMode(QgsLayoutItemPicture.ResizeMode.Stretch)
    except Exception:
        pass
    layout.addLayoutItem(panel)
    panel.attemptSetSceneRect(QRectF(panel_x, 0.0, pr.PANEL_W_MM, PH))

    # ── Legend (contents filled by caller) ────────────────────────────────
    legend = QgsLayoutItemLegend(layout)
    legend.setId("Legend")
    legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(False)
    try:
        legend.setLegendFilterByMapEnabled(True)
    except Exception:
        pass
    legend.setFrameEnabled(False)
    legend.setBackgroundEnabled(False)
    legend.setTitle("")
    try:
        legend.setSymbolWidth(7.0)
        legend.setSymbolHeight(4.5)
    except Exception:
        pass
    _style_legend_fonts(legend)
    layout.addLayoutItem(legend)
    lx, ly, lw, lh = pr.legend_rect_mm()
    legend.attemptMove(QgsLayoutPoint(lx, ly, MM))
    legend.attemptResize(QgsLayoutSize(lw, lh, MM))

    # ── Scale bar (box style, bottom-left of the map) ─────────────────────
    sb = QgsLayoutItemScaleBar(layout)
    sb.setId("Scale Bar")
    sb.setLinkedMap(map_item)
    sb.setStyle("Single Box")
    sb.setUnits(QgsUnitTypes.DistanceUnit.DistanceMeters)
    sb.setUnitLabel("m")
    sb.setFont(QFont("Inter", 7))
    sb.setFillColor(QColor("#1A2420"))
    sb.setLineColor(QColor("#1A2420"))
    sb.setBackgroundEnabled(False)     # hover directly over the map
    # White halo behind the numbers so they stay legible over aerial imagery
    # without a solid box. Falls back silently on builds without text format.
    try:
        from qgis.core import (QgsTextFormat, QgsTextBufferSettings,
                               QgsUnitTypes as _U)
        fmt = QgsTextFormat()
        fmt.setFont(QFont("Inter"))
        fmt.setSize(8)
        fmt.setSizeUnit(_U.RenderPoints)
        fmt.setColor(QColor("#15110A"))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.9)
        buf.setSizeUnit(_U.RenderMillimeters)
        buf.setColor(QColor(255, 255, 255, 235))
        fmt.setBuffer(buf)
        sb.setTextFormat(fmt)
    except Exception:
        pass
    try:
        sb.setBoxContentSpace(1.2)
    except Exception:
        pass
    # Fit-width segment mode: QGIS re-picks round segment values to keep the bar
    # within a target width as the map scale changes, so zooming in the designer
    # re-snaps the bar instead of letting it grow without bound.
    try:
        from qgis.core import QgsScaleBarSettings
        sb.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
        sb.setMinimumBarWidth(40.0)               # mm
        sb.setMaximumBarWidth(pr.PANEL_W_MM - 8.0)  # ~62 mm
    except Exception:
        pass
    layout.addLayoutItem(sb)
    sb.attemptMove(QgsLayoutPoint(6.0, PH - 18.0, MM))

    # ── Belt-and-braces: pin the frames to the real page via scene rects ──
    map_item.attemptSetSceneRect(QRectF(0.0, 0.0, map_w, PH))
    panel.attemptSetSceneRect(QRectF(panel_x, 0.0, pr.PANEL_W_MM, PH))

    return {"map": map_item, "panel": panel, "legend": legend, "scalebar": sb}


def _style_legend_fonts(legend):
    """Apply Inter fonts to legend item + label text. Group / sub-group labels
    (e.g. the rule-based "Retained" / "Newly planted" headings) are bold so they
    read as headers rather than plain entries."""
    try:
        from qgis.core import QgsLegendStyle
        for role, size, weight in (
            (QgsLegendStyle.Style.SymbolLabel, 8.5, QFont.Weight.Normal),
            (QgsLegendStyle.Style.Subgroup,    9.0, QFont.Weight.Bold),
            (QgsLegendStyle.Style.Group,       9.5, QFont.Weight.Bold),
        ):
            st = legend.style(role)
            f = QFont("Inter", int(size))
            f.setWeight(weight)
            st.setFont(f)
            legend.setStyle(role, st)
    except Exception:
        pass


def true_scale_denominator(map_item):
    """GROUND-scale denominator of *map_item*: the 1:X such that 1 mm of paper
    covers X mm of real ground, measured ellipsoidally across the extent's
    mid-line.

    map_item.scale() returns the PROJECTED-unit denominator. On a Web Mercator
    (EPSG:3857) map at UK latitudes projected metres are ~1.6x longer than the
    ground they cover (1/cos(lat)), so the native denominator overstates the
    printed scale by the same factor — a map whose true ground scale is 1:776
    reports 1:1250. Falls back to map_item.scale() if measurement fails (e.g.
    no extent yet)."""
    try:
        from qgis.core import QgsDistanceArea, QgsPointXY, QgsProject
        ext = map_item.extent()
        frame_w_mm = map_item.rect().width()     # layout units are mm
        if ext is None or ext.isEmpty() or not frame_w_mm:
            return map_item.scale()
        da = QgsDistanceArea()
        da.setSourceCrs(map_item.crs(), QgsProject.instance().transformContext())
        try:
            from .config import measurement_ellipsoid
            da.setEllipsoid(measurement_ellipsoid())
        except Exception:
            da.setEllipsoid("EPSG:7030")
        y = ext.center().y()
        ground_m = da.measureLine(QgsPointXY(ext.xMinimum(), y),
                                  QgsPointXY(ext.xMaximum(), y))
        if ground_m and ground_m > 0:
            return ground_m * 1000.0 / frame_w_mm
    except Exception:
        pass
    try:
        return map_item.scale()
    except Exception:
        return 0.0


def _bng_scale_denominator(map_item):
    """Ground denominator measured exactly the way the sidebar's "Scale @ A4"
    expression label measures it: the extent mid-line transformed to
    EPSG:27700 (true-scale in GB), planar length per frame mm. Kept separate
    from true_scale_denominator (ellipsoidal) so a fixed planning scale can
    converge on the SAME number the label prints — the two definitions agree
    within ~0.04%, but a location plan must read exactly "1:1,250"."""
    try:
        from qgis.core import (QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform, QgsGeometry,
                               QgsPointXY, QgsProject)
        ext = map_item.extent()
        frame_w_mm = map_item.rect().width()
        if ext is None or ext.isEmpty() or not frame_w_mm:
            return 0.0
        y = ext.center().y()
        line = QgsGeometry.fromPolylineXY([QgsPointXY(ext.xMinimum(), y),
                                           QgsPointXY(ext.xMaximum(), y)])
        line.transform(QgsCoordinateTransform(
            map_item.crs(), QgsCoordinateReferenceSystem("EPSG:27700"),
            QgsProject.instance()))
        return line.length() * 1000.0 / frame_w_mm
    except Exception:
        return 0.0


def set_true_scale(map_item, denom):
    """Zoom *map_item* (about its current centre) so its TRUE ground scale is
    1:*denom* — e.g. the 1:1250 / 1:2500 a planning location plan must be
    printed at.

    map_item.setScale(denom) alone is NOT enough: it sets the PROJECTED
    denominator, which on the EPSG:3857 map is ~1.6x the ground denominator
    at UK latitudes — the printed plan would really be ~1:776. So the
    projected scale is corrected by the current projected/ground ratio and
    refined until the ground denominator (measured exactly as the sidebar
    label measures it) equals *denom*. setScale emits extentChanged, so the
    scale bar autofit and the sidebar ratio label follow automatically."""
    try:
        denom = float(denom)
        if denom <= 0:
            return
        for _ in range(3):                       # k is ~constant → converges fast
            t = _bng_scale_denominator(map_item)
            p = map_item.scale()
            if t <= 0 or p <= 0:
                map_item.setScale(denom)
                continue
            if abs(t - denom) / denom < 1e-9:
                break
            map_item.setScale(p * (denom / t))
    except Exception:
        pass


def fit_scalebar(sb, map_item, target_mm=48.0, max_mm=None):
    """Size the box scale bar to exactly two segments spanning roughly
    *target_mm* of paper, with the segment snapped to a clean cartographic
    interval and the whole bar never exceeding *max_mm* (defaults to
    target_mm). Labels flip to km once a segment reaches 1 km, so a
    zoomed-out bar reads "2 km" rather than "2000 m". Pair with
    bind_scalebar_autofit() to keep this in sync while the user zooms the
    map in the layout designer.

    Sizing uses the TRUE ground denominator, not map_item.scale(): the native
    bar renderer draws each segment at its ellipsoidal ground width (QGIS
    measures the linked map's extent with the project ellipsoid), so choosing
    segment values from the projected 3857 denominator would overflow the
    width budget by ~1.6x at UK latitudes.
    """
    try:
        if max_mm is None:
            max_mm = target_mm
        scale = true_scale_denominator(map_item)   # 1:scale, ground-true
        if scale <= 0:
            return

        # Metres of ground per mm of paper at this scale.
        m_per_mm = scale / 1000.0
        target_total_m = target_mm * m_per_mm
        max_total_m    = max_mm * m_per_mm

        # Always exactly two segments: snap the segment to a cartographic
        # interval, stepping down until the whole bar fits the width budget.
        n_seg = 2
        seg_m = _nice(target_total_m / n_seg)
        while seg_m * n_seg > max_total_m * 1.02 and seg_m > 0:  # 2% tolerance
            seg_m = _nice_down(seg_m)
        if seg_m <= 0:
            return

        # Units follow the snapped segment value: km once a segment reaches
        # 1 km, metres below that.
        if seg_m >= 1000.0:
            units, unit_label, unit_m = QgsUnitTypes.DistanceUnit.DistanceKilometers, "km", 1000.0
        else:
            units, unit_label, unit_m = QgsUnitTypes.DistanceUnit.DistanceMeters, "m", 1.0

        sb.setUnits(units)
        sb.setUnitLabel(unit_label)
        try:
            # Fixed segment size — fit-width mode would re-pick its own
            # values at render time and ignore the unit flip computed here.
            from qgis.core import QgsScaleBarSettings
            sb.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFixed)
        except Exception:
            pass
        # Units-per-segment is expressed in the bar's display units.
        sb.setUnitsPerSegment(seg_m / unit_m)
        sb.setNumberOfSegments(n_seg)
        sb.setNumberOfSegmentsLeft(0)
        sb.update()
    except Exception:
        pass


def bind_scalebar_autofit(sb, map_item, target_mm=48.0, max_mm=None):
    """Re-fit the scale bar whenever the linked map's extent changes, so
    zooming in the layout designer re-snaps the two segments and switches the
    units between m and km. The Qt connection owns the closure, so it lives
    (and dies) with the map item."""
    def _refit():
        try:
            fit_scalebar(sb, map_item, target_mm=target_mm, max_mm=max_mm)
        except RuntimeError:
            pass        # layout was deleted; the connection dies with it
    try:
        map_item.extentChanged.connect(_refit)
    except Exception:
        pass


def _nice(raw):
    if raw <= 0:
        return 100.0
    import math
    mag = 10 ** math.floor(math.log10(raw))
    for c in (1, 2, 2.5, 5, 10):
        if c * mag >= raw:
            return float(c * mag)
    return float(10 * mag)


def _nice_down(raw):
    """Next clean interval strictly below *raw* (… 1, 2, 2.5, 5, 10 …).
    Candidates span the magnitude below as well as the current one, so
    stepping down from a power of ten lands on the next clean value
    (10 → 5) rather than collapsing a whole decade (10 → 1) — the latter
    used to shrink the bar to a 2 m stub at some zoom levels."""
    if raw <= 0:
        return 0.0
    import math
    mag = 10 ** math.floor(math.log10(raw))
    candidates = [c * m for m in (mag / 10.0, mag) for c in (1, 2, 2.5, 5)]
    below = [c for c in candidates if c < raw - 1e-9]
    if below:
        return float(max(below))
    return float(mag / 10.0)
