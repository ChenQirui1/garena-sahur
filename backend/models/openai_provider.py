"""The OpenAI client both configured tier adapters call through.

Owner: Jerome & Richard

This is one adapter rather than two because the tiers differ only in which model answers and how
much they may spend, both of which arrive on the request or on the settings. What is genuinely
per-tier — which configured model each reaches — lives in `focused_provider` and
`reactive_provider`, the modules the tracked ownership tree names for exactly that.

Nothing here retries, and nothing here falls back. The SDK's own retry loop is switched off, the
gateway owns the deadline, and orchestration decides what a spent attempt is worth; a second
request from any of those three would spend a durable generation claim twice.

The result is the same `GeneratedBehaviour` mock mode returns, so everything downstream —
normalisation, the dialogue bound, telemetry, fallback — cannot tell the two modes apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import APIError, APITimeoutError, AsyncOpenAI
from openai.types.responses import Response
from openai.types.shared_params.reasoning import Reasoning

from backend.models.model_gateway import (
    GeneratedBehaviour,
    GenerationRequest,
    ProviderIdentity,
    ProviderTimeout,
)
from backend.models.token_estimate import estimate_tokens
from backend.orchestration.router_port import AttentionTier

PROVIDER = "openai"

# Specification #1: both tiers run "with reasoning disabled". `none` is the SDK's own word for it
# (`openai.types.shared.reasoning_effort.ReasoningEffort`), so this is the disabled setting rather
# than the cheapest one. It is a constant because specification #1 excludes reasoning-effort
# experiments from this sprint; a knob here would be the first one.
REASONING_DISABLED: Reasoning = {"effort": "none"}

# Automatic retries are off at the SDK layer, which is the only layer that would otherwise retry
# without orchestration seeing it: a durable generation claim is spent by the first request, and a
# transparent second one would bill and publish work the backend believes it never attempted.
SDK_RETRIES_DISABLED = 0


class ProviderError(RuntimeError):
    """The provider refused the call or reported a failed response, so the attempt is spent."""


def openai_client(api_key: str) -> AsyncOpenAI:
    """The one place the SDK client is constructed, so retries cannot be re-enabled by accident."""
    return AsyncOpenAI(api_key=api_key, max_retries=SDK_RETRIES_DISABLED)


@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    """One configured OpenAI model, reached at most once per request."""

    model: str
    client: AsyncOpenAI
    characters_per_token: int

    def identity(self, tier: AttentionTier) -> ProviderIdentity:
        """Who the call is waiting on, answerable before the answer arrives.

        The configured identifier rather than a response's, because a timeout has to name the
        model it was waiting on and no response exists to ask.
        """
        return ProviderIdentity(provider=PROVIDER, model=self.model)

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        try:
            response = await self.client.responses.create(
                model=self.model,
                input=request.prompt,
                max_output_tokens=request.output_token_limit,
                reasoning=REASONING_DISABLED,
            )
        except APITimeoutError as expired:
            # Translated rather than passed through so that a slow provider carries the same
            # `MODEL_TIMEOUT` code whether the SDK gave up or the gateway's budget did.
            raise ProviderTimeout(f"{self.model} did not answer in time") from expired
        except APIError as refused:
            raise ProviderError(f"{self.model} refused the call: {refused}") from refused
        return self._behaviour(response, request)

    def _behaviour(self, response: Response, request: GenerationRequest) -> GeneratedBehaviour:
        """The provider's answer as the result mock mode would have returned.

        An empty answer is left empty rather than raised on: the gateway already refuses a result
        carrying neither dialogue nor an action, and refusing it here as well would give the same
        malformed response two different error codes depending on which layer noticed first.

        No action is emitted. Ivan owns the executable action vocabulary under coordination issue
        #4, and a model is perfectly capable of inventing a verb Minecraft cannot apply.
        """
        if response.error is not None:
            raise ProviderError(f"{self.model} reported {response.error.code}")

        dialogue = (response.output_text or "").strip()
        usage = response.usage
        return GeneratedBehaviour(
            dialogue=dialogue or None,
            action=None,
            provider=PROVIDER,
            # What actually answered, which a configured alias does not always name — Elson &
            # Daniel's per-model costs are only as exact as this field.
            model=response.model or self.model,
            # Reported usage is the real cost when the provider states it; the character estimate
            # (ADR 0006) stands in when it does not, so a fact always carries a token count.
            input_tokens=(
                usage.input_tokens if usage is not None else request.estimated_input_tokens
            ),
            output_tokens=(
                usage.output_tokens
                if usage is not None
                else estimate_tokens(dialogue, self.characters_per_token)
            ),
            fallback_used=False,
        )
