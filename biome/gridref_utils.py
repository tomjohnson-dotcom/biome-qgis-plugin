import re


class GridRefError(Exception):
    pass


# OS National Grid letters (excluding I)
_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


def _letter_index(ch: str) -> int:
    idx = _LETTERS.find(ch)
    if idx == -1:
        raise GridRefError(f"Invalid grid letter: {ch}")
    return idx


def gridref_to_osgb_en(gridref: str):
    """
    Convert a UK National Grid Reference (e.g. "TQ 443 806", "NT2757373172")
    to OSGB36 Easting/Northing in metres (EPSG:27700).
    """
    if not gridref or not isinstance(gridref, str):
        raise GridRefError("Grid reference is empty.")

    s = gridref.strip().upper()
    s = re.sub(r"\s+", "", s)

    if len(s) < 4:
        raise GridRefError("Grid reference too short.")

    l1, l2 = s[0], s[1]
    if l1 not in _LETTERS or l2 not in _LETTERS:
        raise GridRefError("Grid reference must start with two letters (A-Z excluding I).")

    digits = s[2:]
    if not digits.isdigit():
        raise GridRefError("Grid reference digits are invalid.")

    if len(digits) % 2 != 0:
        raise GridRefError("Grid reference must have an even number of digits (e.g. 2, 4, 6, 8, 10).")

    i1 = _letter_index(l1)
    i2 = _letter_index(l2)

    # OS method
    e500 = ((i1 - 2) % 5) * 500000
    n500 = (19 - (i1 // 5) * 5) * 100000
    e = e500 + (i2 % 5) * 100000
    n = n500 - (i2 // 5) * 100000

    half = len(digits) // 2
    e_digits = digits[:half]
    n_digits = digits[half:]

    scale = 10 ** (5 - half)
    e += int(e_digits) * scale
    n += int(n_digits) * scale

    return e, n

def osgb_en_to_gridref(easting: float, northing: float, digits: int = 10) -> str:
    """
    Convert OSGB36 Easting/Northing (EPSG:27700) to a UK National Grid Reference.
    digits: total digit count (6=1km, 8=100m, 10=1m precision). Default 10 (1m).
    Returns e.g. 'SU 12345 67890'.
    """
    half = digits // 2

    # Which 500km square?
    e500 = int(easting // 500000)
    n500 = int(northing // 500000)

    # First letter: column 0–6 west→east, row 0–4 south→north (but inverted)
    # The 500km grid origin is at (-1000000, -500000) in OSGB
    # Standard OS approach:
    e = easting + 1000000
    n = northing + 500000

    l1_col = int(e // 500000)
    l1_row = int(n // 500000)
    l1_idx = (4 - l1_row) * 5 + l1_col

    # Clamp into valid range
    l1_idx = max(0, min(l1_idx, 24))
    letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
    l1 = letters[l1_idx]

    e_rem = e % 500000
    n_rem = n % 500000

    l2_col = int(e_rem // 100000)
    l2_row = int(n_rem // 100000)
    l2_idx = (4 - l2_row) * 5 + l2_col

    l2 = letters[l2_idx]

    e_in = int(e_rem % 100000)
    n_in = int(n_rem % 100000)

    scale = 10 ** (5 - half)
    e_str = str(e_in // scale).zfill(half)
    n_str = str(n_in // scale).zfill(half)

    return f"{l1}{l2} {e_str} {n_str}"
