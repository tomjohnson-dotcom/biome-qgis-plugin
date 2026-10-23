# credits_engine.py — replicate the statutory metric's tier-trading credits.
"""
Reproduces the metric's "Credits Required by Tier / Module" exactly. Validated
2026-06-15 against a recalced metric across all modules and tiers (area A1–A5,
hedge H, watercourse W with per-band trading) — the Summary's "indicative only"
caveat has now been dropped.

The metric does NOT net all units into one pool. It computes, per SPECIFIC
habitat, the project-wide unit change (G-2 'Habitat groups'), then runs a
distinctiveness-tier TRADING cascade (Trading Summary sheets) where unmet losses
in a band can only be covered by gains in the SAME band or HIGHER (cascading
down), with the band rule depending on the broad habitat group for Medium. The
residual unmet losses, plus the +10% headroom, become per-tier credits
(Credits calculations), credits = shortfall x 2.

This module builds that in verifiable pieces:
  1. net_by_habitat(...)  -> per specific habitat: baseline units, post units,
     net change, band, broad group.  (verify against metric G-2)
  2. band_summary(...)    -> per distinctiveness band: gains / losses / net.
  3. tier_credits(...)    -> FIRST-CUT cascade -> per-tier shortfall + credits.
     (to be calibrated against the metric's Credits calc; see debug log.)

Units are computed the SAME way the Summary does (see bng_multipliers /
enhancement_engine), so the credits and the headline figures stay consistent.
"""
from . import bng_multipliers as BM
from . import enhancement_engine as ENG


_BANDS_HIGH_TO_LOW = ["Very High", "High", "Medium", "Low", "Very Low"]


def _u(area, hab, cond, strat):
    """Baseline-rate units = area x distinctiveness x condition x strategic."""
    return (float(area or 0.0) * ENG.distinctiveness(hab)
            * ENG.condition_multiplier(cond)
            * BM.strategic_multiplier(strat or ""))


def _post_units_enhanced(area, bl_hab, bl_cond, pr_hab, pr_cond, strat):
    """Post-intervention units for an ENHANCED parcel: baseline value kept whole
    + uplift discounted (matches the Summary)."""
    base_val = _u(area, bl_hab, bl_cond, strat)
    prop_val = _u(area, pr_hab, pr_cond, strat)
    mult, _ = BM.proposed_multiplier("enhanced", pr_hab, pr_cond,
                                     start_condition=bl_cond,
                                     baseline_habitat=bl_hab)
    return BM.blend_enhanced_units(base_val, prop_val, mult)


def _post_units_created(area, hab, cond, strat):
    base = _u(area, hab, cond, strat)
    mult, _ = BM.proposed_multiplier("created", hab, cond)
    return base * mult


def net_by_habitat(bl_rows, cr_rows, en_rows, area_key="area_ha", water=False):
    """Per SPECIFIC habitat: baseline units, post-intervention units, net change.

    bl_rows  : baseline groups  {sub_habitat_type, condition, <area_key>,
               area_retained_ha/length_retained_km, ...enhanced..., strategic_significance}
    cr_rows  : created groups   {sub_habitat_type, condition, <area_key>, strategic_significance}
    en_rows  : enhancement targets {sub_habitat_type(baseline), condition(baseline),
               prop_sub_habitat_type, prop_condition, <area_key>, strategic_significance}

    water    : when True the rows are watercourse reaches, so every parcel's
               units are scaled by the metric's channel x banks encroachment
               multiplier (a reach with unset/invalid encroachment scores zero).

    Returns {habitat: {"baseline": u, "post": u, "net": u, "dist": d,
                       "band": b, "group": g}}.
    """
    ret_key = ("area_retained_ha" if area_key == "area_ha" else "length_retained_km")
    try:
        from .habitat_data import IRREPLACEABLE_SUBTYPES as _IRR
    except Exception:
        _IRR = set()

    def _em(row, proposed=False):
        """Encroachment multiplier for a watercourse row (1.0 for non-water).
        proposed=True uses the reach's post-intervention encroachment (used for
        enhancement rows, whose prop_* encroachment may differ from baseline)."""
        if not water:
            return 1.0
        return BM.water_row_encroachment_mult(row, proposed=proposed)

    acc = {}

    def slot(hab):
        return acc.setdefault(hab, {
            "baseline": 0.0, "post": 0.0, "net": 0.0,
            "dist": ENG.distinctiveness(hab), "band": ENG.band_name(hab),
            "group": ENG.broad_group(hab)})

    # Baseline: every baseline parcel contributes its full baseline value as a
    # LOSS of that habitat; the RETAINED portion comes back as post value.
    for r in bl_rows:
        hab = r.get("sub_habitat_type", "")
        if not hab or hab in _IRR:
            continue                          # irreplaceable -> no units generated
        cond = r.get("condition", ""); strat = r.get("strategic_significance", "")
        area = float(r.get(area_key) or 0.0)
        ret = float(r.get(ret_key) or 0.0)
        em = _em(r)
        s = slot(hab)
        s["baseline"] += _u(area, hab, cond, strat) * em
        s["post"]     += _u(ret,  hab, cond, strat) * em   # retained kept at baseline rate

    # Enhancement: post value lands on the PROPOSED habitat.
    for e in en_rows:
        bl_hab = e.get("sub_habitat_type", ""); bl_cond = e.get("condition", "")
        pr_hab = e.get("prop_sub_habitat_type") or bl_hab
        pr_cond = e.get("prop_condition") or bl_cond
        if pr_hab in _IRR:
            continue
        strat = e.get("strategic_significance", "")
        area = float(e.get(area_key) or 0.0)
        slot(pr_hab)["post"] += _post_units_enhanced(area, bl_hab, bl_cond,
                                                     pr_hab, pr_cond, strat) * _em(e, proposed=True)

    # Creation: post value on the created habitat.
    for c in cr_rows:
        hab = c.get("sub_habitat_type", "")
        if not hab or hab in _IRR:
            continue
        cond = c.get("condition", ""); strat = c.get("strategic_significance", "")
        area = float(c.get(area_key) or 0.0)
        slot(hab)["post"] += _post_units_created(area, hab, cond, strat) * _em(c)

    for hab, s in acc.items():
        s["net"] = round(s["post"] - s["baseline"], 6)
    return acc


def band_summary(nets):
    """Aggregate per-habitat nets into distinctiveness bands."""
    out = {b: {"gain": 0.0, "loss": 0.0, "net": 0.0} for b in _BANDS_HIGH_TO_LOW}
    for hab, s in nets.items():
        b = s["band"]
        if b not in out:
            continue
        if s["net"] >= 0:
            out[b]["gain"] += s["net"]
        else:
            out[b]["loss"] += -s["net"]
        out[b]["net"] += s["net"]
    return out


# ── What to buy, and at what distinctiveness (Trading Summary sheets) ────────
# The scope a LOSS must be replaced within — and therefore what a surplus is
# allowed to cancel before the high-to-low cascade runs:
#   "habitat" — the same specific habitat; a surplus elsewhere in the same band
#               does NOT cancel the loss.
#   "group"   — any habitat of that band in the same BROAD habitat group.
#   "band"    — any habitat in the same distinctiveness band (no group or habitat
#               condition). Hedgerow Medium: "Same distinctiveness band or better".
#   omitted   — any habitat of that band or higher, so the cascade settles it
#               and no band-specific floor survives.
_TRADE_SCOPE = {
    "area":        {"Very High": "habitat", "High": "habitat", "Medium": "group"},
    # Hedgerow Medium needs its own floor: without it a Medium loss fell through
    # to the free row, which only demands _FREE_BAND ("Low") — so a Low hedge
    # "satisfied" a Medium loss. The live metric rejects exactly that
    # (verified 2026-08-03: B-2, Native hedgerow vs Species-rich native
    # hedgerow → "No - check trading summary").
    "hedgerow":    {"Very High": "habitat", "High": "habitat", "Medium": "band"},
    "watercourse": {"Very High": "habitat", "High": "habitat", "Medium": "habitat"},
}

# Distinctiveness a free-choice (cascade-settled) replacement must still reach.
# Watercourse Low losses are the odd one out: the metric demands BETTER, not equal.
_FREE_BAND = {"area": "Low", "hedgerow": "Low", "watercourse": "Medium"}

# Bands whose floor a surplus from any STRICTLY HIGHER band may settle ("trade
# up"). Source: Defra trading rules, both limbs confirmed against the live
# metric 2026-08-03.
#   area  Medium : "Same broad habitat OR A HIGHER DISTINCTIVENESS HABITAT" — an
#                  OR, and the higher-distinctiveness limb carries no group
#                  condition (metric A-2 accepted High grassland for a Medium
#                  woodland loss).
#   area  High   : "Same habitat required =" — strictly like for like, so a Very
#                  High surplus does NOT settle it.
#   hedge High   : "Like for like OR BETTER" — metric B-2 accepted a V.High hedge
#                  for a High loss.
#   hedge Medium : "Same distinctiveness band or better".
#   hedge V.High : "Like for like" — no trade-up.
_TRADES_UP = {"area": {"Medium"}, "hedgerow": {"High", "Medium"}}


def _bucketise(nets, scope):
    """{band: (scope, {key: bucket})} — losses and gains pooled the way the
    trading rules pool them. Shared so band_requirements and slot_view can never
    disagree about what a bucket is."""
    out = {}
    for band in _BANDS_HIGH_TO_LOW:
        sc = scope.get(band)
        if not sc:
            continue
        buckets = {}
        for hab, s in nets.items():
            if s["band"] != band:
                continue
            if abs(s["baseline"]) < 1e-9 and abs(s["post"]) < 1e-9:
                continue                      # zero-unit habitat
            if sc == "habitat":
                key = hab
            elif sc == "band":                # whole band pools into one bucket
                key = band
            else:
                key = s["group"] or hab
            b = buckets.setdefault(key, {"net": 0.0, "lost": 0.0, "gained": 0.0,
                                         "group": s["group"], "habs": []})
            b["net"] += s["net"]
            if s["net"] < 0:
                b["lost"] += -s["net"]
                b["habs"].append(hab)
            else:
                b["gained"] += s["net"]
        out[band] = (sc, buckets)
    return out


def _tradeup_pool(bucketed, band):
    """Surplus available to settle ``band``'s floors: everything left over in
    STRICTLY HIGHER bands. Only consulted for bands in _TRADES_UP."""
    pool = 0.0
    for higher in _BANDS_HIGH_TO_LOW:
        if higher == band:
            break
        for b in bucketed.get(higher, ("", {}))[1].values():
            if b["net"] > 0:
                pool += b["net"]
    return pool


def band_requirements(nets, baseline_total, post_total, module="area", target=0.10):
    """Split a module's unit deficit into a shopping list that SUMS to it.

    Two different things drive the requirement, and the metric reports them in
    different places:

      * trading-rule FLOORS — a net loss the rules will not let another habitat
        cover (Medium must be replaced inside the same broad habitat group; High
        and Very High need the same habitat). These are the Trading Summary's
        per-band "Unit deficit (required to meet trading rules)".
      * the plain unit SHORTFALL — how far the module still sits below baseline
        once every surplus has cascaded down ("Cumulative surplus of units"),
        plus the mandatory +10% on top.

    The floors are a SUBSET of the deficit, not an addition to it — buying a
    floor unit also counts towards the 10%. So the rows are: one per floor, then
    a single free-choice row mopping up whatever deficit is left. Rows carrying
    the same distinctiveness requirement are not split further, because the row
    is what someone takes to a habitat bank.

    Returns {"rows": [...], "deficit", "shortfall", "topup", "total", "exceeds"}
    where each row is {"kind": "floor"|"free", "band", "group", "habitats",
    "units"}. `shortfall` (back to baseline) and `topup` (the 10%) are reported
    so the split can be explained; they are NOT separate rows. `total` is
    max(deficit, floors) and `exceeds` flags floors alone demanding more than
    the 10% target does.
    """
    scope = _TRADE_SCOPE.get(module, _TRADE_SCOPE["area"])
    deficit = max(0.0, baseline_total * (1.0 + target) - post_total)
    shortfall = max(0.0, baseline_total - post_total)

    bucketed = _bucketise(nets, scope)
    floors = []
    for band in _BANDS_HIGH_TO_LOW:
        if band not in bucketed:
            continue
        sc, buckets = bucketed[band]
        # A band whose rule allows trading up draws on surplus from every higher
        # band before any floor survives.
        pool = (_tradeup_pool(bucketed, band)
                if band in _TRADES_UP.get(module, set()) else 0.0)
        for key in sorted(buckets):
            b = buckets[key]
            if b["net"] >= -1e-9:
                continue
            unmet = -b["net"]
            if pool > 0:
                used = min(pool, unmet)
                unmet -= used
                pool -= used
            if unmet <= 1e-9:
                continue
            floors.append({"kind": "floor", "band": band,
                           "group": b["group"] if sc == "group" else "",
                           "habitats": sorted(set(b["habs"])),
                           "units": round(unmet, 6)})

    floor_sum = sum(f["units"] for f in floors)
    free = max(0.0, deficit - floor_sum)
    rows = list(floors)
    if free > 5e-3:
        rows.append({"kind": "free", "band": _FREE_BAND.get(module, "Low"),
                     "group": "", "habitats": [], "units": round(free, 6)})
    return {"rows": rows,
            "deficit": round(deficit, 6),
            "shortfall": round(shortfall, 6),
            "topup": round(max(0.0, deficit - shortfall), 6),
            "total": round(max(deficit, floor_sum), 6),
            "exceeds": floor_sum > deficit + 1e-9}


def slot_view(nets, baseline_total, post_total, module="area", target=0.10):
    """Display model for the Net Gain panel's slots — what each obligation needs
    and how much of it is already supplied.

    band_requirements() reports only what is still OUTSTANDING, so a satisfied
    row disappears from it. The panel has to keep every slot on screen (a filled
    slot is exactly what tells someone their scheme is doing something), so the
    need here is GROSS and supply-independent:

      * floor slots — one per trading-rule bucket with a net loss.
        need = gross loss, have = gross gain in the same bucket, so
        short = need - have is precisely that bucket's band_requirements floor.
      * the free slot — need = target total - the floors, i.e. the room left for
        anything the rules do not pin to a group. That room carries BOTH the
        mandatory +10% AND the replacement of any Low-distinctiveness loss (the
        area module gives Low no floor of its own — see _TRADE_SCOPE), which is
        why it must not be labelled "the 10%".

    total_short always reconciles to band_requirements()["total"].

    Habitats worth 0 units never appear: they can neither create an obligation
    nor fill one, so a row for them would be empty in both directions.
    """
    scope = _TRADE_SCOPE.get(module, _TRADE_SCOPE["area"])
    tgt_total = baseline_total * (1.0 + target)

    bucketed = _bucketise(nets, scope)
    slots, floor_sum = [], 0.0
    for band in _BANDS_HIGH_TO_LOW:
        if band not in bucketed:
            continue
        sc, buckets = bucketed[band]
        pool = (_tradeup_pool(bucketed, band)
                if band in _TRADES_UP.get(module, set()) else 0.0)
        # Share the trade-up surplus across this band's deficits IN PROPORTION
        # to what each one needs.
        #
        # It used to be handed out in sorted(buckets) order, so whichever
        # bucket sorted first ate the pool — 9.86 units of orchard settled
        # "Grassland" outright and left "Individual Trees" reading 0.000
        # supplied, an artefact of the alphabet rather than of the rules.
        #
        # Group is deliberately NOT considered here. This pool is surplus from
        # a STRICTLY HIGHER band, and trading down a tier satisfies the rule on
        # distinctiveness alone — a High-distinctiveness gain is eligible
        # against any Medium loss whatever group it sits in. Favouring the
        # surplus's own group would invent a precedence the trading rules do
        # not have. Same-group priority still holds where it belongs: losses
        # and gains within a habitat are netted upstream in net_by_habitat, so
        # planted trees have already replaced lost trees before this runs, and
        # a bucket only asks the pool for what it could not cover itself.
        #
        # total_short is unchanged by the split (the pool is fully spent either
        # way); this decides only how the shortfall is ATTRIBUTED.
        deficits = {k: -b["net"] for k, b in buckets.items() if b["net"] < -1e-9}
        want = sum(deficits.values())
        if pool > 0 and want > 1e-9:
            share = (dict(deficits) if pool >= want
                     else {k: pool * (d / want) for k, d in deficits.items()})
        else:
            share = {}
        for key in sorted(buckets):
            b = buckets[key]
            if b["net"] >= -1e-9:
                continue          # bucket is whole — no obligation to show
            unmet = -b["net"]
            traded = min(share.get(key, 0.0), unmet)
            unmet -= traded
            floor_sum += unmet
            slots.append({
                "kind":     "floor",
                "band":     band,
                "scope":    sc,
                # Whether a BETTER habitat also settles this slot, so the panel
                # can say so instead of implying a strict like-for-like match.
                "trades_up": band in _TRADES_UP.get(module, set()),
                "group":    b["group"] if sc == "group" else "",
                "label":    key,
                "habitats": sorted(set(b["habs"])),
                "need":     round(b["lost"], 6),
                # traded-up units count as supplied, so the row shows the
                # cascade instead of demanding habitat already provided.
                "have":     round(b["gained"] + traded, 6),
                "traded":   round(traded, 6),
                "short":    round(unmet, 6),
                "spare":    0.0,
            })

    free_need = max(0.0, tgt_total - floor_sum)
    free_have = max(0.0, post_total)
    # Split the free room so the panel can say what it is made of: the mandatory
    # top-up first, then whatever replaces losses the rules left unpinned.
    topup_full = max(0.0, tgt_total - baseline_total)
    free_topup = min(free_need, topup_full)
    free_replace = max(0.0, free_need - topup_full)
    slots.append({
        "kind":     "free",
        "band":     _FREE_BAND.get(module, "Low"),
        "scope":    "",
        "group":    "",
        "label":    "",
        "habitats": [],
        "need":     round(free_need, 6),
        "have":     round(free_have, 6),
        "short":    round(max(0.0, free_need - free_have), 6),
        "spare":    round(max(0.0, free_have - free_need), 6),
        "replace":  round(free_replace, 6),
        "topup":    round(free_topup, 6),
    })

    # "Trading rules satisfied" is a SEPARATE test from +10% — the metric reports
    # them as two rows and a scheme can pass one while failing the other. Only
    # unmet FLOORS break trading; a free-row shortfall is a net-gain miss.
    trading_ok = all(s["short"] <= 1e-9 for s in slots if s["kind"] == "floor")

    return {"slots": slots,
            "total_short": round(sum(s["short"] for s in slots), 6),
            "target_total": round(tgt_total, 6),
            "floor_sum": round(floor_sum, 6),
            "trading_ok": trading_ok,
            "net_change": round(post_total - baseline_total, 6)}


# ── Credit-tier assignment (Source: metric 'Credits calculations' lookup) ─────
# TWO different rules, and missing the second one is what mis-tiered a Medium
# woodland loss to A1 (see tier_credits):
#
#   * HIGH / VERY HIGH losses are bespoke per SPECIFIC HABITAT — the fixed
#     lists below (the sheet's SUMIF blocks headed H21/H27/H47/H57/H65).
#   * MEDIUM losses take their tier from the BROAD HABITAT GROUP — the sheet's
#     rows 122-131, "Medium Distinctiveness Credits by Broad Habitat Group".
#   * LOW losses fall to A1 (the sheet's Rule 5 row).
#
# Specific habitats earmarked for bespoke compensation tiers A2..A5. Anything
# not listed falls to A1 (the general +10% top-up tier). Stored lower-cased.
_TIER_HABITATS = {
    "A2": {
        "lowland calcareous grassland", "tall herb communities (h6430)",
        "upland calcareous grassland", "lowland heathland",
        "dunes with sea buckthorn (h2160)", "upland heathland",
        "open mosaic habitats on previously developed land",
    },
    "A3": {
        "felled", "felled./replacement for felled woodland", "upland birchwoods",
        "wet woodland", "littoral mud", "littoral mixed sediments",
        "littoral biogenic reefs - mussels", "littoral biogenic reefs - sabellaria",
        "features of littoral sediment", "littoral muddy sand",
    },
    "A4": {
        "floodplain wetland mosaic and cfgm", "ponds (priority habitat)",
        "temporary lakes ponds and pools (h3170)", "coastal sand dunes",
        "coastal vegetated shingle", "inland rock outcrop and scree habitats",
        "maritime cliff and slopes", "lowland mixed deciduous woodland",
        "native pine woodlands", "upland mixed ashwoods",
        "lowland beech and yew woodland", "upland oakwood", "coastal lagoons",
        "high energy littoral rock", "moderate energy littoral rock",
        "low energy littoral rock", "features of littoral rock",
        "littoral seagrass", "saltmarshes and saline reedbeds",
    },
    "A5": {
        "high alkalinity lakes", "low alkalinity lakes", "marl lakes",
        "moderate alkalinity lakes", "peat lakes",
    },
}
_HAB_TIER = {h: t for t, habs in _TIER_HABITATS.items() for h in habs}


def credit_tier(habitat):
    """Credit tier (A1..A5) a HIGH / VERY HIGH habitat's loss is compensated
    in. A2..A5 are fixed bespoke lists; everything else is the general tier A1.

    Medium-distinctiveness losses do NOT use this — they are tiered by broad
    habitat group (``group_credit_tier``)."""
    return _HAB_TIER.get((habitat or "").strip().lower(), "A1")


# Broad habitat group -> credit tier for MEDIUM-distinctiveness losses.
# Metric 'Credits calculations' rows 122-131 (column L names the tier):
#   A1  Cropland, Grassland, Heathland and shrub, Urban, Individual trees
#   A2  Woodland and forest, Intertidal sediment
#   A4  Lakes, Sparsely vegetated land, Wetland
# Keyed on the leading words of OUR group names, which differ from the sheet's
# ("Woodland" vs "Woodland and forest", "Coastal & Intertidal" covers the
# sheet's Intertidal sediment — the only coastal group with Medium habitats).
_MEDIUM_GROUP_TIER = {
    "woodland": "A2",
    "coastal": "A2",          # Coastal & Intertidal -> Intertidal sediment
    "intertidal": "A2",
    "lakes": "A4",
    "sparsely": "A4",
    "wetland": "A4",
}

# The order leftover surplus cascades through the medium tiers — most
# expensive first (sheet rules 2, 3, 4 at rows 69-71).
_MEDIUM_TIER_ORDER = ("A4", "A2", "A1")


def group_credit_tier(group):
    """Credit tier a MEDIUM-distinctiveness loss in ``group`` lands in.
    Unknown / unlisted groups fall to A1, matching the sheet (its rows 122-126
    are the A1 catch-all: cropland, grassland, heathland, urban, trees)."""
    g = (group or "").strip().lower()
    for key, tier in _MEDIUM_GROUP_TIER.items():
        if g.startswith(key):
            return tier
    return "A1"


def tier_credits(nets, baseline_total, post_total, target=0.10):
    """Per-tier shortfall + credits, replicating the metric's Credits calc.

    Three strands feed the tiers, and they are NOT the same shape:

    1. **Bespoke High / Very High losses** — per SPECIFIC habitat
       (``credit_tier``). Another habitat's gain never erases these; only a
       gain of the same habitat, already netted in ``nets``.
    2. **Medium losses** — pooled per BROAD HABITAT GROUP and tiered by that
       group (``group_credit_tier``), after an offset cascade (below). This
       strand is the one the engine used to miss entirely: every Medium loss
       fell to A1 and was then swallowed by the pooled deficit, so a Medium
       woodland loss was quoted as a smaller A1 figure instead of its own A2
       one (Saul White: A1 1.01 where the metric says A2 1.23).
    3. **Low losses** — Rule 5, into A1.

    The offset cascade (sheet rows 68-72). Surplus is used up in this order:
      * Rule 1 — a group's HIGHER-distinctiveness surplus offsets that same
        group's Medium loss.
      * Rules 2-4 — whatever higher surplus is left cascades through the
        medium tiers most-expensive-first: A4, then A2, then A1.
      * Rule 5 — what remains, plus any Medium surplus (which can only trade
        DOWN), offsets Low losses.

    A2..A5 are then their own losses; A1 additionally absorbs the scheme's
    remaining unit deficit (the +10%): ``A1 = max(own losses, deficit -
    A2..A5)``, which is the sheet's long F9 formula reduced to its two limbs.
    Credits = shortfall x 2 (the sheet applies the spatial risk multiplier at
    this point; ours is on-site, where SRM = 1).
    """
    # Use the SAME reconciled obligation rows as the live Net Gain panel and
    # the Market & credits view.  The former implementation ran a second,
    # independent cascade here; in a Low-distinctiveness case it could count
    # the replacement shortfall and the overall +10% deficit separately.  The
    # panel then showed 0.02 units outstanding while this function priced 0.04
    # units and returned 0.08 credits.  A credit is simply the outstanding row
    # assigned to its statutory tier, multiplied 2:1.
    TIERS = ["A1", "A2", "A3", "A4", "A5"]
    req = band_requirements(nets, baseline_total, post_total,
                            module="area", target=target)
    short = {t: 0.0 for t in TIERS}
    medium_short = {t: 0.0 for t in TIERS}
    low_short = 0.0

    for row in req.get("rows", []):
        units = max(0.0, float(row.get("units") or 0.0))
        if units <= 1e-12:
            continue
        band = row.get("band") or "Low"
        if row.get("kind") == "free" or band in ("Low", "Very Low"):
            tier = "A1"
            low_short += units
        elif band == "Medium":
            tier = group_credit_tier(row.get("group") or "")
            medium_short[tier] += units
        else:  # High / Very High — the floor is habitat-specific
            habitats = row.get("habitats") or []
            tier = credit_tier(habitats[0] if habitats else "")
        short[tier] += units

    out = {t: {"shortfall": round(short[t], 4),
               "credits": round(short[t] * 2, 4)} for t in TIERS}
    out["_deficit"] = round(float(req.get("deficit") or 0.0), 4)
    out["_medium_by_tier"] = {t: round(medium_short[t], 4) for t in TIERS}
    out["_low_short"] = round(low_short, 4)
    return out


def tier_credits_from_slots(view):
    """Convert an area's authoritative obligation-slot view to A1–A5 credits.

    This enforces the statutory 2:1 relationship at the UI boundary: the sum
    of credits returned here is always exactly twice ``view['total_short']``.
    Tier routing is retained — free/Low rows are A1, Medium floors use their
    broad-habitat tier, and High/Very High floors use the specific-habitat
    lookup.
    """
    tiers = {t: 0.0 for t in ("A1", "A2", "A3", "A4", "A5")}
    for row in (view or {}).get("slots", []):
        units = max(0.0, float(row.get("short") or 0.0))
        if units <= 1e-12:
            continue
        band = row.get("band") or "Low"
        if row.get("kind") == "free" or band in ("Low", "Very Low"):
            tier = "A1"
        elif band == "Medium":
            tier = group_credit_tier(row.get("group") or "")
        else:
            habitats = row.get("habitats") or []
            tier = credit_tier(habitats[0] if habitats else "")
        tiers[tier] += units * 2.0
    return {t: round(v, 4) for t, v in tiers.items()}


def water_credit(nets):
    """Watercourse credits — replicate the metric 'Trading Summary WaterC's'.

    Watercourse trading is per distinctiveness band:
      * Very High / High / Medium -> "Same habitat required": a net LOSS of such
        a habitat cannot be offset by gains in any OTHER band (or other habitat),
        so it stands as a remaining loss.
      * Low (Culvert) -> "Better distinctiveness required": a Low net loss CAN be
        compensated by surpluses in any higher-distinctiveness band, so it only
        counts to the extent it exceeds those surpluses.

    The credit is the remaining (un-tradeable) net loss x 2 — NO +10% headroom is
    added (the metric's W credit equals the un-tradeable loss exactly). Returns
    (credits, shortfall)."""
    SAME = ("Very High", "High", "Medium")
    higher_surplus = sum(s["net"] for s in nets.values()
                         if s["net"] > 1e-12 and s["band"] in SAME)
    same_loss = sum(-s["net"] for s in nets.values()
                    if s["net"] < -1e-12 and s["band"] in SAME)
    low_loss = sum(-s["net"] for s in nets.values()
                   if s["net"] < -1e-12 and s["band"] == "Low")
    shortfall = same_loss + max(0.0, low_loss - higher_surplus)
    return round(shortfall * 2, 4), round(shortfall, 4)


def debug_dump(nets, bands, tiers, log):
    """Write the full breakdown to the provided log(str) callable."""
    log("-- CREDITS: per-habitat net change (baseline -> post = net) --")
    for hab, s in sorted(nets.items(), key=lambda kv: -kv[1]["dist"]):
        log("   %-34s %-9s base=%.4f post=%.4f net=%+.4f"
            % (hab[:34], s["band"], s["baseline"], s["post"], s["net"]))
    log("-- CREDITS: per band (gain / loss / net) --")
    for b in _BANDS_HIGH_TO_LOW:
        log("   %-10s gain=%.4f loss=%.4f net=%+.4f"
            % (b, bands[b]["gain"], bands[b]["loss"], bands[b]["net"]))
    log("-- CREDITS: tier shortfall / credits (area_deficit=%.4f) --"
        % tiers.get("_deficit", 0.0))
    for t in ["A1", "A2", "A3", "A4", "A5"]:
        log("   %s shortfall=%.4f credits=%.4f"
            % (t, tiers[t]["shortfall"], tiers[t]["credits"]))
