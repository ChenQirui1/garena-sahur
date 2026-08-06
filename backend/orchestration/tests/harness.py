"""The running service plus the fakes an owned component trace ends at.

Owner: Jerome & Richard

Component suites drive the whole pipeline through the HTTP intake boundary so their assertions
are about observable behaviour rather than about how validation, storage, enrichment, policy,
context, and publication are split up behind it. Passing here proves the owned pipeline only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.ingestion.tests.canonical_messages import (
    SESSION_ID,
    conversation_turn,
    game_event,
    world_snapshot,
)
from backend.main import Adapters, Pipeline, create_app
from backend.models.mock_provider import MockProvider
from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.router_port import RouterPort, RoutingNpc, RoutingSnapshot
from backend.orchestration.tests.fake_routers import RecordingRouter
from backend.orchestration.tests.fakes import (
    GatedProvider,
    ManualClock,
    RecordingPublisher,
    RecordingTelemetry,
)

# Routing can queue generation and generation can re-route, so settling alternates between the
# two until neither has anything left rather than draining each once.
SETTLE_ROUNDS = 8

PROFILE_DOCUMENT = """
{
  "version": 1,
  "profiles": [
    {
      "npc_id": "shopkeeper-uuid",
      "name": "Mira",
      "role": "market shopkeeper",
      "persona": "Runs the bread stall and knows every regular by name.",
      "speaking_style": "Warm, quick, a little breathless.",
      "relationships": [{"npc_id": "thief-uuid", "relation": "wary of"}]
    },
    {
      "npc_id": "thief-uuid",
      "name": "Corin",
      "role": "market thief",
      "persona": "Light fingered and always three stalls ahead.",
      "speaking_style": "Clipped and evasive.",
      "relationships": []
    },
    {
      "npc_id": "guard-uuid",
      "name": "Bram",
      "role": "market guard",
      "persona": "Watches the square and dislikes being surprised.",
      "speaking_style": "Blunt and official.",
      "relationships": [{"npc_id": "shopkeeper-uuid", "relation": "protective of"}]
    }
  ]
}
"""


class Harness:
    def __init__(
        self,
        client: AsyncClient,
        pipeline: Pipeline,
        publisher: RecordingPublisher,
        telemetry: RecordingTelemetry,
        clock: ManualClock,
        router: RouterPort,
        provider: GatedProvider,
    ) -> None:
        self.client = client
        self.pipeline = pipeline
        self.publisher = publisher
        self.telemetry = telemetry
        self.clock = clock
        self.router = router
        self.provider = provider

    @property
    def routed(self) -> list[RoutingSnapshot]:
        """Every snapshot the Router saw, for the suites that run a recording one."""
        assert isinstance(self.router, RecordingRouter)
        return self.router.routed

    async def ingest(self, topic: str, message: dict[str, Any]) -> Any:
        return await self.client.post("/ingest", json={"topic": topic, "message": message})

    async def snapshot(self, **overrides: Any) -> Any:
        return await self.ingest("world.snapshot", world_snapshot(**overrides))

    async def turn(self, **overrides: Any) -> Any:
        return await self.ingest("conversation.turn", conversation_turn(**overrides))

    async def event(self, revision: int = 1, **overrides: Any) -> Any:
        return await self.ingest("game.event", game_event(revision, **overrides))

    async def settle_routing(self) -> None:
        """Wait for the coalescing routing worker, which snapshot refresh runs on."""
        await self.pipeline.handoff.wait_until_idle()

    async def settle(self) -> None:
        """Wait for routing and generation to finish, including what each starts in the other.

        Intake now returns once work is queued, so a component assertion has to say when it
        expects the queue to have drained rather than assuming the HTTP response meant it had.
        """
        for _ in range(SETTLE_ROUNDS):
            await self.pipeline.handoff.wait_until_idle()
            await self.pipeline.scheduler.drain()
            if self.pipeline.handoff.is_idle:
                return
        raise AssertionError("routing and generation did not settle")

    def pending_generation_count(self) -> int:
        return self.pipeline.scheduler.pending_count

    def state(self) -> ConversationState:
        return self.pipeline.intake.conversation.state(SESSION_ID)

    def observed(self, name: str) -> list[dict[str, object]]:
        return [
            dict(observation.fields)
            for observation in self.pipeline.observations.recorded
            if observation.name == name
        ]

    def routed_npc(self, npc_id: str) -> RoutingNpc:
        """The enrichment the Router last saw for one candidate."""
        return next(npc for npc in self.routed[-1].npcs if npc.npc_id == npc_id)

    def published_for(self, npc_id: str) -> list[BehaviourCommand]:
        return [command for command in self.publisher.published if command.npc_id == npc_id]


def settings_for(tmp_path: Path, profiles: str = PROFILE_DOCUMENT, **overrides: Any) -> Settings:
    profile_path = tmp_path / "npc_profiles.json"
    profile_path.write_text(profiles)
    return Settings(
        database_path=tmp_path / "state" / "spotlight.sqlite3",
        npc_profiles_path=profile_path,
        **overrides,
    )


async def running(
    settings: Settings, router: RouterPort | None = None, gated: bool = False
) -> AsyncIterator[Harness]:
    clock = ManualClock()
    publisher = RecordingPublisher()
    telemetry = RecordingTelemetry()
    router = router or RecordingRouter()
    provider = GatedProvider(MockProvider(settings.characters_per_token), gated=gated)
    app = create_app(
        settings,
        Adapters(
            router=router,
            publisher=publisher,
            telemetry=telemetry,
            clock=clock,
            provider=provider,
        ),
    )
    pipeline: Pipeline = app.state.pipeline
    publisher.bind(pipeline.commands)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://backend") as client:
            yield Harness(client, pipeline, publisher, telemetry, clock, router, provider)
