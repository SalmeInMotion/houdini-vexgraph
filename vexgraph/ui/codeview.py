"""The generated VEX, shown next to the graph.

This pane is the part of the tool that teaches. Selecting a node lights up the
lines it produced, so the connection between "the thing I dragged" and "the code
Houdini runs" is visible rather than asserted. Someone who cannot write VEX
today can watch it being written, one node at a time.
"""

from __future__ import annotations

import re

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

KEYWORDS = r"\b(if|else|for|foreach|while|do|break|continue|return|function)\b"
TYPES = r"\b(int|float|vector|vector2|vector4|matrix|matrix2|matrix3|string|dict|void)\b"


class VexHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, document: QtGui.QTextDocument):
        super().__init__(document)
        self.rules: list[tuple[re.Pattern, QtGui.QTextCharFormat]] = []
        for pattern, colour, bold in (
            (TYPES, theme.CODE_TYPE, False),
            (KEYWORDS, theme.CODE_KEYWORD, True),
            (r"[fivpu234sd]?@\w+", theme.CODE_ATTRIB, False),
            (r"\b\d+\.?\d*\b", theme.CODE_NUMBER, False),
            (r'"[^"]*"', theme.CODE_STRING, False),
            (r"//.*$", theme.CODE_COMMENT, False),
        ):
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(colour)
            if bold:
                fmt.setFontWeight(QtGui.QFont.Weight.Bold)
            self.rules.append((re.compile(pattern), fmt))

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class CodeView(QtWidgets.QPlainTextEdit):
    line_clicked = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(theme.mono_font(9))
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.CODE_BG.name()};"
            f" color: {theme.CODE_TEXT.name()}; border: none;"
            f" padding: 8px; }}")
        self.highlighter = VexHighlighter(self.document())
        self._line_nodes: dict[int, str] = {}

    def refresh_fonts(self) -> None:
        self.setFont(theme.mono_font(9))

    def set_code(self, code: str, line_nodes: dict[int, str]) -> None:
        scroll = self.verticalScrollBar().value()
        self.setPlainText(code)
        self._line_nodes = dict(line_nodes)
        self.verticalScrollBar().setValue(scroll)

    def highlight_node(self, node_id: str) -> None:
        selections: list[QtWidgets.QTextEdit.ExtraSelection] = []
        if node_id:
            fmt = QtGui.QTextCharFormat()
            fmt.setBackground(theme.CODE_HIGHLIGHT)
            fmt.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
            for line_no, owner in self._line_nodes.items():
                if owner != node_id:
                    continue
                block = self.document().findBlockByLineNumber(line_no - 1)
                if not block.isValid():
                    continue
                selection = QtWidgets.QTextEdit.ExtraSelection()
                selection.format = fmt
                selection.cursor = QtGui.QTextCursor(block)
                selections.append(selection)
        self.setExtraSelections(selections)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """Clicking a line selects the node that wrote it - the reverse trip."""
        super().mousePressEvent(event)
        cursor = self.cursorForPosition(event.pos())
        self.line_clicked.emit(cursor.blockNumber() + 1)

    def node_at_line(self, line_no: int) -> str:
        return self._line_nodes.get(line_no, "")
