"""Cheaper/smaller-model client.

Owner: Jerome & Richard

The counterpart of `focused_provider`, and deliberately its mirror image: the tiers differ in
which model they name and in nothing else, so any difference appearing between these two files is
a difference the sources do not ask for.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from backend.config import Settings
from backend.models.openai_provider import OpenAIProvider


def reactive_provider(settings: Settings, client: AsyncOpenAI) -> OpenAIProvider:
    return OpenAIProvider(
        model=settings.reactive_model,
        client=client,
        characters_per_token=settings.characters_per_token,
    )
