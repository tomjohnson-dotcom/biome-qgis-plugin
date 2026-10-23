# -*- coding: utf-8 -*-
"""
offsite_from_map.py
===================

Route habitat the ecologist MAPPED inside the blue line into the metric's
off-site sheets (D-1 / D-2 / D-3 / E-2).

``xlsm_exporter`` classifies purely by net difference and is position-
independent — it reads every feature regardless of where it sits. Off-site
rows have only ever come from an Enhance-tab *recipe*, so a receptor drawn on
the map was silently counted as ON-SITE in A-1 while the D sheets stayed empty.

A feature counts as off-site when it carries a ``spatial_category`` (the
metric's spatial-risk string). That field is stamped on anything inside the
blue line; on-site features leave it blank. Projects with no tagging behave
exactly as before, so this is inert until the boundary split has been run.

Enhancement vs creation is decided the way the metric does it: a proposed
habitat the metric accepts as an uplift of the receptor becomes a D-3
enhancement; anything else is a D-2 creation (receptor lost, new habitat made).
"""

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsDistanceArea, QgsGeometry, QgsProject, QgsUnitTypes)

from .config import measurement_ellipsoid
from .layer_manager import LAYER_NAMES

# Metric-exact strings, mirrored from enhancements_tab so the two off-site
# routes cannot drift. The trailing spaces are the metric's own — keep them.
SPATIAL_OPTIONS = [
    ("Inside LPA / NCA of impact (×1.0)",
     "Compensation inside LPA boundary or NCA of impact site ", 1.0),
    ("Neighbouring LPA / NCA (×0.75)",
     "Compensation outside LPA or NCA of impact site, but in neighbouring LPA or NCA ", 0.75),
    ("Outside / not neighbouring (×0.5)",
     "Compensation outside LPA or NCA of impact site and neighbouring LPA or NCA  ", 0.5),
]
STRATEGIC_OPTIONS = [
    ("Not in local strategy (Low ×1.0)",
     "Area/compensation not in local strategy/ no local strategy"),
    ("Ecologically desirable (Medium ×1.1)",
     "Location ecologically desirable but not in local strategy"),
    ("Formally in local strategy (High ×1.15)",
     "Formally identified in local strategy"),
]
DEFAULT_SPATIAL = SPATIAL_OPTIONS[0][1]
DEFAULT_STRATEGIC = STRATEGIC_OPTIONS[0][1]

_EPS_HA = 0.0005
_EPS_KM = 0.0005

# Gain-site references, one per module. The metric's positivity check runs PER
# REFERENCE (see offsite_rows_from_map), so every row of a module must share
# one, and the modules must not share with each other.
AREA_REF = "1"
HEDGE_REF = "2"
WATER_REF = "3"

# Set for the duration of one mapped off-site export by the confirmation
# dialog. Geometry decides WHAT is off-site; these are the statutory inputs
# geometry cannot infer.
_EXPORT_CONTEXT = None


def mapped_spatial_category():
    return ((_EXPORT_CONTEXT or {}).get("spatial_category")
            or DEFAULT_SPATIAL)


def mapped_default_strategic():
    return ((_EXPORT_CONTEXT or {}).get("default_strategic")
            or DEFAULT_STRATEGIC)


def mapped_strategic_significance(habitat, existing=""):
    """Strategic significance for one mapped off-site habitat.

    A habitat-specific export override wins, followed by the value already
    recorded on the feature, then the export's default.
    """
    overrides = (_EXPORT_CONTEXT or {}).get("strategic_by_habitat") or {}
    return (overrides.get(str(habitat or "").strip())
            or str(existing or "").strip()
            or mapped_default_strategic())


def mapped_offsite_ref(module):
    defaults = {"area": AREA_REF, "hedge": HEDGE_REF, "water": WATER_REF}
    return str(((_EXPORT_CONTEXT or {}).get("refs") or {}).get(
        module, defaults[module])).strip()


def mapped_offsite_included():
    """Whether the current metric export includes the mapped blue-line area."""
    return bool((_EXPORT_CONTEXT or {}).get("include_offsite"))


def mapped_offsite_configured():
    """Whether the scope/setup dialog has already run for this export."""
    return _EXPORT_CONTEXT is not None


def configure_mapped_offsite(iface):
    """Configure whether and how mapped blue-line habitat is exported.

    Returns False when the user cancels, allowing the export to stop without
    silently choosing its site scope. Workbook linkage references are generated
    internally; they are implementation details, not user inputs.
    """
    global _EXPORT_CONTEXT
    from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                                     QFormLayout, QHBoxLayout, QLabel,
                                     QPushButton, QRadioButton, QVBoxLayout,
                                     QWidget)

    dlg = QDialog(iface.mainWindow() if iface else None)
    dlg.setWindowTitle("Filled Metric Export Setup")
    dlg.setMinimumWidth(600)
    dlg.setStyleSheet(
        "QDialog{background:#F7F9F7;color:#1A2420;}"
        "QLabel{font-size:11px;}"
        "QRadioButton{font-size:12px;padding:7px 10px;border-radius:7px;}"
        "QRadioButton:hover{background:#EEF3EF;}"
        "QComboBox{background:white;border:1px solid #C9D1C9;border-radius:6px;"
        "padding:5px 8px;min-height:18px;}"
        "QPushButton{border:1px solid #BCC8C0;border-radius:6px;padding:6px 12px;"
        "background:white;color:#1F4A37;}"
        "QPushButton:hover{background:#EAF2EC;border-color:#7FA18D;}"
        "QPushButton:disabled{color:#9AA49E;background:#F2F4F2;border-color:#DDE2DE;}"
    )
    v = QVBoxLayout(dlg)
    v.setContentsMargins(18, 16, 18, 14)
    v.setSpacing(10)
    intro = QLabel(
        "Habitat has been found within the Blue Line but outside the Red Line. "
        "Choose whether that mapped off-site habitat should be included in "
        "this Filled Metric export.")
    intro.setWordWrap(True)
    intro.setStyleSheet(
        "background:#EEF5FC;border:1px solid #C8DAEE;border-radius:8px;"
        "color:#24465F;padding:10px 12px;font-size:11px;")
    v.addWidget(intro)

    scope_title = QLabel("EXPORT SCOPE")
    scope_title.setStyleSheet(
        "color:#315B48;font-size:10px;font-weight:600;letter-spacing:1px;"
        "padding-top:2px;")
    v.addWidget(scope_title)
    onsite_only = QRadioButton("Export Red Line habitats only")
    include_offsite = QRadioButton(
        "Include mapped off-site habitats within the Blue Line")
    onsite_only.setChecked(True)
    v.addWidget(onsite_only)
    v.addWidget(include_offsite)

    details = QWidget()
    details.setObjectName("offsiteDetails")
    details.setStyleSheet(
        "QWidget#offsiteDetails{background:#F1F6F3;border:1px solid #D3E1D8;"
        "border-radius:8px;}"
        "QWidget#offsiteDetails QLabel{background:transparent;}"
    )
    form = QFormLayout(details)
    form.setContentsMargins(12, 11, 12, 11)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)

    spatial = QComboBox()
    for label, value, _mult in SPATIAL_OPTIONS:
        spatial.addItem(label, value)
    form.addRow("Spatial risk category:", spatial)

    strategic = QComboBox()
    for label, value in STRATEGIC_OPTIONS:
        strategic.addItem(label, value)
    form.addRow("Default strategic significance:", strategic)
    strategic.setToolTip(
        "Used only where a mapped off-site feature has no strategic "
        "significance recorded on it.")

    strategic_overrides = {}
    custom_row = QHBoxLayout()
    custom_status = QLabel("No habitat-specific overrides")
    custom_status.setStyleSheet("color:#54635A;")
    custom_btn = QPushButton("Set individual habitats…")
    custom_row.addWidget(custom_status, 1)
    custom_row.addWidget(custom_btn)
    form.addRow("Habitat-specific significance:", custom_row)

    def _edit_strategic_overrides():
        habitats = mapped_offsite_habitat_names()
        editor = QDialog(dlg)
        editor.setWindowTitle("Strategic significance by habitat")
        editor.setMinimumWidth(590)
        ev = QVBoxLayout(editor)
        expl = QLabel(
            "Choose only the habitats that differ from the default. “Use "
            "default” leaves that habitat controlled by the main setting.")
        expl.setWordWrap(True)
        ev.addWidget(expl)
        ef = QFormLayout()
        combos = {}
        for habitat in habitats:
            combo = QComboBox()
            combo.addItem("Use default", "")
            for label, value in STRATEGIC_OPTIONS:
                combo.addItem(label, value)
            current = strategic_overrides.get(habitat, "")
            idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))
            ef.addRow(habitat + ":", combo)
            combos[habitat] = combo
        ev.addLayout(ef)
        eb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        eb.accepted.connect(editor.accept)
        eb.rejected.connect(editor.reject)
        ev.addWidget(eb)
        if editor.exec():
            strategic_overrides.clear()
            strategic_overrides.update({h: c.currentData() for h, c in combos.items()
                                        if c.currentData()})
            count = len(strategic_overrides)
            custom_status.setText(
                f"{count} habitat-specific override{'s' if count != 1 else ''}"
                if count else "No habitat-specific overrides")

    custom_btn.clicked.connect(_edit_strategic_overrides)
    v.addWidget(details)

    lnrs_btn = QPushButton("Open the relevant LNRS / strategic-significance map")
    lnrs_btn.setStyleSheet(
        "QPushButton{background:transparent;border:none;color:#246A4A;"
        "text-decoration:underline;text-align:left;padding:3px 1px;}"
        "QPushButton:hover{color:#174E36;background:transparent;}"
    )
    lnrs_btn.setToolTip(
        "Open the Local Nature Recovery Strategy map for this location to "
        "check which habitats are formally identified or ecologically desirable.")
    lnrs_btn.clicked.connect(lambda: open_strategic_significance_map(iface))
    v.addWidget(lnrs_btn)

    note = QLabel(
        "Biome will generate the workbook references needed to link each "
        "off-site baseline and delivery entry automatically.")
    note.setWordWrap(True)
    note.setStyleSheet("color:#66746C;font-size:10px;padding:1px 2px 4px 2px;")
    v.addWidget(note)
    # Keep the inputs in place so the layout does not jump. They are disabled
    # when the user chooses a Red-Line-only export because they will not apply.
    include_offsite.toggled.connect(details.setEnabled)
    include_offsite.toggled.connect(note.setEnabled)
    details.setEnabled(False)
    note.setEnabled(False)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok |
        QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
    buttons.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(
        "QPushButton{background:#2E6B4F;color:white;border:none;"
        "font-weight:600;padding:7px 17px;}"
        "QPushButton:hover{background:#24583F;}"
    )
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    v.addWidget(buttons)

    if not dlg.exec():
        _EXPORT_CONTEXT = None
        return False

    _EXPORT_CONTEXT = {
        "include_offsite": include_offsite.isChecked(),
        "spatial_category": spatial.currentData(),
        "default_strategic": strategic.currentData(),
        "strategic_by_habitat": dict(strategic_overrides),
        # Separate generated references keep the metric modules independent.
        "refs": {"area": AREA_REF, "hedge": HEDGE_REF, "water": WATER_REF},
    }
    return True


def clear_mapped_offsite_context():
    global _EXPORT_CONTEXT
    _EXPORT_CONTEXT = None


def prepare_mapped_offsite_export(iface):
    """Run the optional off-site preflight before the metric save dialog.

    Returns ``(proceed, includes_offsite)``. No dialog is shown when no mapped
    habitat occurs in Blue Line minus Red Line.
    """
    clear_mapped_offsite_context()
    if not has_mapped_offsite():
        return True, False
    if not configure_mapped_offsite(iface):
        return False, False
    return True, mapped_offsite_included()


def _layer(key):
    name = LAYER_NAMES.get(key)
    lyrs = QgsProject.instance().mapLayersByName(name) if name else []
    return lyrs[0] if lyrs else None


def _combined_boundary(layer_key, target_crs):
    """Union a boundary layer and return it in ``target_crs``."""
    boundary = _layer(layer_key)
    if boundary is None or not boundary.isValid():
        return None
    transform = None
    if boundary.crs() != target_crs:
        transform = QgsCoordinateTransform(
            boundary.crs(), target_crs, QgsProject.instance())
    geoms = []
    for feat in boundary.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            continue
        geom = QgsGeometry(geom)
        if transform is not None:
            try:
                geom.transform(transform)
            except Exception:
                continue
        geoms.append(geom)
    if not geoms:
        return None
    result = geoms[0] if len(geoms) == 1 else QgsGeometry.unaryUnion(geoms)
    return result if result and not result.isNull() and not result.isEmpty() else None


def boundary_masks(target_layer):
    """Return ``(redline, offsite)`` masks in a habitat layer's CRS.

    Off-site is the current blue-line area minus the current redline area.
    Calculating this from live geometry makes classification independent of
    whether boundaries or habitat features were drawn first.
    """
    if target_layer is None or not target_layer.isValid():
        return None, None
    redline = _combined_boundary("redline", target_layer.crs())
    blue = _combined_boundary("site_boundary", target_layer.crs())
    if blue is None:
        return redline, None
    offsite = QgsGeometry(blue)
    if redline is not None:
        try:
            offsite = offsite.difference(redline)
        except Exception:
            pass
    if offsite is None or offsite.isNull() or offsite.isEmpty():
        # Keep an empty geometry (rather than None) to record that a blue line
        # exists but contributes no area outside the redline.
        offsite = QgsGeometry()
    return redline, offsite


def scoped_geometry(feat, layer, offsite, masks=None):
    """Return the part of a feature belonging to the requested site scope.

    ``offsite`` True selects blue minus red, False selects red, and None keeps
    the whole feature for dashboards and other non-split callers.  Legacy
    attribute tags remain the fallback when no blue-line geometry exists.
    """
    geom = feat.geometry()
    if geom is None or geom.isNull() or geom.isEmpty() or offsite is None:
        return geom
    redline, offsite_mask = masks or boundary_masks(layer)
    # A blue boundary activates geometry-based classification.  If there is no
    # blue boundary, retain the old tag behaviour for older projects/recipes.
    if offsite_mask is None:
        tagged = bool(_tag(feat))
        return geom if tagged == offsite else QgsGeometry()
    mask = offsite_mask if offsite else redline
    if mask is None:
        return QgsGeometry()
    try:
        return geom.intersection(mask)
    except Exception:
        return QgsGeometry()


def _tag(feat):
    """The spatial risk category, VERBATIM.

    Never strip it. The metric's G-3 lookup is byte-exact and its category
    strings carry a trailing space — "…NCA of impact site ". Strip that and the
    lookup misses, the multiplier resolves to 0, and the whole off-site gain is
    deducted as spatial risk instead of passing through at x1.0.
    """
    try:
        v = feat["spatial_category"]
    except (KeyError, IndexError):
        return ""
    if v is None:
        return ""
    # A NULL attribute is Python None on QGIS 4, but a QVariant on QGIS 3 —
    # which is not None, and whose str() is the literal "NULL". Left
    # unhandled, every untagged feature on QGIS 3 reads as off-site and a
    # project with no blue line at all exports D/E sheets.
    try:
        if v.isNull():
            return ""
    except AttributeError:
        pass
    v = str(v)
    if not v.strip() or v.strip().upper() == "NULL":
        return ""
    return v


def _measurer(layer):
    da = QgsDistanceArea()
    da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    da.setEllipsoid(measurement_ellipsoid())
    return da


def _gather(layer_key, area=True):
    """{(sub, cond, strategic, spatial): qty} for the OFF-SITE features."""
    out = {}
    lyr = _layer(layer_key)
    if lyr is None or not lyr.isValid():
        return out
    da = _measurer(lyr)
    masks = boundary_masks(lyr)
    for f in lyr.getFeatures():
        g = scoped_geometry(f, lyr, True, masks)
        if g is None or g.isNull() or g.isEmpty():
            continue
        # The export dialog is authoritative for the current mapped gain site.
        cat = mapped_spatial_category()
        sub = str(f["sub_habitat_type"] or "").strip()
        if not sub:
            continue
        if area:
            qty = da.convertAreaMeasurement(
                da.measureArea(g), QgsUnitTypes.AreaUnit.SquareMeters) / 10000.0
        else:
            qty = da.convertLengthMeasurement(
                da.measureLength(g), QgsUnitTypes.DistanceUnit.Meters) / 1000.0
        if qty <= 0:
            continue
        # Verbatim too — the metric's strategic-significance strings are matched
        # byte-for-byte in the same way.
        ss = mapped_strategic_significance(sub, f["strategic_significance"])
        key = (sub, str(f["condition"] or "").strip(), ss, cat)
        out[key] = out.get(key, 0.0) + qty
    return out


def mapped_offsite_habitat_names():
    """Sorted habitat types actually present in Blue Line minus Red Line."""
    names = set()
    for key, area in (("baseline_area", True), ("proposed_area", True),
                      ("baseline_hedgerow", False), ("proposed_hedgerow", False),
                      ("baseline_water", False), ("proposed_water", False)):
        names.update(k[0] for k in _gather(key, area=area))
    for key in ("baseline_trees", "proposed_trees"):
        lyr = _layer(key)
        if lyr is None or not lyr.isValid():
            continue
        masks = boundary_masks(lyr)
        for feat in lyr.getFeatures():
            geom = scoped_geometry(feat, lyr, True, masks)
            if geom is None or geom.isNull() or geom.isEmpty():
                continue
            try:
                sub = str(feat["sub_habitat_type"] or "").strip()
            except Exception:
                sub = ""
            if sub:
                names.add(sub)
    return sorted(names, key=str.casefold)


def open_strategic_significance_map(iface=None):
    """Open the existing site-specific LNRS deep-link used elsewhere in Biome."""
    try:
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices
        from .council_maps import build_lnrs_deeplink

        bng = QgsCoordinateReferenceSystem("EPSG:27700")
        geom = _combined_boundary("site_boundary", bng)
        if geom is None or geom.isNull() or geom.isEmpty():
            geom = _combined_boundary("redline", bng)
        if geom is None or geom.isNull() or geom.isEmpty():
            return False
        ext = geom.boundingBox()
        centre = geom.centroid().asPoint()
        easting, northing = centre.x(), centre.y()
        margin = 60.0
        bbox = (ext.xMinimum() - margin, ext.yMinimum() - margin,
                ext.xMaximum() + margin, ext.yMaximum() + margin)
        url, _kind, _area, _authority = build_lnrs_deeplink(
            easting, northing, bbox, (easting, northing))
        return bool(url) and QDesktopServices.openUrl(QUrl(url))
    except Exception:
        return False


def has_mapped_offsite():
    """True when any mapped habitat intersects Blue Line minus Red Line.

    This checks actual clipped habitat content, not merely whether a Blue Line
    boundary exists. A true result means the export may need D/E/F sheets as
    well as A/B/C, subject to the user's export-scope choice.

    Watercourses are included. While they were missing, a project whose ONLY
    blue-line habitat was a reach never turned the split on, so the reach was
    written to C-1 as if it were on site and never took its spatial risk.
    """
    for key, area in (("baseline_area", True), ("proposed_area", True),
                      ("baseline_hedgerow", False), ("proposed_hedgerow", False),
                      ("baseline_water", False), ("proposed_water", False)):
        if _gather(key, area=area):
            return True
    for key in ("baseline_trees", "proposed_trees"):
        lyr = _layer(key)
        if lyr is None or not lyr.isValid():
            continue
        masks = boundary_masks(lyr)
        for feat in lyr.getFeatures():
            geom = scoped_geometry(feat, lyr, True, masks)
            if geom is not None and not geom.isNull() and not geom.isEmpty():
                return True
    return False


def offsite_water_rows(iface=None):
    """The F-1 / F-2 rows for reaches drawn inside the blue line.

    Deliberately NOT built from ``_gather`` like the hedgerow rows are. A
    watercourse carries two extra inputs the metric will not score without —
    channel encroachment (col M) and the combined both-banks string (col O) —
    and the canonicalisation behind them (culvert forcing, L/R merge) already
    lives in ``xlsm_exporter._linear_buckets``. Reusing the export's own
    collector keeps an off-site reach behaving exactly like an on-site one
    instead of re-deriving those rules a second time and drifting.
    """
    from .xlsm_exporter import _collect_linear

    try:
        bl_rows, cr_rows, _en_rows = _collect_linear(
            iface, "baseline_water", "proposed_water", offsite=True)
    except Exception:
        return [], []

    ref = mapped_offsite_ref("water")
    f1, f2 = [], []
    for rows, out in ((bl_rows, f1), (cr_rows, f2)):
        for r in rows:
            row = dict(r)
            row["offsite_ref"] = ref
            # The collector strips every field; put the trailing space back.
            row["spatial_category"] = canonical_spatial(r.get("spatial_category"))
            if out is f2:
                row.setdefault("baseline_ref", ref)
            out.append(row)
    return f1, f2


def canonical_spatial(category):
    """The metric-exact spatial risk string for a possibly-stripped value.

    Anything routed through ``xlsm_exporter._attrs_from_feat`` has had
    ``.strip()`` applied to every field, which silently removes the trailing
    space the metric's category strings carry. That miss resolves the SRM to 0
    and the entire off-site gain is deducted as spatial risk (it once took a
    job's final total to -9.49). Map the value back before writing or scoring.
    """
    if not category:
        return ""
    c = str(category).strip()
    for _label, value, _mult in SPATIAL_OPTIONS:
        if value.strip() == c:
            return value
    return category


def spatial_multiplier(category):
    """The metric's spatial risk factor for a category string.

    Matched verbatim first — the metric's own strings carry a trailing space
    (see ``_tag``) — with a stripped comparison only as a fallback.
    """
    for _label, value, mult in SPATIAL_OPTIONS:
        if value == category:
            return mult
    c = (category or "").strip()
    for _label, value, mult in SPATIAL_OPTIONS:
        if value.strip() == c:
            return mult
    return 1.0


def offsite_pairings():
    """Receptor -> target pairings for the mapped off-site area block.

    ONE list, read by both the metric export (which turns it into D-1/D-2/D-3
    rows) and the dashboard (which turns it into units). Whether a change is a
    creation or an enhancement decides which temporal discount applies, so the
    two must never form that opinion separately.

    Each entry: receptor (``rec_*``), target (``sub``/``cond``/``ss``/``cat``),
    the area transferred (``take``, ha), plus ``retained`` / ``enhanced``.

    Exact baseline/proposed matches MUST be consumed first.  Without that pass,
    an unchanged off-site parcel was paired as a non-uplift and therefore sent
    to D-2 as creation (with its baseline shown as lost).  That disagreed with
    both the mapped ground and the on-site net classifier.
    """
    from .bng_multipliers import area_enhancement_allowed
    from .enhancement_engine import baseline_units

    bl = _gather("baseline_area", area=True)
    pr = _gather("proposed_area", area=True)

    out = []

    # Retained first: same habitat, condition, strategic significance and
    # spatial-risk category on both sides.  The export dialog supplies one
    # spatial category to both baseline and proposed, so this also guarantees
    # that the popup cannot manufacture a difference between them.
    remaining_bl = dict(bl)
    remaining_pr = dict(pr)
    for key in sorted(set(remaining_bl) & set(remaining_pr)):
        take = min(remaining_bl.get(key, 0.0), remaining_pr.get(key, 0.0))
        if take <= _EPS_HA:
            continue
        sub, cond, ss, cat = key
        out.append({"rec_sub": sub, "rec_cond": cond,
                    "rec_ss": ss, "rec_cat": cat,
                    "sub": sub, "cond": cond, "ss": ss, "cat": cat,
                    "take": take, "retained": True, "enhanced": False})
        remaining_bl[key] = max(0.0, remaining_bl[key] - take)
        remaining_pr[key] = max(0.0, remaining_pr[key] - take)

    # Receptors, largest first; each proposed habitat consumes receptor area in
    # turn, so a receptor that is only partly changed still balances.
    remaining = dict(sorted(remaining_bl.items(), key=lambda kv: -kv[1]))
    todo = sorted(remaining_pr.items(), key=lambda kv: -kv[1])

    for (rec_sub, rec_cond, rec_ss, rec_cat), _q in list(remaining.items()):
        for i, ((sub, cond, ss, cat), qty) in enumerate(todo):
            if qty <= _EPS_HA:
                continue
            avail = remaining.get((rec_sub, rec_cond, rec_ss, rec_cat), 0.0)
            if avail <= _EPS_HA:
                break
            take = min(avail, qty)
            if take <= _EPS_HA:
                continue
            remaining[(rec_sub, rec_cond, rec_ss, rec_cat)] = avail - take
            todo[i] = ((sub, cond, ss, cat), qty - take)

            # Enhancement vs creation: off-site D-3 accepts a lower-
            # distinctiveness starting point where on-site A-3 does not (the
            # original workbook enhanced cropland straight to lowland meadow),
            # so the on-site trading rule is the wrong test. What matters is
            # whether the target is a genuine uplift of the land that is there.
            # Compared before temporal discount, which only reduces the
            # proposed side.
            try:
                enhanced = (baseline_units(sub, cond, take, ss)
                            > baseline_units(rec_sub, rec_cond, take, rec_ss))
            except Exception:
                try:
                    enhanced = bool(area_enhancement_allowed(rec_sub, sub))
                except Exception:
                    enhanced = False
            out.append({"rec_sub": rec_sub, "rec_cond": rec_cond,
                        "rec_ss": rec_ss, "rec_cat": rec_cat,
                        "sub": sub, "cond": cond, "ss": ss, "cat": cat,
                        "take": take, "retained": False,
                        "enhanced": enhanced})
    return out


def offsite_rows_from_map():
    """Build the D-1 / D-2 / D-3 / E-1 / E-2 row dicts. Schemas match
    ``xlsm_exporter._build_offsite_plan`` exactly so the sheet writers are
    reused untouched."""
    from .enhancement_engine import broad_group
    from .xlsm_exporter import _broad

    bl_h = _gather("baseline_hedgerow", area=False)
    pr_h = _gather("proposed_hedgerow", area=False)

    d1, d2, d3, e1, e2 = [], [], [], [], []
    # Gain-site references. The blue-line block is ONE area gain site; the
    # hedgerow creation is a separate one, exactly as the original metric for
    # this job numbered them.
    #
    # ONE off-site reference for the whole gain site. The metric's positivity
    # check lives on the hidden "Off-site gain site summary" sheet and runs
    # PER REFERENCE:
    #     IF(K<0, "Error - gain sites must be positive", ...)
    # where K is that reference's net unit change. A reference per pairing
    # turns every row into its own one-row gain site, so an access track over
    # a new meadow reads as a failing site and the metric rejects the whole
    # block. Sharing one reference lets the losing rows be absorbed by the
    # gains, which is how the original metric for this job was built.
    r = mapped_offsite_ref("area")
    hedge_ref = mapped_offsite_ref("hedge")

    # Individual trees use the same D-1/D-2 area sheets as other area
    # habitats.  Classify them geometrically too, including projects where the
    # blue line was added after the trees were placed.
    try:
        from .xlsm_exporter import _collect_trees, _tree_rows_as_area_rows
        tree_bl, tree_cr, _tree_en = _collect_trees(None, offsite=True)
        for row in _tree_rows_as_area_rows(tree_bl, baseline=True):
            row["spatial_category"] = mapped_spatial_category()
            row["offsite_ref"] = r
            d1.append(row)
        for row in _tree_rows_as_area_rows(tree_cr, baseline=False):
            row["spatial_category"] = mapped_spatial_category()
            row["offsite_ref"] = r
            row["baseline_ref"] = r
            d2.append(row)
    except Exception:
        pass

    for p in offsite_pairings():
        take = p["take"]
        d1.append({
            "broad_habitat": _broad(broad_group(p["rec_sub"]), p["rec_sub"]),
            "sub_habitat_type": p["rec_sub"], "irreplaceable_habitat": "No",
            "condition": p["rec_cond"], "strategic_significance": p["rec_ss"],
            "spatial_category": p["rec_cat"], "area_ha": round(take, 6),
            "offsite_ref": r,
            "area_retained_ha": round(take, 6) if p.get("retained") else 0.0,
            "area_enhanced_ha": round(take, 6) if p["enhanced"] else 0.0,
        })
        if p.get("retained"):
            # D-1 carries retained off-site habitat on both sides of the
            # metric.  There must be no corresponding D-2/D-3 row.
            continue
        elif p["enhanced"]:
            d3.append({"prop_sub_habitat_type": p["sub"],
                       "prop_condition": p["cond"],
                       "strategic_significance": p["ss"], "offsite_ref": r})
        else:
            d2.append({"broad_habitat": _broad(broad_group(p["sub"]), p["sub"]),
                       "sub_habitat_type": p["sub"], "condition": p["cond"],
                       "strategic_significance": p["ss"],
                       "spatial_category": p["cat"],
                       "area_ha": round(take, 6),
                       "offsite_ref": r, "baseline_ref": r})

    # E-1 carries the receptor's existing reaches exactly as D-1 does for
    # areas. Leaving it out made the metric's hedgerow baseline 4.33 against
    # the dashboard's 5.84, so the same +1.54 gain read 35.68% instead of
    # 26.45%. A retained off-site reach must appear on both sides.
    for (sub, cond, ss, cat), km in sorted(bl_h.items(), key=lambda kv: -kv[1]):
        if km <= _EPS_KM:
            continue
        kept = min(km, sum(v for (s, c, _s2, _c2), v in pr_h.items()
                           if s == sub and c == cond))
        e1.append({"sub_habitat_type": sub, "condition": cond,
                   "strategic_significance": ss, "spatial_category": cat,
                   "length_km": round(km, 6),
                   "length_retained_km": round(kept, 6),
                   "length_enhanced_km": 0.0,
                   "offsite_ref": hedge_ref})

    # Only NET new length counts as creation.
    for (sub, cond, ss, cat), km in sorted(pr_h.items(), key=lambda kv: -kv[1]):
        net = km - sum(v for (s, c, _s2, _c2), v in bl_h.items()
                       if s == sub and c == cond)
        if net <= _EPS_KM:
            continue
        e2.append({"sub_habitat_type": sub, "condition": cond or "Poor",
                   "strategic_significance": ss, "spatial_category": cat,
                   "length_km": round(net, 6), "offsite_ref": hedge_ref})

    return d1, d2, d3, e1, e2


def _rate(sub, cond, ss, qty):
    """Undiscounted unit value: quantity x distinctiveness x condition x
    strategic significance. Quantity is ha for area, km for hedgerow."""
    from .bng_multipliers import strategic_multiplier
    from .habitat_data import DISTINCTIVENESS_SCORES
    from .xlsm_exporter import _CONDITION_SCORE
    return (qty * DISTINCTIVENESS_SCORES.get(sub, 0)
            * _CONDITION_SCORE.get(cond, 0) * strategic_multiplier(ss))


def offsite_area_units():
    """``(baseline_units, proposed_units)`` for the mapped off-site AREA block.

    The dashboard must not put this land through the on-site net classifier.
    A-3's trading rule refuses a change that crosses broad groups, so cropland
    -> lowland meadow drops out as a CREATION and takes the whole-value
    creation discount. The metric puts the very same change through D-3 as an
    ENHANCEMENT, which keeps the receptor's baseline value whole and discounts
    only the uplift. Same land, same target, materially different multiplier —
    which is why the off-site portion is measured here instead.
    """
    from . import bng_multipliers as M

    bl_u = pr_u = 0.0
    for p in offsite_pairings():
        ha = p["take"]
        srm = spatial_multiplier(p["cat"])
        base_val = _rate(p["rec_sub"], p["rec_cond"], p["rec_ss"], ha)
        bl_u += base_val
        base = _rate(p["sub"], p["cond"], p["ss"], ha)
        if p.get("retained"):
            # Retained habitat keeps its baseline units. Spatial risk applies
            # to delivered gain, not to unchanged baseline habitat.
            pr_u += base_val
        elif p["enhanced"]:
            mult, _n = M.proposed_multiplier(
                "enhanced", p["sub"], p["cond"],
                start_condition=p["rec_cond"], baseline_habitat=p["rec_sub"])
            pr_u += M.blend_enhanced_units(base_val, base, mult) * srm
        else:
            mult, _n = M.proposed_multiplier("created", p["sub"], p["cond"])
            pr_u += base * mult * srm
    return bl_u, pr_u


def offsite_hedge_units():
    """``(baseline_units, proposed_units)`` for the mapped off-site HEDGEROW
    block, read off the same E-1 / E-2 rows the export writes."""
    from . import bng_multipliers as M

    e1 = e2 = None
    _d1, _d2, _d3, e1, e2 = offsite_rows_from_map()

    bl_u = pr_u = 0.0
    for r in e1:
        bl_u += _rate(r["sub_habitat_type"], r["condition"],
                      r["strategic_significance"], float(r["length_km"] or 0.0))
        # Retained reaches carry on at their baseline rate — never discounted.
        pr_u += _rate(r["sub_habitat_type"], r["condition"],
                      r["strategic_significance"],
                      float(r["length_retained_km"] or 0.0))
    for r in e2:
        base = _rate(r["sub_habitat_type"], r["condition"],
                     r["strategic_significance"], float(r["length_km"] or 0.0))
        mult, _n = M.proposed_multiplier("created", r["sub_habitat_type"],
                                         r["condition"])
        pr_u += base * mult * spatial_multiplier(r.get("spatial_category", ""))
    return bl_u, pr_u


def offsite_water_units(iface=None):
    """``(baseline_units, proposed_units)`` for the mapped off-site
    WATERCOURSE block, off the same F-1 / F-2 rows the export writes.

    Every reach is scaled by its channel x banks encroachment, exactly as the
    on-site watercourse path does — that scaling is what makes a watercourse
    unit different from a hedgerow one.
    """
    from . import bng_multipliers as M

    f1, f2 = offsite_water_rows(iface)

    def _km(r, key):
        return float(r.get(key) or 0.0)

    bl_u = pr_u = 0.0
    for r in f1:
        enc = M.water_row_encroachment_mult(r)
        bl_u += _rate(r.get("sub_habitat_type", ""), r.get("condition", ""),
                      r.get("strategic_significance", ""), _km(r, "length_km")) * enc
        # A retained reach carries on at its baseline rate — never discounted.
        pr_u += _rate(r.get("sub_habitat_type", ""), r.get("condition", ""),
                      r.get("strategic_significance", ""),
                      _km(r, "length_retained_km")) * enc
    for r in f2:
        base = _rate(r.get("sub_habitat_type", ""), r.get("condition", ""),
                     r.get("strategic_significance", ""), _km(r, "length_km"))
        mult, _n = M.proposed_multiplier("created", r.get("sub_habitat_type", ""),
                                         r.get("condition", ""))
        pr_u += (base * mult * M.water_row_encroachment_mult(r, proposed=True)
                 * spatial_multiplier(r.get("spatial_category", "")))
    return bl_u, pr_u


def offsite_plan_from_map(iface=None):
    """patch_metric plan tuples for the mapped off-site habitat, or []."""
    from .xlsm_exporter import (_D1_WRITE_COLS, _D1_DATA_START_ROW,
                                _D2_WRITE_COLS, _D2_DATA_START_ROW,
                                _D3_WRITE_COLS, _D3_DATA_START_ROW,
                                _E1_WRITE_COLS, _E1_DATA_START_ROW,
                                _E2_WRITE_COLS, _E2_DATA_START_ROW,
                                _F1_WRITE_COLS, _F1_DATA_START_ROW,
                                _F2_WRITE_COLS, _F2_DATA_START_ROW)
    d1, d2, d3, e1, e2 = offsite_rows_from_map()
    f1, f2 = offsite_water_rows(iface)
    plan = []
    if d1:
        plan.append(("D-1 Off-Site Habitat Baseline", d1, _D1_WRITE_COLS, _D1_DATA_START_ROW))
    if d2:
        plan.append(("D-2 Off-Site Habitat Creation", d2, _D2_WRITE_COLS, _D2_DATA_START_ROW))
    if d3:
        plan.append(("D-3 Off-Site Habitat Enhancment", d3, _D3_WRITE_COLS, _D3_DATA_START_ROW))
    if e1:
        plan.append(("E-1 Off-Site Hedge Baseline", e1, _E1_WRITE_COLS, _E1_DATA_START_ROW))
    if e2:
        plan.append(("E-2 Off-Site Hedge Creation", e2, _E2_WRITE_COLS, _E2_DATA_START_ROW))
    if f1:
        plan.append(("F-1 Off-Site WaterC' Baseline", f1, _F1_WRITE_COLS, _F1_DATA_START_ROW))
    if f2:
        plan.append(("F-2 Off-Site WaterC' Creation", f2, _F2_WRITE_COLS, _F2_DATA_START_ROW))
    return plan
