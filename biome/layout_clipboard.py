# layout_clipboard.py
"""Clipboard integration for QGIS layout designer windows.

QGIS's Layout ▸ Export as Image can only write to a file. This module gives
every layout designer window two things:

1. A "Copy to Clipboard" action (toolbar + Layout menu, Ctrl+Shift+C) that
   renders the layout straight to the clipboard at its export resolution —
   no save dialog, ready to paste.
2. An automatic copy on every normal Export as Image: after QGIS finishes
   saving the file, the SAVED image (so the user's chosen dpi/crop options
   are honoured exactly) is loaded back and placed on the clipboard too.
   Completion is detected via the sticky Success message QGIS pushes to the
   designer's message bar — its text carries a file:/// link to the exported
   path, and nothing is pushed on cancel, so a cancelled dialog never fires.

Installed once from BNGPlugin.initGui via install(iface); it hooks
iface.layoutDesignerOpened so designers opened later get both behaviours.
Already-hooked detection is via the module-level _entries list (NOT a window
property: module state resets on plugin reload, so a designer window that
survives a reload gets re-hooked by the fresh module — a dynamic property
would survive the reload and permanently block it).
"""

import datetime
import os
import re
import time

from qgis.PyQt import sip
from qgis.PyQt.QtCore import (QBuffer, QByteArray, QIODevice, QSize, Qt,
                              QTimer, QUrl)
from qgis.PyQt.QtGui import QImage, QKeySequence
from qgis.PyQt.QtWidgets import QAction, QApplication

_iface = None
_installed = False
_entries = []          # [(window, copy QAction, native QAction|None, slot)]
_auto_copied = set()   # (path, mtime) pairs already sent to the clipboard


# ── TEMP DEBUG (remove once live-verified) ───────────────────────────────
_DBG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "biome_debug.log")


def _dbg(msg):                                            # TEMP DEBUG
    try:
        with open(_DBG_PATH, "a", encoding="utf-8") as f:
            f.write("%s  clip: %s\n" % (
                datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], msg))
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────────────────


def install(iface):
    """Hook layoutDesignerOpened and retrofit any designers already open."""
    global _iface, _installed
    _iface = iface
    _dbg("install()")                                     # TEMP DEBUG
    if not _installed:
        try:
            iface.layoutDesignerOpened.connect(_on_designer_opened)
            _installed = True
            _dbg("layoutDesignerOpened connected")        # TEMP DEBUG
        except Exception as e:
            _dbg("layoutDesignerOpened connect FAILED: %r" % e)  # TEMP DEBUG
    try:
        designers = list(iface.openLayoutDesigners())
        _dbg("retrofitting %d open designer(s)" % len(designers))  # TEMP DEBUG
        for designer in designers:
            _on_designer_opened(designer)
    except Exception as e:
        _dbg("retrofit FAILED: %r" % e)                   # TEMP DEBUG


def uninstall(iface):
    """Disconnect the hook and strip the action from live designer windows."""
    global _installed
    if _installed:
        try:
            iface.layoutDesignerOpened.disconnect(_on_designer_opened)
        except Exception:
            pass
        _installed = False
    for win, act, native, slot in _entries:
        try:
            if act is not None and not sip.isdeleted(act):
                act.setParent(None)
                act.deleteLater()
        except Exception:
            pass
        try:
            if native is not None and not sip.isdeleted(native):
                native.triggered.disconnect(slot)
        except Exception:
            pass
    _entries.clear()


def _already_hooked(win):
    """True if this designer window already has a live entry from THIS module
    instance (module state resets on reload, so reloads re-hook cleanly)."""
    try:
        wid = sip.unwrapinstance(win)
    except Exception:
        return False
    for w, act, _n, _s in _entries:
        try:
            if (not sip.isdeleted(w) and sip.unwrapinstance(w) == wid
                    and act is not None and not sip.isdeleted(act)):
                return True
        except Exception:
            continue
    return False


def _on_designer_opened(designer):
    try:
        win = designer.view().window()
    except Exception as e:
        _dbg("designer window lookup FAILED: %r" % e)     # TEMP DEBUG
        return
    if win is None:
        _dbg("designer window is None")                   # TEMP DEBUG
        return
    if _already_hooked(win):
        _dbg("window already hooked — skip")              # TEMP DEBUG
        return
    _dbg("hooking designer window %r" % win.windowTitle())  # TEMP DEBUG

    try:
        from . import theme
        ico = theme.icon("copy", size=18)
    except Exception:
        from qgis.PyQt.QtGui import QIcon
        ico = QIcon()

    act = QAction(ico, "Copy to Clipboard", win)
    act.setToolTip("Copy this layout to the clipboard as an image "
                   "(at the layout's export resolution) — Ctrl+Shift+C")
    act.setShortcut(QKeySequence("Ctrl+Shift+C"))
    act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
    # Bind the designer interface, not the layout: for reports the current
    # layout changes, and designer.layout() always returns the live one.
    act.triggered.connect(lambda _=False, d=designer: _copy_layout(d))

    added = False
    try:
        tb = designer.actionsToolbar()
        if tb is not None:
            tb.addSeparator()
            tb.addAction(act)
            added = True
    except Exception as e:
        _dbg("actionsToolbar FAILED: %r" % e)             # TEMP DEBUG
    try:
        menu = designer.layoutMenu()
        if menu is not None:
            menu.addSeparator()
            menu.addAction(act)
            added = True
    except Exception as e:
        _dbg("layoutMenu FAILED: %r" % e)                 # TEMP DEBUG
    # Auto-copy after the native Export as Image. QGIS's own slot was
    # connected first, so ours runs after the (synchronous) export finishes;
    # the 0-timer additionally lets the message bar settle before we read it.
    native = None
    slot = None
    try:
        native = win.findChild(QAction, "mActionExportAsImage")
        if native is not None:
            slot = (lambda _=False, d=designer: QTimer.singleShot(
                0, lambda: _after_native_export(d)))
            native.triggered.connect(slot)
    except Exception as e:
        _dbg("native export hook FAILED: %r" % e)         # TEMP DEBUG
        native = None
        slot = None
    _dbg("hooked: toolbar/menu added=%s, native export action found=%s"
         % (added, native is not None))                   # TEMP DEBUG

    if added or native is not None:
        _entries.append((win, act, native, slot))


def _copy_layout(designer):
    from qgis.core import Qgis, QgsLayoutExporter
    _dbg("_copy_layout()")                                # TEMP DEBUG
    try:
        layout = designer.layout()
    except Exception:
        layout = None
    if layout is None:
        _dbg("no layout on designer")                     # TEMP DEBUG
        return
    try:
        exporter = QgsLayoutExporter(layout)
        # dpi=-1 → the layout's own export resolution, matching what the
        # Export as Image dialog would offer by default.
        image = exporter.renderPageToImage(0, QSize(), -1.0)
    except Exception as e:
        _dbg("render FAILED: %r" % e)                     # TEMP DEBUG
        _push(designer, "Copy failed: %s" % e, Qgis.MessageLevel.Critical)
        return
    if image.isNull():
        _dbg("render returned null image")                # TEMP DEBUG
        _push(designer, "Copy failed — the layout rendered an empty image.",
              Qgis.MessageLevel.Critical)
        return

    _set_clipboard(image)

    note = ""
    try:
        pages = layout.pageCollection().pageCount()
        if pages > 1:
            note = " (page 1 of %d)" % pages
    except Exception:
        pass
    _push(designer,
          "Layout copied to clipboard — %d × %d px%s. Paste it anywhere."
          % (image.width(), image.height(), note),
          Qgis.MessageLevel.Success)


def _set_clipboard(image):
    """Place a QImage on the clipboard as both plain image data (DIB — Word,
    Outlook, paint apps) and an explicit image/png entry (browser targets
    like Google Docs)."""
    from qgis.PyQt.QtCore import QMimeData
    data = QMimeData()
    data.setImageData(image)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    data.setData("image/png", ba)
    try:                                                  # TEMP DEBUG
        QApplication.clipboard().setMimeData(data)
        _dbg("clipboard set: %d x %d px, png %d bytes"    # TEMP DEBUG
             % (image.width(), image.height(), ba.size()))
    except Exception as e:                                # TEMP DEBUG
        _dbg("clipboard set FAILED: %r" % e)              # TEMP DEBUG
        raise


def _after_native_export(designer):
    """Runs just after QGIS's Export as Image slot returns. If an export
    actually completed, load the saved file and copy it to the clipboard."""
    from qgis.core import Qgis
    _dbg("_after_native_export()")                        # TEMP DEBUG
    path = _exported_image_path(designer)
    if not path:
        # Message bar unreadable (or message format changed): fall back to
        # the export directory QGIS records in the project, and take the
        # image file written there in the last few minutes, if any.
        fresh = _fresh_export_from_dir()
        if fresh is None:
            _dbg("no exported path found — treated as cancel")  # TEMP DEBUG
            return                  # cancelled dialog / failed export
        path = fresh
        _dbg("dir-scan fallback found: %s" % path)        # TEMP DEBUG
    try:
        mtime = os.path.getmtime(path)
    except OSError as e:
        _dbg("mtime FAILED for %s: %r" % (path, e))       # TEMP DEBUG
        return
    # The success message is sticky (duration 0), so it can still be showing
    # from an earlier export when a later one is cancelled: only act on a
    # file written moments ago, and never on the same (path, mtime) twice.
    age = time.time() - mtime
    if age > 180:
        _dbg("stale export (%.0fs old) — skip: %s" % (age, path))  # TEMP DEBUG
        return
    key = (path, mtime)
    if key in _auto_copied:
        _dbg("already copied this export — skip")         # TEMP DEBUG
        return
    _auto_copied.add(key)

    image = QImage(path)
    if image.isNull():
        _dbg("QImage load failed for %s — falling back to render" % path)  # TEMP DEBUG
        # Unreadable format (shouldn't happen for png/jpg/tif/bmp) — fall
        # back to a fresh render so the clipboard still gets the layout.
        _copy_layout(designer)
        return
    _dbg("auto-copying exported file: %s" % path)         # TEMP DEBUG
    _set_clipboard(image)
    _push(designer,
          "Exported image also copied to clipboard — %d × %d px. "
          "Paste it anywhere." % (image.width(), image.height()),
          Qgis.MessageLevel.Success)


def _find_message_bar(win):
    """Locate the designer window's QgsMessageBar. A plain type-matched
    findChild(QgsMessageBar) returns None on the real (C++-created) bar in
    QGIS 4.2 even though it IS a direct child of centralWidget() — so fall
    back to matching the Qt meta-object class name over all child widgets
    and sip.cast the hit to the proper wrapper type."""
    from qgis.gui import QgsMessageBar
    try:
        bar = win.findChild(QgsMessageBar)
        if bar is not None:
            return bar
    except Exception:
        pass
    try:
        from qgis.PyQt.QtWidgets import QWidget
        for w in win.findChildren(QWidget):
            try:
                if w.metaObject().className() == "QgsMessageBar":
                    _dbg("bar found via className scan")  # TEMP DEBUG
                    return sip.cast(w, QgsMessageBar)
            except Exception:
                continue
    except Exception as e:
        _dbg("className scan FAILED: %r" % e)             # TEMP DEBUG
    return None


_IMG_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def _fresh_export_from_dir():
    """Fallback export detection: QGIS persists the last export DIRECTORY
    (project entry Layouts/lastLayoutExportDir, mirrored to an App-section
    setting) — return the newest image file written there within the last
    few minutes, or None. The caller's (path, mtime) dedup keeps a cancelled
    re-export from re-copying the same file."""
    dirs = []
    try:
        from qgis.core import QgsProject
        d, ok = QgsProject.instance().readEntry(
            "Layouts", "/lastLayoutExportDir", "")
        if ok and d:
            dirs.append(d)
    except Exception:
        pass
    try:
        from qgis.core import QgsSettings
        d = QgsSettings().value("lastLayoutExportDir", "",
                                section=QgsSettings.Section.App)
        if d:
            dirs.append(str(d))
    except Exception:
        pass
    _dbg("dir-scan candidates: %r" % dirs)                # TEMP DEBUG
    best = None
    now = time.time()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if os.path.splitext(n)[1].lower() not in _IMG_EXTS:
                continue
            p = os.path.join(d, n)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if now - mt > 180:
                continue
            if best is None or mt > best[1]:
                best = (p, mt)
    return None if best is None else best[0]


def _exported_image_path(designer):
    """Extract the exported file path from the designer message bar's
    'Successfully exported layout to <a href=file:///...>' Success message.
    Returns None if no such message is present (cancel/failure)."""
    from qgis.core import Qgis
    try:
        bar = _find_message_bar(designer.view().window())
    except Exception as e:
        _dbg("message bar lookup FAILED: %r" % e)         # TEMP DEBUG
        return None
    if bar is None:
        _dbg("no QgsMessageBar in designer window")       # TEMP DEBUG
        return None
    try:
        items = list(bar.items())
    except Exception as e:
        _dbg("bar.items() unavailable (%r) — using currentItem" % e)  # TEMP DEBUG
        item = bar.currentItem()
        items = [] if item is None else [item]
    _dbg("message bar has %d item(s)" % len(items))       # TEMP DEBUG
    for item in items:
        try:
            text = item.text()
            _dbg("  item level=%s text=%r" % (item.level(), text[:160]))  # TEMP DEBUG
            if item.level() != Qgis.MessageLevel.Success:
                continue
            m = re.search(r'href="(file:[^"]+)"', text)
            if m is None:
                continue
            local = QUrl(m.group(1)).toLocalFile()
            if local and os.path.isfile(local):
                return local
            _dbg("  href parsed but not a file: %r" % local)  # TEMP DEBUG
        except Exception as e:
            _dbg("  item inspect FAILED: %r" % e)         # TEMP DEBUG
            continue
    return None


def _push(designer, text, level):
    """Prefer the designer window's own message bar; fall back to the main
    QGIS bar so feedback is never silently lost."""
    try:
        bar = _find_message_bar(designer.view().window())
        if bar is not None:
            bar.pushMessage("Biome", text, level=level, duration=5)
            return
    except Exception:
        pass
    try:
        if _iface is not None:
            _iface.messageBar().pushMessage("Biome", text, level=level,
                                            duration=5)
    except Exception:
        pass
