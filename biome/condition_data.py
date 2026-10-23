# -*- coding: utf-8 -*-
"""
condition_data.py
=================

Computable habitat condition-assessment criteria for the in-form Condition
helper.  Replaces the old display-only ``CONDITION_CRITERIA`` text dump: each
habitat now resolves to a *condition sheet* (the statutory metric organises
condition by sheet, not by sub-habitat) carrying

    * the criteria (concise wording, verified against the Statutory Biodiversity
      Metric "Condition Assessments" workbook, July 2025),
    * which criteria are ESSENTIAL,
    * the scoring MODEL and grade thresholds, and
    * the sheet's footnotes (verbatim, from condition_footnotes.py).

The helper panel renders this and, as the surveyor ticks / scores criteria,
``evaluate()`` returns the resulting Good / Moderate / Poor grade.  The form
uses that only to *set* the Condition dropdown as a convenience — it never locks
it; the surveyor can still choose any value.

Scoring models
--------------
* ``count``    – tick pass/fail; grade by number passed, with optional essential
                 gates (e.g. grassland needs A; meadows need A and F for Good).
* ``urban``    – 3 core criteria + a habitat-specific additional one; criterion
                 C has two levels (<5% = pass, complete absence = "fully met",
                 required for Good).
* ``wetland``  – 6 core criteria + one habitat-specific additional; essential A.
* ``hedgerow`` – 8 (or 10 with trees) attributes in functional groups; grade by
                 failure count + per-group rule.
* ``pond``     – 7 core criteria; non-woodland ponds add H + I (woodland /
                 non-woodland toggle).
* ``score``    – woodland: 13 indicators each scored Good/Moderate/Poor (3/2/1),
                 summed out of 39.
* ``note``     – no checklist (rivers/canals = RCA, lakes = naturalness class,
                 culvert = fixed Poor); informational text only.
"""

from .condition_footnotes import FOOTNOTES


# ---------------------------------------------------------------------------
# Per-sheet criteria + rules
# ---------------------------------------------------------------------------
# Each sheet: model, criteria [(letter, text)], essential [letters], and for
# the ``count`` model the bands [(grade, min_passes, [required_letters])]
# (evaluated best-first) plus human-readable threshold labels.

SHEETS = {

    "trees": {
        "model": "count",
        "criteria": [
            ("A", "Native species (or ≥70% native in a block)"),
            ("B", "Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)"),
            ("C", "Mature tree (or >50% mature in a block)"),
            ("D", "Little/no adverse human impact; no regular pruning; retains >75% expected canopy"),
            ("E", "Natural ecological niches present (deadwood, cavities, ivy, loose bark)"),
            ("F", "More than 20% of canopy area oversails vegetation beneath"),
        ],
        "essential": [],
        "bands": [("Good", 5, []), ("Moderate", 3, []), ("Poor", 0, [])],
        "labels": [("Good", "Pass 5 or 6 of 6"), ("Moderate", "Pass 3 or 4 of 6"), ("Poor", "Pass 2 or fewer")],
    },

    "line_trees": {
        "model": "count",
        "criteria": [
            ("A", "≥70% of trees are native species"),
            ("B", "Canopy predominantly continuous — gaps <10% of area; no gap >5 m"),
            ("C", "≥1 tree has veteran features / natural niches (deadwood, cavities, ivy, loose bark)"),
            ("D", "Undisturbed naturally-vegetated strip ≥6 m on both sides (excl. grazing); root protection for veteran trees"),
            ("E", "≥95% of trees in healthy condition; little adverse impact"),
        ],
        "essential": [],
        "bands": [("Good", 5, []), ("Moderate", 3, []), ("Poor", 0, [])],
        "labels": [("Good", "Pass all 5"), ("Moderate", "Pass 3 or 4 of 5"), ("Poor", "Pass 2 or fewer")],
    },

    "scrub": {
        "model": "count",
        "criteria": [
            ("A", "Good example; ≥80% native scrub; ≥3 native woody species; no single species >75% cover (except hazel, juniper, sea buckthorn in native range, or box)"),
            ("B", "Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present"),
            ("C", "No invasive non-native species (Sch. 9 WCA); suboptimal species <5% ground cover"),
            ("D", "Well-developed edge with scattered scrub and tall grassland/forbs"),
            ("E", "Clearings, glades or rides present within the scrub"),
        ],
        "essential": [],
        "bands": [("Good", 5, []), ("Moderate", 3, []), ("Poor", 0, [])],
        "labels": [("Good", "Pass all 5"), ("Moderate", "Pass 3 or 4 of 5"), ("Poor", "Pass 2 or fewer")],
    },

    "ditch": {
        "model": "count",
        "criteria": [
            ("A", "Good water quality; clear water (low turbidity); no obvious pollution"),
            ("B", "Range of emergent, submerged & floating-leaved plants (guide: >10 species per 20 m)"),
            ("C", "<10% cover of filamentous algae and/or duckweed"),
            ("D", "Aquatic marginal vegetation fringe along >75% of the ditch"),
            ("E", "Physical damage along <5% of the ditch"),
            ("F", "Sufficient water levels (guide: ≈50 cm minor ditches, 1 m main drains)"),
            ("G", "<10% of the ditch heavily shaded"),
            ("H", "Absence of non-native plant and animal species"),
        ],
        "essential": [],
        "bands": [("Good", 8, []), ("Moderate", 6, []), ("Poor", 0, [])],
        "labels": [("Good", "Pass all 8"), ("Moderate", "Pass 6 or 7"), ("Poor", "Pass 5 or fewer")],
    },

    "orchard": {
        "model": "count",
        "criteria": [
            ("A", "Ancient and/or veteran trees present"),
            ("B", "Deadwood in/on trees — ≥20% of mature trees have associated deadwood"),
            ("C", "<5% of fruit trees smothered by scrub; dense scrub <10% ground cover"),
            ("D", "Evidence of formative/restorative pruning to maintain tree longevity"),
            ("E", "≥95% of trees free from human/animal damage"),
            ("F", "Grassland not overgrazed; poaching under canopy <10% of trees"),
            ("G", "Grassland species richness equivalent to medium/high/very high distinctiveness"),
            ("H", "No invasive non-native species (Sch. 9 WCA); suboptimal species <10% ground cover"),
        ],
        "essential": ["A", "B"],
        "bands": [("Good", 6, ["A", "B"]), ("Moderate", 4, []), ("Poor", 0, [])],
        "labels": [("Good", "Pass 6–8 incl. A and B"), ("Moderate", "Pass 4 or 5, or 6–7 failing A or B"), ("Poor", "Pass 3 or fewer")],
    },

    "heathland": {
        "model": "count",
        "criteria": [
            ("A", "Good example; characteristic vascular & non-vascular indicator species present"),
            ("B", "≥2 dwarf shrub species Frequent; dwarf-shrub cover 25–75% (lowland) / 50–75% (upland dry) / >20% (upland wet)"),
            ("C", "All heather age-classes present; ≥10% pioneer (lowland) or ≥10% degenerate/mature (upland)"),
            ("D", "Unshaded bare ground 1–10%"),
            ("E", "No invasive non-native species (Sch. 9 WCA) and no shallon"),
            ("F", "No disturbance of sensitive areas, including managed burns"),
            ("G", "≤33% of heather shoots recently grazed, OR flowering heather ≥Frequent in autumn"),
            ("H", "Scattered tree/scrub canopy <20% (upland) / <15% (lowland dry) / <10% (lowland wet)"),
            ("I", "Total gorse cover <50%; common gorse <25%"),
            ("J", "Bracken cover <5%"),
            ("K", "No damaging activities or contamination (drains, peat extraction, silt, leachate, eutrophication)"),
        ],
        "essential": ["A", "B", "C", "D", "E"],
        "bands": [("Good", 9, ["A", "B", "C", "D", "E"]), ("Moderate", 7, []), ("Poor", 0, [])],
        "labels": [("Good", "Pass 9–11 incl. all of A–E"), ("Moderate", "Pass 7 or 8, or 9–10 failing an essential"), ("Poor", "Pass 6 or fewer")],
    },

    "woodpasture": {
        "model": "count",
        "criteria": [
            ("A", "Ancient and/or veteran trees present"),
            ("B", "Three life-stages (young, mature, veteran) of open-grown/pollarded trees present"),
            ("C", "Native scrub present with varied heights, widths, shapes and species"),
            ("D", "Frequent decaying wood providing ecological niches (deadwood, heart-rot, hollowing)"),
            ("E", "No recent adverse impact on tree health (human, livestock, pests, disease)"),
            ("F", "Ground cover open habitat (grassland/heathland), unimproved/semi-improved (medium+ distinctiveness)"),
            ("G", "Ground cover under appropriate management; not threatened by tree/scrub infill or plantation"),
            ("H", "No invasive non-native species (Sch. 9 WCA); suboptimal species <5% cover"),
        ],
        "essential": ["A"],
        "bands": [("Good", 7, ["A"]), ("Moderate", 5, []), ("Poor", 0, [])],
        "labels": [("Good", "Pass 7 or 8 incl. A"), ("Moderate", "Pass 5 or 6, or 7 failing A"), ("Poor", "Pass 4 or fewer")],
    },

    "grass_low": {
        "model": "count",
        "criteria": [
            ("A", "6–8 vascular plant species per m² incl. ≥2 forbs"),
            ("B", "Sward height varied (≥20% <7 cm AND ≥20% >7 cm)"),
            ("C", "Scrub <20% of the grassland area"),
            ("D", "Physical damage <5% of area"),
            ("E", "Bare ground 1–10%"),
            ("F", "Bracken cover <20%"),
            ("G", "No invasive non-native species (Sch. 9 WCA)"),
        ],
        "essential": ["A"],
        "bands": [("Good", 6, ["A"]), ("Moderate", 4, ["A"]), ("Poor", 0, [])],
        "labels": [("Good", "Pass 6 or 7 incl. A"), ("Moderate", "Pass 4 or 5 incl. A"), ("Poor", "Pass 3 or fewer, or failing A")],
    },

    # Medium/high/very-high grassland sheet covers both a non-acid variant (6
    # criteria A-F, A & F essential) and an acid variant (5 criteria A-E, no
    # essentials).  The habitat mapping selects the variant via params.
    "grass_medhigh": {
        "model": "count",
        "criteria": [
            ("A", "Good example; characteristic indicator species consistently present"),
            ("B", "Sward height varied (≥20% <7 cm AND ≥20% >7 cm)"),
            ("C", "Bare ground 1–5%"),
            ("D", "Bracken <20% AND scrub (incl. bramble) <5%"),
            ("E", "Suboptimal species + physical damage <5%; any invasive non-native species → automatic fail"),
            ("F", "≥10 vascular plant species per m² incl. characteristic forbs"),
        ],
        # variant-specific essentials/bands/labels handled in panel_for / evaluate
    },

    "urban": {
        "model": "urban",
        "criteria": [
            ("A", "Varied vegetation structure; no single component >80% of area"),
            ("B", "Plant species beneficial for wildlife (e.g. nectar sources across the seasons)"),
            ("C", "Invasive non-native species <5% of vegetated area"),
            ("D", "Mosaic of bare substrate PLUS ≥4 early-successional communities"),
            ("E1", "Plant species mostly native; any non-native not detrimental"),
            ("E2", "Vegetation suited to wetland/riparian situations"),
            ("F", "Roof ≥50% native/non-native wildflowers; ≥70% soil & vegetation"),
            ("G", "Roof depth 80–150 mm (≥50% at 150 mm), planted/seeded with wildflowers & sedums"),
        ],
        "essential": [],
        # labels built in panel_for (depend on how many criteria the habitat uses)
    },

    "wetland": {
        "model": "wetland",
        "criteria": [
            ("A", "Water table at/near surface year-round; no artificial drainage"),
            ("B", "Good example; characteristic indicator species present"),
            ("C", "Good water quality (low turbidity, no obvious pollution)"),
            ("D", "Scrub and scattered trees <10%"),
            ("E", "Bare ground <5%"),
            ("F", "No invasive non-native species (Sch. 9 WCA); suboptimal species <5% ground cover"),
            ("G", "≤25% continuous litter cover preventing regeneration"),
            ("H", "Sphagnum & cottongrasses ≥Frequent; ericaceous dwarf shrubs <75%"),
            ("I", "Reedbed diverse: 60–80% reeds; open water ≥10%, species-rich fen and/or wet woodland"),
            ("J", "All ditches achieve Good condition (Ditch condition sheet)"),
        ],
        "essential": ["A"],
        "core": ["A", "B", "C", "D", "E", "F"],
        "labels": [("Good", "Pass 5–6 core incl. A, plus the additional criterion"),
                   ("Moderate", "Pass 3–4 core, or 5 core failing A, or fail the additional"),
                   ("Poor", "Pass ≤3 criteria")],
    },

    "hedgerow": {
        "model": "hedgerow",
        "criteria": [
            ("A1", "Height >1.5 m average along length"),
            ("A2", "Width >1.5 m average at widest point of canopy"),
            ("B1", "Gap at hedge base <0.5 m for >90% of length"),
            ("B2", "Canopy gaps <10% of total length; no individual gap >5 m"),
            ("C1", "≥1 m undisturbed ground with perennial vegetation for >90% of length (one side min.)"),
            ("C2", "Nutrient-enriched species <20% cover of undisturbed ground"),
            ("D1", "≥90% free of invasive non-native and neophyte species"),
            ("D2", "≥90% free of damage from human activities"),
            ("E1", ">1 age-class of tree; ≥1 mature/ancient/veteran tree per 20–50 m"),
            ("E2", "≥95% of hedgerow trees in healthy condition; little adverse impact"),
        ],
        "groups": {"A": "Height & width", "B": "Gaps", "C": "Ground & vegetation",
                   "D": "Protection", "E": "Trees"},
        "essential": [],
        "labels": [("Good", "≤2 failures total; ≤1 per functional group"),
                   ("Moderate", "≤4 failures (≤5 with trees); not both attributes in >1 group"),
                   ("Poor", ">4 failures (>5 with trees), or both attributes in >1 group")],
    },

    "pond": {
        "model": "pond",
        "criteria": [
            ("A", "Good water quality; clear water (low turbidity); no obvious pollution (turbidity OK if grazed)"),
            ("B", "Semi-natural habitat (medium+ distinctiveness) surrounding pond ≥10 m for entire perimeter"),
            ("C", "<10% of water surface covered by duckweed or filamentous algae"),
            ("D", "Pond not artificially connected to other waterbodies (ditches, pipework)"),
            ("E", "Water levels fluctuate naturally; no artificial dams, pumps or pipework"),
            ("F", "Absence of listed non-native plant and animal species"),
            ("G", "Pond not artificially stocked with fish (or native assemblage at low density)"),
            ("H", "Emergent/submerged/floating plants (excl. duckweed) ≥50% of pond area <3 m deep"),
            ("I", "Pond surface ≤50% shaded by adjacent trees and scrub"),
        ],
        "core": ["A", "B", "C", "D", "E", "F", "G"],
        "extra": ["H", "I"],
        "essential": [],
    },

    "woodland": {
        "model": "score",
        # (letter, indicator name, (Good descriptor, Moderate descriptor, Poor descriptor))
        "indicators": [
            ("A", "Age distribution of trees", ("Three age-classes present", "Two age-classes present", "One age-class present")),
            ("B", "Wild/domestic/feral herbivore damage", ("No significant browsing", "Browsing in <40% of woodland", "Browsing in ≥40% of woodland")),
            ("C", "Invasive plant species", ("None present", "No rhododendron/cherry laurel; others <10%", "Rhododendron/cherry laurel present, or others ≥10%")),
            ("D", "Number of native tree species", ("Five or more", "Three or four", "Two or fewer")),
            ("E", "Cover of native tree & shrub species", ("Canopy & understorey >80% native", "50–80% native", "<50% native")),
            ("F", "Open space within woodland", ("10–20% temporary (or <10 ha: 0–20%)", "21–40%", "<10% or >40%")),
            ("G", "Woodland regeneration", ("All three classes present", "One or two classes", "None present")),
            ("H", "Tree health", ("≤10% mortality; no pests/disease", "11–25% or low-risk pest/disease", ">25% or any high-risk pest/disease")),
            ("I", "Vegetation & ground flora", ("NVC community + ancient-woodland specialists", "Recognisable woodland NVC", "No recognisable woodland NVC")),
            ("J", "Woodland vertical structure", ("Three+ storeys, or complex", "Two storeys", "One or fewer storeys")),
            ("K", "Veteran trees", ("Two or more per hectare", "One per hectare", "None present")),
            ("L", "Amount of deadwood", ("≥50% of plots have deadwood", "25–50% of plots", "<25% of plots")),
            ("M", "Woodland disturbance", ("None evident", "<1 ha enrichment or <20% ground damaged", "≥1 ha enrichment or ≥20% damaged")),
        ],
        "bands": [(33, "Good"), (26, "Moderate"), (0, "Poor")],
        "labels": [("Good", "Total ≥33 / 39"), ("Moderate", "Total 26–32"), ("Poor", "Total ≤25")],
    },

    # ── note-only sheets (no checklist) ──────────────────────────────────────
    "rca": {
        "model": "note",
        "note": ("No pass/fail checklist applies. A River Condition Assessment "
                 "(RCA) is required — assess using the separate RCA methodology "
                 "in the Statutory Biodiversity Metric User Guide."),
    },
    "lake": {
        "model": "lake",
        # The statutory lake sheet uses the Freshwater Biological Association
        # Habitat Naturalness Assessment.  Each of the four components receives
        # a class from 1 (natural) to 5 (least natural); their arithmetic mean is
        # rounded to the nearest class and translated to the five metric grades.
        "attributes": [
            ("P", "Physical naturalness",
             "Shoreline condition, riparian land within 10 m and, for artificial lakes, morphology"),
            ("H", "Hydrological naturalness",
             "Structures, water-level fluctuations, inflows, outflows and surrounding drainage"),
            ("C", "Chemical naturalness",
             "Water clarity, algae, submerged plants and available water-quality evidence"),
            ("B", "Biological naturalness",
             "Extent and abundance of non-native plant and animal species"),
        ],
        "labels": [
            ("Good", "Average HNA Class 1 — Natural"),
            ("Fairly Good", "Average HNA Class 2"),
            ("Moderate", "Average HNA Class 3"),
            ("Fairly Poor", "Average HNA Class 4"),
            ("Poor", "Average HNA Class 5 — Least natural"),
        ],
        "note": ("Assign one Habitat Naturalness Assessment class to each of "
                 "the four attributes using the FBA lake naturalness guidance. "
                 "Where an attribute has several sub-categories or methods, use "
                 "the worst-performing applicable class."),
    },
    "culvert": {
        "model": "note",
        "note": ("Condition is fixed at Poor for culverts — the metric marks "
                 "every grade except Poor as 'Not Possible'."),
    },
}


# ---------------------------------------------------------------------------
# Habitat sub-type -> (sheet key, params)
# ---------------------------------------------------------------------------
# params keys: 'acid' (grass_medhigh), 'additional' (urban list / wetland str),
# 'with_trees' (hedgerow, derived), 'footnotes' override.

_TREES = ["Small Rural Tree", "Medium Rural Tree", "Large Rural Tree", "Very Large Rural Tree",
          "Small Urban Tree", "Medium Urban Tree", "Large Urban Tree", "Very Large Urban Tree"]
_SCRUB = ["Blackthorn scrub", "Gorse scrub", "Hawthorn scrub", "Hazel scrub", "Mixed scrub",
          "Dunes with sea buckthorn (H2160)", "Willow scrub"]
_GRASS_NONACID = ["Lowland calcareous grassland", "Lowland meadows", "Other neutral grassland",
                  "Upland calcareous grassland", "Upland hay meadows", "Tall herb communities (H6430)",
                  "Calaminarian grasslands"]
_GRASS_ACID = ["Lowland dry acid grassland", "Other lowland acid grassland", "Upland acid grassland"]
_HEATH = ["Lowland heathland", "Upland heathland", "Mountain heaths and willow scrub"]
_WOODLAND = ["Lowland mixed deciduous woodland", "Lowland beech and yew woodland",
             "Other woodland; broadleaved", "Other woodland; mixed", "Wet woodland",
             "Upland oakwood", "Upland mixed ashwoods", "Upland birchwoods",
             "Native pine woodlands", "Other coniferous woodland", "Other Scot's pine woodland"]
_LAKES = ["Aquifer fed naturally fluctuating water bodies", "High alkalinity lakes",
          "Low alkalinity lakes", "Moderate alkalinity lakes", "Marl lakes", "Peat lakes",
          "Reservoirs", "Ornamental lake or pond"]

# Concise field prompts derived from the Lake Naturalness Assessment Guidance
# (Natural England, 2019). These support judgement; they do not replace the
# full guidance or appropriate water-quality/species evidence.
LAKE_CLASS_GUIDANCE = {
    "P": [
        "No shoreline modification; all land within 10 m is semi-natural; artificial edges gently shelve and support plants.",
        "Modification affects no more than 5% of the shoreline; about 90% of the 10 m riparian zone is semi-natural; artificial margins permit plant colonisation for at least 10 m.",
        "Modification affects no more than one-third of the shoreline; at least two-thirds of the riparian zone is semi-natural; artificial margins permit plant colonisation for at least 3 m.",
        "Modification affects up to two-thirds of the shoreline; at least one-third of the riparian zone is semi-natural; steep margins substantially restrict aquatic plants.",
        "More than two-thirds of the shoreline is reinforced; less than one-third of the riparian zone is semi-natural; marginal wetland is absent or artificial margins support little or no plant habitat.",
    ],
    "H": [
        "No water-level structures or barriers; natural seasonal fluctuation; natural inflows and outflows, with no surrounding drainage ditches.",
        "Structures are generally fish-passable; water levels fluctuate naturally or mimic a moderate seasonal regime; inflows or outflows have only limited modification and no additional ditches enter.",
        "A structure is impassable to most fish for most of the time, water levels are fixed, or modified outflows and drainage ditches materially alter the lake.",
        "A large structure is impassable to all fish, or abstraction causes considerable annual drawdown of less than 2 m.",
        "Very large impassable structures are present, or annual drawdown exceeds 2 m; typical of water-supply reservoirs or hydro-electric schemes.",
    ],
    "C": [
        "Clear through more than 3 m; negligible algae; submerged plants reach at least 3 m where substrate allows; no pollution evidence from available testing.",
        "Clear through more than 1 m and up to 3 m; occasional, non-persistent algae; submerged plants generally extend beyond 1 m; only low pollution evidence.",
        "Clear through more than 0.5 m and up to 1 m; moderate filamentous algae or seasonal blooms; submerged plants limited or dominated by nutrient-tolerant species; moderate pollution evidence.",
        "Brown or green water, clear through only 0.25–0.5 m; frequent blooms or extensive filamentous algae; submerged plants very sparse; high pollution evidence.",
        "Brown or green water, with visibility below 0.25 m; frequent blooms or extensive filamentous algae; no submerged plants; very high or major pollution evidence.",
    ],
    "B": [
        "No evidence of non-native plants or animals in the lake or riparian zone.",
        "Non-native plants occupy no more than 5% of shoreline or lake area; non-native animals are rarely encountered and have no obvious impact.",
        "Non-native plants occupy up to 25% of the shoreline, or at least one non-native animal is found using an appropriate search method.",
        "Non-native plants occupy up to 60% of the shoreline, or multiple non-native animals are found when searched for.",
        "Non-native plants occupy more than 60% of shoreline or lake area, or non-native animals are numerous and found with little effort.",
    ],
}
# Urban: habitat -> additional criteria beyond the 3 core (A, B, C)
_URBAN_ADD = {
    "Allotments": [], "Cemeteries and churchyards": [], "Facade-bound green wall": [],
    "Ground based green wall": [], "Rain garden": [], "Vacant or derelict land": [],
    "Bare ground": [], "Ruderal/Ephemeral": [], "Tall forbs": [],
    "Open mosaic habitats on previously developed land": ["D"],
    "Bioswale": ["E1", "E2"], "Sustainable drainage system": ["E1", "E2"],
    "Intensive green roof": ["F"], "Biodiverse green roof": ["G"],
}
# Wetland: habitat -> the single additional criterion
_WETLAND_ADD = {
    "Blanket bog": "H", "Lowland raised bog": "H",
    "Fens (upland and lowland)": "G", "Purple moor grass and rush pastures": "G",
    "Reedbeds": "I", "Floodplain wetland mosaic and CFGM": "J",
}

HABITAT_SHEET = {}
for _h in _TREES:
    HABITAT_SHEET[_h] = ("trees", {})
for _h in _SCRUB:
    HABITAT_SHEET[_h] = ("scrub", {})
for _h in _GRASS_NONACID:
    HABITAT_SHEET[_h] = ("grass_medhigh", {"acid": False})
for _h in _GRASS_ACID:
    HABITAT_SHEET[_h] = ("grass_medhigh", {"acid": True})
for _h in _HEATH:
    HABITAT_SHEET[_h] = ("heathland", {})
for _h in _WOODLAND:
    HABITAT_SHEET[_h] = ("woodland", {})
for _h in _LAKES:
    HABITAT_SHEET[_h] = ("lake", {})
for _h, _add in _URBAN_ADD.items():
    HABITAT_SHEET[_h] = ("urban", {"additional": _add})
for _h, _add in _WETLAND_ADD.items():
    HABITAT_SHEET[_h] = ("wetland", {"additional": _add})
HABITAT_SHEET["Modified grassland"] = ("grass_low", {})
HABITAT_SHEET["Traditional orchards"] = ("orchard", {})
HABITAT_SHEET["Wood-pasture and parkland"] = ("woodpasture", {})
HABITAT_SHEET["Ditches"] = ("ditch", {})
HABITAT_SHEET["Ponds (priority habitat)"] = ("pond", {})
HABITAT_SHEET["Ponds (non-priority habitat)"] = ("pond", {})
HABITAT_SHEET["Other rivers and streams"] = ("rca", {})
HABITAT_SHEET["Priority habitat"] = ("rca", {})
HABITAT_SHEET["Canals"] = ("rca", {})
HABITAT_SHEET["Culvert"] = ("culvert", {})


def _resolve(sub):
    """Return (sheet_dict, sheet_key, params) for a sub-habitat, or (None, None, None).
    Hedgerows and lines of trees are matched by name so every variant (including
    '- associated with bank or ditch') resolves without listing each one."""
    if sub in HABITAT_SHEET:
        key, params = HABITAT_SHEET[sub]
        return SHEETS.get(key), key, dict(params)
    low = sub.lower()
    if "hedgerow" in low:
        return SHEETS["hedgerow"], "hedgerow", {"with_trees": "with trees" in low}
    if "line of trees" in low:
        return SHEETS["line_trees"], "line_trees", {}
    return None, None, None


def has_panel(sub):
    """True if a condition helper panel is available for this sub-habitat."""
    sheet, _key, _p = _resolve(sub)
    return sheet is not None


# ---------------------------------------------------------------------------
# Panel description for the renderer
# ---------------------------------------------------------------------------
def panel_for(sub):
    """Return a render-ready description of the condition panel for *sub*, or
    None.  Shape:

        {
          "model":   "count"|"urban"|"wetland"|"hedgerow"|"pond"|"score"|"note",
          "criteria":[(letter, text, is_essential)],   # tick models
          "groups":  {letter: group label},            # hedgerow only
          "indicators":[(letter, name, (g, m, p))],     # score model only
          "labels":  [(grade, threshold text)],
          "footnotes":[...],
          "note":    str,                               # note model only
          "needs_c_absent": bool,                       # urban only
          "key": sheet key, "params": params,
        }
    """
    sheet, key, params = _resolve(sub)
    if sheet is None:
        return None
    model = sheet["model"]
    foots = FOOTNOTES.get(key, [])
    out = {"model": model, "key": key, "params": params, "footnotes": foots}

    if model == "note":
        out["note"] = sheet["note"]
        return out

    if model == "lake":
        out["attributes"] = sheet["attributes"]
        out["guidance"] = LAKE_CLASS_GUIDANCE
        out["labels"] = sheet["labels"]
        out["note"] = sheet["note"]
        return out

    if model == "score":
        out["indicators"] = sheet["indicators"]
        out["labels"] = sheet["labels"]
        return out

    if model == "hedgerow":
        with_trees = params.get("with_trees", False)
        letters = [l for (l, _t) in sheet["criteria"] if with_trees or not l.startswith("E")]
        out["criteria"] = [(l, t, False) for (l, t) in sheet["criteria"] if l in letters]
        out["groups"] = sheet["groups"]
        out["labels"] = sheet["labels"]
        return out

    if model == "urban":
        used = ["A", "B", "C"] + list(params.get("additional", []))
        ess = set(sheet.get("essential", []))
        out["criteria"] = [(l, t, l in ess) for (l, t) in sheet["criteria"] if l in used]
        out["needs_c_absent"] = True
        n = len(used)
        out["labels"] = [
            ("Good", "All %d criteria pass + criterion C met by complete absence of invasive species" % n),
            ("Moderate", "Short of Good (e.g. one criterion fails, or C only <5%)"),
            ("Poor", "%s or fewer criteria pass" % ("Two" if n >= 5 else "One")),
        ]
        return out

    if model == "wetland":
        add = params.get("additional")
        core = sheet["core"]
        used = core + ([add] if add else [])
        ess = set(sheet.get("essential", []))
        out["criteria"] = [(l, t, l in ess) for (l, t) in sheet["criteria"] if l in used]
        out["labels"] = sheet["labels"]
        return out

    if model == "pond":
        # default render = non-woodland (all 9); the panel offers a toggle
        out["criteria"] = [(l, t, False) for (l, t) in sheet["criteria"]]
        out["core"] = sheet["core"]
        out["extra"] = sheet["extra"]
        out["labels"] = [
            ("Good", "Pass all — 7 core (woodland pond) or 9 (non-woodland)"),
            ("Moderate", "5–6 (woodland) or 6–8 (non-woodland)"),
            ("Poor", "≤4 (woodland) or ≤5 (non-woodland)"),
        ]
        return out

    # ── plain count model (with optional acid variant for grassland) ─────────
    if key == "grass_medhigh":
        if params.get("acid"):
            crit = [(l, t) for (l, t) in sheet["criteria"] if l != "F"]   # A-E only
            ess = []
            out["labels"] = [("Good", "Pass all 5"), ("Moderate", "Pass 3 or 4 of 5"), ("Poor", "Pass 2 or fewer")]
        else:
            crit = list(sheet["criteria"])                                # A-F
            ess = ["A", "F"]
            out["labels"] = [("Good", "Pass 5 or 6 incl. A and F"),
                             ("Moderate", "Pass 3–5 incl. A"),
                             ("Poor", "Pass 2 or fewer, or 3–4 without A and F")]
        out["criteria"] = [(l, t, l in ess) for (l, t) in crit]
        return out

    ess = set(sheet.get("essential", []))
    out["criteria"] = [(l, t, l in ess) for (l, t) in sheet["criteria"]]
    out["labels"] = sheet["labels"]
    return out


# ---------------------------------------------------------------------------
# Grade evaluation
# ---------------------------------------------------------------------------
def evaluate(sub, state):
    """Return the Good/Moderate/Poor grade for the current panel *state*, or
    None when the grade can't be determined yet (woodland not fully scored, or a
    note-only habitat).  *state* shape depends on the model:

        count/urban/wetland/hedgerow : {"passed": set(letters), "c_absent": bool}
        pond                         : {"passed": set(letters), "variant": "woodland"|"nonwoodland"}
        score                        : {"scores": {letter: 1|2|3}}
        lake                         : {"classes": {letter: 1|2|3|4|5}}
    """
    sheet, key, params = _resolve(sub)
    if sheet is None:
        return None
    model = sheet["model"]
    passed = set(state.get("passed", ()))

    if model == "note":
        return None

    if model == "lake":
        classes = state.get("classes", {})
        attrs = sheet["attributes"]
        vals = [classes.get(letter) for letter, _name, _desc in attrs]
        if any(v not in (1, 2, 3, 4, 5) for v in vals):
            return None
        # Naturalness classes are whole classes on the statutory lake sheet.
        # Round .5 upward rather than Python's banker's rounding.
        avg_class = int((sum(vals) / float(len(vals))) + 0.5)
        return {1: "Good", 2: "Fairly Good", 3: "Moderate",
                4: "Fairly Poor", 5: "Poor"}[avg_class]

    if model == "score":
        scores = state.get("scores", {})
        ind = sheet["indicators"]
        if len([1 for (l, _n, _d) in ind if scores.get(l)]) < len(ind):
            return None
        total = sum(int(scores.get(l, 0)) for (l, _n, _d) in ind)
        for min_total, grade in sheet["bands"]:
            if total >= min_total:
                return grade
        return "Poor"

    if model == "hedgerow":
        with_trees = params.get("with_trees", False)
        letters = [l for (l, _t) in sheet["criteria"] if with_trees or not l.startswith("E")]
        fails = [l for l in letters if l not in passed]
        groups = {}
        for l in letters:
            groups.setdefault(l[0], []).append(l)
        double = sum(1 for g, atts in groups.items()
                     if len(atts) == 2 and all(a not in passed for a in atts))
        mod_fail = 5 if with_trees else 4
        if len(fails) <= 2 and double == 0:
            return "Good"
        if len(fails) <= mod_fail and double <= 1:
            return "Moderate"
        return "Poor"

    if model == "urban":
        used = ["A", "B", "C"] + list(params.get("additional", []))
        t = len(used)
        p = sum(1 for l in used if l in passed)
        poor_thr = 2 if t >= 5 else 1
        if p == t and "C" in passed and state.get("c_absent"):
            return "Good"
        if p <= poor_thr:
            return "Poor"
        return "Moderate"

    if model == "wetland":
        add = params.get("additional")
        core = sheet["core"]
        core_pass = sum(1 for l in core if l in passed)
        add_pass = bool(add) and add in passed
        total = core_pass + (1 if add_pass else 0)
        if core_pass >= 5 and "A" in passed and add_pass:
            return "Good"
        if total <= 3:
            return "Poor"
        return "Moderate"

    if model == "pond":
        if state.get("variant") == "woodland":
            letters = sheet["core"]                       # A-G (7)
            n = sum(1 for l in letters if l in passed)
            return "Good" if n >= 7 else ("Moderate" if n >= 5 else "Poor")
        letters = sheet["core"] + sheet["extra"]          # A-I (9)
        n = sum(1 for l in letters if l in passed)
        return "Good" if n >= 9 else ("Moderate" if n >= 6 else "Poor")

    # ── plain count model ────────────────────────────────────────────────────
    if key == "grass_medhigh" and params.get("acid"):
        letters = [l for (l, _t) in sheet["criteria"] if l != "F"]
        bands = [("Good", 5, []), ("Moderate", 3, []), ("Poor", 0, [])]
    else:
        letters = [l for (l, _t) in sheet["criteria"]]
        if key == "grass_medhigh":
            bands = [("Good", 5, ["A", "F"]), ("Moderate", 3, ["A"]), ("Poor", 0, [])]
        else:
            bands = sheet["bands"]
    n = sum(1 for l in letters if l in passed)
    for grade, minp, req in bands:
        if n >= minp and all(r in passed for r in req):
            return grade
    return "Poor"
