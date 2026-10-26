# -*- coding: utf-8 -*-
"""Clipboard helpers for the desk-study report tables.

Goal: copy a report table so that pasting it into the Google Docs report
template *distributes one value per cell* (instead of dumping the whole
table into a single cell) AND matches the template's house style.

Worked out by inspecting what Google Sheets / Docs put on the clipboard:

  1. Distribute, don't nest. Docs only fills existing cells when the HTML
     carries Google's own markers -- a ``<google-sheets-html-origin>``
     element and ``data-sheets-root="1"`` on the table.
  2. Valid CF_HTML with context. On Windows the clipboard HTML must be a
     proper ``CF_HTML`` payload *with* an ``<html><body>`` wrapper and
     byte-accurate offsets -- and Qt can't be trusted to write it (its
     ``setHtml`` emits a mis-sized, context-less payload and the
     ``x-qt-windows-mime`` MIME just registers a literally-named format),
     so we hand our own CF_HTML bytes straight to Win32.
  3. Match the template. A distribute-paste OVERWRITES each destination
     cell's fill and border, so we bake the report template's style into
     every copied cell. That style is user-editable (the settings cog on
     the report dialogs) and persisted via QgsSettings; the defaults below
     are the ROAVR template.

A tab-separated plain-text copy is always set too as a fallback.
"""

import html as _html
import re as _re
import sys as _sys

# --- Default house style (the ROAVR Google Docs report template) -----------
DEFAULT_STYLE = {
    "border_on": True,
    "border_colour": "#d9d9d9",   # light grey
    "border_width_pt": 0.5,
    "fill_mode": "none",          # "alternating" | "single" | "none"
    "bg_1": "#ffffff",            # 1st / odd body rows
    "bg_2": "#f5f5f5",            # 2nd / even body rows
    "pad_v_pt": 4,                # cell padding, vertical (top/bottom)
    "pad_h_pt": 6,                # cell padding, horizontal (left/right)
    "valign": "middle",          # top | middle | bottom
    # Header styling -- applied only to the leading `header_rows` rows, and
    # only when those rows are actually part of the copy.
    "header_on": True,
    "header_bg": "#333333",       # dark charcoal
    "header_fg": "#ffffff",       # white text
    "header_bold": False,
    # Fonts -- empty family / 0 size means "inherit the destination doc's font"
    # (the default; keeps the ROAVR template's own font). Populated when the
    # user imports a style from a pasted table, or sets one by hand.
    "header_font": "Barlow",      # report-template header family
    "header_font_pt": 10,         # report-template header size
    "body_font": "",              # body cell font family ("" = inherit)
    "body_font_pt": 0,            # body cell font size, pt (0 = inherit)
    "body_fg": "",                # body text colour ("" = inherit)
}

# The style lives in the QGIS *profile* folder (not the plugin folder), so a
# user's preference survives a plugin update or uninstall -- the plugin lives
# at <profile>/python/plugins/biome, the store at
# <profile>/biome_clipboard_style.json. It's a plain JSON file you can back up.
_STYLE_FILENAME = "biome_clipboard_style.json"


def style_path():
    """Absolute path to the style store in the QGIS profile folder."""
    import os
    try:
        from qgis.core import QgsApplication
        base = QgsApplication.qgisSettingsDirPath()
    except Exception:
        base = os.path.expanduser("~")
    return os.path.join(base, _STYLE_FILENAME)


def load_style():
    """The current saved style dict (DEFAULT_STYLE merged with any overrides
    saved to the profile JSON). Always returns a complete dict."""
    style = dict(DEFAULT_STYLE)
    try:
        import os
        import json
        path = style_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict):
                style.update(saved)
    except Exception:
        pass
    return style


def save_style(style):
    """Persist `style` (merged onto the defaults) to the profile JSON."""
    try:
        import json
        merged = dict(DEFAULT_STYLE)
        merged.update(style or {})
        with open(style_path(), "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
    except Exception:
        pass


def _norm_cell(value):
    """Coerce a cell value to a clean string (``""`` for ``None``)."""
    return "" if value is None else str(value)


def rows_to_tsv(rows):
    """Tab-separated text for `rows`; tabs/newlines in a cell flattened to
    spaces so the column grid stays aligned in plain-text targets / Excel."""
    out = []
    for row in rows:
        cells = []
        for c in row:
            c = _norm_cell(c).replace("\t", " ").replace("\r", " ").replace("\n", " ")
            cells.append(c)
        out.append("\t".join(cells))
    return "\n".join(out)


def _fmt_pt(v):
    """'4' for 4.0, '0.5' for 0.5 -- trim trailing zeros for tidy CSS."""
    return ("%f" % float(v)).rstrip("0").rstrip(".")


def _cell_style(style, bg, is_header=False):
    """Inline CSS for one ``<td>`` given the style dict, background colour
    (``""`` for none) and whether it's a header cell."""
    parts = []
    if style.get("border_on", True):
        w = _fmt_pt(style.get("border_width_pt", 0.5))
        col = style.get("border_colour", "#d9d9d9")
        for side in ("left", "right", "bottom", "top"):
            parts.append("border-%s:solid %s %spt" % (side, col, w))
    parts.append("vertical-align:%s" % style.get("valign", "middle"))
    if bg:
        parts.append("background-color:%s" % bg)
    # Fonts (only baked in when set; empty = inherit the destination doc font).
    fam = style.get("header_font") if is_header else style.get("body_font")
    if fam:
        parts.append("font-family:%s" % _css_font_family(fam))
    pt = style.get("header_font_pt") if is_header else style.get("body_font_pt")
    if pt:
        parts.append("font-size:%spt" % _fmt_pt(pt))
    if is_header:
        parts.append("color:%s" % style.get("header_fg", "#ffffff"))
        if style.get("header_bold", True):
            parts.append("font-weight:bold")
    else:
        fg = style.get("body_fg")
        if fg:
            parts.append("color:%s" % fg)
    parts.append("padding:%spt %spt %spt %spt" % (
        _fmt_pt(style.get("pad_v_pt", 4)), _fmt_pt(style.get("pad_h_pt", 6)),
        _fmt_pt(style.get("pad_v_pt", 4)), _fmt_pt(style.get("pad_h_pt", 6))))
    parts.append("overflow:hidden;overflow-wrap:break-word")
    return ";".join(parts) + ";"


def _row_bg(style, body_index):
    mode = style.get("fill_mode", "alternating")
    if mode == "none":
        return ""
    if mode == "single":
        return style.get("bg_1", "#ffffff")
    return style.get("bg_1", "#ffffff") if body_index % 2 == 0 else style.get("bg_2", "#f5f5f5")


def rows_to_html_table(rows, header_rows=0, style=None):
    """Distributing, house-styled HTML ``<table>`` fragment for `rows`.

    The leading `header_rows` rows are styled as headers (when the style's
    ``header_on`` is set); remaining rows take the alternating body fills.
    Carries the ``<google-sheets-html-origin>`` / ``data-sheets-root``
    markers so Google Docs distributes into existing cells.
    """
    if style is None:
        style = load_style()
    header_on = style.get("header_on", True)
    body = []
    body_i = 0
    for i, row in enumerate(rows):
        is_header = header_on and i < header_rows
        bg = style.get("header_bg", "#333333") if is_header else _row_bg(style, body_i)
        if not is_header:
            body_i += 1
        cs = _cell_style(style, bg, is_header)
        tds = "".join(
            '<td style="%s">%s</td>'
            % (cs, _html.escape(_norm_cell(c)).replace("\n", "<br>"))
            for c in row
        )
        body.append("<tr>" + tds + "</tr>")
    return ('<google-sheets-html-origin>'
            '<table data-sheets-root="1" style="border-collapse:collapse;">'
            '<tbody>' + "".join(body) + '</tbody></table>')


def apply_table_style(tbl, style=None):
    """Restyle a QTableWidget so it mirrors the paste output -- gold
    gridlines, alternating row fills, dark column header. A live preview of
    what a copy will look like in the report. Clears any per-item colour
    coding so the house fills show, and uses alternate-row colours so the
    look survives sorting.
    """
    from qgis.PyQt.QtGui import QBrush
    if style is None:
        style = load_style()
    on = bool(style.get("border_on", True))
    border = style.get("border_colour", "#d9d9d9") if on else "#D9DED7"
    mode = style.get("fill_mode", "alternating")
    bg1 = style.get("bg_1", "#ffffff")
    if mode == "none":
        bg1, bg2, alt = "#ffffff", "#ffffff", False
    elif mode == "single":
        bg2, alt = bg1, False
    else:
        bg2, alt = style.get("bg_2", "#f5f5f5"), True

    body_font = ""
    if style.get("body_font"):
        body_font += "font-family:%s;" % _css_font_family(style["body_font"])
    if style.get("body_font_pt"):
        body_font += "font-size:%spt;" % _fmt_pt(style["body_font_pt"])
    if style.get("body_fg"):
        body_font += "color:%s;" % style["body_fg"]
    # Cell padding: style is in points, QSS wants px (96dpi -> 1pt = 4/3 px).
    pad_v = int(round(float(style.get("pad_v_pt", 4)) * 4.0 / 3.0))
    pad_h = int(round(float(style.get("pad_h_pt", 6)) * 4.0 / 3.0))
    css = ("QTableWidget{background:%s;alternate-background-color:%s;"
           "gridline-color:%s;%s}"
           "QTableWidget::item{padding:%dpx %dpx;}"
           "QTableWidget::item:selected{background:#2F5D8A;color:white;}"
           % (bg1, bg2, border, body_font, pad_v, pad_h))
    if style.get("header_on", True):
        header_font = ""
        if style.get("header_font"):
            header_font += "font-family:%s;" % _css_font_family(style["header_font"])
        if style.get("header_font_pt"):
            header_font += "font-size:%spt;" % _fmt_pt(style["header_font_pt"])
        css += ("QHeaderView::section{background:%s;color:%s;%s%sborder:0px;"
                "border-right:1px solid %s;border-bottom:1px solid %s;"
                "padding:%dpx %dpx;}"
                % (style.get("header_bg", "#333333"),
                   style.get("header_fg", "#ffffff"),
                   "font-weight:bold;" if style.get("header_bold", True) else "",
                   header_font, border, border, pad_v, pad_h))

    tbl.setShowGrid(on)
    tbl.setAlternatingRowColors(alt)
    blank = QBrush()
    for r in range(tbl.rowCount()):
        for c in range(tbl.columnCount()):
            it = tbl.item(r, c)
            if it is not None:
                it.setBackground(blank)
                it.setForeground(blank)
    tbl.setStyleSheet(css)


def _css_font_family(fam):
    """A CSS-safe font-family value (quote multi-word families)."""
    fam = str(fam or "").strip()
    if not fam:
        return ""
    if " " in fam and not (fam.startswith('"') or fam.startswith("'")):
        return '"%s"' % fam
    return fam


# ===========================================================================
#  Reading a table's style back OFF the clipboard
#  ---------------------------------------------------------------------------
#  So the user can copy a table from anywhere (Google Docs, Word, Excel, a web
#  page), click "Paste table style", and have the plugin reverse-engineer its
#  look -- header fill / text colour / bold / font, body fills (incl.
#  alternating), cell borders, padding and cell font -- into the style dict.
# ===========================================================================

_NAMED_COLOURS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000",
    "green": "#008000", "blue": "#0000ff", "gray": "#808080",
    "grey": "#808080", "silver": "#c0c0c0", "yellow": "#ffff00",
    "navy": "#000080", "maroon": "#800000", "olive": "#808000",
    "lime": "#00ff00", "aqua": "#00ffff", "cyan": "#00ffff",
    "teal": "#008080", "fuchsia": "#ff00ff", "magenta": "#ff00ff",
    "purple": "#800080", "orange": "#ffa500", "gold": "#ffd700",
    "beige": "#f5f5dc", "ivory": "#fffff0", "whitesmoke": "#f5f5f5",
    "gainsboro": "#dcdcdc", "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3",
    "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "dimgray": "#696969",
}


def _to_hex(token):
    """Normalise a single colour token (#rgb, #rrggbb, rgb()/rgba(), name) to
    ``#rrggbb`` lowercase, or None for transparent / unknown."""
    if not token:
        return None
    c = str(token).strip().lower()
    if c in ("", "transparent", "none", "inherit", "initial", "auto", "currentcolor"):
        return None
    if c in _NAMED_COLOURS:
        return _NAMED_COLOURS[c]
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) == 6 and all(x in "0123456789abcdef" for x in h):
            return "#" + h
        return None
    m = _re.match(r"rgba?\(([^)]+)\)", c)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) >= 4:
            try:
                if float(parts[3]) == 0:
                    return None      # fully transparent
            except Exception:
                pass
        try:
            rgb = []
            for p in parts[:3]:
                if p.endswith("%"):
                    rgb.append(int(round(float(p[:-1]) * 255 / 100)))
                else:
                    rgb.append(int(round(float(p))))
            return "#%02x%02x%02x" % tuple(max(0, min(255, v)) for v in rgb)
        except Exception:
            return None
    return None


def _colour(value):
    """Pull a colour out of a CSS value that may be a shorthand
    (``#fff none repeat`` / ``1px solid black``)."""
    if not value:
        return None
    direct = _to_hex(value)
    if direct:
        return direct
    m = _re.search(r"#[0-9a-fA-F]{3,6}|rgba?\([^)]*\)", value)
    if m:
        return _to_hex(m.group(0))
    for tok in _re.split(r"[\s,]+", value.strip().lower()):
        if tok in _NAMED_COLOURS:
            return _NAMED_COLOURS[tok]
    return None


def _len_to_pt(token):
    """A CSS length token -> points (px assumed at 96dpi -> 0.75pt)."""
    if token is None:
        return None
    m = _re.match(r"^\s*(-?[0-9]*\.?[0-9]+)\s*(pt|px|em|rem|%)?\s*$", str(token))
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2) or "px"
    if unit == "pt":
        return val
    if unit == "px":
        return val * 0.75
    if unit in ("em", "rem"):
        return val * 12.0        # rough: assume a 12pt base
    return None                  # ignore %


def _parse_decls(style_str):
    """A ``prop:value;prop:value`` string -> {prop(lower): value}."""
    out = {}
    for chunk in (style_str or "").split(";"):
        if ":" in chunk:
            k, v = chunk.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _fontweight_num(v):
    try:
        return int(str(v).strip())
    except Exception:
        return 0


def _is_bold(decls):
    fw = str(decls.get("font-weight", "")).lower()
    return "bold" in fw or _fontweight_num(fw) >= 600


def _clean_font(value):
    """First family name from a font-family list, unquoted."""
    if not value:
        return None
    first = str(value).split(",")[0].strip().strip('"').strip("'").strip()
    return first or None


_BORDER_STYLES = {"solid", "dashed", "dotted", "double",
                  "groove", "ridge", "inset", "outset"}


def _border_from(value):
    """Parse a border shorthand -> (style, width_pt, colour_hex).
    Returns (None, None, None) when it's ``none``/``hidden``/empty."""
    v = str(value or "").strip().lower()
    if not v or v in ("none", "hidden", "0", "0px", "0pt"):
        return (None, None, None)
    colour = None
    m = _re.search(r"#[0-9a-f]{3,6}|rgba?\([^)]*\)", v)
    if m:
        colour = _to_hex(m.group(0))
        v = v[:m.start()] + " " + v[m.end():]
    style = None
    width = None
    for tok in v.split():
        if tok in _BORDER_STYLES:
            style = tok
        elif width is None and _len_to_pt(tok) is not None:
            width = _len_to_pt(tok)
        elif colour is None and tok.isalpha() and tok in _NAMED_COLOURS:
            colour = _NAMED_COLOURS[tok]
    return (style, width, colour)


def _cell_border(decls):
    """(colour_hex, width_pt) for a cell's border, or None if it has none."""
    candidates = []
    if decls.get("border"):
        candidates.append(decls["border"])
    for side in ("border-bottom", "border-top", "border-left", "border-right"):
        if decls.get(side):
            candidates.append(decls[side])
    for val in candidates:
        style, width, colour = _border_from(val)
        if style is None and (width is None or width == 0):
            continue
        if width == 0:
            continue
        w = width if (width and width > 0) else 0.5
        return (colour or "#000000", round(w, 3))
    # longhand border-style / -width / -color
    bs = str(decls.get("border-style", "")).lower()
    if bs and bs not in ("none", "hidden"):
        w = _len_to_pt(decls.get("border-width", "1px")) or 0.5
        return (_colour(decls.get("border-color")) or "#000000", round(w, 3))
    return None


def _cell_padding(decls):
    """(vertical_pt, horizontal_pt) for a cell, each None if unspecified."""
    if decls.get("padding"):
        toks = [_len_to_pt(t) for t in decls["padding"].split()]
        toks = [t for t in toks if t is not None]
        if len(toks) == 1:
            return toks[0], toks[0]
        if len(toks) >= 2:
            return toks[0], toks[1]          # top/bottom, left/right
    pv = _len_to_pt(decls["padding-top"]) if "padding-top" in decls else None
    ph = _len_to_pt(decls["padding-left"]) if "padding-left" in decls else None
    return pv, ph


def _css_class_map(html_text):
    """{class-name(lower): {prop: value}} from any ``<style>`` blocks -- so
    class-driven styling (Excel/Word exports) resolves too."""
    out = {}
    for block in _re.findall(r"<style[^>]*>(.*?)</style>", html_text, _re.I | _re.S):
        block = _re.sub(r"/\*.*?\*/", "", block, flags=_re.S)
        for rule in block.split("}"):
            if "{" not in rule:
                continue
            sel, decls = rule.split("{", 1)
            dd = _parse_decls(decls)
            if not dd:
                continue
            for cls in _re.findall(r"\.([A-Za-z0-9_\-]+)", sel):
                out.setdefault(cls.lower(), {}).update(dd)
    return out


try:
    from html.parser import HTMLParser as _HTMLParser
except Exception:                                    # pragma: no cover
    _HTMLParser = object


class _TableStyleParser(_HTMLParser):
    """Collect the FIRST HTML table's cells with their resolved styling.

    Each cell -> {"decls": {...}, "text_decls": {...}, "is_th": bool,
    "bold": bool}. ``text_decls`` gathers colour/font off inner
    ``span``/``p``/``font``/``b`` wrappers (Google Docs et al. put the fill on
    the ``<td>`` but the text colour/font on an inner span)."""

    def __init__(self, css_map):
        super().__init__(convert_charrefs=True)
        self._css = css_map or {}
        self.rows = []
        self._tdepth = 0
        self._first_depth = None
        self._done = False
        self._row = None
        self._cell = None

    def _decls_for(self, attrs):
        cls = ""
        style = ""
        for k, v in attrs:
            lk = (k or "").lower()
            if lk == "class":
                cls = v or ""
            elif lk == "style":
                style = v or ""
        d = {}
        for c in cls.split():
            d.update(self._css.get(c.lower(), {}))
        d.update(_parse_decls(style))
        return d

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        tag = tag.lower()
        if tag == "table":
            self._tdepth += 1
            if self._first_depth is None:
                self._first_depth = self._tdepth
            return
        if self._first_depth is None:
            return
        inside = self._tdepth == self._first_depth
        if tag == "tr" and inside:
            self._row = []
            return
        if tag in ("td", "th") and inside and self._row is not None:
            d = self._decls_for(attrs)
            self._cell = {"decls": d, "text_decls": {}, "is_th": tag == "th",
                          "bold": _is_bold(d)}
            return
        if self._cell is not None:                # inner formatting wrapper
            d = self._decls_for(attrs)
            for key in ("color", "font-family", "font-size", "font-weight"):
                if key in d and key not in self._cell["text_decls"]:
                    self._cell["text_decls"][key] = d[key]
            if tag in ("b", "strong") or _is_bold(d):
                self._cell["bold"] = True
            if tag == "font":
                for k, v in attrs:
                    if (k or "").lower() == "color" and "color" not in self._cell["text_decls"]:
                        self._cell["text_decls"]["color"] = v

    def handle_endtag(self, tag):
        if self._done:
            return
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            if self._first_depth is not None and self._tdepth == self._first_depth:
                self._done = True
            self._tdepth -= 1


def _row_fill_colour(row):
    """The most common explicit background among a row's cells (or None)."""
    from collections import Counter
    bgs = []
    for c in row:
        bg = _colour(c["decls"].get("background-color") or c["decls"].get("background"))
        if bg:
            bgs.append(bg)
    if not bgs:
        return None
    return Counter(bgs).most_common(1)[0][0]


def _looks_alternating(bgs):
    """True if `bgs` is a strict A,B,A,B... banding (None treated as white)."""
    norm = [b or "#ffffff" for b in bgs]
    if len(norm) < 2:
        return False
    a = norm[0]
    b = next((x for x in norm if x != a), None)
    if b is None:
        return False
    return all(x == (a if i % 2 == 0 else b) for i, x in enumerate(norm))


def parse_table_style(html_text):
    """Reverse-engineer a copied table's look into a (partial) style dict.

    Returns keys that were confidently detected (borders, row fills incl.
    alternating, padding, header fill/text/bold/font, body font/colour), ready
    to ``dict.update`` onto the current style. Returns None when no table is
    found. Header keys are only included when a header row is detected, so
    importing a header-less table leaves the header style untouched.
    """
    if not html_text:
        return None
    css = _css_class_map(html_text)
    parser = _TableStyleParser(css)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        pass
    rows = [r for r in parser.rows if r]
    if not rows:
        return None

    def fg(cell):
        return _colour(cell["text_decls"].get("color") or cell["decls"].get("color"))

    def fam(cell):
        return _clean_font(cell["text_decls"].get("font-family")
                           or cell["decls"].get("font-family"))

    def fpt(cell):
        v = cell["text_decls"].get("font-size") or cell["decls"].get("font-size")
        return _len_to_pt(v) if v else None

    result = {}
    first = rows[0]

    # ---- header row? ------------------------------------------------------
    header = False
    if len(rows) >= 2:
        second = rows[1]
        any_th = any(c["is_th"] for c in first)
        fbg, sbg = _row_fill_colour(first), _row_fill_colour(second)
        fbold = any(c["bold"] for c in first)
        sbold = any(c["bold"] for c in second)
        if any_th or (fbg and fbg != sbg) or (fbold and not sbold):
            header = True

    body_rows = rows[1:] if header else rows
    if not body_rows:                # header-only table -> treat all as body
        body_rows = rows
        header = False

    if header:
        result["header_on"] = True
        hb = _row_fill_colour(first)
        if hb:
            result["header_bg"] = hb
        for c in first:
            hf = fg(c)
            if hf:
                result["header_fg"] = hf
                break
        result["header_bold"] = any(c["bold"] for c in first)
        for c in first:
            f = fam(c)
            if f:
                result["header_font"] = f
                break
        for c in first:
            p = fpt(c)
            if p:
                result["header_font_pt"] = round(p, 1)
                break

    # ---- body fills -------------------------------------------------------
    body_bgs = [_row_fill_colour(r) for r in body_rows]
    if not any(body_bgs):
        result["fill_mode"] = "none"
    elif _looks_alternating(body_bgs):
        result["fill_mode"] = "alternating"
        result["bg_1"] = body_bgs[0] or "#ffffff"
        result["bg_2"] = next((b or "#ffffff" for b in body_bgs
                               if (b or "#ffffff") != (body_bgs[0] or "#ffffff")),
                              "#f5f5f5")
    else:
        result["fill_mode"] = "single"
        result["bg_1"] = next((b for b in body_bgs if b), "#ffffff")

    # ---- borders ----------------------------------------------------------
    border = None
    for r in body_rows:
        for c in r:
            border = _cell_border(c["decls"])
            if border:
                break
        if border:
            break
    result["border_on"] = bool(border)
    if border:
        result["border_colour"], result["border_width_pt"] = border

    # ---- padding ----------------------------------------------------------
    for r in body_rows:
        done = False
        for c in r:
            pv, ph = _cell_padding(c["decls"])
            if pv is not None:
                result["pad_v_pt"] = int(round(pv))
                done = True
            if ph is not None:
                result["pad_h_pt"] = int(round(ph))
                done = True
            if done:
                break
        if done:
            break

    # ---- body font + text colour -----------------------------------------
    for r in body_rows:
        for c in r:
            if "body_font" not in result:
                f = fam(c)
                if f:
                    result["body_font"] = f
            if "body_font_pt" not in result:
                p = fpt(c)
                if p:
                    result["body_font_pt"] = round(p, 1)
            if "body_fg" not in result:
                col = fg(c)
                if col:
                    result["body_fg"] = col
        if all(k in result for k in ("body_font", "body_font_pt", "body_fg")):
            break

    return result


def _strip_cf_header(text):
    """Drop the ``CF_HTML`` numeric preamble, keeping the HTML (incl. any
    ``<head><style>`` needed to resolve Excel/Word class styling)."""
    i = text.lower().find("<html")
    if i == -1:
        i = text.find("<")
    return text[i:] if i > 0 else text


def _get_windows_clipboard_html():
    """The current ``CF_HTML`` clipboard payload as text ("" if none)."""
    import ctypes
    from ctypes import c_void_p, c_uint, c_size_t, c_int, c_wchar_p

    u32 = ctypes.WinDLL("user32")
    k32 = ctypes.WinDLL("kernel32")
    u32.RegisterClipboardFormatW.restype = c_uint
    u32.RegisterClipboardFormatW.argtypes = [c_wchar_p]
    u32.OpenClipboard.argtypes = [c_void_p]
    u32.OpenClipboard.restype = c_int
    u32.IsClipboardFormatAvailable.argtypes = [c_uint]
    u32.IsClipboardFormatAvailable.restype = c_int
    u32.GetClipboardData.argtypes = [c_uint]
    u32.GetClipboardData.restype = c_void_p
    u32.CloseClipboard.restype = c_int
    k32.GlobalLock.argtypes = [c_void_p]
    k32.GlobalLock.restype = c_void_p
    k32.GlobalUnlock.argtypes = [c_void_p]
    k32.GlobalUnlock.restype = c_int
    k32.GlobalSize.argtypes = [c_void_p]
    k32.GlobalSize.restype = c_size_t

    cf_html = u32.RegisterClipboardFormatW("HTML Format")
    if not cf_html:
        return ""
    opened = False
    for _ in range(10):
        if u32.OpenClipboard(None):
            opened = True
            break
    if not opened:
        return ""
    try:
        if not u32.IsClipboardFormatAvailable(cf_html):
            return ""
        h = u32.GetClipboardData(cf_html)
        if not h:
            return ""
        ptr = k32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            raw = ctypes.string_at(ptr, k32.GlobalSize(h))
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()
    return _strip_cf_header(raw.decode("utf-8", "replace"))


def get_clipboard_html():
    """The HTML currently on the clipboard, or "" if there's none. Prefers the
    Windows ``CF_HTML`` payload (keeps ``<style>`` blocks); falls back to Qt."""
    if _sys.platform == "win32":
        try:
            h = _get_windows_clipboard_html()
            if h and "<" in h:
                return h
        except Exception:
            pass
    try:
        from qgis.PyQt.QtWidgets import QApplication
        md = QApplication.clipboard().mimeData()
        if md is not None and md.hasHtml():
            return md.html() or ""
    except Exception:
        pass
    return ""


def _build_cf_html(fragment):
    """Wrap an HTML `fragment` as a Windows ``CF_HTML`` payload (bytes) with
    the ``<html><body>`` context and byte-accurate offsets Docs/Office need."""
    pre = "<html><body>\r\n<!--StartFragment-->"
    post = "<!--EndFragment-->\r\n</body></html>"
    tpl = ("Version:0.9\r\n"
           "StartHTML:%010d\r\nEndHTML:%010d\r\n"
           "StartFragment:%010d\r\nEndFragment:%010d\r\n")
    header_len = len(tpl % (0, 0, 0, 0))
    pre_b, frag_b, post_b = (s.encode("utf-8") for s in (pre, fragment, post))
    start_fragment = header_len + len(pre_b)
    end_fragment = start_fragment + len(frag_b)
    end_html = end_fragment + len(post_b)
    header = tpl % (header_len, end_html, start_fragment, end_fragment)
    return (header + pre + fragment + post).encode("utf-8")


def _set_windows_clipboard(cf_bytes, text):
    """Write ``CF_HTML`` ("HTML Format") + ``CF_UNICODETEXT`` to the Windows
    clipboard directly via Win32. Returns True on success."""
    import ctypes
    from ctypes import c_void_p, c_uint, c_size_t, c_int, c_wchar_p

    u32 = ctypes.WinDLL("user32")
    k32 = ctypes.WinDLL("kernel32")
    u32.RegisterClipboardFormatW.restype = c_uint
    u32.RegisterClipboardFormatW.argtypes = [c_wchar_p]
    u32.OpenClipboard.argtypes = [c_void_p]
    u32.OpenClipboard.restype = c_int
    u32.EmptyClipboard.restype = c_int
    u32.SetClipboardData.restype = c_void_p
    u32.SetClipboardData.argtypes = [c_uint, c_void_p]
    u32.CloseClipboard.restype = c_int
    k32.GlobalAlloc.restype = c_void_p
    k32.GlobalAlloc.argtypes = [c_uint, c_size_t]
    k32.GlobalLock.restype = c_void_p
    k32.GlobalLock.argtypes = [c_void_p]
    k32.GlobalUnlock.argtypes = [c_void_p]
    k32.GlobalUnlock.restype = c_int

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    cf_html = u32.RegisterClipboardFormatW("HTML Format")
    if not cf_html:
        return False

    def _put(fmt, data):
        h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return
        ctypes.memmove(k32.GlobalLock(h), data, len(data))
        k32.GlobalUnlock(h)
        u32.SetClipboardData(fmt, h)  # system takes ownership of h

    opened = False
    for _ in range(10):
        if u32.OpenClipboard(None):
            opened = True
            break
    if not opened:
        return False
    try:
        u32.EmptyClipboard()
        _put(cf_html, cf_bytes + b"\x00")
        _put(CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")
    finally:
        u32.CloseClipboard()
    return True


def set_table_clipboard(rows, header_rows=0):
    """Place `rows` (iterable of row-iterables) on the clipboard.

    Sets a distributing, house-styled HTML table (so pasting into the Google
    Docs report template fills one value per cell and matches the template)
    plus a tab-separated plain-text fallback. `header_rows` leading rows are
    styled as headers -- pass 1 when the copy includes the report header.
    """
    grid = [list(row) for row in rows]
    style = load_style()
    tsv = rows_to_tsv(grid)
    fragment = rows_to_html_table(grid, header_rows=header_rows, style=style)

    # TEMP debug: log the style actually used, to diagnose missing
    # alternating/header on copy. Remove once confirmed.
    try:
        import os as _os, json as _json
        with open(_os.path.join(_os.path.dirname(__file__), "biome_debug.log"),
                  "a", encoding="utf-8") as f:
            f.write("[clipstyle] header_rows=%d fill=%s header_on=%s style=%s\n"
                    % (header_rows, style.get("fill_mode"),
                       style.get("header_on"), _json.dumps(style)))
    except Exception:
        pass

    if _sys.platform == "win32":
        try:
            if _set_windows_clipboard(_build_cf_html(fragment), tsv):
                return
        except Exception:
            pass  # fall through to the Qt path below

    # Non-Windows, or the Win32 write failed: use Qt (HTML may not survive
    # into Google Docs, but the plain text always will).
    from qgis.PyQt.QtWidgets import QApplication
    from qgis.PyQt.QtCore import QMimeData
    mime = QMimeData()
    mime.setText(tsv)
    mime.setHtml(fragment)
    QApplication.clipboard().setMimeData(mime)
