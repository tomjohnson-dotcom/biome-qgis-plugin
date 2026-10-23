# credit_prices.py — statutory biodiversity credit prices (£ per credit).
"""
Source: https://www.gov.uk/guidance/statutory-biodiversity-credit-prices

Tiers A1–A5 are area habitats, H is hedgerows, W is watercourses. You must buy
TWO statutory credits per biodiversity unit (the statutory 2:1 rule) — that ×2 is
already baked into credits_engine.tier_credits / water_credit, which return the
number of CREDITS. The figures below are the per-CREDIT tariff.

live_prices() makes a best-effort fetch from gov.uk so the figures don't go stale,
and falls back to the embedded (dated) table if the network or parse fails — so
the tab always shows sane numbers. The result is cached for the session.
"""

# GOV.UK tariff, £ per credit (as at the date below).
EMBEDDED_PRICES = {
    "A1": 42000, "A2": 48000, "A3": 66000, "A4": 125000, "A5": 650000,
    "H": 44000, "W": 230000,
}
PRICES_AS_AT = "2 June 2026"
PRICES_URL = "https://www.gov.uk/guidance/statutory-biodiversity-credit-prices"
# gov.uk developer guidance on sourcing off-site units (the market is private — no
# single national habitat-bank finder; the LPA can also signpost local banks).
OFFSITE_UNITS_URL = "https://www.gov.uk/guidance/make-off-site-biodiversity-gains-as-a-developer"

_cache = {}


def live_prices():
    """Return (prices_dict, source_label). Tries gov.uk once per session, then
    falls back to the embedded table. Never raises."""
    if _cache.get("prices"):
        return _cache["prices"], _cache["source"]
    prices = dict(EMBEDDED_PRICES)
    source = "embedded — as at %s" % PRICES_AS_AT
    try:
        from urllib.request import urlopen, Request
        req = Request(PRICES_URL, headers={"User-Agent": "BiomeQGISPlugin/1.0"})
        with urlopen(req, timeout=6) as resp:
            html = resp.read().decode("utf-8", "replace")
        fetched = _parse_prices(html)
        if fetched:
            prices.update(fetched)
            source = "gov.uk — fetched live"
    except Exception:
        pass
    _cache["prices"], _cache["source"] = prices, source
    return prices, source


def _parse_prices(html):
    """Pull tier -> £ from the gov.uk page. Defensive: returns {} unless EVERY
    tier resolves to a sane value, so a structure change falls back to embedded
    rather than showing a half-parsed/garbled tariff."""
    import re
    pats = {
        "A1": r"\bA1\b.{0,300}?£\s*([\d,]+)",
        "A2": r"\bA2\b.{0,300}?£\s*([\d,]+)",
        "A3": r"\bA3\b.{0,300}?£\s*([\d,]+)",
        "A4": r"\bA4\b.{0,300}?£\s*([\d,]+)",
        "A5": r"\bA5\b.{0,300}?£\s*([\d,]+)",
        "H":  r"[Hh]edgerow.{0,300}?£\s*([\d,]+)",
        "W":  r"[Ww]atercourse.{0,300}?£\s*([\d,]+)",
    }
    out = {}
    for tier, pat in pats.items():
        m = re.search(pat, html, re.S)
        if not m:
            return {}
        try:
            v = int(m.group(1).replace(",", ""))
        except ValueError:
            return {}
        if not (1000 <= v <= 5_000_000):
            return {}
        out[tier] = v
    return out
