# enhancement_engine.py — BNG enhancement engine (rebuilt).
"""
Shared engine for the on-site and off-site Enhancements tabs.

Everything routes through the plugin's statutory-metric data so figures match
the XLSM export:

  habitat_data.DISTINCTIVENESS_SCORES   distinctiveness band score (0/2/4/6/8)
  habitat_data.CONDITION_MULTIPLIERS    Good 3 ... Poor 1
  habitat_data.HABITAT_TYPES            sub-habitat -> broad habitat group
  habitat_data.TREE_AREA_HA             individual-tree area equivalents
  bng_multipliers.strategic_multiplier  SS 1.0 / 1.1 / 1.15
  bng_multipliers.proposed_multiplier   temporal x difficulty (create/enhance)

CORE MODEL — every enhancement is a BEFORE -> AFTER delta in habitat units:

    baseline_units = area x distinct x condition x SS                 (no risk)
    target_units   = area x distinct x condition x SS x R             (R = temporal x difficulty x spatial)
    net_gain       = target_units - baseline_units

When a parcel changes habitat type its full baseline is lost (the "loss must
come into the 10%" rule), so a conversion's net is created - lost.

TRADING RULES — read from the statutory metric (Trading Summary sheets + G-2).
They are BANDED and, for Medium, depend on the BROAD HABITAT GROUP, not just
distinctiveness:

  Area module
    Very High : same specific habitat only (bespoke compensation)
    High      : same specific habitat, or trade up to Very High
    Medium    : same broad habitat group (>= Medium), OR any higher-distinctiveness habitat
    Low       : any habitat of equal-or-higher distinctiveness (any group)
    Very Low  : no requirement
  Hedgerow module
    Very High : same habitat;  High : like-for-like or better;
    Medium/Low/Very Low : same distinctiveness band or better
  Watercourse module
    Very High / High / Medium : same habitat;  Low : better distinctiveness required

Individual trees are their OWN broad group ("Individual Trees"), Medium
distinctiveness. They therefore clear Low / Very Low losses (higher
distinctiveness) and provide +10% headroom, but CANNOT clear a Medium grassland
loss (different broad group) or any High/Very High loss.

Trees and NEW hedgerows are ADDITIVE generators: their units stand alone with
no baseline subtraction (a tree's RPA area is not taken from the host habitat;
a new hedge sits in the separate hedgerow module).
"""

import math
from . import habitat_data as HD
from . import bng_multipliers as BM


# ── band / group helpers ───────────────────────────────────────────────────
_BAND_NAME = {0: "Very Low", 1: "Very Low", 2: "Low", 4: "Medium",
              6: "High", 8: "Very High"}
_BAND_ORDER = {"Very Low": 0, "Low": 1, "Medium": 2, "High": 3, "Very High": 4}
_COND_ORDER = ["Poor", "Fairly Poor", "Moderate", "Fairly Good", "Good"]


def _build_group_map():
    """sub-habitat name -> broad habitat group, from HABITAT_TYPES."""
    m = {}
    try:
        for broad, subs in HD.HABITAT_TYPES.items():
            if isinstance(subs, dict):
                for sub in subs:
                    m[sub] = broad
    except Exception:
        pass
    return m

_SUB_GROUP = _build_group_map()


def distinctiveness(habitat):
    """Distinctiveness score for a sub-habitat. Trees = Medium (4)."""
    if habitat in HD.DISTINCTIVENESS_SCORES:
        return int(HD.DISTINCTIVENESS_SCORES[habitat])
    if "tree" in (habitat or "").lower():
        return 4
    return 0


def band_name(habitat):
    return _BAND_NAME.get(distinctiveness(habitat), "?")


def broad_group(habitat):
    g = _SUB_GROUP.get(habitat)
    if g:
        return g
    if "tree" in (habitat or "").lower():
        return "Individual Trees"
    return ""


def condition_multiplier(condition):
    return float(HD.CONDITION_MULTIPLIERS.get(condition, 1.0))


def _cond_rank(condition):
    try:
        return _COND_ORDER.index(condition)
    except ValueError:
        return 0


# ── core unit maths ─────────────────────────────────────────────────────────
def baseline_units(habitat, condition, area_ha, strategic_value=""):
    return (float(area_ha) * distinctiveness(habitat)
            * condition_multiplier(condition)
            * BM.strategic_multiplier(strategic_value))


def proposed_units(change_type, habitat, condition, area_ha,
                   strategic_value="", start_condition=None, spatial_risk=1.0):
    """Post-development value with temporal x difficulty (x spatial)."""
    risk, note = BM.proposed_multiplier(
        change_type, habitat, condition, start_condition=start_condition)
    units = (float(area_ha) * distinctiveness(habitat)
             * condition_multiplier(condition)
             * BM.strategic_multiplier(strategic_value)
             * risk * float(spatial_risk))
    return units, note


# ── TRADING RULES (verified against the statutory metric) ───────────────────
_RULE_TEXT = {
    ("area", "Very High"): "Very High distinctiveness: the same habitat is required (bespoke compensation).",
    ("area", "High"):      "High distinctiveness: the same habitat is required (or a Very High habitat).",
    ("area", "Medium"):    "Medium distinctiveness: same broad habitat group, or a higher-distinctiveness habitat.",
    ("area", "Low"):       "Low distinctiveness: any habitat of equal-or-higher distinctiveness.",
    ("area", "Very Low"):  "Very Low distinctiveness: no trading constraint.",
    ("hedgerow", "Very High"): "Very High hedgerow: the same habitat is required.",
    ("hedgerow", "High"):      "High hedgerow: like-for-like or better.",
    ("hedgerow", "Medium"):    "Medium hedgerow: same distinctiveness band or better.",
    ("hedgerow", "Low"):       "Low hedgerow: same distinctiveness band or better.",
    ("hedgerow", "Very Low"):  "Very Low hedgerow: same distinctiveness band or better.",
    ("watercourse", "Very High"): "Very High watercourse: the same habitat is required.",
    ("watercourse", "High"):      "High watercourse: the same habitat is required.",
    ("watercourse", "Medium"):    "Medium watercourse: the same habitat is required.",
    ("watercourse", "Low"):       "Low watercourse: a better-distinctiveness habitat is required.",
}


def trading_rule(module, base_habitat, target_habitat):
    """Is `target_habitat` a valid replacement for a loss of `base_habitat`
    in `module`? Returns (allowed: bool, reason: str). Reason is always set,
    explaining the decision in plain English (the metric rule + the comparison).
    """
    db = distinctiveness(base_habitat)
    dt = distinctiveness(target_habitat)
    bb = _BAND_NAME.get(db, "?")
    bt = _BAND_NAME.get(dt, "?")
    same_hab = (base_habitat or "").strip().lower() == (target_habitat or "").strip().lower()
    bg, tg = broad_group(base_habitat), broad_group(target_habitat)
    rule = _RULE_TEXT.get((module, bb), "")

    if module == "area":
        if bb == "Very High":
            if same_hab:
                return True, "Same habitat replaces a Very High loss — %s" % rule
            return False, ("%s %s cannot replace it: only the same habitat (or bespoke "
                           "compensation) qualifies." % (rule, target_habitat))
        if bb == "High":
            if same_hab:
                return True, "Same habitat replaces a High loss — %s" % rule
            if dt > db:
                return True, "%s is higher distinctiveness (Very High) — valid. %s" % (target_habitat, rule)
            return False, ("%s %s is a different habitat at the same or lower band — not permitted."
                           % (rule, target_habitat))
        if bb == "Medium":
            if same_hab:
                return True, "Same habitat — valid. %s" % rule
            if tg == bg and dt >= db:
                return True, ("%s is the same broad habitat group (%s) at %s — valid. %s"
                              % (target_habitat, bg, bt, rule))
            if dt > db:
                return True, ("%s is higher distinctiveness (%s) — valid trade-up. %s"
                              % (target_habitat, bt, rule))
            if tg == bg and dt < db:
                return False, ("%s is the same group but lower distinctiveness (%s) — not permitted. %s"
                               % (target_habitat, bt, rule))
            return False, ("%s is %s but a different broad habitat group (%s, not %s) — not permitted; "
                           "use the same group or trade up. %s" % (target_habitat, bt, tg, bg, rule))
        # Low / Very Low
        if dt >= db:
            return True, "%s is equal-or-higher distinctiveness (%s) — valid. %s" % (target_habitat, bt, rule)
        return False, "%s is lower distinctiveness (%s) — trading down is not permitted. %s" % (target_habitat, bt, rule)

    if module == "hedgerow":
        if bb == "Very High":
            return (same_hab, ("Same hedgerow — valid. %s" % rule) if same_hab
                    else "%s a different hedgerow cannot replace a Very High hedgerow loss." % rule)
        if dt >= db:
            return True, "%s is equal-or-higher distinctiveness (%s) — valid. %s" % (target_habitat, bt, rule)
        return False, "%s is lower distinctiveness (%s) — not permitted. %s" % (target_habitat, bt, rule)

    if module == "watercourse":
        if bb in ("Very High", "High", "Medium"):
            return (same_hab, ("Same watercourse habitat — valid. %s" % rule) if same_hab
                    else "%s only the same habitat qualifies." % rule)
        if dt > db:
            return True, "%s is higher distinctiveness (%s) — valid. %s" % (target_habitat, bt, rule)
        return False, "%s is not higher distinctiveness — a better habitat is required. %s" % (target_habitat, rule)

    return False, "Unknown module '%s'." % module


# ── net gain per unit-of-area (or km), with the temporal-erosion flag ───────
def net_per_unit(base_hab, base_cond, tgt_hab, tgt_cond, change_type,
                 strategic_value="", spatial_risk=1.0):
    """Net units gained per ha (area) / per km (linear) for the move.
    For 'enhanced' on the SAME habitat this is a condition uplift (baseline
    retained). For a habitat change use change_type='created' (baseline lost).
    Additive generators (trees, NEW hedgerows) have their own helpers and must
    NOT pass a baseline here. Returns (net, note); note flags temporal erosion.
    """
    lost = baseline_units(base_hab, base_cond, 1.0, strategic_value) if base_hab else 0.0
    start = base_cond if (change_type == "enhanced" and base_hab) else None
    gained, gnote = proposed_units(change_type, tgt_hab, tgt_cond, 1.0,
                                   strategic_value=strategic_value,
                                   start_condition=start, spatial_risk=spatial_risk)
    if (change_type == "enhanced" and base_hab and tgt_hab != base_hab
            and gnote and "not in lookup" in gnote):
        gained, _ = proposed_units("created", tgt_hab, tgt_cond, 1.0,
                                   strategic_value=strategic_value, spatial_risk=spatial_risk)
        gnote = "modelled as habitat creation (no enhancement pathway in the metric for this change)"
    net = gained - lost
    note = ""
    if net <= 0 and gained > 0 and lost > 0:
        intrinsic = distinctiveness(tgt_hab) * condition_multiplier(tgt_cond) * BM.strategic_multiplier(strategic_value)
        if intrinsic > lost:
            note = ("the long time-to-target discount erodes this below the retained baseline "
                    "— retaining the habitat scores better than enhancing it")
    return net, note


# ── additive generators (no baseline) ───────────────────────────────────────
TREE_DEFAULT_SIZE = "Small"
TREE_DEFAULT_COND = "Poor"


def tree_area_ha(size="Small", urban=False):
    key = "%s %s Tree" % (size, "Urban" if urban else "Rural")
    return float(HD.TREE_AREA_HA.get(key, HD.TREE_AREA_HA["Small Rural Tree"]))


def tree_units(size="Small", condition="Poor", urban=False,
               strategic_value="", n=1, spatial_risk=1.0):
    """Units delivered by n created individual trees. ADDITIVE — no baseline."""
    habitat = "Urban tree" if urban else "Rural tree"
    area = tree_area_ha(size, urban) * float(n)
    units, _ = proposed_units("created", habitat, condition, area,
                              strategic_value=strategic_value, spatial_risk=spatial_risk)
    return units


def trees_needed_for(units_target, size="Small", condition="Poor", urban=False,
                     strategic_value="", spatial_risk=1.0):
    per = tree_units(size, condition, urban, strategic_value, 1, spatial_risk)
    if per <= 0:
        return None
    return int(math.ceil(units_target / per))


def hedgerow_units(habitat, condition, km, strategic_value="", spatial_risk=1.0):
    """Units delivered by km of NEW hedgerow. ADDITIVE — no area baseline."""
    units, _ = proposed_units("created", habitat, condition, float(km),
                              strategic_value=strategic_value, spatial_risk=spatial_risk)
    return units


# ── per-currency deficit decomposition ──────────────────────────────────────
def currency_key(module, habitat):
    """The 'currency' a loss must be replaced in, per the verified rules:
       area High/Very High -> the specific habitat;
       area Medium         -> (band, broad group);
       area Low/Very Low   -> the band (any habitat);
       hedgerow/watercourse-> the band (module is one broad type).
    Returns (key_tuple, human_label, rule_text).
    """
    bb = band_name(habitat)
    rule = _RULE_TEXT.get((module, bb), "")
    if module == "area":
        if bb in ("Very High", "High"):
            return (("hab", habitat), "%s (%s — same habitat)" % (habitat, bb), rule)
        if bb == "Medium":
            g = broad_group(habitat)
            return (("grp", g), "%s group, Medium (same group or trade up)" % g, rule)
        return (("band", bb), "%s distinctiveness (any equal-or-higher habitat)" % bb, rule)
    return (("band", module, bb), "%s %s (same distinctiveness or better)" % (module, bb), rule)


def currency_can_be_filled_by(module, key, target_habitat):
    """Does creating/enhancing target_habitat contribute to a deficit `key`?"""
    kind = key[0]
    if kind == "hab":
        ok, _ = trading_rule(module, key[1], target_habitat)
        return ok
    if kind == "grp":
        tg = broad_group(target_habitat)
        dt = distinctiveness(target_habitat)
        return (tg == key[1] and dt >= 4) or dt > 4
    if kind == "band" and len(key) == 2:
        need = _BAND_ORDER.get(key[1], 0)
        return _BAND_ORDER.get(band_name(target_habitat), 0) >= need
    if kind == "band" and len(key) == 3:
        need = _BAND_ORDER.get(key[2], 0)
        return _BAND_ORDER.get(band_name(target_habitat), 0) >= need
    return False


def currency_deficits(losses):
    """Bucket losses into trading 'currencies'.
    `losses`: list of dicts {module, habitat, units} (units > 0 = lost value).
    Returns list of {key, label, rule, deficit_units} sorted V.High->Low.
    """
    buckets = {}
    for L in losses:
        u = float(L.get("units") or 0.0)
        if u <= 0:
            continue
        key, label, rule = currency_key(L.get("module", "area"), L.get("habitat", ""))
        b = buckets.setdefault(key, {"key": key, "label": label, "rule": rule, "deficit_units": 0.0})
        b["deficit_units"] += u
    def _rank(d):
        k = d["key"]
        if k[0] == "hab":
            return _BAND_ORDER.get(band_name(k[1]), 0)
        if k[0] == "grp":
            return 2
        return _BAND_ORDER.get(k[-1], 0)
    return sorted(buckets.values(), key=_rank, reverse=True)


# ── enhancement options for a loss, ranked by delivered net ─────────────────
_AREA_TARGETS = [
    ("Other neutral grassland", "Good"),
    ("Mixed scrub", "Moderate"),
    ("Lowland meadows", "Good"),
    ("Lowland calcareous grassland", "Moderate"),
    ("Traditional orchards", "Moderate"),
    ("Lowland mixed deciduous woodland", "Moderate"),
]


def options_for_loss(module, base_hab, base_cond, strategic_value="", spatial_risk=1.0):
    """Ranked, annotated enhancement options that legally replace a loss of
    `base_hab` in `module`. Each: {label, target, condition, kind, valid,
    reason, net_per_unit, note}. Valid options first, ranked by net per unit.
    """
    out = []
    if base_cond and _cond_rank("Good") > _cond_rank(base_cond) and distinctiveness(base_hab) > 0:
        net, note = net_per_unit(base_hab, base_cond, base_hab, "Good", "enhanced",
                                 strategic_value, spatial_risk)
        out.append({"label": "Uplift %s to Good condition" % base_hab, "target": base_hab,
                    "condition": "Good", "kind": "uplift", "valid": net > 0,
                    "reason": "Condition uplift of the same habitat — trading rule automatically satisfied.",
                    "net_per_unit": net, "note": note})
    if module == "area":
        for hab, cond in _AREA_TARGETS:
            if hab == base_hab:
                continue
            ok, why = trading_rule("area", base_hab, hab)
            net, note = net_per_unit(base_hab, base_cond, hab, cond, "created",
                                     strategic_value, spatial_risk)
            out.append({"label": "Create %s (%s)" % (hab, cond), "target": hab,
                        "condition": cond, "kind": "convert", "valid": ok and net > 0,
                        "reason": why, "net_per_unit": net, "note": note})
    out.sort(key=lambda o: (o["valid"], o["net_per_unit"] or -1e9), reverse=True)
    return out


# ── recipes to close a per-module gap (single move or combination) ──────────
def recipes_for_module(module, target_units, uplift_parcels=None,
                       strategic_value="", spatial_risk=1.0):
    """Build recipe options to deliver `target_units` within ONE module.
    A recipe is {label, steps[], total, closes, why}; steps are
    {desc, units, kind}. Small poor trees are ALWAYS offered first.
    Negative-net moves are never offered.
    """
    target_units = max(0.0, float(target_units))
    recipes = []

    if module == "area":
        n = trees_needed_for(target_units, "Small", "Poor", False, strategic_value, spatial_risk)
        if n:
            per = tree_units("Small", "Poor", False, strategic_value, 1, spatial_risk)
            u = tree_units("Small", "Poor", False, strategic_value, n, spatial_risk)
            recipes.append({
                "label": "Small trees (poor condition)",
                "steps": [{"desc": "%d × small tree, poor condition (additive — no land taken)" % n,
                           "units": u, "kind": "tree"}],
                "total": u, "closes": u >= target_units - 1e-9,
                "why": ("Small poor trees are the standard planting unit: additive BNG units that don't "
                        "consume the land budget. Valid where the deficit is Low/Very Low distinctiveness "
                        "or pure +10%% headroom. Each small tree ≈ %.4f units." % per)})

    if module == "area" and uplift_parcels:
        steps, uplift_total = [], 0.0
        for p in uplift_parcels:
            net, _ = net_per_unit(p["habitat"], p["condition"], p["habitat"], "Good",
                                  "enhanced", strategic_value, spatial_risk)
            if net and net > 0:
                gain = net * float(p["area_ha"])
                steps.append({"desc": "Uplift %.2f ha %s: %s → Good (+%.3f/ha)"
                              % (p["area_ha"], p["habitat"], p["condition"], net),
                              "units": gain, "kind": "uplift"})
                uplift_total += gain
        if steps:
            total = uplift_total
            why = ("Condition uplift is the cheapest positive route: the parcel is retained so only the "
                   "uplift is discounted, and same-habitat uplift satisfies the trading rule automatically.")
            label = "Condition uplift"
            if total < target_units:
                rem = target_units - total
                n = trees_needed_for(rem, "Small", "Poor", False, strategic_value, spatial_risk)
                if n:
                    u = tree_units("Small", "Poor", False, strategic_value, n, spatial_risk)
                    steps = steps + [{"desc": "%d × small tree, poor condition (to finish)" % n,
                                      "units": u, "kind": "tree"}]
                    total += u
                    label = "Condition uplift + trees"
                    why += " Topped up with small poor trees to close the remainder."
            recipes.append({"label": label, "steps": steps, "total": total,
                            "closes": total >= target_units - 1e-9, "why": why})

    if module == "area":
        for hab, cond in [("Mixed scrub", "Moderate"), ("Other neutral grassland", "Good")]:
            net, _ = net_per_unit("Modified grassland", "Poor", hab, cond, "created",
                                  strategic_value, spatial_risk)
            if net and net > 0:
                ha = target_units / net
                recipes.append({
                    "label": "Create %s" % hab,
                    "steps": [{"desc": "%.2f ha new %s (%s) — %.2f units/ha delivered" % (ha, hab, cond, net),
                               "units": target_units, "kind": "create"}],
                    "total": target_units, "closes": True,
                    "why": ("%s establishes fast, so the time-to-target discount is small and it delivers "
                            "%.2f units/ha — among the most efficient new habitats. Confirm it satisfies the "
                            "trading rule for the specific loss it offsets." % (hab, net))})

    if module == "hedgerow":
        for label, hab, cond in [("species-rich native hedgerow", "Species-rich native hedgerow", "Good"),
                                 ("native hedgerow with trees", "Native hedgerow with trees", "Good")]:
            u_km = hedgerow_units(hab, cond, 1.0, strategic_value, spatial_risk)
            if u_km > 0:
                km = target_units / u_km
                recipes.append({
                    "label": "New %s" % label,
                    "steps": [{"desc": "%.2f km new %s (%s) — %.2f units/km (additive)" % (km, hab, cond, u_km),
                               "units": target_units, "kind": "hedge"}],
                    "total": target_units, "closes": True,
                    "why": ("New hedgerow is additive within the hedgerow module (no area-module baseline "
                            "subtracted). %.2f units/km delivered." % u_km)})

    if module == "watercourse":
        net, _ = net_per_unit("Ditches", "Poor", "Ditches", "Good", "enhanced",
                              strategic_value, spatial_risk)
        if net and net > 0:
            km = target_units / net
            recipes.append({
                "label": "Watercourse condition uplift",
                "steps": [{"desc": "%.2f km watercourse uplifted to Good (+%.2f/km)" % (km, net),
                           "units": target_units, "kind": "uplift"}],
                "total": target_units, "closes": True,
                "why": "Watercourses only trade like-for-like; condition uplift of the existing watercourse is the route."})

    return recipes


# ── one step of a user-built custom recipe ──────────────────────────────────
def custom_step(kind, strategic_value="", spatial_risk=1.0, **kw):
    """Compute + validate a single custom step.
    Returns {desc, units, valid, reason}.
      kind 'tree'   : size, condition, count, [urban]
      kind 'uplift' : habitat, start_condition, target_condition, area_ha
      kind 'create' : habitat, target_condition, area_ha, [loss_habitat, module]
      kind 'hedge'  : habitat, condition, km
    """
    if kind == "tree":
        size = kw.get("size", "Small"); cond = kw.get("condition", "Poor")
        urban = bool(kw.get("urban", False)); n = int(kw.get("count", 0))
        if n <= 0:
            return None
        u = tree_units(size, cond, urban, strategic_value, n, spatial_risk)
        return {"desc": "%d × %s %s tree, %s" % (n, size.lower(), "urban" if urban else "rural", cond.lower()),
                "units": u, "valid": True,
                "reason": "Additive points; valid for Low/Very Low deficits or +10% headroom."}
    if kind == "uplift":
        hab = kw["habitat"]; sc = kw["start_condition"]; tc = kw["target_condition"]
        ha = float(kw.get("area_ha", 0))
        net, note = net_per_unit(hab, sc, hab, tc, "enhanced", strategic_value, spatial_risk)
        if ha <= 0 or not net:
            return None
        valid = net > 0
        reason = "Condition uplift of the same habitat — trading rule satisfied."
        if not valid and note:
            reason = "Not worth it: " + note + "."
        return {"desc": "Uplift %.2f ha %s: %s → %s" % (ha, hab, sc, tc),
                "units": net * ha, "valid": valid, "reason": reason}
    if kind == "create":
        hab = kw["habitat"]; tc = kw["target_condition"]; ha = float(kw.get("area_ha", 0))
        loss = kw.get("loss_habitat"); module = kw.get("module", "area")
        net, _ = net_per_unit("Modified grassland", "Poor", hab, tc, "created",
                              strategic_value, spatial_risk)
        if ha <= 0 or not net:
            return None
        if loss:
            ok, why = trading_rule(module, loss, hab)
        else:
            ok, why = True, "Creation value computed; confirm it satisfies the trading rule for the loss it offsets."
        return {"desc": "Create %.2f ha %s (%s)" % (ha, hab, tc),
                "units": net * ha, "valid": ok and net > 0, "reason": why}
    if kind == "hedge":
        hab = kw.get("habitat", "Species-rich native hedgerow"); cond = kw.get("condition", "Good")
        km = float(kw.get("km", 0))
        if km <= 0:
            return None
        u = hedgerow_units(hab, cond, km, strategic_value, spatial_risk)
        return {"desc": "%.2f km %s (%s)" % (km, hab, cond), "units": u, "valid": True,
                "reason": "Additive within the hedgerow module."}
    return None


# ════════════════════════════════════════════════════════════════════════════
# ON-SITE ENHANCEMENT ENGINE v2 — retained-baseline grounded
# ────────────────────────────────────────────────────────────────────────────
# Generates correctly-priced, feasibility-gated, time-capped enhancement moves
# for the RETAINED portion of each baseline parcel. The maths is metric-correct:
#   enhancement  net/ha = (P - B) * m   (baseline kept; only the uplift discounted)
#   creation     net/ha =  P * m  - B    (baseline destroyed, then re-added at m)
# where B/P are per-ha baseline/proposed rates and m = temporal x difficulty.
# See memory 'biome-onsite-enhance-redesign' for the full design contract.
# ════════════════════════════════════════════════════════════════════════════

# Caps (agreed with the user) ------------------------------------------------
TIME_CAP_YEARS    = 20      # never suggest a move slower than this (30+ excluded)
TREE_DENSITY_SOFT = 50      # trees/ha that keep an open habitat open (<20% canopy)
TREE_DENSITY_HARD = 2000    # ceiling — woodland-density planting

# Per-move physical feasibility (curated; mirrors onsite_habitat_feasibility.xlsx).
#   ENHANCE_UPLIFT_ONLY : condition can improve in place, but NO habitat change and
#                         NO tree planting (engineered green infrastructure).
#   ENHANCE_NON_WORKABLE: excluded entirely — sealed/built, open water, bare rock,
#                         marine/intertidal, or irreplaceable/protected habitat.
#   everything else     : 'full' (uplift + change + create + trees).
ENHANCE_UPLIFT_ONLY = {
    "Biodiverse green roof", "Intensive green roof", "Other green roof",
    "Facade-bound green wall", "Ground based green wall", "Ground level planters",
    "Bioswale", "Rain garden", "Sustainable drainage system",
}
ENHANCE_NON_WORKABLE = {
    "Wood-pasture and parkland", "Dunes with sea buckthorn (H2160)",
    "Fens (upland and lowland)", "Blanket bog", "Lowland raised bog",
    "Transition mires and quaking bogs (H7140)", "Depressions on peat substrates (H7150)",
    "Oceanic valley mire[1] (D2.1)", "Purple moor grass and rush pastures", "Reedbeds",
    "Ponds (priority habitat)", "Ponds (non-priority habitat)", "Marl lakes",
    "High alkalinity lakes", "Moderate alkalinity lakes", "Low alkalinity lakes",
    "Peat lakes", "Aquifer fed naturally fluctuating water bodies", "Reservoirs",
    "Temporary lakes ponds and pools (H3170)", "Ornamental lake or pond",
    "Watercourse footprint", "Inland rock outcrop and scree habitats",
    "Other inland rock and scree", "Limestone pavement", "Calaminarian grasslands",
    "Coastal sand dunes", "Coastal vegetated shingle", "Maritime cliff and slopes",
    "Developed land; sealed surface", "Artificial unvegetated, unsealed surface",
    "Built linear features", "Actively worked sand pit quarry or open cast mine",
    "Saltmarshes and saline reedbeds", "Artificial saltmarshes and saline reedbeds",
    "Coastal lagoons", "Littoral mud", "Littoral sand", "Littoral coarse sediment",
    "Littoral mixed sediments", "Littoral muddy sand", "Littoral seagrass",
    "Littoral seagrass on peat, clay or chalk", "Littoral biogenic reefs - Mussels",
    "Littoral biogenic reefs - Sabellaria", "Features of littoral rock",
    "Features of littoral rock - on peat, clay or chalk", "Features of littoral sediment",
    "High energy littoral rock", "High energy littoral rock - on peat, clay or chalk",
    "Moderate energy littoral rock", "Moderate energy littoral rock - on peat, clay or chalk",
    "Low energy littoral rock", "Low energy littoral rock - on peat, clay or chalk",
    "Artificial littoral biogenic reefs", "Artificial littoral coarse sediment",
    "Artificial littoral mixed sediments", "Artificial littoral mud",
    "Artificial littoral muddy sand", "Artificial littoral sand",
    "Artificial littoral seagrass", "Artificial hard structures",
    "Artificial features of hard structures",
    "Artificial hard structures with integrated greening of grey infrastructure (IGGI)",
}

# Vetted higher-value targets the tab may propose CHANGING / CREATING a parcel
# into. All establish within TIME_CAP_YEARS to a useful condition (user-approved
# list). The route (enhancement vs creation) is decided per parcel by broad group.
ON_SITE_TARGETS = [
    "Mixed scrub", "Blackthorn scrub", "Hawthorn scrub", "Gorse scrub",
    "Hazel scrub", "Willow scrub", "Other neutral grassland",
    "Other lowland acid grassland", "Lowland meadows", "Lowland calcareous grassland",
    "Floodplain wetland mosaic and CFGM", "Traditional orchards",
    # Arable field margins — a fast, low-effort softening of CROPLAND only
    # (guarded below so they are not offered as a target for grassland parcels).
    "Arable field margins pollen and nectar", "Arable field margins tussocky",
    "Arable field margins cultivated annually", "Arable field margins game bird mix",
]

# Targets that only make sense applied to a cropland (arable) host.
_MARGIN_TARGETS = {
    "Arable field margins pollen and nectar", "Arable field margins tussocky",
    "Arable field margins cultivated annually", "Arable field margins game bird mix",
}

def host_feasibility(habitat):
    """'full' | 'uplift' | 'none' — what kinds of move a parcel of this habitat
    can physically host."""
    if habitat in ENHANCE_NON_WORKABLE:
        return "none"
    if habitat in ENHANCE_UPLIFT_ONLY:
        return "uplift"
    return "full"


def can_uplift(habitat):
    return host_feasibility(habitat) != "none"


def can_change(habitat):
    return host_feasibility(habitat) == "full"


def can_plant_trees(habitat):
    # Scattered / parkland-style trees suit LOW-to-MEDIUM open land (modified or
    # neutral grassland, amenity). NOT on HIGH / Very-High open habitats (meadows,
    # heathland, calcareous grassland) where trees shade out and degrade the
    # interest you're retaining; NOT into woodland (already wooded); and NOT on
    # cropland on its own — trees on arable only make sense COMBINED with
    # converting the ground beneath to grassland (the solver builds that combo).
    return (host_feasibility(habitat) == "full"
            and broad_group(habitat) not in ("Woodland", "Cropland")
            and distinctiveness(habitat) <= 4)


def _years_int(years):
    """Normalise a years cell ('30+', int, None) to an int (30 for '30+') or None."""
    if years is None:
        return None
    if isinstance(years, str):
        s = years.strip()
        if s.startswith("30"):
            return 30
        return int(s) if s.isdigit() else None
    try:
        return int(years)
    except (TypeError, ValueError):
        return None


def move_value(route, base_hab, base_cond, tgt_hab, tgt_cond,
               strategic_value="", spatial_risk=1.0):
    """Metric-correct NET units per ha for one on-site move, with its temporal x
    difficulty multiplier and years-to-target.

        enhancement : net = (P - B) * m      (baseline retained; blend rule)
        creation    : net =  P * m  -  B     (baseline destroyed, then re-added)

    Returns (net_per_ha, m, years, note). Differs from net_per_unit(), which
    treats enhancement like creation (P*m - B) and so under-counts it."""
    ss = BM.strategic_multiplier(strategic_value)
    B = distinctiveness(base_hab) * condition_multiplier(base_cond) * ss
    P = distinctiveness(tgt_hab) * condition_multiplier(tgt_cond) * ss
    if route == "enhancement":
        baseline_habitat = base_hab if tgt_hab != base_hab else None
        m, note = BM.proposed_multiplier("enhanced", tgt_hab, tgt_cond,
                                         start_condition=base_cond,
                                         baseline_habitat=baseline_habitat)
        years = BM.years_to_target("enhanced", tgt_hab, tgt_cond,
                                   start_condition=base_cond,
                                   baseline_habitat=baseline_habitat)
        net = (P - B) * m * float(spatial_risk)
    else:  # creation
        m, note = BM.proposed_multiplier("created", tgt_hab, tgt_cond)
        years = BM.years_to_target("created", tgt_hab, tgt_cond)
        net = (P * m - B) * float(spatial_risk)
    return net, m, years, note


def condition_efficiency_note(route, base_hab, base_cond, tgt_hab, tgt_cond,
                              strategic_value="", spatial_risk=1.0):
    """Explain a below-Good target condition that is actually the unit-OPTIMUM:
    returns a note when every higher, assessable condition would deliver FEWER
    units — the metric's time-to-target discount outweighing the extra habitat
    value (the 'aim lower for more' effect, e.g. Fairly Poor orchard creation or
    Fairly Good heathland enhancement). Returns None when the chosen condition
    isn't below Good, when a higher condition genuinely delivers more (it's just
    time-capped), or when no higher condition is assessable."""
    order = ["Poor", "Fairly Poor", "Moderate", "Fairly Good", "Good"]
    if tgt_cond not in order or tgt_cond == "Good":
        return None
    base_net, _m, _y, _n = move_value(route, base_hab, base_cond, tgt_hab, tgt_cond,
                                      strategic_value, spatial_risk)
    if base_net is None:
        return None
    compared = False
    for c in order[order.index(tgt_cond) + 1:]:
        net, _m2, yrs, _n2 = move_value(route, base_hab, base_cond, tgt_hab, c,
                                        strategic_value, spatial_risk)
        if net is None or _years_int(yrs) is None:
            continue                        # no metric pathway for this higher condition
        compared = True
        if net > base_net + 1e-9:
            return None                     # a higher condition really does deliver more
    if not compared:
        return None
    return ("%s is the most unit-efficient target here: reaching a higher condition takes long "
            "enough that the time-to-target discount outweighs the extra habitat value." % tgt_cond)


def moves_for_parcel(parcel, time_cap_years=TIME_CAP_YEARS, strategic_value=None,
                     spatial_risk=1.0, tree_density=TREE_DENSITY_SOFT,
                     tree_size="Small", tree_condition="Poor", tree_density_auto=True):
    """Every correctly-priced, feasibility-gated, time-capped move available on
    ONE retained parcel.

    `parcel` = {habitat, condition, area_ha, [broad], [strategic]}. Returns move
    dicts ranked by net units/ha (best first). Move kinds:
      'uplift'  — condition uplift of the same habitat (enhancement)
      'change'  — to a higher-distinctiveness habitat in the same broad group
                  (enhancement: baseline kept)
      'create'  — to a higher-value habitat in a different broad group (creation:
                  baseline lost then re-added — only from a low-value baseline)
      'tree'    — additive small-tree planting, density-capped (no habitat change)
    """
    hab  = parcel.get("habitat", "")
    cond = (parcel.get("condition") or "").strip()
    area = float(parcel.get("area_ha") or 0.0)
    # Use the PLUGIN broad-group consistently (the parcel's 'broad' from the data
    # is the metric broad name — e.g. 'Heathland and shrub' — which would not
    # match broad_group(target)='Heathland & Shrub' for same-group detection).
    broad = broad_group(hab)
    ss = strategic_value if strategic_value is not None else parcel.get("strategic", "")
    feas = host_feasibility(hab)
    moves = []
    if feas == "none" or area <= 0:
        return moves

    def _capped(years):
        yi = _years_int(years)
        return yi is not None and yi <= time_cap_years

    # (a) Condition uplift of the SAME habitat — always an enhancement ----------
    cr = _cond_rank(cond)
    for tcond in _COND_ORDER:
        if _cond_rank(tcond) <= cr:
            continue
        net, _m, years, _n = move_value("enhancement", hab, cond, hab, tcond, ss, spatial_risk)
        if net <= 1e-9 or not _capped(years):
            continue
        moves.append({
            "kind": "uplift", "route": "enhancement", "target": hab,
            "target_condition": tcond, "target_distinctiveness": distinctiveness(hab),
            "target_broad": broad, "net_per_ha": net, "years": years, "strategic": ss,
            "difficulty": BM.difficulty_enhancement(hab), "feasible": True,
            "label": "Uplift %s to %s condition" % (hab, tcond),
            "note": "Condition uplift of the retained habitat — baseline value kept, only the uplift discounted.",
        })

    # (b)/(c) Habitat change — same group => enhancement, else creation ---------
    if feas == "full":
        for tgt in ON_SITE_TARGETS:
            if tgt == hab or distinctiveness(tgt) <= distinctiveness(hab):
                continue
            if tgt in _MARGIN_TARGETS and broad != "Cropland":
                continue                            # margins only soften arable
            same_group = (broad_group(tgt) == broad)
            # include "Condition Assessment N/A" — the only state some targets
            # (e.g. arable field margins) are scored at.
            for tcond in _COND_ORDER + ["Condition Assessment N/A"]:
                if same_group:
                    if BM.area_enhancement_allowed(hab, cond, tgt, tcond):
                        route = "enhancement"
                    elif distinctiveness(hab) <= 4:
                        route = "creation"          # same-group fallback (rare)
                    else:
                        continue
                else:
                    if distinctiveness(hab) <= 4:
                        route = "creation"
                    else:
                        continue                    # never destroy a high-value baseline
                net, _m, years, _n = move_value(route, hab, cond, tgt, tcond, ss, spatial_risk)
                if net <= 1e-9 or not _capped(years):
                    continue
                kind = "change" if route == "enhancement" else "create"
                mvd = {
                    "kind": kind, "route": route, "target": tgt,
                    "target_condition": tcond, "target_distinctiveness": distinctiveness(tgt),
                    "target_broad": broad_group(tgt), "net_per_ha": net, "years": years, "strategic": ss,
                    "difficulty": (BM.difficulty_enhancement(tgt) if route == "enhancement"
                                   else BM.difficulty_creation(tgt)),
                    "feasible": True,
                    "label": "%s %s (%s)" % ("Convert to" if route == "creation" else "Change to",
                                             tgt, tcond),
                    "note": ("Same broad group — enhancement: baseline value kept, only the uplift discounted."
                             if route == "enhancement" else
                             "Different broad group — creation: the parcel's baseline is lost in full then the "
                             "new habitat added back with a time-to-target discount; worthwhile only from a "
                             "low-value baseline."),
                }
                # Footnote (off by default): for a same-group ENHANCEMENT change,
                # the metric's difficulty tables can make the CREATION route score
                # marginally higher (it rates 'enhance into X' harder than 'create
                # X'). We keep it as enhancement (expected; preserves the baseline)
                # but surface the figure so the asymmetry is visible.
                if kind == "change":
                    alt_net, _am, _ay, _an = move_value("creation", hab, cond, tgt, tcond, ss, spatial_risk)
                    if alt_net > net + 1e-9:
                        mvd["alt_creation_net"] = alt_net
                        mvd["alt_note"] = ("Creation would score +%.2f u/ha here — but only because the metric "
                                           "rates enhancing into this habitat as harder than creating it (a "
                                           "difficulty-table asymmetry). Kept as enhancement: it preserves the "
                                           "existing baseline." % (alt_net - net))
                moves.append(mvd)

    # (d) Tree planting — additive, at the chosen size / condition / density.
    # Trees are point features, so a tree option is ALWAYS offered where planting
    # is possible and at least one tree fits at the woodland ceiling; the density
    # MOULDS to how many trees are needed (see _allocate). `tree_hard_cap` is the
    # physical ceiling (floor(hard-density x area)); `density`/`max_trees` are only
    # the by-parcel DISPLAY defaults (chosen density, or moulded up to show >=1).
    if can_plant_trees(hab):
        per = tree_units(tree_size, tree_condition, urban=False, strategic_value=ss,
                         n=1, spatial_risk=spatial_risk)
        # density_chosen is the FLOOR the solver plants at. In AUTO it's the soft
        # scatter rate (the solver moulds up from it to hit the target, capped at
        # hard); in MANUAL the user's density is instead a hard CAP on how many
        # trees the parcel can hold (see _deliver_trees).
        chosen = float(TREE_DENSITY_SOFT) if tree_density_auto else float(tree_density)
        hard_cap = int(math.floor(TREE_DENSITY_HARD * area))
        yrs = BM.years_to_target("created", "Rural tree", tree_condition)
        if per > 0 and hard_cap >= 1:
            n_disp = int(math.floor(chosen * area))
            if n_disp >= 1:
                disp_trees, disp_density = n_disp, chosen
            else:                                   # parcel too small for 1 tree at chosen density
                disp_trees, disp_density = 1, (1.0 / area if area > 0 else chosen)
            disp_density = min(disp_density, float(TREE_DENSITY_HARD))
            moves.append({
                "kind": "tree", "route": "additive", "target": "Rural tree",
                "target_condition": tree_condition, "target_distinctiveness": 4,
                "target_broad": "Individual Trees", "per_tree_units": per, "strategic": ss,
                "density": disp_density, "density_chosen": chosen, "density_auto": tree_density_auto,
                "density_hard": TREE_DENSITY_HARD, "tree_hard_cap": hard_cap,
                "dense_warn": disp_density > TREE_DENSITY_SOFT + 1e-9,
                "tree_size": tree_size, "tree_condition": tree_condition,
                "max_trees": disp_trees, "units": per * disp_trees,
                "net_per_ha": per * chosen, "years": (yrs if yrs is not None else 10),
                "difficulty": 1.0, "feasible": True,
                "label": "Plant %s trees on %s" % (tree_size.lower(), hab),
                "note": ("Additive units that don't change the habitat. Scattered at ~%d/ha keeps it open; "
                         "above that it trends toward woodland (%d/ha ceiling)."
                         % (TREE_DENSITY_SOFT, TREE_DENSITY_HARD)),
            })

    moves.sort(key=lambda mv: mv.get("net_per_ha") or -1e9, reverse=True)
    return moves


# ════════════════════════════════════════════════════════════════════════════
# ON-SITE RECIPE SOLVER (Phase 2) — assemble parcel moves into ranked recipes
# ────────────────────────────────────────────────────────────────────────────
# A greedy, EXPLAINABLE heuristic (not a black-box optimiser): cover the per-loss
# trading 'currencies' (highest distinctiveness band first) with eligible gains,
# then top up to the +10% headroom, capping each parcel at its retained area.
# Produces a few labelled strategies — trees-only, single change, minimum-land
# mix, and the arable->grassland+trees combination — then ranks so the least-land
# closing recipe is recommended and single moves are preferred over combinations.
# ════════════════════════════════════════════════════════════════════════════

def _mk_step(parcel, move, area_used, units):
    s = {"module": "area",
         "parcel_habitat": parcel.get("habitat", ""),
         "parcel_condition": parcel.get("condition", ""),
         "kind": move["kind"], "route": move.get("route", ""),
         "target": move["target"], "target_condition": move.get("target_condition", ""),
         "area_ha": area_used, "units": units, "years": move.get("years"),
         "strategic": move.get("strategic", parcel.get("strategic", ""))}
    if move["kind"] == "tree":
        dens = float(move.get("density", TREE_DENSITY_SOFT))
        s["trees"] = int(round(dens * area_used))
        s["tree_size"] = move.get("tree_size", "Small")
        s["tree_condition"] = move.get("tree_condition", "Poor")
        s["density"] = dens
        s["dense_warn"] = bool(move.get("dense_warn"))
        s["label"] = ("Plant %d %s trees (%s condition) over %.2f ha of %s (~%d/ha)"
                      % (s["trees"], s["tree_size"].lower(), s["tree_condition"].lower(),
                         area_used, parcel.get("habitat", ""), int(round(dens))))
    elif move["kind"] == "uplift":
        s["label"] = ("Uplift %.2f ha %s: %s -> %s condition"
                      % (area_used, parcel.get("habitat", ""), parcel.get("condition", ""),
                         move.get("target_condition", "")))
    else:
        verb = "Convert" if move["kind"] == "create" else "Change"
        s["label"] = ("%s %.2f ha %s -> %s (%s)"
                      % (verb, area_used, parcel.get("habitat", ""), move["target"],
                         move.get("target_condition", "")))
    # Carry the arable+trees combination components so _merge_steps can re-bill
    # the tree portion as WHOLE trees (the move's net_per_ha folds a continuous
    # per*density tree rate in — see _arable_grassland_trees).
    for _k in ("_per_tree", "_create_npha"):
        if _k in move:
            s[_k] = move[_k]
    return s


def _eligible_units(steps, deficit):
    return sum(s["units"] for s in steps
               if currency_can_be_filled_by("area", deficit["key"], s["target"]))


def _deliver_trees(mv, p, pid, rem_tree_count, steps, want):
    """Place trees to deliver up to `want` units on parcel `p`. The tree budget is
    a COUNT — how many trees the parcel can physically hold at the woodland ceiling
    (floor(hard-density x area)) — NOT a ground area, so successive passes keep
    packing trees up to that ceiling instead of the first pass 'using up' the
    parcel. The effective density (trees / area) and its scatter footprint are
    resolved in _merge_steps once the parcel's total tree count is known, and a
    `dense_warn` is raised there when it exceeds the recommended scatter rate.
    In MANUAL mode the parcel's tree budget is floor(chosen-density x area) — the
    user's density caps how many trees fit — so lowering the density lowers the
    trees and the recipe falls short; in AUTO it's the woodland ceiling so the
    density moulds up to hit the target. Returns the units delivered (whole
    trees only)."""
    per = mv.get("per_tree_units") or 0.0
    area = float(p.get("area_ha") or 0.0)
    if per <= 0 or want <= 1e-9 or area <= 0:
        return 0.0
    dens_cap = (TREE_DENSITY_HARD if mv.get("density_auto", True)
                else float(mv.get("density_chosen") or TREE_DENSITY_SOFT))
    total_cap = int(math.floor(dens_cap * area))     # most trees this parcel can hold at that density
    cap = int(rem_tree_count.get(pid, total_cap))    # remaining after earlier passes
    if cap < 1:
        return 0.0
    trees = max(1, min(cap, int(math.ceil(want / per))))
    rem_tree_count[pid] = cap - trees
    u = trees * per
    steps.append({
        "module": "area", "parcel_habitat": p.get("habitat", ""),
        "parcel_condition": p.get("condition", ""), "kind": "tree", "route": "additive",
        "target": "Rural tree", "target_condition": mv.get("tree_condition", "Poor"),
        "units": u, "trees": trees, "years": mv.get("years"),
        "strategic": mv.get("strategic", p.get("strategic", "")),
        "tree_size": mv.get("tree_size", "Small"), "tree_condition": mv.get("tree_condition", "Poor"),
        "parcel_area_ha": float(p.get("area_ha") or 0.0),
        "density_chosen": float(mv.get("density_chosen") or TREE_DENSITY_SOFT),
        "area_ha": 0.0,     # scatter ground resolved in _merge_steps once total trees known
    })
    return u


def _allocate(options, deficits, target_units):
    """Greedily allocate parcel area across `options` (each {p, move}, pre-ranked
    by net/ha) to cover the currency deficits, then the headroom.

    A parcel's retained area is a shared budget across every GROUND move
    (uplift / change / create) placed on it. TREE planting draws from a SEPARATE
    per-parcel budget expressed as a tree COUNT (floor(hard-density x area)):
    trees are additive point features that sit OVER the ground, so scattering them
    neither consumes nor is consumed by a habitat conversion of the same parcel —
    the land does double duty. The count budget lets the density MOULD up to the
    woodland ceiling on a small parcel (see _deliver_trees).

    EXCEPTION: scattered trees can only coexist with a conversion whose result can
    itself host trees (open habitats — grassland, scrub). A parcel converted to an
    already-treed habitat (orchard, woodland) can't ALSO carry individual trees, so
    trees and such a conversion are mutually exclusive per parcel."""
    rem_ground, rem_tree_count = {}, {}
    for o in options:
        rem_ground.setdefault(id(o["p"]), o["p"].get("area_ha", 0.0))  # tree budget: _deliver_trees inits per mode
    steps = []
    blocked_tree_pids = set()   # converted to a non-tree-hostable habitat -> no scattered trees
    tree_pids = set()           # already carries scattered trees -> no orchard/woodland conversion

    def deliver(units_target, pred):
        got = 0.0
        for o in options:
            if got >= units_target - 1e-9:
                break
            mv = o["move"]; p = o["p"]; pid = id(p)
            if not pred(mv):
                continue
            if mv["kind"] == "tree":
                if pid in blocked_tree_pids:
                    continue
                u = _deliver_trees(mv, p, pid, rem_tree_count, steps, units_target - got)
                if u > 0:
                    tree_pids.add(pid)
                got += u
                continue
            # A conversion to a habitat that can't host scattered trees is incompatible
            # with trees on the same parcel — skip it if trees are already there.
            blocks_trees = not can_plant_trees(mv["target"])
            if blocks_trees and pid in tree_pids:
                continue
            ra = rem_ground.get(pid, 0.0)
            npha = mv.get("net_per_ha") or 0.0
            if ra <= 1e-9 or npha <= 0:
                continue
            use = min((units_target - got) / npha, ra)
            if use <= 1e-9:
                continue
            rem_ground[pid] = ra - use
            u = use * npha
            got += u
            steps.append(_mk_step(p, mv, use, u))
            if blocks_trees:
                blocked_tree_pids.add(pid)
        return got

    for d in deficits:
        if d["deficit_units"] <= 1e-9:
            continue
        need = d["deficit_units"] - _eligible_units(steps, d)
        if need > 1e-9:
            deliver(need, lambda mv, key=d["key"]: currency_can_be_filled_by("area", key, mv["target"]))
    delivered = sum(s["units"] for s in steps)
    if delivered < target_units - 1e-9:
        deliver(target_units - delivered, lambda mv: True)
    return steps


def _eval_steps(steps, deficits, target_units):
    total = sum(s["units"] for s in steps)
    # Trees are additive points over the ground, so they claim no dedicated land:
    # per parcel the land touched is the GREATER of the ground converted and the
    # ground the trees are scattered over (trees / density), never the sum — trees
    # stacked on a conversion of the same parcel add no extra land.
    per_parcel = {}
    for s in steps:
        key = (s.get("parcel_habitat"), s.get("parcel_condition"))
        b = per_parcel.setdefault(key, {"ground": 0.0, "tree": 0.0})
        b["tree" if s["kind"] == "tree" else "ground"] += s.get("area_ha", 0.0)
    land = sum(max(v["ground"], v["tree"]) for v in per_parcel.values())
    yrs = [_years_int(s.get("years")) for s in steps]
    max_years = max([y for y in yrs if y is not None], default=0)
    rem = [d["deficit_units"] for d in deficits]
    for s in sorted(steps, key=lambda s: distinctiveness(s["target"]), reverse=True):
        u = s["units"]
        for i, d in enumerate(deficits):
            if rem[i] <= 1e-9:
                continue
            if currency_can_be_filled_by("area", d["key"], s["target"]):
                take = min(u, rem[i]); rem[i] -= take; u -= take
                if u <= 1e-9:
                    break
    return {"total_units": total, "land_used_ha": land, "max_years": max_years,
            "covers_currencies": all(r <= 1e-6 for r in rem),
            "closes_gap": total >= target_units - 1e-6}


def _merge_steps(steps):
    """Combine steps that are the same move on the same parcel (the allocator can
    emit one for the currency portion and one for the headroom portion), summing
    area / trees / units and regenerating the label."""
    merged, index = [], {}
    for s in steps:
        key = (s.get("parcel_habitat"), s.get("parcel_condition"), s["kind"],
               s.get("route"), s.get("target"), s.get("target_condition"), str(s.get("years")))
        if key in index:
            m = index[key]
            m["area_ha"] = m.get("area_ha", 0.0) + s.get("area_ha", 0.0)
            m["units"] = m.get("units", 0.0) + s.get("units", 0.0)
            if "trees" in s:
                m["trees"] = m.get("trees", 0) + s.get("trees", 0)
        else:
            m = dict(s); index[key] = m; merged.append(m)
    for m in merged:
        a = m.get("area_ha", 0.0); hab = m.get("parcel_habitat", ""); tgt = m.get("target", "")
        if m["kind"] == "tree":
            # Resolve the effective density now the parcel's TOTAL trees are known:
            # scatter at the chosen density where the parcel is big enough, else
            # pack denser (up to the woodland ceiling) — and warn when we do.
            trees = m.get("trees", 0)
            pa = float(m.get("parcel_area_ha") or 0.0)
            chosen = float(m.get("density_chosen") or TREE_DENSITY_SOFT)
            dens = min(float(TREE_DENSITY_HARD), max(chosen, (trees / pa) if pa > 1e-9 else chosen))
            scatter = (trees / dens) if dens > 0 else 0.0
            m["area_ha"] = scatter
            m["density"] = dens
            m["dense_warn"] = dens > TREE_DENSITY_SOFT + 1e-9
            a = scatter
            m["label"] = ("Plant %d %s trees (%s condition) over %.2f ha of %s (~%d/ha)"
                          % (trees, m.get("tree_size", "Small").lower(),
                             m.get("tree_condition", "Poor").lower(), scatter, hab, int(round(dens))))
        elif m["kind"] == "uplift":
            m["label"] = ("Uplift %.2f ha %s: %s -> %s condition"
                          % (a, hab, m.get("parcel_condition", ""), m.get("target_condition", "")))
        elif m["kind"] == "create+tree":
            td = m.get("tree_density", TREE_DENSITY_SOFT)
            trees = int(round(td * a))
            m["trees"] = trees
            # Re-bill from WHOLE trees: the move's net_per_ha folded a continuous
            # per*density tree rate in, so a*net_per_ha over-/under-counts by the
            # fractional tree. units = create portion + whole trees * per-tree.
            per = float(m.get("_per_tree") or 0.0)
            cnp = float(m.get("_create_npha") or 0.0)
            if per or cnp:
                m["units"] = a * cnp + trees * per
            m["label"] = ("Convert %.2f ha %s -> %s (%s) + plant %d %s trees (%s) on the new sward"
                          % (a, hab, tgt, m.get("target_condition", ""), trees,
                             m.get("tree_size", "Small").lower(), m.get("tree_condition", "Poor").lower()))
        else:
            verb = "Convert" if m["kind"] == "create" else "Change"
            m["label"] = "%s %.2f ha %s -> %s (%s)" % (verb, a, hab, tgt, m.get("target_condition", ""))
    return merged


def _recipe(label, kind, steps, deficits, target_units, why):
    if not steps:
        return None
    steps = _merge_steps(steps)
    ev = _eval_steps(steps, deficits, target_units)
    return {"label": label, "kind": kind, "steps": steps,
            "total_units": ev["total_units"], "land_used_ha": ev["land_used_ha"],
            "max_years": ev["max_years"], "closes_gap": ev["closes_gap"],
            "covers_currencies": ev["covers_currencies"], "recommended": False, "why": why}


def _arable_grassland_trees(pm, deficits, target_units, spatial_risk=1.0,
                            tree_density=TREE_DENSITY_SOFT, tree_size="Small", tree_condition="Poor",
                            strategic_value=None):
    """Combination the user asked for: convert arable to grassland AND scatter
    trees on the new sward (trees alone are not offered on bare arable). The tree
    units are additive on top of the grassland, so the land does double duty."""
    opts = []
    for p, mv in pm:
        if broad_group(p.get("habitat", "")) != "Cropland":
            continue
        grass = [m for m in mv if m["kind"] == "create" and m["target_broad"] == "Grassland"]
        if not grass:
            continue
        best = max(grass, key=lambda m: m["net_per_ha"])
        if not can_plant_trees(best["target"]):
            continue
        per = tree_units(tree_size, tree_condition, urban=False,
                         strategic_value=(strategic_value if strategic_value is not None
                                          else p.get("strategic", "")), n=1, spatial_risk=spatial_risk)
        combo = dict(best)
        combo["_per_tree"] = per                       # per-WHOLE-tree units
        combo["_create_npha"] = best["net_per_ha"]     # create-only net/ha
        combo["net_per_ha"] = best["net_per_ha"] + per * float(tree_density)
        opts.append({"p": p, "move": combo})
    if not opts:
        return None
    opts.sort(key=lambda o: o["move"]["net_per_ha"], reverse=True)
    steps = _allocate(opts, deficits, target_units)
    if not steps:
        return None
    for s in steps:                       # _merge_steps regenerates the label with the tree count
        s["kind"] = "create+tree"
        s["tree_density"] = tree_density
        s["tree_size"] = tree_size
        s["tree_condition"] = tree_condition
    return _recipe("Convert arable to grassland + trees", "combination", steps,
                   deficits, target_units,
                   "Takes arable out of production into grassland and scatters trees on the new sward; the "
                   "trees are additive on top of the grassland units, so the same land does double duty.")


def build_recipes(parcels, target_units, losses=None, time_cap_years=TIME_CAP_YEARS,
                  spatial_risk=1.0, tree_density=TREE_DENSITY_SOFT,
                  tree_size="Small", tree_condition="Poor", strategic_value=None,
                  tree_density_auto=True):
    """Ranked recipe options to reach +10% net gain on RETAINED land.

    parcels      : enhancement_retained_parcels() output.
    target_units : net AREA units needed to reach +10% (the module gap).
    losses       : optional [{module:'area', habitat, units}] development losses,
                   so recipes respect the per-currency trading requirement.
    Returns recipes ranked best-first; the least-land closing-and-covering recipe
    is flagged ``recommended``. Each recipe:
      {label, kind, steps[], total_units, land_used_ha, max_years,
       closes_gap, covers_currencies, recommended, why}."""
    target_units = max(0.0, float(target_units))
    if target_units > 1e-9:                  # aim JUST OVER +10% so the metric's % box turns green
        target_units += max(target_units * 0.005, 0.02)
    deficits = currency_deficits(losses or [])
    pm = []
    for p in parcels:
        mv = moves_for_parcel(p, time_cap_years=time_cap_years, spatial_risk=spatial_risk,
                              tree_density=tree_density, tree_size=tree_size, tree_condition=tree_condition,
                              strategic_value=strategic_value, tree_density_auto=tree_density_auto)
        if mv:
            pm.append((p, mv))
    if not pm or target_units <= 1e-9:
        return []

    options = [{"p": p, "move": m} for p, mv in pm for m in mv]
    options.sort(key=lambda o: o["move"].get("net_per_ha") or -1e9, reverse=True)

    def _closes(r):
        return bool(r) and r["closes_gap"] and r["covers_currencies"]

    # 1. Trees only — the simplest delivery
    trees_r = _recipe(
        "Trees only", "trees",
        _allocate([o for o in options if o["move"]["kind"] == "tree"], deficits, target_units),
        deficits, target_units,
        "Simplest delivery — scattered tree planting only, no habitat change. Trees are Medium "
        "distinctiveness in the 'Individual Trees' broad group, so this clears a lost-tree (Medium) "
        "loss, any Low / Very-Low loss and the net-gain headroom — but NOT a Medium loss in a different "
        "broad group (e.g. grassland) or any High / Very-High loss.")

    # 2. Single change — the LEAST-LAND single target that actually COVERS the
    #    trading currencies (skip a higher-net/ha target that can't, e.g. scrub
    #    for a grassland-group loss).
    single_r, seen_t = None, set()
    for o in options:
        if o["move"]["kind"] not in ("change", "create"):
            continue
        t = o["move"]["target"]
        if t in seen_t:
            continue
        seen_t.add(t)
        cand = _recipe(
            "Single change — convert to %s" % t, "single",
            _allocate([oo for oo in options if oo["move"]["kind"] in ("change", "create")
                       and oo["move"]["target"] == t], deficits, target_units),
            deficits, target_units,
            "One habitat change — easiest to design and consent, and the least land of the options that "
            "satisfy the trading rule.")
        if _closes(cand):
            single_r = cand
            break

    # 3. Minimum-land mix — most unit-efficient eligible moves combined
    mix_r = _recipe("Minimum-land mix", "mix", _allocate(options, deficits, target_units),
                    deficits, target_units,
                    "The least total land — the most unit-efficient moves across your parcels, combined. "
                    "More interventions to manage.")

    # 4. Arable -> grassland + trees combination
    combo_r = _arable_grassland_trees(pm, deficits, target_units, spatial_risk, tree_density,
                                      tree_size, tree_condition, strategic_value)

    cand, seen = [], set()
    for r in (trees_r, single_r, mix_r, combo_r):
        if not r:
            continue
        sig = (round(r["land_used_ha"], 3), tuple(s["label"] for s in r["steps"]))
        if sig not in seen:
            seen.add(sig); cand.append(r)

    # Recommendation: least land is the driver; where two recipes are within ~10%
    # land of each other (a near-tie) prefer the SIMPLER delivery — fewer steps,
    # and a single change / trees over a mix or combination. This encodes 'minimum
    # area' as primary with 'single move for ease' as the tie-breaker, so a combo
    # that shaves a sliver of land never beats a single change, but a mix that
    # genuinely saves a lot of land still wins.
    def _kind_rank(r):
        return 0 if r["kind"] in ("single", "trees") else 1
    closers = [r for r in cand if _closes(r)]
    if closers:
        # Gardens are treated like any other habitat here: the On-site view only
        # runs once the user has confirmed the site is NOT within a residential
        # curtilage, so a garden parcel on the surviving land is enhanceable.
        min_land = min(r["land_used_ha"] for r in closers)
        near = [r for r in closers if r["land_used_ha"] <= min_land * 1.10 + 1e-6]
        rec = min(near, key=lambda r: (len(r["steps"]), _kind_rank(r), r["land_used_ha"]))
        rec["recommended"] = True

    cand.sort(key=lambda r: (not r.get("recommended"), not _closes(r),
                             round(r["land_used_ha"], 4), len(r["steps"]), r["max_years"]))
    return cand


def specialist_orchard_recipe(parcels, target_units, losses=None,
                              time_cap_years=TIME_CAP_YEARS, spatial_risk=1.0,
                              strategic_value=None):
    """A 'specialist choice' recipe converting eligible retained parcels to
    Traditional orchard — surfaced even though it rarely ranks top, with a
    ``why_not_top`` note explaining the ranking. Returns the recipe dict, or None
    if no retained parcel can host an orchard within the time cap."""
    target_units = max(0.0, float(target_units))
    if target_units > 1e-9:
        target_units += max(target_units * 0.005, 0.02)
    if target_units <= 1e-9:
        return None
    deficits = currency_deficits(losses or [])
    options = []
    for p in parcels:
        for m in moves_for_parcel(p, time_cap_years=time_cap_years, spatial_risk=spatial_risk,
                                  strategic_value=strategic_value):
            if m.get("target") == "Traditional orchards":
                options.append({"p": p, "move": m})
    if not options:
        return None
    options.sort(key=lambda o: o["move"].get("net_per_ha") or -1e9, reverse=True)
    best_net = float(options[0]["move"].get("net_per_ha") or 0.0)
    r = _recipe("Specialist choice — Traditional orchard", "specialist",
                _allocate(options, deficits, target_units), deficits, target_units,
                "Traditional orchard — a High-distinctiveness priority habitat, offered for "
                "cases where an orchard is specifically wanted.")
    if not r:
        return None
    r["recommended"] = False
    r["why_not_top"] = (
        "Not the least-land option: Traditional orchard is High distinctiveness (6) but slow to "
        "create — only Moderate condition is reachable within the 20-year limit (Good takes 30 "
        "years), and after the ~50%% temporal discount and creation difficulty it delivers only "
        "about %.1f units/ha (roughly half a grassland or meadow conversion), so it needs more land."
        % best_net)
    return r


# ════════════════════════════════════════════════════════════════════════════
# LINEAR MODULES (hedgerow / watercourse) — on-site, no land budget
# ────────────────────────────────────────────────────────────────────────────
# Same rules as the area module (distinctiveness x condition x strategic, with
# the module's own temporal/difficulty via proposed_multiplier), but the
# resource is LENGTH (km) on boundaries/channels, not land area — so there is no
# parcel budget and no 'by parcel' view. Recipes: uplift retained reaches,
# upgrade hedge type, or create new (additive). move_value() prices them per km.
# ════════════════════════════════════════════════════════════════════════════

LINEAR_CREATE = {
    "hedgerow": [("Species-rich native hedgerow", "Good"),
                 ("Native hedgerow with trees", "Good")],
    "watercourse": [],   # you don't dig new rivers on-site — uplift retained reaches
}


def _lin_ok(note):
    """True unless the multiplier note flags that the metric has no pathway (so
    move_value would otherwise fall back to an undiscounted, over-counted value)."""
    return not (note and any(s in note for s in
                             ("not possible", "not applied", "not in lookup")))


def _lin_recipe(label, kind, steps, total, km, target_units, why):
    return {"label": label, "kind": kind, "steps": steps, "total_units": total,
            "length_km": km, "measure": "km", "max_years": None,
            "closes_gap": total >= target_units - 1e-6, "covers_currencies": True,
            "recommended": False, "why": why}


def linear_recipes(module, target_units, features, strategic_value="", spatial_risk=1.0):
    """On-site recipes for the hedgerow / watercourse modules.

    `features` = retained reaches [{habitat, condition, km, strategic}]. No land
    budget (length on boundaries/channels), so no 'by parcel' view. Returns recipe
    dicts shaped like build_recipes (measure='km').

      Hedgerow    : CREATION is the common on-site move and is prioritised —
                    create more of the types already present, plus better
                    achievable types — with condition uplift / type upgrade of
                    retained reaches offered as secondary, grounded options.
      Watercourse : creation is not offered (not a real on-site action — you
                    don't dig new rivers); only condition uplift of retained
                    reaches."""
    target_units = max(0.0, float(target_units))
    if target_units <= 1e-9:
        return []
    target_units += max(target_units * 0.005, 0.02)   # aim just over +10% (green box)
    recipes = []
    # Strategic significance: OFF-SITE passes the chosen value; ON-SITE passes
    # None so each reach matches its OWN baseline SS, and created reaches take the
    # modal SS of the existing reaches. Always resolved to a string before
    # move_value (which can't take None).
    per_feature_ss = (strategic_value is None)

    def _reach_ss(f):
        return ((f.get("strategic", "") if per_feature_ss else strategic_value) or "")

    if per_feature_ss:
        _svals = [f.get("strategic", "") for f in features if f.get("strategic")]
        default_ss = (max(set(_svals), key=_svals.count) if _svals else "")
    else:
        default_ss = strategic_value

    # Condition uplift of retained reaches (enhancement; baseline kept).
    up_steps, up_tot, up_km = [], 0.0, 0.0
    for f in features:
        typ = f.get("habitat", ""); cond = (f.get("condition") or "").strip()
        km = float(f.get("km") or 0.0); ss = _reach_ss(f)
        if km <= 0 or not typ:
            continue
        for tc in reversed(_COND_ORDER):                 # aim high (Good) first
            if _cond_rank(tc) <= _cond_rank(cond):
                break
            net, _m, _y, note = move_value("enhancement", typ, cond, typ, tc, ss, spatial_risk)
            if net > 1e-9 and _lin_ok(note):
                up_steps.append({"label": "Uplift %.2f km %s: %s → %s" % (km, typ, cond or "—", tc),
                                 "units": net * km, "target": typ, "module": module, "kind": "uplift",
                                 "base_habitat": typ, "base_condition": cond,
                                 "target_condition": tc, "km": km, "strategic": ss})
                up_tot += net * km; up_km += km
                break
    uplift = (_lin_recipe("Uplift retained %ss" % module, "uplift", up_steps, up_tot, up_km,
                          target_units, "Improve the condition of the %ss you keep." % module)
              if up_steps else None)

    if module == "watercourse":
        if uplift:
            recipes.append(uplift)
    else:
        # Hedgerow — CREATE first: more of what's on site + better achievable types.
        cand, seen = [], set()
        for f in features:
            t = f.get("habitat", "")
            if t and t not in seen:
                seen.add(t); cand.append(t)
        for t in (c[0] for c in LINEAR_CREATE["hedgerow"]):
            if t not in seen:
                seen.add(t); cand.append(t)
        on_site = {f.get("habitat", "") for f in features}
        for tgt in cand:
            # A newly PLANTED hedge is recorded at Poor condition (as for newly
            # planted trees) — it must establish before it can score any higher.
            # Price with default_ss (the SS actually stamped on the exported B-2
            # row below), NOT the raw strategic_value param: on-site that param is
            # None (=x1.0) while the created reach is stamped with the modal reach
            # SS, so a Formal/desirable site under-counted the displayed units by
            # the strategic multiplier vs what the metric then scored.
            net, _m, _y, note = move_value("creation", "", "", tgt, "Poor", default_ss, spatial_risk)
            if net > 1e-9 and _lin_ok(note):
                km = math.ceil((target_units / net) * 1000.0) / 1000.0   # round UP to the nearest metre
                delivered = km * net
                tag = " — more of what's on site" if tgt in on_site else ""
                recipes.append(_lin_recipe("Create new %s" % tgt, "create",
                                           [{"label": "Plant %d m (%.3f km) new %s (poor condition) — %.2f units/km%s"
                                             % (round(km * 1000), km, tgt, net, tag), "units": delivered,
                                             "target": tgt, "module": "hedgerow", "kind": "create",
                                             "base_habitat": "", "base_condition": "", "target_condition": "Poor",
                                             "km": km, "strategic": default_ss}],
                                           delivered, km, target_units,
                                           "New hedgerow is additive (no baseline removed) and recorded at poor "
                                           "condition as planted; length rounded up to the nearest metre."))
        # Type upgrade of retained reaches (enhancement) — secondary.
        ug_steps, ug_tot, ug_km = [], 0.0, 0.0
        for f in features:
            typ = f.get("habitat", ""); cond = (f.get("condition") or "").strip()
            km = float(f.get("km") or 0.0); ss = _reach_ss(f)
            if km <= 0 or not typ:
                continue
            best = None
            for tgt, tc in LINEAR_CREATE["hedgerow"]:
                if distinctiveness(tgt) <= distinctiveness(typ) or not BM.hedge_enhancement_allowed(typ, tgt):
                    continue
                net, _m, _y, note = move_value("enhancement", typ, cond, tgt, tc, ss, spatial_risk)
                if net > 1e-9 and _lin_ok(note) and (best is None or net > best[2]):
                    best = (tgt, tc, net)
            if best:
                tgt, tc, net = best
                ug_steps.append({"label": "Upgrade %.2f km %s → %s (%s)" % (km, typ, tgt, tc),
                                 "units": net * km, "target": tgt, "module": "hedgerow", "kind": "upgrade",
                                 "base_habitat": typ, "base_condition": cond,
                                 "target_condition": tc, "km": km, "strategic": ss})
                ug_tot += net * km; ug_km += km
        if ug_steps:
            recipes.append(_lin_recipe("Upgrade retained hedgerows", "upgrade", ug_steps, ug_tot, ug_km,
                                       target_units, "Upgrade lower-value hedgerows to species-rich."))
        if uplift:
            recipes.append(uplift)

    # Rank: hedgerow puts creation first; then closing; then least length.
    def _key(r):
        create_first = (0 if r["kind"] == "create" else 1) if module == "hedgerow" else 0
        return (create_first, not r["closes_gap"], round(r["length_km"], 4))
    recipes.sort(key=_key)
    for r in recipes:
        if r["closes_gap"]:
            r["recommended"] = True
            break
    return recipes


# ════════════════════════════════════════════════════════════════════════════
# OFF-SITE (bespoke receptor land) — AREA is the OUTPUT, not a fixed budget
# ────────────────────────────────────────────────────────────────────────────
# On-site reads fixed parcel areas off the map. Off-site is the inverse: the user
# describes a receptor's existing habitat (habitat + condition + strategic
# significance + spatial-risk category) and asks "what is the MINIMUM area I must
# create / enhance there to deliver the gap?". Priced with the SAME move engine as
# on-site (move_value, via moves_for_parcel) so figures reconcile with the metric
# D/E/F sheets — including the spatial-risk multiplier and the receptor baseline
# that a creation destroys (creation net/ha already = P·m − B).
# ════════════════════════════════════════════════════════════════════════════

def offsite_options(base_habitat, base_condition, target_units, strategic_value="",
                    spatial_risk=1.0, tree_density=TREE_DENSITY_SOFT,
                    tree_size="Small", tree_condition="Poor", losses=None):
    """Ranked off-site AREA options for a receptor of (base_habitat,
    base_condition): each candidate move (uplift / change / create / trees) with
    the MINIMUM receptor area (ha) needed to deliver `target_units`, least area
    first. Spatial risk + strategic significance are applied; a creation nets off
    the receptor baseline lost, same as on-site. Aims just over the target so the
    metric's % box greens. Returns [] if the host can't deliver (sealed /
    irreplaceable / no viable move)."""
    target_units = max(0.0, float(target_units))
    if target_units <= 1e-9 or not base_habitat:
        return []
    target_units += max(target_units * 0.005, 0.02)      # just over +10% (green box)
    deficits = currency_deficits(losses or [])           # trading currencies the units must satisfy
    # A large synthetic parcel so every move is feasible and tree counts compute;
    # net_per_ha is area-independent, so the min-area maths stays exact.
    probe = {"habitat": base_habitat, "condition": base_condition,
             "area_ha": 1.0e6, "strategic": strategic_value}
    moves = moves_for_parcel(probe, strategic_value=strategic_value, spatial_risk=spatial_risk,
                             tree_density=tree_density, tree_size=tree_size,
                             tree_condition=tree_condition)
    opts = []
    for mv in moves:
        npha = float(mv.get("net_per_ha") or 0.0)
        if npha <= 1e-9:
            continue
        min_area = math.ceil((target_units / npha) * 10000.0) / 10000.0   # round UP to 0.0001 ha
        opt = {
            "kind": mv["kind"], "route": mv.get("route", ""), "target": mv["target"],
            "target_condition": mv.get("target_condition", ""), "net_per_ha": npha,
            "min_area_ha": min_area, "units": round(min_area * npha, 6),
            "years": mv.get("years"), "strategic": strategic_value,
            "base_habitat": base_habitat, "base_condition": base_condition,
            "spatial_risk": spatial_risk, "module": "area",
            "note": mv.get("note", ""), "label": mv.get("label", ""),
            "covers_currencies": (not deficits) or all(
                currency_can_be_filled_by("area", d["key"], mv["target"]) for d in deficits),
        }
        if mv["kind"] == "tree":
            dens = float(mv.get("density") or tree_density)
            opt["tree_density"] = int(round(dens))
            opt["tree_size"] = tree_size
            opt["tree_condition"] = tree_condition
            opt["trees"] = int(math.ceil(min_area * dens))
            # Trees are whole objects — you cannot plant a fraction of one. Bill the
            # WHOLE tree count (net_per_ha = per-tree units x density, so per-tree =
            # npha / density), NOT the continuous ground area, so the displayed
            # units equal exactly what the metric scores (trees x per-tree area).
            per_tree = (npha / dens) if dens > 0 else 0.0
            opt["units"] = round(opt["trees"] * per_tree, 6)
        opts.append(opt)
    # Least area first; on a tie prefer enhancement / change (keeps the receptor
    # baseline) over creation, and single moves over trees.
    # Currency-satisfying options first, then least area; on a tie prefer
    # enhancement / change (keeps the receptor baseline) over creation, then trees.
    _rank = {"uplift": 0, "change": 0, "create": 1, "tree": 2}
    opts.sort(key=lambda o: (not o["covers_currencies"], o["min_area_ha"], _rank.get(o["kind"], 3)))
    # One row per (move, target) — keep the least-area (best-condition) one, so the
    # list isn't three near-identical 'Lowland meadows' rows at different conditions.
    seen, deduped = set(), []
    for o in opts:
        k = (o["kind"], o["target"])
        if k not in seen:
            seen.add(k); deduped.append(o)
    return deduped


def recipe_for_target(parcels, target_units, target_habitat, target_condition=None, losses=None,
                      time_cap_years=TIME_CAP_YEARS, spatial_risk=1.0,
                      tree_density=TREE_DENSITY_SOFT, tree_size="Small", tree_condition="Poor",
                      strategic_value=None, label=None):
    """Build a single-target recipe converting eligible parcels to `target_habitat`
    at a SPECIFIC `target_condition` (best within cap if None). Used by the tab's
    per-recipe condition selector to re-price a recipe at a chosen condition.
    Returns the recipe, or None if that condition can't be delivered within cap."""
    target_units = max(0.0, float(target_units))
    if target_units > 1e-9:
        target_units += max(target_units * 0.005, 0.02)
    if target_units <= 1e-9:
        return None
    deficits = currency_deficits(losses or [])
    options = []
    for p in parcels:
        for m in moves_for_parcel(p, time_cap_years=time_cap_years, spatial_risk=spatial_risk,
                                  tree_density=tree_density, tree_size=tree_size,
                                  tree_condition=tree_condition, strategic_value=strategic_value):
            if m.get("target") != target_habitat:
                continue
            if target_condition and m.get("target_condition") != target_condition:
                continue
            options.append({"p": p, "move": m})
    if not options:
        return None
    options.sort(key=lambda o: o["move"].get("net_per_ha") or -1e9, reverse=True)
    return _recipe(label or ("Single change — convert to %s" % target_habitat), "single",
                   _allocate(options, deficits, target_units), deficits, target_units,
                   "One habitat change to %s." % target_habitat)


def achievable_conditions(parcels, target_habitat, time_cap_years=TIME_CAP_YEARS,
                          spatial_risk=1.0, strategic_value=None):
    """Conditions of `target_habitat` reachable within the time cap from these
    parcels (ordered Poor→Good) — the options for the per-recipe condition picker."""
    conds = set()
    for p in parcels:
        for m in moves_for_parcel(p, time_cap_years=time_cap_years, spatial_risk=spatial_risk,
                                  strategic_value=strategic_value):
            if m.get("target") == target_habitat and m.get("target_condition"):
                conds.add(m["target_condition"])
    order = _COND_ORDER + ["Condition Assessment N/A"]
    return [c for c in order if c in conds]


# ════════════════════════════════════════════════════════════════════════════
# REPORT PROSE — assemble copy-ready paragraphs from per-module requirement data
# ════════════════════════════════════════════════════════════════════════════

def _join_and(items):
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


_TIER_DISTINCT = {"A1": "low to medium distinctiveness", "A2": "high distinctiveness",
                  "A3": "high distinctiveness", "A4": "high distinctiveness",
                  "A5": "very high distinctiveness"}


def _credits_phrase(creds):
    """[(tier, credits)] -> 'X Tier A1 (low to medium distinctiveness) and Y Tier H
    statutory credits' (or ''). The tier encodes the distinctiveness the credits
    must be, so it is spelled out for the area tiers (H / W are self-evident)."""
    parts = []
    for t, c in creds:
        if not c or c <= 1e-9:
            continue
        d = _TIER_DISTINCT.get(t)
        parts.append("%.2f Tier %s%s" % (c, t, (" (%s)" % d) if d else ""))
    return (_join_and(parts) + " statutory credits") if parts else ""


def report_paragraphs(modules):
    """Report-ready prose from per-module requirement data (built by the tab's
    _report_modules). Each module dict: key, unit_noun ('area habitat units'),
    noun ('area habitats'), total, losses[{habitat,condition,band,units}],
    credits[(tier,c)], recommended(str|None), delivers, closes, residual,
    residual_credits[(tier,c)]. Returns {'full_gap': str, 'residual': str|None};
    the residual paragraph is produced only when ≥1 module has a recommendation."""
    if not modules:
        return {"full_gap": "The scheme achieves the mandatory 10% biodiversity net gain target; "
                            "no further biodiversity units are required.",
                "residual": None}
    multi = len(modules) > 1
    need = _join_and(["%.2f %s" % (m["total"], m["unit_noun"]) for m in modules])
    opening = ("Approximately %s are required to achieve the mandatory 10%% biodiversity net gain "
               "target." % need)

    loss_sentences = []
    for m in modules:
        clauses = []
        for L in m.get("losses", []):
            cond = (" (%s condition)" % L["condition"]) if L.get("condition") else ""
            hab = ("the " if "tree" in L["habitat"].lower() else "") + L["habitat"]
            clauses.append("at least %.2f units must be delivered as %s-distinctiveness habitat or "
                           "higher to replace the loss of %s%s"
                           % (L["units"], (L.get("band") or "").lower(), hab, cond))
        if clauses:
            lead = ("Of the %s, " % m["unit_noun"]) if multi else "Of this, "
            loss_sentences.append(lead + _join_and(clauses) + ".")

    full_credits = _credits_phrase([c for m in modules for c in m.get("credits", [])])

    full = opening
    if loss_sentences:
        full += " " + " ".join(loss_sentences)
    full += (" As this gain is not being delivered on-site, the required units should be secured "
             "off-site — through a registered habitat bank, or on bespoke off-site land first "
             "baselined by a suitably qualified ecologist — and secured for a minimum of 30 years; "
             "habitat banks located further from the development site may require the purchase of a "
             "greater number of units to achieve the same outcome.")
    if full_credits:
        full += (" Statutory biodiversity credits should only be used as a last resort; if required, "
                 "this would equate to approximately %s." % full_credits)

    residual = None
    if any(m.get("recommended") for m in modules):
        recs, leftovers, res_creds = [], [], []
        for m in modules:
            if m.get("recommended"):
                recs.append("%s, delivering approximately %.2f %s"
                            % (m["recommended"], m["delivers"], m["unit_noun"]))
                if m.get("closes"):
                    leftovers.append("the %s requirement met in full on-site" % m["noun"])
                else:
                    leftovers.append("a residual of approximately %.2f %s" % (m["residual"], m["unit_noun"]))
                    res_creds += m.get("residual_credits", [])
            else:
                leftovers.append("the full %s requirement of approximately %.2f %s outstanding "
                                 "(no on-site recipe selected)" % (m["noun"], m["total"], m["unit_noun"]))
                res_creds += m.get("credits", [])
        res = opening
        if loss_sentences:
            res += " " + " ".join(loss_sentences)
        res += (" I would recommend that the client %s; all enhancements must be secured for a minimum "
                "of 30 years." % _join_and(recs))
        res += " This would leave %s." % _join_and(leftovers)
        rcp = _credits_phrase(res_creds)
        if rcp:
            res += (" The residual should be secured off-site through a registered habitat bank or "
                    "bespoke baselined off-site land or, as a last resort, approximately %s." % rcp)
        if full_credits:
            res += (" Should the selected actions not be undertaken, the full requirement would "
                    "equate to approximately %s." % full_credits)
        residual = res
    return {"full_gap": full, "residual": residual}
