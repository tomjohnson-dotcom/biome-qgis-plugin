# -*- coding: utf-8 -*-
"""
condition_export.py
===================

Render a *filled* condition sheet to PDF from the live dialog state, for
dropping straight into a report.  Dependency-free: it builds rich-text HTML and
prints it with Qt's own QTextDocument -> QPrinter (the same toolkit QGIS already
uses), so it works on any install without Excel / LibreOffice.

Content mirrors the stripped export template: habitat name -> verbatim criteria
with the pass/fail (or indicator score) filled in -> the grade.  Criterion
wording is the statutory verbatim text (``condition_verbatim.py``), overlaid by
letter onto the authoritative ``condition_data`` criteria so pass/fail can never
drift from what was assessed.
"""

import html as _html

from qgis.PyQt.QtGui import QTextDocument

from . import condition_data as CD

try:
    from .condition_verbatim import VERBATIM
except Exception:
    VERBATIM = {}

_PASS = "#175E3B"
_FAIL = "#8F2A23"
_MUTE = "#8A968D"
_INK = "#1F4A37"
_LEVEL = ("Good", "Moderate", "Poor")


def _esc(s):
    return _html.escape(str(s or ""))


def _tick_state(ctrls):
    """{letter: 'Pass'|'Fail'|'N/A'} from the live checkboxes."""
    out = {}
    for letter, cb in ctrls.get("checks", []):
        if not cb.isEnabled():
            out[letter] = "N/A"
        else:
            out[letter] = "Pass" if cb.isChecked() else "Fail"
    return out


def _score_state(ctrls):
    """{letter: selected index 0/1/2 or None} from the live score buttons."""
    out = {}
    for letter, btns in ctrls.get("scores", []):
        out[letter] = next((i for i, b in enumerate(btns) if b.isChecked()), None)
    return out


def _lake_state(ctrls):
    """{attribute letter: selected HNA class 1..5 or None}."""
    out = {}
    for letter, btns in ctrls.get("lake_classes", []):
        selected = next((i for i, b in enumerate(btns) if b.isChecked()), None)
        out[letter] = selected + 1 if selected is not None else None
    return out


def _row(cells):
    return "<tr>" + "".join(cells) + "</tr>"


def _td(text, *, align="left", color=None, bold=False, bg=None, width=None):
    style = "padding:5px 8px; border:1px solid #C9D3CC; font-size:12px; vertical-align:top;"
    style += " text-align:%s;" % align
    if color:
        style += " color:%s;" % color
    if bold:
        style += " font-weight:bold;"
    if bg:
        style += " background-color:%s;" % bg
    w = (' width="%s"' % width) if width else ""
    return '<td%s style="%s">%s</td>' % (w, style, text)


def build_html(sub, panel, ctrls):
    key = panel.get("key")
    vb = VERBATIM.get(key, {})
    grade = ctrls.get("grade")
    parts = [
        '<div style="font-family:Segoe UI, Arial, sans-serif;">',
        '<div style="font-size:10px; letter-spacing:2px; color:%s;">CONDITION ASSESSMENT</div>' % _MUTE,
        '<div style="font-size:18px; font-weight:bold; color:%s; padding-bottom:4px;">%s</div>' % (_INK, _esc(sub)),
    ]

    # Trees this assessment covers (tree sheets only): number + DBH.
    _trees = []
    for (no_e, dbh_e) in ctrls.get("tree_rows", []):
        _no, _dbh = no_e.text().strip(), dbh_e.text().strip()
        if _no or _dbh:
            _trees.append((_no, _dbh))
    if _trees:
        _th = _row([_td("Tree no. / ref", bold=True, bg="#EAF2EC", width="55%"),
                    _td("DBH (cm)", bold=True, bg="#EAF2EC")])
        _tb = "".join(_row([_td(_esc(n) or "—"), _td(_esc(d) or "—")]) for (n, d) in _trees)
        parts.append('<div style="font-size:11px; color:%s; margin:2px 0;">Trees assessed</div>' % _MUTE)
        parts.append('<table cellspacing="0" width="55%%">%s%s</table>' % (_th, _tb))
        parts.append('<div style="height:8px;"></div>')

    if panel["model"] == "note":
        parts.append('<p style="font-size:12px; color:#2A332D;">%s</p>' % _esc(panel.get("note", "")))
    elif panel["model"] == "lake":
        selected = _lake_state(ctrls)
        head = _row([_td("HNA attribute", bold=True, bg="#EAF2EC"),
                     _td("Assessment scope", bold=True, bg="#EAF2EC"),
                     _td("Class", bold=True, bg="#EAF2EC", align="center", width="60")])
        body = []
        for letter, name, desc in panel["attributes"]:
            value = selected.get(letter)
            body.append(_row([_td("<b>%s.</b> %s" % (letter, _esc(name))),
                              _td(_esc(desc)),
                              _td(str(value) if value else "—", align="center", bold=True)]))
        parts.append('<table cellspacing="0" width="100%%">%s%s</table>' % (head, "".join(body)))
        vals = [v for v in selected.values() if v is not None]
        if vals:
            parts.append(
                '<p style="font-size:12px;"><b>Average HNA class:</b> %.2f '
                '(%d of 4 attributes assessed)</p>' %
                (sum(vals) / float(len(vals)), len(vals)))
        parts.append('<p style="font-size:11px; color:%s;">%s</p>' %
                     (_MUTE, _esc(panel.get("note", ""))))
    elif panel["model"] == "score":
        sel = _score_state(ctrls)
        vinds = vb.get("indicators", {})
        head = _row([_td("Indicator", bold=True, bg="#EAF2EC", width="40%"),
                     _td("Assessed condition", bold=True, bg="#EAF2EC"),
                     _td("Score", bold=True, bg="#EAF2EC", align="center", width="60")])
        body = []
        total = 0
        for (letter, name, descs) in panel["indicators"]:
            vname, vdescs = vinds.get(letter, [name, list(descs)])
            i = sel.get(letter)
            if i is None:
                assessed, score_txt = "—", "—"
            else:
                assessed = "<b>%s.</b> %s" % (_LEVEL[i], _esc(vdescs[i]))
                score = 3 - i
                total += score
                score_txt = str(score)
            body.append(_row([
                _td("<b>%s.</b> %s" % (letter, _esc(vname))),
                _td(assessed),
                _td(score_txt, align="center")]))
        parts.append('<table cellspacing="0" width="100%%">%s%s</table>' % (head, "".join(body)))
        n = len(panel["indicators"])
        if any(v is not None for v in sel.values()):
            parts.append('<p style="font-size:12px;"><b>Total score:</b> %d of %d</p>' % (total, n * 3))
        else:
            parts.append('<p style="font-size:11px; color:%s;">Per-indicator scores were not '
                         'recorded for this feature; the recorded condition is shown below.</p>' % _MUTE)
    else:
        state = _tick_state(ctrls)
        vcrit = vb.get("criteria", {})
        head = _row([_td("", bold=True, bg="#EAF2EC", align="center", width="34"),
                     _td("Criterion", bold=True, bg="#EAF2EC"),
                     _td("Result", bold=True, bg="#EAF2EC", align="center", width="80")])
        body = []
        npass = ntot = 0
        for (letter, concise, essential) in panel["criteria"]:
            res = state.get(letter)
            if res is None:
                continue                                   # not part of this variant
            text = vcrit.get(letter, concise)
            if essential:
                text += ' <i style="color:%s;">(essential)</i>' % _MUTE
            if res == "Pass":
                npass += 1; ntot += 1; col = _PASS
            elif res == "Fail":
                ntot += 1; col = _FAIL
            else:
                col = _MUTE
            body.append(_row([
                _td("<b>%s</b>" % letter, align="center"),
                _td(text),
                _td(res, align="center", color=col, bold=True)]))
        parts.append('<table cellspacing="0" width="100%%">%s%s</table>' % (head, "".join(body)))
        parts.append('<p style="font-size:12px;"><b>%d of %d</b> criteria passed.</p>' % (npass, ntot))

    if grade:
        bg = {"Good": "#E9F4EC", "Fairly Good": "#EEF6E9", "Moderate": "#FBF4E4",
              "Fairly Poor": "#FCF0E8", "Poor": "#FBEDEB"}.get(grade, "#EEF1EE")
        fg = {"Good": _PASS, "Fairly Good": "#467A3D", "Moderate": "#9A6700",
              "Fairly Poor": "#A44E27", "Poor": _FAIL}.get(grade, _INK)
        parts.append('<div style="margin-top:6px; padding:8px 10px; background-color:%s;'
                     ' color:%s; font-size:14px; font-weight:bold;">Condition: %s</div>'
                     % (bg, fg, _esc(grade)))

    labels = panel.get("labels")
    if labels:
        rows = "".join(_row([_td("<b>%s</b>" % g, width="90"), _td(_esc(t))]) for g, t in labels)
        parts.append('<p style="font-size:11px; color:%s; margin-top:8px;">Grade thresholds</p>' % _MUTE)
        parts.append('<table cellspacing="0" width="100%%">%s</table>' % rows)

    parts.append("</div>")
    return "".join(parts)


def condition_plain_text(ctrls):
    """Full per-criterion record as plain text, for 'Copy full' (paste into a
    report). Verbatim wording + each result, then the overall grade."""
    sub = ctrls.get("sub", "")
    panel = CD.panel_for(sub)
    if not panel:
        return ctrls.get("report", "")
    key = panel.get("key")
    vb = VERBATIM.get(key, {})
    lines = ["Condition Assessment - %s" % sub, ""]
    if panel["model"] == "note":
        lines.append(panel.get("note", ""))
    elif panel["model"] == "lake":
        selected = _lake_state(ctrls)
        for letter, name, _desc in panel["attributes"]:
            value = selected.get(letter)
            lines.append("%s. %s: %s" %
                         (letter, name, "Class %d" % value if value else "not assessed"))
        vals = [v for v in selected.values() if v is not None]
        if vals:
            lines.append("Average HNA class: %.2f" % (sum(vals) / float(len(vals))))
    elif panel["model"] == "score":
        sel = _score_state(ctrls)
        vinds = vb.get("indicators", {})
        for (letter, name, descs) in panel["indicators"]:
            vname, vdescs = vinds.get(letter, [name, list(descs)])
            i = sel.get(letter)
            if i is None:
                lines.append("%s. %s: not scored" % (letter, vname))
            else:
                lines.append("%s. %s: %s (%d) - %s" % (letter, vname, _LEVEL[i], 3 - i, vdescs[i]))
    else:
        state = _tick_state(ctrls)
        vcrit = vb.get("criteria", {})
        for (letter, concise, essential) in panel["criteria"]:
            res = state.get(letter)
            if res is None:
                continue
            text = vcrit.get(letter, concise)
            ess = " (essential)" if essential else ""
            lines.append("%s. %s%s -- %s" % (letter, text, ess, res))
    grade = ctrls.get("grade")
    if grade:
        lines += ["", "Overall condition: %s" % grade]
    return "\n".join(lines)


def _render_png(html_text, path, width=820, margin=16, scale=2):
    """Render the sheet HTML to a single content-sized PNG (no page, just an
    image you can drop straight into a report). Laid out at `width` px, with a
    white `margin`, rendered at `scale`x so the text stays crisp when zoomed."""
    from qgis.PyQt.QtGui import QImage, QPainter
    from qgis.PyQt.QtCore import Qt
    doc = QTextDocument()
    doc.setHtml(html_text)
    doc.setTextWidth(width)                       # tables at width:100% fill this
    size = doc.size()
    w = max(1, int(round(size.width())))
    h = max(1, int(round(size.height())))
    img = QImage((w + 2 * margin) * scale, (h + 2 * margin) * scale, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.white)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.scale(scale, scale)
        p.translate(margin, margin)
        doc.drawContents(p)
    finally:
        p.end()
    img.save(path, "PNG")


def _safe_name(sub):
    keep = "".join(c if (c.isalnum() or c in " -_") else "_" for c in str(sub or "habitat"))
    return keep.strip() or "habitat"


def export_condition_png(parent, ctrls, status_label=None):
    """Save the current assessment as a PNG condition sheet (assess mode only)."""
    from qgis.PyQt.QtWidgets import QFileDialog
    from qgis.PyQt.QtGui import QDesktopServices
    from qgis.PyQt.QtCore import QUrl

    sub = ctrls.get("sub", "")
    panel = CD.panel_for(sub)
    if not panel:
        if status_label is not None:
            status_label.setText("No condition sheet for this habitat.")
        return

    suggested = _safe_name(sub) + " - Condition Assessment.png"
    path, _flt = QFileDialog.getSaveFileName(
        parent, "Export condition sheet (PNG)", suggested, "PNG image (*.png)")
    if not path:
        return
    if not path.lower().endswith(".png"):
        path += ".png"

    try:
        _render_png(build_html(sub, panel, ctrls), path)
    except Exception as e:
        if status_label is not None:
            status_label.setText("Export failed: %s" % e)
        return

    if status_label is not None:
        status_label.setText("Exported ✓")
    try:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception:
        pass
