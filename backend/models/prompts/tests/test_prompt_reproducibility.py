"""Owner: Jerome & Richard

Specification #1 user story 23 promises that the same accepted facts produce reproducible
prompts in mock mode. Until this module existed, nothing in the repository observed the string
that promise is about.
"""

from __future__ import annotations

from typing import Callable

import pytest

from backend.context.context_builder import GenerationContext
from backend.models.prompts.renderer_selection import renderer_for
from backend.models.prompts.tests.contexts import conversation_context, reaction_context
from backend.orchestration.router_port import AttentionTier

EVERY_PATH = [
    conversation_context(AttentionTier.FOCUSED),
    reaction_context(AttentionTier.FOCUSED),
    conversation_context(AttentionTier.REACTIVE),
    reaction_context(AttentionTier.REACTIVE),
]
PATH_IDS = [
    "focused player speech",
    "focused observed event",
    "reactive player speech",
    "reactive observed event",
]


@pytest.mark.parametrize("context", EVERY_PATH, ids=PATH_IDS)
def test_rendering_one_context_twice_is_byte_identical(context: GenerationContext) -> None:
    render = renderer_for(context)

    assert render(context) == render(context)


@pytest.mark.parametrize(
    "build",
    [conversation_context, reaction_context],
    ids=["player speech", "observed event"],
)
@pytest.mark.parametrize("tier", [AttentionTier.FOCUSED, AttentionTier.REACTIVE])
def test_two_independently_built_equal_contexts_render_identically(
    build: Callable[[AttentionTier], GenerationContext], tier: AttentionTier
) -> None:
    first = build(tier)
    second = build(tier)

    assert first is not second
    assert renderer_for(first)(first) == renderer_for(second)(second)


def test_a_prompt_is_not_reproducible_by_being_empty() -> None:
    """Byte-identity alone is satisfied by returning nothing, so pin that it is not nothing."""
    for context in EVERY_PATH:
        assert renderer_for(context)(context)
