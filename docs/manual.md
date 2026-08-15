# VEXgraph — user manual

*(También disponible en español: [manual.es.md](manual.es.md).)*

VEXgraph is a translator between two spellings of the same wrangle: VEX
code and a node graph. Edit whichever side reads better to you — build
nodes and watch the code write itself, or type code and watch it become
nodes. Neither side is a preview of the other; both are the real thing,
and everything you see in the code pane has been through Houdini's own
compiler before you see it.

For what happens under the hood — and the ideas that exist only in this
tool — read [how-vexgraph-thinks.md](how-vexgraph-thinks.md).

## The panel, left to right

- **Library** — every node, browsable by category. Drag one out, or
  ignore this column entirely and use Tab search.
- **Canvas** — the graph. The white arrow chain is the *run order*:
  VEX executes top to bottom, and that chain is the top-to-bottom.
- **Code pane** — the generated VEX, live. It is editable: type or
  paste any VEX here and press **Ctrl+Enter** to build nodes from it.
- **Problems** — anything wrong, in plain words, each entry naming the
  node at fault. Clicking one selects that node.
- **Assistant** — ask for a graph in your own words, or ask what the
  current one does.

## The first five minutes

1. Press **Tab** on the canvas and type what you want ("noise",
   "distance", "set color"). Pick a node.
2. Drag from an output dot to an input dot to wire values. A wire that
   makes no sense (a vector into a count) is refused with an
   explanation in the status line.
3. Values you have not wired are edited right on the node, in its rows.
4. Watch the code pane write the wrangle as you work. Select a node and
   its lines light up; click a line and its node gets selected.
5. **Live** is on by default: the VEX lands in the wrangle as you edit,
   so the viewport keeps up. A graph with errors is never written — the
   last working code stays until the new one is valid.

## Everyday tools

| Action | How |
| --- | --- |
| Find/add a node | **Tab**, or right-click ▸ *Add node...* |
| Frame everything | **F**, or the *Frame* button |
| Lay the graph out again | *Tidy* button |
| Delete selection | **Delete** / **Backspace**, or right-click |
| Undo / redo | **Ctrl+Z** / **Ctrl+Y** |
| Copy, cut, paste nodes | **Ctrl+C / X / V** |
| Pan | drag with right or middle button, or **Alt**+drag |
| Right-click menu | right-click and release without moving |
| Build nodes from the code pane | **Ctrl+Enter** in the code pane |
| Node's Houdini help page | double-click the node |
| Cancel a wire in progress | right-click, or **Escape** |

**Snippets** opens every ready-made expression installed on this
machine (Houdini's own, OD Tools', and yours) as nodes you can read and
change. **Save as Snippet** puts your current graph in that same list,
under a name that also shows on the canvas. Your snippets appear in the
Attribute Wrangle's native preset menu too.

## Types and colours

Every wire carries one VEX type, and every dot is coloured by it. A
socket whose type is chosen by a dropdown (the *Type* setting on Add,
Get Attribute...) shows the chosen type, never a placeholder. When a
float meets an int, the conversion is shown as a real node (Round to
Whole) instead of happening silently — same meaning as VEX, but visible.

## Vectors and component pins

Any vector output offers its parts as pins of their own:
**double-click a vector output** to show `.x .y .z` (and `.w` on a
vector4) as small float pins. Reading one part of a vector is just a
wire — no Split Vector box — and the code says `@P.y`, exactly as a
person would type it. The Split Vector node still exists for whoever
prefers the box.

## Channels

`chf("scale")` is a spinner on the wrangle, not a computation, so a
channel read lives written *inside* the input that uses it — type
`chf("size")` straight into a node's row. Only a channel whose *name*
is computed needs a node.

## Loops

- **Repeat** — a fixed number of passes, with the pass number as an
  output that exists only inside the body.
- **For Each** — once per item of a list, with the item and its
  position as outputs.
- **While** — as long as a condition stays true. Something inside the
  loop must change the condition, or it never ends. A condition that
  *does* something each time it is asked (like `pciterate()`) cannot be
  a wire and stays as Inline VEX — see the manual of ideas for why.
- **Break If / Skip If** — leave the loop, or skip to the next pass.

## Functions

A graph can define functions and call them — the node editor's
equivalent of VOP subnets, except they are real VEX functions.

- **Importing** code that defines functions (`int drawLine(...) {...}`)
  gives each function its own inner graph; the calls appear as nodes.
- **Double-click a call node** to step inside. A breadcrumb bar appears
  above the canvas with the function's signature; its button is the way
  back. The code pane keeps showing the whole document while you are
  inside, highlights included.
- **Collapse**: select some nodes, right-click ▸ *Collapse into a
  function...*, give it a name. What fed the selection becomes
  parameters; the one value that left it becomes the return; the
  selection becomes a call.

Collapse refuses politely — and puts everything back exactly as it
was — when the selection cannot honestly be a function:

- **It writes an attribute.** `@Cd = ...` only means something in the
  main body; VEX functions cannot touch `@` bindings. (Attribute
  *reads* are fine: they quietly stay outside, and their values arrive
  as parameters.)
- **It produces more than one value used outside.** A function returns
  one thing.
- **Its value is used by several steps.** A call happens once; the
  expression it replaces was re-composed at every use, and a change
  between two uses would be missed.
- **Its value drives a While condition**, which is re-checked every
  pass — no once-made call can do that. Collapse the whole loop
  instead.
- **A loop or branch is selected without its whole body.**
- **The name is taken**, is a VEX keyword or type name, or contains
  characters VEX identifiers cannot (accents included).

## Inline VEX — the escape hatch

Anything the importer cannot express as nodes is kept, byte for byte,
in an Inline VEX node that runs like any other statement. Nothing is
ever lost or rejected: the promise is *never fail an import*, degrade
to text instead. The Problems list says exactly what stayed as text and
why. `do...while` loops and a few other corners live there today.

## The assistant

Ask in your own words — "push points along their normals, more at the
top" — and choose whether the answer arrives as nodes or as an
explanation. Local models run on your own machine (and unload
themselves when idle); answers that build graphs go through the same
importer and the same compiler checks as everything else.

## When something looks wrong

- **A node has an orange or red ring** — the Problems list has a plain
  sentence about it.
- **The code pane shows old code** — it regenerates a moment after you
  stop editing; the *Live* checkbox controls whether it lands in the
  wrangle.
- **A wire refuses to connect** — the status line says why, and
  usually which node would fix it (Length, Make Array...).
- **After Ctrl+Enter the graph looks rearranged** — building from code
  lays the nodes out fresh; the code itself is what is preserved.
