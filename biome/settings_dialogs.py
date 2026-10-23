# settings_dialogs.py — SettingsDialogsMixin, extracted verbatim from main_panel.py (2026-07-22 split).
# Methods run on BNGMainPanel via mixin inheritance; `self` is the panel.


from qgis.PyQt.QtCore import Qt
from qgis.core import Qgis
from . import theme
from .designated_sites import clear_ds_cache
from .config import (get_os_api_key, set_os_api_key,
    get_os_ngd_api_key, set_os_ngd_api_key, get_google_maps_api_key,
    set_google_maps_api_key, get_redline_snap_to_habitats,
    set_redline_snap_to_habitats, get_phi_group_nearby,
    set_phi_group_nearby, get_enabled_basemap_buttons,
    set_enabled_basemap_buttons)


from .panel_widgets import (_svg_pixmap, _svg_icon)

class SettingsDialogsMixin:
    def _open_settings_dialog(self):
        """Biome settings hub — one row per feature (OS API key, report-table
        style, final-map branding, Quick Habitats). Each row shows its current
        state and one button into the focused editor; the longer explanations
        live in the row tooltips and the editors themselves."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
            QListWidget, QListWidgetItem, QStackedWidget, QScrollArea,
            QWidget, QSizePolicy,
        )

        BTN_QSS = (
            "QPushButton{background:#FFFFFF;color:#153F2C;border:1px solid #AEBDB3;"
            "border-radius:6px;padding:6px 14px;font-weight:500;}"
            "QPushButton:hover{background:#E4F0E8;border-color:#72927E;}")

        def _key_status():
            key = get_os_api_key()
            if key:
                tail = key[-4:] if len(key) >= 8 else ""
                return "Key set · ····" + tail if tail else "Key set"
            return "Not set — needed for the OS Outdoor basemap"

        def _gkey_status():
            key = get_google_maps_api_key()
            if key:
                tail = key[-4:] if len(key) >= 8 else ""
                return "Key set · ····" + tail if tail else "Key set"
            return "Not set — needed for the 3D Spotlight export"

        def _ngd_key_status():
            key = get_os_ngd_api_key()
            if key:
                tail = key[-4:] if len(key) >= 8 else ""
                return "Key set · ····" + tail if tail else "Key set"
            return "Not set — premium mapping off; free water sources still work"

        def _table_status():
            from .clipboard_utils import load_style
            s = load_style()
            fills = {"alternating": "banded rows", "single": "single row fill",
                     "none": "no row fill"}
            return " · ".join([
                "Borders" if s.get("border_on") else "No borders",
                fills.get(s.get("fill_mode"), "banded rows"),
                "styled header" if s.get("header_on") else "plain header"])

        def _branding_status():
            from .branding import active_theme_name
            name = active_theme_name()
            return (f"Active theme: {name}" if name
                    else "No theme yet — created with your first final map")

        def _qh_status():
            items = self._load_quick_habitats()
            if not items:
                return "None pinned"
            return f"{len(items)} pinned · {items[0]['sub']} first"

        def _banner_status():
            from .banner_widget import LivingBanner
            b = getattr(self, "_banner", None)
            scene = (b.scene_enabled() if b
                     else LivingBanner._load_scene())
            if not scene:
                return "Switched off — a plain header, nothing moves"
            m = b.cycle_minutes() if b else LivingBanner._load_cycle_minutes()
            party = b.party() if b else LivingBanner._load_party()
            if m <= 0:
                text = "Follows the real sun at the site — day, dusk and night"
            elif m >= 60 and m % 60 == 0:
                span = f"{m // 60} hour" + ("s" if m > 60 else "")
                text = f"Fast cycle — a full day every {span}"
            else:
                text = f"Fast cycle — a full day every {m} minutes"
            wmode = b.weather_mode() if b else LivingBanner._load_weather_mode()
            if wmode == "random":
                text += " · random weather"
            elif wmode == "auto":
                text += " · your live weather"
            elif wmode == "live":
                text += " · the site's live weather"
            return text + (" · safari mode on" if party else "")

        def _radius_status():
            from .config import (get_buffer_radius, get_search_radius,
                                 get_pond_inner_radius)

            def _fmt(m):
                if m < 1000:
                    return f"{m} m"
                return f"{m // 1000} km" if m % 1000 == 0 else f"{m / 1000:.1f} km"
            return (f"Desk study {_fmt(get_buffer_radius())} · TPO "
                    f"{_fmt(get_search_radius('tpo'))} outer · standing water "
                    f"{_fmt(get_pond_inner_radius())}–"
                    f"{_fmt(get_search_radius('standing_water'))}")

        def _redline_snap_status():
            return ("On · red and blue lines snap to habitat polygon edges"
                    if get_redline_snap_to_habitats()
                    else "Off · boundaries are drawn without habitat-edge snapping")

        def _basemap_buttons_status():
            enabled = get_enabled_basemap_buttons()
            labels = {
                "Google Hybrid": "Hybrid", "Google Satellite": "Satellite",
                "__offline__": "Offline", "ESRI World Imagery": "ESRI",
                "OS Outdoor": "OS Outdoor", "OS Premium": "Premium"}
            shown = [labels[key] for key in labels if key in enabled]
            return "Shown: " + (" · ".join(shown) if shown else "none")

        def _phi_grouping_status():
            choice = get_phi_group_nearby()
            if choice is None:
                return "Not chosen · you will be asked when the report is first run"
            return ("Group same-habitat polygons within 5 m"
                    if choice else "Keep every mapped polygon as a separate row")

        def _enhance_tab_status():
            from qgis.PyQt.QtCore import QSettings
            shown = QSettings().value(
                "biome/ui/show_enhance_tab", False, type=bool)
            return ("Shown in the main navigation"
                    if shown else "Hidden · off by default")

        def _toggle_enhance_tab():
            from qgis.PyQt.QtCore import QSettings
            settings = QSettings()
            shown = settings.value(
                "biome/ui/show_enhance_tab", False, type=bool)
            self._set_enhance_tab_visible(not shown)

        def _annotator_presets_status():
            from .plan_annotator import load_annotator_presets
            items = load_annotator_presets()
            if len(items) <= 4:
                return " · ".join(items)
            return f"{len(items)} shortcuts · " + " · ".join(items[:3]) + "…"

        def _choose_annotator_shortcuts():
            from .plan_annotator import choose_annotator_presets
            if choose_annotator_presets(dlg):
                annotator = getattr(self, "_plan_annotator_dlg", None)
                if annotator is not None:
                    try:
                        annotator._rebuild_preset_buttons()
                    except Exception:
                        pass

        dlg = QDialog(self)
        dlg.setWindowTitle("Biome settings")
        dlg.setMinimumSize(760, 560)
        dlg.resize(820, 620)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        heading = QLabel("Settings")
        heading.setStyleSheet(
            "font-size:18px;font-weight:600;color:#1A2420;"
            "font-family:'Inter SemiBold','Inter','Segoe UI',sans-serif;")
        v.addWidget(heading)
        subheading = QLabel(
            "Choose a category. Each card explains what the setting changes, "
            "shows its current state, and opens the relevant controls.")
        subheading.setStyleSheet("font-size:11px;color:#3F5047;")
        subheading.setWordWrap(True)
        v.addWidget(subheading)

        body = QHBoxLayout()
        body.setSpacing(12)
        nav = QListWidget()
        nav.setFixedWidth(192)
        nav.setSpacing(2)
        nav.setStyleSheet(
            "QListWidget{background:#EDF1EE;border:1px solid #D5DDD7;border-radius:9px;"
            "padding:6px;font-size:12px;}"
            "QListWidget::item{padding:10px 9px;border-radius:7px;color:#263A30;}"
            "QListWidget::item:selected{background:#CFE4D6;color:#0F4B2E;font-weight:600;}"
            "QListWidget::item:hover:!selected{background:#DDE7E0;}")
        pages = QStackedWidget()
        category_names = (
            "General", "Map & Tools", "Desk Study",
            "Reports & maps", "Connections", "QGIS setup", "Advanced")
        category_meta = {
            "General": ("contrast", "How Biome looks and behaves."),
            "Map & Tools": ("map", "Drawing, annotation, boundaries, and site setup."),
            "Desk Study": ("map-search", "Search distances and ecological data handling."),
            "Reports & maps": ("file-text", "The appearance and wording of exported outputs."),
            "Connections": ("key", "External map and data-service credentials."),
            "QGIS setup": ("rocket", "Windows and QGIS installation guidance."),
            "Advanced": ("settings", "Specialist and troubleshooting controls."),
        }
        category_layouts = {}
        for category in category_names:
            nav_item = QListWidgetItem(_svg_icon(
                category_meta[category][0], "#365B49", 17), category)
            nav.addItem(nav_item)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page = QWidget()
            page.setStyleSheet("background:#FFFFFF;")
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(14, 10, 14, 12)
            page_layout.setSpacing(9)
            intro = QFrame()
            intro.setStyleSheet(
                "QFrame{background:#E8F1EB;border:1px solid #BED1C3;"
                "border-radius:10px;}")
            intro_layout = QHBoxLayout(intro)
            intro_layout.setContentsMargins(12, 10, 12, 10)
            intro_icon = QLabel()
            intro_icon.setFixedSize(38, 38)
            intro_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            intro_icon.setPixmap(_svg_pixmap(
                category_meta[category][0], "#276647", 25))
            intro_icon.setStyleSheet(
                "background:#D2E7D8;border:none;border-radius:8px;")
            intro_layout.addWidget(intro_icon)
            intro_text = QVBoxLayout()
            intro_text.setSpacing(1)
            intro_title = QLabel(category)
            intro_title.setStyleSheet(
                "font-size:15px;font-weight:600;color:#1D3E2E;border:none;")
            intro_desc = QLabel(category_meta[category][1])
            intro_desc.setStyleSheet(
                "font-size:11px;color:#344A3E;border:none;")
            intro_desc.setWordWrap(True)
            intro_text.addWidget(intro_title)
            intro_text.addWidget(intro_desc)
            intro_layout.addLayout(intro_text, 1)
            page_layout.addWidget(intro)
            scroll.setWidget(page)
            pages.addWidget(scroll)
            category_layouts[category] = page_layout
        nav.currentRowChanged.connect(pages.setCurrentIndex)
        nav.setCurrentRow(0)
        body.addWidget(nav)
        body.addWidget(pages, 1)
        v.addLayout(body, 1)

        row_categories = {
            "Appearance": "General", "Living banner": "General",
            "Date format": "General", "Enhance tab": "General",
            "Quick Habitats": "Map & Tools",
            "Basemap buttons": "Map & Tools",
            "Annotator shortcuts": "Map & Tools",
            "Boundary snapping": "Map & Tools",
            "Site region": "Map & Tools",
            "Search distances": "Desk Study",
            "Priority habitat reports": "Desk Study",
            "Report table style": "Reports & maps",
            "Final map branding": "Reports & maps",
            "Map titles & headings": "Reports & maps",
            "Ordnance Survey API key": "Connections",
            "OS NGD premium key": "Connections",
            "Google Maps API key": "Connections",
            "Spatial Hub Scotland key": "Connections",
            "Default QGIS version": "QGIS setup",
            "Live Claude Edit": "Advanced",
        }

        refreshers = []   # callables — re-run after an editor closes

        def _refresh_rows():
            for refresh in refreshers:
                try:
                    refresh()
                except Exception:
                    pass

        def _hline(target=None):
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color:#D9DED7;")
            (target or v).addWidget(sep)

        def _add_row(icon, title_text, status_fn, btn_text, opener, tip,
                     first=False, key_url=None, key_link_text="Get an API key"):
            target = category_layouts[row_categories.get(title_text, "General")]
            card = QFrame()
            card.setObjectName("settingsCard")
            card.setStyleSheet(
                "QFrame#settingsCard{background:#FFFFFF;border:1px solid #C8D3CB;"
                "border-radius:10px;}"
                "QFrame#settingsCard:hover{background:#F3F8F4;border-color:#91AD9B;}"
                "QFrame#settingsCard QLabel{border:none;background:transparent;}")
            row = QHBoxLayout(card)
            row.setContentsMargins(11, 11, 11, 11)
            row.setSpacing(11)
            ic = QLabel()
            ic.setFixedSize(42, 42)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic.setPixmap(_svg_pixmap(icon, "#155F3B", 23))
            ic.setStyleSheet(
                "background:#D8EBDE;border:none;border-radius:9px;")
            row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(4)
            title = QLabel(title_text)
            title.setStyleSheet("font-size:13px;font-weight:600;color:#152B20;")
            explanation = QLabel(tip)
            explanation.setWordWrap(True)
            explanation.setStyleSheet("font-size:11px;color:#3D4D44;")
            status = QLabel(status_fn())
            status.setWordWrap(True)
            status.setStyleSheet(
                "font-size:11px;font-weight:500;color:#174C32;background:#DDEEE3;"
                "border:1px solid #AFCCB8;border-radius:7px;padding:3px 7px;")
            col.addWidget(title)
            col.addWidget(explanation)
            col.addWidget(status)
            key_link = None
            if key_url:
                key_link = QLabel(
                    f'<a href="{key_url}">{key_link_text} &#8599;</a>')
                key_link.setOpenExternalLinks(True)
                key_link.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextBrowserInteraction)
                key_link.setStyleSheet(
                    "font-size:11px;font-weight:600;color:#17643D;")
                key_link.setToolTip("Open the provider's website")
                col.addWidget(key_link)
            row.addLayout(col, 1)
            btn = QPushButton(btn_text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(BTN_QSS)

            def _clicked():
                opener()
                _refresh_rows()

            btn.clicked.connect(_clicked)
            row.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
            for w in (ic, title, explanation, status):
                w.setToolTip(tip)
            def _refresh_row():
                current_status = status_fn()
                status.setText(current_status)
                if key_link is not None:
                    key_link.setVisible(current_status.startswith("Not set"))

            _refresh_row()
            refreshers.append(_refresh_row)
            target.addWidget(card)

        _add_row(
            "key", "Ordnance Survey API key", _key_status, "Change…",
            lambda: self._open_os_key_dialog(dlg),
            "Used for the OS Outdoor basemap. Stored in your QGIS profile, "
            "so it survives plugin updates.",
            first=True,
            key_url="https://osdatahub.os.uk/",
            key_link_text="Get a key from OS Data Hub")
        _add_row(
            "map", "Basemap buttons", _basemap_buttons_status, "Choose…",
            lambda: self._open_basemap_buttons_dialog(dlg),
            "Choose which basemap options appear in Biome's Mapping and Desk "
            "Study selector bars.")
        _add_row(
            "key", "OS NGD premium key", _ngd_key_status, "Change…",
            lambda: self._open_os_ngd_key_dialog(dlg),
            "One premium OS NGD project key for both the detailed basemap and "
            "standing-water search. Enable OS NGD API – Tiles and OS NGD API "
            "– Features on that Data Hub project.",
            key_url="https://osdatahub.os.uk/",
            key_link_text="Set up a project in OS Data Hub")
        _add_row(
            "key", "Google Maps API key", _gkey_status, "Change…",
            lambda: self._open_google_key_dialog(dlg),
            "Used for the 3D Spotlight export (Google photorealistic 3D "
            "tiles). Needs the Map Tiles API enabled on its Google Cloud "
            "project. Stored in your QGIS profile, so it survives plugin "
            "updates.",
            key_url=("https://console.cloud.google.com/apis/library/"
                     "tile.googleapis.com"),
            key_link_text="Get a key from Google Cloud")
        def _shkey_status():
            from .config import get_spatialhub_authkey
            key = get_spatialhub_authkey()
            if key:
                tail = key[-4:] if len(key) >= 8 else ""
                return "Key set · ····" + tail if tail else "Key set"
            return "Not set — needed for TPOs on Scottish sites"
        _add_row(
            "key", "Spatial Hub Scotland key", _shkey_status, "Change…",
            lambda: self._open_spatialhub_key_dialog(dlg),
            "Used for Tree Preservation Orders on a Scottish site (the "
            "Improvement Service's national TPO dataset). Free account at "
            "data.spatialhub.scot; the key is on your user profile page. "
            "Stored in your QGIS profile, so it survives plugin updates.",
            key_url="https://data.spatialhub.scot/",
            key_link_text="Get a key from Spatial Hub Scotland")
        _add_row(
            "table", "Report table style", _table_status, "Customise…",
            self._open_report_style_dialog,
            "How report tables look when copied into your Google Docs report "
            "— cell borders, row fills and header. A live preview shows the "
            "effect.")
        _add_row(
            "palette", "Final map branding", _branding_status, "Customise…",
            self._open_branding_dialog,
            "Theme colours, logo and footnote for the print-ready map sidebar "
            "(Create Final Map). Save themes and pick the active one.")
        _add_row(
            "leaf", "Quick Habitats", _qh_status, "Customise…",
            self._open_quick_habitats_dialog,
            "The pinned habitats under “Quick Habitats” on the Baseline and "
            "Proposed screens. Pick your own, drop the ones you never use "
            "and reorder them.")
        _add_row(
            "map-pin-plus", "Annotator shortcuts", _annotator_presets_status,
            "Customise…", _choose_annotator_shortcuts,
            "The ecological feature buttons pinned at the top of the Plan, "
            "Elevation & Photo Annotator. Swift Box, Bird Box, Bat Box, and "
            "PRF are included by default.")
        _add_row(
            "leaf", "Enhance tab", _enhance_tab_status, "Show / hide",
            _toggle_enhance_tab,
            "Show or hide the optional Enhance tab in the main navigation. "
            "It is hidden by default; changing this does not remove data.")
        _add_row(
            "map-pin", "Boundary snapping", _redline_snap_status, "Customise…",
            lambda: self._open_redline_snapping_dialog(dlg),
            "When enabled, drawing a redline or blue line snaps to the "
            "vertices and edges of existing baseline and proposed "
            "area-habitat polygons. Enabled by default.")
        _add_row(
            "map-search", "Search distances", _radius_status, "Customise…",
            lambda: self._open_search_radius_dialog(dlg),
            "One shared distance controls the wider Desk Study searches. Two "
            "shorter local distances control TPO and pond searches and can also "
            "be used as general redline buffers.")
        _add_row(
            "layers", "Priority habitat reports", _phi_grouping_status, "Customise…",
            lambda: self._open_phi_grouping_dialog(dlg),
            "Choose whether Priority Habitats Inventory polygons of the same "
            "habitat type within 5 m are combined into one report row. The "
            "saved choice is used without asking each time.")

        def _region_status():
            from . import region
            return region.status_text()
        _add_row(
            "world", "Site region", _region_status, "Customise…",
            lambda: self._open_region_dialog(dlg),
            "Which country's data services the desk study uses. Auto reads "
            "the redline: a Scottish site fetches designated sites from "
            "NatureScot / Scottish Government (National Scenic Areas in place "
            "of AONBs) and priority habitats from the Habitat Map of Scotland; "
            "EPSL has no Scottish source. Force a region for sites right on "
            "the border.")
        def _titles_status():
            from .map_exporter import (
                AUTO_TITLES, DESK_TITLES, DESK_LEGEND_DEFAULTS,
                _TITLE_OVERRIDE_GROUP, _DESK_TITLE_GROUP, _DESK_LEGEND_GROUP,
                get_title_override)
            n = sum(1 for mt in AUTO_TITLES
                    if get_title_override(_TITLE_OVERRIDE_GROUP, mt))
            n += sum(1 for mt in DESK_TITLES
                     if get_title_override(_DESK_TITLE_GROUP, mt))
            n += sum(1 for mt in DESK_LEGEND_DEFAULTS
                     if get_title_override(_DESK_LEGEND_GROUP, mt))
            if not n:
                return "All shipped defaults"
            return f"{n} reworded from the shipped defaults"

        def _date_status():
            from .config import format_date
            return f"Dates read as {format_date()}"

        _add_row(
            "tag", "Map titles & headings", _titles_status, "Customise…",
            lambda: self._open_map_titles_dialog(dlg),
            "Permanently reword the default titles — the Create Final Map "
            "defaults, the desk study map titles and their legend headings. "
            "One-off edits (the title field, right-click rename) still layer "
            "on top for the session.")
        _add_row(
            "clock", "Date format", _date_status, "Change…",
            lambda: self._open_date_format_dialog(dlg),
            "How dates read on printed maps, in date pickers and on the "
            "survey forms — e.g. 17 July 2026 vs 17/07/2026. File names and "
            "the exported metric workbook keep their fixed formats.")
        _add_row(
            "cloud-sun", "Living banner", _banner_status, "Customise…",
            self._open_banner_cycle_dialog,
            "How fast the header landscape's day passes. Real time follows "
            "actual sunrise and sunset at the site; the fast cycles compress "
            "a whole day — sun, moon and wildlife — into minutes.")

        def _theme_mode_status():
            pref = theme.theme_preference()
            if pref == "light":
                return "Always light."
            if pref == "dark":
                return "Always dark."
            return ("Auto — follows Windows (currently %s)."
                    % theme.current_mode())

        _add_row(
            "contrast", "Appearance",
            _theme_mode_status, "Change…",
            lambda: self._open_theme_mode_dialog(dlg),
            "Light or dark look for Biome (and, with Biome Mode on, the whole "
            "QGIS window). Auto follows the Windows setting; picking Light or "
            "Dark here applies in seconds — far faster than flipping Windows "
            "with QGIS open.")

        def _default_qgis_status():
            from qgis.PyQt.QtCore import QCoreApplication
            import os
            executable = QCoreApplication.applicationFilePath()
            version_folder = os.path.basename(
                os.path.dirname(os.path.dirname(executable)))
            return (f"This copy is running from {version_folder}"
                    if version_folder else "Use the QGIS version currently open")

        _add_row(
            "rocket", "Default QGIS version",
            _default_qgis_status, "Show instructions…",
            lambda: self._open_default_qgis_dialog(dlg),
            "Make Windows open saved .qgz and .qgs projects in this version "
            "of QGIS. The instructions include the exact installation path "
            "and a button that opens the correct folder.")

        def _live_edit_status():
            from . import live_edit
            return live_edit.status_text()

        _add_row(
            "refresh", "Live Claude Edit",
            _live_edit_status, "Change…",
            lambda: self._open_live_edit_dialog(dlg),
            "Keeps the Biome layers re-reading the project GeoPackage on a "
            "timer, so edits made by something outside QGIS show up without a "
            "manual reload. Off by default — the timed redraw is a distraction "
            "while digitising.")

        for _category_layout in category_layouts.values():
            _category_layout.addStretch(1)

        _hline()
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 10, 0, 0)
        foot.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setDefault(True)
        close_btn.setStyleSheet(BTN_QSS)
        close_btn.clicked.connect(dlg.accept)
        foot.addWidget(close_btn)
        v.addLayout(foot)

        dlg.exec()

    def _open_default_qgis_dialog(self, parent=None):
        """Explain how to associate QGIS project files with this installation."""
        import os
        from qgis.PyQt.QtCore import QCoreApplication, QUrl
        from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication
        from qgis.PyQt.QtWidgets import (
            QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )

        executable = os.path.normpath(QCoreApplication.applicationFilePath())
        install_folder = os.path.dirname(executable)
        version_folder = os.path.basename(os.path.dirname(install_folder))

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Default QGIS version")
        dlg.setMinimumWidth(570)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        heading = QLabel(f"Open saved projects with {version_folder or 'this QGIS version'}")
        heading.setStyleSheet("font-size:15px;font-weight:600;color:#183B2A;")
        layout.addWidget(heading)

        instructions = QLabel(
            "Windows may not show a newly installed QGIS version in the app list. "
            "Use these steps once for each project-file type:\n\n"
            "1. Right-click any saved QGIS project and choose Open with, then "
            "Choose another app.\n"
            "2. Choose an app on your PC.\n"
            "3. Open the QGIS bin folder shown below and select qgis-bin.exe.\n"
            "4. Confirm Always use this app to open the file.\n\n"
            "Repeat for .qgz and .qgs files if your organisation uses both.")
        instructions.setWordWrap(True)
        instructions.setStyleSheet("font-size:12px;color:#344A3E;")
        layout.addWidget(instructions)

        path_label = QLabel(executable)
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setWordWrap(True)
        path_label.setStyleSheet(
            "font-family:Consolas,monospace;font-size:11px;color:#174C32;"
            "background:#E5F1E9;border:1px solid #B8CEBF;"
            "border-radius:6px;padding:7px;")
        layout.addWidget(path_label)

        buttons = QHBoxLayout()
        open_folder = QPushButton("Open QGIS bin folder")
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(install_folder)))
        buttons.addWidget(open_folder)
        copy_path = QPushButton("Copy path")
        copy_path.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(executable))
        buttons.addWidget(copy_path)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(dlg.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dlg.exec()

    def _open_redline_snapping_dialog(self, parent=None):
        """Preference controlling temporary snapping during redline capture."""
        from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                                         QLabel, QVBoxLayout)

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Boundary snapping")
        dlg.setMinimumWidth(430)
        v = QVBoxLayout(dlg)
        note = QLabel(
            "When enabled, the redline and blue-line drawing tools snap to the "
            "vertices and edges of existing baseline and proposed area-habitat "
            "polygons. Your normal QGIS snapping configuration is restored "
            "afterwards.")
        note.setWordWrap(True)
        v.addWidget(note)
        enabled = QCheckBox("Snap red and blue boundaries to existing habitat polygons")
        enabled.setChecked(get_redline_snap_to_habitats())
        v.addWidget(enabled)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        if dlg.exec():
            set_redline_snap_to_habitats(enabled.isChecked())

    def _open_phi_grouping_dialog(self, parent=None):
        """Edit the saved polygon-grouping choice used by PHI reports."""
        from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                                         QLabel, QVBoxLayout)

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Priority habitat reports")
        dlg.setMinimumWidth(450)
        v = QVBoxLayout(dlg)
        note = QLabel(
            "The Priority Habitats Inventory can split a continuous habitat "
            "across several mapped polygons. Grouping combines polygons of "
            "the same habitat type where their boundaries are no more than "
            "5 m apart, and reports their summed area as one habitat patch.")
        note.setWordWrap(True)
        v.addWidget(note)
        enabled = QCheckBox("Group same-habitat polygons within 5 m of each other")
        current = get_phi_group_nearby()
        enabled.setChecked(bool(current) if current is not None else False)
        v.addWidget(enabled)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        if dlg.exec():
            set_phi_group_nearby(enabled.isChecked())

    def _open_live_edit_dialog(self, parent=None):
        """On/off switch plus refresh interval for Live Claude Edit."""
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QHBoxLayout,
                                         QLabel, QPushButton, QSpinBox,
                                         QVBoxLayout)
        from . import live_edit

        BTN_QSS = (
            "QPushButton{background:transparent;color:#1F4A37;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 14px;}"
            "QPushButton:hover{background:#EAF2EC;}")

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Live Claude Edit")
        dlg.setMinimumWidth(430)
        dlg.setStyleSheet(
            f"QDialog {{ background:{theme.SURFACE}; }}"
            f" QLabel, QCheckBox, QPushButton, QSpinBox {{"
            f" font-family:{theme.FONT}; color:{theme.INK}; }}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        blurb = QLabel(
            "QGIS caches a layer's features for drawing, so when something "
            "outside QGIS writes to this project's GeoPackage the canvas keeps "
            "showing the old copy until it is told to re-read. Switch this on "
            "and Biome's own layers re-read themselves on a timer, so those "
            "edits appear as they land.\n\n"
            "Leave it off while you are digitising — the timed redraw gets in "
            "the way, and a layer with uncommitted edits is skipped anyway so "
            "your work is never discarded.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"font-size:12px; color:{theme.MUTED};")
        v.addWidget(blurb)

        chk = QCheckBox("Re-read the Biome layers automatically")
        chk.setChecked(live_edit.is_enabled())
        chk.setCursor(Qt.CursorShape.PointingHandCursor)
        chk.setStyleSheet("font-size:13px; font-weight:600;")
        v.addWidget(chk)

        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 0)
        row.setSpacing(8)
        every = QLabel("Every")
        sb = QSpinBox()
        sb.setRange(live_edit.MIN_SECS, live_edit.MAX_SECS)
        sb.setValue(live_edit.interval_seconds())
        sb.setSuffix(" seconds")
        sb.setFixedWidth(120)
        row.addWidget(every)
        row.addWidget(sb)
        row.addStretch(1)
        v.addLayout(row)

        note = QLabel(
            "Only the GeoPackage can be edited underneath a running QGIS. The "
            "project file itself is read once when you open it and rewritten "
            "whole when you save, so an outside edit to the QGIS project file is invisible "
            "at best and overwritten at worst.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size:11px; color:{theme.MUTED};")
        v.addWidget(note)

        applied = QLabel("")
        applied.setWordWrap(True)
        applied.setStyleSheet(f"font-size:11px; color:{theme.MUTED};")
        v.addWidget(applied)

        def _sync():
            sb.setEnabled(chk.isChecked())
            every.setEnabled(chk.isChecked())

        chk.toggled.connect(lambda _=None: _sync())
        _sync()

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 8, 0, 0)
        foot.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(BTN_QSS)
        cancel.clicked.connect(dlg.reject)
        ok = QPushButton("Apply")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setDefault(True)
        ok.setStyleSheet(BTN_QSS)

        def _apply():
            live_edit.set_enabled(chk.isChecked())
            live_edit.set_interval_seconds(sb.value())
            n = live_edit.apply()
            applied.setText(
                f"Applied to {n} layer{'s' if n != 1 else ''}."
                if chk.isChecked() else "Auto-refresh cleared.")
            dlg.accept()

        ok.clicked.connect(_apply)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)
        dlg.exec()

    def _open_theme_mode_dialog(self, parent=None):
        """Auto / Light / Dark chooser for the plugin theme. Applies on OK —
        in seconds, without touching the Windows setting."""
        from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QRadioButton,
                                         QButtonGroup, QLabel, QHBoxLayout,
                                         QPushButton)
        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Appearance")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(
            f"QDialog {{ background:{theme.SURFACE}; }}"
            f" QLabel, QRadioButton, QPushButton {{ font-family:{theme.FONT};"
            f" color:{theme.INK}; }}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)

        blurb = QLabel(
            "Light or dark look for the Biome panel — and, while Biome Mode is "
            "on, the whole QGIS window. Changing it here applies in seconds; "
            "flipping the Windows setting with QGIS open takes far longer.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"font-size:12px; color:{theme.MUTED};")
        v.addWidget(blurb)

        current = theme.theme_preference()
        group = QButtonGroup(dlg)
        choices = [
            ("auto", "Auto — follow the Windows setting"),
            ("light", "Always light"),
            ("dark", "Always dark"),
        ]
        radios = {}
        for key, label in choices:
            rb = QRadioButton(label)
            rb.setChecked(key == current)
            group.addButton(rb)
            radios[key] = rb
            v.addWidget(rb)

        note = QLabel("Auto follows Windows on QGIS 4; on QGIS 3 it stays "
                      "light. Always light / dark work on both.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size:11px; color:{theme.FAINT};")
        v.addWidget(note)

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("Cancel")
        ok = QPushButton("Apply")
        ok.setDefault(True)
        cancel.setStyleSheet(theme.btn_secondary())
        ok.setStyleSheet(theme.btn_primary())
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen = next((k for k, rb in radios.items() if rb.isChecked()),
                          "auto")
            if chosen != current:
                theme.set_theme_preference(chosen)

    def _open_search_radius_dialog(self, parent=None):
        """Edit the shared search area and the two named local-search presets."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSpinBox,
            QDialogButtonBox, QPushButton, QFrame, QToolButton, QWidget,
            QAbstractSpinBox,
        )
        from qgis.PyQt.QtCore import Qt
        from .config import (
            SHARED_RADIUS_KEYS, get_buffer_radius, set_buffer_radius,
            get_search_radius, set_search_radius, get_pond_inner_radius,
            set_pond_inner_radius,
        )

        BTN_QSS = (
            "QPushButton{background:transparent;color:#1F4A37;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 14px;}"
            "QPushButton:hover{background:#EAF2EC;}")
        STEP_QSS = (
            "QToolButton{background:#F2F6F2;color:#1F4A37;border:1px solid #C9D1C9;"
            "border-radius:6px;font-size:17px;font-weight:700;}"
            "QToolButton:hover{background:#E1EDE4;}"
            "QToolButton:pressed{background:#D2E4D7;}")
        STEP_M = 50   # how much each +/- press changes the radius

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Search distances")
        dlg.setMinimumWidth(560)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        intro = QLabel(
            "These distances are shared with the live buttons on the Desk Study "
            "screen. Changing a value updates the visible search area, the linked "
            "data searches, and future sessions.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(intro)

        def _make_stepper(sb):
            """[ − ][ spinbox ][ + ] — big clickable buttons flanking the box."""
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(5)
            minus = QToolButton(); minus.setText("−")
            plus  = QToolButton(); plus.setText("+")
            for b in (minus, plus):
                b.setFixedSize(28, 28)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet(STEP_QSS)
                b.setAutoRepeat(True)
                b.setAutoRepeatInterval(60)
            minus.clicked.connect(lambda: sb.setValue(sb.value() - STEP_M))
            plus.clicked.connect(lambda: sb.setValue(sb.value() + STEP_M))
            h.addWidget(minus)
            h.addWidget(sb)
            h.addWidget(plus)
            return w

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        rows = (
            ("shared", "Desk study search area",
             "Used by designated sites, priority habitats, ancient woodland, "
             "EPSL, conservation areas, GCN eDNA records, SSSI units, and NBN Atlas.",
             get_buffer_radius(), 2000),
            ("tpo", "TPO outer-only buffer  ★ favourite",
             "A single outer ring used by the TPO search.",
             get_search_radius("tpo"), 200),
            ("pond_inner", "Standing-water inner buffer  ★ favourite",
             "The inner ring in the standing-water search preset.",
             get_pond_inner_radius(), 250),
            ("standing_water", "Standing-water outer buffer  ★ favourite",
             "The outer ring and data-search distance for standing water bodies.",
             get_search_radius("standing_water"), 500),
        )
        spins = {}
        for r, (key, label, explanation, current, default) in enumerate(rows):
            label_box = QWidget()
            label_v = QVBoxLayout(label_box)
            label_v.setContentsMargins(0, 0, 0, 0)
            label_v.setSpacing(2)
            lbl = QLabel(label)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size:12px;font-weight:600;color:#1F4A37;")
            desc = QLabel(explanation)
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size:10px;color:#54635A;")
            label_v.addWidget(lbl)
            label_v.addWidget(desc)
            sb = QSpinBox()
            sb.setRange(50, 25000)
            sb.setSingleStep(STEP_M)
            sb.setSuffix(" m")
            sb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # our +/- replace them
            sb.setValue(current)
            sb.setToolTip(f"Recommended value {default} m")
            sb.setFixedWidth(92)
            grid.addWidget(label_box, r, 0)
            grid.addWidget(_make_stepper(sb), r, 1, Qt.AlignmentFlag.AlignRight)
            spins[key] = sb
        v.addLayout(grid)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#D9DED7;")
        v.addWidget(sep)

        foot = QHBoxLayout()
        reset = QPushButton("Reset to defaults")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setStyleSheet(BTN_QSS)

        def _do_reset():
            spins["shared"].setValue(2000)
            spins["tpo"].setValue(200)
            spins["pond_inner"].setValue(250)
            spins["standing_water"].setValue(500)
        reset.clicked.connect(_do_reset)
        foot.addWidget(reset)
        foot.addStretch(1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(BTN_QSS)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(BTN_QSS)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        foot.addWidget(bb)
        v.addLayout(foot)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            changed = []
            if spins["shared"].value() != get_buffer_radius():
                changed.extend(SHARED_RADIUS_KEYS)
                set_buffer_radius(spins["shared"].value())
            for key in ("tpo", "standing_water"):
                if spins[key].value() != get_search_radius(key):
                    changed.append(key)
                    set_search_radius(key, spins[key].value())
            if spins["pond_inner"].value() != get_pond_inner_radius():
                changed.append("standing_water")
                set_pond_inner_radius(spins["pond_inner"].value())
            if changed:
                self._apply_radius_changes(changed)
            try:
                self._update_search_area_button_labels()
                self._sync_auto_search_areas()
            except Exception:
                pass

    def _open_region_dialog(self, parent=None):
        """Site region override (region.py): Auto / England / Scotland. A
        change purges the region-specific desk study layers and caches so the
        next load fetches from the right country's services."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton,
            QDialogButtonBox, QButtonGroup,
        )
        from . import region

        BTN_QSS = (
            "QPushButton{background:transparent;color:#1F4A37;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 14px;}"
            "QPushButton:hover{background:#EAF2EC;}")

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Site region")
        dlg.setMinimumWidth(440)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        det = region.detected()
        det_txt = {"scotland": "Scotland", "england": "England"}.get(det, "unknown — draw a redline")
        intro = QLabel(
            "Choose which country's data services the desk study uses for "
            "designated sites and priority habitats. <b>Auto</b> reads the "
            f"redline (currently: <b>{det_txt}</b>); force a region only for a "
            "site on the border. Other desk study searches (TPOs, conservation "
            "areas, GCN zones, EPSL) are England-only whatever is chosen.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(intro)

        group = QButtonGroup(dlg)
        radios = {}
        for mode, label, hint in (
            ("auto",     "Auto (recommended)", "Detected from the redline centroid"),
            ("england",  "England",  "DEFRA / Natural England / MAGIC"),
            ("scotland", "Scotland", "NatureScot / Scottish Government / HabMoS"),
        ):
            rb = QRadioButton(label)
            rb.setStyleSheet("font-size:12px; color:#1F4A37;")
            rb.setToolTip(hint)
            group.addButton(rb)
            radios[mode] = rb
            row = QHBoxLayout()
            row.addWidget(rb)
            sub = QLabel(hint)
            sub.setStyleSheet("font-size:11px; color:#54635A;")
            row.addWidget(sub, 1)
            v.addLayout(row)
        radios.get(region.region_mode(), radios["auto"]).setChecked(True)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(BTN_QSS)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(BTN_QSS)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_mode = next((m for m, rb in radios.items() if rb.isChecked()), "auto")
        before = region.active()
        region.set_region_mode(new_mode)
        if region.active() != before:
            # The on-map DS / PHI / EPSL layers and their caches belong to the
            # other country's services — purge them like a radius change does.
            self._apply_radius_changes(
                ["designated_sites", "priority_habitats", "epsl"],
                reason="Site region changed")
        try:
            self._sync_region_ui()
        except Exception:
            pass

    def _apply_radius_changes(self, changed_keys, reason=None):
        """After desk study search radii change, drop the now-stale on-map layers
        and cached fetches for the changed searches so the next report, layer
        load or map refetches at the NEW distance.

        `reason` (optional) replaces "Search distances updated" in the message
        bar — the Site region switch reuses this purge.

        Without this, every loader re-shows its already-loaded layer (and
        designated sites / ancient woodland also serve cached features keyed only
        by redline), so a wider radius would appear to do nothing until the
        redline changed."""
        from qgis.core import QgsProject
        from .designated_sites import _SW_LAYER_NAME

        # Designated sites and ancient woodland (which follows priority_habitats)
        # share the redline-keyed WFS feature cache; clear it so their reports
        # refetch at the new radius.
        if {"designated_sites", "priority_habitats"} & set(changed_keys):
            try:
                clear_ds_cache()
            except Exception:
                pass

        proj = QgsProject.instance()
        root = proj.layerTreeRoot()

        def _remove_named(*names):
            for nm in names:
                for lyr in proj.mapLayersByName(nm):
                    proj.removeMapLayer(lyr.id())

        def _remove_group(group_name):
            grp = root.findGroup(group_name)
            if grp is None:
                return
            for node in list(grp.findLayers()):
                if node.layerId():
                    proj.removeMapLayer(node.layerId())
            parent = grp.parent()
            if parent is not None:
                parent.removeChildNode(grp)

        # Each changed search -> the data layers it currently owns on the map.
        # Removing them forces a fresh fetch (at the new radius) on next load.
        key_purge = {
            "designated_sites":   lambda: _remove_group("Designated Sites"),
            # The Priority Habitats group also contains the ancient woodland
            # layer, so removing the group covers both.
            "priority_habitats":  lambda: _remove_group("Priority Habitats"),
            "epsl":               lambda: _remove_named("EPSL Locations",
                                                        "EPSL Distance Lines"),
            "conservation_areas": lambda: _remove_named("Conservation Areas"),
            "tpo":                lambda: _remove_named("Tree Preservation Orders",
                                                        "TPO Zones",
                                                        "TPO Distance Lines"),
            "standing_water":     lambda: _remove_named(_SW_LAYER_NAME),
            "edna_ponds":         lambda: _remove_named("GCN eDNA Pond Surveys"),
            # sssi_units is report-only — no on-map layer to purge.
        }
        purged = False
        for key in changed_keys:
            fn = key_purge.get(key)
            if fn:
                try:
                    fn()
                    purged = True
                except Exception:
                    pass

        # Reset the desk study toggle buttons to match the (now-removed) layers.
        try:
            self._sync_desk_study_buttons_from_project()
        except Exception:
            pass
        try:
            self.iface.mapCanvas().refresh()
        except Exception:
            pass

        try:
            if reason:
                msg = (f"{reason} — reload the desk study layers you had on, "
                       "or re-run the reports, to fetch from the new sources."
                       if purged else f"{reason}.")
            elif purged:
                msg = ("Search distances updated — reload the desk study layers you "
                       "had on, or re-run the reports, to apply the new distances.")
            else:
                msg = "Search distances updated. New reports and maps use the new distances."
            self.iface.messageBar().pushMessage("Biome", msg, level=Qgis.MessageLevel.Info, duration=7)
        except Exception:
            pass

    def _open_date_format_dialog(self, parent=None):
        """Pick the house date style. One radio per format, each shown as
        today's date so the choice is concrete. Applies to printed final and
        desk study maps, the emergence survey date picker, the weather checker
        and the GCN HSI form; file-name stamps and the exported metric workbook
        keep their fixed formats."""
        from datetime import date as _date
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QRadioButton, QDialogButtonBox,
            QPushButton,
        )
        from .config import DATE_FORMATS, date_format_key, set_date_format_key

        BTN_QSS = (
            "QPushButton{background:transparent;color:#1F4A37;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 14px;}"
            "QPushButton:hover{background:#EAF2EC;}")

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Date format")
        dlg.setMinimumWidth(380)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        intro = QLabel(
            "How dates read on printed maps, in date pickers and on the survey "
            "forms. File names and the exported metric workbook keep their "
            "fixed formats.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(intro)

        current = date_format_key()
        radios = []
        for key, strf, _qt in DATE_FORMATS:
            rb = QRadioButton(_date.today().strftime(strf))
            rb.setStyleSheet("font-size:12px; color:#1F4A37; padding:2px;")
            rb.setChecked(key == current)
            v.addWidget(rb)
            radios.append((key, rb))

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(BTN_QSS)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(BTN_QSS)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            for key, rb in radios:
                if rb.isChecked():
                    set_date_format_key(key)
                    break

    def _open_map_titles_dialog(self, parent=None):
        """Permanently reword the shipped default titles: the Create Final Map
        defaults per map type, the desk study map titles, and the desk maps'
        printed legend headings. An empty box means the shipped default (shown
        as the placeholder); Save stores only real changes. These are the
        DEFAULTS — the per-map session edits (title field / right-click
        rename) still layer on top."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QScrollArea,
            QWidget, QFrame, QDialogButtonBox, QPushButton,
        )
        from .map_exporter import (
            AUTO_TITLES, DESK_TITLES, DESK_LEGEND_DEFAULTS,
            _TITLE_OVERRIDE_GROUP, _DESK_TITLE_GROUP, _DESK_LEGEND_GROUP,
            get_title_override, set_title_override,
        )

        BTN_QSS = (
            "QPushButton{background:transparent;color:#1F4A37;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 14px;}"
            "QPushButton:hover{background:#EAF2EC;}")

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Map titles & headings")
        dlg.setMinimumWidth(520)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        intro = QLabel(
            "Reword any default title for good — it will be pre-filled on every "
            "map from now on. Leave a box empty to keep the shipped default "
            "(shown greyed inside it). One-off edits still work as before: the "
            "title field in Create Final Map, and right-clicking a desk study "
            "map button, override these for the session.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(420)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(2, 2, 8, 2)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(inner)
        v.addWidget(scroll)

        edits = []   # (group, key, shipped default, QLineEdit)
        row = [0]

        def _section(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "font-size:12px; font-weight:600; color:#1F4A37; padding-top:8px;")
            grid.addWidget(lbl, row[0], 0, 1, 2)
            row[0] += 1

        def _row(group, key, label, shipped):
            shipped_flat = " ".join(shipped.split())
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size:11px; color:#54635A;")
            ed = QLineEdit(get_title_override(group, key))
            ed.setPlaceholderText(shipped_flat)
            ed.setStyleSheet("font-size:11px;")
            grid.addWidget(lbl, row[0], 0)
            grid.addWidget(ed, row[0], 1)
            row[0] += 1
            edits.append((group, key, shipped_flat, ed))

        _section("Final maps  ·  default title per map type")
        for mt in AUTO_TITLES:
            _row(_TITLE_OVERRIDE_GROUP, mt, mt, AUTO_TITLES[mt])

        _section("Desk study maps  ·  layout name and printed sheet title")
        for _attr, mt in self._DESK_MAP_BTNS:
            _row(_DESK_TITLE_GROUP, mt, mt.replace(" Map", ""), DESK_TITLES[mt])

        _section("Desk study maps  ·  legend heading")
        for mt, heading in DESK_LEGEND_DEFAULTS.items():
            _row(_DESK_LEGEND_GROUP, mt, mt.replace(" Map", ""), heading)

        foot = QVBoxLayout()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(BTN_QSS)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(BTN_QSS)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        foot.addWidget(bb)
        v.addLayout(foot)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for group, key, shipped_flat, ed in edits:
            text = ed.text().strip()
            # Typing the shipped default back is the same as reverting.
            set_title_override(group, key, "" if text == shipped_flat else text)
        # Desk buttons mirror their effective names — refresh them all.
        for _attr, mt in self._DESK_MAP_BTNS:
            self._refresh_map_btn_label(mt)

    def _open_banner_cycle_dialog(self, parent=None):
        """Visual control panel for the living banner. Everything is icon
        chips: day length, season, weather source (auto / random / match
        site) and the six sky states — click a state and the sky EASES
        over to it rather than snapping. Everything applies live while
        the dialog is open and persists via the banner's QgsSettings
        keys."""
        import math as _m
        from qgis.PyQt.QtCore import Qt, QSize, QPointF, QRectF
        from qgis.PyQt.QtGui import (QColor, QIcon, QPainter, QPainterPath,
                                     QPen, QPixmap)
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QButtonGroup, QFrame, QToolButton,
        )

        b = getattr(self, "_banner", None)
        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Living banner")
        dlg.setMinimumWidth(430)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(7)

        # ── tiny painted glyphs: no emoji, no files ───────────────────
        def glyph(kind, size=22):
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            s = size / 22.0
            ink = QColor("#1F4A37")
            sky = QColor("#2F5D8A")
            sun = QColor("#E8A13A")
            p.setPen(Qt.PenStyle.NoPen)
            if kind in ("clear", "summer"):
                p.setBrush(sun)
                p.drawEllipse(QPointF(11 * s, 11 * s), 4.2 * s, 4.2 * s)
                p.setPen(QPen(sun, 1.4 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                for k in range(8):
                    a = _m.pi * k / 4
                    p.drawLine(
                        QPointF(11 * s + 6.0 * s * _m.cos(a),
                                11 * s + 6.0 * s * _m.sin(a)),
                        QPointF(11 * s + 8.4 * s * _m.cos(a),
                                11 * s + 8.4 * s * _m.sin(a)))
            elif kind in ("cloud", "rain", "storm", "snow", "fog"):
                cl = QColor("#7A8C96")
                p.setBrush(cl)
                p.drawEllipse(QPointF(8 * s, 8.5 * s), 4.4 * s, 3.2 * s)
                p.drawEllipse(QPointF(13.5 * s, 7.5 * s), 5 * s, 3.7 * s)
                p.drawRect(QRectF(4.5 * s, 8.5 * s, 13.5 * s, 3.0 * s))
                if kind == "rain":
                    p.setPen(QPen(sky, 1.3 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    for rx in (7, 11, 15):
                        p.drawLine(QPointF(rx * s, 13.5 * s),
                                   QPointF((rx - 1.6) * s, 18.5 * s))
                elif kind == "storm":
                    bolt = QPainterPath(QPointF(11.5 * s, 12 * s))
                    bolt.lineTo(8.5 * s, 16 * s)
                    bolt.lineTo(10.8 * s, 16 * s)
                    bolt.lineTo(9.2 * s, 20 * s)
                    bolt.lineTo(13.8 * s, 14.8 * s)
                    bolt.lineTo(11.4 * s, 14.8 * s)
                    bolt.lineTo(13 * s, 12 * s)
                    bolt.closeSubpath()
                    p.setBrush(sun)
                    p.drawPath(bolt)
                elif kind == "snow":
                    p.setPen(QPen(QColor("#8FB6C8"), 1.2 * s,
                                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    for rx in (7.5, 11.5, 15.5):
                        cxp, cyp = rx * s, 16 * s
                        for a in (0, _m.pi / 3, 2 * _m.pi / 3):
                            p.drawLine(
                                QPointF(cxp - 1.7 * s * _m.cos(a),
                                        cyp - 1.7 * s * _m.sin(a)),
                                QPointF(cxp + 1.7 * s * _m.cos(a),
                                        cyp + 1.7 * s * _m.sin(a)))
                elif kind == "fog":
                    p.setPen(QPen(QColor("#9AA8A0"), 1.6 * s,
                                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    for fy in (14, 16.5, 19):
                        p.drawLine(QPointF(5 * s, fy * s),
                                   QPointF(17.5 * s, fy * s))
            elif kind == "off":
                p.setPen(QPen(ink, 1.6 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(11 * s, 11 * s), 6.5 * s, 6.5 * s)
                p.drawLine(QPointF(6.6 * s, 15.4 * s),
                           QPointF(15.4 * s, 6.6 * s))
            elif kind == "manual":
                p.setPen(QPen(ink, 1.5 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                for fy, fx in ((7, 13.5), (11, 8.5), (15, 15)):
                    p.drawLine(QPointF(5 * s, fy * s),
                               QPointF(17 * s, fy * s))
                    p.setBrush(ink)
                    p.drawEllipse(QPointF(fx * s, fy * s),
                                  1.9 * s, 1.9 * s)
            elif kind == "random":
                p.setPen(QPen(ink, 1.6 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                a1 = QPainterPath(QPointF(5 * s, 8.5 * s))
                a1.quadTo(11 * s, 4 * s, 16.4 * s, 7.6 * s)
                p.drawPath(a1)
                a2 = QPainterPath(QPointF(17 * s, 13.5 * s))
                a2.quadTo(11 * s, 18 * s, 5.6 * s, 14.4 * s)
                p.drawPath(a2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink)
                t1 = QPainterPath(QPointF(16.2 * s, 4.6 * s))
                t1.lineTo(18.6 * s, 8.6 * s)
                t1.lineTo(14.4 * s, 8.4 * s)
                t1.closeSubpath()
                p.drawPath(t1)
                t2 = QPainterPath(QPointF(5.8 * s, 17.4 * s))
                t2.lineTo(3.4 * s, 13.4 * s)
                t2.lineTo(7.6 * s, 13.6 * s)
                t2.closeSubpath()
                p.drawPath(t2)
            elif kind == "live":
                p.setBrush(sky)
                p.drawEllipse(QPointF(8 * s, 15 * s), 2.1 * s, 2.1 * s)
                p.setPen(QPen(sky, 1.5 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                for rr in (5.2, 8.4):
                    p.drawArc(QRectF((8 - rr) * s, (15 - rr) * s,
                                     rr * 2 * s, rr * 2 * s),
                              20 * 16, 70 * 16)
            elif kind == "auto":
                p.setPen(QPen(ink, 1.6 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(4.5 * s, 4.5 * s, 13 * s, 13 * s),
                          40 * 16, 270 * 16)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink)
                tri = QPainterPath(QPointF(15.6 * s, 2.8 * s))
                tri.lineTo(19.0 * s, 6.6 * s)
                tri.lineTo(14.0 * s, 7.4 * s)
                tri.closeSubpath()
                p.drawPath(tri)
            elif kind == "spring":
                p.setPen(QPen(ink, 1.5 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(11 * s, 19 * s), QPointF(11 * s, 7 * s))
                p.drawLine(QPointF(11 * s, 13 * s),
                           QPointF(7 * s, 9.5 * s))
                p.drawLine(QPointF(11 * s, 11 * s),
                           QPointF(15 * s, 8 * s))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#E2A9C4"))
                for bx, by in ((11, 5.4), (6.0, 8.0), (15.9, 6.4)):
                    p.drawEllipse(QPointF(bx * s, by * s),
                                  2.0 * s, 2.0 * s)
            elif kind == "autumn":
                p.setBrush(QColor("#C08A3E"))
                lf = QPainterPath(QPointF(11 * s, 3.5 * s))
                lf.quadTo(18.5 * s, 8 * s, 11 * s, 17.5 * s)
                lf.quadTo(3.5 * s, 8 * s, 11 * s, 3.5 * s)
                lf.closeSubpath()
                p.drawPath(lf)
                p.setPen(QPen(QColor("#7A5218"), 1.1 * s,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(11 * s, 6 * s),
                           QPointF(11 * s, 19.5 * s))
            elif kind == "winter":
                p.setPen(QPen(QColor("#5B7C8C"), 1.5 * s,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                for a in (0, _m.pi / 3, 2 * _m.pi / 3):
                    p.drawLine(QPointF(11 * s - 7 * s * _m.cos(a),
                                       11 * s - 7 * s * _m.sin(a)),
                               QPointF(11 * s + 7 * s * _m.cos(a),
                                       11 * s + 7 * s * _m.sin(a)))
            elif kind == "clock":
                p.setPen(QPen(ink, 1.6 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(11 * s, 11 * s), 7 * s, 7 * s)
                p.drawLine(QPointF(11 * s, 11 * s),
                           QPointF(11 * s, 6.4 * s))
                p.drawLine(QPointF(11 * s, 11 * s),
                           QPointF(14.4 * s, 12.6 * s))
            elif kind == "moon":
                p.setBrush(QColor("#5B7C8C"))
                p.drawEllipse(QPointF(11 * s, 11 * s), 6.5 * s, 6.5 * s)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                p.drawEllipse(QPointF(13.6 * s, 9.2 * s), 5.4 * s, 5.4 * s)
                p.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver)
            elif kind == "parallax":
                p.setBrush(Qt.BrushStyle.NoBrush)
                for yy, off, amp, w_pen in ((9.0, -1.5, 3.2, 1.2),
                                            (13.0, 0.5, 4.2, 1.4),
                                            (17.0, 2.5, 5.4, 1.6)):
                    p.setPen(QPen(ink, w_pen * s, Qt.PenStyle.SolidLine,
                                  Qt.PenCapStyle.RoundCap))
                    hill = QPainterPath(QPointF((3 + off) * s, yy * s))
                    hill.quadTo((11 + off) * s, (yy - amp) * s,
                                (19 + off) * s, yy * s)
                    p.drawPath(hill)
            elif kind == "scene":
                p.setPen(QPen(ink, 1.5 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(3.5 * s, 5.5 * s, 15 * s, 11 * s),
                                  2 * s, 2 * s)
                hill = QPainterPath(QPointF(4.5 * s, 14.5 * s))
                hill.quadTo(8.5 * s, 9.0 * s, 12.0 * s, 13.0 * s)
                hill.quadTo(14.5 * s, 15.5 * s, 17.5 * s, 12.0 * s)
                p.drawPath(hill)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(sun)
                p.drawEllipse(QPointF(14.8 * s, 8.6 * s),
                              1.5 * s, 1.5 * s)
            elif kind == "safari":
                p.setPen(QPen(ink, 1.5 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(7 * s, 13.8 * s), 3.5 * s, 3.5 * s)
                p.drawEllipse(QPointF(15 * s, 13.8 * s), 3.5 * s, 3.5 * s)
                p.drawLine(QPointF(10.4 * s, 12.6 * s),
                           QPointF(11.6 * s, 12.6 * s))
                p.drawLine(QPointF(5.9 * s, 10.4 * s),
                           QPointF(6.7 * s, 6.8 * s))
                p.drawLine(QPointF(16.1 * s, 10.4 * s),
                           QPointF(15.3 * s, 6.8 * s))
                p.drawLine(QPointF(6.7 * s, 6.8 * s),
                           QPointF(15.3 * s, 6.8 * s))
            p.end()
            return QIcon(pm)

        CHIP_QSS = (
            "QToolButton{border:1px solid #C9D1C9;border-radius:8px;"
            "padding:5px 3px;background:#FFFFFF;color:#1A2420;"
            "font-size:10px;}"
            "QToolButton:hover{background:#EAF2EC;}"
            "QToolButton:checked{background:#E3F0E7;"
            "border:2px solid #1F7A4D;padding:4px 2px;}")

        def chip(text, icon_kind, tip=""):
            btn = QToolButton()
            btn.setText(text)
            btn.setIcon(glyph(icon_kind))
            btn.setIconSize(QSize(22, 22))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumWidth(62)
            btn.setStyleSheet(CHIP_QSS)
            if tip:
                btn.setToolTip(tip)
            return btn

        def heading(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size:12px; font-weight:600; "
                              "color:#1F4A37; padding-top:2px;")
            return lbl

        def hline():
            fr = QFrame()
            fr.setFrameShape(QFrame.Shape.HLine)
            fr.setStyleSheet("color:#D9DED7;")
            return fr

        # ── day length: preset chips from real time down to demo pace ─
        v.addWidget(heading("Day length"))
        day_row = QHBoxLayout()
        day_row.setSpacing(6)
        day_group = QButtonGroup(dlg)
        day_group.setExclusive(True)
        cur = b.cycle_minutes() if b else 0
        for text, mins, tip in (
                ("Real time", 0, "Follows the real sun at the site"),
                ("2 h", 120, "A full day every 2 hours"),
                ("1 h", 60, "A full day every hour"),
                ("30 min", 30, "A full day every 30 minutes"),
                ("10 min", 10, "A full day every 10 minutes"),
                ("5 min", 5, "Demo — a full day every 5 minutes")):
            db = chip(text, "clear" if mins == 0 else "clock", tip)
            db.setProperty("mins", mins)
            db.setChecked(mins == cur)
            day_group.addButton(db)
            day_row.addWidget(db)
        day_row.addStretch(1)
        v.addLayout(day_row)

        def _day(btn):
            if b:
                b.set_cycle_minutes(btn.property("mins"))

        day_group.buttonClicked.connect(_day)

        v.addWidget(hline())

        # ── season: follow the calendar, or pin one ───────────────────
        v.addWidget(heading("Season"))
        sea_row = QHBoxLayout()
        sea_row.setSpacing(6)
        sea_group = QButtonGroup(dlg)
        sea_group.setExclusive(True)
        sea_cur = b.season_mode() if b else "auto"
        for text, sid in (("Auto", "auto"), ("Random", "random"),
                          ("Spring", "spring"), ("Summer", "summer"),
                          ("Autumn", "autumn"), ("Winter", "winter")):
            tip = ("Follow the real calendar" if sid == "auto"
                   else "A different season each day, for variety"
                   if sid == "random"
                   else "Pin the banner to %s" % text.lower())
            sb = chip(text, sid, tip)
            sb.setProperty("season", sid)
            sb.setChecked(sid == sea_cur)
            sea_group.addButton(sb)
            sea_row.addWidget(sb)
        sea_row.addStretch(1)
        v.addLayout(sea_row)

        def _season(btn):
            if b:
                b.set_season(btn.property("season"))

        sea_group.buttonClicked.connect(_season)

        v.addWidget(hline())

        # ── weather: a source chip (auto / random / match site) OR a
        # hand-picked sky — the two rows are exclusive across each other,
        # and every state chip is always one click away.
        v.addWidget(heading("Weather"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_group = QButtonGroup(dlg)
        mode_group.setExclusive(False)   # checked states managed by _sync
        mode_chips = {}
        for text, mid, tip in (
                ("Auto", "auto",
                 "Your current weather, wherever you are — refreshed hourly"),
                ("Random", "random",
                 "Passing fronts roll through on their own"),
                ("Match site", "live",
                 "The site's real weather at the redline, refreshed hourly")):
            mb = chip(text, mid, tip)
            mb.setProperty("wmode", mid)
            mode_group.addButton(mb)
            mode_row.addWidget(mb)
            mode_chips[mid] = mb
        mode_row.addStretch(1)
        v.addLayout(mode_row)

        state_row = QHBoxLayout()
        state_row.setSpacing(5)
        state_group = QButtonGroup(dlg)
        state_group.setExclusive(False)  # checked states managed by _sync
        state_chips = {}
        for text, sid in (("Clear", "clear"), ("Cloud", "cloud"),
                          ("Rain", "rain"), ("Storm", "storm"),
                          ("Snow", "snow"), ("Fog", "fog")):
            sb = chip(text, sid, "Ease the sky over to %s and hold it"
                      % text.lower())
            sb.setProperty("wstate", sid)
            state_group.addButton(sb)
            state_row.addWidget(sb)
            state_chips[sid] = sb
        v.addLayout(state_row)

        def _sync_weather_ui():
            mode = b.weather_mode() if b else "off"
            st = b.weather_state() if b else "clear"
            # Legacy "off" reads as a hand-pinned clear sky.
            pinned = mode in ("manual", "off")
            for mid, mb in mode_chips.items():
                mb.setChecked(not pinned and mid == mode)
            for sid, sb in state_chips.items():
                sb.setChecked(pinned and sid == st)

        def _mode(btn):
            if b:
                b.set_weather_mode(btn.property("wmode"))
            _sync_weather_ui()

        def _state(btn):
            if b:
                b.set_weather_state(btn.property("wstate"))
            _sync_weather_ui()

        mode_group.buttonClicked.connect(_mode)
        state_group.buttonClicked.connect(_state)
        _sync_weather_ui()

        v.addWidget(hline())

        # ── moon: tonight's real phase, or a nightly random one ───────
        v.addWidget(heading("Moon"))
        moon_row = QHBoxLayout()
        moon_row.setSpacing(6)
        moon_group = QButtonGroup(dlg)
        moon_group.setExclusive(True)
        moon_cur = b.moon_mode() if b else "real"
        for text, mmid, icon_kind, tip in (
                ("Real cycle", "real", "moon",
                 "Tonight's actual lunar phase — the same moon the bat "
                 "surveys are planned around"),
                ("Random", "random", "random",
                 "A different phase each night, for variety")):
            mb = chip(text, icon_kind, tip)
            mb.setProperty("mmode", mmid)
            mb.setChecked(mmid == moon_cur)
            moon_group.addButton(mb)
            moon_row.addWidget(mb)
        moon_row.addStretch(1)
        v.addLayout(moon_row)

        def _moon(btn):
            if b:
                b.set_moon_mode(btn.property("mmode"))

        moon_group.buttonClicked.connect(_moon)

        v.addWidget(hline())

        # ── scene: the living banner itself, pointer parallax and
        # safari mode — three toggles on one row.
        v.addWidget(heading("Scene"))
        scene_row = QHBoxLayout()
        scene_row.setSpacing(6)
        scene_chip = chip(
            "Living scene", "scene",
            "The animated countryside itself. Untick for a plain, quiet "
            "header — no hills, no wildlife, nothing moves.")
        scene_chip.setChecked(not b or b.scene_enabled())
        par_chip = chip(
            "Parallax", "parallax",
            "The scenery planes slide a little as the pointer pans "
            "across the banner, like looking round the landscape.")
        par_chip.setChecked(not b or b.parallax_enabled())
        safari_chip = chip(
            "Safari", "safari",
            "Safari mode — a constant parade of wildlife: several animals "
            "at once, any species at any hour, a newcomer every few "
            "seconds. Click again to turn it off.")
        safari_chip.setChecked(bool(b and b.party()))

        def _scene(checked):
            if b:
                b.set_scene_enabled(checked)
            par_chip.setEnabled(checked)

        def _parallax(checked):
            if b:
                b.set_parallax_enabled(checked)

        def _safari(checked):
            if b:
                b.set_party(checked)

        scene_chip.toggled.connect(_scene)
        par_chip.toggled.connect(_parallax)
        safari_chip.toggled.connect(_safari)
        par_chip.setEnabled(scene_chip.isChecked())
        scene_row.addWidget(scene_chip)
        scene_row.addWidget(par_chip)
        scene_row.addWidget(safari_chip)
        scene_row.addStretch(1)
        v.addLayout(scene_row)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 8, 0, 0)
        hint = QLabel("Changes apply live — watch the banner.")
        hint.setStyleSheet("font-size:10px; color:#7A877E;")
        foot.addWidget(hint)
        foot.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setDefault(True)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#1F4A37;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 14px;}"
            "QPushButton:hover{background:#EAF2EC;}")
        close_btn.clicked.connect(dlg.accept)
        foot.addWidget(close_btn)
        v.addLayout(foot)

        dlg.exec()

    def _open_basemap_buttons_dialog(self, parent=None):
        """Choose which basemap buttons are shown throughout Biome."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QCheckBox, QDialogButtonBox)

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Basemap buttons")
        dlg.setMinimumWidth(390)
        layout = QVBoxLayout(dlg)
        intro = QLabel(
            "Choose which basemaps appear in the Mapping and Desk Study bars. "
            "This only hides selector buttons; it does not delete any data.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#54635A; font-size:11px; margin-bottom:4px;")
        layout.addWidget(intro)

        choices = (
            ("Google Hybrid", "Hybrid"),
            ("Google Satellite", "Satellite"),
            ("__offline__", "Offline OS Zoomstack"),
            ("ESRI World Imagery", "ESRI World Imagery"),
            ("OS Outdoor", "OS Outdoor"),
            ("OS Premium", "OS Premium"),
        )
        enabled = get_enabled_basemap_buttons()
        boxes = {}
        for value, label in choices:
            box = QCheckBox(label)
            box.setChecked(value in enabled)
            box.setStyleSheet("padding:3px; color:#20372B;")
            boxes[value] = box
            layout.addWidget(box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def _save():
            set_enabled_basemap_buttons(
                {value for value, box in boxes.items() if box.isChecked()})
            apply_visibility = getattr(
                self, "_apply_basemap_button_visibility", None)
            if apply_visibility is not None:
                apply_visibility()
            dlg.accept()

        buttons.accepted.connect(_save)
        buttons.rejected.connect(dlg.reject)
        dlg.exec()

    def _open_os_ngd_key_dialog(self, parent=None):
        """Edit and validate the optional OS NGD Features project key."""
        import json
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton)

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("OS NGD premium key")
        dlg.setMinimumWidth(470)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(8)

        help_lbl = QLabel(
            "Optional. Enter the Project API Key for a Data Hub project with "
            "<b>OS NGD API – Features</b> and <b>OS NGD API – Tiles</b> enabled. "
            "The same key provides premium mapping and detailed standing-water "
            "polygons. The Project API Secret is not required.")
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(help_lbl)

        field = QLineEdit(get_os_ngd_api_key())
        field.setPlaceholderText("Paste the OS NGD Project API Key")
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setStyleSheet(
            "padding:6px; border:1px solid #C9D1C9; border-radius:6px;")
        reveal = field.addAction(_svg_icon("eye", "#8A968E", 16),
                                 QLineEdit.ActionPosition.TrailingPosition)

        def _toggle_reveal():
            hidden = field.echoMode() == QLineEdit.EchoMode.Password
            field.setEchoMode(QLineEdit.EchoMode.Normal if hidden
                              else QLineEdit.EchoMode.Password)
            reveal.setIcon(_svg_icon(
                "eye-off" if hidden else "eye", "#8A968E", 16))

        reveal.triggered.connect(_toggle_reveal)
        v.addWidget(field)
        status = QLabel("")
        status.setWordWrap(True)
        status.setStyleSheet("font-size:11px; color:#54635A;")
        v.addWidget(status)

        row = QHBoxLayout()
        row.addStretch(1)
        clear_btn = QPushButton("Clear")
        save_btn = QPushButton("Validate && Save")
        save_btn.setDefault(True)
        save_btn.setStyleSheet(
            "QPushButton{background:#1F4A37;color:white;border:none;"
            "border-radius:6px;padding:6px 14px;font-weight:600;}"
            "QPushButton:hover{background:#27654b;}")
        row.addWidget(clear_btn)
        row.addWidget(save_btn)
        v.addLayout(row)

        def _clear():
            set_os_ngd_api_key("")
            field.clear()
            from qgis.core import QgsProject
            for layer in list(QgsProject.instance().mapLayersByName("OS Premium")):
                QgsProject.instance().removeMapLayer(layer.id())
            try:
                self._sync_basemap_buttons()
            except Exception:
                pass
            try:
                self._refresh_pond_controls()
            except Exception:
                pass
            status.setText(
                "Key cleared. Standing-water searches will use the free sources.")

        def _save():
            key = field.text().strip()
            if not key:
                status.setStyleSheet("font-size:11px; color:#B23B3B;")
                status.setText("Enter a key, or select Clear.")
                return
            save_btn.setEnabled(False)
            save_btn.setText("Validating…")
            try:
                # The collection metadata call verifies entitlement without
                # consuming a feature-request transaction.
                url = ("https://api.os.uk/features/ngd/ofa/v1/collections/"
                       "wtr-fts-water-3")
                req = Request(url, headers={"key": key,
                                             "Accept": "application/json",
                                             "User-Agent": "BiomeMapper/1.0"})
                with urlopen(req, timeout=12) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict) or not data.get("id"):
                    raise ValueError("The service returned an unexpected response.")
            except HTTPError as exc:
                status.setStyleSheet("font-size:11px; color:#B23B3B;")
                status.setText(
                    f"Key not accepted (HTTP {exc.code}). Check that OS NGD "
                    "API – Features is enabled for this project.")
                return
            except Exception as exc:
                status.setStyleSheet("font-size:11px; color:#B23B3B;")
                status.setText(f"Could not validate the key: {exc}")
                return
            finally:
                save_btn.setEnabled(True)
                save_btn.setText("Validate && Save")
            set_os_ngd_api_key(key)
            from qgis.core import QgsProject
            if QgsProject.instance().mapLayersByName("OS Premium"):
                from . import base_layers as bl
                from .config import get_os_ngd_basemap_style
                bl.ensure_os_ngd_premium(key, get_os_ngd_basemap_style())
                self._switch_basemap("OS Premium")
            try:
                self._refresh_pond_controls()
            except Exception:
                pass
            dlg.accept()

        clear_btn.clicked.connect(_clear)
        save_btn.clicked.connect(_save)
        dlg.exec()

    def _open_os_key_dialog(self, parent=None):
        """Focused editor for the OS Data Hub API key. The key is masked (eye
        action to reveal), validated against the OS Maps API on save
        (re-prompting in place if rejected) and stored in QgsSettings (QGIS
        profile) so it survives updates. Clear confirms first, because it also
        drops the OS Outdoor layer from the project."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
            QMessageBox,
        )
        from qgis.core import QgsProject

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Ordnance Survey API key")
        dlg.setMinimumWidth(430)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(8)

        help_lbl = QLabel(
            "Used for the OS Outdoor basemap. Get a free key at "
            "<a href='https://osdatahub.os.uk'>osdatahub.os.uk</a> → create an API "
            "project → add the <b>OS Maps API</b> → copy the Project API Key.<br>"
            "Stored in your QGIS profile, so it survives plugin updates.")
        help_lbl.setWordWrap(True)
        help_lbl.setOpenExternalLinks(True)
        help_lbl.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(help_lbl)

        field = QLineEdit(get_os_api_key())
        field.setPlaceholderText("Paste your OS Data Hub API key")
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setStyleSheet("padding:6px; border:1px solid #C9D1C9; border-radius:6px;")
        reveal = field.addAction(_svg_icon("eye", "#8A968E", 16),
                                 QLineEdit.ActionPosition.TrailingPosition)
        reveal.setToolTip("Show key")

        def _toggle_reveal():
            hidden = field.echoMode() == QLineEdit.EchoMode.Password
            field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
            reveal.setIcon(_svg_icon("eye-off" if hidden else "eye", "#8A968E", 16))
            reveal.setToolTip("Hide key" if hidden else "Show key")

        reveal.triggered.connect(_toggle_reveal)
        v.addWidget(field)

        status = QLabel("")
        status.setWordWrap(True)
        status.setStyleSheet("font-size:11px; color:#54635A;")
        v.addWidget(status)

        row = QHBoxLayout()
        row.addStretch(1)
        clear_btn = QPushButton("Clear")
        save_btn  = QPushButton("Validate && Save")
        save_btn.setDefault(True)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton{background:#1F4A37;color:white;border:none;border-radius:6px;"
            "padding:6px 14px;font-weight:600;}"
            "QPushButton:hover{background:#27654b;}")
        clear_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#54635A;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#EAF2EC;}")
        row.addWidget(clear_btn)
        row.addWidget(save_btn)
        v.addLayout(row)

        def _set_status(text, kind="muted"):
            colors = {"muted": "#54635A", "error": "#B23B3B",
                      "warn": "#9A6700", "ok": "#1F7A4D"}
            status.setStyleSheet(f"font-size:11px; color:{colors.get(kind, '#54635A')};")
            status.setText(text)

        def _do_clear():
            if get_os_api_key():
                res = QMessageBox.question(
                    dlg, "Remove key",
                    "Remove the saved key?\n\nOS Outdoor will be removed from "
                    "the project and Biome will ask for a key next time.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if res != QMessageBox.StandardButton.Yes:
                    return
            proj = QgsProject.instance()
            root = proj.layerTreeRoot()
            os_was_visible = any(
                (root.findLayer(l.id()) is not None and root.findLayer(l.id()).isVisible())
                for l in proj.mapLayersByName("OS Outdoor"))
            set_os_api_key("")
            field.setText("")
            for l in list(proj.mapLayersByName("OS Outdoor")):
                proj.removeMapLayer(l.id())
            if os_was_visible:
                self._switch_basemap("Google Hybrid")
            self._sync_basemap_buttons()
            _set_status("Key cleared — OS Outdoor will prompt again next time.", "muted")

        def _do_save():
            key = field.text().strip()
            if not key:
                _set_status("Enter a key, or use Clear to remove it.", "error")
                return
            save_btn.setEnabled(False); save_btn.setText("Validating…")
            from qgis.PyQt.QtWidgets import QApplication
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ok, msg = self._validate_os_key(key)
            finally:
                QApplication.restoreOverrideCursor()
                save_btn.setEnabled(True); save_btn.setText("Validate && Save")
            if ok is False:
                _set_status("✗ " + msg, "error")
                field.selectAll(); field.setFocus()
                return   # keep dialog open → re-prompt in place
            set_os_api_key(key)
            if ok is None:
                _set_status("⚠ " + msg + " Saved anyway.", "warn")
            from . import base_layers as bl
            if QgsProject.instance().mapLayersByName("OS Outdoor"):
                bl.ensure_os_outdoor(key)
            self._sync_basemap_buttons()
            dlg.accept()

        clear_btn.clicked.connect(_do_clear)
        save_btn.clicked.connect(_do_save)
        dlg.exec()

    def _validate_google_key(self, key):
        """Check a Google Maps Platform key against the Photorealistic 3D
        Tiles root endpoint (uses one of the 1,000 free monthly root
        requests). Returns (status, message) like _validate_os_key."""
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest
        from qgis.core import QgsBlockingNetworkRequest
        url = f"https://tile.googleapis.com/v1/3dtiles/root.json?key={key}"
        try:
            blocking = QgsBlockingNetworkRequest()
            blocking.get(QNetworkRequest(QUrl(url)))
            reply = blocking.reply()
            status = (reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                      if reply is not None else None)
        except Exception as exc:
            return None, f"Could not run validation ({exc})."
        if status == 200:
            return True, "Key valid — 3D tiles are ready."
        if status in (400, 401, 403):
            return False, (f"Key rejected by Google (HTTP {status}). Check the key, "
                           "that the Map Tiles API is enabled on its Cloud project "
                           "(console.cloud.google.com → APIs & Services), and that "
                           "billing is active on the project.")
        if status is None:
            return None, "Couldn't reach Google to validate (offline/proxy?)."
        return False, f"Unexpected response from Google (HTTP {status})."

    def _open_spatialhub_key_dialog(self, parent=None):
        """Editor for the Spatial Hub Scotland GeoServer authkey (Scottish
        TPOs). Masked field; Validate & Save checks the key against the
        sh_tpo WFS GetCapabilities (a bad key answers 403); stored in
        QgsSettings like the other keys."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
            QMessageBox, QApplication,
        )
        from .config import get_spatialhub_authkey, set_spatialhub_authkey

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Spatial Hub Scotland key")
        dlg.setMinimumWidth(440)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(8)

        help_lbl = QLabel(
            "Used for Tree Preservation Orders on a Scottish site — the "
            "Improvement Service's national TPO dataset (Open Government "
            "Licence, but its web service needs a registered user's key). "
            "Create a free account at <a href='https://data.spatialhub.scot/'>"
            "data.spatialhub.scot</a>, then copy the <b>Geoserver Authkey</b> "
            "from your user profile page.<br>Stored in your QGIS profile, so "
            "it survives plugin updates.")
        help_lbl.setWordWrap(True)
        help_lbl.setOpenExternalLinks(True)
        help_lbl.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(help_lbl)

        field = QLineEdit(get_spatialhub_authkey())
        field.setPlaceholderText("Paste your Spatial Hub Geoserver authkey")
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setStyleSheet("padding:6px; border:1px solid #C9D1C9; border-radius:6px;")
        reveal = field.addAction(_svg_icon("eye", "#8A968E", 16),
                                 QLineEdit.ActionPosition.TrailingPosition)
        reveal.setToolTip("Show key")

        def _toggle_reveal():
            hidden = field.echoMode() == QLineEdit.EchoMode.Password
            field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
            reveal.setIcon(_svg_icon("eye-off" if hidden else "eye", "#8A968E", 16))
            reveal.setToolTip("Hide key" if hidden else "Show key")

        reveal.triggered.connect(_toggle_reveal)
        v.addWidget(field)

        status = QLabel("")
        status.setWordWrap(True)
        status.setStyleSheet("font-size:11px; color:#54635A;")
        v.addWidget(status)

        row = QHBoxLayout()
        row.addStretch(1)
        clear_btn = QPushButton("Clear")
        save_btn = QPushButton("Validate && Save")
        save_btn.setDefault(True)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton{background:#1F4A37;color:white;border:none;border-radius:6px;"
            "padding:6px 14px;font-weight:600;}"
            "QPushButton:hover{background:#27654b;}")
        clear_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#54635A;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#EAF2EC;}")
        row.addWidget(clear_btn)
        row.addWidget(save_btn)
        v.addLayout(row)

        def _set_status(text, kind="muted"):
            colors = {"muted": "#54635A", "error": "#B23B3B",
                      "warn": "#9A6700", "ok": "#1F7A4D"}
            status.setStyleSheet(f"font-size:11px; color:{colors.get(kind, '#54635A')};")
            status.setText(text)

        def _do_clear():
            if get_spatialhub_authkey():
                res = QMessageBox.question(
                    dlg, "Remove key", "Remove the saved key?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if res != QMessageBox.StandardButton.Yes:
                    return
            set_spatialhub_authkey("")
            field.setText("")
            _set_status("Key cleared.", "muted")

        def _do_save():
            key = field.text().strip()
            if not key:
                _set_status("Enter a key, or use Clear to remove it.", "error")
                return
            save_btn.setEnabled(False); save_btn.setText("Validating…")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                from .designated_sites import _sh_discover_tpo_types, _SH_TYPE_CACHE
                _SH_TYPE_CACHE.pop(key, None)
                found = _sh_discover_tpo_types(key)
                ok, msg = True, ("Key accepted · TPO layers: "
                                 + ", ".join(v for v in found.values() if v))
            except Exception as exc:
                ok, msg = False, str(exc)
            finally:
                QApplication.restoreOverrideCursor()
                save_btn.setEnabled(True); save_btn.setText("Validate && Save")
            if ok:
                set_spatialhub_authkey(key)
                _set_status(msg, "ok")
                dlg.accept()
            else:
                _set_status(msg, "error")

        clear_btn.clicked.connect(_do_clear)
        save_btn.clicked.connect(_do_save)
        dlg.exec()

    def _open_google_key_dialog(self, parent=None):
        """Focused editor for the Google Maps Platform API key used by the
        3D Spotlight export. Masked field, validated against the 3D Tiles
        endpoint on save, stored in QgsSettings like the OS key."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
            QMessageBox,
        )

        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Google Maps API key")
        dlg.setMinimumWidth(430)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(8)

        help_lbl = QLabel(
            "Used for the 3D Spotlight export (Google's photorealistic 3D "
            "tiles). In <a href='https://console.cloud.google.com/apis/library/"
            "tile.googleapis.com'>Google Cloud Console</a>: enable the "
            "<b>Map Tiles API</b> on your project, then use any API key from "
            "that project (APIs &amp; Services → Credentials).<br>"
            "3D tiles include 1,000 free viewer-opens a month — plenty. "
            "Stored in your QGIS profile, so it survives plugin updates.")
        help_lbl.setWordWrap(True)
        help_lbl.setOpenExternalLinks(True)
        help_lbl.setStyleSheet("color:#54635A; font-size:11px;")
        v.addWidget(help_lbl)

        field = QLineEdit(get_google_maps_api_key())
        field.setPlaceholderText("Paste your Google Maps Platform API key")
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setStyleSheet("padding:6px; border:1px solid #C9D1C9; border-radius:6px;")
        reveal = field.addAction(_svg_icon("eye", "#8A968E", 16),
                                 QLineEdit.ActionPosition.TrailingPosition)
        reveal.setToolTip("Show key")

        def _toggle_reveal():
            hidden = field.echoMode() == QLineEdit.EchoMode.Password
            field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
            reveal.setIcon(_svg_icon("eye-off" if hidden else "eye", "#8A968E", 16))
            reveal.setToolTip("Hide key" if hidden else "Show key")

        reveal.triggered.connect(_toggle_reveal)
        v.addWidget(field)

        status = QLabel("")
        status.setWordWrap(True)
        status.setStyleSheet("font-size:11px; color:#54635A;")
        v.addWidget(status)

        row = QHBoxLayout()
        row.addStretch(1)
        clear_btn = QPushButton("Clear")
        save_btn = QPushButton("Validate && Save")
        save_btn.setDefault(True)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton{background:#1F4A37;color:white;border:none;border-radius:6px;"
            "padding:6px 14px;font-weight:600;}"
            "QPushButton:hover{background:#27654b;}")
        clear_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#54635A;border:1px solid #C9D1C9;"
            "border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#EAF2EC;}")
        row.addWidget(clear_btn)
        row.addWidget(save_btn)
        v.addLayout(row)

        def _set_status(text, kind="muted"):
            colors = {"muted": "#54635A", "error": "#B23B3B",
                      "warn": "#9A6700", "ok": "#1F7A4D"}
            status.setStyleSheet(f"font-size:11px; color:{colors.get(kind, '#54635A')};")
            status.setText(text)

        def _do_clear():
            if get_google_maps_api_key():
                res = QMessageBox.question(
                    dlg, "Remove key",
                    "Remove the saved key?\n\nThe 3D Spotlight export will ask "
                    "for a key next time.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if res != QMessageBox.StandardButton.Yes:
                    return
            set_google_maps_api_key("")
            field.setText("")
            _set_status("Key cleared.", "muted")

        def _do_save():
            key = field.text().strip()
            if not key:
                _set_status("Enter a key, or use Clear to remove it.", "error")
                return
            save_btn.setEnabled(False); save_btn.setText("Validating…")
            from qgis.PyQt.QtWidgets import QApplication
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ok, msg = self._validate_google_key(key)
            finally:
                QApplication.restoreOverrideCursor()
                save_btn.setEnabled(True); save_btn.setText("Validate && Save")
            if ok is False:
                _set_status("✗ " + msg, "error")
                field.selectAll(); field.setFocus()
                return
            set_google_maps_api_key(key)
            if ok is None:
                _set_status("⚠ " + msg + " Saved anyway.", "warn")
            dlg.accept()

        clear_btn.clicked.connect(_do_clear)
        save_btn.clicked.connect(_do_save)
        dlg.exec()
