# width_debug.py — temporary instrumentation to diagnose dock width problems.
#
# Logs every resize / float / dock-state change of the Biome panel, and on any
# widening dumps the "critical path": the chain of child widgets whose minimum
# widths are forcing the dock wide. Output goes to:
#   1. The QGIS Log Messages panel  (View ▸ Panels ▸ Log Messages ▸ "Biome Width Debug")
#   2. A file:  <home>/biome_width_debug.log
#
# Remove the install() call in main_panel.py (or delete this file) once the
# problem is found.

import os
import datetime
import traceback

try:
    from qgis.PyQt.QtCore import QObject, QEvent, QTimer, Qt
    from qgis.PyQt.QtWidgets import QWidget, QSizePolicy
except ImportError:  # pragma: no cover
    from qgis.PyQt.QtCore import QObject, QEvent, QTimer, Qt
    from qgis.PyQt.QtWidgets import QWidget, QSizePolicy

_LOG_PATH = os.path.join(os.path.expanduser("~"), "biome_width_debug.log")

_POLICY_NAMES = {
    QSizePolicy.Policy.Fixed: "Fixed", QSizePolicy.Policy.Minimum: "Minimum",
    QSizePolicy.Policy.Maximum: "Maximum", QSizePolicy.Policy.Preferred: "Preferred",
    QSizePolicy.Policy.Expanding: "Expanding",
    QSizePolicy.Policy.MinimumExpanding: "MinimumExpanding",
    QSizePolicy.Policy.Ignored: "Ignored",
}


def _log(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    try:
        from qgis.core import QgsMessageLog, Qgis
        QgsMessageLog.logMessage(line, "Biome Width Debug", Qgis.MessageLevel.Info)
    except Exception:
        pass
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _describe(w):
    cls = type(w).__name__
    name = w.objectName() or ""
    text = ""
    for attr in ("text", "windowTitle"):
        try:
            t = getattr(w, attr)()
            if t:
                text = t.replace("\n", "\\n")[:48]
                break
        except Exception:
            pass
    bits = [cls]
    if name:
        bits.append(f"#{name}")
    if text:
        bits.append(f"'{text}'")
    return " ".join(bits)


def _eff_min_w(w):
    """Effective minimum width a layout will respect for this widget."""
    try:
        pol = w.sizePolicy().horizontalPolicy()
        hint = 0 if pol == QSizePolicy.Policy.Ignored else w.minimumSizeHint().width()
        return max(w.minimumWidth(), hint)
    except Exception:
        return -1


def _widget_line(w, indent):
    try:
        pol = _POLICY_NAMES.get(w.sizePolicy().horizontalPolicy(), "?")
        return (f"{'  ' * indent}{_describe(w)}  "
                f"effMin={_eff_min_w(w)} explicitMin={w.minimumWidth()} "
                f"minHint={w.minimumSizeHint().width()} hint={w.sizeHint().width()} "
                f"max={w.maximumWidth() if w.maximumWidth() < 16777215 else '∞'} "
                f"actual={w.width()} pol={pol} "
                f"vis={'y' if w.isVisible() else 'n'}")
    except Exception as e:  # widget half-destroyed etc.
        return f"{'  ' * indent}<error describing widget: {e}>"


def _dump_critical_path(dock, reason):
    """Walk down from the dock, at each level showing the widest children and
    descending into the widest one — the chain that sets the dock's minimum."""
    lines = [f"── WIDTH REPORT ({reason}) " + "─" * 30]
    try:
        lines.append(
            f"dock: w={dock.width()} minW={dock.minimumWidth()} "
            f"minHint={dock.minimumSizeHint().width()} hint={dock.sizeHint().width()} "
            f"floating={dock.isFloating()}"
        )
    except Exception as e:
        lines.append(f"dock: <error {e}>")

    current = dock.widget()
    depth = 0
    while current is not None and depth < 25:
        lines.append(_widget_line(current, depth + 1))
        try:
            kids = [c for c in current.children() if isinstance(c, QWidget)]
        except Exception:
            break
        if not kids:
            break
        kids.sort(key=_eff_min_w, reverse=True)
        # show the top 4 widest children at this level for context
        for k in kids[:4]:
            lines.append(_widget_line(k, depth + 2) + "   <child>")
        widest = kids[0]
        if _eff_min_w(widest) <= 0:
            break
        current = widest
        depth += 1

    lines.append("─" * 56)
    _log("\n".join(lines))


class _WidthSpy(QObject):
    def __init__(self, dock):
        super().__init__(dock)
        self._dock = dock
        self._last_w = dock.width()
        dock.installEventFilter(self)
        try:
            dock.topLevelChanged.connect(
                lambda fl: self._note(f"topLevelChanged → floating={fl}", dump=True))
            dock.dockLocationChanged.connect(
                lambda area: self._note(
                    f"dockLocationChanged → area={getattr(area, 'value', area)}"))
            dock.visibilityChanged.connect(
                lambda v: self._note(f"visibilityChanged → visible={v}"))
        except Exception:
            pass
        # tab switches are a prime widening suspect
        try:
            dock._tab_stack.currentChanged.connect(
                lambda i: self._note(f"tab switched → index={i}", dump=True))
        except Exception:
            pass
        # snapshots shortly after install to catch the "immediately after open" jump
        for delay in (0, 500, 1500, 3000):
            QTimer.singleShot(delay, lambda d=delay: _dump_critical_path(
                self._dock, f"snapshot t+{d}ms"))

    def _note(self, msg, dump=False):
        _log(f"{msg}  (dock w={self._dock.width()})")
        if dump:
            _dump_critical_path(self._dock, msg)

    def eventFilter(self, obj, ev):
        if obj is self._dock and ev.type() == QEvent.Type.Resize:
            new_w = ev.size().width()
            old_w = ev.oldSize().width()
            if new_w != old_w:
                _log(f"RESIZE {old_w} → {new_w}")
                if new_w > old_w + 15:
                    # widened: capture who did it (python frames, if any) + why
                    stack = "".join(traceback.format_stack(limit=14)[:-1]).strip()
                    _log("python stack at widening:\n" +
                         (stack if stack else "<no python frames — Qt internal>"))
                    _dump_critical_path(self._dock, f"widened {old_w}→{new_w}")
            self._last_w = new_w
        return False


def install(dock):
    """Attach the spy to the dock. Safe to call once from __init__."""
    try:
        open(_LOG_PATH, "a", encoding="utf-8").write(
            f"\n===== Biome width debug session {datetime.datetime.now()} =====\n")
    except Exception:
        pass
    _log(f"width debug installed — log file: {_LOG_PATH}")
    dock._width_spy = _WidthSpy(dock)
