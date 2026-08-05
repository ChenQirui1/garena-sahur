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
from backend.ingestion.message_validation import (
    TOPIC_CONVERSATION_TURN,
    TOPIC_LEGACY_NPC_PROFILE,
    TOPIC_WORLD_SNAPSHOT,
    ConversationTurn,
    MessageValidationError,
    WorldSnapshot,
    validate_conversation_turn,
    validate_world_snapshot,
)
from backend.ingestion.turn_store import TurnStore
from backend.ingestion.world_state_store import WorldStateStore
from backend.orchestration.conversation_manager import ConversationManager, TurnAdmission
from backend.orchestration.generation_coordinator import GenerationCoordinator
from backend.orchestration.observations import UNCONFIRMED_TURN_DISCARDED, Observations
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.routing_snapshot import build_routing_snapshot

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
        conversation: ConversationManager,
        handoff: RouterHandoff,
        generation: GenerationCoordinator,
        observations: Observations,
        max_snapshot_candidates: int,
    ) -> None:
        self.turns = turns
        self.conversation = conversation
        self._world_state = world_state
        self._handoff = handoff
        self._generation = generation
        self._observations = observations
        self._max_snapshot_candidates = max_snapshot_candidates

    async def submit(self, topic: str, message: object) -> IntakeResult:
        if topic == TOPIC_LEGACY_NPC_PROFILE:
            return IntakeResult(IntakeOutcome.IGNORED, IGNORED_LEGACY_PROFILE_DETAIL)
        if topic == TOPIC_WORLD_SNAPSHOT:
            return await self._submit_world_snapshot(message)
        if topic == TOPIC_CONVERSATION_TURN:
            return await self._submit_conversation_turn(message)
        return IntakeResult(IntakeOutcome.UNKNOWN_TOPIC, f"unknown topic: {topic!r}")

    async def _submit_world_snapshot(self, message: object) -> IntakeResult:
        try:
            snapshot = validate_world_snapshot(message)
        except MessageValidationError as invalid:
            return IntakeResult(IntakeOutcome.INVALID, str(invalid))

        if len(snapshot.npcs) > self._max_snapshot_candidates:
            return IntakeResult(
                IntakeOutcome.INVALID,
                f"npcs: at most {self._max_snapshot_candidates} candidates per snapshot",
            )

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

        await self._act_on(self.conversation.admit_turn(turn))
        return IntakeResult(IntakeOutcome.APPLIED)

    async def _refresh_routing(self, snapshot: WorldSnapshot) -> None:
        """Snapshot arrival refreshes routing; it never asks for generation by itself."""
        admission = self.conversation.observe_snapshot(snapshot)
        self._handoff.submit(
            build_routing_snapshot(
                snapshot, self.conversation.projection(snapshot.session_id)
            )
        )
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
