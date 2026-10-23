# fov_label_arcs.py
# Curved FOV-label support. QGIS can only curve label text along a LINE, so for
# every field-of-view wedge (Camera / Surveyor / Observer Station) we generate a
# small arc behind its symbol — on the side OPPOSITE the cone — and put the ref
# as a curved label on that arc. The arc is a throwaway memory layer rebuilt from
# the wedge features on demand (dialog open, placement, zoom, export); it never
# persists and never counts toward BNG.
#
# The arc radius is screen-constant (sized from the current map scale) so the
# curved text always fits and never gets culled at zoom — the whole reason a
# fixed-metre arc won't do. The sweep adapts to the label length, so a long ref
# wraps further around the icon rather than overflowing a too-short arc.

import math

ARC_LAYER_NAME = "Ecological FOV Labels"


def _place_arc_in_eco_group(project, layer):
    """Keep the generated label layer inside Ecological Features.

    The layer remains first within that group so its labels render above the
    ecological symbols. Existing projects with the old top-level node are
    migrated in place the next time labels refresh.
    """
    root = project.layerTreeRoot()
    eco = root.findGroup("Ecological Features")
    if eco is None:
        eco = root.insertGroup(0, "Ecological Features")
    node = root.findLayer(layer.id())
    if node is not None and node.parent() is eco:
        return node
    clone = node.clone() if node is not None else None
    if clone is not None:
        eco.insertChildNode(0, clone)
        node.parent().removeChildNode(node)
        return clone
    return eco.insertLayer(0, layer)
WEDGE_TOKEN = "Field of View"          # every wedge ftype contains this

# Geometry of the arc, in screen millimetres (converted to map units per call).
_ARC_RADIUS_MM = 1.0                    # multiplied by the icon size below
_ARC_MIN_SWEEP = math.radians(90.0)
_ARC_MAX_SWEEP = math.radians(320.0)
_ARC_SEGMENTS  = 28
_ARC_TEXT_SLACK = 1.6                   # arc length ÷ text length (curved-fit headroom)


def _apex_and_cone_dir(pts):
    """Given the wedge ring vertices (no closing duplicate), return
    (apex QgsPointXY, cone_direction_radians).

    The apex is the pointy vertex where the two long radii meet — found as the
    vertex whose two adjacent edges are BOTH longest (every arc vertex has at
    least one short chord edge). This is robust to vertex order, so it works for
    wedges loaded from a GeoPackage or relocated, not just freshly drawn ones.
    The cone opens from the apex toward the mean of the arc vertices."""
    n = len(pts)
    best_i, best_score = 0, -1.0
    for i in range(n):
        a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        e1 = math.hypot(b.x() - a.x(), b.y() - a.y())
        e2 = math.hypot(b.x() - c.x(), b.y() - c.y())
        score = min(e1, e2)
        if score > best_score:
            best_score, best_i = score, i
    apex = pts[best_i]
    ox = sum(p.x() for j, p in enumerate(pts) if j != best_i) / max(1, n - 1)
    oy = sum(p.y() for j, p in enumerate(pts) if j != best_i) / max(1, n - 1)
    return apex, math.atan2(oy - apex.y(), ox - apex.x())


def _ring_points(geom):
    """Exterior-ring vertices of a (multi)polygon as a list of QgsPointXY,
    without the closing duplicate. Returns [] on anything unexpected."""
    from qgis.core import QgsPointXY
    try:
        poly = geom.asPolygon()
        if not poly:
            mp = geom.asMultiPolygon()
            poly = mp[0] if mp else None
        if not poly:
            return []
        ring = [QgsPointXY(p) for p in poly[0]]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        return ring
    except Exception:
        return []


def _units_per_mm(canvas, scale_override=None):
    """Map units per *screen* millimetre at the current canvas view (or per
    *paper* millimetre when scale_override, a scale denominator, is given for an
    export layout). Used to keep the arc a constant size on the page."""
    ms = canvas.mapSettings()
    if scale_override:
        # 1 mm on paper = scale_denominator mm in map units = denom/1000 units.
        return float(scale_override) / 1000.0
    mupp = ms.mapUnitsPerPixel()
    try:
        dpi = canvas.physicalDpiX() or 96.0
    except Exception:
        dpi = 96.0
    return mupp * (dpi / 25.4)


def _est_text_mm(label, font_pt):
    """Rough rendered width of *label* in mm (Inter, mixed case)."""
    return len(str(label)) * font_pt * 0.55 / 2.835


def _int_or_none(f, name):
    """Read an int field, returning None for a missing field or a NULL value
    (which may come back as a NULL QVariant, not Python None)."""
    try:
        v = f[name]
    except (KeyError, IndexError):
        return None
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def labels_hidden():
    """Master 'all labels off' switch for the ecological feature layers, stored
    on the project so it persists with the map and is readable from every arc
    caller (placer dialog, zoom rebuild, export). Default: labels shown."""
    try:
        from qgis.core import QgsProject
        val, ok = QgsProject.instance().readBoolEntry(
            "biome", "eco_labels_hidden", False)
        return bool(val) if ok else False
    except Exception:
        return False


def _feat_flags(f):
    """Per-feature label flags: (hidden, curved). Defaults (missing/NULL field)
    are show + curved. Robust to layers that predate the fields."""
    h = _int_or_none(f, "hide_label")
    c = _int_or_none(f, "label_curved")
    hidden = (h == 1)
    curved = (c is None) or (c != 0)
    return hidden, curved


def _wants_arc(f, mode):
    """Whether feature *f* should get a curved arc label: it must be labelled,
    visible, curved, and (for the area layer) an actual FOV wedge."""
    if not (f["label"] or "").strip():
        return False
    hidden, curved = _feat_flags(f)
    if hidden or not curved:
        return False
    if mode == "wedge" and WEDGE_TOKEN not in str(f["ftype"] or ""):
        return False
    return True


def build_arc_features(layer, mode, arc_layer, units_per_mm, icon_mm, to_arc_crs):
    """Return a list of arc QgsFeatures for *layer*, built in the arc layer's CRS.
    mode 'wedge' → arc opposite each cone; mode 'point' → arc BELOW each marker.
    *to_arc_crs* transforms a source-layer point to the arc CRS."""
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

    font_pt = max(8.0, float(icon_mm) * 1.25)
    # Sit the arc a touch clear of the icon: icon half-width + text height + a
    # small margin. The label stays curved at every zoom (rebuilt on scale
    # change), so it hugs the symbol without the big gap a straight fallback needs.
    radius_mm = max(4.0, float(icon_mm) * 0.5 + font_pt / 2.835 + 2.0)
    radius = radius_mm * units_per_mm
    has_col = layer.fields().indexOf("fov_color") >= 0

    feats = []
    for f in layer.getFeatures():
        if not _wants_arc(f, mode):
            continue
        label = (f["label"] or "").strip()
        g = f.geometry()
        if g is None or g.isEmpty():
            continue

        if mode == "wedge":
            ring = _ring_points(g)
            if len(ring) < 3:
                continue
            try:
                ring = [to_arc_crs(p) for p in ring]
            except Exception:
                pass
            apex, cone_dir = _apex_and_cone_dir(ring)
            opp = cone_dir + math.pi        # away from the cone
        else:  # point — curve BELOW the marker (south; map Y is up)
            try:
                apex = to_arc_crs(g.asPoint())
            except Exception:
                continue
            opp = -math.pi / 2.0

        # Arc must be LONGER than the text: QGIS's curved placer needs headroom
        # beyond the raw text width (curved glyphs take more room), so an arc
        # sized to exactly the text falls back to horizontal. The 1.6x slack makes
        # curved placement succeed for short refs too.
        text_mm = _est_text_mm(label, font_pt)
        sweep = (text_mm * _ARC_TEXT_SLACK) / max(0.01, radius_mm)
        sweep = max(_ARC_MIN_SWEEP, min(_ARC_MAX_SWEEP, sweep))

        pts = []
        for k in range(_ARC_SEGMENTS + 1):
            a = opp - sweep / 2.0 + sweep * (k / _ARC_SEGMENTS)
            pts.append(QgsPointXY(apex.x() + radius * math.cos(a),
                                  apex.y() + radius * math.sin(a)))
        af = QgsFeature(arc_layer.fields())
        af.setGeometry(QgsGeometry.fromPolylineXY(pts))
        af.setAttribute("label", label)
        if has_col:
            af.setAttribute("fov_color", f["fov_color"] or "")
        feats.append(af)
    return feats


def _style_arc_layer(arc, icon_mm):
    from qgis.core import (
        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat,
        QgsTextBufferSettings, QgsProperty,
    )
    from qgis.PyQt.QtGui import QColor, QFont

    pal = QgsPalLayerSettings()
    pal.fieldName = "label"
    pal.enabled = True
    try:
        pal.placement = QgsPalLayerSettings.Placement.Curved
    except AttributeError:
        pal.placement = QgsPalLayerSettings.Placement.Curved
    # Follow even tight arcs without dropping characters; show every label.
    for attr, val in (("maxCurvedCharAngleIn", 99.0),
                      ("maxCurvedCharAngleOut", 99.0)):
        try:
            setattr(pal, attr, val)
        except Exception:
            pass
    try:
        pal.displayAll = True
    except Exception:
        pass

    # Stop QGIS falling back to a STRAIGHT label where the curved placement would
    # collide with nearby cones/buildings: make these labels ignore obstacles,
    # never act as obstacles themselves, take top priority, and sit ON the line
    # (curved) rather than being nudged above/below it.
    try:
        pal.obstacle = False          # this layer's features aren't obstacles
    except Exception:
        pass
    try:
        pal.priority = 10             # win every placement contest
    except Exception:
        pass
    try:
        from qgis.core import QgsLabelObstacleSettings
        obs = pal.obstacleSettings()
        obs.setIsObstacle(False)
        pal.setObstacleSettings(obs)
    except Exception:
        pass
    try:
        from qgis.core import QgsLabeling
        ls = pal.lineSettings()
        ls.setPlacementFlags(QgsLabeling.LinePlacementFlags(QgsLabeling.LinePlacementFlag.OnLine))
        # Let the label run right to the arc ends so curved placement always fits.
        try:
            ls.setOverrunDistance(2.0)
        except Exception:
            pass
        pal.setLineSettings(ls)
    except Exception:
        pass

    fmt = QgsTextFormat()
    font_pt = max(8.0, float(icon_mm) * 1.25)
    f = QFont("Inter")
    f.setBold(True)
    fmt.setFont(f)
    fmt.setSize(font_pt)
    fmt.setColor(QColor(0, 0, 0))
    bufs = QgsTextBufferSettings()
    bufs.setEnabled(True)
    bufs.setColor(QColor(255, 255, 255))
    bufs.setSize(max(1.0, float(icon_mm) * 0.22))
    fmt.setBuffer(bufs)
    pal.setFormat(fmt)

    # Tint each ref with its cone colour, like the on-cone labels did.
    if arc.fields().indexOf("fov_color") >= 0:
        try:
            ddp = pal.dataDefinedProperties()
            ddp.setProperty(
                QgsPalLayerSettings.Property.Color,
                QgsProperty.fromExpression(
                    "if(coalesce(\"fov_color\",'')='',"
                    "'#000000',darker(\"fov_color\",125))"))
            pal.setDataDefinedProperties(ddp)
        except Exception:
            pass

    arc.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    arc.setLabelsEnabled(True)


def _get_or_create_arc_layer(project, icon_mm):
    from qgis.core import (
        QgsVectorLayer, QgsLineSymbol, QgsSingleSymbolRenderer,
    )
    for lyr in project.mapLayers().values():
        if (isinstance(lyr, QgsVectorLayer)
                and lyr.name() == ARC_LAYER_NAME):
            return lyr

    crs = project.crs().authid() or "EPSG:3857"
    lyr = QgsVectorLayer(
        f"LineString?crs={crs}"
        "&field=label:string(120)&field=fov_color:string(9)",
        ARC_LAYER_NAME, "memory")
    # The arc line itself is invisible — only its curved label is wanted.
    sym = QgsLineSymbol.createSimple({"line_color": "0,0,0,0", "line_width": "0"})
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))
    _style_arc_layer(lyr, icon_mm)
    lyr.setCustomProperty("biome_map_only", 1)       # never counts in BNG
    lyr.setCustomProperty("skipMemoryLayersCheck", 1)  # no save-warning
    project.addMapLayer(lyr, False)
    _place_arc_in_eco_group(project, lyr)              # render on top in group
    return lyr


def clear():
    """Remove all arc features (e.g. when 'Show labels' is switched off). Leaves
    the layer in place so it's reused next time."""
    try:
        from qgis.core import QgsProject, QgsVectorLayer
        for lyr in QgsProject.instance().mapLayers().values():
            if (isinstance(lyr, QgsVectorLayer)
                    and lyr.name() == ARC_LAYER_NAME):
                ids = [f.id() for f in lyr.getFeatures()]
                if ids:
                    lyr.startEditing()
                    lyr.deleteFeatures(ids)
                    lyr.commitChanges()
                lyr.triggerRepaint()
    except Exception:
        pass


def refresh(canvas=None, icon_mm=6.0, scale_override=None, units_per_mm=None):
    """Rebuild the FOV-label arc layer from the current wedge features. Safe to
    call repeatedly; a no-op (clears the arcs) when there are no wedges. Returns
    the arc layer or None on failure.

    Sizing: pass *units_per_mm* directly (e.g. computed from an export layout's
    map item — exact), or a *canvas* to read it from the current view, or a
    *scale_override* denominator."""
    try:
        from qgis.core import QgsProject, QgsVectorLayer, QgsCoordinateTransform
    except Exception:
        return None
    try:
        project = QgsProject.instance()
        area_lyr = point_lyr = None
        for lyr in project.mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            if lyr.name() == "Ecological Area Features":
                area_lyr = lyr
            elif lyr.name() == "Ecological Features":
                point_lyr = lyr

        # Which layers to arc: wedges (FOV cones) + all point features. Only build
        # for features that actually want a curved label (visible + curved).
        sources = []
        if area_lyr is not None:
            sources.append((area_lyr, "wedge"))
        if point_lyr is not None:
            sources.append((point_lyr, "point"))

        # The master labels toggle beats every per-feature flag: with labels
        # hidden the arcs are cleared, not rebuilt (zoom/export included).
        has_any = (not labels_hidden()) and any(
            _wants_arc(f, mode)
            for lyr, mode in sources for f in lyr.getFeatures())

        # Find an existing arc layer WITHOUT creating one (so projects with no
        # curved labels — every other map — never get a stray FOV-labels layer
        # just because the canvas was zoomed).
        existing = None
        for lyr in project.mapLayers().values():
            if (isinstance(lyr, QgsVectorLayer)
                    and lyr.name() == ARC_LAYER_NAME):
                existing = lyr
                break

        # Heal projects created by older builds, where the generated label
        # layer was a separate top-level trunk in the custom Layers panel.
        if existing is not None:
            _place_arc_in_eco_group(project, existing)

        if not has_any:
            if existing is not None:
                ids = [ft.id() for ft in existing.getFeatures()]
                if ids:
                    existing.startEditing()
                    existing.deleteFeatures(ids)
                    existing.commitChanges()
                existing.triggerRepaint()
            return existing

        arc = existing or _get_or_create_arc_layer(project, icon_mm)
        _style_arc_layer(arc, icon_mm)   # pick up icon-size / colour changes

        if units_per_mm is None:
            units_per_mm = _units_per_mm(canvas, scale_override)

        feats = []
        for lyr, mode in sources:
            tr = QgsCoordinateTransform(lyr.crs(), arc.crs(), project)
            feats += build_arc_features(
                lyr, mode, arc, units_per_mm, icon_mm,
                lambda p, t=tr: t.transform(p))

        arc.startEditing()
        old = [ft.id() for ft in arc.getFeatures()]
        if old:
            arc.deleteFeatures(old)
        if feats:
            arc.addFeatures(feats)
        arc.commitChanges()
        arc.triggerRepaint()
        return arc
    except Exception:
        return None
