# -*- coding: utf-8 -*-
"""
council_maps.py — site-bounded deep-links into council & LNRS interactive maps.

Two desk-study buttons currently open a landing page or a web search:

  * the "no TPO data found" dialog  (main_panel._show_tpo_no_results_dialog)
  * the strategic-significance check (main_panel.open_strategic_significance_check)

This module upgrades both so they open the *actual* interactive web map already
zoomed to the redline, with the relevant layer switched on where we know it —
mirroring the Defra MAGIC `box=` deep-link in main_panel._open_magic_map.

How it works
------------
The redline gives us a bounding box and centroid in British National Grid
(EPSG:27700). Almost every UK council web-map runs one of a handful of engines,
each with a documented deep-link scheme:

  * Astun iShare  ("MyMaps"/"MyHouse")  StartEasting/StartNorthing/StartZoom (m),
                                        MapSource, Layers.            27700 native.
  * ArcGIS        (WebAppBuilder /      extent=minX,minY,maxX,maxY,27700 (or
                   Experience Builder /  center+level / marker), showLayers (";").
                   AGOL viewer)         extent accepts a WKID, so we pass 27700.
  * Cadcorp       ("LocalView" etc.)    x, y, resolution, datalayers, showpin.
                                        27700 native.

Because the redline box is already in 27700, iShare and Cadcorp need no
reprojection and ArcGIS just needs ",27700" appended to the extent.

Coverage is partial and council URLs churn, so every entry point degrades
through a fallback chain that ends at the existing landing-page / search
resolution in designated_sites.get_tpo_council_portal — i.e. councils we have
not audited still behave exactly as they do today.

Strategic significance is the Local Nature Recovery Strategy (LNRS): 48 areas,
each with one Responsible Authority. We resolve the area by point-in-polygon
against Natural England's published "LNRS Areas (England)" layer (27700), which
also carries an authoritative `Link_to_published` per area — so even with no
audited authority map we can link to the official strategy.

This module is deliberately QGIS-free (stdlib + urllib only) so it can be
unit-tested outside QGIS (`python council_maps.py`). The caller passes the BNG
bbox/centroid in; CRS handling stays in main_panel.
"""

from urllib.parse import urlencode, quote


# ===========================================================================
# Typed map records
# ===========================================================================
# Each record describes one council's (or LNRS authority's) interactive map:
#
#   {
#     "platform": "ishare" | "arcgis" | "cadcorp" | "static",
#     "url":      "<map app base URL>",
#     # iShare:
#     "mapsource": "organisation/planning",   # required for iShare
#     "layers":    "TPO,TPO_Areas",           # comma list (iShare Layers)
#     # ArcGIS:
#     "layers":    "3;7",                      # ";"-sep layer ids (showLayers)
#     # Cadcorp:
#     "datalayers": "Tree Preservation Orders",# comma list
#     # any platform (optional):
#     "confidence": "high" | "medium" | "low",
#     "note":      "free text from the audit",
#   }
#
# "static" = the URL is the live map app but we have no working deep-link
# scheme for it; we still open it (better than a landing page) but cannot bbox.
#
# Keys are lower-case council names matching designated_sites.TPO_COUNCIL_PORTALS
# so the same LPA-name normalisation resolves both tables.

# LNRS authority maps are keyed by the exact `Resp_Auth` string returned by the
# Natural England LNRS Areas layer (e.g. "Surrey County Council"), so a resolved
# area matches its record without fuzzy logic.
#
# Both tables live in council_maps_data.py — regenerated from the map audit — so
# this engine module stays stable. Fall back to empty dicts if that file is
# missing (the deep-link path then degrades to the landing-page fallback chain).
try:
    from .council_maps_data import TPO_COUNCIL_MAPS, LNRS_AUTHORITY_MAPS
except Exception:
    try:
        from council_maps_data import TPO_COUNCIL_MAPS, LNRS_AUTHORITY_MAPS
    except Exception:
        TPO_COUNCIL_MAPS, LNRS_AUTHORITY_MAPS = {}, {}

# council -> downloadable TPO dataset URL (open-data / FeatureServer / CSV) the
# user can import into QGIS. Imported separately so an older data module without
# this table doesn't blank out the maps above.
try:
    from .council_maps_data import TPO_DATA_DOWNLOADS
except Exception:
    try:
        from council_maps_data import TPO_DATA_DOWNLOADS
    except Exception:
        TPO_DATA_DOWNLOADS = {}

# Official council homepage per LPA (audited + derived) for the "LPA website"
# button. Keyed by raw LPA name; imported separately so a missing file can't
# blank the map tables.
try:
    from .lpa_homepages import LPA_HOMEPAGES, LPA_FULL_NAMES
except Exception:
    try:
        from lpa_homepages import LPA_HOMEPAGES, LPA_FULL_NAMES
    except Exception:
        LPA_HOMEPAGES, LPA_FULL_NAMES = {}, {}

# Councils tposearch.co.uk has LIVE TPO data for (pre-normalised names). Used to
# offer a site-zoomed tposearch postcode link ONLY where it will actually show
# data. Imported separately so a missing file degrades gracefully (no link).
try:
    from .tposearch_data import TPOSEARCH_COVERED
except Exception:
    try:
        from tposearch_data import TPOSEARCH_COVERED
    except Exception:
        TPOSEARCH_COVERED = frozenset()


# ===========================================================================
# Per-platform URL builders
# ===========================================================================
# Each takes (record, bbox, centroid) and returns a deep-link string.
#   bbox     = (min_e, min_n, max_e, max_n) in EPSG:27700, already margined
#   centroid = (easting, northing) in EPSG:27700

def _site_span(bbox):
    """Larger of the bbox width/height in metres."""
    min_e, min_n, max_e, max_n = bbox
    return max(max_e - min_e, max_n - min_n)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _join(url, params, safe):
    """Append urlencoded params to url, respecting an existing query string.

    Uses %20 for spaces (quote, not quote_plus) to match the ArcGIS / Cadcorp
    documented examples, which matters for layer names like "Tree Preservation
    Orders"."""
    sep = "&" if "?" in url else "?"
    return url + sep + urlencode(params, safe=safe, quote_via=quote)


def _build_ishare_url(rec, bbox, centroid):
    """Astun iShare. Two URL schemes share the platform:

      * standard "MyMaps" (.aspx) — MapSource / StartEasting / StartNorthing /
        StartZoom / Layers. StartZoom is the visible map width in metres (500 ≈
        street level).
      * "LiteMap" (custom HTML, url contains 'litemap') — lower-case
        mapsource / easting / northing / zoom / layer (singular) + addsearch.

    Both take British National Grid natively, so no reprojection."""
    e, n = centroid
    z = int(_clamp(_site_span(bbox), 250, 2000))

    if "litemap" in rec["url"].lower():
        params = {}
        if rec.get("mapsource"):
            params["mapsource"] = rec["mapsource"]
        if rec.get("layers"):
            params["layer"] = rec["layers"]
        params["easting"]   = "%.0f" % e
        params["northing"]  = "%.0f" % n
        params["zoom"]      = z
        params["addsearch"] = "true"
        return _join(rec["url"], params, safe="/,")

    params = {}
    if rec.get("mapsource"):
        params["MapSource"] = rec["mapsource"]
    params["StartEasting"]  = "%.0f" % e
    params["StartNorthing"] = "%.0f" % n
    params["StartZoom"]     = z
    if rec.get("layers"):
        params["Layers"] = rec["layers"]
    params["tab"] = "maps"
    return _join(rec["url"], params, safe="/,")


def _build_arcgis_url(rec, bbox, centroid):
    """ArcGIS WebAppBuilder / Experience Builder / AGOL viewer.

    `extent` takes projected coords with a trailing WKID, so we feed the BNG
    bbox straight through as ...,27700. `showLayers` is a ";"-separated id list.
    """
    min_e, min_n, max_e, max_n = bbox
    params = {"extent": "%.0f,%.0f,%.0f,%.0f,27700" % (min_e, min_n, max_e, max_n)}
    if rec.get("layers"):
        params["showLayers"] = rec["layers"]   # already ";"-separated
    if rec.get("find"):
        params["find"] = rec["find"]
    return _join(rec["url"], params, safe=",;")


def _build_cadcorp_url(rec, bbox, centroid):
    """Cadcorp Web Map. `resolution` is map units (metres) per pixel."""
    e, n = centroid
    resolution = round(_clamp(_site_span(bbox) / 700.0, 0.4, 5.0), 2)
    params = {"x": "%.0f" % e, "y": "%.0f" % n,
              "resolution": resolution, "showpin": "true"}
    if rec.get("datalayers"):
        params["datalayers"] = rec["datalayers"]
    if rec.get("baselayer"):
        params["baseLayer"] = rec["baselayer"]
    return _join(rec["url"], params, safe=",")


_BUILDERS = {
    "ishare":  _build_ishare_url,
    "arcgis":  _build_arcgis_url,
    "cadcorp": _build_cadcorp_url,
}


def build_map_url(rec, bbox, centroid):
    """Build a site-bounded deep-link from a typed record, or None if we can't.

    A "static" record (live map app, no known deep-link scheme) returns its bare
    URL — still the real map, just not pre-zoomed.
    """
    if not rec or not rec.get("url"):
        return None
    platform = rec.get("platform")
    if platform == "static":
        return rec["url"]
    builder = _BUILDERS.get(platform)
    if builder is None:
        return None
    try:
        return builder(rec, bbox, centroid)
    except Exception:
        return None


# ===========================================================================
# LPA-name → record lookup
# ===========================================================================
# Records are keyed exactly like designated_sites.TPO_COUNCIL_PORTALS, so we
# reuse that module's normaliser/aliases to resolve an LPA name to a record.

_norm_index_cache = {}   # id(table) -> {norm_key: record}


def _ds():
    """Import the sibling designated_sites module, whether we're loaded as part
    of the `biome` package (QGIS runtime) or flat on sys.path (self-test).

    The bare `from designated_sites import ...` form FAILS under QGIS, where the
    module only exists as `biome.designated_sites` — so the TPO landing-page /
    search fallback silently died and every unmapped council fell through to the
    generic Planning Portal finder. Try the relative import first, then bare."""
    try:
        from . import designated_sites as ds
        return ds
    except Exception:
        import designated_sites as ds
        return ds


# Generic descriptor words that may differ between an LPA's official name and a
# table key without changing which authority is meant. Mirrors
# designated_sites._GENERIC_WORDS; a local copy is the fallback if the import
# fails so the whole-word match below still works standalone.
_GENERIC_WORDS = {"city", "of", "the", "royal", "greater", "county",
                  "council", "borough", "district", "metropolitan"}


def _norm_lpa(name):
    """Thin wrapper around designated_sites._norm_lpa with a plain fallback so
    this module still works (and self-tests) if imported standalone."""
    try:
        return _ds()._norm_lpa(name)
    except Exception:
        return " ".join((name or "").strip().lower().replace("&", " and ").split())


def _norm_index(table):
    """Build (and cache) a normalised-key index for a council/authority table."""
    key = id(table)
    idx = _norm_index_cache.get(key)
    if idx is None or len(idx) != len(table):
        idx = {_norm_lpa(k): v for k, v in table.items()}
        _norm_index_cache[key] = idx
    return idx


def _generic_words():
    try:
        return _ds()._GENERIC_WORDS
    except Exception:
        return _GENERIC_WORDS


def _fuzzy_lookup(norm, idx):
    """Guarded whole-word containment match, mirroring
    designated_sites.get_tpo_council_portal step 2: only generic descriptor
    words may differ (so "City of Bristol" ↔ "Bristol", but "South Oxfordshire"
    never matches "Oxford"), a National-Park query must not match a non-park
    key, and the best (most shared distinctive words) key wins. Returns the
    record or None."""
    generic = _generic_words()
    q_words = set(norm.split())
    q_is_np = "national park" in norm
    best = None  # (score, record)
    for nkey, rec in idx.items():
        k_words = set(nkey.split())
        if (q_words ^ k_words) - generic:
            continue
        if q_is_np != ("national park" in nkey):
            continue
        shared = [w for w in (q_words & k_words)
                  if len(w) >= 4 and w not in generic]
        if not shared:
            continue
        if best is None or len(shared) > best[0]:
            best = (len(shared), rec)
    return best[1] if best else None


def _lookup_record(lpa_name, table):
    """Resolve an LPA / authority name to a record via exact-normalised match
    (+ designated_sites aliases), then a guarded whole-word containment match.
    Returns the record or None."""
    if not lpa_name:
        return None
    norm = _norm_lpa(lpa_name)
    try:
        norm = _ds()._LPA_ALIASES.get(norm, norm)
    except Exception:
        pass
    idx = _norm_index(table)
    rec = idx.get(norm)
    if rec is not None:
        return rec
    return _fuzzy_lookup(norm, idx)


# ===========================================================================
# TPO entry point
# ===========================================================================

def build_tpo_deeplink(lpa_name, bbox, centroid):
    """Best site-bounded TPO link for an LPA, with graceful fallback.

    Returns (url, kind, council_display, confidence):
      kind = "map"    -- deep-link into the council's interactive map (bbox[+layer])
             "portal" -- council's TPO landing page (current behaviour)
             "search" -- council-scoped web search
             "finder" -- generic Planning Portal LPA finder (no LPA resolved)
    """
    rec = _lookup_record(lpa_name, TPO_COUNCIL_MAPS)
    if rec:
        url = build_map_url(rec, bbox, centroid)
        if url:
            return (url, "map", (lpa_name or "").strip() or "your local council",
                    rec.get("confidence", "medium"))

    # Fallback: existing landing-page / search / finder resolution.
    try:
        disp, url, is_direct = _ds().get_tpo_council_portal(lpa_name)
        if is_direct:
            kind = "portal"
        elif lpa_name:
            kind = "search"
        else:
            kind = "finder"
        return url, kind, disp, "fallback"
    except Exception:
        return ("https://www.planningportal.co.uk/find-your-local-planning-authority",
                "finder", "your local council", "fallback")


def tpo_data_download(lpa_name):
    """URL of a downloadable TPO dataset for an LPA (open-data page / ArcGIS
    FeatureServer / CSV / GeoJSON) that the user can download and import into
    QGIS, or None. Independent of whether a deep-linkable map exists — many
    councils with no public map still publish the data."""
    rec = _lookup_record(lpa_name, TPO_DATA_DOWNLOADS)
    if isinstance(rec, dict):
        rec = rec.get("url")
    return rec or None


def tposearch_covers(lpa_name):
    """True if tposearch.co.uk has live TPO data for this LPA, so a postcode
    search there will actually plot protected trees (vs a misleadingly empty
    result). Matched via the shared LPA-name normalisation."""
    if not lpa_name:
        return False
    return _norm_lpa(lpa_name) in TPOSEARCH_COVERED


def tposearch_link(postcode, lpa_name=None):
    """Site-zoomed tposearch.co.uk postcode search URL, e.g.
    https://tposearch.co.uk/search/N1+7SL — an aggregated interactive TPO map
    covering ~179 councils, opened straight at the site's postcode.

    Returns None when there is no postcode, or (when lpa_name is given) when
    tposearch has no data for that council — so we never send the user to an
    empty result. Pass lpa_name=None to skip the coverage gate (e.g. when the
    council could not be resolved but a postcode is available)."""
    if not postcode:
        return None
    if lpa_name is not None and not tposearch_covers(lpa_name):
        return None
    pc = "+".join(str(postcode).strip().upper().split())
    return "https://tposearch.co.uk/search/%s" % quote(pc, safe="+")


def lpa_homepage(name):
    """Official council homepage URL for an LPA name (as returned by
    get_lpa_for_bng), or None. Audited per-council; keyed by raw LPA name and
    matched via the shared LPA-name normalisation."""
    rec = _lookup_record(name, LPA_HOMEPAGES)
    if isinstance(rec, dict):
        rec = rec.get("url")
    return rec or None


def lpa_full_name(name):
    """Full official LPA name (e.g. "Swindon" -> "Swindon Borough Council") for
    display, or the input name unchanged if we have no mapping."""
    return _lookup_record(name, LPA_FULL_NAMES) or name


# ===========================================================================
# Strategic significance (LNRS) entry point
# ===========================================================================

_LNRS_FS_QUERY = (
    "https://services.arcgis.com/JJzESW51TqeY9uat/arcgis/rest/services/"
    "Local_Nature_Recovery_Strategy_Areas_England/FeatureServer/0/query"
)

# National fallback: Natural England's LNRS Areas explorer (tells you which area
# / responsible authority you're in and links to the published strategy).
_LNRS_NATIONAL_URL = (
    "https://naturalengland-defra.opendata.arcgis.com/datasets/"
    "Defra::local-nature-recovery-strategy-areas-england/explore"
)


def resolve_lnrs_area(easting, northing):
    """Point-in-polygon against Natural England's LNRS Areas layer (27700).

    Returns {name, authority, published_url} for the area containing the point,
    or None on failure / no hit.
    """
    import json
    from urllib.request import urlopen, Request
    try:
        params = urlencode({
            "f":            "json",
            "geometry":     "%.1f,%.1f" % (easting, northing),
            "geometryType": "esriGeometryPoint",
            "inSR":         27700,
            "spatialRel":   "esriSpatialRelIntersects",
            "outFields":    "Name,Resp_Auth,Link_to_published",
            "returnGeometry": "false",
        })
        req = Request("%s?%s" % (_LNRS_FS_QUERY, params),
                      headers={"User-Agent": "BiomeQGISPlugin/1.0"})
        with urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        feats = data.get("features") or []
        if feats:
            a = feats[0].get("attributes", {}) or {}
            return {
                "name":          (a.get("Name") or "").strip(),
                "authority":     (a.get("Resp_Auth") or "").strip(),
                "published_url": (a.get("Link_to_published") or "").strip(),
            }
    except Exception:
        pass
    return None


def build_lnrs_deeplink(easting, northing, bbox, centroid):
    """Best site-bounded strategic-significance (LNRS) link.

    Returns (url, kind, area_name, authority):
      kind = "map"       -- the responsible authority's interactive LNRS map
                            (bbox[+priority layer])
             "published" -- the official published-strategy link from the NE
                            dataset (may be a viewer, story map or PDF)
             "national"  -- Natural England LNRS Areas national explorer
    """
    area = resolve_lnrs_area(easting, northing) or {}
    authority = area.get("authority", "")
    name      = area.get("name", "")

    rec = _lookup_record(authority, LNRS_AUTHORITY_MAPS)
    if rec:
        url = build_map_url(rec, bbox, centroid)
        if url:
            return url, "map", name, authority

    if area.get("published_url"):
        return area["published_url"], "published", name, authority

    return _LNRS_NATIONAL_URL, "national", name, authority


# ===========================================================================
# Standalone smoke test:  python council_maps.py
# ===========================================================================
if __name__ == "__main__":
    # A ~250 m site near central Guildford (BNG), already margined.
    bbox = (499000, 149000, 499300, 149300)
    cen  = (499150, 149150)

    samples = {
        "ishare": {"platform": "ishare", "url": "https://maps.example.gov.uk/map.aspx",
                   "mapsource": "council/planning", "layers": "TPO,TPO_Areas"},
        "arcgis": {"platform": "arcgis",
                   "url": "https://example.maps.arcgis.com/apps/webappviewer/index.html",
                   "layers": "3;7"},
        "cadcorp": {"platform": "cadcorp",
                    "url": "https://webmap.example.gov.uk/webmap/map/PublicMap",
                    "datalayers": "Tree Preservation Orders"},
        "static": {"platform": "static", "url": "https://maps.example.gov.uk/trees"},
    }
    print("== build_map_url ==")
    for k, rec in samples.items():
        print("%-8s %s" % (k, build_map_url(rec, bbox, cen)))

    print("\n== empty-table fallbacks (no network in offline test) ==")
    print("tpo  :", build_tpo_deeplink("Guildford", bbox, cen))
