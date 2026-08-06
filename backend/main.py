"""FastAPI application and the owned pipeline every transport adapter is built on.

Owner: Jerome & Richard
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.context.context_builder import ContextBuilder, ContextLimits
from backend.context.conversation_history import ConversationHistory
from backend.context.npc_profiles import NpcProfiles, ProfileDocumentError
from backend.ingestion import http_intake
from backend.ingestion.durable_store import DurableStore
from backend.ingestion.event_store import EventStore
from backend.ingestion.intake_service import IntakeService
from backend.ingestion.turn_store import TurnStore
from backend.ingestion.world_state_store import WorldStateStore
from backend.models.fallback import FallbackDocumentError, FallbackLibrary
from backend.models.mock_provider import MockProvider
from backend.models.model_gateway import ModelGateway, Provider
from backend.orchestration.behaviour_publisher import (
    BehaviourPublisher,
    LoggingPublisher,
    PublisherPort,
)
from backend.orchestration.clock import AsyncioDeadlines, Clock, Deadlines, SystemClock
from backend.orchestration.command_store import CommandStore
from backend.orchestration.conversation_manager import ConversationManager
from backend.orchestration.conversation_store import ConversationStore
from backend.orchestration.deduplication import GenerationClaims, ProviderAttempts
from backend.orchestration.development_router import AmbientOnlyRouter
from backend.orchestration.event_relevance import EventRadii
from backend.orchestration.generation_coordinator import GenerationCoordinator
from backend.orchestration.generation_queue import GenerationQueue
from backend.orchestration.generation_scheduler import GenerationScheduler
from backend.orchestration.interaction_recency import InteractionRecency
from backend.orchestration.observations import Observations
from backend.orchestration.recovery import Recovered, Recovery
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.router_port import AttentionTier, RouterPort
from backend.orchestration.routing_snapshot import RoutingSnapshots
from backend.orchestration.session_cleanup import SessionCleanup
from backend.orchestration.telemetry_port import LoggingTelemetry, TelemetryPort


@dataclass(frozen=True, slots=True)
class Adapters:
    """The seams a test or a later integration ticket replaces."""

    router: RouterPort | None = None
    publisher: PublisherPort | None = None
    telemetry: TelemetryPort | None = None
    clock: Clock | None = None
    deadlines: Deadlines | None = None
    provider: Provider | None = None


@dataclass(frozen=True, slots=True)
class Pipeline:
    """The owned intake-to-command pipeline, shared by every transport adapter."""

    intake: IntakeService
    generation: GenerationCoordinator
    scheduler: GenerationScheduler
    commands: CommandStore
    handoff: RouterHandoff
    store: DurableStore
    recovery: Recovery
    cleanup: SessionCleanup
    observations: Observations
    readiness_error: str | None

    @property
    def is_ready(self) -> bool:
        return (
            self.readiness_error is None
            and self.store.is_open
            and self.handoff.is_running
            and self.scheduler.is_running
        )


def build_pipeline(settings: Settings, adapters: Adapters = Adapters()) -> Pipeline:
    """Wire one persistent Router and one durable store into one service lifecycle."""
    profiles, profile_error = _load_profiles(settings)
    fallback, fallback_error = _load_fallback(settings)
    readiness_error = profile_error or fallback_error
    clock = adapters.clock or SystemClock()
    deadlines = adapters.deadlines or AsyncioDeadlines()
    store = DurableStore(settings.database_path)
    turns = TurnStore(store)
    events = EventStore(store)
    commands = CommandStore(store)
    attempts = ProviderAttempts(store)
    world_state = WorldStateStore()
    observations = Observations()
    conversation = ConversationManager(ConversationStore(store, observations))
    handoff = RouterHandoff(adapters.router or AmbientOnlyRouter())
    provider = adapters.provider or MockProvider(settings.characters_per_token)
    telemetry = adapters.telemetry or LoggingTelemetry()
    publisher = BehaviourPublisher(
        commands=commands,
        publisher=adapters.publisher or LoggingPublisher(),
        deadlines=deadlines,
        clock=clock,
        observations=observations,
        retry_delays_ms=settings.publication_retry_delays_ms,
    )
    scheduler = GenerationScheduler(
        queue=GenerationQueue(
            focused_limit=settings.focused_concurrency,
            reactive_limit=settings.reactive_concurrency,
            total_limit=settings.total_concurrency,
        ),
        claims=GenerationClaims(store),
        observations=observations,
        clock=clock,
    )
    radii = EventRadii(
        witness_blocks=settings.witness_radius_blocks,
        nearby_blocks=settings.nearby_radius_blocks,
    )
    recency = InteractionRecency(clock)
    routing_snapshots = RoutingSnapshots(events=events, recency=recency, radii=radii)

    generation = GenerationCoordinator(
        world_state=world_state,
        events=events,
        turns=turns,
        conversation=conversation,
        handoff=handoff,
        routing_snapshots=routing_snapshots,
        scheduler=scheduler,
        context=ContextBuilder(
            profiles=profiles,
            history=ConversationHistory(turns),
            focused=ContextLimits(
                input_tokens=settings.focused_input_token_limit,
                output_tokens=settings.focused_output_token_limit,
                history_turns=settings.focused_history_turns,
            ),
            reactive=ContextLimits(
                input_tokens=settings.reactive_input_token_limit,
                output_tokens=settings.reactive_output_token_limit,
                history_turns=0,
            ),
            characters_per_token=settings.characters_per_token,
        ),
        gateway=ModelGateway(
            focused=provider,
            reactive=provider,
            deadlines=deadlines,
            timeouts_ms={
                AttentionTier.FOCUSED: settings.focused_timeout_ms,
                AttentionTier.REACTIVE: settings.reactive_timeout_ms,
            },
        ),
        fallback=fallback,
        attempts=attempts,
        commands=commands,
        publisher=publisher,
        telemetry=telemetry,
        observations=observations,
        clock=clock,
        radii=radii,
        command_lifetime_ms=settings.command_lifetime_ms,
        characters_per_token=settings.characters_per_token,
    )
    scheduler.bind(generation)
    handoff.listen(generation.on_routing_outcome)

    return Pipeline(
        intake=IntakeService(
            world_state=world_state,
            turns=turns,
            events=events,
            conversation=conversation,
            handoff=handoff,
            generation=generation,
            routing_snapshots=routing_snapshots,
            recency=recency,
            observations=observations,
            radii=radii,
            max_snapshot_candidates=settings.max_snapshot_candidates,
        ),
        generation=generation,
        scheduler=scheduler,
        commands=commands,
        handoff=handoff,
        store=store,
        recovery=Recovery(
            attempts=attempts,
            commands=commands,
            conversation=conversation,
            generation=generation,
            publisher=publisher,
            telemetry=telemetry,
            observations=observations,
            clock=clock,
        ),
        cleanup=SessionCleanup(
            store=store,
            world_state=world_state,
            conversation=conversation,
            handoff=handoff,
            observations=observations,
        ),
        observations=observations,
        readiness_error=readiness_error,
    )


def create_app(settings: Settings | None = None, adapters: Adapters = Adapters()) -> FastAPI:
    settings = settings or load_settings()
    pipeline = build_pipeline(settings, adapters)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await pipeline.store.open()
        await pipeline.handoff.start()
        await pipeline.scheduler.start()
        # Recovery runs after the scheduler so a republished command has somewhere to go, and
        # before intake opens so a redelivered turn cannot race the work it is recovering.
        await pipeline.recovery.run()
        try:
            yield
        finally:
            await pipeline.scheduler.stop()
            await pipeline.handoff.stop()
            await pipeline.store.close()

    app = FastAPI(title="Spotlight backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.pipeline = pipeline
    app.include_router(http_intake.router)
    return app


def _load_profiles(settings: Settings) -> tuple[NpcProfiles, str | None]:
    """A bad profile document makes the service unready rather than unstartable."""
    try:
        return NpcProfiles.load(settings.npc_profiles_path), None
    except ProfileDocumentError as unusable:
        return NpcProfiles.empty(), str(unusable)


def _load_fallback(settings: Settings) -> tuple[FallbackLibrary, str | None]:
    """Same rule as profiles, and the empty library still answers every request.

    Unreadable cached dialogue must not leave a spent provider attempt with nothing to publish,
    so the service reports itself unready while still being able to speak generically.
    """
    try:
        return FallbackLibrary.load(settings.cached_dialogue_path), None
    except FallbackDocumentError as unusable:
        return FallbackLibrary.empty(), str(unusable)
