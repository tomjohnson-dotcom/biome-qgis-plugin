# Biome for QGIS

Biome is an ecological mapping and Biodiversity Net Gain (BNG) plugin for QGIS. It brings habitat mapping, condition assessment, ecological desk-study searches, field data capture, map production, and Statutory Biodiversity Metric export into one dock panel.

The current release is version 2.5 and supports QGIS 3.22 and later, including QGIS 4.

## Features

### Habitat and BNG workflow

- Map baseline and proposed area habitats, hedgerows, watercourses, and individual trees.
- Use UKHab-aligned habitat catalogues, condition assessments, distinctiveness data, strategic significance, and metric multipliers.
- Create linked baseline and proposed features and classify retention, enhancement, creation, and loss.
- Review indicative area, hedgerow, and watercourse balances, trading obligations, unmapped ground, missing data, overlaps, and geometric gross loss for de minimis screening.
- Explore eligible on-site enhancement options and compare off-site land, habitat-bank, and statutory-credit routes.
- Export a blank Statutory Biodiversity Metric or a workbook pre-filled from the mapped habitats, including creation, enhancement, and off-site entries.
- Export habitat schedules and statutory-format condition-assessment sheets.

### Mapping and feature management

- Draw red-line and blue-line boundaries and store project data in a GeoPackage beside the QGIS project.
- Navigate by address, postcode, or grid reference, use online basemaps or an offline Ordnance Survey basemap, and georeference architects' plans against the mapped site. The same workflow also supports drone orthophotos and other suitable imagery.
- Use overlap-safe habitat drawing, polygon cookie-cutting, hedgerow and watercourse trimming, tree placement, tree CSV import, reshaping, duplication, deletion, and recovery.
- Use project-persistent undo and redo across habitat, tree, ecological-feature, and trimming actions.
- Measure areas and lengths ellipsoidally, avoiding Web Mercator distortion in user-visible figures, printed scales, and scale bars.
- Generate baseline, proposed, de minimis, PRA, ecological enhancement, pond, desk-study, building and tree, and custom print layouts.
- Export Google Earth spotlight files and self-contained 3D site viewers.

### Ecological features and field work

- Map ecological survey features, constraints, enhancements, lines, and areas using the bundled SVG symbol catalogue.
- Define custom feature types and apply per-feature colour overrides.
- Use the Building & Tree Mapper, Site Plan Visualiser, and Plan, Elevation & Photo Annotator.
- Mark up house side elevations with ecological enhancement symbols, legends, call-outs, arrows, shapes, and freehand annotation; plans and site photographs are also supported.
- Generate a mobile Field Mapper for points, lines, areas, habitat parcels, condition assessments, pond HSI data, notes, and geotagged photographs.
- Import Field Mapper ZIP or GeoJSON surveys into the correct QGIS layers, including habitat, condition, tree, pond, line, and area records.

### Desk study

- Search designated sites, priority habitats, ancient woodland, SSSI Impact Risk Zones, protected-species licence records, great crested newt risk zones, water bodies, Tree Preservation Orders, and other planning constraints.
- Produce tabulated reports, feature information, configurable buffers, and print-ready desk-study maps.
- Open LPA, Local Nature Recovery Strategy, environmental mapping, biological-record, and historic-map resources centred on the site.
- Use the GCN Habitat Suitability Index calculator and survey-weather reporting.
- Detect England or Scotland from the red line and switch to the appropriate sources and terminology.
- For Scottish sites, use NatureScot and Scottish Government services, the Habitat Map of Scotland, the Scottish Ancient Woodland Inventory, National Scenic Areas, and Spatial Hub Scotland TPO data. Mapped EPSL locations are unavailable because NatureScot does not publish an equivalent spatial dataset.

### Interface

- Work from Map & Tools, Desk Study, Dashboard, Photos, and Enhance tabs.
- Use light and dark palettes and optional Biome Mode styling for the wider QGIS interface.
- Use the compact floating layer panel, hover help, onboarding, and integrated PDF user guide.

## Requirements

- QGIS 3.22 or later, or QGIS 4.
- A saved `.qgs` or `.qgz` project before project data can be created.
- Internet access for online basemaps, geocoding, external links, and live desk-study services.
- Microsoft Excel or another macro-compatible spreadsheet application to open and recalculate the exported `.xlsm` workbook.
- A free Spatial Hub Scotland authentication key for Scottish TPO searches.

## Installation

Copy the complete `biome` folder into the active QGIS profile's Python plugin directory.

Windows:

```text
C:\Users\<user>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\
```

macOS:

```text
~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/
```

Linux:

```text
~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
```

In QGIS, open **Plugins > Manage and Install Plugins**, enable **Biome**, and open the panel from the Biome toolbar button or **Plugins > Biome**.

To update, replace the plugin files and restart QGIS or reload the plugin. Updating the plugin does not replace project data.

## Getting started

- Save a new QGIS project in the site folder.
- Draw or import the red-line boundary.
- Map baseline habitats and complete condition assessments.
- Create the proposed scheme and review retention, enhancement, creation, and loss classifications.
- Run the desk study and resolve Dashboard warnings and trading obligations.
- Generate the required maps, schedules, condition sheets, and Filled Metric workbook.
- Open the exported metric in Excel, allow it to recalculate, and review the workbook results before submission.

The **Getting Started** button reopens the onboarding workflow. The **User Guide** button opens the full illustrated guide.

## Project data

Biome creates `<project>_bng_data.gpkg` beside the saved QGIS project and writes mapped ecological features to that GeoPackage. Layer configuration and project-specific settings remain in the QGIS project file. To transfer a project, copy both files and retain their relative location.

Online basemaps, temporary desk-study services, and other session-only helper layers are excluded from saved projects. Persistent survey layers are stored in the GeoPackage.

## Important limitations

- On-screen unit calculations are design and quality-control aids. The recalculated Statutory Biodiversity Metric workbook remains the submission source of truth.
- For planning applications made on or after 6 August 2026, development on a red-line site of 0.2 hectares or below is generally exempt unless it impacts on-site priority habitat. This is separate from the de minimis impact thresholds.
- From 6 August 2026, wholly temporary development permitted for five years or less is generally exempt unless it impacts on-site priority habitat, while the previous self-build and custom-build exemption no longer applies to new applications.
- Qualifying minor developments that are not exempt may consider on-site and registered off-site delivery together and may choose off-site delivery without first exhausting on-site options. Statutory credits remain a last resort.
- BNG applies to nationally significant infrastructure projects whose development consent applications are made from 2 November 2026; separate NSIP requirements apply.
- The statutory BNG workflow described by Biome applies to England. Scottish regional support provides appropriate ecological desk-study data and mapping; it does not imply that England's statutory regime applies in Scotland.
- Live desk-study results depend on third-party services and their availability, coverage, licensing, and update cycles.
- Spatial datasets support professional assessment; they do not replace field survey, local records searches, consultation, or ecological judgement where these are required.

## Main source files

```text
biome/
|-- __init__.py                 QGIS entry point
|-- metadata.txt                QGIS plugin metadata
|-- bng_plugin.py               Plugin lifecycle and integration
|-- main_panel.py               Main dock panel and workflow
|-- habitat_form.py             Habitat data entry and drawing
|-- habitat_data.py             Habitat catalogue and attributes
|-- layer_manager.py            Persistent layers and styles
|-- condition_dialog.py         Habitat condition assessments
|-- measurements.py             Measurement and de minimis tools
|-- manage_screen.py            Feature review and editing
|-- desk_study_tab.py           Desk-study interface and reports
|-- designated_sites.py         National and regional data services
|-- region.py                   England and Scotland source selection
|-- gridref_dialog.py           Ecological Feature Mapper
|-- building_tree_tab.py        Building and tree tools
|-- plan_annotator.py           Plan, elevation and photo mark-up
|-- map_exporter.py             Print layouts and map exports
|-- xlsm_exporter.py            Filled metric workbook export
|-- field_mapper_app/           Mobile Field Mapper web application
|-- field_mapper_export.py      Field Mapper package generation
|-- field_mapper_import.py      Field survey import
|-- _guide_content.py           User-guide content
|-- _gen_user_guide.py          PDF user-guide generator
|-- biome_test_suite.py         In-QGIS validation suite
|-- styles/                     QGIS layer styles
|-- Symbols/                    Ecological SVG symbols
|-- guide_assets/               User-guide illustrations
|-- biome_user_guide.pdf        Generated user guide
`-- README.md                   This file
```

## Development and validation

The same codebase supports PyQt5/QGIS 3 and PyQt6/QGIS 4. Use only `qgis.PyQt` imports and fully scoped Qt and QGIS enums. Run syntax and static checks with the system Python, then test behaviour in both supported QGIS generations. See `CLAUDE.md` for the compatibility, geometry, theming, Field Mapper, and regional-data rules that govern changes.

Regenerate the guide from the live habitat and feature registries with a QGIS Python environment:

```text
"C:\Program Files\QGIS 4.0.3\bin\python-qgis.bat" _gen_user_guide.py
```

## Licence

GPL-2.0-or-later. See `LICENSE`.
