"""Reading ready-made snippets out of whatever is installed locally.

Nothing from OD's file is copied here - its licence forbids redistribution -
so the parser is tested against the format rather than against their content.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import default_registry, snippets                # noqa: E402
from vexgraph.parser import import_vex                         # noqa: E402

SAMPLE = """\
#
#   a header comment
#

groupexpression/snippet
    30% chance
    rand(@elemnum) < 0.3

pointwrangle/snippet
    Add Value to Y Position
    @P += {0,1,0};

attribwrangle/snippet
    Two Statements
    float d = 2.0;
    @pscale = d;

popspin/localexpression
    Spin It
    @w = {0,1,0};
"""


def test_the_format_is_read_into_named_snippets():
    found = snippets.parse(SAMPLE, source="sample.txt")
    assert [s.name for s in found] == [
        "30% chance", "Add Value to Y Position", "Two Statements", "Spin It"]
    assert found[0].code == "rand(@elemnum) < 0.3"
    assert found[2].code == "float d = 2.0;\n@pscale = d;", "indent not stripped"
    assert found[0].source == "sample.txt"


def test_a_trailing_colon_is_not_part_of_the_name():
    found = snippets.parse("pointwrangle/snippet\n    Named With Colon:\n    @P;\n")
    assert found[0].name == "Named With Colon"


def test_wrangle_snippets_are_separated_from_other_contexts():
    found = snippets.parse(SAMPLE)
    wrangles = [s for s in found if s.is_wrangle]
    others = [s for s in found if not s.is_wrangle]
    assert {s.name for s in wrangles} == {
        "30% chance", "Add Value to Y Position", "Two Statements"}
    assert [s.name for s in others] == ["Spin It"]
    assert others[0].category == "POP / particles"


def test_junk_is_skipped_rather_than_raising():
    """Someone else's file gains entries over time; it must not be brittle."""
    assert snippets.parse("") == []
    assert snippets.parse("no context line here\n    orphan code\n") == []
    assert snippets.parse("pointwrangle/snippet\n") == []      # title, no code


def test_a_snippet_opens_through_the_parser_not_as_text():
    """The point of the feature: a preset arrives as nodes you can read."""
    registry = default_registry()
    snippet = snippets.parse(SAMPLE)[1]          # Add Value to Y Position
    report = import_vex(snippet.code, registry)
    assert report.total >= 1
    assert len(report.graph.nodes) > 1, "nothing became a node"


@pytest.mark.skipif(not snippets.search_paths(),
                    reason="no snippet file installed on this machine")
def test_the_local_file_actually_parses():
    found = snippets.load()
    assert len(found) > 50, f"only {len(found)} snippets read"
    assert all(s.name and s.code for s in found)
    assert any(s.is_wrangle for s in found)


def test_nothing_from_the_licensed_file_is_committed():
    """OD's EULA forbids redistribution, so their content must stay out."""
    root = Path(__file__).resolve().parents[1]
    skip = {".venv", ".git", "__pycache__", ".pytest_cache"}
    for path in root.rglob("*.txt"):
        if skip & set(path.parts):
            continue
        assert "VEXpression" not in path.name, \
            f"{path} looks like a copy of the licensed snippet file"
