"""Prompt templates for Focused-tier generation.

Owner: Jerome & Richard

There are two, because the active-trigger section holds two different things. On the
conversation path it is what the player said; on the reaction path it is what the NPC saw, and
heading that as the player tells the strongest model a line nobody spoke. Everything outside
that one heading is shared between them.
"""

from __future__ import annotations

from backend.context.context_builder import (
    EVENT,
    HISTORY,
    OUTPUT_CONTRACT,
    PROFILE,
    TRIGGER,
    WORLD,
    GenerationContext,
)

PLAYER_HEADING = "THE PLAYER"
SITUATION_HEADING = "WHAT IS HAPPENING"

SHARED_HEADINGS = {
    OUTPUT_CONTRACT: "INSTRUCTIONS",
    PROFILE: "WHO YOU ARE",
    EVENT: SITUATION_HEADING,
    WORLD: "AROUND YOU",
    HISTORY: "RECENT CONVERSATION",
}

# The reaction path carries no separate relevant-event section — the event is the trigger — so
# the two never compete for the situation heading.
CONVERSATION_HEADINGS = {**SHARED_HEADINGS, TRIGGER: PLAYER_HEADING}
REACTION_HEADINGS = {**SHARED_HEADINGS, TRIGGER: SITUATION_HEADING}


def render_focused_conversation_prompt(context: GenerationContext) -> str:
    """The trigger is what the player said, so it is signposted as the player."""
    return _render_with_headings(context, CONVERSATION_HEADINGS)


def render_focused_reaction_prompt(context: GenerationContext) -> str:
    """The trigger is what the NPC observed, so it is signposted as the situation."""
    return _render_with_headings(context, REACTION_HEADINGS)


def _render_with_headings(context: GenerationContext, headings: dict[str, str]) -> str:
    """Headed sections, because Focused context is rich enough to need signposting."""
    return "\n\n".join(
        f"{headings[section.name]}\n{section.body}" for section in context.sections
    )
