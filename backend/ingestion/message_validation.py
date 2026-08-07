"""The canonical schema-version-1.0 boundary wire messages must satisfy.

Owner: Jerome & Richard

Field names, nesting, and bounds come from the team-sent `world_snapshot` payload and the
Router handoff contract, never from house style. Where those sources are silent the boundary
stays open rather than inventing a rule that could reject a legitimate publisher (ADR 0004).
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

# `Final` so this keeps its literal type. The models below declare `schema_version` as
# `Literal["1.0"]`, and a constant inferred as plain `str` cannot be assigned to that — which is
# why `turn_store` rebuilding an accepted turn from its durable row did not type-check.
SCHEMA_VERSION: Final = "1.0"

TOPIC_WORLD_SNAPSHOT = "world.snapshot"
TOPIC_GAME_EVENT = "game.event"
TOPIC_CONVERSATION_TURN = "conversation.turn"
TOPIC_LEGACY_NPC_PROFILE = "npc.profile"

# The team payload shows only `started`; specification #1 fixes the full lifecycle. The set is
# closed because a status outside it has no defined revision rule — an event that could neither
# progress nor terminate would stay active forever. Recorded for #2.
EVENT_STATUS_STARTED = "started"
EVENT_STATUS_UPDATED = "updated"
EVENT_STATUS_ENDED = "ended"
EVENT_STATUS_CANCELLED = "cancelled"
TERMINAL_EVENT_STATUSES = frozenset({EVENT_STATUS_ENDED, EVENT_STATUS_CANCELLED})

# Specification #1: revisions start at one.
FIRST_EVENT_REVISION = 1

# The only speaker the team payload shows. The value stays open (#2 owns the enum) but this is
# the one that makes a turn a player utterance, and only a player utterance can trigger work.
SPEAKER_TYPE_PLAYER = "player"

# A JSON number, whether the publisher wrote it with a decimal point or not.
JsonNumber = float | int

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
NonNegativeNumber = Annotated[JsonNumber, Field(ge=0.0)]
UnitInterval = Annotated[JsonNumber, Field(ge=0.0, le=1.0)]


class MessageValidationError(ValueError):
    """A wire message did not satisfy the canonical schema-version-1.0 boundary."""


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Vector3(CanonicalModel):
    x: JsonNumber
    y: JsonNumber
    z: JsonNumber


class CandidatePolicy(CanonicalModel):
    """The entry and exit radii Minecraft used to select the candidate set."""

    entry_radius_blocks: NonNegativeNumber
    exit_radius_blocks: NonNegativeNumber

    @model_validator(mode="after")
    def check_exit_radius_exceeds_entry(self) -> CandidatePolicy:
        if self.exit_radius_blocks <= self.entry_radius_blocks:
            raise ValueError("exit_radius_blocks must be greater than entry_radius_blocks")
        return self


class Player(CanonicalModel):
    player_id: str
    position: Vector3
    look_direction: Vector3


class ActiveConversationRef(CanonicalModel):
    """Minecraft's reference to the one conversation receiving direct player interaction."""

    conversation_id: str
    target_npc_id: str


class NpcObservation(CanonicalModel):
    """One radius-selected NPC as observed by Minecraft in this snapshot."""

    npc_id: NonEmptyText
    position: Vector3
    world_distance_blocks: NonNegativeNumber
    viewport_center_distance: UnitInterval
    inside_viewport: bool
    line_of_sight: bool


class AttentionEdge(CanonicalModel):
    """A structural attention relation the backend passes through unweighted."""

    source_npc_id: str
    target_npc_id: str
    kind: NonEmptyText
    active: bool


class WorldSnapshot(CanonicalModel):
    """An ordered latest-value observation of the visible game state."""

    schema_version: Literal["1.0"]
    message_type: Literal["world_snapshot"]
    session_id: str
    world_id: str
    sequence: Annotated[int, Field(ge=0)]
    timestamp_ms: Annotated[int, Field(ge=0)]
    candidate_policy: CandidatePolicy
    player: Player
    active_conversation: ActiveConversationRef | None = None
    candidate_count: Annotated[int, Field(ge=0)]
    npcs: list[NpcObservation]
    attention_edges: list[AttentionEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_candidate_set_is_consistent(self) -> WorldSnapshot:
        npc_ids = [observation.npc_id for observation in self.npcs]
        if self.candidate_count != len(npc_ids):
            raise ValueError(f"candidate_count must equal len(npcs) ({len(npc_ids)})")
        if len(set(npc_ids)) != len(npc_ids):
            raise ValueError("npcs must have unique npc_id values")

        candidates = set(npc_ids)
        if self.active_conversation and self.active_conversation.target_npc_id not in candidates:
            raise ValueError("active_conversation.target_npc_id must be a candidate npc_id")
        for edge in self.attention_edges:
            if not candidates.issuperset({edge.source_npc_id, edge.target_npc_id}):
                raise ValueError("attention_edges must reference candidate npc_id values")

        return self


class GameEvent(CanonicalModel):
    """One complete-state revision of a durable lifecycle fact about the game world.

    Delivery identity (`message_id`) prevents replay; `event_id` and `event_revision` track the
    occurrence as it changes. The three role arrays may be empty and may overlap: the handoff
    contract §12.4 expects one NPC to hold several roles at once.
    """

    schema_version: Literal["1.0"]
    message_type: Literal["game_event"]
    session_id: NonEmptyText
    message_id: NonEmptyText
    event_id: NonEmptyText
    event_revision: Annotated[int, Field(ge=FIRST_EVENT_REVISION)]
    timestamp_ms: Annotated[int, Field(ge=0)]
    event_type: NonEmptyText
    status: Literal["started", "updated", "ended", "cancelled"]
    position: Vector3
    actor_npc_ids: list[str]
    target_npc_ids: list[str]
    responder_npc_ids: list[str]

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_EVENT_STATUSES


class ConversationTurn(CanonicalModel):
    """A durable utterance in a conversation, deduplicated by its turn identity."""

    schema_version: Literal["1.0"]
    message_type: Literal["conversation_turn"]
    session_id: NonEmptyText
    conversation_id: NonEmptyText
    turn_id: NonEmptyText
    turn_index: Annotated[int, Field(ge=0)]
    timestamp_ms: Annotated[int, Field(ge=0)]
    speaker_type: NonEmptyText
    speaker_id: NonEmptyText
    target_npc_id: NonEmptyText
    text: str

    @property
    def is_player_turn(self) -> bool:
        return self.speaker_type == SPEAKER_TYPE_PLAYER


def validate_world_snapshot(payload: object) -> WorldSnapshot:
    """Normalize a wire payload into a canonical world snapshot or reject it."""
    return _validate(WorldSnapshot, payload)


def validate_game_event(payload: object) -> GameEvent:
    """Normalize a wire payload into a canonical game-event revision or reject it."""
    return _validate(GameEvent, payload)


def validate_conversation_turn(payload: object) -> ConversationTurn:
    """Normalize a wire payload into a canonical conversation turn or reject it."""
    return _validate(ConversationTurn, payload)


def _validate[Canonical: CanonicalModel](
    model: type[Canonical], payload: object
) -> Canonical:
    try:
        return model.model_validate(payload)
    except ValidationError as invalid:
        raise MessageValidationError(_summarize(invalid)) from invalid


def _summarize(invalid: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in invalid.errors()
    )
