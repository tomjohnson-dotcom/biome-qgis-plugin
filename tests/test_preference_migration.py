"""Isolated preference-migration checks; no real QGIS profile is touched."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from qgis.PyQt.QtCore import QSettings


PLUGIN_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_PARENT))
from biome import preference_migration as migration  # noqa: E402


def settings(path):
    return QSettings(str(path), migration._ini_format())


def main():
    root = Path(tempfile.mkdtemp(prefix="biome-migration-test-"))
    try:
        source = root / "QGIS" / "QGIS3" / "profiles" / "old"
        active = root / "QGIS" / "QGIS4" / "profiles" / "new"
        source_ini = source / "QGIS" / "QGIS3.ini"
        destination_ini = active / "QGIS" / "QGIS4.ini"
        source_ini.parent.mkdir(parents=True)
        destination_ini.parent.mkdir(parents=True)

        old = settings(source_ini)
        old.setValue("biome/os_api_key", "test-secret")
        old.setValue("biome/quick_habitats", "source-value")
        old.setValue("BiomeMapper/activeTheme", "Consultancy")
        old.setValue("RedLineBuffers/dist1", 250)
        old.setValue("unrelated/qgis_setting", "must-not-copy")
        old.sync()

        (source / "biome_custom_features.json").write_text(
            json.dumps({"lines": {"Test feature": {"colour": "#123456"}}}),
            encoding="utf-8",
        )
        icon_dir = source / "biome_custom_icons"
        icon_dir.mkdir()
        (icon_dir / "test.svg").write_text("<svg/>", encoding="utf-8")
        cache_dir = source / "biome_basemaps"
        cache_dir.mkdir()
        (cache_dir / "cache.bin").write_bytes(b"not migrated")

        destination = settings(destination_ini)
        destination.setValue("biome/quick_habitats", "destination-wins")
        destination.sync()

        sources = migration.find_source_profiles(active)
        assert [item["path"] for item in sources] == [source.resolve()]

        result = migration.migrate_from(
            source, active_profile=active, destination_settings=destination)
        destination.sync()

        assert destination.value("biome/os_api_key") == "test-secret"
        assert destination.value("biome/quick_habitats") == "destination-wins"
        assert destination.value("BiomeMapper/activeTheme") == "Consultancy"
        assert int(destination.value("RedLineBuffers/dist1")) == 250
        assert not destination.contains("unrelated/qgis_setting")
        assert destination.value(migration._MARKER) == "imported"
        assert (active / "biome_custom_features.json").is_file()
        assert (active / "biome_custom_icons" / "test.svg").is_file()
        assert not (active / "biome_basemaps").exists()
        backup = Path(result["backup"])
        assert backup.is_file()
        backup_data = json.loads(backup.read_text(encoding="utf-8"))
        assert "test-secret" not in json.dumps(backup_data)
        assert result["settings"] == 3
        assert result["files"] == 2
        print("Preference migration tests passed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
