"""Deterministic mock responses for development and backup.

Owner: Jerome & Richard

The same request always produces the same reply, so component tests, benchmarks, and demo
rehearsals do not depend on an external provider. No action is emitted: Ivan owns the
executable action vocabulary under coordination issue #4, and inventing one here would
manufacture a command Minecraft cannot apply.
"""

from __future__ import annotations

from backend.models.model_gateway import GeneratedBehaviour, GenerationRequest
from backend.models.token_estimate import characters_for, estimate_tokens
from backend.orchestration.router_port import AttentionTier

PROVIDER = "mock"
MODEL_FOR_TIER = {
    AttentionTier.FOCUSED: "mock-focused",
    AttentionTier.REACTIVE: "mock-reactive",
}


class MockProvider:
    def __init__(self, characters_per_token: int) -> None:
        self._characters_per_token = characters_per_token

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        dialogue = _clipped(_dialogue(request), request, self._characters_per_token)
        return GeneratedBehaviour(
            dialogue=dialogue,
            action=None,
            provider=PROVIDER,
            model=MODEL_FOR_TIER[request.tier],
            input_tokens=request.estimated_input_tokens,
            output_tokens=estimate_tokens(dialogue, self._characters_per_token),
            fallback_used=False,
        )


def _dialogue(request: GenerationRequest) -> str:
    if request.tier is AttentionTier.FOCUSED:
        return (
            f'{request.npc_name} leans in. "You asked: {request.trigger_text} '
            'Let me tell you what I saw."'
        )
    return f'{request.npc_name} calls out. "{request.trigger_text}"'


def _clipped(dialogue: str, request: GenerationRequest, characters_per_token: int) -> str:
    budget = characters_for(request.output_token_limit, characters_per_token)
    return dialogue if len(dialogue) <= budget else dialogue[:budget].rstrip()
