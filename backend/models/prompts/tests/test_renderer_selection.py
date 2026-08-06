"""Owner: Jerome & Richard

Selection is asserted over every tier and trigger kind that can reach a provider, so a mapping
that ignores one of the two axes cannot pass.
"""

from __future__ import annotations

import pytest

from backend.context.context_builder import GenerationContext, TriggerKind
from backend.models.prompts.focused_prompt import (
    render_focused_conversation_prompt,
    render_focused_reaction_prompt,
)
from backend.models.prompts.reactive_prompt import render_reactive_prompt
from backend.models.prompts.renderer_selection import PromptRenderer, renderer_for
from backend.models.prompts.tests.contexts import conversation_context, reaction_context
from backend.orchestration.router_port import AttentionTier

FOCUSED = AttentionTier.FOCUSED
REACTIVE = AttentionTier.REACTIVE


@pytest.mark.parametrize(
    "context, expected",
    [
        (conversation_context(FOCUSED), render_focused_conversation_prompt),
        (reaction_context(FOCUSED), render_focused_reaction_prompt),
        (conversation_context(REACTIVE), render_reactive_prompt),
        (reaction_context(REACTIVE), render_reactive_prompt),
    ],
    ids=[
        "focused player speech",
        "focused observed event",
        "reactive player speech",
        "reactive observed event",
    ],
)
def test_the_renderer_is_chosen_by_tier_and_trigger_kind_together(
    context: GenerationContext, expected: PromptRenderer
) -> None:
    assert renderer_for(context) is expected


def test_two_focused_contexts_differing_only_in_trigger_kind_reach_different_renderers() -> None:
    """Tier alone cannot be the key: these two agree on it and must not agree on the renderer."""
    speech = conversation_context(FOCUSED)
    event = reaction_context(FOCUSED)

    assert speech.tier is event.tier
    assert speech.trigger_kind is not event.trigger_kind
    assert renderer_for(speech) is not renderer_for(event)


def test_every_trigger_kind_a_provider_bound_tier_can_carry_is_selectable() -> None:
    """A kind added later without a renderer should fail here, not in front of a player."""
    build = {
        TriggerKind.PLAYER_SPEECH: conversation_context,
        TriggerKind.OBSERVED_EVENT: reaction_context,
    }

    assert set(build) == set(TriggerKind)
    for tier in (FOCUSED, REACTIVE):
        for kind in TriggerKind:
            assert callable(renderer_for(build[kind](tier)))
