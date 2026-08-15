# VEXgraph

Build Houdini VEX by wiring nodes together, Blueprint style, and get out a
wrangle snippet that reads like something a person wrote.

It goes **both ways**. Graph → VEX generates a wrangle snippet; VEX → graph
reads one back. That is what makes this a translator between two
representations rather than another way to author one of them: paste the VEX an
AI wrote you, see it as nodes, understand it, change a node, get VEX back.

**Status: core, editor, and assistant all work.** The node canvas runs inside
Houdini as a Python Panel, or standalone for development.

## Trying it

Windows, Houdini 21 or 22. Clone it anywhere, then:

```bash
python houdini/install.py
```

That writes a Houdini package file pointing at wherever you cloned it. Restart
Houdini and you have:

- **VEXgraph Wrangle** in the SOP Tab menu — creates an Attribute Wrangle and
  opens the editor on it
- a **VEXgraph** shelf tool — opens the editor on whatever wrangle is selected
- **New Pane Tab Type ▸ Python Panel ▸ VEXgraph** — the panel on its own

Nothing else is required: the editor uses the PySide6 that ships with Houdini,
and the node library is already in the repository.

Two optional extras:

- **The Claude assistant** needs the `anthropic` package, which Houdini's Python
  does not have. Create the project's own environment and it will be used
  automatically: `python -m venv .venv` then
  `.venv/Scripts/pip install anthropic`. Set `ANTHROPIC_API_KEY` in your
  environment. Without this the rest of the tool works; only the Ask box says
  what is missing.
- **The local model** option needs [Ollama](https://ollama.com) running. Be
  warned by the measurements further down before relying on it.

To run the tests, or the editor without Houdini:

```bash
.venv/Scripts/pip install pytest PySide6
.venv/Scripts/python -m pytest tests -m "not slow"
.venv/Scripts/python -m vexgraph edit
```

## Importing VEX

```bash
python -m vexgraph import snippet.vex --out graph.vexgraph.json
```

or press **Import VEX** in the editor and paste.

**Nothing is ever rejected.** A statement that maps becomes nodes; a statement
that does not — a `while`, a struct, a ternary, a function nobody wrote a node
for — becomes one **Inline VEX** node holding the original text, byte for byte.
So an import always succeeds and always round-trips, and gets better as the
library grows instead of being blocked until the parser is finished.

The reverse index is **derived, not written**: which node implements `fit` is
already stated by that node's own code template, so reading it back out means
the importer learns every node the library gains, all 1276 of them, with no
lookup table to maintain. Curated nodes win over generated ones, so `fit(...)`
comes back as *Fit Range* rather than the raw function.

Arity comes from the call in the template, not the socket count — those differ
constantly, because an argument can be a node setting (`point(0, "Cd", n)` has
two) or a `&` output parameter that is not an input at all (`xyzdist` passes
two, and they become the node's outputs).

The round trip is tested as a fixed point: for each example, graph → VEX →
graph → VEX must come back **byte for byte identical**.

## Why not just use VOPs

VOPs already compile a node network to VEX, and they run in the same CVEX
context an Attribute Wrangle does, so the two are equivalent in power. What VOPs
do not have:

- **Exec wires.** VOPs are pure dataflow, so the order of side effects
  (`setpointattrib`, `addpoint`, `removepoint`) is implicit and awkward. Here a
  white wire says what runs when, which is what makes "add a point, then colour
  it" drawable rather than inferred.
- **Readable output.** VOP-generated VFL is machine output. This emits VEX with
  variable names taken from the nodes, values inlined where that reads better,
  and brackets only where precedence requires them.
- **Task-level nodes.** A node per VEX function helps nobody who does not
  already know VEX. Tier 1 is written for what an artist wants to do.
- **A way back.** VOPs cannot be produced from text, and cannot be generated
  from existing VEX at all. This reads VEX in and writes VEX out, which is the
  argument for the tool existing: it is a comprehension aid with editing
  attached, not a faster way to type.

### Should the VOP node set be imported wholesale

No, and the numbers are the reason. Houdini has 1299 VOP node types against the
1275 this generates from `vcc`, but only **163 share a name with a VEX
function**. The rest are version duplicates (`addattrib::2.0`), shading VOPs
that mean nothing inside a SOP wrangle, operators under other names (`add` for
`+`), and subnets. Since VOPs compile to VEX, anything a VOP does is by
construction reachable from here — so importing the list would add no capability
and would bury the 64 curated nodes that make the library approachable.

Their **icons** were worth taking. Those are read at runtime from the local
install (`$HFS/houdini/config/Icons/icons.zip`, 447 SVGs) rather than copied
into this repository, so no SideFX artwork ships here and the icons always match
the Houdini being run. The mapping in `ui/icons.py` is by hand and deliberately
partial: our nodes are named for tasks and VOP's for functions, so most pairings
are a judgement, and a wrong icon is worse than none because it is read as a
claim about what the node does.

## Houdini's own help, inside the panel

`help.py` reads `$HFS/houdini/help/vex.zip` — the same text as the website, so
there is nothing to scrape and nothing to keep in sync. Selecting a node shows
its summary, one line per overload, the worked examples SideFX ships (334 of the
1120 pages have one) and a "see also" list. **Docs ↗** opens the full page in a
browser for anything not covered.

Which function a node documents is derived from its template
(`NodeDef.vex_function`), not declared — the template already states what the
node calls, and a second copy would be a second thing to get wrong.

## The two tiers

**Tier 1 — curated** (~64 nodes). Named for the job, not the function:
*Closest Point On Surface*, *Push Along Normal*, *Repeat*, *Make Variable*. Each
one emits a few lines of VEX. This is the part that makes the tool worth using.

**Tier 2 — generated** (~1275 nodes). Every fixed-arity VEX function in the CVEX
context, built from two sources that are each authoritative about one thing:

| Source | Used for |
| --- | --- |
| `vcc -X cvex` | which functions exist and their exact signatures |
| `$HFS/houdini/help/vex.zip` | one-line summaries and real argument names |

Regenerate after a Houdini upgrade:

```bash
python -m vexgraph.autogen
```

## Using it

Inside Houdini, after installing the package (below): select an Attribute
Wrangle, press the **VEXgraph** shelf tool, build the graph, press **Apply to
Wrangle**. The graph is stored in the node's user data, so it travels inside the
.hip; the snippet parm is generated output and says so in its first line.

Standalone, which is how it gets developed:

```bash
python -m vexgraph.ui examples/stick_to_surface.vexgraph.json
```

From the command line:

```bash
python -m vexgraph nodes "closest surface"     # search the palette
python -m vexgraph show closest_surface_point  # sockets, settings, emitted VEX
python -m vexgraph build examples/stick_to_surface.vexgraph.json
python -m vexgraph catalog --tier 1 --out catalog.json   # palette for an LLM
```

Building a graph in Python looks the way the editor will drive it:

```python
from vexgraph import Graph, default_registry
from vexgraph.vccmap import build

g = Graph(default_registry())
g.add("start", "start")
g.add("closest_surface_point", "closest", input="1")
g.add("position_on_primitive", "on_surface", input="1", attrib="P", type="vector")
g.add("attrib_set", "write", attrib="P", type="vector")
g.chain("start", "write")                      # exec order
g.connect("closest", "primitive", "on_surface", "primitive")
g.connect("closest", "uv", "on_surface", "uv")
g.connect("on_surface", "value", "write", "value")

emission, compiled = build(g)
print(emission.code)
```

```c
// Built with VEXgraph. Edit the graph, not this code.
int primitive = -1;
vector spot_on_primitive = {0, 0, 0};
float distance_value = xyzdist(1, @P, primitive, spot_on_primitive);
vector position_on_primitive = primuv(1, "P", primitive, spot_on_primitive);
@P = position_on_primitive;
```

## The editor

Modelled on ComfyUI's canvas, because the two things it does well are exactly
what this audience needs: the port colour says what a wire carries before you
read a label, and values live inline on the node rather than in a properties
panel somewhere else.

- **Typed ports.** One colour per VEX type; lists are squares rather than
  circles, because a list of vectors and a vector are too different for colour
  alone to carry. Run-order pins are white arrows.
- **Inline values.** An unconnected input shows its value on the node, scrubbable
  or typed. Connect a wire and the row disappears, because the wire is the value
  now and leaving a stale number under it invites mistrust.
- **Refusal while dragging.** A wire that cannot be made goes red and dashed
  before you let go, and the reason appears in words at the bottom.
- **The code pane.** Selecting a node lights up the lines it wrote; clicking a
  line selects the node that wrote it. This is the part that teaches.
- **Tab** or double-click to search nodes. Dropping a wire on empty canvas
  searches only what could accept it.
- **Navigation matches Houdini's network editor**: wheel to zoom (0.15x-5x),
  middle-drag or right-drag to pan, alt+left-drag as a Maya-style alternative.
- **Text size** in the toolbar (90%-150%, default 115%) scales node text, the
  code pane, the library and the assistant together. The library's "What it
  does" panel sits in its own splitter section, so it can be dragged taller
  independently of the tree above it.
- **Tidy** lays out a graph that has no positions — which is every graph built
  in code, and every graph the assistant writes.
- **The node library** sits open on the left: categories, a line of description
  per node, and the full help for whatever is selected. Tab-search assumes you
  know what the node is called; the library is for when you don't. Search from
  it reaches all 1300 nodes, including the generated tier the tree hides.

## The assistant

Describe what you want; get a graph. There are **two routes** to that graph,
picked per provider (override with `route` in the worker request):

- **Catalogue route** (Claude's default): the model never writes VEX — it picks
  node types from a closed catalogue and wires them by socket name.
- **Write-VEX route** (Local's default): the model writes plain VEX — the thing
  every model has actually been trained on — `vcc` compiles it, and the
  importer lowers it onto the canvas per statement, with Inline VEX as the
  never-fatal fallback. The repair loop hands the model the compiler's own
  line-numbered errors against its own code. The catalogue route moved the
  task *outside* a local model's training distribution (choose from 1300
  invented types, under a schema whose constrained decoding is what made a
  32B model sit for 25 minutes); this route moves it back inside.

Either way the guarantee is the same — **nothing reaches the canvas that vcc
does not compile** — it just shifts where the vocabulary comes from.

On the catalogue route, every part of the answer is checked against the
registry before anything is built:

| It returns | What happens |
| --- | --- |
| A node type that doesn't exist | Rejected, with the nearest real types suggested |
| A socket that isn't on that node | Rejected, listing the sockets that are |
| A wire between incompatible types | Rejected, with the same sentence the canvas would show |
| A graph with no Start node | Rejected |
| A graph that emits VEX `vcc` won't take | Rejected, with the compiler's message |

Each rejection goes back to the model as the next message, so it corrects itself
rather than handing you something broken. Only a graph that validates *and*
compiles is offered — and even then it is a **proposal**: the canvas shows it,
the code pane shows its VEX, and nothing reaches the wrangle until you press
**Keep**. Discard puts your previous graph back.

Two providers:

- **Claude** (`claude-opus-5`) — markedly better at reading a vague request and
  picking the right nodes. Reads `ANTHROPIC_API_KEY` from your environment;
  VEXgraph never stores, writes, or asks for a key.
- **Local** (Ollama) — free and offline, but see the honest note below.

The catalogue sent with each request is ~18 KB: all 64 curated nodes plus up to
40 generated VEX-function nodes matched to the request, so the long tail is
reachable without sending 1275 entries.

**About the local model.** Measured on this machine, on the catalogue route:
`gemma4:12b` answered in 26 s and self-corrected once, but wired `@N` where
`@P` belonged — a graph that validates and compiles and does the wrong thing.
`qwen3:32b` did not finish within 25 minutes with schema-constrained decoding.
That measurement is what created the write-VEX route above, which is now the
Local default.

On the write-VEX route, the same request ("push each point along its normal by
a controllable amount, default 0.2, and tint pushed points red based on how far
they moved") took `qwen3:32b` **51 seconds and one attempt**, and produced a
9-node graph with a `chf` channel, the push, and the tint kept as Inline VEX.
It chose `push.z` where distance was asked for — the validation layer stops
*impossible* graphs, not *wrong* ones; that is what the visible graph and the
code pane are for.

**Getting the card back.** Ollama keeps a model resident after answering, which
is the right default until you want to render. The panel shows total VRAM use
and which model is holding it; **Release model** sends `keep_alive: 0` for that
model only — the server stays up, other models stay loaded, and nothing about
the editor changes, so the worst it can cost is a reload. Both readings run on a
worker thread (`VramProbe`): `nvidia-smi` and the Ollama socket each block long
enough that polling them on the UI thread stutters the whole of Houdini.

## The node in the Tab menu

**VEXgraph Wrangle** in the SOP Tab menu creates a plain **Attribute Wrangle**
and opens the editor on it. It is deliberately *not* a custom node type.

The first attempt was a digital asset wrapping a wrangle in a subnet, and it was
wrong: `ch()` and `chf()` resolve against the node running the VEX, which inside
a subnet is the *inner* wrangle. A spare parameter created for the VEX would be
looked for on the wrong node, so channel references silently read 0 — and an
asset would have to re-implement the Create Spare Parameters button to fix it.
An ordinary wrangle gets channel references, ramps and spare parameters for
free, and cannot drift from however Houdini decides wrangles should behave next.

The graph lives in the node's user data and the VEX in the `snippet` parm.
`is_wrangle()` accepts anything with a `snippet` parm rather than only matching
a list of type names, which is the condition that actually matters.

Checks live in `tests/test_houdini_node.py`, which runs itself under `hython`
because Houdini's Python has no pytest:

```bash
hython tests/test_houdini_node.py
```

## Keyboard inside a Python Panel

Houdini's hotkey manager sees key presses before an embedded panel does, and
swallows the ones it recognises — Delete among them. A widget claims a key by
accepting `ShortcutOverride`, which is what `GraphView.event()` does for Delete,
Backspace, Tab, Escape, F, Z and Y. Without it, Delete never arrives as a
`keyPressEvent` at all, which is why deleting worked in the tests and not in
Houdini.

Because that is host-dependent, deleting does not rely on it: there is a
**Delete** button on the toolbar and a right-click menu on the canvas, neither
of which can be intercepted.

## Installing into Houdini

```bash
python houdini/install.py
```

Writes `vexgraph.json` into the launcher's shared `packages` folder when there is
one, so a single file serves every Houdini build and sits alongside the other
tools. Falls back to each `houdini*.*/packages` folder under your real Documents
directory (following the OneDrive redirect). Use `--per-version` to force the
latter, `--version 21.0` to limit it, or `--uninstall` to remove it.

Installing in both places would load the package twice and register the panel
twice, so the installer reports any leftover per-version copies.

**Houdini 21 and 22 both.** The package format is identical between them — there
is no versioned variant to write. Verified by building the whole editor under
each: H21.0.700 (Python 3.11, Qt 6.5.3) and H22.0.368 (Python 3.13, Qt 6.8.3)
import the same snippet to the same 35 nodes and emit the same 10 lines.

Verified end to end in Houdini 21.0.700: the panel writes the snippet and the
Class parm, the graph round-trips through user data unchanged, and the wrangle
cooks with no errors — `stick_to_surface` lands every point exactly 1.0 from the
centre of a unit sphere.

One trap worth recording: `hou.homeHoudiniDirectory()` reports
`C:/Users/<you>/houdini21.0` under **hython**, but the GUI resolves the redirected
Documents folder (here `OneDrive/Documentos/houdini21.0`, localised). Trusting
hython's answer would install where Houdini never looks.

## How it decides where a value goes

Exec wires give an order and map straight onto statements. Data wires only give
dependencies, so the emitter has to choose a home for each computed value:

> A value is computed in the deepest scope that still comes before every use of
> it.

Needed by both branches of an If, it lands before the If. Needed only inside a
loop, it lands inside the loop, where a reader expects to find it. And if it
depends on a loop's index but is used after the loop, that is not a placement
problem — it is reported as a mistake, in those words.

Values are inlined into their consumer when that reads better, unless
re-evaluating them would change the answer: `rand()` feeding two places becomes
one variable, `@P` feeding five places stays `@P`.

## Checks that happen before the compiler does

The point of a typed graph is that the mistake is caught while the wire is being
dragged, with a sentence that says what to do instead:

- Type mismatches — *"A vector has 3 components. Use Length, or a Vector to
  Float node to pick one."*
- Narrowing — float to int needs an explicit Round / Floor / Truncate, because
  silent truncation is exactly the quiet wrongness a visual tool exists to stop.
- Loop-only nodes (*Stop Repeating If*) outside a loop.
- A variable read outside the scope it was made in.
- Cycles, unreachable statement nodes, missing Start.

What survives that goes through `vcc`, and any error it reports is mapped back
through the emitter's line map to the node that produced the line. The compiler
check is shared with [VEXpress](../houdini-vexpress) rather than duplicated.

## Layout

```
vexgraph/
  vextypes.py   VEX types, coercion rules, attribute prefixes
  nodedefs.py   what a node is; loads the JSON library
  graph.py      nodes, wires, validation, save/load
  codegen.py    scope placement, emission, the line map
  vccmap.py     compile the result and blame the right node
  autogen.py    build tier 2 from vcc + the Houdini help
  cli.py        build / nodes / show / catalog
  ui/
    theme.py    colours, metrics, the type-to-colour table
    items.py    nodes, ports, value rows, wires
    canvas.py   the scene and view; wiring, panning, refusals
    layout.py   automatic arrangement for graphs with no positions
    codeview.py the VEX pane and its highlighting
    palette.py  node search
    browser.py  the browsable node library
    assistant_panel.py  the ask-for-a-graph pane
    panel.py    the editor as a whole
  parser/
    lexer.py    VEX to tokens, with source spans for the escape hatch
    syntax.py   the grammar and the AST
    lower.py    AST to graph; the derived reverse index
  assistant/
    catalog.py  the closed list of nodes the model may choose from
    agent.py    validation and the repair loop
    providers.py  Claude and the local model
    worker.py   one request in a subprocess, for Houdini's Python
houdini/        package, shelf tool, Python Panel, installer
nodes/          the library, as JSON
examples/       graphs that double as end-to-end tests
tools/shot.py   render the editor to a PNG without opening a window
```

## Tests

```bash
python -m pytest              # fast suite: core + editor
python -m pytest -m slow      # compiles all ~1300 generated nodes, ~4 minutes
```

The editor tests run Qt offscreen and drive real widgets: every curated node is
built on a canvas, wires are refused and accepted, and the code pane is checked
against what the CLI would emit.

The slow one instantiates every node definition in a minimal graph and puts the
result through `vcc`. It is what stands between a misparsed signature and a node
that quietly emits broken VEX.

## Real bugs worth knowing about

### Imported VEX referenced variables that no longer existed

Importing `vector push = v@N * 0.2; v@P += push; @Cd.r = length(push);`
produced a graph whose emitted code did not compile — the pasted VEX did, the
round-trip's did not. Two independent faults, both on the seam between
translated nodes and the Inline VEX escape hatch:

- A declaration whose value is a port and is never reassigned becomes an
  *alias*: `push` is the multiply node's output, and no variable called `push`
  is emitted at all. That is right for translated code, which reads the wire —
  and wrong for a statement kept verbatim, which still says `push`. The
  importer now re-declares such a variable (a Make Variable node fed from the
  same wire) just before any inline statement that names it.
- `push` is also a VEX *function*, so the emitter deliberately declares it as
  `push_value` rather than shadow the builtin. Generated code follows that
  rename; hand-written code cannot. Inline VEX is now rewritten to follow it.

Found by pointing a local model at the write-VEX route — its code compiled and
the round-trip's did not, which is exactly the case the belt-and-braces check
after import exists to catch. If it ever fires anyway, the assistant keeps the
model's snippet whole in one Inline VEX node rather than feeding it errors
about code it never wrote.

### The scene index could crash Houdini

Rebuilding a node destroys every port and row and recreates them. Qt's BSP
scene index purges removed items on a **zero-delay timer**, so an item freed
before that timer runs leaves a dangling pointer in the index, and the next
scene update walks freed memory — a hard segfault that takes Houdini down with
it, no exception, no traceback.

Changing the text size rebuilds *every* node at once, which is what made it
easy to hit ("something made it break and both its window and Houdini closed
suddenly"). Shrinking crashed far more reliably than growing.

The fix is `setItemIndexMethod(NoIndex)`. Qt recommends exactly that for scenes
where items are added, removed or moved often, which is this one; lookup becomes
linear, which is nothing at this scale. Detached child items are also held for
one extra event-loop turn (`items.retire`) rather than freed on the spot.

### Houdini's PYTHONHOME broke the assistant

Asking Claude from inside Houdini died with `AssertionError: SRE module
mismatch`. Houdini sets `PYTHONHOME` to its own Python 3.11; the assistant
worker runs in this project's own interpreter, inherits that variable, loads
Houdini's standard library instead of its own, and dies before running a line of
our code. The worker subprocess now starts from an environment with
`PYTHONHOME` / `PYTHONPATH` stripped — and `ANTHROPIC_API_KEY` deliberately kept.

### Editing a value on a node

Editing a value on a node - a Note's text, an attribute name - happens through
a `QLineEdit` embedded in the canvas via `QGraphicsProxyWidget`. Two things
about that turned out not to be safe by default:

1. **Giving the embedded widget logical scene focus is not the same as the
   view holding real OS keyboard focus.** Without the latter, typed keystrokes
   kept going to whichever widget last held real focus (the assistant's Ask
   button) and the row silently ate nothing - the reported symptom was "I
   click and try to write and nothing happens." Fixed by explicitly focusing
   the view before focusing the embedded field.
2. **Committing with Enter crashed the process.** `QLineEdit.editingFinished`
   fires synchronously from inside the widget's own Return-key handling;
   deleting that same widget from directly inside its own event handler is a
   use-after-free. Fixed by deferring the removal with `QTimer.singleShot(0,
   ...)` so it runs after the current event finishes unwinding.

Both are covered by regression tests in `tests/test_ui.py` that simulate a
real click and real keystrokes end to end, not just the data-model call.

All of the above have regression tests. The crash ones assert by *not
segfaulting*: run with the old `BspTreeIndex` restored and the suite dies with
exit 139 instead of failing an assert.

## Known gaps

- **Import is not a full VEX front end.** `while`, `do`, ternaries, structs,
  user-defined functions and multi-declarations land in Inline VEX nodes. They
  survive intact and still compile; they just are not nodes.
- **The assistant can be confidently wrong.** Validation proves a graph is
  *possible*, not that it is what you asked for. Read the graph before keeping.
- **Run-order wires can take long routes.** The layout ranks data and exec wires
  together; weighting exec chains would keep sequences straighter.
- **No `while` loop.** A condition wired into a while header would be computed
  once, outside the loop, and spin forever. Use *Repeat* with *Stop Repeating
  If* until inputs can be marked for re-evaluation per iteration.
- **Unused outputs are still declared.** A node with several outputs declares
  all of them even when only one is wired.
- **CVEX context only.** Tier 2 is generated for wrangles; shading contexts
  would need their own pass.
