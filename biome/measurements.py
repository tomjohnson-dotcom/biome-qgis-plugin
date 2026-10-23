# measurements.py

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy, QInputDialog,
    QButtonGroup,
)
from qgis.PyQt.QtCore import Qt, QTimer, QSize
from qgis.core import (
    QgsProject, QgsDistanceArea, QgsUnitTypes,
    QgsCoordinateTransform,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
    QgsTextFormat, QgsTextBufferSettings,
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsWkbTypes,
    QgsFillSymbol, QgsSimpleFillSymbolLayer,
    QgsRuleBasedRenderer, QgsField,
    QgsMessageLog, Qgis,
    QgsLayerTreeLayer,
)
from qgis.gui import QgsHighlight
from qgis.PyQt.QtGui import QFont, QColor, QIcon


def _coerce_to_polygon(geom):
    """Safely coerce a geometry to MultiPolygon.

    QgsGeometry.coerceToType() returns a *list* of QgsGeometry objects,
    not a single geometry.  This helper merges the results back into one
    geometry so callers can use the normal .isNull() / .isEmpty() checks.
    Returns None if the result is empty.
    """
    parts = geom.coerceToType(QgsWkbTypes.Type.MultiPolygon)
    if not parts:
        return None
    result = parts[0]
    for part in parts[1:]:
        result = result.combine(part)
    return result if not result.isNull() and not result.isEmpty() else None


def invalid_habitat_geometries():
    """Return [{'layer', 'fid', 'sub', 'area_ha'}] for every habitat polygon whose
    geometry is invalid (self-intersections, holes outside the shell, sliver-
    flipped rings…). An invalid outline makes a parcel's area calculate
    incorrectly (sometimes zero, sometimes a wrong non-zero value), silently
    corrupting the metric and coverage stats — so callers flag these. 'area_ha'
    is the SIGNED ground area the parcel currently computes to (ellipsoidal, in
    hectares) — it goes NEGATIVE when a ring is inverted / a hole lies outside
    the shell, which callers surface as a plain 'reading a negative … ha' signal.
    Shared by the layer panel, the Summary/Mapping banners and the export guard."""
    ctx = QgsProject.instance().transformContext()
    ell = _ellipsoid()
    bad = []
    for lyr in QgsProject.instance().mapLayers().values():
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            continue
        if QgsWkbTypes.geometryType(lyr.wkbType()) != QgsWkbTypes.GeometryType.PolygonGeometry:
            continue
        if lyr.fields().indexOf("sub_habitat_type") < 0:
            continue
        da = QgsDistanceArea()
        try:
            da.setSourceCrs(lyr.crs(), ctx)
            da.setEllipsoid(ell)
        except Exception:
            pass
        for f in lyr.getFeatures():
            g = f.geometry()
            if g is None or g.isNull() or g.isEmpty():
                continue
            try:
                if g.isGeosValid():
                    continue
            except Exception:
                continue
            ti = f.fieldNameIndex("sub_habitat_type")
            sub = str(f["sub_habitat_type"] or "").strip() if ti >= 0 else ""
            # SIGNED area (do NOT abs) — measureArea returns a negative value for
            # an inverted/hole-outside-shell polygon, which is the tell we surface.
            try:
                area_ha = da.convertAreaMeasurement(
                    da.measureArea(g), QgsUnitTypes.AreaUnit.AreaHectares)
            except Exception:
                area_ha = None
            bad.append({"layer": lyr, "fid": f.id(), "sub": sub, "area_ha": area_ha})
    return bad


def clean_polygon_geometry(geom, da=None, min_part_m2=1.0):
    """Repair an invalid polygon: makeValid(), then drop sliver parts smaller
    than `min_part_m2` (and any stray non-polygon parts makeValid leaves behind),
    recombining the survivors. Returns (cleaned_geom, n_parts_dropped).

    Caller is expected to have already established the geometry is an INVALID
    polygon; valid geometry should be left untouched. Shared by the layer
    panel's one-click repair and the Feature Placer's place-time cleaning."""
    fixed = geom.makeValid()
    if fixed is None or fixed.isNull() or fixed.isEmpty():
        return geom, 0
    parts = [p for p in fixed.asGeometryCollection()
             if p.type() == QgsWkbTypes.GeometryType.PolygonGeometry and not p.isEmpty()]
    if not parts:
        return fixed, 0
    if da is None:
        da = QgsDistanceArea()
    keep = [p for p in parts if da.measureArea(p) >= min_part_m2] or parts
    merged = keep[0]
    for p in keep[1:]:
        merged = merged.combine(p)
    return merged, len(parts) - len(keep)


# Below 1 m² an apparent overlap is normally coordinate precision, snapping or
# clipping residue. It is below the metric's practical parcel-area precision
# and does not justify an alarm or a destructive geometry rewrite.
MIN_ACTIONABLE_OVERLAP_HA = 1e-4


def overlapping_habitat_polygons(min_ha=MIN_ACTIONABLE_OVERLAP_HA):
    """Return [{'layer', 'fid_a', 'fid_b', 'sub_a', 'sub_b', 'ha'}] for every pair
    of habitat polygons that overlap WITHIN the same layer. A single layer should
    tile the ground exactly once, so a same-layer overlap double-counts that
    habitat's area and BNG units — silently inflating the metric. Baseline-vs-
    Proposed overlaps are deliberately NOT flagged: the proposed layer legitimately
    re-occupies the baseline footprint. Intersections of 1 m² or less are
    ignored as harmless digitising/precision residue. Shared by the layer
    panel's data-quality warning and export QA."""
    proj = QgsProject.instance()
    ctx = proj.transformContext()
    ell = _ellipsoid()

    def _area_ha(geom, crs):
        ac = QgsDistanceArea()
        try:
            ac.setSourceCrs(crs, ctx)
            ac.setEllipsoid(ell)
        except Exception:
            pass
        return abs(ac.convertAreaMeasurement(
            ac.measureArea(geom), QgsUnitTypes.AreaUnit.AreaHectares))

    out = []
    for lyr in proj.mapLayers().values():
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            continue
        if QgsWkbTypes.geometryType(lyr.wkbType()) != QgsWkbTypes.GeometryType.PolygonGeometry:
            continue
        ti = lyr.fields().indexOf("sub_habitat_type")
        if ti < 0:
            continue
        feats = []
        for f in lyr.getFeatures():
            g = f.geometry()
            if g is None or g.isNull() or g.isEmpty():
                continue
            sub = str(f[ti] or "").strip()
            feats.append((f.id(), QgsGeometry(g), sub))
        for i in range(len(feats)):
            fid_a, geom_a, sub_a = feats[i]
            for j in range(i + 1, len(feats)):
                fid_b, geom_b, sub_b = feats[j]
                if not geom_a.boundingBoxIntersects(geom_b.boundingBox()):
                    continue
                try:
                    inter = geom_a.intersection(geom_b)
                except Exception:
                    continue
                if inter is None or inter.isEmpty():
                    continue
                ha = _area_ha(inter, lyr.crs())
                if ha > min_ha:
                    out.append({"layer": lyr, "fid_a": fid_a, "fid_b": fid_b,
                                "sub_a": sub_a, "sub_b": sub_b, "ha": ha})
    return out


def overlaps_in_layer(candidate_geom, target_layer,
                      min_ha=MIN_ACTIONABLE_OVERLAP_HA, exclude_fid=None):
    """Does ``candidate_geom`` overlap SAME-CLASS features already in
    ``target_layer``? Returns ``(overlaps: bool, overlap_ha: float, fids: list)``.

    Used before a "duplicate feature to the other layer" commit to warn about
    double-counted ground. Because every habitat layer holds exactly ONE geometry
    class, testing only against ``target_layer`` gives the same-class rule for
    free — an AREA is only ever compared with other AREAS, never with trees or
    hedges (which live in different layers entirely). Overlaps of 1 m² or less
    are ignored consistently with the live QA warning. By design only AREAS
    (polygons) can block: a hedge/watercourse LINE or a tree POINT legitimately
    shares its footprint with its retained twin, so those never block and return
    ``(False, 0.0, [])`` immediately.
    """
    if candidate_geom is None or candidate_geom.isNull() or candidate_geom.isEmpty():
        return (False, 0.0, [])
    if target_layer is None or not target_layer.isValid():
        return (False, 0.0, [])
    # Lines (hedge / water) and points (trees) never block — only areas.
    if QgsWkbTypes.geometryType(target_layer.wkbType()) != QgsWkbTypes.GeometryType.PolygonGeometry:
        return (False, 0.0, [])

    proj = QgsProject.instance()
    ctx = proj.transformContext()
    ell = _ellipsoid()

    def _area_ha(geom, crs):
        ac = QgsDistanceArea()
        try:
            ac.setSourceCrs(crs, ctx)
            ac.setEllipsoid(ell)
        except Exception:
            pass
        return abs(ac.convertAreaMeasurement(
            ac.measureArea(geom), QgsUnitTypes.AreaUnit.AreaHectares))

    cand = QgsGeometry(candidate_geom)
    try:
        if not cand.isGeosValid():
            cand = cand.makeValid()
    except Exception:
        pass
    cbb = cand.boundingBox()
    total_ha, fids = 0.0, []
    for f in target_layer.getFeatures():
        if exclude_fid is not None and f.id() == exclude_fid:
            continue
        g = f.geometry()
        if g is None or g.isNull() or g.isEmpty():
            continue
        if not g.boundingBoxIntersects(cbb):
            continue
        try:
            inter = cand.intersection(g)
        except Exception:
            continue
        if inter is None or inter.isEmpty():
            continue
        ha = _area_ha(inter, target_layer.crs())
        if ha > min_ha:
            total_ha += ha
            fids.append(f.id())
    return (total_ha > min_ha, total_ha, fids)


def area_covered_fraction(candidate_geom, target_layer):
    """Fraction (0..1) of ``candidate_geom``'s area already covered by features
    in ``target_layer``. Returns 0.0 for a non-polygon target or empty input.

    Used purely for the Manage-tab duplicate button: if an AREA's footprint is
    (near-)fully covered in the paired layer it already has a twin / is already
    re-mapped in the other phase, so the button is hidden. This is a READ-ONLY
    check — it stamps nothing and never touches the net classifier.
    """
    if candidate_geom is None or candidate_geom.isNull() or candidate_geom.isEmpty():
        return 0.0
    if (target_layer is None or not target_layer.isValid()
            or QgsWkbTypes.geometryType(target_layer.wkbType()) != QgsWkbTypes.GeometryType.PolygonGeometry):
        return 0.0
    _ov, ov_ha, _fids = overlaps_in_layer(candidate_geom, target_layer)
    if ov_ha <= 0.0:
        return 0.0
    proj = QgsProject.instance()
    ac = QgsDistanceArea()
    try:
        ac.setSourceCrs(target_layer.crs(), proj.transformContext())
        ac.setEllipsoid(_ellipsoid())
    except Exception:
        pass
    own_ha = abs(ac.convertAreaMeasurement(
        ac.measureArea(candidate_geom), QgsUnitTypes.AreaUnit.AreaHectares))
    if own_ha <= 0.0:
        return 0.0
    return min(1.0, ov_ha / own_ha)


from qgis.PyQt.QtCore import QVariant
from .layer_manager import TREE_SIZE_AREAS, tree_size_from_sub_type
from .habitat_data import DISTINCTIVENESS_SCORES
from .config import measurement_ellipsoid as _ellipsoid
from . import theme

# ── De minimis thresholds ─────────────────────────────────────────────────────
DEMINIMIS_AREA_THRESHOLD_M2  = 25.0
DEMINIMIS_LINEAR_THRESHOLD_M =  5.0
DEMINIMIS_AMBER_MARGIN       =  5.0

ZERO_VALUE_SUBTYPES = {
    "Developed land; sealed surface",
    "Watercourse footprint",
    "Built linear features",
    "Artificial unvegetated, unsealed surface",
    "Unvegetated garden",
    "Cereal crops",
    "Cereal crops other",
    "Winter stubble",
}

AREA_LAYERS = [
    "Baseline - Area Habitats",
    "Proposed - Area Habitats",
]
LINE_LAYERS = [
    "Baseline - Watercourse Habitat",
    "Proposed - Watercourse Habitat",
    "Baseline - Hedgerow Units",
    "Proposed - Hedgerow Units",
]
TREE_LAYERS = [
    "Baseline - Individual Trees",
    "Proposed - Individual Trees",
]

LOSS_LAYER_NAME         = "⚠ Habitat Loss Areas"
MEASURE_POLY_LAYER_NAME = "📏 Measure Sketches (Polygons)"
MEASURE_LINE_LAYER_NAME = "📏 Measure Sketches (Lines)"
WATCHED_LAYERS  = AREA_LAYERS + LINE_LAYERS + TREE_LAYERS


class _ElidingLabel(QLabel):
    """QLabel that truncates its text with an ellipsis to fit the width the
    layout gives it, instead of forcing its row — and the whole results panel —
    wider than the dock. Keeps the right-hand value/number column on screen when
    the Measurements tab is narrowed; the full text stays available on hover.

    Its horizontal size policy is Ignored so the layout never reserves the full
    (non-wrapping) text width for it — that reserved width was what pushed the
    numbers off the right edge on a narrow panel."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full = text or ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(self._full.strip())

    def setText(self, text):
        self._full = text or ""
        self.setToolTip(self._full.strip())
        self._relabel()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relabel()

    def _relabel(self):
        from qgis.PyQt.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))


class MeasurementsWidget(QWidget):
    """
    Measurements screen — designed to sit on the main panel stack,
    opening exactly like the Manage screen.  Auto-refreshes on layer commits.
    Three toggle buttons control map labels and the loss layer.
    """

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        self._baseline_labels_on = False
        self._proposed_labels_on = False
        self._loss_layer_on      = False
        self._measure_sketches   = []   # list of dicts: {name, fid, layer_name, geom_type, visible}
        self._live_measure_filter = None  # canvas event filter during drawing
        self._area_unit = self._load_area_unit()  # quick-measure area unit: auto|m2|ha

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._refresh)

        self._build_ui()
        self._connect_layer_signals()

        QgsProject.instance().layersAdded.connect(self._on_layers_added)
        QgsProject.instance().layersRemoved.connect(self._on_layers_removed)

        # Rebuild sketch list from any existing measure layers (survives re-open)
        self._rebuild_sketches_from_layers()

        # Initial populate — short delay so the stack has finished switching
        QTimer.singleShot(100, self._refresh)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(6)
        outer.setContentsMargins(12, 10, 12, 10)
        self.setStyleSheet("background:%s;" % theme.SURFACE)

        # Toggle buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._bl_btn   = self._make_toggle_btn("Baseline Labels", theme.ACCENT, "tag")
        self._prop_btn = self._make_toggle_btn("Proposed Labels", theme.INFO,   "tag")
        self._loss_btn = self._make_toggle_btn("Loss Layer",      theme.BAD,    "status-alert")
        self._bl_btn.clicked.connect(lambda _: self._toggle_baseline_labels())
        self._prop_btn.clicked.connect(lambda _: self._toggle_proposed_labels())
        self._loss_btn.clicked.connect(lambda _: self._toggle_loss_layer())
        btn_row.addWidget(self._bl_btn)
        btn_row.addWidget(self._prop_btn)
        btn_row.addWidget(self._loss_btn)
        outer.addLayout(btn_row)

        # ── Quick Measure section (heading + area-unit toggle) ────────
        qm_head = QWidget()
        qm_head.setStyleSheet("background:transparent;")
        qmh = QHBoxLayout(qm_head)
        qmh.setContentsMargins(2, 6, 2, 2)
        qmh.setSpacing(6)
        _qm_ic = QLabel()
        _qm_ic.setPixmap(self._pix("ruler-measure", theme.MUTED, 14))
        _qm_ic.setStyleSheet("background:transparent; border:none;")
        qmh.addWidget(_qm_ic, 0)
        _qm_lbl = QLabel("Quick Measure")
        _qm_lbl.setStyleSheet(
            "background:transparent; border:none; color:%s;"
            " font-family:%s; font-size:11px; font-weight:600;"
            % (theme.MUTED, theme.FONT_SEMIBOLD))
        qmh.addWidget(_qm_lbl, 1)
        qmh.addWidget(self._build_unit_toggle(), 0)
        outer.addWidget(qm_head)

        draw_row = QHBoxLayout()
        draw_row.setSpacing(6)
        self._draw_poly_btn     = self._make_draw_btn("Polygon",  "polygon")
        self._draw_freehand_btn = self._make_draw_btn("Freehand", "pencil")
        self._draw_line_btn     = self._make_draw_btn("Line",     "line")
        self._draw_poly_btn.clicked.connect(self._start_draw_polygon)
        self._draw_freehand_btn.clicked.connect(self._start_draw_freehand)
        self._draw_line_btn.clicked.connect(self._start_draw_line)
        draw_row.addWidget(self._draw_poly_btn)
        draw_row.addWidget(self._draw_freehand_btn)
        draw_row.addWidget(self._draw_line_btn)
        outer.addLayout(draw_row)

        # Container for sketch rows — rebuilt by _refresh_measure_list
        self._sketch_list_widget = QWidget()
        self._sketch_list_widget.setStyleSheet("background:transparent;")
        self._sketch_list_layout = QVBoxLayout(self._sketch_list_widget)
        self._sketch_list_layout.setContentsMargins(0, 2, 0, 2)
        self._sketch_list_layout.setSpacing(3)
        outer.addWidget(self._sketch_list_widget)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet("background:%s; border:none; margin:4px 0;" % theme.BORDER)
        outer.addWidget(divider)

        # Scrollable results area
        self.results_widget = QWidget()
        self.results_widget.setStyleSheet("background:%s;" % theme.SURFACE)
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setContentsMargins(0, 4, 0, 4)
        self.results_layout.setSpacing(3)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.results_widget)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:%s; }" % theme.SURFACE)
        outer.addWidget(scroll, 1)

    # ── Icon / header helpers ───────────────────────────────────────────────────

    def _two_state_icon(self, name, off_color, on_color, size=15):
        """QIcon whose Off pixmap is tinted off_color and On pixmap on_color,
        so a checkable button swaps icon colour with its checked state."""
        ic = QIcon()
        ic.addPixmap(theme.icon(name, off_color, size).pixmap(size, size), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(theme.icon(name, on_color,  size).pixmap(size, size), QIcon.Mode.Normal, QIcon.State.On)
        return ic

    def _pix(self, name, color, size=14):
        """Single tinted pixmap for placing an icon inside a QLabel."""
        return theme.icon(name, color, size).pixmap(size, size)

    def _section_label(self, text, icon_name):
        """Quiet section heading: muted tinted icon + small semibold caption."""
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 6, 2, 2)
        h.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(self._pix(icon_name, theme.MUTED, 14))
        ic.setStyleSheet("background:transparent; border:none;")
        h.addWidget(ic, 0)
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "background:transparent; border:none; color:%s;"
            " font-family:%s; font-size:11px; font-weight:600;"
            % (theme.MUTED, theme.FONT_SEMIBOLD))
        h.addWidget(lbl, 1)
        return w

    def _make_draw_btn(self, label, icon_name):
        btn = QPushButton("  " + label)
        btn.setFixedHeight(30)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setIcon(theme.icon(icon_name, theme.ACCENT_DARK, 15))
        btn.setIconSize(QSize(15, 15))
        btn.setStyleSheet(
            "QPushButton { background:%s; color:%s; border:1px solid %s;"
            " border-radius:6px; font-family:%s; font-size:11px; font-weight:600; }"
            "QPushButton:hover { background:#D8ECDE; border-color:%s; }"
            % (theme.ACCENT_SOFT, theme.ACCENT_DARK, theme.BORDER,
               theme.FONT_SEMIBOLD, theme.BORDER_STRONG))
        return btn

    def _make_toggle_btn(self, label, color, icon_name):
        btn = QPushButton("  " + label)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setFixedHeight(34)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setIcon(self._two_state_icon(icon_name, color, "#FFFFFF", 15))
        btn.setIconSize(QSize(15, 15))
        btn.setStyleSheet(
            "QPushButton { background:%s; color:%s; border:1px solid %s;"
            " border-radius:6px; font-family:%s; font-size:11px; font-weight:600; }"
            "QPushButton:checked { background:%s; color:white; border-color:%s; }"
            "QPushButton:hover:!checked { background:%s; }"
            % (theme.CARD, theme.MUTED, color, theme.FONT_SEMIBOLD,
               color, color, theme.SUNKEN))
        return btn

    def _build_unit_toggle(self):
        """Segmented Auto / m² / ha control for the quick-measure area display.
        Lines are always shown in metres, so only area sketches are affected."""
        wrap = QWidget()
        wrap.setObjectName("measUnitToggle")
        wrap.setStyleSheet(
            "QWidget#measUnitToggle { background:%s; border:1px solid %s;"
            " border-radius:6px; }" % (theme.CARD, theme.BORDER))
        h = QHBoxLayout(wrap)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(2)
        self._unit_group = QButtonGroup(self)
        self._unit_group.setExclusive(True)
        self._unit_btns = {}
        for key, label in [("auto", "Auto"), ("m2", "m²"), ("ha", "ha")]:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(20)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(key == self._area_unit)
            b.setStyleSheet(
                "QPushButton { background:transparent; color:%s; border:none;"
                " border-radius:4px; padding:1px 9px; font-family:%s;"
                " font-size:10px; font-weight:600; }"
                "QPushButton:checked { background:%s; color:white; }"
                "QPushButton:hover:!checked { background:%s; }"
                % (theme.MUTED, theme.FONT_SEMIBOLD, theme.ACCENT, theme.SUNKEN))
            b.clicked.connect(lambda _=False, k=key: self._set_area_unit(k))
            self._unit_group.addButton(b)
            self._unit_btns[key] = b
            h.addWidget(b)
        return wrap

    # ── Signal wiring ─────────────────────────────────────────────────────────

    def _connect_layer_signals(self):
        for name in WATCHED_LAYERS:
            for layer in QgsProject.instance().mapLayersByName(name):
                self._wire_layer(layer)

    def _wire_layer(self, layer):
        for sig in ("committedFeaturesAdded", "committedFeaturesRemoved",
                    "committedGeometriesChanges", "committedAttributeValuesChanges"):
            try:
                getattr(layer, sig).connect(self._schedule_refresh)
            except Exception:
                pass

    def _on_layers_added(self, layers):
        for layer in layers:
            if layer.name() in WATCHED_LAYERS:
                self._wire_layer(layer)
                self._schedule_refresh()

    def _on_layers_removed(self, _):
        self._schedule_refresh()

    def _schedule_refresh(self, *_):
        self._refresh_timer.start()

    # ── Toggles ───────────────────────────────────────────────────────────────

    def _toggle_baseline_labels(self):
        on = self._bl_btn.isChecked()
        self._baseline_labels_on = on
        area  = ["Baseline - Area Habitats"]
        lines = ["Baseline - Hedgerow Units", "Baseline - Watercourse Habitat"]
        trees = ["Baseline - Individual Trees"]
        if on:
            for n in area:
                for l in QgsProject.instance().mapLayersByName(n): self._apply_area_labels(l)
            for n in lines:
                for l in QgsProject.instance().mapLayersByName(n): self._apply_line_labels(l)
            for n in trees:
                for l in QgsProject.instance().mapLayersByName(n): self._apply_tree_labels(l)
        else:
            for n in area + lines + trees:
                for l in QgsProject.instance().mapLayersByName(n):
                    l.setLabelsEnabled(False); l.triggerRepaint()
        self.iface.mapCanvas().refresh()

    def _toggle_proposed_labels(self):
        on = self._prop_btn.isChecked()
        self._proposed_labels_on = on
        area  = ["Proposed - Area Habitats"]
        lines = ["Proposed - Hedgerow Units", "Proposed - Watercourse Habitat"]
        trees = ["Proposed - Individual Trees"]
        if on:
            for n in area:
                for l in QgsProject.instance().mapLayersByName(n): self._apply_area_labels(l)
            for n in lines:
                for l in QgsProject.instance().mapLayersByName(n): self._apply_line_labels(l)
            for n in trees:
                for l in QgsProject.instance().mapLayersByName(n): self._apply_tree_labels(l)
        else:
            for n in area + lines + trees:
                for l in QgsProject.instance().mapLayersByName(n):
                    l.setLabelsEnabled(False); l.triggerRepaint()
        self.iface.mapCanvas().refresh()

    def _toggle_loss_layer(self):
        self._loss_layer_on = self._loss_btn.isChecked()
        if self._loss_layer_on:
            self._draw_loss_layer()
        else:
            self._remove_loss_layer()

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh(self):
        try:
            self._do_refresh()
        except Exception as e:
            QgsMessageLog.logMessage(f"MeasurementsWidget error: {e}", "BNG Plugin", Qgis.MessageLevel.Critical)
            self._clear_results()
            err = QLabel(f"Error: {e}")
            err.setStyleSheet("color:%s; padding:8px; font-size:11px;" % theme.BAD)
            err.setWordWrap(True)
            self.results_layout.addWidget(err)

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _do_refresh(self):
        QgsMessageLog.logMessage("MeasurementsWidget: refresh", "BNG Plugin", Qgis.MessageLevel.Info)
        self._clear_results()
        any_data = False

        for layer_name in AREA_LAYERS:
            layers = QgsProject.instance().mapLayersByName(layer_name)
            if not layers or layers[0].featureCount() == 0:
                continue
            layer = layers[0]
            da    = self._make_da(layer)
            any_data = True
            color = theme.ACCENT if "Baseline" in layer_name else theme.INFO
            self._add_header(layer_name, color, "polygon")
            total = 0.0
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull() or geom.isEmpty():
                    continue
                raw_m2 = da.measureArea(geom)
                if raw_m2 < 0.1:   # skip sliver artefacts
                    continue
                val   = raw_m2 / 10_000
                total += val
                sub = self._get_sub(feat)
                self._add_row(f"  {sub}".rstrip(), f"{val:,.3f} ha")
            self._add_row("  Total", f"{total:,.3f} ha", bold=True, highlight=True)
            self._add_divider()

        for layer_name in LINE_LAYERS:
            layers = QgsProject.instance().mapLayersByName(layer_name)
            if not layers or layers[0].featureCount() == 0:
                continue
            layer = layers[0]
            da    = self._make_da(layer)
            any_data = True
            color = theme.ACCENT if "Baseline" in layer_name else theme.INFO
            icon_name = "droplet" if "Watercourse" in layer_name else "line"
            self._add_header(layer_name, color, icon_name)
            total = 0.0
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull() or geom.isEmpty():
                    continue
                val   = da.measureLength(geom)
                total += val
                sub = self._get_sub(feat)
                self._add_row(f"  {sub}".rstrip(), f"{val:,.1f} m")
            self._add_row("  Total", f"{total:,.1f} m", bold=True, highlight=True)
            self._add_divider()

        for layer_name in TREE_LAYERS:
            layers = QgsProject.instance().mapLayersByName(layer_name)
            if not layers or layers[0].featureCount() == 0:
                continue
            layer = layers[0]
            any_data = True
            color = theme.ACCENT if "Baseline" in layer_name else theme.INFO
            self._add_header(layer_name, color, "tree")
            total_ha = 0.0
            for feat in layer.getFeatures():
                sub      = self._get_sub(feat)
                area_ha  = TREE_SIZE_AREAS.get(tree_size_from_sub_type(sub), 0.0)
                total_ha += area_ha
                self._add_row(f"  {sub}", f"{area_ha:.4f} ha")
            self._add_row("  Total", f"{total_ha:.4f} ha", bold=True, highlight=True)
            self._add_divider()

        if not any_data:
            lbl = QLabel("No habitat features found.")
            lbl.setStyleSheet("color:%s; padding:10px; font-size:12px;" % theme.FAINT)
            self.results_layout.addWidget(lbl)

        self._add_deminimis_check()
        self.results_layout.addStretch()

        if self._baseline_labels_on:
            self._toggle_baseline_labels()
        if self._proposed_labels_on:
            self._toggle_proposed_labels()
        if self._loss_layer_on:
            self._draw_loss_layer()

    # ── Label helpers ─────────────────────────────────────────────────────────

    def _apply_area_labels(self, layer):
        pal = QgsPalLayerSettings()
        pal.fieldName = (
            "if($area >= 10000, "
            "to_string(round($area / 10000, 2)) || ' ha', "
            "to_string(round($area, 1)) || ' m²')"
        )
        pal.isExpression = True
        try:
            pal.placement = QgsPalLayerSettings.Placement.Horizontal
        except AttributeError:
            pal.placement = QgsPalLayerSettings.Placement.Horizontal
        self._fmt_label(pal)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def _apply_line_labels(self, layer):
        pal = QgsPalLayerSettings()
        pal.fieldName    = "to_string(round($length, 1)) || ' m'"
        pal.isExpression = True
        try:
            pal.placement = QgsPalLayerSettings.Placement.Curved
        except AttributeError:
            pal.placement = QgsPalLayerSettings.Placement.Curved
        self._fmt_label(pal)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def _apply_tree_labels(self, layer):
        pal = QgsPalLayerSettings()
        sub   = '"sub_habitat_type"'
        cases = " + ".join([
            f"if(lower({sub}) like '%{k.lower()}%', '{k}\\n{v} ha', '')"
            for k, v in TREE_SIZE_AREAS.items()
        ])
        pal.fieldName    = cases
        pal.isExpression = True
        try:
            pal.placement = QgsPalLayerSettings.Placement.OrderedPositionsAroundPoint
        except AttributeError:
            pal.placement = QgsPalLayerSettings.Placement.OrderedPositionsAroundPoint
        self._fmt_label(pal)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def _fmt_label(self, pal, size=8):
        fmt  = QgsTextFormat()
        font = QFont("Inter", size)
        font.setBold(True)
        fmt.setFont(font)
        fmt.setSize(size)
        fmt.setColor(QColor("black"))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.0)
        buf.setColor(QColor("white"))
        fmt.setBuffer(buf)
        pal.setFormat(fmt)
        pal.enabled = True

    # ── Area-unit display ─────────────────────────────────────────────────────

    def _load_area_unit(self):
        """Remembered quick-measure area unit: 'auto' | 'm2' | 'ha'."""
        try:
            from qgis.core import QgsSettings
            u = QgsSettings().value("biome/measure_area_unit", "auto")
            return u if u in ("auto", "m2", "ha") else "auto"
        except Exception:
            return "auto"

    def _save_area_unit(self, unit):
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue("biome/measure_area_unit", unit)
        except Exception:
            pass

    def _fmt_area(self, area_m2):
        """Format an area (m²) per the current display unit."""
        if self._area_unit == "ha":
            return f"{area_m2 / 10_000:,.4f} ha"
        if self._area_unit == "m2":
            return f"{area_m2:,.1f} m²"
        # auto — hectares once the parcel is big enough to read cleanly
        if area_m2 >= 10_000:
            return f"{area_m2 / 10_000:,.4f} ha"
        return f"{area_m2:,.1f} m²"

    def _area_label_expr(self):
        """On-map polygon label expression matching the current display unit."""
        if self._area_unit == "ha":
            return "to_string(round($area / 10000, 4)) || ' ha'"
        if self._area_unit == "m2":
            return "to_string(round($area, 1)) || ' m²'"
        return ("if($area >= 10000, "
                "to_string(round($area / 10000, 4)) || ' ha', "
                "to_string(round($area, 1)) || ' m²')")

    def _sketch_measurement(self, sketch):
        """Derive a sketch's display string from its stored raw measurement."""
        if sketch.get("geom_type") == "polygon" and sketch.get("area_m2") is not None:
            return self._fmt_area(sketch["area_m2"])
        if sketch.get("length_m") is not None:
            return f"{sketch['length_m']:,.1f} m"
        return sketch.get("measurement", "")

    def _set_area_unit(self, unit):
        """Switch the quick-measure area unit and re-render list + map labels."""
        if unit not in ("auto", "m2", "ha"):
            return
        self._area_unit = unit
        self._save_area_unit(unit)
        for sk in self._measure_sketches:
            sk["measurement"] = self._sketch_measurement(sk)
        self._refresh_measure_list()
        poly_lyrs = QgsProject.instance().mapLayersByName(MEASURE_POLY_LAYER_NAME)
        if poly_lyrs:
            self._apply_measure_labels(poly_lyrs[0], "polygon")
        self.iface.mapCanvas().refresh()

    # ── Measure sketches ──────────────────────────────────────────────────────

    def _rebuild_sketches_from_layers(self):
        """Reconstruct _measure_sketches from any existing measure layers.
        Called on init so the list survives navigating away and back.
        """
        self._measure_sketches = []
        da = QgsDistanceArea()
        for layer_name, geom_type in [
            (MEASURE_POLY_LAYER_NAME, "polygon"),
            (MEASURE_LINE_LAYER_NAME, "line"),
        ]:
            lyrs = QgsProject.instance().mapLayersByName(layer_name)
            if not lyrs:
                continue
            lyr = lyrs[0]
            da.setSourceCrs(lyr.crs(), QgsProject.instance().transformContext())
            da.setEllipsoid(_ellipsoid())
            for feat in lyr.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull():
                    continue
                area_m2 = length_m = None
                if geom_type == "polygon":
                    area_m2 = da.measureArea(geom)
                else:
                    length_m = da.measureLength(geom)
                # Use name field if populated, otherwise generate one
                name_val = ""
                idx = feat.fieldNameIndex("name")
                if idx >= 0 and feat[idx]:
                    name_val = str(feat[idx])
                if not name_val:
                    name_val = f"Quick {len(self._measure_sketches) + 1}"
                # Read visibility from the subset string. Memory layers have no
                # 'fid' column, so we filter on $id — a 'fid' filter would hide
                # every feature, not just the one toggled off.
                current_subset = lyr.subsetString() or ""
                import re
                excluded = set(int(m) for m in re.findall(r"\$id\s*!=\s*(\d+)", current_subset))
                visible = feat.id() not in excluded
                sketch = {
                    "name":        name_val,
                    "fid":         feat.id(),
                    "layer_name":  layer_name,
                    "geom_type":   geom_type,
                    "visible":     visible,
                    "area_m2":     area_m2,
                    "length_m":    length_m,
                }
                sketch["measurement"] = self._sketch_measurement(sketch)
                self._measure_sketches.append(sketch)
                # Re-apply labels in case they were lost
                self._apply_measure_labels(lyr, geom_type)
        self._refresh_measure_list()

    def _get_or_create_measure_layer(self, geom_type):
        """Return (or create) the measure sketch memory layer for poly or line."""
        name = MEASURE_POLY_LAYER_NAME if geom_type == "polygon" else MEASURE_LINE_LAYER_NAME
        existing = QgsProject.instance().mapLayersByName(name)
        if existing:
            return existing[0]
        crs = "EPSG:27700"
        # Try to inherit CRS from a baseline layer
        for lname in ["Baseline - Area Habitats", "Baseline - Hedgerow Units"]:
            lyrs = QgsProject.instance().mapLayersByName(lname)
            if lyrs:
                crs = lyrs[0].crs().authid() or crs
                break
        qtype = "Polygon" if geom_type == "polygon" else "LineString"
        lyr = QgsVectorLayer(f"{qtype}?crs={crs}", name, "memory")
        provider = lyr.dataProvider()
        provider.addAttributes([QgsField("name", QVariant.String)])
        lyr.updateFields()
        lyr.setCustomProperty("skipMemoryLayersCheck", 1)
        # Measure sketches are user drawings, so they persist: tagged for
        # migration into the project GeoPackage on save, like habitat layers.
        lyr.setCustomProperty("biome_scratch", 1)
        lyr.setCustomProperty(
            "biome_gpkg_table",
            "measure_polygons" if geom_type == "polygon" else "measure_lines")
        # Style: semi-transparent teal fill for polygon, teal line for line
        if geom_type == "polygon":
            sym = QgsFillSymbol.createSimple({
                "color": "0,150,136,80",
                "outline_color": "#00796b",
                "outline_width": "0.8",
            })
            lyr.renderer().setSymbol(sym)
        else:
            from qgis.core import QgsLineSymbol
            sym = QgsLineSymbol.createSimple({
                "color": "#00796b",
                "width": "1.0",
                "line_style": "dash",
            })
            lyr.renderer().setSymbol(sym)
        QgsProject.instance().addMapLayer(lyr, False)
        QgsProject.instance().layerTreeRoot().insertLayer(0, lyr)
        return lyr

    def _install_live_measure(self, geom_type):
        """Show a live measurement label next to the cursor while digitising.
        Shared implementation in live_measure.py (also used by the habitat
        form's hedgerow/watercourse draw)."""
        from .live_measure import install_live_measure
        self._remove_live_measure()
        self._live_measure_filter = install_live_measure(
            self.iface, geom_type, fmt_area=self._fmt_area)

    def _remove_live_measure(self):
        """Remove the live measure event filter and floating label."""
        if self._live_measure_filter is not None:
            from .live_measure import remove_live_measure
            remove_live_measure(self.iface, self._live_measure_filter)
            self._live_measure_filter = None

    def _start_draw_polygon(self):
        lyr = self._get_or_create_measure_layer("polygon")
        self._prepare_measure_layer(lyr)

        def on_added(fid):
            try:
                lyr.featureAdded.disconnect(on_added)
            except Exception:
                pass
            self._remove_live_measure()
            QTimer.singleShot(0, lambda: self._commit_measure(lyr, fid, "polygon"))

        lyr.featureAdded.connect(on_added)
        self.iface.actionAddFeature().trigger()
        self._install_live_measure("polygon")
        self.iface.messageBar().pushMessage(
            "Quick Measure", "Draw polygon — right-click to finish.", level=Qgis.MessageLevel.Info, duration=5)

    def _start_draw_freehand(self):
        lyr = self._get_or_create_measure_layer("polygon")
        self._prepare_measure_layer(lyr)

        def on_added(fid):
            try:
                lyr.featureAdded.disconnect(on_added)
            except Exception:
                pass
            QTimer.singleShot(0, lambda: self._commit_measure(lyr, fid, "polygon"))

        lyr.featureAdded.connect(on_added)
        # Use QGIS freehand digitising action
        self.iface.actionAddFeature().trigger()
        try:
            self.iface.actionToggleEditing()  # ensure editing
            from qgis.gui import QgsMapToolDigitizeFeature
            canvas = self.iface.mapCanvas()
            tool = canvas.mapTool()
            if hasattr(tool, 'setDrawingMode'):
                from qgis.gui import QgsMapToolCapture
                tool.setDrawingMode(QgsMapToolCapture.CaptureMode.CaptureLine)
        except Exception:
            pass
        # Trigger QGIS built-in freehand tool via action
        try:
            self.iface.actionDigitizeFreehand().trigger()
        except Exception:
            # Fallback: standard add feature with a hint
            self.iface.actionAddFeature().trigger()
        self.iface.messageBar().pushMessage(
            "Quick Measure", "Freehand — hold and drag to draw, release to finish.", level=Qgis.MessageLevel.Info, duration=6)

    def _start_draw_line(self):
        lyr = self._get_or_create_measure_layer("line")
        self._prepare_measure_layer(lyr)

        def on_added(fid):
            try:
                lyr.featureAdded.disconnect(on_added)
            except Exception:
                pass
            self._remove_live_measure()
            QTimer.singleShot(0, lambda: self._commit_measure(lyr, fid, "line"))

        lyr.featureAdded.connect(on_added)
        self.iface.actionAddFeature().trigger()
        self._install_live_measure("line")
        self.iface.messageBar().pushMessage(
            "Quick Measure", "Draw line — right-click to finish.", level=Qgis.MessageLevel.Info, duration=5)

    def _prepare_measure_layer(self, lyr):
        self.iface.setActiveLayer(lyr)
        from qgis.core import QgsEditFormConfig
        cfg = lyr.editFormConfig()
        cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOn)
        lyr.setEditFormConfig(cfg)
        if not lyr.isEditable():
            lyr.startEditing()

    def _commit_measure(self, lyr, fid, geom_type):
        """Commit the drawn feature, measure it, label it on the map, add a panel row."""
        if not lyr.isEditable():
            return
        lyr.commitChanges()
        self.iface.actionPan().trigger()

        # Find the newly committed feature (highest fid)
        committed_fid = None
        for feat in lyr.getFeatures():
            if committed_fid is None or feat.id() > committed_fid:
                committed_fid = feat.id()
        if committed_fid is None:
            return

        feat = lyr.getFeature(committed_fid)
        geom = feat.geometry()
        da   = QgsDistanceArea()
        da.setSourceCrs(lyr.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(_ellipsoid())

        area_m2 = length_m = None
        if geom_type == "polygon":
            area_m2 = da.measureArea(geom)
        else:
            length_m = da.measureLength(geom)

        n = len(self._measure_sketches) + 1
        # Store the name on the feature itself so it survives project reload.
        name_idx = lyr.fields().indexOf("name")
        if name_idx >= 0:
            lyr.dataProvider().changeAttributeValues(
                {committed_fid: {name_idx: f"Quick {n}"}})
        sketch = {
            "name":        f"Quick {n}",
            "fid":         committed_fid,
            "layer_name":  lyr.name(),
            "geom_type":   geom_type,
            "visible":     True,
            "area_m2":     area_m2,
            "length_m":    length_m,
        }
        sketch["measurement"] = self._sketch_measurement(sketch)
        self._measure_sketches.append(sketch)

        # Apply on-map measurement label to the layer
        self._apply_measure_labels(lyr, geom_type)
        self._refresh_measure_list()

    def _apply_measure_labels(self, lyr, geom_type):
        """Set on-map area/length labels on the quick measure layer."""
        pal = QgsPalLayerSettings()
        if geom_type == "polygon":
            pal.fieldName = self._area_label_expr()
            try:
                pal.placement = QgsPalLayerSettings.Placement.Horizontal
            except AttributeError:
                pal.placement = QgsPalLayerSettings.Placement.Horizontal
        else:
            pal.fieldName = "to_string(round($length, 1)) || ' m'"
            try:
                pal.placement = QgsPalLayerSettings.Placement.Curved
            except AttributeError:
                pal.placement = QgsPalLayerSettings.Placement.Curved
        pal.isExpression = True
        self._fmt_label(pal, size=9)
        lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        lyr.setLabelsEnabled(True)
        lyr.triggerRepaint()

    def _refresh_measure_list(self):
        """Rebuild the sketch list UI."""
        # Clear existing rows
        while self._sketch_list_layout.count():
            item = self._sketch_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, sketch in enumerate(self._measure_sketches):
            row = QWidget()
            row.setObjectName("measSketchRow")
            row.setStyleSheet(
                "QWidget#measSketchRow { background:%s; border:1px solid %s;"
                " border-radius:6px; }" % (theme.CARD, theme.BORDER))
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 6, 4)
            rl.setSpacing(4)

            meas = sketch.get("measurement", "")
            lbl = QLabel(
                f"<b>{sketch['name']}</b>&nbsp;&nbsp;"
                f"<span style='color:{theme.MUTED};'>{meas}</span>")
            lbl.setStyleSheet(
                "font-size:11px; color:%s; border:none; background:transparent;"
                " font-family:%s;" % (theme.INK, theme.FONT_MEDIUM))
            lbl.setWordWrap(False)
            rl.addWidget(lbl, 1)

            # Show/hide toggle
            vis_btn = QPushButton()
            vis_btn.setFixedSize(28, 24)
            vis_btn.setCheckable(True)
            vis_btn.setChecked(sketch["visible"])
            vis_btn.setToolTip("Show / hide on map")
            vis_btn.setIcon(theme.icon("eye" if sketch["visible"] else "eye-off", theme.MUTED, 15))
            vis_btn.setIconSize(QSize(15, 15))
            vis_btn.setStyleSheet(
                "QPushButton { background:%s; border:none; border-radius:5px; }"
                "QPushButton:hover { background:%s; }"
                % (theme.SUNKEN, theme.ACCENT_SOFT))
            def _make_vis(idx, btn):
                def _toggle(checked):
                    self._measure_sketches[idx]["visible"] = checked
                    btn.setIcon(theme.icon("eye" if checked else "eye-off", theme.MUTED, 15))
                    lname = self._measure_sketches[idx]["layer_name"]
                    fid   = self._measure_sketches[idx]["fid"]
                    lyrs  = QgsProject.instance().mapLayersByName(lname)
                    if lyrs:
                        lyr = lyrs[0]
                        current = lyr.subsetString() or ""
                        import re
                        excluded = set(
                            int(m) for m in re.findall(r"\$id\s*!=\s*(\d+)", current)
                        )
                        if checked:
                            excluded.discard(fid)
                        else:
                            excluded.add(fid)
                        if excluded:
                            lyr.setSubsetString(" AND ".join(f"$id != {f}" for f in sorted(excluded)))
                        else:
                            lyr.setSubsetString("")
                        lyr.triggerRepaint()
                        self.iface.mapCanvas().refresh()
                return _toggle
            vis_btn.toggled.connect(_make_vis(i, vis_btn))
            rl.addWidget(vis_btn)

            # Edit name button
            edit_btn = QPushButton()
            edit_btn.setFixedSize(24, 24)
            edit_btn.setToolTip("Rename")
            edit_btn.setIcon(theme.icon("pencil", theme.MUTED, 14))
            edit_btn.setIconSize(QSize(14, 14))
            edit_btn.setStyleSheet(
                "QPushButton { background:%s; border:none; border-radius:5px; }"
                "QPushButton:hover { background:%s; }"
                % (theme.SUNKEN, theme.ACCENT_SOFT))
            def _make_edit(idx):
                def _do_edit():
                    current_name = self._measure_sketches[idx]["name"]
                    new_name, ok = QInputDialog.getText(
                        self, "Rename Sketch", "Name:", text=current_name
                    )
                    if ok and new_name.strip():
                        sk = self._measure_sketches[idx]
                        sk["name"] = new_name.strip()
                        # Write through to the feature so the rename persists.
                        lyrs = QgsProject.instance().mapLayersByName(sk["layer_name"])
                        if lyrs:
                            fidx = lyrs[0].fields().indexOf("name")
                            if fidx >= 0:
                                lyrs[0].dataProvider().changeAttributeValues(
                                    {sk["fid"]: {fidx: sk["name"]}})
                        self._refresh_measure_list()
                return _do_edit
            edit_btn.clicked.connect(_make_edit(i))
            rl.addWidget(edit_btn)

            # Delete button
            del_btn = QPushButton()
            del_btn.setFixedSize(24, 24)
            del_btn.setToolTip("Delete")
            del_btn.setIcon(theme.icon("trash", theme.BAD, 14))
            del_btn.setIconSize(QSize(14, 14))
            del_btn.setStyleSheet(
                "QPushButton { background:%s; border:none; border-radius:5px; }"
                "QPushButton:hover { background:#F4CFCB; }"
                % theme.BAD_BG)
            def _make_del(idx):
                def _do_del():
                    sk = self._measure_sketches[idx]
                    lyrs = QgsProject.instance().mapLayersByName(sk["layer_name"])
                    if lyrs:
                        lyr = lyrs[0]
                        lyr.dataProvider().deleteFeatures([sk["fid"]])
                        lyr.updateExtents()
                        lyr.triggerRepaint()
                        self.iface.mapCanvas().refresh()
                    self._measure_sketches.pop(idx)
                    self._refresh_measure_list()
                return _do_del
            del_btn.clicked.connect(_make_del(i))
            rl.addWidget(del_btn)

            self._sketch_list_layout.addWidget(row)

    # ── Loss layer ────────────────────────────────────────────────────────────

    def _remove_loss_layer(self):
        for lyr in QgsProject.instance().mapLayersByName(LOSS_LAYER_NAME):
            QgsProject.instance().removeMapLayer(lyr.id())
        self.iface.mapCanvas().refresh()

    def _draw_loss_layer(self):
        self._remove_loss_layer()

        # Determine CRS from whichever baseline layer exists first
        ref_crs = None
        for n in ["Baseline - Area Habitats", "Baseline - Hedgerow Units",
                  "Baseline - Individual Trees"]:
            lyrs = QgsProject.instance().mapLayersByName(n)
            if lyrs:
                ref_crs = lyrs[0].crs()
                break
        if ref_crs is None:
            return

        crs_authid = ref_crs.authid() or "EPSG:27700"
        loss_layer = QgsVectorLayer(f"Polygon?crs={crs_authid}", LOSS_LAYER_NAME, "memory")
        provider   = loss_layer.dataProvider()
        provider.addAttributes([
            QgsField("habitat",         QVariant.String),
            QgsField("area_m2",         QVariant.Double),
            QgsField("distinctiveness", QVariant.Int),
            QgsField("loss_type",       QVariant.String),
        ])
        loss_layer.updateFields()
        loss_features = []

        # ── 1. AREA HABITATS ─────────────────────────────────────────────
        bl_area_lyrs   = QgsProject.instance().mapLayersByName("Baseline - Area Habitats")
        prop_area_lyrs = QgsProject.instance().mapLayersByName("Proposed - Area Habitats")

        if bl_area_lyrs and bl_area_lyrs[0].featureCount() > 0:
            bl_layer   = bl_area_lyrs[0]
            prop_layer = prop_area_lyrs[0] if prop_area_lyrs else None
            da         = self._make_da(bl_layer)

            # Union proposed habitat-value polygons only
            proposed_geom = QgsGeometry()
            if prop_layer:
                for feat in prop_layer.getFeatures():
                    g = feat.geometry()
                    if not g or g.isNull() or g.isEmpty():
                        continue
                    if self._is_zero_value(self._get_sub(feat)):
                        continue
                    if prop_layer.crs() != ref_crs:
                        g.transform(QgsCoordinateTransform(
                            prop_layer.crs(), ref_crs, QgsProject.instance()))
                    proposed_geom = g if proposed_geom.isNull() else proposed_geom.combine(g)

            for feat in bl_layer.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull() or geom.isEmpty():
                    continue
                sub   = self._get_sub(feat)
                score = self._deminimis_score(sub)
                if self._is_zero_value(sub):
                    continue
                # Skip sliver baseline parcels before computing difference
                if da.measureArea(geom) < 0.1:
                    continue
                lost_geom = (QgsGeometry(geom) if proposed_geom.isNull() or proposed_geom.isEmpty()
                             else geom.difference(proposed_geom))
                if not lost_geom or lost_geom.isNull() or lost_geom.isEmpty():
                    continue
                if QgsWkbTypes.geometryType(lost_geom.wkbType()) != QgsWkbTypes.GeometryType.PolygonGeometry:
                    lost_geom = _coerce_to_polygon(lost_geom)
                    if not lost_geom:
                        continue
                area_m2 = da.measureArea(lost_geom)
                if area_m2 < 0.1:   # discard slivers from geometric difference
                    continue
                f = QgsFeature(loss_layer.fields())
                f.setGeometry(lost_geom)
                f.setAttributes([sub, round(area_m2, 2), score, "area"])
                loss_features.append(f)

        # ── 2. HEDGEROWS ─────────────────────────────────────────────────
        # Buffer lost baseline hedgerow segments by 1 m each side to make
        # them visible as polygons.  Compare by total length per sub-type;
        # if proposed has less, the difference proportion is shown as a buffer.
        for hedge_bl_name, hedge_pr_name in [
            ("Baseline - Hedgerow Units", "Proposed - Hedgerow Units")
        ]:
            bl_hedge_lyrs = QgsProject.instance().mapLayersByName(hedge_bl_name)
            if not bl_hedge_lyrs or bl_hedge_lyrs[0].featureCount() == 0:
                continue
            bl_h  = bl_hedge_lyrs[0]
            pr_h_lyrs = QgsProject.instance().mapLayersByName(hedge_pr_name)

            # Union proposed hedgerow lines into one geometry
            proposed_hedge_geom = QgsGeometry()
            if pr_h_lyrs:
                for feat in pr_h_lyrs[0].getFeatures():
                    g = feat.geometry()
                    if not g or g.isNull() or g.isEmpty():
                        continue
                    if pr_h_lyrs[0].crs() != ref_crs:
                        g.transform(QgsCoordinateTransform(
                            pr_h_lyrs[0].crs(), ref_crs, QgsProject.instance()))
                    proposed_hedge_geom = (g if proposed_hedge_geom.isNull()
                                           else proposed_hedge_geom.combine(g))

            da_h = self._make_da(bl_h)
            for feat in bl_h.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull() or geom.isEmpty():
                    continue
                sub   = self._get_sub(feat)
                score = self._deminimis_score(sub)
                if self._is_zero_value(sub):
                    continue

                # Reproject to ref CRS if needed
                g = QgsGeometry(geom)
                if bl_h.crs() != ref_crs:
                    g.transform(QgsCoordinateTransform(
                        bl_h.crs(), ref_crs, QgsProject.instance()))

                # Compute lost portion: baseline line minus proposed lines
                if proposed_hedge_geom.isNull() or proposed_hedge_geom.isEmpty():
                    lost_line = g
                else:
                    lost_line = g.difference(proposed_hedge_geom)

                if not lost_line or lost_line.isNull() or lost_line.isEmpty():
                    continue

                # Buffer 1 m each side to create a visible polygon
                buffered = lost_line.buffer(1.0, 5)
                if not buffered or buffered.isNull() or buffered.isEmpty():
                    continue

                length_m = da_h.measureLength(lost_line)
                if length_m < 0.1:
                    continue

                f = QgsFeature(loss_layer.fields())
                f.setGeometry(buffered)
                # Store length in area_m2 field for label display (clearly labelled)
                f.setAttributes([sub, round(length_m, 2), score, "hedgerow"])
                loss_features.append(f)

        # ── 3. INDIVIDUAL TREES ──────────────────────────────────────────
        # For each baseline tree not present in proposed, draw a circle with
        # radius derived from the statutory area equivalent.
        for tree_bl_name, tree_pr_name in [
            ("Baseline - Individual Trees", "Proposed - Individual Trees")
        ]:
            bl_tree_lyrs = QgsProject.instance().mapLayersByName(tree_bl_name)
            if not bl_tree_lyrs or bl_tree_lyrs[0].featureCount() == 0:
                continue
            bl_t  = bl_tree_lyrs[0]
            pr_t_lyrs = QgsProject.instance().mapLayersByName(tree_pr_name)

            # Build set of proposed tree points (buffered slightly) for comparison
            proposed_tree_geom = QgsGeometry()
            if pr_t_lyrs:
                for feat in pr_t_lyrs[0].getFeatures():
                    g = feat.geometry()
                    if not g or g.isNull() or g.isEmpty():
                        continue
                    if pr_t_lyrs[0].crs() != ref_crs:
                        g.transform(QgsCoordinateTransform(
                            pr_t_lyrs[0].crs(), ref_crs, QgsProject.instance()))
                    # Buffer each proposed tree point by 0.5 m to allow spatial match
                    b = g.buffer(0.5, 8)
                    proposed_tree_geom = (b if proposed_tree_geom.isNull()
                                          else proposed_tree_geom.combine(b))

            import math
            for feat in bl_t.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull() or geom.isEmpty():
                    continue
                sub      = self._get_sub(feat)
                score    = self._deminimis_score(sub)
                size_cls = tree_size_from_sub_type(sub)
                area_ha  = TREE_SIZE_AREAS.get(size_cls, 0.0)
                if area_ha == 0.0:
                    continue

                # Reproject point to ref CRS if needed
                g = QgsGeometry(geom)
                if bl_t.crs() != ref_crs:
                    g.transform(QgsCoordinateTransform(
                        bl_t.crs(), ref_crs, QgsProject.instance()))

                # Skip if this tree point is within proposed tree buffer
                if (not proposed_tree_geom.isNull() and
                        not proposed_tree_geom.isEmpty() and
                        g.intersects(proposed_tree_geom)):
                    continue

                # Draw circle with radius from area equivalent
                area_m2 = area_ha * 10_000
                radius  = math.sqrt(area_m2 / math.pi)
                circle  = g.buffer(radius, 16)
                if not circle or circle.isNull() or circle.isEmpty():
                    continue

                f = QgsFeature(loss_layer.fields())
                f.setGeometry(circle)
                f.setAttributes([sub, round(area_m2, 2), score, "tree"])
                loss_features.append(f)

        # ── Add to map ────────────────────────────────────────────────────
        if not loss_features:
            return

        provider.addFeatures(loss_features)
        loss_layer.updateExtents()
        loss_layer.setCustomProperty("skipMemoryLayersCheck", 1)
        self._apply_loss_style(loss_layer)
        self._apply_loss_labels(loss_layer)
        QgsProject.instance().addMapLayer(loss_layer, False)
        QgsProject.instance().layerTreeRoot().insertLayer(0, loss_layer)
        self.iface.mapCanvas().refresh()

    def _apply_loss_style(self, layer):
        try:
            root_rule = QgsRuleBasedRenderer.Rule(None)

            def make_rule(label, expr, fill_color, stroke_color):
                symbol = QgsFillSymbol()
                solid  = QgsSimpleFillSymbolLayer()
                solid.setFillColor(QColor(fill_color))
                solid.setStrokeColor(QColor(stroke_color))
                solid.setStrokeWidth(0.8)
                symbol.changeSymbolLayer(0, solid)
                try:
                    from qgis.core import QgsLinePatternFillSymbolLayer
                    hatch = QgsLinePatternFillSymbolLayer()
                    hatch.setColor(QColor(stroke_color))
                    hatch.setLineWidth(0.5)
                    hatch.setDistance(3.0)
                    hatch.setLineAngle(45)
                    symbol.appendSymbolLayer(hatch)
                except Exception:
                    pass
                rule = QgsRuleBasedRenderer.Rule(symbol)
                rule.setLabel(label)
                rule.setFilterExpression(expr)
                return rule

            root_rule.appendChild(make_rule("Priority habitat loss",   '"distinctiveness" >= 6', "200,30,30,120", "#8b0000"))
            root_rule.appendChild(make_rule("Habitat Loss",            '"distinctiveness" < 6',  "230,100,0,100", "#8A4B12"))
            layer.setRenderer(QgsRuleBasedRenderer(root_rule))
            layer.triggerRepaint()
        except Exception:
            symbol = QgsFillSymbol.createSimple({"color": "200,30,30,80", "outline_color": "#8F2A23", "outline_width": "0.8"})
            from qgis.core import QgsSingleSymbolRenderer
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.triggerRepaint()

    def _apply_loss_labels(self, layer):
        pal = QgsPalLayerSettings()
        # Show metres for hedgerows, m² for area/trees
        pal.fieldName = (
            "CASE "
            "WHEN \"loss_type\" = 'hedgerow' "
            "THEN to_string(round(\"area_m2\", 1)) || ' m\\n' || \"habitat\" "
            "ELSE to_string(round(\"area_m2\", 1)) || ' m²\\n' || \"habitat\" "
            "END"
        )
        pal.isExpression = True
        try:
            pal.placement = QgsPalLayerSettings.Placement.Horizontal
        except AttributeError:
            pal.placement = QgsPalLayerSettings.Placement.Horizontal
        fmt  = QgsTextFormat()
        font = QFont("Inter", 8)
        font.setBold(True)
        fmt.setFont(font)
        fmt.setSize(8)
        fmt.setColor(QColor("#ffffff"))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.2)
        buf.setColor(QColor("#8b0000"))
        fmt.setBuffer(buf)
        pal.setFormat(fmt)
        pal.enabled = True
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    # ── De minimis ────────────────────────────────────────────────────────────

    def _get_sub(self, feat):
        try:
            return str(feat["sub_habitat_type"]) if feat["sub_habitat_type"] else "Unknown"
        except Exception:
            return "Unknown"

    def _deminimis_score(self, sub):
        return DISTINCTIVENESS_SCORES.get(sub, 2)

    def _is_priority_area(self, sub):
        return self._deminimis_score(sub) >= 6

    def _is_priority_linear(self, sub):
        return self._deminimis_score(sub) >= 8

    def _is_zero_value(self, sub):
        return sub in ZERO_VALUE_SUBTYPES or self._deminimis_score(sub) == 0

    def _make_da(self, layer):
        da = QgsDistanceArea()
        da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(_ellipsoid())
        return da

    def _collect_area_baseline(self):
        results = []
        for lyr in QgsProject.instance().mapLayersByName("Baseline - Area Habitats"):
            da = self._make_da(lyr)
            for feat in lyr.getFeatures():
                g = feat.geometry()
                if g and not g.isNull() and not g.isEmpty():
                    area = da.measureArea(g)
                    if area >= 0.1:   # ignore slivers < 0.1 m²
                        results.append((self._get_sub(feat), area))
        return results

    def _collect_proposed_area(self):
        totals = {}
        for lyr in QgsProject.instance().mapLayersByName("Proposed - Area Habitats"):
            da = self._make_da(lyr)
            for feat in lyr.getFeatures():
                g = feat.geometry()
                if g and not g.isNull() and not g.isEmpty():
                    area = da.measureArea(g)
                    if area >= 0.1:   # ignore slivers < 0.1 m²
                        sub = self._get_sub(feat)
                        totals[sub] = totals.get(sub, 0.0) + area
        return totals

    def _collect_linear_baseline(self):
        out = {}
        for lname in ["Baseline - Hedgerow Units", "Baseline - Watercourse Habitat"]:
            rows = []
            for lyr in QgsProject.instance().mapLayersByName(lname):
                da = self._make_da(lyr)
                for feat in lyr.getFeatures():
                    g = feat.geometry()
                    if g and not g.isNull() and not g.isEmpty():
                        rows.append((self._get_sub(feat), da.measureLength(g)))
            out[lname] = rows
        return out

    def _collect_proposed_linear(self):
        out = {}
        for lname in ["Proposed - Hedgerow Units", "Proposed - Watercourse Habitat"]:
            total = 0.0
            for lyr in QgsProject.instance().mapLayersByName(lname):
                da = self._make_da(lyr)
                for feat in lyr.getFeatures():
                    g = feat.geometry()
                    if g and not g.isNull() and not g.isEmpty():
                        total += da.measureLength(g)
            out[lname] = total
        return out

    def _collect_tree_baseline(self):
        results = []
        for lyr in QgsProject.instance().mapLayersByName("Baseline - Individual Trees"):
            for feat in lyr.getFeatures():
                sub = self._get_sub(feat)
                results.append((sub, TREE_SIZE_AREAS.get(tree_size_from_sub_type(sub), 0.0)))
        return results

    def _collect_proposed_tree_area_ha(self):
        total = 0.0
        for lyr in QgsProject.instance().mapLayersByName("Proposed - Individual Trees"):
            for feat in lyr.getFeatures():
                total += TREE_SIZE_AREAS.get(tree_size_from_sub_type(self._get_sub(feat)), 0.0)
        return total

    def _calc_gross_area_loss_m2(self):
        """Return gross area habitat loss in m² using the same geometric
        difference logic as _draw_loss_layer, so both figures always agree.

        For each baseline feature the proposed union is subtracted spatially;
        the remaining (lost) area is summed.  Zero-value and sliver (<0.1 m²)
        features are excluded, matching the loss-layer rules exactly.
        """
        bl_lyrs   = QgsProject.instance().mapLayersByName("Baseline - Area Habitats")
        prop_lyrs = QgsProject.instance().mapLayersByName("Proposed - Area Habitats")

        if not bl_lyrs or bl_lyrs[0].featureCount() == 0:
            return 0.0

        bl_layer   = bl_lyrs[0]
        prop_layer = prop_lyrs[0] if prop_lyrs else None
        ref_crs    = bl_layer.crs()
        da         = self._make_da(bl_layer)

        # Build union of all proposed polygons that have habitat value
        proposed_geom = QgsGeometry()
        if prop_layer:
            for feat in prop_layer.getFeatures():
                g = feat.geometry()
                if not g or g.isNull() or g.isEmpty():
                    continue
                if self._is_zero_value(self._get_sub(feat)):
                    continue
                if prop_layer.crs() != ref_crs:
                    g.transform(QgsCoordinateTransform(
                        prop_layer.crs(), ref_crs, QgsProject.instance()))
                proposed_geom = g if proposed_geom.isNull() else proposed_geom.combine(g)

        total_loss = 0.0
        for feat in bl_layer.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isNull() or geom.isEmpty():
                continue
            sub = self._get_sub(feat)
            if self._is_zero_value(sub):
                continue
            if da.measureArea(geom) < 0.1:
                continue
            lost_geom = (QgsGeometry(geom) if proposed_geom.isNull() or proposed_geom.isEmpty()
                         else geom.difference(proposed_geom))
            if not lost_geom or lost_geom.isNull() or lost_geom.isEmpty():
                continue
            if QgsWkbTypes.geometryType(lost_geom.wkbType()) != QgsWkbTypes.GeometryType.PolygonGeometry:
                lost_geom = _coerce_to_polygon(lost_geom)
                if not lost_geom:
                    continue
            area_m2 = da.measureArea(lost_geom)
            if area_m2 < 0.1:
                continue
            total_loss += area_m2

        return total_loss

    def _add_deminimis_check(self):
        baseline_area    = self._collect_area_baseline()
        proposed_area    = self._collect_proposed_area()
        baseline_lin     = self._collect_linear_baseline()
        proposed_lin     = self._collect_proposed_linear()
        baseline_trees   = self._collect_tree_baseline()
        proposed_tree_ha = self._collect_proposed_tree_area_ha()

        has_baseline = (len(baseline_area) > 0 or any(len(v) > 0 for v in baseline_lin.values()) or len(baseline_trees) > 0)
        has_proposed = (len(proposed_area) > 0 or any(v > 0 for v in proposed_lin.values()) or proposed_tree_ha > 0)

        self._add_divider()
        self._add_header("De Minimis Check", theme.HEADER_SOFT, "license")

        # De minimis explanation
        spiel = QLabel(
            "<b>What is de minimis?</b><br>"
            "Under the statutory BNG regulations, developments that cause only a very small "
            "amount of habitat loss may qualify for a de minimis exemption — meaning they are "
            "not required to deliver a 10% biodiversity net gain. To qualify, the gross habitat "
            "loss must fall below all of the following thresholds:<br><br>"
            f"&nbsp;&nbsp;• Area habitats: &lt; <b>{DEMINIMIS_AREA_THRESHOLD_M2:.0f} m²</b><br>"
            f"&nbsp;&nbsp;• Hedgerows or watercourses: &lt; <b>{DEMINIMIS_LINEAR_THRESHOLD_M:.0f} m</b><br><br>"
            "<b>How this tool calculates it:</b><br>"
            "Gross loss is calculated geometrically — each baseline habitat polygon has the "
            "proposed habitat footprint subtracted from it spatially. The remaining (lost) area "
            "is summed across all non-zero-value habitats. Priority habitats automatically "
            "disqualify the exemption regardless of area. Zero-value habitats (e.g. sealed "
            "surfaces) are excluded from the calculation."
        )
        spiel.setTextFormat(Qt.TextFormat.RichText)
        spiel.setWordWrap(True)
        spiel.setStyleSheet(
            "font-size:11px; color:%s; padding:8px 10px;"
            " background:%s; border:1px solid %s; border-radius:6px;"
            % (theme.MUTED, theme.SUNKEN, theme.BORDER)
        )
        self.results_layout.addWidget(spiel)

        if not has_baseline:
            self._add_info_row("No baseline data.")
            return
        if not has_proposed:
            self._add_info_row("Add proposed habitats to calculate gross loss.")
            self._add_baseline_breakdown(baseline_area, baseline_lin, baseline_trees)
            return

        priority_disqualifiers = list(dict.fromkeys(
            [sub for sub, _ in baseline_area if self._is_priority_area(sub)] +
            [f"{sub} (linear)" for rows in baseline_lin.values() for sub, _ in rows if self._is_priority_linear(sub)]
        ))

        # Use geometric difference (same as loss layer) so both figures agree
        gross_area_loss_m2 = self._calc_gross_area_loss_m2()
        gross_area_loss_m2 += max(0.0, sum(a for _, a in baseline_trees) - proposed_tree_ha) * 10_000

        hedge_bl  = sum(l for s, l in baseline_lin.get("Baseline - Hedgerow Units", []) if not self._is_zero_value(s))
        hedge_pr  = proposed_lin.get("Proposed - Hedgerow Units", 0.0)
        gross_hedge = max(0.0, hedge_bl - hedge_pr)

        wc_bl  = sum(l for s, l in baseline_lin.get("Baseline - Watercourse Habitat", []) if not self._is_zero_value(s))
        wc_pr  = proposed_lin.get("Proposed - Watercourse Habitat", 0.0)
        gross_wc = max(0.0, wc_bl - wc_pr)

        gross_linear = max(gross_hedge, gross_wc)

        if priority_disqualifiers:
            rag = "RED_PRIORITY"
        elif gross_area_loss_m2 >= DEMINIMIS_AREA_THRESHOLD_M2 or gross_linear >= DEMINIMIS_LINEAR_THRESHOLD_M:
            rag = "RED"
        else:
            rag = "GREEN"

        self._add_baseline_breakdown(baseline_area, baseline_lin, baseline_trees)
        self._add_sub_header("Gross habitat loss vs proposed")

        ac, ai = self._rag_style(gross_area_loss_m2, DEMINIMIS_AREA_THRESHOLD_M2, DEMINIMIS_AREA_THRESHOLD_M2 - DEMINIMIS_AMBER_MARGIN)
        self._add_loss_row("  Area habitats", f"{gross_area_loss_m2:,.1f} m²", f"threshold: {DEMINIMIS_AREA_THRESHOLD_M2:.0f} m²", ai, ac)

        if hedge_bl > 0 or hedge_pr > 0:
            hc, hi = self._rag_style(gross_hedge, DEMINIMIS_LINEAR_THRESHOLD_M, DEMINIMIS_LINEAR_THRESHOLD_M - 1.0)
            self._add_loss_row("  Hedgerows", f"{gross_hedge:,.1f} m", f"threshold: {DEMINIMIS_LINEAR_THRESHOLD_M:.0f} m", hi, hc)

        if wc_bl > 0 or wc_pr > 0:
            wc2, wi = self._rag_style(gross_wc, DEMINIMIS_LINEAR_THRESHOLD_M, DEMINIMIS_LINEAR_THRESHOLD_M - 1.0)
            self._add_loss_row("  Watercourses", f"{gross_wc:,.1f} m", f"threshold: {DEMINIMIS_LINEAR_THRESHOLD_M:.0f} m", wi, wc2)

        if priority_disqualifiers:
            prow = QWidget()
            prow.setObjectName("measPriRow")
            pl   = QHBoxLayout(prow)
            pl.setContentsMargins(6, 3, 6, 3)
            pl.setSpacing(5)
            plead = _ElidingLabel("  Priority habitats present")
            plead.setStyleSheet("background:transparent; border:none; font-size:11px;")
            pl.addWidget(plead, 1)
            picon = QLabel()
            picon.setPixmap(self._pix("status-alert", theme.BAD, 14))
            picon.setStyleSheet("background:transparent; border:none;")
            pl.addWidget(picon, 0)
            pval = QLabel("De minimis disqualified")
            pval.setStyleSheet(
                "color:%s; font-weight:600; background:transparent; border:none;" % theme.BAD)
            pval.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pl.addWidget(pval, 0)
            prow.setStyleSheet("QWidget#measPriRow { background:%s; border-radius:5px; }" % theme.BAD_BG)
            self.results_layout.addWidget(prow)
            for d in priority_disqualifiers[:3]:
                self._add_info_row(f"    • {d}", color=theme.BAD)

        self._add_divider()
        self._add_verdict(rag)

    def _add_baseline_breakdown(self, baseline_area, baseline_lin, baseline_trees):
        self._add_sub_header("Baseline habitat parcels")
        labels = {2: "low", 4: "medium", 6: "high", 8: "very high"}
        lin_labels = {1: "very low", 2: "low", 4: "medium", 6: "high", 8: "very high"}
        for sub, area in baseline_area:
            if self._is_zero_value(sub):
                tag, tc = "no value — excluded", theme.FAINT
            elif self._is_priority_area(sub):
                tag, tc = "priority", theme.BAD
            else:
                tag, tc = f"distinctiveness: {labels.get(self._deminimis_score(sub), '?')}", theme.GOOD
            self._add_breakdown_row(sub, f"{area:,.1f} m²", tag, tc)
        for lname, rows in baseline_lin.items():
            for sub, length in rows:
                if self._is_zero_value(sub):
                    tag, tc = "no value — excluded", theme.FAINT
                elif self._is_priority_linear(sub):
                    tag, tc = "priority", theme.BAD
                else:
                    tag, tc = f"distinctiveness: {lin_labels.get(self._deminimis_score(sub), '?')}", theme.GOOD
                self._add_breakdown_row(sub, f"{length:,.1f} m", tag, tc)
        for sub, area_ha in baseline_trees:
            self._add_breakdown_row(sub, f"{area_ha * 10_000:,.1f} m² equiv.", "medium distinctiveness", theme.GOOD)

    def _rag_style(self, value, red_thr, amber_thr):
        if value >= amber_thr:
            return theme.BAD_BG, "status-alert"
        return theme.GOOD_BG, "status-check"

    def _add_verdict(self, rag):
        v = {
            "GREEN":        (theme.GOOD_BG, theme.GOOD, "status-check",
                             "De minimis likely applies",
                             "Gross habitat loss is below all thresholds. Include a de minimis exemption statement with your application."),
            "AMBER":        (theme.WARN_BG, theme.WARN, "status-alert",
                             "Subject to de minimis",
                             "Gross loss is close to the threshold. De minimis may apply — verify with your LPA before relying on the exemption."),
            "RED":          (theme.BAD_BG, theme.BAD, "status-alert",
                             "De minimis does not apply",
                             "Gross habitat loss exceeds the threshold. Full BNG is required."),
            "RED_PRIORITY": (theme.BAD_BG, theme.BAD, "status-alert",
                             "De minimis does not apply — priority habitat present",
                             "Priority habitat within the red line boundary disqualifies de minimis regardless of area. Full BNG is required."),
        }
        bg, fg, icon_name, title, note = v.get(rag, v["RED"])
        card = QWidget()
        # #id-scoped: the bare `border-left` otherwise cascades onto the child
        # labels, giving each a stray 3px left border.
        card.setObjectName("measRagCard")
        cl   = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(3)
        trow = QHBoxLayout()
        trow.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(self._pix(icon_name, fg, 15))
        ic.setStyleSheet("background:transparent; border:none;")
        trow.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        t = QLabel(f"<b>{title}</b>")
        t.setWordWrap(True)
        t.setStyleSheet("color:%s; font-size:12px; background:transparent; border:none;" % fg)
        trow.addWidget(t, 1)
        cl.addLayout(trow)
        n = QLabel(note)
        n.setWordWrap(True)
        n.setStyleSheet("color:%s; font-size:11px; background:transparent; border:none;" % fg)
        cl.addWidget(n)
        card.setStyleSheet("QWidget#measRagCard { background:%s; border-radius:6px;"
                           " border-left:3px solid %s; }" % (bg, fg))
        self.results_layout.addWidget(card)

    # ── Row helpers ───────────────────────────────────────────────────────────

    def _add_header(self, text, color=None, icon_name=None):
        color = color or theme.ACCENT
        bar = QWidget()
        bar.setObjectName("measHeaderBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(7, 4, 8, 4)
        h.setSpacing(6)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(self._pix(icon_name, "#FFFFFF", 14))
            ic.setStyleSheet("background:transparent; border:none;")
            h.addWidget(ic, 0)
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "background:transparent; border:none; color:white;"
            " font-family:%s; font-size:12px; font-weight:600;" % theme.FONT_SEMIBOLD)
        h.addWidget(lbl, 1)
        bar.setStyleSheet("QWidget#measHeaderBar { background:%s; border-radius:5px; }" % color)
        self.results_layout.addWidget(bar)

    def _add_sub_header(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:%s; font-family:%s; font-size:11px; font-weight:600;"
            " padding:6px 4px 3px 4px; border-bottom:1px solid %s;"
            % (theme.MUTED, theme.FONT_SEMIBOLD, theme.BORDER))
        self.results_layout.addWidget(lbl)

    def _add_row(self, label, value, bold=False, highlight=False):
        row = QWidget()
        row.setObjectName("measRow")
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(6, 2, 6, 2)
        # Long habitat names elide rather than force the row (and the whole
        # results panel) wider than the dock, which used to clip the value.
        lbl = _ElidingLabel(label)
        lbl.setStyleSheet(
            "background:transparent; border:none; font-size:12px;%s"
            % (" font-weight:600;" if bold else ""))
        if bold:
            f = lbl.font(); f.setBold(True); lbl.setFont(f)
        val = QLabel(f"<b>{value}</b>" if bold else value)
        val.setTextFormat(Qt.TextFormat.RichText)
        val.setStyleSheet("background:transparent; border:none; font-size:12px;")
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rl.addWidget(lbl, 1)
        rl.addWidget(val, 0)
        if highlight:
            row.setStyleSheet("QWidget#measRow { background:%s; border-radius:5px; }" % theme.SUNKEN)
        self.results_layout.addWidget(row)

    def _add_breakdown_row(self, name, measurement, tag, tag_color):
        row = QWidget()
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(6, 1, 6, 1)
        nl = _ElidingLabel(f"  {name}")
        nl.setStyleSheet("font-size:11px; color:%s;" % theme.INK)
        ml = QLabel(measurement)
        ml.setStyleSheet("font-size:11px; color:%s;" % theme.MUTED)
        ml.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tl = QLabel(tag)
        tl.setStyleSheet(
            "font-size:10px; color:%s; padding:1px 4px; min-width:120px;" % tag_color)
        tl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rl.addWidget(nl, 3)
        rl.addWidget(ml, 2)
        rl.addWidget(tl, 2)
        self.results_layout.addWidget(row)

    def _add_loss_row(self, label, value, threshold_note, icon_name, bg_color):
        row = QWidget()
        row.setObjectName("measLossRow")
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(6, 3, 6, 3)
        rl.setSpacing(5)
        ll = _ElidingLabel(label)
        ll.setStyleSheet("font-size:11px; background:transparent; border:none;")
        vl = QLabel(f"<b>{value}</b>")
        vl.setStyleSheet("font-size:11px; background:transparent; border:none;")
        vl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Icon + threshold note travel together, right-aligned as one unit so
        # the icon always sits flush against its caption.
        endw = QWidget()
        endw.setStyleSheet("background:transparent;")
        eh = QHBoxLayout(endw)
        eh.setContentsMargins(0, 0, 0, 0)
        eh.setSpacing(4)
        eh.addStretch(1)
        icl = QLabel()
        icol = theme.BAD if icon_name == "status-alert" else theme.GOOD
        icl.setPixmap(self._pix(icon_name, icol, 13))
        icl.setStyleSheet("background:transparent; border:none;")
        eh.addWidget(icl, 0)
        tl = QLabel(threshold_note)
        tl.setStyleSheet("font-size:10px; color:%s; background:transparent; border:none;" % theme.MUTED)
        eh.addWidget(tl, 0)
        rl.addWidget(ll, 3)
        rl.addWidget(vl, 2)
        rl.addWidget(endw, 2)
        row.setStyleSheet("QWidget#measLossRow { background:%s; border-radius:5px; }" % bg_color)
        self.results_layout.addWidget(row)

    def _add_info_row(self, text, color=None):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:%s; font-size:11px; padding:2px 6px;" % (color or theme.MUTED))
        lbl.setWordWrap(True)
        self.results_layout.addWidget(lbl)

    def _add_divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background:%s; border:none;" % theme.BORDER)
        self.results_layout.addWidget(line)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def showEvent(self, event):
        """Reconnect signals and refresh when navigating back to this screen."""
        self._connect_layer_signals()
        QgsProject.instance().layersAdded.connect(self._on_layers_added)
        QgsProject.instance().layersRemoved.connect(self._on_layers_removed)
        QTimer.singleShot(100, self._refresh)
        super().showEvent(event)

    def hideEvent(self, event):
        """Stop the refresh timer and disconnect signals when navigating away.
        The loss layer is intentionally left on the map so it remains visible
        for the de minimis map builder and general inspection."""
        try:
            self._refresh_timer.stop()
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
            QgsProject.instance().layersRemoved.disconnect(self._on_layers_removed)
        except Exception:
            pass
        super().hideEvent(event)


# Backwards-compat alias
MeasurementsDialog = MeasurementsWidget
MeasurementsDock   = MeasurementsWidget
