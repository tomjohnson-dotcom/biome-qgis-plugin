# updater.py
"""One-click updater for Biome's private QGIS plugin repository."""

from configparser import ConfigParser
import io
import os
from pathlib import Path
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from qgis.PyQt.QtCore import QObject, QSettings, QTimer, QUrl
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import QPushButton
from qgis.core import Qgis, QgsMessageLog


CATALOGUE_URL = (
    "https://raw.githubusercontent.com/tomjohnson-dotcom/"
    "biome-qgis-plugin/main/plugins.xml"
)
_CHECK_SETTING = "BiomeMapper/updateCheckEnabled"

try:  # PyQt 6 scoped enum / PyQt 5 unscoped enum
    _NO_NETWORK_ERROR = QNetworkReply.NetworkError.NoError
except AttributeError:
    _NO_NETWORK_ERROR = QNetworkReply.NoError


def _version_parts(value):
    """Comparable numeric tuple for the repository's dotted versions."""
    parts = []
    for piece in str(value or "").strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts or [0])


def _metadata_version(plugin_dir):
    parser = ConfigParser(interpolation=None)
    parser.read(Path(plugin_dir) / "metadata.txt", encoding="utf-8")
    return parser.get("general", "version", fallback="0").strip()


class BiomeUpdater(QObject):
    """Check, offer, download, validate, and install a Biome release."""

    def __init__(self, iface, plugin_dir):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.plugin_dir = Path(plugin_dir).resolve()
        self.network = QNetworkAccessManager(self)
        self._reply = None
        self._notice = None
        self._button = None
        self._remote = None
        self._busy = False

    def schedule_check(self, delay_ms=5000):
        enabled = QSettings().value(_CHECK_SETTING, True)
        if str(enabled).lower() not in ("false", "0", "no"):
            QTimer.singleShot(delay_ms, self.check)

    def check(self):
        if self._busy or self._reply is not None:
            return
        request = QNetworkRequest(QUrl(CATALOGUE_URL))
        self._reply = self.network.get(request)
        self._reply.finished.connect(self._catalogue_finished)

    def _catalogue_finished(self):
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        try:
            if reply.error() != _NO_NETWORK_ERROR:
                self._log("Update check failed: " + reply.errorString())
                return
            root = ET.fromstring(bytes(reply.readAll()))
            plugin = next(
                (node for node in root.findall("pyqgis_plugin")
                 if (node.get("name") or "").strip().lower() == "biome"),
                None,
            )
            if plugin is None:
                return
            version = (
                plugin.get("version")
                or plugin.findtext("version")
                or ""
            ).strip()
            download_url = (plugin.findtext("download_url") or "").strip()
            if (not version or not download_url
                    or _version_parts(version)
                    <= _version_parts(_metadata_version(self.plugin_dir))):
                return
            self._remote = {"version": version, "url": download_url}
            self._show_update_notice(version)
        except Exception as exc:
            self._log("Update catalogue error: %s" % exc)
        finally:
            reply.deleteLater()

    def _show_update_notice(self, version):
        bar = self.iface.messageBar()
        notice = bar.createMessage(
            "Biome update available",
            "Version %s is ready." % version,
        )
        button = QPushButton("Update Biome")
        button.clicked.connect(self.install_update)
        notice.layout().addWidget(button)
        bar.pushWidget(notice, Qgis.MessageLevel.Info, 0)
        self._notice = notice
        self._button = button

    def install_update(self):
        if self._busy or not self._remote:
            return
        self._busy = True
        if self._button is not None:
            self._button.setEnabled(False)
            self._button.setText("Downloading…")
        request = QNetworkRequest(QUrl(self._remote["url"]))
        self._reply = self.network.get(request)
        self._reply.finished.connect(self._package_finished)

    def _package_finished(self):
        reply = self._reply
        self._reply = None
        try:
            if reply is None:
                raise RuntimeError("The update download did not start.")
            if reply.error() != _NO_NETWORK_ERROR:
                raise RuntimeError(reply.errorString())
            payload = bytes(reply.readAll())
            self._install_payload(payload, self._remote["version"])
        except Exception as exc:
            self._busy = False
            if self._button is not None:
                self._button.setEnabled(True)
                self._button.setText("Try again")
            self.iface.messageBar().pushMessage(
                "Biome update failed",
                str(exc),
                level=Qgis.MessageLevel.Critical,
                duration=10,
            )
            self._log("Update install failed: %s" % exc)
        finally:
            if reply is not None:
                reply.deleteLater()

    def _install_payload(self, payload, expected_version):
        if len(payload) < 1024:
            raise RuntimeError("The downloaded update was incomplete.")

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if not names or any(
                name.startswith(("/", "\\"))
                or ".." in Path(name.replace("\\", "/")).parts
                for name in names
            ):
                raise RuntimeError("The update package has an invalid structure.")
            metadata_name = "biome/metadata.txt"
            if metadata_name not in names:
                raise RuntimeError("The update package does not contain Biome.")
            parser = ConfigParser(interpolation=None)
            parser.read_string(
                archive.read(metadata_name).decode("utf-8-sig")
            )
            package_version = parser.get(
                "general", "version", fallback=""
            ).strip()
            if package_version != expected_version:
                raise RuntimeError(
                    "The package version does not match the update catalogue."
                )

            temp_root = Path(tempfile.mkdtemp(prefix="biome-update-"))
            try:
                archive.extractall(temp_root)
                staged = temp_root / "biome"
                self._copy_update(staged)
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)

        self._busy = False
        if self._notice is not None:
            try:
                self.iface.messageBar().popWidget(self._notice)
            except Exception:
                pass
        self.iface.messageBar().pushMessage(
            "Biome updated",
            "Biome %s has been installed and is reloading now."
            % expected_version,
            level=Qgis.MessageLevel.Success,
            duration=5,
        )
        # Let this network callback return before unloading the module that
        # owns it. QGIS can safely reload a Python plugin in place, so the
        # project and the rest of the application remain open.
        def _reload_biome():
            try:
                import qgis.utils
                qgis.utils.reloadPlugin("biome")
            except Exception as exc:
                self.iface.messageBar().pushMessage(
                    "Biome",
                    "The update is installed but Biome could not reload: %s"
                    % exc,
                    level=Qgis.MessageLevel.Warning,
                    duration=10,
                )
        QTimer.singleShot(250, _reload_biome)

    def _copy_update(self, staged):
        """Copy only after full validation, restoring overwritten files on error."""
        if not staged.is_dir():
            raise RuntimeError("The staged Biome folder is missing.")

        backup_root = Path(tempfile.mkdtemp(prefix="biome-backup-"))
        backup = backup_root / "biome"
        try:
            shutil.copytree(
                self.plugin_dir,
                backup,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log"),
            )
            try:
                for source in staged.rglob("*"):
                    relative = source.relative_to(staged)
                    target = self.plugin_dir / relative
                    if source.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temp_target = target.with_name(target.name + ".biome-new")
                        shutil.copy2(source, temp_target)
                        os.replace(temp_target, target)
                shutil.rmtree(
                    self.plugin_dir / "__pycache__", ignore_errors=True
                )
            except Exception:
                for source in backup.rglob("*"):
                    relative = source.relative_to(backup)
                    target = self.plugin_dir / relative
                    if source.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                raise
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)

    def stop(self):
        reply = self._reply
        self._reply = None
        if reply is not None:
            try:
                reply.abort()
                reply.deleteLater()
            except Exception:
                pass

    @staticmethod
    def _log(message):
        QgsMessageLog.logMessage(
            message, "Biome", Qgis.MessageLevel.Warning
        )

