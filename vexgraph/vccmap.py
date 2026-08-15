"""Run the generated VEX past the compiler and blame the right node for it.

A node graph can produce code that will not compile — a hand-edited definition,
an attribute read as the wrong type, a function used in a way the catalogue does
not describe. When that happens the useful thing is not the compiler's message,
it is *which node on the canvas* to look at, which the emitter's line map knows.

Compilation itself is delegated to VEXpress's vexcheck, which already knows how
to wrap a wrangle snippet into a standalone program so `@P` means something.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .codegen import Emission
from .graph import ERROR, Graph, Issue

# The compiler reports "line 4: Error 1066: ..." once vexcheck has renumbered
# it back to the snippet's own lines.
LINE_RE = re.compile(r"^line (\d+):\s*(.*)$")


@dataclass
class CompileResult:
    ok: bool
    raw: str = ""
    issues: list[Issue] = field(default_factory=list)
    skipped: str = ""       # why no compiler ran, if none did

    @property
    def checked(self) -> bool:
        return not self.skipped


@lru_cache(maxsize=1)
def _vexcheck():
    """Import VEXpress's compiler wrapper, wherever it happens to live.

    Kept as a sibling project rather than vendored: it is the same check the
    text-based tool runs, and two copies would drift.
    """
    candidates = [
        os.environ.get("VEXPRESS_PATH", ""),
        str(Path(__file__).resolve().parents[2] / "houdini-vexpress"),
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "vexcheck.py").is_file():
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            import vexcheck  # noqa: PLC0415

            return vexcheck
    return None


def compile_check(emission: Emission, graph: Graph | None = None) -> CompileResult:
    """Compile the emitted snippet and turn each error into a node issue."""
    if not emission.code.strip():
        return CompileResult(True, skipped="nothing to compile")

    vexcheck = _vexcheck()
    if vexcheck is None:
        return CompileResult(
            True, skipped="VEXpress not found next to this project; set "
                          "VEXPRESS_PATH to compile-check generated code")

    ok, raw = vexcheck.check(_expand_hscript(emission.code))
    if ok:
        return CompileResult(True, raw)

    issues = [_issue_for(line, emission, graph) for line in raw.splitlines()
              if line.strip()]
    return CompileResult(False, raw, [i for i in issues if i])


# A wrangle expands hscript variables textually before the compiler runs, so
# vcc never sees a `$`. Checking the code as written would report an error on
# something Houdini accepts; substituting a stand-in first checks what Houdini
# will actually compile. The values only need to have the right *type*.
_HSCRIPT_STANDIN = {
    "$PI": "3.14159265358979",
    "$F": "1", "$FF": "1.0", "$T": "0.0", "$FPS": "24.0",
    "$FSTART": "1", "$FEND": "240", "$NFRAMES": "240",
    "$SF": "1", "$ST": "0.0", "$SFPS": "24.0",
    "$OS": '"node"', "$HIP": '"/hip"', "$JOB": '"/job"', "$HIPNAME": '"scene"',
    "$USER": '"user"', "$HOME": '"/home"', "$TEMP": '"/temp"',
}
_HSCRIPT_RE = re.compile(r"\$[A-Za-z_]\w*")


def _expand_hscript(code: str) -> str:
    return _HSCRIPT_RE.sub(lambda m: _HSCRIPT_STANDIN.get(m.group(0), "0"), code)


def _issue_for(message: str, emission: Emission,
               graph: Graph | None) -> Issue | None:
    match = LINE_RE.match(message.strip())
    if not match:
        return Issue(message.strip(), severity=ERROR)

    line_no, detail = int(match.group(1)), match.group(2)
    # The header comment occupies line 1 of the snippet, and the line map is
    # keyed by the same numbering, so no adjustment is needed here.
    node_id = emission.node_at_line(line_no)
    return Issue(_readable(detail, node_id, graph), node_id, severity=ERROR)


def _readable(detail: str, node_id: str, graph: Graph | None) -> str:
    """Say which node broke before repeating what the compiler said.

    The compiler's wording assumes you wrote the line. Naming the node first
    turns it back into something about the graph.
    """
    detail = re.sub(r"^Error \d+:\s*", "", detail).strip()
    if not node_id or graph is None or node_id not in graph.nodes:
        return detail
    node = graph.nodes[node_id]
    label = node.title or graph.definition(node).label
    return f"{label} produced code the compiler rejected: {detail}"


def check_source(code: str) -> CompileResult:
    """Compile a raw wrangle snippet that has no graph behind it.

    Used by the write-VEX assistant route: the errors come back numbered
    against the model's own lines, which is the shape a model is best at
    fixing. No node blame — there are no nodes yet.
    """
    if not code.strip():
        return CompileResult(True, skipped="nothing to compile")
    vexcheck = _vexcheck()
    if vexcheck is None:
        return CompileResult(
            True, skipped="VEXpress not found next to this project; set "
                          "VEXPRESS_PATH to compile-check generated code")
    ok, raw = vexcheck.check(_expand_hscript(code))
    if ok:
        return CompileResult(True, raw)
    issues = [Issue(line.strip(), severity=ERROR)
              for line in raw.splitlines() if line.strip()]
    return CompileResult(False, raw, issues)


def build(graph: Graph, *, check: bool = True) -> tuple[Emission, CompileResult]:
    """Emit and verify in one step - the call the UI and the CLI both make."""
    from .codegen import generate  # noqa: PLC0415

    emission = generate(graph)
    if not check or not emission.ok:
        return emission, CompileResult(emission.ok, skipped="not compiled: the "
                                       "graph has errors of its own")
    return emission, compile_check(emission, graph)
