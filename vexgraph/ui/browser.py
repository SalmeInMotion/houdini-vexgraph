"""The node library, browsable rather than searched.

Tab-search assumes you know roughly what the node is called. Dragging a wire
into space assumes you know what you are building toward. Neither helps the
person this tool is for, who is looking at an empty canvas wondering what is
even possible — for them the answer has to be *visible*, grouped by what it is
for, with a sentence of explanation attached.

So the library sits open on the left: categories, one line of description per
node, and the full help for whatever is selected. Double-click or drag to place.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import help as vexhelp
from ..nodedefs import NodeDef, Registry
from . import icons, theme

# The drag payload between the library and the canvas: one node type name.
NODE_MIME = "application/x-vexgraph-node"

NODE_MIME = "application/x-vexgraph-node"

# Tier 1 is the browsable set. The 1275 generated VEX-function nodes live
# behind the search box: showing them in a tree would bury the 60 nodes that
# were written for a human to read.
BROWSE_TIER = 1


class NodeBrowser(QtWidgets.QWidget):
    node_chosen = QtCore.Signal(str)      # double-clicked: place it

    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.registry = registry

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search all 1300 nodes...")
        self.search.setClearButtonEnabled(True)
        self.search.setFont(theme.ui_font(9))

        self._selected = ""
        self._doc_url = ""
        self.tree = NodeTree(registry)
        self.tree.node_chosen.connect(self.node_chosen)
        self.tree.selection_changed.connect(self.describe)

        self.detail = QtWidgets.QTextBrowser()
        self.detail.setFont(theme.ui_font(8))
        self.detail.setMinimumHeight(60)
        # The "See also" links in Houdini's help are real URLs; let them open in
        # a browser rather than trying to navigate the little pane to them.
        self.detail.setOpenExternalLinks(True)

        # A splitter rather than a fixed max-height on the detail pane: the
        # description is often longer than a hardcoded box, and dragging the
        # handle up is the direct way to answer "let me expand this area."
        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)
        top_layout.addWidget(self.search)
        top_layout.addWidget(self.tree, 1)

        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)
        bottom_layout.addWidget(self._label("What it does"))
        bottom_layout.addWidget(self.detail, 1)

        # Double-clicking the tree already placed the node, but nothing said so.
        # A button makes adding from the library something you can find rather
        # than something you have to be told about.
        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self.add_button = QtWidgets.QPushButton("Add to Graph")
        self.add_button.setFont(theme.ui_font(9))
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_selected)
        buttons.addWidget(self.add_button, 1)

        # The local help covers most functions but not all, and never covers the
        # curated nodes' own pages. The button is always the way out to the full
        # documentation, whether or not anything was found to show inline.
        self.docs_button = QtWidgets.QPushButton("Docs ↗")
        self.docs_button.setFont(theme.ui_font(9))
        self.docs_button.setEnabled(False)
        self.docs_button.setToolTip("Open this function's page on sidefx.com")
        self.docs_button.clicked.connect(self._open_docs)
        buttons.addWidget(self.docs_button)
        bottom_layout.addLayout(buttons)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([440, 200])
        self._splitter = splitter

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label("Node Library"))
        layout.addWidget(splitter, 1)

        self.setStyleSheet(f"""
            QLineEdit {{ background: {theme.TEXT_AREA_BG.name()}; color: #e0e0e0;
                         border: none; padding: 7px 10px; }}
            QTreeWidget {{ background: {theme.PANEL_BG.name()};
                           color: {theme.PANEL_TEXT.name()};
                           border: none; outline: none; }}
            QTreeWidget::item {{ padding: 3px 2px; }}
            QTreeWidget::item:selected {{ background: #3a4a5a; color: #ffffff; }}
            QTextBrowser {{ background: {theme.CODE_BG.name()};
                            color: {theme.PANEL_TEXT.name()};
                            border: none; padding: 8px; }}
            QSplitter::handle {{ background: #1b1b1b; }}
        """)

        self.search.textChanged.connect(self.tree.filter)
        self.tree.populate()

    def refresh_fonts(self) -> None:
        query = self.search.text()
        self.search.setFont(theme.ui_font(9))
        self.detail.setFont(theme.ui_font(8))
        self.tree.setFont(theme.ui_font(9))
        self.tree.populate(query)          # item fonts are set at creation

    @staticmethod
    def _label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setFont(theme.ui_font(8, bold=True))
        label.setContentsMargins(10, 7, 10, 5)
        label.setStyleSheet("color: #8a8a8a; background: #202020;")
        return label

    def _add_selected(self) -> None:
        if self._selected:
            self.node_chosen.emit(self._selected)

    def _open_docs(self) -> None:
        if self._doc_url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._doc_url))

    def describe(self, node_type: str) -> None:
        """Show this node's help, wherever the interest in it came from.

        Public because the canvas drives it too: selecting a node in the graph
        should fill this pane the same way picking it out of the tree does, so
        the description is always about the node you are working on rather than
        the last one you happened to look up.
        """
        definition = self.registry.get(node_type)
        self._selected = node_type if definition is not None else ""
        self.add_button.setEnabled(definition is not None)
        if definition is not None:
            self.add_button.setText(f"Add {definition.label}")
        if definition is None:
            self.detail.setHtml("")
            self.add_button.setText("Add to Graph")
            return

        parts = [f"<b style='color:#d8d8d8'>{definition.label}</b>"]
        if definition.summary:
            parts.append(definition.summary)
        if definition.help:
            parts.append(f"<i style='color:#9a9a9a'>{definition.help}</i>")

        sockets = []
        for socket in definition.inputs:
            colour = theme.type_colour(socket.type).name()
            sockets.append(f"<span style='color:{colour}'>&#9679;</span> "
                           f"{socket.title} <i>{socket.type}</i>")
        for socket in definition.outputs:
            colour = theme.type_colour(socket.type).name()
            sockets.append(f"{socket.title} <i>{socket.type}</i> "
                           f"<span style='color:{colour}'>&#9679;</span>")
        if sockets:
            parts.append("<br>".join(sockets))
        if definition.tier == 2:
            parts.append("<i style='color:#8a8a8a'>Raw VEX function.</i>")

        # Houdini's own words on the function underneath, examples included.
        # Reading it here rather than in a browser is the whole point: the node
        # and the documentation for it are the same thing.
        # The URL is only offered when the local help actually has that page.
        # vex.zip is the source the website is built from, so "not in the
        # archive" means "no page on sidefx.com" - guessing the URL from the
        # name sent people to 404s for anything without one.
        self._doc_url = ""
        doc = vexhelp.page(definition.vex_function)
        if doc is not None:
            self._doc_url = doc.url
            parts.append(vexhelp.as_html(doc, title=f"Houdini help: {doc.name}()"))

        # Naming the function is the difference between a button that looks
        # broken and one that says why it is off - and it also tells you what
        # this node is called in Houdini's own documentation, which is not
        # always what we call it.
        self.docs_button.setEnabled(bool(self._doc_url))
        if self._doc_url:
            self.docs_button.setText(f"{definition.vex_function}() ↗")
            self.docs_button.setToolTip(
                f"Open {definition.vex_function}() on sidefx.com")
        else:
            self.docs_button.setText("No docs")
            self.docs_button.setToolTip(
                "Houdini has no documentation page for this node — it is built "
                "from an operator or a language construct, not a VEX function.")
        self.docs_button.setStyleSheet(
            "QPushButton { background:#2f4a63; color:#cfe3f5; border:none;"
            " padding:6px 10px; }"
            "QPushButton:hover { background:#3a5c7a; }"
            if self._doc_url else
            "QPushButton { background:#262626; color:#5f5f5f; border:none;"
            " padding:6px 10px; font-style:italic; }")
        self.detail.setHtml("<br><br>".join(parts))


class NodeTree(QtWidgets.QTreeWidget):
    node_chosen = QtCore.Signal(str)
    selection_changed = QtCore.Signal(str)

    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.setFont(theme.ui_font(9))
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)
        self.itemDoubleClicked.connect(self._chosen)
        self.currentItemChanged.connect(self._current)

    def mimeData(self, items):
        """Carry the node type, so the canvas knows what was dropped on it.

        Dragging was already enabled but the drag carried only the tree's own
        internal data, which meant nothing to the canvas - so a drag looked
        like it was working and then did nothing.
        """
        data = QtCore.QMimeData()
        for item in items:
            node_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if node_type:
                data.setData(NODE_MIME, node_type.encode("utf-8"))
                data.setText(node_type)
                break
        return data

    def populate(self, query: str = "") -> None:
        self.clear()
        if query.strip():
            # Searching reaches the whole registry, including the generated
            # VEX functions the tree deliberately hides.
            found = self.registry.search(query, limit=200)
            grouped: dict[str, list[NodeDef]] = {}
            for definition in found:
                grouped.setdefault(definition.category, []).append(definition)
        else:
            grouped = {}
            for definition in self.registry:
                if definition.tier != BROWSE_TIER:
                    continue
                grouped.setdefault(definition.category, []).append(definition)

        for category in sorted(grouped):
            parent = QtWidgets.QTreeWidgetItem(self, [category])
            parent.setForeground(0, QtGui.QBrush(QtGui.QColor("#8fa4b0")))
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

            for definition in sorted(grouped[category], key=lambda d: d.label):
                # Our nodes are named for the task; Houdini names the function.
                # Showing both is how someone learns that "Rotate Matrix" is
                # what the documentation calls rotate() - which is the whole
                # point of the tool.
                label = definition.label
                function = definition.vex_function
                if function and function.lower() != label.lower().replace(" ", ""):
                    label = f"{label}   {function}()"
                item = QtWidgets.QTreeWidgetItem(parent, [label])
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, definition.type)
                item.setToolTip(0, definition.summary or definition.label)
                pixmap = icons.node_icon(definition.type, 16)
                if pixmap is not None:
                    item.setIcon(0, QtGui.QIcon(pixmap))
                if definition.tier == 2:
                    item.setForeground(0, QtGui.QBrush(QtGui.QColor("#909090")))
            parent.setExpanded(bool(query.strip()))

        # With nothing typed, open the groups a beginner needs first and leave
        # the rest folded, so the whole library is legible at a glance.
        if not query.strip():
            for index in range(self.topLevelItemCount()):
                item = self.topLevelItem(index)
                item.setExpanded(item.text(0) in ("Flow", "Attributes"))

    def filter(self, query: str) -> None:
        self.populate(query)

    def _chosen(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        node_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if node_type:
            self.node_chosen.emit(node_type)

    def _current(self, item, _previous) -> None:
        if item is None:
            self.selection_changed.emit("")
            return
        self.selection_changed.emit(
            item.data(0, QtCore.Qt.ItemDataRole.UserRole) or "")

    def mimeData(self, items) -> QtCore.QMimeData:
        data = QtCore.QMimeData()
        for item in items:
            node_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if node_type:
                data.setData(NODE_MIME, node_type.encode("utf-8"))
                data.setText(node_type)
                break
        return data
