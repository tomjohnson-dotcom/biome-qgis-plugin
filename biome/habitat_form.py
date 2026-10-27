# habitat_form.py

import math

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QGroupBox, QTextEdit, QLineEdit,
    QScrollArea, QFrame, QInputDialog, QDoubleSpinBox, QSpinBox,
    QCheckBox, QButtonGroup, QSizePolicy, QAbstractSpinBox, QMessageBox,
)
from qgis.PyQt.QtCore import QTimer, Qt, QSize
from qgis.PyQt.QtGui import QFont, QColor, QCursor
from qgis.core import Qgis
from qgis.core import (
    QgsFeature, QgsGeometry, QgsProject, QgsPointXY,
    QgsEditFormConfig, QgsSnappingConfig, QgsTolerance,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand
from .habitat_data import (HABITAT_TYPES, CONDITION_OPTIONS,
                           HABITAT_SCREEN_TYPES, IRREPLACEABLE_SUBTYPES,
                           is_condition_locked, condition_items_for,
                           canonical_habitat_name)
from .layer_manager import (TREE_SIZE_AREAS, tree_size_from_sub_type,
                            tree_location_from_sub_type,
                            is_redline_name, REDLINE_NAME_ALIASES,
                            STRATEGIC_SIGNIFICANCE_OPTIONS,
                            DEFAULT_STRATEGIC_SIGNIFICANCE,
                            WATERCOURSE_ENCROACHMENT_OPTIONS,
                            RIPARIAN_ENCROACHMENT_OPTIONS)
from . import condition_data as CD
from .config import measurement_ellipsoid
from . import theme


class _HabitatFreehandTool(QgsMapTool):
    """Freehand polygon tool for habitat form — hold and drag, release to finish."""

    def __init__(self, canvas, on_finished_cb, on_cancel_cb=None):
        super().__init__(canvas)
        self._on_finished = on_finished_cb
        self._on_cancel   = on_cancel_cb
        self._drawing     = False
        self._points      = []

        self._rubber = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rubber.setColor(QColor(46, 125, 50, 200))
        self._rubber.setFillColor(QColor(46, 125, 50, 40))
        self._rubber.setWidth(2)

    def _cleanup(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        self._points = []
        self._drawing = False

    def canvasPressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drawing = True
            self._points = []
            self._points.append(self.toMapCoordinates(e.pos()))

    def canvasMoveEvent(self, e):
        if not self._drawing:
            return
        pt = self.toMapCoordinates(e.pos())
        self._points.append(pt)
        self._rubber.addPoint(pt, True)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drawing:
            pts = list(self._points)
            self._cleanup()
            if len(pts) >= 3:
                self._on_finished(pts)
            elif self._on_cancel:
                self._on_cancel()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._cleanup()
            if self._on_cancel:
                self._on_cancel()

    def deactivate(self):
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        super().deactivate()


def _local_ring(shape, dims):
    """Return one or more rings of (x, y) tuples in metres, centred on (0, 0),
    un-rotated.  The first ring is the exterior; any further rings are holes.

    All dimensions are in metres (layers are EPSG:27700 / British National Grid).
        circle          → {"d": diameter}
        ellipse         → {"a": long-axis dia, "b": short-axis dia}
        square          → {"s": side}
        rectangle       → {"w": width, "h": height}
        stadium         → {"w": width, "h": height, "r": corner radius}
        triangle        → {"s": side length}            (equilateral, point-up)
        right_triangle  → {"a": base, "b": height}
        regular         → {"s": side length, "n": number of sides}
        annulus         → {"d": outer diameter, "w": ring width}  → ring + hole
    """
    if shape == "circle":
        r = dims["d"] / 2.0
        n = 64
        return [[(r * math.cos(2 * math.pi * i / n),
                  r * math.sin(2 * math.pi * i / n)) for i in range(n)]]

    if shape == "ellipse":
        a, b = dims["a"] / 2.0, dims["b"] / 2.0
        n = 64
        return [[(a * math.cos(2 * math.pi * i / n),
                  b * math.sin(2 * math.pi * i / n)) for i in range(n)]]

    if shape == "square":
        s = dims["s"]
        return [[(-s / 2, -s / 2), (s / 2, -s / 2), (s / 2, s / 2), (-s / 2, s / 2)]]

    if shape == "rectangle":
        w, h = dims["w"], dims["h"]
        return [[(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]]

    if shape == "stadium":
        w, h, r = dims["w"], dims["h"], dims["r"]
        r = max(0.0, min(r, min(w, h) / 2.0))
        if r <= 0.0:
            return [[(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]]
        cx_off, cy_off = w / 2 - r, h / 2 - r
        seg = 8
        ring = []
        # four corner arcs, walked so the straight edges join arc endpoints
        for ccx, ccy, a0 in ((cx_off, cy_off, 0), (-cx_off, cy_off, 90),
                             (-cx_off, -cy_off, 180), (cx_off, -cy_off, 270)):
            for k in range(seg + 1):
                a = math.radians(a0 + 90.0 * k / seg)
                ring.append((ccx + r * math.cos(a), ccy + r * math.sin(a)))
        return [ring]

    if shape == "right_triangle":
        a, b = dims["a"], dims["b"]          # base, height
        verts = [(0.0, 0.0), (a, 0.0), (0.0, b)]
        gx, gy = a / 3.0, b / 3.0            # centre on the centroid
        return [[(x - gx, y - gy) for x, y in verts]]

    if shape in ("triangle", "regular"):
        n = 3 if shape == "triangle" else int(dims["n"])
        n = max(3, n)
        circumradius = dims["s"] / (2 * math.sin(math.pi / n))
        off = math.pi / 2  # point-up
        return [[(circumradius * math.cos(off + 2 * math.pi * i / n),
                  circumradius * math.sin(off + 2 * math.pi * i / n))
                 for i in range(n)]]

    if shape == "annulus":
        ro = dims["d"] / 2.0
        ri = max(0.1, ro - dims["w"])
        n = 64
        outer = [(ro * math.cos(2 * math.pi * i / n),
                  ro * math.sin(2 * math.pi * i / n)) for i in range(n)]
        inner = [(ri * math.cos(2 * math.pi * i / n),
                  ri * math.sin(2 * math.pi * i / n)) for i in range(n)]
        return [outer, inner[::-1]]          # hole wound opposite to exterior

    raise ValueError("unknown shape: %s" % shape)


def shape_rings(center, shape, dims, rot_deg=0.0):
    """Return rings of QgsPointXY for *shape*, centred on *center* and rotated.

    First ring is the exterior; any further rings are holes (the annulus).
    Feed straight into QgsGeometry.fromPolygonXY(rings).
    """
    cx, cy = center.x(), center.y()
    c, sn = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    out = []
    for ring in _local_ring(shape, dims):
        out.append([QgsPointXY(cx + x * c - y * sn, cy + x * sn + y * c)
                    for x, y in ring])
    return out


class _ShapePreview(QWidget):
    """Tiny widget that paints the current shape (with hole, if any), scaled
    to fit, and a width × height caption.  Redrawn whenever a dimension or the
    rotation changes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rings = []          # list of list of (x, y) metres, centred
        self.setMinimumHeight(110)
        self.setMaximumHeight(130)

    def set_rings(self, rings):
        self._rings = rings or []
        self.update()

    def paintEvent(self, _e):
        from qgis.PyQt.QtGui import QPainter, QPen, QBrush, QPolygonF, QPainterPath
        from qgis.PyQt.QtCore import QPointF

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.rect()
        p.fillRect(full, QColor("#fbfdfb"))
        p.setPen(QPen(QColor("#E3E7E1"), 1))
        p.drawRect(full.adjusted(0, 0, -1, -1))

        if not self._rings or len(self._rings[0]) < 2:
            return

        xs = [x for ring in self._rings for x, _ in ring]
        ys = [y for ring in self._rings for _, y in ring]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        w = max(maxx - minx, 1e-6)
        h = max(maxy - miny, 1e-6)

        box = full.adjusted(10, 8, -10, -22)
        scale = min(box.width() / w, box.height() / h)
        ox, oy = box.center().x(), box.center().y()
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0

        def to_px(x, y):
            return QPointF(ox + (x - cx) * scale, oy - (y - cy) * scale)  # y up

        path = QPainterPath()
        for ring in self._rings:
            sub = QPainterPath()
            sub.addPolygon(QPolygonF([to_px(x, y) for x, y in ring]))
            sub.closeSubpath()
            path.addPath(sub)
        path.setFillRule(Qt.FillRule.OddEvenFill)      # renders the annulus hole
        p.setPen(QPen(QColor(27, 94, 32), 2))
        p.setBrush(QBrush(QColor(46, 125, 50, 45)))
        p.drawPath(path)

        p.setPen(QColor("#5F6F65"))
        cap = full.adjusted(4, 0, -4, -4)
        p.drawText(cap, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, "%.1f × %.1f m" % (w, h))


class _HabitatShapeTool(QgsMapTool):
    """Stamp a single fixed-size shape as one habitat polygon.

    The ghost outline follows the cursor; the mouse wheel rotates it.
    Left-click places the shape; right-click cancels.  All parameters are
    pulled live from the form via *get_params*, so the numeric inputs stay
    the single source of truth.
    """

    def __init__(self, canvas, get_params, bump_rotation,
                 on_finished_cb, on_cancel_cb=None, layer_crs=None,
                 bump_dimension=None):
        super().__init__(canvas)
        self._get_params    = get_params      # -> (shape, dims_dict, rot_deg)
        self._bump_rotation = bump_rotation   # callback: add degrees to the spinner
        self._bump_dimension = bump_dimension # callback(axis, delta): nudge a dim
        self._on_finished   = on_finished_cb
        self._on_cancel     = on_cancel_cb
        self._last_pt       = None
        self._layer_crs     = layer_crs       # the layer's actual CRS

        # The shape's dimensions are in real metres, so it must be built in a
        # CRS whose units are true ground metres. The map/layer CRS is often
        # Web Mercator (EPSG:3857), whose "metre" is only ~cos(latitude) of a
        # real metre over the UK — building there shrinks every shape (a 10 m
        # square placed near London came out 6.2 m). We build in British
        # National Grid (EPSG:27700, true metres in the UK) and transform the
        # finished geometry out to the layer/canvas CRS.
        from qgis.core import QgsCoordinateReferenceSystem
        self._metric_crs = QgsCoordinateReferenceSystem("EPSG:27700")

        self._ghost = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._ghost.setColor(QColor(46, 125, 50, 180))
        self._ghost.setFillColor(QColor(46, 125, 50, 30))
        self._ghost.setLineStyle(Qt.PenStyle.DashLine)   # ghosted outline
        self._ghost.setWidth(2)

    def _canvas_crs(self):
        return self.canvas().mapSettings().destinationCrs()

    def _xform(self, src, dst):
        from qgis.core import QgsCoordinateTransform
        return QgsCoordinateTransform(src, dst, QgsProject.instance())

    def _rings(self, canvas_pt):
        """Return rings built in true metres (EPSG:27700), centred on the click
        point. Dimensions here are real ground metres regardless of what the
        map/layer projection is."""
        metric_pt = self._xform(self._canvas_crs(), self._metric_crs).transform(canvas_pt)
        return shape_rings(metric_pt, *self._get_params())

    def _rings_to(self, rings, dst_crs):
        """Transform metric (27700) rings to another CRS."""
        if dst_crs is None or dst_crs == self._metric_crs:
            return rings
        tr = self._xform(self._metric_crs, dst_crs)
        return [[tr.transform(p) for p in ring] for ring in rings]

    def _render(self, pt):
        self._ghost.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        if pt is None:
            return
        try:
            rings = self._rings(pt)                       # true-metre CRS
        except (KeyError, ValueError, ZeroDivisionError):
            return
        # Ghost is drawn on the canvas, so convert to canvas CRS for display
        geom = QgsGeometry.fromPolygonXY(self._rings_to(rings, self._canvas_crs()))
        self._ghost.setToGeometry(geom, None)

    def canvasMoveEvent(self, e):
        # Arrow keys only reach a map tool when the *canvas* holds keyboard
        # focus. Without this, the side-panel shape combo keeps focus and the
        # up/down arrows cycle the shape type instead of resizing. Grabbing
        # focus as the mouse moves over the map makes "hover, then press the
        # arrows to match a size" work as expected.
        if not self.canvas().hasFocus():
            self.canvas().setFocus()
        self._last_pt = self.toMapCoordinates(e.pos())
        self._render(self._last_pt)

    def wheelEvent(self, e):
        step = 1 if (e.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 15
        self._bump_rotation(step if e.angleDelta().y() > 0 else -step)
        self._render(self._last_pt)
        e.accept()

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._last_pt is not None:
            try:
                rings = self._rings(self._last_pt)
            except (KeyError, ValueError, ZeroDivisionError):
                return
            self._ghost.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            if rings and len(rings[0]) >= 3:
                self._on_finished(rings)
        elif e.button() == Qt.MouseButton.RightButton and self._on_cancel:
            self._ghost.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            self._on_cancel()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._ghost.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            if self._on_cancel:
                self._on_cancel()

    def activate(self):
        super().activate()
        # Ensure the canvas can hold keyboard focus and take it now, so the
        # arrow-key resize works without the user first clicking the map.
        try:
            self.canvas().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.canvas().setFocus()
            # Filter key events on the canvas *before* it handles them, so the
            # arrow keys resize the shape instead of panning the map.
            self.canvas().installEventFilter(self)
            self.canvas().viewport().installEventFilter(self)
        except Exception:
            pass

    def handle_key_event(self, event):
        """Resize the ghost from an arrow-key press; return True if it was an
        arrow key and was consumed.  Shared by the canvas event filter (used by
        the habitat form and the Building & Tree placer, where the canvas can
        hold focus) and by the Feature Mapper dialog's app-level filter — that
        dialog is a separate top-level window which steals key focus from the
        canvas, so the arrow keys must be forwarded to the tool explicitly."""
        from qgis.PyQt.QtCore import QEvent
        if (event.type() == QEvent.Type.KeyPress
                and self._bump_dimension is not None
                and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down,
                                     Qt.Key.Key_Left, Qt.Key.Key_Right)):
            step = 0.1 if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 1.0
            k = event.key()
            if k == Qt.Key.Key_Up:
                self._bump_dimension(1, step)
            elif k == Qt.Key.Key_Down:
                self._bump_dimension(1, -step)
            elif k == Qt.Key.Key_Right:
                self._bump_dimension(0, step)
            elif k == Qt.Key.Key_Left:
                self._bump_dimension(0, -step)
            self._render(self._last_pt)
            return True   # consume — stops the canvas from panning
        return False

    def eventFilter(self, obj, event):
        if self.handle_key_event(event):
            return True
        return super().eventFilter(obj, event)

    def deactivate(self):
        try:
            self.canvas().removeEventFilter(self)
            self.canvas().viewport().removeEventFilter(self)
        except Exception:
            pass
        self._ghost.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        super().deactivate()


def _dim_step_btn(icon_name, slot):
    """A small themed −/+ button for a dimension stepper (shared by ShapeControls
    and the habitat form's inline shape settings)."""
    b = QPushButton()
    b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    b.setFixedSize(34, 30)
    b.setIcon(theme.icon(icon_name, theme.ACCENT_DARK, 13))
    b.setIconSize(QSize(13, 13))
    b.setAutoRepeat(True)          # press-and-hold keeps stepping
    b.setAutoRepeatDelay(300)
    b.setAutoRepeatInterval(80)
    b.setStyleSheet(
        "QPushButton { background:%s; border:1px solid %s; border-radius:8px; }"
        "QPushButton:hover { background:%s; border-color:%s; }"
        "QPushButton:pressed { background:%s; }"
        % (theme.SUNKEN, theme.BORDER_STRONG, theme.ACCENT_SOFT,
           theme.ACCENT, theme.BORDER)
    )
    b.clicked.connect(slot)
    return b


def _dim_stepper(spin):
    """Wrap a spin box as three self-contained rounded controls — [−] [ value ]
    [+] — hiding its native arrows, so a dimension can be nudged with one click
    (or a press-and-hold). Returns the row widget; the spin is reparented into
    it but keeps its identity, so callers still read/write the same spin."""
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
    spin.setMinimumHeight(30)
    spin.setStyleSheet(
        "QDoubleSpinBox, QSpinBox { border:1px solid %s; border-radius:8px;"
        " background:%s; color:%s; padding:2px 8px; }"
        "QDoubleSpinBox:focus, QSpinBox:focus { border-color:%s; }"
        % (theme.BORDER_STRONG, theme.CARD, theme.INK, theme.ACCENT)
    )
    row = QWidget()
    hl = QHBoxLayout(row)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)
    hl.addWidget(_dim_step_btn("minus", spin.stepDown))
    hl.addWidget(spin, 1)
    hl.addWidget(_dim_step_btn("plus", spin.stepUp))
    return row


class ShapeControls(QWidget):
    """Reusable shape chooser: shape combo + metre dimension inputs + sides +
    rotation + a live preview.  Used by the Building & Tree Mapper.  (The habitat
    form builds an equivalent inline UI sharing the _dim_stepper helper above.)
    Exposes params() and bump_rotation()."""

    _SPEC = {
        # key: (dim1 label, dim2 label or None, dim3 label or None, show sides?)
        "circle":         ("Diameter (m):",     None,              None,                 False),
        "ellipse":        ("Long axis (m):",     "Short axis (m):", None,                 False),
        "square":         ("Side (m):",          None,              None,                 False),
        "rectangle":      ("Width (m):",         "Height (m):",     None,                 False),
        "stadium":        ("Width (m):",         "Height (m):",     "Corner radius (m):", False),
        "triangle":       ("Side length (m):",   None,              None,                 False),
        "right_triangle": ("Base (m):",          "Height (m):",     None,                 False),
        "regular":        ("Side length (m):",   None,              None,                 True),
        "annulus":        ("Outer diameter (m):", "Ring width (m):", None,                False),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        lay.addWidget(QLabel("Shape:"))
        self.combo = QComboBox()
        for txt, key in [
            ("Circle", "circle"), ("Ellipse / oval", "ellipse"), ("Square", "square"),
            ("Rectangle", "rectangle"), ("Stadium / rounded rect", "stadium"),
            ("Triangle (equilateral)", "triangle"), ("Right triangle", "right_triangle"),
            ("Regular polygon", "regular"), ("Annulus (ring)", "annulus"),
        ]:
            self.combo.addItem(txt, key)
        lay.addWidget(self.combo)

        def _mk_spin(default):
            sp = QDoubleSpinBox()
            sp.setRange(0.1, 100000.0)
            sp.setDecimals(1)
            sp.setSingleStep(1.0)
            sp.setSuffix(" m")
            sp.setValue(default)
            return sp

        self.dim1_label = QLabel("Diameter (m):"); self.dim1_spin = _mk_spin(10.0)
        self.dim2_label = QLabel("Short axis (m):"); self.dim2_spin = _mk_spin(10.0)
        self.dim3_label = QLabel("Corner radius (m):"); self.dim3_spin = _mk_spin(3.0)
        self.dim1_row = self._stepper(self.dim1_spin)
        self.dim2_row = self._stepper(self.dim2_spin)
        self.dim3_row = self._stepper(self.dim3_spin)
        for w in (self.dim1_label, self.dim1_row, self.dim2_label, self.dim2_row,
                  self.dim3_label, self.dim3_row):
            lay.addWidget(w)

        self.sides_label = QLabel("Number of sides:")
        self.sides_spin = QSpinBox(); self.sides_spin.setRange(3, 20); self.sides_spin.setValue(6)
        self.sides_row = self._stepper(self.sides_spin)
        lay.addWidget(self.sides_label); lay.addWidget(self.sides_row)

        lay.addWidget(QLabel("Rotation (°):"))
        self.rot_spin = QSpinBox(); self.rot_spin.setRange(0, 359)
        self.rot_spin.setWrapping(True); self.rot_spin.setSuffix(" °")
        self.rot_spin.setSingleStep(5)
        self.rot_row = self._stepper(self.rot_spin)
        lay.addWidget(self.rot_row)

        self.preview = _ShapePreview()
        lay.addWidget(self.preview)
        self.setLayout(lay)

        self.combo.currentIndexChanged.connect(self._on_combo)
        for sp in (self.dim1_spin, self.dim2_spin, self.dim3_spin):
            sp.valueChanged.connect(self._refresh)
        self.sides_spin.valueChanged.connect(self._refresh)
        self.rot_spin.valueChanged.connect(self._refresh)
        self._on_combo(0)

    # ── Themed −/+ steppers ───────────────────────────────────────────────────
    def _stepper(self, spin):
        return _dim_stepper(spin)

    def key(self):
        return self.combo.currentData() or "circle"

    def _on_combo(self, _idx=0):
        d1, d2, d3, show_sides = self._SPEC.get(self.key(),
                                                ("Size (m):", None, None, False))
        self.dim1_label.setText(d1)
        self.dim2_label.setText(d2 or ""); self.dim2_label.setVisible(d2 is not None)
        self.dim2_row.setVisible(d2 is not None)
        self.dim3_label.setText(d3 or ""); self.dim3_label.setVisible(d3 is not None)
        self.dim3_row.setVisible(d3 is not None)
        self.sides_label.setVisible(show_sides); self.sides_row.setVisible(show_sides)
        self._refresh()

    def params(self):
        """Return (shape_key, dims_dict, rotation_deg)."""
        key = self.key()
        d1, d2, d3 = self.dim1_spin.value(), self.dim2_spin.value(), self.dim3_spin.value()
        if key == "circle":
            dims = {"d": d1}
        elif key == "ellipse":
            dims = {"a": d1, "b": d2}
        elif key == "rectangle":
            dims = {"w": d1, "h": d2}
        elif key == "stadium":
            dims = {"w": d1, "h": d2, "r": d3}
        elif key == "right_triangle":
            dims = {"a": d1, "b": d2}
        elif key == "regular":
            dims = {"s": d1, "n": self.sides_spin.value()}
        elif key == "annulus":
            dims = {"d": d1, "w": d2}
        else:
            dims = {"s": d1}
        return (key, dims, float(self.rot_spin.value()))

    def bump_rotation(self, deg):
        self.rot_spin.setValue(int((self.rot_spin.value() + deg) % 360))

    def bump_dimension(self, axis, delta):
        """Nudge a dimension spinner (arrow-key resize on the map tool).
        axis 0 = primary dimension (width / diameter / side),
        axis 1 = secondary dimension (height / short axis) when the shape has
        one; otherwise both axes fall back to the primary dimension."""
        if axis == 1 and self.dim2_spin.isVisible():
            target = self.dim2_spin
        else:
            target = self.dim1_spin
        lo, hi = target.minimum(), target.maximum()
        target.setValue(max(lo, min(hi, round(target.value() + delta, 1))))

    def _refresh(self, *_):
        try:
            key, dims, rot = self.params()
            rings = shape_rings(QgsPointXY(0.0, 0.0), key, dims, rot)
            local = [[(p.x(), p.y()) for p in ring] for ring in rings]
        except Exception:
            local = []
        self.preview.set_rings(local)


class HabitatFormScreen(QWidget):
    """
    Habitat drawing form — works for both baseline and proposed phases.

    phase == "baseline": draws into baseline layer, mirrors a copy to proposed.
    phase == "proposed": draws directly into proposed layer only.

    Every placed feature is pushed onto the parent BNGMainPanel's global
    undo stack so the ↩ Undo button always works, regardless of which
    screen is visible.

    FID STRATEGY
    ------------
    QGIS assigns a temporary negative fid inside the edit buffer.  After
    commitChanges() the feature gets a new permanent fid.  We therefore
    never store the buffer fid; instead we call _last_fid(layer) right
    after commit to retrieve the highest (most recently written) fid.
    """

    def __init__(self, iface, layer_manager, habitat_key, phase, title,
                 parent=None, proposed_leads_baseline=False):
        super().__init__(parent)
        self.iface                     = iface
        self.layer_manager             = layer_manager
        self.habitat_key               = habitat_key
        self.phase                     = phase
        self.title                     = title
        self._main_panel               = parent
        self._active_layer             = None
        self._tree_mode                = False
        self._tree_committing          = False   # guard: ignore canvas events during commit/re-arm
        self._canvas_event_filter      = None    # installed while in tree mode
        self._proposed_leads_baseline  = proposed_leads_baseline
        self._shape_tool               = None
        self._shape_continuous         = False   # True while a multi-shape session is open
        self._shape_busy               = False   # guard: ignore clicks during a commit
        self._live_measure_filter      = None    # cursor length label while drawing lines
        self._build_ui()

    def keyPressEvent(self, event):
        """Escape while placing trees = Done Placing Trees."""
        if event.key() == Qt.Key.Key_Escape and self._tree_mode:
            self._finish_tree_session()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    #  UI                                                                  #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── Outer scroll area so panel doesn't get cut off ────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        inner = QWidget()
        # Force the bundled Inter family on the whole form. (Setting only the
        # family leaves per-widget font-sizes untouched and never affects the
        # combo box-model, unlike a QWidget{font-family} stylesheet would.)
        _inter = QFont()
        _inter.setFamily("Inter")
        inner.setFont(_inter)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        is_proposed_only      = (self.phase == "proposed")
        is_trees              = (self.habitat_key == "trees")
        is_water              = (self.habitat_key == "watercourse")
        is_reverse            = self._proposed_leads_baseline and self.phase == "proposed"

        # (Residential-curtilage warnings now render at the BOTTOM of the form —
        # built just before layout.addStretch(); see further down.)

        # ── "Mapping OFF" indicator for baseline forms ────────────────
        _proposed_on = getattr(self._main_panel, "_proposed_mapping", True) if self._main_panel else True
        if not _proposed_on and not is_proposed_only and not is_reverse:
            off_frame = QFrame()
            off_frame.setStyleSheet(
                "QFrame { background:#EBF2F8; border:1px solid #CFE0EE; border-radius:8px; }"
            )
            off_lay = QHBoxLayout(off_frame)
            off_lay.setContentsMargins(10, 6, 10, 6)
            off_lay.setSpacing(8)
            off_ic = QLabel()
            off_ic.setFixedSize(15, 15)
            off_ic.setPixmap(theme.icon("info-circle", "#2F5D8A", 15).pixmap(QSize(15, 15)))
            off_ic.setStyleSheet("background:transparent; border:none;")
            off_lbl = QLabel("Baseline only — proposed mapping is off")
            off_lbl.setStyleSheet("font-size:11px; color:#2F5D8A; border:none; background:transparent;")
            off_lbl.setWordWrap(True)
            off_lay.addWidget(off_ic, 0, Qt.AlignmentFlag.AlignVCenter)
            off_lay.addWidget(off_lbl, 1)
            layout.addWidget(off_frame)

        # ── Main habitat details ───────────────────────────────────────
        if is_reverse:
            grp_title = "Proposed Habitat Details"
        elif is_proposed_only:
            grp_title = "Proposed Habitat Details"
        else:
            grp_title = "Baseline Habitat Details"
        grp = QGroupBox(grp_title)
        grp.setStyleSheet("QGroupBox { font-weight: 500; }")
        gl = QVBoxLayout()
        gl.setSpacing(6)

        self.habitat_type_label = QLabel("Habitat Type:")
        gl.addWidget(self.habitat_type_label)
        self.habitat_type_combo = QComboBox()
        self._populate_types(self.habitat_type_combo, screen_key=self.habitat_key)
        self.habitat_type_combo.currentTextChanged.connect(
            lambda _: self._populate_subs(self.habitat_type_combo, self.sub_combo)
        )
        gl.addWidget(self.habitat_type_combo)
        single = len(self.habitat_type_combo) <= 1
        # Only ever HIDE during construction. Calling setVisible(True) on a widget
        # that is still in a detached layout (no parent yet) flashes it up as its
        # own top-level window before it gets reparented — which also churns native
        # windows and lags. Leaving it untouched keeps the default (shows on parent).
        if single:
            self.habitat_type_label.setVisible(False)
            self.habitat_type_combo.setVisible(False)

        # For trees: split into Size + Location dropdowns that derive sub_type.
        # For everything else: single flat sub-type combo as before.
        self._is_trees_form = is_trees

        if is_trees:
            gl.addWidget(QLabel("Tree Size:"))
            self.tree_size_combo = QComboBox()
            self.tree_size_combo.addItems(["Small", "Medium", "Large", "Very Large"])
            gl.addWidget(self.tree_size_combo)

            gl.addWidget(QLabel("Tree Location:"))
            self.tree_location_combo = QComboBox()
            self.tree_location_combo.addItems(["Urban", "Rural"])
            gl.addWidget(self.tree_location_combo)

            # Hidden sub_combo keeps the rest of the form logic working unchanged
            self.sub_combo = QComboBox()
            self.sub_combo.setVisible(False)
            self.sub_combo.currentTextChanged.connect(self._on_sub_changed)
            gl.addWidget(self.sub_combo)

            # Sync hidden sub_combo whenever size or location changes
            def _sync_tree_sub():
                size = self.tree_size_combo.currentText()
                loc  = self.tree_location_combo.currentText()
                derived = f"{size} {loc} Tree"
                idx = self.sub_combo.findText(derived)
                if idx >= 0:
                    self.sub_combo.setCurrentIndex(idx)
            self._sync_tree_sub = _sync_tree_sub
            self.tree_size_combo.currentTextChanged.connect(lambda _: _sync_tree_sub())
            self.tree_location_combo.currentTextChanged.connect(lambda _: _sync_tree_sub())
        else:
            gl.addWidget(QLabel("Sub Habitat Type:"))
            self.sub_combo = QComboBox()
            self.sub_combo.currentTextChanged.connect(self._on_sub_changed)
            gl.addWidget(self.sub_combo)

        gl.addWidget(QLabel("Condition Assessment:"))
        self.condition_combo = QComboBox()
        self.condition_combo.addItems(CONDITION_OPTIONS)
        gl.addWidget(self.condition_combo)

        # When the metric fixes the condition for a sub-type (N/A, or a forced
        # grade) the combo is locked — this muted line says why, so a greyed
        # dropdown never just looks broken.
        self._cond_lock_note = QLabel("")
        self._cond_lock_note.setWordWrap(True)
        self._cond_lock_note.setVisible(False)
        self._cond_lock_note.setStyleSheet(
            f"color:{theme.FAINT}; font-size:11px; background:transparent; border:none;")
        gl.addWidget(self._cond_lock_note)

        # ── Condition Assessment Helper ────────────────────────────────
        # Collapsible panel showing statutory criteria for the selected
        # sub-type.  Toggled by a small "?" button; updates on sub change.

        self._cond_helper_open = False
        self._cond_toggle_btn = QPushButton("Condition assessment")
        self._cond_toggle_btn.setIcon(theme.icon("clipboard-list", theme.ACCENT_DARK, 14))
        self._cond_toggle_btn.setIconSize(QSize(14, 14))
        self._cond_toggle_btn.setMinimumHeight(28)
        self._cond_toggle_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.ACCENT_SOFT}; color:{theme.ACCENT_DARK};"
            f" border:1px solid {theme.BORDER_STRONG}; border-radius:6px;"
            f" font-family:{theme.FONT_MEDIUM}; font-size:12px; font-weight:500; padding:5px 10px; }}"
            f" QPushButton:hover {{ background:#DCEBE0; border-color:{theme.ACCENT}; }}")
        self._cond_toggle_btn.clicked.connect(self._toggle_cond_helper)
        gl.addWidget(self._cond_toggle_btn)

        self._cond_helper_frame = QFrame()
        self._cond_helper_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._cond_helper_frame.setObjectName("condHelper")
        self._cond_helper_frame.setStyleSheet(
            f"QFrame#condHelper {{ background:{theme.CARD}; border:1px solid {theme.BORDER};"
            " border-radius:4px; }}"
        )
        self._cond_helper_layout = QVBoxLayout()
        self._cond_helper_layout.setContentsMargins(8, 6, 8, 6)
        self._cond_helper_layout.setSpacing(4)
        self._cond_helper_frame.setLayout(self._cond_helper_layout)
        self._cond_helper_frame.setVisible(False)
        gl.addWidget(self._cond_helper_frame)

        # Retention category is no longer entered by hand — it is derived
        # automatically: areas from the spatial net of baseline vs proposed
        # polygons, and hedgerows / trees / watercourses from pair_id linking
        # (auto-retained on placement) plus presence. See _on_tree_added /
        # _on_feature_added for where the stored value is set.

        grp.setLayout(gl)
        layout.addWidget(grp)

        # ── Proposed-change / Baseline-details group ─────────────────
        _prop_grp_title = "Baseline Habitat Details" if is_reverse else "Proposed Habitat Change"
        _prop_grp_color = "#2E7D32" if is_reverse else "#2F5D8A"
        self.proposed_group = QGroupBox(_prop_grp_title)
        self.proposed_group.setStyleSheet(
            f"QGroupBox {{ font-weight: 500; color: {_prop_grp_color}; }}"
        )
        pg = QVBoxLayout()
        pg.setSpacing(6)

        self.prop_type_label = QLabel("Proposed Habitat Type:")
        pg.addWidget(self.prop_type_label)
        self.prop_type_combo = QComboBox()
        self._populate_types(self.prop_type_combo, screen_key=self.habitat_key)
        self.prop_type_combo.currentTextChanged.connect(
            lambda _: self._populate_subs(self.prop_type_combo, self.prop_sub_combo)
        )
        pg.addWidget(self.prop_type_combo)
        single = len(self.prop_type_combo) <= 1
        if single:
            self.prop_type_label.setVisible(False)
            self.prop_type_combo.setVisible(False)

        if not is_trees:
            pg.addWidget(QLabel("Proposed Sub Habitat Type:"))

        if is_trees:
            pg.addWidget(QLabel("Proposed Tree Size:"))
            self.prop_tree_size_combo = QComboBox()
            self.prop_tree_size_combo.addItems(["Small", "Medium", "Large", "Very Large"])
            pg.addWidget(self.prop_tree_size_combo)

            pg.addWidget(QLabel("Proposed Tree Location:"))
            self.prop_tree_location_combo = QComboBox()
            self.prop_tree_location_combo.addItems(["Urban", "Rural"])
            pg.addWidget(self.prop_tree_location_combo)

            self.prop_sub_combo = QComboBox()
            self.prop_sub_combo.setVisible(False)
            self._populate_subs(self.prop_type_combo, self.prop_sub_combo)
            pg.addWidget(self.prop_sub_combo)

            def _sync_prop_tree_sub():
                size = self.prop_tree_size_combo.currentText()
                loc  = self.prop_tree_location_combo.currentText()
                derived = f"{size} {loc} Tree"
                idx = self.prop_sub_combo.findText(derived)
                if idx >= 0:
                    self.prop_sub_combo.setCurrentIndex(idx)
            self._sync_prop_tree_sub = _sync_prop_tree_sub
            self.prop_tree_size_combo.currentTextChanged.connect(lambda _: _sync_prop_tree_sub())
            self.prop_tree_location_combo.currentTextChanged.connect(lambda _: _sync_prop_tree_sub())
        else:
            self.prop_sub_combo = QComboBox()
            # Populate immediately so the first sub-type is available by default
            self._populate_subs(self.prop_type_combo, self.prop_sub_combo)
            pg.addWidget(self.prop_sub_combo)

        # Connect proposed sub-type changes to condition/criteria logic
        self.prop_sub_combo.currentTextChanged.connect(self._on_prop_sub_changed)

        pg.addWidget(QLabel("Proposed Condition:"))
        self.prop_condition_combo = QComboBox()
        self.prop_condition_combo.addItems(CONDITION_OPTIONS)
        pg.addWidget(self.prop_condition_combo)

        # ── Proposed condition criteria helper ─────────────────────────
        self._prop_cond_helper_open = False
        self._prop_cond_toggle_btn = QPushButton("Condition assessment")
        self._prop_cond_toggle_btn.setIcon(theme.icon("clipboard-list", "#234A6E", 14))
        self._prop_cond_toggle_btn.setIconSize(QSize(14, 14))
        self._prop_cond_toggle_btn.setMinimumHeight(28)
        self._prop_cond_toggle_btn.setStyleSheet("""
            QPushButton {
                background: #EBF2F8; color: #234A6E;
                border: 1px solid #90caf9; border-radius: 4px;
                font-size: 11px; font-weight: 500; padding: 2px 8px;
            }
            QPushButton:hover { background: #D7E5F0; }
        """)
        self._prop_cond_toggle_btn.setVisible(False)
        self._prop_cond_toggle_btn.clicked.connect(self._toggle_prop_cond_helper)
        pg.addWidget(self._prop_cond_toggle_btn)

        self._prop_cond_helper_frame = QFrame()
        self._prop_cond_helper_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._prop_cond_helper_frame.setStyleSheet(
            "QFrame { background: #F9FAF8; border: 1px solid #90caf9;"
            " border-radius: 4px; }"
        )
        self._prop_cond_helper_layout = QVBoxLayout()
        self._prop_cond_helper_layout.setContentsMargins(8, 6, 8, 6)
        self._prop_cond_helper_layout.setSpacing(4)
        self._prop_cond_helper_frame.setLayout(self._prop_cond_helper_layout)
        self._prop_cond_helper_frame.setVisible(False)
        pg.addWidget(self._prop_cond_helper_frame)

        self.proposed_group.setLayout(pg)
        self.proposed_group.setVisible(False)
        layout.addWidget(self.proposed_group)

        # ── BNG Metric extra fields (collapsible — optional) ───────────
        # Expanded by default so the fields stay in view, but collapsible to
        # tuck the optional inputs away.
        bng_card = QFrame()
        bng_card.setObjectName("bngCard")
        bng_card.setStyleSheet(
            "QFrame#bngCard { background:#FFFFFF; border:1px solid #E3E7E1;"
            " border-radius:8px; }")
        bcl = QVBoxLayout(bng_card)
        bcl.setContentsMargins(0, 0, 0, 0)
        bcl.setSpacing(0)

        self._bng_open = True
        self._bng_toggle = QPushButton("BNG metric details (optional)")
        self._bng_toggle.setMinimumHeight(36)
        self._bng_toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._bng_toggle.setStyleSheet(
            "QPushButton { background:transparent; border:none; text-align:left;"
            f" color:{theme.INK}; font-family:{theme.FONT_MEDIUM};"
            " font-size:12px; font-weight:500; padding:0 12px; }"
            " QPushButton:hover { background:#F3F7F2; }")
        self._bng_toggle.clicked.connect(self._toggle_bng_details)
        bcl.addWidget(self._bng_toggle)

        self._bng_body = QWidget()
        eg = QVBoxLayout(self._bng_body)
        eg.setContentsMargins(12, 2, 12, 12)
        eg.setSpacing(6)

        eg.addWidget(QLabel("Strategic Significance:"))
        self.strategic_sig_combo = QComboBox()
        self.strategic_sig_combo.addItems(STRATEGIC_SIGNIFICANCE_OPTIONS)
        # Default to "no local strategy" — the common case and the metric's own
        # default. The warning note below prompts the user to change it where the
        # site sits within a Local Nature Recovery Strategy area.
        _ss_idx = self.strategic_sig_combo.findText(DEFAULT_STRATEGIC_SIGNIFICANCE)
        if _ss_idx >= 0:
            self.strategic_sig_combo.setCurrentIndex(_ss_idx)
        eg.addWidget(self.strategic_sig_combo)

        eg.addWidget(QLabel("Irreplaceable Habitat:"))
        self.irreplaceable_combo = QComboBox()
        self.irreplaceable_combo.addItems(["", "No", "Yes"])
        # Default to "No"; _on_sub_changed promotes this to "Yes" for habitat
        # types on the statutory irreplaceable list.
        _irr_idx = self.irreplaceable_combo.findText("No")
        if _irr_idx >= 0:
            self.irreplaceable_combo.setCurrentIndex(_irr_idx)
        eg.addWidget(self.irreplaceable_combo)

        # Neither field can be inferred from habitat type alone, so both are
        # defaulted to the common case and flagged here — rather than offered as a
        # one-click "apply defaults" button — so the user always sees the
        # assumption and knows to override it when it doesn't hold.
        self._sig_default_note = QLabel(
            "Using defaults — no strategic significance, not irreplaceable. "
            "Change the two fields above if the site is within a Local Nature "
            "Recovery Strategy area, or the habitat is irreplaceable."
        )
        self._sig_default_note.setWordWrap(True)
        self._sig_default_note.setStyleSheet(
            f"QLabel {{ color:{theme.MUTED}; background:transparent; border:none;"
            f" padding:2px 2px; font-size:11px; font-family:{theme.FONT}; }}")
        eg.addWidget(self._sig_default_note)

        # Watercourse-specific encroachment fields. Default to "No Encroachment"
        # (the common case) rather than the blank first item — a blank
        # encroachment makes the metric's C-1 unable to compute the reach's units
        # (it scores zero), the same way strategic significance and irreplaceable
        # are defaulted above. A culvert reach overrides these to "N/A - Culvert".
        self.water_enc_label = QLabel("Watercourse Encroachment:")
        self.water_enc_combo = QComboBox()
        self.water_enc_combo.addItems(WATERCOURSE_ENCROACHMENT_OPTIONS)
        self.rip_l_label     = QLabel("Riparian Encroachment (Left Bank):")
        self.rip_l_combo     = QComboBox()
        self.rip_l_combo.addItems(RIPARIAN_ENCROACHMENT_OPTIONS)
        self.rip_r_label     = QLabel("Riparian Encroachment (Right Bank):")
        self.rip_r_combo     = QComboBox()
        self.rip_r_combo.addItems(RIPARIAN_ENCROACHMENT_OPTIONS)
        for _enc_combo in (self.water_enc_combo, self.rip_l_combo, self.rip_r_combo):
            _enc_idx = _enc_combo.findText("No Encroachment")
            if _enc_idx >= 0:
                _enc_combo.setCurrentIndex(_enc_idx)
        for w in (self.water_enc_label, self.water_enc_combo,
                  self.rip_l_label,    self.rip_l_combo,
                  self.rip_r_label,    self.rip_r_combo):
            eg.addWidget(w)          # reparent first…
            w.setVisible(is_water)   # …then toggle, so True never spawns a window

        eg.addWidget(QLabel("Habitat Reference:"))
        self.hab_ref_input = QLineEdit()
        self.hab_ref_input.setPlaceholderText("Optional map cross-reference")
        self.hab_ref_input.setStyleSheet(
            "QLineEdit { border:1px solid #C9D1C9; border-radius:4px; padding:4px; }"
        )
        eg.addWidget(self.hab_ref_input)

        eg.addWidget(QLabel("User Comments:"))
        self.comments_edit = QTextEdit()
        self.comments_edit.setPlaceholderText("Evidence, justifications, notes…")
        self.comments_edit.setFixedHeight(60)
        self.comments_edit.setStyleSheet(
            "QTextEdit { border:1px solid #C9D1C9; border-radius:4px; padding:4px; font-size:11px; }"
        )
        eg.addWidget(self.comments_edit)

        bcl.addWidget(self._bng_body)            # reparent first…
        self._bng_body.setVisible(self._bng_open)  # …then toggle (no stray window)
        layout.addWidget(bng_card)
        self._sync_bng_toggle()

        # ── Clip to Redline option (area habitats only) ────────────────
        self.clip_redline_check = None
        if not is_trees and not is_water and self.habitat_key == "area":
            from qgis.PyQt.QtWidgets import QCheckBox as _QCB
            self.clip_redline_check = _QCB("Redline")
            self.clip_redline_check.setChecked(True)
            self.clip_redline_check.setToolTip(
                "When ticked, any polygon drawn outside the redline boundary\n"
                "will be clipped — only the portion inside the redline is saved."
            )
            self.clip_redline_check.setStyleSheet(
                "QCheckBox { font-size: 11px; color: #5F6F65; padding: 2px 0; }"
            )

        # ── Clip to Blue Line Boundary option (area habitats only) ─────
        # On by default.  When both boundary clips are selected their union is
        # used, allowing contiguous habitat mapping across the redline into
        # the wider blue-line ownership boundary.
        self.clip_blueline_check = None
        if not is_trees and not is_water and self.habitat_key == "area":
            from qgis.PyQt.QtWidgets import QCheckBox as _QCB
            self.clip_blueline_check = _QCB("Blue line")
            self.clip_blueline_check.setChecked(True)
            self.clip_blueline_check.setToolTip(
                "When ticked, any polygon drawn outside the Blue Line Boundary\n"
                "will be clipped — only the portion inside the blue line is saved.\n"
                "Use this when habitats extend beyond the redline into the wider\n"
                "ownership area. When Redline is also ticked, the two boundaries\n"
                "are combined so habitat can be placed anywhere inside either."
            )
            self.clip_blueline_check.setStyleSheet(
                "QCheckBox { font-size: 11px; color: #2F5D8A; padding: 2px 0; }"
            )

        # ── Cookie cutter option (area habitats only) ──────────────────
        # Off by default. When ticked the drawn shape takes priority over
        # whatever is already mapped beneath it: instead of trimming the new
        # polygon to fit around existing parcels, the finished (clipped)
        # footprint is punched OUT of every overlapping parcel on the layer.
        self.cookie_cutter_check = None
        if not is_trees and not is_water and self.habitat_key == "area":
            from qgis.PyQt.QtWidgets import QCheckBox as _QCB
            self.cookie_cutter_check = _QCB("Cookie cutter")
            self.cookie_cutter_check.setChecked(False)
            self.cookie_cutter_check.setToolTip(
                "When ticked, the shape you draw is punched out of any habitat\n"
                "parcels already underneath it — they are trimmed back (or\n"
                "removed when fully covered) and the new parcel takes that\n"
                "ground. When unticked, the new parcel only fills unoccupied\n"
                "ground. Works together with the Redline / Blue line clips:\n"
                "the punch uses the clipped shape."
            )
            self.cookie_cutter_check.setStyleSheet(
                "QCheckBox { font-size: 11px; color: #7A5C3E; padding: 2px 0; }"
            )

        self.draw_btn = QPushButton("Draw Feature on Map")
        self.draw_btn.setMinimumHeight(44)
        self.draw_btn.setIcon(theme.icon("pencil", "white", 16))
        self.draw_btn.setIconSize(QSize(16, 16))
        self.draw_btn.setToolTip("Start drawing this habitat on the map canvas.\nClick to place vertices, right-click or double-click to finish.")
        self.draw_btn.setStyleSheet("""
            QPushButton {
                background-color: #175E3B; color: white;
                border-radius: 6px; font-size: 13px; font-weight: 500;
            }
            QPushButton:hover { background-color: #1F7A4D; }
        """)
        self.draw_btn.clicked.connect(self._start_drawing)

        # Freehand toggle — only for area polygons (not trees, hedgerow, watercourse)
        if self.habitat_key == "area":
            self.freehand_btn = QPushButton("Freehand")
            self.freehand_btn.setCheckable(True)
            self.freehand_btn.setChecked(False)
            self.freehand_btn.setMinimumHeight(30)
            self.freehand_btn.setIcon(theme.icon("polygon", "#175E3B", 14))
            self.freehand_btn.setIconSize(QSize(14, 14))
            self.freehand_btn.setToolTip(
                "Freehand mode: hold left mouse button and drag to sketch the polygon.\n"
                "Release to finish. Best for organic shapes like ponds and woodland edges."
            )
            self.freehand_btn.setStyleSheet(
                "QPushButton { background:white; border:1px solid #A6AFA9;"
                " border-radius:5px; font-size:11px; color:#175E3B; padding:0 10px; }"
                "QPushButton:checked { background:#175E3B; color:white; border-color:#175E3B; }"
                "QPushButton:hover:!checked { background:#E9F4EC; }"
            )
            self.freehand_btn.toggled.connect(self._on_freehand_toggled)

            # ── Shape placement (occasional use — hidden behind this toggle) ──
            self._shape_btn = QPushButton("Shape")
            self._shape_btn.setCheckable(True)
            self._shape_btn.setChecked(False)
            self._shape_btn.setMinimumHeight(30)
            self._shape_btn.setIcon(theme.icon("circle-dashed", "#175E3B", 14))
            self._shape_btn.setIconSize(QSize(14, 14))
            self._shape_btn.setToolTip(
                "Shape mode: stamp a single fixed-size shape as one habitat.\n"
                "Move the mouse to position it, scroll to rotate (Shift = fine),\n"
                "left-click to place. Dimensions are set in metres below."
            )
            self._shape_btn.setStyleSheet(
                "QPushButton { background:white; border:1px solid #A6AFA9;"
                " border-radius:5px; font-size:11px; color:#175E3B; padding:0 10px; }"
                "QPushButton:checked { background:#175E3B; color:white; border-color:#175E3B; }"
                "QPushButton:hover:!checked { background:#E9F4EC; }"
            )
            self._shape_btn.toggled.connect(self._on_shape_toggled)

            # Settings group — only visible while Shape mode is on.
            self._shape_group = QGroupBox("Shape settings")
            self._shape_group.setStyleSheet(
                "QGroupBox { font-size:11px; font-weight:500; color:#175E3B;"
                " border:1px solid #D8ECDE; border-radius:5px; margin-top:6px; padding:6px; }"
                "QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 3px; }"
            )
            sg = QVBoxLayout()
            sg.setSpacing(5)

            sg.addWidget(QLabel("Shape:"))
            self._shape_combo = QComboBox()
            # (display text, internal key)
            for _txt, _key in [
                ("Circle",                    "circle"),
                ("Ellipse / oval",            "ellipse"),
                ("Square",                    "square"),
                ("Rectangle",                 "rectangle"),
                ("Stadium / rounded rect",    "stadium"),
                ("Triangle (equilateral)",    "triangle"),
                ("Right triangle",            "right_triangle"),
                ("Regular polygon",           "regular"),
                ("Annulus (ring)",            "annulus"),
            ]:
                self._shape_combo.addItem(_txt, _key)
            sg.addWidget(self._shape_combo)

            def _mk_spin(default):
                sp = QDoubleSpinBox()
                sp.setRange(0.1, 100000.0)
                sp.setDecimals(1)
                sp.setSingleStep(1.0)
                sp.setSuffix(" m")
                sp.setValue(default)
                return sp

            self._dim1_label = QLabel("Diameter (m):")
            self._dim1_spin  = _mk_spin(10.0)
            self._dim1_row   = _dim_stepper(self._dim1_spin)
            sg.addWidget(self._dim1_label)
            sg.addWidget(self._dim1_row)

            self._dim2_label = QLabel("Short axis (m):")
            self._dim2_spin  = _mk_spin(10.0)
            self._dim2_row   = _dim_stepper(self._dim2_spin)
            sg.addWidget(self._dim2_label)
            sg.addWidget(self._dim2_row)

            self._dim3_label = QLabel("Corner radius (m):")
            self._dim3_spin  = _mk_spin(3.0)
            self._dim3_row   = _dim_stepper(self._dim3_spin)
            sg.addWidget(self._dim3_label)
            sg.addWidget(self._dim3_row)

            self._sides_label = QLabel("Number of sides:")
            self._sides_spin  = QSpinBox()
            self._sides_spin.setRange(3, 20)
            self._sides_spin.setValue(6)
            self._sides_row   = _dim_stepper(self._sides_spin)
            sg.addWidget(self._sides_label)
            sg.addWidget(self._sides_row)

            sg.addWidget(QLabel("Rotation (°):"))
            self._rot_spin = QSpinBox()
            self._rot_spin.setRange(0, 359)
            self._rot_spin.setWrapping(True)
            self._rot_spin.setSuffix(" °")
            self._rot_spin.setSingleStep(5)
            self._rot_row = _dim_stepper(self._rot_spin)
            sg.addWidget(self._rot_row)

            # Live preview — redraws whenever any dimension/rotation changes.
            self._shape_preview = _ShapePreview()
            sg.addWidget(self._shape_preview)

            hint = QLabel("Scroll the mouse wheel over the map to rotate "
                          "(hold Shift for 1° steps).  Use the arrow keys to "
                          "resize while placing: ← / → adjust width, ↑ / ↓ "
                          "adjust height (hold Shift for 0.1 m steps).")
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#777; font-size:10px;")
            sg.addWidget(hint)

            self._shape_group.setLayout(sg)
            self._shape_group.setVisible(False)
            # Added to the body further down — directly under the Draw button.

            # Wire all controls to the preview + label/visibility updater.
            self._shape_combo.currentIndexChanged.connect(self._on_shape_combo_changed)
            for _sp in (self._dim1_spin, self._dim2_spin, self._dim3_spin):
                _sp.valueChanged.connect(self._update_shape_preview)
            self._sides_spin.valueChanged.connect(self._update_shape_preview)
            self._rot_spin.valueChanged.connect(self._update_shape_preview)

            # Initialise dim-input labels/visibility + first preview render.
            self._on_shape_combo_changed(0)
        else:
            self.freehand_btn = None
            self._shape_btn    = None

        self.done_btn = QLabel("Right-click to finish placing trees")
        self.done_btn.setVisible(False)
        self.done_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.done_btn.setStyleSheet(
            "color:#2F5D8A; font-size:11px; font-weight:500;"
            " background:#EBF2F8; border:1px solid #90caf9;"
            " border-radius:6px; padding:7px 10px;"
        )

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color:#5F6F65; font-size:11px;")

        # ── Draw controls — inline, directly under the metric details ──
        # Clip options (area only).
        if self.clip_redline_check is not None:
            clip_row = QWidget()
            clip_row.setStyleSheet("background:transparent;")
            crl = QHBoxLayout(clip_row)
            crl.setContentsMargins(0, 0, 0, 0)
            crl.setSpacing(8)
            clip_ic = QLabel()
            clip_ic.setFixedSize(14, 14)
            clip_ic.setPixmap(theme.icon("scissors", "#717E75", 14).pixmap(QSize(14, 14)))
            clip_ic.setStyleSheet("background:transparent; border:none;")
            clip_lbl = QLabel("Clip to")
            clip_lbl.setStyleSheet(
                "color:#54635A; font-size:11px; background:transparent; border:none;")
            crl.addWidget(clip_ic, 0)
            crl.addWidget(clip_lbl, 0)
            crl.addWidget(self.clip_redline_check, 0)
            if self.clip_blueline_check is not None:
                crl.addWidget(self.clip_blueline_check, 0)
            if self.cookie_cutter_check is not None:
                crl.addSpacing(6)
                cookie_ic = QLabel()
                cookie_ic.setFixedSize(14, 14)
                cookie_ic.setPixmap(
                    theme.icon("circle-dashed", "#7A5C3E", 14).pixmap(QSize(14, 14)))
                cookie_ic.setStyleSheet("background:transparent; border:none;")
                crl.addWidget(cookie_ic, 0)
                crl.addWidget(self.cookie_cutter_check, 0)
            crl.addStretch(1)
            layout.addWidget(clip_row)

        # Mode control (area only): Pen / Freehand / Shape as one segmented
        # control driving the existing (mutually-exclusive) draw-mode toggles.
        if self.freehand_btn is not None:
            seg_css = (
                "QPushButton { background:transparent; border:none; border-radius:6px;"
                " color:#54635A; font-size:11px; font-weight:500; padding:6px 4px; }"
                " QPushButton:hover:!checked { background:#E7EDE6; }"
                " QPushButton:checked { background:#FFFFFF; color:#1F4A37;"
                " border:1px solid #D5DBD3; }")
            self._pen_btn = QPushButton("Pen")
            self._pen_btn.setCheckable(True)
            self._pen_btn.setChecked(True)
            self._pen_btn.setIcon(theme.icon("pencil", "#1F4A37", 14))
            self.freehand_btn.setIcon(theme.icon("polygon", "#1F4A37", 14))
            self._shape_btn.setIcon(theme.icon("circle-dashed", "#1F4A37", 14))
            mode_row = QFrame()
            mode_row.setStyleSheet(
                "QFrame { background:#F1F4F0; border:1px solid #E0E4DD; border-radius:9px; }")
            mrl = QHBoxLayout(mode_row)
            mrl.setContentsMargins(3, 3, 3, 3)
            mrl.setSpacing(3)
            self._mode_group = QButtonGroup(self)
            self._mode_group.setExclusive(True)
            for b in (self._pen_btn, self.freehand_btn, self._shape_btn):
                b.setMinimumHeight(30)
                b.setIconSize(QSize(14, 14))
                b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                b.setStyleSheet(seg_css)
                self._mode_group.addButton(b)
                mrl.addWidget(b, 1)
            layout.addWidget(mode_row)

        layout.addWidget(self.draw_btn)

        # Shape settings drop in directly under the Draw button when Shape is on.
        if self._shape_btn is not None:
            layout.addWidget(self._shape_group)

        layout.addWidget(self.done_btn)
        layout.addWidget(self.status_label)

        # Buildings are a Biome display-only distinction: they receive their
        # own charcoal roof symbol and legend item, but the statutory metric
        # has no separate Buildings row. Keep that relationship visible at the
        # point of entry so the user cannot mistake the map label for a new
        # metric habitat.
        self._buildings_metric_banner = None
        if self.habitat_key == "area":
            buildings_banner = QFrame()
            buildings_banner.setObjectName("buildingsMetricBanner")
            buildings_banner.setStyleSheet(
                "QFrame#buildingsMetricBanner { background:#EEF3F1;"
                " border:1px solid #AAB8B0; border-radius:7px; }"
            )
            bml = QHBoxLayout(buildings_banner)
            bml.setContentsMargins(10, 9, 10, 9)
            bml.setSpacing(8)
            bm_icon = QLabel()
            bm_icon.setFixedSize(16, 16)
            bm_icon.setPixmap(theme.icon("info-circle", "#355744", 16).pixmap(QSize(16, 16)))
            bm_icon.setStyleSheet("background:transparent; border:none;")
            bm_text = QLabel(
                "<b>Buildings are mapped separately for display.</b><br>"
                "For all BNG metric calculations and exports, they are counted "
                "as <b>Developed land; sealed surface</b>."
            )
            bm_text.setWordWrap(True)
            bm_text.setTextFormat(Qt.TextFormat.RichText)
            bm_text.setStyleSheet(
                "color:#355744; font-size:11px; background:transparent; border:none;"
            )
            bml.addWidget(bm_icon, 0, Qt.AlignmentFlag.AlignTop)
            bml.addWidget(bm_text, 1)
            buildings_banner.setVisible(False)
            self._buildings_metric_banner = buildings_banner
            layout.addWidget(buildings_banner)

        layout.addStretch()
        inner.setLayout(layout)
        scroll.setWidget(inner)

        # Stop combo boxes from forcing the panel wide. By default a QComboBox's
        # size hint grows to its longest item (e.g. the long strategic-significance
        # option, or sub-habitat names), which pushed the form off the right edge
        # in a narrow dock. Cap the hint so every combo shrinks with the panel —
        # the dropdown still lists the full text.
        for _cb in inner.findChildren(QComboBox):
            _cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            _cb.setMinimumContentsLength(6)
            _cb.setMinimumWidth(0)
            # Ignore the combo's own width hint entirely so it can never set the
            # form's minimum width — it fills the row when there's space and
            # shrinks (eliding the text) when the panel is dragged narrow. The
            # dropdown list still shows every option in full.
            _cb.setSizePolicy(QSizePolicy.Policy.Ignored, _cb.sizePolicy().verticalPolicy())

        # Wrap the scroll area in a plain QVBoxLayout for setLayout().
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setLayout(outer)

        self._populate_subs(self.habitat_type_combo, self.sub_combo)
        self._populate_subs(self.prop_type_combo,    self.prop_sub_combo)

        # For tree forms: sync hidden sub_combos to initial size+location selection
        if self._is_trees_form:
            self._sync_tree_sub()
            if hasattr(self, "_sync_prop_tree_sub"):
                self._sync_prop_tree_sub()

        # Initialise condition-helper button visibility for the default sub-type.
        # Without this, the toggle button stays hidden until the user manually
        # changes the sub-type, because _on_sub_changed is only wired to the
        # currentTextChanged signal (not called on first load).
        self._on_sub_changed(self.sub_combo.currentText())
        self._on_prop_sub_changed(self.prop_sub_combo.currentText())

    # ------------------------------------------------------------------ #
    #  HELPERS                                                             #
    # ------------------------------------------------------------------ #

    def _populate_types(self, combo, screen_key=None):
        combo.clear()
        if screen_key and screen_key in HABITAT_SCREEN_TYPES:
            allowed = HABITAT_SCREEN_TYPES[screen_key]
            combo.addItems([t for t in allowed if t in HABITAT_TYPES])
        else:
            combo.addItems(sorted(HABITAT_TYPES.keys()))

    def _populate_subs(self, type_combo, sub_combo):
        sub_combo.clear()
        t = type_combo.currentText()
        if t in HABITAT_TYPES:
            sub_combo.addItems(sorted(HABITAT_TYPES[t].keys()))

    @staticmethod
    def _condition_items_for(sub):
        """Return (items, target, locked) for a given sub-habitat type.

        Thin wrapper over habitat_data.condition_items_for — the single source
        of truth now shared with the Manage Features property editor.
        """
        return condition_items_for(sub)

    @staticmethod
    def _apply_condition_combo(combo, sub, current_value=None):
        """Repopulate combo with the correct condition items for sub,
        preserving current_value if it is still valid."""
        items, default, locked = HabitatFormScreen._condition_items_for(sub)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        # Restore previous value if still valid, else use default
        target = current_value if (current_value and current_value in items) else default
        idx = combo.findText(target)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        combo.setEnabled(not locked)
        combo.setStyleSheet(
            "QComboBox { color: #8A968D; background: #F4F6F2; }" if locked else ""
        )

    def _on_sub_changed(self, sub_text):
        """Auto-set condition, strategic significance and irreplaceable when
        the sub-habitat type changes.  Locks the condition combo for fixed or
        N/A types.  Also refreshes the condition assessment helper panel."""
        sub = sub_text.strip()
        prev = self.condition_combo.currentText()
        self._apply_condition_combo(self.condition_combo, sub, prev)

        # Watercourses: a Culvert has no assessable banks/channel, so the metric
        # forces every encroachment to "N/A - Culvert"; other types must NOT
        # offer that option.
        if self.habitat_key == "watercourse":
            self._apply_encroachment_combos(sub)

        # ── Auto-set Irreplaceable Habitat ────────────────────────────────
        # Set "Yes" for habitat types that are definitively on the statutory
        # irreplaceable habitat list (SI 2024/48).  All other types default to
        # "No" — the common case — with the warning note prompting the user to
        # override where the habitat actually is irreplaceable (e.g. ancient
        # woodland, which can't be determined from habitat type alone).
        if sub in IRREPLACEABLE_SUBTYPES:
            irr_idx = self.irreplaceable_combo.findText("Yes")
            if irr_idx >= 0:
                self.irreplaceable_combo.setCurrentIndex(irr_idx)
        else:
            irr_idx = self.irreplaceable_combo.findText("No")
            if irr_idx >= 0:
                self.irreplaceable_combo.setCurrentIndex(irr_idx)

        # Strategic significance is defaulted at construction (to "no local
        # strategy") and deliberately NOT reset here: it depends on whether the
        # site sits within a Local Nature Recovery Strategy area, which the user
        # confirms or overrides via the warning note.

        # Show the Condition assessment button only when a helper exists AND the
        # condition isn't fixed by the metric (locked habitats can't be assessed).
        locked = is_condition_locked(sub)
        self._cond_toggle_btn.setVisible(CD.has_panel(sub) and not locked)

        # Explain a locked / greyed condition combo rather than leaving it
        # looking broken.
        if hasattr(self, "_cond_lock_note"):
            self._cond_lock_note.setVisible(locked)
            if locked:
                self._cond_lock_note.setText(
                    "Condition is set automatically for this habitat type — "
                    "it isn't assessed under the metric.")
        self._sync_buildings_metric_banner()

    def _sync_buildings_metric_banner(self):
        """Show the metric-alias note whenever Buildings is being entered."""
        banner = getattr(self, "_buildings_metric_banner", None)
        if banner is None:
            return
        current = (self.sub_combo.currentText().strip()
                   if getattr(self, "sub_combo", None) is not None else "")
        proposed = ""
        prop_group = getattr(self, "proposed_group", None)
        if prop_group is not None and prop_group.isVisible():
            combo = getattr(self, "prop_sub_combo", None)
            proposed = combo.currentText().strip() if combo is not None else ""
        banner.setVisible(current == "Buildings" or proposed == "Buildings")

    def _apply_encroachment_combos(self, sub):
        """Constrain the three watercourse encroachment combos by type.

        * Culvert  -> channel + both banks forced to "N/A - Culvert" and locked
          (the metric marks every culvert condition/encroachment as such).
        * any other watercourse type -> normal severities, with "N/A - Culvert"
          NOT offered. Any previously chosen severity is preserved."""
        combos = (getattr(self, "water_enc_combo", None),
                  getattr(self, "rip_l_combo", None),
                  getattr(self, "rip_r_combo", None))
        if any(c is None for c in combos):
            return
        if sub == "Culvert":
            for c in combos:
                c.blockSignals(True)
                c.clear()
                c.addItem("N/A - Culvert")
                c.setCurrentIndex(0)
                c.blockSignals(False)
                c.setEnabled(False)
                c.setStyleSheet("QComboBox { color:#8A968D; background:#F4F6F2; }")
            return
        chan_opts = [o for o in WATERCOURSE_ENCROACHMENT_OPTIONS if o != "N/A - Culvert"]
        for c, opts in ((self.water_enc_combo, chan_opts),
                        (self.rip_l_combo, RIPARIAN_ENCROACHMENT_OPTIONS),
                        (self.rip_r_combo, RIPARIAN_ENCROACHMENT_OPTIONS)):
            prev = c.currentText()
            c.blockSignals(True)
            c.clear()
            c.addItems(opts)
            idx = c.findText(prev) if prev in opts else 0
            c.setCurrentIndex(max(0, idx))
            c.blockSignals(False)
            c.setEnabled(True)
            c.setStyleSheet("")

    def _toggle_cond_helper(self):
        """Open the condition assessment dialog for the baseline habitat."""
        self._open_condition_dialog(self.sub_combo.currentText().strip(),
                                    self.condition_combo, "_cond_ctrls")

    def _sync_bng_toggle(self):
        """Point the disclosure chevron the right way for the open/closed state."""
        ic = "chevron-down" if self._bng_open else "chevron-right"
        self._bng_toggle.setIcon(theme.icon(ic, theme.MUTED, 14))
        self._bng_toggle.setIconSize(QSize(14, 14))

    def _toggle_bng_details(self):
        """Show / hide the optional BNG metric fields."""
        self._bng_open = not self._bng_open
        self._bng_body.setVisible(self._bng_open)
        self._sync_bng_toggle()

    def _on_prop_sub_changed(self, sub_text):
        """Mirror _on_sub_changed for the proposed habitat sub-type.
        Applies the same condition locking and criteria helper logic."""
        sub = sub_text.strip()
        prev = self.prop_condition_combo.currentText()
        self._apply_condition_combo(self.prop_condition_combo, sub, prev)

        self._prop_cond_toggle_btn.setVisible(CD.has_panel(sub) and not is_condition_locked(sub))
        self._sync_buildings_metric_banner()

    def _toggle_prop_cond_helper(self):
        """Open the read-only condition GOALS view for the proposed habitat —
        you cannot assess the condition of a habitat that does not exist yet, so
        the criteria are shown purely as targets to reach (no ticks/copy)."""
        self._open_condition_dialog(self.prop_sub_combo.currentText().strip(),
                                    self.prop_condition_combo, "_prop_cond_ctrls",
                                    goals=True)

    # ── condition assessment: open the shared dialog + auto-fill comments ────
    def _open_condition_dialog(self, sub, combo, ctrls_attr, goals=False):
        """Open the shared condition dialog (see condition_dialog.py).

        Baseline habitats open it interactively (assess); proposed habitats open
        it read-only (goals). On interaction the resulting grade is written back
        to the Condition dropdown *combo* and the written summary into the User
        Comments box (assess only — opening, or goals mode, never writes)."""
        from .condition_dialog import open_condition_dialog, deserialize_state
        detail_attr = ctrls_attr.replace("_ctrls", "_detail")
        initial = None if goals else deserialize_state(getattr(self, detail_attr, "") or "")

        def on_result(grade, report, detail):
            if grade and combo is not None:
                idx = combo.findText(grade)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            if report:
                self._autofill_comments(report)
            setattr(self, detail_attr, detail or "")

        ctrls = open_condition_dialog(self, sub, goals=goals, allow_export=not goals,
                                      initial=initial,
                                      on_result=(None if goals else on_result))
        setattr(self, ctrls_attr, ctrls)

    def _autofill_comments(self, text):
        """Write the condition summary into the visible User Comments box, after
        any text the user has typed, replacing a previously auto-inserted summary
        so re-assessing never stacks duplicates."""
        if not text:
            return
        box = getattr(self, "comments_edit", None)
        if box is None:
            return
        prev = getattr(self, "_cond_inserted", "")
        cur = box.toPlainText()
        marker = "Condition Assessment:"
        if prev and prev in cur:
            cur = cur.replace(prev, text)
        elif marker in cur:
            cur = cur[:cur.rfind(marker)].rstrip()
            cur = (cur + "\n\n" + text) if cur.strip() else text
        elif cur.strip():
            cur = cur.rstrip() + "\n\n" + text
        else:
            cur = text
        box.blockSignals(True)
        box.setPlainText(cur)
        box.blockSignals(False)
        self._cond_inserted = text

    # The retention combo was removed (retention is now auto-derived), so the
    # handlers that reacted to it — _on_retention_changed and
    # _on_retention_changed_reverse — were removed too.

    def _extra_fields(self):
        """Snapshot optional BNG metric fields from the form.
        Returns a dict; blank values are empty strings (caller skips them)."""
        return {
            "strategic_sig": self.strategic_sig_combo.currentText().strip(),
            "irreplaceable": self.irreplaceable_combo.currentText().strip(),
            "water_enc":     self.water_enc_combo.currentText().strip()
                             if self.habitat_key == "watercourse" else "",
            "rip_l":         self.rip_l_combo.currentText().strip()
                             if self.habitat_key == "watercourse" else "",
            "rip_r":         self.rip_r_combo.currentText().strip()
                             if self.habitat_key == "watercourse" else "",
            "comments":      self.comments_edit.toPlainText().strip(),
            "condition_detail": getattr(self, "_cond_detail", "") or "",
            "hab_ref":       self.hab_ref_input.text().strip(),
            "prop_type":     self.prop_type_combo.currentText().strip(),
            "prop_sub":      self.prop_sub_combo.currentText().strip(),
            "prop_cond":     self.prop_condition_combo.currentText().strip(),
        }

    def _layer_key(self, phase):
        return {
            ("baseline", "area"):        "baseline_area",
            ("baseline", "watercourse"): "baseline_water",
            ("baseline", "hedgerow"):    "baseline_hedgerow",
            ("baseline", "trees"):       "baseline_trees",
            ("proposed", "area"):        "proposed_area",
            ("proposed", "watercourse"): "proposed_water",
            ("proposed", "hedgerow"):    "proposed_hedgerow",
            ("proposed", "trees"):       "proposed_trees",
        }.get((phase, self.habitat_key))

    @staticmethod
    def _last_fid(layer):
        """Return the fid of the most recently written feature (highest fid)."""
        fid = None
        for f in layer.getFeatures():
            if fid is None or f.id() > fid:
                fid = f.id()
        return fid

    # Slivers below this absolute area (m²) are treated as digitising noise
    # from difference()/intersection() along shared edges and are removed so
    # they don't become standalone micro-features. 0.5 m² is well below any
    # real habitat parcel but large enough to swallow GEOS edge artifacts.
    _SLIVER_AREA_M2 = 0.5
    # A part is also dropped if it is a thin ribbon: tiny area relative to its
    # perimeter (area / perimeter < this many metres ⇒ effectively a line).
    _SLIVER_THINNESS_M = 0.05

    def _remove_slivers(self, geom, crs=None):
        """Return *geom* with tiny disconnected parts and thin ribbon slivers
        removed. Keeps the substantive polygon(s); never returns the sliver in
        preference to real area. If every part is a sliver, returns the
        largest part unchanged (so we never silently lose the whole feature).

        Area/length thresholds are true ground metres. Pass *crs* (the layer
        CRS) so they are measured ellipsoidally — a projected CRS such as Web
        Mercator (EPSG:3857) inflates planar area ~2.5x at UK latitudes, which
        would otherwise skew the thresholds. If *crs* is omitted, falls back to
        planar layer-CRS units (correct only when that CRS is already metres).
        """
        from qgis.core import (QgsGeometry, QgsWkbTypes, QgsDistanceArea,
                                QgsUnitTypes, QgsProject)
        if geom is None or geom.isNull() or geom.isEmpty():
            return geom
        flat = QgsWkbTypes.flatType(geom.wkbType())
        if flat == QgsWkbTypes.Type.Polygon:
            return geom   # single part — nothing to prune
        if flat != QgsWkbTypes.Type.MultiPolygon:
            return geom

        parts = geom.asMultiPolygon()
        if not parts or len(parts) == 1:
            return geom

        da = None
        if crs is not None:
            da = QgsDistanceArea()
            try:
                da.setSourceCrs(crs, QgsProject.instance().transformContext())
                da.setEllipsoid(measurement_ellipsoid())
            except Exception:
                da = None

        def _area_m2(g):
            if da is None:
                return g.area()
            try:
                return abs(da.convertAreaMeasurement(
                    da.measureArea(g), QgsUnitTypes.AreaUnit.AreaSquareMeters))
            except Exception:
                return g.area()

        def _len_m(g):
            # Perimeter of a polygon part (measureLength is for lines and can
            # return 0 for polygons — measurePerimeter is the polygon boundary).
            if da is None:
                return g.length()
            try:
                return abs(da.convertLengthMeasurement(
                    da.measurePerimeter(g), QgsUnitTypes.DistanceUnit.DistanceMeters))
            except Exception:
                return g.length()

        kept = []
        for ring_set in parts:
            part = QgsGeometry.fromPolygonXY(ring_set)
            if part is None or part.isNull() or part.isEmpty():
                continue
            area = _area_m2(part)
            perim = _len_m(part) or 1e-9
            is_tiny = area < self._SLIVER_AREA_M2
            is_thin = (area / perim) < self._SLIVER_THINNESS_M
            if not (is_tiny or is_thin):
                kept.append((area, part))

        if not kept:
            # Everything looked like a sliver — keep only the biggest part so
            # we never drop the feature entirely.
            biggest = max(
                (QgsGeometry.fromPolygonXY(p) for p in parts),
                key=lambda g: _area_m2(g), default=geom)
            return biggest

        if len(kept) == 1:
            return kept[0][1]

        # Re-assemble the substantive parts into a multipolygon.
        kept.sort(key=lambda t: t[0], reverse=True)
        out = kept[0][1]
        for _a, p in kept[1:]:
            out = out.combine(p)
        return out

    def _polygonal_only(self, geom):
        """Reduce *geom* to its polygonal parts as a valid Polygon / MultiPolygon.

        GEOS ``difference()`` against edge-sharing neighbours can return a
        GeometryCollection (the real polygons PLUS a dangling boundary line
        where a strip cut clean across) or a self-touching invalid polygon.
        Either would fall through the MultiPolygon-only split step below and be
        stored verbatim — and a mixed geometry coerced into a single Polygon
        column comes out as one malformed "hole outside shell" feature. Keeping
        only the 2-D parts and revalidating turns a cross-cut parcel into a
        clean MultiPolygon, which the split step then saves as separate
        features. Returns the original geom if nothing polygonal is found."""
        from qgis.core import QgsGeometry, QgsWkbTypes
        if geom is None or geom.isNull() or geom.isEmpty():
            return geom
        if not geom.isGeosValid():
            fixed = geom.makeValid()
            if fixed and not fixed.isNull() and not fixed.isEmpty():
                geom = fixed
        polys = []
        for g in geom.asGeometryCollection():
            if (g is None or g.isNull() or g.isEmpty()
                    or QgsWkbTypes.geometryType(g.wkbType())
                    != QgsWkbTypes.GeometryType.PolygonGeometry):
                continue
            if QgsWkbTypes.flatType(g.wkbType()) == QgsWkbTypes.Type.MultiPolygon:
                for ring_set in g.asMultiPolygon():
                    polys.append(QgsGeometry.fromPolygonXY(ring_set))
            else:
                polys.append(QgsGeometry(g))
        if not polys:
            return geom
        if len(polys) == 1:
            return polys[0]
        return QgsGeometry.collectGeometry(polys)

    def _subtract_existing_polygons(self, layer, new_geom, exclude_fid=None):
        """Return new_geom with all existing polygon features in layer subtracted out.
        Ensures a newly drawn polygon never overwrites an existing one — only fills gaps.
        Returns the trimmed geometry, or None if nothing remains."""
        from qgis.core import QgsGeometry, QgsWkbTypes
        if new_geom is None or new_geom.isNull() or new_geom.isEmpty():
            return new_geom
        result = QgsGeometry(new_geom)
        for feat in layer.getFeatures():
            if exclude_fid is not None and feat.id() == exclude_fid:
                continue
            existing = feat.geometry()
            if not existing or existing.isNull() or existing.isEmpty():
                continue
            if not result.intersects(existing):
                continue
            result = result.difference(existing)
            if result is None or result.isNull() or result.isEmpty():
                return None
        # Keep only polygon types — difference can return lines/points at shared edges
        if result and not result.isNull():
            flat = QgsWkbTypes.flatType(result.wkbType())
            if flat not in (QgsWkbTypes.Type.Polygon, QgsWkbTypes.Type.MultiPolygon):
                try:
                    polys = [g for g in result.asGeometryCollection()
                             if QgsWkbTypes.flatType(g.wkbType()) in
                             (QgsWkbTypes.Type.Polygon, QgsWkbTypes.Type.MultiPolygon)]
                    if polys:
                        result = polys[0]
                        for p in polys[1:]:
                            result = result.combine(p)
                    else:
                        return None
                except Exception:
                    pass
        # Drop tiny edge slivers left by difference() so they don't become
        # standalone micro-features.
        result = self._remove_slivers(result, crs=layer.crs())
        return result

    def _cookie_cutter_on(self):
        """Whether the Cookie cutter checkbox exists and is ticked."""
        cb = getattr(self, "cookie_cutter_check", None)
        try:
            return bool(cb is not None and cb.isChecked())
        except Exception:
            return False

    def _cookie_trim_count(self):
        """How many parcels the current placement's cookie cut has changed or
        removed so far (for status text)."""
        rep = getattr(self, "_pending_cookie_repairs", None) or {}
        return len(rep.get("mods") or []) + len(rep.get("readds") or [])

    def _cookie_cut_under(self, layer, keep_fids):
        """Cookie cutter: punch the combined footprint of *keep_fids* out of
        every OTHER polygon feature on *layer*.

        Overlapped parcels are trimmed back, split into separate features
        where the punch cuts clean across (the GPKG column is single-part
        Polygon), or deleted when nothing substantive remains — the deletion
        threshold uses ellipsoidal ground area, since planar EPSG:3857 area is
        ~2.6x inflated at UK latitudes. A donut (interior ring) is a valid,
        expected result when the punch sits wholly inside a parcel.

        Repair data (each parcel's previous geometry, or the whole feature if
        deleted, plus any split-part fids) accumulates on
        self._pending_cookie_repairs; _push_global_undo drains it into the
        global undo entry so one undo restores everything.

        Returns the number of parcels changed or removed."""
        from qgis.core import (QgsGeometry, QgsFeature, QgsWkbTypes,
                                QgsDistanceArea, QgsUnitTypes, QgsProject,
                                QgsMessageLog, Qgis)
        keep = {f for f in (keep_fids or []) if f is not None}
        if not keep:
            return 0
        layer.dataProvider().forceReload()
        layer.reload()

        # Union of the final (already clipped) footprints. WKT round-trip as
        # elsewhere in this file: GEOS ops on provider-read geometries can
        # silently no-op due to a coordinate-space mismatch.
        punch = None
        for kf in keep:
            f = layer.getFeature(kf)
            if f is None or not f.isValid() or f.geometry().isNull():
                continue
            g = QgsGeometry.fromWkt(f.geometry().asWkt(20))
            if g is None or g.isNull() or g.isEmpty():
                continue
            punch = g if punch is None else punch.combine(g)
        if punch is None or punch.isNull() or punch.isEmpty():
            return 0

        rep = getattr(self, "_pending_cookie_repairs", None)
        if rep is None:
            rep = {"mods": [], "readds": [], "split_pairs": []}
            self._pending_cookie_repairs = rep

        da = QgsDistanceArea()
        try:
            da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
            da.setEllipsoid(measurement_ellipsoid())
        except Exception:
            da = None

        def _ground_m2(g):
            if da is None:
                return g.area()
            try:
                return abs(da.convertAreaMeasurement(
                    da.measureArea(g), QgsUnitTypes.AreaUnit.AreaSquareMeters))
            except Exception:
                return g.area()

        def _attrs_dict(feat):
            """JSON-safe {field: value} snapshot (fid excluded) so a deleted
            parcel can be re-added by undo even after a project reopen. PyQt5
            NULLs str() to \"NULL\", so null variants map to None explicitly."""
            out = {}
            for i, fld in enumerate(layer.fields()):
                if fld.name().lower() == "fid":
                    continue
                v = feat.attribute(i)
                if v is None:
                    out[fld.name()] = None
                    continue
                try:
                    if hasattr(v, "isNull") and v.isNull():
                        out[fld.name()] = None
                        continue
                except Exception:
                    pass
                out[fld.name()] = v if isinstance(v, (int, float, str, bool)) else str(v)
            return out

        provider = layer.dataProvider()
        fid_field_idx = layer.fields().indexFromName("fid")
        # Materialise before mutating the provider mid-iteration.
        victims = [QgsFeature(f) for f in layer.getFeatures()
                   if f.id() not in keep]

        changed = 0
        for vf in victims:
            vg_raw = vf.geometry()
            if vg_raw is None or vg_raw.isNull() or vg_raw.isEmpty():
                continue
            vg = QgsGeometry.fromWkt(vg_raw.asWkt(20))
            if vg is None or vg.isNull() or vg.isEmpty() or not vg.intersects(punch):
                continue
            before_wkt = vg.asWkt(20)
            remainder = vg.difference(punch)
            if remainder is None or remainder.isNull():
                continue   # GEOS failure — leave this parcel untouched
            if not remainder.isEmpty():
                remainder = self._polygonal_only(remainder)
                cleaned = self._remove_slivers(remainder, crs=layer.crs())
                if cleaned and not cleaned.isNull() and not cleaned.isEmpty():
                    remainder = cleaned
            if (remainder is None or remainder.isNull() or remainder.isEmpty()
                    or _ground_m2(remainder) < self._SLIVER_AREA_M2):
                # Fully covered (or only a sliver would survive) — remove the
                # parcel, capturing it first so undo can bring it back.
                rep["readds"].append({"lid": layer.id(), "wkt": before_wkt,
                                      "attrs": _attrs_dict(vf)})
                provider.deleteFeatures([vf.id()])
                changed += 1
                continue

            wkb_type = QgsWkbTypes.flatType(remainder.wkbType())
            if wkb_type == QgsWkbTypes.Type.MultiPolygon:
                # Punched clean through — keep the first part on the original
                # fid, save the rest as separate features (single-part column).
                geoms = [QgsGeometry.fromPolygonXY(p)
                         for p in remainder.asMultiPolygon()]
                geoms = [g for g in geoms
                         if g and not g.isNull() and not g.isEmpty()]
                if not geoms:
                    continue
                if provider.capabilities() & provider.ChangeGeometries:
                    provider.changeGeometryValues({vf.id(): geoms[0]})
                rep["mods"].append([layer.id(), vf.id(),
                                    before_wkt, geoms[0].asWkt(20)])
                orig_attrs = vf.attributes()
                new_feats = []
                for g in geoms[1:]:
                    nf = QgsFeature(layer.fields())
                    nf.setGeometry(g)
                    attrs = list(orig_attrs)
                    if fid_field_idx >= 0:
                        attrs[fid_field_idx] = None   # provider assigns the fid
                    nf.setAttributes(attrs)
                    new_feats.append(nf)
                if new_feats:
                    ok, added = provider.addFeatures(new_feats)
                    if ok and added:
                        rep["split_pairs"].extend(
                            (layer, nf.id()) for nf in added)
            else:
                if provider.capabilities() & provider.ChangeGeometries:
                    provider.changeGeometryValues({vf.id(): remainder})
                rep["mods"].append([layer.id(), vf.id(),
                                    before_wkt, remainder.asWkt(20)])
            changed += 1

        if changed:
            layer.updateExtents()
            layer.reload()
            layer.triggerRepaint()
            QgsMessageLog.logMessage(
                f"[CookieCut] trimmed/removed {changed} parcel(s) under the new feature",
                "Biome", Qgis.MessageLevel.Info)
        return changed

    def _clip_feature_to_redline(self, layer, fid, drawn_geom=None):
        """Clip a newly saved feature to the Redline Boundary.

        Core rules:
        - drawn_geom: the QgsGeometry captured directly from the edit buffer
          before commitChanges(). Using the edit-buffer geometry avoids a
          QGIS/GEOS coordinate-space mismatch that causes difference() to
          return the geometry unchanged when operating on provider-read geoms.
        - Existing features are subtracted from drawn_geom (not from the
          provider-read geometry) to ensure difference() works correctly.
        - The result is then clipped to the redline boundary.
        - If the clipped result is a MultiPolygon (disconnected areas separated
          by other habitats or the redline edge), each part is saved as a
          SEPARATE feature with identical attributes and correct FID handling.
        - No holes are ever stored — only clean outer rings.
        - Returns True if any change was made, False if already inside.
        """
        from qgis.core import (QgsProject, QgsGeometry, QgsFeature,
                                QgsCoordinateTransform, QgsWkbTypes,
                                QgsMessageLog, Qgis)

        # Force full provider reload so getFeature(fid) returns committed data
        layer.dataProvider().forceReload()
        layer.reload()

        feat = layer.getFeature(fid)
        if not feat.isValid() or feat.geometry().isNull():
            return False

        # Prefer the edit-buffer geometry passed in; fall back to provider geom
        if drawn_geom is not None and not drawn_geom.isNull():
            working = QgsGeometry(drawn_geom)
        else:
            working = QgsGeometry.fromWkt(feat.geometry().asWkt(20))

        orig_area = working.area()

        # ── Step 1: clip to the selected boundary extent ─────────────
        # Must happen FIRST so the subsequent existing-feature subtraction
        # operates only on the portion inside the redline. A polygon drawn
        # over the whole site correctly splits into a MultiPolygon when the
        # redline has disconnected interior areas.
        boundary_geom = None
        redline_geom = None
        blue_geom = None
        boundary_label = "Red Line Boundary"
        for lyr in QgsProject.instance().mapLayers().values():
            if is_redline_name(lyr.name()):
                for r_feat in lyr.getFeatures():
                    rg = r_feat.geometry()
                    if not rg or rg.isNull() or rg.isEmpty():
                        continue
                    rg_copy = QgsGeometry(rg)
                    redline_geom = (rg_copy if redline_geom is None
                                    else redline_geom.combine(rg_copy))
                break
        if redline_geom is not None:
            boundary_geom = QgsGeometry(redline_geom)

        # Both boxes mean the permissible area is the union, not the
        # intersection.  This is essential for mapping off-site habitat in the
        # part of a blue-line ownership boundary that lies beyond the redline.
        blue_selected = (
            getattr(self, "clip_blueline_check", None) is not None
            and self.clip_blueline_check.isChecked())
        if blue_selected:
            boundary_label = "Red Line / Blue Line Boundary"
            for lyr in QgsProject.instance().mapLayers().values():
                if lyr.name() != "Blue Line Boundary":
                    continue
                for b_feat in lyr.getFeatures():
                    bg = b_feat.geometry()
                    if not bg or bg.isNull() or bg.isEmpty():
                        continue
                    bg_copy = QgsGeometry(bg)
                    blue_geom = (bg_copy if blue_geom is None
                                 else blue_geom.combine(bg_copy))
                break
            if blue_geom is not None:
                boundary_geom = (QgsGeometry(blue_geom)
                                 if boundary_geom is None
                                 else boundary_geom.combine(blue_geom))

        if boundary_geom is not None and not boundary_geom.isNull():
            clipped_to_redline = working.intersection(boundary_geom)
            if (clipped_to_redline and not clipped_to_redline.isNull()
                    and not clipped_to_redline.isEmpty()):
                working = clipped_to_redline
            else:
                # Drawn polygon is entirely outside the selected boundary.
                QgsMessageLog.logMessage(
                    f"[Clip] Drawn polygon has no overlap with {boundary_label} — feature removed",
                    "Biome", Qgis.MessageLevel.Warning)
                provider = layer.dataProvider()
                provider.deleteFeatures([fid])
                layer.updateExtents()
                layer.reload()
                layer.triggerRepaint()
                return []   # no FIDs remain

        # ── Step 2: subtract existing features ─────────────
        # Now that working is already clipped to the redline, each
        # disconnected area is handled independently: a fully-covered area
        # disappears, an empty area stays whole, a partly-covered area shows
        # the correct gap.
        # Cookie-cutter mode inverts the priority: the drawn shape keeps its
        # full clipped footprint and the parcels UNDERNEATH are punched later
        # (once the final footprint is known), so skip the subtraction.
        if not self._cookie_cutter_on():
            for existing_feat in layer.getFeatures():
                if existing_feat.id() == fid:
                    continue
                eg = QgsGeometry.fromWkt(existing_feat.geometry().asWkt(20))
                if not eg or eg.isNull() or eg.isEmpty():
                    continue
                if not working.intersects(eg):
                    continue
                result = working.difference(eg)
                if result is None or result.isNull():
                    # difference() failed (GEOS error) — keep working, don't lose it.
                    continue
                # A valid-but-EMPTY result means this existing feature fully covers
                # the remaining area — apply it (previously this was swallowed, which
                # left the drawn geometry intact and stored a duplicate).
                working = result
                if working.isEmpty():
                    break   # nothing left to place — stop subtracting

        # Remove tiny edge slivers produced by the differences above so the
        # split step below doesn't spawn standalone micro-features. This is the
        # main place the "tiny artifacts" came from.
        # A clean cross-cut leaves a GeometryCollection (polygons + a dangling
        # edge line) or an invalid self-touching polygon; reduce to valid
        # polygonal parts so the split step below runs and nothing malformed is
        # stored.
        if working is None or working.isNull() or working.isEmpty():
            # Drawn area is entirely covered by existing habitat — there is no
            # free ground to fill, so place nothing (delete the just-committed
            # feature) rather than storing a duplicate. Mirrors the 'entirely
            # outside redline' branch above.
            provider = layer.dataProvider()
            provider.deleteFeatures([fid])
            layer.updateExtents()
            layer.reload()
            layer.triggerRepaint()
            return []

        working = self._polygonal_only(working)
        cleaned = self._remove_slivers(working, crs=layer.crs())
        if cleaned and not cleaned.isNull() and not cleaned.isEmpty():
            working = cleaned

        # If both boundaries are active, preserve the redline edge as a hard
        # feature boundary.  A single drawn polygon may cover the on-site
        # redline and the off-site portion of the blue line, but those areas
        # must be stored as separate features for correct BNG classification.
        forced_parts = []
        if blue_selected and redline_geom is not None and blue_geom is not None:
            blue_only_geom = blue_geom.difference(redline_geom)
            for partition in (working.intersection(redline_geom),
                              working.intersection(blue_only_geom)):
                if partition is None or partition.isNull() or partition.isEmpty():
                    continue
                partition = self._polygonal_only(partition)
                partition = self._remove_slivers(partition, crs=layer.crs())
                if partition is None or partition.isNull() or partition.isEmpty():
                    continue
                if QgsWkbTypes.flatType(partition.wkbType()) == QgsWkbTypes.Type.MultiPolygon:
                    forced_parts.extend(
                        QgsGeometry.fromPolygonXY(rings)
                        for rings in partition.asMultiPolygon())
                else:
                    forced_parts.append(QgsGeometry(partition))

        QgsMessageLog.logMessage(
            f"[Clip] FINAL working.area={working.area():.2f} orig={orig_area:.2f}",
            "Biome", Qgis.MessageLevel.Info)

        # ── Step 3: split MultiPolygon into separate features ─────────
        provider = layer.dataProvider()
        wkb_type = QgsWkbTypes.flatType(working.wkbType())

        # Collect non-FID attributes to copy onto extra split features.
        # orig_attrs[0] is the FID primary key — must NOT be copied or SQLite
        # raises "UNIQUE constraint failed: ...fid" on addFeatures().
        orig_attrs = feat.attributes()
        fid_field_idx = layer.fields().indexFromName("fid")

        def _make_part_feature(geom):
            """Create a new QgsFeature for a split part, with FID left NULL."""
            nf = QgsFeature(layer.fields())
            nf.setGeometry(geom)
            attrs = list(orig_attrs)
            if fid_field_idx >= 0:
                attrs[fid_field_idx] = None   # let the provider assign a new FID
            nf.setAttributes(attrs)
            return nf

        produced_fids = [fid]   # track all FIDs this clip operation writes

        if forced_parts:
            part_geometries = forced_parts
        elif wkb_type == QgsWkbTypes.Type.MultiPolygon:
            part_geometries = [QgsGeometry.fromPolygonXY(p)
                               for p in working.asMultiPolygon()]
        else:
            part_geometries = [working]

        if not part_geometries:
            return produced_fids

        # Update the committed feature with the first partition and create one
        # additional feature for every remaining on-site/off-site or
        # disconnected polygon part.
        if provider.capabilities() & provider.ChangeGeometries:
            provider.changeGeometryValues({fid: part_geometries[0]})
        new_feats = [_make_part_feature(g) for g in part_geometries[1:]]
        if new_feats:
            ok, added = provider.addFeatures(new_feats)
            if ok and added:
                produced_fids += [f.id() for f in added]

        layer.updateExtents()
        layer.reload()
        layer.triggerRepaint()
        return produced_fids

    def _clip_feature_to_blueline(self, layer, fid, drawn_geom=None):
        """Clip feature *fid* in *layer* to the Blue Line Boundary polygon.

        Identical logic to _clip_feature_to_redline but uses the
        'Blue Line Boundary' layer as the clip mask.  Applied after the
        redline clip when both checkboxes are ticked, so the feature is
        already constrained to the redline before being further clipped
        to the blue line (or vice-versa if only the blue line is ticked).

        Returns a list of FIDs remaining after the clip, or [] if the
        feature had no overlap with the Blue Line Boundary.
        """
        from qgis.core import (QgsProject, QgsGeometry, QgsFeature,
                                QgsCoordinateTransform, QgsWkbTypes,
                                QgsMessageLog, Qgis)

        layer.dataProvider().forceReload()
        layer.reload()

        feat = layer.getFeature(fid)
        if not feat.isValid() or feat.geometry().isNull():
            return False

        if drawn_geom is not None and not drawn_geom.isNull():
            working = QgsGeometry(drawn_geom)
        else:
            working = QgsGeometry.fromWkt(feat.geometry().asWkt(20))

        orig_area = working.area()

        # ── Step 1: clip to Blue Line Boundary ───────────────────────
        blueline_layer = None
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == "Blue Line Boundary":
                blueline_layer = lyr
                break

        if blueline_layer is not None:
            blueline_geom = None
            for b_feat in blueline_layer.getFeatures():
                bg = b_feat.geometry()
                if not bg or bg.isNull() or bg.isEmpty():
                    continue
                bg_copy = QgsGeometry(bg)
                blueline_geom = bg_copy if blueline_geom is None else blueline_geom.combine(bg_copy)

            if blueline_geom is not None and not blueline_geom.isNull():
                clipped = working.intersection(blueline_geom)
                if clipped and not clipped.isNull() and not clipped.isEmpty():
                    working = clipped
                else:
                    QgsMessageLog.logMessage(
                        "[Clip] Drawn polygon has no overlap with Blue Line Boundary — feature removed",
                        "Biome", Qgis.MessageLevel.Warning)
                    provider = layer.dataProvider()
                    provider.deleteFeatures([fid])
                    layer.updateExtents()
                    layer.reload()
                    layer.triggerRepaint()
                    return []

        # ── Step 2: subtract existing features ───────────────────────
        # Skipped in cookie-cutter mode (parcels underneath are punched after
        # the final footprint is known — see _cookie_cut_under).
        if not self._cookie_cutter_on():
            for existing_feat in layer.getFeatures():
                if existing_feat.id() == fid:
                    continue
                eg = QgsGeometry.fromWkt(existing_feat.geometry().asWkt(20))
                if not eg or eg.isNull() or eg.isEmpty():
                    continue
                if not working.intersects(eg):
                    continue
                result = working.difference(eg)
                if result is None or result.isNull():
                    continue   # difference() failed — keep working, protective
                working = result
                if working.isEmpty():
                    break      # fully covered — nothing left to place

        # If the drawn area is entirely covered by existing habitat, place
        # nothing rather than a duplicate (matches _clip_feature_to_redline).
        if working is None or working.isNull() or working.isEmpty():
            provider = layer.dataProvider()
            provider.deleteFeatures([fid])
            layer.updateExtents()
            layer.reload()
            layer.triggerRepaint()
            return []

        # Reduce a cross-cut GeometryCollection / invalid self-touching polygon
        # to valid polygonal parts so the MultiPolygon split below runs (matches
        # _clip_feature_to_redline; prevents a malformed 'hole outside shell').
        working = self._polygonal_only(working)

        QgsMessageLog.logMessage(
            f"[ClipBL] FINAL working.area={working.area():.2f} orig={orig_area:.2f}",
            "Biome", Qgis.MessageLevel.Info)

        # ── Step 3: split MultiPolygon into separate features ─────────
        provider = layer.dataProvider()
        wkb_type = QgsWkbTypes.flatType(working.wkbType())

        orig_attrs = feat.attributes()
        copy_attrs = orig_attrs[1:] if len(orig_attrs) > 1 else []

        produced_fids = []
        if wkb_type == QgsWkbTypes.Type.MultiPolygon:
            parts = working.asGeometryCollection()
            layer.startEditing()
            provider.deleteFeatures([fid])
            for part in parts:
                if part.isNull() or part.isEmpty():
                    continue
                nf = QgsFeature(layer.fields())
                nf.setGeometry(part)
                if copy_attrs:
                    nf.setAttributes([None] + copy_attrs)
                ok, added = provider.addFeatures([nf])
                if ok and added:
                    produced_fids.append(added[0].id())
            layer.commitChanges()
        else:
            layer.startEditing()
            layer.changeGeometry(fid, working)
            layer.commitChanges()
            produced_fids = [fid]

        layer.updateExtents()
        layer.reload()
        layer.triggerRepaint()
        return produced_fids

    def _push_global_undo(self, bl, bl_fid, pl, pl_fid,
                          extra_baseline_fids=None, extra_pairs=None):
        # Drain this placement's cookie-cutter repairs (if any) into the undo
        # entry: split parts become extra (layer, fid) companions to delete,
        # trimmed/removed parcels become repair slots to restore.
        rep = getattr(self, "_pending_cookie_repairs", None)
        self._pending_cookie_repairs = None
        cookie_pairs = (rep or {}).get("split_pairs") or []
        all_extra_pairs = list(extra_pairs or []) + list(cookie_pairs)
        repairs = None
        if rep and (rep.get("mods") or rep.get("readds")):
            repairs = {"mods": rep.get("mods") or [],
                       "readds": rep.get("readds") or []}
        if self._main_panel and hasattr(self._main_panel, "push_undo"):
            self._main_panel.push_undo(bl, bl_fid, pl, pl_fid,
                                       extra_baseline_fids=extra_baseline_fids,
                                       extra_pairs=all_extra_pairs or None,
                                       repairs=repairs)

    def _banner_nothing_placed(self):
        """Message-bar notice shown when a placement lands entirely on ground
        that is already mapped, so no new habitat was added."""
        try:
            from qgis.core import Qgis
            self.iface.messageBar().pushMessage(
                "Nothing placed",
                "There's no unoccupied land here — the area you drew over is "
                "already fully mapped, so no new habitat was added.",
                level=Qgis.MessageLevel.Warning, duration=6)
        except Exception:
            pass

    def _write_attrs(self, layer, fid, hab_type, sub_type, hab_key,
                     condition, retention, extra=None):
        """Write all attribute fields onto fid in an already-open edit session.
        extra is an optional dict with keys: strategic_sig, irreplaceable,
        water_enc, rip_l, rip_r, comments, hab_ref, prop_type, prop_sub, prop_cond.
        """
        extra = extra or {}
        display_sub = str(sub_type or "").strip()
        metric_sub = canonical_habitat_name(display_sub)
        display_prop_sub = str(extra.get("prop_sub") or "").strip()
        metric_prop_sub = canonical_habitat_name(display_prop_sub)

        def s(name, val):
            idx = layer.fields().indexFromName(name)
            if idx >= 0:
                layer.changeAttributeValue(fid, idx, val)

        s("habitat_type",                   hab_type)
        s("sub_habitat_type",               metric_sub)
        s("habitat_key",                    hab_key)
        s("condition",                      condition)
        s("condition_detail",               extra.get("condition_detail", ""))
        s("retention",                      retention)
        s("phase",                          self.phase)
        # Extra BNG metric fields (only write non-blank values)
        if extra.get("strategic_sig"):
            s("strategic_significance",     extra["strategic_sig"])
        if extra.get("irreplaceable"):
            s("irreplaceable_habitat",      extra["irreplaceable"])
        if extra.get("water_enc"):
            s("watercourse_encroachment",   extra["water_enc"])
        if extra.get("rip_l"):
            s("riparian_encroachment_l",    extra["rip_l"])
        if extra.get("rip_r"):
            s("riparian_encroachment_r",    extra["rip_r"])
        if extra.get("comments"):
            s("user_comments",              extra["comments"])
        if extra.get("hab_ref"):
            s("habitat_ref",                extra["hab_ref"])
        if extra.get("prop_type"):
            s("prop_habitat_type",          extra["prop_type"])
        if display_prop_sub:
            s("prop_sub_habitat_type",      metric_prop_sub)
        if extra.get("prop_cond"):
            s("prop_condition",             extra["prop_cond"])
        # QML style fields
        s("Baseline Habitat Type",          display_sub)
        s("Proposed Habitat Type",          display_sub)
        s("Baseline Hedge Type",            display_sub)
        s("Proposed Hedge Type",            display_sub)
        s("Baseline River Type",            display_sub)
        s("Proposed River Type",            display_sub)
        s("Baseline Condition",             condition)
        s("Proposed Condition",             condition)
        s("Retention Category",             retention)
        # Tree QML style fields — derived from sub_type
        if self.habitat_key == "trees":
            tree_size     = tree_size_from_sub_type(metric_sub)
            tree_location = tree_location_from_sub_type(metric_sub)
            s("Category",                       "Existing" if self.phase == "baseline" else "Proposed")
            s("Baseline Tree Size",             tree_size)
            s("Baseline Rural or Urban Tree",   tree_location)
            s("Proposed Tree Size",             tree_size)
            s("Proposed Rural or Urban Tree",   tree_location)

    # ------------------------------------------------------------------ #
    #  SNAPPING                                                            #
    # ------------------------------------------------------------------ #

    def _enable_digitising_aids(self):
        """Enable snapping, avoid-overlap and topological editing for area drawing.

        Snaps to:
          - Redline Boundary (all habitat types)
          - The active layer itself (all habitat types)
          - Baseline and Proposed Area layers (area type only)
          - Baseline and Proposed Hedgerow layers (hedgerow type only)
          - Baseline and Proposed Watercourse layers (watercourse type only)

        Avoid-overlap (current layer) and topological editing are enabled for
        area layers so new polygons automatically trim to existing ones.
        """
        from qgis.core import QgsSnappingConfig, QgsTolerance, QgsProject

        project = QgsProject.instance()

        # ── Determine which layer names to snap to ────────────────────
        snap_names = list(REDLINE_NAME_ALIASES)
        # Snapping to existing area/hedgerow/watercourse layers is intentionally
        # omitted: it causes new polygons to snap to existing feature edges instead
        # of the redline when clicking near existing features. Avoid-overlap handles
        # the non-overlap constraint programmatically after the polygon is drawn.

        # Always include the active layer itself (may not yet have a name match)
        active_layers = set()
        if self._active_layer and self._active_layer.isValid():
            active_layers.add(self._active_layer)

        cfg = QgsSnappingConfig()
        cfg.setEnabled(True)
        cfg.setMode(QgsSnappingConfig.SnappingMode.AdvancedConfiguration)

        try:
            # QGIS 3.26+ — use keyword-style individual layer settings
            il = QgsSnappingConfig.IndividualLayerSettings()
            il.setEnabled(True)
            # Use new enum path if available, fall back to old constant
            snap_type = getattr(
                QgsSnappingConfig.SnappingType, "VertexAndSegment",
                QgsSnappingConfig.SnappingType.VertexAndSegment
            )
            il.setTypeFlag(snap_type)
            il.setTolerance(10)
            il.setUnits(QgsTolerance.UnitType.Pixels)
        except (TypeError, AttributeError):
            # Older QGIS — positional constructor still works
            il = QgsSnappingConfig.IndividualLayerSettings(
                True, QgsSnappingConfig.SnappingType.VertexAndSegment, 10, QgsTolerance.UnitType.Pixels
            )

        for name in snap_names:
            layers = project.mapLayersByName(name)
            if layers:
                cfg.setIndividualLayerSettings(layers[0], il)
                active_layers.discard(layers[0])   # already added via name

        # Add active layer explicitly if not already covered
        for lyr in active_layers:
            cfg.setIndividualLayerSettings(lyr, il)

        project.setSnappingConfig(cfg)

        # Avoid-overlap is intentionally NOT enabled here.
        # QGIS's AvoidIntersectionsCurrentLayer trims the drawn polygon against
        # existing features BEFORE featureAdded fires, which corrupts drawn_geom.
        # Subtraction of existing features is handled explicitly in
        # _clip_feature_to_redline, so QGIS's automatic trimming is redundant.

        # ── Topological editing — keeps shared vertices in sync ───
        project.setTopologicalEditing(True)

        # Store what we changed so _restore_digitising_aids can undo it
        self._digitising_aids_active = True

    def _restore_digitising_aids(self):
        """Restore snapping, avoid-overlap and topological editing to off."""
        from qgis.core import QgsProject

        project = QgsProject.instance()

        cfg = project.snappingConfig()
        cfg.setEnabled(False)
        project.setSnappingConfig(cfg)

        # Restore avoid-intersections to disabled
        try:
            project.setAvoidIntersectionsMode(
                QgsProject.AvoidIntersectionsMode.AllowIntersections
            )
        except AttributeError:
            try:
                project.setAvoidIntersectionsMode(0)
            except Exception:
                pass

        project.setTopologicalEditing(False)
        self._digitising_aids_active = False

    # keep old names as aliases so any other call sites still work
    def _enable_snapping_to_redline(self):
        self._enable_digitising_aids()

    def _restore_snapping(self):
        self._restore_digitising_aids()

    # ------------------------------------------------------------------ #
    #  DRAWING                                                             #
    # ------------------------------------------------------------------ #

    def _on_freehand_toggled(self, checked):
        """Freehand and Shape modes are mutually exclusive."""
        if checked and getattr(self, "_shape_btn", None) is not None:
            self._shape_btn.setChecked(False)

    def _on_shape_toggled(self, checked):
        """Show/hide the shape settings group; clear the freehand toggle."""
        if hasattr(self, "_shape_group"):
            self._shape_group.setVisible(checked)
        if checked and self.freehand_btn is not None:
            self.freehand_btn.setChecked(False)

    def _shape_key(self):
        """Internal shape key from the combo's userData (falls back to text)."""
        key = self._shape_combo.currentData()
        return key if key else self._shape_combo.currentText().lower()

    def _on_shape_combo_changed(self, _idx=0):
        """Relabel and show/hide the dimension inputs for the chosen shape."""
        key = self._shape_key()
        # Per-shape input layout: (dim1 label, dim2 label or None,
        #                          dim3 label or None, show sides?)
        spec = {
            "circle":         ("Diameter (m):",   None,              None,                 False),
            "ellipse":        ("Long axis (m):",  "Short axis (m):", None,                 False),
            "square":         ("Side (m):",       None,              None,                 False),
            "rectangle":      ("Width (m):",      "Height (m):",     None,                 False),
            "stadium":        ("Width (m):",      "Height (m):",     "Corner radius (m):", False),
            "triangle":       ("Side length (m):",None,              None,                 False),
            "right_triangle": ("Base (m):",       "Height (m):",     None,                 False),
            "regular":        ("Side length (m):",None,              None,                 True),
            "annulus":        ("Outer diameter (m):", "Ring width (m):", None,             False),
        }
        d1, d2, d3, show_sides = spec.get(key, ("Size (m):", None, None, False))
        self._dim1_label.setText(d1)
        self._dim2_label.setText(d2 or "")
        self._dim2_label.setVisible(d2 is not None)
        self._dim2_row.setVisible(d2 is not None)
        self._dim3_label.setText(d3 or "")
        self._dim3_label.setVisible(d3 is not None)
        self._dim3_row.setVisible(d3 is not None)
        self._sides_label.setVisible(show_sides)
        self._sides_row.setVisible(show_sides)
        self._update_shape_preview()

    def _shape_params(self):
        """Return (shape_key, dims_dict, rotation_deg) for the shape tool."""
        key = self._shape_key()
        rot = float(self._rot_spin.value())
        d1, d2, d3 = (self._dim1_spin.value(),
                      self._dim2_spin.value(),
                      self._dim3_spin.value())
        if key == "circle":
            dims = {"d": d1}
        elif key == "ellipse":
            dims = {"a": d1, "b": d2}
        elif key == "rectangle":
            dims = {"w": d1, "h": d2}
        elif key == "stadium":
            dims = {"w": d1, "h": d2, "r": d3}
        elif key == "right_triangle":
            dims = {"a": d1, "b": d2}
        elif key == "regular":
            dims = {"s": d1, "n": self._sides_spin.value()}
        elif key == "annulus":
            dims = {"d": d1, "w": d2}
        else:  # square, triangle
            dims = {"s": d1}
        return (key, dims, rot)

    def _update_shape_preview(self, *_):
        """Recompute the in-form preview from current inputs."""
        if not hasattr(self, "_shape_preview"):
            return
        try:
            key, dims, rot = self._shape_params()
            rings = shape_rings(QgsPointXY(0.0, 0.0), key, dims, rot)
            local = [[(p.x(), p.y()) for p in ring] for ring in rings]
        except Exception:
            local = []
        self._shape_preview.set_rings(local)

    def _bump_rotation(self, deg):
        """Wheel callback — nudge the rotation spinner (which the tool reads)."""
        self._rot_spin.setValue(int((self._rot_spin.value() + deg) % 360))

    def _bump_dimension(self, axis, delta):
        """Arrow-key callback — nudge a dimension spinner (which the tool reads).
        axis 0 = primary dimension (← / →), axis 1 = secondary dimension (↑ / ↓)
        when the shape has one; otherwise both adjust the single dimension."""
        if axis == 1 and self._dim2_spin.isVisible():
            target = self._dim2_spin
        else:
            target = self._dim1_spin
        lo, hi = target.minimum(), target.maximum()
        target.setValue(max(lo, min(hi, round(target.value() + delta, 1))))

    def _rearm_shape(self):
        """Re-open the active layer so the next shape can be placed (continuous mode)."""
        self._shape_busy = False
        layer = self._active_layer
        if layer is None:
            return
        # Re-suppress the attribute dialog and reconnect the commit handler.
        cfg = layer.editFormConfig()
        cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOn)
        layer.setEditFormConfig(cfg)
        self._enable_snapping_to_redline()
        if not layer.isEditable():
            layer.startEditing()
        try:
            layer.featureAdded.disconnect(self._on_feature_added)
        except Exception:
            pass
        layer.featureAdded.connect(self._on_feature_added)
        self.draw_btn.setEnabled(False)
        # Make sure the shape tool is still the active map tool.
        canvas = self.iface.mapCanvas()
        if self._shape_tool is not None and canvas.mapTool() is not self._shape_tool:
            canvas.setMapTool(self._shape_tool)
        self.status_label.setText(
            "✅ Shape placed. Click to add more, or right-click when done."
        )

    def _finish_shape_session(self):
        """End a continuous shape session: commit, restore state, leave draw mode."""
        if not self._shape_continuous:
            return  # already finished (e.g. a second right-click)
        self._shape_continuous = False
        self._shape_busy = False
        layer = self._active_layer
        if layer is not None:
            try:
                layer.featureAdded.disconnect(self._on_feature_added)
            except Exception:
                pass
            if layer.isEditable():
                layer.commitChanges()
            cfg = layer.editFormConfig()
            cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOff)
            layer.setEditFormConfig(cfg)
        self._restore_snapping()
        self.draw_btn.setEnabled(True)
        self.status_label.setText("")
        self.iface.messageBar().clearWidgets()
        # Leave the shape tool so clicks no longer place shapes.
        self.iface.actionPan().trigger()
        self.iface.mapCanvas().refresh()
        if self._main_panel:
            self._go_to_phase_screen()

    def _remove_live_measure(self):
        """Drop the cursor length label installed for a linear-habitat draw."""
        if self._live_measure_filter is not None:
            from .live_measure import remove_live_measure
            remove_live_measure(self.iface, self._live_measure_filter)
            self._live_measure_filter = None

    def cancel_pending_draw(self):
        """Cancel a single armed digitize / freehand placement — Back was pressed
        after Draw but before a feature was placed. Drops the half-drawn geometry
        and the live map tool so nothing lingers on the canvas, and re-arms the
        Draw button. No-op when nothing is armed; continuous tree / shape-stamp
        sessions are handled by their own finish methods, not here.
        """
        # The live length label is only ever armed alongside a single draw, so
        # dropping it unconditionally (before the guard) can never leak one.
        self._remove_live_measure()
        # draw_btn is disabled exactly while a single draw is armed. If it is
        # enabled (or missing), or a continuous session is running, there is
        # nothing to cancel.
        if (self.draw_btn is None or self.draw_btn.isEnabled()
                or self._tree_mode or self._shape_continuous):
            return
        # Switch to Pan first — this deactivates the digitize / freehand tool
        # and discards any rubber-banded partial geometry.
        try:
            self.iface.actionPan().trigger()
        except Exception:
            pass
        layer = self._active_layer
        if layer is not None:
            try:
                layer.featureAdded.disconnect(self._on_feature_added)
            except Exception:
                pass
            try:
                # Nothing was committed yet, so this just closes the edit
                # session that _start_drawing opened.
                if layer.isEditable():
                    layer.commitChanges()
            except Exception:
                pass
            try:
                cfg = layer.editFormConfig()
                cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOff)
                layer.setEditFormConfig(cfg)
            except Exception:
                pass
        self._restore_snapping()
        self._freehand_tool = None
        self.draw_btn.setEnabled(True)
        self.status_label.setText("")
        try:
            self.iface.messageBar().clearWidgets()
        except Exception:
            pass

    def _start_drawing(self):
        # Freeze the classification before QGIS changes layer/editing state.
        # Reading the combo only from featureAdded is unsafe: a panel refresh
        # can briefly clear its model while the map tool is active, producing
        # a valid geometry whose habitat fields are NULL.
        selected_hab_type = self.habitat_type_combo.currentText().strip()
        selected_sub_type = self.sub_combo.currentText().strip()
        if not selected_hab_type or not selected_sub_type:
            QMessageBox.warning(
                self, "Select a habitat",
                "Choose a habitat type and sub-habitat type before drawing."
            )
            return
        self._drawing_hab_type = selected_hab_type
        self._drawing_sub_type = selected_sub_type

        target_phase = self.phase
        layer_key    = self._layer_key(target_phase)
        try:
            layer = self.layer_manager.get_or_create_layer(layer_key)
        except RuntimeError as e:
            if "unsaved_project" in str(e):
                # 'Just looking' session — no GeoPackage to write to.
                # Fall back to a memory-backed scratch layer, exactly as the
                # redline / blue-line tools do, so drawing still works.
                layer = self.layer_manager.create_scratch_layer(layer_key)
            else:
                raise
        self._active_layer = layer

        cfg = layer.editFormConfig()
        cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOn)
        layer.setEditFormConfig(cfg)

        self._enable_snapping_to_redline()
        self.iface.setActiveLayer(layer)
        layer.startEditing()

        # Default off; only the shape-stamp branch turns continuous mode on.
        self._shape_continuous = False

        if self.habitat_key == "trees":
            self._tree_mode = True
            self.draw_btn.setEnabled(False)
            self.done_btn.setVisible(True)
            self.status_label.setText(
                "Click to place trees. Right-click or press Done when finished."
            )
            layer.featureAdded.connect(self._on_tree_added)
            self.iface.actionAddFeature().trigger()
            # Install a canvas event filter to detect right-click (which QGIS
            # handles internally and doesn't expose via mapToolSet for points).
            self._install_tree_right_click_filter()
        elif (self.habitat_key == "area"
              and getattr(self, "_shape_btn", None) is not None
              and self._shape_btn.isChecked()):
            # Shape-stamp mode — one shape = one habitat, place as many as you like
            self._tree_mode = False
            self._shape_continuous = True
            self._shape_busy = False
            self.draw_btn.setEnabled(False)
            self.status_label.setText(
                "Move to position, scroll to rotate, left-click to place. "
                "Right-click when finished."
            )
            canvas = self.iface.mapCanvas()
            self._prev_map_tool = canvas.mapTool()

            def _on_shape_finished(rings):
                # Ignore further clicks while the previous shape is still committing.
                if self._shape_busy:
                    return
                # Rings arrive in true-metre British National Grid (EPSG:27700),
                # built around the click point so the metre dimensions are real
                # ground metres. Transform into the layer's actual CRS (often
                # Web Mercator) for storage — the shape keeps its true size.
                from qgis.core import (QgsCoordinateReferenceSystem,
                                       QgsCoordinateTransform)
                metric_crs = QgsCoordinateReferenceSystem("EPSG:27700")
                lyr_crs = layer.crs()
                if lyr_crs != metric_crs:
                    tr = QgsCoordinateTransform(metric_crs, lyr_crs,
                                                QgsProject.instance())
                    rings = [[tr.transform(p) for p in ring] for ring in rings]
                geom = QgsGeometry.fromPolygonXY(rings)
                # Add via the edit buffer so featureAdded fires exactly as it
                # does for the standard digitize and freehand tools.  The shape
                # tool stays active so the next click places another shape.
                feat = QgsFeature(layer.fields())
                feat.setGeometry(geom)
                self._shape_busy = True
                layer.addFeature(feat)
                # featureAdded (connected below) handles attrs + commit + pairing,
                # then re-arms the layer for the next placement.

            def _on_shape_cancel():
                # Defer so any in-flight placement commit finishes first (FIFO),
                # then tear the session down.
                from qgis.PyQt.QtCore import QTimer as _QTs
                _QTs.singleShot(0, self._finish_shape_session)

            self._shape_tool = _HabitatShapeTool(
                canvas, self._shape_params, self._bump_rotation,
                _on_shape_finished, _on_shape_cancel,
                layer_crs=layer.crs(),
                bump_dimension=self._bump_dimension,
            )
            try:
                layer.featureAdded.disconnect(self._on_feature_added)
            except Exception:
                pass
            layer.featureAdded.connect(self._on_feature_added)
            canvas.setMapTool(self._shape_tool)
            self.iface.messageBar().pushMessage(
                "Biome",
                "Move to position · scroll to rotate (Shift = fine) · "
                "arrow keys resize (←→ width, ↑↓ height; Shift = fine) · "
                "left-click to place · right-click when done",
                level=Qgis.MessageLevel.Info, duration=0,
            )
        elif (self.habitat_key == "area"
              and self.freehand_btn is not None
              and self.freehand_btn.isChecked()):
            # Freehand polygon mode
            self._tree_mode = False
            self.draw_btn.setEnabled(False)
            self.status_label.setText(
                "Hold left mouse button and drag to draw freehand — release to finish."
            )
            canvas = self.iface.mapCanvas()
            self._prev_map_tool = canvas.mapTool()

            def _on_freehand_finished(points):
                self.iface.messageBar().clearWidgets()
                canvas.setMapTool(self._prev_map_tool)
                # Convert points from map CRS to layer CRS
                map_crs = canvas.mapSettings().destinationCrs()
                lyr_crs = layer.crs()
                if map_crs != lyr_crs:
                    from qgis.core import QgsCoordinateTransform
                    tr = QgsCoordinateTransform(map_crs, lyr_crs, QgsProject.instance())
                    points = [tr.transform(p) for p in points]
                geom = QgsGeometry.fromPolygonXY([points])
                # Add via edit buffer so featureAdded signal fires exactly
                # as it does with the standard digitize tool
                feat = QgsFeature(layer.fields())
                feat.setGeometry(geom)
                layer.addFeature(feat)
                # featureAdded already connected above — it handles attrs + commit

            def _on_freehand_cancel():
                self.iface.messageBar().clearWidgets()
                canvas.setMapTool(self._prev_map_tool)
                self.draw_btn.setEnabled(True)
                self.status_label.setText("")

            self._freehand_tool = _HabitatFreehandTool(
                canvas, _on_freehand_finished, _on_freehand_cancel
            )
            try:
                layer.featureAdded.disconnect(self._on_feature_added)
            except Exception:
                pass
            layer.featureAdded.connect(self._on_feature_added)
            canvas.setMapTool(self._freehand_tool)
            self.iface.messageBar().pushMessage(
                "Biome",
                "Hold left mouse button and drag to draw freehand — release to finish — Esc to cancel",
                level=Qgis.MessageLevel.Info, duration=0,
            )
        else:
            self._tree_mode = False
            try:
                layer.featureAdded.disconnect(self._on_feature_added)
            except Exception:
                pass
            layer.featureAdded.connect(self._on_feature_added)
            self.iface.actionAddFeature().trigger()
            hints = {"area": "polygon", "watercourse": "line", "hedgerow": "line"}
            if hints.get(self.habitat_key) == "line":
                # Live length label at the cursor, as in Quick Measure.
                from .live_measure import install_live_measure
                self._remove_live_measure()
                self._live_measure_filter = install_live_measure(self.iface, "line")
            self.status_label.setText(
                f"Draw your {hints.get(self.habitat_key, 'feature')} — right-click to finish."
            )
            self.draw_btn.setEnabled(False)

    # ── Trees (multi-point, session stays open) ───────────────────────

    _CURTILAGE_WARNING_PROPERTY = "residential_curtilage_warning_shown"

    def _is_first_proposed_placement(self):
        """Return True only before the project's first proposed feature."""
        if self.phase != "proposed":
            return False
        project = QgsProject.instance()
        warning_shown, _ok = project.readBoolEntry(
            "Biome", self._CURTILAGE_WARNING_PROPERTY, False)
        if warning_shown:
            return False
        return True

    def _show_first_proposed_curtilage_warning(self):
        """Show and persist the one-off residential-curtilage reminder."""
        QgsProject.instance().writeEntry(
            "Biome", self._CURTILAGE_WARNING_PROPERTY, True)
        QMessageBox.warning(
            self,
            "Residential Curtilage — Habitat Type",
            "Any habitat within a private residential garden must be recorded as "
            "\"Vegetated garden\" or \"Unvegetated garden\", regardless of what is "
            "planted. You cannot assign higher-value habitats (e.g. grassland, "
            "meadow, or scrub) within the residential curtilage, as the statutory "
            "metric does not permit this. See the Statutory Biodiversity Metric "
            "User Guide for further guidance."
        )

    def _on_tree_added(self, fid):
        """
        Called immediately when QGIS adds the point to the edit buffer.
        We write attrs, commit right away so the feature is visible with
        the QML style, then record the permanent fid for undo.
        """
        layer = self._active_layer
        if layer is None:
            return
        show_curtilage_warning = self._is_first_proposed_placement()

        # Disconnect FIRST to prevent re-entrancy: commitChanges() and
        # startEditing() can both fire featureAdded on some QGIS/OGR
        # backends, which would cause infinite recursion and a crash.
        try:
            layer.featureAdded.disconnect(self._on_tree_added)
        except Exception:
            pass

        # Snapshot form values before any async work
        hab_type      = getattr(self, "_drawing_hab_type", "") or self.habitat_type_combo.currentText()
        sub_type      = getattr(self, "_drawing_sub_type", "") or self.sub_combo.currentText()
        condition     = self.condition_combo.currentText()
        # Retention is no longer entered by hand. Baseline trees are auto-set
        # Retained (a Retained proposed twin is created below when paired
        # mapping is on); the classifier derives the final fate via pair_id /
        # presence. Proposed-only trees are Created.
        if self.phase == "proposed":
            retention = "Created"
        else:
            retention = "Retained"
        hab_key       = HABITAT_TYPES.get(hab_type, {}).get(sub_type, {}).get("key", "")
        prop_type     = self.prop_type_combo.currentText()
        prop_sub      = self.prop_sub_combo.currentText()
        prop_cond     = self.prop_condition_combo.currentText()
        extra         = self._extra_fields()

        # Write attributes into the buffer
        self._write_attrs(layer, fid, hab_type, sub_type, hab_key,
                          condition, retention, extra)

        # Defer commitChanges() to the next event loop tick.
        # featureAdded fires while QGIS is still inside QgsVectorLayerEditBuffer::addFeature.
        # Calling commitChanges() synchronously here re-enters that call stack and
        # causes a fatal access violation on Windows (QGIS 3.x). Using singleShot(0)
        # lets QGIS finish its internal bookkeeping before we commit.
        from qgis.PyQt.QtCore import QTimer as _QT
        _QT.singleShot(0, lambda: self._complete_tree_added(
            layer, hab_type, sub_type, hab_key, condition, retention, extra,
            prop_type, prop_sub, prop_cond, show_curtilage_warning
        ))

    def _complete_tree_added(self, layer, hab_type, sub_type, hab_key,
                              condition, retention, extra,
                              prop_type, prop_sub, prop_cond,
                              show_curtilage_warning=False):
        """Deferred continuation of _on_tree_added — runs after QGIS finishes addFeature."""
        if not layer or not layer.isEditable():
            return

        # Commit so the marker is visible with QML style
        self._tree_committing = True
        layer.commitChanges()

        # Retrieve the permanent fid (highest id after commit)
        bl_fid = self._last_fid(layer)

        # Write proposed copy (dataProvider, so permanent immediately)
        # Linear and point features (hedgerow, trees, watercourse) are NOT
        # replaced by another habitat when lost — only copy to proposed if
        # Retained or Enhanced.
        pl_fid, pl = None, None
        if self.phase == "baseline":
            # Re-read geometry from the now-committed feature
            feat = layer.getFeature(bl_fid) if bl_fid is not None else None
            if feat and feat.isValid() and not feat.geometry().isNull():
                geom = QgsGeometry.fromWkt(feat.geometry().asWkt())
                if getattr(self._main_panel, "_proposed_mapping", True):
                    # Auto-retained: mirror the baseline tree to an identical
                    # proposed twin and link them with a shared pair_id. The
                    # classifier then reads the pair's fate (retained, or
                    # enhanced/lost once the twin is edited or deleted) — no
                    # hand-entered retention needed.
                    pl_fid, pl = self._write_proposed(
                        geom, hab_type, sub_type, condition, "Retained", extra
                    )
                    self._stamp_pair_id(layer, bl_fid, pl, pl_fid)

        # Push to global undo before re-opening edit session
        self._push_global_undo(layer, bl_fid, pl, pl_fid)

        # Proximity pairing prompt: only when this was a STANDALONE placement
        # (no proposed twin was just written). If the paired toggle created a
        # twin, pl_fid is set and the pair is already linked — don't ask.
        if pl_fid is None:
            self._maybe_prompt_pairing(layer, bl_fid)

        # Force balance refresh — dataProvider write doesn't fire editingStopped
        if self._main_panel and hasattr(self._main_panel, "_refresh_balance_widget"):
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self._main_panel._refresh_balance_widget)
        if self._main_panel and hasattr(self._main_panel, "_refresh_bng_widget"):
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self._main_panel._refresh_bng_widget)

        # Re-open edit session so user can keep placing trees,
        # then reconnect the signal and re-arm the Add Feature tool so
        # subsequent clicks actually register as new points.
        layer.startEditing()
        layer.featureAdded.connect(self._on_tree_added)
        self.iface.actionAddFeature().trigger()
        self._tree_committing = False  # re-arm complete, mapToolSet can act again

        self.status_label.setText("✅ Tree placed. Click to add more, or press Done.")
        layer.triggerRepaint()
        self.iface.mapCanvas().refresh()
        if show_curtilage_warning and bl_fid is not None:
            self._show_first_proposed_curtilage_warning()

    def _install_tree_right_click_filter(self):
        """Install a viewport event filter on the map canvas to detect
        right mouse button press while placing trees."""
        from qgis.PyQt.QtCore import QObject, QEvent
        from qgis.PyQt.QtCore import Qt as _Qt

        form_ref = self  # capture self for the inner class

        class _RightClickFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.MouseButtonPress:
                    if event.button() == _Qt.MouseButton.RightButton:
                        if form_ref._tree_mode and not form_ref._tree_committing:
                            form_ref._remove_tree_right_click_filter()
                            form_ref._finish_tree_session()
                            return True  # consume the event
                return False

        canvas = self.iface.mapCanvas()
        self._canvas_event_filter = _RightClickFilter(canvas)
        canvas.viewport().installEventFilter(self._canvas_event_filter)

    def _remove_tree_right_click_filter(self):
        """Remove the canvas event filter if installed."""
        if self._canvas_event_filter is not None:
            canvas = self.iface.mapCanvas()
            canvas.viewport().removeEventFilter(self._canvas_event_filter)
            self._canvas_event_filter = None

    def _finish_tree_session(self):
        # Remove the right-click event filter if still installed
        self._remove_tree_right_click_filter()
        layer = self._active_layer
        if layer:
            try:
                layer.featureAdded.disconnect(self._on_tree_added)
            except Exception:
                pass
            # Commit any uncommitted edits (shouldn't be any, but just in case)
            if layer.isEditable():
                layer.commitChanges()
            cfg = layer.editFormConfig()
            cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOff)
            layer.setEditFormConfig(cfg)

        self._restore_snapping()
        self._tree_mode = False
        self.draw_btn.setEnabled(True)
        self.done_btn.setVisible(False)
        self.status_label.setText("")

        # Immediately switch away from the Add Feature tool so clicks no
        # longer place points, then navigate to the phase screen.
        self.iface.actionPan().trigger()
        self.iface.mapCanvas().refresh()

        if self._main_panel:
            self._go_to_phase_screen()

    def _go_to_phase_screen(self):
        """After a feature is placed (or tree session ends), navigate directly
        to the Baseline / Proposed Habitats phase screen.
        """
        if not self._main_panel:
            return

        mp = self._main_panel

        # Guard against a STALE form navigating. Every placement routes here
        # using this form's own ``self.phase``, which is correct — but a form
        # the user has already moved on from can still fire this if a leftover
        # map tool or canvas event filter triggers its finish handler later.
        # When that happens the panel gets yanked to the stale form's phase
        # (e.g. landing on Proposed while placing a baseline). Only the form the
        # panel currently regards as the active placer is allowed to navigate;
        # once it has, release the slot so it can't navigate a second time.
        active = getattr(mp, "_active_habitat_form", None)
        if active is not None and active is not self:
            return
        if active is self:
            mp._active_habitat_form = None

        phase = self.phase
        title = "Proposed Habitats" if phase == "proposed" else "Baseline Habitats"

        # Remove the form screen (slot 3+) from the stack
        while mp.stack.count() > 3:
            w = mp.stack.widget(3)
            mp.stack.removeWidget(w)
            w.deleteLater()

        # Reset to home so _navigate_to records the right previous screen,
        # giving Back a clean path back to the home screen.
        mp._screen_history.clear()
        mp.stack.setCurrentIndex(0)
        mp.header_label.setText("Biome")
        mp.back_btn.setVisible(False)
        mp._tab_bar.setVisible(True)

        # Defer past any pending _refresh_phase_screens timer (singleShot 0)
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(60, lambda: mp._open_phase_screen(phase, title))

    # ── Non-tree features ─────────────────────────────────────────────

    def _on_feature_added(self, fid):
        """
        Called when QGIS adds the feature to the edit buffer.
        Write attrs, commit, grab permanent fid, write proposed copy.
        """
        layer = self._active_layer
        if layer is None:
            return
        show_curtilage_warning = self._is_first_proposed_placement()

        # Drawing is over (right-click finished the line) — drop the label.
        self._remove_live_measure()

        try:
            layer.featureAdded.disconnect(self._on_feature_added)
        except Exception:
            pass

        # Snapshot form values
        hab_type  = getattr(self, "_drawing_hab_type", "") or self.habitat_type_combo.currentText()
        sub_type  = getattr(self, "_drawing_sub_type", "") or self.sub_combo.currentText()
        condition = self.condition_combo.currentText()
        # Retention is no longer entered by hand. Areas derive it from the
        # spatial net of baseline vs proposed polygons (stored blank). Hedgerows
        # and watercourses are auto-set Retained (a Retained proposed twin is
        # created below when paired mapping is on); the classifier derives the
        # final fate via pair_id / presence. Proposed-only features are blank.
        if self.phase == "proposed":
            retention = ""
        elif self.habitat_key == "area":
            retention = ""
        else:
            retention = "Retained"
        hab_key   = HABITAT_TYPES.get(hab_type, {}).get(sub_type, {}).get("key", "")
        prop_type = self.prop_type_combo.currentText()
        prop_sub  = self.prop_sub_combo.currentText()
        prop_cond = self.prop_condition_combo.currentText()
        extra     = self._extra_fields()

        # Write attributes while still in edit mode
        self._write_attrs(layer, fid, hab_type, sub_type, hab_key,
                          condition, retention, extra)

        # Capture the edit-buffer geometry before commitChanges().
        # difference() in QGIS/GEOS returns unchanged geometry when operating
        # on provider-read geometries due to internal coordinate-space mismatch;
        # using the edit-buffer geometry directly fixes this.
        _drawn_geom = QgsGeometry(layer.getFeature(fid).geometry())

        # Defer commitChanges() — featureAdded fires mid-addFeature in QGIS's
        # call stack; calling commitChanges() synchronously causes a fatal
        # access violation on Windows. singleShot(0) defers to next event loop.
        from qgis.PyQt.QtCore import QTimer as _QT2
        _QT2.singleShot(0, lambda: self._complete_feature_added(
            layer, hab_type, sub_type, hab_key, condition, retention, extra,
            prop_type, prop_sub, prop_cond, _drawn_geom,
            show_curtilage_warning
        ))

    def _complete_feature_added(self, layer, hab_type, sub_type, hab_key,
                                 condition, retention, extra,
                                 prop_type, prop_sub, prop_cond,
                                 drawn_geom=None,
                                 show_curtilage_warning=False):
        """Deferred continuation of _on_feature_added — runs after QGIS finishes addFeature."""
        if not layer or not layer.isEditable():
            return

        # Fresh cookie-cutter repair accumulator for THIS placement —
        # _cookie_cut_under appends to it, _push_global_undo drains it.
        self._pending_cookie_repairs = None

        # Snapshot existing FIDs before committing so we can identify exactly
        # which FID was assigned to the just-drawn feature.  _last_fid() returns
        # the globally highest FID which may belong to a split-part feature added
        # by a previous clip — not the feature we are about to commit.
        existing_fids = {f.id() for f in layer.dataProvider().getFeatures()}

        layer.commitChanges()

        new_fids = {f.id() for f in layer.dataProvider().getFeatures()} - existing_fids
        bl_fid = next(iter(new_fids)) if new_fids else self._last_fid(layer)

        cfg = layer.editFormConfig()
        cfg.setSuppress(QgsEditFormConfig.FeatureFormSuppress.SuppressOff)
        layer.setEditFormConfig(cfg)
        self._restore_snapping()
        self.draw_btn.setEnabled(True)

        # ── For baseline area features: discard the edit-buffer commit and
        # re-add via dataProvider using the same flow as _write_proposed.
        # This avoids all FID race conditions and attribute-read timing
        # issues that arise from clipping a provider-committed feature.
        # The proposed layer mirrors baseline using the same geometry.
        _is_linear_or_point = self.habitat_key in ("hedgerow", "trees", "watercourse")

        if self.phase == "baseline" and not _is_linear_or_point:
            # Delete the raw edit-buffer commit — we'll re-add cleanly below.
            if bl_fid is not None:
                layer.dataProvider().deleteFeatures([bl_fid])
                layer.reload()

            # Build extra dict including proposed-counterpart fields
            bl_extra = dict(extra) if extra else {}
            if prop_type: bl_extra["prop_type"] = prop_type
            if prop_sub:  bl_extra["prop_sub"]  = prop_sub
            if prop_cond: bl_extra["prop_cond"] = prop_cond

            # _write_baseline_from_proposed writes via dataProvider + clips with
            # drawn_geom, returning a list of FIDs (>1 if split by redline/existing).
            bl_fids, bl_layer_out = self._write_baseline_from_proposed(
                drawn_geom if (drawn_geom and not drawn_geom.isNull()) else
                    QgsGeometry.fromWkt(layer.getFeature(bl_fid).geometry().asWkt()
                                        if bl_fid else ""),
                hab_type, sub_type, condition, retention, bl_extra,
                drawn_geom=drawn_geom
            )
            if not bl_fids:
                self.status_label.setText("⚠️ Nothing placed — no free space inside the redline for this feature.")
                self._banner_nothing_placed()
                self._push_global_undo(layer, None, None, None)
                layer.triggerRepaint()
                self.iface.mapCanvas().refresh()
                if self._main_panel and hasattr(self._main_panel, "_refresh_balance_widget"):
                    from qgis.PyQt.QtCore import QTimer
                    QTimer.singleShot(0, self._main_panel._refresh_balance_widget)
                if self._main_panel and hasattr(self._main_panel, "_refresh_bng_widget"):
                    from qgis.PyQt.QtCore import QTimer
                    QTimer.singleShot(0, self._main_panel._refresh_bng_widget)
                if self._main_panel and hasattr(self._main_panel, "_refresh_phase_screens"):
                    from qgis.PyQt.QtCore import QTimer
                    QTimer.singleShot(0, self._main_panel._refresh_phase_screens)
                if self._shape_continuous:
                    self._rearm_shape()
                    return
                if self._main_panel:
                    self._go_to_phase_screen()
                return

            self.status_label.setText(
                "✅ Feature saved and clipped to redline." if len(bl_fids) > 1 or bl_fids != [bl_fid]
                else "✅ Feature saved."
            )

            # ── Create Baseline + Proposed (areas) ────────────────────────
            # When the toggle is ON, replicate the baseline area shape into the
            # proposed layer as a geometry twin (same habitat type/condition,
            # same location). Areas are NOT given a retention category here —
            # created / lost / enhanced / retained is derived from the net area
            # difference (baseline vs proposed polygons) by a separate classifier
            # which stays untouched. A blank-retention twin matching the baseline
            # habitat is identical to a manually-drawn proposed area, so the
            # starting net change is zero (fully retained) until the user edits
            # the proposed polygon. Keep one proposed twin per baseline part so
            # the redline remains a hard on-site/off-site feature boundary.
            pl_fid, pl = None, None
            extra_proposed_pairs = []
            if getattr(self._main_panel, "_proposed_mapping", True):
                bl_layer_final = bl_layer_out or layer
                part_geoms = []
                for _bfid in bl_fids:
                    try:
                        _bf = bl_layer_final.getFeature(_bfid)
                    except Exception:
                        _bf = None
                    if _bf and _bf.isValid() and not _bf.geometry().isNull():
                        part_geoms.append(QgsGeometry.fromWkt(_bf.geometry().asWkt()))
                proposed_fids = []
                for twin_geom in part_geoms:
                    if twin_geom is None or twin_geom.isNull():
                        continue
                    twin_fid, twin_layer = self._write_proposed(
                        twin_geom, hab_type, sub_type, condition, "", extra)
                    if twin_layer is None or twin_fid is None:
                        continue
                    proposed_fids.append(twin_fid)
                    if pl_fid is None:
                        pl_fid, pl = twin_fid, twin_layer
                    else:
                        extra_proposed_pairs.append((twin_layer, twin_fid))
                # Cookie cutter: the twins overlap proposed parcels punched
                # on the baseline layer; trim them as the same operation.
                if pl is not None and proposed_fids and self._cookie_cutter_on():
                    self._cookie_cut_under(pl, proposed_fids)

            _trims = self._cookie_trim_count()
            if _trims:
                self.status_label.setText(
                    f"✅ Feature saved — cookie cutter trimmed {_trims} "
                    f"parcel{'s' if _trims != 1 else ''} underneath.")

            _extra = bl_fids[1:] if len(bl_fids) > 1 else None
            self._push_global_undo(bl_layer_out or layer,
                                   bl_fids[0] if bl_fids else None, pl, pl_fid,
                                   extra_baseline_fids=_extra,
                                   extra_pairs=extra_proposed_pairs)

            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()
            if self._main_panel and hasattr(self._main_panel, "_refresh_balance_widget"):
                from qgis.PyQt.QtCore import QTimer
                QTimer.singleShot(0, self._main_panel._refresh_balance_widget)
            if self._main_panel and hasattr(self._main_panel, "_refresh_bng_widget"):
                from qgis.PyQt.QtCore import QTimer
                QTimer.singleShot(0, self._main_panel._refresh_bng_widget)
            if self._main_panel and hasattr(self._main_panel, "_refresh_phase_screens"):
                from qgis.PyQt.QtCore import QTimer
                QTimer.singleShot(0, self._main_panel._refresh_phase_screens)
            if self._main_panel and hasattr(self._main_panel, "push_recent_habitat"):
                snapshot = {
                    "phase": self.phase, "habitat_key": self.habitat_key,
                    "hab_type": hab_type, "sub_type": sub_type,
                    "condition": condition, "retention": retention,
                    "prop_type": prop_type, "prop_sub": prop_sub, "prop_cond": prop_cond,
                    "strategic_sig": extra.get("strategic_sig", "") if extra else "",
                    "irreplaceable": extra.get("irreplaceable", "") if extra else "",
                    "hab_ref": extra.get("hab_ref", "") if extra else "",
                    "comments": extra.get("comments", "") if extra else "",
                    "condition_detail": extra.get("condition_detail", "") if extra else "",
                    "clip_redline": (self.clip_redline_check.isChecked()
                                     if self.clip_redline_check else True),
                    "clip_blueline": (self.clip_blueline_check.isChecked()
                                      if self.clip_blueline_check else True),
                    "cookie_cutter": (self.cookie_cutter_check.isChecked()
                                      if self.cookie_cutter_check else False),
                }
                self._main_panel.push_recent_habitat(self.phase, snapshot)
            if self._shape_continuous:
                self._rearm_shape()
                return
            self.iface.actionPan().trigger()
            if self._main_panel:
                self._go_to_phase_screen()
            return

        # ── Proposed / linear / point path (unchanged) ────────────────
        # Clip the already-committed feature in place.
        bl_fids = [bl_fid] if bl_fid is not None else []
        if (bl_fid is not None
                and getattr(self, "clip_redline_check", None) is not None
                and self.clip_redline_check.isChecked()):
            result_fids = self._clip_feature_to_redline(layer, bl_fid, drawn_geom)
            if isinstance(result_fids, list):
                bl_fids = result_fids
            layer.reload()

        # Blue-only clipping. When both are selected, the redline routine has
        # already clipped once to their combined extent.
        if bl_fids and (getattr(self, "clip_blueline_check", None) is not None
                        and self.clip_blueline_check.isChecked()
                        and not (getattr(self, "clip_redline_check", None) is not None
                                 and self.clip_redline_check.isChecked())):
            remaining = []
            for fid_to_clip in bl_fids:
                r = self._clip_feature_to_blueline(layer, fid_to_clip,
                                                   drawn_geom if fid_to_clip == bl_fid else None)
                if isinstance(r, list):
                    remaining.extend(r)
            bl_fids = remaining
            layer.reload()

        # ── Cookie cutter: punch the final footprint out of parcels beneath ──
        if bl_fids and self._cookie_cutter_on():
            self._cookie_cut_under(layer, bl_fids)

        _trims = self._cookie_trim_count()
        if not bl_fids:
            self.status_label.setText(
                "⚠️ Nothing placed — no free space inside the redline for this feature.")
            self._banner_nothing_placed()
        elif _trims:
            self.status_label.setText(
                f"✅ Feature saved — cookie cutter trimmed {_trims} "
                f"parcel{'s' if _trims != 1 else ''} underneath.")
        elif bl_fids != ([bl_fid] if bl_fid is not None else []):
            self.status_label.setText("✅ Feature saved and clipped to redline.")
        else:
            self.status_label.setText("✅ Feature saved.")

        pl_fid, pl = None, None

        if self._proposed_leads_baseline and self.phase == "proposed":
            # ── Reverse mode: proposed was drawn → write baseline counterpart ──
            feat = layer.getFeature(bl_fid) if bl_fid is not None else None
            if feat and feat.isValid() and not feat.geometry().isNull():
                geom = QgsGeometry.fromWkt(feat.geometry().asWkt())
                if retention == "Retained":
                    bl_hab_type = hab_type; bl_sub_type = sub_type
                    bl_condition = condition; bl_retention = "Retained"
                elif retention == "Enhanced":
                    bl_hab_type = prop_type; bl_sub_type = prop_sub
                    bl_condition = prop_cond; bl_retention = "Enhanced"
                else:
                    bl_hab_type = prop_type; bl_sub_type = prop_sub
                    bl_condition = prop_cond; bl_retention = "Lost"
                bl_extra = dict(extra) if extra else {}
                bl_extra["prop_type"] = hab_type
                bl_extra["prop_sub"]  = sub_type
                bl_extra["prop_cond"] = condition
                _bl_fids, pl = self._write_baseline_from_proposed(
                    geom, bl_hab_type, bl_sub_type, bl_condition, bl_retention, bl_extra
                )
                pl_fid = _bl_fids[0] if isinstance(_bl_fids, list) and _bl_fids else _bl_fids

        elif self.phase == "baseline" and _is_linear_or_point:
            # Hedgerow / watercourse: auto-retained. When paired mapping is on,
            # mirror to an identical proposed twin and link them with a shared
            # pair_id so the classifier reads the pair's fate (retained, or
            # enhanced/lost once the twin is edited or deleted). When off, the
            # standalone feature is paired by proximity / net matching.
            if getattr(self._main_panel, "_proposed_mapping", True):
                feat = layer.getFeature(bl_fid) if bl_fid is not None else None
                if feat and feat.isValid() and not feat.geometry().isNull():
                    geom = QgsGeometry.fromWkt(feat.geometry().asWkt())
                    pl_fid, pl = self._write_proposed(
                        geom, hab_type, sub_type, condition, "Retained", extra
                    )
                    self._stamp_pair_id(layer, bl_fid, pl, pl_fid)

        # Push to global undo with real permanent fids. Redline/blueline split
        # parts ride along as extra fids so one undo removes the whole placement.
        _extra_parts = [f for f in bl_fids if f != bl_fid] or None
        self._push_global_undo(layer, bl_fid, pl, pl_fid,
                               extra_baseline_fids=_extra_parts)

        layer.triggerRepaint()
        self.iface.mapCanvas().refresh()

        # dataProvider().addFeatures() (used by _write_proposed) bypasses the edit
        # buffer so it never fires editingStopped — the balance widget won't auto-
        # refresh after the proposed copy is written.  Force a refresh here.
        if self._main_panel and hasattr(self._main_panel, "_refresh_balance_widget"):
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self._main_panel._refresh_balance_widget)
        if self._main_panel and hasattr(self._main_panel, "_refresh_bng_widget"):
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self._main_panel._refresh_bng_widget)
        if self._main_panel and hasattr(self._main_panel, "_refresh_phase_screens"):
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self._main_panel._refresh_phase_screens)

        # Push this placement to the recent-habitats list on the main panel
        if self._main_panel and hasattr(self._main_panel, "push_recent_habitat"):
            snapshot = {
                "phase":            self.phase,
                "habitat_key":      self.habitat_key,
                "hab_type":         hab_type,
                "sub_type":         sub_type,
                "condition":        condition,
                "retention":        retention,
                "prop_type":        prop_type,
                "prop_sub":         prop_sub,
                "prop_cond":        prop_cond,
                "strategic_sig":    extra.get("strategic_sig", "") if extra else "",
                "irreplaceable":    extra.get("irreplaceable", "") if extra else "",
                "hab_ref":          extra.get("hab_ref", "") if extra else "",
                "comments":         extra.get("comments", "") if extra else "",
                "condition_detail": extra.get("condition_detail", "") if extra else "",
                "clip_redline":     (self.clip_redline_check.isChecked()
                                     if self.clip_redline_check else True),
                "clip_blueline":    (self.clip_blueline_check.isChecked()
                                      if self.clip_blueline_check else True),
                "cookie_cutter":    (self.cookie_cutter_check.isChecked()
                                     if self.cookie_cutter_check else False),
            }
            # Tree-specific fields
            if self._is_trees_form:
                snapshot["tree_size"]         = self.tree_size_combo.currentText()
                snapshot["tree_location"]     = self.tree_location_combo.currentText()
                snapshot["prop_tree_size"]    = self.prop_tree_size_combo.currentText() if hasattr(self, "prop_tree_size_combo") else ""
                snapshot["prop_tree_location"]= self.prop_tree_location_combo.currentText() if hasattr(self, "prop_tree_location_combo") else ""
            self._main_panel.push_recent_habitat(self.phase, snapshot)

        if show_curtilage_warning and bl_fids:
            self._show_first_proposed_curtilage_warning()

        if self._shape_continuous:
            self._rearm_shape()
            return
        # Immediately leave the digitising tool so the user isn't left in draw mode.
        self.iface.actionPan().trigger()

        if self._main_panel:
            self._go_to_phase_screen()

    # ------------------------------------------------------------------ #
    #  SNAPSHOT (pre-fill from recent habitat)                             #
    # ------------------------------------------------------------------ #

    def _apply_snapshot(self, snap):
        """Pre-populate all form combos from a recent-habitat snapshot dict.
        Called by the main panel after the form is constructed."""
        def _set_combo(combo, text):
            idx = combo.findText(text)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        # Habitat type + sub-type
        _set_combo(self.habitat_type_combo, snap.get("hab_type", ""))
        # Repopulate subs for this type first
        self._populate_subs(self.habitat_type_combo, self.sub_combo)

        if self._is_trees_form:
            _set_combo(self.tree_size_combo,     snap.get("tree_size", ""))
            _set_combo(self.tree_location_combo, snap.get("tree_location", ""))
            self._sync_tree_sub()
        else:
            _set_combo(self.sub_combo, snap.get("sub_type", ""))

        _set_combo(self.condition_combo, snap.get("condition", ""))

        # Proposed change fields
        _set_combo(self.prop_type_combo, snap.get("prop_type", ""))
        self._populate_subs(self.prop_type_combo, self.prop_sub_combo)
        if self._is_trees_form:
            _set_combo(self.prop_tree_size_combo,     snap.get("prop_tree_size", ""))
            _set_combo(self.prop_tree_location_combo, snap.get("prop_tree_location", ""))
            if hasattr(self, "_sync_prop_tree_sub"):
                self._sync_prop_tree_sub()
        else:
            _set_combo(self.prop_sub_combo, snap.get("prop_sub", ""))
        _set_combo(self.prop_condition_combo, snap.get("prop_cond", ""))

        # BNG metric details. These two must never be left blank: an empty
        # snapshot value (e.g. a feature placed before they defaulted, or one
        # the user cleared) must fall back to the construction defaults, not
        # select the combos' blank first item. Otherwise a single blank
        # placement self-propagates through the recent-habitat list.
        _set_combo(self.strategic_sig_combo,
                   snap.get("strategic_sig", "") or DEFAULT_STRATEGIC_SIGNIFICANCE)
        # Irreplaceable was already set from the restored sub-type (via
        # _on_sub_changed when sub_combo was set above); only override it when
        # the snapshot actually carries a value.
        if snap.get("irreplaceable", ""):
            _set_combo(self.irreplaceable_combo, snap["irreplaceable"])
        self.hab_ref_input.setText(snap.get("hab_ref", ""))
        self.comments_edit.setPlainText(snap.get("comments", ""))
        # Condition assessment detail (serialised ticks/scores) follows the form
        # so a reopen is pre-filled; blank snapshot clears it for a new feature.
        self._cond_detail = snap.get("condition_detail", "")
        self._prop_cond_detail = ""

        # Restore clip to redline checkbox
        if self.clip_redline_check is not None:
            self.clip_redline_check.setChecked(snap.get("clip_redline", True))
        if self.clip_blueline_check is not None:
            self.clip_blueline_check.setChecked(snap.get("clip_blueline", True))
        if self.cookie_cutter_check is not None:
            self.cookie_cutter_check.setChecked(snap.get("cookie_cutter", False))

    # ── Proximity pairing prompt (trees & hedgerows) ───────────────────────
    PROXIMITY_RADIUS_M = 5.0   # ask "same feature?" within this distance

    # Condition ordering for proximity matching (low -> high)
    _COND_ORDER = ["Poor", "Fairly Poor", "Moderate", "Fairly Good", "Good"]

    def _condition_compatible(self, this_phase, my_cond, other_cond):
        """Whether two conditions are consistent with being the same physical
        feature across baseline/proposed. Same condition always matches. A
        difference only matches if it's a valid uplift: the proposed side is
        better than (or equal to) the baseline side. A downgrade, or any
        non-assessed mismatch, is treated as 'not the same feature' so we don't
        prompt to pair things that clearly differ."""
        if my_cond == other_cond:
            return True
        order = self._COND_ORDER
        if my_cond not in order or other_cond not in order:
            return False   # one is N/A etc. and they differ -> not a match
        # Identify which is baseline-side and which is proposed-side.
        if this_phase == "proposed":
            base_c, prop_c = other_cond, my_cond
        else:
            base_c, prop_c = my_cond, other_cond
        # valid only if proposed condition >= baseline condition (an uplift)
        return order.index(prop_c) >= order.index(base_c)

    def _maybe_prompt_pairing(self, layer, fid):
        """When a tree/hedgerow is placed standalone (paired toggle OFF), look
        for an unlinked feature in the OPPOSITE layer within PROXIMITY_RADIUS_M.
        If found, ask the surveyor whether it's the same physical feature:

            Same  -> stamp a shared pair_id on both  -> classified RETAINED
            New   -> stamp DISTINCT 'solo:' ids on both -> never net,
                     so baseline=Lost and proposed=Created

        No-op for non-tree/hedge layers, when nothing is nearby, or when the
        feature already carries a pair_id (i.e. it was placed as an explicit
        pair via the toggle)."""
        if self.habitat_key not in ("trees", "hedgerow", "watercourse"):
            return
        if fid is None or layer is None or not layer.isValid():
            return
        try:
            feat = layer.getFeature(fid)
        except Exception:
            return
        if not feat or not feat.isValid() or feat.geometry().isNull():
            return
        # Already linked? (placed via the paired toggle) — don't ask.
        pidx = layer.fields().indexOf("pair_id")
        if pidx >= 0 and str(feat["pair_id"] or "").strip():
            return

        # Opposite layer
        this_phase = "proposed" if self.phase == "proposed" else "baseline"
        other_phase = "baseline" if this_phase == "proposed" else "proposed"
        other_key = {"trees": {"baseline": "baseline_trees", "proposed": "proposed_trees"},
                     "hedgerow": {"baseline": "baseline_hedgerow",
                                  "proposed": "proposed_hedgerow"},
                     "watercourse": {"baseline": "baseline_water",
                                     "proposed": "proposed_water"}}[self.habitat_key][other_phase]
        try:
            other = self.layer_manager.get_layer(other_key)
        except Exception:
            other = None
        if other is None or not other.isValid():
            return

        # Find nearest unlinked feature in the other layer within radius.
        # Distances compare against PROXIMITY_RADIUS_M in ground metres, but
        # QgsGeometry.distance() is planar in the layer CRS — on the EPSG:3857
        # habitat layers that reads ~1.6x long at UK latitudes (an effective
        # pairing radius of ~16 m instead of 10 m). So both geometries are
        # reprojected to EPSG:27700 first, where planar metres ARE ground
        # metres. (QgsDistanceArea can't help here: it has no geometry-to-
        # geometry minimum distance, and convertLengthMeasurement only
        # converts units, it doesn't correct the projection.)
        from qgis.core import (QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform, QgsGeometry, QgsProject)
        proj = QgsProject.instance()
        bng  = QgsCoordinateReferenceSystem("EPSG:27700")
        geom = QgsGeometry(feat.geometry())
        try:
            if layer.crs() != bng:
                geom.transform(QgsCoordinateTransform(layer.crs(), bng, proj))
        except Exception:
            pass
        other_to_bng = None
        if other.crs() != bng:
            try:
                other_to_bng = QgsCoordinateTransform(other.crs(), bng, proj)
            except Exception:
                other_to_bng = None

        # What identifies "could be the same physical feature" — the placed
        # feature's matchable attributes. Only an opposite-layer feature with
        # the SAME sub-habitat (and, for trees, same size class) can be a
        # candidate; a Large/Good tree is never "the same" as a Small/Poor one.
        def _match_attrs(f, lyr):
            sub = ""
            cond = ""
            si = lyr.fields().indexOf("sub_habitat_type")
            ci = lyr.fields().indexOf("condition")
            if si >= 0:
                sub = str(f[si] or "").strip()
            if ci >= 0:
                cond = str(f[ci] or "").strip()
            size = ""
            if self.habitat_key == "trees":
                from .layer_manager import tree_size_from_sub_type
                size = tree_size_from_sub_type(sub)
            return sub, cond, size

        my_sub, my_cond, my_size = _match_attrs(feat, layer)

        nearest, nearest_d = None, None
        opidx = other.fields().indexOf("pair_id")
        for of in other.getFeatures():
            og = of.geometry()
            if not og or og.isNull():
                continue
            if opidx >= 0 and str(of["pair_id"] or "").strip():
                continue   # already linked to something else
            # Must share sub-habitat (+ tree size). Condition may legitimately
            # differ ONLY as an uplift (baseline -> better proposed), so we
            # allow same-or-improved condition; a worse/unrelated condition
            # means it isn't the same retained feature.
            o_sub, o_cond, o_size = _match_attrs(of, other)
            if o_sub != my_sub:
                continue
            if self.habitat_key == "trees" and o_size != my_size:
                continue
            # condition: same, or a valid uplift in the proposed direction
            if not self._condition_compatible(this_phase, my_cond, o_cond):
                continue
            try:
                og_bng = QgsGeometry(og)
                if other_to_bng is not None:
                    og_bng.transform(other_to_bng)
                d = geom.distance(og_bng)
            except Exception:
                d = geom.distance(og)
            if d <= self.PROXIMITY_RADIUS_M and (nearest_d is None or d < nearest_d):
                nearest, nearest_d = of, d

        if nearest is None:
            return   # nothing close -> stays solo -> Lost/Created by presence

        # Ask the surveyor.
        from qgis.PyQt.QtWidgets import QMessageBox
        kind = {"trees": "tree", "hedgerow": "hedgerow",
                "watercourse": "watercourse"}.get(self.habitat_key, "feature")
        box = QMessageBox(self)
        box.setWindowTitle("Same %s?" % kind)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            "There's a %s in the %s layer about %.1f m away.\n\n"
            "Is this the SAME physical %s (retained), or a NEW one "
            "(e.g. the old one removed and this one planted)?"
            % (kind, other_phase, nearest_d, kind))
        same_btn = box.addButton("Same (retained)", QMessageBox.ButtonRole.YesRole)
        new_btn  = box.addButton("New / replacement", QMessageBox.ButtonRole.NoRole)
        box.exec()

        import uuid
        if box.clickedButton() is same_btn:
            token = uuid.uuid4().hex            # shared -> RETAINED
            self._set_pair_id(layer, fid, token)
            self._set_pair_id(other, nearest.id(), token)
        else:
            # distinct sentinels -> never net -> Lost + Created
            self._set_pair_id(layer, fid, "solo:" + uuid.uuid4().hex)
            self._set_pair_id(other, nearest.id(), "solo:" + uuid.uuid4().hex)

        # refresh summaries
        if self._main_panel:
            from qgis.PyQt.QtCore import QTimer
            for fn in ("_refresh_balance_widget", "_refresh_bng_widget"):
                if hasattr(self._main_panel, fn):
                    QTimer.singleShot(0, getattr(self._main_panel, fn))

    def _set_pair_id(self, layer, fid, token):
        """Write a pair_id token onto one feature via dataProvider."""
        try:
            idx = layer.fields().indexOf("pair_id")
            if idx < 0:
                from .layer_manager import LayerManager
                LayerManager._ensure_pair_id(layer)
                idx = layer.fields().indexOf("pair_id")
            if idx < 0:
                return
            layer.dataProvider().changeAttributeValues({fid: {idx: token}})
            layer.reload()
            layer.triggerRepaint()
        except Exception:
            pass

    def _stamp_pair_id(self, bl_layer, bl_fid, pr_layer, pr_fid):
        """Write a shared uuid into the 'pair_id' field of a baseline feature
        and its proposed twin, so the tree/hedge classifier knows they are a
        linked Retained/Enhanced pair. Safe no-op if anything is missing."""
        import uuid
        if bl_layer is None or pr_layer is None or bl_fid is None or pr_fid is None:
            return
        token = uuid.uuid4().hex
        for layer, fid in ((bl_layer, bl_fid), (pr_layer, pr_fid)):
            try:
                idx = layer.fields().indexOf("pair_id")
                if idx < 0:
                    # field may be absent on an older layer — add it
                    from .layer_manager import LayerManager
                    LayerManager._ensure_pair_id(layer)
                    idx = layer.fields().indexOf("pair_id")
                if idx < 0:
                    continue
                layer.dataProvider().changeAttributeValues({fid: {idx: token}})
                layer.reload()
            except Exception:
                pass

    def _write_baseline_from_proposed(self, geom, hab_type, sub_type, condition,
                                       retention, extra=None, drawn_geom=None):
        """Write a baseline feature that was generated from a proposed drawing.
        Works like _write_proposed but targets the baseline layer.
        extra may include prop_type/prop_sub/prop_cond describing the proposed state.
        If drawn_geom is supplied it is passed to _clip_feature_to_redline so that
        the geometry subtraction uses the edit-buffer geometry directly.
        """
        extra     = extra or {}
        display_sub = str(sub_type or "").strip()
        metric_sub = canonical_habitat_name(display_sub)
        display_prop_sub = str(extra.get("prop_sub") or "").strip()
        metric_prop_sub = canonical_habitat_name(display_prop_sub)
        bl_key    = self._layer_key("baseline")
        try:
            bl_layer = self.layer_manager.get_or_create_layer(bl_key)
        except RuntimeError as e:
            if "unsaved_project" in str(e):
                from qgis.PyQt.QtWidgets import QMessageBox
                QMessageBox.warning(None, "Save project first",
                    "Please save your QGIS project before saving habitat features.")
                return None, None
            raise

        hab_key_val = HABITAT_TYPES.get(hab_type, {}).get(display_sub, {}).get("key", "")
        feat = QgsFeature(bl_layer.fields())

        feat.setGeometry(geom)

        def s(name, val):
            idx = bl_layer.fields().indexFromName(name)
            if idx >= 0:
                feat.setAttribute(idx, val)

        s("habitat_type",                   hab_type)
        s("sub_habitat_type",               metric_sub)
        s("habitat_key",                    hab_key_val)
        s("condition",                      condition)
        s("condition_detail",               extra.get("condition_detail", ""))
        s("retention",                      retention)
        s("phase",                          "baseline")
        if extra.get("strategic_sig"):
            s("strategic_significance",     extra["strategic_sig"])
        if extra.get("irreplaceable"):
            s("irreplaceable_habitat",      extra["irreplaceable"])
        if extra.get("comments"):
            s("user_comments",              extra["comments"])
        if extra.get("hab_ref"):
            s("habitat_ref",                extra["hab_ref"])
        if extra.get("prop_type"):
            s("prop_habitat_type",          extra["prop_type"])
        if display_prop_sub:
            s("prop_sub_habitat_type",      metric_prop_sub)
        if extra.get("prop_cond"):
            s("prop_condition",             extra["prop_cond"])
        s("Baseline Habitat Type",          display_sub)
        s("Proposed Habitat Type",          display_prop_sub or display_sub)
        s("Baseline Condition",             condition)
        s("Proposed Condition",             extra.get("prop_cond") or condition)
        s("Retention Category",             retention)

        ok, new_feats = bl_layer.dataProvider().addFeatures([feat])
        bl_layer.reload()
        bl_layer.updateExtents()
        bl_layer.triggerRepaint()

        if ok and new_feats:
            bl_fid = new_feats[0].id()
            final_fids = [bl_fid]
            # Clip to redline (same logic as proposed layer).
            # Pass drawn_geom so difference() uses edit-buffer coordinates.
            if (getattr(self, "clip_redline_check", None) is not None
                    and self.clip_redline_check.isChecked()):
                result_fids = self._clip_feature_to_redline(bl_layer, bl_fid, drawn_geom)
                bl_layer.reload()
                if isinstance(result_fids, list) and result_fids:
                    bl_fid = result_fids[0]
                    final_fids = list(result_fids)
                elif not result_fids:
                    return [], bl_layer

            # ── Clip to Blue Line Boundary ────────────────────────────
            if (getattr(self, "clip_blueline_check", None) is not None
                    and self.clip_blueline_check.isChecked()
                    and not (getattr(self, "clip_redline_check", None) is not None
                             and self.clip_redline_check.isChecked())):
                remaining = []
                for fid_to_clip in final_fids:
                    r = self._clip_feature_to_blueline(bl_layer, fid_to_clip,
                                                       drawn_geom if fid_to_clip == bl_fid else None)
                    if isinstance(r, list):
                        remaining.extend(r)
                bl_layer.reload()
                final_fids = remaining
                if not final_fids:
                    return [], bl_layer

            # ── Cookie cutter: the finished (fully clipped) footprint takes
            # priority — punch it out of every parcel already underneath.
            if final_fids and self._cookie_cutter_on():
                self._cookie_cut_under(bl_layer, final_fids)

            return final_fids, bl_layer
        return [], bl_layer

    def _write_proposed(self, geom, hab_type, sub_type, condition,
                         retention, extra=None):
        """
        Write one feature to the proposed layer via dataProvider (no edit
        buffer needed — permanent immediately).  Returns (fid, layer).
        """
        extra      = extra or {}
        display_sub = str(sub_type or "").strip()
        metric_sub = canonical_habitat_name(display_sub)
        prop_key   = self._layer_key("proposed")
        try:
            prop_layer = self.layer_manager.get_or_create_layer(prop_key)
        except RuntimeError as e:
            if "unsaved_project" in str(e):
                from qgis.PyQt.QtWidgets import QMessageBox
                QMessageBox.warning(
                    None, "Save project first",
                    "Please save your QGIS project (Project → Save As…) "
                    "before saving habitat features.",
                )
                return None, None
            raise
        hab_key    = HABITAT_TYPES.get(hab_type, {}).get(display_sub, {}).get("key", "")

        feat   = QgsFeature(prop_layer.fields())

        feat.setGeometry(geom)

        def s(name, val):
            idx = prop_layer.fields().indexFromName(name)
            if idx >= 0:
                feat.setAttribute(idx, val)

        s("habitat_type",                   hab_type)
        s("sub_habitat_type",               metric_sub)
        s("habitat_key",                    hab_key)
        s("condition",                      condition)
        s("retention",                      retention)
        s("phase",                          "proposed")
        # Extra BNG metric fields (only write non-blank)
        if extra.get("strategic_sig"):
            s("strategic_significance",     extra["strategic_sig"])
        if extra.get("irreplaceable"):
            s("irreplaceable_habitat",      extra["irreplaceable"])
        if extra.get("comments"):
            s("user_comments",              extra["comments"])
        if extra.get("hab_ref"):
            s("habitat_ref",                extra["hab_ref"])
        # Watercourse encroachment must be carried onto the twin. The metric
        # scores a reach as distinctiveness x condition x significance x
        # channel x banks, and a BLANK encroachment resolves to a multiplier of
        # ZERO — so an auto-retained ditch whose twin missed these fields
        # delivers no units at all and reads as a total loss.
        if extra.get("water_enc"):
            s("watercourse_encroachment",   extra["water_enc"])
        if extra.get("rip_l"):
            s("riparian_encroachment_l",    extra["rip_l"])
        if extra.get("rip_r"):
            s("riparian_encroachment_r",    extra["rip_r"])
        s("Baseline Habitat Type",          display_sub)
        s("Proposed Habitat Type",          display_sub)
        s("Baseline Hedge Type",            display_sub)
        s("Proposed Hedge Type",            display_sub)
        s("Baseline River Type",            display_sub)
        s("Proposed River Type",            display_sub)
        s("Baseline Condition",             condition)
        s("Proposed Condition",             condition)
        s("Retention Category",             retention)
        # Tree QML style fields — derived from sub_type
        if self.habitat_key == "trees":
            tree_size     = tree_size_from_sub_type(metric_sub)
            tree_location = tree_location_from_sub_type(metric_sub)
            s("Category",                       "Proposed")
            s("Baseline Tree Size",             tree_size)
            s("Baseline Rural or Urban Tree",   tree_location)
            s("Proposed Tree Size",             tree_size)
            s("Proposed Rural or Urban Tree",   tree_location)

        ok, new_feats = prop_layer.dataProvider().addFeatures([feat])
        prop_layer.reload()
        prop_layer.updateExtents()
        prop_layer.triggerRepaint()

        if ok and new_feats:
            pr_fid = new_feats[0].id()
            return pr_fid, prop_layer
        return None, prop_layer
