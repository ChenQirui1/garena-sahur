"""Owner: Jerome & Richard"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, NamedTuple

import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.ingestion.message_validation import TOPIC_WORLD_SNAPSHOT
from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    SESSION_ID,
    SHOPKEEPER,
    THIEF,
    TIMESTAMP_MS,
    WORLD_ID,
    active_conversation,
    attention_edge,
    npc,
    world_snapshot,
)
from backend.main import Adapters, create_app
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.router_port import RouterPort
from backend.orchestration.tests.fake_routers import (
    RaisingRouter,
    RecordingRouter,
    ReportingRouter,
)


class Backend(NamedTuple):
    client: AsyncClient
    router: Any
    handoff: RouterHandoff


@asynccontextmanager
async def backend_for(
    router: RouterPort, tmp_path: Path, **settings: Any
) -> AsyncIterator[Backend]:
    app = create_app(
        settings=Settings(database_path=tmp_path / "spotlight.sqlite3", **settings),
        adapters=Adapters(router=router),
    )
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://backend"
        ) as client:
            yield Backend(client, router, app.state.pipeline.handoff)


@pytest_asyncio.fixture
async def backend(tmp_path: Path) -> AsyncIterator[Backend]:
    async with backend_for(RecordingRouter(), tmp_path) as running:
        yield running


async def ingest(backend: Backend, topic: str, message: dict) -> tuple[int, dict]:
    response = await backend.client.post("/ingest", json={"topic": topic, "message": message})
    await backend.handoff.wait_until_idle()
    return response.status_code, response.json()


async def observe_routing(backend: Backend) -> tuple[int, Any]:
    response = await backend.client.get(f"/routing/{SESSION_ID}/{WORLD_ID}")
    return response.status_code, response.json()


async def test_a_new_snapshot_is_accepted_and_reaches_the_router(backend: Backend) -> None:
    code, body = await ingest(
        backend,
        TOPIC_WORLD_SNAPSHOT,
        world_snapshot(sequence=1842, active_conversation=active_conversation()),
    )
    assert (code, body["outcome"]) == (202, "applied")

    assert await observe_routing(backend) == (
        200,
        {
            "session_id": SESSION_ID,
            "world_id": WORLD_ID,
            "sequence": 1842,
            "status": "routed",
            "failure_reason": None,
            "assignments": [
                {
                    "npc_id": SHOPKEEPER,
                    "tier": "focused",
                    "previous_tier": None,
                    "changed": True,
                    "reasons": [],
                    "direct_score": None,
                    "propagated_score": None,
                    "final_score": None,
                },
                {
                    "npc_id": THIEF,
                    "tier": "ambient",
                    "previous_tier": None,
                    "changed": True,
                    "reasons": [],
                    "direct_score": None,
                    "propagated_score": None,
                    "final_score": None,
                },
            ],
            "counts": None,
            "diagnostics": None,
        },
    )


async def test_the_router_receives_the_enriched_contract_shape(backend: Backend) -> None:
    await ingest(
        backend,
        TOPIC_WORLD_SNAPSHOT,
        world_snapshot(
            sequence=1842,
            active_conversation=active_conversation(),
            attention_edges=[attention_edge()],
        ),
    )

    routed = backend.router.routed[0]

    assert routed.model_dump() == {
        "schema_version": "1.0",
        "snapshot_type": "routing_snapshot",
        "session_id": SESSION_ID,
        "world_id": WORLD_ID,
        "sequence": 1842,
        "timestamp_ms": TIMESTAMP_MS,
        "candidate_policy": {"entry_radius_blocks": 24.0, "exit_radius_blocks": 28.0},
        "active_event_ids": [],
        "active_conversation": {
            "conversation_id": CONVERSATION_ID,
            "target_npc_id": SHOPKEEPER,
            "state": "engaged",
            "started_at_ms": TIMESTAMP_MS,
            "latest_turn_id": None,
        },
        "candidate_count": 2,
        "npcs": [
            {
                "npc_id": SHOPKEEPER,
                "world_distance_blocks": 3.4,
                "viewport_center_distance": 0.07,
                "inside_viewport": True,
                "line_of_sight": True,
                "event_relevance": 0.0,
                "event_roles": [],
                # The player is talking to this NPC, so the interaction is happening now.
                "interaction_recency": 1.0,
            },
            {
                "npc_id": THIEF,
                "world_distance_blocks": 11.2,
                "viewport_center_distance": 0.07,
                "inside_viewport": True,
                "line_of_sight": True,
                "event_relevance": 0.0,
                "event_roles": [],
                "interaction_recency": 0.0,
            },
        ],
        "attention_edges": [
            {
                "source_npc_id": THIEF,
                "target_npc_id": SHOPKEEPER,
                "kind": "gaze",
                "active": True,
            }
        ],
    }


async def test_the_source_sequence_and_timestamp_survive_enrichment(backend: Backend) -> None:
    await ingest(
        backend, TOPIC_WORLD_SNAPSHOT, world_snapshot(sequence=99, timestamp_ms=1_700_000_000_001)
    )

    routed = backend.router.routed[0]
    assert (routed.sequence, routed.timestamp_ms) == (99, 1_700_000_000_001)


async def test_a_conversation_keeps_its_first_observed_start_across_snapshots(
    backend: Backend,
) -> None:
    await ingest(
        backend,
        TOPIC_WORLD_SNAPSHOT,
        world_snapshot(sequence=1, active_conversation=active_conversation()),
    )
    await ingest(
        backend,
        TOPIC_WORLD_SNAPSHOT,
        world_snapshot(
            sequence=2,
            timestamp_ms=TIMESTAMP_MS + 4_000,
            active_conversation=active_conversation(),
        ),
    )

    assert [routed.active_conversation.started_at_ms for routed in backend.router.routed] == [
        TIMESTAMP_MS,
        TIMESTAMP_MS,
    ]


async def test_an_empty_candidate_set_is_accepted_and_routed(backend: Backend) -> None:
    code, body = await ingest(
        backend, TOPIC_WORLD_SNAPSHOT, world_snapshot(npcs=[], candidate_count=0)
    )

    assert (code, body["outcome"]) == (202, "applied")
    assert backend.router.routed[0].npcs == []
    assert (await observe_routing(backend))[1]["assignments"] == []


async def test_a_duplicate_or_stale_snapshot_does_not_regress_state(backend: Backend) -> None:
    await ingest(backend, TOPIC_WORLD_SNAPSHOT, world_snapshot(sequence=7))

    duplicate = await ingest(backend, TOPIC_WORLD_SNAPSHOT, world_snapshot(sequence=7))
    stale = await ingest(backend, TOPIC_WORLD_SNAPSHOT, world_snapshot(sequence=3))

    assert (duplicate[0], duplicate[1]["outcome"]) == (200, "stale")
    assert (stale[0], stale[1]["outcome"]) == (200, "stale")

    _, observed = await observe_routing(backend)
    assert observed["sequence"] == 7
    assert [snapshot.sequence for snapshot in backend.router.routed] == [7]


async def test_an_invalid_snapshot_is_rejected_before_state_changes(backend: Backend) -> None:
    code, body = await ingest(backend, TOPIC_WORLD_SNAPSHOT, world_snapshot(candidate_count=9))

    assert code == 422
    assert body["outcome"] == "invalid"
    assert "candidate_count" in body["detail"]
    assert backend.router.routed == []
    assert (await observe_routing(backend))[0] == 404


async def test_more_candidates_than_configured_are_rejected(tmp_path: Path) -> None:
    async with backend_for(RecordingRouter(), tmp_path, max_snapshot_candidates=1) as backend:
        code, body = await ingest(backend, TOPIC_WORLD_SNAPSHOT, world_snapshot())

        assert code == 422
        assert "at most 1 candidates" in body["detail"]
        assert backend.router.routed == []


async def test_an_unknown_topic_is_rejected(backend: Backend) -> None:
    code, body = await ingest(backend, "world.weather", world_snapshot())

    assert code == 400
    assert body["outcome"] == "unknown_topic"


async def test_a_legacy_profile_record_is_accepted_and_ignored(backend: Backend) -> None:
    code, body = await ingest(backend, "npc.profile", {"npc_id": SHOPKEEPER, "name": "Mara"})

    assert code == 200
    assert body["outcome"] == "ignored"
    assert "ignored" in body["detail"]
    assert backend.router.routed == []


async def test_a_malformed_envelope_is_rejected(backend: Backend) -> None:
    response = await backend.client.post("/ingest", json={"topic": TOPIC_WORLD_SNAPSHOT})

    assert response.status_code == 422


async def test_a_router_failure_is_observable_without_an_invented_tier(
    tmp_path: Path,
) -> None:
    async with backend_for(RaisingRouter(), tmp_path) as backend:
        code, _ = await ingest(backend, TOPIC_WORLD_SNAPSHOT, world_snapshot(sequence=5))
        assert code == 202

        _, observed = await observe_routing(backend)
        assert observed["status"] == "router_failed"
        assert observed["assignments"] == []
        assert "router exploded" in observed["failure_reason"]


async def test_snapshot_intake_stops_at_the_routing_outcome(backend: Backend) -> None:
    await ingest(
        backend,
        TOPIC_WORLD_SNAPSHOT,
        world_snapshot(
            sequence=1,
            npcs=[npc(SHOPKEEPER)],
            candidate_count=1,
            active_conversation=active_conversation(),
        ),
    )

    _, observed = await observe_routing(backend)

    assert [assignment["npc_id"] for assignment in observed["assignments"]] == [SHOPKEEPER]
    assert len(backend.router.routed) == 1
    assert (await backend.client.get("/commands")).status_code == 404


async def test_liveness_and_readiness_distinguish_running_from_ready(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(database_path=tmp_path / "spotlight.sqlite3"),
        adapters=Adapters(router=RecordingRouter()),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://backend") as client:
        assert (await client.get("/health/live")).json() == {"status": "alive"}

        not_ready = await client.get("/health/ready")
        assert not_ready.status_code == 503

        async with LifespanManager(app):
            ready = await client.get("/health/ready")
            assert (ready.status_code, ready.json()) == (200, {"status": "ready"})


async def test_reported_counts_and_diagnostics_reach_the_development_projection(
    tmp_path: Path,
) -> None:
    """A field the Router did report must survive the projection, not only the outcome."""
    async with backend_for(ReportingRouter(), tmp_path) as backend:
        await ingest(
            backend,
            TOPIC_WORLD_SNAPSHOT,
            world_snapshot(sequence=1842, active_conversation=active_conversation()),
        )
        code, body = await observe_routing(backend)

    assert code == 200
    assert body["counts"] == {"focused": 1, "reactive": 0, "ambient": 1}
    assert body["diagnostics"] == {
        "focused_capacity": 2,
        "reactive_capacity": 6,
        "candidate_count": 2,
        "routing_time_ms": 0.31,
    }
