# panel_renderer.py
# Builds the final-map sidebar as a themed SVG that the layout's picture item
# points at. Rendering the panel ourselves (rather than as native QGIS items)
# gives full typographic control and lets a whole theme be re-skinned by
# swapping a few colour tokens. The map, legend and scale bar stay native and
# sit on top of this image.

import base64
import os
import tempfile

# ── Panel geometry, in page millimetres (A4 landscape, 297 x 210) ─────────────
PAGE_W_MM   = 297.0
PAGE_H_MM   = 210.0
MAP_W_MM    = 226.786                 # map occupies the left part of the page
PANEL_X_MM  = MAP_W_MM                 # panel starts here
PANEL_W_MM  = PAGE_W_MM - MAP_W_MM     # ~70.214 mm

# SVG authoring space is 10 units per mm (702 x 2100).
_U_PER_MM = 702.0 / PANEL_W_MM         # ~9.997, effectively 10

# Legend gap inside the SVG (authoring units) -> page mm, for placing the
# native QGIS legend item exactly over the transparent region.
_LEG_X0, _LEG_Y0, _LEG_X1, _LEG_Y1 = 38, 470, 664, 1180

def legend_rect_mm():
    """(x, y, w, h) in PAGE millimetres for the native legend item."""
    x = PANEL_X_MM + _LEG_X0 / _U_PER_MM
    y = _LEG_Y0 / _U_PER_MM
    w = (_LEG_X1 - _LEG_X0) / _U_PER_MM
    h = (_LEG_Y1 - _LEG_Y0) / _U_PER_MM
    return (x, y, w, h)


# ── Logo embedding ────────────────────────────────────────────────────────────

def _content_box(img):
    """(x0, y0, x1, y1) pixel box of the visible artwork in a QImage — the
    non-transparent pixels, or the non-near-white ones for an opaque image —
    or None if nothing can be measured."""
    try:
        import numpy as np
        from qgis.PyQt.QtGui import QImage
        im = img.convertToFormat(QImage.Format.Format_ARGB32)
        w, h = im.width(), im.height()
        ptr = im.constBits()
        try:
            ptr.setsize(im.sizeInBytes())
        except Exception:
            ptr.setsize(im.byteCount())
        arr = np.frombuffer(ptr, np.uint8).reshape(h, w, 4)  # B, G, R, A
        b, g, r, al = (arr[:, :, 0].astype(int), arr[:, :, 1].astype(int),
                       arr[:, :, 2].astype(int), arr[:, :, 3])
        if al.max() > al.min():
            mask = al > 16                       # trim transparency
        else:
            mask = (r + g + b) < 740             # trim near-white border
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return None
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    except Exception:
        return None


def _trim_to_content(img):
    """Crop transparent (or near-white) margins off a QImage so the actual
    artwork fills the frame edge to edge. Returns the original if anything
    goes wrong or there's nothing to trim."""
    box = _content_box(img)
    if box is None:
        return img
    x0, y0, x1, y1 = box
    if x0 == 0 and y0 == 0 and x1 == img.width() and y1 == img.height():
        return img
    try:
        from qgis.PyQt.QtGui import QImage
        return img.convertToFormat(QImage.Format.Format_ARGB32).copy(
            x0, y0, x1 - x0, y1 - y0)
    except Exception:
        return img


def _key_out_white_bg(img):
    """Make a fully opaque near-white logo background transparent so the logo
    blends into the sidebar instead of sitting in a visible white box. Logos
    that already carry transparency are returned untouched, as is anything
    that can't be processed."""
    try:
        import numpy as np
        from qgis.PyQt.QtGui import QImage
        im = img.convertToFormat(QImage.Format.Format_ARGB32)
        w, h = im.width(), im.height()
        ptr = im.bits()
        try:
            ptr.setsize(im.sizeInBytes())
        except Exception:
            ptr.setsize(im.byteCount())
        arr = np.frombuffer(ptr, np.uint8).reshape(h, w, 4)  # B, G, R, A
        if arr[:, :, 3].min() < 255:
            return img                       # already has transparency
        near_white = arr[:, :, :3].min(axis=2) >= 242
        if not near_white.any():
            return img
        arr[near_white, 3] = 0
        return im
    except Exception:
        return img


# Logo band size as a fraction of the full sidebar band (width and the height
# cap scale together). 1.0 = flush to the sidebar width, the original look;
# 0.55 (the shipped default, chosen from side-by-side renders 2026-09-03)
# reads as a footer signature and hands the freed height to the legend. Each
# branding theme can override it ("logoScale"); this is the fallback.
LOGO_SCALE = 0.55


def theme_logo_scale(theme):
    """The logo scale a theme asks for, or the module default."""
    try:
        v = float((theme or {}).get("logoScale") or LOGO_SCALE)
    except (TypeError, ValueError):
        v = LOGO_SCALE
    return max(0.25, min(1.0, v))


def _logo_svg_fragment(logo_path, trim=False, scale_frac=None):
    """Return the logo markup fitted within the bottom band (LOGO_SCALE of
    the sidebar width, centred), or a placeholder if none. Returns
    (svg_fragment, top_y) so the caller can position the footnote just above
    the logo. SVG marks are inlined as vector paths; JPG/PNG (and SVGs that
    can't be inlined) are embedded as PNG via Qt."""
    frac = LOGO_SCALE if scale_frac is None else scale_frac
    frac = max(0.25, min(1.0, float(frac)))
    FULL_X, FULL_W = 24, 654           # flush to the sidebar width
    BAND_W = FULL_W * frac
    BAND_X = FULL_X + (FULL_W - BAND_W) / 2.0
    BAND_BOT = 2062                    # bottom edge margin
    BAND_MAX_H = 484 * frac            # tallest the logo may grow (clears footnote)
    cx = BAND_X + BAND_W / 2.0
    placeholder = (f'<text x="{cx:.0f}" y="1958" text-anchor="middle" '
                   'fill="#9aa79c" font-size="24" font-weight="600" '
                   'letter-spacing="2">ADD A LOGO</text>')

    if not logo_path or not os.path.exists(logo_path):
        return placeholder, 1900
    try:
        # Fill the sidebar width; if that would grow taller than the band,
        # cap the height instead (very tall/square logos) and recentre.
        def fit(iw, ih):
            scale = BAND_W / iw
            if ih * scale > BAND_MAX_H:
                scale = BAND_MAX_H / ih
            dw, dh = iw * scale, ih * scale
            return cx - dw / 2.0, BAND_BOT - dh, dw, dh
        frag, _dx, dy, _dw, _dh = _logo_fitted_fragment(logo_path, trim, fit)
        return frag, dy
    except Exception:
        return placeholder, 1900


def _logo_png_b64(logo_path, trim=False, svg_px=1600):
    """Load a logo (SVG rasterised via Qt at *svg_px* wide so vector marks
    print crisp; PNG/JPG as-is), key out an opaque white background, optionally
    trim margins, and return (base64 PNG, width, height). Raises on failure."""
    from qgis.PyQt.QtGui import QImage
    from qgis.PyQt.QtCore import QByteArray, QBuffer, QIODevice

    if logo_path.lower().endswith(".svg"):
        img, _ = _rasterise_svg(logo_path, svg_px)
    else:
        img = QImage(logo_path)
    if img.isNull():
        raise ValueError("logo image failed to load")

    # Key an opaque white logo background out to transparency so the
    # logo blends into the panel background instead of a white box.
    img = _key_out_white_bg(img)
    # Trim transparent/white margins only when the theme asks for it.
    if trim:
        img = _trim_to_content(img)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.toBase64()).decode("ascii"), img.width(), img.height()


def _rasterise_svg(logo_path, svg_px=1600):
    """Rasterise an SVG via Qt at *svg_px* wide → (QImage, viewBox tuple)."""
    from qgis.PyQt.QtGui import QImage, QPainter
    from qgis.PyQt.QtCore import QRectF
    from qgis.PyQt.QtSvg import QSvgRenderer
    rndr = QSvgRenderer(logo_path)
    vb = rndr.viewBoxF()
    sz = rndr.defaultSize()
    w0, h0 = max(1, sz.width()), max(1, sz.height())
    w = int(svg_px)
    h = max(1, int(round(w * h0 / float(w0))))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rndr.render(p, QRectF(0, 0, w, h))
    p.end()
    if vb.isEmpty():
        vb = QRectF(0, 0, w0, h0)
    return img, (vb.x(), vb.y(), vb.width(), vb.height())


def _logo_vector_inline(logo_path, trim=False):
    """For an SVG logo, return (inner_markup, (vx, vy, vw, vh)): the file's own
    drawing elements, verbatim, plus the user-space box they should be fitted
    by (the viewBox, or — when *trim* is set — the artwork's content box in
    viewBox units, measured off a throwaway raster). Keeping the mark as
    vector paths in the panel SVG means QGIS scales it cleanly at any zoom;
    an embedded bitmap is minified without smoothing and looks grainy.

    Only the simple self-authored marks qualify: a single root <svg> with a
    viewBox and no <defs>/<style>/<use>/ids that could clash with the panel.
    Anything else raises so the caller falls back to the raster route."""
    if not logo_path.lower().endswith(".svg"):
        raise ValueError("not an svg")
    import re
    text = open(logo_path, "r", encoding="utf-8").read()
    if re.search(r"<(defs|style|use|symbol|image|clipPath|mask|filter|script)\b", text):
        raise ValueError("svg too complex to inline")
    if re.search(r"\sid=", text):
        raise ValueError("svg carries ids")
    m = re.search(r"<svg\b[^>]*>", text)
    if not m or text.count("<svg") != 1:
        raise ValueError("no single root <svg>")
    root = m.group(0)
    end = text.rfind("</svg>")
    if end < 0:
        raise ValueError("unterminated svg")
    inner = text[m.end():end].strip()
    vbm = re.search(r'viewBox="([^"]+)"', root)
    if vbm:
        vx, vy, vw, vh = [float(v) for v in vbm.group(1).replace(",", " ").split()]
    else:
        wm = re.search(r'\swidth="([\d.]+)', root)
        hm = re.search(r'\sheight="([\d.]+)', root)
        if not (wm and hm):
            raise ValueError("svg has no viewBox or size")
        vx, vy, vw, vh = 0.0, 0.0, float(wm.group(1)), float(hm.group(1))
    if vw <= 0 or vh <= 0:
        raise ValueError("degenerate viewBox")
    if trim:
        img, _ = _rasterise_svg(logo_path)
        box = _content_box(img)
        if box is not None:
            sx, sy = vw / float(img.width()), vh / float(img.height())
            x0, y0, x1, y1 = box
            vx, vy, vw, vh = (vx + x0 * sx, vy + y0 * sy,
                              max(1e-6, (x1 - x0) * sx), max(1e-6, (y1 - y0) * sy))
    return inner, (vx, vy, vw, vh)


def _logo_fitted_fragment(logo_path, trim, fit):
    """Markup that draws *logo_path* fitted into a panel box. *fit* maps the
    logo's natural (w, h) to its placed rect (dx, dy, dw, dh) — the caller's
    own sizing rule. Vector marks are inlined as paths (crisp at any zoom);
    bitmaps (or SVGs that can't be inlined) go in as an embedded PNG.
    Returns (fragment, dx, dy, dw, dh)."""
    try:
        inner, (vx, vy, vw, vh) = _logo_vector_inline(logo_path, trim)
        dx, dy, dw, dh = fit(vw, vh)
        s = dw / vw
        frag = (f'<g transform="translate({dx - vx * s:.3f},{dy - vy * s:.3f}) '
                f'scale({s:.6f})">{inner}</g>')
        return frag, dx, dy, dw, dh
    except Exception:
        pass
    b64, iw, ih = _logo_png_b64(logo_path, trim)
    dx, dy, dw, dh = fit(iw, ih)
    frag = (f'<image x="{dx:.1f}" y="{dy:.1f}" width="{dw:.1f}" '
            f'height="{dh:.1f}" preserveAspectRatio="xMidYMid meet" '
            f'xlink:href="data:image/png;base64,{b64}" '
            f'href="data:image/png;base64,{b64}"/>')
    return frag, dx, dy, dw, dh


# ── Data-source card (desk study maps) ───────────────────────────────────────
# A uniform card at the foot of the sidebar for maps drawn from a third-party
# data source: the source mark on the left at a normalised size, a caption on
# the right, and a colour link to the header (theme["sourceCard"]):
#   fill     card background — the header colour itself (MAGIC) or a tint of it
#   keyline  optional rule along the card top in the header colour (tint mode)
#   ink      caption colour (default: contrast ink for the fill)
#   title    caption first line (bold); lines: further caption lines
# Fixed geometry so every desk map's footer has the same footprint whatever
# shape the mark is. Unlike the free logo band it ignores the theme logoScale.
SOURCE_CARD_H = 250


def tint(hex_colour, amount=0.14, base="#F7F8F6"):
    """Mix *amount* of hex_colour into *base* (the panel ground) — a light
    tint that links a card to the header colour without fighting the mark."""
    try:
        c = hex_colour.lstrip("#")
        b = base.lstrip("#")
        cr, cg, cb = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)

        def mix(x, y):
            return int(round(y + (x - y) * amount))
        return "#%02X%02X%02X" % (mix(cr, br), mix(cg, bg), mix(cb, bb))
    except Exception:
        return base


def _wrap_chars(text, n):
    words, lines, cur = str(text or "").split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and len(cand) > n:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def _source_card_fragment(theme):
    """Return (svg_fragment, card_top_y) for the data-source card."""
    card = theme.get("sourceCard") or {}
    PW, PH = 702, 2100
    y0 = PH - SOURCE_CARD_H
    fill = card.get("fill") or "#F7F8F6"
    keyline = card.get("keyline")
    ink = card.get("ink") or _contrast(fill)
    parts = [f'<rect x="0" y="{y0}" width="{PW}" height="{SOURCE_CARD_H}" '
             f'fill="{fill}"/>']
    if keyline:
        parts.append(f'<rect x="0" y="{y0}" width="{PW}" height="6" '
                     f'fill="{keyline}"/>')
    # Mark: left box, fitted, vertically centred.
    BX0, BX1 = 38, 338
    BY0, BY1 = y0 + 34, PH - 34
    logo_path = theme.get("logo", "")
    if logo_path and os.path.exists(logo_path):
        try:
            def fit(iw, ih):
                scale = min((BX1 - BX0) / float(iw), (BY1 - BY0) / float(ih))
                dw, dh = iw * scale, ih * scale
                return (BX0 + ((BX1 - BX0) - dw) / 2.0,
                        BY0 + ((BY1 - BY0) - dh) / 2.0, dw, dh)
            frag, *_ = _logo_fitted_fragment(
                logo_path, bool(theme.get("trimLogo")), fit)
            parts.append(frag)
        except Exception:
            pass
    # Caption: right column, block vertically centred.
    title_lines = _wrap_chars(card.get("title", ""), 17)
    body_lines = []
    for ln in (card.get("lines") or []):
        body_lines.extend(_wrap_chars(ln, 21))
    T_FS, T_LH, B_FS, B_LH = 30, 36, 25, 32
    block_h = len(title_lines) * T_LH + len(body_lines) * B_LH
    cy = y0 + SOURCE_CARD_H / 2.0
    y = cy - block_h / 2.0
    for ln in title_lines:
        y += T_LH
        parts.append(f'<text x="366" y="{y - 8:.0f}" fill="{ink}" '
                     f'font-size="{T_FS}" font-weight="700">{_esc(ln)}</text>')
    for ln in body_lines:
        y += B_LH
        parts.append(f'<text x="366" y="{y - 8:.0f}" fill="{ink}" opacity="0.8" '
                     f'font-size="{B_FS}" font-weight="500">{_esc(ln)}</text>')
    return "\n".join(parts), y0


# ── Public API ────────────────────────────────────────────────────────────────

def _esc(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _lighten(hex_colour, amount=0.42):
    """Blend a hex colour toward white by *amount* (0..1) for the accent."""
    try:
        h = hex_colour.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = int(r + (255 - r) * amount)
        g = int(g + (255 - g) * amount)
        b = int(b + (255 - b) * amount)
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return "#9CC79B"


def _approx_w(text, fs, ls):
    """Rough advance width for Inter caps in authoring units."""
    return len(str(text)) * (fs * 0.62 + ls)


def _wrap(text, fs, ls, max_w, max_lines=3):
    """Greedy word-wrap to fit max_w; overflow merges into the last line."""
    words = str(text or "").split()
    if not words:
        return []
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if not cur or _approx_w(cand, fs, ls) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return lines


def _title_lines(title, max_w):
    """Pick the largest title size that fits on one line; else wrap (and
    shrink) to keep it readable. Returns (font_size, letter_spacing, lines)."""
    title = title or ""
    for fs in (44, 40, 36, 32):
        if _approx_w(title, fs, 3) <= max_w:
            return fs, 3, [title]
    for fs in (32, 28, 24):
        lines = _wrap(title, fs, 2, max_w, max_lines=4)
        if all(_approx_w(l, fs, 2) <= max_w for l in lines):
            return fs, 2, lines
    return 22, 2, _wrap(title, 22, 2, max_w, max_lines=5)


def _emit(lines, x, y_top, fs, lh, fill, weight, ls, opacity=1.0):
    """Emit a stack of <text> lines; return (svg, block_bottom_y)."""
    parts = []
    for i, ln in enumerate(lines):
        by = y_top + fs + i * lh
        parts.append(
            f'<text x="{x}" y="{by:.0f}" fill="{fill}" font-size="{fs}" '
            f'font-weight="{weight}" letter-spacing="{ls}" '
            f'opacity="{opacity}">{_esc(ln)}</text>')
    bottom = y_top + fs + (len(lines) - 1) * lh + fs * 0.22
    return "\n".join(parts), bottom


def _luminance(hex_colour):
    try:
        h = hex_colour.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    except Exception:
        return 0.5


def _contrast(hex_colour):
    """A high-contrast ink for a stylised mark over the header colour."""
    return "#FFFFFF" if _luminance(hex_colour) < 0.55 else "#15100A"


def _north_arrow(cx, cy, colour):
    """A slim two-tone compass needle with an 'N', for inside the header."""
    return (
        f'<g>'
        f'<text x="{cx:.0f}" y="{cy-50:.0f}" text-anchor="middle" '
        f'font-size="28" font-weight="700" fill="{colour}">N</text>'
        f'<path d="M{cx:.0f},{cy-36:.0f} L{cx+16:.0f},{cy+30:.0f} '
        f'L{cx:.0f},{cy+16:.0f} L{cx-16:.0f},{cy+30:.0f} Z" fill="{colour}"/>'
        f'<path d="M{cx:.0f},{cy-36:.0f} L{cx+16:.0f},{cy+30:.0f} '
        f'L{cx:.0f},{cy+16:.0f} Z" fill="{colour}" opacity="0.5"/>'
        f'</g>')


def _au_to_mm(x0, y0, x1, y1):
    """Authoring-unit rect -> page-millimetre (x, y, w, h)."""
    return (PANEL_X_MM + x0 / _U_PER_MM, y0 / _U_PER_MM,
            (x1 - x0) / _U_PER_MM, (y1 - y0) / _U_PER_MM)


# Upper bound for the legend gap (authoring units) — the metadata block itself
# is footer-anchored (see _meta_top_au), so this only stops a near-empty
# panel's legend rect from running absurdly far down.
_LEG_GAP_MAX_BOT = 1900


def _meta_rows(meta):
    """Resolve the metadata rows for a panel (caller may pass explicit rows).

    An imagery credit is appended to whatever the caller asked for, so every
    map that is drawn over a tile provider's basemap carries its attribution —
    including the callers that build their own explicit row list."""
    rows = meta.get("rows")
    if rows is None:
        rows = [("Date", meta.get("date_text", "")),
                ("Scale @ A4", meta.get("scale_text", "")),
                ("Prepared by", meta.get("created", ""))]
        imagery_date = (meta.get("imagery_date") or "").strip()
        if imagery_date:
            rows.append(("Imagery date", imagery_date))
    credit = (meta.get("attribution") or "").strip()
    if credit and not any(str(lab).lower() == "imagery" for lab, _v in rows):
        rows = list(rows) + [("Imagery", credit)]
    return rows


def _meta_block_h(rows):
    """Height (au) of the metadata block: divider gap + first row + steps."""
    return 60 + max(0, len(rows) - 1) * 64


def _footer_divider_y(theme, meta):
    """Y (au) of the footer divider line, derived from the logo top exactly
    as render_panel draws it."""
    if theme.get("sourceCard"):
        logo_top = 2100 - SOURCE_CARD_H
    else:
        _, logo_top = _logo_svg_fragment(theme.get("logo", ""),
                                         trim=bool(theme.get("trimLogo")),
                                         scale_frac=theme_logo_scale(theme))
    if meta.get("show_footnote", True):
        return (logo_top - 34) - 38
    return logo_top - 30


def _meta_cap_au(dv, rows):
    """Lowest top (au) the metadata block may be pushed to while staying
    clear of the footer divider at *dv*.

    Deliberately NOT floored at _META_TOP: a tall block (an emergence map with
    several survey dates, plus the imagery credit) does not fit between the
    fixed band top and the footer, and flooring it there let the last rows run
    over the footnote and the logo. Returning a value above _META_TOP instead
    starts the block higher, and because `legend_bottom_limit_mm` derives the
    legend's room from this same cap, the key shrinks to make way rather than
    the metadata being pushed off the panel."""
    return int(dv - 40 - _meta_block_h(rows))


def _meta_top_au(dv, rows, meta):
    """Top (au) of the metadata block. THE one place this is decided — the SVG
    block, the native scale-ratio label that overlays its Scale row, and the
    legend's bottom limit all read it, so they can never drift apart.

    The block is anchored to the FOOTER: it sits as low as the footer divider
    (and so the logo) allows, whatever the legend above it measured. A smaller
    logo therefore pulls Scale/Date down with it and every millimetre freed
    goes to the legend, whose bottom limit is derived from this same cap
    (legend_bottom_limit_mm). `legend_bottom_au`, which older callers pass
    after measuring the legend, is accepted but no longer moves the block —
    the legend can never overlap it because its limit is this cap minus the
    gap, not the other way round."""
    return int(_meta_cap_au(dv, rows))


def legend_bottom_limit_mm(theme, meta):
    """The lowest legend bottom (page mm) that still leaves room for the
    metadata block above the footer/logo. Callers can compare the measured
    legend bottom against this and compress the legend if it would overflow."""
    dv = _footer_divider_y(theme, meta)
    cap = _meta_cap_au(dv, _meta_rows(meta))
    return (cap - 46) / _U_PER_MM


def scale_value_box_mm(theme, meta):
    """Page-millimetre box (x, y, w, h) for the native scale-ratio label that
    overlays the metadata "Scale @ A4" value cell. Mirrors the metadata-block
    geometry render_panel uses, so the native label lands exactly where the SVG
    value would have. Returns None if there is no scale row.

    The native label is right-aligned and vertically centred in this box so it
    reads as part of the same row as the "Scale @ A4" caption drawn in the SVG.
    """
    rows = _meta_rows(meta)
    idx = next((i for i, (lab, _v) in enumerate(rows)
                if str(lab).lower().startswith("scale")), None)
    if idx is None:
        return None
    dv = _footer_divider_y(theme, meta)
    meta_top = _meta_top_au(dv, rows, meta)
    by = meta_top + 60 + idx * 64          # value baseline (au), SVG font-size 30
    # Box left edge at the value column (au x=300, left-aligned like the other
    # metadata values); top just above the caption cap-height so the numeric
    # scale bar's text lines up with the row.
    x0_au, x1_au = 300.0, 664.0
    top_au, h_au = by - 23.0, 37.0
    x = PANEL_X_MM + x0_au / _U_PER_MM
    y = top_au / _U_PER_MM
    w = (x1_au - x0_au) / _U_PER_MM
    h = h_au / _U_PER_MM
    return (x, y, w, h)


def render_panel(theme, meta, plugin_dir=None, out_path=None):
    """Build the themed panel SVG. Returns (out_path, legend_rect_page_mm).

    Everything flows from the header height, which is computed from the
    wrapped title / client / project text, so no two elements collide
    regardless of text length.
    """
    PW, PH = 702, 2100
    TEXT_X, TEXT_MAXW = 40, 486     # text column; right side reserved for arrow
    ARROW_CX = 600

    header = theme.get("header", "#1C2E25")
    tcol   = theme.get("titleText", "#FFFFFF")
    body   = theme.get("bodyText", "#1A2420")
    accent = theme.get("accent") or _lighten(header)

    title = (meta.get("title") or "").upper()
    subtitle = meta.get("subtitle") or meta.get("client") or ""
    ref_line = meta.get("ref_line")
    if ref_line is None:
        bits = [b for b in (meta.get("client"), meta.get("proj_no")) if b]
        ref_line = "  ·  ".join(bits)

    # ── Header: stack title → subtitle → ref, measuring as we go ──────────
    t_fs, t_ls, t_lines = _title_lines(title, TEXT_MAXW)
    s_lines = _wrap(subtitle, 33, 0, TEXT_MAXW, 3)
    r_lines = _wrap(ref_line, 28, 0.5, TEXT_MAXW, 2)

    y = 42
    title_svg, y = _emit(t_lines, TEXT_X, y, t_fs, t_fs * 1.16, tcol, 700, t_ls)
    sub_svg = ref_svg = ""
    if s_lines:
        y += 16
        sub_svg, y = _emit(s_lines, TEXT_X, y, 33, 42, tcol, 400, 0, 0.85)
    if r_lines:
        y += 8
        ref_svg, y = _emit(r_lines, TEXT_X, y, 28, 35, tcol, 600, 0.5, 0.72)
    header_h = y + 40
    # The header must be tall enough to hold the north arrow without clipping.
    # The arrow's 'N' label reaches ~72 au above the header centre and the
    # needle ~30 au below it, so the header needs at least ~204 au of height.
    # When no client/project is entered the text alone can fall short, so floor
    # it here — this also gives a short title some breathing room.
    header_h = max(header_h, 204)

    # North arrow: vertically centred in the (min-height-guaranteed) header.
    arrow_svg = _north_arrow(ARROW_CX, header_h / 2.0, _contrast(header))

    # ── Legend block, directly under the header ───────────────────────────
    leg_label_y = header_h + 52
    leg_div_y   = header_h + 74
    leg_gap_top = min(header_h + 90, 1000)
    # (legend_rect is finished below, once the footer-anchored metadata block
    # has fixed where the legend gap ends.)

    # Optional caption beside the LEGEND heading (e.g. a key for the "u"
    # suffix used by the per-habitat stat figures). Right-aligned so it never
    # collides with the heading; muted so it reads as a footnote.
    leg_note = meta.get("legend_note")
    leg_note_svg = ""
    if leg_note:
        leg_note_svg = (
            f'<text x="664" y="{leg_label_y:.0f}" fill="{body}" '
            f'text-anchor="end" font-size="23" font-weight="500" '
            f'opacity="0.55">{_esc(leg_note)}</text>')

    # ── Footer + logo first — their position bounds how far down a tall
    #    legend may push the metadata block. ─────────────────────────────────
    if theme.get("sourceCard"):
        logo_svg, logo_top = _source_card_fragment(theme)
    else:
        logo_svg, logo_top = _logo_svg_fragment(theme.get("logo", ""),
                                                trim=bool(theme.get("trimLogo")),
                                                scale_frac=theme_logo_scale(theme))
    if meta.get("show_footnote", True):
        # Fallback names the MEASUREMENT CRS (OSGB36), not the 3857 basemap
        # tile CRS — the old "EPSG:3857" label misread as the measurement CRS.
        ft = _esc(meta.get("footnote") or theme.get("footnote")
                  or "OSGB36 / EPSG:27700 · Generated with Biome")
        fb = logo_top - 34
        dv = fb - 38
        footer_svg = (
            f'<line x1="38" y1="{dv:.0f}" x2="664" y2="{dv:.0f}" '
            f'stroke="#E3E7E1" stroke-width="3"/>'
            f'<text x="38" y="{fb:.0f}" fill="{body}" opacity="0.5" '
            f'font-size="24">{ft}</text>')
    else:
        dv = logo_top - 30
        footer_svg = (f'<line x1="38" y1="{dv:.0f}" x2="664" y2="{dv:.0f}" '
                      f'stroke="#E3E7E1" stroke-width="3"/>')

    # ── Metadata block — by default a fixed band, but it can be pushed down
    #    to sit just below a (variable-height) legend when the caller passes
    #    legend_bottom_au, so Scale/Date never overlap a tall legend. The
    #    block may travel as far as the footer divider allows. ───────────────
    rows = _meta_rows(meta)
    meta_top = _meta_top_au(dv, rows, meta)
    # The legend gap runs from under the header down to just above the
    # metadata block — so a smaller logo (lower block) gives the legend more.
    leg_gap_bot = min(meta_top - 64, _LEG_GAP_MAX_BOT)
    legend_rect = _au_to_mm(38, leg_gap_top, 664, max(leg_gap_bot, leg_gap_top + 100))
    md = [f'<line x1="38" y1="{meta_top+4}" x2="664" y2="{meta_top+4}" '
          f'stroke="#E3E7E1" stroke-width="3"/>']
    by = meta_top + 60
    for label, value in rows:
        md.append(f'<text x="38" y="{by}" fill="{body}" opacity="0.55" '
                  f'font-size="30" font-weight="500">{_esc(label)}</text>')
        # Values sit in a tidy second column (left-aligned) in the same muted
        # tone as the labels, rather than pinned to the far-right edge.
        md.append(f'<text x="300" y="{by}" fill="{body}" opacity="0.55" '
                  f'font-size="30" font-weight="500">{_esc(value)}</text>')
        by += 64
    md_svg = "\n".join(md)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" \
xmlns:xlink="http://www.w3.org/1999/xlink" width="{PW}" height="{PH}" \
viewBox="0 0 {PW} {PH}" font-family="Inter, sans-serif">
  <rect x="0" y="0" width="{PW}" height="{PH}" fill="#F7F8F6"/>
  <rect x="0" y="0" width="{PW}" height="{header_h:.0f}" fill="{header}"/>
  <rect x="0" y="{header_h:.0f}" width="{PW}" height="6" fill="{accent}"/>
  {title_svg}
  {sub_svg}
  {ref_svg}
  {arrow_svg}
  <text x="38" y="{leg_label_y:.0f}" fill="{body}" font-size="27" \
font-weight="600" letter-spacing="2.5" opacity="0.65">LEGEND</text>
  {leg_note_svg}
  <line x1="38" y1="{leg_div_y:.0f}" x2="664" y2="{leg_div_y:.0f}" \
stroke="#E3E7E1" stroke-width="3"/>
  {md_svg}
  {footer_svg}
  {logo_svg}
</svg>'''

    if out_path is None:
        fd, out_path = tempfile.mkstemp(suffix="_biome_panel.svg")
        os.close(fd)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    return out_path, legend_rect
