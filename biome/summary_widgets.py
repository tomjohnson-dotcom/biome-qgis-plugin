# summary_widgets.py — SummaryWidgetsMixin, extracted verbatim from main_panel.py (2026-07-22 split).
# Methods run on BNGMainPanel via mixin inheritance; `self` is the panel.


from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QSizePolicy,
    QGridLayout)
from qgis.PyQt.QtCore import Qt, QTimer, QSize
from qgis.PyQt.QtGui import QFont
from . import theme
from .config import measurement_ellipsoid


from .panel_widgets import (_DivergingBar, _StackedBar, _MiniBar,
    _ModuleGlyph, _ProgressRing, _ObligationMeter, _dot_pixmap, _banner,
    _flow_row)

class SummaryWidgetsMixin:
    def _refresh_phase_screens(self):
        """Rebuild both phase screens in-place so feature counts are current.
        Called after any feature addition or deletion."""
        # Removing the widget that's currently on screen makes QStackedWidget
        # advance to the next page. If the user is viewing the Baseline screen
        # (slot 1) when this runs — e.g. they press Undo there — slot 1 is
        # removed and the view silently jumps to slot 2 (Proposed). Slots 1 and
        # 2 are rebuilt in place so every index is preserved, so capture the
        # current index up front and restore it once both are swapped. Signals
        # and repaints are held off so the transient index churn neither fires
        # the fade animation (install_fade hooks currentChanged) nor flickers.
        keep = self.stack.currentIndex()
        was_blocked = self.stack.signalsBlocked()
        self.stack.blockSignals(True)
        self.stack.setUpdatesEnabled(False)
        try:
            for slot, phase in ((1, "baseline"), (2, "proposed")):
                screen = self._build_phase_screen(phase)
                old = self.stack.widget(slot)
                self.stack.insertWidget(slot, screen)
                self.stack.removeWidget(old)
                if old:
                    old.deleteLater()
            if self.stack.currentIndex() != keep:
                self.stack.setCurrentIndex(keep)
        finally:
            self.stack.blockSignals(was_blocked)
            self.stack.setUpdatesEnabled(True)

    # ── Legacy refresh entry points ──────────────────────────────────
    # habitat_form, panel_map_tools, bng_plugin and the editingStopped hooks
    # call these three by name (often two or three back-to-back). They all
    # funnel into one coalesced dashboard refresh now — the balance card is
    # merged into the dashboard and the layer stats are its Data quality
    # section, so there is nothing separate left to rebuild.
    def _refresh_balance_widget(self):
        self._request_dashboard_refresh()

    def _refresh_layers_widget(self):
        self._request_dashboard_refresh()

    def _request_dashboard_refresh(self):
        """Coalesce refresh requests (legacy callers fire several per edit)
        into a single rebuild on the next event-loop turn. Off the Dashboard
        tab it only marks the summary dirty, exactly as before."""
        if getattr(self, "_dash_refresh_queued", False):
            return
        self._dash_refresh_queued = True

        def _go():
            self._dash_refresh_queued = False
            try:
                from qgis.PyQt import sip
                if sip.isdeleted(self):
                    return
            except Exception:
                pass
            if self._summary_on_screen():
                self._refresh_summary_now()
            else:
                self._summary_dirty = True
                self._invalidate_enh_cache()

        QTimer.singleShot(0, _go)

    # ── Dashboard UI ─────────────────────────────────────────────────
    # Built ONCE and updated in place: the hero, tiles and section skeletons
    # persist and take new values per refresh (only the small variable-length
    # row lists are re-filled), so a map edit updates the numbers without
    # tearing the tab down — no flicker, the scroll position survives, and
    # the hero figure / rings animate from the old value to the new one.
    #
    # Styling: every dashboard widget opts OUT of the app-wide dark restyler
    # (biome_fixed_color) and is styled directly from live theme tokens; a
    # theme listener re-applies the static styles and re-runs the update on a
    # light/dark flip, so both directions are exact by construction.

    _DASH_MODS = ("area", "hedgerow", "watercourse")
    _DASH_TITLES = {"area": "Area", "hedgerow": "Hedgerow",
                    "watercourse": "Watercourse"}
    _DASH_GLYPHS = {"area": "area", "hedgerow": "hedge", "watercourse": "water"}
    # The noun a slot row offers ("Any hedgerow · Low+", not "Any habitat").
    _DASH_NOUNS = {"area": "habitat", "hedgerow": "hedgerow",
                   "watercourse": "watercourse"}

    @staticmethod
    def _dash_mod_color(module):
        """Module identity colour (live token read — correct in both modes)."""
        return {"area": theme.GOOD, "hedgerow": theme.MOD_HEDGE,
                "watercourse": theme.INFO}[module]

    @staticmethod
    def _dash_state_color(pct, target=10.0):
        """Token-driven verdict colour (panel_widgets._gain_color bakes light
        hexes; the dashboard restyles itself, so it needs the live tokens)."""
        if pct is None:
            return theme.FAINT
        if pct >= target:
            return theme.GOOD
        if pct >= 0:
            return theme.WARN
        return theme.BAD

    @staticmethod
    def _dstyle(w, qss, cascade=False):
        """Style a dashboard widget from live tokens and opt it out of the
        dark-mode restyler (the dashboard restyles itself on a theme flip —
        letting the restyler also remap these would fight that).

        Bare declaration blocks are auto-scoped to the widget's objectName:
        a selector-less rule is inherited by the widget's own QTipLabel, so an
        unscoped ``font-size:38px`` on the hero number rendered its TOOLTIP at
        38px. ``cascade=True`` keeps the rule selector-less on purpose — the
        container ``background:transparent`` must cascade to painted children
        (rings/meters/bars), which have no stylesheet of their own."""
        w.setProperty("biome_fixed_color", True)
        if not cascade and "{" not in qss:
            name = w.objectName()
            if not name:
                name = "dashW%x" % id(w)
                w.setObjectName(name)
            qss = "#%s { %s }" % (name, qss)
        w.setStyleSheet(qss)

    def _dlbl(self, text="", size=12, weight=400, wrap=False,
              align=Qt.AlignmentFlag.AlignLeft, tabular=False):
        """Dashboard QLabel. Colour is applied later (statics in
        _apply_dashboard_styles, dynamics per update) so labels re-colour on a
        theme flip; the font itself never changes."""
        l = QLabel(text)
        if tabular:
            l.setFont(theme.numeric_font(
                size, weight=(QFont.Weight.DemiBold if weight >= 600
                              else QFont.Weight.Medium)))
        l.setAlignment(align)
        if wrap:
            l.setWordWrap(True)
        l._dash_font = (size, weight, tabular)
        return l

    def _dcolor(self, lbl, color, bg="transparent"):
        """(Re)apply a label's colour + its remembered font as a stylesheet."""
        size, weight, tabular = getattr(lbl, "_dash_font", (12, 400, False))
        if tabular:
            self._dstyle(lbl, f"color:{color}; background:{bg}; border:none;")
        else:
            self._dstyle(lbl,
                         f"color:{color}; font-size:{size}px;"
                         f" font-weight:{weight}; background:{bg}; border:none;"
                         f" font-family:{theme.FONT_MEDIUM};")

    def _dtransparent(self, *widgets):
        """Explicit transparent fill for plain container widgets. The tab's
        scroll-inner sets a selector-less background (the SURFACE chalk), and
        Qt cascades a bare property into every descendant that doesn't set its
        own — an unstyled holder inside a white card paints CHALK over the
        card. Every container the dashboard creates goes through here."""
        for w in widgets:
            self._dstyle(w, "background:transparent; border:none;",
                         cascade=True)

    def _dash_clear_rows(self, layout):
        """Empty a rebuilt row container (setParent(None) BEFORE deleteLater —
        see the deleteLater widget-overlap gotcha)."""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _dash_animate_number(self, lbl, value, fmt="{:.2f}"):
        """Roll a persistent numeric label from its previous value to `value`.
        First value snaps (the widget is fresh); state changes with no prior
        number snap too. The animation is parented to the label so it can
        never outlive it."""
        old = getattr(lbl, "_dash_val", None)
        lbl._dash_val = value
        prev = getattr(lbl, "_dash_anim", None)
        if prev is not None:
            try:
                prev.stop()
            except Exception:
                pass
            lbl._dash_anim = None
        if value is None:
            lbl.setText("")
            return
        if old is None or abs(old - value) < 0.005:
            lbl.setText(fmt.format(value))
            return
        try:
            from qgis.PyQt.QtCore import QVariantAnimation, QEasingCurve
            a = QVariantAnimation(lbl)
            a.setDuration(260)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.setStartValue(float(old))
            a.setEndValue(float(value))
            a.valueChanged.connect(lambda v, l=lbl, f=fmt: l.setText(f.format(float(v))))
            a.finished.connect(lambda l=lbl, f=fmt, v=value: l.setText(f.format(v)))
            a.start()
            lbl._dash_anim = a
        except Exception:
            lbl.setText(fmt.format(value))

    def _dash_slot_caption(self, module, s):
        """(heading, chip, tooltip) for one obligation slot. Same wording the
        old per-module rows used, made module-aware ("Any hedgerow", not "Any
        habitat") — and watercourse floors are POLICY ("loss unacceptable"),
        not a trading table, so their chip says so."""
        noun = self._DASH_NOUNS.get(module, "habitat")
        b, up = s["band"], s.get("trades_up", False)
        water = module == "watercourse"
        if s["kind"] == "free":
            return (f"Any {noun} · {b}+", "free choice",
                    f"Any {noun} at {b} distinctiveness or better.")
        if s.get("scope") == "habitat":
            if water:
                return (s["label"], "policy",
                        f"The metric treats loss of this watercourse as "
                        f"unacceptable — replace with {s['label']} only; a "
                        f"different habitat does NOT satisfy it.")
            return (s["label"],
                    "same habitat or better" if up else "same habitat only",
                    f"Replace with {s['label']}"
                    + (" — or any habitat of higher distinctiveness."
                       if up else " only. A better habitat does NOT satisfy this."))
        if s.get("scope") == "band":
            return (f"Any {noun} · {b}+", "same band or better",
                    f"Any {noun} at {b} distinctiveness or better — no group "
                    f"or habitat-type condition.")
        return (f"{s['label']} · {b}+",
                "group, or trade up" if up else "same broad group",
                f"Replace with {s['label']} habitat at {b} distinctiveness or "
                f"better"
                + (" — or with any habitat of higher distinctiveness, which "
                   "needs no group match." if up else "."))

    def _build_dashboard(self):
        """Create the persistent dashboard skeleton inside _bng_container and
        stash every mutable ref in self._dash. Runs once per panel life."""
        d = {}

        root = QWidget()
        rv = QVBoxLayout(root)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        d["root"] = root

        def _card(name):
            w = QWidget()
            w.setObjectName(name)
            return w

        # ── Hero ─────────────────────────────────────────────────────
        hero = _card("dashHero")
        hv = QVBoxLayout(hero)
        hv.setContentsMargins(14, 11, 14, 12)
        hv.setSpacing(0)
        d["hero"] = hero

        eye = QWidget()
        el = QHBoxLayout(eye)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(6)
        d["eyebrow"] = self._dlbl("NET GAIN", 10, 600)
        el.addWidget(d["eyebrow"])
        el.addStretch()
        # Present-as-infographic: renders this dashboard's payload to a
        # shareable PNG poster (dashboard_infographic).
        d["info_btn"] = QPushButton("Infographic")
        d["info_btn"].setCursor(Qt.CursorShape.PointingHandCursor)
        d["info_btn"].setIconSize(QSize(13, 13))
        d["info_btn"].setToolTip(
            "Present this dashboard as a shareable infographic — preview, "
            "then copy or save as a PNG.")
        d["info_btn"].clicked.connect(self._dash_present_infographic)
        el.addWidget(d["info_btn"])
        el.addSpacing(6)
        d["live_dot"] = QLabel()
        d["live_dot"].setFixedSize(6, 6)
        el.addWidget(d["live_dot"])
        d["live_lbl"] = self._dlbl("live from map", 10)
        el.addWidget(d["live_lbl"])
        hv.addWidget(eye)

        # Dashboard-only scope switch. It never changes geometry or exports;
        # unchecked means calculate strictly inside the redline and ignore the
        # blue-line-only land.
        scope_row = QWidget()
        sl = QHBoxLayout(scope_row)
        sl.setContentsMargins(0, 7, 0, 0)
        sl.setSpacing(0)
        sl.addStretch()
        d["offsite_btn"] = QPushButton("Off-site included")
        d["offsite_btn"].setCheckable(True)
        d["offsite_btn"].setChecked(
            getattr(self, "_dashboard_include_offsite", True))
        d["offsite_btn"].setCursor(Qt.CursorShape.PointingHandCursor)
        d["offsite_btn"].setToolTip(
            "Turn off to ignore every habitat in the blue-line-only area in "
            "Dashboard calculations. Features and metric exports are unchanged.")
        d["offsite_btn"].toggled.connect(self._dash_toggle_offsite)
        sl.addWidget(d["offsite_btn"])
        hv.addWidget(scope_row)
        d["scope_row"] = scope_row

        # Empty state — a designed one, not a skeleton of blank sections.
        empty = QWidget()
        ev = QHBoxLayout(empty)
        ev.setContentsMargins(0, 10, 0, 6)
        ev.setSpacing(12)
        d["empty_ring"] = _ProgressRing(44, 4)
        d["empty_ring"].set_state(None, ghost=True)
        ev.addWidget(d["empty_ring"], 0, Qt.AlignmentFlag.AlignTop)
        ecol = QWidget()
        ecv = QVBoxLayout(ecol)
        ecv.setContentsMargins(0, 2, 0, 0)
        ecv.setSpacing(3)
        d["empty_title"] = self._dlbl("No habitats mapped yet", 13, 600)
        d["empty_sub"] = self._dlbl("", 11, wrap=True)
        ecv.addWidget(d["empty_title"])
        ecv.addWidget(d["empty_sub"])
        ev.addWidget(ecol, 1)
        d["empty_box"] = empty
        hv.addWidget(empty)

        # Action state — the hero number + the shopping list.
        act = QWidget()
        av = QVBoxLayout(act)
        av.setContentsMargins(0, 6, 0, 0)
        av.setSpacing(6)
        # Module figures must wrap as WHOLE number+unit pairs. A fixed HBox of
        # three pairs needs over 400 px and silently widened the dashboard
        # behind the scroll area's disabled horizontal bar, clipping every card
        # on the right in a normal/narrow dock.
        num_row, nl = _flow_row(12)

        def _figure_pair(num, unit):
            pair = QWidget()
            pl = QHBoxLayout(pair)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(7)
            pl.addWidget(num, 0, Qt.AlignmentFlag.AlignBottom)
            pl.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
            self._dtransparent(pair)
            return pair

        d["hero_num"] = QLabel("")
        d["hero_num"].setToolTip(
            "Every outstanding obligation across the modules, summed. Units "
            "are per-module currencies — the rows below are what to secure.")
        d["hero_unit"] = self._dlbl("units\nrequired", 12, 500)
        nl.addWidget(_figure_pair(d["hero_num"], d["hero_unit"]))
        # Units are a per-module currency, so a summed figure was misleading —
        # 0.98 could be area, hedgerow or a mix. Each short module gets its own
        # number instead; a module that is already met simply doesn't appear.
        # Two spare pairs cover the other modules (only three exist).
        d["hero_extra"] = []
        for _i in range(2):
            num = QLabel("")
            unit = self._dlbl("", 12, 500)
            num.setVisible(False)
            unit.setVisible(False)
            pair = _figure_pair(num, unit)
            pair.setVisible(False)
            # Store the holder on the labels so the update can show/hide the
            # pair atomically; otherwise FlowLayout reserves a blank slot or
            # separates a number from its unit caption.
            num._dash_pair = pair
            unit._dash_pair = pair
            nl.addWidget(pair)
            d["hero_extra"].append((num, unit))
        av.addWidget(num_row)
        d["hero_sub"] = self._dlbl("", 11, wrap=True)
        av.addWidget(d["hero_sub"])
        obl = QWidget()
        d["obl_layout"] = QVBoxLayout(obl)
        d["obl_layout"].setContentsMargins(0, 4, 0, 2)
        d["obl_layout"].setSpacing(12)
        av.addWidget(obl)
        # Wraps: a one-line hint this long sets a ~380px minimum, which the
        # hero's 14px margins push past a 400px dock — and the tab's scroll
        # area has no horizontal bar, so the overflow was clipped, not
        # scrollable. NOTHING in the dashboard may demand more width than a
        # 300px dock (the panel's minimum) leaves: wrap it or flow it.
        d["covered_hint"] = self._dlbl("", 10, wrap=True)
        av.addWidget(d["covered_hint"])
        d["action_box"] = act
        hv.addWidget(act)

        # Compliant state — tick, wordmark, per-module stats.
        okb = QWidget()
        ov = QVBoxLayout(okb)
        ov.setContentsMargins(0, 13, 0, 4)
        ov.setSpacing(5)
        ok_row = QWidget()
        ol = QHBoxLayout(ok_row)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(12)
        d["ok_ring"] = _ProgressRing(46, 4)
        ol.addWidget(d["ok_ring"])
        okc = QWidget()
        ocv = QVBoxLayout(okc)
        ocv.setContentsMargins(0, 0, 0, 0)
        ocv.setSpacing(4)
        d["ok_title"] = self._dlbl("Scheme compliant", 18, 600)
        d["ok_sub"] = self._dlbl("", 11, wrap=True)
        ocv.addWidget(d["ok_title"])
        ocv.addWidget(d["ok_sub"])
        ol.addWidget(okc, 1)
        ov.addWidget(ok_row)
        d["ok_box"] = okb
        hv.addWidget(okb)

        # Kill the surface-background cascade on every hero container.
        self._dtransparent(eye, scope_row, empty, ecol, act, num_row, obl, okb,
                           ok_row, okc)

        rv.addWidget(hero)

        # ── Module tiles ─────────────────────────────────────────────
        tiles = QWidget()
        tl = QHBoxLayout(tiles)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        d["tiles_row"] = tiles
        d["tiles"] = {}
        for m in self._DASH_MODS:
            t = {}
            tile = _card("dashTile")
            tv = QVBoxLayout(tile)
            tv.setContentsMargins(8, 10, 8, 9)
            tv.setSpacing(7)
            head = QWidget()
            hh = QHBoxLayout(head)
            hh.setContentsMargins(0, 0, 0, 0)
            hh.setSpacing(5)
            hh.addStretch()
            t["glyph"] = _ModuleGlyph(self._DASH_GLYPHS[m], theme.MUTED, 13)
            hh.addWidget(t["glyph"])
            t["name"] = self._dlbl(self._DASH_TITLES[m], 10, 500)
            hh.addWidget(t["name"])
            hh.addStretch()
            tv.addWidget(head)
            t["ring"] = _ProgressRing(52, 5)
            tv.addWidget(t["ring"], 0, Qt.AlignmentFlag.AlignHCenter)
            t["value"] = self._dlbl("—", 14, 600, align=Qt.AlignmentFlag.AlignHCenter,
                                    tabular=True)
            tv.addWidget(t["value"])
            t["sub"] = self._dlbl("", 9, align=Qt.AlignmentFlag.AlignHCenter)
            tv.addWidget(t["sub"], 0, Qt.AlignmentFlag.AlignHCenter)
            # baseline -> proposed, under a hairline. Per-module, so it belongs
            # on the tile rather than the header, which could only ever show
            # one module's figures.
            t["rule"] = QFrame()
            t["rule"].setFrameShape(QFrame.Shape.HLine)
            t["rule"].setFixedHeight(1)
            tv.addWidget(t["rule"])
            t["units"] = self._dlbl("", 9, align=Qt.AlignmentFlag.AlignHCenter,
                                    tabular=True, wrap=True)
            # Baseline and net change are intentionally shown on two lines.
            # Three one-third-width tiles cannot reliably hold both values on
            # one line at the normal dock width without clipping.
            t["units"].setMinimumWidth(0)
            t["units"].setSizePolicy(QSizePolicy.Policy.Preferred,
                                     QSizePolicy.Policy.Preferred)
            tv.addWidget(t["units"], 0, Qt.AlignmentFlag.AlignHCenter)
            t["card"] = tile
            d["tiles"][m] = t
            self._dtransparent(head)
            # Right-click copies the tile's summary (headline + the tooltip's
            # baseline/target/slot detail) as plain text.
            tile.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tile.customContextMenuRequested.connect(
                lambda pos, w=tile: self._dash_copy_tile(w, pos))
            tl.addWidget(tile, 1)
        self._dtransparent(tiles)
        rv.addWidget(tiles)

        # ── Quiet detail sections ────────────────────────────────────
        def _section(key, title):
            card = _card("dashSec")
            sv = QVBoxLayout(card)
            sv.setContentsMargins(12, 9, 12, 11)
            sv.setSpacing(8)
            head = QWidget()
            sh = QHBoxLayout(head)
            sh.setContentsMargins(0, 0, 0, 0)
            sh.setSpacing(8)
            d[key + "_title"] = self._dlbl(title, 10, 600)
            sh.addWidget(d[key + "_title"])
            d[key + "_note"] = self._dlbl("", 9)
            sh.addWidget(d[key + "_note"])
            d[key + "_rule"] = QFrame()
            d[key + "_rule"].setFixedHeight(1)
            sh.addWidget(d[key + "_rule"], 1)
            sv.addWidget(head)
            body = QWidget()
            d[key + "_layout"] = QVBoxLayout(body)
            d[key + "_layout"].setContentsMargins(0, 0, 0, 0)
            d[key + "_layout"].setSpacing(6)
            sv.addWidget(body)
            self._dtransparent(head, body)
            d[key + "_card"] = card
            rv.addWidget(card)
            return card

        _section("movers", "WHAT CHANGED")
        d["movers_note"].setText("units")

        _section("split", "AREA SPLIT")
        d["split_note"].setText("hectares")
        d["split_bar_slot"] = QVBoxLayout()
        d["split_bar_slot"].setContentsMargins(0, 0, 0, 0)
        d["split_layout"].addLayout(d["split_bar_slot"])
        leg = QWidget()
        lg = QGridLayout(leg)
        lg.setContentsMargins(0, 2, 0, 0)
        lg.setHorizontalSpacing(16)
        lg.setVerticalSpacing(5)
        lg.setColumnStretch(0, 1)
        lg.setColumnStretch(1, 1)
        d["split_legend"] = []
        for i, name in enumerate(("Retained", "Enhanced", "Created", "Lost")):
            item = QWidget()
            il = QHBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(6)
            sw = QLabel()
            sw.setFixedSize(10, 10)
            il.addWidget(sw)
            nm = self._dlbl(name, 11)
            il.addWidget(nm)
            il.addStretch()
            val = self._dlbl("", 11, align=Qt.AlignmentFlag.AlignRight, tabular=True)
            il.addWidget(val)
            lg.addWidget(item, i // 2, i % 2)
            d["split_legend"].append((sw, nm, val))
            self._dtransparent(item)
        self._dtransparent(leg)
        d["split_layout"].addWidget(leg)

        _section("credits", "STATUTORY CREDITS")
        d["credits_zero"] = self._dlbl("No credits required ✓", 11,
                                       align=Qt.AlignmentFlag.AlignHCenter)
        d["credits_layout"].addWidget(d["credits_zero"])
        crows = QWidget()
        d["credits_rows"] = QVBoxLayout(crows)
        d["credits_rows"].setContentsMargins(0, 0, 0, 0)
        d["credits_rows"].setSpacing(6)
        self._dtransparent(crows)
        d["credits_layout"].addWidget(crows)

        _section("quality", "DATA QUALITY")
        # Four chips in a row need ~380px; flowed they wrap onto a second line
        # in a narrow dock instead of running off the right edge.
        qrow, d["quality_chips"] = _flow_row(6)
        d["quality_layout"].addWidget(qrow)
        d["quality_toggle"] = QPushButton("detail ▸")
        d["quality_toggle"].setCheckable(True)
        d["quality_toggle"].setCursor(Qt.CursorShape.PointingHandCursor)
        d["quality_layout"].addWidget(d["quality_toggle"], 0,
                                      Qt.AlignmentFlag.AlignRight)
        qdet = QWidget()
        d["quality_detail_rows"] = QVBoxLayout(qdet)
        d["quality_detail_rows"].setContentsMargins(0, 0, 0, 0)
        d["quality_detail_rows"].setSpacing(0)
        qdet.setVisible(False)
        d["quality_detail"] = qdet
        self._dtransparent(qrow, qdet)
        d["quality_layout"].addWidget(qdet)

        def _tog(checked, w=qdet, b=d["quality_toggle"]):
            w.setVisible(checked)
            b.setText("detail ▾" if checked else "detail ▸")
        d["quality_toggle"].clicked.connect(_tog)

        self._dash = d
        self._bng_container.layout().addWidget(root)
        self._apply_dashboard_styles()

        # Restyle + re-colour on a light/dark flip (self-unregistering).
        def _dash_theme_cb():
            try:
                from qgis.PyQt import sip
                dd = getattr(self, "_dash", None)
                if not dd or sip.isdeleted(dd["root"]):
                    theme.remove_theme_listener(_dash_theme_cb)
                    return
                self._apply_dashboard_styles()
                payload = getattr(self, "_dash_payload", None)
                if payload is not None:
                    self._update_dashboard(payload)
            except Exception:
                pass
        theme.add_theme_listener(_dash_theme_cb)

    def _apply_dashboard_styles(self):
        """Static styling from CURRENT theme tokens — build time and every
        light/dark flip. Dynamic (value-dependent) colours are applied by
        _update_dashboard, which the flip listener re-runs afterwards."""
        d = self._dash
        card_qss = ("{ background:%s; border:1px solid %s; border-radius:8px; }"
                    % (theme.CARD, theme.BORDER))
        self._dstyle(d["hero"], "QWidget#dashHero " + card_qss)
        for t in d["tiles"].values():
            self._dstyle(t["card"], "QWidget#dashTile " + card_qss)
            self._dcolor(t["name"], theme.MUTED)
            t["glyph"].set_color(theme.MUTED)
            self._dstyle(t["rule"],
                         f"background:{theme.BORDER}; border:none;")
        for key in ("movers", "split", "credits", "quality"):
            self._dstyle(d[key + "_card"], "QWidget#dashSec " + card_qss)
            lbl = d[key + "_title"]
            self._dstyle(lbl,
                         f"color:{theme.INK}; font-size:11px; font-weight:600;"
                         f" letter-spacing:0.8px; background:transparent;"
                         f" border:none; font-family:{theme.FONT_MEDIUM};")
            self._dcolor(d[key + "_note"], theme.FAINT)
            self._dstyle(d[key + "_rule"],
                         f"background:{theme.BORDER}; border:none;")
        self._dstyle(d["eyebrow"],
                     f"color:{theme.INK}; font-size:11px; font-weight:600;"
                     f" letter-spacing:1.0px; background:transparent;"
                     f" border:none; font-family:{theme.FONT_MEDIUM};")
        self._dcolor(d["live_lbl"], theme.FAINT)
        self._dstyle(d["info_btn"],
                     f"QPushButton {{ background:transparent; border:none;"
                     f" color:{theme.FAINT}; font-size:10px;"
                     f" font-family:{theme.FONT_MEDIUM}; padding:1px 2px; }}"
                     f"QPushButton:hover {{ color:{theme.ACCENT_DARK}; }}")
        d["info_btn"].setIcon(theme.icon("chart-infographic", theme.FAINT, 13))
        d["live_dot"].setPixmap(_dot_pixmap(theme.GOOD, 6))
        self._dstyle(d["offsite_btn"],
                     f"QPushButton {{ background:{theme.SUNKEN};"
                     f" color:{theme.MUTED}; border:1px solid {theme.BORDER};"
                     f" border-radius:6px; padding:4px 9px; font-size:10px;"
                     f" font-family:{theme.FONT_MEDIUM}; }}"
                     f"QPushButton:checked {{ background:{theme.ACCENT_SOFT};"
                     f" color:{theme.ACCENT_DARK}; border-color:{theme.ACCENT}; }}"
                     f"QPushButton:hover {{ border-color:{theme.ACCENT}; }}")
        self._dcolor(d["empty_title"], theme.INK)
        self._dcolor(d["empty_sub"], theme.MUTED)
        self._dstyle(d["hero_num"],
                     f"color:{theme.BAD}; font-size:38px; font-weight:600;"
                     f" background:transparent; border:none;"
                     f" font-family:{theme.FONT_SEMIBOLD};")
        self._dcolor(d["hero_unit"], theme.MUTED)
        for _num, _unit in d.get("hero_extra", ()):
            self._dstyle(_num,
                         f"color:{theme.BAD}; font-size:38px; font-weight:600;"
                         f" background:transparent; border:none;"
                         f" font-family:{theme.FONT_SEMIBOLD};")
            self._dcolor(_unit, theme.MUTED)
        self._dcolor(d["hero_sub"], theme.FAINT)
        self._dcolor(d["covered_hint"], theme.FAINT)
        self._dcolor(d["ok_title"], theme.INK)
        self._dcolor(d["ok_sub"], theme.MUTED)
        self._dstyle(d["quality_toggle"],
                     f"QPushButton {{ background:transparent; border:none;"
                     f" color:{theme.FAINT}; font-size:10px;"
                     f" font-family:{theme.FONT_MEDIUM}; padding:1px 2px; }}"
                     f"QPushButton:hover {{ color:{theme.MUTED}; }}")
        self._dcolor(d["credits_zero"], theme.GOOD)

    def _dash_derive(self, payload):
        """Verdict derivation shared by the dashboard header and the
        infographic export (dashboard_infographic) — ONE definition of
        outstanding/covered obligations, per-module shortfalls and
        'compliant', so the poster can never disagree with the tab."""
        mods = (payload or {}).get("modules", {})
        active = [m for m in self._DASH_MODS if mods.get(m, {}).get("active")]
        slots_by_mod = {m: (mods[m].get("slots") or {}) for m in active}

        outstanding, covered_n, slot_total = [], 0, 0
        short_by_mod = {}
        net_gap_by_mod = {}
        for m in active:
            rows = slots_by_mod[m].get("slots") or []
            for s in rows:
                if s["need"] <= 0.005:
                    continue
                slot_total += 1
                if s["short"] > 0.005:
                    outstanding.append((m, s))
                else:
                    covered_n += 1
            tot = sum(s["short"] for s in (slots_by_mod[m].get("slots") or []))
            if tot > 0.005:
                short_by_mod[m] = tot
            dm = mods[m]
            bl_on = dm.get("bl_on", dm.get("bl") or 0.0) or 0.0
            net_gap = max(
                0.0,
                bl_on * 0.10
                - ((dm.get("pr") or 0.0) - (dm.get("bl") or 0.0)),
            )
            if net_gap > 1e-9:
                net_gap_by_mod[m] = net_gap
        outstanding.sort(key=lambda t: -t[1]["short"])
        total_short = sum(short_by_mod.values())
        required_by_mod = {
            m: max(short_by_mod.get(m, 0.0), net_gap_by_mod.get(m, 0.0))
            for m in active
            if max(short_by_mod.get(m, 0.0), net_gap_by_mod.get(m, 0.0)) > 1e-9
        }

        scored = [m for m in active if mods[m]["pct"] is not None]
        empty = not active
        compliant = (not empty) and total_short <= 0.005 and all(
            mods[m]["pct"] >= 10.0 for m in scored)
        return {"mods": mods, "active": active, "slots_by_mod": slots_by_mod,
                "outstanding": outstanding, "covered_n": covered_n,
                "slot_total": slot_total, "short_by_mod": short_by_mod,
                "net_gap_by_mod": net_gap_by_mod,
                "required_by_mod": required_by_mod,
                "total_short": total_short, "scored": scored,
                "empty": empty, "compliant": compliant}

    def _dash_toggle_offsite(self, checked):
        """Include/exclude blue-line-only habitat from Dashboard maths.

        Session-only by design: this is a viewing/calculation switch, not a
        project-data setting, and never changes the statutory metric export.
        """
        self._dashboard_include_offsite = bool(checked)
        btn = getattr(self, "_dash", {}).get("offsite_btn")
        if btn is not None:
            btn.setText("Off-site included" if checked else "Off-site ignored")
        self._mark_summary_dirty()
        self._request_dashboard_refresh()

    # ── Update (values in place) ─────────────────────────────────────
    def _update_dashboard(self, payload):
        d = self._dash
        quality = (payload or {}).get("quality") or {}
        v = self._dash_derive(payload)
        mods, active = v["mods"], v["active"]
        slots_by_mod, outstanding = v["slots_by_mod"], v["outstanding"]
        slot_total, short_by_mod = v["slot_total"], v["short_by_mod"]
        net_gap_by_mod = v["net_gap_by_mod"]
        required_by_mod = v["required_by_mod"]
        total_short, scored = v["total_short"], v["scored"]
        empty, compliant = v["empty"], v["compliant"]

        off_btn = d.get("offsite_btn")
        if off_btn is not None:
            has_offsite = bool((payload or {}).get("has_offsite"))
            include_offsite = bool((payload or {}).get("include_offsite", True))
            off_btn.blockSignals(True)
            off_btn.setChecked(include_offsite)
            off_btn.setText("Off-site included" if include_offsite
                            else "Off-site ignored")
            off_btn.setEnabled(has_offsite)
            off_btn.setVisible(has_offsite)
            off_btn.blockSignals(False)

        d["empty_box"].setVisible(empty)
        d["action_box"].setVisible((not empty) and not compliant)
        d["ok_box"].setVisible((not empty) and compliant)
        d["tiles_row"].setVisible(not empty)

        if empty:
            if quality.get("total", 0) > 0:
                d["empty_title"].setText("Mapped features carry no units yet")
                d["empty_sub"].setText(
                    "Only zero-rated surfaces (developed land etc.) are mapped "
                    "so far — the verdict appears once unit-bearing habitat "
                    "is drawn.")
            else:
                d["empty_title"].setText("No habitats mapped yet")
                d["empty_sub"].setText(
                    "Draw baseline and proposed habitats — the verdict "
                    "appears here, live.")
        elif compliant:
            d["ok_ring"].set_state(100, "GOOD", "check")
            parts = ["All modules meet +10%"]
            if any((slots_by_mod[m].get("slots")) for m in active):
                parts.append("trading rules satisfied")
            d["ok_sub"].setText(" · ".join(parts))
        else:
            # One figure per short module, biggest first.
            order = sorted(required_by_mod,
                           key=lambda k: -required_by_mod[k])
            if not order:
                order = [m for m in scored if mods[m]["pct"] < 10.0][:1]
            lead = order[0] if order else None
            hero_need = required_by_mod.get(lead, total_short) if lead else total_short
            if 0.0 < hero_need < 0.01:
                # Preserve the real small shortfall instead of rounding it to
                # the misleading value 0.00.
                d["hero_num"]._dash_val = hero_need
                prev = getattr(d["hero_num"], "_dash_anim", None)
                if prev is not None:
                    prev.stop()
                    d["hero_num"]._dash_anim = None
                d["hero_num"].setText("<0.01")
            else:
                self._dash_animate_number(d["hero_num"], hero_need)
            d["hero_unit"].setText(
                f"{self._DASH_TITLES[lead].lower()} units\nrequired" if lead
                else "units\nrequired")
            for i, (num, unit) in enumerate(d["hero_extra"]):
                m = order[i + 1] if len(order) > i + 1 else None
                pair = getattr(num, "_dash_pair", None)
                if pair is not None:
                    pair.setVisible(m is not None)
                num.setVisible(m is not None)
                unit.setVisible(m is not None)
                if m is not None:
                    need = required_by_mod.get(m, 0.0)
                    num.setText("<0.01" if 0.0 < need < 0.01
                                else f"{need:.2f}")
                    unit.setText(f"{self._DASH_TITLES[m].lower()} units\n"
                                 f"required")
            n_out = len(outstanding)
            if slot_total and n_out == 0:
                bits = ["Trading obligations met ✓"]
            else:
                bits = [f"{n_out} of {slot_total} obligation"
                        f"{'s' if slot_total != 1 else ''} outstanding"]
            # The metric reports TWO different area shortfalls in two different
            # places, and quoting only one made the panel look like it
            # disagreed with the export:
            #   * Headline Results  -> the NET GAIN gap, baseline x 1.10 less
            #     what is delivered.
            #   * Trading Summary   -> the TRADING floor, the per-band deficit
            #     left after higher-distinctiveness units have traded down.
            # The hero shows whichever requirement needs more units. Name the
            # distinction below when trading and net gain differ.
            if lead:
                gain_gap = net_gap_by_mod.get(lead, 0.0)
                trading_gap = short_by_mod.get(lead, 0.0)
                if gain_gap > 1e-9 and trading_gap <= 0.005:
                    bits.append("below +10% net gain")
                if (trading_gap > 0.005 and gain_gap > 0.005
                        and abs(gain_gap - trading_gap) > 0.005):
                    bits.append(f"{gain_gap:.2f} u short on net gain, "
                                f"{trading_gap:.2f} u on trading rules")
            # Name what IS met, rather than repeating the shortfalls above.
            met = [self._DASH_TITLES[m].lower() for m in scored
                   if m not in order]
            if met:
                bits.append(" · ".join(met) + " met ✓")
            d["hero_sub"].setText("  ·  ".join(bits))
            old_meter_fracs = getattr(self, "_dash_meter_fracs", {})
            new_meter_fracs = {}
            self._dash_clear_rows(d["obl_layout"])
            for m, s in outstanding:
                meter_key = (m, s.get("kind"), s.get("band"),
                             bool(s.get("trades_up", False)))
                target_frac = (max(0.0, min(1.0, s["have"] / s["need"]))
                               if s.get("need", 0.0) > 0 else 0.0)
                start_frac = old_meter_fracs.get(meter_key, 0.0)
                d["obl_layout"].addWidget(
                    self._dash_obligation_row(m, s, start_frac=start_frac))
                new_meter_fracs[meter_key] = target_frac
            self._dash_meter_fracs = new_meter_fracs
            # The "N obligations already covered — hover a tile…" hint used to
            # show here. It explained the interface rather than the site, and
            # the outstanding count on the headline row already says where the
            # scheme stands, so the header stays to four elements.
            d["covered_hint"].setVisible(False)

        # ── Tiles ────────────────────────────────────────────────────
        for m in self._DASH_MODS:
            t = d["tiles"][m]
            dm = mods.get(m, {})
            if not dm.get("active"):
                t["ring"].set_state(None, ghost=True)
                t["value"].setText("—")
                self._dcolor(t["value"], theme.FAINT)
                self._dstyle(t["sub"],
                             f"color:{theme.FAINT}; font-size:9px;"
                             f" background:transparent; border:none;"
                             f" font-family:{theme.FONT_MEDIUM};")
                t["sub"].setText("not mapped")
                t["units"].setText("no baseline")
                self._dcolor(t["units"], theme.FAINT)
                t["card"].setToolTip("")
                t["card"]._dash_copy = (f"{self._DASH_TITLES[m]} module — no "
                                        f"{self._DASH_NOUNS[m]} habitats are "
                                        f"mapped in this scheme.")
                continue
            bl, pr, pct = dm["bl"], dm["pr"], dm["pct"]
            sv = slots_by_mod.get(m) or {}
            trading_ok = bool(sv.get("trading_ok", True))
            target = bl * 1.10
            ring_pct = 100.0 if target <= 1e-9 else min(100.0, pr / target * 100.0)
            mod_ok = trading_ok and (pct is None or pct >= 10.0)
            t["ring"].set_state(ring_pct, "GOOD" if mod_ok else "BAD",
                                "check" if mod_ok else "x")
            t["value"].setText("—" if pct is None else f"{pct:+.2f}%")
            self._dcolor(t["value"], self._dash_state_color(pct))
            # Baseline and net change, which is what the percentage above is
            # made of: net / on-site baseline. Showing the combined baseline
            # here instead made the tile's own arithmetic not add up.
            bl_on = dm.get("bl_on", bl)
            net = (pr or 0.0) - (bl or 0.0)
            t["units"].setText(
                f"{bl_on:.2f} u baseline\n{net:+.2f} u net"
            )
            self._dcolor(t["units"], theme.FAINT)
            t["units"].setToolTip(
                f"{bl_on:.2f} on-site baseline units, net change {net:+.2f} "
                f"units across the whole scheme — the metric measures gain "
                f"against the on-site baseline.")
            if not trading_ok:
                fail = "policy ⚠" if m == "watercourse" else "trading ✗"
                self._dstyle(t["sub"],
                             f"color:{theme.BAD}; background:{theme.BAD_BG};"
                             f" border:none; border-radius:7px; padding:1px 7px;"
                             f" font-size:9px; font-weight:500;"
                             f" font-family:{theme.FONT_MEDIUM};")
                t["sub"].setText(fail)
            else:
                self._dstyle(t["sub"],
                             f"color:{theme.FAINT}; font-size:9px;"
                             f" background:transparent; border:none;"
                             f" font-family:{theme.FONT_MEDIUM};")
                t["sub"].setText("compliant" if mod_ok else "below +10%")
            tip = [f"baseline {bl:.2f} u  →  proposed {pr:.2f} u",
                   f"target (+10%)  {target:.2f} u   ·   net {pr - bl:+.2f} u"]
            for s in (sv.get("slots") or []):
                if s["need"] <= 0.005:
                    continue
                head, _chip, _tip = self._dash_slot_caption(m, s)
                if s["short"] > 0.005:
                    tip.append(f"✗  {head} — short {s['short']:.2f}")
                else:
                    extra = (f"  ({s['spare']:.2f} spare)"
                             if (s.get("spare") or 0) > 0.005 else "")
                    tip.append(f"✓  {head} — covered{extra}")
            t["card"].setToolTip("\n".join(tip))
            t["card"]._dash_copy = self._dash_tile_prose(
                m, bl, pr, pct, target, sv, trading_ok, mod_ok)

        # ── What changed ─────────────────────────────────────────────
        movers = (payload or {}).get("movers") or []
        d["movers_card"].setVisible(bool(movers))
        if movers:
            self._dash_clear_rows(d["movers_layout"])
            top = movers[:5]
            maxabs = max(abs(v) for _n, v in top) or 1.0
            for name, delta in top:
                row = QWidget()
                self._dtransparent(row)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 1, 0, 1)
                rl.setSpacing(8)
                nm = self._dlbl(name, 11, wrap=True)
                self._dcolor(nm, theme.MUTED)
                nm.setMinimumWidth(70)
                rl.addWidget(nm, 5)
                rl.addWidget(_DivergingBar(delta, maxabs), 4)
                col = theme.GOOD if delta > 0 else theme.BAD
                vl = self._dlbl(f"{delta:+.2f}", 11, 600,
                                align=Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter, tabular=True)
                self._dcolor(vl, col)
                vl.setMinimumWidth(46)
                rl.addWidget(vl)
                d["movers_layout"].addWidget(row)
            if len(movers) > 5:
                more = self._dlbl(f"+{len(movers) - 5} smaller changes", 10,
                                  align=Qt.AlignmentFlag.AlignHCenter)
                self._dcolor(more, theme.FAINT)
                d["movers_layout"].addWidget(more)

        # ── Area split ───────────────────────────────────────────────
        ret, enh, cre, lost = (payload or {}).get("split") or (0, 0, 0, 0)
        show_split = any(v > 1e-9 for v in (ret, enh, cre, lost))
        d["split_card"].setVisible(show_split)
        if show_split:
            self._dash_clear_rows(d["split_bar_slot"])
            # Retained → Enhanced → Created → Lost: GOOD green and BAD red
            # never sit adjacent (deutan-safe; the 2px gaps do the separating).
            segs = [(ret, theme.SPLIT_RETAINED), (enh, theme.GOOD),
                    (cre, theme.SPLIT_CREATED), (lost, theme.BAD)]
            d["split_bar_slot"].addWidget(_StackedBar(segs))
            cols = (theme.SPLIT_RETAINED, theme.GOOD,
                    theme.SPLIT_CREATED, theme.BAD)
            vals = (ret, enh, cre, lost)
            for (sw, nm, vl), col, v in zip(d["split_legend"], cols, vals):
                self._dstyle(sw, f"background:{col}; border:none;"
                                 f" border-radius:3px;")
                self._dcolor(nm, theme.MUTED)
                self._dcolor(vl, theme.INK)
                vl.setText(f"{v:.2f}")

        # ── Statutory credits ────────────────────────────────────────
        credits = (payload or {}).get("credits") or {}
        # The compliance verdict deliberately tolerates up to 0.005 units of
        # floating/measurement residue. Credits are shortfall ×2, so that same
        # harmless residue can appear as 0.01 credits after rounding. Never
        # contradict a compliant verdict with a purchase requirement.
        nz = ([] if compliant else
              [(t_, v) for t_, v in credits.items() if v > 5e-4])
        nz.sort(key=lambda kv: -kv[1])
        d["credits_card"].setVisible(bool(active))
        self._dash_clear_rows(d["credits_rows"])
        d["credits_zero"].setVisible(bool(active) and not nz)
        if nz:
            cmax = max(v for _t, v in nz)
            tier_fill = {"H": theme.MOD_HEDGE, "W": theme.INFO}
            for t_, v in nz:
                row = QWidget()
                self._dtransparent(row)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(9)
                chip = QLabel(t_)
                chip.setFixedWidth(34)
                chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._dstyle(chip,
                             f"background:{tier_fill.get(t_, theme.ACCENT_DARK)};"
                             f" color:#FFFFFF; border:none; border-radius:4px;"
                             f" padding:2px 0; font-size:10.5px; font-weight:600;"
                             f" font-family:{theme.FONT_MEDIUM};")
                rl.addWidget(chip)
                rl.addWidget(_MiniBar(v, cmax, theme.BAD), 1)
                vl = self._dlbl(f"{v:.2f}", 11, 600,
                                align=Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter, tabular=True)
                self._dcolor(vl, theme.INK)
                vl.setMinimumWidth(42)
                rl.addWidget(vl)
                d["credits_rows"].addWidget(row)

        # ── Data quality ─────────────────────────────────────────────
        total = quality.get("total", 0)
        d["quality_card"].setVisible(total > 0)
        if total > 0:
            self._dash_clear_rows(d["quality_chips"])

            def _chip(text, kind="plain"):
                c = QLabel(text)
                fg, bg = {"plain": (theme.MUTED, theme.SUNKEN),
                          "warn": (theme.WARN, theme.WARN_BG),
                          "good": (theme.GOOD, theme.GOOD_BG),
                          "bad": (theme.BAD, theme.BAD_BG)}[kind]
                self._dstyle(c, f"color:{fg}; background:{bg}; border:none;"
                                f" border-radius:6px; padding:3px 8px;"
                                f" font-size:10px;"
                                f" font-family:{theme.FONT_MEDIUM};")
                return c

            d["quality_chips"].addWidget(_chip(f"{total} features"))
            inc = quality.get("incomplete", 0)
            d["quality_chips"].addWidget(
                _chip(f"{inc} incomplete", "warn") if inc
                else _chip("all complete", "good"))
            cov = (payload or {}).get("coverage")
            if cov:
                rl_ha, bl_ha, pr_ha = cov
                if rl_ha > 1e-6:
                    gap = max(rl_ha - bl_ha, rl_ha - pr_ha)
                    pct = min(bl_ha, pr_ha) / rl_ha * 100.0
                    d["quality_chips"].addWidget(
                        _chip(f"{pct:.0f}% mapped", "warn") if gap > 5e-3
                        else _chip("100% mapped", "good"))
            geom_bad = quality.get("geom_bad", 0)
            d["quality_chips"].addWidget(
                _chip(f"geometry ✗ {geom_bad}", "bad") if geom_bad
                else _chip("geometry ✓", "good"))

            self._dash_clear_rows(d["quality_detail_rows"])
            for i, (label, count, inc_n) in enumerate(quality.get("rows", [])):
                if i:
                    sep = QFrame()
                    sep.setFixedHeight(1)
                    self._dstyle(sep, f"background:{theme.BORDER}; border:none;")
                    d["quality_detail_rows"].addWidget(sep)
                row = QWidget()
                self._dtransparent(row)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 4, 0, 4)
                rl.setSpacing(6)
                nm = self._dlbl(label, 11)
                self._dcolor(nm, theme.MUTED)
                rl.addWidget(nm, 1)
                cl = self._dlbl(str(count), 11, 600,
                                align=Qt.AlignmentFlag.AlignRight, tabular=True)
                self._dcolor(cl, theme.INK)
                rl.addWidget(cl)
                if inc_n:
                    wa = QLabel(f"{inc_n} incomplete")
                    self._dstyle(wa, f"color:{theme.WARN};"
                                     f" background:{theme.WARN_BG}; border:none;"
                                     f" border-radius:4px; padding:1px 5px;"
                                     f" font-size:10px;"
                                     f" font-family:{theme.FONT_MEDIUM};")
                    rl.addWidget(wa)
                d["quality_detail_rows"].addWidget(row)

    def _dash_tile_prose(self, module, bl, pr, pct, target, sv, trading_ok,
                         mod_ok):
        """Natural-language summary of one module for the right-click copy —
        written to stand alone in an email or report, unlike the tooltip's
        space-constrained shorthand."""
        title = self._DASH_TITLES[module]
        if pct is None:
            hdr = (f"{title} module — created habitat only (no baseline "
                   f"units to measure a percentage against).")
        else:
            hdr = f"{title} module — net change {pct:+.2f}%."
        if not trading_ok:
            verdict = ("The watercourse loss policy is not met."
                       if module == "watercourse"
                       else "The trading rules are not satisfied.")
        elif mod_ok:
            verdict = ("Meets the +10% net gain requirement and the trading "
                       "rules.")
        else:
            verdict = "Below the +10% net gain requirement."
        lines = [hdr + "  " + verdict,
                 f"Baseline {bl:.2f} units → proposed {pr:.2f} units; the "
                 f"+10% target is {target:.2f} units (net change "
                 f"{pr - bl:+.2f} units)."]
        needed, covered = [], []
        for s in (sv.get("slots") or []):
            if s["need"] <= 0.005:
                continue
            head, _chip, tip = self._dash_slot_caption(module, s)
            if s["short"] > 0.005:
                needed.append(f"•  {head} — {s['short']:.2f} units still "
                              f"needed ({s['have']:.2f} of {s['need']:.2f} "
                              f"secured). {tip}")
            else:
                spare = s.get("spare") or 0.0
                covered.append(f"•  {head} — covered: {s['need']:.2f} units "
                               f"required, {s['have']:.2f} secured"
                               + (f" ({spare:.2f} spare)" if spare > 0.005
                                  else "") + ".")
        if needed:
            lines.append("Still required:")
            lines.extend(needed)
        if covered:
            lines.append("Already covered:")
            lines.extend(covered)
        return "\n".join(lines)

    def _dash_present_infographic(self):
        """Open the dashboard-as-infographic preview. Computes fresh so the
        poster matches the map even if the tab hasn't refreshed yet."""
        try:
            payload = self._compute_dashboard_data()
        except Exception:
            payload = getattr(self, "_dash_payload", None)
        mods = (payload or {}).get("modules", {})
        if not any((m or {}).get("active") for m in mods.values()):
            try:
                from qgis.core import Qgis
                self.iface.messageBar().pushMessage(
                    "Biome", "Nothing to present yet — draw baseline and "
                    "proposed habitats first.",
                    level=Qgis.MessageLevel.Info, duration=6)
            except Exception:
                pass
            return
        from .dashboard_infographic import InfographicDialog
        InfographicDialog(self, payload, parent=self).exec()

    def _dash_copy_tile(self, tile, pos):
        """Right-click on a module tile: copy its summary text and confirm."""
        txt = getattr(tile, "_dash_copy", "")
        if not txt:
            return
        from qgis.PyQt.QtWidgets import QApplication, QToolTip
        QApplication.clipboard().setText(txt)
        QToolTip.showText(tile.mapToGlobal(pos), "Copied ✓", tile)

    def _dash_obligation_row(self, module, s, start_frac=None):
        """One outstanding obligation in the hero list: module dot + what
        qualifies + how it must be met, a secured/required meter, and the
        shortfall. Rebuilt per refresh (the list length varies)."""
        head, chip_txt, tip = self._dash_slot_caption(module, s)
        row = QWidget()
        rv = QVBoxLayout(row)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(5)
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(7)
        self._dtransparent(row, top)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setPixmap(_dot_pixmap(self._dash_mod_color(module), 8))
        tl.addWidget(dot)
        nm = self._dlbl(head, 11, 600, wrap=True)
        self._dcolor(nm, theme.INK)
        nm.setToolTip(tip)
        tl.addWidget(nm, 1)
        chip = QLabel(chip_txt)
        chip.setToolTip(tip)
        chip_warn = s["kind"] == "floor"
        self._dstyle(chip,
                     f"color:{theme.WARN if chip_warn else theme.FAINT};"
                     f" background:{theme.WARN_BG if chip_warn else theme.SUNKEN};"
                     f" border:none; border-radius:7px; padding:1px 6px;"
                     f" font-size:9px; font-family:{theme.FONT_MEDIUM};")
        tl.addWidget(chip)
        short = self._dlbl(f"{s['short']:.2f} short", 11, 600,
                           align=Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter, tabular=True)
        self._dcolor(short, theme.BAD)
        tl.addWidget(short)
        rv.addWidget(top)
        meter = _ObligationMeter(8)
        meter.set_values(s["need"], s["have"], start_frac=start_frac,
                         animate=True)
        rv.addWidget(meter)
        sub = QWidget()
        self._dtransparent(sub)
        sl = QHBoxLayout(sub)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        left = self._dlbl(f"{s['have']:.2f} u secured", 10, tabular=True)
        self._dcolor(left, theme.FAINT)
        sl.addWidget(left)
        sl.addStretch()
        right = self._dlbl(f"{s['need']:.2f} u required", 10, tabular=True)
        self._dcolor(right, theme.FAINT)
        sl.addWidget(right)
        rv.addWidget(sub)
        # The "replaces lost habitat + the 10% uplift" breakdown used to sit
        # here. It restated the headline shortfall in a framing the metric
        # workbook never shows, so the panel and the metric read differently
        # for the same scheme. Dropped — the tiles now carry baseline ->
        # proposed, which is the figure the metric actually reports.
        return row

    def _summary_on_screen(self):
        """True only when the Summary tab is the visible tab. The summary
        widgets aren't shown on any other tab, so rebuilding them there (an
        expensive classify + credits pass) is wasted work — defer it until the
        user actually opens the tab."""
        try:
            return self._tab_stack.currentIndex() == 2 and self.isVisible()
        except Exception:
            return False

    def _invalidate_enh_cache(self):
        """Drop the area/hedge/water figures cached by the BNG build (_enh_*),
        which the Enhancements tab reads. The BNG build no longer runs eagerly
        on every edit, so clear the cache whenever data changes; the
        Enhancements tab then recomputes fresh figures on demand (see
        enhancement_module_deficits) instead of showing stale ones."""
        for _a in ("_enh_area_baseline", "_enh_area_proposed",
                   "_enh_hedge_baseline", "_enh_hedge_proposed",
                   "_enh_water_baseline", "_enh_water_proposed"):
            if hasattr(self, _a):
                delattr(self, _a)

    def _mark_summary_dirty(self):
        """Record that habitat data changed. If the Summary tab is on screen
        the widgets rebuild immediately so it stays live; otherwise the
        (expensive) rebuild waits until the tab is next opened."""
        self._summary_dirty = True
        self._invalidate_enh_cache()
        try:
            if self._summary_on_screen():
                self._refresh_summary_now()
        except Exception:
            pass
        # Keep the Biome layer panel's stats footer live (cheap recompute).
        try:
            from . import biome_mode
            biome_mode.refresh_layer_panel_stats()
        except Exception:
            pass

    def _refresh_summary_now(self):
        """Recompute and update the Dashboard tab and clear the dirty flag.
        No-op (stays dirty) when the tab isn't on screen."""
        if not self._summary_on_screen():
            self._summary_dirty = True
            return
        self._refresh_dashboard_now()
        self._refresh_geometry_warnings()
        self._summary_dirty = False

    def _refresh_dashboard_now(self):
        """Compute fresh figures and push them into the persistent dashboard,
        building its skeleton on first use. On a compute failure the dashboard
        keeps its last state (an empty payload would read as 'no habitats',
        which is worse than stale numbers) and the error goes to the log."""
        import traceback
        try:
            payload = self._compute_dashboard_data()
        except Exception:
            traceback.print_exc()
            return
        dash = getattr(self, "_dash", None)
        rebuild = dash is None
        if not rebuild:
            try:
                from qgis.PyQt import sip
                rebuild = sip.isdeleted(dash["root"])
            except Exception:
                rebuild = True
        if rebuild:
            self._build_dashboard()
        self._dash_payload = payload
        self._update_dashboard(payload)

    # ── broken-geometry warnings (export guard + Summary/Mapping banners) ──
    def _invalid_geometries(self):
        """Habitat parcels with broken outlines (shared detector). Never raises."""
        try:
            from .measurements import invalid_habitat_geometries
            return invalid_habitat_geometries()
        except Exception:
            return []

    def _geometry_export_guard(self):
        """Warn (and let the user abort) if any habitat parcel has invalid
        geometry — a broken outline silently zeroes its area in the exported
        metric. Returns True to proceed with the export, False to abort."""
        bad = self._invalid_geometries()
        if not bad:
            return True
        from qgis.PyQt.QtWidgets import QMessageBox
        n = len(bad)

        def _reading(it):
            a = it.get("area_ha")
            if a is None:
                return ""
            if a < 0:
                return "  — area reads a NEGATIVE %.3f ha" % abs(a)
            return "  — area reads %.3f ha" % a

        rows = ["• %s%s  (%s)" % (it.get("sub") or "unspecified", _reading(it),
                                  it["layer"].name()) for it in bad[:6]]
        if n > 6:
            rows.append("… and %d more" % (n - 6))
        any_neg = any((it.get("area_ha") or 0) < 0 for it in bad)
        box = QMessageBox(self.iface.mainWindow())
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Broken geometry — metric may be wrong")
        box.setText("%d habitat parcel%s ha%s an invalid outline."
                    % (n, "" if n == 1 else "s", "s" if n == 1 else "ve"))
        box.setInformativeText(
            "A broken outline (e.g. a self-intersection or stray sliver) makes "
            "that parcel's area calculate incorrectly — sometimes zero, sometimes "
            "a wrong non-zero value" +
            (", and here one even computes to a NEGATIVE area (an inverted / "
             "hole-outside-shell outline)" if any_neg else "") +
            " — so the exported metric and its credits can't be trusted until "
            "it's fixed.\n\n" + "\n".join(rows) +
            "\n\nRepair them first (Layers panel → the red ⚠ row → "
            "click to auto-fix), then export.")
        proceed = box.addButton("Export anyway", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel & fix", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(box.buttons()[-1])
        box.exec()
        return box.clickedButton() is proceed

    def _zoom_to_invalid(self, layer, fid):
        """Centre the canvas on one feature so a flagged parcel is easy to find."""
        try:
            from qgis.core import QgsCoordinateTransform, QgsProject
            feat = layer.getFeature(fid)
            g = feat.geometry()
            if g is None or g.isNull() or g.isEmpty():
                return
            canvas = self.iface.mapCanvas()
            bb = g.boundingBox()
            try:
                tr = QgsCoordinateTransform(
                    layer.crs(), canvas.mapSettings().destinationCrs(),
                    QgsProject.instance())
                bb = tr.transformBoundingBox(bb)
            except Exception:
                pass
            if bb.isEmpty():
                return
            bb.scale(2.5)
            canvas.setExtent(bb)
            canvas.refresh()
        except Exception:
            pass

    def _geometry_warning_widget(self):
        """A red, clickable banner if any habitat parcel has invalid geometry,
        else None. Clicking zooms to the first offender."""
        bad = self._invalid_geometries()
        if not bad:
            return None
        n = len(bad)
        neg = [it for it in bad if (it.get("area_ha") or 0) < 0]
        neg_note = (" One is even reading a negative %.3f ha — a sure sign the "
                    "outline is inverted." % abs(neg[0]["area_ha"])) if neg else ""
        html = ("<b>%d parcel%s with broken geometry.</b> "
                "Their area calculates incorrectly, so the metric and coverage "
                "can't be trusted until fixed.%s Use the Layers panel (red ⚠ list → "
                "click to repair) or redraw. Click to locate the first one."
                % (n, "" if n == 1 else "s", neg_note))
        w = _banner("status-alert", html, fg=theme.BAD, bg=theme.BAD_BG,
                    icon_color=theme.BAD, font_px=11,
                    border="1px solid %s" % theme.BAD)
        first = bad[0]
        w.setCursor(Qt.CursorShape.PointingHandCursor)
        w.mousePressEvent = (lambda _e, l=first["layer"], f=first["fid"]:
                             self._zoom_to_invalid(l, f))
        return w

    def _refresh_geometry_warnings(self):
        """Show/hide + rebuild the Summary tab's broken-geometry banner."""
        holder = getattr(self, "_summary_geo_holder", None)
        if holder is None:
            return
        lay = holder.layout()
        while lay.count():
            item = lay.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)
                wdg.deleteLater()
        banner = self._geometry_warning_widget()
        if banner is not None:
            lay.addWidget(banner)
            holder.setVisible(True)
        else:
            holder.setVisible(False)

    def _refresh_bng_widget(self):
        """Legacy entry point (habitat_form / map tools / plugin hooks) — the
        dashboard refreshes as one unit now. Callers that need the FIGURES
        regardless of what's on screen (the Enhancements tab) call
        _compute_dashboard_data directly."""
        self._request_dashboard_refresh()

    def enhancement_module_deficits(self):
        """Return per-module deficit figures for the off-site Enhancements tab:
        {'area': units, 'hedgerow': units, 'watercourse': units} where each is
        max(0, baseline*1.10 - proposed). Triggers a summary compute if the
        figures aren't cached yet. Missing modules default to 0.0."""
        if not hasattr(self, "_enh_area_baseline") \
                and not getattr(self, "_dash_computing", False):
            try:
                # Compute the figures directly: the dashboard refresh no-ops
                # while the Enhancements tab (not the Dashboard) is on screen.
                self._compute_dashboard_data()
            except Exception:
                pass

        def _deficit(bl_attr, pr_attr):
            bl = getattr(self, bl_attr, None)
            pr = getattr(self, pr_attr, None)
            if bl is None:
                return 0.0
            return max(0.0, bl * 1.10 - (pr if pr is not None else 0.0))

        return {
            "area":        _deficit("_enh_area_baseline",  "_enh_area_proposed"),
            "hedgerow":    _deficit("_enh_hedge_baseline", "_enh_hedge_proposed"),
            "watercourse": _deficit("_enh_water_baseline", "_enh_water_proposed"),
        }

    def enhancement_band_requirements(self):
        """Per-module purchase list for the Enhancements 'Market & credits' tab:
        {module: credits_engine.band_requirements(...)} — how many units to
        secure and at what distinctiveness, mirroring the metric's Trading
        Summary. Anchored on the SAME cached baseline/proposed figures as
        enhancement_module_deficits() so the rows sum to the total shown beside
        them. Returns {} if the classifiers are unavailable."""
        try:
            from . import credits_engine as CE
        except Exception:
            return {}
        return {m: CE.band_requirements(n, bl, pr, m)
                for m, (n, bl, pr) in self._module_nets().items()}

    def module_slot_view(self, dashboard_scope=False):
        """{module: credits_engine.slot_view(...)} — the Net Gain card's
        obligation slots. Same nets and the same cached totals as
        enhancement_band_requirements(), so the two views agree by construction;
        slot_view's total_short reconciles to band_requirements' total."""
        try:
            from . import credits_engine as CE
        except Exception:
            return {}
        return {m: CE.slot_view(n, bl, pr, m)
                for m, (n, bl, pr) in
                self._module_nets(dashboard_scope=dashboard_scope).items()}

    def _module_nets(self, dashboard_scope=False):
        """{module: (nets, baseline_total, post_total)} — the shared basis for
        every trading view. Kept in one place so the Market & credits tab and the
        Net Gain slots can never drift apart."""
        try:
            from . import credits_engine as CE
            from .xlsm_exporter import (classify_area_net, classify_linear_net,
                                        _collect_trees, _tree_rows_as_area_rows,
                                        _aggregate_rows_by_key, _AGG_KEYS)
        except Exception:
            return {}
        if not hasattr(self, "_enh_area_baseline") \
                and not getattr(self, "_dash_computing", False):
            try:
                self._compute_dashboard_data()
            except Exception:
                pass

        def _totals(nets, bl_attr, pr_attr, on_attr=None):
            """Prefer the cached headline figures so the rows reconcile with the
            'Total required' table; fall back to the nets' own sums.

            The pair returned is the basis for the scheme obligation. Off-site
            delivery is included in the net change, while the development-site
            target uses the on-site baseline. The gain site's own 10% test is
            handled in the off-site metric block, not added to the developer's
            obligation a second time.
            """
            bl = getattr(self, bl_attr, None)
            pr = getattr(self, pr_attr, None)
            if bl is None or pr is None:
                bl = sum(s["baseline"] for s in nets.values())
                pr = sum(s["post"] for s in nets.values())
            bl, pr = float(bl), float(pr)
            bl_on = getattr(self, on_attr, None) if on_attr else None
            if bl_on is not None and abs(float(bl_on) - bl) > 1e-9:
                bl_on = float(bl_on)
                return bl_on, bl_on + (pr - bl)
            return bl, pr

        out = {}
        _onsite_only = bool(
            dashboard_scope
            and not getattr(self, "_dashboard_include_offsite", True))
        _scope = False if _onsite_only else None
        # Area — trees folded in as area-equivalent rows, exactly as the metric
        # export and the credits path do (a lost tree IS a Medium replacement).
        try:
            bl_rows, cr_rows, en_rows = classify_area_net(
                self.iface, offsite=_scope)
            t_bl, t_cr, t_en = _collect_trees(self.iface, offsite=_scope)
            bl_rows = _aggregate_rows_by_key(
                list(bl_rows) + _tree_rows_as_area_rows(t_bl, baseline=True),
                _AGG_KEYS["area_baseline"])
            cr_rows = _aggregate_rows_by_key(
                list(cr_rows) + _tree_rows_as_area_rows(t_cr, baseline=False),
                _AGG_KEYS["area_creation"])
            nets = CE.net_by_habitat(bl_rows, cr_rows, list(en_rows) + list(t_en))
            bl, pr = _totals(nets, "_enh_area_baseline", "_enh_area_proposed",
                             "_enh_area_baseline_onsite")
            out["area"] = (nets, bl, pr)
        except Exception:
            pass

        for mod, blk, prk, bl_attr, pr_attr, on_attr in (
                ("hedgerow", "baseline_hedgerow", "proposed_hedgerow",
                 "_enh_hedge_baseline", "_enh_hedge_proposed",
                 "_enh_hedge_baseline_onsite"),
                ("watercourse", "baseline_water", "proposed_water",
                 "_enh_water_baseline", "_enh_water_proposed",
                 "_enh_water_baseline_onsite")):
            try:
                x_bl, x_cr, x_en = classify_linear_net(
                    self.iface, blk, prk, offsite=_scope)
                nets = CE.net_by_habitat(x_bl, x_cr, x_en, area_key="length_km",
                                         water=(mod == "watercourse"))
                bl, pr = _totals(nets, bl_attr, pr_attr, on_attr)
                out[mod] = (nets, bl, pr)
            except Exception:
                continue
        return out

    def enhancement_unit_totals(self):
        """Return (baseline_area_units, proposed_area_units) for the current
        project. Prefers the figures cached by the balance/summary widget so
        the Enhancements shortfall matches the Unit Deficit Summary exactly.
        Falls back to recomputing only if the summary hasn't run yet."""
        # Prefer the cached, summary-authoritative figures.
        cached_bl = getattr(self, "_enh_area_baseline", None)
        cached_pr = getattr(self, "_enh_area_proposed", None)
        if (cached_bl is None or cached_pr is None) \
                and not getattr(self, "_dash_computing", False):
            # Compute the figures directly (populates _enh_*) — the dashboard
            # refresh no-ops while the Dashboard tab is off screen.
            try:
                self._compute_dashboard_data()
            except Exception:
                pass
            cached_bl = getattr(self, "_enh_area_baseline", None)
            cached_pr = getattr(self, "_enh_area_proposed", None)
        if cached_bl is not None and cached_pr is not None:
            return cached_bl, cached_pr
        return self._enhancement_unit_totals_fallback()

    def _enhancement_unit_totals_fallback(self):
        """Recompute area baseline/proposed directly (used only if the summary
        cache is unavailable)."""
        from .habitat_data import (DISTINCTIVENESS_SCORES, CONDITION_MULTIPLIERS,
                                    STRATEGIC_MULTIPLIERS)
        try:
            from .xlsm_exporter import classify_area_net
            bl_rows, cr_rows, en_rows = classify_area_net(self.iface)
        except Exception:
            return None, None
        if not bl_rows and not cr_rows and not en_rows:
            return None, None

        try:
            from . import bng_multipliers as BNGM
        except Exception:
            BNGM = None

        def _d(s):  return DISTINCTIVENESS_SCORES.get(s, 0)
        def _c(c):  return CONDITION_MULTIPLIERS.get(c or "", 1.0)
        def _s(s):  return STRATEGIC_MULTIPLIERS.get(s or "", 1.0)

        def base_rate(r, area_key="area_ha"):
            return (float(r.get(area_key) or 0.0) * _d(r.get("sub_habitat_type", ""))
                    * _c(r.get("condition", "")) * _s(r.get("strategic_significance", "")))

        def prop_rate(r, kind, area_key="area_ha"):
            strat = r.get("strategic_significance", "")
            ha    = float(r.get(area_key) or 0.0)
            if kind == "enhanced":
                sub  = r.get("prop_sub_habitat_type") or r.get("sub_habitat_type", "")
                cond = r.get("prop_condition") or r.get("condition", "")
                start = r.get("condition", "")
                # Baseline value of the enhanced ground, kept whole (only the
                # uplift is discounted) — matches the metric's A-3 units.
                base_val = ha * _d(r.get("sub_habitat_type", "")) * _c(start) * _s(strat)
            else:
                sub, cond, start = r.get("sub_habitat_type", ""), r.get("condition", ""), None
                base_val = 0.0
            base = ha * _d(sub) * _c(cond) * _s(strat)
            if BNGM is None:
                return base
            mult, _note = BNGM.proposed_multiplier(
                kind, sub, cond, start_condition=start,
                baseline_habitat=r.get("sub_habitat_type", ""))
            return BNGM.blend_enhanced_units(base_val, base, mult)

        baseline = sum(base_rate(r) for r in bl_rows
                       if r.get("broad_habitat") != "Individual trees")
        proposed = 0.0
        for r in bl_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            proposed += base_rate({**r, "area_ha": float(r.get("area_retained_ha") or 0.0)})
        for r in en_rows:
            if r.get("broad_habitat") != "Individual trees":
                proposed += prop_rate(r, "enhanced")
        for r in cr_rows:
            if r.get("broad_habitat") != "Individual trees":
                proposed += prop_rate(r, "created")
        return baseline, proposed

    def enhancement_uplift_parcels(self):
        """Candidate parcels for in-place condition uplift.

        Uplift can only happen on PROPOSED habitats — you can't uplift a
        baseline parcel that's being developed over. So we read the proposed
        side (retained + created), and only offer parcels that:
          * have non-zero distinctiveness (zero-value habitats like
            'Developed land; sealed surface' cannot be uplifted), and
          * have an assessable condition below Good (room to improve).

        Returns list of {habitat, condition, area_ha}."""
        from .habitat_data import DISTINCTIVENESS_SCORES
        try:
            from .xlsm_exporter import classify_area_net
            bl_rows, cr_rows, en_rows = classify_area_net(self.iface)
        except Exception:
            return []

        NON_ASSESSABLE = {"", "Good", "N/A - Other", "Condition Assessment N/A"}

        def _candidate(sub, cond, ha):
            if not sub or ha <= 0:
                return None
            if DISTINCTIVENESS_SCORES.get(sub, 0) <= 0:
                return None                    # zero-value -> cannot uplift
            if cond in NON_ASSESSABLE:
                return None                    # already Good or non-assessable
            return {"habitat": sub, "condition": cond, "area_ha": ha}

        out = []
        # Retained proposed ground (baseline parcels carried into proposed)
        for r in bl_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            ha = float(r.get("area_retained_ha") or 0.0)
            c = _candidate(r.get("sub_habitat_type", ""),
                           (r.get("condition") or "").strip(), ha)
            if c:
                out.append(c)
        # Created proposed habitats (new habitat the scheme proposes)
        for r in cr_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            ha = float(r.get("area_ha") or 0.0)
            c = _candidate(r.get("sub_habitat_type", ""),
                           (r.get("condition") or "").strip(), ha)
            if c:
                out.append(c)
        return out

    def enhancement_band_breakdown(self, include_trees=False):
        """Per-currency AREA losses, aggregated per (habitat, condition). Returns
        loss dicts {module:'area', habitat, condition, band, units}. Individual
        trees are EXCLUDED by default (additive on-site, so the on-site currency
        view ignores them); pass include_trees=True for the report, where a lost
        tree IS a medium-distinctiveness replacement requirement.
        """
        try:
            from .xlsm_exporter import classify_area_net
            from . import enhancement_engine as ENG
            bl_rows, cr_rows, en_rows = classify_area_net(self.iface)
        except Exception:
            return []
        if include_trees:
            # Individual trees live in their own layer, so the area-net classifier
            # never sees them — fold the lost trees in (as the metric export does)
            # so a lost tree shows as a medium-distinctiveness replacement need.
            try:
                from .xlsm_exporter import _collect_trees, _tree_rows_as_area_rows
                _tbl, _tcr, _ten = _collect_trees(self.iface)
                bl_rows = list(bl_rows) + _tree_rows_as_area_rows(_tbl, baseline=True)
            except Exception:
                pass
        agg = {}
        for r in bl_rows:
            if not include_trees and r.get("broad_habitat") == "Individual trees":
                continue
            sub = r.get("sub_habitat_type", "")
            cond = (r.get("condition") or "").strip()
            ss = r.get("strategic_significance", "")
            lost_area = float(r.get("area_ha") or 0.0) - float(r.get("area_retained_ha") or 0.0)
            if lost_area <= 1e-9 or not sub:
                continue
            u = ENG.baseline_units(sub, cond, lost_area, ss)
            if u <= 0:
                continue
            k = (sub, cond)
            if k in agg:
                agg[k]["units"] += u
            else:
                agg[k] = {"module": "area", "habitat": sub, "condition": cond,
                          "band": ENG.band_name(sub), "group": ENG.broad_group(sub),
                          "units": u}
        return list(agg.values())

    def enhancement_credits(self):
        """Per-tier statutory biodiversity credits the scheme would need (A1-A5,
        H, W) — the SAME credits_engine path the Summary uses. credits = unit
        shortfall × 2 (the statutory 2:1 rule). Returns {tier: credits}; {} on
        failure. Used by the Enhancements 'Market & credits' tab to price them."""
        try:
            from . import credits_engine as _CE
            from .xlsm_exporter import (_spatial_classify_area as _SCA,
                                        _collect_trees as _CTr,
                                        _tree_rows_as_area_rows as _TrR,
                                        _aggregate_rows_by_key as _AggR,
                                        _AGG_KEYS as _AggK,
                                        classify_linear_net as _ClnL)
            abl, acr, aen = _SCA(self.iface)
            tbR, tcR, teR = _CTr(self.iface)
            abl += _TrR(tbR, baseline=True); acr += _TrR(tcR, baseline=False); aen += teR
            abl = _AggR(abl, _AggK["area_baseline"]); acr = _AggR(acr, _AggK["area_creation"])
            anets = _CE.net_by_habitat(abl, acr, aen)
            abt = sum(s["baseline"] for s in anets.values())
            apt = sum(s["post"] for s in anets.values())
            tiers = _CE.tier_credits(anets, abt, apt)
            credits = {t: tiers[t]["credits"] for t in ("A1", "A2", "A3", "A4", "A5")}
            for mk, bk, pk in (("H", "baseline_hedgerow", "proposed_hedgerow"),
                               ("W", "baseline_water", "proposed_water")):
                try:
                    hbR, hcR, heR = _ClnL(self.iface, bk, pk)
                    hnR = _CE.net_by_habitat(hbR, hcR, heR, area_key="length_km",
                                             water=("water" in bk))
                    if mk == "W":
                        credits["W"] = _CE.water_credit(hnR)[0]
                    else:
                        hbt = sum(s["baseline"] for s in hnR.values())
                        hpt = sum(s["post"] for s in hnR.values())
                        credits[mk] = round(max(0.0, hbt * 1.10 - hpt) * 2, 4)
                except Exception:
                    credits[mk] = 0.0
            return credits
        except Exception:
            return {}

    def enhancement_retained_parcels(self):
        """RETAINED baseline parcels — the canvas for on-site enhancement.

        Returns the retained portion of every baseline AREA parcel at its
        BASELINE habitat, condition and strategic significance. This is the land
        that survives development and can therefore be enhanced; lost ground and
        proposed/created habitats are deliberately excluded (they are the
        development, not an enhancement opportunity). Individual trees are
        excluded (additive module). Each: {habitat, broad, condition, strategic,
        area_ha}. Feeds enhancement_engine.moves_for_parcel()."""
        try:
            from .xlsm_exporter import classify_area_net
            bl_rows, _cr, _en = classify_area_net(self.iface)
        except Exception:
            return []
        out = []
        for r in bl_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            ha = float(r.get("area_retained_ha") or 0.0)
            sub = r.get("sub_habitat_type", "")
            if ha <= 1e-9 or not sub:
                continue
            out.append({
                "habitat":   sub,
                "broad":     r.get("broad_habitat", ""),
                "condition": (r.get("condition") or "").strip(),
                "strategic": r.get("strategic_significance", ""),
                "area_ha":   ha,
            })
        return out

    def enhancement_retained_linear(self, module):
        """Retained baseline reaches for a LINEAR module ('hedgerow' |
        'watercourse') — the length that survives the development, at its
        baseline type, condition and strategic significance. Feeds
        enhancement_engine.linear_recipes(); linear features have no land budget
        so there is no 'by parcel' view. Each: {habitat, condition, km, strategic}."""
        keys = {"hedgerow":    ("baseline_hedgerow", "proposed_hedgerow"),
                "watercourse": ("baseline_water",    "proposed_water")}.get(module)
        if not keys:
            return []
        try:
            from .xlsm_exporter import classify_linear_net
            bl_rows, _cr, _en = classify_linear_net(self.iface, keys[0], keys[1])
        except Exception:
            return []
        out = []
        for r in bl_rows:
            km = float(r.get("length_retained_km") or 0.0)
            sub = r.get("sub_habitat_type", "")
            if km <= 1e-9 or not sub:
                continue
            out.append({
                "habitat":   sub,
                "condition": (r.get("condition") or "").strip(),
                "strategic": r.get("strategic_significance", ""),
                "km":        km,
            })
        return out

    def _compute_dashboard_data(self):
        """Compute everything the Dashboard shows — pure data, no widgets —
        and cache the _enh_* module figures the Enhancements tab reads. The
        _dash_computing flag stops the enhancement providers recursing back in
        while the compute is mid-flight (module_slot_view runs inside it)."""
        self._dash_computing = True
        try:
            return self._compute_dashboard_data_inner()
        finally:
            self._dash_computing = False

    def _compute_dashboard_data_inner(self):
        from qgis.core import (QgsDistanceArea, QgsProject, QgsUnitTypes)
        from .habitat_data import (
            DISTINCTIVENESS_SCORES, CONDITION_MULTIPLIERS,
            STRATEGIC_MULTIPLIERS, IRREPLACEABLE_SUBTYPES,
        )
        from .layer_manager import TREE_SIZE_AREAS
        from collections import defaultdict
        _irr = set(IRREPLACEABLE_SUBTYPES)   # generate NO units (metric scores 0)

        project = QgsProject.instance()

        # ── helpers ────────────────────────────────────────────────────
        def _area_calc(layer):
            ac = QgsDistanceArea()
            ac.setSourceCrs(layer.crs(), project.transformContext())
            ac.setEllipsoid(measurement_ellipsoid())
            return ac

        def _dist_calc(layer):
            dc = QgsDistanceArea()
            dc.setSourceCrs(layer.crs(), project.transformContext())
            dc.setEllipsoid(measurement_ellipsoid())
            return dc

        def _d(sub_type):
            """Distinctiveness score — 0 if unknown."""
            return DISTINCTIVENESS_SCORES.get(sub_type, 0)

        def _c(condition):
            """Condition multiplier — default 1.0."""
            return CONDITION_MULTIPLIERS.get(condition or "", 1.0)

        def _s(strategic):
            """Strategic significance multiplier — default 1.0."""
            return STRATEGIC_MULTIPLIERS.get(strategic or "", 1.0)

        def _field(feat, name):
            idx = feat.fieldNameIndex(name)
            return str(feat[name] or "").strip() if idx >= 0 else ""

        # ── calculate units for one layer ──────────────────────────────
        def _area_units(layer_key):
            """Sum indicative area habitat units for an area layer."""
            layer = self.layer_manager.get_layer(layer_key)
            if layer is None or not layer.isValid():
                return 0.0
            ac = _area_calc(layer)
            total = 0.0
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull():
                    continue
                ha = abs(ac.convertAreaMeasurement(
                    ac.measureArea(geom), QgsUnitTypes.AreaUnit.AreaHectares))
                if ha < 0:
                    try:
                        poly = geom.asPolygon()
                        if poly:
                            from qgis.core import QgsGeometry
                            outer = QgsGeometry.fromPolygonXY([poly[0]])
                            ha = abs(ac.convertAreaMeasurement(ac.measureArea(outer), QgsUnitTypes.AreaUnit.AreaHectares))
                    except Exception:
                        ha = 0.0
                sub  = _field(feat, "sub_habitat_type")
                cond = _field(feat, "condition")
                strat = _field(feat, "strategic_significance")
                total += ha * _d(sub) * _c(cond) * _s(strat)
            return total

        def _tree_units(layer_key):
            """Sum indicative area units for a tree layer (Medium dist = 4)."""
            from .layer_manager import tree_size_from_sub_type
            layer = self.layer_manager.get_layer(layer_key)
            if layer is None or not layer.isValid():
                return 0.0
            total = 0.0
            TREE_DIST = 4  # all trees are Medium distinctiveness
            for feat in layer.getFeatures():
                sub   = _field(feat, "sub_habitat_type")
                cond  = _field(feat, "condition")
                strat = _field(feat, "strategic_significance")
                size_key = tree_size_from_sub_type(sub)
                ha = TREE_SIZE_AREAS.get(size_key, TREE_SIZE_AREAS["Small"])
                total += ha * TREE_DIST * _c(cond) * _s(strat)
            return total

        def _linear_units(layer_key):
            """Sum indicative linear units (hedgerow or watercourse).

            Watercourse units carry the metric's channel x banks encroachment
            multipliers; a reach with unset/invalid encroachment scores zero
            units, exactly as the metric does."""
            layer = self.layer_manager.get_layer(layer_key)
            if layer is None or not layer.isValid():
                return 0.0
            water = "water" in layer_key
            from . import bng_multipliers as _BM
            dc = _dist_calc(layer)
            total = 0.0
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull():
                    continue
                km   = dc.measureLength(geom) / 1000.0
                sub  = _field(feat, "sub_habitat_type")
                cond = _field(feat, "condition")
                strat = _field(feat, "strategic_significance")
                u = km * _d(sub) * _c(cond) * _s(strat)
                if water:
                    u *= _BM.water_encroachment_mult_fields(
                        _field(feat, "watercourse_encroachment"),
                        _field(feat, "riparian_encroachment_l"),
                        _field(feat, "riparian_encroachment_r"))
                total += u
            return total

        # ── gather all six figures using the net-difference engine for area ─
        # Same single source of truth as the export and the balance widget.
        # Retained areas keep their baseline unit rate; enhanced and created
        # areas use their proposed unit rate.
        from .xlsm_exporter import classify_area_net, _CONDITION_SCORE
        # Habitat mapped inside a blue line is metric OFF-SITE: it is scored on
        # the D sheets, where an enhancement is allowed to start from a lower
        # distinctiveness than on-site A-3 would ever permit. Creation and
        # enhancement carry different temporal discounts, so letting the
        # on-site trading rule pick the verdict for land it does not govern
        # gets the discount wrong. Score that land under its own rules and add
        # the result. (Verified against the metric: off-site 3.9494 -> 14.4275.)
        _include_offsite = bool(
            getattr(self, "_dashboard_include_offsite", True))
        _off_area_bl = _off_area_pr = 0.0
        _split_off = False
        try:
            from .offsite_from_map import has_mapped_offsite, offsite_area_units
            _split_off = bool(has_mapped_offsite())
            if _split_off and _include_offsite:
                _off_area_bl, _off_area_pr = offsite_area_units()
        except Exception:
            _split_off, _off_area_bl, _off_area_pr = False, 0.0, 0.0
        try:
            _sp_bl_rows, _sp_cr_rows, _sp_en_rows = classify_area_net(self.iface)
        except Exception:
            _sp_bl_rows, _sp_cr_rows, _sp_en_rows = [], [], []
        # Unit totals come from the ON-SITE rows plus the off-site block above;
        # the combined rows stay as they are because the "what changed" lists
        # and the habitat breakdown below read them and must still cover the
        # whole map.
        if _split_off:
            try:
                _u_bl_rows, _u_cr_rows, _u_en_rows = classify_area_net(
                    self.iface, offsite=False)
            except Exception:
                _u_bl_rows, _u_cr_rows, _u_en_rows = (
                    _sp_bl_rows, _sp_cr_rows, _sp_en_rows)
                _off_area_bl = _off_area_pr = 0.0
        else:
            _u_bl_rows, _u_cr_rows, _u_en_rows = (
                _sp_bl_rows, _sp_cr_rows, _sp_en_rows)

        # With off-site hidden, every dashboard section must read the on-site
        # rows—not just the headline totals—so area split, movers, obligations
        # and credits all behave as if blue-line-only habitat were absent.
        if _split_off and not _include_offsite:
            _sp_bl_rows, _sp_cr_rows, _sp_en_rows = (
                _u_bl_rows, _u_cr_rows, _u_en_rows)


        def _units_from_row(row, area_key="area_ha"):
            sub   = row.get("sub_habitat_type", "")
            if sub in _irr:
                return 0.0                    # irreplaceable -> no units (metric)
            cond  = row.get("condition", "")
            strat = row.get("strategic_significance", "")
            ha    = float(row.get(area_key) or 0.0)
            return ha * _d(sub) * _c(cond) * _s(strat)

        # Standard metric multipliers (temporal time-to-target + technical
        # difficulty) for CREATED / ENHANCED parcels, so the indicative summary
        # tracks the statutory metric instead of counting new habitat at full
        # face value. Retained habitat is never discounted.
        try:
            from . import bng_multipliers as _BNGM
        except Exception:
            _BNGM = None

        def _proposed_units_from_row(row, kind, area_key="area_ha"):
            strat = row.get("strategic_significance", "")
            ha    = float(row.get(area_key) or 0.0)
            if kind == "enhanced":
                sub   = row.get("prop_sub_habitat_type") or row.get("sub_habitat_type", "")
                cond  = row.get("prop_condition") or row.get("condition", "")
                start = row.get("condition", "")          # baseline (start) condition
                # Metric enhancement: keep the parcel's baseline value whole and
                # discount only the uplift (see bng_multipliers.blend_enhanced_units).
                base_val = ha * _d(row.get("sub_habitat_type", "")) * _c(start) * _s(strat)
            else:  # created — whole value discounted, no baseline kept
                sub   = row.get("sub_habitat_type", "")
                cond  = row.get("condition", "")
                start = None
                base_val = 0.0
            base  = ha * _d(sub) * _c(cond) * _s(strat)
            if _BNGM is None:
                return base
            mult, note = _BNGM.proposed_multiplier(
                kind, sub, cond, start_condition=start,
                baseline_habitat=row.get("sub_habitat_type", ""))
            if note:
                try:
                    from qgis.core import QgsMessageLog, Qgis
                    QgsMessageLog.logMessage("BNG summary (indicative): " + str(note),
                                             "Biome", Qgis.MessageLevel.Warning)
                except Exception:
                    pass
            return _BNGM.blend_enhanced_units(base_val, base, mult)

        # Baseline area units: full baseline area × baseline rates
        bl_area_spatial = sum(
            _units_from_row(r, "area_ha")
            for r in _u_bl_rows
            if r.get("broad_habitat") != "Individual trees"
        )

        # Proposed area units: retained (baseline rate) + enhanced + created (proposed rates)
        pr_area_spatial = 0.0
        for r in _u_bl_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            # Retained portion uses baseline rate (same sub/cond/strat)
            ret_ha = float(r.get("area_retained_ha") or 0.0)
            pr_area_spatial += _units_from_row({**r, "area_ha": ret_ha})
        # Enhanced and Created rows use their own proposed rates, discounted by
        # the standard temporal (time-to-target) and technical-difficulty multipliers.
        for r in _u_en_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            pr_area_spatial += _proposed_units_from_row(r, "enhanced")
        for r in _u_cr_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            pr_area_spatial += _proposed_units_from_row(r, "created")
        # Off-site block, already discounted by its own D-sheet rules above.
        if _include_offsite:
            bl_area_spatial += _off_area_bl
            pr_area_spatial += _off_area_pr

        # ── Tree units via the SAME net-difference engine as the export ─────
        # Baseline trees: full baseline stock at face rates (identical to the
        # A-1 rows the metric export writes).  Post-development trees:
        # retained at their undiscounted baseline rate, enhanced and created
        # trees discounted by the metric's temporal and difficulty
        # multipliers — exactly what the completed metric will calculate.
        from .xlsm_exporter import _tree_buckets
        from .net_classifier import classify_net_paired as _classify_net
        TREE_DIST = 4  # Urban tree & Rural tree are both Medium (G-1, score 4)

        def _tree_rate(cond, strat, qty_ha):
            return qty_ha * TREE_DIST * _c(cond) * _s(strat)

        bl_tree_units = 0.0
        pr_tree_units = 0.0
        try:
            _tree_scope = (False if (_split_off and not _include_offsite)
                           else None)
            _tree_res = _classify_net(
                _tree_buckets("baseline_trees", offsite=_tree_scope),
                _tree_buckets("proposed_trees", offsite=_tree_scope),
                _CONDITION_SCORE,
                enhance_scope=("broad", "tree_size", "tree_location"),
                no_net=True)
            for g in _tree_res["baseline_groups"].values():
                strat = str((g.get("payload") or {}).get("strategic_significance") or "")
                bl_tree_units += _tree_rate(g["cond"], strat, g["qty"])
                # Retained trees persist unchanged — no discount.
                pr_tree_units += _tree_rate(g["cond"], strat, g["retained"])
            for e in _tree_res["enhanced_records"]:
                _strat = str(e.get("strat") or "")
                base = _tree_rate(e["pr_cond"], _strat, e["qty"])          # proposed value
                base_val = _tree_rate(e["bl_cond"], _strat, e["qty"])      # baseline value kept whole
                if _BNGM is not None:
                    _m, _note = _BNGM.proposed_multiplier(
                        "enhanced", e["pr_sub"], e["pr_cond"],
                        start_condition=e["bl_cond"], baseline_habitat=e.get("bl_sub"))
                    base = _BNGM.blend_enhanced_units(base_val, base, _m)
                pr_tree_units += base
            for cg in _tree_res["created_groups"]:
                base = _tree_rate(cg["cond"], str(cg.get("strat") or ""), cg["qty"])
                if _BNGM is not None:
                    _m, _note = _BNGM.proposed_multiplier("created", cg["sub"], cg["cond"])
                    base *= _m
                pr_tree_units += base
        except Exception:
            # Fall back to face-value layer sums if the engine is unavailable.
            bl_tree_units = _tree_units("baseline_trees")
            pr_tree_units = _tree_units("proposed_trees")

        bl_area  = bl_area_spatial + bl_tree_units
        pr_area  = pr_area_spatial + pr_tree_units

        # The metric measures net gain against the ON-SITE baseline — off-site
        # land is compensation, not part of the target — so the percentages
        # divide by this while the numerator stays the combined change. With
        # nothing tagged off-site this equals bl_area and nothing moves.
        # _u_bl_rows is already the on-site set (it equals the combined set when
        # nothing is tagged), so the denominator is the area total before the
        # off-site block was added, plus trees — which are always on-site.
        bl_area_onsite = (bl_area_spatial - _off_area_bl) + bl_tree_units


        # Cache the authoritative area figures so the Enhancements tab reads the
        # SAME numbers the deficit summary shows (rather than recomputing and
        # disagreeing). Deficit = max(0, baseline*1.10 - proposed).
        self._enh_area_baseline = bl_area
        self._enh_area_proposed = pr_area
        # The 10% net-gain target is 10% of the ON-SITE baseline. Off-site land
        # is compensation the developer brings TO the obligation, so counting
        # its receptor baseline in the target would raise the bar by 10% of
        # land that was never at risk. Cached separately from the true totals
        # above, which must keep reporting the whole map.
        self._enh_area_baseline_onsite = bl_area_onsite
        # Hedgerows: use the SAME pair-aware net classifier as the export so a
        # removed-and-replaced hedge takes its creation discount in the summary
        # too. Watercourses keep the face-value sum (not paired).
        # Per-sub-type unit aggregation for the linear modules, so the
        # "What changed" movers cover hedgerow/watercourse alongside area.
        lin_bl = defaultdict(float)
        lin_pr = defaultdict(float)

        def _hedge_units_via_engine(agg_bl=None, agg_pr=None, offsite=None):
            try:
                from .xlsm_exporter import classify_linear_net
                bl_rows, cr_rows, en_rows = classify_linear_net(
                    self.iface, "baseline_hedgerow", "proposed_hedgerow",
                    offsite=offsite)
            except Exception:
                return _linear_units("baseline_hedgerow"), _linear_units("proposed_hedgerow")

            def _km(r, key):
                return float(r.get(key) or 0.0)

            bl = 0.0
            for r in bl_rows:
                u = (_km(r, "length_km") * _d(r.get("sub_habitat_type", ""))
                     * _c(r.get("condition", "")) * _s(r.get("strategic_significance", "")))
                bl += u
                if agg_bl is not None:
                    agg_bl[r.get("sub_habitat_type", "Unknown")] += u
            pr = 0.0
            for r in bl_rows:
                ret = _km(r, "length_retained_km")
                u = (ret * _d(r.get("sub_habitat_type", ""))
                     * _c(r.get("condition", "")) * _s(r.get("strategic_significance", "")))
                pr += u
                if agg_pr is not None:
                    agg_pr[r.get("sub_habitat_type", "Unknown")] += u
            for r in en_rows:
                sub = r.get("prop_sub_habitat_type") or r.get("sub_habitat_type", "")
                cond = r.get("prop_condition") or r.get("condition", "")
                start = r.get("condition", "")
                strat = r.get("strategic_significance", "")
                length = (_km(r, "length_enhanced_km") or _km(r, "length_km"))
                base = length * _d(sub) * _c(cond) * _s(strat)           # proposed value
                base_val = length * _d(r.get("sub_habitat_type", "")) * _c(start) * _s(strat)
                if _BNGM is not None:
                    m, _n = _BNGM.proposed_multiplier(
                        "enhanced", sub, cond, start_condition=start,
                        baseline_habitat=r.get("sub_habitat_type", ""))
                    base = _BNGM.blend_enhanced_units(base_val, base, m)
                pr += base
                if agg_pr is not None:
                    agg_pr[sub or "Unknown"] += base
            for r in cr_rows:
                sub = r.get("sub_habitat_type", ""); cond = r.get("condition", "")
                base = _km(r, "length_km") * _d(sub) * _c(cond) \
                    * _s(r.get("strategic_significance", ""))
                if _BNGM is not None:
                    m, _n = _BNGM.proposed_multiplier("created", sub, cond)
                    base *= m
                pr += base
                if agg_pr is not None:
                    agg_pr[sub or "Unknown"] += base
            return bl, pr

        def _water_units_via_engine(agg_bl=None, agg_pr=None, offsite=None):
            """Watercourse units via the SAME net classifier the export uses
            (the hedgerow twin above): CREATED reaches take their metric
            difficulty + time-to-target discount (proposed_multiplier), every
            reach is scaled by its channel x banks encroachment, and a
            retained reach whose condition improves is ENHANCED (baseline
            value kept whole, only the uplift discounted)."""
            try:
                from .xlsm_exporter import classify_linear_net
                bl_rows, cr_rows, en_rows = classify_linear_net(
                    self.iface, "baseline_water", "proposed_water",
                    offsite=offsite)
            except Exception:
                return _linear_units("baseline_water"), _linear_units("proposed_water")

            def _km(r, key):
                return float(r.get(key) or 0.0)

            def _enc(r, proposed=False):
                if _BNGM is None:
                    return 1.0
                return _BNGM.water_row_encroachment_mult(r, proposed=proposed)

            bl = 0.0
            for r in bl_rows:
                u = (_km(r, "length_km") * _d(r.get("sub_habitat_type", ""))
                     * _c(r.get("condition", "")) * _s(r.get("strategic_significance", ""))
                     * _enc(r))
                bl += u
                if agg_bl is not None:
                    agg_bl[r.get("sub_habitat_type", "Unknown")] += u
            pr = 0.0
            for r in bl_rows:
                ret = _km(r, "length_retained_km")
                u = (ret * _d(r.get("sub_habitat_type", ""))
                     * _c(r.get("condition", "")) * _s(r.get("strategic_significance", ""))
                     * _enc(r))
                pr += u
                if agg_pr is not None:
                    agg_pr[r.get("sub_habitat_type", "Unknown")] += u
            for r in en_rows:
                sub   = r.get("prop_sub_habitat_type") or r.get("sub_habitat_type", "")
                cond  = r.get("prop_condition") or r.get("condition", "")
                start = r.get("condition", "")
                strat = r.get("strategic_significance", "")
                length = _km(r, "length_enhanced_km") or _km(r, "length_km")
                base = length * _d(sub) * _c(cond) * _s(strat)
                base_val = length * _d(r.get("sub_habitat_type", "")) * _c(start) * _s(strat)
                if _BNGM is not None:
                    m, _n = _BNGM.proposed_multiplier(
                        "enhanced", sub, cond, start_condition=start,
                        baseline_habitat=r.get("sub_habitat_type", ""))
                    base = _BNGM.blend_enhanced_units(base_val, base, m)
                u = base * _enc(r, proposed=True)
                pr += u
                if agg_pr is not None:
                    agg_pr[sub or "Unknown"] += u
            for r in cr_rows:
                sub = r.get("sub_habitat_type", ""); cond = r.get("condition", "")
                base = _km(r, "length_km") * _d(sub) * _c(cond) \
                    * _s(r.get("strategic_significance", "")) * _enc(r)
                if _BNGM is not None:
                    m, _n = _BNGM.proposed_multiplier("created", sub, cond)
                    base *= m
                pr += base
                if agg_pr is not None:
                    agg_pr[sub or "Unknown"] += base
            return bl, pr

        # The aggregators run over the whole map so the "what changed" movers
        # still cover off-site reaches; the unit totals then take the same
        # on-site / off-site split as the area module above, because habitat
        # inside a blue line is scored on E-1/E-2 (hedgerow) or F-1/F-2
        # (watercourse) rather than by the on-site rules.
        _linear_scope = False if (_split_off and not _include_offsite) else None
        bl_hedge, pr_hedge = _hedge_units_via_engine(
            lin_bl, lin_pr, offsite=_linear_scope)
        bl_water, pr_water = _water_units_via_engine(
            lin_bl, lin_pr, offsite=_linear_scope)
        bl_hedge_onsite, bl_water_onsite = bl_hedge, bl_water
        if _split_off and _include_offsite:
            try:
                from .offsite_from_map import offsite_hedge_units
                _off_h_bl, _off_h_pr = offsite_hedge_units()
                bl_hedge, pr_hedge = _hedge_units_via_engine(offsite=False)
                bl_hedge_onsite = bl_hedge
                bl_hedge += _off_h_bl
                pr_hedge += _off_h_pr
            except Exception:
                bl_hedge_onsite = bl_hedge
            try:
                from .offsite_from_map import offsite_water_units
                _off_w_bl, _off_w_pr = offsite_water_units(self.iface)
                bl_water, pr_water = _water_units_via_engine(offsite=False)
                bl_water_onsite = bl_water
                bl_water += _off_w_bl
                pr_water += _off_w_pr
            except Exception:
                bl_water_onsite = bl_water
        # Cache hedgerow & watercourse figures for the off-site Enhancements
        # modules (each module trades only against its own baseline).
        self._enh_hedge_baseline = bl_hedge
        self._enh_hedge_proposed = pr_hedge
        self._enh_water_baseline = bl_water
        self._enh_water_proposed = pr_water
        self._enh_hedge_baseline_onsite = bl_hedge_onsite
        self._enh_water_baseline_onsite = bl_water_onsite

        # ── BNG % per module ──────────────────────────────────────────
        def _pct(bl, pr, denom=None):
            if bl == 0:
                return None
            # `denom` lets the caller divide by the on-site baseline while the
            # numerator stays the combined change, which is how the metric
            # reports it. Falls back to bl when no denominator is supplied.
            d0 = denom if (denom or 0.0) > 1e-9 else bl
            return round(((pr - bl) / d0) * 100, 2)

        area_pct  = _pct(bl_area,  pr_area,  bl_area_onsite)
        hedge_pct = _pct(bl_hedge, pr_hedge, bl_hedge_onsite)
        water_pct = _pct(bl_water, pr_water, bl_water_onsite)


        # ── Per-habitat unit movers (area + trees + linear modules) ────
        # Aggregate units by sub_habitat_type across baseline and proposed
        # rows; the dashboard's "What changed" section shows the biggest nets.
        hab_bl = defaultdict(float)   # sub_type -> baseline units
        hab_pr = defaultdict(float)   # sub_type -> proposed units

        for r in _sp_bl_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            sub = r.get("sub_habitat_type", "Unknown")
            hab_bl[sub] += _units_from_row(r, "area_ha")
            # Retained portion in proposed
            ret_ha = float(r.get("area_retained_ha") or 0.0)
            hab_pr[sub] += _units_from_row({**r, "area_ha": ret_ha})

        for r in _sp_en_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            sub = r.get("prop_sub_habitat_type") or r.get("sub_habitat_type", "Unknown")
            hab_pr[sub] += _proposed_units_from_row(r, "enhanced")

        for r in _sp_cr_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            sub = r.get("sub_habitat_type", "Unknown")
            hab_pr[sub] += _proposed_units_from_row(r, "created")

        # ── Individual trees into the movers ───────────────────────────
        # Trees are stored as points; use TREE_SIZE_AREAS for the metric's
        # area-equivalent.  Baseline trees count at full (face) value.  Proposed
        # trees use the SAME per-feature Retained/Enhanced/Created status that
        # the Manage screen shows (pair_id linking): retained trees keep full
        # value, while enhanced and newly-created trees take the metric's
        # temporal × difficulty discount.
        try:
            from . import bng_multipliers as _TBNGM
        except Exception:
            _TBNGM = None
        from .layer_manager import tree_location_from_sub_type as _tree_loc

        def _tree_size_key(sub):
            s = (sub or "").lower()
            if "large" in s:
                return "Large"
            if "medium" in s:
                return "Medium"
            return "Small"

        def _tree_display_key(sub, cond):
            return f"{sub} · {cond}" if cond else sub

        # Proposed pair_id -> baseline condition, used as the start condition
        # for enhancement temporal lookups.
        _bl_tree_cond_by_pid = {}
        _bl_tree_layer = self.layer_manager.get_layer("baseline_trees")
        _tree_masks_cache = {}

        def _tree_in_dashboard_scope(layer, feat):
            if _include_offsite or not _split_off:
                return True
            try:
                from .offsite_from_map import boundary_masks, scoped_geometry
                masks = _tree_masks_cache.get(layer.id())
                if masks is None:
                    masks = boundary_masks(layer)
                    _tree_masks_cache[layer.id()] = masks
                geom = scoped_geometry(feat, layer, False, masks)
                return (geom is not None and not geom.isNull()
                        and not geom.isEmpty())
            except Exception:
                return False

        if _bl_tree_layer and _bl_tree_layer.isValid():
            for bf in _bl_tree_layer.getFeatures():
                if not _tree_in_dashboard_scope(_bl_tree_layer, bf):
                    continue
                _pid = _field(bf, "pair_id")
                if _pid and not _pid.startswith("solo:"):
                    _bl_tree_cond_by_pid[_pid] = _field(bf, "condition")

        # Baseline trees — always full value.
        if _bl_tree_layer and _bl_tree_layer.isValid():
            for feat in _bl_tree_layer.getFeatures():
                if not _tree_in_dashboard_scope(_bl_tree_layer, feat):
                    continue
                sub = _field(feat, "sub_habitat_type")
                if not sub:
                    continue
                cond  = _field(feat, "condition")
                strat = _field(feat, "strategic_significance")
                ha = TREE_SIZE_AREAS.get(_tree_size_key(sub), TREE_SIZE_AREAS["Small"])
                hab_bl[_tree_display_key(sub, cond)] += ha * 4 * _c(cond) * _s(strat)

        # Proposed trees — discount enhanced/created per their Manage status.
        _pr_tree_layer = self.layer_manager.get_layer("proposed_trees")
        if _pr_tree_layer and _pr_tree_layer.isValid():
            for feat in _pr_tree_layer.getFeatures():
                if not _tree_in_dashboard_scope(_pr_tree_layer, feat):
                    continue
                sub = _field(feat, "sub_habitat_type")
                if not sub:
                    continue
                cond  = _field(feat, "condition")
                strat = _field(feat, "strategic_significance")
                ha = TREE_SIZE_AREAS.get(_tree_size_key(sub), TREE_SIZE_AREAS["Small"])
                base = ha * 4 * _c(cond) * _s(strat)
                status, _tip = self._tree_hedge_status(_pr_tree_layer, feat)
                add_u = base                              # retained: full value
                if _TBNGM is not None and status in ("Created", "Enhanced"):
                    loc = _tree_loc(sub)
                    if status == "Created":
                        mult, _ = _TBNGM.proposed_multiplier("created", loc, cond)
                        add_u = base * mult               # whole value discounted
                    else:  # Enhanced — keep baseline value whole, discount uplift
                        start_cond = _bl_tree_cond_by_pid.get(_field(feat, "pair_id"), "")
                        mult, _ = _TBNGM.proposed_multiplier(
                            "enhanced", loc, cond, start_condition=start_cond)
                        base_val = ha * 4 * _c(start_cond) * _s(strat)
                        add_u = _TBNGM.blend_enhanced_units(base_val, base, mult)
                hab_pr[_tree_display_key(sub, cond)] += add_u

        # Linear modules join the same pool (aggregated by the engines above).
        for _k, _v in lin_bl.items():
            hab_bl[_k] += _v
        for _k, _v in lin_pr.items():
            hab_pr[_k] += _v

        movers = []
        for _sub in set(list(hab_bl.keys()) + list(hab_pr.keys())):
            _ch = round(hab_pr.get(_sub, 0.0) - hab_bl.get(_sub, 0.0), 4)
            if abs(_ch) > 5e-4:
                movers.append((_sub, _ch))
        movers.sort(key=lambda t: -abs(t[1]))

        # ── Area split totals (ha): retained / enhanced / created / lost ─
        # Straight from classify_area_net — the same source the metric export
        # writes to A-1 / A-2. Area module only (ha); trees & linear modules
        # score in units above.
        _tret = _tenh = _tlost = _tcre = 0.0
        for r in _sp_bl_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            _tret  += float(r.get("area_retained_ha") or 0.0)
            _tenh  += float(r.get("area_enhanced_ha") or 0.0)
            _tlost += float(r.get("area_lost_ha") or 0.0)
        for r in _sp_cr_rows:
            if r.get("broad_habitat") == "Individual trees":
                continue
            _tcre += float(r.get("area_ha") or 0.0)

        # ── Statutory credits by tier/module ───────────────────────────
        # Derived directly from the live unit totals computed above — same
        # maths as the statutory metric, so the figures always agree with the
        # rest of the dashboard. Credits = unit shortfall / temporal_multiplier.
        TARGET = 0.10
        TEMPORAL_MULT = 0.5   # standard Low/Medium distinctiveness creation pathway
        try:
            from . import credits_engine as _CE
            from .xlsm_exporter import (_collect_trees as _CTr,
                                        _tree_rows_as_area_rows as _TrR,
                                        _aggregate_rows_by_key as _AggR,
                                        _AGG_KEYS as _AggK,
                                        classify_linear_net as _ClnL)
            _abl2 = list(_sp_bl_rows); _acr2 = list(_sp_cr_rows); _aen2 = list(_sp_en_rows)
            _tbR, _tcR, _teR = _CTr(self.iface)
            _abl2 += _TrR(_tbR, baseline=True); _acr2 += _TrR(_tcR, baseline=False)
            _aen2 += _teR
            _abl2 = _AggR(_abl2, _AggK["area_baseline"])
            _acr2 = _AggR(_acr2, _AggK["area_creation"])
            _anets2 = _CE.net_by_habitat(_abl2, _acr2, _aen2)
            _abt2 = sum(s["baseline"] for s in _anets2.values())
            _apt2 = sum(s["post"] for s in _anets2.values())
            # Mapped off-site receptor land is also subject to the 10% target,
            # so credits use the same combined baseline/post totals as the
            # obligation slots above.
            _atiers2 = _CE.tier_credits(_anets2, _abt2, _apt2)
            _credits = {t: _atiers2[t]["credits"] for t in ("A1", "A2", "A3", "A4", "A5")}
            for _mk, _bk, _pk in (("H", "baseline_hedgerow", "proposed_hedgerow"),
                                  ("W", "baseline_water", "proposed_water")):
                try:
                    _hbR, _hcR, _heR = _ClnL(self.iface, _bk, _pk)
                    _hnR = _CE.net_by_habitat(_hbR, _hcR, _heR, area_key="length_km",
                                              water=("water" in _bk))
                    if _mk == "W":
                        # Watercourse trading is per distinctiveness band (same
                        # habitat for V.High/High/Medium; better-distinctiveness
                        # for Low) — gains in one band do NOT offset losses in a
                        # "same habitat" band, so a pooled deficit under-counts.
                        _credits["W"] = _CE.water_credit(_hnR)[0]
                    else:
                        _hbt = sum(s["baseline"] for s in _hnR.values())
                        _hpt = sum(s["post"] for s in _hnR.values())
                        _credits[_mk] = round(max(
                            0.0, _hbt * (1 + TARGET) - _hpt
                        ) * 2, 4)
                except Exception:
                    _credits[_mk] = 0.0
        except Exception:
            _credits = {
                "A1": max(0.0, round((bl_area  * (1 + TARGET) - pr_area)  / TEMPORAL_MULT, 4)),
                "A2": 0.0, "A3": 0.0, "A4": 0.0, "A5": 0.0,
                "H":  max(0.0, round((bl_hedge * (1 + TARGET) - pr_hedge) / TEMPORAL_MULT, 4)),
                "W":  max(0.0, round((bl_water * (1 + TARGET) - pr_water) / TEMPORAL_MULT, 4)),
            }

        # ── Red-line coverage (Data quality's "% mapped") ──────────────
        _coverage = None
        try:
            _rl_ha = 0.0
            for _l in project.mapLayers().values():
                from .layer_manager import is_redline_name as _is_redline_name
                if _is_redline_name(_l.name()) and _l.featureCount() > 0:
                    _ca = _area_calc(_l)
                    for _f in _l.getFeatures():
                        _g = _f.geometry()
                        if _g and not _g.isNull():
                            _rl_ha += _ca.convertAreaMeasurement(
                                abs(_ca.measureArea(_g)), QgsUnitTypes.AreaUnit.AreaHectares)
                    break
            if _rl_ha > 1e-6:
                # A baseline parcel is retained+enhanced+lost; a proposed one is
                # retained+enhanced+created.
                _coverage = (_rl_ha, _tret + _tenh + _tlost, _tret + _tenh + _tcre)
        except Exception:
            _coverage = None

        # ── Data quality scan (feature counts + incomplete attributes) ─
        _Q_LAYERS = [
            ("baseline_area",     "Baseline area"),
            ("proposed_area",     "Proposed area"),
            ("baseline_hedgerow", "Baseline hedgerow"),
            ("proposed_hedgerow", "Proposed hedgerow"),
            ("baseline_water",    "Baseline water"),
            ("proposed_water",    "Proposed water"),
            ("baseline_trees",    "Baseline trees"),
            ("proposed_trees",    "Proposed trees"),
        ]
        _Q_REQUIRED = ["sub_habitat_type", "condition", "strategic_significance"]
        q_rows, q_total, q_inc = [], 0, 0
        for _key, _label in _Q_LAYERS:
            _lyr = self.layer_manager.get_layer(_key)
            if _lyr is None or not _lyr.isValid():
                q_rows.append((_label, 0, 0))
                continue
            _count = _incomplete = 0
            for _f in _lyr.getFeatures():
                _count += 1
                for _fld in _Q_REQUIRED:
                    _idx = _f.fieldNameIndex(_fld)
                    if _idx >= 0 and not str(_f[_fld] or "").strip():
                        _incomplete += 1
                        break
            q_total += _count
            q_inc += _incomplete
            q_rows.append((_label, _count, _incomplete))
        try:
            _geom_bad = len(self._invalid_geometries())
        except Exception:
            _geom_bad = 0

        # ── Obligation slots (after the _enh_* caches are set above) ───
        try:
            _slot_by_mod = self.module_slot_view(dashboard_scope=True)
        except Exception:
            _slot_by_mod = {}

        # Credits must never be independently re-derived from the map after
        # the obligation panel has settled trading and off-site delivery. Price
        # the exact displayed shortfall at 2:1, retaining its statutory tier.
        try:
            from . import credits_engine as _SlotCE
            _area_slot = _slot_by_mod.get("area") or {}
            _credits.update(_SlotCE.tier_credits_from_slots(_area_slot))
            for _credit_key, _module_key in (("H", "hedgerow"),
                                              ("W", "watercourse")):
                _view = _slot_by_mod.get(_module_key) or {}
                _credits[_credit_key] = round(
                    max(0.0, float(_view.get("total_short") or 0.0)) * 2.0, 4)
        except Exception:
            pass

        def _mod_payload(bl, pr, pct, key, bl_on=None):
            return {"active": (bl or 0.0) > 1e-9 or (pr or 0.0) > 1e-9,
                    "bl": bl, "pr": pr, "pct": pct,
                    # The on-site baseline the percentage divides by. The tile
                    # shows this rather than the combined figure, so the numbers
                    # on the tile actually produce the percentage above them.
                    "bl_on": bl_on if bl_on is not None else bl,
                    "slots": _slot_by_mod.get(key)}

        return {
            "has_offsite": _split_off,
            "include_offsite": _include_offsite,
            "modules": {
                "area":        _mod_payload(bl_area,  pr_area,  area_pct,  "area",
                                            bl_area_onsite),
                "hedgerow":    _mod_payload(bl_hedge, pr_hedge, hedge_pct, "hedgerow",
                                            bl_hedge_onsite),
                "watercourse": _mod_payload(bl_water, pr_water, water_pct, "watercourse",
                                            bl_water_onsite),
            },
            "movers": movers,
            "split": (_tret, _tenh, _tcre, _tlost),
            "credits": _credits,
            "coverage": _coverage,
            "quality": {"total": q_total, "incomplete": q_inc,
                        "geom_bad": _geom_bad, "rows": q_rows},
        }
