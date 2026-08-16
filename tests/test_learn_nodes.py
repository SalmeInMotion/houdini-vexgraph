"""Every exercise, built the way a student clicks it.

The course's reference solutions are VEX, but a student assembles NODES -
and the two paths emit slightly different (equivalent) code: a Make Vector
writes `set(1, 0, 0)` where a typed value writes `{1, 0, 0}`. These
builders follow each exercise's own instructions, node by node, and demand
that the checks pass and the result compiles. If an instruction ever names
a node that cannot do what it claims, this file goes red - not the learner.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import Graph, default_registry, generate, learn  # noqa: E402
from vexgraph.graph import ERROR  # noqa: E402
from vexgraph.vccmap import check_source  # noqa: E402

REG = default_registry()


def new():
    g = Graph(REG)
    g.add("start", "start")
    return g


def paint(g):
    mk = g.add("make_vector", "mk", x="1", y="0", z="0")
    st = g.add("attrib_set", "set", attrib="Cd", type="vector")
    g.connect(mk.id, "result", st.id, "value")
    g.chain("start", st.id)


def flatten(g):
    st = g.add("attrib_set_component", "set", attrib="P", component="y",
               type="vector", value="0")
    g.chain("start", st.id)


def inflate(g):
    n = g.add("attrib_get", "getN", attrib="N", type="vector")
    p = g.add("attrib_get", "getP", attrib="P", type="vector")
    mul = g.add("multiply", "mul", type="vector", b="0.1")
    add = g.add("add", "add", type="vector")
    st = g.add("attrib_set", "set", attrib="P", type="vector")
    g.connect(n.id, "value", mul.id, "a")
    g.connect(p.id, "value", add.id, "a")
    g.connect(mul.id, "result", add.id, "b")
    g.connect(add.id, "result", st.id, "value")
    g.chain("start", st.id)


def dial(g):
    n = g.add("attrib_get", "getN", attrib="N", type="vector")
    p = g.add("attrib_get", "getP", attrib="P", type="vector")
    mul = g.add("multiply", "mul", type="vector", b='chf("amount")')
    add = g.add("add", "add", type="vector")
    st = g.add("attrib_set", "set", attrib="P", type="vector")
    g.connect(n.id, "value", mul.id, "a")
    g.connect(p.id, "value", add.id, "a")
    g.connect(mul.id, "result", add.id, "b")
    g.connect(add.id, "result", st.id, "value")
    g.chain("start", st.id)


def stripes(g):
    num = g.add("attrib_get", "getnum", attrib="ptnum", type="int")
    mod = g.add("modulo", "mod", type="int", b="2")
    eq = g.add("is_equal", "eq", b="0")
    branch = g.add("if_else", "branch")
    white_v = g.add("make_vector", "white_v", x="1", y="1", z="1")
    black_v = g.add("make_vector", "black_v", x="0", y="0", z="0")
    white = g.add("attrib_set", "white", attrib="Cd", type="vector")
    black = g.add("attrib_set", "black", attrib="Cd", type="vector")
    g.connect(num.id, "value", mod.id, "a")
    g.connect(mod.id, "result", eq.id, "a")
    g.connect(eq.id, "result", branch.id, "condition")
    g.connect(white_v.id, "result", white.id, "value")
    g.connect(black_v.id, "result", black.id, "value")
    g.connect(branch.id, "then", white.id, "exec", is_exec=True)
    g.connect(branch.id, "otherwise", black.id, "exec", is_exec=True)
    g.chain("start", branch.id)


def gradient(g):
    p = g.add("attrib_get", "getP", attrib="P", type="vector")
    fit = g.add("fit_range", "fit", old_min="-1", old_max="1",
                new_min="0", new_max="1", type="float")
    mk = g.add("make_vector", "mk")
    st = g.add("attrib_set", "set", attrib="Cd", type="vector")
    g.connect(p.id, "value.y", fit.id, "value")
    for socket in ("x", "y", "z"):
        g.connect(fit.id, "result", mk.id, socket)
    g.connect(mk.id, "result", st.id, "value")
    g.chain("start", st.id)


def jitter(g):
    num = g.add("attrib_get", "getnum", attrib="ptnum", type="int")
    rnd = g.add("random_vector", "rnd")
    sub = g.add("subtract", "sub", type="vector", b="0.5")
    mul = g.add("multiply", "mul", type="vector", b="0.1")
    p = g.add("attrib_get", "getP", attrib="P", type="vector")
    add = g.add("add", "add", type="vector")
    st = g.add("attrib_set", "set", attrib="P", type="vector")
    g.connect(num.id, "value", rnd.id, "seed")
    g.connect(rnd.id, "value", sub.id, "a")
    g.connect(sub.id, "result", mul.id, "a")
    g.connect(p.id, "value", add.id, "a")
    g.connect(mul.id, "result", add.id, "b")
    g.connect(add.id, "result", st.id, "value")
    g.chain("start", st.id)


def falloff(g):
    p = g.add("attrib_get", "getP", attrib="P", type="vector")
    length = g.add("length", "len")
    fit = g.add("fit_range", "fit", old_min="0", old_max="2",
                new_min="1", new_max="0", type="float")
    st = g.add("attrib_set", "set", attrib="pscale", type="float")
    g.connect(p.id, "value", length.id, "vector")
    g.connect(length.id, "length", fit.id, "value")
    g.connect(fit.id, "result", st.id, "value")
    g.chain("start", st.id)


def lookat(g):
    p = g.add("attrib_get", "getP", attrib="P", type="vector")
    neg = g.add("negate", "neg", type="vector")
    nor = g.add("normalize", "nor")
    st = g.add("attrib_set", "set", attrib="N", type="vector")
    g.connect(p.id, "value", neg.id, "value")
    g.connect(neg.id, "result", nor.id, "vector")
    g.connect(nor.id, "direction", st.id, "value")
    g.chain("start", st.id)


def trail(g):
    loop = g.add("for_range", "loop", count="5")
    step = g.add("multiply", "step", type="float", b="0.1")
    n = g.add("attrib_get", "getN", attrib="N", type="vector")
    push = g.add("multiply", "push", type="vector")
    p = g.add("attrib_get", "getP", attrib="P", type="vector")
    add = g.add("add", "add", type="vector")
    pt = g.add("add_point", "pt")
    g.connect(loop.id, "index", step.id, "a")
    g.connect(n.id, "value", push.id, "a")
    g.connect(step.id, "result", push.id, "b")
    g.connect(p.id, "value", add.id, "a")
    g.connect(push.id, "result", add.id, "b")
    g.connect(add.id, "result", pt.id, "position")
    g.connect(loop.id, "body", pt.id, "exec", is_exec=True)
    g.chain("start", loop.id)


BUILDERS = {
    "paint": paint, "flatten": flatten, "inflate": inflate, "dial": dial,
    "stripes": stripes, "gradient": gradient, "jitter": jitter,
    "falloff": falloff, "lookat": lookat, "trail": trail,
}


BUILDERS = {
    "paint": paint, "flatten": flatten, "inflate": inflate, "dial": dial,
    "stripes": stripes, "gradient": gradient, "jitter": jitter,
    "falloff": falloff, "lookat": lookat, "trail": trail,
}


@pytest.mark.parametrize("exercise", learn.BEGINNER,
                         ids=[e.key for e in learn.BEGINNER])
def test_following_the_instructions_with_nodes_solves_the_exercise(exercise):
    graph = new()
    BUILDERS[exercise.key](graph)
    verdict = exercise.review(graph)
    assert verdict == "", f"{exercise.key}: {verdict}"


@pytest.mark.parametrize("exercise", learn.BEGINNER,
                         ids=[e.key for e in learn.BEGINNER])
def test_the_node_built_graph_compiles(exercise):
    graph = new()
    BUILDERS[exercise.key](graph)
    emission = generate(graph)
    assert not [i for i in emission.issues if i.severity == ERROR]
    check = check_source(emission.code)
    if check.checked:
        assert check.ok, f"{exercise.key}: {check.raw}"


def test_every_exercise_names_nodes_the_search_actually_finds():
    """An instruction that says 'Tab, type X' is a promise: X must find it."""
    for exercise in learn.BEGINNER:
        assert exercise.nodes, exercise.key
        for term, node_type, purpose in exercise.nodes:
            found = [d.type for d in REG.search(term, limit=6)]
            assert node_type in found, (
                f"{exercise.key}: typing {term!r} does not find {node_type}")
            assert purpose, f"{exercise.key}: {term} has no explanation"
