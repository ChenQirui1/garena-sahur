"""The Router stand-in the development service runs until the real Router exists.

Owner: Jerome & Richard

A working Router landed in `backend/router/` (teammate commit `208ed26`), but the port itself is
only frozen by coordination issue #3, so the owned pipeline still runs against a contract fake
here rather than calling it directly. This one authors no routing:
Ambient is the tier that requires no decision, so no scoring, propagation, capacity, or
hysteresis judgement is duplicated outside Elson & Daniel's Router. Issue #10 replaces it.
"""

from __future__ import annotations

from backend.orchestration.router_port import (
    RESULT_SCHEMA_VERSION,
    RESULT_TYPE,
    AttentionTier,
    RoutingAssignment,
    RoutingResult,
    RoutingSnapshot,
)

STAND_IN_REASON = "stand-in router: no attention routing available yet"


class AmbientOnlyRouter:
    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        return RoutingResult(
            schema_version=RESULT_SCHEMA_VERSION,
            result_type=RESULT_TYPE,
            session_id=snapshot.session_id,
            world_id=snapshot.world_id,
            sequence=snapshot.sequence,
            timestamp_ms=snapshot.timestamp_ms,
            assignments=tuple(
                RoutingAssignment(
                    npc_id=npc.npc_id,
                    tier=AttentionTier.AMBIENT,
                    previous_tier=None,
                    changed=False,
                    reasons=(STAND_IN_REASON,),
                )
                for npc in snapshot.npcs
            ),
        )

    def reset_session(self, session_id: str) -> None:
        return None
