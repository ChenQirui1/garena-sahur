"""The URLs the shipped Fabric mod is compiled against, answered by the owned pipeline.

Owner: Jerome & Richard

**Development only, and off unless configuration turns it on.** `SpotlightConfig` hard-codes
`POST /api/v1/messages` and `ws://…/api/v1/ws?session_id=…`, and the canonical service serves
neither. Rather than ask Ivan to change a shipped mod for the demo, this adapter answers those
two URLs, translates through `prototype_wire`, and submits to the same `IntakeService` that
`/ingest` uses. ADR 0012 records the departure from the deployment boundary. **It retires at
issue #11**, when the mod publishes canonically over the live transport.

Commands go back the way the mod expects them: the exact bytes `CommandStore` committed, to
every socket registered for that session. A session nobody is listening to is a failed
publication rather than a silent success, which hands the decision to `BehaviourPublisher` — it
already retries while the command is inside its lifetime and expires it with an observation
afterwards. Orchestration is never blocked and never fails on a missing subscriber.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect, status

from backend.ingestion.http_intake import STATUS_FOR_OUTCOME
from backend.ingestion.intake_service import IntakeOutcome, IntakeResult, IntakeService
from backend.ingestion.prototype_wire import PrototypeTranslationError, PrototypeWire

if TYPE_CHECKING:
    from backend.orchestration.command_store import StoredCommand

PUBLISH_PATH = "/api/v1/messages"
SUBSCRIBE_PATH = "/api/v1/ws"

# How long one socket may take the bytes. A command is only worth applying inside its own
# lifetime, and a connection that cannot accept a few hundred bytes in this long is not going to
# deliver one in time — so a stalled socket is dropped and retried rather than awaited. Without
# a bound, a half-open mod would hold the publication path open instead of failing it.
SEND_TIMEOUT_SECONDS = 1.0


class NoCommandSubscriber(RuntimeError):
    """No socket is registered for the session this command belongs to."""


class CommandConnection(Protocol):
    """The one thing this adapter asks of a connected mod."""

    async def send_text(self, data: str) -> None: ...


class CommandSubscribers:
    """Which sockets are listening for which session's commands."""

    def __init__(self) -> None:
        self._by_session: dict[str, list[CommandConnection]] = {}

    async def join(self, session_id: str, connection: CommandConnection) -> None:
        self._by_session.setdefault(session_id, []).append(connection)

    def leave(self, session_id: str, connection: CommandConnection) -> None:
        listening = self._by_session.get(session_id)
        if listening is None:
            return
        if connection in listening:
            listening.remove(connection)
        if not listening:
            self._by_session.pop(session_id, None)

    async def send(self, session_id: str, payload: str) -> int:
        """Deliver to every socket on this session; report how many took it.

        A socket that fails or stalls mid-send is dropped rather than awaited: the mod
        reconnects, and a command held for a connection that has gone is a command the next one
        will not want.
        """
        delivered = 0
        for connection in list(self._by_session.get(session_id, ())):
            try:
                async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                    await connection.send_text(payload)
            except Exception:
                self.leave(session_id, connection)
            else:
                delivered += 1
        return delivered


class WebSocketCommandPublisher:
    """The `PublisherPort` the development bridge satisfies."""

    def __init__(self, subscribers: CommandSubscribers) -> None:
        self._subscribers = subscribers

    async def publish(self, command: "StoredCommand") -> None:
        session_id = command.command.session_id
        if await self._subscribers.send(session_id, command.serialized) == 0:
            raise NoCommandSubscriber(f"no Minecraft subscriber for session {session_id!r}")


def _intake_service(request: Request) -> IntakeService:
    return cast(IntakeService, request.app.state.pipeline.intake)


def _prototype_wire(request: Request) -> PrototypeWire:
    return cast(PrototypeWire, request.app.state.prototype_wire)


router = APIRouter()


@router.post(PUBLISH_PATH)
async def publish(
    payload: dict[str, Any],
    response: Response,
    service: Annotated[IntakeService, Depends(_intake_service)],
    wire: Annotated[PrototypeWire, Depends(_prototype_wire)],
) -> dict[str, str | None]:
    """Accept one bare payload — prototype or canonical — as the mod publishes it."""
    try:
        translated = wire.translate(payload)
    except PrototypeTranslationError as untranslatable:
        result = IntakeResult(IntakeOutcome.INVALID, str(untranslatable))
    else:
        result = await service.submit(translated.topic, translated.message)
    response.status_code = STATUS_FOR_OUTCOME[result.outcome]
    return {"outcome": result.outcome.value, "detail": result.detail}


@router.websocket(SUBSCRIBE_PATH)
async def subscribe(websocket: WebSocket, session_id: str) -> None:
    """Hold one mod's command socket open until it goes away.

    The mod never sends anything on this socket, so receiving is only how a close is noticed.
    """
    subscribers = cast(CommandSubscribers, websocket.app.state.command_subscribers)
    await websocket.accept()
    await subscribers.join(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        subscribers.leave(session_id, websocket)
