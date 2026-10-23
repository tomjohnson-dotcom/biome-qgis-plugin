# -*- coding: utf-8 -*-
"""
condition_dialog.py
===================

Standalone, reusable Condition-assessment dialog, extracted from
``habitat_form.py`` so the SAME dialog can be opened from two places:

  * the habitat placement form (baseline = interactive *assess* mode), and
  * the Manage-Features screen (re-open *pre-filled* for a saved feature, so a
    forgotten Copy/Export can be recovered).

Modes
-----
* ``assess`` – interactive: tick criteria / score indicators, live grade
  readout, Copy button.  Writes the result back through the ``on_result``
  callback the caller supplies.
* ``goals``  – read-only: the criteria are shown purely as *targets to reach*
  with no tick boxes, no grade and no Copy/Export.  Used for **proposed**
  habitats — you cannot assess the condition of something that does not exist
  yet, so the panel only advertises the goals.

The dialog never persists the individual ticks itself; the caller decides what
to do with ``(grade, report)`` via ``on_result``.  For recovery the caller can
pass an ``initial`` state to pre-tick what was recorded (see
``parse_passed_from_comments``).
"""

import re

from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QCheckBox, QPushButton,
    QButtonGroup, QScrollArea, QFrame, QApplication, QLineEdit,
)

from . import condition_data as CD
from . import theme

GRADE_COLOURS = {
    "Good":     ("#175E3B", "#E9F4EC"),
    "Fairly Good": ("#467A3D", "#EEF6E9"),
    "Moderate": ("#9A6700", "#FBF4E4"),
    "Fairly Poor": ("#A44E27", "#FCF0E8"),
    "Poor":     ("#8F2A23", "#FBEDEB"),
}


# ── written-assessment text ──────────────────────────────────────────────────
def _join_letters(letters):
    letters = list(letters)
    if not letters:
        return ""
    if len(letters) == 1:
        return letters[0]
    if len(letters) == 2:
        return "%s and %s" % (letters[0], letters[1])
    return "%s, and %s" % (", ".join(letters[:-1]), letters[-1])


def _crit_word(n):
    return "Criterion" if n == 1 else "Criteria"


def tick_report_text(ctrls, passed, failed, grade, state):
    """Report sentence for tick-based habitats, e.g. 'Condition Assessment:
    Criteria A, D, and F passed; Criteria B, C, and E failed. Overall, the
    habitat was assessed as being in Poor Condition.'"""
    if grade is None:
        return ""
    parts = []
    if passed:
        parts.append("%s %s passed" % (_crit_word(len(passed)), _join_letters(passed)))
    if failed:
        parts.append("%s %s failed" % (_crit_word(len(failed)), _join_letters(failed)))
    body = "; ".join(parts) if parts else "no criteria assessed"
    extra = ""
    if ctrls["model"] == "urban" and "C" in passed and not state.get("c_absent"):
        extra += (" Criterion C was met at <5% cover but not by complete absence of "
                  "invasive species.")
    if ctrls["model"] == "pond" and state.get("variant") == "woodland":
        extra += " Assessed as a woodland pond."
    return ("Condition Assessment: %s.%s Overall, the habitat was assessed as being "
            "in %s Condition." % (body, extra, grade))


def note_report_text(key):
    return {
        "culvert": "Condition Assessment: Watercourse culvert — condition is fixed at Poor.",
        "rca": ("Condition Assessment: This watercourse requires a River Condition "
                "Assessment (RCA); see the Statutory Biodiversity Metric User Guide."),
        "lake": ("Condition Assessment: Lake condition assessed using the Habitat "
                 "Naturalness Assessment (see the Statutory Biodiversity Metric User Guide)."),
    }.get(key, "")


# Letters look like A, B, E1, E2, A1.  Capture the comma/'and'-joined run that
# precedes the word "passed" in a stored summary so a recovery open can re-tick.
_PASSED_RE = re.compile(r"Criteri(?:on|a)\s+(.+?)\s+passed", re.I)


def parse_passed_from_comments(comments):
    """Best-effort reconstruction of which criteria passed, from the stored
    'Condition Assessment: …' summary.  Returns a dict suitable as ``initial``
    (``{"passed": set(), "c_absent": bool, "variant": str|None}``).  Woodland
    (scored) sheets do not record per-indicator detail, so ``passed`` will be
    empty for those — the dialog then just shows the recorded grade."""
    text = comments or ""
    m = _PASSED_RE.search(text)
    passed = set()
    if m:
        run = m.group(1).replace(" and ", ",").replace("&", ",")
        for tok in run.split(","):
            tok = tok.strip().strip(".").upper()
            if tok and len(tok) <= 3 and tok[0].isalpha():
                passed.add(tok)
    return {
        "passed": passed,
        "c_absent": "complete absence" in text.lower(),
        "variant": "woodland" if "woodland pond" in text.lower() else None,
    }


# ── persistable detail (exact reopen / export, survives project saves) ───────
_TREE_KEYS = ("trees", "line_trees")          # sheets that get the tree no./DBH list


def _san(s):
    """Make a value safe inside the detail string's delimiters."""
    return str(s).replace("|", "/").replace(";", ",").replace("~", "-").replace("=", "-").strip()


def _read_trees(ctrls):
    """Current (tree no., DBH) rows that carry any content."""
    out = []
    for (no_e, dbh_e) in ctrls.get("tree_rows", []):
        no, dbh = no_e.text().strip(), dbh_e.text().strip()
        if no or dbh:
            out.append((no, dbh))
    return out


def serialize_state(ctrls):
    """Compact string of the current ticks/scores (+ any tree no./DBH rows), e.g.
    ``tick|A,C,E|wood`` or ``tick|A,C|trees=T1~32;T2~40`` or ``score|A=3,B=2,…``.
    Stored on the feature so the assessment reopens pre-filled and exports fully."""
    if ctrls.get("model") == "lake":
        items = []
        for letter, btns in ctrls.get("lake_classes", []):
            sel = next((i for i, b in enumerate(btns) if b.isChecked()), None)
            if sel is not None:
                items.append("%s=%d" % (letter, sel + 1))
        return "lake|" + ",".join(items) if items else ""
    if ctrls.get("model") == "score":
        items = []
        for (letter, btns) in ctrls.get("scores", []):
            sel = next((i for i, b in enumerate(btns) if b.isChecked()), None)
            if sel is not None:
                items.append("%s=%d" % (letter, 3 - sel))
        return "score|" + ",".join(items) if items else ""
    checks = ctrls.get("checks", [])
    if not checks:
        return ""
    passed = [L for (L, cb) in checks if cb.isChecked() and cb.isEnabled()]
    parts = ["tick", ",".join(passed)]
    if ctrls.get("c_absent") is not None and ctrls["c_absent"].isChecked():
        parts.append("cabs")
    if ctrls.get("variant") is not None and ctrls["variant"].isChecked():
        parts.append("wood")
    trees = _read_trees(ctrls)
    if trees:
        parts.append("trees=" + ";".join("%s~%s" % (_san(n), _san(d)) for (n, d) in trees))
    return "|".join(parts)


def deserialize_state(s):
    """Inverse of serialize_state -> an ``initial`` dict, or None if empty."""
    if not s:
        return None
    parts = str(s).split("|")
    head = parts[0]
    if head == "lake":
        classes = {}
        for tok in (parts[1].split(",") if len(parts) > 1 else []):
            if "=" in tok:
                letter, value = tok.split("=", 1)
                try:
                    value = int(value)
                    if value in (1, 2, 3, 4, 5):
                        classes[letter.strip()] = value
                except ValueError:
                    pass
        return {"classes": classes} if classes else None
    if head == "score":
        scores = {}
        for tok in (parts[1].split(",") if len(parts) > 1 else []):
            if "=" in tok:
                L, v = tok.split("=", 1)
                try:
                    scores[L.strip()] = int(v)
                except ValueError:
                    pass
        return {"scores": scores} if scores else None
    if head == "tick":
        passed = set(t.strip() for t in (parts[1].split(",") if len(parts) > 1 else []) if t.strip())
        out = {"passed": passed}
        if "cabs" in parts:
            out["c_absent"] = True
        if "wood" in parts:
            out["variant"] = "woodland"
        for part in parts:
            if part.startswith("trees="):
                trees = []
                for pair in part[6:].split(";"):
                    if not pair:
                        continue
                    if "~" in pair:
                        n, d = pair.split("~", 1)
                    else:
                        n, d = pair, ""
                    trees.append((n, d))
                if trees:
                    out["trees"] = trees
        return out
    return None


# ── recompute (grade + readout + write-back) ─────────────────────────────────
def _recompute(ctrls, fire=True):
    """Derive the grade from the current controls, refresh the readout, and —
    when *fire* — push (grade, report) to the caller's ``on_result``.  The
    initial render passes fire=False so merely opening never writes anything."""
    readout = ctrls.get("readout")
    sub = ctrls["sub"]
    report = ""

    if ctrls["model"] == "lake":
        classes = {}
        for letter, btns in ctrls.get("lake_classes", []):
            sel = next((i for i, b in enumerate(btns) if b.isChecked()), None)
            if sel is not None:
                classes[letter] = sel + 1
        grade = CD.evaluate(sub, {"classes": classes})
        if classes:
            mean = sum(classes.values()) / float(len(classes))
            detail = ("average class %.2f (%d of 4 assessed)" %
                      (mean, len(classes)))
        else:
            mean = None
            detail = "classify all 4 attributes"
        if grade is not None:
            rounded = int(mean + 0.5)
            report = ("Condition Assessment: Habitat Naturalness Assessment "
                      "classes — physical %d, hydrological %d, chemical %d, and "
                      "biological %d; average %.2f, rounded to Class %d. Overall, "
                      "the habitat was assessed as being in %s Condition." %
                      (classes["P"], classes["H"], classes["C"], classes["B"],
                       mean, rounded, grade))
    elif ctrls["model"] == "score":
        scores = {}
        for (letter, btns) in ctrls["scores"]:
            sel = next((i for i, b in enumerate(btns) if b.isChecked()), None)
            if sel is not None:
                scores[letter] = 3 - sel              # Good=3, Mod=2, Poor=1
        grade = CD.evaluate(sub, {"scores": scores})
        total = sum(scores.values())
        n_tot = len(ctrls["scores"])
        if grade is None:
            detail = "score all %d indicators (%d done, %d/39)" % (n_tot, len(scores), total)
        else:
            detail = "%d / 39" % total
            report = ("Condition Assessment: Assessed using the %d-indicator woodland "
                      "condition method (score %d of %d). Overall, the habitat was "
                      "assessed as being in %s Condition." % (n_tot, total, n_tot * 3, grade))
    else:
        passed = [L for (L, cb) in ctrls["checks"] if cb.isChecked() and cb.isEnabled()]
        active = [L for (L, cb) in ctrls["checks"] if cb.isEnabled()]
        failed = [L for L in active if L not in passed]
        state = {"passed": set(passed)}
        if ctrls.get("c_absent") is not None:
            state["c_absent"] = ctrls["c_absent"].isChecked()
        if ctrls.get("variant") is not None:
            state["variant"] = "woodland" if ctrls["variant"].isChecked() else "nonwoodland"
        grade = CD.evaluate(sub, state)
        detail = "%d passed" % len(passed)
        report = tick_report_text(ctrls, passed, failed, grade, state)

    ctrls["grade"] = grade
    ctrls["report"] = report
    ctrls["detail"] = serialize_state(ctrls)

    if readout is not None:
        if grade is None:
            readout.setText("Condition: —   ·   " + detail)
            readout.setStyleSheet("font-size:15px; font-weight:600; color:#5F6F65; padding:6px 4px;")
        else:
            fg, bg = GRADE_COLOURS.get(grade, ("#1E2924", "#FFFFFF"))
            readout.setText("Condition: %s   ·   %s" % (grade, detail))
            readout.setStyleSheet("font-size:15px; font-weight:600; color:%s; background:%s;"
                                  " border-radius:3px; padding:6px 8px;" % (fg, bg))

    if fire:
        ctrls["touched"] = True
        cb = ctrls.get("on_result")
        if cb and (report or grade):
            cb(grade, report, ctrls["detail"])


def _on_pond_variant(ctrls):
    """Woodland ponds drop criteria H and I (7 core only)."""
    wood = ctrls["variant"].isChecked()
    for letter, cb in ctrls["checks"]:
        if letter in ("H", "I"):
            cb.setEnabled(not wood)
            if wood:
                cb.setChecked(False)
    _recompute(ctrls)


# ── readout helpers ──────────────────────────────────────────────────────────
def _add_band_labels(layout, labels):
    hdr = QLabel("Grade thresholds")
    hdr.setStyleSheet("font-size:13px; font-weight:600; color:#5F6F65; padding-top:8px;")
    layout.addWidget(hdr)
    for grade, text in (labels or []):
        fg, bg = GRADE_COLOURS.get(grade, ("#2A332D", "#FFFFFF"))
        l = QLabel("%s: %s" % (grade, text))
        l.setWordWrap(True)
        l.setStyleSheet("font-size:13px; color:%s; background:%s; border-radius:3px;"
                        " padding:3px 8px; margin-top:2px;" % (fg, bg))
        layout.addWidget(l)


def _add_footnotes(layout, footnotes):
    if not footnotes:
        return
    hdr = QLabel("Footnotes")
    hdr.setStyleSheet("font-size:13px; font-weight:600; color:#5F6F65; padding-top:8px;")
    layout.addWidget(hdr)
    for f in footnotes:
        l = QLabel(f)
        l.setWordWrap(True)
        l.setStyleSheet("font-size:12px; color:#8A968D; padding:0 2px;")
        layout.addWidget(l)


# ── control builders ─────────────────────────────────────────────────────────
def _build_tick_controls(layout, panel, ctrls, goals):
    model = panel["model"]
    groups = panel.get("groups")
    rc = lambda _=None, c=ctrls: _recompute(c)

    def add_check(text, color, on_toggle):
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(6)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size:13px; color:%s;" % color)
        cb = None
        if goals:
            # read-only goal: a bullet, no tick box
            bullet = QLabel("•")
            bullet.setStyleSheet("font-size:13px; color:%s;" % color)
            rl.addWidget(bullet, 0)
        else:
            cb = QCheckBox()
            lbl.mousePressEvent = lambda e, c=cb: c.toggle()
            rl.addWidget(cb, 0)
        rl.addWidget(lbl, 1)
        if cb is not None:
            cb.toggled.connect(on_toggle)
        layout.addWidget(row)
        return cb

    if model == "pond" and not goals:
        ctrls["variant"] = add_check(
            "Woodland pond (surrounded by woodland) — assess 7 core criteria only",
            "#1E2924", lambda _=None, c=ctrls: _on_pond_variant(c))
    last_group = None
    for (letter, text, essential) in panel["criteria"]:
        if groups:
            g = letter[0]
            if g != last_group:
                last_group = g
                hdr = QLabel(groups.get(g, g))
                hdr.setStyleSheet("font-size:13px; font-weight:600; color:#5F6F65;"
                                  " padding-top:4px;")
                layout.addWidget(hdr)
        label = "%s.  %s" % (letter, text)
        if essential:
            label += "   (essential)"
        cb = add_check(label, "#9A6700" if essential else "#2A332D", rc)
        if cb is not None:
            ctrls["checks"].append((letter, cb))
        if model == "urban" and letter == "C" and not goals:
            ctrls["c_absent"] = add_check(
                "↳ C met by complete absence of invasive species (required for Good)",
                "#5F6F65", rc)


def _build_score_controls(layout, panel, ctrls, parent, goals):
    seg = [("Good", "Good", "#175E3B"), ("Mod", "Moderate", "#9A6700"),
           ("Poor", "Poor", "#8F2A23")]
    for (letter, name, descs) in panel["indicators"]:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        nl = QLabel("%s.  %s" % (letter, name))
        nl.setWordWrap(True)
        nl.setStyleSheet("font-size:13px; font-weight:600; color:#2A332D;")
        rl.addWidget(nl, 1)
        if not goals:
            grp = QButtonGroup(parent)
            grp.setExclusive(True)
            btns = []
            for idx, (lab, _full, col) in enumerate(seg):
                b = QPushButton(lab)
                b.setCheckable(True)
                b.setFixedWidth(58)
                b.setToolTip(descs[idx])
                b.setStyleSheet(
                    "QPushButton { font-size:12px; padding:4px 2px; border:1px solid #C9D3CC;"
                    " border-radius:3px; background:#FFFFFF; color:#5F6F65; }"
                    "QPushButton:checked { background:%s22; color:%s; border-color:%s;"
                    " font-weight:600; }" % (col, col, col))
                grp.addButton(b, idx)
                rl.addWidget(b)
                btns.append(b)
            grp.buttonClicked.connect(lambda _b=None, c=ctrls: _recompute(c))
            ctrls["scores"].append((letter, btns))
        layout.addWidget(row)
        for (_lab, full, col), d in zip(seg, descs):
            dl = QLabel("%s: %s" % (full, d))
            dl.setWordWrap(True)
            dl.setStyleSheet("font-size:12px; color:%s; padding-left:10px;" % col)
            layout.addWidget(dl)


def _build_lake_controls(layout, panel, ctrls, parent, goals):
    """Four HNA attributes, each assigned naturalness Class 1–5."""
    for letter, name, desc in panel["attributes"]:
        title = QLabel("%s.  %s" % (letter, name))
        title.setWordWrap(True)
        title.setStyleSheet("font-size:13px; font-weight:600; color:#2A332D; padding-top:5px;")
        layout.addWidget(title)
        help_text = QLabel(desc)
        help_text.setWordWrap(True)
        help_text.setStyleSheet("font-size:11px; color:#5F6F65; padding-bottom:2px;")
        layout.addWidget(help_text)

        class_guidance = panel.get("guidance", {}).get(letter, [])
        if class_guidance:
            guide_button = QPushButton("▸  Show class guidance")
            guide_button.setCheckable(True)
            guide_button.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; color:#2E6B4F;"
                " border:none; padding:2px 0; font-size:11px; }"
                "QPushButton:hover { color:#175E3B; text-decoration:underline; }")
            guide = QLabel("".join(
                '<p style="margin:0 0 5px 0;"><b>Class %d%s</b> — %s</p>' %
                (i, " (Natural)" if i == 1 else (" (Least natural)" if i == 5 else ""), text)
                for i, text in enumerate(class_guidance, 1)))
            guide.setWordWrap(True)
            guide.setTextFormat(Qt.TextFormat.RichText)
            guide.setStyleSheet("font-size:11px; color:#46524A; background:#EEF3EF;"
                                " border:1px solid #D7E1DA; border-radius:4px; padding:7px;")
            guide.setVisible(False)

            def _toggle_guide(checked, button=guide_button, label=guide):
                label.setVisible(checked)
                button.setText(("▾  Hide" if checked else "▸  Show") + " class guidance")

            guide_button.toggled.connect(_toggle_guide)
            layout.addWidget(guide_button)
            layout.addWidget(guide)
        if goals:
            target = QLabel("Target Class 1 (Natural)")
            target.setStyleSheet("font-size:12px; color:#175E3B; padding-bottom:4px;")
            layout.addWidget(target)
            continue
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 4)
        row_layout.setSpacing(4)
        group = QButtonGroup(parent)
        group.setExclusive(True)
        buttons = []
        for value in range(1, 6):
            button = QPushButton(str(value))
            button.setCheckable(True)
            button.setToolTip("Class %d%s" %
                              (value, " — Natural" if value == 1 else
                               (" — Least natural" if value == 5 else "")))
            button.setStyleSheet(
                "QPushButton { font-size:12px; padding:5px; border:1px solid #C9D3CC;"
                " border-radius:3px; background:#FFFFFF; color:#5F6F65; }"
                "QPushButton:checked { background:#DDEBE2; color:#175E3B;"
                " border-color:#175E3B; font-weight:600; }")
            group.addButton(button, value)
            row_layout.addWidget(button)
            buttons.append(button)
        group.buttonClicked.connect(lambda _b=None, c=ctrls: _recompute(c))
        ctrls["lake_classes"].append((letter, buttons))
        layout.addWidget(row)


def _apply_initial(ctrls, initial):
    """Pre-fill the recorded ticks (tick sheets) or scores (woodland)."""
    if not initial:
        return
    classes = initial.get("classes")
    if classes and ctrls.get("lake_classes"):
        for letter, btns in ctrls["lake_classes"]:
            value = classes.get(letter)
            if value in (1, 2, 3, 4, 5):
                btns[value - 1].setChecked(True)
    scores = initial.get("scores")
    if scores and ctrls.get("scores"):
        for (letter, btns) in ctrls["scores"]:
            v = scores.get(letter)
            if v in (1, 2, 3):
                btns[3 - v].setChecked(True)     # 3->Good(0), 2->Mod(1), 1->Poor(2)
    passed = set(initial.get("passed") or ())
    if ctrls.get("variant") is not None and initial.get("variant") == "woodland":
        ctrls["variant"].setChecked(True)
    for letter, cb in ctrls.get("checks", []):
        cb.setChecked(letter in passed)
    if ctrls.get("c_absent") is not None and initial.get("c_absent"):
        ctrls["c_absent"].setChecked(True)


def _mark_trees(ctrls):
    """A tree no./DBH row changed — refresh the stored detail and flag a change,
    without disturbing the criteria-derived grade."""
    ctrls["detail"] = serialize_state(ctrls)
    ctrls["touched"] = True
    cb = ctrls.get("on_result")
    if cb:
        cb(ctrls.get("grade"), ctrls.get("report"), ctrls["detail"])


def _build_tree_inputs(layout, ctrls, initial_trees):
    """Tree-only: a repeatable (tree no., DBH) list. Several trees can share one
    assessment. Stored on the feature + shown on the PNG; never in the copy text."""
    hdr = QLabel("Trees in this assessment — no. & DBH")
    hdr.setStyleSheet("font-size:13px; font-weight:600; color:#5F6F65; padding-top:8px;")
    layout.addWidget(hdr)
    note = QLabel("Optional. List each tree this covers (several can share one "
                  "assessment). Added to the exported PNG only — not the copied text.")
    note.setWordWrap(True)
    note.setStyleSheet("font-size:11px; color:#8A968D;")
    layout.addWidget(note)

    rows_host = QWidget()
    rows_lay = QVBoxLayout(rows_host)
    rows_lay.setContentsMargins(0, 2, 0, 2)
    rows_lay.setSpacing(3)
    layout.addWidget(rows_host)
    ctrls["tree_rows"] = []

    def add_row(no="", dbh=""):
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        no_e = QLineEdit(no)
        no_e.setPlaceholderText("Tree no. / ref")
        dbh_e = QLineEdit(dbh)
        dbh_e.setPlaceholderText("DBH (cm)")
        for e in (no_e, dbh_e):
            e.setStyleSheet("QLineEdit { border:1px solid #C9D3CC; border-radius:4px;"
                            " padding:3px 6px; font-size:12px; background:white; }")
            e.editingFinished.connect(lambda c=ctrls: _mark_trees(c))
        rm = QPushButton("✕")
        rm.setFixedSize(24, 24)
        rm.setToolTip("Remove this tree")
        rm.setStyleSheet("QPushButton { background:#FBEDEB; color:#8F2A23; border:none;"
                         " border-radius:4px; font-size:11px; }"
                         " QPushButton:hover { background:#F4CFCB; }")
        entry = (no_e, dbh_e)

        def _remove():
            try:
                ctrls["tree_rows"].remove(entry)
            except ValueError:
                pass
            row.setParent(None)
            _mark_trees(ctrls)
        rm.clicked.connect(_remove)
        rl.addWidget(no_e, 3)
        rl.addWidget(dbh_e, 2)
        rl.addWidget(rm, 0)
        rows_lay.addWidget(row)
        ctrls["tree_rows"].append(entry)

    for (no, dbh) in (initial_trees or []):
        add_row(no, dbh)
    if not ctrls["tree_rows"]:
        add_row()

    add_btn = QPushButton("+  Add tree")
    add_btn.setStyleSheet("QPushButton { background:#EAF2EC; color:#1F4A37; border:none;"
                          " border-radius:4px; padding:4px 12px; font-size:12px; }"
                          " QPushButton:hover { background:#D2E6DA; }")
    add_btn.clicked.connect(lambda: add_row())
    layout.addWidget(add_btn)


def build_condition_panel(layout, sub, *, parent, goals=False, initial=None, on_result=None):
    """Build the condition helper into *layout* and return its ``ctrls`` dict."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().setParent(None)

    panel = CD.panel_for(sub)
    ctrls = {"sub": sub, "model": None, "checks": [], "c_absent": None,
             "variant": None, "scores": [], "lake_classes": [],
             "readout": None, "report": "",
             "grade": None, "goals": goals, "on_result": on_result, "touched": False}

    if not panel:
        lbl = QLabel("No criteria available for this habitat type.")
        lbl.setStyleSheet("color:#8A968D; font-size:13px;")
        layout.addWidget(lbl)
        return ctrls
    ctrls["model"] = panel["model"]

    if panel["model"] == "note":
        note = QLabel(panel["note"])
        note.setWordWrap(True)
        note.setStyleSheet("color:#5F6F65; font-size:13px; background:#F4F6F2;"
                           " border-radius:4px; padding:8px;")
        layout.addWidget(note)
        _add_footnotes(layout, panel.get("footnotes"))
        ctrls["report"] = note_report_text(panel["key"])
        ctrls["detail"] = ""
        if not goals and on_result:
            on_result(None, ctrls["report"], "")
        return ctrls

    if goals:
        intro = QLabel("Proposed habitat — these are the condition goals to aim for. "
                       "It is a target, so it isn't scored here.")
    else:
        if panel["model"] == "lake":
            intro = QLabel("Assign Class 1 (natural) to Class 5 (least natural) to each "
                           "attribute using the FBA lake naturalness guidance.")
        else:
            intro = QLabel("Score each indicator." if panel["model"] == "score"
                           else "Tick each criterion that passes.")
    intro.setWordWrap(True)
    intro.setStyleSheet("color:#5F6F65; font-size:12px;")
    layout.addWidget(intro)

    if panel["model"] == "lake":
        _build_lake_controls(layout, panel, ctrls, parent, goals)
        evidence_note = QLabel(
            "Evidence tips: assess the lake as a whole where possible; record uncertainty where "
            "access or seasonal evidence is limited. Aquatic plants are best assessed from June "
            "to September, algal blooms from March to October, and water chemistry in winter or "
            "early spring. Photographs, aerial imagery, historic mapping, water-quality data, and "
            "species records can all support the class selected.")
        evidence_note.setWordWrap(True)
        evidence_note.setStyleSheet("font-size:11px; color:#5F6F65; background:#EEF3EF;"
                                    " border-radius:4px; padding:7px;")
        layout.addWidget(evidence_note)
        method_note = QLabel(panel.get("note", ""))
        method_note.setWordWrap(True)
        method_note.setStyleSheet("font-size:11px; color:#5F6F65; background:#F4F6F2;"
                                  " border-radius:4px; padding:7px;")
        layout.addWidget(method_note)
    elif panel["model"] == "score":
        _build_score_controls(layout, panel, ctrls, parent, goals)
    else:
        _build_tick_controls(layout, panel, ctrls, goals)

    if not goals:
        readout = QLabel("")
        readout.setWordWrap(True)
        readout.setStyleSheet("font-size:15px; font-weight:600; padding:6px 4px;")
        ctrls["readout"] = readout
        layout.addWidget(readout)

    if not goals and panel.get("key") in _TREE_KEYS:
        _build_tree_inputs(layout, ctrls, (initial or {}).get("trees"))

    _add_band_labels(layout, panel["labels"])
    _add_footnotes(layout, panel.get("footnotes"))

    if not goals:
        _apply_initial(ctrls, initial)
        _recompute(ctrls, fire=False)          # initial readout only — never writes
        ctrls["touched"] = False               # pre-fill must not count as a user change
        # Recovery fallback: when the criteria can't be reconstructed from the
        # stored text (e.g. woodland's per-indicator scores aren't saved), keep
        # the recorded summary so Copy still works, and show the recorded grade.
        if initial:
            if not ctrls["report"] and initial.get("report"):
                ctrls["report"] = initial["report"]
            g0 = initial.get("grade")
            # Couldn't reconstruct (e.g. woodland scores aren't stored): fall back
            # to the recorded grade so the readout + Export still show it.
            if ctrls["grade"] is None and g0 in GRADE_COLOURS:
                ctrls["grade"] = g0
                if ctrls.get("readout") is not None:
                    fg, bg = GRADE_COLOURS[g0]
                    ctrls["readout"].setText("Condition: %s   ·   recorded (re-score to change)" % g0)
                    ctrls["readout"].setStyleSheet(
                        "font-size:15px; font-weight:600; color:%s; background:%s;"
                        " border-radius:3px; padding:6px 8px;" % (fg, bg))
    return ctrls


def _copy(ctrls, status_label=None):
    text = ctrls.get("report", "")
    if not text:
        if status_label is not None:
            status_label.setText("Assess the criteria first.")
        return
    QApplication.clipboard().setText(text)
    if status_label is not None:
        status_label.setText("Summary copied ✓")


def _copy_full(ctrls, status_label=None):
    """Copy the full per-criterion record (verbatim wording + pass/fail/score)."""
    try:
        from .condition_export import condition_plain_text
        text = condition_plain_text(ctrls)
    except Exception:
        text = ctrls.get("report", "")
    if not text:
        if status_label is not None:
            status_label.setText("Assess the criteria first.")
        return
    QApplication.clipboard().setText(text)
    if status_label is not None:
        status_label.setText("Full assessment copied ✓")


def open_condition_dialog(parent, sub, *, goals=False, initial=None, on_result=None,
                          allow_export=False, on_export=None, title=None):
    """Open the condition dialog modally.  Returns its ``ctrls`` dict afterwards
    so callers that prefer write-on-close (e.g. Manage) can read the final
    ``grade`` / ``report`` / ``touched`` instead of using ``on_result`` live."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Condition goals" if goals else "Condition assessment")
    dlg.setStyleSheet(
        "QDialog { background:#F7F8F6; }"
        " QLabel, QCheckBox, QPushButton { font-family:'Inter','Segoe UI',sans-serif; }")
    dlg.setMinimumSize(380, 420)
    dlg.resize(600, 720)
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    head = title or (("Condition goals — %s" if goals else "Condition assessment — %s") % sub)
    header = QLabel(head)
    header.setWordWrap(True)
    header.setStyleSheet(
        "background:#1F4A37; color:white; font-size:14px; font-weight:600;"
        " padding:11px 14px; font-family:'Inter','Segoe UI',sans-serif;")
    outer.addWidget(header)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background:#F7F8F6; border:none; }")
    inner = QWidget()
    inner.setStyleSheet("background:#F7F8F6;")
    panel_layout = QVBoxLayout(inner)
    panel_layout.setContentsMargins(14, 12, 14, 12)
    panel_layout.setSpacing(5)
    scroll.setWidget(inner)
    outer.addWidget(scroll, 1)
    ctrls = build_condition_panel(panel_layout, sub, parent=dlg, goals=goals,
                                  initial=initial, on_result=on_result)

    footer = QWidget()
    footer.setStyleSheet("background:#EFF2ED;")
    btnrow = QHBoxLayout(footer)
    btnrow.setContentsMargins(12, 8, 12, 8)
    btnrow.setSpacing(8)
    status = QLabel("")
    status.setStyleSheet("font-size:12px; color:#5F6F65;")

    if not goals:
        copy_btn = QPushButton("  Copy summary")
        copy_btn.setIcon(theme.icon("copy", "#FFFFFF", 15))
        copy_btn.setIconSize(QSize(15, 15))
        copy_btn.setStyleSheet(
            "QPushButton { background:#1F4A37; color:white; border:none; border-radius:5px;"
            " padding:8px 12px; font-size:12px; font-weight:500; }"
            " QPushButton:hover { background:#2E6B4F; }")
        copy_btn.clicked.connect(lambda: _copy(ctrls, status))
        btnrow.addWidget(copy_btn)

        full_btn = QPushButton("  Copy full")
        full_btn.setIcon(theme.icon("copy", "#1F4A37", 15))
        full_btn.setIconSize(QSize(15, 15))
        full_btn.setStyleSheet(
            "QPushButton { background:#FFFFFF; color:#1F4A37; border:1px solid #1F4A37;"
            " border-radius:5px; padding:8px 12px; font-size:12px; font-weight:500; }"
            " QPushButton:hover { background:#EAF2EC; }")
        full_btn.clicked.connect(lambda: _copy_full(ctrls, status))
        btnrow.addWidget(full_btn)

        if allow_export:
            export_btn = QPushButton("  Export sheet (PNG)")
            export_btn.setIcon(theme.icon("file-text", "#FFFFFF", 15))
            export_btn.setIconSize(QSize(15, 15))
            export_btn.setStyleSheet(
                "QPushButton { background:#2E6B4F; color:white; border:none; border-radius:5px;"
                " padding:8px 14px; font-size:12px; font-weight:500; }"
                " QPushButton:hover { background:#3C8462; }")

            def _do_export():
                if on_export is not None:
                    on_export(ctrls, status)
                    return
                try:
                    from .condition_export import export_condition_png
                    export_condition_png(dlg, ctrls, status)
                except Exception as e:
                    status.setText("Export failed: %s" % e)
            export_btn.clicked.connect(lambda: _do_export())
            btnrow.addWidget(export_btn)
    btnrow.addWidget(status, 1)

    close_btn = QPushButton("Close")
    close_btn.setStyleSheet(
        "QPushButton { background:#FFFFFF; color:#1E2924; border:1px solid #C9D3CC;"
        " border-radius:5px; padding:8px 16px; font-size:12px; }"
        " QPushButton:hover { background:#E7ECE4; }")
    close_btn.clicked.connect(dlg.accept)
    btnrow.addWidget(close_btn)
    outer.addWidget(footer)

    dlg.exec()
    return ctrls
