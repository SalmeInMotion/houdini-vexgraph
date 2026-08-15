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
    """The generated VEX - and, when you type in it, the source of the graph.

    Read-only was the original design and it was defensible: the graph is the
    document and the code is output. But the two directions already existed
    separately - Import VEX builds a graph from pasted code - and keeping them
    apart meant the fastest way to write something you already knew how to
    write was to leave the tool. Typing here is that route, in place.
    """

    line_clicked = QtCore.Signal(int)
    edited = QtCore.Signal()             # the user typed; this is theirs now
    commit_requested = QtCore.Signal()   # Ctrl+Enter: build nodes from it

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(theme.mono_font(9))
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.CODE_BG.name()};"
            f" color: {theme.CODE_TEXT.name()}; border: none;"
            f" padding: 8px; }}")
        self.highlighter = VexHighlighter(self.document())
        self._line_nodes: dict[int, str] = {}
        # True while set_code() is writing, so the generated text does not
        # announce itself as something the user typed.
        self._loading = False

        self._completer: QtWidgets.QCompleter | None = None
        self.textChanged.connect(self._changed)

    # ------------------------------------------------------------ completion

    def set_vocabulary(self, words: list[str]) -> None:
        """The words to offer while typing: VEX functions, attributes, types.

        The same help as writing in the wrangle itself, which is the point -
        the reason to type here rather than there should be the graph you get
        afterwards, not a worse editor.
        """
        model = QtCore.QStringListModel(sorted(set(words)), self)
        completer = QtWidgets.QCompleter(model, self)
        completer.setWidget(self)
        completer.setCompletionMode(
            QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        completer.activated.connect(self._insert_completion)
        self._completer = completer

    def _prefix(self) -> str:
        """The identifier being typed, `@` included - `@pt` must find `@ptnum`."""
        cursor = self.textCursor()
        line = cursor.block().text()[:cursor.positionInBlock()]
        match = re.search(r"[@\w]+$", line)
        return match.group(0) if match else ""

    def _insert_completion(self, word: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.Left,
                            QtGui.QTextCursor.MoveMode.KeepAnchor,
                            len(self._prefix()))
        cursor.insertText(word)
        self.setTextCursor(cursor)

    def _maybe_complete(self) -> None:
        completer = self._completer
        if completer is None:
            return
        prefix = self._prefix()
        # Two characters before offering anything: one letter matches hundreds
        # of the 1300 functions, and a popup over every keystroke is noise.
        if len(prefix) < 2:
            completer.popup().hide()
            return
        if prefix != completer.completionPrefix():
            completer.setCompletionPrefix(prefix)
            completer.popup().setCurrentIndex(
                completer.completionModel().index(0, 0))
        if not completer.completionCount():
            completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(completer.popup().sizeHintForColumn(0)
                      + completer.popup().verticalScrollBar().sizeHint().width())
        completer.complete(rect)

    # ----------------------------------------------------------------- typing

    def _changed(self) -> None:
        if not self._loading:
            self.edited.emit()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        completer = self._completer
        popup_open = completer is not None and completer.popup().isVisible()
        if popup_open and event.key() in (
                QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter,
                QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Escape,
                QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down):
            # The popup owns these while it is open, or Enter both accepts the
            # completion and breaks the line.
            event.ignore()
            return

        control = event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
        if control and event.key() in (QtCore.Qt.Key.Key_Return,
                                       QtCore.Qt.Key.Key_Enter):
            self.commit_requested.emit()
            event.accept()
            return

        super().keyPressEvent(event)
        if not event.text() and event.key() not in (QtCore.Qt.Key.Key_Backspace,):
            return          # a bare modifier or an arrow; nothing was typed
        self._maybe_complete()

    def refresh_fonts(self) -> None:
        self.setFont(theme.mono_font(9))

    def set_code(self, code: str, line_nodes: dict[int, str]) -> None:
        if code == self.toPlainText():
            self._line_nodes = dict(line_nodes)
            return          # nothing to write; do not move the cursor
        scroll = self.verticalScrollBar().value()
        cursor = self.textCursor().position()
        self._loading = True
        try:
            self.setPlainText(code)
        finally:
            self._loading = False
        self._line_nodes = dict(line_nodes)
        # Put the caret back. Regeneration happens while you work, and a caret
        # that jumps to the top on every keystroke elsewhere is unusable.
        restored = self.textCursor()
        restored.setPosition(min(cursor, len(code)))
        self.setTextCursor(restored)
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
