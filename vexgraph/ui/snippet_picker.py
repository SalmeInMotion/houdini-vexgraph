"""Choose a ready-made snippet and open it as nodes.

The snippet is put through the VEX parser rather than pasted as text. That is
the difference worth having: a preset arrives as a graph you can read, take
apart and change, instead of a block of someone else's code you have to trust.
How much of it becomes nodes varies, and the dialog says so before you commit -
the rest is kept verbatim and still compiles.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import snippets as snippet_store
from ..nodedefs import Registry
from ..parser import import_vex
from . import theme


class SnippetPicker(QtWidgets.QDialog):
    chosen = QtCore.Signal(object, str)          # graph, description

    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Snippets")
        self.resize(940, 620)
        self._snippets = snippet_store.load()

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(
            f"Search {len(self._snippets)} snippets...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._repopulate)

        self.list = QtWidgets.QTreeWidget()
        self.list.setHeaderHidden(True)
        self.list.setIndentation(12)
        self.list.currentItemChanged.connect(self._preview)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept())

        self.code = QtWidgets.QPlainTextEdit()
        self.code.setReadOnly(True)
        self.code.setFont(theme.mono_font(9))

        self.summary = QtWidgets.QLabel("")
        self.summary.setWordWrap(True)

        self.open_button = QtWidgets.QPushButton("Open as Nodes")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._accept)
        close = QtWidgets.QPushButton("Cancel")
        close.clicked.connect(self.reject)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.list, 1)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(self.code, 1)
        right.addWidget(self.summary)

        split = QtWidgets.QHBoxLayout()
        split.addLayout(left, 2)
        split.addLayout(right, 3)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self._source_note(), 1)
        buttons.addWidget(close)
        buttons.addWidget(self.open_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(split, 1)
        layout.addLayout(buttons)

        self._repopulate("")

    def _source_note(self) -> QtWidgets.QLabel:
        if self._snippets:
            sources = sorted({s.source for s in self._snippets if s.source})
            text = f"Read from {', '.join(sources)} on this machine."
        else:
            text = ("No snippet file found. Set VEXGRAPH_SNIPPETS to one, or "
                    "put your own in ~/vexgraph_snippets.txt")
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("color: #8a8a8a;")
        return label

    # ------------------------------------------------------------------ list

    def _repopulate(self, query: str) -> None:
        self.list.clear()
        words = query.lower().split()
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for snippet in self._snippets:
            haystack = (f"{snippet.name} {snippet.description} "
                        f"{snippet.context} {snippet.code}").lower()
            if not all(word in haystack for word in words):
                continue
            parent = groups.get(snippet.category)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem(self.list, [snippet.category])
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                parent.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                groups[snippet.category] = parent
            item = QtWidgets.QTreeWidgetItem(parent, [snippet.name])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, snippet)
            if snippet.description:
                item.setToolTip(0, snippet.description)
            if not snippet.is_wrangle:
                # Still readable, but it belongs to a node this tool does not
                # write - say so rather than letting it look interchangeable.
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#909090")))
                item.setToolTip(0, f"For {snippet.node_type}, not a wrangle")
        for parent in groups.values():
            parent.setExpanded(True)

    def _selected(self) -> object | None:
        item = self.list.currentItem()
        return item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None

    def _preview(self) -> None:
        snippet = self._selected()
        self.open_button.setEnabled(snippet is not None)
        if snippet is None:
            self.code.setPlainText("")
            self.summary.setText("")
            return
        self.code.setPlainText(snippet.code)
        report = import_vex(snippet.code, self.registry)
        lines = []
        if snippet.description:
            lines.append(f"<b>{snippet.description}</b>")
        if snippet.author:
            lines.append(f"<span style='color:#8a8a8a'>by {snippet.author}</span>")
        lines.append(f"<span style='color:#8a8a8a'>{snippet.context} — "
                     f"{report.summary()}</span>")
        self.summary.setText("<br>".join(lines))
        self.summary.setOpenExternalLinks(True)

    def _accept(self) -> None:
        snippet = self._selected()
        if snippet is None:
            return
        report = import_vex(snippet.code, self.registry)
        self.chosen.emit(report.graph, f"{snippet.name} — {report.summary()}")
        self.accept()
