"""Public package surface for Spotlight's Attention Router.

Owner: Elson & Daniel

The concrete Router is added when routing behaviour is implemented. For now the package
exports only the shared contracts required by the upcoming Router modules.
"""

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
    RoutingNpc,
    RoutingResult,
    RoutingSnapshot,
)

__all__ = [
    "RESULT_SCHEMA_VERSION",
    "RESULT_TYPE",
    "SNAPSHOT_SCHEMA_VERSION",
    "SNAPSHOT_TYPE",
    "ActiveConversation",
    "AttentionEdge",
    "AttentionTier",
    "CandidatePolicy",
    "RouterConversationState",
    "RouterInput",
    "RouterPort",
    "RoutingAssignment",
    "RoutingNpc",
    "RoutingResult",
    "RoutingSnapshot",
]
