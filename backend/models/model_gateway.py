"""Route Focused and Reactive requests to the configured providers, inside a time budget.

Owner: Jerome & Richard

Generation policy decides whether to generate; this decides who generates and normalises what
comes back. Ambient never arrives here — it is local Minecraft behaviour — so a request that
does is a policy defect rather than something to serve.

The per-tier deadline is applied here rather than around the caller because this is the only
place that knows which provider a request reached. Nothing here retries or falls back: an
expired or failed attempt is raised, and orchestration decides what a spent attempt is worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from backend.orchestration.clock import DeadlineExceeded, Deadlines
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

    # What the request is about, carried for the fallback library rather than for the provider:
    # cached content is chosen by who is speaking and why, which the prompt alone cannot say.
    trigger: str = ""
    event_type: str | None = None
    roles: tuple[str, ...] = ()


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


class ProviderTimeout(DeadlineExceeded):
    """A provider did not answer inside its tier's budget, and the attempt is spent."""


# The team's handoff contract §21.10 gives the error-code vocabulary in upper snake case, and
# §23's worked timeout example names `MODEL_TIMEOUT`. `docs/message_schemas.md` §7 requires a
# "stable machine-readable" code but lists none, so these are the team's words, not ours.
ERROR_CODE_FOR: dict[type[BaseException], str] = {
    ProviderTimeout: "MODEL_TIMEOUT",
    EmptyGeneration: "INVALID_MODEL_RESPONSE",
}
DEFAULT_ERROR_CODE = "PROVIDER_ERROR"


def error_code_for(failure: BaseException) -> str:
    for kind, code in ERROR_CODE_FOR.items():
        if isinstance(failure, kind):
            return code
    return DEFAULT_ERROR_CODE


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Who would answer a request, knowable without waiting for the answer.

    A timeout still has to name the provider and model it was waiting on — the handoff
    contract's §23 failure example populates both — so identity cannot live only on a result.
    """

    provider: str
    model: str


class Provider(Protocol):
    def identity(self, tier: AttentionTier) -> ProviderIdentity: ...

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour: ...


class ModelGateway:
    def __init__(
        self,
        focused: Provider,
        reactive: Provider,
        deadlines: Deadlines,
        timeouts_ms: Mapping[AttentionTier, int],
    ) -> None:
        self._providers = {AttentionTier.FOCUSED: focused, AttentionTier.REACTIVE: reactive}
        self._deadlines = deadlines
        self._timeouts_ms = timeouts_ms

    def identity_for(self, tier: AttentionTier) -> ProviderIdentity | None:
        """Who a request for this tier reaches, or `None` when nothing would be called."""
        provider = self._providers.get(tier)
        return None if provider is None else provider.identity(tier)

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        provider = self._providers.get(request.tier)
        if provider is None:
            raise NoProviderForTier(f"{request.tier.value} behaviour does not use a provider")

        budget_ms = self._timeouts_ms[request.tier]
        try:
            async with self._deadlines.limit(budget_ms):
                behaviour = await provider.generate(request)
        except DeadlineExceeded as expired:
            raise ProviderTimeout(
                f"{request.tier.value} provider exceeded its {budget_ms}ms budget"
            ) from expired
        return _normalized(behaviour)


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
