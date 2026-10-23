"""Safe first-run migration of Biome preferences between QGIS profiles.

Only Biome-owned settings groups and user customisation files are copied.
Project data, caches, logs, and general QGIS preferences are deliberately
excluded.  The destination profile always remains authoritative.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QMessageBox,
    QPushButton, QVBoxLayout,
)
from qgis.core import QgsApplication


_MIGRATION_VERSION = 1
_MARKER = "biome/migration/v1_decision"
_SOURCE_MARKER = "biome/migration/v1_source"
_OWNED_PREFIXES = ("biome/", "BiomeMapper/", "RedLineBuffers/")
_PROFILE_FILES = (
    "biome_clipboard_style.json",
    "biome_custom_features.json",
)
_PROFILE_DIRS = (
    "biome_custom_icons",
    os.path.join("themes", "Biome"),
)


def _ini_format():
    try:
        return QSettings.Format.IniFormat
    except AttributeError:  # Qt 5 unscoped enum
        return QSettings.IniFormat


def _active_profile():
    return Path(QgsApplication.qgisSettingsDirPath()).resolve()


def _settings_file(profile):
    qgis_dir = profile / "QGIS"
    candidates = sorted(qgis_dir.glob("QGIS*.ini"))
    return candidates[-1] if candidates else None


def _source_settings(profile):
    ini = _settings_file(profile)
    return QSettings(str(ini), _ini_format()) if ini else None


def _owned_keys(settings):
    if settings is None:
        return []
    return sorted(
        key for key in settings.allKeys()
        if key != _MARKER and key != _SOURCE_MARKER
        and any(key.startswith(prefix) for prefix in _OWNED_PREFIXES)
    )


def _has_biome_data(profile):
    settings = _source_settings(profile)
    if _owned_keys(settings):
        return True
    return any((profile / rel).exists()
               for rel in _PROFILE_FILES + _PROFILE_DIRS)


def find_source_profiles(active_profile=None):
    """Return prior/other QGIS profiles containing Biome preferences."""
    active = Path(active_profile or _active_profile()).resolve()
    # <roaming>/QGIS/QGIS4/profiles/default -> <roaming>/QGIS
    try:
        qgis_root = active.parent.parent.parent
    except Exception:
        return []
    found = []
    for version_dir in sorted(qgis_root.glob("QGIS*"), reverse=True):
        profiles_dir = version_dir / "profiles"
        if not profiles_dir.is_dir():
            continue
        for profile in sorted(profiles_dir.iterdir()):
            if not profile.is_dir() or profile.resolve() == active:
                continue
            if _has_biome_data(profile):
                ini = _settings_file(profile)
                stamp = ini.stat().st_mtime if ini and ini.exists() else 0
                found.append({
                    "path": profile.resolve(),
                    "label": f"{version_dir.name} — {profile.name}",
                    "modified": stamp,
                })
    return sorted(found, key=lambda item: item["modified"], reverse=True)


def _json_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _backup_value(key, value):
    """Do not create additional plaintext copies of service credentials."""
    lowered = key.lower()
    if any(word in lowered for word in ("api_key", "authkey", "password", "secret")):
        return "<stored credential — value not duplicated in backup>"
    return _json_value(value)


def _write_backup(profile, settings):
    """Write a redacted destination snapshot before import."""
    backup = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "migration_version": _MIGRATION_VERSION,
        "settings": {
            key: _backup_value(key, settings.value(key))
            for key in _owned_keys(settings)
        },
        "existing_files": [
            rel for rel in _PROFILE_FILES + _PROFILE_DIRS
            if (profile / rel).exists()
        ],
    }
    path = profile / ("biome_settings_backup_"
                      + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    path.write_text(json.dumps(backup, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def migrate_from(source_profile, active_profile=None, destination_settings=None):
    """Copy recognised settings/files and return a result summary."""
    source_profile = Path(source_profile).resolve()
    active = Path(active_profile or _active_profile()).resolve()
    source = _source_settings(source_profile)
    destination = destination_settings or QSettings()
    keys = _owned_keys(source)
    backup = _write_backup(active, destination)
    added_keys = []
    copied_paths = []
    try:
        for key in keys:
            if not destination.contains(key):
                destination.setValue(key, source.value(key))
                added_keys.append(key)

        for rel in _PROFILE_FILES:
            src = source_profile / rel
            dst = active / rel
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied_paths.append(dst)

        for rel in _PROFILE_DIRS:
            src = source_profile / rel
            dst = active / rel
            if src.is_dir() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst)
                copied_paths.append(dst)

        destination.setValue(_MARKER, "imported")
        destination.setValue(_SOURCE_MARKER, str(source_profile))
        destination.sync()
    except Exception:
        # Roll back only values and paths created by this attempt. Existing
        # destination customisation is never deleted or replaced by default.
        for key in added_keys:
            destination.remove(key)
        destination.remove(_MARKER)
        destination.remove(_SOURCE_MARKER)
        destination.sync()
        for path in reversed(copied_paths):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except Exception:
                pass
        raise
    return {
        "settings": len(added_keys),
        "files": len(copied_paths),
        "backup": str(backup),
    }


class _MigrationDialog(QDialog):
    def __init__(self, sources, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Biome preferences")
        self.setMinimumWidth(470)
        self.sources = sources
        layout = QVBoxLayout(self)
        heading = QLabel("Biome preferences were found in another QGIS profile.")
        heading.setStyleSheet("font-size:14px;font-weight:600;")
        layout.addWidget(heading)
        explanation = QLabel(
            "Import API keys, custom features, report styling, map settings, "
            "shortcuts, and other Biome preferences into this profile. "
            "Projects, caches, logs, and general QGIS settings are not copied.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        layout.addWidget(QLabel("Import from:"))
        self.source_combo = QComboBox()
        for source in sources:
            self.source_combo.addItem(source["label"], str(source["path"]))
        layout.addWidget(self.source_combo)
        existing_note = QLabel(
            "Existing preferences in this profile take precedence; only "
            "missing items will be imported.")
        existing_note.setWordWrap(True)
        layout.addWidget(existing_note)
        note = QLabel(
            "A backup is created in this QGIS profile before importing. "
            "API-key values are copied without being shown.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#54635A;font-size:11px;")
        layout.addWidget(note)
        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton(
            "Import preferences", QDialogButtonBox.ButtonRole.AcceptRole)
        self.fresh_button = buttons.addButton(
            "Start fresh", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.import_button.clicked.connect(self.accept)
        self.fresh_button.clicked.connect(self._start_fresh)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.start_fresh = False

    def _start_fresh(self):
        self.start_fresh = True
        self.accept()

    def selected_source(self):
        return Path(self.source_combo.currentData())


def maybe_migrate(parent=None):
    """Offer first-run migration once per destination QGIS profile."""
    destination = QSettings()
    if destination.contains(_MARKER):
        return False
    active = _active_profile()
    sources = find_source_profiles(active)
    if not sources:
        destination.setValue(_MARKER, "no_source")
        destination.sync()
        return False

    dialog = _MigrationDialog(sources, parent)
    try:
        accepted = QDialog.DialogCode.Accepted
    except AttributeError:
        accepted = QDialog.Accepted
    if dialog.exec() != accepted:
        return False  # Cancel means ask again next time.
    if dialog.start_fresh:
        destination.setValue(_MARKER, "fresh")
        destination.sync()
        return False
    try:
        result = migrate_from(dialog.selected_source(), active)
    except Exception as exc:
        QMessageBox.warning(
            parent, "Biome preference migration",
            f"Biome could not import the preferences. Nothing existing was "
            f"changed.\n\n{exc}")
        return False
    QMessageBox.information(
        parent, "Biome preferences imported",
        f"Imported {result['settings']} settings and {result['files']} "
        "customisation items. Restart QGIS if an interface theme does not "
        "update immediately.")
    return True

