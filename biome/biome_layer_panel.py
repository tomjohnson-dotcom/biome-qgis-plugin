# biome_layer_panel.py — Biome's own compact layer show/hide panel.
#
# A small, themed dock that mirrors the TOP LEVEL of the QGIS layer tree as a
# flat list of "trunks" — one row per top-level group or bare layer. It is shown
# on the right while Biome mode is on, replacing the native Layers panel (which
# Biome mode hides). Each trunk carries an eye toggle; the Baseline / Proposed
# habitat groups additionally expand to per-sublayer eye toggles (trees /
# hedgerows / watercourse / area). User-added trunks can be deleted and the list
# can be reordered by drag — the plugin's own scaffold (Base Maps, Baseline /
# Proposed Habitats, Ecological Features, Redline / Blue line) is protected from
# deletion.
#
# Deliberately NOT a replacement for the full QGIS Layers panel: it only does
# visibility, delete and reorder. Toggle Biome mode off to get the native panel
# back.

from qgis.PyQt.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton,
    QListWidget, QListWidgetItem, QAbstractItemView, QMessageBox, QApplication,
    QGraphicsDropShadowEffect,
)
from qgis.PyQt.QtCore import (
    Qt, QTimer, QSize, QMimeData, QRect,
    QVariantAnimation, QPropertyAnimation, QEasingCurve,
)
from qgis.PyQt.QtGui import QDrag, QPixmap, QColor

from qgis.core import Qgis
from qgis.core import (
    QgsProject, QgsLayerTreeGroup, QgsLayerTreeLayer, QgsRasterLayer,
)


def _evt_pos(event):
    """Event position as a QPoint on both bindings: Qt6 renamed
    QDropEvent/QMouseEvent.pos() to position(); Qt5 only has pos()."""
    try:
        return event.position().toPoint()
    except AttributeError:
        return event.pos()

from .config import measurement_ellipsoid
from .layer_manager import display_layer_name, REDLINE_NAME_ALIASES
from . import theme


# Top-level nodes the plugin scaffolds on initiation — never deletable.
CORE_GROUPS = {
    "Base Maps", "Baseline Habitats", "Proposed Habitats", "Ecological Features",
}
CORE_LAYERS = set(REDLINE_NAME_ALIASES) | {"Blue Line Boundary"}

# These groups get an expandable per-sublayer dropdown. Ecological Features
# uses nested geometry subgroups in the native QGIS tree, so _build_children
# deliberately walks descendants rather than direct children only.
DROPDOWN_GROUPS = (
    "Baseline Habitats", "Proposed Habitats", "Ecological Features",
    "Site Plan Visualiser", "Standing Water Bodies",
)

_STANDING_WATER_CHILDREN = {
    "Mapped Water-body Outlines": (0, "Pond outlines"),
    "Standing Water Bodies": (0, "Pond outlines"),  # legacy projects
    "Water Body": (1, "SWB markers"),
    "Pond Distance Lines": (2, "Distance lines"),
}

# Friendly sublayer labels — matched by a token in the layer name (in render
# order: trees, hedgerows, watercourse, area).
_CHILD_ORDER = (
    ("trees", "Trees"),
    ("tree buffers", "Tree buffers"),
    ("tree rpas", "Tree RPAs"),
    ("construction exclusion", "Construction Exclusion Zones"),
    ("restricted activity", "Restricted Activity Zones"),
    ("hedge", "Hedgerows"),
    ("water", "Watercourse"),
    ("area",  "Area habitats"),
)


def _child_rank(name):
    standing_water = _STANDING_WATER_CHILDREN.get(name or "")
    if standing_water is not None:
        return standing_water[0]
    n = (name or "").lower()
    for i, (token, _label) in enumerate(_CHILD_ORDER):
        if token in n:
            return i
    return 99


def _child_label(name):
    standing_water = _STANDING_WATER_CHILDREN.get(name or "")
    if standing_water is not None:
        return standing_water[1]
    n = (name or "").lower()
    for token, label in _CHILD_ORDER:
        if token in n:
            return label
    return name


class _TrunkList(QListWidget):
    """The flat list of trunk rows. Reordering is a manual vertical drag confined
    to the list: the picked-up row follows the cursor up/down inside the box (it
    never leaves it), the other rows part to open a slot, and on release the
    single dragged node is moved between its siblings — it never nests, and the
    row widgets are never orphaned (QListWidget never moves an item itself)."""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)   # we start it ourselves
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSpacing(0)
        self.setUniformItemSizes(False)
        # Accept drops so our own drag loop's move/drop events arrive here.
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

        self._press_row = -1
        self._press_pos = None
        self._dragging = False
        self._proxy = None        # our own vertically-clamped boxed copy
        self._proxy_x = 0
        self._grab_dy = 0
        self._src_row = -1
        self._ph_row = -1
        self._drag_desc = None
        # Tactile feel: pickup pop + landing settle animations.
        self._lift = None         # QVariantAnimation growing the proxy on pickup
        self._settle = None       # QPropertyAnimation landing it in the slot
        self._base_size = None    # un-lifted proxy size (lift scales from this)
        self._base_x = 0
        self._base_grab_dy = 0
        self._last_y = 0          # last cursor y, so lift ticks can re-position

    # ── manual vertical drag (Qt drag loop, invisible payload, own proxy) ──
    def mousePressEvent(self, event):
        self._press_row = -1
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(_evt_pos(event))
            if item is not None:
                self._press_row = self.row(item)
                self._press_pos = _evt_pos(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press_row >= 0 and not self._dragging
                and (event.buttons() & Qt.MouseButton.LeftButton)
                and (_evt_pos(event) - self._press_pos).manhattanLength()
                    >= QApplication.startDragDistance()):
            self._start_drag()        # runs Qt's drag loop (blocks until release)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_row = -1
        super().mouseReleaseEvent(event)

    def _make_slot(self):
        """A dashed-outline placeholder widget marking where the row will drop."""
        slot = QFrame()
        slot.setObjectName("dropSlot")
        slot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return slot

    def _start_drag(self):
        src = self._press_row
        item = self.item(src)
        if item is None:
            self._press_row = -1
            return
        w = self.itemWidget(item)
        # Our own boxed copy of the row card — clamped to vertical movement
        # inside the list, so nothing roams the screen.
        self._proxy = QLabel(self.viewport())
        if w is not None:
            self._proxy.setPixmap(w.grab())
            self._proxy.resize(w.size())
            self._proxy_x = w.x()                       # align with the inset card
            self._grab_dy = self._press_pos.y() - w.y()
            self._base_size = QSize(w.size())
        else:
            self._proxy_x = 0
            self._grab_dy = self._press_pos.y() - self.visualItemRect(item).top()
            self._base_size = QSize(self.visualItemRect(item).size())
            self._proxy.resize(self._base_size)
        # The lift/settle animations resize the proxy — scale the snapshot with it.
        self._proxy.setScaledContents(True)
        self._base_x = self._proxy_x
        self._base_grab_dy = self._grab_dy
        self._proxy.setStyleSheet(
            "background: %s; border: 2px solid %s; border-radius: 9px;"
            % (theme.CARD, theme.ACCENT))
        # Soft shadow so the picked-up card visibly floats above its siblings.
        try:
            shadow = QGraphicsDropShadowEffect(self._proxy)
            shadow.setBlurRadius(20)
            shadow.setOffset(0, 5)
            shadow.setColor(QColor(26, 36, 32, 110))
            self._proxy.setGraphicsEffect(shadow)
        except Exception:
            pass
        self._drag_desc = item.data(Qt.ItemDataRole.UserRole)
        self.setItemWidget(item, self._make_slot())   # leave a placeholder slot
        self._dragging = True
        self._src_row = src
        self._ph_row = src
        self._last_y = self._press_pos.y()
        self._proxy.show()
        self._proxy.raise_()
        self._position(self._press_pos.y())
        self._start_lift()

        # Use Qt's drag loop purely for its reliability — it always ends on
        # release (anywhere) and fires dragMove/drop — but with an invisible
        # 1px payload so there is no screen-roaming ghost (we draw our own proxy).
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-biome-layer-row", b"1")
        drag.setMimeData(mime)
        blank = QPixmap(1, 1)
        blank.fill(Qt.GlobalColor.transparent)
        drag.setPixmap(blank)
        drag.exec(Qt.DropAction.MoveAction)
        self._end_drag()              # always runs once the mouse is released

    def dragEnterEvent(self, event):
        # Only our own row drag — a foreign drag (e.g. a data file dropped on
        # QGIS) must be refused so it isn't silently swallowed by the overlay.
        if event.mimeData().hasFormat("application/x-biome-layer-row"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasFormat("application/x-biome-layer-row"):
            event.ignore()
            return
        if self._dragging:
            self._position(_evt_pos(event).y())
        event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-biome-layer-row"):
            event.acceptProposedAction()   # the move itself happens in _end_drag
        else:
            event.ignore()

    def _start_lift(self):
        """Pickup pop: the card grows ~3.5% over a tenth of a second, staying
        centred on the grab point — with the shadow it reads as lifting off."""
        self._lift = QVariantAnimation(self)
        self._lift.setDuration(120)
        self._lift.setStartValue(0.0)
        self._lift.setEndValue(1.0)
        self._lift.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._lift.valueChanged.connect(self._lift_tick)
        self._lift.start()

    def _lift_tick(self, v):
        if self._proxy is None or not self._dragging or self._base_size is None:
            return
        s = 1.0 + 0.035 * float(v)
        nw = int(round(self._base_size.width() * s))
        nh = int(round(self._base_size.height() * s))
        # Grow outward from the centre so the card doesn't crawl sideways.
        self._proxy_x = self._base_x - (nw - self._base_size.width()) // 2
        self._grab_dy = self._base_grab_dy + (nh - self._base_size.height()) // 2
        self._proxy.resize(nw, nh)
        self._position(self._last_y)

    def _position(self, y):
        self._last_y = y
        # Move the placeholder one row at a time, each time the cursor crosses
        # the neighbouring row's midpoint (so it never jumps two at once).
        guard = 0
        while guard < self.count():
            guard += 1
            ph = self._ph_row
            if ph < self.count() - 1:
                nb = self.item(ph + 1)
                if nb is not None and y > self.visualItemRect(nb).center().y():
                    self._move_slot(ph, ph + 1)
                    continue
            if ph > 0:
                nb = self.item(ph - 1)
                if nb is not None and y < self.visualItemRect(nb).center().y():
                    self._move_slot(ph, ph - 1)
                    continue
            break
        # Proxy follows the cursor vertically only, clamped inside the list box.
        if self._proxy is not None:
            top = max(0, min(y - self._grab_dy,
                             self.viewport().height() - self._proxy.height()))
            self._proxy.move(self._proxy_x, top)
            self._proxy.raise_()

    def _move_slot(self, frm, to):
        it = self.takeItem(frm)
        self.insertItem(to, it)
        self.setItemWidget(it, self._make_slot())   # take() drops the old widget
        self._ph_row = to

    def _end_drag(self):
        """Mouse is up: land the card into the placeholder slot with a short
        settle animation (slight overshoot, then spring back — the tactile
        'thunk'), and only then apply the move. The dragging flag stays set
        until it lands so nothing rebuilds the list under the animation."""
        if self._lift is not None:
            self._lift.stop()
            self._lift = None
        target = None
        item = self.item(self._ph_row) if self._ph_row >= 0 else None
        if item is not None:
            slot = self.itemWidget(item)
            target = slot.geometry() if slot is not None else self.visualItemRect(item)
        if self._proxy is None or target is None or not target.isValid():
            self._finish_drop()
            return
        anim = QPropertyAnimation(self._proxy, b"geometry", self)
        anim.setDuration(220)
        anim.setStartValue(self._proxy.geometry())
        anim.setEndValue(QRect(target))
        anim.setEasingCurve(QEasingCurve.Type.OutBack)   # overshoot the slot, spring back
        anim.finished.connect(self._finish_drop)
        self._settle = anim
        anim.start()

    def _finish_drop(self):
        src = self._src_row
        dst = self._ph_row
        desc = self._drag_desc
        if self._settle is not None:
            try:
                self._settle.stop()
            except Exception:
                pass
            self._settle = None
        proxy = self._proxy
        self._proxy = None
        self._dragging = False
        self._press_row = -1
        self._src_row = -1
        self._ph_row = -1
        self._drag_desc = None
        if desc is not None and dst != src and dst >= 0:
            # Move the node so its final position matches the placeholder. The
            # tree still holds the original order, so this is one clean move.
            insert_index = dst if dst <= src else dst + 1
            self._panel._move_node(desc, insert_index)
        else:
            self._panel.refresh_now()   # no move — rebuild restores the row widget
        # The landed proxy sits exactly where the rebuilt card appears; keep it
        # painted for a beat so the swap to the real card is seamless (removing
        # it first would flash the empty dashed slot for a frame).
        if proxy is not None:
            def _pull():
                try:
                    proxy.hide()
                    proxy.deleteLater()
                except Exception:
                    pass
            QTimer.singleShot(90, _pull)


class BiomeLayerPanel(QFrame):
    """Floating, content-height layer panel overlaying the top-left of the map
    canvas, so the map stays visible all around it. A docked panel can't do this
    — the dock area always reserves a full-height strip — hence the overlay."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setObjectName("biomeLayersOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.iface = iface
        self._canvas = None
        self._filter_installed = False

        self._building = False
        self._dead = False            # True once torn down — bail on stale callbacks
        self._suppress = 0            # >0 while we mutate the tree ourselves
        self._rebuild_pending = False
        self._footer_pending = False  # debounce for feature-driven stat refreshes
        self._wired_layers = set()    # layer ids whose feature signals we listen to
        self._geo_issues = []         # cached invalid-geometry features for the flyout
        self._overlaps = []           # cached same-layer polygon overlaps for the flyout
        self._flyout = None           # hover list of incomplete features
        self._hls = []                # transient QgsHighlights for the hovered row (1–2 feats)
        self._pill_hover = False
        self._flyout_hover = False
        self._collapsed = False       # whole panel folded down to just the header
        self._expanded = {name: False for name in DROPDOWN_GROUPS}
        self._eye_rows = []           # [(descriptor, QToolButton)] for light sync
        self._width = 290
        self._margin = 10
        self._header_h = 32
        self._content_h = 160

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("overlayHeader")
        header.setFixedHeight(self._header_h)
        hh = QHBoxLayout(header)
        hh.setContentsMargins(11, 0, 6, 0)
        hh.setSpacing(0)
        hicon = QLabel()
        hicon.setPixmap(theme.icon("layers", color=theme.ACCENT_DARK, size=15).pixmap(15, 15))
        hicon.setStyleSheet("background: transparent;")
        title = QLabel("Layers")
        title.setObjectName("overlayTitle")
        hh.addWidget(hicon)
        hh.addSpacing(7)
        hh.addWidget(title, 1)
        # Collapse / expand the whole panel — sits at the right of the header.
        self._collapse_btn = QToolButton()
        self._collapse_btn.setProperty("biomeIconBtn", True)
        self._collapse_btn.setObjectName("collapseBtn")
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.setIconSize(QSize(15, 15))
        self._collapse_btn.setAutoRaise(True)
        self._collapse_btn.clicked.connect(self._toggle_collapsed)
        hh.addWidget(self._collapse_btn)
        self._paint_collapse_btn()
        root.addWidget(header)

        self._list = _TrunkList(self)
        self._list.setSpacing(3)      # gaps between the rounded row cards
        root.addWidget(self._list)
        self._empty = QLabel("No layers yet.")
        self._empty.setObjectName("biomeLayersEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setVisible(False)
        root.addWidget(self._empty)

        root.addWidget(self._build_footer())

        self.setFixedWidth(self._width)
        self.setStyleSheet(self._qss())

        self._connect_signals()
        self._rebuild()

        # The overlay floats on the CANVAS, outside the dock's widget tree, so
        # the main panel's theme listener never reaches it. Restyle ourselves
        # on a light/dark flip: regenerate the token-driven QSS and rebuild the
        # rows (their card styles bake tokens in at build time). Self-
        # unregisters once this overlay is destroyed.
        def _on_theme_flip():
            try:
                from qgis.PyQt import sip
                if sip.isdeleted(self):
                    theme.remove_theme_listener(_on_theme_flip)
                    return
            except Exception:
                pass
            try:
                import time as _t
                t0 = _t.perf_counter()
                self.setStyleSheet(self._qss())
                self._rebuild()
                self.refresh_footer()
                self.update()
                theme._tlog("  overlay: restyle+rebuild %.1fms"
                            % ((_t.perf_counter() - t0) * 1000))
            except Exception:
                pass
        self._theme_flip_cb = _on_theme_flip
        theme.add_theme_listener(_on_theme_flip)

    # ── canvas overlay placement ──────────────────────────────────────────
    def attach(self, canvas):
        """Parent onto the map canvas and pin to the top-left corner."""
        self._canvas = canvas
        if self.parent() is not canvas:
            self.setParent(canvas)
        if not self._filter_installed:
            canvas.installEventFilter(self)
            self._filter_installed = True
        self._reposition()
        self.show()
        self.raise_()
        # On first show the footer height and canvas size aren't settled, so the
        # initial fit can under-size and the footer overlaps the last card.
        # Re-fit a few beats later (also catches habitat layers/features that
        # arrive after project open).
        QTimer.singleShot(60, self._settle)
        QTimer.singleShot(400, self._settle)
        QTimer.singleShot(1200, self._settle)

    def _settle(self):
        """Recompute stats + height once the widgets/canvas have laid out."""
        if self._dead:
            return
        self.refresh_footer()
        self._apply_fit_height()

    def eventFilter(self, obj, event):
        try:
            from qgis.PyQt.QtCore import QEvent
        except ImportError:
            from qgis.PyQt.QtCore import QEvent
        et = event.type()
        if obj is self._canvas and et in (QEvent.Type.Resize, QEvent.Type.Show):
            # Recompute the fit (footer height + list cap), not just position —
            # the available canvas height may have changed.
            self._apply_fit_height()
        elif obj is getattr(self, "_incomplete_btn", None):
            if et == QEvent.Type.Enter and self._incomplete_btn.isEnabled():
                self._pill_hover = True
                self._open_flyout()
            elif et == QEvent.Type.Leave:
                self._pill_hover = False
                self._schedule_close_flyout()
        return False

    def _reposition(self):
        if self._dead or self._canvas is None:
            return
        # A moved/resized panel would leave the flyout floating in the wrong spot.
        self._close_flyout()
        # Size to the full content so every layer shows (no scroll). If the list
        # is taller than the canvas the user can collapse it or shrink groups.
        self.resize(self._width, self._content_h)
        self.move(self._margin, self._margin)
        self.raise_()

    # ── collapse / expand the whole panel ────────────────────────────────
    def _paint_collapse_btn(self):
        # MUTED matches chevron-down.svg's baked colour, so up/down look the same.
        if self._collapsed:
            self._collapse_btn.setIcon(theme.icon("chevron-down", color=theme.MUTED, size=15))
            self._collapse_btn.setToolTip("Expand layers")
        else:
            self._collapse_btn.setIcon(theme.icon("chevron-up", color=theme.MUTED, size=15))
            self._collapse_btn.setToolTip("Collapse layers")

    def _toggle_collapsed(self):
        self._collapsed = not self._collapsed
        self._paint_collapse_btn()
        self._list.setVisible(not self._collapsed)
        self._empty.setVisible(not self._collapsed and self._list.count() == 0)
        self._footer.setVisible(not self._collapsed)
        self._apply_fit_height()

    # ── stats footer (incomplete features + site / coverage) ──────────────
    # Required survey fields; a habitat feature missing any is "incomplete".
    _REQ_FIELDS = ("sub_habitat_type", "condition", "strategic_significance")

    def _build_footer(self):
        self._footer = QFrame()
        self._footer.setObjectName("statsFooter")
        self._footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QVBoxLayout(self._footer)
        fl.setContentsMargins(11, 7, 11, 9)
        fl.setSpacing(6)

        # Row 1: site metric.
        r1 = QHBoxLayout(); r1.setContentsMargins(0, 0, 0, 0); r1.setSpacing(5)
        sic = QLabel()
        sic.setPixmap(theme.icon("polygon", color=theme.MUTED, size=14).pixmap(14, 14))
        sic.setStyleSheet("background: transparent;")
        skey = QLabel("Site"); skey.setObjectName("statKey")
        self._site_lbl = QLabel("—"); self._site_lbl.setObjectName("statVal")
        r1.addWidget(sic)
        r1.addWidget(skey)
        r1.addWidget(self._site_lbl)
        r1.addStretch(1)
        fl.addLayout(r1)

        # Coverage rows with a thin progress bar each, wrapped so the whole
        # block can be swapped for a friendly hint before anything is mapped.
        self._cov_container = QWidget()
        self._cov_container.setStyleSheet("background: transparent;")
        cc = QVBoxLayout(self._cov_container)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(6)
        self._baseline_val, self._baseline_bar = self._coverage_row(cc, "Baseline", theme.ACCENT)
        self._proposed_val, self._proposed_bar = self._coverage_row(cc, "Proposed", "#6FA98C")
        fl.addWidget(self._cov_container)

        self._empty_hint = QLabel("No habitats mapped yet")
        self._empty_hint.setObjectName("covVal")
        self._empty_hint.setVisible(False)
        fl.addWidget(self._empty_hint)

        # Data-quality line (own row so the full wording fits). Amber + clickable
        # when something is missing; green and inert when nothing is.
        r3 = QHBoxLayout(); r3.setContentsMargins(0, 0, 0, 0); r3.setSpacing(0)
        self._incomplete_btn = QToolButton()
        self._incomplete_btn.setObjectName("statChip")
        self._incomplete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._incomplete_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._incomplete_btn.setIconSize(QSize(13, 13))
        self._incomplete_btn.clicked.connect(self._open_flyout)
        self._incomplete_btn.installEventFilter(self)   # Enter/Leave → flyout
        r3.addWidget(self._incomplete_btn)
        r3.addStretch(1)
        fl.addLayout(r3)
        return self._footer

    def _coverage_row(self, parent_layout, label, fill_color):
        box = QVBoxLayout(); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(3)
        lr = QHBoxLayout(); lr.setContentsMargins(0, 0, 0, 0); lr.setSpacing(0)
        key = QLabel(label); key.setObjectName("covKey")
        val = QLabel("—"); val.setObjectName("covVal")
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lr.addWidget(key)
        lr.addWidget(val, 1)
        box.addLayout(lr)

        track = QFrame(); track.setObjectName("barTrack"); track.setFixedHeight(5)
        track.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tl = QHBoxLayout(track); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(0)
        fill = QFrame()
        fill.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fill.setStyleSheet("background: %s; border-radius: 3px;" % fill_color)
        tl.addWidget(fill, 0)
        tl.addWidget(QWidget(), 100)
        box.addWidget(track)
        parent_layout.addLayout(box)
        return val, (tl, fill)

    @staticmethod
    def _fmt_ha(ha):
        if ha >= 100:
            return "%.0f ha" % ha
        if ha >= 10:
            return "%.1f ha" % ha
        return "%.2f ha" % ha

    @staticmethod
    def _set_bar(bar, pct):
        tl, fill = bar
        p = max(0, min(100, int(round(pct))))
        tl.setStretch(0, p)
        tl.setStretch(1, 100 - p)
        fill.setVisible(p > 0)

    def _set_cov(self, val_lbl, bar, ha, site):
        if ha <= 1e-9:
            # Phase not mapped yet — read as 0% of a known site, else a dash.
            val_lbl.setText("0%" if site > 1e-9 else "—")
            self._set_bar(bar, 0)
        elif site > 1e-9:
            pct = ha / site * 100.0
            val_lbl.setText("%s · %d%%" % (self._fmt_ha(ha), round(pct)))
            self._set_bar(bar, pct)
        else:
            val_lbl.setText(self._fmt_ha(ha))
            self._set_bar(bar, 0)

    @staticmethod
    def _chip_qss(fg, bg):
        return ("QToolButton#statChip { background:%s; color:%s; border:none;"
                " border-radius:9px; padding:2px 9px 2px 6px; font-size:11px;"
                " font-family:%s; }" % (bg, fg, theme.FONT_MEDIUM))

    def refresh_footer(self):
        """Recompute the footer stats. Cheap; safe to call on every edit."""
        if self._dead:
            return
        # Make sure we're listening to feature edits on every stat layer (incl.
        # any created since the last refresh) so coverage never goes stale.
        self._wire_layer_signals()
        try:
            site, bl, pr = self._areas()
            items, total = self._incomplete_list()
            self._geo_issues = self._geometry_issues()
            self._overlaps = self._overlap_issues()
        except Exception:
            return
        incomplete = len(items)
        geo = len(self._geo_issues)
        ov = len(self._overlaps)
        self._site_lbl.setText(self._fmt_ha(site) if site > 1e-9 else "—")

        # Before anything is mapped, swap the (empty) coverage bars for a hint.
        mapped = total > 0
        self._cov_container.setVisible(mapped)
        self._empty_hint.setVisible(not mapped)
        if mapped:
            self._set_cov(self._baseline_val, self._baseline_bar, bl, site)
            self._set_cov(self._proposed_val, self._proposed_bar, pr, site)

        # Priority order: invalid geometry (silently zeros area) first, then
        # overlaps (silently double-count area) — both corrupt the metric, so
        # both are red — then missing data, then the all-clear.
        if geo > 0:
            self._incomplete_btn.setVisible(True)
            self._incomplete_btn.setEnabled(True)
            self._incomplete_btn.setIcon(theme.icon("status-alert", color=theme.BAD, size=13))
            noun = "feature" if geo == 1 else "features"
            self._incomplete_btn.setText(" %d %s with invalid geometry" % (geo, noun))
            self._incomplete_btn.setStyleSheet(self._chip_qss(theme.BAD, theme.BAD_BG))
            self._incomplete_btn.setToolTip(
                "These parcels have broken outlines (e.g. slivers) that zero out "
                "their area in the metric. Hover to locate, click to auto-repair.")
        elif ov > 0:
            self._incomplete_btn.setVisible(True)
            self._incomplete_btn.setEnabled(True)
            self._incomplete_btn.setIcon(theme.icon("status-alert", color=theme.BAD, size=13))
            noun = "overlap" if ov == 1 else "overlaps"
            self._incomplete_btn.setText(" %d habitat %s" % (ov, noun))
            self._incomplete_btn.setStyleSheet(self._chip_qss(theme.BAD, theme.BAD_BG))
            self._incomplete_btn.setToolTip(
                "Area parcels in the same layer overlap, so the shared ground is "
                "counted twice in the metric. Hover to locate, click to select the "
                "pair (reshape or delete one in Tools ▸ Manage).")
        elif total == 0:
            self._incomplete_btn.setVisible(False)
        elif incomplete > 0:
            self._incomplete_btn.setVisible(True)
            self._incomplete_btn.setEnabled(True)
            self._incomplete_btn.setIcon(theme.icon("status-alert", color=theme.WARN, size=13))
            noun = "feature" if incomplete == 1 else "features"
            self._incomplete_btn.setText(" %d %s with missing data" % (incomplete, noun))
            self._incomplete_btn.setStyleSheet(self._chip_qss(theme.WARN, theme.WARN_BG))
            self._incomplete_btn.setToolTip("Hover to list them; hover a row to find it, click to fix.")
        else:
            self._incomplete_btn.setVisible(True)
            self._incomplete_btn.setEnabled(False)
            self._incomplete_btn.setIcon(theme.icon("status-check", color=theme.GOOD, size=13))
            self._incomplete_btn.setText(" No features with missing data")
            self._incomplete_btn.setStyleSheet(self._chip_qss(theme.GOOD, theme.GOOD_BG))
            self._incomplete_btn.setToolTip(
                "Every habitat feature placed so far has its required data "
                "(this doesn't mean all habitats have been mapped yet).")

        # An open flyout may now be stale.
        if incomplete == 0 and geo == 0 and ov == 0:
            self._close_flyout()

        # A content swap can CHANGE THE FOOTER'S HEIGHT (empty hint ⇄ coverage
        # rows, the data-quality chip appearing). The panel is fixed-height, so
        # without a re-fit Qt squeezes the now-taller footer up OVER the bottom
        # list rows (footer stats printed across the last layer cards). Re-fit
        # only on a real height change, so routine stat refreshes don't close
        # an open flyout via _reposition.
        lay = self._footer.layout()
        if lay is not None:
            lay.activate()
        if self._footer.sizeHint().height() != self._footer.height():
            self._apply_fit_height()

        # The panel floats over the map canvas (a QGraphicsView); a text-only
        # change doesn't repaint until the canvas does (e.g. on zoom), so force
        # it now.
        self._footer.update()
        self.update()

    # ── stat computation ─────────────────────────────────────────────────
    def _areas(self):
        """(site_ha, baseline_area_ha, proposed_area_ha) — real-world areas."""
        from qgis.core import QgsProject, QgsDistanceArea
        proj = QgsProject.instance()
        ctx = proj.transformContext()
        ell = measurement_ellipsoid()

        def area_ha(layer):
            if layer is None or not layer.isValid():
                return 0.0
            calc = QgsDistanceArea()
            try:
                calc.setSourceCrs(layer.crs(), ctx)
                calc.setEllipsoid(ell)
            except Exception:
                pass
            tot = 0.0
            for f in layer.getFeatures():
                g = f.geometry()
                if g is not None and not g.isEmpty():
                    try:
                        tot += calc.measureArea(g)
                    except Exception:
                        pass
            return tot / 10000.0

        def find(name):
            ls = proj.mapLayersByName(name)
            return ls[0] if ls else None

        try:
            from .layer_manager import LAYER_NAMES
            rl, ba, pa = (LAYER_NAMES["redline"], LAYER_NAMES["baseline_area"],
                          LAYER_NAMES["proposed_area"])
        except Exception:
            rl, ba, pa = ("Redline Boundary", "Baseline - Area Habitats",
                          "Proposed - Area Habitats")

        return area_ha(find(rl)), area_ha(find(ba)), area_ha(find(pa))

    _FIELD_LABEL = {"sub_habitat_type": "type", "condition": "condition",
                    "strategic_significance": "significance"}

    def _incomplete_list(self):
        """Return (items, total_features) where each item is
        {layer, fid, type, missing, missing_fields} for one incomplete feature."""
        from qgis.core import QgsProject, QgsVectorLayer
        items = []
        total = 0
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            if lyr.fields().indexOf("sub_habitat_type") < 0:
                continue
            for f in lyr.getFeatures():
                total += 1
                missing = []
                missing_fields = []
                for field in self._REQ_FIELDS:
                    idx = f.fieldNameIndex(field)
                    if idx >= 0 and not str(f[field] or "").strip():
                        missing.append(self._FIELD_LABEL.get(field, field))
                        missing_fields.append(field)
                if missing:
                    ti = f.fieldNameIndex("sub_habitat_type")
                    tval = str(f["sub_habitat_type"] or "").strip() if ti >= 0 else ""
                    items.append({"layer": lyr, "fid": f.id(), "type": tval,
                                  "missing": missing, "missing_fields": missing_fields})
        return items, total

    @staticmethod
    def _only_fields_for(missing_fields):
        """Field rows to show in the trimmed editor for the given missing fields.
        A missing habitat type also opens the category + condition rows, since
        condition depends on the chosen type."""
        only = set()
        for ff in missing_fields:
            if ff == "sub_habitat_type":
                only |= {"habitat_type", "sub_habitat_type", "condition"}
            else:
                only.add(ff)
        return only

    def _geometry_issues(self):
        """Return [{layer, fid, type}] for habitat polygons with INVALID geometry
        (self-intersections, holes outside the shell, slivers that flip a ring
        negative…). These silently zero-out a parcel's area and corrupt the
        metric, so they're flagged ahead of mere missing data. Detection is
        shared with the Summary/Mapping banners and the pre-export guard."""
        try:
            from .measurements import invalid_habitat_geometries
            return [{"layer": b["layer"], "fid": b["fid"], "type": b["sub"],
                     "area_ha": b.get("area_ha")}
                    for b in invalid_habitat_geometries()]
        except Exception:
            return []

    def _overlap_issues(self):
        """Return [{layer, fid_a, fid_b, sub_a, sub_b, ha}] for habitat polygons
        that overlap WITHIN the same layer — the shared ground is counted twice
        in the metric. Detection is shared with measurements so the rule lives in
        one place; this replaces the old standalone Overlaps tool."""
        try:
            from .measurements import overlapping_habitat_polygons
            return overlapping_habitat_polygons()
        except Exception:
            return []

    def _fix_geometry(self, layer, fid):
        """Repair one invalid polygon: make it valid, then drop sliver parts
        (< 1 m²) that are the usual cause. Habitat layers store single-part
        Polygons, so a repair that yields several substantial parts is reduced
        to its largest piece — writing a MultiPolygon into a Polygon column
        would coerce back to an invalid 'hole outside shell'. Only reports
        success after re-reading the stored geometry and confirming it is
        actually valid. Runs through the edit buffer so it's a single undo step."""
        from qgis.core import QgsWkbTypes, QgsDistanceArea, QgsProject
        self._close_flyout()

        def _fail():
            try:
                self.iface.messageBar().pushMessage(
                    "Biome", "Couldn't auto-repair — redraw this parcel instead.",
                    level=Qgis.MessageLevel.Warning, duration=6)
            except Exception:
                pass

        try:
            feat = layer.getFeature(fid)
            g = feat.geometry()
            if g is None or g.isNull():
                return
            da = QgsDistanceArea()
            try:
                da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
                da.setEllipsoid(measurement_ellipsoid())
            except Exception:
                pass
            from .measurements import clean_polygon_geometry
            fixed, dropped = clean_polygon_geometry(g, da=da)
            if fixed is None or fixed.isNull() or fixed.isEmpty():
                _fail()
                return

            # Single-part layer + multi-part repair → keep the largest piece so
            # the write is a valid single Polygon (and tell the user pieces were
            # dropped, in case they were real separate areas to redraw).
            split_dropped = 0
            single_part_layer = (QgsWkbTypes.flatType(layer.wkbType())
                                 == QgsWkbTypes.Type.Polygon)
            parts = [p for p in fixed.asGeometryCollection()
                     if p.type() == QgsWkbTypes.GeometryType.PolygonGeometry
                     and not p.isNull() and not p.isEmpty()]
            if single_part_layer and len(parts) > 1:
                parts.sort(key=lambda p: da.measureArea(p), reverse=True)
                fixed = parts[0]
                split_dropped = len(parts) - 1
            elif len(parts) == 1:
                fixed = parts[0]

            if not fixed.isGeosValid():
                _fail()
                return

            was_editing = layer.isEditable()
            if not was_editing:
                layer.startEditing()
            ok = layer.changeGeometry(fid, fixed)
            committed = True
            if not was_editing:
                if ok:
                    committed = layer.commitChanges()
                else:
                    layer.rollBack()
                    committed = False

            # Re-verify: only claim success if the stored geometry is now valid.
            stored_ok = False
            if ok and committed:
                try:
                    ng = layer.getFeature(fid).geometry()
                    stored_ok = (ng is not None and not ng.isNull()
                                 and not ng.isEmpty() and ng.isGeosValid())
                except Exception:
                    stored_ok = False
            if not stored_ok:
                _fail()
                return

            try:
                layer.triggerRepaint()
                if self._canvas is not None:
                    self._canvas.refresh()
            except Exception:
                pass
            self.refresh_footer()
            # Nudge the main panel so its Summary/Mapping banners update too.
            try:
                panel = self._find_main_panel()
                if panel is not None and hasattr(panel, "_mark_summary_dirty"):
                    panel._mark_summary_dirty()
            except Exception:
                pass
            try:
                bits = []
                if dropped:
                    bits.append("removed %d sliver%s"
                                % (dropped, "" if dropped == 1 else "s"))
                if split_dropped:
                    bits.append("kept the largest of %d pieces — redraw the "
                                "other%s if they were separate areas"
                                % (split_dropped + 1,
                                   "" if split_dropped == 1 else "s"))
                msg = "Repaired geometry" + (" (%s)" % "; ".join(bits) if bits else "")
                self.iface.messageBar().pushMessage(
                    "Biome", msg, level=Qgis.MessageLevel.Warning if split_dropped else Qgis.MessageLevel.Info, duration=5)
            except Exception:
                pass
        except Exception:
            _fail()

    @staticmethod
    def _short_layer(name):
        return (name.replace(" - ", " ").replace(" Habitats", "")
                .replace(" Habitat", "").replace(" Units", ""))

    # ── incomplete-features flyout (hover list, per-row highlight) ─────────
    def _open_flyout(self):
        if self._dead or self._canvas is None:
            return
        geo = self._geometry_issues()
        overlaps = self._overlap_issues()
        items, _total = self._incomplete_list()
        if not geo and not overlaps and not items:
            self._close_flyout()
            return
        self._close_flyout()
        fly = QFrame(self._canvas)
        fly.setObjectName("incFlyout")
        fly.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fly.setStyleSheet(self._flyout_qss())
        fly.setFixedWidth(self._width)
        v = QVBoxLayout(fly)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(2)
        if geo:
            hdr = QLabel("Invalid geometry — hover to locate, click to auto-repair")
            hdr.setObjectName("flyHdr")
            v.addWidget(hdr)
            for it in geo:
                v.addWidget(self._flyout_geo_row(it))
        if overlaps:
            hdr_o = QLabel("Overlapping areas — double-counted, click to select the pair")
            hdr_o.setObjectName("flyHdr")
            v.addWidget(hdr_o)
            for it in overlaps:
                v.addWidget(self._flyout_overlap_row(it))
        if items:
            hdr2 = QLabel("Missing data — hover to locate, click to fix")
            hdr2.setObjectName("flyHdr")
            v.addWidget(hdr2)
            for it in items:
                v.addWidget(self._flyout_row(it))

        def _enter(_e):
            self._flyout_hover = True
        def _leave(_e):
            self._flyout_hover = False
            self._clear_highlight()
            self._schedule_close_flyout()
        fly.enterEvent = _enter
        fly.leaveEvent = _leave

        fly.adjustSize()
        fly.move(self._margin, self._margin + self.height() + 4)
        fly.show()
        fly.raise_()
        self._flyout = fly

    def _flyout_row(self, it):
        row = QFrame()
        row.setObjectName("flyRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        rv = QVBoxLayout(row)
        rv.setContentsMargins(8, 4, 8, 4)
        rv.setSpacing(0)
        t = QLabel(it["type"] or "(no habitat type)")
        t.setObjectName("flyType")
        s = QLabel("%s · missing %s"
                   % (self._short_layer(it["layer"].name()), ", ".join(it["missing"])))
        s.setObjectName("flySub")
        rv.addWidget(t)
        rv.addWidget(s)
        layer, fid = it["layer"], it["fid"]
        only = self._only_fields_for(it.get("missing_fields", []))
        row.enterEvent = lambda _e, l=layer, f=fid: self._highlight_feature(l, f)
        row.leaveEvent = lambda _e: self._clear_highlight()
        row.mousePressEvent = lambda _e, l=layer, f=fid, o=only: self._fix_feature(l, f, o)
        return row

    def _flyout_geo_row(self, it):
        row = QFrame()
        row.setObjectName("flyRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        rv = QVBoxLayout(row)
        rv.setContentsMargins(8, 4, 8, 4)
        rv.setSpacing(0)
        t = QLabel(it["type"] or "(no habitat type)")
        t.setObjectName("flyType")
        a = it.get("area_ha")
        reading = ""
        if a is not None and a < 0:
            reading = " · reading a negative %.3f ha" % abs(a)
        s = QLabel("%s · broken outline%s — click to repair"
                   % (self._short_layer(it["layer"].name()), reading))
        s.setObjectName("flySub")
        rv.addWidget(t)
        rv.addWidget(s)
        layer, fid = it["layer"], it["fid"]
        row.enterEvent = lambda _e, l=layer, f=fid: self._highlight_feature(l, f)
        row.leaveEvent = lambda _e: self._clear_highlight()
        row.mousePressEvent = lambda _e, l=layer, f=fid: self._fix_geometry(l, f)
        return row

    def _flyout_overlap_row(self, it):
        row = QFrame()
        row.setObjectName("flyRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        rv = QVBoxLayout(row)
        rv.setContentsMargins(8, 4, 8, 4)
        rv.setSpacing(0)
        a = it.get("sub_a") or "(no type)"
        b = it.get("sub_b") or "(no type)"
        t = QLabel("%s  ×  %s" % (a, b))
        t.setObjectName("flyType")
        t.setWordWrap(True)   # two habitat names can exceed the flyout width
        s = QLabel("%s · %s ha shared"
                   % (self._short_layer(it["layer"].name()), self._fmt_overlap(it["ha"])))
        s.setObjectName("flySub")
        rv.addWidget(t)
        rv.addWidget(s)
        layer, fa, fb = it["layer"], it["fid_a"], it["fid_b"]
        row.enterEvent = lambda _e, l=layer, x=fa, y=fb: self._highlight_overlap(l, x, y)
        row.leaveEvent = lambda _e: self._clear_highlight()
        row.mousePressEvent = lambda _e, l=layer, x=fa, y=fb: self._show_overlap(l, x, y)
        return row

    @staticmethod
    def _fmt_overlap(ha):
        """Small overlaps round to 0.00 ha; show enough places to stay non-zero."""
        if ha >= 0.01:
            return "%.2f" % ha
        if ha >= 0.0001:
            return "%.4f" % ha
        return "<0.0001"

    def _show_overlap(self, layer, fid_a, fid_b):
        """Zoom to the two overlapping parcels and highlight them temporarily.

        Only actionable overlaps
        (>1 m²) reach this method. They are not auto-fixed because deciding
        which habitat owns meaningful shared ground is a survey judgement;
        subtracting from an arbitrary parcel could change BNG classification."""
        self._close_flyout()
        try:
            from qgis.core import (QgsFeatureRequest, QgsCoordinateTransform,
                                   QgsProject, QgsRectangle)
        except Exception:
            return
        feats = list(layer.getFeatures(
            QgsFeatureRequest().setFilterFids([fid_a, fid_b])))
        bb = None
        for f in feats:
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            gb = g.boundingBox()
            if bb is None:
                bb = QgsRectangle(gb)
            else:
                bb.combineExtentWith(gb)
        canvas = self.iface.mapCanvas()
        if bb is not None and not bb.isEmpty():
            try:
                xform = QgsCoordinateTransform(
                    layer.crs(), canvas.mapSettings().destinationCrs(),
                    QgsProject.instance())
                bb = xform.transformBoundingBox(bb)
            except Exception:
                pass
            bb.scale(1.6)
            canvas.setExtent(bb)
        # Do not use QGIS feature selection here. Its opaque yellow fill
        # persists after the flyout closes and makes the habitat look as though
        # its renderer has broken. Use Biome's temporary outline/fill instead.
        try:
            layer.removeSelection()       # also heals selections from old builds
        except Exception:
            pass
        self._highlight_overlap(layer, fid_a, fid_b)
        QTimer.singleShot(6000, self._clear_highlight)
        try:
            canvas.refresh()
        except Exception:
            pass
        try:
            self.iface.messageBar().pushMessage(
                "Biome",
                "Two overlapping parcels highlighted — reshape or delete one "
                "(Tools ▸ Manage) so the shared area isn't counted twice.",
                level=Qgis.MessageLevel.Warning, duration=6)
        except Exception:
            pass

    def _find_main_panel(self):
        try:
            from qgis.PyQt.QtWidgets import QDockWidget
            return self.iface.mainWindow().findChild(QDockWidget, "BiomePanel")
        except Exception:
            return None

    def _fix_feature(self, layer, fid, only_fields=None):
        """Open the plugin's per-feature editor (trimmed to the missing fields)
        so the gaps can be filled in, after zooming to the feature."""
        self._close_flyout()
        self._zoom_to_feature(layer, fid)
        panel = self._find_main_panel()
        if panel is not None and hasattr(panel, "open_feature_editor"):
            try:
                panel.open_feature_editor(layer, fid, only_fields or None)
            except Exception:
                pass
            self.refresh_footer()
        else:
            try:
                self.iface.messageBar().pushMessage(
                    "Biome", "Open the Biome panel (toolbar) to edit this feature.",
                    level=Qgis.MessageLevel.Info, duration=5)
            except Exception:
                pass

    def _schedule_close_flyout(self):
        QTimer.singleShot(220, self._close_flyout_if_idle)

    def _close_flyout_if_idle(self):
        if not self._pill_hover and not self._flyout_hover:
            self._close_flyout()

    def _close_flyout(self):
        self._clear_highlight()
        if self._flyout is not None:
            try:
                self._flyout.hide()
                self._flyout.deleteLater()
            except Exception:
                pass
            self._flyout = None

    def _make_highlight(self, layer, fid, line_rgb, fill_rgba):
        """Build (and show) one transient QgsHighlight for a feature, or None."""
        try:
            from qgis.gui import QgsHighlight
            from qgis.core import QgsFeatureRequest
            from qgis.PyQt.QtGui import QColor
        except Exception:
            return None
        feats = list(layer.getFeatures(QgsFeatureRequest().setFilterFid(fid)))
        if not feats:
            return None
        try:
            h = QgsHighlight(self.iface.mapCanvas(), feats[0], layer)
            h.setColor(QColor(*line_rgb))
            h.setWidth(5)
            try:
                h.setFillColor(QColor(*fill_rgba))
            except Exception:
                pass
            h.show()
            return h
        except Exception:
            return None

    def _highlight_feature(self, layer, fid):
        self._clear_highlight()
        h = self._make_highlight(layer, fid, (0x9A, 0x67, 0x00), (0xF5, 0x9E, 0x0B, 80))
        if h is not None:
            self._hls.append(h)

    def _highlight_overlap(self, layer, fid_a, fid_b):
        """Highlight both parcels of an overlapping pair in red, so the user can
        see exactly which two features double-count the shared ground."""
        self._clear_highlight()
        for fid in (fid_a, fid_b):
            h = self._make_highlight(layer, fid, (0xB3, 0x37, 0x2F), (0xB3, 0x37, 0x2F, 70))
            if h is not None:
                self._hls.append(h)

    def _clear_highlight(self):
        for h in self._hls:
            try:
                h.hide()
            except Exception:
                pass
        self._hls = []

    def _zoom_to_feature(self, layer, fid):
        try:
            from qgis.core import (QgsFeatureRequest, QgsCoordinateTransform,
                                   QgsProject, QgsRectangle)
        except Exception:
            return
        feats = list(layer.getFeatures(QgsFeatureRequest().setFilterFid(fid)))
        if not feats:
            return
        g = feats[0].geometry()
        if g is None or g.isEmpty():
            return
        canvas = self.iface.mapCanvas()
        bb = g.boundingBox()
        try:
            xform = QgsCoordinateTransform(layer.crs(),
                                           canvas.mapSettings().destinationCrs(),
                                           QgsProject.instance())
            bb = xform.transformBoundingBox(bb)
        except Exception:
            pass
        if bb.width() < 1e-6 and bb.height() < 1e-6:
            c = bb.center()
            bb = QgsRectangle(c.x() - 60, c.y() - 60, c.x() + 60, c.y() + 60)
        else:
            bb.scale(1.6)
        canvas.setExtent(bb)
        canvas.refresh()

    def _flyout_qss(self):
        t = theme
        return ("QFrame#incFlyout { background:%s; border:1px solid %s;"
                " border-radius:9px; }"
                "QLabel#flyHdr { color:%s; font-size:10px; font-family:%s;"
                " background:transparent; padding:1px 2px 3px; }"
                "QFrame#flyRow { background:transparent; border-radius:6px; }"
                "QFrame#flyRow:hover { background:%s; }"
                "QLabel#flyType { color:%s; font-size:12px; font-family:%s;"
                " background:transparent; }"
                "QLabel#flySub { color:%s; font-size:10px; background:transparent; }"
                % (t.CARD, t.BORDER_STRONG, t.FAINT, t.FONT_MEDIUM,
                   t.WARN_BG, t.INK, t.FONT_MEDIUM, t.MUTED))

    # ── styling ──────────────────────────────────────────────────────────
    def _qss(self):
        t = theme
        return theme.dock_qss() + f"""
QFrame#biomeLayersOverlay {{ background: {t.SUNKEN};
    border: 1px solid {t.BORDER_STRONG}; border-radius: 10px; }}
QWidget#overlayHeader {{ background: {t.ACCENT_SOFT}; border: none;
    border-top-left-radius: 10px; border-top-right-radius: 10px;
    border-bottom: 1px solid {t.BORDER}; }}
QLabel#overlayTitle {{ font-family: {t.FONT_SEMIBOLD}; font-size: 13px;
    color: {t.ACCENT_DARK}; background: transparent; }}
QLabel#biomeLayersEmpty {{ color: {t.FAINT}; font-size: 12px; padding: 18px 8px;
    background: transparent; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border: none; padding: 0; margin: 0; background: transparent; }}
QListWidget::item:selected {{ background: transparent; }}
/* Each layer is its own rounded card (Apple-style), inset by the list spacing
   so nothing reaches — and pokes past — the panel's rounded corners. */
QFrame#trunkCard {{ background: {t.CARD};
    border: 1px solid {t.BORDER_STRONG}; border-radius: 9px; }}
QFrame#trunkCard:hover {{ border-color: {t.ACCENT}; }}
QWidget#trunkHeader {{ background: transparent; border: none; }}
QFrame#cardDivider {{ background: {t.BORDER}; border: none; }}
QFrame#dropSlot {{ background: {t.ACCENT_SOFT};
    border: 2px dashed {t.ACCENT}; border-radius: 9px; }}
QWidget#childWrap {{ background: transparent; border: none; }}
QLabel#trunkName {{ font-family: {t.FONT_MEDIUM}; font-size: 13px; color: {t.INK};
                    background: transparent; }}
QLabel#childName {{ font-size: 12px; color: {t.MUTED}; background: transparent; }}
QToolButton[biomeIconBtn="true"] {{
    background: transparent; border: none; border-radius: 5px; }}
QToolButton[biomeIconBtn="true"]:hover {{ background: {t.SUNKEN}; }}
QToolButton#deleteBtn:hover {{ background: {t.BAD_BG}; }}
QToolButton#collapseBtn:hover {{ background: rgba(255,255,255,0.45); }}
QFrame#statsFooter {{ background: transparent; border: none;
    border-top: 1px solid {t.BORDER_STRONG}; }}
QLabel#statKey {{ color: {t.MUTED}; font-size: 11px; font-family: {t.FONT_MEDIUM};
    background: transparent; }}
QLabel#statVal {{ color: {t.INK}; font-size: 13px; font-family: {t.FONT_SEMIBOLD};
    background: transparent; }}
QLabel#covKey {{ color: {t.INK}; font-size: 11px; font-family: {t.FONT_MEDIUM};
    background: transparent; }}
QLabel#covVal {{ color: {t.MUTED}; font-size: 11px; background: transparent; }}
QFrame#barTrack {{ background: {t.BORDER}; border-radius: 3px; }}
"""

    # ── signal wiring ────────────────────────────────────────────────────
    def _connect_signals(self):
        root = QgsProject.instance().layerTreeRoot()
        # Visibility changes never alter structure — just resync the eye icons.
        root.visibilityChanged.connect(self._on_visibility_changed)
        # Structural changes (add / remove / reorder) → full rebuild (debounced).
        root.addedChildren.connect(self._schedule_rebuild)
        root.removedChildren.connect(self._schedule_rebuild)
        try:
            QgsProject.instance().layersAdded.connect(self._schedule_rebuild)
            QgsProject.instance().layersRemoved.connect(self._schedule_rebuild)
        except Exception:
            pass

    def teardown(self):
        """Disconnect every signal and detach from the canvas before deletion,
        so nothing fires into a destroyed C++ object."""
        self._dead = True
        self._close_flyout()
        try:
            if self._canvas is not None and self._filter_installed:
                self._canvas.removeEventFilter(self)
                self._filter_installed = False
        except Exception:
            pass
        try:
            root = QgsProject.instance().layerTreeRoot()
            root.visibilityChanged.disconnect(self._on_visibility_changed)
            root.addedChildren.disconnect(self._schedule_rebuild)
            root.removedChildren.disconnect(self._schedule_rebuild)
        except Exception:
            pass
        try:
            QgsProject.instance().layersAdded.disconnect(self._schedule_rebuild)
            QgsProject.instance().layersRemoved.disconnect(self._schedule_rebuild)
        except Exception:
            pass
        self._unwire_layer_signals()

    def refresh_now(self):
        if self._dead:
            return
        self._rebuild()

    def _schedule_rebuild(self, *args):
        if self._dead or self._suppress or self._rebuild_pending:
            return
        self._rebuild_pending = True
        QTimer.singleShot(0, self._do_rebuild)

    def _do_rebuild(self):
        self._rebuild_pending = False
        if self._dead:
            return
        if self._list._dragging:
            # A row drag is in flight (drag.exec runs its own event loop, so
            # this timer can fire mid-drag). Rebuilding now would clear the
            # list under the drag and strand its stale row indices — retry
            # once the drag is over.
            self._rebuild_pending = True
            QTimer.singleShot(120, self._do_rebuild)
            return
        try:
            self._rebuild()
        except Exception as exc:
            try:
                from qgis.core import QgsMessageLog, Qgis
                import traceback
                QgsMessageLog.logMessage(
                    "Biome layer panel rebuild failed: " + repr(exc) + "\n"
                    + traceback.format_exc(), "Biome", Qgis.MessageLevel.Warning)
            except Exception:
                pass

    # ── feature-level refresh (coverage / data-quality go stale on edits) ──
    # The layer tree only fires on whole-layer add/remove/reorder, so digitising
    # or editing features inside a layer never reached the footer. Listen to
    # these per-layer signals and refresh the stats (debounced).
    _FEATURE_SIGNALS = ("featureAdded", "featureDeleted", "geometryChanged",
                        "afterCommitChanges")

    def _stat_layers(self):
        from qgis.core import QgsProject, QgsVectorLayer
        out = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            if (lyr.fields().indexOf("sub_habitat_type") >= 0
                    or "redline" in lyr.name().lower()):
                out.append(lyr)
        return out

    def _wire_layer_signals(self):
        if self._dead:
            return
        for lyr in self._stat_layers():
            lid = lyr.id()
            if lid in self._wired_layers:
                continue
            for sig in self._FEATURE_SIGNALS:
                try:
                    getattr(lyr, sig).connect(self._schedule_footer)
                except Exception:
                    pass
            self._wired_layers.add(lid)

    def _unwire_layer_signals(self):
        from qgis.core import QgsProject
        for lid in list(self._wired_layers):
            lyr = QgsProject.instance().mapLayer(lid)
            if lyr is None:
                continue
            for sig in self._FEATURE_SIGNALS:
                try:
                    getattr(lyr, sig).disconnect(self._schedule_footer)
                except Exception:
                    pass
        self._wired_layers.clear()

    def _schedule_footer(self, *args):
        if self._dead or self._footer_pending:
            return
        self._footer_pending = True
        QTimer.singleShot(80, self._do_footer)

    def _do_footer(self):
        self._footer_pending = False
        if self._dead:
            return
        self.refresh_footer()

    # ── node lookup / classification ─────────────────────────────────────
    @staticmethod
    def _descriptor_of(node):
        if isinstance(node, QgsLayerTreeLayer):
            return ("layer", node.layerId())
        return ("group", node.name())

    @staticmethod
    def _resolve(desc):
        """Re-resolve a descriptor against the CURRENT tree, so stored rows
        never hold a dangling node pointer across rebuilds."""
        root = QgsProject.instance().layerTreeRoot()
        kind, key = desc
        if kind == "layer":
            return root.findLayer(key)
        return root.findGroup(key)

    @staticmethod
    def _is_protected(node):
        if isinstance(node, QgsLayerTreeGroup):
            return node.name() in CORE_GROUPS
        if isinstance(node, QgsLayerTreeLayer):
            return node.name() in CORE_LAYERS
        return False

    # ── build ────────────────────────────────────────────────────────────
    def _rebuild(self):
        self._building = True
        self._eye_rows = []
        self._list.clear()

        root = QgsProject.instance().layerTreeRoot()
        children = list(root.children())
        self._empty.setVisible(not self._collapsed and not children)
        self._list.setVisible(not self._collapsed and bool(children))

        for node in children:
            widget = self._build_trunk(node)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, self._descriptor_of(node))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
            row_h = max(widget.sizeHint().height(), widget.minimumSizeHint().height())
            item.setSizeHint(QSize(10, row_h))
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)

        self._building = False
        self._footer.setVisible(not self._collapsed)
        self.refresh_footer()
        self._apply_fit_height()
        # Freshly built cards can report a stale sizeHint for a beat (seen
        # under Qt6 on the rebuild after the first habitat placement), which
        # left the panel a row short until a resize re-ran the maths. Re-measure
        # once the event loop has settled.
        QTimer.singleShot(0, self._refit_settled)
        QTimer.singleShot(150, self._refit_settled)

    def _refit_settled(self):
        """Re-measure every row card with its layout settled, update the item
        size hints if they changed, and re-apply the fit height."""
        if self._dead or self._building:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            w = self._list.itemWidget(item)
            if w is None:
                continue
            lay = w.layout()
            if lay is not None:
                lay.activate()
            row_h = max(w.sizeHint().height(), w.minimumSizeHint().height())
            if item.sizeHint().height() != row_h:
                item.setSizeHint(QSize(10, row_h))
        self._apply_fit_height()

    def _icon_button(self, icon_name, color, tip, size=16, obj_name=None):
        btn = QToolButton()
        btn.setProperty("biomeIconBtn", True)
        if obj_name:
            btn.setObjectName(obj_name)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(26, 26)
        btn.setIcon(theme.icon(icon_name, color=color, size=size))
        btn.setIconSize(QSize(size, size))
        btn.setToolTip(tip)
        btn.setAutoRaise(True)
        return btn

    def _make_eye(self, desc, checked):
        btn = QToolButton()
        btn.setProperty("biomeIconBtn", True)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(26, 26)
        btn.setIconSize(QSize(16, 16))
        btn.setAutoRaise(True)
        self._paint_eye(btn, checked)
        btn.toggled.connect(lambda on, d=desc, b=btn: self._on_eye(d, on, b))
        self._eye_rows.append((desc, btn))
        return btn

    @staticmethod
    def _paint_eye(btn, checked):
        if checked:
            btn.setIcon(theme.icon("eye", color=theme.ACCENT, size=16))
            btn.setToolTip("Visible — click to hide")
        else:
            btn.setIcon(theme.icon("eye-off", color=theme.FAINT, size=16))
            btn.setToolTip("Hidden — click to show")

    def _build_trunk(self, node):
        is_group = isinstance(node, QgsLayerTreeGroup)
        name = node.name()
        is_dropdown = is_group and name in DROPDOWN_GROUPS
        protected = self._is_protected(node)
        desc = self._descriptor_of(node)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Each row is a rounded card; the header lives inside it (transparent),
        # so nothing square reaches the card's — or the panel's — rounded corners.
        card = QFrame()
        card.setObjectName("trunkCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        header = QWidget()
        header.setObjectName("trunkHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(6, 0, 6, 0)
        h.setSpacing(2)
        header.setFixedHeight(34)

        grip = QLabel()
        grip.setPixmap(theme.icon("grip-vertical", color=theme.FAINT, size=16).pixmap(16, 16))
        grip.setFixedWidth(16)
        grip.setToolTip("Drag to reorder")
        grip.setStyleSheet("background: transparent;")
        h.addWidget(grip)

        h.addWidget(self._make_eye(desc, node.itemVisibilityChecked()))

        if is_dropdown:
            chev = QToolButton()
            chev.setProperty("biomeIconBtn", True)
            chev.setCursor(Qt.CursorShape.PointingHandCursor)
            chev.setFixedSize(22, 26)
            chev.setAutoRaise(True)
            expanded = self._expanded.get(name, False)
            chev.setIcon(theme.icon("chevron-down" if expanded else "chevron-right",
                                    color=theme.MUTED, size=14))
            chev.setIconSize(QSize(14, 14))
            chev.setToolTip("Collapse" if expanded else "Expand")
            chev.clicked.connect(lambda _=False, n=name: self._toggle_expand(n))
            h.addWidget(chev)

        lbl = QLabel(display_layer_name(name))
        lbl.setObjectName("trunkName")   # same colour for every layer
        h.addWidget(lbl, 1)

        if not protected:
            dele = self._icon_button("trash", theme.BAD, "Delete layer",
                                     size=16, obj_name="deleteBtn")
            dele.clicked.connect(lambda _=False, d=desc: self._on_delete(d))
            h.addWidget(dele)

        cv.addWidget(header)

        if is_dropdown and self._expanded.get(name, False):
            div = QFrame()
            div.setObjectName("cardDivider")
            div.setFixedHeight(1)
            cv.addWidget(div)
            cv.addWidget(self._build_children(node))

        v.addWidget(card)
        return container

    def _build_children(self, group):
        wrap = QWidget()
        wrap.setObjectName("childWrap")
        cv = QVBoxLayout(wrap)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # findLayers() includes layers inside nested subgroups. This makes the
        # Ecological Features dropdown show Point/Linear/Area layers and the
        # FOV-label layer while preserving the native tree's useful subgroups.
        child_nodes = list(group.findLayers())
        child_nodes.sort(key=lambda n: _child_rank(n.name()))

        if not child_nodes:
            empty = QLabel("    (no sublayers yet)")
            empty.setObjectName("childName")
            empty.setFixedHeight(26)
            cv.addWidget(empty)
            return wrap

        for ln in child_nodes:
            row = QWidget()
            rh = QHBoxLayout(row)
            rh.setContentsMargins(22, 0, 6, 0)
            rh.setSpacing(2)
            row.setFixedHeight(28)
            desc = ("layer", ln.layerId())
            rh.addWidget(self._make_eye(desc, ln.itemVisibilityChecked()))
            name_lbl = QLabel(_child_label(ln.name()))
            name_lbl.setObjectName("childName")
            rh.addWidget(name_lbl, 1)
            cv.addWidget(row)
        return wrap

    # ── actions ──────────────────────────────────────────────────────────
    def _on_eye(self, desc, on, btn):
        node = self._resolve(desc)
        if node is None:
            return
        self._suppress += 1
        try:
            node.setItemVisibilityChecked(on)
        finally:
            self._suppress -= 1
        self._paint_eye(btn, on)

    def _on_visibility_changed(self, *args):
        """Light resync — only repaints eye icons, never rebuilds."""
        if self._building:
            return
        for desc, btn in list(self._eye_rows):
            node = self._resolve(desc)
            if node is None:
                continue
            checked = node.itemVisibilityChecked()
            if btn.isChecked() != checked:
                btn.blockSignals(True)
                btn.setChecked(checked)
                btn.blockSignals(False)
            self._paint_eye(btn, checked)

    def _toggle_expand(self, name):
        self._expanded[name] = not self._expanded.get(name, False)
        self._rebuild()

    def _on_delete(self, desc):
        node = self._resolve(desc)
        if node is None or self._is_protected(node):
            return
        name = node.name()
        warning_kind = self._delete_warning_kind(node)
        if warning_kind:
            if warning_kind == "standing_water":
                detail = (
                    "Placed standing-water markers will be removed from the map "
                    "and may need to be recreated.")
            else:
                detail = (
                    "The georeferenced layer will be removed from the map. Its "
                    "source file on disk will not be deleted.")
            reply = QMessageBox.question(
                self, "Remove layer",
                f"Remove “{display_layer_name(name)}” from the project?\n\n"
                + detail,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            # Re-resolve after the modal dialog (its event loop may have moved
            # things). Immediate deletions do not need this extra lookup.
            node = self._resolve(desc)
            if node is None:
                self.refresh_now()
                return

        proj = QgsProject.instance()
        if isinstance(node, QgsLayerTreeGroup):
            layer_ids = [n.layerId() for n in node.findLayers()]
        else:
            layer_ids = [node.layerId()]

        self._suppress += 1
        try:
            # Remove the tree node ourselves FIRST (synchronous) so the row
            # disappears now. QGIS's registry→tree bridge removes nodes lazily,
            # which is why the deleted layer lingered until the next change.
            parent = node.parent()
            if parent is not None:
                parent.removeChildNode(node)
            if layer_ids:
                proj.removeMapLayers(layer_ids)
        finally:
            self._suppress -= 1
        self.refresh_now()
        # Force the map to redraw now — otherwise the deleted layer's features
        # stay painted on the canvas until the next zoom/pan triggers a repaint.
        try:
            self.iface.mapCanvas().refresh()
        except Exception:
            pass

    @staticmethod
    def _delete_warning_kind(node):
        """Return the only deletion types that warrant confirmation.

        Desk-study results, buffers, and other generated layers are cheap to
        reload and delete immediately. Placed standing-water markers contain
        user input, while local GDAL rasters are typically georeferenced plans
        or imagery, so those two cases retain a warning.
        """
        if isinstance(node, QgsLayerTreeGroup):
            layers = [n.layer() for n in node.findLayers() if n.layer()]
        else:
            layer = node.layer() if isinstance(node, QgsLayerTreeLayer) else None
            layers = [layer] if layer is not None else []

        for layer in layers:
            if (layer.name() == "Water Body"
                    or str(layer.customProperty("biome_gpkg_table", ""))
                    == "water_bodies"):
                return "standing_water"
        for layer in layers:
            if (isinstance(layer, QgsRasterLayer)
                    and layer.providerType().lower() == "gdal"):
                return "georeferenced"
        return None

    def _move_node(self, desc, insert_index):
        """Move the dragged node to `insert_index` among the top-level nodes.
        Moves a single node — never touches the others — so an unrelated layer
        can never be dropped from the tree."""
        node = self._resolve(desc)
        if node is None:
            self._schedule_rebuild()
            return
        root = QgsProject.instance().layerTreeRoot()
        insert_index = max(0, min(insert_index, len(root.children())))
        self._suppress += 1
        try:
            clone = node.clone()
            if clone is not None:
                # Insert the copy first, then drop the original by reference —
                # a failure then leaves a (self-healing) duplicate, never a hole.
                root.insertChildNode(insert_index, clone)
                root.removeChildNode(node)
        except Exception as exc:
            try:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage("Biome layer reorder failed: " + repr(exc),
                                         "Biome", Qgis.MessageLevel.Warning)
            except Exception:
                pass
        finally:
            self._suppress -= 1
        self._schedule_rebuild()

    # ── height: always tall enough to show every layer (never scrolls) ────
    def _apply_fit_height(self):
        if self._dead:
            return
        if self._collapsed:
            # Folded down to just the header bar.
            self._content_h = self._header_h + 2
            self._reposition()
            return
        count = self._list.count()
        sp = self._list.spacing()
        total = sum(self._list.item(i).sizeHint().height() for i in range(count))
        # QListView pads every item by `sp` on ALL sides, so the real cost is
        # 2*sp per item (top + bottom), not sp*(count+1) — that undercount was
        # clipping the last card.
        total += 2 * sp * count
        if count == 0:
            total = 56
        total += 2 * self._list.frameWidth() + 4   # frame + small safety buffer

        # Measure the footer with its layout settled, so its height isn't
        # under-counted right after a visibility/content change.
        lay = self._footer.layout()
        if lay is not None:
            lay.activate()
        footer_h = self._footer.sizeHint().height()

        # The stats footer always stays visible; the layer list scrolls only
        # when there genuinely isn't room for every card AND the footer.
        canvas_h = self._canvas.height() if self._canvas is not None else 800
        avail = max(120, canvas_h - self._header_h - footer_h - 2 * self._margin - 8)
        if total > avail:
            self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            list_h = avail
        else:
            self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            list_h = total
        self._list.setFixedHeight(list_h)
        self._content_h = self._header_h + list_h + footer_h + 4
        self._reposition()
