"""Offline-basemap discovery checks; no real QGIS profile is touched."""

import shutil
import sys
import tempfile
from pathlib import Path


PLUGIN_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_PARENT))
from biome import zoomstack  # noqa: E402


def main():
    root = Path(tempfile.mkdtemp(prefix="biome-zoomstack-test-"))
    try:
        active = root / "QGIS" / "QGIS5" / "profiles" / "Clean Test"
        matching = root / "QGIS" / "QGIS4" / "profiles" / "Clean Test"
        differently_named = root / "QGIS" / "QGIS3" / "profiles" / "default"
        active.mkdir(parents=True)
        matching.mkdir(parents=True)
        differently_named.mkdir(parents=True)

        fallback_data = differently_named / "biome_basemaps"
        fallback_data.mkdir()
        (fallback_data / "OS_Open_Zoomstack.gpkg").write_bytes(b"test")
        assert Path(zoomstack._legacy_storage_dir(active)) == fallback_data

        # A matching profile name takes precedence even when another profile
        # also has valid data.
        preferred_data = matching / "roavr_basemaps"
        preferred_data.mkdir()
        (preferred_data / "open_zoomstack.gpkg").write_bytes(b"test")
        assert Path(zoomstack._legacy_storage_dir(active)) == preferred_data

        # Custom locations recorded in a future-version INI are supported.
        custom = root / "shared" / "offline maps"
        custom.mkdir(parents=True)
        (custom / "zoomstack_custom.gpkg").write_bytes(b"test")
        ini_dir = matching / "QGIS"
        ini_dir.mkdir()
        (ini_dir / "QGIS4.ini").write_text(
            "[biome]\nzoomstack_dir=" + str(custom).replace("\\", "/") + "\n",
            encoding="utf-8",
        )
        assert Path(zoomstack._legacy_storage_dir(active)) == custom
        print("Offline basemap profile-discovery tests passed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
