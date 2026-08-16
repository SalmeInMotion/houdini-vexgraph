"""The nodes this person actually reaches for.

A registry of 1360 nodes is fair to the language and unfair to the hands:
in practice everyone uses the same two dozen. Starring those puts them at
the top of every search and gives the library a shelf of its own, without
anyone having to agree on which two dozen they are.

Kept beside the user's own functions, in their home directory - it is a
preference, not part of any project.
"""

from __future__ import annotations

import json
from pathlib import Path

STORE = Path.home() / ".vexgraph_favourites.json"


def all_types() -> set[str]:
    try:
        return set(json.loads(STORE.read_text(encoding="utf8")))
    except (OSError, ValueError):
        return set()


def is_favourite(node_type: str) -> bool:
    return node_type in all_types()


def _write(types: set[str]) -> None:
    STORE.write_text(json.dumps(sorted(types), indent=1), encoding="utf8")


def toggle(node_type: str) -> bool:
    """Star or unstar; returns whether it is starred afterwards."""
    types = all_types()
    if node_type in types:
        types.discard(node_type)
        starred = False
    else:
        types.add(node_type)
        starred = True
    _write(types)
    return starred


def remove(node_type: str) -> None:
    types = all_types()
    if node_type in types:
        types.discard(node_type)
        _write(types)
