"""Owner: Jerome & Richard"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from backend.config import Settings
from backend.ingestion.intake_service import IntakeOutcome, IntakeService
from backend.ingestion.jsonl_intake import JsonlIntakeError, submit_jsonl
from backend.ingestion.message_validation import (
    TOPIC_CONVERSATION_TURN,
    TOPIC_LEGACY_NPC_PROFILE,
    TOPIC_WORLD_SNAPSHOT,
)
from backend.ingestion.tests.canonical_messages import (
    SESSION_ID,
    SHOPKEEPER,
    WORLD_ID,
    active_conversation,
    conversation_turn,
    world_snapshot,
)
from backend.main import (
    Adapters,
    Pipeline,
    PipelineNotReady,
    build_pipeline,
    replay_jsonl,
)
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.router_port import RoutingResult, RoutingSnapshot
from backend.orchestration.tests.fake_routers import RecordingRouter
from backend.orchestration.tests.harness import settings_for

SNAPSHOT_TOPIC = TOPIC_WORLD_SNAPSHOT


def record(topic: str, message: dict[str, Any]) -> str:
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
    outcome = handoff.latest_outcome(SESSION_ID, WORLD_ID)
    assert outcome is not None and outcome.sequence == 2
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


class ReadinessProbe(RecordingRouter):
    """Records whether the pipeline was ready at the moment the replay reached the Router."""

    def __init__(self) -> None:
        super().__init__()
        self.pipeline: Pipeline | None = None
        self.ready_while_routing: list[bool] = []

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        assert self.pipeline is not None
        self.ready_while_routing.append(self.pipeline.is_ready)
        return super().route(snapshot)


async def test_a_replayed_player_turn_ends_with_a_stored_command(tmp_path: Path) -> None:
    """The replay owes the same lifecycle as the service, not merely accepted messages.

    Generation is queued rather than run inline, so an `applied` outcome says only that intake
    took the turn. Reopening the store afterwards is what proves the queued work was dispatched
    and committed before the replay reported anything.
    """
    router = ReadinessProbe()
    pipeline = build_pipeline(settings_for(tmp_path), Adapters(router=router))
    router.pipeline = pipeline
    path = tmp_path / "replay.jsonl"
    path.write_text(
        "\n".join(
            [
                record(SNAPSHOT_TOPIC, world_snapshot(active_conversation=active_conversation())),
                record(TOPIC_CONVERSATION_TURN, conversation_turn()),
            ]
        )
    )

    results = await replay_jsonl(path, pipeline)

    assert [result.outcome for result in results] == [
        IntakeOutcome.APPLIED,
        IntakeOutcome.APPLIED,
    ]
    assert pipeline.scheduler.pending_count == 0
    assert router.ready_while_routing and all(router.ready_while_routing)

    await pipeline.store.open()
    try:
        command = await pipeline.commands.latest_for(SESSION_ID, SHOPKEEPER)
    finally:
        await pipeline.store.close()
    assert command is not None and command.turn_id == conversation_turn()["turn_id"]


async def test_a_replay_reports_nothing_when_the_pipeline_never_became_ready(
    tmp_path: Path,
) -> None:
    """An unreadable profile document is a 503 on the HTTP path, so it is not a clean replay."""
    pipeline = build_pipeline(settings_for(tmp_path, profiles="{ not a document"))
    path = tmp_path / "replay.jsonl"
    path.write_text(record(SNAPSHOT_TOPIC, world_snapshot()))

    with pytest.raises(PipelineNotReady):
        await replay_jsonl(path, pipeline)


async def test_a_pipeline_that_is_not_running_is_refused_rather_than_waited_on(
    tmp_path: Path,
) -> None:
    pipeline = build_pipeline(settings_for(tmp_path))

    with pytest.raises(PipelineNotReady):
        await pipeline.drain()


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
