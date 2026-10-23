/* Biome Field Mapper — core utilities.
 *
 * Dependency-free helpers shared by the rest of the app: persistent storage,
 * geodesic measurement, a STORE-only ZIP writer (so a survey exports as the
 * .zip that _import_field_survey already understands, photos included), and
 * small DOM/format helpers.
 *
 * Nothing here touches the feature catalogue — see app.js for the UI.
 */

/* ── Storage ────────────────────────────────────────────────────────────────
 * Records are persisted on every mutation. A field survey that loses its data
 * to an accidental refresh is worse than no survey at all, and phones reload
 * backgrounded tabs aggressively.
 *
 * IndexedDB holds the photo blobs (localStorage caps out around 5 MB, which is
 * two photos); localStorage holds a lightweight mirror of everything else so a
 * cold start can render the list before IndexedDB opens.
 */
const Store = (() => {
  const DB_NAME = 'biome-field-mapper';
  const STORE   = 'records';
  const LS_KEY  = 'biome.fm.records.v2';
  const LS_META = 'biome.fm.meta.v2';
  let db = null;
  let opening = null;

  function open() {
    if (db) return Promise.resolve(db);
    if (opening) return opening;
    opening = new Promise((resolve, reject) => {
      if (!window.indexedDB) { reject(new Error('no indexeddb')); return; }
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = e => {
        const d = e.target.result;
        if (!d.objectStoreNames.contains(STORE)) d.createObjectStore(STORE, {keyPath: 'uuid'});
      };
      req.onsuccess = e => { db = e.target.result; resolve(db); };
      req.onerror   = () => reject(req.error);
    });
    return opening;
  }

  /* Strip the transient map-layer handle before anything is serialised. */
  function clean(rec) {
    const out = {};
    for (const k in rec) if (k[0] !== '_') out[k] = rec[k];
    return out;
  }

  async function putAll(records) {
    // localStorage mirror without photo payloads — small and synchronous.
    try {
      const light = records.map(r => {
        const c = clean(r);
        if (c.photos) c.photos = c.photos.map(p => ({name: p.name, w: p.w, h: p.h}));
        return c;
      });
      localStorage.setItem(LS_KEY, JSON.stringify(light));
    } catch (e) { /* quota — IndexedDB is the real store */ }

    try {
      const d = await open();
      await new Promise((resolve, reject) => {
        const tx = d.transaction(STORE, 'readwrite');
        const os = tx.objectStore(STORE);
        os.clear();
        records.forEach(r => os.put(clean(r)));
        tx.oncomplete = resolve;
        tx.onerror    = () => reject(tx.error);
      });
    } catch (e) { /* fall back to the localStorage mirror */ }
  }

  async function loadAll() {
    try {
      const d = await open();
      const rows = await new Promise((resolve, reject) => {
        const tx  = d.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).getAll();
        req.onsuccess = () => resolve(req.result || []);
        req.onerror   = () => reject(req.error);
      });
      if (rows.length) return rows.sort((a, b) => (a.seq || 0) - (b.seq || 0));
    } catch (e) { /* fall through */ }
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* nothing recoverable */ }
    return [];
  }

  function meta(key, val) {
    let m = {};
    try { m = JSON.parse(localStorage.getItem(LS_META) || '{}'); } catch (e) { m = {}; }
    if (val === undefined) return m[key];
    m[key] = val;
    try { localStorage.setItem(LS_META, JSON.stringify(m)); } catch (e) { /* full */ }
    return val;
  }

  async function wipe() {
    try { localStorage.removeItem(LS_KEY); } catch (e) { /* ignore */ }
    try {
      const d = await open();
      await new Promise(resolve => {
        const tx = d.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).clear();
        tx.oncomplete = resolve;
        tx.onerror    = resolve;
      });
    } catch (e) { /* ignore */ }
  }

  return {putAll, loadAll, meta, wipe};
})();


/* ── Geodesy ────────────────────────────────────────────────────────────────
 * Lengths and areas are measured on the sphere, never in screen or Mercator
 * space — at UK latitudes planar maths on Web Mercator overstates area by
 * ~2.6x. These are field indicators, so the ~0.3% sphere-vs-ellipsoid gap is
 * immaterial; QGIS re-measures ellipsoidally on import.
 */
const Geo = (() => {
  const R = 6371008.8;                 // IUGG mean earth radius, metres
  const rad = d => d * Math.PI / 180;

  /* Haversine distance between two [lat, lon] pairs, in metres. */
  function distance(a, b) {
    const dLat = rad(b[0] - a[0]), dLon = rad(b[1] - a[1]);
    const s = Math.sin(dLat / 2) ** 2 +
              Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
  }

  /* Total length of a [lat, lon] vertex list, in metres. */
  function length(latlngs) {
    let t = 0;
    for (let i = 1; i < latlngs.length; i++) t += distance(latlngs[i - 1], latlngs[i]);
    return t;
  }

  /* Spherical-excess area of a closed ring of [lat, lon] pairs, in m². */
  function area(latlngs) {
    const n = latlngs.length;
    if (n < 3) return 0;
    let total = 0;
    for (let i = 0; i < n; i++) {
      const p1 = latlngs[i], p2 = latlngs[(i + 1) % n];
      total += rad(p2[1] - p1[1]) * (2 + Math.sin(rad(p1[0])) + Math.sin(rad(p2[0])));
    }
    return Math.abs(total * R * R / 2);
  }

  /* Initial bearing from a to b, in degrees clockwise from true north. */
  function bearing(a, b) {
    const y = Math.sin(rad(b[1] - a[1])) * Math.cos(rad(b[0]));
    const x = Math.cos(rad(a[0])) * Math.sin(rad(b[0])) -
              Math.sin(rad(a[0])) * Math.cos(rad(b[0])) * Math.cos(rad(b[1] - a[1]));
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
  }

  function fmtLength(m) {
    if (m < 1000) return m.toFixed(m < 10 ? 1 : 0) + ' m';
    return (m / 1000).toFixed(2) + ' km';
  }

  function fmtArea(m2) {
    if (m2 < 10000) return m2.toFixed(m2 < 100 ? 1 : 0) + ' m²';
    return (m2 / 10000).toFixed(m2 < 100000 ? 3 : 2) + ' ha';
  }

  return {distance, length, area, bearing, fmtLength, fmtArea};
})();


/* ── OS grid reference ──────────────────────────────────────────────────────
 * WGS84 -> OSGB36 National Grid, so a record reads the way an ecologist would
 * write it in a notebook. Helmert transform: metre-level, which is finer than
 * a phone's GPS fix and finer than the 10-figure reference it prints.
 */
const GridRef = (() => {
  const LETTERS = 'ABCDEFGHJKLMNOPQRSTUVWXYZ';   // no I

  function toOSGB36(lat, lon) {
    const toRad = d => d * Math.PI / 180;
    // WGS84 ellipsoid -> cartesian
    const a1 = 6378137, f1 = 1 / 298.257223563;
    const e2_1 = 2 * f1 - f1 * f1;
    const phi = toRad(lat), lam = toRad(lon), H = 0;
    const nu = a1 / Math.sqrt(1 - e2_1 * Math.sin(phi) ** 2);
    let x = (nu + H) * Math.cos(phi) * Math.cos(lam);
    let y = (nu + H) * Math.cos(phi) * Math.sin(lam);
    let z = ((1 - e2_1) * nu + H) * Math.sin(phi);

    // Helmert WGS84 -> OSGB36
    const tx = -446.448, ty = 125.157, tz = -542.060;
    const rx = toRad(-0.1502 / 3600), ry = toRad(-0.2470 / 3600), rz = toRad(-0.8421 / 3600);
    const s  = 20.4894e-6;
    const x2 = tx + x * (1 + s) - y * rz + z * ry;
    const y2 = ty + x * rz + y * (1 + s) - z * rx;
    const z2 = tz - x * ry + y * rx + z * (1 + s);

    // Airy 1830 cartesian -> geodetic
    const a2 = 6377563.396, f2 = 1 / 299.3249646;
    const e2_2 = 2 * f2 - f2 * f2;
    const p = Math.sqrt(x2 * x2 + y2 * y2);
    let phi2 = Math.atan2(z2, p * (1 - e2_2)), phiP = 2 * Math.PI;
    let nu2 = a2;
    while (Math.abs(phi2 - phiP) > 1e-11) {
      nu2 = a2 / Math.sqrt(1 - e2_2 * Math.sin(phi2) ** 2);
      phiP = phi2;
      phi2 = Math.atan2(z2 + e2_2 * nu2 * Math.sin(phi2), p);
    }
    return {lat: phi2, lon: Math.atan2(y2, x2)};
  }

  /* Airy 1830 / transverse Mercator -> National Grid eastings + northings. */
  function toEN(lat, lon) {
    const g = toOSGB36(lat, lon);
    const a = 6377563.396, b = 6356256.909, F0 = 0.9996012717;
    const phi0 = 49 * Math.PI / 180, lam0 = -2 * Math.PI / 180;
    const N0 = -100000, E0 = 400000;
    const e2 = 1 - (b * b) / (a * a);
    const n = (a - b) / (a + b), n2 = n * n, n3 = n2 * n;
    const phi = g.lat, lam = g.lon;
    const cosPhi = Math.cos(phi), sinPhi = Math.sin(phi);
    const nu   = a * F0 / Math.sqrt(1 - e2 * sinPhi * sinPhi);
    const rho  = a * F0 * (1 - e2) / Math.pow(1 - e2 * sinPhi * sinPhi, 1.5);
    const eta2 = nu / rho - 1;

    const Ma = (1 + n + 1.25 * n2 + 1.25 * n3) * (phi - phi0);
    const Mb = (3 * n + 3 * n2 + 2.625 * n3) * Math.sin(phi - phi0) * Math.cos(phi + phi0);
    const Mc = (1.875 * n2 + 1.875 * n3) * Math.sin(2 * (phi - phi0)) * Math.cos(2 * (phi + phi0));
    const Md = (35 / 24) * n3 * Math.sin(3 * (phi - phi0)) * Math.cos(3 * (phi + phi0));
    const M  = b * F0 * (Ma - Mb + Mc - Md);

    const I    = M + N0;
    const II   = (nu / 2) * sinPhi * cosPhi;
    const III  = (nu / 24) * sinPhi * Math.pow(cosPhi, 3) * (5 - Math.tan(phi) ** 2 + 9 * eta2);
    const IIIA = (nu / 720) * sinPhi * Math.pow(cosPhi, 5) * (61 - 58 * Math.tan(phi) ** 2 + Math.pow(Math.tan(phi), 4));
    const IV   = nu * cosPhi;
    const V    = (nu / 6) * Math.pow(cosPhi, 3) * (nu / rho - Math.tan(phi) ** 2);
    const VI   = (nu / 120) * Math.pow(cosPhi, 5) *
                 (5 - 18 * Math.tan(phi) ** 2 + Math.pow(Math.tan(phi), 4) + 14 * eta2 -
                  58 * Math.tan(phi) ** 2 * eta2);

    const dl = lam - lam0, dl2 = dl * dl;
    const N = I + II * dl2 + III * dl2 * dl2 + IIIA * dl2 * dl2 * dl2;
    const E = E0 + IV * dl + V * dl2 * dl + VI * dl2 * dl2 * dl;
    return {e: E, n: N};
  }

  /* 10-figure grid reference, e.g. "SJ 83421 98765". Empty outside GB. */
  function format(lat, lon) {
    let en;
    try { en = toEN(lat, lon); } catch (e) { return ''; }
    const E = en.e, N = en.n;
    if (!isFinite(E) || !isFinite(N) || E < 0 || N < 0 || E >= 700000 || N >= 1300000) return '';
    const e100 = Math.floor(E / 100000), n100 = Math.floor(N / 100000);
    let l1 = (19 - n100) - (19 - n100) % 5 + Math.floor((e100 + 10) / 5);
    let l2 = (19 - n100) * 5 % 25 + e100 % 5;
    if (l1 < 0 || l1 > 24 || l2 < 0 || l2 > 24) return '';
    const pad = v => String(Math.floor(v)).padStart(5, '0');
    return LETTERS[l1] + LETTERS[l2] + ' ' +
           pad(E % 100000) + ' ' + pad(N % 100000);
  }

  return {format, toEN};
})();


/* ── ZIP writer ─────────────────────────────────────────────────────────────
 * STORE-only (no deflate): photos are already JPEG-compressed and the GeoJSON
 * is small next to them, so compression buys almost nothing and a raw ZIP is
 * ~70 lines instead of a CDN dependency that would not load in the field.
 * Produces exactly the layout _import_field_survey expects: one .geojson with
 * the photo files beside it.
 */
const Zip = (() => {
  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      t[i] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  function dosTime(d) {
    return ((d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() / 2)) & 0xFFFF;
  }
  function dosDate(d) {
    return (((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate()) & 0xFFFF;
  }

  /* files: [{name: string, bytes: Uint8Array}] -> Blob */
  function build(files, when) {
    const enc = new TextEncoder();
    const stamp = when || new Date();
    const time = dosTime(stamp), date = dosDate(stamp);
    const chunks = [], central = [];
    let offset = 0;

    files.forEach(f => {
      const nameBytes = enc.encode(f.name);
      const crc = crc32(f.bytes);
      const size = f.bytes.length;

      const local = new Uint8Array(30 + nameBytes.length);
      const lv = new DataView(local.buffer);
      lv.setUint32(0, 0x04034b50, true);
      lv.setUint16(4, 20, true);            // version needed
      lv.setUint16(6, 0x0800, true);        // UTF-8 names
      lv.setUint16(8, 0, true);             // STORE
      lv.setUint16(10, time, true);
      lv.setUint16(12, date, true);
      lv.setUint32(14, crc, true);
      lv.setUint32(18, size, true);
      lv.setUint32(22, size, true);
      lv.setUint16(26, nameBytes.length, true);
      lv.setUint16(28, 0, true);
      local.set(nameBytes, 30);

      chunks.push(local, f.bytes);

      const cen = new Uint8Array(46 + nameBytes.length);
      const cv = new DataView(cen.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint16(8, 0x0800, true);
      cv.setUint16(10, 0, true);
      cv.setUint16(12, time, true);
      cv.setUint16(14, date, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, size, true);
      cv.setUint32(24, size, true);
      cv.setUint16(28, nameBytes.length, true);
      cv.setUint32(42, offset, true);
      cen.set(nameBytes, 46);
      central.push(cen);

      offset += local.length + size;
    });

    const centralSize = central.reduce((a, c) => a + c.length, 0);
    const end = new Uint8Array(22);
    const ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, files.length, true);
    ev.setUint16(10, files.length, true);
    ev.setUint32(12, centralSize, true);
    ev.setUint32(16, offset, true);

    return new Blob([...chunks, ...central, end], {type: 'application/zip'});
  }

  return {build};
})();


/* ── Misc helpers ───────────────────────────────────────────────────────── */

function uuid() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

/* Escape for interpolation into innerHTML. Every catalogue string and every
 * note the surveyor types passes through here. */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* Case- and punctuation-insensitive matching. Terms may arrive in any order,
 * so "grass acid" finds "Lowland dry acid grassland"; and the space-stripped
 * form is tried too, so a fast-typed "sanddune" still finds "Sand dune". */
function normalise(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function matchesQuery(haystack, query) {
  const h = normalise(haystack);
  const hTight = h.replace(/ /g, '');
  const terms = normalise(query).split(' ').filter(Boolean);
  if (!terms.length) return true;
  return terms.every(t => h.includes(t) || hTight.includes(t));
}

/* Downscale a captured photo before it is stored. A modern phone camera
 * produces 4-8 MB per shot; a survey of 60 records would blow past every
 * storage quota and make the export untransferable. 1600px on the long edge
 * keeps a tree defect or a sett entrance perfectly legible. */
function shrinkImage(file, maxEdge, quality) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('read failed'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => resolve({dataUrl: reader.result, w: 0, h: 0});
      img.onload = () => {
        const scale = Math.min(1, maxEdge / Math.max(img.width, img.height));
        const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
        try {
          const cv = document.createElement('canvas');
          cv.width = w; cv.height = h;
          cv.getContext('2d').drawImage(img, 0, 0, w, h);
          resolve({dataUrl: cv.toDataURL('image/jpeg', quality || 0.82), w, h});
        } catch (e) {
          resolve({dataUrl: reader.result, w: img.width, h: img.height});
        }
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function dataUrlToBytes(dataUrl) {
  const comma = dataUrl.indexOf(',');
  const bin = atob(dataUrl.slice(comma + 1));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
