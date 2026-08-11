"""What a node *is*: sockets, a code template, and enough prose to find it.

Nodes are data, not Python. A definition is a JSON object, so the curated
library, the thousands of definitions generated from the compiler's own function
list, and anything a user writes later all go through one code path. It also
means the assistant can be handed the catalogue as text and asked to pick from
it, instead of being trusted to remember VEX.

A definition carries one of two templates:

    expr    a VEX expression, for pure nodes with a single output. It can be
            inlined into whatever consumes it, so `@P` stays `@P` rather than
            becoming `vector tmp1 = @P;`.
    code    one or more VEX statements. Declares its own outputs, and is the
            only option for anything with side effects or several results.

Placeholders are `{socket_name}`. The regex requires an identifier, so VEX
vector literals such as `{0, 0, 0}` pass through a template untouched.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import vextypes

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_]\w*)\}")

KINDS = ("pure", "statement", "scope")


class NodeDefError(ValueError):
    """A node definition is malformed. Raised at load time, never at emit time."""


@dataclass(frozen=True)
class SocketDef:
    name: str
    type: str
    label: str = ""
    desc: str = ""
    # Inputs only: the VEX literal used when nothing is wired in. `None` means
    # the socket is required and the graph is invalid without a connection.
    default: str | None = None
    # Outputs only: the name of the body this value is confined to. A loop index
    # exists inside the loop and nowhere else, and the emitter enforces it.
    scope: str | None = None
    # Outputs only: the input socket this output is a modified copy of. Some VEX
    # functions edit their first argument in place (`rotate(m, ...)`), which a
    # graph has no way to say. Splitting it into "reads m, produces a new m"
    # keeps the graph honest, and this records the pairing so an import of that
    # call can wire the old value in as well as name the new one.
    reads: str | None = None

    @property
    def title(self) -> str:
        return self.label or self.name.replace("_", " ").title()


@dataclass(frozen=True)
class ParamDef:
    """A setting on the node itself rather than a wire into it.

    Attribute names, a run-over class, a Type dropdown: things that are chosen
    once and change what the node *is*, not values that flow through it. A param
    can also decide the type of a socket, which is how one Get Attribute node
    serves floats, vectors and strings instead of there being three of them.
    """

    name: str
    label: str = ""
    kind: str = "string"        # string | int | float | menu | vextype | attrib
    default: str = ""
    menu: tuple[str, ...] = ()
    desc: str = ""
    retypes: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return self.label or self.name.replace("_", " ").title()


_CALL_RE = re.compile(r"\b([a-z]\w*)\s*\(")
# Things that look exactly like a call but are not a function anyone can look
# up: type constructors, and the control-flow keywords, which are written
# `if (...)` and `for (...)` and so match the same pattern. `foreach` is
# deliberately absent - VEX really does document it as a function.
_NOT_FUNCTIONS = {
    *vextypes.BASE_TYPES, "set", "array",
    "if", "else", "for", "while", "do", "return", "switch",
}


@dataclass(frozen=True)
class NodeDef:
    type: str
    label: str
    category: str
    kind: str = "pure"
    summary: str = ""
    help: str = ""
    inputs: tuple[SocketDef, ...] = ()
    outputs: tuple[SocketDef, ...] = ()
    params: tuple[ParamDef, ...] = ()
    # False for the node a graph starts from, which has an outgoing exec pin
    # but nothing before it.
    exec_in: bool = True
    exec_bodies: tuple[str, ...] = ()
    code: str = ""
    expr: str = ""
    # Whether evaluating this twice is guaranteed to give the same answer for
    # free. True for attribute reads and constants; false for anything random,
    # which must be computed once into a variable no matter how it is used.
    repeatable: bool = False
    # Node types the emitter implements itself because they touch its symbol
    # table (variables) rather than just producing text.
    builtin: str = ""
    # Only meaningful inside a loop body. Checked in the graph so the message
    # names the node, instead of vcc saying "break statement outside loop".
    requires_loop: bool = False
    tier: int = 1
    tags: tuple[str, ...] = ()

    @property
    def has_exec(self) -> bool:
        return self.kind in ("statement", "scope")

    @property
    def vex_function(self) -> str:
        """The VEX function this node is built on, for looking up its help.

        Read out of the template rather than declared, for the same reason the
        reverse index is: the template is already the single statement of what
        this node calls, and a second copy would be a second thing to get wrong.
        """
        if self.type.startswith("vex_"):
            return self.type[4:]
        for template in (self.expr, self.code):
            # The first call in the template is the one the node is named for;
            # anything nested inside it is an argument being prepared.
            match = _CALL_RE.search(template or "")
            if match and match.group(1) not in _NOT_FUNCTIONS:
                return match.group(1)
        return ""

    def input(self, name: str) -> SocketDef | None:
        return next((s for s in self.inputs if s.name == name), None)

    def output(self, name: str) -> SocketDef | None:
        return next((s for s in self.outputs if s.name == name), None)

    def param(self, name: str) -> ParamDef | None:
        return next((p for p in self.params if p.name == name), None)

    def retyper(self, socket_name: str) -> ParamDef | None:
        """The param, if any, that decides this socket's type."""
        return next((p for p in self.params if socket_name in p.retypes), None)

    def search_text(self) -> str:
        # The VEX function is in here so that Houdini's name for something
        # finds our name for it: "xyzdist" should reach Closest Point On
        # Surface, which is the node someone who read the docs is looking for.
        return " ".join((self.label, self.type, self.category, self.summary,
                         self.vex_function, *self.tags)).lower()


def _socket(raw: dict, *, is_input: bool) -> SocketDef:
    name = raw.get("name")
    if not name:
        raise NodeDefError("socket without a name")
    vex_type = raw.get("type")
    if not vextypes.is_valid(vex_type or ""):
        raise NodeDefError(f"socket {name!r} has unknown type {vex_type!r}")
    return SocketDef(
        name=name,
        type=vex_type,
        label=raw.get("label", ""),
        desc=raw.get("desc", ""),
        default=raw.get("default") if is_input else None,
        scope=raw.get("scope") if not is_input else None,
        reads=raw.get("reads") if not is_input else None,
    )


def _param(raw: dict) -> ParamDef:
    name = raw.get("name")
    if not name:
        raise NodeDefError("param without a name")
    kind = raw.get("kind", "string")
    if kind not in ("string", "int", "float", "menu", "vextype", "attrib"):
        raise NodeDefError(f"param {name!r} has unknown kind {kind!r}")
    return ParamDef(
        name=name,
        label=raw.get("label", ""),
        kind=kind,
        default=str(raw.get("default", "")),
        menu=tuple(raw.get("menu", ())),
        desc=raw.get("desc", ""),
        retypes=tuple(raw.get("retypes", ())),
    )


def node_def_from_dict(raw: dict, *, category: str = "") -> NodeDef:
    try:
        node_type = raw["type"]
    except KeyError:
        raise NodeDefError("node definition without a type") from None

    kind = raw.get("kind", "pure")
    if kind not in KINDS:
        raise NodeDefError(f"{node_type}: unknown kind {kind!r}")

    inputs = tuple(_socket(s, is_input=True) for s in raw.get("inputs", ()))
    outputs = tuple(_socket(s, is_input=False) for s in raw.get("outputs", ()))
    params = tuple(_param(p) for p in raw.get("params", ()))
    bodies = tuple(raw.get("exec_bodies", ()))

    definition = NodeDef(
        type=node_type,
        label=raw.get("label") or node_type.replace("_", " ").title(),
        category=raw.get("category") or category or "Other",
        kind=kind,
        summary=raw.get("summary", ""),
        help=raw.get("help", ""),
        inputs=inputs,
        outputs=outputs,
        params=params,
        exec_in=bool(raw.get("exec_in", True)),
        exec_bodies=bodies,
        code=raw.get("code", ""),
        expr=raw.get("expr", ""),
        repeatable=bool(raw.get("repeatable", False)),
        builtin=raw.get("builtin", ""),
        requires_loop=bool(raw.get("requires_loop", False)),
        tier=int(raw.get("tier", 1)),
        tags=tuple(raw.get("tags", ())),
    )
    _validate(definition)
    return definition


def _validate(d: NodeDef) -> None:
    if d.builtin:
        return  # The emitter supplies the code; there is no template to check.

    if d.expr and d.code:
        raise NodeDefError(f"{d.type}: has both expr and code, pick one")
    if not d.expr and not d.code:
        raise NodeDefError(f"{d.type}: has neither expr nor code")
    if d.expr:
        if d.kind != "pure":
            raise NodeDefError(f"{d.type}: expr is only for pure nodes")
        if len(d.outputs) != 1:
            raise NodeDefError(f"{d.type}: expr needs exactly one output")
    if d.kind == "scope" and not d.exec_bodies:
        raise NodeDefError(f"{d.type}: a scope node needs at least one body")
    if d.kind != "scope" and d.exec_bodies:
        raise NodeDefError(f"{d.type}: only scope nodes have bodies")

    for out in d.outputs:
        if out.scope and out.scope not in d.exec_bodies:
            raise NodeDefError(
                f"{d.type}: output {out.name!r} is scoped to unknown body "
                f"{out.scope!r}")

    socket_names = {s.name for s in d.inputs} | {s.name for s in d.outputs}
    for p in d.params:
        for target in p.retypes:
            if target not in socket_names:
                raise NodeDefError(
                    f"{d.type}: param {p.name!r} retypes unknown socket "
                    f"{target!r}")
        if p.kind == "menu" and not p.menu:
            raise NodeDefError(f"{d.type}: menu param {p.name!r} has no items")

    # A socket left as "any" with nothing to resolve it would reach the emitter
    # with no type at all.
    for s in (*d.inputs, *d.outputs):
        if s.type in (vextypes.ANY, vextypes.ANY_ARRAY) and d.retyper(s.name) is None:
            raise NodeDefError(
                f"{d.type}: socket {s.name!r} is 'any' but no param retypes it")

    # Every placeholder must name something, or a typo becomes VEX that fails to
    # compile with an error pointing nowhere near the mistake.
    known = (socket_names | set(d.exec_bodies) | {p.name for p in d.params})
    template = d.expr or d.code
    for name in PLACEHOLDER_RE.findall(template):
        if name not in known:
            raise NodeDefError(
                f"{d.type}: template refers to {{{name}}}, which is not a "
                f"socket or body")

    # A code template owns its outputs completely: it has to declare each one
    # with a type, the way the VEX would be written by hand. The emitter does
    # not add declarations of its own, so an output the template only assigns to
    # would compile as an undeclared variable.
    if d.code:
        for out in d.outputs:
            if not _declares(d.code, out.name):
                raise NodeDefError(
                    f"{d.type}: template must declare output {out.name!r} with "
                    f"its type, e.g. 'vector {{{out.name}}} = ...'")


_DECL_RE_CACHE: dict[str, re.Pattern] = {}


def _declares(template: str, socket_name: str) -> bool:
    """Whether the template writes a VEX type immediately before {socket}."""
    if socket_name not in _DECL_RE_CACHE:
        types = "|".join(vextypes.BASE_TYPES)
        # A type spelled out, or a {param} holding one, as in `{type} {value}`.
        _DECL_RE_CACHE[socket_name] = re.compile(
            rf"(?:\b(?:{types})|\{{[A-Za-z_]\w*\}})(?:\[\])?\s+\{{{socket_name}\}}")
    return bool(_DECL_RE_CACHE[socket_name].search(template))


class Registry:
    """Every node definition available, indexed by type."""

    def __init__(self) -> None:
        self._defs: dict[str, NodeDef] = {}

    def add(self, definition: NodeDef, *, replace: bool = False) -> None:
        if not replace and definition.type in self._defs:
            raise NodeDefError(f"duplicate node type {definition.type!r}")
        self._defs[definition.type] = definition

    def load_file(self, path: Path) -> int:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        category = raw.get("category", "")
        # Generated files replace their previous selves; hand-written ones must
        # not silently shadow each other.
        replace = bool(raw.get("generated", False))
        for entry in raw.get("nodes", ()):
            self.add(node_def_from_dict(entry, category=category),
                     replace=replace)
        return len(raw.get("nodes", ()))

    def load_dir(self, path: Path) -> int:
        # Curated definitions load first so a generated overload can never
        # take a name a hand-written node already claimed.
        files = sorted(Path(path).glob("*.json"))
        files += sorted(Path(path).glob("generated/*.json"))
        return sum(self.load_file(f) for f in files)

    def get(self, node_type: str) -> NodeDef | None:
        return self._defs.get(node_type)

    def require(self, node_type: str) -> NodeDef:
        definition = self._defs.get(node_type)
        if definition is None:
            raise NodeDefError(f"unknown node type {node_type!r}")
        return definition

    def search(self, text: str, *, limit: int = 20,
               tier: int | None = None) -> list[NodeDef]:
        """Rank by where the words land: a title match beats a body match.

        Deliberately dumb. The assistant is what handles "how do I make points
        stick to a surface"; this is for someone who already half-knows the name.
        """
        words = [w for w in re.split(r"\W+", text.lower()) if w]
        scored: list[tuple[int, NodeDef]] = []
        for d in self._defs.values():
            if tier is not None and d.tier != tier:
                continue
            haystack = d.search_text()
            # The VEX function counts as part of the title, not the body: the
            # curated node *is* that function, so searching Houdini's name for
            # it should rank Closest Point On Surface above the raw xyzdist.
            title = f"{d.label} {d.type} {d.vex_function}".lower()
            if not all(w in haystack for w in words):
                continue
            score = sum(10 if w in title else 1 for w in words)
            score += 5 if d.tier == 1 else 0
            scored.append((score, d))
        scored.sort(key=lambda pair: (-pair[0], pair[1].label))
        return [d for _, d in scored[:limit]]

    def categories(self) -> dict[str, list[NodeDef]]:
        out: dict[str, list[NodeDef]] = {}
        for d in sorted(self._defs.values(), key=lambda d: d.label):
            out.setdefault(d.category, []).append(d)
        return out

    def __len__(self) -> int:
        return len(self._defs)

    def __iter__(self):
        return iter(self._defs.values())


def default_registry(nodes_dir: Path | None = None) -> Registry:
    registry = Registry()
    registry.load_dir(nodes_dir or Path(__file__).parent.parent / "nodes")
    return registry
