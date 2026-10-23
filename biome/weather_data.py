# weather_data.py — historical hourly weather lookup for the survey-conditions table.
"""
Fetches *past* hourly weather (temperature, wind, precipitation) for a given date,
location and hour from Open-Meteo — free, no API key, no sign-up.

Three endpoints are used, chosen by the date and with automatic fall-back:

  * forecast api             — the live forecast, covering the recent past, today
    and ~16 days ahead. Used for today and future survey dates (up to a week on).
  * historical-forecast-api  — the actual high-resolution forecast/analysis model
    (2 km–11 km), covering 2022-01-01 → present, including the last few days.
    This is the primary source for typical (past) survey dates (best accuracy).
  * archive-api (ERA5)       — global reanalysis, ~9 km, 1940 → present (a day or
    two behind "now"). Used as the fall-back, and directly for dates before 2022.

So the lookup works for anything from 1940 up to roughly a week in advance. Values
come back already in the report's units: temperature °C, wind & gusts mph,
precipitation mm/hour.

This module is deliberately QGIS-free (stdlib + urllib only) so it can be unit
tested outside QGIS. ``fetch_hours`` never raises for network/parse problems — it
returns ``(None, None, error_message)`` so the dialog can fall back to manual entry.

Attribution: Open-Meteo data is licensed CC-BY 4.0 — credit "Open-Meteo.com" in
the report (this replaces the old "BBC Weather" footnote).
"""

import json
import datetime as _dt
from urllib.request import urlopen, Request
from urllib.parse import urlencode

# Live forecast: recent past, today, and ~16 days ahead. Used for today/future.
OPEN_METEO_LIVE = "https://api.open-meteo.com/v1/forecast"
# High-resolution reanalysis/analysis model, 2022 → present (incl. the last days).
OPEN_METEO_HIST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# ERA5 global reanalysis, ~9 km, 1940 → present (~1 day behind "now").
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

# url → human source label shown under the result table.
SOURCE_LABELS = {
    OPEN_METEO_LIVE: "Open-Meteo — forecast",
    OPEN_METEO_HIST: "Open-Meteo — high-resolution model",
    OPEN_METEO_ARCHIVE: "Open-Meteo — ERA5 reanalysis",
}

# Surveys are recorded in UK local time; Open-Meteo returns the series in this
# zone (handling BST/GMT) so the hour the surveyor types lines up with the clock.
TIMEZONE = "Europe/London"

HOURLY_VARS = ["temperature_2m", "precipitation", "wind_speed_10m",
               "wind_gusts_10m", "wind_direction_10m", "weather_code"]

USER_AGENT = "BiomeQGISPlugin/1.0"

# WMO weather-interpretation codes → plain-English description (Open-Meteo's
# `weather_code`). Used to phrase the precipitation cell and the window strip.
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Light freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light rain showers", 81: "Rain showers", 82: "Heavy rain showers",
    85: "Light snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg):
    """Degrees → 16-point compass label (the direction the wind comes FROM)."""
    if deg is None:
        return ""
    return _COMPASS[int((float(deg) % 360) / 22.5 + 0.5) % 16]


def describe_code(code):
    """WMO weather code → short description, or '' if unknown/missing."""
    try:
        return WMO_CODES.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def _endpoints_for(d, today):
    """Order of endpoints to try for date `d` (a datetime.date), relative to `today`."""
    if d > today:                       # future → live forecast (covers ~16 days)
        return [OPEN_METEO_LIVE]
    if d == today:                      # today → forecast spans the whole day; fall back
        return [OPEN_METEO_LIVE, OPEN_METEO_HIST, OPEN_METEO_ARCHIVE]
    if d >= _dt.date(2022, 1, 1):       # recent past → high-res, ERA5 fall-back
        return [OPEN_METEO_HIST, OPEN_METEO_ARCHIVE]
    return [OPEN_METEO_ARCHIVE]         # deep past → ERA5 only


def _get(seq_map, key, i):
    seq = seq_map.get(key)
    if not seq or i >= len(seq):
        return None
    return seq[i]


def _query(url, lat, lon, d, timeout):
    params = urlencode({
        "latitude": "%.5f" % lat,
        "longitude": "%.5f" % lon,
        "start_date": d.isoformat(),
        "end_date": d.isoformat(),
        "hourly": ",".join(HOURLY_VARS),
        "timezone": TIMEZONE,
        "wind_speed_unit": "mph",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    })
    req = Request("%s?%s" % (url, params), headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_hours(lat, lon, d, timeout=10, today=None):
    """Fetch the 24 hourly rows for date `d` at (lat, lon).

    `d` may be in the past (back to 1940) or up to ~16 days in the future; the
    endpoint is chosen accordingly. `today` overrides the reference date used for
    that choice (handy for tests); it defaults to the real current date.

    Returns ``(rows, source_label, error)``:
      * ``rows`` — list of 24 dicts (or ``None`` on total failure), each:
        ``{hour, time, temp, wind, gust, dir_deg, dir, precip, code, cond}``.
        Individual values may be ``None`` where the model has no data.
      * ``source_label`` — human label of the endpoint that answered, or ``None``.
      * ``error`` — message string on failure, else ``None``.
    Never raises.
    """
    if not isinstance(d, _dt.date):
        return None, None, "invalid date"
    if today is None:
        today = _dt.date.today()
    last_err = None
    for url in _endpoints_for(d, today):
        try:
            data = _query(url, lat, lon, d, timeout)
        except Exception as e:  # network, timeout, decode — try the next endpoint
            last_err = str(e) or e.__class__.__name__
            continue
        if isinstance(data, dict) and data.get("error"):
            last_err = data.get("reason") or "API error"
            continue
        h = (data or {}).get("hourly") or {}
        times = h.get("time") or []
        if not times:
            last_err = "no hourly data returned"
            continue
        rows = []
        for i, t in enumerate(times):
            row = {
                "hour": int(t[11:13]) if len(t) >= 13 else i,
                "time": t[11:16] if len(t) >= 16 else "",
                "temp": _get(h, "temperature_2m", i),
                "wind": _get(h, "wind_speed_10m", i),
                "gust": _get(h, "wind_gusts_10m", i),
                "dir_deg": _get(h, "wind_direction_10m", i),
                "precip": _get(h, "precipitation", i),
                "code": _get(h, "weather_code", i),
            }
            row["dir"] = compass(row["dir_deg"])
            row["cond"] = describe_code(row["code"])
            rows.append(row)
        # Accept this endpoint only if it actually has temperatures for the day;
        # otherwise fall through to the next (e.g. ERA5 for a pre-2022 date).
        if any(r["temp"] is not None for r in rows):
            return rows, SOURCE_LABELS.get(url, "Open-Meteo"), None
        last_err = "no values available for that date"
    return None, None, (last_err or "weather lookup failed")


def nearest_hour(hour, minute):
    """Snap an HH:MM survey time to the nearest whole hour (0–23). 09:40 → 10."""
    h = int(hour) + (1 if int(minute) >= 30 else 0)
    return max(0, min(23, h))


def window(rows, hour, span=2):
    """Rows within ±`span` hours of `hour` (the ‘accurate to within 2 hours’ strip)."""
    if not rows:
        return []
    lo, hi = hour - span, hour + span
    return [r for r in rows if lo <= r["hour"] <= hi]


def row_for_hour(rows, hour):
    """The single row matching `hour`, or None."""
    for r in rows or []:
        if r["hour"] == hour:
            return r
    return None


# ── Report-cell formatting ───────────────────────────────────────────────────
# These produce the strings that land in the Detail column of the report table.
# Kept here (not in the dialog) so they are unit-testable and consistent.

def fmt_temperature(temp):
    if temp is None:
        return "—"
    return "%d °C" % int(round(temp))


def fmt_wind(wind, gust=None, direction=""):
    if wind is None:
        return "—"
    s = "%d mph" % int(round(wind))
    if direction:
        s += " %s" % direction
    # Only mention gusts when they are meaningfully stronger than the mean wind.
    if gust is not None and int(round(gust)) >= int(round(wind)) + 4:
        s += " (gusting %d mph)" % int(round(gust))
    return s


def fmt_precipitation(precip, code=None):
    if precip is None:
        return "—"
    if precip <= 0.05:
        return "0.0 mm (dry)"
    cond = describe_code(code).lower() if code is not None else ""
    base = "%.1f mm" % precip
    return "%s (%s)" % (base, cond) if cond else base


def report_details(row):
    """The three Detail-column strings (temperature, wind, precipitation) for a row."""
    if not row:
        return ["—", "—", "—"]
    return [
        fmt_temperature(row.get("temp")),
        fmt_wind(row.get("wind"), row.get("gust"), row.get("dir")),
        fmt_precipitation(row.get("precip"), row.get("code")),
    ]
