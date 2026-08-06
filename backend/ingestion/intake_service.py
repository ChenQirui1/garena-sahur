"""The canonical intake boundary every transport adapter converges on.

Owner: Jerome & Richard

A durable message is committed before its outcome is reported, so an acknowledged turn is one
the backend can still honour after a restart. What the message then arms — routing refresh or
one generation — is decided by conversation state, never by the transport that carried it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.ingestion.durable_store import StorageUnavailable
from backend.ingestion.event_store import EventStore, RevisionOutcome
from backend.ingestion.message_validation import (
    TOPIC_CONVERSATION_TURN,
    TOPIC_GAME_EVENT,
    TOPIC_LEGACY_NPC_PROFILE,
    TOPIC_WORLD_SNAPSHOT,
    ConversationTurn,
    GameEvent,
    MessageValidationError,
    WorldSnapshot,
    validate_conversation_turn,
    validate_game_event,
    validate_world_snapshot,
)
from backend.ingestion.turn_store import TurnStore
from backend.ingestion.world_state_store import WorldStateStore
from backend.orchestration.conversation_manager import ConversationManager, TurnAdmission
from backend.orchestration.event_relevance import EventRadii, witnesses_at_start
from backend.orchestration.generation_coordinator import GenerationCoordinator
from backend.orchestration.interaction_recency import InteractionRecency
from backend.orchestration.observations import (
    ROUTING_NOT_REFRESHED,
    UNCONFIRMED_TURN_DISCARDED,
    Observations,
)
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.routing_snapshot import RoutingSnapshots

IGNORED_LEGACY_PROFILE_DETAIL = (
    f"{TOPIC_LEGACY_NPC_PROFILE} is accepted for compatibility and ignored; "
    "profiles are loaded from the backend-owned local document"
)


class IntakeOutcome(StrEnum):
    APPLIED = "applied"
    STALE = "stale"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    INVALID = "invalid"
    UNKNOWN_TOPIC = "unknown_topic"
    STORAGE_UNAVAILABLE = "storage_unavailable"


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: IntakeOutcome
    detail: str | None = None


class IntakeService:
    """Validate a canonical message, update owned state, and hand the work off."""

    def __init__(
        self,
        world_state: WorldStateStore,
        turns: TurnStore,
        events: EventStore,
        conversation: ConversationManager,
        handoff: RouterHandoff,
        generation: GenerationCoordinator,
        routing_snapshots: RoutingSnapshots,
        recency: InteractionRecency,
        observations: Observations,
        radii: EventRadii,
    ) -> None:
        self.turns = turns
        self.events = events
        self.conversation = conversation
        self._world_state = world_state
        self._handoff = handoff
        self._generation = generation
        self._routing_snapshots = routing_snapshots
        self._recency = recency
        self._observations = observations
        self._radii = radii

    async def submit(self, topic: str, message: object) -> IntakeResult:
        if topic == TOPIC_LEGACY_NPC_PROFILE:
            return IntakeResult(IntakeOutcome.IGNORED, IGNORED_LEGACY_PROFILE_DETAIL)
        if topic == TOPIC_WORLD_SNAPSHOT:
            return await self._submit_world_snapshot(message)
        if topic == TOPIC_GAME_EVENT:
            return await self._submit_game_event(message)
        if topic == TOPIC_CONVERSATION_TURN:
            return await self._submit_conversation_turn(message)
        return IntakeResult(IntakeOutcome.UNKNOWN_TOPIC, f"unknown topic: {topic!r}")

    async def _submit_world_snapshot(self, message: object) -> IntakeResult:
        try:
            snapshot = validate_world_snapshot(message)
        except MessageValidationError as invalid:
            return IntakeResult(IntakeOutcome.INVALID, str(invalid))

        try:
            applied = self._world_state.apply_if_newer(snapshot)
        except StorageUnavailable as unavailable:
            return IntakeResult(IntakeOutcome.STORAGE_UNAVAILABLE, str(unavailable))

        if not applied:
            return IntakeResult(
                IntakeOutcome.STALE,
                f"sequence {snapshot.sequence} does not supersede retained state",
            )

        await self._refresh_routing(snapshot)
        return IntakeResult(IntakeOutcome.APPLIED)

    async def _submit_game_event(self, message: object) -> IntakeResult:
        try:
            event = validate_game_event(message)
        except MessageValidationError as invalid:
            return IntakeResult(IntakeOutcome.INVALID, str(invalid))

        try:
            recorded = await self.events.record(event, self._witnesses_for(event))
        except StorageUnavailable as unavailable:
            return IntakeResult(IntakeOutcome.STORAGE_UNAVAILABLE, str(unavailable))

        if recorded.outcome is RevisionOutcome.DUPLICATE:
            return IntakeResult(
                IntakeOutcome.DUPLICATE,
                f"revision {event.event_revision} of {event.event_id} is already stored",
            )
        if recorded.outcome is RevisionOutcome.REJECTED:
            return IntakeResult(IntakeOutcome.INVALID, recorded.reason)

        await self._generation.on_event_revision(event)
        return IntakeResult(IntakeOutcome.APPLIED)

    def _witnesses_for(self, event: GameEvent) -> frozenset[str]:
        """Who could have seen this, settled once from the world state at the event's start."""
        snapshot = self._world_state.latest_for_session(event.session_id)
        if snapshot is None:
            return frozenset()
        return witnesses_at_start(event, tuple(snapshot.npcs), self._radii)

    async def _submit_conversation_turn(self, message: object) -> IntakeResult:
        try:
            turn = validate_conversation_turn(message)
        except MessageValidationError as invalid:
            return IntakeResult(IntakeOutcome.INVALID, str(invalid))

        try:
            recorded = await self.turns.record(turn)
        except StorageUnavailable as unavailable:
            return IntakeResult(IntakeOutcome.STORAGE_UNAVAILABLE, str(unavailable))

        if not recorded:
            return IntakeResult(
                IntakeOutcome.DUPLICATE, f"turn {turn.turn_id} is already stored"
            )

        self._recency.note_interaction(turn.session_id, turn.target_npc_id)
        await self._act_on(await self.conversation.admit_turn(turn))
        return IntakeResult(IntakeOutcome.APPLIED)

    async def _refresh_routing(self, snapshot: WorldSnapshot) -> None:
        """Snapshot arrival refreshes routing; it never asks for generation by itself.

        Promotion and expiry are noticed when the refreshed result comes back, on the routing
        worker, so a burst of snapshots is coalesced before any of them is looked at.
        """
        admission = await self.conversation.observe_snapshot(snapshot)
        if snapshot.active_conversation is not None:
            # An open conversation is a live interaction, so its target keeps its recency
            # fresh and only starts decaying once the conversation is gone.
            self._recency.note_interaction(
                snapshot.session_id, snapshot.active_conversation.target_npc_id
            )

        try:
            routing = await self._routing_snapshots.build(
                snapshot, self.conversation.projection(snapshot.session_id)
            )
        except StorageUnavailable as unavailable:
            # World state is memory-only and stays applied, but enrichment is not: without the
            # durable events there is no honest routing snapshot to build, and one claiming no
            # event is active could demote an NPC in the middle of one.
            self._observations.note(
                ROUTING_NOT_REFRESHED,
                session_id=snapshot.session_id,
                sequence=snapshot.sequence,
                reason=str(unavailable),
            )
        else:
            self._handoff.submit(routing)

        await self._act_on(admission)

    async def _act_on(self, admission: TurnAdmission) -> None:
        if admission.discarded is not None:
            self._note_discarded(admission.discarded)
        if admission.triggered is not None:
            await self._generation.on_triggered_turn(admission.triggered)

    def _note_discarded(self, turn: ConversationTurn) -> None:
        self._observations.note(
            UNCONFIRMED_TURN_DISCARDED,
            session_id=turn.session_id,
            conversation_id=turn.conversation_id,
            turn_id=turn.turn_id,
        )
