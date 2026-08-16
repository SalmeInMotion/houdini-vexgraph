"""The canvas: a scene bound to a Graph, and a view you can pan, zoom and draw on.

The scene owns the mapping between the document and what is on screen, and is
the only place that mutates the graph. Everything else asks it to.

Refusing a bad wire is the interaction that matters most here. A connection is
checked while it is being dragged, the wire turns red and dashed the moment it
would be illegal, and dropping it says why in words aimed at someone who does
not write VEX. Being told "a vector has 3 components, use Length" as you drag is
worth more than the same message in a compiler log ten minutes later.
"""

from __future__ import annotations

import json

from PySide6 import QtCore, QtGui, QtWidgets

from ..graph import EXEC_PIN, Graph, Node
from . import theme
from .browser import NODE_MIME
from .items import (BoxItem, DragLink, LinkItem, NodeItem, PortItem, RowItem,
                    retire)
from .layout import arrange, needs_arranging

# How far the mouse may travel between press and release and still count as a
# click rather than a drag. Generous, because a click on a small port with an
# unsteady hand is still a click.
CLICK_SLOP = 6


class GraphScene(QtWidgets.QGraphicsScene):
    graph_changed = QtCore.Signal()
    # The selected node's type and its problem, if it has one, so the
    # library's detail pane can follow the canvas: whichever node you touched
    # last is the one described, whether you reached it through the tree or
    # by clicking it in the graph. One pane, not two - there used to be a
    # second, smaller copy of this on the right, saying strictly less.
    selection_typed = QtCore.Signal(str, str)
    message = QtCore.Signal(str)

    def __init__(self, graph: Graph, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.node_items: dict[str, NodeItem] = {}
        self.link_items: list[LinkItem] = []
        self._drag: DragLink | None = None
        self._drag_origin = QtCore.QPointF()
        # True once a wire has been left armed by a click, waiting for the click
        # that lands it. Dragging never sets this.
        self._sticky = False
        self._editor: QtWidgets.QGraphicsProxyWidget | None = None

        # No BSP index. Qt's spatial index purges removed items on a
        # zero-delay timer, so an item destroyed before that timer runs leaves
        # a dangling pointer in the index and the next update walks freed
        # memory - a hard crash that takes Houdini down with it. Rebuilding a
        # node tears down every port and row, so this fires constantly; it is
        # what made "change the text size" close Houdini.
        #
        # Qt recommends NoIndex whenever items are added, removed or moved
        # often, which is exactly this scene. Lookup becomes linear, which is
        # nothing for graphs of this size.
        self.setItemIndexMethod(QtWidgets.QGraphicsScene.ItemIndexMethod.NoIndex)
        self.setBackgroundBrush(theme.BACKGROUND)
        self.selectionChanged.connect(self._describe_selection)
        self.reload()

    # -------------------------------------------------------------- building

    def reload(self) -> None:
        # clear() destroys the row under any open editor without telling it;
        # the dangling proxy would leave `is_editing` stuck and the keyboard
        # dead. Close it first, always.
        self._close_editor()
        self.clear()
        self.node_items.clear()
        self.link_items.clear()
        for node in self.graph.nodes.values():
            item = NodeItem(self.graph, node)
            self.addItem(item)
            self.node_items[node.id] = item
        for box in self.graph.boxes:
            self.addItem(BoxItem(self.graph, box))
        # A graph built in code, or written by the assistant, has every node at
        # the origin. Showing that pile would look like a broken tool.
        if needs_arranging(self.graph):
            arrange(self.node_items, self.graph)
        self.rebuild_links()

    def add_box(self, rect: QtCore.QRectF, title: str = "") -> BoxItem:
        box = self.graph.add_box(title, (rect.x(), rect.y(),
                                         rect.width(), rect.height()))
        item = BoxItem(self.graph, box)
        self.addItem(item)
        self.graph_changed.emit()
        return item

    def tidy(self) -> None:
        arrange(self.node_items, self.graph)
        self.refresh_links()
        self.graph_changed.emit()

    def rebuild_links(self) -> None:
        for link in self.link_items:
            self.removeItem(link)
        retire(self.link_items)     # see items.retire - freeing now can crash
        self.link_items.clear()
        for link in self.graph.links:
            source = self._port(link.from_node, link.from_socket, False)
            target = self._port(link.to_node, link.to_socket, True)
            if source is None or target is None:
                continue
            item = LinkItem(source, target, link.is_exec)
            self.addItem(item)
            self.link_items.append(item)

    def _port(self, node_id: str, socket: str, is_input: bool) -> PortItem | None:
        item = self.node_items.get(node_id)
        return item.ports.get((socket, is_input)) if item else None

    def refresh_links(self) -> None:
        for link in self.link_items:
            link.refresh()

    # -------------------------------------------------------------- mutation

    def add_node(self, node_type: str, pos: QtCore.QPointF) -> NodeItem | None:
        try:
            node = self.graph.add(node_type)
        except Exception as exc:                      # unknown type, bad JSON
            self.message.emit(str(exc))
            return None
        node.pos = (pos.x(), pos.y())
        item = NodeItem(self.graph, node)
        self.addItem(item)
        self.node_items[node.id] = item
        self.clearSelection()
        item.setSelected(True)
        # Whatever added this - the library list, the search popup - still holds
        # the keyboard otherwise, so the node arrives selected but Delete goes
        # somewhere else entirely and it looks undeletable.
        views = self.views()
        if views:
            views[0].setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        self.graph_changed.emit()
        return item

    def copy_selected(self) -> int:
        """Put the selected nodes, and the wires between them, on the clipboard.

        Plain JSON on the system clipboard rather than a private buffer, so a
        chunk of graph can be moved between two VEXgraph windows - and pasted
        into a text file to keep, which is the cheapest possible way to save a
        fragment you want again tomorrow.

        Only wires with both ends inside the selection travel: a wire to a node
        that was not copied has nothing to reconnect to.
        """
        nodes = [item.node for item in self.selectedItems()
                 if isinstance(item, NodeItem)]
        if not nodes:
            return 0
        inside = {node.id for node in nodes}
        payload = {
            "vexgraph": "nodes",
            "nodes": [node.to_dict() for node in nodes],
            "links": [link.to_dict() for link in self.graph.links
                      if link.from_node in inside and link.to_node in inside],
        }
        QtWidgets.QApplication.clipboard().setText(json.dumps(payload, indent=1))
        return len(nodes)

    def paste(self, at: QtCore.QPointF | None = None) -> int:
        """Add the clipboard's nodes as a fresh copy, keeping their layout.

        Ids are reassigned - pasting into the graph the nodes came from would
        otherwise collide - and the wires are remapped onto the new ids, so the
        pasted group is wired exactly like the one that was copied.
        """
        try:
            payload = json.loads(QtWidgets.QApplication.clipboard().text())
            raw_nodes = list(payload["nodes"])
        except (ValueError, TypeError, KeyError):
            return 0
        if payload.get("vexgraph") != "nodes" or not raw_nodes:
            return 0

        # Keep the shape of the copied group: everything moves by one offset,
        # measured from its top-left corner.
        left = min(raw["pos"][0] for raw in raw_nodes)
        top = min(raw["pos"][1] for raw in raw_nodes)
        shift = (at.x() - left, at.y() - top) if at is not None else (40.0, 40.0)

        renamed: dict[str, str] = {}
        created: list[NodeItem] = []
        for raw in raw_nodes:
            try:
                node = self.graph.add(raw["type"], **raw.get("params", {}))
            except Exception as exc:      # a node type this build does not have
                self.message.emit(str(exc))
                continue
            renamed[raw["id"]] = node.id
            node.title = raw.get("title", "")
            node.pos = (raw["pos"][0] + shift[0], raw["pos"][1] + shift[1])
            item = NodeItem(self.graph, node)
            self.addItem(item)
            self.node_items[node.id] = item
            created.append(item)

        if not created:
            return 0

        for raw in payload.get("links", ()):
            source, target = renamed.get(raw["from"]), renamed.get(raw["to"])
            if source and target:
                self.graph.connect(source, raw["out"], target, raw["in"],
                                   is_exec=bool(raw.get("exec")))

        self.clearSelection()
        for item in created:
            item.setSelected(True)
        self.rebuild_links()
        self._rebuild_rows()
        self.graph_changed.emit()
        return len(created)

    def delete_selected(self) -> None:
        # Deleting the node whose row is being edited left the editor proxy
        # alive and `is_editing` stuck - which silently killed Delete and
        # Ctrl+Z for the rest of the session, because the key filter kept
        # routing them to a text field that no longer existed.
        self._close_editor()
        removed = False
        detached = []
        for item in list(self.selectedItems()):
            if isinstance(item, NodeItem):
                self.graph.remove(item.node.id)
                self.node_items.pop(item.node.id, None)
                self.removeItem(item)
                detached.append(item)
                removed = True
            elif isinstance(item, LinkItem):
                self._remove_link_item(item)
                removed = True
            elif isinstance(item, BoxItem):
                # The box goes; its nodes stay. Grouping is not ownership.
                self.graph.remove_box(item.box.id)
                self.removeItem(item)
                detached.append(item)
                removed = True
        retire(detached)            # see items.retire
        if removed:
            self.rebuild_links()
            self._rebuild_rows()
            self.graph_changed.emit()

    def _remove_link_item(self, item: LinkItem) -> None:
        self.graph.links = [
            x for x in self.graph.links
            if not (x.from_node == item.source.node_item.node.id
                    and x.from_socket == item.source.name
                    and x.to_node == item.target.node_item.node.id
                    and x.to_socket == item.target.name)]

    def remove_link(self, item: LinkItem) -> None:
        """Delete one wire and put back the value row it was covering."""
        self._remove_link_item(item)
        self.rebuild_links()
        self._rebuild_rows()
        self.graph_changed.emit()

    def node_moved(self, item: NodeItem) -> None:
        self.refresh_links()

    def value_changed(self, item: NodeItem, *, retyped: bool) -> None:
        if retyped:
            # A type change can invalidate wires already attached, so they are
            # dropped rather than left pointing at a socket that changed
            # meaning underneath them.
            self._drop_invalid_links(item.node.id)
            item.rebuild()
            self.rebuild_links()
        self.graph_changed.emit()

    def _drop_invalid_links(self, node_id: str) -> None:
        kept = []
        for link in self.graph.links:
            if link.is_exec or node_id not in (link.from_node, link.to_node):
                kept.append(link)
                continue
            refusal = self.graph.check_connection(
                link.from_node, link.from_socket, link.to_node, link.to_socket)
            if refusal:
                self.message.emit(f"Wire removed: {refusal}")
            else:
                kept.append(link)
        self.graph.links = kept

    def _rebuild_rows(self) -> None:
        """Inputs show or hide their value row depending on what is wired."""
        self._close_editor()        # its row is about to be torn down
        for item in self.node_items.values():
            item.rebuild()
        self.rebuild_links()

    def apply_font_scale(self) -> None:
        """Re-measure every node against the current text size.

        Node width and row layout are computed from font metrics at rebuild
        time, not at paint time, so a text-size change needs every item
        rebuilt or the boxes stay the old size while the text inside grows.
        """
        self._rebuild_rows()

    # ---------------------------------------------------------- link dragging

    def begin_link_drag(self, port: PortItem, scene_pos: QtCore.QPointF) -> None:
        existing = self._link_into(port) if port.is_input else None
        if existing is not None:
            # Grabbing a connected input picks the wire up rather than making a
            # second one, which is what every node editor does and what the
            # hand expects.
            source = existing.source
            self._remove_link_item(existing)
            self.rebuild_links()
            self._rebuild_rows()
            port = self._port(source.node_item.node.id, source.name, False) or source
        self._drag = DragLink(port)
        self._drag_origin = scene_pos
        self._sticky = False
        self.addItem(self._drag)
        self._drag.drag_to(scene_pos)

    def _link_into(self, port: PortItem) -> LinkItem | None:
        return next((l for l in self.link_items
                     if l.target is port), None)

    def mousePressEvent(self, event) -> None:
        # Right-click drops a wire in progress. Escape does too, but keys are
        # not reliable inside a docked pane, and abandoning a wire is something
        # you need to be able to do without thinking about it.
        if (self._drag is not None
                and event.button() == QtCore.Qt.MouseButton.RightButton):
            self.cancel_link_drag()
            self.message.emit("")
            event.accept()
            return

        if self._sticky and self._drag is not None:
            source_port = self._drag.port
            target = self._port_at(event.scenePos())
            self.removeItem(self._drag)
            self._drag = None
            self._sticky = False
            if target is None:
                # Same as letting a dragged wire go on empty canvas: offer the
                # nodes that could accept it, rather than silently dropping it.
                self.link_dropped_on_empty(source_port, event.scenePos())
            else:
                self.connect_ports(source_port, target)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            target = self._port_at(event.scenePos())
            refused = bool(target and self._refusal(self._drag.port, target))
            self._drag.drag_to(event.scenePos(), refused)
            if target is not None:
                reason = self._refusal(self._drag.port, target)
                self.message.emit(reason or "")
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None and not self._sticky:
            # A click and a drag both start the same way, so the gesture is only
            # known on release. Barely moving means "click", and the wire stays
            # armed until the next click instead of being dropped here - which
            # is the difference between reaching a far node and not.
            moved = (event.scenePos() - self._drag_origin).manhattanLength()
            if moved < CLICK_SLOP and self._port_at(event.scenePos()) is not None:
                self._sticky = True
                self.message.emit("Click an input to connect, or press Escape.")
                return
            source_port = self._drag.port
            self.removeItem(self._drag)
            self._drag = None
            target = self._port_at(event.scenePos())
            if target is None:
                self.link_dropped_on_empty(source_port, event.scenePos())
            else:
                self.connect_ports(source_port, target)
            return
        super().mouseReleaseEvent(event)
        self._snap_selection()

    def _snap_selection(self) -> None:
        """Settle dropped nodes onto the grid, the way Houdini's canvas does.

        On release rather than during the drag: snapping while moving makes
        the node stutter under the cursor, and the point of the grid is tidy
        resting places, not tidy journeys.
        """
        step = theme.GRID_SPACING
        for item in self.selectedItems():
            if not isinstance(item, NodeItem):
                continue
            snapped = QtCore.QPointF(round(item.pos().x() / step) * step,
                                     round(item.pos().y() / step) * step)
            if snapped != item.pos():
                item.setPos(snapped)

    def cancel_link_drag(self) -> None:
        if self._drag is not None:
            self.removeItem(self._drag)
            self._drag = None
        self._sticky = False

    def link_dropped_on_empty(self, port: PortItem,
                              scene_pos: QtCore.QPointF) -> None:
        """Hook for the panel: offer a filtered node search."""
        view = self.views()[0] if self.views() else None
        if view is not None and hasattr(view, "request_node_search"):
            view.request_node_search(scene_pos, port)

    def _port_at(self, scene_pos: QtCore.QPointF) -> PortItem | None:
        for item in self.items(scene_pos):
            if isinstance(item, PortItem):
                return item
        return None

    def _refusal(self, a: PortItem, b: PortItem) -> str:
        """Why these two ports may not be joined, or empty if they may."""
        source, target = (a, b) if not a.is_input else (b, a)
        if source.is_input or not target.is_input:
            return "Wires run from an output to an input."
        if source.is_exec != target.is_exec:
            return "A run-order wire cannot carry a value."
        if source.node_item is target.node_item:
            return "A node cannot feed itself."
        if source.is_exec:
            definition = self.graph.definition(target.node_item.node.id)
            if not definition.exec_in:
                return f"{definition.label} cannot be put in a sequence."
            return ""
        return self.graph.check_connection(
            source.node_item.node.id, source.name,
            target.node_item.node.id, target.name)

    def connect_ports(self, a: PortItem, b: PortItem) -> bool:
        refusal = self._refusal(a, b)
        if refusal:
            self.message.emit(refusal)
            return False
        source, target = (a, b) if not a.is_input else (b, a)
        self.graph.connect(source.node_item.node.id, source.name,
                           target.node_item.node.id, target.name,
                           is_exec=source.is_exec)
        self._rebuild_rows()
        self.message.emit("")
        self.graph_changed.emit()
        return True

    # ------------------------------------------------------------ row editing

    def edit_row(self, row: RowItem) -> None:
        """Put a real line edit over the pill, so typing behaves normally."""
        self._close_editor()
        line = QtWidgets.QLineEdit(row.value())
        line.setFont(theme.ui_font(8))
        line.setStyleSheet(
            f"QLineEdit {{ background: {theme.TEXT_AREA_BG.name()};"
            f" color: {theme.ROW_VALUE_TEXT.name()};"
            f" border: 1px solid {theme.NODE_OUTLINE_SELECTED.name()};"
            f" border-radius: {theme.ROW_RADIUS}px; padding: 0 8px; }}")
        proxy = self.addWidget(line)
        proxy.setZValue(20)
        rect = row.boundingRect()
        proxy.setPos(row.mapToScene(rect.topLeft()))
        line.setFixedSize(int(rect.width()), int(rect.height()))
        self._editor = proxy

        def commit() -> None:
            row.set_value(line.text())
            # editingFinished fires synchronously from inside the QLineEdit's
            # own key handling (Return, or focus-out on click-elsewhere), so
            # this closure is still on that widget's call stack. Deleting the
            # proxy - and the QLineEdit inside it - right here is a
            # use-after-free waiting to happen; read the value now, defer the
            # actual removal to the next event-loop turn.
            QtCore.QTimer.singleShot(0, self._close_editor)

        line.editingFinished.connect(commit)
        line.selectAll()
        # A QGraphicsProxyWidget's embedded widget only receives real
        # keystrokes once the QGraphicsView itself holds actual OS keyboard
        # focus - line.setFocus() alone only sets the *scene's* logical focus
        # item. Without this, whatever widget last held real focus (the
        # assistant's Ask button, typically) keeps eating every keystroke and
        # typing into the row silently does nothing.
        views = self.views()
        view = views[0] if views else None

        def take_focus() -> None:
            # Deferred to the next event-loop turn. Inside Houdini the first
            # click on an inactive pane is spent activating it, and Houdini
            # moves focus itself as part of that - after this function would
            # otherwise have run. Setting focus now *and* again once that has
            # settled is what makes the first click enough to type, instead of
            # needing a second one.
            if self._editor is not proxy:
                return                      # the editor closed in the meantime
            window = line.window()
            if window is not None:
                window.activateWindow()
            if view is not None:
                view.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            line.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            line.selectAll()

        if view is not None:
            view.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        line.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        QtCore.QTimer.singleShot(0, take_focus)

    def edit_row_code(self, row: RowItem) -> None:
        """A resizable window for the multi-line fields.

        Deliberately not a bigger pill on the node: the canvas is for the shape
        of the graph, and a block of code pasted into it makes every other node
        harder to see. The window remembers the size it was left at.
        """
        from .codeview import VexHighlighter  # noqa: PLC0415 - avoids a cycle

        views = self.views()
        dialog = QtWidgets.QDialog(views[0] if views else None)
        dialog.setWindowTitle(f"Edit {row.label}")
        dialog.resize(*GraphScene._code_editor_size)
        dialog.setSizeGripEnabled(True)

        editor = QtWidgets.QPlainTextEdit(row.value())
        editor.setFont(theme.mono_font(10))
        editor.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setTabStopDistance(
            QtGui.QFontMetricsF(editor.font()).horizontalAdvance(" ") * 4)
        VexHighlighter(editor.document())

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(editor, 1)
        layout.addWidget(buttons)
        editor.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

        accepted = dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
        GraphScene._code_editor_size = (dialog.width(), dialog.height())
        if accepted:
            row.set_value(editor.toPlainText())
            self.value_changed(row.node_item, retyped=False)

    # Shared so the window opens at whatever size it was last left, which is
    # the closest thing to "let me size the box myself" that a dialog offers.
    _code_editor_size = (760, 460)

    @property
    def is_wiring(self) -> bool:
        """Whether a wire is being dragged or is armed waiting for a click."""
        return self._drag is not None

    @property
    def is_editing(self) -> bool:
        """Whether a field on a node currently owns the keyboard."""
        return self._editor is not None

    def _close_editor(self) -> None:
        if self._editor is not None:
            editor, self._editor = self._editor, None
            self.removeItem(editor)

    def open_row_menu(self, row: RowItem, options: list[str]) -> None:
        menu = QtWidgets.QMenu()
        current = row.value()
        for option in options:
            action = menu.addAction(option)
            action.setCheckable(True)
            action.setChecked(option == current)
        views = self.views()
        if not views:
            return
        view = views[0]
        point = view.mapToGlobal(view.mapFromScene(
            row.mapToScene(row.boundingRect().bottomLeft())))
        chosen = menu.exec(point)
        if chosen is not None:
            row.set_value(chosen.text())

    # ------------------------------------------------------------- reporting

    def show_issues(self, issues) -> None:
        """Paint problems onto the nodes that caused them."""
        for item in self.node_items.values():
            item.status = ""
            item.status_text = ""
            item.update()
        for issue in issues:
            item = self.node_items.get(issue.node_id)
            if item is None:
                continue
            if item.status == "error":
                continue
            item.status = issue.severity
            item.status_text = issue.message
            item.update()

    def _describe_selection(self) -> None:
        nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
        if len(nodes) != 1:
            self.selection_typed.emit("", "")
            return
        item = nodes[0]
        self.selection_typed.emit(item.definition.type, item.status_text)

    def selected_node_id(self) -> str:
        nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
        return nodes[0].node.id if len(nodes) == 1 else ""

    def select_node(self, node_id: str) -> None:
        self.clearSelection()
        item = self.node_items.get(node_id)
        if item is not None:
            item.setSelected(True)
            views = self.views()
            if views:
                views[0].centerOn(item)

    def drawBackground(self, painter: QtGui.QPainter,
                       rect: QtCore.QRectF) -> None:
        painter.fillRect(rect, theme.BACKGROUND)
        # A dot grid rather than lines: enough to sense the scale and the
        # movement, not enough to compete with the wires.
        step = int(theme.GRID_SPACING)      # range() below needs an int
        if painter.transform().m11() < 0.45:
            return
        left = int(rect.left()) - int(rect.left()) % step
        top = int(rect.top()) - int(rect.top()) % step
        painter.setPen(QtGui.QPen(theme.GRID_DOT, 1.6))
        points = [QtCore.QPointF(x, y)
                  for x in range(left, int(rect.right()), step)
                  for y in range(top, int(rect.bottom()), step)]
        painter.drawPoints(points)


def _owning_node(item) -> NodeItem | None:
    """Walk up from whatever was clicked to the node it belongs to."""
    while item is not None:
        if isinstance(item, NodeItem):
            return item
        item = item.parentItem()
    return None


class GraphView(QtWidgets.QGraphicsView):
    node_search_requested = QtCore.Signal(QtCore.QPointF, object)
    help_requested = QtCore.Signal(str)          # node type
    function_opened = QtCore.Signal(str)         # user-function name
    collapse_requested = QtCore.Signal()         # selection -> function
    save_function_requested = QtCore.Signal(str)  # fn name -> user library
    library_reveal_requested = QtCore.Signal(str)  # node type -> browser
    favourites_changed = QtCore.Signal()         # a node was starred/unstarred

    def __init__(self, scene: GraphScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(
            QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(
            QtWidgets.QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Effectively infinite. The scene rect is what the view lets you pan
        # across, and a large imported graph walked straight past the old
        # ±8000 edge - nodes existed that could never be scrolled to.
        self.setSceneRect(-200000, -200000, 400000, 400000)
        # A wire armed by a click has to follow the cursor while no button is
        # down, and move events only arrive with the button up if the viewport
        # is tracking.
        self.viewport().setMouseTracking(True)
        self.setAcceptDrops(True)
        self._panning = False
        self._pan_moved = False
        self._pan_origin = QtCore.QPoint()
        self._box_rubber: QtWidgets.QGraphicsRectItem | None = None
        self._box_origin = QtCore.QPointF()

    def request_node_search(self, scene_pos: QtCore.QPointF,
                            port=None) -> None:
        self.node_search_requested.emit(scene_pos, port)

    # ------------------------------------------------------------ drag & drop

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(NODE_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(NODE_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if not event.mimeData().hasFormat(NODE_MIME):
            super().dropEvent(event)
            return
        node_type = bytes(event.mimeData().data(NODE_MIME)).decode("utf-8")
        # Dropped where the cursor is, not in the middle of the view: the whole
        # point of dragging is choosing the spot.
        position = self.mapToScene(event.position().toPoint())
        self.scene().add_node(node_type, position)
        event.acceptProposedAction()
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)

    # ------------------------------------------------------------- navigation
    #
    # Houdini's network editor: wheel zooms, and either middle-drag or
    # right-drag pans. Both pan gestures are kept side by side rather than
    # replacing one with the other, since right-click has no other job here
    # (no context menu is wired to it) and alt+left-drag stays too, for
    # anyone used to the Maya-style binding instead.

    MIN_ZOOM = 0.15
    MAX_ZOOM = 5.0
    ZOOM_STEP = 1.15

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        # angleDelta is the normal mouse-wheel notch; pixelDelta is what a
        # trackpad or a high-resolution wheel reports instead, and reading
        # only angleDelta silently does nothing on those devices.
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            return
        factor = self.ZOOM_STEP if delta > 0 else 1 / self.ZOOM_STEP
        current = self.transform().m11()
        factor = max(self.MIN_ZOOM, min(self.MAX_ZOOM, current * factor)) / current
        if abs(factor - 1.0) > 1e-6:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        pan_button = event.button() in (QtCore.Qt.MouseButton.MiddleButton,
                                        QtCore.Qt.MouseButton.RightButton)
        space_pan = (event.button() == QtCore.Qt.MouseButton.LeftButton
                     and event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier)

        # Shift+drag on empty canvas draws a network box around whatever it
        # ends up hugging - the fastest way to write "these belong together".
        if (event.button() == QtCore.Qt.MouseButton.LeftButton
                and event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
                and self.itemAt(event.position().toPoint()) is None):
            self._box_origin = self.mapToScene(event.position().toPoint())
            self._box_rubber = QtWidgets.QGraphicsRectItem()
            pen = QtGui.QPen(theme.NODE_OUTLINE_SELECTED, 1,
                             QtCore.Qt.PenStyle.DashLine)
            self._box_rubber.setPen(pen)
            self._box_rubber.setZValue(30)
            self.scene().addItem(self._box_rubber)
            event.accept()
            return
        # A wire in progress outranks panning: right-click means "forget it".
        # The view has to check, because it claims the right button before the
        # scene ever sees the press.
        if (event.button() == QtCore.Qt.MouseButton.RightButton
                and self.scene().is_wiring):
            self.scene().cancel_link_drag()
            self.scene().message.emit("")
            event.accept()
            return

        if pan_button or space_pan:
            self._panning = True
            self._pan_origin = event.position().toPoint()
            self._pan_moved = False
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._box_rubber is not None:
            here = self.mapToScene(event.position().toPoint())
            self._box_rubber.setRect(
                QtCore.QRectF(self._box_origin, here).normalized())
            event.accept()
            return
        if self._panning:
            pos = event.position().toPoint()
            delta = pos - self._pan_origin
            self._pan_origin = pos
            if delta.manhattanLength() > 0:
                self._pan_moved = True
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._box_rubber is not None:
            rect = self._box_rubber.rect()
            self.scene().removeItem(self._box_rubber)
            self._box_rubber = None
            if rect.width() > 40 and rect.height() > 30:
                item = self.scene().add_box(rect)
                # Name it right away - the box exists to say something.
                title, accepted = QtWidgets.QInputDialog.getText(
                    self, "Name this group", "What do these nodes do?")
                if accepted and title.strip():
                    item.box.title = title.strip()
                    item.update()
                    self.scene().graph_changed.emit()
            event.accept()
            return
        if self._panning and event.button() in (
                QtCore.Qt.MouseButton.MiddleButton, QtCore.Qt.MouseButton.RightButton,
                QtCore.Qt.MouseButton.LeftButton):
            self._panning = False
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            # A right-click that did not drag was not a pan; it is the ordinary
            # "what can I do here" gesture, and the one way to delete a node
            # that no key interception can take away.
            if (not self._pan_moved
                    and event.button() == QtCore.Qt.MouseButton.RightButton):
                self.show_node_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        # Right-click is claimed for panning above; suppress the inherited
        # context menu so a right-click-release doesn't also pop one up.
        event.accept()

    def build_node_menu(self) -> QtWidgets.QMenu:
        """The right-click menu, built but not shown.

        Separate from showing it so its contents can be checked without a modal
        exec() that would wait forever for a person who is not there.
        """
        selected = [i for i in self.scene().selectedItems()
                    if isinstance(i, NodeItem)]
        menu = QtWidgets.QMenu(self)
        delete = menu.addAction(
            f"Delete {len(selected)} nodes" if len(selected) > 1 else "Delete node")
        delete.setEnabled(bool(selected))
        delete.setData("delete")
        collapse = menu.addAction("Collapse into a function...")
        collapse.setToolTip("Turn the selected nodes into a reusable "
                            "function; what fed them becomes its inputs.")
        collapse.setEnabled(bool(selected))
        collapse.setData("collapse")
        if len(selected) == 1:
            from .. import favourites                          # noqa: PLC0415
            node_type = selected[0].node.type
            star = menu.addAction(
                "Remove from favourites"
                if favourites.is_favourite(node_type) else "Add to favourites")
            star.setToolTip("Starred nodes come first in Tab search and get "
                            "their own shelf in the library.")
            star.setData("favourite")
            reveal = menu.addAction("Show in node library")
            reveal.setToolTip("Open this node's home in the library - its "
                              "neighbours often do related jobs.")
            reveal.setData("reveal")
        if (len(selected) == 1
                and selected[0].node.type.startswith("fn_")):
            name = selected[0].node.type[3:]
            keep = menu.addAction(f"Save {name}() to my library")
            keep.setToolTip("Keep this function in the Custom section of the "
                            "library, usable from any graph.")
            keep.setData("save_fn")
        menu.addSeparator()
        menu.addAction("Add node...").setData("add")
        return menu

    def show_node_menu(self, screen_pos: QtCore.QPoint) -> None:
        """Actions on the selection, reachable without the keyboard.

        Inside Houdini a key press can be intercepted before it reaches this
        widget, so deleting must not depend on one arriving.
        """
        chosen = self.build_node_menu().exec(screen_pos)
        if chosen is None:
            return
        if chosen.data() == "delete":
            self.scene().delete_selected()
        elif chosen.data() == "collapse":
            self.collapse_requested.emit()
        elif chosen.data() == "save_fn":
            selected = [i for i in self.scene().selectedItems()
                        if isinstance(i, NodeItem)]
            if selected:
                self.save_function_requested.emit(selected[0].node.type[3:])
        elif chosen.data() == "reveal":
            selected = [i for i in self.scene().selectedItems()
                        if isinstance(i, NodeItem)]
            if selected:
                self.library_reveal_requested.emit(selected[0].node.type)
        elif chosen.data() == "favourite":
            from .. import favourites                          # noqa: PLC0415
            selected = [i for i in self.scene().selectedItems()
                        if isinstance(i, NodeItem)]
            if selected:
                favourites.toggle(selected[0].node.type)
                self.favourites_changed.emit()
        elif chosen.data() == "add":
            self.request_node_search(
                self.mapToScene(self.mapFromGlobal(screen_pos)))

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        item = self.itemAt(event.pos())
        if item is None:
            self.request_node_search(self.mapToScene(event.pos()))
            event.accept()
            return
        # Double-clicking a wire removes it. The other way - grab the input end
        # and drop it on nothing - works but has to be discovered.
        if isinstance(item, LinkItem):
            self.scene().remove_link(item)
            event.accept()
            return

        # Double-clicking a node opens Houdini's page for the function behind
        # it - except a call to a function defined in this document, which
        # opens that function's own graph instead. Rows keep their own
        # double-click (they open an editor), so this only fires on the body
        # and title.
        if not isinstance(item, RowItem):
            node = _owning_node(item)
            if node is not None:
                if node.node.type.startswith("fn_"):
                    self.function_opened.emit(node.node.type[3:])
                else:
                    self.help_requested.emit(node.node.type)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # The keys the canvas needs Houdini to stop intercepting. Inside a Python
    # Panel, Houdini's own hotkey manager sees key presses first and swallows
    # the ones it recognises - Delete and Backspace among them - so they never
    # arrive as keyPressEvent at all. Accepting ShortcutOverride is how a widget
    # says "this one is mine", and is the only thing that makes Delete work in
    # an embedded panel.
    CLAIMED_KEYS = frozenset({
        QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace,
        QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_F, QtCore.Qt.Key.Key_Escape,
        QtCore.Qt.Key.Key_Z, QtCore.Qt.Key.Key_Y,
        QtCore.Qt.Key.Key_C, QtCore.Qt.Key.Key_X, QtCore.Qt.Key.Key_V,
    })

    def event(self, event: QtCore.QEvent) -> bool:
        if (event.type() == QtCore.QEvent.Type.ShortcutOverride
                and event.key() in self.CLAIMED_KEYS):
            event.accept()
            return True
        return super().event(event)

    def focusNextPrevChild(self, forward: bool) -> bool:
        """Keep Tab for the node list.

        Qt handles Tab inside QWidget.event() as focus navigation and never
        calls keyPressEvent for it, so the Tab handler below looked correct and
        simply never ran - the key moved focus to the next widget instead of
        opening the search. Refusing focus navigation here is what lets it
        through. There is nothing to tab between on a canvas anyway.
        """
        return False

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        # While a field on a node is open for editing, every key belongs to that
        # field. Claiming them here is why Backspace deleted the whole node
        # instead of a character, and why typing an "f" reframed the view.
        if self.scene().is_editing:
            # Escape is the one exception: a QLineEdit ignores it, and it
            # must close the editor rather than leak through to Houdini.
            if key == QtCore.Qt.Key.Key_Escape:
                self.scene()._close_editor()
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if key == QtCore.Qt.Key.Key_Escape:
            self.scene().cancel_link_drag()
            event.accept()
        elif key in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            self.scene().delete_selected()
            event.accept()
        elif key == QtCore.Qt.Key.Key_Tab:
            centre = self.mapToScene(self.viewport().rect().center())
            self.request_node_search(centre)
            event.accept()
        elif key == QtCore.Qt.Key.Key_F:
            self.frame_all()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """Draw the expression's name over the canvas, top left.

        Painted on the viewport rather than into the scene so it stays put
        while you pan and zoom - it labels the whole graph, not a place in it.
        """
        super().paintEvent(event)
        name = getattr(self.scene().graph, "name", "")
        if not name:
            return
        painter = QtGui.QPainter(self.viewport())
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        font = theme.ui_font(11, bold=True)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        box = metrics.boundingRect(name).adjusted(-10, -6, 10, 6)
        box.moveTopLeft(QtCore.QPoint(12, 12))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 90))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QtGui.QColor("#c8d4dc"))
        painter.drawText(box, QtCore.Qt.AlignmentFlag.AlignCenter, name)
        painter.end()

    def frame_all(self) -> None:
        items = [i for i in self.scene().items() if isinstance(i, NodeItem)]
        if not items:
            self.resetTransform()
            return
        bounds = items[0].sceneBoundingRect()
        for item in items[1:]:
            bounds = bounds.united(item.sceneBoundingRect())
        self.fitInView(bounds.adjusted(-60, -60, 60, 60),
                       QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.2:
            self.resetTransform()
            self.centerOn(bounds.center())
