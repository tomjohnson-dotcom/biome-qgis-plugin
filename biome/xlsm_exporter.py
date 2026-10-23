# xlsm_exporter.py
# Reads QGIS BNG layers and writes pre-filled data into the statutory
# biodiversity metric Excel (.xlsm) template.
#
# Tab mapping (metric sheet names → layer / retention combos):
#   A-1 On-Site Habitat Baseline   ← baseline_area,     all features
#   A-2 On-Site Habitat Creation   ← proposed_area,     retention != "Retained"
#   A-3 On-Site Habitat Enhancement← baseline_area,     retention == "Enhanced"
#   B-1 On-site Hedge Baseline     ← baseline_hedgerow, all features
#   B-2 On-site Hedge Creation     ← baseline_hedgerow, retention == "Lost"
#   B-3 On-site Hedge Enhancement  ← baseline_hedgerow, retention == "Enhanced"
#   C-1 On-site Watercourse Baseline← baseline_water,   all features
#   C-2 On-site Watercourse Creation← baseline_water,   retention == "Lost"
#   C-3 On-site Watercourse Enhancement← baseline_water,retention == "Enhanced"
#
# Trees are folded into area sheets: each tree point contributes its
# statutory area equivalent (ha) from TREE_SIZE_AREAS as a single row.
#
# Fields left blank on the map are simply omitted from the metric rows.

import os
import re
import shutil
import traceback
from datetime import date
from math import pi

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QProgressBar,
    QDialogButtonBox, QLineEdit, QFormLayout,
)
from qgis.core import QgsProject, QgsDistanceArea, QgsUnitTypes, QgsMessageLog, Qgis
from qgis.PyQt.QtCore import QSettings

from .config import measurement_ellipsoid

try:
    import openpyxl
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

from .layer_manager import (LAYER_NAMES, TREE_SIZE_AREAS, tree_size_from_sub_type,
                            tree_location_from_sub_type, DEFAULT_STRATEGIC_SIGNIFICANCE)


# ── Sheet names (as they appear in the .xlsm tab labels) ────────────────────
SHEET_NAMES = {
    "area_baseline":  "A-1 On-Site Habitat Baseline",
    "area_creation":  "A-2 On-Site Habitat Creation",
    "area_enhance":   "A-3 On-Site Habitat Enhancement",
    "hedge_baseline": "B-1 On-Site Hedge Baseline",
    "hedge_creation": "B-2 On-Site Hedge Creation",
    "hedge_enhance":  "B-3 On-Site Hedge Enhancement",
    "water_baseline": "C-1 On-Site WaterC' Baseline",
    "water_creation": "C-2 On-Site WaterC' Creation",
    "water_enhance":  "C-3 On-Site WaterC' Enhancement",
}

# First data row (1-based) for each sheet — verified against template
DATA_START_ROW = {
    "area_baseline":  11,   # A-1: headers row 10, data row 11+
    "area_creation":  11,   # A-2: headers row 10, data row 11+
    "area_enhance":   12,   # A-3: auto-populated, nothing written
    "hedge_baseline": 10,   # B-1: headers row 9,  data row 10+
    "hedge_creation": 12,   # B-2: headers row 11, data row 12+
    "hedge_enhance":  12,   # B-3: auto-populated, nothing written
    "water_baseline": 10,   # C-1: headers row 9,  data row 10+
    "water_creation": 12,   # C-2: headers row 11, data row 12+
    "water_enhance":  12,   # C-3: auto-populated, nothing written
}

# ── Column mappings (1-based column → field name) ─────────────────────────────
# Based on direct inspection of Blank_BNG_Metric.xlsm A-1 sheet (row 10 headers):
#   E=Broad Habitat, F=Habitat Type, G=Irreplaceable habitat, H=Area (hectares)
#   I=Distinctiveness (formula), J=Score (formula), K=Condition, L=Score (formula)
#   M=Strategic significance, N=Strat sig label (formula), O=Multiplier (formula)
#   P=Required Action (formula), Q=Total habitat units (formula)
#   R=spacer, S=Area retained, T=Area enhanced
#   U=Baseline units retained (formula), V=Baseline units enhanced (formula)
#   W=Area habitat lost (formula: H-S-T), X=Units lost (formula)
#
# We write ONLY the white (user-input) cells: E, F, G, H, K, M, S, T
# All formula/shaded columns (I,J,L,N,O,P,Q,U,V,W,X) are left for Excel to calculate.
# Column letters → 1-based numbers: E=5, F=6, G=7, H=8, K=11, M=13, S=19, T=20

# A-1 Baseline: text + numeric input columns
AREA_BASELINE_COLS = [
    (5,  "broad_habitat",        None),   # E — Broad Habitat
    (6,  "sub_habitat_type",     None),   # F — Habitat Type
    (7,  "irreplaceable_habitat",None),   # G — Irreplaceable habitat
    (8,  "area_ha",              "0.000000"),  # H — Area (hectares)
    (11, "condition",            None),   # K — Condition
    (13, "strategic_significance",None),  # M — Strategic significance
    (19, "area_retained_ha",     "0.000000"),  # S — Area retained
    (20, "area_enhanced_ha",     "0.000000"),  # T — Area enhanced
]

# A-2 Creation: only Area (ha) is numeric; proposed habitat is a dropdown
AREA_CREATION_COLS = [
    (7,  "area_ha"),           # Area (hectares)
]

# A-3 Enhancement: fully auto-populated from A-1 via VLOOKUP — write nothing
AREA_ENHANCEMENT_COLS = []

# B-1 Hedge Baseline: Length (km), retained, enhanced
HEDGE_BASELINE_COLS = [
    (5,  "length_km"),          # Length (km)
    (16, "length_retained_km"), # Length retained
    (17, "length_enhanced_km"), # Length enhanced
]

# B-2 Hedge Creation: Length (km) only
HEDGE_CREATION_COLS = [
    (5,  "length_km"),          # Length (km)
]

# B-3 Hedge Enhancement: fully auto-populated from B-1 — write nothing
HEDGE_ENHANCEMENT_COLS = []

# C-1 Watercourse Baseline: Length (km), retained, enhanced
WATER_BASELINE_COLS = [
    (5,  "length_km"),          # Length (km)
    (21, "length_retained_km"), # Length retained
    (22, "length_enhanced_km"), # Length enhanced
]

# C-2 Watercourse Creation: Length (km) only
WATER_CREATION_COLS = [
    (4,  "length_km"),          # Length (km)
]

# C-3 Watercourse Enhancement: fully auto-populated from C-1 — write nothing
WATER_ENHANCEMENT_COLS = []

# Map plugin habitat_type category → default metric Broad Habitat name.
# Sub-types that need a different broad habitat are handled by
# BROAD_HABITAT_OVERRIDE in habitat_data.py (checked first by _broad()).
BROAD_HABITAT_MAP = {
    "Grassland":                "Grassland",
    "Woodland":                 "Woodland and forest",
    "Heathland & Shrub":        "Heathland and shrub",
    "Wetland":                  "Wetland",
    "Lakes":                    "Lakes",
    "Coastal & Intertidal":     "Intertidal sediment",   # fallback; most coastal types have an override
    "Sparsely Vegetated Land":  "Sparsely vegetated land",
    "Cropland":                 "Cropland",
    "Urban":                    "Urban",
    "Individual Trees":         "Individual trees",
    "Hedgerow":                 "Hedgerows",
    "Watercourse":              "Watercourse",
    # Legacy names — keep so existing saved projects don't break
    "Scrub & Heathland":        "Heathland and shrub",
    "Wetland & Peatland":       "Wetland",
    "Open Water":               "Lakes",
    "Rock & Bare Ground":       "Sparsely vegetated land",
    "Arable & Cultivated":      "Cropland",
    "Urban & Built":            "Urban",
    "Green Infrastructure":     "Urban",
}

# ── Bundled template path ─────────────────────────────────────────────────────

_BUNDLED_TEMPLATE = os.path.join(os.path.dirname(__file__), "BNG_Metric_Template.xlsm")


# ── Project details ("Start" sheet) ───────────────────────────────────────────
# The statutory metric's "Start" tab holds the project-details block. Labels sit
# in column D; the matching white input cells are in column F, rows 11–19. We
# prompt the user for these when exporting the filled metric and write whatever
# they supply (blank fields are left blank). "Date of metric completion" is
# auto-filled with today's date; "Planning authority" pre-fills from the LPA the
# Desk Study screen resolved for the redline centroid (editable, and a hand-typed
# value is remembered per-project and wins over the auto-fill).
#
#   F11 Planning authority            F16 Completed by
#   F12 Project name                  F17 Date of metric completion (auto)
#   F13 Applicant                     F18 Reviewer
#   F14 Application type              F19 Calculation iteration
#   F15 Planning application reference
#
# (key, label, row, is_auto_date)
PROJECT_DETAIL_FIELDS = [
    ("planning_authority",             "Planning authority",             11, False),
    ("project_name",                   "Project name",                   12, False),
    ("applicant",                      "Applicant",                      13, False),
    ("application_type",               "Application type",               14, False),
    ("planning_application_reference", "Planning application reference", 15, False),
    ("completed_by",                   "Completed by",                   16, False),
    ("date_of_metric_completion",      "Date of metric completion",      17, True),
    ("reviewer",                       "Reviewer",                       18, False),
    ("calculation_iteration",          "Calculation iteration",          19, False),
]

# Column F = 6, first project-details row = 11. Each detail is written as its own
# "row" (one per worksheet row) through the existing patch_metric writer.
_START_WRITE_COLS = [(6, "value", None)]
_START_DATA_START_ROW = 11

# Base file name retained for the separate blank-metric download.
BLANK_METRIC_BASENAME = "The_Statutory_Metric_Macro_Enabled_1_0_4"


# ── Shared memory with the map exporter ───────────────────────────────────────
# Four project-detail fields are the same information the Final Map exporter
# already collects, so the two tools share their memory:
#
#   Metric "Applicant"     ↔ Map "Client Name"   (stored per QGIS project)
#   Metric "Project name"  ↔ Map "Project No."   (stored per QGIS project)
#   Metric "Completed by"  ↔ Map "Created By"    (QSettings, every project)
#   Metric "Reviewer"      ↔ Map "Approved By"   (QSettings, every project)
#
# Client / project number live in the map exporter's project-backed _SESSION
# (stored in the QGIS project, so they survive a plugin reload and reopening the
# project, and are kept separate per project). Completed-by / reviewer are stored
# in QSettings, so — like the map exporter's Created By — they are remembered for
# future metrics across projects and restarts. Imports are lazy so there is never
# a load-order/circular concern; if the map exporter can't be reached we fall back
# to a private session dict and the same QSettings keys.

_FALLBACK_SESSION = {"client": "", "proj_no": ""}


def _shared_session():
    """The map exporter's project-backed client / proj_no store (dict-like)."""
    try:
        from . import map_exporter
        return map_exporter._SESSION
    except Exception:
        return _FALLBACK_SESSION


def _settings_keys():
    """(created-by key, approved-by key) — the same keys the map exporter uses."""
    try:
        from . import map_exporter
        return map_exporter.SETTINGS_KEY_CREATED, map_exporter.SETTINGS_KEY_APPROVED
    except Exception:
        return "BiomeMapper/createdBy", "BiomeMapper/approvedBy"


# Metric-only project details that should also be remembered per-project (like
# the shared Applicant / Project name above, but specific to the metric).
# Completed-by / Reviewer are excluded — they live in QSettings and carry across
# every project. Date of metric completion is auto-filled, so it is not stored.
_PROJECT_ONLY_KEYS = (
    "planning_authority",
    "application_type",
    "planning_application_reference",
    "calculation_iteration",
)


def _project_io():
    """(getter, setter) for per-project values, shared with the map exporter so
    both tools read/write the same project entries. Falls back to a direct
    QgsProject implementation if the map exporter can't be imported."""
    try:
        from . import map_exporter
        return map_exporter.project_value, map_exporter.set_project_value
    except Exception:
        def _get(key, default=""):
            try:
                val, _ = QgsProject.instance().readEntry("BiomeMapper", key, default)
                return val if val else default
            except Exception:
                return default

        def _set(key, value):
            try:
                QgsProject.instance().writeEntry("BiomeMapper", key, value or "")
            except Exception:
                pass

        return _get, _set


def desk_study_lpa():
    """The local planning authority the Desk Study screen resolved for the
    redline centroid (persisted per-project by main_panel under "desk/lpa"),
    or "" if it has not been resolved yet."""
    get_proj, _set_proj = _project_io()
    return get_proj("desk/lpa", "") or ""


def load_remembered_details():
    """Pre-fill values shared with the map exporter, plus the metric-only fields
    remembered per-project, as a {field-key: value}."""
    settings = QSettings()
    sess = _shared_session()
    k_created, k_approved = _settings_keys()
    get_proj, _set_proj = _project_io()
    details = {
        "applicant":    sess.get("client", "") or "",
        "project_name": sess.get("proj_no", "") or "",
        "completed_by": settings.value(k_created, "") or "",
        "reviewer":     settings.value(k_approved, "") or "",
    }
    for k in _PROJECT_ONLY_KEYS:
        details[k] = get_proj("metric/" + k, "") or ""
    # Planning authority: fall back to the LPA the Desk Study screen resolved
    # for the redline centroid, so the field auto-populates on first export.
    if not details["planning_authority"]:
        details["planning_authority"] = desk_study_lpa()
    return details


def remember_details(details):
    """Persist the entered details. Applicant / project name and the metric-only
    fields are remembered per-project (stored in the QGIS project, so they
    survive a plugin reload and reopening the project); completed-by / reviewer
    are written to QSettings (remembered across every project). Blank values are
    not stored, so they never overwrite a previously remembered value."""
    details = details or {}
    sess = _shared_session()
    settings = QSettings()
    k_created, k_approved = _settings_keys()
    _get_proj, set_proj = _project_io()

    applicant    = (details.get("applicant") or "").strip()
    project_name = (details.get("project_name") or "").strip()
    completed_by = (details.get("completed_by") or "").strip()
    reviewer     = (details.get("reviewer") or "").strip()

    if applicant:
        sess["client"] = applicant
    if project_name:
        sess["proj_no"] = project_name
    if completed_by:
        settings.setValue(k_created, completed_by)
    if reviewer:
        settings.setValue(k_approved, reviewer)
    desk_lpa = desk_study_lpa()
    for k in _PROJECT_ONLY_KEYS:
        v = (details.get(k) or "").strip()
        if not v:
            continue
        if k == "planning_authority" and v == desk_lpa:
            # Auto-filled from the desk study — don't freeze it, so the field
            # keeps tracking the resolved LPA if the redline moves. A value the
            # user edits differs from the desk LPA and is stored as usual.
            continue
        set_proj("metric/" + k, v)


def today_completion_date():
    """Today's date in UK DD/MM/YYYY form, for the completion-date cell."""
    return date.today().strftime("%d/%m/%Y")


def build_metric_filename(client_name, project_number, includes_offsite=False):
    """Suggested filled-metric file name (no extension).

    ``Client Name - Project Number - BNG Metric - YYYY-MM-DD``. Blank client
    or project-number components are omitted cleanly. Characters prohibited in
    Windows file names are removed while ordinary spaces are retained.
    """
    def _safe(value):
        return re.sub(r'[\\/:*?"<>|]', "", (value or "").strip()).strip()

    parts = [p for p in (_safe(client_name), _safe(project_number)) if p]
    parts.append("BNG Metric")
    if includes_offsite:
        parts.append("Includes Off-site Units")
    parts.append(date.today().strftime("%Y-%m-%d"))
    return " - ".join(parts)


def _project_detail_rows(details):
    """Turn a {key: value} details dict into the row list patch_metric expects.

    One dict per worksheet row (F11–F19 in order). Blank values are passed
    through as "" so the writer leaves those cells untouched.
    """
    details = details or {}
    rows = []
    for key, _label, _row, is_auto in PROJECT_DETAIL_FIELDS:
        val = details.get(key, "")
        val = "" if val is None else str(val).strip()
        rows.append({"value": val})
    return rows


def _write_area_summary(wb, rows_bl, rows_cr):
    """Write a polished area balance summary sheet (excludes trees)."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, GradientFill
    from openpyxl.utils import get_column_letter

    # Palette
    HDR_DARK  = "1B5E20"   # header bg
    HDR_LIGHT = "2E7D32"   # subheader / totals
    PASS_BG   = "E8F5E9"   # balanced row bg
    PASS_TXT  = "1B5E20"
    FAIL_BG   = "FFEBEE"   # shortfall row bg
    FAIL_TXT  = "B71C1C"
    WARN_BG   = "FFF8E1"   # surplus row bg
    WARN_TXT  = "E65100"
    ALT_BG    = "F5F5F5"   # alternating neutral row bg
    TOTAL_BG  = "37474F"   # dark total row
    NOTE_BG   = "ECEFF1"

    ws = wb.create_sheet(title="Area Balance Check")
    thin   = Side(style="thin",   color="E0E0E0")
    medium = Side(style="medium", color="BDBDBD")
    border = Border(left=thin, right=thin, bottom=thin, top=thin)
    total_border = Border(left=medium, right=medium,
                          bottom=medium, top=medium)

    def _c(row, col, val, bg="FFFFFF", bold=False, align="left",
           num_fmt=None, fc="212121", italic=False, sz=9,
           bdr=None, height=None):
        c = ws.cell(row=row, column=col, value=val)
        c.font      = Font(name="Arial", size=sz, bold=bold, italic=italic,
                           color=fc)
        c.fill      = PatternFill("solid", fgColor=bg)
        c.alignment = Alignment(horizontal=align, vertical="center",
                                wrap_text=(col <= 2))
        c.border    = bdr or border
        if num_fmt:
            c.number_format = num_fmt
        if height:
            ws.row_dimensions[row].height = height
        return c

    # ── Title row ─────────────────────────────────────────────────────
    ws.row_dimensions[1].height = 28
    _c(1, 1, "AREA HABITAT BALANCE CHECK", bg=HDR_DARK, bold=False,
       fc="FFFFFF", sz=11, align="left")
    _c(1, 2, "", bg=HDR_DARK)
    _c(1, 3, "", bg=HDR_DARK)
    _c(1, 4, "", bg=HDR_DARK)
    _c(1, 5, "", bg=HDR_DARK)
    _c(1, 6, "Lost vs Created  |  Trees, hedgerows & watercourses excluded",
       bg=HDR_DARK, italic=True, fc="A5D6A7", sz=8, align="right")

    # ── Column headers ────────────────────────────────────────────────
    ws.row_dimensions[2].height = 32
    col_specs = [
        ("Broad Habitat",    "left"),
        ("Habitat Type",     "left"),
        ("Area Lost (ha)",   "right"),
        ("Area Created (ha)","right"),
        ("Difference (ha)",  "right"),
        ("Status",           "center"),
    ]
    for ci, (h, al) in enumerate(col_specs, 1):
        c = ws.cell(row=2, column=ci, value=h)
        c.font      = Font(name="Arial", bold=False, size=9, color="FFFFFF")
        c.fill      = PatternFill("solid", fgColor=HDR_LIGHT)
        c.alignment = Alignment(horizontal=al, vertical="center")
        c.border    = border

    # ── Aggregate totals using spatially-derived area fields ──────────
    # area_lost_ha = baseline_total - retained - enhanced (set by spatial engine)
    # grand_created = sum of creation row areas (different habitat type fragments)
    grand_lost = grand_created = 0.0
    for row in rows_bl:
        if (row.get("broad_habitat") or "").strip() == "Individual trees":
            continue
        try:
            grand_lost = round(grand_lost + float(row.get("area_lost_ha") or 0), 6)
        except (ValueError, TypeError):
            pass
    for row in rows_cr:
        if (row.get("broad_habitat") or "").strip() == "Individual trees":
            continue
        try:
            grand_created = round(grand_created + float(row.get("area_ha") or 0), 6)
        except (ValueError, TypeError):
            pass

    # ── Single data row ───────────────────────────────────────────────
    diff     = round(grand_created - grand_lost, 6)
    balanced = abs(diff) < 0.0001  # <1m² treated as balanced
    if balanced:
        row_bg, fc, status = PASS_BG, PASS_TXT, "\u2713  Balanced"
    elif diff < 0:
        row_bg, fc, status = FAIL_BG, FAIL_TXT, "\u2717  Shortfall"
    else:
        row_bg, fc, status = WARN_BG, WARN_TXT, "\u26a0  Surplus"

    ws.row_dimensions[3].height = 22
    _c(3, 1, "All Area Habitats", bg=row_bg, bold=False, fc=fc)
    _c(3, 2, "(trees, hedgerows & watercourses excluded)",
       bg=row_bg, italic=True, fc=fc, sz=8)
    _c(3, 3, grand_lost,    bg=row_bg, align="right", num_fmt="0.0000", fc=fc, bold=False)
    _c(3, 4, grand_created, bg=row_bg, align="right", num_fmt="0.0000", fc=fc, bold=False)
    _c(3, 5, diff,          bg=row_bg, align="right", num_fmt="0.0000", fc=fc, bold=False)
    _c(3, 6, status,        bg=row_bg, align="center", fc=fc, bold=False, sz=10)

    # ── Total row ─────────────────────────────────────────────────────
    total_row  = 5
    grand_diff = round(grand_created - grand_lost, 6)
    grand_bal  = abs(grand_diff) < 0.000001
    if grand_bal:
        g_bg, g_fc, g_status = "43A047", "FFFFFF", "✓  Balanced"
    elif grand_diff < 0:
        g_bg, g_fc, g_status = "E53935", "FFFFFF", "✗  Shortfall"
    else:
        g_bg, g_fc, g_status = "FB8C00", "FFFFFF", "⚠  Surplus"

    ws.row_dimensions[total_row].height = 22
    for ci in range(1, 7):
        c = ws.cell(row=total_row, column=ci)
        c.fill   = PatternFill("solid", fgColor=TOTAL_BG)
        c.border = total_border
        c.font   = Font(name="Arial", size=10, bold=False, color="FFFFFF")
        c.alignment = Alignment(vertical="center",
                                horizontal=["left","left","right","right","right","center"][ci-1])

    ws.cell(row=total_row, column=1).value = "TOTAL"
    ws.cell(row=total_row, column=2).value = ""
    ws.cell(row=total_row, column=3).value = round(grand_lost, 4)
    ws.cell(row=total_row, column=3).number_format = "0.0000"
    ws.cell(row=total_row, column=4).value = round(grand_created, 4)
    ws.cell(row=total_row, column=4).number_format = "0.0000"
    ws.cell(row=total_row, column=5).value = grand_diff
    ws.cell(row=total_row, column=5).number_format = "0.0000"

    # Status pill in col 6 uses outcome colour
    sc = ws.cell(row=total_row, column=6, value=g_status)
    sc.fill  = PatternFill("solid", fgColor=g_bg)
    sc.font  = Font(name="Arial", size=10, bold=False, color=g_fc)
    sc.alignment = Alignment(horizontal="center", vertical="center")
    sc.border = total_border

    # ── Separator ─────────────────────────────────────────────────────
    sep_row = total_row + 2
    ws.row_dimensions[sep_row].height = 6
    for ci in range(1, 7):
        ws.cell(row=sep_row, column=ci).fill = PatternFill("solid", fgColor="E8EAF6")

    # ── Legend ────────────────────────────────────────────────────────
    lr = sep_row + 1
    _c(lr, 1, "LEGEND", bg=NOTE_BG, bold=False, fc="546E7A", sz=8)
    for ci in range(2, 7):
        _c(lr, ci, "", bg=NOTE_BG)
    ws.row_dimensions[lr].height = 16

    legend_items = [
        (PASS_BG, PASS_TXT, "✓  Balanced",  "Lost area equals created area"),
        (WARN_BG, WARN_TXT, "⚠  Surplus",   "More created than lost — net gain"),
        (FAIL_BG, FAIL_TXT, "✗  Shortfall", "Less created than lost — deficit requires attention"),
    ]
    for offset, (bg, fc, label, desc) in enumerate(legend_items, lr + 1):
        _c(offset, 1, label, bg=bg, bold=False, fc=fc, sz=8)
        _c(offset, 2, desc,  bg=bg, fc=fc, sz=8)
        for ci in range(3, 7):
            _c(offset, ci, "", bg=bg)
        ws.row_dimensions[offset].height = 15

    # ── Column widths ─────────────────────────────────────────────────
    for ci, w in enumerate([24, 34, 16, 16, 16, 15], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    ws.freeze_panes = "A3"
    return ws




def _write_sheet(wb, title, columns, rows, header_color, alt_color="EAF4EA",
                 agg_key=None):
    """Write a sheet with:
      1. Full list — header + every individual row + TOTAL
      2. 3 blank rows
      3. 'Aggregated Summary' label
      4. Aggregated rows (keyed by agg_key) + TOTAL  (no repeated header)

    agg_key: tuple of field names to group on.  Blanks match blanks.
             If None, the aggregated section is omitted.
    """
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(title=title)

    thin   = Side(style="thin",   color="CCCCCC")
    medium = Side(style="medium", color="AAAAAA")
    border = Border(left=thin, right=thin, bottom=thin, top=thin)

    col_widths = {
        "Broad Habitat": 22, "Habitat Type": 30, "Habitat Ref": 14,
        "Irreplaceable habitat": 16, "Irreplaceable?": 13,
        "Area (ha)": 12, "Area (hectares)": 14,
        "Area Retained (ha)": 18, "Area Enhanced (ha)": 18,
        "Area retained": 16, "Area enhanced": 16,
        "Condition": 18, "Strategic Significance": 26,
        "Strategic significance": 26, "Strategic significance multiplier": 16,
        "Required Action to Meet Trading Rules": 30,
        "Distinctiveness": 16, "Score": 10,
        "Total habitat units": 16, "Baseline units retained": 18,
        "Baseline units enhanced": 18, "Area habitat lost": 16, "Units lost": 12,
        "Retention": 12, "User Comments": 30,
        "Habitat Type (Proposed)": 30, "Condition (Proposed)": 18,
        "Length (km)": 12, "Length Retained (km)": 18, "Length Enhanced (km)": 18,
        "Watercourse Encroachment": 22, "Riparian Encroachment L/R": 24,
        "Features Joined": 14, "Tree Type": 24, "Size": 12,
        "Location": 14, "Count": 10, "Area / Tree (ha)": 16,
        "Total Area (ha)": 16, "": 4,
    }

    # ── helper: write a single data cell ─────────────────────────────────
    def _data_cell(r, c, val, fmt, bg):
        if val is None:
            val = ""
        if fmt and val != "":
            try:
                val = float(val)
            except (ValueError, TypeError):
                pass
        cell = ws.cell(row=r, column=c, value=val)
        cell.font      = Font(name="Arial", size=9)
        cell.fill      = PatternFill("solid", fgColor=bg)
        cell.alignment = Alignment(vertical="center")
        cell.border    = border
        if fmt and val != "":
            cell.number_format = fmt
        return cell

    # ── helper: write a TOTAL row ─────────────────────────────────────────
    def _total_row(r, data_start, data_end, total_bg="D9D9D9"):
        for col_idx, (label, field, fmt) in enumerate(columns, 1):
            cell = ws.cell(row=r, column=col_idx)
            cl   = get_column_letter(col_idx)
            if field == "_blank":
                cell.value = ""
                cell.fill  = PatternFill("solid", fgColor="EEEEEE")
            elif field in _SUM_FIELDS:
                cell.value        = f"=SUM({cl}{data_start}:{cl}{data_end})"
                cell.number_format = fmt or "0.000000"
                cell.fill  = PatternFill("solid", fgColor=total_bg)
            elif field == "_count":
                cell.value = f"=SUM({cl}{data_start}:{cl}{data_end})"
                cell.fill  = PatternFill("solid", fgColor=total_bg)
            elif col_idx == 1:
                cell.value = "TOTAL"
                cell.fill  = PatternFill("solid", fgColor=total_bg)
            else:
                cell.value = ""
                cell.fill  = PatternFill("solid", fgColor=total_bg)
            cell.font      = Font(name="Arial", bold=False, size=9)
            cell.border    = border
            cell.alignment = Alignment(
                vertical="center",
                horizontal="right" if field in _SUM_FIELDS or field == "_count" else "left"
            )

    # ══ SECTION 1 — Full list ════════════════════════════════════════════

    # Header row (row 1)
    for col_idx, (label, field, fmt) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font      = Font(name="Arial", bold=False, size=9, color="FFFFFF")
        cell.fill      = PatternFill("solid", fgColor=header_color)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = border
    ws.row_dimensions[1].height = 30

    # Data rows (rows 2 … 1+n)
    for row_idx, row_data in enumerate(rows, 2):
        bg = "FFFFFF" if row_idx % 2 == 0 else alt_color
        for col_idx, (label, field, fmt) in enumerate(columns, 1):
            if field == "_blank":
                # Shaded placeholder — leave value empty, grey background
                cell = ws.cell(row=row_idx, column=col_idx, value="")
                cell.font      = Font(name="Arial", size=9)
                cell.fill      = PatternFill("solid", fgColor="EEEEEE")
                cell.alignment = Alignment(vertical="center")
                cell.border    = border
            else:
                _data_cell(row_idx, col_idx, row_data.get(field, ""), fmt, bg)

    # TOTAL row
    full_total_row = len(rows) + 2
    if rows:
        _total_row(full_total_row, 2, full_total_row - 1)

    # Column widths
    for col_idx, (label, field, fmt) in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = col_widths.get(label, 16)

    ws.freeze_panes = "A2"

    # ══ SECTION 2 — Aggregated Summary ══════════════════════════════════

    if agg_key:
        agg_rows = _aggregate_rows_by_key(rows, agg_key)

        # 3 blank rows gap
        gap_start = full_total_row + 1
        for r in range(gap_start, gap_start + 3):
            ws.row_dimensions[r].height = 12

        # "Aggregated Summary" label row
        label_row = gap_start + 3
        lc = ws.cell(row=label_row, column=1, value="Aggregated Summary")
        lc.font      = Font(name="Arial", bold=False, size=10, color="FFFFFF")
        lc.fill      = PatternFill("solid", fgColor=header_color)
        lc.alignment = Alignment(vertical="center")
        lc.border    = Border(left=medium, right=thin, bottom=medium, top=medium)
        # Shade remaining columns in same header colour
        for col_idx in range(2, len(columns) + 1):
            c = ws.cell(row=label_row, column=col_idx)
            c.fill   = PatternFill("solid", fgColor=header_color)
            c.border = Border(left=thin, right=medium if col_idx == len(columns) else thin,
                              bottom=medium, top=medium)
        ws.row_dimensions[label_row].height = 22

        # Aggregated data rows
        agg_data_start = label_row + 1
        for row_idx, row_data in enumerate(agg_rows, agg_data_start):
            bg = "FFFFFF" if (row_idx - agg_data_start) % 2 == 0 else alt_color
            for col_idx, (label, field, fmt) in enumerate(columns, 1):
                if field == "_blank":
                    cell = ws.cell(row=row_idx, column=col_idx, value="")
                    cell.font      = Font(name="Arial", size=9)
                    cell.fill      = PatternFill("solid", fgColor="EEEEEE")
                    cell.alignment = Alignment(vertical="center")
                    cell.border    = border
                else:
                    _data_cell(row_idx, col_idx, row_data.get(field, ""), fmt, bg)

        # Aggregated TOTAL row
        agg_total_row = agg_data_start + len(agg_rows)
        if agg_rows:
            _total_row(agg_total_row, agg_data_start, agg_total_row - 1, total_bg="C8E6C9")

    return ws


# ── Table export ──────────────────────────────────────────────────────────────

# Column definitions: (header label, row-dict key, number format or None)
# _AREA_BL_COLS matches the A-1 On-Site Habitat Baseline layout in the
# statutory metric exactly so the ecologist can copy columns straight across.
# Columns left blank in the export are shaded/formula cells in the metric
# that auto-calculate — the ecologist should not fill these manually.
_AREA_BL_COLS = [
    # ── Existing area habitats ────────────────────────────────────────
    ("Broad Habitat",             "broad_habitat",         None),
    ("Habitat Type",              "sub_habitat_type",      None),
    ("Irreplaceable habitat",     "irreplaceable_habitat", None),
    ("Area (hectares)",           "area_ha",               "0.000000"),
    # ── Distinctiveness (auto-calculated in metric) ───────────────────
    ("Distinctiveness",           "_blank",                None),
    ("Score",                     "_blank",                None),
    # ── Condition ─────────────────────────────────────────────────────
    ("Condition",                 "condition",             None),
    ("Score",                     "_blank",                None),
    # ── Strategic significance ────────────────────────────────────────
    ("Strategic significance",    "strategic_significance",None),
    ("Strategic significance",    "_blank",                None),
    ("Strategic significance multiplier", "_blank",        None),
    # ── Required Action (auto-calculated in metric) ───────────────────
    ("Required Action to Meet Trading Rules", "_blank",    None),
    # ── Ecological baseline ───────────────────────────────────────────
    ("Total habitat units",       "_blank",                None),
    ("",                          "_blank",                None),   # spacer col R
    ("Area retained",             "area_retained_ha",      "0.000000"),
    ("Area enhanced",             "area_enhanced_ha",      "0.000000"),
    ("Baseline units retained",   "_blank",                None),
    ("Baseline units enhanced",   "_blank",                None),
    ("Area habitat lost",         "_blank",                None),
    ("Units lost",                "_blank",                None),
    # ── Reference info ────────────────────────────────────────────────
    ("Habitat Ref",               "habitat_ref",           None),
    ("User Comments",             "user_comments",         None),
    ("Features Joined",           "_count",                None),
]

_AREA_CR_COLS = [
    # ── Post intervention habitats ────────────────────────────────────
    ("Broad Habitat",             "broad_habitat",         None),
    ("Proposed habitat type",     "sub_habitat_type",      None),
    ("Area (hectares)",           "area_ha",               "0.000000"),
    # ── Distinctiveness ───────────────────────────────────────────────
    ("Distinctiveness",           "_blank",                None),
    ("Score",                     "_blank",                None),
    # ── Condition ─────────────────────────────────────────────────────
    ("Condition",                 "condition",             None),
    ("Score",                     "_blank",                None),
    # ── Strategic significance ────────────────────────────────────────
    ("Strategic significance",    "strategic_significance",None),
    ("Strategic significance",    "_blank",                None),
    ("Strategic significance multiplier", "_blank",        None),
    # ── Temporal multiplier ───────────────────────────────────────────
    ("Standard time to target (yrs)", "_blank",            None),
    ("Created in advance (yrs)",  "_blank",                None),
    ("Delay in starting (yrs)",   "_blank",                None),
    ("Std / adjusted time",       "_blank",                None),
    ("Final time (yrs)",          "_blank",                None),
    ("Final time multiplier",     "_blank",                None),
    # ── Difficulty multipliers ────────────────────────────────────────
    ("Standard difficulty",       "_blank",                None),
    ("Applied difficulty",        "_blank",                None),
    ("Final difficulty",          "_blank",                None),
    ("Difficulty multiplier",     "_blank",                None),
    # ── Habitat units / reference ─────────────────────────────────────
    ("Habitat units delivered",   "_blank",                None),
    ("User comments",             "user_comments",         None),
    ("Habitat reference number",  "habitat_ref",           None),
    ("Features Joined",           "_count",                None),
]

_AREA_EN_COLS = [
    ("Broad Habitat",          "broad_habitat",        None),
    ("Habitat Type",           "sub_habitat_type",     None),
    ("Habitat Ref",            "habitat_ref",          None),
    ("Area (ha)",              "area_ha",              "0.000000"),
    ("Condition",              "condition",            None),
    ("Habitat Type (Proposed)","prop_sub_habitat_type",None),
    ("Condition (Proposed)",   "prop_condition",       None),
    ("Strategic Significance", "strategic_significance",None),
    ("User Comments",          "user_comments",        None),
    ("Features Joined", "_count", None),
]

_HEDGE_BL_COLS = [
    # ── Existing hedgerow habitats ────────────────────────────────────
    ("Habitat type",              "sub_habitat_type",      None),
    ("Length (km)",               "length_km",             "0.000000"),
    # ── Distinctiveness ───────────────────────────────────────────────
    ("Distinctiveness",           "_blank",                None),
    ("Score",                     "_blank",                None),
    # ── Condition ─────────────────────────────────────────────────────
    ("Condition",                 "condition",             None),
    ("Score",                     "_blank",                None),
    # ── Strategic significance ────────────────────────────────────────
    ("Strategic significance",    "strategic_significance",None),
    ("Strategic significance",    "_blank",                None),
    ("Strategic significance multiplier", "_blank",        None),
    # ── Required Action ───────────────────────────────────────────────
    ("Required Action to Meet Trading Rules", "_blank",    None),
    # ── Ecological baseline ───────────────────────────────────────────
    ("Total hedgerow units",      "_blank",                None),
    ("",                          "_blank",                None),   # spacer
    ("Length retained",           "length_retained_km",   "0.000000"),
    ("Length enhanced",           "length_enhanced_km",   "0.000000"),
    ("Units retained",            "_blank",                None),
    ("Units enhanced",            "_blank",                None),
    ("Length lost",               "_blank",                None),
    ("Units lost",                "_blank",                None),
    # ── Reference ─────────────────────────────────────────────────────
    ("User comments",             "user_comments",         None),
    ("Habitat reference number",  "habitat_ref",           None),
    ("Features Joined",           "_count",                None),
]

_HEDGE_CR_COLS = [
    # ── Proposed habitats ─────────────────────────────────────────────
    ("Habitat type",              "sub_habitat_type",      None),
    ("Length (km)",               "length_km",             "0.000000"),
    # ── Distinctiveness ───────────────────────────────────────────────
    ("Distinctiveness",           "_blank",                None),
    ("Score",                     "_blank",                None),
    # ── Condition ─────────────────────────────────────────────────────
    ("Condition",                 "condition",             None),
    ("Score",                     "_blank",                None),
    # ── Strategic significance ────────────────────────────────────────
    ("Strategic significance",    "strategic_significance",None),
    ("Strategic significance",    "_blank",                None),
    ("Strategic significance multiplier", "_blank",        None),
    # ── Temporal multiplier ───────────────────────────────────────────
    ("Standard time to target (yrs)", "_blank",            None),
    ("Created in advance (yrs)",  "_blank",                None),
    ("Delay in starting (yrs)",   "_blank",                None),
    ("Std / adjusted time",       "_blank",                None),
    ("Final time (yrs)",          "_blank",                None),
    ("Final time multiplier",     "_blank",                None),
    # ── Difficulty multipliers ────────────────────────────────────────
    ("Standard difficulty",       "_blank",                None),
    ("Applied difficulty",        "_blank",                None),
    ("Final difficulty",          "_blank",                None),
    ("Difficulty multiplier",     "_blank",                None),
    # ── Units / reference ─────────────────────────────────────────────
    ("Hedge units delivered",     "_blank",                None),
    ("User comments",             "user_comments",         None),
    ("Habitat reference number",  "habitat_ref",           None),
    ("Features Joined",           "_count",                None),
]

_WATER_BL_COLS = [
    # ── Existing watercourse type ─────────────────────────────────────
    ("Watercourse type",          "sub_habitat_type",      None),
    ("Length (km)",               "length_km",             "0.000000"),
    # ── Distinctiveness ───────────────────────────────────────────────
    ("Distinctiveness",           "_blank",                None),
    ("Score",                     "_blank",                None),
    # ── Condition ─────────────────────────────────────────────────────
    ("Condition",                 "condition",             None),
    ("Score",                     "_blank",                None),
    # ── Strategic significance ────────────────────────────────────────
    ("Strategic significance",    "strategic_significance",None),
    ("Strategic significance",    "_blank",                None),
    ("Strategic significance multiplier", "_blank",        None),
    # ── Watercourse encroachment ──────────────────────────────────────
    ("Extent of encroachment",    "watercourse_encroachment", None),
    ("Multiplier",                "_blank",                None),
    # ── Riparian encroachment ─────────────────────────────────────────
    ("Extent of encroachment (both banks)", "riparian_encroachment_lr", None),
    ("Multiplier",                "_blank",                None),
    # ── Required Action ───────────────────────────────────────────────
    ("Required Action to Meet Trading Rules", "_blank",    None),
    # ── Ecological baseline ───────────────────────────────────────────
    ("Total watercourse units",   "_blank",                None),
    ("",                          "_blank",                None),   # spacer
    ("Length retained",           "length_retained_km",   "0.000000"),
    ("Length enhanced",           "length_enhanced_km",   "0.000000"),
    ("Units retained",            "_blank",                None),
    ("Units enhanced",            "_blank",                None),
    ("Length lost",               "_blank",                None),
    ("Units lost",                "_blank",                None),
    # ── Reference ─────────────────────────────────────────────────────
    ("User comments",             "user_comments",         None),
    ("Habitat reference number",  "habitat_ref",           None),
    ("Features Joined",           "_count",                None),
]

_WATER_CR_COLS = [
    # ── Proposed habitats ─────────────────────────────────────────────
    ("Watercourse type",          "sub_habitat_type",      None),
    ("Length (km)",               "length_km",             "0.000000"),
    # ── Distinctiveness ───────────────────────────────────────────────
    ("Distinctiveness",           "_blank",                None),
    ("Score",                     "_blank",                None),
    # ── Condition ─────────────────────────────────────────────────────
    ("Condition",                 "condition",             None),
    ("Score",                     "_blank",                None),
    # ── Strategic significance ────────────────────────────────────────
    ("Strategic significance",    "strategic_significance",None),
    ("Strategic significance",    "_blank",                None),
    ("Strategic significance multiplier", "_blank",        None),
    # ── Temporal multiplier ───────────────────────────────────────────
    ("Standard time to target (yrs)", "_blank",            None),
    ("Created in advance (yrs)",  "_blank",                None),
    ("Delay in starting (yrs)",   "_blank",                None),
    ("Std / adjusted time",       "_blank",                None),
    ("Final time (yrs)",          "_blank",                None),
    ("Final time multiplier",     "_blank",                None),
    # ── Difficulty multipliers ────────────────────────────────────────
    ("Standard difficulty",       "_blank",                None),
    ("Applied difficulty",        "_blank",                None),
    ("Final difficulty",          "_blank",                None),
    ("Difficulty multiplier",     "_blank",                None),
    # ── Watercourse encroachment ──────────────────────────────────────
    ("Extent of encroachment",    "watercourse_encroachment", None),
    ("Multiplier",                "_blank",                None),
    # ── Riparian encroachment ─────────────────────────────────────────
    ("Extent of encroachment (both banks)", "riparian_encroachment_lr", None),
    ("Multiplier",                "_blank",                None),
    # ── Units / reference ─────────────────────────────────────────────
    ("Watercourse units delivered","_blank",               None),
    ("User comments",             "user_comments",         None),
    ("Habitat reference number",  "habitat_ref",           None),
    ("Features Joined",           "_count",                None),
]

_HEDGE_EN_COLS = [
    ("Habitat Type",           "sub_habitat_type",     None),
    ("Habitat Ref",            "habitat_ref",          None),
    ("Length (km)",            "length_km",            "0.000000"),
    ("Condition",              "condition",            None),
    ("Habitat Type (Proposed)","prop_sub_habitat_type",None),
    ("Condition (Proposed)",   "prop_condition",       None),
    ("Strategic Significance", "strategic_significance",None),
    ("User Comments",          "user_comments",        None),
    ("Features Joined", "_count", None),
]

_WATER_EN_COLS = [
    ("Habitat Type",               "sub_habitat_type",          None),
    ("Habitat Ref",                "habitat_ref",               None),
    ("Length (km)",                "length_km",                 "0.000000"),
    ("Condition",                  "condition",                 None),
    ("Habitat Type (Proposed)",    "prop_sub_habitat_type",     None),
    ("Condition (Proposed)",       "prop_condition",            None),
    ("Strategic Significance",     "strategic_significance",    None),
    ("Watercourse Encroachment",   "watercourse_encroachment",  None),
    ("Riparian Encroachment L/R",  "riparian_encroachment_lr",  None),
    ("User Comments",              "user_comments",             None),
    ("Features Joined", "_count", None),
]

_TREE_BL_COLS = [
    ("Tree Type",           "sub_habitat_type",      None),
    ("Size",                "tree_size",             None),
    ("Location",            "tree_location",         None),
    ("Habitat Ref",         "habitat_ref",           None),
    ("Count",               "_count",                "0"),
    ("Area / Tree (ha)",    "area_ha",               "0.000000"),
    ("Total Area (ha)",     "total_area_ha",         "0.000000"),
    ("Condition",           "condition",             None),
    ("Retention",           "retention",             None),
    ("User Comments",       "user_comments",         None),
]

_TREE_CR_COLS = [
    ("Tree Type",           "sub_habitat_type",      None),
    ("Size",                "tree_size",             None),
    ("Location",            "tree_location",         None),
    ("Habitat Ref",         "habitat_ref",           None),
    ("Count",               "_count",                "0"),
    ("Area / Tree (ha)",    "area_ha",               "0.000000"),
    ("Total Area (ha)",     "total_area_ha",         "0.000000"),
    ("Condition",           "condition",             None),
    ("User Comments",       "user_comments",         None),
]


def run_export_table(iface, save_path):
    """Export all habitat data to a reference xlsx table for manual metric entry."""
    try:
        _export_table(iface, save_path)
    except Exception as e:
        QgsMessageLog.logMessage(f"Table export error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Critical)
        iface.messageBar().pushMessage(
            "BNG Mapper", f"Export failed: {e}", level=Qgis.MessageLevel.Critical, duration=0
        )


def _export_table(iface, out_path):
    if not OPENPYXL_OK:
        raise RuntimeError(
            "openpyxl is not installed. "
            "Install it via: pip install openpyxl --break-system-packages"
        )

    rows_area_bl, rows_area_cr, rows_area_en = _spatial_classify_area(iface)
    rows_area_bl, rows_area_cr = _reallocate_creation_to_retained(rows_area_bl, rows_area_cr)
    rows_hedge_bl, rows_hedge_cr, rows_hedge_en = _collect_linear(
        iface, "baseline_hedgerow", "proposed_hedgerow"
    )
    rows_water_bl, rows_water_cr, rows_water_en = _collect_linear(
        iface, "baseline_water", "proposed_water"
    )
    rows_tree_bl, rows_tree_cr, _ = _collect_trees(iface)

    # Merge identical rows (excluding habitat_ref and user_comments),
    # summing area / length measurements for each group.
    # Do NOT pre-aggregate — full individual rows are passed to _write_sheet,
    # which writes the full list first and then builds the aggregated summary
    # internally using the per-sheet agg_key.
    # Trees are still pre-aggregated by the existing tree collector so that
    # each tree point becomes one properly-sized row; the sheet-level
    # aggregation then further merges identical tree groups.
    rows_tree_bl = _aggregate_tree_rows(rows_tree_bl)
    rows_tree_cr = _aggregate_tree_rows(rows_tree_cr)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default empty sheet

    # Build A-1 and A-2 first so we can reference their data in the summary.
    # Pass the per-sheet aggregation key so the dual-table layout is applied.
    ws_a1 = _write_sheet(wb, "A-1 Habitat Baseline", _AREA_BL_COLS, rows_area_bl,
                         "2E7D32", "E8F5E9", agg_key=_AGG_KEYS["area_baseline"])
    ws_a2 = _write_sheet(wb, "A-2 Habitat Creation",  _AREA_CR_COLS, rows_area_cr,
                         "388E3C", "E8F5E9", agg_key=_AGG_KEYS["area_creation"])

    # ── Area Balance Summary (uses aggregated rows for the balance calc) ──
    agg_area_bl = _aggregate_rows_by_key(rows_area_bl, _AGG_KEYS["area_baseline"])
    agg_area_cr = _aggregate_rows_by_key(rows_area_cr, _AGG_KEYS["area_creation"])
    _write_area_summary(wb, agg_area_bl, agg_area_cr)

    # A-3 Enhancement sheet
    if rows_area_en:
        _write_sheet(wb, "A-3 Habitat Enhancement", _AREA_EN_COLS, rows_area_en,
                     "1B5E20", "E8F5E9", agg_key=_AGG_KEYS["area_baseline"])

    # Remaining sheets
    sheets_rest = [
        ("B-1 Hedgerow Baseline",    _HEDGE_BL_COLS, rows_hedge_bl, "00695C", "E0F2F1",
         _AGG_KEYS["hedge_baseline"]),
        ("B-2 Hedgerow Creation",    _HEDGE_CR_COLS, rows_hedge_cr, "00796B", "E0F2F1",
         _AGG_KEYS["hedge_creation"]),
        ("C-1 Watercourse Baseline", _WATER_BL_COLS, rows_water_bl, "1565C0", "E3F2FD",
         _AGG_KEYS["water_baseline"]),
        ("C-2 Watercourse Creation", _WATER_CR_COLS, rows_water_cr, "1976D2", "E3F2FD",
         _AGG_KEYS["water_creation"]),
    ]
    for title, cols, rows, hdr_col, alt_col, agg_key in sheets_rest:
        _write_sheet(wb, title, cols, rows, hdr_col, alt_col, agg_key=agg_key)

    wb.save(out_path)

    iface.messageBar().pushMessage(
        "BNG Mapper",
        f"Table exported: {os.path.basename(out_path)}  "
        f"— open in Excel, then copy each sheet into the corresponding metric tab.",
        level=Qgis.MessageLevel.Success, duration=12
    )


# ── Blank metric copier ───────────────────────────────────────────────────────

def copy_blank_metric(iface, save_path):
    """Copy the bundled blank metric .xlsm to a user-chosen location."""
    try:
        if not os.path.isfile(_BUNDLED_TEMPLATE):
            raise RuntimeError(
                "Bundled BNG metric template not found inside the plugin folder."
            )
        shutil.copy2(_BUNDLED_TEMPLATE, save_path)
        iface.messageBar().pushMessage(
            "BNG Mapper",
            f"Blank metric saved: {os.path.basename(save_path)}",
            level=Qgis.MessageLevel.Success, duration=8
        )
    except Exception as e:
        QgsMessageLog.logMessage(f"Blank metric copy error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Critical)
        iface.messageBar().pushMessage(
            "BNG Mapper", f"Failed to save blank metric: {e}", level=Qgis.MessageLevel.Critical, duration=0
        )


# ── Metric xlsm export ────────────────────────────────────────────────────────

# Column mappings for A-1 On-Site Habitat Baseline (1-based, verified from template)
# Only white (user-input) cells written — formula/shaded cells left intact.
# Row 10 = sub-headers, data starts row 11.
#
# E=5   Broad Habitat
# F=6   Habitat Type
# G=7   Irreplaceable habitat
# H=8   Area (hectares)
# K=11  Condition
# M=13  Strategic significance (full text)
# S=19  Area retained
# T=20  Area enhanced
# W=23  Area habitat lost  ← FORMULA in template, do NOT write
# Z=26  User comments
# AB=28 Habitat reference number
_A1_WRITE_COLS = [
    (5,  "broad_habitat",          None),
    (6,  "sub_habitat_type",       None),
    (7,  "irreplaceable_habitat",  None),
    (8,  "area_ha",                "0.000000"),
    (11, "condition",              None),
    (13, "strategic_significance", None),
    (19, "area_retained_ha",       "0.000000"),
    (20, "area_enhanced_ha",       "0.000000"),
    (26, "user_comments",          None),
    (28, "habitat_ref",            None),
]


_A1_DATA_START_ROW = 11   # Row 10 = sub-headers, data rows start at 11

# ── A-2 On-Site Habitat Creation ──────────────────────────────────────────────
# White input cols: D=4 Broad Habitat, E=5 Proposed habitat type, G=7 Area,
# J=10 Condition, L=12 Strategic significance, Z=26 User comments, AB=28 Habitat ref
_A2_WRITE_COLS = [
    (4,  "broad_habitat",          None),
    (5,  "sub_habitat_type",       None),
    (7,  "area_ha",                "0.000000"),
    (10, "condition",              None),
    (12, "strategic_significance", None),
    (26, "user_comments",          None),
    (28, "habitat_ref",            None),
]
_A2_DATA_START_ROW = 11

# ── A-3 On-Site Habitat Enhancement ───────────────────────────────────────────
# A-3 rows AUTO-SPAWN from A-1: a baseline row with Area enhanced (T) > 0 spawns
# one A-3 row, in A-1 row order (driven by 'A-1'!AL). The only white inputs are
# the proposed habitat, condition and strategic significance; everything else
# (baseline habitat, enhanced area) flows in via VLOOKUP. Verified against the
# template: R=18 proposed habitat, Y=25 condition, AA=27 strategic significance.
# The enhancement rows we write here MUST be in the same order as the enhanced
# A-1 rows so they line up with the auto-spawned rows.
_A3_WRITE_COLS = [
    (18, "prop_sub_habitat_type", None),   # R — Proposed habitat (broad-locked)
    (25, "prop_condition",        None),   # Y — Condition
    (27, "strategic_significance",None),   # AA — Strategic significance
]
_A3_DATA_START_ROW = 12

# ── B-3 On-Site Hedge Enhancement ─────────────────────────────────────────────
# Same auto-spawn behaviour from B-1 (Length enhanced > 0). Verified inputs:
# M=13 proposed habitat, S=19 condition, U=21 strategic significance.
_B3_WRITE_COLS = [
    (13, "prop_sub_habitat_type", None),   # M — Proposed habitat
    (19, "prop_condition",        None),   # S — Condition
    (21, "strategic_significance",None),   # U — Strategic significance
]
_B3_DATA_START_ROW = 12

# White input cols: D=4 habitat type, E=5 length km, H=8 condition,
# J=10 strategic sig, P=16 length retained, Q=17 length enhanced,
# V=22 user comments, X=24 habitat ref
_B1_WRITE_COLS = [
    (4,  "sub_habitat_type",      None),
    (5,  "length_km",             "0.000000"),
    (8,  "condition",             None),
    (10, "strategic_significance",None),
    (16, "length_retained_km",    "0.000000"),
    (17, "length_enhanced_km",    "0.000000"),
    (22, "user_comments",         None),
    (24, "habitat_ref",           None),
]
_B1_DATA_START_ROW = 10

# ── B-2 On-Site Hedge Creation ────────────────────────────────────────────────
# White input cols: D=4 habitat type, E=5 length km, H=8 condition,
# J=10 strategic sig, N=14 created in advance, O=15 delay,
# X=24 user comments, Z=26 habitat ref
_B2_WRITE_COLS = [
    (4,  "sub_habitat_type",      None),
    (5,  "length_km",             "0.000000"),
    (8,  "condition",             None),
    (10, "strategic_significance",None),
    (24, "user_comments",         None),
    (26, "habitat_ref",           None),
]
_B2_DATA_START_ROW = 12

# ── C-1 On-Site Watercourse Baseline ─────────────────────────────────────────
# White input cols: D=4 watercourse type, E=5 length km, H=8 condition,
# J=10 strategic sig, M=13 watercourse encroachment, O=15 riparian encroachment,
# U=21 length retained, V=22 length enhanced, AB=28 user comments, AD=30 habitat ref
_C1_WRITE_COLS = [
    (4,  "sub_habitat_type",          None),
    (5,  "length_km",                 "0.000000"),
    (8,  "condition",                 None),
    (10, "strategic_significance",    None),
    (13, "watercourse_encroachment",  None),
    (15, "riparian_encroachment_lr",  None),
    (21, "length_retained_km",        "0.000000"),
    (22, "length_enhanced_km",        "0.000000"),
    (28, "user_comments",             None),
    (30, "habitat_ref",               None),
]
_C1_DATA_START_ROW = 10

# ── C-2 On-Site Watercourse Creation ─────────────────────────────────────────
# White input cols: C=3 watercourse type, D=4 length km, G=7 condition,
# I=9 strategic sig, M=13 created in advance, N=14 delay,
# V=22 watercourse encroachment, X=24 riparian encroachment,
# AA=27 user comments, AC=29 habitat ref
_C2_WRITE_COLS = [
    (3,  "sub_habitat_type",         None),
    (4,  "length_km",                "0.000000"),
    (7,  "condition",                None),
    (9,  "strategic_significance",   None),
    (22, "watercourse_encroachment", None),
    (24, "riparian_encroachment_lr", None),
    (27, "user_comments",            None),
    (29, "habitat_ref",              None),
]
_C2_DATA_START_ROW = 12

# ── C-3 On-Site Watercourse Enhancement ──────────────────────────────────────
# Auto-spawned from C-1 (one row per C-1 row whose length-enhanced V > 0), in
# baseline row order — exactly like A-3/B-3. We write only the white input
# cells: N=14 post-intervention (proposed) watercourse type, T=20 target
# condition, V=22 strategic significance, AI=35 channel encroachment,
# AK=37 riparian encroachment (both banks). The enhanced units (col AM) are the
# metric's: baseline value kept whole + uplift x difficulty x temporal, then
# x channel x banks encroachment.
_C3_WRITE_COLS = [
    (14, "prop_sub_habitat_type",         None),
    (20, "prop_condition",                None),
    (22, "strategic_significance",        None),
    (35, "prop_watercourse_encroachment", None),
    (37, "prop_riparian_encroachment_lr", None),
]
_C3_DATA_START_ROW = 12


# ═══════════════════════════════════════════════════════════════════════════════
# OFF-SITE sheets (D / E / F)
# ───────────────────────────────────────────────────────────────────────────────
# The off-site sheets are twins of the on-site A/B/C sheets, plus two inputs the
# metric needs for off-site delivery:
#   • "Spatial risk category"  — a G-3 dropdown STRING whose multiplier (SRM) the
#     metric applies to the positive off-site change. The strings carry the
#     metric's exact trailing spaces (see _SRM_CATEGORIES); metric_xml_patcher
#     writes inline strings with xml:space="preserve" so they survive the round
#     trip and match the VLOOKUP key.
#   • "Off-site reference"     — a per-receptor id. The metric does NOT auto-link;
#     it must be written on the baseline (D-1/E-1/F-1), on the creation row
#     (D-2/E-2/F-2, which also carries a matching "Baseline ref"), and on the
#     auto-spawned enhancement row (D-3 col AU / E-3 / F-3). Without it the
#     delivered-units cell errors "Off-site reference required" and the reach
#     nets negative.
# Every column position below was verified by writing values + recalculating the
# bundled template in Excel (D-1/D-2/D-3 area create+enhance, E-2 hedge create).
_SRM_CATEGORIES = {   # spatial-risk float → exact G-3 category string (trailing spaces intentional)
    1.0:  "Compensation inside LPA boundary or NCA of impact site ",
    0.75: "Compensation outside LPA or NCA of impact site, but in neighbouring LPA or NCA ",
    0.5:  "Compensation outside LPA or NCA of impact site and neighbouring LPA or NCA  ",
}

# D-1 Off-Site Habitat Baseline (data row 11) — receptor's existing habitat
_D1_WRITE_COLS = [
    (5,  "broad_habitat",          None),
    (6,  "sub_habitat_type",       None),
    (7,  "irreplaceable_habitat",  None),
    (8,  "area_ha",                "0.000000"),
    (11, "condition",              None),
    (13, "strategic_significance", None),
    (18, "spatial_category",       None),   # R — Spatial risk category
    (22, "area_retained_ha",       "0.000000"),
    (23, "area_enhanced_ha",       "0.000000"),
    (29, "user_comments",          None),
    (31, "habitat_ref",            None),
    (32, "offsite_ref",            None),   # AF — Off-site reference
]
_D1_DATA_START_ROW = 11
# D-2 Off-Site Habitat Creation (data row 11)
_D2_WRITE_COLS = [
    (4,  "broad_habitat",          None),
    (5,  "sub_habitat_type",       None),
    (7,  "area_ha",                "0.000000"),
    (10, "condition",              None),
    (12, "strategic_significance", None),
    (25, "spatial_category",       None),   # Y
    (29, "user_comments",          None),
    (31, "habitat_ref",            None),
    (32, "offsite_ref",            None),   # AF
    (33, "baseline_ref",           None),   # AG — links to the D-1 receptor
]
_D2_DATA_START_ROW = 11
# D-3 Off-Site Habitat Enhancement (data row 12) — auto-spawns from D-1 (enhanced>0)
_D3_WRITE_COLS = [
    (18, "prop_sub_habitat_type",  None),   # R
    (25, "prop_condition",         None),   # Y
    (27, "strategic_significance", None),   # AA
    (47, "offsite_ref",            None),   # AU — required or delivered units error
]
_D3_DATA_START_ROW = 12
# E-2 Off-Site Hedge Creation (data row 12) — creation-led, no receptor baseline
# E-1 Off-Site Hedge Baseline (data row 10) — the receptor's existing reaches.
# Off-site twin of B-1, so the same fields shifted right by the SRM block
# (N/O/P) that B-1 doesn't have, plus the off-site reference.
_E1_DATA_START_ROW = 10
_E1_WRITE_COLS = [
    (4,  "sub_habitat_type",       None),
    (5,  "length_km",              "0.000000"),
    (8,  "condition",              None),
    (10, "strategic_significance", None),
    (15, "spatial_category",       None),   # O — Spatial risk category
    (19, "length_retained_km",     "0.000000"),   # S
    (20, "length_enhanced_km",     "0.000000"),   # T
    (25, "user_comments",          None),   # Y
    (27, "habitat_ref",            None),   # AA
    (28, "offsite_ref",            None),   # AB
]

_E2_WRITE_COLS = [
    (4,  "sub_habitat_type",       None),
    (5,  "length_km",              "0.000000"),
    (8,  "condition",              None),
    (10, "strategic_significance", None),
    (13, "spatial_category",       None),   # M
    (27, "user_comments",          None),
    (29, "habitat_ref",            None),
    (30, "offsite_ref",            None),   # AD
]
_E2_DATA_START_ROW = 12
# F-1 Off-Site Watercourse Baseline (data row 10)
# The watercourse twin of E-1: a reach inside the blue line needs its receptor
# baseline here, retained length included, or the off-site side of the module
# has a gain with nothing to measure it against. Two columns E-1 has no use
# for — M (channel encroachment) and O (both banks, the single combined string
# built by bng_multipliers.canonical_riparian_lr) — are what make this sheet
# different; without them the metric cannot score the reach.
_F1_WRITE_COLS = [
    (4,  "sub_habitat_type",          None),   # D
    (5,  "length_km",                 "0.000000"),
    (8,  "condition",                 None),   # H
    (10, "strategic_significance",    None),   # J
    (13, "watercourse_encroachment",  None),   # M
    (15, "riparian_encroachment_lr",  None),   # O
    (19, "spatial_category",          None),   # S
    (24, "length_retained_km",        "0.000000"),   # X
    (25, "length_enhanced_km",        "0.000000"),   # Y
    (31, "user_comments",             None),   # AE
    (33, "habitat_ref",               None),   # AG
    (34, "offsite_ref",               None),   # AH
]
_F1_DATA_START_ROW = 10

# F-2 Off-Site Watercourse Creation (data row 12)
_F2_WRITE_COLS = [
    (3,  "sub_habitat_type",          None),
    (4,  "length_km",                 "0.000000"),
    (7,  "condition",                 None),
    (9,  "strategic_significance",    None),
    (22, "watercourse_encroachment",  None),
    (24, "riparian_encroachment_lr",  None),
    (26, "spatial_category",          None),   # Z
    (30, "user_comments",             None),
    (32, "habitat_ref",               None),
    (33, "offsite_ref",               None),   # AG
]
_F2_DATA_START_ROW = 12


def _off_tree_d2(d2, trees, s, ss, cat, ref, tree_areas):
    """Append an off-site 'Individual trees' creation row (D-2) for `trees`
    scattered trees; their statutory area = count × per-tree area."""
    trees = int(trees or 0)
    if trees <= 0:
        return
    area = trees * float(tree_areas.get(s.get("tree_size", "Small"), tree_areas["Small"]))
    d2.append({"broad_habitat": "Individual trees", "sub_habitat_type": "Rural tree",
               "condition": s.get("tree_condition", "Poor"), "strategic_significance": ss,
               "spatial_category": cat, "area_ha": round(area, 6),
               "offsite_ref": ref, "baseline_ref": ref})


def _build_offsite_plan(recipe):
    """Build the off-site D/E/F rows from a tagged off-site recipe and return
    patch_metric plan tuples. The recipe carries:
        offsite=True, module, spatial_cat (or spatial_risk), strategic,
        receptor_habitat/receptor_condition (AREA only), steps=[...].
    Each area step becomes a D-1 receptor baseline row + a D-2 creation
    (create/tree) or an auto-spawned D-3 enhancement (uplift/change). Hedge /
    watercourse off-site are creation-led (E-2 / F-2, no receptor baseline).
    An incrementing off-site reference links each group; the SRM is applied by
    the metric from the spatial-risk category string."""
    from .enhancement_engine import broad_group
    try:
        from .layer_manager import TREE_SIZE_AREAS
    except Exception:
        TREE_SIZE_AREAS = {"Small": 0.0040715, "Medium": 0.01628602,
                           "Large": 0.03664354, "Very Large": 0.0764538}

    module   = recipe.get("module", "area")
    def_ss   = recipe.get("strategic", "")
    cat      = (recipe.get("spatial_cat")
                or _SRM_CATEGORIES.get(round(float(recipe.get("spatial_risk", 1.0) or 1.0), 2), ""))
    rec_hab  = recipe.get("receptor_habitat", "")
    rec_cond = recipe.get("receptor_condition", "")
    rec_irr  = recipe.get("receptor_irreplaceable") or "No"   # receptor never really irreplaceable
    wc_enc   = recipe.get("watercourse_encroachment", "")     # off-site watercourse reach encroachment
    rip_enc  = recipe.get("riparian_encroachment_lr", "")

    d1, d2, d3, e2, f2 = [], [], [], [], []
    ref = 0
    for s in recipe.get("steps", []):
        kind = s.get("kind", "")
        ss   = s.get("strategic") or def_ss
        ref += 1
        r = str(ref)
        if module == "area":
            area = float(s.get("area_ha") or 0.0)
            base = {"broad_habitat": _broad(broad_group(rec_hab), rec_hab),
                    "sub_habitat_type": rec_hab, "irreplaceable_habitat": rec_irr,
                    "condition": rec_cond, "strategic_significance": ss,
                    "spatial_category": cat, "area_ha": round(area, 6), "offsite_ref": r}
            if kind in ("create", "create+tree"):
                base["area_retained_ha"] = 0.0; base["area_enhanced_ha"] = 0.0   # receptor lost
                d1.append(base)
                tgt = s.get("target", "")
                d2.append({"broad_habitat": _broad(broad_group(tgt), tgt), "sub_habitat_type": tgt,
                           "condition": s.get("target_condition", ""), "strategic_significance": ss,
                           "spatial_category": cat, "area_ha": round(area, 6),
                           "offsite_ref": r, "baseline_ref": r})
                if kind == "create+tree":
                    _off_tree_d2(d2, int(round(float(s.get("tree_density") or 0.0) * area)),
                                 s, ss, cat, r, TREE_SIZE_AREAS)
            elif kind == "tree":
                base["area_retained_ha"] = round(area, 6); base["area_enhanced_ha"] = 0.0   # receptor kept
                d1.append(base)
                _off_tree_d2(d2, int(s.get("trees") or 0), s, ss, cat, r, TREE_SIZE_AREAS)
            elif kind in ("uplift", "change"):
                base["area_retained_ha"] = 0.0; base["area_enhanced_ha"] = round(area, 6)   # receptor enhanced
                d1.append(base)
                tgt = rec_hab if kind == "uplift" else s.get("target", rec_hab)
                d3.append({"prop_sub_habitat_type": tgt, "prop_condition": s.get("target_condition", ""),
                           "strategic_significance": ss, "offsite_ref": r})
        elif module == "hedgerow" and kind == "create":
            e2.append({"sub_habitat_type": s.get("target", ""),
                       "condition": s.get("target_condition", "") or "Poor",
                       "strategic_significance": ss, "spatial_category": cat,
                       "length_km": round(float(s.get("km") or 0.0), 6), "offsite_ref": r})
        elif module == "watercourse" and kind == "create":
            # Encroachment is REQUIRED for the metric to score an F-2 reach — a
            # blank collapses the units to zero. The engine's linear steps don't
            # carry encroachment, so take it from the step if the tab supplied it,
            # else default to "No Encroachment" (matching the habitat form).
            f2.append({"sub_habitat_type": s.get("target", ""),
                       "condition": s.get("target_condition", "") or "Poor",
                       "strategic_significance": ss, "spatial_category": cat,
                       "length_km": round(float(s.get("km") or 0.0), 6),
                       "watercourse_encroachment": (s.get("watercourse_encroachment")
                                                    or wc_enc or "No Encroachment"),
                       "riparian_encroachment_lr": (s.get("riparian_encroachment_lr")
                                                    or rip_enc or "No Encroachment/ No Encroachment"),
                       "offsite_ref": r})

    plan = []
    if d1: plan.append(("D-1 Off-Site Habitat Baseline",   d1, _D1_WRITE_COLS, _D1_DATA_START_ROW))
    if d2: plan.append(("D-2 Off-Site Habitat Creation",   d2, _D2_WRITE_COLS, _D2_DATA_START_ROW))
    if d3: plan.append(("D-3 Off-Site Habitat Enhancment", d3, _D3_WRITE_COLS, _D3_DATA_START_ROW))
    if e2: plan.append(("E-2 Off-Site Hedge Creation",     e2, _E2_WRITE_COLS, _E2_DATA_START_ROW))
    if f2: plan.append(("F-2 Off-Site WaterC' Creation",   f2, _F2_WRITE_COLS, _F2_DATA_START_ROW))
    return plan


def _split_baseline_for_enhancement(baseline_rows, enhancement_rows,
                                    match_keys, qty_field,
                                    retained_field, enhanced_field):
    """Pair aggregated baseline rows with their mapped enhancement targets and
    return ``(a1_rows, enh_rows)`` ready to write to A-1/A-3 (or B-1/B-3).

    Why this exists
    ---------------
    The metric AUTO-SPAWNS one enhancement row (A-3/B-3) per baseline row whose
    *enhanced* quantity (T / Length-enhanced) is > 0, in baseline ROW ORDER. The
    proposed habitat/condition/strategic-significance must be supplied on those
    rows. A single baseline row can carry only ONE proposed target, so a baseline
    group enhanced toward several targets must be split into one enhanced row per
    target (plus a retained/lost remainder row).

    This builds ``a1_rows`` (preserving order) and ``enh_rows`` strictly in
    lock-step: ``enh_rows[i]`` corresponds to the i-th enhanced row in
    ``a1_rows``, which is exactly the order the metric will auto-spawn — so they
    line up. ``enh_rows`` carries ``prop_sub_habitat_type`` / ``prop_condition``
    / ``strategic_significance`` (the only A-3/B-3 inputs).

    No units are invented: the split conserves total quantity and only relocates
    the already-mapped enhanced quantity onto its own row(s).
    """
    from collections import defaultdict

    def _key(d):
        return tuple(str(d.get(f) or "").strip() for f in match_keys)

    targets_by_key = defaultdict(list)
    for e in enhancement_rows or []:
        targets_by_key[_key(e)].append(e)

    a1_rows, enh_rows = [], []
    for row in baseline_rows:
        E = float(row.get(enhanced_field) or 0.0)
        if E <= 1e-9:
            a1_rows.append(row)
            continue
        targets = targets_by_key.get(_key(row), [])
        if not targets:
            # Enhanced quantity with no mapped target should never happen (both
            # derive from the same classification). Guard alignment: fold the
            # enhanced quantity back into retained so no blank A-3/B-3 row spawns.
            safe = dict(row)
            safe[retained_field] = round(float(row.get(retained_field) or 0.0) + E, 6)
            safe[enhanced_field] = 0.0
            a1_rows.append(safe)
            continue
        if len(targets) == 1:
            # Keep the combined retained+enhanced row; one auto-spawned A-3 row.
            a1_rows.append(row)
            enh_rows.append(dict(targets[0]))
            continue
        # Multiple targets: split into a retained/lost remainder + one enhanced
        # row per target, each spawning its own A-3 row.
        R = float(row.get(retained_field) or 0.0)
        H = float(row.get(qty_field) or 0.0)
        L = max(0.0, H - R - E)
        if R > 1e-9 or L > 1e-9:
            rem = dict(row)
            rem[qty_field]      = round(R + L, 6)
            rem[retained_field] = round(R, 6)
            rem[enhanced_field] = 0.0
            a1_rows.append(rem)
        for t in targets:
            q = float(t.get(qty_field) or 0.0)
            er = dict(row)
            er[qty_field]      = round(q, 6)
            er[retained_field] = 0.0
            er[enhanced_field] = round(q, 6)
            a1_rows.append(er)
            enh_rows.append(dict(t))
    return a1_rows, enh_rows


def _apply_recipe_to_rows(recipe, a_bl, a_cr, a_en, h_bl, h_cr, h_en, w_bl, w_en):
    """Overlay an on-site Enhancements recipe onto the freshly-collected metric
    rows (BEFORE aggregation/splitting). Each recipe step adds the corresponding
    enhancement / creation / tree / hedgerow row and moves the matching retained
    baseline quantity into enhanced (for an in-situ enhancement) or out of
    retained (for a creation that destroys the parcel). The mapped scheme is left
    intact — the recipe is purely additive on top of it."""
    from .enhancement_engine import broad_group
    try:
        from .layer_manager import TREE_SIZE_AREAS
    except Exception:
        TREE_SIZE_AREAS = {"Small": 0.0040715, "Medium": 0.01628602,
                           "Large": 0.03664354, "Very Large": 0.0764538}

    def _find(rows, **crit):
        for r in rows:
            if all(str(r.get(k) or "").strip() == str(v or "").strip() for k, v in crit.items()):
                return r
        return None

    def _tree_cr_row(trees, size, cond, ss):
        area = int(trees or 0) * float(TREE_SIZE_AREAS.get(size, TREE_SIZE_AREAS["Small"]))
        if area > 0:
            a_cr.append({"broad_habitat": "Individual trees", "sub_habitat_type": "Rural tree",
                         "condition": cond, "strategic_significance": ss, "area_ha": round(area, 6),
                         # carry size + count so the tree-size breakdown note
                         # picks up recipe-injected trees too (one pre-summed row)
                         "tree_size": size, "_tree_count": int(trees or 0)})

    _xlog("RECIPE INJECTION (%d steps):" % len(recipe.get("steps", [])))
    for s in recipe.get("steps", []):
        module = s.get("module", "area")
        kind = s.get("kind", "")
        ss = s.get("strategic", "")          # carry the parcel/reach strategic significance
        _xlog("   step module=%s kind=%s strat=%r | %s" % (module, kind, ss, s.get("label", "")))

        if module == "area":
            hab = s.get("parcel_habitat", ""); cond = s.get("parcel_condition", "")
            area = float(s.get("area_ha") or 0.0)
            bl = _find(a_bl, sub_habitat_type=hab, condition=cond) if hab else None
            if not ss and bl is not None:        # fall back to the parcel's own SS from the map
                ss = bl.get("strategic_significance", "")
            if kind == "tree":
                _tree_cr_row(s.get("trees"), s.get("tree_size", "Small"), s.get("tree_condition", "Poor"), ss)
            elif kind in ("uplift", "change"):
                tgt = hab if kind == "uplift" else s.get("target", hab)
                a_en.append({
                    "broad_habitat":          (bl or {}).get("broad_habitat") or _broad(broad_group(hab), hab),
                    "sub_habitat_type":       hab, "condition": cond,
                    "prop_sub_habitat_type":  tgt, "prop_habitat_type": _broad(broad_group(tgt), tgt),
                    "prop_condition":         s.get("target_condition", ""),
                    "strategic_significance": ss, "area_ha": round(area, 6)})
                if bl is not None and area > 0:
                    bl["area_enhanced_ha"] = round(float(bl.get("area_enhanced_ha") or 0.0) + area, 6)
                    bl["area_retained_ha"] = round(max(0.0, float(bl.get("area_retained_ha") or 0.0) - area), 6)
            elif kind in ("create", "create+tree"):
                tgt = s.get("target", "")
                a_cr.append({"broad_habitat": _broad(broad_group(tgt), tgt), "sub_habitat_type": tgt,
                             "condition": s.get("target_condition", ""), "strategic_significance": ss,
                             "area_ha": round(area, 6)})
                if bl is not None and area > 0:        # converted retained land -> lost
                    bl["area_retained_ha"] = round(max(0.0, float(bl.get("area_retained_ha") or 0.0) - area), 6)
                if kind == "create+tree":
                    trees = int(round(float(s.get("tree_density") or 0.0) * area))
                    _tree_cr_row(trees, s.get("tree_size", "Small"), s.get("tree_condition", "Poor"), ss)

        else:  # linear: hedgerow / watercourse
            base = s.get("base_habitat", ""); bcond = s.get("base_condition", "")
            tgt = s.get("target", ""); tcond = s.get("target_condition", "")
            km = float(s.get("km") or 0.0)
            if kind == "create" and module == "hedgerow":
                h_cr.append({"sub_habitat_type": tgt, "condition": tcond or "Good",
                             "strategic_significance": ss, "length_km": round(km, 6)})
            elif kind in ("uplift", "upgrade"):
                en = h_en if module == "hedgerow" else w_en
                bl = _find(h_bl if module == "hedgerow" else w_bl,
                           sub_habitat_type=base, condition=bcond)
                if not ss and bl is not None:
                    ss = bl.get("strategic_significance", "")
                row = {"sub_habitat_type": base, "condition": bcond,
                       "prop_sub_habitat_type": tgt, "prop_condition": tcond,
                       "strategic_significance": ss, "length_km": round(km, 6)}
                if module == "watercourse" and bl is not None:   # match keys include encroachment
                    wc  = bl.get("watercourse_encroachment", "")
                    rip = bl.get("riparian_encroachment_lr", "")
                    # Baseline encroachment — needed so _split_baseline_for_enhancement
                    # pairs this row with its C-1 baseline (the water match keys
                    # include both encroachment fields).
                    row["watercourse_encroachment"] = wc
                    row["riparian_encroachment_lr"] = rip
                    # PROPOSED (post-intervention) encroachment for the C-3 sheet
                    # (cols AI=35 / AK=37). A recipe uplift changes condition only,
                    # so proposed encroachment == the baseline's. Without these the
                    # C-3 'Watercourse units delivered' cell (AM) is left blank and
                    # the reach nets NEGATIVE (baseline consumed, nothing delivered).
                    row["prop_watercourse_encroachment"] = wc
                    row["prop_riparian_encroachment_lr"] = rip
                en.append(row)
                if bl is not None and km > 0:
                    bl["length_enhanced_km"] = round(float(bl.get("length_enhanced_km") or 0.0) + km, 6)
                    bl["length_retained_km"] = round(max(0.0, float(bl.get("length_retained_km") or 0.0) - km), 6)


# ── Strategic-significance-only change guard ─────────────────────────────────
def _find_strat_only_pairs(iface):
    """Paired baseline+proposed features that are IDENTICAL except for strategic
    significance (same habitat, condition and — for watercourses — encroachment).

    Such a difference is almost always a data-entry slip: the metric scores a
    watercourse one as an enhancement (its C-3 accepts a same-dist/cond change)
    and silently drops a hedge/tree one (its B-3 returns "No enhancement"). We
    offer to reconcile it before export. Returns one dict per flagged pair."""
    mods = [("Hedgerow",        "baseline_hedgerow", "proposed_hedgerow", ()),
            ("Watercourse",     "baseline_water",    "proposed_water",
             ("watercourse_encroachment",
              "riparian_encroachment_l", "riparian_encroachment_r")),
            ("Individual tree", "baseline_trees",    "proposed_trees", ())]

    def g(feat, name):
        i = feat.fieldNameIndex(name)
        return str(feat[name] or "").strip() if i >= 0 else ""

    out = []
    for mod, bl_key, pr_key, enc_fields in mods:
        bl = _get_layer(bl_key); pr = _get_layer(pr_key)
        if not bl or not bl.isValid() or not pr or not pr.isValid():
            continue
        pr_by_pid = {}
        for f in pr.getFeatures():
            pid = g(f, "pair_id")
            if pid and not pid.startswith("solo:"):
                pr_by_pid.setdefault(pid, f)
        for bf in bl.getFeatures():
            pid = g(bf, "pair_id")
            if not pid or pid.startswith("solo:"):
                continue
            pf = pr_by_pid.get(pid)
            if pf is None:
                continue
            if g(bf, "sub_habitat_type") != g(pf, "sub_habitat_type"):
                continue
            if g(bf, "condition") != g(pf, "condition"):
                continue
            if any(g(bf, e) != g(pf, e) for e in enc_fields):
                continue
            bs, ps = g(bf, "strategic_significance"), g(pf, "strategic_significance")
            if bs == ps:
                continue
            out.append({"module": mod, "sub": g(bf, "sub_habitat_type"),
                        "cond": g(bf, "condition"), "bl_strat": bs, "pr_strat": ps,
                        "bl_layer": bl, "bl_fid": bf.id(),
                        "pr_layer": pr, "pr_fid": pf.id()})
    return out


def _apply_strat_only(pairs, values):
    """Write the chosen strategic significance onto BOTH sides of each flagged
    pair (one value per pair, aligned with `pairs`) so the plugin and the
    exported metric agree."""
    from collections import defaultdict
    per_layer = defaultdict(dict)   # layer -> {fid: value}
    for p, val in zip(pairs, values):
        per_layer[p["bl_layer"]][p["bl_fid"]] = val
        per_layer[p["pr_layer"]][p["pr_fid"]] = val
    for layer, fids in per_layer.items():
        idx = layer.fields().indexOf("strategic_significance")
        if idx < 0:
            continue
        layer.dataProvider().changeAttributeValues(
            {fid: {idx: val} for fid, val in fids.items()})
        layer.reload()
        layer.triggerRepaint()


class _StratOnlyDialog(QDialog):
    """Lists every baseline/proposed pair that differs ONLY in strategic
    significance and offers a per-pair dropdown to reconcile each one."""

    def __init__(self, parent, pairs, options):
        super().__init__(parent)
        from qgis.PyQt.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout,
                                         QLabel, QComboBox, QPushButton,
                                         QScrollArea, QWidget, QFrame)
        self.setWindowTitle("Strategic significance — only change")
        self.setMinimumWidth(640)
        self.pairs = pairs
        self.combos = []
        self.action = "cancel"   # 'apply' | 'asis' | 'cancel'

        lay = QVBoxLayout(self)
        intro = QLabel(
            "<b>%d feature(s)</b> differ between baseline and proposed <b>only</b> "
            "in strategic significance — same habitat and condition. This is "
            "usually a data-entry error.<br>Choose a value to set on <b>both</b> "
            "sides of each pair, or export them unchanged." % len(pairs))
        intro.setWordWrap(True)
        lay.addWidget(intro)

        host = QWidget()
        grid = QGridLayout(host)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for col, txt in enumerate(("Feature", "Baseline → Proposed", "Set both to")):
            h = QLabel("<b>%s</b>" % txt)
            grid.addWidget(h, 0, col)
        for r, p in enumerate(pairs, start=1):
            grid.addWidget(QLabel("%s — %s / %s"
                                  % (p["module"], p["sub"],
                                     p["cond"] or "(no condition)")), r, 0)
            grid.addWidget(QLabel("'%s'  →  '%s'"
                                  % (p["bl_strat"] or "(blank)",
                                     p["pr_strat"] or "(blank)")), r, 1)
            cb = QComboBox()
            cb.addItems(options)
            # Default each pair to its own baseline value (the most likely intent
            # is that the proposed strat was changed by mistake).
            cb.setCurrentIndex(options.index(p["bl_strat"])
                               if p["bl_strat"] in options else 0)
            grid.addWidget(cb, r, 2)
            self.combos.append(cb)
        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(320)
        lay.addWidget(scroll)

        btns = QHBoxLayout()
        b_apply = QPushButton("Apply && Export")
        b_asis  = QPushButton("Export as-is")
        b_cancel = QPushButton("Cancel")
        b_apply.setDefault(True)
        btns.addWidget(b_apply)
        btns.addWidget(b_asis)
        btns.addStretch()
        btns.addWidget(b_cancel)
        lay.addLayout(btns)
        b_apply.clicked.connect(lambda: self._finish("apply"))
        b_asis.clicked.connect(lambda: self._finish("asis"))
        b_cancel.clicked.connect(lambda: self._finish("cancel"))

    def _finish(self, action):
        self.action = action
        self.reject() if action == "cancel" else self.accept()

    def chosen_values(self):
        return [cb.currentText() for cb in self.combos]


def _check_strat_only_before_export(iface):
    """Pre-export guard. Returns True to proceed, False to abort. If any
    strat-only baseline/proposed changes exist, shows a per-pair dialog so the
    user can reconcile each one (or export as-is)."""
    try:
        pairs = _find_strat_only_pairs(iface)
    except Exception:
        return True
    if not pairs:
        return True
    from .layer_manager import STRATEGIC_SIGNIFICANCE_OPTIONS
    dlg = _StratOnlyDialog(iface.mainWindow(), pairs,
                           list(STRATEGIC_SIGNIFICANCE_OPTIONS))
    dlg.exec()
    if dlg.action == "asis":
        return True
    if dlg.action != "apply":
        return False                      # Cancel / closed
    _apply_strat_only(pairs, dlg.chosen_values())
    return True


# ── Export logging (diagnostic) ─────────────────────────────────────────────
# Every export writes a full trace to biome_export.log next to the plugin so the
# written metric can be read back and reconciled cell-by-cell against intent.
_XLOG_PATH = os.path.join(os.path.dirname(__file__), "biome_export.log")


def _xlog(msg, reset=False):
    try:
        with open(_XLOG_PATH, "w" if reset else "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass


def _log_export_plan(out_path, recipe, plan):
    """Log the full write-plan: for each sheet, every input cell (column+row =
    value) and the per-sheet sums of the numeric columns. Read back the same
    cells from the exported workbook to verify nothing was mis-written."""
    try:
        from openpyxl.utils import get_column_letter
    except Exception:
        def get_column_letter(c):
            return "col%d" % c
    _xlog("output: %s" % out_path)
    if recipe:
        _xlog("RECIPE: %s  [kind=%s closes=%s covers=%s]"
              % (recipe.get("label"), recipe.get("kind"),
                 recipe.get("closes_gap"), recipe.get("covers_currencies")))
        for s in recipe.get("steps", []):
            _xlog("   step module=%s kind=%s base=%r/%s target=%r/%s area_ha=%s km=%s trees=%s size=%s cond=%s"
                  % (s.get("module"), s.get("kind"),
                     s.get("parcel_habitat", s.get("base_habitat", "")),
                     s.get("parcel_condition", s.get("base_condition", "")),
                     s.get("target", ""), s.get("target_condition", ""),
                     s.get("area_ha"), s.get("km"), s.get("trees"),
                     s.get("tree_size"), s.get("tree_condition")))
    else:
        _xlog("RECIPE: none (mapped scheme only)")
    for sheet, rows, cols, start in plan:
        _xlog("")
        _xlog("=== %s ===  start_row=%d  rows=%d" % (sheet, start, len(rows)))
        sums = {}
        for i, row in enumerate(rows):
            rno = start + i
            cells = []
            for col, field, fmt in cols:
                if field not in row:
                    continue
                val = row.get(field)
                if val in (None, ""):
                    continue
                if fmt:
                    try:
                        val = round(float(val), 6)
                        sums[field] = sums.get(field, 0.0) + val
                    except Exception:
                        pass
                cells.append("%s%d=%s" % (get_column_letter(col), rno, val))
            if cells:
                _xlog("   r%d  %s" % (rno, "  ".join(cells)))
        if sums:
            _xlog("   SUM  " + "  ".join("%s=%.6f" % (k, v) for k, v in sums.items()))


def _required_field_warnings(a_bl, a_cr, a_en, h_bl, h_cr, h_en, w_bl, w_cr, w_en):
    """Flag rows headed for the metric that are missing an input the metric
    silently needs to score them. A blank strategic significance, irreplaceable
    flag, watercourse encroachment or proposed enhancement condition does NOT
    error loudly — the metric scores that row zero (or shows a soft 'Check
    Data'), inflating or hollowing the result. validate_rows only checks habitat
    NAMES, so surface these too. Deduplicated to one message per issue type."""
    warns, seen = [], set()

    def flag(msg):
        if msg not in seen:
            seen.add(msg); warns.append(msg)

    def blank(row, field):
        return not str(row.get(field) or "").strip()

    for r in a_bl:
        is_tree = str(r.get("broad_habitat") or "") == "Individual trees"
        if blank(r, "condition"):
            # A blank condition zeroes the row. Habitats placed via the form always
            # carry one; a blank means an un-assessed feature (e.g. a field-survey
            # imported tree). Condition can't be defaulted safely — surface it.
            flag("area: a baseline %s has no condition assessed — it will score zero units."
                 % ("tree" if is_tree else "habitat"))
        if blank(r, "strategic_significance"):
            flag("area: a baseline habitat has no strategic significance — it will score zero units.")
        # Individual trees are never on the statutory irreplaceable list (and the
        # tree collector now defaults the flag to "No"), so don't demand it here —
        # that would false-alarm on essentially every scheme that maps a tree.
        if not is_tree and blank(r, "irreplaceable_habitat"):
            flag("area: a baseline habitat has a blank 'Irreplaceable habitat' — the metric can't "
                 "compute its baseline units (shows 'Confirm irreplaceable habitat status').")
    for r in a_cr:
        if blank(r, "strategic_significance"):
            flag("area: a created habitat has no strategic significance — it will score zero units.")
    for r in a_en:
        if blank(r, "prop_sub_habitat_type") or blank(r, "prop_condition"):
            flag("area: an enhancement is missing its proposed habitat or condition — it will score zero units.")
    for r in h_bl + h_cr:
        if blank(r, "strategic_significance"):
            flag("hedge: a hedgerow row has no strategic significance — it will score zero units.")
    for r in h_en:
        if blank(r, "prop_condition"):
            flag("hedge: an enhancement is missing its proposed condition — it will score zero units.")
    for r in w_bl + w_cr:
        if blank(r, "strategic_significance"):
            flag("water: a watercourse row has no strategic significance — it will score zero units.")
        if blank(r, "watercourse_encroachment") or blank(r, "riparian_encroachment_lr"):
            flag("water: a watercourse row has blank encroachment (channel and/or banks) — the metric "
                 "can't compute its units.")
    for r in w_en:
        if blank(r, "prop_condition"):
            flag("water: an enhancement is missing its proposed condition — it will score zero units.")
    return warns


def run_export_metric(iface, save_path, project_details=None, recipe=None):
    """Copy the bundled metric template then populate it with QGIS data. When a
    `recipe` (from the on-site Enhancements tab) is supplied, its interventions
    are overlaid on top of the mapped scheme."""
    try:
        _export_metric(iface, save_path, project_details, recipe=recipe)
    except PermissionError:
        QgsMessageLog.logMessage(
            f"Metric export destination locked:\n{traceback.format_exc()}",
            "Biome", Qgis.MessageLevel.Warning)
        iface.messageBar().pushMessage(
            "BNG Mapper",
            "The metric could not be replaced because the destination file is "
            "locked. Close it in Excel and export again, or choose a different "
            "file name.",
            level=Qgis.MessageLevel.Warning, duration=0)
    except Exception as e:
        QgsMessageLog.logMessage(f"Metric export error:\n{traceback.format_exc()}", "Biome", Qgis.MessageLevel.Critical)
        iface.messageBar().pushMessage(
            "BNG Mapper", f"Metric export failed: {e}", level=Qgis.MessageLevel.Critical, duration=0
        )


def _export_metric(iface, out_path, project_details=None, recipe=None):
    if not OPENPYXL_OK:
        raise RuntimeError(
            "openpyxl is not installed. "
            "Install it via: pip install openpyxl --break-system-packages"
        )
    if not os.path.isfile(_BUNDLED_TEMPLATE):
        raise RuntimeError(
            "Bundled BNG metric template not found inside the plugin folder."
        )

    import time
    _xlog("==== BIOME METRIC EXPORT  %s  ====" % time.strftime("%Y-%m-%d %H:%M:%S"), reset=True)
    _xlog("mode: %s" % ("WITH RECIPE" if recipe else "mapped scheme only"))

    # Flag baseline/proposed pairs that differ ONLY in strategic significance
    # (a likely data-entry slip). The user can reconcile both sides to one value
    # or export as-is; returning False means they cancelled.
    if not _check_strat_only_before_export(iface):
        return

    # Collect data. The classifier also returns the MAPPED enhancement targets
    # (a retained parcel whose proposed state is better — condition up or a
    # higher-distinctiveness habitat in the same broad). These are written to the
    # metric's enhancement sheets (A-3 / B-3) so the auto-spawned rows actually
    # score, instead of being left blank. Watercourses are never enhanced.
    # ── Collect the raw rows for every module FIRST (trees folded into area) ──
    # so an optional on-site Enhancements recipe can be overlaid before any
    # aggregation/splitting, then everything goes through the same pipeline.
    # Habitat MAPPED inside the blue line belongs on the metric's off-site D/E
    # sheets, not the on-site A/B ones. When any feature carries a spatial risk
    # category the A/B pass reads on-site only and the blue-line half is written
    # separately below; with nothing tagged this is None and every module reads
    # the whole map, exactly as before. The dashboard never passes the flag, so
    # it keeps netting blue against red — which is what it should show.
    try:
        from .offsite_from_map import (clear_mapped_offsite_context,
                                       configure_mapped_offsite,
                                       has_mapped_offsite,
                                       mapped_offsite_configured,
                                       mapped_offsite_included)
        if not mapped_offsite_configured():
            clear_mapped_offsite_context()
        _has_mapped_offsite = bool(has_mapped_offsite())
    except Exception as exc:
        _xlog("OFF-SITE detection failed: %s" % exc)
        raise RuntimeError(
            "Could not classify mapped on-site/off-site habitat: %s" % exc)
    if _has_mapped_offsite:
        if (not mapped_offsite_configured()
                and not configure_mapped_offsite(iface)):
            _xlog("OFF-SITE export cancelled in setup dialog")
            return
        _split_offsite = mapped_offsite_included()
        # Even when off-site habitat is excluded, explicitly restrict the main
        # A/B/C export to the red line.  Falling back to None here would read the
        # whole mapped scheme and incorrectly treat blue-line habitat as on-site.
        _onsite = False
    else:
        _split_offsite = False
        _onsite = None

    rows_area_bl, rows_area_cr, rows_area_en = classify_area_net(iface, offsite=_onsite)
    rows_tree_bl, rows_tree_cr, rows_tree_en = _collect_trees(iface, offsite=_onsite)
    rows_area_bl += _tree_rows_as_area_rows(rows_tree_bl, baseline=True)
    rows_area_cr += _tree_rows_as_area_rows(rows_tree_cr, baseline=False)
    rows_area_en += rows_tree_en
    rows_hedge_bl, rows_hedge_cr, rows_hedge_en = _collect_linear(
        iface, "baseline_hedgerow", "proposed_hedgerow", offsite=_onsite)
    rows_water_bl, rows_water_cr, rows_water_en = _collect_linear(
        iface, "baseline_water", "proposed_water", offsite=_onsite)

    _xlog("COLLECTED (pre-recipe): area bl/cr/en=%d/%d/%d  hedge=%d/%d/%d  water=%d/%d/%d"
          % (len(rows_area_bl), len(rows_area_cr), len(rows_area_en),
             len(rows_hedge_bl), len(rows_hedge_cr), len(rows_hedge_en),
             len(rows_water_bl), len(rows_water_cr), len(rows_water_en)))

    # Overlay a chosen Enhancements recipe (this is the SEPARATE export — the main
    # button passes recipe=None).
    #   • An ON-SITE recipe is applied on top of the mapped scheme: its
    #     enhancement / creation / tree / hedgerow interventions are added and the
    #     matching retained baseline quantity moved into enhanced / lost, so the
    #     exported metric = everything the main export does, plus the recipe.
    #   • An OFF-SITE recipe (recipe["offsite"]) leaves the on-site scheme exactly
    #     as mapped and writes the receptor + created/enhanced habitat into the
    #     off-site D/E/F sheets (with the spatial-risk category so the metric
    #     applies the SRM). Built below and appended to the write plan.
    offsite_plan = []
    # Habitat drawn inside the blue line: the receptor and what it becomes are
    # already on the map, so build D/E straight from it. This is additive — a
    # recipe-driven off-site plan (below) still works and simply appends.
    if _split_offsite:
        try:
            from .offsite_from_map import offsite_plan_from_map
            offsite_plan = offsite_plan_from_map(iface)
            _xlog("OFF-SITE from map: %d sheet(s) — %s"
                  % (len(offsite_plan), ", ".join(p[0] for p in offsite_plan)))
        except Exception as exc:
            _xlog("OFF-SITE from map FAILED: %s" % exc)
            offsite_plan = []
    if recipe:
        if recipe.get("offsite"):
            offsite_plan = (offsite_plan or []) + _build_offsite_plan(recipe)
        else:
            _apply_recipe_to_rows(recipe, rows_area_bl, rows_area_cr, rows_area_en,
                                  rows_hedge_bl, rows_hedge_cr, rows_hedge_en,
                                  rows_water_bl, rows_water_en)

    # ── Aggregate + split each module (the metric auto-spawns A-3/B-3/C-3 rows
    # in baseline order; the split builds A-1/A-3 in lock-step). ──
    # Tally the size mix of any individual trees folded into each area group
    # BEFORE aggregation merges the per-tree rows away, then write the breakdown
    # into the merged row's User Comments (after any condition assessment).
    _tree_counts_bl = _tree_size_counts_by_key(rows_area_bl, _AGG_KEYS["area_baseline"])
    _tree_counts_cr = _tree_size_counts_by_key(rows_area_cr, _AGG_KEYS["area_creation"])
    rows_area_bl                = _aggregate_rows_by_key(rows_area_bl, _AGG_KEYS["area_baseline"])
    rows_area_cr                = _aggregate_rows_by_key(rows_area_cr, _AGG_KEYS["area_creation"])
    _annotate_tree_size_comments(rows_area_bl, _tree_counts_bl, _AGG_KEYS["area_baseline"])
    _annotate_tree_size_comments(rows_area_cr, _tree_counts_cr, _AGG_KEYS["area_creation"])
    rows_area_bl, rows_area_en = _split_baseline_for_enhancement(
        rows_area_bl, rows_area_en,
        ("broad_habitat", "sub_habitat_type", "condition"),
        "area_ha", "area_retained_ha", "area_enhanced_ha")

    rows_hedge_bl               = _aggregate_rows_by_key(rows_hedge_bl, _AGG_KEYS["hedge_baseline"])
    rows_hedge_cr               = _aggregate_rows_by_key(rows_hedge_cr, _AGG_KEYS["hedge_creation"])
    rows_hedge_bl, rows_hedge_en = _split_baseline_for_enhancement(
        rows_hedge_bl, rows_hedge_en,
        ("sub_habitat_type", "condition"),
        "length_km", "length_retained_km", "length_enhanced_km")

    rows_water_bl               = _aggregate_rows_by_key(rows_water_bl, _AGG_KEYS["water_baseline"])
    rows_water_cr               = _aggregate_rows_by_key(rows_water_cr, _AGG_KEYS["water_creation"])
    rows_water_bl, rows_water_en = _split_baseline_for_enhancement(
        rows_water_bl, rows_water_en,
        ("sub_habitat_type", "condition",
         "watercourse_encroachment", "riparian_encroachment_lr"),
        "length_km", "length_retained_km", "length_enhanced_km")

    # Validate habitat names against the template's own lookup lists.
    # An unmatched name silently scores zero units in the metric, so give
    # the user the chance to fix the map before exporting.
    from .metric_xml_patcher import patch_metric, validate_rows
    warnings = validate_rows(_BUNDLED_TEMPLATE, [
        ("area",  rows_area_bl + rows_area_cr),
        ("hedge", rows_hedge_bl + rows_hedge_cr),
        ("water", rows_water_bl + rows_water_cr),
    ])
    # Also flag rows missing an input the metric silently needs to score them
    # (blank strategic significance / irreplaceable flag / watercourse
    # encroachment / proposed enhancement condition).
    warnings += _required_field_warnings(
        rows_area_bl, rows_area_cr, rows_area_en,
        rows_hedge_bl, rows_hedge_cr, rows_hedge_en,
        rows_water_bl, rows_water_cr, rows_water_en)
    # The off-site D/E/F rows are built separately (below); validate them too so a
    # mistyped receptor / target name or a blank required field is caught the same
    # way as on-site, instead of silently scoring zero off-site.
    if offsite_plan:
        _by = lambda pfx: [r for sh, rws, _c, _s in offsite_plan
                           if sh.startswith(pfx) for r in rws]
        off_d1, off_d2, off_d3 = _by("D-1"), _by("D-2"), _by("D-3")
        off_e1, off_e2 = _by("E-1"), _by("E-2")
        # F-1 matters as much as F-2 here: a RETAINED off-site reach only ever
        # appears on the baseline sheet, so validating creation alone would let
        # a blank encroachment through on exactly the rows the user is least
        # likely to have filled in.
        off_f1, off_f2 = _by("F-1"), _by("F-2")
        warnings += validate_rows(_BUNDLED_TEMPLATE, [
            ("area",  off_d1 + off_d2),
            ("hedge", off_e1 + off_e2),
            ("water", off_f1 + off_f2),
        ])
        warnings += _required_field_warnings(
            off_d1, off_d2, off_d3, off_e1, off_e2, [], off_f1, off_f2, [])
    if warnings:
        from qgis.PyQt.QtWidgets import QMessageBox
        msg = ("Some rows may not score correctly in the exported metric:\n\n• "
               + "\n• ".join(warnings)
               + "\n\nExport anyway?")
        reply = QMessageBox.question(
            iface.mainWindow(), "Check metric inputs", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

    # Write data directly into the template's worksheet XML. Unlike an
    # openpyxl round-trip, this leaves the workbook's macros, navigation
    # buttons, charts, drawings and conditional formatting byte-identical.
    _plan = [
        ("Start",                        _project_detail_rows(project_details),
                                                         _START_WRITE_COLS, _START_DATA_START_ROW),
        ("A-1 On-Site Habitat Baseline",    rows_area_bl,  _A1_WRITE_COLS, _A1_DATA_START_ROW),
        ("A-2 On-Site Habitat Creation",    rows_area_cr,  _A2_WRITE_COLS, _A2_DATA_START_ROW),
        ("A-3 On-Site Habitat Enhancement", rows_area_en,  _A3_WRITE_COLS, _A3_DATA_START_ROW),
        ("B-1 On-Site Hedge Baseline",      rows_hedge_bl, _B1_WRITE_COLS, _B1_DATA_START_ROW),
        ("B-2 On-Site Hedge Creation",      rows_hedge_cr, _B2_WRITE_COLS, _B2_DATA_START_ROW),
        ("B-3 On-Site Hedge Enhancement",   rows_hedge_en, _B3_WRITE_COLS, _B3_DATA_START_ROW),
        ("C-1 On-Site WaterC' Baseline",    rows_water_bl, _C1_WRITE_COLS, _C1_DATA_START_ROW),
        ("C-2 On-Site WaterC' Creation",    rows_water_cr, _C2_WRITE_COLS, _C2_DATA_START_ROW),
        ("C-3 On-Site WaterC' Enhancement", rows_water_en, _C3_WRITE_COLS, _C3_DATA_START_ROW),
    ]
    # Append the off-site (D/E/F) sheets for an off-site recipe. The on-site
    # scheme above is left untouched — off-site delivery sits alongside it.
    _plan += offsite_plan
    _log_export_plan(out_path, recipe, _plan)
    patch_metric(_BUNDLED_TEMPLATE, out_path, _plan)

    iface.messageBar().pushMessage(
        "BNG Mapper",
        f"Metric exported: {os.path.basename(out_path)}  "
        f"— open in Excel to review and complete the shaded columns.",
        level=Qgis.MessageLevel.Success, duration=12
    )


class MetricExportDialog(QDialog):
    """Save-As dialog for the pre-filled metric .xlsm.

    Also prompts for the metric's "Project details" (Start tab). Whatever the
    user enters is written into the metric; blank fields are left blank. The
    completion date is auto-filled with today's date (editable). The suggested
    filename uses the shared client name and project number.
    """
    def __init__(self, parent=None, includes_offsite=False):
        super().__init__(parent)
        self._includes_offsite = bool(includes_offsite)
        self.setWindowTitle("Export to Metric")
        self.setMinimumWidth(460)
        self._save_path = ""
        self._user_picked_name = False  # True once the user names the file by hand

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Pre-fill the BNG Statutory Metric from your QGIS data.\n"
            "Enter the project details below — anything left blank stays blank.\n"
            "The completion date is filled in for you."
        ))

        # ── Project details inputs ────────────────────────────────────────────
        # Pre-fill the fields shared with the Final Map exporter (applicant,
        # project name, completed-by, reviewer) from remembered values.
        remembered = load_remembered_details()
        form = QFormLayout()
        self._detail_edits = {}
        for key, label, _row, is_auto in PROJECT_DETAIL_FIELDS:
            edit = QLineEdit()
            if is_auto:
                edit.setText(today_completion_date())  # auto-filled, still editable
            elif remembered.get(key):
                edit.setText(remembered[key])          # remembered from a previous run
            else:
                edit.setPlaceholderText("(leave blank to skip)")
            self._detail_edits[key] = edit
            form.addRow(f"{label}:", edit)
        layout.addLayout(form)

        # Re-suggest the file name as either identifying field is typed.
        self._detail_edits["applicant"].textChanged.connect(self._refresh_suggested_name)
        self._detail_edits["project_name"].textChanged.connect(self._refresh_suggested_name)

        # ── Save location ─────────────────────────────────────────────────────
        layout.addWidget(QLabel("Save location:"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No location chosen…")
        self.path_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.setLayout(layout)

        # Restore the last save location used for THIS project (per-project) so
        # re-exporting doesn't need a re-browse. Lives in the project, so it
        # survives reloading the plugin and reopening the project.
        try:
            get_proj, _set_proj = _project_io()
            remembered_path = get_proj("metric/save_path", "")
            if remembered_path:
                if self._includes_offsite:
                    # The scope choice now precedes this dialog, so make sure a
                    # remembered on-site filename cannot hide that this workbook
                    # also contains off-site units. Keep its folder, not its name.
                    self._save_path = os.path.join(
                        os.path.dirname(remembered_path),
                        self._suggested_filename() + ".xlsm")
                    self._user_picked_name = False
                else:
                    self._save_path = remembered_path
                    self._user_picked_name = True   # respect the restored file name
                self.path_edit.setText(self._save_path)
        except Exception:
            pass

    def _suggested_filename(self):
        client_name = self._detail_edits["applicant"].text()
        project_number = self._detail_edits["project_name"].text()
        return build_metric_filename(
            client_name, project_number, self._includes_offsite)

    def _refresh_suggested_name(self):
        """Track client/project details until the user names the file manually."""
        if self._user_picked_name or not self._save_path:
            return
        folder = os.path.dirname(self._save_path)
        self._save_path = os.path.join(folder, self._suggested_filename() + ".xlsm")
        self.path_edit.setText(self._save_path)

    def _browse(self):
        start_dir = os.path.dirname(self._save_path) if self._save_path else ""
        default = os.path.join(start_dir, self._suggested_filename()) if start_dir \
            else self._suggested_filename()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Metric", default, "Excel Macro-Enabled Workbook (*.xlsm)"
        )
        if path:
            if not path.lower().endswith(".xlsm"):
                path += ".xlsm"
            self.path_edit.setText(path)
            self._save_path = path
            # If they kept the suggested name we can still auto-update it later;
            # if they typed their own, leave it alone.
            chosen = os.path.splitext(os.path.basename(path))[0]
            self._user_picked_name = (chosen != self._suggested_filename())

    def accept(self):
        # Remember the fields shared with the map exporter (and persist
        # completed-by / reviewer) before the export proceeds.
        try:
            remember_details(self.project_details())
        except Exception:
            pass
        # Remember this project's save location too (per-project).
        try:
            if self._save_path:
                _get_proj, set_proj = _project_io()
                set_proj("metric/save_path", self._save_path)
        except Exception:
            pass
        super().accept()

    def project_details(self):
        """Return the entered project details as a {key: value} dict."""
        return {key: edit.text().strip()
                for key, edit in self._detail_edits.items()}

    def save_path(self):
        return self._save_path


# ── Dialogs ───────────────────────────────────────────────────────────────────

class TableExportDialog(QDialog):
    """Save-As dialog for the reference table xlsx."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export to Table")
        self.setMinimumWidth(420)
        self._save_path = ""

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Choose where to save the BNG data table.\n"
            "Open it in Excel and copy each sheet into the matching metric tab."
        ))

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No location chosen…")
        self.path_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.setLayout(layout)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save BNG Data Table", "BNG Metric Data Table", "Excel Workbook (*.xlsx)"
        )
        if path:
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            self.path_edit.setText(path)
            self._save_path = path

    def save_path(self):
        return self._save_path


class BlankMetricDialog(QDialog):
    """Save-As dialog to copy the blank metric .xlsm."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save Blank Metric")
        self.setMinimumWidth(420)
        self._save_path = ""

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Choose where to save a blank copy of the BNG Statutory Metric."
        ))

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No location chosen…")
        self.path_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.setLayout(layout)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Blank Metric", BLANK_METRIC_BASENAME, "Excel Macro-Enabled Workbook (*.xlsm)"
        )
        if path:
            if not path.lower().endswith(".xlsm"):
                path += ".xlsm"
            self.path_edit.setText(path)
            self._save_path = path

    def save_path(self):
        return self._save_path


# ── Area feature collector ────────────────────────────────────────────────────


# ── Condition score lookup for spatial classification ─────────────────────────
_CONDITION_SCORE = {
    "Good":                 3.0,
    "Fairly Good":          2.5,
    "Moderate":             2.0,
    "Fairly Poor":          1.5,
    "Poor":                 1.0,
    "Condition Assessment N/A":         1.0,   # Condition Assessment N/A
    "N/A - Other": 0.0,   # N/A-Other = 0 units
}
# Geometry simplification tolerance for area/length measurements.
# Freehand-drawn polygons can have hundreds of near-zero micro-segments
# that introduce floating-point noise into area calculations.
# Simplifying at 0.1 map units (sub-decimetre) before measuring collapses
# these without changing any visible boundary.
_SIMPLIFY_TOLERANCE = 0.1

def _simplify_for_measurement(geom):
    """Return a simplified copy of geom suitable for area/length measurement.
    Uses Douglas-Peucker at _SIMPLIFY_TOLERANCE to remove freehand micro-vertices."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return geom
    try:
        simplified = geom.simplify(_SIMPLIFY_TOLERANCE)
        if simplified and not simplified.isNull() and not simplified.isEmpty():
            return simplified
    except Exception:
        pass
    return geom


_SLIVER_M2 = 1.0   # fragments smaller than this are geometry noise


# ═══════════════════════════════════════════════════════════════════════════
#  NET-DIFFERENCE ENGINE ADAPTERS  (single source of truth)
#
#  These thin adapters read the GeoPackage layers, build feature "buckets",
#  hand them to net_classifier.classify_net (the pure, unit-tested core), and
#  translate the result back into the exact legacy row dicts that the export
#  sheet writers and the in-plugin summary already consume.
#
#  Classification is now PURELY net difference between baseline and proposed
#  totals per (broad habitat, sub habitat, condition [, extra dims]).  It is
#  position-independent: identical baseline & proposed footprints net to zero
#  regardless of where they sit inside the redline.  No geometry overlay, no
#  user-set retention field, no floating-point intersection noise, and so no
#  scaling / reallocation correction is required anywhere downstream.
# ═══════════════════════════════════════════════════════════════════════════

def _qg_area_calc(layer):
    project = QgsProject.instance()
    ac = QgsDistanceArea()
    ac.setSourceCrs(layer.crs(), project.transformContext())
    ac.setEllipsoid(measurement_ellipsoid())
    return ac


def _feat_area_ha(geom, calc):
    """Ellipsoidal area in hectares, guarding against corrupt (negative) geometry."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return 0.0
    ha = calc.convertAreaMeasurement(calc.measureArea(geom), QgsUnitTypes.AreaUnit.AreaHectares)
    if ha < 0:
        # Hole larger than outer ring → measure outer ring only.
        try:
            poly = geom.asPolygon()
            if poly:
                from qgis.core import QgsGeometry
                outer = QgsGeometry.fromPolygonXY([poly[0]])
                ha = calc.convertAreaMeasurement(
                    calc.measureArea(outer), QgsUnitTypes.AreaUnit.AreaHectares)
        except Exception:
            ha = 0.0
    return round(max(0.0, ha), 6)


def _feat_length_km(geom, calc):
    if geom is None or geom.isNull() or geom.isEmpty():
        return 0.0
    return round(calc.measureLength(geom) / 1000.0, 6)


def _is_offsite_row(row):
    """A feature is OFF-SITE when it carries a spatial risk category — the
    field is stamped on anything drawn inside the blue line and left blank
    on-site. Untagged projects therefore behave exactly as before."""
    return bool(str(row.get("spatial_category", "") or "").strip())


def _area_buckets(layer_key, offsite=None):
    """Build net-difference buckets for an AREA layer (qty = hectares).

    ``offsite`` selects which half of the map to read: None (default) reads
    EVERYTHING, which is what the dashboard and the existing A/B/C export
    expect — the blue-line area nets against the redline exactly as before.
    Pass False for on-site only, True for the blue-line features, when
    building the split off-site export."""
    buckets = []
    layer = _get_layer(layer_key)
    if not layer or not layer.isValid():
        return buckets
    layer.reload()
    calc = _qg_area_calc(layer)
    masks = None
    if offsite is not None:
        from .offsite_from_map import boundary_masks
        masks = boundary_masks(layer)
    for feat in layer.getFeatures():
        row = _attrs_from_feat(feat)
        if not row.get("sub_habitat_type"):
            continue
        geom = feat.geometry()
        if offsite is not None:
            from .offsite_from_map import (mapped_default_strategic,
                                           mapped_spatial_category,
                                           scoped_geometry)
            geom = scoped_geometry(feat, layer, offsite, masks)
            if offsite and geom is not None and not geom.isEmpty():
                row["spatial_category"] = mapped_spatial_category()
                row["strategic_significance"] = (
                    row.get("strategic_significance")
                    or mapped_default_strategic())
            elif not offsite:
                row["spatial_category"] = ""
        ha = _feat_area_ha(geom, calc)
        if ha <= 0:
            continue
        buckets.append({
            "broad":   row.get("broad_habitat", ""),
            "sub":     row.get("sub_habitat_type", ""),
            "cond":    row.get("condition", ""),
            "strat":   row.get("strategic_significance", ""),
            "qty":     ha,
            "payload": row,
        })
    return buckets


def _linear_buckets(layer_key, water=False, offsite=None):
    """Build net-difference buckets for a LINEAR layer (qty = km).

    For watercourses, encroachment fields are folded into the matching key so
    that two otherwise-identical reaches with different encroachment are not
    netted against each other (they score differently in the metric)."""
    buckets = []
    layer = _get_layer(layer_key)
    if not layer or not layer.isValid():
        return buckets
    project = QgsProject.instance()
    calc = QgsDistanceArea()
    calc.setSourceCrs(layer.crs(), project.transformContext())
    calc.setEllipsoid(measurement_ellipsoid())
    layer.reload()
    masks = None
    if offsite is not None:
        from .offsite_from_map import boundary_masks
        masks = boundary_masks(layer)
    for feat in layer.getFeatures():
        row = _attrs_from_feat(feat)
        if not row.get("sub_habitat_type"):
            continue
        geom = feat.geometry()
        if offsite is not None:
            from .offsite_from_map import (mapped_default_strategic,
                                           mapped_spatial_category,
                                           scoped_geometry)
            geom = scoped_geometry(feat, layer, offsite, masks)
            if offsite and geom is not None and not geom.isEmpty():
                row["spatial_category"] = mapped_spatial_category()
                row["strategic_significance"] = (
                    row.get("strategic_significance")
                    or mapped_default_strategic())
            elif not offsite:
                row["spatial_category"] = ""
        km = _feat_length_km(geom, calc)
        if km <= 0:
            continue
        # Canonicalise the channel value and combine the two banks into the
        # metric's exact col-O string. For hedgerows these fields are blank, so
        # the combined value is "" and this is a harmless no-op.
        xkey = ()
        if water:
            from .bng_multipliers import (canonical_channel_encroachment,
                                          canonical_riparian_lr)
            # The watercourse TYPE is authoritative for culverts: the metric
            # marks every culvert channel/banks value as "N/A - Culvert"
            # regardless of what was stored, so force it here too.
            is_culvert = (str(row.get("sub_habitat_type") or "").strip() == "Culvert")
            channel = ("N/A - Culvert" if is_culvert else
                       canonical_channel_encroachment(row.get("watercourse_encroachment")))
            row["watercourse_encroachment"] = channel
            row["riparian_encroachment_lr"] = canonical_riparian_lr(
                row.get("riparian_encroachment_l"),
                row.get("riparian_encroachment_r"),
                is_culvert=is_culvert)
            xkey = (channel, row["riparian_encroachment_lr"])
        else:
            row["riparian_encroachment_lr"] = ""
        buckets.append({
            "broad":   row.get("broad_habitat", "") or row.get("sub_habitat_type", ""),
            "sub":     row.get("sub_habitat_type", ""),
            "cond":    row.get("condition", ""),
            "strat":   row.get("strategic_significance", ""),
            "xkey":    xkey,
            "qty":     km,
            "pair_id": row.get("pair_id", ""),
            "payload": row,
        })
    return buckets


def _tree_buckets(layer_key, offsite=None):
    """Build net-difference buckets for a TREE point layer.

    qty = the statutory area-equivalent of one tree (ha).  Because every tree
    of a given size has the same area, netting on area is identical to netting
    on count, but keeps the engine uniform across feature classes."""
    buckets = []
    layer = _get_layer(layer_key)
    if not layer or not layer.isValid():
        return buckets
    layer.reload()
    masks = None
    if offsite is not None:
        from .offsite_from_map import boundary_masks
        masks = boundary_masks(layer)
    for feat in layer.getFeatures():
        row = _attrs_from_feat(feat)
        sub = (row.get("sub_habitat_type") or "").strip()
        if not sub:
            continue
        if offsite is not None:
            from .offsite_from_map import (mapped_default_strategic,
                                           mapped_spatial_category,
                                           scoped_geometry)
            geom = scoped_geometry(feat, layer, offsite, masks)
            if geom is None or geom.isNull() or geom.isEmpty():
                continue
            if offsite:
                row["spatial_category"] = mapped_spatial_category()
                row["strategic_significance"] = (
                    row.get("strategic_significance")
                    or mapped_default_strategic())
            else:
                row["spatial_category"] = ""
        size     = tree_size_from_sub_type(sub)
        location = tree_location_from_sub_type(sub)
        area_ha  = TREE_SIZE_AREAS.get(size, 0.0041)
        row["tree_size"]     = size
        row["tree_location"] = location
        row["area_ha"]       = area_ha
        row["broad_habitat"] = "Individual trees"
        buckets.append({
            "broad":   "Individual trees",
            "sub":     location,             # metric "Tree Type" groups by location
            "cond":    row.get("condition", ""),
            "strat":   row.get("strategic_significance", ""),
            "xkey":    (size,),              # a tree only matches one of the same size
            "qty":     area_ha,
            "pair_id": row.get("pair_id", ""),
            "payload": row,
        })
    return buckets


def _merge_refs(payloads):
    """Concatenate habitat_ref / user_comments across a group's payloads."""
    refs = [str(p.get("habitat_ref") or "").strip() for p in payloads]
    refs = [r for r in refs if r]
    cmts = [str(p.get("user_comments") or "").strip() for p in payloads]
    cmts = [c for c in cmts if c]
    return (", ".join(dict.fromkeys(refs)), " | ".join(cmts))


def classify_area_net(iface, offsite=None):
    """NET-DIFFERENCE replacement for the old spatial classifier.

    Returns (baseline_rows, creation_rows, enhancement_rows) in the legacy
    schema, but derived purely from baseline-vs-proposed net difference.

    ``offsite`` is None for every caller except the export's on-site pass —
    the dashboard and summary read the whole map, so the blue-line area nets
    against the redline exactly as it always has."""
    from .net_classifier import classify_net
    from .habitat_data import DISTINCTIVENESS_SCORES
    from .bng_multipliers import area_enhancement_allowed

    bl_buckets = _area_buckets("baseline_area", offsite=offsite)
    pr_buckets = _area_buckets("proposed_area", offsite=offsite)
    # Area enhancement (DEFRA): retained habitat that improves in condition OR
    # changes to a higher-distinctiveness habitat within the same broad group —
    # but only where the metric actually accepts that change as enhancement
    # (area_enhancement_allowed); the rest are creation.
    res = classify_net(bl_buckets, pr_buckets, _CONDITION_SCORE,
                        enhance_scope=("broad",),
                        distinctiveness_score=DISTINCTIVENESS_SCORES,
                        can_enhance=area_enhancement_allowed)

    baseline_rows = []
    for grp in res["baseline_groups"].values():
        p = dict(grp["payload"])
        p["broad_habitat"]        = grp["broad"]
        p["sub_habitat_type"]     = grp["sub"]
        p["condition"]            = grp["cond"]
        p["area_ha"]              = round(grp["qty"], 6)
        p["area_retained_ha"]     = round(grp["retained"], 6)
        p["area_enhanced_ha"]     = round(grp["enhanced"], 6)
        p["area_lost_ha"]         = round(grp["lost"], 6)
        baseline_rows.append(p)

    creation_rows = []
    for c in res["created_groups"]:
        p = dict(c["payload"])
        p["broad_habitat"]         = c["broad"]
        p["sub_habitat_type"]      = c["sub"]
        p["condition"]             = c["cond"]
        p["strategic_significance"] = c["strat"]
        p["area_ha"]               = round(c["qty"], 6)
        creation_rows.append(p)

    enhancement_rows = []
    for e in res["enhanced_records"]:
        p = dict(e["bl_payload"])
        p["broad_habitat"]          = e["broad"]
        p["sub_habitat_type"]       = e["bl_sub"]
        p["condition"]              = e["bl_cond"]
        p["prop_sub_habitat_type"]  = e["pr_sub"]
        p["prop_habitat_type"]      = e["broad"]
        p["prop_condition"]         = e["pr_cond"]
        p["strategic_significance"] = e["strat"]
        p["area_ha"]                = round(e["qty"], 6)
        enhancement_rows.append(p)

    return baseline_rows, creation_rows, enhancement_rows


def classify_linear_net(iface, bl_key, pr_key, offsite=None):
    """NET-DIFFERENCE classification for a linear (hedgerow / watercourse) pair.

    Returns (baseline_rows, creation_rows, enhancement_rows) in the legacy
    linear schema (length_km / length_retained_km / length_enhanced_km)."""
    from .net_classifier import classify_net, classify_net_paired
    from .habitat_data import DISTINCTIVENESS_SCORES
    from .bng_multipliers import strategic_multiplier, water_encroachment_mult

    water = "water" in bl_key
    bl_buckets = _linear_buckets(bl_key, water=water, offsite=offsite)
    pr_buckets = _linear_buckets(pr_key, water=water, offsite=offsite)

    # Per-unit delivery rate of a reach — distinctiveness x condition x strategic
    # significance (x channel/banks encroachment for watercourses). Used by the
    # paired classifier to decide the SAME-distinctiveness-AND-condition case:
    # the metric allows enhancement on hedgerows / watercourses when the reach
    # delivers MORE units (e.g. reduced encroachment or higher strategic
    # significance); fewer units -> lost + created; equal -> retained.
    def _delivery(bucket):
        pay  = bucket.get("payload") or {}
        rate = (DISTINCTIVENESS_SCORES.get(bucket.get("sub", ""), 0)
                * _CONDITION_SCORE.get(bucket.get("cond", ""), 0)
                * strategic_multiplier(bucket.get("strat", "")
                                       or pay.get("strategic_significance", "")))
        if water:
            rate *= water_encroachment_mult(
                pay.get("watercourse_encroachment", ""),
                pay.get("riparian_encroachment_lr", ""))
        return rate

    # Hedgerows can be removed-and-replaced, so honour pair_id links (a planted
    # hedge must take the creation discount). Watercourses are not paired this
    # way, so they keep the plain net-difference path.
    if water:
        # Watercourses now use the SAME pair_id placement + classification as
        # hedgerows (user decision 2026-06-14): an identical baseline+proposed
        # twin is RETAINED; an improvement (condition up and/or distinctiveness
        # up) is ENHANCED; a removed-and-replaced reach (distinct pair_ids) or a
        # trading-DOWN change is LOST + CREATED. Watercourse trading (metric C-3
        # / G-7): enhancement may go to the SAME or HIGHER distinctiveness
        # (never down); a Culvert (locked at Poor) can only be enhanced by
        # moving to a STRICTLY HIGHER distinctiveness habitat.
        from .bng_multipliers import water_enhancement_allowed
        res = classify_net_paired(
            bl_buckets, pr_buckets, _CONDITION_SCORE,
            enhance_scope=("broad",), no_net=True,
            distinctiveness_score=DISTINCTIVENESS_SCORES,
            can_enhance=lambda bs, bc, ps, pc: water_enhancement_allowed(bs, ps),
            delivery_rate=_delivery)
    else:
        # Hedgerows enhance like area habitats: condition up OR a
        # higher-distinctiveness hedge type — but ONLY where the metric's G-6
        # matrix accepts that type change as enhancement (hedge_enhancement_
        # allowed); everything else (e.g. Non-native -> native) is creation.
        # NOTE: no delivery_rate for hedgerows. Unlike watercourses (whose C-3
        # scores a same-distinctiveness-and-condition reach if encroachment /
        # strategic significance improves), the metric's hedge B-3 returns
        # "Error - No enhancement" for any change without a condition OR
        # distinctiveness uplift — so a strat-only change must stay RETAINED.
        from .bng_multipliers import hedge_enhancement_allowed
        res = classify_net_paired(
            bl_buckets, pr_buckets, _CONDITION_SCORE,
            enhance_scope=("broad",), no_net=True,
            distinctiveness_score=DISTINCTIVENESS_SCORES,
            can_enhance=lambda bs, bc, ps, pc: hedge_enhancement_allowed(bs, ps))

    baseline_rows = []
    for grp in res["baseline_groups"].values():
        p = dict(grp["payload"])
        p["sub_habitat_type"]     = grp["sub"]
        p["condition"]            = grp["cond"]
        p["length_km"]            = round(grp["qty"], 6)
        p["length_retained_km"]   = round(grp["retained"], 6)
        p["length_enhanced_km"]   = round(grp["enhanced"], 6)
        p["length_lost_km"]       = round(grp["lost"], 6)
        # net classification supersedes any per-feature retention label
        p["retention"]            = ""
        baseline_rows.append(p)

    creation_rows = []
    for c in res["created_groups"]:
        p = dict(c["payload"])
        p["sub_habitat_type"]       = c["sub"]
        p["condition"]              = c["cond"]
        p["strategic_significance"] = c["strat"]
        p["length_km"]              = round(c["qty"], 6)
        creation_rows.append(p)

    enhancement_rows = []
    for e in res["enhanced_records"]:
        # Keep the BASELINE payload (incl. baseline encroachment) so the row
        # matches its C-1 baseline parent in _split_baseline_for_enhancement.
        p = dict(e["bl_payload"])
        p["sub_habitat_type"]       = e["bl_sub"]
        p["condition"]              = e["bl_cond"]
        p["prop_sub_habitat_type"]  = e["pr_sub"]
        p["prop_condition"]         = e["pr_cond"]
        p["strategic_significance"] = e["strat"]
        p["length_km"]              = round(e["qty"], 6)
        # The metric's C-3 inputs (AI/AK) and the enhancement UNITS use the
        # PROPOSED reach's encroachment, which may differ from the baseline
        # reach's (e.g. an enhancement that reduces riparian encroachment).
        pr_pay = e.get("pr_payload") or {}
        p["prop_watercourse_encroachment"] = (
            pr_pay.get("watercourse_encroachment")
            or p.get("watercourse_encroachment", ""))
        p["prop_riparian_encroachment_lr"] = (
            pr_pay.get("riparian_encroachment_lr")
            or p.get("riparian_encroachment_lr", ""))
        enhancement_rows.append(p)

    return baseline_rows, creation_rows, enhancement_rows


def classify_trees_net(iface, offsite=None):
    """NET-DIFFERENCE classification for individual trees.

    Returns (baseline_rows, creation_rows) in the legacy per-tree schema so the
    existing tree count-aggregation keeps working.  retained / enhanced / lost
    baseline trees are emitted as individual rows with the matching Retention
    label; created proposed trees become creation rows."""
    from .net_classifier import classify_net_paired

    bl_buckets = _tree_buckets("baseline_trees", offsite=offsite)
    pr_buckets = _tree_buckets("proposed_trees", offsite=offsite)
    res = classify_net_paired(bl_buckets, pr_buckets, _CONDITION_SCORE,
                              enhance_scope=("broad", "tree_size", "tree_location"),
                              no_net=True)

    def _per_tree_rows(payload, count, extra):
        out = []
        base = dict(payload)
        base["broad_habitat"] = "Individual trees"
        base.update(extra)
        n = int(round(count))
        for _ in range(max(0, n)):
            out.append(dict(base))
        return out

    baseline_rows = []
    for grp in res["baseline_groups"].values():
        area_each = TREE_SIZE_AREAS.get(
            grp["payload"].get("tree_size", "Small"), 0.0041) or 0.0041
        p = grp["payload"]
        baseline_rows += _per_tree_rows(p, grp["retained"] / area_each,
                                        {"retention": "Retained",
                                         "sub_habitat_type": grp["sub"],
                                         "condition": grp["cond"]})
        baseline_rows += _per_tree_rows(p, grp["enhanced"] / area_each,
                                        {"retention": "Enhanced",
                                         "sub_habitat_type": grp["sub"],
                                         "condition": grp["cond"]})
        baseline_rows += _per_tree_rows(p, grp["lost"] / area_each,
                                        {"retention": "Lost",
                                         "sub_habitat_type": grp["sub"],
                                         "condition": grp["cond"]})

    creation_rows = []
    for c in res["created_groups"]:
        area_each = TREE_SIZE_AREAS.get(
            c["payload"].get("tree_size", "Small"), 0.0041) or 0.0041
        creation_rows += _per_tree_rows(
            c["payload"], c["qty"] / area_each,
            {"sub_habitat_type": c["sub"], "condition": c["cond"],
             "strategic_significance": c["strat"]})

    # Enhancement targets (retained tree whose condition improves). Trees live in
    # the metric's A-1/A-2 area sheets, so these feed the same A-3 builder as
    # area habitats. The proposed habitat is the same tree type at a better
    # condition; broad is "Individual trees" so A-3's broad-locked dropdown
    # accepts it.
    enhancement_rows = []
    for e in res["enhanced_records"]:
        p = dict(e.get("bl_payload") or {})
        p["broad_habitat"]          = "Individual trees"
        p["sub_habitat_type"]       = e["bl_sub"]
        p["condition"]              = e["bl_cond"]
        p["prop_sub_habitat_type"]  = e["pr_sub"]
        p["prop_condition"]         = e["pr_cond"]
        p["strategic_significance"] = e.get("strat", "")
        p["area_ha"]                = round(e.get("qty", 0.0), 6)
        enhancement_rows.append(p)

    return baseline_rows, creation_rows, enhancement_rows


def _reallocate_creation_to_retained(baseline_rows, creation_rows):
    """DEPRECATED — retained for API compatibility.

    This previously undid a geometry-overlay artefact in which the same ground
    could be counted as both lost (baseline view) and created (proposed view).
    The net-difference engine (net_classifier.classify_net) never produces that
    artefact: for a non-growing group ``created = max(0, proposed - baseline)``
    is already zero, so there is nothing to reallocate.  The function is now an
    identity pass-through so existing call-sites keep working unchanged.
    """
    return baseline_rows, creation_rows


def _spatial_classify_area(iface):
    """Baseline-vs-proposed AREA classification (single source of truth).

    Name kept for backward compatibility with existing callers.  The body now
    delegates to the net-difference engine rather than overlaying geometry, so
    classification is position-independent and free of intersection noise.

    Returns (baseline_rows, creation_rows, enhancement_rows).
    """
    return classify_area_net(iface)

def _collect_area(iface, offsite=None):
    """Returns (baseline_rows, creation_rows, enhancement_rows) for area habitats.

    Delegates to the net-difference engine (single source of truth).  Kept for
    API compatibility; identical output to ``_spatial_classify_area``.
    """
    return classify_area_net(iface, offsite=offsite)

def _collect_linear(iface, bl_key, prop_key, offsite=None):
    """Returns (baseline_rows, creation_rows, enhancement_rows) for a linear layer.

    Now uses net difference between the baseline and proposed layers (the old
    version ignored the proposed layer and read a per-feature retention field).
    """
    return classify_linear_net(iface, bl_key, prop_key, offsite=offsite)

def _collect_trees(iface, offsite=None):
    """Collect individual tree features as net-difference rows.

    Returns (baseline_rows, creation_rows) in the legacy per-tree schema so the
    existing tree aggregation/sheet writers keep working.  Net difference makes
    trees position-independent: a baseline tree and a proposed tree of the same
    size/location/condition net to zero wherever they sit.
    """
    bl_rows, cr_rows, en_rows = classify_trees_net(iface, offsite=offsite)
    for r in bl_rows + cr_rows:
        r.setdefault("total_area_ha", r.get("area_ha", 0.0))
    return bl_rows, cr_rows, en_rows

def _tree_rows_as_area_rows(tree_rows, baseline):
    """Convert per-tree rows (legacy schema from _collect_trees) into the
    A-1 / A-2 area-row schema so individual trees land in the filled metric
    with their statutory area equivalents.

    Each input row represents ONE tree carrying its per-tree ``area_ha``
    (looked up from TREE_SIZE_AREAS by size).  Baseline rows carry a
    ``retention`` label which is translated into the A-1 retained /
    enhanced columns; the aggregation pass that follows in _export_metric
    then sums these per (broad habitat, habitat type, condition) group.
    """
    out = []
    for r in tree_rows:
        try:
            area = float(r.get("area_ha") or 0.0)
        except (ValueError, TypeError):
            area = 0.0
        if area <= 0:
            continue
        row = {
            "broad_habitat":          "Individual trees",
            # classify_trees_net sets sub_habitat_type to the metric's
            # habitat-type name ("Urban tree" / "Rural tree").
            "sub_habitat_type":       (r.get("sub_habitat_type")
                                       or r.get("tree_location")
                                       or "Rural tree"),
            "condition":              r.get("condition", ""),
            # Individual trees are placed via the B&T mapper, which doesn't carry a
            # strategic-significance or irreplaceable field. Both are REQUIRED by the
            # metric to score a tree row — a blank strategic significance zeroes it,
            # and a blank irreplaceable flag makes it show "Confirm irreplaceable
            # habitat status". Trees are never on the statutory irreplaceable list,
            # so default "No"; default the SS to the metric's low (x1.0) option (the
            # same default the habitat form uses) so the row always scores. Any real
            # value already on the feature is preserved.
            "strategic_significance": (r.get("strategic_significance") or DEFAULT_STRATEGIC_SIGNIFICANCE),
            "irreplaceable_habitat":  (r.get("irreplaceable_habitat") or "No"),
            "habitat_ref":            r.get("habitat_ref", ""),
            "user_comments":          r.get("user_comments", ""),
            "area_ha":                area,
            # Size/count of the individual tree this row represents. The area
            # sheets ignore these, but they let the export tally how many trees
            # of each size fold into the merged area row (see
            # _tree_size_counts_by_key); each per-tree row is exactly one tree.
            "tree_size":              r.get("tree_size", ""),
            "_tree_count":            1,
        }
        if baseline:
            ret = (r.get("retention") or "").strip().lower()
            row["area_retained_ha"] = area if ret == "retained" else 0.0
            row["area_enhanced_ha"] = area if ret == "enhanced" else 0.0
            row["area_lost_ha"]     = area if ret == "lost" else 0.0
        out.append(row)
    return out


def _aggregate_tree_rows(rows):
    """Group tree rows by (sub_habitat_type, condition, retention/size/location),
    summing count and total_area_ha."""
    if not rows:
        return []

    from collections import defaultdict
    _KEY = ("sub_habitat_type", "tree_size", "tree_location",
            "condition", "retention", "strategic_significance")

    groups = defaultdict(list)
    for row in rows:
        key = tuple(str(row.get(f) or "") for f in _KEY)
        groups[key].append(row)

    result = []
    for key, members in groups.items():
        merged = dict(members[0])
        count = len(members)
        area_each = merged.get("area_ha", 0.0)
        merged["_count"]       = count
        merged["total_area_ha"] = round(area_each * count, 6)
        # Concatenate habitat_refs and user_comments
        refs = ", ".join(str(m["habitat_ref"]) for m in members
                         if m.get("habitat_ref"))
        merged["habitat_ref"]   = refs or ""
        comments = " | ".join(str(m["user_comments"]) for m in members
                               if m.get("user_comments"))
        merged["user_comments"] = comments or ""
        result.append(merged)

    return sorted(result, key=lambda r: (r.get("sub_habitat_type") or "",
                                          r.get("condition") or ""))


# ── Tree-size breakdown note (folded tree area rows) ───────────────────────────
# Individual trees are folded into the metric's A-1/A-2 area sheets, where the
# per-size detail (Small / Medium / Large / Very Large) is otherwise lost when
# rows of the same habitat type + condition are merged. These helpers re-surface
# it as a plain-language note in the User Comments column, e.g.
#   "Comprises 3 small trees, 2 medium trees and 1 large tree."
# so a reviewer can see what makes up the area-equivalent without re-counting.

# Smallest → largest, for ordering the note.
_TREE_SIZE_ORDER = ("Small", "Medium", "Large", "Very Large")


def _tree_size_note(counts):
    """Plain-language tree-size breakdown from a {size: count} map, e.g.
    'Comprises 3 small trees, 2 medium trees and 1 very large tree.'
    Returns '' when there are no trees."""
    sizes = [s for s in _TREE_SIZE_ORDER if counts.get(s)]
    # keep any non-standard sizes too, after the known ones
    sizes += [s for s in counts if s and s not in _TREE_SIZE_ORDER and counts.get(s)]
    parts = []
    for size in sizes:
        n = int(counts[size])
        parts.append("%d %s %s" % (n, str(size).lower(), "tree" if n == 1 else "trees"))
    if not parts:
        return ""
    body = parts[0] if len(parts) == 1 else "%s and %s" % (", ".join(parts[:-1]), parts[-1])
    return "Comprises %s." % body


def _tree_size_counts_by_key(rows, key_fields):
    """Tally individual-tree counts per size for each aggregation group, keyed
    exactly as the area rows are aggregated. Only rows folded in from trees
    (broad habitat 'Individual trees') are counted; every other habitat is
    ignored. Must run BEFORE aggregation merges the per-tree rows away.
    Returns {key_tuple: {size: count}}."""
    from collections import defaultdict
    out = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if (r.get("broad_habitat") or "").strip() != "Individual trees":
            continue
        size = (r.get("tree_size") or "").strip()
        if not size:
            continue
        key = tuple(str(r.get(f) or "").strip() for f in key_fields)
        out[key][size] += int(r.get("_tree_count") or 1)
    return out


def _annotate_tree_size_comments(agg_rows, counts_by_key, key_fields):
    """Append the tree-size breakdown to the User Comments of each aggregated
    tree area row. The note is placed at the END of any existing comment so it
    follows a 'Condition Assessment: …' summary rather than displacing it."""
    for row in agg_rows:
        if (row.get("broad_habitat") or "").strip() != "Individual trees":
            continue
        key = tuple(str(row.get(f) or "").strip() for f in key_fields)
        note = _tree_size_note(counts_by_key.get(key, {}))
        if not note:
            continue
        existing = str(row.get("user_comments") or "").strip()
        row["user_comments"] = (existing + " | " + note) if existing else note


# ── Helpers ───────────────────────────────────────────────────────────────────

_SUM_FIELDS = {
    "area_ha", "area_retained_ha", "area_enhanced_ha", "area_lost_ha",
    "length_km", "length_retained_km", "length_enhanced_km",
}

# Per-sheet aggregation keys (field names from row dicts).
# Blanks ("") match blanks. habitat_ref and user_comments are always
# concatenated rather than keyed.
_AGG_KEYS = {
    # A-1 Habitat Baseline:  Broad Habitat + Habitat Type + Condition
    "area_baseline":  ("broad_habitat", "sub_habitat_type", "condition"),
    # A-2 Habitat Creation:  Broad Habitat + Habitat Type + Condition + Strategic Significance
    "area_creation":  ("broad_habitat", "sub_habitat_type", "condition", "strategic_significance"),
    # B-1 Hedgerow Baseline: Habitat Type + Condition + Strategic Significance + Retention
    "hedge_baseline": ("sub_habitat_type", "condition", "strategic_significance", "retention"),
    # B-2 Hedgerow Creation: Habitat Type + Condition + Strategic Significance
    "hedge_creation": ("sub_habitat_type", "condition", "strategic_significance"),
    # C-1 Watercourse Baseline: Habitat Type + Condition + Strategic Significance + Retention
    #                           + Watercourse Encroachment + Riparian Encroachment L/R
    "water_baseline": ("sub_habitat_type", "condition", "strategic_significance", "retention",
                       "watercourse_encroachment", "riparian_encroachment_lr"),
    # C-2 Watercourse Creation: Habitat Type + Condition + Strategic Significance
    #                           + Watercourse Encroachment + Riparian Encroachment L/R
    "water_creation": ("sub_habitat_type", "condition", "strategic_significance",
                       "watercourse_encroachment", "riparian_encroachment_lr"),
    # D-1/D-2 Trees: Tree Type + Size + Location + Condition (+ Retention for baseline)
    "tree_baseline":  ("sub_habitat_type", "tree_size", "tree_location", "condition", "retention"),
    "tree_creation":  ("sub_habitat_type", "tree_size", "tree_location", "condition"),
}


def _aggregate_rows_by_key(rows, key_fields):
    """Merge rows that are identical on key_fields (blanks match blanks),
    summing _SUM_FIELDS, concatenating habitat_ref (comma-sep) and
    user_comments (pipe-sep).  Sets '_count' on each output row."""
    from collections import OrderedDict
    grouped = OrderedDict()
    for row in rows:
        key = tuple(str(row.get(f) or "").strip() for f in key_fields)
        if key in grouped:
            g = grouped[key]
            g["_count"] += 1
            # Sum numeric fields
            for f in _SUM_FIELDS:
                if f in row:
                    try:
                        g[f] = round(float(g.get(f) or 0) + float(row[f] or 0), 6)
                    except (ValueError, TypeError):
                        pass
            # Concatenate habitat_ref (comma-separated, skip blanks)
            ref = str(row.get("habitat_ref") or "").strip()
            if ref:
                existing = str(g.get("habitat_ref") or "").strip()
                g["habitat_ref"] = f"{existing}, {ref}" if existing else ref
            # Concatenate user_comments (pipe-separated, skip blanks)
            cmt = str(row.get("user_comments") or "").strip()
            if cmt:
                existing = str(g.get("user_comments") or "").strip()
                g["user_comments"] = f"{existing} | {cmt}" if existing else cmt
        else:
            grouped[key] = dict(row)
            grouped[key]["_count"] = 1
            for f in _SUM_FIELDS:
                if f in grouped[key]:
                    try:
                        grouped[key][f] = float(grouped[key][f] or 0)
                    except (ValueError, TypeError):
                        grouped[key][f] = 0.0
    return list(grouped.values())


def _aggregate_rows(rows):
    """Legacy wrapper — kept so existing call-sites still work.
    Uses the old behaviour (key = all non-sum, non-ref fields)."""
    from collections import OrderedDict
    _SKIP = {"habitat_ref", "user_comments"}
    grouped = OrderedDict()
    for row in rows:
        key = tuple(
            (k, v) for k, v in sorted(row.items())
            if k not in _SKIP and k not in _SUM_FIELDS
        )
        if key in grouped:
            grouped[key]["_count"] += 1
            for f in _SUM_FIELDS:
                if f in row:
                    try:
                        grouped[key][f] = round(
                            float(grouped[key].get(f) or 0) + float(row[f] or 0), 6
                        )
                    except (ValueError, TypeError):
                        pass
        else:
            grouped[key] = dict(row)
            grouped[key]["_count"] = 1
            for f in _SUM_FIELDS:
                if f in grouped[key]:
                    try:
                        grouped[key][f] = float(grouped[key][f] or 0)
                    except (ValueError, TypeError):
                        grouped[key][f] = 0.0
    return list(grouped.values())


def _get_layer(layer_key):
    name   = LAYER_NAMES.get(layer_key)
    layers = QgsProject.instance().mapLayersByName(name) if name else []
    return layers[0] if layers else None


def _attrs_from_feat(feat):
    """Extract all stored attributes into a plain dict, skipping NULL/empty."""
    row = {}
    fields = feat.fields()
    for field in fields:
        name = field.name()
        val  = feat[name]
        if val is None or val == NULL:
            val = ""
        row[name] = str(val).strip() if val != "" else ""
    # Derive broad_habitat: check per-sub-type override first, then category default
    row["broad_habitat"] = _broad(row.get("habitat_type", ""),
                                  row.get("sub_habitat_type", ""))
    return row


def _broad(habitat_type, sub_habitat_type=""):
    """Map plugin habitat to metric Broad Habitat name.

    Checks BROAD_HABITAT_OVERRIDE (per sub-type) first, then falls back
    to BROAD_HABITAT_MAP (per category).  This handles cases where a single
    plugin UI category (e.g. "Coastal & Intertidal") spans multiple metric
    broad habitats (Rocky shore, Intertidal sediment, Coastal saltmarsh …).
    """
    from .habitat_data import BROAD_HABITAT_OVERRIDE
    if sub_habitat_type and sub_habitat_type in BROAD_HABITAT_OVERRIDE:
        return BROAD_HABITAT_OVERRIDE[sub_habitat_type]
    return BROAD_HABITAT_MAP.get(habitat_type, habitat_type)


# Handle QGIS NULL sentinel (only available inside QGIS runtime)
try:
    from qgis.core import NULL
except ImportError:
    NULL = None
