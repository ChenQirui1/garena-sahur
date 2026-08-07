"""The running service plus the fakes an owned component trace ends at.

Owner: Jerome & Richard

Component suites drive the whole pipeline through the HTTP intake boundary so their assertions
are about observable behaviour rather than about how validation, storage, enrichment, policy,
context, and publication are split up behind it. Passing here proves the owned pipeline only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, NamedTuple

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.models.model_gateway import Provider
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
    ManualDeadlines,
    RecordingPublisher,
    RecordingTelemetry,
)

CACHED_DIALOGUE_DOCUMENT = """
{
  "version": 1,
  "by_npc_and_trigger": [
    {"npc_id": "shopkeeper-uuid", "trigger": "turn", "dialogue": "Cached for Mira's turn."}
  ],
  "by_role_and_event": [
    {"role": "actor", "event_type": "market_theft", "dialogue": "Cached for the thief."}
  ],
  "by_tier": {"focused": "Scripted focused line.", "reactive": "Scripted reactive line."},
  "generic": "Generic safe line."
}
"""

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


class DurableCounts(NamedTuple):
    """How many rows each durable table holds, named so a moved count says which one moved.

    A test that says what it expects table by table keeps saying it through a schema change
    that preserves behaviour, and a failure names the table rather than a tuple position.
    """

    turns: int
    events: int
    commands: int
    attempts: int
    claims: int
    sessions: int
    threads: int


_DURABLE_COUNTS = (
    "SELECT (SELECT COUNT(*) FROM conversation_turns),"
    " (SELECT COUNT(*) FROM game_events),"
    " (SELECT COUNT(*) FROM behaviour_commands),"
    " (SELECT COUNT(*) FROM provider_attempts),"
    " (SELECT COUNT(*) FROM generation_claims),"
    " (SELECT COUNT(*) FROM conversation_sessions),"
    " (SELECT COUNT(*) FROM conversation_threads)"
)


class Harness:
    def __init__(
        self,
        client: AsyncClient,
        pipeline: Pipeline,
        publisher: RecordingPublisher,
        telemetry: RecordingTelemetry,
        clock: ManualClock,
        deadlines: ManualDeadlines,
        router: RouterPort,
        provider: GatedProvider,
    ) -> None:
        self.client = client
        self.pipeline = pipeline
        self.publisher = publisher
        self.telemetry = telemetry
        self.clock = clock
        self.deadlines = deadlines
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
        This is the service's own drain, so a suite cannot pass against a laxer definition of
        settled than the one the JSONL replay reports on.
        """
        await self.pipeline.drain()

    def pending_generation_count(self) -> int:
        return self.pipeline.scheduler.pending_count

    async def durable_counts(self) -> DurableCounts:
        rows = await self.pipeline.store.connection.execute_fetchall(_DURABLE_COUNTS)
        return DurableCounts(*(int(value) for value in list(rows)[0]))

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


def settings_for(
    tmp_path: Path,
    profiles: str = PROFILE_DOCUMENT,
    cached_dialogue: str = CACHED_DIALOGUE_DOCUMENT,
    **overrides: Any,
) -> Settings:
    profile_path = tmp_path / "npc_profiles.json"
    profile_path.write_text(profiles)
    dialogue_path = tmp_path / "cached_dialogue.json"
    dialogue_path.write_text(cached_dialogue)
    return Settings(
        database_path=tmp_path / "state" / "spotlight.sqlite3",
        npc_profiles_path=profile_path,
        cached_dialogue_path=dialogue_path,
        **overrides,
    )


async def running(
    settings: Settings,
    router: RouterPort | None = None,
    gated: bool = False,
    publisher: RecordingPublisher | None = None,
    clock: ManualClock | None = None,
    provider: Provider | None = None,
) -> AsyncIterator[Harness]:
    """Start the service. Passing a publisher or clock in is how a restart reuses them.

    A recovery test needs the second process to publish into the same recorder as the first,
    and to carry the same wall-clock reading, or a command's 15-second lifetime would look
    fresh again simply because a new `ManualClock` started at the same instant.

    `provider` replaces the deterministic mock behind the same gate the mock runs behind, so a
    test that needs a provider which fails or hangs injects it through the application's own
    `Adapters` wiring rather than reaching into the gateway.
    """
    clock = clock or ManualClock()
    deadlines = ManualDeadlines(clock)
    publisher = publisher or RecordingPublisher()
    telemetry = RecordingTelemetry()
    router = router or RecordingRouter()
    gated_provider = GatedProvider(
        provider or MockProvider(settings.characters_per_token), gated=gated
    )
    app = create_app(
        settings,
        Adapters(
            router=router,
            publisher=publisher,
            telemetry=telemetry,
            clock=clock,
            deadlines=deadlines,
            provider=gated_provider,
        ),
    )
    pipeline: Pipeline = app.state.pipeline
    publisher.bind(pipeline.commands)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://backend") as client:
            yield Harness(
                client, pipeline, publisher, telemetry, clock, deadlines, router, gated_provider
            )
