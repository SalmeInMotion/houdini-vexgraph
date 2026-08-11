"""Build node definitions for the whole VEX function set, from Houdini itself.

Nobody is going to hand-write a node for every VEX function, and a hand-written
list would be wrong the moment Houdini ships a new build. Two sources, each used
for what it is actually authoritative about:

    vcc -X <context>    which functions exist, and their exact signatures. The
                        compiler cannot be out of date with itself.
    help/vex.zip        what each one is for, and what its arguments are called.
                        vcc reports types but no names, and `nearpoint(a, b, c)`
                        is not a node anyone can use.

Where the two disagree, vcc wins: the docs describe some overloads loosely and
omit others, but a signature that will not compile is worthless.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import vextypes

SIGNATURE_RE = re.compile(r"^\s+(?P<ret>[\w\[\]]+)\s+(?P<name>\w+)\(\s*(?P<args>.*?)\s*\)\s*$")
USAGE_RE = re.compile(r":usage:\s*`(?P<usage>[^`]+)`")
SUMMARY_RE = re.compile(r'"""(?P<summary>.*?)"""', re.DOTALL)
TAG_RE = re.compile(r"^#(?P<key>tags|group):\s*(?P<value>.+)$", re.MULTILINE)
CALL_RE = re.compile(r"(?P<name>\w+)\s*\((?P<args>.*)\)\s*$")

# Functions that change the scene or the arguments handed to them, rather than
# just returning an answer. They get exec pins, because when they run matters.
SIDE_EFFECT_PREFIXES = (
    "set", "add", "remove", "insert", "append", "push", "pop", "resize",
    "print", "warning", "error", "assert", "write",
)

# Not useful in a palette: compiler internals, and anything whose arity is not
# fixed, which cannot become a node with a known number of sockets.
def _is_internal(name: str) -> bool:
    return name.startswith("_") or name.startswith("__")


@dataclass
class Signature:
    name: str
    returns: str
    args: tuple[str, ...]           # VEX types, in order
    refs: tuple[bool, ...]          # which of them are outputs

    @property
    def arity(self) -> int:
        return len(self.args)


@dataclass
class DocEntry:
    summary: str = ""
    group: str = ""
    tags: tuple[str, ...] = ()
    # arity -> parameter names, taken from the first :usage: line of that length
    names: dict[int, tuple[str, ...]] = field(default_factory=dict)


# ------------------------------------------------------------------- sources

def read_signatures(vcc: Path, context: str = "cvex") -> list[Signature]:
    proc = subprocess.run([str(vcc), "-X", context], capture_output=True,
                          text=True, timeout=120)
    out: list[Signature] = []
    for line in (proc.stdout or "").splitlines():
        match = SIGNATURE_RE.match(line)
        if not match:
            continue
        raw_args = [a.strip() for a in match.group("args").split(";")]
        raw_args = [a for a in raw_args if a and a != "void"]
        if any(a == "..." for a in raw_args):
            continue                      # variadic: no fixed socket count
        types, refs = [], []
        for arg in raw_args:
            is_ref = arg.endswith("&")
            types.append(arg.rstrip("& ").strip())
            refs.append(is_ref)
        out.append(Signature(match.group("name"), match.group("ret"),
                             tuple(types), tuple(refs)))
    return out


def read_docs(help_zip: Path) -> dict[str, DocEntry]:
    """Parse the shipped VEX help for summaries and argument names."""
    entries: dict[str, DocEntry] = {}
    if not help_zip.is_file():
        return entries
    with zipfile.ZipFile(help_zip) as archive:
        for member in archive.namelist():
            if not member.startswith("functions/") or not member.endswith(".txt"):
                continue
            name = Path(member).stem
            text = archive.read(member).decode("utf-8", "replace")
            entry = DocEntry()
            summary = SUMMARY_RE.search(text)
            if summary:
                entry.summary = " ".join(summary.group("summary").split())
            for match in TAG_RE.finditer(text):
                value = match.group("value").strip()
                if match.group("key") == "group":
                    entry.group = value
                else:
                    entry.tags = tuple(v.strip() for v in value.split(",") if v.strip())
            for match in USAGE_RE.finditer(text):
                names = _argument_names(match.group("usage"), name)
                if names is not None:
                    entry.names.setdefault(len(names), names)
            entries[name] = entry
    return entries


def _argument_names(usage: str, function: str) -> tuple[str, ...] | None:
    """Pull `value, omin, omax` out of `float fit(float value, float omin, ...)`."""
    match = CALL_RE.search(usage.strip())
    if not match or match.group("name") != function:
        return None
    inner = match.group("args").strip()
    if not inner:
        return ()
    names: list[str] = []
    for part in _split_arguments(inner):
        # The name is the last identifier: "const vector &clr" -> "clr".
        identifiers = re.findall(r"[A-Za-z_]\w*", part)
        if not identifiers:
            return None
        names.append(identifiers[-1])
    return tuple(names)


def _split_arguments(text: str) -> list[str]:
    """Split on commas that are not inside brackets, which array types use."""
    parts, depth, current = [], 0, ""
    for char in text:
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------- selection

# When several overloads have the same arity, prefer the everyday one. A node
# labelled `fit` should be the float version, not the matrix2 version.
TYPE_RANK = {
    "float": 0, "vector": 1, "int": 2, "string": 3, "vector4": 5,
    "vector2": 6, "matrix": 7, "matrix3": 8, "matrix2": 9, "dict": 10,
}


def _score(signature: Signature) -> tuple[int, int]:
    types = (signature.returns, *signature.args)
    return (sum(TYPE_RANK.get(vextypes.element_type(t), 4) for t in types),
            len(signature.args))


def _usable(signature: Signature) -> bool:
    if _is_internal(signature.name):
        return False
    if signature.returns != "void" and not vextypes.is_valid(signature.returns):
        return False
    return all(vextypes.is_valid(t) for t in signature.args)


def choose(signatures: list[Signature]
           ) -> tuple[dict[tuple[str, int], Signature], set[tuple[str, int]]]:
    """One signature per function name and argument count.

    Also reports which of those had rivals, since a name with two overloads of
    the same length needs its arguments cast to resolve the call.
    """
    best: dict[tuple[str, int], Signature] = {}
    contested: set[tuple[str, int]] = set()
    for signature in signatures:
        if not _usable(signature):
            continue
        key = (signature.name, signature.arity)
        if key in best:
            contested.add(key)
        if key not in best or _score(signature) < _score(best[key]):
            best[key] = signature
    return best, contested


# ---------------------------------------------------------------- generation

def _socket_names(signature: Signature, docs: DocEntry | None) -> list[str]:
    """Argument names from the docs when they line up, otherwise a, b, c."""
    documented = docs.names.get(signature.arity) if docs else None
    names: list[str] = []
    used: set[str] = set()
    for index in range(signature.arity):
        candidate = documented[index] if documented else ""
        candidate = re.sub(r"\W+", "_", candidate).strip("_").lower()
        if not candidate or candidate[0].isdigit() or candidate in used:
            candidate = chr(ord("a") + index)
        while candidate in used:
            candidate += "_"
        used.add(candidate)
        names.append(candidate)
    if "result" in used:
        names = [f"{n}_" if n == "result" else n for n in names]
    return names


def build_definition(signature: Signature, docs: DocEntry | None,
                     *, node_type: str, disambiguate: bool = False) -> dict:
    names = _socket_names(signature, docs)
    inputs, outputs, body, arguments = [], [], [], []

    for name, vex_type, is_ref in zip(names, signature.args, signature.refs):
        if is_ref:
            outputs.append({"name": name, "type": vex_type,
                            "label": name.replace("_", " ").title()})
            body.append(f"{vextypes.declaration(vex_type, '{%s}' % name)} = "
                        f"{vextypes.zero(vex_type)};")
            arguments.append("{%s}" % name)
            continue

        inputs.append({"name": name, "type": vex_type,
                       "label": name.replace("_", " ").title(),
                       "default": vextypes.zero(vex_type)})
        # Where several overloads share an argument count, VEX cannot tell them
        # apart from values that coerce — `pnoise(0, 0, 0, 0)` matches two. An
        # explicit cast picks one and keeps the node usable.
        if disambiguate and not vextypes.is_array(vex_type):
            arguments.append("%s({%s})" % (vex_type, name))
        else:
            arguments.append("{%s}" % name)

    call = f"{signature.name}({', '.join(arguments)})"
    if signature.returns == "void":
        body.append(f"{call};")
    else:
        outputs.insert(0, {"name": "result", "type": signature.returns,
                           "label": "Result"})
        body.append(
            f"{vextypes.declaration(signature.returns, '{result}')} = {call};")

    side_effect = (signature.returns == "void"
                   or signature.name.startswith(SIDE_EFFECT_PREFIXES))
    return {
        "type": node_type,
        "label": signature.name,
        "kind": "statement" if side_effect else "pure",
        "category": f"VEX / {(docs.group if docs else '') or 'other'}",
        "summary": (docs.summary if docs else "")[:300],
        "tier": 2,
        "tags": list(docs.tags) if docs else [],
        "inputs": inputs,
        "outputs": outputs,
        "code": "\n".join(body),
    }


def generate_library(vcc: Path, help_zip: Path, context: str = "cvex") -> dict:
    signatures = read_signatures(vcc, context)
    docs = read_docs(help_zip)
    chosen, contested = choose(signatures)

    # The arity a function is usually called with gets the plain name; the rest
    # are suffixed, so `vex_fit` is the one people mean.
    arities: dict[str, list[int]] = {}
    for name, arity in chosen:
        arities.setdefault(name, []).append(arity)

    nodes = []
    for name, options in sorted(arities.items()):
        entry = docs.get(name)
        documented = sorted(entry.names) if entry and entry.names else []
        canonical = next((a for a in documented if a in options), min(options))
        for arity in sorted(options):
            node_type = (f"vex_{name}" if arity == canonical
                         else f"vex_{name}_{arity}")
            nodes.append(build_definition(
                chosen[(name, arity)], entry, node_type=node_type,
                disambiguate=(name, arity) in contested))

    return {"generated": True, "context": context, "nodes": nodes}


def find_houdini(explicit: str = "") -> tuple[Path, Path] | None:
    """Locate vcc and the VEX help archive from the newest Houdini install."""
    if explicit:
        root = Path(explicit)
        return root / "bin" / "vcc.exe", root / "houdini" / "help" / "vex.zip"
    base = Path(r"C:\Program Files\Side Effects Software")
    installs = sorted(
        (p for p in base.glob("Houdini *") if (p / "bin" / "vcc.exe").is_file()),
        key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)])
    if not installs:
        return None
    newest = installs[-1]
    return newest / "bin" / "vcc.exe", newest / "houdini" / "help" / "vex.zip"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--houdini", default="", help="Houdini install root")
    parser.add_argument("--context", default="cvex")
    parser.add_argument("--out", default=str(
        Path(__file__).parent.parent / "nodes" / "generated" / "vex.json"))
    args = parser.parse_args()

    found = find_houdini(args.houdini)
    if not found:
        print("No Houdini install found. Pass --houdini.", file=sys.stderr)
        return 1
    vcc, help_zip = found
    if not vcc.is_file():
        print(f"vcc not found at {vcc}", file=sys.stderr)
        return 1

    library = generate_library(vcc, help_zip, args.context)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(library, indent=1), encoding="utf-8")

    documented = sum(1 for n in library["nodes"] if n["summary"])
    print(f"{len(library['nodes'])} nodes -> {out}")
    print(f"{documented} of them carry a description from the Houdini help")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
