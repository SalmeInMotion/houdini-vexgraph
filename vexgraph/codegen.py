"""Turn a graph into VEX a person would be willing to read.

Two orderings have to be reconciled. Exec wires say what happens in what order,
and that maps straight onto statements. Data wires say only what depends on
what, and a value has to be computed somewhere sensible — which is the whole
problem, because "somewhere sensible" depends on loops.

The rule is: a value is computed in the deepest scope that still comes before
every use of it. If both branches of an If need it, it lands before the If. If
only the inside of a loop needs it, it lands inside the loop, where a reader
expects to find it. And if it depends on a loop's index but is used after the
loop has ended, that is not a placement problem, it is a mistake, and it is
reported as one.

The other job here is the line map. Every emitted line remembers which node
produced it, so a compiler error on line 9 can turn a node red instead of
turning into a message about code the user never wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import vextypes
from .graph import EXEC_PIN, ERROR, Graph, Issue, Node, WARNING
from .nodedefs import PLACEHOLDER_RE, NodeDef

HEADER = "// Built with VEXgraph. Edit the graph, not this code."

# Reserved words plus the handful of globals a wrangle always has. A generated
# variable colliding with one of these compiles into something baffling.
VEX_KEYWORDS = {
    "if", "else", "for", "foreach", "while", "do", "break", "continue",
    "return", "function", "struct", "export", "const", "illuminance",
    "gather", "int", "float", "vector", "vector2", "vector4", "matrix",
    "matrix2", "matrix3", "string", "dict", "void", "bsdf", "cvex",
    "surface", "displace", "light", "shadow", "fog", "this",
}

IDENT_RE = re.compile(r"[^0-9a-zA-Z_]+")

# Operator precedence, loosest first. Used to decide whether an expression being
# inlined needs brackets around it. Wrapping everything would be correct and
# unreadable, and unreadable defeats the purpose: the generated VEX is meant to
# teach, and `@P + @N * 0.1` teaches where `(@P + (@N * 0.1))` does not.
PRECEDENCE = {
    "||": 2, "&&": 3,
    "==": 4, "!=": 4,
    "<": 5, ">": 5, "<=": 5, ">=": 5,
    "+": 6, "-": 6,
    "*": 7, "/": 7, "%": 7,
    "[": 9, ".": 9,
}
ATOM = 9
# `a - (b - c)` and `a / (b / c)` need their brackets; `a + (b + c)` does not.
NON_ASSOCIATIVE = {"-", "/", "%"}
OPERATOR_CHARS = set("|&=!<>+-*/%")
# Two-character operators have to be matched before their first character is.
LONG_OPERATORS = ("||", "&&", "==", "!=", "<=", ">=")


def loosest_precedence(expr: str) -> int:
    """The precedence of the weakest operator at the top level of `expr`.

    Anything nested inside brackets, braces or quotes is already grouped and so
    cannot change how the expression binds to its surroundings.
    """
    depth = 0
    in_string = False
    weakest = ATOM
    index = 0
    previous = ""
    while index < len(expr):
        char = expr[index]
        if in_string:
            in_string = char != '"'
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and char in OPERATOR_CHARS:
            token = next((op for op in LONG_OPERATORS
                          if expr.startswith(op, index)), char)
            # A sign with nothing to its left is unary, and binds tightly.
            if previous or token not in "-!":
                weakest = min(weakest, PRECEDENCE.get(token, ATOM))
            index += len(token)
            previous = ""
            continue
        if not char.isspace():
            previous = char
        index += 1
    return weakest


@dataclass(frozen=True)
class Line:
    text: str            # already carries its own indentation
    node_id: str = ""


@dataclass
class Emission:
    code: str
    line_nodes: dict[int, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == ERROR for i in self.issues)

    def node_at_line(self, line_no: int) -> str:
        return self.line_nodes.get(line_no, "")


@dataclass
class Scope:
    owner: str = ""              # node id of the scope node, "" for the root
    body: str = ""               # which body pin, "" for the root
    parent: "Scope | None" = None
    statements: list[str] = field(default_factory=list)

    @property
    def depth(self) -> int:
        return 0 if self.parent is None else self.parent.depth + 1

    def ancestry(self) -> list["Scope"]:
        chain, node = [], self
        while node is not None:
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    def contains(self, other: "Scope") -> bool:
        """True if `other` is this scope or nested inside it."""
        node = other
        while node is not None:
            if node is self:
                return True
            node = node.parent
        return False

    def label(self) -> str:
        return f"{self.owner}.{self.body}" if self.owner else "the graph"


def lowest_common_scope(scopes: list[Scope]) -> Scope:
    chains = [s.ancestry() for s in scopes]
    common = chains[0]
    for chain in chains[1:]:
        shared = []
        for a, b in zip(common, chain):
            if a is not b:
                break
            shared.append(a)
        common = shared
    return common[-1]


class NameAllocator:
    """Readable identifiers, because the generated code is meant to be read."""

    def __init__(self, reserved: set[str] | None = None) -> None:
        self.used: set[str] = set(VEX_KEYWORDS) | (reserved or set())

    def alloc(self, hint: str) -> str:
        base = IDENT_RE.sub("_", hint.strip().lower()).strip("_") or "value"
        if base[0].isdigit():
            base = f"v_{base}"
        # A plain word first, then a suffixed one, and only then numbers. Going
        # straight to numbers produces `distance3` — the name is taken because
        # `distance` and `distance2` are both VEX functions, but it reads as if
        # there were two other distances somewhere.
        for candidate in (base, f"{base}_value"):
            if candidate not in self.used:
                self.used.add(candidate)
                return candidate
        for n in range(2, 1000):
            candidate = f"{base}_value{n}"
            if candidate not in self.used:
                self.used.add(candidate)
                return candidate
        raise RuntimeError(f"could not find a free name for {hint!r}")


class Emitter:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.issues: list[Issue] = []
        self.root = Scope()
        self.scope_of: dict[str, Scope] = {}          # statement node -> scope
        self.bodies: dict[tuple[str, str], Scope] = {}
        self.placement: dict[str, Scope] = {}          # pure node -> scope
        self.var_of: dict[tuple[str, str], str] = {}   # (node, socket) -> name
        self.emitted: set[str] = set()
        self.variables: dict[str, tuple[str, str, Scope]] = {}  # user vars
        self.names = NameAllocator(self._reserved_function_names())

    # ---------------------------------------------------------------- public

    def emit(self) -> Emission:
        self.issues += self.graph.validate()
        if any(i.severity == ERROR for i in self.issues):
            return Emission("", {}, self.issues)

        entries = self.graph.entry_nodes()
        self._build_scopes(entries[0].id)
        self._place_pure_nodes()
        self._check_loop_only_nodes()

        lines = [Line(HEADER)] + self._emit_scope(self.root)
        code = "\n".join(l.text for l in lines) + "\n"
        line_nodes = {i: l.node_id for i, l in enumerate(lines, start=1)
                      if l.node_id}
        return Emission(code, line_nodes, self.issues)

    # ------------------------------------------------------------- structure

    def _reserved_function_names(self) -> set[str]:
        """Never name a variable after a VEX function.

        `float distance = ...` compiles, right up until the same snippet calls
        `distance()`, at which point the error blames the call rather than the
        node whose label produced the name.
        """
        return {re.sub(r"_\d+$", "", d.type[4:]) for d in self.graph.registry
                if d.tier == 2 and d.type.startswith("vex_")}

    def _build_scopes(self, entry_id: str) -> None:
        """Walk the exec chain, opening a scope for every body pin."""
        seen: set[str] = set()

        def walk(first_id: str, scope: Scope) -> None:
            current: str | None = first_id
            while current:
                if current in seen:
                    self.issues.append(Issue(
                        "The sequence loops back on itself. Use a For Each "
                        "node to repeat something.", current, EXEC_PIN))
                    return
                seen.add(current)
                scope.statements.append(current)
                self.scope_of[current] = scope
                definition = self.graph.definition(current)
                for body in definition.exec_bodies:
                    child = Scope(owner=current, body=body, parent=scope)
                    self.bodies[(current, body)] = child
                    first = self.graph.exec_next(current, body)
                    if first:
                        walk(first, child)
                current = self.graph.exec_next(current, EXEC_PIN)

        walk(entry_id, self.root)

    def _value_scope(self, node_id: str, socket: str) -> Scope:
        """Where the value on this output actually exists."""
        definition = self.graph.definition(node_id)
        if definition.kind == "pure":
            return self.placement[node_id]
        socket_def = definition.output(socket)
        if socket_def is not None and socket_def.scope:
            return self.bodies[(node_id, socket_def.scope)]
        return self.scope_of.get(node_id, self.root)

    def _place_pure_nodes(self) -> None:
        """Decide, for each pure node, which scope computes it.

        Resolved forwards along the data flow — a node's home follows from its
        consumers, and the recursion always ends at a statement, which already
        has a scope of its own.
        """
        resolving: set[str] = set()

        def place(node_id: str) -> Scope:
            if node_id in self.placement:
                return self.placement[node_id]
            if node_id in resolving:
                # connect() refuses these, but a hand-edited file can contain one.
                self.issues.append(Issue(
                    "These nodes feed each other in a circle.", node_id))
                self.placement[node_id] = self.root
                return self.root
            resolving.add(node_id)

            consumer_scopes: list[Scope] = []
            for link in self.graph.consumers_of(node_id):
                target = self.graph.definition(link.to_node)
                consumer_scopes.append(
                    place(link.to_node) if target.kind == "pure"
                    else self.scope_of.get(link.to_node, self.root))

            scope = lowest_common_scope(consumer_scopes) if consumer_scopes \
                else self.root
            resolving.discard(node_id)
            self.placement[node_id] = scope
            return scope

        for node in self.graph.nodes.values():
            if self.graph.definition(node).kind == "pure":
                place(node.id)

        self._check_scope_escapes()

    def _check_scope_escapes(self) -> None:
        """Catch a value that is used where it does not exist yet.

        The loop index of a For Each is the usual one: wiring it into something
        after the loop looks reasonable on the canvas and is meaningless in the
        generated code.
        """
        for node in self.graph.nodes.values():
            definition = self.graph.definition(node)
            if definition.kind != "pure" or node.id not in self.placement:
                continue
            home = self.placement[node.id]
            for socket in definition.inputs:
                link = self.graph.source_of(node.id, socket.name)
                if link is None:
                    continue
                source_scope = self._value_scope(link.from_node, link.from_socket)
                if not source_scope.contains(home):
                    self.issues.append(Issue(
                        f"{socket.title} comes from inside "
                        f"{self._scope_name(source_scope)}, but it is used "
                        f"outside it, where that value no longer exists.",
                        node.id, socket.name))

        for node_id, scope in self.scope_of.items():
            definition = self.graph.definition(node_id)
            for socket in definition.inputs:
                link = self.graph.source_of(node_id, socket.name)
                if link is None:
                    continue
                source_scope = self._value_scope(link.from_node, link.from_socket)
                if not source_scope.contains(scope):
                    self.issues.append(Issue(
                        f"{socket.title} comes from inside "
                        f"{self._scope_name(source_scope)}, but this node runs "
                        f"outside it.", node_id, socket.name))

    def _check_loop_only_nodes(self) -> None:
        loops = {d.type for d in self.graph.registry if d.exec_bodies
                 and any(o.scope for o in d.outputs)}
        for node_id, scope in self.scope_of.items():
            if not self.graph.definition(node_id).requires_loop:
                continue
            if not any(s.owner and self.graph.nodes[s.owner].type in loops
                       for s in scope.ancestry()):
                label = self.graph.definition(node_id).label
                self.issues.append(Issue(
                    f"{label} only works inside a Repeat or For Each. Move it "
                    f"into the loop's body.", node_id))

    def _scope_name(self, scope: Scope) -> str:
        if not scope.owner:
            return "the graph"
        node = self.graph.nodes.get(scope.owner)
        label = node.title or self.graph.definition(node).label if node else scope.owner
        return f"the {label}"

    # -------------------------------------------------------------- emission

    def _emit_scope(self, scope: Scope) -> list[Line]:
        lines: list[Line] = []
        for statement_id in scope.statements:
            for pure_id in self._pending_pure(statement_id, scope):
                lines += self._declare_pure(pure_id)
            lines += self._emit_statement(statement_id)
        return lines

    def _pending_pure(self, statement_id: str, scope: Scope) -> list[str]:
        """Pure nodes this statement needs that belong here and aren't out yet.

        "Needs" reaches through nested bodies: a value used only deep inside an
        If still has to be computed before the If if that is where it lives.
        """
        wanted = self._demand_closure(statement_id)
        pending = [n for n in wanted
                   if self.placement.get(n) is scope and n not in self.emitted
                   and not self._is_inlined(n)]
        return self._topological(pending)

    def _demand_closure(self, statement_id: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def visit_data(node_id: str) -> None:
            for socket in self.graph.definition(node_id).inputs:
                link = self.graph.source_of(node_id, socket.name)
                if link is None:
                    continue
                source = link.from_node
                if self.graph.definition(source).kind != "pure":
                    continue
                if source in seen:
                    continue
                seen.add(source)
                visit_data(source)
                out.append(source)

        def visit_statement(node_id: str) -> None:
            visit_data(node_id)
            for body in self.graph.definition(node_id).exec_bodies:
                nested = self.bodies.get((node_id, body))
                if nested:
                    for inner in nested.statements:
                        visit_statement(inner)

        visit_statement(statement_id)
        return out

    def _topological(self, node_ids: list[str]) -> list[str]:
        order: list[str] = []
        placed: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in placed:
                return
            placed.add(node_id)
            for socket in self.graph.definition(node_id).inputs:
                link = self.graph.source_of(node_id, socket.name)
                if link and link.from_node in node_ids:
                    visit(link.from_node)
            order.append(node_id)

        for node_id in node_ids:
            visit(node_id)
        return order

    def _is_inlined(self, node_id: str) -> bool:
        """Whether this node's value goes straight into whatever uses it.

        Worth doing: `@P + @N * 0.1` beats three temporaries for the same thing.
        Only safe when re-evaluating costs nothing and gives the same answer, or
        when there is exactly one consumer to inline into.
        """
        definition = self.graph.definition(node_id)
        if not definition.expr or definition.builtin:
            return False
        if definition.repeatable:
            return True
        return len(self.graph.consumers_of(node_id)) <= 1

    def _declare_pure(self, node_id: str) -> list[Line]:
        self.emitted.add(node_id)
        node = self.graph.nodes[node_id]
        definition = self.graph.definition(node)
        if definition.builtin:
            return self._emit_builtin(node, definition)

        if definition.expr:
            # Not inlinable (used more than once, and not free to repeat), so
            # it becomes a real variable.
            socket = definition.outputs[0]
            name = self._name_for(node, socket.name)
            vex_type = self.graph.socket_type(node, socket.name, is_input=False)
            expr = self._render(definition.expr, node)
            return [Line(f"{vex_type} {name} = {expr};", node_id)]

        return self._render_block(definition.code, node)

    def _emit_statement(self, node_id: str) -> list[Line]:
        self.emitted.add(node_id)
        node = self.graph.nodes[node_id]
        definition = self.graph.definition(node)
        if definition.builtin:
            return self._emit_builtin(node, definition)
        if not definition.code:
            return []                      # Start and other markers emit nothing
        return self._render_block(definition.code, node)

    # ------------------------------------------------------------- templates

    def _name_for(self, node: Node, socket_name: str) -> str:
        key = (node.id, socket_name)
        if key not in self.var_of:
            builtin = self.graph.definition(node).builtin
            # Reading a variable is not a new value: it resolves to the name the
            # declaration already allocated, which is the whole point of it.
            if builtin == "var_get":
                entry = self._lookup_variable(node, self.graph.param_value(node, "name"))
                self.var_of[key] = entry[0] if entry else "0"
                return self.var_of[key]
            if builtin == "attrib_get":
                self.var_of[key] = vextypes.attribute_reference(
                    self.graph.param_value(node, "attrib"),
                    self.graph.param_value(node, "type"))
                return self.var_of[key]
            definition = self.graph.definition(node)
            socket = definition.output(socket_name)
            # Prefer what the user renamed the node to; it is the most likely
            # to describe what the value actually means in this graph.
            hint = node.title or (socket.title if socket and len(definition.outputs) > 1
                                  else definition.label)
            self.var_of[key] = self.names.alloc(hint)
        return self.var_of[key]

    @staticmethod
    def _context(template: str, start: int, end: int) -> tuple[int, bool]:
        """How tightly the text around a placeholder binds to it.

        Returns the precedence to beat, and whether the placeholder sits on the
        right of an operator that does not associate.
        """
        before = template[:start].rstrip()
        after = template[end:].lstrip()

        def operator_at_end(text: str) -> str:
            for op in LONG_OPERATORS:
                if text.endswith(op):
                    return op
            return text[-1] if text and text[-1] in OPERATOR_CHARS else ""

        def operator_at_start(text: str) -> str:
            for op in LONG_OPERATORS:
                if text.startswith(op):
                    return op
            return text[0] if text and text[0] in "([{.*/%+-<>" else ""

        left = operator_at_end(before)
        right = operator_at_start(after)
        # An operator to the left with nothing before it is unary, and a unary
        # minus binds its operand as tightly as a bracket would.
        left_precedence = PRECEDENCE.get(left, 0) if left else 0
        right_precedence = PRECEDENCE.get(right, 0) if right else 0
        return (max(left_precedence, right_precedence),
                bool(left) and left in NON_ASSOCIATIVE)

    def _input_expression(self, node: Node, socket_name: str,
                          context: int = 0, right_of_nonassoc: bool = False
                          ) -> str:
        definition = self.graph.definition(node)
        socket = definition.input(socket_name)
        link = self.graph.source_of(node.id, socket_name)
        want = self.graph.socket_type(node, socket_name, is_input=True)

        if link is None:
            value = node.params.get(socket_name)
            # A socket whose type is chosen per instance cannot have a fixed
            # literal default: "0" is right for a float and nonsense for a list.
            if value is None and socket is not None and socket.type in (
                    vextypes.ANY, vextypes.ANY_ARRAY):
                return vextypes.zero(want)
            if value is None:
                value = socket.default if socket else None
            return vextypes.zero(want) if value is None else str(value)

        source_node = self.graph.nodes[link.from_node]
        source_def = self.graph.definition(source_node)
        got = self.graph.socket_type(source_node, link.from_socket, is_input=False)

        if source_def.kind == "pure" and self._is_inlined(link.from_node):
            expr = self._render(source_def.expr, source_node).strip()
            binding = loosest_precedence(expr)
            if binding < context or (binding == context and right_of_nonassoc
                                     and context > 0):
                expr = f"({expr})"
        else:
            expr = self._name_for(source_node, link.from_socket)

        return vextypes.cast_expression(expr, got, want)

    def _substitution(self, node: Node, name: str, context: int = 0,
                      right_of_nonassoc: bool = False) -> str | None:
        """What a single `{placeholder}` resolves to."""
        definition = self.graph.definition(node)
        if definition.input(name) is not None:
            return self._input_expression(node, name, context, right_of_nonassoc)
        if definition.output(name) is not None:
            return self._name_for(node, name)
        param = definition.param(name)
        if param is not None:
            return node.params.get(name, param.default)
        return None

    def _render(self, template: str, node: Node) -> str:
        def replace(match: re.Match) -> str:
            context, right_of_nonassoc = self._context(
                template, match.start(), match.end())
            value = self._substitution(node, match.group(1), context,
                                       right_of_nonassoc)
            return match.group(0) if value is None else value

        return PLACEHOLDER_RE.sub(replace, template)

    def _render_block(self, template: str, node: Node) -> list[Line]:
        """Render a statement template, splicing bodies in at their indent."""
        definition = self.graph.definition(node)
        lines: list[Line] = []

        for raw_line in template.split("\n"):
            body = self._body_placeholder(raw_line, definition)
            if body is None:
                rendered = self._render(raw_line, node)
                # A substituted value can itself be multi-line — hand-written
                # VEX in an Inline node is the usual case. Splitting keeps one
                # Line per real line, so the line map still points at the node.
                indent = raw_line[:len(raw_line) - len(raw_line.lstrip())]
                for index, part in enumerate(rendered.split("\n")):
                    lines.append(Line(part if index == 0 else indent + part,
                                      node.id))
                continue
            indent = raw_line[:len(raw_line) - len(raw_line.lstrip())]
            nested = self.bodies.get((node.id, body))
            inner = self._emit_scope(nested) if nested else []
            if not inner:
                # An empty body still needs something between the braces or the
                # generated code reads as if a step went missing.
                inner = [Line(f"// nothing connected to {body}", node.id)]
            lines += [Line(indent + l.text, l.node_id) for l in inner]

        return lines

    @staticmethod
    def _body_placeholder(raw_line: str, definition: NodeDef) -> str | None:
        for name in PLACEHOLDER_RE.findall(raw_line):
            if name in definition.exec_bodies:
                return name
        return None

    # -------------------------------------------------------------- builtins

    def _emit_builtin(self, node: Node, definition: NodeDef) -> list[Line]:
        handler = getattr(self, f"_builtin_{definition.builtin}", None)
        if handler is None:
            self.issues.append(Issue(
                f"{definition.label} needs emitter support that is missing "
                f"({definition.builtin}).", node.id))
            return []
        return handler(node, definition)

    def _builtin_start(self, node: Node, definition: NodeDef) -> list[Line]:
        return []

    def _builtin_attrib_get(self, node: Node, definition: NodeDef) -> list[Line]:
        return []   # An attribute read is an expression; see _name_for.

    def _builtin_attrib_set(self, node: Node, definition: NodeDef) -> list[Line]:
        name = self.graph.param_value(node, "attrib")
        vex_type = self.graph.param_value(node, "type")
        value = self._input_expression(node, "value")
        return [Line(f"{vextypes.attribute_reference(name, vex_type)} = {value};",
                     node.id)]

    def _builtin_var_declare(self, node: Node, definition: NodeDef) -> list[Line]:
        label = self.graph.param_value(node, "name")
        vex_type = self.graph.param_value(node, "type")
        if label in self.variables:
            self.issues.append(Issue(
                f"There is already a variable called {label!r}. Use Set "
                f"Variable to change it, or give this one another name.",
                node.id))
            return []
        name = self.names.alloc(label)
        scope = self.scope_of.get(node.id, self.root)
        self.variables[label] = (name, vex_type, scope)
        value = self._input_expression(node, "value")
        return [Line(f"{vextypes.declaration(vex_type, name)} = {value};",
                     node.id)]

    def _builtin_list_append(self, node: Node, definition: NodeDef) -> list[Line]:
        label = self.graph.param_value(node, "name")
        entry = self._lookup_variable(node, label)
        if entry is None:
            return []
        name, vex_type, _ = entry
        if not vextypes.is_array(vex_type):
            self.issues.append(Issue(
                f"{label!r} holds a single {vex_type}, not a list. Change its "
                f"Make Variable node to a list type.", node.id))
            return []
        return [Line(f"append({name}, {self._input_expression(node, 'item')});",
                     node.id)]

    def _builtin_var_set(self, node: Node, definition: NodeDef) -> list[Line]:
        label = self.graph.param_value(node, "name")
        entry = self._lookup_variable(node, label)
        if entry is None:
            return []
        name, _, _ = entry
        value = self._input_expression(node, "value")
        return [Line(f"{name} = {value};", node.id)]

    def _builtin_var_get(self, node: Node, definition: NodeDef) -> list[Line]:
        return []   # Reading a variable emits nothing; see _substitution below.

    def _lookup_variable(self, node: Node,
                         label: str) -> tuple[str, str, Scope] | None:
        entry = self.variables.get(label)
        if entry is None:
            self.issues.append(Issue(
                f"There is no variable called {label!r}. Add a Make Variable "
                f"node before this one.", node.id))
            return None
        _, _, declared_in = entry
        here = self.scope_of.get(node.id) or self.placement.get(node.id, self.root)
        if not declared_in.contains(here):
            self.issues.append(Issue(
                f"{label!r} is made inside {self._scope_name(declared_in)}, so "
                f"it does not exist here. Make it earlier, outside.", node.id))
        return entry


def generate(graph: Graph) -> Emission:
    return Emitter(graph).emit()
