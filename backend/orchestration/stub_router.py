"""A stand-in Router that keeps every candidate Ambient.

Owner: Jerome & Richard

The Attention Router is owned by Elson & Daniel and `backend/router/` is still a scaffold.
This stand-in proves the owned handoff without inventing routing: it never promotes an NPC,
so no scoring, capacity, or hysteresis decision is duplicated here. Coordination issue #3
freezes the real port; issue #10 replaces this with the real Router.
"""

from __future__ import annotations

from backend.orchestration.router_port import AttentionTier, RoutingAssignment, RoutingSnapshot

STAND_IN_REASON = "stand-in router: no attention routing available yet"


class AmbientStubRouter:
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
