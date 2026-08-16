"""The manual, inside the tool, in both its languages.

The markdown under docs/ is the single source; this just renders it. No
web engine, no external viewer - Qt's own markdown support is plenty for
a manual, and it means the help works offline and inside Houdini alike.
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from . import settings, theme

_DOCS = Path(__file__).resolve().parents[2] / "docs"
_PAGES = (("English", "manual.md"), ("Español", "manual.es.md"))


class HelpDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VEXgraph Help")
        self.resize(760, 640)
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.PANEL_BG.name()}; }}
            QTextBrowser {{ background: {theme.CODE_BG.name()};
                            color: {theme.PANEL_TEXT.name()};
                            border: none; padding: 14px; }}
            {theme.scrollbar_qss()}
        """)
        layout = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        for label, filename in _PAGES:
            view = QtWidgets.QTextBrowser()
            view.setOpenExternalLinks(True)
            view.setFont(theme.ui_font(10))
            path = _DOCS / filename
            try:
                view.setMarkdown(path.read_text(encoding="utf8"))
            except OSError:
                view.setPlainText(f"Manual not found: {path}")
            tabs.addTab(view, label)

        # Remember which language was open last time.
        store = settings.store()
        tabs.setCurrentIndex(int(store.value("help/tab", 0)))
        tabs.currentChanged.connect(
            lambda index: store.setValue("help/tab", index))
