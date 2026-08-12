"""Ready-made VEX snippets, read from wherever they already live.

The interesting ones on this machine come from Oliver Hotz's OD Houdini Tools
Shelf, whose licence forbids redistribution. So nothing is copied here: the
file is read from the local install at runtime, exactly as the Houdini icons
and help are. Anyone cloning this repository gets the reader; the snippets come
with the tools they already installed.

A snippet opens through the VEX parser rather than being pasted as text, which
is the point: a preset arrives as *nodes*, so it can be read and changed rather
than trusted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Where OD's file usually sits, and the env var that overrides it. A user file
# is also read, so people can keep their own without editing anyone's install.
OD_DEFAULT = Path(r"P:\Resources\Add-ons\Houdini\ODHoudiniShelfTools2021"
                  r"\VEXpressions.txt")
ENV_PATH = "VEXGRAPH_SNIPPETS"
USER_FILE = "vexgraph_snippets.txt"

# The Houdini contexts whose code is a wrangle snippet. A POP force expression
# is VEX too, but it runs with different bindings and is not what this tool
# builds, so those are labelled rather than hidden - they still read fine.
WRANGLE_CONTEXTS = {
    "attribwrangle", "pointwrangle", "primitivewrangle", "vertexwrangle",
    "detailwrangle", "volumewrangle", "deformationwrangle", "channelwrangle",
    "attribexpression", "groupexpression",
}


@dataclass(frozen=True)
class Snippet:
    name: str
    code: str
    context: str            # "attribwrangle/snippet" as written in the file
    source: str = ""        # which file it came from, for the tooltip

    @property
    def node_type(self) -> str:
        return self.context.split("/")[0]

    @property
    def category(self) -> str:
        """Grouping for the list: the wrangles together, the rest by context."""
        node = self.node_type
        if node in WRANGLE_CONTEXTS:
            return "Wrangles"
        if node.startswith("pop"):
            return "POP / particles"
        return "Other contexts"

    @property
    def is_wrangle(self) -> bool:
        return self.node_type in WRANGLE_CONTEXTS


def search_paths() -> list[Path]:
    """Every file to read, in order. Missing ones are simply skipped."""
    found: list[Path] = []
    override = os.environ.get(ENV_PATH)
    if override:
        found += [Path(p) for p in override.split(os.pathsep) if p]
    else:
        found.append(OD_DEFAULT)
    found.append(Path.home() / USER_FILE)
    return [p for p in found if p.is_file()]


def load() -> list[Snippet]:
    out: list[Snippet] = []
    for path in search_paths():
        try:
            text = path.read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        out += parse(text, source=path.name)
    return out


_HEADER_RE = re.compile(r"^(\w+/\w+)\s*$")


def parse(text: str, source: str = "") -> list[Snippet]:
    """Read the format OD uses: a context line, a title, then indented code.

    Tolerant on purpose. This is someone else's file that gains entries over
    time, so anything that does not fit the shape is skipped rather than
    treated as an error.
    """
    lines = text.splitlines()
    out: list[Snippet] = []
    index = 0
    while index < len(lines):
        header = _HEADER_RE.match(lines[index])
        if not header or lines[index].startswith("#"):
            index += 1
            continue

        title = lines[index + 1].strip() if index + 1 < len(lines) else ""
        body: list[str] = []
        cursor = index + 2
        while cursor < len(lines):
            line = lines[cursor]
            if line.strip() and not line.startswith((" ", "\t")):
                break                       # the next entry's context line
            body.append(line)
            cursor += 1

        code = _dedent("\n".join(body).strip("\n"))
        if title and code:
            out.append(Snippet(name=title.rstrip(":"), code=code,
                               context=header.group(1), source=source))
        index = cursor if cursor > index else index + 1
    return out


def _dedent(code: str) -> str:
    """Drop the common leading indent the file format adds."""
    lines = [line for line in code.splitlines() if line.strip()]
    if not lines:
        return code
    indent = min(len(line) - len(line.lstrip()) for line in lines)
    return "\n".join(line[indent:] if len(line) >= indent else line
                     for line in code.splitlines()).strip("\n")
