"""Prompt template for Focused-tier generation.

Owner: Jerome & Richard
"""

from __future__ import annotations

from backend.context.context_builder import GenerationContext

HEADINGS = {
    "output_contract": "INSTRUCTIONS",
    "trigger": "THE PLAYER",
    "profile": "WHO YOU ARE",
    "event": "WHAT IS HAPPENING",
    "world": "AROUND YOU",
    "history": "RECENT CONVERSATION",
}


def render_focused_prompt(context: GenerationContext) -> str:
    """Headed sections, because Focused context is rich enough to need signposting."""
    return "\n\n".join(
        f"{HEADINGS[section.name]}\n{section.body}" for section in context.sections
    )
