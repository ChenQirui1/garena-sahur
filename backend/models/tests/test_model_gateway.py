"""Owner: Jerome & Richard"""

from __future__ import annotations

import pytest

from backend.models.mock_provider import MODEL_FOR_TIER, PROVIDER, MockProvider
from backend.models.model_gateway import (
    EmptyGeneration,
    GeneratedBehaviour,
    GenerationRequest,
    ModelGateway,
)
from backend.orchestration.clock import AsyncioDeadlines
from backend.orchestration.router_port import AttentionTier

CHARACTERS_PER_TOKEN = 4

# Long enough that a deterministic mock never approaches them; the tier budgets themselves are
# asserted through the running service, where a manual deadline can actually expire.
GENEROUS_TIMEOUTS_MS = {AttentionTier.FOCUSED: 4_000, AttentionTier.REACTIVE: 2_000}


def request_for(tier: AttentionTier, **overrides: object) -> GenerationRequest:
    fields: dict[str, object] = {
        "request_id": "request-abc",
        "session_id": "demo-01",
        "npc_id": "shopkeeper-uuid",
        "npc_name": "Mira",
        "tier": tier,
        "conversation_id": "conversation-07",
        "turn_id": "turn-004",
        "event_id": None,
        "source_sequence": 1842,
        "prompt": "INSTRUCTIONS\nStay in character.",
        "trigger_text": "Which direction did the thief run?",
        "estimated_input_tokens": 42,
        "output_token_limit": 120,
    }
    return GenerationRequest(**(fields | overrides))  # type: ignore[arg-type]


class SilentProvider:
    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        return GeneratedBehaviour(
            dialogue="   ",
            action=None,
            provider="silent",
            model="silent",
            input_tokens=1,
            output_tokens=0,
            fallback_used=False,
        )


def gateway() -> ModelGateway:
    provider = MockProvider(CHARACTERS_PER_TOKEN)
    return ModelGateway(
        focused=provider,
        reactive=provider,
        deadlines=AsyncioDeadlines(),
        timeouts_ms=GENEROUS_TIMEOUTS_MS,
    )


@pytest.mark.parametrize("tier", [AttentionTier.FOCUSED, AttentionTier.REACTIVE])
async def test_the_mock_provider_is_deterministic_for_both_tiers(
    tier: AttentionTier,
) -> None:
    request = request_for(tier)

    first = await gateway().generate(request)
    second = await gateway().generate(request)

    assert first == second
    assert first.provider == PROVIDER
    assert first.model == MODEL_FOR_TIER[tier]
    assert first.dialogue
    assert first.fallback_used is False


async def test_the_mock_result_is_normalised_and_costed() -> None:
    behaviour = await gateway().generate(request_for(AttentionTier.FOCUSED))

    assert behaviour.dialogue == behaviour.dialogue.strip()
    assert behaviour.input_tokens == 42
    assert behaviour.output_tokens > 0


async def test_mock_dialogue_stays_inside_the_output_budget() -> None:
    behaviour = await gateway().generate(
        request_for(AttentionTier.REACTIVE, output_token_limit=5, trigger_text="w" * 500)
    )

    assert behaviour.dialogue is not None
    assert len(behaviour.dialogue) <= 5 * CHARACTERS_PER_TOKEN


async def test_the_mock_provider_never_invents_an_action() -> None:
    """Ivan owns the executable action vocabulary under #4."""
    behaviour = await gateway().generate(request_for(AttentionTier.FOCUSED))

    assert behaviour.action is None


async def test_an_ambient_assignment_has_no_provider() -> None:
    with pytest.raises(ValueError, match="does not use a provider"):
        await gateway().generate(request_for(AttentionTier.AMBIENT))


async def test_a_result_with_neither_dialogue_nor_action_is_refused() -> None:
    silent = SilentProvider()

    with pytest.raises(EmptyGeneration):
        await ModelGateway(
            focused=silent,
            reactive=silent,
            deadlines=AsyncioDeadlines(),
            timeouts_ms=GENEROUS_TIMEOUTS_MS,
        ).generate(
            request_for(AttentionTier.FOCUSED)
        )
