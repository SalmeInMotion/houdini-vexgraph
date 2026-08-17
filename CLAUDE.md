# VEXgraph — working notes

A Houdini editor that translates both ways between VEX and a node graph, plus
a Beginner course that teaches VEX by building graphs. Public repo:
`github.com/SalmeInMotion/houdini-vexgraph`.

Everything here is what the code and `git log` do **not** say. Read it before
changing the importer, the Houdini glue, or the course.

## Running things

```
.venv/Scripts/python.exe -m pytest -q                          # ~390 tests, ~2 min
"C:/Program Files/Side Effects Software/Houdini 21.0.700/bin/hython.exe" \
    tests/test_houdini_node.py                                 # 17 checks, self-running
.venv/Scripts/python.exe tools/audit_corpus.py                 # the 414-snippet corpus
```

`tests/test_houdini_node.py` has no pytest inside Houdini, so it runs itself and
is skipped by the ordinary suite. The audit is the real regression net for the
importer: **broken must stay at 0** (a snippet that becomes nodes and then emits
VEX the compiler rejects). Baseline 222 clean / 43 partial / 13 all-inline / 0
broken. The other 136 are snippets that did not compile to begin with — they
depend on scene context we do not have, and are not our problem.

## Houdini traps, learned the hard way

- **Reloading.** Every door into the editor — the shelf button, the Python
  Panel, the button on the wrangle — goes through `vexgraph_reload.fresh()`,
  which reloads only when the files on disk are newer. So editing this project
  needs **no Houdini restart**: just reopen the editor. The exceptions are the
  three files read at startup — `houdini/python/vexgraph_reload.py`,
  `houdini/toolbar/vexgraph.shelf`, `houdini/python_panels/vexgraph.pypanel` —
  and the button callback baked into old `.hip` files, which `add_open_button`
  rewrites when it finds an out-of-date one.
- **Keys.** Houdini filters key events at the application level, so `Delete`,
  `Tab`, `B` and friends never reach a docked panel on their own. The only
  thing that wins is another application-level filter installed *after*
  Houdini's (Qt runs the most recent first) — `panel._install_key_filter`.
  Accepting `ShortcutOverride` alone does not work; neither does the view's
  `keyPressEvent`.
- **Focus.** Delete goes to whatever holds the keyboard, so anything that
  selects a node from elsewhere (a code line, a Problems entry, the library)
  must call `scene.focus_canvas()`, and the code pane claims focus on click
  (Houdini spends the first click activating the pane).
- **The wrangle wins.** On attach, if the node's VEX differs from what the
  stored graph emits, the user is asked — and the wrangle's own code is the
  default answer, re-read through the importer. The stored graph is only a
  record of how the code was last built.
- **HOM identity.** Compare `node.path()`, never `is`: Houdini hands back fresh
  wrapper objects for the same node.

## The course (`vexgraph/learn.py`)

- No model, no connection: each exercise is checked deterministically, and
  **the first failing check IS the lesson** shown to the student. The assistant
  is an optional professor on top, never a dependency.
- **Be lenient about spelling, strict about meaning.** Numbers compare as
  numbers (`.1` == `0.1`), `set(1,0,0)` == `{1,0,0}`, and `#` in a check means
  "any number". Marking a correct graph wrong is the worst thing a teacher can
  do — it makes people quit something they had actually got right.
- Exercises must name nodes by the label the library shows (typing "modulo"
  finds a node called **Remainder**). `test_an_exercise_never_names_a_node_that_does_not_exist`
  enforces this — it caught three more the day it was written.
- Learn's folded state is deliberately never remembered: the panel opens on the
  tool, not on teaching material.

## Open question

The name. VEXgraph is the tool; "Professor Vex" was floated for the tutor
persona. **Ivan's call, nothing renamed** — do not improvise one.
