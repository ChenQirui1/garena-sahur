"""Owner: Jerome & Richard"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.ingestion.tests.canonical_messages import (
    SESSION_ID,
    SHOPKEEPER,
    THIEF,
    WORLD_ID,
    candidate,
    world_snapshot,
)
from backend.main import create_app
from backend.orchestration.router_port import RouterPort
from backend.orchestration.tests.fake_routers import RaisingRouter, RecordingRouter

SNAPSHOT_TOPIC = "world.snapshot"


@asynccontextmanager
async def client_for(router: RouterPort, **settings: object) -> AsyncIterator[AsyncClient]:
    app = create_app(settings=Settings(**settings), router=router)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://backend"
        ) as client:
            yield client


@pytest_asyncio.fixture
async def routed() -> AsyncIterator[tuple[AsyncClient, RecordingRouter]]:
    router = RecordingRouter()
    async with client_for(router) as client:
        yield client, router


async def ingest(client: AsyncClient, topic: str, message: dict) -> tuple[int, dict]:
    response = await client.post("/ingest", json={"topic": topic, "message": message})
    return response.status_code, response.json()


async def test_a_new_snapshot_is_accepted_and_reaches_the_router(
    routed: tuple[AsyncClient, RecordingRouter],
) -> None:
    client, router = routed

    code, body = await ingest(
        client,
        SNAPSHOT_TOPIC,
        world_snapshot(
            sequence=1842,
            active_conversation={"conversation_id": "conversation-07", "npc_id": SHOPKEEPER},
        ),
    )
    assert (code, body["outcome"]) == (202, "applied")

    observed = await client.get(f"/routing/{SESSION_ID}/{WORLD_ID}")
    assert observed.status_code == 200
    assert observed.json() == {
        "session_id": SESSION_ID,
        "world_id": WORLD_ID,
        "source_sequence": 1842,
        "status": "routed",
        "failure_reason": None,
        "assignments": [
            {"npc_id": SHOPKEEPER, "tier": "focused", "reasons": []},
            {"npc_id": THIEF, "tier": "ambient", "reasons": []},
        ],
    }
    assert [snapshot.source_sequence for snapshot in router.routed] == [1842]


async def test_a_duplicate_or_stale_snapshot_does_not_regress_state(
    routed: tuple[AsyncClient, RecordingRouter],
) -> None:
    client, router = routed
    await ingest(client, SNAPSHOT_TOPIC, world_snapshot(sequence=7))

    duplicate = await ingest(client, SNAPSHOT_TOPIC, world_snapshot(sequence=7))
    stale = await ingest(client, SNAPSHOT_TOPIC, world_snapshot(sequence=3))

    assert duplicate[0] == 200 and duplicate[1]["outcome"] == "stale"
    assert stale[0] == 200 and stale[1]["outcome"] == "stale"

    observed = await client.get(f"/routing/{SESSION_ID}/{WORLD_ID}")
    assert observed.json()["source_sequence"] == 7
    assert [snapshot.source_sequence for snapshot in router.routed] == [7]


async def test_an_invalid_snapshot_is_rejected_before_state_changes(
    routed: tuple[AsyncClient, RecordingRouter],
) -> None:
    client, router = routed

    code, body = await ingest(client, SNAPSHOT_TOPIC, world_snapshot(sequence=0))

    assert code == 422
    assert body["outcome"] == "invalid"
    assert "sequence" in body["detail"]
    assert router.routed == []
    assert (await client.get(f"/routing/{SESSION_ID}/{WORLD_ID}")).status_code == 404


async def test_more_candidates_than_configured_are_rejected() -> None:
    router = RecordingRouter()
    async with client_for(router, max_snapshot_candidates=1) as client:
        code, body = await ingest(client, SNAPSHOT_TOPIC, world_snapshot())

        assert code == 422
        assert "at most 1 candidates" in body["detail"]
        assert router.routed == []


async def test_an_unknown_topic_is_rejected(routed: tuple[AsyncClient, RecordingRouter]) -> None:
    client, _ = routed

    code, body = await ingest(client, "world.weather", world_snapshot())

    assert code == 400
    assert body["outcome"] == "unknown_topic"


async def test_a_legacy_profile_record_is_accepted_and_ignored(
    routed: tuple[AsyncClient, RecordingRouter],
) -> None:
    client, router = routed

    code, body = await ingest(client, "npc.profile", {"npc_id": SHOPKEEPER, "name": "Mara"})

    assert code == 200
    assert body["outcome"] == "ignored"
    assert "ignored" in body["detail"]
    assert router.routed == []


async def test_a_malformed_envelope_is_rejected(
    routed: tuple[AsyncClient, RecordingRouter],
) -> None:
    client, _ = routed

    response = await client.post("/ingest", json={"topic": SNAPSHOT_TOPIC})

    assert response.status_code == 422


async def test_a_router_failure_is_observable_without_an_invented_tier() -> None:
    async with client_for(RaisingRouter()) as client:
        code, _ = await ingest(client, SNAPSHOT_TOPIC, world_snapshot(sequence=5))
        assert code == 202

        observed = (await client.get(f"/routing/{SESSION_ID}/{WORLD_ID}")).json()
        assert observed["status"] == "router_failed"
        assert observed["assignments"] == []
        assert "router exploded" in observed["failure_reason"]


async def test_snapshot_intake_stops_at_the_routing_outcome(
    routed: tuple[AsyncClient, RecordingRouter],
) -> None:
    client, router = routed
    await ingest(
        client,
        SNAPSHOT_TOPIC,
        world_snapshot(
            sequence=1,
            candidates=[candidate(SHOPKEEPER)],
            active_conversation={"conversation_id": "conversation-07", "npc_id": SHOPKEEPER},
        ),
    )

    observed = (await client.get(f"/routing/{SESSION_ID}/{WORLD_ID}")).json()

    assert observed["assignments"] == [
        {"npc_id": SHOPKEEPER, "tier": "focused", "reasons": []}
    ]
    assert len(router.routed) == 1
    assert (await client.get("/commands")).status_code == 404


async def test_liveness_and_readiness_distinguish_running_from_ready() -> None:
    app = create_app(settings=Settings(), router=RecordingRouter())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://backend"
    ) as client:
        assert (await client.get("/health/live")).json() == {"status": "alive"}

        not_ready = await client.get("/health/ready")
        assert not_ready.status_code == 503

        async with LifespanManager(app):
            ready = await client.get("/health/ready")
            assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
