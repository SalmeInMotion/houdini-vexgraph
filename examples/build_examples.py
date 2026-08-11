"""Build the example graphs, and prove each one still generates working VEX.

These double as documentation and as end-to-end tests: they are assembled with
the same calls the editor will make, so if the API drifts they stop building.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import Graph, default_registry  # noqa: E402
from vexgraph.vccmap import build  # noqa: E402

HERE = Path(__file__).parent


def stick_to_surface(registry) -> Graph:
    """Move every point onto the closest spot of the second input.

    The graph a lookdev artist actually wants and would otherwise have to ask
    someone to write: xyzdist plus primuv, which is three lines of VEX nobody
    remembers the argument order of.
    """
    g = Graph(registry)
    g.add("start", "start")
    g.add("closest_surface_point", "closest", input="1")
    g.add("position_on_primitive", "on_surface", input="1", attrib="P",
          type="vector")
    g.add("attrib_set", "write", attrib="P", type="vector")
    g.chain("start", "write")

    g.connect("closest", "primitive", "on_surface", "primitive")
    g.connect("closest", "uv", "on_surface", "uv")
    g.connect("on_surface", "value", "write", "value")
    return g


def colour_by_proximity(registry) -> Graph:
    """Colour points red near a second input and blue far from it.

    Exercises the parts that matter: a value used by two different branches,
    a remap, and an If.
    """
    g = Graph(registry)
    g.add("start", "start")
    g.add("closest_surface_point", "closest", input="1")
    g.add("fit_range", "falloff", old_min="0", old_max="2",
          new_min="1", new_max="0")
    g.add("make_vector", "colour", y="0", z="0")
    g.add("attrib_set", "write", attrib="Cd", type="vector")
    g.add("is_greater", "too_far", b="2")
    g.add("if", "cull")
    g.add("remove_point", "kill")

    g.chain("start", "write", "cull")
    g.connect("cull", "then", "kill", "exec", is_exec=True)
    g.connect("closest", "distance", "falloff", "value")
    g.connect("falloff", "result", "colour", "x")
    g.connect("colour", "result", "write", "value")
    g.connect("closest", "distance", "too_far", "a")
    g.connect("too_far", "result", "cull", "condition")
    return g


def average_neighbour_colour(registry) -> Graph:
    """Blur colour by averaging the neighbours found within a radius.

    The one that justifies the whole exec-pin design: a running total that has
    to be made before a loop, added to inside it, and read after.
    """
    g = Graph(registry)
    g.add("start", "start")
    g.add("nearest_points", "neighbours", input="0", radius="0.5", maximum="8")
    g.add("var_make", "total", name="total", type="vector",
          value="{0, 0, 0}")
    g.add("foreach", "each", type="int")
    g.add("read_point_attribute", "their_colour", input="0", attrib="Cd",
          type="vector")
    g.add("var_get", "running", name="total", type="vector")
    g.add("add", "accumulate", type="vector")
    g.add("var_set", "store", name="total", type="vector")
    g.add("var_get", "final", name="total", type="vector")
    g.add("list_length", "how_many", type="int")
    g.add("divide", "average", type="vector")
    g.add("attrib_set", "write", attrib="Cd", type="vector")

    g.chain("start", "total", "each", "write")
    g.connect("each", "body", "store", "exec", is_exec=True)

    g.connect("neighbours", "point_numbers", "each", "items")
    g.connect("each", "item", "their_colour", "point_number")
    g.connect("running", "value", "accumulate", "a")
    g.connect("their_colour", "value", "accumulate", "b")
    g.connect("accumulate", "result", "store", "value")

    g.connect("neighbours", "point_numbers", "how_many", "items")
    g.connect("final", "value", "average", "a")
    g.connect("how_many", "count", "average", "b")
    g.connect("average", "result", "write", "value")
    return g


EXAMPLES = {
    "stick_to_surface": stick_to_surface,
    "colour_by_proximity": colour_by_proximity,
    "average_neighbour_colour": average_neighbour_colour,
}


def main() -> int:
    registry = default_registry()
    failed = 0
    for name, make in EXAMPLES.items():
        graph = make(registry)
        emission, compiled = build(graph)
        graph.save(HERE / f"{name}.vexgraph.json")

        status = "compiles" if compiled.ok else "FAILED"
        if not compiled.ok or not emission.ok:
            failed += 1
        print(f"\n=== {name}  [{status}] " + "=" * (40 - len(name)))
        print(emission.code or "(nothing emitted)")
        for issue in emission.issues:
            print(f"  {issue.severity}: {issue}")
        if not compiled.ok:
            print(compiled.raw)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
