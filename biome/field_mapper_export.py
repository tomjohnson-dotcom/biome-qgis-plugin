"""
field_mapper_export.py
Assembles the self-contained Biome Field Mapper page.

The app itself lives in field_mapper_app/ as ordinary .html/.css/.js files —
edit those, not a Python string. This module only substitutes the payload built
by field_mapper_data and inlines the assets, so the result is one HTML file the
tiny local server can hand to a phone.

Authoring the app as real files (rather than the f-string this module used to
be) removes the brace-doubling hazard entirely: every ``{`` in the old version
had to be written ``{{`` or the export crashed at format time.

Exports are consumed by main_panel._import_field_survey.
"""

import json
import os

from .field_mapper_data import build_payload

_APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "field_mapper_app")

# Pinned so a Leaflet release cannot change the app under a survey.
LEAFLET_VERSION = "1.9.4"
_LEAFLET_CSS_URL = "https://unpkg.com/leaflet@%s/dist/leaflet.css" % LEAFLET_VERSION
_LEAFLET_JS_URL = "https://unpkg.com/leaflet@%s/dist/leaflet.js" % LEAFLET_VERSION


def _read(name):
    with open(os.path.join(_APP_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _vendored(name):
    """Return a vendored asset's text, or None.

    Dropping leaflet.css / leaflet.js into field_mapper_app/vendor/ makes the
    page fully self-contained, so it survives even a first load with no
    signal. Without them the CDN tags are used and the service worker caches
    the files on first load instead — which covers the real workflow (load at
    the office, survey out of signal) but not a cold start in a field.
    """
    path = os.path.join(_APP_DIR, "vendor", name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def _leaflet_tags():
    css = _vendored("leaflet.css")
    js = _vendored("leaflet.js")
    if css and js:
        return "<style>%s</style>" % css, "<script>%s</script>" % js
    head = ('<link rel="stylesheet" href="%s" crossorigin="anonymous"/>' % _LEAFLET_CSS_URL)
    body = ('<script src="%s" crossorigin="anonymous"></script>' % _LEAFLET_JS_URL)
    return head, body


def _fill(template, tokens):
    """Replace the ``/*{{NAME}}*/`` and ``<!--{{NAME}}-->`` markers.

    Uses str.replace rather than str.format so nothing in the CSS or JS needs
    escaping — the assets contain thousands of braces.
    """
    out = template
    for name, value in tokens.items():
        for marker in ("/*{{%s}}*/" % name, "<!--{{%s}}-->" % name, "{{%s}}" % name):
            if marker in out:
                out = out.replace(marker, value)
    return out


def build_site_dict(redline_geojson_str, centre_lat, centre_lon,
                    name="", ref="", surveyor=""):
    """Assemble the ``site`` block of the payload."""
    try:
        redline = json.loads(redline_geojson_str) if redline_geojson_str else None
    except Exception:
        redline = None
    return {
        "name": name or "",
        "ref": ref or "",
        "surveyor": surveyor or "",
        "lat": centre_lat,
        "lon": centre_lon,
        "redline": redline or {"type": "FeatureCollection", "features": []},
    }


def build_field_mapper_html(redline_geojson_str, centre_lat, centre_lon,
                            site_name="", site_ref="", surveyor=""):
    """Return the full self-contained HTML string for the field mapper.

    Signature is backwards compatible: the first three arguments are unchanged
    from the previous version, the rest are optional project metadata.
    """
    site = build_site_dict(redline_geojson_str, centre_lat, centre_lon,
                           site_name, site_ref, surveyor)
    payload = build_payload(site)
    payload_js = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    leaflet_head, leaflet_body = _leaflet_tags()

    return _fill(_read("index.html"), {
        "LEAFLET_HEAD": leaflet_head,
        "LEAFLET_BODY": leaflet_body,
        "APP_CSS": _read("app.css"),
        "CORE_JS": _read("core.js"),
        "APP_JS": _read("app.js"),
        "PAYLOAD": payload_js,
    })


def build_service_worker(version_token):
    """Return the service worker source, stamped with a cache-busting token.

    The token changes per served session so a rebuilt app is never masked by a
    stale shell cache.
    """
    return _fill(_read("sw.js"), {"VERSION": str(version_token)})
