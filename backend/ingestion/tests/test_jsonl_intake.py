"""Owner: Jerome & Richard"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.config import Settings
from backend.ingestion.intake_service import IntakeOutcome, IntakeService
from backend.ingestion.jsonl_intake import JsonlIntakeError, submit_jsonl
from backend.ingestion.message_validation import (
    TOPIC_LEGACY_NPC_PROFILE,
    TOPIC_WORLD_SNAPSHOT,
)
from backend.ingestion.tests.canonical_messages import SESSION_ID, WORLD_ID, world_snapshot
from backend.main import Adapters, build_pipeline
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.tests.fake_routers import RecordingRouter

SNAPSHOT_TOPIC = TOPIC_WORLD_SNAPSHOT


def record(topic: str, message: dict) -> str:
    return json.dumps({"topic": topic, "message": message})


@pytest_asyncio.fixture
async def service(
    tmp_path: Path,
) -> AsyncIterator[tuple[IntakeService, RouterHandoff, RecordingRouter]]:
    router = RecordingRouter()
    pipeline = build_pipeline(
        Settings(database_path=tmp_path / "spotlight.sqlite3"), Adapters(router=router)
    )
    await pipeline.store.open()
    await pipeline.handoff.start()
    try:
        yield pipeline.intake, pipeline.handoff, router
    finally:
        await pipeline.handoff.stop()
        await pipeline.store.close()


async def test_records_share_the_application_service_and_reach_the_router(
    service: tuple[IntakeService, RouterHandoff, RecordingRouter],
) -> None:
    intake, handoff, router = service

    results = await submit_jsonl(
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
    assert handoff.latest_outcome(SESSION_ID, WORLD_ID).sequence == 2
    assert router.routed != []


async def test_a_legacy_profile_record_is_ignored_rather_than_failing_the_replay(
    service: tuple[IntakeService, RouterHandoff, RecordingRouter],
) -> None:
    """This adapter opts in to the legacy topic, so a replay carrying one still runs on.

    `world.weather` in the rejection cases below is the contrast: an unknown topic stops the
    replay, and only the topic this adapter explicitly accepts does not.
    """
    intake, handoff, router = service

    results = await submit_jsonl(
        [
            record(TOPIC_LEGACY_NPC_PROFILE, {"npc_id": "npc.shopkeeper", "name": "Mara"}),
            record(SNAPSHOT_TOPIC, world_snapshot(sequence=1)),
        ],
        intake,
    )
    await handoff.wait_until_idle()

    ignored, applied = results

    assert (ignored.outcome, applied.outcome) == (IntakeOutcome.IGNORED, IntakeOutcome.APPLIED)
    assert ignored.detail is not None and "ignored" in ignored.detail
    assert len(router.routed) == 1


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("{not json", "malformed JSON"),
        (json.dumps({"topic": SNAPSHOT_TOPIC}), "exactly topic and message"),
        (json.dumps({"topic": SNAPSHOT_TOPIC, "message": {}, "extra": 1}), "exactly topic and message"),
        (json.dumps({"topic": 7, "message": {}}), "topic must be a string"),
        (record("world.weather", world_snapshot()), "unknown_topic"),
        (record(SNAPSHOT_TOPIC, world_snapshot(candidate_count=9)), "invalid"),
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
        await submit_jsonl(lines, intake)

    assert rejected.value.line_number == 2
    assert reason in rejected.value.reason
    assert str(rejected.value).startswith("line 2:")
    assert len(router.routed) <= 1
