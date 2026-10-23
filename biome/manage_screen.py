# manage_screen.py — ManageScreenMixin, extracted verbatim from main_panel.py (2026-07-22 split).
# Methods run on BNGMainPanel via mixin inheritance; `self` is the panel.


from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QLineEdit, QScrollArea, QFormLayout, QMessageBox, QComboBox)
from qgis.PyQt.QtCore import (Qt, QSettings, QSize, QRectF)
from qgis.PyQt.QtGui import (QPainter, QPainterPath, QColor, QPen)
from qgis.core import Qgis
from qgis.gui import QgsHighlight
from . import theme
from .config import measurement_ellipsoid


from .panel_widgets import (_style_hover_highlight, _svg_pixmap, _svg_icon, _banner)
from .panel_map_tools import _OnboardingWizard

# Original (pre-colour-override) renderers, keyed by layer id. Was a
# BNGMainPanel class attribute before the split; only this module uses it.
_ORIG_RENDERERS = {}


class _SymbolTextureCard(QWidget):
    """Feature card painted with a quiet tile of its live map symbol."""

    def __init__(self, texture=None, fill="#FFFFFF", border="transparent",
                 hover_fill="#F1F4F0", hover_border="transparent",
                 divider="#E5E9E7", parent=None):
        super().__init__(parent)
        self._texture = texture
        self._fill = QColor(fill)
        self._border = QColor(border)
        self._hover_fill = QColor(hover_fill)
        self._hover_border = QColor(hover_border)
        self._divider = QColor(divider)
        self._hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def setHovered(self, hovered):
        self._hovered = bool(hovered)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)

        painter.fillPath(path, self._hover_fill if self._hovered else self._fill)
        if self._texture is not None and not self._texture.isNull():
            painter.save()
            painter.setClipPath(path)
            painter.setOpacity(0.13 if not self._hovered else 0.18)
            painter.drawTiledPixmap(self.rect(), self._texture)
            painter.restore()

        painter.setPen(QPen(self._hover_border if self._hovered
                            else self._border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        # A single quiet rule separates adjacent rows without boxing every
        # feature into another competing card outline.
        painter.setPen(QPen(self._divider, 1.0))
        painter.drawLine(8, self.height() - 1, self.width() - 8,
                         self.height() - 1)
        painter.end()


class _LineSymbolStrip(QWidget):
    """Compact, undistorted repeat of a linear feature's map symbol."""

    def __init__(self, texture, parent=None):
        super().__init__(parent)
        self._texture = texture
        self.setFixedSize(72, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        if self._texture is None or self._texture.isNull():
            return
        painter = QPainter(self)
        # Symbol previews include generous transparent padding. Enlarge the
        # preview and crop its centre so the displayed line matches the visual
        # weight seen on the map, while retaining dash/overlay proportions.
        tile = self._texture.scaledToHeight(
            self.height() * 2, Qt.TransformationMode.SmoothTransformation)
        if tile.isNull() or tile.width() <= 0:
            painter.end()
            return
        y = (self.height() - tile.height()) // 2
        x = 0
        while x < self.width():
            painter.drawPixmap(x, y, tile)
            x += tile.width()
        painter.end()


class _AreaSymbolPill(QWidget):
    """Map-symbol swatch clipped to the same pill shape as status badges."""

    def __init__(self, texture, outline="#D5DFDB", parent=None):
        super().__init__(parent)
        self._texture = texture
        self._outline = QColor(outline)
        self.setFixedSize(50, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setClipPath(path)
        if self._texture is not None and not self._texture.isNull():
            # Cover rather than stretch: preserve the renderer preview's
            # proportions and crop only its excess vertical padding.
            pixmap = self._texture.scaled(
                self.width(), self.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            x = (self.width() - pixmap.width()) // 2
            y = (self.height() - pixmap.height()) // 2
            painter.drawPixmap(x, y, pixmap)
        painter.setClipping(False)
        # Boundary pills intentionally contain no fill and use their actual
        # red/blue map colour as a stronger outline.
        _outline_width = 2.0 if self._texture is None or self._texture.isNull() else 1.0
        painter.setPen(QPen(self._outline, _outline_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()


class ManageScreenMixin:
    def _abort_move_tool(self):
        """Disarm a pending "Move feature" click-to-move tool. Called when the
        user navigates away (Back, tab switch, dock hide) so the next canvas
        click can't silently relocate the feature they forgot was armed.
        No-op when no move is pending."""
        tool = getattr(self, "_active_move_tool", None)
        if tool is None:
            return
        self._active_move_tool = None
        try:
            lyr = getattr(tool, "layer_ref", None)
            if lyr is not None:
                lyr.removeSelection()
        except Exception:
            pass
        try:
            canvas = self.iface.mapCanvas()
            if canvas.mapTool() is tool:
                self.iface.messageBar().clearWidgets()
                self.iface.actionPan().trigger()
        except Exception:
            pass

    def _open_manage_screen(self, restore_scroll=None):
        """Show the screen listing every placed feature.

        Two cases, picked automatically:

        * **First open** (navigating in from home or another screen): the screen
          is added to the stack and navigated to normally — Back always returns
          to home — with the usual fade-in.
        * **Rebuild in place** (the Manage screen is already showing and just
          needs refreshing after an add/edit/delete): the freshly-built screen
          is swapped in at the *same* stack slot without re-navigating.  This is
          what stops the flash — re-navigating fires ``currentChanged`` and so
          the install_fade animation, making the list fade in from blank every
          time.  Here the swap happens with that signal blocked and painting
          frozen, restoring the scroll position before the first repaint, so the
          old list simply gives way to the new one with no blink or jump.

        ``restore_scroll`` (a vertical scrollbar value) forces a scroll target;
        on an in-place rebuild it is captured automatically when omitted.
        """
        from qgis.PyQt.QtCore import QTimer

        prev = getattr(self, "_manage_screen_widget", None)
        in_place = prev is not None and self.stack.currentWidget() is prev

        # On an in-place rebuild, keep the list where it was scrolled to.
        if in_place and restore_scroll is None and hasattr(prev, "verticalScrollBar"):
            restore_scroll = prev.verticalScrollBar().value()

        screen = self._build_manage_screen()
        self._manage_screen_widget = screen

        if in_place:
            # Swap new-for-old at the same slot, no navigation → no fade.
            idx = self.stack.indexOf(prev)
            was_blocked = self.stack.signalsBlocked()
            self.stack.blockSignals(True)
            self.stack.setUpdatesEnabled(False)
            self.stack.insertWidget(idx, screen)
            self.stack.setCurrentWidget(screen)
            self.stack.removeWidget(prev)
            self.stack.blockSignals(was_blocked)
            prev.deleteLater()

            sb = screen.verticalScrollBar() if hasattr(screen, "verticalScrollBar") else None

            def _finish():
                # A newer rebuild may have superseded this one (rapid deletes);
                # if so, leave the stack frozen for that rebuild's _finish to
                # resolve — and don't touch this now-deleted screen's scrollbar.
                if screen is not getattr(self, "_manage_screen_widget", None):
                    return
                # Restore scroll, then unfreeze, so the first paint already shows
                # the list at the right place (no jump back to the top).
                if sb is not None and restore_scroll:
                    sb.setValue(restore_scroll)
                self.stack.setUpdatesEnabled(True)

            QTimer.singleShot(0, _finish)
            return

        # First open — full navigate (Back returns to home), with fade-in.
        while self.stack.count() > 3:
            w = self.stack.widget(3)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.addWidget(screen)
        # Reset stack so Back always goes to home
        self.stack.setCurrentIndex(0)
        self._screen_history.clear()
        self._navigate_to(self.stack.count() - 1, "Manage Features")
        if restore_scroll:
            sb = screen.verticalScrollBar() if hasattr(screen, "verticalScrollBar") else None
            if sb is not None:
                # Defer until the layout has been built and the scrollbar
                # range is valid; the value clamps itself if the list is now
                # shorter (e.g. a row was deleted).
                QTimer.singleShot(0, lambda: sb.setValue(restore_scroll))

    def _schedule_manage_rebuild(self, delay=400):
        """Coalesce a burst of deletes/edits into a single Manage-screen rebuild
        (which refreshes the deleted-items bin), rather than rebuilding on every
        operation.  Only rebuilds if the Manage screen is still showing."""
        from qgis.PyQt.QtCore import QTimer
        t = getattr(self, "_manage_rebuild_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._do_manage_rebuild)
            self._manage_rebuild_timer = t
        t.start(delay)

    def _do_manage_rebuild(self):
        """Coalesced rebuild after a burst of edits/deletes — only when the
        Manage screen is still showing.  Delegates to _open_manage_screen,
        which detects the in-place case and swaps the list in seamlessly (no
        fade, scroll preserved)."""
        current = getattr(self, "_manage_screen_widget", None)
        if current is not None and self.stack.currentWidget() is current:
            self._open_manage_screen()

    def _flush_manage_changes(self):
        """Run the deferred summary recompute once, on leaving the Manage screen.
        The balance/BNG/phase widgets are not visible while managing, so there is
        no need to recompute them after every individual delete or edit."""
        if not getattr(self, "_manage_dirty", False):
            return
        self._manage_dirty = False
        self._mark_summary_dirty()
        self._refresh_phase_screens()

    def _maybe_show_onboarding(self):
        """Show onboarding wizard if this is the first time the plugin has been opened."""
        s = QSettings()
        if not s.value("biome/onboarding_complete", False, type=bool):
            dlg = _OnboardingWizard(self.iface.mainWindow())
            dlg.exec()
            s.setValue("biome/onboarding_complete", True)

    def _wire_hint(self, widget, text):
        """Wire a widget's enter/leave events to show hint text in the hint bar."""
        if not text:
            return
        def _enter(e):
            # Elide so a long hint can never widen the panel
            fm = self._hint_bar.fontMetrics()
            avail = max(40, self._hint_bar.width() - 24)
            self._hint_bar.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, avail))
        def _leave(e):
            self._hint_bar.setText("")
        widget.enterEvent = _enter
        widget.leaveEvent = _leave

    # ── Colour override helpers ────────────────────────────────────────────
    @staticmethod
    def _colour_override_key(feat_uuid):
        return f"biome_colour_override_{feat_uuid}"

    @staticmethod
    def _get_colour_override(layer, fid):
        """Return QColor if a colour override is stored for this fid, else None."""
        from qgis.PyQt.QtGui import QColor
        key = f"biome_colour_{fid}"
        val = layer.customProperty(key)
        if val:
            c = QColor(val)
            return c if c.isValid() else None
        return None

    @staticmethod
    def _apply_colour_override(layer, fid, colour):
        """Apply or remove a colour override for a single feature using auxiliary storage."""
        from qgis.PyQt.QtGui import QColor
        from qgis.core import (
            QgsRuleBasedRenderer, QgsFillSymbol, QgsLineSymbol,
            QgsMarkerSymbol, QgsWkbTypes,
        )

        key = f"biome_colour_{fid}"

        from qgis.core import Qgis, QgsMessageLog
        QgsMessageLog.logMessage(
            f"[ColourOverride] layer={layer.name()} fid={fid} "
            f"colour={colour.name() if colour and colour.isValid() else 'None'} "
            f"key={key}",
            "Biome", Qgis.MessageLevel.Info
        )

        if colour and colour.isValid():
            layer.setCustomProperty(key, colour.name())
        else:
            layer.removeCustomProperty(key)

        # Collect all overrides for this layer
        overrides = {}  # fid (int) -> hex str
        for prop in layer.customProperties().keys():
            if prop.startswith("biome_colour_"):
                try:
                    stored_fid = int(prop[len("biome_colour_"):])
                    overrides[stored_fid] = layer.customProperty(prop)
                except ValueError:
                    pass
        # Detect geometry type reliably
        from qgis.core import QgsWkbTypes as _WKB
        wkb = layer.wkbType()
        geom_kind = _WKB.geometryType(wkb)  # returns GeometryType enum
        is_polygon = (geom_kind == _WKB.GeometryType.PolygonGeometry)
        is_line    = (geom_kind == _WKB.GeometryType.LineGeometry)
        if not overrides:
            # No overrides left — restore the habitat placer's EXACT styling by
            # rebuilding the renderer straight from the bundled QML on a throwaway
            # layer (safe — see LayerManager.build_builtin_renderer) and copying
            # it across. This matches the placer for every type and never calls
            # loadNamedStyle on the real (in-project) layer. Eco layers are
            # rebuilt via gridref's own styler.
            _ORIG_RENDERERS.pop(layer.id(), None)
            layer.removeCustomProperty("biome_orig_renderer")
            lname = layer.name().lower()
            eco_names = {"ecological features", "ecological linear features",
                         "ecological area features", "ecological buffer zones"}
            if lname in eco_names:
                try:
                    from .gridref_dialog import GridRefDialog as _GRD
                    _grd = _GRD.__new__(_GRD)
                    _grd._icon_size_mm = 6
                    _grd.emoji_mode_chk = type("_C", (), {"isChecked": lambda s: False})()
                    _grd.show_labels_chk = type("_C", (), {"isChecked": lambda s: True})()
                    _grd._apply_style(layer)
                except Exception:
                    pass
            else:
                try:
                    from .layer_manager import LAYER_NAMES, LayerManager
                    bkey = next((k for k, n in LAYER_NAMES.items()
                                 if n == layer.name()), None)
                    if bkey is not None:
                        geom_str = ("Polygon" if is_polygon
                                    else "LineString" if is_line else "Point")
                        renderer, opacity = LayerManager.build_builtin_style(
                            bkey, geom_str)
                        if renderer is not None:
                            layer.setRenderer(renderer)
                            # Habitat QMLs carry LAYER-level opacity (0.6) —
                            # without this the rebuilt fills render solid.
                            layer.setOpacity(opacity)
                except Exception:
                    pass
            layer.triggerRepaint()
            return

        # Build rule-based renderer with one rule per override + ELSE default
        # ── Snapshot the original (pre-override) renderer ─────────────────────
        # Store a clone of the original renderer in a module-level dict keyed by
        # layer id. This survives multiple colour changes on the same layer and
        # works for all renderer types including SVG/categorised eco renderers
        # where XML round-trip is unreliable.
        lid = layer.id()
        current = layer.renderer()
        is_already_rule = isinstance(current, QgsRuleBasedRenderer)

        if not is_already_rule:
            # First override on this layer — save a clone of the original
            _ORIG_RENDERERS[lid] = current.clone()
            orig = _ORIG_RENDERERS[lid]
        else:
            # Already overridden — use the saved original if available
            orig = _ORIG_RENDERERS.get(lid)
            if orig is None:
                orig = current  # fallback (shouldn't normally happen)

        def _symbol_for_feature(feature_id):
            """Get the symbol the original renderer would use for this feature."""
            try:
                from qgis.core import QgsRenderContext, QgsExpressionContext, QgsExpressionContextUtils
                ctx = QgsRenderContext()
                exp_ctx = QgsExpressionContext()
                exp_ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
                feat = layer.getFeature(feature_id)
                if feat.isValid():
                    exp_ctx.setFeature(feat)
                ctx.setExpressionContext(exp_ctx)
                orig.startRender(ctx, layer.fields())
                sym = orig.symbolForFeature(feat, ctx)
                orig.stopRender(ctx)
                if sym:
                    return sym.clone()
            except Exception:
                pass
            return None

        def _recolour_symbol(sym, colour):
            """Clone symbol and change all symbol layer colours to the override colour."""
            s = sym.clone()
            for i in range(s.symbolLayerCount()):
                sl = s.symbolLayer(i)
                sl.setColor(colour)
                # Preserve stroke/outline but darken it slightly
                try:
                    sl.setStrokeColor(colour.darker(140))
                except Exception:
                    pass
                try:
                    sl.setOutlineColor(colour.darker(140))
                except Exception:
                    pass
            return s

        # Get default symbol for ELSE rule
        try:
            if hasattr(orig, 'symbol') and callable(orig.symbol):
                default_sym = orig.symbol().clone()
            else:
                from qgis.core import QgsRenderContext
                ctx = QgsRenderContext()
                syms = orig.symbols(ctx) if hasattr(orig, 'symbols') else []
                default_sym = syms[0].clone() if syms else None
        except Exception:
            default_sym = None

        if default_sym is None:
            if is_polygon:
                default_sym = QgsFillSymbol.createSimple(
                    {"color": "144,238,144,160", "outline_color": "#1F7A4D", "outline_width": "0.3"})
            elif is_line:
                default_sym = QgsLineSymbol.createSimple(
                    {"color": "#1F7A4D", "width": "0.5"})
            else:
                default_sym = QgsMarkerSymbol.createSimple({"color": "#1F7A4D"})

        root = QgsRuleBasedRenderer.Rule(None)

        for ofid, hex_col in overrides.items():
            c = QColor(hex_col)
            if not c.isValid():
                continue
            # Get ftype for this feature to use as legend label
            feat = layer.getFeature(ofid)
            ftype_label = ""
            if feat.isValid():
                try:
                    ftype_label = feat["ftype"] or feat["label"] or feat["sub_type"] or ""
                except Exception:
                    pass
            # Try to get the actual symbol for this feature to preserve styling
            orig_sym = _symbol_for_feature(ofid)
            if orig_sym:
                sym = _recolour_symbol(orig_sym, c)
            else:
                # Fallback to simple symbol if we can't get the original
                if is_polygon:
                    sym = QgsFillSymbol.createSimple({
                        "color": f"{c.red()},{c.green()},{c.blue()},200",
                        "outline_color": c.darker(130).name(),
                        "outline_width": "0.4",
                    })
                elif is_line:
                    sym = QgsLineSymbol.createSimple({
                        "color": f"{c.red()},{c.green()},{c.blue()},255",
                        "width": "0.6",
                    })
                else:
                    sym = QgsMarkerSymbol.createSimple({
                        "color": f"{c.red()},{c.green()},{c.blue()},255",
                    })
            rule = QgsRuleBasedRenderer.Rule(sym)
            rule.setFilterExpression(f"$id = {ofid}")
            rule.setLabel(ftype_label)  # shows in legend
            root.appendChild(rule)

        # ── ELSE rule: embed the *entire* original renderer converted to rule-based
        # so non-overridden features keep all their original per-category colours.
        # This prevents every other feature from falling back to a single default symbol.
        try:
            else_renderer = QgsRuleBasedRenderer.convertFromRenderer(orig)
            if else_renderer:
                else_group = QgsRuleBasedRenderer.Rule(None)
                else_group.setIsElse(True)
                else_group.setLabel("(original style)")
                for child in else_renderer.rootRule().children():
                    else_group.appendChild(child.clone())
                root.appendChild(else_group)
            else:
                raise RuntimeError("convertFromRenderer returned None")
        except Exception:
            # Absolute fallback — plain symbol
            else_rule = QgsRuleBasedRenderer.Rule(default_sym, 0, 0, "", layer.name(), "", True)
            root.appendChild(else_rule)

        layer.setRenderer(QgsRuleBasedRenderer(root))
        layer.triggerRepaint()

    def _merge_matching_neighbours(self):
        """Dissolve touching AREA parcels that share every metric-relevant field.

        Digitising a site parcel-by-parcel leaves neighbours that are the same
        habitat in the same condition; the metric aggregates them anyway, so
        splitting them adds rows to the export and clutter to the map without
        changing a number.

        Rules, in order of how much trouble each one saves:
          * same LAYER, and area layers only — trees and linear features are
            never merged
          * every scoring field must match: habitat, condition, strategic
            significance, irreplaceable status and off-site category. Merging
            across any of those would silently rewrite one parcel's score
          * a shared EDGE, not a corner. Dissolving parcels that meet at a point
            yields a multipart polygon, and these layers are wkbType Polygon —
            OGR stores the second part as an interior ring, which measures
            NEGATIVE area
          * coverage is measured before and after; anything that moves it is
            rolled back rather than committed
        """
        from qgis.core import (QgsDistanceArea, QgsGeometry,
                               QgsGeometryParameters, QgsProject, QgsUnitTypes)
        from qgis.PyQt.QtWidgets import QMessageBox
        from .shared_edges import (
            EDGE_TOLERANCE, has_shared_edge,
            normalise_geometry as _merge_geom,
            valid_geometry as _valid,
        )

        def _s(feat, name):
            try:
                v = feat[name]
            except (KeyError, IndexError):
                return ""
            if v is None:
                return ""
            try:
                if v.isNull():
                    return ""
            except AttributeError:
                pass
            t = str(v).strip()
            return "" if t.upper() == "NULL" else t

        # Boundaries produced by separate drawing/clip operations can be
        # visually coincident yet differ by a microscopic amount.  A plain
        # vertex grid snap does not fix a vertex which lies on the middle of a
        # neighbouring segment (it only rounds its X/Y values), so GEOS can
        # report a long shared edge as a single point.  Use the same 1 mm
        # tolerance both to recognise those edges and for the precision-aware
        # union below.  The 1 cm minimum still rejects genuine corner touches.
        union_parameters = QgsGeometryParameters()
        union_parameters.setGridSize(EDGE_TOLERANCE)

        KEY_FIELDS = ("sub_habitat_type", "condition", "strategic_significance",
                      "irreplaceable_habitat", "spatial_category")
        plans, summary, total_gone = {}, [], 0
        for layer_key in ("baseline_area", "proposed_area"):
            layer = self.layer_manager.get_layer(layer_key)
            if layer is None or not layer.isValid() or layer.featureCount() < 2:
                continue
            da = QgsDistanceArea()
            da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
            da.setEllipsoid(measurement_ellipsoid())

            def m2(g):
                if g is None or g.isEmpty() or not g.isGeosValid():
                    return 0.0
                return da.convertAreaMeasurement(da.measureArea(g),
                                                 QgsUnitTypes.AreaUnit.SquareMeters)

            feats, attrs, groups = {}, {}, {}
            names = [fl.name() for fl in layer.fields() if fl.name() != "fid"]
            for f in layer.getFeatures():
                g = _valid(f.geometry())
                if g is None or g.isEmpty():
                    continue
                key = tuple(_s(f, n) for n in KEY_FIELDS)
                feats[f.id()] = g
                # Captured now, while the feature still exists — the undo entry
                # re-adds the dropped parcels from these. fid is excluded: the
                # provider assigns a fresh one on re-add.
                attrs[f.id()] = {n: f[n] for n in names}
                groups.setdefault(key, []).append(f.id())

            merge_geoms = {fid: _merge_geom(g) for fid, g in feats.items()}

            layer_plan = []
            for key, ids in groups.items():
                if len(ids) < 2:
                    continue
                adj = {i: set() for i in ids}
                for a in range(len(ids)):
                    for b in range(a + 1, len(ids)):
                        ga, gb = merge_geoms[ids[a]], merge_geoms[ids[b]]
                        if not has_shared_edge(ga, gb):
                            continue                      # corner touch only
                        adj[ids[a]].add(ids[b])
                        adj[ids[b]].add(ids[a])
                seen = set()
                for start in ids:
                    if start in seen or not adj[start]:
                        continue
                    comp, stack = set(), [start]
                    while stack:
                        cur = stack.pop()
                        if cur in comp:
                            continue
                        comp.add(cur)
                        seen.add(cur)
                        stack.extend(adj[cur] - comp)
                    if len(comp) < 2:
                        continue
                    comp = sorted(comp)
                    u = _valid(QgsGeometry.unaryUnion(
                        [merge_geoms[i] for i in comp], union_parameters))
                    parts = [p for p in (u.asGeometryCollection() if u.isMultipart()
                                         else [u]) if p.type() == 2]
                    want = sum(m2(feats[i]) for i in comp)
                    if len(parts) != 1 or abs(m2(u) - want) > 1.0:
                        continue                          # would split or lose area
                    layer_plan.append((comp[0], parts[0], comp[1:]))
                    summary.append(f"    {key[0] or '(no habitat)'} · "
                                   f"{key[1] or '(no condition)'} — "
                                   f"{len(comp)} parcels ({want / 10000:.4f} ha)")
                    total_gone += len(comp) - 1
            if layer_plan:
                plans[layer_key] = (layer, layer_plan, feats, attrs,
                                    m2(_valid(QgsGeometry.unaryUnion(
                                        list(feats.values())))))

        if not plans:
            QMessageBox.information(
                self.iface.mainWindow(), "Merge Matching Neighbours",
                "Nothing to merge — no touching parcels share the same habitat, "
                "condition, strategic significance and irreplaceable status.")
            return

        shown = summary[:12]
        more = ("\n    …and %d more" % (len(summary) - len(shown))
                if len(summary) > len(shown) else "")
        if QMessageBox.question(
                self.iface.mainWindow(), "Merge Matching Neighbours",
                f"Merge {total_gone + len(summary)} parcels into {len(summary)}, "
                f"removing {total_gone}?\n\n" + "\n".join(shown) + more +
                "\n\nCoverage is unchanged — only the parcel boundaries go.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        done, failed = 0, []
        undo_mods, undo_readds = [], []
        for layer_key, (layer, layer_plan, feats, attrs, before) in plans.items():
            if layer.isEditable():
                # Proposed placement can leave its layer in an active edit
                # session even though the newly drawn features are complete.
                # The old code skipped that layer wholesale, which made the
                # command appear baseline-only. Commit those pending edits
                # before opening the merge transaction; if QGIS cannot commit,
                # leave the layer untouched and report the real error.
                if not layer.commitChanges():
                    failed.append(
                        f"{layer.name()}: could not finish pending edits — "
                        f"{' ; '.join(layer.commitErrors()[:2])}")
                    continue
                layer.reload()
            da = QgsDistanceArea()
            da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
            da.setEllipsoid(measurement_ellipsoid())
            layer.startEditing()
            for keep, geom, drop in layer_plan:
                layer.changeGeometry(keep, geom)
                layer.deleteFeatures(drop)
            if not layer.commitChanges():
                failed.append(f"{layer.name()}: {'; '.join(layer.commitErrors()[:2])}")
                layer.rollBack()
                continue
            layer.dataProvider().reloadData()
            layer.reload()
            after = da.convertAreaMeasurement(
                da.measureArea(_valid(QgsGeometry.unaryUnion(
                    [_valid(f.geometry()) for f in layer.getFeatures()]))),
                QgsUnitTypes.AreaUnit.SquareMeters)
            if abs(after - before) > 1.0:
                failed.append(f"{layer.name()}: coverage moved "
                              f"{after - before:+.1f} m² — please check")
            # Undo payload, built only for a merge that actually committed:
            # the kept parcel goes back to its pre-merge outline, each dropped
            # parcel is re-added from the attributes captured above.
            lid = layer.id()
            for keep, geom, drop in layer_plan:
                undo_mods.append([lid, keep, feats[keep].asWkt(), geom.asWkt()])
                for d in drop:
                    undo_readds.append({"lid": lid, "wkt": feats[d].asWkt(),
                                        "attrs": attrs[d]})
            done += sum(len(d) for _k, _g, d in layer_plan)
            layer.updateExtents()
            layer.triggerRepaint()

        # One undo entry for the whole merge. There is no "placed feature" to
        # remove, so the primary slots are None — undo's _delete() returns
        # early on a None fid and just replays the repairs. Pushed AFTER the
        # commit because push_undo stamps each mod with the feature's CURRENT
        # identity sig, so a later undo refuses to overwrite a parcel the user
        # has edited since.
        if (undo_mods or undo_readds) and hasattr(self, "push_undo"):
            try:
                self.push_undo(None, None, None, None,
                               repairs={"mods": undo_mods, "readds": undo_readds})
            except Exception:
                pass
        try:
            self.iface.mapCanvas().refresh()
        except Exception:
            pass
        for hook in ("_mark_summary_dirty", "_refresh_balance_widget",
                     "_refresh_layers_widget"):
            fn = getattr(self, hook, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        # Rebuild and SWAP the visible Manage screen. Calling
        # _build_manage_screen() alone only constructs an unattached widget,
        # which left the old per-feature rows and feature count on screen even
        # though the merged geometry had already committed to the layer.
        try:
            self._open_manage_screen()
        except Exception:
            pass
        msg = f"Merged away {done} parcel{'s' if done != 1 else ''}."
        if failed:
            QMessageBox.warning(self.iface.mainWindow(), "Merge Matching Neighbours",
                                msg + "\n\nProblems:\n• " + "\n• ".join(failed))
        else:
            QMessageBox.information(self.iface.mainWindow(),
                                    "Merge Matching Neighbours", msg)

    def _reset_all_colours(self):
        """Remove every colour override from every layer and restore original QML styles."""
        import os
        from qgis.core import QgsProject
        from qgis.PyQt.QtWidgets import QMessageBox

        ALL_LAYER_KEYS = [
            "baseline_area", "baseline_water", "baseline_hedgerow", "baseline_trees",
            "proposed_area", "proposed_water", "proposed_hedgerow", "proposed_trees",
        ]
        ECO_NAMES = {
            "Ecological Features", "Ecological Linear Features",
            "Ecological Area Features", "Ecological Buffer Zones",
        }
        plugin_dir = os.path.dirname(os.path.abspath(__file__))

        reset_count = 0
        for key in ALL_LAYER_KEYS:
            lyr = self.layer_manager.get_layer(key)
            if lyr is None:
                continue
            # Remove all biome_colour_* custom properties
            props_to_remove = [p for p in lyr.customProperties().keys()
                               if p.startswith("biome_colour_")]
            for p in props_to_remove:
                lyr.removeCustomProperty(p)
            reset_count += len(props_to_remove)
            # Restore the habitat placer's EXACT styling by rebuilding the
            # renderer straight from the bundled QML on a throwaway layer (safe —
            # see LayerManager.build_builtin_renderer) and copying it across. This
            # reproduces the placer look for every type (area/hedge/water/trees,
            # baseline & proposed) regardless of how the live layer drifted. We do
            # NOT call lyr.loadNamedStyle() on the real layer (that combination —
            # replacing a heavy override renderer — was the crash).
            _ORIG_RENDERERS.pop(lyr.id(), None)
            geom_str = self.layer_manager._geom_type(key)
            renderer, opacity = self.layer_manager.build_builtin_style(
                key, geom_str)
            if renderer is not None:
                lyr.setRenderer(renderer)
                # Habitat QMLs carry LAYER-level opacity (0.6) — without this
                # the rebuilt fills render solid.
                lyr.setOpacity(opacity)
            else:
                try:
                    self.layer_manager._apply_fallback_style(lyr, key)
                except Exception:
                    pass
            lyr.triggerRepaint()

        # Also reset eco layers
        for lname in ECO_NAMES:
            layers = QgsProject.instance().mapLayersByName(lname)
            for lyr in layers:
                props_to_remove = [p for p in lyr.customProperties().keys()
                                   if p.startswith("biome_colour_")]
                for p in props_to_remove:
                    lyr.removeCustomProperty(p)
                reset_count += len(props_to_remove)
                _ORIG_RENDERERS.pop(lyr.id(), None)
                try:
                    from .gridref_dialog import GridRefDialog as _GRD
                    _grd = _GRD.__new__(_GRD)
                    _grd._icon_size_mm = 6
                    _grd.emoji_mode_chk = type("_C", (), {"isChecked": lambda s: False})()
                    _grd.show_labels_chk = type("_C", (), {"isChecked": lambda s: True})()
                    _grd._apply_style(lyr)
                except Exception:
                    pass
                lyr.triggerRepaint()

        self.iface.mapCanvas().refresh()
        # Rebuild the manage screen so the reset button reflects the new state
        self._open_manage_screen()

    def _build_manage_screen(self):
        """Return a scrollable screen listing every placed feature with
        Delete and Edit buttons per row."""
        from qgis.core import QgsProject, QgsVectorLayer

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: #F7F8F6; }")

        container = QWidget()
        container.setStyleSheet(
            "background: #F7F8F6;")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(6)

        # Broken-geometry warning (only shown when a parcel is invalid).
        geo_banner = self._geometry_warning_widget()
        if geo_banner is not None:
            layout.addWidget(geo_banner)

        # ── Reset All Colours button ──────────────────────────────────────────
        from qgis.PyQt.QtCore import QSize as _QSizeReset
        reset_btn = QPushButton("Reset All Colours to Default")
        reset_btn.setFixedHeight(34)
        reset_btn.setIcon(_svg_icon("palette", "#9A6700", 16))
        reset_btn.setIconSize(_QSizeReset(16, 16))
        reset_btn.setStyleSheet(
            "QPushButton { background:#FBF4E4; color:#9A6700; border:1px solid #ffb74d;"
            " border-radius:6px; font-size:11px; font-weight:500; padding:0 12px; }"
            "QPushButton:hover { background:#ffe0b2; border-color:#9A6700; }"
            "QPushButton:pressed { background:#ffcc80; }"
        )
        reset_btn.clicked.connect(self._reset_all_colours)
        layout.addWidget(reset_btn)

        # ── Merge matching neighbours ─────────────────────────────────────────
        merge_btn = QPushButton("Merge Matching Neighbours")
        merge_btn.setFixedHeight(34)
        merge_btn.setIcon(_svg_icon("layers", theme.ACCENT_DARK, 16))
        merge_btn.setIconSize(_QSizeReset(16, 16))
        merge_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.ACCENT_SOFT}; color:{theme.ACCENT_DARK};"
            f" border:1px solid {theme.ACCENT}; border-radius:6px; font-size:11px;"
            f" font-weight:500; padding:0 12px; }}"
            f"QPushButton:hover {{ background:{theme.ACCENT}; color:{theme.SURFACE}; }}"
        )
        merge_btn.setToolTip(
            "Dissolve touching AREA parcels that share habitat type, condition, "
            "strategic significance and irreplaceable status.\n"
            "Only parcels sharing a real edge are merged — a corner touch is left "
            "alone, because dissolving it would make a multipart parcel.\n"
            "Coverage is checked before and after and the merge is abandoned if "
            "it moves. Trees and linear features are never touched.")
        merge_btn.clicked.connect(self._merge_matching_neighbours)
        layout.addWidget(merge_btn)

        # Collect all features from every managed layer
        ALL_KEYS = [
            ("baseline_area",     "Baseline – Area Habitats"),
            ("baseline_water",    "Baseline – Watercourse"),
            ("baseline_hedgerow", "Baseline – Hedgerow"),
            ("baseline_trees",    "Baseline – Trees"),
            ("proposed_area",     "Proposed – Area Habitats"),
            ("proposed_water",    "Proposed – Watercourse"),
            ("proposed_hedgerow", "Proposed – Hedgerow"),
            ("proposed_trees",    "Proposed – Trees"),
            ("redline",           "Red Line Boundary"),
            ("site_boundary",     "Blue Line Boundary"),
        ]
        ECO_NAMES = {"Ecological Features", "Ecological Linear Features", "Ecological Area Features", "Ecological Buffer Zones"}

        any_features = False

        # Rebuilding the Manage screen after a delete used to recreate every
        # fold button unchecked, closing the habitat the user was working
        # through. Keep fold state on the panel (session-only) so repeated
        # deletes remain in the same open group.
        if not hasattr(self, "_manage_expanded_folds"):
            self._manage_expanded_folds = set()

        # Baseline and proposed sections each share one phase-coloured panel.
        # Individual feature rows remain neutral so long lists are easier to
        # scan and the phase is communicated by the surrounding background.
        phase_layouts = {}
        symbol_texture_cache = {}

        def _feature_symbol_texture(layer, feat):
            """Return a small transparent preview of this feature's map symbol."""
            if layer.name() in ("Redline Boundary", "Blue Line Boundary"):
                return None
            cache_key = (layer.id(),
                         str(feat["sub_habitat_type"] or "")
                         if feat.fieldNameIndex("sub_habitat_type") >= 0 else "",
                         str(feat["condition"] or "")
                         if feat.fieldNameIndex("condition") >= 0 else "",
                         str(feat["retention"] or "")
                         if feat.fieldNameIndex("retention") >= 0 else "")
            if cache_key in symbol_texture_cache:
                return symbol_texture_cache[cache_key]
            pixmap = None
            renderer = layer.renderer()
            if renderer is not None:
                try:
                    from qgis.core import (QgsRenderContext, QgsExpressionContext,
                                           QgsExpressionContextUtils,
                                           QgsSymbolLayerUtils)
                    context = QgsRenderContext()
                    expression_context = QgsExpressionContext()
                    expression_context.appendScopes(
                        QgsExpressionContextUtils.globalProjectLayerScopes(layer))
                    expression_context.setFeature(feat)
                    context.setExpressionContext(expression_context)
                    renderer.startRender(context, layer.fields())
                    try:
                        symbol = renderer.symbolForFeature(feat, context)
                        if symbol is not None:
                            pixmap = QgsSymbolLayerUtils.symbolPreviewPixmap(
                                symbol, QSize(52, 34))
                    finally:
                        renderer.stopRender(context)
                except Exception:
                    pixmap = None
            symbol_texture_cache[cache_key] = pixmap
            return pixmap

        def _feature_geometry_kind(layer):
            """Return polygon, line, point or other for card-symbol treatment."""
            try:
                from qgis.core import QgsWkbTypes
                geom_type = QgsWkbTypes.geometryType(layer.wkbType())
                if geom_type == QgsWkbTypes.GeometryType.PolygonGeometry:
                    return "polygon"
                if geom_type == QgsWkbTypes.GeometryType.LineGeometry:
                    return "line"
                if geom_type == QgsWkbTypes.GeometryType.PointGeometry:
                    return "point"
            except Exception:
                pass
            return "other"

        def _delete_feature(layer, fid, row_widget):
            # If this is a linked tree/hedge twin, clear the survivor's pair_id
            # so it reclassifies correctly (baseline-only -> Lost, proposed-only
            # -> Created) instead of dangling a reference to a deleted partner.
            self._orphan_pair_twin(layer, fid)
            # Snapshot into bin BEFORE deletion so geometry/attrs are still readable
            self._bin_feature(layer, fid)
            # Always use the edit-buffer path — direct provider.deleteFeatures()
            # can fail with OGR error on GPKG layers that are not in edit mode.
            was_editing = layer.isEditable()
            if not was_editing:
                layer.startEditing()
            ok = layer.deleteFeature(fid)
            if ok:
                ok = bool(layer.commitChanges())
                if ok:
                    # Reload clears the OGR provider cache so it doesn't try to
                    # re-read the now-deleted fid and log a spurious OGR error.
                    layer.dataProvider().reloadData()
            if not ok:
                layer.rollBack()
            layer.updateExtents()
            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()
            if ok:
                # Summary stats (balance / BNG / phase screens) are not visible
                # while managing, so don't recompute them now — just mark dirty
                # and flush once when the user presses Back.
                self._manage_dirty = True
                # Instant feedback: remove just this row instead of rebuilding
                # the whole (disk-scanning) list.
                if row_widget is not None:
                    row_widget.setParent(None)
                    row_widget.deleteLater()
                # Coalesce rapid deletes into a single rebuild that refreshes
                # the deleted-items bin.
                self._schedule_manage_rebuild()
            else:
                # Deletion failed — remove the bin entry we just added
                if self._deleted_bin:
                    self._deleted_bin.pop()
                QMessageBox.warning(self.iface.mainWindow(), "Delete",
                                    "Failed to delete feature.")

        def _trim_area_habitats_to_redline(redline_layer):
            """Intersect existing area habitats with the current redline.

            Intersection is deliberately one-way: shrinking or moving the
            redline trims mapped parcels, while enlarging it leaves them
            untouched and never invents habitat for newly enclosed ground.
            """
            from qgis.core import (
                QgsCoordinateTransform, QgsFeature, QgsGeometry,
                QgsProject, QgsWkbTypes,
            )

            boundary = None
            for boundary_feat in redline_layer.getFeatures():
                geom = boundary_feat.geometry()
                if geom is None or geom.isNull() or geom.isEmpty():
                    continue
                copy = QgsGeometry(geom)
                boundary = (copy if boundary is None
                            else boundary.combine(copy))
            if boundary is None or boundary.isNull() or boundary.isEmpty():
                return 0
            if not boundary.isGeosValid():
                boundary = boundary.makeValid()

            changed_total = 0
            failures = []
            project = QgsProject.instance()
            for layer_key in ("baseline_area", "proposed_area"):
                area_layer = self.layer_manager.get_layer(layer_key)
                if area_layer is None or not area_layer.isValid():
                    continue

                layer_boundary = QgsGeometry(boundary)
                if area_layer.crs() != redline_layer.crs():
                    try:
                        transform = QgsCoordinateTransform(
                            redline_layer.crs(), area_layer.crs(), project)
                        layer_boundary.transform(transform)
                    except Exception as exc:
                        failures.append(
                            f"{area_layer.name()}: coordinate transform failed ({exc})")
                        continue

                changes = []
                for habitat in list(area_layer.getFeatures()):
                    original = habitat.geometry()
                    if (original is None or original.isNull()
                            or original.isEmpty()
                            or not original.intersects(layer_boundary)):
                        if (original is not None and not original.isNull()
                                and not original.isEmpty()
                                and not original.intersects(layer_boundary)):
                            changes.append((habitat, []))
                        continue
                    clipped = original.intersection(layer_boundary)
                    if clipped is None or clipped.isNull():
                        failures.append(
                            f"{area_layer.name()}: could not clip feature {habitat.id()}")
                        continue
                    parts = []
                    candidates = (clipped.asGeometryCollection()
                                  if clipped.isMultipart() else [clipped])
                    for part in candidates:
                        if (part is not None and not part.isNull()
                                and not part.isEmpty()
                                and QgsWkbTypes.geometryType(part.wkbType())
                                == QgsWkbTypes.GeometryType.PolygonGeometry
                                and part.area() > 0.001):
                            parts.append(QgsGeometry(part))
                    if not parts:
                        changes.append((habitat, []))
                    elif not clipped.equals(original):
                        changes.append((habitat, parts))

                if not changes:
                    continue
                if area_layer.isEditable() and not area_layer.commitChanges():
                    failures.append(
                        f"{area_layer.name()}: could not finish pending edits")
                    continue
                if not area_layer.startEditing():
                    failures.append(
                        f"{area_layer.name()}: could not start editing")
                    continue

                layer_changed = 0
                for habitat, parts in changes:
                    fid = habitat.id()
                    if not parts:
                        self._bin_feature(area_layer, fid)
                        if area_layer.deleteFeature(fid):
                            layer_changed += 1
                        continue
                    if area_layer.changeGeometry(fid, parts[0]):
                        layer_changed += 1
                    for part in parts[1:]:
                        split = QgsFeature(area_layer.fields())
                        attrs = list(habitat.attributes())
                        fid_idx = area_layer.fields().indexFromName("fid")
                        if fid_idx >= 0:
                            attrs[fid_idx] = None
                        split.setAttributes(attrs)
                        split.setGeometry(part)
                        area_layer.addFeature(split)

                if not area_layer.commitChanges():
                    errors = "; ".join(area_layer.commitErrors()[:2])
                    area_layer.rollBack()
                    failures.append(f"{area_layer.name()}: {errors}")
                    continue
                area_layer.dataProvider().reloadData()
                area_layer.updateExtents()
                area_layer.triggerRepaint()
                changed_total += layer_changed

            if changed_total:
                self._manage_dirty = True
                self._schedule_manage_rebuild()
                self.iface.messageBar().pushMessage(
                    "Biome",
                    f"Redline updated — trimmed {changed_total} area habitat"
                    f"{'s' if changed_total != 1 else ''} to the new boundary.",
                    level=Qgis.MessageLevel.Info, duration=4)
            if failures:
                QMessageBox.warning(
                    self.iface.mainWindow(), "Trim habitats to redline",
                    "Some habitat geometry could not be trimmed:\n\n"
                    + "\n".join(failures))
            return changed_total

        def _cookie_cut_reshaped_area(area_layer, edited_fid):
            """Give a reshaped area priority over every other parcel.

            This mirrors placement's cookie-cutter rule: the edited polygon is
            retained intact and its final footprint is subtracted from any
            overlapping habitat on the same baseline/proposed layer.
            """
            from qgis.core import QgsFeature, QgsGeometry, QgsWkbTypes

            cutter_feat = area_layer.getFeature(edited_fid)
            if not cutter_feat.isValid():
                return 0
            cutter = cutter_feat.geometry()
            if cutter is None or cutter.isNull() or cutter.isEmpty():
                return 0
            cutter = QgsGeometry.fromWkt(cutter.asWkt(20))
            if not cutter.isGeosValid():
                cutter = cutter.makeValid()
            if cutter.isNull() or cutter.isEmpty():
                return 0

            victims = [QgsFeature(f) for f in area_layer.getFeatures()
                       if f.id() != edited_fid]
            changes = []
            for victim in victims:
                original = victim.geometry()
                if (original is None or original.isNull() or original.isEmpty()
                        or not original.intersects(cutter)):
                    continue
                source = QgsGeometry.fromWkt(original.asWkt(20))
                remainder = source.difference(cutter)
                if remainder is None or remainder.isNull():
                    continue
                if not remainder.isEmpty() and not remainder.isGeosValid():
                    remainder = remainder.makeValid()

                parts = []
                candidates = (remainder.asGeometryCollection()
                              if remainder.isMultipart() else [remainder])
                for part in candidates:
                    if (part is not None and not part.isNull()
                            and not part.isEmpty()
                            and QgsWkbTypes.geometryType(part.wkbType())
                            == QgsWkbTypes.GeometryType.PolygonGeometry
                            and part.area() > 0.001):
                        parts.append(QgsGeometry(part))
                if not parts or not remainder.equals(source):
                    changes.append((victim, parts))

            if not changes or not area_layer.startEditing():
                return 0

            changed = 0
            fid_idx = area_layer.fields().indexFromName("fid")
            for victim, parts in changes:
                if not parts:
                    self._bin_feature(area_layer, victim.id())
                    if area_layer.deleteFeature(victim.id()):
                        changed += 1
                    continue

                if not area_layer.changeGeometry(victim.id(), parts[0]):
                    continue
                changed += 1
                for part in parts[1:]:
                    split = QgsFeature(area_layer.fields())
                    split.setGeometry(part)
                    attrs = list(victim.attributes())
                    if fid_idx >= 0:
                        attrs[fid_idx] = None
                    split.setAttributes(attrs)
                    area_layer.addFeature(split)

            if changed and not area_layer.commitChanges():
                area_layer.rollBack()
                return -1
            if not changed:
                area_layer.rollBack()
            area_layer.updateExtents()
            area_layer.triggerRepaint()
            return changed

        def _add_redline_islands(layer):
            """List interior redline rings as deletable virtual features.

            An island is stored as a ring inside its parent redline feature, so
            it has no provider feature ID of its own.  The Manage screen still
            exposes each ring separately and deleting it fills that hole back
            into the parent boundary.
            """
            from qgis.core import QgsGeometry

            islands = []
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if geom is None or geom.isEmpty():
                    continue
                multipart = geom.isMultipart()
                polygons = geom.asMultiPolygon() if multipart else [geom.asPolygon()]
                for part_index, rings in enumerate(polygons):
                    for ring_index in range(1, len(rings)):
                        ring_geom = QgsGeometry.fromPolygonXY([rings[ring_index]])
                        islands.append((feat.id(), part_index, ring_index,
                                        ring_geom, ring_geom.area()))

            if not islands:
                return

            section_layout = phase_layouts.get("redline", layout)
            hdr = QLabel("REDLINE ISLANDS")
            hdr.setStyleSheet(
                "color:#80362F; font-size:10px; font-weight:600;"
                "letter-spacing:1.2px; padding:11px 2px 2px 2px;"
                "background:transparent; border:none;")
            section_layout.addWidget(hdr)

            def _delete_island(fid, part_index, ring_index, row_widget):
                parent = layer.getFeature(fid)
                if not parent.isValid():
                    QMessageBox.warning(self.iface.mainWindow(), "Delete",
                                        "The parent redline could not be found.")
                    return
                geom = parent.geometry()
                multipart = geom.isMultipart()
                polygons = geom.asMultiPolygon() if multipart else [geom.asPolygon()]
                if (part_index >= len(polygons)
                        or ring_index >= len(polygons[part_index])):
                    QMessageBox.warning(self.iface.mainWindow(), "Delete",
                                        "This redline island no longer exists.")
                    self._schedule_manage_rebuild()
                    return

                del polygons[part_index][ring_index]
                replacement = (QgsGeometry.fromMultiPolygonXY(polygons)
                               if multipart else QgsGeometry.fromPolygonXY(polygons[0]))
                was_editing = layer.isEditable()
                if not was_editing:
                    layer.startEditing()
                ok = layer.changeGeometry(fid, replacement)
                if ok and (was_editing or layer.commitChanges()):
                    layer.updateExtents()
                    layer.triggerRepaint()
                    self.iface.mapCanvas().refresh()
                    self._manage_dirty = True
                    row_widget.setParent(None)
                    row_widget.deleteLater()
                    self._schedule_manage_rebuild()
                else:
                    if not was_editing:
                        layer.rollBack()
                    QMessageBox.warning(self.iface.mainWindow(), "Delete",
                                        "Failed to remove the redline island.")

            def _island_enter(ring_geometry, row_widget):
                def _enter(event):
                    row_widget.setHovered(True)
                    if self._hover_highlight is not None:
                        self._hover_highlight.hide()
                        self._hover_highlight = None
                    highlight = QgsHighlight(
                        self.iface.mapCanvas(), ring_geometry, layer)
                    _style_hover_highlight(
                        highlight, 0, 190, 210,
                        fill_alpha=70, outline_alpha=220)
                    highlight.show()
                    self._hover_highlight = highlight
                return _enter

            def _island_leave(row_widget):
                def _leave(event):
                    row_widget.setHovered(False)
                    if self._hover_highlight is not None:
                        self._hover_highlight.hide()
                        self._hover_highlight = None
                return _leave

            for island_number, (fid, part_index, ring_index,
                                ring_geometry, area) in enumerate(
                    islands, start=1):
                row = _SymbolTextureCard(
                    None, fill="#FFF9F8", border="transparent",
                    hover_fill="#F9E3DF", hover_border="transparent",
                    divider="#F1DFDC")
                row.enterEvent = _island_enter(ring_geometry, row)
                row.leaveEvent = _island_leave(row)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(10, 7, 8, 7)
                label = QLabel(
                    "Redline island %d  ·  %.4f ha"
                    % (island_number, area / 10000.0))
                label.setStyleSheet(
                    "color:%s; font-size:12px; background:transparent; border:none;"
                    % theme.INK)
                row_layout.addWidget(label, 1)

                delete_btn = QPushButton()
                delete_btn.setFixedSize(28, 28)
                delete_btn.setIcon(_svg_icon("trash", "#8F2A23", 16))
                delete_btn.setIconSize(QSize(16, 16))
                delete_btn.setToolTip(
                    "Delete this island and restore the excluded area to the redline")
                self._wire_hint(delete_btn, "Delete redline island")
                delete_btn.setStyleSheet(
                    "QPushButton { background:#FBEDEB; border:none; border-radius:6px; }"
                    "QPushButton:hover { background:#F4CFCB; }")
                delete_btn.clicked.connect(
                    lambda _checked, f=fid, p=part_index, r=ring_index, w=row:
                        _delete_island(f, p, r, w))
                row_layout.addWidget(delete_btn)
                section_layout.addWidget(row)

        def _edit_eco_feature(layer, fid, feat):
            """Eco-specific editor: change feature type, label, add buffer, override colour."""
            from qgis.PyQt.QtWidgets import (
                QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
                QGroupBox, QHBoxLayout, QPushButton, QDoubleSpinBox,
            )
            from qgis.PyQt.QtGui import QColor
            from .gridref_dialog import (FEATURE_GROUPS_FLAT, LINE_FEATURE_GROUPS_FLAT,
                                          AREA_FEATURE_GROUPS_FLAT, _populate_grouped_combo)

            def _field(fname):
                if feat.fieldNameIndex(fname) < 0:
                    return ""
                v = feat[fname]
                # QGIS NULL is not Python None and stringifies to "NULL" —
                # collapse both to an empty string so the editor shows real
                # values (and never writes the literal text "NULL" back).
                if v is None or str(v) == "NULL":
                    return ""
                return str(v)

            lname = layer.name()
            if lname == "Ecological Linear Features":
                groups = LINE_FEATURE_GROUPS_FLAT
            elif lname == "Ecological Area Features":
                groups = AREA_FEATURE_GROUPS_FLAT
            else:
                groups = FEATURE_GROUPS_FLAT

            combo_style = (
                "QComboBox { border:1px solid #C9D1C9; border-radius:4px;"
                " padding:4px 8px; font-size:11px; background:white; color:#1A2420; }"
                "QComboBox:focus { border-color:#00695c; }"
                "QComboBox::drop-down { border:none; }"
                "QComboBox QAbstractItemView { background:white; color:#1A2420;"
                " selection-background-color:#00695c; selection-color:white; }"
            )
            label_style = "color:#1A2420; font-size:11px; font-weight:500;"

            dlg = QDialog(self.iface.mainWindow())
            dlg.setWindowTitle("Edit Feature")
            dlg.setMinimumWidth(340)
            dlg.setStyleSheet("background:#f0faf5;")

            form = QFormLayout()
            form.setSpacing(10)
            form.setContentsMargins(16, 16, 16, 8)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            def _lbl(text):
                l = QLabel(text)
                l.setStyleSheet(label_style)
                return l

            # Feature type combo (grouped by category)
            type_combo = QComboBox()
            type_combo.setStyleSheet(combo_style)
            _populate_grouped_combo(type_combo, groups)
            current_ftype = _field("ftype").strip()
            for i in range(type_combo.model().rowCount()):
                item = type_combo.model().item(i)
                if item and item.isEnabled() and item.text().strip() == current_ftype:
                    type_combo.setCurrentIndex(i)
                    break
            form.addRow(_lbl("Feature type:"), type_combo)

            # Label
            label_edit = QLineEdit(_field("label"))
            label_edit.setStyleSheet(
                "QLineEdit { border:1px solid #C9D1C9; border-radius:4px;"
                " padding:4px 8px; font-size:11px; background:white; }"
                "QLineEdit:focus { border-color:#00695c; }"
            )
            form.addRow(_lbl("Label:"), label_edit)

            # ── Colour override ───────────────────────────────────────
            colour_row = QHBoxLayout()
            current_override = self._get_colour_override(layer, fid)
            colour_swatch = QPushButton()
            colour_swatch.setFixedSize(32, 24)
            self._wire_hint(colour_swatch, "Click to choose a custom colour for this feature")
            _chosen_colour = [current_override]  # mutable container

            def _update_swatch():
                c = _chosen_colour[0]
                if c:
                    colour_swatch.setStyleSheet(
                        f"background:{c.name()}; border:1px solid #A6AFA9; border-radius:3px;"
                    )
                else:
                    colour_swatch.setStyleSheet(
                        "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                        "stop:0 white,stop:0.45 white,stop:0.5 red,stop:0.55 white,stop:1 white);"
                        " border:1px solid #A6AFA9; border-radius:3px;"
                    )
            _update_swatch()

            def _pick_colour():
                from qgis.PyQt.QtWidgets import QColorDialog
                init = _chosen_colour[0] or QColor("#2F5D8A")
                c = QColorDialog.getColor(init, dlg, "Choose feature colour")
                if c.isValid():
                    _chosen_colour[0] = c
                    _update_swatch()

            def _clear_colour():
                _chosen_colour[0] = None
                _update_swatch()

            colour_swatch.clicked.connect(_pick_colour)
            clear_btn = QPushButton("Reset")
            clear_btn.setFixedHeight(24)
            clear_btn.setStyleSheet(
                "QPushButton { font-size:10px; background:#F4F6F2; border:1px solid #C9D1C9;"
                " border-radius:3px; padding:0 6px; }"
                "QPushButton:hover { background:#E3E7E1; }"
            )
            clear_btn.clicked.connect(_clear_colour)
            colour_row.addWidget(colour_swatch)
            colour_row.addWidget(clear_btn)
            colour_row.addStretch()
            colour_w = QWidget()
            colour_w.setLayout(colour_row)
            form.addRow(_lbl("Colour override:"), colour_w)

            # ── Buffer zone section ───────────────────────────────────
            buf_group = QGroupBox("Add Buffer Zone")
            buf_group.setCheckable(True)
            buf_group.setChecked(False)
            buf_group.setStyleSheet(
                "QGroupBox { font-size:11px; font-weight:500; color:#00695c;"
                " border:1px solid #b2dfdb; border-radius:5px; margin-top:6px; padding:6px; }"
                "QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; }"
            )
            buf_hl = QHBoxLayout(buf_group)
            buf_hl.setContentsMargins(6, 4, 6, 4)
            buf_hl.setSpacing(6)

            buf_spin = QDoubleSpinBox()
            buf_spin.setRange(0.1, 10000.0)
            buf_spin.setSingleStep(1.0)
            buf_spin.setValue(10.0)
            buf_spin.setSuffix(" m")
            buf_spin.setStyleSheet(
                "QDoubleSpinBox { border:1px solid #C9D1C9; border-radius:4px;"
                " padding:3px 6px; font-size:11px; background:white; }"
            )
            buf_hl.addWidget(buf_spin, 1)

            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            btns.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(
                "background:#00695c; color:white; border-radius:4px;"
                " padding:5px 16px; font-weight:500;"
            )
            btns.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(
                "background:#e0f2f1; color:#00695c; border:1px solid #00897b;"
                " border-radius:4px; padding:5px 16px;"
            )
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)

            vl = QVBoxLayout()
            vl.setContentsMargins(0, 0, 0, 8)
            vl.setSpacing(6)
            vl.addLayout(form)
            vl.addWidget(buf_group)
            vl.addWidget(btns)
            dlg.setLayout(vl)

            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

            layer.startEditing()

            def _set(fname, value):
                i = layer.fields().indexFromName(fname)
                if i >= 0:
                    layer.changeAttributeValue(fid, i, value)

            _set("ftype", type_combo.currentText().strip())
            _set("label", label_edit.text().strip())
            layer.commitChanges()

            # Apply or clear colour override
            self._apply_colour_override(layer, fid, _chosen_colour[0])

            # ── Generate buffer if requested ──────────────────────────
            if buf_group.isChecked():
                geom = feat.geometry()
                if geom and not geom.isNull():
                    from qgis.core import QgsFeature, QgsGeometry
                    import uuid as _uuid
                    dist = buf_spin.value()
                    dist_str = f"{dist:.0f}m" if dist == int(dist) else f"{dist:.1f}m"
                    final_label = label_edit.text().strip() or _field("label") or current_ftype
                    buf_label = f"{final_label} {dist_str} Buffer"
                    buf_geom = geom.buffer(dist, 8)
                    if buf_geom and not buf_geom.isEmpty():
                        from .gridref_dialog import GridRefDialog as _GRD
                        _inst = _GRD.__new__(_GRD)
                        buf_layer = _inst._get_or_create_buffer_layer()
                        buf_feat = QgsFeature(buf_layer.fields())
                        buf_feat.setGeometry(buf_geom)
                        buf_feat.setAttribute("label",   buf_label)
                        buf_feat.setAttribute("gridref", "")
                        buf_feat.setAttribute("ftype",   type_combo.currentText().strip())
                        buf_feat.setAttribute("uuid",    str(_uuid.uuid4()))
                        buf_layer.startEditing()
                        buf_layer.addFeature(buf_feat)
                        buf_layer.commitChanges()
                        # Apply labeling directly — avoids __init__ dependency
                        try:
                            from qgis.core import (
                                QgsPalLayerSettings, QgsTextFormat,
                                QgsTextBufferSettings, QgsVectorLayerSimpleLabeling,
                                QgsUnitTypes, QgsWkbTypes,
                            )
                            from qgis.PyQt.QtGui import QColor as _QColor
                            pal = QgsPalLayerSettings()
                            pal.fieldName = "label"
                            pal.enabled = True
                            pal.offsetUnits = QgsUnitTypes.RenderUnit.RenderMillimeters
                            try:
                                pal.placement = QgsPalLayerSettings.Placement.PerimeterCurved
                            except AttributeError:
                                pal.placement = QgsPalLayerSettings.Placement.Line
                            fmt = QgsTextFormat()
                            fmt.setSize(9.0)
                            fmt.setColor(_QColor(0, 0, 0))
                            buf_settings = QgsTextBufferSettings()
                            buf_settings.setEnabled(True)
                            buf_settings.setColor(_QColor(255, 255, 255))
                            buf_settings.setSize(1.2)
                            fmt.setBuffer(buf_settings)
                            pal.setFormat(fmt)
                            buf_layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
                            buf_layer.setLabelsEnabled(True)
                        except Exception:
                            pass
                        buf_layer.triggerRepaint()

            # Re-apply style so the new ftype symbol takes effect
            # BUT only if there are no colour overrides — _apply_style
            # would wipe out the rule-based renderer we just built.
            has_overrides = any(
                p.startswith("biome_colour_")
                for p in layer.customProperties().keys()
            )
            if not has_overrides:
                from .gridref_dialog import GridRefDialog
                _grd = GridRefDialog.__new__(GridRefDialog)
                _grd._icon_size_mm = 6
                _grd.emoji_mode_chk = type("_Chk", (), {"isChecked": lambda s: False})()
                _grd.show_labels_chk = type("_Chk", (), {"isChecked": lambda s: True})()
                _grd._apply_style(layer)

            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()
            self._schedule_manage_rebuild()

        def _edit_feature(layer, fid, only_fields=None):
            """Open a dropdown-based property editor. After saving, updates
            all QML style fields so the map symbology refreshes correctly.

            If `only_fields` (a set of field names) is given, only those rows are
            shown — a trimmed "fill in the gaps" editor. The save logic already
            falls back to each field's stored value when its row is absent, so
            the hidden fields are preserved untouched."""
            from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QScrollArea
            from .habitat_data import (
                HABITAT_TYPES, HABITAT_SCREEN_TYPES,
                IRREPLACEABLE_SUBTYPES, condition_items_for,
            )
            from .layer_manager import (
                STRATEGIC_SIGNIFICANCE_OPTIONS,
                WATERCOURSE_ENCROACHMENT_OPTIONS,
                RIPARIAN_ENCROACHMENT_OPTIONS,
                tree_size_from_sub_type, tree_location_from_sub_type,
            )

            feat = layer.getFeature(fid)
            if not feat.isValid():
                return

            # ── Site boundaries have geometry but no editable properties ──
            if layer.name() in ("Redline Boundary", "Blue Line Boundary"):
                return

            # ── Ecological feature layers get their own editor ────────
            ECO_LAYER_NAMES = {"Ecological Features", "Ecological Linear Features", "Ecological Area Features", "Ecological Buffer Zones"}
            if layer.name() in ECO_LAYER_NAMES:
                _edit_eco_feature(layer, fid, feat)
                return

            # Work out what type of layer this is
            lname = layer.name()
            if "Hedgerow" in lname:
                hab_key = "hedgerow"
            elif "Watercourse" in lname:
                hab_key = "watercourse"
            elif "Trees" in lname or "tree" in lname.lower():
                hab_key = "trees"
            else:
                hab_key = "area"
            is_proposed = "Proposed" in lname

            # Allowed top-level habitat categories for this layer type
            allowed_top_types = HABITAT_SCREEN_TYPES.get(hab_key, [])

            # Current values
            feature_top_type = str(feat["habitat_type"] or "")                 if feat.fieldNameIndex("habitat_type") >= 0 else ""
            feature_sub_type = str(feat["sub_habitat_type"] or "")                 if feat.fieldNameIndex("sub_habitat_type") >= 0 else ""
            feature_prop_top_type = str(feat["prop_habitat_type"] or feat["habitat_type"] or "")                 if feat.fieldNameIndex("prop_habitat_type") >= 0 else feature_top_type
            feature_prop_sub_type = str(feat["prop_sub_habitat_type"] or feat["sub_habitat_type"] or "")                 if feat.fieldNameIndex("prop_sub_habitat_type") >= 0 else feature_sub_type

            # Inputs (combos, line edits), the dropdown chevron and the popup
            # lists all come from the shared theme stylesheet applied to the
            # dialog below, so widgets here only declare what differs — e.g. the
            # medium-weight form labels.
            label_style = (f"color:{theme.INK}; font-family:{theme.FONT_MEDIUM};"
                           f" font-size:{theme.SIZE_LABEL}px; font-weight:500;")

            def _make_combo(options, current):
                cb = QComboBox()
                for opt in options:
                    cb.addItem(opt)
                idx = cb.findText(str(current or ""))
                if idx >= 0:
                    cb.setCurrentIndex(idx)
                return cb

            def _field(fname):
                if feat.fieldNameIndex(fname) < 0:
                    return ""
                v = feat[fname]
                # QGIS NULL is not Python None and stringifies to "NULL" —
                # collapse both to an empty string so the editor shows real
                # values (and never writes the literal text "NULL" back).
                if v is None or str(v) == "NULL":
                    return ""
                return str(v)

            def _fill_sub_combo(sub_cb, top_type, current_sub):
                """Repopulate sub_cb with sub-types for top_type, preserving selection."""
                sub_cb.blockSignals(True)
                sub_cb.clear()
                subs = sorted(HABITAT_TYPES.get(top_type, {}).keys())
                for s in subs:
                    sub_cb.addItem(s)
                idx = sub_cb.findText(current_sub)
                if idx >= 0:
                    sub_cb.setCurrentIndex(idx)
                elif sub_cb.count():
                    sub_cb.setCurrentIndex(0)
                sub_cb.blockSignals(False)

            # ── Build dialog ──────────────────────────────────────────
            dlg = QDialog(self.iface.mainWindow())
            dlg.setWindowTitle("Fill in missing details" if only_fields
                               else "Edit Feature Properties")
            dlg.setMinimumWidth(460)
            # Match the rest of the plugin: Inter font, themed inputs with a
            # dropdown chevron, themed popup lists and the chalk surface. A
            # disabled input (a locked condition) reads as greyed-out.
            dlg.setStyleSheet(
                theme.dock_qss()
                + f"QDialog{{background:{theme.SURFACE};}}"
                + f"QComboBox:disabled, QLineEdit:disabled"
                + f"{{color:{theme.FAINT}; background:{theme.SUNKEN};}}"
            )

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea{background:transparent; border:none;}")
            inner = QWidget()
            inner.setObjectName("biomeEditInner")
            inner.setStyleSheet(f"QWidget#biomeEditInner{{background:{theme.SURFACE};}}")
            form = QFormLayout()
            form.setSpacing(8)
            form.setContentsMargins(16, 16, 16, 8)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            editors = {}

            def _row(fname, label, widget):
                # Trimmed editor: only build the requested rows. The save path
                # falls back to the stored value for any field not shown.
                if only_fields is not None and fname not in only_fields:
                    return
                lbl = QLabel(label)
                lbl.setStyleSheet(label_style)
                form.addRow(lbl, widget)
                editors[fname] = widget

            # The editor mirrors the habitat placer exactly: a feature offers
            # the same category / sub-type choices and the same condition list
            # (with the same metric-driven locking) as it did when placed.
            #
            # Proposed-only features store their proposed values directly in
            # habitat_type / sub_habitat_type / condition, so they are edited
            # the same way as a baseline — only the field labels differ.
            #
            # Baseline areas carry NO inline proposed pair: proposed areas are
            # independent features (edited on their own), and retained / lost /
            # enhanced / created is derived from the spatial net by the
            # classifier — never entered here.
            prefix = "Proposed" if is_proposed else "Baseline"

            sub_cb = None
            if allowed_top_types:
                hab_type_cb = _make_combo(allowed_top_types, feature_top_type)
                _row("habitat_type", f"{prefix} Category:", hab_type_cb)

                hab_sub_cb = QComboBox()
                _fill_sub_combo(hab_sub_cb, hab_type_cb.currentText(), feature_sub_type)
                _row("sub_habitat_type", f"{prefix} Habitat Type:", hab_sub_cb)
                sub_cb = hab_sub_cb

            # ── Condition (constrained + locked to match the placer) ──────
            cond_cb = QComboBox()
            _row("condition", f"{prefix} Condition:", cond_cb)

            def _current_sub():
                return (sub_cb.currentText().strip() if sub_cb is not None
                        else feature_sub_type)

            def _apply_condition_lock(sub, want=None):
                """Repopulate + lock the condition combo for sub, exactly like
                the placer's _apply_condition_combo. want overrides which value
                to pre-select (used on open to keep the stored grade)."""
                items, default, locked = condition_items_for(sub)
                cur = want if want is not None else cond_cb.currentText()
                cond_cb.blockSignals(True)
                cond_cb.clear()
                cond_cb.addItems(items)
                target = cur if (cur and cur in items) else default
                i = cond_cb.findText(target)
                cond_cb.setCurrentIndex(i if i >= 0 else 0)
                cond_cb.blockSignals(False)
                # Disabling greys the box via the dialog's :disabled rule, so a
                # locked grade (e.g. Poor for a non-native hedgerow) is obvious.
                cond_cb.setEnabled(not locked)

            def _on_sub_changed_edit(*_a):
                """User changed the sub-type: re-derive the condition list and
                auto-set Irreplaceable, mirroring the placer's _on_sub_changed."""
                sub = _current_sub()
                _apply_condition_lock(sub)
                irr = editors.get("irreplaceable_habitat")
                if irr is not None:
                    if sub in IRREPLACEABLE_SUBTYPES:
                        j = irr.findText("Yes")
                        if j >= 0:
                            irr.setCurrentIndex(j)
                    else:
                        irr.setCurrentIndex(0)   # blank — let the user re-judge

            # Pre-select the stored grade on open (corrected to the locked value
            # for fixed / N/A habitats), then keep it in sync with the sub-type.
            _apply_condition_lock(_current_sub(), want=_field("condition"))
            if sub_cb is not None:
                sub_cb.currentTextChanged.connect(_on_sub_changed_edit)
                # Changing the category repopulates the sub combo with signals
                # blocked, so refresh the dependent combos by hand afterwards.
                def _on_top_changed(top, cb=sub_cb):
                    _fill_sub_combo(cb, top, "")
                    _on_sub_changed_edit()
                hab_type_cb.currentTextChanged.connect(_on_top_changed)

            # ── Strategic significance + Irreplaceable habitat ────────────
            # Both apply to every habitat module in the metric, so — like the
            # placer — they are shown for areas, hedgerows, trees and
            # watercourses alike.
            _row("strategic_significance", "Strategic Significance:",
                 _make_combo(STRATEGIC_SIGNIFICANCE_OPTIONS,
                             _field("strategic_significance")))
            _row("irreplaceable_habitat", "Irreplaceable Habitat:",
                 _make_combo(["", "No", "Yes"], _field("irreplaceable_habitat")))

            # Watercourse encroachment fields
            if hab_key == "watercourse":
                _row("watercourse_encroachment", "Encroachment:",
                     _make_combo(WATERCOURSE_ENCROACHMENT_OPTIONS,
                                 _field("watercourse_encroachment")))
                _row("riparian_encroachment_l", "Riparian Left:",
                     _make_combo(RIPARIAN_ENCROACHMENT_OPTIONS,
                                 _field("riparian_encroachment_l")))
                _row("riparian_encroachment_r", "Riparian Right:",
                     _make_combo(RIPARIAN_ENCROACHMENT_OPTIONS,
                                 _field("riparian_encroachment_r")))

                # A Culvert has no assessable banks/channel — the metric forces
                # every encroachment to "N/A - Culvert"; other types must not
                # offer it. Constrain + lock the combos to match the chosen type.
                def _apply_culvert_enc(*_a):
                    ec = editors.get("watercourse_encroachment")
                    lc = editors.get("riparian_encroachment_l")
                    rc = editors.get("riparian_encroachment_r")
                    sc = editors.get("sub_habitat_type")
                    if not (ec and lc and rc):
                        return
                    sub_now = (sc.currentText().strip() if sc
                               else _field("sub_habitat_type"))
                    if sub_now == "Culvert":
                        for c in (ec, lc, rc):
                            c.blockSignals(True); c.clear()
                            c.addItem("N/A - Culvert"); c.setCurrentIndex(0)
                            c.blockSignals(False); c.setEnabled(False)
                        return
                    chan = [o for o in WATERCOURSE_ENCROACHMENT_OPTIONS
                            if o != "N/A - Culvert"]
                    for c, opts, fld in (
                            (ec, chan, "watercourse_encroachment"),
                            (lc, RIPARIAN_ENCROACHMENT_OPTIONS, "riparian_encroachment_l"),
                            (rc, RIPARIAN_ENCROACHMENT_OPTIONS, "riparian_encroachment_r")):
                        prev = c.currentText()
                        keep = prev if prev in opts else _field(fld)
                        c.blockSignals(True); c.clear(); c.addItems(opts)
                        idx = c.findText(keep)
                        c.setCurrentIndex(idx if idx >= 0 else 0)
                        c.blockSignals(False); c.setEnabled(True)

                _sc = editors.get("sub_habitat_type")
                if _sc is not None:
                    _sc.currentTextChanged.connect(_apply_culvert_enc)
                _apply_culvert_enc()

            # Free-text fields — inherit the themed QLineEdit from the dialog.
            for fname, label in [("habitat_ref", "Habitat Ref:"),
                                  ("user_comments", "Comments:")]:
                _row(fname, label, QLineEdit(_field(fname)))

            # ── Colour override ───────────────────────────────────────
            # Trees use point symbols — colour override not applicable. Skipped
            # in the trimmed (missing-fields-only) editor.
            if only_fields is None and hab_key != "trees":
                from qgis.PyQt.QtWidgets import QHBoxLayout as _HBL
                from qgis.PyQt.QtGui import QColor as _QC
                current_override_hab = self._get_colour_override(layer, fid)
                colour_swatch_hab = QPushButton()
                colour_swatch_hab.setFixedSize(32, 24)
                self._wire_hint(colour_swatch_hab, "Click to choose a custom colour for this feature")
                _chosen_colour_hab = [current_override_hab]

                def _update_swatch_hab():
                    c = _chosen_colour_hab[0]
                    if c:
                        colour_swatch_hab.setStyleSheet(
                            f"background:{c.name()}; border:1px solid #A6AFA9; border-radius:3px;"
                        )
                    else:
                        colour_swatch_hab.setStyleSheet(
                            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                            "stop:0 white,stop:0.45 white,stop:0.5 red,stop:0.55 white,stop:1 white);"
                            " border:1px solid #A6AFA9; border-radius:3px;"
                        )
                _update_swatch_hab()

                def _pick_colour_hab():
                    from qgis.PyQt.QtWidgets import QColorDialog
                    init = _chosen_colour_hab[0] or _QC("#1F7A4D")
                    c = QColorDialog.getColor(init, dlg, "Choose feature colour")
                    if c.isValid():
                        _chosen_colour_hab[0] = c
                        _update_swatch_hab()

                def _clear_colour_hab():
                    _chosen_colour_hab[0] = None
                    _update_swatch_hab()

                colour_swatch_hab.clicked.connect(_pick_colour_hab)
                clear_btn_hab = QPushButton("Reset")
                clear_btn_hab.setFixedHeight(24)
                clear_btn_hab.setStyleSheet(
                    f"QPushButton {{ font-family:{theme.FONT_MEDIUM}; font-size:10px;"
                    f" color:{theme.MUTED}; background:{theme.SUNKEN};"
                    f" border:1px solid {theme.BORDER_STRONG}; border-radius:6px;"
                    f" padding:0 8px; }}"
                    f"QPushButton:hover {{ background:{theme.ACCENT_SOFT}; }}"
                )
                clear_btn_hab.clicked.connect(_clear_colour_hab)
                colour_hl = _HBL()
                colour_hl.addWidget(colour_swatch_hab)
                colour_hl.addWidget(clear_btn_hab)
                colour_hl.addStretch()
                colour_w_hab = QWidget()
                colour_w_hab.setLayout(colour_hl)
                lbl_colour = QLabel("Colour override:")
                lbl_colour.setStyleSheet(label_style)
                form.addRow(lbl_colour, colour_w_hab)

            inner.setLayout(form)
            scroll.setWidget(inner)

            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            btns.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(theme.btn_primary())
            btns.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(theme.btn_secondary())
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)

            # Delete, reachable straight from Feature Info -> Edit rather than
            # only from the Manage list. Returns its own result code so the
            # attribute-writing path below is skipped entirely.
            _DELETE_CODE = 2
            if not only_fields:
                del_btn = QPushButton("Delete")
                # theme tokens, not hex, so it follows the light/dark palette
                del_btn.setStyleSheet(
                    f"QPushButton{{background:{theme.BAD_BG}; color:{theme.BAD};"
                    f" border:1px solid {theme.BAD}; border-radius:4px;"
                    f" padding:5px 14px; font-family:{theme.FONT_MEDIUM};}}"
                    f"QPushButton:hover{{background:{theme.BAD}; color:{theme.SURFACE};}}"
                    if hasattr(theme, "FONT_MEDIUM") else
                    f"QPushButton{{background:{theme.BAD_BG}; color:{theme.BAD};"
                    f" border:1px solid {theme.BAD}; border-radius:4px;"
                    f" padding:5px 14px;}}"
                    f"QPushButton:hover{{background:{theme.BAD}; color:{theme.SURFACE};}}")

                def _ask_delete():
                    from qgis.PyQt.QtWidgets import QMessageBox
                    label = (_field("sub_habitat_type") or _field("habitat_type")
                             or "this feature")
                    if QMessageBox.question(
                            dlg, "Delete feature",
                            f"Delete {label}?\n\nIt goes to the Biome bin, so it "
                            f"can be restored.",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No
                    ) == QMessageBox.StandardButton.Yes:
                        dlg.done(_DELETE_CODE)

                del_btn.clicked.connect(_ask_delete)
                btns.addButton(del_btn, QDialogButtonBox.ButtonRole.DestructiveRole)

            vl = QVBoxLayout()
            vl.setContentsMargins(0, 0, 0, 8)
            vl.setSpacing(0)
            vl.addWidget(scroll)
            vl.addWidget(btns)
            dlg.setLayout(vl)

            _res = dlg.exec()
            if _res == _DELETE_CODE:
                # Same sequence the Manage list uses: clear the twin's pair_id so
                # the survivor reclassifies (baseline-only -> Lost, proposed-only
                # -> Created), snapshot to the bin while the feature is still
                # readable, then delete through the EDIT BUFFER — a direct
                # provider delete can fail with an OGR error on a GPKG layer that
                # is not in edit mode.
                try:
                    self._orphan_pair_twin(layer, fid)
                except Exception:
                    pass
                try:
                    self._bin_feature(layer, fid)
                except Exception:
                    pass
                was_editing = layer.isEditable()
                if not was_editing:
                    layer.startEditing()
                if layer.deleteFeature(fid):
                    layer.commitChanges()
                    layer.dataProvider().reloadData()
                else:
                    layer.rollBack()
                layer.updateExtents()
                layer.triggerRepaint()
                try:
                    self.iface.mapCanvas().refresh()
                except Exception:
                    pass
                for _hook in ("_mark_summary_dirty", "_refresh_balance_widget"):
                    fn = getattr(self, _hook, None)
                    if callable(fn):
                        try:
                            fn()
                        except Exception:
                            pass
                return
            if _res != QDialog.DialogCode.Accepted:
                return

            # ── Write attributes ──────────────────────────────────────
            def _val(fname):
                w = editors.get(fname)
                if w is None:
                    return None
                return w.currentText() if isinstance(w, QComboBox) else w.text()

            layer.startEditing()

            def _set(fname, value):
                idx = layer.fields().indexFromName(fname)
                if idx >= 0 and value is not None:
                    layer.changeAttributeValue(fid, idx, value)

            hab_type  = _val("habitat_type")           or _field("habitat_type")
            sub_type  = _val("sub_habitat_type")      or _field("sub_habitat_type")
            condition = _val("condition")              or _field("condition")
            # Retention is derived (spatial net for areas; pair_id / presence for
            # hedges, trees, watercourses), never edited here — preserve whatever
            # is stored.
            retention = _field("retention")

            # Re-derive habitat_key from the (possibly changed) type/sub
            hab_key_derived = HABITAT_TYPES.get(hab_type, {}).get(sub_type, {}).get("key", _field("habitat_key"))

            _set("habitat_type",           hab_type)
            _set("sub_habitat_type",       sub_type)
            _set("habitat_key",            hab_key_derived)
            _set("condition",              condition)
            _set("retention",              retention)
            _set("strategic_significance", _val("strategic_significance"))
            _set("irreplaceable_habitat",  _val("irreplaceable_habitat"))
            _set("watercourse_encroachment",  _val("watercourse_encroachment"))
            _set("riparian_encroachment_l",   _val("riparian_encroachment_l"))
            _set("riparian_encroachment_r",   _val("riparian_encroachment_r"))
            _set("habitat_ref",    _val("habitat_ref"))
            _set("user_comments",  _val("user_comments"))

            if is_proposed:
                # Proposed-only feature: habitat_type/sub_habitat_type ARE the
                # proposed values — write them into the QML "Proposed" style fields
                _set("Proposed Habitat Type",  sub_type)
                _set("Proposed Hedge Type",    sub_type)
                _set("Proposed River Type",    sub_type)
                _set("Proposed Condition",     condition)
                _set("Retention Category",     retention)
            else:
                # Baseline feature: also carry proposed pair if present
                prop_type = _val("prop_habitat_type")      or hab_type
                prop_sub  = _val("prop_sub_habitat_type")  or sub_type
                prop_cond = _val("prop_condition")         or condition
                prop_key  = HABITAT_TYPES.get(prop_type, {}).get(prop_sub, {}).get("key", hab_key_derived)

                _set("prop_habitat_type",      prop_type)
                _set("prop_sub_habitat_type",  prop_sub)
                _set("prop_condition",         prop_cond)
                _set("prop_habitat_key",       prop_key)

                # ── QML style fields ──
                _set("Baseline Habitat Type",  sub_type)
                _set("Proposed Habitat Type",  prop_sub)
                _set("Baseline Hedge Type",    sub_type)
                _set("Proposed Hedge Type",    prop_sub)
                _set("Baseline River Type",    sub_type)
                _set("Proposed River Type",    prop_sub)
                _set("Baseline Condition",     condition)
                _set("Proposed Condition",     prop_cond)
                _set("Retention Category",     retention)

            if hab_key == "trees":
                tree_size     = tree_size_from_sub_type(sub_type)
                tree_location = tree_location_from_sub_type(sub_type)
                _set("Category",                      "Existing" if not is_proposed else "Proposed")
                _set("Baseline Tree Size",            tree_size)
                _set("Baseline Rural or Urban Tree",  tree_location)
                _set("Proposed Tree Size",            tree_size)
                _set("Proposed Rural or Urban Tree",  tree_location)

            layer.commitChanges()
            layer.triggerRepaint()
            self.iface.mapCanvas().refresh()
            # Editing a habitat changes the BNG figures, but the summary widgets
            # aren't visible here — defer their refresh until Back is pressed.
            self._manage_dirty = True
            self._schedule_manage_rebuild()

            # Apply colour override if the user set one (full editor only)
            if only_fields is None and hab_key != "trees":
                self._apply_colour_override(layer, fid, _chosen_colour_hab[0])

        def _feature_measurement(geom, crs):
            """Return a short human-readable size string for a feature:
            area in ha (or m² when tiny) for polygons, length in m (or km when
            long) for lines, and "" for points/empty geometries."""
            from qgis.core import (QgsDistanceArea, QgsUnitTypes,
                                   QgsWkbTypes, QgsProject)
            if geom is None or geom.isEmpty():
                return ""
            da = QgsDistanceArea()
            try:
                da.setSourceCrs(crs, QgsProject.instance().transformContext())
                da.setEllipsoid(measurement_ellipsoid())
            except Exception:
                pass
            gtype = geom.type()
            try:
                if gtype == QgsWkbTypes.GeometryType.PolygonGeometry:
                    ha = abs(da.convertAreaMeasurement(
                        da.measureArea(geom), QgsUnitTypes.AreaUnit.AreaHectares))
                    if ha <= 0:
                        return ""
                    if ha < 0.01:
                        m2 = ha * 10000.0
                        return f"{m2:,.0f} m²"
                    return f"{ha:.3f} ha"
                if gtype == QgsWkbTypes.GeometryType.LineGeometry:
                    m = abs(da.measureLength(geom))
                    if m <= 0:
                        return ""
                    if m >= 1000:
                        return f"{m / 1000.0:.2f} km"
                    return f"{m:.1f} m"
            except Exception:
                return ""
            return ""

        def _open_feature_condition(lyr, feature_id):
            """Open the shared condition dialog for a placed feature, in *assess*
            mode (editable). The button only appears for baseline assessable
            habitats and retained proposed trees/hedges/watercourses, so this is
            always a real assessment.

            It pre-fills exactly from the stored condition_detail (incl. woodland
            scores); a legacy feature reconstructs what it can from the saved
            summary; a feature with only a manually-set grade opens BLANK and
            fully editable. Ticking writes the new condition (+ detail + summary)
            back to the layer, so the grade updates and persists across saves."""
            from .condition_dialog import (open_condition_dialog,
                                           parse_passed_from_comments, deserialize_state)
            f = lyr.getFeature(feature_id)
            if not f.isValid():
                return
            sub = str(f["sub_habitat_type"] or "") if f.fieldNameIndex("sub_habitat_type") >= 0 else ""
            if not sub:
                return
            comments = str(f["user_comments"] or "") if f.fieldNameIndex("user_comments") >= 0 else ""
            detail = str(f["condition_detail"] or "") if f.fieldNameIndex("condition_detail") >= 0 else ""
            initial = deserialize_state(detail)
            if initial is None and "Condition Assessment:" in comments:
                # Legacy: structured detail wasn't stored — reconstruct from the summary.
                initial = parse_passed_from_comments(comments)
                initial["report"] = comments[comments.rfind("Condition Assessment:"):].strip()
                if f.fieldNameIndex("condition") >= 0:
                    initial["grade"] = str(f["condition"] or "")
            # else (no detail, no summary): open blank — just a manual grade was set.
            ctrls = open_condition_dialog(self.iface.mainWindow(), sub, allow_export=True,
                                          goals=False, initial=initial, on_result=None)
            if not ctrls.get("touched"):
                return                                   # nothing assessed/changed
            grade = ctrls.get("grade")
            report = ctrls.get("report")
            # Make sure the detail field exists on this (possibly older) layer.
            if lyr.fields().indexFromName("condition_detail") < 0:
                try:
                    from .layer_manager import _f
                    lyr.dataProvider().addAttributes([_f("condition_detail")])
                    lyr.updateFields()
                except Exception:
                    pass
            if not lyr.isEditable():
                lyr.startEditing()
            ci = lyr.fields().indexFromName("condition")
            if grade and ci >= 0:
                lyr.changeAttributeValue(feature_id, ci, grade)
            di = lyr.fields().indexFromName("condition_detail")
            if di >= 0:
                lyr.changeAttributeValue(feature_id, di, ctrls.get("detail") or "")
            ui = lyr.fields().indexFromName("user_comments")
            if report and ui >= 0:
                marker = "Condition Assessment:"
                cur = comments
                if marker in cur:
                    cur = cur[:cur.rfind(marker)].rstrip()
                merged = (cur + "\n\n" + report) if cur.strip() else report
                lyr.changeAttributeValue(feature_id, ui, merged)
            lyr.commitChanges()
            lyr.triggerRepaint()
            self._manage_dirty = True
            self._schedule_manage_rebuild()

        def _add_section(title, layer, features, key=None):
            nonlocal any_features
            any_features = True
            _title_lower = title.lower()
            _is_baseline_section = _title_lower.startswith("baseline")
            _is_proposed_section = _title_lower.startswith("proposed")
            if _is_baseline_section:
                _section_fg = "#4E4562"
                _section_divider = "transparent"
                _card_bg = "#F4F2F8"
                _card_border = "#DDD8E8"
                _card_hover = "#ECE8F3"
                _card_hover_border = "#CBC3DA"
            elif _is_proposed_section:
                _section_fg = "#18533F"
                _section_divider = "transparent"
                _card_bg = "#EDF8F3"
                _card_border = "#CDE7DA"
                _card_hover = "#E2F2EA"
                _card_hover_border = "#B7D9C8"
            elif title == "Red Line Boundary":
                _section_fg = "#80362F"
                _section_divider = "#EED5D1"
                _card_bg = "#FFF2F0"
                _card_border = "#EACDC8"
                _card_hover = "#F9E3DF"
                _card_hover_border = "#DFAFA8"
            elif title == "Blue Line Boundary":
                _section_fg = "#24577F"
                _section_divider = "#D6E4F1"
                _card_bg = "#F0F6FD"
                _card_border = "#CBDDEC"
                _card_hover = "#DEEAF7"
                _card_hover_border = "#AFC8DF"
            else:
                _section_fg = "#20543F"
                _section_divider = "#EAF2EC"
                _card_bg = "white"
                _card_border = "#EAF2EC"
                _card_hover = "#D8ECDE"
                _card_hover_border = "#B6D7C1"

            section_layout = layout
            _is_redline_section = title == "Red Line Boundary"
            _is_blueline_section = title == "Blue Line Boundary"
            _uses_section_panel = (_is_baseline_section or _is_proposed_section
                                   or _is_redline_section or _is_blueline_section)
            if _uses_section_panel:
                if _is_baseline_section:
                    phase_key = "baseline"
                elif _is_proposed_section:
                    phase_key = "proposed"
                elif _is_redline_section:
                    phase_key = "redline"
                else:
                    phase_key = "blueline"
                if phase_key not in phase_layouts:
                    phase_panel = QFrame()
                    phase_panel.setObjectName("managePhasePanel")
                    phase_panel.setStyleSheet(
                        "QFrame#managePhasePanel { background:%s; border:1px solid %s;"
                        " border-radius:9px; }" % (_card_bg, _card_border))
                    phase_layout = QVBoxLayout(phase_panel)
                    phase_layout.setContentsMargins(8, 6, 8, 8)
                    phase_layout.setSpacing(5)
                    layout.addWidget(phase_panel)
                    phase_layouts[phase_key] = phase_layout
                section_layout = phase_layouts[phase_key]

                # Rows use a lighter tint from the same family as their phase
                # panel. This preserves card separation without introducing a
                # separate white surface or another competing colour.
                if _is_baseline_section:
                    _card_bg = "#FBFAFD"
                    _card_border = "#E9E5EF"
                    _card_hover = "#EEEAF5"
                    _card_hover_border = "#CFC7DD"
                elif _is_proposed_section:
                    _card_bg = "#F8FCFA"
                    _card_border = "#DDEDE5"
                    _card_hover = "#E2F2EA"
                    _card_hover_border = "#B7D9C8"
                elif _is_redline_section:
                    _card_bg = "#FFF9F8"
                    _card_border = "#F1DFDC"
                    _card_hover = "#F9E3DF"
                    _card_hover_border = "#DFAFA8"
                else:
                    _card_bg = "#F8FBFE"
                    _card_border = "#DCE8F2"
                    _card_hover = "#DEEAF7"
                    _card_hover_border = "#AFC8DF"
            # Section header
            hdr = QLabel(title.upper())
            hdr.setStyleSheet(
                "color:%s; font-size:10px; font-weight:600;"
                "letter-spacing:1.2px; padding:11px 2px 2px 2px;"
                " background:transparent; border:none;" % _section_fg
            )
            section_layout.addWidget(hdr)
            divider = QFrame(); divider.setFrameShape(QFrame.Shape.HLine)
            divider.setFixedHeight(1)
            divider.setStyleSheet(
                "background:%s; border:none;" % _section_divider)
            if _uses_section_panel:
                divider.setVisible(False)
            section_layout.addWidget(divider)

            # Fold long runs of one habitat under a chevron. A section listing
            # 44 vegetated gardens in a row is unreadable and makes everything
            # below it unreachable without a lot of scrolling.
            FOLD_AT = 5
            counts = {}
            for _f_ in features:
                _s_ = (str(_f_["sub_habitat_type"]).strip()
                       if _f_.fieldNameIndex("sub_habitat_type") >= 0
                       and _f_["sub_habitat_type"] else "")
                if _s_:
                    counts[_s_] = counts.get(_s_, 0) + 1
            folds = {}

            def _fold_target(sub):
                """Where a row for *sub* belongs: the section layout, or a
                collapsible box once there are more than FOLD_AT of them."""
                if not sub or counts.get(sub, 0) <= FOLD_AT:
                    return section_layout
                if sub in folds:
                    return folds[sub]
                n = counts[sub]
                fold_key = (layer.id(), sub)
                box = QWidget()
                box.setStyleSheet("background:transparent;")
                box_l = QVBoxLayout(box)
                box_l.setContentsMargins(12, 0, 0, 0)
                box_l.setSpacing(6)
                box.setVisible(False)

                btn = QPushButton("▸  %s  ·  %d features" % (sub, n))
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    "QPushButton { text-align:left; padding:7px 10px;"
                    " border:1px solid %s; border-radius:6px;"
                    " background:%s; color:%s; font-size:12px;"
                    " font-weight:600; }"
                    "QPushButton:hover { background:%s; }"
                    % (_card_border, _card_bg, theme.INK, _card_hover)
                )

                def _toggle(checked, b=btn, w=box, s=sub, count=n,
                            state_key=fold_key):
                    w.setVisible(checked)
                    b.setText("%s  %s  ·  %d features"
                              % ("▾" if checked else "▸", s, count))
                    if checked:
                        self._manage_expanded_folds.add(state_key)
                    else:
                        self._manage_expanded_folds.discard(state_key)

                btn.toggled.connect(_toggle)
                section_layout.addWidget(btn)
                section_layout.addWidget(box)
                # Restore only after the widgets are in the layout. setChecked
                # emits toggled, updating visibility, chevron and state through
                # the same path as an ordinary user click.
                if fold_key in self._manage_expanded_folds:
                    btn.setChecked(True)
                folds[sub] = box_l
                return box_l

            # Display order is independent of permanent provider feature IDs.
            # This lets numbered boundary names close gaps after deletion, so a
            # sole remaining boundary is always shown as Boundary 1.
            for feature_number, feat in enumerate(
                    sorted(features, key=lambda feature: feature.id()), start=1):
                fid   = feat.id()
                # Build a meaningful display label based on what fields are available
                parts = []

                def _f(field):
                    v = feat[field] if feat.fieldNameIndex(field) >= 0 else None
                    return str(v).strip() if v and str(v).strip() else ""

                label_val = _f("label")
                ftype_val = _f("ftype")
                sub_val   = _f("sub_habitat_type")
                cond_val  = _f("condition")
                ret_val   = _f("retention")
                ref_val   = _f("habitat_ref")

                if label_val:
                    # Eco feature — label is the map name (e.g. "Pond-01")
                    parts.append(label_val)
                    if ftype_val:
                        parts.append(ftype_val)
                elif sub_val:
                    # Habitat feature — sub type is primary identifier
                    parts.append(sub_val)
                    if cond_val:
                        parts.append(cond_val)
                    # Only show retention if it was explicitly set (non-blank).
                    # Area features drawn without the paired toggle store "" so
                    # nothing appears. Proposed standalone features store "Created"
                    # which is also suppressed here as it's implicit not user-chosen.
                    #
                    # Trees & hedgerows: the stored 'retention' value is stale
                    # (it isn't updated when a linked twin is deleted), so the
                    # live status badge to the right is the source of truth —
                    # don't duplicate a possibly-wrong word in the label.
                    _lname = layer.name().lower()
                    _is_tree_or_hedge = ("tree" in _lname or "hedge" in _lname)
                    _pr_area_lyr = self.layer_manager.get_layer("proposed_area")
                    _pr_area_name = _pr_area_lyr.name() if _pr_area_lyr else ""
                    _is_proposed_standalone = (
                        layer.name() == _pr_area_name
                        and ret_val == "Created"
                    )
                    if ret_val and not _is_proposed_standalone and not _is_tree_or_hedge:
                        parts.append(ret_val)
                elif title == "Red Line Boundary":
                    # Redline — name it clearly
                    parts.append(f"Red Line Boundary {feature_number}")
                elif title == "Blue Line Boundary":
                    # Wider ownership boundary — name it clearly
                    parts.append(f"Blue Line Boundary {feature_number}")
                elif ftype_val:
                    parts.append(ftype_val)
                else:
                    parts.append(f"Feature {fid}")

                if ref_val:
                    parts.append(f"[{ref_val}]")

                display = "  ·  ".join(parts)

                display = display  # already set above

                # Pairing-derived status for trees & hedgerows (Created /
                # Retained / Enhanced / Lost). Empty for other layers.
                _status, _status_tip = self._tree_hedge_status(layer, feat)

                _geometry_kind = _feature_geometry_kind(layer)
                # Keep every feature card neutral. Area habitats and linear
                # habitats get compact renderer swatches in the controls row;
                # point/tree cards deliberately have no symbol treatment.
                _card_texture = None
                row = _SymbolTextureCard(
                    _card_texture,
                    fill=_card_bg,
                    border="transparent",
                    hover_fill=_card_hover,
                    hover_border="transparent",
                    divider=_card_border)
                _row_css_normal = (
                    "background:transparent; border:none;"
                )
                _row_css_hover = (
                    "background:transparent; border:none;"
                )
                row.setStyleSheet(_row_css_normal)

                # Highlight feature on map when mouse hovers over row, and
                # darken the row's own box so it's obvious which one the
                # cursor is over.
                def _make_enter(lyr, feature, row_widget, hover_css):
                    def _enter(event):
                        if hasattr(row_widget, "setHovered"):
                            row_widget.setHovered(True)
                        else:
                            row_widget.setStyleSheet(hover_css)
                        if self._hover_highlight is not None:
                            self._hover_highlight.hide()
                            self._hover_highlight = None
                        h = QgsHighlight(self.iface.mapCanvas(), feature, lyr)
                        _style_hover_highlight(h, 0, 190, 210,   # cyan/teal
                                               fill_alpha=50, outline_alpha=180)
                        h.show()
                        self._hover_highlight = h
                    return _enter

                def _make_leave(row_widget, normal_css):
                    def _leave(event):
                        if hasattr(row_widget, "setHovered"):
                            row_widget.setHovered(False)
                        else:
                            row_widget.setStyleSheet(normal_css)
                        if self._hover_highlight is not None:
                            self._hover_highlight.hide()
                            self._hover_highlight = None
                    return _leave

                row.enterEvent = _make_enter(layer, feat, row, _row_css_hover)
                row.leaveEvent = _make_leave(row, _row_css_normal)

                # Two-line row: the feature name takes the full panel width on
                # its own line, so it wraps to ~2 lines instead of bunching when
                # the dock is narrow; the badge, size and action buttons sit on
                # a second line below it.
                vl_row = QVBoxLayout()
                vl_row.setContentsMargins(10, 6, 8, 6)
                vl_row.setSpacing(5)

                # Habitat name is slightly stronger; condition and supporting
                # text follow naturally rather than being forced into columns.
                if sub_val and parts:
                    _title_tail = "  ·  ".join(parts[1:])
                    title_row = QHBoxLayout()
                    title_row.setContentsMargins(0, 0, 0, 0)
                    title_row.setSpacing(0)

                    name_lbl = QLabel(parts[0])
                    name_lbl.setTextFormat(Qt.TextFormat.PlainText)
                    name_lbl.setStyleSheet(
                        "color:#1A2420; font-size:12px; font-weight:500;"
                        " border:none; background:transparent;")
                    name_lbl.setToolTip(parts[0])
                    title_row.addWidget(name_lbl, 0)

                    if _title_tail:
                        tail_lbl = QLabel("  ·  " + _title_tail)
                        tail_lbl.setTextFormat(Qt.TextFormat.PlainText)
                        tail_lbl.setStyleSheet(
                            "color:#1A2420; font-size:12px; font-weight:400;"
                            " border:none; background:transparent;")
                        title_row.addWidget(tail_lbl, 0)

                    if _geometry_kind == "line":
                        title_row.addSpacing(12)
                        line_preview = _feature_symbol_texture(layer, feat)
                        if line_preview is not None and not line_preview.isNull():
                            line_swatch = _LineSymbolStrip(line_preview)
                            line_swatch.setToolTip("Map line style")
                            title_row.addWidget(line_swatch, 0)
                    title_row.addStretch(1)
                    vl_row.addLayout(title_row)
                else:
                    lbl = QLabel(display)
                    lbl.setStyleSheet(
                        "color:#1A2420; font-size:12px; font-weight:400;"
                        " border:none; background:transparent;")
                    lbl.setWordWrap(_geometry_kind != "line")
                    vl_row.addWidget(lbl)

                # Controls line — badge + size on the left, buttons pushed right.
                rl = QHBoxLayout()
                rl.setContentsMargins(0, 0, 0, 0)
                # Compact enough for the full action set (including Duplicate)
                # to remain visible at the standard 400 px dock width.
                rl.setSpacing(4)

                # Fixed information columns keep every area and length aligned:
                # status badges occupy the first column for linear/point
                # habitats, while area symbols use that same column immediately
                # to the left of the measurement.
                _info_column_used = False

                # ── Status badge (trees & hedgerows): Created/Retained/Lost ──
                if _status:
                    _badge_colours = {
                        "Retained": ("#1F7A4D", "#E9F4EC"),
                        "Enhanced": ("#1F7A4D", "#E9F4EC"),
                        "Created":  ("#9A6700", "#FBF4E4"),
                        "Lost":     ("#B3372F", "#FBEDEB"),
                    }
                    _fg, _bg = _badge_colours.get(_status, ("#54635A", "#EEF2EC"))
                    badge = QLabel(_status)
                    badge.setStyleSheet(
                        "color:%s; background:%s; font-size:10px; font-weight:600;"
                        "border:none; border-radius:8px; padding:2px 4px;" % (_fg, _bg)
                    )
                    badge.setFixedSize(50, 20)
                    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    badge.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                    if _status_tip:
                        badge.setToolTip(_status_tip)
                        self._wire_hint(badge, _status_tip)
                    rl.addWidget(badge, 0)
                    _info_column_used = True

                # Area habitats: show their map-style swatch in the same fixed
                # column used by status badges, directly left of the area value.
                if _geometry_kind == "polygon" and not _info_column_used:
                    area_preview = _feature_symbol_texture(layer, feat)
                    _pill_outline = (
                        "#E21D2D" if title == "Red Line Boundary" else
                        "#0066CC" if title == "Blue Line Boundary" else
                        "#D5DFDB"
                    )
                    area_swatch = _AreaSymbolPill(
                        area_preview, outline=_pill_outline)
                    if area_preview is not None and not area_preview.isNull():
                        area_swatch.setToolTip("Map habitat style")
                    rl.addWidget(area_swatch, 0)
                    _info_column_used = True

                # Preserve the grid for any measured feature without a badge or
                # area symbol, including older or incomplete linear records.
                meas_text = _feature_measurement(feat.geometry(), layer.crs())
                if meas_text and not _info_column_used:
                    rl.addSpacing(50)

                # ── Size readout (area for polygons, length for lines) ──
                if meas_text:
                    meas_lbl = QLabel(meas_text)
                    meas_lbl.setFixedWidth(50)
                    meas_lbl.setStyleSheet(
                        "color:#1A2420; font-size:12px; font-weight:500;"
                        " border:none; background:transparent; padding:0;"
                    )
                    meas_lbl.setAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    rl.addWidget(meas_lbl, 0)

                # Push the action buttons to the right edge of the controls line.
                rl.addStretch(1)

                # Action controls share one quiet blue-grey treatment so colour
                # remains meaningful for habitat swatches, status badges and
                # the destructive Delete action.
                _action_icon = "#304F59"
                _action_qss = (
                    "QPushButton { background:transparent; border:none; border-radius:6px;"
                    " font-size:13px; }"
                    "QPushButton:hover { background:#DCE7E9; }"
                    "QPushButton:pressed { background:#CEDCDF; }"
                )

                # ── Visibility toggle ─────────────────────────────────
                # Use per-feature fid-based subset string filtering so only
                # the individual feature is hidden, not the whole layer.
                # Ecological layers use their uuid field; all others use fid.
                ECO_LAYER_NAMES_VIS = {"Ecological Features", "Ecological Linear Features",
                                       "Ecological Area Features", "Ecological Buffer Zones"}
                is_eco = layer.name() in ECO_LAYER_NAMES_VIS
                if is_eco:
                    feat_key   = _f("uuid")
                    key_field  = "uuid"
                    key_quote  = True   # uuid is a string — wrap in quotes
                else:
                    feat_key   = str(fid) if fid is not None else ""
                    key_field  = "fid"
                    key_quote  = False  # fid is an integer — no quotes

                # Determine current visibility from the layer subset string
                current_subset = layer.subsetString() or ""
                if feat_key:
                    kv = f"{key_field} != '{feat_key}'" if key_quote else f"{key_field} != {feat_key}"
                    is_vis = kv not in current_subset
                    vis_tip = f"Show/hide this feature on the map"
                else:
                    # No key available — fall back to layer-level toggle
                    root_tree = QgsProject.instance().layerTreeRoot()
                    lyr_node  = root_tree.findLayer(layer.id())
                    is_vis    = lyr_node.isVisible() if lyr_node is not None else True
                    vis_tip   = f"Show/hide all {layer.name()}"

                from qgis.PyQt.QtCore import QSize
                vis_btn = QPushButton()
                vis_btn.setFixedSize(28, 28)
                vis_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                vis_btn.setToolTip("Show or hide this feature on the map")
                self._wire_hint(vis_btn, vis_tip)
                vis_btn.setCheckable(True)
                vis_btn.setChecked(is_vis)
                vis_btn.setIcon(_svg_icon("eye" if is_vis else "eye-off", _action_icon, 16))
                vis_btn.setIconSize(QSize(16, 16))
                vis_btn.setStyleSheet(_action_qss)

                def _make_vis_toggle(lyr, btn, fk, kf, kq):
                    def _toggle(checked):
                        import re as _re
                        if fk:
                            current = lyr.subsetString() or ""
                            # Parse all currently excluded keys for this field
                            if kq:
                                pattern = rf"{kf}\s*!=\s*'([^']+)'"
                            else:
                                pattern = rf"{kf}\s*!=\s*(\d+)"
                            excluded = set()
                            for m in _re.finditer(pattern, current):
                                excluded.add(m.group(1))
                            key_str = fk if kq else str(fk)
                            if checked:
                                excluded.discard(key_str)
                            else:
                                excluded.add(key_str)
                            if excluded:
                                if kq:
                                    parts = [f"{kf} != '{u}'" for u in sorted(excluded)]
                                else:
                                    parts = [f"{kf} != {u}" for u in sorted(excluded, key=int)]
                                lyr.setSubsetString(" AND ".join(parts))
                            else:
                                lyr.setSubsetString("")
                        else:
                            nd = QgsProject.instance().layerTreeRoot().findLayer(lyr.id())
                            if nd is not None:
                                nd.setItemVisibilityChecked(checked)
                        btn.setIcon(_svg_icon("eye" if checked else "eye-off", _action_icon, 16))
                        lyr.triggerRepaint()
                        self.iface.mapCanvas().refresh()
                    return _toggle

                vis_btn.toggled.connect(_make_vis_toggle(layer, vis_btn, feat_key, key_field, key_quote))
                rl.addWidget(vis_btn)

                # ── Reshape button (polygon/line area habitats only) ──
                is_reshapeable = (
                    layer.name() not in {
                        "Ecological Features",
                        "Ecological Buffer Zones",
                    }
                )
                if is_reshapeable:
                    reshape_btn = QPushButton()
                    reshape_btn.setFixedSize(28, 28)
                    reshape_btn.setIcon(_svg_icon("polygon", _action_icon, 16))
                    reshape_btn.setIconSize(QSize(16, 16))
                    reshape_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                    reshape_btn.setToolTip("Reshape — edit the nodes of this feature on the map")
                    self._wire_hint(reshape_btn,
                        "Reshape feature — activates node tool. "
                        "If Create Baseline + Proposed is ON, the paired "
                        "proposed feature will be reshaped to match."
                    )
                    reshape_btn.setStyleSheet(_action_qss)
                    def _make_reshape(lyr, feature_id, row_widget):
                        def _do_reshape():
                            from qgis.core import (
                                QgsExpressionContext,
                                QgsExpressionContextUtils,
                                QgsGeometry,
                                QgsProject,
                                QgsRenderContext,
                            )
                            from qgis.gui import QgsRubberBand, QgsVertexMarker
                            from qgis.PyQt.QtCore import QEvent, QObject, QTimer
                            from qgis.PyQt.QtWidgets import QToolButton
                            from qgis.PyQt.QtGui import QColor
                            orig_feat = lyr.getFeature(feature_id)
                            orig_geom = QgsGeometry(orig_feat.geometry()) if orig_feat.isValid() else None
                            if not orig_feat.isValid():
                                QMessageBox.warning(
                                    self, "Reshape feature",
                                    "The selected feature could not be found.")
                                return
                            was_editable = lyr.isEditable()
                            if not was_editable and not lyr.startEditing():
                                QMessageBox.warning(
                                    self, "Reshape feature",
                                    "This habitat layer could not be opened for editing.")
                                return

                            # Set the active layer only after edit mode is live.
                            # QGIS enables the current-layer vertex action from
                            # both states, and doing this in the opposite order
                            # can leave the action bound to the previously
                            # active boundary layer until the next UI cycle.
                            if not self.iface.setActiveLayer(lyr):
                                if not was_editable:
                                    lyr.rollBack()
                                QMessageBox.warning(
                                    self, "Reshape feature",
                                    "The habitat layer is not available in the current project.")
                                return
                            lyr.removeSelection()
                            lyr.select(feature_id)
                            self.iface.mapCanvas().zoomToSelected(lyr)

                            # Give area habitats a persistent edit tint based on
                            # their rendered colour. Boundaries keep a fully
                            # transparent selection overlay, so their real
                            # red/blue renderer moves live with the edit instead
                            # of drawing a second or stale outline.
                            canvas = self.iface.mapCanvas()
                            orig_sel_color = canvas.selectionColor()
                            is_redline = lyr.name() in {
                                "Redline Boundary", "Red Line Boundary"}
                            is_boundary = (is_redline
                                           or lyr.name() == "Blue Line Boundary")

                            def _rendered_feature_colour():
                                fallback = (QColor("#D64545")
                                            if "Red" in lyr.name()
                                            else QColor("#3278B8")
                                            if "Blue" in lyr.name()
                                            else QColor("#5F8F68"))
                                renderer = lyr.renderer()
                                if renderer is None:
                                    return fallback
                                try:
                                    context = QgsRenderContext.fromMapSettings(
                                        canvas.mapSettings())
                                    expression_context = QgsExpressionContext()
                                    expression_context.appendScopes(
                                        QgsExpressionContextUtils.globalProjectLayerScopes(
                                            lyr))
                                    expression_context.setFeature(orig_feat)
                                    context.setExpressionContext(expression_context)
                                    renderer.startRender(context, lyr.fields())
                                    try:
                                        symbol = renderer.symbolForFeature(
                                            orig_feat, context)
                                        if symbol is not None:
                                            if is_boundary:
                                                for index in range(
                                                        symbol.symbolLayerCount()):
                                                    symbol_layer = symbol.symbolLayer(index)
                                                    for getter_name in (
                                                            "strokeColor",
                                                            "outlineColor"):
                                                        getter = getattr(
                                                            symbol_layer,
                                                            getter_name, None)
                                                        if callable(getter):
                                                            stroke = QColor(getter())
                                                            if (stroke.isValid()
                                                                    and stroke.alpha() > 0):
                                                                return stroke
                                            colour = QColor(symbol.color())
                                            if (colour.isValid()
                                                    and colour.alpha() > 0):
                                                return colour
                                    finally:
                                        renderer.stopRender(context)
                                except Exception:
                                    pass
                                return fallback

                            edit_colour = _rendered_feature_colour().lighter(118)
                            edit_colour.setAlpha(0 if is_boundary else 82)
                            canvas.setSelectionColor(edit_colour)

                            # Area reshape should snap cleanly to neighbouring
                            # habitat edges and to the application redline. Keep
                            # the user's full previous configuration and restore
                            # it when this one reshape session ends.
                            restore_reshape_snapping = lambda: None
                            if key in ("baseline_area", "proposed_area"):
                                from qgis.core import (
                                    QgsSnappingConfig, QgsTolerance,
                                )
                                project = QgsProject.instance()
                                previous_snapping = project.snappingConfig()
                                snap_cfg = QgsSnappingConfig()
                                snap_cfg.setEnabled(True)
                                snap_cfg.setMode(
                                    QgsSnappingConfig.SnappingMode.AdvancedConfiguration)
                                try:
                                    snap_setting = (
                                        QgsSnappingConfig.IndividualLayerSettings())
                                    snap_setting.setEnabled(True)
                                    try:
                                        snap_setting.setTypeFlag(
                                            QgsSnappingConfig.SnappingTypes.Vertex |
                                            QgsSnappingConfig.SnappingTypes.Segment)
                                    except (TypeError, AttributeError):
                                        snap_setting.setTypeFlag(
                                            QgsSnappingConfig.SnappingType.VertexAndSegment)
                                    snap_setting.setTolerance(10)
                                    snap_setting.setUnits(
                                        QgsTolerance.UnitType.Pixels)
                                except (TypeError, AttributeError):
                                    snap_setting = (
                                        QgsSnappingConfig.IndividualLayerSettings(
                                            True,
                                            QgsSnappingConfig.SnappingType.VertexAndSegment,
                                            10, QgsTolerance.UnitType.Pixels))

                                snap_cfg.setIndividualLayerSettings(
                                    lyr, snap_setting)
                                redline = self.layer_manager.get_layer("redline")
                                if redline is not None and redline.isValid():
                                    snap_cfg.setIndividualLayerSettings(
                                        redline, snap_setting)
                                project.setSnappingConfig(snap_cfg)
                                snapping_restored = [False]

                                def restore_reshape_snapping():
                                    if snapping_restored[0]:
                                        return
                                    snapping_restored[0] = True
                                    project.setSnappingConfig(previous_snapping)

                            # Recolour QGIS's own vertex-tool canvas items. This
                            # avoids a second set of overlaid handles, so the
                            # visible yellow vertex/midpoint is always the one
                            # QGIS will actually edit.
                            native_vertex_styles = {}

                            def _style_native_vertex_handles():
                                if finished[0]:
                                    return
                                yellow = QColor("#FFD400")
                                for item in canvas.scene().items():
                                    try:
                                        if isinstance(item, QgsVertexMarker):
                                            item_key = id(item)
                                            if item_key not in native_vertex_styles:
                                                native_vertex_styles[item_key] = (
                                                    item, "marker",
                                                    QColor(item.color()),
                                                    QColor(item.fillColor()),
                                                    item.zValue())
                                            item.setColor(yellow)
                                            item.setFillColor(yellow)
                                            item.setIconSize(18)
                                            item.setPenWidth(4)
                                            item.setZValue(10001)
                                        elif (isinstance(item, QgsRubberBand)
                                              and item.icon()
                                              == QgsRubberBand.IconType.ICON_CIRCLE
                                              and round(item.iconSize()) in (6, 15)):
                                            # The vertex tool uses circular
                                            # rubber-band icons for real nodes.
                                            # Its edge highlight has no icon, so
                                            # it is deliberately left alone.
                                            item_key = id(item)
                                            if item_key not in native_vertex_styles:
                                                native_vertex_styles[item_key] = (
                                                    item, "band",
                                                    QColor(item.strokeColor()),
                                                    QColor(item.fillColor()),
                                                    item.iconSize(), item.width(),
                                                    item.zValue())
                                            item.setStrokeColor(yellow)
                                            item.setFillColor(yellow)
                                            item.setIconSize(max(
                                                11, item.iconSize()))
                                            item.setWidth(3)
                                            item.setZValue(10000)
                                    except RuntimeError:
                                        pass

                            def _restore_native_vertex_handles():
                                # The vertex tool owns and may delete these C++
                                # canvas items at any point. Calling a setter on
                                # a stale Python wrapper can crash QGIS before
                                # Python can raise RuntimeError, so never touch
                                # them during teardown. Switching to Pan removes
                                # the tool's temporary handles immediately.
                                native_vertex_styles.clear()

                            canvas.refresh()

                            # Only one reshape session can own the canvas
                            # right-click handler. Remove a stale handler if a
                            # previous session was superseded unexpectedly.
                            old_filter = getattr(
                                self, "_reshape_right_click_filter", None)
                            if old_filter is not None:
                                try:
                                    canvas.viewport().removeEventFilter(old_filter)
                                except Exception:
                                    pass
                                self._reshape_right_click_filter = None

                            finished = [False]

                            def _remove_right_click_filter():
                                event_filter = getattr(
                                    self, "_reshape_right_click_filter", None)
                                if event_filter is not None:
                                    try:
                                        canvas.viewport().removeEventFilter(
                                            event_filter)
                                    except Exception:
                                        pass
                                    self._reshape_right_click_filter = None

                            def _remove_vertex_overlay():
                                _restore_native_vertex_handles()
                                canvas.refresh()

                            def _finish():
                                if finished[0]:
                                    return
                                finished[0] = True
                                _remove_right_click_filter()
                                _remove_vertex_overlay()
                                restore_reshape_snapping()
                                self.iface.messageBar().clearWidgets()
                                edited_feat = lyr.getFeature(feature_id)
                                edited_geom = (edited_feat.geometry()
                                               if edited_feat.isValid()
                                               else None)

                                # Removing every node with the vertex tool does
                                # not delete the feature: QGIS leaves a row with
                                # an empty geometry, which a GeoPackage polygon
                                # table rejects at commit time. Treat that
                                # gesture as the same proper deletion offered by
                                # Habitat Manager (including bin/paired cleanup).
                                deleted_via_shape = (
                                    not edited_feat.isValid()
                                    or edited_geom is None
                                    or edited_geom.isNull()
                                    or edited_geom.isEmpty())
                                if deleted_via_shape:
                                    lyr.rollBack()  # restore it for bin snapshot
                                    lyr.removeSelection()
                                    canvas.setSelectionColor(orig_sel_color)
                                    self.iface.actionPan().trigger()
                                    _delete_feature(
                                        lyr, feature_id, row_widget)
                                    return

                                committed = lyr.commitChanges()
                                if not committed:
                                    errors = [str(e) for e in lyr.commitErrors()
                                              if str(e).strip()]
                                    lyr.rollBack()
                                    detail = ("\n\n" + "\n".join(errors)) if errors else ""
                                    QMessageBox.warning(
                                        self, "Reshape feature",
                                        "The shape change could not be saved, so "
                                        "the original geometry has been restored."
                                        + detail)
                                elif is_redline:
                                    _trim_area_habitats_to_redline(lyr)
                                elif key in ("baseline_area", "proposed_area"):
                                    cut_count = _cookie_cut_reshaped_area(
                                        lyr, feature_id)
                                    if cut_count < 0:
                                        QMessageBox.warning(
                                            self, "Reshape feature",
                                            "The habitat was reshaped, but the "
                                            "overlapping habitat could not be "
                                            "trimmed. Please check for overlaps.")
                                # Deselect before restoring the (opaque) selection
                                # colour, otherwise the still-selected feature
                                # renders as a solid yellow fill until the layer
                                # is next reloaded.
                                lyr.removeSelection()
                                lyr.triggerRepaint()
                                canvas.setSelectionColor(orig_sel_color)
                                self.iface.actionPan().trigger()
                                self.iface.mapCanvas().refresh()

                            def _cancel():
                                if finished[0]:
                                    return
                                finished[0] = True
                                _remove_right_click_filter()
                                _remove_vertex_overlay()
                                restore_reshape_snapping()
                                self.iface.messageBar().clearWidgets()
                                lyr.rollBack()
                                lyr.removeSelection()
                                canvas.setSelectionColor(orig_sel_color)
                                self.iface.actionPan().trigger()
                                self.iface.mapCanvas().refresh()

                            if (orig_geom and not orig_geom.isNull()
                                    and getattr(self, "_proposed_mapping", False)):
                                def _on_geom_changed(fid, new_geom):
                                    if fid != feature_id:
                                        return
                                    lname = lyr.name()
                                    if "Baseline" in lname:
                                        prop_name = lname.replace("Baseline", "Proposed")
                                        prop_lyrs = QgsProject.instance().mapLayersByName(prop_name)
                                        if prop_lyrs:
                                            prop_lyr = prop_lyrs[0]
                                            for pf in prop_lyr.getFeatures():
                                                if pf.geometry().equals(orig_geom):
                                                    if not prop_lyr.isEditable():
                                                        prop_lyr.startEditing()
                                                    prop_lyr.changeGeometry(pf.id(), QgsGeometry(new_geom))
                                                    prop_lyr.commitChanges()
                                                    prop_lyr.triggerRepaint()
                                                    break
                                    try:
                                        lyr.geometryChanged.disconnect(_on_geom_changed)
                                    except Exception:
                                        pass
                                lyr.geometryChanged.connect(_on_geom_changed)

                            # Push message bar with Finish / Cancel buttons
                            msg = self.iface.messageBar().createMessage(
                                "Biome", "Reshaping — move nodes, then:")
                            finish_btn = QToolButton()
                            finish_btn.setText("Finish")
                            finish_btn.setIcon(_svg_icon("check", "white", 13))
                            finish_btn.setIconSize(QSize(13, 13))
                            finish_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                            finish_btn.setStyleSheet(
                                "background:#1F7A4D; color:white; padding:3px 8px;"
                                " border-radius:4px; font-weight:500;")
                            finish_btn.clicked.connect(_finish)
                            cancel_btn = QToolButton()
                            cancel_btn.setText("Cancel")
                            cancel_btn.setIcon(_svg_icon("x", "white", 13))
                            cancel_btn.setIconSize(QSize(13, 13))
                            cancel_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                            cancel_btn.setStyleSheet(
                                "background:#B3372F; color:white; padding:3px 8px;"
                                " border-radius:4px; font-weight:500;")
                            cancel_btn.clicked.connect(_cancel)
                            msg.layout().addWidget(finish_btn)
                            msg.layout().addWidget(cancel_btn)
                            self.iface.messageBar().pushWidget(msg, level=Qgis.MessageLevel.Info)

                            class _ReshapeRightClickFilter(QObject):
                                """Make map right-click identical to Finish."""

                                def eventFilter(filter_self, obj, event):
                                    if event.type() == QEvent.Type.MouseMove:
                                        # QGIS creates/shows its midpoint cross
                                        # while processing this same move. Style
                                        # it afterwards so it cannot revert red.
                                        QTimer.singleShot(
                                            0, _style_native_vertex_handles)
                                    if (event.type() in (
                                            QEvent.Type.MouseButtonPress,
                                            QEvent.Type.MouseButtonRelease)
                                            and event.button()
                                            == Qt.MouseButton.RightButton):
                                        # Consume both halves of the click so
                                        # the vertex tool cannot also handle it;
                                        # save on release to match a normal
                                        # completed mouse gesture.
                                        if (event.type()
                                                == QEvent.Type.MouseButtonRelease):
                                            _finish()
                                        return True
                                    return False

                            self._reshape_right_click_filter = (
                                _ReshapeRightClickFilter(canvas.viewport()))
                            canvas.viewport().installEventFilter(
                                self._reshape_right_click_filter)

                            def _activate_vertex_tool():
                                if finished[0]:
                                    return
                                # Restrict vertex picking to the habitat layer.
                                # The all-layers action can attach to an
                                # overlapping editable redline/blue-line layer,
                                # making the habitat's nodes appear immovable.
                                try:
                                    action = self.iface.actionVertexToolActiveLayer()
                                except (AttributeError, TypeError):
                                    action = self.iface.actionVertexTool()
                                if action is not None:
                                    action.trigger()
                                    # The QGIS tool owns these handles; recolour
                                    # them only after it is active.
                                    QTimer.singleShot(
                                        0, _style_native_vertex_handles)

                            # Let edit/active-layer signals settle before QGIS
                            # constructs its vertex map tool.
                            QTimer.singleShot(0, _activate_vertex_tool)

                        return _do_reshape
                    reshape_btn.clicked.connect(
                        _make_reshape(layer, fid, row))
                    rl.addWidget(reshape_btn)

                # ── Move button (point features only) ────────────────
                POINT_LAYER_NAMES = {
                    "Ecological Features",
                    "Baseline - Individual Trees",
                    "Proposed - Individual Trees",
                }
                if layer.name() in POINT_LAYER_NAMES:
                    move_btn = QPushButton()
                    move_btn.setFixedSize(28, 28)
                    move_btn.setIcon(_svg_icon("arrows-move", _action_icon, 16))
                    move_btn.setIconSize(QSize(16, 16))
                    move_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                    move_btn.setToolTip("Move this feature to a new location on the map")
                    self._wire_hint(move_btn, "Move feature — click new location on the map")
                    move_btn.setStyleSheet(_action_qss)
                    def _make_move(lyr, feature_id):
                        def _do_move():
                            from qgis.PyQt.QtWidgets import QToolButton
                            from qgis.gui import QgsMapToolEmitPoint
                            from qgis.core import QgsGeometry, QgsPointXY

                            self.iface.setActiveLayer(lyr)
                            lyr.removeSelection()
                            lyr.select(feature_id)
                            self.iface.mapCanvas().zoomToSelected(lyr)

                            canvas = self.iface.mapCanvas()
                            # Every exit restores PAN, never the captured
                            # previous tool — that could itself be another
                            # armed Biome tool (delete/place), and restoring
                            # it would silently re-arm a stale action.

                            class _ClickToMoveTool(QgsMapToolEmitPoint):
                                def __init__(self, canvas):
                                    super().__init__(canvas)

                                def canvasReleaseEvent(self, event):
                                    from qgis.core import (QgsPointXY, QgsGeometry,
                                        QgsCoordinateTransform, QgsProject)
                                    if event.button() == Qt.MouseButton.LeftButton:
                                        # Point is in canvas CRS — transform to layer CRS
                                        pt_canvas = self.toMapCoordinates(event.pos())
                                        canvas_crs = canvas.mapSettings().destinationCrs()
                                        layer_crs  = lyr.crs()
                                        if canvas_crs != layer_crs:
                                            tr = QgsCoordinateTransform(
                                                canvas_crs, layer_crs, QgsProject.instance())
                                            pt_canvas = tr.transform(pt_canvas)
                                        if not lyr.isEditable():
                                            lyr.startEditing()
                                        lyr.changeGeometry(feature_id,
                                            QgsGeometry.fromPointXY(QgsPointXY(pt_canvas)))
                                        lyr.commitChanges()
                                        lyr.removeSelection()
                                        lyr.triggerRepaint()
                                        canvas.refresh()
                                        self.iface_ref.messageBar().clearWidgets()
                                        self.iface_ref.actionPan().trigger()
                                        self.panel_ref._active_move_tool = None
                                        self.panel_ref._open_manage_screen()

                                def keyPressEvent(self, event):
                                    if event.key() == Qt.Key.Key_Escape:
                                        lyr.removeSelection()
                                        self.iface_ref.messageBar().clearWidgets()
                                        self.iface_ref.actionPan().trigger()
                                        self.panel_ref._active_move_tool = None

                            tool = _ClickToMoveTool(canvas)
                            tool.iface_ref = self.iface
                            tool.panel_ref = self
                            tool.layer_ref = lyr   # for _abort_move_tool cleanup
                            self._active_move_tool = tool  # keep reference to prevent GC
                            canvas.setMapTool(tool)

                            msg = self.iface.messageBar().createMessage(
                                "Biome", "Click the new location for this feature  (Esc to cancel)")
                            cancel_btn = QToolButton()
                            cancel_btn.setText("Cancel")
                            cancel_btn.setIcon(_svg_icon("x", "white", 13))
                            cancel_btn.setIconSize(QSize(13, 13))
                            cancel_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                            cancel_btn.setStyleSheet(
                                "background:#B3372F; color:white; padding:3px 8px;"
                                " border-radius:4px; font-weight:500;")
                            def _cancel():
                                lyr.removeSelection()
                                self.iface.messageBar().clearWidgets()
                                self.iface.actionPan().trigger()
                                self._active_move_tool = None
                            cancel_btn.clicked.connect(_cancel)
                            msg.layout().addWidget(cancel_btn)
                            self.iface.messageBar().pushWidget(msg, level=Qgis.MessageLevel.Info)

                        return _do_move
                    move_btn.clicked.connect(_make_move(layer, fid))
                    rl.addWidget(move_btn)

                # ── Condition assessment button ──────────────────────────────
                # Baseline assessable habitats always get it (opens editable — even
                # if only a grade was set, ticking writes the condition back).
                # Proposed habitats DON'T get it, except a RETAINED tree / hedgerow
                # / watercourse: those are existing habitats kept on-site, so they
                # have a real condition to assess.
                _cond_sub = (str(feat["sub_habitat_type"] or "")
                             if feat.fieldNameIndex("sub_habitat_type") >= 0 else "")
                _show_cond = False
                if _cond_sub:
                    try:
                        from . import condition_data as _CD
                        from .habitat_data import is_condition_locked as _cond_locked
                        _assessable = _CD.has_panel(_cond_sub) and not _cond_locked(_cond_sub)
                    except Exception:
                        _assessable = False
                    if _assessable:
                        if "Proposed" in layer.name():
                            _ret = (str(feat["retention"] or "").strip()
                                    if feat.fieldNameIndex("retention") >= 0 else "")
                            _is_thw = any(k in layer.name() for k in
                                          ("Individual Trees", "Hedgerow", "Watercourse"))
                            _show_cond = _is_thw and _ret == "Retained"
                        else:
                            _show_cond = True
                if _show_cond:
                    cond_btn = QPushButton()
                    cond_btn.setFixedSize(28, 28)
                    cond_btn.setIcon(_svg_icon("clipboard-check", _action_icon, 16))
                    cond_btn.setIconSize(QSize(16, 16))
                    cond_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                    _cond_tip = "Condition assessment — open, copy or export the sheet"
                    cond_btn.setToolTip(_cond_tip)
                    self._wire_hint(cond_btn, _cond_tip)
                    cond_btn.setStyleSheet(_action_qss)
                    cond_btn.clicked.connect(
                        lambda _checked, l=layer, f=fid: _open_feature_condition(l, f)
                    )
                    rl.addWidget(cond_btn)

                # Trim — cut a section out of a hedgerow/watercourse line
                # (e.g. an access road through a retained hedge). Works on
                # linked pairs (directional trim + hidden twin split) and on
                # unlinked lines (plain geometry cut) — see hedge_trimmer.py.
                from .hedge_trimmer import TRIM_KEYS
                if key in TRIM_KEYS and feat.geometry() is not None \
                        and not feat.geometry().isNull():
                    trim_btn = QPushButton()
                    trim_btn.setFixedSize(28, 28)
                    trim_btn.setIcon(_svg_icon("scissors", _action_icon, 16))
                    trim_btn.setIconSize(QSize(16, 16))
                    trim_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                    _trim_tip = ("Trim — click two points on the line to cut "
                                 "the section between them (e.g. an access "
                                 "road through the hedge)")
                    trim_btn.setToolTip(_trim_tip)
                    self._wire_hint(trim_btn, "Trim a section out")
                    trim_btn.setStyleSheet(_action_qss)
                    trim_btn.clicked.connect(
                        lambda _checked, l=layer, f=fid, k=key:
                            self._arm_hedge_trimmer(l, f, k)
                    )
                    rl.addWidget(trim_btn)

                edit_btn = QPushButton()
                edit_btn.setFixedSize(28, 28)
                edit_btn.setIcon(_svg_icon("pencil", _action_icon, 16))
                edit_btn.setIconSize(QSize(16, 16))
                edit_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                edit_btn.setToolTip("Edit this feature's habitat type, condition and other properties")
                self._wire_hint(edit_btn, "Edit properties")
                edit_btn.setStyleSheet(_action_qss)
                edit_btn.clicked.connect(
                    lambda _checked, l=layer, f=fid: _edit_feature(l, f)
                )
                if layer.name() not in ("Redline Boundary", "Blue Line Boundary"):
                    rl.addWidget(edit_btn)

                # Duplicate to the paired baseline<->proposed layer. Only shown for
                # habitat features that don't already have a twin (see
                # _dup_button_allowed): trees/hedges/water without a pair_id, and
                # all areas (guarded by the overlap check at duplicate time).
                if self._dup_button_allowed(layer, feat, key):
                    _dup_dir = "proposed" if key.startswith("baseline_") else "baseline"
                    _dup_tip = "Duplicate this feature into the %s layer" % _dup_dir
                    if key not in ("baseline_area", "proposed_area"):
                        _dup_tip += " as a retained twin"
                    dup_btn = QPushButton()
                    dup_btn.setFixedSize(28, 28)
                    dup_btn.setIcon(_svg_icon("copy", _action_icon, 16))
                    dup_btn.setIconSize(QSize(16, 16))
                    dup_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                    dup_btn.setToolTip(_dup_tip)
                    self._wire_hint(dup_btn, "Duplicate to %s" % _dup_dir)
                    dup_btn.setStyleSheet(_action_qss)
                    dup_btn.clicked.connect(
                        lambda _checked, l=layer, f=fid, k=key: self._duplicate_to_paired(l, f, k)
                    )
                    rl.addWidget(dup_btn)

                del_btn = QPushButton()
                del_btn.setFixedSize(28, 28)
                del_btn.setIcon(_svg_icon("trash", "#8F2A23", 16))
                del_btn.setIconSize(QSize(16, 16))
                del_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                del_btn.setToolTip("Permanently delete this feature from the map")
                self._wire_hint(del_btn, "Delete feature")
                del_btn.setStyleSheet(
                    "QPushButton { background:#FBEDEB; border:none; border-radius:6px;"
                    "font-size:13px; }"
                    "QPushButton:hover { background:#F4CFCB; }"
                )
                del_btn.clicked.connect(
                    lambda _checked, l=layer, f=fid, r=row: _delete_feature(l, f, r)
                )
                rl.addWidget(del_btn)

                vl_row.addLayout(rl)
                row.setLayout(vl_row)
                _fold_target(sub_val).addWidget(row)

        # BNG habitat layers
        for key, section_title in ALL_KEYS:
            layer = self.layer_manager.get_layer(key)
            if layer is None or not layer.isValid():
                continue
            feats = list(layer.getFeatures())
            if not feats:
                continue
            _add_section(section_title, layer, feats, key=key)
            if key == "redline":
                _add_redline_islands(layer)

        # Eco feature layers — fetch ALL features regardless of subset string
        # (subset is used for visibility toggling; we always show all in the list)
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            if lyr.name() not in ECO_NAMES:
                continue
            lyr.reload()
            saved_subset = lyr.subsetString()
            if saved_subset:
                lyr.setSubsetString("")
            feats = list(lyr.getFeatures())
            if saved_subset:
                lyr.setSubsetString(saved_subset)
            if not feats:
                continue
            _add_section(lyr.name(), lyr, feats)

        if not any_features:
            empty_w = QWidget()
            empty_w.setStyleSheet("background:transparent;")
            ev = QVBoxLayout()
            ev.setContentsMargins(20, 40, 20, 20)
            ev.setSpacing(12)
            icon = QLabel()
            icon.setPixmap(_svg_pixmap("map", theme.ACCENT, 40))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet("background:transparent;")
            ev.addWidget(icon)
            title = QLabel("No features placed yet")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet("font-size:13px; font-weight:500; color:#1F4A37; background:transparent;")
            ev.addWidget(title)
            steps = QLabel(
                "To get started:\n"
                "1.  Save your project (Project → Save As)\n"
                "2.  Draw a Red Line Boundary on the home screen\n"
                "3.  Open Baseline Habitats and draw your first feature\n"
                "4.  Come back here to edit or manage placed features"
            )
            steps.setAlignment(Qt.AlignmentFlag.AlignLeft)
            steps.setWordWrap(True)
            steps.setStyleSheet(
                "font-size:11px; color:#54635A; background:#F1F4F0;"
                " border-radius:8px; padding:12px 16px; line-height:1.6;"
            )
            ev.addWidget(steps)
            ev.addStretch()
            empty_w.setLayout(ev)
            layout.addWidget(empty_w)

        layout.addStretch()

        # ── Deleted Feature Bin ───────────────────────────────────────
        if self._deleted_bin:
            layout.addWidget(_banner("trash",
                "Deleted Features  (session only — not saved)",
                fg="#8F2A23", bg="transparent", font_px=9, weight="500",
                size=13, margins=(2, 10, 2, 2), spacing=6))

            bin_div = QFrame()
            bin_div.setFrameShape(QFrame.Shape.HLine)
            bin_div.setFixedHeight(1)
            bin_div.setStyleSheet("background:#F4CFCB; border:none;")
            layout.addWidget(bin_div)

            for i, entry in enumerate(self._deleted_bin):
                bin_row = QWidget()
                _bin_css_normal = (
                    "background:#fff8f8; border:1px solid #F4CFCB; border-radius:5px;"
                )
                _bin_css_hover = (
                    "background:#FBE3E1; border:1px solid #EBB3AD; border-radius:5px;"
                )
                bin_row.setStyleSheet(_bin_css_normal)
                brl = QHBoxLayout(bin_row)
                brl.setContentsMargins(10, 5, 8, 5)
                brl.setSpacing(6)

                # Hover highlight — show ghost outline of where feature would be
                # restored, and darken this row's box so it's clear which one.
                def _make_bin_enter(geom_wkt, row_widget, hover_css):
                    def _enter(event):
                        from qgis.core import QgsGeometry, QgsFeature, QgsVectorLayer, QgsProject
                        row_widget.setStyleSheet(hover_css)
                        if self._hover_highlight is not None:
                            self._hover_highlight.hide()
                            self._hover_highlight = None
                        if not geom_wkt:
                            return
                        geom = QgsGeometry.fromWkt(geom_wkt)
                        if not geom or geom.isNull():
                            return
                        # QgsHighlight requires a feature+layer — build a throwaway memory layer
                        wkb_type = geom.wkbType()
                        from qgis.core import QgsWkbTypes
                        type_str = QgsWkbTypes.displayString(wkb_type).split(" ")[0] or "Polygon"
                        crs_authid = self.iface.mapCanvas().mapSettings().destinationCrs().authid()
                        tmp = QgsVectorLayer(f"{type_str}?crs={crs_authid}", "_bin_preview", "memory")
                        feat = QgsFeature()
                        feat.setGeometry(geom)
                        tmp.dataProvider().addFeatures([feat])
                        feat = next(tmp.getFeatures())
                        h = QgsHighlight(self.iface.mapCanvas(), feat, tmp)
                        _style_hover_highlight(h, 183, 28, 28)   # restore-preview red
                        h.show()
                        self._hover_highlight = h
                    return _enter

                def _make_bin_leave(row_widget, normal_css):
                    def _leave(event):
                        row_widget.setStyleSheet(normal_css)
                        if self._hover_highlight is not None:
                            self._hover_highlight.hide()
                            self._hover_highlight = None
                    return _leave

                bin_row.enterEvent = _make_bin_enter(
                    entry.get("geom_wkt", ""), bin_row, _bin_css_hover
                )
                bin_row.leaveEvent = _make_bin_leave(bin_row, _bin_css_normal)

                # Label + source layer + time
                info = QVBoxLayout()
                info.setSpacing(1)
                name_lbl = QLabel(entry["label"])
                name_lbl.setStyleSheet(
                    "font-size:11px; color:#1A2420; font-weight:500;"
                    " border:none; background:transparent;"
                )
                name_lbl.setWordWrap(True)
                meta_lbl = QLabel(
                    f"{entry['layer_name']}  ·  deleted {entry['deleted_at']}"
                )
                meta_lbl.setStyleSheet(
                    "font-size:9px; color:#717E75; border:none; background:transparent;"
                )
                info.addWidget(name_lbl)
                info.addWidget(meta_lbl)
                brl.addLayout(info, 1)

                # Restore button
                restore_btn = QPushButton("↩  Restore")
                restore_btn.setFixedHeight(26)
                restore_btn.setFixedWidth(72)
                restore_btn.setToolTip("Restore this feature back to its original layer")
                restore_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                restore_btn.setStyleSheet(
                    "QPushButton { background:#E9F4EC; color:#175E3B;"
                    " border:1px solid #66bb6a; border-radius:5px;"
                    " font-size:10px; font-weight:500; }"
                    "QPushButton:hover { background:#D8ECDE; }"
                )
                restore_btn.clicked.connect(
                    lambda _checked, idx=i: self._restore_from_bin(idx)
                )
                brl.addWidget(restore_btn)

                # Permanently discard button
                discard_btn = QPushButton()
                discard_btn.setIcon(_svg_icon("x", "#8F2A23", 13))
                discard_btn.setIconSize(QSize(13, 13))
                discard_btn.setFixedSize(26, 26)
                discard_btn.setToolTip("Permanently discard — cannot be recovered")
                discard_btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
                discard_btn.setStyleSheet(
                    "QPushButton { background:#FBEDEB; color:#8F2A23;"
                    " border:none; border-radius:5px; font-size:11px; font-weight:500; }"
                    "QPushButton:hover { background:#F4CFCB; }"
                )
                def _make_discard(idx):
                    def _do():
                        self._deleted_bin.pop(idx)
                        self._open_manage_screen()
                    return _do
                discard_btn.clicked.connect(_make_discard(i))
                brl.addWidget(discard_btn)

                layout.addWidget(bin_row)

        container.setLayout(layout)
        scroll.setWidget(container)
        # Expose the per-feature editor so other UI (the Biome layer panel's
        # incomplete-features flyout) can open it directly for one feature.
        self._feature_editor = _edit_feature
        return scroll
