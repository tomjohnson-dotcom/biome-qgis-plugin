# dashboard_infographic.py
"""Present the Dashboard tab as a report-ready infographic.

One entry point, ``InfographicDialog`` — opened from the small "Infographic"
button on the Dashboard tab. It renders the CURRENT dashboard payload (the
same dict `_update_dashboard` consumes, derived through the same
`_dash_derive` verdict logic, so the poster can never disagree with the tab)
to a high-resolution QImage, previews it, and offers copy-to-clipboard and
save-as-PNG.

The poster is painted with QPainter (dual-version: no chart framework on
QGIS 3), in the sibling style of the GCN HSI infographic export
(`gcn_hsi_dialog._render_infographic`): theme tokens are read at paint time,
so the export follows the app's light/dark mode; fonts are the bundled Inter
cuts. Layout is measured as it paints and the image is cropped to the final
height, so variable-length sections (obligations, movers, credits) never
clip or leave a void.
"""

import os

from qgis.PyQt.QtCore import QByteArray, QDate, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage,
                             QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from qgis.PyQt.QtSvg import QSvgRenderer
from qgis.PyQt.QtWidgets import (QApplication, QDialog, QFileDialog,
                                 QHBoxLayout, QLabel, QPushButton,
                                 QScrollArea, QVBoxLayout)

from . import branding, theme
from .config import format_date
from .panel_widgets import _ModuleGlyph

# Logical canvas. Painted at `scale`x (default 2), so the saved PNG is
# 1720 px wide — crisp in a document, small enough to email.
_W = 860
_M = 36                      # page margin
_CW = _W - 2 * _M            # content width
_EST_H = 3200                # generous paint canvas; cropped to true height


# ── data helpers ─────────────────────────────────────────────────────────


def _site_name():
    """Project title, else the saved file's base name, else a placeholder."""
    try:
        from qgis.core import QgsProject
        prj = QgsProject.instance()
        name = (prj.title() or "").strip() or (prj.baseName() or "").strip()
        return name or "Untitled scheme"
    except Exception:
        return "Untitled scheme"


def _wrap_two(fm, text, w):
    """Break text onto at most two lines at word boundaries; the second line
    (or an unbreakable first) is elided to fit."""
    text = str(text)
    if fm.horizontalAdvance(text) <= w:
        return [text]
    words = text.split()
    line1 = ""
    for i, wd in enumerate(words):
        trial = (line1 + " " + wd).strip()
        if line1 and fm.horizontalAdvance(trial) > w:
            return [fm.elidedText(line1, Qt.TextElideMode.ElideRight, w),
                    fm.elidedText(" ".join(words[i:]),
                                  Qt.TextElideMode.ElideRight, w)]
        line1 = trial
    return [fm.elidedText(line1, Qt.TextElideMode.ElideRight, w)]


def _branding_bits():
    """(logo QImage or None, footnote str) from the active branding theme.
    Never opens the branding dialog — an unbranded install just gets the
    default footnote and no logo."""
    logo, footnote = None, branding.DEFAULT_THEME["footnote"]
    try:
        t = branding.active_theme()
        if t:
            footnote = (t.get("footnote") or "").strip() or footnote
            path = t.get("logo") or ""
            if path and os.path.exists(path):
                img = QImage(path)
                if not img.isNull():
                    if t.get("trimLogo"):
                        try:
                            from .panel_renderer import _trim_to_content
                            img = _trim_to_content(img)
                        except Exception:
                            pass
                    logo = img
    except Exception:
        pass
    return logo, footnote


# ── painting ─────────────────────────────────────────────────────────────


def render_infographic(panel, payload, scale=2):
    """Paint the dashboard payload as a poster; returns the QImage."""
    try:
        theme._load_fonts()
    except Exception:
        pass

    v = panel._dash_derive(payload)
    mods, active, scored = v["mods"], v["active"], v["scored"]
    slots_by_mod = v["slots_by_mod"]
    outstanding, slot_total = v["outstanding"], v["slot_total"]
    short_by_mod, compliant = v["short_by_mod"], v["compliant"]
    required_by_mod = v["required_by_mod"]
    net_gap_by_mod = v["net_gap_by_mod"]
    quality = (payload or {}).get("quality") or {}
    movers = (payload or {}).get("movers") or []
    split = (payload or {}).get("split") or (0, 0, 0, 0)
    credits = (payload or {}).get("credits") or {}
    coverage = (payload or {}).get("coverage")

    img = QImage(_W * scale, _EST_H * scale, QImage.Format.Format_RGB32)
    img.fill(QColor(theme.SURFACE))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.scale(scale, scale)

    # -- painter helpers (HSI-infographic idiom) --------------------------
    def setf(px, fam="Inter", w=QFont.Weight.Normal, spacing=None,
             tabular=False):
        if tabular:
            f = theme.numeric_font(px, weight=w)
        else:
            f = QFont(fam)
            f.setPixelSize(int(px))
            f.setWeight(w)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        p.setFont(f)
        return QFontMetricsF(f)

    AL = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    AR = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    AC = Qt.AlignmentFlag.AlignCenter
    WRAP = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap)

    def txt(x, y, w, h, flags, s, color):
        p.setPen(QColor(color))
        p.drawText(QRectF(x, y, w, h), int(flags), str(s))

    def box(x, y, w, h, r, fill, border=None, bw=1.0):
        p.setPen(QPen(QColor(border), bw) if border else Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(fill)) if fill else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x, y, w, h), r, r)

    def card(x, y, w, h):
        box(x, y, w, h, 10, theme.CARD, theme.BORDER, 1.0)

    def hline(x0, x1, y, color=None):
        p.setPen(QPen(QColor(color or theme.BORDER), 1))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))

    def elide(fm, s, w):
        return fm.elidedText(str(s), Qt.TextElideMode.ElideRight, w)

    def chip(x, y, s, fg, bg, px=10, pad=8, fm=None):
        """Draw a pill; returns its width. x may be a callable taking the
        measured width (for right-aligned placement)."""
        fm = fm or setf(px, "Inter Medium", QFont.Weight.Medium)
        w = fm.horizontalAdvance(str(s)) + 2 * pad
        h = px + 10
        if callable(x):
            x = x(w)
        box(x, y, w, h, h / 2.0, bg)
        txt(x, y - 0.5, w, h, AC, s, fg)
        return w

    def ring(cx, cy, rad, stroke, frac, color, glyph=None, ghost=False):
        """Progress-to-target ring, the tile widget's painted twin."""
        r = QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad)
        pen = QPen(QColor(theme.FAINT if ghost else theme.BORDER),
                   max(1.5, stroke * 0.55) if ghost else stroke)
        if ghost:
            pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        if ghost:
            return
        if frac is not None and frac > 0.004:
            pen = QPen(QColor(color), stroke)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 90 * 16, int(-min(1.0, frac) * 360 * 16))
        if glyph:
            pen = QPen(QColor(color), max(2.0, stroke * 0.75))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            s = rad * 0.52
            if glyph == "check":
                p.drawPolyline(QPolygonF([
                    QPointF(cx - s, cy + 0.05 * s),
                    QPointF(cx - 0.2 * s, cy + 0.75 * s),
                    QPointF(cx + s, cy - 0.55 * s)]))
            else:  # "x"
                p.drawLine(QPointF(cx - 0.65 * s, cy - 0.65 * s),
                           QPointF(cx + 0.65 * s, cy + 0.65 * s))
                p.drawLine(QPointF(cx + 0.65 * s, cy - 0.65 * s),
                           QPointF(cx - 0.65 * s, cy + 0.65 * s))

    def mod_glyph(kind, color, x, y, size):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"'
               ' fill="none" stroke="%s" stroke-width="2"'
               ' stroke-linecap="round" stroke-linejoin="round">%s</svg>'
               % (color, _ModuleGlyph._PATHS[kind]))
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(
            p, QRectF(x, y, size, size))

    def stacked_bar(x, y, w, h, segs):
        """_StackedBar's painted twin: 2px surface gaps, rounded outer ends."""
        segs = [(float(val or 0.0), c) for val, c in segs if (val or 0.0) > 1e-9]
        if not segs:
            return
        tot = sum(val for val, _c in segs)
        gap = 2.0
        avail = max(1.0, w - gap * (len(segs) - 1))
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), 4, 4)
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        sx = x
        for val, c in segs:
            seg = val / tot * avail
            p.setBrush(QColor(c))
            p.drawRect(QRectF(sx, y, seg, h))
            sx += seg + gap
        p.restore()

    def meter(x, y, w, h, need, have):
        """_ObligationMeter's painted twin: BAD-tinted track = the obligation,
        GOOD fill = share secured. Surplus is never drawn."""
        r = h / 2.0
        box(x, y, w, h, r, theme.BAD_BG)
        edge = QColor(theme.BAD)
        edge.setAlpha(80)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x + 0.5, y + 0.5, w - 1, h - 1), r, r)
        frac = 0.0 if need <= 0 else max(0.0, min(1.0, have / need))
        if frac * w > 0.5:
            box(x, y, max(frac * w, h), h, r, theme.GOOD)

    # ── header band ──────────────────────────────────────────────────────
    # The title owns the FULL content width and may wrap to two lines (long
    # job titles were cramped against a right-hand meta block — live round
    # 1). The date shares the eyebrow row (both short, can't collide) and
    # the red-line/metric meta sits on its own line under the title; the
    # band grows to fit.
    fm = setf(25, "Inter SemiBold", QFont.Weight.DemiBold)
    t_lines = _wrap_two(fm, _site_name(), _CW)
    LH = 32
    meta_y = 38 + len(t_lines) * LH + 4
    BAND_H = meta_y + 14 + 15
    box(0, 0, _W, BAND_H, 0, theme.BANNER_TOP)
    box(0, BAND_H - 3, _W, 3, 0, theme.LICHEN)
    setf(10, "Inter Medium", QFont.Weight.Medium, spacing=1.6)
    txt(_M, 16, _CW - 210, 15, AL, "BIODIVERSITY NET GAIN — DASHBOARD",
        theme.LICHEN)
    setf(11, "Inter Medium", QFont.Weight.Medium)
    txt(_W - _M - 200, 15, 200, 16, AR, format_date(), "#DCE8DE")
    setf(25, "Inter SemiBold", QFont.Weight.DemiBold)
    for i, ln in enumerate(t_lines):
        txt(_M, 38 + i * LH, _CW, 34, AL, ln, "#FFFFFF")
    setf(10)
    sub_r = "Statutory biodiversity metric · indicative"
    if (payload or {}).get("has_offsite") and not (payload or {}).get(
            "include_offsite", True):
        sub_r += " · redline only (off-site ignored)"
    if coverage and coverage[0] > 1e-6:
        sub_r = "Red line %.2f ha  ·  %s" % (coverage[0], sub_r)
    txt(_M, meta_y, _CW, 14, AL, sub_r, "#AFC7B4")

    y = BAND_H + 20

    # ── verdict hero ─────────────────────────────────────────────────────
    RING_R, PADX = 40, 24
    tx = _M + PADX + 2 * RING_R + 26          # text column start
    tw = _W - _M - PADX - tx                  # text column width
    if compliant:
        sub_bits = ["All modules meet the +10% requirement"]
        if any(slots_by_mod.get(m, {}).get("slots") for m in active):
            sub_bits.append("trading rules satisfied")
        if slot_total:
            sub_bits.append("%d obligation%s covered"
                            % (slot_total, "" if slot_total == 1 else "s"))
        sub = "  ·  ".join(sub_bits)
        fm = setf(11.5)
        sub_h = fm.boundingRect(QRectF(0, 0, tw, 200), int(WRAP), sub).height()
        hero_h = max(2 * RING_R + 36, 18 + 26 + 6 + sub_h + 12 + 24 + 18)
        card(_M, y, _CW, hero_h)
        ring(_M + PADX + RING_R, y + hero_h / 2.0, RING_R, 7, 1.0,
             theme.GOOD, "check")
        setf(21, "Inter SemiBold", QFont.Weight.DemiBold)
        txt(tx, y + 18, tw, 26, AL, "Scheme compliant", theme.INK)
        setf(11.5)
        txt(tx, y + 18 + 26 + 6, tw, sub_h, WRAP, sub, theme.MUTED)
        cx = tx
        cy = y + 18 + 26 + 6 + sub_h + 12
        for m in scored:
            cx += chip(cx, cy, "%+.2f%%  %s" % (mods[m]["pct"],
                                                panel._DASH_TITLES[m].lower()),
                       theme.GOOD, theme.GOOD_BG) + 8
        spare = sum((s.get("spare") or 0.0)
                    for m in active
                    for s in (slots_by_mod[m].get("slots") or [])
                    if s["kind"] == "free")
        if spare > 0.005:
            cx += chip(cx, cy, "%.2f u spare" % spare,
                       theme.MUTED, theme.SUNKEN) + 8
        if not scored:
            chip(cx, cy, "created habitat only", theme.MUTED, theme.SUNKEN)
    else:
        order = [m for m in sorted(
            required_by_mod, key=lambda k: -required_by_mod[k])]
        if not order:
            order = [m for m in scored if mods[m]["pct"] < 10.0]
        met = [panel._DASH_TITLES[m].lower() for m in scored if m not in order]
        n_out = len(outstanding)
        sub_bits = []
        if slot_total and n_out == 0:
            sub_bits.append("Trading obligations met ✓")
        elif slot_total:
            sub_bits.append("%d of %d obligation%s outstanding"
                            % (n_out, slot_total,
                               "" if slot_total == 1 else "s"))
        if met:
            sub_bits.append("  ·  ".join(met) + " met ✓")
        sub = "  ·  ".join(sub_bits) or "Below the +10% net gain requirement"
        hero_h = 18 + 16 + 8 + 40 + 20 + 8 + 20 + 16
        card(_M, y, _CW, hero_h)
        covered_frac = ((slot_total - n_out) / slot_total) if slot_total else 0.0
        ring(_M + PADX + RING_R, y + hero_h / 2.0, RING_R, 7,
             covered_frac, theme.BAD, "x")
        setf(10, "Inter Medium", QFont.Weight.Medium, spacing=1.2)
        txt(tx, y + 18, tw, 14, AL, "UNITS STILL REQUIRED", theme.FAINT)
        nx = tx
        for m in order[:3]:
            # The slot shortfalls (which already include the +10% headroom via
            # the free-choice slot); belt-and-braces fall back to the raw gap
            # to target so a below-target module never prints 0.00.
            need = required_by_mod.get(m)
            if need is None:
                dm = mods[m]
                need = max(0.0, dm.get("bl_on", dm["bl"]) * 1.10
                           - (dm["pr"] or 0.0))
            fm = setf(34, w=QFont.Weight.DemiBold, tabular=True)
            ns = "<0.01" if 0.0 < need < 0.01 else "%.2f" % need
            nw = fm.horizontalAdvance(ns)
            txt(nx, y + 18 + 16 + 8, nw + 4, 40, AL, ns, theme.BAD)
            fm2 = setf(10.5, "Inter Medium", QFont.Weight.Medium)
            lab = "%s units" % panel._DASH_TITLES[m].lower()
            txt(nx, y + 18 + 16 + 8 + 40, max(nw, fm2.horizontalAdvance(lab)),
                16, AL, lab, theme.MUTED)
            nx += max(nw, fm2.horizontalAdvance(lab)) + 34
        if (order and net_gap_by_mod.get(order[0], 0.0) > 1e-9
                and short_by_mod.get(order[0], 0.0) <= 0.005):
            sub_bits.append("below +10% net gain")
            sub = "  ·  ".join(sub_bits)
        setf(11)
        txt(tx, y + hero_h - 16 - 20, tw, 20, AL,
            elide(setf(11), sub, tw), theme.FAINT)
    y += hero_h + 16

    # ── module tiles ─────────────────────────────────────────────────────
    TILE_H = 208
    tile_w = (_CW - 2 * 14) / 3.0
    for i, m in enumerate(panel._DASH_MODS):
        x = _M + i * (tile_w + 14)
        card(x, y, tile_w, TILE_H)
        dm = mods.get(m, {})
        act = bool(dm.get("active"))
        name = panel._DASH_TITLES[m]
        fm = setf(11, "Inter Medium", QFont.Weight.Medium)
        gsz = 14
        head_w = gsz + 6 + fm.horizontalAdvance(name)
        hx = x + (tile_w - head_w) / 2.0
        mod_glyph(panel._DASH_GLYPHS[m], theme.MUTED, hx, y + 15, gsz)
        txt(hx + gsz + 6, y + 14, head_w, 16, AL, name, theme.MUTED)
        rcx, rcy, rrad = x + tile_w / 2.0, y + 40 + 31, 29
        vy = rcy + rrad + 12                      # value baseline area
        if not act:
            ring(rcx, rcy, rrad, 5.5, None, theme.FAINT, ghost=True)
            setf(19, w=QFont.Weight.DemiBold, tabular=True)
            txt(x, vy, tile_w, 24, AC, "—", theme.FAINT)
            setf(9.5, "Inter Medium", QFont.Weight.Medium)
            txt(x, vy + 26, tile_w, 14, AC, "not mapped", theme.FAINT)
            hline(x + 14, x + tile_w - 14, y + TILE_H - 40)
            setf(10)
            txt(x, y + TILE_H - 32, tile_w, 16, AC, "no baseline", theme.FAINT)
            continue
        bl, pr, pct = dm["bl"], dm["pr"], dm["pct"]
        bl_on = dm.get("bl_on", bl)
        sv = slots_by_mod.get(m) or {}
        trading_ok = bool(sv.get("trading_ok", True))
        target = bl * 1.10
        frac = 1.0 if target <= 1e-9 else min(1.0, (pr or 0.0) / target)
        mod_ok = trading_ok and (pct is None or pct >= 10.0)
        ring(rcx, rcy, rrad, 5.5, frac, theme.GOOD if mod_ok else theme.BAD,
             "check" if mod_ok else "x")
        setf(19, w=QFont.Weight.DemiBold, tabular=True)
        txt(x, vy, tile_w, 24, AC, "—" if pct is None else "%+.2f%%" % pct,
            panel._dash_state_color(pct))
        if not trading_ok:
            fail = "policy ⚠" if m == "watercourse" else "trading ✗"
            chip(lambda w, _x=x, _tw=tile_w: _x + (_tw - w) / 2.0,
                 vy + 26, fail, theme.BAD, theme.BAD_BG, px=9.5)
        else:
            setf(9.5, "Inter Medium", QFont.Weight.Medium)
            txt(x, vy + 26, tile_w, 14, AC,
                "compliant" if mod_ok else "below +10%", theme.FAINT)
        hline(x + 14, x + tile_w - 14, y + TILE_H - 46)
        setf(11, w=QFont.Weight.Medium, tabular=True)
        txt(x, y + TILE_H - 40, tile_w, 16, AC,
            "%.2f → %.2f u" % (bl_on, pr or 0.0), theme.INK)
        setf(9.5, tabular=True)
        txt(x, y + TILE_H - 24, tile_w, 14, AC,
            "target %.2f u  ·  net %+.2f u" % (target, (pr or 0.0) - (bl or 0.0)),
            theme.FAINT)
    y += TILE_H + 16

    # ── section scaffolding ──────────────────────────────────────────────
    def sec_head(x, w, sy, title, note=""):
        fm = setf(10, "Inter SemiBold", QFont.Weight.DemiBold, spacing=1.1)
        tw_ = fm.horizontalAdvance(title)
        txt(x + 16, sy + 14, tw_ + 4, 14, AL, title, theme.FAINT)
        nx = x + 16 + tw_ + 10
        if note:
            fm2 = setf(9)
            txt(nx, sy + 14, fm2.horizontalAdvance(note) + 4, 14, AL, note,
                theme.FAINT)
            nx += fm2.horizontalAdvance(note) + 10
        hline(nx, x + w - 16, sy + 21)
        return sy + 14 + 14 + 8               # content start y

    # ── still required ───────────────────────────────────────────────────
    if outstanding:
        MAX_ROWS = 8
        rows = outstanding[:MAX_ROWS]
        extra = len(outstanding) - len(rows)
        ROW_H, GAP = 54, 10
        sec_h = (36 + 8 + len(rows) * (ROW_H + GAP) - GAP
                 + (20 if extra else 0) + 14)
        card(_M, y, _CW, sec_h)
        ry = sec_head(_M, _CW, y, "STILL REQUIRED",
                      "%d obligation%s under the trading rules"
                      % (len(outstanding), "" if len(outstanding) == 1 else "s"))
        for m, s in rows:
            head, chip_txt, _tip = panel._dash_slot_caption(m, s)
            x0 = _M + 16
            iw = _CW - 32
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(panel._dash_mod_color(m)))
            p.drawEllipse(QRectF(x0, ry + 5.5, 8, 8))
            fm = setf(12, w=QFont.Weight.DemiBold, tabular=True)
            short_s = "%.2f short" % s["short"]
            short_w = fm.horizontalAdvance(short_s) + 4
            txt(x0 + iw - short_w, ry - 1, short_w, 20, AR, short_s, theme.BAD)
            fmc = setf(9, "Inter Medium", QFont.Weight.Medium)
            chip_w = fmc.horizontalAdvance(chip_txt) + 16
            fm = setf(12.5, "Inter SemiBold", QFont.Weight.DemiBold)
            head_w = iw - 18 - chip_w - 8 - short_w - 8
            head_e = elide(fm, head, head_w)
            txt(x0 + 18, ry - 1, head_w, 20, AL, head_e, theme.INK)
            floor = s["kind"] == "floor"
            chip(x0 + 18 + min(head_w, fm.horizontalAdvance(head_e)) + 8,
                 ry - 1, chip_txt,
                 theme.WARN if floor else theme.FAINT,
                 theme.WARN_BG if floor else theme.SUNKEN, px=9)
            meter(x0, ry + 24, iw, 8, s["need"], s["have"])
            setf(9.5, tabular=True)
            txt(x0, ry + 36, iw / 2.0, 13, AL,
                "%.2f u secured" % s["have"], theme.FAINT)
            txt(x0 + iw / 2.0, ry + 36, iw / 2.0, 13, AR,
                "%.2f u required" % s["need"], theme.FAINT)
            ry += ROW_H + GAP
        if extra:
            setf(9.5)
            txt(_M, ry - 4, _CW, 14, AC,
                "+%d more obligation%s — see the Dashboard tab"
                % (extra, "" if extra == 1 else "s"), theme.FAINT)
        y += sec_h + 16

    # ── what changed | area split ────────────────────────────────────────
    show_movers = bool(movers)
    ret, enh, cre, lost = split
    show_split = any(val > 1e-9 for val in split)
    if show_movers or show_split:
        LW = 440 if (show_movers and show_split) else _CW
        RW = _CW - LW - 14
        top = movers[:7]
        lh = (36 + 8 + len(top) * 24 + (18 if len(movers) > 7 else 0) + 12
              ) if show_movers else 0
        rh = (36 + 8 + 16 + 10 + 4 * 21 + 12) if show_split else 0
        both_h = max(lh, rh)
        if show_movers:
            card(_M, y, LW, both_h if show_split else lh)
            ry = sec_head(_M, LW, y, "WHAT CHANGED", "units")
            iw = LW - 32
            maxabs = max(abs(val) for _n, val in top) or 1.0
            bar_w, val_w = 110, 54
            name_w = iw - bar_w - val_w - 20
            for name, delta in top:
                fm = setf(11)
                txt(_M + 16, ry, name_w, 18, AL, elide(fm, name, name_w),
                    theme.MUTED)
                # diverging bar, centre tick on top
                bx = _M + 16 + name_w + 10
                bh, bcy = 9, ry + 9
                fracb = max(-1.0, min(1.0, delta / maxabs))
                bw = abs(fracb) * (bar_w / 2.0 - 2)
                p.setPen(Qt.PenStyle.NoPen)
                if bw > 0.5:
                    p.setBrush(QColor(theme.GOOD if delta > 0 else theme.BAD))
                    bxx = bx + bar_w / 2.0 if delta > 0 else bx + bar_w / 2.0 - bw
                    p.drawRoundedRect(QRectF(bxx, bcy - bh / 2.0, bw, bh), 2, 2)
                p.setBrush(QColor(theme.MUTED))
                p.drawRect(QRectF(bx + bar_w / 2.0 - 0.75, bcy - bh / 2.0 - 2,
                                  1.5, bh + 4))
                setf(11, w=QFont.Weight.DemiBold, tabular=True)
                txt(bx + bar_w + 10, ry, val_w, 18, AR, "%+.2f" % delta,
                    theme.GOOD if delta > 0 else theme.BAD)
                ry += 24
            if len(movers) > 7:
                setf(9.5)
                txt(_M, ry, LW, 14, AC, "+%d smaller changes"
                    % (len(movers) - 7), theme.FAINT)
        if show_split:
            sx = (_M + LW + 14) if show_movers else _M
            sw = RW if show_movers else _CW
            card(sx, y, sw, both_h if show_movers else rh)
            ry = sec_head(sx, sw, y, "AREA SPLIT", "hectares")
            iw = sw - 32
            stacked_bar(sx + 16, ry, iw, 14,
                        [(ret, theme.SPLIT_RETAINED), (enh, theme.GOOD),
                         (cre, theme.SPLIT_CREATED), (lost, theme.BAD)])
            ry += 14 + 10
            for label, val, col in (("Retained", ret, theme.SPLIT_RETAINED),
                                    ("Enhanced", enh, theme.GOOD),
                                    ("Created", cre, theme.SPLIT_CREATED),
                                    ("Lost", lost, theme.BAD)):
                box(sx + 16, ry + 3, 10, 10, 3, col)
                setf(11)
                txt(sx + 16 + 16, ry, iw - 70, 16, AL, label, theme.MUTED)
                setf(11, w=QFont.Weight.Medium, tabular=True)
                txt(sx + 16, ry, iw, 16, AR, "%.2f" % val, theme.INK)
                ry += 21
        y += max((both_h if (show_movers and show_split) else 0), lh, rh) + 16

    # ── statutory credits ────────────────────────────────────────────────
    if active:
        # Match the live dashboard verdict. A compliant scheme may carry up to
        # 0.005 units of numerical residue; at the statutory 2:1 rate that can
        # round to 0.01 credits, but it is within the accepted tolerance and
        # must not be presented as a purchase requirement.
        nz = ([] if compliant else
              [(t_, val) for t_, val in credits.items() if val > 5e-4])
        nz.sort(key=lambda kv: -kv[1])
        rows_h = (len(nz) * 26 - 6) if nz else 20
        sec_h = 36 + 8 + rows_h + 14
        card(_M, y, _CW, sec_h)
        ry = sec_head(_M, _CW, y, "STATUTORY CREDITS",
                      "indicative requirement" if nz else "")
        if not nz:
            setf(11.5, "Inter Medium", QFont.Weight.Medium)
            txt(_M, ry, _CW, 18, AC, "No credits required ✓", theme.GOOD)
        else:
            cmax = max(val for _t, val in nz)
            tier_fill = {"H": theme.MOD_HEDGE, "W": theme.INFO}
            iw = _CW - 32
            for t_, val in nz:
                x0 = _M + 16
                box(x0, ry, 36, 19, 5, tier_fill.get(t_, theme.ACCENT_DARK))
                setf(10.5, "Inter SemiBold", QFont.Weight.DemiBold)
                txt(x0, ry - 0.5, 36, 19, AC, t_, "#FFFFFF")
                bar_x = x0 + 36 + 12
                val_w = 64
                bar_w = iw - 36 - 12 - val_w - 12
                box(bar_x, ry + 4.5, bar_w, 10, 5, theme.SUNKEN)
                fw = max(0.0, min(1.0, val / cmax)) * bar_w
                if fw > 1.0:
                    box(bar_x, ry + 4.5, max(fw, 10), 10, 5, theme.BAD)
                setf(12, w=QFont.Weight.DemiBold, tabular=True)
                txt(x0 + iw - val_w, ry, val_w, 19, AR, "%.2f" % val,
                    theme.INK)
                ry += 26
        y += sec_h + 16

    # ── footer ───────────────────────────────────────────────────────────
    y += 2
    hline(_M, _W - _M, y)
    y += 12
    logo, footnote = _branding_bits()
    logo_w = 0
    if logo is not None:
        lh_ = 30.0
        logo_w = lh_ * logo.width() / max(1, logo.height())
        if logo_w > 170:
            logo_w = 170.0
            lh_ = logo_w * logo.height() / max(1, logo.width())
        p.drawImage(QRectF(_W - _M - logo_w, y, logo_w, lh_), logo,
                    QRectF(0, 0, logo.width(), logo.height()))
        logo_w += 20
    bits = []
    total = quality.get("total", 0)
    if total:
        bits.append("%d feature%s mapped" % (total, "" if total == 1 else "s"))
        inc = quality.get("incomplete", 0)
        bits.append("%d incomplete" % inc if inc else "all attributes complete")
        if coverage and coverage[0] > 1e-6:
            rl_ha, bl_ha, pr_ha = coverage
            gap = max(rl_ha - bl_ha, rl_ha - pr_ha)
            pct_cov = min(bl_ha, pr_ha) / rl_ha * 100.0
            bits.append("%.0f%% of the red line mapped" % pct_cov
                        if gap > 5e-3 else "100% of the red line mapped")
        geom_bad = quality.get("geom_bad", 0)
        bits.append("%d invalid geometr%s" % (geom_bad,
                    "y" if geom_bad == 1 else "ies")
                    if geom_bad else "geometry valid")
    fw_ = _CW - logo_w
    if bits:
        setf(10)
        txt(_M, y, fw_, 15, AL, elide(setf(10), "  ·  ".join(bits), fw_),
            theme.FAINT)
        y += 17
    setf(10)
    line2 = footnote if "Biome" in footnote else (footnote +
                                                  " · Generated with Biome")
    line2 = "%s  ·  %s" % (line2, format_date())
    txt(_M, y, fw_, 15, AL, elide(setf(10), line2, fw_), theme.FAINT)
    y += 15 + 24

    p.end()
    return img.copy(0, 0, _W * scale, int(y * scale))


# ── dialog ───────────────────────────────────────────────────────────────


class InfographicDialog(QDialog):
    """Preview of the rendered infographic with copy / save actions."""

    def __init__(self, panel, payload, parent=None):
        from qgis.PyQt.QtWidgets import QWidget
        if parent is None and isinstance(panel, QWidget):
            parent = panel
        super().__init__(parent)
        self.setWindowTitle("Dashboard infographic")
        self.setProperty("biome_fixed_color", True)
        self._img = render_infographic(panel, payload, scale=2)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background:%s; border:1px"
                                   " solid %s; border-radius:8px; }"
                                   % (theme.SUNKEN, theme.BORDER))
        self._scroll.setProperty("biome_fixed_color", True)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignHCenter
                                   | Qt.AlignmentFlag.AlignTop)
        self._preview.setStyleSheet("background:%s; border:none;"
                                    % theme.SUNKEN)
        self._preview.setProperty("biome_fixed_color", True)
        self._scroll.setWidget(self._preview)
        root.addWidget(self._scroll, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        copy_btn = QPushButton("Copy image")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(
            "QPushButton { background:%s; color:#FFFFFF; border:none;"
            " border-radius:8px; padding:8px 16px; font-size:12px;"
            " font-weight:600; font-family:%s; }"
            " QPushButton:hover { background:%s; }"
            % (theme.ACCENT_DARK, theme.FONT_SEMIBOLD, theme.ACCENT))
        copy_btn.setProperty("biome_fixed_color", True)
        copy_btn.clicked.connect(self._copy)
        save_btn = QPushButton("Save PNG…")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton { background:%s; color:%s; border:1px solid %s;"
            " border-radius:8px; padding:8px 14px; font-size:12px;"
            " font-weight:600; font-family:%s; }"
            " QPushButton:hover { background:%s; }"
            % (theme.CARD, theme.INK, theme.BORDER_STRONG,
               theme.FONT_SEMIBOLD, theme.ACCENT_SOFT))
        save_btn.setProperty("biome_fixed_color", True)
        save_btn.clicked.connect(self._save)
        self._status = QLabel("")
        self._status.setStyleSheet("color:%s; font-size:11px; border:none;"
                                   " background:transparent;" % theme.FAINT)
        self._status.setProperty("biome_fixed_color", True)
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background:transparent; color:%s; border:none;"
            " padding:8px 10px; font-size:12px; font-family:%s; }"
            " QPushButton:hover { color:%s; }"
            % (theme.MUTED, theme.FONT_MEDIUM, theme.INK))
        close_btn.setProperty("biome_fixed_color", True)
        close_btn.clicked.connect(self.reject)
        row.addWidget(copy_btn)
        row.addWidget(save_btn)
        row.addWidget(self._status, 1)
        row.addWidget(close_btn)
        root.addLayout(row)

        self.setStyleSheet("QDialog { background:%s; }" % theme.SURFACE)
        self.resize(724, 840)

    # -- preview scaling --------------------------------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_preview()

    def showEvent(self, e):
        super().showEvent(e)
        self._fit_preview()

    def _fit_preview(self):
        vw = max(300, self._scroll.viewport().width() - 16)
        dpr = theme.device_pixel_ratio(self)
        scaled = self._img.scaledToWidth(
            int(vw * dpr), Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(scaled)
        pm.setDevicePixelRatio(dpr)
        self._preview.setPixmap(pm)
        self._preview.resize(int(vw + 16),
                             int(scaled.height() / dpr) + 16)

    # -- actions ----------------------------------------------------------
    def _copy(self):
        QApplication.clipboard().setImage(self._img)
        self._status.setText("Copied ✓ — paste into a report, email or chat.")

    def _save(self):
        safe = "".join(c if (c.isalnum() or c in " -_") else "_"
                       for c in _site_name()).strip().replace(" ", "_")
        base = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        default = os.path.join(base, "BNG_dashboard_%s_%s.png"
                               % (safe or "scheme",
                                  QDate.currentDate().toString("yyyy-MM-dd")))
        path, _f = QFileDialog.getSaveFileName(
            self, "Save dashboard infographic", default, "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        ok = self._img.save(path, "PNG")
        self._status.setText("Saved ✓  %s" % os.path.basename(path)
                             if ok else "Could not save the image.")
