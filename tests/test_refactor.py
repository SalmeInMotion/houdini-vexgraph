"""Collapsing a selection into a function.

Each case builds a graph by importing VEX (the same path a user's canvas
takes), collapses part of it, and then holds the result to the same bar as
everything else: the emission must compile, and re-importing it must emit
the same text back - functions included.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import Graph, default_registry, generate, refactor  # noqa: E402
from vexgraph.graph import ERROR  # noqa: E402
from vexgraph.parser import import_vex  # noqa: E402
from vexgraph.vccmap import check_source  # noqa: E402


@pytest.fixture(scope="session")
def registry():
    return default_registry()


def _graph(source: str, registry) -> Graph:
    return import_vex(source, registry).graph


def _ids(graph: Graph, *types: str) -> set[str]:
    return {n.id for n in graph.nodes.values() if n.type in types}


def _proven(graph: Graph) -> str:
    emission = generate(graph)
    errors = [str(i) for i in emission.issues if i.severity == ERROR]
    assert not errors, "\n".join(errors)
    check = check_source(emission.code)
    if check.checked:
        assert check.ok, check.raw
    again = generate(import_vex(emission.code, default_registry()).graph)
    assert again.code == emission.code, "collapse must hold the fixed point"
    return emission.code


def test_a_pure_cluster_becomes_a_function_with_parameters(registry):
    graph = _graph("f@d = length(@P - @N) * 2 + 0.5;", registry)
    error = refactor.collapse(
        graph, _ids(graph, "subtract", "multiply", "add", "length"),
        "fancy_measure")
    assert error == ""
    code = _proven(graph)
    assert "float fancy_measure(vector p; vector n)" in code
    assert "fancy_measure(@P, @N)" in code


def test_a_statement_chain_becomes_a_void_function(registry):
    graph = _graph(
        "int a = addpoint(0, @P + {0,1,0});\n"
        "int b = addpoint(0, @P + {0,2,0});\n"
        'addprim(0, "polyline", a, b);', registry)
    error = refactor.collapse(
        graph, _ids(graph, "add_point", "vex_addprim_4", "add"), "rung")
    assert error == ""
    code = _proven(graph)
    assert "void rung(vector p)" in code
    assert "rung(@P);" in code


def test_attribute_writes_refuse_with_a_reason(registry):
    graph = _graph("@Cd = @P * 0.5;", registry)
    error = refactor.collapse(
        graph, _ids(graph, "multiply", "attrib_set"), "paint")
    assert "attribute" in error
    # And nothing changed: the graph still emits exactly what it did.
    assert "@Cd = @P * 0.5;" in generate(graph).code


def test_two_escaping_values_refuse(registry):
    graph = _graph("f@a = @P.x * 2;\nf@b = @P.x * 3;", registry)
    error = refactor.collapse(graph, _ids(graph, "multiply"), "both")
    assert "one thing" in error


def test_collapse_only_makes_sense_from_the_main_graph(registry):
    graph = _graph("int twice(int a){ return a * 2; }\ni@x = twice(3);",
                   registry)
    inner = graph.functions["twice"]
    error = refactor.collapse(inner, set(inner.nodes), "deeper")
    assert "main graph" in error


def test_a_taken_name_refuses(registry):
    graph = _graph("int twice(int a){ return a * 2; }\n"
                   "f@d = length(@P) * 2;", registry)
    error = refactor.collapse(graph, _ids(graph, "length", "multiply"),
                              "twice")
    assert "already" in error


def test_the_collapsed_call_is_a_navigable_function(registry):
    graph = _graph("f@d = length(@P) * 0.1;", registry)
    assert refactor.collapse(graph, _ids(graph, "length", "multiply"),
                             "shrunk") == ""
    assert "shrunk" in graph.functions
    inner = graph.functions["shrunk"]
    assert inner.signature.name == "shrunk"
    assert any(n.type == "return_value" for n in inner.nodes.values())
    assert any(n.type == "fn_shrunk" for n in graph.nodes.values())


# ------------------------------------------- findings of the adversarial review

def test_partially_selected_bodies_refuse(registry):
    """Selecting an If and only part of its body must not free the rest of
    the body to run unconditionally outside its scope."""
    graph = _graph("if (@P.x > 0) {\n    int a = addpoint(0, v@N);\n"
                   "    int b = addpoint(0, v@up);\n}", registry)
    if_id = next(n.id for n in graph.nodes.values() if n.type == "if")
    first = graph.exec_next(if_id, "then")
    assert refactor.collapse(graph, {if_id, first}, "guarded") != ""


def test_a_value_with_two_consuming_statements_refuses(registry):
    """A call happens once; an expression was re-composed at every use. With
    a write between two consumers they would read different worlds."""
    graph = Graph(registry)
    graph.add("start", "start")
    graph.add("multiply", "mul", a="2", b="3", type="float")
    graph.add("var_make", "s1", name="early", type="float")
    graph.add("var_make", "s2", name="late", type="float")
    graph.chain("start", "s1", "s2")
    graph.connect("mul", "result", "s2", "value")   # later wired first
    graph.connect("mul", "result", "s1", "value")
    assert "several steps" in refactor.collapse(graph, {"mul"}, "six")


def test_a_while_condition_refuses(registry):
    graph = _graph("float n = 0;\nwhile (n < 10) {\n    n = n + 1;\n}\n"
                   "f@d = n;", registry)
    error = refactor.collapse(graph, _ids(graph, "is_less"), "keep_going")
    assert "While" in error


def test_attribute_defaults_and_templates_never_enter_a_function(registry):
    """@ arrives through more doors than Get Attribute: socket defaults
    (Random Range's seed is @ptnum) and expr templates (@elemnum)."""
    graph = Graph(registry)
    graph.add("start", "start")
    graph.add("element_number", "en")
    graph.add("multiply", "mul", b="2", type="float")
    graph.connect("en", "number", "mul", "a")
    graph.add("attrib_set", "s2", attrib="A", type="float")
    graph.connect("mul", "result", "s2", "value")
    graph.chain("start", "s2")
    assert refactor.collapse(graph, {"en", "mul"}, "dbl") == ""
    assert "@elemnum" not in _proven(graph).split("dbl(")[0].split("{", 1)[-1]

    lone = Graph(registry)
    lone.add("start", "start")
    lone.add("remove_point", "rm")
    lone.chain("start", "rm")
    assert "attribute" in refactor.collapse(lone, {"rm"}, "cull")


def test_a_parameter_is_never_called_result(registry):
    """An input named like the call template's {result} output placeholder
    rendered the declaration as the argument expression."""
    graph = _graph("f@out = (length(@P) + 1) * 2;", registry)
    assert refactor.collapse(graph, _ids(graph, "multiply"), "dbl") == ""
    code = _proven(graph)
    assert "(float result)" not in code    # result_in or similar is fine


def test_inline_text_outside_keeps_its_variable(registry):
    graph = Graph(registry)
    graph.add("start", "start")
    graph.add("var_make", "mk", name="amp", type="float", value="2")
    graph.add("inline_vex", "inl", code="@P *= amp;")
    graph.chain("start", "mk", "inl")
    assert "amp" in refactor.collapse(graph, {"mk"}, "setup")


def test_an_outer_variable_read_becomes_a_parameter_not_a_shadow(registry):
    graph = Graph(registry)
    graph.add("start", "start")
    graph.add("var_make", "mk", name="d", type="float", value="5")
    source = graph.add("add", "outer_add", type="float", a="1", b="2")
    source.title = "d"
    graph.add("var_get", "rd", name="d", type="float")
    graph.add("add", "inner_add", type="float")
    graph.add("multiply", "mul", type="float", b="2")
    graph.add("attrib_set", "out", attrib="out", type="float")
    graph.connect("rd", "value", "inner_add", "a")
    graph.connect("outer_add", "result", "inner_add", "b")
    graph.connect("inner_add", "result", "mul", "a")
    graph.connect("mul", "result", "out", "value")
    graph.chain("start", "mk", "out")
    assert refactor.collapse(graph, {"rd", "inner_add", "mul"},
                             "calc") == ""
    code = _proven(graph)
    assert "calc(1 + 2, d)" in code    # outer d arrives as an argument


def test_reserved_and_non_ascii_names_refuse(registry):
    for bad in ("foreach", "export", "break", "vector2", "dict", "año"):
        graph = _graph("f@d = length(@P - @N) * 2 + 0.5;", registry)
        error = refactor.collapse(
            graph, _ids(graph, "subtract", "multiply", "add", "length"), bad)
        assert error != "", bad
