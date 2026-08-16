"""The Beginner course: ten exercises that teach how wrangles think.

Design decisions, because they shape everything here:

- The course itself needs NO model and no connection. Each exercise is
  checked deterministically against the live graph and its emitted VEX, and
  the first failing check IS the teaching - a plain sentence about what is
  missing, in order. A tutor that sometimes hallucinates on step 3 of a
  beginner course would poison the whole idea; the assistant stays available
  as the *optional* professor on top, with the exercise as context.
- Hints are staged: first the concept, then the exact node, then the line of
  VEX itself. Asking for all three is fine - that is what they are for.
- Every exercise ends with where to go deeper: SideFX's own pages and this
  tool's manual, so the course is a hallway of doors rather than a corridor.

The ten follow one arc: attribute -> component -> expression -> channel ->
condition -> variable and ranges -> randomness -> measuring -> direction ->
loop. Each teaches exactly one new idea and reuses the previous ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .codegen import generate
from .graph import Graph

SIDEFX = "https://www.sidefx.com/docs/houdini"


@dataclass(frozen=True)
class Check:
    """One thing the graph must satisfy, and the sentence that teaches it."""
    teach: str                              # shown when this check fails
    passes: Callable[[Graph, str], bool]    # (graph, emitted code) -> ok


@dataclass(frozen=True)
class Exercise:
    key: str
    title: str
    goal: str            # what the viewport should end up doing
    steps: str           # the guided path, markdown-ish plain text
    hints: tuple[str, ...]
    checks: tuple[Check, ...]
    deeper: tuple[tuple[str, str], ...] = ()      # (label, url)
    solution: str = ""   # reference VEX; also proves the checks are passable

    def review(self, graph: Graph) -> str:
        """Empty string when solved; otherwise the first lesson still due."""
        code = generate(graph).code
        for check in self.checks:
            if not check.passes(graph, code):
                return check.teach
        return ""


def _has_node(graph: Graph, *types: str) -> bool:
    return any(n.type in types for n in graph.nodes.values())


def _code_has(code: str, *needles: str) -> bool:
    squashed = "".join(code.split())
    return all("".join(n.split()) in squashed for n in needles)


BEGINNER: tuple[Exercise, ...] = (
    Exercise(
        key="paint",
        title="Paint everything red",
        goal="Every point turns pure red.",
        steps=(
            "A wrangle runs once per point, so you only ever describe ONE "
            "point's change - Houdini repeats it for all of them.\n\n"
            "1. Press Tab, type 'set attribute', add the node.\n"
            "2. Wire Start's white arrow into it: the arrow chain is the "
            "order things happen.\n"
            "3. Set Attribute to Cd (that is colour), Type to vector, and "
            "the value to {1, 0, 0} - red, as (red, green, blue)."),
        hints=(
            "Colour lives in an attribute called Cd; changing a point means "
            "writing one of its attributes.",
            "The node is Set Attribute; it needs the exec wire from Start "
            "or it never runs.",
            "The whole program is one line: @Cd = {1, 0, 0};"),
        checks=(
            Check("Nothing runs yet: add a Set Attribute node and wire "
                  "Start's white arrow into it.",
                  lambda g, c: _has_node(g, "attrib_set")),
            Check("The attribute to write is Cd - the colour. Set the "
                  "node's Attribute field to Cd, with Type vector.",
                  lambda g, c: "@Cd" in c),
            Check("Red as (red, green, blue) is {1, 0, 0}. Put that in the "
                  "value.",
                  lambda g, c: _code_has(c, "@Cd = {1, 0, 0};")),
        ),
        deeper=(
            ("Attributes, in this tool's manual", "manual: Types and colours"),
            ("SideFX: wrangle basics", f"{SIDEFX}/vex/snippets.html"),
        ),
        solution="@Cd = {1, 0, 0};",
    ),
    Exercise(
        key="flatten",
        title="Flatten the ground",
        goal="Every point drops to height zero; the model becomes a pancake.",
        steps=(
            "Position is the attribute P, a vector of (x, y, z). You only "
            "want to change its height - the y part.\n\n"
            "1. Add a Set Component node (Tab: 'set component').\n"
            "2. Wire Start's arrow into it.\n"
            "3. Attribute P, Component y, value 0."),
        hints=(
            "P is position; a vector has .x .y .z parts and y is up.",
            "Set Component writes ONE part of a vector attribute and "
            "leaves the rest alone.",
            "The line is: @P.y = 0;"),
        checks=(
            Check("Add a Set Component node and wire the white arrow into "
                  "it - it writes one part of a vector.",
                  lambda g, c: _has_node(g, "attrib_set_component")),
            Check("The attribute is P (position) and the component is y - "
                  "the height.",
                  lambda g, c: "@P.y" in c),
            Check("Height zero means the value is 0.",
                  lambda g, c: _code_has(c, "@P.y = 0;")),
        ),
        deeper=(
            ("Component pins, in the manual", "manual: Vectors and component pins"),
            ("SideFX: VEX language basics", f"{SIDEFX}/vex/lang.html"),
        ),
        solution="@P.y = 0;",
    ),
    Exercise(
        key="inflate",
        title="Inflate the model",
        goal="Every point pushes outward along its normal, like blowing up "
             "a balloon.",
        steps=(
            "The normal N is the direction a point faces. Move each point a "
            "little way along its own N and the surface inflates.\n\n"
            "1. Add Get Attribute (N, vector) and Get Attribute (P, vector).\n"
            "2. Add a Multiply: N times 0.1 - the push, scaled down.\n"
            "3. Add an Add: P plus that push.\n"
            "4. Set Attribute P to the result, wired into the run order."),
        hints=(
            "Reading is Get Attribute; maths nodes combine values; writing "
            "is Set Attribute. Read, combine, write is the whole pattern.",
            "Multiply N by 0.1 first, then Add that onto P, then write P.",
            "The line is: @P += @N * 0.1;  (written out: @P = @P + @N * 0.1)"),
        checks=(
            Check("Read the normal: add Get Attribute with Attribute N, "
                  "Type vector.",
                  lambda g, c: "@N" in c),
            Check("Scale the push down: Multiply the normal by 0.1.",
                  lambda g, c: _has_node(g, "multiply") and "0.1" in c),
            Check("Add the push onto the position and write it back to P "
                  "with Set Attribute.",
                  lambda g, c: _code_has(c, "@P = @P + @N * 0.1;")
                  or _code_has(c, "@P = @N * 0.1 + @P;")),
        ),
        deeper=(
            ("SideFX: common attributes (P, N, Cd...)",
             f"{SIDEFX}/model/attributes.html"),
        ),
        solution="@P = @P + @N * 0.1;",
    ),
    Exercise(
        key="dial",
        title="Give it a dial",
        goal="The same inflate, but the amount is a slider on the wrangle "
             "you can drag while watching the viewport.",
        steps=(
            "Numbers you will want to tweak should not be buried in the "
            "graph - a channel turns one into a spinner on the node.\n\n"
            "1. Take your inflate graph (or rebuild it).\n"
            "2. In the Multiply node's b value, type: chf(\"amount\")\n"
            "3. Look at the wrangle in Houdini: it grew a parameter. Click "
            "the little slider icon by the snippet if it has not - then "
            "drag it."),
        hints=(
            "chf means CHannel Float: a float that lives as a knob on the "
            "node instead of a constant in the code.",
            "Channels are typed straight into a value row - they are "
            "parameters, not nodes.",
            "The line is: @P += @N * chf(\"amount\");"),
        checks=(
            Check("Start from the inflate: read N, Multiply, write P.",
                  lambda g, c: "@N" in c and _has_node(g, "multiply")),
            Check("Replace the 0.1 with chf(\"amount\") - typed right into "
                  "the Multiply's value row.",
                  lambda g, c: 'chf("amount")' in c),
        ),
        deeper=(
            ("Channels, in the manual", "manual: Channels"),
            ("SideFX: ch* functions", f"{SIDEFX}/vex/functions/ch.html"),
        ),
        solution='@P = @P + @N * chf("amount");',
    ),
    Exercise(
        key="stripes",
        title="Black and white stripes",
        goal="Even-numbered points turn white, odd ones black.",
        steps=(
            "Every point knows its own number: ptnum. Whether a number is "
            "even is a question - and questions need an If.\n\n"
            "1. Add Get Attribute for ptnum (int).\n"
            "2. Add a Modulo node: ptnum % 2 is 0 for even, 1 for odd.\n"
            "3. Add Is Equal: result == 0.\n"
            "4. Add an If / Else, condition from that comparison.\n"
            "5. In the If's 'then' arrow: Set Attribute Cd {1,1,1}. In the "
            "'otherwise': Cd {0,0,0}."),
        hints=(
            "ptnum % 2 == 0 is the classic even test.",
            "The If / Else node has TWO body arrows; each branch gets its "
            "own Set Attribute.",
            "if (@ptnum % 2 == 0) { @Cd = {1,1,1}; } else { @Cd = {0,0,0}; }"),
        checks=(
            Check("Read the point number: Get Attribute ptnum, Type int.",
                  lambda g, c: "@ptnum" in c),
            Check("Even or odd is ptnum % 2 - add a Modulo by 2.",
                  lambda g, c: "% 2" in c),
            Check("Add an If / Else whose condition is that comparison.",
                  lambda g, c: _has_node(g, "if_else")),
            Check("White {1,1,1} in one branch, black {0,0,0} in the other.",
                  lambda g, c: _code_has(c, "{1, 1, 1}")
                  and _code_has(c, "{0, 0, 0}")),
        ),
        deeper=(
            ("Loops and branches, in the manual", "manual: Loops"),
        ),
        solution=("if (@ptnum % 2 == 0) {\n    @Cd = {1, 1, 1};\n}"
                  " else {\n    @Cd = {0, 0, 0};\n}"),
    ),
    Exercise(
        key="gradient",
        title="A gradient by height",
        goal="Points fade from black at the bottom to white at the top.",
        steps=(
            "Height is @P.y, but it runs over whatever the model's range is. "
            "Fit Range remaps one range onto another - here, onto 0..1.\n\n"
            "1. Read P, take its y (double-click P's output for the pins).\n"
            "2. Add Fit Range: from -1..1 to 0..1 (adjust to your model).\n"
            "3. Add Make Vector with that value in x, y AND z - grey scale.\n"
            "4. Set Attribute Cd to it."),
        hints=(
            "fit(value, oldmin, oldmax, newmin, newmax) is the most useful "
            "function in VEX - remapping ranges is half of all effects work.",
            "The same number in all three of a colour's parts gives grey; "
            "0 is black, 1 is white.",
            "float t = fit(@P.y, -1, 1, 0, 1);  @Cd = set(t, t, t);"),
        checks=(
            Check("Read the height: P's y component.",
                  lambda g, c: "@P.y" in c),
            Check("Remap it with a Fit Range node onto 0..1.",
                  lambda g, c: "fit(" in c),
            Check("Build the grey with Make Vector - the same value in all "
                  "three parts - and write it to Cd.",
                  lambda g, c: _has_node(g, "make_vector") and "@Cd" in c),
        ),
        deeper=(
            ("SideFX: fit()", f"{SIDEFX}/vex/functions/fit.html"),
        ),
        solution="float t = fit(@P.y, -1, 1, 0, 1);\n@Cd = set(t, t, t);",
    ),
    Exercise(
        key="jitter",
        title="Scatter, but the same every frame",
        goal="Every point shifts a small random amount - and stays put when "
             "the frame changes, instead of buzzing.",
        steps=(
            "Computers fake randomness from a seed: the same seed always "
            "gives the same number. Seeding with each point's own number "
            "gives every point its own stable offset.\n\n"
            "1. Add Get Attribute ptnum.\n"
            "2. Add Random Number, seed from ptnum.\n"
            "3. That is 0..1; subtract 0.5 so it can push both ways.\n"
            "4. Multiply by 0.1, add onto P, write P."),
        hints=(
            "rand(seed) is repeatable on purpose - stable is what makes it "
            "usable in a render.",
            "rand gives 0..1; centring it (minus 0.5) lets points move both "
            "directions.",
            "@P += (vector(rand(@ptnum)) - 0.5) * 0.1;"),
        checks=(
            Check("Random needs a seed, and the point's own number is the "
                  "classic one: Random Number seeded with ptnum.",
                  lambda g, c: "rand(" in c and "@ptnum" in c),
            Check("Centre it: subtract 0.5 so the push can go both ways.",
                  lambda g, c: "0.5" in c),
            Check("Scale it down and add it onto P.",
                  lambda g, c: "@P" in c and _has_node(g, "attrib_set")),
        ),
        deeper=(
            ("SideFX: rand()", f"{SIDEFX}/vex/functions/rand.html"),
        ),
        solution="@P = @P + (vector(rand(@ptnum)) - 0.5) * 0.1;",
    ),
    Exercise(
        key="falloff",
        title="Shrink with distance",
        goal="Points near the centre stay big; far ones shrink away. "
             "(Give the points pscale - e.g. render as spheres.)",
        steps=(
            "length(P) is how far a point sits from the origin. Feed that "
            "distance through Fit Range, flipped, and it becomes a falloff.\n\n"
            "1. Read P; add a Length node.\n"
            "2. Fit Range: 0..2 onto 1..0 - NOTE the flip: near means big.\n"
            "3. Set Attribute pscale (float) to it."),
        hints=(
            "pscale is the attribute instancing and rendering read as "
            "per-point size.",
            "Fitting 0..2 onto 1..0 - backwards on purpose - is what turns "
            "a distance into a falloff.",
            "@pscale = fit(length(@P), 0, 2, 1, 0);"),
        checks=(
            Check("Measure the distance: Length of P.",
                  lambda g, c: "length(" in c),
            Check("Remap it with Fit Range - flipped, so near is 1 and far "
                  "is 0.",
                  lambda g, c: "fit(" in c),
            Check("Write it to pscale, a float attribute.",
                  lambda g, c: "@pscale" in c),
        ),
        deeper=(
            ("SideFX: length()", f"{SIDEFX}/vex/functions/length.html"),
        ),
        solution="@pscale = fit(length(@P), 0, 2, 1, 0);",
    ),
    Exercise(
        key="lookat",
        title="Look at the centre",
        goal="Every point's normal turns to face the origin - copied "
             "geometry would all look inward.",
        steps=(
            "A direction between two places is a subtraction. From a point "
            "toward the origin is minus its own position - then normalize, "
            "because a direction should have length 1.\n\n"
            "1. Read P; add a Negate node.\n"
            "2. Add Normalize.\n"
            "3. Set Attribute N (vector) to it."),
        hints=(
            "Directions want length 1: normalize() keeps the direction and "
            "throws away the distance.",
            "Toward the origin from P is simply -P.",
            "@N = normalize(-@P);"),
        checks=(
            Check("The direction to the origin is minus the position: "
                  "Negate P.",
                  lambda g, c: "-@P" in c or "- @P" in c),
            Check("Directions should be length 1: pass it through "
                  "Normalize.",
                  lambda g, c: "normalize(" in c),
            Check("Write it to N.",
                  lambda g, c: "@N" in c),
        ),
        deeper=(
            ("SideFX: normalize()", f"{SIDEFX}/vex/functions/normalize.html"),
        ),
        solution="@N = normalize(-@P);",
    ),
    Exercise(
        key="trail",
        title="Grow a trail",
        goal="Each point sprouts a little trail of five new points along "
             "its normal.",
        steps=(
            "Until now every exercise changed points that existed. Wrangles "
            "can also MAKE geometry - and a loop repeats the making.\n\n"
            "1. Add a Repeat node (5 times), wired into the run order.\n"
            "2. Inside its body arrow: Add Point.\n"
            "3. The new point's position: P plus N times (0.1 times the "
            "loop Number) - so each one lands a step further out."),
        hints=(
            "The Repeat node's Number output counts 0,1,2,3,4 - and only "
            "exists inside the loop's body.",
            "Multiply the loop number by 0.1 for the step, multiply N by "
            "that, add P.",
            "for (int i = 0; i < 5; i++) {\n    addpoint(0, @P + @N * (0.1 * i));\n}"),
        checks=(
            Check("Add a Repeat node set to 5 times, wired into the white "
                  "chain.",
                  lambda g, c: _has_node(g, "for_range")),
            Check("Inside its body: an Add Point node.",
                  lambda g, c: _has_node(g, "add_point", "vex_addpoint")
                  and "addpoint(" in c),
            Check("Each new point steps further out: P + N * (0.1 * the "
                  "loop's Number).",
                  lambda g, c: "@N" in c and "0.1" in c),
        ),
        deeper=(
            ("SideFX: addpoint()", f"{SIDEFX}/vex/functions/addpoint.html"),
            ("Functions, in the manual", "manual: Functions"),
        ),
        solution=("for (int i = 0; i < 5; i++) {\n"
                  "    addpoint(0, @P + @N * (0.1 * i));\n}"),
    ),
)


@dataclass
class Progress:
    """What the student has done, kept plain so the panel can persist it."""
    completed: set[str] = field(default_factory=set)
    attempts: dict[str, int] = field(default_factory=dict)
    hints_used: dict[str, int] = field(default_factory=dict)

    def weakest(self) -> list[str]:
        """Exercise keys ranked by struggle - most attempts and hints first."""
        scored = [(self.attempts.get(k, 0) + 2 * self.hints_used.get(k, 0), k)
                  for k in self.completed]
        return [k for score, k in sorted(scored, reverse=True) if score > 2]
