# building_tree_tab.py — BuildingTreeMixin, extracted verbatim from main_panel.py (2026-07-22 split).
# Methods run on BNGMainPanel via mixin inheritance; `self` is the panel.


from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QScrollArea, QDialog, QMessageBox)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QCursor
from qgis.core import Qgis
from . import theme


from .panel_widgets import (_svg_pixmap, _svg_icon, _dot_pixmap)
from .panel_map_tools import _TreePlaceTool

class BuildingTreeMixin:
    def _open_building_tree_mapper(self):
        """Build and navigate to the Site Plan Visualiser screen.
        Always returns to home on Back."""
        screen = self._build_building_tree_screen()
        # Remembered so go_back can recognise this screen and stop any armed
        # building/tree placement tool before navigating away.
        self._bt_screen_widget = screen
        while self.stack.count() > 3:
            w = self.stack.widget(3)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.addWidget(screen)
        self.stack.setCurrentIndex(0)
        self._screen_history.clear()
        self._navigate_to(self.stack.count() - 1, "Site Plan Visualiser")

    # Building & Tree Mapper palette — building brown, tree green, theme greens.
    _BT_BROWN = "#5d4037"
    # Enhancement-availability accent — terracotta, matches the curtilage hatch.
    _BT_TERRA = "#B4552D"

    def _build_building_tree_screen(self):
        from .habitat_form import ShapeControls
        from qgis.PyQt.QtCore import QSize

        BROWN = self._BT_BROWN
        TERRA = self._BT_TERRA      # curtilage / availability terracotta
        GREEN = theme.GOOD          # tree green  #1F7A4D
        DARK  = theme.ACCENT_DARK   # maps / structural green

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:%s; }" % theme.SURFACE)

        container = QWidget()
        container.setStyleSheet("background:%s;" % theme.SURFACE)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(9)

        # ── local builders (kept in-method, mirroring the home screen) ──────
        def _card(title, dot_color):
            # Match Manage's section treatment: one softly tinted group owns
            # its rows, instead of a grey title strip above a white bordered box.
            palettes = {
                BROWN: ("#F7F2F0", "#E6D8D2"),
                GREEN: ("#EEF7F2", "#CEE3D6"),
                TERRA: ("#FFF3ED", "#EED7CB"),
                DARK:  ("#F0F5F2", "#D3E0D8"),
            }
            tint, edge = palettes.get(dot_color, ("#F3F6F3", "#D8E0DA"))
            card = QWidget()
            card.setStyleSheet(
                "background:%s; border:1px solid %s; border-radius:10px;"
                % (tint, edge))
            cvl = QVBoxLayout(card); cvl.setContentsMargins(0, 0, 0, 0); cvl.setSpacing(0)
            hdr = QWidget()
            hdr.setStyleSheet(
                "background:transparent; border:none; border-top-left-radius:10px;"
                " border-top-right-radius:10px;")
            hl = QHBoxLayout(hdr); hl.setContentsMargins(11, 10, 11, 4); hl.setSpacing(7)
            dot = QLabel(); dot.setFixedSize(9, 9)
            dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            dot.setStyleSheet("background:transparent; border:none;")
            dot.setPixmap(_dot_pixmap(dot_color, 9))
            lbl = QLabel(title)
            lbl.setStyleSheet(
                "color:%s; font-size:10px; font-weight:600; letter-spacing:0.8px;"
                " background:transparent; border:none;"
                " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;" % dot_color)
            hl.addWidget(dot); hl.addWidget(lbl); hl.addStretch()
            sep = QFrame(); sep.setFixedHeight(1)
            sep.setStyleSheet("background:transparent; border:none;")
            body = QVBoxLayout(); body.setContentsMargins(10, 6, 10, 10); body.setSpacing(8)
            holder = QWidget(); holder.setStyleSheet("background:transparent; border:none;")
            holder.setLayout(body)
            cvl.addWidget(hdr); cvl.addWidget(sep); cvl.addWidget(holder)
            return card, body

        def _action_btn(label, icon_name, color, cb):
            """Checkable card button: centred SVG icon + label in `color`, which
            gains a quiet selected state while active."""
            btn = QPushButton(); btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); btn.setMinimumHeight(42)
            btn.setStyleSheet(
                "QPushButton { background:rgba(255,255,255,0.72); border:1px solid rgba(74,96,82,0.14); border-radius:9px; }"
                "QPushButton:hover:!checked { background:rgba(255,255,255,0.95); border-color:rgba(74,96,82,0.28); }"
                "QPushButton:checked { background:%s; border:2px solid %s; }"
                % (theme.ACCENT_SOFT, color))
            row = QHBoxLayout(btn); row.setContentsMargins(12, 6, 12, 6); row.setSpacing(9)
            ico = QLabel(); ico.setFixedSize(18, 18)
            ico.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            ico.setStyleSheet("background:transparent; border:none;")
            ico.setPixmap(_svg_pixmap(icon_name, color, 18))
            tcss = ("font-size:12px; font-weight:600; background:transparent; border:none;"
                    " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif; color:%s;")
            txt = QLabel(label); txt.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            txt.setStyleSheet(tcss % color)
            row.addStretch(); row.addWidget(ico); row.addWidget(txt); row.addStretch()
            btn._ico = ico; btn._txt = txt; btn._icon_name = icon_name
            btn._color = color; btn._tcss = tcss
            def _sync(checked, b=btn):
                col = b._color
                b._ico.setPixmap(_svg_pixmap(b._icon_name, col, 18))
                b._txt.setStyleSheet(b._tcss % col)
            btn.toggled.connect(_sync)
            btn.setText = (lambda s, b=btn: b._txt.setText(str(s)))
            btn.toggled.connect(cb)
            return btn

        def _seg_btn(label, icon_name, mode):
            b = QPushButton(label); b.setCheckable(True)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); b.setMinimumHeight(30)
            b.setStyleSheet(
                "QPushButton { background:transparent; border:none; border-radius:7px;"
                " color:%s; font-size:11px; font-weight:600; padding:4px 6px;"
                " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif; }"
                "QPushButton:checked { background:rgba(255,255,255,0.88); color:%s; }"
                % (theme.FAINT, BROWN))
            b.setIcon(_svg_icon(icon_name, theme.FAINT, 14, on_color=BROWN))
            b.setIconSize(QSize(14, 14))
            b.clicked.connect(lambda _=False, m=mode: self._bt_set_mode(m))
            return b

        def _mini_btn(label, icon_name, color, cb, danger=False):
            b = QPushButton(" " + label); b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setMinimumHeight(28)
            b.setIcon(_svg_icon(icon_name, color, 14)); b.setIconSize(QSize(14, 14))
            if danger:
                b.setStyleSheet(
                    "QPushButton { background:%s; color:%s; border:1px solid #F0D5D2;"
                    " border-radius:7px; padding:3px 10px; font-size:11px; font-weight:600;"
                    " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif; }"
                    "QPushButton:hover { background:#F6DAD7; }"
                    "QPushButton:disabled { color:%s; background:%s; border-color:%s; }"
                    % (theme.BAD_BG, theme.BAD, theme.FAINT, theme.SUNKEN, theme.BORDER))
            else:
                b.setStyleSheet(
                    "QPushButton { background:transparent; color:%s; border:none;"
                    " border-radius:7px; padding:3px 8px; font-size:11px; font-weight:500; }"
                    "QPushButton:hover { background:%s; color:%s; }"
                    "QPushButton:disabled { color:#C0C8C0; }"
                    % (theme.MUTED, theme.SUNKEN, theme.INK))
            b.clicked.connect(cb)
            return b

        def _footer(undo_cb, clear_cb):
            row = QHBoxLayout(); row.setContentsMargins(0, 2, 0, 0); row.setSpacing(8)
            chip = QLabel("0 placed")
            chip.setStyleSheet(
                "background:%s; color:%s; font-size:11px; font-weight:600;"
                " padding:2px 9px; border-radius:9px;"
                " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;"
                % (theme.ACCENT_SOFT, theme.ACCENT))
            undo  = _mini_btn("Undo last", "arrow-back-up", theme.MUTED, undo_cb)
            clear = _mini_btn("Clear", "trash", theme.BAD, clear_cb, danger=True)
            row.addWidget(chip); row.addStretch(); row.addWidget(undo); row.addWidget(clear)
            return row, chip, undo, clear

        def _map_row(title, subtitle, cb):
            btn = QPushButton(); btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setMinimumHeight(44)
            btn.setStyleSheet(
                "QPushButton { background:transparent; border:none; border-radius:8px; }"
                "QPushButton:hover { background:%s; }" % theme.ACCENT_SOFT)
            r = QHBoxLayout(btn); r.setContentsMargins(8, 4, 8, 4); r.setSpacing(10)
            ico = QLabel(); ico.setFixedSize(20, 20)
            ico.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            ico.setStyleSheet("background:transparent; border:none;")
            ico.setPixmap(_svg_pixmap("map", DARK, 18))
            tw = QWidget(); tw.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            tw.setStyleSheet("background:transparent; border:none;")
            tvl = QVBoxLayout(tw); tvl.setContentsMargins(0, 0, 0, 0); tvl.setSpacing(0)
            t1 = QLabel(title); t1.setStyleSheet(
                "color:%s; font-size:12px; font-weight:600; background:transparent;"
                " border:none; font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;" % theme.INK)
            t2 = QLabel(subtitle); t2.setStyleSheet(
                "color:%s; font-size:10px; background:transparent; border:none;" % theme.FAINT)
            tvl.addWidget(t1); tvl.addWidget(t2)
            chev = QLabel(); chev.setFixedSize(16, 16)
            chev.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            chev.setStyleSheet("background:transparent; border:none;")
            chev.setPixmap(_svg_pixmap("chevron-right", "#B7BFB9", 16))
            r.addWidget(ico); r.addWidget(tw, 1); r.addWidget(chev)
            btn.clicked.connect(cb)
            return btn

        # ── illustration-only note (themed amber) ──────────────────────────
        note = QLabel(
            "Buildings, trees and availability areas placed here are for map "
            "illustration only. They are NOT included in any BNG calculation, "
            "balance check or summary."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "background:%s; border:1px solid #F0DFB0; border-radius:8px;"
            " color:#7a5400; font-size:10px; padding:8px 10px;" % theme.WARN_BG)
        layout.addWidget(note)

        # ── active-tool banner (hidden until a placement tool is running) ──
        banner = QWidget()
        banner.setStyleSheet(
            "background:%s; border:1px solid #BBD9C6; border-radius:9px;" % theme.ACCENT_SOFT)
        bl = QHBoxLayout(banner); bl.setContentsMargins(10, 8, 8, 8); bl.setSpacing(9)
        bdot = QLabel(); bdot.setFixedSize(9, 9)
        bdot.setStyleSheet("background:transparent; border:none;")
        bdot.setPixmap(_dot_pixmap(theme.ACCENT, 9))
        self._bt_banner_lbl = QLabel("")
        self._bt_banner_lbl.setWordWrap(True)
        self._bt_banner_lbl.setStyleSheet(
            "color:%s; font-size:10.5px; background:transparent; border:none;" % theme.ACCENT_DARK)
        fin = QPushButton("Finish"); fin.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        fin.setMinimumHeight(28)
        fin.setStyleSheet(
            "QPushButton { background:%s; color:white; border:none; border-radius:7px;"
            " font-size:11px; font-weight:600; padding:4px 12px; }"
            "QPushButton:hover { background:%s; }" % (theme.ACCENT_DARK, theme.HEADER))
        fin.clicked.connect(self._bt_finish)
        bl.addWidget(bdot); bl.addWidget(self._bt_banner_lbl, 1); bl.addWidget(fin)
        banner.setVisible(False)
        self._bt_banner = banner
        layout.addWidget(banner)

        # ── Buildings card ─────────────────────────────────────────────────
        b_card, b_body = _card("BUILDINGS · B1, B2, B3 …", BROWN)
        seg = QWidget()
        seg.setStyleSheet(
            "background:rgba(255,255,255,0.45); border:none; border-radius:9px;")
        sl = QHBoxLayout(seg); sl.setContentsMargins(3, 3, 3, 3); sl.setSpacing(3)
        self._bt_seg_free  = _seg_btn("Freehand", "polygon", "freehand")
        self._bt_seg_shape = _seg_btn("Fixed shape", "circle-dashed", "shape")
        sl.addWidget(self._bt_seg_free); sl.addWidget(self._bt_seg_shape)
        b_body.addWidget(seg)

        self._bt_shape_controls = ShapeControls()
        shape_wrap = QWidget(); shape_wrap.setStyleSheet("background:transparent; border:none;")
        swl = QVBoxLayout(shape_wrap); swl.setContentsMargins(0, 0, 0, 0); swl.setSpacing(5)
        sc_lbl = QLabel("Fixed-shape footprint")
        sc_lbl.setStyleSheet("color:%s; font-size:11px; background:transparent; border:none;" % theme.MUTED)
        swl.addWidget(sc_lbl); swl.addWidget(self._bt_shape_controls)
        self._bt_shape_wrap = shape_wrap
        b_body.addWidget(shape_wrap)

        self._bt_build_btn = _action_btn("Draw building", "polygon", BROWN, self._bt_building_action)
        b_body.addWidget(self._bt_build_btn)
        b_row, self._bt_b_count, self._bt_undo_b_btn, self._bt_clear_b_btn = _footer(
            self._bt_undo_building, self._bt_clear_buildings)
        b_body.addLayout(b_row)
        layout.addWidget(b_card)

        # ── Trees card ─────────────────────────────────────────────────────
        t_card, t_body = _card("TREES · T1, T2, T3 …", GREEN)
        self._bt_tree_btn = _action_btn("Place trees", "tree", GREEN, self._bt_place_trees)
        t_body.addWidget(self._bt_tree_btn)
        t_hint = QLabel("Click to drop trees one after another; right-click when finished.")
        t_hint.setWordWrap(True)
        t_hint.setStyleSheet("color:%s; font-size:10px; background:transparent; border:none;" % theme.FAINT)
        t_body.addWidget(t_hint)

        imp_btn = QPushButton("  Import trees from CSV…")
        imp_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); imp_btn.setMinimumHeight(32)
        imp_btn.setIcon(_svg_icon("table", GREEN, 15)); imp_btn.setIconSize(QSize(15, 15))
        imp_btn.setStyleSheet(
            "QPushButton { background:transparent; color:%s; border:none; border-radius:8px;"
            " font-size:11.5px; font-weight:500; padding:6px 10px;"
            " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif; }"
            "QPushButton:hover { background:rgba(255,255,255,0.72); }"
            % GREEN)
        imp_btn.setToolTip("Place trees from a spreadsheet of coordinates — "
                           "eastings/northings, lat/lon or grid references, auto-detected")
        imp_btn.clicked.connect(self._bt_import_trees_csv)
        t_body.addWidget(imp_btn)

        manage_btn = QPushButton("  Manage trees…")
        manage_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        manage_btn.setMinimumHeight(32)
        manage_btn.setIcon(_svg_icon("settings", GREEN, 15))
        manage_btn.setIconSize(QSize(15, 15))
        manage_btn.setStyleSheet(
            "QPushButton { background:transparent; color:%s; border:none; border-radius:8px;"
            " font-size:11.5px; font-weight:500; padding:6px 10px;"
            " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif; }"
            "QPushButton:hover { background:rgba(255,255,255,0.72); }"
            % GREEN)
        manage_btn.setToolTip(
            "Mark mapped trees as retained or removed and set individual buffer distances")
        manage_btn.clicked.connect(self._bt_manage_trees)
        self._bt_manage_trees_btn = manage_btn
        t_body.addWidget(manage_btn)

        def _zone_btn(label, zone_kind, fill_colour, edge_colour):
            button = QPushButton()
            button.setCheckable(True)
            button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            button.setMinimumHeight(34)
            button._zone_label = label
            button._zone_fill = fill_colour
            button._zone_edge = edge_colour
            button.setToolTip(
                "Click to show this crosshatched overlay; click again to hide it")
            button.toggled.connect(
                lambda checked, kind=zone_kind, btn=button:
                self._bt_zone_button_changed(kind, checked, btn))
            self._bt_paint_zone_button(button, False)
            return button

        self._bt_cez_btn = _zone_btn(
            "Construction Exclusion Zones", "cez", "#FFD600", "#202020")
        self._bt_raz_btn = _zone_btn(
            "Restricted Activity Zones", "raz", "#FF5A1F", "#8B1A0E")

        def _crop_btn(zone_kind, fill_colour, edge_colour):
            button = QPushButton()
            button.setCheckable(True)
            button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            button.setMinimumHeight(34)
            button._zone_fill = fill_colour
            button._zone_edge = edge_colour
            button.toggled.connect(
                lambda checked, kind=zone_kind, btn=button:
                self._bt_crop_button_changed(kind, checked, btn))
            self._bt_paint_crop_button(button, False)
            return button

        self._bt_cez_crop_btn = _crop_btn("cez", "#FFD600", "#202020")
        self._bt_raz_crop_btn = _crop_btn("raz", "#FF5A1F", "#8B1A0E")
        for attr_name, add_button, crop_button in (
                ("_bt_cez_controls", self._bt_cez_btn, self._bt_cez_crop_btn),
                ("_bt_raz_controls", self._bt_raz_btn, self._bt_raz_crop_btn)):
            controls = QWidget()
            controls.setStyleSheet("background:transparent; border:none;")
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(add_button, 1)
            row.addWidget(crop_button)
            controls.setLayout(row)
            setattr(self, attr_name, controls)
            t_body.addWidget(controls)

        # Reflect saved layer visibility/cropping when reopening the screen.
        from qgis.core import QgsProject
        from .building_tree_mapper import (
            TREE_CEZ_LAYER_NAME, TREE_RAZ_LAYER_NAME,
        )
        project = QgsProject.instance()
        for layer_name, add_button, crop_button in (
                (TREE_CEZ_LAYER_NAME, self._bt_cez_btn, self._bt_cez_crop_btn),
                (TREE_RAZ_LAYER_NAME, self._bt_raz_btn, self._bt_raz_crop_btn)):
            layers = project.mapLayersByName(layer_name)
            if not layers:
                continue
            layer = layers[0]
            node = project.layerTreeRoot().findLayer(layer.id())
            for button in (add_button, crop_button):
                button.blockSignals(True)
            add_button.setChecked(node is not None and node.isVisible())
            try:
                crop_button.setChecked(
                    int(layer.customProperty("biome_crop_to_redline", 0)) == 1)
            except (TypeError, ValueError):
                crop_button.setChecked(False)
            for button in (add_button, crop_button):
                button.blockSignals(False)
            self._bt_paint_zone_button(add_button, add_button.isChecked())
            self._bt_paint_crop_button(crop_button, crop_button.isChecked())
            if node is not None:
                self._bt_bind_zone_node(node, add_button)

        t_row, self._bt_t_count, self._bt_undo_t_btn, self._bt_clear_t_btn = _footer(
            self._bt_undo_tree, self._bt_clear_trees)
        t_body.addLayout(t_row)
        layout.addWidget(t_card)

        # ── Enhancement availability card ──────────────────────────────────
        a_card, a_body = _card("ENHANCEMENT AVAILABILITY", TERRA)
        self._bt_curt_btn = _action_btn(
            "Draw curtilage", "polygon", TERRA, self._bt_curtilage_action)
        a_body.addWidget(self._bt_curt_btn)

        def _avail_btn(label, icon_name, cb, tip):
            b = QPushButton("  " + label)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setMinimumHeight(32)
            b.setIcon(_svg_icon(icon_name, TERRA, 15)); b.setIconSize(QSize(15, 15))
            b.setStyleSheet(
                "QPushButton { background:transparent; color:%s; border:none; border-radius:8px;"
                " font-size:11.5px; font-weight:500; padding:6px 10px;"
                " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif; }"
                "QPushButton:hover { background:rgba(255,255,255,0.72); }"
                % TERRA)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            return b

        a_body.addWidget(_avail_btn(
            "Detect from proposal", "map-search", self._bt_detect_availability,
            "Scan the proposed habitats: sealed and built land becomes "
            "developed — unavailable for enhancement. If the proposal has "
            "garden parcels you'll be asked whether they count as residential "
            "curtilage. Re-run any time; previous detections are replaced."))
        a_body.addWidget(_avail_btn(
            "Fill remainder as available", "leaf", self._bt_fill_available,
            "Re-detect the proposal's developed land (and gardens, if you say "
            "they're curtilage), then mark everything else inside the redline "
            "as available for enhancement. Re-run any time; previous results "
            "are replaced."))
        a_hint = QLabel("Fill re-detects the proposal first, so one click "
                        "gives the full picture. You'll be asked whether "
                        "garden areas count as curtilage.")
        a_hint.setWordWrap(True)
        a_hint.setStyleSheet("color:%s; font-size:10px; background:transparent; border:none;" % theme.FAINT)
        a_body.addWidget(a_hint)

        a_row, self._bt_a_count, self._bt_undo_a_btn, self._bt_clear_a_btn = _footer(
            self._bt_undo_curtilage, self._bt_clear_availability)
        self._bt_a_area = QLabel("")
        self._bt_a_area.setStyleSheet(
            "background:%s; color:%s; font-size:11px; font-weight:600;"
            " padding:2px 9px; border-radius:9px;"
            " font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;"
            % (theme.ACCENT_SOFT, theme.ACCENT))
        self._bt_a_area.setVisible(False)
        a_row.insertWidget(1, self._bt_a_area)
        a_body.addLayout(a_row)
        layout.addWidget(a_card)

        # ── Maps card ──────────────────────────────────────────────────────
        m_card, m_body = _card("MAPS", DARK)
        m_body.setSpacing(2)
        m_body.addWidget(_map_row("Building & tree map", "buildings and trees together",
                                  lambda: self._create_bt_map("Building & Tree Map")))
        m_body.addWidget(_map_row("Building map", "each building colour in the key",
                                  lambda: self._create_bt_map("Building Map")))
        m_body.addWidget(_map_row("Tree map", "trees labelled T1, T2 …",
                                  lambda: self._create_bt_map("Tree Map")))
        m_body.addWidget(_map_row("Enhancement availability map",
                                  "curtilage & developed vs available",
                                  lambda: self._create_bt_map("Enhancement Availability Map")))
        layout.addWidget(m_card)

        layout.addStretch()
        container.setLayout(layout)
        scroll.setWidget(container)

        # initial state: freehand mode, live counts populated
        self._bt_mode = "freehand"
        self._bt_seg_free.setChecked(True)
        self._bt_shape_wrap.setVisible(False)
        self._bt_update_counts()
        return scroll

    @staticmethod
    def _bt_paint_zone_button(button, checked):
        """Make overlay visibility and map palette unmistakable."""
        fill = button._zone_fill
        edge = button._zone_edge
        button.setText(("Hide " if checked else "Show ") + button._zone_label)
        background = fill if checked else "rgba(255,255,255,0.52)"
        button.setStyleSheet(
            "QPushButton { background:%s; color:%s; border:2px solid %s;"
            " border-radius:8px; font-size:11px; font-weight:700;"
            " padding:6px 10px; text-align:left; }"
            "QPushButton:hover { border-width:3px; }"
            % (background, edge, edge))

    @staticmethod
    def _bt_paint_crop_button(button, checked):
        fill = button._zone_fill
        edge = button._zone_edge
        button.setText("Crop: ON" if checked else "Crop: OFF")
        background = fill if checked else "rgba(255,255,255,0.52)"
        button.setStyleSheet(
            "QPushButton { background:%s; color:%s; border:2px solid %s;"
            " border-radius:8px; font-size:10px; font-weight:700;"
            " padding:5px 8px; }"
            % (background, edge, edge))

    def _bt_zone_button_changed(self, kind, checked, button):
        self._bt_paint_zone_button(button, checked)
        self._bt_toggle_tree_zone(kind, checked, button)
        # A failed show request may reset the checked state with signals blocked.
        self._bt_paint_zone_button(button, button.isChecked())

    def _bt_crop_button_changed(self, kind, checked, button):
        self._bt_paint_crop_button(button, checked)
        self._bt_toggle_zone_crop(kind, checked, button)
        self._bt_paint_crop_button(button, button.isChecked())

    def _bt_bind_zone_node(self, node, button):
        """Keep this button in sync with the separate Layers-panel eye."""
        if getattr(button, "_zone_node_id", None) == node.layerId():
            return
        button._zone_node_id = node.layerId()

        def _sync(*_args):
            checked = node.itemVisibilityChecked()
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)
            self._bt_paint_zone_button(button, checked)

        node.visibilityChanged.connect(_sync)

    def _bt_toggle_tree_zone(self, kind, checked, button):
        """Show or hide a derived CEZ/RAZ crosshatched map layer."""
        from qgis.core import QgsProject
        from .building_tree_mapper import (
            TREE_BUFFER_LAYER_NAME, TREE_CEZ_LAYER_NAME, TREE_RAZ_LAYER_NAME,
            TREE_RPA_LAYER_NAME,
            rebuild_construction_exclusion_zones,
            rebuild_restricted_activity_zones, rebuild_tree_buffers,
            rebuild_tree_rpas,
        )

        if kind == "cez":
            layer_name = TREE_CEZ_LAYER_NAME
            rebuild = rebuild_construction_exclusion_zones
            empty_message = (
                "Enter a positive general buffer for at least one retained tree "
                "in Manage Trees first.")
        else:
            layer_name = TREE_RAZ_LAYER_NAME
            rebuild = rebuild_restricted_activity_zones
            empty_message = (
                "Enter an RPA radius larger than the Construction Exclusion Zone "
                "for at least one retained tree in Manage Trees first.")

        project = QgsProject.instance()
        layers = project.mapLayersByName(layer_name)
        layer = layers[0] if layers else None
        if checked:
            # The CEZ/RAZ are additional overlays. Ensure the original buffer
            # and RPA layers exist, but never use or replace them as the zone.
            if not project.mapLayersByName(TREE_BUFFER_LAYER_NAME):
                rebuild_tree_buffers()
            if not project.mapLayersByName(TREE_RPA_LAYER_NAME):
                rebuild_tree_rpas()
            layer = rebuild()
            if layer is None or layer.featureCount() == 0:
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
                QMessageBox.information(self, button.text(), empty_message)
                return
        if layer is not None:
            node = project.layerTreeRoot().findLayer(layer.id())
            if node is not None:
                node.setItemVisibilityChecked(checked)
                self._bt_bind_zone_node(node, button)
        self.iface.mapCanvas().refresh()

    def _bt_toggle_zone_crop(self, kind, checked, button):
        """Persist and apply red-line clipping for one derived tree overlay."""
        from qgis.core import QgsProject
        from .building_tree_mapper import (
            TREE_CEZ_LAYER_NAME, TREE_RAZ_LAYER_NAME,
            _redline_geometry_27700, rebuild_construction_exclusion_zones,
            rebuild_restricted_activity_zones,
        )

        if checked and _redline_geometry_27700() is None:
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
            QMessageBox.information(
                self, "Crop to red line",
                "Add a Red Line Boundary before enabling this option.")
            return

        layer_name = (
            TREE_CEZ_LAYER_NAME if kind == "cez" else TREE_RAZ_LAYER_NAME)
        rebuild = (rebuild_construction_exclusion_zones if kind == "cez"
                   else rebuild_restricted_activity_zones)
        project = QgsProject.instance()
        layers = project.mapLayersByName(layer_name)
        layer = layers[0] if layers else rebuild()
        if layer is None:
            return
        node = project.layerTreeRoot().findLayer(layer.id())
        was_visible = node.isVisible() if node is not None else False
        layer.setCustomProperty("biome_crop_to_redline", 1 if checked else 0)
        layer = rebuild()
        node = project.layerTreeRoot().findLayer(layer.id())
        if node is not None:
            node.setItemVisibilityChecked(was_visible)
        self.iface.mapCanvas().refresh()

    def _bt_untoggle_others(self, active):
        for btn in (getattr(self, "_bt_build_btn", None),
                    getattr(self, "_bt_tree_btn", None),
                    getattr(self, "_bt_curt_btn", None)):
            if btn is not None and btn is not active:
                btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
                try:
                    self._bt_reset_btn_visual(btn)
                except Exception:
                    pass

    def _bt_reset_btn_visual(self, btn):
        """Restore a checkable action button's idle look (icon + label tinted
        with its category colour) after it was unchecked with signals blocked —
        which otherwise skips the toggled recolour."""
        btn._ico.setPixmap(_svg_pixmap(btn._icon_name, btn._color, 18))
        btn._txt.setStyleSheet(btn._tcss % btn._color)

    def _bt_set_mode(self, mode):
        """Switch the Buildings card between freehand-polygon and fixed-shape
        placement, finishing any live tool first."""
        if any(b is not None and b.isChecked()
               for b in (getattr(self, "_bt_build_btn", None),
                         getattr(self, "_bt_tree_btn", None),
                         getattr(self, "_bt_curt_btn", None))):
            self._bt_finish()
        self._bt_mode = mode
        is_shape = (mode == "shape")
        for seg, on in ((self._bt_seg_free, not is_shape), (self._bt_seg_shape, is_shape)):
            seg.blockSignals(True); seg.setChecked(on); seg.blockSignals(False)
        self._bt_shape_wrap.setVisible(is_shape)
        self._bt_set_build_icon("building" if is_shape else "polygon")
        self._bt_build_btn._txt.setText("Place building" if is_shape else "Draw building")

    def _bt_set_build_icon(self, icon_name):
        b = getattr(self, "_bt_build_btn", None)
        if b is None:
            return
        b._icon_name = icon_name
        col = "white" if b.isChecked() else b._color
        b._ico.setPixmap(_svg_pixmap(icon_name, col, 18))

    def _bt_building_action(self, checked):
        """Toggled handler for the single Buildings action button — dispatches to
        the polygon or fixed-shape tool depending on the active mode."""
        if not checked:
            self._bt_finish()
            return
        if getattr(self, "_bt_mode", "freehand") == "shape":
            self._bt_start_shape()
        else:
            self._bt_start_polygon()

    def _bt_show_banner(self, text):
        b = getattr(self, "_bt_banner", None)
        if b is None:
            return
        try:
            self._bt_banner_lbl.setText(text)
            b.setVisible(True)
        except RuntimeError:
            pass

    def _bt_hide_banner(self):
        b = getattr(self, "_bt_banner", None)
        if b is None:
            return
        try:
            b.setVisible(False)
        except RuntimeError:
            pass

    def _bt_update_counts(self):
        """Refresh the 'N placed' chips and enable/disable Undo + Clear so a
        destructive action is only offered when there's something to remove."""
        from .building_tree_mapper import count_buildings, count_trees
        try:
            nb, nt = count_buildings(), count_trees()
        except Exception:
            return
        for n, chip_attr, undo_attr, clear_attr in (
            (nb, "_bt_b_count", "_bt_undo_b_btn", "_bt_clear_b_btn"),
            (nt, "_bt_t_count", "_bt_undo_t_btn", "_bt_clear_t_btn"),
        ):
            chip = getattr(self, chip_attr, None)
            if chip is None:
                continue
            try:
                chip.setText("%d placed" % n)
                getattr(self, undo_attr).setEnabled(n > 0)
                getattr(self, clear_attr).setEnabled(n > 0)
            except RuntimeError:
                pass
        self._bt_update_zone_control_visibility()
        # Availability card: drawn count drives Undo, ANY feature drives Clear,
        # and the ha chip only shows once a Fill has produced an available area.
        chip = getattr(self, "_bt_a_count", None)
        if chip is None:
            return
        from .curtilage_mapper import count_drawn, has_availability, available_area_ha
        try:
            nd = count_drawn()
            chip.setText("%d drawn" % nd)
            self._bt_undo_a_btn.setEnabled(nd > 0)
            self._bt_clear_a_btn.setEnabled(has_availability())
            ha = available_area_ha()
            self._bt_a_area.setText("%.2f ha available" % ha)
            self._bt_a_area.setVisible(ha > 0)
        except RuntimeError:
            pass
        except Exception:
            pass

    def _bt_update_zone_control_visibility(self):
        """Only offer CEZ/RAZ controls when their source radius exists."""
        from qgis.core import QgsProject
        from .building_tree_mapper import (
            TREE_CEZ_LAYER_NAME, TREE_LAYER_NAME, TREE_RAZ_LAYER_NAME,
            _ensure_tree_fields,
        )

        has_buffer = False
        has_rpa = False
        layers = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
        if layers:
            layer = layers[0]
            _ensure_tree_fields(layer)
            for feature in layer.getFeatures():
                if str(feature["status"] or "Retained") != "Retained":
                    continue
                try:
                    has_buffer = has_buffer or float(feature["buffer_m"] or 0) > 0
                except (TypeError, ValueError):
                    pass
                try:
                    has_rpa = has_rpa or float(feature["rpa_m"] or 0) > 0
                except (TypeError, ValueError):
                    pass
                if has_buffer and has_rpa:
                    break

        project = QgsProject.instance()
        for available, controls_attr, button_attr, layer_name in (
                (has_buffer, "_bt_cez_controls", "_bt_cez_btn",
                 TREE_CEZ_LAYER_NAME),
                (has_rpa, "_bt_raz_controls", "_bt_raz_btn",
                 TREE_RAZ_LAYER_NAME)):
            controls = getattr(self, controls_attr, None)
            button = getattr(self, button_attr, None)
            if controls is not None:
                controls.setVisible(available)
            if available:
                continue
            if button is not None:
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
                self._bt_paint_zone_button(button, False)
            for zone_layer in project.mapLayersByName(layer_name):
                node = project.layerTreeRoot().findLayer(zone_layer.id())
                if node is not None:
                    node.setItemVisibilityChecked(False)

    def _bt_finish(self):
        """Exit any active placement tool, restore the pan tool, reset the
        buttons + banner, and refresh the live counts. Re-entrancy-guarded:
        the pan switch below deactivates the tree tool, whose deactivate()
        calls back into _bt_finish (that path covers the user picking some
        OTHER tool directly)."""
        if getattr(self, "_bt_finishing", False):
            return
        self._bt_finishing = True
        try:
            self._bt_finish_inner()
        finally:
            self._bt_finishing = False

    def _bt_tree_ui_reset(self):
        """Reset ONLY the tree button + banner. Called from
        _TreePlaceTool.deactivate(), which fires on every switch away from the
        tree tool — including a cross-arm to the building tool mid-setMapTool,
        so this must never touch the canvas tool or the sibling button."""
        btn = getattr(self, "_bt_tree_btn", None)
        if btn is not None:
            try:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
                self._bt_reset_btn_visual(btn)
                btn._txt.setText("Place trees")
            except Exception:
                pass
        # Only drop the banner if the tree banner is the one showing — a
        # sibling tool arming right now may have just pushed its own.
        if not any(b is not None and b.isChecked()
                   for b in (getattr(self, "_bt_build_btn", None),
                             getattr(self, "_bt_curt_btn", None))):
            self._bt_hide_banner()

    def _bt_finish_inner(self):
        self.iface.actionPan().trigger()
        lyr = getattr(self, "_bt_draw_layer", None)
        if lyr is not None:
            try:
                lyr.featureAdded.disconnect(self._bt_on_draw_added)
            except Exception:
                pass
            try:
                # Never leave the layer editable+dirty: a failed commit rolls
                # back (a dirty layer removed later = QGIS 4.2 access violation).
                if lyr.isEditable() and not lyr.commitChanges():
                    lyr.rollBack()
            except Exception:
                pass
        self._bt_draw_layer = None
        self._bt_draw_cb = None
        mode = getattr(self, "_bt_mode", "freehand")
        for btn, label in (
            (getattr(self, "_bt_build_btn", None),
             "Place building" if mode == "shape" else "Draw building"),
            (getattr(self, "_bt_tree_btn", None), "Place trees"),
            (getattr(self, "_bt_curt_btn", None), "Draw curtilage"),
        ):
            if btn is None:
                continue
            try:
                btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
                self._bt_reset_btn_visual(btn)
                btn._txt.setText(label)
            except Exception:
                pass
        self._bt_hide_banner()
        self._bt_update_counts()
        self.iface.messageBar().clearWidgets()
        self.iface.mapCanvas().refresh()

    def _bt_start_draw_polygon(self, lyr, on_commit, banner_text):
        """Arm the native add-feature tool on *lyr* with the attribute form
        suppressed; when the user right-click-finishes a polygon, *on_commit*
        (lyr, edit-buffer fid) runs and the session ends via _bt_finish().
        Shared by the freehand building and curtilage draw paths."""
        from qgis.core import QgsEditFormConfig
        self._bt_draw_layer = lyr
        self._bt_draw_cb = on_commit
        try:
            cfg = lyr.editFormConfig()
            cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOn)
            lyr.setEditFormConfig(cfg)
        except Exception:
            pass
        self.iface.setActiveLayer(lyr)
        if not lyr.isEditable():
            lyr.startEditing()
        try:
            lyr.featureAdded.disconnect(self._bt_on_draw_added)
        except Exception:
            pass
        lyr.featureAdded.connect(self._bt_on_draw_added)
        self.iface.actionAddFeature().trigger()
        self._bt_show_banner(banner_text)

    def _bt_on_draw_added(self, fid):
        lyr = getattr(self, "_bt_draw_layer", None)
        if lyr is None:
            return
        try:
            lyr.featureAdded.disconnect(self._bt_on_draw_added)
        except Exception:
            pass
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._bt_commit_draw(fid))

    def _bt_commit_draw(self, fid):
        import traceback
        lyr = getattr(self, "_bt_draw_layer", None)
        cb = getattr(self, "_bt_draw_cb", None)
        if lyr is None or cb is None or not lyr.isEditable():
            self._bt_finish()
            return
        try:
            cb(lyr, fid)
        except Exception:
            traceback.print_exc()
        self._bt_finish()

    def _bt_start_polygon(self):
        self._bt_untoggle_others(self._bt_build_btn)
        from .building_tree_mapper import get_or_create_building_layer
        self._bt_start_draw_polygon(
            get_or_create_building_layer(), self._bt_commit_building_cb,
            "Click to add the building's corners, then right-click to finish it.")
        self._bt_build_btn.setText("Finish building")

    def _bt_commit_building_cb(self, lyr, fid):
        from .building_tree_mapper import assign_next_building, rebuild_building_renderer
        bid, _colour = assign_next_building(lyr, fid)
        lyr.commitChanges()
        rebuild_building_renderer(lyr)
        # Register with global undo — the committed fid differs from the
        # edit-buffer fid, so find the feature by its unique building_id.
        new_fids = [f.id() for f in lyr.getFeatures()
                    if f["building_id"] == bid]
        if new_fids:
            self.push_undo(lyr, max(new_fids), None, None)
        self.iface.mapCanvas().refresh()
        self.iface.messageBar().pushMessage(
            "Site Plan Visualiser", f"{bid} placed", level=Qgis.MessageLevel.Info, duration=2
        )

    def _bt_start_shape(self):
        self._bt_untoggle_others(self._bt_build_btn)
        from .building_tree_mapper import get_or_create_building_layer, add_building
        from .habitat_form import _HabitatShapeTool
        from qgis.core import QgsGeometry
        get_or_create_building_layer()
        canvas = self.iface.mapCanvas()

        def on_finished(rings):
            # _HabitatShapeTool now emits rings already in EPSG:27700 (true
            # metres), which is exactly the building layer's CRS. Use them
            # directly — transforming again (the old canvas→BNG step) double-
            # transformed the coordinates and threw the building off-map.
            geom = QgsGeometry.fromPolygonXY(rings)
            bid, _colour = add_building(geom)
            lyr = get_or_create_building_layer()
            new_fids = [f.id() for f in lyr.getFeatures()
                        if f["building_id"] == bid]
            if new_fids:
                self.push_undo(lyr, max(new_fids), None, None)
            canvas.refresh()
            self._bt_update_counts()
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser", f"{bid} placed", level=Qgis.MessageLevel.Info, duration=2
            )

        self._bt_shape_tool = _HabitatShapeTool(
            canvas, self._bt_shape_controls.params,
            self._bt_shape_controls.bump_rotation,
            on_finished, self._bt_finish,
            bump_dimension=self._bt_shape_controls.bump_dimension,
        )
        canvas.setMapTool(self._bt_shape_tool)
        self._bt_build_btn.setText("Finish placing shapes")
        self._bt_show_banner(
            "Move to position · scroll to rotate · arrow keys resize "
            "(←→ width, ↑↓ height; Shift = fine) · left-click to place · "
            "right-click when done")

    def _bt_place_trees(self, checked):
        if not checked:
            self._bt_finish()
            return
        self._bt_untoggle_others(self._bt_tree_btn)
        from .building_tree_mapper import get_or_create_tree_layer
        get_or_create_tree_layer()
        self._bt_tree_tool = _TreePlaceTool(self.iface.mapCanvas(), self.iface, self)
        self.iface.mapCanvas().setMapTool(self._bt_tree_tool)
        self._bt_tree_btn.setText("Finish placing trees")
        self._bt_show_banner(
            "Click to place trees (T1, T2 …). Right-click or Esc to finish.")

    def _bt_manage_trees(self):
        """Manage retained/removed status and an optional buffer per tree."""
        from qgis.core import QgsProject
        from qgis.PyQt.QtCore import QSize
        from qgis.PyQt.QtGui import QKeySequence
        from qgis.PyQt.QtWidgets import (
            QAbstractItemView, QApplication, QDoubleSpinBox, QHeaderView,
            QTableWidget, QTableWidgetItem,
        )
        from .building_tree_mapper import (
            TREE_LAYER_NAME, _ensure_tree_fields, _id_sort_key,
            add_tree_records, get_or_create_tree_layer, update_tree_management,
        )

        self._bt_finish()
        layers = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
        lyr = layers[0] if layers else get_or_create_tree_layer()
        _ensure_tree_fields(lyr)
        features = sorted(
            list(lyr.getFeatures()),
            key=lambda feature: _id_sort_key(str(feature["tree_id"] or "")),
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Manage trees")
        dlg.setMinimumWidth(860)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(9)

        intro = QLabel(
            "Edit the table directly, or select several rows and use the controls "
            "below. Coordinates are British National Grid (EPSG:27700); buffer "
            "and RPA values are radii in metres.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:%s; font-size:11px;" % theme.MUTED)
        root.addWidget(intro)

        row_fids = []

        class _TreeSpreadsheet(QTableWidget):
            """QTableWidget with rectangular spreadsheet copy and paste."""

            def _copy_selection(self):
                indexes = self.selectedIndexes()
                if not indexes:
                    return
                rows = [index.row() for index in indexes]
                cols = [index.column() for index in indexes]
                top, bottom = min(rows), max(rows)
                left, right = min(cols), max(cols)
                selected = {(index.row(), index.column()) for index in indexes}
                lines = []
                for row in range(top, bottom + 1):
                    values = []
                    for col in range(left, right + 1):
                        item = self.item(row, col)
                        values.append(
                            item.text() if item is not None
                            and (row, col) in selected else "")
                    lines.append("\t".join(values))
                QApplication.clipboard().setText("\n".join(lines))

            def _paste_clipboard(self):
                text = QApplication.clipboard().text()
                if not text:
                    return
                indexes = self.selectedIndexes()
                all_selected = (
                    self.rowCount() > 0
                    and len(indexes) == self.rowCount() * self.columnCount()
                )
                if all_selected:
                    # Anchor a full-table round trip at (0, 0), retaining the
                    # alignment of every value copied to and edited in Excel.
                    start_row, start_col = 0, 0
                else:
                    start = self.currentIndex()
                    start_row = start.row() if start.isValid() else 0
                    start_col = start.column() if start.isValid() else 0
                rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                if rows and rows[-1] == "":
                    rows.pop()
                for row_offset, line in enumerate(rows):
                    target_row = start_row + row_offset
                    while target_row >= self.rowCount():
                        self.insertRow(self.rowCount())
                        row_fids.append(None)
                        defaults = ["", "Retained", "", "", "0", "0"]
                        for col, default in enumerate(defaults):
                            self.setItem(
                                self.rowCount() - 1, col,
                                QTableWidgetItem(default))
                    for col_offset, value in enumerate(line.split("\t")):
                        target_col = start_col + col_offset
                        if target_col >= self.columnCount():
                            break
                        item = self.item(target_row, target_col)
                        if item is not None:
                            item.setText(value.strip())
                callback = getattr(self, "_fit_rows_callback", None)
                if callback is not None:
                    callback()

            def keyPressEvent(self, event):
                if event.matches(QKeySequence.StandardKey.SelectAll):
                    self.selectAll()
                    event.accept()
                    return
                if event.matches(QKeySequence.StandardKey.Copy):
                    self._copy_selection()
                    event.accept()
                    return
                if event.matches(QKeySequence.StandardKey.Paste):
                    self._paste_clipboard()
                    event.accept()
                    return
                super().keyPressEvent(event)

        table = _TreeSpreadsheet(len(features), 6)
        table.setHorizontalHeaderLabels(
            ["Tree label", "Status", "Easting", "Northing",
             "Buffer radius (m)", "RPA radius (m)"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        for row, feat in enumerate(features):
            row_fids.append(feat.id())
            status = str(feat["status"] or "Retained")
            try:
                radius = float(feat["buffer_m"] or 0)
            except (TypeError, ValueError):
                radius = 0.0
            try:
                rpa_radius = float(feat["rpa_m"] or 0)
            except (TypeError, ValueError):
                rpa_radius = 0.0
            point = feat.geometry().asPoint()
            values = [
                str(feat["tree_label"] or feat["tree_id"] or ""), status,
                "%.3f" % point.x(), "%.3f" % point.y(),
                "%g" % radius, "%g" % rpa_radius,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (Qt.AlignmentFlag.AlignCenter if col else Qt.AlignmentFlag.AlignLeft))
                table.setItem(row, col, item)

        # Show the complete schedule when practical, capped at 30 visible rows.
        # Larger schedules retain a vertical scrollbar. The screen-height guard
        # keeps the controls reachable on smaller displays.
        visible_rows = min(max(len(features), 1), 30)
        row_height = table.verticalHeader().defaultSectionSize()
        header_height = table.horizontalHeader().sizeHint().height()
        desired_table_height = header_height + visible_rows * row_height + 4
        screen = QApplication.primaryScreen()
        available_height = (
            screen.availableGeometry().height() if screen is not None else 900)
        table_height = min(desired_table_height, max(180, available_height - 300))
        table.setFixedHeight(table_height)
        dlg.resize(920, min(table_height + 260, available_height - 40))
        def _fit_rows():
            visible = min(max(table.rowCount(), 1), 30)
            desired = header_height + visible * row_height + 4
            fitted = min(desired, max(180, available_height - 300))
            table.setFixedHeight(fitted)
            dlg.resize(920, min(fitted + 260, available_height - 40))
        table._fit_rows_callback = _fit_rows
        root.addWidget(table, 1)

        status_row = QHBoxLayout()
        status_row.setSpacing(7)
        retained_btn = QPushButton("Retained")
        retained_btn.setIcon(_svg_icon("tree", theme.GOOD, 14))
        retained_btn.setIconSize(QSize(14, 14))
        removed_btn = QPushButton("Removed")
        removed_btn.setIcon(_svg_icon("trash", theme.BAD, 14))
        removed_btn.setIconSize(QSize(14, 14))
        for button in (retained_btn, removed_btn):
            button.setMinimumHeight(30)
        status_row.addWidget(QLabel("Tree status"))
        status_row.addWidget(retained_btn)
        status_row.addWidget(removed_btn)
        status_row.addStretch()
        root.addLayout(status_row)

        def _area_row(label, default_radius, set_label, clear_label):
            row = QHBoxLayout()
            row.setSpacing(7)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 500.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setSuffix(" m")
            spin.setValue(default_radius)
            set_btn = QPushButton(set_label)
            clear_btn = QPushButton(clear_label)
            set_btn.setMinimumHeight(30)
            clear_btn.setMinimumHeight(30)
            row.addWidget(QLabel(label))
            row.addStretch()
            row.addWidget(spin)
            row.addWidget(set_btn)
            row.addWidget(clear_btn)
            root.addLayout(row)
            return spin, set_btn, clear_btn

        radius_spin, set_buffer_btn, clear_buffer_btn = _area_row(
            "General buffer", 15.0, "Set buffer", "Clear buffer")
        rpa_spin, set_rpa_btn, clear_rpa_btn = _area_row(
            "Root protection area", 12.0, "Set RPA", "Clear RPA")

        def _selected_rows():
            return sorted({idx.row() for idx in table.selectionModel().selectedRows()})

        def _set_status(status):
            for row in _selected_rows():
                table.item(row, 1).setText(status)

        def _set_buffer(radius):
            for row in _selected_rows():
                table.item(row, 4).setText("%g" % radius)

        def _sync_map_selection():
            lyr.selectByIds([row_fids[row] for row in _selected_rows()])
            self.iface.mapCanvas().refresh()

        table.itemSelectionChanged.connect(_sync_map_selection)
        retained_btn.clicked.connect(lambda: _set_status("Retained"))
        removed_btn.clicked.connect(lambda: _set_status("Removed"))
        set_buffer_btn.clicked.connect(lambda: _set_buffer(radius_spin.value()))
        clear_buffer_btn.clicked.connect(lambda: _set_buffer(0.0))
        set_rpa_btn.clicked.connect(
            lambda: [table.item(row, 5).setText("%g" % rpa_spin.value())
                     for row in _selected_rows()])
        clear_rpa_btn.clicked.connect(
            lambda: [table.item(row, 5).setText("0")
                     for row in _selected_rows()])

        footer = QHBoxLayout()
        select_all_btn = QPushButton("Select all")
        select_all_btn.clicked.connect(table.selectAll)
        apply_btn = QPushButton("Apply and close")
        apply_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        footer.addWidget(select_all_btn)
        footer.addStretch()
        footer.addWidget(cancel_btn)
        footer.addWidget(apply_btn)
        root.addLayout(footer)

        def _apply():
            changes = {}
            new_rows = []
            for row, fid in enumerate(row_fids):
                values = (
                    table.item(row, 0).text(),
                    table.item(row, 1).text(),
                    table.item(row, 2).text(),
                    table.item(row, 3).text(),
                    table.item(row, 4).text(),
                    table.item(row, 5).text(),
                )
                try:
                    float(values[2])
                    float(values[3])
                except (TypeError, ValueError):
                    QMessageBox.warning(
                        dlg, "Invalid tree coordinates",
                        "Enter valid eastings and northings for row %d." % (row + 1))
                    table.setCurrentCell(row, 2)
                    return
                if fid is None:
                    new_rows.append((row, values))
                else:
                    changes[fid] = values
            if new_rows:
                new_fids = add_tree_records([
                    (values[0], values[2], values[3])
                    for _row, values in new_rows
                ])
                for (_row, values), fid in zip(new_rows, new_fids):
                    changes[fid] = values
            update_tree_management(changes)
            self._bt_update_zone_control_visibility()
            lyr.removeSelection()
            self.iface.mapCanvas().refresh()
            dlg.accept()

        apply_btn.clicked.connect(_apply)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.finished.connect(lambda _result: lyr.removeSelection())
        dlg.exec()

    def _bt_undo_building(self):
        from .building_tree_mapper import remove_last_building, peek_last_building
        if getattr(self, "_bt_build_btn", None) and self._bt_build_btn.isChecked():
            self._bt_finish()      # commit/exit the draw session before removing
        pair = peek_last_building()   # sync the global stack with this removal
        bid = remove_last_building()
        if bid and pair:
            self.drop_undo_entries([pair])
        self._bt_draw_layer = None
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()
        if bid:
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser", f"{bid} removed", level=Qgis.MessageLevel.Info, duration=1)

    def _bt_undo_tree(self):
        from .building_tree_mapper import remove_last_tree, peek_last_tree
        pair = peek_last_tree()       # sync the global stack with this removal
        tid = remove_last_tree()
        if tid and pair:
            self.drop_undo_entries([pair])
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()
        if tid:
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser", f"{tid} removed", level=Qgis.MessageLevel.Info, duration=1)

    def _bt_clear_buildings(self):
        from .building_tree_mapper import count_buildings, clear_buildings
        if count_buildings() == 0:
            return
        reply = QMessageBox.question(
            self, "Clear buildings", "Remove all mapped buildings?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._bt_finish()
        self._bt_draw_layer = None
        # The whole layer goes — purge its undo entries / redo captures so
        # they don't linger as click-burning duds.
        try:
            from qgis.core import QgsProject
            from .building_tree_mapper import BUILDING_LAYER_NAME
            for lyr in QgsProject.instance().mapLayersByName(BUILDING_LAYER_NAME):
                self.drop_undo_entries_for_layer(lyr)
        except Exception:
            pass
        clear_buildings()
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()

    def _bt_clear_trees(self):
        from .building_tree_mapper import count_trees, clear_trees
        if count_trees() == 0:
            return
        reply = QMessageBox.question(
            self, "Clear trees", "Remove all mapped trees?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._bt_finish()
        try:
            from qgis.core import QgsProject
            from .building_tree_mapper import TREE_LAYER_NAME
            for lyr in QgsProject.instance().mapLayersByName(TREE_LAYER_NAME):
                self.drop_undo_entries_for_layer(lyr)
        except Exception:
            pass
        clear_trees()
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()

    # ── Enhancement availability (curtilage / developed / available) ────────

    def _bt_curtilage_action(self, checked):
        """Toggled handler for the Draw curtilage button — same native
        add-feature session as freehand buildings, on the availability layer."""
        if not checked:
            self._bt_finish()
            return
        self._bt_untoggle_others(self._bt_curt_btn)
        from .curtilage_mapper import get_or_create_availability_layer
        self._bt_start_draw_polygon(
            get_or_create_availability_layer(), self._bt_commit_curtilage_cb,
            "Click to outline the residential curtilage, then right-click to finish.")
        self._bt_curt_btn.setText("Finish curtilage")

    def _bt_commit_curtilage_cb(self, lyr, fid):
        from .curtilage_mapper import (
            assign_drawn_curtilage, clip_to_redline, has_autofill)
        # Clip the drawn polygon to the redline (when one exists) so curtilage
        # never spills outside the scheme boundary on the map.
        rg = self._get_redline_geom()
        if rg is not None:
            feat = lyr.getFeature(fid)
            clipped = clip_to_redline(feat.geometry(), rg)
            if clipped is None:
                lyr.deleteFeature(fid)
                lyr.commitChanges()
                self.iface.messageBar().pushMessage(
                    "Site Plan Visualiser",
                    "That area is entirely outside the redline — nothing was added.",
                    level=Qgis.MessageLevel.Warning, duration=3)
                return
            clipped.convertToMultiType()     # layer is MultiPolygon
            lyr.changeGeometry(fid, clipped)
        assign_drawn_curtilage(lyr, fid)
        # No unique id field here (unlike buildings), so find the committed
        # feature by fid set-difference across the commit.
        pre = {f.id() for f in lyr.getFeatures()}
        from .curtilage_mapper import _commit_or_rollback
        if not _commit_or_rollback(lyr):
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser",
                "The curtilage area couldn't be saved to the layer — nothing was added.",
                level=Qgis.MessageLevel.Warning, duration=4)
            return
        new_fids = [f.id() for f in lyr.getFeatures() if f.id() not in pre]
        if new_fids:
            self.push_undo(lyr, max(new_fids), None, None)
        self.iface.mapCanvas().refresh()
        msg = "Curtilage area added"
        if has_autofill():
            msg += " — re-run Fill remainder to update the available area"
        self.iface.messageBar().pushMessage(
            "Site Plan Visualiser", msg, level=Qgis.MessageLevel.Info, duration=3)

    def _bt_ask_gardens_curtilage(self):
        """Popup: are the proposal's garden parcels residential curtilage?
        Only asked when the proposal actually contains garden parcels; the
        answer is remembered for the session and pre-selected next time.
        Returns False (gardens stay available) when there are none to ask
        about."""
        from .curtilage_mapper import count_proposal_gardens
        n = count_proposal_gardens()
        if n == 0:
            return False
        prev = getattr(self, "_bt_gardens_curtilage", False)
        box = QMessageBox(self)
        box.setWindowTitle("Garden areas")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            "The proposal includes %d garden parcel%s.\n\n"
            "Are the garden areas within the redline residential curtilage?"
            % (n, "" if n == 1 else "s"))
        box.setInformativeText(
            "Yes — gardens are hatched as residential curtilage (not "
            "available for enhancement).\n"
            "No — gardens are left as available like any other soft area.")
        yes = box.addButton("Yes — curtilage",
                            QMessageBox.ButtonRole.YesRole)
        no = box.addButton("No — keep available",
                           QMessageBox.ButtonRole.NoRole)
        box.setDefaultButton(yes if prev else no)
        box.exec()
        ans = box.clickedButton() is yes
        self._bt_gardens_curtilage = ans
        return ans

    def _bt_detect_availability(self):
        """Scan the proposed habitats: sealed/built → developed, and gardens →
        curtilage when the user says so (popup). Re-runnable; each run
        replaces the previous detection and registers as ONE global-undo
        entry."""
        from .curtilage_mapper import (
            detect_from_proposal, get_or_create_availability_layer, has_autofill)
        self._bt_finish()      # stop any armed placement tool first
        res = detect_from_proposal(self._bt_ask_gardens_curtilage())
        if res is None:
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser",
                "No proposed habitat layer to scan — draw the proposed habitats first.",
                level=Qgis.MessageLevel.Warning, duration=4)
            return
        removed, added, n_curt, n_dev = res
        lyr = get_or_create_availability_layer()
        if removed:
            # The old detected features are gone — purge their undo entries so
            # they don't linger as click-burning duds.
            self.drop_undo_entries([(lyr, fid) for fid in removed])
        if added:
            self.push_undo(lyr, added[0], None, None,
                           extra_baseline_fids=added[1:])
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()
        if n_curt or n_dev:
            parts = []
            if n_dev:
                parts.append("%d developed parcel%s"
                             % (n_dev, "" if n_dev == 1 else "s"))
            if n_curt:
                parts.append("%d garden parcel%s as curtilage"
                             % (n_curt, "" if n_curt == 1 else "s"))
            msg = "Detected " + " and ".join(parts)
            if has_autofill():
                msg += " — re-run Fill remainder to update the available area"
        else:
            msg = "No developed land found in the proposal"
        self.iface.messageBar().pushMessage(
            "Site Plan Visualiser", msg, level=Qgis.MessageLevel.Info, duration=4)

    def _bt_fill_available(self):
        """Mark everything inside the redline not already curtilage/developed
        as available for enhancement. ALWAYS re-runs the proposal detection
        first, so Fill reflects the current proposal without needing Detect
        pressed separately. Re-runnable; replaces the previous fill."""
        from .curtilage_mapper import (
            detect_from_proposal, fill_remainder, get_or_create_availability_layer)
        self._bt_finish()      # stop any armed placement tool first
        rg = self._get_redline_geom()
        if rg is None:
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser",
                "Draw the redline boundary first — the available area is "
                "everything inside it.",
                level=Qgis.MessageLevel.Warning, duration=4)
            return
        lyr = get_or_create_availability_layer()
        det_note = ""
        det = detect_from_proposal(self._bt_ask_gardens_curtilage())
        if det is not None:
            d_removed, d_added, n_curt, n_dev = det
            if d_removed:
                self.drop_undo_entries([(lyr, fid) for fid in d_removed])
            if d_added:
                self.push_undo(lyr, d_added[0], None, None,
                               extra_baseline_fids=d_added[1:])
            n_unavail = n_curt + n_dev
            if n_unavail:
                det_note = ("%d parcel%s marked unavailable · "
                            % (n_unavail, "" if n_unavail == 1 else "s"))
        removed, added, ha = fill_remainder(rg)
        if removed:
            self.drop_undo_entries([(lyr, fid) for fid in removed])
        if added:
            self.push_undo(lyr, added[0], None, None,
                           extra_baseline_fids=added[1:])
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()
        msg = det_note + ("%.2f ha marked available for enhancement" % ha
                          if added else "no area remains available inside the redline")
        self.iface.messageBar().pushMessage(
            "Site Plan Visualiser", msg, level=Qgis.MessageLevel.Info, duration=4)

    def _bt_undo_curtilage(self):
        from .curtilage_mapper import peek_last_drawn, remove_last_drawn
        if getattr(self, "_bt_curt_btn", None) and self._bt_curt_btn.isChecked():
            self._bt_finish()  # commit/exit the draw session before removing
        pair = peek_last_drawn()   # sync the global stack with this removal
        ok = remove_last_drawn()
        if ok and pair:
            self.drop_undo_entries([pair])
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()
        if ok:
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser", "Curtilage area removed",
                level=Qgis.MessageLevel.Info, duration=1)

    def _bt_clear_availability(self):
        from .curtilage_mapper import has_availability, clear_availability
        if not has_availability():
            return
        reply = QMessageBox.question(
            self, "Clear availability areas",
            "Remove all curtilage, developed and available areas?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._bt_finish()
        self._bt_draw_layer = None
        try:
            from qgis.core import QgsProject
            from .curtilage_mapper import AVAIL_LAYER_NAME
            for lyr in QgsProject.instance().mapLayersByName(AVAIL_LAYER_NAME):
                self.drop_undo_entries_for_layer(lyr)
        except Exception:
            pass
        clear_availability()
        self.iface.mapCanvas().refresh()
        self._bt_update_counts()

    def _bt_import_trees_csv(self):
        """Pick a CSV and open the auto-detecting tree importer; on success the
        placed trees are zoomed to and the live counts refresh."""
        from qgis.PyQt.QtWidgets import QFileDialog
        self._bt_finish()      # stop any armed placement tool first
        path, _ = QFileDialog.getOpenFileName(
            self, "Import trees from CSV", "",
            "CSV / text files (*.csv *.txt *.tsv);;All files (*)")
        if not path:
            return
        try:
            from .tree_csv_import import TreeCsvImportDialog
            redline = self._get_redline_geom()
            dlg = TreeCsvImportDialog(path, redline, self)
        except Exception:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(
                self, "Import trees",
                "Sorry — that file couldn't be read as a table of coordinates.")
            return
        # Snapshot the tree layer's fids so the whole import can be registered
        # as ONE global undo entry (a single Ctrl+Z removes the entire batch).
        from qgis.core import QgsProject
        from .building_tree_mapper import TREE_LAYER_NAME
        _pre_lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
        pre_fids = ({f.id() for f in _pre_lyrs[0].getFeatures()}
                    if _pre_lyrs else set())
        if dlg.exec() and getattr(dlg, "imported_count", 0):
            n = dlg.imported_count
            lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
            if lyrs:
                added = [f.id() for f in lyrs[0].getFeatures()
                         if f.id() not in pre_fids]
                if added:
                    self.push_undo(lyrs[0], added[0], None, None,
                                   extra_baseline_fids=added[1:])
            self.iface.mapCanvas().refresh()
            self._bt_update_counts()
            self._bt_zoom_trees()
            self.iface.messageBar().pushMessage(
                "Site Plan Visualiser",
                "%d tree%s imported from CSV" % (n, "" if n == 1 else "s"),
                level=Qgis.MessageLevel.Info, duration=3)

    def _bt_zoom_trees(self):
        """Frame the Trees (Map Only) layer on the canvas after an import."""
        from .building_tree_mapper import TREE_LAYER_NAME
        from qgis.core import QgsProject, QgsCoordinateTransform
        lyrs = QgsProject.instance().mapLayersByName(TREE_LAYER_NAME)
        if not lyrs or lyrs[0].featureCount() == 0:
            return
        lyr = lyrs[0]
        canvas = self.iface.mapCanvas()
        try:
            ext = lyr.extent()
            if ext.width() < 60 and ext.height() < 60:
                ext.grow(80)          # single point / tight cluster → give it room
            src = lyr.crs()
            dst = canvas.mapSettings().destinationCrs()
            if src != dst:
                xform = QgsCoordinateTransform(src, dst, QgsProject.instance())
                ext = xform.transformBoundingBox(ext)
            ext.scale(1.15)
            canvas.setExtent(ext)
            canvas.refresh()
        except Exception:
            pass

    def _create_bt_map(self, map_type):
        import traceback
        try:
            from .map_exporter import MapExportDialog, build_layout
            dlg = MapExportDialog(parent=self.iface.mainWindow())
            dlg.map_type_combo.setCurrentText(map_type)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                vals = dlg.values()
                _ce, _cn = None, None
                if getattr(self, "_centroid_bng", None):
                    _ce, _cn = self._centroid_bng
                _rg = self._get_redline_geom()
                if _rg:
                    _bb = _rg.boundingBox()
                    vals["redline_bbox"] = (
                        _bb.xMinimum(), _bb.yMinimum(),
                        _bb.xMaximum(), _bb.yMaximum(),
                    )
                else:
                    vals["redline_bbox"] = None
                vals["centroid_e"] = _ce
                vals["centroid_n"] = _cn
                build_layout(self.iface, vals)
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Biome", f"Map error: {e}", level=Qgis.MessageLevel.Critical, duration=0
            )
            traceback.print_exc()

