"""The Claude Code CLI provider.

Every test here fakes the subprocess. A test suite must not spend someone's
subscription allowance to prove a command line is assembled correctly - and
the parts worth pinning (the flags, stdin rather than argv, the JSON
envelope, the error paths) are all decided before the process would run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph.assistant.providers import (ClaudeCliProvider,  # noqa: E402
                                          ProviderError, all_providers)


class FakeRun:
    """Stands in for subprocess.run, remembering how it was called."""

    def __init__(self, stdout: str = "", returncode: int = 0,
                 stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.command: list[str] = []
        self.stdin = ""

    def __call__(self, command, **kwargs):
        self.command = command
        self.stdin = kwargs.get("input", "")
        return subprocess.CompletedProcess(
            command, self.returncode, self.stdout, self.stderr)


def envelope(result: str, is_error: bool = False) -> str:
    return json.dumps({"type": "result", "subtype": "success",
                       "is_error": is_error, "result": result})


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(ClaudeCliProvider, "executable",
                        staticmethod(lambda: "claude.exe"))
    return ClaudeCliProvider(model="claude-sonnet-5")


def test_it_is_offered_as_a_provider():
    assert "Claude (CLI)" in all_providers()


def test_the_prompt_goes_in_on_stdin_not_the_command_line(provider, monkeypatch):
    """The catalogue makes prompts long, and Windows caps a command line at
    about 32k characters."""
    fake = FakeRun(envelope("hello"))
    monkeypatch.setattr(subprocess, "run", fake)

    huge = "x" * 40000
    assert provider.complete("be helpful", [{"content": huge}], None) == "hello"
    assert fake.stdin == huge
    assert not any(huge in part for part in fake.command)


def test_the_call_is_one_answer_with_no_tools(provider, monkeypatch):
    fake = FakeRun(envelope("{}"))
    monkeypatch.setattr(subprocess, "run", fake)
    provider.complete("system words", [{"content": "hi"}], None)

    command = " ".join(fake.command)
    assert "-p" in fake.command
    assert "--output-format json" in command
    assert "claude-sonnet-5" in command
    assert "--strict-mcp-config" in command
    assert "--disallowed-tools" in command
    assert "system words" in fake.command      # our system prompt, not theirs


def test_a_missing_cli_explains_itself(monkeypatch):
    monkeypatch.setattr(ClaudeCliProvider, "executable",
                        staticmethod(lambda: ""))
    ready, why = ClaudeCliProvider().available()
    assert not ready and "not found" in why


def test_an_error_envelope_becomes_a_provider_error(provider, monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        FakeRun(envelope("Credit balance too low", True)))
    with pytest.raises(ProviderError, match="Credit balance"):
        provider.complete("s", [{"content": "hi"}], None)


def test_unreadable_output_says_so(provider, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun("not json at all"))
    with pytest.raises(ProviderError, match="could not read"):
        provider.complete("s", [{"content": "hi"}], None)


def test_a_timeout_is_reported_not_raised_raw(provider, monkeypatch):
    def explode(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 900)

    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(ProviderError, match="did not answer"):
        provider.complete("s", [{"content": "hi"}], None)


def test_the_cli_takes_the_vex_route_like_the_local_provider():
    """No structured-output switch on the CLI, so asking for catalogue JSON
    would lean on a schema nobody enforces. Writing VEX is the honest path."""
    import inspect

    from vexgraph.assistant import worker

    source = inspect.getsource(worker.run)
    assert '"Claude (CLI)"' in source and '"vex"' in source


# ------------------------------------------------- explaining, and not wiping

def test_a_reply_carries_its_reasoning_separately():
    from vexgraph.assistant.agent import split_reasoning, strip_vex

    raw = ("// run over: points\n@P.y += @N.y * 0.2;\n"
           "=== why ===\nDriven from N so it follows the surface; scaling P "
           "directly would have ignored orientation.")
    code, run_over = strip_vex(raw)
    assert code == "@P.y += @N.y * 0.2;"
    assert run_over == "points"
    assert "follows the surface" in split_reasoning(raw)[1]


def test_the_prompt_says_the_answer_replaces_everything():
    """A model told only "modify this" will happily return the new part
    alone - and the rest is gone, which is what it looks like from the other
    side of the screen."""
    import inspect

    from vexgraph.assistant.agent import Assistant

    source = inspect.getsource(Assistant.build_graph_via_vex)
    assert "REPLACES" in source
    assert "keep every line that should" in source
