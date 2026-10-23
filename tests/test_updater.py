"""Focused checks for Biome's in-place updater helpers."""

import shutil
import tempfile
from pathlib import Path

from biome import updater


def main():
    assert updater._version_parts("2.10") > updater._version_parts("2.9")
    assert updater._version_parts("3") > updater._version_parts("2.99")
    assert updater._version_parts("") == (0,)

    root = Path(tempfile.mkdtemp(prefix="biome-updater-test-"))
    try:
        installed = root / "installed" / "biome"
        staged = root / "staged" / "biome"
        installed.mkdir(parents=True)
        staged.mkdir(parents=True)
        (installed / "changed.py").write_text("old", encoding="utf-8")
        (installed / "preserved.txt").write_text("keep", encoding="utf-8")
        (staged / "changed.py").write_text("new", encoding="utf-8")
        (staged / "added.py").write_text("added", encoding="utf-8")

        target = type("UpdaterTarget", (), {"plugin_dir": installed})()
        updater.BiomeUpdater._copy_update(target, staged)

        assert (installed / "changed.py").read_text(encoding="utf-8") == "new"
        assert (installed / "added.py").read_text(encoding="utf-8") == "added"
        assert (installed / "preserved.txt").read_text(encoding="utf-8") == "keep"
        print("Updater tests passed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
