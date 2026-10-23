"""Non-georeferenced elevation and plan annotation tool.

The canvas deliberately lives outside the QGIS map: architectural elevations
are page graphics, not spatial layers.  SVG enhancement symbols stay vector
sharp while the imported drawing supplies the export dimensions.
"""

import copy
import os

from qgis.PyQt.QtCore import Qt, QRectF, QSize, QLineF, QPointF, QSettings
from qgis.PyQt.QtGui import (QColor, QFont, QIcon, QImage, QPainter, QPixmap,
                            QPen, QBrush, QPolygonF, QKeySequence, QPainterPath)
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem,
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsTextItem, QGraphicsView, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSlider, QSpinBox,
    QVBoxLayout, QWidget, QDialogButtonBox, QGridLayout,
    QScrollArea, QTabWidget, QToolButton, QColorDialog, QDoubleSpinBox,
)

try:
    from qgis.PyQt.QtSvgWidgets import QGraphicsSvgItem
except ImportError:  # Qt5 fallback
    from qgis.PyQt.QtSvg import QGraphicsSvgItem
from qgis.PyQt.QtSvg import QSvgRenderer


_SYMBOLS = (
    ("Swift Box", "swift_box.svg"),
    ("Bird Box", "bird_box.svg"),
    ("Bat Box", "bat_box.svg"),
)

_DEFAULT_PRESETS = ("Swift Box", "Bird Box", "Bat Box", "PRF")
_PRESET_KEY = "biome/plan_annotator/presets"
_HANDLE_BORDER = "#17633F"
_HANDLE_FILL = "#CFE7D8"


def ecological_feature_names():
    try:
        from .gridref_dialog import FEATURE_GROUPS_FLAT, load_custom_features
        names = []
        for _group, group_names in FEATURE_GROUPS_FLAT:
            for name in group_names:
                if name not in names:
                    names.append(name)
        for name in load_custom_features().get("points", {}):
            if name not in names:
                names.append(name)
        return names
    except Exception:
        return list(_DEFAULT_PRESETS)


def _feature_catalogue():
    """Return the point-feature catalogue in the same groups as Feature Mapper."""
    try:
        from .gridref_dialog import FEATURE_GROUPS_FLAT, load_custom_features
        groups = []
        seen = set()
        for label, names in FEATURE_GROUPS_FLAT:
            unique = []
            for name in names:
                if name not in seen:
                    seen.add(name)
                    unique.append(name)
            if unique:
                groups.append((label.split("  ", 1)[-1], unique))
        custom = [name for name in load_custom_features().get("points", {})
                  if name not in seen]
        if custom:
            groups.append(("Custom", custom))
        return groups
    except Exception:
        return [("Enhancements", list(_DEFAULT_PRESETS))]


def _feature_svg_path(name):
    try:
        from .gridref_dialog import FEATURE_SVG, load_custom_features
        custom = load_custom_features().get("points", {}).get(name, {})
        filename = custom.get("svg") or FEATURE_SVG.get(name)
    except Exception:
        filename = dict(_SYMBOLS).get(name)
    if not filename:
        return None
    if os.path.isabs(filename):
        return filename if os.path.exists(filename) else None
    base = os.path.dirname(__file__)
    for folder in ("Symbols", "symbols"):
        path = os.path.join(base, folder, filename)
        if os.path.exists(path):
            return path
    return None


def _feature_icon(name, size=42):
    path = _feature_svg_path(name)
    if path:
        renderer = QSvgRenderer(path)
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter, QRectF(1, 1, size - 2, size - 2))
        painter.end()
        return QIcon(QPixmap.fromImage(image))
    try:
        from .gridref_dialog import FEATURE_EMOJI
        emoji = FEATURE_EMOJI.get(name, "")
    except Exception:
        emoji = ""
    if not emoji:
        return QIcon()
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    font = QFont("Segoe UI Emoji")
    font.setPixelSize(int(size * .66))
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, emoji)
    painter.end()
    return QIcon(QPixmap.fromImage(image))


class _EcologicalFeaturePicker(QDialog):
    """Visual, searchable point-feature picker shared by the annotator and Settings."""
    def __init__(self, parent=None, selected=None, multiple=False):
        super().__init__(parent)
        self.multiple = multiple
        self.selected = set(selected or [])
        self.chosen_name = None
        self._buttons = {}
        self.setWindowTitle("Choose annotator shortcuts" if multiple
                            else "Add an ecological feature")
        self.resize(820, 610)
        self.setMinimumSize(650, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(9)
        title = QLabel("Choose ecological features" if multiple
                       else "Ecological features")
        title.setStyleSheet("font-size:16px;font-weight:600;color:#1D3027;")
        root.addWidget(title)
        intro = QLabel(
            "Select the features to keep as shortcuts in the annotator."
            if multiple else
            "Choose a feature to add to the plan, elevation or photograph.")
        intro.setStyleSheet("color:#5E6E65;")
        root.addWidget(intro)
        self._search = QLineEdit()
        self._search.setClearButtonEnabled(True)
        self._search.setPlaceholderText("Search features — e.g. PRF, swift box, badger…")
        self._search.textChanged.connect(self._filter)
        root.addWidget(self._search)
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            "QTabWidget::pane{border:none;background:#F7FAF8;top:3px;}"
            "QTabBar::tab{background:#FFFFFF;color:#53645B;border:1px solid #D8E0DA;"
            "border-radius:7px;padding:6px 11px;margin:2px 4px 4px 0;}"
            "QTabBar::tab:selected{background:#E7F2EA;color:#204D39;"
            "border-color:#78A189;font-weight:600;}")
        root.addWidget(self._tabs, 1)
        catalogue = _feature_catalogue()
        all_names = []
        for _group, names in catalogue:
            for name in names:
                if name not in all_names:
                    all_names.append(name)
        self._add_tab("All", all_names)
        for group, names in catalogue:
            self._add_tab(group, names)
        if multiple:
            reset = QPushButton("Restore recommended shortcuts")
            reset.clicked.connect(self._restore_defaults)
            root.addWidget(reset)
        buttons = QDialogButtonBox(
            (QDialogButtonBox.StandardButton.Save if multiple else
             QDialogButtonBox.StandardButton.Open) |
            QDialogButtonBox.StandardButton.Cancel)
        if not multiple:
            buttons.button(QDialogButtonBox.StandardButton.Open).setText("Add feature")
            buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(False)
            self._accept_button = buttons.button(QDialogButtonBox.StandardButton.Open)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _tile_text(name):
        if len(name) <= 18:
            return name
        midpoint = len(name) // 2
        split = name.rfind(" ", 0, midpoint + 5)
        return name if split < 1 else name[:split] + "\n" + name[split + 1:]

    def _add_tab(self, label, names):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:#F7FAF8;border:none;}")
        container = QWidget()
        container.setStyleSheet("background:#F7FAF8;")
        grid = QGridLayout(container)
        grid.setContentsMargins(10, 12, 10, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, name in enumerate(names):
            button = QToolButton()
            button.setText(self._tile_text(name))
            button.setToolTip(name)
            button.setFixedSize(112, 100)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIcon(_feature_icon(name))
            button.setIconSize(QSize(42, 42))
            button.setCheckable(True)
            button.setChecked(name in self.selected)
            button.setStyleSheet(
                "QToolButton{background:white;color:#24332B;border:1px solid #D9E0DA;"
                "border-radius:9px;padding:5px;font-size:10px;}"
                "QToolButton:hover{background:#EDF4EF;border-color:#91AD9B;}"
                "QToolButton:checked{background:#E4F1E8;border:2px solid #28704E;}")
            button.clicked.connect(lambda checked, n=name: self._clicked(n, checked))
            self._buttons.setdefault(name, []).append(button)
            grid.addWidget(button, index // 6, index % 6)
        grid.setRowStretch((len(names) + 5) // 6, 1)
        scroll.setWidget(container)
        self._tabs.addTab(scroll, label)

    def _clicked(self, name, checked):
        if self.multiple:
            if checked:
                self.selected.add(name)
            else:
                self.selected.discard(name)
            for button in self._buttons.get(name, []):
                button.blockSignals(True)
                button.setChecked(name in self.selected)
                button.blockSignals(False)
            return
        self.chosen_name = name if checked else None
        for other_name, buttons in self._buttons.items():
            state = other_name == self.chosen_name
            for button in buttons:
                button.blockSignals(True)
                button.setChecked(state)
                button.blockSignals(False)
        self._accept_button.setEnabled(bool(self.chosen_name))

    def _filter(self, text):
        query = text.strip().casefold()
        if query:
            self._tabs.setCurrentIndex(0)
        for name, buttons in self._buttons.items():
            visible = not query or query in name.casefold()
            for button in buttons:
                button.setVisible(visible)

    def _restore_defaults(self):
        self.selected = set(_DEFAULT_PRESETS)
        for name, buttons in self._buttons.items():
            for button in buttons:
                button.blockSignals(True)
                button.setChecked(name in self.selected)
                button.blockSignals(False)

    def _accept(self):
        if self.multiple or self.chosen_name:
            self.accept()


def load_annotator_presets():
    value = QSettings().value(_PRESET_KEY, list(_DEFAULT_PRESETS))
    if isinstance(value, str):
        value = [value]
    available = ecological_feature_names()
    chosen = [name for name in (value or []) if name in available]
    return chosen or list(_DEFAULT_PRESETS)


def choose_annotator_presets(parent=None):
    dlg = _EcologicalFeaturePicker(
        parent, selected=load_annotator_presets(), multiple=True)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    ordered = [name for name in ecological_feature_names() if name in dlg.selected]
    QSettings().setValue(_PRESET_KEY, ordered)
    return True


class _ResizableSvgItem(QGraphicsSvgItem):
    """SVG with four drag handles that resize uniformly about the opposite corner."""
    def __init__(self, path):
        super().__init__(path)
        self._resize_corner = None
        self._anchor_scene = None
        self._start_distance = 1.0
        self._start_scale = 1.0
        self.setAcceptHoverEvents(True)

    def _handle_size(self):
        return 14.0 / max(abs(self.scale()), 0.05)

    def _corners(self):
        r = super().boundingRect()
        return (r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft())

    def _handle_rects(self):
        s = self._handle_size()
        return [QRectF(p.x() - s / 2, p.y() - s / 2, s, s)
                for p in self._corners()]

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor(_HANDLE_BORDER), 1.4 / max(self.scale(), 0.05)))
            painter.setBrush(QBrush(QColor(_HANDLE_FILL)))
            for rect in self._handle_rects():
                painter.drawRect(rect)

    def hoverMoveEvent(self, event):
        if self.isSelected() and any(r.contains(event.pos()) for r in self._handle_rects()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if self.isSelected():
            for i, rect in enumerate(self._handle_rects()):
                if rect.contains(event.pos()):
                    self._resize_corner = i
                    opposite = self._corners()[(i + 2) % 4]
                    self._anchor_scene = self.mapToScene(opposite)
                    self._start_distance = max(
                        QLineF(self._anchor_scene, event.scenePos()).length(), 1.0)
                    self._start_scale = self.scale()
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_corner is not None:
            distance = QLineF(self._anchor_scene, event.scenePos()).length()
            new_scale = max(0.03, min(30.0,
                self._start_scale * distance / self._start_distance))
            self.setScale(new_scale)
            opposite = self._corners()[(self._resize_corner + 2) % 4]
            now = self.mapToScene(opposite)
            self.setPos(self.pos() + (self._anchor_scene - now))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_corner = None
        self._anchor_scene = None
        super().mouseReleaseEvent(event)


class _ResizableTextItem(QGraphicsTextItem):
    """Movable text/text-box/legend item with corner scaling handles."""
    def __init__(self, text, box=True, kind="Text box",
                 text_color="#17221D", box_color="#FFFFFF"):
        super().__init__(text)
        self._symbol_name = kind
        self._is_annotation_text = True
        self._box = box
        self._text_color = QColor(text_color)
        self._box_color = QColor(box_color)
        self._resize_corner = None
        self.setTextWidth(190)
        self.setDefaultTextColor(self._text_color)
        font = QFont("Inter")
        font.setPointSize(11)
        self.setFont(font)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)

    def _handle_size(self):
        return 14.0 / max(abs(self.scale()), 0.05)

    def _base_rect(self):
        return super().boundingRect().adjusted(-7, -5, 7, 5)

    def _corners(self):
        r = self._base_rect()
        return (r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft())

    def _handle_rects(self):
        s = self._handle_size()
        return [QRectF(p.x()-s/2, p.y()-s/2, s, s) for p in self._corners()]

    def boundingRect(self):
        pad = self._handle_size()
        return self._base_rect().adjusted(-pad, -pad, pad, pad)

    def paint(self, painter, option, widget=None):
        if self._box:
            painter.setPen(QPen(QColor("#617069"), 1.0 / max(self.scale(), .05)))
            fill = QColor(self._box_color)
            fill.setAlpha(220)
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(self._base_rect(), 5, 5)
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor(_HANDLE_BORDER), 1.4 / max(self.scale(), .05)))
            painter.setBrush(QBrush(QColor(_HANDLE_FILL)))
            for rect in self._handle_rects():
                painter.drawRect(rect)

    def mousePressEvent(self, event):
        if self.isSelected():
            for i, rect in enumerate(self._handle_rects()):
                if rect.contains(event.pos()):
                    self._resize_corner = i
                    opposite = self._corners()[(i+2) % 4]
                    self._anchor_scene = self.mapToScene(opposite)
                    self._start_distance = max(QLineF(self._anchor_scene,
                                                       event.scenePos()).length(), 1)
                    self._start_scale = self.scale()
                    event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_corner is not None:
            dist = QLineF(self._anchor_scene, event.scenePos()).length()
            self.setScale(max(.1, min(10, self._start_scale * dist / self._start_distance)))
            opposite = self._corners()[(self._resize_corner+2) % 4]
            now = self.mapToScene(opposite)
            self.setPos(self.pos() + (self._anchor_scene - now))
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_corner = None
        super().mouseReleaseEvent(event)


class _ArrowItem(QGraphicsLineItem):
    """Movable arrow whose two end handles can be dragged independently."""
    def __init__(self, length=180, color="#B3372F", width=5.0,
                 arrow=True):
        super().__init__(QLineF(0, 0, length, 0))
        self._has_arrow = arrow
        self._symbol_name = "Arrow" if arrow else "Line"
        self._arrow_item = True
        self._endpoint = None
        self.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)

    def boundingRect(self):
        return super().boundingRect().adjusted(-14, -14, 14, 14)

    def paint(self, painter, option, widget=None):
        line = self.line()
        painter.setPen(self.pen())
        painter.drawLine(line)
        if self._has_arrow:
            import math
            angle = math.atan2(-line.dy(), line.dx())
            p = line.p2(); size = 18
            a = p - QPointF(math.sin(angle + math.pi/3)*size,
                            math.cos(angle + math.pi/3)*size)
            b = p - QPointF(math.sin(angle + math.pi-math.pi/3)*size,
                            math.cos(angle + math.pi-math.pi/3)*size)
            painter.setBrush(QBrush(self.pen().color()))
            painter.drawPolygon(QPolygonF([p, a, b]))
        if self.isSelected():
            painter.setPen(QPen(QColor(_HANDLE_BORDER), 1.4))
            painter.setBrush(QBrush(QColor(_HANDLE_FILL)))
            for pt in (line.p1(), line.p2()):
                painter.drawRect(QRectF(pt.x()-7, pt.y()-7, 14, 14))

    def mousePressEvent(self, event):
        for i, pt in enumerate((self.line().p1(), self.line().p2())):
            if QLineF(pt, event.pos()).length() <= 15:
                self._endpoint = i
                event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._endpoint is not None:
            line = self.line()
            self.setLine(QLineF(event.pos(), line.p2()) if self._endpoint == 0
                         else QLineF(line.p1(), event.pos()))
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._endpoint = None
        super().mouseReleaseEvent(event)


class _BoxShapeItem(QGraphicsRectItem):
    """Resizable rectangle or ellipse used for visual call-outs."""
    def __init__(self, kind="Rectangle", width=190, height=120,
                 color="#D52B27", line_width=5.0):
        super().__init__(0, 0, width, height)
        self._symbol_name = kind
        self._shape_kind = kind.lower()
        self._resize_corner = None
        self._anchor = None
        self.setPen(QPen(QColor(color), line_width))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)

    def _corners(self):
        r = self.rect()
        return (r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft())

    def boundingRect(self):
        return super().boundingRect().adjusted(-11, -11, 11, 11)

    def paint(self, painter, option, widget=None):
        painter.setPen(self.pen()); painter.setBrush(self.brush())
        if self._shape_kind == "ellipse":
            painter.drawEllipse(self.rect())
        else:
            painter.drawRect(self.rect())
        if self.isSelected():
            painter.setPen(QPen(QColor(_HANDLE_BORDER), 1.4))
            painter.setBrush(QBrush(QColor(_HANDLE_FILL)))
            for p in self._corners():
                painter.drawRect(QRectF(p.x()-7, p.y()-7, 14, 14))

    def mousePressEvent(self, event):
        for i, point in enumerate(self._corners()):
            if QLineF(point, event.pos()).length() <= 16:
                self._resize_corner = i
                self._anchor = self._corners()[(i + 2) % 4]
                event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_corner is not None:
            point = event.pos(); anchor = self._anchor
            self.setRect(QRectF(anchor, point).normalized())
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_corner = None
        super().mouseReleaseEvent(event)


class _FreehandItem(QGraphicsPathItem):
    """Selectable freehand ink stroke created directly on the canvas."""
    def __init__(self, path=None, color="#B3372F", width=5.0):
        super().__init__(path or QPainterPath())
        self._symbol_name = "Pen stroke"
        self.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(QColor(_HANDLE_BORDER), 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.path().boundingRect().adjusted(-5, -5, 5, 5))


class _CircleItem(QGraphicsEllipseItem):
    """Transparent highlight circle with corner resize handles."""
    def __init__(self, diameter=150, color="#D52B27", width=5.0):
        super().__init__(0, 0, diameter, diameter)
        self._symbol_name = "Circle"
        self._shape_item = True
        self._resize_corner = None
        self.setPen(QPen(QColor(color), width))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)

    def _corners(self):
        r = self.rect()
        return (r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft())

    def boundingRect(self):
        return super().boundingRect().adjusted(-10, -10, 10, 10)

    def paint(self, painter, option, widget=None):
        painter.setPen(self.pen()); painter.setBrush(self.brush())
        painter.drawEllipse(self.rect())
        if self.isSelected():
            painter.setPen(QPen(QColor(_HANDLE_BORDER), 1.4))
            painter.setBrush(QBrush(QColor(_HANDLE_FILL)))
            for p in self._corners():
                painter.drawRect(QRectF(p.x()-7, p.y()-7, 14, 14))

    def mousePressEvent(self, event):
        for i, p in enumerate(self._corners()):
            if QLineF(p, event.pos()).length() <= 16:
                self._resize_corner = i
                self._anchor = self._corners()[(i+2) % 4]
                event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_corner is not None:
            p = event.pos(); a = self._anchor
            size = max(20.0, max(abs(p.x()-a.x()), abs(p.y()-a.y())))
            left = a.x() if p.x() >= a.x() else a.x()-size
            top = a.y() if p.y() >= a.y() else a.y()-size
            self.setRect(left, top, size, size)
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_corner = None
        super().mouseReleaseEvent(event)


class _LegendItem(QGraphicsItem):
    """Scalable legend containing the actual SVG swatches and feature counts."""
    def __init__(self, entries):
        super().__init__()
        self._symbol_name = "Legend"
        self._entries = entries
        self._base_scale = 1.0
        self._resize_corner = None
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def set_entries(self, entries):
        self.prepareGeometryChange()
        self._entries = entries
        self.update()

    def _rect(self):
        return QRectF(0, 0, 270, 42 + max(1, len(self._entries)) * 38)

    def _corners(self):
        r = self._rect()
        return (r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft())

    def boundingRect(self):
        return self._rect().adjusted(-8, -8, 8, 8)

    def paint(self, painter, option, widget=None):
        r = self._rect()
        painter.setPen(QPen(QColor("#89958E"), 1.2))
        painter.setBrush(QBrush(QColor(255, 255, 255, 238)))
        painter.drawRoundedRect(r, 8, 8)
        title_font = QFont("Inter"); title_font.setBold(True); title_font.setPointSize(11)
        painter.setFont(title_font); painter.setPen(QColor("#1A2420"))
        painter.drawText(QRectF(14, 8, 240, 24),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "LEGEND")
        body_font = QFont("Inter"); body_font.setPointSize(10)
        painter.setFont(body_font)
        if not self._entries:
            painter.setPen(QColor("#68766E"))
            painter.drawText(QRectF(14, 43, 240, 30), "No ecological features placed")
        for i, (name, count, path) in enumerate(self._entries):
            y = 42 + i * 38
            renderer = QSvgRenderer(path)
            renderer.render(painter, QRectF(13, y + 5, 26, 26))
            painter.setPen(QColor("#24342C"))
            painter.drawText(QRectF(49, y, 205, 36),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"{name}  × {count}")
        if self.isSelected():
            painter.setPen(QPen(QColor(_HANDLE_BORDER), 1.4 / max(self.scale(), .05)))
            painter.setBrush(QColor(_HANDLE_FILL))
            for p in self._corners():
                s = 14 / max(self.scale(), .05)
                painter.drawRect(QRectF(p.x()-s/2, p.y()-s/2, s, s))

    def mousePressEvent(self, event):
        for i, p in enumerate(self._corners()):
            if QLineF(p, event.pos()).length() <= 17 / max(self.scale(), .05):
                self._resize_corner = i
                opposite = self._corners()[(i+2) % 4]
                self._anchor_scene = self.mapToScene(opposite)
                self._start_distance = max(QLineF(self._anchor_scene,
                                                   event.scenePos()).length(), 1)
                self._start_scale = self.scale()
                event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_corner is not None:
            distance = QLineF(self._anchor_scene, event.scenePos()).length()
            self.setScale(max(.1, min(10, self._start_scale *
                                      distance / self._start_distance)))
            opposite = self._corners()[(self._resize_corner+2) % 4]
            now = self.mapToScene(opposite)
            self.setPos(self.pos() + (self._anchor_scene - now))
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_corner = None
        super().mouseReleaseEvent(event)


class _Canvas(QGraphicsView):
    def __init__(self, scene, delete_callback=None, copy_callback=None,
                 paste_callback=None, undo_callback=None, redo_callback=None,
                 begin_change=None, end_change=None, freehand_factory=None,
                 parent=None):
        super().__init__(scene, parent)
        self._delete_callback = delete_callback
        self._copy_callback = copy_callback
        self._paste_callback = paste_callback
        self._undo_callback = undo_callback
        self._redo_callback = redo_callback
        self._begin_change = begin_change
        self._end_change = end_change
        self._freehand_factory = freehand_factory
        self._pen_mode = False
        self._ink_item = None
        self._space_down = False
        self._panning = False
        self._pan_pos = None
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setBackgroundBrush(QColor("#DDE2DE"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Keep the detail beneath the cursor stationary while wheel-zooming.
        # This is much more useful for inspecting a roof feature or elevation
        # detail than zooming around the centre of the imported image.
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def set_pen_mode(self, enabled):
        self._pen_mode = bool(enabled)
        if not self._panning:
            self.viewport().setCursor(
                Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.MiddleButton or
                (event.button() == Qt.MouseButton.LeftButton and self._space_down)):
            self._panning = True
            self._pan_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if (self._pen_mode and event.button() == Qt.MouseButton.LeftButton
                and self._freehand_factory):
            if self._begin_change:
                self._begin_change()
            start = self.mapToScene(event.position().toPoint())
            path = QPainterPath(start)
            self._ink_item = self._freehand_factory(path)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._begin_change:
            self._begin_change()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_pos is not None:
            current = event.position().toPoint()
            delta = current - self._pan_pos
            self._pan_pos = current
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._ink_item is not None:
            path = self._ink_item.path()
            path.lineTo(self.mapToScene(event.position().toPoint()))
            self._ink_item.setPath(path)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self._pan_pos = None
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor if self._space_down
                else (Qt.CursorShape.CrossCursor if self._pen_mode
                      else Qt.CursorShape.ArrowCursor))
            event.accept()
            return
        if self._ink_item is not None:
            if self._ink_item.path().elementCount() < 2:
                path = self._ink_item.path()
                start = path.elementAt(0)
                path.lineTo(start.x + .5, start.y + .5)
                self._ink_item.setPath(path)
            self.scene().clearSelection()
            self._ink_item.setSelected(True)
            self._ink_item = None
            if self._end_change:
                self._end_change()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._end_change:
            self._end_change()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Undo) and self._undo_callback:
            self._undo_callback(); event.accept(); return
        if event.matches(QKeySequence.StandardKey.Redo) and self._redo_callback:
            self._redo_callback(); event.accept(); return
        if event.matches(QKeySequence.StandardKey.Copy) and self._copy_callback:
            self._copy_callback(); event.accept(); return
        if event.matches(QKeySequence.StandardKey.Paste) and self._paste_callback:
            self._paste_callback(); event.accept(); return
        if (event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
                and self.scene().selectedItems()):
            if self._delete_callback:
                self._delete_callback()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            if not self._panning:
                self.viewport().setCursor(
                    Qt.CursorShape.CrossCursor if self._pen_mode
                    else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)


class PlanAnnotatorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plan, Elevation & Photo Annotator")
        self.resize(1180, 760)
        self.setMinimumSize(900, 620)
        self._source_path = ""
        self._background = None
        self._legend_item = None
        self._undo_stack = []
        self._redo_stack = []
        self._pending_before = None
        self._restoring = False
        self._dirty = False
        self._clean_snapshot = []
        self._clipboard_item = None
        self._next_history_id = 1
        settings = QSettings()
        self._arrow_color = settings.value(
            "biome/plan_annotator/arrow_color", "#B3372F", type=str)
        self._circle_color = settings.value(
            "biome/plan_annotator/circle_color", "#D52B27", type=str)
        self._line_width = float(settings.value(
            "biome/plan_annotator/line_width", 5.0))
        self._text_color = settings.value(
            "biome/plan_annotator/text_color", "#17221D", type=str)
        self._box_color = settings.value(
            "biome/plan_annotator/box_color", "#FFFFFF", type=str)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        top = QHBoxLayout()
        title = QLabel("Plan, Elevation & Photo Annotator")
        title.setStyleSheet(
            "font-size:17px;font-weight:600;color:#1A2420;"
            "font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;")
        top.addWidget(title)
        self._undo_btn = QPushButton("Undo")
        self._redo_btn = QPushButton("Redo")
        self._undo_btn.setToolTip("Undo the last annotation change (Ctrl+Z)")
        self._redo_btn.setToolTip("Redo the last undone change (Ctrl+Y)")
        self._undo_btn.clicked.connect(self._undo)
        self._redo_btn.clicked.connect(self._redo)
        top.addWidget(self._undo_btn)
        top.addWidget(self._redo_btn)
        top.addStretch(1)
        self._open_btn = QPushButton("Open image…")
        self._fit_btn = QPushButton("Fit to screen")
        self._copy_btn = QPushButton("Copy image")
        self._export_btn = QPushButton("Export PNG…")
        for btn in (self._undo_btn, self._redo_btn, self._open_btn,
                    self._fit_btn, self._copy_btn, self._export_btn):
            btn.setMinimumHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:white;color:#27483A;border:1px solid #C9D1C9;"
                "border-radius:7px;padding:5px 12px;font-size:11px;}"
                "QPushButton:hover{background:#EAF2EC;border-color:#799486;}"
                "QPushButton:disabled{color:#A1AAA4;background:#F2F4F2;}")
            top.addWidget(btn)
        self._export_btn.setStyleSheet(
            "QPushButton{background:#2E6B4F;color:white;border:none;border-radius:7px;"
            "padding:5px 13px;font-size:11px;font-weight:500;}"
            "QPushButton:hover{background:#245A41;}"
            "QPushButton:disabled{background:#AEBBB3;}")
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(10)

        tools = QWidget()
        tools.setFixedWidth(210)
        tools.setStyleSheet(
            "QWidget#toolPanel{background:#F2F5F2;border:1px solid #D9E0DA;"
            "border-radius:10px;}")
        tools.setObjectName("toolPanel")
        tl = QVBoxLayout(tools)
        tl.setContentsMargins(10, 11, 10, 11)
        tl.setSpacing(7)
        sh = QLabel("ENHANCEMENT FEATURES")
        sh.setStyleSheet("font-size:10px;font-weight:600;letter-spacing:0.7px;color:#42584C;")
        tl.addWidget(sh)
        self._preset_widget = QWidget()
        self._preset_layout = QVBoxLayout(self._preset_widget)
        self._preset_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_layout.setSpacing(5)
        tl.addWidget(self._preset_widget)
        choose_presets_btn = QPushButton("Choose shortcuts…")
        choose_presets_btn.clicked.connect(self._choose_presets)
        tl.addWidget(choose_presets_btn)
        all_hdr = QLabel("ALL ECOLOGICAL FEATURES")
        all_hdr.setStyleSheet(
            "font-size:10px;font-weight:600;letter-spacing:0.7px;"
            "color:#42584C;padding-top:7px;")
        tl.addWidget(all_hdr)
        try:
            from .gridref_dialog import load_custom_features
            self._custom_point_paths = {
                name: props.get("svg") for name, props in
                load_custom_features().get("points", {}).items()
                if props.get("svg")
            }
        except Exception:
            self._custom_point_paths = {}
        self._rebuild_preset_buttons()
        add_feature_btn = QPushButton("Browse all features…")
        add_feature_btn.setMinimumHeight(38)
        add_feature_btn.setStyleSheet(
            "QPushButton{text-align:left;background:white;color:#243B31;"
            "border:1px solid #C9D5CD;border-radius:8px;padding:6px 10px;}"
            "QPushButton:hover{background:#E4EEE7;border-color:#86A492;}")
        add_feature_btn.clicked.connect(self._browse_library_features)
        tl.addWidget(add_feature_btn)

        draw_hdr = QLabel("MARK-UP TOOLS")
        draw_hdr.setStyleSheet(
            "font-size:10px;font-weight:600;letter-spacing:0.7px;"
            "color:#42584C;padding-top:7px;")
        tl.addWidget(draw_hdr)
        markup = QGridLayout()
        markup.setSpacing(5)
        for index, (text, callback) in enumerate((
                ("Pen", self._toggle_pen), ("Line", self._add_line),
                ("Arrow", self._add_arrow), ("Circle", self._add_circle),
                ("Rectangle", self._add_rectangle),
                ("Ellipse", self._add_ellipse))):
            btn = QPushButton(text)
            if text == "Pen":
                btn.setCheckable(True)
                self._pen_btn = btn
            btn.clicked.connect(callback)
            markup.addWidget(btn, index // 2, index % 2)
        tl.addLayout(markup)
        text_tools = QHBoxLayout()
        for text, callback in (("Text", self._add_text),
                               ("Text box", self._add_text_box)):
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            text_tools.addWidget(btn)
        tl.addLayout(text_tools)
        legend_btn = QPushButton("Add / refresh legend")
        legend_btn.clicked.connect(self._add_or_refresh_legend)
        tl.addWidget(legend_btn)

        hint = QLabel("Use any plan, elevation or photograph. Drag the corner handles to resize.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:10px;color:#68766E;padding:5px 2px;")
        tl.addWidget(hint)
        tl.addStretch(1)
        body.addWidget(tools)

        self._scene = QGraphicsScene(self)
        self._scene.selectionChanged.connect(self._selection_changed)
        self._view = _Canvas(
            self._scene, self._delete_selected, self._copy_selected,
            self._paste_selected, self._undo, self._redo,
            self._begin_change, self._commit_change, self._create_freehand)
        body.addWidget(self._view, 1)

        props = QWidget()
        props.setObjectName("propertyPanel")
        props.setFixedWidth(230)
        props.setStyleSheet(
            "QWidget#propertyPanel{background:#F7F8F6;border:1px solid #D9E0DA;"
            "border-radius:10px;}")
        pl = QVBoxLayout(props)
        pl.setContentsMargins(11, 11, 11, 11)
        pl.setSpacing(7)
        ph = QLabel("SELECTED FEATURE")
        ph.setStyleSheet("font-size:10px;font-weight:600;letter-spacing:0.7px;color:#42584C;")
        pl.addWidget(ph)
        self._selected_name = QLabel("Nothing selected")
        self._selected_name.setWordWrap(True)
        self._selected_name.setStyleSheet("font-size:13px;font-weight:600;color:#1A2420;")
        pl.addWidget(self._selected_name)
        pl.addWidget(QLabel("Label"))
        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("e.g. SB1")
        self._label_edit.textEdited.connect(self._apply_label)
        self._label_edit.editingFinished.connect(self._commit_change)
        pl.addWidget(self._label_edit)
        pl.addWidget(QLabel("Size"))
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(25, 1000)
        self._size_slider.setValue(100)
        self._size_slider.valueChanged.connect(self._apply_size)
        self._size_slider.sliderPressed.connect(self._begin_change)
        self._size_slider.sliderReleased.connect(self._commit_change)
        pl.addWidget(self._size_slider)
        pl.addWidget(QLabel("Rotation"))
        self._rotation = QSpinBox()
        self._rotation.setRange(0, 359)
        self._rotation.setSuffix("°")
        self._rotation.valueChanged.connect(self._apply_rotation)
        pl.addWidget(self._rotation)

        self._style_heading = QLabel("STYLE")
        self._style_heading.setStyleSheet(
            "font-size:10px;font-weight:600;letter-spacing:0.7px;"
            "color:#42584C;padding-top:7px;")
        pl.addWidget(self._style_heading)
        self._stroke_label = QLabel("Line colour")
        self._stroke_color_btn = QPushButton()
        self._stroke_color_btn.clicked.connect(self._choose_stroke_color)
        pl.addWidget(self._stroke_label)
        pl.addWidget(self._stroke_color_btn)
        self._width_label = QLabel("Line thickness")
        self._width_spin = QDoubleSpinBox()
        self._width_spin.setRange(1.0, 20.0)
        self._width_spin.setSingleStep(0.5)
        self._width_spin.setSuffix(" px")
        self._width_spin.valueChanged.connect(self._apply_line_width)
        pl.addWidget(self._width_label)
        pl.addWidget(self._width_spin)
        self._text_color_label = QLabel("Text colour")
        self._text_color_btn = QPushButton()
        self._text_color_btn.clicked.connect(self._choose_text_color)
        pl.addWidget(self._text_color_label)
        pl.addWidget(self._text_color_btn)
        self._box_color_label = QLabel("Text-box fill")
        self._box_color_btn = QPushButton()
        self._box_color_btn.clicked.connect(self._choose_box_color)
        pl.addWidget(self._box_color_label)
        pl.addWidget(self._box_color_btn)
        actions = QHBoxLayout()
        self._duplicate_btn = QPushButton("Duplicate")
        self._delete_btn = QPushButton("Delete")
        self._duplicate_btn.clicked.connect(self._duplicate_selected)
        self._delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self._duplicate_btn)
        actions.addWidget(self._delete_btn)
        pl.addLayout(actions)
        pl.addStretch(1)
        body.addWidget(props)
        root.addLayout(body, 1)

        foot = QLabel(
            "Wheel: zoom  ·  middle mouse or Space + drag: pan  ·  Ctrl+C / Ctrl+V: copy / paste")
        foot.setStyleSheet("font-size:10px;color:#718078;")
        root.addWidget(foot)

        self._open_btn.clicked.connect(self._open_image)
        self._fit_btn.clicked.connect(self._fit)
        self._copy_btn.clicked.connect(self._copy_image)
        self._export_btn.clicked.connect(self._export_png)
        self._set_document_enabled(False)
        self._selection_changed()
        self._update_history_buttons()

    def _history_id(self, item):
        if not hasattr(item, "_history_id"):
            item._history_id = self._next_history_id
            self._next_history_id += 1
        return item._history_id

    @staticmethod
    def _common_state(item):
        return {
            "x": item.pos().x(), "y": item.pos().y(),
            "scale": item.scale(), "rotation": item.rotation(),
            "z": item.zValue(),
        }

    def _serialise_item(self, item):
        common = self._common_state(item)
        common["id"] = self._history_id(item)
        if isinstance(item, _ResizableSvgItem):
            common.update({
                "type": "svg", "name": item._symbol_name,
                "path": item._svg_path,
                "base_scale": getattr(item, "_base_scale", 1.0),
                "label": item._label_item.text() if hasattr(item, "_label_item") else "",
            })
        elif isinstance(item, _ResizableTextItem):
            common.update({
                "type": "text", "name": item._symbol_name,
                "text": item.toPlainText(), "box": item._box,
                "text_width": item.textWidth(),
                "text_color": item._text_color.name(),
                "box_color": item._box_color.name(),
                "base_scale": getattr(item, "_base_scale", 1.0),
            })
        elif isinstance(item, _ArrowItem):
            line = item.line(); pen = item.pen()
            common.update({
                "type": "arrow", "line": [line.x1(), line.y1(), line.x2(), line.y2()],
                "color": pen.color().name(), "width": pen.widthF(),
                "arrow": item._has_arrow,
                "base_scale": getattr(item, "_base_scale", 1.0),
            })
        elif isinstance(item, _CircleItem):
            rect = item.rect(); pen = item.pen()
            common.update({
                "type": "circle",
                "rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                "color": pen.color().name(), "width": pen.widthF(),
                "base_scale": getattr(item, "_base_scale", 1.0),
            })
        elif isinstance(item, _BoxShapeItem):
            rect = item.rect(); pen = item.pen()
            common.update({
                "type": "box_shape", "kind": item._symbol_name,
                "rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                "color": pen.color().name(), "width": pen.widthF(),
                "base_scale": getattr(item, "_base_scale", 1.0),
            })
        elif isinstance(item, _FreehandItem):
            pen = item.pen(); path = item.path()
            common.update({
                "type": "freehand",
                "points": [[path.elementAt(i).x, path.elementAt(i).y]
                           for i in range(path.elementCount())],
                "color": pen.color().name(), "width": pen.widthF(),
                "base_scale": getattr(item, "_base_scale", 1.0),
            })
        elif isinstance(item, _LegendItem):
            common.update({"type": "legend", "base_scale": 1.0})
        else:
            return None
        return common

    def _snapshot(self):
        states = [self._serialise_item(item) for item in self._scene.items()
                  if item is not self._background]
        return sorted((state for state in states if state is not None),
                      key=lambda state: state["id"])

    def _restore_common(self, item, state):
        item._history_id = state["id"]
        self._next_history_id = max(self._next_history_id, state["id"] + 1)
        item._base_scale = state.get("base_scale", 1.0)
        item.setPos(state["x"], state["y"])
        item.setScale(state["scale"])
        item.setRotation(state["rotation"])
        item.setZValue(state.get("z", 0.0))
        item.setTransformOriginPoint(item.boundingRect().center())
        self._scene.addItem(item)
        return item

    def _item_from_state(self, state):
        kind = state["type"]
        if kind == "svg":
            item = _ResizableSvgItem(state["path"])
            item._symbol_name = state["name"]
            item._svg_path = state["path"]
            item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                          QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            label = QGraphicsSimpleTextItem(state.get("label", ""), item)
            label.setBrush(QColor("#111A16"))
            font = QFont("Inter"); font.setBold(True); font.setPointSize(9)
            label.setFont(font)
            br = item.boundingRect()
            label.setPos(br.left(), br.bottom() + 3)
            item._label_item = label
        elif kind == "text":
            item = _ResizableTextItem(
                state["text"], state["box"], state["name"],
                state.get("text_color", "#17221D"),
                state.get("box_color", "#FFFFFF"))
            item.setTextWidth(state.get("text_width", 190))
        elif kind == "arrow":
            item = _ArrowItem(color=state["color"], width=state["width"],
                              arrow=state.get("arrow", True))
            item.setLine(QLineF(*state["line"]))
        elif kind == "circle":
            item = _CircleItem(color=state["color"], width=state["width"])
            item.setRect(QRectF(*state["rect"]))
        elif kind == "box_shape":
            item = _BoxShapeItem(state["kind"], color=state["color"],
                                 line_width=state["width"])
            item.setRect(QRectF(*state["rect"]))
        elif kind == "freehand":
            points = state.get("points", [])
            path = QPainterPath()
            if points:
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
            item = _FreehandItem(path, state["color"], state["width"])
        elif kind == "legend":
            item = _LegendItem(self._legend_entries())
            self._legend_item = item
        else:
            return None
        return self._restore_common(item, state)

    def _restore_snapshot(self, snapshot):
        self._restoring = True
        try:
            for item in list(self._scene.items()):
                if item is not self._background:
                    self._scene.removeItem(item)
            self._legend_item = None
            for state in snapshot:
                if state["type"] != "legend":
                    self._item_from_state(state)
            for state in snapshot:
                if state["type"] == "legend":
                    self._item_from_state(state)
            self._scene.clearSelection()
            self._selection_changed()
        finally:
            self._restoring = False

    def _begin_change(self):
        if (not self._restoring and self._background is not None
                and self._pending_before is None):
            self._pending_before = self._snapshot()

    def _commit_change(self):
        if self._pending_before is None or self._restoring:
            return
        before = self._pending_before
        self._pending_before = None
        after = self._snapshot()
        if before != after:
            self._undo_stack.append(before)
            self._redo_stack.clear()
            self._dirty = after != self._clean_snapshot
            self._update_history_buttons()

    def _undo(self):
        if not self._undo_stack:
            return
        self._pending_before = None
        current = self._snapshot()
        target = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._restore_snapshot(target)
        self._dirty = target != self._clean_snapshot
        self._update_history_buttons()

    def _redo(self):
        if not self._redo_stack:
            return
        self._pending_before = None
        current = self._snapshot()
        target = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._restore_snapshot(target)
        self._dirty = target != self._clean_snapshot
        self._update_history_buttons()

    def _update_history_buttons(self):
        self._undo_btn.setEnabled(bool(self._undo_stack))
        self._redo_btn.setEnabled(bool(self._redo_stack))

    def _set_document_enabled(self, enabled):
        self._fit_btn.setEnabled(enabled)
        self._copy_btn.setEnabled(enabled)
        self._export_btn.setEnabled(enabled)

    def _open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open plan, elevation or photograph", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All files (*.*)")
        if not path:
            return
        if self._dirty:
            answer = QMessageBox.question(
                self, "Discard unsaved annotations?",
                "Opening another image will discard the current annotations. "
                "Export the annotated image first if you need to keep them.\n\n"
                "Discard the annotations and continue?",
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.warning(self, "Open image", "The selected image could not be read.")
            return
        self._scene.clear()
        self._legend_item = None
        self._source_path = path
        self._background = QGraphicsPixmapItem(pix)
        self._background.setZValue(-1000)
        self._scene.addItem(self._background)
        self._scene.setSceneRect(QRectF(pix.rect()))
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._pending_before = None
        self._clean_snapshot = []
        self._dirty = False
        self._clipboard_item = None
        self._update_history_buttons()
        self._set_document_enabled(True)
        self._fit()

    def _fit(self):
        if self._background is not None:
            self._view.fitInView(self._scene.sceneRect(),
                                 Qt.AspectRatioMode.KeepAspectRatio)

    def _rebuild_preset_buttons(self):
        while self._preset_layout.count():
            item = self._preset_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name in load_annotator_presets():
            btn = QPushButton(name)
            btn.setMinimumHeight(42)
            btn.setIcon(_feature_icon(name, 26))
            btn.setIconSize(QSize(26, 26))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{text-align:left;background:white;color:#243B31;"
                "border:1px solid transparent;border-radius:8px;padding:6px 9px;}"
                "QPushButton:hover{background:#E4EEE7;border-color:#9CB6A5;}")
            btn.clicked.connect(
                lambda _checked=False, n=name: self._add_preset_name(n))
            self._preset_layout.addWidget(btn)

    def _choose_presets(self):
        if choose_annotator_presets(self):
            self._rebuild_preset_buttons()

    def _add_preset_name(self, name):
        path = self._library_symbol(name)
        if not path:
            QMessageBox.information(
                self, "Symbol unavailable",
                "This shortcut does not currently have an SVG symbol.")
            return
        self._add_symbol(name, path, absolute=True)

    def _symbol_path(self, filename):
        return os.path.join(os.path.dirname(__file__), "Symbols", filename)

    def _library_symbol(self, name):
        """Resolve the same SVG used by Feature Mapper, with a filename fallback."""
        custom_path = getattr(self, "_custom_point_paths", {}).get(name)
        if custom_path and os.path.exists(custom_path):
            return custom_path
        filename = None
        try:
            from .gridref_dialog import FEATURE_SVG
            filename = FEATURE_SVG.get(name)
        except Exception:
            pass
        if filename and os.path.isabs(filename):
            return filename if os.path.exists(filename) else None
        candidates = []
        if filename:
            candidates.append(filename)
        import re
        candidates.append(re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + ".svg")
        for candidate in candidates:
            path = self._symbol_path(candidate)
            if os.path.exists(path):
                return path
        return None

    def _browse_library_features(self):
        picker = _EcologicalFeaturePicker(self, multiple=False)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        name = picker.chosen_name
        path = self._library_symbol(name)
        if not path:
            QMessageBox.information(
                self, "Symbol unavailable",
                "This feature does not yet have an SVG symbol that can be placed on an image.")
            return
        self._add_symbol(name, path, absolute=True)

    def _require_background(self):
        if self._background is not None:
            return True
        QMessageBox.information(self, "Open an image first",
                                "Open a plan, elevation or photograph before adding mark-up.")
        return False

    def _canvas_centre(self):
        return self._view.mapToScene(self._view.viewport().rect().center())

    def _add_symbol(self, name, filename, absolute=False):
        if not self._require_background():
            return
        path = filename if absolute else self._symbol_path(filename)
        if not os.path.exists(path):
            QMessageBox.warning(self, "Missing symbol", f"The symbol file is missing:\n{path}")
            return
        self._begin_change()
        item = _ResizableSvgItem(path)
        item._symbol_name = name
        item._svg_path = path
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        br = item.boundingRect()
        item.setTransformOriginPoint(br.center())
        target = max(self._scene.sceneRect().width(),
                     self._scene.sceneRect().height()) * 0.065
        base = max(br.width(), br.height(), 1.0)
        item._base_scale = target / base
        item.setScale(item._base_scale)
        centre = self._canvas_centre()
        item.setPos(centre.x() - br.center().x() * item.scale(),
                    centre.y() - br.center().y() * item.scale())
        label = QGraphicsSimpleTextItem("", item)
        label.setBrush(QColor("#111A16"))
        font = QFont("Inter")
        font.setBold(True)
        font.setPointSize(9)
        label.setFont(font)
        label.setPos(br.left(), br.bottom() + 3)
        item._label_item = label
        self._scene.addItem(item)
        self._scene.clearSelection()
        item.setSelected(True)
        self._update_legend()
        self._commit_change()

    def _place_new_item(self, item):
        if not self._require_background():
            return False
        self._begin_change()
        centre = self._canvas_centre()
        item.setPos(centre - item.boundingRect().center())
        item.setTransformOriginPoint(item.boundingRect().center())
        item._base_scale = 1.0
        self._scene.addItem(item)
        self._scene.clearSelection()
        item.setSelected(True)
        self._commit_change()
        return True

    def _add_arrow(self):
        self._place_new_item(_ArrowItem(
            color=self._arrow_color, width=self._line_width))

    def _add_line(self):
        self._place_new_item(_ArrowItem(
            color=self._arrow_color, width=self._line_width, arrow=False))

    def _add_circle(self):
        self._place_new_item(_CircleItem(
            color=self._circle_color, width=self._line_width))

    def _add_rectangle(self):
        self._place_new_item(_BoxShapeItem(
            "Rectangle", color=self._circle_color,
            line_width=self._line_width))

    def _add_ellipse(self):
        self._place_new_item(_BoxShapeItem(
            "Ellipse", color=self._circle_color,
            line_width=self._line_width))

    def _toggle_pen(self, checked):
        if checked and not self._require_background():
            self._pen_btn.blockSignals(True)
            self._pen_btn.setChecked(False)
            self._pen_btn.blockSignals(False)
            checked = False
        self._view.set_pen_mode(checked)
        self._pen_btn.setText("Stop pen" if checked else "Pen")

    def _create_freehand(self, path):
        item = _FreehandItem(
            path, color=self._arrow_color, width=self._line_width)
        item._base_scale = 1.0
        self._scene.addItem(item)
        return item

    def _add_text_box(self):
        self._place_new_item(_ResizableTextItem(
            "Text box", box=True, text_color=self._text_color,
            box_color=self._box_color))

    def _add_text(self):
        self._place_new_item(_ResizableTextItem(
            "Text", box=False, kind="Text", text_color=self._text_color,
            box_color=self._box_color))

    def _legend_entries(self):
        counts = {}
        paths = {}
        for item in self._scene.items():
            if isinstance(item, _ResizableSvgItem):
                counts[item._symbol_name] = counts.get(item._symbol_name, 0) + 1
                paths[item._symbol_name] = item._svg_path
        return [(name, counts[name], paths[name]) for name in sorted(counts)]

    def _add_or_refresh_legend(self):
        if not self._require_background():
            return
        if self._legend_item is None or self._legend_item.scene() is None:
            self._legend_item = _LegendItem(self._legend_entries())
            self._place_new_item(self._legend_item)
        else:
            self._begin_change()
            self._legend_item.set_entries(self._legend_entries())
            self._scene.clearSelection()
            self._legend_item.setSelected(True)
            self._commit_change()

    def _update_legend(self):
        if self._legend_item is not None and self._legend_item.scene() is not None:
            self._legend_item.set_entries(self._legend_entries())

    def _selected(self):
        for item in self._scene.selectedItems():
            if hasattr(item, "_symbol_name"):
                return item
        return None

    def _selection_changed(self):
        item = self._selected()
        enabled = item is not None
        self._selected_name.setText(item._symbol_name if item else "Nothing selected")
        for widget in (self._size_slider, self._rotation,
                       self._duplicate_btn, self._delete_btn):
            widget.setEnabled(enabled)
        self._duplicate_btn.setEnabled(enabled and not isinstance(item, _LegendItem))
        label_enabled = enabled and (hasattr(item, "_label_item") or
                                     isinstance(item, _ResizableTextItem))
        self._label_edit.setEnabled(label_enabled)
        if item:
            self._label_edit.blockSignals(True)
            if hasattr(item, "_label_item"):
                self._label_edit.setText(item._label_item.text())
            elif isinstance(item, _ResizableTextItem):
                self._label_edit.setText(item.toPlainText())
            else:
                self._label_edit.clear()
            self._label_edit.blockSignals(False)
            self._size_slider.blockSignals(True)
            base_scale = getattr(item, "_base_scale", 1.0)
            self._size_slider.setValue(
                max(25, min(1000, round(item.scale() / base_scale * 100))))
            self._size_slider.blockSignals(False)
            self._rotation.blockSignals(True)
            self._rotation.setValue(round(item.rotation()) % 360)
            self._rotation.blockSignals(False)
        is_line_shape = isinstance(
            item, (_ArrowItem, _CircleItem, _BoxShapeItem, _FreehandItem))
        is_text = isinstance(item, _ResizableTextItem)
        self._style_heading.setVisible(is_line_shape or is_text)
        for widget in (self._stroke_label, self._stroke_color_btn, self._width_label,
                       self._width_spin):
            widget.setVisible(is_line_shape)
        for widget in (self._text_color_label, self._text_color_btn):
            widget.setVisible(is_text)
        for widget in (self._box_color_label, self._box_color_btn):
            widget.setVisible(is_text and bool(getattr(item, "_box", False)))
        if is_line_shape:
            self._set_color_button(self._stroke_color_btn, item.pen().color())
            self._width_spin.blockSignals(True)
            self._width_spin.setValue(item.pen().widthF())
            self._width_spin.blockSignals(False)
        if is_text:
            self._set_color_button(self._text_color_btn, item._text_color)
            self._set_color_button(self._box_color_btn, item._box_color)

    @staticmethod
    def _set_color_button(button, color):
        colour = QColor(color)
        button.setText(colour.name().upper())
        text = "#FFFFFF" if colour.lightness() < 120 else "#17221D"
        button.setStyleSheet(
            "QPushButton{background:%s;color:%s;border:1px solid #809087;"
            "border-radius:6px;padding:5px;}" % (colour.name(), text))

    def _choose_stroke_color(self):
        item = self._selected()
        if not isinstance(
                item, (_ArrowItem, _CircleItem, _BoxShapeItem, _FreehandItem)):
            return
        colour = QColorDialog.getColor(item.pen().color(), self, "Choose line colour")
        if not colour.isValid():
            return
        self._begin_change()
        pen = item.pen(); pen.setColor(colour); item.setPen(pen)
        self._set_color_button(self._stroke_color_btn, colour)
        key = ("biome/plan_annotator/arrow_color"
               if isinstance(item, (_ArrowItem, _FreehandItem))
               else "biome/plan_annotator/circle_color")
        QSettings().setValue(key, colour.name())
        if isinstance(item, (_ArrowItem, _FreehandItem)):
            self._arrow_color = colour.name()
        else:
            self._circle_color = colour.name()
        self._commit_change()

    def _apply_line_width(self, value):
        item = self._selected()
        if not isinstance(
                item, (_ArrowItem, _CircleItem, _BoxShapeItem, _FreehandItem)):
            return
        self._begin_change()
        pen = item.pen(); pen.setWidthF(value); item.setPen(pen)
        self._line_width = value
        QSettings().setValue("biome/plan_annotator/line_width", value)
        self._commit_change()

    def _choose_text_color(self):
        item = self._selected()
        if not isinstance(item, _ResizableTextItem):
            return
        colour = QColorDialog.getColor(item._text_color, self, "Choose text colour")
        if not colour.isValid():
            return
        self._begin_change()
        item._text_color = QColor(colour)
        item.setDefaultTextColor(colour)
        self._text_color = colour.name()
        QSettings().setValue("biome/plan_annotator/text_color", self._text_color)
        self._set_color_button(self._text_color_btn, colour)
        self._commit_change()

    def _choose_box_color(self):
        item = self._selected()
        if not isinstance(item, _ResizableTextItem) or not item._box:
            return
        colour = QColorDialog.getColor(item._box_color, self, "Choose text-box fill")
        if not colour.isValid():
            return
        self._begin_change()
        item._box_color = QColor(colour)
        item.update()
        self._box_color = colour.name()
        QSettings().setValue("biome/plan_annotator/box_color", self._box_color)
        self._set_color_button(self._box_color_btn, colour)
        self._commit_change()

    def _apply_label(self, text):
        item = self._selected()
        if item:
            self._begin_change()
        if item and hasattr(item, "_label_item"):
            item._label_item.setText(text.strip())
        elif isinstance(item, _ResizableTextItem):
            item.setPlainText(text)

    def _apply_size(self, value):
        item = self._selected()
        if item:
            keyboard_change = not self._size_slider.isSliderDown()
            if keyboard_change:
                self._begin_change()
            item.setScale(getattr(item, "_base_scale", 1.0) * value / 100.0)
            if keyboard_change:
                self._commit_change()

    def _apply_rotation(self, value):
        item = self._selected()
        if item:
            self._begin_change()
            item.setRotation(value)
            self._commit_change()

    def _delete_selected(self):
        selected = [item for item in self._scene.selectedItems()
                    if hasattr(item, "_symbol_name")]
        if selected:
            self._begin_change()
            for item in selected:
                self._scene.removeItem(item)
                if item is self._legend_item:
                    self._legend_item = None
            self._update_legend()
            self._selection_changed()
            self._commit_change()

    def _copy_selected(self):
        item = self._selected()
        if item is None or isinstance(item, _LegendItem):
            return
        self._clipboard_item = copy.deepcopy(self._serialise_item(item))

    def _paste_selected(self):
        if not self._clipboard_item or not self._require_background():
            return
        self._begin_change()
        state = copy.deepcopy(self._clipboard_item)
        state["id"] = self._next_history_id
        self._next_history_id += 1
        state["x"] += 18
        state["y"] += 18
        item = self._item_from_state(state)
        if item is None:
            self._pending_before = None
            return
        self._clipboard_item = copy.deepcopy(state)
        self._scene.clearSelection()
        item.setSelected(True)
        self._update_legend()
        self._commit_change()

    def _duplicate_selected(self):
        src = self._selected()
        if not src:
            return
        self._begin_change()
        if isinstance(src, _ResizableSvgItem):
            item = _ResizableSvgItem(src._svg_path)
            item._symbol_name = src._symbol_name
            item._svg_path = src._svg_path
            item._base_scale = src._base_scale
            item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                          QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            label = QGraphicsSimpleTextItem(src._label_item.text(), item)
            label.setBrush(src._label_item.brush())
            label.setFont(src._label_item.font())
            label.setPos(item.boundingRect().left(), item.boundingRect().bottom() + 3)
            item._label_item = label
        elif isinstance(src, _ResizableTextItem):
            item = _ResizableTextItem(src.toPlainText(), src._box,
                                      src._symbol_name, src._text_color,
                                      src._box_color)
            item.setTextWidth(src.textWidth())
            item.setFont(src.font())
            item._base_scale = getattr(src, "_base_scale", 1.0)
        elif isinstance(src, _ArrowItem):
            item = _ArrowItem(color=src.pen().color(), width=src.pen().widthF(),
                              arrow=src._has_arrow)
            item.setLine(src.line())
            item._base_scale = getattr(src, "_base_scale", 1.0)
        elif isinstance(src, _CircleItem):
            item = _CircleItem(color=src.pen().color(), width=src.pen().widthF())
            item.setRect(src.rect())
            item._base_scale = getattr(src, "_base_scale", 1.0)
        elif isinstance(src, _BoxShapeItem):
            item = _BoxShapeItem(src._symbol_name, color=src.pen().color(),
                                 line_width=src.pen().widthF())
            item.setRect(src.rect())
            item._base_scale = getattr(src, "_base_scale", 1.0)
        elif isinstance(src, _FreehandItem):
            item = _FreehandItem(src.path(), src.pen().color(), src.pen().widthF())
            item._base_scale = getattr(src, "_base_scale", 1.0)
        else:
            self._pending_before = None
            return
        item.setTransformOriginPoint(item.boundingRect().center())
        item.setScale(src.scale())
        item.setRotation(src.rotation())
        item.setPos(src.pos().x() + 18, src.pos().y() + 18)
        self._scene.addItem(item)
        self._scene.clearSelection()
        item.setSelected(True)
        self._update_legend()
        self._commit_change()

    def _render_image(self):
        if self._background is None:
            return None
        rect = self._scene.sceneRect()
        image = QImage(max(1, round(rect.width())), max(1, round(rect.height())),
                       QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.white)
        selected = self._scene.selectedItems()
        self._scene.clearSelection()
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing |
                               QPainter.RenderHint.SmoothPixmapTransform)
        self._scene.render(painter, QRectF(image.rect()), rect)
        painter.end()
        for item in selected:
            item.setSelected(True)
        return image

    def _copy_image(self):
        image = self._render_image()
        if image is not None:
            QApplication.clipboard().setImage(image)

    def _export_png(self):
        image = self._render_image()
        if image is None:
            return
        base = os.path.splitext(os.path.basename(self._source_path))[0]
        start = os.path.join(os.path.dirname(self._source_path),
                             base + " - annotated.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export annotated plan", start, "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not image.save(path, "PNG"):
            QMessageBox.warning(self, "Export failed", "The annotated image could not be saved.")
            return
        self._clean_snapshot = self._snapshot()
        self._dirty = False
        QMessageBox.information(self, "Export complete", "The annotated image has been saved.")

