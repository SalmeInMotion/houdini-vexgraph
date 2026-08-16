"""Pick up edits to VEXgraph without restarting Houdini.

Python imports a module once per session. Every later `import vexgraph_houdini`
hands back the copy already in memory, so editing the source and pressing the
shelf button ran the old code - and the only way to see a change was to restart
Houdini, which during a run of small fixes is most of the cost of making them.

Dropping the modules from `sys.modules` makes the next import read the files
again. The shelf calls this rather than importing directly.

Deliberately tiny and dependency-free, and it must stay that way: this is the
one module reloading cannot reload, because it is what does the reloading. The
shelf file has the same problem - it is read at startup - which is why almost
nothing lives there either.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE = "vexgraph"
ENTRY = "vexgraph_houdini"

ROOT = Path(__file__).resolve().parents[2]
# What counts as "the source changed": the package, the node library, and the
# Houdini glue. Cheap to stat - a few dozen files - and it is what lets every
# door reload without every door paying for it.
WATCHED = ("vexgraph/**/*.py", "nodes/**/*.json", "houdini/python/*.py")

_loaded_stamp: float | None = None


def source_stamp() -> float:
    """The newest modification time across VEXgraph's own files."""
    newest = 0.0
    for pattern in WATCHED:
        for path in ROOT.glob(pattern):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def is_stale() -> bool:
    """Whether the files on disk are newer than what is loaded in memory."""
    return _loaded_stamp is None or source_stamp() > _loaded_stamp


def stale_modules() -> list[str]:
    """Every VEXgraph module currently held in memory."""
    return [name for name in list(sys.modules)
            if name in (PACKAGE, ENTRY, __name__.rsplit(".", 1)[-1])
            or name.startswith(f"{PACKAGE}.")]


def purge() -> list[str]:
    """Forget them, so the next import re-reads the source.

    The open window goes first. Its widgets are instances of classes that are
    about to be replaced, and it holds the module-level reference to itself; if
    it were left open, the next press would build a second window while the
    first went on running code that no longer exists anywhere.
    """
    entry = sys.modules.get(ENTRY)
    if entry is not None:
        closer = getattr(entry, "close_window", None)
        if callable(closer):
            try:
                closer()
            except Exception:            # noqa: BLE001 - teardown, never fatal
                pass

    dropped = [name for name in stale_modules() if name != __name__]
    for name in dropped:
        del sys.modules[name]
    return dropped


def fresh(force: bool = False):
    """The Houdini entry point, re-read from disk if the source moved on.

    Every way into the editor comes through here - the shelf, the panel, and
    the button on the wrangle - so an edit is picked up whichever one you
    reach for. Only when something actually changed, though: purging closes
    the open window, and doing that on every press would be its own small
    punishment.
    """
    global _loaded_stamp

    if force or is_stale():
        purge()
        _loaded_stamp = source_stamp()
    import vexgraph_houdini                                    # noqa: PLC0415

    return vexgraph_houdini
