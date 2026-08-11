"""The things you see on the canvas: nodes, their ports, their value rows, wires.

Everything a node shows is derived from its NodeDef, so adding a node to the
JSON library makes it appear here with no UI work. That is the whole reason the
library is data.

Value rows are the ComfyUI idea and they carry a lot of weight for this
audience: an unconnected input shows its literal value inline, editable in
place, and the moment a wire arrives the row disappears because the wire now
supplies it. There is no properties panel to go and find.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import vextypes
from ..graph import EXEC_PIN, Graph, Node
from ..nodedefs import NodeDef, ParamDef, SocketDef
from . import icons, theme


def retire(items: list) -> None:
    """Drop these graphics items on the *next* event-loop turn, not this one.

    Removing a QGraphicsItem from its scene and dropping the last Python
    reference in the same turn destroys the underlying C++ object while Qt
    still has pending work that points at it - a queued repaint, a scene-index
    update. The next time the event loop runs it walks freed memory and the
    process dies, taking Houdini with it.

    Rebuilding a node tears down and recreates every port and row, so this is
    the hot path for that failure: it showed up as "changing the text size
    closed Houdini". Handing the detached items to a list that a zero-delay
    timer clears keeps them alive exactly long enough.
    """
    if not items:
        return
    batch = list(items)
    QtCore.QTimer.singleShot(0, batch.clear)


def _elide(text: str, font: QtGui.QFont, width: float) -> str:
    return QtGui.QFontMetricsF(font).elidedText(
        text, QtCore.Qt.TextElideMode.ElideRight, width)


def _text_width(text: str, font: QtGui.QFont) -> float:
    return QtGui.QFontMetricsF(font).horizontalAdvance(text)


# --------------------------------------------------------------------- ports

class PortItem(QtWidgets.QGraphicsItem):
    """One socket. Data ports are round, lists square, exec pins arrows."""

    def __init__(self, node_item: "NodeItem", name: str, label: str,
                 vex_type: str, *, is_input: bool, is_exec: bool = False):
        super().__init__(node_item)
        self.node_item = node_item
        self.name = name
        self.label = label
        self.vex_type = vex_type
        self.is_input = is_input
        self.is_exec = is_exec
        self.hovered = False
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    # Geometry is a fat invisible box around the dot: a 5px circle is not
    # something anyone can reliably hit with a mouse.
    def boundingRect(self) -> QtCore.QRectF:
        half = theme.PORT_HIT
        return QtCore.QRectF(-half, -half, half * 2, half * 2)

    @property
    def colour(self) -> QtGui.QColor:
        return theme.EXEC_COLOUR if self.is_exec else theme.type_colour(self.vex_type)

    def scene_anchor(self) -> QtCore.QPointF:
        return self.mapToScene(QtCore.QPointF(0, 0))

    def hoverEnterEvent(self, event) -> None:
        self.hovered = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        self.hovered = False
        self.update()

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colour = self.colour
        grow = 1.6 if self.hovered else 0.0
        painter.setPen(QtGui.QPen(colour.darker(160), 1))
        painter.setBrush(colour)

        if self.is_exec:
            size = theme.EXEC_SIZE + grow
            arrow = QtGui.QPolygonF([
                QtCore.QPointF(-size * 0.8, -size),
                QtCore.QPointF(size * 0.9, 0),
                QtCore.QPointF(-size * 0.8, size),
            ])
            painter.drawPolygon(arrow)
        elif theme.is_list(self.vex_type):
            size = theme.PORT_RADIUS + grow
            painter.drawRoundedRect(
                QtCore.QRectF(-size, -size, size * 2, size * 2), 1.5, 1.5)
        else:
            size = theme.PORT_RADIUS + grow
            painter.drawEllipse(QtCore.QPointF(0, 0), size, size)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            scene = self.scene()
            if hasattr(scene, "begin_link_drag"):
                scene.begin_link_drag(self, event.scenePos())
                event.accept()
                return
        super().mousePressEvent(event)


# ---------------------------------------------------------------- value rows

class RowItem(QtWidgets.QGraphicsItem):
    """A pill showing one editable value inside the node."""

    def __init__(self, node_item: "NodeItem", key: str, label: str,
                 width: float):
        super().__init__(node_item)
        self.node_item = node_item
        self.key = key
        self.label = label
        self._width = width
        self.hovered = False
        self.setAcceptHoverEvents(True)
        self.setZValue(1)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0, 0, self._width, theme.ROW_HEIGHT)

    def value(self) -> str:
        return self.node_item.value_of(self.key)

    def set_value(self, text: str) -> None:
        self.node_item.set_value(self.key, text)
        self.update()

    def claim_selection(self) -> None:
        """Select the node this row belongs to.

        Rows cover most of a node's surface and each one accepts its own
        clicks, so without this a click almost anywhere on a node left the node
        unselected - and Delete, which acts on the selection, did nothing. Only
        the thin title bar ever selected anything.
        """
        scene = self.scene()
        if scene is not None and not self.node_item.isSelected():
            scene.clearSelection()
            self.node_item.setSelected(True)

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.claim_selection()
        super().mousePressEvent(event)

    def hoverEnterEvent(self, event) -> None:
        self.hovered = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        self.hovered = False
        self.update()

    def _paint_pill(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(theme.ROW_BG_HOVER if self.hovered else theme.ROW_BG)
        painter.drawRoundedRect(self.boundingRect(), theme.ROW_RADIUS,
                                theme.ROW_RADIUS)

    def _paint_label_and_value(self, painter: QtGui.QPainter,
                               arrows: bool) -> None:
        font = theme.ui_font(8)
        painter.setFont(font)
        inset = 18 if arrows else 10
        rect = self.boundingRect().adjusted(inset, 0, -inset, 0)

        value = self.value()
        value_width = min(_text_width(value, font) + 6, rect.width() * 0.68)
        label_width = max(rect.width() - value_width - 6, 10)

        painter.setPen(theme.ROW_TEXT)
        painter.drawText(
            QtCore.QRectF(rect.left(), rect.top(), label_width, rect.height()),
            int(QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter),
            _elide(self.label, font, label_width))

        painter.setPen(theme.ROW_VALUE_TEXT)
        painter.drawText(
            QtCore.QRectF(rect.right() - value_width, rect.top(), value_width,
                          rect.height()),
            int(QtCore.Qt.AlignmentFlag.AlignRight
                | QtCore.Qt.AlignmentFlag.AlignVCenter),
            _elide(value, font, value_width))

        if arrows:
            painter.setPen(QtGui.QPen(theme.ROW_ARROW, 1.4))
            painter.setBrush(theme.ROW_ARROW)
            middle = self.boundingRect().center().y()
            left = QtGui.QPolygonF([
                QtCore.QPointF(11, middle - 3.5), QtCore.QPointF(7, middle),
                QtCore.QPointF(11, middle + 3.5)])
            right_x = self._width - 11
            right = QtGui.QPolygonF([
                QtCore.QPointF(right_x, middle - 3.5),
                QtCore.QPointF(right_x + 4, middle),
                QtCore.QPointF(right_x, middle + 3.5)])
            painter.drawPolygon(left)
            painter.drawPolygon(right)

    def _arrow_hit(self, pos: QtCore.QPointF) -> int:
        if pos.x() < 18:
            return -1
        if pos.x() > self._width - 18:
            return 1
        return 0


class NumberRow(RowItem):
    """A number, nudged with the arrows, scrubbed by dragging, typed on click."""

    def __init__(self, node_item, key, label, width, *, integer: bool):
        super().__init__(node_item, key, label, width)
        self.integer = integer
        self._drag_origin: QtCore.QPointF | None = None
        self._drag_start_value = 0.0
        self._moved = False
        self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)

    def paint(self, painter, option, widget=None) -> None:
        self._paint_pill(painter)
        self._paint_label_and_value(painter, arrows=True)

    def _as_number(self) -> float:
        try:
            return float(self.value())
        except ValueError:
            return 0.0

    def _format(self, number: float) -> str:
        if self.integer:
            return str(int(round(number)))
        text = f"{number:.4f}".rstrip("0").rstrip(".")
        return text or "0"

    def _step(self) -> float:
        return 1.0 if self.integer else 0.1

    def mousePressEvent(self, event) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.claim_selection()
        direction = self._arrow_hit(event.pos())
        if direction:
            self.set_value(self._format(self._as_number()
                                        + direction * self._step()))
            event.accept()
            return
        self._drag_origin = event.scenePos()
        self._drag_start_value = self._as_number()
        self._moved = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is None:
            return
        delta = event.scenePos().x() - self._drag_origin.x()
        if abs(delta) < 3 and not self._moved:
            return
        self._moved = True
        self.set_value(self._format(
            self._drag_start_value + delta * self._step() * 0.5))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        # A click that never became a drag means "let me type it".
        if self._drag_origin is not None and not self._moved:
            self.node_item.edit_row_text(self)
        self._drag_origin = None
        event.accept()


class MenuRow(RowItem):
    """A fixed set of choices: the arrows step through, a click opens the list."""

    def __init__(self, node_item, key, label, width, options: list[str]):
        super().__init__(node_item, key, label, width)
        self.options = options
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def paint(self, painter, option, widget=None) -> None:
        self._paint_pill(painter)
        self._paint_label_and_value(painter, arrows=True)

    def mousePressEvent(self, event) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self.options:
            super().mousePressEvent(event)
            return
        self.claim_selection()
        direction = self._arrow_hit(event.pos())
        if direction:
            try:
                index = self.options.index(self.value())
            except ValueError:
                index = 0
            self.set_value(self.options[(index + direction) % len(self.options)])
        else:
            self.node_item.open_menu(self, self.options)
        event.accept()


class TextRow(RowItem):
    """Free text: attribute names, variable names, comments."""

    def __init__(self, node_item, key, label, width, *, mono: bool = False):
        super().__init__(node_item, key, label, width)
        self.mono = mono
        self.setCursor(QtCore.Qt.CursorShape.IBeamCursor)

    def paint(self, painter, option, widget=None) -> None:
        self._paint_pill(painter)
        self._paint_label_and_value(painter, arrows=False)

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.claim_selection()
            self.node_item.edit_row_text(self)
            event.accept()
            return
        super().mousePressEvent(event)


# --------------------------------------------------------------------- nodes

class NodeItem(QtWidgets.QGraphicsItem):
    def __init__(self, graph: Graph, node: Node):
        super().__init__()
        self.graph = graph
        self.node = node
        self.definition: NodeDef = graph.definition(node)
        self.ports: dict[tuple[str, bool], PortItem] = {}
        self.rows: list[RowItem] = []
        self._width = theme.NODE_MIN_WIDTH
        self._height = theme.TITLE_HEIGHT
        self.status = ""          # "", "error" or "warning"
        self.status_text = ""

        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(QtCore.QPointF(*node.pos))
        self.rebuild()

    # ------------------------------------------------------------- geometry

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(-2, -2, self._width + 4, self._height + 4)

    @property
    def title(self) -> str:
        return self.node.title or self.definition.label

    def _measure_width(self) -> float:
        title_font = theme.ui_font(9, bold=False)
        label_font = theme.ui_font(8)
        widest = _text_width(self.title, title_font) + 46

        inputs, outputs = self._port_columns()
        for index in range(max(len(inputs), len(outputs))):
            left = inputs[index].title if index < len(inputs) else ""
            right = outputs[index].title if index < len(outputs) else ""
            widest = max(widest, _text_width(left, label_font)
                         + _text_width(right, label_font) + 62)

        for label, value in self._row_specs():
            widest = max(widest, _text_width(label, label_font)
                         + _text_width(value, label_font) + 62)

        return max(theme.NODE_MIN_WIDTH, min(theme.NODE_MAX_WIDTH, widest))

    def _port_columns(self) -> tuple[list[SocketDef], list[SocketDef]]:
        """Inputs that get a row of their own are not listed again as ports.

        Their dot is drawn on the left edge of their value row instead, the way
        ComfyUI does it. Listing the name twice wastes a line and reads as if
        there were two different things called Radius.
        """
        ports = [s for s in self.definition.inputs if not self._shows_row(s)]
        return ports, list(self.definition.outputs)

    def _row_specs(self) -> list[tuple[str, str]]:
        """Label and current value for every row this node will show."""
        specs = [(p.title, self.value_of(f"param:{p.name}"))
                 for p in self.definition.params]
        for socket in self.definition.inputs:
            if self._shows_row(socket):
                specs.append((socket.title, self.value_of(f"in:{socket.name}")))
        return specs

    def _shows_row(self, socket: SocketDef) -> bool:
        """An unconnected input shows its value; a connected one does not.

        The wire is the value once there is a wire, and leaving a stale number
        visible underneath it is the fastest way to make someone distrust the
        whole tool.
        """
        if self.graph.source_of(self.node.id, socket.name) is not None:
            return False
        return socket.default is not None or socket.name in self.node.params

    # -------------------------------------------------------------- building

    def rebuild(self) -> None:
        """Recreate ports and rows. Cheap enough to do on any change."""
        detached = []
        for child in list(self.childItems()):
            child.setParentItem(None)
            if child.scene():
                child.scene().removeItem(child)
            detached.append(child)
        self.ports.clear()
        self.rows.clear()
        retire(detached)        # freeing these now would crash the next repaint

        self.prepareGeometryChange()
        self._width = self._measure_width()
        y = theme.TITLE_HEIGHT + 6

        if self.definition.has_exec:
            if self.definition.exec_in:
                self._add_port(EXEC_PIN, "", "", is_input=True, is_exec=True,
                               y=y)
            self._add_port(EXEC_PIN, "", "", is_input=False, is_exec=True, y=y)
            y += theme.PORT_ROW_HEIGHT

        inputs, outputs = self._port_columns()
        for index in range(max(len(inputs), len(outputs))):
            if index < len(inputs):
                socket = inputs[index]
                self._add_port(socket.name, socket.title,
                               self._socket_type(socket.name, True),
                               is_input=True, is_exec=False, y=y)
            if index < len(outputs):
                socket = outputs[index]
                self._add_port(socket.name, socket.title,
                               self._socket_type(socket.name, False),
                               is_input=False, is_exec=False, y=y)
            y += theme.PORT_ROW_HEIGHT

        for body in self.definition.exec_bodies:
            self._add_port(body, body.title(), "", is_input=False,
                           is_exec=True, y=y)
            y += theme.PORT_ROW_HEIGHT

        if self.ports:
            y += 4
        for row, socket in self._build_rows():
            row.setPos(theme.PADDING_X, y)
            self.rows.append(row)
            if socket is not None:
                # No label: the row beside it already says what it is.
                self._add_port(
                    socket.name, "", self._socket_type(socket.name, True),
                    is_input=True, is_exec=False,
                    y=y + theme.ROW_HEIGHT / 2 - theme.PORT_ROW_HEIGHT / 2)
            y += theme.ROW_HEIGHT + theme.ROW_GAP

        self._height = y + theme.PADDING_BOTTOM - (
            theme.ROW_GAP if self.rows else 0)
        self.update()

    def _socket_type(self, name: str, is_input: bool) -> str:
        try:
            return self.graph.socket_type(self.node, name, is_input=is_input)
        except (KeyError, ValueError):
            return ""

    def _add_port(self, name: str, label: str, vex_type: str, *,
                  is_input: bool, is_exec: bool, y: float) -> PortItem:
        port = PortItem(self, name, label, vex_type, is_input=is_input,
                        is_exec=is_exec)
        port.setPos(0 if is_input else self._width, y + theme.PORT_ROW_HEIGHT / 2)
        self.ports[(name, is_input)] = port
        return port

    def _build_rows(self) -> list[tuple[RowItem, SocketDef | None]]:
        """Each row, paired with the input socket it stands for, if any."""
        width = self._width - theme.PADDING_X * 2
        rows: list[tuple[RowItem, SocketDef | None]] = []

        for param in self.definition.params:
            rows.append((self._row_for_param(param, width), None))
        for socket in self.definition.inputs:
            if self._shows_row(socket):
                rows.append((self._row_for_socket(socket, width), socket))
        return rows

    def _row_for_param(self, param: ParamDef, width: float) -> RowItem:
        key = f"param:{param.name}"
        if param.kind == "menu":
            return MenuRow(self, key, param.title, width, list(param.menu))
        if param.kind == "vextype":
            return MenuRow(self, key, param.title, width,
                           [t for t in vextypes.ALL_TYPES
                            if t not in (vextypes.ANY, vextypes.ANY_ARRAY)])
        if param.kind in ("int", "float"):
            return NumberRow(self, key, param.title, width,
                             integer=param.kind == "int")
        return TextRow(self, key, param.title, width)

    def _row_for_socket(self, socket: SocketDef, width: float) -> RowItem:
        key = f"in:{socket.name}"
        vex_type = self._socket_type(socket.name, True)
        # A vector's literal is `{0, 0, 0}` and a scrubber cannot help with
        # that, so anything that is not a plain number gets a text field.
        if vex_type in ("int", "float"):
            return NumberRow(self, key, socket.title, width,
                             integer=vex_type == "int")
        return TextRow(self, key, socket.title, width)

    # ---------------------------------------------------------------- values

    def value_of(self, key: str) -> str:
        kind, _, name = key.partition(":")
        if kind == "param":
            return self.graph.param_value(self.node, name)
        socket = self.definition.input(name)
        if name in self.node.params:
            return self.node.params[name]
        if socket is not None and socket.default is not None:
            return socket.default
        return ""

    def set_value(self, key: str, text: str) -> None:
        kind, _, name = key.partition(":")
        self.node.params[name] = text
        scene = self.scene()
        if scene is not None and hasattr(scene, "value_changed"):
            # A param can retype a socket, which changes what may connect to
            # it, so the node is rebuilt rather than just repainted.
            scene.value_changed(self, retyped=kind == "param")

    def edit_row_text(self, row: RowItem) -> None:
        scene = self.scene()
        if scene is not None and hasattr(scene, "edit_row"):
            scene.edit_row(row)

    def open_menu(self, row: RowItem, options: list[str]) -> None:
        scene = self.scene()
        if scene is not None and hasattr(scene, "open_row_menu"):
            scene.open_row_menu(row, options)

    # --------------------------------------------------------------- drawing

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        body = QtCore.QRectF(0, 0, self._width, self._height)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(theme.NODE_SHADOW)
        painter.drawRoundedRect(body.translated(0, 2), theme.NODE_RADIUS,
                                theme.NODE_RADIUS)

        painter.setBrush(theme.NODE_BODY_SELECTED if self.isSelected()
                         else theme.NODE_BODY)
        painter.drawRoundedRect(body, theme.NODE_RADIUS, theme.NODE_RADIUS)

        outline = None
        if self.status == "error":
            outline = theme.NODE_ERROR
        elif self.status == "warning":
            outline = theme.NODE_WARNING
        elif self.isSelected():
            outline = theme.NODE_OUTLINE_SELECTED
        if outline is not None:
            painter.setPen(QtGui.QPen(outline, 1.6))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body.adjusted(0.8, 0.8, -0.8, -0.8),
                                    theme.NODE_RADIUS, theme.NODE_RADIUS)

        self._paint_title(painter)
        self._paint_port_labels(painter)

    def _paint_title(self, painter: QtGui.QPainter) -> None:
        font = theme.ui_font(9)
        painter.setFont(font)
        left = theme.PADDING_X + 14

        # Houdini's own icon where there is one, so a node is recognisable at a
        # glance and at a zoom where the label is unreadable. The status dot
        # takes its place otherwise, and still wins when something is wrong.
        size = int(theme.TITLE_HEIGHT * 0.62)
        icon = (icons.node_icon(self.node.type, size)
                if self.status != "error" else None)
        if icon is not None:
            painter.drawPixmap(
                QtCore.QPointF(theme.PADDING_X - 2,
                               (theme.TITLE_HEIGHT - size) / 2), icon)
            left = theme.PADDING_X + size + 2
        else:
            dot = QtCore.QPointF(theme.PADDING_X + 2, theme.TITLE_HEIGHT / 2)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(theme.NODE_LABEL_TEXT if self.status != "error"
                             else theme.NODE_ERROR)
            painter.drawEllipse(dot, 3.2, 3.2)

        painter.setPen(theme.NODE_TITLE_TEXT)
        rect = QtCore.QRectF(left, 0,
                             self._width - left - 6,
                             theme.TITLE_HEIGHT)
        painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignLeft
                                   | QtCore.Qt.AlignmentFlag.AlignVCenter),
                         _elide(self.title, font, rect.width()))

    def _paint_port_labels(self, painter: QtGui.QPainter) -> None:
        font = theme.ui_font(8)
        painter.setFont(font)
        painter.setPen(theme.NODE_LABEL_TEXT)
        for (name, is_input), port in self.ports.items():
            if not port.label:
                continue
            y = port.pos().y() - theme.PORT_ROW_HEIGHT / 2
            if is_input:
                rect = QtCore.QRectF(14, y, self._width - 28,
                                     theme.PORT_ROW_HEIGHT)
                align = QtCore.Qt.AlignmentFlag.AlignLeft
            else:
                rect = QtCore.QRectF(14, y, self._width - 28,
                                     theme.PORT_ROW_HEIGHT)
                align = QtCore.Qt.AlignmentFlag.AlignRight
            painter.drawText(rect, int(align
                                       | QtCore.Qt.AlignmentFlag.AlignVCenter),
                             _elide(port.label, font, rect.width()))

    # ---------------------------------------------------------------- events

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.pos = (self.pos().x(), self.pos().y())
            scene = self.scene()
            if scene is not None and hasattr(scene, "node_moved"):
                scene.node_moved(self)
        return super().itemChange(change, value)


# --------------------------------------------------------------------- links

class LinkItem(QtWidgets.QGraphicsPathItem):
    """A wire, coloured by what it carries, with ComfyUI's midpoint dot."""

    def __init__(self, source: PortItem, target: PortItem, is_exec: bool):
        super().__init__()
        self.source = source
        self.target = target
        self.is_exec = is_exec
        self.setZValue(-1)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self._midpoint = QtCore.QPointF()
        self.refresh()

    @property
    def colour(self) -> QtGui.QColor:
        return theme.EXEC_COLOUR if self.is_exec else theme.type_colour(
            self.source.vex_type)

    def refresh(self) -> None:
        start = self.source.scene_anchor()
        end = self.target.scene_anchor()
        self.setPath(bezier(start, end))
        self._midpoint = self.path().pointAtPercent(0.5)

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        width = theme.LINK_WIDTH_EXEC if self.is_exec else theme.LINK_WIDTH
        colour = self.colour
        if self.isSelected():
            colour = colour.lighter(150)
            width += 1
        painter.setPen(QtGui.QPen(colour, width, QtCore.Qt.PenStyle.SolidLine,
                                  QtCore.Qt.PenCapStyle.RoundCap))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(self._midpoint, theme.LINK_DOT, theme.LINK_DOT)

    def shape(self) -> QtGui.QPainterPath:
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())


def bezier(start: QtCore.QPointF, end: QtCore.QPointF) -> QtGui.QPainterPath:
    """A horizontal-tangent curve, so wires leave and arrive sideways."""
    path = QtGui.QPainterPath(start)
    distance = abs(end.x() - start.x())
    reach = max(40.0, min(distance * 0.6, 180.0))
    path.cubicTo(QtCore.QPointF(start.x() + reach, start.y()),
                 QtCore.QPointF(end.x() - reach, end.y()), end)
    return path


class DragLink(QtWidgets.QGraphicsPathItem):
    """The wire that follows the cursor while a connection is being made."""

    def __init__(self, port: PortItem):
        super().__init__()
        self.port = port
        self.setZValue(10)
        self.refused = False
        self._end = port.scene_anchor()

    def drag_to(self, point: QtCore.QPointF, refused: bool = False) -> None:
        self._end = point
        self.refused = refused
        anchor = self.port.scene_anchor()
        if self.port.is_input:
            self.setPath(bezier(point, anchor))
        else:
            self.setPath(bezier(anchor, point))
        self.update()

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colour = theme.NODE_ERROR if self.refused else self.port.colour
        pen = QtGui.QPen(colour, theme.LINK_WIDTH, QtCore.Qt.PenStyle.DashLine
                         if self.refused else QtCore.Qt.PenStyle.SolidLine)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())
