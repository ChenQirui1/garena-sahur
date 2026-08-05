"""Owner: Jerome & Richard"""

from __future__ import annotations

from typing import AsyncIterator

import pytest_asyncio

from backend.ingestion.http_intake import STATUS_FOR_OUTCOME
from backend.ingestion.intake_service import IntakeOutcome, IntakeService
from backend.ingestion.message_validation import WorldSnapshot
from backend.ingestion.tests.canonical_messages import world_snapshot
from backend.ingestion.world_state_store import StorageUnavailable, WorldStateStore
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.tests.fake_routers import RecordingRouter

SNAPSHOT_TOPIC = "world.snapshot"


class UnavailableStore(WorldStateStore):
    def apply_if_newer(self, snapshot: WorldSnapshot) -> bool:
        raise StorageUnavailable("world state is unavailable")


@pytest_asyncio.fixture
async def handoff() -> AsyncIterator[tuple[RouterHandoff, RecordingRouter]]:
    router = RecordingRouter()
    started = RouterHandoff(router)
    await started.start()
    try:
        yield started, router
    finally:
        await started.stop()


async def test_unavailable_storage_is_reported_and_nothing_is_routed(
    handoff: tuple[RouterHandoff, RecordingRouter],
) -> None:
    started, router = handoff
    service = IntakeService(UnavailableStore(), started, max_snapshot_candidates=128)

    result = service.submit(SNAPSHOT_TOPIC, world_snapshot())

    assert result.outcome is IntakeOutcome.STORAGE_UNAVAILABLE
    assert result.detail == "world state is unavailable"
    assert router.routed == []


def test_every_intake_outcome_has_an_http_status() -> None:
    assert set(STATUS_FOR_OUTCOME) == set(IntakeOutcome)
    assert STATUS_FOR_OUTCOME[IntakeOutcome.STORAGE_UNAVAILABLE] == 503
