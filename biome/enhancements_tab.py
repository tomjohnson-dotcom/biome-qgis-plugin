# enhancements_tab.py
"""
Enhancements tab UI for Biome.

ON-SITE (retained-baseline grounded — enhancement_engine v2): reads the land that
survives the development and suggests how to reach +10% net gain. Two views:
  • Recipes   — the gap, the trading-currency requirement, and ranked recipes.
  • By parcel — each retained parcel and what it could become, with the % net
                gain each move would add.
OFF-SITE (legacy, unchanged): the original gap + currency + recipes_for_module +
  custom builder, with the spatial-risk selector.

Styling comes from theme.py so the tab matches the rest of the plugin.
"""

import os
import traceback

from qgis.core import Qgis
from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QDoubleSpinBox,
    QSpinBox, QPushButton, QScrollArea, QFrame, QSizePolicy,
)

from . import enhancement_engine as ENG
from . import habitat_data as HD
from . import theme as T


# Back-compat colour aliases mapped onto the theme tokens.
_INK, _MUTED        = T.INK, T.MUTED
_GREEN              = T.ACCENT_DARK     # section headings
_GREEN_MID          = T.ACCENT          # values / totals
_AMBER              = T.WARN
_CARD_BG, _CARD_BORDER = T.CARD, T.BORDER
_VALID_BG, _INVALID_BG = T.GOOD_BG, T.BAD_BG
_FONT = "font-family:%s;" % T.FONT

_MODULES = [("area", "Area"), ("hedgerow", "Hedgerow"), ("watercourse", "Watercourse")]
# Spatial-risk categories: (display label, EXACT metric category string, multiplier).
# The metric VLOOKUPs the category string (trailing spaces included), so they must
# be byte-exact; the multiplier drives the engine's net/ha.
_SPATIAL = [
    ("Inside LPA / NCA of impact (×1.0)",
     "Compensation inside LPA boundary or NCA of impact site ", 1.0),
    ("Neighbouring LPA / NCA (×0.75)",
     "Compensation outside LPA or NCA of impact site, but in neighbouring LPA or NCA ", 0.75),
    ("Outside / not neighbouring (×0.5)",
     "Compensation outside LPA or NCA of impact site and neighbouring LPA or NCA  ", 0.5),
]
_AREA_CREATE = ["Other neutral grassland", "Mixed scrub", "Lowland meadows",
                "Lowland calcareous grassland", "Traditional orchards",
                "Lowland mixed deciduous woodland", "Bramble scrub"]
_CONDITIONS = ["Poor", "Fairly Poor", "Moderate", "Fairly Good", "Good"]

# Every metric AREA habitat (sub-types outside the linear Hedgerow / Watercourse
# categories) — the receptor's EXISTING habitat for an off-site assessment.
_AREA_HABITATS = sorted(
    sub for cat in HD.HABITAT_SCREEN_TYPES.get("area", [])
    for sub in HD.HABITAT_TYPES.get(cat, {}))

# Distinctiveness a residual/credit tier must be met at (for the report tables).
_DISTINCT_LABEL = {"A1": "low to medium or higher", "A2": "high or higher",
                   "A3": "high or higher", "A4": "high or higher", "A5": "very high",
                   "H": "hedgerow", "W": "watercourse"}

# route -> (chip label, theme chip kind)
_ROUTE_CHIP = {"enhancement": ("Enhance", "accent"),
               "creation":    ("Create", "info"),
               "additive":    ("Trees", "good")}

# Tree-planting density steps for the +/- control (scattered ~50 → woodland 2000).
_DENSITY_STOPS = [25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000]

# Strategic significance the recipes assume (default Low). (label, metric value).
_STRAT = [
    ("Not in local strategy (Low ×1.0)",      "Area/compensation not in local strategy/ no local strategy"),
    ("Ecologically desirable (Medium ×1.1)",  "Location ecologically desirable but not in local strategy"),
    ("Formally in local strategy (High ×1.15)", "Formally identified in local strategy"),
]

# Off-site watercourse encroachment options (metric-exact strings; the blank first
# option is omitted deliberately — a watercourse must carry an encroachment value
# or the metric can't score it). The two banks combine into the metric's col-O
# string via bng_multipliers.canonical_riparian_lr at export.
_WC_ENC_OPTIONS = ["No Encroachment", "Minor", "Major"]
_RIP_ENC_OPTIONS = ["No Encroachment", "Minor", "Moderate", "Major"]


def _viewtab_style(active):
    """Underline sub-tab style for the Recipes / By-parcel toggle — visually
    distinct from the filled On-site / Off-site mode pills (which sit above it)."""
    if active:
        return ("QPushButton{background:transparent; color:%s; border:none;"
                " border-bottom:2px solid %s; border-radius:0; padding:5px 10px;"
                " font-family:%s; font-size:12px;}" % (T.ACCENT_DARK, T.ACCENT, T.FONT_SEMIBOLD))
    return ("QPushButton{background:transparent; color:%s; border:none;"
            " border-bottom:2px solid transparent; border-radius:0; padding:5px 10px;"
            " font-family:%s; font-size:12px;}QPushButton:hover{color:%s;}"
            % (T.MUTED, T.FONT_MEDIUM, T.INK))


def _lbl(text, color=_INK, size=T.SIZE_BODY, bold=False, wrap=False):
    l = QLabel(text)
    fam = T.FONT_SEMIBOLD if bold else T.FONT
    l.setStyleSheet("color:%s; font-size:%dpx; font-family:%s; background:transparent; border:none;"
                    % (color, size, fam))
    if wrap:
        l.setWordWrap(True)
    return l


def _hline():
    f = QFrame(); f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:%s;" % T.BORDER); return f


def _replacement_rule(band, group):
    """Plain-English 'what may replace this loss' phrase for the full-requirement
    table. Names the broad habitat GROUP where the metric's trading rule is
    group-based (Medium) or habitat-specific (High / Very High), so the ecologist
    can see exactly what needs putting back — not just a bare distinctiveness band.
    """
    b = (band or "").strip().lower()
    g = (group or "").strip()
    if b == "very high":
        return "Same specific habitat only (bespoke compensation)"
    if b == "high":
        return "Same specific habitat, or trade up to Very High"
    if b == "medium":
        return ("Medium or higher — same broad group (%s), or trade up" % g) if g \
               else "Medium or higher — same broad habitat group, or trade up"
    if b == "low":
        return "Low or higher — any broad habitat group"
    if b in ("very low", ""):
        return "No trading constraint"
    return "%s or higher" % b.capitalize()


def _card(greyed=False):
    # #id-scoped so the border doesn't cascade onto child QFrames inside the card.
    c = QFrame(); c.setObjectName("enhCard")
    bg = T.SUNKEN if greyed else T.CARD
    c.setStyleSheet("QFrame#enhCard{background:%s; border:1px solid %s; border-radius:%dpx;}"
                    % (bg, T.BORDER, T.RADIUS))
    lay = QVBoxLayout(c); lay.setContentsMargins(12, 10, 12, 11); lay.setSpacing(5)
    return c, lay


def _combo(items):
    cb = QComboBox()
    cb.addItems(items)
    # Don't let a long item dictate the panel width; the drop-down still shows
    # the full text. Visual styling is inherited from the dock theme.
    cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(10)
    cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return cb


def _fmt(n):
    """Thousands-separated whole pounds, e.g. 1234567 -> '1,234,567'."""
    try:
        return "{:,.0f}".format(float(n))
    except (TypeError, ValueError):
        return str(n)


def _stepper(glyph):
    """Small square +/- button for the density control (clean, centred glyph)."""
    b = QPushButton(glyph)
    b.setFixedSize(26, 24)
    b.setStyleSheet("QPushButton{background:%s; color:%s; border:1px solid %s; border-radius:5px;"
                    " font-family:%s; font-size:16px; padding:0;}"
                    "QPushButton:hover{background:%s;}"
                    "QPushButton:disabled{color:%s; background:%s;}"
                    % (T.CARD, T.INK, T.BORDER_STRONG, T.FONT_SEMIBOLD, T.ACCENT_SOFT, T.FAINT, T.SUNKEN))
    return b


def _btn(text, primary=False):
    b = QPushButton(text)
    if primary:
        b.setStyleSheet("QPushButton{background:%s; color:white; border:none; border-radius:7px;"
                        " padding:5px 13px; font-family:%s; font-size:12px;}"
                        "QPushButton:hover{background:%s;}" % (T.ACCENT, T.FONT_SEMIBOLD, T.ACCENT_DARK))
    else:
        b.setStyleSheet("QPushButton{background:%s; color:%s; border:1px solid %s; border-radius:7px;"
                        " padding:5px 13px; font-family:%s; font-size:12px;}"
                        "QPushButton:hover{background:%s;}"
                        % (T.CARD, T.INK, T.BORDER_STRONG, T.FONT_MEDIUM, T.ACCENT_SOFT))
    return b


class EnhancementsTab(QWidget):
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._mode = "onsite"          # 'onsite' | 'offsite'
        self._view = "recipes"         # on-site sub-view: 'recipes' | 'parcels'
        self._curtilage_ok = None      # on-site gate: None=unasked, True=outside curtilage, False=all curtilage
        self._module = "area"
        self._spatial = 1.0
        self._baseline = 0.0           # baseline area units, for % net gain
        self._tree_density = ENG.TREE_DENSITY_SOFT   # trees/ha cap when density is set MANUALLY
        self._tree_density_auto = True               # True = mould density to hit target; False = _tree_density is a cap
        self._tree_eff_density = None                # last trees-only recipe's effective density (for the +/- control)
        self._tree_size = "Small"                    # tree size class for planting
        self._tree_condition = "Poor"                # tree target condition
        self._strategic = _STRAT[0][1]               # strategic significance the recipes assume (default Low)
        self._spatial_cat = _SPATIAL[0][1]           # off-site spatial-risk metric category (default inside LPA/NCA)
        self._off_habitat = "Modified grassland"     # off-site receptor's existing habitat
        self._off_condition = "Poor"                 # off-site receptor's current condition
        self._off_irreplaceable = "No"               # off-site receptor irreplaceable flag (~always No)
        self._off_wc_enc = "No Encroachment"         # off-site watercourse channel encroachment
        self._off_rip_l = "No Encroachment"          # off-site watercourse left-bank encroachment
        self._off_rip_r = "No Encroachment"          # off-site watercourse right-bank encroachment
        self._off_basis = "full"                     # off-site target basis: 'full' | 'residual'
        self._bank_spatial = 1.0                     # 'Market & credits' habitat-bank spatial risk
        self._bank_price = 25000.0                   # default £/unit for a habitat-bank band (fallback)
        self._bank_prices = {}                       # per-band £/unit overrides {tier: price}
        self._sel_recipe = {}                        # module -> chosen on-site recipe label (off-site residual)
        self._cond_override = {}                      # (module, recipe label) -> manual target condition
        self._market_view = "full"                   # Market & credits sub-view: 'full' | 'selected'
        self._target_override = None   # off-site editable target
        self._custom_steps = []        # off-site custom builder steps (legacy, unused)
        self._build()

    # ── public hooks (called by the panel) ─────────────────────────────────
    def refresh_onsite(self):
        self.refresh()

    def refresh_offsite(self):
        self.refresh()

    # ── scaffold ───────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        modebar = QHBoxLayout(); modebar.setSpacing(6)
        self._btn_on = _btn("On-site", primary=True)
        self._btn_off = _btn("Off-site")
        self._btn_market = _btn("Market && credits")   # && -> literal & (Qt treats & as a mnemonic)
        self._btn_on.clicked.connect(lambda: self._set_mode("onsite"))
        self._btn_off.clicked.connect(lambda: self._set_mode("offsite"))
        self._btn_market.clicked.connect(lambda: self._set_mode("market"))
        modebar.addWidget(self._btn_on); modebar.addWidget(self._btn_off)
        modebar.addWidget(self._btn_market)
        modebar.addStretch(1)
        root.addLayout(modebar)

        self._subtitle = _lbl("", _MUTED, 12, wrap=True)
        root.addWidget(self._subtitle)

        modbar = QHBoxLayout(); modbar.setSpacing(6)
        self._mod_wrap = QWidget(); mwl = QHBoxLayout(self._mod_wrap)
        mwl.setContentsMargins(0, 0, 0, 0); mwl.setSpacing(6)
        mwl.addWidget(_lbl("Module:", _MUTED, 11, bold=True))
        self._mod_combo = _combo([m[1] for m in _MODULES])
        self._mod_combo.currentIndexChanged.connect(self._on_module_changed)
        mwl.addWidget(self._mod_combo)
        modbar.addWidget(self._mod_wrap)

        self._sp_wrap = QWidget(); spl = QHBoxLayout(self._sp_wrap)
        spl.setContentsMargins(10, 0, 0, 0); spl.setSpacing(6)
        spl.addWidget(_lbl("Spatial risk:", _MUTED, 11, bold=True))
        self._sp_combo = _combo([s[0] for s in _SPATIAL])
        self._sp_combo.currentIndexChanged.connect(self._on_spatial_changed)
        spl.addWidget(self._sp_combo)
        modbar.addWidget(self._sp_wrap)

        self._view_wrap = QWidget(); vwl = QHBoxLayout(self._view_wrap)
        vwl.setContentsMargins(8, 0, 0, 0); vwl.setSpacing(2)
        self._btn_recipes = QPushButton("Recipes"); self._btn_recipes.setStyleSheet(_viewtab_style(True))
        self._btn_parcels = QPushButton("By parcel"); self._btn_parcels.setStyleSheet(_viewtab_style(False))
        self._btn_recipes.clicked.connect(lambda: self._set_view("recipes"))
        self._btn_parcels.clicked.connect(lambda: self._set_view("parcels"))
        vwl.addWidget(self._btn_recipes); vwl.addWidget(self._btn_parcels)
        modbar.addWidget(self._view_wrap)
        modbar.addStretch(1)
        root.addLayout(modbar)

        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{border:none;}")
        self._holder = QWidget()
        self._content = QVBoxLayout(self._holder)
        self._content.setContentsMargins(0, 4, 0, 0); self._content.setSpacing(9)
        self._scroll.setWidget(self._holder)
        root.addWidget(self._scroll, 1)

        self._apply_bar_visibility()
        self.refresh()

    def _apply_bar_visibility(self):
        on = (self._mode == "onsite")
        market = (self._mode == "market")
        # While the on-site curtilage gate is unanswered (or blocked), hide the
        # module selector and the by-parcel toggle — there's nothing to pick yet.
        onsite_live = on and self._curtilage_ok is True
        self._mod_wrap.setVisible(not market and (onsite_live or not on))
        self._sp_wrap.setVisible(False)                             # spatial risk now lives in the off-site input card
        self._view_wrap.setVisible(onsite_live and self._module == "area")   # by-parcel: on-site Area only

    # ── state changes ───────────────────────────────────────────────────────
    def _set_mode(self, mode):
        self._mode = mode
        self._btn_on.setStyleSheet(_btn("x", primary=(mode == "onsite")).styleSheet())
        self._btn_off.setStyleSheet(_btn("x", primary=(mode == "offsite")).styleSheet())
        self._btn_market.setStyleSheet(_btn("x", primary=(mode == "market")).styleSheet())
        if mode == "onsite":
            self._module = "area"
        self._apply_bar_visibility()
        self._target_override = None
        self._custom_steps = []
        self.refresh()

    def _set_view(self, view):
        self._view = view
        rec = (view == "recipes")
        self._btn_recipes.setStyleSheet(_viewtab_style(rec))
        self._btn_parcels.setStyleSheet(_viewtab_style(not rec))
        self.refresh()

    def _on_module_changed(self, i):
        self._module = _MODULES[i][0]
        self._target_override = None
        self._custom_steps = []
        self._apply_bar_visibility()       # by-parcel toggle only applies to Area
        self.refresh()

    def _on_spatial_changed(self, i):
        if 0 <= i < len(_SPATIAL):
            self._spatial = _SPATIAL[i][2]
            self._spatial_cat = _SPATIAL[i][1]
        self._target_override = None
        bar = self._scroll.verticalScrollBar(); pos = bar.value()
        self.refresh(); bar.setValue(pos)

    # ── data helpers ──────────────────────────────────────────────────────────
    def _module_gap(self):
        try:
            defs = self._panel.enhancement_module_deficits() or {}
        except Exception:
            defs = {}
        return max(0.0, float(defs.get(self._module, 0.0)))

    def _target(self):
        if self._target_override is not None:
            return self._target_override
        gap = self._module_gap()
        if self._mode == "offsite" and self._spatial > 0:
            return gap / self._spatial
        return gap

    def _losses(self, include_trees=True):
        # GROSS baseline losses — what was destroyed, before anything the scheme
        # builds back is netted off. That is the right currency picture for the
        # on-site solver (which has to find a home for each lost habitat), but it
        # is NOT a quantity to buy: the Market & credits table uses the netted
        # trading-rule deficits from enhancement_band_requirements() instead.
        # Trees are INCLUDED by default: a lost tree is a Medium-distinctiveness
        # replacement obligation (broad group "Individual Trees"), so it's a real
        # trading currency when the solver ranks recipes. It only adds a currency
        # constraint; it does NOT change the unit target (that comes from the
        # module deficit), so there's no double-counting.
        try:
            return self._panel.enhancement_band_breakdown(include_trees=include_trees) or []
        except Exception:
            return []

    def _retained_parcels(self):
        try:
            return self._panel.enhancement_retained_parcels() or []
        except Exception:
            return []

    def _baseline_units(self):
        try:
            bl, _pr = self._panel.enhancement_unit_totals()
            return float(bl) if bl else 0.0
        except Exception:
            return 0.0

    # ── rebuild ──────────────────────────────────────────────────────────────
    def _clear(self):
        while self._content.count():
            it = self._content.takeAt(0)
            w = it.widget()
            if w:
                # setParent(None) detaches from the visible tree NOW; deleteLater is
                # async, so on its own it leaves the old card painted at its previous
                # geometry until the event loop ticks — which shows as cards
                # overlapping on a mode switch or a height-changing refresh.
                w.setParent(None)
                w.deleteLater()

    def refresh(self):
        self._clear()
        if self._mode == "onsite":
            self._subtitle.setText(
                "Ways to reach +10% net gain on the land that survives the development.")
            self._refresh_onsite()
        elif self._mode == "offsite":
            self._subtitle.setText(
                "Off-site: describe a receptor you control — Biome finds the minimum to create / enhance there.")
            self._refresh_offsite()
        else:
            self._subtitle.setText(
                "Buy the shortfall: statutory credits (last resort), or off-site units from a habitat bank.")
            self._refresh_market()
        self._content.addStretch(1)

    def _on_target_edited(self, val):
        self._target_override = float(val)
        self.refresh()

    def _on_density(self, direction):
        stops = _DENSITY_STOPS
        if self._tree_density_auto:
            # Leaving auto: seed the manual cap from the density the recipe moulded
            # to, then take one step from there. Now the density CAPS the trees.
            eff = self._tree_eff_density or self._tree_density
            if direction < 0:
                below = [s for s in stops if s < eff - 1e-9]
                self._tree_density = below[-1] if below else stops[0]
            else:
                above = [s for s in stops if s > eff + 1e-9]
                self._tree_density = above[0] if above else stops[-1]
            self._tree_density_auto = False
        else:
            try:
                i = stops.index(self._tree_density)
            except ValueError:
                i = min(range(len(stops)), key=lambda k: abs(stops[k] - self._tree_density))
            i = max(0, min(len(stops) - 1, i + direction))
            if stops[i] == self._tree_density:
                return
            self._tree_density = stops[i]
        bar = self._scroll.verticalScrollBar(); pos = bar.value()
        self.refresh()                 # re-evaluates recipes at the new density
        bar.setValue(pos)              # keep the view where it was

    def _reset_density_auto(self):
        """Back to auto: the density moulds to whatever hits the target again."""
        self._tree_density_auto = True
        self._tree_density = ENG.TREE_DENSITY_SOFT
        bar = self._scroll.verticalScrollBar(); pos = bar.value()
        self.refresh(); bar.setValue(pos)

    def _on_tree_size(self, text):
        if text and text != self._tree_size:
            self._tree_size = text
            bar = self._scroll.verticalScrollBar(); pos = bar.value()
            self.refresh(); bar.setValue(pos)

    def _on_tree_condition(self, text):
        if text and text != self._tree_condition:
            self._tree_condition = text
            bar = self._scroll.verticalScrollBar(); pos = bar.value()
            self.refresh(); bar.setValue(pos)

    def _on_strategic(self, i):
        if 0 <= i < len(_STRAT) and _STRAT[i][1] != self._strategic:
            self._strategic = _STRAT[i][1]
            bar = self._scroll.verticalScrollBar(); pos = bar.value()
            self.refresh(); bar.setValue(pos)

    def _add_strategic_row(self):
        """Strategic significance assumed for every recipe suggestion — defaults to
        'not in local strategy' (Low); change it if the site is within an LNRS."""
        w = QWidget(); row = QHBoxLayout(w); row.setContentsMargins(2, 0, 2, 0); row.setSpacing(6)
        row.addWidget(_lbl("Strategic significance:", _MUTED, 11, bold=True))
        cb = _combo([o[0] for o in _STRAT])
        for i, o in enumerate(_STRAT):
            if o[1] == self._strategic:
                cb.setCurrentIndex(i); break
        cb.currentIndexChanged.connect(self._on_strategic)
        row.addWidget(cb, 1)
        chk = QPushButton("Check ↗")
        chk.setCursor(Qt.CursorShape.PointingHandCursor)
        chk.setToolTip(
            "Open your site authority's Local Nature Recovery Strategy / local plan "
            "map to confirm whether this location is of strategic significance — "
            "High if formally identified in the strategy, Medium if ecologically "
            "desirable but not identified, otherwise Low.")
        chk.setStyleSheet(
            "QPushButton{background:transparent; color:%s; border:none; font-family:%s;"
            " font-size:11px; text-decoration:underline; padding:0 4px;}"
            "QPushButton:hover{color:%s;}" % (T.ACCENT, T.FONT_MEDIUM, T.ACCENT_DARK))
        chk.clicked.connect(self._check_strategic)
        row.addWidget(chk)
        self._content.addWidget(w)

    def _check_strategic(self):
        try:
            self._panel.open_strategic_significance_check()
        except Exception as e:
            self._content.addWidget(_lbl(
                "Couldn't open the strategic-significance check: %s" % e, _AMBER, 12, wrap=True))

    def _add_density_row(self, lay, eff=None):
        """Tree controls: planting density (+/-) and size / condition. The density
        shown is the EFFECTIVE rate — in auto it's whatever was moulded to hit the
        target; lowering it fixes a manual cap so fewer trees fit (and the recipe
        then falls short)."""
        stops = _DENSITY_STOPS
        if eff:
            self._tree_eff_density = eff
        dval = int(round(eff)) if (self._tree_density_auto and eff) else self._tree_density
        r1 = QHBoxLayout(); r1.setSpacing(6)
        r1.addWidget(_lbl("Tree density:", _MUTED, 11, bold=True))
        minus = _stepper("−"); plus = _stepper("+")
        minus.setEnabled(dval > stops[0])
        plus.setEnabled(dval < stops[-1])
        minus.clicked.connect(lambda: self._on_density(-1))
        plus.clicked.connect(lambda: self._on_density(1))
        r1.addWidget(minus)
        r1.addWidget(_lbl("%d / ha" % dval, _INK, 12, bold=True))
        r1.addWidget(plus)
        if self._tree_density_auto:
            r1.addWidget(_lbl("auto", _MUTED, 10))
        else:
            reset = QPushButton("Auto"); reset.setStyleSheet(_viewtab_style(False))
            reset.clicked.connect(self._reset_density_auto)
            r1.addWidget(reset)
        r1.addStretch(1)
        lay.addLayout(r1)
        r2 = QHBoxLayout(); r2.setSpacing(6)
        r2.addWidget(_lbl("Trees:", _MUTED, 11, bold=True))
        sz = _combo(["Small", "Medium", "Large", "Very Large"]); sz.setCurrentText(self._tree_size)
        sz.setMaximumWidth(120); sz.currentTextChanged.connect(self._on_tree_size)
        r2.addWidget(sz)
        co = _combo(["Poor", "Moderate", "Good"]); co.setCurrentText(self._tree_condition)
        co.setMaximumWidth(110); co.currentTextChanged.connect(self._on_tree_condition)
        r2.addWidget(co)
        r2.addStretch(1)
        lay.addLayout(r2)
        if dval > ENG.TREE_DENSITY_SOFT:
            lay.addWidget(_lbl("Above ~%d/ha this is woodland-density planting, not scattered trees."
                               % ENG.TREE_DENSITY_SOFT, _AMBER, 9, wrap=True))

    def _status_icon(self, name, color, tip):
        """Fixed-size tinted SVG status glyph (uniform across all cards)."""
        lab = QLabel()
        lab.setPixmap(T.icon(name, color, 16).pixmap(QSize(16, 16)))
        lab.setFixedSize(20, 20)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setToolTip(tip)
        lab.setStyleSheet("background:transparent; border:none;")
        return lab

    # ── shared: required-units header ─────────────────────────────────────────
    def _add_target_header(self, target, editable=True):
        gap = self._module_gap()
        card, lay = _card()
        head = QHBoxLayout()
        head.addWidget(_lbl("Units required to reach +10%", _GREEN, 13, bold=True))
        head.addStretch(1)
        if editable:
            self._target_spin = QDoubleSpinBox()
            self._target_spin.setRange(0.0, 1e6); self._target_spin.setDecimals(3)
            self._target_spin.setSingleStep(0.1); self._target_spin.setValue(round(target, 3))
            self._target_spin.valueChanged.connect(self._on_target_edited)
            head.addWidget(self._target_spin)
            head.addWidget(_lbl("units", _MUTED, 11))
        else:
            head.addWidget(_lbl("%.2f units" % target, _GREEN, 16, bold=True))
        lay.addLayout(head)
        if editable and self._mode == "offsite" and self._spatial < 1.0:
            lay.addWidget(_lbl("Gap from Summary ÷ ×%.2f spatial = %.2f raw units off-site."
                               % (self._spatial, target), _MUTED, 10, wrap=True))
        self._content.addWidget(card)

    # ── shared: per-currency breakdown ────────────────────────────────────────
    def _add_currency_breakdown(self, target):
        card, lay = _card()
        lay.addWidget(_lbl("What those %.2f units must be" % target, _INK, 13, bold=True))
        if self._module == "area":
            # include_trees=True: a lost tree is a Medium replacement need — mirror
            # the Market & credits full-requirement table so the two agree.
            defs = ENG.currency_deficits(self._losses(include_trees=True))
            scale = (1.0 / self._spatial) if (self._mode == "offsite" and self._spatial > 0) else 1.0
            loss_tot, medium_plus = 0.0, False
            for d in defs:
                need = d["deficit_units"] * scale
                loss_tot += need
                if any(k in d["label"] for k in ("Medium", "High", "group")):
                    medium_plus = True
                lay.addWidget(_lbl("•  %.2f units — %s" % (need, d["label"]), _INK, 12, wrap=True))
            headroom = max(0.0, target - loss_tot)
            if headroom > 0.01:
                lay.addWidget(_lbl("•  %.2f units — 10%% net gain on top: any habitat (Low or higher)"
                                   % headroom, _INK, 12, wrap=True))
            if not defs and headroom <= 0.01:
                lay.addWidget(_lbl("No losses yet — any positive enhancement counts.", _MUTED, 12, wrap=True))
            elif medium_plus:
                lay.addWidget(_lbl("Higher distinctiveness covers a lower requirement; a same-band gain in a "
                                   "different broad group does not.", _MUTED, 12, wrap=True))
        else:
            lay.addWidget(_lbl("Replace a loss with the same distinctiveness band or better.", _MUTED, 12, wrap=True))
        self._content.addWidget(card)

    # ════════════════════════════════════════════════════════════════════════
    # ON-SITE
    # ════════════════════════════════════════════════════════════════════════
    def _refresh_onsite(self):
        # Gate: on-site delivery is only possible on land OUTSIDE a residential
        # curtilage. Ask once; the answer persists until "Change answer".
        if self._curtilage_ok is None:
            self._add_curtilage_gate()
            return
        target = self._target()
        self._baseline = self._baseline_units()
        self._add_change_answer_bar()
        self._add_target_header(target, editable=False)
        self._add_currency_breakdown(target)          # what the units must be — shown either way
        if self._curtilage_ok is False:
            self._add_curtilage_blocked()             # can't be reached on-site; requirement still shown above
            return
        if self._module != "area":
            # Hedgerow / watercourse: linear, no land budget, no 'by parcel'.
            self._add_linear_recipes(target)
        else:
            parcels = self._retained_parcels()
            losses = self._losses()
            self._dbg_onsite(target, parcels, losses)
            if self._view == "parcels":
                self._add_parcel_explorer(parcels)
            else:
                self._add_recipe_cards_v2(target, parcels, losses)

    # ── curtilage gate ────────────────────────────────────────────────────────
    def _set_curtilage(self, ok):
        """ok=True  -> site is (partly) OUTSIDE a residential curtilage: proceed.
        ok=False -> whole site is within a curtilage: on-site not possible."""
        self._curtilage_ok = bool(ok)
        self._apply_bar_visibility()
        bar = self._scroll.verticalScrollBar(); pos = bar.value()
        self.refresh(); bar.setValue(pos)

    def _reset_curtilage(self):
        self._curtilage_ok = None
        self._apply_bar_visibility()
        self.refresh()

    def _add_curtilage_gate(self):
        """Decision screen shown before any on-site recipes. Carries the curtilage
        rule (moved here from the old bottom note) and the yes/no choice."""
        card, lay = _card()
        title = QLabel("⚠  Residential curtilage check")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight:600; color:#9A6700; border:none; background:transparent;")
        lay.addWidget(title)
        lay.addWidget(_lbl(
            "On-site biodiversity gains can only be delivered on land OUTSIDE a "
            "residential curtilage. The statutory metric records private gardens as "
            "“Vegetated garden” / “Unvegetated garden”, does not permit upgrading "
            "them to higher-value habitats, and trees or hedgerows planted within a "
            "curtilage add no units.", _INK, 12, wrap=True))
        lay.addWidget(_lbl("Is the entire site within a residential curtilage?", _INK, 13, bold=True, wrap=True))
        # Stacked full-width so the labels never clip on a narrow dock panel.
        no = _btn("No — some of the site is outside", primary=True)
        yes = _btn("Yes — the whole site is curtilage")
        no.clicked.connect(lambda: self._set_curtilage(True))
        yes.clicked.connect(lambda: self._set_curtilage(False))
        for b in (no, yes):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay.addWidget(b)
        self._content.addWidget(card)

    def _add_change_answer_bar(self):
        card, lay = _card()
        state = ("outside the residential curtilage" if self._curtilage_ok
                 else "entirely within a residential curtilage")
        lay.addWidget(_lbl("Curtilage answer: site is %s." % state, _MUTED, 12, wrap=True))
        btn = _btn("↩  Change curtilage answer")
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(self._reset_curtilage)
        lay.addWidget(btn)
        self._content.addWidget(card)

    def _add_curtilage_blocked(self):
        card, lay = _card()
        title = QLabel("⚠  On-site delivery not possible")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight:600; color:#9A6700; border:none; background:transparent;")
        lay.addWidget(title)
        lay.addWidget(_lbl(
            "The whole site is within a residential curtilage, so the metric won't "
            "let you deliver these units on-site — gardens can't be upgraded and "
            "curtilage trees or hedgerows score no units. The requirement above still "
            "stands: secure it Off-site, or via statutory credits.", _INK, 12, wrap=True))
        offb = _btn("Go to Off-site", primary=True)
        mkb = _btn("Go to Market && credits", primary=True)   # && -> literal & (Qt treats & as a mnemonic)
        offb.clicked.connect(lambda: self._set_mode("offsite"))
        mkb.clicked.connect(lambda: self._set_mode("market"))
        for b in (offb, mkb):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay.addWidget(b)
        self._content.addWidget(card)

    def _add_linear_recipes(self, target):
        self._content.addWidget(_lbl("Suggested recipes", _GREEN, 13, bold=True))
        try:
            feats = self._panel.enhancement_retained_linear(self._module) or []
        except Exception:
            feats = []
        try:
            recipes = ENG.linear_recipes(self._module, target, feats, strategic_value=None)
        except Exception as e:
            self._content.addWidget(_lbl("Could not build recipes: %s" % e, _AMBER, 12, wrap=True)); return
        if not recipes:
            self._content.addWidget(_lbl(
                "No %s suggestions yet — no retained %ss found, or the gap is already met."
                % (self._module, self._module), _MUTED, 12, wrap=True)); return
        for r in recipes:
            self._add_recipe_card_v2(r, target)

    def _dbg_onsite(self, target, parcels, losses):
        # TEMP (QGIS test): snapshot inputs/outputs to biome_debug.log.
        try:
            path = os.path.join(os.path.dirname(__file__), "biome_debug.log")
            out = ["", "==== ON-SITE refresh (view=%s) ====" % self._view,
                   "target=%.4f  baseline=%.4f  parcels=%d  losses=%d"
                   % (target, self._baseline, len(parcels), len(losses))]
            for p in parcels[:40]:
                out.append("  parcel: %-34s %-24s %.3f ha  feas=%s"
                           % (p.get("habitat", ""), (p.get("condition") or ""),
                              float(p.get("area_ha") or 0.0), ENG.host_feasibility(p.get("habitat", ""))))
            try:
                recs = ENG.build_recipes(parcels, target, losses=losses, tree_density=self._tree_density,
                                         tree_size=self._tree_size, tree_condition=self._tree_condition,
                                         strategic_value=None)
                out.append("  recipes=%d" % len(recs))
                for r in recs:
                    out.append("    [%s] %s | land=%.2f units=%.2f closes=%s covers=%s rec=%s"
                               % (r["kind"], r["label"], r["land_used_ha"], r["total_units"],
                                  r["closes_gap"], r["covers_currencies"], r["recommended"]))
            except Exception:
                out.append("  build_recipes EXCEPTION:\n" + traceback.format_exc())
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
        except Exception:
            pass

    # ── recipes view ──────────────────────────────────────────────────────────
    def _add_recipe_cards_v2(self, target, parcels, losses):
        self._content.addWidget(_lbl("Suggested recipes", _GREEN, 13, bold=True))
        if not parcels:
            self._content.addWidget(_lbl(
                "No retained habitats found — map a baseline and a proposed / red-line layer.", _MUTED, 12, wrap=True))
            return
        try:
            recipes = ENG.build_recipes(parcels, target, losses=losses, tree_density=self._tree_density,
                                        tree_size=self._tree_size, tree_condition=self._tree_condition,
                                        strategic_value=None, tree_density_auto=self._tree_density_auto)
        except Exception as e:
            self._content.addWidget(_lbl("Could not build recipes: %s" % e, _AMBER, 12, wrap=True))
            return
        if not recipes:
            self._content.addWidget(_lbl(
                "No recipe fits within 20 years — see the By parcel view.", _MUTED, 12, wrap=True))
            return
        recipes = self._apply_cond_overrides(recipes, "area", target)
        # One persistent tree-planting control for the whole list (density / size /
        # condition) — shown whenever trees are possible so it never disappears when
        # a low density drops the count to zero, and it stays put while you adjust.
        if (any(ENG.can_plant_trees(p.get("habitat", "")) for p in parcels)
                or any(s.get("kind") == "tree" for r in recipes for s in r["steps"])):
            self._add_tree_controls(recipes)
        for r in recipes:
            self._add_recipe_card_v2(r, target)
        # Always surface Traditional orchard as a specialist option (rarely ranks
        # top — the card itself carries the reasons why).
        try:
            orch = ENG.specialist_orchard_recipe(parcels, target, losses=losses, strategic_value=None)
        except Exception:
            orch = None
        if orch and not any(r.get("label") == orch["label"] for r in recipes):
            orch = self._apply_cond_overrides([orch], "area", target)[0]
            self._add_recipe_card_v2(orch, target)

    def _add_tree_controls(self, recipes):
        """Persistent tree-planting controls at a fixed spot above the recipes."""
        eff = max((s.get("density", 0) for r in recipes for s in r.get("steps", []) if s.get("kind") == "tree"),
                  default=None)
        card, lay = _card()
        lay.addWidget(_lbl("Tree planting — applies to the tree options below", _GREEN, 12, bold=True))
        self._add_density_row(lay, eff)
        self._content.addWidget(card)

    def _add_recipe_card_v2(self, r, target):
        selected = (self._sel_recipe.get(self._module) == r["label"])
        card, lay = _card()
        if selected:
            card.setStyleSheet("QFrame#enhCard{background:%s; border:2px solid %s; border-radius:%dpx;}"
                               % (T.ACCENT_SOFT, T.ACCENT, T.RADIUS))
        top = QHBoxLayout()
        top.addWidget(_lbl(r["label"], _INK, 13, bold=True, wrap=True))
        top.addStretch(1)
        if r.get("kind") == "specialist":
            ic = self._status_icon("status-star", T.MUTED,
                                   "Specialist choice — surfaced on request, not the least-land option")
        elif r.get("recommended"):
            ic = self._status_icon("status-star", T.ACCENT, "Recommended — reaches +10%")
        elif r["closes_gap"] and r["covers_currencies"]:
            ic = self._status_icon("status-check", T.GOOD, "Closes the gap")
        elif r["closes_gap"]:
            ic = self._status_icon("status-alert", T.BAD, "Right amount, wrong trading currency")
        else:
            ic = self._status_icon("circle-dashed", T.WARN, "Partial — short of the target")
        top.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        for s in r["steps"]:
            lay.addWidget(_lbl("–  %s" % s["label"], _INK, 12, wrap=True))
        # Explain a below-Good target condition that is actually the unit-optimum.
        seen_notes = set()
        for s in r["steps"]:
            if s.get("route") in ("creation", "enhancement") and s.get("target_condition"):
                note = ENG.condition_efficiency_note(
                    s["route"], s.get("parcel_habitat", ""), s.get("parcel_condition", ""),
                    s.get("target", ""), s.get("target_condition", ""))
                if note and note not in seen_notes:
                    seen_notes.add(note)
                    lay.addWidget(_lbl("ⓘ  %s" % note, _MUTED, 10, wrap=True))
        rem = target - r["total_units"]
        if r["closes_gap"] and r["covers_currencies"]:
            tail = ("reaches +10%% with %.2f to spare" % -rem) if rem < -0.01 else "reaches +10% exactly"
        elif r["closes_gap"]:
            tail = "right amount, wrong currency"
        else:
            tail = "%.2f units short" % max(0.0, rem)
        amount = r["length_km"] if "length_km" in r else r.get("land_used_ha", 0.0)
        unit = "km" if "length_km" in r else "ha"
        yrs = (" · ≤%s yr" % r["max_years"]) if r.get("max_years") is not None else ""
        lay.addWidget(_lbl("%.2f units · %.2f %s%s — %s"
                           % (r["total_units"], amount, unit, yrs, tail),
                           _GREEN_MID, 12, bold=True, wrap=True))
        if r.get("why_not_top"):
            lay.addWidget(_lbl("ⓘ  %s" % r["why_not_top"], _AMBER, 10, wrap=True))
        total_trees = sum(int(s.get("trees", 0) or 0) for s in r["steps"])
        if total_trees > 0:
            ts = next((s.get("tree_size") for s in r["steps"] if s.get("trees")), self._tree_size)
            tc = next((s.get("tree_condition") for s in r["steps"] if s.get("trees")), self._tree_condition)
            lay.addWidget(_lbl("Total: %d %s trees in %s condition"
                               % (total_trees, str(ts).lower(), str(tc).lower()),
                               _INK, 12, bold=True, wrap=True))
            # Density moulded above the recommended scatter rate to fit the trees on
            # a small parcel — flag it (it's trending toward woodland-density planting).
            dense = max((s.get("density", 0) for s in r["steps"] if s.get("dense_warn")), default=0)
            if dense:
                lay.addWidget(_lbl(
                    "⚠  Planted at ~%d/ha to fit them on this land — above the ~%d/ha recommended "
                    "scatter rate, so it trends toward woodland-density planting."
                    % (int(round(dense)), ENG.TREE_DENSITY_SOFT), _AMBER, 10, wrap=True))
        # Manual density that no longer reaches the target: say what's still needed
        # (the density itself is set by the persistent control above the list).
        if total_trees > 0 and not self._tree_density_auto and not r["closes_gap"]:
            short = max(0.0, target - r["total_units"])
            lay.addWidget(_lbl(
                "At this density only %d tree%s fit here — still %.2f units short of the target. "
                "Raise the tree density above, use more land, or secure the rest off-site / via credits."
                % (total_trees, "" if total_trees == 1 else "s", short), _AMBER, 10, wrap=True))
        tgt = self._recipe_single_target(r)
        if self._module == "area" and tgt and r.get("kind") in ("single", "specialist"):
            self._add_cond_row(lay, r, tgt)
        sel = _btn("Selected ✓" if selected else "Select", primary=selected)
        sel.setToolTip("Mark this as your chosen on-site recipe — the off-site "
                       "'Residual after on-site' basis subtracts its on-site delivery.")
        sel.clicked.connect(lambda _=False, lbl=r["label"]: self._select_recipe(lbl))
        lay.addWidget(sel)
        exp = _btn("Export to metric with this recipe  ⤓")
        exp.clicked.connect(lambda _=False, rec=r: self._export_recipe(rec))
        lay.addWidget(exp)
        self._content.addWidget(card)

    def _export_recipe(self, recipe):
        """Hand the chosen recipe to the panel, which opens the metric export
        dialog and writes the metric with this recipe overlaid on the map."""
        try:
            self._panel.export_recipe_to_metric(recipe)
        except Exception as e:
            self._content.addWidget(_lbl("Export failed: %s" % e, _AMBER, 12, wrap=True))

    def _select_recipe(self, label):
        """Toggle the chosen on-site recipe for the current module — it's what the
        off-site 'Residual after on-site' basis subtracts. Click again to clear."""
        if self._sel_recipe.get(self._module) == label:
            self._sel_recipe.pop(self._module, None)
        else:
            self._sel_recipe[self._module] = label
        bar = self._scroll.verticalScrollBar(); pos = bar.value()
        self.refresh(); bar.setValue(pos)

    # ── per-recipe target-condition override ───────────────────────────────────
    def _recipe_single_target(self, r):
        """The single change/create target of a recipe (for the condition picker),
        or None if it has zero or multiple distinct targets."""
        tgts = {s.get("target") for s in r.get("steps", []) if s.get("kind") in ("change", "create")}
        return next(iter(tgts)) if len(tgts) == 1 else None

    def _apply_cond_overrides(self, recipes, module, deficit):
        """Re-price any recipe carrying a manual condition override at that
        condition, preserving its label / kind / recommended / why_not_top so its
        RANK (and on-screen position) is unchanged — the card never jumps."""
        if module != "area":
            return recipes
        out = []
        for r in recipes:
            ov = self._cond_override.get((module, r.get("label")))
            tgt = self._recipe_single_target(r)
            nr = None
            if ov and tgt:
                nr = ENG.recipe_for_target(self._retained_parcels(), deficit, tgt, target_condition=ov,
                                           losses=self._losses(), tree_density=self._tree_density,
                                           tree_size=self._tree_size, tree_condition=self._tree_condition,
                                           strategic_value=None, label=r.get("label"))
            if nr:
                nr["kind"] = r.get("kind", nr.get("kind"))
                nr["recommended"] = r.get("recommended", False)
                if r.get("why_not_top"):
                    nr["why_not_top"] = r["why_not_top"]
                out.append(nr)
            else:
                out.append(r)
        return out

    def _add_cond_row(self, lay, r, target):
        """Per-recipe target-condition picker. Changing it re-prices the card in
        place — the rank is fixed at the default condition so it won't jump — and
        feeds the off-site residual / report via the stored override."""
        conds = ENG.achievable_conditions(self._retained_parcels(), target) or []
        if len(conds) < 2:
            return
        cur = self._cond_override.get((self._module, r["label"]))
        if not cur:
            cur = next((s.get("target_condition") for s in r.get("steps", [])
                        if s.get("target") == target), None)
        row = QHBoxLayout(); row.setSpacing(6)
        row.addWidget(_lbl("Target condition:", _MUTED, 11, bold=True))
        cb = _combo(conds)
        if cur in conds:
            cb.setCurrentText(cur)
        cb.currentTextChanged.connect(lambda c, lbl=r["label"]: self._on_cond_override(lbl, c))
        row.addWidget(cb, 1)
        lay.addLayout(row)

    def _on_cond_override(self, label, cond):
        if not cond or self._cond_override.get((self._module, label)) == cond:
            return
        self._cond_override[(self._module, label)] = cond
        bar = self._scroll.verticalScrollBar(); pos = bar.value()
        self.refresh(); bar.setValue(pos)


    # ── parcel explorer view ──────────────────────────────────────────────────
    def _add_parcel_explorer(self, parcels):
        self._content.addWidget(_lbl("Your retained habitats — what each could become", _GREEN, 13, bold=True))
        if not parcels:
            self._content.addWidget(_lbl(
                "No retained habitats found — map a baseline and a proposed / red-line layer.", _MUTED, 12, wrap=True))
            return
        if any(ENG.can_plant_trees(p.get("habitat", "")) for p in parcels):
            self._add_density_row(self._content)
        for p in sorted(parcels, key=lambda q: -float(q.get("area_ha") or 0.0)):
            self._add_parcel_card(p)

    def _add_parcel_card(self, p):
        hab = p.get("habitat", ""); cond = (p.get("condition") or "").strip()
        area = float(p.get("area_ha") or 0.0)
        feas = ENG.host_feasibility(hab)
        card, lay = _card(greyed=(feas == "none"))
        lay.addWidget(_lbl("%s — %.2f ha" % (hab, area), _INK, 13, bold=True, wrap=True))
        lay.addWidget(_lbl("%s · %s distinctiveness" % (cond or "—", ENG.band_name(hab)), _MUTED, 10, wrap=True))
        if feas == "none":
            lay.addWidget(_lbl("Not enhanceable (sealed, water, rock or protected).", _AMBER, 10, wrap=True))
            self._content.addWidget(card); return
        if feas == "uplift":
            lay.addWidget(_lbl("Condition uplift only.", _MUTED, 10, wrap=True))
        moves = self._group_moves(ENG.moves_for_parcel(
            p, tree_density=self._tree_density, tree_size=self._tree_size, tree_condition=self._tree_condition,
            strategic_value=None))
        if not moves:
            lay.addWidget(_lbl("No move within 20 years.", _MUTED, 10, wrap=True))
        else:
            lay.addWidget(_hline())
            for mv in moves:                 # deduped to one row per target — show all
                self._add_move_row(lay, p, mv)
        self._content.addWidget(card)

    def _group_moves(self, moves):
        """One row per achievable outcome. First keep the best-net move per
        (kind, target) — so condition variants collapse and every target habitat
        (e.g. traditional orchard) shows once instead of being buried under
        several conditions of a higher-value habitat. Then merge interchangeable
        targets of equal value (e.g. the scrub types) into a single row."""
        best = {}
        for m in moves:
            k = (m["kind"], m["target"])
            if k not in best or (m.get("net_per_ha") or 0) > (best[k].get("net_per_ha") or 0):
                best[k] = m
        picks = sorted(best.values(), key=lambda m: m.get("net_per_ha") or -1e9, reverse=True)
        out, index = [], {}
        for m in picks:
            key = (m["kind"], m.get("route"), m.get("target_condition"),
                   round(m.get("net_per_ha") or 0.0, 3), str(m.get("years")))
            if key in index:
                g = index[key]
                if m["target"] not in g["targets"]:
                    g["targets"].append(m["target"])
            else:
                g = dict(m); g["targets"] = [m["target"]]
                index[key] = g; out.append(g)
        return out

    def _group_name(self, targets):
        if len(targets) == 1:
            return targets[0]
        if all(t.endswith("scrub") for t in targets):
            return "scrub (%s)" % " / ".join(t.replace(" scrub", "") for t in targets)
        return " / ".join(targets)

    def _add_move_row(self, lay, p, mv):
        area = float(p.get("area_ha") or 0.0)
        head = QHBoxLayout(); head.setSpacing(6)
        if mv["kind"] == "tree":
            label = ("Plant %d %s trees, %s (%d/ha)"
                     % (mv.get("max_trees", 0), mv.get("tree_size", "Small").lower(),
                        mv.get("tree_condition", "Poor").lower(), mv.get("density", ENG.TREE_DENSITY_SOFT)))
            full_units = mv.get("units", 0.0)
        else:
            tn = self._group_name(mv["targets"])
            if mv["kind"] == "uplift":
                label = "Uplift condition to %s" % mv.get("target_condition", "")
            else:
                verb = "Convert to" if mv["kind"] == "create" else "Change to"
                label = "%s %s (%s)" % (verb, tn, mv.get("target_condition", ""))
            full_units = (mv.get("net_per_ha") or 0.0) * area
        head.addWidget(_lbl("•  " + label, _INK, 12, bold=True, wrap=True))
        head.addStretch(1)
        rb = _ROUTE_CHIP.get(mv.get("route"))
        if rb:
            head.addWidget(T.chip(rb[0], rb[1]))
        lay.addLayout(head)
        gain = ("+%.1f%% net gain" % (full_units / self._baseline * 100.0)) if self._baseline > 0 \
            else ("+%.2f units" % full_units)
        yrs = mv.get("years")
        lay.addWidget(_lbl(gain + ((" · ≤%s yr" % yrs) if yrs is not None else ""), _MUTED, 12, wrap=True))

    # ════════════════════════════════════════════════════════════════════════
    # OFF-SITE (legacy, unchanged behaviour)
    # ════════════════════════════════════════════════════════════════════════
    # ── OFF-SITE (input-driven receptor — area is the OUTPUT) ──────────────────
    def _refresh_offsite(self):
        """Describe the receptor land you control; Biome returns the minimum to
        create / enhance there to close the gap (spatial risk + the receptor's own
        baseline loss accounted). Area: minimum area per move. Hedge/water:
        creation-led length recipes."""
        self._add_offsite_input_card()
        target = self._offsite_target()
        self._add_offsite_target_header(target)
        if target <= 1e-9:
            self._content.addWidget(_lbl(
                "No off-site units required for this module — the on-site gain already meets the target.",
                _MUTED, 12, wrap=True))
            return
        if self._module == "area":
            self._add_offsite_area_options(target)
        else:
            self._add_offsite_linear_options(target)

    def _offsite_target(self):
        """Units the receptor must deliver. 'full' = the whole module gap;
        'residual' = the gap minus the SELECTED on-site recipe's delivery. If no
        recipe is ticked we show the full gap and prompt for a selection (we do
        NOT silently borrow the recommended recipe)."""
        try:
            defs = self._panel.enhancement_module_deficits() or {}
        except Exception:
            defs = {}
        deficit = max(0.0, float(defs.get(self._module, 0.0)))
        self._residual_used = None
        if self._off_basis == "residual":
            used, lbl = self._onsite_chosen_recipe(self._module, deficit)
            self._residual_used = (used, lbl)
            if used is None:                 # nothing ticked on the On-site tab
                return deficit
            return max(0.0, deficit - used)
        return deficit

    def _onsite_chosen_recipe(self, module, deficit):
        """Delivery of the on-site recipe the user SELECTED for this module on the
        On-site tab. Returns (units_capped_at_deficit, label), or (None, None) if
        nothing is ticked — we do NOT fall back to the recommended recipe."""
        sel = self._sel_recipe.get(module)
        if not sel or deficit <= 1e-9:
            return None, None
        try:
            if module == "area":
                recs = ENG.build_recipes(
                    self._retained_parcels(), deficit, losses=self._losses(),
                    tree_density=self._tree_density, tree_size=self._tree_size,
                    tree_condition=self._tree_condition, strategic_value=None)
                spec = ENG.specialist_orchard_recipe(
                    self._retained_parcels(), deficit, losses=self._losses(), strategic_value=None)
                if spec:
                    recs = recs + [spec]
            else:
                feats = self._panel.enhancement_retained_linear(module) or []
                recs = ENG.linear_recipes(module, deficit, feats, strategic_value=None)
        except Exception:
            return None, None
        recs = self._apply_cond_overrides(recs, module, deficit)
        chosen = next((r for r in recs if r.get("label") == sel), None)
        if chosen is None:
            return None, None
        return min(deficit, float(chosen.get("total_units") or 0.0)), chosen.get("label")

    def _add_offsite_input_card(self):
        """The receptor: spatial-risk category (drives the metric SRM), and — for
        area — its existing habitat + condition (a creation destroys it). Plus the
        delivery basis. Irreplaceable is assumed No and never asked."""
        card, lay = _card()
        lay.addWidget(_lbl("The off-site receptor", _GREEN, 13, bold=True))
        sp = QHBoxLayout(); sp.setSpacing(6)
        sp.addWidget(_lbl("Spatial risk:", _MUTED, 11, bold=True))
        spc = _combo([s[0] for s in _SPATIAL])
        for i, s in enumerate(_SPATIAL):
            if s[2] == self._spatial:
                spc.setCurrentIndex(i); break
        spc.currentIndexChanged.connect(self._on_spatial_changed)
        sp.addWidget(spc, 1)
        lay.addLayout(sp)
        # Strategic significance — off-site is user-chosen (on-site derives it from
        # the baseline). The Check link sits on its OWN line so the row never runs
        # off the right of the dock.
        ssr = QHBoxLayout(); ssr.setSpacing(6)
        ssr.addWidget(_lbl("Strategic significance:", _MUTED, 11, bold=True))
        ssc = _combo([o[0] for o in _STRAT])
        for i, o in enumerate(_STRAT):
            if o[1] == self._strategic:
                ssc.setCurrentIndex(i); break
        ssc.currentIndexChanged.connect(self._on_strategic)
        ssr.addWidget(ssc, 1)
        lay.addLayout(ssr)
        chk = QPushButton("Check strategic significance  ↗")
        chk.setCursor(Qt.CursorShape.PointingHandCursor)
        chk.setToolTip("Open your site authority's Local Nature Recovery Strategy / "
                       "local plan map to confirm strategic significance — High if "
                       "formally identified, Medium if ecologically desirable, else Low.")
        chk.setStyleSheet(
            "QPushButton{background:transparent; color:%s; border:none; font-family:%s;"
            " font-size:11px; text-decoration:underline; padding:0 2px;}"
            "QPushButton:hover{color:%s;}" % (T.ACCENT, T.FONT_MEDIUM, T.ACCENT_DARK))
        chk.clicked.connect(self._check_strategic)
        clr = QHBoxLayout(); clr.setContentsMargins(0, 0, 0, 0); clr.setSpacing(0)
        clr.addWidget(chk); clr.addStretch(1)
        lay.addLayout(clr)
        if self._module == "area":
            hh = QHBoxLayout(); hh.setSpacing(6)
            hh.addWidget(_lbl("Existing habitat:", _MUTED, 11, bold=True))
            hc = _combo(_AREA_HABITATS)
            if self._off_habitat in _AREA_HABITATS:
                hc.setCurrentText(self._off_habitat)
            else:
                self._off_habitat = hc.currentText()
            hc.currentTextChanged.connect(self._on_off_habitat)
            hh.addWidget(hc, 1)
            lay.addLayout(hh)
            ch = QHBoxLayout(); ch.setSpacing(6)
            ch.addWidget(_lbl("Current condition:", _MUTED, 11, bold=True))
            cc = _combo(_CONDITIONS + ["Condition Assessment N/A"])
            cc.setCurrentText(self._off_condition)
            cc.currentTextChanged.connect(self._on_off_condition)
            ch.addWidget(cc, 1)
            lay.addLayout(ch)
            # Irreplaceable — the metric requires it on the D-1 receptor baseline
            # (a blank shows "Confirm irreplaceable habitat status" and won't score).
            # Almost always No; offered so the value is explicit, never blank.
            ir = QHBoxLayout(); ir.setSpacing(6)
            ir.addWidget(_lbl("Irreplaceable:", _MUTED, 11, bold=True))
            ic = _combo(["No", "Yes"])
            ic.setCurrentText(self._off_irreplaceable if self._off_irreplaceable in ("No", "Yes") else "No")
            ic.setToolTip("Almost always No — you cannot create biodiversity units on irreplaceable "
                          "land, so a receptor is not irreplaceable. Set so the metric never sees a blank.")
            ic.currentTextChanged.connect(self._on_off_irreplaceable)
            ir.addWidget(ic, 1)
            lay.addLayout(ir)
        elif self._module == "watercourse":
            # Off-site watercourse is creation-led (a new reach). The metric needs
            # the created reach's channel + bank encroachment to score it, so ask
            # for them here (defaulted to "No Encroachment", never left blank).
            lay.addWidget(_lbl(
                "Off-site watercourses are creation-led (a new reach), so no receptor "
                "baseline is needed. Set the new reach's encroachment below — the metric "
                "needs it to score the reach.", _MUTED, 10, wrap=True))
            for _label, _opts, _cur, _handler in (
                    ("Channel encroachment:", _WC_ENC_OPTIONS, self._off_wc_enc, self._on_off_wc_enc),
                    ("Left bank encroachment:", _RIP_ENC_OPTIONS, self._off_rip_l, self._on_off_rip_l),
                    ("Right bank encroachment:", _RIP_ENC_OPTIONS, self._off_rip_r, self._on_off_rip_r)):
                er = QHBoxLayout(); er.setSpacing(6)
                er.addWidget(_lbl(_label, _MUTED, 11, bold=True))
                ec = _combo(_opts)
                ec.setCurrentText(_cur if _cur in _opts else _opts[0])
                ec.currentTextChanged.connect(_handler)
                er.addWidget(ec, 1)
                lay.addLayout(er)
        else:
            lay.addWidget(_lbl(
                "Off-site %ss are creation-led (an existing off-site reach to enhance "
                "is rare), so no receptor baseline is needed." % self._module, _MUTED, 10, wrap=True))
        bt = QHBoxLayout(); bt.setSpacing(6)
        bt.addWidget(_lbl("Cover:", _MUTED, 11, bold=True))
        full_b = _btn("Full gap", primary=(self._off_basis == "full"))
        res_b = _btn("Residual after on-site", primary=(self._off_basis == "residual"))
        full_b.clicked.connect(lambda: self._on_off_basis("full"))
        res_b.clicked.connect(lambda: self._on_off_basis("residual"))
        bt.addWidget(full_b); bt.addWidget(res_b); bt.addStretch(1)
        lay.addLayout(bt)
        self._content.addWidget(card)

    def _add_offsite_target_header(self, target):
        card, lay = _card()
        head = QHBoxLayout()
        head.addWidget(_lbl("Off-site units required", _GREEN, 13, bold=True))
        head.addStretch(1)
        head.addWidget(_lbl("%.2f units" % target, _GREEN, 16, bold=True))
        lay.addLayout(head)
        if self._off_basis == "residual":
            used, lbl = getattr(self, "_residual_used", None) or (None, None)
            if used is not None and lbl:
                lay.addWidget(_lbl("After on-site recipe “%s” (delivers %.2f units on-site) — change it "
                                   "by selecting a different recipe on the On-site tab."
                                   % (lbl, used), _MUTED, 10, wrap=True))
            else:
                lay.addWidget(_lbl("No on-site recipe selected — showing the full gap. Tick a recipe on "
                                   "the On-site tab to subtract it.", _AMBER, 10, wrap=True))
            lay.addWidget(_lbl("Priced at spatial risk ×%.2f." % self._spatial, _MUTED, 10, wrap=True))
        else:
            lay.addWidget(_lbl("Covering the full module gap, priced at spatial risk ×%.2f."
                               % self._spatial, _MUTED, 10, wrap=True))
        self._content.addWidget(card)

    def _add_offsite_area_options(self, target):
        self._content.addWidget(_lbl("Minimum receptor area — least first", _GREEN, 13, bold=True))
        try:
            opts = ENG.offsite_options(
                self._off_habitat, self._off_condition, target,
                strategic_value=self._strategic, spatial_risk=self._spatial,
                tree_density=self._tree_density, tree_size=self._tree_size,
                tree_condition=self._tree_condition, losses=self._losses())
        except Exception as e:
            self._content.addWidget(_lbl("Could not compute options: %s" % e, _AMBER, 12, wrap=True)); return
        if not opts:
            self._content.addWidget(_lbl(
                "No viable move from “%s” — it can't be enhanced or created on (e.g. "
                "sealed, irreplaceable, or already high-value). Pick a different "
                "receptor habitat." % self._off_habitat, _MUTED, 12, wrap=True))
            return
        for o in opts[:8]:
            self._add_offsite_option_card(o)

    def _add_offsite_option_card(self, o):
        card, lay = _card()
        if o["kind"] == "uplift":
            head = "Enhance %s → %s condition" % (o["base_habitat"], o["target_condition"])
        elif o["kind"] == "change":
            head = "Enhance %s → %s (%s)" % (o["base_habitat"], o["target"], o["target_condition"])
        elif o["kind"] == "tree":
            head = "Plant %s trees on %s" % (o.get("tree_size", "Small").lower(), o["base_habitat"])
        else:
            head = "Create %s (%s)" % (o["target"], o["target_condition"])
        top = QHBoxLayout()
        top.addWidget(_lbl(head, _INK, 13, bold=True, wrap=True))
        top.addStretch(1)
        if o.get("covers_currencies", True):
            top.addWidget(self._status_icon("status-check", T.GOOD,
                          "Satisfies the loss's trading currency"), 0, Qt.AlignmentFlag.AlignTop)
        else:
            top.addWidget(self._status_icon("status-alert", T.BAD,
                          "Won't satisfy the loss's trading currency on its own"), 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        lay.addWidget(_lbl("Minimum area: %.3f ha" % o["min_area_ha"], _GREEN_MID, 13, bold=True))
        yrs = (" · ≤%s yr to target" % o["years"]) if o.get("years") is not None else ""
        lay.addWidget(_lbl("%.2f units delivered · %.2f units/ha%s"
                           % (o["units"], o["net_per_ha"], yrs), _MUTED, 12, wrap=True))
        if not o.get("covers_currencies", True):
            lay.addWidget(_lbl(
                "⚠  Lower distinctiveness than the loss — won't meet the trading rules on its own.",
                _AMBER, 10, wrap=True))
        if o["kind"] == "tree" and o.get("trees"):
            lay.addWidget(_lbl("≈ %d %s trees in %s condition at %d/ha"
                               % (o["trees"], o.get("tree_size", "Small").lower(),
                                  o.get("tree_condition", "Poor").lower(), o.get("tree_density", 50)),
                               _INK, 12, bold=True, wrap=True))
        if o.get("note"):
            lay.addWidget(_lbl(o["note"], _MUTED, 10, wrap=True))
        exp = _btn("Export to metric (off-site) ⤓")
        exp.setToolTip("Write this receptor + action into the metric's off-site D sheets, "
                       "with the spatial-risk category applied.")
        exp.clicked.connect(lambda _=False, opt=dict(o): self._export_recipe(self._offsite_area_payload(opt)))
        lay.addWidget(exp)
        self._content.addWidget(card)

    def _offsite_area_payload(self, o):
        """Turn a flat off-site AREA option into the off-site export recipe the
        exporter's _build_offsite_plan consumes (a single receptor step)."""
        step = {"kind": o.get("kind", ""), "target": o.get("target", ""),
                "target_condition": o.get("target_condition", ""),
                "area_ha": float(o.get("min_area_ha") or 0.0),
                "strategic": o.get("strategic", self._strategic)}
        if o.get("kind") == "tree":
            step["trees"] = int(o.get("trees") or 0)
            step["tree_size"] = o.get("tree_size", "Small")
            step["tree_condition"] = o.get("tree_condition", "Poor")
        return {"offsite": True, "module": "area",
                "spatial_cat": self._spatial_cat, "spatial_risk": self._spatial,
                "strategic": self._strategic,
                "receptor_habitat": self._off_habitat, "receptor_condition": self._off_condition,
                "receptor_irreplaceable": self._off_irreplaceable or "No",
                "steps": [step],
                "label": o.get("label") or "Off-site receptor action"}

    def _add_offsite_linear_options(self, target):
        # Off-site hedge / watercourse — creation-led (an existing off-site reach
        # to enhance is rare). Reuses the linear engine with spatial risk applied.
        self._content.addWidget(_lbl("Suggested off-site %ss — least length first" % self._module,
                                     _GREEN, 13, bold=True))
        try:
            recipes = ENG.linear_recipes(self._module, target, [], strategic_value=self._strategic,
                                         spatial_risk=self._spatial)
        except Exception as e:
            self._content.addWidget(_lbl("Could not build recipes: %s" % e, _AMBER, 12, wrap=True)); return
        if not recipes:
            self._content.addWidget(_lbl(
                "No off-site %s creation options yet — watercourse off-site creation "
                "is being wired in the next step." % self._module, _MUTED, 12, wrap=True))
            return
        for r in recipes:
            # Off-site linear export only supports CREATION-led recipes (the
            # exporter writes E-2 / F-2 creation rows). Skip any uplift/upgrade
            # recipe so it can't be exported to an empty (zero-unit) off-site plan.
            if any(s.get("kind") != "create" for s in r.get("steps", [])):
                continue
            # Tag as off-site so the shared card's "Export to metric" button routes
            # this recipe into the off-site E/F sheets (with the spatial-risk
            # category), instead of the on-site B/C sheets.
            r["offsite"] = True
            r["module"] = self._module
            r["spatial_cat"] = self._spatial_cat
            r["spatial_risk"] = self._spatial
            r["strategic"] = self._strategic
            if self._module == "watercourse":
                # Carry the new reach's encroachment (channel + combined banks) so
                # the F-2 row is never written blank.
                r["watercourse_encroachment"] = self._off_wc_enc
                r["riparian_encroachment_lr"] = ENG.BM.canonical_riparian_lr(
                    self._off_rip_l, self._off_rip_r)
            self._add_recipe_card_v2(r, target)

    def _on_off_habitat(self, text):
        if text and text != self._off_habitat:
            self._off_habitat = text
            bar = self._scroll.verticalScrollBar(); pos = bar.value()
            self.refresh(); bar.setValue(pos)

    def _on_off_condition(self, text):
        if text and text != self._off_condition:
            self._off_condition = text
            bar = self._scroll.verticalScrollBar(); pos = bar.value()
            self.refresh(); bar.setValue(pos)

    # Irreplaceable / encroachment only affect what's WRITTEN to the metric, not
    # which recipes are shown, so they just store state (no rebuild / scroll jump).
    def _on_off_irreplaceable(self, text):
        self._off_irreplaceable = text or "No"

    def _on_off_wc_enc(self, text):
        self._off_wc_enc = text or "No Encroachment"

    def _on_off_rip_l(self, text):
        self._off_rip_l = text or "No Encroachment"

    def _on_off_rip_r(self, text):
        self._off_rip_r = text or "No Encroachment"

    def _on_off_basis(self, basis):
        if basis != self._off_basis:
            self._off_basis = basis
            self.refresh()

    # ── MARKET & CREDITS (buy the shortfall) ───────────────────────────────────
    def _refresh_market(self):
        modules = self._report_modules()
        if not modules:
            card, lay = _card()
            lay.addWidget(_lbl("The scheme achieves the mandatory 10% biodiversity net gain target — "
                               "no further biodiversity units are required.", _MUTED, 12, wrap=True))
            self._content.addWidget(card)
            return
        self._add_market_view_toggle()
        if self._market_view == "selected":
            if any(m.get("recommended") for m in modules):
                self._add_selected_table(modules)
                self._add_credits_cost(
                    self._excess_credits(modules),
                    "Statutory credits — residual after the selected action",
                    "The excess remaining once the selected on-site action is delivered "
                    "(two credits per unit).")
            else:
                self._content.addWidget(_lbl(
                    "No on-site recipe selected — tick one on the On-site tab to see the requirement "
                    "after your selection.", _AMBER, 12, wrap=True))
        else:
            self._add_full_table(modules)
            self._add_credits_cost(
                self._panel_credits(),
                "Statutory credits — full requirement",
                "Government last-resort purchase if nothing is delivered on-site or off-site "
                "(two credits per unit).")

    def _add_full_table(self, modules):
        """The shopping list: how many units to secure off-site / from a habitat
        bank, and at what distinctiveness.

        Every figure here is a DEFICIT (what is still missing once the proposal's
        own creation and enhancement have been counted), never a gross baseline
        loss — the two differ by whatever the scheme builds back, and quoting the
        gross loss overstates the buy. Rows sum to the module total below.
        """
        headers = ["Module", "Distinctiveness needed", "What to secure", "Units"]
        rows, bold_rows, splits = [], [], []
        exceeds = False
        for m in modules:
            mod = m["key"].capitalize()
            req = m.get("requirement") or {}
            band_rows = req.get("rows") or []
            if not band_rows:
                # Trading cascade unavailable — fall back to the gross losses,
                # labelled as such so they are never read as a purchase quantity.
                for L in self._losses(include_trees=True):
                    rows.append([mod, _replacement_rule(L.get("band"), L.get("group")),
                                 "%s — gross loss, not netted" % str(L.get("habitat", "")).lower(),
                                 "%.2f" % float(L.get("units") or 0.0)])
                continue
            for r in band_rows:
                if r.get("kind") == "floor":
                    what = ", ".join(r.get("habitats") or []).lower() or "net loss"
                    rule = _replacement_rule(r.get("band"), r.get("group"))
                else:
                    what = "any habitat — free choice"
                    rule = _replacement_rule(r.get("band"), "")
                    bold_rows.append(len(rows))
                rows.append([mod, rule, what, "%.2f" % r["units"]])
            sf, tu = float(req.get("shortfall") or 0.0), float(req.get("topup") or 0.0)
            if sf > 5e-3 or tu > 5e-3:
                splits.append("%s: %.2f to get back to the baseline, then %.2f more "
                              "for the mandatory 10%%." % (mod, sf, tu))
            exceeds = exceeds or bool(req.get("exceeds"))
        if rows:
            note = ("Buy these and the requirement is met — the rows sum to the total below. "
                    "A row naming a broad habitat group is a trading-rule floor: the metric "
                    "will not let any other habitat cover that loss, so it has to be secured "
                    "in that group (or traded up). The free-choice row can be any habitat at "
                    "the stated distinctiveness or better.")
            if splits:
                note += "  " + "  ".join(splits)
            if exceeds:
                note += ("  The trading-rule floors alone come to more than the 10% net gain "
                         "requires, so the floors are what has to be secured.")
            self._add_table_card("Units to secure, by distinctiveness — full requirement",
                                 headers, rows, note=note, bold_rows=bold_rows)
        trows = []
        for m in modules:
            req = m.get("requirement") or {}
            trows.append([m["key"].capitalize(),
                          "%.2f" % float(req.get("total") or m["total"])])
        self._add_table_card("Total required (incl. 10% net gain)", ["Module", "Units"], trows)

    def _add_selected_table(self, modules):
        actions = [m["recommended"] for m in modules if m.get("recommended")]
        if actions:
            self._content.addWidget(_lbl("Selected on-site action — " + "; ".join(actions) + ".",
                                         _INK, 12, wrap=True))
        headers = ["Module", "Full", "Delivered on-site", "Residual to secure", "Residual distinctiveness"]
        rows = []
        for m in modules:
            mod = m["key"].capitalize()
            deliv = ("%.2f" % m["delivers"]) if m.get("recommended") else "—"
            resid = ("%.2f" % m["residual"]) if m.get("recommended") else ("%.2f" % m["total"])
            rows.append([mod, "%.2f" % m["total"], deliv, resid,
                         _DISTINCT_LABEL.get(m.get("base_tier", ""), "—")])
        self._add_table_card("Requirement after the selected on-site action", headers, rows)

    def _add_table_card(self, title, headers, rows, note=None, bold_rows=None):
        bold_rows = set(bold_rows or [])
        card, lay = _card()
        lay.addWidget(_lbl(title, _GREEN, 13, bold=True))
        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(3)
        for c, h in enumerate(headers):
            grid.addWidget(_lbl(h, _INK, 12, bold=True, wrap=True), 0, c)
        for ri, row in enumerate(rows, start=1):
            rb = (ri - 1) in bold_rows
            for c, cell in enumerate(row):
                grid.addWidget(_lbl(str(cell), _INK, 11, bold=rb, wrap=True), ri, c)
        lay.addLayout(grid)
        if note:
            lay.addWidget(_lbl(note, _MUTED, 10, wrap=True))
        bl = QHBoxLayout()
        cp = _btn("Copy table")
        cp.clicked.connect(lambda _=False, h=list(headers), r=[list(x) for x in rows], b=sorted(bold_rows):
                           self._copy_table(h, r, b))
        bl.addWidget(cp)
        from . import credit_prices as CP
        link = QPushButton("Find off-site units / habitat banks  ↗"); link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setStyleSheet("QPushButton{background:transparent; color:%s; border:none; font-family:%s;"
                           " font-size:11px; text-decoration:underline; padding:0 8px;}"
                           "QPushButton:hover{color:%s;}" % (T.ACCENT, T.FONT_MEDIUM, T.ACCENT_DARK))
        link.clicked.connect(lambda _=False, u=CP.OFFSITE_UNITS_URL: self._open(u))
        bl.addWidget(link); bl.addStretch(1)
        lay.addLayout(bl)
        self._content.addWidget(card)

    def _copy_table(self, headers, rows, bold_rows=None):
        bold = set(bold_rows or [])
        def _esc(s):
            return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        def _cell(c, strong):
            v = _esc(c)
            return "<td style=\"border:1px solid #888;padding:5px\">%s</td>" % (
                ("<strong>%s</strong>" % v) if strong else v)
        h = ["<table style=\"border-collapse:collapse\" border=\"1\" cellpadding=\"5\">"]
        h.append("<tr>" + "".join("<th style=\"border:1px solid #888;padding:5px;text-align:left\">%s</th>"
                                  % _esc(x) for x in headers) + "</tr>")
        for i, r in enumerate(rows):
            h.append("<tr>" + "".join(_cell(c, i in bold) for c in r) + "</tr>")
        h.append("</table>")
        html = "".join(h)
        tsv = "\t".join(str(x) for x in headers) + "\n" + "\n".join(
            "\t".join(str(c) for c in r) for r in rows)
        try:
            from qgis.PyQt.QtCore import QMimeData
            from qgis.PyQt.QtWidgets import QApplication
            md = QMimeData(); md.setHtml(html); md.setText(tsv)
            QApplication.clipboard().setMimeData(md)
            self._panel.iface.messageBar().pushMessage(
                "Biome", "Table copied — paste into Google Docs.", level=Qgis.MessageLevel.Info, duration=4)
        except Exception:
            pass

    def _add_market_view_toggle(self):
        w = QWidget(); row = QHBoxLayout(w); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(2)
        full = QPushButton("Full requirement"); full.setStyleSheet(_viewtab_style(self._market_view == "full"))
        sel = QPushButton("With selected enhancements"); sel.setStyleSheet(_viewtab_style(self._market_view == "selected"))
        full.clicked.connect(lambda: self._set_market_view("full"))
        sel.clicked.connect(lambda: self._set_market_view("selected"))
        row.addWidget(full); row.addWidget(sel); row.addStretch(1)
        self._content.addWidget(w)

    def _set_market_view(self, v):
        if v != self._market_view:
            self._market_view = v
            self.refresh()

    def _panel_credits(self):
        try:
            return self._panel.enhancement_credits() or {}
        except Exception:
            return {}

    def _excess_credits(self, modules):
        """{tier: credits} remaining after the selected on-site actions — each
        module's residual credits where a recipe is ticked, else its full credits."""
        out = {}
        for m in modules:
            pairs = m.get("residual_credits", []) if m.get("recommended") else m.get("credits", [])
            for t, c in pairs:
                out[t] = out.get(t, 0.0) + c
        return out

    def _report_modules(self):
        """Per-module requirement data for the report generator — only modules with
        a shortfall. Keys match enhancement_engine.report_paragraphs()."""
        try:
            defs = self._panel.enhancement_module_deficits() or {}
        except Exception:
            defs = {}
        try:
            credits = self._panel.enhancement_credits() or {}
        except Exception:
            credits = {}
        # Trading-rule requirement per module (deficit rows, already netted
        # against everything the proposal delivers). "losses" below is derived
        # from these floors, NOT from gross baseline losses — quoting a gross
        # loss here would overstate both the table and the report prose by
        # whatever the scheme builds back.
        try:
            reqs = self._panel.enhancement_band_requirements() or {}
        except Exception:
            reqs = {}
        META = [
            ("area", "area habitat units", "area habitats", ["A1", "A2", "A3", "A4", "A5"], "A1"),
            ("hedgerow", "hedgerow units", "hedgerow", ["H"], "H"),
            ("watercourse", "watercourse units", "watercourse", ["W"], "W"),
        ]
        out = []
        for key, unit_noun, noun, tiers, base_tier in META:
            total = max(0.0, float(defs.get(key, 0.0)))
            if total <= 1e-9:
                continue
            m = {"key": key, "unit_noun": unit_noun, "noun": noun, "base_tier": base_tier,
                 "total": round(total, 2)}
            req = reqs.get(key) or {}
            m["requirement"] = req
            losses = []
            for r in req.get("rows", []):
                if r.get("kind") != "floor":
                    continue          # free-choice units replace no named habitat
                u = round(float(r.get("units") or 0.0), 2)
                if u > 0:
                    losses.append({"habitat": ", ".join(r.get("habitats") or []).lower(),
                                   "condition": "",
                                   "band": str(r.get("band", "")).lower(),
                                   # keep original case — it's shown to the ecologist
                                   "group": str(r.get("group", "")),
                                   "units": u})
            m["losses"] = losses
            m["credits"] = [(t, round(float(credits.get(t, 0.0) or 0.0), 2))
                            for t in tiers if float(credits.get(t, 0.0) or 0.0) > 1e-9]
            used, action, closes = self._recommended_action(key, total)
            if action:
                m["recommended"] = action
                m["delivers"] = round(used, 2)
                m["closes"] = bool(closes)
                resid = round(max(0.0, total - used), 2)
                m["residual"] = resid
                m["residual_credits"] = [(base_tier, round(resid * 2, 2))] if resid > 1e-9 else []
            else:
                m["recommended"] = None
            out.append(m)
        return out

    def _recommended_action(self, module, deficit):
        """(units_delivered, action_phrase, closes) for the SELECTED on-site recipe
        of this module, or (0.0, None, False) if none is ticked."""
        sel = self._sel_recipe.get(module)
        if not sel or deficit <= 1e-9:
            return 0.0, None, False
        try:
            if module == "area":
                recs = ENG.build_recipes(self._retained_parcels(), deficit, losses=self._losses(),
                                         tree_density=self._tree_density, tree_size=self._tree_size,
                                         tree_condition=self._tree_condition, strategic_value=None)
                spec = ENG.specialist_orchard_recipe(
                    self._retained_parcels(), deficit, losses=self._losses(), strategic_value=None)
                if spec:
                    recs = recs + [spec]
            else:
                feats = self._panel.enhancement_retained_linear(module) or []
                recs = ENG.linear_recipes(module, deficit, feats, strategic_value=None)
        except Exception:
            return 0.0, None, False
        recs = self._apply_cond_overrides(recs, module, deficit)
        r = next((x for x in recs if x.get("label") == sel), None)
        if not r:
            return 0.0, None, False
        return min(deficit, float(r.get("total_units") or 0.0)), self._action_phrase(r), bool(r.get("closes_gap"))

    def _action_phrase(self, r):
        """Third-person prose for a recipe's steps, e.g. 'converts 0.20 ha of
        modified grassland (poor condition) to other neutral grassland (good
        condition)' — joined for multi-step recipes."""
        parts = []
        for s in r.get("steps", []):
            kind = s.get("kind")
            if kind == "tree":
                parts.append("plants %d %s trees in %s condition"
                             % (int(s.get("trees", 0) or 0), str(s.get("tree_size", "small")).lower(),
                                str(s.get("tree_condition", "poor")).lower()))
            elif s.get("km") is not None:
                tc = str(s.get("target_condition", "")).lower()
                parts.append("plants approximately %d m of %s%s"
                             % (round(float(s.get("km", 0) or 0) * 1000), str(s.get("target", "")).lower(),
                                (" (%s condition)" % tc) if tc else ""))
            elif kind == "uplift":
                parts.append("enhances %.2f ha of %s (%s condition) to %s condition"
                             % (float(s.get("area_ha", 0) or 0), str(s.get("parcel_habitat", "")).lower(),
                                str(s.get("parcel_condition", "")).lower(), str(s.get("target_condition", "")).lower()))
            else:
                fc = str(s.get("parcel_condition", "")).lower(); tc = str(s.get("target_condition", "")).lower()
                ph = ("converts %.2f ha of %s%s to %s%s"
                      % (float(s.get("area_ha", 0) or 0), str(s.get("parcel_habitat", "")).lower(),
                         (" (%s condition)" % fc) if fc else "", str(s.get("target", "")).lower(),
                         (" (%s condition)" % tc) if tc else ""))
                if kind == "create+tree" and s.get("trees"):
                    ph += " with %d %s trees" % (int(s.get("trees", 0)), str(s.get("tree_size", "small")).lower())
                parts.append(ph)
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " and " + parts[-1]

    def _add_report_summary(self, modules):
        rep = ENG.report_paragraphs(modules)
        self._add_report_card("Report summary — full requirement", rep["full_gap"])
        if rep.get("residual"):
            self._add_report_card("Report summary — with the recommended on-site action", rep["residual"])
        elif modules:
            self._content.addWidget(_lbl(
                "Tick a recipe on the On-site tab to add a recommended-action / residual paragraph.",
                _MUTED, 10, wrap=True))

    def _add_report_card(self, title, text):
        card, lay = _card()
        lay.addWidget(_lbl(title, _GREEN, 13, bold=True))
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("color:%s; font-size:12px; font-family:%s; background:transparent; border:none;"
                           % (_INK, T.FONT))
        lay.addWidget(body)
        cp = _btn("Copy")
        cp.clicked.connect(lambda _=False, t=text: self._copy(t))
        row = QHBoxLayout(); row.addWidget(cp); row.addStretch(1)
        lay.addLayout(row)
        self._content.addWidget(card)

    def _add_market_figures(self, modules, residual=False):
        from . import credit_prices as CP
        card, lay = _card()
        lay.addWidget(_lbl("Off-site units — residual after selection" if residual else "Off-site units required",
                           _GREEN, 13, bold=True))
        shown = False
        for m in modules:
            u = m["residual"] if (residual and m.get("recommended")) else m["total"]
            if u <= 1e-9:
                continue
            shown = True
            lay.addWidget(_lbl("%s: %.2f units" % (m["noun"][:1].upper() + m["noun"][1:], u),
                               _INK, 12, wrap=True))
        if not shown:
            lay.addWidget(_lbl("None — met in full on-site." if residual
                               else "No shortfall — the scheme reaches the mandatory net gain.",
                               _MUTED, 12, wrap=True))
        b = QPushButton("Find off-site units / habitat banks  ↗"); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet("QPushButton{background:transparent; color:%s; border:none; font-family:%s;"
                        " font-size:11px; text-decoration:underline; padding:0 2px;}"
                        "QPushButton:hover{color:%s;}" % (T.ACCENT, T.FONT_MEDIUM, T.ACCENT_DARK))
        b.clicked.connect(lambda _=False, u=CP.OFFSITE_UNITS_URL: self._open(u))
        r = QHBoxLayout(); r.setContentsMargins(0, 0, 0, 0); r.addWidget(b); r.addStretch(1)
        lay.addLayout(r)
        self._content.addWidget(card)

    def _copy(self, text):
        try:
            from qgis.PyQt.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self._panel.iface.messageBar().pushMessage(
                "Biome", "Report text copied to clipboard.", level=Qgis.MessageLevel.Info, duration=4)
        except Exception:
            pass

    def _add_credits_cost(self, credits, title, note=None):
        from . import credit_prices as CP
        card, lay = _card()
        lay.addWidget(_lbl(title, _GREEN, 13, bold=True))
        if note:
            lay.addWidget(_lbl(note, _MUTED, 10, wrap=True))
        prices, source = CP.live_prices()
        total, any_needed = 0.0, False
        for t in ("A1", "A2", "A3", "A4", "A5", "H", "W"):
            cr = float((credits or {}).get(t, 0.0) or 0.0)
            if cr <= 1e-9:
                continue
            any_needed = True
            price = prices.get(t, 0)
            cost = cr * price
            total += cost
            lay.addWidget(_lbl("Tier %s:  %.2f credits × £%s/credit = £%s"
                               % (t, cr, _fmt(price), _fmt(cost)), _INK, 12, wrap=True))
        if not any_needed:
            lay.addWidget(_lbl("None required.", _MUTED, 12, wrap=True))
        else:
            lay.addWidget(_hline())
            lay.addWidget(_lbl("Total:  £%s" % _fmt(total), _GREEN_MID, 14, bold=True))
        lay.addWidget(_lbl("Prices: %s." % source, _MUTED, 9, wrap=True))
        link = QPushButton("Check gov.uk prices  ↗")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setStyleSheet("QPushButton{background:transparent; color:%s; border:none; font-family:%s;"
                           " font-size:11px; text-decoration:underline; padding:0 2px;}"
                           "QPushButton:hover{color:%s;}" % (T.ACCENT, T.FONT_MEDIUM, T.ACCENT_DARK))
        link.clicked.connect(lambda: self._open(CP.PRICES_URL))
        lr = QHBoxLayout(); lr.setContentsMargins(0, 0, 0, 0); lr.addWidget(link); lr.addStretch(1)
        lay.addLayout(lr)
        self._content.addWidget(card)

    def _add_bank_panel(self):
        from . import credit_prices as CP
        card, lay = _card()
        lay.addWidget(_lbl("Off-site units from a habitat bank", _GREEN, 13, bold=True))
        lay.addWidget(_lbl(
            "Indicative — habitat-bank prices vary by provider, region and distinctiveness, so set your "
            "own £/unit per band. Units bought OUTSIDE the impact site's LPA / NCA are discounted by "
            "spatial risk, so you'd buy more than the gap; inside (×1.0) you buy the gap exactly.",
            _MUTED, 10, wrap=True))
        sp = QHBoxLayout(); sp.setSpacing(6)
        sp.addWidget(_lbl("Spatial risk:", _MUTED, 11, bold=True))
        spc = _combo([s[0] for s in _SPATIAL])
        for i, s in enumerate(_SPATIAL):
            if s[2] == self._bank_spatial:
                spc.setCurrentIndex(i); break
        spc.currentIndexChanged.connect(self._on_bank_spatial)
        sp.addWidget(spc, 1)
        lay.addLayout(sp)
        # Per-band unit need = credits / 2 (credits_engine credits are shortfall×2).
        # Trading is respected: each band must be met by its own (or higher)
        # distinctiveness units, so each is priced separately.
        try:
            credits = self._panel.enhancement_credits() or {}
        except Exception:
            credits = {}
        srm = self._bank_spatial if self._bank_spatial > 0 else 1.0
        labels = {"A1": "Area — low / medium distinctiveness (A1)",
                  "A2": "Area — high distinctiveness (A2)",
                  "A3": "Area — high distinctiveness (A3)",
                  "A4": "Area — high distinctiveness (A4)",
                  "A5": "Area — very high / lakes (A5)",
                  "H":  "Hedgerow", "W": "Watercourse"}
        total, any_needed = 0.0, False
        for t in ("A1", "A2", "A3", "A4", "A5", "H", "W"):
            need = float(credits.get(t, 0.0) or 0.0) / 2.0     # credits = unit shortfall × 2
            if need <= 1e-9:
                continue
            any_needed = True
            price = float(self._bank_prices.get(t, self._bank_price))
            buy = need / srm
            cost = buy * price
            total += cost
            row = QHBoxLayout(); row.setSpacing(6)
            row.addWidget(_lbl(labels[t], _INK, 12, bold=True, wrap=True), 1)
            spin = self._price_spin(price)
            spin.editingFinished.connect(lambda s=spin, tt=t: self._on_bank_band_price(tt, s.value()))
            row.addWidget(spin)
            lay.addLayout(row)
            lay.addWidget(_lbl("%.2f units needed → buy %.2f (÷ ×%.2f) × £%s/unit = £%s"
                               % (need, buy, srm, _fmt(price), _fmt(cost)), _MUTED, 10, wrap=True))
        if not any_needed:
            lay.addWidget(_lbl("No off-site units required — the scheme already reaches net gain "
                               "(or no metric data is loaded).", _MUTED, 12, wrap=True))
        else:
            lay.addWidget(_hline())
            lay.addWidget(_lbl("Indicative habitat-bank cost:  £%s" % _fmt(total), _GREEN_MID, 14, bold=True))
        link = QPushButton("Find off-site units / habitat banks  ↗")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setToolTip("gov.uk guidance on sourcing off-site biodiversity units. The market is private "
                        "(no single national finder) — your LPA can also signpost local habitat banks.")
        link.setStyleSheet("QPushButton{background:transparent; color:%s; border:none; font-family:%s;"
                           " font-size:11px; text-decoration:underline; padding:0 2px;}"
                           "QPushButton:hover{color:%s;}" % (T.ACCENT, T.FONT_MEDIUM, T.ACCENT_DARK))
        link.clicked.connect(lambda: self._open(CP.OFFSITE_UNITS_URL))
        lr = QHBoxLayout(); lr.setContentsMargins(0, 0, 0, 0); lr.addWidget(link); lr.addStretch(1)
        lay.addLayout(lr)
        self._content.addWidget(card)

    def _price_spin(self, value):
        """£/unit spin box with clear +/- stepper buttons (plus.svg / minus.svg).
        The dock QSS leaves the default arrows blank, so we style the buttons
        explicitly — same pattern as the feature-placer dialog."""
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1e7); spin.setDecimals(0); spin.setSingleStep(1000.0)
        spin.setGroupSeparatorShown(True); spin.setValue(float(value))
        spin.setMaximumWidth(160)
        icons = os.path.join(os.path.dirname(__file__), "icons")
        plus = os.path.join(icons, "plus.svg").replace("\\", "/")
        minus = os.path.join(icons, "minus.svg").replace("\\", "/")
        spin.setStyleSheet(
            "QDoubleSpinBox{padding-right:24px;}"
            "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button{subcontrol-origin:border;"
            " width:21px; background:%s; border-left:1px solid %s;}" % (T.SUNKEN, T.BORDER_STRONG)
            + "QDoubleSpinBox::up-button{subcontrol-position:top right; border-top-right-radius:6px;}"
            + "QDoubleSpinBox::down-button{subcontrol-position:bottom right; border-bottom-right-radius:6px;}"
            + "QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover{background:%s;}" % T.ACCENT_SOFT
            + "QDoubleSpinBox::up-arrow{image:url(%s); width:10px; height:10px;}" % plus
            + "QDoubleSpinBox::down-arrow{image:url(%s); width:10px; height:10px;}" % minus)
        return spin

    def _on_bank_spatial(self, i):
        if 0 <= i < len(_SPATIAL) and _SPATIAL[i][2] != self._bank_spatial:
            self._bank_spatial = _SPATIAL[i][2]
            self.refresh()

    def _on_bank_band_price(self, tier, val):
        if abs(float(val) - float(self._bank_prices.get(tier, self._bank_price))) < 1e-6:
            return
        self._bank_prices[tier] = float(val)
        bar = self._scroll.verticalScrollBar(); pos = bar.value()
        self.refresh(); bar.setValue(pos)

    def _open(self, url):
        from qgis.PyQt.QtGui import QDesktopServices
        from qgis.PyQt.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(url))

    def _add_custom_builder(self, target):
        self._content.addWidget(_lbl("Or build a custom offset", _GREEN, 13, bold=True))
        card, lay = _card()
        krow = QHBoxLayout(); krow.setSpacing(6)
        krow.addWidget(_lbl("Add:", _MUTED, 11, bold=True))
        self._kind = _combo(["Small/large trees", "Condition uplift", "Create habitat", "Hedgerow"])
        self._kind.currentIndexChanged.connect(self._sync_custom_inputs)
        krow.addWidget(self._kind)
        krow.addStretch(1)
        lay.addLayout(krow)

        self._ci = {}
        self._ci["tree_count"] = QSpinBox(); self._ci["tree_count"].setRange(1, 100000); self._ci["tree_count"].setValue(50)
        self._ci["tree_size"] = _combo(["Small", "Medium", "Large", "Very Large"])
        self._ci["tree_cond"] = _combo(["Poor", "Moderate", "Good"])
        self._ci["up_hab"] = _combo(_AREA_CREATE)
        self._ci["up_from"] = _combo(_CONDITIONS)
        self._ci["up_to"] = _combo(list(reversed(_CONDITIONS)))
        self._ci["up_area"] = self._dspin()
        self._ci["cr_hab"] = _combo(_AREA_CREATE)
        self._ci["cr_cond"] = _combo(["Moderate", "Good", "Poor"])
        self._ci["cr_area"] = self._dspin()
        self._ci["cr_loss"] = _combo(["(loss habitat — optional)"] + _AREA_CREATE + ["Modified grassland"])
        self._ci["hd_hab"] = _combo(["Species-rich native hedgerow", "Native hedgerow with trees",
                                     "Native hedgerow", "Species-rich native hedgerow with trees"])
        self._ci["hd_cond"] = _combo(["Good", "Moderate", "Poor"])
        self._ci["hd_km"] = self._dspin(maxv=1000.0, step=0.1)

        self._input_row = QHBoxLayout(); self._input_row.setSpacing(6)
        lay.addLayout(self._input_row)
        add = _btn("Add to recipe", primary=True)
        add.clicked.connect(self._add_custom_step)
        lay.addWidget(add)
        lay.addWidget(_hline())
        self._steps_box = QVBoxLayout(); self._steps_box.setSpacing(3)
        lay.addLayout(self._steps_box)
        self._content.addWidget(card)
        self._sync_custom_inputs()
        self._render_custom_steps(target)

    def _dspin(self, maxv=1e5, step=0.05):
        s = QDoubleSpinBox(); s.setRange(0.0, maxv); s.setDecimals(3); s.setSingleStep(step); s.setValue(0.1)
        return s

    def _sync_custom_inputs(self):
        while self._input_row.count():
            it = self._input_row.takeAt(0); w = it.widget()
            if w:
                w.setParent(None)
        k = self._kind.currentIndex()
        def add(*ws):
            for w in ws:
                self._input_row.addWidget(w)
        if k == 0:
            add(_lbl("n:", _MUTED, 11), self._ci["tree_count"], self._ci["tree_size"], self._ci["tree_cond"])
        elif k == 1:
            add(self._ci["up_hab"], _lbl("from", _MUTED, 11), self._ci["up_from"],
                _lbl("to", _MUTED, 11), self._ci["up_to"], _lbl("ha:", _MUTED, 11), self._ci["up_area"])
        elif k == 2:
            add(self._ci["cr_hab"], self._ci["cr_cond"], _lbl("ha:", _MUTED, 11), self._ci["cr_area"], self._ci["cr_loss"])
        else:
            add(self._ci["hd_hab"], self._ci["hd_cond"], _lbl("km:", _MUTED, 11), self._ci["hd_km"])
        for i in range(self._input_row.count()):
            w = self._input_row.itemAt(i).widget()
            if w:
                w.setVisible(True)
        self._input_row.addStretch(1)

    def _add_custom_step(self):
        sp = 1.0
        k = self._kind.currentIndex()
        step = None
        if k == 0:
            step = ENG.custom_step("tree", spatial_risk=sp,
                                   size=self._ci["tree_size"].currentText(),
                                   condition=self._ci["tree_cond"].currentText(),
                                   count=self._ci["tree_count"].value())
        elif k == 1:
            step = ENG.custom_step("uplift", spatial_risk=sp,
                                   habitat=self._ci["up_hab"].currentText(),
                                   start_condition=self._ci["up_from"].currentText(),
                                   target_condition=self._ci["up_to"].currentText(),
                                   area_ha=self._ci["up_area"].value())
        elif k == 2:
            loss = self._ci["cr_loss"].currentText()
            loss = None if loss.startswith("(") else loss
            step = ENG.custom_step("create", spatial_risk=sp, module=self._module,
                                   habitat=self._ci["cr_hab"].currentText(),
                                   target_condition=self._ci["cr_cond"].currentText(),
                                   area_ha=self._ci["cr_area"].value(), loss_habitat=loss)
        else:
            step = ENG.custom_step("hedge", spatial_risk=sp,
                                   habitat=self._ci["hd_hab"].currentText(),
                                   condition=self._ci["hd_cond"].currentText(),
                                   km=self._ci["hd_km"].value())
        if step:
            self._custom_steps.append(step)
            self._render_custom_steps(self._target())

    def _render_custom_steps(self, target):
        while self._steps_box.count():
            it = self._steps_box.takeAt(0); w = it.widget()
            if w:
                w.deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())
        if not self._custom_steps:
            self._steps_box.addWidget(_lbl("No steps yet — add a move above.", _MUTED, 10, wrap=True))
            return
        total = 0.0
        for i, s in enumerate(self._custom_steps):
            total += s["units"]
            row = QFrame()
            bg = _VALID_BG if s.get("valid") else _INVALID_BG
            row.setStyleSheet("QFrame{background:%s; border-radius:6px;}" % bg)
            rl = QVBoxLayout(row); rl.setContentsMargins(8, 5, 8, 6); rl.setSpacing(1)
            top = QHBoxLayout()
            top.addWidget(_lbl("%s  (+%.3f units)" % (s["desc"], s["units"]), _INK, 12, bold=True, wrap=True))
            top.addStretch(1)
            rm = _btn("remove")
            rm.clicked.connect(lambda _=False, idx=i: self._remove_custom(idx))
            top.addWidget(rm)
            rl.addLayout(top)
            rl.addWidget(_lbl(("✓ " if s.get("valid") else "✗ ") + s.get("reason", ""), _MUTED, 10, wrap=True))
            self._steps_box.addWidget(row)
        closes = total >= target - 1e-9
        self._steps_box.addWidget(_lbl(
            "Running total: %.3f / %.3f units — %s"
            % (total, target, "gap closed ✓" if closes else "%.3f to go" % (target - total)),
            _GREEN_MID if closes else _AMBER, 12, bold=True, wrap=True))

    def _remove_custom(self, idx):
        if 0 <= idx < len(self._custom_steps):
            self._custom_steps.pop(idx)
            self._render_custom_steps(self._target())

    def _clear_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0); w = it.widget()
            if w:
                w.deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())
