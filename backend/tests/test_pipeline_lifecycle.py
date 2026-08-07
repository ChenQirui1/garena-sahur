"""Every entry point starts the pipeline through the one lifecycle helper.

Owner: Jerome & Richard

The JSONL replay drifted from the HTTP service because each entry point started the stages it
happened to know about, and the scheduler was added to one of them. A behavioural test cannot
catch the next stage being forgotten — the forgotten stage is exactly the one nothing asserts on
— so this reads the source instead and refuses a second start order anywhere.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from backend.main import Pipeline
from backend.tests.test_module_boundaries import owned_sources
from backend.tests.tracked_documents import REPO_ROOT

MAIN = REPO_ROOT / "backend" / "main.py"
LIFECYCLE_HELPER = "running"

# Read from the pipeline rather than listed, because a listed name is exactly what a newly added
# stage would not be in — and a stage nothing here knows about is the drift this file exists for.
STAGES = frozenset(field.name for field in fields(Pipeline))

# The calls that start or stop a stage. Naming both directions matters: a stage stopped in the
# wrong place outlives the store it writes to.
TRANSITIONS = frozenset({"open", "close", "start", "stop", "run"})


def stage_transitions(source: Path) -> list[tuple[int, str]]:
    """Every ``<stage>.<transition>()`` call in one file, with the line it sits on.

    The receiver has to be written as an attribute of something, which is how every entry point
    reaches a stage today. A stage first bound to a local name would escape this.
    """
    found = []
    for node in ast.walk(ast.parse(source.read_text())):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if not isinstance(called, ast.Attribute) or called.attr not in TRANSITIONS:
            continue
        stage = called.value
        named = stage.attr if isinstance(stage, ast.Attribute) else None
        if named in STAGES:
            found.append((node.lineno, f"{named}.{called.attr}"))
    return found


def helper_line_range() -> range:
    """The lines the lifecycle helper occupies, which is where transitions are allowed."""
    for node in ast.walk(ast.parse(MAIN.read_text())):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == LIFECYCLE_HELPER:
            assert node.end_lineno is not None
            return range(node.lineno, node.end_lineno + 1)
    raise AssertionError(f"{MAIN} no longer defines {LIFECYCLE_HELPER}")


def test_the_lifecycle_helper_starts_and_stops_every_stage() -> None:
    """It is the shared helper only if it is the thing that runs the whole pipeline."""
    inside = helper_line_range()
    transitioned = {
        transition for line, transition in stage_transitions(MAIN) if line in inside
    }

    assert transitioned == {
        "store.open",
        "store.close",
        "handoff.start",
        "handoff.stop",
        "scheduler.start",
        "scheduler.stop",
        "recovery.run",
    }


def production_sources() -> list[Path]:
    """The owned modules a deployment runs. A test fixture may still drive one stage alone."""
    return [MAIN, *(source for source in owned_sources() if "tests" not in source.parts)]


def test_no_entry_point_starts_a_stage_outside_the_lifecycle_helper() -> None:
    inside = helper_line_range()
    strays = [
        f"{source.relative_to(REPO_ROOT)}:{line} {transition}"
        for source in production_sources()
        for line, transition in stage_transitions(source)
        if not (source == MAIN and line in inside)
    ]

    assert strays == []
