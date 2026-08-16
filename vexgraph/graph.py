"""The document: nodes, wires, and everything that can be wrong with them.

Two kinds of wire share one structure. Data wires carry a typed value from an
output to an input. Exec wires carry *order* — they are what makes this a
Blueprint rather than a VOP network, and they are why "add a point, then colour
it" can be drawn instead of inferred.

Validation lives here rather than in the emitter because the answer has to be
available while the user is still drawing. A wire that cannot be made should be
refused as it is dragged, with a sentence saying why, not accepted and then
reported as a compiler error thirty seconds later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from . import vextypes
from .nodedefs import NodeDef, Registry

FORMAT_VERSION = 1

# The wrangle's Run Over setting. Kept on the graph because it decides which
# implicit attributes exist (@ptnum vs @primnum) and what the tier-1 nodes mean.
RUN_OVER = ("points", "primitives", "vertices", "detail", "numbers")

EXEC_PIN = "exec"

ERROR, WARNING = "error", "warning"


@dataclass
class Issue:
    message: str
    node_id: str = ""
    socket: str = ""
    severity: str = ERROR

    def __str__(self) -> str:
        where = self.node_id + (f".{self.socket}" if self.socket else "")
        return f"{where}: {self.message}" if where else self.message


@dataclass
class Node:
    id: str
    type: str
    params: dict[str, str] = field(default_factory=dict)
    title: str = ""          # user override for the header text
    pos: tuple[float, float] = (0.0, 0.0)

    def to_dict(self) -> dict:
        out: dict = {"id": self.id, "type": self.type}
        if self.params:
            out["params"] = dict(self.params)
        if self.title:
            out["title"] = self.title
        out["pos"] = [self.pos[0], self.pos[1]]
        return out


@dataclass(frozen=True)
class Link:
    from_node: str
    from_socket: str
    to_node: str
    to_socket: str
    is_exec: bool = False

    def to_dict(self) -> dict:
        out = {"from": self.from_node, "out": self.from_socket,
               "to": self.to_node, "in": self.to_socket}
        if self.is_exec:
            out["exec"] = True
        return out


@dataclass
class Box:
    """A titled rectangle that groups nodes visually - Houdini's network box.

    Membership is geometric, the way Houdini's boxes work: whatever nodes sit
    inside the rectangle move with it. The box says nothing to the emitter;
    it exists so a person can write "these compute the matrix" on the canvas.
    """
    id: str
    title: str = ""
    rect: tuple[float, float, float, float] = (0.0, 0.0, 200.0, 120.0)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "rect": list(self.rect)}

    @classmethod
    def from_dict(cls, raw: dict) -> "Box":
        return cls(id=raw["id"], title=raw.get("title", ""),
                   rect=tuple(raw.get("rect", (0, 0, 200, 120))))


@dataclass
class FunctionSignature:
    """What a user-defined function looks like from the outside."""
    name: str
    return_type: str = "void"
    returns_array: bool = False
    params: list[tuple[str, str, bool]] = field(default_factory=list)
    #        (vex type, name, is_array)

    def to_dict(self) -> dict:
        return {"name": self.name, "return": self.return_type,
                "returns_array": self.returns_array,
                "params": [list(p) for p in self.params]}

    @classmethod
    def from_dict(cls, raw: dict) -> "FunctionSignature":
        return cls(name=raw["name"], return_type=raw.get("return", "void"),
                   returns_array=bool(raw.get("returns_array", False)),
                   params=[(p[0], p[1], bool(p[2])) for p in raw.get("params", ())])


def function_call_def(signature: FunctionSignature) -> NodeDef:
    """The node a call to this function appears as.

    Document-local: it lives on the graph, never in the shared registry, so
    two documents defining different drawLine() functions cannot see each
    other's. A call is a statement (the function may do anything), with one
    input per parameter and the return value as its output.
    """
    from .nodedefs import SocketDef      # local to dodge a cycle at import
    # Each input carries its type's zero as a default, so a freshly placed
    # call - nothing wired yet - still emits provisional code instead of
    # blanking the whole document with a "needs a value" error.
    inputs = []
    for vex_type, name, is_array in signature.params:
        full = f"{vex_type}[]" if is_array else vex_type
        inputs.append(SocketDef(name, full, default=vextypes.zero(full)))
    inputs = tuple(inputs)
    args = ", ".join("{%s}" % name for _, name, _ in signature.params)
    if signature.return_type == "void":
        outputs: tuple = ()
        code = f"{signature.name}({args});"
    else:
        result = signature.return_type + ("[]" if signature.returns_array else "")
        outputs = (SocketDef("result", result),)
        code = (f"{vextypes.declaration(result, '{result}')}"
                f" = {signature.name}({args});")
    return NodeDef(
        type=f"fn_{signature.name}", label=f"{signature.name}()",
        category="Functions", kind="statement",
        summary=f"Calls the {signature.name}() function defined in this graph.",
        inputs=inputs, outputs=outputs, code=code,
        tags=("function", "call", signature.name))


class Graph:
    def __init__(self, registry: Registry, *, run_over: str = "points",
                 name: str = "") -> None:
        self.registry = registry
        self.run_over = run_over
        # What this expression is called. Shown on the canvas, carried into a
        # saved snippet, and inherited from the snippet an import came from -
        # so a graph you opened from the library still says what it is a
        # quarter of an hour later, when the shapes have stopped being a clue.
        self.name = name
        self.nodes: dict[str, Node] = {}
        self.links: list[Link] = []
        # User-defined functions: each is a whole Graph of its own, carrying
        # its signature, plus a document-local node definition so calls can
        # appear on this canvas as ordinary nodes.
        self.functions: dict[str, Graph] = {}
        self.local_defs: dict[str, NodeDef] = {}
        self.signature: FunctionSignature | None = None   # set on inner graphs
        self.boxes: list[Box] = []
        self._counter = 0

    def add_box(self, title: str,
                rect: tuple[float, float, float, float]) -> Box:
        self._counter += 1
        box = Box(id=f"box_{self._counter}", title=title, rect=rect)
        self.boxes.append(box)
        return box

    def remove_box(self, box_id: str) -> None:
        self.boxes = [b for b in self.boxes if b.id != box_id]

    def define_function(self, inner: "Graph") -> None:
        """Adopt an inner graph as this document's function."""
        assert inner.signature is not None
        self.functions[inner.signature.name] = inner
        definition = function_call_def(inner.signature)
        self.local_defs[definition.type] = definition

    # ------------------------------------------------------------- authoring

    def add(self, node_type: str, node_id: str = "", **params: str) -> Node:
        if node_type not in self.local_defs:
            self.registry.require(node_type)      # fail now, not at emit time
        if not node_id:
            self._counter += 1
            node_id = f"{node_type}_{self._counter}"
        if node_id in self.nodes:
            raise ValueError(f"duplicate node id {node_id!r}")
        node = Node(id=node_id, type=node_type,
                    params={k: str(v) for k, v in params.items()})
        self.nodes[node_id] = node
        return node

    def connect(self, from_node: str, from_socket: str,
                to_node: str, to_socket: str, *, is_exec: bool = False) -> Link:
        link = Link(from_node, from_socket, to_node, to_socket, is_exec)
        # An input takes one wire; a second replaces the first, which is what
        # dropping a wire onto an occupied socket should do.
        self.links = [x for x in self.links
                      if not (x.to_node == to_node and x.to_socket == to_socket)]
        # An exec output likewise goes to exactly one place: it is a sequence.
        if is_exec:
            self.links = [x for x in self.links
                          if not (x.is_exec and x.from_node == from_node
                                  and x.from_socket == from_socket)]
        self.links.append(link)
        return link

    def chain(self, *node_ids: str) -> None:
        """Wire an exec sequence, the common case of building a graph by hand."""
        for earlier, later in zip(node_ids, node_ids[1:]):
            self.connect(earlier, EXEC_PIN, later, EXEC_PIN, is_exec=True)

    def remove(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        self.links = [x for x in self.links
                      if node_id not in (x.from_node, x.to_node)]

    # --------------------------------------------------------------- queries

    def definition(self, node: Node | str) -> NodeDef:
        node = self.nodes[node] if isinstance(node, str) else node
        local = self.local_defs.get(node.type)
        return local if local is not None else self.registry.require(node.type)

    def param_value(self, node: Node | str, name: str) -> str:
        """A node's setting, falling back to the definition's default.

        Always go through this rather than reading `node.params` directly: a
        node only stores what has been changed, so a freshly placed one has an
        empty dict and every default still has to apply.
        """
        node = self.nodes[node] if isinstance(node, str) else node
        if name in node.params:
            return node.params[name]
        param = self.definition(node).param(name)
        return param.default if param else ""

    def socket_type(self, node: Node | str, socket: str, *,
                    is_input: bool) -> str:
        """The concrete VEX type of one socket on one node instance.

        Resolves the "any" placeholder through whichever param retypes it, so
        callers never have to know that a socket's type can be a setting.
        """
        node = self.nodes[node] if isinstance(node, str) else node
        d = self.definition(node)
        # "value.y" is the y component pin of the "value" output: every
        # vector-typed output offers its parts as float pins of their own.
        if not is_input and "." in socket:
            base, component = socket.split(".", 1)
            base_type = self.socket_type(node, base, is_input=False)
            if component in vextypes.components_of(base_type):
                return "float"
            raise KeyError(f"{node.type}: a {base_type} output has no "
                           f"{component!r} component")
        socket_def = d.input(socket) if is_input else d.output(socket)
        if socket_def is None:
            raise KeyError(f"{node.type} has no {'input' if is_input else 'output'}"
                           f" named {socket!r}")
        if socket_def.type not in (vextypes.ANY, vextypes.ANY_ARRAY):
            return socket_def.type
        param = d.retyper(socket)
        assert param is not None, "guaranteed by NodeDef validation"
        value = node.params.get(param.name, param.default)
        if socket_def.type == vextypes.ANY_ARRAY:
            # The param names the element type; this socket carries the list.
            if value not in vextypes.BASE_TYPES:
                raise ValueError(f"{node.id}: {param.title} is {value!r}, which "
                                 f"is not a type a list can hold")
            return f"{value}[]"
        if not vextypes.is_valid(value) or value in (vextypes.ANY,
                                                     vextypes.ANY_ARRAY):
            raise ValueError(
                f"{node.id}: {param.title} is {value!r}, which is not a VEX type")
        return value

    def source_of(self, node_id: str, input_socket: str) -> Link | None:
        return next((x for x in self.links
                     if not x.is_exec and x.to_node == node_id
                     and x.to_socket == input_socket), None)

    def consumers_of(self, node_id: str, output_socket: str = "") -> list[Link]:
        return [x for x in self.links
                if not x.is_exec and x.from_node == node_id
                and (not output_socket or x.from_socket == output_socket)]

    def exec_next(self, node_id: str, pin: str = EXEC_PIN) -> str | None:
        link = next((x for x in self.links
                     if x.is_exec and x.from_node == node_id
                     and x.from_socket == pin), None)
        return link.to_node if link else None

    def exec_sequence(self, start_node: str, pin: str = EXEC_PIN) -> list[str]:
        """Follow an exec pin to the end of its chain.

        Guards against a loop in the chain: the emitter would otherwise walk it
        until it ran out of memory, and a user can absolutely draw one.
        """
        out: list[str] = []
        seen: set[str] = set()
        current = self.exec_next(start_node, pin)
        while current and current not in seen:
            seen.add(current)
            out.append(current)
            current = self.exec_next(current)
        return out

    def entry_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if not self.definition(n).exec_in]

    def __iter__(self) -> Iterator[Node]:
        return iter(self.nodes.values())

    # ------------------------------------------------------------ validation

    def check_connection(self, from_node: str, from_socket: str,
                         to_node: str, to_socket: str) -> str:
        """Empty string if the wire is legal, otherwise why it is not.

        Called while a wire is being dragged, so it must never raise.
        """
        try:
            src = self.socket_type(from_node, from_socket, is_input=False)
            dst = self.socket_type(to_node, to_socket, is_input=True)
        except (KeyError, ValueError) as exc:
            return str(exc)
        if from_node == to_node:
            return "A node cannot feed itself."
        if not vextypes.can_connect(src, dst):
            return vextypes.explain_mismatch(src, dst)
        if self._would_cycle(from_node, to_node):
            return ("This would make a loop: the value would depend on itself. "
                    "Use a For Each node if you meant to repeat something.")
        return ""

    def _would_cycle(self, from_node: str, to_node: str) -> bool:
        """True if `from_node` already depends on `to_node`."""
        stack, seen = [from_node], set()
        while stack:
            current = stack.pop()
            if current == to_node:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack += [x.from_node for x in self.links
                      if not x.is_exec and x.to_node == current]
        return False

    def validate(self) -> list[Issue]:
        issues: list[Issue] = []

        entries = self.entry_nodes()
        if not entries:
            issues.append(Issue("The graph has no Start node, so nothing runs."))
        elif len(entries) > 1:
            issues.append(Issue(
                f"There are {len(entries)} Start nodes. Only one graph can run; "
                "delete the extras."))

        for node in self.nodes.values():
            issues += self._check_node(node)

        for link in self.links:
            issues += self._check_link(link)

        issues += self._check_unreachable()
        return issues

    def _check_node(self, node: Node) -> list[Issue]:
        issues: list[Issue] = []
        d = self.definition(node)

        for param in d.params:
            value = node.params.get(param.name, param.default)
            if param.kind == "menu" and value not in param.menu:
                issues.append(Issue(
                    f"{param.title} is {value!r}; expected one of "
                    f"{', '.join(param.menu)}.", node.id))
            if param.kind == "vextype" and not vextypes.is_valid(value):
                issues.append(Issue(
                    f"{param.title} is {value!r}, which is not a VEX type.",
                    node.id))
            if param.kind == "attrib" and not value:
                issues.append(Issue(
                    f"{param.title} is empty: name the attribute to use.",
                    node.id))

        for socket in d.inputs:
            # An unwired socket is fine if it has a default or the user typed a
            # value into it, which is how most sockets are actually filled in.
            if (self.source_of(node.id, socket.name) is None
                    and socket.default is None
                    and socket.name not in node.params):
                issues.append(Issue(
                    f"{socket.title} needs a value or a connection.",
                    node.id, socket.name))

        if d.has_exec and d.exec_in:
            incoming = any(x.is_exec and x.to_node == node.id for x in self.links)
            if not incoming:
                issues.append(Issue(
                    "Nothing runs this node: connect it into the sequence.",
                    node.id, EXEC_PIN, WARNING))
        return issues

    def _check_link(self, link: Link) -> list[Issue]:
        if link.from_node not in self.nodes or link.to_node not in self.nodes:
            return [Issue("A wire points at a node that no longer exists.")]
        if link.is_exec:
            return self._check_exec_link(link)
        try:
            src = self.socket_type(link.from_node, link.from_socket, is_input=False)
            dst = self.socket_type(link.to_node, link.to_socket, is_input=True)
        except (KeyError, ValueError) as exc:
            return [Issue(str(exc), link.to_node, link.to_socket)]
        if not vextypes.can_connect(src, dst):
            return [Issue(vextypes.explain_mismatch(src, dst),
                          link.to_node, link.to_socket)]
        return []

    def _check_exec_link(self, link: Link) -> list[Issue]:
        source_def = self.definition(link.from_node)
        target_def = self.definition(link.to_node)
        valid_out = {EXEC_PIN, *source_def.exec_bodies}
        if link.from_socket not in valid_out or not source_def.has_exec:
            return [Issue(f"{source_def.label} has no {link.from_socket!r} "
                          f"output to run from.", link.from_node)]
        if not target_def.has_exec or not target_def.exec_in:
            return [Issue(f"{target_def.label} cannot be put in a sequence.",
                          link.to_node)]
        return []

    def _check_unreachable(self) -> list[Issue]:
        """Statement nodes the exec chain never arrives at.

        A pure node off to one side is fine — it is just unused. A statement
        node that is wired up but never reached is a silent no-op, and looks
        exactly like a working graph.
        """
        reached: set[str] = set()
        for entry in self.entry_nodes():
            stack = [entry.id]
            while stack:
                current = stack.pop()
                if current in reached:
                    continue
                reached.add(current)
                d = self.definition(current)
                for pin in (EXEC_PIN, *d.exec_bodies):
                    nxt = self.exec_next(current, pin)
                    if nxt:
                        stack.append(nxt)
                # Follow the rest of each body's chain too.
                for pin in (EXEC_PIN, *d.exec_bodies):
                    stack += self.exec_sequence(current, pin)

        return [Issue("This node never runs: it is not connected to the Start "
                      "node's sequence.", node.id, EXEC_PIN, WARNING)
                for node in self.nodes.values()
                if self.definition(node).has_exec and node.id not in reached]

    # ------------------------------------------------------- serialisation

    def to_dict(self) -> dict:
        out = {
            "version": FORMAT_VERSION,
            "run_over": self.run_over,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "links": [x.to_dict() for x in self.links],
        }
        if self.name:
            out["name"] = self.name
        if self.signature is not None:
            out["signature"] = self.signature.to_dict()
        if self.functions:
            out["functions"] = {name: inner.to_dict()
                                for name, inner in self.functions.items()}
        if self.boxes:
            out["boxes"] = [b.to_dict() for b in self.boxes]
        return out

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, raw: dict, registry: Registry) -> Graph:
        version = raw.get("version", FORMAT_VERSION)
        if version > FORMAT_VERSION:
            raise ValueError(
                f"This graph was saved by a newer version of VEXgraph "
                f"(format {version}, this build reads {FORMAT_VERSION}).")
        graph = cls(registry, run_over=raw.get("run_over", "points"),
                    name=raw.get("name", ""))
        for entry in raw.get("nodes", ()):
            node = Node(
                id=entry["id"],
                type=entry["type"],
                params={k: str(v) for k, v in entry.get("params", {}).items()},
                title=entry.get("title", ""),
                pos=tuple(entry.get("pos", (0.0, 0.0))),
            )
            graph.nodes[node.id] = node
        for entry in raw.get("links", ()):
            graph.links.append(Link(
                from_node=entry["from"], from_socket=entry["out"],
                to_node=entry["to"], to_socket=entry["in"],
                is_exec=bool(entry.get("exec", False)),
            ))
        if "signature" in raw:
            graph.signature = FunctionSignature.from_dict(raw["signature"])
        for inner_raw in raw.get("functions", {}).values():
            graph.define_function(cls.from_dict(inner_raw, registry))
        graph.boxes = [Box.from_dict(b) for b in raw.get("boxes", ())]
        return graph

    @classmethod
    def load(cls, path: Path, registry: Registry) -> Graph:
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8")), registry)


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == ERROR]
