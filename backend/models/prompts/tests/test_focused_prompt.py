"""Owner: Jerome & Richard

Focused prompts are asserted whole rather than by substring. A renderer that returns the empty
string, drops a heading, or reorders sections is then a failure here rather than a wording
change nothing in the repository can see.
"""

from __future__ import annotations

from backend.models.prompts.focused_prompt import (
    PLAYER_HEADING,
    SITUATION_HEADING,
    render_focused_conversation_prompt,
    render_focused_reaction_prompt,
)
from backend.models.prompts.tests.contexts import (
    OBSERVED_EVENT_TEXT,
    PLAYER_SPEECH_TEXT,
    conversation_context,
    reaction_context,
)
from backend.orchestration.router_port import AttentionTier

FOCUSED = AttentionTier.FOCUSED


def test_the_conversation_prompt_heads_every_section_in_the_order_context_fixed() -> None:
    prompt = render_focused_conversation_prompt(conversation_context(FOCUSED))

    assert prompt == "\n\n".join(
        [
            "INSTRUCTIONS\nbody of output_contract",
            f"THE PLAYER\n{PLAYER_SPEECH_TEXT}",
            "WHO YOU ARE\nbody of profile",
            "WHAT IS HAPPENING\nbody of event",
            "AROUND YOU\nbody of world",
            "RECENT CONVERSATION\nbody of history",
        ]
    )


def test_the_reaction_prompt_heads_every_section_in_the_order_context_fixed() -> None:
    prompt = render_focused_reaction_prompt(reaction_context(FOCUSED))

    assert prompt == "\n\n".join(
        [
            "INSTRUCTIONS\nbody of output_contract",
            f"WHAT IS HAPPENING\n{OBSERVED_EVENT_TEXT}",
            "WHO YOU ARE\nbody of profile",
            "AROUND YOU\nbody of world",
        ]
    )


def test_the_reaction_prompt_never_attributes_the_event_to_the_player() -> None:
    prompt = render_focused_reaction_prompt(reaction_context(FOCUSED))

    assert PLAYER_HEADING not in prompt
    assert f"{SITUATION_HEADING}\n{OBSERVED_EVENT_TEXT}" in prompt


def test_the_conversation_prompt_keeps_attributing_the_trigger_to_the_player() -> None:
    prompt = render_focused_conversation_prompt(conversation_context(FOCUSED))

    assert f"{PLAYER_HEADING}\n{PLAYER_SPEECH_TEXT}" in prompt


def test_the_two_focused_renderers_head_the_same_trigger_slot_differently() -> None:
    """The defect this ticket fixes was one heading map serving both paths."""
    conversation = render_focused_conversation_prompt(conversation_context(FOCUSED))
    reaction = render_focused_reaction_prompt(reaction_context(FOCUSED))

    assert PLAYER_HEADING != SITUATION_HEADING
    assert conversation.split("\n\n")[1].startswith(f"{PLAYER_HEADING}\n")
    assert reaction.split("\n\n")[1].startswith(f"{SITUATION_HEADING}\n")
