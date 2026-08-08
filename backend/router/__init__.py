"""Public package surface for Spotlight's Attention Router.

Owner: Elson & Daniel
"""

from backend.router.config import RouterConfig
from backend.router.graph import PropagatedCandidate, propagate_attention
from backend.router.models import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    ActiveConversation,
    AttentionEdge,
    AttentionTier,
    CandidatePolicy,
    RouterConversationState,
    RouterInput,
    RouterPort,
    RoutingAssignment,
    RoutingDiagnostics,
    RoutingNpc,
    RoutingResult,
    RoutingSnapshot,
    TierCounts,
)
from backend.router.router import Router, StaleSnapshotError

__all__ = [
    "RESULT_SCHEMA_VERSION",
    "RESULT_TYPE",
    "SNAPSHOT_SCHEMA_VERSION",
    "SNAPSHOT_TYPE",
    "ActiveConversation",
    "AttentionEdge",
    "AttentionTier",
    "CandidatePolicy",
    "PropagatedCandidate",
    "Router",
    "RouterConfig",
    "RouterConversationState",
    "RouterInput",
    "RouterPort",
    "RoutingAssignment",
    "RoutingDiagnostics",
    "RoutingNpc",
    "RoutingResult",
    "RoutingSnapshot",
    "StaleSnapshotError",
    "TierCounts",
    "propagate_attention",
]
