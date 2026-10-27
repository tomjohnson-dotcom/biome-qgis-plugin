# habitat_data.py
# Official DEFRA BNG Metric habitat types from the Statutory Biodiversity
# Metric v1.0.4 (July 2025), tab G-1 "All Habitats".
# Colours match exactly those used in the supplied QML styles.
#
# Categories align with the BNG Metric broad habitat grouping convention.
# The plugin groups all coastal/intertidal types under a single
# "Coastal & Intertidal" UI category; the per-sub-type metric broad
# habitat is resolved at export time via BROAD_HABITAT_OVERRIDE.

# Structure:
#   HABITAT_TYPES = {
#       "Category Label": {
#           "Sub Habitat Type Label": {
#               "key": "short_unique_key",
#               "color": "#hexcode"
#           }
#       }
#   }

# Display-only habitat labels which Biome may show on maps and in its habitat
# picker, but which are not separate rows in the statutory biodiversity metric.
# Features selected with one of these labels are stored/scored/exported using
# the canonical metric habitat on the right. The QML display fields retain the
# label on the left so the map and legend can distinguish it.
DISPLAY_ONLY_HABITAT_ALIASES = {
    "Buildings": "Developed land; sealed surface",
}


def canonical_habitat_name(name):
    """Return the statutory metric habitat for a Biome display label."""
    clean = str(name or "").strip()
    return DISPLAY_ONLY_HABITAT_ALIASES.get(clean, clean)


HABITAT_TYPES = {

    # ── GRASSLAND — metric broad habitat: Grassland ──
    "Grassland": {
        "Modified grassland":                                                    {"key": "GR-MO", "color": "#00FC04"},
        "Lowland meadows":                                                       {"key": "GR-LM", "color": "#98F05D"},
        "Lowland calcareous grassland":                                          {"key": "GR-LC", "color": "#98F05D"},
        "Lowland dry acid grassland":                                            {"key": "GR-LA", "color": "#98F05D"},
        "Other neutral grassland":                                               {"key": "GR-ON", "color": "#CBFFB6"},
        "Other lowland acid grassland":                                          {"key": "GR-OA", "color": "#B2DF8A"},
        "Upland hay meadows":                                                    {"key": "GR-UH", "color": "#98F05D"},
        "Upland calcareous grassland":                                           {"key": "GR-UC", "color": "#98F05D"},
        "Upland acid grassland":                                                 {"key": "GR-UA", "color": "#98F05D"},
        "Floodplain wetland mosaic and CFGM":                                    {"key": "GR-FM", "color": "#98F05D"},
        "Bracken":                                                               {"key": "GR-BR", "color": "#FFCC80"},
        "Tall herb communities (H6430)":                                         {"key": "GR-TH", "color": "#A8A8A4"},
        "Traditional orchards":                                                  {"key": "GR-TO", "color": "#FF7F00"},
    },

    # ── WOODLAND — metric broad habitat: Woodland and forest ──
    "Woodland": {
        "Lowland mixed deciduous woodland":                                      {"key": "WD-LM", "color": "#A94900"},
        "Other woodland; mixed":                                                 {"key": "WD-OM", "color": "#A94900"},
        "Lowland beech and yew woodland":                                        {"key": "WD-LB", "color": "#FFFFFF"},
        "Other woodland; broadleaved":                                           {"key": "WD-OB", "color": "#33A02C"},
        "Wet woodland":                                                          {"key": "WD-WE", "color": "#33A02C"},
        "Upland oakwood":                                                        {"key": "WD-UO", "color": "#33A02C"},
        "Upland mixed ashwoods":                                                 {"key": "WD-UA", "color": "#33A02C"},
        "Upland birchwoods":                                                     {"key": "WD-UB", "color": "#33A02C"},
        "Native pine woodlands":                                                 {"key": "WD-NP", "color": "#33A02C"},
        "Other Scot's pine woodland":                                            {"key": "WD-SP", "color": "#33A02C"},
        "Other coniferous woodland":                                             {"key": "WD-OC", "color": "#FBFD81"},
        "Wood-pasture and parkland":                                             {"key": "WD-WP", "color": "#FFFFFF"},
        "Felled":                                                                {"key": "WD-FE", "color": "#A5D6A7"},
    },

    # ── HEATHLAND & SHRUB — metric broad habitat: Heathland and shrub ──
    "Heathland & Shrub": {
        "Lowland heathland":                                                     {"key": "SH-LL", "color": "#8268D6"},
        "Upland heathland":                                                      {"key": "SH-UH", "color": "#8268D6"},
        "Mountain heaths and willow scrub":                                      {"key": "SH-MW", "color": "#8268D6"},
        "Mixed scrub":                                                           {"key": "SH-MS", "color": "#8268D6"},
        "Blackthorn scrub":                                                      {"key": "SH-BL", "color": "#8268D6"},
        "Bramble scrub":                                                         {"key": "SH-BR", "color": "#8268D6"},
        "Hawthorn scrub":                                                        {"key": "SH-HW", "color": "#8268D6"},
        "Hazel scrub":                                                           {"key": "SH-HZ", "color": "#8268D6"},
        "Gorse scrub":                                                           {"key": "SH-GO", "color": "#8268D6"},
        "Willow scrub":                                                          {"key": "SH-WI", "color": "#BFE3D5"},
        "Rhododendron scrub":                                                    {"key": "SH-RH", "color": "#8268D6"},
        "Dunes with sea buckthorn (H2160)":                                      {"key": "SH-DS", "color": "#8268D6"},
        "Other sea buckthorn scrub":                                             {"key": "SH-OS", "color": "#8268D6"},
    },

    # ── WETLAND — metric broad habitat: Wetland ──
    "Wetland": {
        "Fens (upland and lowland)":                                             {"key": "WL-FE", "color": "#FD7BEE"},
        "Blanket bog":                                                           {"key": "WL-BB", "color": "#FD7BEE"},
        "Lowland raised bog":                                                    {"key": "WL-LR", "color": "#FD7BEE"},
        "Transition mires and quaking bogs (H7140)":                             {"key": "WL-TM", "color": "#FD7BEE"},
        "Depressions on peat substrates (H7150)":                                {"key": "WL-DP", "color": "#FD7BEE"},
        "Oceanic valley mire[1] (D2.1)":                                         {"key": "WL-OV", "color": "#FD7BEE"},
        "Purple moor grass and rush pastures":                                   {"key": "WL-PM", "color": "#FD7BEE"},
        "Reedbeds":                                                              {"key": "WL-RE", "color": "#FBFD81"},
    },

    # ── LAKES — metric broad habitat: Lakes ──
    "Lakes": {
        "Ponds (priority habitat)":                                              {"key": "OW-PP", "color": "#27EDF5"},
        "Ponds (non-priority habitat)":                                          {"key": "OW-PN", "color": "#27EDF5"},
        "Marl lakes":                                                            {"key": "OW-ML", "color": "#27EDF5"},
        "High alkalinity lakes":                                                 {"key": "OW-HA", "color": "#27EDF5"},
        "Moderate alkalinity lakes":                                             {"key": "OW-MA", "color": "#27EDF5"},
        "Low alkalinity lakes":                                                  {"key": "OW-LA", "color": "#27EDF5"},
        "Peat lakes":                                                            {"key": "OW-PL", "color": "#27EDF5"},
        "Aquifer fed naturally fluctuating water bodies":                        {"key": "OW-AF", "color": "#27EDF5"},
        "Reservoirs":                                                            {"key": "OW-RE", "color": "#27EDF5"},
        "Temporary lakes ponds and pools (H3170)":                               {"key": "OW-TL", "color": "#27EDF5"},
        "Ornamental lake or pond":                                               {"key": "OW-OR", "color": "#B3E5FC"},
        "Watercourse footprint":                                                 {"key": "OW-WF", "color": "#A6CEE3"},
    },

    # ── SPARSELY VEGETATED LAND — metric broad habitat: Sparsely vegetated land ──
    "Sparsely Vegetated Land": {
        "Inland rock outcrop and scree habitats":                                {"key": "RK-IN", "color": "#A8A8A4"},
        "Other inland rock and scree":                                           {"key": "RK-OI", "color": "#FFFFFF"},
        "Limestone pavement":                                                    {"key": "RK-LP", "color": "#A8A8A4"},
        "Calaminarian grasslands":                                               {"key": "SV-CA", "color": "#98F05D"},
        "Ruderal/Ephemeral":                                                     {"key": "RK-RE", "color": "#A8A8A4"},
        "Tall forbs":                                                            {"key": "RK-TF", "color": "#A8A8A4"},
        "Coastal sand dunes":                                                    {"key": "SV-SD", "color": "#A8A8A4"},
        "Coastal vegetated shingle":                                             {"key": "SV-VS", "color": "#A8A8A4"},
        "Maritime cliff and slopes":                                             {"key": "SV-MC", "color": "#A8A8A4"},
    },

    # ── CROPLAND — metric broad habitat: Cropland ──
    "Cropland": {
        "Cereal crops":                                                          {"key": "AR-CC", "color": "#FFFFFF"},
        "Cereal crops other":                                                    {"key": "AR-CO", "color": "#FFFFFF"},
        "Winter stubble":                                                        {"key": "AR-WS", "color": "#FFFFFF"},
        "Non-cereal crops":                                                      {"key": "AR-NC", "color": "#FF7F00"},
        "Horticulture":                                                          {"key": "AR-HO", "color": "#FFFFFF"},
        "Temporary grass and clover leys":                                       {"key": "AR-TG", "color": "#FF7F00"},
        "Arable field margins cultivated annually":                              {"key": "AR-MA", "color": "#FFFFFF"},
        "Arable field margins game bird mix":                                    {"key": "AR-GB", "color": "#FFFFFF"},
        "Arable field margins pollen and nectar":                                {"key": "AR-PN", "color": "#FFFFFF"},
        "Arable field margins tussocky":                                         {"key": "AR-TU", "color": "#FFFFFF"},
        "Intensive orchards":                                                    {"key": "AR-IO", "color": "#FF7F00"},
    },

    # ── URBAN — metric broad habitat: Urban ──
    "Urban": {
        "Developed land; sealed surface":                                        {"key": "UR-DS", "color": "#EC2244"},
        # Display-only UKHab distinction. Stored and scored as UR-DS; the
        # charcoal colour is used only for the picker fallback swatch.
        "Buildings":                                                             {"key": "UR-DS", "color": "#555C59"},
        "Artificial unvegetated, unsealed surface":                              {"key": "UR-AU", "color": "#EC2244"},
        "Built linear features":                                                 {"key": "UR-BL", "color": "#9E9E9E"},
        "Vacant or derelict land":                                               {"key": "UR-VD", "color": "#BCAAA4"},
        "Open mosaic habitats on previously developed land":                     {"key": "UR-OM", "color": "#FFF9C4"},
        "Cemeteries and churchyards":                                            {"key": "UR-CE", "color": "#B0BEC5"},
        "Vegetated garden":                                                      {"key": "UR-VG", "color": "#66BB6A"},
        "Unvegetated garden":                                                    {"key": "UR-UG", "color": "#BCAAA4"},
        "Actively worked sand pit quarry or open cast mine":                     {"key": "UR-QU", "color": "#EC2244"},
        "Bare ground":                                                           {"key": "UR-BG", "color": "#D7CCC8"},
        "Introduced shrub":                                                      {"key": "UR-IS", "color": "#8268D6"},
        "Allotments":                                                            {"key": "UR-AL", "color": "#FF7F00"},
        "Biodiverse green roof":                                                 {"key": "GI-BG", "color": "#A5D6A7"},
        "Intensive green roof":                                                  {"key": "GI-IG", "color": "#EF9A9A"},
        "Other green roof":                                                      {"key": "GI-OG", "color": "#A5D6A7"},
        "Facade-bound green wall":                                               {"key": "GI-FW", "color": "#A5D6A7"},
        "Ground based green wall":                                               {"key": "GI-GW", "color": "#A5D6A7"},
        "Ground level planters":                                                 {"key": "GI-GL", "color": "#A5D6A7"},
        "Bioswale":                                                              {"key": "GI-BS", "color": "#81D4FA"},
        "Rain garden":                                                           {"key": "GI-RG", "color": "#80CBC4"},
        "Sustainable drainage system":                                           {"key": "GI-SD", "color": "#81D4FA"},
    },

    # ── COASTAL & INTERTIDAL — metric broad habitats: Coastal saltmarsh / Coastal lagoons / Rocky shore / Intertidal sediment / Intertidal hard structures (per-sub-type override) ──
    "Coastal & Intertidal": {
        "Saltmarshes and saline reedbeds":                                       {"key": "CO-SR", "color": "#1B53D6"},
        "Artificial saltmarshes and saline reedbeds":                            {"key": "CO-AS", "color": "#1B53D6"},
        "Coastal lagoons":                                                       {"key": "CO-CL", "color": "#FFFFFF"},
        "Littoral mud":                                                          {"key": "CO-LM", "color": "#FBFD81"},
        "Littoral sand":                                                         {"key": "CO-LS", "color": "#FBFD81"},
        "Littoral coarse sediment":                                              {"key": "CO-LC", "color": "#FBFD81"},
        "Littoral mixed sediments":                                              {"key": "CO-LX", "color": "#FBFD81"},
        "Littoral muddy sand":                                                   {"key": "CO-LMS", "color": "#FBFD81"},
        "Littoral seagrass":                                                     {"key": "CO-LSG", "color": "#FBFD81"},
        "Littoral seagrass on peat, clay or chalk":                              {"key": "CO-LP", "color": "#FBFD81"},
        "Littoral biogenic reefs - Mussels":                                     {"key": "CO-RM", "color": "#FBFD81"},
        "Littoral biogenic reefs - Sabellaria":                                  {"key": "CO-RS", "color": "#FBFD81"},
        "Features of littoral rock":                                             {"key": "CO-FR", "color": "#FFFFFF"},
        "Features of littoral rock - on peat, clay or chalk":                    {"key": "CO-FP", "color": "#FFFFFF"},
        "Features of littoral sediment":                                         {"key": "CO-FS", "color": "#FBFD81"},
        "High energy littoral rock":                                             {"key": "CO-HR", "color": "#FFFFFF"},
        "High energy littoral rock - on peat, clay or chalk":                    {"key": "CO-HP", "color": "#FFFFFF"},
        "Moderate energy littoral rock":                                         {"key": "CO-MR", "color": "#FFFFFF"},
        "Moderate energy littoral rock - on peat, clay or chalk":                {"key": "CO-MP", "color": "#FFFFFF"},
        "Low energy littoral rock":                                              {"key": "CO-LR", "color": "#FFFFFF"},
        "Low energy littoral rock - on peat, clay or chalk":                     {"key": "CO-LP2", "color": "#FFFFFF"},
        "Artificial littoral biogenic reefs":                                    {"key": "CO-AB", "color": "#FBFD81"},
        "Artificial littoral coarse sediment":                                   {"key": "CO-AC", "color": "#FBFD81"},
        "Artificial littoral mixed sediments":                                   {"key": "CO-AX", "color": "#FBFD81"},
        "Artificial littoral mud":                                               {"key": "CO-AM", "color": "#FBFD81"},
        "Artificial littoral muddy sand":                                        {"key": "CO-AMS", "color": "#FBFD81"},
        "Artificial littoral sand":                                              {"key": "CO-AS2", "color": "#FBFD81"},
        "Artificial littoral seagrass":                                          {"key": "CO-AG", "color": "#FBFD81"},
        "Artificial hard structures":                                            {"key": "CO-AH", "color": "#8C9692"},
        "Artificial features of hard structures":                                {"key": "CO-AF", "color": "#8C9692"},
        "Artificial hard structures with integrated greening of grey infrastructure (IGGI)": {"key": "CO-IG", "color": "#8C9692"},
    },

    # ── WATERCOURSE — metric broad habitat: Watercourse ──
    "Watercourse": {
        "Other rivers and streams":                                              {"key": "WC-RS", "color": "#18EB89"},
        "Priority habitat":                                                      {"key": "WC-PH", "color": "#00C5FF"},
        "Ditches":                                                               {"key": "WC-DI", "color": "#FFFF36"},
        "Canals":                                                                {"key": "WC-CA", "color": "#8D5A99"},
        "Culvert":                                                               {"key": "WC-CU", "color": "#232323"},
    },

    # ── HEDGEROW — metric broad habitat: Hedgerows ──
    "Hedgerow": {
        "Species-rich native hedgerow":                                          {"key": "HG-SR", "color": "#008000"},
        "Species-rich native hedgerow with trees":                               {"key": "HG-ST", "color": "#008000"},
        "Species-rich native hedgerow - associated with bank or ditch":          {"key": "HG-SB", "color": "#008000"},
        "Species-rich native hedgerow with trees - associated with bank or ditch": {"key": "HG-SD", "color": "#008000"},
        "Native hedgerow":                                                       {"key": "HG-NA", "color": "#8437E1"},
        "Native hedgerow with trees":                                            {"key": "HG-NT", "color": "#8437E1"},
        "Native hedgerow - associated with bank or ditch":                       {"key": "HG-NB", "color": "#8437E1"},
        "Native hedgerow with trees - associated with bank or ditch":            {"key": "HG-ND", "color": "#8437E1"},
        "Ecologically valuable line of trees":                                   {"key": "HG-EV", "color": "#000000"},
        "Ecologically valuable line of trees - associated with bank or ditch":   {"key": "HG-EB", "color": "#000000"},
        "Line of trees":                                                         {"key": "HG-LT", "color": "#000000"},
        "Line of trees - associated with bank or ditch":                         {"key": "HG-LB", "color": "#000000"},
        "Non-native and ornamental hedgerow":                                    {"key": "HG-NN", "color": "#FF0000"},
    },

    # ── INDIVIDUAL TREES — metric broad habitat: Individual trees ──
    "Individual Trees": {
        "Very Large Urban Tree":                                                 {"key": "TR-VU", "color": "#54B04A"},
        "Very Large Rural Tree":                                                 {"key": "TR-VR", "color": "#3D8035"},
        "Large Urban Tree":                                                      {"key": "TR-LU", "color": "#54B04A"},
        "Large Rural Tree":                                                      {"key": "TR-LR", "color": "#3D8035"},
        "Medium Urban Tree":                                                     {"key": "TR-MU", "color": "#54B04A"},
        "Medium Rural Tree":                                                     {"key": "TR-MR", "color": "#3D8035"},
        "Small Urban Tree":                                                      {"key": "TR-SU", "color": "#54B04A"},
        "Small Rural Tree":                                                      {"key": "TR-SR", "color": "#3D8035"},
    },
}


# ── Broad habitat override for metric export ─────────────────────────────────
# Maps sub_habitat_type → exact metric "Broad Habitat" string for cases where
# the plugin category name does not map 1:1 to the metric broad habitat.
# The exporter checks this dict first; if absent, falls back to the category
# default via BROAD_HABITAT_MAP.
#
# Source: Statutory Biodiversity Metric v1.0.4, tab G-1 "All Habitats".

BROAD_HABITAT_OVERRIDE = {
    # → Coastal lagoons
    "Coastal lagoons": "Coastal lagoons",
    # → Coastal saltmarsh
    "Artificial saltmarshes and saline reedbeds": "Coastal saltmarsh",
    "Saltmarshes and saline reedbeds": "Coastal saltmarsh",
    # → Intertidal hard structures
    "Artificial features of hard structures": "Intertidal hard structures",
    "Artificial hard structures": "Intertidal hard structures",
    "Artificial hard structures with integrated greening of grey infrastructure (IGGI)": "Intertidal hard structures",
    # → Intertidal sediment
    "Artificial littoral biogenic reefs": "Intertidal sediment",
    "Artificial littoral coarse sediment": "Intertidal sediment",
    "Artificial littoral mixed sediments": "Intertidal sediment",
    "Artificial littoral mud": "Intertidal sediment",
    "Artificial littoral muddy sand": "Intertidal sediment",
    "Artificial littoral sand": "Intertidal sediment",
    "Artificial littoral seagrass": "Intertidal sediment",
    "Features of littoral sediment": "Intertidal sediment",
    "Littoral biogenic reefs - Mussels": "Intertidal sediment",
    "Littoral biogenic reefs - Sabellaria": "Intertidal sediment",
    "Littoral coarse sediment": "Intertidal sediment",
    "Littoral mixed sediments": "Intertidal sediment",
    "Littoral mud": "Intertidal sediment",
    "Littoral muddy sand": "Intertidal sediment",
    "Littoral sand": "Intertidal sediment",
    "Littoral seagrass": "Intertidal sediment",
    "Littoral seagrass on peat, clay or chalk": "Intertidal sediment",
    # → Rocky shore
    "Features of littoral rock": "Rocky shore",
    "Features of littoral rock - on peat, clay or chalk": "Rocky shore",
    "High energy littoral rock": "Rocky shore",
    "High energy littoral rock - on peat, clay or chalk": "Rocky shore",
    "Low energy littoral rock": "Rocky shore",
    "Low energy littoral rock - on peat, clay or chalk": "Rocky shore",
    "Moderate energy littoral rock": "Rocky shore",
    "Moderate energy littoral rock - on peat, clay or chalk": "Rocky shore",
    # → Watercourse footprint
    "Watercourse footprint": "Watercourse footprint",
    # → Wetland
    "Purple moor grass and rush pastures": "Wetland",
}

CONDITION_OPTIONS = [
    "Good",
    "Fairly Good",
    "Moderate",
    "Fairly Poor",
    "Poor",
    "Condition Assessment N/A",
    "N/A - Other",
]

BASELINE_RETENTION_OPTIONS = ["Retained", "Lost", "Enhanced"]
PROPOSED_RETENTION_OPTIONS = ["Created", "Retained", "Enhanced"]
# Legacy alias so any existing references keep working
RETENTION_OPTIONS = BASELINE_RETENTION_OPTIONS

# ── Condition defaults derived from the Statutory BNG Metric condition sheet ──
#
# NOT_ASSESSED_SUBTYPES: metric only permits "Condition Assessment N/A"
#   (all Good/Moderate/Poor options marked "Not Possible" in the metric tool).
#   Includes both "Condition Assessment N/A" and "N/A - Other" categories from
#   the official condition assessment reference sheet (July 2025).
# NOT_ASSESSED_SUBTYPES: metric only permits "Condition Assessment N/A" (score 1).
# These habitats have "Not Possible" for all Good/Moderate/Poor grades in G-8.
NOT_ASSESSED_SUBTYPES = {
    # Arable & Cultivated
    "Arable field margins cultivated annually",
    "Arable field margins game bird mix",
    "Arable field margins pollen and nectar",
    "Arable field margins tussocky",
    "Cereal crops",
    "Cereal crops other",
    "Winter stubble",
    "Horticulture",
    "Intensive orchards",
    "Non-cereal crops",
    "Temporary grass and clover leys",
    # Grassland (metric: Grassland)
    "Bracken",
    # Heathland & Shrub
    "Bramble scrub",
    "Rhododendron scrub",
    "Other sea buckthorn scrub",
    # Urban
    "Introduced shrub",
    # Urban (continued) — Condition Assessment N/A (score 1)
    "Other green roof",
    "Ground level planters",
    "Actively worked sand pit quarry or open cast mine",
    "Vegetated garden",
}

# NA_OTHER_SUBTYPES: metric assigns "N/A - Other" (score 0, zero biodiversity value).
# These habitats have "Not Possible" for all grades INCLUDING "Condition Assessment N/A"
# in G-8 — only "N/A - Other" (column H, value 0) is permitted.
NA_OTHER_SUBTYPES = {
    "Developed land; sealed surface",
    "Buildings",
    "Artificial unvegetated, unsealed surface",
    "Built linear features",
    "Unvegetated garden",
    "Watercourse footprint",
}

# Combined set for any code that only needs to know "is this non-assessable?"
ALL_NOT_ASSESSED_SUBTYPES = NOT_ASSESSED_SUBTYPES | NA_OTHER_SUBTYPES

# FIXED_CONDITION_SUBTYPES: condition is fixed by the metric (not user-selectable).
#   Maps sub-habitat name → forced condition string.
FIXED_CONDITION_SUBTYPES = {
    "Felled":                           "Good",   # Woodland - Felled: fixed at Good
    "Non-native and ornamental hedgerow": "Poor",  # Hedgerow: fixed at Poor
    # Watercourse - Culvert: metric G-7 marks every grade except Poor as
    # "Not Possible", so a culvert can only be assessed as Poor (score 1).
    "Culvert":                          "Poor",
}


def is_condition_locked(sub):
    """True when the metric fixes this sub-habitat's condition (a fixed grade,
    'N/A - Other', or 'Condition Assessment N/A'), so no condition assessment
    applies — e.g. 'Non-native and ornamental hedgerow' is locked to Poor.
    Used to hide the condition-assessment button for these habitats."""
    sub = (sub or "").strip()
    return (sub in FIXED_CONDITION_SUBTYPES
            or sub in NOT_ASSESSED_SUBTYPES
            or sub in NA_OTHER_SUBTYPES)


def condition_items_for(sub):
    """Return (items, target, locked) for a sub-habitat's condition combo.

    items  — the condition strings to offer
    target — the value to pre-select when none is supplied
    locked — True when the metric fixes the condition (combo should be disabled)

    Single source of truth shared by the habitat placer and the Manage
    Features property editor, so a habitat offers exactly the same condition
    choices — and the same locking — in both places.
    """
    sub = (sub or "").strip()
    if sub in FIXED_CONDITION_SUBTYPES:
        return [FIXED_CONDITION_SUBTYPES[sub]], FIXED_CONDITION_SUBTYPES[sub], True
    if sub in NA_OTHER_SUBTYPES:
        return ["N/A - Other"], "N/A - Other", True
    if sub in NOT_ASSESSED_SUBTYPES:
        return ["Condition Assessment N/A"], "Condition Assessment N/A", True
    # Assessable habitats (FIVE_GRADE_SUBTYPES) and any unknown/future type
    # both offer the full five-grade list, unlocked, defaulting to Moderate.
    return ["Good", "Fairly Good", "Moderate", "Fairly Poor", "Poor",
            "N/A - Other"], "Moderate", False

# FIVE_GRADE_SUBTYPES: habitats where all 5 condition grades are valid in the
# statutory metric (Good / Fairly Good / Moderate / Fairly Poor / Poor).
# Source: G-8 Condition Look up sheet, statutory metric v1.0.4.
# All other assessable habitats that are not in NOT_ASSESSED_SUBTYPES also
# support five grades — this is the complete list from the metric.
FIVE_GRADE_SUBTYPES = {
    "Allotments",
    "Aquifer fed naturally fluctuating water bodies",
    "Artificial features of hard structures",
    "Artificial hard structures",
    "Artificial hard structures with integrated greening of grey infrastructure (IGGI)",
    "Artificial littoral biogenic reefs",
    "Artificial littoral coarse sediment",
    "Artificial littoral mixed sediments",
    "Artificial littoral mud",
    "Artificial littoral muddy sand",
    "Artificial littoral sand",
    "Artificial littoral seagrass",
    "Artificial saltmarshes and saline reedbeds",
    "Bare ground",
    "Biodiverse green roof",
    "Bioswale",
    "Blackthorn scrub",
    "Blanket bog",
    "Calaminarian grasslands",
    "Cemeteries and churchyards",
    "Coastal lagoons",
    "Coastal sand dunes",
    "Coastal vegetated shingle",
    "Depressions on peat substrates (H7150)",
    "Dunes with sea buckthorn (H2160)",
    "Facade-bound green wall",
    "Features of littoral rock",
    "Features of littoral rock - on peat, clay or chalk",
    "Features of littoral sediment",
    "Fens (upland and lowland)",
    "Floodplain wetland mosaic and CFGM",
    "Gorse scrub",
    "Ground based green wall",
    "Hawthorn scrub",
    "Hazel scrub",
    "High alkalinity lakes",
    "High energy littoral rock",
    "High energy littoral rock - on peat, clay or chalk",
    "Inland rock outcrop and scree habitats",
    "Intensive green roof",
    "Limestone pavement",
    "Littoral biogenic reefs - Mussels",
    "Littoral biogenic reefs - Sabellaria",
    "Littoral coarse sediment",
    "Littoral mixed sediments",
    "Littoral mud",
    "Littoral muddy sand",
    "Littoral sand",
    "Littoral seagrass",
    "Littoral seagrass on peat, clay or chalk",
    "Low alkalinity lakes",
    "Low energy littoral rock",
    "Low energy littoral rock - on peat, clay or chalk",
    "Lowland beech and yew woodland",
    "Lowland calcareous grassland",
    "Lowland dry acid grassland",
    "Lowland heathland",
    "Lowland meadows",
    "Lowland mixed deciduous woodland",
    "Lowland raised bog",
    "Maritime cliff and slopes",
    "Marl lakes",
    "Mixed scrub",
    "Moderate alkalinity lakes",
    "Moderate energy littoral rock",
    "Moderate energy littoral rock - on peat, clay or chalk",
    "Modified grassland",
    "Mountain heaths and willow scrub",
    "Native pine woodlands",
    "Oceanic valley mire[1] (D2.1)",
    "Open mosaic habitats on previously developed land",
    "Ornamental lake or pond",
    "Other Scot's pine woodland",
    "Other coniferous woodland",
    "Other inland rock and scree",
    "Other lowland acid grassland",
    "Other neutral grassland",
    "Other woodland; broadleaved",
    "Other woodland; mixed",
    "Peat lakes",
    "Ponds (non-priority habitat)",
    "Ponds (priority habitat)",
    "Purple moor grass and rush pastures",
    "Rain garden",
    "Reedbeds",
    "Reservoirs",
    "Ruderal/Ephemeral",
    "Rural tree",
    "Saltmarshes and saline reedbeds",
    "Sustainable drainage system",
    "Tall forbs",
    "Tall herb communities (H6430)",
    "Temporary lakes ponds and pools (H3170)",
    "Traditional orchards",
    "Transition mires and quaking bogs (H7140)",
    "Upland acid grassland",
    "Upland birchwoods",
    "Upland calcareous grassland",
    "Upland hay meadows",
    "Upland heathland",
    "Upland mixed ashwoods",
    "Upland oakwood",
    "Urban tree",
    "Vacant or derelict land",
    "Wet woodland",
    "Willow scrub",
    "Wood-pasture and parkland",
}




# ── Irreplaceable habitats ────────────────────────────────────────────────────
# Source: The Biodiversity Gain Requirements (Irreplaceable Habitat)
#         Regulations 2024 (SI 2024/48), Schedule, Tables 1 and 2.
#         This is the definitive statutory list — only these 8 habitat types
#         are legally defined as irreplaceable for BNG purposes.
#
# Table 1 (Section 41 list habitats):
#   Blanket bog, Lowland fens, Limestone pavements, Coastal sand dunes
#
# Table 2 (defined habitats):
#   Ancient woodland (incl. ASNW, PAWS, Ancient Wood Pasture & Parkland)
#   Ancient and veteran trees
#   Spartina saltmarsh swards
#   Mediterranean saltmarsh scrub
#
# Plugin habitat type matches:
#   - "Blanket bog"                     → Wetland & Peatland
#   - "Fens (upland and lowland)"       → Wetland & Peatland (= Lowland fens)
#   - "Limestone pavement"              → Sparsely Vegetated Land
#   - "Coastal sand dunes"              → Coastal & Intertidal
#   - "Saltmarshes and saline reedbeds" → Coastal & Intertidal
#     (covers Spartina swards and Mediterranean saltmarsh scrub — the plugin
#      does not distinguish these as separate sub-types)
#   - Wood-pasture and parkland         → Woodland (= Ancient Wood Pasture)
#
# Ancient woodland and ancient/veteran trees CANNOT be auto-set to Yes
# because the plugin records woodland types by ecological category (e.g.
# "Lowland mixed deciduous woodland") — whether it is ancient depends on
# age continuity data not available from the habitat type alone.
# These must be assessed by the ecologist on a case-by-case basis.
IRREPLACEABLE_SUBTYPES = {
    "Blanket bog",
    "Fens (upland and lowland)",
    "Limestone pavement",
    "Coastal sand dunes",
    "Saltmarshes and saline reedbeds",
    "Wood-pasture and parkland",
}


# ── BNG Statutory Metric Lookup Tables ────────────────────────────────────────
#
# Source: DEFRA Statutory Biodiversity Metric (v1.0.4, July 2025), tab G-1
#         "All Habitats" and G-3 "Multipliers".
#         Verified against the Statutory Biodiversity Metric User Guide
#         (July 2025) Table 5 and Technical Annex 2, Section 3.
#
# Distinctiveness score tiers (Table 5, Statutory BNG Metric User Guide):
#   Very high  = 8
#   High       = 6
#   Medium     = 4
#   Low        = 2
#   Very low (area module)     = 0
#   Very low (hedgerow module) = 1  (non-native/ornamental hedgerow only)
#
# DISTINCTIVENESS_SCORES maps sub_habitat_type string → integer score.
# Area habitats use area_ha × score × condition × strategic_significance.
# Hedgerows and watercourses use length_km × score × condition × strategic_sig.
# Individual trees use their statutory area equivalent (TREE_AREA_HA) then
# the same area formula — they map to the "Urban tree" / "Rural tree" rows in
# the metric which are classified as Medium distinctiveness (score 4).
#
DISTINCTIVENESS_SCORES = {

    # ── Grassland ──────────────────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # V.High (8)
    "Lowland meadows":                          8,
    "Lowland dry acid grassland":               8,
    "Upland hay meadows":                       8,
    # High (6)
    "Lowland calcareous grassland":             6,
    "Upland calcareous grassland":              6,
    "Traditional orchards":                     6,
    "Floodplain wetland mosaic and CFGM":       6,
    "Tall herb communities (H6430)":            6,
    # Medium (4)
    "Other neutral grassland":                  4,
    "Other lowland acid grassland":             4,
    "Upland acid grassland":                    4,
    # Low (2)
    "Modified grassland":                       2,
    "Bracken":                                  2,

    # ── Cropland ──────────────────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # Medium (4)
    "Arable field margins cultivated annually": 4,
    "Arable field margins game bird mix":       4,
    "Arable field margins pollen and nectar":   4,
    "Arable field margins tussocky":            4,
    # Low (2)
    "Temporary grass and clover leys":          2,
    "Non-cereal crops":                         2,
    "Horticulture":                             2,
    "Intensive orchards":                       2,
    "Cereal crops":                             2,
    "Winter stubble":                           2,
    "Cereal crops other":                       2,

    # ── Woodland ───────────────────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # V.High (8)
    "Wood-pasture and parkland":                8,
    # High (6)
    "Lowland beech and yew woodland":           6,
    "Lowland mixed deciduous woodland":         6,
    "Native pine woodlands":                    6,
    "Upland oakwood":                           6,
    "Upland mixed ashwoods":                    6,
    "Wet woodland":                             6,
    "Upland birchwoods":                        6,
    "Felled":                                   6,
    # Medium (4)
    "Other woodland; broadleaved":              4,
    "Other woodland; mixed":                    4,
    "Other Scot's pine woodland":               4,
    # Low (2)
    "Other coniferous woodland":                2,

    # ── Heathland & Shrub ─────────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # V.High (8)
    "Mountain heaths and willow scrub":         8,
    # High (6)
    "Lowland heathland":                        6,
    "Upland heathland":                         6,
    "Dunes with sea buckthorn (H2160)":         6,
    # Medium (4)
    "Mixed scrub":                              4,
    "Blackthorn scrub":                         4,
    "Hawthorn scrub":                           4,
    "Hazel scrub":                              4,
    "Gorse scrub":                              4,
    "Willow scrub":                             4,
    "Bramble scrub":                            4,
    # Low (2)
    "Rhododendron scrub":                       2,
    "Other sea buckthorn scrub":                2,

    # ── Wetland ──────────────────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # V.High (8)
    "Fens (upland and lowland)":                8,
    "Blanket bog":                              8,
    "Lowland raised bog":                       8,
    "Transition mires and quaking bogs (H7140)": 8,
    "Depressions on peat substrates (H7150)":   8,
    "Oceanic valley mire[1] (D2.1)":            8,
    "Purple moor grass and rush pastures":      8,
    # High (6)
    "Reedbeds":                                 6,

    # ── Lakes ─────────────────────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # V.High (8)
    "Aquifer fed naturally fluctuating water bodies": 8,
    # High (6)
    "Ponds (priority habitat)":                 6,
    "High alkalinity lakes":                    6,
    "Moderate alkalinity lakes":                6,
    "Low alkalinity lakes":                     6,
    "Peat lakes":                               6,
    "Marl lakes":                               6,
    "Temporary lakes ponds and pools (H3170)":  6,
    # Medium (4)
    "Ponds (non-priority habitat)":             4,
    "Reservoirs":                               4,
    # Low (2)
    "Ornamental lake or pond":                  2,
    # V.Low (0)
    "Watercourse footprint":                    0,

    # ── Coastal & Intertidal ──────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # V.High (8)
    "Littoral seagrass on peat, clay or chalk": 8,
    "High energy littoral rock - on peat, clay or chalk": 8,
    "Moderate energy littoral rock - on peat, clay or chalk": 8,
    "Low energy littoral rock - on peat, clay or chalk": 8,
    "Features of littoral rock - on peat, clay or chalk": 8,
    # High (6)
    "Saltmarshes and saline reedbeds":          6,
    "Coastal lagoons":                          6,
    "High energy littoral rock":                6,
    "Moderate energy littoral rock":            6,
    "Low energy littoral rock":                 6,
    "Features of littoral rock":                6,
    "Littoral mud":                             6,
    "Littoral mixed sediments":                 6,
    "Littoral seagrass":                        6,
    "Littoral biogenic reefs - Mussels":        6,
    "Littoral biogenic reefs - Sabellaria":     6,
    "Features of littoral sediment":            6,
    "Littoral muddy sand":                      6,
    "Coastal sand dunes":                       6,
    "Coastal vegetated shingle":                6,
    # Medium (4)
    "Littoral coarse sediment":                 4,
    "Littoral sand":                            4,
    "Artificial hard structures with integrated greening of grey infrastructure (IGGI)": 4,
    # Low (2)
    "Artificial saltmarshes and saline reedbeds": 2,
    "Artificial littoral biogenic reefs":       2,
    "Artificial littoral seagrass":             2,
    "Artificial littoral muddy sand":           2,
    "Artificial littoral mud":                  2,
    "Artificial littoral mixed sediments":      2,
    "Artificial littoral sand":                 2,
    "Artificial littoral coarse sediment":      2,
    "Artificial hard structures":               2,
    "Artificial features of hard structures":   2,

    # ── Sparsely Vegetated Land ──────────────────────────────────────────────
    # Source: G-1 All Habitats
    # V.High (8)
    "Limestone pavement":                       8,
    "Calaminarian grasslands":                  8,
    # High (6)
    "Inland rock outcrop and scree habitats":   6,
    "Maritime cliff and slopes":                6,
    "Coastal sand dunes":                       6,
    "Coastal vegetated shingle":                6,
    # Medium (4)
    "Other inland rock and scree":              4,
    # Low (2)
    "Ruderal/Ephemeral":                        2,
    "Tall forbs":                               2,

    # ── Urban ─────────────────────────────────────────────────────────────────
    # Source: G-1 All Habitats
    # High (6)
    "Open mosaic habitats on previously developed land": 6,
    # Medium (4)
    "Cemeteries and churchyards":               4,
    "Biodiverse green roof":                    4,
    # Low (2)
    "Vegetated garden":                         2,
    "Vacant or derelict land":                  2,
    "Actively worked sand pit quarry or open cast mine": 2,
    "Bare ground":                              2,
    "Introduced shrub":                         2,
    "Allotments":                               2,
    "Bioswale":                                 2,
    "Intensive green roof":                     2,
    "Other green roof":                         2,
    "Facade-bound green wall":                  2,
    "Ground based green wall":                  2,
    "Ground level planters":                    2,
    "Rain garden":                              2,
    "Sustainable drainage system":              2,
    # V.Low (0)
    "Developed land; sealed surface":           0,
    "Buildings":                                0,
    "Artificial unvegetated, unsealed surface": 0,
    "Built linear features":                    0,
    "Unvegetated garden":                       0,

    # ── Hedgerow (linear — km) ─────────────────────────────────────────────────
    # Source: G-6 Hedgerow Data — SEPARATE scoring system from area habitats
    # V.High (8)
    "Species-rich native hedgerow with trees - associated with bank or ditch": 8,
    # High (6)
    "Species-rich native hedgerow with trees":                          6,
    "Species-rich native hedgerow - associated with bank or ditch":     6,
    "Native hedgerow with trees - associated with bank or ditch":       6,
    # Medium (4)
    "Species-rich native hedgerow":                                     4,
    "Native hedgerow - associated with bank or ditch":                  4,
    "Native hedgerow with trees":                                       4,
    "Ecologically valuable line of trees":                              4,
    "Ecologically valuable line of trees - associated with bank or ditch": 4,
    # Low (2)
    "Native hedgerow":                                                  2,
    "Line of trees":                                                    2,
    "Line of trees - associated with bank or ditch":                    2,
    # V.Low (1) — special case: score is 1 not 0
    "Non-native and ornamental hedgerow":                               1,

    # ── Watercourse (linear — km) ──────────────────────────────────────────────
    # Source: G-2 Habitat groups (watercourse section)
    # V.High (8): priority river habitat
    "Priority habitat":                                                 8,
    # High (6): other rivers and streams
    "Other rivers and streams":                                         6,
    # Medium (4): canals and ditches
    "Canals":                                                           4,
    "Ditches":                                                          4,
    # Low (2): culverts
    "Culvert":                                                          2,

    # ── Individual Trees ───────────────────────────────────────────────────────
    # Source: G-1 — Urban tree / Rural tree = Medium distinctiveness (score 4)
    "Very Large Urban Tree":                                            4,
    "Very Large Rural Tree":                                            4,
    "Large Urban Tree":                                                 4,
    "Large Rural Tree":                                                 4,
    "Medium Urban Tree":                                                4,
    "Medium Rural Tree":                                                4,
    "Small Urban Tree":                                                 4,
    "Small Rural Tree":                                                 4,
}

# ── Hedgerow enhancement matrix (Source: metric G-6 Hedgerow Data) ────────────
# Which (baseline hedge type -> proposed hedge type) changes the metric accepts
# as ENHANCEMENT (a non-"Error" cell in G-6). Anything NOT in this set is either
# "Trading Down" or "Enhancement not possible" and must be treated as CREATION,
# not enhancement — e.g. a Non-native and ornamental hedgerow can ONLY stay
# Non-native; it can never be *enhanced* into a native/species-rich hedge.
# Same-type pairs are included (a same-type condition uplift is a valid
# enhancement). Used by bng_multipliers.hedge_enhancement_allowed().
HEDGE_ENHANCEMENT_OK = {
    ('Species-rich native hedgerow with trees - associated with bank or ditch', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Species-rich native hedgerow with trees', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Species-rich native hedgerow with trees', 'Species-rich native hedgerow with trees'),
    ('Species-rich native hedgerow - associated with bank or ditch', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Species-rich native hedgerow - associated with bank or ditch', 'Species-rich native hedgerow - associated with bank or ditch'),
    ('Native hedgerow with trees - associated with bank or ditch', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Native hedgerow with trees - associated with bank or ditch', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Species-rich native hedgerow', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Species-rich native hedgerow', 'Species-rich native hedgerow with trees'),
    ('Species-rich native hedgerow', 'Species-rich native hedgerow - associated with bank or ditch'),
    ('Species-rich native hedgerow', 'Species-rich native hedgerow'),
    ('Native hedgerow - associated with bank or ditch', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Native hedgerow - associated with bank or ditch', 'Species-rich native hedgerow - associated with bank or ditch'),
    ('Native hedgerow - associated with bank or ditch', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Native hedgerow - associated with bank or ditch', 'Native hedgerow - associated with bank or ditch'),
    ('Native hedgerow with trees', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Native hedgerow with trees', 'Species-rich native hedgerow with trees'),
    ('Native hedgerow with trees', 'Species-rich native hedgerow - associated with bank or ditch'),
    ('Native hedgerow with trees', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Native hedgerow with trees', 'Native hedgerow with trees'),
    ('Ecologically valuable line of trees', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Ecologically valuable line of trees', 'Species-rich native hedgerow with trees'),
    ('Ecologically valuable line of trees', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Ecologically valuable line of trees', 'Ecologically valuable line of trees'),
    ('Ecologically valuable line of trees', 'Ecologically valuable line of trees - associated with bank or ditch'),
    ('Ecologically valuable line of trees - associated with bank or ditch', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Ecologically valuable line of trees - associated with bank or ditch', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Ecologically valuable line of trees - associated with bank or ditch', 'Ecologically valuable line of trees - associated with bank or ditch'),
    ('Native hedgerow', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Native hedgerow', 'Species-rich native hedgerow with trees'),
    ('Native hedgerow', 'Species-rich native hedgerow - associated with bank or ditch'),
    ('Native hedgerow', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Native hedgerow', 'Species-rich native hedgerow'),
    ('Native hedgerow', 'Native hedgerow - associated with bank or ditch'),
    ('Native hedgerow', 'Native hedgerow with trees'),
    ('Native hedgerow', 'Native hedgerow'),
    ('Line of trees', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Line of trees', 'Species-rich native hedgerow with trees'),
    ('Line of trees', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Line of trees', 'Native hedgerow with trees'),
    ('Line of trees', 'Line of trees'),
    ('Line of trees', 'Line of trees - associated with bank or ditch'),
    ('Line of trees - associated with bank or ditch', 'Species-rich native hedgerow with trees - associated with bank or ditch'),
    ('Line of trees - associated with bank or ditch', 'Native hedgerow with trees - associated with bank or ditch'),
    ('Line of trees - associated with bank or ditch', 'Line of trees - associated with bank or ditch'),
    ('Non-native and ornamental hedgerow', 'Non-native and ornamental hedgerow'),
}

# ── Statutory tree area equivalents (ha) ──────────────────────────────────────
# Source: Statutory Biodiversity Metric tool "tree helper" / individual trees
# module. DBH size class → statutory area (ha) used in the area formula.
# Urban vs Rural have the same area bands in the current metric.
TREE_AREA_HA = {
    # Source: DEFRA Statutory Metric G-3 Multipliers sheet
    # Formula: RPA radius = diameter x 12; area = pi x radius^2
    # Small  (DBH 8-29 cm):  diameter=0.3m, RPA=3.6m,  area=pi*3.6^2 /1e4 = 0.0040715 ha
    # Medium (DBH 30-74 cm): diameter=0.6m, RPA=7.2m,  area=pi*7.2^2 /1e4 = 0.01628602 ha
    # Large  (DBH 75-119 cm):diameter=0.9m, RPA=10.8m, area=pi*10.8^2/1e4 = 0.03664354 ha
    # V.large(DBH >=120 cm): diameter=1.3m, RPA=15.6m, area=pi*15.6^2/1e4 = 0.0764538 ha
    # These are the FULL-PRECISION pi*r^2 areas — identical to layer_manager
    # .TREE_SIZE_AREAS, which is what the exporter actually writes into the metric.
    # (Previously rounded to 6 dp here, which left tree units ~0.01% adrift of the
    # exported/scored value; a whole tree must price to exactly what it scores.)
    "Very Large Urban Tree": 0.0764538,
    "Very Large Rural Tree": 0.0764538,
    "Large Urban Tree":   0.03664354,
    "Large Rural Tree":   0.03664354,
    "Medium Urban Tree":  0.01628602,
    "Medium Rural Tree":  0.01628602,
    "Small Urban Tree":   0.0040715,
    "Small Rural Tree":   0.0040715,
}

# ── Condition multipliers ─────────────────────────────────────────────────────
# Source: Statutory Biodiversity Metric User Guide (July 2025), Table 6.
# The statutory metric uses four condition grades: Good, Moderate, Poor, Not Assessed.
CONDITION_MULTIPLIERS = {
    "Good":                 3.0,
    "Fairly Good":          2.5,
    "Moderate":             2.0,
    "Fairly Poor":          1.5,
    "Poor":                 1.0,
    "Condition Assessment N/A":         1.0,   # Condition Assessment N/A (score 1 in statutory metric)
    "N/A - Other": 0.0,   # N/A-Other in metric = 0 units
}

STRATEGIC_MULTIPLIERS = {
    # Keys MUST match the strings stored in the strategic_significance field,
    # i.e. STRATEGIC_SIGNIFICANCE_OPTIONS in layer_manager.py. These three are
    # the values the dropdown actually writes to the layer:
    "Area/compensation not in local strategy/ no local strategy":      1.00,  # Low
    "Location ecologically desirable but not in local strategy":       1.10,  # Medium
    "Formally identified in local strategy":                           1.15,  # High
    # Blank / unset
    "":                                                                1.00,
    "Not applicable":                                                  1.00,
    # Legacy / alternative spellings kept as aliases so nothing silently
    # falls through to the 1.0 default if an older layer is opened.
    "High (Formally identified in local strategy)":                    1.15,
    "Medium (Location ecologically desirable but not in local strategy)": 1.10,
    "Low (Area / compensation not in local strategy)":                 1.00,
}

# Which habitat types appear on which screen
HABITAT_SCREEN_TYPES = {
    "area":       ["Grassland", "Woodland", "Heathland & Shrub", "Wetland",
                   "Lakes", "Coastal & Intertidal", "Sparsely Vegetated Land",
                   "Cropland", "Urban"],
    "watercourse":["Watercourse"],
    "hedgerow":   ["Hedgerow"],
    "trees":      ["Individual Trees"],
}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CONDITION_CRITERIA
# Official condition assessment criteria from the Statutory Biodiversity Metric
# Condition Assessment Sheets (Technical Annex 1, July 2025 v1.0.2).
#
# Structure: sub_habitat_type → {grade: [list of criteria/notes]}
# ---------------------------------------------------------------------------

# DEPRECATED (2026-06): superseded by condition_data.py, which holds the
# computable criteria (essential flags + grade thresholds), the sheet footnotes
# and drives the interactive condition calculator. This dict is no longer read
# by the plugin — kept for reference only. Do NOT edit it; update condition_data.py.
CONDITION_CRITERIA = {

    # ── GRASSLAND (Low distinctiveness) ────────────────────────────────────
    # Sheet: 5A.GRASSLAND LOW

    "Modified grassland": {
        "Good":      ["Pass 6 or 7 criteria including essential criterion A",
                      "A. 6-8 vascular plant species per m2 including >=2 forbs (ESSENTIAL - must pass for Moderate or Good)",
                      "B. Sward height varied: >=20% below 7 cm AND >=20% above 7 cm",
                      "C. Scrub <20% of total area",
                      "D. Physical damage (poaching, machinery, erosion) <5% of area",
                      "E. Bare ground between 1-10%",
                      "F. Bracken cover <20%",
                      "G. No invasive non-native plant species (Schedule 9 WCA)"],
        "Moderate":  ["Pass 4 or 5 criteria including essential criterion A"],
        "Poor":      ["Pass 3 or fewer criteria, OR pass 4-6 criteria but fail essential criterion A"],
    },

    # ── GRASSLAND (Medium / High / Very High distinctiveness) ──────────────
    # Sheet: 6A.GRASSLAND MED HIGH & V.HIGH

    "Lowland calcareous grassland": {
        "Good":      ["Pass 5 or 6 criteria including A and F (non-acid type)",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL for non-acid types)",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species (Schedule 9 WCA)",
                      "F. >=10 vascular plant species per m2 including characteristic forbs (ESSENTIAL for Good, non-acid types)"],
        "Moderate":  ["Pass 3-5 criteria including essential criterion A"],
        "Poor":      ["Pass 2 or fewer criteria, OR pass 3-4 without criteria A and F"],
    },

    "Lowland dry acid grassland": {
        "Good":      ["Pass all 5 criteria (acid grassland — criterion F not required)",
                      "A. Good example of habitat type — characteristic indicator species present",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species (Schedule 9 WCA)"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Lowland meadows": {
        "Good":      ["Pass 5 or 6 criteria including A and F",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species",
                      "F. >=10 vascular plant species per m2 including characteristic forbs (ESSENTIAL for Good)"],
        "Moderate":  ["Pass 3-5 criteria including essential criterion A"],
        "Poor":      ["Pass 2 or fewer criteria, OR pass 3-4 without criteria A and F"],
    },

    "Other neutral grassland": {
        "Good":      ["Pass 5 or 6 criteria including A and F",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species",
                      "F. >=10 vascular plant species per m2 including characteristic forbs (ESSENTIAL for Good)"],
        "Moderate":  ["Pass 3-5 criteria including essential criterion A"],
        "Poor":      ["Pass 2 or fewer criteria, OR pass 3-4 without criteria A and F"],
    },

    "Other lowland acid grassland": {
        "Good":      ["Pass all 5 criteria (acid grassland)",
                      "A. Good example of habitat type — characteristic indicator species present",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Upland calcareous grassland": {
        "Good":      ["Pass 5 or 6 criteria including A and F",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species",
                      "F. >=10 vascular plant species per m2 including characteristic forbs (ESSENTIAL for Good)"],
        "Moderate":  ["Pass 3-5 criteria including essential criterion A"],
        "Poor":      ["Pass 2 or fewer criteria, OR pass 3-4 without criteria A and F"],
    },

    "Upland hay meadows": {
        "Good":      ["Pass 5 or 6 criteria including A and F",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species",
                      "F. >=10 vascular plant species per m2 including characteristic forbs (ESSENTIAL for Good)"],
        "Moderate":  ["Pass 3-5 criteria including essential criterion A"],
        "Poor":      ["Pass 2 or fewer criteria, OR pass 3-4 without criteria A and F"],
    },

    "Upland acid grassland": {
        "Good":      ["Pass all 5 criteria (acid grassland)",
                      "A. Good example of habitat type — characteristic indicator species present",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Tall herb communities (H6430)": {
        "Good":      ["Pass 5 or 6 criteria including A and F",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species",
                      "F. >=10 vascular plant species per m2 including characteristic forbs (ESSENTIAL for Good)"],
        "Moderate":  ["Pass 3-5 criteria including essential criterion A"],
        "Poor":      ["Pass 2 or fewer criteria, OR pass 3-4 without criteria A and F"],
    },

    "Calaminarian grasslands": {
        "Good":      ["Pass 5 or 6 criteria including A and F",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. Sward height varied: >=20% <7 cm AND >=20% >7 cm",
                      "C. Bare ground 1-5%",
                      "D. Bracken <20% AND scrub (incl. bramble) <5%",
                      "E. Suboptimal species + physical damage <5%; no invasive non-native species",
                      "F. >=10 vascular plant species per m2 including characteristic forbs (ESSENTIAL for Good)"],
        "Moderate":  ["Pass 3-5 criteria including essential criterion A"],
        "Poor":      ["Pass 2 or fewer criteria, OR pass 3-4 without criteria A and F"],
    },

    "Traditional orchards": {
        "Good":      ["Pass 6-8 criteria including essential criteria A and B",
                      "A. Ancient and/or veteran trees present (ESSENTIAL for Good)",
                      "B. Deadwood on/in trees — >=20% of mature trees have associated deadwood (ESSENTIAL for Good)",
                      "C. <5% of fruit trees smothered by scrub; dense scrub <10% ground cover",
                      "D. Evidence of formative or restorative pruning to maintain tree longevity",
                      "E. >=95% of trees free from human or animal damage",
                      "F. Grassland not overgrazed; poaching under canopy <10% of trees",
                      "G. Grassland species richness equivalent to medium, high or very high distinctiveness",
                      "H. No invasive non-native plants (Schedule 9 WCA); suboptimal species <10% ground cover"],
        "Moderate":  ["Pass 4-5 criteria, OR pass 6-7 but fail an essential criterion (A or B)"],
        "Poor":      ["Pass 3 or fewer criteria"],
    },

    # ── HEATHLAND ──────────────────────────────────────────────────────────
    # Sheet: 7A.HEATHLAND

    "Lowland heathland": {
        "Good":      ["Pass 9-11 criteria including ALL essential criteria A-E",
                      "A. Good example of habitat type — characteristic vascular and non-vascular indicator species consistently present (ESSENTIAL)",
                      "B. >=2 dwarf shrub species Frequent; dwarf shrub cover 25-75% (lowland dry) or >20% (upland wet) (ESSENTIAL)",
                      "C. All heather age-classes present; >=10% pioneer heather (lowland) (ESSENTIAL)",
                      "D. Unshaded bare ground 1-10% (ESSENTIAL)",
                      "E. No invasive non-native plants (Schedule 9 WCA) and no shallon Gaultheria shallon (ESSENTIAL)",
                      "F. No disturbance of sensitive areas; no managed burns",
                      "G. <=33% of heather shoots recently grazed, OR flowering heather at least Frequent in autumn",
                      "H. Scattered trees/scrub canopy: <15% (lowland dry) or <10% (lowland wet)",
                      "I. Total gorse cover <50%; common gorse <25%",
                      "J. Bracken cover <5%",
                      "K. No damaging activities or contamination (artificial drains, peat extraction, eutrophication etc.)"],
        "Moderate":  ["Pass 7-8 of 11 criteria, OR pass 9-10 but fail any essential criterion (A-E)"],
        "Poor":      ["Pass 6 or fewer criteria"],
    },

    "Upland heathland": {
        "Good":      ["Pass 9-11 criteria including ALL essential criteria A-E",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. >=2 dwarf shrub species Frequent; dwarf shrub cover 50-75% (upland dry) or >20% (upland wet) (ESSENTIAL)",
                      "C. All heather age-classes present; >=10% degenerate or mature heather (ESSENTIAL)",
                      "D. Unshaded bare ground 1-10% (ESSENTIAL)",
                      "E. No invasive non-native plants (Schedule 9 WCA) and no shallon (ESSENTIAL)",
                      "F. No disturbance of sensitive areas; no managed burns on wet heath",
                      "G. <=33% of heather shoots recently grazed, OR flowering heather at least Frequent in autumn",
                      "H. Canopy cover of scattered trees/scrub <20% (upland)",
                      "I. Total gorse cover <50%; common gorse <25%",
                      "J. Bracken cover <5%",
                      "K. No damaging activities or contamination"],
        "Moderate":  ["Pass 7-8 of 11 criteria, OR pass 9-10 but fail any essential criterion (A-E)"],
        "Poor":      ["Pass 6 or fewer criteria"],
    },

    "Mountain heaths and willow scrub": {
        "Good":      ["Pass 9-11 criteria including ALL essential criteria A-E",
                      "A. Good example of habitat type — characteristic indicator species consistently present (ESSENTIAL)",
                      "B. >=2 dwarf shrub species Frequent; appropriate dwarf shrub cover for type (ESSENTIAL)",
                      "C. Heather age-class structure appropriate to type (ESSENTIAL)",
                      "D. Unshaded bare ground 1-10% (ESSENTIAL)",
                      "E. No invasive non-native plants (Schedule 9 WCA) and no shallon (ESSENTIAL)",
                      "F. No disturbance of sensitive areas",
                      "G. <=33% heather shoots recently grazed, OR flowering heather at least Frequent in autumn",
                      "H. Canopy cover of scattered trees/scrub <20%",
                      "I. Total gorse cover <50%; common gorse <25%",
                      "J. Bracken cover <5%",
                      "K. No damaging activities or contamination"],
        "Moderate":  ["Pass 7-8 of 11 criteria, OR pass 9-10 but fail any essential criterion (A-E)"],
        "Poor":      ["Pass 6 or fewer criteria"],
    },

    # ── HEDGEROWS ──────────────────────────────────────────────────────────
    # Sheet: 8A.HEDGEROW

    "Native hedgerow": {
        "Good":      ["No more than 2 failures in total; AND no more than 1 failure in any functional group",
                      "A1. Height >1.5 m average (newly laid/coppiced hedgerows pass for up to 4 years if managed to good practice)",
                      "A2. Width >1.5 m average at widest point of canopy",
                      "B1. Gap at hedge base <0.5 m for >90% of length",
                      "B2. Canopy gaps <10% of total length; no individual gap >5 m",
                      "C1. >=1 m undisturbed ground with perennial vegetation for >90% of length (one side minimum)",
                      "C2. Nutrient-enriched species (nettles, cleavers, docks) <20% cover of undisturbed ground",
                      "D1. >=90% of hedgerow and ground free of invasive non-native and neophyte plant species",
                      "D2. >=90% of hedgerow and ground free of damage from human activities"],
        "Moderate":  ["No more than 4 failures total; AND does not fail both attributes in more than 1 functional group"],
        "Poor":      ["More than 4 attribute failures total, OR fails both attributes in more than 1 functional group"],
    },

    "Native hedgerow with trees": {
        "Good":      ["No more than 2 failures in total; AND no more than 1 failure in any functional group",
                      "A1. Height >1.5 m average",
                      "A2. Width >1.5 m average",
                      "B1. Gap at hedge base <0.5 m for >90% of length",
                      "B2. Canopy gaps <10% of total length; no individual gap >5 m",
                      "C1. >=1 m undisturbed ground with perennial vegetation for >90% of length (one side minimum)",
                      "C2. Nutrient-enriched species <20% cover of undisturbed ground",
                      "D1. >=90% free of invasive non-native and neophyte plant species",
                      "D2. >=90% free of damage from human activities",
                      "E1. More than one age-class of tree present; >=1 mature/ancient/veteran tree per 20-50 m",
                      "E2. >=95% of hedgerow trees in healthy condition; little adverse impact"],
        "Moderate":  ["No more than 5 failures total; AND does not fail both attributes in more than 1 functional group"],
        "Poor":      ["More than 5 attribute failures total, OR fails both attributes in more than 1 functional group"],
    },

    "Species-rich native hedgerow": {
        "Good":      ["No more than 2 failures in total; AND no more than 1 failure in any functional group",
                      "A1. Height >1.5 m average",
                      "A2. Width >1.5 m average",
                      "B1. Gap at hedge base <0.5 m for >90% of length",
                      "B2. Canopy gaps <10% of total length; no individual gap >5 m",
                      "C1. >=1 m undisturbed ground with perennial vegetation for >90% of length (one side minimum)",
                      "C2. Nutrient-enriched species <20% cover of undisturbed ground",
                      "D1. >=90% free of invasive non-native and neophyte plant species",
                      "D2. >=90% free of damage from human activities"],
        "Moderate":  ["No more than 4 failures total; AND does not fail both attributes in more than 1 functional group"],
        "Poor":      ["More than 4 attribute failures total, OR fails both attributes in more than 1 functional group"],
    },

    "Species-rich native hedgerow with trees": {
        "Good":      ["No more than 2 failures in total; AND no more than 1 failure in any functional group",
                      "A1. Height >1.5 m average",
                      "A2. Width >1.5 m average",
                      "B1. Gap at hedge base <0.5 m for >90% of length",
                      "B2. Canopy gaps <10% of total length; no individual gap >5 m",
                      "C1. >=1 m undisturbed ground with perennial vegetation for >90% of length (one side minimum)",
                      "C2. Nutrient-enriched species <20% cover of undisturbed ground",
                      "D1. >=90% free of invasive non-native and neophyte plant species",
                      "D2. >=90% free of damage from human activities",
                      "E1. More than one age-class of tree present; >=1 mature/ancient/veteran tree per 20-50 m",
                      "E2. >=95% of hedgerow trees in healthy condition"],
        "Moderate":  ["No more than 5 failures total; AND does not fail both attributes in more than 1 functional group"],
        "Poor":      ["More than 5 attribute failures total, OR fails both attributes in more than 1 functional group"],
    },

    # ── INDIVIDUAL TREES ───────────────────────────────────────────────────
    # Sheet: 9A.INDIVIDUAL TREES

    "Small Rural Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    "Medium Rural Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    "Large Rural Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    "Very Large Rural Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    "Small Urban Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    "Medium Urban Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    "Large Urban Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    "Very Large Urban Tree": {
        "Good":      ["Pass 5 or 6 of 6 criteria",
                      "A. Native species (or >=70% native in a block)",
                      "B. Canopy predominantly continuous — gaps <10% of area; no gap >5 m (individual trees pass automatically)",
                      "C. Mature tree (or >50% mature in block)",
                      "D. Little or no adverse human impact; no regular pruning; retains >75% expected canopy",
                      "E. Natural ecological niches present (deadwood, cavities, ivy, loose bark)",
                      "F. >20% of canopy area oversails vegetation beneath"],
        "Moderate":  ["Pass 3-4 of 6 criteria"],
        "Poor":      ["Pass 2 or fewer of 6 criteria"],
    },

    # ── LINES OF TREES ─────────────────────────────────────────────────────
    # Sheet: 16A.LINE OF TREES

    "Line of trees": {
        "Good":      ["Pass all 5 criteria",
                      "A. >=70% of trees are native species",
                      "B. Canopy predominantly continuous — gaps <10% of area; no individual gap >5 m",
                      "C. >=1 tree has veteran features or natural ecological niches (deadwood, cavities, ivy, loose bark)",
                      "D. Undisturbed naturally-vegetated strip >=6 m on both sides (excl. grazing); root protection for veteran trees per standing advice",
                      "E. >=95% of trees in healthy condition; little adverse impact from livestock, pests, diseases or human activity"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Line of trees - associated with bank or ditch": {
        "Good":      ["Pass all 5 criteria",
                      "A. >=70% of trees are native species",
                      "B. Canopy predominantly continuous — gaps <10% of area; no individual gap >5 m",
                      "C. >=1 tree has veteran features or natural ecological niches (deadwood, cavities, ivy, loose bark)",
                      "D. Undisturbed naturally-vegetated strip >=6 m on both sides; root protection for veteran trees",
                      "E. >=95% of trees in healthy condition"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Ecologically valuable line of trees": {
        "Good":      ["Pass all 5 criteria",
                      "A. >=70% of trees are native species",
                      "B. Canopy predominantly continuous — gaps <10% of area; no individual gap >5 m",
                      "C. >=1 tree has veteran features or natural ecological niches (deadwood, cavities, ivy, loose bark)",
                      "D. Undisturbed naturally-vegetated strip >=6 m on both sides; root protection for veteran trees",
                      "E. >=95% of trees in healthy condition"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    # ── SCRUB ───────────────────────────────────────────────────────────────
    # Sheet: 20A.SCRUB

    "Blackthorn scrub": {
        "Good":      ["Pass all 5 criteria",
                      "A. Good example of its type; >=80% native scrub; >=3 native woody species; no single species >75% cover (except hazel, juniper, sea buckthorn in native range, or box)",
                      "B. Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present",
                      "C. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "D. Well-developed edge with scattered scrub and tall grassland/forbs between scrub and adjacent habitat",
                      "E. Clearings, glades or rides present within scrub providing sheltered edges"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Gorse scrub": {
        "Good":      ["Pass all 5 criteria",
                      "A. Good example of its type; >=80% native scrub; >=3 native woody species; no single species >75% cover (except applicable exceptions)",
                      "B. Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present",
                      "C. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "D. Well-developed edge with scattered scrub and tall grassland/forbs",
                      "E. Clearings, glades or rides present within scrub"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Hawthorn scrub": {
        "Good":      ["Pass all 5 criteria",
                      "A. Good example of its type; >=80% native scrub; >=3 native woody species; no single species >75% cover",
                      "B. Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present",
                      "C. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "D. Well-developed edge with scattered scrub and tall grassland/forbs",
                      "E. Clearings, glades or rides present within scrub"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Hazel scrub": {
        "Good":      ["Pass all 5 criteria",
                      "A. Good example of its type; hazel can be up to 100% cover; >=3 native woody species present",
                      "B. Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present",
                      "C. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "D. Well-developed edge with scattered scrub and tall grassland/forbs",
                      "E. Clearings, glades or rides present within scrub"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Mixed scrub": {
        "Good":      ["Pass all 5 criteria",
                      "A. Good example of its type; >=80% native scrub; >=3 native woody species; no single species >75% cover",
                      "B. Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present",
                      "C. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "D. Well-developed edge with scattered scrub and tall grassland/forbs",
                      "E. Clearings, glades or rides present within scrub"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Dunes with sea buckthorn (H2160)": {
        "Good":      ["Pass all 5 criteria",
                      "A. Good example of its type; sea buckthorn can be up to 100% cover in its restricted native range; >=3 native woody species",
                      "B. Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present",
                      "C. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "D. Well-developed edge with scattered scrub and tall grassland/forbs",
                      "E. Clearings, glades or rides present within scrub"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Willow scrub": {
        "Good":      ["Pass all 5 criteria",
                      "A. Good example of its type; >=80% native scrub; >=3 native woody species; no single species >75% cover",
                      "B. Seedlings, saplings, young shrubs and mature/ancient/veteran shrubs all present",
                      "C. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "D. Well-developed edge with scattered scrub and tall grassland/forbs",
                      "E. Clearings, glades or rides present within scrub"],
        "Moderate":  ["Pass 3-4 of 5 criteria"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    # ── URBAN ───────────────────────────────────────────────────────────────
    # Sheet: 22A.URBAN  |  Core criteria A, B, C apply to all urban types

    "Allotments": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met (no invasive non-native species at all)",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife (e.g. flowering species providing nectar at different times of year)",
                      "C. Invasive non-native plants (Schedule 9 WCA) completely absent for Good; others detrimental to wildlife <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met (invasive non-native <5%)"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Biodiverse green roof": {
        "Good":      ["Pass all 4 criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife (nectar sources at different times of year)",
                      "C. Invasive non-native plants (incl. buddleia) completely absent for Good; others <5% vegetated area",
                      "G. Varied substrate depth 80-150 mm; >=50% at 150 mm depth; planted/seeded with wildflowers and sedums; additional features (sand piles, stones, logs etc.) present"],
        "Moderate":  ["Pass 2-3 of 4 criteria, OR pass all 4 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 4 criteria"],
    },

    "Bioswale": {
        "Good":      ["Pass all 5 criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area",
                      "E1. Plant species mostly native; non-native species not detrimental to habitat or native wildlife",
                      "E2. Vegetation comprised of plant species suited to wetland or riparian situations"],
        "Moderate":  ["Pass 3-4 of 5 criteria, OR pass all 5 but criterion C not fully met"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Cemeteries and churchyards": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife (e.g. flowering species providing nectar at different times of year)",
                      "C. Invasive non-native plants (Schedule 9 WCA) completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Facade-bound green wall": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Ground based green wall": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Intensive green roof": {
        "Good":      ["Pass all 4 criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants (incl. buddleia) completely absent for Good; others <5% vegetated area",
                      "F. >=50% native and non-native wildflowers; >=70% of roof area is soil and vegetation (including water features)"],
        "Moderate":  ["Pass 2-3 of 4 criteria, OR pass all 4 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 4 criteria"],
    },

    "Open mosaic habitats on previously developed land": {
        "Good":      ["Pass all 4 criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area",
                      "D. Spatial variation: mosaic of bare substrate PLUS >=4 early successional communities from: (a) annuals, (b) mosses/liverworts, (c) lichens, (d) ruderals, (e) inundation species, (f) open grassland, (g) flower-rich grassland, (h) heathland, (i) pools"],
        "Moderate":  ["Pass 2-3 of 4 criteria, OR pass all 4 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 4 criteria"],
    },

    "Rain garden": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Sustainable drainage system": {
        "Good":      ["Pass all 5 criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area",
                      "E1. Plant species mostly native; non-native species not detrimental to habitat or wildlife",
                      "E2. Vegetation comprised of plant species suited to wetland or riparian situations"],
        "Moderate":  ["Pass 3-4 of 5 criteria, OR pass all 5 but criterion C not fully met"],
        "Poor":      ["Pass 2 or fewer of 5 criteria"],
    },

    "Vacant or derelict land": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Bare ground": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Ruderal/Ephemeral": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    "Tall forbs": {
        "Good":      ["Pass all 3 core criteria AND criterion C fully met",
                      "A. Vegetation structure varied — no single component >80% of area",
                      "B. Different plant species beneficial for wildlife",
                      "C. Invasive non-native plants completely absent for Good; others <5% vegetated area"],
        "Moderate":  ["Pass 2 of 3 core criteria, OR pass all 3 but criterion C not fully met"],
        "Poor":      ["Pass 0-1 of 3 core criteria"],
    },

    # ── WETLAND ─────────────────────────────────────────────────────────────
    # Sheet: 23A.WETLAND

    "Blanket bog": {
        "Good":      ["Pass 5-6 core criteria including A, AND pass additional criterion H",
                      "A. Water table at or near surface throughout year; no artificial drainage (ESSENTIAL for Good)",
                      "B. Good example of habitat type — characteristic vascular and non-vascular indicator species consistently present",
                      "C. Water supplies of good quality; clear water (low turbidity); no obvious signs of pollution",
                      "D. Scrub and scattered trees <10% cover",
                      "E. Bare ground <5%",
                      "F. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "H. Sphagnum spp. and cottongrasses at least Frequent; ericaceous dwarf shrub cover <75%"],
        "Moderate":  ["Pass 4-5 of 7 criteria, OR pass 6 but fail criterion A or H"],
        "Poor":      ["Pass 3 or fewer of 7 criteria"],
    },

    "Lowland raised bog": {
        "Good":      ["Pass 5-6 core criteria including A, AND pass additional criterion H",
                      "A. Water table at or near surface throughout year; no artificial drainage (ESSENTIAL for Good)",
                      "B. Good example of habitat type — characteristic indicator species consistently present",
                      "C. Water supplies of good quality; clear water; no obvious signs of pollution",
                      "D. Scrub and scattered trees <10% cover",
                      "E. Bare ground <5%",
                      "F. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "H. Sphagnum spp. and cottongrasses at least Frequent; ericaceous dwarf shrub cover <75%"],
        "Moderate":  ["Pass 4-5 of 7 criteria, OR pass 6 but fail criterion A or H"],
        "Poor":      ["Pass 3 or fewer of 7 criteria"],
    },

    "Fens (upland and lowland)": {
        "Good":      ["Pass 5-6 core criteria including A, AND pass additional criterion G",
                      "A. Water table at or near surface throughout year; no artificial drainage (ESSENTIAL for Good)",
                      "B. Good example of habitat type — characteristic indicator species consistently present",
                      "C. Water supplies of good quality; clear water; no obvious signs of pollution",
                      "D. Scrub and scattered trees <10% cover",
                      "E. Bare ground <5%",
                      "F. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "G. No more than 25% of area has continuous cover of litter preventing regeneration"],
        "Moderate":  ["Pass 4-5 of 7 criteria, OR pass 6 but fail criterion A or G"],
        "Poor":      ["Pass 3 or fewer of 7 criteria"],
    },

    "Purple moor grass and rush pastures": {
        "Good":      ["Pass 5-6 core criteria including A, AND pass additional criterion G",
                      "A. Water table at or near surface throughout year; no artificial drainage (ESSENTIAL for Good)",
                      "B. Good example of habitat type — characteristic indicator species consistently present",
                      "C. Water supplies of good quality; clear water; no obvious signs of pollution",
                      "D. Scrub and scattered trees <10% cover",
                      "E. Bare ground <5%",
                      "F. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "G. No more than 25% of area has continuous cover of litter preventing regeneration"],
        "Moderate":  ["Pass 4-5 of 7 criteria, OR pass 6 but fail criterion A or G"],
        "Poor":      ["Pass 3 or fewer of 7 criteria"],
    },

    "Reedbeds": {
        "Good":      ["Pass 5-6 core criteria including A, AND pass additional criterion I",
                      "A. Water table at or near surface throughout year; no artificial drainage (ESSENTIAL for Good)",
                      "B. Good example of habitat type — characteristic indicator species consistently present",
                      "C. Water supplies of good quality; clear water; no obvious signs of pollution",
                      "D. Scrub and scattered trees <10% cover",
                      "E. Bare ground <5%",
                      "F. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "I. Reedbed has diverse structure: 60-80% reeds Phragmites australis; open water >=10%; may include species-rich fen and/or wet woodland"],
        "Moderate":  ["Pass 4-5 of 7 criteria, OR pass 6 but fail criterion A or I"],
        "Poor":      ["Pass 3 or fewer of 7 criteria"],
    },

    "Floodplain wetland mosaic and CFGM": {
        "Good":      ["Pass 5-6 core criteria including A, AND pass additional criterion J",
                      "A. Water table at or near surface throughout year; no artificial drainage (ESSENTIAL for Good)",
                      "B. Good example of habitat type — characteristic indicator species consistently present",
                      "C. Water supplies of good quality; clear water; no obvious signs of pollution",
                      "D. Scrub and scattered trees <10% cover",
                      "E. Bare ground <5%",
                      "F. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% ground cover",
                      "J. All ditches recorded within the habitat achieve Good condition (Ditch condition sheet)"],
        "Moderate":  ["Pass 4-5 of 7 criteria, OR pass 6 but fail criterion A or J"],
        "Poor":      ["Pass 3 or fewer of 7 criteria"],
    },

    # ── WOODLAND ─────────────────────────────────────────────────────────────
    # Sheet: 24A.WOODLAND  |  Scored system: each of 13 indicators scores 1-3 points (max 39)

    "Lowland mixed deciduous woodland": {
        "Good":      ["Total score >32 (33-39 points) across all 13 indicators",
                      "A. Age distribution: 3 age-classes = 3pts; 2 = 2pts; 1 = 1pt",
                      "B. Herbivore damage: none significant = 3pts; <40% of woodland = 2pts; >=40% = 1pt",
                      "C. Invasive species: none = 3pts; no rhododendron/cherry laurel, others <10% = 2pts; rhododendron/cherry laurel present or others >=10% = 1pt",
                      "D. Native tree species: >=5 species = 3pts; 3-4 = 2pts; <=2 = 1pt",
                      "E. Native canopy/understorey: >80% native = 3pts; 50-80% = 2pts; <50% = 1pt",
                      "F. Open space: 10-20% temporary open space = 3pts; 21-40% = 2pts; <10% or >40% = 1pt",
                      "G. Regeneration: all 3 classes present (seedlings, saplings, 4-7cm DBH) = 3pts; 1-2 classes = 2pts; none = 1pt",
                      "H. Tree health: <=10% mortality, no pests/diseases = 3pts; 11-25% or low-risk pest = 2pts; >25% or high-risk pest = 1pt",
                      "I. Ground flora: NVC community with ancient woodland flora specialists = 3pts; recognisable woodland NVC = 2pts; no NVC = 1pt",
                      "J. Vertical structure: >=3 storeys or complex woodland = 3pts; 2 storeys = 2pts; <=1 storey = 1pt",
                      "K. Veteran trees: >=2 per hectare = 3pts; 1 per hectare = 2pts; none = 1pt",
                      "L. Deadwood: >=50% of survey plots have deadwood = 3pts; 25-50% = 2pts; <25% = 1pt",
                      "M. Disturbance: no nutrient enrichment or damaged ground = 3pts; <1ha enrichment/<20% damaged = 2pts; >=1ha enrichment/>=20% damaged = 1pt"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 (13-25) out of 39"],
    },

    "Lowland beech and yew woodland": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Other woodland; broadleaved": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Other woodland; mixed": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Wet woodland": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Upland oakwood": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Upland mixed ashwoods": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Upland birchwoods": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Native pine woodlands": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Other coniferous woodland": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    "Other Scot's pine woodland": {
        "Good":      ["Total score >32 (33-39 points) — same 13 indicators as Lowland mixed deciduous woodland",
                      "A-M. See Lowland mixed deciduous woodland for full indicator descriptions"],
        "Moderate":  ["Total score 26-32 out of 39"],
        "Poor":      ["Total score <26 out of 39"],
    },

    # ── WOOD-PASTURE AND PARKLAND ────────────────────────────────────────────
    # Sheet: 25A.WOOD-PASTURE & PARKLAND

    "Wood-pasture and parkland": {
        "Good":      ["Pass 7 or 8 criteria AND pass essential criterion A",
                      "A. Ancient and/or veteran trees present (ESSENTIAL for Good)",
                      "B. Three different life-stages of open grown or pollarded trees present (e.g. young, mature, veteran)",
                      "C. Native scrub present with variety of heights, widths, shapes and species compositions",
                      "D. Frequent decaying wood providing ecological niches (standing, attached and fallen deadwood; heart-rot; hollowing in trunk or major limbs)",
                      "E. No recent adverse impact on tree health from human activities, livestock, wild animals, pests or diseases",
                      "F. Ground cover comprises open unimproved or semi-improved habitats (medium distinctiveness or higher)",
                      "G. Ground cover subject to appropriate management providing structural diversity; no infill of trees/scrub by natural establishment or plantation",
                      "H. No invasive non-native plants (Schedule 9 WCA); suboptimal species <5% cover (excluding ancient/veteran trees)"],
        "Moderate":  ["Pass 5-6 criteria, OR pass 7 but fail essential criterion A"],
        "Poor":      ["Pass 4 or fewer criteria"],
    },

    # ── LAKES ───────────────────────────────────────────────────────────────
    # Sheet: 14A.LAKE  |  Uses FBA Habitat Naturalness Assessment

    "Aquifer fed naturally fluctuating water bodies": {
        "Good":      ["Average Habitat Naturalness Assessment Class 1 (Natural)",
                      "Uses the Freshwater Biological Association Habitat Naturalness Assessment methodology",
                      "Four attributes scored: physical, hydrological, chemical, and biological naturalness",
                      "FBA classes map to metric grades: Class 1 (Natural) = Good; Class 2-3 = Moderate; Class 4-5 (Least natural) = Poor",
                      "Full methodology available at: Discovering Priority Habitats in England (FBA)"],
        "Moderate":  ["Average Habitat Naturalness Assessment Class 3"],
        "Poor":      ["Average Habitat Naturalness Assessment Class 5 (Least natural)"],
    },

    "High alkalinity lakes": {
        "Good":      ["Average Habitat Naturalness Assessment Class 1 (Natural)",
                      "Four attributes scored: physical, hydrological, chemical, and biological naturalness",
                      "FBA classes map to metric grades: Class 1 = Good; Class 2-3 = Moderate; Class 4-5 = Poor"],
        "Moderate":  ["Average Habitat Naturalness Assessment Class 3"],
        "Poor":      ["Average Habitat Naturalness Assessment Class 5"],
    },

    "Low alkalinity lakes": {
        "Good":      ["Average Habitat Naturalness Assessment Class 1 (Natural)",
                      "FBA classes map to metric grades: Class 1 = Good; Class 2-3 = Moderate; Class 4-5 = Poor"],
        "Moderate":  ["Average Habitat Naturalness Assessment Class 3"],
        "Poor":      ["Average Habitat Naturalness Assessment Class 5"],
    },

    "Moderate alkalinity lakes": {
        "Good":      ["Average Habitat Naturalness Assessment Class 1 (Natural)",
                      "FBA classes map to metric grades: Class 1 = Good; Class 2-3 = Moderate; Class 4-5 = Poor"],
        "Moderate":  ["Average Habitat Naturalness Assessment Class 3"],
        "Poor":      ["Average Habitat Naturalness Assessment Class 5"],
    },

    "Marl lakes": {
        "Good":      ["Average Habitat Naturalness Assessment Class 1 (Natural)",
                      "FBA classes map to metric grades: Class 1 = Good; Class 2-3 = Moderate; Class 4-5 = Poor"],
        "Moderate":  ["Average Habitat Naturalness Assessment Class 3"],
        "Poor":      ["Average Habitat Naturalness Assessment Class 5"],
    },

    "Peat lakes": {
        "Good":      ["Average Habitat Naturalness Assessment Class 1 (Natural)",
                      "FBA classes map to metric grades: Class 1 = Good; Class 2-3 = Moderate; Class 4-5 = Poor"],
        "Moderate":  ["Average Habitat Naturalness Assessment Class 3"],
        "Poor":      ["Average Habitat Naturalness Assessment Class 5"],
    },

    "Reservoirs": {
        "Good":      ["Average Habitat Naturalness Assessment Class 1 (Natural)",
                      "FBA classes map to metric grades: Class 1 = Good; Class 2-3 = Moderate; Class 4-5 = Poor"],
        "Moderate":  ["Average Habitat Naturalness Assessment Class 3"],
        "Poor":      ["Average Habitat Naturalness Assessment Class 5"],
    },

    "Ornamental lake or pond": {
        "Good":      ["Lake assessment (ornamental lake): Average Habitat Naturalness Assessment Class 1",
                      "Pond assessment (ornamental pond) — Core criteria (all ponds):",
                      "A. Good water quality; clear water; no obvious signs of pollution",
                      "B. Semi-natural habitat (moderate distinctiveness or above) completely surrounding pond for >=10 m from edge",
                      "C. Duckweed Lemna spp. or filamentous algae <10% of water surface",
                      "D. Not artificially connected to other waterbodies (e.g. agricultural ditches or artificial pipework)",
                      "E. Natural water level fluctuation; no obvious artificial dams, pumps or pipework",
                      "F. No listed non-native plant or animal species (WFD UKTAG GB High Impact Species List)",
                      "G. Not artificially stocked with fish; if fish present, native assemblage at low density",
                      "Additional (non-woodland ponds only): H. Emergent/submerged/floating plants (excl. duckweed) >=50% of area <3 m deep; I. <=50% shaded by adjacent trees/scrub",
                      "Woodland pond scoring (7 criteria): all 7 = Good; 5-6 = Moderate; <=4 = Poor",
                      "Non-woodland pond scoring (9 criteria): all 9 = Good; 6-8 = Moderate; <=5 = Poor"],
        "Moderate":  ["Woodland pond: 5-6 of 7 criteria; Non-woodland pond: 6-8 of 9 criteria"],
        "Poor":      ["Woodland pond: <=4 of 7 criteria; Non-woodland pond: <=5 of 9 criteria"],
    },

    "Ponds (priority habitat)": {
        "Good":      ["All criteria passed — woodland pond (7 criteria) or non-woodland pond (9 criteria)",
                      "A. Good water quality; clear water (low turbidity); no obvious signs of pollution",
                      "B. Semi-natural habitat (moderate distinctiveness or above) surrounding entire pond for >=10 m",
                      "C. Duckweed or filamentous algae <10% of water surface",
                      "D. Not artificially connected to other waterbodies",
                      "E. Natural water level fluctuation; no artificial dams, pumps or pipework",
                      "F. No listed non-native plant or animal species (WFD UKTAG GB High Impact Species List)",
                      "G. Not artificially stocked with fish; if fish, native assemblage at low density",
                      "Additional (non-woodland ponds only): H. Emergent/submerged/floating plants >=50% of area <3 m deep; I. <=50% shaded by trees/scrub",
                      "Woodland pond (7 criteria): all 7 = Good; 5-6 = Moderate; <=4 = Poor",
                      "Non-woodland pond (9 criteria): all 9 = Good; 6-8 = Moderate; <=5 = Poor"],
        "Moderate":  ["Woodland pond: 5-6 of 7 criteria; Non-woodland pond: 6-8 of 9 criteria"],
        "Poor":      ["Woodland pond: <=4 of 7; Non-woodland pond: <=5 of 9"],
    },

    "Ponds (non-priority habitat)": {
        "Good":      ["All criteria passed — woodland pond (7 criteria) or non-woodland pond (9 criteria)",
                      "A. Good water quality; clear water; no obvious signs of pollution",
                      "B. Semi-natural habitat (moderate distinctiveness or above) surrounding entire pond for >=10 m",
                      "C. Duckweed or filamentous algae <10% of water surface",
                      "D. Not artificially connected to other waterbodies",
                      "E. Natural water level fluctuation; no artificial dams, pumps or pipework",
                      "F. No listed non-native plant or animal species",
                      "G. Not artificially stocked with fish; if fish, native assemblage at low density",
                      "Additional (non-woodland ponds only): H. Emergent/submerged/floating plants >=50% of area <3 m deep; I. <=50% shaded",
                      "Woodland pond (7 criteria): all 7 = Good; 5-6 = Moderate; <=4 = Poor",
                      "Non-woodland pond (9 criteria): all 9 = Good; 6-8 = Moderate; <=5 = Poor"],
        "Moderate":  ["Woodland pond: 5-6 of 7 criteria; Non-woodland pond: 6-8 of 9 criteria"],
        "Poor":      ["Woodland pond: <=4 of 7; Non-woodland pond: <=5 of 9"],
    },

}

