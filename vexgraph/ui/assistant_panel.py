"""Describe what you want; get a graph you can look at before you keep it.

The result is never applied silently. The assistant proposes, the canvas shows
what it proposed, the code pane shows the VEX that came out of it, and only then
does the user press Keep. That ordering is the whole safety story for someone
who cannot audit the VEX: they audit the *graph*, which is readable.

The call runs on a worker thread — a Claude request with the full catalogue
takes seconds, a local model can take minutes, and neither may freeze Houdini.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from ..assistant import vram
# By name, not the module: the assistant package also exports a
# function called `providers`, which shadows it on import.
from ..assistant.providers import (CLAUDE_MODELS, describe_local_model,
                                   installed_local_models,
                                   local_model_advice)
from ..graph import Graph
from ..nodedefs import Registry
from . import theme

# Short enough to fit the box at every text size. The longer version was clipped
# mid-sentence, which reads as a bug rather than a hint.
PLACEHOLDER = ('Describe what you want the graph to do — e.g. "stick every '
               'point to the closest spot on the second input"')


def _clean_python_env() -> dict:
    """The environment for the worker process, with Houdini's Python removed.

    Houdini sets PYTHONHOME to its own interpreter (3.11). A subprocess
    inherits it, so launching this project's Python (a different version)
    makes it load Houdini's standard library instead of its own, and it dies
    on the spot with "SRE module mismatch" - the C extension and the stdlib
    are from different Pythons.

    Everything else is kept deliberately: PATH, and above all
    ANTHROPIC_API_KEY, which is how the worker authenticates.
    """
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
                 "PYTHONEXECUTABLE", "PYTHONNOUSERSITE"):
        env.pop(name, None)
    return env


class AssistantWorker(QtCore.QThread):
    """One request, off the UI thread.

    Runs in-process when the Anthropic SDK is importable, and in a subprocess
    using this project's own interpreter when it is not — which is the case
    inside Houdini, whose embedded Python has neither the SDK nor a compatible
    ABI for its compiled dependencies. Vendoring would mean one copy per
    Houdini release; a subprocess needs none.
    """

    finished_ok = QtCore.Signal(dict)
    failed = QtCore.Signal(list)

    def __init__(self, request: dict, parent=None):
        super().__init__(parent)
        self.request = request

    def run(self) -> None:
        try:
            result = self._in_process() if self._importable() else self._subprocess()
        except Exception as exc:                    # noqa: BLE001 - thread guard
            self.failed.emit([f"{type(exc).__name__}: {exc}"])
            return
        if result.get("ok"):
            self.finished_ok.emit(result)
        else:
            self.failed.emit(result.get("problems") or ["The request failed."])

    def _importable(self) -> bool:
        if self.request.get("provider") != "Claude":
            return True                     # the local provider is stdlib only
        try:
            import anthropic  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def _in_process(self) -> dict:
        from ..assistant.worker import run  # noqa: PLC0415

        return run(self.request)

    def _subprocess(self) -> dict:
        interpreter = self._project_python()
        if interpreter is None:
            return {"ok": False, "problems": [
                "The Anthropic SDK is not available to Houdini's Python, and "
                "VEXgraph's own virtual environment was not found. Create it "
                "with:  python -m venv .venv  then  .venv/Scripts/pip install "
                "anthropic"]}
        root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [str(interpreter), "-m", "vexgraph.assistant.worker"],
            input=json.dumps(self.request), capture_output=True, text=True,
            cwd=str(root), timeout=1800, env=_clean_python_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            return json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"ok": False, "problems": [
                completed.stderr.strip()
                or "The assistant process returned nothing."]}

    @staticmethod
    def _project_python() -> Path | None:
        root = Path(__file__).resolve().parents[2]
        for candidate in (root / ".venv" / "Scripts" / "python.exe",
                          root / ".venv" / "bin" / "python"):
            if candidate.is_file():
                return candidate
        return None


def ask_for_api_key(parent) -> bool:
    """Ask for an Anthropic key and put it in this process's environment.

    Refusing to prompt was a defensible rule that turned out to be a dead end:
    the panel said what was missing and then offered no way to supply it. What
    the rule was really protecting against is a key written into the project -
    which now matters more, not less, since the repository is public. So the key
    goes into this process only, unless the box is ticked, and even then it goes
    to the Windows user environment (`setx`) rather than any file here.
    """
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Anthropic API key")
    dialog.setMinimumWidth(460)

    field = QtWidgets.QLineEdit()
    field.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
    field.setPlaceholderText("sk-ant-...")
    remember = QtWidgets.QCheckBox(
        "Remember on this machine (saves it to your Windows user environment)")

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok
        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(
        "Paste your key from console.anthropic.com.\n"
        "It is used by this Houdini session only unless you tick the box.\n"
        "VEXgraph never writes it into the project."))
    layout.addWidget(field)
    layout.addWidget(remember)
    layout.addWidget(buttons)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return False
    key = field.text().strip()
    if not key:
        return False

    os.environ["ANTHROPIC_API_KEY"] = key
    if remember.isChecked():
        try:
            # setx writes the user's environment. Same thing you would do by
            # hand; nothing about it is specific to this project.
            subprocess.run(["setx", "ANTHROPIC_API_KEY", key], check=True,
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass          # the session still has it; saving is a convenience
    return True


class VramProbe(QtCore.QThread):
    """Reads GPU and Ollama state off the UI thread.

    Both readings block - one spawns nvidia-smi, the other waits on a socket -
    and a few hundred milliseconds of that on the UI thread is a visible stall
    in Houdini, repeating every few seconds.
    """

    done = QtCore.Signal(str, bool, bool)      # text, a model is loaded, has GPU

    def run(self) -> None:
        usage = vram.gpu_usage()
        if usage is None:
            self.done.emit("", False, False)
            return
        loaded = vram.loaded_models()
        text = str(usage)
        if loaded:
            held = sum(size for _, size in loaded) / (1024 ** 3)
            text += f" — {loaded[0][0]} holding {held:.1f} GB"
        self.done.emit(text, bool(loaded), True)


class AssistantPanel(QtWidgets.QWidget):
    """The chat pane. Proposes a graph; the editor decides what to do with it."""

    proposed = QtCore.Signal(object, str, str)      # graph, code, notes
    kept = QtCore.Signal()
    discarded = QtCore.Signal()

    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.worker: AssistantWorker | None = None
        self._current_graph: Graph | None = None

        self._last_request = ""

        self.provider = QtWidgets.QComboBox()
        self.provider.addItems(["Claude", "Local"])
        self.provider.setToolTip(
            "Claude is markedly better at picking the right nodes.\n"
            "Local runs on this machine and costs nothing, but is slower\n"
            "and more likely to wire something plausible but wrong.")
        self.provider.currentTextChanged.connect(self._fill_models)

        self.model = QtWidgets.QComboBox()
        self.model.setToolTip("Which model answers.")
        self._fill_models(self.provider.currentText())

        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["Build a graph", "Just answer"])
        self.mode.setToolTip(
            "Build a graph proposes nodes you can keep or discard.\n"
            "Just answer explains which nodes to use, changing nothing.")

        self.input = QtWidgets.QPlainTextEdit()
        self.input.setPlaceholderText(PLACEHOLDER)
        self.input.setFont(theme.ui_font(9))
        self.input.setMaximumHeight(92)
        # Small enough to survive a cramped pane, big enough to type into.
        self.input.setMinimumHeight(40)
        # Inside a Houdini pane the first click is spent activating the pane,
        # and Houdini moves focus itself while doing so - after the click has
        # been delivered. Taking focus again on the next event-loop turn is
        # what makes one click enough, instead of needing a second.
        self.input.installEventFilter(self)

        self.send = QtWidgets.QPushButton("Ask")
        self.stop = QtWidgets.QPushButton("Stop")
        self.stop.setEnabled(False)
        self.keep = QtWidgets.QPushButton("Keep This Graph")
        self.discard = QtWidgets.QPushButton("Discard")
        for button in (self.keep, self.discard):
            button.setEnabled(False)

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(8, 6, 8, 0)
        top.addWidget(self.provider)
        top.addWidget(self.model, 1)
        top.addWidget(self.mode)

        # A local model sits in VRAM after answering, which matters on a machine
        # that is also rendering. The reading is of the whole card, not just
        # Ollama, because that is the number you actually care about.
        self.vram = QtWidgets.QLabel("")
        self.vram.setFont(theme.ui_font(8))
        self.vram.setStyleSheet("color: #8a8a8a;")
        self.release = QtWidgets.QPushButton("Release model")
        self.release.setToolTip(
            "Unload the local model from the GPU. Only the model is unloaded - "
            "Ollama keeps running and nothing else is touched. The next "
            "question loads it again.")
        self.release.clicked.connect(self._release_model)

        gpu = QtWidgets.QHBoxLayout()
        gpu.setContentsMargins(8, 0, 8, 0)
        gpu.addWidget(self.vram, 1)
        gpu.addWidget(self.release)

        # Both readings shell out or hit the network, so they are taken off the
        # UI thread. Doing this inline froze the panel for as long as
        # nvidia-smi took, every few seconds, which inside Houdini is a stutter
        # in the whole application.
        self._vram_probe: VramProbe | None = None
        self._vram_timer = QtCore.QTimer(self)
        self._vram_timer.timeout.connect(self._refresh_vram)
        self._vram_timer.start(5000)
        QtCore.QTimer.singleShot(0, self._refresh_vram)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(8, 0, 8, 6)
        buttons.addWidget(self.send)
        buttons.addWidget(self.stop)
        buttons.addStretch(1)
        buttons.addWidget(self.discard)
        buttons.addWidget(self.keep)

        self.log = QtWidgets.QTextBrowser()
        self.log.setFont(theme.ui_font(8))
        self.log.setOpenExternalLinks(False)
        # The transcript is the part that can afford to disappear. Without this
        # it kept its own minimum and the layout pushed the box you type in -
        # and the Ask button - past the bottom edge of a short docked pane,
        # where they could not be clicked at all.
        self.log.setMinimumHeight(0)
        self.log.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                               QtWidgets.QSizePolicy.Policy.Ignored)

        # Transcript on top, compose box underneath, the way every chat window
        # works: the newest message is nearest the thing you type into, so
        # your eye does not have to travel back up after sending.
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self.log, 1)
        layout.addWidget(self.input)
        layout.addLayout(gpu)
        layout.addLayout(buttons)

        self.setStyleSheet(f"""
            QPlainTextEdit, QTextBrowser {{
                background: {theme.CODE_BG.name()};
                color: {theme.PANEL_TEXT.name()};
                border: none; padding: 8px; }}
            QPushButton {{ background: #333333; border: none;
                           padding: 5px 11px; border-radius: 4px; }}
            QPushButton:hover:enabled {{ background: #3f3f3f; }}
            QPushButton:disabled {{ color: #6a6a6a; }}
            QComboBox {{ background: #333333; border: none; padding: 4px 8px;
                         border-radius: 4px; }}
        """)

        self.send.clicked.connect(self.ask)
        self.stop.clicked.connect(self.cancel)
        self.keep.clicked.connect(self._keep)
        self.discard.clicked.connect(self._discard)
        self._say("Ask for a graph in plain language. Nothing reaches the "
                  "wrangle until you press Keep.", "#8a8a8a")

    # ------------------------------------------------------------------ chat

    def set_graph(self, graph: Graph) -> None:
        """The editor tells us what is on the canvas, so requests can modify it."""
        self._current_graph = graph

    def _fill_models(self, provider: str) -> None:
        """The model menu follows the provider, and offers only real choices.

        The local list is whatever Ollama has actually pulled: offering a model
        that is not installed only produces a failure several seconds later.
        """
        self.model.clear()
        if provider == "Claude":
            for name, label in CLAUDE_MODELS:
                self.model.addItem(label, name)
            return
        installed = installed_local_models()
        total = vram.gpu_usage()
        total_gb = (total.total_mb / 1024) if total else 0.0
        for model in installed:
            self.model.addItem(describe_local_model(model), model["name"])
            self.model.setItemData(
                self.model.count() - 1,
                local_model_advice(model, total_gb),
                QtCore.Qt.ItemDataRole.ToolTipRole)
        if not installed:
            self.model.addItem("No local models found", "")
            self.model.setToolTip(
                "Ollama is not running, or has no models pulled.\n"
                "Install one with:  ollama pull qwen3:32b")

    def refresh_fonts(self) -> None:
        self.input.setFont(theme.ui_font(9))
        self.log.setFont(theme.ui_font(8))

    def ask(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or (self.worker is not None and self.worker.isRunning()):
            return

        explaining = self.mode.currentText().startswith("Just")
        self._say(f"<b>You</b><br>{html.escape(text)}", "#d0d0d0")
        self._busy(True)

        has_graph = (self._current_graph is not None
                     and len(self._current_graph.nodes) > 1)
        # Kept so a request can be sent again after supplying a key, without
        # making the user type it a second time.
        self._last_request = text
        self.worker = AssistantWorker({
            "provider": self.provider.currentText(),
            "model": self.model.currentData(),
            "mode": "explain" if explaining else "build",
            "text": text,
            "graph": self._current_graph.to_dict() if has_graph else None,
        }, self)
        self.worker.finished_ok.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(lambda: self._busy(False))
        self.worker.start()
        self.input.clear()

    def cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()
            self._say("Stopped.", "#d9a441")
        self._busy(False)

    def _busy(self, running: bool) -> None:
        self.send.setEnabled(not running)
        self.stop.setEnabled(running)
        if running:
            self._say(f"<i>Asking {self.provider.currentText()}…</i>", "#7a9a7a")

    def _done(self, result: dict) -> None:
        if "answer" in result:
            self._say(f"<b>Answer</b><br>{html.escape(result['answer'])}",
                      "#c8d8c8")
            return

        try:
            graph = Graph.from_dict(result["graph"], self.registry)
        except Exception as exc:                    # noqa: BLE001
            self._failed([f"The proposed graph could not be loaded: {exc}"])
            return

        tries = result.get("tries", 1)
        retried = f" after {tries} tries" if tries > 1 else ""
        self._say(f"<b>{result.get('provider', 'Assistant')}</b><br>"
                  f"{html.escape(result.get('notes', ''))}<br>"
                  f"<i style='color:#7a9a7a'>Compiles{retried}. "
                  f"{len(graph.nodes)} nodes.</i>", "#c8d8c8")
        self._say("<i style='color:#8a8a8a'>Shown on the canvas. Read the graph "
                  "and the VEX, then Keep or Discard.</i>", "#8a8a8a")
        self.keep.setEnabled(True)
        self.discard.setEnabled(True)
        self.proposed.emit(graph, result.get("code", ""),
                           result.get("notes", ""))

    def _failed(self, problems: list[str]) -> None:
        # A missing key is not a failure to report, it is a question to ask.
        # Saying "set it and restart Houdini" while offering no way to set it
        # left the only path to Claude closed from inside the tool.
        if any("ANTHROPIC_API_KEY" in p for p in problems):
            if ask_for_api_key(self) and self._last_request:
                self._say("<i>Key set for this session. Asking again…</i>",
                          "#7a9a7a")
                self.input.setPlainText(self._last_request)
                self.ask()
                return
        self._say("<b>That did not work</b><br>"
                  + "<br>".join(html.escape(p) for p in problems[:6]), "#e05a5a")

    def _keep(self) -> None:
        self._settle()
        self.kept.emit()
        self._say("<i style='color:#7a9a7a'>Kept.</i>", "#7a9a7a")

    def _discard(self) -> None:
        self._settle()
        self.discarded.emit()
        self._say("<i style='color:#8a8a8a'>Discarded; your graph is back.</i>",
                  "#8a8a8a")

    def _settle(self) -> None:
        self.keep.setEnabled(False)
        self.discard.setEnabled(False)

    def eventFilter(self, watched, event) -> bool:
        if (watched is self.input
                and event.type() == QtCore.QEvent.Type.MouseButtonPress):
            QtCore.QTimer.singleShot(0, self._claim_input_focus)
        return super().eventFilter(watched, event)

    def _claim_input_focus(self) -> None:
        window = self.window()
        if window is not None:
            window.activateWindow()
        self.input.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)

    def _refresh_vram(self) -> None:
        if self._vram_probe is not None or not self.isVisible():
            return          # one at a time, and never while nobody is looking
        self._vram_probe = VramProbe()
        self._vram_probe.done.connect(self._show_vram)
        self._vram_probe.finished.connect(self._probe_finished)
        self._vram_probe.start()

    def _probe_finished(self) -> None:
        self._vram_probe = None

    def closeEvent(self, event) -> None:
        # A QThread still running when its Python wrapper is collected takes the
        # process down. Stop polling and wait for the one in flight; it is
        # bounded by its own timeouts, so this cannot hang.
        self._vram_timer.stop()
        probe, self._vram_probe = self._vram_probe, None
        if probe is not None and probe.isRunning():
            probe.wait(6000)
        super().closeEvent(event)

    def _show_vram(self, text: str, has_model: bool, has_gpu: bool) -> None:
        if not has_gpu:
            self.vram.setText("")
            self.release.setVisible(False)
            self._vram_timer.stop()      # no NVIDIA card; nothing to report ever
            return
        self.vram.setText(text)
        self.release.setEnabled(has_model)

    def _release_model(self) -> None:
        self._say(f"<i>{vram.release()}</i>", "#8a8a8a")
        self._refresh_vram()

    def _say(self, message: str, colour: str) -> None:
        self.log.append(
            f"<div style='color:{colour}; margin-bottom:8px'>{message}</div>")
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())
