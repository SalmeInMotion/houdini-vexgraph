"""The user's own function library, shared across every graph.

A collapsed function is document-local by design - two documents defining
different drawLine() functions must never see each other's. This store is
the deliberate exception: a function the user chose to keep, by name, so
another graph can place a call to it. Placing one copies the function INTO
that document (definition and all), so the emitted VEX stays self-contained
and the library remains what it looks like: a shelf, not a link.

Kept in one JSON file in the home directory, next to the user's snippets.
"""

from __future__ import annotations

import json
from pathlib import Path

from .graph import Graph
from .nodedefs import Registry

STORE = Path.home() / ".vexgraph_functions.json"


def _read() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def names() -> list[str]:
    return sorted(_read())


def summaries() -> dict[str, str]:
    """name -> one-line signature description, for the library tree."""
    out = {}
    for name, raw in _read().items():
        signature = raw.get("signature", {})
        params = ", ".join(f"{p[0]} {p[1]}"
                           for p in signature.get("params", ()))
        out[name] = f"{signature.get('return', 'void')} {name}({params})"
    return out


def save(inner: Graph) -> None:
    assert inner.signature is not None
    store = _read()
    store[inner.signature.name] = inner.to_dict()
    STORE.write_text(json.dumps(store, indent=1), encoding="utf8")


def remove(name: str) -> None:
    store = _read()
    if store.pop(name, None) is not None:
        STORE.write_text(json.dumps(store, indent=1), encoding="utf8")


def load(name: str, registry: Registry) -> Graph | None:
    raw = _read().get(name)
    return Graph.from_dict(raw, registry) if raw else None
