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
from backend.models.mock_provider import MockProvider
from backend.models.model_gateway import ModelGateway
from backend.orchestration.behaviour_publisher import (
    BehaviourPublisher,
    LoggingPublisher,
    PublisherPort,
)
from backend.orchestration.clock import Clock, SystemClock
from backend.orchestration.command_store import CommandStore
from backend.orchestration.conversation_manager import ConversationManager
from backend.orchestration.deduplication import GenerationClaims
from backend.orchestration.development_router import AmbientOnlyRouter
from backend.orchestration.event_relevance import EventRadii
from backend.orchestration.generation_coordinator import GenerationCoordinator
from backend.orchestration.interaction_recency import InteractionRecency
from backend.orchestration.observations import Observations
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.router_port import RouterPort
from backend.orchestration.routing_snapshot import RoutingSnapshots
from backend.orchestration.telemetry_port import LoggingTelemetry, TelemetryPort


@dataclass(frozen=True, slots=True)
class Adapters:
    """The seams a test or a later integration ticket replaces."""

    router: RouterPort | None = None
    publisher: PublisherPort | None = None
    telemetry: TelemetryPort | None = None
    clock: Clock | None = None


@dataclass(frozen=True, slots=True)
class Pipeline:
    """The owned intake-to-command pipeline, shared by every transport adapter."""

    intake: IntakeService
    generation: GenerationCoordinator
    commands: CommandStore
    handoff: RouterHandoff
    store: DurableStore
    observations: Observations
    readiness_error: str | None

    @property
    def is_ready(self) -> bool:
        return (
            self.readiness_error is None and self.store.is_open and self.handoff.is_running
        )


def build_pipeline(settings: Settings, adapters: Adapters = Adapters()) -> Pipeline:
    """Wire one persistent Router and one durable store into one service lifecycle."""
    profiles, readiness_error = _load_profiles(settings)
    clock = adapters.clock or SystemClock()
    store = DurableStore(settings.database_path)
    turns = TurnStore(store)
    events = EventStore(store)
    commands = CommandStore(store)
    world_state = WorldStateStore()
    conversation = ConversationManager()
    handoff = RouterHandoff(adapters.router or AmbientOnlyRouter())
    observations = Observations()
    provider = MockProvider(settings.characters_per_token)
    radii = EventRadii(
        witness_blocks=settings.witness_radius_blocks,
        nearby_blocks=settings.nearby_radius_blocks,
    )
    recency = InteractionRecency(clock)
    routing_snapshots = RoutingSnapshots(events=events, recency=recency, radii=radii)

    generation = GenerationCoordinator(
        world_state=world_state,
        events=events,
        conversation=conversation,
        handoff=handoff,
        routing_snapshots=routing_snapshots,
        claims=GenerationClaims(store),
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
        gateway=ModelGateway(focused=provider, reactive=provider),
        commands=commands,
        publisher=BehaviourPublisher(commands, adapters.publisher or LoggingPublisher()),
        telemetry=adapters.telemetry or LoggingTelemetry(),
        observations=observations,
        clock=clock,
        radii=radii,
        command_lifetime_ms=settings.command_lifetime_ms,
    )

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
        commands=commands,
        handoff=handoff,
        store=store,
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
        try:
            yield
        finally:
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
