# panel_widgets.py — module-level helpers, painted widgets and constants
# extracted verbatim from main_panel.py (2026-07-22 split).

import os

from qgis.PyQt.QtWidgets import (QWidget, QLabel, QSizePolicy, QTableWidgetItem,
    QLayout)
from qgis.PyQt.QtCore import Qt, QSize, QRect, QPoint
from qgis.PyQt.QtGui import QColor
from . import theme


def _dbg(msg):
    """TEMPORARY perf logging → biome_debug.log next to this module. Remove
    once the habitat-form navigation lag is diagnosed."""
    try:
        import time
        with open(os.path.join(os.path.dirname(__file__), "biome_debug.log"),
                  "a", encoding="utf-8") as _fh:
            _fh.write("%.3f  %s\n" % (time.time(), msg))
    except Exception:
        pass


def _build_no_gc(factory):
    """Build a heavy widget with cyclic GC paused. On a large project the GC scan
    during the widget-allocation burst is what makes form construction drag — and
    scale with project size. Refcounting still frees everything immediately; we
    just skip cycle collection for the burst, then re-enable."""
    import gc
    was_enabled = gc.isenabled()
    if was_enabled:
        gc.disable()
    try:
        return factory()
    finally:
        if was_enabled:
            gc.enable()


# Manage-tab "duplicate to the other layer" pairing: each habitat layer key maps
# to its baseline<->proposed twin. Only these 8 keys get a Duplicate button
# (redline / ecological layers are excluded).
DUP_PAIR = {
    "baseline_area":     "proposed_area",     "proposed_area":     "baseline_area",
    "baseline_hedgerow": "proposed_hedgerow", "proposed_hedgerow": "baseline_hedgerow",
    "baseline_water":    "proposed_water",    "proposed_water":    "baseline_water",
    "baseline_trees":    "proposed_trees",    "proposed_trees":    "baseline_trees",
}


def _style_hover_highlight(h, r, g, b, width=6, buffer_mm=1.2,
                          min_width_mm=5.0, fill_alpha=70, outline_alpha=255):
    """Configure a QgsHighlight for a clearly-visible Manage-features hover.

    A bold outline plus a buffer / minimum-width halo so POINT (tree) and LINE
    (hedge) features render as a real, easy-to-spot shape instead of a hairline,
    on any habitat colour. ``setBuffer`` / ``setMinWidth`` are guarded in case an
    older QGIS build lacks them."""
    h.setColor(QColor(r, g, b, outline_alpha))
    h.setFillColor(QColor(r, g, b, fill_alpha))
    h.setWidth(width)
    try:
        h.setBuffer(buffer_mm)        # mm halo — widens points & thin lines
    except Exception:
        pass
    try:
        h.setMinWidth(min_width_mm)   # mm — guarantees a minimum footprint
    except Exception:
        pass


class _NumericSortItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a numeric value stored in Qt.UserRole.

    Use this for any column that displays a formatted distance string
    (e.g. "1.22 km SW", "498 m NW") so that Qt sorts by the raw integer
    distance in metres rather than alphabetically by the display text.

    Usage:
        item = _NumericSortItem(display_text)
        item.setData(Qt.UserRole, distance_m)   # int, metres
    """

    def __lt__(self, other):
        try:
            return self.data(Qt.ItemDataRole.UserRole) < other.data(Qt.ItemDataRole.UserRole)
        except TypeError:
            return super().__lt__(other)


# ── Responsive dashboard primitives (width-driven; repaint to current width) ──
def _gain_color(pct, target=10.0):
    """Green when the +target net gain is met, amber for a gain below target,
    red for a net loss, grey when not assessable."""
    if pct is None:
        return "#717E75"   # FAINT
    if pct >= target:
        return "#1F7A4D"   # GOOD
    if pct >= 0:
        return "#9A6700"   # WARN
    return "#B3372F"       # BAD


class _GaugeBar(QWidget):
    """Net-change gauge. The bar FILLS from the left edge (−100%, total loss)
    rightward to the value; the tick marks 0% (no change), and gains run
    open-ended to the right (room to +%RMAX, clamped beyond). Fill colour encodes
    the result — green once it meets the +target, amber for a gain still short,
    red for a net loss. Width-driven, so it reflows on resize."""
    _ZERO = 0.40     # 0% tick position as a fraction of the width
    _RMAX = 200.0    # net % mapped to the right edge (clamps above)
    def __init__(self, value_pct, target_pct=10.0, height=10):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        self._v = None if value_pct is None else float(value_pct)
        self._t = float(target_pct) or 10.0

    def _frac(self, v):
        # −100% → 0.0, 0% → _ZERO, +_RMAX → 1.0 (clamped to [0, 1]).
        if v <= 0.0:
            f = self._ZERO * (v + 100.0) / 100.0
        else:
            f = self._ZERO + min(v, self._RMAX) / self._RMAX * (1.0 - self._ZERO)
        return max(0.0, min(1.0, f))

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter, QPen
        from qgis.PyQt.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width()); h = float(self.height()); r = h / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SUNKEN))                 # track follows theme
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        if self._v is not None:                          # fill from −100% (left)
            end = self._frac(self._v) * w
            if end > 1.0:
                p.setBrush(QColor(_gain_color(self._v, self._t)))
                p.drawRoundedRect(QRectF(0, 0, max(end, h), h), r, r)
        tx = int(self._frac(self._t) * w)                # +target (+10%) tick
        p.setPen(QPen(QColor(theme.INK), 1.4))
        p.drawLine(tx, 0, tx, int(h))
        p.end()


class _SlotBar(QWidget):
    """One Net-Gain obligation slot: how much the trading rules demand and how
    much of it is already supplied.

    The bar IS the requirement: it spans ``need`` and nothing more, so a filled
    length always means the same thing — progress toward that obligation. Full
    bar = covered, part bar = short by the pale remainder.

    Surplus is deliberately NOT drawn. An earlier version extended the bar past
    ``need`` to show spare, which collided with the short case: a pale region
    right of the fill meant "missing" on one row and "excess" on the next, and
    both read at a glance as needing a top-up. Spare is reported in the row's
    status text instead, where it cannot be confused with a deficit.

    Drawn on a scale SHARED across a module's slots (``scale``) so the relative
    size of each obligation is visible. Flat tints, not hatching — diagonal
    stripes at this size read as noise.
    """
    def __init__(self, need, have, scale, height=20):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        self._need  = max(0.0, float(need or 0.0))
        self._have  = max(0.0, float(have or 0.0))
        self._scale = float(scale) or 1.0

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter, QPen
        from qgis.PyQt.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width()); h = float(self.height())
        if self._need <= 0 or w <= 2:
            p.end(); return
        px    = w / self._scale                       # units → pixels
        needx = self._need * px                       # the bar = the obligation
        solid = min(self._have, self._need) * px      # progress toward it
        p.setPen(Qt.PenStyle.NoPen)

        if self._have < self._need:                   # pale remainder = missing
            p.setBrush(QColor(theme.BAD_BG))
            p.drawRoundedRect(QRectF(0, 0, needx, h), 3, 3)
            _edge = QColor(theme.BAD); _edge.setAlpha(110)
            p.setPen(QPen(_edge, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, max(needx - 1, 1), h - 1), 3, 3)
            p.setPen(Qt.PenStyle.NoPen)

        if solid > 0.5:
            p.setBrush(QColor(theme.GOOD))
            p.drawRoundedRect(QRectF(0, 0, max(solid, 3.0), h), 3, 3)
        p.end()


class _DivergingBar(QWidget):
    """Centre line with a bar extending right (gain, green) or left (loss, red),
    scaled against max_abs. Width-driven."""
    def __init__(self, value, max_abs, height=14):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        self._v = float(value or 0.0)
        self._m = float(max_abs) or 1.0

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter
        from qgis.PyQt.QtCore import QRectF
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width()); h = float(self.height()); cx = w / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        frac = max(-1.0, min(1.0, self._v / self._m))
        bw = abs(frac) * (cx - 2.0)
        if bw > 0.5:
            p.setBrush(QColor(theme.GOOD if self._v >= 0 else theme.BAD))
            x = cx if self._v >= 0 else cx - bw
            p.drawRoundedRect(QRectF(x, h * 0.18, bw, h * 0.64), 2, 2)
        # Zero-reference tick — drawn on top of the bar so it stays visible.
        p.setBrush(QColor(theme.MUTED))
        p.drawRect(QRectF(cx - 0.75, 0, 1.5, h))
        p.end()


class _StackedBar(QWidget):
    """Proportional horizontal segments [(value, '#hex'), ...]. Width-driven.
    Touching segments are separated by a 2px gap in the surface colour (the gap
    does the separating — never a stroke) and the bar's outer corners are
    rounded."""
    def __init__(self, segments, height=14):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        self._segs = [(float(v or 0.0), c) for v, c in segments if (v or 0.0) > 1e-9]

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter, QPainterPath
        from qgis.PyQt.QtCore import QRectF
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width()); h = float(self.height())
        tot = sum(v for v, _ in self._segs) or 1.0
        n = len(self._segs)
        if n == 0 or w <= 2:
            p.end(); return
        GAP = 2.0
        avail = max(1.0, w - GAP * (n - 1))
        # Clip to a rounded rect so the first/last segments get round outer
        # corners without per-segment path work.
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 4, 4)
        p.setClipPath(path)
        x = 0.0
        p.setPen(Qt.PenStyle.NoPen)
        for v, c in self._segs:
            seg = v / tot * avail
            p.setBrush(QColor(c))
            p.drawRect(QRectF(x, 0, seg, h))
            x += seg + GAP
        p.end()


class _MiniBar(QWidget):
    """Single left-anchored bar, value/max of the width. Width-driven."""
    def __init__(self, value, max_val, color, height=11):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        self._v = float(value or 0.0)
        self._m = float(max_val) or 1.0
        self._c = color

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter
        from qgis.PyQt.QtCore import QRectF
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width()); h = float(self.height()); r = h / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SUNKEN))
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        fw = max(0.0, min(1.0, self._v / self._m)) * w
        if fw > 1.0:
            p.setBrush(QColor(self._c))
            p.drawRoundedRect(QRectF(0, 0, max(fw, h), h), r, r)
        p.end()


class _ProgressRing(QWidget):
    """Circular progress-to-target ring for a module tile.

    States (set_state):
      * pct 0..100 + colour + glyph "check"/"x"/None — progress arc from 12
        o'clock, round caps, centre verdict glyph.
      * ghost=True — dashed faint track, no arc, no glyph ("not mapped").

    Colours are theme tokens resolved at PAINT time so a light/dark flip
    repaints correctly with no restyle pass. The arc animates old→new via
    QVariantAnimation (245 ms) so live map edits read as movement, not a
    redraw; the very first set_state snaps (no sweep-from-zero on tab open
    after every rebuild — the widget persists, so this runs once)."""

    def __init__(self, size=52, stroke=5):
        super().__init__()
        self.setFixedSize(size, size)
        self._stroke = float(stroke)
        self._pct = None          # displayed (animated) value
        self._target_pct = None   # value being animated toward
        self._color_token = "GOOD"
        self._glyph = None
        self._ghost = True
        self._anim = None

    def set_state(self, pct, color_token="GOOD", glyph=None, ghost=False):
        self._ghost = bool(ghost)
        self._color_token = color_token
        self._glyph = glyph
        if ghost or pct is None:
            self._pct = self._target_pct = None
            self._stop_anim()
            self.update()
            return
        pct = max(0.0, min(100.0, float(pct)))
        if self._pct is None:                 # first real value — snap
            self._pct = self._target_pct = pct
            self.update()
            return
        if abs(pct - (self._target_pct or 0.0)) < 0.5:
            self._pct = self._target_pct = pct
            self.update()
            return
        self._target_pct = pct
        self._animate_to(pct)

    def _stop_anim(self):
        if self._anim is not None:
            try:
                self._anim.stop()
            except Exception:
                pass
            self._anim = None

    def _animate_to(self, pct):
        self._stop_anim()
        try:
            from qgis.PyQt.QtCore import QVariantAnimation, QEasingCurve
            a = QVariantAnimation(self)
            a.setDuration(245)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.setStartValue(float(self._pct))
            a.setEndValue(float(pct))

            def _step(v, _self=self):
                _self._pct = float(v)
                _self.update()

            a.valueChanged.connect(_step)
            a.start()
            self._anim = a
        except Exception:
            self._pct = pct
            self.update()

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter, QPen
        from qgis.PyQt.QtCore import QRectF, QPointF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = float(min(self.width(), self.height()))
        sw = self._stroke
        r = QRectF(sw / 2 + 1, sw / 2 + 1, s - sw - 2, s - sw - 2)
        if self._ghost:
            pen = QPen(QColor(theme.BORDER), sw)
            pen.setStyle(Qt.PenStyle.DotLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 0, 360 * 16)
            p.end()
            return
        p.setPen(QPen(QColor(theme.SUNKEN), sw))
        p.drawArc(r, 0, 360 * 16)
        col = QColor(getattr(theme, self._color_token, theme.GOOD))
        if self._pct is not None and self._pct > 0.5:
            pen = QPen(col, sw)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            span = -int(self._pct / 100.0 * 360 * 16)   # clockwise from 12
            p.drawArc(r, 90 * 16, span)
        if self._glyph in ("check", "x"):
            pen = QPen(col, 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            c = s / 2.0
            g = s * 0.115                     # glyph half-extent
            if self._glyph == "check":
                p.drawPolyline([QPointF(c - g, c + g * 0.1),
                                QPointF(c - g * 0.25, c + g * 0.85),
                                QPointF(c + g, c - g * 0.75)])
            else:
                p.drawLine(QPointF(c - g, c - g), QPointF(c + g, c + g))
                p.drawLine(QPointF(c + g, c - g), QPointF(c - g, c + g))
        p.end()


class _ObligationMeter(QWidget):
    """Hero shopping-list meter: the full width IS one obligation; the GOOD
    fill is the share already secured, the pale BAD-tinted remainder is what's
    missing. Unlike _SlotBar (module detail, need-proportional on a shared
    scale) every hero row is its own normalized meter — rows of different
    modules aren't in comparable units, so proportional widths would lie.
    Surplus is never drawn (see _SlotBar's history). Values update in place."""

    def __init__(self, height=8):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        self._need = 0.0
        self._have = 0.0
        self._display_frac = 0.0
        self._anim = None

    def set_values(self, need, have, start_frac=None, animate=True):
        self._need = max(0.0, float(need or 0.0))
        self._have = max(0.0, float(have or 0.0))
        target = (max(0.0, min(1.0, self._have / self._need))
                  if self._need > 0 else 0.0)
        if self._anim is not None:
            try:
                self._anim.stop()
            except Exception:
                pass
            self._anim = None
        if start_frac is None:
            start_frac = self._display_frac
        start_frac = max(0.0, min(1.0, float(start_frac)))
        if not animate or abs(target - start_frac) < 0.002:
            self._display_frac = target
            self.update()
            return
        self._display_frac = start_frac
        try:
            from qgis.PyQt.QtCore import QVariantAnimation, QEasingCurve
            anim = QVariantAnimation(self)
            anim.setDuration(520)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            anim.setStartValue(start_frac)
            anim.setEndValue(target)
            anim.valueChanged.connect(self._set_display_fraction)
            anim.finished.connect(lambda: self._set_display_fraction(target))
            anim.start()
            self._anim = anim
        except Exception:
            self._display_frac = target
            self.update()

    def _set_display_fraction(self, value):
        self._display_frac = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter, QPen
        from qgis.PyQt.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width()); h = float(self.height()); r = h / 2.0
        if self._need <= 0 or w <= 2:
            p.end(); return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BAD_BG))
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        edge = QColor(theme.BAD); edge.setAlpha(80)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), r, r)
        p.setPen(Qt.PenStyle.NoPen)
        fw = self._display_frac * w
        if fw > 0.5:
            p.setBrush(QColor(theme.GOOD))
            p.drawRoundedRect(QRectF(0, 0, max(fw, h), h), r, r)
        p.end()


class _ModuleGlyph(QWidget):
    """Crisp line icon for a module, rendered from inline SVG (Tabler-style) so
    it matches the rest of the UI and scales cleanly. Stroked in the module
    colour: a map polygon (area), a sprout (hedgerow), ripples (watercourse)."""
    _PATHS = {
        "area":  '<path d="M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2z"/>'
                 '<path d="M9 3v16"/><path d="M15 5v16"/>',
        "hedge": '<path d="M12 21v-9"/>'
                 '<path d="M12 13c-3 0-5-2-5-5 3 0 5 2 5 5z"/>'
                 '<path d="M12 11c0-3 2-5 5-5 0 3-2 5-5 5z"/>',
        "water": '<path d="M3 8q3-3 6 0t6 0 6 0"/>'
                 '<path d="M3 14q3-3 6 0t6 0 6 0"/>',
    }
    def __init__(self, kind, color, size=15):
        super().__init__()
        self.setFixedSize(size, size)
        self._kind = kind
        self._svg = b""
        self.set_color(color)

    def set_color(self, color):
        """Re-tint in place (dashboard tiles persist across theme flips)."""
        body = self._PATHS.get(self._kind, self._PATHS["area"])
        self._svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            'fill="none" stroke="%s" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">%s</svg>'
            % (color, body)).encode("utf-8")
        self.update()

    def paintEvent(self, _e):
        from qgis.PyQt.QtSvg import QSvgRenderer
        from qgis.PyQt.QtGui import QPainter
        from qgis.PyQt.QtCore import QByteArray, QRectF
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(QByteArray(self._svg)).render(
            p, QRectF(0, 0, float(self.width()), float(self.height())))
        p.end()


_ICON_CACHE = {}


def _screen_dpr():
    """The real device pixel ratio of the primary screen, so rasterized icons
    are rendered at native resolution instead of a guessed supersample factor
    that Qt then has to resize (softening/roughening thin strokes)."""
    try:
        from qgis.PyQt.QtWidgets import QApplication
        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
        if screen:
            return max(1.0, screen.devicePixelRatio())
    except Exception:
        pass
    return 1.0


def _svg_pixmap(name, color, size=18):
    """Tinted QPixmap of icons/<name>.svg (a Tabler outline glyph). The SVG's
    `currentColor` is swapped for `color`; rendered at the screen's native
    device pixel ratio + DPR-tagged so the stroke stays crisp."""
    import os
    from qgis.PyQt.QtGui import QPixmap, QPainter
    from qgis.PyQt.QtCore import QByteArray, QRectF
    from qgis.PyQt.QtSvg import QSvgRenderer
    path = os.path.join(os.path.dirname(__file__), "icons", name + ".svg")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return QPixmap()
    data = QByteArray(raw.replace("currentColor", color).encode("utf-8"))
    dpr = _screen_dpr()
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(data).render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _svg_icon(name, color="#1A2420", size=18, on_color=None):
    """QIcon from icons/<name>.svg tinted to `color`; if `on_color` is given the
    icon's On state (checked buttons) renders in that colour. Cached."""
    key = (name, color, size, on_color, _screen_dpr())
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    from qgis.PyQt.QtGui import QIcon
    icon = QIcon(_svg_pixmap(name, color, size))
    if on_color:
        icon.addPixmap(_svg_pixmap(name, on_color, size), QIcon.Mode.Normal, QIcon.State.On)
    _ICON_CACHE[key] = icon
    return icon


def _dot_pixmap(color, size=10):
    """A filled round dot in `color` (the module marker on constraint rows),
    painted so it's reliably circular at small sizes."""
    from qgis.PyQt.QtGui import QPixmap, QPainter, QColor
    dpr = _screen_dpr()
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, px, px)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _banner(name, html, *, fg, bg, icon_color=None, size=16, font_px=12,
            weight="400", radius=8, margins=(12, 10, 14, 10), spacing=9,
            border="none"):
    """Styled hint / status row: a tinted SVG icon beside a rich-text label in
    a rounded, filled card. Drop-in replacement for the emoji-prefixed QLabel
    banners (💡 ℹ️ ⚠️ ✅) so they use the icon set instead of pictographs."""
    from qgis.PyQt.QtWidgets import QWidget, QHBoxLayout, QLabel
    w = QWidget()
    w.setStyleSheet(
        f"QWidget {{ background:{bg}; border:{border}; border-radius:{radius}px; }}")
    h = QHBoxLayout(w)
    h.setContentsMargins(*margins)
    h.setSpacing(spacing)
    ico = QLabel()
    ico.setFixedSize(size, size)
    ico.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    ico.setStyleSheet("background:transparent; border:none;")
    ico.setPixmap(_svg_pixmap(name, icon_color or fg, size))
    lbl = QLabel(html)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        f"color:{fg}; font-size:{font_px}px; font-weight:{weight};"
        f" background:transparent; border:none; font-family:{theme.FONT};")
    h.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
    h.addWidget(lbl, 1)
    return w


# ── Habitat quick-list visuals ────────────────────────────────────────────────
# Left-rail colour keyed to the BNG broad habitat group so a quick / recent row
# can be scanned by colour band. Non-area types fall back to a per-key colour.
_HAB_RAIL = {
    "Grassland": "#7DAE54", "Woodland": "#2E6B4F",
    "Heathland & Shrub": "#7E6CA8", "Wetland": "#B65BA6",
    "Lakes": "#2B9BB0", "Coastal & Intertidal": "#C9923C",
    "Sparsely Vegetated Land": "#A8A29A", "Cropland": "#C79A2E",
    "Urban": "#8A8F88", "Hedgerow": "#9A6700",
    "Watercourse": "#234A6E", "Individual Trees": "#6B4F2E",
}
_HAB_RAIL_BY_KEY = {"area": "#7DAE54", "hedgerow": "#9A6700",
                    "watercourse": "#234A6E", "trees": "#6B4F2E"}


def _rail_for(hab_type, habitat_key="area"):
    """Left-rail colour for a quick/recent row — from its broad group (preferred)
    or its habitat_key as a fallback."""
    return _HAB_RAIL.get(hab_type) or _HAB_RAIL_BY_KEY.get(habitat_key, "#8A8F88")


def _swatch_for(sub_type, hab_type=None, habitat_key="area"):
    """Map-symbol swatch colour for a habitat row — the sub-type's *actual*
    on-canvas fill from habitat_data, so the quick list reads like the map
    legend rather than an abstract accent. Falls back to the broad-group rail
    colour when the sub-type is unknown (e.g. a synthesised/legacy label)."""
    sub = (sub_type or "").strip()
    try:
        from .habitat_data import HABITAT_TYPES
        # Prefer the sub-type within its own broad group…
        grp = HABITAT_TYPES.get(hab_type)
        if isinstance(grp, dict):
            info = grp.get(sub)
            if isinstance(info, dict) and info.get("color"):
                return info["color"]
        # …then anywhere in the catalogue (labels are effectively unique).
        for _subs in HABITAT_TYPES.values():
            info = _subs.get(sub)
            if isinstance(info, dict) and info.get("color"):
                return info["color"]
    except Exception:
        pass
    return _rail_for(hab_type, habitat_key)


# Default "Quick Habitats" — the pinned favourites on the Baseline/Proposed hub.
# Each entry is (habitat_key, broad_group, sub_type); trees store their display
# label as the sub_type ("Medium Urban Tree"). The user can override this list
# via Biome settings → Quick Habitats (persisted in QSettings); these are the
# fallback when they haven't. Kept minimal — condition, prop_* and clip are all
# derived at build time exactly as a search hit would be.
DEFAULT_QUICK_HABITATS = [
    ("area",     "Urban",             "Vegetated garden"),
    ("area",     "Urban",             "Developed land; sealed surface"),
    ("area",     "Urban",             "Artificial unvegetated, unsealed surface"),
    ("area",     "Urban",             "Open mosaic habitats on previously developed land"),
    ("area",     "Grassland",         "Modified grassland"),
    ("area",     "Heathland & Shrub", "Bramble scrub"),
    ("area",     "Woodland",          "Lowland mixed deciduous woodland"),
    ("hedgerow", "Hedgerow",          "Native hedgerow"),
    ("hedgerow", "Hedgerow",          "Non-native and ornamental hedgerow"),
    ("trees",    "Individual Trees",  "Medium Urban Tree"),
    ("trees",    "Individual Trees",  "Large Urban Tree"),
]


def _darken(hexc, f=0.82):
    """Return `hexc` nudged toward black by factor f — used for chip hover states."""
    try:
        h = hexc.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))
    except Exception:
        return hexc


def _cond_chip(condition):
    """Return (text, fg, bg) for a condition pill, or None when there is nothing
    worth showing. Maps the metric's grades onto calm traffic-light fills."""
    c = (condition or "").strip()
    if not c:
        return None
    low = c.lower()
    if low.startswith("good") or low.startswith("fairly good"):
        return ("Good", theme.GOOD, theme.GOOD_BG)
    if low.startswith("moderate"):
        return ("Moderate", theme.WARN, theme.WARN_BG)
    if low.startswith("poor") or low.startswith("fairly poor"):
        return ("Poor", theme.BAD, theme.BAD_BG)
    if "n/a" in low or low.startswith("condition assessment"):
        return ("N/A", theme.FAINT, theme.SUNKEN)
    return (c, theme.MUTED, theme.SUNKEN)


class _FlowLayout(QLayout):
    """A left-to-right wrapping layout: children flow across the full available
    width and wrap to the next line.

    Its minimumSize is the WIDEST single child, never the sum — which is the
    whole point in the dock: a row of chips laid out with QHBoxLayout pins a
    minimum width equal to all of them side by side, and anything wider than
    the dock is then silently clipped off the right edge (the panel's scroll
    areas have their horizontal scrollbar off). Flowed, the same chips wrap
    instead."""

    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()      # rows are rebuilt in place (dashboard refresh):
                               # without this the cached height-for-width from
                               # the previous set of chips survives.

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        if not 0 <= i < len(self._items):
            return None
        item = self._items.pop(i)
        self.invalidate()
        return item

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_height = rect.x(), rect.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


def _flow_row(spacing=6):
    """(widget, flow layout) for a wrapping row of small items. The size policy
    must opt into height-for-width or the parent QVBoxLayout gives the widget
    one line's height and the wrapped rows spill out of it."""
    w = QWidget()
    lay = _FlowLayout(w, margin=0, spacing=spacing)
    pol = w.sizePolicy()
    pol.setHeightForWidth(True)
    w.setSizePolicy(pol)
    return w, lay


class _ElidingLabel(QLabel):
    """A QLabel that elides its text to the available width so a long habitat
    name never widens or wraps a quick-list row."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full = text or ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        self._full = text or ""
        self._relabel()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relabel()

    def _relabel(self):
        from qgis.PyQt.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))


