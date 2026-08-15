"""The write-VEX route, tested without spending a token.

The catalogue route exists so a model cannot invent nodes; this route exists
because local models are far better at writing plain VEX (their training data)
than at choosing from a 1300-entry invented catalogue under a giant schema.
The guarantee shifts shape but stays: whatever the model writes must compile
(vcc), and the importer lowers it per-statement with Inline VEX as the
never-fatal fallback. These tests script the exact replies models produce —
fences, prose, broken code that gets repaired — and check the loop behaves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import default_registry  # noqa: E402
from vexgraph.assistant.agent import Assistant, strip_vex  # noqa: E402
from vexgraph.codegen import generate  # noqa: E402
from vexgraph.parser import import_vex  # noqa: E402
from vexgraph.vccmap import check_source  # noqa: E402

from test_assistant import ScriptedProvider  # noqa: E402


@pytest.fixture(scope="session")
def registry():
    return default_registry()


vcc_available = check_source("@P += 1;").checked
needs_vcc = pytest.mark.skipif(
    not vcc_available, reason="VEXpress/vcc not available on this machine")


# ------------------------------------------------------------------ strip_vex

def test_strip_vex_takes_fenced_code_and_run_over():
    code, run_over = strip_vex(
        "Here you go:\n```vex\n// run over: primitives\n@Cd = {1,0,0};\n```")
    assert code == "@Cd = {1,0,0};"
    assert run_over == "primitives"


def test_strip_vex_defaults_to_points_and_drops_thinking():
    code, run_over = strip_vex("<think>hmm</think>@Cd = {0,1,0};")
    assert code == "@Cd = {0,1,0};"
    assert run_over == "points"


def test_strip_vex_accepts_run_over_without_fence():
    code, run_over = strip_vex("// run over: detail\ni@count = npoints(0);")
    assert code == "i@count = npoints(0);"
    assert run_over == "detail"


# ----------------------------------------------------------------- happy path

@needs_vcc
def test_valid_vex_becomes_a_graph(registry):
    provider = ScriptedProvider([
        "// run over: points\nv@Cd = set(1, 0, 0);\n"])
    result = Assistant(registry, provider).build_graph_via_vex("make it red")

    assert result.ok, result.problems
    assert result.graph is not None and len(result.graph.nodes) > 1
    assert result.graph.run_over == "points"
    assert result.code.strip()
    assert result.tries == 1
    # The notes tell the user how much of the code became real nodes.
    assert "statement" in result.notes


@needs_vcc
def test_unmappable_but_valid_vex_survives_as_inline(registry):
    # A do-while maps to no node; the importer's promise is that it becomes
    # an Inline VEX node rather than a failure.
    provider = ScriptedProvider([
        "// run over: points\n"
        "int i = 0;\ndo {\n    i++;\n} while (i < 3);\nf@done = i;\n"])
    result = Assistant(registry, provider).build_graph_via_vex("count to three")

    assert result.ok, result.problems
    types = {n.type for n in result.graph.nodes.values()}
    assert "inline_vex" in types


# ---------------------------------------------------------------- repair loop

@needs_vcc
def test_broken_vex_is_repaired_with_compiler_errors(registry):
    provider = ScriptedProvider([
        # First try: a call that does not exist, so vcc rejects it.
        "// run over: points\nv@Cd = definitely_not_a_function(@P);\n",
        # Second try: fixed.
        "// run over: points\nv@Cd = normalize(v@P);\n",
    ])
    result = Assistant(registry, provider).build_graph_via_vex("colour by P")

    assert result.ok, result.problems
    assert result.tries == 2
    assert result.attempts[0].problems, "first attempt should carry vcc errors"
    # The repair message hands the model the compiler's words, not ours.
    repair = provider.seen[1][-1]["content"]
    assert "Compiler errors" in repair


@needs_vcc
def test_persistent_failure_reports_problems(registry):
    provider = ScriptedProvider(
        ["v@Cd = nope_%d(@P);" % i for i in range(3)])
    result = Assistant(registry, provider).build_graph_via_vex("nonsense")

    assert not result.ok
    assert result.problems
    assert result.tries == 3


def test_empty_reply_is_a_problem_not_a_crash(registry):
    provider = ScriptedProvider(["", "", ""])
    result = Assistant(registry, provider).build_graph_via_vex("anything")

    assert not result.ok
    assert any("no code" in p for p in result.problems)


# ------------------------------------------------------------- the round trip
#
# Found by running a local model at this route: its VEX compiled, but the code
# re-emitted from the imported graph did not. A variable the importer had
# dissolved into nodes was still named by a statement that fell back to Inline
# VEX, so the emitted snippet referenced something that no longer existed.

def test_variable_named_by_inline_code_is_declared_again(registry):
    # `@Cd.r = ...` has no node, so it stays inline - and it names `push`,
    # which the declaration above it turned into a plain multiply node.
    source = ("vector push = v@N * 0.2;\n"
              "v@P += push;\n"
              "@Cd.r = length(push);")
    report = import_vex(source, registry)
    code = generate(report.graph).code

    assert "inline_vex" in {n.type for n in report.graph.nodes.values()}
    assert check_source(code).ok, code


def test_inline_code_follows_a_renamed_variable(registry):
    # `push` is a VEX function, so the declaration cannot be called `push`
    # without shadowing it; the inline statement must follow that rename.
    source = ("vector push = v@N * 0.2;\n"
              "v@P += push;\n"
              "@Cd.r = length(push);")
    code = generate(import_vex(source, registry).graph).code

    assert "push_value" in code, code
    assert "length(push)" not in code, code


def test_round_trip_holds_for_a_variable_with_a_safe_name(registry):
    source = ("vector offset = v@N * 0.2;\n"
              "v@P += offset;\n"
              "@Cd.r = length(offset);")
    code = generate(import_vex(source, registry).graph).code

    assert "offset" in code
    assert check_source(code).ok, code


@needs_vcc
def test_route_survives_code_that_only_partly_maps(registry):
    provider = ScriptedProvider([
        "// run over: points\n"
        "vector push = v@N * 0.2;\nv@P += push;\n@Cd.r = length(push);\n"])
    result = Assistant(registry, provider).build_graph_via_vex("push and tint")

    assert result.ok, result.problems
    assert result.tries == 1, "the model should not be blamed for our round trip"
    assert check_source(result.code).ok


# ------------------------------------------------------------- modify current

@needs_vcc
def test_modifying_shows_the_current_code(registry):
    provider = ScriptedProvider([
        "// run over: points\nv@Cd = set(0, 0, 1);\n"])
    assistant = Assistant(registry, provider)

    first = assistant.build_graph_via_vex("make it blue")
    assert first.ok

    provider.replies = ["// run over: points\nv@Cd = set(0, 1, 0);\n"]
    second = assistant.build_graph_via_vex("now green", first.graph)
    assert second.ok
    prompt = provider.seen[-1][0]["content"]
    assert "The code as it stands" in prompt
