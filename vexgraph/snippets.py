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
import time
from dataclasses import dataclass
from pathlib import Path

# OD ships two stores, in two formats. The pair of .json files is what its
# Snippets Manager actually reads - shipped content plus the user's own - and
# they are the better source anyway: a description, an author and a category
# per entry.
#
# The .txt is read here for its 177 entries, but nothing in OD reads it any
# more: its modules contain no reference to the filename, only to a category
# that happens to be spelled the same way. An earlier note here claimed the
# .txt drove OD's parameter menus, which is not true and sent the search for
# "where do OD's snippets come from" to the wrong file.
OD_ROOT = Path(r"P:\Resources\Add-ons\Houdini\ODHoudiniShelfTools2021")
OD_DEFAULTS = (
    OD_ROOT / "python_panels" / "shippedSnippets.json",
    OD_ROOT / "python_panels" / "snippets.json",     # the user's own additions
    OD_ROOT / "VEXpressions.txt",
)
ENV_PATH = "VEXGRAPH_SNIPPETS"
USER_FILES = ("vexgraph_snippets.txt", "vexgraph_snippets.json")

# Where a graph you save becomes a snippet. In your home directory rather than
# in the project: these are yours, they should survive updating or re-cloning
# VEXgraph, and the repository is public.
USER_STORE = Path.home() / "vexgraph_snippets.json"

# The category your own saved graphs appear under, in both tools.
USER_GROUP = "VEXgraph"

# OD's Snippets Manager keeps the user's own additions here, next to the panel
# script - `dirname(scriptPath) + "snippets.json"` in its own code. Writing a
# copy there is what makes a graph saved in VEXgraph turn up in the menu you
# get from an Attribute Wrangle, so the two sets can be used interchangeably.
#
# Only ever this file: `shippedSnippets.json` is OD's own content and is never
# touched.
OD_USER_STORE = OD_ROOT / "python_panels" / "snippets.json"

# If OD has been pointed at a different store, this file says where. Its format
# is not documented anywhere we can read, so its mere presence is taken as
# "somebody has configured this deliberately" and OD's store is left alone.
OD_CONFIG = OD_ROOT / "python_panels" / "snippets.cfg"

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
        # Snippets travel through web pages and word processors before they
        # get here, picking up smart quotes and stray byte-order marks - and
        # not only at the start of the file: a BOM pasted mid-line breaks the
        # lexer at "line 27". vcc accepts none of these, so a snippet is
        # normalised the way its author meant it, not the way a clipboard
        # mangled it.
        text = (text.replace("\ufeff", "")
                    .replace("“", '"').replace("”", '"')
                    .replace("„", '"')
                    .replace("‘", "'").replace("’", "'"))
        reader = parse_json if path.suffix.lower() == ".json" else parse
        for snippet in reader(text, source=path.name):
            key = (snippet.name.lower(), snippet.code)
            if key in seen:
                continue        # the two stores overlap
            seen.add(key)
            out.append(snippet)
    return out


# Houdini's own snippet menu - the one you get from the Attribute Wrangle - is
# built from every `VEXpressions.txt` found on HOUDINI_PATH. It is a Houdini
# feature, not an OD one: Houdini ships its own at $HFS/houdini, and OD puts
# theirs at the root of their HOUDINI_PATH entry, which is why theirs appear
# there and ours did not.
#
# Ours goes at the root of *our* HOUDINI_PATH entry, for the same reason. It is
# generated, and it contains OD's snippets as read from this machine, so it is
# git-ignored: the repository is public and that content is licensed.
VEXPRESSIONS_EXPORT = Path(__file__).resolve().parents[1] / "houdini" / "VEXpressions.txt"

# The context every wrangle snippet is also filed under. A snippet written for
# a Point Wrangle is keyed `pointwrangle/snippet`, so Houdini will not offer it
# on an `attribwrangle` - which is the node this tool creates and the one most
# people actually use. Listing both keys is what puts all of them in the menu
# you are looking at, and is exactly what the format's "multiple keys" is for.
GENERAL_CONTEXT = "attribwrangle/snippet"

_INDENT = " " * 4


def write_vexpressions(entries: list[Snippet] | None = None,
                       path: Path | None = None) -> tuple[Path, int]:
    """Write our snippets into Houdini's own snippet menu format.

    Returns the file written and how many entries went into it.

    Only wrangle snippets: the menu is per parameter, and a POP force
    expression offered on an Attribute Wrangle is a snippet that cannot work
    where it is being offered.
    """
    entries = load() if entries is None else entries
    target = Path(path) if path is not None else VEXPRESSIONS_EXPORT

    lines = [
        "# Generated by VEXgraph - do not edit; it is rewritten on export.",
        "#",
        "# Everything VEXgraph can see, in Houdini's own VEXpressions format,",
        "# so the same snippets are in the wrangle's menu as in the tool.",
        "",
    ]
    written = 0
    seen: set[tuple[str, str]] = set()
    for snippet in entries:
        if not snippet.is_wrangle:
            continue
        name = " ".join(snippet.name.split())
        code = snippet.code.strip("\n")
        if not name or not code.strip():
            continue
        key = (snippet.context, name)
        if key in seen:
            continue
        seen.add(key)

        keys = [snippet.context]
        if snippet.context != GENERAL_CONTEXT:
            keys.append(GENERAL_CONTEXT)
        lines += keys
        lines.append(f"{_INDENT}{name}")
        # Every non-empty line indented to the name's level, so nothing in the
        # code can ever start at column 0 and be mistaken for the next key.
        lines += [f"{_INDENT}{line.rstrip()}" if line.strip() else ""
                  for line in code.splitlines()]
        lines.append("")
        written += 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf8")
    return target, written


def od_writable_store() -> Path | None:
    """OD's user snippet file, when writing to it is both possible and safe.

    None when OD is not installed, when its folder cannot be written, or when a
    `snippets.cfg` says it has been pointed somewhere else - guessing wrong
    there would write a file nothing reads while looking like it worked.
    """
    folder = OD_USER_STORE.parent
    if not folder.is_dir() or OD_CONFIG.exists():
        return None
    return OD_USER_STORE if os.access(folder, os.W_OK) else None


def _write_store(store: Path, name: str, entry: dict) -> None:
    """Merge one entry into a store, replacing any earlier one of that name.

    Read-modify-write rather than rewrite: OD's file is shared with OD's own
    manager, and anything already in it belongs to somebody else. Saving the
    same name twice replaces it, because an expression you are still working on
    gets saved repeatedly and six numbered copies is not what save means.
    """
    try:
        existing = json.loads(store.read_text(encoding="utf8"))
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, json.JSONDecodeError, ValueError):
        existing = {}

    for key, previous in list(existing.items()):
        if isinstance(previous, dict) and (previous.get("name") or "") == name:
            del existing[key]

    # A numeric key in OD's own style. Its manager reads the values, not the
    # keys, but a file that looks like the one it wrote is one less thing to be
    # surprised by.
    existing[str(int(time.time() * 1000))] = entry
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(existing, indent=1), encoding="utf8")


def save_user_snippet(name: str, code: str, description: str = "",
                      author: str = "VEXgraph",
                      path: Path | None = None,
                      also_od: bool = True) -> list[Path]:
    """Keep a graph's VEX as a snippet, under a name you chose.

    Written twice when OD is installed: once into VEXgraph's own store, and
    once into OD's, so the same snippet is there whether you go looking for it
    from this tool or from an Attribute Wrangle. Ours is the copy that matters
    - it survives OD being uninstalled, or its drive being offline.

    Returns every file actually written.
    """
    entry = {
        "name": name,
        "type": USER_GROUP,
        "snippet": base64.b64encode(code.encode("utf8")).decode("ascii"),
        "description": description,
        "author": author,
    }

    written: list[Path] = []
    targets = [Path(path) if path is not None else USER_STORE]
    if path is None and also_od:
        od = od_writable_store()
        if od is not None:
            targets.append(od)

    for store in targets:
        try:
            _write_store(store, name, entry)
        except OSError:
            # A network share that went away should not lose you the snippet
            # you saved locally a moment ago.
            continue
        written.append(store)
    return written


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
