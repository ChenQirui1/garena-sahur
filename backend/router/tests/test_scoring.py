"""Router tests: viewport, proximity, event and interaction scoring.

Owner: Elson & Daniel
"""

from __future__ import annotations

import pytest

from backend.router.config import RouterConfig
from backend.router.models import RoutingNpc
from backend.router.scoring import score_candidate, viewport_score


def candidate(**overrides: object) -> RoutingNpc:
    fields: dict[str, object] = {
        "npc_id": "shopkeeper-uuid",
        "world_distance_blocks": 14.0,
        "viewport_center_distance": 0.25,
        "inside_viewport": True,
        "line_of_sight": True,
        "event_relevance": 1.0,
        "event_roles": ["target"],
        "interaction_recency": 0.8,
    }
    return RoutingNpc(**(fields | overrides))  # type: ignore[arg-type]


def test_direct_score_matches_the_documented_weighted_formula() -> None:
    scored = score_candidate(candidate(), None, RouterConfig()).score

    assert scored.viewport == pytest.approx(0.75)
    assert scored.proximity == pytest.approx(0.50)
    assert scored.direct_score == pytest.approx(0.78)
    assert "direct event target" in scored.reasons


def test_viewport_signal_requires_both_viewport_membership_and_line_of_sight() -> None:
    assert viewport_score(candidate(inside_viewport=False)) == 0.0
    assert viewport_score(candidate(line_of_sight=False)) == 0.0


def test_active_conversation_adds_the_configured_priority_bonus() -> None:
    config = RouterConfig()
    npc = candidate(event_relevance=0.0, interaction_recency=0.0)
    ordinary = score_candidate(npc, None, config).score.direct_score
    active = score_candidate(npc, npc.npc_id, config).score

    assert active.direct_score == pytest.approx(
        ordinary + config.active_conversation_bonus
    )
    assert active.reasons[0] == "active conversation"
