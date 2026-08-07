"""Owner: Jerome & Richard

Reactive spends no characters on headings, on either trigger path. The reaction case is here
because a heading map creeping in on one path is exactly how the Focused defect happened.
"""

from __future__ import annotations

from backend.models.prompts.reactive_prompt import render_reactive_prompt
from backend.models.prompts.tests.contexts import (
    OBSERVED_EVENT_TEXT,
    PLAYER_SPEECH_TEXT,
    conversation_context,
    reaction_context,
)
from backend.orchestration.router_port import AttentionTier

REACTIVE = AttentionTier.REACTIVE


def test_the_reactive_conversation_prompt_is_the_section_bodies_alone() -> None:
    prompt = render_reactive_prompt(conversation_context(REACTIVE))

    assert prompt == "\n".join(
        [
            "body of output_contract",
            PLAYER_SPEECH_TEXT,
            "body of profile",
            "body of event",
            "body of world",
            "body of history",
        ]
    )


def test_the_reactive_reaction_prompt_is_the_section_bodies_alone() -> None:
    prompt = render_reactive_prompt(reaction_context(REACTIVE))

    assert prompt == "\n".join(
        [
            "body of output_contract",
            OBSERVED_EVENT_TEXT,
            "body of profile",
            "body of world",
        ]
    )
