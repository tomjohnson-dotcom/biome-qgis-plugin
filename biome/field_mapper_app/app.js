/* Biome Field Mapper — application.
 *
 * Runs entirely from the BIOME payload built by field_mapper_data.py, so the
 * catalogue, habitat taxonomy, condition sheets and HSI indices are whatever
 * the plugin currently defines. Nothing is hardcoded here.
 *
 * Structure:
 *   1  icons + toast + sheet stack     6  drawing (line / area)
 *   2  map + basemaps + GPS            7  condition assessment
 *   3  records model + persistence     8  GCN HSI
 *   4  the Add flow                    9  records browser
 *   5  placement                      10  finish / export / settings
 */

/* ══ 1. Chrome ═══════════════════════════════════════════════════════════ */

function icon(name, size) {
  const inner = BIOME.icons[name] || '';
  const s = size || 24;
  return `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
}

function fillIcon(elId, name) {
  const el = document.getElementById(elId);
  if (el) el.innerHTML = BIOME.icons[name] || '';
}

let _toastTimer = null;
function toast(msg, opts) {
  opts = opts || {};
  const t = document.getElementById('toast');
  t.querySelector('.t-msg').textContent = msg;
  const btn = document.getElementById('toast-action');
  if (opts.action) {
    btn.textContent = opts.action;
    btn.style.display = '';
    btn.onclick = () => { hideToast(); opts.onAction && opts.onAction(); };
  } else {
    btn.style.display = 'none';
    btn.onclick = null;
  }
  t.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(hideToast, opts.ms || (opts.action ? 6000 : 2600));
}
function hideToast() { document.getElementById('toast').classList.remove('show'); }

/* Haptic confirmation — on a phone held at arm's length over a hedge, the
 * buzz lands before the eye finds the toast. */
function buzz(ms) { try { navigator.vibrate && navigator.vibrate(ms || 18); } catch (e) {} }

/* ── Sheet stack ───────────────────────────────────────────────────────────
 * One sheet element, a stack of views. Back returns to the previous view
 * rather than closing, which is what makes drilling into a 131-habitat list
 * feel navigable instead of modal.
 */
const Sheet = (() => {
  const el       = document.getElementById('sheet');
  const backdrop = document.getElementById('backdrop');
  const body     = document.getElementById('sheet-body');
  const foot     = document.getElementById('sheet-foot');
  const searchW  = document.getElementById('sheet-search');
  const searchI  = document.getElementById('sheet-search-input');
  const searchC  = document.getElementById('sheet-search-clear');
  const chipRow  = document.getElementById('sheet-chips');
  let stack = [];

  function current() { return stack[stack.length - 1]; }

  function render() {
    const v = current();
    if (!v) return;
    document.getElementById('sheet-title').textContent = v.title || '';
    document.getElementById('sheet-sub').textContent = v.sub || '';
    document.getElementById('sheet-back').classList.toggle('show', stack.length > 1);

    if (v.search) {
      searchW.style.display = '';
      searchI.placeholder = v.search;
      if (v._q === undefined) v._q = '';
      searchI.value = v._q;
      searchC.classList.toggle('show', !!v._q);
    } else {
      searchW.style.display = 'none';
    }

    if (v.chips && v.chips.length) {
      chipRow.style.display = '';
      chipRow.innerHTML = v.chips.map((c, i) =>
        `<button class="chip${i === (v._chip || 0) ? ' active' : ''}" data-i="${i}">${esc(c.label)}</button>`
      ).join('');
      chipRow.querySelectorAll('.chip').forEach(b => b.onclick = () => {
        v._chip = +b.dataset.i;
        chipRow.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        paint();
      });
    } else {
      chipRow.style.display = 'none';
    }

    if (v.foot) { foot.style.display = ''; foot.innerHTML = ''; v.foot(foot); }
    else foot.style.display = 'none';

    paint();
  }

  function paint() {
    const v = current();
    if (!v) return;
    body.innerHTML = '';
    v.render(body, v._q || '', v._chip || 0);
    body.scrollTop = v._scroll || 0;
  }

  function push(view) {
    if (stack.length) { current()._scroll = body.scrollTop; }
    stack.push(view);
    render();
    open();
  }

  function replace(view) { stack.pop(); push(view); }

  function back() {
    if (stack.length <= 1) { close(); return; }
    stack.pop();
    render();
  }

  function open() {
    backdrop.classList.add('show');
    el.classList.add('open');
  }

  function close() {
    el.classList.remove('open');
    backdrop.classList.remove('show');
    stack = [];
    setActiveTab('map');
  }

  function repaint() { if (stack.length) paint(); }
  function isOpen() { return stack.length > 0; }

  searchI.addEventListener('input', () => {
    const v = current();
    if (!v) return;
    v._q = searchI.value;
    searchC.classList.toggle('show', !!v._q);
    paint();
  });
  searchC.onclick = () => { searchI.value = ''; searchI.dispatchEvent(new Event('input')); searchI.focus(); };
  document.getElementById('sheet-back').onclick = back;
  document.getElementById('sheet-close').onclick = close;
  backdrop.onclick = close;

  return {push, replace, back, close, repaint, isOpen, current};
})();

function setActiveTab(name) {
  ['map', 'records', 'export', 'more'].forEach(t => {
    const b = document.getElementById('tab-' + t);
    if (b) b.classList.toggle('active', t === name);
  });
}

/* Build a standard list row. */
function rowHTML(o) {
  const iconHTML = o.svg
    ? `<span class="row-icon">${o.svg}</span>`
    : o.swatch
      ? `<span class="row-icon"><span class="swatch${o.swatchLine ? ' line' : ''}"
           style="${o.swatchLine ? 'border-top-color:' + o.swatch : 'background:' + o.swatch}"></span></span>`
      : `<span class="row-icon">${icon(o.icon || 'map-pin', 19)}</span>`;
  const tail = (o.pill ? `<span class="pill ${o.pillClass || ''}">${esc(o.pill)}</span>` : '') +
               (o.chevron === false ? '' : icon('chevron-right', 17));
  return `${iconHTML}<span class="row-main">
      <span class="row-name">${esc(o.name)}</span>
      ${o.sub ? `<span class="row-sub">${esc(o.sub)}</span>` : ''}
    </span><span class="row-tail">${tail}</span>`;
}

function makeRow(o, onClick) {
  const b = document.createElement('button');
  b.className = 'row';
  b.innerHTML = rowHTML(o);
  b.onclick = onClick;
  return b;
}

function groupHeader(text) {
  const d = document.createElement('div');
  d.className = 'group-hdr';
  d.textContent = text;
  return d;
}

function emptyState(container, iconName, title, msg) {
  container.innerHTML = `<div class="empty">${icon(iconName, 44)}
    <h3>${esc(title)}</h3><p>${esc(msg)}</p></div>`;
}


/* ══ 2. Map ══════════════════════════════════════════════════════════════ */

const SITE = BIOME.site || {};

const BASEMAPS = [
  {id: 'sat', name: 'Satellite',
   url: 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', max: 21, attr: 'Google'},
  {id: 'hybrid', name: 'Satellite + labels',
   url: 'https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', max: 21, attr: 'Google'},
  {id: 'esri', name: 'Esri imagery',
   url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
   max: 19, attr: 'Esri, Maxar, Earthstar Geographics'},
  {id: 'osm', name: 'OpenStreetMap',
   url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', max: 19, attr: '© OpenStreetMap contributors'},
  {id: 'topo', name: 'Topographic',
   url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', max: 17, attr: '© OpenTopoMap, © OpenStreetMap contributors'},
];

const map = L.map('map', {zoomControl: false, attributionControl: true});
L.control.attribution({prefix: false}).addTo(map);

let baseLayer = null;
function setBasemap(id) {
  const spec = BASEMAPS.find(b => b.id === id) || BASEMAPS[0];
  if (baseLayer) map.removeLayer(baseLayer);
  baseLayer = L.tileLayer(spec.url, {maxZoom: spec.max, maxNativeZoom: spec.max,
                                     attribution: spec.attr, crossOrigin: true});
  baseLayer.addTo(map);
  baseLayer.bringToBack();
  Store.meta('basemap', id);
}

const redlineLayer = L.geoJSON(SITE.redline || {type: 'FeatureCollection', features: []}, {
  style: () => ({color: getCSS('--redline'), weight: 3, fill: true,
                 fillColor: getCSS('--redline'), fillOpacity: 0.06, dashArray: null}),
  interactive: false,
});
const featureGroup = L.featureGroup().addTo(map);

function getCSS(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}

/* ── Themes ────────────────────────────────────────────────────────────── */
const THEMES = [
  {id: 'dark',  name: 'Dark',      sub: 'Default — easy on the eyes at dusk'},
  {id: 'day',   name: 'Daylight',  sub: 'High contrast for bright sun'},
  {id: 'night', name: 'Night (red)', sub: 'Preserves dark adaptation on bat surveys'},
];
function setTheme(id) {
  document.documentElement.setAttribute('data-theme', id);
  Store.meta('theme', id);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', getCSS('--header'));
  redlineLayer.setStyle({color: getCSS('--redline'), fillColor: getCSS('--redline')});
  redrawAll();
}

/* ── GPS ───────────────────────────────────────────────────────────────── */
const GPS = {
  pos: null, acc: null, watchId: null, marker: null, circle: null,
  follow: false, heading: null,
};

function gpsClass(acc) {
  if (acc == null) return '';
  return acc <= 10 ? 'good' : acc <= 30 ? 'fair' : 'poor';
}

function updateGpsChip() {
  const chip = document.getElementById('gps-chip');
  const lbl  = document.getElementById('gps-acc');
  chip.classList.remove('good', 'fair', 'poor');
  if (GPS.acc == null) { lbl.textContent = 'GPS'; return; }
  chip.classList.add(gpsClass(GPS.acc));
  lbl.textContent = '±' + Math.round(GPS.acc) + ' m';
}

function startGps(onFirst) {
  if (!navigator.geolocation) { toast('This browser has no GPS'); return; }
  if (GPS.watchId != null) { if (onFirst && GPS.pos) onFirst(GPS.pos, GPS.acc); return; }
  document.getElementById('gps-acc').textContent = '…';
  let first = true;
  GPS.watchId = navigator.geolocation.watchPosition(p => {
    GPS.pos = [p.coords.latitude, p.coords.longitude];
    GPS.acc = p.coords.accuracy;
    if (p.coords.heading != null && !isNaN(p.coords.heading)) GPS.heading = p.coords.heading;
    drawGpsMarker();
    updateGpsChip();
    if (GPS.follow) map.panTo(GPS.pos, {animate: true});
    if (first) { first = false; if (onFirst) onFirst(GPS.pos, GPS.acc); }
  }, err => {
    GPS.watchId = null;
    document.getElementById('gps-acc').textContent = 'GPS';
    toast(err.code === 1 ? 'Location permission denied' : 'No GPS fix yet', {ms: 4000});
  }, {enableHighAccuracy: true, maximumAge: 3000, timeout: 25000});
}

function drawGpsMarker() {
  if (!GPS.pos) return;
  const c = getCSS('--info');
  if (!GPS.marker) {
    GPS.circle = L.circle(GPS.pos, {radius: GPS.acc || 10, color: c, fillColor: c,
                                    fillOpacity: 0.10, weight: 1, interactive: false}).addTo(map);
    GPS.marker = L.circleMarker(GPS.pos, {radius: 7, color: '#fff', fillColor: c,
                                          fillOpacity: 1, weight: 3, interactive: false}).addTo(map);
  } else {
    GPS.marker.setLatLng(GPS.pos);
    GPS.circle.setLatLng(GPS.pos).setRadius(GPS.acc || 10);
  }
}

/* Requiring a fix before committing a record is the difference between a
 * survey point and a guess. */
function withGpsFix(cb) {
  if (GPS.pos) { cb(GPS.pos, GPS.acc); return; }
  toast('Waiting for a GPS fix…', {ms: 9000});
  startGps((pos, acc) => { hideToast(); cb(pos, acc); });
}


/* ══ 3. Records ══════════════════════════════════════════════════════════ */

let records = [];
let seqCounter = 0;

const KIND_LABEL = {
  eco: 'Ecological feature', habitat: 'Habitat', condition: 'Condition assessment',
  hsi: 'Pond HSI',
};

function recTitle(r) {
  if (r.kind === 'habitat') return r.subType || 'Habitat';
  if (r.kind === 'condition') return 'Condition — ' + (r.subType || '?');
  if (r.kind === 'hsi') return 'Pond HSI — ' + (r.pondName || 'unnamed');
  return r.ftype || 'Feature';
}

function recColour(r) {
  if (r.kind === 'habitat') {
    const m = BIOME.habitats.meta[r.subType];
    return (m && m.color) || getCSS('--accent');
  }
  if (r.geomType === 'line') {
    const m = BIOME.features.line_meta[r.ftype];
    return (m && m.color) || getCSS('--accent');
  }
  if (r.geomType === 'area') {
    const m = BIOME.features.area_meta[r.ftype];
    return (m && m.fill) || getCSS('--accent');
  }
  return getCSS('--accent');
}

function recSubtitle(r) {
  const bits = [];
  if (r.kind === 'habitat') {
    bits.push(BIOME.habitats.module_labels[r.module] || r.module);
    if (r.condition) bits.push(r.condition);
  } else if (r.kind === 'condition') {
    bits.push(r.condition || 'not graded');
  } else if (r.kind === 'hsi') {
    bits.push(r.hsiScore != null ? 'HSI ' + r.hsiScore.toFixed(2) + ' · ' + r.hsiBand : 'incomplete');
  }
  if (r.measure) bits.push(r.measure);
  if (r.notes) bits.push(r.notes.split('\n')[0]);
  if (r.photos && r.photos.length) bits.push(r.photos.length + ' photo' + (r.photos.length > 1 ? 's' : ''));
  return bits.join(' · ');
}

function newRecord(kind) {
  return {
    uuid: uuid(), seq: ++seqCounter, kind,
    geomType: null, geom: null,
    ftype: '', module: '', habType: '', subType: '',
    condition: '', conditionDetail: '', conditionReport: '', conditionState: null,
    strategic: BIOME.habitats.strategic_default, irreplaceable: 'No',
    waterEnc: '', ripL: '', ripR: '',
    habRef: '', notes: '', label: '',
    treeSize: '', treeLocation: '',
    bearing: null, fovAngle: null, fovRange: null,
    hsiState: null, hsiScore: null, hsiBand: '', pondName: '', pondRef: '',
    photos: [], gridref: '', accuracy: null,
    timestamp: new Date().toISOString(),
    surveyor: Store.meta('surveyor') || '',
  };
}

function addRecord(r) {
  records.push(r);
  drawRecord(r);
  persist();
  updateCounts();
}

function removeRecord(r, silent) {
  const i = records.indexOf(r);
  if (i < 0) return;
  records.splice(i, 1);
  if (r._layer) { r._layer.remove(); r._layer = null; }
  persist();
  updateCounts();
  if (!silent) {
    /* Deleting a record you walked to is expensive. Always offer the undo. */
    toast('Deleted ' + recTitle(r), {action: 'Undo', onAction: () => {
      records.splice(Math.min(i, records.length), 0, r);
      drawRecord(r); persist(); updateCounts(); Sheet.repaint();
    }});
  }
}

let _persistTimer = null;
function persist() {
  clearTimeout(_persistTimer);
  _persistTimer = setTimeout(() => Store.putAll(records), 220);
}

function updateCounts() {
  const badge = document.getElementById('rec-badge');
  badge.textContent = records.length ? String(records.length) : '';
  const sub = document.getElementById('site-sub');
  if (!records.length) sub.textContent = SITE.name ? 'No records yet' : 'Ready';
  else sub.textContent = records.length + ' record' + (records.length === 1 ? '' : 's') +
                         (Store.meta('lastExport') ? '' : ' · not yet exported');
}

/* ── Map rendering ─────────────────────────────────────────────────────── */

function pointIconFor(r) {
  const meta = BIOME.features.point_meta[r.ftype];
  const label = labelsOn() && r.label ? `<div class="map-label">${esc(r.label)}</div>` : '';
  if (r.kind === 'habitat' && r.module === 'trees') {
    const c = recColour(r);
    return L.divIcon({className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: `<div class="marker-pin"><svg width="30" height="30" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" fill="${c}" stroke="#fff" stroke-width="1.6"/>
        <path d="M12 17v-4M9 11l3-4 3 4" stroke="#fff" stroke-width="1.6" fill="none"
              stroke-linecap="round" stroke-linejoin="round"/></svg></div>${label}`});
  }
  if (meta && meta.svg) {
    return L.divIcon({className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: `<div class="marker-pin" style="width:30px;height:30px">${
        meta.svg.replace('<svg', '<svg width="30" height="30"')}</div>${label}`});
  }
  /* Assessments get their own marks so a pond score and a condition sheet are
   * not two identical dots. */
  if (r.kind === 'hsi' || r.kind === 'condition') {
    const c = r.kind === 'hsi' ? getCSS('--info') : getCSS('--warn');
    const glyph = r.kind === 'hsi'
      ? '<path d="M12 6.5c2.2 2.6 3.5 4.5 3.5 6a3.5 3.5 0 0 1-7 0c0-1.5 1.3-3.4 3.5-6z"/>'
      : '<path d="M9 12.5l2 2 4-4.5"/>';
    return L.divIcon({className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: `<div class="marker-pin"><svg width="30" height="30" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9.5" fill="${c}" stroke="#fff" stroke-width="1.6"/>
        <g fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"
           stroke-linejoin="round">${glyph}</g></svg></div>${label}`});
  }
  const c = recColour(r);
  return L.divIcon({className: '', iconSize: [26, 26], iconAnchor: [13, 13],
    html: `<div class="marker-pin"><svg width="26" height="26" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="8" fill="${c}" stroke="#fff" stroke-width="2"/></svg></div>${label}`});
}

function drawRecord(r) {
  if (r._layer) { r._layer.remove(); r._layer = null; }
  if (!r.geom) return;
  const c = recColour(r);
  let layer = null;

  if (r.geomType === 'point') {
    layer = L.marker([r.geom.coordinates[1], r.geom.coordinates[0]], {icon: pointIconFor(r)});
  } else if (r.geomType === 'line') {
    const meta = BIOME.features.line_meta[r.ftype] || {};
    const latlngs = r.geom.coordinates.map(p => [p[1], p[0]]);
    const opts = {color: c, weight: r.kind === 'habitat' ? 5 : 4};
    if (meta.style === 'dash') opts.dashArray = '10 7';
    else if (meta.style === 'dot') opts.dashArray = '2 7';
    else if (meta.style === 'dash dot') opts.dashArray = '10 5 2 5';
    layer = L.polyline(latlngs, opts);
  } else if (r.geomType === 'area') {
    const meta = BIOME.features.area_meta[r.ftype] || {};
    const ring = r.geom.coordinates[0].map(p => [p[1], p[0]]);
    layer = L.polygon(ring, {
      color: meta.border || c, weight: 2, fillColor: meta.fill || c,
      fillOpacity: r.kind === 'habitat' ? 0.45 : parseFloat(meta.opacity || '0.3'),
    });
  }
  if (!layer) return;

  layer.on('click', ev => {
    /* Mid-placement or mid-draw the tap belongs to the map, not to whatever
     * happens to be underneath — otherwise you could not put a point on top of
     * an existing feature. */
    if (_tapArmed || draw) return;
    if (ev && ev.originalEvent) L.DomEvent.stopPropagation(ev);
    /* While "assess by tapping" is armed, a tap picks the subject rather than
     * opening the record. */
    if (assessPick) { pickForAssessment(r); return; }
    openRecordSheet(r);
  });
  layer.addTo(featureGroup);
  r._layer = layer;
}

/* ── Assess by tapping a feature ─────────────────────────────────────────── */

let assessPick = false;

function canAssess(r) {
  return r.kind === 'habitat' && !!BIOME.condition.habitats[r.subType];
}

function armAssessPick() {
  assessPick = true;
  Sheet.close();
  document.getElementById('place-bar-title').textContent =
    'Tap the habitat you want to assess';
  const n = records.filter(canAssess).length;
  document.getElementById('place-bar-sub').textContent =
    n ? `${n} mapped habitat${n === 1 ? '' : 's'} can be assessed`
      : 'Nothing assessable is mapped yet';
  document.getElementById('place-bar').classList.add('show');
  document.getElementById('fab').classList.add('hidden');
  map.on('click', onAssessMiss);
}

function disarmAssessPick() {
  assessPick = false;
  map.off('click', onAssessMiss);
  document.getElementById('place-bar').classList.remove('show');
  document.getElementById('fab').classList.remove('hidden');
}

/* A tap that reaches the map rather than a feature. */
function onAssessMiss() {
  toast('Tap directly on a habitat — its outline or marker');
}

function pickForAssessment(r) {
  if (!canAssess(r)) {
    /* Say which thing was tapped: "nothing happened" is the worst answer. */
    toast(r.kind === 'habitat'
      ? `${recTitle(r)} has no criteria sheet — its condition is fixed`
      : `${recTitle(r)} is a feature, not a habitat`, {ms: 3800});
    return;
  }
  disarmAssessPick();
  zoomToRecord(r);
  openCondition(r, () => {
    persist();
    drawRecord(r);
    toast(`✓ ${r.subType} — ${r.condition || 'not graded'}`);
  });
}

function redrawAll() { records.forEach(drawRecord); }

function labelsOn() { return Store.meta('labels') !== false; }

/* Auto-number a blank label the way the desktop placer does: "<type>-01". */
function autoLabel(base) {
  const re = new RegExp('^' + base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '-(\\d+)$');
  let max = 0;
  records.forEach(r => {
    const m = re.exec(r.label || '');
    if (m) max = Math.max(max, parseInt(m[1], 10));
  });
  return base + '-' + String(max + 1).padStart(2, '0');
}


/* ══ 4. The Add flow ═════════════════════════════════════════════════════ */

/* A single flat index of everything placeable, so one search box covers the
 * whole plugin. Typing "badger" finds the species record, the sett, the
 * latrine, the gate and the territory polygon at once — which is how a
 * surveyor thinks, and is impossible with the geometry-first menus. */
const INDEX = (() => {
  const out = [];
  BIOME.features.point_groups.forEach(([tab, subs]) => {
    subs.forEach(([sub, names]) => names.forEach(n => out.push({
      cat: 'eco', geom: 'point', name: n, group: tab, sub,
      terms: n + ' ' + tab + ' ' + sub,
    })));
  });
  BIOME.features.line_groups.forEach(([tab, names]) => names.forEach(n => out.push({
    cat: 'eco', geom: 'line', name: n, group: tab, terms: n + ' ' + tab,
  })));
  BIOME.features.area_groups.forEach(([tab, names]) => names.forEach(n => out.push({
    cat: 'eco', geom: 'area', name: n, group: tab, terms: n + ' ' + tab,
  })));
  Object.keys(BIOME.habitats.modules).forEach(mod => {
    BIOME.habitats.modules[mod].forEach(([broad, subs]) => subs.forEach(s => out.push({
      cat: 'habitat', geom: BIOME.habitats.module_geom[mod], name: s,
      group: broad, module: mod, terms: s + ' ' + broad,
    })));
  });
  // De-duplicate: a point type may appear under several tabs.
  const seen = new Set();
  return out.filter(e => {
    const k = e.cat + '|' + e.geom + '|' + e.name;
    if (seen.has(k)) return false;
    seen.add(k); return true;
  });
})();

const GEOM_ICON = {point: 'map-pin', line: 'line', area: 'polygon'};

function ecoRowOpts(name, geom) {
  if (geom === 'point') {
    const meta = BIOME.features.point_meta[name] || {};
    return {name, svg: meta.svg ? meta.svg.replace('<svg', '<svg width="21" height="21"') : '',
            icon: 'map-pin'};
  }
  if (geom === 'line') {
    const m = BIOME.features.line_meta[name] || {};
    return {name, swatch: m.color || getCSS('--accent'), swatchLine: true};
  }
  const m = BIOME.features.area_meta[name] || {};
  return {name, swatch: m.fill || getCSS('--accent')};
}

function habitatRowOpts(sub) {
  const m = BIOME.habitats.meta[sub] || {};
  const dist = ['Very low', '', 'Low', '', 'Medium', '', 'High', '', 'Very high'][m.dist] || '';
  return {name: sub, swatch: m.color, sub: m.broad + (dist ? ' · ' + dist + ' distinctiveness' : '')};
}

function openAddSheet() {
  Sheet.push({
    title: 'Add a record',
    sub: 'Search everything, or pick a category',
    search: 'Search habitats and features…',
    render(body, q) {
      if (q && normalise(q).length >= 2) { renderSearchResults(body, q); return; }

      /* Counts are derived, never written down — the catalogue is whatever
       * the plugin currently defines. */
      const nMod = m => (BIOME.habitats.modules[m] || [])
        .reduce((n, g) => n + g[1].length, 0);
      const nEco = INDEX.filter(e => e.cat === 'eco').length;
      const nCond = Object.keys(BIOME.condition.habitats).length;

      const cats = [
        {icon: 'plant',      name: 'Area habitat',
         sub: `${nMod('area')} types — draw a parcel`,
         go: () => openHabitatPicker('area')},
        {icon: 'tree',       name: 'Individual tree',
         sub: `${nMod('trees')} size and location classes`,
         go: () => openTreePicker()},
        {icon: 'line',       name: 'Hedgerow',
         sub: `${nMod('hedgerow')} types — trace the line`,
         go: () => openHabitatPicker('hedgerow')},
        {icon: 'droplet',    name: 'Watercourse',
         sub: `${nMod('watercourse')} types — trace the channel`,
         go: () => openHabitatPicker('watercourse')},
        {icon: 'map-pin',    name: 'Ecological feature',
         sub: `${nEco} survey and enhancement types`,
         go: () => openEcoPicker()},
        {icon: 'checklist',  name: 'Condition assessment',
         sub: `${nCond} habitats with statutory criteria`,
         go: () => openConditionHabitatPicker()},
        {icon: 'calculator', name: 'Pond HSI',
         sub: 'Great crested newt suitability index',
         go: () => openHsi(null)},
      ];
      cats.forEach(c => body.appendChild(makeRow(c, c.go)));

      if (records.length) {
        body.appendChild(groupHeader('Recent'));
        const seen = new Set();
        records.slice().reverse().forEach(r => {
          if (seen.size >= 5) return;
          const key = r.kind + '|' + (r.subType || r.ftype);
          if (!key || seen.has(key) || r.kind === 'hsi') return;
          seen.add(key);
          const opts = r.kind === 'habitat'
            ? habitatRowOpts(r.subType)
            : ecoRowOpts(r.ftype, r.geomType);
          opts.sub = 'Repeat this record';
          body.appendChild(makeRow(opts, () => repeatRecord(r)));
        });
      }
    },
  });
}

function renderSearchResults(body, q) {
  const hits = INDEX.filter(e => matchesQuery(e.terms, q));
  if (!hits.length) {
    emptyState(body, 'search', 'Nothing found',
               'Try a shorter word — search ignores punctuation and word order.');
    return;
  }
  const habs = hits.filter(h => h.cat === 'habitat');
  const ecos = hits.filter(h => h.cat === 'eco');

  if (habs.length) {
    body.appendChild(groupHeader(`Habitats (${habs.length})`));
    habs.slice(0, 40).forEach(h => {
      const o = habitatRowOpts(h.name);
      o.pill = BIOME.habitats.module_labels[h.module];
      body.appendChild(makeRow(o, () => startHabitat(h.module, h.name)));
    });
  }
  if (ecos.length) {
    body.appendChild(groupHeader(`Features (${ecos.length})`));
    ecos.slice(0, 60).forEach(h => {
      const o = ecoRowOpts(h.name, h.geom);
      o.sub = h.group;
      o.pill = h.geom === 'point' ? 'Point' : h.geom === 'line' ? 'Line' : 'Area';
      body.appendChild(makeRow(o, () => startEco(h.name, h.geom)));
    });
  }
}

function openEcoPicker() {
  const tabsFor = g => (g === 'point' ? BIOME.features.point_groups.map(x => x[0])
                     : g === 'line'  ? BIOME.features.line_groups.map(x => x[0])
                     :                 BIOME.features.area_groups.map(x => x[0]));
  Sheet.push({
    title: 'Ecological feature',
    sub: 'Same catalogue as the desktop placer',
    search: 'Search features…',
    chips: [{label: 'All'}, {label: 'Points'}, {label: 'Lines'}, {label: 'Areas'}],
    render(body, q, chip) {
      const geoms = chip === 0 ? ['point', 'line', 'area']
                  : chip === 1 ? ['point'] : chip === 2 ? ['line'] : ['area'];
      const pool = INDEX.filter(e => e.cat === 'eco' && geoms.includes(e.geom));
      const hits = q ? pool.filter(e => matchesQuery(e.terms, q)) : pool;

      if (!hits.length) {
        emptyState(body, 'search', 'Nothing found', 'Try a different word.');
        return;
      }
      // Group by tab, preserving catalogue order.
      const order = [];
      const buckets = {};
      hits.forEach(h => {
        const k = h.geom + ' ' + h.group;
        if (!buckets[k]) { buckets[k] = []; order.push(k); }
        buckets[k].push(h);
      });
      order.forEach(k => {
        const [geom, group] = k.split(' ');
        const tag = geom === 'point' ? '' : geom === 'line' ? ' · lines' : ' · areas';
        body.appendChild(groupHeader(group + tag));
        buckets[k].forEach(h => {
          const o = ecoRowOpts(h.name, h.geom);
          if (h.sub) o.sub = h.sub;
          body.appendChild(makeRow(o, () => startEco(h.name, h.geom)));
        });
      });
      void tabsFor;
    },
  });
}

function openHabitatPicker(module) {
  const groups = BIOME.habitats.modules[module] || [];
  const label = BIOME.habitats.module_labels[module];
  Sheet.push({
    title: label,
    sub: groups.reduce((n, g) => n + g[1].length, 0) + ' types',
    search: 'Search ' + label.toLowerCase() + 's…',
    render(body, q) {
      let any = false;
      groups.forEach(([broad, subs]) => {
        const hits = q ? subs.filter(s => matchesQuery(s + ' ' + broad, q)) : subs;
        if (!hits.length) return;
        any = true;
        if (groups.length > 1) body.appendChild(groupHeader(broad));
        hits.forEach(s => body.appendChild(
          makeRow(habitatRowOpts(s), () => startHabitat(module, s))));
      });
      if (!any) emptyState(body, 'search', 'Nothing found', 'Try a different word.');
    },
  });
}

function openTreePicker() {
  Sheet.push({
    title: 'Individual tree',
    sub: 'Classify by trunk diameter at breast height',
    render(body) {
      BIOME.habitats.tree_locations.forEach(loc => {
        body.appendChild(groupHeader(loc));
        BIOME.habitats.tree_sizes.forEach(size => {
          const sub = `${size} ${loc} Tree`;
          if (!BIOME.habitats.meta[sub]) return;
          const o = habitatRowOpts(sub);
          o.name = size;
          o.sub = BIOME.habitats.tree_dbh[size];
          const ha = BIOME.habitats.tree_area_ha[sub];
          if (ha != null) o.pill = (ha * 10000).toFixed(0) + ' m²';
          body.appendChild(makeRow(o, () => startHabitat('trees', sub)));
        });
      });
    },
  });
}


/* ══ 5. Placement ════════════════════════════════════════════════════════ */

let pending = null;      // the record being placed

function startEco(name, geom) {
  const r = newRecord('eco');
  r.ftype = name;
  r.geomType = geom;
  r.label = autoLabel(name);
  const meta = BIOME.features.point_meta[name];
  openPlaceSheet(r, {
    title: name,
    sub: geom === 'point' ? 'Point feature' : geom === 'line' ? 'Line feature' : 'Area feature',
    svg: meta && meta.svg,
  });
}

function startHabitat(module, sub) {
  const r = newRecord('habitat');
  const m = BIOME.habitats.meta[sub] || {};
  r.module = module;
  r.subType = sub;
  r.habType = m.broad || '';
  r.geomType = BIOME.habitats.module_geom[module];
  r.condition = m.cond_target || '';
  r.irreplaceable = m.irreplaceable ? 'Yes' : 'No';
  if (module === 'watercourse') {
    const dflt = sub === 'Culvert' ? 'N/A - Culvert' : 'No Encroachment';
    r.waterEnc = dflt; r.ripL = dflt; r.ripR = dflt;
  }
  if (module === 'trees') {
    r.treeSize = sub.replace(/\s*(Urban|Rural)\s*Tree$/, '').trim();
    r.treeLocation = /Urban/.test(sub) ? 'Urban tree' : 'Rural tree';
  }
  r.label = autoLabel(sub);
  openPlaceSheet(r, {
    title: sub,
    sub: (BIOME.habitats.module_labels[module] || '') + ' · baseline',
    swatch: m.color,
  });
}

function repeatRecord(src) {
  if (src.kind === 'habitat') startHabitat(src.module, src.subType);
  else startEco(src.ftype, src.geomType);
}

const PLACE_MODES = {
  gps:    {label: 'Use my GPS position', sub: 'Records the fix and its accuracy', icon: 'target'},
  tap:    {label: 'Tap on the map',      sub: 'For somewhere you cannot stand',   icon: 'click'},
  centre: {label: 'Use the map centre',  sub: 'Crosshair position',               icon: 'map-pin'},
};

function openPlaceSheet(r, head) {
  pending = r;
  const isPoint = r.geomType === 'point';
  Sheet.push({
    title: head.title,
    sub: head.sub,
    render(body) {
      if (isPoint) {
        const preferred = Store.meta('placeMode') || 'gps';
        const pm = PLACE_MODES[preferred] || PLACE_MODES.gps;
        const primary = document.createElement('button');
        primary.className = 'place-primary';
        primary.innerHTML = `${icon(pm.icon, 26)}
          <span><span class="pp-main">${esc(pm.label)}</span>
          <span class="pp-sub">${esc(pm.sub)}</span></span>`;
        primary.onclick = () => placePoint(preferred);
        body.appendChild(primary);

        const alt = document.createElement('div');
        alt.className = 'place-alt';
        Object.keys(PLACE_MODES).filter(k => k !== preferred).forEach(k => {
          const b = document.createElement('button');
          b.innerHTML = `${icon(PLACE_MODES[k].icon, 19)}<span>${
            k === 'gps' ? 'My GPS' : k === 'tap' ? 'Tap map' : 'Centre'}</span>`;
          b.onclick = () => placePoint(k);
          alt.appendChild(b);
        });
        body.appendChild(alt);
      } else {
        const primary = document.createElement('button');
        primary.className = 'place-primary';
        primary.innerHTML = `${icon(r.geomType === 'line' ? 'line' : 'polygon', 26)}
          <span><span class="pp-main">Draw on the map</span>
          <span class="pp-sub">Tap to add each corner — length shown live</span></span>`;
        primary.onclick = () => { Sheet.close(); startDraw(r); };
        body.appendChild(primary);

        const alt = document.createElement('div');
        alt.className = 'place-alt';
        const walk = document.createElement('button');
        walk.innerHTML = `${icon('target', 19)}<span>Walk the line</span>`;
        walk.onclick = () => { Sheet.close(); startDraw(r, true); };
        alt.appendChild(walk);
        body.appendChild(alt);
      }

      body.appendChild(detailBlock(r));
    },
  });
}

/* The detail block is deliberately collapsed. In the field the common case is
 * "this is a Badger Sett, here" — everything else can be filled in later from
 * the record list, and burying it keeps the placement two taps deep. */
function detailBlock(r) {
  const wrap = document.createElement('div');

  const det = document.createElement('details');
  det.className = 'disclose';
  det.open = !!Store.meta('detailOpen');
  det.addEventListener('toggle', () => Store.meta('detailOpen', det.open));
  const filled = detailSummary(r);
  det.innerHTML = `<summary>${icon('pencil', 17)} Detail${
    filled ? ` <span class="pill accent" style="margin-left:auto">${esc(filled)}</span>` : ''}</summary>
    <div class="disclose-body"></div>`;
  const dbody = det.querySelector('.disclose-body');

  dbody.appendChild(field('Label', () => {
    const i = document.createElement('input');
    i.className = 'input'; i.value = r.label || '';
    i.placeholder = 'Auto-numbered';
    i.oninput = () => { r.label = i.value; };
    return i;
  }));

  dbody.appendChild(field('Notes', () => {
    const t = document.createElement('textarea');
    t.className = 'input'; t.rows = 3; t.value = r.notes || '';
    t.placeholder = 'What you saw, why it matters…';
    t.oninput = () => { r.notes = t.value; };
    return t;
  }));

  dbody.appendChild(photoField(r));

  if (r.kind === 'habitat') {
    const m = BIOME.habitats.meta[r.subType] || {};

    if (m.cond_locked) {
      dbody.appendChild(field('Condition', () => {
        const d = document.createElement('div');
        d.className = 'status-line';
        d.innerHTML = `<span class="sl-dot"></span>Fixed at <b style="margin-left:4px">${
          esc(m.cond_target)}</b> for this habitat`;
        return d;
      }));
      r.condition = m.cond_target;
    } else {
      dbody.appendChild(field('Condition', () => {
        const holder = document.createElement('div');
        const sel = document.createElement('select');
        sel.className = 'select';
        (m.cond_items || BIOME.habitats.condition_options).forEach(o => {
          const op = document.createElement('option');
          op.value = o; op.textContent = o;
          if (o === r.condition) op.selected = true;
          sel.appendChild(op);
        });
        sel.onchange = () => { r.condition = sel.value; };
        holder.appendChild(sel);

        if (BIOME.condition.habitats[r.subType]) {
          const b = document.createElement('button');
          b.className = 'btn secondary wide';
          b.style.marginTop = '8px';
          b.innerHTML = icon('checklist', 18) + ' Assess against criteria';
          b.onclick = () => openCondition(r, () => { sel.value = r.condition; });
          holder.appendChild(b);
        }
        return holder;
      }));
    }

    dbody.appendChild(field('Strategic significance', () => {
      const sel = document.createElement('select');
      sel.className = 'select';
      BIOME.habitats.strategic.filter(Boolean).forEach(o => {
        const op = document.createElement('option');
        op.value = o; op.textContent = o;
        if (o === r.strategic) op.selected = true;
        sel.appendChild(op);
      });
      sel.onchange = () => { r.strategic = sel.value; };
      return sel;
    }, 'Defaults to the metric’s own default — change only if the site sits in an LNRS area.'));

    dbody.appendChild(field('Irreplaceable habitat', () => {
      const s = seg(['No', 'Yes'], r.irreplaceable, v => { r.irreplaceable = v; });
      return s;
    }, m.irreplaceable ? 'This habitat is statutorily irreplaceable — set automatically.' : ''));

    if (r.module === 'watercourse' && r.subType !== 'Culvert') {
      dbody.appendChild(field('Channel encroachment', () => selectFrom(
        BIOME.habitats.water_enc.filter(Boolean), r.waterEnc, v => { r.waterEnc = v; })));
      dbody.appendChild(field('Riparian — left bank', () => selectFrom(
        BIOME.habitats.rip_enc.filter(Boolean), r.ripL, v => { r.ripL = v; })));
      dbody.appendChild(field('Riparian — right bank', () => selectFrom(
        BIOME.habitats.rip_enc.filter(Boolean), r.ripR, v => { r.ripR = v; })));
    }

    dbody.appendChild(field('Habitat reference', () => {
      const i = document.createElement('input');
      i.className = 'input'; i.value = r.habRef || '';
      i.placeholder = 'Your own parcel reference';
      i.oninput = () => { r.habRef = i.value; };
      return i;
    }));
  }

  wrap.appendChild(det);
  return wrap;
}

function detailSummary(r) {
  const bits = [];
  if (r.notes) bits.push('note');
  if (r.photos && r.photos.length) bits.push(r.photos.length + ' photo' + (r.photos.length > 1 ? 's' : ''));
  if (r.kind === 'habitat' && r.condition) bits.push(r.condition);
  return bits.join(' · ');
}

function field(label, build, hint) {
  const d = document.createElement('div');
  d.className = 'field';
  const l = document.createElement('label');
  l.textContent = label;
  d.appendChild(l);
  d.appendChild(build());
  if (hint) {
    const h = document.createElement('div');
    h.className = 'hint'; h.textContent = hint;
    d.appendChild(h);
  }
  return d;
}

function selectFrom(options, value, onChange) {
  const sel = document.createElement('select');
  sel.className = 'select';
  options.forEach(o => {
    const op = document.createElement('option');
    op.value = o; op.textContent = o;
    if (o === value) op.selected = true;
    sel.appendChild(op);
  });
  sel.onchange = () => onChange(sel.value);
  return sel;
}

function seg(options, value, onChange) {
  const d = document.createElement('div');
  d.className = 'seg';
  options.forEach(o => {
    const b = document.createElement('button');
    b.textContent = o;
    b.className = o === value ? 'on' : '';
    b.onclick = () => {
      d.querySelectorAll('button').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      onChange(o);
    };
    d.appendChild(b);
  });
  return d;
}

/* ── Photos ────────────────────────────────────────────────────────────── */

let _photoTarget = null;
function photoField(r) {
  return field('Photos', () => {
    const strip = document.createElement('div');
    strip.className = 'photo-strip';
    const paint = () => {
      strip.innerHTML = '';
      (r.photos || []).forEach((p, i) => {
        const t = document.createElement('div');
        t.className = 'photo-thumb';
        t.innerHTML = `<img src="${p.data}" alt=""/><button aria-label="Remove">✕</button>`;
        t.querySelector('button').onclick = e => {
          e.stopPropagation();
          r.photos.splice(i, 1); persist(); paint();
        };
        strip.appendChild(t);
      });
      const add = document.createElement('button');
      add.className = 'photo-add';
      add.innerHTML = icon('camera', 20) + '<span>Add</span>';
      add.onclick = () => {
        _photoTarget = {rec: r, repaint: paint};
        document.getElementById('photo-input').click();
      };
      strip.appendChild(add);
    };
    paint();
    return strip;
  });
}

document.getElementById('photo-input').onchange = async function () {
  const files = Array.from(this.files || []);
  this.value = '';
  if (!files.length || !_photoTarget) return;
  const {rec, repaint} = _photoTarget;
  toast('Processing ' + files.length + ' photo' + (files.length > 1 ? 's' : '') + '…', {ms: 8000});
  for (const f of files) {
    try {
      const {dataUrl, w, h} = await shrinkImage(f, 1600, 0.82);
      rec.photos = rec.photos || [];
      const idx = rec.photos.length + 1;
      const stem = (rec.label || rec.ftype || rec.subType || 'photo')
        .replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 40);
      rec.photos.push({name: `${stem}_${idx}.jpg`, data: dataUrl, w, h});
    } catch (e) { /* skip the unreadable one, keep the rest */ }
  }
  persist();
  hideToast();
  repaint && repaint();
  toast('✓ Photo added');
};

/* ── Committing a point ────────────────────────────────────────────────── */

function placePoint(mode) {
  Store.meta('placeMode', mode);
  const r = pending;
  if (!r) return;
  if (mode === 'gps') {
    Sheet.close();
    withGpsFix((pos, acc) => { r.accuracy = acc; commitPoint(r, pos[0], pos[1]); });
  } else if (mode === 'centre') {
    Sheet.close();
    const c = map.getCenter();
    commitPoint(r, c.lat, c.lng);
  } else {
    Sheet.close();
    armTap(r);
  }
}

let _tapArmed = null;
function armTap(r) {
  _tapArmed = r;
  document.getElementById('place-bar-title').textContent = 'Tap the map to place';
  document.getElementById('place-bar-sub').textContent = recTitle(r);
  document.getElementById('place-bar').classList.add('show');
  document.getElementById('crosshair').classList.add('show');
  document.getElementById('fab').classList.add('hidden');
  map.on('click', onTapPlace);
}

function disarmTap() {
  _tapArmed = null;
  map.off('click', onTapPlace);
  document.getElementById('place-bar').classList.remove('show');
  document.getElementById('crosshair').classList.remove('show');
  document.getElementById('fab').classList.remove('hidden');
}

function onTapPlace(e) {
  if (map._userDragged) return;          // a pan that ends in a tap is not a placement
  const r = _tapArmed;
  disarmTap();
  if (r) commitPoint(r, e.latlng.lat, e.latlng.lng);
}

document.getElementById('place-bar-cancel').onclick = () => {
  if (assessPick) { disarmAssessPick(); toast('Cancelled'); return; }
  disarmTap();
  toast('Cancelled');
};

function commitPoint(r, lat, lon) {
  r.geom = {type: 'Point', coordinates: [round6(lon), round6(lat)]};
  r.gridref = GridRef.format(lat, lon);
  r.timestamp = new Date().toISOString();
  if (!r.label) r.label = autoLabel(r.ftype || r.subType || 'Record');
  addRecord(r);
  buzz();
  /* Some records continue into a form once they have a position — a pond is
   * pinned first, then scored. The hook is underscore-prefixed so it is
   * stripped before the record is persisted. */
  const next = r._afterPlace;
  if (next) { r._afterPlace = null; next(r); return; }
  afterPlace(r);
}

function round6(v) { return Math.round(v * 1e6) / 1e6; }

/* Placing 30 trees should not mean 30 trips through the menu. */
function afterPlace(r) {
  pending = null;
  const repeatable = r.kind === 'eco' || r.kind === 'habitat';
  toast('✓ ' + recTitle(r) + (r.gridref ? ' · ' + r.gridref : ''),
        repeatable ? {action: 'Add another', onAction: () => repeatRecord(r)} : {});
}


/* ══ 6. Drawing ══════════════════════════════════════════════════════════ */

let draw = null;

function startDraw(r, walkMode) {
  draw = {rec: r, verts: [], markers: [], shape: null, walk: !!walkMode};
  document.getElementById('draw-bar-title').textContent =
    (walkMode ? 'Walking ' : 'Drawing ') + recTitle(r);
  document.getElementById('draw-bar').classList.add('show');
  document.getElementById('fab').classList.add('hidden');
  document.getElementById('draw-here').style.display = '';
  map.on('click', onDrawClick);
  if (walkMode) { startGps(); GPS.follow = true; }
  updateDrawBar();
}

function onDrawClick(e) {
  if (map._userDragged) return;
  addVertex(e.latlng.lat, e.latlng.lng);
}

function addVertex(lat, lon) {
  if (!draw) return;
  draw.verts.push([lat, lon]);
  const vm = L.circleMarker([lat, lon], {
    radius: 6, color: '#fff', fillColor: getCSS('--accent'), fillOpacity: 1, weight: 2,
  }).addTo(featureGroup);
  draw.markers.push(vm);
  redrawShape();
  updateDrawBar();
  buzz(12);
}

function redrawShape() {
  if (!draw) return;
  if (draw.shape) { draw.shape.remove(); draw.shape = null; }
  const r = draw.rec;
  const c = recColour(r);
  if (r.geomType === 'line' && draw.verts.length > 1) {
    draw.shape = L.polyline(draw.verts, {color: c, weight: 4}).addTo(featureGroup);
  } else if (r.geomType === 'area' && draw.verts.length > 2) {
    const meta = BIOME.features.area_meta[r.ftype] || {};
    draw.shape = L.polygon(draw.verts, {
      color: meta.border || c, weight: 2, fillColor: meta.fill || c,
      fillOpacity: r.kind === 'habitat' ? 0.45 : parseFloat(meta.opacity || '0.3'),
    }).addTo(featureGroup);
  }
}

/* Live measurement is the single most useful thing a drawing tool can show:
 * it is the only way to tell a 40 m hedge from a 400 m one on a phone screen. */
function updateDrawBar() {
  if (!draw) return;
  const n = draw.verts.length;
  const r = draw.rec;
  let txt = n + ' point' + (n === 1 ? '' : 's');
  if (r.geomType === 'line' && n > 1) {
    txt += ' · ' + Geo.fmtLength(Geo.length(draw.verts));
  } else if (r.geomType === 'area' && n > 2) {
    txt += ' · ' + Geo.fmtArea(Geo.area(draw.verts)) +
           ' · perimeter ' + Geo.fmtLength(Geo.length(draw.verts.concat([draw.verts[0]])));
  }
  const need = r.geomType === 'line' ? 2 : 3;
  if (n < need) txt += ' · need ' + (need - n) + ' more';
  document.getElementById('draw-bar-sub').textContent = txt;
  document.getElementById('draw-finish').disabled = n < need;
  document.getElementById('draw-finish').style.opacity = n < need ? 0.45 : 1;
}

document.getElementById('draw-here').onclick = () =>
  withGpsFix(pos => addVertex(pos[0], pos[1]));

document.getElementById('draw-undo').onclick = () => {
  if (!draw || !draw.verts.length) return;
  draw.verts.pop();
  const m = draw.markers.pop(); if (m) m.remove();
  redrawShape(); updateDrawBar();
};

document.getElementById('draw-finish').onclick = () => {
  if (!draw) return;
  const r = draw.rec;
  const need = r.geomType === 'line' ? 2 : 3;
  if (draw.verts.length < need) { toast('Need at least ' + need + ' points'); return; }

  if (r.geomType === 'line') {
    r.geom = {type: 'LineString', coordinates: draw.verts.map(([la, lo]) => [round6(lo), round6(la)])};
    r.measure = Geo.fmtLength(Geo.length(draw.verts));
  } else {
    const ring = draw.verts.map(([la, lo]) => [round6(lo), round6(la)]);
    ring.push(ring[0]);
    r.geom = {type: 'Polygon', coordinates: [ring]};
    r.measure = Geo.fmtArea(Geo.area(draw.verts));
  }
  const mid = draw.verts[Math.floor(draw.verts.length / 2)];
  r.gridref = GridRef.format(mid[0], mid[1]);
  r.timestamp = new Date().toISOString();
  if (!r.label) r.label = autoLabel(r.ftype || r.subType || 'Record');

  endDraw();
  addRecord(r);
  buzz();
  afterPlace(r);
};

document.getElementById('draw-cancel').onclick = () => { endDraw(); toast('Drawing cancelled'); };

function endDraw() {
  if (!draw) return;
  draw.markers.forEach(m => m.remove());
  if (draw.shape) draw.shape.remove();
  GPS.follow = false;
  draw = null;
  map.off('click', onDrawClick);
  document.getElementById('draw-bar').classList.remove('show');
  document.getElementById('fab').classList.remove('hidden');
}


/* ══ 7. Condition assessment ═════════════════════════════════════════════
 * A direct port of condition_data.evaluate. The sheet definitions, criteria
 * text, footnotes and effective bands all arrive in the payload, so only the
 * seven scoring models are implemented here.
 */

function evaluateCondition(sub, state) {
  const rec = BIOME.condition.habitats[sub];
  if (!rec) return null;
  const sheet = BIOME.condition.sheets[rec.sheet];
  const model = rec.model;
  const passed = new Set(state.passed || []);

  if (model === 'note') return null;

  if (model === 'score') {
    const scores = state.scores || {};
    const ind = sheet.indicators || [];
    if (ind.filter(i => scores[i[0]]).length < ind.length) return null;
    const total = ind.reduce((t, i) => t + (parseInt(scores[i[0]], 10) || 0), 0);
    for (const [minTotal, grade] of sheet.bands) if (total >= minTotal) return grade;
    return 'Poor';
  }

  if (model === 'hedgerow') {
    const letters = rec.letters;
    const fails = letters.filter(l => !passed.has(l));
    const groups = {};
    letters.forEach(l => { (groups[l[0]] = groups[l[0]] || []).push(l); });
    const double = Object.keys(groups).filter(g =>
      groups[g].length === 2 && groups[g].every(a => !passed.has(a))).length;
    const modFail = rec.with_trees ? 5 : 4;
    if (fails.length <= 2 && double === 0) return 'Good';
    if (fails.length <= modFail && double <= 1) return 'Moderate';
    return 'Poor';
  }

  if (model === 'urban') {
    const used = rec.letters;
    const t = used.length;
    const p = used.filter(l => passed.has(l)).length;
    const poorThr = t >= 5 ? 2 : 1;
    if (p === t && passed.has('C') && state.c_absent) return 'Good';
    if (p <= poorThr) return 'Poor';
    return 'Moderate';
  }

  if (model === 'wetland') {
    const core = rec.core;
    const corePass = core.filter(l => passed.has(l)).length;
    const addPass = !!rec.additional && passed.has(rec.additional);
    const total = corePass + (addPass ? 1 : 0);
    if (corePass >= 5 && passed.has('A') && addPass) return 'Good';
    if (total <= 3) return 'Poor';
    return 'Moderate';
  }

  if (model === 'pond') {
    if (state.variant === 'woodland') {
      const n = rec.core.filter(l => passed.has(l)).length;
      return n >= 7 ? 'Good' : n >= 5 ? 'Moderate' : 'Poor';
    }
    const letters = rec.core.concat(rec.extra || []);
    const n = letters.filter(l => passed.has(l)).length;
    return n >= 9 ? 'Good' : n >= 6 ? 'Moderate' : 'Poor';
  }

  // count
  const n = rec.letters.filter(l => passed.has(l)).length;
  for (const [grade, minp, req] of rec.bands) {
    if (n >= minp && req.every(x => passed.has(x))) return grade;
  }
  return 'Poor';
}

/* Which letters are live for this habitat right now — pond drops H/I in the
 * woodland variant, exactly as the desktop dialog disables them. */
function activeLetters(sub, state) {
  const rec = BIOME.condition.habitats[sub];
  if (!rec) return [];
  if (rec.model === 'pond') {
    return state.variant === 'woodland' ? rec.core.slice() : rec.core.concat(rec.extra || []);
  }
  if (rec.model === 'wetland') {
    return rec.core.concat(rec.additional ? [rec.additional] : []);
  }
  return rec.letters ? rec.letters.slice() : [];
}

function joinLetters(list) {
  if (!list.length) return '';
  if (list.length === 1) return list[0];
  if (list.length === 2) return list[0] + ' and ' + list[1];
  return list.slice(0, -1).join(', ') + ', and ' + list[list.length - 1];
}

function critWord(n) { return n === 1 ? 'Criterion' : 'Criteria'; }

/* Mirrors condition_dialog.tick_report_text so the prose written into
 * user_comments reads identically whether the assessment was done on the
 * phone or at the desk. */
function conditionReport(sub, state, grade) {
  const rec = BIOME.condition.habitats[sub];
  if (!rec) return '';
  const sheet = BIOME.condition.sheets[rec.sheet];

  if (rec.model === 'note') return 'Condition Assessment: ' + (sheet.note || '');

  if (rec.model === 'score') {
    const ind = sheet.indicators || [];
    const total = ind.reduce((t, i) => t + (parseInt((state.scores || {})[i[0]], 10) || 0), 0);
    const max = ind.length * 3;
    return `Condition Assessment: Assessed using the ${ind.length}-indicator woodland ` +
           `condition method (score ${total} of ${max}). Overall, the habitat was assessed ` +
           `as being in ${grade} Condition.`;
  }

  const letters = activeLetters(sub, state);
  const passed = letters.filter(l => (state.passed || []).includes(l));
  const failed = letters.filter(l => !(state.passed || []).includes(l));
  let s = 'Condition Assessment: ';
  const parts = [];
  if (passed.length) parts.push(`${critWord(passed.length)} ${joinLetters(passed)} passed`);
  if (failed.length) parts.push(`${critWord(failed.length)} ${joinLetters(failed)} failed`);
  s += parts.join('; ') + '.';
  if (rec.model === 'urban' && passed.includes('C') && !state.c_absent) {
    s += ' Criterion C was met at <5% cover but not by complete absence of invasive species.';
  }
  if (rec.model === 'pond' && state.variant === 'woodland') {
    s += ' Assessed as a woodland pond.';
  }
  if (grade) s += ` Overall, the habitat was assessed as being in ${grade} Condition.`;
  return s;
}

/* condition_detail wire format — condition_dialog.serialize_state. */
function serializeCondition(sub, state) {
  const rec = BIOME.condition.habitats[sub];
  if (!rec) return '';
  if (rec.model === 'score') {
    const parts = Object.keys(state.scores || {}).sort()
      .map(k => k + '=' + state.scores[k]);
    return parts.length ? 'score|' + parts.join(',') : '';
  }
  const passed = (state.passed || []).slice().sort();
  let s = 'tick|' + passed.join(',');
  const tail = [];
  if (state.c_absent) tail.push('cabs');
  if (state.variant === 'woodland') tail.push('wood');
  if (state.trees && state.trees.length) {
    tail.push('trees=' + state.trees
      .filter(t => t.ref || t.dbh)
      .map(t => san(t.ref) + '~' + san(t.dbh)).join(';'));
  }
  return tail.length ? s + '|' + tail.join('|') : s;
}
function san(v) { return String(v == null ? '' : v).replace(/[|;~=]/g, ''); }

/* Assessing something you have already mapped is the normal case, so that is
 * the route offered first — tap the parcel and grade it. Picking a bare habitat
 * type is the fallback for something not drawn yet. */
function openConditionHabitatPicker() {
  const mapped = records.filter(canAssess);
  Sheet.push({
    title: 'Condition assessment',
    sub: 'Which habitat are you assessing?',
    render(body) {
      const primary = document.createElement('button');
      primary.className = 'place-primary';
      primary.innerHTML = `${icon('click', 26)}<span>
        <span class="pp-main">Tap it on the map</span>
        <span class="pp-sub">${mapped.length
          ? mapped.length + ' mapped habitat' + (mapped.length === 1 ? '' : 's')
          : 'Nothing assessable mapped yet'}</span></span>`;
      primary.onclick = armAssessPick;
      body.appendChild(primary);

      if (mapped.length) {
        body.appendChild(groupHeader('Or pick from what you have mapped'));
        mapped.slice().reverse().forEach(r => {
          const o = habitatRowOpts(r.subType);
          o.name = r.label || r.subType;
          o.sub = r.subType + (r.measure ? ' · ' + r.measure : '');
          if (r.condition) { o.pill = r.condition; o.pillClass = gradeClass(r.condition); }
          body.appendChild(makeRow(o, () => {
            Sheet.close();
            zoomToRecord(r);
            openCondition(r, () => {
              persist(); drawRecord(r);
              toast(`✓ ${r.subType} — ${r.condition || 'not graded'}`);
            });
          }));
        });
      }

      body.appendChild(groupHeader('Not mapped yet'));
      body.appendChild(makeRow({
        icon: 'checklist', name: 'Assess a habitat type',
        sub: Object.keys(BIOME.condition.habitats).length +
             ' habitats have a criteria sheet — link it in QGIS later',
      }, openStandaloneConditionPicker));
    },
  });
}

function gradeClass(g) {
  return g === 'Good' ? 'good' : g === 'Poor' ? 'bad'
       : g === 'Moderate' ? 'warn' : '';
}

function openStandaloneConditionPicker() {
  const assessable = Object.keys(BIOME.condition.habitats).sort();
  Sheet.push({
    title: 'Assess a habitat type',
    sub: assessable.length + ' habitats have a criteria sheet',
    search: 'Which habitat?',
    render(body, q) {
      const hits = q ? assessable.filter(s => matchesQuery(s, q)) : assessable;
      if (!hits.length) { emptyState(body, 'search', 'Nothing found', 'Try a different word.'); return; }
      const byBroad = {};
      hits.forEach(s => {
        const b = (BIOME.habitats.meta[s] || {}).broad || 'Other';
        (byBroad[b] = byBroad[b] || []).push(s);
      });
      Object.keys(byBroad).sort().forEach(b => {
        body.appendChild(groupHeader(b));
        byBroad[b].forEach(s => body.appendChild(
          makeRow(habitatRowOpts(s), () => startStandaloneCondition(s))));
      });
    },
  });
}

/* A standalone assessment carries no polygon — the desktop links it to a
 * parcel later. An optional pin makes that link findable. */
function startStandaloneCondition(sub) {
  const r = newRecord('condition');
  const m = BIOME.habitats.meta[sub] || {};
  r.subType = sub;
  r.habType = m.broad || '';
  r.module = m.module || '';
  r.geomType = 'point';
  r.label = autoLabel('Condition');
  openCondition(r, () => {
    Sheet.replace({
      title: 'Where is it?',
      sub: sub + (r.condition ? ' · ' + r.condition : ''),
      render(body) {
        const note = document.createElement('div');
        note.className = 'status-line';
        note.innerHTML = `<span class="sl-dot"></span>A pin is optional — QGIS can link this
          assessment to a parcel by habitat type.`;
        body.appendChild(note);

        const primary = document.createElement('button');
        primary.className = 'place-primary';
        primary.innerHTML = `${icon('target', 26)}<span><span class="pp-main">Drop a pin here</span>
          <span class="pp-sub">Uses my GPS position</span></span>`;
        primary.onclick = () => { pending = r; placePoint('gps'); };
        body.appendChild(primary);

        const alt = document.createElement('div');
        alt.className = 'place-alt';
        const tapB = document.createElement('button');
        tapB.innerHTML = icon('click', 19) + '<span>Tap map</span>';
        tapB.onclick = () => { pending = r; placePoint('tap'); };
        const noneB = document.createElement('button');
        noneB.innerHTML = icon('check', 19) + '<span>No pin</span>';
        noneB.onclick = () => {
          r.geomType = null; r.geom = null;
          Sheet.close(); addRecord(r); buzz();
          toast('✓ Assessment saved');
        };
        alt.appendChild(tapB); alt.appendChild(noneB);
        body.appendChild(alt);
        body.appendChild(detailBlock(r));
      },
    });
  }, true);
}

function openCondition(r, onDone, isStandalone) {
  const sub = r.subType;
  const rec = BIOME.condition.habitats[sub];
  if (!rec) { toast('No criteria sheet for this habitat'); return; }
  const sheet = BIOME.condition.sheets[rec.sheet];

  const state = r.conditionState
    ? JSON.parse(JSON.stringify(r.conditionState))
    : {passed: [], c_absent: false, variant: null, scores: {}, trees: []};

  const commit = () => {
    const grade = evaluateCondition(sub, state);
    r.conditionState = state;
    r.condition = grade || r.condition;
    r.conditionDetail = serializeCondition(sub, state);
    r.conditionReport = conditionReport(sub, state, grade);
    persist();
  };

  Sheet.push({
    title: 'Condition — ' + sub,
    sub: sheet.model === 'score' ? '13-indicator woodland method'
       : sheet.model === 'note' ? 'No criteria apply'
       : 'Tick every criterion the habitat meets',
    foot(f) {
      const done = document.createElement('button');
      done.className = 'btn primary';
      done.innerHTML = icon('check', 18) + ' Done';
      done.onclick = () => {
        commit();
        if (isStandalone) { onDone && onDone(); }
        else { Sheet.back(); onDone && onDone(); }
      };
      f.appendChild(done);
    },
    render(body) {
      if (sheet.model === 'note') {
        const d = document.createElement('div');
        d.className = 'status-line';
        d.style.alignItems = 'flex-start';
        d.innerHTML = `<span class="sl-dot"></span><span>${esc(sheet.note || '')}</span>`;
        body.appendChild(d);
        return;
      }

      const verdict = document.createElement('div');
      verdict.className = 'verdict';
      body.appendChild(verdict);

      const refresh = () => {
        const grade = evaluateCondition(sub, state);
        const cls = grade ? grade.toLowerCase().replace(/[^a-z]/g, '') : '';
        let tally;
        if (sheet.model === 'score') {
          const ind = sheet.indicators || [];
          const set = ind.filter(i => state.scores[i[0]]).length;
          const total = ind.reduce((t, i) => t + (parseInt(state.scores[i[0]], 10) || 0), 0);
          tally = set < ind.length
            ? `${set} of ${ind.length} indicators scored`
            : `Score ${total} of ${ind.length * 3}`;
        } else {
          const letters = activeLetters(sub, state);
          const n = letters.filter(l => state.passed.includes(l)).length;
          tally = `${n} of ${letters.length} criteria met`;
        }
        verdict.innerHTML = `<span class="grade ${cls}">${esc(grade || '—')}</span>
          <span class="tally">${esc(tally)}</span>`;
        // Repaint only the rows whose enabled-state can change.
        if (sheet.model === 'pond') paintCriteria();
      };

      // Pond variant switch — woodland ponds are graded on A–G only.
      if (sheet.model === 'pond') {
        body.appendChild(field('Pond type', () => seg(
          ['Standard', 'Woodland pond'],
          state.variant === 'woodland' ? 'Woodland pond' : 'Standard',
          v => {
            state.variant = v === 'Woodland pond' ? 'woodland' : null;
            if (state.variant === 'woodland') {
              (rec.extra || []).forEach(l => {
                const i = state.passed.indexOf(l);
                if (i >= 0) state.passed.splice(i, 1);
              });
            }
            refresh();
          })));
      }

      const critWrap = document.createElement('div');
      body.appendChild(critWrap);

      function paintCriteria() {
        critWrap.innerHTML = '';
        if (sheet.model === 'score') {
          (sheet.indicators || []).forEach(([letter, name, descs]) => {
            const row = document.createElement('div');
            row.className = 'score-row';
            row.innerHTML = `<div class="sr-name">${esc(letter)}. ${esc(name)}</div>`;
            const opts = document.createElement('div');
            opts.className = 'score-opts';
            [['Good', 3, 'g'], ['Moderate', 2, 'm'], ['Poor', 1, 'p']].forEach(([lbl, val, cls]) => {
              const b = document.createElement('button');
              b.textContent = lbl;
              if (state.scores[letter] === val) b.classList.add('on', cls);
              b.onclick = () => {
                state.scores[letter] = val;
                opts.querySelectorAll('button').forEach(x => x.className = '');
                b.classList.add('on', cls);
                desc.textContent = descs[3 - val] || '';
                refresh();
              };
              opts.appendChild(b);
            });
            row.appendChild(opts);
            const desc = document.createElement('div');
            desc.className = 'score-desc';
            desc.textContent = state.scores[letter] ? (descs[3 - state.scores[letter]] || '') : '';
            row.appendChild(desc);
            critWrap.appendChild(row);
          });
          return;
        }

        const live = activeLetters(sub, state);
        (sheet.criteria || []).forEach(([letter, text, essential]) => {
          const enabled = live.includes(letter);
          const grp = sheet.groups && sheet.groups[letter[0]];
          const on = state.passed.includes(letter);
          const d = document.createElement('div');
          d.className = 'crit' + (on ? ' on' : '') + (enabled ? '' : ' off-limits');
          d.innerHTML = `<span class="box">✓</span><span style="flex:1;min-width:0">
            <span class="letter">${esc(letter)}${grp ? ' · ' + esc(grp) : ''}</span>
            ${essential ? '<span class="ess">ESSENTIAL</span>' : ''}
            <div class="text">${esc(text)}</div></span>`;
          if (enabled) {
            d.onclick = () => {
              const i = state.passed.indexOf(letter);
              if (i >= 0) state.passed.splice(i, 1); else state.passed.push(letter);
              d.classList.toggle('on');
              buzz(10);
              refresh();
            };
          }
          critWrap.appendChild(d);
        });

        // Urban sheet: criterion C distinguishes "<5% cover" from "wholly absent",
        // and only the latter can reach Good.
        if (sheet.needs_c_absent) {
          const d = document.createElement('div');
          d.className = 'crit' + (state.c_absent ? ' on' : '');
          d.innerHTML = `<span class="box">✓</span><span style="flex:1">
            <span class="letter">C+</span><div class="text">Invasive species are
            <b>completely absent</b>, not merely below 5% cover.</div></span>`;
          d.onclick = () => {
            state.c_absent = !state.c_absent;
            d.classList.toggle('on');
            refresh();
          };
          critWrap.appendChild(d);
        }
      }

      paintCriteria();
      refresh();

      // Tree number / DBH rows for the tree sheets.
      if (BIOME.condition.tree_sheets.includes(rec.sheet)) {
        const det = document.createElement('details');
        det.className = 'disclose';
        det.innerHTML = `<summary>${icon('tree', 17)} Trees measured</summary>
          <div class="disclose-body"></div>`;
        const db = det.querySelector('.disclose-body');
        const paintTrees = () => {
          db.innerHTML = '';
          state.trees = state.trees || [];
          state.trees.forEach((t, i) => {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;gap:6px;margin-top:8px';
            row.innerHTML = `<input class="input" placeholder="Tree no." value="${esc(t.ref)}"/>
              <input class="input" placeholder="DBH cm" inputmode="numeric" value="${esc(t.dbh)}"/>
              <button class="btn danger" style="flex:0 0 46px;padding:0;min-height:46px">✕</button>`;
            const [a, b] = row.querySelectorAll('input');
            a.oninput = () => { t.ref = a.value; };
            b.oninput = () => { t.dbh = b.value; };
            row.querySelector('button').onclick = () => { state.trees.splice(i, 1); paintTrees(); };
            db.appendChild(row);
          });
          const add = document.createElement('button');
          add.className = 'btn secondary wide';
          add.style.marginTop = '8px';
          add.innerHTML = icon('plus', 17) + ' Add a tree';
          add.onclick = () => { state.trees.push({ref: '', dbh: ''}); paintTrees(); };
          db.appendChild(add);
        };
        paintTrees();
        body.appendChild(det);
      }

      if (sheet.footnotes && sheet.footnotes.length) {
        const det = document.createElement('details');
        det.className = 'disclose';
        det.innerHTML = `<summary>${icon('info', 17)} Footnotes</summary>
          <div class="disclose-body">${sheet.footnotes.map(f =>
            `<p style="font-size:12px;color:var(--muted);line-height:1.5;margin-top:8px">${esc(f)}</p>`
          ).join('')}</div>`;
        body.appendChild(det);
      }

      if (sheet.labels && sheet.labels.length) {
        const det = document.createElement('details');
        det.className = 'disclose';
        det.innerHTML = `<summary>${icon('checklist', 17)} How this is graded</summary>
          <div class="disclose-body">${sheet.labels.map(([g, t]) =>
            `<div style="display:flex;gap:10px;font-size:12.5px;padding:6px 0;
              border-bottom:1px solid var(--border)"><b style="min-width:74px">${esc(g)}</b>
              <span style="color:var(--muted)">${esc(t)}</span></div>`).join('')}</div>`;
        body.appendChild(det);
      }
    },
  });
}


/* ══ 8. GCN HSI ══════════════════════════════════════════════════════════
 * Port of gcn_hsi_data: interp, graph_si, compute_hsi, band_for.
 */

function interp(curve, x) {
  if (x <= curve[0][0]) return curve[0][1];
  if (x >= curve[curve.length - 1][0]) return curve[curve.length - 1][1];
  for (let i = 0; i < curve.length - 1; i++) {
    const [x0, y0] = curve[i], [x1, y1] = curve[i + 1];
    if (x0 <= x && x <= x1) {
      if (x1 === x0) return y1;
      return y0 + (x - x0) / (x1 - x0) * (y1 - y0);
    }
  }
  return curve[curve.length - 1][1];
}

/* Match Python's round(x, 3). Multiplying by 1000 first introduces float error
 * that Python does not have — 0.9275 * 1000 is 927.5000000000001 in JS, which
 * rounds up where Python rounds down. toFixed rounds the true double directly. */
function round3(v) { return Number(v.toFixed(3)); }

function graphSi(factor, measurement) {
  if (measurement == null) return null;
  if (factor.key === 'SI2') {
    if (measurement < 50) return 0.05;
    return round3(interp(factor.curve, measurement));
  }
  if (factor.key === 'SI8') {
    return round3(interp(factor.curve, measurement / BIOME.hsi.pond_density_divisor));
  }
  return round3(interp(factor.curve, measurement));
}

function computeHsi(values) {
  const used = [];
  let complete = true;
  BIOME.hsi.factors.forEach(f => {
    const v = values[f.key];
    if (v == null) { complete = false; return; }
    used.push(Math.max(0.01, Math.min(1.0, v)));
  });
  if (!complete || !used.length) return {hsi: null, n: used.length, complete: false};
  const prod = used.reduce((a, b) => a * b, 1.0);
  return {hsi: Math.pow(prod, 1.0 / used.length), n: used.length, complete: true};
}

function hsiBand(hsi) {
  if (hsi == null) return null;
  for (const [thr, label] of BIOME.hsi.bands) {
    if (thr == null || hsi < thr) return label;
  }
  return 'Excellent';
}

function siClass(si) {
  if (si == null) return '';
  return si >= 0.8 ? 'good' : si >= 0.5 ? 'warn' : 'bad';
}

/* Draw the digitised curve as inline SVG with a live marker. Better than the
 * desktop's reference PNGs here: it themes, it scales, and it shows where the
 * surveyor's own value lands. */
function curveSvg(factor, value) {
  const W = 300, H = 92, PADL = 26, PADB = 16, PADT = 6, PADR = 6;
  const xs = factor.curve.map(p => p[0]);
  const lo = factor.log ? Math.log10(Math.max(xs[0], 0.05)) : Math.min(...xs);
  const hi = factor.log ? Math.log10(Math.max(...xs)) : Math.max(...xs);
  const mx = v => {
    const t = factor.log ? Math.log10(Math.max(v, 0.05)) : v;
    return PADL + (t - lo) / (hi - lo || 1) * (W - PADL - PADR);
  };
  const my = si => PADT + (1 - si) * (H - PADT - PADB);
  const pts = factor.curve.map(([x, si]) => `${mx(x).toFixed(1)},${my(si).toFixed(1)}`).join(' ');
  const accent = getCSS('--accent'), border = getCSS('--border'), faint = getCSS('--faint');

  let marker = '';
  if (value != null) {
    const plotted = factor.key === 'SI8' ? value / BIOME.hsi.pond_density_divisor : value;
    const si = graphSi(factor, value);
    const cx = mx(Math.min(Math.max(plotted, factor.curve[0][0]), factor.curve[factor.curve.length - 1][0]));
    const cy = my(si);
    marker = `<line x1="${cx}" y1="${my(0)}" x2="${cx}" y2="${cy}" stroke="${accent}"
                stroke-width="1" stroke-dasharray="3 3"/>
              <circle cx="${cx}" cy="${cy}" r="4.5" fill="${accent}" stroke="var(--card)" stroke-width="2"/>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;margin-top:10px">
    <line x1="${PADL}" y1="${my(1)}" x2="${W - PADR}" y2="${my(1)}" stroke="${border}" stroke-width="1"/>
    <line x1="${PADL}" y1="${my(0.5)}" x2="${W - PADR}" y2="${my(0.5)}" stroke="${border}" stroke-width="1" stroke-dasharray="2 4"/>
    <line x1="${PADL}" y1="${my(0)}" x2="${W - PADR}" y2="${my(0)}" stroke="${border}" stroke-width="1"/>
    <text x="2" y="${my(1) + 4}" fill="${faint}" font-size="9">1.0</text>
    <text x="2" y="${my(0) + 4}" fill="${faint}" font-size="9">0</text>
    <polyline points="${pts}" fill="none" stroke="${faint}" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>
    ${marker}
    <text x="${W / 2}" y="${H - 3}" fill="${faint}" font-size="9" text-anchor="middle">${esc(factor.xlabel || '')}</text>
  </svg>`;
}

/* A pond is a place before it is a score: you are standing at it, so the pin
 * comes first and the ten indices follow. Editing an existing record skips
 * straight to the indices. */
function openHsi(existing) {
  if (existing) { openHsiIndices(existing, true); return; }

  const r = newRecord('hsi');
  r.geomType = 'point';
  r.label = autoLabel('Pond');

  const go = mode => {
    r._afterPlace = rec => openHsiIndices(rec, false);
    pending = r;
    placePoint(mode);
  };

  Sheet.push({
    title: 'Which pond?',
    sub: 'Mark where it is, then score it',
    render(body) {
      body.appendChild(field('Pond name', () => {
        const i = document.createElement('input');
        i.className = 'input';
        i.value = r.pondName || '';
        i.placeholder = 'e.g. Pond 1';
        i.oninput = () => { r.pondName = i.value; r.label = i.value || r.label; };
        return i;
      }, 'Optional — the pin and the score are what matter.'));

      const primary = document.createElement('button');
      primary.className = 'place-primary';
      primary.innerHTML = `${icon('target', 26)}<span>
        <span class="pp-main">I'm standing at the pond</span>
        <span class="pp-sub">Drops a pin at my GPS position</span></span>`;
      primary.onclick = () => go('gps');
      body.appendChild(primary);

      const alt = document.createElement('div');
      alt.className = 'place-alt';
      const tapB = document.createElement('button');
      tapB.innerHTML = icon('click', 19) + '<span>Tap the map</span>';
      tapB.onclick = () => go('tap');
      const centreB = document.createElement('button');
      centreB.innerHTML = icon('map-pin', 19) + '<span>Map centre</span>';
      centreB.onclick = () => go('centre');
      const noneB = document.createElement('button');
      noneB.innerHTML = icon('minus', 19) + '<span>No pin</span>';
      noneB.onclick = () => {
        r.geomType = null; r.geom = null;
        Sheet.close();
        addRecord(r);
        buzz();
        openHsiIndices(r, false);
      };
      alt.appendChild(tapB); alt.appendChild(centreB); alt.appendChild(noneB);
      body.appendChild(alt);
    },
  });
}

/* The ten suitability indices. The record already exists and is on the map by
 * the time this opens, so Save only ever updates it. */
function openHsiIndices(r, isEdit) {
  const state = r.hsiState ? Object.assign({}, r.hsiState) : {};
  const inputs = r.hsiInputs ? Object.assign({}, r.hsiInputs) : {};

  Sheet.push({
    title: r.pondName || 'Pond HSI',
    sub: 'Great crested newt habitat suitability index'
         + (r.gridref ? ' · ' + r.gridref : ''),
    foot(f) {
      const save = document.createElement('button');
      save.className = 'btn primary';
      save.innerHTML = icon('check', 18) + ' Save';
      save.onclick = () => {
        const res = computeHsi(state);
        r.hsiState = state;
        r.hsiInputs = inputs;
        r.hsiScore = res.complete ? res.hsi : null;
        r.hsiBand = res.complete ? hsiBand(res.hsi) : '';
        if (r.pondName) r.label = r.pondName;
        persist();
        drawRecord(r);
        if (isEdit) { Sheet.back(); toast('✓ Saved'); return; }
        Sheet.close();
        toast(res.complete
          ? `✓ ${r.pondName || 'Pond'} · HSI ${res.hsi.toFixed(2)} — ${hsiBand(res.hsi)}`
          : `Saved — ${10 - res.n} more ${10 - res.n === 1 ? 'index' : 'indices'} needed for a score`,
          {ms: 5000});
      };
      f.appendChild(save);
    },
    render(body) {
      const result = document.createElement('div');
      result.className = 'hsi-result';
      body.appendChild(result);

      const refresh = () => {
        const res = computeHsi(state);
        if (res.complete) {
          const band = hsiBand(res.hsi);
          const cls = res.hsi >= 0.7 ? 'good' : res.hsi >= 0.5 ? 'warn' : 'bad';
          result.innerHTML = `<div class="hsi-score num">${res.hsi.toFixed(2)}</div>
            <div class="hsi-band pill ${cls}">${esc(band)}</div>
            <div class="hsi-meta">Geometric mean of all 10 suitability indices</div>`;
        } else {
          const set = BIOME.hsi.factors.filter(f => state[f.key] != null).length;
          result.innerHTML = `<div class="hsi-score num" style="color:var(--faint)">—</div>
            <div class="hsi-meta">${set} of 10 indices set — every index is required</div>`;
        }
      };

      body.appendChild(field('Pond name', () => {
        const i = document.createElement('input');
        i.className = 'input'; i.value = r.pondName || '';
        i.placeholder = 'e.g. Pond 1';
        i.oninput = () => { r.pondName = i.value; };
        return i;
      }));

      BIOME.hsi.factors.forEach(f => {
        const card = document.createElement('div');
        card.className = 'si-card';
        const head = document.createElement('div');
        head.className = 'si-head';
        head.innerHTML = `<span class="si-num">${esc(f.key)}</span>
          <span class="si-title">${esc(f.title)}</span>
          <span class="si-val" id="siv-${f.key}">—</span>`;
        card.appendChild(head);
        const valEl = () => head.querySelector('.si-val');

        const setVal = si => {
          const v = valEl();
          v.className = 'si-val ' + siClass(si);
          v.textContent = si == null ? '—' : si.toFixed(2);
        };

        if (f.type === 'cat') {
          const opts = document.createElement('div');
          opts.className = 'si-opts';
          f.options.forEach(([label, si, help]) => {
            const b = document.createElement('button');
            b.innerHTML = `<span>${esc(label)}</span><span class="si-w num">${si.toFixed(2)}</span>`;
            b.title = help || '';
            if (state[f.key] === si) b.classList.add('on');
            b.onclick = () => {
              state[f.key] = si;
              opts.querySelectorAll('button').forEach(x => x.classList.remove('on'));
              b.classList.add('on');
              setVal(si); refresh();
            };
            opts.appendChild(b);
          });
          card.appendChild(opts);
          setVal(state[f.key] != null ? state[f.key] : null);
        } else {
          const step = document.createElement('div');
          step.className = 'stepper';
          const minus = document.createElement('button'); minus.textContent = '−';
          const input = document.createElement('input');
          input.type = 'number'; input.inputMode = 'decimal';
          input.placeholder = f.input_label + (f.unit ? ' (' + f.unit + ')' : '');
          if (inputs[f.key] != null) input.value = inputs[f.key];
          const plus = document.createElement('button'); plus.textContent = '+';
          step.appendChild(minus); step.appendChild(input); step.appendChild(plus);
          card.appendChild(step);

          const plot = document.createElement('div');
          card.appendChild(plot);

          const extra = document.createElement('div');
          extra.className = 'si-note';
          card.appendChild(extra);

          const apply = () => {
            const raw = input.value.trim();
            const v = raw === '' ? null : parseFloat(raw);
            inputs[f.key] = raw === '' ? null : v;
            const si = (v == null || isNaN(v)) ? null : graphSi(f, v);
            state[f.key] = si;
            setVal(si);
            plot.innerHTML = curveSvg(f, (v == null || isNaN(v)) ? null : v);
            if (f.count && v != null && !isNaN(v)) {
              extra.textContent = '= ' + (v / BIOME.hsi.pond_density_divisor).toFixed(2) + ' ponds / km²';
            } else {
              extra.textContent = f.note || '';
            }
            refresh();
          };
          minus.onclick = () => {
            const cur = parseFloat(input.value || '0') || 0;
            input.value = Math.max(0, cur - f.step);
            apply();
          };
          plus.onclick = () => {
            const cur = parseFloat(input.value || '0') || 0;
            input.value = Math.min(f.maxinput, cur + f.step);
            apply();
          };
          input.oninput = apply;
          apply();
        }

        if (f.note && f.type === 'cat') {
          const n = document.createElement('div');
          n.className = 'si-note';
          n.textContent = f.note;
          card.appendChild(n);
        }
        body.appendChild(card);
      });

      refresh();
    },
  });
}


/* ══ 9. Records browser ══════════════════════════════════════════════════ */

function openRecords() {
  setActiveTab('records');
  Sheet.push({
    title: 'Records',
    sub: records.length + ' captured',
    search: 'Search records…',
    chips: [{label: 'All'}, {label: 'Habitats'}, {label: 'Features'},
            {label: 'Assessments'}, {label: 'Photos'}],
    render(body, q, chip) {
      let list = records.slice().reverse();
      if (chip === 1) list = list.filter(r => r.kind === 'habitat');
      else if (chip === 2) list = list.filter(r => r.kind === 'eco');
      else if (chip === 3) list = list.filter(r => r.kind === 'condition' || r.kind === 'hsi');
      else if (chip === 4) list = list.filter(r => r.photos && r.photos.length);
      if (q) list = list.filter(r => matchesQuery(
        [recTitle(r), r.label, r.notes, r.gridref, r.condition].join(' '), q));

      if (!list.length) {
        emptyState(body, 'list', records.length ? 'No matches' : 'Nothing recorded yet',
          records.length ? 'Try a different filter or search word.'
                         : 'Tap Add to place your first record. Everything is saved on this phone as you go.');
        return;
      }
      list.forEach(r => {
        const card = document.createElement('div');
        card.className = 'rec-card';
        const thumb = r.photos && r.photos.length
          ? `<img class="rec-thumb" src="${r.photos[0].data}" alt=""/>`
          : `<span class="row-icon" style="width:42px;height:42px">${
              r.kind === 'habitat' && BIOME.habitats.meta[r.subType]
                ? `<span class="swatch" style="background:${BIOME.habitats.meta[r.subType].color}"></span>`
                : (BIOME.features.point_meta[r.ftype] || {}).svg
                  ? BIOME.features.point_meta[r.ftype].svg.replace('<svg', '<svg width="22" height="22"')
                  : icon(r.kind === 'hsi' ? 'droplet' : r.kind === 'condition' ? 'checklist'
                       : GEOM_ICON[r.geomType] || 'map-pin', 20)}</span>`;
        card.innerHTML = `${thumb}<div class="rec-info">
            <div class="rec-type">${esc(recTitle(r))}</div>
            <div class="rec-meta">${esc(recSubtitle(r) || r.gridref || '')}</div>
          </div><span class="row-tail">${icon('chevron-right', 17)}</span>`;
        card.onclick = () => openRecordSheet(r);
        body.appendChild(card);
      });
    },
  });
}

function openRecordSheet(r) {
  Sheet.push({
    title: recTitle(r),
    sub: [r.label, r.gridref, r.measure].filter(Boolean).join(' · '),
    foot(f) {
      if (r.geom) {
        const zoom = document.createElement('button');
        zoom.className = 'btn secondary';
        zoom.innerHTML = icon('map', 18) + ' Show';
        zoom.onclick = () => { Sheet.close(); zoomToRecord(r); };
        f.appendChild(zoom);
      }
      const del = document.createElement('button');
      del.className = 'btn danger';
      del.innerHTML = icon('trash', 18) + ' Delete';
      del.onclick = () => { Sheet.back(); removeRecord(r); Sheet.repaint(); };
      f.appendChild(del);
    },
    render(body) {
      const meta = [];
      meta.push(['Type', KIND_LABEL[r.kind]]);
      if (r.subType) meta.push(['Habitat', r.subType]);
      if (r.ftype) meta.push(['Feature', r.ftype]);
      if (r.condition) meta.push(['Condition', r.condition]);
      if (r.hsiScore != null) meta.push(['HSI', r.hsiScore.toFixed(2) + ' · ' + r.hsiBand]);
      if (r.measure) meta.push([r.geomType === 'area' ? 'Area' : 'Length', r.measure]);
      if (r.gridref) meta.push(['Grid ref', r.gridref]);
      if (r.accuracy != null) meta.push(['GPS accuracy', '±' + Math.round(r.accuracy) + ' m']);
      meta.push(['Recorded', new Date(r.timestamp).toLocaleString()]);
      if (!r.geom) meta.push(['Position', 'No pin — link in QGIS']);

      const table = document.createElement('div');
      table.innerHTML = meta.map(([k, v]) =>
        `<div style="display:flex;gap:12px;padding:9px 2px;border-bottom:1px solid var(--border);font-size:13px">
          <span style="color:var(--muted);min-width:104px">${esc(k)}</span>
          <span style="flex:1;text-align:right">${esc(v)}</span></div>`).join('');
      body.appendChild(table);

      if (r.conditionReport) {
        const p = document.createElement('p');
        p.style.cssText = 'font-size:12.5px;color:var(--muted);line-height:1.55;margin-top:12px';
        p.textContent = r.conditionReport;
        body.appendChild(p);
      }

      if (r.kind === 'hsi') {
        const b = document.createElement('button');
        b.className = 'btn secondary wide';
        b.style.marginTop = '12px';
        b.innerHTML = icon('calculator', 18) + ' Edit the HSI inputs';
        b.onclick = () => openHsi(r);
        body.appendChild(b);
      }
      if (r.kind !== 'hsi' && BIOME.condition.habitats[r.subType]) {
        const b = document.createElement('button');
        b.className = 'btn secondary wide';
        b.style.marginTop = '12px';
        b.innerHTML = icon('checklist', 18) + (r.conditionDetail ? ' Edit the assessment' : ' Assess condition');
        b.onclick = () => openCondition(r, () => { persist(); Sheet.repaint(); });
        body.appendChild(b);
      }

      body.appendChild(detailBlock(r));

      const save = document.createElement('button');
      save.className = 'btn primary wide';
      save.style.marginTop = '14px';
      save.innerHTML = icon('check', 18) + ' Save changes';
      save.onclick = () => { persist(); drawRecord(r); Sheet.back(); toast('✓ Saved'); };
      body.appendChild(save);
    },
  });
}

function zoomToRecord(r) {
  if (!r.geom) return;
  if (r.geomType === 'point') map.setView([r.geom.coordinates[1], r.geom.coordinates[0]], 19);
  else if (r._layer && r._layer.getBounds) map.fitBounds(r._layer.getBounds(), {padding: [60, 60]});
  if (r._layer && r._layer.openPopup) r._layer.openPopup();
}


/* ══ 10. Finish, export, settings ════════════════════════════════════════ */

function surveyFilename(ext) {
  const site = (SITE.name || 'survey').replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 40);
  const d = new Date().toISOString().slice(0, 10);
  return `biome_field_${site}_${d}.${ext}`;
}

function buildFeatureCollection() {
  const feats = records.map(r => ({
    type: 'Feature',
    geometry: r.geom || null,
    properties: {
      /* v1 keys kept so an old plugin build still imports the points. */
      feature_type: r.ftype || r.subType || '',
      label: r.label || '',
      photo: (r.photos && r.photos[0]) ? r.photos[0].name : '',
      timestamp: r.timestamp,

      biome_kind: r.kind,
      uuid: r.uuid,
      geom_type: r.geomType || '',
      module: r.module || '',
      habitat_type: r.habType || '',
      sub_habitat_type: r.subType || '',
      condition: r.condition || '',
      condition_detail: r.conditionDetail || '',
      condition_report: r.conditionReport || '',
      strategic_significance: r.kind === 'habitat' ? (r.strategic || '') : '',
      irreplaceable_habitat: r.kind === 'habitat' ? (r.irreplaceable || '') : '',
      watercourse_encroachment: r.waterEnc || '',
      riparian_encroachment_l: r.ripL || '',
      riparian_encroachment_r: r.ripR || '',
      habitat_ref: r.habRef || '',
      tree_size: r.treeSize || '',
      tree_location: r.treeLocation || '',
      notes: r.notes || '',
      gridref: r.gridref || '',
      gps_accuracy: r.accuracy == null ? '' : Math.round(r.accuracy * 10) / 10,
      surveyor: r.surveyor || Store.meta('surveyor') || '',
      photos: (r.photos || []).map(p => p.name).join('|'),
      hsi_score: r.hsiScore == null ? '' : Math.round(r.hsiScore * 1000) / 1000,
      hsi_band: r.hsiBand || '',
      hsi_detail: r.hsiInputs ? JSON.stringify(r.hsiInputs) : '',
      pond_name: r.pondName || '',
    },
  }));
  return {
    type: 'FeatureCollection',
    biome: {
      version: BIOME.version,
      site: SITE.name || '',
      ref: SITE.ref || '',
      surveyor: Store.meta('surveyor') || '',
      exported: new Date().toISOString(),
      records: records.length,
    },
    features: feats,
  };
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 4000);
  Store.meta('lastExport', new Date().toISOString());
  updateCounts();
}

function exportGeoJSON() {
  if (!records.length) { toast('Nothing to export yet'); return; }
  const fc = buildFeatureCollection();
  download(new Blob([JSON.stringify(fc, null, 2)], {type: 'application/geo+json'}),
           surveyFilename('geojson'));
  toast('✓ GeoJSON downloaded — import it in QGIS');
}

/* The ZIP is the one that carries photos, and it is the layout
 * _import_field_survey already understands: one .geojson with the image files
 * beside it. */
function exportZip() {
  if (!records.length) { toast('Nothing to export yet'); return; }
  const fc = buildFeatureCollection();
  const files = [{
    name: surveyFilename('geojson'),
    bytes: new TextEncoder().encode(JSON.stringify(fc, null, 2)),
  }];
  const used = new Set([files[0].name]);
  records.forEach(r => (r.photos || []).forEach(p => {
    let name = p.name;
    let i = 1;
    while (used.has(name)) name = p.name.replace(/\.jpg$/, '') + '_' + (++i) + '.jpg';
    used.add(name);
    if (name !== p.name) {
      // Keep the manifest honest when a collision forced a rename.
      const props = fc.features.find(f => f.properties.uuid === r.uuid);
      if (props) {
        props.properties.photos = props.properties.photos.replace(p.name, name);
        if (props.properties.photo === p.name) props.properties.photo = name;
      }
    }
    files.push({name, bytes: dataUrlToBytes(p.data)});
  }));
  // Re-encode the manifest after any renames.
  files[0].bytes = new TextEncoder().encode(JSON.stringify(fc, null, 2));

  const blob = Zip.build(files, new Date());
  download(blob, surveyFilename('zip'));
  toast('✓ ZIP downloaded — ' + (files.length - 1) + ' photo' +
        (files.length === 2 ? '' : 's') + ' included');
}

/* Push straight back into QGIS while the tunnel is still up. Saves the
 * download-then-find-the-file dance entirely. */
async function syncToQgis(btn) {
  if (!records.length) { toast('Nothing to send'); return; }
  const original = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = 'Sending…'; }
  try {
    const payload = buildFeatureCollection();
    payload.photos = {};
    records.forEach(r => (r.photos || []).forEach(p => { payload.photos[p.name] = p.data; }));
    const res = await fetch('sync', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const out = await res.json().catch(() => ({}));
    Store.meta('lastExport', new Date().toISOString());
    updateCounts();
    /* The server acknowledges receipt, not a completed import — QGIS reports
     * that in its own dialog. Say what actually happened. */
    toast('✓ QGIS received ' + (out.received != null ? out.received + ' record' +
          (out.received === 1 ? '' : 's') : 'the survey') +
          ' — check QGIS for the import result', {ms: 6000});
  } catch (e) {
    toast('Could not reach QGIS. Is it still open? Use Download instead.', {ms: 6000});
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = original; }
  }
}

function openExport() {
  setActiveTab('export');
  Sheet.push({
    title: 'Finish the survey',
    sub: records.length + ' record' + (records.length === 1 ? '' : 's') + ' on this phone',
    render(body) {
      const counts = {habitat: 0, eco: 0, condition: 0, hsi: 0, photos: 0};
      records.forEach(r => {
        counts[r.kind] = (counts[r.kind] || 0) + 1;
        counts.photos += (r.photos || []).length;
      });
      const grid = document.createElement('div');
      grid.className = 'tally-grid';
      [['Habitats', counts.habitat], ['Features', counts.eco],
       ['Assessments', counts.condition + counts.hsi], ['Photos', counts.photos]
      ].forEach(([l, n]) => {
        const t = document.createElement('div');
        t.className = 'tally-tile';
        t.innerHTML = `<div class="tt-n">${n}</div><div class="tt-l">${l}</div>`;
        grid.appendChild(t);
      });
      body.appendChild(grid);

      const missing = records.filter(r => r.kind === 'habitat' && !r.condition).length;
      if (missing) {
        const w = document.createElement('div');
        w.className = 'status-line off';
        w.style.marginTop = '8px';
        w.innerHTML = `<span class="sl-dot"></span>${missing} habitat${missing > 1 ? 's have' : ' has'}
          no condition — the metric will not score ${missing > 1 ? 'them' : 'it'}.`;
        body.appendChild(w);
      }

      body.appendChild(groupHeader('Send it back'));

      const sync = document.createElement('button');
      sync.className = 'btn primary wide';
      sync.innerHTML = icon('sync', 18) + ' Send straight to QGIS';
      sync.onclick = () => syncToQgis(sync);
      body.appendChild(sync);
      const syncHint = document.createElement('div');
      syncHint.className = 'hint';
      syncHint.style.margin = '6px 2px 0';
      syncHint.textContent = 'Works while QGIS is still open on the same link you scanned. ' +
        'Photos travel with it.';
      body.appendChild(syncHint);

      const zip = document.createElement('button');
      zip.className = 'btn secondary wide';
      zip.style.marginTop = '10px';
      zip.innerHTML = icon('download', 18) + ' Download ZIP (with photos)';
      zip.onclick = exportZip;
      body.appendChild(zip);

      const gj = document.createElement('button');
      gj.className = 'btn secondary wide';
      gj.style.marginTop = '8px';
      gj.innerHTML = icon('file-text', 18) + ' Download GeoJSON only';
      gj.onclick = exportGeoJSON;
      body.appendChild(gj);

      const last = Store.meta('lastExport');
      const st = document.createElement('div');
      st.className = 'status-line ' + (last ? 'ok' : '');
      st.style.marginTop = '12px';
      st.innerHTML = `<span class="sl-dot"></span>${last
        ? 'Last sent ' + new Date(last).toLocaleString()
        : 'Not yet sent anywhere'}`;
      body.appendChild(st);

      body.appendChild(groupHeader('Danger zone'));
      const clear = document.createElement('button');
      clear.className = 'btn danger wide';
      clear.innerHTML = icon('trash', 18) + ' Clear this survey';
      clear.onclick = () => {
        if (!records.length) { toast('Already empty'); return; }
        Sheet.push({
          title: 'Clear the whole survey?',
          sub: 'This cannot be undone',
          render(b2) {
            const warn = document.createElement('div');
            warn.className = 'status-line off';
            warn.innerHTML = `<span class="sl-dot"></span>${records.length} records and
              ${counts.photos} photos will be deleted from this phone.`;
            b2.appendChild(warn);
            if (!last) {
              const w2 = document.createElement('div');
              w2.className = 'status-line off';
              w2.style.marginTop = '8px';
              w2.innerHTML = `<span class="sl-dot"></span>You have not sent this survey to
                QGIS yet. Send or download it first.`;
              b2.appendChild(w2);
            }
            const go = document.createElement('button');
            go.className = 'btn danger wide';
            go.style.marginTop = '14px';
            go.textContent = 'Yes, delete everything';
            go.onclick = async () => {
              records.forEach(r => { if (r._layer) r._layer.remove(); });
              records = []; seqCounter = 0;
              await Store.wipe();
              Store.meta('lastExport', '');
              updateCounts(); Sheet.close(); toast('Survey cleared');
            };
            b2.appendChild(go);
          },
        });
      };
      body.appendChild(clear);
    },
  });
}

function openMore() {
  setActiveTab('more');
  Sheet.push({
    title: 'Settings',
    sub: SITE.name || 'Biome Field Mapper',
    render(body) {
      body.appendChild(field('Surveyor', () => {
        const i = document.createElement('input');
        i.className = 'input';
        i.value = Store.meta('surveyor') || '';
        i.placeholder = 'Your name — stamped on every record';
        i.oninput = () => Store.meta('surveyor', i.value);
        return i;
      }));

      body.appendChild(field('Display mode', () => {
        const cur = Store.meta('theme') || 'dark';
        const d = document.createElement('div');
        THEMES.forEach(t => {
          const b = document.createElement('button');
          b.className = 'row';
          b.style.marginBottom = '6px';
          b.innerHTML = rowHTML({
            name: t.name, sub: t.sub, icon: 'contrast',
            pill: t.id === cur ? 'On' : '', pillClass: 'accent', chevron: false,
          });
          b.onclick = () => { setTheme(t.id); Sheet.repaint(); };
          d.appendChild(b);
        });
        return d;
      }));

      body.appendChild(field('Map labels', () => seg(
        ['Show', 'Hide'], labelsOn() ? 'Show' : 'Hide',
        v => { Store.meta('labels', v === 'Show'); redrawAll(); })));

      body.appendChild(groupHeader('Offline'));
      const cache = document.createElement('button');
      cache.className = 'btn secondary wide';
      cache.innerHTML = icon('download', 18) + ' Save this site’s maps for offline';
      cache.onclick = () => cacheTiles(cache);
      body.appendChild(cache);
      const oh = document.createElement('div');
      oh.className = 'hint';
      oh.style.margin = '6px 2px 0';
      oh.textContent = 'Do this before you leave signal. The app itself already works offline ' +
        'once loaded — this adds the map tiles around the redline.';
      body.appendChild(oh);

      body.appendChild(groupHeader('Storage'));
      const st = document.createElement('div');
      st.className = 'status-line ok';
      st.innerHTML = `<span class="sl-dot"></span>Saving automatically as you go`;
      body.appendChild(st);
      if (navigator.storage && navigator.storage.estimate) {
        navigator.storage.estimate().then(e => {
          const used = (e.usage || 0) / 1048576;
          st.innerHTML = `<span class="sl-dot"></span>${used.toFixed(1)} MB used · saving automatically`;
        }).catch(() => {});
      }

      body.appendChild(groupHeader('About'));
      const about = document.createElement('div');
      about.style.cssText = 'font-size:12.5px;color:var(--muted);line-height:1.6;padding:4px 2px';
      about.innerHTML = `Catalogue built from this project: ${
        Object.keys(BIOME.features.point_meta).length} point features,
        ${Object.keys(BIOME.features.line_meta).length} line, ${
        Object.keys(BIOME.features.area_meta).length} area, ${
        Object.keys(BIOME.habitats.meta).length} habitats, ${
        Object.keys(BIOME.condition.sheets).length} condition sheets.
        Payload v${BIOME.version}.`;
      body.appendChild(about);
    },
  });
}

/* Pre-fetch tiles around the redline so panning works with no signal. The
 * service worker caches whatever the page requests, so simply fetching them
 * is enough. */
async function cacheTiles(btn) {
  if (!redlineLayer.getLayers().length) { toast('No redline to cache'); return; }
  const b = redlineLayer.getBounds().pad(0.35);
  const spec = BASEMAPS.find(x => x.id === (Store.meta('basemap') || 'sat')) || BASEMAPS[0];
  const urls = [];
  const lat2y = (lat, z) => Math.floor((1 - Math.log(Math.tan(lat * Math.PI / 180) +
    1 / Math.cos(lat * Math.PI / 180)) / Math.PI) / 2 * Math.pow(2, z));
  const lon2x = (lon, z) => Math.floor((lon + 180) / 360 * Math.pow(2, z));

  for (let z = 14; z <= Math.min(19, spec.max); z++) {
    const x0 = lon2x(b.getWest(), z), x1 = lon2x(b.getEast(), z);
    const y0 = lat2y(b.getNorth(), z), y1 = lat2y(b.getSouth(), z);
    for (let x = x0; x <= x1; x++)
      for (let y = y0; y <= y1; y++)
        urls.push(spec.url.replace('{z}', z).replace('{x}', x).replace('{y}', y).replace('{s}', 'a'));
  }
  if (urls.length > 3000) urls.length = 3000;

  const original = btn.innerHTML;
  btn.disabled = true;
  let done = 0, failed = 0;
  const batch = 12;
  for (let i = 0; i < urls.length; i += batch) {
    await Promise.all(urls.slice(i, i + batch).map(u =>
      fetch(u, {mode: 'no-cors'}).then(() => { done++; }).catch(() => { failed++; })));
    btn.innerHTML = `Caching… ${Math.round((done + failed) / urls.length * 100)}%`;
  }
  btn.disabled = false;
  btn.innerHTML = original;
  toast(`✓ ${done} map tiles saved for offline use`, {ms: 5000});
}


/* ══ Wiring ══════════════════════════════════════════════════════════════ */

document.getElementById('fab').onclick = openAddSheet;
document.getElementById('tab-map').onclick = () => { Sheet.close(); setActiveTab('map'); };
document.getElementById('tab-records').onclick = openRecords;
document.getElementById('tab-export').onclick = openExport;
document.getElementById('tab-more').onclick = openMore;

document.getElementById('gps-btn').onclick = () => {
  const btn = document.getElementById('gps-btn');
  if (GPS.pos && GPS.follow) {
    GPS.follow = false; btn.classList.remove('on'); toast('Follow off');
  } else if (GPS.pos) {
    GPS.follow = true; btn.classList.add('on'); map.setView(GPS.pos, Math.max(map.getZoom(), 18));
    toast('Following your position');
  } else {
    startGps(pos => { map.setView(pos, 18); GPS.follow = true; btn.classList.add('on'); });
  }
};
document.getElementById('gps-chip').onclick = () => {
  if (!GPS.pos) { startGps(pos => map.setView(pos, 18)); return; }
  toast(`±${Math.round(GPS.acc)} m · ${GridRef.format(GPS.pos[0], GPS.pos[1]) || 'outside GB'}`, {ms: 5000});
};
/* A drag that ends in a tap must not place a feature. */
map.on('dragstart', () => { map._userDragged = true; });
map.on('dragend', () => { setTimeout(() => { map._userDragged = false; }, 120); });

document.getElementById('theme-btn').onclick = () => {
  const cur = Store.meta('theme') || 'dark';
  const i = THEMES.findIndex(t => t.id === cur);
  const next = THEMES[(i + 1) % THEMES.length];
  setTheme(next.id);
  toast(next.name + ' mode');
};

document.getElementById('layers-btn').onclick = () => {
  Sheet.push({
    title: 'Basemap',
    render(body) {
      const cur = Store.meta('basemap') || 'sat';
      BASEMAPS.forEach(b => {
        body.appendChild(makeRow({
          name: b.name, sub: b.attr, icon: 'layers',
          pill: b.id === cur ? 'On' : '', pillClass: 'accent', chevron: false,
        }, () => { setBasemap(b.id); Sheet.repaint(); }));
      });
    },
  });
};

/* Escape and the hardware back button cancel whatever is armed. */
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (draw) { endDraw(); toast('Drawing cancelled'); }
  else if (_tapArmed) { disarmTap(); toast('Cancelled'); }
  else if (assessPick) { disarmAssessPick(); toast('Cancelled'); }
  else if (Sheet.isOpen()) Sheet.back();
});

/* Losing a survey to a mistaken swipe-close is unacceptable. */
window.addEventListener('beforeunload', e => {
  if (records.length && !Store.meta('lastExport')) {
    e.preventDefault();
    e.returnValue = '';
  }
});

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') Store.putAll(records);
});


/* ══ Boot ════════════════════════════════════════════════════════════════ */

(async function boot() {
  ['brand-mark:leaf', 'ic-map:map', 'ic-list:list', 'ic-download:download',
   'ic-settings:settings', 'ic-layers:layers', 'ic-target:target', 'ic-theme:contrast']
    .forEach(pair => { const [id, name] = pair.split(':'); fillIcon(id, name); });

  setTheme(Store.meta('theme') || 'dark');
  setBasemap(Store.meta('basemap') || 'sat');

  document.getElementById('site-name').textContent = SITE.name || 'Biome Field Mapper';

  redlineLayer.addTo(map);
  const hasRedline = redlineLayer.getLayers().length > 0;
  if (hasRedline) map.fitBounds(redlineLayer.getBounds(), {padding: [50, 50]});
  else map.setView([SITE.lat || 54.0, SITE.lon || -2.0], 15);

  // Restore anything captured before the tab was reloaded.
  const saved = await Store.loadAll();
  if (saved.length) {
    records = saved;
    seqCounter = saved.reduce((m, r) => Math.max(m, r.seq || 0), 0);
    records.forEach(drawRecord);
    toast(`Restored ${records.length} record${records.length === 1 ? '' : 's'}`, {ms: 3500});
  }
  updateCounts();

  startGps();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* http:// LAN fallback */ });
  }
})();
