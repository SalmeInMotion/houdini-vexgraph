"""The Houdini-side node handling, exercised inside a real Houdini.

Houdini's bundled Python has no pytest, so this runs itself:

    hython tests/test_houdini_node.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import hou
except ModuleNotFoundError:      # collected by the ordinary suite, which has no Houdini
    import pytest

    pytest.skip("needs Houdini; run with hython", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "houdini" / "python"))

import vexgraph_houdini as vh                                  # noqa: E402
from vexgraph import default_registry, generate                # noqa: E402
from vexgraph.parser import import_vex                         # noqa: E402


def test_the_tab_menu_makes_a_real_attribute_wrangle(geo):
    """Not a custom type and not a subnet: an ordinary wrangle.

    Anything wrapping the wrangle resolves ch() against the inner node, so a
    spare parameter the VEX names would be looked for in the wrong place.
    """
    box = geo.createNode("box")
    box.setSelected(True, clear_all_selected=True)
    node = vh.create_wrangle({})
    assert node.type().name() == "attribwrangle"
    assert node.input(0) is not None, "it should wire to what was selected"
    assert node.input(0).path() == box.path()


def test_channel_references_resolve_on_the_node_itself(geo):
    """The regression the subnet asset had: chf() found nothing and read 0."""
    box = geo.createNode("box")
    box.setSelected(True, clear_all_selected=True)
    node = vh.create_wrangle({})
    node.parm("class").set(2)
    node.parm("snippet").set('@pscale = chf("mysize");')

    group = node.parmTemplateGroup()
    group.append(hou.FloatParmTemplate("mysize", "My Size", 1,
                                       default_value=(3.0,)))
    node.setParmTemplateGroup(group)
    node.parm("mysize").set(3.0)

    pscale = node.geometry().iterPoints()[0].attribValue("pscale")
    assert abs(pscale - 3.0) < 1e-6, f"chf() read {pscale}, not the parm's 3.0"


def test_a_graph_applied_to_it_cooks_that_geometry(geo):
    box = geo.createNode("box")
    box.setSelected(True, clear_all_selected=True)
    node = vh.create_wrangle({})
    node.parm("class").set(2)

    registry = default_registry()
    report = import_vex("@Cd = set(0, 1, 0);", registry)
    vh.apply_to_node(node, report.graph, generate(report.graph).code)

    assert node.geometry().iterPoints()[0].attribValue("Cd") == (0.0, 1.0, 0.0)
    assert vh.read_graph(node, registry) is not None


def test_any_node_with_a_snippet_parm_is_a_valid_target(geo):
    assert vh.is_wrangle(geo.createNode("attribwrangle"))
    assert vh.is_wrangle(geo.createNode("volumewrangle"))
    assert not vh.is_wrangle(geo.createNode("box"))
    assert not vh.is_wrangle(None)





def test_the_panel_docks_into_the_largest_leaf_pane(geo):
    """Regression: hou.Pane has no size(); assuming it did broke every launch.

    hou.ui does not exist in hython, so the pane choice is exercised with
    stand-ins. What this pins down is the logic - skip splits, pick the largest
    - while the method names are checked against the real class below.
    """
    class Rect:
        def __init__(self, w, h):
            self._w, self._h = w, h

        def width(self):
            return self._w

        def height(self):
            return self._h

    class FakePane:
        def __init__(self, name, w, h, split=False):
            self.name, self._rect, self._split = name, Rect(w, h), split

        def isSplit(self):
            return self._split

        def qtScreenGeometry(self):
            return self._rect

    class FakeDesktop:
        def __init__(self, panes):
            self._panes = panes

        def panes(self):
            return self._panes

    biggest = vh._biggest_pane(FakeDesktop([
        FakePane("small", 100, 100),
        FakePane("huge_but_a_split", 4000, 4000, split=True),
        FakePane("big", 900, 800),
    ]))
    assert biggest.name == "big", "a split pane cannot hold a tab"

    # The methods used must exist on the real class, not just the stand-ins.
    for method in ("isSplit", "qtScreenGeometry", "createTab"):
        assert hasattr(hou.Pane, method), f"hou.Pane has no {method}()"
    assert not hasattr(hou.Pane, "size"), \
        "hou.Pane grew a size() method; this test is no longer meaningful"


def test_no_pane_to_dock_into_is_reported_not_guessed(geo):
    class FakeDesktop:
        def panes(self):
            return []

    try:
        vh._biggest_pane(FakeDesktop())
    except IndexError:
        return          # the caller catches this and opens a window instead
    raise AssertionError("an empty desktop should not silently return a pane")


def _run() -> int:
    checks = [(n, f) for n, f in sorted(globals().items())
              if n.startswith("test_") and callable(f)]
    failures = 0
    for name, check in checks:
        node = hou.node("/obj").createNode("geo", "vexgraph_test")
        try:
            check(node)
            print(f"  ok    {name}")
        except Exception as exc:
            failures += 1
            print(f"  FAIL  {name}: {exc}")
        finally:
            node.destroy()
    print(f"\n{len(checks) - failures}/{len(checks)} passed in Houdini "
          f"{hou.applicationVersionString()}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
