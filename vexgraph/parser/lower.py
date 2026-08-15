"""The tree onto the canvas: VEX statements become nodes and wires.

This is the direction that makes the tool a translator rather than a second way
to author. Paste the VEX an AI wrote you, see it as nodes, understand it, change
a node, get VEX back.

Two decisions carry the design:

**The reverse index is derived, not written.** Which node implements `fit` is
already stated by that node's own code template — reading it back out means the
importer learns every node the library gains, including all 1275 generated ones,
without a lookup table to maintain and get wrong.

**Failure is per statement, and never fatal.** Anything that does not map — a
`while`, a struct, a ternary, a function nobody wrote a node for — becomes one
Inline VEX node holding the original text. So an import always succeeds, always
round-trips, and gets better as the library grows instead of being blocked until
the parser is finished.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import vextypes
from ..graph import Graph
from ..nodedefs import Registry
from . import syntax
from .syntax import (Assign, Attribute, Binary, Block, Call, Cast, Declare, Hscript,
                     Expr, ExprStatement, ForEach, For, If, Index, Jump,
                     Literal, Member, Name, Raw, Statement, Ternary, Unary,
                     VectorLiteral, While)

# Attributes a wrangle types for you. Needed to infer that `@P + @N` is a
# vector add rather than a float one.
STANDARD_ATTRIBUTES = {
    "P": "vector", "N": "vector", "Cd": "vector", "v": "vector", "up": "vector",
    "scale": "vector", "rest": "vector", "uv": "vector", "accel": "vector",
    "force": "vector", "targetv": "vector", "torque": "vector",
    "orient": "vector4", "rot": "vector4",
    "ptnum": "int", "numpt": "int", "primnum": "int", "numprim": "int",
    "vtxnum": "int", "numvtx": "int", "elemnum": "int", "numelem": "int",
    "id": "int", "nextid": "int", "group": "int",
    "pscale": "float", "width": "float", "Alpha": "float", "density": "float",
    "age": "float", "life": "float", "mass": "float", "drag": "float",
    "bounce": "float", "friction": "float",
    "Frame": "float", "Time": "float", "TimeInc": "float",
    "name": "string", "instance": "string", "shop_materialpath": "string",
}

PREFIX_TYPES = {v: k for k, v in vextypes.ATTR_PREFIX.items()}

# Globals the SOP context provides without an `@`, and the attribute each one
# is the same thing as. Documented on the Point Wrangle page; snippets written
# by hand use the bare spelling often, and reading them as undeclared variables
# sent whole snippets to Inline VEX - along with everything that depended on
# them, which is how one unknown name cost a dozen nodes.
SOP_GLOBALS = {
    "Time": ("Time", "float"),
    "Frame": ("Frame", "float"),
    "TimeInc": ("TimeInc", "float"),
    "Npt": ("numpt", "int"),
}

# How wide a type is, for working out what `int + vector` comes to.
WIDTH = {"int": 0, "float": 1, "vector2": 2, "vector": 3, "vector4": 4}

CALL_RE = re.compile(r"^([A-Za-z_]\w*)\((.*)\)$", re.DOTALL)
PLACEHOLDER_ONLY = re.compile(r"^\{[A-Za-z_]\w*\}$")


class Unsupported(Exception):
    """This piece does not map; the statement around it becomes Inline VEX."""


@dataclass
class Report:
    graph: Graph
    total: int = 0
    inlined: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def translated(self) -> int:
        return self.total - self.inlined

    def summary(self) -> str:
        if not self.total:
            return "Nothing to import."
        if not self.inlined:
            return f"All {self.total} statements became nodes."
        return (f"{self.translated} of {self.total} statements became nodes; "
                f"{self.inlined} kept as Inline VEX.")


@dataclass(frozen=True)
class Slot:
    """What one argument position of a VEX call means for a node."""
    kind: str        # "in" | "out" | "param" | "param_str" | "fixed"
    name: str


@dataclass(frozen=True)
class Signature:
    node_type: str
    slots: tuple[Slot, ...]
    result: str      # output socket carrying the return value, "" for void


class FunctionIndex:
    """Which node implements which VEX function, read out of the templates.

    A node's template already says what it emits, so the mapping exists in the
    library; this just inverts it. Tier 1 wins over tier 2, so `fit` imports as
    the Fit Range node a person can read rather than the raw function.

    Arity comes from the **call in the template**, not from the socket count.
    Those differ constantly: an argument can be a node setting rather than a
    wire (`point(0, "Cd", n)` has two settings), and a `&` output parameter is
    an argument that is not an input at all (`xyzdist` passes two). Counting
    sockets matched `xyzdist(a,b,c,d)` to the six-argument overload.
    """

    def __init__(self, registry: Registry):
        # Every signature per (function, arity), in tier order. VEX overloads
        # freely on argument *types* at the same arity - quaternion() takes an
        # angle-axis pair or a matrix3 - and keeping only the first signature
        # meant the other overloads did not exist as far as importing was
        # concerned, however correct the original code was.
        self.by_call: dict[tuple[str, int], list[Signature]] = {}
        self.by_operator: dict[tuple[str, int], str] = {}
        for definition in sorted(registry, key=lambda d: (d.tier, d.type)):
            self._index(definition)

    def _index(self, definition) -> None:
        template = (definition.expr or definition.code).strip()
        if not template or definition.builtin:
            return

        if definition.expr:
            match = CALL_RE.match(template)
            if match:
                self._record(definition, match.group(1), match.group(2),
                             definition.outputs[0].name)
                return
            operator = self._operator(template, definition)
            if operator:
                self.by_operator.setdefault(
                    (operator, len(definition.inputs)), definition.type)
            return

        # In a code template the call that produces the result is the last
        # line; anything above it is the declaration of its output arguments.
        last = template.split("\n")[-1].strip().rstrip(";")
        assigned, _, call = last.rpartition("= ")
        match = CALL_RE.match(call.strip())
        if not match:
            return
        result = ""
        if assigned:
            names = re.findall(r"\{([A-Za-z_]\w*)\}", assigned)
            result = names[-1] if names else ""
        self._record(definition, match.group(1), match.group(2), result)

    def _record(self, definition, function: str, args: str, result: str) -> None:
        parts = _split_arguments(args)
        slots = tuple(self._slot(definition, part) for part in parts)
        if any(slot is None for slot in slots):
            return
        self.by_call.setdefault((function, len(parts)), []).append(
            Signature(definition.type, slots, result))

    @staticmethod
    def _slot(definition, text: str) -> Slot | None:
        """Classify one template argument: wire, setting, or output."""
        text = text.strip()
        quoted = text.startswith('"') and text.endswith('"')
        if quoted:
            text = text[1:-1]
        # Generated nodes wrap arguments in a cast to pin down an overload.
        cast = re.match(r"^[A-Za-z_]\w*\((\{[A-Za-z_]\w*\})\)$", text)
        if cast:
            text = cast.group(1)
        if not PLACEHOLDER_ONLY.match(text):
            return Slot("fixed", text)
        name = text[1:-1]
        if definition.input(name) is not None:
            return Slot("in", name)
        if definition.output(name) is not None:
            return Slot("out", name)
        if definition.param(name) is not None:
            return Slot("param_str" if quoted else "param", name)
        return None

    @staticmethod
    def _operator(template: str, definition) -> str:
        names = [s.name for s in definition.inputs]
        if len(names) == 2:
            for op in ("&&", "||", "==", "!=", "<=", ">=", "+", "-", "*", "/", "%",
                       "<", ">"):
                if template == f"{{{names[0]}}} {op} {{{names[1]}}}":
                    return op
        if len(names) == 1:
            for op in ("-", "!"):
                if template == f"{op}{{{names[0]}}}":
                    return op
        return ""

    def call(self, name: str, arity: int) -> list[Signature]:
        return self.by_call.get((name, arity), [])

    def operator(self, op: str, arity: int) -> str | None:
        return self.by_operator.get((op, arity))


def _split_arguments(text: str) -> list[str]:
    """Split on commas that are not inside brackets, braces or quotes."""
    parts, depth, quoted, current = [], 0, False, ""
    for char in text:
        if quoted:
            quoted = char != '"'
        elif char == '"':
            quoted = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current)
    return [p.strip() for p in parts if p.strip()]


# A value being lowered is either a literal to type into a socket, or a port.
@dataclass
class Value:
    literal: str = ""
    node: str = ""
    socket: str = ""
    type: str = "float"

    @property
    def is_port(self) -> bool:
        return bool(self.node)


class Importer:
    def __init__(self, registry: Registry, source: str):
        self.registry = registry
        self.source = source
        self.index = FunctionIndex(registry)
        self.graph = Graph(registry)
        self.report = Report(self.graph)
        self.variables: dict[str, str] = {}       # name -> VEX type
        self._loop_indices: dict[str, tuple[str, str]] = {}
        self._declared_by: dict[str, str] = {}    # name -> the var_make node
        self._read: set[str] = set()
        self._dead: set[str] = set()             # makers an alias made pointless
        # Names that exist ONLY as a wire to a node's output: `vector push =
        # v@N * 0.2` becomes a multiply node, and `push` never appears in the
        # emitted code. Loop indices are deliberately not in here - a loop node
        # does declare its variable, so materialising one would duplicate it.
        self._value_aliases: dict[str, tuple[str, str]] = {}
        self._declared_attributes: dict[str, str] = {}
        # How deep in branch/loop bodies lowering currently is, and how deep
        # each variable was declared. VOP's rule for state that crosses a
        # scope: the variable lives outside, the branch only assigns it. An
        # alias is a claim that a wire IS the variable, and a wire from inside
        # a body does not exist outside it - so writes from a deeper scope go
        # through a real Set Variable instead.
        self._depth = 0
        self._declared_depth: dict[str, int] = {}
        # Set Variable nodes made for a call statement's out-arguments; the
        # chain appends them right after the statement they belong to.
        self._pending_setters: list[str] = []
        # One read node per binding per epoch. `@P` mentioned five times used
        # to be five identical Get Attribute nodes - 40% of an imported graph
        # was this kind of plumbing. A read is the *binding*, not a snapshot,
        # so mentions may share a node until something writes that binding,
        # which starts a new epoch. Keys: ("attr"|"var", name, type).
        self._read_cache: dict[tuple[str, str, str], tuple[str, str]] = {}
        self._counter = 0
        # Where the next node in run order attaches: (node id, exec pin). Kept
        # current while a statement lowers so that a call with side effects
        # found *inside an expression* - `pr = addprim(0, "polyline")` - can be
        # spliced into the sequence at the point it occurs. Those nodes used to
        # be created and never wired to run, which emitted references to
        # results that were never computed.
        self._cursor: tuple[str, str] = ("", "")

    # ------------------------------------------------------------- driving

    def run(self, statements: list[Statement]) -> Report:
        self._reassigned = _reassigned_names(statements)
        self._loop_declared = _loop_variable_names(statements)
        # One prefixed mention types the attribute for the whole snippet,
        # exactly as the wrangle compiler reads it. Scanned from the source
        # text rather than the tree so mentions inside inline-bound statements
        # count too.
        self._declared_attributes = {
            match.group(3): PREFIX_TYPES.get(match.group(1), "float")
            for match in re.finditer(r"\b([fivpu234sd])(\[\])?@(\w+)", self.source)
            if match.group(3) not in STANDARD_ATTRIBUTES
        }
        start = self.graph.add("start", "start")
        self._chain(start.id, statements)
        self._prune_dead()
        return self.report

    def _prune_dead(self) -> None:
        """Drop declarations an alias replaced, healing the exec chain."""
        for node_id in self._dead:
            if node_id not in self.graph.nodes:
                continue
            before = next((l for l in self.graph.links
                           if l.is_exec and l.to_node == node_id), None)
            after = next((l for l in self.graph.links
                          if l.is_exec and l.from_node == node_id), None)
            self.graph.remove(node_id)
            if before and after:
                self.graph.connect(before.from_node, before.from_socket,
                                   after.to_node, after.to_socket, is_exec=True)

    def _chain(self, previous: str, statements: list[Statement],
               pin: str = "exec") -> str:
        """Lower a run of statements, wiring each into the exec chain.

        The cursor, not `previous`, is what statements attach to: lowering one
        statement can splice side-effect nodes into the sequence (a call in an
        expression), and the statement's own node has to land after them.
        """
        for statement in statements:
            self.report.total += 1
            mark = len(self.graph.nodes)
            self._cursor = (previous, pin)
            try:
                node_ids = [self._statement(statement)]
                node_ids += self._pending_setters
                self._pending_setters = []
            except Unsupported as exc:
                self._rollback(mark)
                self._cursor = (previous, pin)   # spliced nodes are gone too
                self._pending_setters = []       # theirs went with the rollback
                # An inlined statement may need declarations put back first,
                # so this is a run of nodes rather than one.
                node_ids = (self._inline_declaration(statement, str(exc))
                            or self._inline(statement, str(exc)))
            for node_id in node_ids:
                if not node_id:
                    continue
                at, at_pin = self._cursor
                self.graph.connect(at, at_pin, node_id, "exec", is_exec=True)
                self._cursor = (node_id, "exec")
            previous, pin = self._cursor
        return previous

    def _chain_body(self, previous: str, statements: list[Statement],
                    pin: str) -> str:
        """Lower a body without disturbing where its OWNER attaches.

        A branch or loop node is wired into the outer sequence *after* its
        bodies are lowered, and the inner chain moves the cursor as it works -
        so without saving it, the loop node would be attached after the last
        statement of its own body, turning the sequence inside out.
        """
        saved = self._cursor
        self._depth += 1
        try:
            tail = self._chain(previous, statements, pin=pin)
        finally:
            self._depth -= 1
            self._cursor = saved
        return tail

    def _inline_declaration(self, statement: Statement,
                            reason: str) -> list[str] | None:
        """An unsupported initialiser becomes a real variable plus inline text.

        `int flag = a ? b : c;` used to go verbatim into one inline node, and
        because the graph then had no record of `flag`, every later mention of
        it fell to inline too - one cause, a cascade of symptoms. Splitting it
        keeps both sides honest: a Make Variable node declares the name, so the
        emitter and later statements know it, and the inline node holds only
        the assignment the graph could not express.

        None when the statement is not a splittable declaration, in which case
        the ordinary verbatim path takes over.
        """
        if not isinstance(statement, Declare) or not statement.name:
            return None
        text = self.source[statement.start:statement.end].strip()
        opener = re.match(
            rf"^\s*{re.escape(statement.type)}\s+{re.escape(statement.name)}"
            rf"\s*(\[\s*\])?\s*=",
            text)
        if opener is None:
            return None

        vex_type = statement.type + ("[]" if statement.is_array else "")
        self.variables[statement.name] = vex_type
        self._declared_depth[statement.name] = self._depth
        self._invalidate_reads("var", statement.name)
        maker = self.graph.add("var_make", self._name("make"),
                               name=statement.name, type=vex_type)
        self._declared_by[statement.name] = maker.id

        self._read_cache.clear()      # its inline half can read-modify-write
        assignment = f"{statement.name} ={text[opener.end():]}".strip()
        if not assignment.endswith((";", "}")):
            assignment += ";"
        self.report.inlined += 1
        self.report.reasons.append(reason)
        nodes = self._materialise_aliases(assignment)
        nodes.append(self.graph.add("inline_vex", self._name("inline"),
                                    code=assignment).id)
        return [maker.id, *nodes]

    def _rollback(self, mark: int) -> None:
        """Drop the half-built nodes of a statement that turned out unsupported."""
        for node_id in list(self.graph.nodes)[mark:]:
            self.graph.remove(node_id)

    def _inline(self, statement: Statement, reason: str) -> list[str]:
        text = self.source[statement.start:statement.end].strip()
        if not text.endswith((";", "}")):
            text += ";"
        self.report.inlined += 1
        self.report.reasons.append(reason)
        self._read_cache.clear()      # verbatim text can write anything
        nodes = self._materialise_aliases(text)
        nodes.append(self.graph.add("inline_vex", self._name("inline"),
                                    code=text).id)
        return nodes

    def _materialise_aliases(self, text: str) -> list[str]:
        """Give back the variables this inline text still expects to exist.

        A translated declaration leaves no variable behind - `vector push =
        v@N * 0.2` is a multiply node, and nothing in the emitted code is
        called `push`. Kept code that mentions it would reference a variable
        that no longer exists, which compiles as source and fails after the
        round-trip. Declaring it again just before the inline node keeps both
        halves true: the graph stays wired, and the name is back in the code.
        """
        wanted = [name for name in _identifiers(text) if name in self._value_aliases]
        made: list[str] = []
        for name in dict.fromkeys(wanted):          # stable, no duplicates
            node_id, socket = self._value_aliases.pop(name)
            if node_id not in self.graph.nodes:
                continue
            # A Make Variable for this name may still be alive - an aliased
            # in-place edit (rotate(xform, ...)) leaves the original maker in
            # the chain when something read it. Declaring the name a second
            # time is exactly the "there is already a variable" refusal.
            if any(n.type in ("var_make", "var_declare")
                   and n.params.get("name") == name
                   for n in self.graph.nodes.values()):
                continue
            vex_type = self.variables.get(name) or self.graph.socket_type(
                node_id, socket, is_input=False)
            maker = self.graph.add("var_make", self._name("make"),
                                   name=name, type=vex_type)
            self.graph.connect(node_id, socket, maker.id, "value")
            self._declared_by[name] = maker.id
            self._dead.discard(maker.id)
            made.append(maker.id)
        return made

    def _name(self, stem: str) -> str:
        self._counter += 1
        return f"{stem}_{self._counter}"

    # ----------------------------------------------------------- statements

    def _statement(self, statement: Statement) -> str:
        if isinstance(statement, Declare):
            return self._declare(statement)
        if isinstance(statement, Assign):
            return self._assign(statement)
        if isinstance(statement, ExprStatement):
            return self._call_statement(statement)
        if isinstance(statement, If):
            return self._if(statement)
        if isinstance(statement, For):
            return self._for(statement)
        if isinstance(statement, ForEach):
            return self._foreach(statement)
        if isinstance(statement, Jump):
            node = self.graph.add(
                "break_if" if statement.word == "break" else "skip_if",
                self._name(statement.word), condition="1")
            return node.id
        if isinstance(statement, Block) and not statement.body:
            self.report.total -= 1              # a bare `;` is not a statement
            return ""
        if isinstance(statement, (Raw, While, Block)):
            if isinstance(statement, While):
                raise Unsupported("while loops are not modelled")
            raise Unsupported(getattr(statement, "reason", "") or "no node models this")
        raise Unsupported(f"{type(statement).__name__} is not modelled")

    def _declare(self, statement: Declare) -> str:
        if statement.value is None and statement.name in self._loop_declared:
            self.report.total -= 1       # the For Each node declares this one
            self.variables[statement.name] = statement.type
            return ""
        vex_type = statement.type + ("[]" if statement.is_array else "")
        self._invalidate_reads("var", statement.name)
        value = (self._expression(statement.value) if statement.value is not None
                 else Value(literal=vextypes.zero(vex_type), type=vex_type))
        # `matrix3 m = ident();` asks for a 3x3 from a function that returns 4x4.
        # The declared type is the better evidence of intent, so a polymorphic
        # node feeding it is retyped to match rather than being refused.
        if value.is_port and value.type != vex_type:
            self._retype_to(value, vex_type)
        self.variables[statement.name] = vex_type
        self._declared_depth[statement.name] = self._depth

        # `float d = xyzdist(...)` is one node in a graph, not a call plus a
        # variable: the name is just how the code refers to that output. Only
        # a variable that is written again later needs to be a real variable.
        # And only when the wire IS the declared type: aliasing `int n` to a
        # float wire records a claim the emitter cannot honour, and surfaces
        # later as a mismatch on whatever the alias gets materialised into.
        # Without the alias, the var_make path below converts properly.
        if (value.is_port and value.type == vex_type
                and not self._reassigned.get(statement.name)):
            self._loop_indices[statement.name] = (value.node, value.socket)
            self._value_aliases[statement.name] = (value.node, value.socket)
            return ""

        node = self.graph.add("var_make", self._name("make"),
                              name=statement.name, type=vex_type)
        self._declared_by[statement.name] = node.id
        self._value_aliases.pop(statement.name, None)
        self._feed(node.id, "value", value)
        return node.id

    def _assign(self, statement: Assign) -> str:
        value = statement.value
        if statement.op != "=":
            # `@P += x` is `@P = @P + x`; rewriting keeps one code path.
            value = Binary(statement.op[0], statement.target, value)

        if isinstance(statement.target, Attribute):
            vex_type = self._attribute_type(statement.target)
            node = self.graph.add("attrib_set", self._name("set"),
                                  attrib=statement.target.name, type=vex_type)
            self._feed(node.id, "value", self._expression(value))
            self._invalidate_reads("attr", statement.target.name)
            return node.id

        if isinstance(statement.target, Name):
            name = statement.target.name
            if name not in self.variables:
                raise Unsupported(f"{name} was never declared here")
            node = self.graph.add("var_set", self._name("set"), name=name,
                                  type=self.variables[name])
            self._feed(node.id, "value", self._expression(value))
            self._invalidate_reads("var", name)
            return node.id

        if isinstance(statement.target, Member):
            return self._assign_component(statement.target, value)

        raise Unsupported("only attributes and variables can be assigned")

    def _assign_component(self, target: Member, value: Expr) -> str:
        """`@P.x = v` in one node for attributes; rebuilt for variables.

        Attributes get the direct form because the wrangle language has one -
        `@P.y = v;` is a legal, idiomatic statement - and the split/remake
        spelling of it was the single biggest reason simple one-liners
        exploded into big graphs. Variables keep the split-and-remake, which
        reads as what it is: a new value built from the old one.
        """
        if target.name not in ("x", "y", "z"):
            raise Unsupported(f".{target.name} cannot be assigned")
        if not isinstance(target.target, (Attribute, Name)):
            raise Unsupported("only an attribute or a variable can be rebuilt "
                              "component by component")

        if isinstance(target.target, Attribute):
            vex_type = self._attribute_type(target.target)
            if vex_type in vextypes.VECTOR_TYPES:
                node = self.graph.add("attrib_set_component", self._name("set"),
                                      attrib=target.target.name,
                                      component=target.name, type=vex_type)
                self._feed(node.id, "value", self._expression(value))
                self._invalidate_reads("attr", target.target.name)
                return node.id

        current = self._expression(target.target)
        split = self.graph.add("split_vector", self._name("split"))
        self._feed(split.id, "vector", current)

        make = self.graph.add("make_vector", self._name("rebuilt"))
        for axis in ("x", "y", "z"):
            if axis == target.name:
                self._feed(make.id, axis, self._expression(value))
            else:
                self._feed(make.id, axis,
                           Value(node=split.id, socket=axis, type="float"))

        rebuilt = Value(node=make.id, socket="result", type="vector")
        if isinstance(target.target, Attribute):
            node = self.graph.add(
                "attrib_set", self._name("set"),
                attrib=target.target.name,
                type=self._attribute_type(target.target))
        else:
            name = target.target.name
            if name not in self.variables:
                raise Unsupported(f"{name} was never declared here")
            node = self.graph.add("var_set", self._name("set"), name=name,
                                  type=self.variables[name])
        self._feed(node.id, "value", rebuilt)
        if isinstance(target.target, Attribute):
            self._invalidate_reads("attr", target.target.name)
        else:
            self._invalidate_reads("var", target.target.name)
        return node.id

    def _invalidate_reads(self, kind: str, name: str) -> None:
        """A write starts a new epoch: reads after it get fresh nodes.

        Without this, sharing read nodes would wire a consumer that runs after
        the write to a read from before it - a graph that says something the
        code does not.
        """
        for key in [k for k in self._read_cache
                    if k[0] == kind and k[1] == name]:
            del self._read_cache[key]

    def _call_statement(self, statement: ExprStatement) -> str:
        if not isinstance(statement.value, Call):
            raise Unsupported("an expression on its own does nothing")
        signature = self._choose_signature(statement.value)
        if signature is None:
            raise Unsupported(f"no node calls {statement.value.name}()")
        placed = self._place_call(signature, statement.value,
                                  chained_by_caller=True).node
        # A call written as a statement may still land on a pure value node -
        # `rotate(m, ...)` becomes "the rotated matrix". Those have no exec pin,
        # so returning them here would try to wire them into the run order.
        if not self.registry.require(self.graph.nodes[placed].type).has_exec:
            self.report.total -= 1
            return ""
        return placed

    def _if(self, statement: If) -> str:
        node_type = "if_else" if statement.otherwise else "if"
        node = self.graph.add(node_type, self._name("branch"))
        self._feed(node.id, "condition",
                   self._as_condition(self._expression(statement.condition)))
        self._chain_body(node.id, statement.then, pin="then")
        if statement.otherwise:
            self._chain_body(node.id, statement.otherwise, pin="otherwise")
        return node.id

    def _for(self, statement: For) -> str:
        count = self._counted_loop(statement)
        if count is None:
            raise Unsupported(
                "only counted loops (for i = 0; i < n; i++) map")
        variable, start, limit = count
        node = self.graph.add("for_range", self._name("repeat"))
        # The emitter names the loop variable from the node's title. Keeping
        # the original name is not cosmetic: an inline statement in the body
        # still says `i`, and a loop emitted as `repeat` leaves that text
        # referring to a variable that no longer exists.
        node.title = variable
        if start != "0":
            node.params["start"] = start
        self._feed(node.id, "count", self._expression(limit))
        self.variables[variable] = "int"
        self._loop_indices[variable] = (node.id, "index")
        self._chain_body(node.id, statement.body, pin="body")
        return node.id

    @staticmethod
    def _counted_loop(statement: For) -> tuple[str, Expr] | None:
        """Recognise a loop that runs a fixed number of times from zero.

        `i < N` is the textbook form, but `i <= N` is just as common in
        hand-written VEX and used to fail here - and failing here is expensive,
        because the whole loop body then falls back to one block of inline VEX.
        On a real snippet that difference was 5 nodes against 20. `i != N` is
        the same loop written a third way.

        `<=` runs one more time than `<`, so the count becomes `N + 1` rather
        than `N`; the extra term is built here instead of being special-cased
        downstream, so the loop node stays a plain counted loop. A literal
        non-zero start (`for (int i = 1; ...)`) is the loop node's Start
        setting - common enough in hand-written VEX that refusing it cost the
        whole body.
        """
        setup, condition, step = statement.setup, statement.condition, statement.step
        if not isinstance(setup, Declare) or setup.type != "int":
            return None
        if not (isinstance(setup.value, Literal) and setup.value.kind == "int"):
            return None
        if not (isinstance(condition, Binary) and condition.op in ("<", "<=", "!=")
                and isinstance(condition.left, Name)
                and condition.left.name == setup.name):
            return None
        if not (isinstance(step, Assign) and step.op == "+="
                and isinstance(step.target, Name)
                and step.target.name == setup.name
                and isinstance(step.value, Literal) and step.value.text == "1"):
            return None

        limit = condition.right
        if condition.op == "<=":
            if isinstance(limit, Literal) and limit.kind == "int":
                # `i <= 3` is four iterations; fold it rather than emitting the
                # arithmetic, so the node reads "4" and not "3 + 1".
                limit = Literal(text=str(int(limit.text) + 1), kind="int")
            else:
                limit = Binary(op="+", left=limit,
                               right=Literal(text="1", kind="int"))
        return setup.name, setup.value.text, limit

    def _foreach(self, statement: ForEach) -> str:
        element = statement.value_type or self._element_type(statement.array)
        node = self.graph.add("foreach", self._name("each"), type=element)
        # No title trick here, unlike _for: a title is one hint serving this
        # node's TWO outputs (index and item), so naming it after the value
        # variable collides the two and renames both. Instead each output gets
        # its own name as a per-socket param, which matters beyond cosmetics:
        # an inline statement in the body still says `i`, and a loop emitted
        # as `item` leaves that text referring to a variable that never
        # existed.
        node.params["item_name"] = statement.value_name
        if statement.index_name:
            node.params["index_name"] = statement.index_name
        self._feed(node.id, "items", self._expression(statement.array))
        if statement.index_name:
            self.variables[statement.index_name] = "int"
            self._loop_indices[statement.index_name] = (node.id, "index")
        self.variables[statement.value_name] = element
        self._loop_indices[statement.value_name] = (node.id, "item")
        self._chain_body(node.id, statement.body, pin="body")
        return node.id

    def _element_type(self, array: Expr) -> str:
        vex_type = self._type_of(array)
        return vextypes.element_type(vex_type) if vex_type else "float"

    # ---------------------------------------------------------- expressions

    def _feed(self, node_id: str, socket: str, value: Value) -> None:
        """Wire a value into a socket, converting the way VEX itself would.

        The graph is stricter than VEX on purpose - a person wiring by hand
        should say whether a float rounds or truncates. But imported code
        already made that decision when it compiled, so refusing here turned
        working snippets into graphs whose emitted VEX no longer built. The
        conversion VEX applied implicitly is inserted as a visible node
        instead: same meaning, and the graph now *shows* the coercion the
        original code hid.
        """
        if not value.is_port:
            self.graph.nodes[node_id].params[socket] = value.literal
            return

        try:
            wanted = self.graph.socket_type(node_id, socket, is_input=True)
        except Exception:               # noqa: BLE001 - unresolved; keep old path
            wanted = ""
        if wanted and not vextypes.can_connect(value.type, wanted):
            # Cheapest first: if the source is polymorphic, its type was our
            # guess, and the socket is the evidence of what the author meant.
            if not self._retype_to(value, wanted):
                if value.type == "float" and wanted == "int":
                    value = self._truncate(value)
                else:
                    raise Unsupported(
                        vextypes.explain_mismatch(value.type, wanted)
                        or f"a {value.type} cannot feed a {wanted} input")
        self.graph.connect(value.node, value.socket, node_id, socket)

    def _as_condition(self, value: Value) -> Value:
        """A float driving a condition means `!= 0`, never truncation.

        VEX treats any non-zero value as true, so `if (rand(x))` is true for
        0.7 - and the generic float->int shim truncates, which would turn that
        same 0.7 into false. Same emitted meaning, spelled the way VEX reads
        it: an explicit comparison against zero.
        """
        if value.is_port and value.type == "float":
            node = self.graph.add("is_not_equal", self._name("istrue"), b="0")
            self.graph.connect(value.node, value.socket, node.id, "a")
            return Value(node=node.id, socket="result", type="int")
        return value

    def _truncate(self, value: Value) -> Value:
        """A visible float->int conversion, in the direction VEX itself goes.

        Implicit float->int in VEX truncates, so the shim says `trunc` rather
        than round - importing must not change what the code computes, only
        make it visible.
        """
        shim = self.graph.add("round_to_int", self._name("whole"), mode="trunc")
        self.graph.connect(value.node, value.socket, shim.id, "value")
        return Value(node=shim.id, socket="result", type="int")

    def _expression(self, expr: Expr) -> Value:
        if isinstance(expr, Literal):
            return Value(literal=expr.text, type=expr.kind)

        if isinstance(expr, Hscript):
            # Houdini textually substitutes these before compiling, so the only
            # correct thing to do is carry the text through untouched.
            return Value(literal=expr.text, type="float")

        if isinstance(expr, VectorLiteral):
            if all(isinstance(i, Literal) for i in expr.items):
                text = ", ".join(i.text for i in expr.items)
                return Value(literal=f"{{{text}}}",
                             type=self._vector_type(len(expr.items)))
            if len(expr.items) == 3:
                node = self.graph.add("make_vector", self._name("vector"))
                for socket, item in zip(("x", "y", "z"), expr.items):
                    self._feed(node.id, socket, self._expression(item))
                return Value(node=node.id, socket="result", type="vector")
            raise Unsupported("only 3-component vectors can be built from parts")

        if isinstance(expr, Attribute):
            # `@OpInput1` is not an attribute: it is the wrangle's spelling of
            # "my first input". The functions it is passed to also take the
            # input *number*, which is what it becomes here - same compiled
            # meaning, and it saves modelling a string binding that only ever
            # names an input.
            opinput = re.fullmatch(r"OpInput([1-4])", expr.name)
            if opinput and not expr.prefix:
                return Value(literal=str(int(opinput.group(1)) - 1), type="int")
            vex_type = self._attribute_type(expr)
            key = ("attr", expr.name, vex_type)
            cached = self._read_cache.get(key)
            if cached and cached[0] in self.graph.nodes:
                return Value(node=cached[0], socket=cached[1], type=vex_type)
            node = self.graph.add("attrib_get", self._name("get"),
                                  attrib=expr.name, type=vex_type)
            self._read_cache[key] = (node.id, "value")
            return Value(node=node.id, socket="value", type=vex_type)

        if isinstance(expr, Name):
            if expr.name in self._loop_indices:
                node_id, socket = self._loop_indices[expr.name]
                return Value(node=node_id, socket=socket,
                             type=self.variables.get(expr.name, "int"))
            if expr.name in SOP_GLOBALS:
                # `Time` and friends are globals the SOP context provides, not
                # undeclared variables. SideFX recommend the `@` spelling, and
                # that is exactly what the Get Attribute node emits - so this
                # reads as the attribute it is, and round-trips as `@Time`.
                attribute, vex_type = SOP_GLOBALS[expr.name]
                node = self.graph.add("attrib_get", self._name("global"),
                                      attrib=attribute, type=vex_type)
                return Value(node=node.id, socket="value", type=vex_type)
            if expr.name not in self.variables:
                raise Unsupported(f"{expr.name} was never declared here")
            self._read.add(expr.name)
            vex_type = self.variables[expr.name]
            key = ("var", expr.name, vex_type)
            cached = self._read_cache.get(key)
            if cached and cached[0] in self.graph.nodes:
                return Value(node=cached[0], socket=cached[1], type=vex_type)
            node = self.graph.add("var_get", self._name("read"),
                                  name=expr.name, type=vex_type)
            self._read_cache[key] = (node.id, "value")
            return Value(node=node.id, socket="value", type=vex_type)

        if isinstance(expr, Cast):
            # A cast that only restates a type is dropped rather than modelled.
            # One that actually changes the type has to change the graph too:
            # claiming the new type on an unchanged wire lied to the connection
            # checks, and the lie surfaced later as an emitter refusal on a
            # graph already built.
            inner = self._expression(expr.value)
            if not inner.is_port or inner.type == expr.to:
                return Value(inner.literal, inner.node, inner.socket, expr.to)
            if self._retype_to(inner, expr.to):
                return inner
            if inner.type == "float" and expr.to == "int":
                return self._truncate(inner)      # what a C cast does anyway
            if vextypes.can_connect(inner.type, expr.to):
                return Value(inner.literal, inner.node, inner.socket, expr.to)
            raise Unsupported(f"a ({expr.to}) cast from {inner.type} "
                              f"has no node to do the converting")

        if isinstance(expr, Unary):
            if expr.op == "-" and isinstance(expr.operand, Literal):
                return Value(literal=f"-{expr.operand.text}",
                             type=expr.operand.kind)
            return self._operator(expr.op, [expr.operand])
        if isinstance(expr, Binary):
            return self._operator(expr.op, [expr.left, expr.right])

        if isinstance(expr, Call):
            signature = self._choose_signature(expr)
            if signature is None:
                raise Unsupported(f"no node calls {expr.name}()")
            return self._place_call(signature, expr)

        if isinstance(expr, Member):
            if expr.name not in ("x", "y", "z"):
                raise Unsupported(f".{expr.name} is not modelled")
            node = self.graph.add("split_vector", self._name("split"))
            self._feed(node.id, "vector", self._expression(expr.target))
            return Value(node=node.id, socket=expr.name, type="float")

        if isinstance(expr, Index):
            element = self._element_type(expr.target)
            node = self.graph.add("list_item", self._name("item"), type=element)
            self._feed(node.id, "items", self._expression(expr.target))
            self._feed(node.id, "position", self._expression(expr.index))
            return Value(node=node.id, socket="item", type=element)

        if isinstance(expr, Ternary):
            # The dataflow twin of an If: VOP's Two Way Switch. Modelling it
            # matters beyond the expression itself, because a ternary in an
            # initialiser used to send the whole declaration inline.
            then_value = self._expression(expr.then)
            else_value = self._expression(expr.otherwise)
            chosen = self._widest([expr.then, expr.otherwise])
            node = self.graph.add("choose", self._name("choose"), type=chosen)
            self._feed(node.id, "condition",
                       self._as_condition(self._expression(expr.condition)))
            self._feed(node.id, "a", then_value)
            self._feed(node.id, "b", else_value)
            return Value(node=node.id, socket="result", type=chosen)

        raise Unsupported(f"{type(expr).__name__} is not modelled")

    def _operator(self, op: str, operands: list[Expr]) -> Value:
        # `v * m` is the one arithmetic operator in VEX whose operands are
        # deliberately different types, so no single Type setting describes it.
        # It gets its own node, which is also the name someone would search for.
        if op == "*" and len(operands) == 2:
            types = [self._type_of(o) for o in operands]
            matrices = [t in vextypes.MATRIX_TYPES for t in types]
            vectors = [t in vextypes.VECTOR_TYPES for t in types]
            if any(matrices) and any(vectors):
                index = matrices.index(True)
                node = self.graph.add("transform_by_matrix", self._name("xform"),
                                      type=types[index])
                self._feed(node.id, "matrix", self._expression(operands[index]))
                self._feed(node.id, "vector", self._expression(operands[1 - index]))
                return Value(node=node.id, socket="result", type=types[1 - index])

        node_type = self.index.operator(op, len(operands))
        if node_type is None:
            raise Unsupported(f"no node implements {op!r}")

        definition = self.registry.require(node_type)
        node = self.graph.add(node_type, self._name("op"))

        # Polymorphic nodes (Add, Multiply) carry a Type setting that has to
        # match the operands, or the wires will not attach.
        result_type = self._widest(operands)
        if definition.param("type") is not None:
            menu = definition.param("type").menu
            chosen = result_type if result_type in menu else definition.param("type").default
            node.params["type"] = chosen
            result_type = chosen

        for socket, operand in zip((s.name for s in definition.inputs), operands):
            self._feed(node.id, socket, self._expression(operand))

        output = definition.outputs[0]
        return Value(node=node.id, socket=output.name,
                     type=output.type if output.type != vextypes.ANY
                     else result_type)

    def _choose_signature(self, expr: Call):
        """The overload whose slots best fit what is actually being passed.

        VEX overloads on argument types at the same arity - `quaternion()`
        takes an angle and an axis or a matrix3 - and the old index kept only
        the first signature per arity, so the rest did not exist as far as
        importing was concerned. Scoring: an exact type match beats a
        polymorphic socket beats a widening; anything the wiring could not
        serve disqualifies. Ties keep tier order, so curated nodes still win.
        """
        candidates = self.index.call(expr.name, len(expr.args))
        if not candidates:
            return None
        if len(candidates) == 1:
            # Even an only child has to fit. Returning it regardless fed
            # `getbbox_center("uvwrap:uv")` into the int-geometry node, which
            # emitted int("uvwrap:uv") - code that cannot compile. Inline is
            # the honest fallback when the one node the registry has is wrong.
            return None if self._literal_mismatch(candidates[0], expr) \
                else candidates[0]

        best, best_score = None, -1
        for signature in candidates:
            definition = self.registry.require(signature.node_type)
            score, fits = 0, True
            for slot, argument in zip(signature.slots, expr.args):
                if slot.kind == "in":
                    socket = definition.input(slot.name)
                    wanted = socket.type if socket else ""
                    got = self._type_of(argument)
                    if wanted in (vextypes.ANY, vextypes.ANY_ARRAY):
                        score += 2
                    elif got == wanted:
                        score += 3
                    elif vextypes.can_connect(got, wanted):
                        score += 1
                    elif got == "float" and wanted == "int":
                        score += 1                    # a truncate shim serves it
                    else:
                        fits = False
                        break
                elif slot.kind in ("param", "param_str"):
                    if not (isinstance(argument, Literal)
                            or (isinstance(argument, Attribute)
                                and re.fullmatch(r"OpInput[1-4]", argument.name))):
                        fits = False
                        break
                    # A quoted slot must take a string and an unquoted one must
                    # not: `getbbox_center(0)` landing in the string-group node
                    # would emit getbbox_center("0"), which compiles but means
                    # a different thing - "0" is a group pattern, not input 0.
                    if isinstance(argument, Literal) and (
                            (argument.kind == "string")
                            != (slot.kind == "param_str")):
                        fits = False
                        break
                    score += 3    # a literal in a setting is an exact fit too
                elif slot.kind == "out":
                    if not isinstance(argument, Name):
                        fits = False
                        break
                    score += 3
                elif slot.kind == "fixed":
                    # The template hard-codes this argument (`removepoint(0,
                    # {pt})`), so the call only fits when it passes exactly
                    # that. Not scoring these let a generated node with a real
                    # socket outbid the curated statement it should lose to.
                    if (not isinstance(argument, Literal)
                            or argument.text.strip('"') != slot.name):
                        fits = False
                        break
                    score += 3
            if fits and score > best_score:
                best, best_score = signature, score
        if best is not None:
            return best
        # Nothing fits cleanly: keep the old first-wins behaviour and let the
        # wiring gates downgrade the statement honestly if they must - except
        # where a literal proves the candidate can never work.
        for signature in candidates:
            if not self._literal_mismatch(signature, expr):
                return signature
        return None

    def _literal_mismatch(self, signature, expr: Call) -> bool:
        """A literal argument whose type can never serve its slot.

        An expression's type is often a guess that retyping repairs once the
        socket says what was meant, so a mismatch there is survivable. A
        literal's type is ground truth: the string "uvwrap:uv" into an int
        socket becomes int("uvwrap:uv"), and the int 0 into a quoted group
        slot becomes "0" - one refuses to compile, the other compiles into a
        different meaning. Neither can be repaired downstream.
        """
        definition = self.registry.require(signature.node_type)
        for slot, argument in zip(signature.slots, expr.args):
            if not isinstance(argument, Literal):
                continue
            if slot.kind in ("param", "param_str"):
                if (argument.kind == "string") != (slot.kind == "param_str"):
                    return True
                continue
            if slot.kind == "fixed":
                if argument.text.strip('"') != slot.name:
                    return True
                continue
            if slot.kind != "in":
                continue
            socket = definition.input(slot.name)
            wanted = socket.type if socket else ""
            got = argument.kind
            if not wanted or not got:
                continue
            if wanted in (vextypes.ANY, vextypes.ANY_ARRAY):
                continue
            if got == wanted or vextypes.can_connect(got, wanted):
                continue
            if got == "float" and wanted == "int":
                continue
            return True
        return False

    def _place_call(self, signature, expr: Call, *,
                    chained_by_caller: bool = False) -> Value:
        definition = self.registry.require(signature.node_type)
        node = self.graph.add(signature.node_type,
                              self._name(signature.node_type.replace("vex_", "")))
        # (variable, output socket) pairs that must become Set Variable nodes
        # right after this call runs - out-arguments writing to a variable
        # declared in an outer scope. Collected during the slot walk, spliced
        # once the call itself is in the run order.
        setters: list[tuple[str, str]] = []

        # A node with a Type setting (Fit Range's To Type, say) has to be told
        # which overload this call is before anything is wired, or the sockets
        # are still the default type when the arguments arrive.
        setting = definition.param("type")
        if setting is not None and setting.retypes:
            for slot, argument in zip(signature.slots, expr.args):
                if slot.kind == "in" and slot.name in setting.retypes:
                    found = self._type_of(argument)
                    if found in setting.menu:
                        node.params["type"] = found
                        break

        for slot, argument in zip(signature.slots, expr.args):
            if slot.kind == "in":
                self._feed(node.id, slot.name, self._expression(argument))
            elif slot.kind in ("param", "param_str"):
                node.params[slot.name] = self._as_setting(argument, slot.kind)
            elif slot.kind == "out":
                # `xyzdist(0, @P, prim, uv)` fills `prim` and `uv`. In a graph
                # those are outputs, so the variable the caller passed becomes
                # a name for this node's socket rather than a variable of its
                # own — and the declaration that made it falls away.
                if not isinstance(argument, Name):
                    raise Unsupported(
                        f"{expr.name}() writes into an argument that is not a "
                        f"plain variable")
                # An in-place edit reads the old value before it writes the new
                # one, so the variable has to be wired in as well as renamed.
                socket = definition.output(slot.name)
                if socket is not None and socket.reads:
                    self._feed(node.id, socket.reads, self._expression(argument))
                name = argument.name
                if (name in self._declared_by
                        and (self._declared_depth.get(name, 0) < self._depth
                             or self._reassigned.get(name))):
                    # VOP's rule for state crossing a scope: the variable
                    # lives outside, this call only assigns it. An alias here
                    # would claim a wire from inside the branch as the
                    # variable's value everywhere - which is exactly the
                    # "comes from inside the If, used outside it" refusal the
                    # emitter then makes, on a graph already built. The same
                    # goes for a variable written again later (`points[i] =`):
                    # aliasing it away leaves that write with no variable, so
                    # the declaration must stay the one true binding.
                    setters.append((name, slot.name))
                else:
                    self._alias(name, node.id, slot.name)

        # A call with side effects found inside an expression still has to
        # *run*. Its arguments were fed above - any exec nodes among them are
        # already spliced, in argument order - so this node goes after them and
        # before the statement that consumes its result.
        if definition.has_exec and not chained_by_caller:
            at, at_pin = self._cursor
            if at:
                self.graph.connect(at, at_pin, node.id, "exec", is_exec=True)
                self._cursor = (node.id, "exec")

        for name, out_socket in setters:
            self._invalidate_reads("var", name)
            setter = self.graph.add("var_set", self._name("set"),
                                    name=name, type=self.variables[name])
            out_type = self.graph.socket_type(node.id, out_socket,
                                              is_input=False)
            self._feed(setter.id, "value",
                       Value(node=node.id, socket=out_socket, type=out_type))
            if chained_by_caller:
                # The call node itself is wired by _chain after we return, so
                # these must follow it there rather than land before it here.
                self._pending_setters.append(setter.id)
            else:
                at, at_pin = self._cursor
                if at:
                    self.graph.connect(at, at_pin, setter.id, "exec",
                                       is_exec=True)
                    self._cursor = (setter.id, "exec")

        if not signature.result:
            return Value(node=node.id, socket="", type="")
        output = definition.output(signature.result)
        out_type = output.type if output else "float"
        if out_type in (vextypes.ANY, vextypes.ANY_ARRAY):
            # "any" is a definition-side placeholder, never a real type. Left
            # unresolved it poisoned everything downstream: width arithmetic
            # treated it as nothing (an operator chain over two attribute
            # reads came out int), and the wiring gate let it through
            # unchecked. The graph resolves it from the node's Type setting.
            out_type = self.graph.socket_type(node.id, signature.result,
                                              is_input=False)
        return Value(node=node.id, socket=signature.result, type=out_type)

    def _as_setting(self, argument: Expr, kind: str) -> str:
        """A template argument that is a node setting must be a constant."""
        if isinstance(argument, Literal):
            text = argument.text
            return text[1:-1] if kind == "param_str" and text.startswith('"') else text
        if isinstance(argument, Attribute) and not argument.prefix:
            opinput = re.fullmatch(r"OpInput([1-4])", argument.name)
            if opinput:               # the input number, as a constant setting
                return str(int(opinput.group(1)) - 1)
        raise Unsupported("a setting on this node must be a constant here")

    def _retype_to(self, value: Value, wanted: str) -> bool:
        """Switch a polymorphic node's Type setting so its output is `wanted`.

        Recursive, because the evidence propagates: `float d = point(...) /
        point(...)` types the division float, and the division's operands are
        then evidence about the two reads feeding it - which is exactly how
        vcc resolved the original code. All or nothing: a chain that cannot be
        retyped end to end is put back the way it was found.
        """
        touched: list[tuple[str, str | None]] = []
        if self._retype_node(value.node, value.socket, wanted, touched):
            value.type = wanted
            return True
        for node_id, old in reversed(touched):
            params = self.graph.nodes[node_id].params
            if old is None:
                params.pop("type", None)
            else:
                params["type"] = old
        return False

    def _retype_node(self, node_id: str, socket: str, wanted: str,
                     touched: list[tuple[str, str | None]]) -> bool:
        node = self.graph.nodes.get(node_id)
        if node is None:
            return False
        definition = self.registry.require(node.type)
        if self.graph.socket_type(node_id, socket, is_input=False) == wanted:
            return True
        setting = definition.param("type")
        if (setting is None or socket not in setting.retypes
                or wanted not in setting.menu):
            return False
        # Some types are not ours to change. A declared variable's reads mean
        # what its declaration said, and `@P` is a vector whatever a socket
        # downstream might prefer - retyping either one would silently change
        # what the code computes, which is exactly the wrongness this tool
        # exists to prevent. A custom attribute with no prefix is fair game:
        # its type was our guess in the first place, and the socket is better
        # evidence than the guess (it is how vcc resolved the original).
        if node.type.startswith("var_"):
            return False
        attrib = node.params.get("attrib", "")
        if attrib in STANDARD_ATTRIBUTES and STANDARD_ATTRIBUTES[attrib] != wanted:
            return False
        touched.append((node_id, node.params.get("type")))
        node.params["type"] = wanted

        # Inputs governed by the same setting changed type with it; whatever
        # feeds them has to still fit, retyped in turn if need be.
        for sock in definition.inputs:
            if sock.name not in setting.retypes:
                continue
            link = next((x for x in self.graph.links
                         if not x.is_exec and x.to_node == node_id
                         and x.to_socket == sock.name), None)
            if link is None:
                continue          # a literal or a default adapts on its own
            source = self.graph.socket_type(link.from_node, link.from_socket,
                                            is_input=False)
            if vextypes.can_connect(source, wanted):
                continue
            if not self._retype_node(link.from_node, link.from_socket,
                                     wanted, touched):
                return False
        return True

    def _alias(self, name: str, node_id: str, socket: str) -> None:
        """Point a variable name at a node's output instead of a variable."""
        self._invalidate_reads("var", name)
        # Ask the graph, not the definition: on a polymorphic node the
        # definition still says "any", and recording that as the variable's type
        # makes every later use of it untypeable.
        self.variables[name] = self.graph.socket_type(node_id, socket, is_input=False)
        self._loop_indices[name] = (node_id, socket)
        self._value_aliases[name] = (node_id, socket)
        # The declaration that introduced it is now dead, unless something
        # already read it. It cannot be removed here: it may already be in the
        # exec chain, and deleting it would cut the chain in half.
        maker = self._declared_by.pop(name, None)
        if maker and name not in self._read:
            self._dead.add(maker)

    # ------------------------------------------------------------ inference

    def _attribute_type(self, expr: Attribute) -> str:
        if expr.prefix:
            element = PREFIX_TYPES.get(expr.prefix, "float")
        else:
            base = (expr.name.split("_", 1)[-1] if expr.name.startswith("opinput")
                    else expr.name)
            element = STANDARD_ATTRIBUTES.get(base)
            if element is None:
                # Declared once anywhere in the snippet - `v@dir;` at the top,
                # say - and every later bare `@dir` means that type. Guessing
                # float for those wired vectors into float sockets all over,
                # which surfaced as type mismatches far from the real cause.
                element = self._declared_attributes.get(base, "float")
        # `i[]@hits` binds a list of ints, not one.
        return f"{element}[]" if getattr(expr, "is_array", False) else element

    @staticmethod
    def _vector_type(count: int) -> str:
        return {2: "vector2", 3: "vector", 4: "vector4"}.get(count, "vector")

    def _widest(self, operands: list[Expr]) -> str:
        """The type an operation between these operands produces."""
        types = [self._type_of(o) for o in operands]
        best = "int"
        for vex_type in types:
            if WIDTH.get(vex_type, -1) > WIDTH.get(best, -1):
                best = vex_type
        return best

    def _type_of(self, expr: Expr) -> str:
        if isinstance(expr, Literal):
            return expr.kind
        if isinstance(expr, Hscript):
            return "float"
        if isinstance(expr, VectorLiteral):
            return self._vector_type(len(expr.items))
        if isinstance(expr, Attribute):
            return self._attribute_type(expr)
        if isinstance(expr, Name):
            return self.variables.get(expr.name, "float")
        if isinstance(expr, Cast):
            return expr.to
        if isinstance(expr, Unary):
            return "int" if expr.op == "!" else self._type_of(expr.operand)
        if isinstance(expr, Binary):
            if expr.op in ("&&", "||", "==", "!=", "<", ">", "<=", ">="):
                return "int"
            return self._widest([expr.left, expr.right])
        if isinstance(expr, Member):
            return "float"
        if isinstance(expr, Index):
            return self._element_type(expr.target)
        if isinstance(expr, Call):
            signature = self._choose_signature(expr)
            if signature is not None and signature.result:
                definition = self.registry.require(signature.node_type)
                socket = definition.output(signature.result)
                if socket:
                    if socket.type in (vextypes.ANY, vextypes.ANY_ARRAY):
                        # A polymorphic node's best guess before placement is
                        # its Type setting's default; "any" is not an answer.
                        setting = definition.param("type")
                        base = setting.default if setting else "float"
                        return (f"{base}[]" if socket.type == vextypes.ANY_ARRAY
                                else base)
                    return socket.type
        return "float"


# Bare identifiers: not an @attribute, not a .member, not a function being
# called, not a string's contents. Only names already known to be aliases are
# acted on, so the aim here is to avoid false positives, not to be a lexer.
_IDENT_RE = re.compile(r"(?<![@.\w])([A-Za-z_]\w*)\s*(?!\s*\()")
_STRING_RE = re.compile(r'"[^"]*"')


def _identifiers(text: str) -> list[str]:
    return _IDENT_RE.findall(_STRING_RE.sub('""', text))


def _loop_variable_names(statements: list[Statement]) -> set[str]:
    """Names a `foreach` header binds, at any depth."""
    found: set[str] = set()

    def walk(items: list[Statement]) -> None:
        for item in items:
            if isinstance(item, ForEach):
                found.update({item.index_name, item.value_name} - {""})
            for attribute in ("body", "then", "otherwise"):
                nested = getattr(item, attribute, None)
                if isinstance(nested, list):
                    walk(nested)

    walk(statements)
    return found


def _reassigned_names(statements: list[Statement]) -> dict[str, bool]:
    """Variables written to after they are declared.

    Only those need to be variables in the graph; the rest are just names for
    a node's output, and turning them into Make Variable nodes would litter an
    imported graph with boxes that do nothing.
    """
    found: dict[str, bool] = {}

    def walk(items: list[Statement]) -> None:
        for item in items:
            if isinstance(item, Assign):
                if isinstance(item.target, Name):
                    found[item.target.name] = True
                # `pos.z += x` writes pos just as surely as `pos = x` does.
                # Not counting it left pos aliased to the node that made it,
                # and the component write then set a variable that was never
                # declared.
                elif (isinstance(item.target, Member)
                        and isinstance(item.target.target, Name)):
                    found[item.target.target.name] = True
                # And `points[i] = x` writes points the same way.
                elif (isinstance(item.target, Index)
                        and isinstance(item.target.target, Name)):
                    found[item.target.target.name] = True
            for attribute in ("body", "then", "otherwise"):
                nested = getattr(item, attribute, None)
                if isinstance(nested, list):
                    walk(nested)

    walk(statements)
    return found


def import_vex(source: str, registry: Registry) -> Report:
    """Turn a wrangle snippet into a graph. Never raises on unsupported code."""
    importer = Importer(registry, source)
    try:
        statements = syntax.parse(source)
    except SyntaxError as exc:
        # The parser recovers statement by statement, so reaching here means the
        # *lexer* gave up and there are no reliable statement boundaries left.
        # Fall back to splitting on top-level semicolons: crude, but it still
        # beats handing back one opaque block for a stray character.
        report = Report(importer.graph)
        start = importer.graph.add("start", "start")
        previous, previous_socket = start.id, "exec"
        for number, chunk in enumerate(_split_statements(source), start=1):
            node = importer.graph.add("inline_vex", f"inline_{number}", code=chunk)
            importer.graph.connect(previous, previous_socket, node.id, "exec",
                                   is_exec=True)
            previous, previous_socket = node.id, "then"
            report.total += 1
            report.inlined += 1
        report.reasons.append(str(exc))
        return report
    return importer.run(statements)


def _split_statements(source: str) -> list[str]:
    """Break source on semicolons that sit outside braces, brackets and strings."""
    chunks, depth, start, index = [], 0, 0, 0
    in_string = False
    while index < len(source):
        char = source[index]
        if in_string:
            index += 2 if char == "\\" else 1
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == ";" and depth == 0:
            chunk = source[start:index + 1].strip()
            if chunk:
                chunks.append(chunk)
            start = index + 1
        index += 1
    tail = source[start:].strip()
    if tail:
        chunks.append(tail)
    return chunks or [source.strip()]
