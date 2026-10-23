# grid_ref.py
# Converts UK Ordnance Survey National Grid references to OSGB36 easting/northing.
# QGIS handles the CRS transformation to the map canvas CRS accurately.

import re

# 500km square origins (easting, northing) relative to OSGB false origin
_GRID_500 = {
    'S': (0,       0),
    'T': (500000,  0),
    'N': (0,       500000),
    'O': (500000,  500000),
    'H': (0,       1000000),
}

# 100km square letters (row 0 = top of each 500km square)
_GRID_100 = [
    ['A','B','C','D','E'],
    ['F','G','H','J','K'],
    ['L','M','N','O','P'],
    ['Q','R','S','T','U'],
    ['V','W','X','Y','Z'],
]


def _letters_to_en(letters):
    """Return (easting, northing) in OSGB36 metres for a two-letter grid prefix."""
    letters = letters.upper()
    l1, l2 = letters[0], letters[1]

    if l1 not in _GRID_500:
        return None

    e500, n500 = _GRID_500[l1]

    for row in range(5):
        for col in range(5):
            if _GRID_100[row][col] == l2:
                e = e500 + col * 100000
                n = n500 + (4 - row) * 100000
                return e, n
    return None


def grid_ref_to_osgb36(ref):
    """
    Parse a UK National Grid reference and return (easting, northing) in OSGB36 metres.
    Returns None if the reference cannot be parsed.

    Supported formats:
        SU 12345 67890   10-figure spaced
        SU1234567890     10-figure compact
        TQ 300 800       6-figure spaced
        TQ300800         6-figure compact
        NY0005           4-figure compact
    """
    ref = ref.strip().upper().replace(',', '').replace('.', '')

    match = re.match(r'^([A-Z]{2})\s*(\d+)\s*(\d*)$', ref)
    if not match:
        return None

    letters = match.group(1)
    digits1 = match.group(2)
    digits2 = match.group(3)

    if digits2:
        e_str, n_str = digits1, digits2
    else:
        d = digits1
        if len(d) % 2 != 0:
            return None
        half = len(d) // 2
        e_str, n_str = d[:half], d[half:]

    if len(e_str) != len(n_str):
        return None

    precision  = len(e_str)
    multiplier = 10 ** (5 - precision)
    e_local    = int(e_str) * multiplier
    n_local    = int(n_str) * multiplier

    origin = _letters_to_en(letters)
    if origin is None:
        return None

    return origin[0] + e_local, origin[1] + n_local
