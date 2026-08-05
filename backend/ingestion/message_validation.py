"""The canonical schema-version-1.0 boundary wire messages must satisfy.

Owner: Jerome & Richard
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

SCHEMA_VERSION = "1.0"

TOPIC_WORLD_SNAPSHOT = "world.snapshot"
TOPIC_LEGACY_NPC_PROFILE = "npc.profile"

EARLIEST_ACCEPTED_TIMESTAMP_MS = 1_000_000_000_000
LATEST_ACCEPTED_TIMESTAMP_MS = 4_000_000_000_000
MAX_WORLD_DISTANCE_BLOCKS = 1_024.0

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")]
TimestampMs = Annotated[
    int, Field(ge=EARLIEST_ACCEPTED_TIMESTAMP_MS, le=LATEST_ACCEPTED_TIMESTAMP_MS)
]


class MessageValidationError(ValueError):
    """A wire message did not satisfy the canonical schema-version-1.0 boundary."""


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CandidateObservation(CanonicalModel):
    """One radius-selected NPC as observed by Minecraft in this snapshot."""

    npc_id: Identifier
    world_distance: Annotated[float, Field(ge=0.0, le=MAX_WORLD_DISTANCE_BLOCKS)]
    viewport_center_distance: Annotated[float, Field(ge=0.0, le=1.0)]
    visible: bool
    line_of_sight: bool


class ActiveConversationRef(CanonicalModel):
    """The one conversation currently receiving direct player interaction."""

    conversation_id: Identifier
    npc_id: Identifier


class AttentionEdge(CanonicalModel):
    """A structural attention relation the backend passes through unweighted."""

    source_npc_id: Identifier
    target_npc_id: Identifier
    relation: Identifier


class WorldSnapshot(CanonicalModel):
    """An ordered latest-value observation of the visible game state."""

    schema_version: Literal["1.0"]
    type: Literal["world_snapshot"]
    session_id: Identifier
    world_id: Identifier
    sequence: Annotated[int, Field(ge=1)]
    observed_at_ms: TimestampMs
    candidates: Annotated[list[CandidateObservation], Field(min_length=1)]
    active_conversation: ActiveConversationRef | None = None
    attention_edges: list[AttentionEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_references_resolve(self) -> WorldSnapshot:
        candidate_ids = [candidate.npc_id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidates must have unique npc_id values")

        known = set(candidate_ids)
        if self.active_conversation and self.active_conversation.npc_id not in known:
            raise ValueError("active_conversation.npc_id must be a candidate npc_id")

        seen_edges: set[tuple[str, str, str]] = set()
        for edge in self.attention_edges:
            if edge.source_npc_id == edge.target_npc_id:
                raise ValueError("attention_edges must not contain self edges")
            if not known.issuperset({edge.source_npc_id, edge.target_npc_id}):
                raise ValueError("attention_edges must reference candidate npc_id values")
            identity = (edge.source_npc_id, edge.target_npc_id, edge.relation)
            if identity in seen_edges:
                raise ValueError("attention_edges must be unique")
            seen_edges.add(identity)

        return self


def validate_world_snapshot(payload: object) -> WorldSnapshot:
    """Normalize a wire payload into a canonical world snapshot or reject it."""
    try:
        return WorldSnapshot.model_validate(payload)
    except ValidationError as invalid:
        raise MessageValidationError(_summarize(invalid)) from invalid


def _summarize(invalid: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in invalid.errors()
    )
