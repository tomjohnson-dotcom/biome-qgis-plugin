# -*- coding: utf-8 -*-
"""
net_classifier.py
=================

SINGLE SOURCE OF TRUTH for baseline-vs-proposed habitat classification.

Replaces the old geometry-overlay classifier (``_spatial_classify_area``) and
the attribute-driven linear/tree collectors with one position-independent
*net-difference* engine that is shared by:

    * the statutory metric / accounting export (xlsm_exporter.py), and
    * the in-plugin Summary / Balance widget (main_panel.py).

Both consume the SAME pure per-group numbers, so the summary can never
disagree with the export.  The export uses the numbers directly; the summary
applies its own downstream unit maths on top of the identical figures.

--------------------------------------------------------------------------
ALGORITHM (per feature class: area / hedgerow / watercourse / trees)
--------------------------------------------------------------------------
Everything is driven purely by how much habitat of each
``(broad habitat, sub habitat, condition)`` exists in the baseline versus the
proposed layer.  Position within the redline is irrelevant: 20 m of the same
hedgerow in baseline and proposed nets to zero regardless of where it sits.

For each group key ``k = (broad, sub, condition)``:

    BL[k]        = total baseline quantity   (ha / km / tree-area-ha)
    PR[k]        = total proposed quantity
    retained[k]  = min(BL[k], PR[k])         # same habitat & condition persists
    raw_lost[k]  = max(0, BL[k] - PR[k])     # baseline that has no proposed match
    raw_created  = max(0, PR[k] - BL[k])     # proposed beyond the matched baseline

Then a single ENHANCEMENT reconciliation pass runs *within each broad
habitat* (cross sub-habitat allowed, per spec).  Left-over ``raw_lost`` at a
baseline condition X is paired against left-over ``raw_created`` at a proposed
condition Y **iff Y is strictly better than X** (``condition_score[Y] >
condition_score[X]``).  The paired quantity is reclassified as ENHANCED
(recorded baseline X -> proposed Y) and removed from both lost and created.

Consequences that match the agreed spec:
    * A pure condition UPGRADE on persisting ground  -> Enhanced.
    * A pure condition DOWNGRADE (Y worse than X)     -> stays Lost + Created
      (the proposed parcel is Created at its own, lower, condition).
    * Equal-condition different-sub left-overs        -> stays Lost + Created
      (not "strictly better", so never enhanced).

Because retained / enhanced / lost / created are derived arithmetically from
measured totals, there is NO floating-point intersection noise and therefore
NO scaling / reallocation fudge is needed (the old ``_reallocate_*`` and the
summary's ``_scale`` step are both gone).

--------------------------------------------------------------------------
Determinism note (read before changing pairing)
--------------------------------------------------------------------------
The *total* enhanced quantity per broad habitat is fixed by the data, but when
several baseline conditions could each pair with the same created parcel, the
*attribution* (which baseline condition -> which proposed condition) affects
downstream unit uplift.  With no geometry there is no ground truth, so a
single deterministic rule is used and documented in ``_pair_enhancements``:
    1. prefer same sub-habitat pairs,
    2. then consume the HIGHEST eligible baseline condition first
       (most conservative uplift).
Change that one function if a different convention is required.
"""

from collections import defaultdict, OrderedDict


# ---------------------------------------------------------------------------
# Pure core — no QGIS, fully unit-testable.
# ---------------------------------------------------------------------------

def classify_net(baseline_buckets, proposed_buckets, condition_score,
                 enhance_scope=("broad",), distinctiveness_score=None,
                 can_enhance=None):
    """Net-difference classification of baseline vs proposed.

    Parameters
    ----------
    baseline_buckets, proposed_buckets : iterable of dict
        Each bucket describes one already-summed quantity::

            {
                "broad":   str,    # broad habitat (enhancement scope by default)
                "sub":     str,    # sub habitat type
                "cond":    str,    # condition label (key of condition_score)
                "strat":   str,    # strategic significance (proposed side; "" ok)
                "qty":     float,  # ha / km / tree-area-ha
                "payload": dict,   # original attributes to carry onto output rows
            }

        Buckets sharing a key are summed, so the caller may pass one bucket per
        feature or one per pre-aggregated group — the result is identical.
    condition_score : dict[str, float]
        Ordinal rank of each condition (higher == better).
    enhance_scope : tuple[str, ...]
        Group-key fields that must match for a lost<->created pair to be
        eligible for enhancement.  Default ``("broad",)`` allows pairing across
        sub-habitats within the same broad habitat.  Pass ``("broad", "size")``
        etc. for trees where size must also match.
    distinctiveness_score : dict[str, float] | None
        Optional ordinal rank of each *sub-habitat's* distinctiveness (higher ==
        better; e.g. ``habitat_data.DISTINCTIVENESS_SCORES`` bands 0/2/4/6/8).
        When supplied, a lost<->created pair within the same broad habitat is
        enhancement-eligible if the proposed sub-habitat is **higher
        distinctiveness** than the baseline, *or* the proposed condition is
        better (DEFRA: enhancement = same broad + condition up OR BNG-per-area
        up). When ``None`` the original condition-only test is used (trees,
        hedgerows).

    Returns
    -------
    dict with keys:
        "baseline_groups" : OrderedDict[key -> dict]
            key = (broad, sub, cond); value has qty / retained / enhanced /
            lost (sum == qty) plus a representative payload.
        "created_groups"  : list[dict]
            {broad, sub, cond, strat, qty, payload} — genuinely new habitat,
            split by strategic significance.
        "enhanced_records": list[dict]
            {broad, bl_sub, bl_cond, pr_sub, pr_cond, strat, qty,
             bl_payload, pr_payload}.
        "totals": dict  (baseline_qty, proposed_qty, retained, enhanced,
                         lost, created) — handy for the summary balance.
    """
    def _score(cond):
        return condition_score.get(cond, 0.0)

    _dist_tbl = distinctiveness_score or {}
    def _dist(sub):
        return float(_dist_tbl.get(sub, 0.0))

    def _scope_key(broad, payload):
        parts = []
        for field in enhance_scope:
            if field == "broad":
                parts.append(broad)
            else:
                parts.append(str((payload or {}).get(field, "")))
        return tuple(parts)

    # Net-matching key = (broad, sub, cond, xkey).  ``xkey`` is an optional
    # tuple of extra dimensions that must ALSO match for two parcels to be
    # treated as "the same habitat" (e.g. watercourse encroachment, tree size).
    # Defaults to () so the key is effectively (broad, sub, cond).
    def _net_key(bucket):
        return (bucket["broad"], bucket["sub"], bucket["cond"],
                tuple(bucket.get("xkey") or ()))

    # ── 1. Tally baseline ──────────────────────────────────────────────────
    bl_qty      = defaultdict(float)
    bl_payload  = {}
    for b in baseline_buckets:
        k = _net_key(b)
        bl_qty[k] += float(b.get("qty") or 0.0)
        bl_payload.setdefault(k, dict(b.get("payload") or {}))

    # ── 2. Tally proposed, tracking strategic-significance sub-split ───────
    pr_qty       = defaultdict(float)
    pr_strat_qty = defaultdict(lambda: defaultdict(float))   # key -> strat -> qty
    pr_payload   = {}
    pr_strat_payload = {}                                    # (key, strat) -> payload
    for p in proposed_buckets:
        k = _net_key(p)
        strat = str(p.get("strat") or "")
        q = float(p.get("qty") or 0.0)
        pr_qty[k] += q
        pr_strat_qty[k][strat] += q
        pr_payload.setdefault(k, dict(p.get("payload") or {}))
        pr_strat_payload.setdefault((k, strat), dict(p.get("payload") or {}))

    all_keys = list(OrderedDict.fromkeys(list(bl_qty.keys()) + list(pr_qty.keys())))

    # ── 3. Per-key net difference ──────────────────────────────────────────
    retained   = {}
    raw_lost    = {}
    raw_created = {}
    for k in all_keys:
        bl = round(bl_qty.get(k, 0.0), 9)
        pr = round(pr_qty.get(k, 0.0), 9)
        retained[k]    = min(bl, pr)
        raw_lost[k]    = max(0.0, bl - pr)
        raw_created[k] = max(0.0, pr - bl)

    # ── 4. Enhancement reconciliation (within enhance_scope) ───────────────
    # Build per-key enhanced accumulator on the baseline side, and consume
    # raw_created on the proposed side.  enhanced_records records each pairing.
    enhanced_per_blkey = defaultdict(float)            # bl key -> enhanced qty
    consumed_created   = defaultdict(float)            # pr key -> created consumed
    enhanced_records_raw = []                          # (bl_key, pr_key, qty)

    # Group lost & created entries by enhancement scope.
    lost_by_scope    = defaultdict(list)   # scope -> [ (bl_key, qty) ]
    created_by_scope = defaultdict(list)   # scope -> [ (pr_key, qty) ]
    for k in all_keys:
        broad, sub, cond = k[0], k[1], k[2]
        if raw_lost.get(k, 0.0) > 1e-12:
            sc = _scope_key(broad, bl_payload.get(k))
            lost_by_scope[sc].append([k, raw_lost[k]])
        if raw_created.get(k, 0.0) > 1e-12:
            sc = _scope_key(broad, pr_payload.get(k))
            created_by_scope[sc].append([k, raw_created[k]])

    for sc, created_items in created_by_scope.items():
        lost_items = lost_by_scope.get(sc, [])
        if not lost_items:
            continue
        # Process the best (highest condition, then highest distinctiveness)
        # created parcels first — they can absorb the widest range of baseline
        # parcels.
        created_items.sort(key=lambda it: (-_score(it[0][2]), -_dist(it[0][1]),
                                           it[0][1], it[0][2]))
        for cre in created_items:
            pr_sub, pr_cond = cre[0][1], cre[0][2]
            pr_score, pr_dist = _score(pr_cond), _dist(pr_sub)
            # Enhancement-eligible baseline (same broad, already guaranteed by
            # the scope grouping): proposed is genuinely better — either a
            # condition uplift OR a higher-distinctiveness habitat (BNG-per-area
            # up).  Without a distinctiveness table this reduces to the original
            # condition-only test (trees / hedgerows).
            def _enh_eligible(li):
                bl_sub, bl_cond = li[0][1], li[0][2]
                if not (_score(bl_cond) < pr_score - 1e-12
                        or _dist(bl_sub) < pr_dist - 1e-12):
                    return False
                # The metric only accepts some baseline->proposed changes as
                # enhancement (the rest are creation). When a validity predicate
                # is supplied, reject the disallowed ones so they stay
                # lost + created.
                if can_enhance is not None and not can_enhance(
                        bl_sub, bl_cond, pr_sub, pr_cond):
                    return False
                return True
            # Tie-break: same sub-habitat first, then HIGHEST baseline
            # condition first (closest below -> most conservative uplift).
            elig = [li for li in lost_items
                    if li[1] > 1e-12 and _enh_eligible(li)]
            elig.sort(key=lambda li: (0 if li[0][1] == pr_sub else 1,
                                      -_score(li[0][2]),
                                      li[0][1], li[0][2]))
            for li in elig:
                if cre[1] <= 1e-12:
                    break
                take = min(cre[1], li[1])
                if take <= 1e-12:
                    continue
                bl_key = li[0]
                pr_key = cre[0]
                enhanced_per_blkey[bl_key] += take
                consumed_created[pr_key]   += take
                enhanced_records_raw.append((bl_key, pr_key, take))
                cre[1] -= take
                li[1]  -= take

    # ── 5. Assemble baseline groups (qty / retained / enhanced / lost) ─────
    baseline_groups = OrderedDict()
    for k in all_keys:
        bl = round(bl_qty.get(k, 0.0), 9)
        if bl <= 1e-12 and retained.get(k, 0.0) <= 1e-12 \
           and enhanced_per_blkey.get(k, 0.0) <= 1e-12 \
           and raw_lost.get(k, 0.0) <= 1e-12:
            # purely a proposed-only key — no baseline row.
            continue
        broad, sub, cond = k[0], k[1], k[2]
        enh  = enhanced_per_blkey.get(k, 0.0)
        lost = max(0.0, raw_lost.get(k, 0.0) - enh)
        baseline_groups[k] = {
            "broad":    broad,
            "sub":      sub,
            "cond":     cond,
            "qty":      bl,
            "retained": retained.get(k, 0.0),
            "enhanced": enh,
            "lost":     lost,
            "payload":  bl_payload.get(k, {}),
        }

    # ── 6. Assemble enhanced records, splitting strat by proposed shares ───
    enhanced_records = []
    for bl_key, pr_key, qty in enhanced_records_raw:
        b_broad, b_sub, b_cond = bl_key[0], bl_key[1], bl_key[2]
        p_broad, p_sub, p_cond = pr_key[0], pr_key[1], pr_key[2]
        strat_map = pr_strat_qty.get(pr_key, {})
        strat_total = sum(strat_map.values()) or 1.0
        if strat_map:
            for strat, sq in strat_map.items():
                part = qty * (sq / strat_total)
                if part <= 1e-12:
                    continue
                enhanced_records.append({
                    "broad":   b_broad,
                    "bl_sub":  b_sub,
                    "bl_cond": b_cond,
                    "pr_sub":  p_sub,
                    "pr_cond": p_cond,
                    "strat":   strat,
                    "qty":     part,
                    "bl_payload": bl_payload.get(bl_key, {}),
                    "pr_payload": pr_strat_payload.get((pr_key, strat),
                                                       pr_payload.get(pr_key, {})),
                })
        else:
            enhanced_records.append({
                "broad": b_broad, "bl_sub": b_sub, "bl_cond": b_cond,
                "pr_sub": p_sub, "pr_cond": p_cond, "strat": "",
                "qty": qty,
                "bl_payload": bl_payload.get(bl_key, {}),
                "pr_payload": pr_payload.get(pr_key, {}),
            })

    # ── 7. Assemble created groups (left-over created, split by strat) ─────
    created_groups = []
    for k in all_keys:
        leftover = raw_created.get(k, 0.0) - consumed_created.get(k, 0.0)
        if leftover <= 1e-12:
            continue
        broad, sub, cond = k[0], k[1], k[2]
        strat_map = pr_strat_qty.get(k, {})
        strat_total = sum(strat_map.values()) or 1.0
        if strat_map:
            for strat, sq in strat_map.items():
                part = leftover * (sq / strat_total)
                if part <= 1e-12:
                    continue
                created_groups.append({
                    "broad": broad, "sub": sub, "cond": cond, "strat": strat,
                    "qty": part,
                    "payload": pr_strat_payload.get((k, strat),
                                                    pr_payload.get(k, {})),
                })
        else:
            created_groups.append({
                "broad": broad, "sub": sub, "cond": cond, "strat": "",
                "qty": leftover, "payload": pr_payload.get(k, {}),
            })

    # ── 8. Totals ──────────────────────────────────────────────────────────
    totals = {
        "baseline_qty": round(sum(bl_qty.values()), 9),
        "proposed_qty": round(sum(pr_qty.values()), 9),
        "retained":     round(sum(g["retained"] for g in baseline_groups.values()), 9),
        "enhanced":     round(sum(g["enhanced"] for g in baseline_groups.values()), 9),
        "lost":         round(sum(g["lost"] for g in baseline_groups.values()), 9),
        "created":      round(sum(g["qty"] for g in created_groups), 9),
    }

    return {
        "baseline_groups": baseline_groups,
        "created_groups":  created_groups,
        "enhanced_records": enhanced_records,
        "totals": totals,
    }


# ---------------------------------------------------------------------------
# Pair-aware classification for trees & hedgerows
# ---------------------------------------------------------------------------
def classify_net_paired(baseline_buckets, proposed_buckets, condition_score,
                        enhance_scope=("broad",), no_net=False,
                        distinctiveness_score=None, can_enhance=None,
                        delivery_rate=None):
    """Like classify_net, but first resolves features linked by ``pair_id``
    using their FATE rather than net-quantity matching.

    Why this exists
    ---------------
    The net engine treats same-key baseline+proposed quantity as *retained*
    (``min(BL, PR)``). For ground-based area habitats that's correct and
    standard practice. But for individual trees and hedgerows a baseline twin
    being *removed* and a new one *planted* would also look like a no-change
    retention, silently dropping the temporal/difficulty discount the new
    planting must carry.

    To fix this without geometry, paired placement stamps both twins with a
    shared ``pair_id``. Here we honour that link explicitly:

        * pair_id present on BOTH a baseline and a proposed feature
              -> RETAINED (or ENHANCED if the proposed condition is better),
                 carrying the baseline rate, no creation discount.
        * pair_id present on a BASELINE feature only (twin deleted)
              -> LOST.
        * pair_id present on a PROPOSED feature only (twin deleted)
              -> CREATED (gets the discount — it is genuinely new).

    ``no_net``
        When True (trees & hedgerows), UNLINKED features are NOT passed through
        the net-difference algorithm. Instead every unlinked baseline feature
        is LOST and every unlinked proposed feature is CREATED — they can never
        silently cancel. This is the airtight behaviour for features that can
        be removed-and-replaced. When False (default), unlinked features fall
        through to ``classify_net`` (area-habitat behaviour, unchanged).

    Buckets must expose ``pair_id`` either at top level or inside ``payload``.
    Two features that the surveyor has explicitly declared *different* should
    carry DISTINCT pair_ids (e.g. "solo:<uuid>") so they never match.
    """
    def _pid(b):
        v = b.get("pair_id")
        if v in (None, ""):
            v = (b.get("payload") or {}).get("pair_id")
        return str(v).strip() if v not in (None, "") else ""

    def _score(cond):
        return condition_score.get(cond, 0.0)

    _dist_tbl = distinctiveness_score or {}
    def _dist(sub):
        return float(_dist_tbl.get(sub, 0.0))

    # ── split linked vs unlinked ───────────────────────────────────────────
    bl_linked, bl_free = {}, []
    for b in baseline_buckets:
        pid = _pid(b)
        if pid:
            bl_linked.setdefault(pid, b)        # one twin per id per side
        else:
            bl_free.append(b)

    pr_linked, pr_free = {}, []
    for p in proposed_buckets:
        pid = _pid(p)
        if pid:
            pr_linked.setdefault(pid, p)
        else:
            pr_free.append(p)

    # ── resolve every pair_id by fate ──────────────────────────────────────
    paired_baseline_groups = OrderedDict()
    paired_enhanced = []
    paired_created  = []

    def _bl_key(b):
        return (b["broad"], b["sub"], b["cond"], tuple(b.get("xkey") or ()))

    def _accum_baseline(b, field, qty):
        k = _bl_key(b)
        g = paired_baseline_groups.get(k)
        if g is None:
            g = {"broad": b["broad"], "sub": b["sub"], "cond": b["cond"],
                 "qty": 0.0, "retained": 0.0, "enhanced": 0.0, "lost": 0.0,
                 "payload": dict(b.get("payload") or {})}
            paired_baseline_groups[k] = g
        g["qty"]   += qty
        g[field]   += qty

    all_pids = set(bl_linked) | set(pr_linked)
    for pid in all_pids:
        b = bl_linked.get(pid)
        p = pr_linked.get(pid)
        if b and p:
            qty = float(b.get("qty") or 0.0)
            # Same enhancement rule as area habitats (DEFRA): the retained twin
            # is ENHANCED if it improves in condition OR changes to a
            # higher-distinctiveness habitat; if it gets WORSE (condition down,
            # or — for hedgerows — a lower-distinctiveness habitat) the baseline
            # value is Lost and the proposed twin is Created at its own (worse)
            # state; otherwise it is Retained unchanged.
            better = (_score(p["cond"]) > _score(b["cond"]) + 1e-12
                      or _dist(p["sub"]) > _dist(b["sub"]) + 1e-12)
            worse  = (_score(p["cond"]) < _score(b["cond"]) - 1e-12
                      or _dist(p["sub"]) < _dist(b["sub"]) - 1e-12)
            # Same distinctiveness AND condition: the metric still allows
            # ENHANCEMENT for hedgerows / watercourses when the reach DELIVERS
            # MORE units (e.g. reduced riparian encroachment or higher strategic
            # significance). Decide that case on the full per-unit delivery rate:
            # more -> enhanced, fewer -> lost+created, equal -> retained. (Trees
            # and the area module pass no delivery_rate, so they are unchanged.)
            if not better and not worse and delivery_rate is not None:
                br = delivery_rate(b)
                pr_ = delivery_rate(p)
                better = pr_ > br + 1e-12
                worse  = pr_ < br - 1e-12
            # The metric only accepts some habitat changes as enhancement; a
            # disallowed "better" change (e.g. Non-native hedgerow -> native) is
            # CREATION, not enhancement.
            allowed = (can_enhance is None
                       or can_enhance(b["sub"], b["cond"], p["sub"], p["cond"]))
            if better and allowed:
                _accum_baseline(b, "enhanced", qty)
                paired_enhanced.append({
                    "broad": b["broad"], "bl_sub": b["sub"], "bl_cond": b["cond"],
                    "pr_sub": p["sub"], "pr_cond": p["cond"],
                    "strat": str(p.get("strat") or ""), "qty": qty,
                    "bl_payload": dict(b.get("payload") or {}),
                    "pr_payload": dict(p.get("payload") or {}),
                })
            elif worse or (better and not allowed):
                # Downgrade, OR a "better" change the metric won't accept as
                # enhancement -> baseline lost, proposed created (like area).
                _accum_baseline(b, "lost", qty)
                paired_created.append({
                    "broad": p["broad"], "sub": p["sub"], "cond": p["cond"],
                    "strat": str(p.get("strat") or ""),
                    "qty": float(p.get("qty") or 0.0),
                    "payload": dict(p.get("payload") or {}),
                })
            else:
                _accum_baseline(b, "retained", qty)
        elif b and not p:
            # proposed twin deleted -> the baseline tree/hedge is Lost
            _accum_baseline(b, "lost", float(b.get("qty") or 0.0))
        elif p and not b:
            # baseline twin deleted -> the proposed tree/hedge is Created
            paired_created.append({
                "broad": p["broad"], "sub": p["sub"], "cond": p["cond"],
                "strat": str(p.get("strat") or ""),
                "qty": float(p.get("qty") or 0.0),
                "payload": dict(p.get("payload") or {}),
            })

    # ── unlinked features ──────────────────────────────────────────────────
    if no_net:
        # Airtight mode: never net. Each unlinked baseline feature is LOST,
        # each unlinked proposed feature is CREATED. Build minimal result sets
        # in the same shape classify_net returns, then merge below.
        free_baseline = OrderedDict()
        free_created  = []
        for b in bl_free:
            k = _bl_key(b)
            g = free_baseline.get(k)
            if g is None:
                g = {"broad": b["broad"], "sub": b["sub"], "cond": b["cond"],
                     "qty": 0.0, "retained": 0.0, "enhanced": 0.0, "lost": 0.0,
                     "payload": dict(b.get("payload") or {})}
                free_baseline[k] = g
            q = float(b.get("qty") or 0.0)
            g["qty"]  += q
            g["lost"] += q
        for p in pr_free:
            free_created.append({
                "broad": p["broad"], "sub": p["sub"], "cond": p["cond"],
                "strat": str(p.get("strat") or ""),
                "qty": float(p.get("qty") or 0.0),
                "payload": dict(p.get("payload") or {})})
        free = {
            "baseline_groups": free_baseline,
            "created_groups":  free_created,
            "enhanced_records": [],
            "totals": {
                "proposed_qty": round(sum(float(p.get("qty") or 0.0)
                                          for p in pr_free), 9),
            },
        }
    else:
        free = classify_net(bl_free, pr_free, condition_score,
                            enhance_scope=enhance_scope)

    # ── merge the two result sets ──────────────────────────────────────────
    merged_baseline = OrderedDict()
    for src in (free["baseline_groups"], paired_baseline_groups):
        for k, g in src.items():
            if k in merged_baseline:
                m = merged_baseline[k]
                for fld in ("qty", "retained", "enhanced", "lost"):
                    m[fld] += g[fld]
            else:
                merged_baseline[k] = dict(g)

    merged_created  = list(free["created_groups"])  + paired_created
    merged_enhanced = list(free["enhanced_records"]) + paired_enhanced

    totals = {
        "baseline_qty": round(sum(g["qty"] for g in merged_baseline.values()), 9),
        "proposed_qty": round(free["totals"]["proposed_qty"]
                              + sum(b.get("qty") or 0.0 for b in pr_linked.values()), 9),
        "retained": round(sum(g["retained"] for g in merged_baseline.values()), 9),
        "enhanced": round(sum(g["enhanced"] for g in merged_baseline.values()), 9),
        "lost":     round(sum(g["lost"] for g in merged_baseline.values()), 9),
        "created":  round(sum(g["qty"] for g in merged_created), 9),
    }

    return {
        "baseline_groups":  merged_baseline,
        "created_groups":   merged_created,
        "enhanced_records": merged_enhanced,
        "totals": totals,
    }
