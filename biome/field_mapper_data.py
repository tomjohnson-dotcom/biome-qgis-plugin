"""
field_mapper_data.py
Builds the JSON payload the browser Field Mapper runs on.

Everything here is DERIVED from the plugin's live Python data — the feature
catalogue in gridref_dialog, the habitat taxonomy in habitat_data, the
condition sheets in condition_data and the GCN indices in gcn_hsi_data. The
field app therefore offers exactly what the desktop offers, and a habitat added
to habitat_data appears on the phone with no second edit.

That is the whole point of this module. The previous field mapper kept its own
hand-copied catalogue, which had drifted to 48 of 167 point types, 14 of 20
lines and 25 of 41 areas, and silently omitted every PRA/PRF, invasive species
and survey-method feature added since it was written.

Import cost note: gridref_dialog._register_builtin_additions() mutates
FEATURE_EMOJI / FEATURE_SVG / LINE_FEATURES / AREA_FEATURES in place at import
time, so these dicts must be READ FROM THE IMPORTED MODULE. Re-parsing the
literals loses 112 point types.
"""

import json
import os
import re

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_SYMBOLS_DIR = os.path.join(_PLUGIN_DIR, "Symbols")

PAYLOAD_VERSION = 3

# Tabs whose leading emoji is stripped for display — the app draws its own
# icons, and the plugin's emoji policy is opt-in only.
_TAB_EMOJI_RE = re.compile(r"^[^\w(]+\s*")

# Comment header on the tabler icon files.
_SVG_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SVG_WS_RE = re.compile(r">\s+<")


def _clean_svg(text):
    """Strip the licence comment and collapse inter-tag whitespace."""
    text = _SVG_COMMENT_RE.sub("", text or "")
    text = _SVG_WS_RE.sub("><", text)
    return text.strip()


def _tab_label(raw):
    """'🐾  Species Records' -> 'Species Records'."""
    return _TAB_EMOJI_RE.sub("", str(raw)).strip() or str(raw).strip()


def _read_symbol(filename):
    """Inline a Symbols/*.svg. Returns '' when missing rather than raising —
    a field app that fails to build because one icon moved is worse than a
    field app with one blank icon."""
    if not filename:
        return ""
    path = filename
    if not os.path.isabs(path):
        path = os.path.join(_SYMBOLS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _clean_svg(fh.read())
    except Exception:
        return ""


# ── Feature catalogue ────────────────────────────────────────────────────────

def _feature_payload():
    from . import gridref_dialog as gd

    def groups(nested):
        out = []
        for tab, subgroups in nested:
            out.append([_tab_label(tab),
                        [[str(sub), list(names)] for sub, names in subgroups]])
        return out

    def flat_groups(flat):
        return [[_tab_label(tab), list(names)] for tab, names in flat]

    # Points — nested tabs, plus inlined icons for every pickable name.
    point_groups = groups(gd.FEATURE_GROUPS)
    pickable = []
    for _tab, subs in gd.FEATURE_GROUPS:
        for _sub, names in subs:
            pickable.extend(names)
    pickable = sorted(set(pickable))

    # Icons are the plugin's own Symbols/*.svg, inlined. The FEATURE_EMOJI
    # fallback is deliberately NOT carried: every pickable type has an SVG, and
    # the plugin's emoji policy is opt-in only.
    point_meta = {}
    svg_cache = {}
    for name in pickable:
        svg_file = gd.FEATURE_SVG.get(name, "")
        if svg_file not in svg_cache:
            svg_cache[svg_file] = _read_symbol(svg_file)
        point_meta[name] = {"svg": svg_cache[svg_file]}

    # Lines and areas — flat groups, style dicts carried verbatim so the phone
    # draws the same colour the canvas does.
    line_groups = flat_groups(gd.LINE_FEATURE_GROUPS_FLAT)
    line_names = {n for _t, names in gd.LINE_FEATURE_GROUPS_FLAT for n in names}
    line_meta = {n: dict(gd.LINE_FEATURES.get(n, {})) for n in line_names}

    area_groups = flat_groups(gd.AREA_FEATURE_GROUPS_FLAT)
    area_names = {n for _t, names in gd.AREA_FEATURE_GROUPS_FLAT for n in names}
    area_meta = {n: dict(gd.AREA_FEATURES.get(n, {})) for n in area_names}

    directional = sorted(getattr(gd, "DIRECTIONAL_TYPES", set()))
    arrow_types = sorted(getattr(gd, "ARROW_DIRECTIONAL_TYPES", set()))
    single_obj = sorted(getattr(gd, "SINGLE_OBJECT_DIRECTIONAL_TYPES", set()))

    return {
        "point_groups": point_groups,
        "point_meta": point_meta,
        "line_groups": line_groups,
        "line_meta": line_meta,
        "area_groups": area_groups,
        "area_meta": area_meta,
        "directional": directional,
        "arrow_types": arrow_types,
        "single_object": single_obj,
        "fov_wedge": dict(getattr(gd, "FOV_WEDGE_FTYPE", {})),
        "fov_default_angle": 60.0,
        "fov_default_range": 25.0,
    }


# ── Habitat taxonomy ─────────────────────────────────────────────────────────

def _habitat_payload():
    from . import habitat_data as hd
    from . import layer_manager as lm

    module_of_group = {}
    for hab_key, group_names in hd.HABITAT_SCREEN_TYPES.items():
        for g in group_names:
            module_of_group[g] = hab_key

    modules = {}          # habitat_key -> [[broad_group, [sub, ...]], ...]
    meta = {}             # sub -> {...}
    for broad, subs in hd.HABITAT_TYPES.items():
        hab_key = module_of_group.get(broad)
        if hab_key is None:
            continue
        names = sorted(subs.keys())
        modules.setdefault(hab_key, []).append([broad, names])
        for sub, rec in subs.items():
            items, target, locked = hd.condition_items_for(sub)
            meta[sub] = {
                "key": rec.get("key", ""),
                "color": rec.get("color", "#7a7a7a"),
                "broad": broad,
                "module": hab_key,
                "dist": hd.DISTINCTIVENESS_SCORES.get(sub, 0),
                "cond_items": list(items),
                "cond_target": target,
                "cond_locked": bool(locked),
                "irreplaceable": sub in hd.IRREPLACEABLE_SUBTYPES,
            }
    for hab_key in modules:
        modules[hab_key].sort(key=lambda pair: pair[0])

    return {
        "modules": modules,
        "meta": meta,
        "condition_options": list(hd.CONDITION_OPTIONS),
        "strategic": list(lm.STRATEGIC_SIGNIFICANCE_OPTIONS),
        "strategic_default": lm.DEFAULT_STRATEGIC_SIGNIFICANCE,
        "water_enc": list(lm.WATERCOURSE_ENCROACHMENT_OPTIONS),
        "rip_enc": list(lm.RIPARIAN_ENCROACHMENT_OPTIONS),
        "tree_sizes": ["Small", "Medium", "Large", "Very Large"],
        "tree_locations": ["Urban", "Rural"],
        "tree_area_ha": dict(hd.TREE_AREA_HA),
        # Documented DBH bands (habitat_data.py) — shown as a picker hint so the
        # surveyor classifies by girth rather than by eye.
        "tree_dbh": {
            "Small": "8–29 cm DBH",
            "Medium": "30–74 cm DBH",
            "Large": "75–119 cm DBH",
            "Very Large": "120 cm+ DBH",
        },
        "module_labels": {
            "area": "Area habitat",
            "hedgerow": "Hedgerow",
            "watercourse": "Watercourse",
            "trees": "Individual tree",
        },
        "module_geom": {
            "area": "area", "hedgerow": "line",
            "watercourse": "line", "trees": "point",
        },
    }


# ── Condition sheets ─────────────────────────────────────────────────────────

def _effective_count_rules(sheet, key, params):
    """Replicate condition_data.evaluate's count-model letter/band selection so
    the browser evaluator is a plain table walk.

    grass_medhigh is the reason this exists: the sheet deliberately carries no
    'bands' key and both panel_for and evaluate synthesise them per acid /
    non-acid variant.
    """
    if key == "grass_medhigh" and params.get("acid"):
        letters = [l for (l, _t) in sheet["criteria"] if l != "F"]
        bands = [["Good", 5, []], ["Moderate", 3, []], ["Poor", 0, []]]
    else:
        letters = [l for (l, _t) in sheet["criteria"]]
        if key == "grass_medhigh":
            bands = [["Good", 5, ["A", "F"]], ["Moderate", 3, ["A"]], ["Poor", 0, []]]
        else:
            bands = [[g, int(m), list(r)] for (g, m, r) in sheet.get("bands", [])]
    return letters, bands


def _condition_payload():
    from . import condition_data as cd
    from . import habitat_data as hd

    sheets = {}
    habitats = {}

    # Every sub-habitat the placer offers, resolved through condition_data's own
    # matcher (which falls back to substring rules for hedgerow / line-of-trees).
    all_subs = set()
    for _broad, subs in hd.HABITAT_TYPES.items():
        all_subs.update(subs.keys())

    for sub in sorted(all_subs):
        if not cd.has_panel(sub):
            continue
        try:
            panel = cd.panel_for(sub)
        except Exception:
            continue
        key = panel["key"]
        params = panel.get("params") or {}
        sheet = cd.SHEETS[key]
        model = panel["model"]

        if key not in sheets:
            entry = {
                "model": model,
                "labels": [[str(a), str(b)] for a, b in (panel.get("labels") or [])],
                "footnotes": list(panel.get("footnotes") or []),
            }
            if model == "score":
                entry["indicators"] = [
                    [l, n, list(d)] for (l, n, d) in (panel.get("indicators") or [])
                ]
                entry["bands"] = [[int(m), g] for (m, g) in sheet.get("bands", [])]
            else:
                entry["criteria"] = [
                    [l, t, bool(e)] for (l, t, e) in (panel.get("criteria") or [])
                ]
            if panel.get("note"):
                entry["note"] = panel["note"]
            if panel.get("groups"):
                entry["groups"] = {str(k): str(v) for k, v in panel["groups"].items()}
            if panel.get("core"):
                entry["core"] = list(panel["core"])
            if panel.get("extra"):
                entry["extra"] = list(panel["extra"])
            if panel.get("needs_c_absent"):
                entry["needs_c_absent"] = True
            sheets[key] = entry

        rec = {"sheet": key, "model": model, "params": dict(params)}
        if model == "count":
            letters, bands = _effective_count_rules(sheet, key, params)
            rec["letters"] = letters
            rec["bands"] = bands
        elif model == "hedgerow":
            with_trees = bool(params.get("with_trees"))
            rec["letters"] = [l for (l, _t) in sheet["criteria"]
                              if with_trees or not l.startswith("E")]
            rec["with_trees"] = with_trees
        elif model == "urban":
            rec["letters"] = ["A", "B", "C"] + list(params.get("additional", []))
        elif model == "wetland":
            rec["core"] = list(sheet["core"])
            rec["additional"] = params.get("additional") or ""
        elif model == "pond":
            rec["core"] = list(sheet["core"])
            rec["extra"] = list(sheet.get("extra") or [])
        habitats[sub] = rec

    # The tree / line-of-trees sheets carry a repeatable tree no. + DBH list,
    # serialised into condition_detail as trees=T1~32;T2~40. Mirrors
    # condition_dialog._TREE_KEYS, read here rather than duplicated as a literal.
    try:
        from .condition_dialog import _TREE_KEYS
        tree_keys = list(_TREE_KEYS)
    except Exception:
        tree_keys = ["trees", "line_trees"]

    return {"sheets": sheets, "habitats": habitats, "tree_sheets": tree_keys}


# ── GCN HSI ──────────────────────────────────────────────────────────────────

def _hsi_payload():
    from . import gcn_hsi_data as G

    factors = []
    for f in G.FACTORS:
        entry = {
            "key": f["key"],
            "n": f["n"],
            "title": f["title"],
            "type": f["type"],
            "note": f.get("note", ""),
        }
        if f["type"] == "cat":
            entry["options"] = [[lbl, si, help_txt] for (lbl, si, help_txt) in f["options"]]
        else:
            entry["curve"] = [[float(x), float(y)] for (x, y) in f["curve"]]
            entry["unit"] = f.get("unit", "")
            entry["xmin"] = f.get("xmin", 0)
            entry["xmax"] = f.get("xmax", 100)
            entry["xlabel"] = f.get("xlabel", "")
            entry["step"] = f.get("step", 1)
            entry["maxinput"] = f.get("maxinput", 100)
            entry["log"] = bool(f.get("log"))
            entry["count"] = bool(f.get("count"))
            entry["input_label"] = {
                "SI2": "Surface area", "SI5": "Perimeter shaded",
                "SI8": "Ponds within 1 km", "SI10": "Surface covered",
            }.get(f["key"], "Value")
        factors.append(entry)

    return {
        "factors": factors,
        "bands": [[(None if thr is None else float(thr)), label, rng]
                  for (thr, label, rng) in G.BANDS],
        "pond_density_divisor": float(G.PI),
    }


# ── UI icon set ──────────────────────────────────────────────────────────────

# Chrome icons, taken from the plugin's own tabler set so the phone and the
# panel share a visual language. Keyed by the name used in app.js.
_UI_ICONS = {
    "leaf": "leaf.svg",
    "map": "map.svg",
    "map-pin": "map-pin.svg",
    "list": "clipboard-list.svg",
    "camera": "camera.svg",
    "plus": "plus.svg",
    "minus": "minus.svg",
    "check": "check.svg",
    "search": "search.svg",
    "layers": "layers.svg",
    "settings": "settings.svg",
    "trash": "trash.svg",
    "pencil": "pencil.svg",
    "download": "download.svg",
    "sync": "route.svg",
    "target": "circles.svg",
    "line": "line.svg",
    "polygon": "polygon.svg",
    "droplet": "droplet.svg",
    "calculator": "calculator.svg",
    "checklist": "checklist.svg",
    "chevron-right": "chevron-right.svg",
    "arrow-left": "arrow-left.svg",
    "undo": "arrow-back-up.svg",
    "clock": "clock.svg",
    "info": "info-circle.svg",
    "eye": "eye.svg",
    "contrast": "contrast.svg",
    "ruler": "ruler.svg",
    "tree": "tree.svg",
    "plant": "plant-2.svg",
    "click": "click.svg",
    "x": "x.svg",
    "bat": "bat.svg",
    "newt": "newt.svg",
    "shield": "shield.svg",
    "file-text": "file-text.svg",
    "device-mobile": "device-mobile.svg",
    "world": "world.svg",
}


def _ui_icons():
    icons_dir = os.path.join(_PLUGIN_DIR, "icons")
    out = {}
    for name, fname in _UI_ICONS.items():
        path = os.path.join(icons_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                svg = _clean_svg(fh.read())
        except Exception:
            svg = ""
        # Keep only the inner paths — app.js supplies its own <svg> wrapper so
        # sizing and colour stay under CSS control.
        inner = ""
        if svg:
            start = svg.find(">", svg.find("<svg"))
            end = svg.rfind("</svg>")
            if start > 0 and end > start:
                inner = svg[start + 1:end].strip()
        out[name] = inner
    return out


# ── Public entry point ───────────────────────────────────────────────────────

def build_payload(site=None):
    """Return the full payload dict for the browser field mapper.

    ``site`` is an optional dict of project metadata: name, ref, surveyor,
    date, plus the redline GeoJSON and centre. Everything else is derived.
    """
    payload = {
        "version": PAYLOAD_VERSION,
        "site": dict(site or {}),
        "features": _feature_payload(),
        "habitats": _habitat_payload(),
        "condition": _condition_payload(),
        "hsi": _hsi_payload(),
        "icons": _ui_icons(),
    }
    return payload


def build_payload_json(site=None):
    """Payload as a compact JSON string ready to embed in the app HTML."""
    return json.dumps(build_payload(site), separators=(",", ":"), ensure_ascii=False)
