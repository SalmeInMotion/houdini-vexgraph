"""Read VEX back into a graph.

Graph → VEX was always the point; VEX → graph is what makes the tool a
translator between the two, which is the thing no other Houdini surface does.
Paste code an AI wrote, see the nodes, understand it, change one, get code back.
"""

from .lower import Report, import_vex
from .syntax import ParseError, parse

__all__ = ["ParseError", "Report", "import_vex", "parse"]
