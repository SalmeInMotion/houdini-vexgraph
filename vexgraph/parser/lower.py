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
        self.by_call: dict[tuple[str, int], Signature] = {}
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
        self.by_call.setdefault(
            (function, len(parts)),
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

    def call(self, name: str, arity: int) -> Signature | None:
        return self.by_call.get((name, arity))

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
        self._counter = 0

    # ------------------------------------------------------------- driving

    def run(self, statements: list[Statement]) -> Report:
        self._reassigned = _reassigned_names(statements)
        self._loop_declared = _loop_variable_names(statements)
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
        """Lower a run of statements, wiring each into the exec chain."""
        for statement in statements:
            self.report.total += 1
            mark = len(self.graph.nodes)
            try:
                node_id = self._statement(statement)
            except Unsupported as exc:
                self._rollback(mark)
                node_id = self._inline(statement, str(exc))
            if node_id:
                self.graph.connect(previous, pin, node_id, "exec", is_exec=True)
                previous, pin = node_id, "exec"
        return previous

    def _rollback(self, mark: int) -> None:
        """Drop the half-built nodes of a statement that turned out unsupported."""
        for node_id in list(self.graph.nodes)[mark:]:
            self.graph.remove(node_id)

    def _inline(self, statement: Statement, reason: str) -> str:
        text = self.source[statement.start:statement.end].strip()
        if not text.endswith((";", "}")):
            text += ";"
        self.report.inlined += 1
        self.report.reasons.append(reason)
        return self.graph.add("inline_vex", self._name("inline"), code=text).id

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
        value = (self._expression(statement.value) if statement.value is not None
                 else Value(literal=vextypes.zero(vex_type), type=vex_type))
        # `matrix3 m = ident();` asks for a 3x3 from a function that returns 4x4.
        # The declared type is the better evidence of intent, so a polymorphic
        # node feeding it is retyped to match rather than being refused.
        if value.is_port and value.type != vex_type:
            self._retype_to(value, vex_type)
        self.variables[statement.name] = vex_type

        # `float d = xyzdist(...)` is one node in a graph, not a call plus a
        # variable: the name is just how the code refers to that output. Only
        # a variable that is written again later needs to be a real variable.
        if value.is_port and not self._reassigned.get(statement.name):
            self._loop_indices[statement.name] = (value.node, value.socket)
            return ""

        node = self.graph.add("var_make", self._name("make"),
                              name=statement.name, type=vex_type)
        self._declared_by[statement.name] = node.id
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
            return node.id

        if isinstance(statement.target, Name):
            name = statement.target.name
            if name not in self.variables:
                raise Unsupported(f"{name} was never declared here")
            node = self.graph.add("var_set", self._name("set"), name=name,
                                  type=self.variables[name])
            self._feed(node.id, "value", self._expression(value))
            return node.id

        if isinstance(statement.target, Member):
            return self._assign_component(statement.target, value)

        raise Unsupported("only attributes and variables can be assigned")

    def _assign_component(self, target: Member, value: Expr) -> str:
        """`@P.x = v` as nodes: read the vector, rebuild it, write it back.

        There is no "poke one component" node and there should not be - a graph
        edge carries a value, not a reference into one. Splitting and remaking
        says the same thing with nodes that already exist, and emits
        `@P = set(v, @P.y, @P.z)`, which is what it means.
        """
        if target.name not in ("x", "y", "z"):
            raise Unsupported(f".{target.name} cannot be assigned")
        if not isinstance(target.target, (Attribute, Name)):
            raise Unsupported("only an attribute or a variable can be rebuilt "
                              "component by component")

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
        return node.id

    def _call_statement(self, statement: ExprStatement) -> str:
        if not isinstance(statement.value, Call):
            raise Unsupported("an expression on its own does nothing")
        signature = self.index.call(statement.value.name,
                                    len(statement.value.args))
        if signature is None:
            raise Unsupported(f"no node calls {statement.value.name}()")
        placed = self._place_call(signature, statement.value).node
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
        self._feed(node.id, "condition", self._expression(statement.condition))
        self._chain(node.id, statement.then, pin="then")
        if statement.otherwise:
            self._chain(node.id, statement.otherwise, pin="otherwise")
        return node.id

    def _for(self, statement: For) -> str:
        count = self._counted_loop(statement)
        if count is None:
            raise Unsupported("only counted loops (for i = 0; i < n; i++) map")
        variable, limit = count
        node = self.graph.add("for_range", self._name("repeat"))
        self._feed(node.id, "count", self._expression(limit))
        self.variables[variable] = "int"
        self._loop_indices[variable] = (node.id, "index")
        self._chain(node.id, statement.body, pin="body")
        return node.id

    @staticmethod
    def _counted_loop(statement: For) -> tuple[str, Expr] | None:
        """Recognise `for (int i = 0; i < N; i++)` and nothing else."""
        setup, condition, step = statement.setup, statement.condition, statement.step
        if not isinstance(setup, Declare) or setup.type != "int":
            return None
        if not (isinstance(setup.value, Literal) and setup.value.text == "0"):
            return None
        if not (isinstance(condition, Binary) and condition.op == "<"
                and isinstance(condition.left, Name)
                and condition.left.name == setup.name):
            return None
        if not (isinstance(step, Assign) and step.op == "+="
                and isinstance(step.target, Name)
                and step.target.name == setup.name
                and isinstance(step.value, Literal) and step.value.text == "1"):
            return None
        return setup.name, condition.right

    def _foreach(self, statement: ForEach) -> str:
        element = statement.value_type or self._element_type(statement.array)
        node = self.graph.add("foreach", self._name("each"), type=element)
        self._feed(node.id, "items", self._expression(statement.array))
        if statement.index_name:
            self.variables[statement.index_name] = "int"
            self._loop_indices[statement.index_name] = (node.id, "index")
        self.variables[statement.value_name] = element
        self._loop_indices[statement.value_name] = (node.id, "item")
        self._chain(node.id, statement.body, pin="body")
        return node.id

    def _element_type(self, array: Expr) -> str:
        vex_type = self._type_of(array)
        return vextypes.element_type(vex_type) if vex_type else "float"

    # ---------------------------------------------------------- expressions

    def _feed(self, node_id: str, socket: str, value: Value) -> None:
        if value.is_port:
            self.graph.connect(value.node, value.socket, node_id, socket)
        else:
            self.graph.nodes[node_id].params[socket] = value.literal

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
            vex_type = self._attribute_type(expr)
            node = self.graph.add("attrib_get", self._name("get"),
                                  attrib=expr.name, type=vex_type)
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
            node = self.graph.add("var_get", self._name("read"),
                                  name=expr.name, type=vex_type)
            return Value(node=node.id, socket="value", type=vex_type)

        if isinstance(expr, Cast):
            # The emitter re-inserts whatever coercion the types require, so a
            # cast that only restates a type is dropped rather than modelled.
            inner = self._expression(expr.value)
            return Value(inner.literal, inner.node, inner.socket, expr.to)

        if isinstance(expr, Unary):
            if expr.op == "-" and isinstance(expr.operand, Literal):
                return Value(literal=f"-{expr.operand.text}",
                             type=expr.operand.kind)
            return self._operator(expr.op, [expr.operand])
        if isinstance(expr, Binary):
            return self._operator(expr.op, [expr.left, expr.right])

        if isinstance(expr, Call):
            signature = self.index.call(expr.name, len(expr.args))
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
            raise Unsupported("a ? b : c has no node; use If")

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

    def _place_call(self, signature, expr: Call) -> Value:
        definition = self.registry.require(signature.node_type)
        node = self.graph.add(signature.node_type,
                              self._name(signature.node_type.replace("vex_", "")))

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
                self._alias(argument.name, node.id, slot.name)

        if not signature.result:
            return Value(node=node.id, socket="", type="")
        output = definition.output(signature.result)
        return Value(node=node.id, socket=signature.result,
                     type=output.type if output else "float")

    def _as_setting(self, argument: Expr, kind: str) -> str:
        """A template argument that is a node setting must be a constant."""
        if isinstance(argument, Literal):
            text = argument.text
            return text[1:-1] if kind == "param_str" and text.startswith('"') else text
        raise Unsupported("a setting on this node must be a constant here")

    def _retype_to(self, value: Value, wanted: str) -> None:
        """Switch a polymorphic node's Type setting so its output is `wanted`."""
        node = self.graph.nodes.get(value.node)
        if node is None:
            return
        setting = self.registry.require(node.type).param("type")
        if (setting is None or value.socket not in setting.retypes
                or wanted not in setting.menu):
            return
        node.params["type"] = wanted
        value.type = wanted

    def _alias(self, name: str, node_id: str, socket: str) -> None:
        """Point a variable name at a node's output instead of a variable."""
        # Ask the graph, not the definition: on a polymorphic node the
        # definition still says "any", and recording that as the variable's type
        # makes every later use of it untypeable.
        self.variables[name] = self.graph.socket_type(node_id, socket, is_input=False)
        self._loop_indices[name] = (node_id, socket)
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
            element = STANDARD_ATTRIBUTES.get(base, "float")
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
            signature = self.index.call(expr.name, len(expr.args))
            if signature and signature.result:
                socket = self.registry.require(
                    signature.node_type).output(signature.result)
                if socket:
                    return socket.type
        return "float"


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
            if isinstance(item, Assign) and isinstance(item.target, Name):
                found[item.target.name] = True
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
