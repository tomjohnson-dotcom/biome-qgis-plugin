# biome_app_theme.py — deliver Biome Mode's app-wide restyle through the
# official QGIS 4 plugin theme API (QgsApplicationThemeRegistry, added in
# QGIS 4.0, PR #64503) instead of a raw QApplication.setStyleSheet.
#
# Why: the registry is the sanctioned route for a plugin to ship a full
# application style (QSS + QPalette). QGIS applies it via setUITheme(), fires
# themeChanged, lists it in Settings ▸ Interface ▸ UI theme, and restores the
# default cleanly with setUITheme("default") — replacing Biome Mode's fragile
# snapshot/restore of the app stylesheet and palette.
#
# This module is a no-op on QGIS 3 (registry absent); callers keep their Qt5
# path there. Everything is written into the profile's themes/ folder because
# QgsApplication resolves a theme's icons/ overrides only from there.

import os

from qgis.core import QgsApplication

THEME_NAME = "Biome"


def is_available():
    """True only where the QGIS 4 theme registry exists."""
    return hasattr(QgsApplication, "applicationThemeRegistry")


def _theme_dir():
    """The Biome theme folder inside the active profile's themes/ directory."""
    base = QgsApplication.userThemesFolder()
    return os.path.join(base, THEME_NAME)


def _palette_lines(palette_map):
    """Render a QPalette map to palette.txt lines: '<int ColorRole>: <color>'.

    palette_map is {QPalette.ColorRole: '#rrggbb'}. The registry reads the role
    by its integer enum value, so we resolve .value for each role.
    """
    lines = []
    for role, color in palette_map.items():
        try:
            role_int = int(role.value) if hasattr(role, "value") else int(role)
        except Exception:
            continue
        lines.append(f"{role_int}: {color}")
    return "\n".join(lines) + "\n"


def build_and_register(qss, palette_map, variables=None):
    """Write the Biome theme folder (style.qss + palette.txt [+ variables.qss])
    into the profile themes dir and register it. Returns the theme name on
    success, or None if unavailable/failed. Idempotent: re-registers cleanly.

    - qss: the application stylesheet string (Biome Mode's app_qss()).
    - palette_map: {QPalette.ColorRole: '#rrggbb'}.
    - variables: optional {name: value} written to variables.qss and usable as
      @name in the QSS (string-substituted by QGIS).
    """
    if not is_available():
        return None
    try:
        folder = _theme_dir()
        os.makedirs(folder, exist_ok=True)
        # style.qss is REQUIRED by the registry (registration fails without it).
        with open(os.path.join(folder, "style.qss"), "w", encoding="utf-8") as f:
            f.write(qss or "/* Biome */\n")
        if palette_map:
            with open(os.path.join(folder, "palette.txt"), "w", encoding="utf-8") as f:
                f.write(_palette_lines(palette_map))
        if variables:
            with open(os.path.join(folder, "variables.qss"), "w", encoding="utf-8") as f:
                f.write("".join(f"@{k}: {v};\n" for k, v in variables.items()))

        reg = QgsApplication.applicationThemeRegistry()
        # addTheme rejects duplicates; remove any prior registration first so a
        # reload / palette change re-registers the fresh folder.
        try:
            if THEME_NAME in reg.themes():
                reg.removeTheme(THEME_NAME)
        except Exception:
            pass
        reg.addTheme(THEME_NAME, folder)
        return THEME_NAME
    except Exception:
        return None


def apply():
    """Switch QGIS to the Biome theme. Returns True on success."""
    if not is_available():
        return False
    try:
        QgsApplication.setUITheme(THEME_NAME)
        return True
    except Exception:
        return False


def clear():
    """Restore QGIS's default UI theme (clears the app stylesheet + palette)."""
    if not is_available():
        return False
    try:
        QgsApplication.setUITheme("default")
        return True
    except Exception:
        return False


def unregister():
    """Remove the Biome theme from the registry (on plugin unload)."""
    if not is_available():
        return
    try:
        reg = QgsApplication.applicationThemeRegistry()
        if THEME_NAME in reg.themes():
            reg.removeTheme(THEME_NAME)
    except Exception:
        pass
