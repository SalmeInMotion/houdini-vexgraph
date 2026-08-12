"""Reading VEX back into a graph.

The strongest check available is a fixed point: take a graph, emit its VEX,
import that VEX, emit again, and require the two texts to match byte for byte.
If anything in the chain loses information it shows up immediately, and the
examples are real graphs rather than fixtures written to pass.

The second check is that hand-written VEX — not our own output — imports and
still compiles, and that anything unsupported becomes an Inline VEX node rather
than an error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import Graph, default_registry, generate  # noqa: E402
from vexgraph.graph import ERROR  # noqa: E402
from vexgraph.parser import import_vex, parse  # noqa: E402
from vexgraph.parser.lexer import Kind, tokenize  # noqa: E402
from vexgraph.parser.lower import FunctionIndex  # noqa: E402
from vexgraph.vccmap import compile_check  # noqa: E402

EXAMPLES = ["stick_to_surface", "colour_by_proximity", "average_neighbour_colour"]


@pytest.fixture(scope="session")
def registry():
    return default_registry()


def body_of(code: str) -> str:
    return "\n".join(l for l in code.splitlines()
                     if not l.startswith("//")).strip()


def imported(source: str, registry) -> tuple[str, object]:
    report = import_vex(source, registry)
    emission = generate(report.graph)
    errors = [str(i) for i in emission.issues if i.severity == ERROR]
    assert not errors, "\n".join(errors)
    return body_of(emission.code), report


# ------------------------------------------------------------------- lexing

def test_attributes_carry_their_type_prefix():
    tokens = tokenize("v@up = @P; f@w = 1;")
    attribs = [t for t in tokens if t.kind is Kind.ATTRIB]
    assert [(t.text, t.prefix) for t in attribs] == [
        ("up", "v"), ("P", ""), ("w", "f")]


def test_comments_do_not_shift_line_numbers():
    tokens = tokenize("// one\n/* two\nthree */\n@P = 1;")
    assert next(t for t in tokens if t.kind is Kind.ATTRIB).line == 4


def test_strings_survive_escaped_quotes():
    tokens = tokenize(r'printf("a\"b");')
    assert next(t for t in tokens if t.kind is Kind.STRING).text == r'"a\"b"'


# ------------------------------------------------------------------ parsing

def test_braces_are_blocks_or_vectors_depending_on_position():
    statements = parse("vector v = {1, 2, 3};\nif (1) { @P = v; }")
    assert type(statements[0]).__name__ == "Declare"
    assert type(statements[1]).__name__ == "If"


def test_unmodellable_syntax_still_parses_as_raw():
    statements = parse("struct thing { int a; };\n@P = 1;")
    assert type(statements[0]).__name__ == "Raw"
    assert type(statements[1]).__name__ == "Assign"


def test_precedence_is_c_precedence():
    from vexgraph.parser.syntax import Binary  # noqa: PLC0415

    statement = parse("@x = 1 + 2 * 3;")[0]
    assert isinstance(statement.value, Binary) and statement.value.op == "+"
    assert statement.value.right.op == "*"


# ------------------------------------------------------------- the index

def test_index_uses_the_call_arity_not_the_socket_count(registry):
    """`xyzdist(a,b,c,d)` has four arguments but the node has one input."""
    index = FunctionIndex(registry)
    signature = index.call("xyzdist", 4)
    assert signature is not None
    assert signature.node_type == "closest_surface_point"
    assert [s.kind for s in signature.slots] == ["param", "in", "out", "out"]


def test_index_prefers_the_curated_node_over_the_generated_one(registry):
    index = FunctionIndex(registry)
    assert index.call("fit", 5).node_type == "fit_range"
    assert index.call("nearpoints", 4).node_type == "nearest_points"


def test_index_knows_the_operators(registry):
    index = FunctionIndex(registry)
    assert index.operator("+", 2) == "add"
    assert index.operator("!", 1) == "not_true"


# --------------------------------------------------------------- round trip

@pytest.mark.parametrize("name", EXAMPLES)
def test_example_vex_round_trips_byte_for_byte(name, registry):
    """graph -> VEX -> graph -> VEX must be a fixed point."""
    original = body_of(generate(
        Graph.load(Path(__file__).resolve().parents[1] / "examples"
                   / f"{name}.vexgraph.json", registry)).code)
    again, report = imported(original, registry)
    assert report.inlined == 0, report.reasons
    assert again == original


@pytest.mark.parametrize("name", EXAMPLES)
def test_imported_examples_still_compile(name, registry):
    original = body_of(generate(
        Graph.load(Path(__file__).resolve().parents[1] / "examples"
                   / f"{name}.vexgraph.json", registry)).code)
    report = import_vex(original, registry)
    result = compile_check(generate(report.graph), report.graph)
    assert result.ok, result.raw


# ------------------------------------------------------ hand-written VEX

HAND_WRITTEN = [
    ("attribute maths", "@P += @N * 0.1;"),
    ("compound on a custom attribute", "f@heat = f@heat * 0.9;"),
    ("comparison and branch",
     "if (@P.y > 1.0) {\n    @Cd = {1, 0, 0};\n}"),
    ("else branch",
     "if (@ptnum % 2 == 0) {\n    @Cd = {1, 0, 0};\n} else {\n    @Cd = {0, 0, 1};\n}"),
    ("counted loop",
     "for (int i = 0; i < 5; i++) {\n    @P += @N;\n}"),
    ("variable accumulated in a loop",
     "float total = 0;\nfor (int i = 0; i < 4; i++) {\n    total += 1;\n}\n@mass = total;"),
    ("nearest point lookup",
     "int near = nearpoint(1, @P);\nvector there = point(1, \"P\", near);\n@P = there;"),
    ("vector components", "@Cd = set(@P.x, @P.y, @P.z);"),
    ("negation and logic",
     "if (!(@ptnum > 3) && @ptnum < 10) {\n    removepoint(0, @ptnum);\n}"),
    ("array indexing",
     "int pts[] = nearpoints(0, @P, 1.0, 4);\n@Cd = point(0, \"Cd\", pts[0]);"),
]


@pytest.mark.parametrize("label,source",
                         HAND_WRITTEN, ids=[c[0] for c in HAND_WRITTEN])
def test_hand_written_vex_imports_and_compiles(label, source, registry):
    _, report = imported(source, registry)
    result = compile_check(generate(report.graph), report.graph)
    assert result.ok, f"{label}\n{result.raw}"


def test_hand_written_vex_mostly_becomes_real_nodes(registry):
    """Not a fixed point — but it must not all fall into the escape hatch."""
    joined = "\n".join(source for _, source in HAND_WRITTEN)
    _, report = imported(joined, registry)
    assert report.inlined <= 1, report.reasons


# ------------------------------------------------------------ escape hatch

def test_a_while_loop_becomes_one_inline_node(registry):
    code, report = imported("int i = 0;\nwhile (i < 3) {\n    i += 1;\n}", registry)
    assert report.inlined == 1
    assert "while (i < 3)" in code


def test_an_unknown_function_becomes_inline_rather_than_an_error(registry):
    code, report = imported('@P = 1;\nsome_future_function(@P, 2);', registry)
    assert report.inlined == 1
    assert "some_future_function(@P, 2);" in code
    assert "@P = 1;" in code            # the rest still became nodes


def test_a_struct_is_kept_verbatim(registry):
    code, _ = imported("struct thing { int a; };\n@P = 1;", registry)
    assert "struct thing { int a; }" in code


def test_source_that_will_not_parse_still_opens(registry):
    """A broken snippet has to land somewhere editable, not throw."""
    report = import_vex("@P = ;;; ) unclosed", default_registry())
    assert report.inlined >= 1
    assert report.reasons
    # Every inline node is reachable, so nothing is silently dropped on the way.
    assert len(report.graph.nodes) == report.inlined + 1     # + start


def test_one_bad_line_does_not_cost_the_good_ones(registry):
    """Recovery is per statement: a stray token must not inline the whole file."""
    report = import_vex("@P = @P * 2;\n@Cd = ) broken (;\n@N = @N * 3;", registry)
    assert report.inlined == 1
    assert report.translated == report.total - 1
    code = generate(report.graph).code
    assert "@P = @P * 2;" in code and "@N = @N * 3;" in code


def test_hscript_variables_survive_the_round_trip(registry):
    """`$PI` is expanded by Houdini, not VEX - passing it through is correct."""
    code, report = imported("float a = $PI * 2;\n@pscale = a;", registry)
    assert report.inlined == 0
    assert "$PI" in code


def test_the_report_says_what_it_could_not_translate(registry):
    report = import_vex("while (1) { @P += 1; }", registry)
    assert "Inline VEX" in report.summary()
    assert any("while" in reason for reason in report.reasons)


def test_single_quoted_strings_are_read(registry):
    """`ch('parm')` is valid VEX and the commonest spelling in real snippets.

    Rejecting it was the single largest reason working code fell through to
    Inline VEX - 88 of the installed snippets.
    """
    code, report = imported("@pscale = ch('size');", registry)
    assert report.inlined == 0
    assert '"size"' in code, "quotes should be normalised on the way in"
    assert "'" not in code, "a single quote should not survive to the output"


def test_a_component_can_be_assigned(registry):
    """`@P.x = v` reads the vector, rebuilds it and writes it back."""
    code, report = imported("@P.x = 1.5;", registry)
    assert report.inlined == 0
    assert "@P = set(1.5," in code
    assert compile_check(generate(report.graph)).ok


def test_sop_globals_are_not_undeclared_variables(registry):
    """`Time` is provided by the SOP context; treating it as a variable cost
    every statement that used it, and everything downstream of those."""
    code, report = imported("float t = Time * 0.5;\n@pscale = t;", registry)
    assert report.inlined == 0
    assert "@Time" in code, "SideFX recommend the @ spelling"


def test_an_array_attribute_is_read(registry):
    """`i[]@hits` binds a list; the `[]` sits between the prefix and the `@`."""
    tokens = tokenize("i[]@hits")
    assert tokens[0].kind is Kind.ATTRIB
    assert tokens[0].text == "hits"
    assert tokens[0].prefix == "i"
    assert tokens[0].is_array

    plain = tokenize("v@up")[0]
    assert plain.prefix == "v" and not plain.is_array
