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

import re
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
    # What to hunt for in the library: (what to type after Tab, the node it
    # finds, what it is for). A beginner facing 1360 nodes cannot be told
    # "add a node" - they have to be told the word to type.
    nodes: tuple[tuple[str, str, str], ...] = ()
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


_SET_CALL = re.compile(r"\bset\(([^()]*)\)")


def _normalise(text: str) -> str:
    """One spelling for things VEX considers identical.

    A student who builds a colour with a Make Vector node emits
    `@Cd = set(1, 0, 0);` where typing the value straight into the row emits
    `@Cd = {1, 0, 0};`. VEX cannot tell those apart and neither may a course:
    marking a correct graph wrong is the worst thing a teacher can do.
    """
    return "".join(_SET_CALL.sub(lambda m: "{" + m.group(1) + "}", text).split())


def _code_has(code: str, *needles: str) -> bool:
    normalised = _normalise(code)
    return all(_normalise(n) in normalised for n in needles)


BEGINNER: tuple[Exercise, ...] = (
    Exercise(
        key="paint",
        title="Paint everything red",
        goal="Every point turns pure red.",
        steps=(
            "A wrangle runs once per point. You describe what happens to ONE "
            "point and Houdini repeats it for all of them - so there is no "
            "loop to write, and no list of points to handle.\n\n"
            "1. Click on the empty canvas and press Tab. A search box opens: "
            "type set attribute and press Enter. The node appears where you "
            "clicked.\n"
            "2. Look at the Start node: it has a white arrow on its right "
            "edge. Drag from that arrow to the white arrow on the LEFT of "
            "your new node. That white chain is the order things happen - a "
            "node outside it never runs at all.\n"
            "3. On the new node, click the Attribute row and type Cd. That "
            "is Houdini's name for colour. Leave Type as vector, because a "
            "colour is three numbers: red, green and blue.\n"
            "4. Now the Value. Two ways, both correct:\n"
            "   - Type it straight into the Value row: {1, 0, 0} means full "
            "red, no green, no blue.\n"
            "   - Or build it from three separate numbers: press Tab, type "
            "make vector, and wire its Result into Value. Then set x to 1, "
            "y to 0, z to 0.\n"
            "   The generated code says {1, 0, 0} for the first and "
            "set(1, 0, 0) for the second. Those are the same thing to VEX - "
            "if you ever see the code change spelling but not meaning, that "
            "is what happened."),
        nodes=(
            ("set attribute", "attrib_set",
             "writes one attribute of the point being worked on"),
            ("make vector", "make_vector",
             "optional: builds a vector out of three separate numbers"),
        ),
        hints=(
            "Colour is an attribute called Cd. Changing how a point looks "
            "means writing one of its attributes.",
            "The node is Set Attribute, and it needs the white arrow from "
            "Start or it never runs.",
            "The whole program is one line: @Cd = {1, 0, 0};"),
        checks=(
            Check("Nothing runs yet. Press Tab, type 'set attribute', add "
                  "it, and drag Start's white arrow into its white arrow.",
                  lambda g, c: _has_node(g, "attrib_set")),
            Check("Set the node's Attribute row to Cd - Houdini's name for "
                  "colour - with Type vector.",
                  lambda g, c: _code_has(c, "@Cd")),
            Check("Red is {1, 0, 0} as (red, green, blue). Type that into "
                  "the Value row, or wire a Make Vector set to 1, 0, 0.",
                  lambda g, c: _code_has(c, "@Cd = {1, 0, 0};")),
        ),
        deeper=(
            ("Types and colours, in the manual", "manual: Types and colours"),
            ("SideFX: writing VEX in a wrangle", f"{SIDEFX}/vex/snippets.html"),
        ),
        solution="@Cd = {1, 0, 0};",
    ),
    Exercise(
        key="flatten",
        title="Flatten the ground",
        goal="Every point drops to height zero; the model becomes a pancake.",
        steps=(
            "Position is an attribute too, called P. It is a vector: three "
            "numbers, (x, y, z), where y is up. You only want to touch the "
            "height and leave x and z alone.\n\n"
            "1. Delete what you built before (click a node, press Delete) or "
            "just start on empty canvas.\n"
            "2. Tab, type set component, Enter. This node writes ONE part of "
            "a vector attribute - exactly what you need.\n"
            "3. Wire Start's white arrow into it.\n"
            "4. Set Attribute to P, Component to y, and Value to 0."),
        nodes=(
            ("set component", "attrib_set_component",
             "writes one part (x, y or z) of a vector attribute"),
        ),
        hints=(
            "P is position, and in Houdini y is the up axis.",
            "Set Component changes one part and leaves the other two as they "
            "were - unlike Set Attribute, which replaces all three.",
            "The line is: @P.y = 0;"),
        checks=(
            Check("Press Tab, type 'set component', add it, and wire "
                  "Start's white arrow into it.",
                  lambda g, c: _has_node(g, "attrib_set_component")),
            Check("The attribute is P (position) and the component is y - "
                  "the height.",
                  lambda g, c: _code_has(c, "@P.y")),
            Check("Flat means height zero: put 0 in the Value row.",
                  lambda g, c: _code_has(c, "@P.y = 0;")),
        ),
        deeper=(
            ("Component pins, in the manual",
             "manual: Vectors and component pins"),
            ("SideFX: the P attribute", f"{SIDEFX}/model/attributes.html"),
        ),
        solution="@P.y = 0;",
    ),
    Exercise(
        key="inflate",
        title="Inflate the model",
        goal="Every point pushes outward along its normal, like blowing up "
             "a balloon.",
        steps=(
            "Here is the pattern behind almost every wrangle: READ some "
            "attributes, COMBINE them with maths, WRITE the result back.\n\n"
            "N is the normal - the direction a point faces. Move each point "
            "a little way along its own N and the whole surface inflates.\n\n"
            "1. Tab, get attribute. Set its Attribute to N and Type to "
            "vector. (Reading needs no white arrow: values are computed "
            "wherever they are used.)\n"
            "2. Tab, multiply. Set its Type to vector, wire N into a, and "
            "type 0.1 into b. That is the push, scaled down so it is not "
            "enormous.\n"
            "3. Tab, get attribute again - this one reads P, Type vector.\n"
            "4. Tab, add. Type vector. Wire P into a and the multiply's "
            "Result into b.\n"
            "5. Tab, set attribute: Attribute P, Type vector, the add's "
            "Result wired into Value. Wire Start's white arrow into it.\n\n"
            "Watch the code pane as you go: it writes itself, line by line."),
        nodes=(
            ("get attribute", "attrib_get", "reads an attribute of this point"),
            ("multiply", "multiply", "multiplies two values"),
            ("add", "add", "adds two values"),
            ("set attribute", "attrib_set", "writes an attribute"),
        ),
        hints=(
            "Read, combine, write. Get Attribute reads, the maths nodes "
            "combine, Set Attribute writes.",
            "Scale the normal down first (multiply by 0.1), then add that "
            "onto the position, then write P.",
            "The line is: @P = @P + @N * 0.1;"),
        checks=(
            Check("Read the normal first: a Get Attribute node with "
                  "Attribute N and Type vector.",
                  lambda g, c: _code_has(c, "@N")),
            Check("Scale the push down: a Multiply node (Type vector) with "
                  "0.1 in the other side.",
                  lambda g, c: _has_node(g, "multiply") and _code_has(c, "0.1")),
            Check("Now add that onto the position and write it back: an Add "
                  "node into a Set Attribute on P.",
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
        goal="The same inflate, but the amount becomes a slider on the "
             "wrangle that you can drag while watching the viewport.",
        steps=(
            "A number you will want to tweak should not be buried in the "
            "graph. A channel turns it into a knob on the wrangle itself.\n\n"
            "1. Keep the inflate graph from the last exercise (or rebuild "
            "it).\n"
            "2. Find the Multiply node. Where you typed 0.1, type this "
            "instead, including the quotes:\n"
            "      chf(\"amount\")\n"
            "3. That is all - a channel is a value you type, not a node to "
            "add. chf means CHannel Float.\n"
            "4. Go to the wrangle in Houdini. It now wants a parameter "
            "called amount: click the small 'Create parameters' button beside "
            "the VEXpression field if it has not appeared, then drag it and "
            "watch the model breathe."),
        nodes=(
            ("multiply", "multiply", "the node whose value you are replacing"),
        ),
        hints=(
            "Channels are typed into a value row - they are parameters, not "
            "nodes, so there is nothing to search for.",
            "The spelling is chf(\"amount\") with the quotes; chf is float, "
            "chi is integer, chv is vector.",
            "The line is: @P = @P + @N * chf(\"amount\");"),
        checks=(
            Check("Start from the inflate: read N and scale it with a "
                  "Multiply.",
                  lambda g, c: _code_has(c, "@N") and _has_node(g, "multiply")),
            Check("Replace the 0.1 with chf(\"amount\") - typed straight "
                  "into the Multiply's value row, quotes included.",
                  lambda g, c: _code_has(c, 'chf("amount")')),
        ),
        deeper=(
            ("Channels, in the manual", "manual: Channels"),
            ("SideFX: ch() and friends", f"{SIDEFX}/vex/functions/ch.html"),
        ),
        solution='@P = @P + @N * chf("amount");',
    ),
    Exercise(
        key="stripes",
        title="Black and white stripes",
        goal="Even-numbered points turn white, odd ones black.",
        steps=(
            "Every point knows its own number: the attribute ptnum. Whether "
            "that number is even is a QUESTION, and questions need an If.\n\n"
            "1. Tab, get attribute: Attribute ptnum, Type int (a whole "
            "number).\n"
            "2. Tab, modulo. Type int. Wire ptnum into a and put 2 in b. "
            "Modulo is the remainder after dividing: even numbers leave 0, "
            "odd ones leave 1.\n"
            "3. Tab, equal. Wire the modulo's Result into a and leave b as "
            "0. Now you have a true/false answer.\n"
            "4. Tab, if else. Wire that answer into its Condition.\n"
            "5. The If / Else node has TWO white body arrows. From the first "
            "one, wire a Set Attribute (Cd, vector, {1, 1, 1} = white). From "
            "the second, another Set Attribute (Cd, vector, {0, 0, 0} = "
            "black).\n"
            "6. Finally wire Start's white arrow into the If / Else itself."),
        nodes=(
            ("get attribute", "attrib_get", "reads ptnum, this point's number"),
            ("modulo", "modulo", "the remainder after a division"),
            ("equal", "is_equal", "asks whether two values are the same"),
            ("if else", "if_else", "runs one branch or the other"),
            ("set attribute", "attrib_set", "one in each branch"),
        ),
        hints=(
            "ptnum % 2 == 0 is the classic 'is it even' test - the remainder "
            "of dividing by two is zero.",
            "The If / Else node has two body arrows; each one gets its own "
            "Set Attribute node.",
            "if (@ptnum % 2 == 0) { @Cd = {1,1,1}; } else { @Cd = {0,0,0}; }"),
        checks=(
            Check("Read the point's number: Get Attribute, Attribute ptnum, "
                  "Type int.",
                  lambda g, c: _code_has(c, "@ptnum")),
            Check("Even or odd is the remainder of dividing by 2: add a "
                  "Modulo node with 2 in it.",
                  lambda g, c: _code_has(c, "% 2")),
            Check("Turn that into a true/false with an Equal (is it 0?) and "
                  "feed an If / Else node's Condition.",
                  lambda g, c: _has_node(g, "if_else")),
            Check("White is {1, 1, 1} and black is {0, 0, 0} - one Set "
                  "Attribute on each of the If / Else's two body arrows.",
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
            "Height is @P.y, but it runs over whatever range your model "
            "happens to occupy, while colour wants 0 to 1. Remapping one "
            "range onto another is called fitting, and it is possibly the "
            "most used idea in all of VFX.\n\n"
            "1. Tab, get attribute: P, Type vector.\n"
            "2. Double-click the P node's output dot. Three little pins "
            "appear: .x, .y and .z. You will use .y.\n"
            "3. Tab, fit. The Fit Range node arrives: wire the .y pin into "
            "its Value, set Old Min to -1 and Old Max to 1 (roughly your "
            "model's height range), New Min to 0 and New Max to 1.\n"
            "4. Tab, make vector. Wire the fit's Result into ALL THREE of "
            "x, y and z - the same number in all three parts is grey, from "
            "black at 0 to white at 1.\n"
            "5. Tab, set attribute: Cd, vector, the Make Vector wired into "
            "Value, and Start's white arrow into its arrow."),
        nodes=(
            ("get attribute", "attrib_get", "reads P"),
            ("fit", "fit_range", "remaps a number from one range to another"),
            ("make vector", "make_vector", "three numbers into a colour"),
            ("set attribute", "attrib_set", "writes Cd"),
        ),
        hints=(
            "A vector output can give you its parts directly: double-click "
            "the output dot to reveal .x .y .z pins.",
            "fit(value, oldmin, oldmax, newmin, newmax) is the workhorse - "
            "remapping ranges is half of all effects work.",
            "float t = fit(@P.y, -1, 1, 0, 1);  @Cd = set(t, t, t);"),
        checks=(
            Check("Read the height: add Get Attribute for P, then "
                  "double-click its output dot and use the .y pin.",
                  lambda g, c: _code_has(c, "@P.y")),
            Check("Remap it onto 0..1 with a Fit Range node.",
                  lambda g, c: _code_has(c, "fit(")),
            Check("Same value in all three parts makes grey: a Make Vector "
                  "with the fit wired into x, y and z, written to Cd.",
                  lambda g, c: _has_node(g, "make_vector")
                  and _code_has(c, "@Cd")),
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
            "gives the same number back. Seed with each point's own number "
            "and every point gets its own offset that never changes - which "
            "is exactly what you want in a render.\n\n"
            "1. Tab, get attribute: ptnum, Type int.\n"
            "2. Tab, random. Pick Random Vector (it gives three random "
            "numbers at once, one per axis). Wire ptnum into its Seed.\n"
            "3. Those numbers land between 0 and 1, so they can only push "
            "one way. Tab, subtract: Type vector, the random into a, 0.5 in "
            "b. Now they run -0.5 to 0.5.\n"
            "4. Tab, multiply: Type vector, that into a, 0.1 in b - a small "
            "nudge rather than an explosion.\n"
            "5. Tab, get attribute for P; Tab, add (Type vector) with P and "
            "the nudge; Tab, set attribute writing P. White arrow from "
            "Start."),
        nodes=(
            ("get attribute", "attrib_get", "reads ptnum, then P"),
            ("random", "random_vector", "three random numbers from one seed"),
            ("subtract", "subtract", "centres the range on zero"),
            ("multiply", "multiply", "scales the nudge down"),
            ("add", "add", "adds the nudge to the position"),
            ("set attribute", "attrib_set", "writes P"),
        ),
        hints=(
            "Random Vector needs a Seed; the point's own number is the "
            "classic choice, and it is why the result is stable.",
            "rand gives 0..1; subtracting 0.5 lets the points move both "
            "ways instead of only one.",
            "@P = @P + (vector(rand(@ptnum)) - 0.5) * 0.1;"),
        checks=(
            Check("Random needs a seed: add a Random Vector node and wire "
                  "ptnum into it.",
                  lambda g, c: _code_has(c, "rand(") and _code_has(c, "@ptnum")),
            Check("Centre the range with a Subtract of 0.5, so the nudge "
                  "can go both ways.",
                  lambda g, c: _code_has(c, "0.5")),
            Check("Scale it down and add it onto P, then write P back.",
                  lambda g, c: _code_has(c, "@P") and _has_node(g, "attrib_set")),
        ),
        deeper=(
            ("SideFX: rand()", f"{SIDEFX}/vex/functions/rand.html"),
        ),
        solution="@P = @P + (vector(rand(@ptnum)) - 0.5) * 0.1;",
    ),
    Exercise(
        key="falloff",
        title="Shrink with distance",
        goal="Points near the centre stay big, far ones shrink away. (Give "
             "the geometry something to show it - copy spheres onto the "
             "points, say.)",
        steps=(
            "How far is a point from the origin? That is the length of its "
            "position vector. Feed that distance through a fit - backwards - "
            "and you have a falloff.\n\n"
            "1. Tab, get attribute: P, vector.\n"
            "2. Tab, length. Wire P into it. The output is a single number: "
            "the distance.\n"
            "3. Tab, fit. Value from the length, Old Min 0, Old Max 2, and "
            "now the trick: New Min 1, New Max 0. Backwards on purpose, so "
            "near means big.\n"
            "4. Tab, set attribute: Attribute pscale, Type float (it is a "
            "single number, not a colour), the fit into Value, white arrow "
            "from Start. pscale is the attribute Houdini reads as per-point "
            "size."),
        nodes=(
            ("get attribute", "attrib_get", "reads P"),
            ("length", "length", "how long a vector is - here, a distance"),
            ("fit", "fit_range", "remaps the distance into a size"),
            ("set attribute", "attrib_set", "writes pscale, Type float"),
        ),
        hints=(
            "pscale is what instancing and rendering read as per-point size.",
            "Fitting 0..2 onto 1..0 - the new range backwards - is what "
            "turns a distance into a falloff.",
            "@pscale = fit(length(@P), 0, 2, 1, 0);"),
        checks=(
            Check("Measure the distance first: a Length node fed with P.",
                  lambda g, c: _code_has(c, "length(")),
            Check("Remap it with Fit Range - New Min 1 and New Max 0, "
                  "backwards, so near is big.",
                  lambda g, c: _code_has(c, "fit(")),
            Check("Write it to pscale, with Type float.",
                  lambda g, c: _code_has(c, "@pscale")),
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
            "A direction from A to B is a subtraction. From a point toward "
            "the origin is therefore minus its own position. Directions "
            "should also have length 1, which is what normalize does.\n\n"
            "1. Tab, get attribute: P, vector.\n"
            "2. Tab, negate. IMPORTANT: set its Type to vector - it starts "
            "as float, and a dropdown that says the wrong type is the most "
            "common reason a wire refuses to connect.\n"
            "3. Tab, normalize. Wire the negate into it.\n"
            "4. Tab, set attribute: N, Type vector, wired from normalize, "
            "with Start's white arrow."),
        nodes=(
            ("get attribute", "attrib_get", "reads P"),
            ("negate", "negate", "flips the sign - set its Type to vector"),
            ("normalize", "normalize", "keeps the direction, sets length 1"),
            ("set attribute", "attrib_set", "writes N"),
        ),
        hints=(
            "Toward the origin from P is simply -P: the direction back to "
            "zero.",
            "Directions want length 1: normalize() keeps the direction and "
            "throws the distance away.",
            "@N = normalize(-@P);"),
        checks=(
            Check("The direction to the origin is minus the position: add a "
                  "Negate node (Type vector) fed with P.",
                  lambda g, c: _code_has(c, "-@P")),
            Check("Give it length 1: pass it through a Normalize node.",
                  lambda g, c: _code_has(c, "normalize(")),
            Check("Write the result to N.",
                  lambda g, c: _code_has(c, "@N")),
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
            "Every exercise so far changed points that already existed. A "
            "wrangle can also MAKE geometry - and a loop repeats the "
            "making.\n\n"
            "1. Tab, repeat. The Repeat node arrives: set Times to 5, and "
            "wire Start's white arrow into it.\n"
            "2. Notice it has its own white BODY arrow, and an output called "
            "Number. That number counts 0, 1, 2, 3, 4 - and it only exists "
            "inside the body.\n"
            "3. Tab, add point. Wire the Repeat's BODY arrow into its white "
            "arrow, so it runs once per pass.\n"
            "4. Now the position. Tab, multiply (Type float): the Repeat's "
            "Number into a, 0.1 into b - that is how far along this pass "
            "goes.\n"
            "5. Tab, get attribute for N; Tab, multiply (Type vector) with N "
            "and the step; Tab, get attribute for P; Tab, add (Type vector) "
            "with P and that. Wire the result into Add Point's Position.\n\n"
            "Five points per point, each one a step further out."),
        nodes=(
            ("repeat", "for_range", "runs the steps inside a fixed number of times"),
            ("add point", "add_point", "creates a new point"),
            ("get attribute", "attrib_get", "reads N and P"),
            ("multiply", "multiply", "the step, and the push along N"),
            ("add", "add", "position plus push"),
        ),
        hints=(
            "The Repeat node's Number output counts the passes and only "
            "exists inside its body - wiring it outside is an error the "
            "tool will explain.",
            "Multiply the Number by 0.1 for this pass's distance, multiply "
            "N by that, then add P.",
            "for (int i = 0; i < 5; i++) {\n"
            "    addpoint(0, @P + @N * (0.1 * i));\n}"),
        checks=(
            Check("Add a Repeat node, Times 5, wired into the white chain.",
                  lambda g, c: _has_node(g, "for_range")),
            Check("Inside its body arrow: an Add Point node.",
                  lambda g, c: _has_node(g, "add_point", "vex_addpoint")
                  and _code_has(c, "addpoint(")),
            Check("Each new point steps further out: the Repeat's Number "
                  "times 0.1, along N, added to P.",
                  lambda g, c: _code_has(c, "@N") and _code_has(c, "0.1")),
        ),
        deeper=(
            ("SideFX: addpoint()", f"{SIDEFX}/vex/functions/addpoint.html"),
            ("Functions and subgraphs, in the manual", "manual: Functions"),
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
