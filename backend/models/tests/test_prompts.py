"""Owner: Jerome & Richard

The renderers head sections by name, so a context section with no heading reaches no model at
all. These cases exist to make that failure land here rather than in a component trace.
"""

from __future__ import annotations

import pytest

from backend.context.context_builder import (
    EVENT,
    HISTORY,
    OUTPUT_CONTRACT,
    PROFILE,
    TRIGGER,
    WORLD,
    ContextSection,
    GenerationContext,
)
from backend.context.npc_profiles import NpcProfiles
from backend.models.prompts.focused_prompt import render_focused_prompt
from backend.models.prompts.reactive_prompt import render_reactive_prompt
from backend.orchestration.router_port import AttentionTier

EVERY_SECTION = (OUTPUT_CONTRACT, TRIGGER, PROFILE, EVENT, WORLD, HISTORY)


def _context(tier: AttentionTier) -> GenerationContext:
    return GenerationContext(
        tier=tier,
        npc=NpcProfiles.empty().profile_for("shopkeeper-uuid"),
        trigger_text="Which direction did the thief run?",
        sections=tuple(ContextSection(name, f"body of {name}") for name in EVERY_SECTION),
        estimated_input_tokens=42,
        output_token_limit=120,
    )


@pytest.mark.parametrize("name", EVERY_SECTION)
def test_the_focused_prompt_heads_every_section_a_context_can_carry(name: str) -> None:
    prompt = render_focused_prompt(_context(AttentionTier.FOCUSED))

    assert f"body of {name}" in prompt


def test_the_focused_prompt_signposts_the_event_between_persona_and_surroundings() -> None:
    prompt = render_focused_prompt(_context(AttentionTier.FOCUSED))

    assert prompt.index("WHO YOU ARE") < prompt.index("WHAT IS HAPPENING")
    assert prompt.index("WHAT IS HAPPENING") < prompt.index("AROUND YOU")


def test_the_reactive_prompt_spends_no_characters_on_headings() -> None:
    prompt = render_reactive_prompt(_context(AttentionTier.REACTIVE))

    assert prompt == "\n".join(f"body of {name}" for name in EVERY_SECTION)
