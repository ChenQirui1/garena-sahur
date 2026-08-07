"""Configuration for deterministic scoring, tier capacities and hysteresis.

Owner: Elson & Daniel

Graph propagation settings are intentionally deferred until that module is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """Starting weights and hard limits agreed for the Spotlight prototype."""

    focused_capacity: int = 2
    reactive_capacity: int = 6
    max_relevant_distance_blocks: float = 28.0

    viewport_weight: float = 0.40
    proximity_weight: float = 0.20
    event_relevance_weight: float = 0.30
    interaction_recency_weight: float = 0.10
    active_conversation_bonus: float = 10.0

    # Capacity is a maximum rather than a target. A candidate at or below this score remains
    # Ambient unless it is the active conversation target.
    minimum_tier_score: float = 0.0

    focused_hold_ms: int = 1800
    reactive_hold_ms: int = 2500
    focused_sticky_bonus: float = 0.18
    reactive_sticky_bonus: float = 0.10

    def __post_init__(self) -> None:
        if self.focused_capacity < 0:
            raise ValueError("focused_capacity must be non-negative")
        if self.reactive_capacity < 0:
            raise ValueError("reactive_capacity must be non-negative")
        if self.max_relevant_distance_blocks <= 0:
            raise ValueError("max_relevant_distance_blocks must be positive")
        if self.focused_hold_ms < 0:
            raise ValueError("focused_hold_ms must be non-negative")
        if self.reactive_hold_ms < 0:
            raise ValueError("reactive_hold_ms must be non-negative")

        non_negative = {
            "viewport_weight": self.viewport_weight,
            "proximity_weight": self.proximity_weight,
            "event_relevance_weight": self.event_relevance_weight,
            "interaction_recency_weight": self.interaction_recency_weight,
            "active_conversation_bonus": self.active_conversation_bonus,
            "minimum_tier_score": self.minimum_tier_score,
            "focused_sticky_bonus": self.focused_sticky_bonus,
            "reactive_sticky_bonus": self.reactive_sticky_bonus,
        }
        for name, value in non_negative.items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
