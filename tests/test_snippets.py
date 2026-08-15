"""Reading ready-made snippets out of whatever is installed locally.

Nothing from OD's file is copied here - its licence forbids redistribution -
so the parser is tested against the format rather than against their content.
"""

from __future__ import annotations

import base64
import json
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
    """OD's EULA forbids redistribution, so their content must stay out.

    Asks git what it tracks rather than scanning the disk. VEXgraph now
    *generates* a `VEXpressions.txt` - that is how its snippets reach Houdini's
    own wrangle menu - and it contains OD's content read from this machine. It
    is git-ignored, and this is the test that says so: checking filenames on
    disk would have to be loosened to allow it, while checking what git tracks
    is the question that actually matters and gets stricter instead.
    """
    import subprocess

    root = Path(__file__).resolve().parents[1]
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=root, text=True,
                                 capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no git here to ask")

    for name in tracked.stdout.splitlines():
        assert "VEXpression" not in name, \
            f"{name} is tracked and looks like the licensed snippet file"

    # And the file we do generate must be ignored, not merely absent today.
    generated = snippets.VEXPRESSIONS_EXPORT
    generated.parent.mkdir(parents=True, exist_ok=True)
    if not generated.exists():
        generated.write_text("# probe\n", encoding="utf8")
    status = subprocess.run(["git", "status", "--porcelain", "--", str(generated)],
                            cwd=root, text=True, capture_output=True, timeout=60)
    assert not status.stdout.strip(), \
        f"{generated} is visible to git; it must stay ignored"


JSON_SAMPLE = """{
  "111": {"author": "Someone", "description": "Tapers on Y",
          "name": "Taper on Y", "type": "Point Wrangles",
          "snippet": "QFAgKj0gMjsK"},
  "222": {"author": "", "description": "a bookmark", "name": "CGWiki",
          "type": "Links", "snippet": "aHR0cHM6Ly9leGFtcGxlLmNvbQ=="},
  "333": {"author": "", "description": "", "name": "Broken",
          "type": "Point Wrangles", "snippet": "!!!not base64!!!"},
  "444": {"author": "", "description": "", "name": "", 
          "type": "Detail Wrangles", "snippet": "QFAgKj0gMjsK"}
}"""


def test_the_json_store_is_read_and_decoded():
    found = snippets.parse_json(JSON_SAMPLE, source="shippedSnippets.json")
    assert [s.name for s in found] == ["Taper on Y"]
    assert found[0].code == "@P *= 2;"
    assert found[0].description == "Tapers on Y"
    assert found[0].author == "Someone"
    assert found[0].group == "Point Wrangles"


def test_bookmarks_and_broken_entries_are_left_out():
    """563 of the entries are links; they would bury the actual snippets."""
    found = snippets.parse_json(JSON_SAMPLE)
    names = {s.name for s in found}
    assert "CGWiki" not in names, "a Links entry is not VEX"
    assert "Broken" not in names, "undecodable base64 must be skipped"
    assert "" not in names, "an unnamed entry must be skipped"


def test_a_json_category_becomes_the_wrangle_context():
    found = snippets.parse_json(JSON_SAMPLE)
    assert found[0].context == "pointwrangle/snippet"
    assert found[0].is_wrangle


def test_malformed_json_is_not_an_error():
    assert snippets.parse_json("{not json") == []
    assert snippets.parse_json("[]") == []


def test_the_same_snippet_in_both_stores_is_listed_once(monkeypatch, tmp_path):
    """OD ships overlapping entries in the .txt and the .json."""
    text_file = tmp_path / "a.txt"
    text_file.write_text("pointwrangle/snippet\n    Taper on Y\n    @P *= 2;\n")
    json_file = tmp_path / "b.json"
    json_file.write_text(JSON_SAMPLE)
    monkeypatch.setenv(snippets.ENV_PATH,
                       os.pathsep.join([str(json_file), str(text_file)]))

    names = [s.name for s in snippets.load()]
    assert names.count("Taper on Y") == 1, f"duplicated: {names}"


# ------------------------------------------------------- saving your own


def test_a_saved_graph_also_lands_where_the_wrangle_looks(tmp_path, monkeypatch):
    """OD's menu is the one you get from an Attribute Wrangle.

    Writing only to our own store meant a snippet saved here was invisible from
    the node, so the two sets could not be used interchangeably.
    """
    od = tmp_path / "python_panels"
    od.mkdir()
    monkeypatch.setattr(snippets, "OD_USER_STORE", od / "snippets.json")
    monkeypatch.setattr(snippets, "OD_CONFIG", od / "snippets.cfg")
    monkeypatch.setattr(snippets, "USER_STORE", tmp_path / "vexgraph_snippets.json")

    written = snippets.save_user_snippet("Curve tangents", "@P.y += 1;", "note")
    assert len(written) == 2, "ours and OD's"

    stored = json.loads((od / "snippets.json").read_text(encoding="utf8"))
    entry = next(iter(stored.values()))
    # Exactly OD's own fields, so its manager cannot be surprised by ours.
    assert set(entry) == {"author", "description", "name", "snippet", "type"}
    assert entry["name"] == "Curve tangents"
    assert base64.b64decode(entry["snippet"]).decode() == "@P.y += 1;"


def test_saving_twice_replaces_rather_than_piles_up(tmp_path, monkeypatch):
    monkeypatch.setattr(snippets, "USER_STORE", tmp_path / "mine.json")
    monkeypatch.setattr(snippets, "OD_USER_STORE", tmp_path / "none" / "s.json")

    snippets.save_user_snippet("Same name", "@P.y += 1;")
    snippets.save_user_snippet("Same name", "@P.y += 2;")
    stored = json.loads((tmp_path / "mine.json").read_text(encoding="utf8"))
    assert len(stored) == 1
    assert base64.b64decode(next(iter(stored.values()))["snippet"]).decode() \
        == "@P.y += 2;"


def test_other_peoples_entries_in_ods_store_are_left_alone(tmp_path, monkeypatch):
    """That file is shared with OD's own manager."""
    od = tmp_path / "python_panels"
    od.mkdir()
    theirs = {"27021290079170": {"name": "Taper on Y", "type": "Point Wrangles",
                                 "snippet": "", "description": "", "author": "OD"}}
    (od / "snippets.json").write_text(json.dumps(theirs), encoding="utf8")
    monkeypatch.setattr(snippets, "OD_USER_STORE", od / "snippets.json")
    monkeypatch.setattr(snippets, "OD_CONFIG", od / "snippets.cfg")
    monkeypatch.setattr(snippets, "USER_STORE", tmp_path / "mine.json")

    snippets.save_user_snippet("Mine", "@P.y += 1;")
    stored = json.loads((od / "snippets.json").read_text(encoding="utf8"))
    assert "27021290079170" in stored, "OD's own entry must survive"
    assert {e["name"] for e in stored.values()} == {"Taper on Y", "Mine"}


def test_a_configured_od_store_is_not_guessed_at(tmp_path, monkeypatch):
    """snippets.cfg means OD was pointed elsewhere; writing blind would lie."""
    od = tmp_path / "python_panels"
    od.mkdir()
    (od / "snippets.cfg").write_text("somewhere else", encoding="utf8")
    monkeypatch.setattr(snippets, "OD_USER_STORE", od / "snippets.json")
    monkeypatch.setattr(snippets, "OD_CONFIG", od / "snippets.cfg")
    monkeypatch.setattr(snippets, "USER_STORE", tmp_path / "mine.json")

    assert snippets.od_writable_store() is None
    assert [p.name for p in snippets.save_user_snippet("X", "@P.y += 1;")] \
        == ["mine.json"]


def test_od_being_absent_costs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(snippets, "OD_USER_STORE",
                        tmp_path / "not_installed" / "snippets.json")
    monkeypatch.setattr(snippets, "USER_STORE", tmp_path / "mine.json")
    assert snippets.od_writable_store() is None
    assert len(snippets.save_user_snippet("X", "@P.y += 1;")) == 1
