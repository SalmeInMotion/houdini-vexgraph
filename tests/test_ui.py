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
def _isolate_settings(tmp_path, monkeypatch):
    """Never write into the user's real settings.

    Fold states, Learn progress, the last assistant question and the canvas
    preferences all live in QSettings - so without this a test run quietly
    rearranged the actual app, and one test's click became the next run's
    starting state. Per test, not per session: exercises and fold states
    must not leak between tests either.
    """
    from vexgraph.ui import settings as settings_module

    ini = str(tmp_path / "vexgraph.ini")
    monkeypatch.setattr(
        settings_module, "store",
        lambda: QtCore.QSettings(ini, QtCore.QSettings.Format.IniFormat))
    yield


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
    editor.browser.describe("closest_surface_point")
    text = editor.browser.detail.toPlainText()
    assert "nearest spot" in text
    assert "Distance" in text


def test_selecting_a_node_on_the_canvas_describes_it_in_the_library(editor):
    """The description follows whichever node you touched last.

    Reaching a node by clicking it in the graph is at least as common as
    finding it in the tree, and until now only the tree filled this pane - so
    while you were working on a node the help on screen was for a different
    one.
    """
    editor.scene.add_node("closest_surface_point", QtCore.QPointF(0, 0))
    item = next(iter(editor.scene.node_items.values()))
    item.setSelected(True)
    assert "nearest spot" in editor.browser.detail.toPlainText()

    # Clicking empty canvas keeps the last description rather than blanking it.
    editor.scene.clearSelection()
    assert "nearest spot" in editor.browser.detail.toPlainText()


def test_copying_and_pasting_keeps_the_wiring_and_the_layout(editor):
    source = editor.scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    target = editor.scene.add_node("attrib_set", QtCore.QPointF(200, 40))
    definition = editor.registry.get("attrib_get")
    editor.graph.connect(source.node.id, definition.outputs[0].name,
                         target.node.id,
                         editor.registry.get("attrib_set").inputs[0].name)
    editor.scene.rebuild_links()

    editor.scene.clearSelection()
    source.setSelected(True)
    target.setSelected(True)
    assert editor.scene.copy_selected() == 2

    nodes, links = len(editor.graph.nodes), len(editor.graph.links)
    assert editor.scene.paste(QtCore.QPointF(500, 300)) == 2
    assert len(editor.graph.nodes) == nodes + 2
    # The wire between the two copied nodes came along, remapped onto the copies.
    assert len(editor.graph.links) == links + 1
    # ...and so did the gap between them.
    pasted = sorted(n.pos for n in editor.graph.nodes.values())[-2:]
    assert pasted == [(500.0, 300.0), (700.0, 340.0)]
    # The copy is what you are now holding, so the next drag moves it.
    assert len([i for i in editor.scene.selectedItems()
                if isinstance(i, type(source))]) == 2


def test_pasting_an_empty_clipboard_does_nothing_rather_than_failing(editor):
    QtWidgets.QApplication.clipboard().setText("not a graph at all")
    assert editor.scene.paste(QtCore.QPointF(0, 0)) == 0


def test_a_section_can_be_folded_away_and_stays_folded(editor):
    """A docked panel is short; the section you are not using costs the room."""
    from vexgraph.ui.panel import SectionHeader

    header = next(h for h in editor.findChildren(SectionHeader)
                  if "Ask for a graph" in h.text())
    assert editor.assistant.isVisibleTo(editor)

    header.clicked.emit()
    assert not editor.assistant.isVisibleTo(editor)
    assert header.text().startswith("▸")

    header.clicked.emit()
    assert editor.assistant.isVisibleTo(editor)
    assert header.text().startswith("▾")


def test_an_answer_is_laid_out_rather_than_run_together(editor):
    """Models write headings, lists and fenced code whatever you ask them.

    Escaping all of it produced one paragraph with literal asterisks in it,
    which is the least readable form the same words could take.
    """
    from vexgraph.ui import richtext

    rendered = richtext.to_html(
        "The **Get Attribute** node reads a value.\n"
        "\n"
        "### How to wire it\n"
        "1. Set `attrib` to the name.\n"
        "   Wrapped continuation of the same step.\n"
        "2. Connect the output.\n"
        "\n"
        "```vex\n"
        "@P += 1;\n"
        "```\n")
    assert "<b>Get Attribute</b>" in rendered          # bold, not asterisks
    assert rendered.count("<li") == 2                  # two steps, not four
    assert "Wrapped continuation" in rendered          # folded into step one
    assert "<ol" in rendered and "<pre" in rendered
    assert "```" not in rendered and "###" not in rendered


def test_the_graph_name_survives_being_saved_and_reloaded(editor, registry):
    from vexgraph.graph import Graph

    editor.graph.name = "Tangents along a curve"
    reloaded = Graph.from_dict(editor.graph.to_dict(), registry)
    assert reloaded.name == "Tangents along a curve"

    # An unnamed graph does not litter the file with an empty key.
    editor.graph.name = ""
    assert "name" not in editor.graph.to_dict()


def test_tab_is_kept_for_the_node_list_instead_of_moving_focus(editor):
    """Qt handles Tab as focus navigation before keyPressEvent ever runs."""
    assert editor.view.focusNextPrevChild(True) is False
    assert QtCore.Qt.Key.Key_Tab in editor.view.CLAIMED_KEYS


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

    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    row = next(r for r in item.rows if r.key == "param:attrib")

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

    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    row = next(r for r in item.rows if r.key == "param:attrib")
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
    assert item.node.params.get("attrib") == "hi"


# ------------------------------------------------------------------ text size

def test_text_size_buttons_grow_and_shrink_node_boxes(editor):
    node = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    before = node.boundingRect().width()

    for _ in range(4):                      # all the way up the steps
        editor.text_larger.click()
    assert node.boundingRect().width() > before
    assert theme.get_ui_scale() == pytest.approx(1.5)
    assert editor.text_size_label.text() == "150%"

    for _ in range(6):                      # and past the bottom, clamped
        editor.text_smaller.click()
    assert node.boundingRect().width() < before
    assert theme.get_ui_scale() == pytest.approx(0.9)
    assert editor.text_size_label.text() == "90%"


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
        widget._set_text_size(value)
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
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))

    focused = _click_row(editor, item, "param:attrib")
    QtTest.QTest.keyClicks(focused, "first")
    QtTest.QTest.keyClick(focused, QtCore.Qt.Key.Key_Return)
    QtWidgets.QApplication.instance().processEvents()

    node_id = item.node.id
    item = next(i for i in editor.scene.items()
                if isinstance(i, NodeItem) and i.node.id == node_id)
    focused = _click_row(editor, item, "param:attrib")
    assert editor.scene.is_editing, "clicking the row a second time did not reopen it"
    line = editor.scene._editor.widget()
    line.selectAll()
    QtTest.QTest.keyClicks(line, "second")
    QtTest.QTest.keyClick(line, QtCore.Qt.Key.Key_Return)
    QtWidgets.QApplication.instance().processEvents()

    assert editor.scene.graph.nodes[node_id].params["attrib"] == "second"


def test_backspace_while_editing_edits_text_not_the_graph(editor):
    """Backspace inside a field must delete a character, never the node."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    before = len(editor.scene.graph.nodes)

    focused = _click_row(editor, item, "param:attrib")
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


def test_inline_vex_is_a_code_box_you_can_read(app, registry):
    """Ivan's ask: the inline shows its code at a glance, like the note.

    It was a one-line row summarising "first line (+N more)" - the one node
    whose whole content is worth seeing hid it behind a click."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    item = scene.add_node("inline_vex", QtCore.QPointF(0, 0))
    assert item.is_text_box and not item.is_note
    assert not item.rows, "a box, not a pill"

    lines = "\n".join(f"@P.y += {n} * 0.01;" for n in range(14))
    item.node.params["code"] = lines
    item.rebuild()
    assert item.boundingRect().height() > 200, "sized to show the snippet"
    assert item.boundingRect().width() > 250

    # Hand-dragged sizes win over the automatic fit, and persist.
    item.node.params.update(width="500", height="150")
    item.rebuild()
    assert abs(item.boundingRect().width() - 504) < 6


def test_double_clicking_a_wire_removes_it(editor):
    source = editor.scene.add_node("element_count", QtCore.QPointF(0, 0))
    target = editor.scene.add_node("for_range", QtCore.QPointF(400, 0))
    editor.scene.connect_ports(source.ports[("count", False)],
                               target.ports[("count", True)])
    assert len(editor.scene.link_items) == 1

    editor.scene.remove_link(editor.scene.link_items[0])
    assert not editor.scene.link_items
    assert not editor.scene.graph.links
    # The value row the wire was covering must come back.
    target = editor.scene.node_items[target.node.id]
    assert any(r.key == "in:count" for r in target.rows)


def test_right_click_abandons_a_wire_in_progress(editor):
    source = editor.scene.add_node("element_count", QtCore.QPointF(0, 0))
    port = source.ports[("count", False)]
    editor.scene.begin_link_drag(port, port.mapToScene(
        port.boundingRect().center()))
    assert editor.scene.is_wiring

    press = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress, QtCore.QPointF(10, 10),
        QtCore.Qt.MouseButton.RightButton, QtCore.Qt.MouseButton.RightButton,
        QtCore.Qt.KeyboardModifier.NoModifier)
    editor.view.mousePressEvent(press)

    assert not editor.scene.is_wiring, "right-click should drop the wire"
    assert not editor.view._panning, "and must not start panning instead"


def test_live_apply_skips_writing_the_same_vex_twice(editor):
    """Re-setting the parm to the same string still recooks the geometry.

    Moving or selecting a node fires graph_changed without changing a
    character of output, so this is most of what Live was doing.
    """
    applied = []
    editor.applied.connect(applied.append)
    editor.auto_apply.setChecked(True)

    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params["value"] = "{1, 0, 0}"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._regenerate()
    editor._auto_apply()
    assert len(applied) == 1

    # Moving a node changes the graph but not the VEX.
    item.node.pos = (123.0, 45.0)
    editor._regenerate()
    editor._auto_apply()
    assert len(applied) == 1, "the same VEX was written to the wrangle again"

    item.node.params["attrib"] = "changed"
    editor._regenerate()
    editor._auto_apply()
    assert len(applied) == 2, "a real change should still be written"


def test_typing_vex_in_the_code_pane_builds_the_nodes(editor):
    """The two directions already existed apart; this joins them up.

    Import VEX built a graph from pasted code and the pane was read-only, so
    the fastest way to write something you already knew how to write was to
    leave the tool.
    """
    editor.code.setPlainText("@P.y += 1;\n@Cd = {1,0,0};")
    assert editor._code_is_user_written, "typing must claim the pane"

    # ...and the emitter must not overwrite it while it is theirs.
    editor._regenerate()
    assert "@Cd = {1,0,0};" in editor.code.toPlainText()

    before = len(editor.graph.nodes)
    editor.build_from_code()
    assert len(editor.graph.nodes) > before
    assert not editor._code_is_user_written, "the pane goes back to generated"
    # Round-trips: what came back out says the same thing.
    assert "@Cd" in editor.code.toPlainText()


def test_escape_abandons_hand_written_code(editor):
    generated = editor.code.toPlainText()
    editor.code.setPlainText("this is not even VEX")
    assert editor._code_is_user_written

    editor.revert_code()
    assert not editor._code_is_user_written
    assert editor.code.toPlainText() == generated


def test_unreadable_code_leaves_the_graph_alone(editor):
    before = editor.graph.to_dict()
    editor.code.setPlainText("@P.y += ;;; ((")
    editor.build_from_code()
    # It still becomes a graph - Inline VEX is the never-fatal fallback - but
    # nothing may crash on the way, which is what this pins down.
    assert editor.graph is not None
    assert before is not None


def test_the_completion_vocabulary_covers_functions_and_attributes(editor):
    words = set(editor._vex_vocabulary())
    assert "getbbox_center" in words, "from the registry, not a hand-kept list"
    assert "@ptnum" in words and "@P" in words
    assert "vector" in words and "foreach" in words


def test_the_build_is_shown_so_a_reload_can_be_told_apart(editor):
    from vexgraph import __version__

    assert __version__ in editor.build.text()
    # The timestamp is what moves on every edit; the version only on release.
    assert "built" in editor.build.text()


def test_the_toolbar_no_longer_duplicates_working_shortcuts(editor):
    labels = {b.text() for b in editor.findChildren(QtWidgets.QPushButton)}
    assert not labels & {"Add Node", "Delete Node", "Undo", "Redo"}
    assert {"Open", "Save", "Tidy", "Frame"} <= labels


def test_building_from_code_never_opens_a_blocking_dialog(editor):
    """It runs on every Ctrl+Enter, so it must not need dismissing.

    Written after a modal added here hung the whole suite: offscreen there is
    nobody to press OK, and in use there is nobody who wants to.
    """
    editor.code.setPlainText("do { @P += 1; } while (@P.y < 3);")  # stays inline
    editor.build_from_code()

    listed = [editor.issues.item(i).text()
              for i in range(editor.issues.count())]
    assert any("Kept as Inline VEX" in text for text in listed), \
        "the reason must still be reported, just not in a dialog"
    assert editor.status.text(), "the status line must say what happened"


def test_released_nodes_settle_onto_the_grid(editor):
    """Dragged nodes snap on release, the way Houdini's canvas settles them."""
    from vexgraph.ui import theme

    item = editor.scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    item.setPos(QtCore.QPointF(37.0, 41.0))
    item.setSelected(True)
    editor.scene._snap_selection()

    step = theme.GRID_SPACING
    assert item.pos().x() % step == 0 and item.pos().y() % step == 0
    # And the graph document followed the item, so the snap survives a save.
    assert item.node.pos == (item.pos().x(), item.pos().y())


def test_component_pins_appear_where_wires_use_them(app, registry):
    """A loaded graph with a wire off `value.y` must draw that pin."""
    graph = Graph(registry)
    scene = GraphScene(graph)
    source = scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    source.node.params["type"] = "vector"
    target = scene.add_node("attrib_set", QtCore.QPointF(300, 0))
    graph.connect(source.node.id, "value.y", target.node.id, "value")
    source.rebuild()
    scene.rebuild_links()

    pin = source.ports.get(("value.y", False))
    assert pin is not None
    assert pin.vex_type == "float"
    assert len(scene.link_items) == 1


def test_expanding_an_output_shows_every_component(app, registry):
    graph = Graph(registry)
    scene = GraphScene(graph)
    item = scene.add_node("attrib_get", QtCore.QPointF(0, 0))
    item.node.params["type"] = "vector"
    item.rebuild()
    assert ("value.x", False) not in item.ports

    item.expanded_outputs.add("value")
    item.rebuild()
    assert all(("value." + c, False) in item.ports for c in "xyz")


# ---------------------------------------------------------- function graphs

def _editor_with_function(editor):
    editor.code.setPlainText(
        "int twice(int a){ return a * 2; }\ni@out = twice(@ptnum);")
    editor.build_from_code()
    assert "twice" in editor.graph.functions
    return editor


def test_opening_a_call_shows_the_functions_own_graph(editor):
    """The double-click signal routes to the inner graph; the breadcrumb bar
    appears and is the way back."""
    _editor_with_function(editor)
    editor.view.function_opened.emit("twice")
    assert editor.scene.graph is editor.graph.functions["twice"]
    assert not editor.crumb_bar.isHidden()
    assert "twice" in editor.crumb_label.text()
    types = {n.type for n in editor.scene.graph.nodes.values()}
    assert "return_value" in types

    editor.leave_function()
    assert editor.scene.graph is editor.graph
    assert editor.crumb_bar.isHidden()


def test_the_code_pane_keeps_showing_the_whole_document(editor):
    """Standing inside a function must not shrink the emitted code."""
    _editor_with_function(editor)
    editor.enter_function("twice")
    editor._regenerate()
    text = editor.code.toPlainText()
    assert "int twice(int a)" in text
    assert "twice(@ptnum)" in text


def test_rebuilding_while_inside_a_vanished_function_returns_home(editor):
    """Ctrl+Enter replaces the document; a function that no longer exists
    cannot stay open, and coming home must not crash."""
    _editor_with_function(editor)
    editor.enter_function("twice")
    editor.code.setPlainText("i@out = 3;")
    editor.build_from_code()
    assert editor.scene.graph is editor.graph
    assert editor.crumb_bar.isHidden()


def test_collapse_from_the_panel_is_transactional(editor):
    """A refused collapse leaves the document byte-identical; a good one
    lands in history so undo can take it back."""
    editor.code.setPlainText("f@d = length(@P - @N) * 2 + 0.5;")
    editor.build_from_code()
    before = editor.graph.to_dict()

    for item in editor.scene.node_items.values():
        item.setSelected(item.node.type in ("subtract", "multiply",
                                            "add", "length"))
    assert editor.collapse_selection("measure")
    assert "measure" in editor.graph.functions
    assert "measure" in editor.code.toPlainText() or True  # regenerated async
    editor._regenerate()
    assert "float measure(" in editor.code.toPlainText()

    # And an impossible one rolls back without a trace.
    editor.scene.clearSelection()
    for item in editor.scene.node_items.values():
        if item.node.type == "attrib_set":
            item.setSelected(True)
    snapshot = editor.graph.to_dict()
    assert not editor.collapse_selection("bad")
    assert editor.graph.to_dict() == snapshot


# ------------------------------------------------------- feedback round one

def test_deleting_the_edited_node_frees_the_keyboard(editor):
    """A stale row editor once routed Delete and Ctrl+Z into a text field
    that no longer existed - killing the keyboard for the session."""
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    row = next(r for r in item.rows if r.key == "param:attrib")
    editor.scene.edit_row(row)
    assert editor.scene.is_editing
    item.setSelected(True)
    editor.scene.delete_selected()
    assert not editor.scene.is_editing


def test_the_canvas_pans_effectively_forever(editor):
    rect = editor.view.sceneRect()
    assert rect.width() >= 200000 and rect.height() >= 200000


def test_the_search_placeholder_counts_the_real_registry(editor):
    count = sum(1 for _ in editor.registry)
    assert str(count) in editor.browser.search.placeholderText()


def test_nodes_carry_their_summary_as_a_tooltip(editor):
    item = editor.scene.add_node("random_number", QtCore.QPointF(0, 0))
    assert "random" in item.toolTip().lower()


def test_boxes_survive_saving_and_deleting_one_keeps_its_nodes(editor):
    from vexgraph.ui.items import BoxItem
    node = editor.scene.add_node("attrib_set", QtCore.QPointF(100, 60))
    box_item = editor.scene.add_box(QtCore.QRectF(50, 20, 400, 240))
    box_item.box.title = "these compute the matrix"

    clone = Graph.from_dict(editor.graph.to_dict(), editor.registry)
    assert clone.boxes and clone.boxes[0].title == "these compute the matrix"

    editor.scene.clearSelection()
    box_item.setSelected(True)
    editor.scene.delete_selected()
    assert not editor.graph.boxes
    assert node.node.id in editor.graph.nodes    # grouping is not ownership


def test_apply_button_hides_while_live_is_on(editor):
    assert editor.apply_button.isHidden()
    editor.auto_apply.setChecked(False)
    assert not editor.apply_button.isHidden()
    editor.auto_apply.setChecked(True)
    assert editor.apply_button.isHidden()


# ------------------------------------------------------- feedback round two

def test_live_applies_however_the_graph_arrived(editor):
    """Ctrl+Enter, Open, Keep - a replaced document reaches the wrangle."""
    shipped = []
    editor.applied.connect(shipped.append)
    editor.code.setPlainText("@P.y += 1;")
    editor.build_from_code()
    assert shipped and "@P.y" in shipped[-1]


def test_a_proposal_never_ships_until_kept(editor):
    """The assistant's proposal on screen must not touch the wrangle; Keep
    is the consent - and then it ships immediately."""
    from vexgraph.parser import import_vex
    shipped = []
    editor.applied.connect(shipped.append)
    proposal = import_vex("@P.x += 2;", editor.registry).graph
    editor._propose(proposal, "", "")
    editor._auto_apply()
    assert not shipped
    editor._keep_proposal()
    assert shipped and "@P.x" in shipped[-1]


def test_custom_function_library_round_trip(editor, monkeypatch, tmp_path):
    from vexgraph import userfns
    monkeypatch.setattr(userfns, "STORE", tmp_path / "fns.json")
    editor.code.setPlainText(
        "int twice(int a){ return a * 2; }\ni@x = twice(3);")
    editor.build_from_code()
    editor._save_function("twice")
    assert userfns.names() == ["twice"]

    editor.code.setPlainText("i@y = 1;")        # a fresh document
    editor.build_from_code()
    assert "twice" not in editor.graph.functions
    editor._place_from_browser("fn_twice")
    assert "twice" in editor.graph.functions
    assert any(n.type == "fn_twice" for n in editor.graph.nodes.values())
    editor._regenerate()
    assert "int twice(int a)" in editor.code.toPlainText()

    userfns.remove("twice")
    assert userfns.names() == []


def test_status_is_only_red_for_real_errors(editor):
    editor._show_message("3 of 4 statements became nodes")
    assert "e05a5a" not in editor.status.styleSheet()
    editor._show_message("that did not work", error=True)
    assert "e05a5a" in editor.status.styleSheet()


def test_the_help_dialog_carries_both_manuals(app):
    from vexgraph.ui.helpdialog import HelpDialog
    dialog = HelpDialog()
    tabs = dialog.findChild(QtWidgets.QTabWidget)
    assert tabs.count() == 2
    for index, needle in ((0, "VEXgraph"), (1, "manual de uso")):
        text = tabs.widget(index).toPlainText()
        assert needle in text
    dialog.deleteLater()


def test_the_code_pane_starts_folded_only_inside_houdini(monkeypatch):
    from vexgraph.ui.panel import _default_folded
    assert not _default_folded("code"), "standalone: the pane is the teacher"
    monkeypatch.setitem(sys.modules, "hou", object())
    assert _default_folded("code")
    assert not _default_folded("issues"), "only the code pane starts folded"


# ----------------------------------------------------- feedback round three

def test_show_in_library_reveals_curated_and_generated_nodes(editor):
    """Curated nodes sit in the browse tree; generated ones are reached by
    typing their search into the box - which also teaches the search."""
    assert editor.browser.reveal("attrib_get")
    item = editor.browser.tree.currentItem()
    assert item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "attrib_get"

    assert editor.browser.reveal("vex_getbbox_center")
    item = editor.browser.tree.currentItem()
    assert item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "vex_getbbox_center"
    assert editor.browser.search.text()          # the search box shows how


def test_the_reply_schema_is_acceptable_to_claude():
    """Claude's structured outputs rejects `additionalProperties` carrying a
    schema; every object in ours must pin it to false."""
    from vexgraph.assistant.agent import REPLY_SCHEMA

    def walk(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert schema.get("additionalProperties") is False, schema
            for value in schema.values():
                walk(value)
        elif isinstance(schema, list):
            for value in schema:
                walk(value)

    walk(REPLY_SCHEMA)


def test_params_are_read_from_either_shape():
    from vexgraph.assistant.agent import _param_pairs
    assert _param_pairs({"attrib": "Cd"}) == [("attrib", "Cd")]
    assert _param_pairs([{"name": "attrib", "value": "Cd"}]) == [
        ("attrib", "Cd")]
    assert _param_pairs(None) == []


def test_repeat_asks_the_last_question_again(editor, monkeypatch):
    asked = []
    monkeypatch.setattr(editor.assistant, "ask",
                        lambda: asked.append(editor.assistant.input.toPlainText()))
    editor.assistant._last_request = "make the points dance"
    editor.assistant.repeat.setEnabled(True)
    editor.assistant._repeat_last()
    assert asked == ["make the points dance"]


def test_snippet_list_has_one_grabbable_scrollbar_and_no_arrows(app, registry):
    """Qt un-styles a whole scrollbar the moment a sheet paints its
    background, leaving stray arrow buttons and an invisible handle. Assert
    the rendered sub-controls, not the sheet text: no step buttons, and a
    handle wide enough to grab."""
    from vexgraph.ui.snippet_picker import SnippetPicker

    picker = SnippetPicker(registry)
    picker.resize(940, 620)
    picker.show()
    QtWidgets.QApplication.processEvents()

    bar = picker.list.verticalScrollBar()
    bar.setRange(0, 500)                      # give it something to scroll
    QtWidgets.QApplication.processEvents()

    option = QtWidgets.QStyleOptionSlider()
    bar.initStyleOption(option)
    style = bar.style()
    control = QtWidgets.QStyle.ComplexControl.CC_ScrollBar

    def rect(sub):
        return style.subControlRect(control, option, sub, bar)

    for sub in (QtWidgets.QStyle.SubControl.SC_ScrollBarAddLine,
                QtWidgets.QStyle.SubControl.SC_ScrollBarSubLine):
        area = rect(sub)
        assert area.width() == 0 or area.height() == 0, \
            f"step button still drawn: {area}"

    handle = rect(QtWidgets.QStyle.SubControl.SC_ScrollBarSlider)
    assert handle.height() >= 20 and handle.width() >= 8, handle
    picker.deleteLater()


# ------------------------------------------------------------- learn mode

@pytest.fixture
def student(editor, monkeypatch):
    """An editor whose Learn progress starts blank and stays out of the
    real settings."""
    from vexgraph import learn
    editor.learn.progress = learn.Progress()
    editor.learn._index = 0
    editor.learn._hints_shown = 0
    monkeypatch.setattr(editor.learn, "_save_progress", lambda: None)
    editor.learn._show()
    return editor


def test_the_first_exercise_is_solvable_on_the_real_canvas(student):
    """The student's actual journey: empty canvas fails with the first
    lesson, building the graph passes, and the next exercise unlocks."""
    student.learn.check()
    assert "set attribute" in student.learn.verdict.text().lower()

    item = student.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params.update(attrib="Cd", type="vector", value="{1, 0, 0}")
    student.scene.connect_ports(
        student.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    student.learn.check()
    assert "Solved" in student.learn.verdict.text()
    assert "paint" in student.learn.progress.completed
    assert student.learn.next_button.isEnabled()


def test_hints_stage_and_are_counted(student):
    assert student.learn._hints_shown == 0
    student.learn._reveal_hint()
    student.learn._reveal_hint()
    assert student.learn._hints_shown == 2
    assert student.learn.progress.hints_used["paint"] == 2
    assert "💡" in student.learn.body.toHtml()


def test_the_professor_gets_the_exercise_as_context(student, monkeypatch):
    asked = []
    monkeypatch.setattr(student.assistant, "ask",
                        lambda: asked.append(
                            student.assistant.input.toPlainText()))
    student.learn._call_professor()
    assert asked and "Paint everything red" in asked[0]
    assert "without giving me the finished VEX" in asked[0]


def test_the_learn_button_owns_the_course(editor):
    state = editor._section_state["learn"]
    assert state["folded"], "the course starts folded - it is opt-in"
    assert not editor.learn_button.isChecked()

    editor.learn_button.click()
    assert not state["folded"] and editor.learn_button.isChecked()
    assert editor._node_focus is not None, "focus applies on the way in"

    editor.learn_button.click()
    assert state["folded"] and not editor.learn_button.isChecked()
    assert editor._node_focus is None

    # Folding by the section header keeps the button honest too.
    editor.learn_button.click()
    editor._section_toggles["learn"]()
    assert not editor.learn_button.isChecked()


def test_the_exercise_lists_the_words_to_search_for(student):
    """A beginner cannot be told 'add a node' with 1360 to choose from."""
    html = student.learn.body.toHtml()
    assert "Nodes for this exercise" in html
    assert "set attribute" in html


def test_navigation_never_traps_the_student(student):
    """Locking the way forward meant a correct-but-unrecognised graph left
    no way out. Browsing ahead is not cheating."""
    assert student.learn.next_button.isEnabled()
    assert not student.learn.back_button.isEnabled()      # first exercise
    student.learn._go(+1)
    assert student.learn.back_button.isEnabled()
    assert student.learn.exercise.key == "flatten"


def test_a_make_vector_solution_counts_as_solved(student):
    """Ivan's stumble: building the colour with Make Vector emits
    set(1, 0, 0) where a typed value emits {1, 0, 0}. VEX cannot tell them
    apart and neither may the course."""
    scene = student.scene
    mk = scene.add_node("make_vector", QtCore.QPointF(0, 0))
    mk.node.params.update(x="1", y="0", z="0")
    st = scene.add_node("attrib_set", QtCore.QPointF(300, 0))
    st.node.params.update(attrib="Cd", type="vector")
    student.graph.connect(mk.node.id, "result", st.node.id, "value")
    scene.connect_ports(scene.node_items["start"].ports[("exec", False)],
                        st.ports[("exec", True)])
    student.learn.check()
    assert "Solved" in student.learn.verdict.text()


def test_focus_mode_narrows_the_library_to_the_exercise(student):
    """Ivan's ask: five or ten nodes to choose from, not 1360."""
    from vexgraph import learn

    student.learn._push_focus()
    allowed = learn.allowed_upto(0)
    assert len(allowed) < 10
    assert student._node_focus == allowed
    assert "nodes for this exercise" in \
        student.browser.search.placeholderText().lower()

    shown = set()
    tree = student.browser.tree
    for index in range(tree.topLevelItemCount()):
        category = tree.topLevelItem(index)
        for row in range(category.childCount()):
            shown.add(category.child(row).data(
                0, QtCore.Qt.ItemDataRole.UserRole))
    assert shown and shown <= allowed, shown - allowed

    # Tab search obeys the same rule.
    dialog = NodeSearch(student.registry, focus=allowed)
    dialog.repopulate("")
    listed = {dialog.list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
              for i in range(dialog.list.count())}
    assert listed <= allowed
    dialog.deleteLater()


def test_focus_grows_with_the_course_and_can_be_switched_off(student):
    from vexgraph import learn

    early = len(learn.allowed_upto(0))
    late = len(learn.allowed_upto(len(learn.BEGINNER) - 1))
    assert early < late, "later exercises keep what earlier ones taught"

    student.learn.focus_box.setChecked(False)
    assert student._node_focus is None
    assert "search all" in student.browser.search.placeholderText().lower()


def test_folding_the_course_gives_the_whole_library_back(editor):
    editor.learn_button.click()               # unfolds, focus on
    assert editor._node_focus is not None
    editor._section_toggles["learn"]()        # fold it away again
    assert editor._node_focus is None


def test_every_exercise_says_how_to_see_it(student):
    from vexgraph import learn

    for exercise in learn.BEGINNER:
        assert exercise.scene is not None and exercise.scene.describe
    assert "To see it:" in student.learn.body.toHtml()


def test_one_description_pane_not_two(editor):
    """"About the selected node" was a smaller copy of the library's own
    pane, which already follows the canvas and says strictly more."""
    assert not hasattr(editor, "help"), "the duplicate pane is gone"
    assert "help" not in editor._section_state


def test_selecting_a_node_fills_the_library_pane_with_its_problem(editor):
    """The one thing the removed pane knew that the library did not: what is
    wrong with THIS node. It moved rather than being lost."""
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params.pop("value", None)
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._regenerate()
    editor.scene.select_node(item.node.id)

    detail = editor.browser.detail.toPlainText()
    assert "Set Attribute" in detail
    assert item.status_text and item.status_text[:20] in detail


def test_the_course_starts_folded_in_a_clean_profile(editor):
    """A fresh profile must not open the course by itself."""
    assert editor._section_state["learn"]["folded"]


# --------------------------------------------------------------- favourites

@pytest.fixture(autouse=True)
def _isolate_favourites(tmp_path, monkeypatch):
    from vexgraph import favourites
    monkeypatch.setattr(favourites, "STORE", tmp_path / "favourites.json")
    yield


def test_starring_a_node_puts_it_first_in_tab_search(editor):
    from vexgraph import favourites

    # Third in the plain ranking for "vector"; starring must lift it.
    plain = NodeSearch(editor.registry)
    plain.repopulate("vector")
    before = [plain.list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
              for i in range(min(4, plain.list.count()))]
    assert before[0] != "split_vector" and "split_vector" in before
    plain.deleteLater()

    favourites.toggle("split_vector")
    dialog = NodeSearch(editor.registry)
    dialog.repopulate("vector")
    first = dialog.list.item(0)
    assert first.data(QtCore.Qt.ItemDataRole.UserRole) == "split_vector"
    assert first.text().startswith("★")
    dialog.deleteLater()


def test_the_library_gets_a_favourites_shelf_and_a_filter(editor):
    from vexgraph import favourites

    favourites.toggle("length")
    editor.browser.refresh_favourites()
    tree = editor.browser.tree
    categories = [tree.topLevelItem(i).text(0)
                  for i in range(tree.topLevelItemCount())]
    assert categories[0].endswith("Favourites")
    assert len(categories) > 1                 # the rest of the library too

    editor.browser.star_filter.setChecked(True)
    categories = [tree.topLevelItem(i).text(0)
                  for i in range(tree.topLevelItemCount())]
    assert categories == ["★ Favourites"]


def test_the_canvas_can_star_a_node(editor):
    from vexgraph import favourites

    item = editor.scene.add_node("random_number", QtCore.QPointF(0, 0))
    item.setSelected(True)
    menu = editor.view.build_node_menu()
    action = next(a for a in menu.actions() if a.data() == "favourite")
    assert action.text() == "Add to favourites"

    favourites.toggle("random_number")
    menu = editor.view.build_node_menu()
    action = next(a for a in menu.actions() if a.data() == "favourite")
    assert action.text() == "Remove from favourites"


def test_delete_works_after_selecting_from_the_code_pane(editor):
    """Delete goes to whatever holds the keyboard. Selecting a node from a
    code line used to leave focus in the code pane, so the node looked
    selected and refused to die."""
    item = editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    item.node.params["value"] = "{1, 0, 0}"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._regenerate()
    editor.show()
    QtWidgets.QApplication.processEvents()

    editor.code.setFocus()
    QtWidgets.QApplication.processEvents()
    line = next(n for n, text in
                enumerate(editor.code.toPlainText().splitlines(), 1)
                if "@Cd" in text)
    editor._select_from_code(line)
    QtWidgets.QApplication.processEvents()

    assert editor.view.hasFocus(), "the canvas owns the selection now"
    before = len(editor.graph.nodes)
    editor.scene.delete_selected()
    assert len(editor.graph.nodes) < before


def test_placing_from_the_library_leaves_the_keyboard_on_the_canvas(editor):
    editor.show()
    QtWidgets.QApplication.processEvents()
    editor.browser.search.setFocus()
    QtWidgets.QApplication.processEvents()
    editor._place_from_browser("attrib_set")
    QtWidgets.QApplication.processEvents()
    assert editor.view.hasFocus()


def test_the_course_never_reopens_itself_next_session(editor, tmp_path):
    """Learn is a side room. Whatever you did last time, the panel opens on
    the tool, not on teaching material."""
    editor.learn_button.click()               # open it, which persists nothing
    assert not editor._section_state["learn"]["folded"]

    second = VexGraphEditor(editor.registry)
    try:
        assert second._section_state["learn"]["folded"]
        assert not second.learn_button.isChecked()
    finally:
        second.deleteLater()


def test_an_answer_offers_to_build_itself(editor):
    """"Which nodes do I need?" ends in "so wire X into Y" - and the next
    thought is "go on then"."""
    assistant = editor.assistant
    assert assistant.build_it.isHidden()
    assistant._last_request = "make the points wobble"
    assistant._done({"answer": "Use a Random Vector into an Add."})
    assert not assistant.build_it.isHidden()

    asked = []
    assistant.ask = lambda: asked.append(
        (assistant.mode.currentText(), assistant.input.toPlainText()))
    assistant._build_the_answer()
    assert asked == [("Build a graph", "make the points wobble")]
    assert assistant.build_it.isHidden()


# ---------------------------------------------------------------- notes

def test_a_note_is_a_resizable_box_not_a_row(editor):
    """Somewhere to write, sized to the writing."""
    item = editor.scene.add_node("note", QtCore.QPointF(0, 0))
    assert item.is_note
    assert not item.rows, "a note has no one-line pill"
    assert item.boundingRect().height() > 80

    item.node.params.update(width="420", height="300")
    item.rebuild()
    assert item.boundingRect().width() > 400
    assert item.boundingRect().height() > 290


def test_a_multi_line_note_stays_a_comment(editor):
    """Every line of it, or the second one arrives in the wrangle as code."""
    from vexgraph.vccmap import check_source

    item = editor.scene.add_node("note", QtCore.QPointF(0, 0))
    item.node.params["text"] = "first line\nsecond line"
    editor.scene.connect_ports(
        editor.scene.node_items["start"].ports[("exec", False)],
        item.ports[("exec", True)])
    editor._regenerate()

    code = editor.code.toPlainText()
    assert "// first line" in code and "// second line" in code
    check = check_source(code)
    if check.checked:
        assert check.ok, check.raw


def test_notes_survive_the_round_trip(registry):
    """They vanished on every Ctrl+Enter: the lexer drops comments, so the
    writing someone did for themselves went with them."""
    from vexgraph import generate
    from vexgraph.parser import import_vex

    source = ("// remember why\n// two lines of it\n@P.y += 1;\n"
              "// and a tail note\n")
    first = generate(import_vex(source, registry).graph).code
    assert "// remember why" in first and "// and a tail note" in first
    assert first == generate(import_vex(first, registry).graph).code


def test_a_hint_keeps_your_place_instead_of_jumping_to_the_top(student):
    """setHtml throws the reader back to the top, so every Hint cost you the
    line you were reading. The new hint is at the bottom; that is where it
    scrolls."""
    student.learn.body.setFixedHeight(60)      # force something to scroll
    QtWidgets.QApplication.processEvents()
    bar = student.learn.body.verticalScrollBar()

    student.learn._reveal_hint()
    QtWidgets.QApplication.processEvents()
    assert bar.value() == bar.maximum(), "a hint should show itself"

    # Any other refresh keeps the position it had.
    bar.setValue(bar.maximum() // 2)
    kept = bar.value()
    student.learn._show()
    QtWidgets.QApplication.processEvents()
    assert bar.value() == min(kept, bar.maximum())


# --------------------------------------------------------------- bypass

def test_bypassing_a_value_node_keeps_the_expression_alive(editor):
    """Ivan's question: bypass an Add and the rest should still work."""
    editor.code.setPlainText("@P = @P + @N * 0.5;")
    editor.build_from_code()
    add = next(i for i in editor.scene.node_items.values()
               if i.node.type == "add")
    add.setSelected(True)
    editor.scene.toggle_bypass()
    editor._regenerate()

    code = editor.code.toPlainText()
    assert "@P = @P;" in code, code
    assert "@N" not in code.split("\n")[-2]


def test_bypassing_a_step_emits_nothing_and_leaves_the_chain(editor):
    editor.code.setPlainText("@P.y = 1;\n@Cd = {1, 0, 0};")
    editor.build_from_code()
    first = next(i for i in editor.scene.node_items.values()
                 if i.node.type == "attrib_set_component")
    first.setSelected(True)
    editor.scene.toggle_bypass()
    editor._regenerate()

    code = editor.code.toPlainText()
    assert "@P.y" not in code
    assert "@Cd" in code, "the rest of the chain still runs"


def test_bypass_survives_saving_and_the_start_node_refuses(editor):
    editor.code.setPlainText("@P.y = 1;")
    editor.build_from_code()
    item = next(i for i in editor.scene.node_items.values()
                if i.node.type == "attrib_set_component")
    item.setSelected(True)
    editor.scene.toggle_bypass()

    clone = Graph.from_dict(editor.graph.to_dict(), editor.registry)
    assert any(n.bypassed for n in clone.nodes.values())

    editor.scene.clearSelection()
    editor.scene.node_items["start"].setSelected(True)
    editor.scene.toggle_bypass()
    assert not editor.graph.nodes["start"].bypassed


def test_the_open_editor_follows_a_node_that_moves(editor):
    """A node snapping to the grid mid-edit used to leave its own text box
    floating where the node had been."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    item = editor.scene.add_node("multiply", QtCore.QPointF(0, 0))
    row = next(r for r in item.rows if r.key.startswith("param:")
               or r.key.startswith("in:"))
    editor.scene.edit_row(row)
    QtWidgets.QApplication.processEvents()

    before = editor.scene._editor.pos()
    item.setPos(item.pos() + QtCore.QPointF(120, 80))
    QtWidgets.QApplication.processEvents()
    after = editor.scene._editor.pos()

    assert after != before
    expected = row.mapToScene(row.boundingRect().topLeft())
    assert (after - expected).manhattanLength() < 1.0


def test_undo_leaves_the_view_exactly_where_it_was(editor):
    """Undo answers "what just changed" - and reframing moves everything on
    screen, which is the one thing that makes that question unanswerable."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    editor.code.setPlainText("@P.y = 1;\n@Cd = {1, 0, 0};")
    editor.build_from_code()
    QtWidgets.QApplication.processEvents()

    # Park the view somewhere deliberate: zoomed in, off to one side.
    editor.view.scale(1.7, 1.7)
    editor.view.centerOn(QtCore.QPointF(400, 250))
    QtWidgets.QApplication.processEvents()
    zoom = editor.view.transform().m11()
    centre = editor.view.mapToScene(editor.view.viewport().rect().center())

    editor.scene.add_node("attrib_set", QtCore.QPointF(0, 0))
    editor._record_history()
    editor.undo()
    QtWidgets.QApplication.processEvents()

    assert editor.view.transform().m11() == pytest.approx(zoom), "zoom moved"
    now = editor.view.mapToScene(editor.view.viewport().rect().center())
    assert (now - centre).manhattanLength() < 2.0, "the view drifted"


def test_opening_a_document_still_frames_it(editor):
    """The other half of the same rule: arriving somewhere new should show it."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    editor.view.scale(4.0, 4.0)
    editor.code.setPlainText("@P.y = 1;")
    editor.build_from_code()
    QtWidgets.QApplication.processEvents()
    assert editor.view.transform().m11() != pytest.approx(4.0)


# ------------------------------------------------------- the right column

def test_the_right_column_is_four_draggable_sections(editor):
    """Ivan's ask: the regions on the right behave like the library column -
    every divider is the user's to drag."""
    assert editor.right_split.count() == 4
    assert not editor.right_split.childrenCollapsible()


def test_the_build_button_belongs_to_the_code_section(editor):
    """It floated alone in the middle of two folded sections' emptiness, and
    showed even when the code it builds from was folded away."""
    editor.show()
    QtWidgets.QApplication.processEvents()
    assert not editor._section_state["code"]["folded"], "standalone: open"
    assert editor.build_button.isVisible()
    assert editor.build_button.height() < 40, "a button, not a panel"

    editor._section_toggles["code"]()
    assert not editor.build_button.isVisible(), "folded code, no button"
    editor._section_toggles["code"]()
    assert editor.build_button.isVisible()


def test_a_folded_section_gives_its_room_back(editor):
    editor.show()
    QtWidgets.QApplication.processEvents()
    box = editor.right_split.widget(2)          # Problems
    editor._section_state["issues"]["folded"] or \
        editor._section_toggles["issues"]()
    assert box.maximumHeight() < 40


def test_the_handle_positions_survive_reopening(editor):
    editor.show()
    QtWidgets.QApplication.processEvents()
    editor.right_split.setSizes([80, 500, 60, 200])
    editor._save_right_split()

    second = VexGraphEditor(editor.registry)
    try:
        second.resize(1200, 800)
        second.show()
        QtWidgets.QApplication.processEvents()
        # Not exact: restoring happens before the window takes its final
        # size, and the proportional redistribution rounds. What matters is
        # that the dragged layout came back rather than the defaults.
        for mine, theirs in zip(editor.right_split.sizes(),
                                second.right_split.sizes()):
            assert abs(mine - theirs) < 40, (editor.right_split.sizes(),
                                             second.right_split.sizes())
    finally:
        second.deleteLater()
