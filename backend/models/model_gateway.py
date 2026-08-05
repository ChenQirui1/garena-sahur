"""Route Focused and Reactive requests to the configured providers.

Owner: Jerome & Richard

Generation policy decides whether to generate; this decides who generates and normalises what
comes back. Ambient never arrives here — it is local Minecraft behaviour — so a request that
does is a policy defect rather than something to serve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.orchestration.router_port import AttentionTier


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    request_id: str
    session_id: str
    npc_id: str
    npc_name: str
    tier: AttentionTier
    conversation_id: str | None
    turn_id: str | None
    event_id: str | None
    source_sequence: int
    prompt: str
    trigger_text: str
    estimated_input_tokens: int
    output_token_limit: int


@dataclass(frozen=True, slots=True)
class GeneratedBehaviour:
    dialogue: str | None
    action: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    fallback_used: bool


class EmptyGeneration(ValueError):
    """A provider returned neither dialogue nor an action, so there is nothing to publish."""


class NoProviderForTier(ValueError):
    """Generation was asked for a tier that has no provider, so no call was attempted."""


class Provider(Protocol):
    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour: ...


class ModelGateway:
    def __init__(self, focused: Provider, reactive: Provider) -> None:
        self._providers = {AttentionTier.FOCUSED: focused, AttentionTier.REACTIVE: reactive}

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        provider = self._providers.get(request.tier)
        if provider is None:
            raise NoProviderForTier(f"{request.tier.value} behaviour does not use a provider")
        return _normalized(await provider.generate(request))


def _normalized(behaviour: GeneratedBehaviour) -> GeneratedBehaviour:
    dialogue = (behaviour.dialogue or "").strip() or None
    action = (behaviour.action or "").strip() or None
    if dialogue is None and action is None:
        raise EmptyGeneration("a command must carry dialogue or an action")
    return GeneratedBehaviour(
        dialogue=dialogue,
        action=action,
        provider=behaviour.provider,
        model=behaviour.model,
        input_tokens=behaviour.input_tokens,
        output_tokens=behaviour.output_tokens,
        fallback_used=behaviour.fallback_used,
    )
