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

PACKAGE = "vexgraph"
ENTRY = "vexgraph_houdini"


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


def fresh():
    """The Houdini entry point, re-read from disk. Use instead of importing."""
    purge()
    import vexgraph_houdini                                    # noqa: PLC0415

    return vexgraph_houdini
