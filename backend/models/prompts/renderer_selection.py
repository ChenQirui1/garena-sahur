"""Which renderer a context reaches.

Owner: Jerome & Richard

Tier alone does not decide it. A Focused reaction and a Focused answer carry the same sections
in the same order and differ only in what the trigger section is, so a tier-keyed choice heads
an observed event as player speech. Selection lives here rather than in orchestration so that
the coordinator carries no heading and no prompt text.
"""

from __future__ import annotations

from typing import Callable

from backend.context.context_builder import GenerationContext
from backend.context.trigger_kind import TriggerKind
from backend.models.prompts.focused_prompt import (
    render_focused_conversation_prompt,
    render_focused_reaction_prompt,
)
from backend.models.prompts.reactive_prompt import render_reactive_prompt
from backend.orchestration.router_port import AttentionTier

PromptRenderer = Callable[[GenerationContext], str]

# Ambient is absent deliberately: it never reaches a provider, so it has no prompt to render.
RENDERER_FOR: dict[tuple[AttentionTier, TriggerKind], PromptRenderer] = {
    (AttentionTier.FOCUSED, TriggerKind.PLAYER_SPEECH): render_focused_conversation_prompt,
    (AttentionTier.FOCUSED, TriggerKind.OBSERVED_EVENT): render_focused_reaction_prompt,
    (AttentionTier.REACTIVE, TriggerKind.PLAYER_SPEECH): render_reactive_prompt,
    (AttentionTier.REACTIVE, TriggerKind.OBSERVED_EVENT): render_reactive_prompt,
}


def renderer_for(context: GenerationContext) -> PromptRenderer:
    return RENDERER_FOR[(context.tier, context.trigger_kind)]
