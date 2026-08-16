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


def test_the_wrangle_carries_a_button_back_to_the_editor(geo):
    """The way back must be on the node, not only on the shelf.

    You are already looking at the wrangle; going to find a shelf tool to
    reopen a window you closed is the wrong shape of errand.
    """
    node = vh.create_wrangle({})
    button = node.parm(vh.OPEN_PARM)
    assert button is not None, "a new wrangle should carry the button"

    template = button.parmTemplate()
    assert isinstance(template, hou.ButtonParmTemplate)
    assert template.label() == "Edit in VEXgraph"
    assert template.scriptCallbackLanguage() == hou.scriptLanguage.Python
    # It must open the node it is on, not whatever happens to be selected.
    assert 'kwargs["node"]' in template.scriptCallback()

    # First in the parameter pane, where it can be found without scrolling.
    assert node.parmTemplateGroup().entries()[0].name() == vh.OPEN_PARM


def test_the_button_is_added_once_and_to_older_nodes_too(geo):
    """A wrangle made before the button existed gains it on attach."""
    plain = geo.createNode("attribwrangle")
    assert plain.parm(vh.OPEN_PARM) is None

    assert vh.add_open_button(plain) is True
    assert plain.parm(vh.OPEN_PARM) is not None
    # Asking twice is not an error, and must not stack a second button.
    assert vh.add_open_button(plain) is False
    names = [e.name() for e in plain.parmTemplateGroup().entries()]
    assert names.count(vh.OPEN_PARM) == 1

    assert vh.add_open_button(None) is False


def test_the_button_does_not_disturb_the_wrangles_own_parameters(geo):
    """Adding a spare parm must not cost the node anything it already had.

    Compared by parameter name rather than by the top-level entries: rebuilding
    the template group renumbers Houdini's own auto-named folders (`folder0`
    becomes `folder1`), which looks like a difference and is not one - nothing
    references a folder by name.
    """
    reference = geo.createNode("attribwrangle")
    before = {p.name() for p in reference.parms()}

    node = vh.create_wrangle({})
    after = {p.name() for p in node.parms()}
    # Nothing lost is the invariant. Not exact equality: Houdini renumbers its
    # own auto-named folder parms when the group is rebuilt, so the new node
    # carries an extra folder bookkeeping name that means nothing to anyone.
    assert not before - after, f"lost {sorted(before - after)}"
    assert vh.OPEN_PARM in after

    # The two the tool actually writes to, named explicitly - losing either is
    # the failure that would matter.
    assert node.parm("snippet") is not None
    assert node.parm("class") is not None


def test_the_shelf_button_re_reads_the_source_without_a_restart(geo):
    """Iterating on this tool should not cost a Houdini restart each time.

    Python caches modules, so pressing the button after editing ran the old
    code. The reloader drops them; the next import reads the files again.
    """
    global vh                                              # noqa: PLW0603
    import vexgraph_reload

    import vexgraph                                        # noqa: PLC0415

    before = id(vh)
    assert "vexgraph.nodedefs" in sys.modules, "the package should be loaded"

    dropped = vexgraph_reload.purge()
    assert "vexgraph_houdini" in dropped
    assert any(n.startswith("vexgraph.") for n in dropped), \
        "the package must go too, or edits to it are still cached"
    assert "vexgraph_houdini" not in sys.modules
    # The reloader itself must survive: it is what does the reloading.
    assert "vexgraph_reload" in sys.modules

    fresh = vexgraph_reload.fresh()
    assert id(fresh) != before, "it should be a newly imported module"
    assert fresh.OPEN_PARM == "vexgraph_open"
    assert callable(fresh.open_window)

    # Leave the rest of the suite pointing at the module that is now current.
    vh = fresh
    assert vexgraph is not None


def test_closing_the_window_is_safe_to_call_with_none_open(geo):
    """purge() calls this before dropping the module; it must never raise."""
    vh.close_window()
    vh.close_window()


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



def test_every_exercise_scene_builds_and_cooks(geo):
    """Each Learn exercise can build its own geometry, and the result cooks.

    A recipe that names a SOP wrongly, or wires Copy to Points backwards
    (the thing to copy goes in input 0, the points in input 1), must fail
    here - not in front of someone on their second exercise.
    """
    from vexgraph import learn                                # noqa: PLC0415

    for exercise in learn.BEGINNER:
        scene = exercise.scene
        assert scene is not None, exercise.key
        wrangle = geo.createNode("attribwrangle", f"w_{exercise.key}")
        wrangle.parm("snippet").set(exercise.solution)
        made = vh.build_scene_nodes(geo, wrangle, scene)
        assert made, exercise.key

        last = made[-1]
        geometry = last.geometry()          # raises if anything fails to cook
        assert len(geometry.points()) > 0, f"{exercise.key} produced no points"
        # The wrangle really is fed by the scene, not left dangling.
        assert wrangle.inputs() and wrangle.inputs()[0] is not None, exercise.key


def test_the_falloff_scene_copies_onto_the_wrangled_points(geo):
    """Copy to Points wired the wrong way round still cooks - and produces
    the wrong thing. This pins the order down."""
    from vexgraph import learn                                # noqa: PLC0415

    exercise = next(e for e in learn.BEGINNER if e.key == "falloff")
    wrangle = geo.createNode("attribwrangle", "w_falloff_order")
    wrangle.parm("snippet").set(exercise.solution)
    made = vh.build_scene_nodes(geo, wrangle, exercise.scene)
    copy = made[-1]
    assert copy.type().name().startswith("copytopoints")
    # input 1 is the point cloud: it must be the wrangle, not the sphere.
    # By path, not by identity: HOM hands back a fresh wrapper each call, so
    # `is` compares two different Python objects for the same node.
    assert copy.inputs()[1].path() == wrangle.path()
    # 20x20 grid points, each with a sphere copied onto it.
    assert len(copy.geometry().points()) > len(wrangle.geometry().points())



def test_every_door_picks_up_an_edit_without_restarting(geo):
    """The shelf reloaded; the panel and the node's button imported directly
    and quietly ran whatever was loaded when Houdini started. Now all three
    come through the reloader - and only pay for it when something changed.
    """
    import time

    import vexgraph_reload as reloader

    reloader.fresh(force=True)
    assert not reloader.is_stale(), "just loaded, nothing to pick up"

    (ROOT / "vexgraph" / "learn.py").touch()
    time.sleep(0.02)
    assert reloader.is_stale(), "an edited file must be noticed"

    dropped = reloader.purge()
    assert any(name.startswith("vexgraph") for name in dropped)
    reloader.fresh()
    assert not reloader.is_stale()

    # Both entry points reach the module through the reloader, not by import.
    panel = (ROOT / "houdini" / "python_panels" / "vexgraph.pypanel").read_text(
        encoding="utf8")
    assert "vexgraph_reload.fresh()" in panel
    assert "vexgraph_reload" in vh.OPEN_CALLBACK


def test_an_old_wrangles_button_is_upgraded(geo):
    """The callback lives in the .hip, so a scene saved before it changed
    would keep running the stale one for ever."""
    node = geo.createNode("attribwrangle", "old_button")
    template = hou.ButtonParmTemplate(
        vh.OPEN_PARM, "Edit in VEXgraph",
        script_callback="import vexgraph_houdini",
        script_callback_language=hou.scriptLanguage.Python)
    group = node.parmTemplateGroup()
    group.insertBefore(group.entries()[0], template)
    node.setParmTemplateGroup(group)

    assert vh.add_open_button(node), "an out-of-date button must be replaced"
    current = node.parm(vh.OPEN_PARM).parmTemplate().scriptCallback()
    assert "vexgraph_reload" in current
    assert not vh.add_open_button(node), "a current button is left alone"



def test_a_hand_edited_wrangle_is_noticed(geo):
    """Somebody typing into the wrangle directly is the normal case, not a
    mistake - and their VEX must not be silently replaced by whatever graph
    VEXgraph stored last time it was open."""
    registry = default_registry()
    report = import_vex("@Cd = set(1, 0, 0);", registry)
    node = vh.create_wrangle({})
    vh.apply_to_node(node, report.graph, generate(report.graph).code)

    stored = vh.read_graph(node, registry)
    assert not vh.wrangle_was_edited(node, stored), "nothing was touched yet"

    node.parm("snippet").set("@P.y = 10;")
    assert vh.wrangle_was_edited(node, stored), "a hand edit must be seen"

    # Reformatting is not an edit: the comparison is about meaning.
    node.parm("snippet").set(generate(stored).code + "\n\n")
    assert not vh.wrangle_was_edited(node, stored)


def test_the_wrangles_own_vex_can_be_read_back_as_nodes(geo):
    """The "keep what is in the wrangle" answer has to lead somewhere: the
    importer turns that code into the graph the editor then shows."""
    registry = default_registry()
    node = vh.create_wrangle({})
    node.parm("snippet").set("@P.y = 10;\n@Cd = set(0, 1, 0);")

    graph = import_vex(node.parm("snippet").evalAsString(), registry).graph
    types = {n.type for n in graph.nodes.values()}
    assert "attrib_set_component" in types and "attrib_set" in types


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
