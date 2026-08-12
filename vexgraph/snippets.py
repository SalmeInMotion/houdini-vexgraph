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

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# OD ships two stores, in two formats. The .txt drives the parameter menus; the
# .json is the Snippets panel, and is both larger and better labelled - it
# carries a description, an author and a category per entry.
OD_ROOT = Path(r"P:\Resources\Add-ons\Houdini\ODHoudiniShelfTools2021")
OD_DEFAULTS = (
    OD_ROOT / "python_panels" / "shippedSnippets.json",
    OD_ROOT / "python_panels" / "snippets.json",     # the user's own additions
    OD_ROOT / "VEXpressions.txt",
)
ENV_PATH = "VEXGRAPH_SNIPPETS"
USER_FILES = ("vexgraph_snippets.txt", "vexgraph_snippets.json")

# Categories in the JSON store that are not VEX. Links are bookmarks - 563 of
# them - and would bury the snippets that can actually become nodes.
NON_VEX_TYPES = {"Links", "Notes", "Python Nodes", "Python Tools"}

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
    description: str = ""
    author: str = ""
    group: str = ""         # the JSON store's own category, when it has one

    @property
    def node_type(self) -> str:
        return self.context.split("/")[0]

    @property
    def category(self) -> str:
        """Grouping for the list.

        The JSON store already sorts its entries sensibly ("Point Wrangles"),
        so that is used as-is; the .txt store has only a context to go on.
        """
        if self.group:
            return self.group
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
        found += list(OD_DEFAULTS)
    found += [Path.home() / name for name in USER_FILES]
    return [p for p in found if p.is_file()]


def load() -> list[Snippet]:
    """Every snippet from every store, with duplicates by name removed."""
    out: list[Snippet] = []
    seen: set[tuple[str, str]] = set()
    for path in search_paths():
        try:
            text = path.read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        reader = parse_json if path.suffix.lower() == ".json" else parse
        for snippet in reader(text, source=path.name):
            key = (snippet.name.lower(), snippet.code)
            if key in seen:
                continue        # the two stores overlap
            seen.add(key)
            out.append(snippet)
    return out


def parse_json(text: str, source: str = "") -> list[Snippet]:
    """OD's Snippets panel store: one object per snippet, code in base64.

    Anything that fails to decode is skipped rather than shown as mojibake -
    this is a live file people add to from inside Houdini.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []

    out: list[Snippet] = []
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        kind = (entry.get("type") or "").strip()
        if kind in NON_VEX_TYPES:
            continue            # bookmarks and Python, not VEX
        raw = entry.get("snippet") or ""
        try:
            code = base64.b64decode(raw).decode("utf8", "replace").strip()
        except (binascii.Error, ValueError):
            continue
        name = (entry.get("name") or "").strip()
        if not name or not code:
            continue
        out.append(Snippet(
            name=name, code=code,
            context=_context_for(kind), source=source,
            description=(entry.get("description") or "").strip(),
            author=(entry.get("author") or "").strip(),
            group=kind or "Snippets"))
    return sorted(out, key=lambda s: (s.group, s.name.lower()))


def _context_for(kind: str) -> str:
    """Map the JSON store's category onto the node type it is written for."""
    word = kind.lower().replace(" wrangles", "").replace(" ", "")
    known = {"point": "pointwrangle", "primitive": "primitivewrangle",
             "detail": "detailwrangle", "vertex": "vertexwrangle",
             "volume": "volumewrangle", "pop": "poppwrangle",
             "rig": "deformationwrangle", "gasfield": "volumewrangle",
             "vexpressions": "attribexpression"}
    return f"{known.get(word, 'attribwrangle')}/snippet"


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
