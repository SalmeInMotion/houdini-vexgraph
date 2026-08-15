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
    signatures = index.call("xyzdist", 4)
    assert signatures, "the four-argument overload must be indexed"
    signature = signatures[0]           # tier order: the curated node first
    assert signature.node_type == "closest_surface_point"
    assert [s.kind for s in signature.slots] == ["param", "in", "out", "out"]


def test_index_prefers_the_curated_node_over_the_generated_one(registry):
    index = FunctionIndex(registry)
    assert index.call("fit", 5)[0].node_type == "fit_range"
    assert index.call("nearpoints", 4)[0].node_type == "nearest_points"


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


def test_a_loop_that_counts_to_and_including_n_still_maps(registry):
    """`i <= n` is as common as `i < n` in hand-written VEX.

    Refusing it was expensive out of all proportion: the whole loop body then
    became one block of inline VEX. On a real snippet that was the difference
    between 5 nodes and 20.
    """
    for condition, expected in (("i < @numpt", "@numpt"),
                                ("i <= @numpt", "@numpt + 1"),
                                ("i != @numpt", "@numpt"),
                                ("i <= 3", "4")):        # folded, not "3 + 1"
        code, report = imported(
            f"for (int i = 0; {condition}; i++) {{\n    @P.y += i;\n}}", registry)
        assert report.inlined == 0, (condition, report.reasons)
        assert f"< {expected};" in code, (condition, code)


def test_several_variables_declared_on_one_line_become_several_nodes(registry):
    """One Raw statement here used to cost the whole snippet.

    The graph had no record of the names, so every later assignment to them
    was refused as undeclared too - one unreadable line turning into a cascade
    of inline blocks.
    """
    _, report = imported("vector t, tc, bt;", registry)
    assert report.total == 3 and report.inlined == 0, report.reasons

    # And the names are usable afterwards, which is the point.
    _, report = imported("int closed, curve;\ncurve = 1;", registry)
    assert report.inlined == 0, report.reasons


def test_splitting_declarations_does_not_split_call_arguments(registry):
    """The commas inside `fit(x, 0, 1, 0, 1)` belong to the call, not the line."""
    _, report = imported("float d = fit(@P.x, 0, 1, 0, 1);", registry)
    assert report.total == 1 and report.inlined == 0, report.reasons

    _, report = imported("vector a = {0,0,0}, b = {1,1,1};", registry)
    assert report.total == 2 and report.inlined == 0, report.reasons


def test_a_side_effect_call_inside_an_expression_still_runs(registry):
    """`pr = addprim(0, "polyline")` must put addprim in the run order.

    The node used to be created to feed the assignment and never wired to
    execute, so the emitted code referenced a result that was never computed -
    a graph that looked right and did not compile. The worst failure this
    tool can produce, because its whole promise is that what you see builds.
    """
    code, report = imported(
        'int pr;\npr = addprim(0, "polyline");\n'
        'int pt = addpoint(0, @P);\naddvertex(0, pr, pt);', registry)
    assert report.inlined == 0, report.reasons
    # The calls appear, and before their results are used.
    assert code.index("addprim(") < code.index("addvertex(")
    assert code.index("addpoint(") < code.index("addvertex(")


def test_declared_type_flows_back_through_operator_chains(registry):
    """`float d = point(a)/point(b)` types the reads float, as vcc did.

    The reads defaulted to vector, the division inherited it, and the declared
    type only retyped the last node - leaving vector wires into float sockets
    and an emission that failed. The declaration is evidence about the whole
    chain, so the retype now follows the wires down.
    """
    code, report = imported(
        'float decay = point(0, "life", @ptnum) / point(0, "age", @ptnum);\n'
        "@pscale = decay;", registry)
    assert report.inlined == 0, report.reasons
    assert "vector" not in code


def test_a_float_feeding_an_int_socket_gets_a_visible_truncate(registry):
    """VEX truncates on implicit float->int; the graph now shows that."""
    code, report = imported(
        'float budget = ch("count");\n'
        "int pts[] = nearpoints(0, @P, 1.0, budget);\n"
        "i@found = len(pts);", registry)
    assert report.inlined == 0, report.reasons
    assert "trunc" in code, "the coercion should be spelled out, not refused"


def test_a_wire_that_cannot_convert_falls_inline_not_broken(registry):
    """A statement the types cannot serve becomes Inline VEX and compiles.

    Refusing at emission time - after the graph was built - broke the whole
    round trip. Failing while lowering keeps the statement verbatim, which is
    the never-fatal promise the importer makes everywhere else.
    """
    report = import_vex('string s = "x";\n'
                        "int pts[] = nearpoints(0, @P, 1.0, s);", registry)
    assert report.inlined >= 1
    emission = generate(report.graph)
    errors = [str(i) for i in emission.issues if i.severity == ERROR]
    assert not errors, "\n".join(errors)


def test_opinput_bindings_become_input_numbers(registry):
    """`@OpInput1` is the wrangle's name for input 0, not an attribute."""
    code, report = imported(
        'int h = pcopen(@OpInput1, "P", @P, 1.0, 10);\ni@n = pcnumfound(h);',
        registry)
    assert report.inlined == 0, report.reasons
    assert "pcopen(0," in code.replace(" ", "")
    assert "OpInput" not in code


def test_do_while_is_kept_whole(registry):
    """`do { } while (c);` is one statement; splitting it broke both halves."""
    report = import_vex("do { @P.y += 1; } while (@P.y < 3);", registry)
    assert report.inlined == 1
    emission = generate(report.graph)
    assert "while (@P.y < 3);" in emission.code


def test_a_declaration_kept_inline_still_declares_its_name(registry):
    """One unsupported initialiser must not poison every later mention.

    An unknown function stands in for "anything the graph cannot express";
    a ternary used to be the example here, until it gained a node of its own.
    """
    code, report = imported(
        "float flag = future_function(@P);\n@Cd = set(flag, 0, 0);", registry)
    # The unknown call stays verbatim; the set() line still becomes nodes.
    assert report.inlined == 1, report.reasons
    assert "future_function(@P)" in code
    assert "set(" in code


def test_a_ternary_becomes_a_choose_node(registry):
    """`a ? b : c` is VOP's Two Way Switch; now it is ours as well."""
    code, report = imported(
        "int flag = @P.y > 0 ? 1 : 0;\n@Cd = set(flag, 0, 0);", registry)
    assert report.inlined == 0, report.reasons
    assert "?" in code and ":" in code


def test_a_float_condition_means_nonzero_not_truncation(registry):
    """`if (@pscale)` is true for 0.7; a trunc shim would make it false."""
    code, report = imported("if (@pscale) { @Cd.x = 1; }", registry)
    assert report.inlined == 0, report.reasons
    assert "trunc" not in code
    assert "!= 0" in code


def test_overloads_are_chosen_by_argument_types(registry):
    """quaternion() takes an angle-axis pair OR a matrix3, same arity."""
    code, report = imported(
        "matrix3 m = matrix3(1);\np@orient = quaternion(m);", registry)
    assert report.inlined == 0, report.reasons

    code2, report2 = imported("p@orient = quaternion({0, 1, 0});", registry)
    assert report2.inlined == 0, report2.reasons


def test_loops_starting_above_zero_still_map(registry):
    code, report = imported(
        "for (int i = 1; i < 5; i++) { @P.y += i; }", registry)
    assert report.inlined == 0, report.reasons
    assert "int i = 1" in code


def test_one_prefixed_mention_types_the_attribute_everywhere(registry):
    """`v@dir;` at the top means every later bare `@dir` is a vector."""
    code, report = imported(
        "v@dir;\n@dir = @N * 0.5;\n@P += @dir;", registry)
    # The bare binding statement stays verbatim; everything else is nodes.
    assert report.inlined == 1, report.reasons
    assert "v@dir" in code


def test_a_c_cast_changes_the_graph_not_just_the_label(registry):
    """`(int)rint(x)` used to claim int on a float wire and break later."""
    code, report = imported(
        "if ((int)rint(@P.y) == 0) { @Cd = {1, 0, 0}; }", registry)
    assert report.inlined == 0, report.reasons
    assert "trunc" in code, "the cast becomes a visible conversion"


def test_pcclose_takes_its_handle_instead_of_inventing_one(registry):
    """The generator misread pcclose(int handle) as writing through it.

    That rebound the caller's variable to a fake output initialised to zero,
    orphaning the pcopen it came from - `pcclose(0)` on a handle nobody
    opened, and an undefined variable everywhere the real one was mentioned.
    """
    code, report = imported(
        'int h = pcopen(0, "P", @P, 1.0, 10);\n'
        "while (pciterate(h)) { @Cd.x = 1; }\n"
        "pcclose(h);", registry)
    assert report.inlined == 1          # the while, and only the while
    assert "pcclose(0)" not in code.replace(" ", "")
    assert "int h = " in code, "the alias is materialised for the inline text"


def test_a_loop_keeps_the_variable_name_the_body_text_uses(registry):
    """Inline VEX inside the body still says `i`; the loop must too."""
    code, report = imported(
        "for (int i = 0; i < 3; i++) {\n"
        "    while (i > 99) { @P.x = 0; }\n"
        "}", registry)
    assert report.inlined == 1          # the while
    assert "for (int i = 0" in code


def test_polymorphic_results_never_type_as_any(registry):
    """`point()/point() * 0.1` must come out float end to end.

    The unresolved "any" on a polymorphic node's output poisoned width
    arithmetic (the divide typed int) and slid through the wiring gate,
    because "any" connects to everything. Resolving it at placement is what
    lets the declared type flow back through the whole operator chain.
    """
    code, report = imported(
        'float decay = point(0, "life", @ptnum) / point(0, "age", @ptnum) '
        "* 0.1;\n@pscale = decay;", registry)
    assert report.inlined == 0, report.reasons
    assert "vector" not in code
    assert code.count("float ") == 2, "both reads declare as float"


def test_state_written_inside_a_branch_lives_outside_it(registry):
    """VOP's rule: the variable lives outside; the branch only assigns it.

    An out-argument used to rebind the variable to a wire from inside the
    branch, and reading it afterwards was refused - "comes from inside the
    If, used outside it" - on a graph that was already built. Writing through
    a real Set Variable keeps the state where the original declared it.
    """
    code, report = imported(
        "float vals[];\n"
        "if (@P.y > 0) { append(vals, @P.y); }\n"
        "f@n = len(vals);", registry)
    assert report.inlined == 0, report.reasons
    # The assignment stays inside the branch; the read stays outside.
    assert code.index("{") < code.index("vals = ") < code.index("}")


def test_matrix3_attributes_survive_the_round_trip(registry):
    code, report = imported("3@inertia = matrix3(1);", registry)
    assert report.inlined == 0, report.reasons
    assert "3@inertia" in code


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
    """`@P.x = v` is one node and one line, exactly as a wrangle spells it.

    It used to read the vector, split it, rebuild it and write it back -
    five nodes and four lines for the single most common statement in
    hand-written VEX, and the biggest reason one-liners became big graphs.
    """
    code, report = imported("@P.x = 1.5;", registry)
    assert report.inlined == 0
    assert "@P.x = 1.5;" in code
    assert compile_check(generate(report.graph)).ok


def test_component_reads_emit_inline_not_as_declarations(registry):
    """`@P.y` in an expression stays `@P.y`, not three float declarations."""
    code, report = imported("@P.y += 1;", registry)
    assert report.inlined == 0
    assert "@P.y = @P.y + 1;" in code
    assert "float " not in code, "no unused component declarations"

    # A compound source needs its brackets to keep meaning the same thing.
    code, _ = imported("f@m = (@P + @N).x;", registry)
    assert "(@P + @N).x" in code


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
