"""The editor: canvas on the left, the VEX it produces on the right.

Regeneration is debounced rather than immediate. Emitting is fast enough to do
on every keystroke, but `vcc` is a process launch, so the two run on different
timers: the code appears as you work, and the compiler check follows a moment
after you stop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import help as vexhelp
from .. import snippets
from .. import version_line
from ..codegen import generate
from ..graph import ERROR, Graph, Issue
from ..nodedefs import Registry, default_registry
from ..vccmap import compile_check
from . import settings, theme
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


# Qt's "no maximum", which PySide does not expose as a constant.
QWIDGETSIZE_MAX = (1 << 24) - 1


# Sections whose folded state is never remembered: they open only when asked
# for, every session. The course is a side room, not part of the tool's core
# job, and a panel that opens teaching material at you is a panel that has an
# opinion about why you came.
ALWAYS_FOLDED_AT_START = ("learn",)


def _default_folded(key: str) -> bool:
    """How a section starts the very first time; a saved choice usually wins.

    Inside Houdini the wrangle already shows the applied VEX and the screen
    is scarce, so the code pane starts folded there: a clean canvas first,
    the teacher one click away. Standalone there is no wrangle - the pane is
    the only window onto the code - so it starts open.
    """
    return key == "code" and "hou" in sys.modules


class SectionHeader(QtWidgets.QLabel):
    """A section heading that reports being clicked.

    A plain QLabel would do if PySide reliably honoured an event handler
    assigned onto an instance; it does not, so the one virtual this needs is
    overridden the ordinary way.
    """

    clicked = QtCore.Signal()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


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
    # A Learn exercise asking its host to build the geometry it wants. Only a
    # host that owns a scene (the Houdini panel) can answer it.
    scene_requested = QtCore.Signal(object)

    def __init__(self, registry: Registry | None = None,
                 graph: Graph | None = None, parent=None):
        super().__init__(parent)
        self.registry = registry or default_registry()
        self.graph = graph or self._starter_graph()
        self.path: Path | None = None
        self._search: NodeSearch | None = None
        # Name of the user-defined function whose graph the canvas currently
        # shows; "" means the main graph.
        self._open_function = ""
        self._section_state: dict[str, dict] = {}
        self._section_toggles: dict[str, object] = {}
        # Learn's focus mode, when the course is open: the node types the
        # library and Tab search are narrowed to. None means everything.
        self._node_focus: set[str] | None = None
        # Which sections were folded away last time. Small enough to belong in
        # the platform's own settings rather than a file of our own.
        self._settings = settings.store()

        self.scene = GraphScene(self.graph, self)
        self.view = GraphView(self.scene)
        self.code = CodeView()
        self.browser = NodeBrowser(self.registry)
        # Set aside while a proposal is on screen, so Discard is a real undo.
        self._graph_before_proposal: Graph | None = None
        # The most recent emission, and the text last written to the wrangle.
        self._last_emission = None
        self._applied_code = ""
        # True once the code pane holds something a person typed rather than
        # something the emitter produced. While it is, the emitter keeps off.
        self._code_is_user_written = False

        from . import prefs                                     # noqa: PLC0415
        prefs.load()
        self._build_ui()
        self._install_history()
        self._install_key_filter()
        self._connect()

        self._emit_timer = QtCore.QTimer(self, singleShot=True)
        self._emit_timer.timeout.connect(self._regenerate)
        self._compile_timer = QtCore.QTimer(self, singleShot=True)
        self._compile_timer.timeout.connect(self._compile)
        self._compile_timer.timeout.connect(self._auto_apply)

        self.code.set_vocabulary(self._vex_vocabulary())
        self._regenerate()
        QtCore.QTimer.singleShot(0, self.view.frame_all)
        # Kept current in the background: it reads a few files and writes one,
        # which is too slow to do while the window is trying to appear and far
        # too cheap to make anybody ask for it.
        QtCore.QTimer.singleShot(400, self._export_wrangle_menu)

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
            QMenu {{ background: #2b2b2b; border: 1px solid #454545;
                     padding: 4px; }}
            QMenu::item {{ padding: 5px 22px; border-radius: 3px; }}
            QMenu::item:selected {{ background: #4a6fa5; color: #ffffff; }}
            QMenu::item:disabled {{ color: #6a6a6a; }}
            QMenu::separator {{ height: 1px; background: #454545;
                                margin: 4px 6px; }}
            {theme.scrollbar_qss()}
        """)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(6)
        # Add Node, Delete Node, Undo and Redo used to be here. They existed
        # because a docked panel loses its keys to Houdini, so none of Tab,
        # Delete, Ctrl+Z or Ctrl+Y could be relied on to arrive. Opening in a
        # real window fixed that, and four buttons duplicating four working
        # shortcuts is just a narrower canvas.
        for text, slot, tip in (
            ("Open", self.open_graph, "Open a .vexgraph.json"),
            ("Save", self.save_graph, "Save the graph"),
            ("Tidy", self.scene.tidy, "Lay the nodes out again"),
            ("Frame", self.view.frame_all, "Fit everything on screen (F)"),
        ):
            button = QtWidgets.QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            bar.addWidget(button)

        # Import VEX used to be here. It opened a box to paste VEX into and
        # built a graph from it - which is now what the code pane on the right
        # does, through the same importer, with the code already in front of
        # you. Two doors onto one room.
        self.snippets_button = QtWidgets.QPushButton("Snippets")
        self.snippets_button.setToolTip(
            "Ready-made VEX from the tools installed on this machine,\n"
            "opened as nodes so you can read and change it.")
        self.snippets_button.clicked.connect(self.open_snippets)
        bar.addWidget(self.snippets_button)

        self.keep_button = QtWidgets.QPushButton("Save as Snippet")
        self.keep_button.setToolTip(
            "Give this expression a name and keep it in the Snippets list,\n"
            "alongside the ready-made ones. The name is what shows on the "
            "canvas.")
        self.keep_button.clicked.connect(self.save_as_snippet)
        bar.addWidget(self.keep_button)

        self.apply_button = QtWidgets.QPushButton("Apply to Wrangle")
        self.apply_button.setToolTip(
            "Write the generated VEX into the wrangle's snippet")
        bar.addWidget(self.apply_button)

        self.auto_apply = QtWidgets.QCheckBox("Live")
        self.auto_apply.setChecked(True)
        # While Live is on, applying is automatic and the button is noise;
        # it only appears when someone turns Live off and wants the manual
        # moment back.
        self.apply_button.setVisible(False)
        self.auto_apply.toggled.connect(
            lambda checked: self.apply_button.setVisible(not checked))
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
        # Two buttons beat a dropdown for something you nudge until it looks
        # right; the label between them says where you are.
        self.text_smaller = QtWidgets.QPushButton("−")
        self.text_smaller.setFixedWidth(28)
        self.text_size_label = QtWidgets.QLabel(
            f"{round(theme.get_ui_scale() * 100)}%")
        self.text_size_label.setToolTip(
            "Scales node text, the code pane, the library and the assistant "
            "together.")
        self.text_larger = QtWidgets.QPushButton("+")
        self.text_larger.setFixedWidth(28)
        for widget in (self.text_smaller, self.text_size_label,
                       self.text_larger):
            bar.addWidget(widget)


        # Where a host (the Houdini panel) puts its own controls, so there is
        # one row of buttons instead of a second strip above this one - which
        # in a short docked pane was enough to push the assistant off screen.
        bar.addSpacing(12)
        self.host_slot = QtWidgets.QHBoxLayout()
        self.host_slot.setContentsMargins(0, 0, 0, 0)
        self.host_slot.setSpacing(6)
        bar.addLayout(self.host_slot)

        bar.addStretch(1)

        # Right-aligned, out of the way of the work: learn, help, preferences.
        self.learn_button = QtWidgets.QPushButton("Learn")
        self.learn_button.setCheckable(True)
        self.learn_button.setToolTip(
            "The Beginner course: ten exercises, checked against your "
            "graph as you build it.")
        self.learn_button.setStyleSheet(
            "QPushButton:checked { background: #4a6fa5; color: #ffffff; }")
        bar.addWidget(self.learn_button)
        self.help_button = QtWidgets.QPushButton("Help EN/ES")
        self.help_button.setToolTip("The manual, in English and Spanish.")
        bar.addWidget(self.help_button)
        self.prefs_button = QtWidgets.QPushButton("⚙")
        self.prefs_button.setToolTip("Preferences: grid size, node spacing.")
        self.prefs_button.setStyleSheet(
            "QPushButton { padding: 5px 8px; min-width: 22px; }")
        bar.addWidget(self.prefs_button)

        self.status = QtWidgets.QLabel("")
        self.status.setFont(theme.ui_font(8))
        self.status.setContentsMargins(10, 4, 10, 4)

        # Which build this is, always on screen. After a reload there is
        # otherwise no way to tell whether the editor in front of you is the
        # one you just changed, and guessing wrong wastes the next ten minutes.
        self.build = QtWidgets.QLabel(f"VEXgraph {version_line()}")
        self.build.setFont(theme.ui_font(8))
        self.build.setContentsMargins(10, 4, 10, 4)
        self.build.setStyleSheet("color: #6f6f6f;")
        self.build.setToolTip(
            "The release, and when the newest source file was last written.\n"
            "The timestamp moves on every edit; the version only when it is "
            "deliberately raised.")


        self.issues = QtWidgets.QListWidget()
        self.issues.setFont(theme.ui_font(8))
        self.issues.setMaximumHeight(120)
        self.issues.setStyleSheet(
            f"QListWidget {{ background: {theme.CODE_BG.name()};"
            f" border: none; }} QListWidget::item {{ padding: 3px 8px; }}")

        # The right pane stacks the code and the assistant, so a request and
        # the VEX it produced are on screen together rather than in rival tabs.
        self.assistant = AssistantPanel(self.registry)
        from .learn_panel import LearnPanel                     # noqa: PLC0415
        self.learn = LearnPanel(lambda: self.graph)
        self.learn.ask_professor.connect(self._professor)
        self.learn.open_manual.connect(self._open_help)
        self.learn.focus_changed.connect(self.set_node_focus)
        self.learn.scene_requested.connect(self.scene_requested)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        code_side = QtWidgets.QWidget()
        code_layout = QtWidgets.QVBoxLayout(code_side)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(0)
        code_layout.addWidget(
            self._folding_section("Learn", self.learn, "learn"))
        code_layout.addWidget(self.learn, 1)
        code_layout.addWidget(
            self._folding_section("Generated VEX", self.code, "code"))
        code_layout.addWidget(self.code, 1)
        code_layout.addWidget(
            self._folding_section("Problems", self.issues, "issues"))
        code_layout.addWidget(self.issues)
        # "About the selected node" used to sit here: a smaller copy of the
        # library's own description pane, which already follows the canvas
        # selection and says strictly more (sockets, types, Houdini's help
        # with examples). One pane, in the column that was already about
        # describing nodes.

        assistant_side = QtWidgets.QWidget()
        assistant_layout = QtWidgets.QVBoxLayout(assistant_side)
        assistant_layout.setContentsMargins(0, 0, 0, 0)
        assistant_layout.setSpacing(0)
        assistant_layout.addWidget(
            self._folding_section("Ask for a graph", self.assistant,
                                  "assistant", container=assistant_side))
        assistant_layout.addWidget(self.assistant, 1)

        right.addWidget(code_side)
        right.addWidget(assistant_side)
        right.setSizes([520, 380])

        # Everything that can afford to be small must say so. A Python Panel is
        # given whatever height its pane has, and a widget whose minimum is
        # taller than that does not scroll - it is simply cut off, taking the
        # box you type in with it. Panels docked short are normal, so the
        # editor has to survive one.
        for shrinkable in (self.browser, self.code, self.issues, self.learn,
                           self.assistant, code_side, assistant_side, right):
            shrinkable.setMinimumHeight(0)
            shrinkable.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                     QtWidgets.QSizePolicy.Policy.Ignored)

        # The canvas column: a thin breadcrumb bar appears above the view
        # while a function's graph is open, and is the way back out of it.
        canvas_column = QtWidgets.QWidget()
        canvas_layout = QtWidgets.QVBoxLayout(canvas_column)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)
        self.crumb_bar = QtWidgets.QWidget()
        crumb_layout = QtWidgets.QHBoxLayout(self.crumb_bar)
        crumb_layout.setContentsMargins(8, 4, 8, 4)
        crumb_layout.setSpacing(8)
        back = QtWidgets.QPushButton("◀ Main graph")
        back.setToolTip("Leave this function and return to the graph that "
                        "calls it")
        back.clicked.connect(self.leave_function)
        crumb_layout.addWidget(back)
        self.crumb_label = QtWidgets.QLabel("")
        crumb_layout.addWidget(self.crumb_label)
        crumb_layout.addStretch(1)
        self.crumb_bar.setVisible(False)
        canvas_layout.addWidget(self.crumb_bar)
        canvas_layout.addWidget(self.view, 1)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self.browser)
        splitter.addWidget(canvas_column)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([260, 780, 520])

        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.build)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)
        layout.addLayout(footer)

    def _folding_section(self, text: str, body: QtWidgets.QWidget, key: str,
                         container: QtWidgets.QWidget | None = None
                         ) -> QtWidgets.QLabel:
        """A heading you can click to fold the panel under it away.

        A docked panel is short, and the library, the code, the problems, the
        node description and the assistant all want the same vertical room. The
        section you are not using is exactly the one costing you the space, so
        each one can be put away and stays that way next time.

        `container` is the widget the splitter sizes, when the body sits inside
        a wrapper: capping its height is what actually makes the splitter give
        the room back, rather than leaving a folded section holding its slot.
        """
        label = SectionHeader(text)
        label.setFont(theme.ui_font(8, bold=True))
        label.setContentsMargins(10, 7, 10, 5)
        label.setStyleSheet("color: #8a8a8a; background: #202020;")
        label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        label.setToolTip("Click to fold this section away")

        outer = container if container is not None else body

        # Tracked here rather than read back from `isVisible()`: a widget whose
        # window has not been shown yet reports invisible whatever you set, so
        # asking Qt for the current state would fold the wrong way on the first
        # click of every session.
        state = {"folded": True if key in ALWAYS_FOLDED_AT_START else
                 self._settings.value(f"folded/{key}",
                                      _default_folded(key), type=bool)}

        def apply() -> None:
            folded = state["folded"]
            body.setVisible(not folded)
            label.setText(("▸  " if folded else "▾  ") + text)
            outer.setMaximumHeight(label.sizeHint().height() if folded
                                   else QWIDGETSIZE_MAX)

        def toggle() -> None:
            state["folded"] = not state["folded"]
            apply()
            self._settings.setValue(f"folded/{key}", state["folded"])
            if key == "learn":
                # Focus belongs to the open course: folding it away gives the
                # whole library back, opening it narrows again. (Favourites
                # are not part of this - they are yours whatever mode you are
                # in.) The button follows, so it looks pressed while the
                # course is up however it was opened.
                if state["folded"]:
                    self.learn.release_focus()
                else:
                    self.learn._push_focus()
                self.learn_button.blockSignals(True)
                self.learn_button.setChecked(not state["folded"])
                self.learn_button.blockSignals(False)

        label.clicked.connect(toggle)
        apply()
        # So a button elsewhere (Learn, Ask the professor) can open a
        # section without pretending to be a mouse.
        self._section_state[key] = state
        self._section_toggles[key] = toggle
        return label

    def unfold_section(self, key: str) -> None:
        state = self._section_state.get(key)
        if state is not None and state["folded"]:
            self._section_toggles[key]()

    def _show_learn(self, on: bool) -> None:
        """The Learn button owns the course: pressed shows it, again hides it.

        Focus is pushed on the way in rather than only when the checkbox is
        touched - it was checked from the start and applying nothing, which
        reads as a broken setting.
        """
        state = self._section_state.get("learn")
        if state is None or state["folded"] != on:
            return                       # already in the state being asked for
        self._section_toggles["learn"]()

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
        # and stopping it there is the whole fix. `is_editing` is needed as
        # well as the focus check - the embedded row editor is not always the
        # global focus widget - and it is safe again: a stale flag once kept
        # this branch alive after its field was gone, killing Delete and
        # Ctrl+Z for the session, so the scene now closes the editor on every
        # path that could orphan it (delete, reload, rebuild, Escape).
        if self.scene.is_editing or isinstance(
                focus, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit,
                        QtWidgets.QTextEdit, QtWidgets.QAbstractSpinBox)):
            # Escape in the code pane abandons hand edits. Handled here rather
            # than in the widget because a QPlainTextEdit ignores Escape, so it
            # would travel on to whatever is behind - inside Houdini, that is
            # Houdini.
            if (focus is self.code and self._code_is_user_written
                    and event.key() == QtCore.Qt.Key.Key_Escape):
                if event.type() == QtCore.QEvent.Type.KeyPress:
                    self.revert_code()
                event.accept()
                return True
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
        elif control and key == QtCore.Qt.Key.Key_C:
            action = self._copy
        elif control and key == QtCore.Qt.Key.Key_X:
            action = self._cut
        elif control and key == QtCore.Qt.Key.Key_V:
            action = self._paste
        elif key == QtCore.Qt.Key.Key_Tab:
            # Houdini binds Tab to its own node menu, so inside a docked panel
            # it never reaches the canvas without being claimed here first.
            action = self._search_at_centre

        if action is None:
            return super().eventFilter(watched, event)
        if event.type() == QtCore.QEvent.Type.KeyPress:
            action()
        event.accept()
        return True          # consumed: Houdini never sees it

    def _install_history(self) -> None:
        self.history = History(self.graph.to_dict())
        self._restoring = False
        # Copy, cut and paste are deliberately *not* shortcuts here. A shortcut
        # on this widget fires wherever focus is, so Ctrl+C in the assistant's
        # text box would copy the selected nodes instead of the selected words.
        # The key filter above already makes that distinction, so it owns them.
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

    # ------------------------------------------------- writing code by hand

    def _vex_vocabulary(self) -> list[str]:
        """Everything worth offering while typing VEX.

        The function names come from the registry rather than a list kept here:
        it already holds every function `vcc` reports, so the completion is as
        complete as the node library and cannot drift from it.
        """
        words = [d.vex_function for d in self.registry if d.vex_function]
        words += [
            "@P", "@N", "@Cd", "@Alpha", "@v", "@up", "@orient", "@scale",
            "@pscale", "@id", "@name", "@ptnum", "@primnum", "@vtxnum",
            "@elemnum", "@numpt", "@numprim", "@numvtx", "@numelem",
            "@Time", "@Frame", "@TimeInc", "@ix", "@iy", "@iz", "@group_",
            "@P.x", "@P.y", "@P.z",
        ]
        words += ["int", "float", "vector", "vector2", "vector4", "matrix",
                  "matrix2", "matrix3", "string", "dict", "void",
                  "if", "else", "for", "foreach", "while", "do", "return",
                  "break", "continue", "function"]
        return words

    def _code_edited(self) -> None:
        """The code pane is now the user's; stop generating over it.

        Regeneration runs on a timer while you work, so without this the first
        thing a typed line meets is the emitter overwriting it. The graph is
        left exactly as it was until the code is committed - nothing is
        half-applied.
        """
        if self._code_is_user_written:
            return
        self._code_is_user_written = True
        self._emit_timer.stop()
        self._show_message("Editing the VEX. Ctrl+Enter builds the nodes; "
                           "Esc goes back to the generated code.")

    def build_from_code(self) -> None:
        """Turn what is in the code pane into nodes.

        The same importer the Import VEX button and the assistant's write-VEX
        route use, so hand-written code gets the same treatment as any other:
        translated where it can be, kept verbatim in Inline VEX where it
        cannot, and never rejected.
        """
        from ..parser import import_vex as run_import              # noqa: PLC0415

        source = self.code.toPlainText().strip()
        if not source:
            return
        try:
            report = run_import(source, self.registry)
        except Exception as exc:                                   # noqa: BLE001
            QtWidgets.QMessageBox.warning(
                self, "Could not read that VEX",
                f"{exc}\n\nThe code pane is unchanged.")
            return

        name = self.graph.name
        self._code_is_user_written = False
        self.set_graph(report.graph)
        self.graph.name = name          # the expression is still the same one

        # Laid out and framed: the nodes arrive at wherever the importer put
        # them, which for a graph you have never seen is nowhere useful.
        self.scene.tidy()
        self.view.frame_all()
        # Saying *why* something stayed as text is the one thing the old
        # Import VEX dialog did that this did not. It goes in the status line
        # and the Problems list rather than a dialog: this runs on every
        # Ctrl+Enter, and a modal you have to dismiss each time is a worse
        # tax than the information is worth. It also cannot be dismissed at
        # all without a screen, which hung the test suite when it was one.
        message = report.summary()
        if report.reasons:
            unique = list(dict.fromkeys(report.reasons))
            message += f" — {unique[0]}"
            if len(unique) > 1:
                message += f" (and {len(unique) - 1} more, listed in Problems)"
            self._note_inline_reasons(unique)
        self._show_message(message)

    def _note_inline_reasons(self, reasons: list[str]) -> None:
        """Add "kept as text, because…" lines under the graph's own problems.

        Not errors - the code compiles and runs. They are the answer to "why is
        that one node a block of text", which is otherwise something you have
        to already know to ask.
        """
        for reason in reasons:
            item = QtWidgets.QListWidgetItem(f"Kept as Inline VEX: {reason}")
            item.setForeground(QtGui.QBrush(QtGui.QColor("#8a8a8a")))
            self.issues.addItem(item)

    def revert_code(self) -> None:
        """Throw away hand edits and show the graph's own VEX again."""
        if not self._code_is_user_written:
            return
        self._code_is_user_written = False
        self._regenerate()
        self._show_message("Back to the generated VEX.")

    def _export_wrangle_menu(self) -> int:
        """Refresh Houdini's own snippet menu with everything we can see.

        Houdini builds that menu from the `VEXpressions.txt` files on
        HOUDINI_PATH, and reads them at startup - so a snippet saved now shows
        up there after the next Houdini launch, not immediately. Writing it on
        every open rather than on request is what keeps that gap to one
        restart instead of "whenever somebody remembers to export".
        """
        try:
            _, count = snippets.write_vexpressions()
        except OSError:
            return 0        # read-only checkout; the tool's own list is intact
        return count

    def save_as_snippet(self) -> None:
        """Keep this expression in the Snippets list, under a name you choose.

        Saved as VEX rather than as a graph, so it reopens through the same
        importer as every other snippet: one way in, and a snippet of your own
        behaves exactly like one of OD's.
        """
        emission = generate(self.graph)
        if not emission.ok:
            QtWidgets.QMessageBox.warning(
                self, "Not saved",
                "This graph does not compile yet. Fix the problems listed on "
                "the right, then save it.")
            return

        name, agreed = QtWidgets.QInputDialog.getText(
            self, "Save as snippet", "Name for this expression:",
            QtWidgets.QLineEdit.EchoMode.Normal, self.graph.name)
        name = name.strip()
        if not agreed or not name:
            return

        try:
            written = snippets.save_user_snippet(
                name, emission.code,
                description=f"Saved from VEXgraph — {len(self.graph.nodes)} nodes.")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Not saved",
                                          f"Could not write the snippet: {exc}")
            return
        if not written:
            QtWidgets.QMessageBox.warning(
                self, "Not saved", "No snippet store could be written to.")
            return

        self.graph.name = name
        self.view.viewport().update()
        self._export_wrangle_menu()
        self._show_message(
            f"Saved “{name}”. In the Snippets list now; in the wrangle's own "
            f"menu after the next Houdini restart.")

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

    def _search_at_centre(self) -> None:
        self._search_at(self.view.mapToScene(self.view.viewport().rect().center()),
                        None)

    def _copy(self) -> None:
        copied = self.scene.copy_selected()
        self._show_message(f"Copied {copied} node{'s' if copied != 1 else ''}."
                           if copied else "Nothing selected to copy.")

    def _cut(self) -> None:
        if self.scene.copy_selected():
            self.scene.delete_selected()

    def _paste(self) -> None:
        """Paste under the pointer when it is over the canvas, else offset.

        Pasting on top of the original is confusing - you cannot see that
        anything happened - so the copy always lands somewhere you can tell it
        apart from what it came from.
        """
        at = None
        cursor = self.view.mapFromGlobal(QtGui.QCursor.pos())
        if self.view.rect().contains(cursor):
            at = self.view.mapToScene(cursor)
        pasted = self.scene.paste(at)
        self._show_message(f"Pasted {pasted} node{'s' if pasted != 1 else ''}."
                           if pasted else "There are no nodes on the clipboard.")

    def _describe_in_library(self, node_type: str, status: str = "") -> None:
        """Point the library's detail pane at the node just selected.

        Deselecting deliberately leaves the last description on screen rather
        than blanking it: you often click empty canvas on the way to somewhere
        else, and losing the help you were reading each time is worse than
        showing help for a node that is no longer highlighted.
        """
        if node_type:
            self.browser.describe(node_type, status)

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
            self._show_scene()
            self.run_over.setCurrentText(self.graph.run_over)
            self.assistant.set_graph(self.graph)
            self._regenerate()
        finally:
            self._restoring = False

    def _connect(self) -> None:
        self.scene.graph_changed.connect(self._record_history)
        self.scene.graph_changed.connect(self._schedule)
        self.scene.message.connect(self._show_message)
        self.scene.selection_typed.connect(self._describe_in_library)
        self.scene.selectionChanged.connect(self._sync_highlight)
        self.view.node_search_requested.connect(self._search_at)
        self.view.help_requested.connect(self.open_help_for)
        self.view.function_opened.connect(self.enter_function)
        self.view.collapse_requested.connect(self._collapse_dialog)
        self.view.save_function_requested.connect(self._save_function)
        self.view.library_reveal_requested.connect(self.browser.reveal)
        self.view.favourites_changed.connect(self.browser.refresh_favourites)
        self.code.line_clicked.connect(self._select_from_code)
        self.code.edited.connect(self._code_edited)
        self.code.commit_requested.connect(self.build_from_code)
        self.issues.itemClicked.connect(self._select_from_issue)
        self.apply_button.clicked.connect(self._apply)
        self.run_over.currentTextChanged.connect(self._set_run_over)
        self.text_smaller.clicked.connect(lambda: self._nudge_text_size(-1))
        self.text_larger.clicked.connect(lambda: self._nudge_text_size(+1))
        self.prefs_button.clicked.connect(self._open_preferences)
        self.help_button.clicked.connect(self._open_help)
        self.learn_button.toggled.connect(self._show_learn)
        self.browser.node_chosen.connect(self._place_from_browser)
        self.assistant.proposed.connect(self._propose)
        self.assistant.kept.connect(self._keep_proposal)
        self.assistant.discarded.connect(self._discard_proposal)
        self.assistant.set_graph(self.graph)

    # -------------------------------------------------------------- assistant

    def _place_from_browser(self, node_type: str) -> None:
        """Double-clicking the library drops the node in the middle of the view."""
        # A Custom-section function first has to exist in THIS document:
        # placing it copies the definition in, so the emitted VEX stays
        # self-contained and editing the copy never rewrites the library.
        if node_type.startswith("fn_"):
            name = node_type[3:]
            if name not in self.graph.functions:
                from .. import userfns                          # noqa: PLC0415
                inner = userfns.load(name, self.registry)
                if inner is None:
                    self._show_message(f"{name}() is not in the library any "
                                       f"more.", error=True)
                    return
                self.graph.define_function(inner)
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
        # Keeping is consent: with Live on, the wrangle gets it right now.
        # (The screenshot that found this bug showed a kept graph in the code
        # pane and last week's VEX still running in the wrangle - "press the
        # Apply button" was the old message, and the button was hidden.)
        self._auto_apply()
        if self.auto_apply.isChecked():
            self._show_message("Kept, and written to the wrangle.")
        else:
            self._show_message("Kept. Apply to Wrangle writes it out.")

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
        # Hand-written code is never overwritten. The graph still emits - the
        # problems list and the wrangle stay honest about what the *graph*
        # says - but the pane keeps what was typed until it is committed.
        if not self._code_is_user_written:
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

    def _show_message(self, text: str, *, error: bool = False) -> None:
        """Status line. Red is reserved for things that actually went wrong -
        it used to colour every informative sentence too, which read as a
        wall of alarms for a tool that was working fine."""
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color: {'#e05a5a' if error else '#9a9a9a'};")

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

    _TEXT_STEPS = (0.9, 1.0, 1.15, 1.3, 1.5)

    def _nudge_text_size(self, direction: int) -> None:
        current = theme.get_ui_scale()
        nearest = min(range(len(self._TEXT_STEPS)),
                      key=lambda i: abs(self._TEXT_STEPS[i] - current))
        step = max(0, min(len(self._TEXT_STEPS) - 1, nearest + direction))
        self.text_size_label.setText(f"{round(self._TEXT_STEPS[step] * 100)}%")
        self._set_text_size(f"{round(self._TEXT_STEPS[step] * 100)}%")

    def _open_preferences(self) -> None:
        from . import prefs                                     # noqa: PLC0415
        if prefs.PreferencesDialog(self).exec():
            self._show_message("Preferences saved. Tidy re-lays the graph "
                               "with the new spacing.")

    def _open_help(self) -> None:
        from .helpdialog import HelpDialog                      # noqa: PLC0415
        dialog = HelpDialog(self)
        dialog.setModal(False)
        dialog.show()

    def set_node_focus(self, types: set[str] | None) -> None:
        """Narrow the library (and Tab search) to a set of node types.

        Learn's focus mode: five or ten nodes to choose from instead of the
        whole registry, which for someone on their second exercise is not a
        library but a wall.
        """
        self._node_focus = types
        self.browser.set_focus(types)

    def _professor(self, question: str) -> None:
        """The Learn panel's question, delivered to the assistant with the
        exercise already attached - and the assistant brought into view."""
        self.unfold_section("assistant")
        self.assistant.input.setPlainText(question)
        self.assistant.ask()

    def _set_text_size(self, value: str) -> None:
        theme.set_ui_scale(int(value.rstrip("%")) / 100)
        self.scene.apply_font_scale()
        self.code.refresh_fonts()
        self.browser.refresh_fonts()
        self.assistant.refresh_fonts()
        for label in (self.status, self.issues):
            label.setFont(theme.ui_font(8))
        self.view.viewport().update()

    def search_nodes(self) -> None:
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        self._search_at(centre, None)

    def _search_at(self, scene_pos: QtCore.QPointF, port=None) -> None:
        dialog = (search_for_port(self.registry, self.graph, port) if port
                  else NodeSearch(self.registry, focus=self._node_focus))
        if port is not None:
            dialog.focus = self._node_focus       # Tab from a port, same rule
            dialog.repopulate(dialog.field.text())
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

    # -------------------------------------------------- function navigation

    def enter_function(self, name: str) -> None:
        """Show a user-defined function's own graph on the canvas."""
        if name not in self.graph.functions:
            return
        self._open_function = name
        self._show_scene()

    def leave_function(self) -> None:
        self._open_function = ""
        self._show_scene()

    def collapse_selection(self, name: str) -> bool:
        """Turn the selected nodes into a function, or say why not.

        Transactional: the collapse mutates the document, then the whole
        document is emitted as proof. A refusal or a broken emission puts
        everything back exactly as it was - the user never sees a half-made
        function.
        """
        from .. import refactor                                # noqa: PLC0415
        from .items import NodeItem                            # noqa: PLC0415

        if self.scene.graph is not self.graph:
            self._show_message("Collapse from the main graph, not from "
                               "inside a function.", error=True)
            return False
        selected = {item.node.id for item in self.scene.selectedItems()
                    if isinstance(item, NodeItem)}
        snapshot = self.graph.to_dict()
        error = refactor.collapse(self.graph, selected, name)
        if not error:
            emission = generate(self.graph)
            if any(i.severity == ERROR for i in emission.issues):
                error = ("That selection does not emit working VEX as a "
                         "function; nothing was changed.")
            else:
                # The emitter's checks are necessary but vcc is the truth:
                # a collapse only commits as *compiled* code.
                check = compile_check(emission, self.graph)
                if check.checked and not check.ok:
                    error = ("The collapsed VEX does not compile; nothing "
                             "was changed.")
        if error:
            self.graph = Graph.from_dict(snapshot, self.registry)
            self._show_scene()
            self.assistant.set_graph(self.graph)
            self._show_message(error, error=True)
            return False
        self._show_scene()
        self._record_history()
        self._schedule()
        self._show_message(f"Collapsed into {name}() — double-click the "
                           f"call to step inside.")
        return True

    def _save_function(self, name: str) -> None:
        from .. import userfns                                  # noqa: PLC0415
        inner = self.graph.functions.get(name)
        if inner is None:
            return
        userfns.save(inner)
        self.browser.tree.populate("")
        self._show_message(f"{name}() saved to your library — it is in the "
                           f"Custom section, for any graph.")

    def _collapse_dialog(self) -> None:
        name, accepted = QtWidgets.QInputDialog.getText(
            self, "Collapse into a function",
            "Function name (letters, digits, no spaces):")
        if accepted and name.strip():
            self.collapse_selection(name.strip())

    def _show_scene(self) -> None:
        """Point the canvas at the open function, or at the main graph.

        One scene, rebound - the same mechanism undo uses - so every signal
        connection and shortcut keeps working wherever the user is standing.
        """
        inner = self.graph.functions.get(self._open_function)
        if inner is None:
            self._open_function = ""
        shown = inner if inner is not None else self.graph
        self.scene.graph = shown
        self.scene.reload()
        self.view.frame_all()
        self.crumb_bar.setVisible(inner is not None)
        if inner is not None and inner.signature is not None:
            signature = inner.signature
            params = "; ".join(f"{t} {n}" for t, n, _ in signature.params)
            self.crumb_label.setText(
                f"{signature.return_type} {signature.name}({params})")

    def _rebind(self) -> None:
        # Replacing the whole graph (opening a file, importing VEX, keeping a
        # proposal) starts a new history rather than appending to the old one:
        # undoing across that boundary would resurrect a graph the user has
        # already moved on from.
        self.history.reset(self.graph.to_dict())
        self._last_emission = None
        self._applied_code = ""
        # A function that survived the swap stays open; the scene re-resolves
        # it by name, because the new document is a different object tree.
        self._show_scene()
        self.run_over.setCurrentText(self.graph.run_over)
        # The assistant needs the current graph to answer "change this" requests.
        self.assistant.set_graph(self.graph)
        self._regenerate()
        # A replaced document (Open, Ctrl+Enter, Keep) must reach the wrangle
        # the same as an edited one - Live means live, whichever door the
        # graph came in through.
        self._auto_apply()
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
        if self._graph_before_proposal is not None:
            return          # a proposal is on screen; nothing ships until Keep
        emission = self._last_emission or generate(self.graph)
        if not emission.ok or emission.code == self._applied_code:
            return
        self._applied_code = emission.code
        self.applied.emit(emission.code)
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
