"""Strong-model client.

Owner: Jerome & Richard

Specification #1: "Focused uses the configured OpenAI `gpt-5.6-terra` adapter with reasoning
disabled." Which setting names that model is the whole of what is Focused-specific, so it is the
whole of what lives here — the request itself already carries the tier's output budget, and the
gateway already owns its four-second deadline.

It is a module of its own, beside `reactive_provider`, because the tracked ownership tree names
both: changing which model answers Focused is then a change to the file the team's own document
points at, rather than a line buried in service wiring.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from backend.config import Settings
from backend.models.openai_provider import OpenAIProvider


def focused_provider(settings: Settings, client: AsyncOpenAI) -> OpenAIProvider:
    return OpenAIProvider(
        model=settings.focused_model,
        client=client,
        characters_per_token=settings.characters_per_token,
    )
