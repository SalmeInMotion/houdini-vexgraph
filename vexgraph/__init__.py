"""VEXgraph - build Houdini VEX by wiring nodes together.

The graph is the document; the VEX is output. Nothing here parses VEX back into
a graph, deliberately: the generated code is meant to be read and learned from,
not edited and re-imported.
"""

from .codegen import Emission, generate
from .graph import Graph, Issue, Node
from .nodedefs import NodeDef, Registry, default_registry

__all__ = ["Emission", "Graph", "Issue", "Node", "NodeDef", "Registry",
           "default_registry", "generate"]

__version__ = "0.1.0"
