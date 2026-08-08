"""The mod's own URLs, answered by the owned pipeline.

Owner: Jerome & Richard

The trace is driven through `POST /api/v1/messages` and `/api/v1/ws` — the endpoints
`SpotlightConfig` compiles into the shipped mod — so the cases are about what the mod can
actually reach, not about the adapter's internals.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator, NamedTuple

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.ingestion import prototype_bridge
from backend.ingestion.message_validation import (
    TOPIC_CONVERSATION_TURN,
    TOPIC_GAME_EVENT,
    TOPIC_WORLD_SNAPSHOT,
    NpcObservation,
)
from backend.ingestion.prototype_bridge import (
    CommandSubscribers,
    WebSocketCommandPublisher,
)
from backend.ingestion.tests import canonical_messages, prototype_messages
from backend.ingestion.tests.prototype_messages import SESSION_ID
from backend.main import Adapters, Pipeline, create_app
from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.command_store import StoredCommand
from backend.orchestration.observations import COMMAND_PUBLICATION_EXPIRED
from backend.orchestration.tests.fake_routers import RecordingRouter
from backend.orchestration.tests.fakes import ManualClock, ManualDeadlines
from backend.orchestration.tests.harness import settings_for
from backend.tests.tracked_documents import documented_keys

PUBLISH = "/api/v1/messages"
SUBSCRIBE = f"/api/v1/ws?session_id={SESSION_ID}"

DOCUMENTED_SECTION = {
    TOPIC_WORLD_SNAPSHOT: "1. `world.snapshot`",
    TOPIC_GAME_EVENT: "2. `game.event`",
    TOPIC_CONVERSATION_TURN: "3. `conversation.turn`",
}


def _names_in(payload: object) -> set[str]:
    """Every key at every depth, so a prototype field cannot hide inside a nested object."""
    if isinstance(payload, dict):
        return set(payload) | {
            name for value in payload.values() for name in _names_in(value)
        }
    if isinstance(payload, list):
        return {name for entry in payload for name in _names_in(entry)}
    return set()


def prototype_field_names() -> frozenset[str]:
    """Every name the mod uses that the canonical boundary does not accept, derived rather than
    listed.

    Listing them would be a second source of truth: a field the mod grows later would be absent
    from the list, and the case below would keep passing while no longer covering it.

    The observation's own field names join the canonical fixtures because a name our boundary
    accepts is not prototype-shaped, whether or not the team's example payload happens to carry
    it. `profession` is the case in point — §1 omits it, the mod publishes it, and #58 made it a
    declared optional extension the intake accepts.
    """
    mods = (
        _names_in(prototype_messages.world_snapshot())
        | _names_in(prototype_messages.game_event())
        | _names_in(prototype_messages.conversation_turn())
    )
    ours = (
        _names_in(canonical_messages.world_snapshot())
        | _names_in(canonical_messages.game_event())
        | _names_in(canonical_messages.conversation_turn())
        | set(NpcObservation.model_fields)
    )
    return frozenset(mods - ours)


class Bridge(NamedTuple):
    client: AsyncClient
    app: FastAPI
    pipeline: Pipeline
    router: RecordingRouter


def bridge_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return settings_for(tmp_path, prototype_bridge_enabled=True, **overrides)


def app_for(settings: Settings, router: RecordingRouter, **adapters: Any) -> FastAPI:
    return create_app(settings, Adapters(router=router, **adapters))


@pytest_asyncio.fixture
async def bridge(tmp_path: Path) -> AsyncIterator[Bridge]:
    router = RecordingRouter()
    app = app_for(bridge_settings(tmp_path), router)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://backend"
        ) as client:
            yield Bridge(client, app, app.state.pipeline, router)


async def test_the_mods_snapshot_is_accepted_at_the_mods_url(bridge: Bridge) -> None:
    response = await bridge.client.post(PUBLISH, json=prototype_messages.world_snapshot())

    assert response.status_code == 202
    assert response.json() == {"outcome": "applied", "detail": None}


async def test_the_mods_event_is_accepted_at_the_mods_url(bridge: Bridge) -> None:
    await bridge.client.post(PUBLISH, json=prototype_messages.world_snapshot())
    response = await bridge.client.post(PUBLISH, json=prototype_messages.game_event())

    assert response.status_code == 202


async def test_the_mods_turn_is_accepted_at_the_mods_url(bridge: Bridge) -> None:
    await bridge.client.post(PUBLISH, json=prototype_messages.world_snapshot())
    response = await bridge.client.post(
        PUBLISH, json=prototype_messages.conversation_turn()
    )

    assert response.status_code == 202


async def test_a_canonical_payload_still_validates_unchanged(bridge: Bridge) -> None:
    """The endpoint is not only the prototype's; a canonical publisher must reach us too."""
    response = await bridge.client.post(PUBLISH, json=canonical_messages.world_snapshot())
    await bridge.pipeline.handoff.wait_until_idle()

    assert response.status_code == 202
    assert bridge.router.routed[-1].session_id == canonical_messages.SESSION_ID
    assert bridge.router.routed[-1].world_id == canonical_messages.WORLD_ID


async def test_the_prototype_snapshot_reaches_the_router_like_any_other(
    bridge: Bridge,
) -> None:
    """`/ingest` and this endpoint are one intake path, not two."""
    await bridge.client.post(PUBLISH, json=prototype_messages.world_snapshot())
    await bridge.pipeline.handoff.wait_until_idle()

    routed = bridge.router.routed[-1]
    assert routed.session_id == SESSION_ID
    assert routed.sequence == 1842
    assert [npc.npc_id for npc in routed.npcs] == [
        canonical_messages.SHOPKEEPER,
        canonical_messages.THIEF,
    ]


async def test_an_untranslatable_payload_is_refused_with_its_reason(
    bridge: Bridge,
) -> None:
    response = await bridge.client.post(PUBLISH, json={"type": "world.weather"})

    assert response.status_code == 422
    body = response.json()
    assert body["outcome"] == "invalid"
    assert "world.weather" in str(body["detail"])


async def test_nothing_prototype_shaped_reaches_the_intake_boundary(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every downstream module is behind `IntakeService.submit`, so this is where to look."""
    submitted: list[tuple[str, dict[str, Any]]] = []
    accepted = bridge.pipeline.intake.submit

    async def recording(topic: str, message: object) -> Any:
        assert isinstance(message, dict)
        submitted.append((topic, message))
        return await accepted(topic, message)

    monkeypatch.setattr(bridge.pipeline.intake, "submit", recording)

    await bridge.client.post(PUBLISH, json=prototype_messages.world_snapshot())
    await bridge.client.post(PUBLISH, json=prototype_messages.game_event())
    await bridge.client.post(PUBLISH, json=prototype_messages.conversation_turn())

    assert [topic for topic, _ in submitted] == [
        TOPIC_WORLD_SNAPSHOT,
        TOPIC_GAME_EVENT,
        TOPIC_CONVERSATION_TURN,
    ]
    prototype_only = prototype_field_names()
    assert prototype_only, "the fixtures no longer differ, so this case proves nothing"
    for topic, message in submitted:
        assert set(message) == documented_keys(DOCUMENTED_SECTION[topic])
        assert _names_in(message) & prototype_only == set()


async def test_the_envelope_endpoint_is_untouched(bridge: Bridge) -> None:
    """`/ingest` keeps its `{topic, message}` contract while the bridge is running."""
    response = await bridge.client.post(
        "/ingest",
        json={"topic": TOPIC_WORLD_SNAPSHOT, "message": canonical_messages.world_snapshot()},
    )

    assert response.status_code == 202


async def test_the_bridge_is_absent_unless_configuration_turns_it_on(
    tmp_path: Path,
) -> None:
    """Production takes the default, and the default is that these URLs do not exist."""
    app = app_for(settings_for(tmp_path), RecordingRouter())
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://backend"
        ) as client:
            response = await client.post(PUBLISH, json=prototype_messages.world_snapshot())

    assert response.status_code == 404
    assert Settings(database_path=tmp_path / "db").prototype_bridge_enabled is False


def test_the_bridge_declares_when_it_retires() -> None:
    """A temporary layer with no stated end is a permanent one.

    Pinned because the issue asks for it in those terms: the module docstring must say the
    adapter is development-only and name #11 as its retirement.
    """
    docstring = (prototype_bridge.__doc__ or "").lower()

    assert "#11" in docstring
    assert "development only" in docstring


async def test_a_command_is_delivered_only_to_its_own_session() -> None:
    subscribers = CommandSubscribers()
    mine, theirs = _FakeConnection(), _FakeConnection()
    await subscribers.join(SESSION_ID, mine)
    await subscribers.join("another-session", theirs)
    stored = _stored_command()

    await WebSocketCommandPublisher(subscribers).publish(stored)

    assert mine.sent == [stored.serialized]
    assert theirs.sent == []


async def test_a_stalled_socket_is_dropped_rather_than_waited_on() -> None:
    """A half-open mod must not hold the publication path open; only the command is lost.

    The wait is bounded here as well as in production. Without a bound of its own this case
    would inherit the very defect it exists to catch: removing the production timeout would
    hang it rather than fail it, and a hang reaches CI as a run that never finishes.
    """
    subscribers = CommandSubscribers()
    await subscribers.join(SESSION_ID, _StalledConnection())
    publisher = WebSocketCommandPublisher(subscribers)

    with pytest.raises(prototype_bridge.NoCommandSubscriber):
        async with asyncio.timeout(prototype_bridge.SEND_TIMEOUT_SECONDS * 5):
            await publisher.publish(_stored_command())

    assert await subscribers.send(SESSION_ID, "anything") == 0, "the socket was kept"


async def test_publication_without_a_subscriber_is_reported_as_a_failure() -> None:
    """`BehaviourPublisher` turns this into its retry cadence, which is the wanted behaviour:
    a mod connecting a second later still gets the command, and an absent one costs a
    bounded number of attempts inside the command's own lifetime."""
    with pytest.raises(prototype_bridge.NoCommandSubscriber):
        await WebSocketCommandPublisher(CommandSubscribers()).publish(_stored_command())


async def test_a_command_with_no_subscriber_expires_without_failing_orchestration(
    tmp_path: Path,
) -> None:
    """The mod may not be connected; that must cost the pipeline nothing but the command."""
    clock = ManualClock()
    app = app_for(
        bridge_settings(tmp_path),
        RecordingRouter(),
        clock=clock,
        deadlines=ManualDeadlines(clock),
    )
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://backend"
        ) as client:
            await client.post(PUBLISH, json=prototype_messages.world_snapshot())
            await client.post(PUBLISH, json=prototype_messages.conversation_turn())
            await client.post(
                PUBLISH, json=prototype_messages.world_snapshot(sequence=1843)
            )
            pipeline: Pipeline = app.state.pipeline
            await pipeline.drain()

            assert pipeline.is_ready

    expired = [
        observation
        for observation in pipeline.observations.recorded
        if observation.name == COMMAND_PUBLICATION_EXPIRED
    ]
    assert expired, "no command was generated, so nothing proves the absent subscriber"


def _received_within(subscriber: Any, seconds: float) -> Any:
    """Read one message, failing rather than waiting forever if none is coming.

    The test client's socket has no receive timeout, so a bridge that stops delivering would
    hang this case instead of failing it — which is how a broken delivery reaches CI as a run
    that never finishes rather than a red one. Closing the socket releases the worker.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(subscriber.receive_json).result(timeout=seconds)
    finally:
        pool.shutdown(wait=False)


def test_a_connected_mod_receives_the_behaviour_command(tmp_path: Path) -> None:
    """The whole demo path: prototype snapshot, prototype turn, command back over the socket."""
    app = app_for(bridge_settings(tmp_path), RecordingRouter())
    with TestClient(app) as client:
        with client.websocket_connect(SUBSCRIBE) as subscriber:
            for published in (
                prototype_messages.world_snapshot(),
                prototype_messages.conversation_turn(),
                prototype_messages.world_snapshot(sequence=1843),
            ):
                assert client.post(PUBLISH, json=published).status_code == 202

            command = _received_within(subscriber, seconds=10.0)

    assert command["message_type"] == "behaviour_command"
    assert command["session_id"] == SESSION_ID
    assert command["npc_id"] == canonical_messages.SHOPKEEPER
    assert command["conversation_id"] == prototype_messages.CONVERSATION_ID
    assert command["turn_id"] == prototype_messages.TURN_ID
    assert command["dialogue"]


class _FakeConnection:
    """A connected mod, without a socket. `CommandSubscribers` only ever sends text."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


class _StalledConnection:
    """A mod whose socket accepted the connection and then stopped reading."""

    async def send_text(self, data: str) -> None:
        await asyncio.Event().wait()


def _stored_command() -> StoredCommand:
    command = BehaviourCommand(
        session_id=SESSION_ID,
        command_id="command-322",
        request_id="request-0091",
        npc_id=canonical_messages.SHOPKEEPER,
        tier="focused",
        event_id=None,
        conversation_id=None,
        turn_id=None,
        source_sequence=1842,
        created_at_ms=1_786_208_500_984,
        expires_at_ms=1_786_208_515_000,
        dialogue="Towards the fountain!",
        action=None,
        fallback_used=False,
        command_sequence=1,
    )
    return StoredCommand(command=command, serialized=json.dumps(command.as_payload()))
