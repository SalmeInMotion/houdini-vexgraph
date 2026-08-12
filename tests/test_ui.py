"""Drive the editor without a screen.

Qt runs offscreen here, so these are real widgets doing real work: nodes get
built from definitions, wires get refused or accepted, and the code pane gets
the same VEX the CLI would produce. It catches the failure that matters most in
UI code - something raising the moment it is drawn - which no amount of looking
at a screenshot reliably does.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402

from vexgraph import Graph, default_registry  # noqa: E402
from vexgraph.ui.canvas import GraphScene, GraphView  # noqa: E402
from vexgraph.ui.items import LinkItem, NodeItem  # noqa: E402
from vexgraph.ui.palette import NodeSearch  # noqa: E402
from vexgraph.ui.panel import VexGraphEditor  # noqa: E402
from vexgraph.ui import theme  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="session")
def registry():
    return default_registry()


@pytest.fixture(autouse=True)
def _restore_ui_scale():
    """theme.UI_SCALE is a process-wide global; do not let one test's text-size
    change leak into whichever test happens to run next."""
    before = theme.get_ui_scale()
    yield
    theme.set_ui_scale(before)


@pytest.fixture
def editor(app, registry):
    widget = VexGraphEditor(registry)
    widget.resize(1200, 800)
    yield widget
    widget.deleteLater()


def test_every_curated_node_can_be_drawn(app, registry):
    """A definition that cannot be laid out is a crash waiting on a canvas."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    broken = []
    for definition in (d for d in registry if d.tier == 1):
        try:
            item = scene.add_node(definition.type, QtCore.QPointF(0, 0))
            assert item is not None
            assert item.boundingRect().height() > 20
        except Exception as exc:
            broken.append(f"{definition.type}: {exc}")
    assert not broken, "\n".join(broken)


def test_a_sample_of_generated_nodes_can_be_drawn(app, registry):
    graph = Graph(registry)
    scene = GraphScene(graph)
    tier2 = [d for d in registry if d.tier == 2]
    for definition in tier2[::37]:
        assert scene.add_node(definition.type, QtCore.QPointF(0, 0)) is not None


def test_ports_carry_the_resolved_type_not_the_placeholder(app, registry):
    """A socket typed by a dropdown must show that type, never 'any'."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    item = scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    item.node.params["type"] = "vector"
    item.rebuild()
    assert item.ports[("value", False)].vex_type == "vector"


def test_bad_wire_is_refused_with_an_explanation(app, registry):
    graph = Graph(registry)
    scene = GraphScene(graph)
    source = scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    target = scene.add_node("for_range", QtCore.QPointF(300, 0))
    messages = []
    scene.message.connect(messages.append)

    joined = scene.connect_ports(source.ports[("value", False)],
                                 target.ports[("count", True)])
    assert not joined
    assert messages and "components" in messages[-1]


def test_good_wire_connects_and_hides_the_value_row(app, registry):
    """Once a wire supplies a value, the stale literal must stop showing."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    source = scene.add_node("element_count", QtCore.QPointF(0, 0))
    target = scene.add_node("for_range", QtCore.QPointF(300, 0))
    assert any(r.key == "in:count" for r in target.rows)

    assert scene.connect_ports(source.ports[("count", False)],
                               target.ports[("count", True)])
    assert not any(r.key == "in:count" for r in target.rows)
    assert len(scene.link_items) == 1


def test_exec_and_data_wires_do_not_mix(app, registry):
    graph = Graph(registry)
    scene = GraphScene(graph)
    start = scene.add_node("start", QtCore.QPointF(0, 0))
    target = scene.add_node("attrib_set", QtCore.QPointF(300, 0))
    assert not scene.connect_ports(start.ports[("exec", False)],
                                   target.ports[("value", True)])
    assert scene.connect_ports(start.ports[("exec", False)],
                               target.ports[("exec", True)])


def test_editing_a_row_updates_the_graph_and_the_code(editor):
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params["value"] = "{1, 0, 0}"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    row = next(r for r in item.rows if r.key == "param:attrib")
    row.set_value("myattr")
    editor._regenerate()
    assert "myattr" in editor.code.toPlainText()


def test_retyping_drops_a_wire_that_no_longer_fits(app, registry):
    graph = Graph(registry)
    scene = GraphScene(graph)
    source = scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    source.node.params["type"] = "vector"
    source.rebuild()
    target = scene.add_node("attrib_set", QtCore.QPointF(300, 0))
    target.node.params["type"] = "vector"
    target.rebuild()
    assert scene.connect_ports(source.ports[("value", False)],
                               target.ports[("value", True)])

    target.set_value("param:type", "string")
    assert not scene.link_items, "a vector must not stay wired into a string"


def test_selecting_a_node_highlights_its_lines(editor):
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params["value"] = "{1, 0, 0}"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._regenerate()
    editor.scene.select_node(item.node.id)
    assert editor.code.extraSelections()


def test_clicking_a_code_line_selects_the_node_that_wrote_it(editor):
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params["value"] = "{1, 0, 0}"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._regenerate()
    line = next(n for n, text in
                enumerate(editor.code.toPlainText().splitlines(), 1)
                if "@Cd" in text)
    editor._select_from_code(line)
    assert editor.scene.selected_node_id() == item.node.id


def test_problems_are_listed_and_mark_the_node(editor):
    """A node with a missing required input should be visibly at fault."""
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params.pop("value", None)
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._regenerate()
    assert editor.issues.count() >= 1
    assert item.status in ("error", "warning")


def test_search_filters_to_what_can_take_the_dragged_type(app, registry):
    graph = Graph(registry)
    scene = GraphScene(graph)
    item = scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    item.node.params["type"] = "vector"
    item.rebuild()

    dialog = NodeSearch(registry, accepts="vector")
    dialog.repopulate("length")
    labels = [dialog.list.item(i).text() for i in range(dialog.list.count())]
    assert any("Length" in text for text in labels)

    dialog.repopulate("repeat")
    labels = [dialog.list.item(i).text() for i in range(dialog.list.count())]
    assert not any(text.startswith("Repeat\n") for text in labels), \
        "Repeat only takes an int, so a vector drag should not offer it"


def test_deleting_a_node_takes_its_wires_with_it(app, registry):
    graph = Graph(registry)
    scene = GraphScene(graph)
    source = scene.add_node("element_count", QtCore.QPointF(0, 0))
    target = scene.add_node("for_range", QtCore.QPointF(300, 0))
    scene.connect_ports(source.ports[("count", False)],
                        target.ports[("count", True)])
    scene.clearSelection()
    source.setSelected(True)
    scene.delete_selected()
    assert not scene.link_items
    assert not graph.links


def test_a_graph_with_no_positions_gets_laid_out(app, registry):
    """Anything built in code arrives at the origin; a pile is not a graph."""
    from vexgraph.ui.layout import needs_arranging  # noqa: PLC0415

    graph = Graph(registry)
    graph.add("start", "start")
    graph.add("element_count", "count")
    graph.add("for_range", "loop")
    graph.chain("start", "loop")
    graph.connect("count", "count", "loop", "count")
    assert needs_arranging(graph)

    scene = GraphScene(graph)
    positions = [scene.node_items[i].pos() for i in graph.nodes]
    assert len({(round(p.x()), round(p.y())) for p in positions}) == len(positions)


def test_layout_puts_a_source_left_of_what_it_feeds(app, registry):
    graph = Graph(registry)
    graph.add("start", "start")
    graph.add("element_count", "count")
    graph.add("for_range", "loop")
    graph.chain("start", "loop")
    graph.connect("count", "count", "loop", "count")
    scene = GraphScene(graph)
    assert (scene.node_items["count"].pos().x()
            < scene.node_items["loop"].pos().x())


def test_node_library_groups_the_curated_nodes(editor):
    tree = editor.browser.tree
    categories = {tree.topLevelItem(i).text(0)
                  for i in range(tree.topLevelItemCount())}
    assert {"Flow", "Attributes", "Maths", "Geometry"} <= categories


def test_node_library_search_reaches_the_generated_tier(editor):
    editor.browser.search.setText("xyzdist")
    tree = editor.browser.tree
    labels = []
    for i in range(tree.topLevelItemCount()):
        parent = tree.topLevelItem(i)
        labels += [parent.child(j).text(0) for j in range(parent.childCount())]
    assert any("xyzdist" in text for text in labels)


def test_node_library_describes_the_selected_node(editor):
    editor.browser._describe("closest_surface_point")
    text = editor.browser.detail.toPlainText()
    assert "nearest spot" in text
    assert "Distance" in text


def test_double_clicking_the_library_places_a_node(editor):
    before = len(editor.scene.node_items)
    editor._place_from_browser("attrib_get")
    assert len(editor.scene.node_items) == before + 1


def test_a_proposal_can_be_discarded_and_the_graph_comes_back(editor, registry):
    editor.scene.add_node("note", QtCore.QPointF(0, 0))
    original = len(editor.graph.nodes)

    proposed = Graph(registry)
    proposed.add("start", "start")
    proposed.add("attrib_set", "paint", attrib="Cd", type="vector",
                 value="{1, 0, 0}")
    proposed.chain("start", "paint")

    editor._propose(proposed, "", "a proposal")
    assert len(editor.graph.nodes) == 2

    editor._discard_proposal()
    assert len(editor.graph.nodes) == original


def test_keeping_a_proposal_leaves_it_in_place(editor, registry):
    proposed = Graph(registry)
    proposed.add("start", "start")
    proposed.add("attrib_set", "paint", attrib="Cd", type="vector",
                 value="{1, 0, 0}")
    proposed.chain("start", "paint")

    editor._propose(proposed, "", "")
    editor._keep_proposal()
    assert "@Cd" in editor.code.toPlainText()
    assert editor._graph_before_proposal is None


def test_assistant_is_told_which_graph_is_on_the_canvas(editor, registry):
    graph = Graph(registry)
    graph.add("start", "start")
    editor.set_graph(graph)
    assert editor.assistant._current_graph is graph


def test_example_graph_opens_and_renders(app, registry):
    path = (Path(__file__).resolve().parents[1] / "examples"
            / "average_neighbour_colour.vexgraph.json")
    editor = VexGraphEditor(registry, Graph.load(path, registry))
    assert len(editor.scene.node_items) == len(editor.graph.nodes)
    assert len(editor.scene.link_items) == len(editor.graph.links)
    assert "foreach" in editor.code.toPlainText()
    editor.deleteLater()


# --------------------------------------------------------- navigation, focus

def test_wheel_zooms_in_and_out_within_bounds(editor):
    """A real regression: reported as 'zoom does not work' with a hard 3x cap."""
    start = editor.view.transform().m11()

    def wheel(times: int, *, zoom_in: bool) -> None:
        for _ in range(times):
            editor.view.wheelEvent(QtGui.QWheelEvent(
                QtCore.QPointF(50, 50), QtCore.QPointF(50, 50),
                QtCore.QPoint(0, 0), QtCore.QPoint(0, 120 if zoom_in else -120),
                QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                QtCore.Qt.ScrollPhase.NoScrollPhase, False))

    wheel(3, zoom_in=True)
    assert editor.view.transform().m11() > start

    wheel(40, zoom_in=False)
    zoomed_out = editor.view.transform().m11()
    assert editor.view.MIN_ZOOM - 1e-6 <= zoomed_out <= editor.view.MAX_ZOOM + 1e-6
    assert zoomed_out < start                # actually reached the floor, not stuck


def test_right_click_drag_pans_like_middle_click(editor):
    view = editor.view
    start_h = view.horizontalScrollBar().value()

    def mouse_event(kind, pos, button, buttons):
        point = QtCore.QPointF(*pos)
        return QtGui.QMouseEvent(kind, point, point, button, buttons,
                                 QtCore.Qt.KeyboardModifier.NoModifier)

    view.mousePressEvent(mouse_event(
        QtCore.QEvent.Type.MouseButtonPress, (100, 100),
        QtCore.Qt.MouseButton.RightButton, QtCore.Qt.MouseButton.RightButton))
    assert view._panning

    view.mouseMoveEvent(mouse_event(
        QtCore.QEvent.Type.MouseMove, (160, 100),
        QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.RightButton))
    assert view.horizontalScrollBar().value() != start_h

    view.mouseReleaseEvent(mouse_event(
        QtCore.QEvent.Type.MouseButtonRelease, (160, 100),
        QtCore.Qt.MouseButton.RightButton, QtCore.Qt.MouseButton.NoButton))
    assert not view._panning


def test_clicking_a_row_and_typing_reaches_the_embedded_editor(editor):
    """The reported bug: a real click, then real keystrokes, went nowhere.

    Root cause was that logical scene focus on the QGraphicsProxyWidget is
    not the same thing as the QGraphicsView holding actual OS keyboard focus
    - without the latter, keystrokes kept going to whatever widget focus
    happened to be on (the assistant's Ask button) and never reached the row.
    """
    # A widget must actually be shown to hold real OS focus - the fixture
    # only constructs it, which is enough for every other test but not this
    # one, since the bug being tested for lives entirely in focus routing.
    editor.show()
    QtWidgets.QApplication.processEvents()

    item = editor.scene.add_node("note", QtCore.QPointF(0, 0))
    row = next(r for r in item.rows if r.key == "param:text")

    view_pos = editor.view.mapFromScene(row.mapToScene(row.boundingRect().center()))
    QtTest.QTest.mouseClick(editor.view.viewport(),
                            QtCore.Qt.MouseButton.LeftButton,
                            QtCore.Qt.KeyboardModifier.NoModifier, view_pos)

    focused = QtWidgets.QApplication.focusWidget()
    assert focused is editor.view, (
        "a click on a row must give the view real OS focus, or typing has "
        "nowhere to go")

    QtTest.QTest.keyClicks(focused, "hello")
    line = editor.scene._editor.widget()
    assert line.text() == "hello"


def test_committing_an_edit_by_pressing_enter_does_not_crash(editor):
    """Regression for a real segfault.

    editingFinished fires synchronously from inside the QLineEdit's own
    Return-key handling. Deleting the QGraphicsProxyWidget - and the QLineEdit
    living inside it - from that same call stack is a use-after-free; the
    removal has to be deferred to the next event-loop turn.
    """
    editor.show()
    QtWidgets.QApplication.processEvents()

    item = editor.scene.add_node("note", QtCore.QPointF(0, 0))
    row = next(r for r in item.rows if r.key == "param:text")
    view_pos = editor.view.mapFromScene(row.mapToScene(row.boundingRect().center()))
    QtTest.QTest.mouseClick(editor.view.viewport(),
                            QtCore.Qt.MouseButton.LeftButton,
                            QtCore.Qt.KeyboardModifier.NoModifier, view_pos)
    focused = QtWidgets.QApplication.focusWidget()
    assert focused is not None, "the click did not give any widget real focus"
    QtTest.QTest.keyClicks(focused, "hi")

    QtTest.QTest.keyClick(focused, QtCore.Qt.Key.Key_Return)
    app = QtWidgets.QApplication.instance()
    app.processEvents()      # runs the deferred close - this must not crash

    assert editor.scene._editor is None
    assert item.node.params.get("text") == "hi"


# ------------------------------------------------------------------ text size

def test_text_size_control_grows_node_boxes(editor):
    node = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    before = node.boundingRect().width()

    editor.text_size.setCurrentText("150%")

    assert node.boundingRect().width() > before
    assert theme.get_ui_scale() == pytest.approx(1.5)


def test_text_size_setting_is_restored_on_smaller_value(editor):
    node = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    editor.text_size.setCurrentText("150%")
    grown = node.boundingRect().width()

    editor.text_size.setCurrentText("90%")

    assert node.boundingRect().width() < grown
    assert theme.get_ui_scale() == pytest.approx(0.9)


def test_repeated_text_size_changes_do_not_crash(app, registry):
    """Regression for a hard crash that closed Houdini.

    Rebuilding a node destroys every port and row. Qt's BSP scene index
    purges removed items on a zero-delay timer, so an item freed before that
    timer runs leaves a dangling pointer in the index and the next update
    walks freed memory. Scaling text rebuilds every node at once, which made
    it easy to hit - especially when shrinking.

    If this ever regresses it segfaults rather than failing an assert, so a
    green run here IS the assertion.
    """
    path = (Path(__file__).resolve().parents[1] / "examples"
            / "colour_by_proximity.vexgraph.json")
    widget = VexGraphEditor(registry, Graph.load(path, registry))
    widget.resize(1000, 700)
    widget.show()
    app.processEvents()

    for value in ["90%", "150%", "100%", "130%", "90%", "115%"] * 3:
        widget.text_size.setCurrentText(value)
        app.processEvents()
        widget.view.viewport().repaint()
        app.processEvents()

    assert len(widget.scene.node_items) == len(widget.graph.nodes)
    widget.deleteLater()


def test_scene_uses_no_spatial_index(editor):
    """The index is the thing that crashed; keep it off deliberately."""
    assert (editor.scene.itemIndexMethod()
            is QtWidgets.QGraphicsScene.ItemIndexMethod.NoIndex)


# ------------------------------------------------------------------ assistant

def test_assistant_transcript_sits_above_the_compose_box(editor):
    """Reported as 'the chat window is upside down'."""
    panel = editor.assistant
    layout = panel.layout()
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order.index(panel.log) < order.index(panel.input), (
        "the transcript must be above the box you type into")


def test_worker_subprocess_env_drops_houdinis_python(monkeypatch):
    """Regression for 'SRE module mismatch' when asking Claude inside Houdini.

    Houdini sets PYTHONHOME to its own 3.11. Inheriting it makes this
    project's Python load Houdini's standard library and die instantly.
    """
    from vexgraph.ui.assistant_panel import _clean_python_env  # noqa: PLC0415

    monkeypatch.setenv("PYTHONHOME", "C:/houdini/python311")
    monkeypatch.setenv("PYTHONPATH", "C:/houdini/houdini/python3.11libs")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")

    env = _clean_python_env()

    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    # The credential must survive - it is how the worker authenticates.
    assert env["ANTHROPIC_API_KEY"] == "sk-test-not-a-real-key"


def _click_row(editor, item, key):
    """Click the value pill of one row and return the widget holding focus."""
    row = next(r for r in item.rows if r.key == key)
    view_pos = editor.view.mapFromScene(row.mapToScene(row.boundingRect().center()))
    QtTest.QTest.mouseClick(editor.view.viewport(),
                            QtCore.Qt.MouseButton.LeftButton,
                            QtCore.Qt.KeyboardModifier.NoModifier, view_pos)
    return QtWidgets.QApplication.focusWidget()


def test_a_row_can_be_edited_more_than_once(editor):
    """Committing an edit must not make the field read-only from then on."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    item = editor.scene.add_node("note", QtCore.QPointF(0, 0))

    focused = _click_row(editor, item, "param:text")
    QtTest.QTest.keyClicks(focused, "first")
    QtTest.QTest.keyClick(focused, QtCore.Qt.Key.Key_Return)
    QtWidgets.QApplication.instance().processEvents()

    node_id = item.node.id
    item = next(i for i in editor.scene.items()
                if isinstance(i, NodeItem) and i.node.id == node_id)
    focused = _click_row(editor, item, "param:text")
    assert editor.scene.is_editing, "clicking the row a second time did not reopen it"
    line = editor.scene._editor.widget()
    line.selectAll()
    QtTest.QTest.keyClicks(line, "second")
    QtTest.QTest.keyClick(line, QtCore.Qt.Key.Key_Return)
    QtWidgets.QApplication.instance().processEvents()

    assert editor.scene.graph.nodes[node_id].params["text"] == "second"


def test_backspace_while_editing_edits_text_not_the_graph(editor):
    """Backspace inside a field must delete a character, never the node."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    item = editor.scene.add_node("note", QtCore.QPointF(0, 0))
    before = len(editor.scene.graph.nodes)

    focused = _click_row(editor, item, "param:text")
    QtTest.QTest.keyClicks(focused, "abc")
    line = editor.scene._editor.widget()
    QtTest.QTest.keyClick(line, QtCore.Qt.Key.Key_Backspace)

    assert line.text() == "ab"
    assert len(editor.scene.graph.nodes) == before, "Backspace deleted the node"


def test_choosing_from_the_search_adds_exactly_one_node(app, registry):
    """A double-click sends itemClicked and itemActivated; that is still one node."""
    dialog = NodeSearch(registry)
    dialog.repopulate("set attribute")
    picked = []
    dialog.chosen.connect(picked.append)

    dialog._accept_current()
    dialog._accept_current()          # the second signal of the same gesture

    assert len(picked) == 1


def test_a_node_added_from_the_library_can_be_deleted(editor):
    """Adding must hand the keyboard back, or Delete never reaches the canvas."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    before = len(editor.scene.graph.nodes)

    editor._place_from_browser("attrib_set")
    assert len(editor.scene.graph.nodes) == before + 1
    assert QtWidgets.QApplication.focusWidget() is editor.view

    QtTest.QTest.keyClick(editor.view, QtCore.Qt.Key.Key_Delete)
    assert len(editor.scene.graph.nodes) == before


def test_a_click_on_an_output_arms_a_wire_that_a_second_click_lands(app, registry):
    """Reaching a distant node must not require holding the button down."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    source = scene.add_node("element_count", QtCore.QPointF(0, 0))
    target = scene.add_node("for_range", QtCore.QPointF(600, 0))
    out = source.ports[("count", False)]
    into = target.ports[("count", True)]

    at = out.mapToScene(out.boundingRect().center())
    scene.begin_link_drag(out, at)
    release = QtWidgets.QGraphicsSceneMouseEvent(
        QtCore.QEvent.Type.GraphicsSceneMouseRelease)
    release.setScenePos(at)                       # released without moving
    scene.mouseReleaseEvent(release)

    assert scene._sticky, "a click on a port should leave the wire armed"
    assert not scene.link_items, "nothing is connected until the second click"

    press = QtWidgets.QGraphicsSceneMouseEvent(
        QtCore.QEvent.Type.GraphicsSceneMousePress)
    press.setScenePos(into.mapToScene(into.boundingRect().center()))
    scene.mousePressEvent(press)

    assert len(scene.link_items) == 1
    assert not scene._sticky


def test_dragging_still_connects_without_arming(app, registry):
    """The click path must not break the ordinary press-drag-release gesture."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    source = scene.add_node("element_count", QtCore.QPointF(0, 0))
    target = scene.add_node("for_range", QtCore.QPointF(600, 0))
    out = source.ports[("count", False)]
    into = target.ports[("count", True)]

    scene.begin_link_drag(out, out.mapToScene(out.boundingRect().center()))
    release = QtWidgets.QGraphicsSceneMouseEvent(
        QtCore.QEvent.Type.GraphicsSceneMouseRelease)
    release.setScenePos(into.mapToScene(into.boundingRect().center()))
    scene.mouseReleaseEvent(release)

    assert not scene._sticky
    assert len(scene.link_items) == 1


def test_undo_and_redo_walk_the_graph_back_and_forward(editor):
    before = len(editor.scene.graph.nodes)
    editor._place_from_browser("attrib_set")
    editor._place_from_browser("attrib_get")
    assert len(editor.scene.graph.nodes) == before + 2

    editor.undo()
    assert len(editor.scene.graph.nodes) == before + 1
    editor.undo()
    assert len(editor.scene.graph.nodes) == before

    editor.redo()
    assert len(editor.scene.graph.nodes) == before + 1
    editor.redo()
    assert len(editor.scene.graph.nodes) == before + 2


def test_undo_restores_a_deleted_node_with_its_wires(editor):
    source = editor.scene.add_node("element_count", QtCore.QPointF(0, 0))
    target = editor.scene.add_node("for_range", QtCore.QPointF(300, 0))
    editor.scene.connect_ports(source.ports[("count", False)],
                               target.ports[("count", True)])
    assert len(editor.scene.graph.links) == 1

    editor.scene.clearSelection()
    editor.scene.node_items[source.node.id].setSelected(True)
    editor.scene.delete_selected()
    assert len(editor.scene.graph.links) == 0

    editor.undo()
    assert len(editor.scene.graph.links) == 1, "the wire came back broken"


def test_a_new_edit_after_undoing_drops_the_redo_branch(editor):
    editor._place_from_browser("attrib_set")
    editor.undo()
    assert editor.history.can_redo

    editor._place_from_browser("attrib_get")
    assert not editor.history.can_redo


def test_importing_vex_does_not_leave_the_old_graph_undoable(editor):
    editor._place_from_browser("attrib_set")
    editor.set_graph(Graph(editor.registry))
    assert not editor.history.can_undo


def test_clicking_any_part_of_a_node_selects_it(editor, monkeypatch):
    """Delete acts on the selection, so every click on a node must select it.

    Rows cover most of a node, and each row handles its own clicks; when they
    did not pass selection up, clicking a node's body left nothing selected and
    Delete appeared to be broken.
    """
    # A dropdown row opens a modal QMenu on click, which would wait forever for
    # a person. The menu is not what is under test; selection is.
    monkeypatch.setattr(GraphScene, "open_row_menu", lambda *a, **k: None)

    editor.show()
    QtWidgets.QApplication.processEvents()
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    kinds = {type(row).__name__ for row in item.rows}
    assert len(kinds) > 1, "this node should exercise more than one row type"

    for row in item.rows:
        editor.scene.clearSelection()
        assert not item.isSelected()
        centre = editor.view.mapFromScene(row.mapToScene(row.boundingRect().center()))
        QtTest.QTest.mouseClick(editor.view.viewport(),
                                QtCore.Qt.MouseButton.LeftButton,
                                QtCore.Qt.KeyboardModifier.NoModifier, centre)
        assert item.isSelected(), (
            f"clicking the {row.key!r} row ({type(row).__name__}) did not select "
            f"the node, so Delete would do nothing")
        editor.scene._close_editor()


def test_delete_works_after_clicking_a_nodes_body(editor):
    """The end-to-end version of the bug as reported: click node, press Delete."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    before = len(editor.scene.graph.nodes)

    row = next(r for r in item.rows if r.key.startswith("param:"))
    centre = editor.view.mapFromScene(row.mapToScene(row.boundingRect().center()))
    QtTest.QTest.mouseClick(editor.view.viewport(),
                            QtCore.Qt.MouseButton.LeftButton,
                            QtCore.Qt.KeyboardModifier.NoModifier, centre)
    editor.scene._close_editor()
    QtTest.QTest.keyClick(editor.view, QtCore.Qt.Key.Key_Delete)

    assert len(editor.scene.graph.nodes) == before - 1


def test_the_canvas_claims_the_keys_houdini_would_otherwise_eat(app, registry):
    """Inside a Python Panel, Houdini's hotkeys see key presses first.

    A widget only gets them by accepting ShortcutOverride. Without this, Delete
    never reaches keyPressEvent at all, which is why deleting worked in tests
    and not in Houdini.
    """
    graph = Graph(registry)
    scene = GraphScene(graph)
    view = GraphView(scene)
    for key in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace,
                QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Escape,
                QtCore.Qt.Key.Key_Z, QtCore.Qt.Key.Key_Y):
        event = QtGui.QKeyEvent(QtCore.QEvent.Type.ShortcutOverride, key,
                                QtCore.Qt.KeyboardModifier.NoModifier)
        assert view.event(event), f"{key} was not claimed"
        assert event.isAccepted()

    # A key the canvas has no use for must be left for Houdini.
    other = QtGui.QKeyEvent(QtCore.QEvent.Type.ShortcutOverride,
                            QtCore.Qt.Key.Key_S,
                            QtCore.Qt.KeyboardModifier.ControlModifier)
    view.event(other)
    assert not other.isAccepted(), "Ctrl+S should stay with Houdini"


def test_deleting_works_without_any_key_press(editor):
    """The toolbar route, which no key interception can take away."""
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    before = len(editor.scene.graph.nodes)
    editor.scene.clearSelection()
    item.setSelected(True)

    editor.scene.delete_selected()
    assert len(editor.scene.graph.nodes) == before - 1


def test_the_right_click_menu_can_delete_the_selection(editor):
    """A route to deleting that does not go through the keyboard at all."""
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    editor.scene.clearSelection()
    item.setSelected(True)

    menu = editor.view.build_node_menu()
    actions = {a.data(): a for a in menu.actions() if a.data()}
    assert actions["delete"].isEnabled(), "a selected node must be deletable"

    before = len(editor.scene.graph.nodes)
    editor.scene.delete_selected()
    assert len(editor.scene.graph.nodes) == before - 1

    editor.scene.clearSelection()
    assert not editor.view.build_node_menu().actions()[0].isEnabled(),         "with nothing selected, Delete must be greyed rather than silent"


def test_delete_survives_a_host_that_eats_key_events(editor):
    """Houdini filters keys at the application level and swallows Delete.

    Two earlier fixes (keyPressEvent, then ShortcutOverride) both passed
    standalone and both failed in Houdini. This models the host: a filter that
    eats every key press. Ours is installed after it, so Qt calls ours first
    and the key must still reach the canvas.
    """
    app = QtWidgets.QApplication.instance()

    class GreedyHost(QtCore.QObject):
        def eventFilter(self, watched, event):
            if event.type() == QtCore.QEvent.Type.KeyPress:
                return True          # "mine", says Houdini
            return False

    host = GreedyHost()
    app.installEventFilter(host)                 # installed BEFORE ours
    editor._install_key_filter()                 # so ours runs first
    try:
        editor.show()
        app.processEvents()
        item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
        editor.scene.clearSelection()
        item.setSelected(True)
        editor.view.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        before = len(editor.scene.graph.nodes)

        QtTest.QTest.keyClick(editor.view, QtCore.Qt.Key.Key_Delete)
        assert len(editor.scene.graph.nodes) == before - 1, \
            "the host ate Delete before the canvas saw it"
    finally:
        app.removeEventFilter(host)
        app.removeEventFilter(editor)


def test_the_key_filter_leaves_typing_alone(editor):
    """Backspace in the assistant box must edit text, not delete a node."""
    app = QtWidgets.QApplication.instance()
    editor.show()
    app.processEvents()
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    editor.scene.clearSelection()
    item.setSelected(True)
    before = len(editor.scene.graph.nodes)

    editor.assistant.input.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    QtTest.QTest.keyClicks(editor.assistant.input, "abc")
    QtTest.QTest.keyClick(editor.assistant.input, QtCore.Qt.Key.Key_Backspace)

    assert editor.assistant.input.toPlainText() == "ab"
    assert len(editor.scene.graph.nodes) == before, "typing deleted a node"


def test_a_node_dropped_from_the_library_lands_where_it_was_dropped(editor):
    from vexgraph.ui.browser import NODE_MIME

    editor.show()
    QtWidgets.QApplication.processEvents()
    before = len(editor.scene.graph.nodes)

    data = QtCore.QMimeData()
    data.setData(NODE_MIME, b"fit_range")
    where = QtCore.QPointF(120, 90)
    drop = QtGui.QDropEvent(where, QtCore.Qt.DropAction.CopyAction, data,
                            QtCore.Qt.MouseButton.LeftButton,
                            QtCore.Qt.KeyboardModifier.NoModifier)
    editor.view.dropEvent(drop)

    assert len(editor.scene.graph.nodes) == before + 1
    added = [n for n in editor.scene.graph.nodes.values()
             if n.type == "fit_range"]
    assert added, "the dropped node type was not created"
    expected = editor.view.mapToScene(where.toPoint())
    assert abs(added[0].pos[0] - expected.x()) < 1.0


def test_the_library_drag_carries_the_node_type(app, registry):
    from vexgraph.ui.browser import NODE_MIME, NodeTree

    tree = NodeTree(registry)
    tree.populate("fit range")
    items = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        items += [top.child(j) for j in range(top.childCount())]
    chosen = next(i for i in items
                  if i.data(0, QtCore.Qt.ItemDataRole.UserRole) == "fit_range")

    data = tree.mimeData([chosen])
    assert data.hasFormat(NODE_MIME), "the drag carried nothing the canvas reads"
    assert bytes(data.data(NODE_MIME)).decode() == "fit_range"


def test_documented_nodes_are_marked_on_the_canvas(app, registry):
    """Double-click opens help; the colour is what makes that discoverable."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    documented = scene.add_node("fit_range", QtCore.QPointF(0, 0))
    plain = scene.add_node("not_true", QtCore.QPointF(300, 0))

    assert documented.has_help, "fit() is documented"
    assert not plain.has_help, "Not is an operator, with no page"
    assert theme.NODE_TITLE_DOCUMENTED != theme.NODE_TITLE_TEXT


def test_live_apply_writes_only_a_graph_that_works(editor):
    """A half-finished edit must not push broken VEX at the viewport."""
    applied = []
    editor.applied.connect(applied.append)
    editor.auto_apply.setChecked(True)

    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params["value"] = "{1, 0, 0}"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._auto_apply()
    assert applied, "a valid graph should have been written"
    good = len(applied)

    item.node.params.pop("value", None)          # now it is incomplete
    editor._auto_apply()
    assert len(applied) == good, "a graph with errors was written to the wrangle"


def test_live_apply_can_be_turned_off(editor):
    applied = []
    editor.applied.connect(applied.append)
    editor.auto_apply.setChecked(False)

    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params["value"] = "{1, 0, 0}"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._auto_apply()
    assert not applied


def test_typing_claims_every_key_from_the_host(editor):
    """Houdini binds the arrows to frame stepping and Home/End to the range.

    While a text field has focus those belong to the field, so the filter has
    to claim them from Houdini - not only the printable keys. Without this,
    moving the cursor through a word scrubbed the timeline.
    """
    editor.show()
    QtWidgets.QApplication.processEvents()
    editor.assistant.input.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
    QtWidgets.QApplication.processEvents()

    for key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right,
                QtCore.Qt.Key.Key_Home, QtCore.Qt.Key.Key_End,
                QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down):
        event = QtGui.QKeyEvent(QtCore.QEvent.Type.ShortcutOverride, key,
                                QtCore.Qt.KeyboardModifier.NoModifier)
        claimed = editor.eventFilter(editor.assistant.input, event)
        assert claimed and event.isAccepted(), (
            f"{key} would reach Houdini and move the playbar instead")


def test_arrow_keys_on_the_canvas_are_left_alone(editor):
    """Nothing is being typed into, so the host may keep its own bindings."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    editor.view.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
    QtWidgets.QApplication.processEvents()

    event = QtGui.QKeyEvent(QtCore.QEvent.Type.ShortcutOverride,
                            QtCore.Qt.Key.Key_Left,
                            QtCore.Qt.Key.Key_unknown and
                            QtCore.Qt.KeyboardModifier.NoModifier)
    assert not editor.eventFilter(editor.view, event)


def test_inline_vex_gets_a_multi_line_editor(app, registry):
    """The escape hatch holds exactly the code that was too involved to model,
    so it is never one short line - a one-line pill was the wrong shape."""
    from vexgraph.ui.items import CodeRow

    assert registry.require("inline_vex").param("code").kind == "text"
    graph = Graph(registry)
    scene = GraphScene(graph)
    item = scene.add_node("inline_vex", QtCore.QPointF(0, 0))
    row = next(r for r in item.rows if r.key == "param:code")
    assert isinstance(row, CodeRow)

    row.set_value("int i = 0;\nfor (i = 0; i < 3; i++) {\n    @P += 1;\n}")
    assert "4 lines" in row.summary(), row.summary()
    row.set_value("")
    assert "click to write" in row.summary()
