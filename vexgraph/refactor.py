"""Collapse a selection into a user-defined function.

The exact inverse of what the importer does with `int drawLine(...) {...}`:
the wires that entered the selection become parameters, the one value that
left it becomes the return, and the selection itself becomes a call node.

The rules are VEX's own, discovered by asking vcc rather than guessing:
attributes (`@P`, `@ptnum`) are not accessible inside a function, so
attribute *reads* quietly stay outside (their values arrive as parameters)
and attribute *writes* refuse the collapse. Channels (`chf`) work fine
inside a function and travel with their nodes.

`collapse()` mutates the graph it is given and returns "" on success or the
reason it refused. Callers who want a transaction take a `to_dict()`
snapshot first and restore on refusal or on a broken emission - which the
editor does.
"""

from __future__ import annotations

import re

from . import vextypes
from .codegen import VEX_KEYWORDS
from .graph import EXEC_PIN, FunctionSignature, Graph

_IDENT_RE = re.compile(r"[A-Za-z_]\w*")


def _reserved(name: str) -> bool:
    """Names no function or parameter may take.

    The emitter's own keyword list plus every VEX type name: `foreach` fails
    vcc outright, and a function called `vector2` *compiles* but does not
    survive the round trip - the importer reads it as a cast.
    """
    return (name in VEX_KEYWORDS or vextypes.is_valid(name)
            or name in ("set", "array", "result"))


def _mentions_attributes(graph: Graph, node_id: str) -> bool:
    """Whether this node's emission would say `@something`.

    `@P` and friends exist only in the main body of a wrangle. The obvious
    carrier is a Get Attribute node, but `@` also arrives through templates
    (Element Number emits `@elemnum`), through unwired socket defaults
    (Random Range's seed defaults to `@ptnum`) and through values a person
    typed into a row.
    """
    node = graph.nodes[node_id]
    definition = graph.definition(node)
    # The attribute builtins carry no template at all - the emitter writes
    # their `@` itself - so they are known by name.
    if definition.builtin in ("attrib_get", "attrib_set",
                              "attrib_set_component"):
        return True
    if "@" in (definition.expr or "") or "@" in (definition.code or ""):
        return True
    for value in node.params.values():
        if "@" in str(value):
            return True
    for socket in definition.inputs:
        if graph.source_of(node_id, socket.name) is not None:
            continue
        if socket.name not in node.params and "@" in str(socket.default or ""):
            return True
    return False


def collapse(graph: Graph, node_ids: set[str], name: str) -> str:
    if graph.signature is not None:
        return ("This is already inside a function. Collapse from the main "
                "graph instead.")
    if (not name.isidentifier() or not name.isascii() or _reserved(name)):
        return f"{name!r} cannot be a function name."
    if name in graph.functions:
        return f"There is already a function called {name}()."

    selected = {i for i in node_ids if i in graph.nodes}
    selected.discard("start")
    # Pure nodes whose emission would say `@something` hoist themselves out:
    # their values become parameters, because `@P` does not exist inside a
    # function. A *statement* that mentions an attribute cannot be hoisted -
    # it is part of the run order - so it refuses instead, below.
    hoisted = {i for i in selected
               if _mentions_attributes(graph, i)
               and not graph.definition(i).has_exec}
    selected -= hoisted
    # A variable read whose maker is not selected reads outer state; hoisted,
    # the value crosses the edge and arrives as a parameter named after it.
    made_inside = {graph.nodes[i].params.get("name") for i in selected
                   if graph.definition(i).builtin == "var_declare"}
    outer_reads = {i for i in selected
                   if graph.definition(i).builtin == "var_get"
                   and graph.nodes[i].params.get("name") not in made_inside}
    selected -= outer_reads
    if not selected:
        return "Select the nodes to collapse first."

    for node_id in selected:
        definition = graph.definition(node_id)
        if definition.has_exec and _mentions_attributes(graph, node_id):
            return (f"{definition.label} mentions an attribute, which only "
                    f"means something in the main graph. Leave it outside "
                    f"the selection.")
        if definition.type == "inline_vex":
            return ("Inline VEX may mention attributes, which do not exist "
                    "inside a function. Leave it outside the selection.")
        if definition.type.startswith("fn_"):
            return ("Collapsing a call to another function is not supported "
                    "yet.")

    # A variable made inside but mentioned outside would leave that mention
    # dangling. Node mentions the emitter would catch and roll back, but text
    # inside an Inline VEX node it cannot see into - so both refuse here,
    # with the name.
    for node_id, node in graph.nodes.items():
        if node_id in selected:
            continue
        definition = graph.definition(node)
        if (definition.builtin in ("var_get", "var_set", "list_append")
                and node.params.get("name") in made_inside):
            return (f"The variable {node.params.get('name')!r} would move "
                    f"inside the function, but something outside still uses "
                    f"it. Select that too, or leave the Make Variable out.")
        if definition.type == "inline_vex":
            mentioned = set(_IDENT_RE.findall(node.params.get("code", "")))
            clash = mentioned & {m for m in made_inside if m}
            if clash:
                return (f"The variable {sorted(clash)[0]!r} would move "
                        f"inside the function, but Inline VEX text outside "
                        f"still says it.")

    # ------------------------------------------------------------- run order
    statements = [i for i in selected if graph.definition(i).has_exec]
    # Statements living inside a selected scope's bodies, at any depth. A
    # crossing exec link that leaves from one of these is not the selection's
    # exit - it is a body escaping its scope, which would run the unselected
    # remainder unconditionally at the outer level.
    body_members: set[str] = set()
    frontier = [(i, pin) for i in statements
                for pin in graph.definition(i).exec_bodies]
    while frontier:
        owner, pin = frontier.pop()
        for inside in graph.exec_sequence(owner, pin):
            body_members.add(inside)
            if inside in graph.nodes:
                frontier += [(inside, p)
                             for p in graph.definition(inside).exec_bodies]
    entry_from: tuple[str, str] | None = None   # (outside node, its pin)
    entry_node = ""
    exit_to = ""
    for link in graph.links:
        if not link.is_exec:
            continue
        inside_from = link.from_node in selected
        inside_to = link.to_node in selected
        if inside_from and not inside_to:
            if link.from_socket != EXEC_PIN or link.from_node in body_members:
                return ("A loop or branch in the selection runs steps that "
                        "are not selected. Select its whole body with it.")
            if exit_to:
                return "The selection leaves the run order in two places."
            exit_to = link.to_node
        elif inside_to and not inside_from:
            if entry_from is not None:
                return ("The selection is not one continuous run of steps. "
                        "Collapse one stretch at a time.")
            entry_from = (link.from_node, link.from_socket)
            entry_node = link.to_node
    if statements and not entry_node:
        first = [i for i in statements
                 if not any(l.is_exec and l.to_node == i for l in graph.links)]
        if len(first) != 1:
            return ("The selection is not one continuous run of steps. "
                    "Collapse one stretch at a time.")
        entry_node = first[0]

    # ------------------------------------------------- what crosses the edge
    incoming: dict[tuple[str, str], list[tuple[str, str]]] = {}
    outgoing: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for link in graph.links:
        if link.is_exec:
            continue
        if link.to_node in selected and link.from_node not in selected:
            incoming.setdefault((link.from_node, link.from_socket), []).append(
                (link.to_node, link.to_socket))
        elif link.from_node in selected and link.to_node not in selected:
            base = link.from_socket.partition(".")[0]
            outgoing.setdefault((link.from_node, base), []).append(
                (link.to_node, link.to_socket))

    if len(outgoing) > 1:
        return ("The selection produces more than one value used outside. "
                "A function returns one thing; collapse less, or use the "
                "extra value inside.")
    if not outgoing and not statements:
        return ("This selection computes a value nobody outside uses, and "
                "runs nothing. There is no function to make from it.")

    # A call happens ONCE; the expression it replaces was re-composed at
    # every use. Those only agree when there is exactly one consuming
    # statement (or when the value came off a statement in the selection,
    # which was already a fixed, named thing). Two consumers with a write
    # between them would silently read different worlds - and a while
    # condition must be re-composed every single pass, which no call can be.
    consumer_stmt = ""
    if outgoing:
        key = next(iter(outgoing))
        source_id = key[0]
        consumers, feeds_while = _consuming_statements(
            graph, outgoing[key], selected)
        if feeds_while:
            return ("The value drives a While condition, which is "
                    "re-checked every pass - a function call is not. "
                    "Collapse the whole loop instead.")
        if graph.definition(source_id).has_exec:
            pass                        # a statement's output is a fixed value
        elif len(consumers) > 1:
            return ("The value is used by several steps, and a function is "
                    "called once - a change between those steps would be "
                    "missed. Collapse the steps that use it too.")
        consumer_stmt = next(iter(consumers), "")

    params: list[tuple[str, str, bool]] = []       # signature entries
    param_names: dict[tuple[str, str], str] = {}
    # A parameter may not shadow a variable made inside the selection (the
    # read would silently rebind to it), nor take the call template's
    # {result} placeholder, a VEX keyword, or a type name.
    taken: set[str] = {m for m in made_inside if m}
    for key in sorted(incoming):
        source_node, source_socket = key
        vex_type = graph.socket_type(source_node, source_socket,
                                     is_input=False)
        base = _param_name(graph, source_node, source_socket, taken)
        taken.add(base)
        param_names[key] = base
        is_array = vex_type.endswith("[]")
        params.append((vex_type[:-2] if is_array else vex_type, base,
                       is_array))

    returns: tuple[str, str] | None = next(iter(outgoing), None)
    return_type = "void"
    if returns is not None:
        return_type = graph.socket_type(returns[0], returns[1],
                                        is_input=False)
        if return_type.endswith("[]"):
            return "Returning a whole list is not supported yet."

    # --------------------------------------------------- build the function
    signature = FunctionSignature(name=name, return_type=return_type,
                                  params=params)
    inner = Graph(graph.registry, name=name)
    inner.signature = signature
    inner.local_defs.update(graph.local_defs)
    inner.add("start", "start")

    for node_id in selected:
        node = graph.nodes[node_id]
        copy = inner.add(node.type, node.id, **node.params)
        copy.title = node.title
        copy.pos = node.pos
    for link in graph.links:
        if link.from_node in selected and link.to_node in selected:
            inner.connect(link.from_node, link.from_socket,
                          link.to_node, link.to_socket, is_exec=link.is_exec)

    for key, consumers in incoming.items():
        vex_type, pname, is_array = next(
            (t, n, a) for t, n, a in params if n == param_names[key])
        full = f"{vex_type}[]" if is_array else vex_type
        reader = inner.add("var_get", f"param_{pname}",
                           name=pname, type=full)
        for to_node, to_socket in consumers:
            inner.connect(reader.id, "value", to_node, to_socket)

    if statements:
        inner.connect("start", EXEC_PIN, entry_node, EXEC_PIN, is_exec=True)
    tail = inner.exec_sequence("start")
    last = tail[-1] if tail else "start"
    if returns is not None:
        ret = inner.add("return_value", "return", type=return_type)
        inner.connect(returns[0], returns[1], ret.id, "value")
        inner.connect(last, EXEC_PIN, ret.id, EXEC_PIN, is_exec=True)

    # ------------------------------------------------- rewrite the document
    graph.define_function(inner)
    call = graph.add(f"fn_{name}")
    positions = [graph.nodes[i].pos for i in selected]
    call.pos = (sum(p[0] for p in positions) / len(positions),
                sum(p[1] for p in positions) / len(positions))

    for key, targets in outgoing.items():
        for to_node, to_socket in targets:
            original = next(l for l in graph.links
                            if l.to_node == to_node
                            and l.to_socket == to_socket)
            component = original.from_socket.partition(".")[2]
            socket = f"result.{component}" if component else "result"
            graph.connect(call.id, socket, to_node, to_socket)
    for key in incoming:
        graph.connect(key[0], key[1], call.id, param_names[key])

    successor = exit_to
    if statements:
        if entry_from is not None:
            graph.connect(entry_from[0], entry_from[1], call.id, EXEC_PIN,
                          is_exec=True)
    else:
        # A pure selection still has to *run* somewhere: right before its
        # ONE consuming statement (guaranteed above), which is exactly where
        # the expression used to be composed.
        if consumer_stmt:
            before = next((l for l in graph.links
                           if l.is_exec and l.to_node == consumer_stmt), None)
            if before is not None:
                graph.connect(before.from_node, before.from_socket,
                              call.id, EXEC_PIN, is_exec=True)
            successor = consumer_stmt
    if successor:
        graph.connect(call.id, EXEC_PIN, successor, EXEC_PIN, is_exec=True)

    for node_id in selected:
        graph.remove(node_id)
    return ""


def _param_name(graph: Graph, node_id: str, socket: str,
                taken: set[str]) -> str:
    """A readable parameter name derived from what feeds it."""
    node = graph.nodes[node_id]
    definition = graph.definition(node)
    if definition.builtin == "var_get":
        hint = node.params.get("name", "")
    elif definition.builtin == "attrib_get":
        hint = node.params.get("attrib", "")
    else:
        hint = node.title or socket.replace(".", "_")
    hint = "".join(c if (c.isascii() and (c.isalnum() or c == "_")) else "_"
                   for c in hint.lower())
    hint = hint.strip("_")
    if not hint or not hint[0].isalpha():
        hint = "value"
    if _reserved(hint):
        hint += "_in"
    candidate, n = hint, 1
    while candidate in taken:
        n += 1
        candidate = f"{hint}{n}"
    return candidate


def _consuming_statements(graph: Graph, targets: list[tuple[str, str]],
                          gone: set[str]) -> tuple[set[str], bool]:
    """Every statement the escaping value reaches, and whether any of those
    arrivals is a While condition (which no once-evaluated call can serve)."""
    statements: set[str] = set()
    feeds_while = False
    seen: set[str] = set()
    frontier = list(targets)
    while frontier:
        to_node, to_socket = frontier.pop()
        if (to_node, to_socket) in seen or to_node in gone:
            continue
        seen.add((to_node, to_socket))
        definition = graph.definition(to_node)
        if definition.has_exec:
            statements.add(to_node)
            if definition.type == "while" and to_socket == "condition":
                feeds_while = True
            continue
        for link in graph.links:
            if not link.is_exec and link.from_node == to_node:
                frontier.append((link.to_node, link.to_socket))
    return statements, feeds_while
