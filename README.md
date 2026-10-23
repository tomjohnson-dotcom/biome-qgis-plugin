# Biome QGIS plugin

Biome is a QGIS plugin for Biodiversity Net Gain habitat mapping, ecological desk studies, field survey data, and report and map production.

## Installation and updates

In QGIS, open **Plugins > Manage and Install Plugins > Settings**, select **Add**, and enter:

- Name: `Biome Updates`
- URL: `https://raw.githubusercontent.com/tomjohnson-dotcom/biome-qgis-plugin/main/plugins.xml`

Select **Reload Repository**, then install Biome from the **Not installed** tab. QGIS will subsequently offer newer published versions through Plugin Manager.

## Release process

Update `version` in `biome/metadata.txt`, run `python build_release.py`, commit the resulting catalogue and ZIP, and push to `main`.

## Licence

Biome is licensed under the GNU General Public License, version 2 or later. See `biome/LICENSE`.

