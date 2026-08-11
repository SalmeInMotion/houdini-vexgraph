"""Arrange a graph that has no positions of its own.

Needed more than it looks. A graph built in Python, loaded from an older file,
or - shortly - written by the assistant arrives as a pile of nodes at the
origin. Left like that the tool appears broken before it has done anything.

This is a small layered layout: columns come from how far a node is from a
source, rows from the average height of what feeds it, which is the cheap and
well-behaved way to keep wires from crossing. It is not trying to be optimal,
it is trying to be immediately readable.
"""

from __future__ import annotations

from PySide6 import QtCore

from ..graph import Graph

COLUMN_GAP = 70
ROW_GAP = 26
BARYCENTRE_PASSES = 4


def needs_arranging(graph: Graph) -> bool:
    """True when the nodes are all sitting on top of each other."""
    positions = {(round(n.pos[0]), round(n.pos[1])) for n in graph.nodes.values()}
    return len(positions) <= 1 and len(graph.nodes) > 1


def arrange(node_items: dict[str, object], graph: Graph) -> None:
    """Place every node. `node_items` supplies the real on-screen sizes."""
    if not node_items:
        return

    columns = _assign_columns(graph)
    order = _order_within_columns(graph, columns)

    x = 0.0
    for column in sorted(order):
        ids = order[column]
        widths = [node_items[i].boundingRect().width() for i in ids
                  if i in node_items]
        column_width = max(widths) if widths else 200.0

        total = sum(node_items[i].boundingRect().height() for i in ids
                    if i in node_items) + ROW_GAP * (len(ids) - 1)
        y = -total / 2
        for node_id in ids:
            item = node_items.get(node_id)
            if item is None:
                continue
            height = item.boundingRect().height()
            # Centre each node in its column so wires arrive level.
            offset = (column_width - item.boundingRect().width()) / 2
            item.setPos(QtCore.QPointF(x + offset, y))
            graph.nodes[node_id].pos = (x + offset, y)
            y += height + ROW_GAP
        x += column_width + COLUMN_GAP


def _assign_columns(graph: Graph) -> dict[str, int]:
    """Longest path from a source, over data and run-order wires together."""
    incoming: dict[str, list[str]] = {n: [] for n in graph.nodes}
    for link in graph.links:
        if link.to_node in incoming and link.from_node in graph.nodes:
            incoming[link.to_node].append(link.from_node)

    columns: dict[str, int] = {}
    resolving: set[str] = set()

    def depth(node_id: str) -> int:
        if node_id in columns:
            return columns[node_id]
        if node_id in resolving:
            return 0                       # a cycle; the emitter reports it
        resolving.add(node_id)
        sources = incoming.get(node_id, ())
        value = max((depth(s) + 1 for s in sources), default=0)
        resolving.discard(node_id)
        columns[node_id] = value
        return value

    for node_id in graph.nodes:
        depth(node_id)
    return columns


def _order_within_columns(graph: Graph,
                          columns: dict[str, int]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for node_id, column in columns.items():
        grouped.setdefault(column, []).append(node_id)
    for ids in grouped.values():
        ids.sort()

    positions = {node_id: float(index)
                 for ids in grouped.values()
                 for index, node_id in enumerate(ids)}

    # Repeatedly pull each node towards the average row of its inputs. A few
    # passes is enough to untangle the common cases and it cannot oscillate.
    for _ in range(BARYCENTRE_PASSES):
        for column in sorted(grouped):
            if column == 0:
                continue
            weights = {}
            for node_id in grouped[column]:
                sources = [positions[l.from_node] for l in graph.links
                           if l.to_node == node_id and l.from_node in positions]
                weights[node_id] = (sum(sources) / len(sources) if sources
                                    else positions[node_id])
            grouped[column].sort(key=lambda n: weights[n])
            for index, node_id in enumerate(grouped[column]):
                positions[node_id] = float(index)
    return grouped
