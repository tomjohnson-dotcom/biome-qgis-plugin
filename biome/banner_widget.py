"""Living header banner.

The dock's title strip is a painted landscape: the stylised "bıome"
wordmark (leaf for the i's dot, ring for the o) half-tucked behind a
pine hill, under a sky that follows the REAL sun at the site. The sun
tracks across the banner between local sunrise and sunset; after dark
the palette crossfades to dusk greens and a moon crosses instead.

Wildlife lives in a layered world: animals cross on the far hill
(small, slipping behind the near hill), the mid slope, or the near
foreground (large, in front of the grass tufts), each with a soft
contact shadow. They behave: a hare stops and sits bolt upright, a
deer grazes or stands alert, a fox mouse-pounces or quarters for
scent, a hedgehog curls into a ball, a badger snuffles, a heron
stalks to the lake edge and strikes, a kingfisher dips into a fishing
dive, and the newt and otter swim when their path crosses water. Land
animals crossing a lake sink to a chin-up swim — slower, bobbing,
trailing a wake. Crossings wander: animals enter from either side,
dawdle, stop more than once, and sometimes double back the way they
came. Sometimes they arrive together — boxing hares meeting head-on,
a fox coursing a hare, a badger cub trailing its parent, deer grazing
as a pair.

The countryside is a biome ribbon revealed by widening the dock:
wildflower meadow, hedgerow and ponds, a stream, dense woodland, a
drystone wall with a gateway, a waterfall feeding a meandering river,
reeded lakes, gorse in bloom, fir stands, a moorland tarn and a
heather bank — under craggy, faceted, snow-capped mountain ranges.
Some trees stand proud of the far slope, so distant wildlife slips
behind them. Hovering the pointer over an animal makes it stop and
watch the cursor, turning to face it.

Site coordinates come from the redline centroid when one exists
(latlon_provider), else a Midlands fallback. A QgsSettings-backed
day-length lets the whole cycle run compressed (5-min demo … real
24 h), and party mode floods the banner with wildlife. Timers run
only while the banner is visible; everything is drawn in a 44-px
design space scaled up to the actual banner height.
"""

import math
import random
import time
from datetime import datetime, timedelta

from qgis.PyQt.QtCore import Qt, QTimer, QPointF, QRectF
from qgis.PyQt.QtGui import (QColor, QFont, QFontMetricsF, QLinearGradient,
                             QPainter, QPainterPath, QPen, QPicture)
from qgis.PyQt.QtWidgets import QWidget

# Palette anchors (day → dusk); front hill keeps the brand green in both.
_SKY_DAY,   _SKY_NIGHT   = "#E9F1F6", "#1C2E25"
_BACK_DAY,  _BACK_NIGHT  = "#9CC79B", "#2A3D33"
_FRONT                    = "#2E6B4F"
_TREE_DAY,  _TREE_NIGHT  = "#1F4A37", "#16241D"
_BARK_DAY,  _BARK_NIGHT  = "#5F4A36", "#221B14"
_WORD_DAY,  _WORD_NIGHT  = "#1A2420", "#F4F6F1"
_LEAF_DAY,  _LEAF_NIGHT  = "#1F7A4D", "#9CC79B"
_INK, _CHALK, _BELLY      = "#1C2E25", "#F4F6F1", "#D85A30"
_HAZE_DAY,  _HAZE_NIGHT  = "#C7D8C9", "#233830"
_HAZE2_DAY, _HAZE2_NIGHT = "#DAE5DA", "#20342B"
_HAZE3_DAY, _HAZE3_NIGHT = "#E7EEE6", "#1D2E27"
_SNOW_DAY,  _SNOW_NIGHT  = "#FAFCFA", "#465B4F"
_WATER_DAY, _WATER_NIGHT = "#AECBDE", "#3E5866"
_FALLS_DAY, _FALLS_NIGHT = "#EAF3F9", "#5E7886"
_TUFT_DAY,  _TUFT_NIGHT  = "#26573F", "#142720"

_FALLBACK_LATLON = (52.48, -1.90)   # Birmingham — mid-UK sunrise is close enough

# QgsSettings keys.
_CYCLE_KEY = "biome/banner_cycle_minutes"   # 0 = follow the real sun
_PARTY_KEY = "biome/banner_party"
_WEATHER_KEY = "biome/banner_weather"       # off|manual|random|auto|live
_WEATHER_STATE_KEY = "biome/banner_weather_state"  # manual-mode state
_PACE_KEY = "biome/banner_weather_pace"     # 0 calm … 100 wild (random)
_SEASON_KEY = "biome/banner_season"         # auto | random | spring | … | winter
_PARALLAX_KEY = "biome/banner_parallax"     # pointer parallax on/off
_SCENE_KEY = "biome/banner_scene"           # the living scene on/off
_MOON_KEY = "biome/banner_moon"             # real | random lunar phase

# Weather tints.
_RAIN_DAY,  _RAIN_NIGHT  = "#8FB6C8", "#5B7C8C"
_SNOW_FLAKE               = "#FBFDFF"
_FOG_DAY,   _FOG_NIGHT    = "#E7ECE8", "#2A3A34"

_SPECIES = ("gcn", "badger", "hog", "owl", "bat", "hare", "fox", "deer",
            "buzzard", "otter", "heron", "kingfisher", "pheasant",
            "beaver", "water_vole", "bee", "stag_beetle",
            "grass_snake", "adder", "kite", "dragonfly", "barn_owl")

# Who's about when. Crepuscular species bridge phases; picks avoid
# repeating the previous visitor.
_ROSTER = {
    "dawn":  ("hog", "hare", "fox", "deer", "pheasant", "otter", "heron",
              "bee", "water_vole", "beaver", "grass_snake", "kite"),
    "day":   ("gcn", "hare", "deer", "buzzard", "kingfisher", "heron",
              "pheasant", "bee", "water_vole", "grass_snake", "adder",
              "kite", "dragonfly"),
    "dusk":  ("owl", "fox", "bat", "deer", "otter", "heron", "pheasant",
              "stag_beetle", "beaver", "water_vole", "kite", "barn_owl"),
    "night": ("badger", "bat", "fox", "owl", "hog", "otter", "stag_beetle",
              "beaver", "barn_owl"),
}

# Paired encounters, by phase. Each spawns a scripted little scene.
# Deer and badger scenes are weighted up so hares don't hog the stage.
# "converge" walks two deer in from opposite edges to graze the same
# spot — meeting somewhere on purpose is the clearest intent there is.
_ENCOUNTERS = {
    "dawn":  ("boxing", "deer_pair", "deer_pair", "chase", "converge"),
    "day":   ("deer_pair", "deer_pair", "boxing", "converge", "converge"),
    "dusk":  ("deer_pair", "chase", "badger_cub", "converge"),
    "night": ("badger_cub", "badger_cub", "chase"),
}

# Movement/shape spec per species.
_SPEC = {
    "gcn":        dict(speed=11, gait_hz=1.3, w=33, h=10, flying=False),
    "badger":     dict(speed=21, gait_hz=1.8, w=36, h=15, flying=False),
    "hog":        dict(speed=19, gait_hz=2.6, w=28, h=14, flying=False),
    "hare":       dict(speed=36, gait_hz=1.7, w=26, h=16, flying=False),
    "fox":        dict(speed=28, gait_hz=2.2, w=34, h=15, flying=False),
    "deer":       dict(speed=24, gait_hz=1.4, w=30, h=22, flying=False),
    "otter":      dict(speed=24, gait_hz=2.0, w=30, h=12, flying=False),
    "heron":      dict(speed=14, gait_hz=0.8, w=26, h=24, flying=False),
    "pheasant":   dict(speed=24, gait_hz=2.8, w=26, h=15, flying=False),
    "owl":        dict(speed=68, gait_hz=1.3, w=34, h=12, flying=True),
    "bat":        dict(speed=48, gait_hz=3.2, w=24, h=9,  flying=True),
    "buzzard":    dict(speed=45, gait_hz=0.9, w=30, h=10, flying=True),
    "kingfisher": dict(speed=95, gait_hz=5.0, w=13, h=8,  flying=True),
    "beaver":     dict(speed=17, gait_hz=1.8, w=30, h=13, flying=False),
    "water_vole": dict(speed=17, gait_hz=4.0, w=15, h=8,  flying=False),
    "bee":        dict(speed=20, gait_hz=9.0, w=10, h=7,  flying=True),
    "stag_beetle": dict(speed=11, gait_hz=5.0, w=16, h=7, flying=False),
    "grass_snake": dict(speed=13, gait_hz=0.5, w=26, h=6, flying=False),
    "adder":      dict(speed=11, gait_hz=0.45, w=25, h=6, flying=False),
    "kite":       dict(speed=40, gait_hz=0.8, w=32, h=11, flying=True),
    "dragonfly":  dict(speed=34, gait_hz=1.2, w=13, h=7,  flying=True),
    "barn_owl":   dict(speed=26, gait_hz=1.1, w=30, h=11, flying=True),
    "squirrel":   dict(speed=30, gait_hz=3.3, w=16, h=13, flying=False),
    "ecologist":  dict(speed=22, gait_hz=1.5, w=15, h=27, flying=False),
    "sheep":      dict(speed=14, gait_hz=1.4, w=24, h=16, flying=False),
    "rabbit":     dict(speed=21, gait_hz=2.3, w=13, h=9,  flying=False),
}

# Species whose crossing can be shared with a smaller juvenile.
_BABIES = ("deer", "fox", "badger", "hare", "hog", "otter", "pheasant",
           "gcn", "beaver", "water_vole")

# Weather states the banner can render.
_WEATHERS = ("clear", "cloud", "fog", "rain", "snow", "storm")

# Depth layers: (allowed layers, weights). Swimmers and the heron stay on
# the mid slope where the lakes are.
_LAYER_W = {
    "deer":     (("back", "mid", "fore"), (0.45, 0.45, 0.10)),
    "fox":      (("back", "mid", "fore"), (0.20, 0.50, 0.30)),
    "hare":     (("back", "mid", "fore"), (0.20, 0.50, 0.30)),
    "badger":   (("back", "mid", "fore"), (0.15, 0.55, 0.30)),
    "hog":      (("back", "mid", "fore"), (0.10, 0.55, 0.35)),
    "pheasant": (("back", "mid", "fore"), (0.30, 0.50, 0.20)),
}
_LAYER_SCALE = {"back": 0.66, "mid": 1.0, "fore": 1.28}

# Poses a species can strike when it stops mid-crossing. Repeats weight
# the pick; the ecologist's stops are field tasks in their own right.
_IDLE_POSES = {
    "hare": ("sit",), "deer": ("graze", "graze", "alert"),
    "fox": ("pounce", "sniff"), "hog": ("ball",),
    "badger": ("snuffle",), "otter": ("groom",), "pheasant": ("peck",),
    "gcn": ("bask",), "ecologist": ("binoculars", "photo"),
    "beaver": ("gnaw",), "water_vole": ("nibble",),
    "stag_beetle": ("rear",), "grass_snake": ("coil",), "adder": ("coil",),
    "sheep": ("graze", "graze", "graze"), "rabbit": ("nibble",),
}

# Who takes to the water: swimmers cross lakes on the surface; the
# heron shares their mid-slope beat but fishes from the shore. Snakes
# swim but prefer to coil up on dry ground, so they avoid lake pauses.
_SWIMMERS = ("gcn", "otter", "beaver", "water_vole", "grass_snake",
             "adder")
_LAKEFOLK = _SWIMMERS + ("heron",)
_LAKE_PAUSERS = ("gcn", "otter", "beaver", "water_vole", "heron")

# Water manners. _NO_SWIM would never cross open water: they divert
# around a lake, along the far bank or the near shore. _RARE_SWIM can
# swim but only sometimes choose to — each approach is a coin toss
# between going through and going around. Everyone else keeps their
# existing habits (true swimmers swim, the hare bounds over, the
# pheasant bursts across, the heron fishes from the shore).
_NO_SWIM = ("fox", "sheep", "hog")
_RARE_SWIM = ("deer", "badger", "adder")

# Winter breath: cold-blooded species (and the birds, whose puffs are
# too faint to see) never steam. Everyone else gets a muzzle anchor as
# (x, y) fractions of the sprite box; the default suits most quadrupeds.
_NO_BREATH = ("gcn", "adder", "grass_snake", "stag_beetle", "dragonfly",
              "bee", "pheasant", "heron", "kingfisher", "barn_owl",
              "kite", "buzzard", "bat")
_BREATH_AT = {"deer": (0.88, 0.10), "ecologist": (0.72, 0.10),
              "sheep": (0.92, 0.20)}

# The banner is drawn in a fixed 44-px-tall design space and scaled to the
# real banner height, so raising _H makes the whole scene bigger.
_DESIGN_H = 44.0
_H = 58

# Hill ridgelines as ABSOLUTE design-x polylines (like the ribbon and
# the mountain wall), so widening the dock reveals more rolling ground
# instead of stretching the same few bumps flat. The drawn path smooths
# through these, and walkers interpolate them so feet stay on the crest.
# The front hill's first stretch (x 0-160) sets the wordmark's tuck —
# now identical at every dock width.
_BACK_PTS = [
    (0, 29.5), (80, 26.5), (160, 23.5), (250, 25.5), (330, 23.0),
    (420, 26.0), (500, 28.5), (560, 27.0), (610, 29.5), (700, 26.0),
    (780, 23.0), (860, 25.5), (940, 23.5),
    (1020, 27.5), (1090, 30.0),            # low ground: the U-valley shows
    (1170, 26.5), (1250, 24.0), (1330, 26.5), (1420, 29.0), (1500, 26.0),
    (1580, 23.0), (1660, 25.5), (1740, 28.0), (1830, 24.5), (1920, 22.0),
    (2010, 24.5), (2100, 27.0), (2190, 24.0), (2280, 26.0), (2400, 25.0),
]
_FRONT_PTS = [
    (0, 38.0), (55, 31.5), (115, 28.7), (185, 29.8), (255, 32.0),
    (330, 34.5), (400, 36.5), (470, 34.5), (540, 31.5), (610, 33.5),
    (690, 35.8), (760, 37.5), (830, 39.3), (910, 36.8), (980, 34.2),
    (1050, 36.2), (1120, 38.3), (1200, 35.8), (1280, 33.2), (1360, 35.2),
    (1440, 37.4), (1520, 39.4), (1600, 36.8), (1680, 34.2), (1760, 36.2),
    (1840, 38.4), (1920, 35.8), (2000, 33.5), (2080, 35.5), (2160, 37.6),
    (2240, 35.4), (2320, 36.8), (2400, 36.0),
]

# ── the biome ribbon (design-space x, fixed) ─────────────────────────
_TREES = [
    (350, "b", 1.00), (366, "c", 0.90), (382, "b", 0.62),   # hedgerow copse
    (645, "b", 1.25),                                        # lone oak
    (860, "c", 1.00), (878, "c", 1.20), (896, "c", 0.85),    # fir stand
    (1040, "b", 1.00), (1062, "b", 0.70), (1080, "c", 0.95),
    (1170, "b", 1.10), (1192, "c", 1.00), (1210, "b", 0.75),
    (1460, "b", 1.30), (1482, "c", 1.05), (1500, "b", 0.80),  # far oak wood
    (1720, "c", 1.15), (1738, "c", 1.35), (1756, "c", 0.95),  # far pinewood
    (1774, "c", 1.10), (1980, "b", 1.20), (2002, "b", 0.78),
    # Infill so the back ridge never runs bare for long — but the way
    # trees really stand: tight clumps of two or three, then a long
    # empty stretch, then a loner. Clear of the rivers (440/800/1520)
    # and the U-valley reveal window (1012-1094).
    (178, "b", 0.85), (194, "c", 0.62),                       # a pair
    (307, "b", 0.70),                                         # a loner
    (703, "b", 0.90), (716, "b", 0.66), (734, "c", 0.78),     # a clump
    (949, "c", 0.90),                                         # a loner
    (1118, "b", 0.70),
    (1552, "c", 0.88), (1571, "b", 1.00),                     # a pair
    (1646, "c", 0.72),
    (1871, "b", 0.95),                                        # a loner
    (2054, "b", 0.90), (2071, "c", 0.78), (2093, "b", 0.60),  # a clump
    (2334, "c", 0.95),                                        # a loner
]

# Low scrub clumps — bramble and blackthorn — scattered along the back
# ridge between the trees, hazed with the rest of that plane.
_SCRUB = (
    (212, 0.9), (332, 0.7), (430, 0.8), (672, 0.75), (736, 0.9),
    (832, 0.7), (936, 0.85), (1000, 0.7), (1122, 0.8), (1232, 0.75),
    (1446, 0.85), (1536, 0.7), (1652, 0.8), (1756, 0.9), (1812, 0.7),
    (1950, 0.8), (2036, 0.75), (2116, 0.85), (2262, 0.7), (2332, 0.8),
)

# Lakes: (x, rx, ry, shoreline multipliers). Each shoreline is a distinct
# irregular shape — the third is a kidney tarn, the fourth a grand loch.
_LAKES = [
    # The first two sit flatter (smaller ry) so a real strip of bank
    # stays walkable above them — the classic-height clamp otherwise
    # pushes their rims up over the ridge line.
    (410, 20, 2.6, (1.00, 0.75, 1.12, 0.90, 1.05, 0.70,
                    1.18, 0.85, 0.95, 1.10, 0.80, 1.06)),
    (700, 34, 3.4, (1.15, 0.90, 1.05, 0.75, 1.20, 0.95,
                    0.80, 1.10, 0.70, 1.12, 0.92, 1.04)),
    (1000, 22, 3.2, (1.10, 0.95, 0.60, 0.90, 1.15, 1.00,
                     0.85, 1.20, 0.90, 0.55, 1.00, 1.10)),
    (1600, 52, 5.4, (1.10, 0.82, 1.16, 0.94, 1.22, 0.78, 1.14,
                     0.88, 1.05, 0.70, 1.18, 0.92, 1.08, 0.84)),
]

_FOREST = (470, 610)
_FOREST2 = (1250, 1420)          # a second, deeper wood revealed further out
_STREAM_X, _RIVER_X = 440, 800
_RIVER2_X = 1520                 # a second, grander waterfall + river

# UK habitat extras along the front hill: a wildflower meadow, a bluebell
# drift (the bees' favourite), a drystone wall with a gateway, gorse
# clumps in bloom, and a heather bank.
_MEADOW = (240, 322)
_BLUEBELLS = (456, 488)
# Field-boundary hedgerows along the front hill's contour: (x0, x1,
# gate_x). Spans thread the gaps between the other front-hill features
# AND the back-ridge woods — the tall hedges were burying the forests
# at 470-610 and 1250-1420, so they keep clear of both; only the small
# fir stand (860-896) and one lone tree (1871) stand behind a hedge
# now, their tops well proud of it.
_HEDGES = ((830, 945, 886), (1780, 1985, 1876), (2100, 2215, 2156))
# A landmark veteran oak on the near hill — a whole habitat in itself:
# a fox earth in the roots, a bat roost (PRF) in the trunk, perching
# branches, and acorns a red squirrel comes for.
_OAK_X = 386
_WALL = (738, 788)
_WALL_GAP = (760.0, 766.0)
_GORSE = ((952, 1.0), (968, 0.78), (1098, 1.05), (1116, 0.85), (1132, 0.7))
_HEATHER = (1658, 1712)

# Red deer body outline, traced from a reference silhouette (a hind, so
# no antlers) and clipped just above the legs. Facing right, in the
# sprite's 30×22 space; the lower legs are drawn separately (IK) so the
# deer can walk. Points 6..34 are the torso alone (neck/head excluded),
# used for the head-down grazing pose. The crown (44..51 + 0..5) was
# reworked from the raw trace: both ears now cluster at the BACK of the
# skull as a true side profile — one full near ear and a shorter peek
# of the far ear — with a clean crown ahead of them for the pedicles.
_DEER_BODY = [
    (21.45, 3.60), (21.35, 4.20), (21.10, 4.80), (20.90, 5.20),
    (20.80, 5.45), (20.67, 5.69), (19.57, 6.68), (18.70, 6.68),
    (17.65, 6.27), (14.68, 6.50), (6.65, 6.27), (5.31, 6.74),
    (3.91, 8.13), (3.91, 10.63), (4.21, 11.57), (4.50, 11.51),
    (4.90, 10.52), (5.54, 11.04), (5.89, 11.68), (5.54, 12.73),
    (4.61, 13.60), (8.69, 13.60), (9.09, 13.02), (9.97, 12.21),
    (11.30, 13.08), (12.47, 13.37), (14.62, 13.08), (15.73, 13.19),
    (15.76, 13.60), (17.73, 13.60), (17.76, 13.08), (18.64, 12.73),
    (19.28, 12.21), (19.80, 11.16), (21.66, 9.18), (23.12, 5.75),
    (24.22, 5.80), (24.81, 5.63), (25.27, 5.86), (25.97, 5.80),
    (26.26, 5.22), (26.03, 4.64), (24.86, 3.94), (24.57, 3.19),
    (24.22, 2.95), (23.70, 2.25), (23.30, 2.05), (22.90, 0.62),
    (22.35, 2.10), (21.75, 1.30), (21.30, 2.45), (21.35, 3.05)]

# ── the mountain wall ─────────────────────────────────────────────────
# Four ranges deep, drawn back to front, each modelled on REAL ridge
# forms rather than random zig-zags:
#   A — farthest, palest: high rolling whalebacks (Cairngorm plateau)
#       that climb eastward into a remote snow-wall crowning the vista.
#   B — a long serrated wall of sawtooth pinnacles (the Cuillin ridge)
#       with a lone dark horn standing at the head of the glen.
#   C — soft overlapping grassy spurs stepping down into the valley
#       (the Five Sisters of Kintail): the vegetated middle distance.
#   D — the near range in full detail, west to east: shattered
#       Tryfan-like teeth; a pyramidal horn with a stepped arête
#       falling off its east ridge; a corrie-bitten peak; a hanging
#       valley notched over each waterfall; a twin-summit arête
#       (Striding Edge); a deep U-shaped glacial trough with a flat
#       floor; a whaleback dome beside a sharp crag; and — revealed at
#       the widest widths — the grand snowy massif, the highest ground.
_MTN_D = [
    (420, 29.0), (444, 25.5), (460, 26.8), (476, 22.0),      # moor rise
    (490, 23.6), (502, 18.5), (510, 20.0), (518, 15.0),      # the teeth:
    (525, 17.2), (532, 12.8), (539, 15.4), (547, 11.8),      # shattered,
    (554, 14.4), (561, 10.8), (569, 13.8), (577, 9.6),       # climbing
    (585, 12.6),
    (593, 17.0),                                             # deep col
    (603, 10.5), (610, 6.8), (615, 5.0), (619, 6.4),         # the horn
    (627, 9.8), (635, 14.5), (641, 13.2),                    # its shoulder
    (649, 15.6), (655, 14.2), (663, 17.6), (669, 16.2),      # stepped arête
    (677, 19.6), (685, 21.6),                                # down to a col
    (701, 15.2), (713, 8.8), (721, 10.6), (729, 9.8),        # corrie peak
    (741, 14.4),
    (753, 17.6), (765, 13.8), (777, 12.6), (789, 13.4),      # hanging
    (800, 15.8), (812, 13.6), (824, 12.2),                   # valley (falls)
    (836, 7.6), (848, 9.0), (858, 5.9), (866, 8.2),          # the arête:
    (874, 7.1), (884, 10.4), (896, 9.4), (908, 11.4),        # two summits,
    (920, 10.2), (932, 7.9), (944, 9.4), (956, 8.4),         # a serrated
    (968, 12.4), (982, 17.0), (996, 22.5),                   # crest between
    (1012, 26.5), (1028, 28.2), (1050, 28.8),                # the U-valley:
    (1074, 28.4), (1094, 27.2),                              # glacial floor
    (1114, 24.5), (1132, 17.5), (1154, 11.0), (1176, 6.8),   # whaleback
    (1198, 7.8), (1218, 10.6), (1234, 15.5), (1248, 14.2),   # dome
    (1260, 17.0),
    (1274, 13.0), (1286, 9.2), (1294, 11.0), (1302, 10.4),   # the crag
    (1314, 15.0), (1328, 20.0), (1342, 24.0),
    (1358, 26.0), (1374, 24.5), (1392, 25.8), (1408, 22.0),  # low pass
    (1422, 23.4),
    (1438, 18.5), (1450, 12.0), (1460, 8.0), (1468, 4.8),    # grand massif:
    (1474, 6.2), (1482, 2.8), (1490, 5.4), (1496, 4.0),      # pinnacled
    (1504, 6.8), (1512, 10.5), (1520, 13.2),                 # west peak,
    (1530, 10.0), (1540, 6.5), (1548, 8.0), (1556, 4.6),     # hanging col
    (1564, 6.8), (1572, 5.4), (1584, 9.5), (1596, 13.5),     # (falls 2),
    (1610, 8.2), (1622, 10.8), (1634, 9.0), (1648, 13.5),    # east peaks
    (1664, 18.5), (1680, 23.5),
    (1702, 25.5), (1726, 21.0), (1750, 14.5), (1764, 16.8),  # the range
    (1778, 12.0), (1792, 14.8), (1812, 20.5), (1838, 24.5),  # recedes
    (1868, 19.0), (1894, 21.8), (1918, 16.5), (1938, 18.8),  # eastward,
    (1960, 23.5), (1992, 26.0), (2022, 21.5), (2052, 23.8),  # sinking to
    (2082, 18.0), (2106, 20.5), (2142, 24.5), (2182, 26.5),  # moorland
    (2222, 22.0), (2262, 24.8), (2302, 26.5), (2362, 27.5),
    (2400, 28.0),
]
_MTN_C = [
    (410, 27.0), (446, 22.5), (482, 24.8), (516, 19.5), (548, 22.5),
    (578, 18.0), (612, 21.0), (648, 16.5), (684, 19.8), (718, 15.5),
    (752, 18.8), (788, 14.8), (824, 18.0), (860, 14.2), (896, 17.2),
    (930, 13.5), (964, 16.2), (998, 12.8), (1030, 14.8), (1062, 11.2),
    (1096, 13.8), (1130, 10.8), (1168, 13.2), (1206, 10.2), (1246, 13.0),
    (1288, 10.5), (1330, 13.5), (1374, 10.8), (1418, 13.8), (1464, 11.2),
    (1510, 14.2), (1558, 11.8), (1608, 14.8), (1658, 12.2), (1710, 15.2),
    (1760, 12.8), (1815, 15.8), (1870, 13.2), (1925, 16.2), (1980, 13.8),
    (2040, 16.8), (2100, 14.2), (2165, 17.2), (2230, 15.0), (2300, 17.8),
    (2400, 16.0),
]
_MTN_B = [
    (430, 24.0), (485, 20.5), (540, 22.5), (595, 18.5), (650, 21.0),
    (705, 17.0), (760, 19.8), (815, 15.8), (870, 18.5), (925, 14.8),
    (975, 17.5), (1015, 12.5), (1048, 9.2), (1072, 11.5), (1105, 14.0),
    (1150, 11.2), (1200, 13.6), (1255, 10.6), (1310, 13.2), (1365, 10.0),
    (1415, 13.8), (1465, 10.2), (1512, 12.8), (1560, 9.5), (1606, 12.0),
    (1652, 8.8), (1698, 11.5), (1745, 8.2), (1792, 10.8), (1840, 8.0),
    (1900, 11.5), (1950, 9.8), (2000, 12.0), (2050, 10.2), (2100, 12.5),
    (2150, 10.5), (2200, 13.0), (2260, 11.5), (2320, 13.5), (2400, 12.0),
]
_MTN_A = [
    (500, 30.0), (570, 26.5), (645, 28.0), (720, 24.5), (800, 26.5),
    (880, 23.0), (965, 25.5), (1050, 22.0), (1140, 24.5), (1235, 21.0),
    (1330, 23.0), (1425, 19.5), (1520, 21.5), (1615, 18.0), (1710, 20.0),
    (1805, 16.5), (1885, 13.0), (1955, 15.0), (2020, 10.5), (2080, 12.5),
    (2135, 8.0), (2185, 10.0), (2235, 6.5), (2290, 8.8), (2345, 5.8),
    (2400, 7.5),
]
# Corrie scoops — glacial armchairs bitten into named east faces of the
# near range (summit x of the corrie peak, the whaleback, the massif).
_CORRIES = (713, 1176, 1556)

# A low foothill band between the mountain wall and the back hill —
# distant patchwork farmland glimpsed wherever the back hill dips.
_FOOTHILLS = [
    (240, 25.0), (330, 21.0), (430, 22.6), (540, 20.0), (660, 21.8),
    (800, 19.6), (950, 21.6), (1120, 19.8), (1300, 22.0), (1500, 19.2),
    (1700, 21.4), (1950, 19.8), (2200, 21.6), (2400, 20.2),
]
_FIELD_DAY, _FIELD_NIGHT = "#C2D4AC", "#243B31"

# Parallax: how far each depth plane slides (design px) at full pointer
# throw. Farther planes barely move; the nearest flora sweeps the most.
# (Amplitudes ~2.3x the originals so the effect actually registers;
# _PAR_FRONT and _PAR_BACK must stay inside _ridge_path's ±12 pad.)
_PAR_PEAKS, _PAR_FOOTHILLS = 2.4, 3.6
_PAR_BACK, _PAR_FRONT, _PAR_NEAR, _PAR_FLORA = 5.6, 8.4, 11.5, 15.0

# The survey boat draws at this scale so the rower holds his own
# against the wildlife around the loch (sprite local centre = 8).
_BOAT_S = 1.45


def _jag(pts, amp):
    """Insert deterministic jitter points between ridge vertices so the
    skyline reads as broken rock rather than a smooth polyline. The
    amplitude itself varies point to point, so one flank shatters while
    the next runs almost clean — no repeated sawtooth."""
    out = []
    for i in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        out.append((x0, y0))
        for t in (0.3, 0.55, 0.78):
            jx = x0 + (x1 - x0) * t
            jy = y0 + (y1 - y0) * t
            n = math.sin(jx * 12.9898) * 43758.5453
            n2 = math.sin(jx * 3.71) * 24634.63
            local = amp * (0.35 + 1.3 * (n2 - math.floor(n2)))
            jy += (n - math.floor(n) - 0.5) * 2 * local
            out.append((jx, min(jy, _DESIGN_H - 2)))
    out.append(pts[-1])
    return out


def _round_fells(pts, k, base=26.0, passes=2):
    """English the ranges: pull every summit toward the valley floor by
    (1-k) and smooth the line, so the authored massifs become rounded
    fells — the same forms, without the alpine points."""
    sm = [(x, base - (base - y) * k) for x, y in pts]
    for _ in range(passes):
        sm = ([sm[0]]
              + [(sm[i][0],
                  (sm[i - 1][1] + 2 * sm[i][1] + sm[i + 1][1]) / 4.0)
                 for i in range(1, len(sm) - 1)]
              + [sm[-1]])
    return sm


_MTN_A = _round_fells(_MTN_A, 0.7)
_MTN_B = _round_fells(_MTN_B, 0.7)
_MTN_C = _round_fells(_MTN_C, 0.7)
_MTN_D = _round_fells(_MTN_D, 0.8, passes=3)

_MTND_DAY = "#A9BFAB"           # near fell: a real presence, not haze
_MTNC_DAY, _MTNC_NIGHT = "#C2D5BC", "#213629"     # the grassy spurs
_MTNB_DAY = "#D0DED2"           # the far wall, a step nearer the sky

# The ranges, back to front: colour anchors, aerial-haze factor,
# parallax amplitude, reveal gate, detail level (0 silhouette /
# 1 facets / 2 full crag detail — unused since the fells rounded) and
# snowline: the SPRING remnant line; winter drops it 6 units lower so
# whole tops whiten, and summer/autumn carry no snow at all. None =
# never snowy (the grassy spurs stay green).
_MTN_LAYERS = (
    dict(pts=_MTN_A, jag=_MTN_A, day=_HAZE3_DAY, night=_HAZE3_NIGHT,
         haze=0.30, par=0.55, gate=900, detail=0, snowline=11.5),
    dict(pts=_MTN_B, jag=_MTN_B, day=_MTNB_DAY, night=_HAZE2_NIGHT,
         haze=0.18, par=1.1, gate=620, detail=0, snowline=12.5),
    dict(pts=_MTN_C, jag=_MTN_C, day=_MTNC_DAY, night=_MTNC_NIGHT,
         haze=0.10, par=1.75, gate=470, detail=0, snowline=None),
    dict(pts=_MTN_D, jag=_MTN_D, day=_MTND_DAY, night=_HAZE_NIGHT,
         haze=0.04, par=2.4, gate=490, detail=1, snowline=12.0),
)


def _lerp(c1, c2, t):
    a, b = QColor(c1), QColor(c2)
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def _sun_times(lat, lon, on_date):
    """Local (sunrise, sunset) naive datetimes via the Almanac for
    Computers method — pure math, no network, plenty for a banner."""
    zenith = 90.833
    rad, deg = math.radians, math.degrees

    def calc(rising):
        n = on_date.timetuple().tm_yday
        lng_hour = lon / 15.0
        t = n + ((6 if rising else 18) - lng_hour) / 24.0
        m = (0.9856 * t) - 3.289
        l = (m + 1.916 * math.sin(rad(m)) + 0.020 * math.sin(rad(2 * m))
             + 282.634) % 360
        ra = deg(math.atan(0.91764 * math.tan(rad(l)))) % 360
        ra = (ra + (math.floor(l / 90) * 90 - math.floor(ra / 90) * 90)) / 15.0
        sin_dec = 0.39782 * math.sin(rad(l))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = ((math.cos(rad(zenith)) - sin_dec * math.sin(rad(lat)))
                 / (cos_dec * math.cos(rad(lat))))
        cos_h = max(-1.0, min(1.0, cos_h))
        h = (360 - deg(math.acos(cos_h))) if rising else deg(math.acos(cos_h))
        t_local = h / 15.0 + ra - (0.06571 * t) - 6.622
        return (t_local - lng_hour) % 24

    offset = datetime.now().astimezone().utcoffset() or timedelta()
    base = datetime(on_date.year, on_date.month, on_date.day)
    return (base + timedelta(hours=calc(True)) + offset,
            base + timedelta(hours=calc(False)) + offset)


class LivingBanner(QWidget):

    def __init__(self, latlon_provider=None, parent=None):
        super().__init__(parent)
        self._latlon_provider = latlon_provider
        self._wordmark = True
        self._dark = 0.0
        self._sun_frac = None
        self._moon_frac = None
        self._is_dark = None          # last bool handed to on_dark_changed
        self.on_dark_changed = None   # panel hook: flips label/back/cog colours
        self._band_light = None       # last bool handed to the tab hook
        self.on_band_light_changed = None   # panel hook: restyles the tabs
        self._sun_cache = {}          # (date, lat, lon) -> (sunrise, sunset)
        self._first_walker = True     # first crossing: 20 s in, any species
        self._cycle_minutes = self._load_cycle_minutes()
        self._party = self._load_party()
        self._last_kind = None
        self._parallax_on = self._load_parallax()
        self._scene_on = self._load_scene()
        self._moon_mode = self._load_moon_mode()

        # Weather: mode is off / random / live; _weather is the active state
        # with a strength 0..1 that eases in and out. _live_code caches the
        # last fetched WMO code.
        self._weather_mode = self._load_weather_mode()
        self._manual_state = self._load_manual_state()
        self._pace = self._load_pace()
        self._season_override = self._load_season_override()
        self._weather = "clear"
        self._weather_target = ("clear" if self._weather_mode != "manual"
                                else self._manual_state)
        self._weather_strength = 0.0
        self._weather_until = 0.0
        self._live_code = None
        self._precip = []             # active precipitation particles
        self._drops = []              # raindrops running down the "glass"
        self._flash = 0.0             # lightning flash decay
        self._t = 0.0                 # high-res animation clock (particles)
        self._wclock = 0.0            # slow clock for weather scheduling
        self._live_at = -1e9
        self._force_until = 0.0       # "try next weather" override window
        self._tracks = []             # snow footprints: [x, y, born, layer]
        self._snowcover = 0.0         # lying snow on the ground, 0..1
        self._rainbow = 0.0           # fades in after rain clears
        self._swell = 0.0             # rivers run fuller after rain
        self._flock = None            # starling murmuration state
        self._rings = []              # fish-rise ripples: [x, y, r]
        self._leaves = []             # autumn leaves: [x, y, spin]
        self._shoot = None            # a shooting star, once in a while
        self._seeds = []              # summer thistledown on the wind
        self._moon_lit = 0.0          # tonight's illuminated fraction
        self._eco_debut = True        # first field visit showcases a
                                      # set-piece (boat / hike)

        # Active wildlife (see _make_walker for the dict shape) and any
        # scripted group scenes keyed by group id.
        self._walkers = []
        self._groups = {}
        self._gid = 0

        # Hover interaction: design-space cursor position (or None). An
        # animal under the pointer stops and watches it.
        self._mouse = None
        self.setMouseTracking(True)

        # Parallax: the scene's planes slide a touch as the pointer pans
        # across the banner. _par eases toward _par_t (-1..1, 0 = rest).
        self._par = 0.0
        self._par_t = 0.0
        self._far_birds = None        # a distant skein along the peaks

        # The mountain wall is static geometry under slow colour change,
        # so each range is recorded once per (width, light, weather)
        # step and replayed — the per-frame cost is four blits.
        self._mtn_key = None
        self._mtn_pics = []

        # Season crossfade: a snapshot of the old season fades out over
        # the new one instead of the whole scene recolouring in a frame.
        self._season_shown = None
        self._season_snap = None
        self._season_snap_size = None
        self._season_fade = 0.0
        self._season_force = None

        # Wind (smoothed gust amplitude, weather-driven), the summer
        # swallows, and the static-dressing recording (wall, hedges,
        # gorse, heather — rebuilt only when light/season steps).
        self._wind = 0.35
        self._swallows = []
        self._dress_key = None
        self._dress_pic = None
        self._hedge_pic = None

        # The veteran oak's interaction anchors, set when it is painted
        # (den / PRF roost / perch branch / crown), else None if off-screen.
        self._oak = None

        self._sky_timer = QTimer(self)
        self._sky_timer.setInterval(2000)
        self._sky_timer.timeout.connect(self._sky_tick)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._anim_tick)

        self._sched_timer = QTimer(self)
        self._sched_timer.setSingleShot(True)
        self._sched_timer.timeout.connect(self._start_walker)

        f = QFont("Inter Medium")
        f.setPixelSize(27)
        self._word_font = f

    # ── public ────────────────────────────────────────────────────────
    def set_wordmark_visible(self, visible):
        if self._wordmark != bool(visible):
            self._wordmark = bool(visible)
            self.update()

    @staticmethod
    def _load_cycle_minutes():
        try:
            from qgis.core import QgsSettings
            return max(0, int(QgsSettings().value(_CYCLE_KEY, 0)))
        except Exception:
            return 0

    @staticmethod
    def _load_party():
        try:
            from qgis.core import QgsSettings
            return str(QgsSettings().value(_PARTY_KEY, "false")).lower() == "true"
        except Exception:
            return False

    @staticmethod
    def _load_parallax():
        try:
            from qgis.core import QgsSettings
            return str(QgsSettings().value(_PARALLAX_KEY,
                                           "true")).lower() == "true"
        except Exception:
            return True

    @staticmethod
    def _load_scene():
        try:
            from qgis.core import QgsSettings
            return str(QgsSettings().value(_SCENE_KEY,
                                           "true")).lower() == "true"
        except Exception:
            return True

    @staticmethod
    def _load_moon_mode():
        try:
            from qgis.core import QgsSettings
            v = str(QgsSettings().value(_MOON_KEY, "real")).lower()
            return v if v in ("real", "random") else "real"
        except Exception:
            return "real"

    @staticmethod
    def _load_weather_mode():
        try:
            from qgis.core import QgsSettings
            v = str(QgsSettings().value(_WEATHER_KEY, "off")).lower()
            return (v if v in ("off", "manual", "random", "auto", "live")
                    else "off")
        except Exception:
            return "off"

    @staticmethod
    def _load_manual_state():
        try:
            from qgis.core import QgsSettings
            v = str(QgsSettings().value(_WEATHER_STATE_KEY,
                                        "clear")).lower()
            return v if v in _WEATHERS else "clear"
        except Exception:
            return "clear"

    @staticmethod
    def _load_pace():
        try:
            from qgis.core import QgsSettings
            return max(0, min(100, int(QgsSettings().value(_PACE_KEY, 35))))
        except Exception:
            return 35

    @staticmethod
    def _load_season_override():
        try:
            from qgis.core import QgsSettings
            v = str(QgsSettings().value(_SEASON_KEY, "auto")).lower()
            return v if v in ("auto", "random", "spring", "summer",
                              "autumn", "winter") else "auto"
        except Exception:
            return "auto"

    def weather_mode(self):
        return self._weather_mode

    def weather_state(self):
        return self._weather_target

    def weather_pace(self):
        return self._pace

    def season_mode(self):
        return self._season_override

    def weather_label(self):
        names = {"off": "Off — always clear",
                 "manual": "Pinned by hand",
                 "random": "Random passing weather",
                 "auto": "Auto — your weather now",
                 "live": "Match site — the redline's weather"}
        base = names.get(self._weather_mode, "Off")
        if (self._weather_mode in ("auto", "live", "manual")
                and self._weather_target != "clear"):
            base += f" ({self._weather_target})"
        if self._season_override != "auto":
            base += f" · {self._season_override}"
        return base

    def set_weather_mode(self, mode):
        """off = never; manual = one chosen state, eased in; random =
        occasional passing fronts; auto = the machine's own weather
        (IP-located); live = the site's weather at the redline. Auto and
        live fetch in the background and refresh hourly."""
        self._weather_mode = (mode if mode in ("off", "manual", "random",
                                               "auto", "live") else "off")
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_WEATHER_KEY, self._weather_mode)
        except Exception:
            pass
        self._force_until = 0.0
        if self._weather_mode == "off":
            self._weather_target = "clear"
        elif self._weather_mode == "manual":
            self._weather_target = self._manual_state
        elif self._weather_mode in ("auto", "live"):
            self._fetch_live_weather()
        else:
            self._weather_until = 0.0     # force a fresh random roll
        if self.isVisible() and not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def set_weather_state(self, state):
        """Manual weather: choose the state; the sky EASES over to it —
        the old front fades out, the new one builds in."""
        if state not in _WEATHERS:
            return
        self._manual_state = state
        self._weather_mode = "manual"
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_WEATHER_KEY, "manual")
            QgsSettings().setValue(_WEATHER_STATE_KEY, state)
        except Exception:
            pass
        self._force_until = 0.0
        self._weather_target = state
        if self.isVisible() and not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def set_weather_pace(self, pace):
        """How changeable random weather is: 0 = settled, 100 = wild."""
        self._pace = max(0, min(100, int(pace)))
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_PACE_KEY, self._pace)
        except Exception:
            pass
        self._weather_until = min(self._weather_until,
                                  self._wclock + 10)

    def set_season(self, season):
        """auto = follow the real calendar; random = a different season
        each day; otherwise pin a season."""
        self._season_override = (season if season in
                                 ("auto", "random", "spring", "summer",
                                  "autumn", "winter") else "auto")
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_SEASON_KEY, self._season_override)
        except Exception:
            pass
        self._season_check()          # crossfade rather than hard cut
        self.update()

    def cycle_minutes(self):
        return self._cycle_minutes

    def set_cycle_minutes(self, minutes):
        """0 = follow the real sun; otherwise a full virtual day lasts this
        many minutes (5-min demo loop up to slow ambience)."""
        self._cycle_minutes = max(0, int(minutes))
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_CYCLE_KEY, self._cycle_minutes)
        except Exception:
            pass
        self._sky_timer.setInterval(self._sky_interval())
        self._sky_tick()

    def party(self):
        return self._party

    def set_party(self, on):
        """Party mode: a constant parade — several animals at once, any
        species at any hour, encounters more likely."""
        self._party = bool(on)
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_PARTY_KEY, "true" if self._party else "false")
        except Exception:
            pass
        if self._party and self.isVisible():
            self._sched_timer.start(800)

    def parallax_enabled(self):
        return self._parallax_on

    def set_parallax_enabled(self, on):
        """Pointer parallax: the scenery planes slide a little as the
        cursor pans across the banner. Off pins every plane at rest."""
        self._parallax_on = bool(on)
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_PARALLAX_KEY,
                                   "true" if self._parallax_on else "false")
        except Exception:
            pass
        self._par_t = 0.0             # glide back to rest either way
        if self.isVisible() and not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def scene_enabled(self):
        return self._scene_on

    def set_scene_enabled(self, on):
        """The living scene itself. Off paints a plain, quiet header —
        flat ground colour, static wordmark, no wildlife, no timers —
        for anyone who wants the plugin without the countryside."""
        on = bool(on)
        if on == self._scene_on:
            return
        self._scene_on = on
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_SCENE_KEY, "true" if on else "false")
        except Exception:
            pass
        if not on:
            self._sky_timer.stop()
            self._anim_timer.stop()
            self._sched_timer.stop()
            self._walkers = []
            self._groups = {}
            self._precip = []
            self._drops = []
            self._swallows = []
            self._seeds = []
            self._rings = []
            self._leaves = []
            self._flock = None
            self._far_birds = None
            self._shoot = None
            self._par = self._par_t = 0.0
            # The flat header is dark ground: flip the chrome to its
            # dark treatment and let the tab strip use light labels.
            # (The flags track it so re-enabling re-syncs correctly.)
            self._is_dark, self._band_light = True, False
            if callable(getattr(self, "on_dark_changed", None)):
                self.on_dark_changed(True)
            if callable(getattr(self, "on_band_light_changed", None)):
                self.on_band_light_changed(False)
        elif self.isVisible():
            self._sky_timer.setInterval(self._sky_interval())
            self._sky_tick()
            self._sky_timer.start()
            self._anim_timer.start()
            self._sched_timer.start(1200)
        self.update()

    def moon_mode(self):
        return self._moon_mode

    def set_moon_mode(self, mode):
        """real = tonight's actual lunar phase; random = a different
        (date-seeded) phase each night, for variety's sake."""
        self._moon_mode = mode if mode in ("real", "random") else "real"
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_MOON_KEY, self._moon_mode)
        except Exception:
            pass
        self.update()

    # ── weather ───────────────────────────────────────────────────────
    @staticmethod
    def _code_to_weather(code):
        try:
            c = int(code)
        except (TypeError, ValueError):
            return "clear"
        if c in (0, 1):
            return "clear"
        if c in (2, 3):
            return "cloud"
        if c in (45, 48):
            return "fog"
        if c in (71, 73, 75, 77, 85, 86):
            return "snow"
        if c in (95, 96, 99):
            return "storm"
        if c >= 51:
            return "rain"
        return "clear"

    def _ip_latlon(self):
        """Rough machine location by IP (worker thread only; cached 6 h).
        None when every lookup fails."""
        cached = getattr(self, "_ip_ll", None)
        if cached and time.time() - cached[1] < 6 * 3600:
            return cached[0]
        import json
        from urllib.request import urlopen, Request
        for url, la_k, lo_k in (
                ("https://ipapi.co/json/", "latitude", "longitude"),
                ("http://ip-api.com/json/?fields=lat,lon", "lat", "lon")):
            try:
                req = Request(url,
                              headers={"User-Agent": "BiomeQGISPlugin/1.0"})
                with urlopen(req, timeout=8) as r:
                    data = json.load(r)
                ll = (float(data[la_k]), float(data[lo_k]))
                self._ip_ll = (ll, time.time())
                return ll
            except Exception:
                continue
        return None

    def _fetch_live_weather(self):
        """Grab the current WMO code from Open-Meteo on a daemon thread —
        at the machine's own IP-located position (mode "auto") or the
        site's redline (mode "live"); apply on the GUI thread. Silent on
        any failure (auto falls back to the site coords)."""
        mode = self._weather_mode
        lat, lon = self._latlon()

        def work():
            code = None
            try:
                import json
                from urllib.request import urlopen, Request
                la, lo = lat, lon
                if mode == "auto":
                    ll = self._ip_latlon()
                    if ll:
                        la, lo = ll
                url = ("https://api.open-meteo.com/v1/forecast?"
                       f"latitude={la:.3f}&longitude={lo:.3f}"
                       "&current=weather_code")
                req = Request(url, headers={"User-Agent": "BiomeQGISPlugin/1.0"})
                with urlopen(req, timeout=8) as r:
                    data = json.load(r)
                code = data.get("current", {}).get("weather_code")
            except Exception:
                return

            def apply():
                if self._weather_mode not in ("auto", "live"):
                    return
                self._live_code = code
                self._weather_target = self._code_to_weather(code)
            try:
                QTimer.singleShot(0, apply)
            except Exception:
                pass

        try:
            import threading
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def force_next_weather(self):
        """Preview helper: jump straight to the next weather state and
        hold it briefly, whatever the mode. Returns the new state."""
        order = ("clear", "cloud", "rain", "storm", "snow", "fog")
        try:
            i = order.index(self._weather_target)
        except ValueError:
            i = 0
        self._weather_target = order[(i + 1) % len(order)]
        if self._weather_mode == "manual":
            self._manual_state = self._weather_target
            try:
                from qgis.core import QgsSettings
                QgsSettings().setValue(_WEATHER_STATE_KEY,
                                       self._manual_state)
            except Exception:
                pass
        self._force_until = self._wclock + 40
        self._weather_until = self._wclock + 45
        if self.isVisible() and not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()
        return self._weather_target

    def _weather_logic(self):
        """Pick the target weather state. Called from the sky tick."""
        if self._wclock < self._force_until:
            return                    # a forced preview state is running
        if self._weather_mode == "off":
            self._weather_target = "clear"
            return
        if self._weather_mode == "manual":
            self._weather_target = self._manual_state
            return
        if self._weather_mode in ("auto", "live"):
            if self._wclock - self._live_at > 3600:
                self._live_at = self._wclock
                self._fetch_live_weather()
            return
        # random: passing fronts, paced by the changeability setting
        # (0 = settled fen drizzle-free days, 100 = wild hill weather).
        f = 2.0 - 1.75 * (self._pace / 100.0)
        if self._wclock >= self._weather_until:
            if self._weather_target == "clear":
                self._weather_target = random.choice(
                    ("rain", "rain", "cloud", "fog", "snow", "storm"))
                self._weather_until = (self._wclock
                                       + random.uniform(25, 60) * f)
            else:
                self._weather_target = "clear"
                self._weather_until = (self._wclock
                                       + random.uniform(40, 120) * f)

    def _step_weather(self, dt):
        """Ease strength toward the target and animate particles."""
        if self._weather != self._weather_target and self._weather_strength <= 0.02:
            self._weather = self._weather_target        # swap once faded out
        target_strength = 1.0 if self._weather == self._weather_target else 0.0
        self._weather_strength += (target_strength - self._weather_strength) \
            * min(1.0, dt * 0.7)

        want = self._weather in ("rain", "snow", "storm")
        wd = self._design_width()
        bot = self._design_bot()
        density = {"rain": 0.9, "storm": 1.3, "snow": 0.55}.get(self._weather, 0)
        cap = int(wd * density * self._weather_strength / 12)
        if want:
            wind = 5.5 if self._weather != "snow" else 1.4
            vy = 62 if self._weather != "snow" else 15
            while len(self._precip) < cap:
                # sp doubles as the particle's depth: most drops hang
                # back small and slow, a few fall big and fast right in
                # front of the glass. Size, speed, slant and alpha all
                # ride on it, which is what layers the rain.
                self._precip.append([random.uniform(0, wd),
                                     random.uniform(0, bot),
                                     0.55 + 1.15 * random.random() ** 1.7])
            for pt in self._precip:
                pt[1] += vy * pt[2] * dt
                pt[0] += wind * pt[2] * dt
                if self._weather == "snow":
                    pt[0] += 3.0 * math.sin(self._t * 1.7 + pt[1] * 0.3) * dt
                if pt[1] > bot:
                    pt[1] = -2
                    pt[0] = random.uniform(0, wd)
        if len(self._precip) > cap:
            self._precip = self._precip[:max(0, cap)]
        if self._weather == "storm" and self._weather_strength > 0.4:
            if random.random() < dt * 0.35:
                self._flash = 1.0
        self._flash = max(0.0, self._flash - dt * 3.5)
        # The odd raindrop lands on the "glass" and runs down it, car-
        # window style: stick-slip descent, a slight wander, growing as
        # it sweeps up water, gone off the bottom edge. Straggler drops
        # keep running after the front clears.
        if (self._weather in ("rain", "storm")
                and self._weather_strength > 0.4 and len(self._drops) < 3
                and random.random() < dt * (0.20 if self._weather == "storm"
                                            else 0.10)):
            self._drops.append({
                "x": random.uniform(3.0, wd - 3.0),
                "y": random.uniform(1.0, bot * 0.45),
                "y0": None, "size": random.uniform(0.75, 1.25),
                "ph": random.uniform(0.0, 6.28), "age": 0.0})
        for dr in self._drops:
            dr["age"] += dt
            if dr["y0"] is None:
                dr["y0"] = dr["y"]
            # Stick-slip: the drop hangs, then breaks loose and slides.
            slip = max(0.0, math.sin(self._t * (0.8 + dr["ph"] * 0.1)
                                     + dr["ph"])) ** 2
            dr["y"] += (2.0 + 26.0 * slip * dr["size"]) * dt
            dr["x"] += 0.8 * math.sin(self._t * 0.9 + dr["ph"] * 1.7) * dt
            dr["size"] = min(1.7, dr["size"] + dt * 0.03)
        self._drops = [dr for dr in self._drops if dr["y"] < bot + 2.0]
        # Rain leaves things behind it: a rainbow as the front clears,
        # and rivers that run fuller for a while.
        if (self._weather in ("rain", "storm")
                and self._weather_target == "clear"
                and self._weather_strength < 0.35 and self._rainbow <= 0):
            self._rainbow = 1.0
        self._rainbow = max(0.0, self._rainbow - dt / 18.0)
        if self._weather in ("rain", "storm") and self._weather_strength > 0.5:
            self._swell = min(1.0, self._swell + dt / 30.0)
        else:
            self._swell = max(0.0, self._swell - dt / 70.0)
        # Falling snow settles into ground cover; once the front passes
        # it lingers a while, melting faster if rain moves in.
        if self._weather == "snow" and self._weather_strength > 0.3:
            self._snowcover = min(1.0, self._snowcover + dt / 22.0)
        else:
            melt = 45.0 if self._weather in ("rain", "storm") else 90.0
            self._snowcover = max(0.0, self._snowcover - dt / melt)
        return (want or self._weather_strength > 0.02
                or self._snowcover > 0.0 or bool(self._drops))

    def _weather_active(self):
        return (self._weather_strength > 0.02
                or self._weather != self._weather_target
                or bool(self._precip) or self._snowcover > 0.0
                or bool(self._drops))

    def _sky_interval(self):
        # Fast demo cycles need frequent ticks or the sun visibly steps;
        # _paint also samples the sky live, so ticks just drive repaints.
        m = self._cycle_minutes
        if m == 0:
            return 2000
        return max(60, min(2000, m * 60 * 1000 // 3600))

    def _now(self):
        """Scene clock: real time, or a compressed virtual day anchored to
        the wall clock so every dock shows the same moment."""
        if self._cycle_minutes <= 0:
            return datetime.now()
        span = self._cycle_minutes * 60.0
        frac = (time.time() % span) / span
        real = datetime.now()
        midnight = datetime(real.year, real.month, real.day)
        return midnight + timedelta(seconds=frac * 86400.0)

    def _moon_phase(self):
        """Tonight's lunar phase, 0..1 (0 = new, 0.5 = full). "real"
        mode anchors to the 2000-01-06 18:14 UTC new moon — good to a
        few hours, so the banner shows the same moon the bat surveyors
        planned around. "random" mode deals a different (date-seeded)
        phase each night instead, for variety."""
        if self._moon_mode == "random":
            n = math.sin(self._now().toordinal() * 9.173) * 43758.5453
            return n - math.floor(n)
        days = (self._now()
                - datetime(2000, 1, 6, 18, 14)).total_seconds() / 86400.0
        return (days / 29.530588853) % 1.0

    # ── lifecycle ─────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        if not self._scene_on:
            # Plain-header mode: nothing moves, nothing schedules.
            return
        self._sky_timer.setInterval(self._sky_interval())
        if self._weather_mode in ("auto", "live"):
            self._fetch_live_weather()
        self._sky_tick()
        self._sky_timer.start()
        if self._weather_active():
            self._anim_timer.start()
        if not self._sched_timer.isActive():
            if self._first_walker:
                self._sched_timer.start(20 * 1000)
            else:
                self._sched_timer.start(int(self._next_delay() * 1000))

    def hideEvent(self, e):
        super().hideEvent(e)
        self._sky_timer.stop()
        self._anim_timer.stop()
        self._sched_timer.stop()
        self._walkers = []
        self._groups = {}
        self._precip = []
        self._drops = []
        self._tracks = []
        self._flock = None
        self._rings = []
        self._leaves = []
        self._mouse = None
        self._far_birds = None
        self._swallows = []
        self._shoot = None
        self._seeds = []
        self._par = self._par_t = 0.0

    def mouseMoveEvent(self, e):
        try:
            px, py = e.position().x(), e.position().y()   # Qt6
        except AttributeError:
            px, py = float(e.x()), float(e.y())           # Qt5
        s = _H / _DESIGN_H
        self._mouse = (px / s, py / s)
        # Parallax target: which side of the banner the pointer is on.
        if self._parallax_on:
            self._par_t = (px / max(1.0, float(self.width())) - 0.5) * 2.0
        if (self._scene_on and not self._anim_timer.isActive()
                and self.isVisible()):
            self._anim_timer.start()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._mouse = None
        self._par_t = 0.0             # planes glide back to rest
        if (self._scene_on and not self._anim_timer.isActive()
                and self.isVisible()):
            self._anim_timer.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        # A click startles whatever is under the cursor: it breaks off
        # whatever it was doing and bolts away from you.
        if self._mouse:
            mx = self._mouse[0]
            for wk in self._walkers:
                if wk["flying"] or wk["group"] is not None:
                    continue
                span = wk["w"] * wk["scale"]
                cx = wk["x"] + span / 2
                if abs(mx - cx) < span * 0.9 + 6:
                    wk["dir"] = -1 if mx > cx else 1
                    wk["_face"] = float(wk["dir"])   # a startle IS abrupt
                    wk["pause_t"] = 0.0
                    wk["pause_x"], wk["pause_done"] = None, True
                    wk["pose"] = "rest"
                    wk["speed"] = wk["base_speed"] * 1.9
                    wk["wander"] = 0
            self.update()
        super().mousePressEvent(e)

    # ── sun / clock ───────────────────────────────────────────────────
    def _latlon(self):
        now = datetime.now()
        cached = getattr(self, "_ll_cache", None)
        if cached and (now - cached[1]).total_seconds() < 120:
            return cached[0]
        try:
            ll = self._latlon_provider() if self._latlon_provider else None
        except Exception:
            ll = None
        if not ll or not (-90 < ll[0] < 90):
            ll = _FALLBACK_LATLON
        self._ll_cache = (ll, now)
        return ll

    def _times(self, now):
        lat, lon = self._latlon()
        key = (now.date(), round(lat, 2), round(lon, 2))
        if key not in self._sun_cache:
            self._sun_cache.clear()
            try:
                self._sun_cache[key] = _sun_times(lat, lon, now.date())
            except Exception:
                base = datetime(now.year, now.month, now.day)
                self._sun_cache[key] = (base + timedelta(hours=6),
                                        base + timedelta(hours=20))
        return self._sun_cache[key]

    def _sky_state(self, now):
        """(darkness 0..1, sun_frac or None, moon_frac or None) — fracs are
        progress across the banner. 25-minute twilight ramps each side."""
        sunrise, sunset = self._times(now)
        if sunrise <= now <= sunset:
            edge = min((now - sunrise).total_seconds(),
                       (sunset - now).total_seconds()) / 60.0
            dark = max(0.0, 0.5 - edge / 50.0)
            frac = ((now - sunrise).total_seconds()
                    / max(1.0, (sunset - sunrise).total_seconds()))
            return dark, frac, None
        if now > sunset:
            mins = (now - sunset).total_seconds() / 60.0
            night_len = 24 * 3600 - (sunset - sunrise).total_seconds()
            frac = (now - sunset).total_seconds() / max(1.0, night_len)
        else:
            mins = (sunrise - now).total_seconds() / 60.0
            night_len = 24 * 3600 - (sunset - sunrise).total_seconds()
            frac = 1.0 - (sunrise - now).total_seconds() / max(1.0, night_len)
        dark = min(1.0, 0.5 + mins / 50.0)
        return dark, None, max(0.0, min(1.0, frac))

    def _sky_tick(self):
        self._wclock += self._sky_timer.interval() / 1000.0
        self._season_check()          # catches the calendar ticking over
        self._weather_logic()
        if self._weather_active() and not self._anim_timer.isActive():
            self._anim_timer.start()
        now = self._now()
        self._dark, self._sun_frac, self._moon_frac = self._sky_state(now)
        is_dark = self._dark > 0.5
        if is_dark != self._is_dark:
            self._is_dark = is_dark
            if self.on_dark_changed:
                try:
                    self.on_dark_changed(is_dark)
                except Exception:
                    pass
        # The tabs restyle whenever the ground behind them flips
        # between dark grass and light snow.
        band_light = self.band_is_light()
        if band_light != self._band_light:
            self._band_light = band_light
            if self.on_band_light_changed:
                try:
                    self.on_band_light_changed(band_light)
                except Exception:
                    pass
        self.update()

    # ── wildlife scheduling ───────────────────────────────────────────
    def _next_delay(self):
        if self._party:
            return random.uniform(2.5, 7.0)
        if 0 < self._cycle_minutes <= 10:
            return random.uniform(15, 40)      # demo loop: keep it lively
        return random.uniform(45, 150)

    def _phase(self, now):
        sunrise, sunset = self._times(now)
        if sunset - timedelta(minutes=15) <= now <= sunset + timedelta(minutes=45):
            return "dusk"
        if sunrise - timedelta(minutes=45) <= now <= sunrise + timedelta(minutes=15):
            return "dawn"
        if sunrise < now < sunset:
            return "day"
        return "night"

    def _season(self):
        force = getattr(self, "_season_force", None)
        if force is not None:
            return force              # transient: the crossfade snapshot
        if self._season_override == "random":
            # Date-seeded like the random moon: a different season each
            # day, stable within the day so nothing flickers mid-frame.
            n = math.sin(self._now().toordinal() * 7.919) * 43758.5453
            return ("spring", "summer", "autumn",
                    "winter")[int((n - math.floor(n)) * 4) % 4]
        if self._season_override != "auto":
            return self._season_override
        m = self._now().month
        if m in (3, 4, 5):
            return "spring"
        if m in (6, 7, 8):
            return "summer"
        if m in (9, 10, 11):
            return "autumn"
        return "winter"

    def _season_check(self):
        """Cross-dissolve on season change: the frame the season flips
        (calendar tick or user override), grab one last rendering of
        the OLD season — _season_force pins it while grab() re-renders
        — and fade that snapshot out over the new season's paint."""
        s_now = self._season()
        shown = getattr(self, "_season_shown", None)
        if shown is None:
            self._season_shown = s_now
            return
        if s_now == shown:
            return
        self._season_shown = s_now
        if not self.isVisible():
            return
        self._season_force = shown
        try:
            self._season_snap = self.grab()
            self._season_snap_size = (self.width(), self.height())
            self._season_fade = 1.0
        except Exception:
            self._season_snap = None
            self._season_fade = 0.0
        finally:
            self._season_force = None
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _season_gate(self, kind):
        """Cold-blooded and season-bound species swap out of season —
        no snakes, newts or insects abroad in a UK winter."""
        s = self._season()
        if kind == "dragonfly" and s != "summer":
            return "bee" if s != "winter" else "hare"
        if s == "winter" and kind in ("grass_snake", "adder", "gcn",
                                      "stag_beetle", "bee"):
            return {"grass_snake": "fox", "adder": "hare", "gcn": "otter",
                    "stag_beetle": "badger", "bee": "hare"}[kind]
        return kind

    def _pick_species(self, now):
        roster = _ROSTER[self._phase(now)]
        kind = random.choice(roster)
        if kind == self._last_kind and len(roster) > 1:
            kind = random.choice([k for k in roster if k != kind])
        return kind

    def band_is_light(self):
        """Whether the ground painted behind the tab strip is currently
        LIGHT (a snowy winter day) or dark (grass, night) — the panel
        restyles the tabs to stay readable either way."""
        if not self._scene_on:
            return False              # the plain header is dark ground
        d = self._dark
        season = self._season()
        front_c = QColor(_FRONT)
        if season == "winter":
            front_c = _lerp(front_c.name(),
                            "#6E7C55" if d < 0.5 else "#28372E", 0.62)
        elif season == "autumn":
            front_c = _lerp(front_c.name(), "#4C6A3C", 0.2)
        cover = self._snowcover
        if cover > 0.0:
            snow_c = _lerp("#EDF2F3", "#3A4849", d)
            front_c = _lerp(front_c.name(), snow_c.name(), 0.85 * cover)
        lum = (0.299 * front_c.red() + 0.587 * front_c.green()
               + 0.114 * front_c.blue()) / 255.0
        return lum > 0.55

    def _wind_at(self, x):
        """The gust field: sway factor (roughly -1..1 × amplitude) at
        design-x right now. Two travelling waves under a slow gustiness
        envelope, so squalls sweep visibly across the scene with calm
        between them; amplitude follows the weather."""
        t = self._t
        a = math.sin(x * 0.016 - t * 1.9)
        b = math.sin(x * 0.006 - t * 0.53 + 1.7)
        g = 0.5 + 0.5 * math.sin(t * 0.21 + x * 0.002)
        return self._wind * g * (0.65 * a + 0.35 * b)

    def _design_width(self):
        return self.width() / (_H / _DESIGN_H)

    def _design_bot(self):
        """Design-space bottom edge. Equals _DESIGN_H at the classic banner
        height; larger when the widget extends below the scene (the
        foreground grass carries on down behind the tab strip)."""
        return max(_DESIGN_H, self.height() / (_H / _DESIGN_H))

    def _make_walker(self, kind, direction=1, scale_mult=1.0, x=None,
                     layer=None, allow_pause=True, group=None):
        spec = _SPEC[kind]
        wd = self._design_width()
        if layer is None:
            if kind in _LAKEFOLK:
                layer = "mid"          # swimmers and waders keep to the lakes
            elif kind == "stag_beetle":
                layer = "fore"         # big beetle trundling right up close
            elif spec["flying"]:
                layer = "mid"
            else:
                layers, weights = _LAYER_W.get(kind, (("mid",), (1.0,)))
                layer = random.choices(layers, weights)[0]
        # Depth: swimmers, the heron and the ecologist keep to their lake
        # depth; everyone else takes a continuous depth in their layer's
        # band and will drift within it.
        depth_lock = (kind in _LAKEFOLK or kind == "ecologist"
                      or spec["flying"])
        if layer == "back":
            depth = random.uniform(0.12, 0.30)
        elif layer == "fore":
            depth = random.uniform(0.74, 0.92)
        else:
            depth = random.uniform(0.42, 0.62)
        if spec["flying"]:
            sc = 1.0 * scale_mult
        elif depth_lock:
            sc = _LAYER_SCALE[layer] * scale_mult
        else:
            sc = self._depth_scale(depth) * scale_mult
        if kind == "gcn":
            sc *= 0.72               # great crested newts are little
        if x is None:
            x = (-spec["w"] * sc - 6.0) if direction > 0 else wd + 6.0
        pause_x = None
        pose = "rest"
        sip_y = None
        errand = False
        if allow_pause and not spec["flying"]:
            if kind == "heron":
                # The heron stalks to a lake shore and fishes — the shore
                # it reaches first, so the strike lands in the water. Now
                # and then it simply roosts on one leg instead.
                shores = [(lx - lrx - 9 if direction > 0 else lx + lrx + 2)
                          for lx, lrx, _r, _m in _LAKES if lx + lrx < wd - 4]
                if shores and random.random() < 0.7:
                    pause_x = random.choice(shores)
                    pose = "fish"
                else:
                    pause_x = wd * random.uniform(0.35, 0.65)
                    pose = "oneleg"
            # Rivers are drinking spots: stop at the bank, head down.
            rivers = [rx for rx in (_STREAM_X, _RIVER_X, _RIVER2_X)
                      if rx + 14 < wd]
            if (pause_x is None and rivers and layer == "mid"
                    and kind not in _SWIMMERS and kind != "stag_beetle"
                    and random.random() < 0.30):
                random.shuffle(rivers)
                for rx in rivers:
                    px = (rx - spec["w"] * sc - 1.5 if direction > 0
                          else rx + 1.5)
                    # Skip banks that put the drinker in a lake.
                    if self._lake_at(px + spec["w"] * sc / 2, wd) is None:
                        pause_x = px
                        pose = "drink"
                        errand = True
                        break
            # Named errands read as intended trips: grazers make for the
            # wildflower meadow, and the oak's regulars go to the oak.
            if pause_x is None and layer == "mid":
                spots = []
                if (kind in ("deer", "hare", "pheasant", "badger", "hog",
                             "fox")
                        and _MEADOW[0] + 12 < wd - 8):
                    spots += ["meadow", "meadow"]
                oak = getattr(self, "_oak", None)
                if (kind in ("fox", "badger", "hog") and oak is not None
                        and oak["base"][0] + 10 < wd - 8):
                    spots.append("oak")
                if spots and random.random() < 0.55:
                    span = spec["w"] * sc
                    if random.choice(spots) == "meadow":
                        px = (random.uniform(_MEADOW[0] + 6, _MEADOW[1] - 6)
                              - span / 2)
                    else:
                        px = (oak["base"][0] - span / 2
                              + random.uniform(-2.0, 2.0))
                    if self._lake_at(px + span / 2, wd) is None:
                        pause_x = px
                        pose = random.choice(_IDLE_POSES.get(kind, ("rest",)))
                        errand = True
            if pause_x is None and random.random() < 0.7:
                pause_x = wd * random.uniform(0.30, 0.70)
                if layer == "mid" and kind not in _LAKE_PAUSERS:
                    pause_x = self._avoid_lake(pause_x, wd)
                pose = random.choice(_IDLE_POSES.get(kind, ("rest",)))
        # Bees make a beeline for the bluebells (or the meadow) and stop
        # to drink at a flower head — and sometimes they inspect the
        # leaf on the bıome wordmark itself.
        if kind == "bee":
            spots = []
            if (_BLUEBELLS[0] + 8 < wd - 6
                    and self._season() in ("spring", "summer")):
                spots += [random.uniform(_BLUEBELLS[0] + 2,
                                         _BLUEBELLS[1] - 2)] * 2
            if _MEADOW[0] + 10 < wd - 6:
                spots.append(random.uniform(_MEADOW[0] + 4, _MEADOW[1] - 4))
            leaf_x = getattr(self, "_leaf_x", None)
            if leaf_x is not None and self._wordmark:
                spots.append(-leaf_x)          # negative marks a leaf visit
            if spots and random.random() < 0.85:
                px = random.choice(spots)
                pose = "sip"
                if px < 0:
                    pause_x, sip_y = -px, 12.6
                else:
                    pause_x = px
                    sip_y = self._ridge_y(_FRONT_PTS, wd, pause_x) - 2.2
        # Dragonflies stitch over the water, hanging in the air mid-beat.
        if kind == "dragonfly":
            pools = [lx for lx, lrx, lry, _m in _LAKES
                     if lx + lrx < wd - 6]
            if pools and random.random() < 0.8:
                pause_x = random.choice(pools) + random.uniform(-8, 8)
                pose = "hover"
        # A kingfisher passing a lake sometimes dips into a fishing dive
        # — never in winter, when the ponds are frozen over.
        dive = None
        if kind == "kingfisher" and self._season() != "winter":
            pools = [(lx, lrx, lry) for lx, lrx, lry, _m in _LAKES
                     if lx - lrx > 24 and lx + lrx < wd - 10]
            if pools and random.random() < 0.65:
                lx, lrx, lry = random.choice(pools)
                ly, _ry = self._lake_geom(lx, lry, wd)
                dive = (lx, ly - 1.0)
        # Stag beetles sometimes clatter into clumsy flight mid-crossing.
        takeoff = None
        if kind == "stag_beetle" and random.random() < 0.4:
            takeoff = wd * random.uniform(0.35, 0.75)
        # Foraging flight: many flyers hawk back and forth over a patch
        # for a while — circling as if hunting insects — before moving on.
        forage_t, forage_cx, forage_r = 0.0, 0.0, 0.0
        if (spec["flying"] and kind in ("bat", "owl", "buzzard", "kite")
                and random.random() < 0.6):
            forage_t = random.uniform(4.5, 9.0)
            forage_cx = wd * random.uniform(0.3, 0.7)
            forage_r = max(24.0, wd * random.uniform(0.12, 0.20))
        jitter = (random.uniform(0.85, 1.2) if self._party
                  else random.uniform(0.9, 1.12))
        wander = (random.randint(1, 3)
                  if allow_pause and not spec["flying"] else 0)
        return dict(kind=kind, x=float(x), dir=direction, layer=layer,
                    scale=sc, gait=random.uniform(0, 6.28),
                    speed=spec["speed"] * jitter,
                    base_speed=spec["speed"] * jitter,
                    gait_hz=spec["gait_hz"], w=spec["w"], h=spec["h"],
                    flying=spec["flying"], pause_x=pause_x, pause_t=0.0,
                    pause_total=1.0, pause_done=pause_x is None,
                    pose=pose, group=group, wander=wander, dive=dive,
                    sip_y=sip_y, takeoff=takeoff, stalk=(kind == "heron"),
                    forage_t=forage_t, forage_cx=forage_cx,
                    forage_r=forage_r, depth=depth, depth_lock=depth_lock,
                    depth_t=depth, errand=errand, home_x=None,
                    homebound=False, depart_t=0.0, _pause_len=None)

    def _spawn_encounter(self, phase):
        """A scripted two-animal scene. Returns True if one was spawned."""
        options = _ENCOUNTERS.get(phase, ())
        if not options:
            return False
        kind = random.choice(options)
        self._gid += 1
        gid = self._gid
        wd = self._design_width()
        if kind == "boxing":
            # Two hares meet head-on mid-banner and box.
            a = self._make_walker("hare", 1, allow_pause=False, group=gid)
            b = self._make_walker("hare", -1, allow_pause=False, group=gid,
                                  layer=a["layer"])
            b["scale"] = a["scale"]
            self._walkers += [a, b]
            self._groups[gid] = {"kind": "boxing", "done": False}
        elif kind == "chase":
            hare = self._make_walker("hare", 1, allow_pause=False, group=gid)
            fox = self._make_walker("fox", 1, allow_pause=False, group=gid,
                                    layer=hare["layer"], x=hare["x"] - 55)
            fox["scale"] = hare["scale"]
            fox["speed"] = fox["base_speed"] = hare["base_speed"] * 1.12
            self._walkers += [hare, fox]
            self._groups[gid] = {"kind": "chase", "burst": False}
        elif kind == "deer_pair":
            # A stag leads, a hind follows.
            a = self._make_walker("deer", 1, group=gid)
            a["stag"] = True
            a["scale"] *= 1.06
            b = self._make_walker("deer", 1, group=gid, layer=a["layer"],
                                  scale_mult=0.85, x=a["x"] - 36)
            b["speed"] = b["base_speed"] = a["base_speed"]
            # They pause together to graze — at the meadow when it's on
            # screen, so the walk has a visible reason.
            if _MEADOW[0] + 12 < wd - 8 and random.random() < 0.6:
                px = random.uniform(_MEADOW[0] + 6, _MEADOW[1] - 12)
            else:
                px = wd * random.uniform(0.35, 0.65)
            for wk in (a, b):
                wk["pause_x"], wk["pause_done"] = px, False
                wk["pose"] = "graze"
            b["pause_x"] = px - 30 * a["scale"]   # hind stops short of him
            self._walkers += [a, b]
            self._groups[gid] = {"kind": "deer_pair"}
        elif kind == "converge":
            # Two deer come in from opposite edges and meet to graze the
            # same spot, then part again the way each one came.
            a = self._make_walker("deer", 1, allow_pause=False, group=gid)
            b = self._make_walker("deer", -1, allow_pause=False, group=gid,
                                  layer=a["layer"])
            b["scale"] = a["scale"]
            b["depth"] = b["depth_t"] = a["depth"]
            if random.random() < 0.5:
                a["stag"] = True
                a["scale"] *= 1.06
            if _MEADOW[0] + 12 < wd - 8 and random.random() < 0.6:
                px = random.uniform(_MEADOW[0] + 10, _MEADOW[1] - 10)
            else:
                px = self._avoid_lake(wd * random.uniform(0.38, 0.62), wd)
            span = a["w"] * a["scale"]
            a["pause_x"] = px - span - 2.0
            b["pause_x"] = px + 2.0
            for wk in (a, b):
                wk["pause_done"] = False
                wk["pose"] = "graze"
                wk["_pause_len"] = (4.5, 8.0)
            self._walkers += [a, b]
            self._groups[gid] = {"kind": "converge"}
        elif kind == "badger_cub":
            a = self._make_walker("badger", 1, group=gid)
            cub = self._make_walker("badger", 1, group=gid, layer=a["layer"],
                                    scale_mult=0.62, x=a["x"] - 26)
            cub["speed"] = cub["base_speed"] = a["base_speed"]
            cub["gait_hz"] = a["gait_hz"] * 1.5    # little legs hurry
            if a["pause_x"] is not None:
                cub["pause_x"], cub["pause_done"] = a["pause_x"], False
                cub["pose"] = a["pose"]
            self._walkers += [a, cub]
            self._groups[gid] = {"kind": "badger_cub"}
        else:
            return False
        return True

    def _spawn_den_dweller(self, kind, den):
        """A fox or badger that already lives here: it wakes curled in
        its den (the ring-o, or the oak earth), trots out on an errand —
        a drink at a river, a forage at the meadow — then comes home,
        curls up again, and finally slips away. The whole trip is one
        intention, never an edge-to-edge crossing."""
        wd = self._design_width()
        wk = self._make_walker(kind, direction=1, layer="mid",
                               allow_pause=False)
        if den == "oak" and self._oak is not None:
            den_x = self._oak["base"][0]
            wk["_den_at"] = "oak"
        else:
            den_x = self._den_cx
        span = wk["w"] * wk["scale"]
        home = den_x - span / 2
        # Pick the errand before setting off, so the journey has a
        # destination from its very first step.
        targets = []
        for rx in (_STREAM_X, _RIVER_X, _RIVER2_X):
            if rx + 14 < wd and abs(rx - den_x) > 30:
                px = rx - span - 1.5 if rx > den_x else rx + 1.5
                if self._lake_at(px + span / 2, wd) is None:
                    targets.append((px, "drink"))
        if _MEADOW[0] + 12 < wd - 8 and abs(_MEADOW[0] - den_x) > 30:
            mx = random.uniform(_MEADOW[0] + 6, _MEADOW[1] - 6) - span / 2
            targets.append((mx,
                            random.choice(_IDLE_POSES.get(kind, ("rest",)))))
        if not targets:
            return False
        px, pose = random.choice(targets)
        wk["x"] = home
        wk["home_x"] = home
        wk["wander"] = 0
        wk["errand"] = True
        wk["_errand_plan"] = (1 if px > home else -1, px, pose)
        # Start the scene asleep in the den; the wake-up is the entrance.
        # A proper doze before setting off — the old 2-3 s nap made the
        # den look like a revolving door.
        wk["pose"] = "den"
        wk["pause_x"] = home
        wk["pause_done"] = True
        wk["pause_total"] = wk["pause_t"] = random.uniform(6.5, 10.0)
        wk["_curl"] = 1.0             # wakes already curled up
        wk["_fade"] = 0.0             # ease into view, don't materialise
        self._walkers.append(wk)
        return True

    def _start_walker(self):
        if not self.isVisible() or not self._scene_on:
            return
        if self._party and len(self._walkers) >= 7:
            self._sched_timer.start(3000)
            return
        phase = self._phase(self._now())
        # A dusk sky sometimes carries a starling murmuration across.
        if (self._flock is None and not self._first_walker
                and phase == "dusk" and random.random() < 0.10):
            fd = random.choice((1, -1))
            self._flock = {"x": (-30.0 if fd > 0
                                 else self._design_width() + 30.0),
                           "dir": fd, "t": 0.0}
            self._anim_timer.start()
        enc_p = 0.35 if self._party else 0.20
        spawned = False
        if not self._first_walker and random.random() < enc_p:
            spawned = self._spawn_encounter(phase)
        eco_p = 0.18 if self._party else 0.12
        if (self._eco_debut and phase in ("dawn", "day")
                and self._design_width() > 210):
            # The set-pieces are rare enough to go unseen for hours;
            # until the session's debut visit has run, field visits
            # come round much sooner.
            eco_p = 0.5
        if not spawned and not self._first_walker and random.random() < eco_p:
            self._spawn_ecologist()
            spawned = True
        # Livestock: sheep drift in to graze the fields by daylight,
        # lambs at heel in spring.
        if (not spawned and not self._first_walker
                and phase in ("dawn", "day", "dusk")
                and random.random() < 0.10):
            self._spawn_sheep()
            spawned = True
        # The wordmark is part of the habitat: foxes and badgers den in
        # the ring-o, and after dark an owl drops in to perch on its rim.
        logo_ok = (getattr(self, "_den_cx", None) is not None
                   and self._wordmark and self._design_width() > 170)
        if (not spawned and not self._first_walker and logo_ok
                and random.random() < 0.14):
            kind = random.choice(("fox", "badger"))
            self._last_kind = kind
            # Half the time the den is home, not a stop: the animal wakes
            # there, runs an errand and comes back.
            if random.random() < 0.5 and self._spawn_den_dweller(kind,
                                                                 "logo"):
                spawned = True
            else:
                d = random.choice((1, -1))
                wk = self._make_walker(kind, direction=d, layer="mid",
                                       allow_pause=False)
                wk["pause_x"] = self._den_cx - wk["w"] * wk["scale"] / 2
                wk["pause_done"] = False
                wk["pose"] = "den"
                wk["wander"] = 0
                self._walkers.append(wk)
                spawned = True
        if (not spawned and not self._first_walker and logo_ok
                and phase in ("dusk", "night") and random.random() < 0.07):
            self._last_kind = "owl"
            d = random.choice((1, -1))
            wk = self._make_walker("owl", direction=d)
            wk["pause_x"] = self._den_cx - wk["w"] / 2
            wk["pause_done"] = False
            wk["pose"] = "perch"
            self._walkers.append(wk)
            spawned = True
        # The oak's own residents: a squirrel comes for acorns by day, a
        # bird drops onto a perch branch, a fox slips into the earth, and
        # at dusk a bat leaves the roost.
        oak = self._oak
        if not spawned and not self._first_walker and oak is not None:
            if phase in ("dawn", "day", "dusk") and random.random() < 0.14:
                self._spawn_squirrel()
                spawned = True
            elif (phase in ("dusk", "night") and random.random() < 0.08):
                # An owl drops onto a bare perch branch, approaching
                # from whichever edge is nearer the oak.
                self._last_kind = "owl"
                d = 1 if oak["x"] > self._design_width() * 0.5 else -1
                wk = self._make_walker("owl", direction=d)
                wk["pause_x"] = oak["perch"][0] - wk["w"] / 2
                wk["pause_done"] = False
                wk["pose"] = "perch"
                wk["_perch_y"] = oak["perch"][1] - 7.0
                self._walkers.append(wk)
                spawned = True
            elif (phase == "night" and random.random() < 0.10):
                self._last_kind = random.choice(("fox", "badger"))
                if (random.random() < 0.5
                        and self._spawn_den_dweller(self._last_kind, "oak")):
                    spawned = True
                else:
                    wk = self._make_walker(self._last_kind, direction=1,
                                           layer="mid", allow_pause=False)
                    wk["pause_x"] = (oak["base"][0]
                                     - wk["w"] * wk["scale"] / 2)
                    wk["pause_done"] = False
                    wk["pose"] = "den"
                    wk["_den_at"] = "oak"
                    wk["wander"] = 0
                    self._walkers.append(wk)
                    spawned = True
            elif (phase in ("dusk", "night") and random.random() < 0.10):
                # A bat slips out of the roost cavity and flies off.
                self._last_kind = "bat"
                d = random.choice((1, -1))
                wk = self._make_walker("bat", direction=d)
                wk["pose"] = "emerge"
                wk["_prf"] = oak["prf"]
                wk["x"] = oak["prf"][0] - wk["w"] / 2
                self._walkers.append(wk)
                spawned = True
        if not spawned:
            if self._first_walker or self._party:
                self._first_walker = False
                kind = random.choice(_SPECIES)
            else:
                kind = self._pick_species(self._now())
            kind = self._season_gate(kind)
            self._last_kind = kind
            d = random.choice((1, -1))
            adult = self._make_walker(kind, direction=d)
            if kind == "deer" and random.random() < 0.4:
                adult["stag"] = True          # antlers, a touch bigger
                adult["scale"] *= 1.06
            self._walkers.append(adult)
            # A lone parent often brings young along.
            if kind in _BABIES and random.random() < 0.45:
                self._gid += 1
                adult["group"] = self._gid
                n = 1 if kind in ("deer", "otter") else random.randint(1, 2)
                for i in range(n):
                    baby = self._make_walker(
                        kind, direction=d, layer=adult["layer"],
                        group=self._gid,
                        scale_mult=(0.5 if kind == "gcn" else 0.58),
                        x=adult["x"] - d * (24 + i * 14) * adult["scale"])
                    baby["speed"] = baby["base_speed"] = adult["base_speed"]
                    baby["gait_hz"] = adult["gait_hz"] * 1.4
                    if adult["pause_x"] is not None:
                        baby["pause_x"], baby["pause_done"] = adult["pause_x"], False
                        baby["pose"] = adult["pose"]
                    self._walkers.append(baby)
                self._groups[self._gid] = {"kind": "family"}
        self._anim_timer.start()
        if self._party:
            self._sched_timer.start(int(self._next_delay() * 1000))

    def _spawn_ecologist(self):
        """A field ecologist crosses and stops to work — measuring a tree
        (DBH tape + clinometer), taking a water sample at a lake, scanning
        with binoculars or photographing the site — and often fits several
        jobs into one visit."""
        wd = self._design_width()
        self._last_kind = "ecologist"
        d = random.choice((1, -1))
        eco = self._make_walker("ecologist", direction=d, allow_pause=False)
        eco["wander"] = random.randint(1, 2)   # extra stops between tasks
        phase = self._phase(self._now())
        # Two field excursions that use the whole scene: a hike up the far
        # hill (shrinking into the distance for an upland survey) and a
        # boat survey rowed out onto a lake.
        lakes_on = [(lx, lrx, lry) for lx, lrx, lry, _m in _LAKES
                    if lx + lrx < wd - 6 and lrx > 18]
        if phase in ("dawn", "day") and wd > 210:
            r = random.random()
            if self._eco_debut:
                # The session's first daytime visit showcases a
                # set-piece the random windows might not surface for
                # hours: the boat survey if the loch is open, else the
                # hike up the fells.
                self._eco_debut = False
                boat_ok = (self._season() != "winter"
                           and any(lrx >= 44 for _lx, lrx, _lry
                                   in lakes_on))
                r = random.choice((0.1, 0.4)) if boat_ok else 0.1
            if r < 0.20:
                eco["hike"] = "up"
                eco["hike_cx"] = wd * random.uniform(0.5, 0.75)
                eco["pose"], eco["x"], eco["dir"] = "rest", -16.0, 1
                self._walkers.append(eco)
                return
            # The boat survey needs open water: only the grand loch is
            # big enough for the full-size boat, so it launches there
            # or not at all.
            big = [(lx, lrx, lry) for lx, lrx, lry in lakes_on
                   if lrx >= 44]
            if (big and r < 0.55           # a third of day visits row out
                    and self._season() != "winter"):   # loch's frozen
                lx, lrx, lry = big[0]
                ly, _ry = self._lake_geom(lx, lry, wd)
                d2 = random.choice((1, -1))    # which reeds he sets out from
                eco["boat"] = True
                eco["boat_lx"] = lx
                eco["boat_y"] = ly + 0.4
                eco["boat_edge"] = lrx * 0.85   # land back in the reeds
                eco["boat_st"] = "out"
                eco["dir"] = d2
                eco["x"] = lx - d2 * lrx * 0.78 - 8.0 * _BOAT_S
                eco["boat_t"] = random.uniform(9.0, 15.0)
                eco["_fade"] = 0.0              # and ease into view
                self._walkers.append(eco)
                return
        # Otherwise: prefer a task site that's actually on screen; shores
        # and tree stand-points flip with the approach direction so the
        # tools face the work.
        shores = [(lx - lrx - 10 if d > 0 else lx + lrx + 3)
                  for lx, lrx, _r, _m in _LAKES if lx + lrx < wd - 4]
        choice = []
        if phase in ("dusk", "night"):
            # After dark: moth trapping, bat-detector transects, a torch
            # survey of the pond, and — under the veteran oak — a dusk
            # bat-emergence count at the roost.
            for npose in ("moth_trap", "detector"):
                choice.append((npose, self._avoid_lake(
                    wd * random.uniform(0.28, 0.72), wd)))
            if shores:
                choice.append(("torch", random.choice(shores)))
            if self._oak is not None:
                bx = (self._oak["base"][0] - 9 if d > 0
                      else self._oak["base"][0] + 9)
                choice.append(("bat_survey", bx))
        else:
            trees = [tx for tx, _k, _s in _TREES
                     if tx + 8 < wd - 4 and tx > 60]
            if trees:
                tx = random.choice(trees)
                choice.append(("measure", tx - 6 if d > 0 else tx - 1))
            if shores:
                choice.append(("sample", random.choice(shores)))
                choice.append(("dip", random.choice(shores)))
            # Reptiles bask by day — lift a refugia mat on open ground.
            choice.append(("refugia", self._avoid_lake(
                wd * random.uniform(0.3, 0.7), wd)))
            choice.append(("binoculars", self._avoid_lake(
                wd * random.uniform(0.25, 0.75), wd)))
            choice.append(("photo", self._avoid_lake(
                wd * random.uniform(0.25, 0.75), wd)))
        pose, px = random.choice(choice)
        # The bat-emergence survey is really a detector held up under the
        # oak; flag it so bats stream out of the roost while it counts.
        eco["bat_survey"] = pose == "bat_survey"
        if pose == "bat_survey":
            pose = "detector"
        eco["pose"], eco["pause_x"], eco["pause_done"] = pose, px, False
        self._walkers.append(eco)

    def _tick_ambient(self, dt, wd):
        """Ambience that lives outside the walker list: the murmuration,
        fish rising on the lakes, drifting autumn leaves."""
        # While an ecologist counts a bat emergence, bats stream out of
        # the oak roost.
        if self._oak is not None and any(
                w["kind"] == "ecologist" and w.get("bat_survey")
                and w["pause_t"] > 0 for w in self._walkers):
            if random.random() < dt * 0.7 and len(self._walkers) < 14:
                d = random.choice((1, -1))
                bat = self._make_walker("bat", direction=d)
                bat["pose"] = "emerge"
                bat["_prf"] = self._oak["prf"]
                bat["x"] = self._oak["prf"][0] - bat["w"] / 2
                self._walkers.append(bat)
        # On clear nights, once in a while, a shooting star.
        if self._shoot is not None:
            self._shoot["age"] += dt
            if self._shoot["age"] > 0.9:
                self._shoot = None
        elif (self._dark > 0.65 and self._weather == "clear"
              and random.random() < dt / 300.0):
            self._shoot = {"x": random.uniform(wd * 0.15, wd * 0.80),
                           "y": random.uniform(2.0, 8.0),
                           "dx": random.choice((-1, 1))
                           * random.uniform(26.0, 40.0),
                           "dy": random.uniform(9.0, 15.0), "age": 0.0}
        # Summer thistledown sails the same wind that leans the grass;
        # the odd seed drifts by big and soft, right past the glass.
        if (self._season() == "summer" and self._dark < 0.8
                and len(self._seeds) < 4 and wd > 240
                and random.random() < dt * (0.015 + 0.05 * max(
                    0.0, self._wind - 0.30))):
            self._seeds.append({
                "x": -6.0, "y": random.uniform(18.0, 34.0),
                "ph": random.uniform(0.0, 6.28), "born": self._t,
                "big": random.random() < 0.25})
        for sd in self._seeds:
            sd["x"] += ((3.0 + 5.5 * self._wind
                         + 2.5 * self._wind_at(sd["x"]))
                        * dt * (1.6 if sd["big"] else 1.0))
            sd["y"] += (math.sin(self._t * 0.7 + sd["ph"]) * 1.6
                        - 0.15) * dt
        self._seeds = [sd for sd in self._seeds if sd["x"] < wd + 10]
        # A far-off skein of birds tracking along the mountain wall.
        if self._far_birds is None:
            if (wd > 520 and self._dark < 0.5
                    and random.random() < dt * 0.02):
                fdir = random.choice((1, -1))
                self._far_birds = {
                    "x": -26.0 if fdir > 0 else wd + 26.0, "dir": fdir,
                    "y": random.uniform(9.5, 15.0), "t": 0.0}
        else:
            fb = self._far_birds
            fb["x"] += fb["dir"] * 7.5 * dt
            fb["t"] += dt
            if not -40 <= fb["x"] <= wd + 40:
                self._far_birds = None
        # Swallows hawk low over the meadow on summer evenings.
        if (self._season() == "summer" and 0.15 <= self._dark <= 0.75
                and len(self._swallows) < 3 and wd > 260
                and random.random() < dt * 0.04):
            self._swallows.append({
                "cx": random.uniform(wd * 0.2, wd * 0.8),
                "r": random.uniform(40.0, 90.0),
                "ph": random.uniform(0.0, 6.28),
                "born": self._t, "life": random.uniform(18.0, 35.0)})
        if self._swallows:
            self._swallows = [s for s in self._swallows
                              if self._t - s["born"] < s["life"]]
        # Rabbits work the warren in the near band: one emerges from
        # the burrow, nibbles about, and dives home when its time is
        # up — or the moment a fox spooks it.
        rabbits = [w for w in self._walkers if w["kind"] == "rabbit"]
        if not rabbits and self._dark < 0.85:
            wpos = self._warren_pos(wd, self._design_bot())
            if wpos is not None and random.random() < dt * 0.02:
                r = self._make_walker("rabbit", direction=1,
                                      x=wpos[0] - 6.0, layer="fore",
                                      allow_pause=False)
                r["depth_lock"] = True
                r["_hole"] = wpos
                r["_rab_t"] = random.uniform(18.0, 32.0)
                r["_fade"] = 0.0              # up out of the burrow
                r["pause_x"] = wpos[0] + random.choice((-1, 1)) \
                    * random.uniform(6.0, 16.0)
                r["dir"] = 1 if r["pause_x"] > wpos[0] else -1
                r["pause_done"] = False
                r["pose"] = "nibble"
                r["wander"] = 1
                self._walkers.append(r)
        for r in rabbits:
            r["_rab_t"] = r.get("_rab_t", 20.0) - dt
            hole = r.get("_hole")
            if hole is None:
                continue
            cxr = r["x"] + r["w"] * r["scale"] / 2
            if ((r["_rab_t"] <= 0 or r.get("fled"))
                    and r.get("_fade_dir") != -1):
                r["pause_x"], r["pause_done"] = None, True
                r["pause_t"] = 0.0
                r["dir"] = 1 if hole[0] > cxr else -1
                r["_face"] = float(r["dir"]) if r.get("fled") \
                    else r.get("_face", float(r["dir"]))
                if r.get("fled"):
                    r["speed"] = r["base_speed"] * 1.9
                if abs(cxr - hole[0]) < 2.2:
                    r["_fade_dir"] = -1       # dive down the burrow
            if r.get("_fade_dir") == -1:
                r["_fade"] = r.get("_fade", 1.0) - dt * 3.0
                if r["_fade"] <= 0.0:
                    r["x"] = -9999.0          # gone underground
        if self._flock is not None:
            fl = self._flock
            fl["x"] += fl["dir"] * 26.0 * dt
            fl["t"] += dt
            if not -60 <= fl["x"] <= wd + 60:
                self._flock = None
        vis = [(lx, lrx, lry) for lx, lrx, lry, _m in _LAKES
               if lx + lrx < wd - 4]
        if vis and self._season() == "winter":
            vis = []                    # nothing rises through the ice
        if vis:
            bonus = 3.0 if any(w["kind"] == "heron" and w["pause_t"] > 0
                               and w["pose"] == "fish"
                               for w in self._walkers) else 1.0
            if random.random() < dt * 0.05 * len(vis) * bonus:
                lx, lrx, lry = random.choice(vis)
                ly, ry = self._lake_geom(lx, lry, wd)
                self._rings.append([lx + random.uniform(-0.5, 0.5) * lrx,
                                    ly + random.uniform(-0.2, 0.5) * ry,
                                    0.3])
        for ring in self._rings:
            ring[2] += dt * 1.8
        self._rings = [r for r in self._rings if r[2] < 3.2]
        if self._season() == "autumn":
            if len(self._leaves) < 12 and random.random() < dt * 1.2:
                tx = random.choice(_TREES)[0] + random.uniform(-6, 6)
                if 10 < tx < wd - 4:
                    ty = (self._ridge_y(_BACK_PTS, wd, tx)
                          - random.uniform(4, 9))
                    self._leaves.append([tx, ty, random.uniform(0, 6.3)])
            for lf in self._leaves:
                lf[2] += dt * 3.0
                lf[0] += ((0.35 + 0.5 * math.sin(lf[2])) * dt * 6
                          + self._wind_at(lf[0]) * dt * 9.0)
                lf[1] += dt * 2.6
            self._leaves = [l for l in self._leaves
                            if l[1] < _DESIGN_H - 1]
        elif self._leaves:
            self._leaves = []

    def _anim_tick(self):
        if not self._scene_on:
            self._anim_timer.stop()
            return
        dt = self._anim_timer.interval() / 1000.0
        self._t += dt
        wd = self._design_width()
        # Ease the parallax toward the pointer's side of the banner —
        # a touch gentler than the old rate now the throw is bigger, so
        # the planes glide rather than lurch.
        if self._par != self._par_t:
            self._par += (self._par_t - self._par) * min(1.0, dt * 1.9)
            if abs(self._par_t - self._par) < 0.003:
                self._par = self._par_t
        # Step the season crossfade, if one is running.
        if self._season_fade > 0.0:
            self._season_fade = max(0.0, self._season_fade - dt / 2.2)
            if self._season_fade == 0.0:
                self._season_snap = None
        # The wind rises and falls with the weather.
        wtgt = {"clear": 0.35, "cloud": 0.55, "fog": 0.15, "rain": 0.85,
                "storm": 1.35, "snow": 0.5}.get(self._weather, 0.35)
        amp_t = 0.35 + (wtgt - 0.35) * self._weather_strength
        self._wind += (amp_t - self._wind) * min(1.0, dt * 0.8)
        weather_running = self._step_weather(dt)
        self._tick_ambient(dt, wd)
        ambient = (weather_running or self._flock is not None
                   or bool(self._rings) or bool(self._leaves)
                   or self._rainbow > 0.02 or self._swell > 0.02
                   or self._far_birds is not None
                   or self._par != self._par_t
                   or self._season_fade > 0.0
                   or bool(self._swallows)
                   or self._shoot is not None
                   or bool(self._seeds))
        if not self._walkers:
            if ambient:
                self.update()
                return
            self._anim_timer.stop()
            return
        alive = []
        for wk in self._walkers:
            # Universal transition easing, for every species and state:
            # _face turns the sprite over ~a third of a second instead
            # of mirror-snapping; _fade eases spawned-in-place animals
            # into view; _pose_u gives every pose swap a short settle.
            f = wk.get("_face", float(wk["dir"]))
            if f != wk["dir"] and wk.get("_curl", 0.0) <= 0.05:
                # (While curled in a den the sprite is the ball — hold
                # the turn until it is back on its feet.)
                f += (wk["dir"] - f) * min(1.0, dt * 6.5)
                if abs(wk["dir"] - f) < 0.02:
                    f = float(wk["dir"])
            wk["_face"] = f
            if wk.get("_fade", 1.0) < 1.0 and wk.get("_fade_dir") != -1:
                wk["_fade"] = min(1.0, wk["_fade"] + dt * 1.4)
            if wk["pose"] != wk.get("_pose_prev"):
                wk["_pose_prev"] = wk["pose"]
                wk["_pose_u"] = 0.0
            if wk.get("_pose_u", 1.0) < 1.0:
                wk["_pose_u"] = min(1.0, wk["_pose_u"] + dt * 5.0)
            # Den curl blend: 0 on its feet → 1 curled in the den. A
            # fixed ~0.45 s ramp each way, so the animal visibly slips
            # up into the ring-o (or the oak earth) and uncurls out of
            # it again during the depart-beat stillness — never the old
            # single-frame teleport between sprite and ball.
            ct = 1.0 if (wk["pose"] == "den" and wk["pause_t"] > 0) else 0.0
            cu = wk.get("_curl", 0.0)
            if cu != ct:
                cs = dt / 0.45
                cu = min(1.0, cu + cs) if ct > cu else max(0.0, cu - cs)
                wk["_curl"] = cu
                if cu <= 0.0 and wk["pose"] != "den":
                    wk.pop("_den", None)
            # Water manners: approaching a lake, non-swimmers pick a
            # bank to divert along; the rare swimmers toss a coin
            # between going through and going around. One choice per
            # approach, forgotten once the lake is behind them.
            if (not wk["flying"] and wk["kind"] != "squirrel"
                    and (wk["kind"] in _NO_SWIM
                         or wk["kind"] in _RARE_SWIM)):
                cxw = wk["x"] + wk["w"] * wk["scale"] / 2
                near_lake = None
                for lx, lrx, _lry, _m in _LAKES:
                    if lx + lrx < wd - 4 and abs(cxw - lx) < lrx + 20:
                        near_lake = lx
                        break
                if near_lake is None:
                    wk["_lane_lake"] = None
                elif wk.get("_lane_lake") != near_lake:
                    wk["_lane_lake"] = near_lake
                    if wk["kind"] in _NO_SWIM:
                        wk["_lane"] = random.choice(("above", "below"))
                    else:
                        wk["_lane"] = random.choice(
                            ("swim", "above", "swim", "below"))
            if wk["kind"] == "squirrel":
                self._tick_squirrel(wk, dt, wd)
                span = wk["w"] * wk["scale"]
                if -span - 14 <= wk["x"] <= wd + 14:
                    alive.append(wk)
                continue
            if wk["kind"] == "ecologist" and wk.get("hike"):
                self._tick_hiker(wk, dt, wd)
                span = wk["w"] * wk["scale"]
                if -span - 16 <= wk["x"] <= wd + 16:
                    alive.append(wk)
                continue
            if wk["kind"] == "ecologist" and wk.get("boat"):
                self._tick_boat(wk, dt, wd)
                if -30 <= wk["x"] <= wd + 30:
                    alive.append(wk)
                continue
            # Perspective drift: a lone walker wanders slowly nearer and
            # further, growing and shrinking as its depth changes — but
            # only while roaming free. On the way to (or stopped at) a
            # destination it holds its line, which is half of what makes
            # the trip look meant.
            if (not wk["flying"] and not wk.get("depth_lock")
                    and wk["group"] is None and wk["pause_done"]
                    and wk["pause_t"] <= 0):
                if random.random() < dt * 0.16:
                    wk["depth_t"] = min(0.95, max(0.08, wk["depth"]
                                        + random.uniform(-0.32, 0.32)))
                wk["depth"] += ((wk["depth_t"] - wk["depth"])
                                * min(1.0, dt * 0.35))
                wk["scale"] = self._depth_scale(wk["depth"])
                wk["layer"] = self._depth_layer(wk["depth"], wk["layer"])
            # A pointer hovering over an animal makes it stop and watch —
            # turning to face the cursor. Scripted groups stay on script,
            # and anything mid-way to a task keeps its appointment.
            if (self._mouse and not wk["flying"] and wk["group"] is None
                    and wk.get("fly_t") is None):
                mspan = wk["w"] * wk["scale"]
                mcx = wk["x"] + mspan / 2
                if wk["layer"] == "fore":
                    mcy = (self._depth_crest(wk["depth"], mcx, wd)
                           - wk["h"] * wk["scale"] / 2)
                else:
                    mpts = _BACK_PTS if wk["layer"] == "back" else _FRONT_PTS
                    mcy = (self._ridge_y(mpts, wd, mcx) + 2
                           - wk["h"] * wk["scale"] / 2)
                if (abs(self._mouse[0] - mcx) < mspan * 0.75 + 5
                        and abs(self._mouse[1] - mcy)
                        < wk["h"] * wk["scale"] * 0.8 + 5):
                    if wk["pause_t"] > 0:
                        wk["pause_t"] = max(wk["pause_t"], 0.35)
                    elif wk["pause_done"]:
                        wk["pose"] = "watch"
                        wk["pause_total"] = 0.6
                        wk["pause_t"] = 0.35
                    if (wk["pause_done"]
                            and (self._mouse[0] - mcx) * wk["dir"] < -4):
                        wk["dir"] *= -1
            if wk["pause_t"] > 0:
                wk["pause_t"] -= dt
                if wk["pose"] in ("boxing", "sip", "hover"):
                    idle = 1.0            # hovering wings stay up to speed
                elif wk["pose"] in ("groom", "peck", "measure", "sample",
                                    "binoculars", "photo", "gnaw", "nibble",
                                    "moth_trap", "detector", "refugia",
                                    "dip", "torch"):
                    idle = 0.7
                elif wk["pose"] in ("fish", "graze", "snuffle", "sniff",
                                    "rear", "drink"):
                    idle = 0.45
                else:
                    idle = 0.15
                wk["gait"] += 2 * math.pi * wk["gait_hz"] * dt * idle
                if wk["pause_t"] <= 0:
                    self._after_pause(wk, wd)
            else:
                # A foraging flyer wheels back and forth over its patch,
                # turning at the edges, until it has hunted its fill.
                if wk["flying"] and wk.get("forage_t", 0) > 0:
                    wk["forage_t"] -= dt
                    fcx = wk["x"] + wk["w"] / 2
                    if fcx < wk["forage_cx"] - wk["forage_r"]:
                        wk["dir"] = 1
                    elif fcx > wk["forage_cx"] + wk["forage_r"]:
                        wk["dir"] = -1
                step = wk["dir"] * wk["speed"] * dt
                gait = 2 * math.pi * wk["gait_hz"] * dt
                if wk.get("depart_t", 0) > 0:
                    # A beat of stillness before setting off — looking
                    # the journey over — then away.
                    wk["depart_t"] -= dt
                    step = 0.0
                    gait *= 0.15
                elif not wk["pause_done"] and wk["pause_x"] is not None:
                    # Travel with a destination: press on while it is
                    # far, ease up on the approach so the arrival reads
                    # as planned rather than a freeze mid-stride.
                    dist = (wk["pause_x"] - wk["x"]) * wk["dir"]
                    if dist < 14.0:
                        ease = max(0.30, dist / 14.0)
                        step *= ease
                        if not wk["flying"]:
                            gait *= max(0.5, ease)
                    elif dist > 70.0:
                        step *= 1.10
                if wk.get("stalk"):
                    # Deliberate stalk: the body glides forward only as a
                    # foot swings, and freezes between steps.
                    step *= self._stalk_gate(wk["gait"])
                if (not wk["flying"] and wk["layer"] == "mid"
                        and wk["kind"] not in _LAKEFOLK
                        and wk["kind"] != "ecologist"):
                    cx = wk["x"] + wk["w"] * wk["scale"] / 2
                    lake = self._lake_at(cx, wd)
                    if lake is not None:
                        t = max(0.0, 1.0 - abs(cx - lake[0])
                                / (lake[2] * 0.85))
                        if wk["kind"] in ("hare", "pheasant"):
                            step *= 1.0 + 0.35 * t   # dash across the water
                            if wk["kind"] == "pheasant":
                                gait *= 1.0 + 1.3 * t  # wings whirring
                        else:
                            step *= 1.0 - 0.45 * t   # swimming is slower
                            gait *= 1.0 - 0.55 * t   # legs paddle, not trot
                wk["x"] += step
                wk["gait"] += gait
                # Now and then an animal simply changes its mind: a beat
                # of hesitation, then back the way it came. Never on the
                # way home, though — homeward-bound animals don't dither.
                if (wk["group"] is None and not wk["flying"]
                        and wk["pause_done"] and wk.get("fly_t") is None
                        and not wk.get("homebound")
                        and random.random() < dt * 0.03):
                    wk["dir"] *= -1
                    wk["pause_t"] = wk["pause_total"] = 0.45
                # The barn owl quarters: swinging back over the rough
                # grass again and again as it hunts.
                if (wk["kind"] == "barn_owl" and wk["group"] is None
                        and random.random() < dt * 0.10):
                    wk["dir"] *= -1
                # A stag beetle with a takeoff point clatters into the air
                # when it reaches it and flies out clumsy and fast.
                if wk["kind"] == "stag_beetle":
                    if wk.get("fly_t") is not None:
                        wk["fly_t"] += dt
                    elif wk["takeoff"] is not None:
                        if ((wk["x"] >= wk["takeoff"]) if wk["dir"] > 0
                                else (wk["x"] <= wk["takeoff"])):
                            wk["fly_t"] = 0.0
                            wk["speed"] = wk["base_speed"] * 2.3
                            wk["pause_x"], wk["pause_done"] = None, True
                # Once snow is lying, the ground takes footprints that
                # linger and fade.
                if (self._snowcover > 0.45
                        and not wk["flying"] and wk["layer"] != "back"):
                    lx = wk.get("_track_x")
                    if lx is None or abs(wk["x"] - lx) > 2.6:
                        wk["_track_x"] = wk["x"]
                        tcx = wk["x"] + wk["w"] * wk["scale"] / 2
                        gy = (43.2 if wk["layer"] == "fore"
                              else self._ridge_y(_FRONT_PTS, wd, tcx) + 1.7)
                        gy += 0.45 if len(self._tracks) % 2 else -0.1
                        self._tracks.append([tcx, gy, self._t, wk["layer"]])
                        if len(self._tracks) > 260:
                            del self._tracks[:40]
                if not wk["pause_done"] and wk["pause_x"] is not None:
                    hit = (wk["x"] >= wk["pause_x"] if wk["dir"] > 0
                           else wk["x"] <= wk["pause_x"])
                    if hit:
                        wk["pause_done"] = True
                        if wk.get("_pause_len") is not None:
                            lo, hi = wk["_pause_len"]
                            wk["_pause_len"] = None
                            wk["pause_total"] = random.uniform(lo, hi)
                        elif wk["pose"] in ("measure", "sample", "refugia",
                                            "dip"):
                            wk["pause_total"] = random.uniform(5.0, 7.5)
                        elif wk["pose"] in ("binoculars", "photo"):
                            wk["pause_total"] = random.uniform(3.5, 5.5)
                        elif wk["pose"] in ("moth_trap", "detector",
                                            "torch"):
                            wk["pause_total"] = (random.uniform(9.0, 13.0)
                                                 if wk.get("bat_survey")
                                                 else random.uniform(5.0, 8.0))
                        elif wk["pose"] == "fish":
                            wk["pause_total"] = random.uniform(6.0, 9.5)
                            wk["caught"] = random.random() < 0.5
                        elif wk["pose"] == "oneleg":
                            wk["pause_total"] = random.uniform(7.0, 12.0)
                        elif wk["pose"] in ("sip", "hover"):
                            wk["pause_total"] = random.uniform(1.2, 2.4)
                        elif wk["pose"] == "perch":
                            wk["pause_total"] = random.uniform(6.0, 11.0)
                        elif wk["pose"] == "den":
                            wk["pause_total"] = random.uniform(9.0, 16.0)
                        elif wk["pose"] == "drink":
                            wk["pause_total"] = random.uniform(3.0, 5.0)
                        else:
                            wk["pause_total"] = random.uniform(1.4, 3.4)
                        wk["pause_t"] = wk["pause_total"]
            span = wk["w"] * wk["scale"]
            if -span - 14 <= wk["x"] <= wd + 14:
                alive.append(wk)
        self._walkers = alive
        if self._tracks:
            self._tracks = [t for t in self._tracks if self._t - t[2] < 24]
        self._tick_groups()
        self._tick_flush()
        if not self._walkers:
            self._sched_timer.start(int(self._next_delay() * 1000))
            if not ambient:
                self._anim_timer.stop()
        self.update()

    def _after_pause(self, wk, wd):
        """A stop just ended: errand-runners turn for home, den dwellers
        wake and set off (or curl up for good), and everyone else
        sometimes changes its mind — doubling back, or ambling on at a
        new pace to pause again further along. Scripted groups stay on
        script."""
        if wk["pose"] == "watch":
            wk["pose"] = "rest"          # done watching the pointer
            return
        if wk["pose"] == "den":
            plan = wk.pop("_errand_plan", None)
            # Every den wake gets a depart beat long enough to cover
            # the ~0.45 s uncurl blend, so it stands up out of the O
            # before it moves off — never pops straight into a stride.
            if plan is not None:
                # Up from the den and out on the errand it woke with.
                wk["dir"], wk["pause_x"], wk["pose"] = plan
                wk["pause_done"] = False
                wk["depart_t"] = random.uniform(0.65, 0.95)
                return
            if wk.get("homebound"):
                # Home, rested after the trip out: up, and away.
                wk["home_x"] = None
                wk["pose"] = "rest"
                wk["wander"] = 0
                wk["dir"] = 1 if wk["x"] > wd * 0.5 else -1
                wk["depart_t"] = random.uniform(0.65, 0.95)
                return
            wk["pose"] = "rest"          # up from the den, moving on
            wk["depart_t"] = random.uniform(0.65, 0.95)
            return
        # A heron rarely leaves the water's edge in a hurry — it usually
        # stalks on to the next shore and fishes again.
        if (wk["kind"] == "heron" and wk["group"] is None
                and random.random() < 0.55):
            shores = [(lx - lrx - 9 if wk["dir"] > 0 else lx + lrx + 2)
                      for lx, lrx, _r, _m in _LAKES if lx + lrx < wd - 4]
            ahead = [sx for sx in shores
                     if (sx - wk["x"]) * wk["dir"] > 15]
            if ahead:
                wk["pause_x"] = random.choice(ahead)
                wk["pause_done"] = False
                wk["pose"] = "fish"
                return
        # A den dweller that has finished its errand turns for home.
        if wk.get("home_x") is not None and not wk.get("homebound"):
            wk["dir"] = 1 if wk["home_x"] > wk["x"] else -1
            wk["pause_x"] = wk["home_x"]
            wk["pause_done"] = False
            wk["pose"] = "den"
            wk["homebound"] = True
            wk["_pause_len"] = (5.0, 9.0)
            wk["depart_t"] = random.uniform(0.35, 0.7)
            return
        if wk["group"] is not None:
            # A converging pair parts again the way each one came.
            grp = self._groups.get(wk["group"])
            if grp is not None and grp.get("kind") == "converge":
                wk["dir"] *= -1
                wk["homebound"] = True
                wk["depart_t"] = random.uniform(0.3, 0.7)
            return
        # Out-and-back: an animal that came for something — a drink, the
        # meadow, the oak — usually turns for home once it is done, so
        # the whole visit reads as one intention.
        if (wk.get("errand") and not wk.get("homebound")
                and not wk["flying"] and random.random() < 0.55):
            wk["dir"] *= -1
            wk["homebound"] = True
            wk["wander"] = 0
            wk["speed"] = wk["base_speed"] * random.uniform(1.0, 1.12)
            wk["depart_t"] = random.uniform(0.3, 0.6)
            return
        if wk["flying"] or wk["wander"] <= 0:
            return
        wk["wander"] -= 1
        r = random.random()
        if r < 0.30:
            wk["dir"] *= -1              # double back
        elif r > 0.65:
            return                       # just carry on
        # A river ahead tempts a drink stop before anything else.
        span = wk["w"] * wk["scale"]
        if (wk["layer"] == "mid" and wk["kind"] not in _SWIMMERS
                and wk["kind"] not in ("stag_beetle", "ecologist")
                and random.random() < 0.22):
            ahead = [rx for rx in (_STREAM_X, _RIVER_X, _RIVER2_X)
                     if rx + 14 < wd and (rx - wk["x"]) * wk["dir"] > 20]
            if ahead:
                rx = min(ahead, key=lambda r2: abs(r2 - wk["x"]))
                px = rx - span - 1.5 if wk["dir"] > 0 else rx + 1.5
                if self._lake_at(px + span / 2, wd) is None:
                    wk["pause_x"] = px
                    wk["pause_done"] = False
                    wk["pose"] = "drink"
                    wk["errand"] = True
                    wk["depart_t"] = random.uniform(0.25, 0.5)
                    return
        px = wk["x"] + wk["dir"] * wd * random.uniform(0.12, 0.30)
        if not 12 < px < wd - 12:
            return
        if wk["layer"] == "mid" and wk["kind"] not in _LAKE_PAUSERS:
            px = self._avoid_lake(px, wd)
        wk["pause_x"], wk["pause_done"] = px, False
        wk["pose"] = random.choice(_IDLE_POSES.get(wk["kind"], ("rest",)))
        wk["speed"] = wk["base_speed"] * random.uniform(0.8, 1.15)
        wk["depart_t"] = random.uniform(0.25, 0.5)

    def _tick_hiker(self, wk, dt, wd):
        """The ecologist hikes up the far hill for an upland survey,
        shrinking into the distance, glasses the ground, then walks back
        down and off. Depth drives both its size and how far up it is."""
        gy = 2 * math.pi
        st = wk["hike"]
        wk["depth_lock"] = False           # let depth move it up the hill
        cx = wk["x"] + wk["w"] * wk["scale"] / 2
        if st == "up":
            wk["dir"] = 1 if wk["hike_cx"] >= cx else -1
            if abs(wk["hike_cx"] - cx) > 2.5:
                wk["x"] += wk["dir"] * wk["speed"] * 0.7 * dt
                wk["gait"] += gy * wk["gait_hz"] * dt
            wk["depth"] += (0.14 - wk["depth"]) * min(1.0, dt * 0.28)
            if abs(wk["hike_cx"] - cx) < 3 and wk["depth"] < 0.2:
                wk["hike"], wk["pose"] = "survey", "binoculars"
                wk["pause_t"] = random.uniform(4.0, 7.0)
        elif st == "survey":
            wk["pause_t"] -= dt
            wk["gait"] += gy * wk["gait_hz"] * dt * 0.7
            if wk["pause_t"] <= 0:
                wk["hike"], wk["pose"] = "down", "rest"
                wk["dir"] = random.choice((1, -1))
        else:                              # down: return and leave
            wk["x"] += wk["dir"] * wk["speed"] * dt
            wk["gait"] += gy * wk["gait_hz"] * dt
            wk["depth"] += (0.56 - wk["depth"]) * min(1.0, dt * 0.28)
        wk["scale"] = self._depth_scale(wk["depth"])
        wk["layer"] = self._depth_layer(wk["depth"], wk["layer"])

    def _tick_boat(self, wk, dt, wd):
        """A boat survey on the loch, one honest trip: row out from the
        reeds to open water, ship the oars and work the station (eDNA
        over the side), then row back the way he came and land."""
        st = wk.get("boat_st", "out")
        cx = wk["x"] + 8.0 * _BOAT_S
        if st == "out":
            wk["gait"] += 2 * math.pi * 1.4 * dt      # rowing stroke
            wk["x"] += wk["dir"] * wk["speed"] * 0.5 * dt
            if (cx - wk["boat_lx"]) * wk["dir"] >= -2.0:
                wk["boat_st"] = "work"                # on station
        elif st == "work":
            wk["gait"] += 2 * math.pi * 0.18 * dt     # oars shipped
            wk["boat_t"] = wk.get("boat_t", 10.0) - dt
            if wk["boat_t"] <= 0:
                wk["boat_st"] = "back"
                wk["dir"] *= -1                       # home the way he came
        else:
            wk["gait"] += 2 * math.pi * 1.4 * dt
            wk["x"] += wk["dir"] * wk["speed"] * 0.5 * dt
            # Landing: the boat slips in behind the reeds and is gone —
            # it doesn't row on across dry land.
            edge = wk.get("boat_edge")
            if (edge is not None
                    and abs(cx - wk["boat_lx"]) > edge + 10.0):
                wk["x"] = wd + 60.0 if wk["dir"] > 0 else -60.0

    def _spawn_sheep(self):
        """A ewe ambles in to graze one of the fields — by the wall or
        a hedgerow when one is on screen — and in spring a lamb comes
        at heel."""
        wd = self._design_width()
        self._last_kind = "sheep"
        d = random.choice((1, -1))
        ewe = self._make_walker("sheep", direction=d, layer="mid",
                                allow_pause=False)
        # Graze spots stand OFF the hedge line (the hedge is tall enough
        # to swallow a sheep whole now) — by the wall, or in the open
        # field just beyond a hedge's end.
        spots = [(_WALL[0] + _WALL[1]) / 2 + random.uniform(-26, 26)]
        for hx0, hx1, _g in _HEDGES:
            if hx1 + 30 < wd - 8:
                spots.append(hx1 + random.uniform(12, 26))
            if hx0 - 30 > 20:
                spots.append(hx0 - random.uniform(12, 26))
        spots = [s for s in spots if 20 < s < wd - 24]
        if spots:
            px = self._avoid_lake(random.choice(spots), wd)
            ewe["pause_x"] = px - ewe["w"] * ewe["scale"] / 2
            ewe["pause_done"] = False
            ewe["pose"] = "graze"
            ewe["_pause_len"] = (12.0, 26.0)   # a proper graze
            ewe["wander"] = 1
        self._walkers.append(ewe)
        if self._season() == "spring" and random.random() < 0.8:
            self._gid += 1
            ewe["group"] = self._gid
            lamb = self._make_walker("sheep", direction=d,
                                     layer=ewe["layer"], group=self._gid,
                                     scale_mult=0.55,
                                     x=ewe["x"] - d * 20 * ewe["scale"])
            lamb["speed"] = lamb["base_speed"] = ewe["base_speed"]
            lamb["gait_hz"] = ewe["gait_hz"] * 1.5
            if ewe["pause_x"] is not None:
                lamb["pause_x"] = ewe["pause_x"] - d * 9
                lamb["pause_done"] = False
                lamb["pose"] = "graze"
            self._walkers.append(lamb)
            self._groups[self._gid] = {"kind": "family"}

    def _warren_pos(self, wd, bot):
        """The rabbit warren's burrow mouth (x, ground-y), if the
        cropped-sward set-piece is on screen — mirrors the hash walk
        in _draw_band_props exactly."""
        extra = bot - _DESIGN_H
        if extra < 4.0 or self._snowcover >= 0.4:
            return None
        band_y = _DESIGN_H + extra * 0.6
        tx = 78.0
        while tx < wd - 20:
            n = math.sin(tx * 3.91) * 999.0
            fr = n - math.floor(n)
            kind = int(fr * 3.98)
            py = band_y + (fr - 0.5) * min(extra * 0.3, 3.0)
            lk = self._lake_at(tx, wd)
            if lk is not None and py < lk[1] + lk[3] + 2.0:
                tx += 90.0
                continue
            if kind == 3:
                return (tx + 6.4, py)
            tx += 240.0 + fr * 160.0
        return None

    def _spawn_squirrel(self):
        """A red squirrel bounds in from the edge, bound for the oak."""
        if self._oak is None:
            return
        wd = self._design_width()
        self._last_kind = "squirrel"
        from_left = self._oak["x"] > wd * 0.45
        d = 1 if from_left else -1
        x0 = -18.0 if from_left else wd + 18.0
        wk = self._make_walker("squirrel", direction=d, layer="mid",
                               allow_pause=False, x=x0)
        wk["sq"] = "toward"
        wk["climb"] = 0.0
        wk["pose"] = "rest"
        self._walkers.append(wk)

    def _tick_squirrel(self, wk, dt, wd):
        """Life of a foraging squirrel: bound to the oak, climb the
        trunk, sit in the crown gnawing an acorn, come back down and
        scamper off. Falls back to a plain bound if the oak has scrolled
        out of view."""
        oak = self._oak
        st = wk.get("sq", "away")
        gy = 2 * math.pi
        wk["_anchor_u"] = min(1.0, wk.get("_anchor_u", 1.0) + dt * 3.2)

        def _mark(fx, fy):
            # Remember where the old anchor frame left the squirrel, so
            # the renderer can ease into the new one.
            wk["_anchor_from"] = (fx, fy)
            wk["_anchor_u"] = 0.0

        if oak is None or st == "away":
            wk["x"] += wk["dir"] * wk["speed"] * dt
            wk["gait"] += gy * wk["gait_hz"] * dt
            return
        tx = oak["x"]
        if st == "toward":
            cx = wk["x"] + wk["w"] * wk["scale"] / 2
            tgt = tx - 2.6
            wk["dir"] = 1 if tgt >= cx else -1
            wk["x"] += wk["dir"] * wk["speed"] * dt
            wk["gait"] += gy * wk["gait_hz"] * dt
            if abs(tgt - cx) < 2.0:
                crest = self._ridge_y(_FRONT_PTS, wd, cx)
                _mark(wk["x"], crest - wk["h"] * wk["scale"] + 2)
                wk["sq"], wk["pose"] = "up", "climb"
                wk["climb"] = 0.0
        elif st == "up":
            # Climb in scrabbling bounds, not an elevator glide: each
            # gait cycle gathers (a near-stall) then surges, the
            # average pace unchanged.
            hitch = max(0.0, math.sin(wk["gait"])) ** 2
            wk["climb"] += 13.0 * dt * (0.25 + 3.0 * hitch)
            wk["gait"] += gy * 3.2 * dt
            if wk["climb"] >= (oak["base_y"] - oak["crown"][1]):
                _mark(oak["x"] - 2.2, oak["base_y"] - wk["climb"] - 10.0)
                wk["sq"], wk["pose"] = "forage", "forage"
                wk["pause_t"] = random.uniform(2.5, 4.5)
        elif st == "forage":
            wk["pause_t"] -= dt
            wk["gait"] += gy * 2.0 * dt
            if wk["pause_t"] <= 0:
                _mark(oak["crown"][0] - 1.4, oak["crown"][1] - 4.0)
                wk["sq"], wk["pose"] = "down", "climb"
        elif st == "down":
            # Down in the same checked bounds, head-first (the flip is
            # the renderer's).
            hitch = max(0.0, math.sin(wk["gait"])) ** 2
            wk["climb"] -= 15.0 * dt * (0.25 + 3.0 * hitch)
            wk["gait"] += gy * 3.4 * dt
            if wk["climb"] <= 0:
                wk["climb"] = 0.0
                _mark(oak["x"] - 2.2, oak["base_y"] - 10.0)
                wk["sq"], wk["pose"] = "away", "rest"
                wk["dir"] = random.choice((1, -1))

    def _tick_flush(self):
        """Prey break and run from a fox that wanders too close — a
        little emergent drama, each animal spooking only once."""
        preds = [w for w in self._walkers
                 if w["kind"] == "fox" and not w["flying"]]
        if not preds:
            return
        for wk in self._walkers:
            if (wk["kind"] not in ("hare", "pheasant", "water_vole",
                                   "rabbit")
                    or wk["group"] is not None or wk["flying"]
                    or wk.get("fled")):
                continue
            wcx = wk["x"] + wk["w"] * wk["scale"] / 2
            for pr in preds:
                if pr["layer"] != wk["layer"]:
                    continue
                pcx = pr["x"] + pr["w"] * pr["scale"] / 2
                if abs(pcx - wcx) < 26:
                    wk["fled"] = True
                    wk["pause_t"] = 0.0
                    wk["pause_x"], wk["pause_done"] = None, True
                    wk["pose"] = "rest"
                    wk["wander"] = 0
                    wk["dir"] = 1 if wcx >= pcx else -1
                    wk["speed"] = wk["base_speed"] * 2.0
                    break

    def _tick_groups(self):
        gone = []
        for gid, st in self._groups.items():
            members = [w for w in self._walkers if w["group"] == gid]
            if not members:
                gone.append(gid)
                continue
            if st["kind"] == "boxing" and not st["done"] and len(members) == 2:
                a, b = members
                ca = a["x"] + a["w"] * a["scale"] / 2
                cb = b["x"] + b["w"] * b["scale"] / 2
                if abs(ca - cb) < 26 * a["scale"]:
                    st["done"] = True
                    for wk in (a, b):
                        wk["pose"] = "boxing"
                        wk["pause_total"] = 3.0
                        wk["pause_t"] = 3.0
                        wk["pause_done"] = True
            elif st["kind"] == "chase" and not st["burst"]:
                hares = [w for w in members if w["kind"] == "hare"]
                foxes = [w for w in members if w["kind"] == "fox"]
                if hares and foxes:
                    if hares[0]["x"] - foxes[0]["x"] < 32:
                        hares[0]["speed"] = hares[0]["base_speed"] * 1.75
                        st["burst"] = True
        for gid in gone:
            self._groups.pop(gid, None)

    # ── geometry helpers ──────────────────────────────────────────────
    def _ridge_path(self, pts, w, bottom=_DESIGN_H):
        # Absolute-x ridgeline, padded past both edges so a parallax
        # slide never uncovers sky, and extended flat past the last
        # authored point when the dock runs wider still.
        pad = 12.0
        path = QPainterPath(QPointF(-pad, bottom))
        path.lineTo(-pad, pts[0][1])
        path.lineTo(pts[0][0], pts[0][1])
        for i in range(1, len(pts)):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            path.quadTo(QPointF((x0 + x1) / 2, y0 if i % 2 else y1),
                        QPointF(x1, y1))
            if x0 > w + pad:
                break
        end = max(w, pts[-1][0]) + pad
        path.lineTo(end, pts[-1][1])
        path.lineTo(end, bottom)
        path.closeSubpath()
        return path

    @staticmethod
    def _ridge_y(pts, w, x):
        # Absolute-x interpolation; w is kept for signature stability
        # (and clamping keeps feet sane past the authored ends).
        if x <= pts[0][0]:
            return pts[0][1]
        for i in range(1, len(pts)):
            if x <= pts[i][0]:
                x0, y0 = pts[i - 1]
                x1, y1 = pts[i]
                t = (x - x0) / max(1e-6, x1 - x0)
                return y0 + (y1 - y0) * t
        return pts[-1][1]

    @staticmethod
    def _ridge_y_abs(pts, x):
        """As _ridge_y, but for polylines in absolute design-x (the
        ribbon features: foothills, peaks) rather than width fractions."""
        if x <= pts[0][0]:
            return pts[0][1]
        for i in range(1, len(pts)):
            if x <= pts[i][0]:
                x0, y0 = pts[i - 1]
                x1, y1 = pts[i]
                t = (x - x0) / max(1e-6, x1 - x0)
                return y0 + (y1 - y0) * t
        return pts[-1][1]

    def _lake_geom(self, lx, lry, wd):
        """Lake centre y and vertical radius — the single source of
        truth for where a lake's water sits. With the banner extended
        behind the tabs the lakes swell and ease down into the new
        foreground so the water reads nearer; the shift is kept small
        so wading animals still sink convincingly at the waterline."""
        base = min(self._ridge_y(_FRONT_PTS, wd, lx) + 4.6,
                   _DESIGN_H - lry - 1)
        extra = min(self._design_bot() - _DESIGN_H, 10.0)
        ry = lry * (1.0 + 0.035 * extra)
        return base + min(extra * 0.3, 3.0), ry

    def _lake_at(self, x, wd):
        """The lake under design-space x, if visible — (lx, ly, rx, ry)."""
        for lx, lrx, lry, _m in _LAKES:
            if lx + lrx < wd - 4 and abs(x - lx) < lrx * 0.8:
                ly, ry = self._lake_geom(lx, lry, wd)
                return lx, ly, lrx, ry
        return None

    def _avoid_lake(self, px, wd):
        """Nudge a stopping point onto the nearest shore so nobody ends
        up grazing waist-deep in a lake."""
        for lx, lrx, _lry, _m in _LAKES:
            if lx + lrx < wd - 4 and abs(px - lx) < lrx * 0.95 + 4:
                return lx - lrx - 6 if px < lx else lx + lrx + 6
        return px

    # ── painting ──────────────────────────────────────────────────────
    def paintEvent(self, e):
        try:
            self._paint()
        except Exception:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor("#2A3D33"))
            p.end()

    def _paint(self):
        # Sample the sky live each frame so fast day-cycles glide rather
        # than stepping at the sky-tick rate.
        try:
            d, sun_f, moon_f = self._sky_state(self._now())
        except Exception:
            d, sun_f, moon_f = self._dark, self._sun_frac, self._moon_frac
        s = _H / _DESIGN_H
        wd = self.width() / s          # width in design space
        bot = self._design_bot()       # bottom edge (>_DESIGN_H behind tabs)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.scale(s, s)

        if not self._scene_on:
            # The banner, switched off: a quiet survey-sheet header. Unlike the
            # living scene (which runs its own day/night), the still header
            # follows the plugin's light/dark theme via theme.BANNER_TOP /
            # BANNER_GROUND. The band paints over the contours' lower skirts,
            # and the wordmark paints last so the ring-o stands whole in front
            # of the band — the sliced-baseline tuck is a live-mode conceit.
            from . import theme as _thm
            p.fillRect(QRectF(0, 0, wd, bot), QColor(_thm.BANNER_TOP))
            self._draw_still_contours(p, wd, _thm)
            p.fillRect(QRectF(0, 34.0, wd, bot - 34.0),
                       QColor(_thm.BANNER_GROUND))
            if self._wordmark:
                self._draw_wordmark(p, 0.85)
            p.end()
            return

        # Overcast/foggy skies dim and grey the daytime blue-green.
        ws = self._weather_strength
        overcast = (self._weather in ("cloud", "fog", "rain", "storm")) * ws
        sky = _lerp(_SKY_DAY, _SKY_NIGHT, d)
        if overcast:
            sky = _lerp(sky.name(), _lerp(_FOG_DAY, _FOG_NIGHT, d).name(),
                        0.55 * overcast)
        # Golden hour: around sunrise and sunset the sky itself warms
        # toward amber — and because every distant plane hazes toward
        # `sky`, the warmth suffuses the mountains and foothills too,
        # exactly as low light does. Swallowed by any real weather.
        gold = max(0.0, 1.0 - abs(d - 0.5) / 0.30) * (1.0 - overcast)
        if gold > 0.02:
            sky = _lerp(sky.name(), "#D9A868", 0.16 * gold)
        # A touch cooler overhead, a faint glow at the horizon — the sky
        # recedes instead of reading as a paint fill.
        sky_g = QLinearGradient(0, 0, 0, 26.0)
        sky_g.setColorAt(0.0, sky.darker(104))
        sky_g.setColorAt(1.0, sky.lighter(104))
        p.fillRect(QRectF(0, 0, wd, bot), sky_g)
        if gold > 0.02:
            # …and a deeper amber blush low over the ranges.
            glow = QColor("#E8A96B")
            g_gold = QLinearGradient(0, 2.0, 0, 22.0)
            c_hi = QColor(glow)
            c_hi.setAlphaF(0.04 * gold)
            c_lo = QColor(glow)
            c_lo.setAlphaF(0.34 * gold)
            g_gold.setColorAt(0.0, c_hi)
            g_gold.setColorAt(1.0, c_lo)
            p.fillRect(QRectF(0, 2.0, wd, 20.0), g_gold)

        season = self._season()
        self._season_cache = season

        stars_hidden = overcast > 0.4
        if d > 0.6 and not stars_hidden:
            p.setPen(Qt.PenStyle.NoPen)
            star = QColor(_CHALK)
            for i, (fx, fy, r) in enumerate(
                    ((0.45, 6, 0.9), (0.62, 3.5, 0.6), (0.70, 12, 0.7),
                     (0.80, 5, 0.6), (0.86, 8, 0.7), (0.93, 11, 0.6))):
                # Each star breathes on its own slow cycle.
                tw = 0.72 + 0.28 * math.sin(self._t * (0.9 + 0.23 * i)
                                            + i * 2.4)
                star.setAlphaF(0.7 * tw * (d - 0.6) / 0.4 * (1 - overcast))
                p.setBrush(star)
                p.drawEllipse(QPointF(fx * wd, fy), r, r)
        if self._shoot is not None and d > 0.6 and not stars_hidden:
            # A meteor: bright head, tail fading along the track it
            # just burned. Blink and you miss it.
            sht = self._shoot
            a = math.sin(min(1.0, sht["age"] / 0.9) * math.pi)
            hx = sht["x"] + sht["dx"] * sht["age"]
            hy = sht["y"] + sht["dy"] * sht["age"]
            ln = math.hypot(sht["dx"], sht["dy"])
            ux, uy = sht["dx"] / ln, sht["dy"] / ln
            trail = QColor(_CHALK)
            for seg0, seg1, al, wpen in ((7.5, 3.0, 0.16, 0.9),
                                         (4.5, 1.2, 0.34, 0.7),
                                         (2.2, 0.0, 0.60, 0.55)):
                trail.setAlphaF(al * a)
                p.setPen(QPen(trail, wpen))
                p.drawLine(QPointF(hx - ux * seg0, hy - uy * seg0),
                           QPointF(hx - ux * seg1, hy - uy * seg1))
            head = QColor(_CHALK)
            head.setAlphaF(0.9 * a)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(head)
            p.drawEllipse(QPointF(hx, hy), 0.5, 0.5)

        # On rare clear winter nights at high latitudes, the aurora.
        if d > 0.75 and season == "winter" and overcast < 0.2:
            lat, _lon = self._latlon()
            seed = math.sin(self._now().toordinal() * 12.9898) * 43758.5453
            if lat >= 53.0 and (seed - math.floor(seed)) < 0.30:
                self._draw_aurora(p, wd, d)

        par = self._par

        # The arc starts east of the wordmark so a low sun/moon never
        # sits on the letters. Drawn BEFORE the ranges: the sun climbs
        # from behind the mountain wall and slips back behind it, and a
        # summit crossing its path genuinely eclipses it.
        arc_x0 = min(150.0, wd * 0.45)
        if sun_f is not None and d < 1.0:
            f = sun_f
            x, y = (arc_x0 + f * (wd - 40 - arc_x0),
                    24 - 15 * math.sin(math.pi * f))
            p.setPen(QPen(_lerp(_LEAF_DAY, _LEAF_NIGHT, d), 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setOpacity(1.0 - d)
            p.drawEllipse(QPointF(x, y), 7, 7)
            p.setOpacity(1.0)
        self._moon_lit = 0.0
        if moon_f is not None and d > 0.0:
            # The real lunar phase: lit shape = limb semicircle on the
            # bright side closed by the terminator, a semi-ellipse that
            # bulges lit-side for a crescent and dark-side for a
            # gibbous. Around new moon there is nothing to draw.
            ph_m = self._moon_phase()
            illum = 0.5 * (1.0 - math.cos(2 * math.pi * ph_m))
            self._moon_lit = illum
            if illum > 0.04:
                f = moon_f
                x, y = (arc_x0 + f * (wd - 40 - arc_x0),
                        22 - 14 * math.sin(math.pi * f))
                r = 6.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setOpacity(d)
                dim = QColor(_CHALK)
                dim.setAlphaF(0.14)         # earthshine: the dark limb,
                p.setBrush(dim)             # just there against the sky
                p.drawEllipse(QPointF(x, y), r, r)
                s = 1.0 if ph_m < 0.5 else -1.0   # waxing lights the right
                w = math.cos(2 * math.pi * ph_m)  # terminator: +1 new..-1 full
                lit = QPainterPath(QPointF(x, y - r))
                lit.arcTo(QRectF(x - r, y - r, r * 2, r * 2), 90, -180 * s)
                rx = max(0.05, abs(w) * r)
                lit.arcTo(QRectF(x - rx, y - r, rx * 2, r * 2), -90,
                          180 * s * (1.0 if w > 0 else -1.0))
                lit.closeSubpath()
                p.setBrush(QColor(_CHALK))
                p.drawPath(lit)
                p.setOpacity(1.0)

        self._draw_mountains(p, wd, d, sky, overcast, par, season)
        # Cumulus rides overhead, nearer than the wall — and can drift
        # across the sun it just rose past.
        self._draw_clouds(p, wd, d, overcast, par)

        # The deep middle distance: a far skein of birds, patchwork
        # foothill farmland, then valley mist veiling both.
        self._draw_far_birds(p, wd, d, par)
        self._draw_foothills(p, wd, d, sky, overcast, par)
        if wd > 490:
            self._draw_valley_mist(p, wd, d)

        # A rainbow hangs briefly over the ranges as rain clears.
        if self._rainbow > 0.02 and d < 0.7:
            al = self._rainbow * (1.0 - d) * 0.5
            cxr = wd * 0.55
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i, col in enumerate(("#D85A30", "#E4C25A", "#7CB56B",
                                     "#6C7BD0")):
                rc = QColor(col)
                rc.setAlphaF(al)
                p.setPen(QPen(rc, 1.1))
                r = 30 - i * 1.15
                p.drawArc(QRectF(cxr - r, _DESIGN_H - r, r * 2, r * 2),
                          20 * 16, 140 * 16)
            p.setPen(Qt.PenStyle.NoPen)

        # Hills carry a soft top-light so they read as rounded ground, not
        # flat cut-outs.
        p.setPen(Qt.PenStyle.NoPen)
        back_c = _lerp(_BACK_DAY, _BACK_NIGHT, d)
        front_c = QColor(_FRONT)
        if season == "winter":
            # Frosted, dormant grass: greens wash out to a cold pale
            # buff-grey, the near hill a muted sage.
            back_c = _lerp(back_c.name(), "#D6DCCF" if d < 0.5 else "#3E4A44",
                           0.72)
            front_c = _lerp(front_c.name(),
                            "#6E7C55" if d < 0.5 else "#28372E", 0.62)
        elif season == "autumn":
            # A tired, tawnier sward.
            back_c = _lerp(back_c.name(), "#A9B583", 0.22)
            front_c = _lerp(front_c.name(), "#4C6A3C", 0.2)
        # Lying snow blankets both hills white before any footprints
        # can be pressed into it.
        cover = self._snowcover
        snow_c = _lerp("#EDF2F3", "#3A4849", d)
        if cover > 0.0:
            back_c = _lerp(back_c.name(), snow_c.name(), 0.9 * cover)
            front_c = _lerp(front_c.name(), snow_c.name(), 0.85 * cover)
        # Even the back hill lifts a whisper toward the sky — aerial
        # perspective against the crisp near ground.
        back_c = _lerp(back_c.name(), sky.name(), 0.07)
        p.save()
        p.translate(-_PAR_BACK * par, 0)
        back_path = self._ridge_path(_BACK_PTS, wd)
        back_g = QLinearGradient(0, 18, 0, _DESIGN_H)
        back_g.setColorAt(0.0, back_c.lighter(107))
        back_g.setColorAt(0.45, back_c)
        back_g.setColorAt(1.0, back_c.darker(107))
        p.setBrush(back_g)
        p.drawPath(back_path)
        self._hill_form(p, _BACK_PTS, wd, back_path, d, cover)
        self._draw_back_tufts(p, wd, d, back_c, cover)
        self._draw_cloud_shadows(p, wd, d, back_path, _BACK_PTS, overcast)
        # The rivers' upper reaches lie on this slope, so they go down
        # now — BEFORE the far wildlife — and animals crossing the back
        # hill ford in front of the water instead of sliding beneath
        # it. The front hill will bury the lower reaches; a clipped
        # second pass restores them on the front plane. Both passes use
        # the front plane's parallax so the reaches always meet.
        p.save()
        p.translate(-(_PAR_FRONT - _PAR_BACK) * par, 0)
        self._draw_rivers(p, wd, d, bot, sky)
        # The tucks ride the river's own parallax so they never slide
        # off the channel; against the hill the shift is invisible
        # because the gradient they wear is purely vertical.
        self._draw_river_shoulders(p, wd, d, back_g)
        p.restore()

        tree_c = _lerp(_TREE_DAY, _TREE_NIGHT, d)
        # Broadleaves follow the calendar: amber in autumn, bare in
        # winter, blossom-flecked in spring. Conifers stay evergreen.
        if season == "autumn":
            leaf_c = _lerp("#B5762F", _INK, d * 0.7)
        elif season == "winter":
            leaf_c = _lerp("#6B5E4E", _INK, d * 0.7)
        else:
            leaf_c = tree_c
        bare = season == "winter"
        blossom = season == "spring"
        # Everything rooted on the back ridge is a plane away: its greens
        # lift toward the sky. (The veteran oak keeps the pure leaf_c —
        # it stands on the near hill.)
        leaf_bk = _lerp(leaf_c.name(), sky.name(), 0.10)
        tree_bk = _lerp(tree_c.name(), sky.name(), 0.10)
        bark_bk = _lerp(_lerp(_BARK_DAY, _BARK_NIGHT, d).name(),
                        sky.name(), 0.10)
        forest_far = _lerp(leaf_bk.name(), sky.name(), 0.30)
        self._draw_forest(p, wd, leaf_bk, conifer_c=tree_bk, bare=bare,
                          far_c=forest_far)
        self._draw_forest(p, wd, leaf_bk, span=_FOREST2, conifer_c=tree_bk,
                          bare=bare, far_c=forest_far)

        def _tree_shadow(txx, tss, tyy):
            s2 = QColor(_INK)
            s2.setAlphaF(0.10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(s2)
            p.drawEllipse(QPointF(txx + 0.5, tyy + 0.4), 3.1 * tss, 0.5)

        # Low scrub — bramble and blackthorn clumps — knits the ridge
        # together between the trees, hazed with the rest of the plane.
        p.setPen(Qt.PenStyle.NoPen)
        for sx, ss in _SCRUB:
            if sx + 4 * ss >= wd - 4:
                continue
            sy = self._ridge_y(_BACK_PTS, wd, sx) + 1
            for j, (ox, oy, rx2, ry2) in enumerate(
                    ((0.0, -0.9, 2.0, 1.15), (-1.6, -0.5, 1.4, 0.85),
                     (1.7, -0.55, 1.5, 0.9))):
                p.setBrush(tree_bk if (int(sx) + j) % 2 else
                           QColor(tree_bk).lighter(112))
                p.drawEllipse(QPointF(sx + ox * ss, sy + oy * ss),
                              rx2 * ss, ry2 * ss)

        # Roughly two in five standalone trees stand proud of the far
        # slope, drawn after the wildlife so animals slip behind them.
        front_trees = []
        for tx, kind, ts in _TREES:
            if tx + 6 * ts < wd - 4:
                if (int(tx) * 13) % 10 < 4:
                    front_trees.append((tx, kind, ts))
                    continue
                ty = self._ridge_y(_BACK_PTS, wd, tx) + 1
                _tree_shadow(tx, ts, ty)
                if kind == "b":
                    self._draw_broadleaf(p, tx, ty, ts, leaf_bk, bare=bare,
                                         blossom=blossom, trunk=bark_bk)
                else:
                    self._draw_conifer(p, tx, ty, ts, tree_bk)

        # Far-layer wildlife walks the back hill — small, and about to be
        # partly hidden by the front hill: instant depth. Within each
        # band the deeper animals draw first so nearer ones overlap them,
        # and the deepest fade toward the haze like everything else that
        # far away.
        # Painter's order: by each walker's actual ground line, higher
        # (farther) first, so nearer animals overlap farther ones.
        for wk in sorted((w for w in self._walkers
                          if not w["flying"] and w["layer"] == "back"),
                         key=lambda w: self._sort_y(w, wd)):
            p.setOpacity(min(1.0, 0.60 + 1.15 * wk["depth"]))
            self._render_walker(p, wk, wd)
        p.setOpacity(1.0)

        for tx, kind, ts in front_trees:
            ty = self._ridge_y(_BACK_PTS, wd, tx) + 1
            _tree_shadow(tx, ts, ty)
            if kind == "b":
                self._draw_broadleaf(p, tx, ty, ts, leaf_bk, bare=bare,
                                     blossom=blossom, trunk=bark_bk)
            else:
                self._draw_conifer(p, tx, ty, ts, tree_bk)

        # Autumn leaves drifting off the woods.
        if self._leaves:
            lf_c = _lerp("#C08A3E", "#6B4E2A", d)
            p.setPen(Qt.PenStyle.NoPen)
            for lx2, ly2, spin in self._leaves:
                c2 = QColor(lf_c)
                c2.setAlphaF(0.85)
                p.setBrush(c2)
                p.save()
                p.translate(lx2, ly2)
                p.rotate(math.degrees(spin) % 360.0)
                p.drawEllipse(QPointF(0, 0), 0.85, 0.45)
                p.restore()
        p.restore()                     # end of the back-hill plane

        if self._wordmark:
            self._draw_wordmark(p, d)

        p.save()
        p.translate(-_PAR_FRONT * par, 0)
        p.setPen(Qt.PenStyle.NoPen)
        # The near meadow closes at the widget's real bottom, so when the
        # banner extends behind the tab strip the grass simply carries on
        # down rather than stopping at the old scene edge.
        front_path = self._ridge_path(_FRONT_PTS, wd, bottom=bot)
        front_g = QLinearGradient(0, 26, 0, bot)
        front_g.setColorAt(0.0, front_c.lighter(109))
        front_g.setColorAt(0.4, front_c)
        front_g.setColorAt(1.0, front_c.darker(112))
        p.setBrush(front_g)
        p.drawPath(front_path)
        self._hill_form(p, _FRONT_PTS, wd, front_path, d, cover)
        self._draw_cloud_shadows(p, wd, d, front_path, _FRONT_PTS, overcast)

        # UK habitat dressing on the front hill.
        # The stiff dressing (wall, gorse, heather) is static under
        # slow light change: record it once per (width, light, season,
        # snow) step and replay — a blit instead of Python paint loops
        # every frame. The hedgerows record separately because they
        # replay AFTER the mid wildlife (animals walk behind the hedge
        # line, legs hidden — that occlusion is what sells their
        # scale); the meadow and bluebells stay live in the wind.
        dkey = (int(wd), int(d * 50), season, int(cover * 20))
        if self._dress_key != dkey:
            self._dress_key = dkey
            pic = QPicture()
            pp = QPainter(pic)
            pp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_wall(pp, wd, d)
            self._draw_gorse(pp, wd, d)
            self._draw_heather(pp, wd, d)
            pp.end()
            self._dress_pic = pic
            hpic = QPicture()
            pp = QPainter(hpic)
            pp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_hedgerows(pp, wd, d, leaf_c, season, cover, snow_c,
                                 bare, blossom)
            pp.end()
            self._hedge_pic = hpic
        p.drawPicture(0, 0, self._dress_pic)
        self._draw_meadow(p, wd, d)
        self._draw_bluebells(p, wd, d)
        self._draw_oak(p, wd, d, leaf_c, season)

        # Footprints pressed into the lying snow — dark hollows on the
        # white ground, fading as they age (and as the snow melts).
        if self._tracks and cover > 0.05:
            p.setPen(Qt.PenStyle.NoPen)
            trk = _lerp("#8AA1A7", _INK, 0.25 + d * 0.45)
            for tcx, gy, t0, lay in self._tracks:
                age = self._t - t0
                if age > 24:
                    continue
                trk.setAlphaF(0.5 * cover * max(0.0, 1.0 - age / 24.0))
                p.setBrush(trk)
                r = 0.68 if lay == "fore" else 0.5
                p.drawEllipse(QPointF(tcx, gy), r, r * 0.7)

        # Whichever light is up lays its glitter path on the lakes.
        celest_x = None
        if sun_f is not None and d < 0.6:
            celest_x = arc_x0 + sun_f * (wd - 40 - arc_x0)
        elif moon_f is not None and d > 0.4 and self._moon_lit > 0.10:
            # A thin crescent lays no glitter; new-moon nights are dark.
            celest_x = arc_x0 + moon_f * (wd - 40 - arc_x0)
        for lx, lrx, lry, mults in _LAKES:
            if lx + lrx < wd - 4:
                ly, ry = self._lake_geom(lx, lry, wd)
                self._draw_lake(p, lx, ly, lrx, ry, d, mults,
                                glint_x=celest_x)

        # Fish rising: rings spreading and fading on the water.
        if self._rings:
            p.setBrush(Qt.BrushStyle.NoBrush)
            for rxp, ryp, rr in self._rings:
                fade = max(0.0, 1.0 - rr / 3.2)
                rc = QColor(_CHALK)
                rc.setAlphaF(0.4 * fade)
                p.setPen(QPen(rc, 0.55))
                p.drawEllipse(QPointF(rxp, ryp), rr, rr * 0.32)
                if rr > 1.0:
                    rc.setAlphaF(0.25 * fade)
                    p.setPen(QPen(rc, 0.45))
                    p.drawEllipse(QPointF(rxp, ryp), rr * 0.55,
                                  rr * 0.18)
            p.setPen(Qt.PenStyle.NoPen)

        # Rivers, second pass: repaint only where the front hill buried
        # pass one — same geometry, same parallax, so the upper and
        # lower reaches join invisibly at the crest.
        p.save()
        p.setClipPath(front_path)
        self._draw_rivers(p, wd, d, bot, sky)
        p.restore()

        for wk in sorted((w for w in self._walkers
                          if w["flying"] or w["layer"] == "mid"),
                         key=lambda w: self._sort_y(w, wd)):
            self._render_walker(p, wk, wd)
        self._draw_swallows(p, wd, d)   # hawking low over the meadow
        # The hedgerows overlap the wildlife that just drew: a deer at
        # the hedge stands BEHIND it, legs hidden, body over the top —
        # the occlusion that makes its size read true.
        p.drawPicture(0, 0, self._hedge_pic)
        p.restore()                     # end of the front-hill plane

        p.save()
        p.translate(-_PAR_NEAR * par, 0)
        # Foreground grass tufts — the near layer walks in front of them.
        # Under lying snow they all but disappear beneath the blanket.
        tuft = _lerp(_TUFT_DAY, _TUFT_NIGHT, d)
        if cover > 0.0:
            tuft = _lerp(tuft.name(), snow_c.name(), 0.8 * cover)
        pen = QPen(tuft, 1.1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        tx = 30
        while tx < wd + 8:
            wv = self._wind_at(tx) * 1.3      # gusts lean the blades
            for ddx, h in ((-2.2, 3.2), (0, 4.2), (2.2, 3.0)):
                p.drawLine(QPointF(tx + ddx, _DESIGN_H),
                           QPointF(tx + ddx * 1.6 + wv, _DESIGN_H - h))
            tx += 90 + (tx * 7 % 40)

        # The extension band's own meadow, behind the nearest wildlife.
        self._draw_near_band(p, wd, d, bot, season, cover, snow_c, "mid")
        self._draw_band_props(p, wd, d, bot, season, cover, snow_c)
        self._draw_puddles(p, wd, d, bot)

        for wk in sorted((w for w in self._walkers
                          if not w["flying"] and w["layer"] == "fore"),
                         key=lambda w: self._sort_y(w, wd)):
            self._render_walker(p, wk, wd)

        # The very nearest rank overlaps even the foreground wildlife —
        # animals walk BETWEEN the grass, not on top of it.
        self._draw_near_band(p, wd, d, bot, season, cover, snow_c, "front")
        self._draw_seeds(p, wd, d)
        self._draw_glowworms(p, wd, d, bot, season)
        p.restore()                     # end of the near plane

        # The nearest ground falls into shade under the "camera": a
        # whisper of vignette along the bottom edge.
        vg = QLinearGradient(0, bot - 6.0, 0, bot)
        v0 = QColor(_INK)
        v0.setAlphaF(0.0)
        v1 = QColor(_INK)
        v1.setAlphaF(0.10 + 0.05 * d)
        vg.setColorAt(0.0, v0)
        vg.setColorAt(1.0, v1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(vg)
        p.drawRect(QRectF(0, bot - 6.0, wd, 6.0))

        # The dusk murmuration: a ribbon of starlings folding over itself
        # as it crosses.
        if self._flock is not None:
            fl = self._flock
            p.setPen(Qt.PenStyle.NoPen)
            sb = QColor(_INK)
            sb.setAlphaF(0.75)
            p.setBrush(sb)
            ft = fl["t"]
            for i in range(38):
                ph = i * 0.53
                px = (fl["x"] + 13 * math.sin(ft * 0.9 + ph)
                      + (i % 9) * 2.0)
                py = (10.5 + 5.5 * math.sin(ft * 1.25 + ph * 1.7)
                      + 2.6 * math.sin(ft * 0.6 + ph * 0.43))
                p.drawEllipse(QPointF(px, py), 0.42, 0.3)

        # Nearer than the scene itself: out-of-focus plants leaning in
        # from the bottom corners.
        self._draw_foreground_flora(p, wd, d, bot, par)

        self._draw_weather(p, wd, d, bot)

        # Season crossfade: the old season's last frame dissolves over
        # the new one. Dropped if the widget has been resized since.
        if (self._season_fade > 0.0 and self._season_snap is not None
                and self._season_snap_size == (self.width(),
                                               self.height())):
            p.save()
            p.resetTransform()
            p.setOpacity(self._season_fade)
            p.drawPixmap(0, 0, self._season_snap)
            p.restore()
        p.end()

    def _draw_mountains(self, p, wd, d, sky, overcast, par, season):
        """The four-range fell wall. Static geometry under slow light
        change, so each range is recorded into a QPicture once per
        (width, light, weather, season) step and replayed with its own
        parallax slide; the waterfalls are drawn live on top."""
        if wd <= _MTN_LAYERS[-1]["gate"] - 20:
            return
        key = (int(wd), int(d * 50), int(overcast * 20), season)
        if self._mtn_key != key:
            self._mtn_key = key
            self._mtn_pics = []
            for lay in _MTN_LAYERS:
                if wd <= lay["gate"]:
                    self._mtn_pics.append(None)
                    continue
                pic = QPicture()
                pp = QPainter(pic)
                pp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                self._paint_mtn_layer(pp, lay, wd, d, sky, overcast,
                                      season)
                pp.end()
                self._mtn_pics.append(pic)
        for lay, pic in zip(_MTN_LAYERS, self._mtn_pics):
            if pic is None:
                continue
            p.save()
            p.translate(-lay["par"] * par, 0)
            p.drawPicture(0, 0, pic)
            p.restore()
        if wd > _MTN_LAYERS[-1]["gate"]:
            # Waterfalls spill from the near range's cols down to the
            # low ground, their tops seated on the actual ridgeline.
            p.save()
            p.translate(-_PAR_PEAKS * par, 0)
            falls = _lerp(_FALLS_DAY, _FALLS_NIGHT, d)
            for rx, wdt in ((_RIVER_X, 2.2), (_RIVER2_X, 3.0)):
                if rx + 8 < wd:
                    top = self._ridge_y_abs(_MTN_D, rx) + 0.4
                    pen = QPen(falls, wdt)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    yb = self._ridge_y(_BACK_PTS, wd, rx)
                    p.drawLine(QPointF(rx - 1.4, top), QPointF(rx - 1.4, yb))
                    p.drawLine(QPointF(rx + 1.6, top + 1),
                               QPointF(rx + 1.6, yb))
            p.setPen(Qt.PenStyle.NoPen)
            p.restore()

    def _paint_mtn_layer(self, pp, lay, wd, d, sky, overcast, season):
        """One range: silhouette fill from the ridgeline, then — by
        detail level — per-segment facet relief and, in the cold
        months, snow on the tops."""
        pts, jag = lay["pts"], lay["jag"]
        col = _lerp(lay["day"], lay["night"], d)
        col = _lerp(col.name(), sky.name(), lay["haze"])
        if overcast:
            col = _lerp(col.name(), _lerp(_FOG_DAY, _FOG_NIGHT, d).name(),
                        0.4 * overcast)
        ridge = QPainterPath(QPointF(jag[0][0], _DESIGN_H))
        for px, py in jag:
            ridge.lineTo(px, py)
        ridge.lineTo(jag[-1][0], _DESIGN_H)
        ridge.closeSubpath()
        pp.setPen(Qt.PenStyle.NoPen)
        pp.setBrush(col)
        pp.drawPath(ridge)
        if lay["detail"] >= 1:
            self._mtn_facets(pp, pts, wd, ridge, col, overcast)
        if lay["detail"] >= 2:
            self._mtn_detail(pp, pts, wd, ridge, col)
        # Seasonal snow: the stored snowline is the SPRING remnant line
        # (only the highest tops keep a fading patch); winter drops it
        # 6 units so whole fell tops whiten; the rest of the year the
        # fells are bare.
        sl = lay["snowline"]
        if sl is not None:
            if season == "winter":
                sl = sl + 6.0
            elif season != "spring":
                sl = None
        if sl is not None:
            snow = _lerp(_SNOW_DAY, _SNOW_NIGHT, d)
            snow_sh = _lerp(snow.name(),
                            "#A9BCCE" if d < 0.5 else "#3E5262", 0.4)
            rock = _lerp(col.name(), _INK, 0.30)
            pp.save()
            pp.setClipPath(ridge)
            self._snow_mantle(pp, pts, wd, sl, snow, snow_sh, rock)
            pp.restore()

    def _mtn_facets(self, pp, pts, wd, ridge, col, overcast):
        """Rock relief: light from the west, so every flank falling away
        east sits in shade — steeper faces darker — and steep west
        flanks catch the light. Facets follow the AUTHORED segments so
        the relief carries the ridge's real forms; the fill's jitter
        stays as surface texture over them."""
        shade = _lerp(col.name(), _INK, 0.20)
        deep = _lerp(col.name(), _INK, 0.34)
        lit = _lerp(col.name(), _CHALK, 0.16)
        if overcast:
            shade = _lerp(shade.name(), col.name(), 0.5 * overcast)
            deep = _lerp(deep.name(), col.name(), 0.5 * overcast)
            lit = _lerp(lit.name(), col.name(), 0.6 * overcast)
        pp.save()
        pp.setClipPath(ridge)
        pp.setPen(Qt.PenStyle.NoPen)
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            if x0 > wd + 12:
                break
            dy = y1 - y0
            if abs(dy) < 1.4:
                continue                     # near-level crest: no facet
            steep = abs(dy) / max(2.0, x1 - x0)
            if steep < 0.14 or min(y0, y1) > 17.0:
                continue                     # gentle slopes and low moor
                                             # roll smooth — relief belongs
                                             # to the high rock
            if dy > 0:                       # falling away east → shade
                pp.setBrush(deep if steep > 0.55 else shade)
            elif steep > 0.4:                # steep west flank → lit
                pp.setBrush(lit)
            else:
                continue
            drop = min(10.0, abs(dy) * 1.1 + 1.5)
            facet = QPainterPath(QPointF(x0, y0))
            facet.lineTo(x1, y1)
            facet.lineTo(x1, y1 + drop)
            facet.lineTo(x0, y0 + drop * 0.55)
            facet.closeSubpath()
            pp.drawPath(facet)
        pp.restore()

    def _mtn_detail(self, pp, pts, wd, ridge, col):
        """The near range's fine grain: gully lines scored down the big
        faces, pale scree fans spilling into the cols beneath them, and
        dark corrie scoops with moraine lips under the named summits."""
        gully = _lerp(col.name(), _INK, 0.32)
        scree = _lerp(col.name(), _CHALK, 0.16)
        pp.save()
        pp.setClipPath(ridge)
        for i in range(1, len(pts) - 1):
            px, py = pts[i]
            if px > wd + 10:
                break
            ly, ry_ = pts[i - 1][1], pts[i + 1][1]
            if py < ly and py < ry_:
                # A summit: score gullies down each face tall enough
                # to hold them — two on the shaded east, one west.
                for side, nx, ny, kmax in ((1, pts[i + 1][0], ry_, 2),
                                           (-1, pts[i - 1][0], ly, 1)):
                    face = ny - py
                    if face < 4.5:
                        continue
                    for k in range(kmax):
                        n = math.sin((px + k * 17 + side * 7) * 9.1) * 999.0
                        fr = n - math.floor(n)
                        run = abs(nx - px)
                        gp = QPainterPath(
                            QPointF(px + side * (1.0 + k * 1.8),
                                    py + 1.2 + k))
                        length = face * (0.45 + 0.30 * fr)
                        for s2 in range(1, 5):
                            t = s2 / 4.0
                            gx = (px + side * (1.0 + k * 1.8
                                               + t * run * 0.42))
                            gy = py + 1.2 + k + t * length
                            gx += math.sin((gy + fr * 9.0) * 1.7) * 0.5
                            gp.lineTo(QPointF(gx, gy))
                        c = QColor(gully)
                        c.setAlphaF(0.34 if side > 0 else 0.20)
                        pen = QPen(c, 0.4)
                        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                        pp.setPen(pen)
                        pp.setBrush(Qt.BrushStyle.NoBrush)
                        pp.drawPath(gp)
            elif py > ly + 3.5 and py > ry_ + 3.5 and py > 18.0:
                # A col below steep ground: a pale scree fan spills in.
                n = math.sin(px * 7.7) * 999.0
                if (n - math.floor(n)) < 0.55:
                    fan = QPainterPath(QPointF(px - 4.5, py + 2.5))
                    fan.lineTo(QPointF(px + 4.5, py + 2.5))
                    fan.lineTo(QPointF(px + 1.0, py - 3.6))
                    fan.closeSubpath()
                    c = QColor(scree)
                    c.setAlphaF(0.5)
                    pp.setPen(Qt.PenStyle.NoPen)
                    pp.setBrush(c)
                    pp.drawPath(fan)
        # Corrie scoops on the named east faces: a dark glacial
        # armchair with a pale moraine lip at its mouth.
        dark = QColor(_lerp(col.name(), _INK, 0.24))
        rim = QColor(_lerp(col.name(), _CHALK, 0.22))
        pp.setPen(Qt.PenStyle.NoPen)
        for cx in _CORRIES:
            if cx > wd + 10:
                continue
            sy = self._ridge_y_abs(pts, cx)
            dark.setAlphaF(0.55)
            pp.setBrush(dark)
            pp.drawEllipse(QPointF(cx + 6.0, sy + 4.6), 4.8, 2.3)
            rim.setAlphaF(0.5)
            pp.setBrush(rim)
            pp.drawEllipse(QPointF(cx + 6.0, sy + 6.1), 4.8, 1.0)
        pp.restore()

    def _snow_mantle(self, p, pts, wd, sl, snow, snow_sh, rock):
        """One continuous snow mantle per range, the way snow actually
        lies: it follows the ridgeline wherever the ground climbs past
        the snowline, thickest over the tops, thinning to nothing as
        the ground drops away — a single flowing cap with a ragged
        lower edge, tongues down the gullies, a cool shadow along its
        underside, and rock breaking through on the steep bits. The
        caller has already clipped to the ridge."""
        x0 = max(pts[0][0], -6.0)
        x1 = min(pts[-1][0], wd + 12.0)
        step = 6.5
        run = []

        def flush():
            if len(run) < 3:
                del run[:]
                return
            mantle = QPainterPath(QPointF(run[0][0], run[0][1]))
            for sx, st, _sb in run[1:]:
                mantle.lineTo(QPointF(sx, st))
            for sx, _st, sb in reversed(run):
                mantle.lineTo(QPointF(sx, sb))
            mantle.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(snow)
            p.drawPath(mantle)
            # A cool shadow tucked under the mantle's lower edge.
            shade = QPen(snow_sh, 1.0)
            shade.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(shade)
            for i in range(len(run) - 1):
                if run[i][2] - run[i][1] > 1.2:
                    p.drawLine(QPointF(run[i][0], run[i][2] - 0.4),
                               QPointF(run[i + 1][0], run[i + 1][2] - 0.4))
            p.setPen(Qt.PenStyle.NoPen)
            # Rock breaking through where the cover runs thin.
            p.setBrush(rock)
            for sx, st, sb in run[2:-2:3]:
                n = math.sin(sx * 3.71) * 999.0
                fr = n - math.floor(n)
                if fr > 0.72 and sb - st > 2.2:
                    ry = st + (sb - st) * (0.45 + 0.3 * fr)
                    streak = QPainterPath(QPointF(sx - 0.5, ry + 1.6))
                    streak.lineTo(QPointF(sx - 0.15, ry - 0.9))
                    streak.lineTo(QPointF(sx + 0.4, ry - 0.2))
                    streak.lineTo(QPointF(sx + 0.6, ry + 1.4))
                    streak.closeSubpath()
                    p.drawPath(streak)
            del run[:]

        x = x0
        while x <= x1:
            y = self._ridge_y_abs(pts, x)
            c = max(0.0, min(1.0, (sl - y) / 4.0))
            if c <= 0.0:
                flush()
                x += step
                continue
            n = math.sin(x * 1.31) * 999.0
            fr = n - math.floor(n)
            n2 = math.sin(x * 0.37 + 5.0) * 999.0
            fr2 = n2 - math.floor(n2)
            depth = c * (2.4 + 2.6 * fr2 + 1.0 * fr)
            if fr > 0.84:                 # a tongue down a gully
                depth += c * 2.4
            run.append((x, y - 0.35, y + max(0.5, depth)))
            x += step
        flush()

    def _draw_aurora(self, p, wd, d):
        """Faint green curtains above the ranges — soft overlapping
        gradient strips that come and go in patches, never a repeating
        pattern, and kept clear of the wordmark."""
        t = self._wclock * 0.10
        glow = min(1.0, (d - 0.75) / 0.25)
        p.setPen(Qt.PenStyle.NoPen)
        x = wd * 0.22
        while x < wd - 12:
            patch = math.sin(x * 0.0085 + t * 0.35)
            if patch > 0.15:
                yy = (3.0 + 2.4 * math.sin(x * 0.02 + t)
                      + 1.2 * math.sin(x * 0.043 - t * 0.7))
                hh = 6.5 + 3.0 * math.sin(x * 0.031 + t * 1.3)
                c1 = QColor("#58D6A2")
                c1.setAlphaF(0.14 * glow * patch)
                c2 = QColor("#7FE3C0")
                c2.setAlphaF(0.0)
                grad = QLinearGradient(0, yy, 0, yy + hh)
                grad.setColorAt(0.0, c1)
                grad.setColorAt(1.0, c2)
                p.setBrush(grad)
                p.drawRect(QRectF(x - 1.0, yy, 7.0, hh))
            x += 6.0

    def _draw_far_birds(self, p, wd, d, par):
        """A distant skein — a V of specks tracking the mountain wall,
        a whole sky-depth behind the banner's resident fliers."""
        fb = self._far_birds
        if fb is None or wd <= 520:
            return
        p.save()
        p.translate(-_PAR_PEAKS * par, 0)
        ink = _lerp(_INK, _CHALK, d * 0.55)   # reads against day or dusk
        al = 0.38 * (1.0 - 0.4 * d)
        pen = QPen(ink, 0.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for i, (ox, oy) in enumerate(((0.0, 0.0), (-2.3, 0.9), (-4.6, 1.8),
                                      (2.3, 0.9), (4.6, 1.8))):
            flap = 0.5 * math.sin(fb["t"] * 5.0 + i * 1.7)
            bx = fb["x"] + ox * fb["dir"]
            by = fb["y"] + oy + 0.3 * math.sin(fb["t"] * 1.1 + i)
            c = QColor(ink)
            c.setAlphaF(al)
            pen.setColor(c)
            p.setPen(pen)
            p.drawLine(QPointF(bx - 0.7, by - flap), QPointF(bx, by))
            p.drawLine(QPointF(bx, by), QPointF(bx + 0.7, by - flap))
        p.restore()

    def _draw_seeds(self, p, wd, d):
        """Thistledown riding the summer wind: a seed dot under a fan
        of pappus hairs, leaning with the gusts. The occasional big one
        drifts by soft-focus, nearer than the scene itself."""
        if not self._seeds:
            return
        for sd in self._seeds:
            age = self._t - sd["born"]
            a = min(1.0, age / 1.5) * min(1.0, (wd + 8 - sd["x"]) / 12.0)
            if a <= 0.0:
                continue
            x, y = sd["x"], sd["y"]
            lean = 16.0 * self._wind_at(x)      # degrees, gust-driven
            p.save()
            p.translate(x, y)
            p.rotate(lean)
            fluff = QColor(_CHALK)
            if sd["big"]:
                # Out-of-focus: the foreground-flora trick, three soft
                # passes widening outward.
                for wpen, al in ((1.7, 0.10), (1.0, 0.16), (0.5, 0.28)):
                    fluff.setAlphaF(al * a)
                    pen = QPen(fluff, wpen)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    for k in range(6):
                        ang = math.radians(-150 + 24 * k)
                        p.drawLine(QPointF(0, 0),
                                   QPointF(math.cos(ang) * 3.4,
                                           math.sin(ang) * 3.4))
                fluff.setAlphaF(0.30 * a)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fluff)
                p.drawEllipse(QPointF(0, 0.8), 0.8, 0.8)
            else:
                fluff.setAlphaF(0.55 * a)
                pen = QPen(fluff, 0.32)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                for k in range(6):
                    ang = math.radians(-150 + 24 * k)
                    p.drawLine(QPointF(0, 0),
                               QPointF(math.cos(ang) * 1.3,
                                       math.sin(ang) * 1.3))
                fluff.setAlphaF(0.7 * a)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fluff)
                p.drawEllipse(QPointF(0, 0.35), 0.32, 0.32)
            p.restore()

    def _draw_swallows(self, p, wd, d):
        """Summer-evening swallows hawking low over the meadow: fast,
        jinking sweeps on sickle wings, each bird a phase-driven arc so
        the flight needs no per-frame state."""
        if not self._swallows:
            return
        ink = _lerp("#20303F", _INK, 0.3)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for s in self._swallows:
            age = self._t - s["born"]
            al = max(0.0, min(1.0, age / 1.5, (s["life"] - age) / 2.0))
            if al <= 0.0:
                continue
            x = s["cx"] + s["r"] * math.sin(age * 0.55 + s["ph"])
            if x < 4 or x > wd - 4:
                continue
            vx = math.cos(age * 0.55 + s["ph"])
            y = (33.5 + 3.2 * math.sin(age * 2.3 + s["ph"] * 1.7)
                 + 1.4 * math.sin(age * 5.1))
            c = QColor(ink)
            c.setAlphaF(0.9 * al)
            pen = QPen(c, 0.55)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            dr = 1.0 if vx >= 0 else -1.0
            flap = math.sin(age * 9.0 + s["ph"]) * 0.8
            p.drawLine(QPointF(x, y),
                       QPointF(x - 1.6 * dr, y - 1.0 - flap))
            p.drawLine(QPointF(x, y),
                       QPointF(x + 1.1 * dr, y - 0.9 + flap * 0.4))
            p.drawLine(QPointF(x, y), QPointF(x - 1.4 * dr, y + 0.8))
        p.setPen(Qt.PenStyle.NoPen)

    def _draw_glowworms(self, p, wd, d, bot, season):
        """Glow-worms in the near-band grass on warm summer nights:
        tiny green lamps pulsing in and out, gone in any rain."""
        if season != "summer" or d < 0.7:
            return
        if self._weather in ("rain", "storm") and self._weather_strength > 0.4:
            return
        extra = bot - _DESIGN_H
        if extra < 3.0:
            return
        stren = min(1.0, (d - 0.7) / 0.25)
        p.setPen(Qt.PenStyle.NoPen)
        tx = 55.0
        while tx < wd - 10:
            n = math.sin(tx * 4.13) * 999.0
            fr = n - math.floor(n)
            if fr < 0.55:
                py = _DESIGN_H + extra * (0.35 + fr * 0.5)
                pulse = 0.5 + 0.5 * math.sin(self._t * 0.8 + tx * 0.7)
                al = stren * pulse
                if al > 0.12:
                    lamp = QColor("#C7F07A")
                    lamp.setAlphaF(al * 0.25)
                    p.setBrush(lamp)
                    p.drawEllipse(QPointF(tx, py), 1.0, 1.0)
                    lamp.setAlphaF(al * 0.9)
                    p.setBrush(lamp)
                    p.drawEllipse(QPointF(tx, py), 0.32, 0.32)
            tx += 70.0 + fr * 70.0

    def _draw_puddles(self, p, wd, d, bot):
        """After rain the near band holds water for a while: gleaming
        puddles that grow in the wet and shrink away as the ground
        dries — the same memory the rivers keep with their swell."""
        wet = min(1.0, self._swell * 1.5)
        if self._weather in ("rain", "storm"):
            wet = max(wet, self._weather_strength * 0.7)
        extra = bot - _DESIGN_H
        if wet < 0.08 or extra < 4.0:
            return
        band_y = _DESIGN_H + extra * 0.5
        water = _lerp(_WATER_DAY, _WATER_NIGHT, d)
        p.setPen(Qt.PenStyle.NoPen)
        tx = 150.0
        while tx < wd - 15:
            n = math.sin(tx * 2.47) * 999.0
            fr = n - math.floor(n)
            if fr > 0.45 and self._lake_at(tx, wd) is None:
                py = band_y + (fr - 0.5) * extra * 0.25
                w_ = (3.0 + fr * 3.5) * wet
                c = QColor(water.lighter(112))
                c.setAlphaF(0.75 * min(1.0, wet * 1.4))
                p.setBrush(c)
                p.drawEllipse(QPointF(tx, py), w_, w_ * 0.22)
                gl = QColor(_CHALK)
                gl.setAlphaF(0.30 * wet * (1.0 - 0.5 * d))
                p.setBrush(gl)
                p.drawEllipse(QPointF(tx - w_ * 0.2, py - w_ * 0.05),
                              w_ * 0.45, w_ * 0.08)
            tx += 260.0 + fr * 180.0

    def _draw_valley_mist(self, p, wd, d):
        """A soft band of haze where the ranges meet the low ground —
        aerial perspective's last word, thickest at dawn and dusk when
        real valleys hold their mist."""
        tw = max(0.0, 1.0 - abs(d - 0.5) / 0.35)   # twilight factor
        al = 0.06 + 0.12 * tw + 0.02 * math.sin(self._t * 0.15)
        if al <= 0.02:
            return
        mist = _lerp(_FOG_DAY, _FOG_NIGHT, d)
        y0, y1 = 19.0, 28.0
        g = QLinearGradient(0, y0, 0, y1)
        c0 = QColor(mist)
        c0.setAlphaF(0.0)
        c1 = QColor(mist)
        c1.setAlphaF(al)
        c2 = QColor(mist)
        c2.setAlphaF(0.0)
        g.setColorAt(0.0, c0)
        g.setColorAt(0.72, c1)
        g.setColorAt(1.0, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRect(QRectF(0, y0, wd, y1 - y0))

    def _draw_foothills(self, p, wd, d, sky, overcast, par):
        """Distant patchwork farmland between the mountain wall and the
        back hill — pale field strips with hedgerow seams, glimpsed
        wherever the back hill dips. Fills the missing plane between
        the crags and the wildlife's ground."""
        if wd <= 520:
            return
        base = _lerp(_FIELD_DAY, _FIELD_NIGHT, d)
        base = _lerp(base.name(), sky.name(), 0.25)      # far → sky-lifted
        if overcast:
            base = _lerp(base.name(),
                         _lerp(_FOG_DAY, _FOG_NIGHT, d).name(),
                         0.35 * overcast)
        p.save()
        p.translate(-_PAR_FOOTHILLS * par, 0)
        path = QPainterPath(QPointF(_FOOTHILLS[0][0], _DESIGN_H))
        for px, py in _FOOTHILLS:
            path.lineTo(px, py)
        path.lineTo(_FOOTHILLS[-1][0], _DESIGN_H)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawPath(path)
        # Field strips: alternating tints with hedgerow seams between.
        p.setClipPath(path)
        hedge = _lerp(base.name(), _INK, 0.30)
        fx = float(_FOOTHILLS[0][0])
        k = 0
        while fx < min(_FOOTHILLS[-1][0], wd + 8):
            n = math.sin(fx * 3.77) * 999.0
            fr = n - math.floor(n)
            fw = 46.0 + fr * 46.0
            tint = QColor(base.lighter(107) if k % 2 else base.darker(106))
            tint.setAlphaF(0.38)
            p.setBrush(tint)
            p.drawRect(QRectF(fx, 14.0, fw, 14.0))
            hy = self._ridge_y_abs(_FOOTHILLS, fx + fw)
            hc = QColor(hedge)
            hc.setAlphaF(0.5)
            p.setPen(QPen(hc, 0.4))
            p.drawLine(QPointF(fx + fw, hy + 0.3),
                       QPointF(fx + fw + 2.0, hy + 4.5))
            p.setPen(Qt.PenStyle.NoPen)
            fx += fw
            k += 1
        # Contour hedgerows following the land, so the band recedes as
        # strips of fields rather than one flat slab.
        hc2 = QColor(hedge)
        for drop, aa in ((2.2, 0.40), (4.8, 0.30)):
            hc2.setAlphaF(aa)
            p.setPen(QPen(hc2, 0.35))
            cx0 = float(_FOOTHILLS[0][0])
            step = 26.0
            while cx0 + step < min(_FOOTHILLS[-1][0], wd + 8):
                y0 = self._ridge_y_abs(_FOOTHILLS, cx0) + drop
                y1 = self._ridge_y_abs(_FOOTHILLS, cx0 + step) + drop
                p.drawLine(QPointF(cx0, y0), QPointF(cx0 + step, y1))
                cx0 += step
            p.setPen(Qt.PenStyle.NoPen)
        # A far farmstead below the mountain glen: gable, barn and — at
        # dawn and dusk — a wisp of chimney smoke. Drawn unclipped so
        # the rooftops rise above the band's ridge.
        p.setClipping(False)
        fx0 = 1080.0
        if fx0 + 8 < min(_FOOTHILLS[-1][0], wd):
            fy0 = self._ridge_y_abs(_FOOTHILLS, fx0) + 1.8
            house = _lerp("#D9D4C8", "#37423C", d)
            house = _lerp(house.name(), sky.name(), 0.20)
            roof = _lerp("#7A6F66", "#2C3630", d)
            roof = _lerp(roof.name(), sky.name(), 0.20)
            p.setBrush(house)
            p.drawRect(QRectF(fx0 - 1.6, fy0 - 1.7, 3.2, 1.7))
            gable = QPainterPath(QPointF(fx0 - 1.9, fy0 - 1.7))
            gable.lineTo(QPointF(fx0, fy0 - 2.9))
            gable.lineTo(QPointF(fx0 + 1.9, fy0 - 1.7))
            gable.closeSubpath()
            p.setBrush(roof)
            p.drawPath(gable)
            p.drawRect(QRectF(fx0 + 0.9, fy0 - 2.75, 0.45, 0.8))  # chimney
            p.setBrush(QColor(roof).darker(108))                  # the barn
            p.drawRect(QRectF(fx0 + 2.9, fy0 - 1.3, 2.6, 1.3))
            p.drawRect(QRectF(fx0 + 2.7, fy0 - 1.75, 3.0, 0.5))
            tw2 = max(0.0, 1.0 - abs(d - 0.5) / 0.30)
            if tw2 > 0.05:
                for k in range(3):
                    ph = (self._t * 0.35 + k * 0.33) % 1.0
                    sx2 = (fx0 + 1.12 + ph * 1.6
                           + 0.3 * math.sin(self._t + k))
                    sy2 = fy0 - 3.0 - ph * 2.4
                    smoke = QColor(_CHALK)
                    smoke.setAlphaF(0.35 * tw2 * (1.0 - ph))
                    p.setBrush(smoke)
                    p.drawEllipse(QPointF(sx2, sy2),
                                  0.5 + ph * 0.7, 0.4 + ph * 0.5)
        p.restore()

    def _cloud_xs(self, wd):
        """The three fair-weather clouds' x positions — shared by the
        cumulus in the sky and the shadows they cast on both hills, so
        cloud and shadow visibly travel together as one weather."""
        per = wd + 340.0
        return [(((self._t * (2.6 + 1.2 * k) + k * per / 3.0) % per)
                 - 170.0, k) for k in range(3)]

    def _draw_clouds(self, p, wd, d, overcast, par):
        """Fair-weather cumulus drifting on the same clock as the
        ground shadows below them. They fade out as real weather rolls
        in (the grey wash takes over) and dim to near-nothing at
        night."""
        # Overlapping lobes stack their alpha, so even faint night
        # clouds read as grey smears — fade them fully out by dusk and
        # leave the night sky to the stars and the murmuration.
        al = 0.85 * max(0.0, 1.0 - d / 0.55) * (1.0 - overcast)
        if al <= 0.04:
            return
        sky_c = _lerp(_SKY_DAY, _SKY_NIGHT, d)
        under = _lerp(_CHALK, sky_c.name(), 0.5)
        p.save()
        p.translate(-1.3 * par, 0)
        p.setPen(Qt.PenStyle.NoPen)
        for cx, k in self._cloud_xs(wd):
            cy = 4.8 + 2.4 * k
            s = 0.8 + 0.35 * k
            body = QColor(_CHALK)
            body.setAlphaF(al * (0.85 - 0.1 * k))
            p.setBrush(body)
            for ox, oy, rx, ry in ((0.0, 0.0, 7.0, 2.1),
                                   (-5.2, 0.5, 4.6, 1.6),
                                   (5.6, 0.4, 4.8, 1.7),
                                   (-1.6, -1.5, 4.2, 1.6),
                                   (2.8, -1.3, 3.7, 1.5)):
                p.drawEllipse(QPointF(cx + ox * s, cy + oy * s),
                              rx * s, ry * s)
            shade = QColor(under)
            shade.setAlphaF(al * 0.35)
            p.setBrush(shade)
            p.drawEllipse(QPointF(cx + 0.6 * s, cy + 1.3 * s),
                          6.2 * s, 0.9 * s)
        p.restore()

    def _draw_cloud_shadows(self, p, wd, d, path, pts, overcast):
        """Cloud shadows drifting over the open ground: broad, soft
        darker patches sliding slowly across the hill face, cast by
        the cumulus overhead (same _cloud_xs drift). Moving light over
        a still form — the cheapest proof the hills are rounded ground
        and not cut-outs."""
        al = (0.05 + 0.05 * min(1.0, overcast * 1.6)) * (1.0 - 0.55 * d)
        if al <= 0.015:
            return
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        for cx, k in self._cloud_xs(wd):
            cy = self._ridge_y(pts, wd, cx) + 4.0 + 1.6 * k
            rx = 52.0 + 20.0 * k
            sh = QColor(_INK)
            sh.setAlphaF(al * 0.55)
            p.setBrush(sh)
            p.drawEllipse(QPointF(cx, cy), rx, 5.2 + 1.4 * k)
            sh.setAlphaF(al * 0.45)
            p.setBrush(sh)
            p.drawEllipse(QPointF(cx + rx * 0.15, cy + 0.6),
                          rx * 0.62, 3.4 + 1.0 * k)
        p.restore()

    def _hill_form(self, p, pts, wd, path, d, cover):
        """Lateral form on a hill: with the light in the west, slopes
        falling away east sit in soft shade and west-facing rises catch
        a faint top-light — the same law the mountain facets follow —
        plus a whisper of crest highlight along the ridge."""
        fade = 1.0 - 0.5 * cover
        if fade <= 0.05:
            return
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        xs = pts
        for i in range(len(xs) - 1):
            (x0, y0), (x1, y1) = xs[i], xs[i + 1]
            if x0 > wd:
                break
            east = y1 > y0            # falling away east → shaded flank
            top = min(y0, y1) - 0.5
            g = QLinearGradient(0, top, 0, top + 9.0)
            c = QColor(_INK) if east else QColor(_CHALK)
            a = (0.085 if east else 0.055 * (1.0 - 0.5 * d)) * fade
            c0 = QColor(c)
            c0.setAlphaF(a)
            c1 = QColor(c)
            c1.setAlphaF(0.0)
            g.setColorAt(0.0, c0)
            g.setColorAt(1.0, c1)
            quad = QPainterPath(QPointF(x0, y0 - 1.0))
            quad.lineTo(x1, y1 - 1.0)
            quad.lineTo(x1, y1 + 9.0)
            quad.lineTo(x0, y0 + 9.0)
            quad.closeSubpath()
            p.setBrush(g)
            p.drawPath(quad)
        p.restore()
        # Crest highlight, following the same smoothing as the fill.
        hi = QColor(_CHALK)
        hi.setAlphaF(0.10 * (1.0 - 0.6 * d) * fade)
        p.setPen(QPen(hi, 0.55))
        p.setBrush(Qt.BrushStyle.NoBrush)
        crest = QPainterPath(QPointF(0, xs[0][1]))
        for i in range(1, len(xs)):
            x0, y0 = xs[i - 1]
            x1, y1 = xs[i]
            crest.quadTo(QPointF((x0 + x1) / 2, y0 if i % 2 else y1),
                         QPointF(x1, y1))
        p.drawPath(crest)
        p.setPen(Qt.PenStyle.NoPen)

    def _draw_back_tufts(self, p, wd, d, back_c, cover):
        """Sparse micro-tufts down the back hill's face — the far
        slope's answer to the foreground grass, small and hazed so the
        distance keeps its texture without stealing detail."""
        if cover > 0.75:
            return
        tuft = _lerp(back_c.name(), _INK, 0.30)
        al = 0.5 * (1.0 - cover)
        pen = QPen(tuft, 0.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        tx = 36.0
        while tx < wd + 6:
            n = math.sin(tx * 5.21) * 999.0
            fr = n - math.floor(n)
            in_wood = any(a - 6 < tx < b + 6 for a, b in (_FOREST, _FOREST2))
            if not in_wood:
                ty = self._ridge_y(_BACK_PTS, wd, tx) + 2.2 + fr * 3.6
                c = QColor(tuft)
                c.setAlphaF(al * (0.6 + 0.4 * fr))
                pen.setColor(c)
                p.setPen(pen)
                h = 1.1 + 0.8 * fr
                wv = self._wind_at(tx) * 0.5
                p.drawLine(QPointF(tx, ty), QPointF(tx + 0.4 + wv, ty - h))
                p.drawLine(QPointF(tx + 1.0, ty + 0.3),
                           QPointF(tx + 1.6 + wv, ty - h * 0.7))
            tx += 21.0 + fr * 17.0
        p.setPen(Qt.PenStyle.NoPen)

    def _draw_foreground_flora(self, p, wd, d, bot, par):
        """The nearest layer of all: oversized, softly-blurred plant
        silhouettes leaning in from the bottom corners — cow parsley on
        the left, arcing grass blades and a dock seed-spike on the
        right. Drawn out of focus (layered low-alpha strokes) so they
        read as closer than the scene itself, with the strongest
        parallax and a slow sway of their own."""
        dk = _lerp(_lerp(_TUFT_DAY, _TUFT_NIGHT, d).name(), _INK, 0.45)
        sway = (2.4 * math.sin(self._t * 0.42)
                + 1.1 * math.sin(self._t * 0.13)
                + 3.4 * self._wind_at(wd * 0.5))   # and the gusts hit hard

        def soft_path(path, w):
            for ww, aa in ((w * 2.4, 0.14), (w * 1.5, 0.20), (w, 0.34)):
                c = QColor(dk)
                c.setAlphaF(aa)
                pen = QPen(c, ww)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

        def soft_dot(cx, cy, r):
            c = QColor(dk)
            p.setPen(Qt.PenStyle.NoPen)
            for rr, aa in ((r * 1.8, 0.14), (r * 1.3, 0.20), (r, 0.34)):
                c.setAlphaF(aa)
                p.setBrush(c)
                p.drawEllipse(QPointF(cx, cy), rr, rr)

        # Cow parsley, bottom-left.
        p.save()
        p.translate(-_PAR_FLORA * par, 0)
        p.translate(15.0, bot + 1.5)
        p.rotate(sway)
        stem = QPainterPath(QPointF(0.0, 0.0))
        stem.cubicTo(1.5, -6.0, 4.5, -10.0, 6.5, -14.5)
        soft_path(stem, 0.8)
        for ang, ln in ((-38, 4.2), (-12, 4.8), (14, 4.4), (38, 3.8)):
            a = math.radians(ang - 90)
            ex = 6.5 + math.cos(a) * ln
            ey = -14.5 + math.sin(a) * ln
            ray = QPainterPath(QPointF(6.5, -14.5))
            ray.lineTo(ex, ey)
            soft_path(ray, 0.5)
            for jj in range(3):
                aa2 = a + (jj - 1) * 0.5
                soft_dot(ex + math.cos(aa2) * 1.1,
                         ey + math.sin(aa2) * 1.1, 0.55)
        p.restore()

        # Grass blades and a dock seed-spike, bottom-right.
        if wd > 340:
            p.save()
            p.translate(-_PAR_FLORA * par, 0)
            p.translate(wd - 13.0, bot + 1.5)
            p.rotate(-sway * 0.8)
            for bx, cx1, cy1, ex, ey in ((0.0, -3.0, -7.0, -7.5, -12.5),
                                         (2.0, 0.5, -8.0, -1.5, -15.0)):
                blade = QPainterPath(QPointF(bx, 0.0))
                blade.quadTo(cx1, cy1, ex, ey)
                soft_path(blade, 0.7)
            spike = QPainterPath(QPointF(4.0, 0.0))
            spike.quadTo(4.8, -6.0, 4.2, -11.0)
            soft_path(spike, 0.6)
            for k in range(6):
                tt = k / 5.0
                sy = -3.5 - tt * 7.0
                soft_dot(4.0 + (0.9 if k % 2 else -0.9), sy, 0.5)
            p.restore()

    def _draw_weather(self, p, wd, d, bot=_DESIGN_H):
        ws = self._weather_strength
        if ws <= 0.02 and not self._precip and not self._drops:
            return
        wtype = self._weather

        if wtype == "fog":
            # Layered drifting haze — thicker low down.
            fog = _lerp(_FOG_DAY, _FOG_NIGHT, d)
            for i, (yy, hh, al) in enumerate(((6, 12, 0.14), (18, 14, 0.20),
                                              (30, 16, 0.28))):
                fog.setAlphaF(al * ws)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fog)
                off = 4 * math.sin(self._t * 0.25 + i)
                p.drawRect(QRectF(-10 + off, yy, wd + 20, hh))

        if wtype in ("rain", "storm"):
            rain = _lerp(_RAIN_DAY, _RAIN_NIGHT, d)
            slant = 3.2 if wtype == "rain" else 4.2
            wbase = 0.9 if wtype == "rain" else 1.1
            # Two depth ranks: a dim veil of distant drops behind
            # fewer, bolder streaks close to the glass.
            for lo, hi, wmul, amul in ((0.0, 1.0, 0.72, 0.38),
                                       (1.0, 9.9, 1.30, 0.68)):
                rain.setAlphaF(amul * ws)
                pen = QPen(rain, wbase * wmul)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                for px, py, sp in self._precip:
                    if lo <= sp < hi:
                        p.drawLine(QPointF(px, py),
                                   QPointF(px - slant * sp, py - 5.5 * sp))
        elif wtype == "snow":
            flake = QColor(_SNOW_FLAKE)
            p.setPen(Qt.PenStyle.NoPen)
            for lo, hi, amul in ((0.0, 1.0, 0.55), (1.0, 9.9, 0.95)):
                flake.setAlphaF(amul * ws)
                p.setBrush(flake)
                for px, py, sp in self._precip:
                    if lo <= sp < hi:
                        p.drawEllipse(QPointF(px, py), 0.8 * sp, 0.8 * sp)

        # Drops on the glass, in front of everything: a wobbling trail
        # of wet behind a bright bead that catches the light.
        if self._drops:
            base = _lerp(_RAIN_DAY, _RAIN_NIGHT, d)
            for dr in self._drops:
                x, y, sz = dr["x"], dr["y"], dr["size"]
                born = min(1.0, dr["age"] / 0.8)   # ease in on arrival
                trail_top = max(dr["y0"], y - 14.0)
                if y - trail_top > 1.5:
                    # The wet track wanders a touch, the way a run of
                    # water follows the grain of the glass.
                    wet = QColor(base)
                    wet.setAlphaF(0.17 * born)
                    pen = QPen(wet, 0.7 * sz)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    span_y = y - 0.8 - trail_top
                    steps = 4
                    pts = []
                    for k in range(steps + 1):
                        tt = k / float(steps)
                        wob = 0.55 * math.sin(dr["ph"] * 1.7
                                              + tt * 4.2) * (1.0 - tt)
                        pts.append(QPointF(x + wob,
                                           trail_top + span_y * tt))
                    for k in range(steps):
                        p.drawLine(pts[k], pts[k + 1])
                # The bead is a little lens: a fine dark edge defines it
                # against pale hill and dark field alike.
                edge = QColor(base.darker(175))
                edge.setAlphaF(0.50 * born)
                p.setPen(QPen(edge, 0.26))
                bead = QColor(base.lighter(126))
                bead.setAlphaF(0.55 * born)
                p.setBrush(bead)
                p.drawEllipse(QPointF(x, y), 1.0 * sz, 1.25 * sz)
                p.setPen(Qt.PenStyle.NoPen)
                rim = QColor(base.darker(160))
                rim.setAlphaF(0.50 * born)
                p.setBrush(rim)
                p.drawEllipse(QPointF(x, y + 0.65 * sz), 0.62 * sz,
                              0.38 * sz)
                glint = QColor(_CHALK)
                glint.setAlphaF(0.85 * born)
                p.setBrush(glint)
                p.drawEllipse(QPointF(x - 0.35 * sz, y - 0.4 * sz),
                              0.26 * sz, 0.34 * sz)

        if self._flash > 0.02:
            fl = QColor("#FFFFFF")
            fl.setAlphaF(0.45 * self._flash)
            p.fillRect(QRectF(0, 0, wd, bot), fl)

    def _apply_facing(self, p, wk, sc=1.0, ground=None):
        """Face the sprite along wk['_face'] with a horizontal squash,
        sold as a wheel-around rather than a card flip: the width never
        drops below a third (the paper-thin sliver frame is the single
        biggest 2-D tell), the body bunches up TALLER on planted feet
        mid-turn (volume conserved, like an animal gathering itself),
        and the nose dips a touch as it comes round. The dip is applied
        in local space, so it stays nose-down through the mirror."""
        face = max(-1.0, min(1.0, wk.get("_face", float(wk["dir"]))))
        p.translate(wk["w"] * sc / 2.0, 0)
        p.scale((1.0 if face >= 0 else -1.0) * max(0.35, abs(face)) * sc,
                sc)
        p.translate(-wk["w"] / 2.0, 0)
        turn = 1.0 - abs(face)
        if turn > 0.02:
            gy = wk["h"] if ground is None else ground
            p.translate(wk["w"] / 2.0, gy)
            p.scale(1.0, 1.0 + 0.13 * turn)
            p.rotate(4.0 * turn)
            p.translate(-wk["w"] / 2.0, -gy)

    def _render_walker(self, p, wk, wd):
        p.save()
        # The ring-o is painted with the (unshifted) wordmark, but the
        # animals denning in it live on parallax-shifted planes. Blend
        # the plane's slide away as a walker closes on the ring, so a
        # curled fox or perching owl stays glued to the logo at any
        # pointer position — and the blend is spatial, so there's
        # nothing to snap when a pose changes.
        den_cx = getattr(self, "_den_cx", None)
        if den_cx is not None and self._par:
            near = max(0.0, 1.0 - abs(wk["x"] + wk["w"] * wk["scale"] / 2
                                      - den_cx) / 60.0)
            if near > 0.0:
                amp = (_PAR_FRONT if wk["flying"] else
                       {"back": _PAR_BACK, "mid": _PAR_FRONT,
                        "fore": _PAR_NEAR}[wk["layer"]])
                p.translate(amp * self._par * near, 0)
        # Animals spawned in place (den dwellers, the boat) ease into
        # view rather than materialising.
        fade = wk.get("_fade", 1.0)
        if fade < 1.0:
            p.setOpacity(p.opacity() * (0.1 + 0.9 * fade))
        g = wk["gait"]
        sc = wk["scale"]
        if wk["kind"] == "squirrel":
            self._render_squirrel(p, wk, wd)
            p.restore()
            return
        if wk["kind"] == "ecologist" and wk.get("boat"):
            self._render_boat(p, wk)
            p.restore()
            return
        if wk["flying"]:
            if wk["kind"] == "owl":
                y = 8 + 2.5 * math.sin(g * 0.35)
                perch_y = wk.get("_perch_y", 12.0)
                if wk["pose"] == "perch" and wk["pause_x"] is not None:
                    # Glide down onto the ring-o rim, or an oak branch.
                    if wk["pause_t"] > 0:
                        y = perch_y
                        wk["_perch_ref"] = wk["x"]   # for the climb-out
                    elif (not wk["pause_done"]
                          and wk.get("_perch_ref") is None):
                        dist = abs(wk["x"] - wk["pause_x"])
                        if dist < 20:
                            y += (perch_y - y) * (1 - dist / 20.0)
                if wk.get("_perch_ref") is not None and wk["pause_t"] <= 0:
                    # Leaving mirrors the glide in: climb back to the
                    # flight line over the same distance — keyed on
                    # where it perched, so a retargeted pause can't
                    # break the blend.
                    dist = abs(wk["x"] - wk["_perch_ref"])
                    if dist < 20:
                        y += (perch_y - y) * (1 - dist / 20.0)
                    else:
                        wk["_perch_ref"] = None
            elif wk["kind"] == "buzzard":
                y = 7 + 3.5 * math.sin(g * 0.5)
            elif wk["kind"] == "kite":
                y = 6.5 + 2.5 * math.sin(g * 0.33)   # high, lazy circles
            elif wk["kind"] == "barn_owl":
                y = 26.5 + 1.8 * math.sin(g * 0.5)   # low over rough grass
            elif wk["kind"] == "dragonfly":
                y = (29.5 + 2.2 * math.sin(g * 0.45)
                     + 1.1 * math.sin(g * 1.31))
                if wk["pose"] == "hover" and wk["pause_t"] > 0:
                    y = 31.5 + 0.5 * math.sin(g * 2)  # hanging in the air
            elif wk["kind"] == "bee":
                # Low over the flowers, wandering as it goes; eases down
                # onto a flower head to sip, then lifts away.
                y = (30.5 + 2.6 * math.sin(g * 0.10)
                     + 1.0 * math.sin(g * 0.23))
                if wk["sip_y"] is not None and wk["pause_x"] is not None:
                    if wk["pause_t"] > 0:
                        y = wk["sip_y"] + 0.35 * math.sin(g * 0.5)
                        wk["_sip_ref"] = wk["x"]     # for the lift-away
                    elif (not wk["pause_done"]
                          and wk.get("_sip_ref") is None):
                        dxs = abs(wk["x"] + wk["w"] / 2 - wk["pause_x"])
                        if dxs < 14:
                            y += (wk["sip_y"] - y) * (1 - dxs / 14)
                if (wk.get("_sip_ref") is not None and wk["pause_t"] <= 0
                        and wk["sip_y"] is not None):
                    # Lifting away mirrors the landing: ease back up to
                    # the flight path over the same span, keyed on the
                    # flower it sat on rather than the next target.
                    dxs = abs(wk["x"] - wk["_sip_ref"])
                    if dxs < 14:
                        y += (wk["sip_y"] - y) * (1 - dxs / 14)
                    else:
                        wk["_sip_ref"] = None
            elif wk["kind"] == "kingfisher":
                # Low and arrow-straight: barely any drift. (The old
                # sin(g*0.8) bob ran at ~4 Hz off the 5 Hz gait — a
                # visible vertical judder, nothing like the real bird.)
                y = 25.2 + 0.6 * math.sin(g * 0.13)
                if wk["dive"]:
                    # Swoop down to the lake surface and out the other side.
                    dx = (wk["x"] + wk["w"] / 2) - wk["dive"][0]
                    if abs(dx) < 20:
                        u = 1.0 - (dx / 20.0) ** 2
                        y += (wk["dive"][1] - y) * (u ** 1.6)
            else:
                y = (9 + 2.6 * math.sin(g * 0.45)
                     + 1.2 * math.sin(g * 1.13) - 0.9 * math.sin(g))
                if wk["kind"] == "bat" and wk.get("_prf"):
                    # Emerging from the oak roost: start at the cavity and
                    # climb away into normal jinking flight.
                    prog = min(1.0, abs(wk["x"] + wk["w"] / 2
                                        - wk["_prf"][0]) / 24.0)
                    y = wk["_prf"][1] + (y - wk["_prf"][1]) * prog
                if wk.get("forage_t", 0) > 0:      # deeper swoops hawking
                    y += 3.0 * math.sin(g * 0.27)
            p.translate(wk["x"], y)
            # Turn through the shared wheel-around squash, banking
            # about mid-body rather than crouching on absent feet.
            self._apply_facing(p, wk, 1.0, ground=wk["h"] * 0.55)
            if wk["kind"] in ("buzzard", "kite"):
                # High soarers sit a sky-layer deeper than the low
                # fliers: a touch smaller, scaled about their centre.
                p.translate(wk["w"] * 0.09, wk["h"] * 0.09)
                p.scale(0.82, 0.82)
            if wk["kind"] == "kingfisher" and wk["dive"]:
                dx = (wk["x"] + wk["w"] / 2) - wk["dive"][0]
                if abs(dx) < 20 and 1.0 - (dx / 20.0) ** 2 > 0.9:
                    spl = QColor(_CHALK)          # feet clip the surface
                    spl.setAlphaF(0.75)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(spl)
                    for sx, sy in ((9.5, 6.2), (12.5, 6.6), (15.0, 5.9)):
                        p.drawEllipse(QPointF(sx, sy), 0.5, 0.5)
            getattr(self, "_draw_" + wk["kind"])(p, wk)
            p.restore()
            return
        pts = _BACK_PTS if wk["layer"] == "back" else _FRONT_PTS
        span = wk["w"] * sc
        cx = wk["x"] + span / 2
        swimmer = wk["kind"] in _SWIMMERS
        frozen = getattr(self, "_season_cache", "") == "winter"
        lake = (self._lake_at(cx, wd)
                if (wk["kind"] in _LAKEFOLK and wk["layer"] == "mid"
                    and not frozen)
                else None)
        hold = wk["pause_t"] > 0
        clip = False
        # This approach's water manner: a rare swimmer that chose to
        # divert takes a bank lane instead of the float line.
        lane = (wk.get("_lane") if wk.get("_lane_lake") is not None
                else None)
        if lake is not None and lane in ("above", "below"):
            lake = None
        if lake is not None and swimmer:
            dip = {"gcn": 5.5, "otter": 4.0, "beaver": 4.4,
                   "water_vole": 4.8, "grass_snake": 4.3,
                   "adder": 4.3}[wk["kind"]]
            top = lake[1] - dip * sc
            # Ease between the walk line and the float line across the
            # shore zone — the animal slips into the water and hauls
            # out of it instead of snapping between levels. While only
            # part-way in, whatever dips below the waterline is hidden,
            # the same trick the waders use.
            lxq, lyq, lrxq, _lryq = lake
            u = (lrxq * 0.8 - abs(cx - lxq)) / max(3.0, lrxq * 0.26)
            u = max(0.0, min(1.0, u))
            u = u * u * (3.0 - 2.0 * u)
            gy = self._ridge_y(_FRONT_PTS, wd, cx) - wk["h"] * sc + 2
            if u < 1.0:
                p.setClipRect(QRectF(0, 0, wd, lyq + 0.6))
                clip = True
            p.translate(wk["x"], gy + (top - gy) * u)
            swim = u > 0.45
        else:
            if wk.get("depth_lock"):
                crest = (self._front_limit() if wk["layer"] == "fore"
                         else self._ridge_y(pts, wd, cx))
            else:
                crest = self._depth_crest(wk["depth"], cx, wd)
            if wk["layer"] == "mid" and not wk["flying"]:
                # Passing a hedgerow, everyone walks the hedge LANE:
                # the walk line eases up to the ridge across the span,
                # so bodies hide behind the hedge without feet poking
                # out beneath its skirt at deeper wander depths.
                for hx0, hx1, _hg in _HEDGES:
                    if hx0 - 14 <= cx <= hx1 + 14 and hx1 + 10 < wd:
                        t3 = min(1.0, min(cx - (hx0 - 14),
                                          (hx1 + 14) - cx) / 14.0)
                        fc2 = self._ridge_y(_FRONT_PTS, wd, cx)
                        crest += (fc2 - crest) * max(0.0, t3)
                        break
            eco_lane = ("above" if wk["kind"] == "ecologist" else lane)
            if eco_lane in ("above", "below") and wk["layer"] == "mid":
                # Diverting around water: the walk line eases onto the
                # far bank (above the rim) or the near shore (below the
                # water) across the lake's span, and back off beyond
                # it. The ecologist always takes the far bank.
                for lx2, lrx2, lry2, _m2 in _LAKES:
                    if lx2 + lrx2 >= wd - 4:
                        continue
                    t2 = 1.0 - abs(cx - lx2) / (lrx2 + 8.0)
                    if t2 <= 0.0:
                        continue
                    ly2, ry2 = self._lake_geom(lx2, lry2, wd)
                    if eco_lane == "below":
                        tgt = ly2 + ry2 + 1.4
                        if tgt > self._front_limit():
                            tgt = ly2 - ry2 - 2.2   # no room: far bank
                    else:
                        tgt = ly2 - ry2 - 2.2
                    crest += (tgt - crest) * min(1.0, t2 * 1.6)
                    break
            bob = 0.0 if hold else 0.5 * math.sin(2 * g)
            base_y = crest - wk["h"] * sc + 2 + bob
            swim = False
            # A land animal that walks into a lake wades in — deeper toward
            # the middle — and everything below the waterline is hidden.
            # An animal that chose a bank lane never wades.
            wade_lake = (self._lake_at(cx, wd)
                         if (not swimmer and wk["kind"] not in ("heron",
                                                                "ecologist")
                             and wk["kind"] not in _NO_SWIM
                             and lane not in ("above", "below")
                             and not frozen
                             and wk["layer"] == "mid") else None)
            if wade_lake is not None and wk["kind"] in ("hare", "pheasant"):
                # These two clear the water instead of swimming — the hare
                # in one long bound, the pheasant in a whirring burst.
                # The arc zeroes exactly at the lake gate (0.8·rx) so the
                # lift builds from nothing rather than starting mid-step.
                lx, ly, lrx, lry = wade_lake
                t = max(0.0, 1.0 - abs(cx - lx) / (lrx * 0.8))
                arc = math.sin(min(1.0, t) * math.pi / 2)
                lift = arc * (lry + wk["h"] * sc * 0.35 + 3.5)
                wk["_leap"] = t
                p.translate(wk["x"], base_y - lift)
                if t > 0.05:
                    sh = QColor(_INK)         # shadow cast on the water
                    sh.setAlphaF(0.10 * min(1.0, (t - 0.05) * 2.5))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(sh)
                    p.drawEllipse(QPointF(span / 2,
                                          wk["h"] * sc + lift * 0.85),
                                  span * 0.3, 0.9)
            elif wade_lake is not None:
                lx, ly, lrx, lry = wade_lake
                t = max(0.0, 1.0 - abs(cx - lx) / (lrx * 0.8))
                deep = max(0.0, (t - 0.45) / 0.55)   # 0 wading → 1 swimming
                # Chest-deep at most: head, neck and back stay dry.
                sink = t * wk["h"] * sc * (0.50 + 0.22 * deep)
                waterline = ly + 0.5
                p.setClipRect(QRectF(0, 0, wd, waterline))
                clip = True
                bob2 = 0.45 * deep * math.sin(self._t * 3.1
                                              + wk["gait"] * 0.5)
                p.translate(wk["x"], base_y + sink + bob2)
                yy = waterline - (base_y + sink + bob2)
                # Ripple ring at the waterline where the body enters —
                # fading up from nothing at the shore, like the wake.
                wet = min(1.0, t * 3.5)
                rip = QColor(_lerp(_WATER_DAY, _WATER_NIGHT, self._dark))
                rip.setAlphaF(wet)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(rip)
                p.drawEllipse(QPointF(span / 2, yy), span * 0.42, 1.4)
                wk_wake = QColor(_CHALK)
                wk_wake.setAlphaF(0.45 * wet)
                pen = QPen(wk_wake, 0.7)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(span * 0.1, yy), QPointF(span * -0.3, yy + 0.4))
                if deep > 0.15:
                    # Properly swimming: bow wave off the chest, a longer
                    # stern wake, and a chin-up trim keeping the nose dry.
                    p.drawLine(QPointF(span * 0.92, yy),
                               QPointF(span * 1.30, yy + 0.4))
                    p.drawLine(QPointF(span * 0.05, yy + 0.8),
                               QPointF(span * -0.42, yy + 1.1))
                    p.translate(span / 2, wk["h"] * sc * 0.55)
                    p.rotate(-8 * deep * wk["dir"])
                    p.translate(-span / 2, -wk["h"] * sc * 0.55)
            else:
                wk["_leap"] = 0.0
                alt = 0.0
                if (wk["kind"] == "stag_beetle"
                        and wk.get("fly_t") is not None):
                    alt = min(14.0, wk["fly_t"] * 8.0)
                p.translate(wk["x"], base_y - alt)
                # Contact shadow, graded by depth: the nearer the
                # animal, the darker and rounder the pool at its feet.
                dep = wk.get("depth", 0.5)
                sh = QColor(_INK)
                sh.setAlphaF((0.09 + 0.10 * dep)
                             * max(0.25, 1.0 - alt / 20.0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(sh)
                p.drawEllipse(QPointF(span / 2, wk["h"] * sc - 0.2 + alt),
                              span * (0.30 + 0.12 * dep), 0.7 + 0.75 * dep)
                if hold and wk["pose"] == "drink":
                    # Tip the whole animal toward the water, lapping.
                    lap = 0.75 + 0.25 * math.sin(g * 1.3)
                    p.translate(span * 0.68, wk["h"] * sc)
                    p.rotate(7 * wk["dir"] * lap)
                    p.translate(-span * 0.68, -wk["h"] * sc)
                elif ((hold and wk["pose"] == "den")
                      or wk.get("_curl", 0.0) > 0.0):
                    # Curl point: inside the ring-o, or under the oak's
                    # roots when this is the oak earth. Also kept fresh
                    # through the uncurl blend after waking, so the
                    # fading ball stays glued to the den.
                    if wk.get("_den_at") == "oak" and self._oak is not None:
                        den_cx, den_cy = (self._oak["base"][0],
                                          self._oak["base_y"] - 1.4)
                    elif getattr(self, "_den_cx", None) is not None:
                        den_cx, den_cy = self._den_cx, 27.0
                    else:
                        den_cx, den_cy = None, None
                    if den_cx is not None:
                        if wk["dir"] > 0:
                            lxd = (den_cx - wk["x"]) / sc
                        else:
                            lxd = (wk["x"] + span - den_cx) / sc
                        wk["_den"] = (lxd, (den_cy - base_y) / sc)
        # Turn through the shared wheel-around squash.
        self._apply_facing(p, wk, sc)
        # A fresh pose gets a beat to settle: a quick rise from a
        # slight crouch masks the hard cut between sprite branches.
        pu = wk.get("_pose_u", 1.0)
        if pu < 1.0:
            settle = 0.93 + 0.07 * (pu * pu * (3.0 - 2.0 * pu))
            p.translate(0, wk["h"] * (1.0 - settle))
            p.scale(1.0, settle)
        if swimmer:
            getattr(self, "_draw_" + wk["kind"])(p, wk, swim=swim)
        else:
            getattr(self, "_draw_" + wk["kind"])(p, wk)
        # Winter air: warm-blooded animals puff visible breath — little
        # clouds that drift back off the muzzle and thin away. The
        # ectotherms (newt, snakes, beetle) rightly never do.
        if (getattr(self, "_season_cache", "") == "winter"
                and not swim and not clip
                and wk["kind"] not in _NO_BREATH
                and wk["pose"] != "den"
                and wk.get("_curl", 0.0) <= 0.05
                and wk.get("_leap", 0.0) <= 0.05):
            bph = wk.setdefault("_breath_ph", random.uniform(0.0, 3.4))
            age = (self._t + bph) % 3.4
            if age < 1.2:
                mxr, myr = _BREATH_AT.get(wk["kind"], (0.93, 0.26))
                bx = wk["w"] * mxr + 0.9 - age * 2.6
                by = wk["h"] * myr - age * 1.5
                fade = 1.0 - age / 1.2
                rr = (1.20 + 3.20 * age) * max(0.55, wk["h"] / 18.0)
                # An ink-shade ring under a cool vapour fill keeps the
                # cloud legible against pale hill and green field alike.
                edge = QColor(_INK)
                edge.setAlphaF(0.20 * fade)
                p.setPen(QPen(edge, 0.45))
                puff = QColor("#DCE9ED")
                puff.setAlphaF(0.60 * fade)
                p.setBrush(puff)
                p.drawEllipse(QPointF(bx, by), rr, rr * 0.8)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(bx + rr * 0.5, by + rr * 0.3),
                              rr * 0.55, rr * 0.5)
        if clip:
            p.setClipping(False)
        p.restore()

    def _render_squirrel(self, p, wk, wd):
        """Place the squirrel: bounding on the ground, or clinging to the
        oak trunk / sitting in its crown when it is climbing or foraging.
        Assumes the caller has already p.save()'d."""
        sc = wk["scale"]
        oak = self._oak
        st = wk.get("sq", "away")

        def _blend(px, py):
            # Ease between anchor frames (ground↔trunk↔crown) so the
            # squirrel flows through its climb instead of teleporting.
            u = wk.get("_anchor_u", 1.0)
            if u < 1.0 and wk.get("_anchor_from") is not None:
                fx, fy = wk["_anchor_from"]
                uu = u * u * (3.0 - 2.0 * u)
                return fx + (px - fx) * uu, fy + (py - fy) * uu
            return px, py

        if oak is not None and st in ("up", "down", "forage"):
            if st == "forage":
                px, py = oak["crown"][0] - 1.4, oak["crown"][1] - 4.0
            else:
                px, py = oak["x"] - 2.2, oak["base_y"] - wk["climb"] - 10.0
            px, py = _blend(px, py)
            p.translate(px, py)
            p.scale(sc, sc)
            if st == "down":
                # Head-first down the trunk, the way a real squirrel
                # descends: the climbing sprite flipped nose-down, tail
                # streaming up the trunk behind it.
                p.translate(0.0, 13.0)
                p.scale(1.0, -1.0)
            self._draw_squirrel(p, wk)
            return
        span = wk["w"] * sc
        cx = wk["x"] + span / 2
        crest = self._ridge_y(_FRONT_PTS, wd, cx)
        # The squirrel skirts the pond on its dash to the oak — around
        # the near shore, or the far bank if the band leaves no room.
        for lx2, lrx2, lry2, _m2 in _LAKES:
            if lx2 + lrx2 >= wd - 4:
                continue
            t2 = 1.0 - abs(cx - lx2) / (lrx2 + 8.0)
            if t2 <= 0.0:
                continue
            ly2, ry2 = self._lake_geom(lx2, lry2, wd)
            tgt = ly2 + ry2 + 1.0
            if tgt > self._front_limit():
                tgt = ly2 - ry2 - 2.2
            crest += (tgt - crest) * min(1.0, t2 * 1.6)
            break
        base_y = crest - wk["h"] * sc + 2
        tx0, ty0 = _blend(wk["x"], base_y)
        p.translate(tx0, ty0)
        sh = QColor(_INK)
        sh.setAlphaF(0.13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sh)
        p.drawEllipse(QPointF(span / 2, wk["h"] * sc - 0.2),
                      span * 0.34, 1.0)
        self._apply_facing(p, wk, sc)
        self._draw_squirrel(p, wk)

    def _render_boat(self, p, wk):
        """Place the rowing boat on the lake surface with a gentle bob,
        at full survey-boat scale, fading out through the shore reeds
        when it lands. The turn for home eases through the same facing
        squash the animals use."""
        edge = wk.get("boat_edge")
        if edge is not None:
            over = abs(wk["x"] + 8.0 * _BOAT_S - wk["boat_lx"]) - edge
            if over > 0.0:
                p.setOpacity(p.opacity()
                             * max(0.0, 1.0 - over / 9.0))
        bob = 0.35 * math.sin(self._t * 1.6 + wk["x"] * 0.2)
        p.translate(wk["x"], wk["boat_y"] + bob)
        p.scale(_BOAT_S, _BOAT_S)
        p.translate(0, -0.55)          # keep the hull's draft sensible
        face = max(-1.0, min(1.0, wk.get("_face", float(wk["dir"]))))
        p.translate(8.0, 0)
        p.scale((1.0 if face >= 0 else -1.0) * max(0.35, abs(face)), 1.0)
        p.translate(-8.0, 0)
        self._draw_boat(p, wk)

    def _draw_boat(self, p, wk):
        """A small rowing boat, the ecologist working the oars — a lake
        survey (eDNA, water-vole, otter). Drawn at the waterline."""
        g = wk["gait"]
        d = self._dark
        hull = _lerp("#6E4A2A", _INK, d * 0.7)
        coat = _lerp("#3C6B4E", _INK, d * 0.7)
        vest = _lerp("#E39B2D", "#7A5218", d * 0.55)
        skin = _lerp("#D8B295", "#6E5A4A", d * 0.6)
        hat = _lerp(_BELLY, "#7A3A22", d * 0.5)
        working = wk.get("boat_st") == "work"
        oar_c = QPen(_lerp("#8A6E4A", _INK, d * 0.5), 0.7)
        oar_c.setCapStyle(Qt.PenCapStyle.RoundCap)
        hand = QPointF(8.6, -2.4)
        if not working:
            # Under way: a wake trailing astern, the near oar sweeping.
            wake = QColor(_CHALK)
            wake.setAlphaF(0.4)
            pen = QPen(wake, 0.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for wx in (-1.2, -3.8):
                arc = QPainterPath(QPointF(wx, 1.4))
                arc.quadTo(wx - 2.0, 2.2, wx - 4.0, 1.8)
                p.drawPath(arc)
            stroke = math.sin(g)
            p.setPen(oar_c)
            p.drawLine(hand, QPointF(11.8 + 2.6 * stroke, 1.7))
            p.setPen(Qt.PenStyle.NoPen)
        else:
            # On station: oar shipped along the gunwale, a sample line
            # over the side with its slow ring spreading on the water.
            p.setPen(oar_c)
            p.drawLine(QPointF(4.2, -1.3), QPointF(12.4, -1.9))
            line = QColor(_CHALK)
            line.setAlphaF(0.6)
            p.setPen(QPen(line, 0.35))
            p.drawLine(QPointF(10.6, -2.6), QPointF(11.3, 1.6))
            rr = 0.8 + 0.7 * ((self._t * 0.5) % 1.0)
            ring = QColor(_CHALK)
            ring.setAlphaF(0.4 * (1.0 - (rr - 0.8) / 0.7))
            p.setPen(QPen(ring, 0.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(11.3, 1.7), rr, rr * 0.3)
            p.setPen(Qt.PenStyle.NoPen)
        # Hull.
        p.setBrush(hull)
        boat = QPainterPath(QPointF(1.6, -0.7))
        boat.quadTo(7.8, -2.1, 14.6, -0.7)
        boat.quadTo(13.4, 2.3, 7.6, 2.7)
        boat.quadTo(3.0, 2.5, 1.6, -0.7)
        boat.closeSubpath()
        p.drawPath(boat)
        p.setBrush(_lerp(hull.name(), _INK, 0.3))
        p.drawRect(QRectF(4.0, -1.0, 7.6, 0.5))        # inner gunwale line
        # The rower.
        p.setBrush(coat)
        p.drawRoundedRect(QRectF(6.4, -4.6, 3.0, 3.4), 0.8, 0.8)
        p.setBrush(vest)
        p.drawRoundedRect(QRectF(6.7, -4.4, 2.4, 2.6), 0.6, 0.6)
        p.setBrush(skin)
        p.drawEllipse(QPointF(7.9, -5.4), 1.0, 1.0)
        p.setBrush(hat)
        beanie = QPainterPath(QPointF(6.9, -5.5))
        beanie.quadTo(7.9, -7.1, 8.9, -5.5)
        beanie.closeSubpath()
        p.drawPath(beanie)
        ap = QPen(coat, 1.0)
        ap.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(ap)
        p.drawLine(QPointF(7.9, -3.6), hand)
        p.setPen(Qt.PenStyle.NoPen)

    def _draw_still_contours(self, p, wd, thm):
        """Scene-off dressing: tone-on-tone survey contours of an unseen
        summit in plan view, index contours a whisper stronger, with a
        lichen trig point on top. Anchored off the right edge; the loops'
        lower skirts are meant to run under the ground band."""
        rng = random.Random(7)
        cx = max(210.0, wd - 100.0)
        cy = 30.0
        p.setBrush(Qt.BrushStyle.NoBrush)
        n = 9
        jit = [[rng.uniform(-0.16, 0.16) for _ in range(n)] for _ in range(6)]
        chalk = QColor(_CHALK)
        for k in range(6):
            r0 = 14.0 + 11.5 * k
            pts = []
            for i in range(n):
                a = 2 * math.pi * i / n
                # Cumulative jitter keeps successive rings roughly parallel,
                # the way real contours nest.
                j = 1.0 + sum(jit[m][i] for m in range(k + 1)) * 0.55
                pts.append(QPointF(cx + math.cos(a) * r0 * j * 1.55,
                                   cy + math.sin(a) * r0 * j * 0.62))
            path = QPainterPath(pts[0])
            for i in range(n):
                a0, a1 = pts[i], pts[(i + 1) % n]
                path.quadTo(a0, QPointF((a0.x() + a1.x()) / 2,
                                        (a0.y() + a1.y()) / 2))
            path.closeSubpath()
            heavy = (k % 3 == 0)
            chalk.setAlphaF(0.13 if heavy else 0.07)
            p.setPen(QPen(chalk, 0.8 if heavy else 0.6))
            p.drawPath(path)
        lichen = QColor(thm.LICHEN)
        lichen.setAlphaF(0.75)
        tpen = QPen(lichen, 0.9)
        tpen.setCapStyle(Qt.PenCapStyle.RoundCap)
        tpen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(tpen)
        tri = QPainterPath(QPointF(cx, cy - 3.4))
        tri.lineTo(QPointF(cx - 2.9, cy + 1.6))
        tri.lineTo(QPointF(cx + 2.9, cy + 1.6))
        tri.closeSubpath()
        p.drawPath(tri)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(lichen)
        p.drawEllipse(QPointF(cx, cy - 0.2), 0.7, 0.7)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_wordmark(self, p, d):
        word = _lerp(_WORD_DAY, _WORD_NIGHT, d)
        leaf = _lerp(_LEAF_DAY, _LEAF_NIGHT, d)
        fm = QFontMetricsF(self._word_font)
        x0, base = 16.0, 34.0
        p.setFont(self._word_font)
        p.setPen(word)
        p.drawText(QPointF(x0, base), "bı")
        w_b = fm.horizontalAdvance("b")
        w_bi = fm.horizontalAdvance("bı")
        self._leaf_x = x0 + w_b + fm.horizontalAdvance("ı") / 2
        p.save()
        p.translate(self._leaf_x, 11.5)
        p.rotate(-38)
        path = QPainterPath(QPointF(0, 7.6))
        path.cubicTo(-6, 3, -6, -6, 0, -7.6)
        path.cubicTo(6, -6, 6, 3, 0, 7.6)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(leaf)
        p.drawPath(path)
        # Veins: a midrib with three pairs of laterals.
        vein = QColor(leaf).darker(135)
        vpen = QPen(vein, 0.55)
        vpen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(vpen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(0, 6.6), QPointF(0, -6.6))
        for vy in (3.4, 0.6, -2.2):
            p.drawLine(QPointF(0, vy), QPointF(2.6, vy - 2.4))
            p.drawLine(QPointF(0, vy), QPointF(-2.6, vy - 2.4))
        p.restore()
        r = 7.2
        cx = x0 + w_bi + 4 + r
        self._den_cx = cx           # the ring-o doubles as a den site
        p.setPen(QPen(leaf, 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, 27), r, r)
        p.setPen(word)
        p.drawText(QPointF(cx + r + 4.5, base), "me")

    # ── scenery painters ──────────────────────────────────────────────
    @staticmethod
    def _draw_broadleaf(p, x, y, s, color, bare=False, blossom=False,
                        trunk=None):
        # The trunk and branches are BARK, not foliage — otherwise an
        # autumn tree turns orange right down to the ground.
        pen = QPen(color if trunk is None else trunk, 1.0 * s)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x, y), QPointF(x, y - 4.2 * s))
        pen.setWidthF(0.6 * s)
        p.setPen(pen)
        p.drawLine(QPointF(x, y - 2.9 * s), QPointF(x - 1.7 * s, y - 4.6 * s))
        p.drawLine(QPointF(x, y - 3.4 * s), QPointF(x + 1.5 * s, y - 4.9 * s))
        if bare:
            # Winter: no canopy, just a spread of fine twigs.
            pen.setWidthF(0.4 * s)
            p.setPen(pen)
            for dx0, dy0, dx1, dy1 in (
                    (-1.7, -4.6, -2.8, -6.4), (-1.7, -4.6, -0.9, -6.8),
                    (1.5, -4.9, 2.6, -6.9), (1.5, -4.9, 0.7, -7.1),
                    (0.0, -4.2, -0.6, -7.6), (0.0, -4.2, 0.8, -8.0)):
                p.drawLine(QPointF(x + dx0 * s, y + dy0 * s),
                           QPointF(x + dx1 * s, y + dy1 * s))
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for cx, cy, rx, ry in ((0.0, -6.6, 3.6, 2.6), (-2.4, -5.6, 2.2, 1.8),
                               (2.4, -5.8, 2.3, 1.8), (0.6, -8.4, 2.3, 1.7),
                               (-1.3, -8.0, 1.9, 1.5)):
            p.drawEllipse(QPointF(x + cx * s, y + cy * s), rx * s, ry * s)
        if blossom:
            # Spring: a scatter of blossom at the canopy edges.
            bl = QColor("#F2D7E2")
            bl.setAlphaF(0.9)
            p.setBrush(bl)
            for bx, by in ((-2.8, -6.9), (-0.6, -9.2), (1.8, -8.2),
                           (3.2, -6.2), (0.4, -6.0)):
                p.drawEllipse(QPointF(x + bx * s, y + by * s),
                              0.55 * s, 0.55 * s)

    @staticmethod
    def _draw_conifer(p, x, y, s, color):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRect(QRectF(x - 0.35 * s, y - 1.8 * s, 0.7 * s, 1.9 * s))
        tree = QPainterPath(QPointF(x - 2.7 * s, y - 1.5 * s))
        for dx, dy in ((-1.25, -3.5), (-2.0, -3.5), (-0.95, -5.8),
                       (-1.55, -5.8), (0.0, -9.2), (1.55, -5.8),
                       (0.95, -5.8), (2.0, -3.5), (1.25, -3.5),
                       (2.7, -1.5)):
            tree.lineTo(QPointF(x + dx * s, y + dy * s))
        tree.closeSubpath()
        p.drawPath(tree)

    def _draw_oak(self, p, wd, d, leaf_c, season):
        """The veteran oak: a spreading crown on forking limbs, a fox
        earth arched into the roots, a dark bat-roost cavity (PRF) up the
        trunk, and a stout perch branch. Stores its interaction anchors
        in self._oak. Bare in winter, acorned in autumn, blossomed in
        spring."""
        x = _OAK_X
        if x + 15 > wd - 4 or x < 20:
            self._oak = None
            return
        base_y = self._ridge_y(_FRONT_PTS, wd, x) + 1.0
        # With the banner extended behind the tabs, the oak steps down
        # into the near band and grows — nearer, grander, its residents
        # (den fox, roosting bats, the squirrel) easier to watch.
        extra = min(self._design_bot() - _DESIGN_H, 10.0)
        base_y += extra * 0.45
        top_y = base_y - 12.0
        bark = _lerp("#6B5236", _INK, d * 0.7)
        bark_lo = _lerp(bark.name(), _INK, 0.35)
        # A veteran oak stands well above the wildlife — draw the whole
        # tree scaled up around its rooted base. The scale is clamped by
        # the headroom above the (lowered) base so the crown never crops
        # more than a whisker off the top of the scene; 24.9 is the
        # tree's full local height, rooted base to the crown's top.
        S = min(1.35 + 0.045 * extra, (base_y + 2.0) / 24.9)
        p.save()
        p.translate(x, base_y)
        p.scale(S, S)
        p.translate(-x, -base_y)
        p.setPen(Qt.PenStyle.NoPen)
        # The great tree grounds itself in a pool of root-shadow.
        osh = QColor(_INK)
        osh.setAlphaF(0.12)
        p.setBrush(osh)
        p.drawEllipse(QPointF(x + 0.6, base_y + 0.7), 9.5, 1.1)
        # Limbs first (behind the crown), thick tapering strokes.
        limbs = ((-9.0, -4.5, 1.4), (-6.5, -1.0, 1.6), (9.5, -2.0, 1.5),
                 (7.0, -5.5, 1.4), (0.6, -8.0, 1.7), (3.5, -3.0, 1.3))
        for ex, ey, w0 in limbs:
            lp = QPen(bark, w0)
            lp.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(lp)
            limb = QPainterPath(QPointF(x, top_y + 2.5))
            limb.quadTo(x + ex * 0.4, top_y + ey * 0.3,
                        x + ex, top_y + ey)
            p.drawPath(limb)
        p.setPen(Qt.PenStyle.NoPen)
        # Trunk with a flared, rooted base.
        trunk = QPainterPath(QPointF(x - 3.6, base_y + 0.4))
        trunk.quadTo(x - 2.3, base_y - 5, x - 1.7, top_y + 2)
        trunk.quadTo(x - 1.4, top_y - 0.2, x + 0.2, top_y - 0.6)
        trunk.quadTo(x + 1.6, top_y - 0.2, x + 1.9, top_y + 2)
        trunk.quadTo(x + 2.5, base_y - 5, x + 3.8, base_y + 0.4)
        trunk.quadTo(x + 2.3, base_y - 0.7, x + 1.5, base_y + 0.3)
        trunk.quadTo(x + 0.7, base_y - 0.6, x, base_y + 0.4)
        trunk.quadTo(x - 0.7, base_y - 0.6, x - 1.5, base_y + 0.3)
        trunk.quadTo(x - 2.3, base_y - 0.7, x - 3.6, base_y + 0.4)
        trunk.closeSubpath()
        p.setBrush(bark)
        p.drawPath(trunk)
        # A little bark shading down the east side.
        p.setBrush(bark_lo)
        sh = QPainterPath(QPointF(x + 0.4, top_y - 0.2))
        sh.quadTo(x + 1.7, top_y + 2, x + 1.6, base_y - 1)
        sh.quadTo(x + 1.0, base_y - 1, x + 0.6, base_y)
        sh.quadTo(x + 0.5, top_y + 3, x + 0.4, top_y - 0.2)
        sh.closeSubpath()
        p.drawPath(sh)
        # The fox earth: a dark arch dug in under the roots.
        den = QColor(_INK)
        earth = QPainterPath(QPointF(x - 3.2, base_y + 0.6))
        earth.quadTo(x - 3.0, base_y - 2.4, x - 1.3, base_y - 2.5)
        earth.quadTo(x + 0.2, base_y - 2.3, x + 0.2, base_y + 0.6)
        earth.closeSubpath()
        p.setBrush(den)
        p.drawPath(earth)
        # The PRF — a bat-roost cavity up the trunk, dark with a torn
        # lighter lip of bark.
        prf_x, prf_y = x - 1.9, base_y - 6.6
        p.setBrush(_lerp(bark.name(), _CHALK, 0.25))
        p.drawEllipse(QPointF(prf_x + 0.15, prf_y), 1.35, 2.3)
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(prf_x, prf_y), 0.95, 1.85)
        # Crown.
        crown_y = base_y - 15.5
        if season == "winter":
            twig = QPen(bark, 0.5)
            twig.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(twig)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for ex, ey, _w in limbs:
                bx, by = x + ex, top_y + ey
                for k in range(4):
                    a = -1.9 + 1.3 * k / 3
                    p.drawLine(QPointF(bx, by),
                               QPointF(bx + 2.6 * math.cos(a),
                                       by - 2.6 * abs(math.sin(a)) - 0.6))
            # Snow lies along the tops of the bare limbs, caps the
            # fork, and banks against the rooted base.
            snowc = _lerp(_SNOW_DAY, _SNOW_NIGHT, d)
            sn = QPen(snowc, 0.8)
            sn.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(sn)
            for ex, ey, _w in limbs:
                limb = QPainterPath(QPointF(x, top_y + 2.1))
                limb.quadTo(x + ex * 0.4, top_y + ey * 0.3 - 0.5,
                            x + ex, top_y + ey - 0.4)
                p.drawPath(limb)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(snowc)
            p.drawEllipse(QPointF(x + 0.2, top_y - 0.7), 2.1, 0.7)
            p.drawEllipse(QPointF(x - 2.7, base_y + 0.1), 2.0, 0.55)
            p.drawEllipse(QPointF(x + 2.9, base_y + 0.1), 2.0, 0.55)
        else:
            lobes = ((0.5, 0.0, 8.5, 6.2), (-6.0, 1.8, 5.2, 4.2),
                     (6.4, 1.4, 5.4, 4.2), (-2.6, -4.4, 4.8, 3.8),
                     (3.4, -4.0, 5.0, 3.8), (0.4, -6.2, 4.0, 3.2))
            base_leaf = QColor(leaf_c)
            hi = base_leaf.lighter(112)
            lo = base_leaf.darker(110)
            for j, (cx, cy, rx, ry) in enumerate(lobes):
                p.setBrush((base_leaf, hi, lo)[j % 3])
                p.drawEllipse(QPointF(x + cx, crown_y + cy), rx, ry)
            if season == "autumn":
                p.setBrush(_lerp("#7A5A22", _INK, d * 0.5))
                for ax, ay in ((-3.5, 1.0), (2.0, 2.4), (5.0, -1.0),
                               (-1.0, -3.0), (4.0, 1.6)):
                    p.drawEllipse(QPointF(x + ax, crown_y + ay), 0.5, 0.65)
            elif season == "spring":
                bl = QColor("#F1E7B8")
                bl.setAlphaF(0.85)
                p.setBrush(bl)
                for bx, by in ((-4.0, -1.0), (3.0, -2.4), (0.0, 2.0),
                               (5.5, 1.0), (-2.0, 3.0)):
                    p.drawEllipse(QPointF(x + bx, crown_y + by), 0.7, 0.7)
        p.restore()

        def _a(px, py):        # map a design point through the tree scale
            return (x + (px - x) * S, base_y + (py - base_y) * S)

        self._oak = {"x": x, "base": _a(x - 1.4, base_y - 0.4),
                     "prf": _a(prf_x, prf_y),
                     "perch": _a(x + 9.2, top_y - 1.6),
                     "crown": _a(x + 0.5, crown_y),
                     "top": base_y + (top_y - base_y) * S,
                     "base_y": base_y}

    def _draw_forest(self, p, wd, color, span=_FOREST, conifer_c=None,
                     bare=False, far_c=None):
        """A wood of individual crowns, not one blob: tone alternates
        light/dark tree to tree, sizes and lifts vary, and the odd gap
        opens right through the canopy. In winter the broadleaves lose
        their leaves and only a grey filigree of bare crowns remains;
        emergent conifers keep their evergreen colour year-round. A
        paler second rank of crown-tops peeks over the crest from the
        hill's far side, so the wood reads as rolling on down."""
        x0, x1 = span
        if x0 > wd:
            return
        base = QColor(color)
        hi = QColor(color).lighter(115)
        lo = QColor(color).darker(112)
        if conifer_c is None:
            conifer_c = color
        p.setPen(Qt.PenStyle.NoPen)
        if far_c is not None and not bare:
            fx2 = float(x0) + 4.0
            while fx2 < x1 - 2:
                if fx2 + 5 > wd - 4:
                    break
                n = math.sin(fx2 * 6.31) * 999.0
                fr = n - math.floor(n)
                if fr > 0.88:                 # the far canopy breaks too
                    fx2 += 9 + fr * 5
                    continue
                fy = self._ridge_y(_BACK_PTS, wd, fx2) + 0.5
                p.setBrush(QColor(far_c).lighter(106) if fr > 0.5
                           else QColor(far_c))
                p.drawEllipse(QPointF(fx2, fy - 1.6 - fr * 1.6),
                              3.6 + fr * 2.0, 1.8 + fr * 0.8)
                fx2 += 6.5 + fr * 5.0
        # The wood sits ON the slope: a soft shadow band along the
        # trunk line anchors the canopy to the ground.
        if not bare:
            shb = QColor(_INK)
            shb.setAlphaF(0.14)
            p.setBrush(shb)
            sx0 = float(x0) + 2.0
            while sx0 < min(x1 - 4, wd - 8):
                sy = self._ridge_y(_BACK_PTS, wd, sx0 + 5) + 1.6
                p.drawEllipse(QPointF(sx0 + 5, sy), 6.5, 0.9)
                sx0 += 11.0
        i = 0
        tx = float(x0)
        while tx < x1:
            if tx + 9 > wd - 4:
                break
            n = math.sin(tx * 4.91) * 999.0
            fr = n - math.floor(n)
            if fr > 0.86:                     # a break in the canopy
                tx += 10 + fr * 6
                i += 1
                continue
            ty = self._ridge_y(_BACK_PTS, wd, tx) + 1
            lift = fr * 3.4
            r1 = 6.0 + fr * 3.6
            if bare:
                # A leafless crown: a faint domed haze of twigs.
                twig = QColor(color).darker(112)
                twig.setAlphaF(0.5)
                cxp, cyp = tx + r1 * 0.28, ty - 6.2 - lift
                tp = QPen(twig, 0.5)
                tp.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(tp)
                for k in range(7):
                    a = math.pi * (0.12 + 0.76 * k / 6)
                    p.drawLine(QPointF(cxp, ty),
                               QPointF(cxp + (r1 * 0.9) * math.cos(a),
                                       cyp - (3.4 + fr) * math.sin(a) + 1.2))
                p.setPen(Qt.PenStyle.NoPen)
            else:
                p.setBrush((base, hi, lo)[i % 3])
                p.drawEllipse(QPointF(tx, ty - 5.4 - lift), r1,
                              3.6 + fr * 1.4)
                p.drawEllipse(QPointF(tx + r1 * 0.55, ty - 7.6 - lift * 0.7),
                              r1 * 0.62, 2.6 + fr)
            i += 1
            tx += 9 + fr * 8
        pen = QPen(QColor(color).darker(118), 0.9)
        p.setPen(pen)
        for tx in range(x0 + 6, min(x1, int(wd) - 6), 17):
            ty = self._ridge_y(_BACK_PTS, wd, tx) + 1
            p.drawLine(QPointF(tx, ty), QPointF(tx, ty - 4.5))
        for fx, fs in ((x0 + 34, 1.25), (x0 + 106, 1.05)):
            if fx + 8 < wd - 4:
                self._draw_conifer(p, fx, self._ridge_y(_BACK_PTS, wd, fx) + 1,
                                   fs, conifer_c)

    def _draw_rivers(self, p, wd, d, bot, sky):
        """All three rivers, running fuller for a while after rain.
        Called twice per frame: once on the back plane (upper reaches,
        under the far wildlife's feet) and once clipped to the front
        hill (lower reaches) — see _paint."""
        swell = 1.0 + 0.45 * self._swell
        if _STREAM_X + 20 < wd:
            self._draw_river(p, d, _STREAM_X,
                             self._ridge_y(_BACK_PTS, wd, _STREAM_X) + 2,
                             widths=(1.2 * swell, 3.0 * swell), amp=5.0,
                             sky=sky, bottom=bot + 1)
        if _RIVER_X + 12 < wd:
            self._draw_river(p, d, _RIVER_X,
                             self._ridge_y(_BACK_PTS, wd, _RIVER_X) - 0.5,
                             widths=(2.2 * swell, 6.0 * swell), amp=10.0,
                             sky=sky, bottom=bot + 1, fall=True)
        if _RIVER2_X + 14 < wd:
            self._draw_river(p, d, _RIVER2_X,
                             self._ridge_y(_BACK_PTS, wd, _RIVER2_X) - 0.5,
                             widths=(2.6 * swell, 7.5 * swell), amp=13.0,
                             sky=sky, bottom=bot + 1, fall=True)

    # Terrain tucks: (river x, shoulder dx, y below the back ridge,
    # rx, ry) — a gradient-matched rise of the back hill drawn OVER
    # each river's far reach, so the water slips out of sight behind
    # the land and reappears below. The single strongest cue that the
    # river lies IN the landscape rather than on it.
    _RIVER_TUCKS = ((_STREAM_X, -2.5, 4.6, 8.0, 1.7),
                    (_RIVER_X, 3.0, 4.2, 11.0, 2.2),
                    (_RIVER2_X, -3.5, 5.4, 13.0, 2.6))

    def _draw_river_shoulders(self, p, wd, d, back_g):
        """Painted with the SAME vertical gradient as the back hill, so
        each shoulder is invisible against the grass and only shows by
        what it hides. A whisper of shade along the downhill edge gives
        the rise its form."""
        p.setPen(Qt.PenStyle.NoPen)
        for rx, dx, dy, srx, sry in self._RIVER_TUCKS:
            if rx + 14 >= wd:
                continue
            sy = self._ridge_y(_BACK_PTS, wd, rx) + dy
            p.setBrush(back_g)
            p.drawEllipse(QPointF(rx + dx, sy), srx, sry)
            shade = QColor(_INK)
            shade.setAlphaF(0.07)
            pen = QPen(shade, 0.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(rx + dx - srx, sy - sry, srx * 2, sry * 2),
                      200 * 16, 140 * 16)
            p.setPen(Qt.PenStyle.NoPen)

    def _draw_river(self, p, d, x_top, y_top, widths, amp, sky,
                    bottom=_DESIGN_H + 1, fall=False):
        """A living hill river: shaded banks, flow-lines drifting
        downstream, boulders parting the current with bow-waves and
        wakes, and a band of broken white water — rapids — partway
        down. Everything is placed by a per-river hash so the rocks
        never jump between frames, and the foam brightens in spate.

        Perspective is what stops it reading as a flat ribbon: the far
        reach snakes through tight foreshortened switchbacks and stays
        thread-thin and sky-hazed, then the channel opens as it comes
        forward — width grows super-linearly, the bends slacken to the
        old lazy curve, and only the near water carries foam, rocks
        and bank detail."""
        span = bottom - y_top

        def h(k):
            v = math.sin(x_top * 12.9898 + k * 78.233) * 43758.5453
            return v - math.floor(v)

        # The far switchback zone (top ~third) vs the near lazy curve.
        sw = 0.32
        k_far = math.pi * (2.6 + 0.9 * h(1))   # 2-3 bends per river
        s_far = 1.0 if h(2) > 0.5 else -1.0

        def _blend(t):
            u = max(0.0, min(1.0, (t - sw + 0.10) / 0.20))
            return u * u * (3.0 - 2.0 * u)

        def meander(t):
            far = (s_far * math.sin(t / sw * k_far) * amp * 0.5
                   * min(1.0, t / 0.06))       # leaves the pool straight
            near = math.sin(t * 4.4) * amp * (0.3 + 0.7 * t)
            u = _blend(t)
            return far * (1.0 - u) + near * u

        def bend(t):
            far = abs(math.sin(t / sw * k_far))
            near = abs(math.sin(t * 4.4))
            u = _blend(t)
            return far * (1.0 - u) + near * u

        def centre(t):
            return (x_top + meander(t), y_top + span * t)

        def halfw(t):
            # Slim in the distance, opening toward the viewer, with
            # the channel widening into pools on the bends.
            w0 = widths[0] * 0.85
            return ((w0 + (widths[-1] - w0) * t ** 1.7)
                    * (1.0 + 0.24 * bend(t)))

        def flow(t):
            e = 0.012
            x0, yy0 = centre(max(0.0, t - e))
            x1, yy1 = centre(min(1.0, t + e))
            dx, dy = x1 - x0, yy1 - yy0
            ln = math.hypot(dx, dy) or 1.0
            return dx / ln, dy / ln

        n = 21
        left, right = [], []
        for i in range(n):
            t = i / (n - 1)
            hw = halfw(t)
            cx, cy = centre(t)
            left.append(QPointF(cx - hw, cy))
            right.append(QPointF(cx + hw, cy))

        def smooth(path, pts):
            for i in range(1, len(pts) - 1):
                path.quadTo(pts[i], (pts[i] + pts[i + 1]) / 2)
            path.lineTo(pts[-1])

        path = QPainterPath(left[0])
        smooth(path, left)
        path.lineTo(right[-1])
        smooth(path, list(reversed(right)))
        path.closeSubpath()
        # Moving water sits a shade deeper than the still lakes, which
        # also lets the white water tell against it — and the far reach
        # lifts toward the sky like every other distant thing.
        water = QColor(_lerp(_WATER_DAY, _WATER_NIGHT, d).darker(122))
        # The far reach carries the LAKES' bright sky-reflection tone —
        # that's what makes the distant thread and its switchbacks read
        # against the pale hill — deepening to moving-water dark as the
        # channel comes forward.
        far_water = _lerp(_WATER_DAY, _WATER_NIGHT, d).lighter(104)
        chan_g = QLinearGradient(0, y_top, 0, y_top + span * 0.55)
        chan_g.setColorAt(0.0, far_water)
        chan_g.setColorAt(1.0, water)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(chan_g)
        p.drawPath(path)
        if fall:
            # A plunge pool where the falls land, wide enough that the
            # thread of river visibly sets out from something.
            pool_rx = max(3.4, widths[-1] * 0.62)
            p.setBrush(far_water)
            p.drawEllipse(QPointF(x_top, y_top + 0.5), pool_rx,
                          pool_rx * 0.30)
            rim = QColor(water.darker(130))
            rim.setAlphaF(0.5)
            pen = QPen(rim, 0.4)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(x_top - pool_rx, y_top + 0.5 - pool_rx * 0.3,
                             pool_rx * 2, pool_rx * 0.6),
                      200 * 16, 140 * 16)
            p.setPen(Qt.PenStyle.NoPen)

        night = 1.0 - 0.45 * d               # foam dims after dark
        spate = 1.0 + 0.45 * self._swell     # pre-swollen widths match
        foam = min(1.0, (0.9 + 0.5 * self._swell)) * night
        big = widths[-1] >= 5.0

        p.save()
        p.setClipPath(path)

        # Banks, asymmetric — identical crisp strokes down both edges
        # are exactly what makes a ribbon. The shaded bank hugs the
        # right edge only, and only on the near reach; the left edge
        # gets a pale shingle sliver instead.
        i30 = int(0.30 * (n - 1)) + 1
        bank = QColor(water.darker(135))
        bank.setAlphaF(0.55)
        pen = QPen(bank, 1.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rp = QPainterPath(right[i30])
        smooth(rp, right[i30:])
        p.drawPath(rp)
        shingle = _lerp("#C9BFA6", _INK, 0.55 * d)
        shingle.setAlphaF(0.45)
        pen = QPen(shingle, 0.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        sh_pts = [QPointF(lpt.x() + halfw(i / (n - 1)) * 0.22, lpt.y())
                  for i, lpt in enumerate(left)
                  if 0.52 <= i / (n - 1) <= 0.88]
        if len(sh_pts) > 2:
            shp = QPainterPath(sh_pts[0])
            smooth(shp, sh_pts)
            p.drawPath(shp)

        # Flow-lines: pale slivers riding the current downstream —
        # near water only; the far reach is too distant for detail.
        streak = QColor("#FFFFFF")
        for k in range(5 if big else 3):
            tt = (h(k) + self._t * (0.045 + 0.02 * (k % 3))) % 1.0
            tt = 0.30 + 0.70 * tt
            cx, cy = centre(tt)
            fx, fy = flow(tt)
            off = (h(k + 9) - 0.5) * 1.2 * halfw(tt)
            ln = (1.3 + 1.4 * h(k + 5)) * (0.4 + 0.6 * tt)
            streak.setAlphaF(0.40 * night
                             * min(1.0, (tt - 0.30) * 6, (1.0 - tt) * 4))
            pen = QPen(streak, 0.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            sx, sy = cx - fy * off, cy + fx * off
            p.drawLine(QPointF(sx - fx * ln, sy - fy * ln),
                       QPointF(sx + fx * ln, sy + fy * ln))

        # Rapids: a rocky pinch where the surface breaks into white
        # crests, shimmering as the water tumbles through. Each crest
        # is pure white over a dark trough just downstream — that
        # light/dark pairing is what makes broken water read.
        tb = 0.34 + 0.30 * h(20)
        chalk = QColor("#FFFFFF")
        trough = QColor(water.darker(150))
        # First the whole reach whitens, so it reads even from afar…
        rap = QPainterPath()
        for i in range(7):
            t2 = tb - 0.10 + 0.20 * i / 6
            cxx, cyy = centre(t2)
            if i == 0:
                rap.moveTo(cxx, cyy)
            else:
                rap.lineTo(cxx, cyy)
        chalk.setAlphaF(0.32 * foam)
        pen = QPen(chalk, 2 * halfw(tb) * 0.85)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rap)
        # …then the individual breaking crests over it.
        for k in range(9 if big else 4):
            tt = tb + (h(k + 60) - 0.5) * 0.17
            cx, cy = centre(tt)
            fx, fy = flow(tt)
            off = (h(k + 70) - 0.5) * 1.5 * halfw(tt)
            ln = 0.5 + 0.5 * h(k + 80)
            pulse = 0.65 + 0.35 * math.sin(self._t * 3.0 + k * 2.4)
            sx, sy = cx - fy * off, cy + fx * off
            # Dashes sit across the flow, like stacked breaking crests.
            for col, al, w, push in ((trough, 0.45, 0.55, 0.5),
                                     (chalk, 0.85, 0.6, 0.0)):
                col.setAlphaF(al * foam * pulse)
                pen = QPen(col, w)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                ox, oy = sx + fx * push, sy + fy * push
                p.drawLine(QPointF(ox - fy * ln - fx * ln * 0.4,
                                   oy + fx * ln - fy * ln * 0.4),
                           QPointF(ox + fy * ln + fx * ln * 0.4,
                                   oy - fx * ln + fy * ln * 0.4))

        # Boulders: weather-worn stones the river must go around.
        rock = _lerp(_lerp("#8E9289", _INK, 0.18).name(),
                     _INK, 0.45 * d)
        rock_hi = _lerp(rock.name(), _CHALK, 0.35)
        p.setPen(Qt.PenStyle.NoPen)
        n_rock = 3 if big else 1
        for k in range(n_rock):
            # One stone per stretch, swapping banks, so they never bunch
            # — and none in the far reach, where a stone would be a dot.
            tt = 0.36 + (k + 0.15 + 0.7 * h(k + 30)) * 0.52 / n_rock
            cx, cy = centre(tt)
            fx, fy = flow(tt)
            hw = halfw(tt) / spate      # rocks don't grow in spate…
            off = (1 if k % 2 else -1) * (0.15 + 0.45 * h(k + 40)) * hw
            r = max(0.6, hw * (0.30 + 0.20 * h(k + 50)))
            bx, by = cx - fy * off, cy + fx * off
            # …the water around them does: bow-wave upstream…
            chalk.setAlphaF(0.8 * foam)
            pen = QPen(chalk, 0.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(bx - r * 1.25, by - r * 1.25,
                             r * 2.5, r * 2.5),
                      int((math.degrees(math.atan2(fy, fx)) + 130) * 16),
                      100 * 16)
            # …and a diverging white wake downstream.
            chalk.setAlphaF(0.6 * foam)
            p.setPen(QPen(chalk, 0.45))
            for s in (-1, 1):
                px, py = -fy * s, fx * s
                p.drawLine(QPointF(bx + fx * r * 0.9 + px * r * 0.7,
                                   by + fy * r * 0.9 + py * r * 0.7),
                           QPointF(bx + fx * r * 2.6 + px * r * 1.15,
                                   by + fy * r * 2.6 + py * r * 1.15))
            p.setPen(Qt.PenStyle.NoPen)
            for j in range(3):
                pulse = 0.5 + 0.5 * math.sin(self._t * 2.6
                                             + j * 2.1 + k * 1.3)
                chalk.setAlphaF(0.7 * foam * pulse)
                p.setBrush(chalk)
                wob = (h(k * 3 + j + 90) - 0.5) * r * 1.4
                p.drawEllipse(QPointF(bx + fx * r * (1.5 + 0.6 * j)
                                      - fy * wob,
                                      by + fy * r * (1.5 + 0.6 * j)
                                      + fx * wob),
                              0.28, 0.22)
            # The stone itself, lit along its upstream shoulder.
            p.setBrush(rock)
            p.drawEllipse(QPointF(bx, by), r, r * 0.8)
            p.setBrush(rock_hi)
            p.drawEllipse(QPointF(bx - r * 0.22, by - r * 0.3),
                          r * 0.55, r * 0.34)

        # The big near bend behaves like a meander: a glint along the
        # outside of the turn, a shingle bar dropped on the inside.
        if big:
            ta = 0.357                      # sin(t·4.4) apex
            cx, cy = centre(ta)
            fx, fy = flow(ta)
            sgn = 1.0 if meander(ta) >= 0 else -1.0
            hw = halfw(ta)
            gl = QColor(_CHALK)
            gl.setAlphaF(0.40 * night
                         * (0.7 + 0.3 * math.sin(self._t * 1.1)))
            pen = QPen(gl, 0.45)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            gx, gy = cx + sgn * hw * 0.72, cy
            p.drawLine(QPointF(gx - fx * 1.1, gy - fy * 1.1),
                       QPointF(gx + fx * 1.1, gy + fy * 1.1))
            p.setPen(Qt.PenStyle.NoPen)
            bar = _lerp("#C9BFA6", _INK, 0.55 * d)
            bar.setAlphaF(0.65)
            p.setBrush(bar)
            p.drawEllipse(QPointF(cx - sgn * hw * 0.55, cy + 0.1),
                          hw * 0.45, 0.5)
        p.restore()

        # Plunge pool dressing where the waterfall lands: churned foam
        # across the pool and spray mist drifting up off it.
        if fall:
            pool_rx = max(3.4, widths[-1] * 0.62)
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(5):
                pulse = 0.55 + 0.45 * math.sin(self._t * 3.4 + k * 1.7)
                chalk.setAlphaF(0.7 * foam * pulse)
                p.setBrush(chalk)
                fxp = x_top + (h(k + 100) - 0.5) * 1.7 * pool_rx
                p.drawEllipse(QPointF(fxp,
                                      y_top + 0.4 + h(k + 110) * 0.7),
                              0.5 + 0.3 * h(k + 105), 0.3)
            for k in range(3):
                ph = h(k + 120) * 6.28
                rise = (self._t * 0.35 + h(k + 130)) % 1.0
                mist = QColor(_CHALK)
                mist.setAlphaF(0.13 * night * (1.0 - rise)
                               * min(1.0, rise * 4.0))
                p.setBrush(mist)
                mx = x_top + (h(k + 140) - 0.5) * pool_rx
                p.drawEllipse(
                    QPointF(mx + 0.6 * math.sin(rise * 6.28 + ph),
                            y_top - 0.2 - rise * 2.6),
                    1.1 + rise * 0.9, 0.8 + rise * 0.5)

    def _draw_lake(self, p, cx, cy, rx, ry, d, mults, glint_x=None):
        n = len(mults)
        pts = []
        for i, m in enumerate(mults):
            a = 2 * math.pi * i / n
            pts.append(QPointF(cx + rx * m * math.cos(a),
                               cy + ry * (0.6 + 0.4 * m) * math.sin(a)))
        shore = QPainterPath((pts[-1] + pts[0]) / 2)
        for i in range(n):
            shore.quadTo(pts[i], (pts[i] + pts[(i + 1) % n]) / 2)
        frozen = getattr(self, "_season_cache", "") == "winter"
        if frozen:
            # Frozen over: a pale, flat ice sheet — no reflections, no
            # movement — with frost streaks and windblown snow patches.
            ice = _lerp("#DDE8EE", "#4A5C66", d)
            grad = QLinearGradient(0, cy - ry, 0, cy + ry)
            grad.setColorAt(0.0, ice.lighter(106))
            grad.setColorAt(0.55, ice)
            grad.setColorAt(1.0, ice.darker(105))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawPath(shore)
            edge = QColor(ice.darker(135))
            edge.setAlphaF(0.55)
            p.setPen(QPen(edge, 0.55))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(shore)
            p.setPen(Qt.PenStyle.NoPen)
            p.save()
            p.setClipPath(shore)
            frost = QColor(_CHALK)
            for k in range(3):
                nq = math.sin((cx + k * 31.7) * 2.9) * 999.0
                fq = nq - math.floor(nq)
                frost.setAlphaF(0.22 - 0.05 * k)
                p.setPen(QPen(frost, 0.4))
                y2 = cy - ry * 0.4 + k * ry * 0.42 + fq * 0.6
                p.drawLine(QPointF(cx - rx * (0.7 - 0.15 * k), y2),
                           QPointF(cx + rx * (0.55 - 0.1 * k),
                                   y2 + 0.8 + fq))
            p.setPen(Qt.PenStyle.NoPen)
            snowp = _lerp(_SNOW_DAY, _SNOW_NIGHT, d)
            for k in range(4):
                nq = math.sin((cx + k * 17.3) * 4.1) * 999.0
                fq = nq - math.floor(nq)
                sp2 = QColor(snowp)
                sp2.setAlphaF(0.75)
                p.setBrush(sp2)
                p.drawEllipse(QPointF(cx + (fq - 0.5) * rx * 1.4,
                                      cy - ry * 0.5 + fq * ry * 1.1),
                              rx * (0.10 + 0.09 * fq), 0.7 + fq * 0.5)
            p.restore()
            # Frosted reeds still stand at the shore, no reflections.
            reed_c = _lerp("#8A7E5E", _INK, d * 0.6)
            pen = QPen(reed_c, 1.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for ex, ey, lean, k in ((cx - rx * 0.92, cy, -1, 3),
                                    (cx + rx * 0.95, cy, 1, 3)):
                for dx, h in ((0, 8), (1.8, 6), (3.4, 7))[:k]:
                    bx = ex + dx * lean
                    p.drawLine(QPointF(bx, ey),
                               QPointF(bx + 1.2 * lean, ey - h))
            p.setPen(Qt.PenStyle.NoPen)
            return
        # A real water surface: the far edge mirrors the dark bank, then
        # a bright band of reflected sky, deepening toward the near shore.
        water = _lerp(_WATER_DAY, _WATER_NIGHT, d)
        bank_refl = _lerp(water.name(),
                          _lerp(_FRONT, _INK, 0.25).name(), 0.55)
        grad = QLinearGradient(0, cy - ry, 0, cy + ry)
        grad.setColorAt(0.0, bank_refl)
        grad.setColorAt(0.14, water.lighter(112))
        grad.setColorAt(0.5, water)
        grad.setColorAt(1.0, water.darker(118))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(shore)
        # A thin dark contact line the whole way round: water sits IN
        # the ground, not on it.
        edge = QColor(water.darker(138))
        edge.setAlphaF(0.5)
        p.setPen(QPen(edge, 0.55))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(shore)
        p.setPen(Qt.PenStyle.NoPen)
        p.save()
        p.setClipPath(shore)
        # Far bank casts a thin shadow where the land meets the water…
        bank = QColor(water.darker(140))
        bank.setAlphaF(0.5)
        p.setPen(QPen(bank, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        p.setClipRect(QRectF(cx - rx * 1.5, cy - ry * 1.7,
                             rx * 3.0, ry * 1.25), Qt.ClipOperation.IntersectClip)
        p.drawPath(shore)
        p.restore()
        # …and the near waterline catches the light.
        rim = QColor(_CHALK)
        rim.setAlphaF(0.30 - 0.12 * d)
        p.setPen(QPen(rim, 0.9))
        p.save()
        p.setClipRect(QRectF(cx - rx * 1.5, cy + ry * 0.3,
                             rx * 3.0, ry * 2.0), Qt.ClipOperation.IntersectClip)
        p.drawPath(shore)
        p.restore()
        # Ripple slivers drift slowly across the surface.
        p.setPen(Qt.PenStyle.NoPen)
        rip = QColor(_CHALK)
        for k, fy in enumerate((-0.12, 0.22, 0.52)):
            rip.setAlphaF((0.16, 0.12, 0.09)[k] * (1.0 - 0.5 * d))
            p.setBrush(rip)
            off = math.sin(self._t * (0.5 + 0.3 * k) + k * 2.1 + cx) \
                * rx * 0.08
            p.drawEllipse(QPointF(cx + off - rx * 0.1, cy + ry * fy),
                          rx * (0.55 - 0.12 * k), 0.42)
        # The sun or moon lays a broken glitter path down the water.
        if glint_x is not None and abs(glint_x - cx) < rx * 0.9:
            gl = QColor(_CHALK)
            for k in range(4):
                yy = cy - ry * 0.55 + k * ry * 0.42
                wob = math.sin(self._t * 1.7 + k * 1.9 + cx) * 0.8
                gl.setAlphaF(0.30 - 0.055 * k)
                p.setBrush(gl)
                p.drawEllipse(QPointF(glint_x + wob, yy),
                              1.6 - 0.25 * k, 0.38)
        p.restore()
        reed_c = _lerp(_TREE_DAY, _TREE_NIGHT, d)
        pen = QPen(reed_c, 1.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        refl_pen = QPen(reed_c, 0.8)
        refl_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for ex, ey, lean, k in ((cx - rx * 0.92, cy, -1, 3),
                                (cx + rx * 0.95, cy, 1, 3),
                                (cx + rx * 0.25, cy - ry * 0.95, 1, 2)):
            for i, (dx, h) in enumerate(((0, 8), (1.8, 6), (3.4, 7))[:k]):
                bx = ex + dx * lean
                p.setPen(pen)
                p.drawLine(QPointF(bx, ey),
                           QPointF(bx + 1.2 * lean, ey - h))
                # Each reed lays a wobbling mirror-image on the water.
                rc = QColor(reed_c)
                rc.setAlphaF(0.30)
                refl_pen.setColor(rc)
                p.setPen(refl_pen)
                wob = 0.5 * math.sin(self._t * 1.1 + bx)
                p.drawLine(QPointF(bx, ey + 0.4),
                           QPointF(bx + 0.9 * lean + wob,
                                   ey + h * 0.45))
                if i == 1:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(reed_c)
                    p.drawEllipse(QPointF(bx + 1.0 * lean, ey - h + 0.8),
                                  0.7, 1.6)
        if getattr(self, "_season_cache", "") == "spring" and cx < 900:
            # Frogspawn rafts in the warm near-shore shallows.
            sp = QColor(_CHALK)
            sp.setAlphaF(0.55)
            dot = QColor(_INK)
            dot.setAlphaF(0.7)
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(7):
                n = math.sin((cx + k) * 5.13) * 999.0
                fr = n - math.floor(n)
                fx2 = cx - rx * 0.45 + fr * rx * 0.35
                fy2 = cy + ry * 0.38 + (k % 2) * 0.45
                p.setBrush(sp)
                p.drawEllipse(QPointF(fx2, fy2), 0.5, 0.42)
                p.setBrush(dot)
                p.drawEllipse(QPointF(fx2, fy2), 0.16, 0.14)

    def _flower(self, p, fx, fy, s, alpha, kind, d):
        """One flower head. Four species, each with its own shape —
        oxeye daisy, poppy, buttercup and knapweed."""
        p.setPen(Qt.PenStyle.NoPen)
        night = max(0.0, (d - 0.5) * 1.2)

        def col(name):
            c = QColor(name)
            if night:
                c = _lerp(c.name(), _INK, min(0.75, night))
            c.setAlphaF(alpha)
            return c

        if kind == "daisy":
            p.setBrush(col(_CHALK))
            for k in range(5):
                a = 2 * math.pi * k / 5
                p.drawEllipse(QPointF(fx + 0.55 * s * math.cos(a),
                                      fy + 0.55 * s * math.sin(a)),
                              0.34 * s, 0.34 * s)
            p.setBrush(col("#E4C25A"))
            p.drawEllipse(QPointF(fx, fy), 0.32 * s, 0.32 * s)
        elif kind == "poppy":
            p.setBrush(col("#D0442B"))
            p.drawEllipse(QPointF(fx, fy), 0.62 * s, 0.55 * s)
            p.setBrush(col(_INK))
            p.drawEllipse(QPointF(fx, fy), 0.2 * s, 0.2 * s)
        elif kind == "buttercup":
            p.setBrush(col("#E8C63F"))
            p.drawEllipse(QPointF(fx, fy), 0.42 * s, 0.42 * s)
        else:                                   # knapweed: pink tuft
            p.setBrush(col("#B86AA0"))
            for ddx, ddy in ((-0.3, 0.1), (0.3, 0.1), (0.0, -0.35)):
                p.drawEllipse(QPointF(fx + ddx * s, fy + ddy * s),
                              0.3 * s, 0.38 * s)

    def _draw_meadow(self, p, wd, d):
        """A wildflower meadow with real depth: flowers scatter down the
        hill face in three loose ranks — smaller and fainter up-slope,
        bigger and bolder near the foot — mixing daisies, poppies,
        buttercups and knapweed."""
        x0, x1 = _MEADOW
        if x0 + 10 > wd - 4:
            return
        winter = getattr(self, "_season_cache", "") == "winter"
        stem = _lerp(_TUFT_DAY, _TUFT_NIGHT, d)
        if winter:
            stem = _lerp("#8A7E5E", _INK, d * 0.6)   # dead-grass straw
        kinds = ("daisy", "poppy", "buttercup", "knapweed")
        for rank, (depth, mul, alpha) in enumerate(
                ((1.2, 0.62, 0.7), (4.2, 0.82, 0.88), (7.4, 1.05, 1.0))):
            tx = x0 + (rank * 2.3) % 5
            while tx < min(x1, wd - 6):
                n = math.sin((tx + rank * 37.7) * 7.13) * 999.0
                fr = n - math.floor(n)
                ty = self._ridge_y(_FRONT_PTS, wd, tx) + depth + fr * 1.8
                h = (2.0 + 1.6 * fr) * mul
                lean = (fr - 0.5) * 1.4 + self._wind_at(tx) * 1.1
                p.setPen(QPen(stem, 0.55 * mul))
                p.drawLine(QPointF(tx, ty), QPointF(tx + lean, ty - h))
                if not winter:                 # no blooms in winter
                    self._flower(p, tx + lean, ty - h, mul, alpha,
                                 kinds[int(fr * 7.9) % 4], d)
                tx += 3.6 + fr * 4.2

    def _draw_near_band(self, p, wd, d, bot, season, cover, snow_c, rank):
        """Dressing for the extension band below the classic scene edge —
        the nearest few metres of meadow, when the banner runs taller
        than the scene. Two ranks give it real depth: "mid" (drawn
        behind the nearest wildlife) scatters big tufts and tall
        wildflowers through the middle of the band; "front" (drawn
        after the wildlife, bigger and sparser, hugging the bottom
        edge) overlaps their feet so animals walk between the grass."""
        extra = bot - _DESIGN_H
        if extra < 2.5:
            return
        winter = season == "winter"
        stem = _lerp(_TUFT_DAY, _TUFT_NIGHT, d)
        if winter:
            stem = _lerp("#8A7E5E", _INK, d * 0.6)   # dead-grass straw
        if cover > 0.0:
            stem = _lerp(stem.name(), snow_c.name(), 0.8 * cover)
        if rank == "mid":
            mul = 1.5
            band_y = _DESIGN_H + extra * 0.42
            step, seed, x0 = 36.0, 37.7, 16.0
        else:
            mul = 2.2
            stem = _lerp(stem.name(), _INK, 0.12)    # nearer = deeper tone
            band_y = bot - 0.8
            step, seed, x0 = 58.0, 91.3, 44.0
        kinds = ("daisy", "poppy", "buttercup", "knapweed")
        tuft_pen = QPen(stem, 0.75 * mul)
        tuft_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        tx = x0
        while tx < wd + 4:            # padded past the parallax slide
            n = math.sin((tx + seed) * 7.13) * 999.0
            fr = n - math.floor(n)
            ty = band_y + (fr - 0.5) * min(extra * 0.22, 2.6)
            # Keep the mid rank off any lake that dips into the band.
            if rank == "mid":
                lk = self._lake_at(tx, wd)
                if lk is not None and ty < lk[1] + lk[3] + 1.5:
                    tx += step * 0.6
                    continue
            p.setPen(tuft_pen)
            wv = self._wind_at(tx) * mul      # gusts sweep the band too
            for ddx, hh in ((-1.5, 2.4), (0.0, 3.4), (1.5, 2.2)):
                p.drawLine(QPointF(tx + ddx * mul, ty),
                           QPointF(tx + ddx * 1.7 * mul + wv,
                                   ty - hh * mul))
            # A tall wildflower beside most tufts — not in winter, and
            # not once lying snow has buried the sward.
            if not winter and cover < 0.4 and fr > 0.3:
                fx = tx + 3.2 * mul * (fr - 0.5)
                fh = (2.8 + 1.9 * fr) * mul
                lean = (fr - 0.5) * 2.6 + wv * 1.2
                p.setPen(QPen(stem, 0.55 * mul))
                p.drawLine(QPointF(fx, ty), QPointF(fx + lean, ty - fh))
                self._flower(p, fx + lean, ty - fh, mul * 0.85, 1.0,
                             kinds[int(fr * 7.9) % 4], d)
            tx += step + fr * 30.0

    def _draw_bluebells(self, p, wd, d):
        """A bluebell drift with depth: two ranks of nodding stems strung
        with little hanging bells — the bees' favourite stop."""
        x0, x1 = _BLUEBELLS
        if x0 + 8 > wd - 4:
            return
        if getattr(self, "_season_cache", "") not in ("spring", "summer"):
            return                     # bluebells are a spring show
        stem = _lerp(_TUFT_DAY, _TUFT_NIGHT, d)
        for rank, (depth, mul, alpha) in enumerate(
                ((1.4, 0.72, 0.8), (4.0, 1.0, 1.0))):
            bell = _lerp("#6C7BD0", "#333F6E", d)
            bell.setAlphaF(alpha)
            tx = x0 + rank * 1.6
            while tx < min(x1, wd - 5):
                n = math.sin((tx + rank * 21.3) * 8.21) * 999.0
                fr = n - math.floor(n)
                ty = (self._ridge_y(_FRONT_PTS, wd, tx) + depth
                      + fr * 1.4)
                h = (2.8 + 1.6 * fr) * mul
                p.setPen(QPen(stem, 0.55 * mul))
                p.setBrush(Qt.BrushStyle.NoBrush)
                curve = QPainterPath(QPointF(tx, ty))
                curve.quadTo(tx + 0.4, ty - h * 0.7, tx + 1.4 * mul,
                             ty - h)
                p.drawPath(curve)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bell)
                for k in range(3):
                    p.drawEllipse(
                        QPointF(tx + (0.6 + k * 0.5) * mul,
                                ty - h + (0.4 + k * 0.7) * mul),
                        0.36 * mul, 0.52 * mul)
                tx += 2.6 + fr * 1.6

    @staticmethod
    def _hedge_leaf(p, x, y, s, ang):
        """One pointed, lobed leaf (ivy-ish) in the current brush —
        the hedge's whole texture is built from these."""
        p.save()
        p.translate(x, y)
        p.rotate(ang)
        leaf = QPainterPath(QPointF(0.0, -1.0 * s))
        leaf.quadTo(0.18 * s, -0.25 * s, 0.95 * s, -0.35 * s)
        leaf.quadTo(0.35 * s, 0.12 * s, 0.55 * s, 0.90 * s)
        leaf.quadTo(0.0, 0.35 * s, -0.55 * s, 0.90 * s)
        leaf.quadTo(-0.35 * s, 0.12 * s, -0.95 * s, -0.35 * s)
        leaf.quadTo(-0.18 * s, -0.25 * s, 0.0, -1.0 * s)
        leaf.closeSubpath()
        p.drawPath(leaf)
        p.restore()

    def _draw_hedgerows(self, p, wd, d, leaf_c, season, cover, snow_c,
                        bare, blossom):
        """Field-boundary hedgerows along the front hill's contour,
        built leaf on leaf: a dense dark body under a scatter of
        pointed leaves, lime-lit along the top, deep green in the
        shade. The run rises and falls — tall stands that hide a deer
        whole, low saddles where its back and head pop into view as it
        passes behind. A five-bar gate in the gap, standards clear of
        the crown; may blossom in spring, haws in autumn."""
        for x0, x1, gate in _HEDGES:
            if x0 + 12 > wd - 4:
                continue
            base = _lerp(_TREE_DAY, _TREE_NIGHT, d)
            if season == "autumn":
                base = _lerp(base.name(), "#8A6A2E", 0.40)
            elif season == "winter":
                base = _lerp("#5E5142", _INK, d * 0.7)
            if cover > 0.0:
                base = _lerp(base.name(), snow_c.name(), 0.35 * cover)
            hi = QColor(base).lighter(118)
            lo = QColor(base).darker(124)
            lit = _lerp(base.name(), "#B9CB6A", 0.40 * (1.0 - 0.6 * d))
            p.setPen(Qt.PenStyle.NoPen)
            seed1 = x0 * 0.13
            i = 0
            tx = float(x0)
            while tx < min(x1, wd - 6):
                if gate - 4.8 < tx < gate + 4.8:   # the gateway gap
                    tx += 2.2
                    i += 1
                    continue
                ty = self._ridge_y(_FRONT_PTS, wd, tx) + 2.6
                n = math.sin(tx * 6.7) * 999.0
                fr = n - math.floor(n)
                # The undulating top line: two slow waves, clamped.
                hf = (0.52 + 0.28 * math.sin(tx * 0.035 + seed1)
                      + 0.20 * math.sin(tx * 0.011 + seed1 * 2.7))
                hf = max(0.45, min(1.0, hf))
                hh = 13.0 * hf
                # A solid body first, so no daylight leaks through. The
                # skirt runs well below the base line: mid-layer
                # animals wander a few px deeper than the ridge, and
                # without it their feet poke out under the hedge.
                p.setBrush(lo if i % 2 else QColor(base).darker(110))
                p.drawRoundedRect(QRectF(tx - 2.3, ty - hh + 1.0,
                                         4.6 + fr, hh + 2.9), 1.6, 1.6)
                # …then the foliage: leaf on leaf, lit well down the
                # face the way low sun catches a hedge, deep green only
                # in the under-shade.
                for k in range(8):
                    n2 = math.sin(tx * 2.9 + k * 23.7) * 999.0
                    f2 = n2 - math.floor(n2)
                    n3 = math.sin(tx * 5.3 + k * 7.9) * 999.0
                    f3 = n3 - math.floor(n3)
                    lyy = ty - 0.8 - f2 * (hh - 1.2)
                    lxx = tx + (f3 - 0.5) * 4.6
                    rel = (ty - lyy) / hh
                    if rel > 0.66 and f3 > 0.25:
                        p.setBrush(lit)
                    elif rel > 0.38:
                        p.setBrush(hi if f2 > 0.45 else base)
                    else:
                        p.setBrush(base if f3 > 0.5 else lo)
                    self._hedge_leaf(p, lxx, lyy, 1.35 + f3 * 1.0,
                                     (f2 - 0.5) * 80.0)
                # Crown leaves breaking the top edge — the ragged leafy
                # silhouette instead of a smooth line.
                for k in range(2):
                    n4 = math.sin(tx * 8.3 + k * 13.7) * 999.0
                    f4 = n4 - math.floor(n4)
                    p.setBrush(lit if f4 > 0.45 else hi)
                    self._hedge_leaf(p, tx + (f4 - 0.5) * 3.6,
                                     ty - hh - 0.3 - f4 * 0.7,
                                     1.1 + f4 * 0.7, (f4 - 0.5) * 70.0)
                if season == "winter":
                    # Snow settles along the hedge top.
                    p.setBrush(_lerp(_SNOW_DAY, _SNOW_NIGHT, d))
                    p.drawEllipse(QPointF(tx + 0.4,
                                          ty - hh - 0.35 - fr * 0.3),
                                  2.2 + fr * 0.8, 0.75)
                if blossom and fr > 0.62:          # may blossom
                    fl = QColor(_CHALK)
                    fl.setAlphaF(0.85)
                    p.setBrush(fl)
                    p.drawEllipse(QPointF(tx + 0.3, ty - hh - 0.8),
                                  0.45, 0.45)
                    p.drawEllipse(QPointF(tx + 2.0, ty - hh + 0.4),
                                  0.4, 0.4)
                elif season == "autumn" and fr > 0.55:   # haws
                    haw = QColor("#C23A2E")
                    haw.setAlphaF(0.85)
                    p.setBrush(haw)
                    p.drawEllipse(QPointF(tx + 1.4, ty - hh + 0.9),
                                  0.28, 0.28)
                i += 1
                tx += 2.9 + fr * 1.5
            # A proper five-bar field gate spanning the gap — hung a
            # little lower than the hedge, as they are.
            if gate + 3 < wd - 4:
                gy = self._ridge_y(_FRONT_PTS, wd, gate) + 2.6
                bark = _lerp("#8A7A5E", _INK, d * 0.6)
                pen = QPen(bark, 0.55)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                for px2 in (gate - 4.0, gate + 4.0):
                    p.drawLine(QPointF(px2, gy + 0.4),
                               QPointF(px2, gy - 8.2))
                for by in (-7.4, -5.8, -4.2, -2.6, -1.0):
                    p.drawLine(QPointF(gate - 4.0, gy + by),
                               QPointF(gate + 4.0, gy + by))
                p.drawLine(QPointF(gate - 4.0, gy - 1.0),
                           QPointF(gate + 4.0, gy - 7.4))
                p.setPen(Qt.PenStyle.NoPen)
            # Standard trees rising well clear of the hedge line.
            for ft, fs in ((0.30, 2.3), (0.74, 1.9)):
                ftx = x0 + (x1 - x0) * ft
                n = math.sin(ftx * 4.3) * 999.0
                if (n - math.floor(n)) > 0.75 or ftx + 6 > wd - 4:
                    continue                       # not every hedge keeps two
                fty = self._ridge_y(_FRONT_PTS, wd, ftx) + 2.6
                self._draw_broadleaf(p, ftx, fty, fs, leaf_c, bare=bare,
                                     blossom=blossom,
                                     trunk=_lerp(_BARK_DAY, _BARK_NIGHT, d))

    def _draw_band_props(self, p, wd, d, bot, season, cover, snow_c):
        """One-off set-pieces scattered through the extension band —
        molehills, a lichened boulder, a log pile, a rabbit-cropped
        patch with its burrow — spaced widely so no two screens look
        alike. Drawn behind the nearest wildlife."""
        extra = bot - _DESIGN_H
        if extra < 4.0:
            return
        band_y = _DESIGN_H + extra * 0.6
        tx = 78.0
        while tx < wd - 20:
            n = math.sin(tx * 3.91) * 999.0
            fr = n - math.floor(n)
            kind = int(fr * 3.98)
            py = band_y + (fr - 0.5) * min(extra * 0.3, 3.0)
            lk = self._lake_at(tx, wd)
            if lk is not None and py < lk[1] + lk[3] + 2.0:
                tx += 90.0
                continue
            p.setPen(Qt.PenStyle.NoPen)
            if kind == 0:                        # molehills, fresh earth
                soil = _lerp("#4A3B2A", _INK, d * 0.5)
                soil.setAlphaF(1.0 - 0.25 * cover)
                p.setBrush(soil)
                for j in range(2 + int(fr * 1.9)):
                    mx = tx + j * (4.6 + fr * 2.0)
                    my = py + (j % 2) * 1.1
                    p.drawEllipse(QPointF(mx, my), 2.1, 1.25)
                    p.drawEllipse(QPointF(mx - 0.9, my + 0.55), 1.4, 0.7)
            elif kind == 1:                      # a lichened boulder
                rock = _lerp("#8E9289", _INK, d * 0.6)
                if cover > 0.0:
                    rock = _lerp(rock.name(), snow_c.name(), 0.45 * cover)
                sh = QColor(_INK)
                sh.setAlphaF(0.14)
                p.setBrush(sh)
                p.drawEllipse(QPointF(tx + 0.4, py + 1.6), 3.6, 0.8)
                p.setBrush(rock)
                p.drawEllipse(QPointF(tx, py), 3.4, 2.3)
                p.setBrush(QColor(rock).lighter(116))
                p.drawEllipse(QPointF(tx - 1.0, py - 0.8), 1.9, 1.1)
                lich = QColor("#C9C79A")
                lich.setAlphaF(0.7 * (1.0 - 0.6 * d))
                p.setBrush(lich)
                for lx3, ly3, lr in ((-1.8, 0.2, 0.5), (0.9, -1.0, 0.4),
                                     (1.6, 0.7, 0.45)):
                    p.drawEllipse(QPointF(tx + lx3, py + ly3), lr, lr)
            elif kind == 2:                      # a log pile (beetle inn)
                bark = _lerp("#6B5236", _INK, d * 0.6)
                ring = _lerp("#C9B58A", _INK, d * 0.55)
                pen = QPen(bark, 1.7)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(tx - 4.6, py + 0.9),
                           QPointF(tx + 4.2, py + 0.6))
                p.setPen(Qt.PenStyle.NoPen)
                for ex, ey in ((-1.6, -0.4), (1.6, -0.5), (0.0, -2.0)):
                    p.setBrush(bark)
                    p.drawEllipse(QPointF(tx + ex, py + ey), 1.5, 1.5)
                    p.setBrush(ring)
                    p.drawEllipse(QPointF(tx + ex, py + ey), 0.95, 0.95)
                    p.setBrush(QColor(bark).darker(115))
                    p.drawEllipse(QPointF(tx + ex, py + ey), 0.3, 0.3)
            elif cover < 0.4:                    # rabbit-cropped sward
                lawn = _lerp("#4E7D5B", "#22392E", d)
                lawn.setAlphaF(0.9)
                p.setBrush(lawn)
                p.drawEllipse(QPointF(tx, py + 0.4), 8.5, 1.9)
                hole = QColor(_INK)
                hole.setAlphaF(0.8)
                p.setBrush(hole)
                p.drawEllipse(QPointF(tx + 6.4, py - 0.1), 1.15, 0.8)
                soil = _lerp("#4A3B2A", _INK, d * 0.5)
                soil.setAlphaF(0.85)
                p.setBrush(soil)
                p.drawEllipse(QPointF(tx + 7.2, py + 0.75), 1.5, 0.55)
                drop = QColor(_INK)
                drop.setAlphaF(0.30)
                p.setBrush(drop)
                for dx2 in (-3.0, -1.2, 1.4):
                    p.drawEllipse(QPointF(tx + dx2, py + 0.9), 0.22, 0.18)
            tx += 240.0 + fr * 160.0

    def _draw_wall(self, p, wd, d):
        """A drystone field wall of big rounded boulders: two fat
        courses of blue-grey stones over a dark joint-shadow core, a
        run of round copers along the top, rough grass at the foot,
        stone cheeks at the gateway and a timber five-bar gate hung
        between them — the boulder wall of every Dales gateway."""
        x0, x1 = _WALL
        if x0 + 8 > wd - 4:
            return
        joint = _lerp("#46534F", _INK, d * 0.5)
        tones = (_lerp("#7E8F8E", "#33413E", d),
                 _lerp("#8FA09E", "#3A4844", d),
                 _lerp("#6E807F", "#2D3A38", d),
                 _lerp("#93A39B", "#3D4A44", d))
        winter = getattr(self, "_season_cache", "") == "winter"
        snow = _lerp(_SNOW_DAY, _SNOW_NIGHT, d)
        gt = _lerp(_TUFT_DAY, _TUFT_NIGHT, d)
        p.setPen(Qt.PenStyle.NoPen)
        runs = ((float(x0), _WALL_GAP[0] - 0.6),
                (_WALL_GAP[1] + 0.6, float(x1)))
        for rx0, rx1 in runs:
            if rx0 + 6 > wd - 5:
                continue
            rx1 = min(rx1, wd - 5.0)
            # The dark core first — every gap between boulders reads as
            # a shadowed joint.
            core = QPainterPath(
                QPointF(rx0, self._ridge_y(_FRONT_PTS, wd, rx0) + 1.2))
            xq = rx0
            while xq < rx1:
                core.lineTo(QPointF(
                    xq, self._ridge_y(_FRONT_PTS, wd, xq) - 4.5))
                xq += 6.0
            core.lineTo(QPointF(
                rx1, self._ridge_y(_FRONT_PTS, wd, rx1) - 4.5))
            core.lineTo(QPointF(
                rx1, self._ridge_y(_FRONT_PTS, wd, rx1) + 1.2))
            core.closeSubpath()
            p.setBrush(joint)
            p.drawPath(core)
            # Two staggered courses of fat rounded boulders.
            for course, (oy, sh2) in enumerate(((-0.1, 2.2), (-2.15, 2.0))):
                txs = rx0 + 0.25 + (course % 2) * 1.8
                while txs < rx1 - 0.7:
                    n = math.sin(txs * 4.83 + course * 9.1) * 999.0
                    fr = n - math.floor(n)
                    w2 = min(2.4 + fr * 1.7, rx1 - txs)
                    ty = self._ridge_y(_FRONT_PTS, wd, txs + w2 / 2)
                    p.setBrush(tones[int(fr * 3.98)])
                    p.drawRoundedRect(
                        QRectF(txs, ty + oy - sh2 + (fr - 0.5) * 0.3,
                               w2 - 0.4, sh2), 1.0, 1.0)
                    txs += w2
            # Round copers along the top.
            txs = rx0 + 0.8
            while txs < rx1 - 0.6:
                n = math.sin(txs * 7.21) * 999.0
                fr = n - math.floor(n)
                ty = self._ridge_y(_FRONT_PTS, wd, txs)
                p.setBrush(tones[(int(fr * 3.98) + 2) % 4])
                p.drawEllipse(QPointF(txs + 0.7, ty - 4.5 + fr * 0.3),
                              0.95 + fr * 0.35, 0.85)
                if winter:
                    p.setBrush(snow)
                    p.drawEllipse(QPointF(txs + 0.7, ty - 5.05 + fr * 0.3),
                                  0.9 + fr * 0.3, 0.36)
                txs += 1.5 + fr * 0.9
            # Rough grass seats the wall into the hill.
            pen = QPen(gt, 0.45)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            gx2 = rx0 + 2.0
            while gx2 < rx1 - 1.0:
                n = math.sin(gx2 * 3.3) * 999.0
                fr = n - math.floor(n)
                gy2 = self._ridge_y(_FRONT_PTS, wd, gx2) + 0.8
                p.drawLine(QPointF(gx2, gy2 + 0.5),
                           QPointF(gx2 - 0.4 + fr * 0.8, gy2 - 1.3))
                gx2 += 3.0 + fr * 2.5
            p.setPen(Qt.PenStyle.NoPen)
        # Stone cheeks — stacked boulders — and the timber gate between.
        for gx in _WALL_GAP:
            if gx < wd - 5:
                ty = self._ridge_y(_FRONT_PTS, wd, gx) + 0.2
                for k, (cw, chh) in enumerate(((2.1, 1.7), (1.9, 1.6),
                                               (1.7, 1.5))):
                    n = math.sin(gx * 3.1 + k * 7.7) * 999.0
                    fr = n - math.floor(n)
                    p.setBrush(tones[int(fr * 3.98)])
                    p.drawRoundedRect(
                        QRectF(gx - cw / 2, ty - 1.65 * (k + 1) - 0.15,
                               cw, chh), 0.8, 0.8)
        if _WALL_GAP[1] < wd - 5:
            g0, g1 = _WALL_GAP
            ty = self._ridge_y(_FRONT_PTS, wd, (g0 + g1) / 2) + 0.2
            bark = _lerp("#8A7A5E", _INK, d * 0.6)
            pen = QPen(bark, 0.45)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for by in (-3.6, -2.6, -1.6, -0.6):
                p.drawLine(QPointF(g0 + 0.7, ty + by),
                           QPointF(g1 - 0.7, ty + by))
            p.drawLine(QPointF(g0 + 0.7, ty - 0.6),
                       QPointF(g1 - 0.7, ty - 3.6))
            p.setPen(Qt.PenStyle.NoPen)

    def _draw_gorse(self, p, wd, d):
        """Gorse clumps — dense dark mounds studded with yellow bloom."""
        winter = getattr(self, "_season_cache", "") == "winter"
        bloom = _lerp("#E8C63F", "#8A7A2C", d)
        bush = _lerp("#4A6B3A", "#1C2C22", d)
        for gx, gs in _GORSE:
            if gx + 5 * gs > wd - 4:
                continue
            gy = self._ridge_y(_FRONT_PTS, wd, gx) + 1.2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bush)
            p.drawEllipse(QPointF(gx, gy - 1.8 * gs), 3.2 * gs, 1.9 * gs)
            p.drawEllipse(QPointF(gx - 1.8 * gs, gy - 1.0 * gs),
                          2.0 * gs, 1.3 * gs)
            p.drawEllipse(QPointF(gx + 1.9 * gs, gy - 1.1 * gs),
                          2.1 * gs, 1.3 * gs)
            if winter:
                # A dusting of snow settles along the top of the bush.
                cap = _lerp(_SNOW_DAY, _SNOW_NIGHT, d)
                p.setBrush(cap)
                p.drawEllipse(QPointF(gx, gy - 3.2 * gs),
                              2.6 * gs, 0.55 * gs)
                continue
            p.setBrush(bloom)
            for k in range(7):
                n = math.sin((gx + k) * 9.31) * 999.0
                fr = n - math.floor(n)
                bx = gx + (fr - 0.5) * 5.6 * gs
                by = (gy - 1.4 * gs
                      - (math.sin((gx + k) * 3.7) * 0.5 + 0.5) * 1.6 * gs)
                p.drawEllipse(QPointF(bx, by), 0.42 * gs, 0.42 * gs)

    def _draw_heather(self, p, wd, d):
        """A heather bank: overlapping mauve and deep-purple mounds."""
        x0, x1 = _HEATHER
        if x0 + 8 > wd - 4:
            return
        season = getattr(self, "_season_cache", "")
        bloom = _lerp("#9A6FB0", "#3E3350", d)
        deep = _lerp("#7B5691", "#332B44", d)
        if season == "winter":
            # Faded winter heather: russet-brown, past its bloom.
            bloom = _lerp("#8A6A52", _INK, d * 0.6)
            deep = _lerp("#6E5442", _INK, d * 0.6)
        p.setPen(Qt.PenStyle.NoPen)
        i = 0
        tx = float(x0)
        while tx < min(x1, wd - 5):
            ty = self._ridge_y(_FRONT_PTS, wd, tx) + 1.6 + (i % 3) * 0.9
            p.setBrush(bloom if i % 2 else deep)
            p.drawEllipse(QPointF(tx, ty), 2.6, 1.1)
            i += 1
            tx += 3.4

    # ── species sprites ───────────────────────────────────────────────
    # All drawn at origin top-left facing right; _render_walker handles
    # mirroring, layer scale, shadows and water. Each takes the walker
    # dict so poses can read pause state.
    @staticmethod
    def _hold_frac(wk):
        if wk["pause_t"] <= 0:
            return 0.0
        return 1.0 - wk["pause_t"] / max(0.01, wk["pause_total"])

    @staticmethod
    def _legs(p, color, hips, length, gait, swing_deg):
        pen = QPen(QColor(color), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for hx, hy, phase in hips:
            a = math.radians(swing_deg) * math.sin(gait + phase)
            p.drawLine(QPointF(hx, hy),
                       QPointF(hx + length * math.sin(a),
                               hy + length * math.cos(a)))

    @staticmethod
    def _leg_joint(p, color, hips, upper, lower, gait, swing_deg,
                   bend_deg=26, ks=-1, alpha=1.0, width=1.5, a_off=0.0,
                   foot=0.0):
        """Two-segment legs with a real joint: the shin folds by up to
        bend_deg as the leg swings through its lifted phase. hips is a
        sequence of (hip_x, hip_y, phase). ks=-1 tucks the foot backward
        (mammal knees and hocks); ks=+1 kicks it forward under a
        backward-pointing bird ankle (pair with a negative a_off so the
        thigh sits behind). a_off biases the thigh angle; foot > 0 adds a
        little forward foot at the end of the shin. Far-side legs pass a
        lower alpha so limbs visibly scissor past each other."""
        col = QColor(color)
        col.setAlphaF(alpha)
        pen = QPen(col, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for hx, hy, phase in hips:
            a = (math.radians(swing_deg) * math.sin(gait + phase)
                 + math.radians(a_off))
            lift = max(0.0, math.sin(gait + phase + 0.8))
            b = a + ks * math.radians(bend_deg) * (0.22 + 0.78 * lift)
            kx = hx + upper * math.sin(a)
            ky = hy + upper * math.cos(a)
            fx = kx + lower * math.sin(b)
            fy = ky + lower * math.cos(b)
            leg = QPainterPath(QPointF(hx, hy))
            leg.lineTo(QPointF(kx, ky))
            leg.lineTo(QPointF(fx, fy))
            if foot > 0:
                leg.lineTo(QPointF(fx + foot, fy - 0.15))
            p.drawPath(leg)

    @staticmethod
    def _foot_cycle(gait, phase, stride, lift, duty=0.62):
        """Where a foot is in its step, as an (dx, dy) offset from the
        neutral ground contact below the hip. During STANCE the foot is
        planted and slides backward (so it appears to stay put on the
        ground as the body moves over it); during SWING it lifts and
        arcs forward. This is what makes a walk read as a walk."""
        u = ((gait / (2 * math.pi)) + phase) % 1.0
        if u < duty:                       # stance: planted, sliding back
            s = u / duty
            return stride * (0.5 - s), 0.0
        s = (u - duty) / (1.0 - duty)      # swing: lift and reach forward
        return stride * (-0.5 + s), -lift * math.sin(math.pi * s)

    @staticmethod
    def _leg_ik(p, color, hx, hy, fx, fy, l1, l2, knee_sign,
                width=1.5, alpha=1.0):
        """Two-bone leg from hip to a foot TARGET, knee solved by the law
        of cosines so the joint bends like a real leg. knee_sign picks
        which way the knee folds (fore vs hind)."""
        col = QColor(color)
        col.setAlphaF(alpha)
        pen = QPen(col, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        dx, dy = fx - hx, fy - hy
        d = math.hypot(dx, dy)
        d = max(abs(l1 - l2) + 0.05, min(l1 + l2 - 0.03, d))
        base = math.atan2(dy, dx)
        cos_a = max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2)
                              / (2 * l1 * d)))
        ang = base + knee_sign * math.acos(cos_a)
        kx, ky = hx + l1 * math.cos(ang), hy + l1 * math.sin(ang)
        fx2, fy2 = hx + d * math.cos(base), hy + d * math.sin(base)
        path = QPainterPath(QPointF(hx, hy))
        path.lineTo(QPointF(kx, ky))
        path.lineTo(QPointF(fx2, fy2))
        p.drawPath(path)

    def _walk_legs(self, p, color, legs, gait, ground, stride, lift,
                   l1, l2, alpha=1.0, width=1.5, duty=0.62):
        """Draw a set of walking legs. legs = (hip_x, hip_y, phase,
        knee_sign). Each foot follows the plant-and-lift cycle; a high
        duty makes the foot dwell (a deliberate, pausing step)."""
        for hx, hy, phase, ksign in legs:
            dx, dy = self._foot_cycle(gait, phase, stride, lift, duty)
            self._leg_ik(p, color, hx, hy, hx + dx, ground + dy,
                         l1, l2, ksign, width, alpha)

    @staticmethod
    def _stalk_gate(gait, duty=0.8):
        """Forward-motion multiplier for a stalking bird: near-still
        while both feet are planted, gliding forward as a foot swings.
        Averages to ~1 so overall pace is unchanged."""
        a = (gait / (2 * math.pi)) % 1.0
        b = (a + 0.5) % 1.0
        return 2.35 if (a > duty or b > duty) else 0.06

    @staticmethod
    def _walk_bob(gait, amp=0.5):
        """Vertical body rise/fall over a stride — the back lifts twice
        per cycle as the legs pass under it."""
        return amp * math.sin(2 * gait)

    # ── depth / perspective ────────────────────────────────────────────
    # A grounded animal carries a continuous depth 0 (deep at the back,
    # small) … 1 (right at the front, large). Scale and the ground line
    # it walks on both follow from it, and it drifts slowly so animals
    # wander nearer and further as they cross — real perspective.
    @staticmethod
    def _depth_scale(depth):
        return 0.52 + 0.9 * depth

    @staticmethod
    def _depth_layer(depth, prev=None):
        # Hysteresis around the band edges: a walker drifting near a
        # threshold doesn't flicker between render layers (each flip
        # changes what occludes it — lakes, grass ranks, trees).
        lo, hi = 0.34, 0.7
        if prev == "back":
            lo = 0.37
        elif prev == "fore":
            hi = 0.67
        elif prev == "mid":
            lo, hi = 0.31, 0.73
        if depth < lo:
            return "back"
        if depth < hi:
            return "mid"
        return "fore"

    def _front_limit(self):
        """The nearest ground line an animal can walk on: the classic
        43.5 at scene height, easing down into the extension band when
        the banner runs taller — so the closest wildlife comes right
        down among the tabs, larger and nearer."""
        return 43.5 + min((self._design_bot() - _DESIGN_H) * 0.5, 8.0)

    def _depth_crest(self, depth, cx, wd):
        """The ground line at a given depth: up on the back hill when
        deep, down to the foreground when near."""
        fc = self._ridge_y(_FRONT_PTS, wd, cx)
        if depth <= 0.5:
            bc = self._ridge_y(_BACK_PTS, wd, cx)
            return bc + (fc - bc) * (depth / 0.5)
        return fc + (self._front_limit() - fc) * ((depth - 0.5) / 0.5)

    def _sort_y(self, wk, wd):
        """Paint-order key: the walker's ACTUAL ground line. Stored
        depth alone lies for the depth-locked species (snakes, otters,
        the heron, the ecologist walk the ridge line whatever their
        depth says), and a locked snake sorted by raw depth crawled
        across the front of a sheep grazing below it."""
        cx = wk["x"] + wk["w"] * wk["scale"] / 2
        if wk.get("depth_lock"):
            if wk["layer"] == "fore":
                return self._front_limit()
            return self._ridge_y(_BACK_PTS if wk["layer"] == "back"
                                 else _FRONT_PTS, wd, cx)
        return self._depth_crest(wk["depth"], cx, wd)

    @staticmethod
    def _antlers(p, color, hx, hy, ang, s=1.0):
        """A stag's antlers in profile: two near-parallel beams — one per
        side of the skull, the far one offset and fainter — each raking
        up-and-back with a forward brow tine, a mid tine and a crown
        fork. Anchored at (hx, hy), pitched by ang (degrees, clockwise)
        so they track the head when it lowers to graze, scaled by s."""
        p.save()
        p.translate(hx, hy)
        p.rotate(ang)
        p.scale(s, s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        w = 0.85 / max(0.6, s)
        for dx, aa in ((0.62, 0.55), (0.0, 1.0)):   # far side, then near
            col = QColor(color)
            col.setAlphaF(aa)
            pen = QPen(col, w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            beam = QPainterPath(QPointF(dx, 0.0))    # back-swept main beam
            beam.quadTo(dx - 0.75, -1.7, dx - 0.85, -3.0)
            beam.quadTo(dx - 0.78, -3.9, dx - 0.35, -4.5)
            p.drawPath(beam)
            p.drawLine(QPointF(dx + 0.02, -0.55),
                       QPointF(dx + 1.0, -1.15))     # brow tine, forward
            p.drawLine(QPointF(dx - 0.8, -2.3),
                       QPointF(dx + 0.12, -2.9))     # mid (trez) tine
            p.drawLine(QPointF(dx - 0.5, -4.05),
                       QPointF(dx - 1.15, -4.85))    # crown, back point
            p.drawLine(QPointF(dx - 0.35, -4.5),
                       QPointF(dx + 0.3, -5.15))     # crown, fore point
        p.restore()

    def _draw_ecologist(self, p, wk):
        """A field ecologist: olive coat under a hi-vis vest, dark
        trousers into boots, orange beanie, rucksack with a bedroll.
        Walks on knees that actually bend, then works — measuring a tree,
        crouching for a water sample, scanning with binoculars, or
        photographing the site."""
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        pose = wk["pose"]
        coat = _lerp("#3C6B4E", _INK, self._dark * 0.7)
        sleeve = _lerp("#2F5540", _INK, self._dark * 0.7)  # reads on coat
        vest = _lerp("#E39B2D", "#7A5218", self._dark * 0.55)
        pack = _lerp("#6E4A2A", _INK, self._dark * 0.7)
        hat = _lerp(_BELLY, "#7A3A22", self._dark * 0.5)
        skin = _lerp("#D8B295", "#6E5A4A", self._dark * 0.6)
        board = QColor(_CHALK)
        ink = QColor(_INK)
        crouch = hold and pose in ("sample", "refugia")
        dy = 4.0 if crouch else 0.0
        # Legs: the shin folds back as the foot lifts; the far leg runs
        # fainter and half a stride out of phase, boots tip each shin.
        if crouch:
            pen = QPen(ink, 2.1)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            for off, al in ((1.2, 0.55), (0.0, 1.0)):
                col = QColor(ink)
                col.setAlphaF(al)
                pen.setColor(col)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                leg = QPainterPath(QPointF(6.9 + off, 18.2 + dy))
                leg.lineTo(QPointF(10.2 + off, 22.4))
                leg.lineTo(QPointF(7.6 + off, 25.6))
                leg.lineTo(QPointF(9.0 + off, 25.8))
                p.drawPath(leg)
        elif hold:
            self._leg_joint(p, ink, ((8.3, 17.8, math.pi),), 4.0, 4.5, g,
                            2, bend_deg=10, alpha=0.55, width=2.0, foot=1.0)
            self._leg_joint(p, ink, ((7.1, 17.8, 0),), 4.0, 4.5, g,
                            2, bend_deg=10, width=2.0, foot=1.0)
        else:
            self._leg_joint(p, ink, ((8.1, 17.8, math.pi),), 4.0, 4.5, g,
                            20, bend_deg=34, alpha=0.55, width=2.0,
                            foot=1.0)
            self._leg_joint(p, ink, ((7.3, 17.8, 0),), 4.0, 4.5, g,
                            20, bend_deg=34, width=2.0, foot=1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pack)
        p.drawRoundedRect(QRectF(3.0, 10.4 + dy, 3.2, 6.8), 1.1, 1.1)
        p.drawEllipse(QPointF(4.7, 10.1 + dy), 1.7, 0.9)   # bedroll
        p.setBrush(coat)
        torso = QPainterPath(QPointF(4.8, 18.4 + dy))
        torso.quadTo(4.2, 11 + dy, 6.6, 9.4 + dy)
        torso.lineTo(9.4, 9.4 + dy)
        torso.quadTo(11, 12 + dy, 10.3, 18.4 + dy)
        torso.closeSubpath()
        p.drawPath(torso)
        # Hi-vis vest with a reflective band — very UK site-wear.
        p.setBrush(vest)
        p.drawRoundedRect(QRectF(6.0, 10.4 + dy, 3.6, 6.2), 1.0, 1.0)
        band = QColor(_CHALK)
        band.setAlphaF(0.85)
        p.setBrush(band)
        p.drawRect(QRectF(6.0, 12.9 + dy, 3.6, 0.72))
        hy = 6.0 + dy
        p.setBrush(skin)
        p.drawEllipse(QPointF(8.4, hy), 2.1, 2.2)
        p.setBrush(hat)
        beanie = QPainterPath(QPointF(6.2, hy - 0.3))
        beanie.quadTo(8.4, hy - 3.8, 10.6, hy - 0.3)
        beanie.closeSubpath()
        p.drawPath(beanie)
        p.drawRect(QRectF(6.2, hy - 0.6, 4.5, 0.9))
        if self._dark > 0.55:
            # After dark the head-torch comes on: a soft cone of light.
            beam = QColor("#F2E39A")
            beam.setAlphaF(0.15)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(beam)
            cone = QPainterPath(QPointF(10.3, hy - 0.7))
            cone.lineTo(QPointF(19.8, hy - 3.6))
            cone.lineTo(QPointF(19.8, hy + 2.4))
            cone.closeSubpath()
            p.drawPath(cone)
            lamp = QColor("#F2E39A")
            lamp.setAlphaF(0.95)
            p.setBrush(lamp)
            p.drawRect(QRectF(10.1, hy - 1.4, 0.9, 0.8))
        pen = QPen(coat, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        if pose == "measure" and hold:
            tape = QColor(_CHALK)
            tape.setAlphaF(0.85)
            p.setPen(QPen(tape, 0.8))
            p.drawLine(QPointF(13.6, 5.5), QPointF(13.6, 20))
            p.setPen(pen)
            reach = 0.4 * math.sin(g * 2)
            p.drawLine(QPointF(9.4, 11 + dy), QPointF(12.8 + reach, 8.2))
            p.drawLine(QPointF(7.6, 12 + dy), QPointF(9.6, 15.4))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(board)
            p.drawRect(QRectF(9.3, 14.4, 3.0, 3.9))
            p.setPen(QPen(ink, 0.4))
            for ly in (15.2, 16.2, 17.2):
                p.drawLine(QPointF(9.7, ly), QPointF(11.9, ly))
        elif crouch:
            dip = 0.6 * math.sin(g * 2)
            p.drawLine(QPointF(9.4, 13 + dy), QPointF(12.6, 20.4 + dy + dip))
            p.drawLine(QPointF(7.4, 13 + dy), QPointF(9.2, 16 + dy))
            p.setPen(Qt.PenStyle.NoPen)
            if pose == "refugia":
                # Lifting a corrugated tin refugia mat — a snake basks
                # underneath, revealed.
                tin = _lerp("#9AA0A2", "#454D4F", self._dark)
                p.save()
                p.translate(12.6, 19.4 + dy + dip)
                p.rotate(-26)
                p.setBrush(tin)
                p.drawRect(QRectF(0, 0, 5.0, 2.0))
                cp = QPen(_lerp("#787E80", _INK, self._dark), 0.3)
                p.setPen(cp)
                for cx2 in (1.0, 2.0, 3.0, 4.0):
                    p.drawLine(QPointF(cx2, 0.15), QPointF(cx2, 1.85))
                p.restore()
                sc_ = _lerp("#5A7A46", _INK, self._dark * 0.6)
                spen = QPen(sc_, 1.1)
                spen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(spen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                coil = QPainterPath(QPointF(13.6, 26.0 + dy))
                coil.quadTo(16.4, 25.0 + dy, 16.0, 26.6 + dy)
                coil.quadTo(15.6, 27.7 + dy, 14.2, 27.0 + dy)
                p.drawPath(coil)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(_CHALK))
                p.drawEllipse(QPointF(16.1, 25.4 + dy), 0.28, 0.28)
            else:
                p.setBrush(_lerp("#8FB6C8", "#5B7C8C", self._dark))
                p.drawRect(QRectF(11.9, 19.8 + dy + dip, 1.8, 3.0))
        elif pose == "binoculars" and hold:
            # Slim arms up to binoculars held FORWARD of the face, so the
            # head stays visible; a slow scanning sway.
            scan = 0.7 * math.sin(g * 0.7)
            spen = QPen(sleeve, 1.35)
            spen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(spen)
            p.drawLine(QPointF(9.4, 11.6), QPointF(11.0 + scan, hy + 0.9))
            p.drawLine(QPointF(7.6, 12.0), QPointF(10.2 + scan, hy + 1.1))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(10.3 + scan, hy - 0.65, 2.5, 1.25),
                              0.4, 0.4)
        elif pose == "photo" and hold:
            # Camera held out at eye level; the shutter blinks now and
            # then. Face stays clear of the arms.
            spen = QPen(sleeve, 1.35)
            spen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(spen)
            p.drawLine(QPointF(9.4, 11.7), QPointF(11.3, hy + 0.8))
            p.drawLine(QPointF(7.6, 12.2), QPointF(10.6, hy + 1.3))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(10.9, hy - 0.35, 2.3, 1.7), 0.3, 0.3)
            p.setBrush(_lerp("#8FB6C8", "#5B7C8C", self._dark))
            p.drawEllipse(QPointF(12.7, hy + 0.5), 0.5, 0.5)
            if math.sin(g * 2.4) > 0.93:
                fl = QColor(_CHALK)
                fl.setAlphaF(0.9)
                p.setBrush(fl)
                p.drawEllipse(QPointF(13.8, hy - 0.5), 0.8, 0.8)
        elif pose == "moth_trap" and hold:
            # Stood over a glowing moth trap, checking the catch.
            spen = QPen(sleeve, 1.35)
            spen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(spen)
            p.drawLine(QPointF(9.4, 11.6), QPointF(12.4, 17.8))
            p.setPen(Qt.PenStyle.NoPen)
            glow = QColor("#F2E39A")
            for gr, ga in ((4.2, 0.16), (2.6, 0.28)):
                glow.setAlphaF(ga)
                p.setBrush(glow)
                p.drawEllipse(QPointF(14.2, 23.4), gr, gr * 0.75)
            p.setBrush(QColor(_CHALK))
            p.drawRoundedRect(QRectF(12.8, 22.0, 2.9, 3.1), 0.5, 0.5)
            p.setBrush(QColor("#F2E39A"))
            p.drawRect(QRectF(13.3, 22.6, 1.9, 1.1))
        elif pose == "detector" and hold:
            # Bat detector held high, listening for passes.
            spen = QPen(sleeve, 1.35)
            spen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(spen)
            p.drawLine(QPointF(9.4, 11.4), QPointF(11.2, hy - 2.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(10.6, hy - 4.1, 1.7, 2.2), 0.3, 0.3)
            if math.sin(g * 3) > 0.55:          # click… click…
                tick = QColor(_CHALK)
                tick.setAlphaF(0.8)
                p.setPen(QPen(tick, 0.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                for rr in (1.2, 2.1):
                    p.drawArc(QRectF(11.4 - rr, hy - 3.1 - rr,
                                     rr * 2, rr * 2), 20 * 16, 90 * 16)
                p.setPen(Qt.PenStyle.NoPen)
        elif pose == "dip" and hold:
            # Pond-dipping: a long-handled net swept through the shallows.
            sweep = 1.3 * math.sin(g * 1.4)
            p.setPen(QPen(sleeve, 1.35))
            p.drawLine(QPointF(9.4, 11.8), QPointF(12.2, 13.4))
            pole = QPen(_lerp("#8A6E4A", _INK, self._dark * 0.5), 0.7)
            pole.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pole)
            tip = QPointF(19.6 + sweep, 22.8)
            p.drawLine(QPointF(12.2, 13.4), tip)
            p.setPen(QPen(_lerp("#D8DED9", "#7C8A82", self._dark), 0.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(tip, 1.7, 1.05)
            p.setPen(Qt.PenStyle.NoPen)
        elif pose == "torch" and hold:
            # Night torch survey of the pond: a beam sweeps the water and
            # picks out newts near the surface.
            sweep = 1.6 * math.sin(g * 0.6)
            p.setPen(QPen(sleeve, 1.35))
            p.drawLine(QPointF(9.4, 11.6), QPointF(12.6, 12.7))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawRect(QRectF(12.4, 12.1, 1.7, 1.0))
            beam = QColor("#F2E39A")
            beam.setAlphaF(0.15)
            p.setBrush(beam)
            cone = QPainterPath(QPointF(14.0, 12.2))
            cone.lineTo(QPointF(23.0 + sweep, 20.5))
            cone.lineTo(QPointF(22.0 + sweep, 24.2))
            cone.lineTo(QPointF(13.9, 13.6))
            cone.closeSubpath()
            p.drawPath(cone)
            nc = QColor("#D9C24A")
            nc.setAlphaF(0.85)
            p.setBrush(nc)
            for nx, ny in ((18.8 + sweep, 22.2), (20.8 + sweep, 23.0)):
                p.drawEllipse(QPointF(nx, ny), 0.85, 0.35)
        else:
            # Arms swing with a soft elbow in a darker sleeve tone so
            # they read against the coat; clipboard carried in front.
            self._leg_joint(p, sleeve, ((8.6, 10.8, math.pi),), 2.3, 2.5,
                            g, 14, bend_deg=24, ks=1, alpha=0.6,
                            width=1.5)
            self._leg_joint(p, sleeve, ((8.2, 10.8, 0),), 2.3, 2.5, g,
                            14, bend_deg=24, ks=1, width=1.6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(board)
            p.drawRect(QRectF(10.4, 14.3, 2.2, 3.0))

    def _draw_gcn(self, p, wk, swim=False):
        """Great crested newt: one sinuous form with a LOW wavy crest
        (not a dinosaur sawtooth), a pale flash along the flattened tail
        and a thin orange line at the belly edge."""
        g = wk["gait"]
        ink = QColor(_INK)
        wig = (0.9 * math.sin(g * 1.2)) if swim else (0.3 * math.sin(g))
        if swim:
            wake = QColor(_CHALK)
            wake.setAlphaF(0.5)
            pen = QPen(wake, 0.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for wx, wy in ((-2.5, 6.4), (-5.5, 5.8)):
                arc = QPainterPath(QPointF(wx, wy))
                arc.quadTo(wx - 2.4, wy + 1.0, wx - 4.8, wy + 0.6)
                p.drawPath(arc)
        else:
            self._legs(p, _INK, ((13, 7.0, 0), (16.5, 6.9, math.pi),
                                 (24, 6.8, math.pi), (27, 6.7, 0)),
                       2.4, g, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        body = QPainterPath(QPointF(0.6, 6.6 + 0.8 * wig))  # tail tip
        body.quadTo(4.0, 5.2, 8.0, 5.1)          # tail fin top
        body.quadTo(10.0, 4.2, 12.0, 4.9)        # low crest undulations
        body.quadTo(14.2, 4.0, 16.5, 4.8)
        body.quadTo(19.0, 4.0, 21.5, 4.8)
        body.quadTo(24.5, 4.4, 26.5, 5.1)        # neck
        body.quadTo(28.0, 4.5, 29.8, 4.8)        # head top
        body.quadTo(31.4, 5.1, 31.6, 5.8)        # blunt snout
        body.quadTo(31.2, 6.8, 29.4, 6.9)        # jaw
        body.quadTo(26.0, 7.3, 22.0, 7.1)        # belly
        body.quadTo(16.0, 7.5, 11.0, 7.2)
        body.quadTo(6.5, 7.6, 3.0, 7.5 + 0.9 * wig)   # tail underside
        body.quadTo(1.6, 7.4, 0.6, 6.6 + 0.8 * wig)
        body.closeSubpath()
        p.drawPath(body)
        # pale flash along the tail (breeding male)
        flash = QColor(_CHALK)
        flash.setAlphaF(0.75)
        pen = QPen(flash, 0.65)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(1.8, 6.5 + 0.7 * wig), QPointF(7.8, 6.2))
        # thin orange belly edge
        pen = QPen(QColor(_BELLY), 0.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        belly = QPainterPath(QPointF(11.5, 7.3))
        belly.quadTo(17.5, 7.7, 25.0, 7.15)
        p.drawPath(belly)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(30.1, 5.3), 0.5, 0.5)

    def _draw_otter(self, p, wk, swim=False):
        g = wk["gait"]
        c = _lerp("#4A342A", _INK, self._dark * 0.8)
        if swim:
            # One connected surfacing curve: tail awash, rolling hump,
            # neck and head riding clear, wake trailing behind.
            wake = QColor(_CHALK)
            wake.setAlphaF(0.5)
            pen = QPen(wake, 0.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for wx in (-2.5, -6):
                arc = QPainterPath(QPointF(wx, 4.4))
                arc.quadTo(wx - 2.4, 5.2, wx - 4.6, 4.8)
                p.drawPath(arc)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            hump = 0.8 * math.sin(g)
            surf = QPainterPath(QPointF(2.0, 5.1))
            surf.quadTo(6.0, 4.3 - 0.3 * hump, 10.0, 4.5)
            surf.quadTo(14.0, 2.8 - 0.7 * hump, 18.0, 4.1)
            surf.quadTo(20.5, 4.5, 22.3, 3.8)
            surf.quadTo(23.0, 2.6, 24.6, 2.6)
            surf.quadTo(26.2, 2.7, 26.6, 3.7)
            surf.quadTo(25.0, 4.9, 23.0, 4.9)
            surf.quadTo(18.0, 5.5, 10.0, 5.5)
            surf.quadTo(5.0, 5.7, 2.0, 5.1)
            surf.closeSubpath()
            p.drawPath(surf)
            p.setBrush(QColor(_INK))
            p.drawEllipse(QPointF(26.5, 3.4), 0.4, 0.35)   # nose
            p.setBrush(QColor(_CHALK))
            p.drawEllipse(QPointF(25.2, 3.1), 0.4, 0.4)    # eye
            return
        if wk["pause_t"] > 0 and wk["pose"] == "groom":
            # Sat up on the tail, rubbing its face with both forepaws.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            pen = QPen(c, 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(11, 11.2), QPointF(3.5, 11.6))    # tail out back
            p.setPen(Qt.PenStyle.NoPen)
            body = QPainterPath(QPointF(9, 11.6))
            body.quadTo(7.5, 6, 11, 4.2)
            body.quadTo(14, 2.8, 15.6, 5.2)
            body.quadTo(16.6, 8, 15, 11.6)
            body.closeSubpath()
            p.drawPath(body)
            p.drawEllipse(QPointF(14.4, 3.6), 2.3, 2.1)          # head
            rub = 0.7 * math.sin(g * 3)
            p.setBrush(c)
            p.drawEllipse(QPointF(13.2, 5.4 + rub), 0.9, 0.9)    # paws at face
            p.drawEllipse(QPointF(15.4, 5.6 - rub), 0.9, 0.9)
            p.setBrush(QColor(_CHALK))
            p.drawEllipse(QPointF(15.4, 3.0), 0.4, 0.4)
            return
        # Loping bound: one sleek connected form nose-to-tail — the back
        # arches with each stride, the thick rudder tapers to the ground.
        arch = (1 + math.sin(g)) / 2
        GND, L1, L2 = 12.2, 1.6, 1.6
        ofar = [(11.5, 9.5, 0.25, -1), (19.8, 9.5, 0.75, +1)]
        onear = [(9.0, 9.7, 0.0, -1), (21.6, 9.7, 0.5, +1)]
        self._walk_legs(p, _lerp(c.name(), _INK, 0.45), ofar, g, GND,
                        3.5, 1.4, L1, L2, alpha=0.9, width=1.3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        top = 6.6 - 1.8 * arch                     # arch height varies
        form = QPainterPath(QPointF(29.2, 6.6))    # nose tip
        form.quadTo(27.6, 5.2, 25.4, 5.6)          # head top
        form.quadTo(22.5, 5.4, 20.5, top + 1.0)    # neck into back
        form.quadTo(15.5, top - 1.4, 11.0, top + 0.8)   # the arch
        form.quadTo(7.5, 7.6, 5.2, 8.8)            # rump easing down
        form.quadTo(2.4, 10.2, -1.2, 11.5)         # tail upper edge
        form.lineTo(-1.4, 12.2)                    # tail tip
        form.quadTo(2.8, 11.5, 5.6, 10.6)          # tail underside
        form.quadTo(8.0, 10.2, 11.0, 10.4)         # belly
        form.quadTo(16.0, 10.8, 21.0, 10.2)
        form.quadTo(24.6, 9.4, 26.4, 8.6)          # chest
        form.quadTo(28.4, 7.8, 29.2, 6.6)          # under-jaw
        form.closeSubpath()
        p.drawPath(form)
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(28.9, 6.3), 0.45, 0.4)   # nose
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(27.3, 6.1), 0.45, 0.45)  # eye
        self._walk_legs(p, c, onear, g, GND, 3.5, 1.4, L1, L2, width=1.4)

    def _draw_heron(self, p, wk):
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        hf = self._hold_frac(wk)
        c = _lerp("#77878F", "#243430", self._dark * 0.75)
        oneleg = hold and wk["pose"] == "oneleg"
        GND, L1, L2 = 23.6, 4.4, 4.8
        stride = 0.0 if hold else 3.4
        if oneleg:
            # The raised leg, folded up warm against the belly.
            tpen = QPen(_lerp(c.name(), _INK, 0.4), 1.0)
            tpen.setCapStyle(Qt.PenCapStyle.RoundCap)
            tpen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(tpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            tuck = QPainterPath(QPointF(14.6, 14.6))
            tuck.lineTo(QPointF(16.0, 17.2))
            tuck.lineTo(QPointF(13.9, 14.4))
            p.drawPath(tuck)
        else:
            # Deliberate high step: the foot dwells planted, then lifts
            # clear and reaches (duty 0.78 + the body's stalk-gate).
            self._walk_legs(p, _lerp(c.name(), _INK, 0.4),
                            [(14.3, 14.6, 0.5, +1)], g, GND, stride, 3.0,
                            L1, L2, alpha=0.9, width=1.0, duty=0.78)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        body = QPainterPath(QPointF(6, 13))
        body.quadTo(5, 10.6, 9, 10.2)
        body.quadTo(14, 9.6, 17.5, 11)
        body.quadTo(19, 12, 18, 14)
        body.quadTo(13, 16, 8, 14.6)
        body.closeSubpath()
        p.drawPath(body)
        tail = QPainterPath(QPointF(6.6, 12))
        tail.lineTo(3.2, 14.2)
        tail.lineTo(6.4, 14.4)
        tail.closeSubpath()
        p.drawPath(tail)
        if oneleg:                             # the single planted leg
            self._walk_legs(p, c, [(13.4, 14.8, 0.0, +1)], g, GND, 0.0,
                            0.0, L1, L2, width=1.25)
        else:
            self._walk_legs(p, c, [(12.8, 14.8, 0.0, +1)], g, GND, stride,
                            3.0, L1, L2, width=1.15, duty=0.78)
        pen = QPen(c, 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        strike = hold and wk["pose"] == "fish" and 0.5 < hf < 0.68
        if strike:
            # The dagger strike — neck unfolds down to the water.
            neck = QPainterPath(QPointF(16.5, 11.4))
            neck.cubicTo(19.5, 11.2, 21.5, 12.6, 23.0, 15.4)
            p.drawPath(neck)
            head = QPointF(23.2, 15.8)
        else:
            sway = 0.6 * math.sin(g * 0.8)
            neck = QPainterPath(QPointF(16.5, 11.4))
            neck.cubicTo(20 + sway, 10, 20.4 + sway, 6.4, 18.6 + sway, 4.6)
            neck.cubicTo(18.0 + sway, 3.9, 18.6 + sway, 3.2, 19.6 + sway, 3.0)
            p.drawPath(neck)
            head = QPointF(19.8 + sway, 2.9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(head, 1.6, 1.1)
        beak = QPainterPath(head + QPointF(1.2, -0.3))
        beak.lineTo(head + QPointF(5.4, 0.7))
        beak.lineTo(head + QPointF(1.2, 0.9))
        beak.closeSubpath()
        p.drawPath(beak)
        pen = QPen(c, 0.8)
        p.setPen(pen)
        p.drawLine(head + QPointF(-0.6, -0.8), head + QPointF(-2.2, -2.2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(head + QPointF(0.4, -0.3), 0.45, 0.45)
        if strike:
            splash = QColor(_CHALK)
            splash.setAlphaF(0.7)
            p.setBrush(splash)
            for sx, sy in ((22.4, 16.6), (24.2, 16.9), (23.4, 15.9)):
                p.drawEllipse(QPointF(sx, sy), 0.5, 0.5)
        # It surfaces with a silver fish crosswise in the bill, gives it a
        # wriggle, and swallows — sometimes it just comes up empty.
        if (hold and wk["pose"] == "fish" and wk.get("caught")
                and 0.68 <= hf < 0.86):
            fish_c = _lerp("#BCC7CF", "#5E6E78", self._dark)
            fx = head + QPointF(4.4, 0.4)
            p.save()
            p.translate(fx.x(), fx.y())
            p.rotate(14 * math.sin(self._t * 22))
            p.setBrush(fish_c)
            p.drawEllipse(QPointF(0, 0), 1.4, 0.62)
            ftail = QPainterPath(QPointF(1.2, 0))
            ftail.lineTo(2.2, -0.7)
            ftail.lineTo(2.2, 0.7)
            ftail.closeSubpath()
            p.drawPath(ftail)
            p.setBrush(QColor(_INK))
            p.drawEllipse(QPointF(-0.8, -0.1), 0.18, 0.18)
            p.restore()

    def _draw_kingfisher(self, p, wk):
        """Dumpy body merging straight into an oversized head, a dagger
        beak nearly as long as the bird, orange underparts and cheek, a
        bright cyan flash down the back, and a blurred whirring wing."""
        g = wk["gait"]
        blue = _lerp("#2F86C4", "#1E4B66", self._dark * 0.8)
        flash = _lerp("#5FD4E8", "#3A7E94", self._dark * 0.8)
        orange = _lerp(_BELLY, "#7A3A22", self._dark * 0.6)
        ink = QColor(_INK)
        p.setPen(Qt.PenStyle.NoPen)
        # Short square tail, a shade deeper than the back.
        p.setBrush(QColor(blue).darker(115))
        tail = QPainterPath(QPointF(2.4, 2.9))
        tail.lineTo(0.2, 2.6)
        tail.lineTo(0.0, 4.4)
        tail.lineTo(2.4, 4.4)
        tail.closeSubpath()
        p.drawPath(tail)
        # One plump form, no neck: tail root over the back to a big
        # crown, steep forehead, deep chest tucking back to the vent.
        p.setBrush(blue)
        body = QPainterPath(QPointF(1.6, 3.2))
        body.quadTo(4.0, 0.9, 8.6, 1.0)
        body.quadTo(10.6, 1.1, 11.3, 2.3)
        body.quadTo(11.5, 3.4, 10.8, 4.3)
        body.quadTo(9.6, 5.8, 7.2, 5.7)
        body.quadTo(4.5, 5.5, 2.6, 4.4)
        body.quadTo(1.6, 4.0, 1.6, 3.2)
        body.closeSubpath()
        p.drawPath(body)
        # Orange breast and belly.
        p.setBrush(orange)
        breast = QPainterPath(QPointF(10.8, 3.8))
        breast.quadTo(10.3, 5.4, 7.4, 5.65)
        breast.quadTo(4.6, 5.5, 2.8, 4.4)
        breast.lineTo(3.4, 4.0)
        breast.quadTo(7.0, 4.6, 10.8, 3.8)
        breast.closeSubpath()
        p.drawPath(breast)
        # Electric cyan stripe from nape to tail.
        pen = QPen(flash, 0.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        stripe = QPainterPath(QPointF(8.4, 2.0))
        stripe.quadTo(5.0, 2.3, 2.1, 3.5)
        p.drawPath(stripe)
        p.setPen(Qt.PenStyle.NoPen)
        # Orange cheek, white throat.
        p.setBrush(orange)
        p.drawEllipse(QPointF(10.2, 2.85), 0.8, 0.62)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(10.85, 4.3), 0.55, 0.42)
        # The dagger: a filled spike, not a line.
        p.setBrush(ink)
        beak = QPainterPath(QPointF(10.9, 2.15))
        beak.lineTo(15.4, 3.05)
        beak.lineTo(10.9, 3.45)
        beak.closeSubpath()
        p.drawPath(beak)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(10.4, 2.1), 0.38, 0.38)   # eye
        # Whirring wing over the shoulder: a blurred ghost of the
        # previous beat behind the live one sells the buzz.
        for phase, op in ((g - 1.3, 0.28), (g, 1.0)):
            p.save()
            p.translate(6.6, 2.6)
            p.rotate(38 * math.sin(phase))
            p.setOpacity(op)
            wing = QPainterPath(QPointF(0.4, -0.3))
            wing.quadTo(-2.2, 0.1, -3.9, 2.3)
            wing.quadTo(-1.8, 1.6, 0.2, 1.1)
            wing.closeSubpath()
            p.setBrush(QColor(blue).darker(125))
            p.drawPath(wing)
            p.restore()
        p.setOpacity(1.0)

    def _draw_pheasant(self, p, wk):
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        body_c = _lerp("#8A4A22", _INK, self._dark * 0.8)
        head_c = _lerp("#1C3A2E", _INK, self._dark * 0.5)
        psw = 22 if not hold else 4
        leap = wk.get("_leap", 0.0)
        if leap <= 0.15:
            self._leg_joint(p, _lerp(body_c.name(), _INK, 0.45),
                            ((15.9, 12.0, math.pi),), 1.7, 1.7, g, psw,
                            bend_deg=24, ks=1, alpha=0.9, width=1.2,
                            a_off=-8, foot=0.8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body_c)
        # Teardrop body flowing straight into the long pointed tail.
        body = QPainterPath(QPointF(19.8, 8.0))
        body.quadTo(20.6, 10.2, 18.4, 11.5)      # keel
        body.quadTo(15.0, 12.8, 12.0, 11.6)      # underside
        body.quadTo(9.8, 10.6, 9.8, 8.8)         # vent
        body.lineTo(0.8, 6.7)                    # tail lower edge
        body.lineTo(1.2, 5.9)                    # the point
        body.lineTo(10.2, 7.0)                   # tail upper edge
        body.quadTo(11.0, 6.2, 13.0, 6.2)        # rump
        body.quadTo(17.0, 5.8, 19.8, 8.0)        # breast
        body.closeSubpath()
        p.drawPath(body)
        bar = QPen(QColor(_INK), 0.45)           # tail barring
        bar.setCapStyle(Qt.PenCapStyle.RoundCap)
        barc = QColor(_INK)
        barc.setAlphaF(0.5)
        bar.setColor(barc)
        p.setPen(bar)
        p.drawLine(QPointF(3.6, 6.1), QPointF(3.4, 6.9))
        p.drawLine(QPointF(6.4, 6.4), QPointF(6.2, 7.5))
        p.setPen(Qt.PenStyle.NoPen)
        if leap > 0.15:
            # Burst flight over the water: legs tucked, wing whirring.
            pen = QPen(body_c, 1.1)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(14.2, 11.6), QPointF(12.8, 12.3))
            p.drawLine(QPointF(15.6, 11.7), QPointF(14.2, 12.5))
            p.setPen(Qt.PenStyle.NoPen)
            for ph, op in ((0.0, 1.0), (math.pi * 0.5, 0.55)):
                p.save()
                p.translate(14.5, 8.0)
                p.rotate(42 * math.sin(g * 3 + ph))
                wing = QPainterPath(QPointF(0, 0))
                wing.quadTo(-2.0, -3.4, -5.2, -4.6)   # rounded leading edge
                wing.quadTo(-7.2, -5.1, -8.2, -4.2)   # blunt tip
                wing.quadTo(-5.0, -1.6, -2.2, -0.2)   # trailing curve
                wing.quadTo(-1.0, 0.4, 0, 0.8)
                wing.closeSubpath()
                p.setOpacity(op)
                p.setBrush(body_c)
                p.drawPath(wing)
                p.restore()
            p.setOpacity(1.0)
        else:
            self._leg_joint(p, body_c, ((14.2, 12.1, 0),), 1.7, 1.7, g,
                            psw, bend_deg=24, ks=1, width=1.2, a_off=-8,
                            foot=0.8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body_c)
        # Head bobs forward with each step; when paused it pecks the ground.
        p.save()
        if hold and wk["pose"] == "peck":
            peck = max(0.0, math.sin(g * 4)) * 5.0
            p.translate(1.6 + 0.3 * peck, peck)
        elif hold:
            p.translate(0.0, 0.0)
        else:
            # The characteristic ground-bird head-bob: the head holds
            # steady in space (drifting back in body frame) then darts
            # forward to catch up — a sawtooth, twice per stride.
            u = (g / math.pi) % 1.0
            if u < 0.65:
                jab = 1.9 * (0.65 - u) / 0.65
            else:
                jab = 1.9 * (u - 0.65) / 0.35
            p.translate(jab, 0)
        p.setBrush(head_c)
        neck = QPainterPath(QPointF(18.2, 7.4))
        neck.quadTo(19.6, 6.4, 20.0, 4.8)
        neck.lineTo(21.4, 5.2)
        neck.quadTo(20.8, 7, 19.6, 8.2)
        neck.closeSubpath()
        p.drawPath(neck)
        p.drawEllipse(QPointF(20.9, 4.4), 1.5, 1.2)
        beak = QPainterPath(QPointF(22.2, 4.1))
        beak.lineTo(23.6, 4.5)
        beak.lineTo(22.2, 4.9)
        beak.closeSubpath()
        p.drawPath(beak)
        pen = QPen(QColor(_CHALK), 0.8)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        ring = QPainterPath(QPointF(19.4, 5.6))
        ring.quadTo(20.4, 6.2, 21.4, 5.8)
        p.drawPath(ring)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_BELLY))
        p.drawEllipse(QPointF(20.6, 4.2), 0.55, 0.55)
        p.restore()

    def _draw_badger_curl(self, p, wk, cx, cy):
        """The badger curled in its den: grizzled grey curl with the
        dark underside band tucked round, and the striped head laid
        large across the top — the white blaze and both black eye
        stripes running crown to snout — so it reads instantly as a
        badger."""
        g = wk["gait"]
        grizzle = _lerp("#75796F", _INK, self._dark * 0.55)
        ink = QColor(_INK)
        breathe = 0.12 * math.sin(g * 0.55)
        p.save()
        p.translate(cx, cy)
        p.setPen(Qt.PenStyle.NoPen)
        # Short grizzled tail tucked at the rump.
        p.setBrush(grizzle)
        p.drawEllipse(QPointF(-3.6, 2.4), 1.25, 0.9)
        # The curled body, breathing.
        body = QPointF(0.0, -0.1)
        p.drawEllipse(body, 4.15, 3.8 + breathe)
        # Dark underside band, tucked round the lower flank.
        p.save()
        clipb = QPainterPath()
        clipb.addEllipse(body, 4.15, 3.8 + breathe)
        p.setClipPath(clipb, Qt.ClipOperation.IntersectClip)
        p.setBrush(ink)
        p.drawEllipse(QPointF(0.3, 2.7), 3.9, 1.9)
        p.restore()
        # Ear at the rear crown, white-tipped.
        p.setBrush(ink)
        p.drawEllipse(QPointF(-0.9, -3.0 - breathe), 0.7, 0.7)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(-1.05, -3.25 - breathe), 0.32, 0.32)
        # The head: a white wedge laid across the top of the curl,
        # nose down by the brush of the flank.
        hd = QPainterPath(QPointF(-1.2, -2.9 - breathe))
        hd.quadTo(1.4, -4.0 - breathe, 3.3, -2.2)
        hd.quadTo(4.6, -1.0, 5.1, 0.6)
        hd.quadTo(3.4, 1.5, 1.6, 0.9)
        hd.quadTo(-0.6, 0.1, -1.2, -2.9 - breathe)
        hd.closeSubpath()
        p.drawPath(hd)
        # Both black stripes, crown to snout.
        p.setBrush(ink)
        st1 = QPainterPath(QPointF(-0.7, -3.1 - breathe))
        st1.quadTo(1.6, -3.6 - breathe, 3.2, -2.0)
        st1.quadTo(4.0, -1.1, 4.5, -0.1)
        st1.quadTo(3.6, -0.35, 2.6, -1.2)
        st1.quadTo(1.1, -2.5, -0.7, -3.1 - breathe)
        st1.closeSubpath()
        p.drawPath(st1)
        st2 = QPainterPath(QPointF(-0.1, -1.35))
        st2.quadTo(1.8, -1.8, 3.3, -0.7)
        st2.quadTo(4.1, -0.1, 4.65, 0.5)
        st2.quadTo(3.3, 0.95, 2.2, 0.5)
        st2.quadTo(0.85, -0.05, -0.1, -1.35)
        st2.closeSubpath()
        p.drawPath(st2)
        p.drawEllipse(QPointF(5.0, 0.6), 0.5, 0.45)      # nose
        # Eye shut tight inside the lower stripe.
        p.setPen(QPen(QColor(_CHALK), 0.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        eye = QPainterPath(QPointF(2.3, -0.75))
        eye.quadTo(2.85, -0.45, 3.4, -0.6)
        p.drawPath(eye)
        p.setPen(Qt.PenStyle.NoPen)
        p.restore()

    def _draw_badger(self, p, wk):
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        p.save()
        curl = wk.get("_curl", 0.0)
        if curl > 0.001 and wk.get("_den"):
            # Slip into the den: ball fades in gliding up to the den
            # point, standing badger fades out after it — a visible
            # curl-up, never a single-frame swap. (Reverse on waking.)
            dxl, dyl = wk["_den"]
            uu = curl * curl * (3.0 - 2.0 * curl)
            sxc, syc = wk["w"] * 0.45, wk["h"] * 0.55
            op = p.opacity()
            p.setOpacity(op * uu)
            self._draw_badger_curl(p, wk, sxc + (dxl - sxc) * uu,
                                   syc + (dyl - syc) * uu)
            if curl >= 0.999:
                p.restore()
                return
            p.setOpacity(op * (1.0 - uu))
            p.translate((dxl - sxc) * uu, (dyl - syc) * uu)
            p.translate(sxc, syc)
            p.scale(1.0 - 0.3 * uu, 1.0 - 0.3 * uu)
            p.translate(-sxc, -syc)
        if hold and wk["pose"] == "snuffle":
            # Nose-down snuffling: the whole badger — legs included —
            # pivots at the haunches, so the body never lifts off its legs.
            p.translate(5, 10.4)
            p.rotate(4.5 + 1.8 * math.sin(g * 6))
            p.translate(-5, -10.4)
        grizzle = _lerp("#75796F", _INK, self._dark * 0.55)  # grey coat
        ink = QColor(_INK)
        # A low, rolling waddle: short IK legs in a 4-beat, a small body
        # roll over the stride.
        GND, L1, L2 = 14.7, 2.2, 2.2
        stride = 0.0 if hold else 3.8
        far = [(9.0, 10.6, 0.25, -1), (20.0, 10.6, 0.75, +1)]
        near = [(6.5, 10.8, 0.0, -1), (23.0, 10.8, 0.5, +1)]
        self._walk_legs(p, ink, far, g, GND, stride, 1.4, L1, L2,
                        alpha=0.55, width=1.5)
        bob = 0.0 if hold else self._walk_bob(g, 0.25)
        p.save()
        p.translate(0, bob)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grizzle)
        # Low-slung pear: highest over the haunches, easing to the snout.
        body = QPainterPath(QPointF(3.6, 11.0))
        body.quadTo(2.4, 8.2, 5.0, 6.0)
        body.quadTo(8.0, 4.0, 12.5, 3.9)
        body.quadTo(18.0, 3.9, 22.0, 5.2)
        body.quadTo(25.4, 6.2, 26.4, 8.0)
        body.lineTo(26.2, 11.0)
        body.closeSubpath()
        p.drawPath(body)
        tail = QPainterPath(QPointF(4.0, 8.4))    # short grizzled tail
        tail.quadTo(2.0, 9.0, 2.4, 10.6)
        tail.quadTo(3.4, 10.2, 4.4, 9.6)
        tail.closeSubpath()
        p.drawPath(tail)
        # Black underside band — badgers are dark below, pale above.
        p.setBrush(ink)
        belly = QPainterPath(QPointF(4.2, 11.0))
        belly.quadTo(4.8, 9.0, 7.5, 8.8)
        belly.quadTo(15.0, 8.4, 22.0, 8.8)
        belly.quadTo(25.2, 9.0, 26.2, 11.0)
        belly.closeSubpath()
        p.drawPath(belly)
        p.drawEllipse(QPointF(26.6, 4.4), 0.95, 0.95)  # ear
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(26.8, 4.1), 0.4, 0.4)    # white ear tip
        face = QPainterPath(QPointF(25.5, 4.5))
        face.quadTo(30.5, 3, 34.5, 6.8)
        face.quadTo(32.5, 9.6, 27.5, 9.2)
        face.quadTo(24.8, 8.8, 25, 6.5)
        face.closeSubpath()
        p.setBrush(QColor(_CHALK))
        p.drawPath(face)
        stripe = QPainterPath(QPointF(27, 4.6))
        stripe.quadTo(31, 5, 33.6, 6.6)
        stripe.quadTo(32, 8, 29.5, 7.6)
        stripe.quadTo(27.5, 7, 26.8, 5.6)
        stripe.closeSubpath()
        p.setBrush(ink)
        p.drawPath(stripe)
        p.drawEllipse(QPointF(34.2, 6.8), 0.9, 0.9)
        p.restore()                                    # end body roll
        self._walk_legs(p, ink, near, g, GND, stride, 1.4, L1, L2,
                        width=1.6)
        p.restore()

    def _draw_hog(self, p, wk):
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        # Storybook colouring like the fox: brown spiny coat over a cream
        # face and belly, all lerping to silhouette-ink as night falls.
        dark = self._dark * 0.8
        coat = _lerp("#6B4A2E", _INK, dark)     # mid-brown spiny coat
        spike = _lerp("#452E1B", _INK, dark)    # darker spine texture
        cream = _lerp("#F1DDB6", _INK, dark)    # face + belly
        tan = _lerp("#DCBC8C", _INK, dark)      # legs + ear
        ink = QColor(_INK)

        def spiky(cx, cy, rx, ry, a0, a1, n, tip=1.26, lean=6.0):
            # Jagged rim: bases on the (rx, ry) ellipse, spike tips pushed
            # out to tip x radius and leaning back toward the rump.
            def pt(deg, s=1.0):
                a = math.radians(deg)
                return QPointF(cx + rx * s * math.cos(a),
                               cy - ry * s * math.sin(a))
            path = QPainterPath(pt(a0))
            for i in range(n):
                mid = a0 + (a1 - a0) * (i + 0.5) / n
                path.lineTo(pt(mid + lean, tip))
                path.lineTo(pt(a0 + (a1 - a0) * (i + 1.0) / n))
            return path

        if hold and wk["pose"] == "ball":
            # Curled tight: a bristling conker, spikes all the way round.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(coat))
            cx, cy = 12.5, 10.0
            ball = spiky(cx, cy, 5.4, 4.4, -26, 206, 16, tip=1.32)
            ball.quadTo(cx, cy + 3.6, cx + 4.8, cy + 1.9)
            ball.closeSubpath()
            p.drawPath(ball)
            p.setPen(QPen(QColor(spike), 0.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(11):
                a = math.radians(-16 + 212 * i / 10)
                p.drawLine(QPointF(cx + 2.8 * math.cos(a),
                                   cy - 2.3 * math.sin(a)),
                           QPointF(cx + 4.9 * math.cos(a),
                                   cy - 4.0 * math.sin(a)))
            return
        # A busy little scurry on short IK legs, the dome bustling.
        GND, L1, L2 = 13.7, 1.6, 1.6
        stride = 2.8
        far = [(9.5, 10.8, 0.25, -1), (19.5, 10.8, 0.75, +1)]
        near = [(7.2, 11.0, 0.0, -1), (21.5, 11.0, 0.5, +1)]
        self._walk_legs(p, tan, far, g, GND, stride, 1.0, L1, L2,
                        alpha=0.55, width=1.3)
        p.save()
        p.translate(0, self._walk_bob(g, 0.18))
        p.setPen(Qt.PenStyle.NoPen)
        # Cream body first: pointed snout, round chin, low belly.
        p.setBrush(QColor(cream))
        body = QPainterPath(QPointF(4.2, 12.6))
        body.quadTo(2.8, 9.2, 6.2, 6.2)               # rump (under the coat)
        body.quadTo(12.0, 3.4, 18.2, 5.4)             # back (under the coat)
        body.quadTo(21.4, 6.4, 23.4, 8.2)             # brow
        body.quadTo(25.4, 9.4, 26.9, 10.4)            # snout, tipped down
        body.quadTo(25.8, 12.0, 23.2, 12.3)           # chin
        body.quadTo(15.0, 13.2, 6.6, 12.9)            # belly
        body.closeSubpath()
        p.drawPath(body)
        # The spiny coat: one spiky mass thrown over the back like a
        # cloak, front edge sweeping down behind the ear; the cream
        # belly stays visible beneath its hem.
        cx, cy, rx, ry = 11.2, 12.0, 8.4, 8.0
        coat_path = QPainterPath(QPointF(18.7, 11.6))   # front lower corner
        coat_path.lineTo(QPointF(cx + rx * math.cos(math.radians(33)),
                                 cy - ry * math.sin(math.radians(33))))
        coat_path.connectPath(spiky(cx, cy, rx, ry, 33, 173, 12))
        coat_path.quadTo(2.6, 12.2, 5.4, 12.4)          # rear drop
        coat_path.quadTo(11.5, 11.9, 18.7, 11.6)        # hem over the belly
        coat_path.closeSubpath()
        p.setBrush(QColor(coat))
        p.drawPath(coat_path)
        # Darker spine strokes for texture inside the dome.
        p.setPen(QPen(QColor(spike), 0.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(9):
            a = math.radians(38 + 132 * i / 8 + 6)
            p.drawLine(QPointF(cx + 0.52 * rx * math.cos(a - 0.1),
                               cy - 0.52 * ry * math.sin(a - 0.1)),
                       QPointF(cx + 0.92 * rx * math.cos(a),
                               cy - 0.92 * ry * math.sin(a)))
        p.setPen(Qt.PenStyle.NoPen)
        # Ear on the face, tucked against the coat's edge.
        p.setBrush(QColor(tan))
        p.drawEllipse(QPointF(20.7, 7.3), 1.0, 1.05)
        p.setBrush(QColor(spike))
        p.drawEllipse(QPointF(20.9, 7.4), 0.5, 0.55)
        # Eye — big, dark, with a glint.
        p.setBrush(ink)
        p.drawEllipse(QPointF(23.0, 8.9), 0.8, 0.8)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(23.25, 8.65), 0.28, 0.28)
        # Button nose on the snout tip, with its own glint.
        p.setBrush(ink)
        p.drawEllipse(QPointF(26.9, 10.4), 0.85, 0.8)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(27.1, 10.15), 0.26, 0.26)
        # A hint of a smile.
        p.setPen(QPen(ink, 0.32, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        smile = QPainterPath(QPointF(24.4, 11.5))
        smile.quadTo(25.3, 12.0, 26.2, 11.5)
        p.drawPath(smile)
        p.restore()                                        # end bustle
        self._walk_legs(p, tan, near, g, GND, stride, 1.0, L1, L2,
                        width=1.4)

    def _draw_hare(self, p, wk):
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        ink = QColor(_INK)
        # Warm hare-brown coat; legs and ear tips darker (black tips are
        # the brown hare's field mark).
        coat = _lerp("#8A6B45", _INK, self._dark * 0.75)
        leg_c = _lerp(coat.name(), _INK, 0.3)
        if hold:
            # Sat bolt upright: big haunch on the ground, S-curved back,
            # small head under long splayed ears, forepaws to the front.
            boxing = wk["pose"] == "boxing"
            if boxing:
                p.translate(0, -1.8 * abs(math.sin(g * 1.5)))
                p.rotate(3 * math.sin(g * 3))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(coat)
            haunch = QPainterPath(QPointF(7.2, 15.4))
            haunch.quadTo(5.4, 11.4, 7.8, 9.0)
            haunch.quadTo(10.2, 7.0, 12.6, 8.8)
            haunch.quadTo(14.6, 10.6, 14.3, 15.4)
            haunch.closeSubpath()
            p.drawPath(haunch)
            torso = QPainterPath(QPointF(9.4, 10.6))
            torso.quadTo(9.5, 6.6, 11.3, 4.4)     # back curve up
            torso.quadTo(12.3, 3.3, 13.6, 3.9)
            torso.quadTo(14.9, 6.2, 14.5, 10.8)   # chest line
            torso.quadTo(12.2, 12.2, 9.4, 10.6)
            torso.closeSubpath()
            p.drawPath(torso)
            p.drawEllipse(QPointF(13.2, 3.1), 1.9, 1.6)   # head
            muzz = QPainterPath(QPointF(14.7, 2.7))       # little muzzle
            muzz.lineTo(16.1, 3.3)
            muzz.lineTo(14.8, 3.8)
            muzz.closeSubpath()
            p.drawPath(muzz)
            for rx0, tip in ((12.1, -1.0), (13.6, 1.0)):  # splayed ears
                ear = QPainterPath(QPointF(rx0, 1.9))
                ear.quadTo(rx0 + tip * 0.4 - 0.6, -1.0,
                           rx0 + tip - 0.4, -3.1)
                ear.quadTo(rx0 + tip + 0.4, -1.2, rx0 + 1.2, 1.6)
                ear.closeSubpath()
                p.drawPath(ear)
                p.setBrush(ink)                           # black ear tip
                p.drawEllipse(QPointF(rx0 + tip - 0.35, -2.5), 0.5, 0.75)
                p.setBrush(coat)
            pen = QPen(leg_c, 1.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            if boxing:
                jab = 2.2 * max(0.0, math.sin(g * 3))
                p.drawLine(QPointF(14.0, 6.2), QPointF(16.4 + jab, 5.0))
                p.drawLine(QPointF(13.8, 7.4), QPointF(15.8 + jab * 0.6,
                                                       6.8))
            else:
                # forepaws resting to the ground in front
                p.drawLine(QPointF(13.8, 11.2), QPointF(14.6, 15.2))
                p.drawLine(QPointF(14.6, 11.2), QPointF(15.5, 15.2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(_CHALK))
            p.drawEllipse(QPointF(14.0, 2.7), 0.55, 0.55)  # eye
            p.drawEllipse(QPointF(6.6, 13.2), 1.0, 1.0)    # bob tail
            return
        hop = abs(math.sin(g))                   # leg-fold: tight near both
        air = max(0.0, math.sin(g))              # swing extremes
        # ONE apex per stride: the body rises only through the airborne
        # suspension, in step with the stretch below — not the old
        # abs(sin) double-bob that desynced from the footfalls.
        squash = max(0.0, -math.sin(g))          # ground-contact gather half
        crouch = squash                          # roach + dip only on contact
        p.save()
        p.translate(13, 9 - 4.4 * air + 1.4 * squash)
        p.rotate(-11 * math.sin(g + 0.4))        # nose-up launch, down landing
        p.translate(-13, -9)
        # Stretch out in the air, gather on the ground.
        p.translate(13, 9)
        p.scale(1.0 + 0.11 * air - 0.05 * squash,
                1.0 - 0.05 * air + 0.03 * squash)
        p.translate(-13, -9)
        # True bound: both hinds drive together, both fronts land
        # together a beat later; legs fold tight in the air. Hinds swing
        # wide so they throw forward past the fore contact at full stretch.
        fold = 10 + 34 * hop
        ffold = 8 + 12 * hop
        far_c = _lerp(coat.name(), _INK, 0.5)
        self._leg_joint(p, far_c, ((9.0, 11.6, -0.35),), 2.2, 2.4, g, 52,
                        bend_deg=fold, alpha=0.5, a_off=-16, width=1.45,
                        foot=1.0)
        self._leg_joint(p, far_c, ((17.4, 11.4, 2.75),), 1.7, 1.7, g, 26,
                        bend_deg=ffold, alpha=0.5, a_off=10, width=1.3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(coat)
        # Lean coursing silhouette: high round rump, deep narrow chest,
        # tucked-up belly. The spine is dorsomobile — the back roaches up
        # and the belly draws under as the hindfeet gather (crouch), then
        # hollows out flat as the animal launches into suspension.
        arch = 1.7 * crouch                      # mid-back humps when gathered
        tuck = 0.9 * crouch                      # belly draws up to meet it
        body = QPainterPath(QPointF(5.6, 11.6))
        body.quadTo(4.4, 9.0, 6.4, 7.2)
        body.quadTo(9.0, 5.4 - arch, 12.5, 5.6 - arch)
        body.quadTo(16.5, 5.2 - arch * 0.6, 19.6, 6.2)
        body.quadTo(21.8, 7.0, 22.2, 8.8)
        body.quadTo(21.6, 10.4, 19.2, 10.8)
        body.quadTo(15.0, 11.8 - tuck, 10.5, 11.6 - tuck)
        body.quadTo(7.6, 11.9 - tuck * 0.5, 5.6, 11.6)
        body.closeSubpath()
        p.drawPath(body)
        p.drawEllipse(QPointF(21.6, 6.4), 2.1, 1.7)       # small head
        muzzle = QPainterPath(QPointF(23.0, 5.8))
        muzzle.quadTo(24.4, 6.2, 24.6, 6.9)
        muzzle.quadTo(23.6, 7.4, 22.6, 7.2)
        muzzle.closeSubpath()
        p.drawPath(muzzle)
        # Long ears laid flat along the back at speed, black-tipped.
        pen = QPen(coat, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(21.0, 5.2), QPointF(16.6, 2.6))
        p.drawLine(QPointF(22.0, 4.8), QPointF(18.2, 1.7))
        pen = QPen(ink, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(17.7, 3.25), QPointF(16.6, 2.6))
        p.drawLine(QPointF(19.2, 2.5), QPointF(18.2, 1.7))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(22.3, 5.9), 0.55, 0.55)     # eye
        p.drawEllipse(QPointF(5.4, 9.2), 1.1, 1.1)        # bob tail
        self._leg_joint(p, leg_c, ((7.8, 11.7, 0),), 2.3, 2.5, g, 52,
                        bend_deg=fold, a_off=-16, width=1.6, foot=1.1)
        self._leg_joint(p, leg_c, ((18.4, 11.5, 2.4),), 1.7, 1.8, g, 26,
                        bend_deg=ffold, a_off=10, width=1.45)
        p.restore()

    def _draw_fox_curl(self, p, wk, cx, cy):
        """The fox curled in its den, Firefox-wise: the brush wraps
        right round the OUTSIDE of the curl — rump, under the body, up
        past the chin — with the white tip riding up by the cheek, and
        the face laid large on top with big pricked ears, so it reads
        as a fox and not a round ball with an eye."""
        g = wk["gait"]
        d8 = self._dark * 0.8
        c = _lerp("#B0512A", _INK, d8)          # coat
        deep = _lerp("#8A3C1D", _INK, d8)       # shaded flank, ear backs
        cream = _lerp("#F1DDB6", _INK, d8)      # muzzle and cheek
        breathe = 0.12 * math.sin(g * 0.6)
        p.save()
        p.translate(cx, cy)
        p.setPen(Qt.PenStyle.NoPen)
        # The curled flank — a touch darker than the brush so the tail
        # wrap reads against it — rising softly with the breath.
        p.setBrush(deep)
        p.drawEllipse(QPointF(0.0, -0.1), 4.05, 3.75 + breathe)
        # The brush: a thick tapered crescent hugging the outside.
        p.setBrush(c)
        tail = QPainterPath(QPointF(-1.6, -3.5))
        tail.quadTo(-5.3, -2.9, -5.4, 0.3)          # outer edge, rump…
        tail.quadTo(-5.4, 3.4, -1.9, 4.6)           # …round the bottom
        tail.quadTo(1.6, 5.6, 4.7, 3.5)             # …up toward the chin
        tail.quadTo(5.6, 2.8, 5.3, 1.9)             # tip cap
        tail.quadTo(4.2, 2.2, 3.0, 2.9)             # inner edge back
        tail.quadTo(0.4, 4.0, -1.8, 3.0)
        tail.quadTo(-3.7, 2.1, -3.7, 0.2)
        tail.quadTo(-3.7, -1.9, -1.6, -3.5)
        tail.closeSubpath()
        p.drawPath(tail)
        # White tip, finishing up under the cheek.
        tip = QPainterPath(QPointF(2.0, 4.35))
        tip.quadTo(3.6, 4.35, 4.7, 3.5)
        tip.quadTo(5.6, 2.8, 5.3, 1.9)
        tip.quadTo(4.2, 2.2, 3.0, 2.9)
        tip.quadTo(2.4, 3.4, 2.0, 4.35)
        tip.closeSubpath()
        p.setBrush(QColor(_CHALK))
        p.drawPath(tip)
        # Big pricked ears behind the crown, dark-backed.
        p.setBrush(deep)
        for ex, tilt in ((-0.7, -0.5), (1.4, 0.4)):
            ear = QPainterPath(QPointF(ex, -2.6 - breathe))
            ear.lineTo(ex + 0.9 + tilt, -5.2 - breathe)
            ear.lineTo(ex + 1.9, -2.8 - breathe)
            ear.closeSubpath()
            p.drawPath(ear)
        # The head, laid over the curl, brighter than the flank.
        p.setBrush(c)
        head = QPainterPath(QPointF(-1.3, -2.3 - breathe))
        head.quadTo(1.2, -3.9 - breathe, 3.0, -2.3 - breathe)
        head.quadTo(4.3, -1.2, 4.9, 0.6)
        head.quadTo(2.6, 1.6, 0.6, 1.0)
        head.quadTo(-1.3, 0.2, -1.3, -2.3 - breathe)
        head.closeSubpath()
        p.drawPath(head)
        # Cream muzzle and cheek, chin resting on the white brush tip.
        muz = QPainterPath(QPointF(1.4, -0.5))
        muz.quadTo(3.0, -1.2, 4.9, 0.6)
        muz.quadTo(3.4, 1.9, 1.4, 1.15)
        muz.quadTo(0.7, 0.3, 1.4, -0.5)
        muz.closeSubpath()
        p.setBrush(cream)
        p.drawPath(muz)
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(4.75, 0.5), 0.5, 0.45)     # nose
        p.setPen(QPen(QColor(_INK), 0.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        eye = QPainterPath(QPointF(1.7, -1.15))          # eye shut tight
        eye.quadTo(2.4, -0.7, 3.1, -1.05)
        p.drawPath(eye)
        p.restore()

    def _draw_fox(self, p, wk):
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        hf = self._hold_frac(wk)
        c = _lerp("#B0512A", _INK, self._dark * 0.8)
        p.save()
        curl = wk.get("_curl", 0.0)
        if curl > 0.001 and wk.get("_den"):
            # Slip into the den: the curled fox fades in gliding from
            # the body's centre up to the den point while the standing
            # fox fades out shrinking after it — a visible curl-up,
            # never a single-frame swap. (Reverse on waking.)
            dxl, dyl = wk["_den"]
            uu = curl * curl * (3.0 - 2.0 * curl)
            sxc, syc = wk["w"] * 0.45, wk["h"] * 0.55
            op = p.opacity()
            p.setOpacity(op * uu)
            self._draw_fox_curl(p, wk, sxc + (dxl - sxc) * uu,
                                syc + (dyl - syc) * uu)
            if curl >= 0.999:
                p.restore()
                return
            p.setOpacity(op * (1.0 - uu))
            p.translate((dxl - sxc) * uu, (dyl - syc) * uu)
            p.translate(sxc, syc)
            p.scale(1.0 - 0.3 * uu, 1.0 - 0.3 * uu)
            p.translate(-sxc, -syc)
        if hold and wk["pose"] == "pounce":
            # The mouse pounce: crouch, spring, nose-dive.
            if hf < 0.35:
                t = hf / 0.35
                p.translate(0, 0.8 * t)
                p.rotate(3 * t)
            elif hf < 0.75:
                t = (hf - 0.35) / 0.40
                p.translate(2.5 * t, -7 * math.sin(math.pi * t))
                p.rotate(3 - 18 * math.sin(math.pi * min(1.0, t * 1.15)))
            else:
                p.translate(2.5, 0.6)
                p.rotate(4)
        elif hold and wk["pose"] == "sniff":
            # Nose along the ground, quartering for scent — the whole fox
            # tips at the haunches so legs and body stay together.
            p.translate(9, 12)
            p.rotate(5.5 + 2.0 * math.sin(g * 5))
            p.translate(-9, -12)
        sock = _lerp("#3A2A22", _INK, self._dark * 0.5)   # black stockings
        # A brisk 4-beat trot-walk: IK legs plant and lift, hind and fore
        # a quarter-stride apart, the far pair fainter behind.
        GND, L1, L2 = 14.9, 2.5, 2.3
        stride = 0.0 if hold else 4.6
        far = [(12.6, 10.6, 0.25, -1), (21.0, 10.6, 0.75, +1)]
        near = [(10.0, 10.8, 0.0, -1), (23.2, 10.8, 0.5, +1)]
        self._walk_legs(p, sock, far, g, GND, stride, 1.8, L1, L2,
                        alpha=0.7, width=1.25)
        bob = 0.0 if hold else self._walk_bob(g, 0.3)
        p.save()
        p.translate(0, bob)
        # The brush: long, low-slung, white-tipped with a slight up-curl,
        # its pivot buried in the rump so it never detaches.
        p.save()
        p.translate(8.6, 8.6)
        p.rotate(3 + 4 * math.sin(g * 0.5))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        brush = QPainterPath(QPointF(0.6, -1.0))
        brush.quadTo(-3.5, -1.8, -6.8, 0.2)
        brush.quadTo(-8.6, 1.4, -8.0, 2.6)
        brush.quadTo(-5.0, 3.0, -2.0, 1.9)
        brush.quadTo(-0.5, 1.3, 0.6, 0.9)
        brush.closeSubpath()
        p.drawPath(brush)
        p.setBrush(QColor(_CHALK))
        tip = QPainterPath(QPointF(-6.2, 0.4))
        tip.quadTo(-8.6, 1.4, -8.0, 2.6)
        tip.quadTo(-6.6, 2.8, -5.6, 2.0)
        tip.closeSubpath()
        p.drawPath(tip)
        p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        # Slender body: rounded rump, level back, deep chest, waist tuck.
        body = QPainterPath(QPointF(7.4, 10.8))
        body.quadTo(6.6, 8.4, 8.6, 7.2)
        body.quadTo(12.0, 6.2, 16.0, 6.5)
        body.quadTo(19.5, 6.4, 22.0, 7.0)
        body.quadTo(24.6, 7.8, 25.0, 10.0)
        body.quadTo(24.0, 11.6, 21.6, 11.4)
        body.quadTo(17.0, 10.6, 13.0, 10.9)
        body.quadTo(9.8, 11.3, 7.4, 10.8)
        body.closeSubpath()
        p.drawPath(body)
        # Wedge head: flat crown, long thin muzzle, big pricked ears.
        head = QPainterPath(QPointF(22.6, 7.2))
        head.quadTo(25.4, 5.6, 27.2, 5.8)
        head.lineTo(31.6, 7.9)
        head.quadTo(28.4, 9.3, 25.6, 9.2)
        head.quadTo(23.6, 9.0, 22.6, 8.2)
        head.closeSubpath()
        p.drawPath(head)
        for ex in (24.4, 26.4):
            ear = QPainterPath(QPointF(ex, 6.4))
            ear.lineTo(ex + 0.8, 3.2)
            ear.lineTo(ex + 2.0, 6.0)
            ear.closeSubpath()
            p.drawPath(ear)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(23.6, 9.6), 1.5, 1.1)       # white bib
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(31.2, 7.85), 0.6, 0.55)     # nose
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(27.5, 6.9), 0.5, 0.5)       # eye
        p.restore()                                        # end body bob
        self._walk_legs(p, sock, near, g, GND, stride, 1.8, L1, L2,
                        width=1.4)
        p.restore()

    def _deer_shading(self, p, body, coat):
        """Break up the flat coat: the pale caudal rump patch every red
        deer carries, a lighter underside, and a darker dorsal line down
        the back — all clipped inside the body outline, all converging
        on silhouette-ink after dark — plus the little tail dropped over
        the rump patch."""
        d8 = self._dark * 0.8
        p.save()
        p.setClipPath(body, Qt.ClipOperation.IntersectClip)
        p.setPen(Qt.PenStyle.NoPen)
        # Rump patch: a narrow crescent hugging the rear contour — kept
        # slim and only a shade paler, or the rear reads as a log end.
        p.setBrush(_lerp("#D2AF7E", _INK, d8))
        p.drawEllipse(QPointF(4.4, 10.9), 1.45, 2.7)
        p.setBrush(_lerp("#B58A5E", _INK, d8))       # paler underside
        p.drawEllipse(QPointF(11.8, 14.2), 7.6, 2.4)
        p.setBrush(_lerp("#5E3A20", _INK, d8))       # dark dorsal line
        p.drawEllipse(QPointF(12.0, 5.75), 6.2, 1.15)
        p.restore()

    def _draw_deer(self, p, wk):
        """A red deer traced from a reference silhouette. The body outline
        (_DEER_BODY) is drawn as a filled polygon; the lower legs are
        articulated IK cannons so it can walk, dropping from a haunch and
        a shoulder. Grazing drops the head to the sward."""
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        # Dimorphic coats: stags a darker russet, hinds a warmer chestnut.
        coat = _lerp("#7A4A28" if wk.get("stag") else "#96603A",
                     _INK, self._dark * 0.8)
        grazing = hold and wk["pose"] == "graze"
        # Vigilance runs on a slow wall clock (roughly a 3-4 s scan every
        # ~14 s, phase-jittered per deer) — the old gait-driven test
        # flicked the head several times a second.
        vig_ph = wk.setdefault("_vig_ph", random.uniform(0.0, math.tau))
        vigilant = math.sin(self._t * 0.45 + vig_ph) > 0.72
        # The head drop is EASED between up (0) and the sward (1) so the
        # neck visibly sweeps down and back instead of snapping poses.
        drop = wk.get("_drop", 0.0)
        drop += ((1.0 if grazing and not vigilant else 0.0) - drop) * 0.14
        wk["_drop"] = drop
        GND = 21.4
        stride = 0.0 if hold else 5.2
        HLFT, HL1, HL2 = 1.2, 4.0, 4.6      # hind cannon, hocked
        FLFT, FL1, FL2 = 0.5, 4.3, 4.3      # fore, near-straight
        # Far pair (behind the body), fainter.
        far_c = _lerp(coat.name(), _INK, 0.5)
        self._walk_legs(p, far_c, [(8.9, 12.8, 0.25, -1)], g, GND, stride,
                        HLFT, HL1, HL2, alpha=0.5, width=1.45)
        self._walk_legs(p, far_c, [(16.7, 13.2, 0.75, 1)], g, GND, stride,
                        FLFT, FL1, FL2, alpha=0.5, width=1.3)
        bob = 0.0 if hold else self._walk_bob(g, 0.3)
        p.save()
        p.translate(0, bob)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(coat)
        p.drawEllipse(QPointF(7.2, 11.0), 2.5, 2.5)   # haunch / thigh mass
        if drop > 0.02:
            # Torso only (points 6..34), plus a parametric neck+head
            # posed by `drop`: the HEAD sweeps from its head-up station
            # to the sward along a forward-bowed arc (out and down, never
            # through the chest) and a straight tapered neck hinges after
            # it from the shoulder. At drop 1 the muzzle lands just
            # ahead of the forefeet.
            dd = drop * drop * (3.0 - 2.0 * drop)     # smoothstep ease
            nib = 0.4 * math.sin(g * 6) * dd
            torso = QPainterPath(QPointF(*_DEER_BODY[6]))
            for x, y in _DEER_BODY[7:35]:
                torso.lineTo(QPointF(x, y))
            torso.closeSubpath()
            p.drawPath(torso)
            self._deer_shading(p, torso, coat)
            p.setBrush(coat)

            def sw(pu, pd, fwd):
                # Quadratic sweep up-station → down-station, bowed
                # forward by fwd so the arc clears the chest.
                cx = (pu[0] + pd[0]) * 0.5 + fwd
                cy = (pu[1] + pd[1]) * 0.5
                u = 1.0 - dd
                return (u * u * pu[0] + 2 * u * dd * cx + dd * dd * pd[0],
                        u * u * pu[1] + 2 * u * dd * cy + dd * dd * pd[1])

            # A deer does not BEND its neck to graze — the neck stays a
            # straight taper and hinges DOWN from the shoulder like a
            # lever. So: one straight axis from a shoulder root (sliding
            # a little down the chest as the head drops) to just behind
            # the head, with the wedge built on perpendicular stations —
            # never a curved centreline, so it can't read as a hosepipe.
            hc = sw((24.35, 3.3), (24.4, 19.9 + nib), 2.8)
            bx = 20.5 + 0.5 * dd                    # neck root (shoulder)
            by = 8.0 + 1.7 * dd
            ux, uy = hc[0] - bx, hc[1] - by
            ul = math.hypot(ux, uy) or 1.0
            ux, uy = ux / ul, uy / ul               # axis, root → head
            nx, ny = uy, -ux                        # dorsal normal
            al = ul - 0.9                           # stop short of the head

            def st(t):
                # Dorsal/ventral edge points at fraction t along the
                # axis; half-width tapers 1.8 (shoulder) → 0.75 (throat).
                w = 1.8 - 1.05 * t
                cx = bx + ux * al * t
                cy = by + uy * al * t
                return (cx + nx * w, cy + ny * w, cx - nx * w, cy - ny * w)

            s15, s50, s80, s99 = st(0.15), st(0.5), st(0.8), st(1.0)
            rvx = 21.66 - 1.06 * dd                 # chest root slides down
            rvy = 9.18 + 1.22 * dd
            neck = QPainterPath(QPointF(19.4, 6.85))    # off the withers
            neck.quadTo(s15[0], s15[1], s50[0], s50[1])
            neck.quadTo(s80[0], s80[1], s99[0], s99[1])
            neck.lineTo(QPointF(s99[2], s99[3]))
            neck.quadTo(s80[2], s80[3], s50[2], s50[3])
            neck.quadTo(s15[2], s15[3], rvx, rvy)
            neck.closeSubpath()
            p.drawPath(neck)
            p.save()
            p.translate(hc[0], hc[1])
            p.rotate(38 + 32 * dd)
            # Ears in profile: the far ear a shorter peek behind the
            # near one, both at the back of the skull.
            for ex, ey, er, el in ((-0.85, -0.85, -38, 0.75),
                                   (-0.1, -1.1, -6, 0.95)):
                p.save()
                p.translate(ex, ey)
                p.rotate(er)
                p.drawEllipse(QPointF(0, 0), 0.42, el)
                p.restore()
            p.drawEllipse(QPointF(0, 0), 1.5, 0.9)
            p.drawEllipse(QPointF(1.5, 0.2), 0.7, 0.5)      # muzzle
            p.restore()
            if wk.get("stag"):
                aa = sw((23.55, 2.1), (24.0, 18.5 + nib), 2.7)
                self._antlers(p, _lerp(coat.name(), _INK, 0.35),
                              aa[0], aa[1], -6 + 62 * dd, 1.7 - 0.6 * dd)
            p.setBrush(QColor(_CHALK))
            ec = sw((24.9, 2.6), (24.85, 19.5 + nib), 2.8)
            p.drawEllipse(QPointF(ec[0], ec[1]), 0.34, 0.34)
        else:
            # Full traced body — head up.
            body = QPainterPath(QPointF(*_DEER_BODY[0]))
            for x, y in _DEER_BODY[1:]:
                body.lineTo(QPointF(x, y))
            body.closeSubpath()
            p.drawPath(body)
            self._deer_shading(p, body, coat)
            if wk.get("stag"):
                self._antlers(p, _lerp(coat.name(), _INK, 0.35),
                              23.55, 2.1, -6, 1.7)
            p.setBrush(QColor(_CHALK))
            p.drawEllipse(QPointF(24.9, 2.6), 0.34, 0.34)   # eye
        p.restore()
        # Near pair (over the body), darker toward the hooves.
        leg_c = _lerp(coat.name(), _INK, 0.3)
        self._walk_legs(p, leg_c, [(6.8, 12.8, 0.0, -1)], g, GND, stride,
                        HLFT, HL1, HL2, width=1.65)
        self._walk_legs(p, leg_c, [(17.9, 13.2, 0.5, 1)], g, GND, stride,
                        FLFT, FL1, FL2, width=1.45)

    def _draw_owl(self, p, wk):
        g = wk["gait"]
        # Tawny by day (so it reads against a pale sky), moonlit-pale by
        # night. Long glides on outstretched wings, the odd lazy beat.
        c = _lerp("#8A6247", _CHALK, self._dark)
        if wk["pose"] == "perch" and wk["pause_t"] > 0:
            # Perched upright on the ring-o: wings folded, blinking.
            # Stays warm buff even at night so it reads against the
            # pale wordmark behind it.
            c = _lerp("#8A6247", "#D9CDB9", self._dark)
            face = _lerp("#EDE4D4", _CHALK, self._dark)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            body = QPainterPath(QPointF(14.6, 7.8))
            body.quadTo(14.0, 2.6, 16.6, 1.2)
            body.quadTo(19.2, 2.4, 18.8, 7.8)
            body.closeSubpath()
            p.drawPath(body)
            p.drawEllipse(QPointF(16.7, 1.6), 2.2, 2.0)   # round head
            wingf = QPainterPath(QPointF(14.9, 3.4))      # folded wing
            wingf.quadTo(14.2, 5.4, 15.2, 7.4)
            wingf.quadTo(16.2, 6.0, 15.9, 3.8)
            wingf.closeSubpath()
            p.setBrush(QColor(c).darker(115))
            p.drawPath(wingf)
            p.setBrush(face)
            p.drawEllipse(QPointF(17.3, 1.6), 1.5, 1.6)   # facial disc
            blink = math.sin(g * 0.8) > 0.9
            p.setBrush(QColor(_INK))
            if blink:
                p.setPen(QPen(QColor(_INK), 0.4))
                p.drawLine(QPointF(17.2, 1.4), QPointF(17.9, 1.4))
                p.setPen(Qt.PenStyle.NoPen)
            else:
                p.drawEllipse(QPointF(17.6, 1.4), 0.5, 0.5)
            for tx2 in (15.9, 17.3):                      # talons on the rim
                p.setPen(QPen(QColor(_INK), 0.5))
                p.drawLine(QPointF(tx2, 7.8), QPointF(tx2 + 0.5, 8.6))
            p.setPen(Qt.PenStyle.NoPen)
            return
        env = max(0.0, math.sin(g * 0.16))     # beat bursts between glides
        flap = (2.5 + 21 * env) * math.sin(g)
        p.setPen(Qt.PenStyle.NoPen)
        for sweep, ang, op in ((-1, flap, 1.0), (1, -flap * 0.75, 0.8)):
            p.save()
            p.translate(16.5, 4.8)
            p.rotate(ang)
            p.setOpacity(op)
            wing = QPainterPath(QPointF(0, 0))     # broad, round-tipped
            wing.quadTo(4.5 * sweep, -4.6, 9.5 * sweep, -4.2)
            wing.quadTo(12.6 * sweep, -3.6, 13.0 * sweep, -2.0)
            wing.quadTo(8.0 * sweep, 0.4, 0, 1.6)
            wing.closeSubpath()
            p.setBrush(c)
            p.drawPath(wing)
            p.restore()
        p.setOpacity(1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(17.5, 5.4), 4.6, 2.4)      # compact body
        tail = QPainterPath(QPointF(13.6, 4.9))          # short fan tail
        tail.lineTo(10.6, 6.1)
        tail.lineTo(13.8, 6.9)
        tail.closeSubpath()
        p.drawPath(tail)
        p.drawEllipse(QPointF(22.0, 4.4), 2.6, 2.5)      # BIG round head
        face = _lerp("#EDE4D4", _CHALK, self._dark)      # facial disc
        p.setBrush(face)
        p.drawEllipse(QPointF(22.9, 4.4), 1.75, 1.95)
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(23.1, 4.0), 0.55, 0.55)    # eye
        beak = QPainterPath(QPointF(24.4, 4.9))          # little hook
        beak.lineTo(25.2, 5.3)
        beak.lineTo(24.3, 5.6)
        beak.closeSubpath()
        p.drawPath(beak)

    def _draw_bat(self, p, wk):
        g = wk["gait"]
        # Dark by day, moon-pale by night; scalloped membrane wings.
        wing_c = _lerp("#3E4A42", _LEAF_NIGHT, self._dark)
        flap = 54 * math.sin(g) - 8 * math.sin(2 * g)
        p.setPen(Qt.PenStyle.NoPen)
        for pivot_x, sweep in ((10.5, -1), (13.5, 1)):
            p.save()
            p.translate(pivot_x, 4)
            p.rotate(-flap * sweep)
            wing = QPainterPath(QPointF(0, 0))
            wing.quadTo(5.0 * sweep, -5.2, 10.5 * sweep, -1.2)
            wing.quadTo(8.6 * sweep, -0.4, 7.0 * sweep, 0.4)  # scallop 1
            wing.quadTo(5.2 * sweep, -0.1, 3.8 * sweep, 0.8)  # scallop 2
            wing.quadTo(1.8 * sweep, 0.4, 0, 1.1)             # scallop 3
            wing.closeSubpath()
            p.setBrush(wing_c)
            p.drawPath(wing)
            p.restore()
        p.setBrush(wing_c)
        p.drawEllipse(QPointF(12, 4), 2.1, 1.6)
        for ex, tip in ((10.8, -0.35), (12.6, 0.35)):   # big ears
            ear = QPainterPath(QPointF(ex, 2.9))
            ear.lineTo(ex + tip, 1.1)
            ear.lineTo(ex + 0.85, 2.7)
            ear.closeSubpath()
            p.drawPath(ear)

    def _draw_buzzard(self, p, wk):
        g = wk["gait"]
        # Dark buzzard-brown — just off-silhouette, matching the tawny
        # owl / kite treatment so it reads against a pale sky.
        ink = _lerp("#5F4630", _INK, self._dark * 0.75)
        # A slow scale pulse fakes circling toward and away from the viewer.
        pulse = 0.9 + 0.12 * math.sin(g * 0.22)
        p.translate(15, 5)
        p.scale(pulse, pulse)
        p.translate(-15, -5)
        flap = 11 * math.sin(g)
        p.setPen(Qt.PenStyle.NoPen)
        for sweep, ang, op in ((-1, flap, 1.0), (1, -flap * 0.8, 0.85)):
            p.save()
            p.translate(15, 5)
            p.rotate(ang)
            p.setOpacity(op)
            wing = QPainterPath(QPointF(0, 0))
            wing.quadTo(6 * sweep, -4.2, 12.6 * sweep, -3.6)
            wing.lineTo(11.6 * sweep, -2.6)
            wing.lineTo(13.4 * sweep, -2.4)
            wing.lineTo(12.2 * sweep, -1.4)
            wing.lineTo(13.8 * sweep, -1.2)
            wing.quadTo(6 * sweep, 1.0, 0, 1.4)
            wing.closeSubpath()
            p.setBrush(ink)
            p.drawPath(wing)
            p.restore()
        p.setOpacity(1.0)
        p.setBrush(ink)
        p.drawEllipse(QPointF(15, 5.4), 4.0, 1.7)
        tail = QPainterPath(QPointF(11.6, 4.6))
        tail.lineTo(8.4, 3.8)
        tail.lineTo(8.2, 6.8)
        tail.lineTo(11.5, 6.2)
        tail.closeSubpath()
        p.drawPath(tail)
        p.drawEllipse(QPointF(19.4, 5.0), 1.5, 1.3)
        beak = QPainterPath(QPointF(20.8, 4.8))
        beak.lineTo(21.9, 5.3)
        beak.lineTo(20.7, 5.8)
        beak.closeSubpath()
        p.drawPath(beak)

    def _draw_bee(self, p, wk):
        g = wk["gait"]
        amber = _lerp("#D9A23B", "#8A6A28", self._dark * 0.6)
        ink = QColor(_INK)
        # Blurred wing pair, buzzing at full gait rate.
        wing = QColor(_CHALK)
        wing.setAlphaF(0.5)
        flut = 0.55 + 0.45 * abs(math.sin(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(wing)
        p.save()
        p.translate(4.6, 2.4)
        p.scale(1.0, flut)
        p.drawEllipse(QPointF(-0.7, -1.1), 1.8, 1.05)
        p.drawEllipse(QPointF(1.1, -1.3), 1.4, 0.9)
        p.restore()
        body = QPainterPath()
        body.addEllipse(QPointF(4.4, 3.9), 2.6, 1.75)
        p.setBrush(amber)
        p.drawPath(body)
        p.save()
        p.setClipPath(body)
        p.setBrush(ink)
        for bx in (3.3, 4.7):                          # bands
            p.drawRect(QRectF(bx, 1.9, 0.8, 4.0))
        p.restore()
        p.setBrush(ink)
        p.drawEllipse(QPointF(7.2, 3.6), 1.05, 0.95)   # head
        sting = QPainterPath(QPointF(1.9, 3.8))
        sting.lineTo(0.9, 4.15)
        sting.lineTo(2.0, 4.5)
        sting.closeSubpath()
        p.drawPath(sting)
        pen = QPen(ink, 0.45)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for lx in (3.4, 4.6, 5.8):                     # dangling legs
            p.drawLine(QPointF(lx, 5.4), QPointF(lx - 0.5, 6.3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(7.6, 3.3), 0.3, 0.3)

    def _draw_stag_beetle(self, p, wk):
        g = wk["gait"]
        hold = wk["pause_t"] > 0
        rear = hold and wk["pose"] == "rear"
        body_c = _lerp("#2B1B10", _INK, self._dark * 0.5)
        horn_c = _lerp("#7A4222", _INK, self._dark * 0.45)
        fly = wk.get("fly_t") is not None
        p.save()
        if rear:
            # Rears up, antler-jaws spread — the classic threat display.
            p.translate(2.5, 6.2)
            p.rotate(-16 - 2.5 * math.sin(g * 1.4))
            p.translate(-2.5, -6.2)
        if fly:
            # Clumsy flight: nose-up, wing-cases held open, membrane
            # wings a buzzing blur, legs trailing.
            p.translate(6, 4)
            p.rotate(-9)
            p.translate(-6, -4)
            wingc = QColor(_CHALK)
            wingc.setAlphaF(0.5)
            flut = 0.5 + 0.5 * abs(math.sin(g * 1.2))
            p.setPen(Qt.PenStyle.NoPen)
            p.save()
            p.translate(6.4, 2.2)
            p.scale(1.0, flut)
            p.setBrush(wingc)
            p.drawEllipse(QPointF(-1.2, -1.2), 2.6, 1.3)
            p.drawEllipse(QPointF(1.6, -1.4), 2.2, 1.1)
            p.restore()
            for ang, exo in ((-38, -1.5), (-64, 0.6)):    # open elytra
                p.save()
                p.translate(7.5 + exo, 3.0)
                p.rotate(ang)
                p.setBrush(body_c)
                p.drawEllipse(QPointF(-1.6, 0.0), 2.1, 1.05)
                p.restore()
            pen = QPen(body_c, 0.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for lx in (4.6, 6.6, 8.6):                    # dangling legs
                p.drawLine(QPointF(lx, 5.2), QPointF(lx - 0.7, 6.8))
        else:
            # Six scuttling legs, far rank fainter.
            for rank, al in ((((5.0, 4.9, math.pi), (7.2, 4.9, 0),
                               (9.4, 4.8, math.pi)), 0.5),
                             (((4.4, 5.0, 0), (6.6, 5.0, math.pi),
                               (8.8, 4.9, 0)), 1.0)):
                col = QColor(body_c)
                col.setAlphaF(al)
                pen = QPen(col, 0.8)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                for hx, hy, phase in rank:
                    a = math.radians(30) * math.sin(g + phase)
                    p.drawLine(QPointF(hx, hy),
                               QPointF(hx + 2.0 * math.sin(a) - 0.4,
                                       hy + 2.0 * math.cos(a)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body_c)
        p.drawEllipse(QPointF(5.3, 4.0), 3.3, 1.9)     # elytra
        p.drawEllipse(QPointF(9.5, 3.9), 1.5, 1.35)    # thorax
        p.drawEllipse(QPointF(11.5, 4.0), 1.15, 1.0)   # head
        sheen = QColor(_CHALK)
        sheen.setAlphaF(0.18)
        p.setPen(QPen(sheen, 0.4))
        p.drawLine(QPointF(2.6, 3.7), QPointF(8.2, 3.5))  # wing-case seam
        # The antler mandibles, spreading wide in display.
        mo = (1.6 * max(0.0, math.sin(g * 1.6)) if rear
              else 0.25 + 0.2 * math.sin(g * 0.7))
        pen = QPen(horn_c, 0.75)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, lift in ((0, 1.0), (1, 0.55)):
            horn = QPainterPath(QPointF(12.2, 3.8 + k * 0.5))
            horn.quadTo(13.8, 3.2 - (1.2 + mo) * lift * 0.8,
                        15.2, 2.6 - (1.0 + mo) * lift)
            p.drawPath(horn)
        p.drawLine(QPointF(13.9, 2.6 - mo * 0.6), QPointF(14.5, 3.3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(11.8, 3.6), 0.28, 0.28)
        p.restore()

    def _draw_beaver(self, p, wk, swim=False):
        g = wk["gait"]
        c = _lerp("#5A4030", _INK, self._dark * 0.75)
        tail_c = _lerp("#3B3733", _INK, self._dark * 0.5)
        chalk = QColor(_CHALK)
        if swim:
            wake = QColor(_CHALK)
            wake.setAlphaF(0.5)
            pen = QPen(wake, 0.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for wx in (-2.0, -5.5):
                arc = QPainterPath(QPointF(wx, 4.6))
                arc.quadTo(wx - 2.2, 5.4, wx - 4.4, 5.0)
                p.drawPath(arc)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tail_c)
            paddle = 0.4 * math.sin(g * 0.8)
            ptl = QPainterPath(QPointF(8.0, 4.5))          # paddle astern
            ptl.quadTo(4.5, 4.2 + paddle, 1.2, 4.5 + paddle)
            ptl.quadTo(0.2, 4.9 + paddle, 1.0, 5.4 + paddle)
            ptl.quadTo(4.5, 5.7, 8.0, 5.3)
            ptl.closeSubpath()
            p.drawPath(ptl)
            p.setBrush(c)
            surf = QPainterPath(QPointF(7.2, 4.9))         # one wet line
            surf.quadTo(12.0, 3.2, 17.5, 3.8)              # low back
            surf.quadTo(20.6, 4.0, 21.8, 3.3)              # neck
            surf.quadTo(22.4, 2.3, 24.0, 2.4)              # broad head
            surf.quadTo(25.8, 2.6, 26.2, 3.6)              # blunt muzzle
            surf.quadTo(24.8, 4.8, 22.6, 4.8)              # chin
            surf.quadTo(16.0, 5.5, 10.0, 5.4)              # waterline
            surf.quadTo(8.2, 5.3, 7.2, 4.9)
            surf.closeSubpath()
            p.drawPath(surf)
            p.setBrush(QColor(_INK))
            p.drawEllipse(QPointF(26.0, 3.3), 0.4, 0.35)   # nose
            p.setBrush(chalk)
            p.drawEllipse(QPointF(24.4, 2.9), 0.42, 0.42)  # eye
            return
        if wk["pause_t"] > 0 and wk["pose"] == "gnaw":
            # Sat up on the flat tail, turning a stick in its paws.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tail_c)
            p.drawEllipse(QPointF(8.0, 12.3), 3.8, 0.95)
            p.setBrush(c)
            body = QPainterPath(QPointF(8.6, 12.6))
            body.quadTo(7.4, 6.6, 10.6, 4.6)
            body.quadTo(13.6, 3.0, 15.4, 5.6)
            body.quadTo(16.6, 8.6, 15.2, 12.6)
            body.closeSubpath()
            p.drawPath(body)
            p.drawEllipse(QPointF(14.2, 3.9), 2.3, 2.1)    # head
            p.drawEllipse(QPointF(12.9, 2.6), 0.55, 0.55)  # ear
            gn = 0.5 * math.sin(g * 4)
            stick = QPen(_lerp("#6E4A2A", _INK, self._dark * 0.5), 0.9)
            stick.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(stick)
            p.drawLine(QPointF(12.6, 5.6 + gn), QPointF(17.2, 4.6 - gn))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(13.9, 5.4 + gn * 0.5), 0.85, 0.85)
            p.drawEllipse(QPointF(15.5, 5.1 - gn * 0.5), 0.85, 0.85)
            p.setBrush(chalk)
            p.drawEllipse(QPointF(15.3, 3.4), 0.4, 0.4)    # eye
            p.drawRect(QRectF(15.6, 4.6, 0.5, 0.8))        # the teeth
            return
        GND, L1, L2 = 13.0, 1.4, 1.4
        bfar = [(10.5, 10.7, 0.25, -1), (18.8, 10.7, 0.75, +1)]
        bnear = [(8.2, 10.9, 0.0, -1), (21.0, 10.9, 0.5, +1)]
        self._walk_legs(p, _lerp(c.name(), _INK, 0.45), bfar, g, GND,
                        2.6, 0.9, L1, L2, alpha=0.9, width=1.3)
        p.save()
        p.translate(0, self._walk_bob(g, 0.16))
        # Paddle tail attached at the rump, dragging along the ground.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tail_c)
        wag = 0.5 * math.sin(g)
        ptl = QPainterPath(QPointF(6.2, 9.0))
        ptl.quadTo(3.2, 9.8 + wag, 1.0, 11.4 + wag)
        ptl.quadTo(-0.3, 12.4, 0.7, 13.0)
        ptl.quadTo(3.4, 13.2, 5.5, 11.8)
        ptl.quadTo(6.7, 11.0, 7.4, 10.3)
        ptl.closeSubpath()
        p.drawPath(ptl)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        body = QPainterPath(QPointF(4.2, 10.9))
        body.quadTo(3.0, 6.8, 9.0, 4.8)
        body.quadTo(15.5, 3.1, 20.0, 4.6)
        body.quadTo(24.0, 5.8, 24.4, 7.9)
        body.quadTo(24.6, 9.8, 21.6, 10.9)
        body.quadTo(12.0, 12.2, 6.2, 11.4)
        body.closeSubpath()
        p.drawPath(body)
        p.drawEllipse(QPointF(24.7, 6.9), 2.6, 2.15)       # head
        p.drawEllipse(QPointF(23.3, 4.8), 0.6, 0.6)        # ear
        p.drawEllipse(QPointF(26.9, 7.6), 1.25, 0.95)      # muzzle
        p.setBrush(chalk)
        p.drawEllipse(QPointF(25.5, 6.3), 0.42, 0.42)      # eye
        p.drawRect(QRectF(27.3, 8.1, 0.5, 0.75))           # teeth
        p.restore()                                        # end body bob
        self._walk_legs(p, c, bnear, g, GND, 2.6, 0.9, L1, L2, width=1.4)

    def _draw_water_vole(self, p, wk, swim=False):
        g = wk["gait"]
        c = _lerp("#6B4A33", _INK, self._dark * 0.75)
        chalk = QColor(_CHALK)
        if swim:
            wake = QColor(_CHALK)
            wake.setAlphaF(0.5)
            pen = QPen(wake, 0.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for wx in (-1.5, -4.0):
                arc = QPainterPath(QPointF(wx, 4.8))
                arc.quadTo(wx - 1.8, 5.5, wx - 3.6, 5.1)
                p.drawPath(arc)
            pen = QPen(c, 0.7)
            p.setPen(pen)
            p.drawLine(QPointF(2.6, 5.0), QPointF(-0.4, 5.3))  # tail astern
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(7.0, 4.7), 3.5, 1.25)    # low body
            p.drawEllipse(QPointF(11.4, 4.1), 1.75, 1.35)  # blunt head
            p.setBrush(chalk)
            p.drawEllipse(QPointF(12.0, 3.7), 0.32, 0.32)
            return
        if wk["pause_t"] > 0 and wk["pose"] == "nibble":
            # Sat up nibbling a grass stem held in both paws.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(7.2, 6.3), 2.5, 1.7)     # haunches
            body = QPainterPath(QPointF(5.6, 7.2))
            body.quadTo(5.2, 3.6, 7.6, 2.4)
            body.quadTo(9.4, 1.6, 10.2, 3.2)
            body.quadTo(10.7, 5.2, 9.8, 7.2)
            body.closeSubpath()
            p.drawPath(body)
            p.drawEllipse(QPointF(9.6, 2.2), 1.45, 1.3)    # head
            p.drawEllipse(QPointF(8.6, 1.2), 0.45, 0.45)   # ear
            nib = 0.35 * math.sin(g * 5)
            stem_c = _lerp(_TUFT_DAY, _TUFT_NIGHT, self._dark)
            pen = QPen(stem_c, 0.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(10.4, 3.4 + nib), QPointF(12.2, 0.9 + nib))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(10.2, 3.4 + nib * 0.6), 0.6, 0.6)
            p.setBrush(chalk)
            p.drawEllipse(QPointF(10.3, 1.9), 0.3, 0.3)
            pen = QPen(c, 0.8)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            tail = QPainterPath(QPointF(5.0, 7.4))
            tail.quadTo(3.2, 7.8, 2.2, 7.0)
            p.drawPath(tail)
            return
        GND, L1, L2 = 8.0, 0.9, 0.95
        vfar = [(5.8, 6.6, 0.25, -1), (10.2, 6.6, 0.75, +1)]
        vnear = [(4.8, 6.8, 0.0, -1), (11.0, 6.8, 0.5, +1)]
        self._walk_legs(p, _lerp(c.name(), _INK, 0.45), vfar, g, GND,
                        1.8, 0.6, L1, L2, alpha=0.9, width=1.0)
        p.save()
        p.translate(0, self._walk_bob(g, 0.12))
        pen = QPen(c, 0.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        tail = QPainterPath(QPointF(3.4, 6.2))
        tail.quadTo(1.2, 6.6, 0.2, 7.4)
        p.drawPath(tail)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        body = QPainterPath(QPointF(3.2, 6.9))
        body.quadTo(2.5, 3.3, 6.6, 2.6)
        body.quadTo(10.6, 2.1, 12.4, 4.1)
        body.quadTo(13.2, 5.5, 11.6, 6.6)
        body.quadTo(7.2, 7.8, 4.4, 7.2)
        body.closeSubpath()
        p.drawPath(body)
        p.drawEllipse(QPointF(12.8, 4.7), 1.15, 0.95)      # blunt muzzle
        p.drawEllipse(QPointF(10.7, 2.6), 0.5, 0.5)        # small ear
        p.setBrush(chalk)
        p.drawEllipse(QPointF(11.9, 3.9), 0.32, 0.32)
        p.restore()                                        # end body bob
        self._walk_legs(p, c, vnear, g, GND, 1.8, 0.6, L1, L2, width=1.1)

    def _draw_sheep(self, p, wk):
        """A ewe in the 24×16 frame: lumpy fleece, slate face and legs,
        head down in the sward most of the time. Lambs are the same
        sheep drawn at lamb scale."""
        g = wk["gait"]
        d = self._dark
        fleece = _lerp("#E9E5D8", "#4A5248", d)
        fl_lo = QColor(fleece).darker(112)
        slate = _lerp("#4B4B47", _INK, 0.4 + d * 0.3)
        hold = wk["pause_t"] > 0
        graze = hold and wk["pose"] == "graze"
        walking = not hold
        pen = QPen(slate, 1.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for i, lx in enumerate((7.5, 10.0, 16.5, 19.0)):
            sw = math.sin(g + (0.0, 3.1, 3.1, 0.0)[i]) \
                * (1.6 if walking else 0.0)
            p.drawLine(QPointF(lx, 11.0), QPointF(lx + sw, 15.6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fl_lo)                       # belly shade
        p.drawEllipse(QPointF(13.0, 9.4), 8.0, 4.2)
        p.setBrush(fleece)                      # the lumpy cloud
        for ox, oy, rx, ry in ((-4.5, -0.5, 4.0, 3.2), (0.5, -1.6, 4.6, 3.4),
                               (4.5, -0.3, 3.8, 3.0), (0.0, 1.0, 6.4, 3.2)):
            p.drawEllipse(QPointF(13.0 + ox, 8.6 + oy), rx, ry)
        p.drawEllipse(QPointF(4.8, 6.8), 1.3, 1.6)   # docked tail
        # The head: slate, wool topknot, an ear — dropped to the sward
        # when grazing, up and level otherwise.
        if graze:
            hx, hy, ang = 21.6, 13.2 + 0.35 * math.sin(g * 1.3), 55.0
        else:
            hx, hy, ang = 22.0, 6.4, 8.0
        p.save()
        p.translate(hx, hy)
        p.rotate(ang)
        p.setBrush(slate)
        p.drawRoundedRect(QRectF(-1.5, -1.6, 3.3, 3.3), 1.3, 1.3)
        p.drawEllipse(QPointF(-1.8, -0.8), 1.3, 0.55)   # ear
        p.setBrush(fleece)
        p.drawEllipse(QPointF(-0.5, -1.8), 1.7, 1.0)    # topknot
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(0.7, -0.6), 0.3, 0.3)     # eye
        p.restore()

    def _draw_rabbit(self, p, wk):
        """A warren rabbit in the 13×9 frame: hunched grey-brown body,
        tall ears, white scut, bobbing hops between nibbles."""
        g = wk["gait"]
        d = self._dark
        coat = _lerp("#8A7B66", _INK, d * 0.75)
        coat_lo = QColor(coat).darker(112)
        hold = wk["pause_t"] > 0
        nibble = hold and wk["pose"] == "nibble"
        bounce = 0.0 if hold else max(0.0, math.sin(g * 1.3)) * 1.1
        p.save()
        p.translate(0, -bounce)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(2.9, 4.8), 1.1, 1.1)      # the scut
        p.setBrush(coat_lo)
        p.drawEllipse(QPointF(4.6, 5.4), 2.6, 2.5)      # haunch
        p.setBrush(coat)
        p.drawEllipse(QPointF(6.4, 5.6), 4.0, 2.9)      # body
        if nibble:
            hx, hy, ang, ea = 10.4, 6.2, 38.0, 55.0     # head to the sward
        else:
            hx, hy, ang, ea = 10.0, 3.6, 4.0, 12.0
        p.save()
        p.translate(hx, hy)
        p.rotate(ang)
        p.setBrush(coat)
        p.drawEllipse(QPointF(0.0, 0.0), 2.0, 1.7)      # head
        p.save()                                        # the ears
        p.rotate(ea - 30.0)
        p.setBrush(coat_lo)
        p.drawEllipse(QPointF(-1.1, -2.4), 0.55, 1.9)
        p.setBrush(coat)
        p.drawEllipse(QPointF(0.1, -2.6), 0.6, 2.1)
        p.restore()
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(0.8, -0.35), 0.28, 0.28)  # eye
        p.restore()
        pen = QPen(coat_lo, 0.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)                                   # forepaw
        p.drawLine(QPointF(8.6, 7.2), QPointF(9.2, 8.6 + bounce * 0.7))
        p.setPen(Qt.PenStyle.NoPen)
        p.restore()

    def _draw_squirrel(self, p, wk):
        """A native red squirrel: chestnut coat, cream belly, ear tufts,
        a great bushy tail. Bounds along the ground, clings head-up to
        climb the oak, and sits upright to nibble an acorn."""
        g = wk["gait"]
        red = _lerp("#A65523", _INK, self._dark * 0.7)
        cream = _lerp("#E8D9BE", "#6E6250", self._dark * 0.55)
        acorn_c = _lerp("#7A5A22", _INK, self._dark * 0.4)
        pose = wk["pose"]
        if pose == "forage":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(red)
            tail = QPainterPath(QPointF(5.0, 11.5))     # tail curled up
            tail.quadTo(-0.6, 10.5, -0.2, 4.5)
            tail.quadTo(0.4, -0.2, 5.0, 1.4)
            tail.quadTo(2.8, 3.2, 3.2, 6.2)
            tail.quadTo(3.4, 9.2, 6.4, 10.8)
            tail.closeSubpath()
            p.drawPath(tail)
            p.drawEllipse(QPointF(7.2, 9.6), 3.0, 3.3)   # haunch
            body = QPainterPath(QPointF(5.7, 10.6))
            body.quadTo(5.2, 4.6, 8.2, 2.9)
            body.quadTo(10.6, 1.7, 11.4, 4.1)
            body.quadTo(11.7, 7.1, 10.3, 10.6)
            body.closeSubpath()
            p.drawPath(body)
            p.setBrush(cream)
            p.drawEllipse(QPointF(9.4, 8.0), 1.5, 2.4)   # pale belly
            p.setBrush(red)
            p.drawEllipse(QPointF(10.5, 2.7), 1.9, 1.8)  # head
            for ex in (9.5, 11.1):
                ear = QPainterPath(QPointF(ex, 1.3))
                ear.lineTo(ex + 0.2, -1.1)
                ear.lineTo(ex + 0.9, 1.1)
                ear.closeSubpath()
                p.drawPath(ear)
            nib = 0.35 * math.sin(g * 4)
            p.setBrush(cream)
            p.drawEllipse(QPointF(10.4, 4.4 + nib), 0.85, 0.7)  # paws
            p.setBrush(acorn_c)
            p.drawEllipse(QPointF(10.6, 4.0 + nib), 0.7, 0.85)  # acorn
            p.setBrush(_lerp("#4E3A18", _INK, 0.3))
            p.drawRect(QRectF(10.2, 3.1 + nib, 0.8, 0.5))       # cap
            p.setBrush(QColor(_CHALK))
            p.drawEllipse(QPointF(11.1, 2.3), 0.35, 0.35)       # eye
            return
        if pose == "climb":
            # The body gathers and surges with each scrabbling bound —
            # stretched on the pull, bunched between — anchored at the
            # hind feet so the claws stay planted on the bark.
            p.save()
            reach = math.sin(g)
            p.translate(5.3, 11.2)
            p.scale(1.0, 1.0 + 0.09 * reach)
            p.translate(-5.3, -11.2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(red)
            tail = QPainterPath(QPointF(6.2, 5.0))       # tail streams down
            tail.quadTo(9.4, 8.0, 8.8, 12.2)
            tail.quadTo(8.2, 15.2, 5.5, 14.6)
            tail.quadTo(6.9, 12.0, 6.3, 9.0)
            tail.quadTo(5.7, 6.4, 4.6, 5.4)
            tail.closeSubpath()
            p.drawPath(tail)
            body = QPainterPath(QPointF(3.6, 11.2))      # body vertical
            body.quadTo(2.7, 6.0, 4.1, 3.0)
            body.quadTo(5.1, 1.0, 6.5, 2.6)
            body.quadTo(7.3, 6.0, 6.5, 11.2)
            body.closeSubpath()
            p.drawPath(body)
            p.drawEllipse(QPointF(5.3, 1.9), 1.7, 1.7)   # head up
            for ex in (4.3, 6.0):
                ear = QPainterPath(QPointF(ex, 0.7))
                ear.lineTo(ex + 0.1, -1.3)
                ear.lineTo(ex + 0.8, 0.5)
                ear.closeSubpath()
                p.drawPath(ear)
            gp = QPen(red, 1.0)
            gp.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(gp)
            rf = 0.6 * math.sin(g)
            p.drawLine(QPointF(4.1, 4.2), QPointF(2.5, 3.3 + rf))
            p.drawLine(QPointF(6.0, 4.2), QPointF(7.5, 3.1 - rf))
            p.drawLine(QPointF(4.2, 9.0), QPointF(2.7, 10.0 - rf))
            p.drawLine(QPointF(6.3, 9.0), QPointF(7.7, 10.0 + rf))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(_CHALK))
            p.drawEllipse(QPointF(5.7, 1.5), 0.3, 0.3)
            p.restore()
            return
        # Ground bound.
        hop = abs(math.sin(g))
        p.save()
        p.translate(8, 8 - 1.8 * hop)
        p.rotate(-6 * math.sin(g + 0.4))
        p.translate(-8, -8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(red)
        tail = QPainterPath(QPointF(2.5, 9.2))           # arched over back
        tail.quadTo(-1.6, 6.0, 0.6, 1.8)
        tail.quadTo(2.2, -0.7, 5.2, 1.6)
        tail.quadTo(3.0, 2.6, 3.0, 5.2)
        tail.quadTo(3.2, 7.6, 5.2, 9.2)
        tail.closeSubpath()
        p.drawPath(tail)
        body = QPainterPath(QPointF(4.2, 9.6))
        body.quadTo(3.7, 6.0, 7.2, 5.0)
        body.quadTo(11.2, 4.2, 13.2, 6.0)
        body.quadTo(13.8, 7.5, 12.6, 9.2)
        body.quadTo(8.2, 10.6, 4.2, 9.6)
        body.closeSubpath()
        p.drawPath(body)
        p.drawEllipse(QPointF(13.1, 5.6), 1.8, 1.7)      # head
        for ex in (12.3, 13.9):
            ear = QPainterPath(QPointF(ex, 4.2))
            ear.lineTo(ex + 0.1, 2.3)
            ear.lineTo(ex + 0.8, 4.0)
            ear.closeSubpath()
            p.drawPath(ear)
        p.setBrush(cream)
        p.drawEllipse(QPointF(8.6, 9.1), 2.5, 1.2)       # belly
        lp = QPen(red, 1.1)
        lp.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(lp)
        sw = math.sin(g)
        p.drawLine(QPointF(6.0, 9.3), QPointF(5.0 + 1.5 * sw, 11.6))
        p.drawLine(QPointF(11.6, 9.1), QPointF(12.1 + 1.2 * sw, 11.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(13.7, 5.2), 0.35, 0.35)    # eye
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(14.7, 5.9), 0.3, 0.3)      # nose
        p.restore()

    def _draw_dragonfly(self, p, wk):
        g = wk["gait"]
        body_c = _lerp("#2E8B8B", "#1E4B54", self._dark * 0.7)
        wing = QColor(_CHALK)
        wing.setAlphaF(0.45)
        p.setPen(Qt.PenStyle.NoPen)
        flut = 0.5 + 0.5 * abs(math.sin(g * 6))
        for wx in (5.2, 6.8):                    # two blurred wing pairs
            p.save()
            p.translate(wx, 3.2)
            p.scale(1.0, flut)
            p.setBrush(wing)
            p.drawEllipse(QPointF(-1.6, -1.0), 2.6, 0.7)
            p.drawEllipse(QPointF(1.2, -1.1), 2.4, 0.65)
            p.restore()
        pen = QPen(body_c, 0.85)                 # needle abdomen
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(0.6, 4.4), QPointF(7.4, 3.6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body_c)
        p.drawEllipse(QPointF(8.3, 3.5), 1.15, 0.8)    # thorax
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(9.6, 3.3), 0.75, 0.7)    # big eyes
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(9.8, 3.1), 0.28, 0.28)

    def _draw_barn_owl(self, p, wk):
        g = wk["gait"]
        # Ghost-pale, buoyant, slow — quartering low over the grass.
        buff = _lerp("#D9C29A", "#EDE6D8", self._dark * 0.8)
        white = _lerp("#F2EDE2", _CHALK, self._dark)
        env = max(0.0, math.sin(g * 0.35))
        flap = (8 + 16 * env) * math.sin(g)
        p.setPen(Qt.PenStyle.NoPen)
        for sweep, ang, op in ((-1, flap, 1.0), (1, -flap * 0.75, 0.85)):
            p.save()
            p.translate(15.5, 4.4)
            p.rotate(ang)
            p.setOpacity(op)
            wing = QPainterPath(QPointF(0, 0))
            wing.quadTo(4.0 * sweep, -4.4, 8.5 * sweep, -4.0)
            wing.quadTo(11.4 * sweep, -3.4, 11.8 * sweep, -2.0)
            wing.quadTo(7.0 * sweep, 0.4, 0, 1.4)
            wing.closeSubpath()
            p.setBrush(buff)
            p.drawPath(wing)
            p.restore()
        p.setOpacity(1.0)
        p.setBrush(white)
        p.drawEllipse(QPointF(15.8, 5.0), 4.0, 2.1)   # white underparts
        p.setBrush(buff)
        tail = QPainterPath(QPointF(12.2, 4.6))
        tail.lineTo(9.6, 5.8)
        tail.lineTo(12.4, 6.6)
        tail.closeSubpath()
        p.drawPath(tail)
        p.drawEllipse(QPointF(19.8, 4.2), 2.2, 2.1)   # head
        p.setBrush(white)
        p.drawEllipse(QPointF(20.5, 4.2), 1.5, 1.65)  # heart face
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(20.8, 4.0), 0.38, 0.38)

    def _draw_kite(self, p, wk):
        g = wk["gait"]
        # Red kite: long angled wings, rufous body, pale head, and the
        # unmistakable forked tail. Soars in slow banked circles.
        c = _lerp("#8A4A28", _INK, self._dark * 0.75)
        pale = _lerp("#D8CFC2", "#8C8478", self._dark * 0.6)
        pulse = 0.9 + 0.14 * math.sin(g * 0.19)
        p.translate(16, 5)
        p.scale(pulse, pulse)
        p.rotate(6 * math.sin(g * 0.31))
        p.translate(-16, -5)
        flap = 7 * math.sin(g * 0.5)             # mostly soaring
        p.setPen(Qt.PenStyle.NoPen)
        for sweep, ang, op in ((-1, flap, 1.0), (1, -flap * 0.8, 0.85)):
            p.save()
            p.translate(16, 5)
            p.rotate(ang)
            wing = QPainterPath(QPointF(0, 0))
            wing.quadTo(5 * sweep, -3.0, 9.5 * sweep, -3.4)
            wing.lineTo(13.8 * sweep, -2.2)
            wing.lineTo(12.6 * sweep, -1.5)
            wing.lineTo(13.9 * sweep, -1.0)
            wing.quadTo(6 * sweep, 0.8, 0, 1.3)
            wing.closeSubpath()
            p.setOpacity(op)
            p.setBrush(c)
            p.drawPath(wing)
            p.restore()
        p.setOpacity(1.0)
        p.setBrush(c)
        p.drawEllipse(QPointF(16, 5.2), 3.8, 1.5)
        fork = QPainterPath(QPointF(12.6, 4.6))   # the forked tail
        fork.lineTo(8.2, 3.4)
        fork.lineTo(10.4, 5.2)
        fork.lineTo(8.4, 6.9)
        fork.lineTo(12.6, 5.8)
        fork.closeSubpath()
        p.drawPath(fork)
        p.setBrush(pale)
        p.drawEllipse(QPointF(19.6, 4.9), 1.4, 1.1)
        p.setBrush(QColor(_INK))
        p.drawEllipse(QPointF(20.6, 4.8), 0.4, 0.4)

    def _draw_grass_snake(self, p, wk, swim=False):
        self._draw_snake(p, wk, swim, "#5A7A46", collar=True)

    def _draw_adder(self, p, wk, swim=False):
        self._draw_snake(p, wk, swim, "#6E6258", zigzag=True)

    def _draw_snake(self, p, wk, swim, base, collar=False, zigzag=False):
        """Shared serpent body: a travelling wave slithers along the
        spine (tail-heavy, so the head runs steady), tapering from head
        to tail. Grass snakes carry the yellow collar, adders the dark
        dorsal zigzag. Both swim with their head periscoped up."""
        g = wk["gait"]
        c = _lerp(base, _INK, self._dark * 0.7)
        hold = wk["pause_t"] > 0
        if hold and wk["pose"] == "coil" and not swim:
            # Basking coil: distinct spaced loops, head resting on the
            # rim, the odd tongue flick.
            pen = QPen(c, 1.3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for rr, yo in ((3.7, 0.0), (2.35, 0.55), (1.1, 1.0)):
                p.drawEllipse(QPointF(10.0, 3.9 + yo), rr, rr * 0.6)
            neck = QPainterPath(QPointF(11.6, 2.4))
            neck.quadTo(12.6, 1.9, 13.4, 2.0)
            p.drawPath(neck)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(13.9, 2.1), 1.0, 0.7)
            if math.sin(g * 1.5) > 0.82:
                p.setPen(QPen(QColor(_BELLY), 0.4))
                p.drawLine(QPointF(14.9, 2.0), QPointF(16.0, 1.8))
                p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(_CHALK))
            p.drawEllipse(QPointF(14.2, 1.9), 0.26, 0.26)
            return
        n = 11
        amp = 0.7 if swim else 1.0
        yb = 4.6 if swim else 4.2
        pts = []
        for i in range(n):
            t = i / (n - 1.0)
            pts.append(QPointF(
                2 + t * 20,
                yb + amp * math.sin(g * 2.2 - i * 1.05) * (0.45 + 0.55 * (1 - t))))
        if swim:
            wake = QColor(_CHALK)
            wake.setAlphaF(0.5)
            pen = QPen(wake, 0.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for wx in (-1.0, -3.6):
                arc = QPainterPath(QPointF(wx, 4.9))
                arc.quadTo(wx - 1.8, 5.6, wx - 3.6, 5.2)
                p.drawPath(arc)
        # Smooth tapering body: one polyline stroked in three widths so
        # it thins to the tail with no visible segmentation.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i0, i1, wpen in ((0, 4, 0.85), (3, 8, 1.5), (7, 10, 2.0)):
            seg = QPainterPath(pts[i0])
            for i in range(i0 + 1, i1):
                seg.quadTo(pts[i], (pts[i] + pts[min(i + 1, n - 1)]) / 2)
            seg.lineTo(pts[i1])
            pen = QPen(c, wpen)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPath(seg)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        head = QPointF(pts[-1].x() + 1.2,
                       pts[-1].y() - (0.9 if swim else 0.1))
        p.drawEllipse(head, 1.25, 0.85)
        if collar:
            ring = QColor("#E8C63F")
            if self._dark > 0.5:
                ring = _lerp(ring.name(), _INK, (self._dark - 0.5))
            cp = QPen(ring, 0.75)
            cp.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(cp)
            p.drawLine(QPointF(head.x() - 1.45, head.y() - 0.8),
                       QPointF(head.x() - 1.65, head.y() + 0.8))
            p.setPen(Qt.PenStyle.NoPen)
        if zigzag:
            # Dorsal zigzag riding ON the spine, small amplitude.
            zz = QPen(_lerp("#2E2620", _INK, self._dark * 0.4), 0.55)
            zz.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(zz)
            p.setBrush(Qt.BrushStyle.NoBrush)
            zig = QPainterPath(pts[2] + QPointF(0, -0.15))
            for i in range(3, n):
                off = -0.5 if i % 2 else 0.25
                zig.lineTo(pts[i] + QPointF(0, off))
            p.drawPath(zig)
            p.setPen(Qt.PenStyle.NoPen)
        if math.sin(g * 1.3 + 1.0) > 0.86:      # tongue tasting the air
            tongue = QPen(QColor(_BELLY), 0.4)
            p.setPen(tongue)
            p.drawLine(QPointF(head.x() + 1.1, head.y()),
                       QPointF(head.x() + 2.2, head.y() - 0.3))
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CHALK))
        p.drawEllipse(QPointF(head.x() + 0.35, head.y() - 0.25),
                      0.28, 0.28)
