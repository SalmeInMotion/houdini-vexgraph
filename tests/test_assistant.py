"""The assistant's guarantee, tested without spending a token.

The claim this feature rests on is that a model cannot make VEXgraph emit VEX
for a function that does not exist — it picks from a closed catalogue, and
everything it returns is checked. These tests script a provider with the exact
replies a model actually produces (invented node types, wrong socket names,
markdown fences, a graph that compiles) and check the loop behaves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import default_registry  # noqa: E402
from vexgraph.assistant import catalog  # noqa: E402
from vexgraph.assistant.agent import Assistant, GraphBuilder, strip_fence  # noqa: E402
from vexgraph.assistant.providers import OllamaProvider  # noqa: E402


class ScriptedProvider:
    """Replies from a list, so a whole repair loop runs offline."""

    name = "Scripted"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def available(self):
        return True, ""

    def complete(self, system, messages, schema):
        self.seen.append(messages)
        return self.replies.pop(0) if self.replies else "{}"


@pytest.fixture(scope="session")
def registry():
    return default_registry()


PUSH_ALONG_NORMAL = {
    "notes": "Moves each point along its normal.",
    "run_over": "points",
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "p", "type": "attrib_get", "params": {"attrib": "P", "type": "vector"}},
        {"id": "n", "type": "attrib_get", "params": {"attrib": "N", "type": "vector"}},
        {"id": "off", "type": "scale", "params": {"amount": "0.2"}},
        {"id": "sum", "type": "add", "params": {"type": "vector"}},
        {"id": "w", "type": "attrib_set", "params": {"attrib": "P", "type": "vector"}},
    ],
    "links": [
        {"from": "start", "out": "exec", "to": "w", "in": "exec", "exec": True},
        {"from": "n", "out": "value", "to": "off", "in": "value"},
        {"from": "p", "out": "value", "to": "sum", "in": "a"},
        {"from": "off", "out": "result", "to": "sum", "in": "b"},
        {"from": "sum", "out": "result", "to": "w", "in": "value"},
    ],
}


# ----------------------------------------------------------------- catalogue

def test_catalogue_covers_tier_one_and_stays_small(registry):
    text = catalog.build(registry, "colour points by distance")
    assert "attrib_set" in text and "closest_surface_point" in text
    # It rides in every request; a catalogue that bloats makes the feature
    # expensive on Claude and impossible on a local model.
    assert len(text) < 40_000


def test_catalogue_pulls_in_matching_vex_functions(registry):
    text = catalog.build(registry, "compute a cross product of two vectors")
    assert "vex_cross" in text


def test_catalogue_without_a_request_omits_the_generated_tier(registry):
    assert "Raw VEX functions" not in catalog.build(registry)


# ----------------------------------------------------------------- rejection

def test_invented_node_type_is_rejected_with_a_suggestion(registry):
    graph, problems = GraphBuilder(registry).build({
        "run_over": "points",
        "nodes": [{"id": "s", "type": "start"},
                  {"id": "x", "type": "get_closest_point"}],
        "links": [],
    })
    assert graph is None
    assert "no node type 'get_closest_point'" in problems[0]
    assert "Did you mean" in problems[0]


def test_invented_socket_is_rejected_and_names_the_real_ones(registry):
    graph, problems = GraphBuilder(registry).build({
        "run_over": "points",
        "nodes": [{"id": "s", "type": "start"},
                  {"id": "c", "type": "element_count"},
                  {"id": "l", "type": "for_range"}],
        "links": [{"from": "c", "out": "total", "to": "l", "in": "count"}],
    })
    assert graph is None
    assert "no output 'total'" in problems[0] and "count" in problems[0]


def test_wire_between_incompatible_types_is_rejected(registry):
    graph, problems = GraphBuilder(registry).build({
        "run_over": "points",
        "nodes": [{"id": "s", "type": "start"},
                  {"id": "p", "type": "attrib_get",
                   "params": {"attrib": "P", "type": "vector"}},
                  {"id": "l", "type": "for_range"}],
        "links": [{"from": "p", "out": "value", "to": "l", "in": "count"}],
    })
    assert graph is None
    assert "components" in problems[0]


def test_bad_menu_value_is_rejected(registry):
    graph, problems = GraphBuilder(registry).build({
        "run_over": "points",
        "nodes": [{"id": "s", "type": "start"},
                  {"id": "g", "type": "attrib_get",
                   "params": {"attrib": "P", "type": "colour"}}],
        "links": [],
    })
    assert graph is None
    assert "must be one of" in problems[0]


def test_missing_start_node_is_rejected(registry):
    graph, problems = GraphBuilder(registry).build({
        "run_over": "points",
        "nodes": [{"id": "g", "type": "attrib_get"}],
        "links": [],
    })
    assert graph is None
    assert any("start" in p for p in problems)


def test_exec_link_without_the_flag_is_still_understood(registry):
    """A model that forgets `exec: true` has still expressed the right intent."""
    graph, problems = GraphBuilder(registry).build({
        "run_over": "points",
        "nodes": [{"id": "s", "type": "start"},
                  {"id": "w", "type": "attrib_set",
                   "params": {"attrib": "Cd", "type": "vector",
                              "value": "{1, 0, 0}"}}],
        "links": [{"from": "s", "out": "exec", "to": "w", "in": "exec"}],
    })
    assert graph is not None, problems
    assert graph.links[0].is_exec


# ---------------------------------------------------------------- the loop

def test_a_good_reply_is_accepted_and_compiles(registry):
    provider = ScriptedProvider([json.dumps(PUSH_ALONG_NORMAL)])
    result = Assistant(registry, provider).build_graph("push points along N")
    assert result.ok, result.problems
    assert "@P = @P + @N * 0.2;" in result.code
    assert result.tries == 1


def test_a_bad_reply_is_repaired_on_the_second_try(registry):
    """The failure mode this whole design exists to prevent."""
    invented = {
        "notes": "", "run_over": "points",
        "nodes": [{"id": "s", "type": "start"},
                  {"id": "x", "type": "vex_magically_solve_it"}],
        "links": [],
    }
    provider = ScriptedProvider([json.dumps(invented),
                                 json.dumps(PUSH_ALONG_NORMAL)])
    result = Assistant(registry, provider).build_graph("push points along N")

    assert result.ok
    assert result.tries == 2
    # The second request must actually carry the reason it failed.
    repair = provider.seen[1][-1]["content"]
    assert "vex_magically_solve_it" in repair


def test_markdown_fences_and_thinking_tags_are_survived(registry):
    wrapped = ("<think>let me plan</think>\nHere you go:\n```json\n"
               + json.dumps(PUSH_ALONG_NORMAL) + "\n```")
    result = Assistant(registry, ScriptedProvider([wrapped])).build_graph("x")
    assert result.ok, result.problems


def test_a_reply_that_never_validates_fails_cleanly(registry):
    junk = json.dumps({"notes": "", "run_over": "points",
                       "nodes": [{"id": "a", "type": "nope"}], "links": []})
    result = Assistant(registry, ScriptedProvider([junk] * 3)).build_graph(
        "x", max_attempts=3)
    assert not result.ok
    assert result.tries == 3
    assert result.graph is None


def test_strip_fence_handles_prose_around_the_json():
    assert strip_fence('Sure!\n{"a": 1}\nHope that helps.') == '{"a": 1}'


def test_modifying_an_existing_graph_puts_it_in_the_prompt(registry):
    from vexgraph.graph import Graph  # noqa: PLC0415

    current = Graph(registry)
    current.add("start", "start")
    current.add("attrib_set", "paint", attrib="Cd", type="vector",
                value="{1, 0, 0}")
    current.chain("start", "paint")

    provider = ScriptedProvider([json.dumps(PUSH_ALONG_NORMAL)])
    Assistant(registry, provider).build_graph("also move them", current)
    prompt = provider.seen[0][0]["content"]
    assert "The graph as it stands" in prompt and "attrib_set" in prompt


# ----------------------------------------------------------------- providers

def test_local_provider_reports_a_stopped_ollama_without_raising():
    provider = OllamaProvider(url="http://127.0.0.1:59999")
    ready, why = provider.available()
    assert not ready
    assert "not answering" in why


def test_claude_provider_never_asks_for_or_stores_a_key(monkeypatch):
    from vexgraph.assistant.providers import ClaudeProvider  # noqa: PLC0415

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ready, why = ClaudeProvider().available()
    assert not ready
    assert "ANTHROPIC_API_KEY" in why and "never stores" in why
