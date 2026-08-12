"""The editor: canvas on the left, the VEX it produces on the right.

Regeneration is debounced rather than immediate. Emitting is fast enough to do
on every keystroke, but `vcc` is a process launch, so the two run on different
timers: the code appears as you work, and the compiler check follows a moment
after you stop.
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import help as vexhelp
from ..codegen import generate
from ..graph import ERROR, Graph, Issue
from ..nodedefs import Registry, default_registry
from ..vccmap import compile_check
from . import theme
from .assistant_panel import AssistantPanel
from .browser import NodeBrowser
from .canvas import GraphScene, GraphView
from .codeview import CodeView
from .palette import NodeSearch, search_for_port
from .snippet_picker import SnippetPicker

EMIT_DELAY_MS = 120
COMPILE_DELAY_MS = 700
# Snapshots are whole-graph dicts. Command objects would use less memory, but
# every command would have to get its inverse exactly right, and an undo that
# quietly corrupts the graph is worse than no undo. These graphs are small.
UNDO_DEPTH = 100


class History:
    """Undo/redo over whole-graph snapshots.

    Holds states, not edits: position `self.at` is the current one, everything
    before it is undo, everything after is redo.
    """

    def __init__(self, initial: dict):
        self.states: list[dict] = [initial]
        self.at = 0

    def record(self, state: dict) -> None:
        if state == self.states[self.at]:
            return                     # nothing actually changed
        # A new edit after undoing abandons the states that were ahead.
        del self.states[self.at + 1:]
        self.states.append(state)
        if len(self.states) > UNDO_DEPTH:
            self.states.pop(0)
        self.at = len(self.states) - 1

    def reset(self, state: dict) -> None:
        self.states = [state]
        self.at = 0

    @property
    def can_undo(self) -> bool:
        return self.at > 0

    @property
    def can_redo(self) -> bool:
        return self.at < len(self.states) - 1

    def undo(self) -> dict | None:
        if not self.can_undo:
            return None
        self.at -= 1
        return self.states[self.at]

    def redo(self) -> dict | None:
        if not self.can_redo:
            return None
        self.at += 1
        return self.states[self.at]


class VexGraphEditor(QtWidgets.QWidget):
    """The whole editor. Embeddable in a Houdini Python Panel or run alone."""

    applied = QtCore.Signal(str)      # the VEX, when the user asks to apply it

    def __init__(self, registry: Registry | None = None,
                 graph: Graph | None = None, parent=None):
        super().__init__(parent)
        self.registry = registry or default_registry()
        self.graph = graph or self._starter_graph()
        self.path: Path | None = None
        self._search: NodeSearch | None = None

        self.scene = GraphScene(self.graph, self)
        self.view = GraphView(self.scene)
        self.code = CodeView()
        self.browser = NodeBrowser(self.registry)
        # Set aside while a proposal is on screen, so Discard is a real undo.
        self._graph_before_proposal: Graph | None = None
        # The most recent emission, and the text last written to the wrangle.
        self._last_emission = None
        self._applied_code = ""

        self._build_ui()
        self._install_history()
        self._install_key_filter()
        self._connect()

        self._emit_timer = QtCore.QTimer(self, singleShot=True)
        self._emit_timer.timeout.connect(self._regenerate)
        self._compile_timer = QtCore.QTimer(self, singleShot=True)
        self._compile_timer.timeout.connect(self._compile)
        self._compile_timer.timeout.connect(self._auto_apply)

        self._regenerate()
        QtCore.QTimer.singleShot(0, self.view.frame_all)

    def _starter_graph(self) -> Graph:
        graph = Graph(self.registry)
        node = graph.add("start", "start")
        node.pos = (-260.0, 0.0)
        return graph

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{ background: {theme.PANEL_BG.name()};
                       color: {theme.PANEL_TEXT.name()}; }}
            QToolButton, QPushButton {{ background: #333333; border: none;
                       padding: 5px 11px; border-radius: 4px; }}
            QToolButton:hover, QPushButton:hover {{ background: #3f3f3f; }}
            QComboBox {{ background: #333333; border: none; padding: 4px 8px;
                         border-radius: 4px; }}
            QSplitter::handle {{ background: #1b1b1b; }}
            QLabel {{ color: {theme.PANEL_TEXT.name()}; }}
        """)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(6)
        for text, slot, tip in (
            ("Open", self.open_graph, "Open a .vexgraph.json"),
            ("Save", self.save_graph, "Save the graph"),
            ("Add Node", self.search_nodes, "Also: Tab, or double-click the canvas"),
            # Not only a shortcut: inside a Houdini panel a key press can be
            # taken by Houdini before this widget sees it, so deleting needs a
            # route that does not depend on one arriving.
            ("Delete Node", self.scene.delete_selected,
             "Delete the selected nodes.\nAlso: Delete key, or right-click a node"),
            # Buttons as well as shortcuts, because inside a docked pane the
            # shortcuts are Houdini's before they are ours.
            ("Undo", self.undo, "Undo the last change (Ctrl+Z)"),
            ("Redo", self.redo, "Redo (Ctrl+Y)"),
            ("Tidy", self.scene.tidy, "Lay the nodes out again"),
            ("Frame", self.view.frame_all, "Fit everything on screen (F)"),
        ):
            button = QtWidgets.QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            bar.addWidget(button)

        # Import VEX and Apply to Wrangle are the two ends of the same idea -
        # read VEX in, write VEX out - so they sit together rather than at
        # opposite ends of the bar.
        self.import_button = QtWidgets.QPushButton("Import VEX")
        self.import_button.setToolTip(
            "Paste VEX and see it as nodes.\n"
            "Anything that will not translate is kept verbatim.")
        self.import_button.clicked.connect(self.import_vex)
        bar.addWidget(self.import_button)

        self.snippets_button = QtWidgets.QPushButton("Snippets")
        self.snippets_button.setToolTip(
            "Ready-made VEX from the tools installed on this machine,\n"
            "opened as nodes so you can read and change it.")
        self.snippets_button.clicked.connect(self.open_snippets)
        bar.addWidget(self.snippets_button)

        self.apply_button = QtWidgets.QPushButton("Apply to Wrangle")
        self.apply_button.setToolTip(
            "Write the generated VEX into the wrangle's snippet")
        bar.addWidget(self.apply_button)

        self.auto_apply = QtWidgets.QCheckBox("Live")
        self.auto_apply.setChecked(True)
        self.auto_apply.setToolTip(
            "Write to the wrangle as you edit, so the viewport keeps up.\n"
            "Debounced, and a graph with errors is never written - the last\n"
            "working VEX stays until the new one is valid.")
        bar.addWidget(self.auto_apply)

        bar.addSpacing(12)
        bar.addWidget(QtWidgets.QLabel("Run over"))
        self.run_over = QtWidgets.QComboBox()
        self.run_over.addItems(["points", "primitives", "vertices", "detail",
                                "numbers"])
        self.run_over.setCurrentText(self.graph.run_over)
        bar.addWidget(self.run_over)

        bar.addSpacing(12)
        bar.addWidget(QtWidgets.QLabel("Text size"))
        self.text_size = QtWidgets.QComboBox()
        self.text_size.addItems(["90%", "100%", "115%", "130%", "150%"])
        self.text_size.setCurrentText(f"{round(theme.get_ui_scale() * 100)}%")
        self.text_size.setToolTip(
            "Scales node text, the code pane, the library and the assistant "
            "together.")
        bar.addWidget(self.text_size)

        # Where a host (the Houdini panel) puts its own controls, so there is
        # one row of buttons instead of a second strip above this one - which
        # in a short docked pane was enough to push the assistant off screen.
        bar.addSpacing(12)
        self.host_slot = QtWidgets.QHBoxLayout()
        self.host_slot.setContentsMargins(0, 0, 0, 0)
        self.host_slot.setSpacing(6)
        bar.addLayout(self.host_slot)

        bar.addStretch(1)

        self.status = QtWidgets.QLabel("")
        self.status.setFont(theme.ui_font(8))
        self.status.setContentsMargins(10, 4, 10, 4)

        self.help = QtWidgets.QLabel("")
        self.help.setWordWrap(True)
        self.help.setFont(theme.ui_font(8))
        self.help.setContentsMargins(10, 6, 10, 6)
        self.help.setMinimumHeight(0)
        self.help.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        self.issues = QtWidgets.QListWidget()
        self.issues.setFont(theme.ui_font(8))
        self.issues.setMaximumHeight(120)
        self.issues.setStyleSheet(
            f"QListWidget {{ background: {theme.CODE_BG.name()};"
            f" border: none; }} QListWidget::item {{ padding: 3px 8px; }}")

        # The right pane stacks the code and the assistant, so a request and
        # the VEX it produced are on screen together rather than in rival tabs.
        self.assistant = AssistantPanel(self.registry)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        code_side = QtWidgets.QWidget()
        code_layout = QtWidgets.QVBoxLayout(code_side)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(0)
        code_layout.addWidget(self._section_label("Generated VEX"))
        code_layout.addWidget(self.code, 1)
        code_layout.addWidget(self._section_label("Problems"))
        code_layout.addWidget(self.issues)
        code_layout.addWidget(self._section_label("About the selected node"))
        code_layout.addWidget(self.help)

        assistant_side = QtWidgets.QWidget()
        assistant_layout = QtWidgets.QVBoxLayout(assistant_side)
        assistant_layout.setContentsMargins(0, 0, 0, 0)
        assistant_layout.setSpacing(0)
        assistant_layout.addWidget(self._section_label("Ask for a graph"))
        assistant_layout.addWidget(self.assistant, 1)

        right.addWidget(code_side)
        right.addWidget(assistant_side)
        right.setSizes([520, 380])

        # Everything that can afford to be small must say so. A Python Panel is
        # given whatever height its pane has, and a widget whose minimum is
        # taller than that does not scroll - it is simply cut off, taking the
        # box you type in with it. Panels docked short are normal, so the
        # editor has to survive one.
        for shrinkable in (self.browser, self.code, self.issues, self.help,
                           self.assistant, code_side, assistant_side, right):
            shrinkable.setMinimumHeight(0)
            shrinkable.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                     QtWidgets.QSizePolicy.Policy.Ignored)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self.browser)
        splitter.addWidget(self.view)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([260, 780, 520])

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status)

    def _section_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setFont(theme.ui_font(8, bold=True))
        label.setContentsMargins(10, 7, 10, 5)
        label.setStyleSheet("color: #8a8a8a; background: #202020;")
        return label

    def _install_key_filter(self) -> None:
        """Catch our keys before Houdini's hotkey manager can eat them.

        Two earlier attempts - keyPressEvent on the view, then accepting
        ShortcutOverride - both work standalone and both lose to Houdini, which
        filters key events at the application level. The only thing that
        reliably wins is another application-level filter installed *after*
        Houdini's, because Qt runs the most recently installed one first.

        Scoped so it cannot misbehave: it acts only when the focused widget is
        inside this editor, and only on keys the canvas actually uses.
        """
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() not in (QtCore.QEvent.Type.KeyPress,
                                QtCore.QEvent.Type.ShortcutOverride):
            return super().eventFilter(watched, event)

        focus = QtWidgets.QApplication.focusWidget()
        if focus is None or not self.isAncestorOf(focus):
            return super().eventFilter(watched, event)

        # Typing into a text field claims *everything*, not just the printable
        # keys. Houdini binds the arrows to frame stepping and Home/End to the
        # frame range, so without this an attempt to move the cursor through a
        # word scrubs the timeline instead. Letting the widget have the event
        # and stopping it there is the whole fix.
        if self.scene.is_editing or isinstance(
                focus, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit,
                        QtWidgets.QTextEdit, QtWidgets.QAbstractSpinBox)):
            if event.type() == QtCore.QEvent.Type.ShortcutOverride:
                event.accept()
                return True          # "this key is mine", to Houdini
            return super().eventFilter(watched, event)

        key, modifiers = event.key(), event.modifiers()
        control = modifiers & QtCore.Qt.KeyboardModifier.ControlModifier
        action = None
        if key in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            action = self.scene.delete_selected
        elif control and key == QtCore.Qt.Key.Key_Z:
            action = self.redo if modifiers & (
                QtCore.Qt.KeyboardModifier.ShiftModifier) else self.undo
        elif control and key == QtCore.Qt.Key.Key_Y:
            action = self.redo

        if action is None:
            return super().eventFilter(watched, event)
        if event.type() == QtCore.QEvent.Type.KeyPress:
            action()
        event.accept()
        return True          # consumed: Houdini never sees it

    def _install_history(self) -> None:
        self.history = History(self.graph.to_dict())
        self._restoring = False
        for keys, slot in ((QtGui.QKeySequence.StandardKey.Undo, self.undo),
                           (QtGui.QKeySequence.StandardKey.Redo, self.redo),
                           ("Ctrl+Y", self.redo)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(keys), self)
            # The canvas, the code pane and the assistant are all children of
            # this widget, so the shortcut has to cover the window rather than
            # just whichever one happens to hold focus.
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    def closeEvent(self, event) -> None:
        # An application-wide filter that outlives its widget is a dangling
        # pointer waiting to be called; Houdini closing the panel must take it
        # with it.
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def open_snippets(self) -> None:
        picker = SnippetPicker(self.registry, self)
        picker.chosen.connect(self._take_snippet)
        picker.exec()

    def _take_snippet(self, graph: Graph, description: str) -> None:
        self.set_graph(graph)
        self._show_message(description)

    def open_help_for(self, node_type: str) -> None:
        """Open Houdini's page for a node, or say why there is not one.

        Not everything is documented, and that is not a gap in this tool: a
        node built from an operator (`!`, `+`) or a language construct (if,
        for) has no function page to open, and some functions vcc exposes are
        simply absent from the help.
        """
        definition = self.registry.get(node_type)
        if definition is None:
            return
        doc = vexhelp.page(definition.vex_function)
        if doc is not None:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(doc.url))
            self._show_message(f"Opened Houdini's help for {doc.name}().")
            return

        reason = ("it is built from an operator or a language construct rather "
                  "than a VEX function"
                  if not definition.vex_function else
                  f"Houdini ships no page for {definition.vex_function}()")
        QtWidgets.QMessageBox.information(
            self, "No Houdini help",
            f"{definition.label} has no page on sidefx.com — {reason}.\n\n"
            f"Not every node maps to a documented function: operators, loops "
            f"and branches are part of the VEX language itself, and a few "
            f"functions the compiler knows are undocumented.")

    def _record_history(self) -> None:
        if not self._restoring:
            self.history.record(self.graph.to_dict())

    def undo(self) -> None:
        self._restore(self.history.undo(), "Nothing left to undo.")

    def redo(self) -> None:
        self._restore(self.history.redo(), "Nothing to redo.")

    def _restore(self, state: dict | None, nothing: str) -> None:
        if state is None:
            self._show_message(nothing)
            return
        self._restoring = True
        try:
            self.graph = Graph.from_dict(state, self.registry)
            self.scene.graph = self.graph
            self.scene.reload()
            self.run_over.setCurrentText(self.graph.run_over)
            self.assistant.set_graph(self.graph)
            self._regenerate()
        finally:
            self._restoring = False

    def _connect(self) -> None:
        self.scene.graph_changed.connect(self._record_history)
        self.scene.graph_changed.connect(self._schedule)
        self.scene.message.connect(self._show_message)
        self.scene.selection_described.connect(self.help.setText)
        self.scene.selectionChanged.connect(self._sync_highlight)
        self.view.node_search_requested.connect(self._search_at)
        self.view.help_requested.connect(self.open_help_for)
        self.code.line_clicked.connect(self._select_from_code)
        self.issues.itemClicked.connect(self._select_from_issue)
        self.apply_button.clicked.connect(self._apply)
        self.run_over.currentTextChanged.connect(self._set_run_over)
        self.text_size.currentTextChanged.connect(self._set_text_size)
        self.browser.node_chosen.connect(self._place_from_browser)
        self.assistant.proposed.connect(self._propose)
        self.assistant.kept.connect(self._keep_proposal)
        self.assistant.discarded.connect(self._discard_proposal)
        self.assistant.set_graph(self.graph)

    # -------------------------------------------------------------- assistant

    def _place_from_browser(self, node_type: str) -> None:
        """Double-clicking the library drops the node in the middle of the view."""
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_node(node_type, centre)

    def _propose(self, graph: Graph, code: str, notes: str) -> None:
        """Show a proposed graph without committing to it."""
        self._graph_before_proposal = Graph.from_dict(self.graph.to_dict(),
                                                      self.registry)
        self.set_graph(graph)
        self._show_message(notes or "Proposed graph — Keep or Discard.")

    def _keep_proposal(self) -> None:
        self._graph_before_proposal = None
        self._show_message("Kept. Press Apply to Wrangle to write it out.")

    def _discard_proposal(self) -> None:
        if self._graph_before_proposal is not None:
            self.set_graph(self._graph_before_proposal)
            self._graph_before_proposal = None
        self._show_message("Discarded.")

    # -------------------------------------------------------------- pipeline

    def _schedule(self) -> None:
        self._emit_timer.start(EMIT_DELAY_MS)
        self._compile_timer.start(COMPILE_DELAY_MS)

    def _regenerate(self) -> None:
        emission = generate(self.graph)
        # Kept so the live apply and the compile check do not each generate the
        # same text again a moment later.
        self._last_emission = emission
        self.code.set_code(emission.code, emission.line_nodes)
        self._report(emission.issues)
        self._sync_highlight()

    def _compile(self) -> None:
        emission = self._last_emission or generate(self.graph)
        if not emission.ok:
            return                              # its own errors come first
        result = compile_check(emission, self.graph)
        if result.skipped:
            self._show_message(result.skipped)
            return
        if result.ok:
            self._show_message("vcc: compiles")
        else:
            self._report(list(emission.issues) + list(result.issues))

    def _report(self, issues: list[Issue]) -> None:
        self.scene.show_issues(issues)
        self.issues.clear()
        for issue in issues:
            item = QtWidgets.QListWidgetItem(issue.message)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, issue.node_id)
            item.setForeground(QtGui.QBrush(
                theme.NODE_ERROR if issue.severity == ERROR
                else theme.NODE_WARNING))
            self.issues.addItem(item)
        if not issues:
            placeholder = QtWidgets.QListWidgetItem("Nothing wrong.")
            placeholder.setForeground(QtGui.QBrush(QtGui.QColor("#6a8a6a")))
            self.issues.addItem(placeholder)

    def _show_message(self, text: str) -> None:
        self.status.setText(text)
        colour = "#e05a5a" if text and "compiles" not in text else "#7a9a7a"
        self.status.setStyleSheet(f"color: {colour};")

    # ------------------------------------------------------------ selection

    def _sync_highlight(self) -> None:
        # selectionChanged can still arrive while the panel is being torn down,
        # after Qt has destroyed the scene underneath the Python wrapper.
        try:
            node_id = self.scene.selected_node_id()
        except RuntimeError:
            return
        self.code.highlight_node(node_id)

    def _select_from_code(self, line_no: int) -> None:
        node_id = self.code.node_at_line(line_no)
        if node_id:
            self.scene.select_node(node_id)

    def _select_from_issue(self, item: QtWidgets.QListWidgetItem) -> None:
        node_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if node_id:
            self.scene.select_node(node_id)

    # --------------------------------------------------------------- actions

    def _set_run_over(self, value: str) -> None:
        self.graph.run_over = value
        self._schedule()

    def _set_text_size(self, value: str) -> None:
        theme.set_ui_scale(int(value.rstrip("%")) / 100)
        self.scene.apply_font_scale()
        self.code.refresh_fonts()
        self.browser.refresh_fonts()
        self.assistant.refresh_fonts()
        for label in (self.status, self.help, self.issues):
            label.setFont(theme.ui_font(8))
        self.view.viewport().update()

    def search_nodes(self) -> None:
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        self._search_at(centre, None)

    def _search_at(self, scene_pos: QtCore.QPointF, port=None) -> None:
        dialog = (search_for_port(self.registry, self.graph, port) if port
                  else NodeSearch(self.registry))
        self._search = dialog

        def place(node_type: str) -> None:
            item = self.scene.add_node(node_type, scene_pos)
            if item is not None and port is not None:
                self._auto_connect(port, item)

        dialog.chosen.connect(place)
        dialog.popup_at(QtGui.QCursor.pos())

    def _auto_connect(self, port, item) -> None:
        """Join the dragged wire to the first socket on the new node that fits."""
        for (name, is_input), candidate in item.ports.items():
            if is_input == port.is_input:
                continue
            if not self.scene.connect_ports(port, candidate):
                continue
            return

    def import_vex(self) -> None:
        """Paste VEX, see the nodes. The reverse direction, for learning."""
        from ..parser import import_vex as run_import  # noqa: PLC0415

        source, accepted = _ask_for_vex(self)
        if not accepted or not source.strip():
            return
        report = run_import(source, self.registry)
        self.set_graph(report.graph)
        self._show_message(report.summary())
        if report.reasons:
            unique = list(dict.fromkeys(report.reasons))[:4]
            detail = "\n".join(f"- {reason}" for reason in unique)
            QtWidgets.QMessageBox.information(
                self, "Imported, with some code kept as-is",
                f"{report.summary()}\n\n{detail}\n\n"
                f"Those parts are in Inline VEX nodes, unchanged.")

    def open_graph(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open graph", "", "VEXgraph (*.json)")
        if not path:
            return
        try:
            self.graph = Graph.load(Path(path), self.registry)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Could not open", str(exc))
            return
        self.path = Path(path)
        self._rebind()

    def save_graph(self) -> None:
        path = self.path
        if path is None:
            chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save graph", "untitled.vexgraph.json",
                "VEXgraph (*.json)")
            if not chosen:
                return
            path = Path(chosen)
        self.graph.save(path)
        self.path = path
        self._show_message(f"Saved {path.name}")

    def set_graph(self, graph: Graph) -> None:
        self.graph = graph
        self._rebind()

    def _rebind(self) -> None:
        # Replacing the whole graph (opening a file, importing VEX, keeping a
        # proposal) starts a new history rather than appending to the old one:
        # undoing across that boundary would resurrect a graph the user has
        # already moved on from.
        self.history.reset(self.graph.to_dict())
        self._last_emission = None
        self._applied_code = ""
        self.scene.graph = self.graph
        self.scene.reload()
        self.run_over.setCurrentText(self.graph.run_over)
        # The assistant needs the current graph to answer "change this" requests.
        self.assistant.set_graph(self.graph)
        self._regenerate()
        self.view.frame_all()

    def _apply(self) -> None:
        emission = generate(self.graph)
        if not emission.ok:
            QtWidgets.QMessageBox.warning(
                self, "Not applied",
                "Fix the problems listed on the right first.")
            return
        self._applied_code = emission.code
        self.applied.emit(emission.code)
        self._show_message("Applied to the wrangle.")

    def _auto_apply(self) -> None:
        """Push the VEX to the wrangle as the graph changes.

        Three things keep this cheap, because a wrangle recooks everything
        downstream of it and that is the expensive part, not the codegen:

        - it runs off the debounce, not off every change;
        - it reuses the emission the code pane just produced instead of
          generating again;
        - and above all it does nothing when the VEX is byte-for-byte what the
          wrangle already has. Moving a node, selecting one, or opening a menu
          all fire graph_changed without altering a character of output, and
          re-setting the parm to the same string still triggers a full recook.
        """
        if not self.auto_apply.isChecked():
            return
        emission = self._last_emission or generate(self.graph)
        if not emission.ok or emission.code == self._applied_code:
            return
        self._applied_code = emission.code
        self.applied.emit(emission.code)


def _ask_for_vex(parent) -> tuple[str, bool]:
    """A paste box. Bigger than QInputDialog gives, because VEX has newlines."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Import VEX")
    dialog.resize(680, 420)

    editor = QtWidgets.QPlainTextEdit()
    editor.setFont(theme.mono_font(9))
    editor.setPlaceholderText("Paste a wrangle snippet here.")
    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok
        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(
        "Anything that cannot be turned into nodes is kept verbatim."))
    layout.addWidget(editor, 1)
    layout.addWidget(buttons)

    accepted = dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    return editor.toPlainText(), accepted


def run_standalone(graph_path: str = "") -> int:
    """Open the editor outside Houdini, which is how it gets developed."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    registry = default_registry()
    graph = Graph.load(Path(graph_path), registry) if graph_path else None
    editor = VexGraphEditor(registry, graph)
    editor.resize(1500, 900)
    editor.setWindowTitle("VEXgraph")
    editor.show()
    return app.exec()
