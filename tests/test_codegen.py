"""The core's safety net: every graph here must compile with vcc.

The point is not that the emitter produces *some* text — it is that the text is
VEX Houdini accepts. Anything less and the tool is a plausible-code generator,
which is exactly what it exists to replace.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import default_registry, generate  # noqa: E402
from vexgraph.graph import ERROR, Graph  # noqa: E402
from vexgraph.vccmap import compile_check  # noqa: E402


@pytest.fixture(scope="session")
def registry():
    return default_registry()


@pytest.fixture
def graph(registry):
    g = Graph(registry)
    g.add("start", "start")
    return g


def emit(graph: Graph) -> str:
    emission = generate(graph)
    errors = [str(i) for i in emission.issues if i.severity == ERROR]
    assert not errors, "\n".join(errors)
    return emission.code


def compiles(graph: Graph) -> str:
    """Emit, then insist the compiler accepts it."""
    code = emit(graph)
    result = compile_check(generate(graph), graph)
    assert result.ok, f"{code}\n--- vcc said ---\n{result.raw}"
    return code


# --------------------------------------------------------------- the basics

def test_library_loads(registry):
    assert len(registry) > 30
    assert registry.get("attrib_get") is not None


def test_empty_graph_emits_only_a_header(graph):
    assert emit(graph).strip().startswith("//")


def test_push_points_along_normal(graph):
    """The hello-world of wrangles, and the shape of most real graphs."""
    graph.add("attrib_get", "get_p", attrib="P", type="vector")
    graph.add("attrib_get", "get_n", attrib="N", type="vector")
    graph.add("scale", "offset")
    graph.add("add", "moved", type="vector")
    graph.add("attrib_set", "set_p", attrib="P", type="vector")
    graph.chain("start", "set_p")

    graph.connect("get_n", "value", "offset", "value")
    graph.connect("offset", "result", "moved", "b")
    graph.connect("get_p", "value", "moved", "a")
    graph.connect("moved", "result", "set_p", "value")
    graph.nodes["offset"].params["amount"] = "0.1"

    code = compiles(graph)
    assert "@P = @P + @N * 0.1;" in code


def test_repeatable_reads_are_not_given_temporaries(graph):
    """@P used three times should stay @P, not become a variable."""
    graph.add("attrib_get", "get_p", attrib="P", type="vector")
    graph.add("add", "a", type="vector")
    graph.add("add", "b", type="vector")
    graph.add("attrib_set", "set_p", attrib="P", type="vector")
    graph.chain("start", "set_p")
    graph.connect("get_p", "value", "a", "a")
    graph.connect("get_p", "value", "a", "b")
    graph.connect("a", "result", "b", "a")
    graph.connect("get_p", "value", "b", "b")
    graph.connect("b", "result", "set_p", "value")

    code = compiles(graph)
    assert "vector" not in code.split("//")[-1] or "= @P;" not in code


def test_unrepeatable_values_are_computed_once(graph):
    """rand() feeding two places must not be called twice."""
    graph.add("random_number", "r")
    graph.add("add", "sum")
    graph.add("attrib_set", "out", attrib="mass", type="float")
    graph.chain("start", "out")
    graph.connect("r", "value", "sum", "a")
    graph.connect("r", "value", "sum", "b")
    graph.connect("sum", "result", "out", "value")

    code = compiles(graph)
    assert code.count("rand(") == 1


def test_inlined_expression_is_parenthesised(graph):
    """`(a + b) * c` must not come out as `a + b * c`."""
    graph.add("add", "sum")
    graph.add("scale", "scaled")
    graph.add("attrib_set", "out", attrib="mass", type="float")
    graph.chain("start", "out")
    graph.nodes["sum"].params.update(a="1", b="2")
    graph.connect("sum", "result", "scaled", "amount")
    graph.connect("scaled", "result", "out", "value")
    graph.nodes["out"].params["type"] = "vector"

    assert "(1 + 2)" in compiles(graph)


# ------------------------------------------------------------------- scopes

def test_loop_body_is_indented_and_scoped(graph):
    graph.add("for_range", "loop")
    graph.add("add_point", "spawn")
    graph.chain("start", "loop")
    graph.connect("loop", "body", "spawn", "exec", is_exec=True)

    code = compiles(graph)
    assert "for (int" in code
    assert "\n    int" in code, code


def test_value_used_in_both_branches_lands_before_the_if(graph):
    graph.add("random_number", "r")
    graph.add("if_else", "branch")
    graph.add("attrib_set", "hot", attrib="mass", type="float")
    graph.add("attrib_set", "cold", attrib="drag", type="float")
    graph.chain("start", "branch")
    graph.connect("branch", "then", "hot", "exec", is_exec=True)
    graph.connect("branch", "otherwise", "cold", "exec", is_exec=True)
    graph.connect("r", "value", "hot", "value")
    graph.connect("r", "value", "cold", "value")

    code = compiles(graph)
    lines = [l.strip() for l in code.splitlines()]
    assert lines.index([l for l in lines if "rand(" in l][0]) < lines.index("if (0)")


def test_loop_index_cannot_escape_its_loop(graph):
    """The mistake a Blueprint user makes on day one, caught with a sentence."""
    graph.add("for_range", "loop")
    graph.add("note", "inside", text="body")
    graph.add("attrib_set", "after", attrib="mass", type="float")
    graph.chain("start", "loop", "after")
    graph.connect("loop", "body", "inside", "exec", is_exec=True)
    graph.connect("loop", "index", "after", "value")

    issues = generate(graph).issues
    assert any("no longer exists" in i.message or "runs outside" in i.message
               for i in issues), [str(i) for i in issues]


def test_variable_survives_a_loop(graph):
    """The reason Make Variable exists: accumulate inside, read outside."""
    graph.add("var_make", "make", name="total", type="float")
    graph.add("for_range", "loop")
    graph.add("var_set", "bump", name="total", type="float")
    graph.add("var_get", "read", name="total", type="float")
    graph.add("add", "plus")
    graph.add("attrib_set", "out", attrib="mass", type="float")
    graph.chain("start", "make", "loop", "out")
    graph.connect("loop", "body", "bump", "exec", is_exec=True)
    graph.connect("read", "value", "plus", "a")
    graph.nodes["plus"].params["b"] = "1"
    graph.connect("plus", "result", "bump", "value")
    graph.connect("read", "value", "out", "value")

    code = compiles(graph)
    assert "float total = 0;" in code


def test_variable_made_inside_a_loop_is_refused_outside(graph):
    graph.add("for_range", "loop")
    graph.add("var_make", "make", name="total", type="float")
    graph.add("var_get", "read", name="total", type="float")
    graph.add("attrib_set", "out", attrib="mass", type="float")
    graph.chain("start", "loop", "out")
    graph.connect("loop", "body", "make", "exec", is_exec=True)
    graph.connect("read", "value", "out", "value")

    issues = generate(graph).issues
    assert any("does not exist here" in i.message for i in issues), \
        [str(i) for i in issues]


def test_foreach_over_a_list(graph):
    graph.add("nearest_points", "near")
    graph.add("foreach", "each", type="int")
    graph.add("read_point_attribute", "read", attrib="P", type="vector")
    graph.add("attrib_set", "out", attrib="P", type="vector")
    graph.chain("start", "each")
    graph.connect("near", "point_numbers", "each", "items")
    graph.connect("each", "body", "out", "exec", is_exec=True)
    graph.connect("each", "item", "read", "point_number")
    graph.connect("read", "value", "out", "value")

    code = compiles(graph)
    assert "foreach (" in code
    assert "int[]" not in code, "arrays declare their brackets after the name"


def test_list_variable_and_append(graph):
    graph.add("var_make", "make", name="found", type="int[]")
    graph.add("for_range", "loop")
    graph.add("list_append", "push", name="found", type="int")
    graph.chain("start", "make", "loop")
    graph.connect("loop", "body", "push", "exec", is_exec=True)
    graph.connect("loop", "index", "push", "item")

    code = compiles(graph)
    assert "int found[] = {};" in code or "int found[]" in code
    assert "append(found," in code


# ------------------------------------------------------- validation and maps

def test_type_mismatch_is_explained_not_just_rejected(graph):
    graph.add("attrib_get", "get_p", attrib="P", type="vector")
    graph.add("for_range", "loop")
    message = graph.check_connection("get_p", "value", "loop", "count")
    assert "components" in message, message


def test_wire_that_would_loop_is_refused(graph):
    graph.add("add", "a")
    graph.add("add", "b")
    graph.connect("a", "result", "b", "a")
    assert "loop" in graph.check_connection("b", "result", "a", "a")


def test_missing_start_is_reported(registry):
    empty = Graph(registry)
    empty.add("attrib_set", "out", attrib="Cd", type="vector")
    assert any("Start" in i.message for i in empty.validate())


def test_line_map_points_at_the_right_node(graph):
    graph.add("attrib_set", "out", attrib="Cd", type="vector",
              value="{1, 0, 0}")
    graph.chain("start", "out")
    emission = generate(graph)
    line = next(n for n, text in enumerate(emission.code.splitlines(), 1)
                if "@Cd" in text)
    assert emission.node_at_line(line) == "out"


def test_round_trip_through_json(graph, registry, tmp_path):
    graph.add("attrib_set", "out", attrib="Cd", type="vector")
    graph.chain("start", "out")
    graph.nodes["out"].params["value"] = "{1, 0, 0}"
    before = generate(graph).code

    path = tmp_path / "g.vexgraph.json"
    graph.save(path)
    assert generate(Graph.load(path, registry)).code == before


# ------------------------------------------------- every node in the library

@pytest.mark.parametrize("name", [
    "stick_to_surface", "colour_by_proximity", "average_neighbour_colour"])
def test_example_graphs_still_build(registry, name):
    """The examples are documentation, so they must not be allowed to rot."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
    from build_examples import EXAMPLES  # noqa: PLC0415

    compiles(EXAMPLES[name](registry))


def test_generated_names_never_shadow_a_vex_function(registry):
    """`float distance = ...` breaks any later call to distance()."""
    g = Graph(registry)
    g.add("start", "start")
    g.add("distance_between", "gap")
    g.add("attrib_set", "out", attrib="mass", type="float")
    g.chain("start", "out")
    g.connect("gap", "distance", "out", "value")
    # Two consumers, so the value has to become a variable rather than inline.
    g.add("attrib_set", "out2", attrib="drag", type="float")
    g.chain("out", "out2")
    g.connect("gap", "distance", "out2", "value")

    code = compiles(g)
    assert "float distance =" not in code, code


def test_every_curated_node_compiles(registry):
    _check_definitions([d for d in registry if d.tier == 1], registry)


@pytest.mark.slow
def test_every_generated_node_compiles(registry):
    """All ~1300 nodes built from vcc's function list, one vcc run each.

    Slow by nature and worth it: it is the only thing standing between a
    misparsed signature and a node that silently produces broken VEX.
    """
    _check_definitions([d for d in registry if d.tier == 2], registry)


def _check_definitions(definitions, registry) -> None:
    """Instantiate each node on its own and put the result past vcc.

    This is what keeps the library honest: a template with a misremembered
    argument order fails here rather than in someone's scene.
    """
    broken: list[str] = []
    for definition in definitions:
        if definition.type == "start":
            continue
        g = _exercise(registry, definition)
        emission = generate(g)
        errors = [str(i) for i in emission.issues if i.severity == ERROR]
        if errors:
            broken.append(f"{definition.type}: {'; '.join(errors)}")
            continue
        result = compile_check(emission, g)
        if not result.ok:
            broken.append(f"{definition.type}: {result.raw.strip()}")

    assert not broken, "\n".join(broken)


def _exercise(registry, definition) -> Graph:
    """The smallest graph that makes one node definition actually run.

    Required inputs are fed from variables of the matching type, loop-only
    nodes go inside a loop, and a pure node gets something to consume it so it
    is not dropped as dead code.
    """
    g = Graph(registry)
    g.add("start", "start")
    g.add(definition.type, "subject")
    sequence = ["start"]

    # Nodes that name a variable need one to exist before they run.
    if definition.builtin in ("var_set", "var_get", "list_append"):
        label = g.param_value("subject", "name")
        held = g.param_value("subject", "type")
        if definition.builtin == "list_append":
            held += "[]"
        g.add("var_make", "declare", name=label, type=held)
        sequence.append("declare")

    for socket in definition.inputs:
        if socket.default is not None:
            continue
        vex_type = g.socket_type("subject", socket.name, is_input=True)
        label = f"feed_{socket.name}"
        g.add("var_make", label, name=label, type=vex_type)
        g.add("var_get", f"read_{socket.name}", name=label, type=vex_type)
        sequence.append(label)

    if definition.requires_loop:
        g.add("for_range", "loop")
        sequence.append("loop")
    elif definition.has_exec:
        sequence.append("subject")

    if not definition.has_exec:
        out = definition.outputs[0]
        g.add("var_make", "sink", name="probe",
              type=g.socket_type("subject", out.name, is_input=False))
        sequence.append("sink")
        g.connect("subject", out.name, "sink", "value")

    g.chain(*sequence)
    if definition.requires_loop:
        g.connect("loop", "body", "subject", "exec", is_exec=True)
    for socket in definition.inputs:
        if socket.default is None:
            g.connect(f"read_{socket.name}", "value", "subject", socket.name)
    return g
