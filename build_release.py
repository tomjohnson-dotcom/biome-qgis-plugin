"""Build a Biome ZIP and regenerate the QGIS repository catalogue."""

import argparse
from configparser import ConfigParser
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape
import compileall
import re
import shutil
import zipfile


ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT / "biome"
RELEASE_DIR = ROOT / "releases"
REPOSITORY_URL = "https://github.com/tomjohnson-dotcom/biome-qgis-plugin"
RAW_BASE = "https://raw.githubusercontent.com/tomjohnson-dotcom/biome-qgis-plugin/main"


def metadata():
    parser = ConfigParser(interpolation=None)
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    return parser["general"]


def set_metadata_version(version):
    """Update only the metadata version line, preserving the hand-written file."""
    path = PLUGIN_DIR / "metadata.txt"
    text = path.read_bytes().decode("utf-8")
    updated, count = re.subn(
        r"(?m)^version\s*=.*$", f"version={version}", text, count=1
    )
    if count != 1:
        raise RuntimeError("Could not update the plugin version in metadata.txt")
    path.write_bytes(updated.encode("utf-8"))


def build_zip(version, archive_name=None):
    RELEASE_DIR.mkdir(exist_ok=True)
    output = RELEASE_DIR / (archive_name or f"biome-{version}.zip")
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(PLUGIN_DIR)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".log"}:
                continue
            archive.write(path, Path("biome") / relative)
    return output


def write_catalogue(info, version, archive):
    description = escape(info.get("description", ""))
    about = escape(info.get("about", ""))
    tags = escape(info.get("tags", ""))
    today = date.today().isoformat()
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<plugins>
  <pyqgis_plugin name="Biome" version="{escape(version)}">
    <description><![CDATA[{description}]]></description>
    <about><![CDATA[{about}]]></about>
    <version>{escape(version)}</version>
    <qgis_minimum_version>{escape(info.get("qgisminimumversion", "3.22"))}</qgis_minimum_version>
    <qgis_maximum_version>{escape(info.get("qgismaximumversion", "4.99"))}</qgis_maximum_version>
    <author_name>{escape(info.get("author", "Tom Johnson"))}</author_name>
    <homepage>{REPOSITORY_URL}#readme</homepage>
    <tracker>{REPOSITORY_URL}/issues</tracker>
    <repository>{REPOSITORY_URL}</repository>
    <icon>{RAW_BASE}/biome/icon.png</icon>
    <tags>{tags}</tags>
    <download_url>{RAW_BASE}/releases/{archive.name}</download_url>
    <file_name>biome.zip</file_name>
    <experimental>{escape(info.get("experimental", "False"))}</experimental>
    <deprecated>{escape(info.get("deprecated", "False"))}</deprecated>
    <create_date>{today}</create_date>
    <update_date>{today}</update_date>
  </pyqgis_plugin>
</plugins>
'''
    (ROOT / "plugins.xml").write_text(xml, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version",
        help="Version to write into metadata.txt and the repository catalogue",
    )
    parser.add_argument(
        "--archive-name",
        help="ZIP filename inside releases (for example biome-latest.zip)",
    )
    args = parser.parse_args()
    if args.version:
        set_metadata_version(args.version.strip())
    if not compileall.compile_dir(PLUGIN_DIR, quiet=1, force=True):
        raise SystemExit("Python compilation failed")
    shutil.rmtree(PLUGIN_DIR / "__pycache__", ignore_errors=True)
    info = metadata()
    version = info["version"].strip()
    archive = build_zip(version, args.archive_name)
    write_catalogue(info, version, archive)
    print(f"Built {archive.name} ({archive.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    main()
