# quick_habitats_dialog.py
"""Editor for the hub's "Quick Habitats" list.

Opened from Biome settings → Quick Habitats. Lets the user pick which habitats
are pinned under "Quick Habitats" on the Baseline / Proposed screens, reorder
them (drag, or the ▲▼ buttons) and drop the ones they never place. The panel
owns persistence (QSettings) and rebuilds the hub on save; this dialog is pure
UI over plain {habitat_key, broad, sub} dicts.
"""

from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QListWidget, QListWidgetItem, QAbstractItemView,
)


class QuickHabitatsDialog(QDialog):
    """Two-pane favourites editor. `result_items()` returns the chosen list,
    in order, as {habitat_key, broad, sub} dicts."""

    _ROLE = Qt.ItemDataRole.UserRole
    _CAP = 50   # cap catalogue rows shown at once (keeps icon rendering snappy)

    def __init__(self, parent, catalogue, current, defaults, icon_fn):
        super().__init__(parent)
        self._catalogue = catalogue        # [{habitat_key, broad, sub}] — all placeable
        self._defaults = defaults          # [{habitat_key, broad, sub}]
        self._icon_fn = icon_fn            # (sub, broad, habitat_key) -> QIcon
        self.setWindowTitle("Quick Habitats")
        self.setMinimumSize(680, 500)
        self._build_ui()
        self._load(current)
        self._render_catalogue("")

    # ── item helpers ──────────────────────────────────────────────────────
    # Identity is stored on each item as a delimited STRING, not a Python dict:
    # QListWidget's InternalMove drag-reorder round-trips item data through a
    # QDataStream, which silently drops arbitrary Python objects but preserves
    # strings — so a reorder-then-save can never read back a null identity.
    _SEP = "\x1f"   # unit separator — never appears in a habitat name

    def _encode(self, d):
        return self._SEP.join((d["habitat_key"], d["broad"], d["sub"]))

    def _decode(self, item):
        parts = (item.data(self._ROLE) or "").split(self._SEP)
        if len(parts) != 3:
            return None
        return {"habitat_key": parts[0], "broad": parts[1], "sub": parts[2]}

    @staticmethod
    def _key(d):
        return (d["habitat_key"], d["broad"], d["sub"])

    def _make_item(self, d):
        it = QListWidgetItem(d["sub"])
        it.setData(self._ROLE, self._encode(d))
        it.setToolTip(d["broad"])
        try:
            it.setIcon(self._icon_fn(d["sub"], d["broad"], d["habitat_key"]))
        except Exception:
            pass
        return it

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        intro = QLabel(
            "Choose the habitats pinned under <b>Quick Habitats</b> on the "
            "Baseline and Proposed screens. Double-click a habitat on the right "
            "to add it; drag or use ▲▼ to reorder.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#54635A; font-size:11px;")
        outer.addWidget(intro)

        cols = QHBoxLayout()
        cols.setSpacing(12)

        # left — current favourites (reorderable)
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._col_label("Your quick habitats"))
        self.fav_list = QListWidget()
        self.fav_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.fav_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.fav_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.fav_list.setIconSize(QSize(24, 24))
        self.fav_list.setStyleSheet(self._list_qss())
        left.addWidget(self.fav_list, 1)
        lb = QHBoxLayout()
        self.up_btn = QPushButton("▲")
        self.down_btn = QPushButton("▼")
        self.remove_btn = QPushButton("Remove")
        self.reset_btn = QPushButton("Restore defaults")
        for b in (self.up_btn, self.down_btn, self.remove_btn, self.reset_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(self._ghost_qss())
        self.up_btn.setFixedWidth(34)
        self.down_btn.setFixedWidth(34)
        lb.addWidget(self.up_btn)
        lb.addWidget(self.down_btn)
        lb.addWidget(self.remove_btn)
        lb.addStretch(1)
        lb.addWidget(self.reset_btn)
        left.addLayout(lb)
        cols.addLayout(left, 1)

        # right — searchable catalogue
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(self._col_label("Add from all habitats"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search habitats…")
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            "QLineEdit{background:#FFFFFF;border:1px solid #C9D1C9;border-radius:8px;"
            "padding:6px 9px;font-size:12px;} QLineEdit:focus{border:1px solid #2E6B4F;}")
        right.addWidget(self.search)
        self.all_list = QListWidget()
        self.all_list.setIconSize(QSize(24, 24))
        self.all_list.setStyleSheet(self._list_qss())
        right.addWidget(self.all_list, 1)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color:#8A8F88; font-size:10px;")
        right.addWidget(self.count_lbl)
        cols.addLayout(right, 1)

        outer.addLayout(cols, 1)

        # bottom — Cancel / Save
        bb = QHBoxLayout()
        bb.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.save_btn = QPushButton("Save")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet(self._ghost_qss())
        self.save_btn.setStyleSheet(
            "QPushButton{background:#1F4A37;color:white;border:none;border-radius:6px;"
            "padding:6px 16px;font-weight:600;} QPushButton:hover{background:#27654b;}")
        self.save_btn.setDefault(True)
        bb.addWidget(self.cancel_btn)
        bb.addWidget(self.save_btn)
        outer.addLayout(bb)

        # wire up
        self.search.textChanged.connect(self._render_catalogue)
        self.all_list.itemDoubleClicked.connect(self._add_from_catalogue)
        self.fav_list.itemDoubleClicked.connect(self._remove_item)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.remove_btn.clicked.connect(self._remove_selected)
        self.reset_btn.clicked.connect(self._restore_defaults)
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self.accept)

    @staticmethod
    def _col_label(text):
        l = QLabel(text)
        l.setStyleSheet("font-size:12px; font-weight:600; color:#1F4A37;")
        return l

    @staticmethod
    def _list_qss():
        return ("QListWidget{background:#FFFFFF;border:1px solid #D9DED7;"
                "border-radius:8px;padding:3px;font-size:12px;}"
                "QListWidget::item{padding:4px;border-radius:5px;}"
                "QListWidget::item:selected{background:#EAF2EC;color:#12362A;}")

    @staticmethod
    def _ghost_qss():
        return ("QPushButton{background:transparent;color:#54635A;"
                "border:1px solid #C9D1C9;border-radius:6px;padding:5px 12px;}"
                "QPushButton:hover{background:#EAF2EC;}")

    # ── data operations ───────────────────────────────────────────────────
    def _load(self, items):
        self.fav_list.clear()
        for d in items:
            self.fav_list.addItem(self._make_item(d))

    def _current_keys(self):
        keys = set()
        for i in range(self.fav_list.count()):
            d = self._decode(self.fav_list.item(i))
            if d:
                keys.add(self._key(d))
        return keys

    def _render_catalogue(self, text=""):
        q = (text or "").strip().lower()
        self.all_list.clear()
        shown = matched = 0
        for d in self._catalogue:
            if q and q not in d["sub"].lower() and q not in d["broad"].lower():
                continue
            matched += 1
            if shown >= self._CAP:
                continue
            self.all_list.addItem(self._make_item(d))
            shown += 1
        if matched == 0:
            self.count_lbl.setText("No habitats match.")
        elif matched > shown:
            self.count_lbl.setText(
                "Showing %d of %d — keep typing to narrow." % (shown, matched))
        else:
            self.count_lbl.setText(
                "%d habitat%s" % (matched, "" if matched == 1 else "s"))

    def _add_from_catalogue(self, item):
        d = self._decode(item)
        if not d:
            return
        key = self._key(d)
        # Already pinned → just select it rather than duplicate.
        if key in self._current_keys():
            for i in range(self.fav_list.count()):
                e = self._decode(self.fav_list.item(i))
                if e and self._key(e) == key:
                    self.fav_list.setCurrentRow(i)
                    return
            return
        new = self._make_item(d)
        self.fav_list.addItem(new)
        self.fav_list.setCurrentItem(new)

    def _remove_item(self, item):
        self.fav_list.takeItem(self.fav_list.row(item))

    def _remove_selected(self):
        for it in self.fav_list.selectedItems():
            self.fav_list.takeItem(self.fav_list.row(it))

    def _move(self, delta):
        row = self.fav_list.currentRow()
        if row < 0:
            return
        new = row + delta
        if new < 0 or new >= self.fav_list.count():
            return
        it = self.fav_list.takeItem(row)
        self.fav_list.insertItem(new, it)
        self.fav_list.setCurrentRow(new)

    def _restore_defaults(self):
        self._load(self._defaults)

    def result_items(self):
        out = []
        for i in range(self.fav_list.count()):
            d = self._decode(self.fav_list.item(i))
            if d:
                out.append(d)
        return out
