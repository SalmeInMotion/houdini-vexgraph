"""Render the editor to a PNG offscreen, so the look can be checked without
opening a window - and, more usefully, so a change to the drawing code can be
compared against what it looked like before.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)  # offscreen has no fonts
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from vexgraph import Graph, default_registry  # noqa: E402
from vexgraph.ui.panel import VexGraphEditor  # noqa: E402


def shoot(graph_path: str, out: str, size=(1600, 950)) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    registry = default_registry()
    graph = Graph.load(Path(graph_path), registry) if graph_path else None
    editor = VexGraphEditor(registry, graph)
    editor.resize(*size)
    # Lays the widget out for real without ever putting a window on screen,
    # which matters because a view that has not been sized cannot frame itself.
    editor.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    editor.show()

    for _ in range(3):
        app.processEvents()
    editor.view.frame_all()
    editor._regenerate()
    for _ in range(3):
        app.processEvents()

    image = QtGui.QImage(QtCore.QSize(*size),
                         QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor("#252525"))
    painter = QtGui.QPainter(image)
    editor.render(painter, QtCore.QPoint(0, 0))
    painter.end()
    image.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    graph = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "shot.png"
    shoot(graph, out)
