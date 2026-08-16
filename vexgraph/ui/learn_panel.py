"""The Beginner course's classroom: one exercise at a time, checked live.

The panel never guesses: Check runs the exercise's deterministic review
against the graph on the canvas, and the first missing piece comes back as
a plain sentence. The assistant stays one button away as the professor -
sent the exercise as context - but the course never depends on it.
"""

from __future__ import annotations

import html
import json
import re

from PySide6 import QtCore, QtGui, QtWidgets

from .. import learn
from . import settings, theme


class LearnPanel(QtWidgets.QWidget):
    ask_professor = QtCore.Signal(str)        # question, exercise attached
    open_manual = QtCore.Signal()
    focus_changed = QtCore.Signal(object)     # set[str] of node types, or None
    reveal_node = QtCore.Signal(str)          # node type -> find it in the library
    scene_requested = QtCore.Signal(object)   # learn.Scene to build

    def __init__(self, get_graph, registry=None, parent=None):
        super().__init__(parent)
        self._get_graph = get_graph
        self._registry = registry
        self._course = learn.BEGINNER
        self._index = 0
        self._hints_shown = 0
        self._settings = settings.store()
        self.progress = self._load_progress()

        self.header = QtWidgets.QLabel()
        self.header.setFont(theme.ui_font(9, bold=True))
        self.body = QtWidgets.QTextBrowser()
        self.body.setOpenExternalLinks(False)
        # Node names and functions read as links because they are ones: blue,
        # and they go somewhere useful.
        self.body.setStyleSheet("QTextBrowser { background: transparent; }")
        palette = self.body.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Link,
                         QtGui.QColor("#6db3f2"))
        self.body.setPalette(palette)
        self.body.anchorClicked.connect(self._follow)
        self.body.setFont(theme.ui_font(9))

        self.focus_box = QtWidgets.QCheckBox("Focus")
        self.focus_box.setChecked(True)
        self.focus_box.setToolTip(
            "Show only the nodes this exercise needs - and the ones earlier "
            "exercises taught. Turn it off to reach the whole library.")
        self.focus_box.toggled.connect(lambda _: self._push_focus())
        self.scene_button = QtWidgets.QPushButton("Set up the scene")
        self.scene_button.setToolTip(
            "Build the geometry this exercise wants and wire it to the "
            "wrangle, so you can see what you are doing.")
        self.scene_button.clicked.connect(
            lambda: self.scene_requested.emit(self.exercise.scene))
        self.check_button = QtWidgets.QPushButton("Check my graph")
        self.hint_button = QtWidgets.QPushButton("Hint")
        self.professor_button = QtWidgets.QPushButton("Ask the professor")
        self.professor_button.setToolTip(
            "Send your question to the assistant with this exercise "
            "attached, so it knows what you are working on.")
        self.back_button = QtWidgets.QPushButton("◀")
        self.back_button.setFixedWidth(30)
        self.next_button = QtWidgets.QPushButton("▶")
        self.next_button.setFixedWidth(30)

        self.verdict = QtWidgets.QLabel("")
        self.verdict.setWordWrap(True)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(8, 4, 8, 8)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.focus_box)
        buttons.addWidget(self.scene_button)
        buttons.addWidget(self.check_button, 1)
        buttons.addWidget(self.hint_button)
        buttons.addWidget(self.professor_button)
        buttons.addWidget(self.next_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.header)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.verdict)
        layout.addLayout(buttons)

        self.check_button.clicked.connect(self.check)
        self.hint_button.clicked.connect(self._reveal_hint)
        self.professor_button.clicked.connect(self._call_professor)
        self.back_button.clicked.connect(lambda: self._go(-1))
        self.next_button.clicked.connect(lambda: self._go(+1))

        self._index = self._first_unsolved()
        self._show()
        # No focus until the course is actually opened; the signal is
        # connected by the panel, which pushes it when Learn unfolds.

    # ------------------------------------------------------------- persistence

    def _load_progress(self) -> learn.Progress:
        raw = self._settings.value("learn/progress", "")
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return learn.Progress(
            completed=set(data.get("completed", ())),
            attempts=dict(data.get("attempts", {})),
            hints_used=dict(data.get("hints", {})))

    def _save_progress(self) -> None:
        self._settings.setValue("learn/progress", json.dumps({
            "completed": sorted(self.progress.completed),
            "attempts": self.progress.attempts,
            "hints": self.progress.hints_used,
        }))

    def _first_unsolved(self) -> int:
        for index, exercise in enumerate(self._course):
            if exercise.key not in self.progress.completed:
                return index
        return len(self._course) - 1

    # ------------------------------------------------------------------ view

    @property
    def exercise(self) -> learn.Exercise:
        return self._course[self._index]

    def _show(self, keep_scroll: bool = True, to_bottom: bool = False) -> None:
        exercise = self.exercise
        bar = self.body.verticalScrollBar()
        was_at = bar.value()
        done = exercise.key in self.progress.completed
        dots = "".join("●" if e.key in self.progress.completed else "○"
                       for e in self._course)
        self.header.setText(
            f"  Beginner {self._index + 1}/{len(self._course)}   {dots}")
        parts = [f"<h3>{exercise.title}{' ✓' if done else ''}</h3>",
                 f"<p><b>{exercise.goal}</b></p>"]
        if exercise.scene is not None:
            # An exercise whose result is invisible teaches nothing.
            parts.append(
                "<p style='color:#8fa4b0'>To see it: "
                f"{exercise.scene.describe}</p>")
        if exercise.nodes:
            # Naming the search word is the difference between "add a node"
            # and a beginner staring at 1360 of them - and naming what the
            # node is CALLED matters just as much: typing "modulo" finds a
            # node labelled Remainder, and being told only the first reads as
            # an instruction to use a node that does not exist.
            rows = "".join(
                f"<li>Tab → “{term}” → <a href='node:{node_type}'>"
                f"{self._label_of(node_type)}</a> — {purpose}</li>"
                for term, node_type, purpose in exercise.nodes)
            parts.append(
                "<p style='color:#8fa4b0'>Nodes for this exercise "
                "<span style='color:#6a6a6a'>(click one to find it in the "
                "library)</span>:</p>"
                f"<ul style='color:#a8b4bc'>{rows}</ul>")
        steps = self._linkify(exercise.steps, exercise)
        parts.append("<p>" + steps.replace("\n\n", "</p><p>")
                     .replace("\n", "<br>") + "</p>")
        for shown in range(self._hints_shown):
            if shown < len(exercise.hints):
                # Hints are where the function names live - and a function
                # name is exactly the thing worth a trip to SideFX.
                parts.append(f"<p style='color:#b0a48f'>💡 "
                             f"{self._linkify(exercise.hints[shown], exercise)}"
                             f"</p>")
        if done and exercise.deeper:
            links = " · ".join(
                f"<a href='{url}'>{label}</a>" for label, url in exercise.deeper)
            parts.append(f"<p style='color:#8a8a8a'>Go deeper: {links}</p>")
        self.body.setHtml("".join(parts))
        # setHtml throws the reader back to the top, which on a long exercise
        # means every Hint costs you your place. The new hint is at the
        # bottom, so that is where a hint scrolls to; anything else keeps the
        # position it had.
        if to_bottom:
            bar.setValue(bar.maximum())
        elif keep_scroll:
            bar.setValue(min(was_at, bar.maximum()))
        self.verdict.setText("")
        # Always walkable. Locking the way forward until an exercise is
        # solved meant a student whose CORRECT graph was not recognised had
        # no way out at all - and browsing ahead is not cheating anyway.
        self.back_button.setEnabled(self._index > 0)
        self.next_button.setEnabled(self._index < len(self._course) - 1)
        self.back_button.setToolTip("Previous exercise")
        self.next_button.setToolTip("Next exercise")
        self.hint_button.setEnabled(self._hints_shown < len(exercise.hints))

    def _label_of(self, node_type: str) -> str:
        """What the node is actually called on screen."""
        definition = (self._registry.get(node_type)
                      if self._registry is not None else None)
        return definition.label if definition is not None else node_type

    def _linkify(self, text: str, exercise) -> str:
        """Node names and VEX functions become links you can follow.

        Two kinds, both worth a click: the node the sentence is telling you
        to find (it opens the library at it, under whatever it is really
        called), and any VEX function mentioned (it opens SideFX's page for
        it, which is where the real detail lives).
        """
        from .. import help as vexhelp                            # noqa: PLC0415

        escaped = html.escape(text)
        stash: list[str] = []

        def keep(markup: str) -> str:
            """Park finished markup so later passes cannot chew through it."""
            stash.append(markup)
            return f"\x00{len(stash) - 1}\x00"

        # Functions first, and by name-followed-by-paren rather than empty
        # parens: the hints write fit(value, oldmin, ...) as often as they
        # write normalize(). The help archive is the filter - a name with no
        # page is not a VEX function, which is what keeps `if (` out.
        def function_link(match: re.Match) -> str:
            name = match.group(1)
            doc = vexhelp.page(name)
            if doc is None:
                return match.group(0)
            return keep(f"<a href='{doc.url}'>{name}</a>") + match.group(2)

        escaped = re.sub(r"\b([a-z_][a-z0-9_]*)(\s*\()", function_link, escaped)

        # Then node names, once each: they open the library at that node,
        # under whatever it is really called.
        for term, node_type, _purpose in exercise.nodes:
            label = self._label_of(node_type)
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            replacement = keep(
                f"<a href='node:{node_type}'>{term}</a>"
                + (f" <span style='color:#6a6a6a'>({label})</span>"
                   if label.lower() != term.lower() else ""))
            escaped = pattern.sub(replacement, escaped, count=1)

        return re.sub(r"\x00(\d+)\x00",
                      lambda m: stash[int(m.group(1))], escaped)

    def _go(self, step: int) -> None:
        self._index = max(0, min(len(self._course) - 1, self._index + step))
        self._hints_shown = 0
        self._show()
        self._push_focus()

    def _push_focus(self) -> None:
        """Tell the library how much of itself to show right now."""
        self.focus_changed.emit(
            learn.allowed_upto(self._index) if self.focus_box.isChecked()
            else None)

    def release_focus(self) -> None:
        """Called when the course is folded away: the library is whole again."""
        self.focus_changed.emit(None)

    # --------------------------------------------------------------- actions

    def check(self) -> None:
        exercise = self.exercise
        self.progress.attempts[exercise.key] = (
            self.progress.attempts.get(exercise.key, 0) + 1)
        verdict = exercise.review(self._get_graph())
        if verdict:
            self.verdict.setStyleSheet("color: #d0a55a; padding: 0 10px;")
            self.verdict.setText(verdict)
        else:
            first_time = exercise.key not in self.progress.completed
            self.progress.completed.add(exercise.key)
            self.verdict.setStyleSheet("color: #7aba7a; padding: 0 10px;")
            if self._index + 1 < len(self._course):
                self.verdict.setText(
                    "Solved! That is exactly it." if first_time
                    else "Still solved.")
            else:
                weak = self.progress.weakest()
                tail = (f" Revisit: {', '.join(weak)}." if weak else "")
                self.verdict.setText(
                    f"Solved - and that was the whole Beginner block!{tail}")
            self._show()
            self.verdict.setText(self.verdict.text() or "Solved!")
        self._save_progress()

    def _reveal_hint(self) -> None:
        exercise = self.exercise
        if self._hints_shown < len(exercise.hints):
            self._hints_shown += 1
            self.progress.hints_used[exercise.key] = max(
                self.progress.hints_used.get(exercise.key, 0),
                self._hints_shown)
            self._save_progress()
            self._show(to_bottom=True)

    def _call_professor(self) -> None:
        exercise = self.exercise
        self.ask_professor.emit(
            f"I am on the exercise \"{exercise.title}\" — the goal is: "
            f"{exercise.goal} Explain what I am missing, step by step, "
            f"without giving me the finished VEX.")

    def _follow(self, url: QtCore.QUrl) -> None:
        text = url.toString()
        if text.startswith("manual"):
            self.open_manual.emit()
        elif text.startswith("node:"):
            self.reveal_node.emit(text[5:])
        else:
            QtGui.QDesktopServices.openUrl(url)
