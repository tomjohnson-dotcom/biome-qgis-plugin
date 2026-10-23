# hedge_trimmer.py
"""Hedge / watercourse trimmer — cut a section out of a placed line feature.

Armed from a Manage-tab scissors button for one specific hedgerow or
watercourse feature. Two clicks on (or near) the line define the gap; the
section between them is removed from the layer the tool was armed on.

The trim is DIRECTIONAL: only the armed layer changes visibly. When the
feature is half of a linked baseline<->proposed pair, the OTHER layer is
split at the same chainages into collinear segments that render identically
(the map there looks untouched) so the pair-based net classifier can score
each segment independently:

    armed on PROPOSED  -> proposed shows the gap; the matching hidden
                          baseline segment is left unlinked  -> LOST
    armed on BASELINE  -> baseline shows the gap; the matching hidden
                          proposed segment is left unlinked  -> CREATED

Kept segments are re-paired side-for-side: the surviving original features
keep their existing pair token; a second kept segment gets a fresh shared
token. Unlinked features get a plain geometry cut with no side effects.

Undo: one global-undo entry per trim — the added segment features ride the
entry's delete slots and the two shortened originals ride the ``repairs``
geometry-mods slot, so a single Undo (and Redo) restores the exact
pre-trim state through the existing sig-checked machinery.

All writes go straight through the data providers (no edit sessions), the
same as the global undo and the Manage duplicate button.
"""

import uuid

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.PyQt.QtWidgets import QLabel, QMessageBox
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsDistanceArea, QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker

from . import theme
from .config import measurement_ellipsoid
from .panel_widgets import DUP_PAIR

# Keys the trimmer works on (line features with pair-based classification).
TRIM_KEYS = ("baseline_hedgerow", "proposed_hedgerow",
             "baseline_water", "proposed_water")

# A kept end shorter than this (ground metres) is dropped as a sliver —
# the cut then behaves as an end-shortening rather than a mid-gap.
SLIVER_KEEP_M = 0.5
# A gap shorter than this is a misclick, not a trim.
MIN_GAP_M = 0.05
# Clicks farther than this (ground metres) from the armed line are ignored.
CLICK_TOL_M = 25.0


# ── geometry helpers ─────────────────────────────────────────────────────────

def _as_single_line(geom):
    """Return a single-part LineString QgsGeometry copy, or None if the
    geometry is missing, not a line, or has more than one part."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    if geom.type() != QgsWkbTypes.GeometryType.LineGeometry:
        return None
    if geom.isMultipart():
        parts = [p for p in geom.asGeometryCollection()
                 if p is not None and not p.isNull() and not p.isEmpty()]
        if len(parts) != 1:
            return None
        return QgsGeometry(parts[0])
    return QgsGeometry(geom)


def _substring(line_geom, a, b):
    """Substring of a single-part line between chainages a..b (layer units).
    Returns a QgsGeometry or None when empty/degenerate."""
    if b - a <= 0:
        return None
    try:
        sub = line_geom.constGet().curveSubstring(a, b)
    except Exception:
        return None
    if sub is None:
        return None
    g = QgsGeometry(sub)
    if g.isNull() or g.isEmpty() or g.constGet().numPoints() < 2:
        return None
    return g


def _ground_da(crs):
    """QgsDistanceArea measuring ellipsoidal ground metres from `crs`
    (the canonical measurements.py pattern — never planar 3857)."""
    da = QgsDistanceArea()
    try:
        da.setSourceCrs(crs, QgsProject.instance().transformContext())
        da.setEllipsoid(measurement_ellipsoid())
    except Exception:
        pass
    return da


def _ground_len(da, geom):
    if geom is None:
        return 0.0
    try:
        return abs(da.measureLength(geom))
    except Exception:
        return 0.0


def _attrs_of(layer, feat):
    """{field: value} snapshot of every non-fid attribute."""
    d = {}
    for i, fld in enumerate(layer.fields()):
        if fld.name().lower() == "fid":
            continue
        d[fld.name()] = feat.attribute(i)
    return d


def _set_if_present(attrs, name, value):
    if name in attrs:
        attrs[name] = value


def _pair_token(feat):
    """The feature's pair_id, '' when absent/blank."""
    try:
        v = feat["pair_id"]
    except Exception:
        return ""
    return str(v).strip() if v not in (None, "") else ""


def _find_twin(panel, key, token):
    """(layer, feature) holding the same real pair token in the paired layer,
    or (None, None). 'solo:' sentinels never have a twin by design."""
    if not token or token.startswith("solo:"):
        return None, None
    other_key = DUP_PAIR.get(key)
    if not other_key:
        return None, None
    try:
        other = panel.layer_manager.get_layer(other_key)
    except Exception:
        other = None
    if other is None or not other.isValid():
        return None, None
    for f in other.getFeatures():
        if _pair_token(f) == token:
            return other, f
    return None, None


def _add_feature(layer, geom, attrs):
    """Provider-level add. Returns the new fid or None."""
    feat = QgsFeature(layer.fields())
    feat.setGeometry(geom)
    for name, val in attrs.items():
        idx = layer.fields().indexOf(name)
        if idx >= 0:
            feat.setAttribute(idx, val)
    try:
        ok, added = layer.dataProvider().addFeatures([feat])
    except Exception:
        return None
    if not ok or not added:
        return None
    return added[0].id()


# ── the trim itself ──────────────────────────────────────────────────────────

def perform_trim(panel, layer, fid, key, d1, d2):
    """Cut chainage interval d1..d2 (layer units, along the armed feature's
    line) out of feature `fid`. Handles paired and unlinked features, writes
    one global-undo entry. Returns (ok, user_message)."""
    if layer is None or not layer.isValid():
        return False, "The feature's layer is no longer available."
    try:
        feat = layer.getFeature(fid)
    except Exception:
        feat = None
    if feat is None or not feat.isValid():
        return False, "That feature no longer exists."
    line = _as_single_line(feat.geometry())
    if line is None:
        return False, "Only single-part line features can be trimmed."

    da = _ground_da(layer.crs())
    total = line.constGet().length()
    d1, d2 = sorted((max(0.0, min(d1, total)), max(0.0, min(d2, total))))

    gap_armed = _substring(line, d1, d2)
    if _ground_len(da, gap_armed) < MIN_GAP_M:
        return False, "The two points land on the same spot — nothing to trim."

    keep_start = _ground_len(da, _substring(line, 0.0, d1)) >= SLIVER_KEEP_M
    keep_end = _ground_len(da, _substring(line, d2, total)) >= SLIVER_KEEP_M
    if not keep_start and not keep_end:
        return False, ("That cut removes the whole feature — "
                       "delete it from Manage Features instead.")

    # ── locate the linked twin (if any) and its matching chainages ──────
    token = _pair_token(feat)
    twin_layer, twin_feat = _find_twin(panel, key, token)
    twin_line = None
    if twin_feat is not None:
        twin_line = _as_single_line(twin_feat.geometry())
        if twin_line is None:
            # Twin exists but isn't a clean line — leave it alone rather
            # than corrupt it; trim the armed side only.
            twin_layer, twin_feat = None, None

    # Unsaved edit sessions on a touched layer would tangle with the
    # provider-level writes below (same guard the global undo applies).
    for lyr in (layer, twin_layer):
        if lyr is not None and lyr.isEditable() and lyr.isModified():
            return False, ('Can\'t trim while "%s" has unsaved edits — finish '
                           "or cancel that edit session first." % lyr.name())

    t1 = t2 = None
    if twin_line is not None:
        twin_total = twin_line.constGet().length()
        locs = []
        for d in (d1, d2):
            pt = line.interpolate(d)
            loc = -1.0
            try:
                loc = twin_line.lineLocatePoint(pt)
            except Exception:
                loc = -1.0
            if loc < 0:
                # Fallback: proportional chainage (twin drawn as a verbatim
                # copy, so proportions match even if locate fails).
                loc = (d / total) * twin_total if total > 0 else 0.0
            locs.append(max(0.0, min(loc, twin_total)))
        t1, t2 = sorted(locs)

    # ── build every new/changed geometry BEFORE writing anything ────────
    # Armed layer: the original keeps the first kept piece; a second kept
    # piece becomes a new feature. The gap piece is simply not re-created.
    armed_first = _substring(line, 0.0, d1) if keep_start \
        else _substring(line, d2, total)
    armed_second = _substring(line, d2, total) if (keep_start and keep_end) \
        else None
    if armed_first is None:
        return False, "Couldn't compute the trimmed geometry."

    # Hidden twin layer: same kept pieces (its own chainages) plus a gap
    # piece, so the twin's line stays visually complete end-to-end.
    twin_first = twin_second = twin_gap = None
    if twin_line is not None:
        twin_total = twin_line.constGet().length()
        twin_first = _substring(twin_line, 0.0, t1) if keep_start \
            else _substring(twin_line, t2, twin_total)
        twin_second = _substring(twin_line, t2, twin_total) \
            if (keep_start and keep_end) else None
        gs = t1 if keep_start else 0.0
        ge = t2 if keep_end else twin_total
        twin_gap = _substring(twin_line, gs, ge)
        if twin_first is None:
            # Degenerate twin (e.g. near-zero length) — trim armed side only.
            twin_layer, twin_feat = None, None
            twin_second = twin_gap = None

    paired = twin_feat is not None
    second_token = uuid.uuid4().hex if (paired and armed_second is not None) \
        else None

    src_attrs = _attrs_of(layer, feat)

    # ── apply: adds first (they can fail cleanly), then geometry mods ───
    adds = []          # (layer, fid) in add order, for undo bookkeeping

    def _abort_adds():
        for lyr, afid in adds:
            try:
                lyr.dataProvider().deleteFeatures([afid])
            except Exception:
                pass

    if armed_second is not None:
        attrs = dict(src_attrs)
        if second_token:
            _set_if_present(attrs, "pair_id", second_token)
        elif str(attrs.get("pair_id") or "").startswith("solo:"):
            # A duplicated 'solo:' sentinel would collide in the classifier's
            # one-per-id buckets — give the new piece its own.
            _set_if_present(attrs, "pair_id", "solo:" + uuid.uuid4().hex)
        new_fid = _add_feature(layer, armed_second, attrs)
        if new_fid is None:
            return False, "Couldn't add the second kept segment — trim aborted."
        adds.append((layer, new_fid))

    if paired:
        twin_attrs = _attrs_of(twin_layer, twin_feat)
        if twin_second is not None:
            attrs = dict(twin_attrs)
            _set_if_present(attrs, "pair_id", second_token or "")
            new_fid = _add_feature(twin_layer, twin_second, attrs)
            if new_fid is None:
                _abort_adds()
                return False, "Couldn't split the linked twin — trim aborted."
            adds.append((twin_layer, new_fid))
        if twin_gap is not None:
            attrs = dict(twin_attrs)
            _set_if_present(attrs, "pair_id", "")
            _set_if_present(attrs, "retention", "")
            _set_if_present(attrs, "Retention Category", "")
            new_fid = _add_feature(twin_layer, twin_gap, attrs)
            if new_fid is None:
                _abort_adds()
                return False, "Couldn't split the linked twin — trim aborted."
            adds.append((twin_layer, new_fid))

    # Geometry mods on the surviving originals (undo restores these WKTs).
    mods = []
    before_armed = feat.geometry().asWkt()
    if not layer.dataProvider().changeGeometryValues({fid: armed_first}):
        _abort_adds()
        return False, "Couldn't shorten the feature — trim aborted."
    mods.append([layer.id(), fid, before_armed, armed_first.asWkt()])

    if paired:
        before_twin = twin_feat.geometry().asWkt()
        if not twin_layer.dataProvider().changeGeometryValues(
                {twin_feat.id(): twin_first}):
            # Roll the armed layer back so nothing is half-trimmed.
            try:
                layer.dataProvider().changeGeometryValues(
                    {fid: QgsGeometry.fromWkt(before_armed)})
            except Exception:
                pass
            _abort_adds()
            return False, "Couldn't split the linked twin — trim aborted."
        mods.append([twin_layer.id(), twin_feat.id(),
                     before_twin, twin_first.asWkt()])

    # ── one global-undo entry for the whole trim ────────────────────────
    # Added features go in the entry's delete slots (first baseline add in
    # the baseline slot, first proposed add in the proposed slot, the rest
    # as extra fids/pairs); the shortened originals ride repairs["mods"].
    def _is_baseline(lyr):
        armed_baseline = key.startswith("baseline_")
        return armed_baseline if lyr is layer else not armed_baseline

    b_adds = [(l, f) for l, f in adds if _is_baseline(l)]
    p_adds = [(l, f) for l, f in adds if not _is_baseline(l)]
    try:
        panel.push_undo(
            b_adds[0][0] if b_adds else None,
            b_adds[0][1] if b_adds else None,
            p_adds[0][0] if p_adds else None,
            p_adds[0][1] if p_adds else None,
            extra_baseline_fids=[f for _, f in b_adds[1:]],
            extra_pairs=list(p_adds[1:]),
            repairs={"mods": mods},
        )
    except Exception:
        pass

    for lyr in ({layer, twin_layer} - {None}):
        try:
            lyr.reload()
            lyr.updateExtents()
            lyr.triggerRepaint()
        except Exception:
            pass

    gap_m = _ground_len(da, gap_armed)
    kept_bits = [g for g in (armed_first, armed_second) if g is not None]
    kept_txt = " + ".join("%.1f m" % _ground_len(da, g) for g in kept_bits)
    what = "watercourse" if "water" in key else "hedgerow"
    if paired:
        fate = "Lost" if twin_layer is not None and key.startswith("proposed_") \
            else "Created"
        msg = ("Trimmed %.1f m out of the %s (%s kept). The linked %s layer "
               "is unchanged on the map; the trimmed section now counts as "
               "%s in the metric."
               % (gap_m, what, kept_txt,
                  "baseline" if key.startswith("proposed_") else "proposed",
                  fate))
    else:
        msg = "Trimmed %.1f m out of the %s (%s kept)." % (gap_m, what, kept_txt)
    return True, msg


# ── the map tool ─────────────────────────────────────────────────────────────

class HedgeTrimTool(QgsMapToolEmitPoint):
    """Two-click gap picker for one armed hedgerow/watercourse feature.

    Click 1 marks where the gap starts, click 2 where it ends (both snapped
    onto the line); a red preview and a floating ground-metres label track
    the cursor in between. Confirms, performs the trim, then stays armed for
    further cuts on the same feature. Right-click or Escape exits.
    """

    def __init__(self, canvas, iface, panel, layer, fid, key):
        super().__init__(canvas)
        self.iface = iface
        self.panel = panel
        self.layer = layer
        self.fid = fid
        self.key = key
        self.valid = False

        self._line = None          # cached single-part line (layer CRS)
        self._line_bng = None      # same line in EPSG:27700 (ground maths)
        self._d1 = None            # first click chainage (layer units)
        self._marker = None
        self._msg_item = None
        self._halo = None
        self._gap_band = None
        self._lbl = None
        self._da = _ground_da(layer.crs()) if layer is not None else None

        if not self._refresh_line():
            return
        self.valid = True

        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

        # Teal halo on the armed line so it's obvious what will be cut.
        self._halo = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._halo.setColor(QColor(0, 190, 210, 140))
        self._halo.setWidth(7)
        self._halo.setToGeometry(self._line, layer)

        # Red preview of the section being removed.
        self._gap_band = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._gap_band.setColor(QColor(179, 55, 47, 220))
        self._gap_band.setWidth(4)

        # Floating cut-length label (custom tools own their move events, so
        # no live_measure event filter — see live_measure.py's module note).
        self._lbl = QLabel(canvas.viewport())
        self._lbl.setStyleSheet(
            "background:%s; color:%s; border-radius:5px; padding:3px 8px;"
            "font-family:%s; font-size:11px; font-weight:600;"
            % (theme.INK, theme.SURFACE, theme.FONT_SEMIBOLD))
        self._lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._lbl.hide()

        bar = iface.messageBar()
        self._msg_item = bar.createMessage(
            "Hedge Trimmer",
            "Click where the gap starts, then where it ends — the section "
            "between is removed. Right-click or Esc to finish.")
        bar.pushWidget(self._msg_item, Qgis.MessageLevel.Info, 0)

    # ── geometry cache ──────────────────────────────────────────────────
    def _refresh_line(self):
        """(Re)read the armed feature. Returns False when it's unusable."""
        try:
            feat = self.layer.getFeature(self.fid)
        except Exception:
            feat = None
        if feat is None or not feat.isValid():
            return False
        line = _as_single_line(feat.geometry())
        if line is None:
            return False
        self._line = line
        bng = QgsCoordinateReferenceSystem("EPSG:27700")
        g = QgsGeometry(line)
        try:
            if self.layer.crs() != bng:
                g.transform(QgsCoordinateTransform(
                    self.layer.crs(), bng, QgsProject.instance()))
        except Exception:
            pass
        self._line_bng = g
        return True

    def _click_to_chainage(self, event):
        """Chainage (layer units) of the click projected onto the line, or
        None when the click is too far away (CLICK_TOL_M ground metres)."""
        map_pt = self.toMapCoordinates(event.pos())
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        pt = QgsGeometry.fromPointXY(QgsPointXY(map_pt))
        # Ground-metre distance check in 27700 (planar 3857 reads ~1.6x long).
        bng = QgsCoordinateReferenceSystem("EPSG:27700")
        pt_bng = QgsGeometry(pt)
        try:
            if canvas_crs != bng:
                pt_bng.transform(QgsCoordinateTransform(
                    canvas_crs, bng, QgsProject.instance()))
            if self._line_bng.distance(pt_bng) > CLICK_TOL_M:
                return None
        except Exception:
            pass
        # Chainage in the LAYER CRS (where the cut happens).
        pt_lyr = QgsGeometry(pt)
        try:
            if canvas_crs != self.layer.crs():
                pt_lyr.transform(QgsCoordinateTransform(
                    canvas_crs, self.layer.crs(), QgsProject.instance()))
        except Exception:
            pass
        try:
            d = self._line.lineLocatePoint(pt_lyr)
        except Exception:
            d = -1.0
        return d if d >= 0 else None

    # ── canvas events ───────────────────────────────────────────────────
    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._exit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._refresh_line():
            self.iface.messageBar().pushMessage(
                "Hedge Trimmer", "That feature is no longer available.",
                level=Qgis.MessageLevel.Warning, duration=4)
            self._exit()
            return
        d = self._click_to_chainage(event)
        if d is None:
            self.iface.messageBar().pushMessage(
                "Hedge Trimmer",
                "Click on (or within %.0f m of) the highlighted line."
                % CLICK_TOL_M,
                level=Qgis.MessageLevel.Info, duration=3)
            return

        if self._d1 is None:
            self._d1 = d
            pt = self._line.interpolate(d).asPoint()
            self._marker = QgsVertexMarker(self.canvas())
            self._marker.setCenter(self._to_canvas_pt(pt))
            self._marker.setColor(QColor(179, 55, 47))
            self._marker.setIconSize(11)
            self._marker.setIconType(QgsVertexMarker.IconType.ICON_CROSS)
            self._marker.setPenWidth(3)
            return

        # Second click — confirm and trim.
        d1, d2 = sorted((self._d1, d))
        gap = _substring(self._line, d1, d2)
        gap_m = _ground_len(self._da, gap)
        if gap_m < MIN_GAP_M:
            return
        what = "watercourse" if "water" in self.key else "hedgerow"
        reply = QMessageBox.question(
            self.iface.mainWindow(), "Trim %s" % what,
            "Remove %.1f m from this %s?" % (gap_m, what),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        self._reset_pick()
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, msg = perform_trim(self.panel, self.layer, self.fid, self.key,
                               d1, d2)
        self.iface.messageBar().pushMessage(
            "Hedge Trimmer", msg,
            level=(Qgis.MessageLevel.Success if ok
                   else Qgis.MessageLevel.Warning),
            duration=7)
        if ok:
            try:
                self.panel._mark_summary_dirty()
                self.panel._manage_dirty = True
                self.panel._schedule_manage_rebuild()
                self.panel._refresh_phase_screens()
            except Exception:
                pass
            self.iface.mapCanvas().refresh()
        if not self._refresh_line():
            self._exit()
            return
        self._halo.setToGeometry(self._line, self.layer)

    def canvasMoveEvent(self, event):
        if self._d1 is None or self._line is None:
            return
        d = self._click_to_chainage(event)
        if d is None:
            self._gap_band.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self._lbl.hide()
            return
        a, b = sorted((self._d1, d))
        gap = _substring(self._line, a, b)
        if gap is None:
            self._gap_band.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self._lbl.hide()
            return
        self._gap_band.setToGeometry(gap, self.layer)
        self._lbl.setText("cut %.1f m" % _ground_len(self._da, gap))
        self._lbl.adjustSize()
        p = event.pos()
        viewport = self.canvas().viewport()
        x = p.x() + 16
        y = p.y() - self._lbl.height() - 4
        if x + self._lbl.width() > viewport.width():
            x = p.x() - self._lbl.width() - 8
        if y < 0:
            y = p.y() + 16
        self._lbl.move(x, y)
        self._lbl.show()
        self._lbl.raise_()

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._d1 is not None:
                self._reset_pick()      # first Esc abandons the current pick
            else:
                self._exit()            # second exits the tool
        else:
            super().keyReleaseEvent(event)

    # ── plumbing ────────────────────────────────────────────────────────
    def _to_canvas_pt(self, pt_layer):
        """Layer-CRS point -> canvas-CRS QgsPointXY for markers."""
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        g = QgsGeometry.fromPointXY(QgsPointXY(pt_layer))
        try:
            if canvas_crs != self.layer.crs():
                g.transform(QgsCoordinateTransform(
                    self.layer.crs(), canvas_crs, QgsProject.instance()))
        except Exception:
            pass
        return g.asPoint()

    def _reset_pick(self):
        self._d1 = None
        if self._marker is not None:
            self.canvas().scene().removeItem(self._marker)
            self._marker = None
        if self._gap_band is not None:
            self._gap_band.reset(QgsWkbTypes.GeometryType.LineGeometry)
        if self._lbl is not None:
            self._lbl.hide()

    def _clear(self):
        self._reset_pick()
        try:
            if self._halo is not None:
                self._halo.reset(QgsWkbTypes.GeometryType.LineGeometry)
                self.canvas().scene().removeItem(self._halo)
            if self._gap_band is not None:
                self.canvas().scene().removeItem(self._gap_band)
        except Exception:
            pass
        if self._lbl is not None:
            self._lbl.hide()
            self._lbl.deleteLater()
            self._lbl = None
        item = self._msg_item
        if item is not None:
            self._msg_item = None
            try:
                self.iface.messageBar().popWidget(item)
            except Exception:
                pass

    def deactivate(self):
        """Runs however the tool ends (exit, another tool picked, disarm)."""
        self._clear()
        try:
            self.panel._trim_ui_reset()
        except Exception:
            pass
        super().deactivate()

    def _exit(self):
        self.canvas().unsetMapTool(self)
        self.iface.actionPan().trigger()
