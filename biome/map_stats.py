# map_stats.py
# Per-habitat figures (size + indicative BNG units) for tagging onto each
# legend entry on Baseline / Proposed habitat maps.
#
# This does NOT re-implement the unit maths. It reuses the same classifiers the
# Summary tab uses as its single source of truth — classify_area_net /
# classify_linear_net / classify_trees_net — and the same
# distinctiveness × condition × strategic-significance multipliers, so the
# numbers on the map match the Summary tab exactly.

from .habitat_data import (
    DISTINCTIVENESS_SCORES, CONDITION_MULTIPLIERS, STRATEGIC_MULTIPLIERS,
)
from .layer_manager import (TREE_SIZE_AREAS, tree_size_from_sub_type,
                            tree_location_from_sub_type)
from .config import measurement_ellipsoid

_TREE_DIST = 4  # all individual trees are Medium distinctiveness


def _d(sub):
    return DISTINCTIVENESS_SCORES.get(sub, 0)


def _c(cond):
    return CONDITION_MULTIPLIERS.get(cond or "", 1.0)


def _s(strat):
    return STRATEGIC_MULTIPLIERS.get(strat or "", 1.0)


def _add(figs, sub, size, units, unit):
    """Accumulate a parcel into the per-habitat figures dict."""
    e = figs.setdefault(sub, {"size": 0.0, "units": 0.0, "unit": unit})
    e["size"]  += size
    e["units"] += units


def _water_enc(row, BNGM, proposed=False):
    """Channel x banks encroachment multiplier for a watercourse row (1.0 if the
    BNG module is unavailable). proposed=True uses the post-intervention
    encroachment (enhancement rows, whose prop_* encroachment may differ from
    baseline). The combined riparian string is canonical (set by _linear_buckets)."""
    if BNGM is None:
        return 1.0
    return BNGM.water_row_encroachment_mult(row, proposed=proposed)


def _prop_mult(base, BNGM, kind, sub, cond, start, base_val=0.0, base_sub=None):
    """Discounted proposed units. ``base`` is the full proposed value.

    For CREATION ``base_val`` stays 0 → ``base * mult`` (whole value discounted).
    For ENHANCEMENT pass ``base_val`` = the parcel's baseline-rate value so only
    the uplift is discounted (matches the metric's A-3 units; see
    bng_multipliers.blend_enhanced_units). ``base_sub`` is the baseline habitat,
    so a habitat upgrade picks up the establishment temporal."""
    if BNGM is None:
        return base
    try:
        mult, _ = BNGM.proposed_multiplier(kind, sub, cond, start_condition=start,
                                           baseline_habitat=base_sub)
        return BNGM.blend_enhanced_units(base_val, base, mult)
    except Exception:
        return base


def _safe_linear(iface, bl_key, pr_key):
    try:
        from .xlsm_exporter import classify_linear_net
        return classify_linear_net(iface, bl_key, pr_key)
    except Exception:
        return [], [], []


def _safe_trees(iface):
    try:
        from .xlsm_exporter import classify_trees_net
        return classify_trees_net(iface)
    except Exception:
        return [], [], []


def habitat_figures(iface, phase):
    """Return {sub_habitat_type: {"size": float, "units": float, "unit": "ha"|"km"}}
    for *phase* ('Baseline' or 'Proposed'), aggregated per habitat the same way
    the Summary tab computes its totals. Returns {} on any failure.

    Baseline map → baseline-state figures.
    Proposed map → proposed-state figures: retained habitat at its baseline
    rate, created/enhanced habitat at its proposed rate (temporal + difficulty
    multipliers applied), mirroring the Summary tab's indicative units.
    """
    try:
        from .xlsm_exporter import classify_area_net
        try:
            from . import bng_multipliers as BNGM
        except Exception:
            BNGM = None

        baseline = (phase == "Baseline")
        figs = {}

        bl_area, cr_area, en_area    = classify_area_net(iface)
        bl_hedge, cr_hedge, en_hedge = _safe_linear(iface, "baseline_hedgerow",
                                                    "proposed_hedgerow")
        bl_water, cr_water, en_water = _safe_linear(iface, "baseline_water",
                                                    "proposed_water")
        bl_tree, cr_tree, _en_tree   = _safe_trees(iface)

        if baseline:
            for r in bl_area:
                sub = r.get("sub_habitat_type", "Unknown")
                ha  = float(r.get("area_ha") or 0.0)
                u   = ha * _d(sub) * _c(r.get("condition", "")) * \
                    _s(r.get("strategic_significance", ""))
                _add(figs, sub, ha, u, "ha")
            for r in bl_hedge:
                sub = r.get("sub_habitat_type", "Unknown")
                km  = float(r.get("length_km") or 0.0)
                u   = km * _d(sub) * _c(r.get("condition", "")) * \
                    _s(r.get("strategic_significance", ""))
                _add(figs, sub, km, u, "km")
            for r in bl_water:
                sub = r.get("sub_habitat_type", "Unknown")
                km  = float(r.get("length_km") or 0.0)
                u   = km * _d(sub) * _c(r.get("condition", "")) * \
                    _s(r.get("strategic_significance", "")) * _water_enc(r, BNGM)
                _add(figs, sub, km, u, "km")
            for r in bl_tree:
                sub  = r.get("sub_habitat_type", "Unknown")
                size = TREE_SIZE_AREAS.get(tree_size_from_sub_type(sub), 0.0)
                u    = size * _TREE_DIST * _c(r.get("condition", "")) * \
                    _s(r.get("strategic_significance", ""))
                _add(figs, sub, size, u, "ha")
        else:
            _proposed_area(figs, bl_area, cr_area, en_area, BNGM)
            _proposed_linear(figs, bl_hedge, cr_hedge, en_hedge, BNGM)
            _proposed_linear(figs, bl_water, cr_water, en_water, BNGM, water=True)
            _proposed_trees(figs, bl_tree, cr_tree, BNGM)

        return {k: v for k, v in figs.items()
                if v["size"] > 1e-9 or v["units"] > 1e-9}
    except Exception:
        return {}


def _proposed_area(figs, bl_rows, cr_rows, en_rows, BNGM):
    for r in bl_rows:
        retained = float(r.get("area_retained_ha") or 0.0)
        if retained > 1e-9:
            sub = r.get("sub_habitat_type", "Unknown")
            u   = retained * _d(sub) * _c(r.get("condition", "")) * \
                _s(r.get("strategic_significance", ""))
            _add(figs, sub, retained, u, "ha")
    for r in en_rows:
        sub   = r.get("prop_sub_habitat_type") or r.get("sub_habitat_type", "Unknown")
        cond  = r.get("prop_condition") or r.get("condition", "")
        start = r.get("condition", "")
        size  = float(r.get("area_ha") or 0.0)
        strat = r.get("strategic_significance", "")
        base  = size * _d(sub) * _c(cond) * _s(strat)
        # Baseline value of the enhanced ground (kept whole; only uplift discounted)
        base_val = size * _d(r.get("sub_habitat_type", "")) * _c(start) * _s(strat)
        _add(figs, sub, size,
             _prop_mult(base, BNGM, "enhanced", sub, cond, start, base_val,
                        base_sub=r.get("sub_habitat_type", "")), "ha")
    for r in cr_rows:
        sub  = r.get("sub_habitat_type", "Unknown")
        cond = r.get("condition", "")
        size = float(r.get("area_ha") or 0.0)
        base = size * _d(sub) * _c(cond) * _s(r.get("strategic_significance", ""))
        _add(figs, sub, size, _prop_mult(base, BNGM, "created", sub, cond, None), "ha")


def _proposed_linear(figs, bl_rows, cr_rows, en_rows, BNGM, water=False):
    for r in bl_rows:
        retained = float(r.get("length_retained_km") or 0.0)
        if retained > 1e-9:
            sub = r.get("sub_habitat_type", "Unknown")
            u   = retained * _d(sub) * _c(r.get("condition", "")) * \
                _s(r.get("strategic_significance", ""))
            if water:
                u *= _water_enc(r, BNGM)
            _add(figs, sub, retained, u, "km")
    for r in en_rows:
        sub   = r.get("prop_sub_habitat_type") or r.get("sub_habitat_type", "Unknown")
        cond  = r.get("prop_condition") or r.get("condition", "")
        start = r.get("condition", "")
        size  = float(r.get("length_km") or 0.0)
        strat = r.get("strategic_significance", "")
        base  = size * _d(sub) * _c(cond) * _s(strat)
        base_val = size * _d(r.get("sub_habitat_type", "")) * _c(start) * _s(strat)
        u = _prop_mult(base, BNGM, "enhanced", sub, cond, start, base_val,
                       base_sub=r.get("sub_habitat_type", ""))
        if water:
            u *= _water_enc(r, BNGM, proposed=True)
        _add(figs, sub, size, u, "km")
    for r in cr_rows:
        sub  = r.get("sub_habitat_type", "Unknown")
        cond = r.get("condition", "")
        size = float(r.get("length_km") or 0.0)
        base = size * _d(sub) * _c(cond) * _s(r.get("strategic_significance", ""))
        u = _prop_mult(base, BNGM, "created", sub, cond, None)
        if water:
            u *= _water_enc(r, BNGM)
        _add(figs, sub, size, u, "km")


def _proposed_trees(figs, bl_rows, cr_rows, BNGM):
    for r in bl_rows:
        if r.get("retention") == "Lost":
            continue
        sub  = r.get("sub_habitat_type", "Unknown")
        size = TREE_SIZE_AREAS.get(tree_size_from_sub_type(sub), 0.0)
        cond = r.get("condition", "")
        base = size * _TREE_DIST * _c(cond) * _s(r.get("strategic_significance", ""))
        if r.get("retention") == "Enhanced":
            # This legend path lacks the baseline twin's condition, so the uplift
            # can't be isolated; keep the baseline value whole (base_val=base).
            u = _prop_mult(base, BNGM, "enhanced", sub, cond, cond, base)
        else:
            u = base
        _add(figs, sub, size, u, "ha")
    for r in cr_rows:
        sub  = r.get("sub_habitat_type", "Unknown")
        size = TREE_SIZE_AREAS.get(tree_size_from_sub_type(sub), 0.0)
        cond = r.get("condition", "")
        base = size * _TREE_DIST * _c(cond) * _s(r.get("strategic_significance", ""))
        _add(figs, sub, size, _prop_mult(base, BNGM, "created", sub, cond, None), "ha")


def label_suffix(figs, sub, show_area=True, show_units=True):
    """Figure string for a habitat's legend line, honouring which parts are
    enabled. Examples: "23.714 ha · 47.43 u", "23.714 ha", "47.43 u".
    Empty string if there's no figure or nothing is enabled."""
    entry = figs.get(sub)
    if not entry:
        return ""
    unit  = entry.get("unit", "ha")
    size  = entry.get("size", 0.0)
    units = entry.get("units", 0.0)
    parts = []
    if show_area:
        parts.append(f"{size:,.3f} {unit}")
    if show_units:
        parts.append(f"{units:,.2f} u")
    return " · ".join(parts)


def direct_figures(layers):
    """Per-habitat area/length + BNG units read STRAIGHT from the given map
    layers, grouped by sub_habitat_type.

    This is a robust fallback for tagging the map legend when the net-difference
    classifier returns nothing (e.g. a baseline-only or proposed-only project,
    or a transient state after reopening a saved file). It reports each
    habitat's own units (qty x distinctiveness x condition x strategic
    significance), which equals the metric's figure for baseline/retained and
    created habitats at base rate. Returns {} on any failure.
    """
    try:
        from qgis.core import (QgsProject, QgsDistanceArea, QgsWkbTypes)
    except Exception:
        return {}

    project = QgsProject.instance()
    figs = {}
    for lyr in (layers or []):
        try:
            if lyr is None or not lyr.isValid():
                continue
            flds = lyr.fields()
            if flds.indexOf("sub_habitat_type") < 0:
                continue
            gtype = QgsWkbTypes.geometryType(lyr.wkbType())
            is_point = gtype == QgsWkbTypes.GeometryType.PointGeometry
            is_line  = gtype == QgsWkbTypes.GeometryType.LineGeometry
            is_water = is_line and "watercourse" in (lyr.name() or "").lower()
            calc = QgsDistanceArea()
            try:
                calc.setSourceCrs(lyr.crs(), project.transformContext())
                calc.setEllipsoid(measurement_ellipsoid())
            except Exception:
                pass
            for feat in lyr.getFeatures():
                sub = feat["sub_habitat_type"]
                sub = ("" if sub is None else str(sub)).strip()
                if not sub:
                    continue
                cond  = _attr(feat, "condition")
                strat = _attr(feat, "strategic_significance")
                geom  = feat.geometry()
                if is_point:
                    size = TREE_SIZE_AREAS.get(tree_size_from_sub_type(sub), 0.0)
                    unit = "ha"
                    u = size * _TREE_DIST * _c(cond) * _s(strat)
                elif is_line:
                    try:
                        size = calc.measureLength(geom) / 1000.0
                    except Exception:
                        size = 0.0
                    unit = "km"
                    u = size * _d(sub) * _c(cond) * _s(strat)
                    if is_water:
                        try:
                            from . import bng_multipliers as _BM
                            u *= _BM.water_encroachment_mult_fields(
                                _attr(feat, "watercourse_encroachment"),
                                _attr(feat, "riparian_encroachment_l"),
                                _attr(feat, "riparian_encroachment_r"))
                        except Exception:
                            pass
                else:
                    try:
                        size = calc.measureArea(geom) / 10000.0
                    except Exception:
                        size = 0.0
                    unit = "ha"
                    u = size * _d(sub) * _c(cond) * _s(strat)
                if size <= 0:
                    continue
                _add(figs, sub, size, u, unit)
        except Exception:
            continue
    return {k: v for k, v in figs.items()
            if v["size"] > 1e-9 or v["units"] > 1e-9}


def _attr(feat, name):
    try:
        v = feat[name]
        return "" if v is None else str(v)
    except Exception:
        return ""


def _tree_size_label(sub):
    """Build the size/location label used by the tree rules, e.g.
    "Proposed Small Urban Tree" -> "Small Urban Tree"."""
    size = tree_size_from_sub_type(sub)
    loc  = tree_location_from_sub_type(sub)
    loc_word = "Urban" if "urban" in (loc or "").lower() else "Rural"
    return f"{size} {loc_word} Tree"


def tree_legend_figures(layer, phase):
    """Area + BNG units for the trees on *layer*, ready to bake into the tree
    rule labels.

    Returns a flat dict:
      Baseline map  -> {"Small Urban Tree": {size, units, unit}, ...}
      Proposed map  -> {"Retained|Small Urban Tree": {...},
                        "Newly planted|Small Urban Tree": {...}, ...}

    Proposed units honour each tree's status: retained at base rate, enhanced
    and newly-planted (created) with the proposed temporal/difficulty
    multipliers, exactly as the Summary tab does — so 3 newly-planted trees do
    not report the same units as 3 retained trees.
    """
    try:
        from . import bng_multipliers as BNGM
    except Exception:
        BNGM = None
    figs = {}
    proposed = (phase == "Proposed")
    try:
        if layer is None or not layer.isValid():
            return figs
        if layer.fields().indexOf("sub_habitat_type") < 0:
            return figs
        for feat in layer.getFeatures():
            sub = _attr(feat, "sub_habitat_type").strip()
            if not sub:
                continue
            size_label = _tree_size_label(sub)
            area = TREE_SIZE_AREAS.get(tree_size_from_sub_type(sub), 0.0)
            cond  = _attr(feat, "condition")
            strat = _attr(feat, "strategic_significance")
            base = area * _TREE_DIST * _c(cond) * _s(strat)
            if proposed:
                ret = _attr(feat, "Retention Category") or _attr(feat, "retention")
                if ret == "Lost":
                    continue
                # The metric tables key trees by location ("Urban tree" →
                # "Individual trees - Urban tree"), not by the raw sub-type —
                # same call the Summary tab makes, so the discounts match it.
                loc = tree_location_from_sub_type(sub)
                if ret == "Created":
                    status = "Newly planted"
                    u = _prop_mult(base, BNGM, "created", loc, cond, None)
                elif ret == "Enhanced":
                    status = "Retained"
                    u = _prop_mult(base, BNGM, "enhanced", loc, cond, cond)
                else:                      # Retained / blank
                    status = "Retained"
                    u = base
                key = f"{status}|{size_label}"
            else:
                key = size_label
                u = base
            _add(figs, key, area, u, "ha")
    except Exception:
        return {}
    return {k: v for k, v in figs.items()
            if v["size"] > 1e-9 or v["units"] > 1e-9}