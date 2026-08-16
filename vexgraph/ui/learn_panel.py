"""The Beginner course's classroom: one exercise at a time, checked live.

The panel never guesses: Check runs the exercise's deterministic review
against the graph on the canvas, and the first missing piece comes back as
a plain sentence. The assistant stays one button away as the professor -
sent the exercise as context - but the course never depends on it.
"""

from __future__ import annotations

import json

from PySide6 import QtCore, QtGui, QtWidgets

from .. import learn
from . import settings, theme


class LearnPanel(QtWidgets.QWidget):
    ask_professor = QtCore.Signal(str)        # question, exercise attached
    open_manual = QtCore.Signal()

    def __init__(self, get_graph, parent=None):
        super().__init__(parent)
        self._get_graph = get_graph
        self._course = learn.BEGINNER
        self._index = 0
        self._hints_shown = 0
        self._settings = settings.store()
        self.progress = self._load_progress()

        self.header = QtWidgets.QLabel()
        self.header.setFont(theme.ui_font(9, bold=True))
        self.body = QtWidgets.QTextBrowser()
        self.body.setOpenExternalLinks(False)
        self.body.anchorClicked.connect(self._follow)
        self.body.setFont(theme.ui_font(9))

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

    def _show(self) -> None:
        exercise = self.exercise
        done = exercise.key in self.progress.completed
        dots = "".join("●" if e.key in self.progress.completed else "○"
                       for e in self._course)
        self.header.setText(
            f"  Beginner {self._index + 1}/{len(self._course)}   {dots}")
        parts = [f"<h3>{exercise.title}{' ✓' if done else ''}</h3>",
                 f"<p><b>{exercise.goal}</b></p>"]
        if exercise.nodes:
            # Naming the search word is the difference between "add a node"
            # and a beginner staring at 1360 of them.
            rows = "".join(
                f"<li><b>Tab → “{term}”</b> — {purpose}</li>"
                for term, _type, purpose in exercise.nodes)
            parts.append(
                "<p style='color:#8fa4b0'>Nodes for this exercise:</p>"
                f"<ul style='color:#a8b4bc'>{rows}</ul>")
        parts.append("<p>" + exercise.steps.replace("\n\n", "</p><p>")
                     .replace("\n", "<br>") + "</p>")
        for shown in range(self._hints_shown):
            if shown < len(exercise.hints):
                parts.append(f"<p style='color:#b0a48f'>💡 "
                             f"{exercise.hints[shown]}</p>")
        if done and exercise.deeper:
            links = " · ".join(
                f"<a href='{url}'>{label}</a>" for label, url in exercise.deeper)
            parts.append(f"<p style='color:#8a8a8a'>Go deeper: {links}</p>")
        self.body.setHtml("".join(parts))
        self.verdict.setText("")
        # Always walkable. Locking the way forward until an exercise is
        # solved meant a student whose CORRECT graph was not recognised had
        # no way out at all - and browsing ahead is not cheating anyway.
        self.back_button.setEnabled(self._index > 0)
        self.next_button.setEnabled(self._index < len(self._course) - 1)
        self.back_button.setToolTip("Previous exercise")
        self.next_button.setToolTip("Next exercise")
        self.hint_button.setEnabled(self._hints_shown < len(exercise.hints))

    def _go(self, step: int) -> None:
        self._index = max(0, min(len(self._course) - 1, self._index + step))
        self._hints_shown = 0
        self._show()

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
            self._show()

    def _call_professor(self) -> None:
        exercise = self.exercise
        self.ask_professor.emit(
            f"I am on the exercise \"{exercise.title}\" — the goal is: "
            f"{exercise.goal} Explain what I am missing, step by step, "
            f"without giving me the finished VEX.")

    def _follow(self, url: QtCore.QUrl) -> None:
        if url.toString().startswith("manual"):
            self.open_manual.emit()
        else:
            QtGui.QDesktopServices.openUrl(url)
