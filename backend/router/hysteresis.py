"""Promote quickly, demote slowly; prevent tier flickering.

Owner: Elson & Daniel
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.router.config import RouterConfig
from backend.router.models import AttentionTier
from backend.router.state import NpcRoutingState


@dataclass(frozen=True, slots=True)
class HysteresisAdjustment:
    """Ranking evidence contributed by an NPC's previous attention tier."""

    effective_score: float
    sticky_bonus: float
    within_hold: bool
    reason: str | None


def apply_hysteresis(
    final_score: float,
    previous: NpcRoutingState | None,
    timestamp_ms: int,
    config: RouterConfig,
) -> HysteresisAdjustment:
    """Return the deterministic score adjustment for the previous tier's hold."""
    if previous is None:
        return HysteresisAdjustment(
            effective_score=final_score,
            sticky_bonus=0.0,
            within_hold=False,
            reason=None,
        )

    elapsed_ms = max(0, timestamp_ms - previous.tier_entered_at_ms)

    if (
        previous.tier is AttentionTier.FOCUSED
        and elapsed_ms < config.focused_hold_ms
    ):
        sticky_bonus = config.focused_sticky_bonus
        reason = "focused hysteresis hold"
    elif (
        previous.tier is AttentionTier.REACTIVE
        and elapsed_ms < config.reactive_hold_ms
    ):
        sticky_bonus = config.reactive_sticky_bonus
        reason = "reactive hysteresis hold"
    else:
        sticky_bonus = 0.0
        reason = None

    within_hold = reason is not None
    return HysteresisAdjustment(
        effective_score=final_score + sticky_bonus,
        sticky_bonus=sticky_bonus,
        within_hold=within_hold,
        reason=reason,
    )
