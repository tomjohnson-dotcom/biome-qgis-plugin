# theme.py — Biome visual identity ("moss & chalk").
#
# Single source of truth for the plugin's look: design tokens, the dock-level
# stylesheet, an SVG icon loader, and small UI helpers (chips, skeletons,
# screen-fade transitions). Widgets should take colors from here rather than
# hardcoding hex values.
#
# THEMING (QGIS 4): the design tokens below are LIVE module globals driven by a
# light/dark palette. Call-sites read them dynamically (``theme.INK``), so
# ``set_mode("dark")`` reassigns every global and any QSS rebuilt afterwards
# comes out dark. Register a callback with ``add_theme_listener`` to re-apply a
# widget's stylesheet when the mode flips. On QGIS 3 the mode is simply always
# light unless set explicitly.

import os

from qgis.PyQt.QtCore import Qt, QRectF, QByteArray, QPropertyAnimation, QEasingCurve
from qgis.PyQt.QtGui import QIcon, QPixmap, QPainter
from qgis.PyQt.QtSvg import QSvgRenderer
from qgis.PyQt.QtWidgets import QLabel, QFrame, QGraphicsOpacityEffect


# ── Palettes ─────────────────────────────────────────────────────────────────
# The token NAMES are identical across both palettes; only the values differ.
# Every name here becomes a live module global (see _set_palette).

_LIGHT = dict(
    # Text
    INK           = "#1A2420",   # primary text — deep pine, near-black
    MUTED         = "#54635A",   # secondary text
    FAINT         = "#717E75",   # tertiary text, placeholders
    # Surfaces
    SURFACE       = "#F7F8F6",   # panel background — warm chalk
    CARD          = "#FFFFFF",   # cards sit on SURFACE
    SUNKEN        = "#F1F4F0",   # wells, inactive tabs
    # Lines
    BORDER        = "#E3E7E1",   # hairline
    BORDER_STRONG = "#C9D1C9",   # emphasised hairline
    # Accent — moss green: primary actions and active states
    ACCENT        = "#2E6B4F",
    ACCENT_DARK   = "#1F4A37",
    ACCENT_SOFT   = "#EAF2EC",   # hover / selected fills
    # Header
    HEADER        = "#1C2E25",   # flat deep pine (button pressed/hover)
    HEADER_SOFT   = "#2A3D33",   # header strip + tab backing
    LICHEN        = "#9CC79B",   # 2px accent line under the header
    # Still-banner (scene-off plain header) fills
    BANNER_TOP    = "#24332A",
    BANNER_GROUND = "#1D2A22",
    # Semantics (calmer than material defaults)
    GOOD = "#1F7A4D", GOOD_BG = "#E9F4EC",
    BAD  = "#B3372F", BAD_BG  = "#FBEDEB",
    WARN = "#9A6700", WARN_BG = "#FBF4E4",
    INFO = "#2F5D8A", INFO_BG = "#EBF2F8",
    # Dashboard identities: hedgerow module accent; area-split retained (a
    # deliberate low-chroma sand — "nothing happened here") and created blue.
    # Order in the split bar is Retained/Enhanced/Created/Lost so GOOD green
    # and BAD red never sit adjacent (deutan ΔE 5.4 when they did).
    MOD_HEDGE      = "#8A5A2B",
    SPLIT_RETAINED = "#9C8A62",
    SPLIT_CREATED  = "#185FA5",
)

# "Moss & Midnight" — the dark palette. Text goes to warm off-white, surfaces to
# deep pine near-black, the accent brightens a step so it reads on dark, and the
# semantic fills invert to dim tints of their hue.
_DARK = dict(
    INK           = "#E7EDE7",
    MUTED         = "#AEBAB2",
    FAINT         = "#93A29A",   # kept bright enough to read on CARD
    SURFACE       = "#131A16",
    CARD          = "#1B241F",
    SUNKEN        = "#151E19",   # wells a step darker than CARD, not black
    BORDER        = "#2E3A33",
    BORDER_STRONG = "#46554C",
    ACCENT        = "#4E9B74",
    ACCENT_DARK   = "#58A67F",   # doubles as text-on-soft-fill → must stay light
    ACCENT_SOFT   = "#25402F",   # soft moss hover / selected fill
    HEADER        = "#0E1712",
    HEADER_SOFT   = "#16211B",
    LICHEN        = "#6FA96E",
    BANNER_TOP    = "#1A241E",
    BANNER_GROUND = "#131C16",
    GOOD = "#5FC88C", GOOD_BG = "#16281E",
    BAD  = "#E8756B", BAD_BG  = "#2E1714",
    WARN = "#E0A83C", WARN_BG = "#2A2110",
    INFO = "#6BA8D8", INFO_BG = "#14212E",
    MOD_HEDGE      = "#C89A62",
    SPLIT_RETAINED = "#B3A379",
    SPLIT_CREATED  = "#5E9BD0",
)

RADIUS = 8

_mode = "light"                 # "light" | "dark"
_theme_listeners = []           # callables invoked (no args) after a mode flip


def _set_palette(d):
    """Push a palette dict onto the module globals so ``theme.NAME`` reads it."""
    globals().update(d)


# Token names declared for static analysis / greppability; the real values are
# assigned (and reassigned on a mode flip) by _set_palette from the dicts above.
INK = MUTED = FAINT = SURFACE = CARD = SUNKEN = BORDER = BORDER_STRONG = \
    ACCENT = ACCENT_DARK = ACCENT_SOFT = HEADER = HEADER_SOFT = LICHEN = \
    BANNER_TOP = BANNER_GROUND = GOOD = GOOD_BG = BAD = BAD_BG = WARN = \
    WARN_BG = INFO = INFO_BG = MOD_HEDGE = SPLIT_RETAINED = SPLIT_CREATED = ""

# Prime the light palette at import so every existing ``theme.INK`` works and
# nothing depends on a caller invoking set_mode first.
_set_palette(_LIGHT)


def current_mode():
    return _mode


def is_dark():
    return _mode == "dark"


def set_mode(mode):
    """Switch the active palette. ``mode`` is "light" or "dark". Reassigns every
    token global and notifies listeners (which re-apply their stylesheets).
    Returns True if the mode actually changed."""
    global _mode
    mode = "dark" if str(mode).lower() == "dark" else "light"
    if mode == _mode:
        return False
    _mode = mode
    _set_palette(_DARK if mode == "dark" else _LIGHT)
    _tlog("set_mode(%s): palette swapped, %d listeners" % (
        mode, len(_theme_listeners)))
    try:
        _clear_icon_cache()   # default-colour icons re-tint to the new INK
    except Exception:
        pass
    for cb in list(_theme_listeners):
        t0 = _time.perf_counter()
        try:
            cb()
        except Exception:
            pass
        name = getattr(cb, "__qualname__", None) or repr(cb)
        _tlog("listener %s took %.1fms" % (
            name, (_time.perf_counter() - t0) * 1000.0))
    _tlog("set_mode(%s) COMPLETE" % mode)
    return True


def add_theme_listener(cb):
    """Register a no-arg callback fired after each mode flip (to re-apply QSS).
    Safe against duplicates so reloads don't stack callbacks."""
    if cb not in _theme_listeners:
        _theme_listeners.append(cb)


def remove_theme_listener(cb):
    try:
        _theme_listeners.remove(cb)
    except ValueError:
        pass


# ── TEMP perf instrumentation (dark-mode flip tuning; remove at sign-off) ────
import time as _time

_PERF_T0 = None


def _tlog(msg, reset=False):
    """Append a timestamped perf line to biome_live.log. Delta is measured from
    the start of the current flip (reset=True stamps t0)."""
    global _PERF_T0
    try:
        import datetime
        now = _time.perf_counter()
        if reset or _PERF_T0 is None:
            _PERF_T0 = now
        delta = (now - _PERF_T0) * 1000.0
        path = os.path.join(os.path.dirname(__file__), "biome_live.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s PERF +%8.1fms  %s\n" % (
                datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], delta, msg))
    except Exception:
        pass


# ── Dark-mode restyler ───────────────────────────────────────────────────────
# The panel and dialogs carry hundreds of widget styles with the LIGHT palette
# hexes baked in as literals. Rather than rewrite every call site, this shim
# (same pattern as the tooltip enforcer below) remaps those literals to their
# dark equivalents on any Biome-owned widget: existing widgets via
# restyle_widget_tree(), future ones via an application Show-event filter.
# The original light stylesheet is kept on the widget so flipping back to
# light restores it exactly.
#
# Widgets whose colours must NEVER theme (habitat rail/swatch colours — they
# mirror the map legend) opt out with setProperty("biome_fixed_color", True).

# White is deliberately NOT remapped: #FFFFFF appears both as card fills and as
# text-on-accent, and remapping it would break the latter. (CARD-coloured
# fills authored via the token theme correctly already.)
_REMAP_EXCLUDE = {"#FFFFFF"}

_DARK_RESTYLER = None


def _light_to_dark_pairs():
    pairs = []
    for name, lv in _LIGHT.items():
        dv = _DARK.get(name)
        if (isinstance(lv, str) and lv.startswith("#")
                and dv and lv.upper() != str(dv).upper()
                and lv.upper() not in _REMAP_EXCLUDE):
            pairs.append((lv.upper(), dv))
    # Longest first so e.g. 8-digit values never partially match 6-digit ones.
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


# Ad-hoc light neutrals used across the panel that aren't palette tokens —
# mostly hover tints and off-whites. Curated (NOT generated): bright hexes like
# selection yellow / pink are map colours and must never be remapped.
_EXTRA_DARK_TINTS = {
    "#F4F6F2": "#18211C",   # off-white panel tint
    "#F9FAF8": "#161E19",   # near-white card tint
    "#F3F7F2": "#232F28",   # hover tint on white cards
    "#EEF1ED": "#2A352E",   # hairline divider tint
    "#F0F2EE": "#0E1511",   # gauge/bar track tint
    "#FBF6E6": "#2A2110",   # warm cream rows → dark warn tint
}


def _all_remap_pairs():
    pairs = _light_to_dark_pairs()
    pairs.extend((k.upper(), v) for k, v in _EXTRA_DARK_TINTS.items())
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


_REMAP_PAIRS = _all_remap_pairs()


_WHITE_BG_RE = None
_BG_HEX_RE = None
_BORDER_HEX_RE = None
_TEXT_HEX_RE = None


def _hex_lightness(h):
    """Perceived lightness 0..1 of a #RRGGBB hex."""
    try:
        r = int(h[1:3], 16) / 255.0
        g = int(h[3:5], 16) / 255.0
        b = int(h[5:7], 16) / 255.0
        return 0.299 * r + 0.587 * g + 0.114 * b
    except Exception:
        return 0.0


def _dark_equiv(h):
    """A dark fill with the same hue as a light tint — for ad-hoc hover /
    well colours that aren't palette tokens. Keeps a whisper of the hue so
    green-tinted hovers stay green-tinted, at panel-dark lightness."""
    import colorsys
    try:
        r = int(h[1:3], 16) / 255.0
        g = int(h[3:5], 16) / 255.0
        b = int(h[5:7], 16) / 255.0
        hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
        r2, g2, b2 = colorsys.hls_to_rgb(hh, 0.14, min(ss, 0.28))
        return "#%02X%02X%02X" % (round(r2 * 255), round(g2 * 255), round(b2 * 255))
    except Exception:
        return _DARK["CARD"]


def _light_equiv(h):
    """A light text colour with the same hue as an ad-hoc dark ink — the
    symmetric partner of _dark_equiv, so dark-pine section headings become
    pale sage instead of vanishing on the darkened strips."""
    import colorsys
    try:
        r = int(h[1:3], 16) / 255.0
        g = int(h[3:5], 16) / 255.0
        b = int(h[5:7], 16) / 255.0
        hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
        r2, g2, b2 = colorsys.hls_to_rgb(hh, 0.84, min(ss, 0.30))
        return "#%02X%02X%02X" % (round(r2 * 255), round(g2 * 255), round(b2 * 255))
    except Exception:
        return _DARK["INK"]


def _compile_res():
    global _WHITE_BG_RE, _BG_HEX_RE, _BORDER_HEX_RE, _TEXT_HEX_RE
    import re
    if _WHITE_BG_RE is None:
        _WHITE_BG_RE = re.compile(
            r"((?:background|background-color|selection-background-color|"
            r"alternate-background-color)\s*:\s*)(#FFFFFF\b|#FFF\b|white\b)",
            re.IGNORECASE)
    if _BG_HEX_RE is None:
        _BG_HEX_RE = re.compile(
            r"((?:background|background-color|selection-background-color|"
            r"alternate-background-color)\s*:\s*)(#[0-9A-Fa-f]{6})\b",
            re.IGNORECASE)
    if _BORDER_HEX_RE is None:
        # colour anywhere inside a border/border-*-color declaration
        _BORDER_HEX_RE = re.compile(
            r"(border[a-z-]*\s*:\s*[^;{}]*?)(#[0-9A-Fa-f]{6})\b",
            re.IGNORECASE)
    if _TEXT_HEX_RE is None:
        # a bare `color:` declaration — the lookbehind excludes background-color,
        # border-color, selection-color etc., which have their own rules
        _TEXT_HEX_RE = re.compile(
            r"((?<![-\w])color\s*:\s*)(#[0-9A-Fa-f]{6})\b",
            re.IGNORECASE)


# Distinct stylesheet strings are few (rows/buttons share styles), so memoise
# the remap — the regex passes then run once per unique string, not per widget.
_REMAP_CACHE = {}


def _remap_qss_to_dark(qss):
    """Swap light-palette hex literals in a stylesheet for their dark values.

    Three passes:
    1. Known palette + curated tints (any position — includes text colours).
    2. White backgrounds → dark CARD. White is remapped ONLY as a background:
       white cards must go dark, but white TEXT on accent fills stays white —
       the property name is the disambiguator.
    3. Generic catcher: any REMAINING light colour (lightness > 0.78) in a
       background position — ad-hoc hover tints, wells — becomes a computed
       same-hue dark fill, and light borders become the dark hairline. This is
       what keeps hovered rows readable without enumerating every tint.
    """
    import re
    cached = _REMAP_CACHE.get(qss)
    if cached is not None:
        return cached
    out = qss
    for light, dark in _REMAP_PAIRS:
        out = re.sub(re.escape(light), dark, out, flags=re.IGNORECASE)
    _compile_res()
    out = _WHITE_BG_RE.sub(lambda m: m.group(1) + _DARK["CARD"], out)
    out = _BG_HEX_RE.sub(
        lambda m: (m.group(1) + _dark_equiv(m.group(2))
                   if _hex_lightness(m.group(2)) > 0.78 else m.group(0)), out)
    out = _BORDER_HEX_RE.sub(
        lambda m: (m.group(1) + _DARK["BORDER"]
                   if _hex_lightness(m.group(2)) > 0.72 else m.group(0)), out)
    # 4. Ad-hoc dark text (section headings etc.) → same-hue light ink, so it
    #    stays readable on the fills darkened above.
    out = _TEXT_HEX_RE.sub(
        lambda m: (m.group(1) + _light_equiv(m.group(2))
                   if _hex_lightness(m.group(2)) < 0.30 else m.group(0)), out)
    _REMAP_CACHE[qss] = out
    return out


def _qss_has_white_bg(qss):
    _compile_res()
    if _WHITE_BG_RE.search(qss):
        return True
    m = _BG_HEX_RE.search(qss)
    while m:
        if _hex_lightness(m.group(2)) > 0.78:
            return True
        m = _BG_HEX_RE.search(qss, m.end())
    m = _BORDER_HEX_RE.search(qss)
    while m:
        if _hex_lightness(m.group(2)) > 0.72:
            return True
        m = _BORDER_HEX_RE.search(qss, m.end())
    m = _TEXT_HEX_RE.search(qss)
    while m:
        if _hex_lightness(m.group(2)) < 0.30:
            return True
        m = _TEXT_HEX_RE.search(qss, m.end())
    return False


_DETECT_CACHE = {}


def _qss_has_light_hex(qss):
    hit = _DETECT_CACHE.get(qss)
    if hit is not None:
        return hit
    up = qss.upper()
    hit = (any(light in up for light, _ in _REMAP_PAIRS)
           or _qss_has_white_bg(qss))
    _DETECT_CACHE[qss] = hit
    return hit


def _is_biome_widget(w):
    """True if the widget or any ancestor is a Biome-defined widget (module
    name starts with the plugin package) — scopes the restyler so QGIS's own
    widgets are never touched."""
    seen = 0
    while w is not None and seen < 30:
        mod = type(w).__module__ or ""
        if "biome" in mod.split(".")[0] or mod.startswith("biome"):
            return True
        w = w.parentWidget() if hasattr(w, "parentWidget") else None
        seen += 1
    return False


def restyle_widget(w):
    """Apply (dark) or restore (light) the palette remap on one widget."""
    try:
        if w.property("biome_fixed_color"):
            return
        if is_dark():
            orig = w.property("_biome_light_qss")
            src = orig if orig else (w.styleSheet() or "")
            if src and _qss_has_light_hex(src):
                if not orig:
                    w.setProperty("_biome_light_qss", src)
                w.setStyleSheet(_remap_qss_to_dark(src))
        else:
            orig = w.property("_biome_light_qss")
            if orig:
                w.setStyleSheet(orig)
                w.setProperty("_biome_light_qss", None)
    except Exception:
        pass


def restyle_widget_tree(root):
    """Run the palette remap over a widget and all its descendants. Call after
    a mode flip on long-lived roots (the dock panel, open dialogs).

    Painting is suspended for the walk so the hundreds of individual
    setStyleSheet calls coalesce into one repaint instead of a visible (and
    slow) cascade."""
    from qgis.PyQt.QtWidgets import QWidget
    try:
        was_enabled = root.updatesEnabled()
        root.setUpdatesEnabled(False)
        try:
            restyle_widget(root)
            for w in root.findChildren(QWidget):
                restyle_widget(w)
        finally:
            root.setUpdatesEnabled(was_enabled)
            root.update()
    except Exception:
        pass


def install_dark_restyler():
    """Install (once, reload-safe) an app event filter that dark-remaps every
    Biome widget's stylesheet as it first shows. Complements
    restyle_widget_tree, which handles widgets already on screen at flip time."""
    global _DARK_RESTYLER
    from qgis.PyQt.QtCore import QObject, QEvent
    from qgis.PyQt.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if app is None:
        return

    class _Restyler(QObject):
        def eventFilter(self, obj, ev):
            try:
                if (ev.type() == QEvent.Type.Show and is_dark()
                        and isinstance(obj, QWidget)
                        and not obj.property("_biome_light_qss")
                        and (obj.styleSheet() or "")
                        and _qss_has_light_hex(obj.styleSheet())
                        and _is_biome_widget(obj)):
                    restyle_widget(obj)
            except Exception:
                pass
            return False

    for old in app.findChildren(QObject, "biome_dark_restyler"):
        try:
            app.removeEventFilter(old)
            old.deleteLater()
        except Exception:
            pass
    _DARK_RESTYLER = _Restyler(app)
    _DARK_RESTYLER.setObjectName("biome_dark_restyler")
    app.installEventFilter(_DARK_RESTYLER)


# ── OS colour-scheme integration (QGIS 4 / Qt 6.5+) ──────────────────────────
# QGIS 4 follows the operating system's light/dark preference with no in-app
# opt-out, so Biome mirrors it. On QGIS 3 (Qt 5) the colorScheme API is absent
# and these are no-ops, leaving the plugin light.

_scheme_hook = None
_scheme_timer = None

# User preference: "auto" (follow the OS; light on Qt5), or a pinned
# "light"/"dark". Pinned modes work on QGIS 3 too — only auto-detection
# needs the Qt 6.5 colorScheme API.
_PREF_KEY = "biome/theme_mode"


def theme_preference():
    try:
        from qgis.core import QgsSettings
        v = (QgsSettings().value(_PREF_KEY, "auto", type=str) or "auto").lower()
        return v if v in ("auto", "light", "dark") else "auto"
    except Exception:
        return "auto"


def set_theme_preference(pref):
    """Persist the Auto/Light/Dark choice and apply it immediately."""
    pref = str(pref).lower()
    if pref not in ("auto", "light", "dark"):
        pref = "auto"
    try:
        from qgis.core import QgsSettings
        QgsSettings().setValue(_PREF_KEY, pref)
    except Exception:
        pass
    return apply_preference()


def resolved_mode():
    pref = theme_preference()
    if pref in ("light", "dark"):
        return pref
    return "dark" if os_is_dark() else "light"


def apply_preference():
    """Set the mode from the preference (or OS when on auto). Returns True if
    the mode actually changed."""
    return set_mode(resolved_mode())


def os_is_dark():
    """True if QGIS is currently in a dark scheme.

    Primary signal is Qt 6.5+'s colorScheme(); but during startup (when the
    plugin first checks) it can still read Unknown before QGIS applies its dark
    theme, so we fall back to the ACTUAL application window colour — the most
    reliable "is the app dark right now" test, and what makes this work when
    QGIS launched already-dark. On Qt 5 both are absent → False (stays light).
    """
    try:
        from qgis.PyQt.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()          # AttributeError on Qt5
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
        # scheme == Unknown → fall through to the palette test
    except Exception:
        pass
    try:
        from qgis.PyQt.QtWidgets import QApplication
        from qgis.PyQt.QtGui import QPalette
        app = QApplication.instance()
        if app is not None:
            return app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:
        pass
    return False


def sync_to_os():
    """Re-resolve the mode (honouring a pinned preference; OS only on auto).
    Kept under its historical name — timers and the scheme hook call it."""
    return apply_preference()


def install_os_scheme_follow():
    """Apply the theme preference now, and (Qt 6.5+) subscribe to OS light/dark
    changes so auto mode flips live. Idempotent and reload-safe. On Qt 5 the
    preference still applies (a pinned dark works there); only the OS-follow
    signal is unavailable."""
    global _scheme_hook
    try:
        apply_preference()
    except Exception:
        pass
    try:
        from qgis.PyQt.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        if not hasattr(hints, "colorSchemeChanged"):
            return
        if _scheme_hook is not None:
            try:
                hints.colorSchemeChanged.disconnect(_scheme_hook)
            except Exception:
                pass
        def _on_scheme(_scheme):
            # NEVER react synchronously: this signal fires inside Qt's own
            # scheme-change cascade (palette propagation, style rebuild across
            # every widget), and re-styling the panel / re-applying the app
            # palette mid-cascade hard-crashed QGIS. Debounce well past the
            # cascade and run from the event loop instead.
            global _scheme_timer
            try:
                from qgis.PyQt.QtCore import QTimer
                first = _scheme_timer is None or not _scheme_timer.isActive()
                _tlog("colorSchemeChanged received (scheme=%s)" % _scheme,
                      reset=first)
                if _scheme_timer is None:
                    _scheme_timer = QTimer()
                    _scheme_timer.setSingleShot(True)
                    def _fire():
                        _tlog("debounce fired -> sync_to_os")
                        sync_to_os()
                    _scheme_timer.timeout.connect(_fire)
                _scheme_timer.start(600)   # restarts on repeat fires
            except Exception:
                pass
        hints.colorSchemeChanged.connect(_on_scheme)
        _scheme_hook = _on_scheme
        sync_to_os()
        # QGIS applies its own dark theme DURING startup, so an initial check in
        # initGui can read light before the app is actually dark — and if QGIS
        # launched already-dark, colorSchemeChanged never fires (no change). So
        # re-sync a few times as startup settles; the panel's theme listener
        # re-applies if the mode flips. One-shot, startup only.
        try:
            from qgis.PyQt.QtCore import QTimer
            for delay in (300, 1200, 2500):
                QTimer.singleShot(delay, sync_to_os)
        except Exception:
            pass
    except Exception:
        pass

# Bundled Inter (OFL) — loaded once into Qt's font registry so the panel
# looks identical on every machine instead of inheriting whatever sans the
# OS provides. Four weights are registered as distinct families because
# Qt5 does not reliably resolve weight variations within one family.
_FONTS_LOADED = False


_VARIABLE_FONT_FAMILY = None   # set if Inter Variable loaded (Qt 6.7+)


def _load_fonts():
    global _FONTS_LOADED, _VARIABLE_FONT_FAMILY
    if _FONTS_LOADED:
        return
    from qgis.PyQt.QtGui import QFontDatabase
    font_dir = os.path.join(os.path.dirname(__file__), "fonts")
    for fname in ("Inter-Regular.ttf", "Inter-Medium.ttf",
                  "Inter-SemiBold.ttf", "Inter-Bold.ttf"):
        path = os.path.join(font_dir, fname)
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
    # Optional: a variable Inter cut (InterVariable.ttf) gives continuous weight
    # /optical-size control on Qt 6.7+. Falls back silently to the static cuts.
    for vfname in ("InterVariable.ttf", "Inter-Variable.ttf"):
        vpath = os.path.join(font_dir, vfname)
        if os.path.exists(vpath):
            fid = QFontDatabase.addApplicationFont(vpath)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            if fams:
                _VARIABLE_FONT_FAMILY = fams[0]
            break
    _FONTS_LOADED = True


def numeric_font(size=13, weight=None, family=None, wght=None):
    """A QFont with OpenType tabular figures (``tnum``) enabled so digits are
    fixed-width and columns of numbers align. Requires Qt 6.7+ for the feature
    call; on Qt 5 it degrades to a plain QFont (proportional figures).

    Uses the Inter Variable family when it's loaded, so ``wght`` (a continuous
    weight axis value, e.g. 460) renders precisely; otherwise falls back to
    static Inter with the nearest ``weight``.

    Use on widgets that stack numbers — gauges, unit totals, metric/credits
    tables — via ``widget.setFont(theme.numeric_font(...))``. (QSS cannot set
    font features, so this must be applied to the QFont object directly.)
    """
    from qgis.PyQt.QtGui import QFont
    _load_fonts()
    fam = family or _VARIABLE_FONT_FAMILY or "Inter"
    f = QFont(fam)
    f.setPixelSize(int(size))
    if wght is not None and _VARIABLE_FONT_FAMILY and not family:
        try:
            f.setVariableAxis(QFont.Tag("wght"), float(wght))
        except Exception:
            if weight is not None:
                f.setWeight(weight)
    elif weight is not None:
        f.setWeight(weight)
    try:
        # Qt 6.7+: type-safe feature tag. tnum = tabular (mono-width) figures.
        f.setFeature(QFont.Tag("tnum"), 1)
    except Exception:
        pass  # Qt 5 / older PyQt6 — proportional figures, still renders fine.
    return f


def variable_font(size=13, wght=400, family=None):
    """A QFont using the loaded Inter Variable family at an arbitrary weight
    axis value (Qt 6.7+). Falls back to a plain sized Inter QFont if no variable
    font is loaded or the axis API is missing."""
    from qgis.PyQt.QtGui import QFont
    _load_fonts()
    fam = family or _VARIABLE_FONT_FAMILY or "Inter"
    f = QFont(fam)
    f.setPixelSize(int(size))
    try:
        f.setVariableAxis(QFont.Tag("wght"), float(wght))
    except Exception:
        f.setWeight(QFont.Weight.Normal if wght < 550 else QFont.Weight.DemiBold)
    return f


def device_pixel_ratio(widget=None):
    """The device pixel ratio to render crisp pixmaps at. Qt 6 passes fractional
    Windows scaling (125%%→1.25) through, so caches must render at the real DPR,
    not a rounded 2x, to stay sharp. Falls back to 2.0 when no widget/app."""
    try:
        if widget is not None and hasattr(widget, "devicePixelRatioF"):
            return max(1.0, float(widget.devicePixelRatioF()))
        from qgis.PyQt.QtWidgets import QApplication
        scr = QApplication.primaryScreen()
        if scr is not None:
            return max(1.0, float(scr.devicePixelRatio()))
    except Exception:
        pass
    return 2.0


# Font families. Inter falls back to the platform UI sans if the bundled
# files ever fail to load, so text always renders.
#
# Body text (weight 400) renders from Inter Variable. The weighted tiers keep
# their static cut FIRST because many QSS rules set weight via the family name
# alone (no font-weight): Qt always picks the first EXISTING family, and
# 'Inter Variable' with no weight would render at 400 — flattening medium /
# semibold to regular. The static cuts carry their weight in the glyphs, so
# leading with them guarantees the right weight everywhere. (Inter static and
# Inter Variable are the same typeface at matched weights, so this is seamless;
# the variable file is used for body text and is available via
# theme.variable_font()/numeric_font() for any bespoke weight.)
_FALLBACK = "'Segoe UI','SF Pro Text','Ubuntu','Helvetica Neue',sans-serif"
FONT          = f"'Inter Variable','Inter',{_FALLBACK}"          # body, 400
FONT_MEDIUM   = f"'Inter Medium','Inter Variable',{_FALLBACK}"   # 500 (glyph weight)
FONT_SEMIBOLD = f"'Inter SemiBold','Inter Variable',{_FALLBACK}" # 600 (glyph weight)
FONT_STACK    = FONT  # backwards-compatible alias

# Base type sizes — bumped one step from the first cut for legibility.
SIZE_BODY  = 13
SIZE_LABEL = 11
SIZE_VALUE = 19

_ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")


# ── Dock-level stylesheet ────────────────────────────────────────────────────

def style_tooltips():
    """Make tooltips readable across every Qt styling path.

    Tooltips are top-level ``QTipLabel`` windows and are stubborn to colour:

    * Native style (no app stylesheet): painted from the *palette*, so we set
      ToolTipBase / ToolTipText on the shared QToolTip palette below.
    * App stylesheet active (Biome mode -> QStyleSheetStyle): Qt resolves the
      tip's colours itself and IGNORES both QToolTip.setPalette() AND a
      ``QToolTip {}`` QSS rule — the tip picks up the light window colour and
      the white text becomes unreadable (verified in QGIS: ToolTipBase
      resolved to SURFACE). The lever that reliably wins is setting the colour
      directly on each QTipLabel as it is shown — see _install_tip_filter().

    So the palette here and the QToolTip{} rules in dock_qss()/app_qss() are
    kept as cheap fallbacks for builds where they are honoured; the event
    filter is the actual enforcer. Safe to call whenever the dock is (re)built.
    """
    from qgis.PyQt.QtWidgets import QToolTip
    from qgis.PyQt.QtGui import QPalette, QColor
    pal = QToolTip.palette()
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(INK))   # opaque dark fill
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(CARD))  # white text
    QToolTip.setPalette(pal)
    _install_tip_filter()


# ── Tooltip enforcer ─────────────────────────────────────────────────────────
# When Biome mode's application stylesheet is live, QStyleSheetStyle paints each
# top-level QTipLabel from a palette it resolves to the light window colour,
# ignoring both QToolTip.setPalette() and any QToolTip{} QSS rule — so white
# tooltip text lands on a near-white fill and is unreadable. The reliable fix is
# to set the dark colours on each tip instance as it appears; an application
# event filter does exactly that. (Diagnosed in QGIS: without it the tip paints
# #f7f8f6; with it, #1a2420.)

_TIP_FILTER = None


def _install_tip_filter():
    """Install (once, reload-safe) an application event filter that paints every
    tooltip with the Biome dark palette as it is shown. This is what actually
    makes tooltips readable when an app stylesheet is active; see style_tooltips.
    """
    global _TIP_FILTER
    from qgis.PyQt.QtCore import QObject, QEvent
    from qgis.PyQt.QtWidgets import QApplication
    from qgis.PyQt.QtGui import QPalette, QColor

    app = QApplication.instance()
    if app is None:
        return

    css = "background-color:%s; color:%s; border:0px;" % (INK, CARD)

    class _TipFilter(QObject):
        def eventFilter(self, obj, ev):
            try:
                if (ev.type() == QEvent.Type.Show
                        and obj.metaObject().className() == "QTipLabel"
                        and obj.styleSheet() != css):
                    pal = QPalette(obj.palette())
                    for role in (QPalette.ColorRole.ToolTipBase, QPalette.ColorRole.Window,
                                 QPalette.ColorRole.Base, QPalette.ColorRole.Button):
                        pal.setColor(role, QColor(INK))
                    for role in (QPalette.ColorRole.ToolTipText, QPalette.ColorRole.WindowText,
                                 QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
                        pal.setColor(role, QColor(CARD))
                    obj.setPalette(pal)
                    obj.setAutoFillBackground(True)
                    obj.setStyleSheet(css)
            except Exception:
                pass
            return False

    # Reload-safe: a prior module load leaves its filter parented to the (long-
    # lived) app, where our reset module global can't see it. Find any previous
    # one by name and drop it so filters don't stack up across reloads.
    for old in app.findChildren(QObject, "biome_tip_filter"):
        try:
            app.removeEventFilter(old)
            old.deleteLater()
        except Exception:
            pass
    _TIP_FILTER = _TipFilter(app)
    _TIP_FILTER.setObjectName("biome_tip_filter")
    app.installEventFilter(_TIP_FILTER)


def _qss_icon(name, color):
    """Colour-baked copy of an icon SVG for use in QSS ``url()``.

    QSS image references are plain files — Qt won't substitute currentColor
    the way :func:`icon` does — so bake the colour into a copy under the
    user's temp dir (one file per icon+colour, so light and dark coexist)
    and return its forward-slash path.
    """
    import tempfile
    out = os.path.join(tempfile.gettempdir(),
                       f"biome_qss_{name}_{color.lstrip('#')}.svg")
    if not os.path.exists(out):
        try:
            with open(os.path.join(_ICON_DIR, name + ".svg"),
                      encoding="utf-8") as f:
                svg = f.read()
            with open(out, "w", encoding="utf-8") as f:
                f.write(svg.replace("currentColor", color))
        except OSError:
            return ""
    return out.replace("\\", "/")


def dock_qss():
    """Base stylesheet for the whole dock: typography, inputs, scrollbars.

    Sets calm defaults so individual widgets only need to declare what makes
    them different. Loads the bundled Inter family on first call.
    """
    _load_fonts()
    # MUTED matches the arrow colour the combo always had in light mode, and
    # follows the palette in dark mode (the raw SVGs use currentColor now).
    chev        = _qss_icon("chevron-down", MUTED)
    chev_up     = _qss_icon("chevron-up", MUTED)
    chev_dim    = _qss_icon("chevron-down", BORDER_STRONG)
    chev_up_dim = _qss_icon("chevron-up", BORDER_STRONG)
    check       = os.path.join(_ICON_DIR, "check.svg").replace("\\", "/")
    return f"""
QDockWidget, QWidget {{
    font-family: {FONT};
    font-size: {SIZE_BODY}px;
    color: {INK};
}}
QToolTip {{
    background-color: {INK}; color: {CARD};
    padding: 5px 9px; font-size: 12px;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {INK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox {{ padding-right: 24px; }}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 24px; border: none; background: transparent;
}}
QComboBox::down-arrow {{ image: url({chev}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {CARD}; border: 1px solid {BORDER_STRONG};
    border-radius: 8px; padding: 4px; outline: none;
    selection-background-color: {ACCENT_SOFT}; selection-color: {INK};
}}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 5px 8px; border-radius: 5px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; margin: 2px 2px 0 0;
    border: none; border-radius: 4px; background: transparent;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; margin: 0 2px 2px 0;
    border: none; border-radius: 4px; background: transparent;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {ACCENT_SOFT};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({chev_up}); width: 11px; height: 11px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({chev}); width: 11px; height: 11px;
}}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off,
QDoubleSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:off {{
    image: url({chev_up_dim}); width: 11px; height: 11px;
}}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off,
QDoubleSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:off {{
    image: url({chev_dim}); width: 11px; height: 11px;
}}
QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 8px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG}; border-radius: 4px; min-width: 32px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {CARD};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url({check});
}}
QCheckBox::indicator:disabled {{
    background: {SUNKEN};
    border-color: {BORDER};
}}
"""


# Reusable button styles — assign with setStyleSheet, or via object names if
# screens are later migrated to the shared stylesheet.

def btn_secondary():
    """Quiet bordered button — the default for tool grids and dialogs."""
    return f"""
QPushButton {{
    background: {CARD}; color: {INK};
    border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    font-family: {FONT_MEDIUM}; font-size: 12px; font-weight: 500;
    padding: 8px 8px; text-align: center;
}}
QPushButton:hover  {{ background: {ACCENT_SOFT}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {SUNKEN}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SUNKEN}; }}
"""


def btn_primary():
    """Filled accent button — one per screen, the main action."""
    return f"""
QPushButton {{
    background: {ACCENT}; color: white;
    border: none; border-radius: {RADIUS}px;
    font-family: {FONT_SEMIBOLD}; font-size: 13px; font-weight: 600;
    padding: 9px 14px;
}}
QPushButton:hover  {{ background: {ACCENT_DARK}; }}
QPushButton:pressed {{ background: {HEADER}; }}
QPushButton:disabled {{ background: {BORDER_STRONG}; }}
"""


def card_qss():
    """White card on the chalk surface, hairline border."""
    return (f"background: {CARD}; border: 1px solid {BORDER};"
            f" border-radius: {RADIUS}px;")


# ── Icons ────────────────────────────────────────────────────────────────────

_pixmap_cache = {}


def icon(name, color=None, size=18):
    """Load icons/<name>.svg, tint its strokes, return a crisp QIcon.

    SVGs use stroke/fill="currentColor"; rendering goes through QSvgRenderer at
    the real device pixel ratio for sharpness on fractional Windows scaling
    (Qt 6). ``color`` defaults to the current theme's INK so icons follow
    light/dark automatically when the caller passes no explicit colour.

    Qt 6.7+ renders SVG masks and filters (soft shadows, blurs) authored inside
    the .svg, so effects can live in the file itself.
    """
    if color is None:
        color = INK
    dpr = device_pixel_ratio()
    # Quantise DPR into the cache key so switching monitors re-renders but we
    # don't thrash the cache on sub-pixel jitter.
    dprq = round(dpr, 2)
    key = (name, color, size, dprq)
    if key in _pixmap_cache:
        return QIcon(_pixmap_cache[key])
    path = os.path.join(_ICON_DIR, name + ".svg")
    try:
        with open(path, "r", encoding="utf-8") as f:
            svg = f.read().replace("currentColor", color)
    except OSError:
        return QIcon()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(int(round(size * dprq)), int(round(size * dprq)))
    pm.setDevicePixelRatio(dprq)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Explicit logical bounds: with a device-pixel ratio set, the painter
    # works in logical coordinates (size x size), not the pixmap's device
    # rect — rendering without bounds would clip to the top-left quarter.
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    _pixmap_cache[key] = pm
    return QIcon(pm)


def _clear_icon_cache():
    """Drop cached tinted pixmaps — call on a light/dark flip so default-colour
    icons re-render in the new INK."""
    _pixmap_cache.clear()


# ── Components ───────────────────────────────────────────────────────────────

_CHIP_KINDS = {
    "good":   (GOOD,  GOOD_BG),
    "bad":    (BAD,   BAD_BG),
    "warn":   (WARN,  WARN_BG),
    "info":   (INFO,  INFO_BG),
    "accent": (ACCENT, ACCENT_SOFT),
    "muted":  (MUTED, SUNKEN),
}


def chip(text, kind="muted", parent=None):
    """Pill-shaped status label, e.g. chip('▲ +0.42 units', 'good')."""
    fg, bg = _CHIP_KINDS.get(kind, _CHIP_KINDS["muted"])
    lbl = QLabel(text, parent)
    lbl.setStyleSheet(
        f"color:{fg}; background:{bg}; border:none; border-radius:9px;"
        f" padding:2px 9px; font-family:{FONT_SEMIBOLD}; font-size:11px;"
        f" font-weight:600;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def skeleton(height=14, parent=None):
    """Flat placeholder block shown while content loads."""
    f = QFrame(parent)
    f.setFixedHeight(height)
    f.setStyleSheet(
        f"background:{SUNKEN}; border:none; border-radius:{height // 2}px;")
    return f


# ── Motion ───────────────────────────────────────────────────────────────────

def install_fade(stack, duration=150):
    """Fade each incoming QStackedWidget screen in (one-time hookup).

    Connects to currentChanged so no navigation call sites need touching.
    The opacity effect is removed when the animation finishes, so it never
    interferes with normal painting.
    """
    def _on_changed(_index):
        w = stack.currentWidget()
        if w is None:
            return
        effect = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", w)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda w=w: w.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        stack._fade_anim = anim   # keep a reference while running

    stack.currentChanged.connect(_on_changed)
