/* Biome Field Mapper — service worker.
 *
 * The whole point: a field site has no signal, and the app is served from a
 * laptop that will be shut in a car boot. Once the page has loaded at the
 * office, everything it needs must already be on the phone.
 *
 * Two caches:
 *   shell — the app HTML (and Leaflet, if it came from a CDN). Network-first
 *           so a re-scan picks up a rebuilt app, cache-fallback so a reload
 *           with no signal still works.
 *   tiles — basemap imagery, cache-first and never revalidated. Tiles do not
 *           change, and re-fetching them is exactly what fails in the field.
 *
 * The sync POST is never cached or queued here: a survey that silently
 * "sent" into a background queue and then vanished would be worse than an
 * honest failure, so app.js reports the error and offers the download.
 */

const VERSION = '{{VERSION}}';
const SHELL = 'biome-shell-' + VERSION;
const TILES = 'biome-tiles-v1';

const TILE_HOSTS = [
  'mt0.google.com', 'mt1.google.com', 'mt2.google.com', 'mt3.google.com',
  'server.arcgisonline.com', 'tile.openstreetmap.org',
  'a.tile.opentopomap.org', 'b.tile.opentopomap.org', 'c.tile.opentopomap.org',
  'basemaps.cartocdn.com',
];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(['./']).catch(() => {})));
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.map(n =>
      (n.startsWith('biome-shell-') && n !== SHELL) ? caches.delete(n) : null));
    await self.clients.claim();
  })());
});

function isTile(url) {
  return TILE_HOSTS.some(h => url.hostname === h || url.hostname.endsWith('.' + h));
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                  // never intercept the sync POST
  const url = new URL(req.url);

  if (isTile(url)) {
    e.respondWith((async () => {
      const cache = await caches.open(TILES);
      const hit = await cache.match(req);
      if (hit) return hit;
      try {
        const res = await fetch(req);
        // Opaque responses (mode: no-cors) are cacheable and are what the
        // pre-fetch in app.js produces.
        if (res && (res.ok || res.type === 'opaque')) cache.put(req, res.clone());
        return res;
      } catch (err) {
        return new Response('', {status: 504, statusText: 'Offline'});
      }
    })());
    return;
  }

  // App shell and any same-origin or CDN asset.
  e.respondWith((async () => {
    const cache = await caches.open(SHELL);
    try {
      const res = await fetch(req);
      if (res && res.ok) cache.put(req, res.clone());
      return res;
    } catch (err) {
      const hit = await cache.match(req) || await cache.match('./');
      if (hit) return hit;
      throw err;
    }
  })());
});
