"""VEXgraph inside Houdini: the panel, and the trip to and from a wrangle.

The graph is stored on the wrangle node itself, in user data, so it travels
inside the .hip with no side files and no library to lose. The snippet parm is
generated output: it is overwritten whenever the graph is applied, and the
header comment in it says so.

Nothing here parses VEX. If someone edits the snippet by hand, the graph is
still the source of truth and applying again will replace their edit — which is
why the button says so before it does it.
"""

from __future__ import annotations

import json

import hou
from PySide6 import QtCore, QtWidgets

from vexgraph import Graph, default_registry
from vexgraph.codegen import generate
from vexgraph.ui.panel import VexGraphEditor

# Where the graph lives on the node. Prefixed because user data is a shared
# namespace and other tools write there too.
USER_DATA_KEY = "vexgraph_document"

# The interface name inside vexgraph.pypanel. Passing it to createTab() both
# selects it and hides the Python Panel's own toolbar.
PANEL_INTERFACE = "vexgraph"

WRANGLE_TYPES = {"attribwrangle", "pointwrangle", "primitivewrangle",
                 "vertexwrangle", "detailwrangle", "volumewrangle",
                 "attribvop"}

# The graph's Run Over names against the wrangle's Class menu tokens.
RUN_OVER_TO_CLASS = {
    "points": "point", "primitives": "primitive", "vertices": "vertex",
    "detail": "detail", "numbers": "number",
}
CLASS_TO_RUN_OVER = {v: k for k, v in RUN_OVER_TO_CLASS.items()}


# The spare parameter that reopens the editor from the node itself.
OPEN_PARM = "vexgraph_open"

# Runs in Houdini's Python when the button is pressed. `kwargs["node"]` is the
# node the button is on, which is what makes one button work on every wrangle
# rather than reattaching to whatever happens to be selected.
OPEN_CALLBACK = (
    'import hou\n'
    'try:\n'
    '    import vexgraph_houdini\n'
    'except ImportError:\n'
    '    hou.ui.displayMessage(\n'
    '        "VEXgraph is not on this Houdini\'s Python path.\\n"\n'
    '        "Check that its package file is installed.")\n'
    'else:\n'
    '    vexgraph_houdini.open_window(kwargs["node"])\n'
)


def add_open_button(node) -> bool:
    """Put an "Edit in VEXgraph" button at the top of the wrangle's parameters.

    The editor was reachable only from the shelf, which is the wrong place to
    look for it: you are already looking at the node, and if the window was
    closed - accidentally or otherwise - the way back should be on the thing
    you are working on rather than somewhere else in the interface.

    A spare parameter rather than a custom node type, for the same reason the
    node is a plain wrangle: an asset wrapping it would break `ch()`.

    Returns whether it added one; already having the button is not a failure.
    """
    if node is None or node.parm(OPEN_PARM) is not None:
        return False

    template = hou.ButtonParmTemplate(
        OPEN_PARM, "Edit in VEXgraph",
        script_callback=OPEN_CALLBACK,
        script_callback_language=hou.scriptLanguage.Python,
        help="Open this wrangle's graph in the VEXgraph editor.")

    group = node.parmTemplateGroup()
    entries = group.entries()
    # At the top: below the whole Code folder it is off the bottom of a short
    # parameter pane, which is exactly where you would not find it.
    if entries:
        group.insertBefore(entries[0], template)
    else:
        group.append(template)
    try:
        with hou.undos.group("Add VEXgraph button"):
            node.setParmTemplateGroup(group)
    except hou.OperationFailed:
        return False        # a locked asset, say; the editor still works
    return True


def is_wrangle(node) -> bool:
    """Whether this node can hold a graph and the VEX it generates.

    Asking for a `snippet` parm as well as the known type names: that parm is
    the thing actually required, so a wrangle variant this list has not heard of
    still works.
    """
    if node is None:
        return False
    return (node.type().name() in WRANGLE_TYPES
            or node.parm("snippet") is not None)


def read_graph(node, registry) -> Graph | None:
    """The graph stored on a node, or None if it has never had one."""
    raw = node.userData(USER_DATA_KEY)
    if not raw:
        return None
    try:
        return Graph.from_dict(json.loads(raw), registry)
    except Exception as exc:
        hou.ui.displayMessage(
            f"The graph stored on {node.path()} could not be read:\n{exc}",
            severity=hou.severityType.Warning)
        return None


def write_graph(node, graph: Graph) -> None:
    node.setUserData(USER_DATA_KEY, graph.to_json(indent=0))


def apply_to_node(node, graph: Graph, code: str) -> None:
    """Write the graph and its VEX onto the wrangle, in one undo block."""
    with hou.undos.group("Apply VEXgraph"):
        write_graph(node, graph)
        snippet = node.parm("snippet")
        if snippet is not None:
            snippet.set(code)
        class_parm = node.parm("class")
        token = RUN_OVER_TO_CLASS.get(graph.run_over)
        if class_parm is not None and token:
            try:
                class_parm.set(class_parm.menuItems().index(token))
            except ValueError:
                pass


class VexGraphPanel(QtWidgets.QWidget):
    """The editor plus the strip that says which wrangle it is attached to."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registry = default_registry()
        self.node_path = ""

        self.editor = VexGraphEditor(self.registry, parent=self)
        self.editor.applied.connect(self._apply)

        self.node_label = QtWidgets.QLabel("No wrangle attached")
        self.node_label.setContentsMargins(10, 0, 10, 0)
        attach = QtWidgets.QPushButton("Attach to Selected Wrangle")
        attach.clicked.connect(self.attach_to_selection)
        new_wrangle = QtWidgets.QPushButton("New Wrangle")
        new_wrangle.setToolTip(
            "Create an Attribute Wrangle under the current network and attach")
        new_wrangle.clicked.connect(self.create_wrangle)

        # Into the editor's own toolbar rather than a strip above it: two rows
        # of chrome is enough to push the bottom of the panel out of sight when
        # it is docked in a short pane.
        self.editor.host_slot.addWidget(attach)
        self.editor.host_slot.addWidget(new_wrangle)
        self.editor.host_slot.addWidget(self.node_label)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.editor, 1)

        self.attach_to_selection(quiet=True)

    # ------------------------------------------------------------ attaching

    def node(self):
        return hou.node(self.node_path) if self.node_path else None

    def attach_to_selection(self, quiet: bool = False) -> None:
        selected = [n for n in hou.selectedNodes() if is_wrangle(n)]
        if not selected:
            if not quiet:
                hou.ui.displayMessage(
                    "Select an Attribute Wrangle first, or press New Wrangle.")
            return
        self.attach(selected[0])

    def attach(self, node) -> None:
        self.node_path = node.path()
        self.node_label.setText(node.path())
        # Attaching is the moment this wrangle becomes a VEXgraph node, so it
        # is also when it gains the button that reopens the editor. Nodes made
        # before the button existed pick it up here.
        add_open_button(node)
        graph = read_graph(node, self.registry)
        if graph is None:
            graph = Graph(self.registry)
            graph.add("start", "start")
            class_parm = node.parm("class")
            if class_parm is not None:
                token = class_parm.evalAsString()
                graph.run_over = CLASS_TO_RUN_OVER.get(token, "points")
        self.editor.set_graph(graph)

    def create_wrangle(self) -> None:
        network = self._current_network()
        if network is None:
            hou.ui.displayMessage("Open a SOP network first.")
            return
        with hou.undos.group("Create VEXgraph Wrangle"):
            node = network.createNode("attribwrangle", "vexgraph")
            node.moveToGoodPosition()
            node.setSelected(True, clear_all_selected=True)
        self.attach(node)

    @staticmethod
    def _current_network():
        for pane in hou.ui.paneTabs():
            if pane.type() == hou.paneTabType.NetworkEditor:
                node = pane.pwd()
                if node.childTypeCategory() == hou.sopNodeTypeCategory():
                    return node
        return None

    # -------------------------------------------------------------- applying

    def _apply(self, code: str) -> None:
        node = self.node()
        if node is None:
            hou.ui.displayMessage("Attach a wrangle first.")
            return
        snippet = node.parm("snippet")
        # Anything not generated by this tool is somebody's hand-written VEX,
        # and replacing it silently would be a bad way to find that out.
        if snippet is not None:
            existing = snippet.evalAsString().strip()
            if existing and not existing.startswith("// Built with VEXgraph"):
                choice = hou.ui.displayMessage(
                    f"{node.path()} already has VEX that VEXgraph did not "
                    f"write.\n\nReplace it?",
                    buttons=("Replace", "Cancel"), close_choice=1,
                    severity=hou.severityType.Warning)
                if choice != 0:
                    return
        apply_to_node(node, self.editor.graph, code)


_WINDOW = None          # one window, reused; a second would fight for the node


def open_window(node=None):
    """Open the editor as its own top-level window.

    This is the answer to the keyboard problem, not a preference. A Python
    Panel is a *pane inside Houdini's main window*, so Houdini's hotkey manager
    sees every key first and the panel only gets what is left - which is why
    Delete, undo and even the arrow keys had to be fought for one at a time. A
    separate top-level window has its own focus and its own key handling, so
    shortcuts simply work, the way they do when the editor runs standalone.

    The panel still exists for anyone who wants it docked. The window is what
    the shelf opens.
    """
    global _WINDOW

    parent = None
    try:
        import hou.qt  # noqa: PLC0415 - GUI only, absent in hython

        parent = hou.qt.mainWindow()
    except (ImportError, AttributeError):
        pass

    if _WINDOW is None or not _shiboken_alive(_WINDOW):
        _WINDOW = VexGraphWindow(parent)
    if node is not None:
        _WINDOW.panel.attach(node)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


def close_window() -> None:
    """Close the editor window and forget it.

    Called before a reload: the window's widgets belong to classes that are
    about to be replaced, and this module's reference to it is about to be
    dropped along with the module. Leaving it open would mean a second window
    next time, with the first still running code that no longer exists.
    """
    global _WINDOW

    window, _WINDOW = _WINDOW, None
    if window is not None and _shiboken_alive(window):
        window.close()
        window.deleteLater()


def _shiboken_alive(widget) -> bool:
    """Whether the C++ side of a Qt object still exists."""
    try:
        widget.objectName()
    except RuntimeError:
        return False
    return True


class VexGraphWindow(QtWidgets.QWidget):
    """The editor in a window of its own, owned by Houdini's main window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.setWindowTitle("VEXgraph")
        self.resize(1700, 950)

        self.panel = VexGraphPanel(self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)

    def closeEvent(self, event) -> None:
        """Shut the children down explicitly.

        close() on a top-level widget delivers a close event to that widget
        only - children never see one. The assistant's is not optional: it
        stops the timers and waits for the probe thread, and a QThread still
        running when its Python wrapper is collected takes Houdini with it.
        """
        editor = getattr(self.panel, "editor", None)
        if editor is not None:
            assistant = getattr(editor, "assistant", None)
            if assistant is not None:
                assistant.close()
            editor.close()
        super().closeEvent(event)


def create_wrangle(kwargs=None):
    """Make an Attribute Wrangle in the current network and edit it here.

    Deliberately a plain wrangle rather than a custom node type. A wrangle
    wrapped in a subnet resolves `ch()` against the inner node, so any spare
    parameter the VEX refers to would be looked for on the wrong node; and an
    asset would have to re-implement the Create Spare Parameters button to fix
    it. An ordinary wrangle gets all of that for free and cannot drift from
    however Houdini decides wrangles should behave next.
    """
    kwargs = kwargs or {}
    # Read the selection before creating anything: createNode() selects the new
    # node, so asking afterwards finds only itself and the input never gets
    # wired to what the user had highlighted.
    selected = list(hou.selectedNodes())

    pane = kwargs.get("pane")
    parent = None
    if pane is not None and hasattr(pane, "pwd"):
        parent = pane.pwd()
    if parent is None:
        parent = (selected[0].parent() if selected
                  else hou.node("/obj").createNode("geo"))

    node = parent.createNode("attribwrangle", "vexgraph")
    node.parm("class").set(2)                     # points, the usual case
    add_open_button(node)
    for existing in selected:
        # Compare paths, not objects: hou hands back a fresh Python wrapper on
        # every call, so `is` between two references to the same node is False.
        if (existing.parent().path() == parent.path()
                and existing.path() != node.path()):
            node.setInput(0, existing)
            break
    node.moveToGoodPosition()
    node.setSelected(True, clear_all_selected=True)
    if hasattr(hou, "ui"):          # absent in hython; the node is still made
        # Opening the panel must never cost the node. The UI half of this
        # depends on desktop layout APIs that cannot be exercised outside a
        # running interface, so a surprise there is reported and the wrangle -
        # which is the part that matters - is still there to work with.
        try:
            open_window(node)
        except Exception as exc:                              # noqa: BLE001
            hou.ui.setStatusMessage(
                f"VEXgraph: the node was created but the panel did not open "
                f"({exc}). Open it from the VEXgraph shelf.",
                severity=hou.severityType.Warning)
    return node


def open_for_node(node=None, floating: bool = False) -> None:
    """Open the panel and point it at a node. Used by the shelf tool."""
    if node is None:
        selected = [n for n in hou.selectedNodes() if is_wrangle(n)]
        node = selected[0] if selected else None

    tab = _find_or_create_tab(floating)
    if tab is None:
        return
    # The interface is created lazily, so the widget may not exist yet on a tab
    # that was made a moment ago. Not finding it is not a failure - the panel is
    # open and simply unattached - so it must not raise on the way out.
    try:
        widget = tab.activeInterfaceRootWidget()
    except hou.Error:
        widget = None
    if isinstance(widget, VexGraphPanel) and node is not None:
        widget.attach(node)


def _find_or_create_tab(floating: bool = False):
    """The VEXgraph tab, docked into the current desktop unless asked otherwise.

    A floating window cannot be dragged into the layout afterwards, so creating
    one by default meant the panel could never be part of the interface. Making
    it a tab in an existing pane gives Houdini's own docking: drag the tab where
    you want it, split, or tear it off.
    """
    for pane in hou.ui.paneTabs():
        if (pane.type() == hou.paneTabType.PythonPanel
                and pane.activeInterface()
                and pane.activeInterface().name() == "vexgraph"):
            pane.setIsCurrentTab()
            return pane

    desktop = hou.ui.curDesktop()
    tab = None
    if not floating:
        try:
            tab = _biggest_pane(desktop).createTab(
                hou.paneTabType.PythonPanel, PANEL_INTERFACE)
        except (AttributeError, hou.Error, IndexError):
            tab = None      # any surprise here falls back to a window
    if tab is None:
        tab = desktop.createFloatingPaneTab(hou.paneTabType.PythonPanel,
                                            size=(1500, 900))

    if tab.activeInterface() is None or tab.activeInterface().name() != PANEL_INTERFACE:
        interface = hou.pypanel.interfacesInFile(
            hou.findFile("python_panels/vexgraph.pypanel"))
        if interface:
            tab.setActiveInterface(interface[0])
    tab.setIsCurrentTab()
    return tab


def _biggest_pane(desktop):
    """The largest leaf pane - the least disruptive place to add a tab.

    A split pane is a container for two others, not somewhere a tab can live,
    so those are skipped. Size comes from qtScreenGeometry(): hou.Pane has no
    size() method, and assuming it did is what broke this the first time.
    """
    leaves = [p for p in desktop.panes() if not p.isSplit()]
    if not leaves:
        raise IndexError("no pane to dock into")
    return max(leaves, key=lambda p: (lambda r: r.width() * r.height())(
        p.qtScreenGeometry()))


def createInterface():
    """Entry point named by the .pypanel file."""
    return VexGraphPanel()
