# earth3d_export.py
# Standalone-HTML "3D Spotlight" viewer builder for the Desk Study tab.
#
# Generates a self-contained HTML page that loads CesiumJS from CDN plus
# Google's Photorealistic 3D Tiles (the SAME photogrammetric mesh Google
# Earth uses, via the Map Tiles API) and shows the classic site-location
# figure. Always fully 3D (a 2D-island hybrid mode existed briefly; Tom
# scrapped it — the island visibly floated above the flat imagery):
#   - The FULL mesh streams (no clipping anywhere — clipping polygons are a
#     GPU distance-field texture that visibly rounds/erodes corners on small
#     sites). Fog = a classification POLYGON with the boundary as a hole,
#     draped onto the mesh; classification follows exact polygon geometry, so
#     corners stay crisp. Google 2D satellite tiles (same key; OSM fallback;
#     added AFTER Viewer construction — a broken baseLayer kills the whole
#     globe) sit under the mesh, washed with the same fog for the streaming-
#     in period.
#   - Boundary line: a draped classification polyline (clampToGround +
#     ClassificationType.BOTH) that PAINTS onto whatever surface lies under
#     it — Tom preferred that over a contour ribbon, which went jagged on
#     rough mesh. A refinement pass then repaints ONLY short stretches where
#     something floats above matching ground ends (overhanging tree,
#     sculpture) through a HEIGHT-LIMITED classification volume (extruded
#     corridor): the ground line continues unbroken beneath the object, the
#     object itself stays clean. Everything else stays draped and
#     pixel-identical. HUD: hide checkbox + width slider.
#
# MUST be opened over http://127.0.0.1 (the plugin serves it), NEVER file://:
# Chrome refuses workers on file:// pages and Cesium builds the globe surface
# in a worker — the globe renders as black space and takes fog + lines with
# it (debugged live, 2026-07-09).
#
# This module is deliberately QGIS-free: build_spotlight_html() takes plain
# WGS84 ring data, so it can be exercised with the system Python.
#
# Costs: CesiumJS is Apache-2.0 (no Cesium ion assets are requested). Google
# bills one Photorealistic-3D-Tiles root request per page open (1,000
# free/month) and 2D tiles against a 100k/month free tier.

import json

CESIUM_VERSION = "1.130"
# Shown in the HUD and logged to the console so a stale export (old plugin
# module still cached in a running QGIS) is immediately recognisable. Bump
# whenever the template changes.
BUILD_TAG = "r17"

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__ — 3D spotlight</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VERSION__/Build/Cesium/Cesium.js"></script>
<link href="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VERSION__/Build/Cesium/Widgets/widgets.css" rel="stylesheet">
<style>
  html, body, #cesiumContainer { width:100%; height:100%; margin:0; padding:0; overflow:hidden; }
  body { font-family: "Segoe UI", system-ui, sans-serif; background:#101418; }
  #hud {
    position:absolute; top:12px; left:12px; z-index:10;
    background:rgba(16,20,24,.82); color:#E8ECEA; border-radius:10px;
    padding:12px 14px; width:230px; backdrop-filter:blur(4px);
    box-shadow:0 2px 10px rgba(0,0,0,.35); font-size:12px;
  }
  #hud h1 { font-size:13px; margin:0 0 8px; font-weight:600; }
  #hud label { display:block; margin:8px 0 2px; color:#B7C0BC; }
  #hud input[type=range] { width:100%; }
  #hud .opt { display:inline-flex; align-items:center; gap:5px;
              margin:2px 12px 0 0; color:#E8ECEA; cursor:pointer; }
  #hud button {
    margin-top:10px; width:100%; padding:7px 0; border:0; border-radius:7px;
    background:#3D8361; color:#fff; font-weight:600; font-size:12px; cursor:pointer;
  }
  #hud button:hover { background:#4a9c74; }
  #hud .hint { margin-top:8px; color:#8A968E; font-size:11px; line-height:1.35; }
  #err {
    display:none; position:absolute; top:0; left:0; right:0; z-index:20;
    background:#7a2b2b; color:#fff; padding:10px 16px; font-size:13px;
  }
  #loading {
    position:absolute; inset:0; z-index:5; display:flex; align-items:center;
    justify-content:center; color:#B7C0BC; font-size:14px; pointer-events:none;
  }
</style>
</head>
<body>
<div id="cesiumContainer"></div>
<div id="err"></div>
<div id="loading">Loading Google 3D tiles…</div>
<div id="hud">
  <h1>__TITLE__ — 3D spotlight</h1>
  <div id="bndRow" style="display:none">
    <label>Boundary</label>
    <label class="opt"><input type="radio" name="bndSel" value="red" checked>
      Red line</label>
    <label class="opt"><input type="radio" name="bndSel" value="blue">
      Blue line</label>
  </div>
  <label>Fog strength <span id="fogPct"></span></label>
  <input type="range" id="fogAmt" min="0" max="90" value="60">
  <label>Fog brightness</label>
  <input type="range" id="fogTone" min="0" max="100" value="88">
  <label class="opt" style="margin-top:10px">
    <input type="checkbox" id="bndShow" checked> Show boundary line</label>
  <label>Boundary width</label>
  <input type="range" id="bndWidth" min="1" max="10" step="0.5" value="3">
  <button id="shotBtn">Save screenshot (PNG)</button>
  <div class="hint">Drag to orbit &middot; right-drag / Ctrl-drag to tilt &middot;
  scroll to zoom. Give the 3D mesh ~20 s to stream in fully.
  <span style="opacity:.55">&middot; build __BUILD__</span></div>
</div>
<script>
"use strict";
console.log("Biome 3D Spotlight build __BUILD__");
const RED_RINGS = __RINGS_JSON__;        // [[[lon,lat], ...], ...] WGS84, unclosed
const BLUE_RINGS = __BLUE_RINGS_JSON__;  // blue line boundary, [] if none
const API_KEY = __API_KEY_JSON__;

let activeRings = RED_RINGS;
let boundaryColor = "#FF1F1F";

const errBox = document.getElementById("err");
function showError(msg) {
  errBox.style.display = "block";
  errBox.textContent = msg;
  hideLoading();
}
const KEY_HINT = " — check the API key is valid, has the Map Tiles API " +
  "enabled (Google Cloud Console \\u2192 APIs) and billing is active on its project.";
window.addEventListener("unhandledrejection", e =>
  showError("Google 3D tiles failed to load: " +
            (e.reason && e.reason.message || e.reason) + KEY_HINT));

Cesium.GoogleMaps.defaultApiKey = API_KEY;

const viewer = new Cesium.Viewer("cesiumContainer", {
  baseLayer: false,   // imagery added async below — a broken base layer at
                      // construction kills the whole globe (black space)
  baseLayerPicker: false, geocoder: false, homeButton: false,
  sceneModePicker: false, navigationHelpButton: false, timeline: false,
  animation: false, fullscreenButton: false, infoBox: false,
  selectionIndicator: false, requestRenderMode: false,
  contextOptions: { webgl: { preserveDrawingBuffer: true } },  // for screenshots
});
viewer.scene.skyAtmosphere.show = true;
// Whatever happens to imagery, the globe must render — it carries the 2D
// context and anchors the scene.
viewer.scene.globe.show = true;
viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#D8DCDE");

// Flat 2D context imagery: Google's own satellite tiles (2D Map Tiles are in
// the same Map Tiles API as the 3D tiles, 100k free/month), so the fogged
// surroundings match the mesh island's textures. OSM as fallback.
async function addBaseImagery() {
  let provider = null;
  try {
    const r = await fetch(
      "https://tile.googleapis.com/v1/createSession?key=" + API_KEY, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mapType: "satellite" }),
      });
    if (!r.ok) throw new Error("2D tile session: HTTP " + r.status);
    const s = await r.json();
    provider = new Cesium.UrlTemplateImageryProvider({
      url: "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}?session=" +
           encodeURIComponent(s.session) + "&key=" + API_KEY,
      maximumLevel: 22,
      credit: new Cesium.Credit("Google"),
    });
  } catch (e) {
    try {
      provider = new Cesium.OpenStreetMapImageryProvider({
        url: "https://tile.openstreetmap.org/" });
    } catch (e2) { provider = null; }
  }
  if (provider) {
    viewer.imageryLayers.addImageryProvider(provider);
    applyFog();
  }
}

let userMoved = false;
for (const evt of ["pointerdown", "wheel"])
  viewer.canvas.addEventListener(evt, () => { userMoved = true; });

function boundsOf(rings) {
  let w = 180, e = -180, s = 90, n = -90;
  for (const ring of rings) for (const [lon, lat] of ring) {
    w = Math.min(w, lon); e = Math.max(e, lon);
    s = Math.min(s, lat); n = Math.max(n, lat);
  }
  return { w, e, s, n, cLon: (w + e) / 2, cLat: (s + n) / 2 };
}

// Straight-down framing. groundH is the ellipsoidal height of the site
// ground — 0 until the mesh answers a height query.
function frameSite(groundH, extraMargin) {
  const b = boundsOf(activeRings);
  const spanM = Math.max(
    (b.e - b.w) * 111320 * Math.cos(b.cLat * Math.PI / 180),
    (b.n - b.s) * 111320, 40);
  const h = groundH + Math.max(spanM * 1.7, 130) + (extraMargin || 0);
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(b.cLon, b.cLat, h),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-89.9), roll: 0 },
    duration: 0,
  });
}

let siteTileset = null;
let fogEntity = null;
let meshGroundH = null;   // sampled site ground height (ellipsoidal)

// Fog: a classification polygon draped over the mesh, covering a wide area
// around the site with the boundary cut out as holes. Follows the exact
// polygon geometry (clipping polygons round off corners). NOTE a Ctrl-click
// "clear fog patch" tool existed briefly (r12) and was scrapped: the fog
// curtain is vertical, so a patch big enough to free an overhanging
// structure also un-fogs the ground beneath it — worse than the fogged
// corner it fixed.
function fogHierarchy() {
  const all = BLUE_RINGS.length ? RED_RINGS.concat(BLUE_RINGS) : RED_RINGS;
  const b = boundsOf(all);
  const M = 0.1;   // ~11 km of fogged context around the site
  const outer = Cesium.Cartesian3.fromDegreesArray([
    b.w - M, b.s - M, b.e + M, b.s - M, b.e + M, b.n + M, b.w - M, b.n + M]);
  const holes = activeRings.map(r => new Cesium.PolygonHierarchy(
    Cesium.Cartesian3.fromDegreesArray(r.flat())));
  return new Cesium.PolygonHierarchy(outer, holes);
}

async function start() {
  addBaseImagery();   // deliberately not awaited — streams in parallel

  siteTileset = await Cesium.createGooglePhotorealistic3DTileset();
  viewer.scene.primitives.add(siteTileset);

  fogEntity = viewer.entities.add({
    polygon: {
      hierarchy: fogHierarchy(),
      material: Cesium.Color.WHITE.withAlpha(0.6),
      classificationType: Cesium.ClassificationType.BOTH,
    },
  });
  applyFog();

  frameSite(0, 200);   // provisional height margin until the mesh answers
  drawBoundary();
  sampleGroundForFraming(0);
  siteTileset.initialTilesLoaded && siteTileset.initialTilesLoaded.addEventListener &&
    siteTileset.initialTilesLoaded.addEventListener(hideLoading);
  setTimeout(hideLoading, 12000);
}
function hideLoading() { document.getElementById("loading").style.display = "none"; }

// ---- boundary line ---------------------------------------------------------
// The boundary PAINTS onto whatever surface lies under it (draped
// classification lines; need the globe pass — reliable now that exports
// always open over 127.0.0.1; on file:// the globe is dead, gotcha 7).
// Refinement pass (Tom: "fix the small bits, don't average the whole
// thing"): sample heights along the ring; short stretches that jump well
// above the local ground level (overhanging tree, the sculpture) are
// re-drawn as POSITIONED segments at ground height interpolated from their
// neighbours — the ground stays marked, the object above doesn't. Everywhere
// else stays draped, pixel-identical. If sampling is inconclusive the full
// draped ring stays as-is.
const bndShow = document.getElementById("bndShow");
const bndWidth = document.getElementById("bndWidth");
const boundaryEntities = [];
let bndGen = 0;

function densify(ring, stepM) {
  const out = [];
  const closed = ring.concat([ring[0]]);
  for (let i = 0; i < closed.length - 1; i++) {
    const [x1, y1] = closed[i], [x2, y2] = closed[i + 1];
    const latScale = 111320, lonScale = 111320 * Math.cos(y1 * Math.PI / 180);
    const len = Math.hypot((x2 - x1) * lonScale, (y2 - y1) * latScale);
    const nSeg = Math.max(1, Math.ceil(len / stepM));
    for (let k = 0; k < nSeg; k++)
      out.push([x1 + (x2 - x1) * k / nSeg, y1 + (y2 - y1) * k / nSeg]);
  }
  return out;   // open ring: no duplicate closing point
}

function _addDraped(pts) {
  boundaryEntities.push(viewer.entities.add({
    show: bndShow.checked,
    polyline: {
      positions: Cesium.Cartesian3.fromDegreesArray(pts.flat()),
      width: parseFloat(bndWidth.value),
      material: Cesium.Color.fromCssColorString(boundaryColor),
      clampToGround: true,
      classificationType: Cesium.ClassificationType.BOTH,
    },
  }));
}
// A flagged span still paints THROUGH a height-limited classification
// volume: the ground below the floating object keeps its continuous line
// (the drape already painted it correctly — Tom), while anything above the
// band (cube, cables, canopy) stays clean. Width is metres, matched
// approximately to the pixel slider at typical figure framing.
const boundaryPrims = [];
let lastSpans = [];
// Metre width that visually matches the draped line's pixel width at the
// camera's current distance from the site.
function metresPerPixel() {
  const b = boundsOf(activeRings);
  const centre = Cesium.Cartesian3.fromDegrees(b.cLon, b.cLat, meshGroundH || 0);
  const dist = Math.max(Cesium.Cartesian3.distance(viewer.camera.position, centre), 20);
  const fovy = (viewer.camera.frustum && viewer.camera.frustum.fovy) || 1.05;
  return 2 * dist * Math.tan(fovy / 2) / viewer.canvas.clientHeight;
}
function _addSpanPrim(span) {
  const width = Math.max(parseFloat(bndWidth.value) * metresPerPixel(), 0.15);
  const prim = viewer.scene.primitives.add(new Cesium.ClassificationPrimitive({
    geometryInstances: new Cesium.GeometryInstance({
      geometry: new Cesium.CorridorGeometry({
        positions: Cesium.Cartesian3.fromDegreesArray(span.pts.flat()),
        width,
        height: span.lo,
        extrudedHeight: span.hi,
        vertexFormat: Cesium.PerInstanceColorAppearance.VERTEX_FORMAT,
      }),
      attributes: {
        color: Cesium.ColorGeometryInstanceAttribute.fromColor(
          Cesium.Color.fromCssColorString(boundaryColor)),
      },
    }),
    classificationType: Cesium.ClassificationType.BOTH,
  }));
  prim.show = bndShow.checked;
  boundaryPrims.push(prim);
}
function _clearSpanPrims() {
  boundaryPrims.splice(0).forEach(p => viewer.scene.primitives.remove(p));
}

function drawBoundary() {
  bndGen++;
  boundaryEntities.splice(0).forEach(en => viewer.entities.remove(en));
  _clearSpanPrims();
  lastSpans = [];
  for (const ring of activeRings)
    _addDraped(ring.concat([ring[0]]));
  refineBoundary(bndGen, 0);
}
bndShow.addEventListener("change", () => {
  boundaryEntities.forEach(en => { en.show = bndShow.checked; });
  boundaryPrims.forEach(p => { p.show = bndShow.checked; });
});
bndWidth.addEventListener("input", () => {
  boundaryEntities.forEach(en => { en.polyline.width = parseFloat(bndWidth.value); });
  _clearSpanPrims();
  lastSpans.forEach(_addSpanPrim);
});
// Keep span widths matched to the pixel line as the camera zooms.
viewer.camera.moveEnd.addEventListener(() => {
  if (lastSpans.length) { _clearSpanPrims(); lastSpans.forEach(_addSpanPrim); }
});

async function refineBoundary(g, attempt) {
  const dense = activeRings.map(r => densify(r, 2.5));
  let heights = [];
  try {
    const carts = dense.flat().map(
      ([lon, lat]) => Cesium.Cartesian3.fromDegrees(lon, lat, 0));
    const res = await viewer.scene.clampToHeightMostDetailed(carts);
    heights = res.map(c => c ? Cesium.Cartographic.fromCartesian(c).height : null);
  } catch (e) { heights = []; }
  if (g !== bndGen) return;   // boundary changed while sampling

  const sane = heights.filter(h => h !== null && h > -200 && h < 5000)
    .sort((a, b) => a - b);
  const med = sane.length ? sane[Math.floor(sane.length / 2)] : null;
  const usable = med === null ? 0 : sane.filter(h => Math.abs(h - med) <= 150).length;
  const total = dense.reduce((s, pts) => s + pts.length, 0);
  if (!usable || usable < total * 0.7) {
    // Not enough of the mesh has streamed to judge — keep the draped ring.
    if (attempt < 12)
      setTimeout(() => { if (g === bndGen) refineBoundary(g, attempt + 1); }, 3000);
    return;
  }
  const ok = h => h !== null && h > -200 && h < 5000 && Math.abs(h - med) <= 150;

  const JUMP = 1.8;     // metres above the bracket ends = overhang (2.5
                        // missed a low canopy edge clipping the boundary)
  const segments = [];  // {draped: [...ptsLL]} or {span: {pts, lo, hi}}
  let offset = 0;
  for (const pts of dense) {
    const n = pts.length;
    const raw = heights.slice(offset, offset + n).map(h => ok(h) ? h : null);
    offset += n;
    // Fill nulls by wrapping linear interpolation for the ground estimate.
    const filled = raw.slice();
    if (filled.every(h => h === null)) { segments.push({ draped: pts.concat([pts[0]]) }); continue; }
    for (let i = 0; i < n; i++) {
      if (filled[i] !== null) continue;
      let a = i, b = i;
      while (filled[(a + n) % n] === null) a--;
      while (filled[b % n] === null) b++;
      const ha = filled[(a + n) % n], hb = filled[b % n];
      for (let j = a + 1; j < b; j++)
        filled[(j + n) % n] = ha + (hb - ha) * (j - a) / (b - a);
    }
    // Spike detector (v3): flag short runs bracketed by two points at
    // MATCHING ground level where ANYTHING between jumps above them. v2
    // required the elevated stretch to be continuous, which missed floating
    // structures (sculpture: cube / ground-between-legs / hanging box
    // alternate rapidly). The whole bracket gets replaced by a lerp between
    // the end heights, so interior dips don't matter. Monotonic slopes
    // never re-descend to a matching end, so they still can't be flagged
    // (v1's windowed threshold ate genuine banks); long features exceed
    // MAXRUN and stay draped.
    const TOL = 1.2;     // m: how closely the two ends must agree
    const MAXRUN = 10;   // points (~25 m): longest fixable span
    const flag = new Array(n).fill(false);
    for (let a = 0; a < n; a++) {
      const ha = filled[a];
      for (let len = 2; len <= MAXRUN + 1; len++) {
        const hb = filled[(a + len) % n];
        if (Math.abs(hb - ha) > TOL) continue;
        const base = Math.max(ha, hb);
        let anyHigh = false;
        for (let j = a + 1; j < a + len; j++)
          if (filled[j % n] > base + JUMP) { anyHigh = true; break; }
        if (anyHigh)
          for (let j = a + 1; j < a + len; j++) flag[j % n] = true;
        break;   // tightest matching bracket from this start decides
      }
    }
    const nFlag = flag.filter(Boolean).length;
    if (nFlag === 0 || nFlag > n * 0.5) {
      // Nothing to fix, or too much flagged to trust — keep it draped.
      segments.push({ draped: pts.concat([pts[0]]) });
      continue;
    }
    // Walk the ring from an unflagged start, cutting it into runs.
    let s = flag.indexOf(false);
    let run = [s], runFlag = false;
    const flush = () => {
      if (run.length < 2) return;
      if (!runFlag) {
        segments.push({ draped: run.map(i => pts[i % n]) });
      } else {
        // Endpoints are the unflagged bracket ends — real ground heights.
        // Paint band: comfortably below ground level up to just above it,
        // so the ground line continues but the object overhead stays clean.
        const ga = filled[run[0] % n], gb = filled[run[run.length - 1] % n];
        segments.push({ span: {
          pts: run.map(i => pts[i % n]),
          lo: Math.min(ga, gb) - 5,
          hi: Math.max(ga, gb) + 1.2,
        } });
      }
    };
    for (let i = s + 1; i <= s + n; i++) {
      const f = flag[i % n];
      if (f === runFlag) { run.push(i); continue; }
      run.push(i);    // share the junction point so segments connect
      flush();
      run = [i]; runFlag = f;
    }
    flush();
  }

  if (g !== bndGen) return;
  boundaryEntities.splice(0).forEach(en => viewer.entities.remove(en));
  _clearSpanPrims();
  lastSpans = [];
  for (const seg of segments) {
    if (seg.draped) _addDraped(seg.draped);
    else { lastSpans.push(seg.span); _addSpanPrim(seg.span); }
  }
  if (usable < total && attempt < 12)
    setTimeout(() => { if (g === bndGen) refineBoundary(g, attempt + 1); }, 3000);
}

// Ground height is still sampled once — purely to frame the camera at the
// right altitude (ellipsoidal heights sit ~50 m up in the UK).
async function sampleGroundForFraming(attempt) {
  const pts = [];
  for (const ring of RED_RINGS) {
    let cx = 0, cy = 0;
    ring.forEach(([x, y]) => { cx += x; cy += y; });
    cx /= ring.length; cy /= ring.length;
    pts.push([cx, cy]);
    ring.forEach(([x, y]) => pts.push([cx + (x - cx) * 0.85, cy + (y - cy) * 0.85]));
  }
  let heights = [];
  try {
    const carts = pts.map(([lon, lat]) => Cesium.Cartesian3.fromDegrees(lon, lat, 0));
    const res = await viewer.scene.clampToHeightMostDetailed(carts);
    heights = res.filter(c => c)
      .map(c => Cesium.Cartographic.fromCartesian(c).height)
      .filter(h => h > -200 && h < 5000).sort((a, b) => a - b);
  } catch (e) { /* retry below */ }
  // Reject deep-garbage outliers (observed: -23787 m) around the median.
  const med = heights.length ? heights[Math.floor(heights.length / 2)] : null;
  const valid = med === null ? [] : heights.filter(h => Math.abs(h - med) <= 150);
  if (!valid.length) {
    if (attempt < 12) setTimeout(() => sampleGroundForFraming(attempt + 1), 3000);
    return;
  }
  meshGroundH = valid[Math.floor(valid.length * 0.05)];
  if (!userMoved) frameSite(meshGroundH, 0);
}

// ---- fog controls -------------------------------------------------------------
const fogAmt = document.getElementById("fogAmt");
const fogTone = document.getElementById("fogTone");
const fogPct = document.getElementById("fogPct");
function applyFog() {
  const amt = fogAmt.value / 100;
  const tone = 0.55 + 0.45 * (fogTone.value / 100);   // grey → near-white
  fogPct.textContent = fogAmt.value + "%";
  const imagery = viewer.imageryLayers.length ? viewer.imageryLayers.get(0) : null;
  if (imagery) {   // 2D context fog: wash the imagery itself towards milky
    imagery.saturation = 1 - amt;
    imagery.brightness = 1 + amt * (tone * 1.6 - 0.45);
    imagery.contrast = 1 - amt * 0.5;
  }
  if (fogEntity) { // 3D context fog: translucent drape over the mesh
    fogEntity.polygon.material = new Cesium.ColorMaterialProperty(
      new Cesium.Color(tone, tone, tone * 0.993, amt));
  }
}
fogAmt.addEventListener("input", applyFog);
fogTone.addEventListener("input", applyFog);
applyFog();

// ---- red line / blue line switch (only shown when a blue line exists) --------
if (BLUE_RINGS.length) {
  document.getElementById("bndRow").style.display = "block";
  for (const r of document.querySelectorAll("input[name=bndSel]"))
    r.addEventListener("change", () => setBoundary(r.value));
}
function setBoundary(kind) {
  activeRings = (kind === "blue" && BLUE_RINGS.length) ? BLUE_RINGS : RED_RINGS;
  boundaryColor = (kind === "blue") ? "#1F6FFF" : "#FF1F1F";
  if (fogEntity) fogEntity.polygon.hierarchy = fogHierarchy();
  drawBoundary();
  if (!userMoved) frameSite(meshGroundH || 0, 0);
}

// ---- screenshot ----------------------------------------------------------------
document.getElementById("shotBtn").addEventListener("click", () => {
  viewer.render();
  viewer.canvas.toBlob(blob => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "site_3d_spotlight.png";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  });
});

start().catch(e =>
  showError("Google 3D tiles failed to load: " + (e && e.message || e) + KEY_HINT));
</script>
</body>
</html>
"""


def _clean_rings(rings):
    """Normalise rings to [[lon, lat], ...] lists — floats rounded, closing
    duplicate stripped (ClippingPolygon wants open rings), degenerates
    dropped."""
    clean = []
    for ring in rings or []:
        pts = [[round(float(x), 7), round(float(y), 7)] for x, y in ring]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) >= 3:
            clean.append(pts)
    return clean


def build_spotlight_html(rings, api_key, title="Site", blue_rings=None):
    """Return the self-contained 3D-spotlight HTML page.

    rings: redline exterior rings, each [(lon, lat), ...] in WGS84.
    blue_rings: optional Blue Line Boundary rings — when given, the viewer
    shows a red-line/blue-line switch that swaps both the spotlight clip and
    the drawn boundary; without them the switch stays hidden.
    api_key: Google Maps Platform key with the Map Tiles API enabled.

    NOTE the key is embedded in the page — the file is meant to stay local
    (or the key referrer-restricted); the caller should say so in its UI.
    """
    clean = _clean_rings(rings)
    if not clean:
        raise ValueError("no usable rings")
    blue = _clean_rings(blue_rings)
    # str.replace, not .format(): the template is full of JS/CSS braces.
    return (_TEMPLATE
            .replace("__CESIUM_VERSION__", CESIUM_VERSION)
            .replace("__BUILD__", BUILD_TAG)
            .replace("__TITLE__", (title or "Site").replace("<", "&lt;"))
            .replace("__RINGS_JSON__", json.dumps(clean, separators=(",", ":")))
            .replace("__BLUE_RINGS_JSON__", json.dumps(blue, separators=(",", ":")))
            .replace("__API_KEY_JSON__", json.dumps(api_key or "")))
