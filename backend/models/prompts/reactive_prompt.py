"""Prompt template for Reactive-tier generation.

Owner: Jerome & Richard
"""

from __future__ import annotations

from backend.context.context_builder import GenerationContext


def render_reactive_prompt(context: GenerationContext) -> str:
    """No headings: Reactive context is deliberately small and every character is budget."""
    return "\n".join(section.body for section in context.sections)
