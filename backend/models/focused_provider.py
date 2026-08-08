"""Strong-model client.

Owner: Jerome & Richard

Which setting names Focused's model is all that is Focused-specific: the request already carries
the tier's output budget, and the gateway already owns its four-second deadline. It is a module of
its own because the tracked ownership tree names one, so changing the strong model is a change to
the file the team's own document points at.
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
        reasoning_effort=settings.reasoning_effort,
    )
