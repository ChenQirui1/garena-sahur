"""The owned boundary through which orchestration reaches the Attention Router.

Owner: Jerome & Richard

The Router implementation itself is owned by Elson & Daniel. This module carries only the
call shape; scoring, propagation, capacity, hysteresis, and tier state live behind it. The
input models mirror the team's recommended Router input so the documented bounds are enforced
before the Router sees a snapshot. The result is a foreign object, so it stays a plain type
that `router_handoff` can reject observably instead of raising inside the routing worker.
Coordination issue #3 still owns the final shared types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

SNAPSHOT_SCHEMA_VERSION: Literal["1.0"] = "1.0"
SNAPSHOT_TYPE: Literal["routing_snapshot"] = "routing_snapshot"
RESULT_SCHEMA_VERSION = "1.0"
RESULT_TYPE = "routing_result"


class AttentionTier(StrEnum):
    FOCUSED = "focused"
    REACTIVE = "reactive"
    AMBIENT = "ambient"


class RouterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidatePolicy(RouterInput):
    entry_radius_blocks: float = Field(ge=0)
    exit_radius_blocks: float = Field(gt=0)

    @model_validator(mode="after")
    def check_exit_radius_exceeds_entry(self) -> CandidatePolicy:
        if self.exit_radius_blocks <= self.entry_radius_blocks:
            raise ValueError("exit_radius_blocks must be greater than entry_radius_blocks")
        return self


# The recommended conversation states in the handoff contract.
RouterConversationState = Literal["engaged", "awaiting_player", "awaiting_npc", "ending"]


class ActiveConversation(RouterInput):
    """The router-facing projection of the conversation currently receiving the player."""

    conversation_id: str
    target_npc_id: str
    state: RouterConversationState
    started_at_ms: int
    latest_turn_id: str | None = None


class RoutingNpc(RouterInput):
    npc_id: str
    world_distance_blocks: float = Field(ge=0)
    viewport_center_distance: float = Field(ge=0, le=1)
    inside_viewport: bool
    line_of_sight: bool
    event_relevance: float = Field(ge=0, le=1)
    event_roles: list[str]
    interaction_recency: float = Field(ge=0, le=1)


class AttentionEdge(RouterInput):
    source_npc_id: str
    target_npc_id: str
    # Open while the three recommended kinds are unconfirmed (#3): an unrecognised kind must
    # not reject an otherwise valid snapshot.
    kind: str = Field(min_length=1)
    active: bool


class RoutingSnapshot(RouterInput):
    """The backend-enriched, stable-shape input the Router consumes."""

    schema_version: Literal["1.0"]
    snapshot_type: Literal["routing_snapshot"]

    session_id: str
    world_id: str
    sequence: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)

    candidate_policy: CandidatePolicy
    active_event_ids: list[str]
    active_conversation: ActiveConversation | None

    candidate_count: int = Field(ge=0)
    npcs: list[RoutingNpc]
    attention_edges: list[AttentionEdge]

    @model_validator(mode="after")
    def check_candidate_set_is_consistent(self) -> RoutingSnapshot:
        npc_ids = [npc.npc_id for npc in self.npcs]
        if self.candidate_count != len(npc_ids):
            raise ValueError("candidate_count must equal len(npcs)")
        if len(set(npc_ids)) != len(npc_ids):
            raise ValueError("duplicate npc_id in routing snapshot")
        return self


@dataclass(frozen=True, slots=True)
class RoutingAssignment:
    npc_id: str
    tier: AttentionTier
    previous_tier: AttentionTier | None
    changed: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingResult:
    """What the Router returned for one routed snapshot, before the backend trusts it."""

    schema_version: str
    result_type: str
    session_id: str
    world_id: str
    sequence: int
    timestamp_ms: int
    assignments: tuple[RoutingAssignment, ...]


class RouterPort(Protocol):
    """One persistent Router instance per service lifecycle."""

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        """Assign tiers for one snapshot, synchronously and without performing I/O.

        The backend serializes routing by never awaiting between choosing a snapshot and
        returning from this call. A `route` that awaited, blocked, or reached a network or a
        disk would break that, so a caller could interleave two routings of the same session
        and the second would be handed a sequence the Router had already moved past. #3 owns
        confirming this with Elson & Daniel.
        """
        ...

    def reset_session(self, session_id: str) -> None: ...
