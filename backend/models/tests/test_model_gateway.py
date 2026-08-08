"""Owner: Jerome & Richard"""

from __future__ import annotations

import pytest

from backend.context.trigger_kind import TriggerKind
from backend.models.mock_provider import MODEL_FOR_TIER, PROVIDER, MockProvider
from backend.models.model_gateway import (
    EmptyGeneration,
    GeneratedBehaviour,
    GenerationRequest,
    ModelGateway,
    ProviderIdentity,
)
from backend.orchestration.behaviour_command import (
    MAX_DIALOGUE_CHARACTERS,
    consumer_length,
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
        "trigger_kind": TriggerKind.PLAYER_SPEECH,
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
    def identity(self, tier: AttentionTier) -> ProviderIdentity:
        return ProviderIdentity(provider="silent", model="silent")

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

    dialogue = behaviour.dialogue
    assert dialogue is not None and dialogue == dialogue.strip()
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


class FixedProvider:
    """A provider that answers with whatever a case needs to see bounded."""

    def __init__(self, dialogue: str) -> None:
        self._dialogue = dialogue

    def identity(self, tier: AttentionTier) -> ProviderIdentity:
        return ProviderIdentity(provider="fixed", model="fixed")

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        return GeneratedBehaviour(
            dialogue=self._dialogue,
            action=None,
            provider="fixed",
            model="fixed",
            input_tokens=1,
            output_tokens=1,
            fallback_used=False,
        )


async def test_a_provider_answer_past_the_dialogue_limit_is_bounded_at_this_seam() -> None:
    """Every provider result converges on the gateway, so the consumer's bound belongs here.

    An output-token budget does not cover this: 120 tokens is estimated as 480 characters at four
    per token, but a real provider's 120 tokens routinely run past 512, and Minecraft drops the
    whole command rather than the overflow.
    """
    gateway = ModelGateway(
        focused=FixedProvider("t" * (MAX_DIALOGUE_CHARACTERS + 200)),
        reactive=FixedProvider("unused"),
        deadlines=AsyncioDeadlines(),
        timeouts_ms=GENEROUS_TIMEOUTS_MS,
    )

    behaviour = await gateway.generate(request_for(AttentionTier.FOCUSED))

    assert behaviour.dialogue is not None
    assert consumer_length(behaviour.dialogue) == MAX_DIALOGUE_CHARACTERS


async def test_a_provider_answer_inside_the_limit_is_left_exactly_as_it_came() -> None:
    """The bound must not be a truncation everything quietly passes through."""
    answer = "t" * MAX_DIALOGUE_CHARACTERS
    gateway = ModelGateway(
        focused=FixedProvider(answer),
        reactive=FixedProvider("unused"),
        deadlines=AsyncioDeadlines(),
        timeouts_ms=GENEROUS_TIMEOUTS_MS,
    )

    behaviour = await gateway.generate(request_for(AttentionTier.FOCUSED))

    assert behaviour.dialogue == answer
