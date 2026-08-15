"""VEXgraph - build Houdini VEX by wiring nodes together.

The graph is the document; the VEX is output. Nothing here parses VEX back into
a graph, deliberately: the generated code is meant to be read and learned from,
not edited and re-imported.
"""

from pathlib import Path

from .codegen import Emission, generate
from .graph import Graph, Issue, Node
from .nodedefs import NodeDef, Registry, default_registry

__all__ = ["Emission", "Graph", "Issue", "Node", "NodeDef", "Registry",
           "build_stamp", "default_registry", "generate", "version_line"]

__version__ = "0.2.0"


def build_stamp() -> str:
    """When the newest source file here was last written, as YYYY-MM-DD HH:MM.

    The version number alone cannot answer "am I running the change that was
    just made" - it only moves when somebody remembers to move it. The source
    timestamp moves on every edit, so together they say both which release this
    is and whether it is the copy you just changed. That matters most right
    after a reload, which is exactly when you cannot otherwise tell.
    """
    import datetime                                          # noqa: PLC0415

    try:
        newest = max(p.stat().st_mtime
                     for p in Path(__file__).parent.rglob("*.py"))
    except (OSError, ValueError):
        return ""
    return datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")


def version_line() -> str:
    """One string for a title bar or a status line."""
    stamp = build_stamp()
    return f"v{__version__}" + (f" · built {stamp}" if stamp else "")
