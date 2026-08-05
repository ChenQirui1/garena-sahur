"""Owner: Jerome & Richard"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest_asyncio

from backend.config import Settings
from backend.ingestion.http_intake import STATUS_FOR_OUTCOME
from backend.ingestion.intake_service import IntakeOutcome
from backend.ingestion.message_validation import (
    TOPIC_CONVERSATION_TURN,
    TOPIC_WORLD_SNAPSHOT,
)
from backend.ingestion.tests.canonical_messages import conversation_turn, world_snapshot
from backend.main import Adapters, Pipeline, build_pipeline
from backend.orchestration.tests.fake_routers import RecordingRouter


@pytest_asyncio.fixture
async def unopened(tmp_path: Path) -> AsyncIterator[tuple[Pipeline, RecordingRouter]]:
    """A pipeline whose durable store was never opened, so durable intake cannot commit."""
    router = RecordingRouter()
    pipeline = build_pipeline(
        Settings(database_path=tmp_path / "spotlight.sqlite3"), Adapters(router=router)
    )
    await pipeline.handoff.start()
    try:
        yield pipeline, router
    finally:
        await pipeline.handoff.stop()


async def test_a_turn_is_not_acknowledged_when_it_cannot_be_committed(
    unopened: tuple[Pipeline, RecordingRouter],
) -> None:
    pipeline, router = unopened

    result = await pipeline.intake.submit(TOPIC_CONVERSATION_TURN, conversation_turn())

    assert result.outcome is IntakeOutcome.STORAGE_UNAVAILABLE
    assert router.routed == []


async def test_world_state_does_not_depend_on_the_durable_store(
    unopened: tuple[Pipeline, RecordingRouter],
) -> None:
    """Snapshots are latest-value state in memory, so they survive a closed database."""
    pipeline, _ = unopened

    result = await pipeline.intake.submit(TOPIC_WORLD_SNAPSHOT, world_snapshot())

    assert result.outcome is IntakeOutcome.APPLIED


def test_every_intake_outcome_has_an_http_status() -> None:
    assert set(STATUS_FOR_OUTCOME) == set(IntakeOutcome)
    assert STATUS_FOR_OUTCOME[IntakeOutcome.STORAGE_UNAVAILABLE] == 503
