"""Houdini's own help and icons, read from the local install.

These skip rather than fail without Houdini: the tool has to keep working when
the archives are not there, and a machine without them is a valid place to run
the suite.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import help as vexhelp                       # noqa: E402
from vexgraph.nodedefs import default_registry             # noqa: E402


@pytest.fixture(scope="module")
def registry():
    return default_registry()


needs_help = pytest.mark.skipif(vexhelp.houdini_help_archive() is None,
                                reason="no local Houdini help archive")


def test_a_node_names_the_vex_function_it_is_built_on(registry):
    """Derived from the template, so it cannot drift from what is emitted."""
    assert registry.require("fit_range").vex_function == "fit"
    assert registry.require("square_root").vex_function == "sqrt"
    assert registry.require("rotate_matrix").vex_function == "rotate"
    assert registry.require("vex_xyzdist").vex_function == "xyzdist"


def test_a_type_constructor_is_not_mistaken_for_a_function(registry):
    """`make_vector` emits set(...), which has no help page of its own."""
    assert registry.require("make_vector").vex_function == ""
    assert registry.require("note").vex_function == ""


@needs_help
def test_help_is_parsed_into_parts_not_dumped_raw():
    page = vexhelp.page("fit")
    assert page is not None
    assert "range" in page.summary.lower()
    assert len(page.usages) >= 2, "both the float and vector forms are documented"
    assert page.examples, "fit ships a worked example"
    assert "clamp" in [label for label, _ in page.related]
    assert page.url.endswith("/vex/functions/fit.html")


def test_code_samples_inside_the_body_are_kept():
    """`foreach` documents its forms as code in the prose, not under @examples.

    Only reading the @examples section dropped every sample on that page - the
    most useful part of it.
    """
    page = vexhelp.page("foreach")
    assert page is not None
    assert len(page.examples) >= 3, "the forms shown in the body were lost"
    kinds = [kind for kind, _ in page.blocks]
    assert "code" in kinds and "prose" in kinds
    assert kinds.index("prose") < kinds.index("code", kinds.index("prose")), \
        "the prose introducing a sample must stay before it"


def test_a_topic_link_is_kept_not_only_function_links():
    """`[/vex/arrays]` is a see-also too; reading only [Vex:x] dropped it."""
    page = vexhelp.page("foreach")
    labels = [label for label, _ in page.related]
    assert "Arrays" in labels
    assert all(url.startswith("https://www.sidefx.com/docs/houdini/")
               for _, url in page.related)


def test_emphasis_and_headings_become_formatting_not_punctuation():
    html = vexhelp.as_html(vexhelp.page("foreach"))
    prose = re.sub(r"<pre.*?</pre>", "", html, flags=re.S)
    # `==` is legal VEX so it is only checked outside code blocks.
    for marker in ("*copies*", "_position_", "== ", "= foreach ="):
        assert marker not in prose, f"{marker!r} was shown as literal text"
    assert "<b>copies</b>" in prose and "<i>position</i>" in prose


def test_code_is_coloured_the_same_way_the_code_pane_colours_it():
    html = vexhelp.as_html(vexhelp.page("fit"))
    assert "<span style='color:#b5cea8'>" in html, "numbers are not highlighted"


@needs_help
def test_markup_does_not_leak_into_what_is_shown():
    """The wiki syntax must be resolved, not printed at the user."""
    page = vexhelp.page("append")
    html = vexhelp.as_html(page)
    for marker in ("{{{", "}}}", "[Vex:", "#type:", "<<"):
        assert marker not in html, f"{marker!r} leaked into the rendered help"


@needs_help
def test_an_unknown_function_is_absent_rather_than_an_error():
    assert vexhelp.page("not_a_real_vex_function") is None
    assert vexhelp.page("") is None


@needs_help
def test_most_generated_nodes_can_show_help(registry):
    tier2 = [d for d in registry if d.tier == 2]
    covered = sum(1 for d in tier2 if vexhelp.page(d.vex_function))
    assert covered > len(tier2) * 0.6, f"only {covered}/{len(tier2)} had help"


def test_icons_are_only_mapped_to_real_houdini_icons():
    """A mapping typo would silently show nothing; catch it here instead."""
    from PySide6 import QtWidgets

    from vexgraph.ui import icons
    if not icons.available():
        pytest.skip("no local Houdini icon archive")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    missing = [node for node, vop in icons.VOP_ICONS.items()
               if icons.node_icon(node, 16) is None]
    assert not missing, f"mapped to icons that do not exist: {missing}"


def test_every_icon_mapping_names_a_node_that_exists(registry):
    from vexgraph.ui import icons
    unknown = [node for node in icons.VOP_ICONS if registry.get(node) is None]
    assert not unknown, f"icons mapped to nodes that are gone: {unknown}"


def test_releasing_a_model_names_it_and_leaves_everything_else(monkeypatch):
    """Release must be scoped to one model - never a server-wide reset."""
    from vexgraph.assistant import vram

    monkeypatch.setattr(vram, "loaded_models", lambda url=vram.OLLAMA_URL: [
        ("qwen3:32b", 20 * 1024 ** 3), ("gemma4:12b", 8 * 1024 ** 3)])
    sent = []

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, timeout=0):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse()

    monkeypatch.setattr(vram.urllib.request, "urlopen", fake_urlopen)

    message = vram.release("qwen3:32b")
    assert [p["model"] for p in sent] == ["qwen3:32b"], "released the wrong models"
    assert all(p["keep_alive"] == 0 for p in sent)
    assert "qwen3:32b" in message


def test_release_says_so_when_nothing_is_loaded(monkeypatch):
    from vexgraph.assistant import vram
    monkeypatch.setattr(vram, "loaded_models", lambda url=vram.OLLAMA_URL: [])
    assert "No local model" in vram.release()


def test_a_dead_ollama_does_not_raise(monkeypatch):
    """The meter is a convenience; it must never interrupt editing."""
    from vexgraph.assistant import vram

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(vram.urllib.request, "urlopen", boom)
    assert vram.loaded_models() == []


def test_control_flow_nodes_do_not_claim_a_vex_function(registry):
    """`if (...)` looks like a call, so If/Repeat pointed at 404 doc pages."""
    for node_type in ("if", "if_else", "break_if", "skip_if", "for_range"):
        assert registry.require(node_type).vex_function == "", (
            f"{node_type} claims to be a VEX function; its Docs link would 404")
    # foreach is the exception: VEX really does document it as a function.
    assert registry.require("foreach").vex_function == "foreach"


@needs_help
def test_no_node_offers_a_docs_link_to_a_page_that_does_not_exist(registry):
    """The archive is what the website is built from, so absent here is a 404."""
    bogus = [d.type for d in registry
             if d.vex_function and vexhelp.page(d.vex_function) is None]
    # Generated nodes are named for real functions, so any miss is a curated
    # node guessing at a name.
    curated = [t for t in bogus if not t.startswith("vex_")]
    assert not curated, f"these would link to a 404: {curated}"


def test_the_docs_button_looks_different_when_there_is_nothing_to_open(registry):
    """Disabled alone was too quiet to notice; it has to say what it is."""
    from PySide6 import QtWidgets

    from vexgraph.ui.browser import NodeBrowser
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    browser = NodeBrowser(registry)

    browser.describe("closest_surface_point")          # xyzdist(): documented
    assert browser.docs_button.isEnabled()
    documented_text = browser.docs_button.text()
    documented_style = browser.docs_button.styleSheet()
    assert "xyzdist" in documented_text, "the button should name the function"

    browser.describe("not_true")                        # an operator: no page
    assert not browser.docs_button.isEnabled()
    assert browser.docs_button.text() != documented_text
    assert browser.docs_button.styleSheet() != documented_style, \
        "the two states must be visually distinct, not only enabled/disabled"


def test_houdinis_name_for_a_function_finds_our_node(registry):
    """Someone who read the docs searches xyzdist, not 'closest point'."""
    for query, expected in (("xyzdist", "Closest Point On Surface"),
                            ("lerp", "Blend"),
                            ("rotate", "Rotate Matrix"),
                            ("fit", "Fit Range")):
        top = registry.search(query, limit=1)
        assert top and top[0].label == expected, (
            f"searching {query!r} gave {top[0].label if top else None!r}, "
            f"not {expected!r}")


@needs_help
def test_an_overload_suffix_still_finds_the_function_page(registry):
    """`ptransform_3` is our name for an overload; Houdini's page is ptransform.

    Not stripping it cost 386 nodes their documentation.
    """
    page = vexhelp.page("ptransform_3")
    assert page is not None
    assert page.name == "ptransform", "the label should name the real function"

    tier2 = [d for d in registry if d.tier == 2]
    covered = sum(1 for d in tier2 if vexhelp.page(d.vex_function))
    assert covered > len(tier2) * 0.98, f"only {covered}/{len(tier2)} had help"


@needs_help
def test_a_function_whose_name_really_ends_in_digits_is_not_stripped():
    """`norm_1` exists; stripping blindly would send it to the wrong page."""
    page = vexhelp.page("norm_1")
    assert page is not None and page.name == "norm_1"
