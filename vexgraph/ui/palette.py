"""Finding a node among 1339 of them.

Two tiers with very different jobs, so the search treats them differently: the
64 curated nodes are what someone should find first and are always ranked above
the generated ones, which are there when you already know the VEX function you
want. Dragging a wire into empty space filters the list to nodes that can
actually accept it, which turns "what fits here?" from a question into a list.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import favourites, vextypes
from ..graph import Graph
from ..nodedefs import NodeDef, Registry
from . import theme


class NodeSearch(QtWidgets.QDialog):
    chosen = QtCore.Signal(str)

    def __init__(self, registry: Registry, parent=None, *,
                 accepts: str = "", produces: str = "", exec_only: bool = False,
                 focus: set[str] | None = None):
        super().__init__(parent, QtCore.Qt.WindowType.Popup)
        self.registry = registry
        self.accepts = accepts        # the node must take this type as input
        self.produces = produces      # the node must give this type as output
        self.exec_only = exec_only
        # Learn's focus mode: only the node types the course has reached so
        # far. None means the whole library.
        self.focus = focus
        self._accepted = False
        self.setMinimumWidth(430)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.field = QtWidgets.QLineEdit()
        self.field.setPlaceholderText(self._placeholder())
        self.field.setFont(theme.ui_font(10))
        layout.addWidget(self.field)

        self.list = QtWidgets.QListWidget()
        self.list.setFont(theme.ui_font(9))
        self.list.setUniformItemSizes(False)
        layout.addWidget(self.list)

        self.setStyleSheet(f"""
            QDialog {{ background: {theme.PANEL_BG.name()};
                       border: 1px solid #4a4a4a; }}
            QLineEdit {{ background: {theme.TEXT_AREA_BG.name()};
                         color: #e8e8e8; border: none; padding: 9px 12px; }}
            QListWidget {{ background: {theme.PANEL_BG.name()};
                           color: {theme.PANEL_TEXT.name()};
                           border: none; outline: none; }}
            QListWidget::item {{ padding: 6px 12px; }}
            QListWidget::item:selected {{ background: #3a4a5a; color: #ffffff; }}
            {theme.scrollbar_qss()}
        """)

        self.field.textChanged.connect(self.repopulate)
        self.field.returnPressed.connect(self._accept_current)
        self.list.itemActivated.connect(lambda _: self._accept_current())
        self.list.itemClicked.connect(lambda _: self._accept_current())
        self.repopulate("")

    def _placeholder(self) -> str:
        if self.exec_only:
            return "Search for a step to run..."
        if self.accepts:
            return f"Search for a node that takes a {self.accepts}..."
        if self.produces:
            return f"Search for a node that gives a {self.produces}..."
        return "Search nodes..."

    # ----------------------------------------------------------- the filter

    def _fits(self, definition: NodeDef) -> bool:
        if self.focus is not None and definition.type not in self.focus:
            return False
        if self.exec_only:
            return definition.has_exec and definition.exec_in
        if self.accepts:
            return any(vextypes.can_connect(self.accepts, s.type)
                       or s.type in (vextypes.ANY, vextypes.ANY_ARRAY)
                       for s in definition.inputs)
        if self.produces:
            return any(vextypes.can_connect(s.type, self.produces)
                       or s.type in (vextypes.ANY, vextypes.ANY_ARRAY)
                       for s in definition.outputs)
        return True

    def repopulate(self, text: str) -> None:
        self.list.clear()
        if text.strip():
            found = self.registry.search(text, limit=200)
        else:
            # With nothing typed, show the curated set: it is small enough to
            # read and it is what someone should reach for first.
            found = sorted((d for d in self.registry if d.tier == 1),
                           key=lambda d: (d.category, d.label))

        # Starred nodes first, order otherwise untouched: search relevance
        # still decides among them, and among everything else.
        starred = favourites.all_types()
        if starred:
            found = sorted(found, key=lambda d: d.type not in starred)

        shown = 0
        for definition in found:
            if not self._fits(definition):
                continue
            item = QtWidgets.QListWidgetItem()
            # Name the VEX function next to our own label. Searching "rotate"
            # should find "Rotate Matrix", and seeing rotate() on it is how the
            # connection to Houdini's documentation gets made.
            function = definition.vex_function
            if function and function.lower() != definition.label.lower().replace(" ", ""):
                suffix = f"   · {function}()"
            else:
                suffix = "" if definition.tier == 1 else "   · vex"
            item.setText(f"{definition.label}{suffix}\n{definition.summary}"
                         if definition.summary else
                         f"{definition.label}{suffix}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, definition.type)
            item.setToolTip(definition.help or definition.summary)
            if definition.type in starred:
                item.setText("★  " + item.text())
            if definition.tier == 2:
                item.setForeground(QtGui.QBrush(QtGui.QColor("#8f8f8f")))
            self.list.addItem(item)
            shown += 1
            if shown >= 120:
                break

        if self.list.count():
            self.list.setCurrentRow(0)

    def _accept_current(self) -> None:
        # A double-click delivers itemClicked *and* itemActivated, and Return in
        # the field adds returnPressed on top. Each of those is a legitimate way
        # to choose, so rather than dropping one, choosing is made to happen at
        # most once per dialog - two of them arriving used to add two nodes.
        if self._accepted:
            return
        self._accepted = True
        item = self.list.currentItem()
        if item is not None:
            self.chosen.emit(item.data(QtCore.Qt.ItemDataRole.UserRole))
        self.accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        # Arrows move through the list while the cursor stays in the field, so
        # typing and choosing never need a mouse.
        if event.key() in (QtCore.Qt.Key.Key_Down, QtCore.Qt.Key.Key_Up):
            row = self.list.currentRow()
            step = 1 if event.key() == QtCore.Qt.Key.Key_Down else -1
            self.list.setCurrentRow(
                max(0, min(self.list.count() - 1, row + step)))
            event.accept()
            return
        super().keyPressEvent(event)

    def popup_at(self, global_pos: QtCore.QPoint) -> None:
        self.move(global_pos)
        self.resize(430, 420)
        self.show()
        self.field.setFocus()


def search_for_port(registry: Registry, graph: Graph, port) -> NodeSearch:
    """A search filtered to what could join the wire being dragged."""
    if port.is_exec:
        return NodeSearch(registry, exec_only=True)
    vex_type = port.vex_type
    if port.is_input:
        return NodeSearch(registry, produces=vex_type)
    return NodeSearch(registry, accepts=vex_type)
