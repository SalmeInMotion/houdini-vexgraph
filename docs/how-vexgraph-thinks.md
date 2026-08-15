# How VEXgraph thinks

VEXgraph is a translator between two spellings of the same program: VEX
code and a node graph. Neither side is the "real" one - edit whichever
reads better to you, and the other follows. This document explains what
happens under the hood in plain terms, and names the ideas we invented
along the way. SideFX's own VEX documentation (Help ▸ VEX in Houdini, or
the node's help button) covers the *language*; this covers the *bridge*.

## The one idea everything hangs off

A wrangle is a tiny program that Houdini runs **once per element** - once
per point, per primitive, per vertex, depending on the wrangle's Run Over
setting. When you write `@P.y = 0;`, you are not flattening the whole
mesh with one command; you are saying "whoever I am, set *my* height to
zero", and Houdini says it to every point at once.

Everything else in VEX is decoration on that idea: attributes are the
columns of Houdini's geometry spreadsheet (`@P`, `@Cd`, `@ptnum`),
variables are scratch values that live only while one element runs, and
functions (`length()`, `noise()`, `addpoint()`) are the same toolbox
VOPs expose as nodes.

## What a graph is made of

Three families of nodes appear on the canvas:

- **The run order** - the white arrow chain starting at **Start**. VEX
  is a list of statements executed top to bottom; the exec chain *is*
  that top-to-bottom. Statements (writing an attribute, adding a point,
  an If, a loop) live on it.
- **Values** - everything else computes something and hangs off the
  chain by data wires: reads, maths, comparisons. They have no arrow
  pins; they run whenever the statement that uses them needs the value.
- **Inline VEX** - the escape hatch. When code cannot be expressed as
  nodes (a `do…while`, a user-defined function), the importer keeps the
  original text, byte for byte, inside one node that participates in the
  run order like any other statement. Nothing is ever lost: the promise
  is *never fail an import*, degrade to text instead.

## Ideas of our own (not in VOPs, not in VEX)

These are the pieces you will not find in SideFX's docs, because we made
them up for this bridge:

**Component pins.** Any vector output offers `.x .y .z` (and `.w` for
vector4) as small float pins of its own - double-click a vector output
to show them. Reading one part of a vector is just a wire, not a Split
Vector box. The emitted code says `@P.y`, exactly as a person would
type it.

**Channels are literals, not boxes.** `chf("scale")` is a spinner on the
wrangle, not a step of the computation, so it lives written inside the
input that uses it. Only `ch(variable_name)` - a channel whose *name* is
computed - needs a node.

**Read sharing by epoch.** Five mentions of `@P` are one Get Attribute
node, shared, *until something writes `@P`* - a write starts a new
"epoch" and later reads get a fresh node. This is safe because reads
are emitted by reference at each use site: `@P` in the code always
means "P as of now", so sharing the node changes nothing about the
meaning, only the amount of wire on screen.

**Set Component.** `@P.y = 0;` is one node (Set Component) and one line,
because the wrangle language has that exact statement. Variables get the
honest long form instead - `pos = set(pos.x, 0, pos.z);` - built from
the source's component pins.

**Random Range.** `fit01(rand(seed), min, max)` is *the* idiom for "a
random number in a range, stable per point". Three calls, one node.

**The While rule.** A While node is safe because reads render by
reference - `while (n < 10)` keeps watching `n` as the body changes it.
But a condition that *does* something each time it is asked (like
`pciterate()`, which advances an iterator) cannot be a wire: the graph
would sample it once, outside the loop, and the code would compile while
meaning something else. Such a loop stays as Inline VEX on purpose.

**Visible coercions.** VEX silently truncates a float fed into an int.
The graph shows that as a Round to Whole node instead - same meaning,
but you can *see* the decision. The one exception is conditions:
`if (rand(x))` means "not zero", never "truncate", so it becomes an
explicit `!= 0` comparison.

## What the importer does with your code, in order

1. **Lex and parse** - the text becomes a tree. Smart quotes and stray
   byte-order marks (souvenirs from web pages) are cleaned first.
2. **Lower** - each statement tries to become nodes. Function calls pick
   the node whose sockets best fit the argument types; literals that
   could never fit disqualify a candidate outright.
3. **Fall back honestly** - anything unsupported becomes Inline VEX,
   with the reason listed in the Issues panel.
4. **Emit and verify** - the graph is turned back into VEX and compiled
   with Houdini's own compiler (vcc). What you see in the code pane is
   proven code, not a guess.

That last step is the tool's conscience: across the 414 installed
snippets, every importable one round-trips to code that compiles.

## Reading a graph like a sentence

A tidy imported graph reads left to right: sources (attribute reads,
channels) on the left, maths in the middle, writes on the right, with
the white chain running along the statements. When you select a node,
the lines it wrote light up in the code pane; clicking a line selects
the node that wrote it. That two-way highlight is the fastest way to
learn VEX from the tool: build something with nodes, read the sentence
it wrote, and shortly you will find yourself typing the sentence first.
