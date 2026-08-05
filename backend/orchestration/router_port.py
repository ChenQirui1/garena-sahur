"""The owned boundary through which orchestration reaches the Attention Router.

Owner: Jerome & Richard

The Router implementation itself is owned by Elson & Daniel. This module carries only
the call shape; scoring, propagation, capacity, hysteresis, and tier state live behind it.
The exact shared types are still open in coordination issue #3, so owned work runs against
a stand-in that implements this same boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Sequence


class AttentionTier(StrEnum):
    FOCUSED = "focused"
    REACTIVE = "reactive"
    AMBIENT = "ambient"


@dataclass(frozen=True, slots=True)
class RoutingCandidate:
    npc_id: str
    world_distance: float
    viewport_center_distance: float
    visible: bool
    line_of_sight: bool
    in_active_conversation: bool


@dataclass(frozen=True, slots=True)
class RoutingAttentionEdge:
    source_npc_id: str
    target_npc_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class RoutingSnapshot:
    """The backend-enriched, stable-shape input the Router consumes."""

    session_id: str
    world_id: str
    source_sequence: int
    observed_at_ms: int
    candidates: tuple[RoutingCandidate, ...]
    active_conversation_npc_id: str | None
    attention_edges: tuple[RoutingAttentionEdge, ...]


@dataclass(frozen=True, slots=True)
class RoutingAssignment:
    npc_id: str
    tier: AttentionTier
    reasons: tuple[str, ...] = ()


class RouterPort(Protocol):
    """One persistent Router instance per service lifecycle."""

    def route(self, snapshot: RoutingSnapshot) -> Sequence[RoutingAssignment]: ...

    def reset_session(self, session_id: str) -> None: ...
