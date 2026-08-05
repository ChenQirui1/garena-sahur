"""The Router stand-in the development service runs until the real Router exists.

Owner: Jerome & Richard

`backend/router/` is still a scaffold and the port itself is only frozen by coordination
issue #3, so the owned pipeline runs against a contract fake. This one authors no routing:
Ambient is the tier that requires no decision, so no scoring, propagation, capacity, or
hysteresis judgement is duplicated outside Elson & Daniel's Router. Issue #10 replaces it.
"""

from __future__ import annotations

from backend.orchestration.router_port import AttentionTier, RoutingAssignment, RoutingSnapshot

STAND_IN_REASON = "stand-in router: no attention routing available yet"


class AmbientOnlyRouter:
    def route(self, snapshot: RoutingSnapshot) -> tuple[RoutingAssignment, ...]:
        return tuple(
            RoutingAssignment(
                npc_id=candidate.npc_id,
                tier=AttentionTier.AMBIENT,
                reasons=(STAND_IN_REASON,),
            )
            for candidate in snapshot.candidates
        )

    def reset_session(self, session_id: str) -> None:
        return None
