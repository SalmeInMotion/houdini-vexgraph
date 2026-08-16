"""The Beginner course, held to the tool's own standard.

The one unforgivable failure in a course is an exercise that cannot be
completed. So the reference solution of every exercise is imported through
the same path the student's graph takes, and must pass its own checks -
which also proves the checks test what the emission really says, not what
the author remembered it saying.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vexgraph import default_registry, generate, learn  # noqa: E402
from vexgraph.graph import ERROR, Graph  # noqa: E402
from vexgraph.parser import import_vex  # noqa: E402
from vexgraph.vccmap import check_source  # noqa: E402


@pytest.fixture(scope="session")
def registry():
    return default_registry()


def test_the_course_has_ten_exercises_with_everything_filled_in():
    assert len(learn.BEGINNER) == 10
    for exercise in learn.BEGINNER:
        assert exercise.title and exercise.goal and exercise.steps
        assert len(exercise.hints) >= 2, exercise.key
        assert exercise.checks, exercise.key
        assert exercise.solution, exercise.key


@pytest.mark.parametrize("exercise", learn.BEGINNER,
                         ids=[e.key for e in learn.BEGINNER])
def test_every_reference_solution_passes_its_own_checks(exercise, registry):
    report = import_vex(exercise.solution, registry)
    verdict = exercise.review(report.graph)
    assert verdict == "", f"{exercise.key}: {verdict}"


@pytest.mark.parametrize("exercise", learn.BEGINNER,
                         ids=[e.key for e in learn.BEGINNER])
def test_every_reference_solution_compiles(exercise, registry):
    report = import_vex(exercise.solution, registry)
    emission = generate(report.graph)
    assert not [i for i in emission.issues if i.severity == ERROR]
    check = check_source(emission.code)
    if check.checked:
        assert check.ok, f"{exercise.key}:\n{check.raw}"


@pytest.mark.parametrize("exercise", learn.BEGINNER,
                         ids=[e.key for e in learn.BEGINNER])
def test_an_empty_graph_fails_with_the_first_lesson(exercise, registry):
    empty = Graph(registry)
    empty.add("start", "start")
    verdict = exercise.review(empty)
    assert verdict == exercise.checks[0].teach


def test_progress_ranks_struggle():
    progress = learn.Progress(
        completed={"paint", "stripes", "trail"},
        attempts={"paint": 1, "stripes": 6, "trail": 3},
        hints_used={"stripes": 2, "trail": 1})
    assert progress.weakest()[0] == "stripes"


# ------------------------------------------------- a teacher worth having

def test_the_same_number_written_differently_still_counts(registry):
    """`.1` is `0.1`. Marking that wrong is how you make someone give up on
    something they had actually got right."""
    for spelling in (".1", "0.1", "0.10"):
        report = import_vex(f"@P = @P + @N * {spelling};", registry)
        exercise = next(e for e in learn.BEGINNER if e.key == "inflate")
        assert exercise.review(report.graph) == "", spelling


def test_a_different_push_still_counts(registry):
    """The lesson is read-combine-write, not the exact amount."""
    exercise = next(e for e in learn.BEGINNER if e.key == "inflate")
    for amount in ("0.05", "0.5", "2"):
        report = import_vex(f"@P = @P + @N * {amount};", registry)
        assert exercise.review(report.graph) == "", amount


def test_leniency_has_limits(registry):
    """Tolerant about spelling, not about meaning: subtracting is not adding."""
    exercise = next(e for e in learn.BEGINNER if e.key == "inflate")
    report = import_vex("@P = @P - @N * 0.1;", registry)
    assert exercise.review(report.graph) != ""


def test_a_colour_typed_or_built_both_count(registry):
    exercise = next(e for e in learn.BEGINNER if e.key == "paint")
    for spelling in ("@Cd = {1, 0, 0};", "@Cd = set(1, 0, 0);",
                     "@Cd = set(1.0, 0.0, 0.0);"):
        report = import_vex(spelling, registry)
        assert exercise.review(report.graph) == "", spelling
