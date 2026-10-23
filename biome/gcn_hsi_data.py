# -*- coding: utf-8 -*-
"""
gcn_hsi_data.py
===============

Great Crested Newt Habitat Suitability Index (HSI) — the computable model behind
the in-plugin HSI calculator (see gcn_hsi_dialog.py).

Method: ARG UK Advice Note 5 (2010), after Oldham, Keeble, Swan & Jeffcote (2000),
"Evaluating the suitability of habitat for the great crested newt". The HSI is the
geometric mean of ten suitability indices (SI1–SI10), each on a 0.01–1 scale::

    HSI = (SI1 x SI2 x ... x SI10) ^ (1/10)

Six indices are CATEGORICAL — pick a band, take a fixed SI. Four are read off
GRAPHS in the guide (SI2 pond area, SI5 shade, SI8 pond density, SI10 macrophyte
cover). Those four graphs are digitised here as piecewise-linear lookup tables
(originals reproduced in icons/gcn_hsi/), so the surveyor enters a field
measurement and the SI is interpolated, while the original graph is still shown.

Special cases for pond area (SI2):

    * pond < 50 m^2   -> SI2 = 0.05 (hard to read off the graph at this end)
    * pond > 2000 m^2 -> holds the 2000 m^2 score (0.80); the curve is flat here.

Pond suitability bands (Lee Brady, in the Advice Note):

    < 0.5  poor · 0.5–0.59 below average · 0.6–0.69 average ·
    0.7–0.79 good · >= 0.8 excellent

This module is pure data + arithmetic (no Qt), so it can be unit-tested on its own.
"""

import math

PI = math.pi

# Sentinel: SI2 deliberately dropped from the product because the pond is larger
# than 2000 m^2. compute_hsi() skips it and divides by nine instead of ten.
OMIT = "__omit__"


# ---------------------------------------------------------------------------
# Categorical indices — (label, SI, criteria). Ordered best-first.
# ---------------------------------------------------------------------------

LOCATION = [        # SI1 — desktop/map exercise; see icons/gcn_hsi/si1_zone_map.png
    ("Zone A — optimal", 1.0, "Location is optimal for great crested newts."),
    ("Zone B — marginal", 0.5, "Marginal location, or a site on a zone boundary."),
    ("Zone C — unsuitable", 0.01, "Location is unsuitable for great crested newts."),
]

PERMANENCE = [      # SI3
    ("Rarely dries", 1.0, "Dries no more than two years in ten, or only in drought."),
    ("Never dries", 0.9, "Permanent water; never dries."),
    ("Sometimes dries", 0.5, "Dries between three years in ten and most years."),
    ("Dries annually", 0.1, "Dries every year."),
]

WATER_QUALITY = [   # SI4
    ("Good", 1.0, "Abundant, diverse invertebrate community (e.g. mayfly larvae, water shrimps)."),
    ("Moderate", 0.67, "Moderate invertebrate diversity."),
    ("Poor", 0.33, "Low invertebrate diversity (e.g. midge/mosquito larvae); few submerged plants."),
    ("Bad", 0.01, "Clearly polluted; only pollution-tolerant invertebrates; no submerged plants."),
]

WATERFOWL = [       # SI6
    ("Absent", 1.0, "No evidence of waterfowl impact (moorhens may be present)."),
    ("Minor", 0.67, "Waterfowl present but little impact; submerged plants and banks intact."),
    ("Major", 0.01, "Severe impact; little/no submerged plants, turbid water, denuded banks."),
]

FISH = [            # SI7
    ("Absent", 1.0, "No records of stocking and none found by netting or torchlight."),
    ("Possible", 0.67, "No evidence of fish, but local conditions suggest they may be present."),
    ("Minor", 0.33, "Small numbers of crucian carp, goldfish or stickleback present."),
    ("Major", 0.01, "Dense populations of fish present."),
]

TERRESTRIAL = [     # SI9
    ("Good", 1.0, "Good foraging/shelter habitat (semi-natural, brownfield, low-intensity "
                  "farmland) over >75% of the available area."),
    ("Moderate", 0.67, "Habitat offering foraging/shelter over 25–75% of the available area."),
    ("Poor", 0.33, "Poor structure (amenity grass, improved pasture, arable); <25% suitable."),
    ("None", 0.01, "No suitable habitat (e.g. centre of an arable field or large bare expanse)."),
]


# ---------------------------------------------------------------------------
# Graph indices — piecewise-linear (x, SI), x ascending. Digitised from ARG UK
# Advice Note 5 (after Oldham et al. 2000); the reproduced originals live in
# icons/gcn_hsi/ and are shown beside the live read-off in the dialog.
# ---------------------------------------------------------------------------

POND_AREA = [       # SI2 — x = surface area (m^2). <50 -> 0.05; >2000 -> omit.
    (50, 0.10), (100, 0.20), (200, 0.40), (300, 0.60), (400, 0.80),
    (500, 1.00), (700, 1.00), (1000, 0.95), (1200, 0.92), (1600, 0.86), (2000, 0.80),
]

SHADE = [           # SI5 — x = % of perimeter shaded
    (0, 1.00), (60, 1.00), (70, 0.90), (80, 0.70), (90, 0.50), (100, 0.20),
]

POND_DENSITY = [    # SI8 — x = ponds per km^2 (= ponds within 1 km / pi). Log x-axis.
    (0.1, 0.10), (0.2, 0.20), (0.3, 0.27), (0.5, 0.40), (0.7, 0.50), (1.0, 0.60),
    (1.5, 0.70), (2.0, 0.77), (3.0, 0.87), (4.0, 0.95), (5.0, 1.00), (10.0, 1.00),
]

MACROPHYTE = [      # SI10 — x = % of pond surface with macrophyte cover
    (0, 0.30), (10, 0.40), (20, 0.50), (30, 0.60), (40, 0.70), (50, 0.80),
    (60, 0.90), (70, 0.97), (78, 1.00), (82, 1.00), (90, 0.93), (100, 0.80),
]


# ---------------------------------------------------------------------------
# Factor table — drives the dialog. One entry per SI, in calculation order.
# ---------------------------------------------------------------------------
# Common keys: key, n (SI number), title, type ('cat'|'graph'), note (help text).
#   cat:   options = LOCATION/...; (desktop=True flags a map/desk exercise)
#   graph: curve, unit, xmin/xmax/xlabel/log (axis), ref (guide PNG); plus
#          count=True for SI8 (input is a pond count, converted to density);
#          ref2/ref2_label for a second reference image (SI10 cover circles).

FACTORS = [
    {"key": "SI1", "n": 1, "title": "Geographic location", "type": "cat",
     "options": LOCATION, "desktop": True, "ref": "si1_zone_map.png",
     "ref_label": "Location zones map (A / B / C)",
     "note": "Score the zone the site falls in (desktop exercise). On a boundary, take the "
             "middle value (Zone B)."},

    {"key": "SI2", "n": 2, "title": "Pond area", "type": "graph", "curve": POND_AREA,
     "unit": "m²", "xmin": 0, "xmax": 2000, "xlabel": "Pond area (m²)",
     "ref": "si2_pond_area.png", "step": 50, "maxinput": 100000,
     "note": "Surface area at highest spring level, in steps of 50 m². "
             "Under 50 m² scores 0.05; over 2000 m² holds the 2000 m² score (0.80)."},

    {"key": "SI3", "n": 3, "title": "Pond permanence", "type": "cat", "options": PERMANENCE,
     "note": "How often the pond dries — from local knowledge or judgement."},

    {"key": "SI4", "n": 4, "title": "Water quality", "type": "cat", "options": WATER_QUALITY,
     "note": "Subjective, based on invertebrate diversity and submerged plants — not water clarity."},

    {"key": "SI5", "n": 5, "title": "Shade", "type": "graph", "curve": SHADE,
     "unit": "%", "xmin": 0, "xmax": 100, "xlabel": "Shoreline shade (%)",
     "ref": "si5_shade.png", "step": 5, "maxinput": 100,
     "note": "Percentage of the perimeter shaded to at least 1 m from shore (May–Sept)."},

    {"key": "SI6", "n": 6, "title": "Waterfowl", "type": "cat", "options": WATERFOWL,
     "note": "Impact of ducks, geese and swans (exclude moorhens)."},

    {"key": "SI7", "n": 7, "title": "Fish", "type": "cat", "options": FISH,
     "note": "From local knowledge and observation/netting."},

    {"key": "SI8", "n": 8, "title": "Pond density", "type": "graph", "curve": POND_DENSITY,
     "unit": "ponds", "xmin": 0.1, "xmax": 10, "xlabel": "Ponds / km²", "log": True,
     "ref": "si8_pond_density.png", "count": True, "step": 1, "maxinput": 1000,
     "note": "Ponds within 1 km of the survey pond (exclude the survey pond and any beyond "
             "major barriers); density = count ÷ π. Desktop exercise."},

    {"key": "SI9", "n": 9, "title": "Terrestrial habitat", "type": "cat", "options": TERRESTRIAL,
     "note": "Quality of habitat within ~250 m, on the near side of major barriers."},

    {"key": "SI10", "n": 10, "title": "Macrophyte cover", "type": "graph", "curve": MACROPHYTE,
     "unit": "%", "xmin": 0, "xmax": 100, "xlabel": "Macrophyte cover (%)",
     "ref": "si10_macrophyte.png", "ref2": "macrophyte_cover_circles.png",
     "ref2_label": "Cover estimation guide (match your pond)", "step": 10, "maxinput": 100,
     "note": "Percentage of the pond surface with macrophytes — emergent, floating (excluding "
             "duckweed) and submerged plants reaching the surface (Mar–Sept)."},
]

FACTOR_BY_KEY = {f["key"]: f for f in FACTORS}


# Suitability bands — (upper-exclusive threshold, label, range text). The final
# band has threshold None (>= 0.8).
BANDS = [
    (0.50, "Poor", "< 0.5"),
    (0.60, "Below average", "0.5 – 0.59"),
    (0.70, "Average", "0.6 – 0.69"),
    (0.80, "Good", "0.7 – 0.79"),
    (None, "Excellent", "≥ 0.8"),
]


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------

def interp(curve, x):
    """Piecewise-linear read-off of a digitised SI curve, clamped to its ends."""
    if x <= curve[0][0]:
        return curve[0][1]
    if x >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return curve[-1][1]


def density_for_count(count):
    """Ponds within 1 km -> ponds per km^2 (the SI8 graph's x-axis)."""
    return count / PI


def graph_si(factor, measurement):
    """SI for a graph factor from a raw field measurement. Returns (si, False).

    The second element is kept for backwards compatibility (it used to flag SI2's
    'omit' case); pond area > 2000 m^2 now simply holds the 2000 m^2 score (0.80),
    since interp() clamps to the flat top of the curve.
    """
    if measurement is None:
        return None, False
    key = factor["key"]
    if key == "SI2":
        if measurement < 50:
            return 0.05, False
        return round(interp(POND_AREA, measurement), 3), False   # >=2000 -> 0.80
    if key == "SI8":
        return round(interp(POND_DENSITY, density_for_count(measurement)), 3), False
    return round(interp(factor["curve"], measurement), 3), False


def compute_hsi(si_values):
    """Geometric mean of the supplied SI values.

    *si_values* is a dict {key: si | None | OMIT}. None = not yet set; OMIT = SI2
    dropped (pond > 2000 m^2). Returns (hsi | None, n_used, complete) where
    *complete* is True only when every factor is resolved (set or omitted).
    """
    used = []
    complete = True
    for f in FACTORS:
        v = si_values.get(f["key"])
        # Equality is intentional: project persistence serialises this sentinel
        # through JSON, so the restored string is not guaranteed to be the same
        # Python object as the module constant.
        if v == OMIT:
            continue
        if v is None:
            complete = False
            continue
        used.append(max(0.01, min(1.0, v)))
    if not complete or not used:
        return None, len(used), False
    prod = 1.0
    for v in used:
        prod *= v
    return prod ** (1.0 / len(used)), len(used), True


def band_for(hsi):
    """Lee Brady suitability band label for an HSI score (None -> None)."""
    if hsi is None:
        return None
    for thr, label, _rng in BANDS:
        if thr is None or hsi < thr:
            return label
    return "Excellent"
