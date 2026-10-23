import os
import math
import re
import json
import uuid
import math
from pathlib import Path
from typing import Optional, List, Dict

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QComboBox, QSlider, QDoubleSpinBox,
    QGroupBox, QFrame, QSizePolicy, QScrollArea, QWidget,
    QTabWidget, QToolButton, QGridLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QButtonGroup, QApplication
)
from qgis.PyQt.QtGui import QImage, QPainter, QFont, QFontDatabase, QColor, QStandardItem, QStandardItemModel, QPixmap, QCursor
from qgis.PyQt.QtCore import Qt, QMetaType, QSize, QRectF, QEvent, QTimer
try:
    from qgis.PyQt.QtSvg import QSvgRenderer
    _HAS_SVG = True
except ImportError:
    _HAS_SVG = False

from qgis.gui import QgsMapTool, QgsMapToolEmitPoint, QgsMapToolPan, QgsRubberBand
from qgis.core import Qgis
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFeature, QgsGeometry,
    QgsPointXY, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat, QgsTextBufferSettings,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSingleSymbolRenderer,
    QgsMarkerSymbol, QgsRasterMarkerSymbolLayer,
    QgsFeatureRequest, QgsRectangle,
    QgsUnitTypes, QgsSvgMarkerSymbolLayer,
    QgsWkbTypes, QgsSymbol, QgsVectorFileWriter,
    QgsSimpleLineSymbolLayer, QgsSimpleFillSymbolLayer,
    QgsGeometryGeneratorSymbolLayer,
)

from .gridref_utils import gridref_to_osgb_en, GridRefError
from .config import measurement_ellipsoid
from . import theme


LAYER_NAME = "Ecological Features"
LINE_LAYER_NAME = "Ecological Linear Features"
POLY_LAYER_NAME = "Ecological Area Features"
BUFFER_LAYER_NAME = "Ecological Buffer Zones"
LAYER_CRS = QgsCoordinateReferenceSystem("EPSG:27700")

# High-separation, colour-blind-conscious buffer palette.  A single green ramp
# became difficult to distinguish over aerial imagery, especially where broad
# outer bands covered woodland and grassland. Equal distances still receive the
# same colour across nearby features.
BUFFER_BAND_COLOURS = (
    "#1B9E77", "#D9A400", "#5B7DBD", "#D95F02",
    "#7570B3", "#66A61E", "#E06C9F", "#A6761D",
)

# Point feature types that have a directional field of view (FOV) wedge.
# Placing one of these uses the three-click "aim" tool: click the position,
# then click in the direction it faces (the distance sets the view range),
# then swing to set the cone width. By default a point marker plus a separate
# FOV wedge polygon are created; types listed in SINGLE_OBJECT_DIRECTIONAL_TYPES
# below are instead stored as one feature (the wedge alone, drawing its icon).
DIRECTIONAL_TYPES = {"Camera", "Thermal Camera", "Surveyor", "Observer Station"}

# Maps a directional point type to the ftype used for its FOV wedge polygon
# (these wedge ftypes are styled in AREA_FEATURES above but are NOT user-pickable).
FOV_WEDGE_FTYPE = {
    "Camera":           "Camera Field of View",
    "Thermal Camera":   "Thermal Camera Field of View",
    "Surveyor":         "Surveyor Field of View",
    "Observer Station": "Observer Field of View",
}

# Directional types placed as a SINGLE object instead of two (a point marker +
# a separate FOV wedge polygon). For these, only the FOV wedge polygon is
# created; its symbology draws the type's icon at the wedge apex (vertex 1) via
# a geometry generator, so one feature represents both the station and its view.
SINGLE_OBJECT_DIRECTIONAL_TYPES = {"Observer Station", "Surveyor", "Camera",
                                   "Thermal Camera"}

# Inverse lookup for the single-object types: wedge ftype -> originating point
# type (used by the renderer to pick the icon drawn at the apex).
SINGLE_OBJECT_WEDGE_SOURCE = {
    FOV_WEDGE_FTYPE[t]: t for t in SINGLE_OBJECT_DIRECTIONAL_TYPES
}

# Opacity used for a custom survey-position's FOV wedge — matches the built-in
# FOV wedges (Camera/Surveyor 0.18) so a custom cone is see-through like theirs.
FOV_CUSTOM_OPACITY = "0.18"

# Built-in directional types whose FOV cone is auto-coloured from the shared
# qualitative palette (the same one the Building Mapper cycles) on every
# placement, so two cones that overlap are easy to tell apart — "what is looking
# at what". A frozenset literal on purpose: DIRECTIONAL_TYPES is mutated when
# custom survey positions register, but those keep the single colour the user
# picked for them, so they are deliberately excluded here.
AUTO_FOV_COLOUR_TYPES = frozenset({"Camera", "Thermal Camera", "Surveyor",
                                   "Observer Station"})

# Directional types drawn as a rotated ARROW instead of a field-of-view wedge —
# the conventional "suggested surveyor position" marker on a bat PRA figure.
# These stay ORDINARY POINT features: placement is two clicks (position, then
# the direction it faces), the compass bearing is stored in the point's
# "bearing" field, and the marker symbol is rotated by it. No wedge polygon is
# created, so they are deliberately NOT in SINGLE_OBJECT_DIRECTIONAL_TYPES /
# FOV_WEDGE_FTYPE. They ARE added to DIRECTIONAL_TYPES (below) so the picker
# routes them to the aim tool rather than the plain point tool.
ARROW_DIRECTIONAL_TYPES = {"Suggested Surveyor Position"}

# Rubber-band colour used while aiming an arrow type (no wedge ftype to read a
# colour from), matching the pink arrow symbol itself.
ARROW_AIM_COLOUR = "#E33D96"

DIRECTIONAL_TYPES |= ARROW_DIRECTIONAL_TYPES

FEATURE_EMOJI: Dict[str, str] = {
    # ── Bat features ──────────────────────────────────────────────────────
    "PRF": "🦇",
    "Bat Box": "🦇📦",
    "Roost Entry / Exit Point": "🦇",
    "Static Bat Detector": "📡",
    "Handheld Bat Detector": "📟",
    "Observer Station": "👁️",
    "Camera": "📷",
    "Thermal Camera": "🌡️📷",
    "Surveyor": "🧍",
    # ── Bird features ─────────────────────────────────────────────────────
    "Bird Nest (Active)":   "🪺",
    "Bird Nest (Inactive)": "🪹",
    "Bird Box": "🐦🏠",
    "Swift Brick": "🏗️",
    "Swift Box": "🏠",
    "Barn Owl Box": "🦉",
    "House Martin Cup": "🐦",
    # ── Invertebrate features ─────────────────────────────────────────────
    "Bee Brick": "🐝",
    "Bee Bank": "🐝",
    "Insect Box": "🐞🏠",
    "Stag Beetle Loggery": "🪲",
    # ── Mammal features ───────────────────────────────────────────────────
    "Wildlife Gap": "🦔",
    "Hedgehog House": "🦔",
    "Hedgehog Gap": "🦔",
    "Dormouse Box": "🐭",
    "Badger Sett": "🦡",
    "Badger Gate": "🦡",
    "Otter Holt": "🦦",
    "Water Vole Burrow": "🐾",
    # ── Amphibian / reptile ───────────────────────────────────────────────
    "Herptile Refuge": "🐸",
    "Reptile Hibernaculum": "🦎",
    "GCN Pond": "🐸",
    # ── Habitat / structural ──────────────────────────────────────────────
    "Water Body": "🌊",
    "Kingfisher Bank": "🐟",
    "Log Pile": "🪵",
    "Brash Pile": "🌿",
    "Veteran Tree": "🌳",
    "Green Roof": "🏡",
    # ── Field signs ───────────────────────────────────────────────────────
    "Badger Latrine": "🦡",
    "Deer Droppings": "🦌",
    "Fox Droppings": "🦊",
    "Rabbit Burrow": "🐇",
    "Bird Pellets": "🦉",
    "Amphibian Eggs": "🐸",
    "Stag Beetle": "🪲",
    # ── Mammal sightings ─────────────────────────────────────────────────
    "Squirrel": "🐿️",
    "Deer": "🦌",
    "Hedgehog": "🦔",
    "Fox": "🦊",
    "Badger": "🦡",
    # ── Habitat features ─────────────────────────────────────────────────
    "Tree Stump": "🌳",
    "Dead Wood": "🪵",
    "Tree": "🌳",
    "Tree Proposed for Removal": "🪓",
    # ── Survey / design ───────────────────────────────────────────────────
    "Building": "🏠",
    "Target Note": "🗒️",
}

FEATURE_SVG: Dict[str, str] = {
    # ── Bat features ──────────────────────────────────────────────────────
    "PRF": "prf_bat.svg",
    "Bat Box": "bat_box.svg",
    "Roost Entry / Exit Point": "roost_entry_exit.svg",
    "Static Bat Detector": "static_bat_detector.svg",
    "Observer Station": "observer_station.svg",
    "Camera": "camera.svg",
    "Thermal Camera": "thermal_camera.svg",
    "Surveyor": "surveyor.svg",
    # ── Bird features ─────────────────────────────────────────────────────
    "Bird Nest (Active)":   "bird_nest_active.svg",
    "Bird Nest (Inactive)": "bird_nest_inactive.svg",
    "Bird Box": "bird_box.svg",
    "Swift Brick": "swift_brick.svg",
    "Swift Box": "swift_box.svg",
    "Barn Owl Box": "barn_owl_box.svg",
    "House Martin Cup": "house_martin_cup.svg",
    # ── Invertebrate features ─────────────────────────────────────────────
    "Bee Brick": "bee_brick.svg",
    "Bee Bank": "bee_bank.svg",
    "Insect Box": "insect_box.svg",
    "Stag Beetle Loggery": "stag_beetle_loggery.svg",
    # ── Mammal features ───────────────────────────────────────────────────
    "Wildlife Gap": "wildlife_gap.svg",
    "Hedgehog House": "hedgehog_house.svg",
    "Hedgehog Gap": "hedgehog_gap.svg",
    "Dormouse Box": "dormouse_box.svg",
    "Badger Sett": "badger_sett.svg",
    "Badger Gate": "badger_gate.svg",
    "Otter Holt": "otter_holt.svg",
    "Water Vole Burrow": "water_vole_burrow.svg",
    # ── Amphibian / reptile ───────────────────────────────────────────────
    "Herptile Refuge": "herptile_refuge_frog_log.svg",
    "Reptile Hibernaculum": "reptile_hibernaculum.svg",
    "GCN Pond": "gcn_pond.svg",
    # ── Habitat / structural ──────────────────────────────────────────────
    "Water Body": "water_body.svg",
    "Kingfisher Bank": "kingfisher_bank.svg",
    "Log Pile": "log_pile.svg",
    "Brash Pile": "brash_pile.svg",
    "Veteran Tree": "veteran_tree.svg",
    "Green Roof": "green_roof.svg",
    # ── Survey / design ───────────────────────────────────────────────────
    "Building": "building.svg",
    "Target Note": "target_note.svg",
    # ── Species sightings ─────────────────────────────────────────────────
    "Badger": "badger.svg",
    "Deer": "deer.svg",
    "Fox": "fox.svg",
    "Hedgehog": "hedgehog.svg",
    "Squirrel": "squirrel.svg",
    # ── Field signs ───────────────────────────────────────────────────────
    "Badger Latrine": "badger_latrine.svg",
    "Deer Droppings": "deer_droppings.svg",
    "Fox Droppings": "fox_droppings.svg",
    "Rabbit Burrow": "rabbit_burrow.svg",
    "Bird Pellets": "bird_pellets.svg",
    "Amphibian Eggs": "amphibian_eggs.svg",
    "Stag Beetle": "stag_beetle.svg",
    # ── Existing habitat features ─────────────────────────────────────────
    "Tree Stump": "tree_stump.svg",
    "Dead Wood": "dead_wood.svg",
    "Tree": "tree.svg",
    "Tree Proposed for Removal": "tree_removal.svg",
    # ── 2026-06 catalogue: SVGs for every remaining feature type ───────────
    # Badge-style icons (white glyph on a taxon-coloured disc) generated to
    # match the house style, so SVG mode now has a real icon for every type
    # rather than falling back to the emoji. Emoji remains available via the
    # "Emoji symbols" opt-in. Colour key: bats #5B4FA0, mammals #7C5226,
    # herptiles #2E8B57, birds #2E7DB2, invertebrates #C2620A, trees/veg
    # #5F7F2D, survey #44535E, invasive species #B23A48.
    "Adder": "adder.svg",
    "American Mink": "american_mink.svg",
    "Ancient Tree": "ancient_tree.svg",
    "Ancient Woodland Indicator": "ancient_woodland_indicator.svg",
    "Annexe Sett": "annexe_sett.svg",
    "Artificial Sett": "artificial_sett.svg",
    "Badger Footprints": "badger_footprints.svg",
    "Badger Hair": "badger_hair.svg",
    "Badger Push-through": "badger_push_through.svg",
    "Badger Snuffle Hole": "badger_snuffle_hole.svg",
    "Bat Access Tile": "bat_access_tile.svg",
    "Bat Corpse": "bat_corpse.svg",
    "Bat Droppings": "bat_droppings.svg",
    "Bat Feeding Remains": "bat_feeding_remains.svg",
    "Bat Urine Staining": "bat_urine_staining.svg",
    "Bottle Trap": "bottle_trap.svg",
    "Brown Hairstreak Egg": "brown_hairstreak_egg.svg",
    "Brown Hare": "brown_hare.svg",
    "Brown Hare Form": "brown_hare_form.svg",
    "Buddleia": "buddleia.svg",
    "Common Frog": "common_frog.svg",
    "Common Lizard": "common_lizard.svg",
    "Common Toad": "common_toad.svg",
    "Confirmed Roost": "confirmed_roost.svg",
    "Cotoneaster": "cotoneaster.svg",
    "Dormouse Bridge": "dormouse_bridge.svg",
    "Dormouse Footprint Tunnel": "dormouse_footprint_tunnel.svg",
    "Dormouse Nest": "dormouse_nest.svg",
    "Dormouse Nest Tube": "dormouse_nest_tube.svg",
    "Egg Search Point": "egg_search_point.svg",
    "Floating Pennywort": "floating_pennywort.svg",
    "Giant Hogweed": "giant_hogweed.svg",
    "Glow Worm": "glow_worm.svg",
    "Gnawed Hazelnut": "gnawed_hazelnut.svg",
    "Grass Snake": "grass_snake.svg",
    "Grass Snake Egg-laying Heap": "grass_snake_egg_laying_heap.svg",
    "Great Crested Newt": "great_crested_newt.svg",
    "Gully-pot Ladder": "gully_pot_ladder.svg",
    "Handheld Bat Detector": "handheld_bat_detector.svg",
    "Harvest Mouse Nest": "harvest_mouse_nest.svg",
    "Hedgehog Droppings": "hedgehog_droppings.svg",
    "Hedgehog Footprint Tunnel": "hedgehog_footprint_tunnel.svg",
    "Heronry": "heronry.svg",
    "Himalayan Balsam": "himalayan_balsam.svg",
    "Integrated Bat Tube": "integrated_bat_tube.svg",
    "Japanese Knotweed": "japanese_knotweed.svg",
    "Kestrel Box": "kestrel_box.svg",
    "Little Owl Box": "little_owl_box.svg",
    "Main Sett": "main_sett.svg",
    "Mink Raft": "mink_raft.svg",
    "Mink Scat": "mink_scat.svg",
    "Mole Hill": "mole_hill.svg",
    "Montbretia": "montbretia.svg",
    "New Zealand Pigmyweed": "new_zealand_pigmyweed.svg",
    "Notable Plant": "notable_plant.svg",
    "Notable Tree": "notable_tree.svg",
    "Otter Couch": "otter_couch.svg",
    "Otter Footprints": "otter_footprints.svg",
    "Otter Slide": "otter_slide.svg",
    "Otter Spraint": "otter_spraint.svg",
    "Outlier Sett": "outlier_sett.svg",
    "Palmate Newt": "palmate_newt.svg",
    "Photo Viewpoint": "photo_viewpoint.svg",
    "Pine Marten": "pine_marten.svg",
    "Pine Marten Den Box": "pine_marten_den_box.svg",
    "Pine Marten Scat": "pine_marten_scat.svg",
    "Pitfall Trap": "pitfall_trap.svg",
    "Quadrat": "quadrat.svg",
    "Rabbit Warren": "rabbit_warren.svg",
    "Reptile Refugium / Mat": "reptile_refugium_mat.svg",
    "Rhododendron": "rhododendron.svg",
    "Sample Point": "sample_point.svg",
    "Sand Martin Wall": "sand_martin_wall.svg",
    "Schedule 1 Nest": "schedule_1_nest.svg",
    "Sloughed Skin": "sloughed_skin.svg",
    "Slow-worm": "slow_worm.svg",
    "Smooth Newt": "smooth_newt.svg",
    "Sparrow Terrace": "sparrow_terrace.svg",
    "Squirrel Drey": "squirrel_drey.svg",
    "Standing Deadwood / Snag": "standing_deadwood_snag.svg",
    "Starling Box": "starling_box.svg",
    "Subsidiary Sett": "subsidiary_sett.svg",
    "TPO Tree": "tpo_tree.svg",
    "Tawny Owl Box": "tawny_owl_box.svg",
    "Tern Raft": "tern_raft.svg",
    "Torch Count Point": "torch_count_point.svg",
    "Water Vole Droppings": "water_vole_droppings.svg",
    "Water Vole Feeding Station": "water_vole_feeding_station.svg",
    "Water Vole Latrine": "water_vole_latrine.svg",
    "Water Vole Runs": "water_vole_runs.svg",
    "White-clawed Crayfish": "white_clawed_crayfish.svg",
    "Wood Ant Nest": "wood_ant_nest.svg",
    "eDNA Sample Point": "edna_sample_point.svg",
}

LINE_FEATURES: Dict[str, Dict[str, str]] = {
    # ── Exclusion / construction ───────────────────────────────────────────
    "Temporary Fencing":            {"color": "#C97B16", "width": "0.6", "style": "dash"},
    "Reptile Exclusion Fencing":    {"color": "#cc4400", "width": "0.7", "style": "dash dot"},
    "Amphibian Exclusion Fencing":  {"color": "#aa2200", "width": "0.7", "style": "dash dot"},
    # ── Connectivity / movement ───────────────────────────────────────────
    "Connectivity Corridor":        {"color": "#2e8b2e", "width": "1.0", "style": "solid"},
    "Bat Flight Line":              {"color": "#4455cc", "width": "0.7", "style": "dot"},
    "Bat Commuting Route":          {"color": "#2233aa", "width": "1.0", "style": "dash"},
    "Otter Ledge":                  {"color": "#3F8266", "width": "0.8", "style": "dash"},
    "Newt Tunnel":                  {"color": "#228b44", "width": "0.8", "style": "dash dot"},
    "Animal Worn Path":             {"color": "#8B5e3c", "width": "0.7", "style": "dash"},
    # ── Habitat / survey ──────────────────────────────────────────────────
    "Riparian Buffer":              {"color": "#3F8266", "width": "1.0", "style": "solid"},
    "Habitat Management Zone":      {"color": "#4a8c2a", "width": "0.8", "style": "dash"},
    "Survey Transect":              {"color": "#8A968D", "width": "0.6", "style": "dot"},
    # ── Trees / arboriculture ─────────────────────────────────────────────
    "Line of Trees for Removal":            {"color": "#B3372F", "width": "1.2", "style": "dash"},
    "Line of Trees for Trimming / Pruning": {"color": "#C97B16", "width": "1.2", "style": "dash"},
    # ── Built enhancements ────────────────────────────────────────────────
    "Green Corridor":               {"color": "#66bb44", "width": "1.0", "style": "solid"},
    "SuDS / Drainage Feature":      {"color": "#0077aa", "width": "0.8", "style": "solid"},
}

# Area (polygon) feature types: fill colour, border colour, opacity, border style
AREA_FEATURES: Dict[str, Dict[str, str]] = {
    # ── Exclusion / Construction ───────────────────────────────────────────
    "Root Protection Area":           {"fill": "#C97B16", "border": "#cc5500", "opacity": "0.25", "style": "dash"},
    "Construction Exclusion Zone":    {"fill": "#ff4400", "border": "#cc2200", "opacity": "0.20", "style": "dash"},
    "Ecological Mitigation Zone":     {"fill": "#ffcc00", "border": "#cc9900", "opacity": "0.25", "style": "dash dot"},
    # ── Protected Species Territories ─────────────────────────────────────
    "Badger Territory":               {"fill": "#8B6340", "border": "#5a3e10", "opacity": "0.25", "style": "dash"},
    "Otter Spraint Territory":        {"fill": "#3F8266", "border": "#1F4A37", "opacity": "0.25", "style": "dash"},
    "Dormouse Home Range":            {"fill": "#d4aa40", "border": "#8B6320", "opacity": "0.25", "style": "dash"},
    "GCN Terrestrial Zone":           {"fill": "#44bb44", "border": "#228822", "opacity": "0.25", "style": "dash dot"},
    "Raptor Disturbance Buffer":      {"fill": "#cc44aa", "border": "#881188", "opacity": "0.20", "style": "dash"},
    # ── Protected Areas ───────────────────────────────────────────────────
    "Protected Habitat":              {"fill": "#22aa44", "border": "#116622", "opacity": "0.30", "style": "solid"},
    "Protected Area":                 {"fill": "#44ccaa", "border": "#228866", "opacity": "0.30", "style": "solid"},
    # ── Water Features ────────────────────────────────────────────────────
    "Pond":                           {"fill": "#2a7fc4", "border": "#1F4A37", "opacity": "0.55", "style": "solid"},
    "Lake":                           {"fill": "#1a60a0", "border": "#0e3a6a", "opacity": "0.55", "style": "solid"},
    "Waterway":                       {"fill": "#4aabdd", "border": "#2a7faa", "opacity": "0.55", "style": "solid"},
    # ── Survey Areas ──────────────────────────────────────────────────────
    "Bat Activity Survey Zone":       {"fill": "#4455cc", "border": "#222288", "opacity": "0.20", "style": "dot"},
    "Bat Foraging Area":              {"fill": "#221166", "border": "#110044", "opacity": "0.25", "style": "dash"},
    "Observer Field of View":         {"fill": "#888844", "border": "#555522", "opacity": "0.15", "style": "dot"},
    "Camera Field of View":           {"fill": "#2a6fd0", "border": "#103a78", "opacity": "0.18", "style": "solid"},
    "Thermal Camera Field of View":   {"fill": "#d0562a", "border": "#7a2810", "opacity": "0.18", "style": "solid"},
    "Surveyor Field of View":         {"fill": "#f2c200", "border": "#9a7a00", "opacity": "0.18", "style": "solid"},
    "Walkover Survey Area":           {"fill": "#8A968D", "border": "#555555", "opacity": "0.20", "style": "dot"},
    "Phase 2 Survey Area":            {"fill": "#aaaaaa", "border": "#666666", "opacity": "0.20", "style": "dash dot"},
    "Dusk / Dawn Survey Zone":        {"fill": "#332266", "border": "#221144", "opacity": "0.20", "style": "dot"},
    # ── Enhancement Design ────────────────────────────────────────────────
    "Wildflower Seed Mix Area":       {"fill": "#eebb22", "border": "#aa7700", "opacity": "0.30", "style": "dash"},
    "Native Planting Zone":           {"fill": "#44aa22", "border": "#226611", "opacity": "0.30", "style": "dash"},
    "Orchard / Fruit Tree Area":      {"fill": "#88cc44", "border": "#448822", "opacity": "0.30", "style": "dash"},
    "Hibernaculum Creation Zone":     {"fill": "#cc8844", "border": "#886622", "opacity": "0.25", "style": "dash dot"},
    "Rewilding / No-Mow Zone":        {"fill": "#66bb44", "border": "#338822", "opacity": "0.30", "style": "dash"},
    # ── Built Environment ─────────────────────────────────────────────────
    "Green Roof Footprint":           {"fill": "#44bb88", "border": "#228855", "opacity": "0.40", "style": "solid"},
    "Living Wall Area":               {"fill": "#66dd66", "border": "#33aa33", "opacity": "0.40", "style": "solid"},
    "Biosolar Roof Zone":             {"fill": "#ffdd44", "border": "#cc9900", "opacity": "0.40", "style": "solid"},
    # ── Drainage ──────────────────────────────────────────────────────────
    "SuDS Catchment Area":            {"fill": "#0077aa", "border": "#004466", "opacity": "0.30", "style": "solid"},
    "Flood Risk Zone":                {"fill": "#aaddff", "border": "#4499cc", "opacity": "0.35", "style": "dash"},
    "Riparian Management Strip":      {"fill": "#3F8266", "border": "#1F4A37", "opacity": "0.30", "style": "solid"},
}

# Grouped categories for the linear-feature tab picker.
LINE_FEATURE_GROUPS: List[tuple] = [
    ("🚧  Exclusion", [
        ("Fencing", [
            "Temporary Fencing",
            "Reptile Exclusion Fencing",
            "Amphibian Exclusion Fencing",
        ]),
    ]),
    ("🔗  Movement", [
        ("Bat", [
            "Bat Flight Line",
            "Bat Commuting Route",
        ]),
        ("Wildlife", [
            "Connectivity Corridor",
            "Otter Ledge",
            "Newt Tunnel",
            "Animal Worn Path",
        ]),
    ]),
    ("🌿  Habitat", [
        ("Management", [
            "Riparian Buffer",
            "Habitat Management Zone",
            "Survey Transect",
        ]),
        ("Trees", [
            "Line of Trees for Removal",
            "Line of Trees for Trimming / Pruning",
        ]),
    ]),
    ("🏗️  Built", [
        ("Enhancements", [
            "Green Corridor",
            "SuDS / Drainage Feature",
        ]),
    ]),
]

LINE_FEATURE_GROUPS_FLAT: List[tuple] = [
    ("🚧  Exclusion", ["Temporary Fencing", "Reptile Exclusion Fencing", "Amphibian Exclusion Fencing"]),
    ("🔗  Movement", ["Bat Flight Line", "Bat Commuting Route", "Connectivity Corridor", "Otter Ledge", "Newt Tunnel", "Animal Worn Path"]),
    ("🌿  Habitat", ["Riparian Buffer", "Habitat Management Zone", "Survey Transect", "Line of Trees for Removal", "Line of Trees for Trimming / Pruning"]),
    ("🏗️  Built", ["Green Corridor", "SuDS / Drainage Feature"]),
]

AREA_FEATURE_GROUPS: List[tuple] = [
    ("🚧  Exclusion", [
        ("Zones", [
            "Root Protection Area",
            "Construction Exclusion Zone",
            "Ecological Mitigation Zone",
        ]),
        ("Species Buffers", [
            "Raptor Disturbance Buffer",
            "GCN Terrestrial Zone",
        ]),
    ]),
    ("🐾  Territories", [
        ("Mammals", [
            "Badger Territory",
            "Otter Spraint Territory",
            "Dormouse Home Range",
        ]),
        ("Protected", [
            "Protected Habitat",
            "Protected Area",
        ]),
    ]),
    ("🔬  Survey", [
        ("Bat Survey", [
            "Bat Activity Survey Zone",
            "Bat Foraging Area",
            "Dusk / Dawn Survey Zone",
            "Observer Field of View",
        ]),
        ("General Survey", [
            "Walkover Survey Area",
            "Phase 2 Survey Area",
        ]),
    ]),
    ("🌱  Enhancement", [
        ("Planting", [
            "Wildflower Seed Mix Area",
            "Native Planting Zone",
            "Orchard / Fruit Tree Area",
            "Rewilding / No-Mow Zone",
        ]),
        ("Habitat Creation", [
            "Hibernaculum Creation Zone",
        ]),
        ("Built", [
            "Green Roof Footprint",
            "Living Wall Area",
            "Biosolar Roof Zone",
        ]),
    ]),
    ("💧  Water", [
        ("Features", [
            "Pond",
            "Lake",
            "Waterway",
        ]),
        ("Drainage", [
            "SuDS Catchment Area",
            "Flood Risk Zone",
            "Riparian Management Strip",
        ]),
    ]),
]

AREA_FEATURE_GROUPS_FLAT: List[tuple] = [
    ("🚧  Exclusion", ["Root Protection Area", "Construction Exclusion Zone", "Ecological Mitigation Zone", "Raptor Disturbance Buffer", "GCN Terrestrial Zone"]),
    ("🐾  Territories", ["Badger Territory", "Otter Spraint Territory", "Dormouse Home Range", "Protected Habitat", "Protected Area"]),
    ("🔬  Survey", ["Bat Activity Survey Zone", "Bat Foraging Area", "Dusk / Dawn Survey Zone", "Observer Field of View", "Walkover Survey Area", "Phase 2 Survey Area"]),
    ("🌱  Enhancement", ["Wildflower Seed Mix Area", "Native Planting Zone", "Orchard / Fruit Tree Area", "Rewilding / No-Mow Zone", "Hibernaculum Creation Zone", "Green Roof Footprint", "Living Wall Area", "Biosolar Roof Zone"]),
    ("💧  Water", ["Pond", "Lake", "Waterway", "SuDS Catchment Area", "Flood Risk Zone", "Riparian Management Strip"]),
]

# ── User-defined custom line / area feature types ──────────────────────────
# Stored as JSON in the active QGIS *profile* folder — i.e. OUTSIDE the plugin
# directory — so a user's own line/area types survive a plugin update or
# reinstall (the plugin lives at <profile>/python/plugins/biome, the store at
# <profile>/biome_custom_features.json).
CUSTOM_FEATURES_FILENAME = "biome_custom_features.json"


def custom_features_path() -> str:
    """Absolute path to the custom-feature store in the QGIS profile folder."""
    try:
        from qgis.core import QgsApplication
        base = QgsApplication.qgisSettingsDirPath()
    except Exception:
        base = os.path.expanduser("~")
    return os.path.join(base, CUSTOM_FEATURES_FILENAME)


def load_custom_features() -> dict:
    """Load the custom-feature store. Always returns the full skeleton
    {'lines':{}, 'areas':{}, 'points':{}}; a missing or corrupt file just yields
    empty dicts rather than crashing."""
    store = {"lines": {}, "areas": {}, "points": {}}
    path = custom_features_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for kind in ("lines", "areas", "points"):
                if isinstance(data.get(kind), dict):
                    store[kind] = data[kind]
    except Exception:
        pass
    return store


def custom_icons_dir() -> str:
    """Folder (in the QGIS profile) where uploaded custom point SVGs are kept,
    created on demand. Lives outside the plugin dir so icons survive updates."""
    d = os.path.join(os.path.dirname(custom_features_path()), "biome_custom_icons")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def save_custom_features(store: dict) -> bool:
    """Persist the custom-feature store to the profile folder. Returns success."""
    try:
        with open(custom_features_path(), "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def register_custom_features(store: dict) -> None:
    """Merge stored custom defs into the live module tables (LINE_FEATURES,
    AREA_FEATURES, FEATURE_SVG and the directional/FOV lookups) so the pickers,
    the selected-feature swatch and the categorized renderer all treat them
    exactly like the built-in types."""
    for name, props in (store.get("lines") or {}).items():
        LINE_FEATURES[name] = dict(props)
    for name, props in (store.get("areas") or {}).items():
        AREA_FEATURES[name] = dict(props)
    for name, props in (store.get("points") or {}).items():
        svg = props.get("svg")
        if svg:
            FEATURE_SVG[name] = svg          # absolute path → resolved as-is
        FEATURE_EMOJI.setdefault(name, "📍")  # emoji-mode fallback
        if props.get("directional"):
            wedge = name + " Field of View"
            DIRECTIONAL_TYPES.add(name)
            SINGLE_OBJECT_DIRECTIONAL_TYPES.add(name)
            FOV_WEDGE_FTYPE[name] = wedge
            SINGLE_OBJECT_WEDGE_SOURCE[wedge] = name
            fov_col = props.get("fov_color", "#2a6fd0")
            AREA_FEATURES[wedge] = {
                "fill": fov_col,
                "border": QColor(fov_col).darker(160).name(),
                "opacity": FOV_CUSTOM_OPACITY,   # see-through, like the built-ins
                "style": "solid",
                "custom": True,
            }


def unregister_custom_feature(kind: str, name: str) -> None:
    """Remove a single custom def from the live module tables (used by delete).
    Only ever called for user customs, never the built-ins."""
    if kind == "line":
        LINE_FEATURES.pop(name, None)
    elif kind == "area":
        AREA_FEATURES.pop(name, None)
    elif kind == "point":
        FEATURE_SVG.pop(name, None)
        FEATURE_EMOJI.pop(name, None)
        wedge = FOV_WEDGE_FTYPE.pop(name, None)
        DIRECTIONAL_TYPES.discard(name)
        SINGLE_OBJECT_DIRECTIONAL_TYPES.discard(name)
        if wedge:
            AREA_FEATURES.pop(wedge, None)
            SINGLE_OBJECT_WEDGE_SOURCE.pop(wedge, None)


# Order here determines display order; every key must exist in FEATURE_EMOJI.
# Top-level key is the TAB label; value is list of (group_header, [items]) pairs.
FEATURE_GROUPS: List[tuple] = [
    ("🐾  Species Records", [
        ("Bats", [
            "PRF",
            "Roost Entry / Exit Point",
        ]),
        ("Birds", [
            "Bird Nest (Active)",
            "Bird Nest (Inactive)",   # kept here per user preference
            "Bird Pellets",
        ]),
        ("Mammals", [
            "Badger",
            "Deer",
            "Fox",
            "Hedgehog",
            "Squirrel",
        ]),
        ("Amphibians & Reptiles", [
            "Amphibian Eggs",
        ]),
        ("Invertebrates", [
            "Stag Beetle",
        ]),
    ]),
    ("🔍  Field Signs", [
        ("Mammal Signs", [
            "Badger Latrine",
            "Deer Droppings",
            "Fox Droppings",
        ]),
        ("Structures & Burrows", [   # kept here per user preference
            "Badger Sett",
            "Otter Holt",
            "Water Vole Burrow",
            "Rabbit Burrow",
        ]),
    ]),
    ("🌳  Existing Features", [
        ("Water", [
            "Water Body",
            "GCN Pond",              # moved from Species Records
            "Kingfisher Bank",
        ]),
        ("Vegetation & Deadwood", [
            "Veteran Tree",
            "Tree",
            "Tree Proposed for Removal",
            "Tree Stump",
            "Dead Wood",
            "Log Pile",
            "Brash Pile",
        ]),
        ("Amphibians & Reptiles", [
            "Herptile Refuge",       # also in Enhancements
            "Reptile Hibernaculum",  # also in Enhancements
        ]),
        ("Built", [
            "Green Roof",
        ]),
    ]),
    ("🦇  Bat Survey", [
        ("Roost & Presence", [
            "PRF",
            "Roost Entry / Exit Point",
        ]),
        ("Equipment", [
            "Static Bat Detector",
            "Handheld Bat Detector",
            "Bat Box",
        ]),
        ("Survey Positions", [
            "Camera",
            "Thermal Camera",
            "Surveyor",
            "Observer Station",
        ]),
    ]),
    ("🏡  Enhancements", [
        ("Bats", [
            "Bat Box",
        ]),
        ("Birds", [
            "Bird Box",
            "Swift Brick",
            "Swift Box",
            "Barn Owl Box",
            "House Martin Cup",
        ]),
        ("Invertebrates", [
            "Bee Brick",
            "Bee Bank",
            "Insect Box",
            "Stag Beetle Loggery",
        ]),
        ("Mammals", [
            "Wildlife Gap",
            "Hedgehog House",
            "Hedgehog Gap",
            "Dormouse Box",
            "Badger Gate",
        ]),
        ("Amphibians & Reptiles", [
            "Herptile Refuge",       # also in Existing Features
            "Reptile Hibernaculum",  # also in Existing Features
        ]),
    ]),
    ("📋  Survey & Design", [
        ("Site", [
            "Building",
            "Target Note",
            "Observer Station",
        ]),
    ]),
    ("📹  Survey Positions", [
        ("Nocturnal / Emergence Survey", [
            "Camera",
            "Thermal Camera",
            "Surveyor",
            "Observer Station",
        ]),
    ]),
]

# Flat list used by hidden combo — each name appears only once, first occurrence wins
FEATURE_GROUPS_FLAT: List[tuple] = [
    ("🐾  Species Records", [
        "PRF", "Roost Entry / Exit Point",
        "Bird Nest (Active)", "Bird Nest (Inactive)", "Bird Pellets",
        "Badger", "Deer", "Fox", "Hedgehog", "Squirrel",
        "Amphibian Eggs", "Stag Beetle",
    ]),
    ("🔍  Field Signs", [
        "Badger Latrine", "Deer Droppings", "Fox Droppings",
        "Badger Sett", "Otter Holt", "Water Vole Burrow", "Rabbit Burrow",
    ]),
    ("🌳  Existing Features", [
        "Water Body", "GCN Pond", "Kingfisher Bank",
        "Veteran Tree", "Tree", "Tree Proposed for Removal", "Tree Stump", "Dead Wood", "Log Pile", "Brash Pile",
        "Herptile Refuge", "Reptile Hibernaculum",
        "Green Roof",
    ]),
    ("🦇  Bat Survey", [
        "Static Bat Detector", "Handheld Bat Detector", "Bat Box", "Observer Station",
    ]),
    ("🏡  Enhancements", [
        "Bird Box", "Swift Brick", "Swift Box", "Barn Owl Box", "House Martin Cup",
        "Bee Brick", "Bee Bank", "Insect Box", "Stag Beetle Loggery",
        "Wildlife Gap", "Hedgehog House", "Hedgehog Gap", "Dormouse Box", "Badger Gate",
    ]),
    ("📋  Survey & Design", [
        "Building", "Target Note",
    ]),
    ("📹  Survey Positions", [
        "Camera", "Thermal Camera", "Surveyor",
    ]),
]


# ── 2026-06 built-in catalogue expansion ───────────────────────────────────
# A large batch of standard UK survey features — field signs, badger sett
# classes, survey-method markers, enhancements and invasive species (INNS) —
# added on top of the literals above. They are folded into the existing
# tables/groups (below) so the pickers, search, recents and the categorized
# renderer treat them exactly like the original built-ins. Points with no SVG
# fall back to the emoji map, both on the map symbol and on the picker tile;
# a bespoke SVG can be added to FEATURE_SVG later to replace the emoji for any
# of these without touching this block.

_NEW_POINT_EMOJI: Dict[str, str] = {
    # Bats
    "Confirmed Roost": "🦇", "Bat Droppings": "🦇", "Bat Urine Staining": "🦇",
    "Bat Feeding Remains": "🦇", "Bat Corpse": "🦇",
    "Integrated Bat Tube": "🦇", "Bat Access Tile": "🦇",
    # Badger
    "Main Sett": "🦡", "Annexe Sett": "🦡", "Subsidiary Sett": "🦡", "Outlier Sett": "🦡",
    "Badger Snuffle Hole": "🦡", "Badger Footprints": "🦡", "Badger Hair": "🦡",
    "Badger Push-through": "🦡", "Artificial Sett": "🦡",
    # Otter
    "Otter Spraint": "🦦", "Otter Footprints": "🦦", "Otter Couch": "🦦", "Otter Slide": "🦦",
    # Water vole
    "Water Vole Latrine": "🐀", "Water Vole Droppings": "🐀",
    "Water Vole Feeding Station": "🐀", "Water Vole Runs": "🐀",
    # Mink
    "American Mink": "🐾", "Mink Scat": "🐾", "Mink Raft": "🛶",
    # Hedgehog
    "Hedgehog Droppings": "🦔", "Hedgehog Footprint Tunnel": "🦔",
    # Dormouse
    "Gnawed Hazelnut": "🌰", "Dormouse Nest": "🐭", "Dormouse Nest Tube": "🐭",
    "Dormouse Footprint Tunnel": "🐭", "Dormouse Bridge": "🐭",
    # Other mammals
    "Mole Hill": "🐾", "Squirrel Drey": "🐿️", "Harvest Mouse Nest": "🐁",
    "Rabbit Warren": "🐇", "Brown Hare": "🐇", "Brown Hare Form": "🐇",
    "Pine Marten": "🐾", "Pine Marten Scat": "🐾", "Pine Marten Den Box": "🐾",
    # Reptiles
    "Slow-worm": "🦎", "Common Lizard": "🦎", "Grass Snake": "🐍", "Adder": "🐍",
    "Sloughed Skin": "🐍", "Grass Snake Egg-laying Heap": "🐍", "Reptile Refugium / Mat": "🦎",
    # Amphibians / GCN
    "Common Frog": "🐸", "Common Toad": "🐸", "Smooth Newt": "🦎", "Palmate Newt": "🦎",
    "Great Crested Newt": "🦎",
    "eDNA Sample Point": "🧪", "Bottle Trap": "🪤", "Pitfall Trap": "🪤",
    "Torch Count Point": "🔦", "Egg Search Point": "🔦", "Gully-pot Ladder": "🪜",
    # Birds
    "Sparrow Terrace": "🐦", "Starling Box": "🐦", "Kestrel Box": "🦅",
    "Tawny Owl Box": "🦉", "Little Owl Box": "🦉", "Sand Martin Wall": "🐦",
    "Tern Raft": "🐦", "Heronry": "🐦", "Schedule 1 Nest": "🪺",
    # Invertebrates
    "White-clawed Crayfish": "🦞", "Glow Worm": "✨",
    "Brown Hairstreak Egg": "🦋", "Wood Ant Nest": "🐜",
    # Trees / botanical
    "Ancient Tree": "🌳", "Notable Tree": "🌳", "TPO Tree": "🌳",
    "Standing Deadwood / Snag": "🪵", "Ancient Woodland Indicator": "🌿",
    "Notable Plant": "🌼",
    # Survey method
    "Quadrat": "🔲", "Photo Viewpoint": "📸", "Sample Point": "🧪",
    # Invasive non-native species (INNS)
    "Japanese Knotweed": "🚫", "Himalayan Balsam": "🚫", "Giant Hogweed": "🚫",
    "Rhododendron": "🚫", "Cotoneaster": "🚫", "Buddleia": "🚫", "Montbretia": "🚫",
    "New Zealand Pigmyweed": "🚫", "Floating Pennywort": "🚫",
}

_NEW_POINT_GROUPS: List[tuple] = [
    ("🐾  Species Records", [
        ("Bats", ["Confirmed Roost"]),
        ("Birds", ["Heronry", "Schedule 1 Nest"]),
        ("Mammals", ["Brown Hare", "Pine Marten"]),
        ("Reptiles", ["Slow-worm", "Common Lizard", "Grass Snake", "Adder"]),
        ("Amphibians", ["Great Crested Newt", "Common Frog", "Common Toad", "Smooth Newt", "Palmate Newt"]),
        ("Invertebrates", ["White-clawed Crayfish", "Glow Worm"]),
    ]),
    ("🔍  Field Signs", [
        ("Bat Signs", ["Bat Droppings", "Bat Urine Staining", "Bat Feeding Remains", "Bat Corpse"]),
        ("Mammal Signs", ["Otter Spraint", "Otter Footprints", "Water Vole Latrine",
                          "Water Vole Droppings", "Water Vole Feeding Station",
                          "Hedgehog Droppings", "Mink Scat", "Pine Marten Scat",
                          "Mole Hill", "Gnawed Hazelnut"]),
        ("Badger Signs", ["Badger Snuffle Hole", "Badger Footprints",
                          "Badger Hair", "Badger Push-through"]),
        ("Reptile & Amphibian Signs", ["Sloughed Skin"]),
        ("Invertebrate Signs", ["Brown Hairstreak Egg"]),
        ("Structures & Burrows", ["Main Sett", "Annexe Sett", "Subsidiary Sett", "Outlier Sett",
                                  "Otter Couch", "Otter Slide", "Water Vole Runs",
                                  "Rabbit Warren", "Brown Hare Form", "Squirrel Drey",
                                  "Harvest Mouse Nest", "Dormouse Nest", "Wood Ant Nest"]),
    ]),
    ("🌳  Existing Features", [
        ("Vegetation & Deadwood", ["Ancient Tree", "Notable Tree", "TPO Tree",
                                   "Standing Deadwood / Snag"]),
        ("Botanical", ["Notable Plant", "Ancient Woodland Indicator"]),
    ]),
    ("🦇  Bat Survey", [
        ("Roost & Presence", ["Confirmed Roost", "Bat Droppings"]),
    ]),
    ("🏡  Enhancements", [
        ("Bats", ["Integrated Bat Tube", "Bat Access Tile"]),
        ("Birds", ["Sparrow Terrace", "Starling Box", "Kestrel Box",
                   "Tawny Owl Box", "Little Owl Box", "Sand Martin Wall", "Tern Raft"]),
        ("Mammals", ["Artificial Sett", "Dormouse Bridge", "Pine Marten Den Box"]),
        ("Amphibians & Reptiles", ["Grass Snake Egg-laying Heap", "Gully-pot Ladder"]),
    ]),
    ("🔬  Survey Methods", [
        ("Reptiles", ["Reptile Refugium / Mat"]),
        ("Amphibians / GCN", ["eDNA Sample Point", "Bottle Trap", "Torch Count Point",
                              "Egg Search Point", "Pitfall Trap"]),
        ("Dormouse", ["Dormouse Nest Tube", "Dormouse Footprint Tunnel"]),
        ("Mammals", ["Mink Raft", "Hedgehog Footprint Tunnel"]),
        ("Botanical", ["Quadrat"]),
        ("General", ["Photo Viewpoint", "Sample Point"]),
    ]),
    ("🚫  Invasive Species", [
        ("Terrestrial Plants", ["Japanese Knotweed", "Himalayan Balsam", "Giant Hogweed",
                                "Rhododendron", "Cotoneaster", "Buddleia", "Montbretia"]),
        ("Aquatic Plants", ["New Zealand Pigmyweed", "Floating Pennywort"]),
        ("Animals", ["American Mink"]),
    ]),
]

_NEW_LINE_FEATURES: Dict[str, Dict[str, str]] = {
    "Important Hedgerow": {"color": "#2e7d32", "width": "1.4", "style": "solid"},
}
_NEW_LINE_GROUPS: List[tuple] = [
    ("🌿  Habitat", [
        ("Hedgerows", ["Important Hedgerow"]),
    ]),
]

_NEW_AREA_FEATURES: Dict[str, Dict[str, str]] = {
    "Japanese Knotweed Stand": {"fill": "#cc3333", "border": "#991111", "opacity": "0.25", "style": "dash dot"},
    "Invasive Species Stand":  {"fill": "#cc5555", "border": "#992222", "opacity": "0.22", "style": "dash dot"},
    "Reptile Receptor Site":   {"fill": "#44aa44", "border": "#227722", "opacity": "0.25", "style": "dash"},
    "Amphibian Receptor Site": {"fill": "#44bb88", "border": "#228855", "opacity": "0.25", "style": "dash"},
    "Wader Scrape":            {"fill": "#aaddff", "border": "#4499cc", "opacity": "0.35", "style": "solid"},
}
_NEW_AREA_GROUPS: List[tuple] = [
    ("🚫  Invasive", [
        ("Stands", ["Japanese Knotweed Stand", "Invasive Species Stand"]),
    ]),
    ("🌱  Enhancement", [
        ("Habitat Creation", ["Reptile Receptor Site", "Amphibian Receptor Site", "Wader Scrape"]),
    ]),
]


# ── 2026-07 PRA (Preliminary Roost Assessment) catalogue ───────────────────
# The vocabulary a bat PRA figure needs: the individual potential roost features
# (PRFs) recorded on a building or tree, the runs that are mapped as lines
# rather than points, the loft/void outline, and the "suggested surveyor
# position" arrow. Folded into the live tables by _register_builtin_additions
# exactly like the 2026-06 batch above.

_PRA_POINT_SVG: Dict[str, str] = {
    # Roof
    "Gap Under Tiles":                 "gap_under_tiles.svg",
    "Gap Under Ridge Tiles":           "gap_under_ridge_tiles.svg",
    "Slipped / Raised Tile":           "slipped_tile.svg",
    "Gap Under Flashing":              "gap_under_flashing.svg",
    "Lifted Roofing Felt":             "lifted_roofing_felt.svg",
    # Eaves and boards
    "Gap Under Soffit":                "gap_under_soffit.svg",
    "Gap Behind Fascia / Barge Board": "gap_behind_fascia.svg",
    "Gap Under Weatherboarding":       "gap_under_weatherboarding.svg",
    # Masonry and openings
    "Missing / Eroded Mortar":         "missing_mortar.svg",
    "Gap Under Coping Stones":         "gap_under_coping.svg",
    "Air Brick / Vent":                "air_brick.svg",
    "Cavity Wall Access":              "cavity_wall_access.svg",
    "Gap Around Window / Door Frame":  "gap_around_frame.svg",
    "Loft Access Point":               "loft_access_point.svg",
    # Trees
    "Tree Cavity / Rot Hole":          "tree_cavity.svg",
    "Tree Split / Crack":              "tree_split.svg",
    "Lifting Bark":                    "lifting_bark.svg",
    "Woodpecker Hole":                 "woodpecker_hole.svg",
    "Dense Ivy":                       "dense_ivy.svg",
    # Survey position (arrow — see ARROW_DIRECTIONAL_TYPES)
    "Suggested Surveyor Position":     "suggested_surveyor_position.svg",
}

_PRA_POINT_EMOJI: Dict[str, str] = {
    "Gap Under Tiles": "🧱", "Gap Under Ridge Tiles": "🏠",
    "Slipped / Raised Tile": "🧱", "Gap Under Flashing": "🏠",
    "Lifted Roofing Felt": "🏠", "Gap Under Soffit": "🏠",
    "Gap Behind Fascia / Barge Board": "🏠",
    "Gap Under Weatherboarding": "🪵", "Missing / Eroded Mortar": "🧱",
    "Gap Under Coping Stones": "🧱", "Air Brick / Vent": "🧱",
    "Cavity Wall Access": "🧱", "Gap Around Window / Door Frame": "🪟",
    "Loft Access Point": "🪜",
    "Tree Cavity / Rot Hole": "🌳", "Tree Split / Crack": "🌳",
    "Lifting Bark": "🌳", "Woodpecker Hole": "🌳", "Dense Ivy": "🌿",
    "Suggested Surveyor Position": "➡️",
}

_PRA_BUILDING_PRFS = [
    "Gap Under Tiles", "Gap Under Ridge Tiles", "Slipped / Raised Tile",
    "Gap Under Flashing", "Lifted Roofing Felt", "Gap Under Soffit",
    "Gap Behind Fascia / Barge Board", "Gap Under Weatherboarding",
    "Missing / Eroded Mortar", "Gap Under Coping Stones", "Air Brick / Vent",
    "Cavity Wall Access", "Gap Around Window / Door Frame",
    "Loft Access Point",
]
_PRA_TREE_PRFS = [
    "Tree Cavity / Rot Hole", "Tree Split / Crack", "Lifting Bark",
    "Woodpecker Hole", "Dense Ivy",
]

_PRA_POINT_GROUPS: List[tuple] = [
    ("🦇  Bat Survey", [
        ("Building Roost Features", _PRA_BUILDING_PRFS),
        ("Tree Roost Features",     _PRA_TREE_PRFS),
        ("Survey Positions",        ["Suggested Surveyor Position"]),
    ]),
    ("📹  Survey Positions", [
        ("Nocturnal / Emergence Survey", ["Suggested Surveyor Position"]),
    ]),
]

# Roost features mapped as a RUN rather than a single point — a whole elevation
# of hanging tiles, or a continuous gap along the eaves.
_PRA_LINE_FEATURES: Dict[str, Dict[str, str]] = {
    "Hanging Tiles":       {"color": "#F2C200", "width": "1.6", "style": "dash"},
    "Gap Under Eaves":     {"color": "#5B4FA0", "width": "1.4", "style": "dash"},
    "Gap Along Ridge":     {"color": "#8A7ED8", "width": "1.4", "style": "dash"},
}
_PRA_LINE_GROUPS: List[tuple] = [
    ("🦇  Bat Survey", [
        ("Roost Features", ["Hanging Tiles", "Gap Under Eaves",
                            "Gap Along Ridge"]),
    ]),
]

# Internal roost spaces outlined on the map. Drawn as a bright cyan outline with
# an almost-clear fill so the aerial imagery beneath still reads.
_PRA_AREA_FEATURES: Dict[str, Dict[str, str]] = {
    "Loft Space":     {"fill": "#41D6E8", "border": "#00B8CE", "opacity": "0.10", "style": "solid"},
    "Roof Void":      {"fill": "#41D6E8", "border": "#0090A8", "opacity": "0.10", "style": "dash"},
    "Cellar / Basement Void": {"fill": "#7A6FC4", "border": "#4A3F94", "opacity": "0.12", "style": "dash"},
}
_PRA_AREA_GROUPS: List[tuple] = [
    ("🔬  Survey", [
        ("Bat Survey", ["Loft Space", "Roof Void", "Cellar / Basement Void"]),
    ]),
]

# Feature types that belong on a PRA map. map_exporter filters the ecological
# layers to exactly these, so keeping them derived from the tables above means a
# new PRF type lands on the map without a second edit there.
PRA_POINT_FTYPES = set(_PRA_POINT_SVG) | {
    "PRF", "Roost Entry / Exit Point", "Confirmed Roost", "Bat Droppings",
    "Bat Urine Staining", "Bat Feeding Remains", "Bat Corpse", "Bat Access Tile",
    "Target Note",
}
PRA_LINE_FTYPES = set(_PRA_LINE_FEATURES)
PRA_AREA_FTYPES = set(_PRA_AREA_FEATURES)


def _flat_contains(groups_flat: List[tuple], name: str) -> bool:
    return any(name in names for _lbl, names in groups_flat)


def _merge_nested_groups(groups: List[tuple], groups_flat: List[tuple],
                         additions: List[tuple]) -> None:
    """Fold nested (category → subgroup → [names]) *additions* into a GROUPS /
    GROUPS_FLAT pair in place. A name may appear in several subgroups/tabs (as
    several originals already do), but lands in GROUPS_FLAT only once — first
    category wins — keeping the 'each name appears once' invariant the renderer
    and the hidden combo rely on. Categories/subgroups not already present are
    appended in the order given."""
    for cat_label, sub_groups in additions:
        cat = next((c for c in groups if c[0] == cat_label), None)
        if cat is None:
            cat = (cat_label, [])
            groups.append(cat)
        flat = next((c for c in groups_flat if c[0] == cat_label), None)
        if flat is None:
            flat = (cat_label, [])
            groups_flat.append(flat)
        for sub_label, names in sub_groups:
            sub = next((s for s in cat[1] if s[0] == sub_label), None)
            if sub is None:
                sub = (sub_label, [])
                cat[1].append(sub)
            for name in names:
                if name not in sub[1]:
                    sub[1].append(name)
                if not _flat_contains(groups_flat, name):
                    flat[1].append(name)


def _register_builtin_additions() -> None:
    """Fold the 2026-06 catalogue expansion into the live module tables. Runs
    once at import, before any dialog is built."""
    for name, emoji in _NEW_POINT_EMOJI.items():
        FEATURE_EMOJI.setdefault(name, emoji)
    _merge_nested_groups(FEATURE_GROUPS, FEATURE_GROUPS_FLAT, _NEW_POINT_GROUPS)

    LINE_FEATURES.update(_NEW_LINE_FEATURES)
    _merge_nested_groups(LINE_FEATURE_GROUPS, LINE_FEATURE_GROUPS_FLAT, _NEW_LINE_GROUPS)

    AREA_FEATURES.update(_NEW_AREA_FEATURES)
    _merge_nested_groups(AREA_FEATURE_GROUPS, AREA_FEATURE_GROUPS_FLAT, _NEW_AREA_GROUPS)

    # 2026-07 PRA catalogue
    FEATURE_SVG.update(_PRA_POINT_SVG)
    for name, emoji in _PRA_POINT_EMOJI.items():
        FEATURE_EMOJI.setdefault(name, emoji)
    _merge_nested_groups(FEATURE_GROUPS, FEATURE_GROUPS_FLAT, _PRA_POINT_GROUPS)

    LINE_FEATURES.update(_PRA_LINE_FEATURES)
    _merge_nested_groups(LINE_FEATURE_GROUPS, LINE_FEATURE_GROUPS_FLAT, _PRA_LINE_GROUPS)

    AREA_FEATURES.update(_PRA_AREA_FEATURES)
    _merge_nested_groups(AREA_FEATURE_GROUPS, AREA_FEATURE_GROUPS_FLAT, _PRA_AREA_GROUPS)


_register_builtin_additions()


def _populate_grouped_combo(combo: "QComboBox", groups: List[tuple]) -> None:
    """Populate *combo* with category headers (non-selectable) and items."""
    model = QStandardItemModel()
    for group_label, items in groups:
        # Header row — bold, not selectable/enabled
        header = QStandardItem(group_label)
        header.setEnabled(False)
        header.setSelectable(False)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        model.appendRow(header)
        for name in items:
            item = QStandardItem("    " + name)  # indent to show grouping
            model.appendRow(item)
    combo.setModel(model)
    # Select the first real item (skip header at row 0)
    for i in range(model.rowCount()):
        if model.item(i).isEnabled():
            combo.setCurrentIndex(i)
            break


def _current_feature_text(combo: "QComboBox") -> str:
    """Return the trimmed feature name, stripping the indent added by grouping."""
    return combo.currentText().strip()


DEFAULT_EMOJI_FONTS = [
    "Segoe UI Emoji",
    "Apple Color Emoji",
    "Noto Color Emoji",
    "EmojiOne Color",
]


class _PickPointTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, on_point_cb, on_cancel_cb=None):
        super().__init__(canvas)
        self._cb = on_point_cb
        self._on_cancel = on_cancel_cb

    def canvasReleaseEvent(self, e):
        # Right-click stops (so keep-placing can be ended with a right-click);
        # only a left-click places a point.
        if e.button() == Qt.MouseButton.RightButton:
            if self._on_cancel:
                self._on_cancel()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._cb(self.toMapCoordinates(e.pos()))


class _CaptureAimTool(QgsMapTool):
    """Three-click directional placement tool for cameras / surveyors / observers.

    1st left-click  → sets the position (apex of the field of view).
    move + 2nd click → sets the bearing AND the view range (distance from click 1).
    move + 3rd click → swing the mouse to either side of the aim line to set the
                       field-of-view cone width; the angle between the aim line
                       and the cursor is the cone half-angle.
    right-click/Esc → cancels.

    On finish, calls ``on_finished_cb(origin, aim, fov_deg)`` where ``aim`` is the
    2nd-click point (giving bearing + range) and ``fov_deg`` is the cone width.
    ``fov_deg_getter`` / ``range_getter`` are no-arg callables used only as
    fall-backs when a click produces a near-zero angle or distance.

    In ``two_click`` mode (arrow types — a "suggested surveyor position" has a
    direction but no field of view) stage 3 is skipped: the 2nd click finishes
    the placement and ``fov_deg`` comes back as None.
    """

    def __init__(self, canvas, on_finished_cb, on_cancel_cb,
                 fov_deg_getter, colour, range_getter=None, two_click=False):
        super().__init__(canvas)
        self.canvas = canvas
        self._on_finished = on_finished_cb
        self._on_cancel = on_cancel_cb
        self._fov_deg_getter = fov_deg_getter
        self._range_getter = range_getter
        self._two_click = bool(two_click)
        self._origin = None  # QgsPointXY in map CRS (1st click)
        self._aim = None      # QgsPointXY in map CRS (2nd click → range + bearing)
        c = QColor(colour)
        fill = QColor(c)
        fill.setAlphaF(0.20)
        # Polygon rubber band for the wedge preview (stage 3)
        self._rb = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rb.setColor(c)
        self._rb.setFillColor(fill)
        self._rb.setWidth(2)
        # Line rubber band for the aim line preview (stage 2)
        self._rb_line = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._rb_line.setColor(c)
        self._rb_line.setWidth(2)

    def _cleanup(self):
        for rb, gt in ((self._rb, QgsWkbTypes.GeometryType.PolygonGeometry),
                       (self._rb_line, QgsWkbTypes.GeometryType.LineGeometry)):
            try:
                rb.reset(gt)
            except Exception:
                pass

    def _fov_from_mouse(self, origin, aim, mouse):
        """Cone width (deg) = twice the angle between the aim line and the cursor."""
        ca = math.atan2(aim.y() - origin.y(), aim.x() - origin.x())
        ma = math.atan2(mouse.y() - origin.y(), mouse.x() - origin.x())
        d = abs((ma - ca + math.pi) % (2 * math.pi) - math.pi)  # smallest diff
        fov = 2.0 * math.degrees(d)
        if fov < 1.0 and self._fov_deg_getter:
            fov = float(self._fov_deg_getter())
        return max(1.0, min(350.0, fov))

    def _wedge_points(self, origin, aim, fov_deg, segments=24):
        """Return a list of QgsPointXY forming the FOV wedge (apex + arc)."""
        dx = aim.x() - origin.x()
        dy = aim.y() - origin.y()
        rng = math.hypot(dx, dy)
        if rng <= 0:
            return None
        centre = math.atan2(dy, dx)
        half = math.radians(max(1.0, fov_deg) / 2.0)
        pts = [QgsPointXY(origin.x(), origin.y())]
        for i in range(segments + 1):
            a = centre - half + (2 * half) * (i / segments)
            pts.append(QgsPointXY(
                origin.x() + rng * math.cos(a),
                origin.y() + rng * math.sin(a),
            ))
        pts.append(QgsPointXY(origin.x(), origin.y()))
        return pts

    def canvasMoveEvent(self, e):
        mouse = self.toMapCoordinates(e.pos())
        if self._origin is None:
            return
        if self._aim is None:
            # Stage 2: previewing the aim line (bearing + range)
            self._rb_line.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self._rb_line.setToGeometry(
                QgsGeometry.fromPolylineXY([self._origin, mouse]), None)
        else:
            # Stage 3: previewing the wedge; cone width follows the cursor
            fov = self._fov_from_mouse(self._origin, self._aim, mouse)
            pts = self._wedge_points(self._origin, self._aim, fov)
            self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            if pts:
                self._rb.setToGeometry(QgsGeometry.fromPolygonXY([pts]), None)

    def canvasPressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._cleanup()
            self._origin = self._aim = None
            if self._on_cancel:
                self._on_cancel()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self.toMapCoordinates(e.pos())
        if self._origin is None:
            # 1st click — position
            self._origin = pt
        elif self._aim is None:
            # 2nd click — bearing + range. Arrow types finish here: there is no
            # cone to swing out, so the direction is all that is still needed.
            if self._two_click:
                origin = self._origin
                self._origin = self._aim = None
                self._cleanup()
                self._on_finished(origin, pt, None)
                return
            self._aim = pt
            self._rb_line.reset(QgsWkbTypes.GeometryType.LineGeometry)
        else:
            # 3rd click — cone width, then finish
            fov = self._fov_from_mouse(self._origin, self._aim, pt)
            origin, aim = self._origin, self._aim
            self._origin = self._aim = None
            self._cleanup()
            self._on_finished(origin, aim, fov)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._cleanup()
            self._origin = self._aim = None
            if self._on_cancel:
                self._on_cancel()

    def deactivate(self):
        self._cleanup()
        super().deactivate()


class _CaptureLineTool(QgsMapTool):
    """Polyline capture tool with rubber-band preview.

    Left-click  — add vertex
    Right-click — finish (≥2 pts) or cancel
    Escape      — cancel
    """

    def __init__(self, canvas, on_finished_cb, on_cancel_cb=None):
        super().__init__(canvas)
        self._on_finished = on_finished_cb
        self._on_cancel   = on_cancel_cb
        self._points: List[QgsPointXY] = []

        # Rubber band for preview
        self._rubber = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._rubber.setColor(QColor(255, 140, 0, 200))
        self._rubber.setWidth(2)

    def _cleanup(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self._points = []

    def canvasPressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            pts = list(self._points)
            self._cleanup()
            if len(pts) >= 2:
                self._on_finished(pts)
            elif self._on_cancel:
                self._on_cancel()
            return

        if e.button() == Qt.MouseButton.LeftButton:
            pt = self.toMapCoordinates(e.pos())
            self._points.append(pt)
            self._rubber.addPoint(pt, True)

    def canvasMoveEvent(self, e):
        if not self._points:
            return
        # Show a preview segment to the cursor
        preview = list(self._points) + [self.toMapCoordinates(e.pos())]
        self._rubber.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for p in preview:
            self._rubber.addPoint(p, True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._cleanup()
            if self._on_cancel:
                self._on_cancel()

    def deactivate(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.LineGeometry)
        super().deactivate()


class _CapturePolygonTool(QgsMapTool):
    """Polygon capture tool with rubber-band preview.

    Left-click  — add vertex
    Right-click — close and finish polygon (≥3 pts)
    Escape      — cancel
    """

    def __init__(self, canvas, on_finished_cb, on_cancel_cb=None):
        super().__init__(canvas)
        self._on_finished = on_finished_cb
        self._on_cancel   = on_cancel_cb
        self._points: List[QgsPointXY] = []

        self._rubber = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rubber.setColor(QColor(100, 200, 100, 160))
        self._rubber.setFillColor(QColor(100, 200, 100, 60))
        self._rubber.setWidth(2)

    def _cleanup(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        self._points = []

    def canvasPressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            pts = list(self._points)
            self._cleanup()
            if len(pts) >= 3:
                self._on_finished(pts)
            elif self._on_cancel:
                self._on_cancel()
            return

        if e.button() == Qt.MouseButton.LeftButton:
            pt = self.toMapCoordinates(e.pos())
            self._points.append(pt)
            self._rubber.addPoint(pt, True)

    def canvasMoveEvent(self, e):
        if not self._points:
            return
        preview = list(self._points) + [self.toMapCoordinates(e.pos())]
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        for p in preview:
            self._rubber.addPoint(p, True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._cleanup()
            if self._on_cancel:
                self._on_cancel()

    def deactivate(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        super().deactivate()


class _FreehandPolygonTool(QgsMapTool):
    """Freehand polygon tool — hold left button and drag to draw, release to finish."""

    def __init__(self, canvas, on_finished_cb, on_cancel_cb=None):
        super().__init__(canvas)
        self._on_finished = on_finished_cb
        self._on_cancel   = on_cancel_cb
        self._drawing     = False
        self._points: List[QgsPointXY] = []

        self._rubber = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rubber.setColor(QColor(100, 200, 100, 160))
        self._rubber.setFillColor(QColor(100, 200, 100, 60))
        self._rubber.setWidth(2)

    def _cleanup(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        self._points = []
        self._drawing = False

    def canvasPressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._cleanup()
            if self._on_cancel:
                self._on_cancel()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drawing = True
            self._points = []
            pt = self.toMapCoordinates(e.pos())
            self._points.append(pt)
            self._rubber.addPoint(pt, True)

    def canvasMoveEvent(self, e):
        if not self._drawing:
            return
        pt = self.toMapCoordinates(e.pos())
        self._points.append(pt)
        self._rubber.addPoint(pt, True)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drawing:
            pts = list(self._points)
            self._cleanup()
            if len(pts) >= 3:
                self._on_finished(pts)
            elif self._on_cancel:
                self._on_cancel()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._cleanup()
            if self._on_cancel:
                self._on_cancel()

    def deactivate(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        super().deactivate()


# The wrapping layout lives in panel_widgets now (the dashboard needs it too);
# imported under the old name so the icon grid below is untouched.
from .panel_widgets import _FlowLayout


class _FlowScroll(QScrollArea):
    """QScrollArea for a flow-layout grid. With widgetResizable the inner widget
    is sized to the viewport width; we then force its height to the layout's
    height-for-that-width so tall content scrolls instead of clipping."""

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self.widget()
        lay = w.layout() if w is not None else None
        if lay is not None and lay.hasHeightForWidth():
            w.setMinimumHeight(lay.heightForWidth(self.viewport().width()))


class _HoverWidget(QWidget):
    """A row widget that fires callbacks on hover-enter, hover-leave and click —
    used for the 'On the map' rows (hover highlights the feature on the canvas,
    click zooms to it). Buttons inside consume their own clicks."""
    def __init__(self, on_enter=None, on_leave=None, on_click=None, parent=None):
        super().__init__(parent)
        self._on_enter = on_enter
        self._on_leave = on_leave
        self._on_click = on_click

    def enterEvent(self, e):
        if self._on_enter:
            self._on_enter()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._on_leave:
            self._on_leave()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if self._on_click:
            self._on_click()
        super().mousePressEvent(e)


class GridRefDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self._prev_map_tool = None
        self._pick_tool = None
        # Bumped every time a placement tool is armed or aborted; a pending
        # continuous-placement re-arm timer only fires if the seq it captured is
        # still current, so switching feature type mid-loop can't resurrect the
        # old tool.
        self._arm_seq = 0
        self._run_count = 0   # features placed in the current armed run
        # When True, the user chose "Just looking" — eco layers are created in
        # memory (lost on project close) instead of a GeoPackage.
        self._scratch_mode = False

        self._undo_stack: List[tuple] = []  # each entry: (feat_uuid, buf_uuid_or_None)
        self._redo_stack: List[dict] = []   # snapshots of undone placements
        self._main_panel = None  # set by BNGMainPanel to bridge global undo
        self._icon_size_mm = 6
        self._recent_features: List[str] = []   # up to 5 recently placed feature types

        # User-defined custom line/area types live in the QGIS profile folder
        # (outside the plugin dir) so they survive plugin updates. Load + merge
        # them now, before the pickers/combo/renderer are built, so they are
        # treated exactly like the built-in types.
        self._custom_store = load_custom_features()
        register_custom_features(self._custom_store)
        self._custom_point_names = set()   # custom point fnames currently shown

        self.setWindowTitle("Biome")
        self.setMinimumWidth(760)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        # Match the plugin's "moss & chalk" identity: bundled Inter, themed
        # inputs/combos/scrollbars, chalk surface. Per-widget styles below only
        # declare what differs. (The QWidget rule sets font/colour only — no
        # border — so it can't cascade a stray border onto children.)
        _icons = os.path.join(os.path.dirname(__file__), "icons")
        _plus = os.path.join(_icons, "plus.svg").replace("\\", "/")
        _minus = os.path.join(_icons, "minus.svg").replace("\\", "/")
        # Clear +/- stepper buttons on every spin box (distance, diameter,
        # rotation, sides, FOV) instead of the cramped default arrows.
        _spin_qss = (
            "QSpinBox, QDoubleSpinBox{padding-right:24px;}"
            "QSpinBox::up-button, QDoubleSpinBox::up-button,"
            " QSpinBox::down-button, QDoubleSpinBox::down-button{"
            "subcontrol-origin:border; width:21px; background:%s;"
            " border-left:1px solid %s;}" % (theme.SUNKEN, theme.BORDER_STRONG)
            + "QSpinBox::up-button, QDoubleSpinBox::up-button{"
            "subcontrol-position:top right; border-top-right-radius:6px;}"
            + "QSpinBox::down-button, QDoubleSpinBox::down-button{"
            "subcontrol-position:bottom right; border-bottom-right-radius:6px;}"
            + "QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
            " QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover{"
            "background:%s;}" % theme.ACCENT_SOFT
            + "QSpinBox::up-arrow, QDoubleSpinBox::up-arrow{"
            "image:url(%s); width:10px; height:10px;}" % _plus
            + "QSpinBox::down-arrow, QDoubleSpinBox::down-arrow{"
            "image:url(%s); width:10px; height:10px;}" % _minus
        )
        self.setStyleSheet(
            theme.dock_qss() + "QDialog{background:%s;}" % theme.SURFACE + _spin_qss)
        # This dialog can be opened independently of the main Biome panel, so
        # install the tooltip colour enforcer here as well.  Some QGIS themes
        # otherwise render the intended white tooltip text on a pale green
        # background, making longer hints such as Relocate difficult to read.
        theme.style_tooltips()

        layout = QVBoxLayout()
        layout.setSpacing(6)

        # ── Geometry type selector — three toggle buttons ─────────────────
        geom_row = QHBoxLayout()
        _type_lbl = QLabel("Type:")
        _type_lbl.setStyleSheet(
            "font-family:%s; font-weight:500; color:%s;" % (theme.FONT_MEDIUM, theme.INK))
        geom_row.addWidget(_type_lbl)
        self._geom_btns: Dict[str, QPushButton] = {}
        self.geometry_combo = QComboBox()   # kept hidden for downstream compat
        self.geometry_combo.addItems(["Point", "Line", "Area"])
        self.geometry_combo.setVisible(False)
        layout.addWidget(self.geometry_combo)
        _geom_qss = (
            "QPushButton{border:1px solid %s; border-radius:8px; padding:4px 14px;"
            " background:%s; color:%s; font-family:%s; font-size:12px; font-weight:500;}"
            % (theme.BORDER_STRONG, theme.CARD, theme.MUTED, theme.FONT_MEDIUM)
            + "QPushButton:checked{background:%s; color:white; border-color:%s;}"
            % (theme.ACCENT, theme.ACCENT_DARK)
            + "QPushButton:hover:!checked{background:%s; color:%s; border-color:%s;}"
            % (theme.ACCENT_SOFT, theme.ACCENT_DARK, theme.BORDER_STRONG)
        )
        _geom_icons = {"Point": "map-pin", "Line": "line",
                       "Area": "polygon", "Shape": "circle-dashed"}
        for mode in ("Point", "Line", "Area"):
            b = QPushButton("  " + mode)
            b.setCheckable(True)
            b.setFixedHeight(32)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setIcon(self._two_state_icon(_geom_icons[mode], theme.MUTED, "#FFFFFF", 15))
            b.setIconSize(QSize(15, 15))
            b.setStyleSheet(_geom_qss)
            b.clicked.connect(lambda checked, m=mode: self._on_geom_btn_clicked(m))
            geom_row.addWidget(b)
            self._geom_btns[mode] = b
        geom_row.addStretch(1)
        layout.addLayout(geom_row)
        self._geom_btns["Point"].setChecked(True)

        # Hidden combo — keeps downstream code unchanged; populated with ALL feature types
        self.type_combo = QComboBox()
        self.type_combo.setVisible(False)
        _all_features_flat = (
            FEATURE_GROUPS_FLAT
            + LINE_FEATURE_GROUPS_FLAT
            + AREA_FEATURE_GROUPS_FLAT
        )
        _populate_grouped_combo(self.type_combo, _all_features_flat)
        layout.addWidget(self.type_combo)

        # ── Search-first feature picker ───────────────────────────────────
        # Typing filters every feature type across all three geometries into a
        # single results grid; clearing it returns to browse-by-category.
        # Picking any result infers the geometry, so there's no need to choose
        # Point / Line / Area first.
        search_row = QHBoxLayout()
        _search_lbl = QLabel("Find:")
        _search_lbl.setStyleSheet(
            "font-family:%s; font-weight:500; color:%s;" % (theme.FONT_MEDIUM, theme.INK))
        search_row.addWidget(_search_lbl)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Search features — e.g. badger, fence, pond…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_edit)
        layout.addLayout(search_row)

        # The cross-geometry category chips were removed — browsing is by the
        # per-geometry tabs (each with an "All" tab); search spans geometries.
        # _active_category stays None so the search grid applies no category
        # filter.
        self._active_category = None

        self._picker_stack = QStackedWidget()
        # Prefer three complete tile rows. On a shorter screen only this swatch
        # region is allowed to contract; its internal scroll area then handles
        # the overflow while the controls above and placement card stay fixed.
        self._picker_stack.setMinimumHeight(220)
        self._picker_stack.setMaximumHeight(368)
        self._picker_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pt_icon_btns: Dict[str, list] = {}   # fname -> [QToolButton, ...]
        self._geom_tile_btns: Dict[tuple, list] = {}  # (kind, fname) -> [tile, ...]

        # Each picker is built by a (re)usable, group-aware method so adding or
        # deleting a custom rebuilds just that picker — placing each custom in
        # the tab it was created under (or the Custom tab when added there).
        self._pt_tabs = QTabWidget(); self._pt_tabs.setDocumentMode(True)
        self._build_point_tabs()
        self._picker_stack.addWidget(self._pt_tabs)   # stack index 0

        self._ln_tabs = QTabWidget(); self._ln_tabs.setDocumentMode(True)
        self._build_line_tabs()
        self._picker_stack.addWidget(self._ln_tabs)   # stack index 1

        self._ar_tabs = QTabWidget(); self._ar_tabs.setDocumentMode(True)
        self._build_area_tabs()
        self._picker_stack.addWidget(self._ar_tabs)   # stack index 2

        # Unified search-results page (index 3) — one flow grid of tiles drawn
        # from all geometries; populated on demand by _on_search_changed.
        self._results_scroll, self._results_flow = self._new_flow_tab()
        self._picker_stack.addWidget(self._results_scroll)   # stack index 3
        self._PICKER_PAGE = {"Point": 0, "Line": 1, "Area": 2, "Shape": 2}

        # Start with roughly seven tile columns plus the on-map rail, giving the
        # dialog a more vertical desktop footprint. Once laid out, the fit
        # helper removes any remainder beside the final tile column.
        self._initial_width = max(
            1080,
            20 + (7 * 108) + (6 * 8) + 10 + 280 + 16,
            self._pt_tabs.tabBar().sizeHint().width() + 340,
        )
        self.setMinimumWidth(900)

        # Shape mode reuses the SAME area feature picker (index 2) — it places
        # the chosen area feature type as a parametric shape instead of a
        # free-drawn polygon. No extra picker page needed; we just show index 2
        # and reveal the shape-options group below.

        layout.addWidget(self._picker_stack)

        # ── Selected feature & placement card ─────────────────────────────
        self._placement_card = QWidget()
        self._placement_card.setObjectName("selectedPlacementCard")
        self._placement_card.setStyleSheet(
            "QWidget#selectedPlacementCard{background:#EEF7F2;"
            "border:1px solid #CEE3D6;border-radius:10px;}"
            "QWidget#selectedPlacementCard QLabel{background:transparent;border:none;}"
            "QWidget#selectedPlacementCard QGroupBox{background:rgba(255,255,255,0.45);"
            "border:1px solid rgba(67,101,80,0.13);border-radius:8px;margin-top:8px;}"
            "QWidget#selectedPlacementCard QGroupBox::title{subcontrol-origin:margin;"
            "left:10px;padding:0 4px;color:#315B48;}"
        )
        placement_layout = QVBoxLayout(self._placement_card)
        placement_layout.setContentsMargins(10, 8, 10, 8)
        placement_layout.setSpacing(5)
        selected_heading = QLabel("Selected feature & placement")
        selected_heading.setStyleSheet(
            "font-family:%s;font-size:14px;font-weight:600;color:%s;"
            % (theme.FONT_SEMIBOLD, theme.INK))

        swatch_row = QHBoxLayout()
        self.colour_swatch = QLabel()
        self.colour_swatch.setFixedSize(56, 56)
        self.colour_swatch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.colour_swatch.setToolTip("Symbol/colour used for this feature type on the map")
        self.selected_type_lbl = QLabel("")
        self.selected_type_lbl.setStyleSheet(
            "font-family:%s;font-size:16px;font-weight:600;color:%s;"
            % (theme.FONT_SEMIBOLD, theme.INK))
        selected_text = QVBoxLayout()
        selected_text.setSpacing(2)
        selected_text.addStretch(1)
        selected_text.addWidget(self.selected_type_lbl)
        selected_text.addStretch(1)
        swatch_row.addWidget(self.colour_swatch)
        swatch_row.addLayout(selected_text)
        swatch_row.addStretch(1)

        # ── Recent features quick-pick ────────────────────────────────────
        self._recent_widget = QWidget()
        recent_row = QHBoxLayout(self._recent_widget)
        recent_row.setContentsMargins(0, 0, 0, 0)
        recent_lbl = QLabel("Recent:")
        recent_lbl.setToolTip("Click to re-select a recently used feature type")
        recent_row.addWidget(recent_lbl)
        self._recent_btns: List[QPushButton] = []
        for _ in range(5):
            btn = QPushButton("")
            btn.setFixedHeight(22)
            btn.setVisible(False)
            btn.setStyleSheet(
                "QPushButton{font-size:10px; padding:2px 8px; border:1px solid %s;"
                " border-radius:9px; color:%s; background:%s;}"
                % (theme.BORDER, theme.MUTED, theme.CARD)
                + "QPushButton:hover{background:%s; border-color:%s; color:%s;}"
                % (theme.ACCENT_SOFT, theme.BORDER_STRONG, theme.ACCENT_DARK)
            )
            recent_row.addWidget(btn)
            self._recent_btns.append(btn)
        recent_row.addStretch(1)
        self._recent_widget.setVisible(False)
        # Recent choices share the heading line.  Revealing them after the first
        # placement must not add a new row and push the card beneath the footer.
        placement_header = QHBoxLayout()
        placement_header.setSpacing(12)
        placement_header.addWidget(selected_heading)
        placement_header.addWidget(self._recent_widget, 1)
        placement_layout.addLayout(placement_header)

        # ── Instruction label ─────────────────────────────────────────────
        self.instruction_lbl = QLabel(
            "Click 'Pick on map' to place a feature, or enter coordinates below.")
        self.instruction_lbl.setStyleSheet(
            "QLabel{background:%s; border:1px solid %s; border-radius:8px;"
            " padding:6px 9px; color:%s;}" % (theme.ACCENT_SOFT, theme.BORDER, theme.ACCENT_DARK)
        )
        self.instruction_lbl.setWordWrap(True)
        self.instruction_lbl.setVisible(False)
        placement_layout.addWidget(self.instruction_lbl)

        # ── Grid ref row (Point only) ─────────────────────────────────────
        self.gridref_widget = QWidget()
        row1 = QHBoxLayout(self.gridref_widget)
        row1.setContentsMargins(0, 0, 0, 0)
        gridref_lbl = QLabel("Coordinates:")
        gridref_lbl.setFixedWidth(86)
        row1.addWidget(gridref_lbl)
        self.gridref_edit = QLineEdit()
        self.gridref_edit.setPlaceholderText(
            "Grid ref, E/N, latitude/longitude, or EPSG:code x,y")
        row1.addWidget(self.gridref_edit)
        self.add_from_ref_btn = QPushButton("Add coordinate")
        self.add_from_ref_btn.setFixedWidth(138)
        row1.addWidget(self.add_from_ref_btn)
        # ── Label ─────────────────────────────────────────────────────────
        self.label_widget = QWidget()
        row2 = QHBoxLayout(self.label_widget)
        row2.setContentsMargins(0, 0, 0, 0)
        label_lbl = QLabel("Label:")
        label_lbl.setFixedWidth(86)
        row2.addWidget(label_lbl)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Leave blank for auto-number")
        row2.addWidget(self.label_edit)

        # Keep the placement card compact and horizontal; the overall dialog,
        # not this card, carries the taller/narrower aspect ratio.
        identity_widget = QWidget()
        identity_widget.setLayout(swatch_row)
        identity_widget.setMaximumWidth(245)
        fields_widget = QWidget()
        fields_layout = QVBoxLayout(fields_widget)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fields_layout.setSpacing(5)
        fields_layout.addWidget(self.gridref_widget)
        fields_layout.addWidget(self.label_widget)
        compact_row = QHBoxLayout()
        compact_row.setSpacing(12)
        compact_row.addWidget(identity_widget, 0)
        compact_row.addWidget(fields_widget, 1)
        placement_layout.addLayout(compact_row)

        # ── Point-only controls group ─────────────────────────────────────
        # Always shown in Point mode (no "More options" fold). Auto-numbering is
        # always on (a custom name in Label overrides it) and label show/hide is
        # per-feature (Edit menu on the "On the map" rail), so no global
        # checkboxes are needed here.
        self.point_controls = QWidget()
        pc_layout = QVBoxLayout(self.point_controls)
        pc_layout.setContentsMargins(0, 0, 0, 0)
        pc_layout.setSpacing(4)

        self._icon_size_widget = QWidget()
        row4 = QHBoxLayout()
        row4.setContentsMargins(2, 2, 2, 2)
        row4.addWidget(QLabel("Icon size:"))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setMinimum(2)
        self.size_slider.setMaximum(20)
        self.size_slider.setValue(self._icon_size_mm)
        self.size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.size_slider.setTickInterval(1)
        self.size_slider.setMaximumWidth(150)   # doesn't need the full width
        row4.addWidget(self.size_slider)
        self.size_value_lbl = QLabel(f"{self._icon_size_mm} mm")
        self.size_value_lbl.setMinimumWidth(44)
        self.size_value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row4.addWidget(self.size_value_lbl)
        row4.addStretch(1)
        self._icon_size_widget.setLayout(row4)

        # ── Directional field-of-view controls (cameras / surveyors) ──────
        # Shown only when a directional point type is selected. The bearing
        # and range come from the two-click placement on the map; these set
        # the cone width and a fallback range.
        self.fov_group = QGroupBox("Field of view (cameras & surveyors)")
        fov_layout = QVBoxLayout(self.fov_group)
        fov_layout.setSpacing(4)

        fov_row = QHBoxLayout()
        fov_row.addWidget(QLabel("Default cone:"))
        self.fov_angle_spin = QDoubleSpinBox()
        self.fov_angle_spin.setRange(5.0, 360.0)
        self.fov_angle_spin.setSingleStep(5.0)
        self.fov_angle_spin.setValue(60.0)
        self.fov_angle_spin.setSuffix(" °")
        self.fov_angle_spin.setToolTip(
            "Fallback cone width if the 3rd click produces a near-zero angle")
        fov_row.addWidget(self.fov_angle_spin)

        fov_row.addSpacing(10)
        fov_row.addWidget(QLabel("Default range:"))
        self.fov_range_spin = QDoubleSpinBox()
        self.fov_range_spin.setRange(1.0, 500.0)
        self.fov_range_spin.setSingleStep(5.0)
        self.fov_range_spin.setValue(25.0)
        self.fov_range_spin.setSuffix(" m")
        self.fov_range_spin.setToolTip(
            "Fallback range if the 2nd click lands on the position (zero distance)")
        fov_row.addWidget(self.fov_range_spin)
        fov_row.addStretch(1)
        fov_layout.addLayout(fov_row)

        fov_hint = QLabel(
            "Three clicks: 1) place the position, 2) click the direction it faces "
            "(distance sets the range), 3) swing the mouse out to set the view "
            "angle and click. Right-click or Esc cancels."
        )
        fov_hint.setWordWrap(True)
        fov_hint.setStyleSheet("color:#5F6F65; font-size:10px;")
        fov_layout.addWidget(fov_hint)

        self.fov_group.setVisible(False)
        pc_layout.addWidget(self.fov_group)

        # ── Shape options (Shape mode only) ───────────────────────────────
        self.shape_controls = QGroupBox("Shape options")
        sc_layout = QVBoxLayout(self.shape_controls)
        sc_layout.setSpacing(5)

        sc_row1 = QHBoxLayout()
        sc_row1.addWidget(QLabel("Shape:"))
        self.shape_combo = QComboBox()
        for _txt, _key in [
            ("Circle", "circle"), ("Square", "square"),
            ("Rectangle", "rectangle"), ("Ellipse", "ellipse"),
            ("Stadium (rounded)", "stadium"), ("Triangle", "triangle"),
            ("Regular polygon", "regular"), ("Annulus (ring)", "annulus"),
        ]:
            self.shape_combo.addItem(_txt, _key)
        self.shape_combo.currentIndexChanged.connect(self._on_shape_dims_changed)
        sc_row1.addWidget(self.shape_combo, 1)
        sc_layout.addLayout(sc_row1)

        # Up to three numeric dimension fields, relabelled per shape.
        self._shape_dim_spins = []
        self._shape_dim_lbls = []
        for _i in range(3):
            drow = QHBoxLayout()
            dl = QLabel("")
            dl.setMinimumWidth(120)
            ds = QDoubleSpinBox()
            ds.setRange(0.1, 100000.0)
            ds.setSingleStep(1.0)
            ds.setValue(10.0)
            ds.setSuffix(" m")
            drow.addWidget(dl)
            drow.addWidget(ds, 1)
            sc_layout.addLayout(drow)
            self._shape_dim_lbls.append(dl)
            self._shape_dim_spins.append(ds)

        nrow = QHBoxLayout()
        self.shape_sides_lbl = QLabel("Sides (regular):")
        nrow.addWidget(self.shape_sides_lbl)
        self.shape_sides_spin = QDoubleSpinBox()
        self.shape_sides_spin.setRange(3, 20)
        self.shape_sides_spin.setDecimals(0)
        self.shape_sides_spin.setValue(6)
        nrow.addWidget(self.shape_sides_spin, 1)
        sc_layout.addLayout(nrow)

        rrow = QHBoxLayout()
        self.shape_rot_lbl = QLabel("Rotation:")
        rrow.addWidget(self.shape_rot_lbl)
        self.shape_rot_spin = QDoubleSpinBox()
        self.shape_rot_spin.setRange(0, 359)
        self.shape_rot_spin.setDecimals(0)
        self.shape_rot_spin.setValue(0)
        self.shape_rot_spin.setSuffix(" \u00b0")
        rrow.addWidget(self.shape_rot_spin, 1)
        sc_layout.addLayout(rrow)

        sc_hint = QLabel("Pick a feature type above, set dimensions, then "
                         "click \u201cPlace shape\u201d and click the map for the centre.")
        sc_hint.setWordWrap(True)
        sc_hint.setStyleSheet("color:#5A6B5F; font-size:10px;")
        sc_layout.addWidget(sc_hint)

        self.shape_controls.setVisible(False)
        self._on_shape_dims_changed()

        # Symbol/shape settings and buffering form one compact settings row.
        # Only one of point_controls and shape_controls is visible at a time.
        self._settings_row = QWidget()
        settings_layout = QHBoxLayout(self._settings_row)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(10)
        settings_layout.addWidget(self.point_controls, 3)
        settings_layout.addWidget(self.shape_controls, 3)
        settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        placement_layout.addWidget(self._settings_row)
        layout.addWidget(self._placement_card)

        # ── Action buttons (mounted in the sticky footer, built below) ────
        # One consistent system: a filled green primary, matching bordered
        # secondaries, and toggles that fill green when on. All share height,
        # border, radius and font so the row reads as a set.
        _FOOT_H = 34
        _sec_qss = (
            "QPushButton{background:%s; border:1px solid %s; border-radius:8px;"
            " padding:0 14px; font-family:%s; font-size:12px; color:%s;}"
            % (theme.CARD, theme.BORDER_STRONG, theme.FONT_MEDIUM, theme.INK)
            + "QPushButton:hover{background:%s; border-color:%s; color:%s;}"
            % (theme.ACCENT_SOFT, theme.BORDER_STRONG, theme.ACCENT_DARK)
            + "QPushButton:disabled{color:%s; background:%s; border-color:%s;}"
            % (theme.FAINT, theme.SUNKEN, theme.BORDER)
        )
        _toggle_qss = (
            "QPushButton{background:%s; border:1px solid %s; border-radius:8px;"
            " padding:0 14px; font-family:%s; font-size:12px; color:%s;}"
            % (theme.CARD, theme.BORDER_STRONG, theme.FONT_MEDIUM, theme.MUTED)
            + "QPushButton:checked{background:%s; color:white; border-color:%s;}"
            % (theme.ACCENT, theme.ACCENT_DARK)
            + "QPushButton:hover:!checked{background:%s; color:%s;}"
            % (theme.ACCENT_SOFT, theme.ACCENT_DARK)
        )

        btns = QHBoxLayout()
        btns.setSpacing(6)
        # The primary button doubles as the armed-state indicator: placement is
        # CONTINUOUS (keep clicking the map to place more), the button flips to
        # a red "■ Stop placing" while a tool is armed, and clicking it then —
        # or Esc / right-click on the map — ends the run.
        self.pick_on_map_btn = QPushButton("Place")
        self.pick_on_map_btn.setFixedHeight(_FOOT_H)
        self.pick_on_map_btn.setFixedWidth(138)
        self.pick_on_map_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.pick_on_map_btn.setStyleSheet(theme.btn_primary())
        self.pick_on_map_btn.setToolTip(
            "Selecting a feature arms placement automatically; this button "
            "re-arms it after a stop. Placement is continuous — keep clicking "
            "the map to place feature after feature. Right-click, Esc, or this "
            "button stops.")
        self._place_btn_stop_qss = (
            "QPushButton{background:%s; color:white; border:none;"
            " border-radius:%spx; font-family:%s; font-size:13px;"
            " font-weight:600; padding:9px 14px;}"
            % (theme.BAD, theme.RADIUS, theme.FONT_SEMIBOLD)
        )

        self.freehand_chk = QPushButton("Freehand")
        self.freehand_chk.setCheckable(True)
        self.freehand_chk.setFixedHeight(_FOOT_H)
        self.freehand_chk.setIcon(
            self._two_state_icon("pencil", theme.MUTED, "#FFFFFF", 14))
        self.freehand_chk.setIconSize(QSize(14, 14))
        self.freehand_chk.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.freehand_chk.setToolTip(
            "Freehand: hold the left mouse button and drag to draw, release to finish.")
        self.freehand_chk.setStyleSheet(_toggle_qss)
        self.freehand_chk.setVisible(False)   # Area mode only
        self.freehand_chk.toggled.connect(self._on_freehand_toggled)

        # Place-as-shape: stamp a parametric shape instead of drawing. In Line
        # mode it lays down just the shape's outline as a polyline.
        self.shape_mode_chk = QPushButton("Place as shape")
        self.shape_mode_chk.setCheckable(True)
        self.shape_mode_chk.setFixedHeight(_FOOT_H)
        self.shape_mode_chk.setIcon(
            self._two_state_icon("circle-dashed", theme.MUTED, "#FFFFFF", 14))
        self.shape_mode_chk.setIconSize(QSize(14, 14))
        self.shape_mode_chk.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.shape_mode_chk.setToolTip(
            "Stamp a parametric shape (circle, rectangle, ellipse…) instead of "
            "drawing. In Line mode it places the shape's outline.")
        self.shape_mode_chk.setStyleSheet(_toggle_qss)
        self.shape_mode_chk.setVisible(False)   # Area & Line modes
        self.shape_mode_chk.toggled.connect(self._on_shape_mode_toggled)

        self.undo_btn = QPushButton("↩ Undo last")
        self.undo_btn.setMinimumHeight(_FOOT_H)
        self.undo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.undo_btn.setStyleSheet(_sec_qss)
        self.redo_btn = QPushButton("↪ Redo")
        self.redo_btn.setMinimumHeight(_FOOT_H)
        self.redo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.redo_btn.setStyleSheet(_sec_qss)
        self.redo_btn.setEnabled(False)
        self.close_btn = QPushButton("Close")
        self.close_btn.setMinimumHeight(_FOOT_H)
        self.close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_btn.setStyleSheet(_sec_qss)

        # The primary action occupies the same trailing column as Add from
        # grid ref, keeping the two compact form rows visually consistent.
        row2.addWidget(self.pick_on_map_btn)

        # Area/line placement modes retain their secondary drawing controls.
        # The sticky footer is reserved for history and closing the window.
        place_actions = QHBoxLayout()
        place_actions.setSpacing(6)
        place_actions.addWidget(self.freehand_chk)
        place_actions.addWidget(self.shape_mode_chk)
        place_actions.addStretch(1)
        placement_layout.addLayout(place_actions)

        btns.addStretch(1)
        btns.addWidget(self.undo_btn)
        btns.addWidget(self.redo_btn)
        btns.addWidget(self.close_btn)

        # ── Status / feedback label (sits in the footer, above the buttons) ─
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(
            "QLabel{color:%s; font-family:%s; font-weight:600; padding:2px 0px;}"
            % (theme.GOOD, theme.FONT_SEMIBOLD)
        )

        # ── On-the-map feature list (interactive, right-hand rail) ────────
        # Every placed feature is a row you can hover (highlights it on the
        # map), relocate, rename or delete. Lives in the right column.
        list_hdr = QHBoxLayout()
        list_hdr.setSpacing(6)
        _onmap_lbl = QLabel("On the map")
        _onmap_lbl.setStyleSheet(
            "font-family:%s; font-weight:500; color:%s;" % (theme.FONT_MEDIUM, theme.INK))
        list_hdr.addWidget(_onmap_lbl)
        self._feat_total_lbl = QLabel("")
        self._feat_total_lbl.setStyleSheet("color:%s; font-size:11px;" % theme.FAINT)
        list_hdr.addWidget(self._feat_total_lbl)
        list_hdr.addStretch(1)
        self._zoom_all_btn = QPushButton("Zoom to all")
        self._zoom_all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._zoom_all_btn.setStyleSheet(
            "QPushButton{font-size:11px; padding:3px 9px; border:1px solid %s;"
            " border-radius:7px; color:%s; background:%s;}"
            % (theme.BORDER, theme.MUTED, theme.CARD)
            + "QPushButton:hover{background:%s; color:%s;}"
            % (theme.ACCENT_SOFT, theme.ACCENT_DARK)
        )
        self._zoom_all_btn.clicked.connect(self._zoom_to_all)
        list_hdr.addWidget(self._zoom_all_btn)

        # Master labels switch: hides/shows EVERY eco-feature label on the map
        # (straight layer labels AND the curved arc refs) without touching the
        # per-feature Hide-label flags, which apply again when switched back on.
        self._labels_btn = QPushButton("All labels on")
        self._labels_btn.setCheckable(True)
        self._labels_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._labels_btn.setToolTip(
            "Show or hide all feature labels on the map. Per-feature "
            "'Hide label' choices still apply when labels are on.")
        self._labels_btn.setStyleSheet(
            "QPushButton{font-size:11px; padding:3px 9px; border:1px solid %s;"
            " border-radius:7px; color:%s; background:%s;}"
            % (theme.BORDER, theme.MUTED, theme.CARD)
            + "QPushButton:checked{background:%s; color:white; border-color:%s;}"
            % (theme.ACCENT, theme.ACCENT_DARK)
            + "QPushButton:hover:!checked{background:%s; color:%s;}"
            % (theme.ACCENT_SOFT, theme.ACCENT_DARK)
        )
        self._labels_btn.setChecked(not self._labels_hidden())
        self._labels_btn.toggled.connect(self._on_labels_toggled)

        list_controls = QHBoxLayout()
        list_controls.setSpacing(6)
        list_controls.addWidget(self._labels_btn)
        list_controls.addStretch(1)

        self._feat_list_container = QWidget()
        self._feat_list_container.setStyleSheet("background:#F7F9F7;")
        self._feat_list_layout = QVBoxLayout(self._feat_list_container)
        self._feat_list_layout.setContentsMargins(4, 4, 4, 4)
        self._feat_list_layout.setSpacing(2)
        self._feat_list_layout.addStretch(1)

        _list_scroll = QScrollArea()
        _list_scroll.setWidget(self._feat_list_container)
        _list_scroll.setWidgetResizable(True)
        _list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        _list_scroll.setStyleSheet(
            "QScrollArea{background:#F7F9F7;border:1px solid #D8E0DA;"
            "border-radius:9px;}"
        )
        _list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        _right_panel = QWidget()
        _right_v = QVBoxLayout(_right_panel)
        _right_v.setContentsMargins(0, 0, 0, 0)
        _right_v.setSpacing(5)
        _right_v.addLayout(list_hdr)
        _right_v.addLayout(list_controls)
        _right_v.addWidget(self._icon_size_widget)
        _right_v.addWidget(_list_scroll, 1)
        _right_panel.setMinimumWidth(250)
        _right_panel.setMaximumWidth(320)

        # ── Two-column body: fixed controls + independently scrolling rail ─
        # The overall left-hand UI must remain fixed. Only each swatch grid's
        # own _FlowScroll and the on-map list are scrollable.
        _body = QWidget()
        _body.setLayout(layout)

        _cols = QHBoxLayout()
        _cols.setContentsMargins(0, 0, 0, 0)
        _cols.setSpacing(10)
        _cols.addWidget(_body, 1)
        _cols.addWidget(_right_panel)

        _footer = QWidget()
        _footer_l = QVBoxLayout(_footer)
        _footer_l.setContentsMargins(0, 6, 0, 0)
        _footer_l.setSpacing(4)
        _footer_l.addWidget(self.status_lbl)
        _footer_l.addLayout(btns)

        _outer = QVBoxLayout()
        _outer.setContentsMargins(8, 8, 8, 8)
        _outer.addLayout(_cols, 1)
        _outer.addWidget(_footer)
        self.setLayout(_outer)

        # Open fully visible and tall enough for the complete three-row picker,
        # while retaining a small usable margin around the desktop work area.
        try:
            _screen = QApplication.primaryScreen().availableGeometry()
            _ideal = _body.sizeHint().height() + _footer.sizeHint().height() + 48
            self.resize(
                min(self._initial_width, _screen.width() - 24),
                max(650, min(_ideal, _screen.height() - 24)))
        except Exception:
            self.resize(self._initial_width, 870)

        # ── Signal wiring ─────────────────────────────────────────────────
        self.add_from_ref_btn.clicked.connect(self.add_from_coordinates)
        self.pick_on_map_btn.clicked.connect(self._on_pick_or_draw)
        self.undo_btn.clicked.connect(self.undo_last)
        self.redo_btn.clicked.connect(self.redo_last)
        self.close_btn.clicked.connect(self.close)
        self.size_slider.valueChanged.connect(self._on_style_changed)
        self.geometry_combo.currentIndexChanged.connect(self._on_geometry_changed)

        self._cache_dir().mkdir(parents=True, exist_ok=True)

        # Initial UI state — select first point feature and switch to Point stack
        self._on_geometry_changed()
        self._select_first_picker_item()
        self._update_colour_swatch()

        # Apply style if layers exist
        layer = self._get_existing_layer()
        if layer is not None:
            self._ensure_fields(layer)
            self._apply_style(layer)
            layer.triggerRepaint()

        self._update_undo_state()
        self._update_counts()

        # Re-insert any existing eco layers into the tree (needed on project reopen)
        self._ensure_eco_layers_in_tree()

    # ---------------- UI callbacks ----------------
    def _update_undo_state(self):
        self.undo_btn.setEnabled(len(self._undo_stack) > 0)
        if hasattr(self, "redo_btn"):
            self.redo_btn.setEnabled(len(self._redo_stack) > 0)

    def _record_placement(self, entry):
        """Push a placement onto the undo stack and clear redo — a fresh
        placement supersedes any previously undone ones."""
        self._undo_stack.append(entry)
        self._redo_stack.clear()
        self._update_undo_state()

    def _activate_pan_tool(self):
        """Switch the canvas to QGIS's Pan Map tool — the hand that grabs and
        drags the map (left-click-drag to pan; wheel still zooms). Prefer the
        real toolbar action so it's the exact same tool; fall back to a raw
        QgsMapToolPan kept referenced on self so it can't be garbage-collected
        (an unreferenced one gets collected and leaves the canvas grab-less)."""
        try:
            act = self.iface.actionPan()
            if act is not None:
                act.trigger()
                return
        except Exception:
            pass
        self._pan_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self._pan_tool)

    def _rearm_placement(self, activator):
        """After a placement, re-activate the placement tool on the next
        event-loop tick (after the just-finished tool tears down): placing is
        CONTINUOUS by default — right-click / Esc / the ■ Stop button end the
        run. The re-arm is seq-guarded: if the user switches feature type (or
        stops) before the timer fires, the old activator must NOT come back."""
        seq = self._arm_seq
        QTimer.singleShot(
            0, lambda: activator() if self._arm_seq == seq else None)

    # ── Placement-tool switching ──────────────────────────────────────────
    # One rule everywhere: selecting a feature type (tile, list row, search
    # result, geometry toggle, shape/freehand mode) while a placement tool is
    # armed fully exits that tool and immediately arms the right tool for the
    # NEW selection — no stale crosshair placing yesterday's geometry.

    def _own_placement_tools(self):
        """Every map tool this dialog can arm for placing features."""
        return tuple(t for t in (getattr(self, "_aim_tool", None),
                                 getattr(self, "_pick_tool", None),
                                 getattr(self, "_line_tool", None),
                                 getattr(self, "_shape_tool", None),
                                 getattr(self, "_poly_tool", None))
                     if t is not None)

    def _placement_tool_active(self):
        cur = self.canvas.mapTool()
        return cur is not None and any(cur is t
                                       for t in self._own_placement_tools())

    def _capture_prev_tool(self):
        """Remember the current canvas tool for restore-on-cancel — but never
        one of OUR placement tools: switching placer→placer must not let a
        later cancel resurrect the old crosshair. In that case the previously
        remembered (non-placer) tool is kept."""
        cur = self.canvas.mapTool()
        if cur is not None and any(cur is t
                                   for t in self._own_placement_tools()):
            return
        self._prev_map_tool = cur

    def _abort_active_placement(self):
        """Fully exit any armed placement tool AND cancel a pending keep-placing
        re-arm. Returns True if a tool was actually armed (so the caller knows
        the user was mid-placement and can arm the replacement)."""
        self._arm_seq += 1   # invalidate any queued _rearm_placement timer
        if not self._placement_tool_active():
            return False
        self._remove_shape_key_filter()
        try:
            self.iface.messageBar().clearWidgets()
        except Exception:
            pass
        self._activate_pan_tool()   # deactivates the tool → rubber bands clear
        return True

    def _on_type_selected(self, arm=True):
        """Called after EVERY feature-type selection. Select-to-arm: a user's
        click on a feature immediately arms the matching placement tool (no
        separate 'Pick on map' step), and if a tool was already armed it is
        fully exited first so the new selection's tool takes over.
        _on_pick_or_draw dispatches on the current geometry mode, shape toggle
        and type, so point/aim/line/shape/area all route correctly.

        ``arm=False`` marks a selection that is NOT a direct "place this now"
        gesture — programmatic auto-selection (dialog open, geometry-mode
        browse) and config toggles — which must never arm from idle (but still
        hot-swaps the tool when one is already armed)."""
        was_armed = self._abort_active_placement()
        if was_armed or arm:
            QTimer.singleShot(0, self._on_pick_or_draw)

    def _idle_place_btn_text(self):
        """Primary-button label when no placement tool is armed."""
        return "Place"

    def _schedule_place_btn_refresh(self, *_):
        """Coalesce armed-state UI updates (connected to canvas.mapToolSet).
        The continuous loop tears the tool down and re-arms it around every
        placement; the short debounce swallows that pan-tool blip so the button
        doesn't flicker Stop→idle→Stop (and the run counter isn't reset)."""
        t = getattr(self, "_place_btn_timer", None)
        if t is None:
            t = self._place_btn_timer = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(80)
            t.timeout.connect(self._refresh_place_btn)
        t.start()

    def _refresh_place_btn(self):
        """Sync the primary button with the armed state: a red '■ Stop placing'
        while a placement tool is live, the mode's normal label otherwise.
        Settling back to idle also ends the run tally."""
        if not hasattr(self, "pick_on_map_btn"):
            return
        if self._placement_tool_active():
            self.pick_on_map_btn.setText("■ Stop placing")
            self.pick_on_map_btn.setStyleSheet(self._place_btn_stop_qss)
        else:
            self._run_count = 0
            self.pick_on_map_btn.setText(self._idle_place_btn_text())
            self.pick_on_map_btn.setStyleSheet(theme.btn_primary())

    def _note_placed(self, text, continuing=True):
        """Footer status after a tool-driven commit: the ✓ message plus the
        live run tally and, when the tool remains armed, how to finish."""
        self._run_count = getattr(self, "_run_count", 0) + 1
        if continuing:
            self.status_lbl.setText(
                "%s · %d this run — right-click / Esc / ■ Stop to finish"
                % (text, self._run_count))
        else:
            self.status_lbl.setText(text)
            self._refresh_place_btn()

    def _sync_options_visibility(self):
        """Show point-only settings only when they are applicable."""
        if not hasattr(self, "point_controls"):
            return
        mode = self.geometry_combo.currentText()
        ftype = self.type_combo.currentText().strip()
        has_fov = (ftype in DIRECTIONAL_TYPES
                   and ftype not in ARROW_DIRECTIONAL_TYPES)
        self.point_controls.setVisible(mode == "Point" and has_fov)
        if hasattr(self, "_icon_size_widget"):
            self._icon_size_widget.setVisible(mode == "Point")

    def _push_global_undo(self, layer, fid, extra_pairs=None):
        """Mirror this placement onto the main panel's global undo stack
        so Ctrl+Z / the main undo button can remove Feature Mapper features."""
        if self._main_panel and hasattr(self._main_panel, "push_undo"):
            try:
                self._main_panel.push_undo(layer, fid, None, None,
                                           extra_pairs=extra_pairs)
            except TypeError:
                # Older panel build without extra_pairs support.
                self._main_panel.push_undo(layer, fid, None, None)

    def _placement_pairs(self, main_uuid, buf_uuid=None):
        """Every (layer, fid) carrying this placement's uuids — the marker,
        an FOV wedge stored under the same uuid on the polygon layer, and the
        buffer zone stored under buf_uuid on the buffer layer."""
        pairs = []
        main_layers = [self._get_existing_layer(),
                       self._get_existing_line_layer(),
                       self._get_existing_poly_layer()]
        for target_uuid, layers in ((main_uuid, main_layers),
                                    (buf_uuid,
                                     [self._get_existing_buffer_layer()])):
            if not target_uuid:
                continue
            for layer in layers:
                if layer is None:
                    continue
                req = QgsFeatureRequest().setFilterExpression(
                    f"\"uuid\" = '{target_uuid}'")
                pairs += [(layer, f.id()) for f in layer.getFeatures(req)]
        return pairs

    def _push_global_undo_uuid(self, main_uuid, buf_uuid=None):
        """Bridge one placement to the global undo as a SINGLE entry covering
        every feature it created (marker + FOV wedge + buffer zone), so a
        global Ctrl+Z removes the whole placement instead of stranding the
        companions on other layers."""
        pairs = self._placement_pairs(main_uuid, buf_uuid)
        if not pairs:
            return
        (layer0, fid0), rest = pairs[0], pairs[1:]
        self._push_global_undo(layer0, fid0, extra_pairs=rest or None)

    def _on_style_changed(self, *_):
        self._icon_size_mm = int(self.size_slider.value())
        self.size_value_lbl.setText(f"{self._icon_size_mm} mm")
        self._update_colour_swatch()   # live preview reflects the new size

        pt = self._get_existing_layer()
        if pt is not None:
            self._apply_style(pt)
            pt.triggerRepaint()

        ln = self._get_existing_line_layer()
        if ln is not None:
            self._apply_style(ln)
            ln.triggerRepaint()

        pl = self._get_existing_poly_layer()
        if pl is not None:
            self._apply_style(pl)
            pl.triggerRepaint()

        # Icon-size / show-labels change → resize & rebuild the FOV-label arcs.
        # Persist the icon size so the export can match the arc text to the canvas.
        try:
            from qgis.PyQt.QtCore import QSettings
            QSettings().setValue("biome/eco_icon_mm", int(self._icon_size_mm))
        except Exception:
            pass
        if hasattr(self, "_do_refresh_fov_arcs"):
            self._do_refresh_fov_arcs()


    def _select_first_picker_item(self):
        """Auto-select the first item in the current geometry mode's picker.
        Programmatic (from_user=False): browsing to a geometry page or opening
        the dialog must never arm a placement tool by itself — only a direct
        click on a feature does that (select-to-arm)."""
        mode = self.geometry_combo.currentText()
        if mode == "Point":
            if self._pt_icon_btns:
                first_btns = next(iter(self._pt_icon_btns.values()))
                self._on_icon_btn_clicked(first_btns[0].toolTip(),
                                          from_user=False)
        elif mode == "Line":
            ft = self._first_ftype(LINE_FEATURE_GROUPS)
            if ft:
                self._on_geom_tile_clicked("line", ft, from_user=False)
        else:   # Area / Shape (both use the area picker)
            ft = self._first_ftype(AREA_FEATURE_GROUPS)
            if ft:
                self._on_geom_tile_clicked("area", ft, from_user=False)

    def _on_icon_btn_clicked(self, fname: str, from_user: bool = True):
        """Handle a point icon button click — select that feature type, sync all
        tab instances, and (for a real user click) arm the placement tool."""
        for n, btns in self._pt_icon_btns.items():
            for b in btns:
                b.setChecked(n == fname)
        self._set_type_combo(fname)
        self.selected_type_lbl.setText(fname)
        self._update_colour_swatch(fname=fname)
        self._update_fov_controls(fname)
        self._on_type_selected(arm=from_user)

    def _update_fov_controls(self, fname: str):
        """Show the field-of-view controls only for directional point types,
        and relabel the pick button so the two-click workflow is clear.

        Arrow types are directional but have no cone, so the angle/range
        controls stay hidden for them."""
        is_dir = (fname in DIRECTIONAL_TYPES
                  and fname not in ARROW_DIRECTIONAL_TYPES)
        if hasattr(self, "fov_group"):
            self.fov_group.setVisible(is_dir)
        self._refresh_place_btn()
        self._sync_options_visibility()

    def _on_line_item_clicked(self, item):
        """Handle a line list item click."""
        if item is None:
            return
        if item.data(Qt.ItemDataRole.UserRole + 1) == "ADD_CUSTOM_LINE":
            self._add_custom_feature("line", item.data(Qt.ItemDataRole.UserRole + 2) or "__custom__")
            return
        fname = item.text()
        self._set_type_combo(fname)
        self.selected_type_lbl.setText(fname)
        self._update_colour_swatch(fname=fname)
        self._on_type_selected()

    def _on_area_item_clicked(self, item):
        """Handle an area list item click."""
        if item is None:
            return
        if item.data(Qt.ItemDataRole.UserRole + 1) == "ADD_CUSTOM_AREA":
            self._add_custom_feature("area", item.data(Qt.ItemDataRole.UserRole + 2) or "__custom__")
            return
        fname = item.text()
        self._set_type_combo(fname)
        self.selected_type_lbl.setText(fname)
        self._update_colour_swatch(fname=fname)
        self._on_type_selected()

    def _set_type_combo(self, fname: str):
        """Set the hidden type_combo to fname without triggering repopulation."""
        for i in range(self.type_combo.model().rowCount()):
            item = self.type_combo.model().item(i)
            if item and item.text().strip() == fname:
                self.type_combo.setCurrentIndex(i)
                return
        # Not in the combo (e.g. a freshly-created custom type) — append it so
        # the selection, swatch and commit path all resolve the name.
        model = self.type_combo.model()
        model.appendRow(QStandardItem("    " + fname))
        self.type_combo.setCurrentIndex(model.rowCount() - 1)

    # ── Custom (user-defined) line / area types ───────────────────────────
    def _make_add_custom_item(self, kind, group="__custom__"):
        """A styled '➕ Add custom …' row for the bottom of a line/area list.
        Tagged in UserRole+1 (sentinel) and UserRole+2 (the originating tab's
        group) so the click handler opens the create dialog and files the new
        custom under this tab."""
        txt = "➕  Add custom line…" if kind == "line" else "➕  Add custom area…"
        item = QListWidgetItem(txt)
        item.setData(Qt.ItemDataRole.UserRole + 1,
                     "ADD_CUSTOM_LINE" if kind == "line" else "ADD_CUSTOM_AREA")
        item.setData(Qt.ItemDataRole.UserRole + 2, group)
        f = item.font(); f.setItalic(True); item.setFont(f)
        item.setForeground(QColor(theme.ACCENT))
        item.setToolTip("Create your own named %s with a colour of your choice. "
                        "It is stored outside the plugin folder, so it survives "
                        "plugin updates." % kind)
        return item

    def _append_custom_list_item(self, kind, name):
        """Insert a newly-created custom type into its Custom-tab list, just
        above the trailing '➕ Add custom …' row."""
        lw = self._ln_custom_list if kind == "line" else self._ar_custom_list
        props = (LINE_FEATURES if kind == "line" else AREA_FEATURES).get(name, {})
        colour = props.get("color" if kind == "line" else "fill",
                           "#8A968D" if kind == "line" else "#44aa44")
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, colour)
        lw.insertItem(max(0, lw.count() - 1), item)   # before the add-new row

    def _add_custom_feature(self, kind, group="__custom__"):
        """Prompt for a name + colour, then save a custom line/area type to the
        profile-folder store and register it live so it can be placed at once.
        *group* is the tab it was created under (it appears in that tab)."""
        from qgis.PyQt.QtWidgets import QColorDialog, QDialogButtonBox

        store_key   = "lines" if kind == "line" else "areas"
        existing    = self._custom_store[store_key]
        builtin     = LINE_FEATURES if kind == "line" else AREA_FEATURES
        default_hex = "#1F7A4D" if kind == "line" else "#44aa44"

        dlg = QDialog(self)
        dlg.setWindowTitle("Add custom %s" % ("line" if kind == "line" else "area"))
        dlg.setMinimumWidth(320)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Name:"))
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("e.g. Site boundary" if kind == "line"
                                     else "e.g. Compensation area")
        v.addWidget(name_edit)

        chosen = {"col": QColor(default_hex)}
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Colour:"))
        swatch = QLabel(); swatch.setFixedSize(44, 22); swatch.setFrameShape(QFrame.Shape.Box)

        def _paint():
            swatch.setStyleSheet("background:%s; border:1px solid #5F6F65; "
                                 "border-radius:3px;" % chosen["col"].name())
        _paint()

        def _pick():
            c = QColorDialog.getColor(chosen["col"], dlg, "Choose colour")
            if c.isValid():
                chosen["col"] = c
                _paint()
        pick_btn = QPushButton("Choose colour…")
        pick_btn.clicked.connect(_pick)
        crow.addWidget(swatch); crow.addWidget(pick_btn); crow.addStretch(1)
        v.addLayout(crow)

        # Areas also let the user set the fill opacity (lines are always solid).
        opacity_spin = None
        if kind == "area":
            orow = QHBoxLayout()
            orow.addWidget(QLabel("Fill opacity:"))
            opacity_spin = QDoubleSpinBox()
            opacity_spin.setRange(0.05, 1.0)
            opacity_spin.setSingleStep(0.05)
            opacity_spin.setDecimals(2)
            opacity_spin.setValue(0.30)
            opacity_spin.setToolTip("0.05 = barely visible · 1.00 = fully solid")
            orow.addWidget(opacity_spin); orow.addStretch(1)
            v.addLayout(orow)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Biome", "Please enter a name for the feature.")
            return
        if name in builtin or name in existing:
            QMessageBox.warning(self, "Biome",
                                "A %s type called “%s” already exists." % (kind, name))
            return

        hex_col = chosen["col"].name()
        if kind == "line":
            props = {"color": hex_col, "width": "0.8", "style": "solid", "custom": True}
        else:
            border = chosen["col"].darker(160).name()
            op = "%.2f" % opacity_spin.value() if opacity_spin is not None else "0.30"
            props = {"fill": hex_col, "border": border,
                     "opacity": op, "style": "solid", "custom": True}
        props["group"] = group   # the tab this custom belongs to

        # Persist to the profile folder + register live so the renderer and the
        # selected-feature swatch immediately treat it like a built-in type.
        existing[name] = props
        if not save_custom_features(self._custom_store):
            QMessageBox.warning(self, "Biome",
                                "Couldn't save the custom feature to disk; it will "
                                "work for this session only.")
        register_custom_features(self._custom_store)

        # Rebuild that picker so the new tile lands in its tab, show that tab,
        # then select the new type.
        if kind == "line":
            self._build_line_tabs()
            tabs, groups_def = self._ln_tabs, LINE_FEATURE_GROUPS
        else:
            self._build_area_tabs()
            tabs, groups_def = self._ar_tabs, AREA_FEATURE_GROUPS
        tabs.setCurrentIndex(self._geom_group_tab_index(tabs, groups_def, group))
        self._on_geom_tile_clicked(kind, name)
        self.iface.messageBar().pushMessage(
            "Biome",
            "Custom %s “%s” added and armed — draw it on the map."
            % (kind, name),
            level=Qgis.MessageLevel.Info, duration=4)

    @staticmethod
    def _geom_group_tab_index(tabs, groups_def, group):
        labels = [lbl for lbl, _ in groups_def]
        return labels.index(group) if group in labels else tabs.count() - 1

    # ── Custom POINT types (with an uploaded SVG icon) ────────────────────
    # ── Theme helpers + group-aware picker builders ──────────────────────
    def _two_state_icon(self, name, off_color, on_color, size=16):
        """QIcon from plugin icons/<name>.svg with an Off (off_color) and On
        (on_color) pixmap, so a checkable button swaps icon colour with state."""
        from qgis.PyQt.QtGui import QIcon
        from qgis.PyQt.QtCore import QByteArray
        icon = QIcon()
        try:
            with open(os.path.join(os.path.dirname(__file__), "icons", name + ".svg"),
                      "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            return icon
        for col, state in ((off_color, QIcon.State.Off), (on_color, QIcon.State.On)):
            r = QSvgRenderer(QByteArray(raw.replace("currentColor", col).encode("utf-8")))
            img = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(img)
            r.render(painter, QRectF(0, 0, size * 2, size * 2))
            painter.end()
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(2)
            icon.addPixmap(pm, QIcon.Mode.Normal, state)
        return icon

    @staticmethod
    def _short_tab_label(tab_label):
        s = tab_label.split("  ", 1)[-1] if "  " in tab_label else tab_label
        return s.replace("&", "&&")   # avoid Qt mnemonic underline

    def _tabs_qss(self):
        # Chip-style tabs: each tab is a bounded rounded pill (so they're easy
        # to tell apart), the active one tinted accent. No pane frame / base
        # line (that drew the stray line behind the row).
        return (
            "QTabWidget::pane{border:none; background:transparent; top:3px;}"
            + "QTabBar{background:transparent;}"
            + "QTabBar::tab{background:%s; color:%s; border:1px solid %s;"
            " border-radius:7px; padding:5px 11px; margin:2px 4px 4px 0;"
            " font-family:%s; font-size:11px;}"
            % (theme.CARD, theme.MUTED, theme.BORDER, theme.FONT_MEDIUM)
            + "QTabBar::tab:selected{background:%s; color:%s; border-color:%s;"
            " font-weight:600;}" % (theme.ACCENT_SOFT, theme.ACCENT_DARK, theme.ACCENT)
            + "QTabBar::tab:hover:!selected{background:%s; color:%s; border-color:%s;}"
            % (theme.SUNKEN, theme.ACCENT_DARK, theme.BORDER_STRONG)
        )

    def _list_qss(self):
        return (
            "QListWidget{background:%s; border:none; font-family:%s;}"
            % (theme.CARD, theme.FONT)
            + "QListWidget::item{padding:6px 8px; border-radius:5px; color:%s;}" % theme.INK
            + "QListWidget::item:selected{background:%s; color:%s;}"
            % (theme.ACCENT_SOFT, theme.ACCENT_DARK)
            + "QListWidget::item:hover{background:%s;}" % theme.SUNKEN
        )

    def _point_tile_qss(self):
        return (
            "QToolButton{border:1px solid %s; border-radius:10px; font-size:10px;"
            " color:%s; background:%s; padding:5px; font-family:%s;}"
            % (theme.BORDER, theme.INK, theme.CARD, theme.FONT)
            + "QToolButton:checked{border:2px solid %s; background:%s;}"
            % (theme.ACCENT, theme.ACCENT_SOFT)
            + "QToolButton:hover{border-color:%s; background:%s;}"
            % (theme.BORDER_STRONG, theme.SUNKEN)
        )

    def _customs_for(self, kind, group, known_labels):
        """Yield (name, props) for customs of *kind* belonging to tab *group*.
        A custom whose stored group is missing or no longer a real tab falls
        back to the '__custom__' (Custom tab) bucket."""
        key = {"line": "lines", "area": "areas", "point": "points"}[kind]
        out = []
        for name, props in self._custom_store.get(key, {}).items():
            g = props.get("group") or "__custom__"
            if g not in known_labels:
                g = "__custom__"
            if g == group:
                out.append((name, props))
        return out

    def _add_geom_list_item(self, lw, kind, fname):
        item = QListWidgetItem(fname)
        if kind == "line":
            item.setData(Qt.ItemDataRole.UserRole, LINE_FEATURES.get(fname, {}).get("color", "#8A968D"))
        else:
            item.setData(Qt.ItemDataRole.UserRole, AREA_FEATURES.get(fname, {}).get("fill", "#44aa44"))
        lw.addItem(item)

    def _make_point_button(self, fname):
        """A selectable tile for a built-in point type (registers in _pt_icon_btns)."""
        btn = QToolButton()
        display = fname if len(fname) <= 14 else fname.replace(" / ", "/\n").replace(" (", "\n(")
        if len(display.replace("\n", "")) > 14 and "\n" not in display:
            mid = len(fname) // 2
            space = fname.rfind(" ", 0, mid + 4)
            if space > 0:
                display = fname[:space] + "\n" + fname[space + 1:]
        btn.setText(display)
        # No tooltip: the tile caption already shows the full name (wrapped, never
        # truncated), so a name-only tooltip would just repeat it.
        btn.setFixedSize(108, 100)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setCheckable(True)
        btn.setAutoExclusive(False)
        icon = self._make_point_icon(fname)
        if icon is not None:
            btn.setIcon(icon)
            btn.setIconSize(QSize(40, 40))
        btn.setStyleSheet(self._point_tile_qss())
        btn.clicked.connect(lambda checked, n=fname: self._on_icon_btn_clicked(n))
        self._pt_icon_btns.setdefault(fname, []).append(btn)
        return btn

    def _clear_tabs(self, tabs):
        """Remove and delete every tab widget (used before a rebuild)."""
        while tabs.count():
            w = tabs.widget(0)
            tabs.removeTab(0)
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _new_flow_tab(self):
        """A scroll area + flow layout for one picker tab (shared by points,
        lines and areas). Top margin gives the tiles breathing room below the
        tab row."""
        scroll = _FlowScroll(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:#F7FAF8; border:none;}")
        container = QWidget()
        container.setStyleSheet("background:#F7FAF8;")
        flow = _FlowLayout(container, spacing=8)
        flow.setContentsMargins(10, 12, 10, 10)
        scroll.setWidget(container)
        return scroll, flow

    _POINT_CATEGORY_BUCKETS = {
        "Species Records": "Records & Signs",
        "Field Signs": "Records & Signs",
        "Existing Features": "Existing",
        "Bat Survey": "Survey",
        "Survey & Design": "Survey",
        "Survey Positions": "Survey",
        "Survey Methods": "Survey",
        "Survey": "Survey",
        "Enhancements": "Enhancements",
        "Invasive Species": "Invasive",
        "Records & Signs": "Records & Signs",
        "Existing": "Existing",
        "Invasive": "Invasive",
    }
    _POINT_CATEGORY_ICONS = {
        "Records & Signs": "🐾  ",
        "Existing": "🌳  ",
        "Survey": "🔬  ",
        "Enhancements": "🏡  ",
        "Invasive": "🚫  ",
    }

    def _point_browse_groups(self):
        """Consolidate overlapping point tabs into five stable categories."""
        grouped = {}
        order = []
        for old_label, sub_groups in FEATURE_GROUPS:
            raw = self._short_tab_label(old_label).replace("&&", "&")
            bucket = self._POINT_CATEGORY_BUCKETS.get(raw, raw)
            if bucket not in grouped:
                grouped[bucket] = []
                order.append(bucket)
            for sub_label, names in sub_groups:
                target = next(
                    (row for row in grouped[bucket] if row[0] == sub_label), None)
                if target is None:
                    target = (sub_label, [])
                    grouped[bucket].append(target)
                for name in names:
                    if name not in target[1]:
                        target[1].append(name)
        return [
            (self._POINT_CATEGORY_ICONS.get(name, "") + name, grouped[name])
            for name in order
        ]

    def _point_customs_for_bucket(self, display_label):
        raw_display = self._short_tab_label(display_label).replace("&&", "&")
        rows = []
        for name, props in self._custom_store.get("points", {}).items():
            old_group = props.get("group") or "__custom__"
            old_raw = self._short_tab_label(old_group).replace("&&", "&")
            if self._POINT_CATEGORY_BUCKETS.get(old_raw, old_raw) == raw_display:
                rows.append((name, props))
        return rows

    def _build_point_tabs(self):
        """(Re)build the point picker: a tab per category with built-in tiles,
        this tab's customs, and an add tile; plus a final Custom tab. Tiles use
        a flow layout so they fill the full width and reflow on resize."""
        tabs = self._pt_tabs
        self._clear_tabs(tabs)
        tabs.setStyleSheet(self._tabs_qss())
        tabs.tabBar().setDrawBase(False)   # no stray base line behind the tabs
        tabs.tabBar().setUsesScrollButtons(False)
        self._pt_icon_btns = {}
        self._custom_point_names = set(self._custom_store.get("points", {}).keys())
        known = {lbl for lbl, _ in FEATURE_GROUPS}
        browse_groups = self._point_browse_groups()
        known.update(lbl for lbl, _ in browse_groups)

        # "All" tab first — every point feature in one grid.
        all_scroll, all_flow = self._new_flow_tab()
        for _tl, _subs in FEATURE_GROUPS:
            for _sg, items in _subs:
                for fname in items:
                    all_flow.addWidget(self._make_point_button(fname))
        for name, props in self._custom_store.get("points", {}).items():
            all_flow.addWidget(self._make_custom_point_button(name, props))
        tabs.addTab(all_scroll, "All")

        for tab_label, sub_groups in browse_groups:
            scroll, flow = self._new_flow_tab()
            for _sg, items in sub_groups:
                for fname in items:
                    flow.addWidget(self._make_point_button(fname))
            for name, props in self._point_customs_for_bucket(tab_label):
                flow.addWidget(self._make_custom_point_button(name, props))
            flow.addWidget(self._make_add_point_button("Survey" in tab_label,
                                                       tab_label))
            tabs.addTab(scroll, self._short_tab_label(tab_label))

        # Custom tab
        scroll, flow = self._new_flow_tab()
        self._pt_custom_scroll = scroll
        for name, props in self._customs_for("point", "__custom__", known):
            flow.addWidget(self._make_custom_point_button(name, props))
        flow.addWidget(self._make_add_point_button(False, "__custom__"))
        flow.addWidget(self._make_add_point_button(True, "__custom__"))
        tabs.addTab(scroll, "Custom")

    def _build_line_tabs(self):
        self._build_geom_tabs("line", self._ln_tabs, LINE_FEATURE_GROUPS)

    def _build_area_tabs(self):
        self._build_geom_tabs("area", self._ar_tabs, AREA_FEATURE_GROUPS)

    def _build_geom_tabs(self, kind, tabs, groups):
        """(Re)build a line/area picker as preview-tile grids (a tab per
        category + a Custom tab), mirroring the point picker — each tile shows
        a rendered preview of the line/area style."""
        self._clear_tabs(tabs)
        tabs.setStyleSheet(self._tabs_qss())
        tabs.tabBar().setDrawBase(False)
        for key in [k for k in self._geom_tile_btns if k[0] == kind]:
            del self._geom_tile_btns[key]
        known = {lbl for lbl, _ in groups}
        store_key = "lines" if kind == "line" else "areas"

        # "All" tab first — every feature of this geometry in one grid.
        all_scroll, all_flow = self._new_flow_tab()
        for _tl, _subs in groups:
            for _sg, items in _subs:
                for fname in items:
                    all_flow.addWidget(self._make_geom_tile(kind, fname))
        for name in self._custom_store.get(store_key, {}):
            all_flow.addWidget(self._make_geom_tile(kind, name))
        tabs.addTab(all_scroll, "All")

        for tab_label, sub_groups in groups:
            scroll, flow = self._new_flow_tab()
            for _sg, items in sub_groups:
                for fname in items:
                    flow.addWidget(self._make_geom_tile(kind, fname))
            for name, props in self._customs_for(kind, tab_label, known):
                flow.addWidget(self._make_geom_tile(kind, name))
            flow.addWidget(self._make_add_geom_tile(kind, tab_label))
            tabs.addTab(scroll, self._short_tab_label(tab_label))

        scroll, flow = self._new_flow_tab()
        for name, props in self._customs_for(kind, "__custom__", known):
            flow.addWidget(self._make_geom_tile(kind, name))
        flow.addWidget(self._make_add_geom_tile(kind, "__custom__"))
        tabs.addTab(scroll, "Custom")

    @staticmethod
    def _tile_display(fname):
        """Wrap a feature name to up to two lines for a tile caption."""
        if len(fname) <= 14:
            return fname
        d = fname.replace(" / ", "/\n").replace(" (", "\n(")
        if len(d.replace("\n", "")) > 14 and "\n" not in d:
            mid = len(fname) // 2
            sp = fname.rfind(" ", 0, mid + 4)
            if sp > 0:
                d = fname[:sp] + "\n" + fname[sp + 1:]
        return d

    @staticmethod
    def _first_ftype(groups):
        for _lbl, subs in groups:
            for _sg, items in subs:
                for it in items:
                    return it
        return None

    _PEN_STYLES = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine,
                   "dot": Qt.PenStyle.DotLine, "dash dot": Qt.PenStyle.DashDotLine}

    def _line_preview_icon(self, props, size=40):
        """A small diagonal stroke in the line's colour/width/style."""
        from qgis.PyQt.QtGui import QIcon, QPen
        img = QImage(size, size, QImage.Format.Format_ARGB32); img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(props.get("color", "#8A968D")))
        pen.setWidthF(3.2)
        pen.setStyle(self._PEN_STYLES.get(props.get("style", "solid"), Qt.PenStyle.SolidLine))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(7, int(size * 0.64), size - 7, int(size * 0.36))
        p.end()
        return QIcon(QPixmap.fromImage(img))

    def _area_preview_icon(self, props, size=40):
        """A filled rounded rect at the area's fill colour/opacity + border."""
        from qgis.PyQt.QtGui import QIcon, QPen, QBrush
        img = QImage(size, size, QImage.Format.Format_ARGB32); img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(props.get("fill", "#44aa44"))
        try:
            fill.setAlphaF(float(props.get("opacity", "0.30")))
        except (TypeError, ValueError):
            fill.setAlphaF(0.30)
        pen = QPen(QColor(props.get("border", "#228822")))
        pen.setWidthF(1.6)
        pen.setStyle(self._PEN_STYLES.get(props.get("style", "solid"), Qt.PenStyle.SolidLine))
        p.setPen(pen); p.setBrush(QBrush(fill))
        p.drawRoundedRect(QRectF(7, 9, size - 14, size - 18), 5, 5)
        p.end()
        return QIcon(QPixmap.fromImage(img))

    def _add_tile_qss(self):
        return (
            "QToolButton{border:1px dashed %s; border-radius:8px; font-size:9px;"
            " color:%s; background:%s; padding:2px; font-family:%s;}"
            % (theme.ACCENT, theme.ACCENT_DARK, theme.ACCENT_SOFT, theme.FONT)
            + "QToolButton:hover{background:#DCEBE0;}"
        )

    def _add_custom_delete_badge(self, tile, kind, name):
        """Add an iOS-style delete badge to a custom picker tile."""
        badge = QPushButton("×", tile)
        badge.setObjectName("customDeleteBadge")
        badge.setFixedSize(22, 22)
        badge.move(tile.width() - 25, 3)
        badge.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        badge.setToolTip("Delete this custom feature")
        badge.setStyleSheet(
            "QPushButton#customDeleteBadge{background:#E5484D;color:white;"
            "border:2px solid white;border-radius:11px;font-size:16px;"
            "font-weight:700;padding:0px;}"
            "QPushButton#customDeleteBadge:hover{background:#C92F35;}"
            "QPushButton#customDeleteBadge:pressed{background:#A92228;}"
        )
        badge.clicked.connect(
            lambda _checked=False, k=kind, n=name:
            self._delete_custom_feature(k, n))
        badge.raise_()
        return badge

    def _make_geom_tile(self, kind, fname):
        """A selectable preview tile for a line/area type (right-click a custom
        to delete). Registers in _geom_tile_btns for selection highlighting."""
        props = (LINE_FEATURES if kind == "line" else AREA_FEATURES).get(fname, {})
        is_custom = fname in self._custom_store.get(
            "lines" if kind == "line" else "areas", {})
        btn = QToolButton()
        btn.setText(self._tile_display(fname))
        # Caption already shows the full name; only add a tooltip for the custom
        # delete hint (extra info that isn't on the tile).
        if is_custom:
            btn.setToolTip("Right-click to delete")
        btn.setFixedSize(108, 100)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setCheckable(True); btn.setAutoExclusive(False)
        icon = (self._line_preview_icon(props) if kind == "line"
                else self._area_preview_icon(props))
        btn.setIcon(icon); btn.setIconSize(QSize(40, 40))
        btn.setStyleSheet(self._point_tile_qss())
        btn.clicked.connect(lambda _c, k=kind, n=fname: self._on_geom_tile_clicked(k, n))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda _p, k=kind, n=fname: self._geom_tile_context_menu(k, n))
        if is_custom:
            self._add_custom_delete_badge(btn, kind, fname)
        self._geom_tile_btns.setdefault((kind, fname), []).append(btn)
        return btn

    def _make_add_geom_tile(self, kind, group="__custom__"):
        btn = QToolButton()
        btn.setText("➕ Add custom\n%s" % ("line" if kind == "line" else "area"))
        btn.setToolTip("Create your own named %s with a colour%s. Stored outside "
                       "the plugin folder, so it survives updates."
                       % (kind, " and opacity" if kind == "area" else ""))
        btn.setFixedSize(108, 100)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(self._add_tile_qss())
        btn.clicked.connect(lambda _c, k=kind, g=group: self._add_custom_feature(k, g))
        return btn

    def _on_geom_tile_clicked(self, kind, fname, from_user: bool = True):
        for (k, n), btns in self._geom_tile_btns.items():
            sel = (k == kind and n == fname)
            for b in btns:
                b.setChecked(sel)
        self._set_type_combo(fname)
        self.selected_type_lbl.setText(fname)
        self._update_colour_swatch(fname=fname)
        self._on_type_selected(arm=from_user)

    def _geom_tile_context_menu(self, kind, fname):
        from qgis.PyQt.QtWidgets import QMenu
        store_key = "lines" if kind == "line" else "areas"
        if fname not in self._custom_store.get(store_key, {}):
            return
        m = QMenu(self)
        act = m.addAction("Delete “%s”" % fname)
        if m.exec(QCursor.pos()) is act:
            self._delete_custom_feature(kind, fname)

    def _make_point_icon(self, fname, size=40):
        """QIcon for a point feature: rendered from its SVG (built-in filename or
        a custom absolute path) when present, otherwise from the emoji map so
        emoji-only built-ins still get a picture on their picker tile. Returns
        None only when neither an SVG nor an emoji is available."""
        from qgis.PyQt.QtGui import QIcon
        path = self._symbol_svg_path(fname) if _HAS_SVG else None
        if path:
            renderer = QSvgRenderer(path)
            img = QImage(size, size, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(img)
            renderer.render(painter, QRectF(0, 0, size, size))
            painter.end()
            return QIcon(QPixmap.fromImage(img))
        emoji = FEATURE_EMOJI.get(fname)
        if emoji:
            return QIcon(self._render_emoji_to_png(fname, emoji))
        return None

    def _make_add_point_button(self, directional=False, group="__custom__"):
        """A dashed '➕ Add custom …' tile for the bottom of a point tab."""
        btn = QToolButton()
        btn.setText("➕ Add custom\nsurvey position" if directional
                    else "➕ Add custom\npoint")
        btn.setToolTip("Create your own point feature. "
                       + ("You choose an SVG icon and the field-of-view colour."
                          if directional else "You choose an SVG icon.")
                       + " Stored outside the plugin folder, so it survives updates.")
        btn.setFixedSize(108, 100)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(self._add_tile_qss())
        btn.clicked.connect(
            lambda _checked, d=directional, g=group: self._add_custom_point(d, g))
        return btn

    def _make_custom_point_button(self, name, props):
        """A selectable tile for a saved custom point (right-click to delete).
        Registers itself in _pt_icon_btns so selection highlighting works."""
        btn = QToolButton()
        truncated = len(name) > 14
        btn.setText(name if not truncated else name[:13] + "…")
        # Only carry info the caption doesn't already show: the full name when it
        # had to be truncated, the FOV note, and the delete hint.
        tip = []
        if truncated:
            tip.append(name)
        if props.get("directional"):
            tip.append("survey position with FOV")
        tip.append("right-click to delete")
        btn.setToolTip("  ·  ".join(tip))
        btn.setFixedSize(108, 100)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setCheckable(True)
        btn.setAutoExclusive(False)
        icon = self._make_point_icon(name)
        if icon is not None:
            btn.setIcon(icon)
            btn.setIconSize(QSize(40, 40))
        btn.setStyleSheet(self._point_tile_qss())
        btn.clicked.connect(lambda _checked, n=name: self._on_icon_btn_clicked(n))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda _pos, n=name: self._custom_point_context_menu(n))
        self._add_custom_delete_badge(btn, "point", name)
        self._pt_icon_btns.setdefault(name, []).append(btn)
        return btn

    def _add_custom_point(self, directional=False, group="__custom__"):
        """Prompt for a name + SVG icon (+ FOV colour for survey positions), copy
        the icon into the profile folder, persist it, and register it live.
        *group* is the tab it was created under (it appears in that tab)."""
        from qgis.PyQt.QtWidgets import QColorDialog, QDialogButtonBox, QFileDialog
        import shutil

        existing = self._custom_store["points"]
        dlg = QDialog(self)
        dlg.setWindowTitle("Add custom survey position" if directional
                           else "Add custom point")
        dlg.setMinimumWidth(380)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Name:"))
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("e.g. Thermal camera" if directional
                                     else "e.g. Bat trap")
        v.addWidget(name_edit)

        v.addWidget(QLabel("Icon (SVG):"))
        chosen = {"svg": None}
        srow = QHBoxLayout()
        preview = QLabel(); preview.setFixedSize(40, 40); preview.setFrameShape(QFrame.Shape.Box)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        path_lbl = QLabel("No file chosen")
        path_lbl.setStyleSheet("color:#5F6F65; font-size:10px;")

        def _choose_svg():
            fn, _ = QFileDialog.getOpenFileName(dlg, "Choose an SVG icon", "",
                                                "SVG files (*.svg)")
            if fn:
                chosen["svg"] = fn
                path_lbl.setText(os.path.basename(fn))
                if _HAS_SVG:
                    r = QSvgRenderer(fn)
                    img = QImage(40, 40, QImage.Format.Format_ARGB32); img.fill(Qt.GlobalColor.transparent)
                    p = QPainter(img); r.render(p, QRectF(0, 0, 40, 40)); p.end()
                    preview.setPixmap(QPixmap.fromImage(img))
        svg_btn = QPushButton("Choose SVG…")
        svg_btn.clicked.connect(_choose_svg)
        srow.addWidget(preview); srow.addWidget(svg_btn); srow.addWidget(path_lbl, 1)
        v.addLayout(srow)

        fov = {"col": QColor("#2a6fd0")}
        if directional:
            frow = QHBoxLayout()
            frow.addWidget(QLabel("FOV colour:"))
            fov_sw = QLabel(); fov_sw.setFixedSize(44, 22); fov_sw.setFrameShape(QFrame.Shape.Box)

            def _paint_fov():
                fov_sw.setStyleSheet("background:%s; border:1px solid #5F6F65; "
                                     "border-radius:3px;" % fov["col"].name())
            _paint_fov()

            def _pick_fov():
                c = QColorDialog.getColor(fov["col"], dlg, "Choose field-of-view colour")
                if c.isValid():
                    fov["col"] = c; _paint_fov()
            fov_btn = QPushButton("Choose colour…")
            fov_btn.clicked.connect(_pick_fov)
            frow.addWidget(fov_sw); frow.addWidget(fov_btn); frow.addStretch(1)
            v.addLayout(frow)
            hint = QLabel("The view cone is drawn see-through, at the same opacity "
                          "as the built-in survey field-of-view wedges.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#5F6F65; font-size:10px;")
            v.addWidget(hint)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Biome", "Please enter a name for the feature.")
            return
        if name in FEATURE_SVG or name in existing:
            QMessageBox.warning(self, "Biome",
                                "A point type called “%s” already exists." % name)
            return
        if not chosen["svg"] or not os.path.exists(chosen["svg"]):
            QMessageBox.information(self, "Biome", "Please choose an SVG icon file.")
            return

        # Copy the chosen SVG into the profile's custom-icons folder so it
        # persists (and survives plugin updates) independently of the original.
        slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "icon"
        dest = os.path.join(custom_icons_dir(), slug + ".svg")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(custom_icons_dir(), "%s_%d.svg" % (slug, n)); n += 1
        try:
            shutil.copyfile(chosen["svg"], dest)
        except Exception as e:
            QMessageBox.warning(self, "Biome", "Couldn't copy the SVG icon: %s" % e)
            return

        props = {"svg": dest, "custom": True, "group": group}
        if directional:
            props["directional"] = True
            props["fov_color"] = fov["col"].name()
        existing[name] = props
        if not save_custom_features(self._custom_store):
            QMessageBox.warning(self, "Biome",
                                "Couldn't save the custom feature to disk; it will "
                                "work for this session only.")
        register_custom_features(self._custom_store)

        # Rebuild the point picker so the new type lands in its tab, show that
        # tab, then select it.
        self._build_point_tabs()
        self._pt_tabs.setCurrentIndex(self._point_group_tab_index(group))
        self._on_icon_btn_clicked(name)
        self.iface.messageBar().pushMessage(
            "Biome",
            ("Custom survey position “%s” added — click 'Place && aim on map' to place it."
             if directional else
             "Custom point “%s” added — click 'Pick on map' to place it.") % name,
            level=Qgis.MessageLevel.Info, duration=4)

    @staticmethod
    def _point_group_tab_index_for(group):
        labels = [lbl for lbl, _ in FEATURE_GROUPS]
        return labels.index(group) if group in labels else None

    def _point_group_tab_index(self, group):
        idx = self._point_group_tab_index_for(group)
        return idx if idx is not None else self._pt_tabs.count() - 1

    # ── Delete custom (never touches the built-in types) ──────────────────
    def _custom_point_context_menu(self, name):
        from qgis.PyQt.QtWidgets import QMenu
        if name not in self._custom_store.get("points", {}):
            return
        m = QMenu(self)
        act = m.addAction("Delete “%s”" % name)
        if m.exec(QCursor.pos()) is act:
            self._delete_custom_feature("point", name)

    def _custom_list_context_menu(self, lw, kind, pos):
        from qgis.PyQt.QtWidgets import QMenu
        item = lw.itemAt(pos)
        if item is None:
            return
        name = item.text()
        store_key = "lines" if kind == "line" else "areas"
        if name not in self._custom_store.get(store_key, {}):
            return   # the add-new row, or a built-in — not deletable
        m = QMenu(self)
        act = m.addAction("Delete “%s”" % name)
        if m.exec(lw.viewport().mapToGlobal(pos)) is act:
            self._delete_custom_feature(kind, name)

    def _remove_custom_list_item(self, lw, name):
        for i in range(lw.count()):
            if lw.item(i).text() == name:
                lw.takeItem(i)
                return

    def _delete_custom_feature(self, kind, name):
        """Delete a user custom (line/area/point). Built-ins are never in the
        store, so this can only ever remove a custom."""
        store_key = {"line": "lines", "area": "areas", "point": "points"}[kind]
        if name not in self._custom_store.get(store_key, {}):
            return
        if QMessageBox.question(
                self, "Delete custom feature",
                "Delete the custom %s “%s”?\n\nFeatures already placed on the map "
                "stay, but lose their custom styling." % (kind, name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        props = self._custom_store[store_key].pop(name, None)
        save_custom_features(self._custom_store)
        unregister_custom_feature(kind, name)
        if kind == "point" and props and props.get("svg"):
            try:
                if (os.path.dirname(props["svg"]) == custom_icons_dir()
                        and os.path.exists(props["svg"])):
                    os.remove(props["svg"])
            except Exception:
                pass
        if kind == "point":
            self._build_point_tabs()
        elif kind == "line":
            self._build_line_tabs()
        else:
            self._build_area_tabs()
        # The deleted type was selected — move the selection to a safe default
        # (this also disables the Delete button via _update_colour_swatch).
        self._select_first_picker_item()
        self.iface.messageBar().pushMessage(
            "Biome", "Custom %s “%s” deleted." % (kind, name), level=Qgis.MessageLevel.Info, duration=3)

    def _selected_custom_kind(self, name=None):
        """Return 'line'/'area'/'point' if *name* (or the current selection) is a
        user custom, else None — used to gate the Delete button."""
        if name is None:
            name = self.type_combo.currentText().strip()
        if name in self._custom_store.get("lines", {}):
            return "line"
        if name in self._custom_store.get("areas", {}):
            return "area"
        if name in self._custom_store.get("points", {}):
            return "point"
        return None

    def _update_delete_btn_state(self, name=None):
        if not hasattr(self, "_delete_custom_btn"):
            return
        self._delete_custom_btn.setEnabled(self._selected_custom_kind(name) is not None)

    def _on_delete_custom_clicked(self):
        kind = self._selected_custom_kind()
        if kind is not None:
            self._delete_custom_feature(kind, self.type_combo.currentText().strip())

    def _update_colour_swatch(self, *_, fname: str = None):
        """Update the swatch: SVG icon for Point, colour block for Line/Area."""
        if fname is None:
            fname = self.type_combo.currentText().strip()
        mode = self.geometry_combo.currentText()
        # Refresh the Delete-custom button here — this runs on every selection
        # change, and several branches below return early.
        self._update_delete_btn_state(fname)

        if mode == "Point" and _HAS_SVG:
            svg_path = self._symbol_svg_path(fname)
            if svg_path:
                renderer = QSvgRenderer(svg_path)
                box = 64
                # Scale the preview with the chosen icon size so the slider
                # gives live feedback (clamped to fit the swatch).
                draw = max(34, min(58, int(round(30 + self._icon_size_mm * 2.0))))
                off = (box - draw) / 2.0
                img = QImage(box, box, QImage.Format.Format_ARGB32)
                img.fill(Qt.GlobalColor.transparent)
                painter = QPainter(img)
                renderer.render(painter, QRectF(off, off, draw, draw))
                painter.end()
                pix = QPixmap.fromImage(img)
                self.colour_swatch.setPixmap(pix)
                self.colour_swatch.setStyleSheet(
                    "background-color:#FFFFFF; border:1px solid #C9DDD1; border-radius:8px;"
                )
                return
            colour = QColor("#1F7A4D")
        elif mode == "Line":
            props  = LINE_FEATURES.get(fname)
            colour = QColor(props.get("color", "#8A968D")) if props else QColor("#8A968D")
        else:
            props  = AREA_FEATURES.get(fname)
            colour = QColor(props.get("fill", "#44aa44")) if props else QColor("#44aa44")

        self.colour_swatch.setPixmap(QPixmap())
        self.colour_swatch.setStyleSheet(
            f"background-color:{colour.name()}; border:1px solid #AFC7B8; border-radius:8px;"
        )

    def _record_recent(self, ftype: str):
        """Add ftype to the recent list (max 5, no duplicates) and refresh buttons."""
        if ftype in self._recent_features:
            self._recent_features.remove(ftype)
        self._recent_features.insert(0, ftype)
        self._recent_features = self._recent_features[:5]
        self._refresh_recent_btns()

    def _refresh_recent_btns(self):
        if hasattr(self, "_recent_widget"):
            self._recent_widget.setVisible(bool(self._recent_features))
        for i, btn in enumerate(self._recent_btns):
            if i < len(self._recent_features):
                ft = self._recent_features[i]
                btn.setText(ft)
                btn.setVisible(True)
                # Reconnect without accumulating connections
                try:
                    btn.clicked.disconnect()
                except Exception:
                    pass
                btn.clicked.connect(lambda _checked, f=ft: self._select_recent(f))
            else:
                btn.setVisible(False)

    def _select_recent(self, ftype: str):
        """Switch to the geometry mode that owns ftype and select it in the picker."""
        # Build flat name sets from the new nested structure
        pt_names   = {n for _, subs in FEATURE_GROUPS_FLAT     for n in subs} | set(self._custom_store["points"])
        line_names = {n for _, subs in LINE_FEATURE_GROUPS_FLAT  for n in subs} | set(self._custom_store["lines"])
        area_names = {n for _, subs in AREA_FEATURE_GROUPS_FLAT  for n in subs} | set(self._custom_store["areas"])

        if ftype in pt_names:
            self.geometry_combo.setCurrentText("Point")
            self._on_icon_btn_clicked(ftype)
        elif ftype in line_names:
            self.geometry_combo.setCurrentText("Line")   # switches the picker page
            self._on_geom_tile_clicked("line", ftype)
        elif ftype in area_names:
            self.geometry_combo.setCurrentText("Area")
            self._on_geom_tile_clicked("area", ftype)


    def _on_geom_btn_clicked(self, mode: str):
        """Handle geometry toggle button — deselect others, sync hidden combo, trigger changed."""
        for m, b in self._geom_btns.items():
            b.setChecked(m == mode)
        self.geometry_combo.setCurrentText(mode)
        self._on_geometry_changed()

    def _apply_mode_controls(self, mode):
        """Configure the per-geometry controls — grid-ref / point / shape /
        freehand visibility, instruction text, pick-button label and the
        geometry-toggle highlight. Does NOT switch the picker page or the
        current selection, so search-result selection can reuse it."""
        if hasattr(self, "_geom_btns"):
            for m, b in self._geom_btns.items():
                b.setChecked(m == mode)
        # The auto-buffer 'Side' selector only makes sense for lines.
        as_shape = self.shape_mode_chk.isChecked()
        if mode == "Point":
            self.gridref_widget.setVisible(True)
            self._sync_options_visibility()
            self.shape_mode_chk.setVisible(False)
            self.shape_controls.setVisible(False)
            self.freehand_chk.setVisible(False)
            self.instruction_lbl.setText(
                "Click a feature type — the placement tool arms itself. Click "
                "the map to place each one; Esc, right-click, or ■ Stop "
                "finishes. Or enter a grid reference or coordinates and click "
                "'Add coordinate'."
            )
        elif mode == "Line":
            self.gridref_widget.setVisible(False)
            self.point_controls.setVisible(False)
            self._icon_size_widget.setVisible(False)
            self.shape_mode_chk.setVisible(True)
            self.shape_controls.setVisible(as_shape)
            self.freehand_chk.setVisible(False)
            self.instruction_lbl.setText(
                "Click a feature type, set the shape and dimensions, then click "
                "the map — it lays down the shape's outline."
                if as_shape else
                "Click a feature type — the draw tool arms itself. "
                "Left-click to add vertices — Right-click to finish — Esc to cancel."
            )
        else:  # Area
            self.gridref_widget.setVisible(False)
            self.point_controls.setVisible(False)
            self._icon_size_widget.setVisible(False)
            self.shape_mode_chk.setVisible(True)
            self.shape_controls.setVisible(as_shape)
            self.freehand_chk.setVisible(not as_shape)
            self.instruction_lbl.setText(
                "Click a feature type, choose a shape and its dimensions, then "
                "click the map to set the centre."
                if as_shape else
                "Click a feature type — the draw tool arms itself. "
                "Left-click to add vertices — Right-click to close polygon — Esc to cancel. "
                "Or enable Freehand: hold and drag to draw, release to finish."
            )
        self._refresh_place_btn()

    def _on_shape_mode_toggled(self, checked):
        """The 'Place as shape' toggle: in Area or Line mode it swaps free-drawing
        for parametric shape stamping and reveals the shape options."""
        mode = self.geometry_combo.currentText()
        if mode not in ("Area", "Line"):
            return
        self.shape_controls.setVisible(checked)
        if mode == "Area":
            self.freehand_chk.setVisible(not checked)
            if checked:
                self.freehand_chk.setChecked(False)
        if checked:
            self._on_shape_dims_changed()
        self._apply_mode_controls(mode)
        # Mid-placement mode swap (draw ↔ stamp): exit the armed tool and arm
        # the one matching the new mode. Config toggles never arm from idle.
        self._on_type_selected(arm=False)

    def _on_freehand_toggled(self, _checked):
        """Freehand toggled while the area tool is armed → swap capture ↔
        freehand immediately (the choice is otherwise only read at arm time).
        Never arms from idle — it's configuration, not a place-now gesture."""
        self._on_type_selected(arm=False)

    def _on_geometry_changed(self, *_):
        # A deliberate geometry toggle means "browse this geometry": clear any
        # active search and show that geometry's category page.
        mode = self.geometry_combo.currentText()
        self._apply_mode_controls(mode)
        if hasattr(self, "search_edit"):
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        self._picker_stack.setCurrentIndex(self._PICKER_PAGE.get(mode, 0))
        self._select_first_picker_item()
        self._update_colour_swatch()
        self._sync_options_visibility()

    _KIND_LABEL = {"point": "Point", "line": "Line", "area": "Area"}

    # Crosswalk from each geometry's category label to a unified chip category,
    # so "Enhancements" (points), "Enhancement" (areas) and "Built" (lines) all
    # land under one "Enhancement" chip, etc.
    _CATEGORY_MAP = {
        "Species Records": "Species",
        "Field Signs": "Field signs",
        "Existing Features": "Existing",
        "Bat Survey": "Bat",
        "Enhancements": "Enhancement",
        "Survey & Design": "Survey",
        "Survey Positions": "Survey",
        "Exclusion": "Exclusion",
        "Movement": "Movement",
        "Habitat": "Habitat",
        "Built": "Enhancement",
        "Territories": "Territories",
        "Survey": "Survey",
        "Enhancement": "Enhancement",
        "Water": "Water",
    }
    _CATEGORY_ORDER = ["Species", "Field signs", "Existing", "Bat", "Survey",
                       "Exclusion", "Movement", "Habitat", "Territories",
                       "Enhancement", "Water"]

    def _all_feature_entries(self):
        """Flat list of (kind, fname, category) for every feature type across
        all three geometries — built-ins then customs. The search/chip index."""
        out, seen = [], set()

        def _add(kind, fname, category):
            key = (kind, fname)
            if key not in seen:
                seen.add(key)
                out.append((kind, fname, category))

        for groups, kind in ((FEATURE_GROUPS, "point"),
                             (LINE_FEATURE_GROUPS, "line"),
                             (AREA_FEATURE_GROUPS, "area")):
            for tab_label, subs in groups:
                raw = tab_label.split("  ", 1)[-1]
                cat = self._CATEGORY_MAP.get(raw, raw)
                for _sg, items in subs:
                    for it in items:
                        _add(kind, it, cat)
        for store_key, kind in (("points", "point"), ("lines", "line"), ("areas", "area")):
            for name in self._custom_store.get(store_key, {}):
                _add(kind, name, "Custom")
        return out

    def _badged_icon(self, kind, fname):
        """The feature's preview icon with a small P/L/A geometry badge in the
        corner, so geometry reads at a glance in the unified grid."""
        from qgis.PyQt.QtGui import QIcon, QBrush, QPen, QFont
        if kind == "point":
            base = self._make_point_icon(fname)
        elif kind == "line":
            base = self._line_preview_icon(LINE_FEATURES.get(fname, {}))
        else:
            base = self._area_preview_icon(AREA_FEATURES.get(fname, {}))
        c = 44
        img = QImage(c, c, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if base is not None:
            p.drawPixmap(2, 0, base.pixmap(40, 40))
        letter = {"point": "P", "line": "L", "area": "A"}[kind]
        bw, bh = 15, 13
        bx, by = c - bw - 1, c - bh - 1
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(theme.ACCENT)))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 3, 3)
        p.setPen(QPen(QColor("#FFFFFF")))
        f = QFont(); f.setPixelSize(9); f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, letter)
        p.end()
        return QIcon(QPixmap.fromImage(img))

    def _make_result_tile(self, kind, fname):
        """A selectable tile for the unified grid (search + category chips).
        Shows the per-geometry preview with a geometry badge; clicking it
        infers the geometry."""
        btn = QToolButton()
        btn.setText(self._tile_display(fname))
        btn.setToolTip("%s  ·  %s feature" % (fname, self._KIND_LABEL.get(kind, kind)))
        btn.setFixedSize(108, 100)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setIcon(self._badged_icon(kind, fname))
        btn.setIconSize(QSize(44, 44))
        btn.setStyleSheet(self._point_tile_qss())
        btn.clicked.connect(lambda _c, k=kind, n=fname: self._select_unified(k, n))
        store_key = {"point": "points", "line": "lines", "area": "areas"}[kind]
        if fname in self._custom_store.get(store_key, {}):
            self._add_custom_delete_badge(btn, kind, fname)
        return btn

    def _refresh_unified_grid(self):
        """Rebuild the unified grid from the active category chip + search box."""
        if not hasattr(self, "_results_flow"):
            return
        while self._results_flow.count():
            item = self._results_flow.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        q = self.search_edit.text().strip().lower() if hasattr(self, "search_edit") else ""
        cat = getattr(self, "_active_category", None)
        CAP, n = 120, 0
        for kind, fname, fcat in self._all_feature_entries():
            if cat and cat != "All" and fcat != cat:
                continue
            if q and q not in fname.lower():
                continue
            self._results_flow.addWidget(self._make_result_tile(kind, fname))
            n += 1
            if n >= CAP:
                break

    def _on_search_changed(self, text):
        """Filter the catalogue by name into the unified results grid. An empty
        query returns to the current geometry's per-category browse tabs."""
        if not hasattr(self, "_results_flow"):
            return
        q = (text or "").strip()
        if not q:
            mode = self.geometry_combo.currentText()
            self._picker_stack.setCurrentIndex(self._PICKER_PAGE.get(mode, 0))
            return
        self._refresh_unified_grid()
        self._picker_stack.setCurrentIndex(3)

    def _select_unified(self, kind, fname):
        """Select a feature from the search results: infer the geometry mode,
        configure its controls and select the type — without disturbing the
        results grid, so the user can keep picking."""
        mode = self._KIND_LABEL.get(kind, "Point")
        if hasattr(self, "geometry_combo"):
            self.geometry_combo.blockSignals(True)
            self.geometry_combo.setCurrentText(mode)
            self.geometry_combo.blockSignals(False)
        self._apply_mode_controls(mode)
        if kind == "point":
            self._on_icon_btn_clicked(fname)
        else:
            self._on_geom_tile_clicked(kind, fname)
        self._sync_options_visibility()

    def _on_pick_or_draw(self):
        # While a tool is armed the primary button reads "■ Stop placing" —
        # clicking it then ends the run instead of re-arming.
        if self._placement_tool_active():
            self._abort_active_placement()
            self.status_lbl.setText("Placing stopped")
            return
        mode = self.geometry_combo.currentText()
        as_shape = self.shape_mode_chk.isChecked()
        if mode == "Point":
            ftype = self.type_combo.currentText().strip()
            if ftype in DIRECTIONAL_TYPES:
                self.activate_aim_tool()
            else:
                self.activate_pick_tool()
        elif mode == "Line":
            self.activate_shape_tool() if as_shape else self.activate_line_tool()
        else:  # Area
            self.activate_shape_tool() if as_shape else self.activate_polygon_tool()

    def activate_aim_tool(self):
        ftype = self.type_combo.currentText().strip()
        arrow = ftype in ARROW_DIRECTIONAL_TYPES
        if arrow:
            colour = QColor(ARROW_AIM_COLOUR)
        else:
            props = AREA_FEATURES.get(FOV_WEDGE_FTYPE.get(ftype, ""), {})
            colour = QColor(props.get("border", "#2a6fd0"))
        self._capture_prev_tool()
        self._aim_tool = _CaptureAimTool(
            self.canvas,
            on_finished_cb=self._finished_aim,
            on_cancel_cb=self._cancel_aim,
            fov_deg_getter=lambda: float(self.fov_angle_spin.value()),
            range_getter=lambda: float(self.fov_range_spin.value()),
            colour=colour,
            two_click=arrow,
        )
        self.canvas.setMapTool(self._aim_tool)
        if arrow:
            hint = ("1) click the position  2) click the direction it faces  "
                    "— right-click/Esc to cancel")
        else:
            hint = ("1) click the position  2) click the direction & range  "
                    "3) swing the mouse to set the view angle and click  "
                    "— right-click/Esc to cancel")
        self.iface.messageBar().pushMessage(
            "Biome", f"{ftype}: {hint}",
            level=Qgis.MessageLevel.Info, duration=0,
        )

    def _cancel_aim(self):
        self.iface.messageBar().clearWidgets()
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)
        else:
            self._activate_pan_tool()

    def _finished_aim(self, origin_canvas, aim_canvas, fov_deg):
        """Create the directional point marker plus its field-of-view wedge.

        ``aim_canvas`` is the 2nd click (bearing + range); ``fov_deg`` is the
        cone width chosen by the 3rd click. For arrow types (which finish on the
        2nd click, so ``fov_deg`` arrives as None) a single point feature is
        written with the compass bearing stored on it and no wedge at all."""
        self.iface.messageBar().clearWidgets()
        self._activate_pan_tool()
        if not self._require_saved_project():
            return

        ftype = self.type_combo.currentText().strip()
        tr = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(), LAYER_CRS, QgsProject.instance()
        )
        origin = tr.transform(origin_canvas)
        aim    = tr.transform(aim_canvas)

        dx, dy = aim.x() - origin.x(), aim.y() - origin.y()
        rng = math.hypot(dx, dy)
        if rng < 0.5:
            # 2nd click on top of the 1st — fall back to the spin-box range, facing north.
            rng = float(self.fov_range_spin.value())
            bearing_rad = math.pi / 2.0  # north (positive Y)
        else:
            bearing_rad = math.atan2(dy, dx)
        # Compass bearing (0 = north, clockwise) — reported to the user, and
        # stored on arrow features to rotate their marker.
        bearing_deg = (90.0 - math.degrees(bearing_rad)) % 360.0
        try:
            fov_deg = float(fov_deg)
        except (TypeError, ValueError):
            fov_deg = float(self.fov_angle_spin.value())
        fov_deg = max(1.0, min(350.0, fov_deg))

        # Arrow types are a plain point carrying a bearing — no cone, no wedge.
        arrow_only = ftype in ARROW_DIRECTIONAL_TYPES

        # Single-object types (e.g. Observer Station) are stored as ONE feature:
        # just the FOV wedge polygon, whose symbology draws the station icon at
        # the apex. Other directional types still get a point marker + wedge.
        single_obj = ftype in SINGLE_OBJECT_DIRECTIONAL_TYPES

        # Shared uuid links the marker and its wedge so a single undo removes both
        # (and lets undo find the lone wedge for single-object types).
        link_uuid = str(uuid.uuid4())

        ok_pt = True
        pt_layer = self._get_or_create_layer()
        pt_layer.reload()
        label = self.label_edit.text().strip()

        if not single_obj:
            # 1) Point marker at the apex
            label = self._auto_label_if_needed(pt_layer, label, ftype)

            pfeat = QgsFeature(pt_layer.fields())
            pfeat.setAttribute("label", label)
            pfeat.setAttribute("gridref", "")
            pfeat.setAttribute("ftype", ftype)
            pfeat.setAttribute("uuid", link_uuid)
            if arrow_only:
                # Rotates the marker (see _make_symbol's data-defined angle).
                pfeat.setAttribute("bearing", bearing_deg)
            pfeat.setGeometry(QgsGeometry.fromPointXY(origin))
            pt_layer.startEditing()
            ok_pt = pt_layer.addFeature(pfeat)
            pt_layer.commitChanges()
            pt_layer.triggerRepaint()

        if arrow_only:
            if not ok_pt:
                QMessageBox.critical(self, "Error", f"Failed to add {ftype}.")
                return
            self._record_placement((link_uuid, None))
            self._update_counts()
            self._record_recent(ftype)
            self._note_placed(f"✓ {ftype} placed — facing {bearing_deg:.0f}°")
            self.label_edit.clear()
            self._push_global_undo_uuid(link_uuid)
            self._rearm_placement(self.activate_aim_tool)
            return

        # 2) Field-of-view wedge polygon
        wedge_ftype = FOV_WEDGE_FTYPE.get(ftype, "Observer Field of View")
        half = math.radians(max(1.0, fov_deg) / 2.0)
        segments = 18
        ring = [QgsPointXY(origin.x(), origin.y())]
        for i in range(segments + 1):
            a = bearing_rad - half + (2 * half) * (i / segments)
            ring.append(QgsPointXY(
                origin.x() + rng * math.cos(a),
                origin.y() + rng * math.sin(a),
            ))
        ring.append(QgsPointXY(origin.x(), origin.y()))
        wedge_geom = QgsGeometry.fromPolygonXY([ring])

        poly_layer = self._get_or_create_poly_layer()
        poly_layer.reload()
        self._apply_style(poly_layer)

        if single_obj:
            # The wedge is the whole object: carry the station's own label and
            # auto-number against the wedges already on the polygon layer.
            label = self._auto_label_for_single_obj(poly_layer, label, ftype)
            wedge_label = label
        else:
            wedge_label = f"{label} FOV" if label else ""

        wfeat = QgsFeature(poly_layer.fields())
        wfeat.setGeometry(wedge_geom)
        wfeat.setAttribute("label", wedge_label)
        wfeat.setAttribute("gridref", "")
        wfeat.setAttribute("ftype", wedge_ftype)
        wfeat.setAttribute("uuid", link_uuid)
        # Give each built-in observer/surveyor/camera cone its own colour from
        # the shared palette so overlapping fields of view stay tellable-apart.
        # Custom survey positions keep the single colour the user chose for them.
        if ftype in AUTO_FOV_COLOUR_TYPES:
            wfeat.setAttribute("fov_color", self._next_fov_colour(poly_layer))
        poly_layer.startEditing()
        ok_poly = poly_layer.addFeature(wfeat)
        poly_layer.commitChanges()
        poly_layer.triggerRepaint()

        if not ok_pt or (single_obj and not ok_poly):
            QMessageBox.critical(self, "Error", f"Failed to add {ftype}.")
            return

        # One undo entry removes the feature(s) (shared uuid; undo scans layers).
        self._record_placement((link_uuid, None))
        self._update_counts()
        self._record_recent(ftype)
        self._note_placed(
            f"✓ {ftype} placed — bearing {bearing_deg:.0f}°, "
            f"{fov_deg:.0f}° cone, {rng:.0f} m"
        )
        self.label_edit.clear()
        # Bridge to main panel global undo — one entry covers the marker AND
        # its FOV wedge, so a global Ctrl+Z can't strand the cone polygon.
        self._push_global_undo_uuid(link_uuid)
        self._rearm_placement(self.activate_aim_tool)

    def activate_line_tool(self):
        self._capture_prev_tool()
        self._line_tool = _CaptureLineTool(self.canvas, self._finished_line, self._cancel_line)
        self.canvas.setMapTool(self._line_tool)
        self.iface.messageBar().pushMessage(
            "Biome",
            "Left-click to add vertices — Right-click to finish line — Esc to cancel",
            level=Qgis.MessageLevel.Info, duration=0,
        )

    def _cancel_line(self):
        self.iface.messageBar().clearWidgets()
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)
        else:
            self._activate_pan_tool()

    def _finished_line(self, points: List[QgsPointXY]):
        self.iface.messageBar().clearWidgets()
        self._activate_pan_tool()
        if not self._require_saved_project():
            return
        tr = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), LAYER_CRS, QgsProject.instance())
        pts = [tr.transform(p) for p in points]
        self._commit_line_geometry(
            QgsGeometry.fromPolylineXY(pts), continuing=False)
        # A standard line is completed with right-click. That same gesture also
        # ends the placement run; immediately re-arming here left the primary
        # button showing "Stop placing" after the user had finished.
        self._arm_seq += 1
        self._refresh_place_btn()

    def _commit_line_geometry(self, geom, continuing=True):
        """Commit a ready-made polyline geometry (LAYER_CRS) as a line feature.
        Shared by free-drawn lines and shape-outline (perimeter) placement."""
        if not self._require_saved_project():
            return
        layer = self._get_or_create_line_layer()
        self._ensure_fields(layer)
        layer.reload()
        self._apply_style(layer)

        label = self.label_edit.text().strip()
        ftype = self.type_combo.currentText().strip()
        if not label:
            label = self._auto_label_if_needed(layer, label, ftype)

        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttribute("label",   label)
        feat.setAttribute("gridref", "")
        feat.setAttribute("ftype",   ftype)
        feat.setAttribute("uuid",    str(uuid.uuid4()))

        layer.startEditing()
        ok = layer.addFeature(feat)
        layer.commitChanges()
        layer.triggerRepaint()
        if ok:
            buf_uuid = None
            self._record_placement((feat["uuid"], buf_uuid))
            # One global entry covers the line and its buffer zone.
            self._push_global_undo_uuid(feat["uuid"], buf_uuid)
            self._update_counts()
            self._record_recent(ftype)
            self._note_placed(f"✓ {ftype} placed", continuing=continuing)
            self.label_edit.clear()
        else:
            QMessageBox.critical(self, "Error", "Failed to add line feature.")

    def _on_shape_dims_changed(self, *_):
        """Relabel the three dimension fields to suit the chosen shape."""
        if not hasattr(self, "shape_combo"):
            return
        key = self.shape_combo.currentData() or "circle"
        # (label_for_spin_0, label_for_spin_1, label_for_spin_2) — None hides it
        specs = {
            "circle":    ("Diameter (m):", None, None),
            "square":    ("Side (m):", None, None),
            "rectangle": ("Width (m):", "Height (m):", None),
            "ellipse":   ("Long axis (m):", "Short axis (m):", None),
            "stadium":   ("Width (m):", "Height (m):", "Corner radius (m):"),
            "triangle":  ("Side (m):", None, None),
            "regular":   ("Side length (m):", None, None),
            "annulus":   ("Outer diameter (m):", "Ring width (m):", None),
        }
        labels = specs.get(key, ("Size (m):", None, None))
        for i, lbl in enumerate(labels):
            visible = lbl is not None
            self._shape_dim_lbls[i].setText(lbl or "")
            self._shape_dim_lbls[i].setVisible(visible)
            self._shape_dim_spins[i].setVisible(visible)
        # "Sides" only applies to a regular polygon — hide it otherwise.
        is_regular = (key == "regular")
        self.shape_sides_lbl.setVisible(is_regular)
        self.shape_sides_spin.setVisible(is_regular)
        # Rotation is meaningless for rotationally-symmetric shapes.
        has_rotation = key not in ("circle", "annulus")
        self.shape_rot_lbl.setVisible(has_rotation)
        self.shape_rot_spin.setVisible(has_rotation)

    def _current_shape_dims(self):
        """Return (shape_key, dims_dict, rotation_deg) from the controls.
        Dim keys match habitat_form._local_ring's contract exactly."""
        key = self.shape_combo.currentData() or "circle"
        v = [s.value() for s in self._shape_dim_spins]
        rot = float(self.shape_rot_spin.value())
        if key == "circle":
            dims = {"d": v[0]}
        elif key == "square":
            dims = {"s": v[0]}
        elif key == "rectangle":
            dims = {"w": v[0], "h": v[1]}
        elif key == "ellipse":
            dims = {"a": v[0], "b": v[1]}          # long-axis dia, short-axis dia
        elif key == "stadium":
            dims = {"w": v[0], "h": v[1], "r": v[2]}
        elif key == "triangle":
            dims = {"s": v[0]}
        elif key == "regular":
            dims = {"s": v[0], "n": int(self.shape_sides_spin.value())}
        elif key == "annulus":
            dims = {"d": v[0], "w": v[1]}
        else:
            dims = {"d": v[0]}
        return key, dims, rot

    def activate_shape_tool(self):
        """Stamp a parametric shape of the chosen area feature type with a live
        ghost preview that follows the cursor. Mouse wheel rotates; arrow keys
        resize; left-click places; right-click / Esc cancels. Reuses the same
        _HabitatShapeTool the habitat mapper uses, so behaviour is identical."""
        from .habitat_form import _HabitatShapeTool
        ftype = self.type_combo.currentText().strip()
        if not ftype:
            QMessageBox.information(self, "Biome",
                                    "Select a feature type first.")
            return
        self._capture_prev_tool()

        def _get_params():
            return self._current_shape_dims()

        def _bump_rotation(delta):
            self.shape_rot_spin.setValue((self.shape_rot_spin.value() + delta) % 360)

        def _bump_dimension(axis, delta):
            # axis 0 = first dim spin (width/diameter/side), axis 1 = second
            spins = [s for s in self._shape_dim_spins if s.isVisible()]
            if axis < len(spins):
                spins[axis].setValue(max(0.1, spins[axis].value() + delta))

        self._shape_tool = _HabitatShapeTool(
            self.canvas, _get_params, _bump_rotation,
            on_finished_cb=self._on_shape_finished,
            on_cancel_cb=self._on_shape_cancel,
            layer_crs=LAYER_CRS,
            bump_dimension=_bump_dimension,
        )
        self.canvas.setMapTool(self._shape_tool)
        # This dialog is a separate top-level window, so it keeps key focus and
        # the arrow keys never reach the canvas (where the tool's own event
        # filter lives). Forward them from the application level instead, so
        # arrow-key resize works just like it does in the Building & Tree placer.
        self._install_shape_key_filter()
        self.iface.messageBar().pushMessage(
            "Biome",
            "Move to position the shape \u2014 wheel rotates, arrow keys resize "
            "(\u2190\u2192 width, \u2191\u2193 height; Shift = fine) \u2014 "
            "left-click to place \u2014 right-click / Esc to cancel",
            level=Qgis.MessageLevel.Info, duration=0)

    def _install_shape_key_filter(self):
        """Install this dialog as an application-wide event filter so arrow keys
        reach the active shape tool no matter which dialog widget holds focus."""
        if not getattr(self, "_shape_key_filter_on", False):
            QApplication.instance().installEventFilter(self)
            self._shape_key_filter_on = True

    def _remove_shape_key_filter(self):
        if getattr(self, "_shape_key_filter_on", False):
            QApplication.instance().removeEventFilter(self)
            self._shape_key_filter_on = False

    def eventFilter(self, obj, event):
        """While the shape tool is active, forward arrow-key presses to it. The
        tool resizes the ghost and consumes the key (stopping a spin box from
        also stepping). All other events pass straight through."""
        if (event.type() == QEvent.Type.KeyPress
                and getattr(self, "_shape_tool", None) is not None
                and self.canvas.mapTool() is self._shape_tool):
            if self._shape_tool.handle_key_event(event):
                return True
        return super().eventFilter(obj, event)

    def _on_shape_cancel(self):
        self._remove_shape_key_filter()
        self.iface.messageBar().clearWidgets()
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)
        else:
            self._activate_pan_tool()

    def _on_shape_finished(self, rings):
        """rings arrive in the tool's metric CRS (EPSG:27700 == LAYER_CRS), so
        they can be committed directly. In Area mode the shape is a polygon; in
        Line mode its outline is committed as a polyline. The tool keeps itself
        active for the next placement (continuous stamping)."""
        self.iface.messageBar().clearWidgets()
        if not rings:
            return
        if self.geometry_combo.currentText() == "Line":
            ring = rings[0] if rings else []
            if len(ring) < 2:
                return
            self._commit_line_geometry(QgsGeometry.fromPolylineXY(ring))
            msg = "Outline placed \u2014 move to place another, or right-click / Esc to stop"
        else:
            try:
                geom = QgsGeometry.fromPolygonXY(rings)
            except Exception as e:
                QMessageBox.critical(self, "Biome", "Could not build shape: %s" % e)
                return
            self._commit_area_geometry(geom)
            msg = "Shape placed \u2014 move to place another, or right-click / Esc to stop"
        # Continuous stamping: the tool keeps itself armed until right-click /
        # Esc / the ■ Stop button ends the run.
        self.iface.messageBar().pushMessage("Biome", msg, level=Qgis.MessageLevel.Info, duration=0)

    def _clean_placed_geometry(self, geom, layer):
        """If a just-placed polygon outline is INVALID (self-intersecting, stray
        slivers), repair it (makeValid + drop <1 m² parts) and tell the user with
        an OK notice. Valid outlines are returned untouched. Returns the geometry
        to actually store, so the feature AND any buffer use the clean one."""
        try:
            from qgis.core import QgsWkbTypes, QgsDistanceArea
            if (geom is None or geom.isNull() or geom.isEmpty()
                    or QgsWkbTypes.geometryType(geom.wkbType())
                    != QgsWkbTypes.GeometryType.PolygonGeometry
                    or geom.isGeosValid()):
                return geom
            from .measurements import clean_polygon_geometry
            da = QgsDistanceArea()
            try:
                da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
                da.setEllipsoid(measurement_ellipsoid())
            except Exception:
                pass
            cleaned, dropped = clean_polygon_geometry(geom, da=da)
            if cleaned is None or cleaned.isNull() or cleaned.isEmpty():
                return geom
            extra = (" and removed %d stray sliver%s"
                     % (dropped, "" if dropped == 1 else "s")) if dropped else ""
            QMessageBox.information(
                self, "Geometry cleaned",
                "The outline you placed was invalid (self-intersecting or with "
                "stray slivers), which would make the parcel's area calculate "
                "incorrectly. Biome repaired it%s.\n\nPlease check the parcel "
                "looks right." % extra)
            return cleaned
        except Exception:
            return geom

    def _commit_area_geometry(self, geom):
        """Commit a ready-made polygon geometry (LAYER_CRS) as an area feature.
        Shared by Shape placement; mirrors the tail of _finished_polygon."""
        if not self._require_saved_project():
            return
        layer = self._get_or_create_poly_layer()
        self._ensure_fields(layer)
        layer.reload()
        self._apply_style(layer)
        geom = self._clean_placed_geometry(geom, layer)

        label = self.label_edit.text().strip()
        ftype = self.type_combo.currentText().strip()
        if not label:
            label = self._auto_label_if_needed(layer, label, ftype)

        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttribute("label",   label)
        feat.setAttribute("gridref", "")
        feat.setAttribute("ftype",   ftype)
        feat.setAttribute("uuid",    str(uuid.uuid4()))

        layer.startEditing()
        ok = layer.addFeature(feat)
        layer.commitChanges()
        layer.triggerRepaint()
        if ok:
            buf_uuid = None
            self._record_placement((feat["uuid"], buf_uuid))
            self._update_counts()
            self._record_recent(ftype)
            self._note_placed(f"\u2713 {ftype} shape placed")
            # One global entry covers the shape and its buffer zone.
            self._push_global_undo_uuid(feat["uuid"], buf_uuid)
            self.label_edit.clear()
        else:
            QMessageBox.critical(self, "Error", "Failed to add shape feature.")

    def activate_polygon_tool(self):
        self._capture_prev_tool()
        use_freehand = hasattr(self, 'freehand_chk') and self.freehand_chk.isChecked()
        if use_freehand:
            self._poly_tool = _FreehandPolygonTool(
                self.canvas,
                lambda points: self._finished_polygon(points, continuing=True),
                self._cancel_polygon)
            self.canvas.setMapTool(self._poly_tool)
            self.iface.messageBar().pushMessage(
                "Biome",
                "Hold left mouse button and drag to draw freehand — release to finish — Esc to cancel",
                level=Qgis.MessageLevel.Info, duration=0,
            )
        else:
            self._poly_tool = _CapturePolygonTool(
                self.canvas,
                lambda points: self._finished_polygon(points, continuing=False),
                self._cancel_polygon)
            self.canvas.setMapTool(self._poly_tool)
            self.iface.messageBar().pushMessage(
                "Biome",
                "Left-click to add vertices — Right-click to close polygon — Esc to cancel",
                level=Qgis.MessageLevel.Info, duration=0,
            )

    def _cancel_polygon(self):
        self.iface.messageBar().clearWidgets()
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)
        else:
            self._activate_pan_tool()

    def _finished_polygon(self, points: List[QgsPointXY], continuing=True):
        self.iface.messageBar().clearWidgets()
        self._activate_pan_tool()
        if not self._require_saved_project():
            return

        layer = self._get_or_create_poly_layer()
        self._ensure_fields(layer)
        layer.reload()
        self._apply_style(layer)
        tr = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), LAYER_CRS, QgsProject.instance())
        pts = [tr.transform(p) for p in points]
        geom = QgsGeometry.fromPolygonXY([pts])
        geom = self._clean_placed_geometry(geom, layer)

        label = self.label_edit.text().strip()
        ftype = self.type_combo.currentText().strip()
        if not label:
            label = self._auto_label_if_needed(layer, label, ftype)

        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttribute("label",   label)
        feat.setAttribute("gridref", "")
        feat.setAttribute("ftype",   ftype)
        feat.setAttribute("uuid",    str(uuid.uuid4()))

        layer.startEditing()
        ok = layer.addFeature(feat)
        layer.commitChanges()
        layer.triggerRepaint()
        if ok:
            buf_uuid = None
            self._record_placement((feat["uuid"], buf_uuid))
            self._update_counts()
            self._record_recent(ftype)
            self._note_placed(f"✓ {ftype} area placed", continuing=continuing)
            # One global entry covers the area and its buffer zone.
            self._push_global_undo_uuid(feat["uuid"], buf_uuid)
            self.label_edit.clear()
            if continuing:
                self._rearm_placement(self.activate_polygon_tool)
            else:
                # Standard polygons finish on right-click, which also ends the
                # run. Freehand polygons finish on left-button release and keep
                # their existing continuous-placement behaviour.
                self._arm_seq += 1
                self._refresh_place_btn()
        else:
            QMessageBox.critical(self, "Error", "Failed to add area feature.")

    # ---------------- Counts summary ----------------
    def _update_counts(self):
        """Rebuild the 'On the map' rail AND the curved-label arc layer. Every
        feature change (place / rename / relocate / delete / undo / edit) calls
        this, so both stay in sync in one place."""
        self._rebuild_feature_list()
        if hasattr(self, "_do_refresh_fov_arcs"):
            self._do_refresh_fov_arcs()

    def _rebuild_feature_list(self):
        """Rebuild the interactive 'On the map' list — one row per placed
        feature, grouped by geometry, each with zoom / edit / delete."""
        if not hasattr(self, "_feat_list_layout"):
            return
        while self._feat_list_layout.count():
            item = self._feat_list_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        def _flag(f, name, default):
            # A newly-added Int field reads back as a NULL QVariant (not Python
            # None) for pre-existing features, so guard int() and fall back.
            try:
                v = f[name]
            except (KeyError, IndexError):
                return default
            if v is None:
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _rows(layer, kind):
            rows = []
            if layer is None:
                return rows
            for f in layer.getFeatures():
                row = {"layer": layer, "fid": f.id(), "uuid": f["uuid"],
                       "label": (f["label"] or ""), "ftype": (f["ftype"] or ""),
                       "kind": kind,
                       "hide_label": _flag(f, "hide_label", 0),
                       "curved": _flag(f, "label_curved", 1)}
                if kind == "buffer":
                    try:
                        row["parent_uuid"] = str(
                            f.attribute("buffer_parent_uuid") or "")
                    except (KeyError, IndexError):
                        row["parent_uuid"] = ""
                    row["distance"] = self._distance_from_buffer_feature(f)
                rows.append(row)
            return rows

        pt_layer = self._get_existing_layer()
        ln_layer = self._get_existing_line_layer()
        pl_layer = self._get_existing_poly_layer()
        buf_layer = self._get_existing_buffer_layer()

        pt_rows = _rows(pt_layer, "point")
        pt_uuids = {r["uuid"] for r in pt_rows if r["uuid"]}
        pl_rows_all = _rows(pl_layer, "area")
        poly_uuids = {r["uuid"] for r in pl_rows_all if r["uuid"]}
        # FOV wedges paired with a point marker are represented by the point row
        area_rows = [r for r in pl_rows_all if r["uuid"] not in pt_uuids]

        groups = []
        if pt_rows:
            groups.append(("Point features", pt_rows))
        ln_rows = _rows(ln_layer, "line")
        if ln_rows:
            groups.append(("Linear features", ln_rows))
        if area_rows:
            groups.append(("Area features", area_rows))
        buf_rows = _rows(buf_layer, "buffer")

        # Attach each buffer to its source feature.  Current buffers carry the
        # source UUID; older saved buffers are matched from their generated
        # '<feature> <distance>m Buffer' label.
        all_parent_rows = pt_rows + ln_rows + area_rows
        parent_by_uuid = {
            str(row["uuid"]): row for row in all_parent_rows if row["uuid"]}
        buffers_by_parent = {}
        unlinked_buffers = []
        for buffer_row in buf_rows:
            parent = parent_by_uuid.get(buffer_row.get("parent_uuid", ""))
            if parent is None:
                match = re.match(
                    r"^(.*) \d+(?:\.\d+)?m Buffer$",
                    buffer_row.get("label", "") or "")
                parent_name = match.group(1) if match else ""
                candidates = [
                    row for row in all_parent_rows
                    if parent_name and (row.get("label") or "") == parent_name]
                if len(candidates) == 1:
                    parent = candidates[0]
            if parent is None:
                unlinked_buffers.append(buffer_row)
            else:
                buffers_by_parent.setdefault(id(parent), []).append(buffer_row)
        for rows in buffers_by_parent.values():
            rows.sort(key=lambda row: (
                float("inf") if row.get("distance") is None else row["distance"],
                row.get("label", "")))

        total = len(pt_rows) + len(ln_rows) + len(area_rows)

        if not groups and not buf_rows:
            self._feat_list_layout.addStretch(1)
            ph = QLabel(
                "No features placed yet.\n\n"
                "Choose a feature on the left,\nthen click the map to place it."
            )
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setWordWrap(True)
            ph.setStyleSheet("color:%s; padding:18px;" % theme.FAINT)
            self._feat_list_layout.addWidget(ph)
            self._feat_list_layout.addStretch(2)
            self._feat_total_lbl.setText("")
            return

        for title, rows in groups:
            self._feat_list_layout.addWidget(self._feat_group_header(title, len(rows)))
            for r in rows:
                fov = (r["kind"] == "point" and r["uuid"] in poly_uuids)
                self._feat_list_layout.addWidget(self._make_feature_row(r, fov))
                for buffer_row in buffers_by_parent.get(id(r), []):
                    self._feat_list_layout.addWidget(
                        self._make_feature_row(buffer_row, False, nested=True))
        # Preserve access to malformed or very old buffers whose source can no
        # longer be identified, rather than silently dropping them from the UI.
        if unlinked_buffers:
            self._feat_list_layout.addWidget(
                self._feat_group_header("Unlinked buffers", len(unlinked_buffers)))
            for r in unlinked_buffers:
                self._feat_list_layout.addWidget(
                    self._make_feature_row(r, False, nested=True))
        self._feat_list_layout.addStretch(1)
        self._feat_total_lbl.setText("%d feature%s" % (total, "" if total == 1 else "s"))

    def _feat_group_header(self, title, n):
        lbl = QLabel("%s  ·  %d" % (title, n))
        lbl.setStyleSheet(
            "color:%s; font-size:11px; font-family:%s; padding:5px 2px 1px;"
            % (theme.MUTED, theme.FONT_MEDIUM))
        return lbl

    _GEOM_TAG = {"point": "P", "line": "L", "area": "A", "buffer": "B"}

    def _make_feature_row(self, r, fov=False, nested=False):
        """One feature row in the 'On the map' rail, styled like the main
        Manage-features list: a word-wrapped label (so it never forces a side
        scroll) plus soft-filled relocate / rename / delete icon buttons.
        Hovering highlights the feature on the map and darkens the row."""
        kind = r["kind"]
        row = _HoverWidget()
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _normal = ("background:#F2F7F3; border:1px solid #E1ECE4; border-radius:6px;"
                   if nested else
                   "background:#FFFFFF; border:1px solid #EAF2EC; border-radius:6px;")
        _hover = "background:#D8ECDE; border:1px solid #B6D7C1; border-radius:6px;"
        row.setStyleSheet(_normal)
        h = QHBoxLayout(row)
        h.setContentsMargins(22 if nested else 8, 4 if nested else 5, 6,
                             4 if nested else 5)
        h.setSpacing(6)

        tag = QLabel(self._GEOM_TAG.get(kind, "?"))
        tag.setFixedSize(16, 16)
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setStyleSheet(
            "background:%s; color:white; border:none; border-radius:4px;"
            " font-size:9px; font-weight:bold;"
            % (theme.FAINT if kind == "buffer" else theme.ACCENT))
        h.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)

        # Just the feature's name — its type is implied by the name in almost
        # every case (e.g. "Camera-01"), so the "· Camera Field of View" suffix
        # was redundant clutter. Fall back to the type only when unlabelled.
        text = (r["label"] or r["ftype"] or "(unlabelled)") + ("  +FOV" if fov else "")
        lbl = QLabel(text)
        lbl.setWordWrap(True)   # wraps to a 2nd line instead of forcing the row wider
        lbl.setStyleSheet(
            "border:none; background:transparent; font-size:11px; color:%s;" % theme.INK)
        h.addWidget(lbl, 1)

        def _row_btn(icon_name, colour, tip, slot, hover_bg="#D8ECDE"):
            b = QPushButton()
            b.setFixedSize(28, 26)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setToolTip(tip)
            b.setIcon(theme.icon(icon_name, colour, 16))
            b.setIconSize(QSize(16, 16))
            b.setStyleSheet(
                "QPushButton{background:#E9F4EC; border:none; border-radius:6px;}"
                "QPushButton:hover{background:%s;}" % hover_bg)
            b.clicked.connect(lambda _c: slot())
            return b

        if kind != "buffer":
            h.addWidget(_row_btn(
                "arrows-move", theme.MUTED,
                "Relocate — attached buffers move automatically",
                lambda rr=r: self._relocate_feature(rr)),
                0, Qt.AlignmentFlag.AlignTop)
            h.addWidget(_row_btn(
                "circles", theme.ACCENT_DARK,
                "Add buffer zones",
                lambda rr=r: self._add_buffer_to_feature(rr)),
                0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(_row_btn("pencil", theme.MUTED, "Edit — rename, show/hide, curved/straight",
                             lambda rr=r: self._edit_feature(rr)), 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(_row_btn("trash", theme.BAD, "Delete (use Redo to restore)",
                             lambda u=r["uuid"]: self._delete_placed(u),
                             hover_bg="#F6D9D5"), 0, Qt.AlignmentFlag.AlignTop)

        row._on_enter = lambda rr=r, w=row, hc=_hover: (
            self._highlight_feature(rr), w.setStyleSheet(hc))
        row._on_leave = lambda w=row, nc=_normal: (
            self._clear_highlight(), w.setStyleSheet(nc))
        row._on_click = lambda lyr=r["layer"], fid=r["fid"]: self._zoom_to_feature(lyr, fid)
        return row

    def _highlight_feature(self, r):
        """Highlight the feature on the canvas exactly like the Manage-features
        list — a QgsHighlight with a bold, slightly see-through cyan/teal outline
        + halo so points and thin lines are easy to spot on any basemap."""
        self._clear_highlight()
        layer = r.get("layer")
        if layer is None:
            return
        try:
            feat = layer.getFeature(r["fid"])
        except Exception:
            return
        if feat is None or not feat.isValid():
            return
        from qgis.gui import QgsHighlight
        h = QgsHighlight(self.canvas, feat, layer)
        h.setColor(QColor(0, 190, 210, 180))    # cyan/teal, slightly see-through
        h.setFillColor(QColor(0, 190, 210, 50))
        h.setWidth(6)
        try:
            h.setBuffer(1.2)
        except Exception:
            pass
        try:
            h.setMinWidth(5.0)
        except Exception:
            pass
        h.show()
        self._hl_band = h

    def _clear_highlight(self):
        h = getattr(self, "_hl_band", None)
        if h is not None:
            try:
                h.hide()
            except Exception:
                pass
            self._hl_band = None

    def _relocate_feature(self, r):
        """Move a placed feature: the next map click sets its new location."""
        self._relocate_target = r
        self._capture_prev_tool()
        self._relocate_tool = _PickPointTool(self.canvas, self._relocate_clicked, self._cancel_relocate)
        self.canvas.setMapTool(self._relocate_tool)
        self.iface.messageBar().pushMessage(
            "Biome", "Click the new location for “%s” — right-click to cancel."
            % (r["label"] or r["ftype"] or "feature"), level=Qgis.MessageLevel.Info, duration=0)

    def _cancel_relocate(self):
        """Right-click during a relocate → abandon the move."""
        self._relocate_target = None
        self.iface.messageBar().clearWidgets()
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)
        else:
            self._activate_pan_tool()

    def _relocate_clicked(self, canvas_point):
        self.iface.messageBar().clearWidgets()
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)
        else:
            self._activate_pan_tool()
        r = getattr(self, "_relocate_target", None)
        self._relocate_target = None
        if not r:
            return
        try:
            xform = QgsCoordinateTransform(
                self.canvas.mapSettings().destinationCrs(), LAYER_CRS, QgsProject.instance())
            target = xform.transform(canvas_point)
        except Exception:
            return
        self._move_feature_to(r, QgsPointXY(target.x(), target.y()))

    def _move_feature_to(self, r, target_pt):
        """Translate the feature — plus any FOV wedge and buffer sharing its
        uuid — so its centre lands on target_pt (LAYER_CRS)."""
        layer = r.get("layer")
        if layer is None:
            return
        try:
            f = layer.getFeature(r["fid"]); g = f.geometry()
        except Exception:
            return
        if g is None or g.isEmpty():
            return
        c = g.centroid().asPoint()
        dx, dy = target_pt.x() - c.x(), target_pt.y() - c.y()
        buf_uuid = None
        for entry in self._undo_stack:
            fu, bu = entry if isinstance(entry, tuple) else (entry, None)
            if fu == r["uuid"]:
                buf_uuid = bu
                break
        jobs = [([self._get_existing_layer(), self._get_existing_line_layer(),
                  self._get_existing_poly_layer()], r["uuid"])]
        if buf_uuid:
            jobs.append(([self._get_existing_buffer_layer()], buf_uuid))
        for lyrs, u in jobs:
            if not u:
                continue
            for lyr in lyrs:
                if lyr is None:
                    continue
                req = QgsFeatureRequest().setFilterExpression(f"\"uuid\" = '{u}'")
                feats = list(lyr.getFeatures(req))
                if not feats:
                    continue
                lyr.startEditing()
                for ff in feats:
                    gg = QgsGeometry(ff.geometry())
                    gg.translate(dx, dy)
                    lyr.changeGeometry(ff.id(), gg)
                lyr.commitChanges()
                lyr.triggerRepaint()
        # Buffers are derived from their parent geometry, not independently
        # movable objects. Rebuild every attached band around the new position;
        # this also handles all modern multi-band buffers and legacy labels.
        self._refresh_all_feature_buffer_bands()
        self.canvas.refresh()
        self._update_counts()
        self.status_lbl.setText("✓ %s moved" % (r["label"] or r["ftype"] or "feature"))

    def _delete_features_by_uuid(self, layers, target_uuid):
        """Delete every feature carrying target_uuid across the given layers.
        Also drops any global-undo entries referencing the deleted features so
        the main panel's stack never holds a stale (recyclable) fid."""
        if not target_uuid:
            return False
        found = False
        removed_pairs = []
        for layer in layers:
            if layer is None:
                continue
            req = QgsFeatureRequest().setFilterExpression(f"\"uuid\" = '{target_uuid}'")
            ids = [f.id() for f in layer.getFeatures(req)]
            if ids:
                layer.startEditing()
                for fid in ids:
                    layer.deleteFeature(fid)
                layer.commitChanges()
                layer.triggerRepaint()
                removed_pairs.extend((layer, fid) for fid in ids)
                found = True
        if removed_pairs and self._main_panel and hasattr(
                self._main_panel, "drop_undo_entries"):
            try:
                self._main_panel.drop_undo_entries(removed_pairs)
            except Exception:
                pass
        return found

    def _delete_placed(self, feat_uuid):
        """Delete a placed feature (and its paired FOV wedge / buffer) from the
        list. Captured to the redo stack so it can be restored, and the matching
        undo entry is dropped so the stacks stay consistent."""
        if not feat_uuid:
            return
        all_layers = [self._get_existing_layer(), self._get_existing_line_layer(),
                      self._get_existing_poly_layer(), self._get_existing_buffer_layer()]
        buf_layer = self._get_existing_buffer_layer()
        buf_uuid = None
        for i, entry in enumerate(self._undo_stack):
            fu, bu = entry if isinstance(entry, tuple) else (entry, None)
            if fu == feat_uuid:
                buf_uuid = bu
                del self._undo_stack[i]
                break
        captured = self._capture_by_uuid(all_layers, feat_uuid)
        if buf_uuid:
            captured += self._capture_by_uuid([buf_layer], buf_uuid)
        self._delete_features_by_uuid(all_layers, feat_uuid)
        if buf_uuid:
            self._delete_features_by_uuid([buf_layer], buf_uuid)
        if captured:
            self._redo_stack.append({"entry": (feat_uuid, buf_uuid), "feats": captured})
        self._refresh_all_feature_buffer_bands()
        self._update_undo_state()
        self._update_counts()

    def _edit_feature(self, r):
        """Popup 'Edit' menu for an 'On the map' row: rename, show/hide the label,
        and (for points & FOV cones) switch the label curved/straight."""
        from qgis.PyQt.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename_feature(r))
        hidden = int(r.get("hide_label", 0)) == 1
        menu.addAction("Show label" if hidden else "Hide label",
                       lambda: self._set_feature_flag(
                           r, "hide_label", 0 if hidden else 1))
        # Curved/straight only applies to labels carried on the arc layer:
        # plain points, and FOV wedge cones. No point offering it when hidden.
        curvable = (r["kind"] == "point"
                    or (r["kind"] == "area"
                        and "Field of View" in (r["ftype"] or "")))
        if curvable and not hidden:
            curved = int(r.get("curved", 1)) != 0
            menu.addAction("Straight label" if curved else "Curved label",
                           lambda: self._set_feature_flag(
                               r, "label_curved", 0 if curved else 1))
        if r["kind"] == "buffer":
            # A buffer built from a LINE can be switched left / right / centre.
            parent = self._buffer_parent(r)
            if parent is not None and parent[1] == "line":
                menu.addSeparator()
                menu.addAction("Buffer left of line",
                               lambda: self._reshape_buffer(r, "Left only"))
                menu.addAction("Buffer right of line",
                               lambda: self._reshape_buffer(r, "Right only"))
                menu.addAction("Buffer both sides (centre)",
                               lambda: self._reshape_buffer(r, None))
        menu.exec(QCursor.pos())

    def _buffer_parent(self, r):
        """Resolve the feature a buffer row was built from, via its label
        ('<name> <dist>m Buffer'). Returns (parent_geometry, kind, dist) or None.
        Used so a line's buffer can be recomputed left / right / both."""
        if r.get("kind") != "buffer":
            return None
        m = re.match(r"^(.*) (\d+(?:\.\d+)?)m Buffer$", r.get("label", "") or "")
        if not m:
            return None
        parent_name, dist = m.group(1), float(m.group(2))
        # Prefer a line parent (the only kind the side option applies to).
        for lyr, kind in ((self._get_existing_line_layer(), "line"),
                          (self._get_existing_layer(), "point"),
                          (self._get_existing_poly_layer(), "area")):
            if lyr is None:
                continue
            for f in lyr.getFeatures():
                if (f["label"] or "") == parent_name:
                    g = f.geometry()
                    if g is not None and not g.isEmpty():
                        return QgsGeometry(g), kind, dist
        return None

    def _reshape_buffer(self, r, line_side):
        """Recompute a line's buffer to a different side (Left/Right/centre) in
        place, leaving its label and everything else untouched."""
        parent = self._buffer_parent(r)
        if parent is None:
            QMessageBox.information(
                self, "Buffer",
                "Couldn't find the line this buffer belongs to — was it renamed?")
            return
        pgeom, _kind, dist = parent
        if line_side in ("Left only", "Right only"):
            new_geom = self._single_sided_buffer(pgeom, float(dist), line_side)
        else:
            new_geom = pgeom.buffer(float(dist), 8)
        if new_geom is None or new_geom.isEmpty():
            return
        layer = r.get("layer")
        if layer is None:
            return
        try:
            self._ensure_fields(layer)
            layer.startEditing()
            layer.changeGeometry(r["fid"], new_geom)
            side_idx = layer.fields().indexOf("buffer_side")
            if side_idx >= 0:
                layer.changeAttributeValue(
                    r["fid"], side_idx, line_side or "Both sides")
            layer.commitChanges()
            self._refresh_all_feature_buffer_bands()
            layer.triggerRepaint()
            self.status_lbl.setText("✓ Buffer side updated")
        except Exception:
            pass
        self._update_counts()

    def _prompt_buffer_distances(self, default, existing_bands, feature_kind):
        """Edit the complete buffer setup, including existing bands."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Buffer setup")
        dlg.setMinimumWidth(390)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("Buffer setup")
        title.setStyleSheet("font-size:15px; font-weight:700;")
        layout.addWidget(title)

        instruction = QLabel(
            "Edit the complete buffer setup below. Each distance becomes a "
            "separate, non-overlapping coloured band.")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        rows_layout = QVBoxLayout()
        rows_layout.setSpacing(7)
        layout.addLayout(rows_layout)
        rows = []

        add_btn = QPushButton("＋ Add another buffer")
        # Six is the normal limit (the original band plus five additions), but
        # never hide or discard older projects which already contain more.
        max_rows = max(6, len(existing_bands))

        def _renumber_rows():
            for index, row in enumerate(rows, 1):
                row["label"].setText(f"Buffer {index}")
            add_btn.setEnabled(len(rows) < max_rows)
            add_btn.setText(
                "＋ Add another buffer" if len(rows) < max_rows else
                f"Maximum of {max_rows} buffers")

        def _remove_row(row):
            rows.remove(row)
            rows_layout.removeWidget(row["widget"])
            row["widget"].deleteLater()
            _renumber_rows()

        def _add_row(value=None, side="Both sides"):
            if len(rows) >= max_rows:
                return
            widget = QFrame()
            row_layout = QHBoxLayout(widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            row_label = QLabel()
            row_label.setMinimumWidth(88)
            spin = QDoubleSpinBox()
            spin.setRange(0.1, 10000.0)
            spin.setDecimals(1)
            spin.setSingleStep(1.0)
            spin.setSuffix(" m")
            spin.setValue(float(default if value is None else value))
            spin.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Fixed)
            remove = QPushButton("Remove")
            remove.setFlat(True)
            side_combo = None
            if feature_kind == "line":
                side_combo = QComboBox()
                side_combo.addItems(["Both sides", "Left only", "Right only"])
                side_combo.setCurrentText(side or "Both sides")
                side_combo.setToolTip(
                    "Left and right follow the direction in which the line was drawn.")
            row = {"widget": widget, "label": row_label,
                   "spin": spin, "side": side_combo, "remove": remove}
            remove.clicked.connect(lambda _checked=False, item=row: _remove_row(item))
            row_layout.addWidget(row_label)
            row_layout.addWidget(spin, 1)
            if side_combo is not None:
                row_layout.addWidget(side_combo)
            row_layout.addWidget(remove)
            rows.append(row)
            rows_layout.addWidget(widget)
            _renumber_rows()

        add_btn.clicked.connect(lambda _checked=False: _add_row())
        layout.addWidget(add_btn)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        save_btn = QPushButton("Save buffers")
        save_btn.setDefault(True)
        footer.addWidget(cancel_btn)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

        result = []
        def _accept():
            values = [(
                round(float(row["spin"].value()), 6),
                row["side"].currentText() if row["side"] is not None
                else "Both sides") for row in rows]
            duplicates = {value for value in values if values.count(value) > 1}
            if duplicates:
                text = ", ".join(
                    f"{distance:g} m" +
                    (f" ({side.lower()})" if feature_kind == "line" else "")
                    for distance, side in sorted(duplicates))
                QMessageBox.information(
                    dlg, "Duplicate distances",
                    f"Each buffer row must be unique. Repeated: {text}.")
                return
            result.extend(values)
            dlg.accept()

        cancel_btn.clicked.connect(dlg.reject)
        save_btn.clicked.connect(_accept)
        if existing_bands:
            for distance, side in sorted(existing_bands):
                _add_row(distance, side)
        else:
            _add_row(default)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return result

    def _add_buffer_to_feature(self, r):
        """Add buffers around a pre-existing feature.

        Points, lines, and areas may receive up to five additional distance bands
        in one run. Line side is selected in the same consolidated dialog.
        """
        layer = r.get("layer")
        if layer is None:
            return
        try:
            f = layer.getFeature(r["fid"])
            g = f.geometry()
        except Exception:
            return
        if g is None or g.isEmpty():
            return
        default = 10.0

        ftype = r.get("ftype", "") or ""
        label = r.get("label", "") or ""
        parent_uuid = r.get("uuid", "") or ""
        added = []

        # Prevent duplicate distances on the same side of the same parent.
        existing_bands = set()
        existing_features = {}
        buffer_layer = self._get_existing_buffer_layer()
        if buffer_layer is not None:
            self._ensure_fields(buffer_layer)
            for feature in buffer_layer.getFeatures():
                feature_parent = str(feature.attribute("buffer_parent_uuid") or "")
                same_parent = bool(parent_uuid and feature_parent == parent_uuid)
                legacy_match = (not feature_parent and label and
                                str(feature.attribute("label") or "").startswith(f"{label} "))
                if same_parent or legacy_match:
                    distance = self._distance_from_buffer_feature(feature)
                    if distance is not None:
                        try:
                            side = str(feature.attribute("buffer_side") or "Both sides")
                        except (KeyError, IndexError):
                            side = "Both sides"
                        if r["kind"] != "line":
                            side = "Both sides"
                        key = (round(float(distance), 6), side)
                        existing_bands.add(key)
                        existing_features.setdefault(key, []).append(feature.id())

        setup = self._prompt_buffer_distances(
            float(default), existing_bands, r["kind"])
        if setup is None:
            return
        desired_bands = set(setup)

        # Remove rows which the user deleted or changed. Unchanged rows retain
        # their UUIDs and remain independently editable in the side panel.
        removed = 0
        if buffer_layer is not None:
            obsolete_fids = [
                fid for key, fids in existing_features.items()
                if key not in desired_bands for fid in fids]
            obsolete_fids.extend(
                fid for key, fids in existing_features.items()
                if key in desired_bands for fid in fids[1:])
            if obsolete_fids:
                buffer_layer.startEditing()
                for fid in obsolete_fids:
                    if buffer_layer.deleteFeature(fid):
                        removed += 1
                buffer_layer.commitChanges()

        for dist, side in sorted(desired_bands - existing_bands):
            line_side = side if r["kind"] == "line" else None
            if line_side == "Both sides":
                line_side = None
            buf_uuid = self._add_buffer_feature(
                g, label, ftype, float(dist), line_side=line_side,
                parent_uuid=parent_uuid, parent_kind=r["kind"])
            if not buf_uuid:
                break
            added.append(buf_uuid)
            # Each new band remains independently removable and undoable.
            self._push_global_undo_uuid(None, buf_uuid)

        if added or removed:
            # Rebuild every source first, then dissolve matching distances so
            # neighbouring features share one continuous set of bands.
            self._refresh_all_feature_buffer_bands()
            self.status_lbl.setText("✓ Buffer setup updated")
        self._update_counts()

    def _set_feature_flag(self, r, field, value):
        """Write a per-feature label flag (hide_label / label_curved) and refresh
        the labels, the curved-arc layer and the 'On the map' rail."""
        layer = r.get("layer")
        if layer is None:
            return
        try:
            self._ensure_fields(layer)
            idx = layer.fields().indexOf(field)
            if idx < 0:
                return
            layer.startEditing()
            layer.changeAttributeValue(r["fid"], idx, value)
            layer.commitChanges()
            self._apply_labeling(layer)
            layer.triggerRepaint()
        except Exception:
            pass
        self._update_counts()   # rebuilds rail + curved-label arcs

    def _rename_feature(self, r):
        """Prompt for a new label and write it back; keep a directional marker's
        FOV wedge label in step."""
        from qgis.PyQt.QtWidgets import QInputDialog
        layer = r["layer"]
        if layer is None:
            return
        new, ok = QInputDialog.getText(self, "Rename feature", "Label:", text=r["label"] or "")
        if not ok:
            return
        new = new.strip()
        idx = layer.fields().indexOf("label")
        if idx < 0:
            return
        layer.startEditing()
        layer.changeAttributeValue(r["fid"], idx, new)
        layer.commitChanges()
        layer.triggerRepaint()
        if r["kind"] == "point" and r["uuid"]:
            pl = self._get_existing_poly_layer()
            if pl is not None:
                pidx = pl.fields().indexOf("label")
                req = QgsFeatureRequest().setFilterExpression(f"\"uuid\" = '{r['uuid']}'")
                wfids = [wf.id() for wf in pl.getFeatures(req)]
                if wfids and pidx >= 0:
                    pl.startEditing()
                    for wfid in wfids:
                        pl.changeAttributeValue(wfid, pidx, (new + " FOV") if new else "")
                    pl.commitChanges()
                    pl.triggerRepaint()
        self._update_counts()

    def _zoom_to_feature(self, layer, fid):
        if layer is None:
            return
        try:
            f = layer.getFeature(fid)
            g = f.geometry()
        except Exception:
            return
        if g is None or g.isEmpty():
            return
        self._zoom_to_geom(g)

    def _zoom_to_geom(self, geom_27700):
        """Frame a line/area feature (bbox + margin); centre a point feature."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        xform = QgsCoordinateTransform(LAYER_CRS, canvas_crs, QgsProject.instance())
        g = QgsGeometry(geom_27700)
        try:
            g.transform(xform)
        except Exception:
            pass
        bb = g.boundingBox()
        if bb.width() <= 0 and bb.height() <= 0:
            self._pan_to_feature(geom_27700)
            return
        bb.scale(1.3)
        self.canvas.setExtent(bb)
        self.canvas.refresh()

    def _zoom_to_all(self):
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        xform = QgsCoordinateTransform(LAYER_CRS, canvas_crs, QgsProject.instance())
        full = QgsRectangle()
        full.setMinimal()
        any_feat = False
        for layer in (self._get_existing_layer(), self._get_existing_line_layer(),
                      self._get_existing_poly_layer(), self._get_existing_buffer_layer()):
            if layer is None:
                continue
            for f in layer.getFeatures():
                g = f.geometry()
                if g is None or g.isEmpty():
                    continue
                gg = QgsGeometry(g)
                try:
                    gg.transform(xform)
                except Exception:
                    continue
                full.combineExtentWith(gg.boundingBox())
                any_feat = True
        if not any_feat:
            return
        full.scale(1.1)
        self.canvas.setExtent(full)
        self.canvas.refresh()

    # ---------------- Emoji PNG rendering ----------------
    def _cache_dir(self) -> Path:
        return Path(os.path.dirname(__file__)) / "emoji_cache"

    def _pick_emoji_font(self) -> str:
        families = set(QFontDatabase().families())
        for f in DEFAULT_EMOJI_FONTS:
            if f in families:
                return f
        return QFont().family()

    def _emoji_png_path(self, ftype: str, emoji: str) -> str:
        safe = "".join(c for c in ftype if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        code = "_".join([f"{ord(ch):x}" for ch in emoji])
        return str(self._cache_dir() / f"{safe}_{code}.png")

    def _render_emoji_to_png(self, ftype: str, emoji: str, px: int = 256) -> str:
        path = self._emoji_png_path(ftype, emoji)
        if os.path.exists(path):
            return path

        img = QImage(px, px, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # White backing circle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        inset = int(px * 0.06)
        painter.drawEllipse(img.rect().adjusted(inset, inset, -inset, -inset))

        painter.setPen(QColor(0, 0, 0, 255))

        font_family = self._pick_emoji_font()
        font = QFont(font_family)

        pad = int(px * 0.08)
        target_rect = img.rect().adjusted(pad, pad, -pad, -pad)

        # Auto-fit multi-emoji strings
        font_px = int(px * 0.82)
        if font_px < 10:
            font_px = 10

        while font_px >= 10:
            font.setPixelSize(font_px)
            painter.setFont(font)
            fm = painter.fontMetrics()
            br = fm.boundingRect(target_rect, Qt.AlignmentFlag.AlignCenter, emoji)
            if br.width() <= target_rect.width() and br.height() <= target_rect.height():
                break
            font_px -= 2

        painter.drawText(target_rect, Qt.AlignmentFlag.AlignCenter, emoji)
        painter.end()

        img.save(path, "PNG")
        return path

    # ---------------- Layer helpers ----------------
    # Ecological feature layers are memory layers added directly to the QGIS
    # project — no GeoPackage or file dialog required. They behave the same as
    # the BNG baseline/proposed layers and are saved with the project file.

    def _ensure_eco_layers_in_tree(self):
        """Re-insert eco layers into the layer tree on project reopen.
        Layers are loaded into the registry from the project file but may not
        be visible in the tree until a new feature triggers _place_in_eco_group."""
        root = QgsProject.instance().layerTreeRoot()
        for layer in [
            self._get_existing_layer(),
            self._get_existing_line_layer(),
            self._get_existing_poly_layer(),
            self._get_existing_buffer_layer(),
        ]:
            if layer is None:
                continue
            if root.findLayer(layer.id()) is not None:
                continue  # already in tree
            self._place_in_eco_group(layer)

    def _get_existing_layer(self) -> Optional[QgsVectorLayer]:
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == LAYER_NAME:
                return lyr
        return None

    def _get_existing_line_layer(self) -> Optional[QgsVectorLayer]:
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == LINE_LAYER_NAME:
                return lyr
        return None

    def _get_existing_poly_layer(self) -> Optional[QgsVectorLayer]:
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == POLY_LAYER_NAME:
                return lyr
        return None

    def _suppress_transform_dialog(self):
        """Set OSTN15 as the preferred BNG↔Mercator transform so QGIS
        never shows the 'Select Transformation' dialog."""
        try:
            from qgis.PyQt.QtCore import QSettings
            QSettings().setValue("projections/promptWhenMultipleTransformationsExist", False)
            ctx  = QgsProject.instance().transformContext()
            bng  = QgsCoordinateReferenceSystem("EPSG:27700")
            merc = QgsCoordinateReferenceSystem("EPSG:3857")
            wgs  = QgsCoordinateReferenceSystem("EPSG:4326")
            try:
                from qgis.core import QgsDatumTransform
                for src, dst in [(bng, merc), (bng, wgs), (merc, bng), (wgs, bng)]:
                    ops = QgsDatumTransform.operations(src, dst)
                    if ops:
                        ctx.addSourceDestinationDatumTransform(src, dst, ops[0])
            except Exception:
                for src, dst in [(bng, merc), (bng, wgs), (merc, bng), (wgs, bng)]:
                    ctx.addSourceDestinationDatumTransform(src, dst, 7952)
            QgsProject.instance().setTransformContext(ctx)
        except Exception:
            pass

    def _gpkg_path(self) -> Optional[str]:
        """Return the path of the project GeoPackage, or None if project not saved."""
        project_path = QgsProject.instance().fileName()
        if not project_path:
            return None
        project_dir  = os.path.dirname(project_path)
        project_name = os.path.splitext(os.path.basename(project_path))[0]
        return os.path.join(project_dir, f"{project_name}_bng_data.gpkg")

    _ECO_GPKG_LAYERS = {
        LAYER_NAME:      ("eco_points",   "Point"),
        LINE_LAYER_NAME: ("eco_lines",    "LineString"),
        POLY_LAYER_NAME: ("eco_polygons", "Polygon"),
        BUFFER_LAYER_NAME: ("eco_buffers", "Polygon"),
    }

    def _get_existing_buffer_layer(self) -> Optional[QgsVectorLayer]:
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == BUFFER_LAYER_NAME:
                return lyr
        return None

    def _get_or_create_buffer_layer(self) -> QgsVectorLayer:
        bl = self._get_existing_buffer_layer()
        if bl is None:
            bl = self._create_eco_layer(BUFFER_LAYER_NAME, "Polygon")
            QgsProject.instance().addMapLayer(bl, False)
            self._place_in_eco_group(bl)
        self._ensure_fields(bl)
        self._apply_buffer_style(bl)
        bl.triggerRepaint()
        return bl

    def _apply_buffer_style(self, layer: QgsVectorLayer):
        """Apply a restrained sequential cartographic style to buffer bands."""
        categories = []
        distance_for_colour = {}
        if layer.fields().indexOf("buffer_color") >= 0:
            for feature in layer.getFeatures():
                colour = str(feature.attribute("buffer_color") or "").strip()
                if colour:
                    distance = self._distance_from_buffer_feature(feature)
                    if distance is not None:
                        distance_for_colour[colour] = distance

        for colour, distance in sorted(
                distance_for_colour.items(), key=lambda item: item[1]):
            sl = QgsSimpleFillSymbolLayer()
            fill_colour = QColor(colour)
            sl.setFillColor(fill_colour)
            # Equal-distance pieces are partitioned across their source
            # features. Suppressing per-feature outlines lets those pieces read
            # as one dissolved band rather than exposing internal seams.
            sl.setStrokeStyle(Qt.PenStyle.NoPen)
            sym = QgsSymbol.defaultSymbol(layer.geometryType())
            sym.changeSymbolLayer(0, sl)
            categories.append(QgsRendererCategory(
                colour, sym, f"{distance:g} m buffer"))

        # Blank colour is the fallback for non-pond and legacy buffers.
        sl = QgsSimpleFillSymbolLayer()
        sl.setFillColor(QColor(255, 200, 50))
        sl.setStrokeColor(QColor(200, 140, 0))
        sl.setStrokeWidth(0.5)
        sl.setStrokeStyle(Qt.PenStyle.DashLine)
        sym = QgsSymbol.defaultSymbol(layer.geometryType())
        sym.changeSymbolLayer(0, sl)
        categories.append(QgsRendererCategory("", sym, "Other buffers"))

        layer.setRenderer(QgsCategorizedSymbolRenderer("buffer_color", categories))
        # Slightly stronger than the old single-hue bands, while retaining the
        # underlying aerial detail needed to interpret each buffer.
        layer.setOpacity(0.50)

    def _add_buffer_feature(self, geom: QgsGeometry, label: str, ftype: str,
                            dist: float, line_side: Optional[str] = None,
                            parent_uuid: str = "",
                            parent_kind: Optional[str] = None) -> Optional[str]:
        """Compute and store a side-panel buffer around an existing feature."""
        dist_str = f"{dist:.0f}m" if dist == int(dist) else f"{dist:.1f}m"
        base_name = label if label else ftype
        buf_label = f"{base_name} {dist_str} Buffer"

        buf_geom: Optional[QgsGeometry] = None

        if line_side in ("Left only", "Right only"):
            buf_geom = self._single_sided_buffer(geom, float(dist), line_side)
        else:
            buf_geom = geom.buffer(dist, 8)

        if buf_geom is None or buf_geom.isEmpty():
            self._buf_log(f"no buffer geometry (side={line_side} dist={dist}) -> abort")
            return None

        layer = self._get_or_create_buffer_layer()
        layer.reload()
        buf_uuid = str(uuid.uuid4())
        feat = QgsFeature(layer.fields())
        feat.setGeometry(buf_geom)
        feat.setAttribute("label",   buf_label)
        feat.setAttribute("gridref", "")
        feat.setAttribute("ftype",   ftype)
        feat.setAttribute("uuid",    buf_uuid)
        feat.setAttribute("buffer_distance", float(dist))
        feat.setAttribute("buffer_parent_uuid", parent_uuid or "")
        feat.setAttribute("buffer_side", line_side or "Both sides")

        layer.startEditing()
        layer.addFeature(feat)
        layer.commitChanges()
        self._rebuild_feature_buffer_bands(
            layer, geom, label, parent_uuid or "",
            parent_kind, ftype)
        # Apply perimeter labeling to buffer layer
        self._apply_buffer_style(layer)
        self._apply_labeling(layer)
        layer.triggerRepaint()

        return buf_uuid

    @staticmethod
    def _distance_from_buffer_feature(feature) -> Optional[float]:
        """Read the stored distance, falling back to legacy buffer labels."""
        try:
            value = feature.attribute("buffer_distance")
            if value is not None and str(value) != "":
                return float(value)
        except (TypeError, ValueError, KeyError):
            pass
        match = re.search(
            r" (\d+(?:\.\d+)?)m Buffer$",
            str(feature.attribute("label") or ""))
        return float(match.group(1)) if match else None

    def _rebuild_feature_buffer_bands(self, layer: QgsVectorLayer,
                                      parent_geom: QgsGeometry, parent_label: str,
                                      parent_uuid: str, parent_kind=None,
                                      parent_ftype: str = ""):
        """Turn a feature's cumulative buffers into non-overlapping bands.

        Line bands are rebuilt separately for left, right, and both-side buffers,
        preserving the direction chosen in the side-panel buffer dialog.
        """
        groups = {}
        legacy_prefix = f"{parent_label} " if parent_label else ""
        for feature in layer.getFeatures():
            feature_parent = str(feature.attribute("buffer_parent_uuid") or "")
            same_parent = bool(parent_uuid and feature_parent == parent_uuid)
            legacy_match = (not feature_parent and legacy_prefix and
                            str(feature.attribute("label") or "").startswith(legacy_prefix)
                            and (not parent_ftype or
                                 str(feature.attribute("ftype") or "") == parent_ftype))
            if not (same_parent or legacy_match):
                continue
            distance = self._distance_from_buffer_feature(feature)
            if distance is not None:
                try:
                    side = str(feature.attribute("buffer_side") or "Both sides")
                except (KeyError, IndexError):
                    side = "Both sides"
                groups.setdefault(side, []).append((distance, feature))

        if not groups:
            return

        # A pond buffer may surround a neighbouring pond, but must never paint
        # over the pond polygon itself. Keep the exclusion geometry separate so
        # the distance bands remain measured from their own parent pond.
        other_pond_geometries = []
        if parent_kind == "area" and (parent_ftype or "").strip().lower() == "pond":
            pond_layer = self._get_existing_poly_layer()
            if pond_layer is not None:
                for pond in pond_layer.getFeatures():
                    pond_uuid = str(pond.attribute("uuid") or "")
                    pond_type = str(pond.attribute("ftype") or "").strip().lower()
                    if pond_type != "pond" or (parent_uuid and pond_uuid == parent_uuid):
                        continue
                    geometry = pond.geometry()
                    if geometry is not None and not geometry.isEmpty():
                        other_pond_geometries.append(QgsGeometry(geometry))

        layer.startEditing()
        parent_idx = layer.fields().indexOf("buffer_parent_uuid")
        distance_idx = layer.fields().indexOf("buffer_distance")
        side_idx = layer.fields().indexOf("buffer_side")
        for side, rows in groups.items():
            rows.sort(key=lambda item: item[0])
            previous = QgsGeometry(parent_geom)
            for distance, feature in rows:
                if parent_kind == "line" and side in ("Left only", "Right only"):
                    outer = self._single_sided_buffer(parent_geom, float(distance), side)
                else:
                    outer = parent_geom.buffer(float(distance), 8)
                if outer is None or outer.isEmpty():
                    continue
                ring = outer.difference(previous)
                for other_pond in other_pond_geometries:
                    if ring is None or ring.isEmpty():
                        break
                    try:
                        if ring.intersects(other_pond):
                            ring = ring.difference(other_pond)
                    except Exception:
                        pass
                if ring is not None:
                    layer.changeGeometry(feature.id(), ring)
                if parent_idx >= 0 and parent_uuid:
                    layer.changeAttributeValue(feature.id(), parent_idx, parent_uuid)
                if distance_idx >= 0:
                    layer.changeAttributeValue(feature.id(), distance_idx, float(distance))
                if side_idx >= 0:
                    layer.changeAttributeValue(feature.id(), side_idx, side)
                previous = outer
        layer.commitChanges()
        self._refresh_buffer_colours(layer)

    def _merge_touching_buffer_bands(self, layer: QgsVectorLayer):
        """Make all feature buffers a single non-overlapping distance system.

        One source record carries the dissolved display geometry for each
        distance and the other source records remain as empty placeholders.
        Their parent links still preserve independent editing and deletion, but
        QGIS sees one geometry to label instead of placing a competing label for
        every pond. Wider bands are cut around all smaller-distance bands.
        """
        grouped = {}
        for feature in layer.getFeatures():
            distance = self._distance_from_buffer_feature(feature)
            geometry = feature.geometry()
            if distance is None or geometry is None or geometry.isEmpty():
                continue
            grouped.setdefault(float(distance), []).append(
                (feature, QgsGeometry(geometry)))
        if not grouped:
            return

        covered = None
        changes = {}
        for distance in sorted(grouped):
            rows = sorted(grouped[distance], key=lambda row: row[0].id())
            geometries = [geometry for _feature, geometry in rows]
            try:
                merged = QgsGeometry.unaryUnion(geometries)
            except Exception:
                merged = QgsGeometry(geometries[0])
                for geometry in geometries[1:]:
                    merged = merged.combine(geometry)
            if merged is None or merged.isEmpty():
                continue

            band = (merged.difference(covered)
                    if covered is not None and not covered.isEmpty()
                    else QgsGeometry(merged))
            try:
                covered = (QgsGeometry.unaryUnion([covered, merged])
                           if covered is not None and not covered.isEmpty()
                           else QgsGeometry(merged))
            except Exception:
                covered = (covered.combine(merged)
                           if covered is not None else QgsGeometry(merged))

            # Keep a single multipart feature for the visible band. The empty
            # rows are deliberately retained: rebuilding restores their source
            # geometry before every move, edit or deletion operation.
            for index, (feature, _original) in enumerate(rows):
                changes[feature.id()] = (QgsGeometry(band) if index == 0
                                         else QgsGeometry())

        if not changes:
            return
        layer.startEditing()
        for fid, geometry in changes.items():
            layer.changeGeometry(fid, geometry)
        layer.commitChanges()

    def _refresh_buffer_colours(self, layer: QgsVectorLayer):
        """Give equal distances equal colours across all feature buffers."""
        colour_idx = layer.fields().indexOf("buffer_color")
        if colour_idx < 0:
            return
        buffer_rows = []
        distances = set()
        for feature in layer.getFeatures():
            distance = self._distance_from_buffer_feature(feature)
            if distance is not None:
                buffer_rows.append((feature.id(), distance))
                distances.add(distance)
        colour_for = {
            distance: BUFFER_BAND_COLOURS[index % len(BUFFER_BAND_COLOURS)]
            for index, distance in enumerate(sorted(distances))
        }
        layer.startEditing()
        for fid, distance in buffer_rows:
            layer.changeAttributeValue(fid, colour_idx, colour_for[distance])
        layer.commitChanges()

    def _refresh_all_feature_buffer_bands(self):
        """Migrate or rebuild saved buffers from their source features.

        This also keeps the remaining rings correct after a band is deleted.
        Legacy buffers are associated by their auto-generated pond label; new
        buffers use the source feature UUID.
        """
        layer = self._get_existing_buffer_layer()
        if layer is None:
            return
        self._ensure_fields(layer)
        for parent_layer, kind in (
                (self._get_existing_layer(), "point"),
                (self._get_existing_line_layer(), "line"),
                (self._get_existing_poly_layer(), "area")):
            if parent_layer is None:
                continue
            for parent in parent_layer.getFeatures():
                geom = parent.geometry()
                if geom is None or geom.isEmpty():
                    continue
                self._rebuild_feature_buffer_bands(
                    layer, geom, str(parent.attribute("label") or ""),
                    str(parent.attribute("uuid") or ""), kind,
                    str(parent.attribute("ftype") or ""))
        self._merge_touching_buffer_bands(layer)
        self._refresh_buffer_colours(layer)
        self._apply_buffer_style(layer)
        layer.triggerRepaint()

    def _buf_log(self, msg):
        """TEMP: append a single-sided-buffer diagnostic to biome_debug.log so a
        one-sided buffer that comes out wrong can be traced. Remove once happy."""
        try:
            import os as _os
            with open(_os.path.join(_os.path.dirname(__file__), "biome_debug.log"),
                      "a", encoding="utf-8") as f:
                f.write("[buffer] " + str(msg) + "\n")
        except Exception:
            pass

    def _single_sided_buffer(self, geom: QgsGeometry, dist: float,
                             line_side: str) -> Optional[QgsGeometry]:
        """Buffer a line on ONE side only ('Left only' / 'Right only', relative to
        the line's drawn direction). Uses QgsGeometry.singleSidedBuffer (the
        proper API); falls back to offset-curve + intersection, then to a plain
        symmetric buffer. Every step is logged so a bad result can be diagnosed."""
        want_left = (line_side == "Left only")
        try:
            nverts = len(geom.asPolyline()) if geom else 0
        except Exception:
            nverts = -1
        self._buf_log(f"single-sided START side={line_side} dist={dist} "
                      f"wkb={geom.wkbType() if geom else 'None'} "
                      f"verts={nverts} valid={geom.isGeosValid() if geom else 'n/a'}")

        # 1) The proper API: singleSidedBuffer.
        try:
            side = 0 if want_left else 1
            try:
                from qgis.core import Qgis
                side = (Qgis.BufferSide.Left if want_left
                        else Qgis.BufferSide.Right)
            except Exception:
                pass  # older builds take a plain int (0=left, 1=right)
            g = geom.singleSidedBuffer(float(dist), 8, side)
            ok = g is not None and not g.isNull() and not g.isEmpty()
            self._buf_log(f"  singleSidedBuffer: ok={ok} "
                          f"area={g.area() if ok else 'n/a'} "
                          f"valid={g.isGeosValid() if ok else 'n/a'}")
            if ok and not g.isGeosValid():
                g = g.makeValid()
                self._buf_log(f"  makeValid: empty={g.isEmpty()} area={g.area()}")
            if g is not None and not g.isEmpty():
                return g
        except Exception as ex:
            self._buf_log(f"  singleSidedBuffer EXC: {ex!r}")

        # 2) Fallback: offset the line to the chosen side, then take the strip
        #    between the original and the offset (intersection of both buffers).
        try:
            signed = dist if want_left else -dist
            offset = geom.offsetCurve(signed, 8, 1, 5.0)
            if offset is not None and not offset.isEmpty():
                strip = geom.buffer(dist, 8).intersection(offset.buffer(dist, 8))
                ok = strip is not None and not strip.isEmpty()
                self._buf_log(f"  fallback offset+intersect: ok={ok} "
                              f"area={strip.area() if ok else 'n/a'}")
                if ok:
                    return strip
            else:
                self._buf_log("  fallback offsetCurve returned empty")
        except Exception as ex:
            self._buf_log(f"  fallback EXC: {ex!r}")

        # 3) Last resort: symmetric buffer so the user still gets *something*.
        self._buf_log("  -> symmetric buffer (single-sided unavailable)")
        return geom.buffer(dist, 8)

    def _create_gpkg_layer(self, layer_name: str, wkb_type_str: str, gpkg_path: str) -> QgsVectorLayer:
        """Create or open an ecological feature layer backed by a GeoPackage table."""
        gpkg_table = self._ECO_GPKG_LAYERS[layer_name][0]

        # If the GPKG already exists, ALWAYS try to open from it — never overwrite
        if os.path.exists(gpkg_path):
            uri  = f"{gpkg_path}|layername={gpkg_table}"
            test = QgsVectorLayer(uri, layer_name, "ogr")
            if test.isValid():
                return test
            # Table doesn't exist yet in this GPKG — fall through to create it
            # But only if we can confirm the table is genuinely absent, not corrupt
            import subprocess, sys
            # Use ogrinfo to list tables — if gpkg_table is listed, data exists, don't overwrite
            try:
                import osgeo.ogr as ogr
                ds = ogr.Open(gpkg_path)
                if ds is not None:
                    existing_tables = [ds.GetLayerByIndex(i).GetName()
                                       for i in range(ds.GetLayerCount())]
                    ds = None
                    if gpkg_table in existing_tables:
                        # Table exists but layer failed to load — don't overwrite, raise
                        raise RuntimeError(
                            f"GPKG table '{gpkg_table}' exists but could not be loaded. "
                            f"The file may be locked or corrupted: {gpkg_path}"
                        )
            except ImportError:
                pass  # osgeo not available — fall through cautiously

        # GPKG doesn't exist yet, or the specific table is genuinely absent — create it
        schema_uri = f"{wkb_type_str}?crs=EPSG:27700"
        schema_layer = QgsVectorLayer(schema_uri, layer_name, "memory")
        if not schema_layer.isValid():
            raise RuntimeError(f"Failed to build schema layer for '{layer_name}'.")
        pr = schema_layer.dataProvider()
        pr.addAttributes([
            QgsField("label",   QMetaType.Type.QString),
            QgsField("gridref", QMetaType.Type.QString),
            QgsField("ftype",   QMetaType.Type.QString),
            QgsField("uuid",    QMetaType.Type.QString),
        ])
        schema_layer.updateFields()

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName  = "GPKG"
        options.layerName   = gpkg_table
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            if os.path.exists(gpkg_path)
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        )

        error, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
            schema_layer, gpkg_path,
            QgsProject.instance().transformContext(),
            options,
        )
        if error != QgsVectorFileWriter.WriterError.NoError:
            raise RuntimeError(f"Failed to create GPKG layer '{layer_name}': {msg}")

        uri   = f"{gpkg_path}|layername={gpkg_table}"
        layer = QgsVectorLayer(uri, layer_name, "ogr")
        if not layer.isValid():
            raise RuntimeError(f"GPKG layer '{layer_name}' invalid after creation.")
        # Writing a NEW table into the shared project GeoPackage stales the OGR
        # layers already open on that same file (their features vanish until
        # reloaded). Reload the siblings so, e.g., creating eco_buffers on the
        # first buffer doesn't blank the points/lines/areas already placed.
        self._reload_gpkg_siblings(gpkg_path, exclude=layer)
        return layer

    def _reload_gpkg_siblings(self, gpkg_path, exclude=None):
        """Reload every project layer backed by *gpkg_path* (except *exclude*),
        so a fresh table write doesn't leave sibling OGR layers showing empty."""
        try:
            import os as _os
            target = _os.path.normcase(_os.path.abspath(gpkg_path))
            for lyr in QgsProject.instance().mapLayers().values():
                if not isinstance(lyr, QgsVectorLayer):
                    continue
                if exclude is not None and lyr.id() == exclude.id():
                    continue
                path = (lyr.source() or "").split("|")[0]
                if not path:
                    continue
                if _os.path.normcase(_os.path.abspath(path)) == target:
                    lyr.reload()
                    lyr.triggerRepaint()
        except Exception:
            pass

    def _create_scratch_layer(self, layer_name: str, wkb_type_str: str) -> QgsVectorLayer:
        """Create a memory-backed ('just looking') ecological feature layer.

        Holds the same base fields as the GeoPackage version but lives only in
        memory, so it is lost when the project closes. Used when the user has
        chosen to continue without saving."""
        uri = f"{wkb_type_str}?crs=EPSG:27700"
        layer = QgsVectorLayer(uri, layer_name, "memory")
        if not layer.isValid():
            raise RuntimeError(f"Failed to build scratch layer for '{layer_name}'.")
        pr = layer.dataProvider()
        pr.addAttributes([
            QgsField("label",   QMetaType.Type.QString),
            QgsField("gridref", QMetaType.Type.QString),
            QgsField("ftype",   QMetaType.Type.QString),
            QgsField("uuid",    QMetaType.Type.QString),
        ])
        layer.updateFields()
        # Avoid the 'memory layers will not be saved' warning on project save,
        # and tag it so other code can recognise it as a throwaway layer.
        layer.setCustomProperty("skipMemoryLayersCheck", 1)
        layer.setCustomProperty("biome_scratch", 1)
        # Record the GeoPackage table so the layer is migrated to disk when the
        # project is saved.
        layer.setCustomProperty("biome_gpkg_table", self._ECO_GPKG_LAYERS[layer_name][0])
        return layer

    def _create_eco_layer(self, layer_name: str, wkb_type_str: str) -> QgsVectorLayer:
        """Create an ecological feature layer, GeoPackage-backed when the project
        is saved, otherwise memory-backed ('just looking')."""
        gpkg = self._gpkg_path()
        if gpkg:
            return self._create_gpkg_layer(layer_name, wkb_type_str, gpkg)
        return self._create_scratch_layer(layer_name, wkb_type_str)

    def _is_scratch_mode(self) -> bool:
        """Whether the user has chosen 'Just looking' this session.

        Reads the shared flag on the panel's LayerManager when this dialog is
        bridged to the panel, so the choice is consistent with redline/habitat
        placement and the prompt only appears once. Falls back to the local
        flag when no panel is attached."""
        lm = getattr(self._main_panel, "layer_manager", None) if self._main_panel else None
        if lm is not None:
            return bool(getattr(lm, "scratch_mode", False))
        return self._scratch_mode

    def _set_scratch_mode(self, value: bool) -> None:
        """Record the 'Just looking' choice on the shared flag (when bridged)
        and the local flag, so it persists for the rest of the session."""
        self._scratch_mode = value
        lm = getattr(self._main_panel, "layer_manager", None) if self._main_panel else None
        if lm is not None:
            lm.scratch_mode = value

    def _require_saved_project(self) -> bool:
        """Return True if features can be placed.

        True when the project is saved (GeoPackage available) OR the user has
        chosen "Just looking" (memory-backed scratch layers). False only when
        the user cancels. Offers inline Save / Just looking buttons."""
        if self._gpkg_path():
            return True
        if self._is_scratch_mode():
            return True  # user already chose 'just looking' this session
        from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
        from qgis.PyQt.QtGui import QFont
        from qgis.PyQt.QtCore import Qt

        dlg = QDialog(self)
        dlg.setWindowTitle("Save project first")
        dlg.setMinimumWidth(440)
        dlg.setStyleSheet("background:#FBF6E6;")

        vl = QVBoxLayout(dlg)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 16)

        icon_lbl = QLabel("💾")
        icon_lbl.setStyleSheet("font-size:32px; background:transparent;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(icon_lbl)

        msg = QLabel(
            "<b>Project not saved yet.</b><br><br>"
            "Features are stored in a GeoPackage alongside the project file. "
            "Save the project first so nothing is lost on exit.<br><br>"
            "Save the QGIS project as either <b>.qgs</b> or <b>.qgz</b>.<br><br>"
            "Or, if you are just having a look, you can continue without "
            "saving — but anything you place will be <b>lost when you close "
            "the project</b>."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#2A332D; font-size:11px; background:transparent;")
        vl.addWidget(msg)

        btns = QHBoxLayout()
        btns.setSpacing(8)

        save_btn = QPushButton("💾  Save project now…")
        save_btn.setStyleSheet(
            "QPushButton{background:#B07D00; color:white; border:none;"
            " border-radius:5px; padding:7px 16px; font-weight:bold; font-size:11px;}"
            "QPushButton:hover{background:#9A6700;}"
        )
        scratch_btn = QPushButton("👀  Just looking")
        scratch_btn.setToolTip(
            "Continue without saving. Layers are kept in memory only and "
            "are lost when the project is closed."
        )
        scratch_btn.setStyleSheet(
            "QPushButton{background:#5c6bc0; color:white; border:none;"
            " border-radius:5px; padding:7px 16px; font-weight:500; font-size:11px;}"
            "QPushButton:hover{background:#3949ab;}"
        )
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "QPushButton{background:#eee; color:#5F6F65; border:none;"
            " border-radius:5px; padding:7px 16px; font-size:11px;}"
            "QPushButton:hover{background:#DDE2DC;}"
        )

        btns.addWidget(save_btn)
        btns.addWidget(scratch_btn)
        btns.addWidget(cancel_btn)
        vl.addLayout(btns)

        saved = [False]

        def _do_save():
            dlg.accept()  # dismiss our dialog first
            self.iface.actionSaveProjectAs().trigger()
            # After the native Save As completes, check if project now has a path
            if self._gpkg_path():
                saved[0] = True

        def _do_scratch():
            self._set_scratch_mode(True)
            saved[0] = True
            dlg.accept()

        save_btn.clicked.connect(_do_save)
        scratch_btn.clicked.connect(_do_scratch)
        cancel_btn.clicked.connect(dlg.reject)

        dlg.exec()
        return saved[0]

    def _get_or_create_layer(self) -> QgsVectorLayer:
        pt = self._get_existing_layer()
        if pt is None:
            pt = self._create_eco_layer(LAYER_NAME, "Point")
            self._suppress_transform_dialog()
            QgsProject.instance().addMapLayer(pt, False)
            self._place_in_eco_group(pt)
        self._ensure_fields(pt)
        self._apply_style(pt)
        pt.triggerRepaint()
        return pt

    def _get_or_create_line_layer(self) -> QgsVectorLayer:
        ln = self._get_existing_line_layer()
        if ln is None:
            ln = self._create_eco_layer(LINE_LAYER_NAME, "LineString")
            QgsProject.instance().addMapLayer(ln, False)
            self._place_in_eco_group(ln)
        self._ensure_fields(ln)
        self._apply_style(ln)
        ln.triggerRepaint()
        return ln

    def _get_or_create_poly_layer(self) -> QgsVectorLayer:
        pl = self._get_existing_poly_layer()
        if pl is None:
            pl = self._create_eco_layer(POLY_LAYER_NAME, "Polygon")
            QgsProject.instance().addMapLayer(pl, False)
            self._place_in_eco_group(pl)
        self._ensure_fields(pl)
        self._apply_style(pl)
        pl.triggerRepaint()
        return pl

    def _place_in_eco_group(self, layer):
        """Place a layer inside the 'Ecological Features' group under the correct
        sub-group ('Area Features', 'Point Features', or 'Linear Features'),
        creating groups as needed. The top-level group is always at position 0."""
        root = QgsProject.instance().layerTreeRoot()

        eco_group = root.findGroup("Ecological Features")
        if eco_group is None:
            eco_group = root.insertGroup(0, "Ecological Features")

        from qgis.core import QgsWkbTypes as _WKB
        geom_type = layer.geometryType()
        if layer.name() == BUFFER_LAYER_NAME:
            sub_name = "Buffer Zones"
            sub_idx  = 3
        elif geom_type == _WKB.GeometryType.PolygonGeometry:
            sub_name = "Area Features"
            sub_idx  = 0
        elif geom_type == _WKB.GeometryType.LineGeometry:
            sub_name = "Linear Features"
            sub_idx  = 1
        else:
            sub_name = "Point Features"
            sub_idx  = 2

        sub_group = eco_group.findGroup(sub_name)
        if sub_group is None:
            sub_group = eco_group.insertGroup(sub_idx, sub_name)

        existing_ids = {n.layerId() for n in sub_group.findLayers()}
        if layer.id() not in existing_ids:
            node = sub_group.addLayer(layer)
            if node:
                node.setItemVisibilityChecked(True)

        eco_group.setExpanded(True)
        sub_group.setExpanded(True)
        _apply_tree_collapse(root)

    def _ensure_fields(self, layer: QgsVectorLayer):
        needed = [
            QgsField("label",           QMetaType.Type.QString),
            QgsField("gridref",         QMetaType.Type.QString),
            QgsField("ftype",           QMetaType.Type.QString),
            QgsField("uuid",            QMetaType.Type.QString),
            QgsField("photo_path",      QMetaType.Type.QString),
            QgsField("field_survey_id", QMetaType.Type.QString),
            QgsField("source",          QMetaType.Type.QString),
            QgsField("fov_color",       QMetaType.Type.QString),
            # Compass bearing (degrees, 0 = north, clockwise) for arrow-style
            # directional points — see ARROW_DIRECTIONAL_TYPES. Added here (not
            # only at layer creation) so eco layers in projects made before the
            # arrow types existed pick the field up on open.
            QgsField("bearing",         QMetaType.Type.Double),
            # Per-feature label controls (Edit menu on the "On the map" rail):
            # hide_label 1=hidden (default show); label_curved 0=straight (default curved).
            QgsField("hide_label",      QMetaType.Type.Int),
            QgsField("label_curved",    QMetaType.Type.Int),
            # Buffer metadata used to rebuild ordered, non-overlapping bands for
            # points, lines, and areas and style equal distances consistently.
            QgsField("buffer_distance", QMetaType.Type.Double),
            QgsField("buffer_parent_uuid", QMetaType.Type.QString),
            QgsField("buffer_color",    QMetaType.Type.QString),
            QgsField("buffer_side",     QMetaType.Type.QString),
        ]
        existing = {f.name() for f in layer.fields()}
        to_add = [f for f in needed if f.name() not in existing]
        if not to_add:
            return

        layer.startEditing()
        ok = layer.dataProvider().addAttributes(to_add)
        layer.updateFields()
        layer.commitChanges()
        layer.reload()
        if not ok:
            raise RuntimeError("Failed to add required fields to the layer.")

    # ---------------- Labeling ----------------
    def _labels_hidden(self) -> bool:
        """Master 'all labels off' state (project-stored; see fov_label_arcs)."""
        from . import fov_label_arcs as FLA
        return FLA.labels_hidden()

    def _on_labels_toggled(self, checked):
        """The rail-header 'Labels' toggle: checked = labels shown. Applies to
        the straight labels on all four eco layers and the curved arc layer,
        persists with the project, and is honoured by exports and the global
        zoom-driven arc rebuild."""
        try:
            QgsProject.instance().writeEntry(
                "biome", "eco_labels_hidden", not checked)
        except Exception:
            pass
        if hasattr(self, "_labels_btn"):
            self._labels_btn.setText(
                "All labels on" if checked else "All labels off")
        for getter in (self._get_existing_layer, self._get_existing_line_layer,
                       self._get_existing_poly_layer,
                       self._get_existing_buffer_layer):
            try:
                lyr = getter()
                if lyr is not None:
                    lyr.setLabelsEnabled(checked)
                    lyr.triggerRepaint()
            except Exception:
                pass
        # Rebuilds the curved refs when switching on; clears them when off.
        self._do_refresh_fov_arcs()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText(
                "Labels shown" if checked else "Labels hidden")

    def showEvent(self, event):
        """Re-stamp existing eco layers with the current labeling rules when the
        dialog opens, so a map built before a labeling change picks up the new
        behaviour (outside-polygon placement, leader lines, show-all) without the
        user having to nudge a style control.

        The actual re-stamp is DEFERRED to the idle loop (and runs once): doing
        labeling changes + triggerRepaint synchronously inside showEvent re-enters
        the canvas / legend / layer-overlay while they are mid-render, which
        crashes when a saved project already has eco layers loaded."""
        super().showEvent(event)
        # The scroll viewport width is only reliable after Qt has completed the
        # first layout pass.  Trim the dialog to a whole number of swatch
        # columns so there is no dead strip between the final column and the
        # vertical scroll bar.
        QTimer.singleShot(0, self._fit_dialog_to_swatch_grid)
        # Watch the canvas tool so the primary button can mirror the armed
        # state (→ "■ Stop placing"). Connected while shown; torn down in
        # closeEvent so nothing fires into a closed dialog.
        if not getattr(self, "_maptool_watch_on", False):
            try:
                self.canvas.mapToolSet.connect(self._schedule_place_btn_refresh)
                self._maptool_watch_on = True
            except Exception:
                pass
        # Re-sync the master Labels toggle with the (project-stored) state —
        # the project may have changed since the dialog was last shown.
        if hasattr(self, "_labels_btn"):
            shown = not self._labels_hidden()
            if self._labels_btn.isChecked() != shown:
                self._labels_btn.blockSignals(True)
                self._labels_btn.setChecked(shown)
                self._labels_btn.blockSignals(False)
            self._labels_btn.setText(
                "All labels on" if shown else "All labels off")
        if not getattr(self, "_labeling_refreshed", False):
            self._labeling_refreshed = True
            QTimer.singleShot(0, self._refresh_existing_labeling)

    def _fit_dialog_to_swatch_grid(self):
        """Trim spare picker width without changing the number of columns."""
        try:
            picker = self._picker_stack.currentWidget()
            scroll = picker.currentWidget() if isinstance(picker, QTabWidget) else picker
            if not isinstance(scroll, _FlowScroll):
                return

            flow = scroll.widget().layout()
            if flow is None or flow.count() == 0:
                return
            tile = flow.itemAt(0).widget()
            if tile is None:
                return

            margins = flow.contentsMargins()
            spacing = max(0, flow.spacing())
            tile_width = tile.width() or tile.sizeHint().width()
            viewport_width = scroll.viewport().width()
            usable_width = viewport_width - margins.left() - margins.right()
            column_width = tile_width + spacing
            columns = max(1, (usable_width + spacing) // column_width)
            fitted_width = (margins.left() + margins.right()
                            + columns * tile_width
                            + (columns - 1) * spacing)
            spare_width = viewport_width - fitted_width

            # Leave tiny style/frame rounding differences alone.  A larger
            # remainder is the visible blank strip this method is intended to
            # remove.
            if 3 < spare_width < column_width:
                self.resize(max(self.minimumWidth(), self.width() - spare_width),
                            self.height())
        except Exception:
            # Sizing is cosmetic and must never prevent the mapper opening.
            pass

    def _refresh_existing_labeling(self):
        """Apply current labeling to any eco layers already in the project. Runs
        deferred from showEvent so it never fires during a canvas render."""
        for getter in (self._get_existing_layer, self._get_existing_line_layer,
                       self._get_existing_poly_layer,
                       self._get_existing_buffer_layer):
            try:
                lyr = getter()
                if lyr is not None:
                    self._ensure_fields(lyr)   # add hide_label/label_curved if missing
                    self._apply_labeling(lyr)
                    lyr.triggerRepaint()
            except Exception:
                pass
        # Upgrade cumulative feature buffers saved by earlier versions in place.
        try:
            self._refresh_all_feature_buffer_bands()
        except Exception:
            pass
        # Build the curved FOV-label arcs once on open. Keeping them sized to the
        # view on zoom is handled globally by the plugin (bng_plugin), so it works
        # whether or not this dialog is open.
        self._do_refresh_fov_arcs()

    def _connect_arc_zoom(self):
        """Rebuild the FOV-label arcs (debounced) whenever the map scale changes,
        so the screen-constant arcs stay the right size and the curved text never
        gets too big/small to fit. Connected once; torn down in closeEvent."""
        if getattr(self, "_arc_zoom_connected", False):
            return
        self._arc_zoom_connected = True
        self._arc_timer = QTimer(self)
        self._arc_timer.setSingleShot(True)
        self._arc_timer.setInterval(120)
        self._arc_timer.timeout.connect(self._do_refresh_fov_arcs)
        self._arc_zoom_slot = lambda *_: self._arc_timer.start()
        try:
            self.canvas.scaleChanged.connect(self._arc_zoom_slot)
        except Exception:
            pass

    def _do_refresh_fov_arcs(self):
        """Rebuild the curved-label arc layer (FOV cones + curved points) at the
        current map scale. Per-feature flags control what appears. Never raises."""
        try:
            from . import fov_label_arcs as FLA
        except Exception:
            return
        try:
            FLA.refresh(self.canvas, float(self._icon_size_mm))
        except Exception:
            pass

    def _apply_labeling(self, layer: QgsVectorLayer):
        # Label visibility is now per-feature (Edit menu on the "On the map"
        # rail), driven by the hide_label / label_curved fields via the data-
        # defined Show expression built below.
        pal = QgsPalLayerSettings()
        pal.fieldName = "label"
        pal.enabled = True

        geom_type = layer.geometryType()
        pal.offsetUnits = QgsUnitTypes.RenderUnit.RenderMillimeters

        is_buffer_layer = (layer.name() == BUFFER_LAYER_NAME)

        if is_buffer_layer:
            # Merged buffers belong to every source they touch, so a pond name
            # is misleading. Label the shared band by distance only. Converting
            # each connected polygon part to one interior point prevents QGIS
            # from repeating the text around long or irregular perimeters.
            pal.fieldName = (
                "format_number(\"buffer_distance\", "
                "CASE WHEN \"buffer_distance\" = round(\"buffer_distance\") "
                "THEN 0 ELSE 2 END) || 'm'")
            pal.isExpression = True
            try:
                pal.labelPerPart = True
            except Exception:
                pass
            try:
                pal.geometryGeneratorEnabled = True
                pal.geometryGeneratorType = QgsWkbTypes.GeometryType.PointGeometry
                pal.geometryGenerator = (
                    "collect_geometries(array_foreach("
                    "geometries_to_array($geometry),"
                    "with_variable('axis_origin',centroid(@element),"
                    "centroid(intersection(@element,make_line("
                    "@axis_origin,project(@axis_origin,100000,radians(45))))))"
                    "))")
            except Exception:
                pass

        if geom_type == QgsWkbTypes.GeometryType.LineGeometry:
            pal.placement = QgsPalLayerSettings.Placement.Line
        elif is_buffer_layer:
            # The geometry generator above supplies one fixed point within the
            # middle of each connected band component.
            try:
                pal.placement = QgsPalLayerSettings.Placement.OverPoint
            except AttributeError:
                pass
            pal.dist = 0
        elif geom_type == QgsWkbTypes.GeometryType.PolygonGeometry:
            # Area-feature labels belong in the middle of their polygon. Keeping
            # centroidInside enabled gives concave areas a point on the visible
            # surface instead of allowing a geometric centroid outside the shape.
            pal.placement = QgsPalLayerSettings.Placement.OverPoint
            try:
                pal.centroidInside = True
                pal.fitInPolygonOnly = True
            except Exception:
                pass
        else:
            # Points: float the label around the marker so the engine can fan a
            # cluster out; a callout keeps each label tied to its icon.
            try:
                pal.placement = QgsPalLayerSettings.Placement.AroundPoint
            except AttributeError:
                pal.placement = QgsPalLayerSettings.Placement.OverPoint

            # Push point labels off the marker so the icon isn't obscured. The gap
            # scales with the icon size.
            if geom_type == QgsWkbTypes.GeometryType.PointGeometry:
                pal.dist = max(2.0, float(self._icon_size_mm) * 0.7)
                try:
                    pal.distUnits = QgsUnitTypes.RenderUnit.RenderMillimeters
                except Exception:
                    pass

        # ── Collision avoidance ───────────────────────────────────────────
        # Allow the QGIS label placement engine to move labels to avoid overlap.
        # We want labels to be able to move freely (not pinned to a fixed offset)
        # while the icons themselves stay put. Setting obstacle=True on the layer
        # means other labels treat features as obstacles. upsidedownLabels and
        # the placement mode together let the engine find the best free position.
        try:
            # Treat the feature geometry as an obstacle so labels route around it
            pal.obstacle = True
            pal.obstacleFactor = 1.0
        except Exception:
            pass
        try:
            # Lower priority = engine tries harder to place without overlap
            pal.priority = 5
        except Exception:
            pass
        try:
            # Show EVERY label even when the engine can't fully de-conflict them.
            # Without this, zooming out crowds the cones/points together on
            # screen, the engine runs out of clash-free slots, and it silently
            # drops the losing labels — so refs vanish at some zoom levels. With
            # displayAll the around-point placement + obstacles still pick the
            # tidiest available slot; nothing is culled.
            pal.displayAll = True
        except Exception:
            pass

        # ── Leader lines (callouts) ───────────────────────────────────────
        # Point labels may move around their marker to avoid collisions, so retain
        # a callout for points only. Area labels are centred within their polygon
        # and no longer need an edge-to-label leader line.
        if geom_type == QgsWkbTypes.GeometryType.PointGeometry:
            try:
                from qgis.core import QgsSimpleLineCallout, QgsCallout
                callout = QgsSimpleLineCallout()
                callout.setEnabled(True)
                try:
                    # Anchor the line to a point inside the cone (near its icon)
                    # rather than the nearest edge, so it clearly reads "this one".
                    callout.setAnchorPoint(QgsCallout.AnchorPoint.PointOnSurface)
                except Exception:
                    pass
                try:
                    # Build a FRESH line symbol and hand it over once. Never
                    # mutate the symbol returned by lineSymbol() and re-set it:
                    # setLineSymbol deletes the existing symbol, which would be
                    # the very object just mutated -> use-after-free -> segfault.
                    from qgis.core import QgsLineSymbol
                    sym = QgsLineSymbol.createSimple(
                        {"line_color": "80,80,80", "line_width": "0.3"})
                    callout.setLineSymbol(sym)
                except Exception:
                    pass
                try:
                    # No stub line when the label already sits on the feature.
                    callout.setMinimumLength(1.0)
                    callout.setMinimumLengthUnit(QgsUnitTypes.RenderUnit.RenderMillimeters)
                except Exception:
                    pass
                pal.setCallout(callout)
            except Exception:
                pass

        fmt = QgsTextFormat()
        if is_buffer_layer:
            fmt.setSize(7.5)
            fmt.setColor(QColor("#164A43"))
        else:
            fmt.setSize(max(8.0, float(self._icon_size_mm) * 1.25))
            fmt.setColor(QColor(0, 0, 0))

        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setColor(QColor(255, 255, 255))
        buf.setSize(1.1 if is_buffer_layer else
                    max(1.0, float(self._icon_size_mm) * 0.22))
        fmt.setBuffer(buf)

        # Tint each FOV-wedge label with its own cone colour so a label can be
        # matched back to its cone when fields of view overlap. Only features
        # carrying fov_color are affected (a slightly darkened hue keeps it
        # legible on the white buffer); everything else stays black. Guarded so
        # any layer without the field is left untouched.
        if layer.fields().indexOf("fov_color") >= 0:
            try:
                from qgis.core import QgsProperty
                ddp = pal.dataDefinedProperties()
                ddp.setProperty(
                    QgsPalLayerSettings.Property.Color,
                    QgsProperty.fromExpression(
                        "if(coalesce(\"fov_color\",'')='',"
                        "'#000000',darker(\"fov_color\",125))"
                    ),
                )
                pal.setDataDefinedProperties(ddp)
            except Exception:
                # API/enum mismatch just leaves labels their default black.
                pass

        # Per-feature label visibility (Edit menu on the "On the map" rail). A
        # feature's OWN label shows only when it is visible AND not carried as a
        # curved arc label: points and FOV wedges default to curved (the arc layer
        # draws them), so their own label appears only when switched to straight;
        # everything else (lines, plain areas, buffers) shows whenever visible.
        try:
            from qgis.core import QgsProperty
            visible = "coalesce(\"hide_label\",0)=0"
            if geom_type == QgsWkbTypes.GeometryType.PointGeometry:
                show_expr = f"{visible} AND coalesce(\"label_curved\",1)=0"
            elif (geom_type == QgsWkbTypes.GeometryType.PolygonGeometry
                  and not is_buffer_layer):
                show_expr = (f"{visible} AND (NOT (\"ftype\" LIKE '%Field of View%')"
                             f" OR coalesce(\"label_curved\",1)=0)")
            else:
                show_expr = visible
            ddp = pal.dataDefinedProperties()
            ddp.setProperty(QgsPalLayerSettings.Property.Show,
                            QgsProperty.fromExpression(show_expr))
            pal.setDataDefinedProperties(ddp)
        except Exception:
            pass

        pal.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        # The master 'Labels' toggle gates the whole layer; the per-feature
        # hide_label flag is already baked into the Show expression above.
        layer.setLabelsEnabled(not self._labels_hidden())



    # ---------------- Renderer ----------------
    def _symbol_svg_path(self, ftype: str) -> Optional[str]:
        fname = FEATURE_SVG.get(ftype)
        if not fname:
            return None
        # Custom point types store an absolute path to an SVG in the profile
        # folder; built-ins store a bare filename resolved against Symbols/.
        if os.path.isabs(fname):
            return fname if os.path.exists(fname) else None
        base = os.path.dirname(__file__)
        for folder in ("Symbols", "symbols"):
            p = os.path.join(base, folder, fname)
            if os.path.exists(p):
                return p
        return None

    def _make_symbol(self, ftype: str) -> QgsMarkerSymbol:
        symbol = QgsMarkerSymbol.createSimple({})
        symbol.deleteSymbolLayer(0)

        svg_path = self._symbol_svg_path(ftype)
        if not svg_path:
            # fallback to emoji if SVG missing
            emoji = FEATURE_EMOJI.get(ftype, "📍")
            png_path = self._render_emoji_to_png(ftype, emoji)
            raster = QgsRasterMarkerSymbolLayer(png_path)
            raster.setSize(float(self._icon_size_mm))
            symbol.appendSymbolLayer(raster)
            return symbol

        svg_layer = QgsSvgMarkerSymbolLayer(svg_path)
        svg_layer.setSize(float(self._icon_size_mm))
        self._apply_bearing_rotation(svg_layer, ftype)
        symbol.appendSymbolLayer(svg_layer)
        return symbol

    @staticmethod
    def _apply_bearing_rotation(marker_layer, ftype: str) -> None:
        """Rotate an arrow type's marker to the bearing stored on the feature.

        The SVG is drawn pointing north, so the feature's "bearing" (degrees
        clockwise from north) is the marker angle directly. Data-defined, so a
        feature-less legend swatch falls back to the static angle and the key
        shows the arrow pointing straight up."""
        if ftype not in ARROW_DIRECTIONAL_TYPES:
            return
        try:
            from qgis.core import QgsProperty, QgsSymbolLayer
            marker_layer.setDataDefinedProperty(
                QgsSymbolLayer.Property.PropertyAngle,
                QgsProperty.fromExpression('coalesce("bearing", 0)'),
            )
        except Exception:
            # Any API/enum mismatch just leaves the arrow unrotated rather than
            # losing the marker entirely.
            pass

    def _apply_categorized_renderer(self, layer: QgsVectorLayer):
        categories = []
        geom_type = layer.geometryType()

        _style_map = {
            "solid":    Qt.PenStyle.SolidLine,
            "dash":     Qt.PenStyle.DashLine,
            "dot":      Qt.PenStyle.DotLine,
            "dash dot": Qt.PenStyle.DashDotLine,
        }

        if geom_type == QgsWkbTypes.GeometryType.PointGeometry:
            pt_names = [name for _, names in FEATURE_GROUPS_FLAT for name in names]
            pt_names += list(self._custom_store.get("points", {}).keys())  # customs
            seen = set()
            for ftype in pt_names:
                if ftype in seen:
                    continue
                seen.add(ftype)
                sym = self._make_symbol(ftype)
                categories.append(QgsRendererCategory(ftype, sym, ftype))

        elif geom_type == QgsWkbTypes.GeometryType.LineGeometry:
            for ftype, props in LINE_FEATURES.items():
                color     = QColor(props.get("color", "#C97B16"))
                width     = float(props.get("width", "0.6"))
                pen_style = _style_map.get(props.get("style", "solid"), Qt.PenStyle.SolidLine)
                line_sl   = QgsSimpleLineSymbolLayer()
                line_sl.setColor(color)
                line_sl.setWidth(width)
                line_sl.setPenStyle(pen_style)
                sym = QgsSymbol.defaultSymbol(geom_type)
                sym.changeSymbolLayer(0, line_sl)
                categories.append(QgsRendererCategory(ftype, sym, ftype))

        else:  # Polygon
            for ftype, props in AREA_FEATURES.items():
                fill_col   = QColor(props.get("fill",   "#44aa44"))
                border_col = QColor(props.get("border", "#228822"))
                opacity    = float(props.get("opacity", "0.30"))
                pen_style  = _style_map.get(props.get("style", "solid"), Qt.PenStyle.SolidLine)

                fill_sl = QgsSimpleFillSymbolLayer()
                fill_col_alpha = QColor(fill_col)
                fill_col_alpha.setAlphaF(opacity)
                fill_sl.setFillColor(fill_col_alpha)
                fill_sl.setStrokeColor(border_col)
                fill_sl.setStrokeWidth(0.5)
                fill_sl.setStrokeStyle(pen_style)

                # FOV wedges carry a per-feature colour (fov_color) so each
                # observer/surveyor/camera placed gets its own cone and
                # overlapping fields of view stay tellable-apart. Data-defined
                # overrides read that colour (re-applying this category's
                # opacity, and darkening it for the outline), falling back to the
                # category default for wedges with no stored colour — legacy
                # built-ins and custom survey positions that keep their own hue.
                if ftype in SINGLE_OBJECT_WEDGE_SOURCE:
                    try:
                        from qgis.core import QgsProperty, QgsSymbolLayer
                        a255 = max(0, min(255, int(round(opacity * 255))))
                        fill_sl.setDataDefinedProperty(
                            QgsSymbolLayer.Property.PropertyFillColor,
                            QgsProperty.fromExpression(
                                "set_color_part("
                                "if(coalesce(\"fov_color\",'')='','%s',\"fov_color\"),"
                                "'alpha',%d)" % (fill_col.name(), a255)
                            ),
                        )
                        fill_sl.setDataDefinedProperty(
                            QgsSymbolLayer.Property.PropertyStrokeColor,
                            QgsProperty.fromExpression(
                                "if(coalesce(\"fov_color\",'')='',"
                                "'%s',darker(\"fov_color\",160))" % border_col.name()
                            ),
                        )
                    except Exception:
                        # Any API/enum mismatch just leaves the static category
                        # colour in place — wedges still render, all one hue.
                        pass

                sym = QgsSymbol.defaultSymbol(geom_type)
                sym.changeSymbolLayer(0, fill_sl)

                # For single-object directional types (e.g. Observer Station) the
                # wedge polygon IS the whole feature, so draw the station icon at
                # the wedge apex (vertex 1) using a geometry generator. This keeps
                # the point + FOV as one object rather than two.
                src_type = SINGLE_OBJECT_WEDGE_SOURCE.get(ftype)
                if src_type is not None:
                    apex = QgsGeometryGeneratorSymbolLayer.create({})
                    apex.setGeometryExpression("point_n($geometry, 1)")
                    apex.setSymbolType(QgsSymbol.SymbolType.Marker)
                    apex.setSubSymbol(self._make_symbol(src_type))
                    sym.appendSymbolLayer(apex)

                categories.append(QgsRendererCategory(ftype, sym, ftype))

        layer.setRenderer(QgsCategorizedSymbolRenderer("ftype", categories))


    def _apply_style(self, layer: QgsVectorLayer):
        self._apply_categorized_renderer(layer)
        self._apply_labeling(layer)

    # ---------------- Auto numbering ----------------
    def _next_number_for_type(self, layer: QgsVectorLayer, ftype: str) -> int:
        pattern = re.compile(rf"^{re.escape(ftype)}-(\d+)$")
        max_n = 0
        req = QgsFeatureRequest().setFilterExpression(f"\"ftype\" = '{ftype}'")
        for f in layer.getFeatures(req):
            lab = (f.attribute("label") or "").strip()
            m = pattern.match(lab)
            if m:
                try:
                    max_n = max(max_n, int(m.group(1)))
                except ValueError:
                    pass
        return max_n + 1

    def _auto_label_if_needed(self, layer: QgsVectorLayer, label: str, ftype: str) -> str:
        label = (label or "").strip()
        if label:
            return label
        n = self._next_number_for_type(layer, ftype)
        return f"{ftype}-{n:02d}"

    def _auto_label_for_single_obj(self, layer: QgsVectorLayer, label: str,
                                   station_type: str) -> str:
        """Auto-number a single-object directional feature (e.g. Observer
        Station) by its station-type label pattern. The feature lives on the
        polygon layer with a wedge ``ftype``, so we match on the label text
        rather than on ``ftype`` to keep numbering continuous per station type."""
        label = (label or "").strip()
        if label:
            return label
        pattern = re.compile(rf"^{re.escape(station_type)}-(\d+)$")
        max_n = 0
        for f in layer.getFeatures():
            lab = (f.attribute("label") or "").strip()
            m = pattern.match(lab)
            if m:
                try:
                    max_n = max(max_n, int(m.group(1)))
                except ValueError:
                    pass
        return f"{station_type}-{max_n + 1:02d}"

    # ---------------- FOV cone colour ----------------
    def _next_fov_colour(self, poly_layer: QgsVectorLayer) -> str:
        """Return the next field-of-view cone colour from the shared qualitative
        palette (the same one the Building Mapper cycles), so each observer /
        surveyor / camera placed gets a visually distinct cone and overlapping
        FOVs are easy to tell apart. Indexed by how many auto-coloured cones
        already exist on the polygon layer, mirroring the building placer."""
        from .building_tree_mapper import building_colour_for
        n = 0
        for f in poly_layer.getFeatures():
            if (f.attribute("fov_color") or "").strip():
                n += 1
        return building_colour_for(n)

    # ---------------- Zoom first feature ----------------
    def _pan_to_feature(self, geom_27700: "QgsGeometry"):
        """Pan the map canvas to centre on geom without changing zoom level."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        xform = QgsCoordinateTransform(LAYER_CRS, canvas_crs, QgsProject.instance())
        centroid = geom_27700.centroid().asPoint()
        p = xform.transform(centroid)
        ext = self.canvas.extent()
        half_w = ext.width()  / 2.0
        half_h = ext.height() / 2.0
        self.canvas.setExtent(QgsRectangle(
            p.x() - half_w, p.y() - half_h,
            p.x() + half_w, p.y() + half_h,
        ))
        self.canvas.refresh()

    # ---------------- Add / Undo ----------------
    def _add_point_feature(self, point_27700: QgsPointXY, label: str, gridref: str, ftype: str, pan: bool = False):
        if not self._require_saved_project():
            return
        layer = self._get_or_create_layer()
        layer.reload()

        label = self._auto_label_if_needed(layer, label, ftype)

        feat = QgsFeature(layer.fields())
        feat.setAttribute("label", label)
        feat.setAttribute("gridref", gridref or "")
        feat.setAttribute("ftype", ftype or "")
        this_uuid = str(uuid.uuid4())
        feat.setAttribute("uuid", this_uuid)
        feat.setGeometry(QgsGeometry.fromPointXY(point_27700))

        layer.startEditing()
        ok = layer.addFeature(feat)
        layer.commitChanges()
        layer.triggerRepaint()

        if not ok:
            raise RuntimeError("Failed to add feature.")

        # Buffers are added explicitly from the feature's dedicated side-panel
        # button, never implicitly during placement.
        buf_uuid = None

        self._record_placement((this_uuid, buf_uuid))
        self._update_counts()   # rebuilds rail + curved-label arcs
        self._record_recent(ftype)
        self.status_lbl.setText(f"✓ {ftype} placed")
        # One global entry covers the point and its buffer zone.
        self._push_global_undo_uuid(this_uuid, buf_uuid)
        self.label_edit.clear()
        if pan:   # only when added from a grid reference (so you can see where it landed)
            self._pan_to_feature(feat.geometry())

        # Intentionally not zooming — map view stays unchanged after placement.

    def undo_last(self):
        if not self._undo_stack:
            self._update_undo_state()
            return

        entry = self._undo_stack.pop()
        # Support both old str entries and new (feat_uuid, buf_uuid) tuples
        if isinstance(entry, tuple):
            last_uuid, buf_uuid = entry
        else:
            last_uuid, buf_uuid = entry, None

        removed_pairs = []   # (layer, fid) actually deleted, for global-stack sync

        def _delete_by_uuid(layers, target_uuid):
            found = False
            for layer in layers:
                if layer is None:
                    continue
                req = QgsFeatureRequest().setFilterExpression(f"\"uuid\" = '{target_uuid}'")
                ids = [f.id() for f in layer.getFeatures(req)]
                if ids:
                    layer.startEditing()
                    all(layer.deleteFeature(fid) for fid in ids)
                    layer.commitChanges()
                    layer.triggerRepaint()
                    removed_pairs.extend((layer, fid) for fid in ids)
                    found = True
                    # Keep going: a directional feature stores its marker on the
                    # point layer AND its FOV wedge on the polygon layer under the
                    # same uuid, so both must be removed.
            return found

        # Snapshot the feature(s) before deletion so Redo can restore them.
        main_layers = [
            self._get_existing_layer(),
            self._get_existing_line_layer(),
            self._get_existing_poly_layer(),
        ]
        buf_layer = self._get_existing_buffer_layer()
        captured = self._capture_by_uuid(main_layers, last_uuid)
        if buf_uuid:
            captured += self._capture_by_uuid([buf_layer], buf_uuid)

        # Delete the main feature
        found = _delete_by_uuid(main_layers, last_uuid)

        if not found:
            QMessageBox.warning(self, "Undo", "Could not find the last placed feature.")

        # Delete the paired buffer zone if one was recorded
        if buf_uuid:
            _delete_by_uuid([buf_layer], buf_uuid)

        if captured:
            self._redo_stack.append({"entry": (last_uuid, buf_uuid),
                                     "feats": captured})

        # Keep the global stack consistent: these features are gone, so any
        # global entry referencing them is stale — and with GPKG fid
        # recycling a stale entry could later target the wrong feature.
        if removed_pairs and self._main_panel and hasattr(
                self._main_panel, "drop_undo_entries"):
            try:
                self._main_panel.drop_undo_entries(removed_pairs)
            except Exception:
                pass

        self._refresh_all_feature_buffer_bands()
        self._update_undo_state()
        self._update_counts()

    def _capture_by_uuid(self, layers, target_uuid):
        """Snapshot every feature carrying *target_uuid* (geometry + attributes
        + its layer) so an undo can be reversed by redo_last."""
        caps = []
        if not target_uuid:
            return caps
        for layer in layers:
            if layer is None:
                continue
            req = QgsFeatureRequest().setFilterExpression(
                f"\"uuid\" = '{target_uuid}'")
            for f in layer.getFeatures(req):
                attrs = {fld.name(): f[fld.name()] for fld in layer.fields()}
                caps.append({"layer": layer,
                             "geom": QgsGeometry(f.geometry()),
                             "attrs": attrs})
        return caps

    def redo_last(self):
        """Re-add the most recently undone feature(s), restoring their original
        geometry, attributes and uuid so a later undo still finds them."""
        if not self._redo_stack:
            self._update_undo_state()
            return

        rec = self._redo_stack.pop()
        main_uuid = rec["entry"][0]
        buf_layer = self._get_existing_buffer_layer()
        main_layer = None
        for cap in rec["feats"]:
            layer = cap["layer"]
            if layer is None:
                continue
            feat = QgsFeature(layer.fields())
            feat.setGeometry(cap["geom"])
            for name, val in cap["attrs"].items():
                if name.lower() == "fid":   # let the provider assign a fresh key
                    continue
                feat.setAttribute(name, val)
            layer.startEditing()
            layer.addFeature(feat)
            layer.commitChanges()
            layer.triggerRepaint()
            if layer is not buf_layer and main_layer is None:
                main_layer = layer

        # Put the placement back on the undo stack (without clearing redo).
        self._undo_stack.append(rec["entry"])
        self._update_undo_state()
        self._update_counts()

        # Mirror onto the main panel's global undo, like a fresh placement —
        # covering the re-added marker, wedge and buffer under one entry.
        if main_uuid:
            self._push_global_undo_uuid(main_uuid, rec["entry"][1])

    # ---------------- Actions ----------------

    @staticmethod
    def _dms_coordinate_pair(text):
        """Return a WGS84 (longitude, latitude) pair from two DMS values."""
        pattern = re.compile(
            r"(\d+(?:\.\d+)?)\s*[°º]\s*"
            r"(?:(\d+(?:\.\d+)?)\s*['′]\s*)?"
            r"(?:(\d+(?:\.\d+)?)\s*[\"″]\s*)?([NSEW])",
            re.IGNORECASE)
        values = {}
        for degrees, minutes, seconds, hemisphere in pattern.findall(text):
            value = (float(degrees) + float(minutes or 0) / 60.0
                     + float(seconds or 0) / 3600.0)
            hemisphere = hemisphere.upper()
            if hemisphere in ("S", "W"):
                value = -value
            values[hemisphere] = value
        latitude = values.get("N", values.get("S"))
        longitude = values.get("E", values.get("W"))
        return ((longitude, latitude)
                if longitude is not None and latitude is not None else None)

    def _transform_coordinate_to_bng(self, x, y, source_crs):
        if not (math.isfinite(float(x)) and math.isfinite(float(y))):
            raise ValueError("Coordinates must be finite numbers.")
        point = QgsPointXY(float(x), float(y))
        if source_crs == LAYER_CRS:
            return point
        if not source_crs.isValid():
            raise ValueError("The supplied coordinate reference system is not valid.")
        transform = QgsCoordinateTransform(
            source_crs, LAYER_CRS, QgsProject.instance())
        result = transform.transform(point)
        return QgsPointXY(float(result.x()), float(result.y()))

    def _parse_coordinate_input(self, text):
        """Parse common coordinate formats and return an EPSG:27700 point.

        Accepted inputs include OS grid references, British National Grid
        eastings/northings, decimal WGS84 latitude/longitude (either order),
        DMS coordinates, coordinates in the current map CRS, and an explicit
        ``EPSG:code x,y`` pair.
        """
        value = (text or "").strip()
        if not value:
            raise ValueError("Please enter a grid reference or coordinate pair.")

        # UK National Grid reference, e.g. TQ 443 806 or NT2757373172.
        if re.match(r"^[A-Za-z]{2}\s*\d", value):
            easting, northing = gridref_to_osgb_en(value)
            return QgsPointXY(float(easting), float(northing))

        # Degrees/minutes/seconds with hemisphere letters.
        dms = self._dms_coordinate_pair(value)
        if dms is not None:
            return self._transform_coordinate_to_bng(
                dms[0], dms[1], QgsCoordinateReferenceSystem("EPSG:4326"))

        # Explicit CRS, e.g. EPSG:3857 -14226,6711542.  Explicit pairs always
        # follow normal x,y order for that CRS.
        epsg_match = re.match(
            r"^\s*EPSG\s*[:=]?\s*(\d{4,6})\s*[,;:]?\s*(.*)$",
            value, re.IGNORECASE)
        pair_text = epsg_match.group(2) if epsg_match else value
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", pair_text)
        if len(numbers) != 2:
            raise ValueError(
                "Enter two coordinates, for example ‘530000, 180000’, "
                "‘51.5074, -0.1278’, or ‘EPSG:3857 x,y’.")
        first, second = map(float, numbers)

        if epsg_match:
            source = QgsCoordinateReferenceSystem(
                "EPSG:%s" % epsg_match.group(1))
            return self._transform_coordinate_to_bng(first, second, source)

        # Plausible British National Grid eastings/northings.
        if 0 <= first <= 800000 and 0 <= second <= 1400000:
            return QgsPointXY(first, second)

        # Decimal geographic coordinates.  Support both the common latitude,
        # longitude order and GIS x,y (longitude, latitude) order.
        if 45 <= first <= 65 and -15 <= second <= 15:
            latitude, longitude = first, second
        elif -15 <= first <= 15 and 45 <= second <= 65:
            longitude, latitude = first, second
        elif abs(first) <= 90 and abs(second) <= 180:
            # In the absence of an explicit CRS, latitude,longitude is the
            # most common pasted form.
            latitude, longitude = first, second
        elif abs(first) <= 180 and abs(second) <= 90:
            longitude, latitude = first, second
        else:
            # A numeric pair outside geographic/BNG ranges is interpreted in
            # the current map CRS, which supports coordinates copied from QGIS.
            source = self.canvas.mapSettings().destinationCrs()
            return self._transform_coordinate_to_bng(first, second, source)
        return self._transform_coordinate_to_bng(
            longitude, latitude, QgsCoordinateReferenceSystem("EPSG:4326"))

    def add_from_coordinates(self):
        coordinate_text = self.gridref_edit.text().strip()
        label = self.label_edit.text().strip()
        ftype = self.type_combo.currentText().strip()

        if not coordinate_text:
            QMessageBox.warning(
                self, "Missing input",
                "Please enter a grid reference or coordinate pair.")
            return

        try:
            pt_27700 = self._parse_coordinate_input(coordinate_text)
            self._add_point_feature(
                pt_27700, label=label, gridref=coordinate_text,
                ftype=ftype, pan=True)
            if not label:
                self.label_edit.setText("")
        except (GridRefError, ValueError) as ex:
            QMessageBox.critical(self, "Invalid coordinates", str(ex))
        except Exception as ex:
            QMessageBox.critical(
                self, "Coordinate conversion failed", str(ex))

    # Kept for compatibility with any external code that called the previous
    # grid-reference-only action directly.
    def add_from_gridref(self):
        self.add_from_coordinates()

    def closeEvent(self, event):
        self._remove_shape_key_filter()
        self._clear_highlight()
        # End the placement run: invalidate any pending re-arm timer (the
        # continuous loop must not re-arm after the dialog is gone) and stop
        # watching the canvas tool.
        self._arm_seq += 1
        if getattr(self, "_maptool_watch_on", False):
            try:
                self.canvas.mapToolSet.disconnect(
                    self._schedule_place_btn_refresh)
            except Exception:
                pass
            self._maptool_watch_on = False
        # Leave the canvas on the Pan tool — never a lingering placement crosshair
        # (and drop any dialog-owned map tool before the dialog is torn down).
        try:
            self.iface.messageBar().clearWidgets()
            self._activate_pan_tool()
        except Exception:
            pass
        # Stop the zoom-driven arc rebuild so nothing fires after the dialog goes.
        if getattr(self, "_arc_zoom_connected", False):
            try:
                self.canvas.scaleChanged.disconnect(self._arc_zoom_slot)
            except Exception:
                pass
            self._arc_zoom_connected = False
        super().closeEvent(event)

    def activate_pick_tool(self):
        self._capture_prev_tool()
        self._pick_tool = _PickPointTool(self.canvas, self._picked_point, self._cancel_pick)
        self.canvas.setMapTool(self._pick_tool)

    def _cancel_pick(self):
        """Right-click while pick-placing points → end the keep-placing loop."""
        self.iface.messageBar().clearWidgets()
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)
        else:
            self._activate_pan_tool()

    def _picked_point(self, canvas_point):
        if self._prev_map_tool is not None:
            self.canvas.setMapTool(self._prev_map_tool)

        label = self.label_edit.text().strip()
        ftype = self.type_combo.currentText().strip()

        try:
            canvas_crs = self.canvas.mapSettings().destinationCrs()
            xform = QgsCoordinateTransform(canvas_crs, LAYER_CRS, QgsProject.instance())
            pt_27700 = xform.transform(canvas_point)
            self._add_point_feature(QgsPointXY(pt_27700.x(), pt_27700.y()), label=label, gridref="", ftype=ftype)
            if not label:
                self.label_edit.setText("")
            # Replace the plain "✓ placed" status with the run tally + stop hint.
            self._note_placed(f"✓ {ftype} placed")
            self._rearm_placement(self.activate_pick_tool)
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))


# ── Module-level helpers ──────────────────────────────────────────────────────

# Groups that should remain expanded in the layer panel
_KEEP_EXPANDED = {"Base Maps"}


def _apply_tree_collapse(root):
    """Collapse all layer tree groups and layer nodes except those in
    _KEEP_EXPANDED. Called whenever a new layer is placed in the tree."""
    for child in root.children():
        from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer
        if isinstance(child, QgsLayerTreeGroup):
            if child.name() in _KEEP_EXPANDED:
                child.setExpanded(True)
            else:
                child.setExpanded(False)
                # Also collapse layer nodes inside collapsed groups
                for sub in child.children():
                    if isinstance(sub, QgsLayerTreeLayer):
                        sub.setExpanded(False)
        elif isinstance(child, QgsLayerTreeLayer):
            child.setExpanded(False)


def _uncollapse_layer_node(layer):
    """After a feature is added to `layer`, expand only that layer's tree
    node and its parent group. All other groups stay collapsed."""
    from qgis.core import QgsProject, QgsLayerTreeGroup, QgsLayerTreeLayer
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer.id())
    if node is None:
        return
    node.setExpanded(True)
    parent = node.parent()
    for child in root.children():
        if isinstance(child, QgsLayerTreeGroup):
            is_target = (child is parent or child.findLayer(layer.id()) is not None)
            child.setExpanded(is_target)
        elif isinstance(child, QgsLayerTreeLayer):
            child.setExpanded(
                child.layer() is not None and child.layer().id() == layer.id()
            )
