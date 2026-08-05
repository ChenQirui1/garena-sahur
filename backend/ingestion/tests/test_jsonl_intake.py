"""Owner: Jerome & Richard"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.ingestion.intake_service import IntakeOutcome, IntakeService
from backend.ingestion.jsonl_intake import JsonlIntakeError, submit_jsonl
from backend.ingestion.message_validation import TOPIC_WORLD_SNAPSHOT
from backend.ingestion.tests.canonical_messages import SESSION_ID, WORLD_ID, world_snapshot
from backend.ingestion.world_state_store import WorldStateStore
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.tests.fake_routers import RecordingRouter

SNAPSHOT_TOPIC = TOPIC_WORLD_SNAPSHOT


def record(topic: str, message: dict) -> str:
    return json.dumps({"topic": topic, "message": message})


@pytest_asyncio.fixture
async def service() -> AsyncIterator[tuple[IntakeService, RouterHandoff, RecordingRouter]]:
    router = RecordingRouter()
    handoff = RouterHandoff(router)
    await handoff.start()
    try:
        yield (
            IntakeService(WorldStateStore(), handoff, max_snapshot_candidates=128),
            handoff,
            router,
        )
    finally:
        await handoff.stop()


async def test_records_share_the_application_service_and_reach_the_router(
    service: tuple[IntakeService, RouterHandoff, RecordingRouter],
) -> None:
    intake, handoff, router = service

    results = submit_jsonl(
        [
            record(SNAPSHOT_TOPIC, world_snapshot(sequence=1)),
            "",
            record(SNAPSHOT_TOPIC, world_snapshot(sequence=2)),
            record(SNAPSHOT_TOPIC, world_snapshot(sequence=2)),
        ],
        intake,
    )
    await handoff.wait_until_idle()

    assert [result.outcome for result in results] == [
        IntakeOutcome.APPLIED,
        IntakeOutcome.APPLIED,
        IntakeOutcome.STALE,
    ]
    assert handoff.latest_outcome(SESSION_ID, WORLD_ID).source_sequence == 2
    assert router.routed != []


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("{not json", "malformed JSON"),
        (json.dumps({"topic": SNAPSHOT_TOPIC}), "exactly topic and message"),
        (json.dumps({"topic": SNAPSHOT_TOPIC, "message": {}, "extra": 1}), "exactly topic and message"),
        (json.dumps({"topic": 7, "message": {}}), "topic must be a string"),
        (record("world.weather", world_snapshot()), "unknown_topic"),
        (record(SNAPSHOT_TOPIC, world_snapshot(sequence=0)), "invalid"),
    ],
)
async def test_a_bad_record_fails_fast_with_its_line_number(
    service: tuple[IntakeService, RouterHandoff, RecordingRouter],
    line: str,
    reason: str,
) -> None:
    intake, _, router = service
    lines = [record(SNAPSHOT_TOPIC, world_snapshot(sequence=1)), line, "unreachable"]

    with pytest.raises(JsonlIntakeError) as rejected:
        submit_jsonl(lines, intake)

    assert rejected.value.line_number == 2
    assert reason in rejected.value.reason
    assert str(rejected.value).startswith("line 2:")
    assert len(router.routed) <= 1
