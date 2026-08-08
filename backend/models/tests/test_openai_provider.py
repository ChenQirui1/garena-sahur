"""Owner: Jerome & Richard

Every case here drives the real SDK client over a stubbed HTTP transport, so what is asserted is
the request that would actually go out and the result that would actually come back — not a
hand-written double's idea of either. Nothing reaches the network and nothing reads a secret, so
these are part of the ordinary deterministic suite. Live provider evidence is
`test_openai_live.py`, which is skipped unless it is explicitly asked for.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import pytest
from openai import DefaultAsyncHttpxClient

from backend.orchestration.tests.fakes import ManualDeadlines

from backend.config import Settings
from backend.context.trigger_kind import TriggerKind
from backend.models.focused_provider import focused_provider
from backend.models.model_gateway import (
    EmptyGeneration,
    GenerationRequest,
    ModelGateway,
    ProviderTimeout,
)
from backend.models.openai_provider import (
    PROVIDER,
    OpenAIProvider,
    ProviderError,
    openai_client,
)
from backend.models.reactive_provider import reactive_provider
from backend.orchestration.clock import AsyncioDeadlines
from backend.orchestration.router_port import AttentionTier

CHARACTERS_PER_TOKEN = 4
GENEROUS_TIMEOUTS_MS = {AttentionTier.FOCUSED: 4_000, AttentionTier.REACTIVE: 2_000}

ANSWER = "The thief went east, past the well."


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


def completed_response(
    text: str = ANSWER,
    model: str = "gpt-5.6-terra-2026-07-01",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One successful Responses API body, in the SDK's own shape."""
    body: dict[str, Any] = {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": "completed",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


USAGE = {
    "input_tokens": 231,
    "output_tokens": 34,
    "total_tokens": 265,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens_details": {"reasoning_tokens": 0},
}


class Exchange:
    """The stubbed transport, and every request that actually reached it."""

    def __init__(self, respond: Callable[[int], httpx.Response]) -> None:
        self._respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(len(self.requests))

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def provider_over(
    exchange: Exchange, model: str = "gpt-5.6-terra", characters_per_token: int = 4
) -> OpenAIProvider:
    """The production client factory, with only its socket replaced.

    Built through `openai_client` rather than around it so that what these cases exercise is the
    client the service actually constructs — retries included.
    """
    client = openai_client("test-key").with_options(
        http_client=DefaultAsyncHttpxClient(transport=httpx.MockTransport(exchange))
    )
    return OpenAIProvider(
        model=model, client=client, characters_per_token=characters_per_token
    )


def always(status: int, body: dict[str, Any] | None = None) -> Callable[[int], httpx.Response]:
    return lambda _attempt: httpx.Response(status, json=body or {"error": {"message": "no"}})


def raising(failure: Exception) -> Callable[[int], httpx.Response]:
    def refuse(_attempt: int) -> httpx.Response:
        raise failure

    return refuse


# ---- what goes out ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "model", "output_tokens"),
    [
        (AttentionTier.FOCUSED, "gpt-5.6-terra", 120),
        (AttentionTier.REACTIVE, "gpt-5.6-luna", 40),
    ],
)
async def test_each_tier_calls_its_configured_model_with_reasoning_disabled(
    tier: AttentionTier, model: str, output_tokens: int
) -> None:
    """Specification #1 fixes both model identifiers and disables reasoning on both."""
    exchange = Exchange(always(200, completed_response(usage=USAGE)))

    await provider_over(exchange, model=model).generate(
        request_for(tier, output_token_limit=output_tokens)
    )

    body = exchange.bodies[0]
    assert body["model"] == model
    assert body["reasoning"] == {"effort": "none"}
    assert body["max_output_tokens"] == output_tokens


async def test_a_configured_effort_is_what_reaches_the_provider() -> None:
    """The disabled default is a setting, so it has to be one the wire actually follows."""
    exchange = Exchange(always(200, completed_response(usage=USAGE)))
    provider = OpenAIProvider(
        model="gpt-5.6-terra",
        client=provider_over(exchange).client,
        characters_per_token=4,
        reasoning_effort="high",
    )

    await provider.generate(request_for(AttentionTier.FOCUSED))

    assert exchange.bodies[0]["reasoning"] == {"effort": "high"}


async def test_an_unset_effort_omits_the_parameter_rather_than_nulling_it() -> None:
    """Omitting and disabling are different requests; `{"effort": null}` asks for the first while
    reading as the second, which is why the shipped default is the explicit `none`."""
    exchange = Exchange(always(200, completed_response(usage=USAGE)))
    provider = OpenAIProvider(
        model="gpt-5.6-terra",
        client=provider_over(exchange).client,
        characters_per_token=4,
        reasoning_effort=None,
    )

    await provider.generate(request_for(AttentionTier.FOCUSED))

    assert "reasoning" not in exchange.bodies[0]


async def test_the_rendered_prompt_is_what_is_sent() -> None:
    """The owned renderers produce the whole prompt, so nothing here rewrites or splits it."""
    exchange = Exchange(always(200, completed_response(usage=USAGE)))
    request = request_for(AttentionTier.FOCUSED, prompt="WHO YOU ARE\nMira, a baker.")

    await provider_over(exchange).generate(request)

    assert exchange.bodies[0]["input"] == "WHO YOU ARE\nMira, a baker."


async def test_a_refused_call_is_issued_exactly_once() -> None:
    """`max_retries=0`: the SDK's retry loop would spend a durable claim orchestration owns.

    A 500 is chosen deliberately — it is in the SDK's default retryable set, so a client that had
    not disabled retries would issue more than one request here.
    """
    exchange = Exchange(always(500))

    with pytest.raises(ProviderError):
        await provider_over(exchange).generate(request_for(AttentionTier.FOCUSED))

    assert len(exchange.requests) == 1


async def test_a_rate_limited_call_is_also_issued_exactly_once() -> None:
    """429 is the other status the SDK retries by default, and the demo's likeliest failure."""
    exchange = Exchange(always(429))

    with pytest.raises(ProviderError):
        await provider_over(exchange).generate(request_for(AttentionTier.REACTIVE))

    assert len(exchange.requests) == 1


# ---- what comes back -------------------------------------------------------------


async def test_a_provider_answer_normalises_into_the_mock_shaped_result() -> None:
    exchange = Exchange(always(200, completed_response(usage=USAGE)))

    behaviour = await provider_over(exchange).generate(request_for(AttentionTier.FOCUSED))

    assert behaviour.dialogue == ANSWER
    assert behaviour.provider == PROVIDER
    assert behaviour.fallback_used is False
    # Ivan owns the executable action vocabulary under #4, so a model may not invent one.
    assert behaviour.action is None


async def test_reported_usage_is_carried_rather_than_estimated() -> None:
    """Elson & Daniel's cost figures are only as good as this: an estimate would understate a
    long prompt and overstate a short one, and neither is what the call was billed."""
    exchange = Exchange(always(200, completed_response(usage=USAGE)))

    behaviour = await provider_over(exchange).generate(
        request_for(AttentionTier.FOCUSED, estimated_input_tokens=42)
    )

    assert behaviour.input_tokens == 231
    assert behaviour.output_tokens == 34


async def test_the_exact_answering_model_is_reported_not_the_configured_alias() -> None:
    """A configured alias resolves to a dated build, and the fact has to name the build."""
    exchange = Exchange(
        always(200, completed_response(model="gpt-5.6-terra-2026-07-01", usage=USAGE))
    )

    behaviour = await provider_over(exchange, model="gpt-5.6-terra").generate(
        request_for(AttentionTier.FOCUSED)
    )

    assert behaviour.model == "gpt-5.6-terra-2026-07-01"


async def test_usage_the_provider_did_not_report_falls_back_to_the_estimate() -> None:
    """A fact must always carry a token count, so a silent provider does not produce a gap."""
    exchange = Exchange(always(200, completed_response(text="12345678")))

    behaviour = await provider_over(exchange, characters_per_token=4).generate(
        request_for(AttentionTier.FOCUSED, estimated_input_tokens=42)
    )

    assert behaviour.input_tokens == 42
    assert behaviour.output_tokens == 2


async def test_the_identity_is_the_configured_model_before_any_answer_arrives() -> None:
    """A timeout has to name what it was waiting on, and no response exists to ask."""
    exchange = Exchange(always(200, completed_response(usage=USAGE)))

    identity = provider_over(exchange, model="gpt-5.6-terra").identity(AttentionTier.FOCUSED)

    assert identity.provider == PROVIDER
    assert identity.model == "gpt-5.6-terra"


# ---- what goes wrong -------------------------------------------------------------


async def test_an_unreachable_provider_fails_once_as_a_provider_error() -> None:
    """A connection error is retryable in the SDK too, so this counts requests as well."""
    exchange = Exchange(raising(httpx.ConnectError("no route to host")))

    with pytest.raises(ProviderError):
        await provider_over(exchange).generate(request_for(AttentionTier.FOCUSED))

    assert len(exchange.requests) == 1


async def test_a_timeout_is_reported_as_a_provider_timeout_not_a_provider_error() -> None:
    """The distinction is the `MODEL_TIMEOUT` code: Elson & Daniel's timeout rate depends on a
    slow provider and a refusing one being told apart."""
    exchange = Exchange(raising(httpx.ReadTimeout("timed out")))

    with pytest.raises(ProviderTimeout):
        await provider_over(exchange).generate(request_for(AttentionTier.FOCUSED))

    assert len(exchange.requests) == 1


async def test_a_response_carrying_an_error_is_a_provider_error() -> None:
    """A 200 with an `error` body is a failed call, and must not read as malformed output."""
    body = completed_response(usage=USAGE)
    body["error"] = {"code": "server_error", "message": "upstream failed"}
    exchange = Exchange(always(200, body))

    with pytest.raises(ProviderError):
        await provider_over(exchange).generate(request_for(AttentionTier.FOCUSED))


async def test_malformed_empty_output_is_refused_at_the_gateway() -> None:
    """Refusing it here as well would give one malformed answer two different error codes
    depending on which layer happened to notice first."""
    exchange = Exchange(always(200, completed_response(text="   ", usage=USAGE)))
    provider = provider_over(exchange)
    gateway = ModelGateway(
        focused=provider,
        reactive=provider,
        deadlines=AsyncioDeadlines(),
        timeouts_ms=GENEROUS_TIMEOUTS_MS,
    )

    with pytest.raises(EmptyGeneration):
        await gateway.generate(request_for(AttentionTier.FOCUSED))


async def test_a_truncated_answer_is_a_success_the_same_way_mock_mode_clips() -> None:
    """A 40-token Reactive budget makes `max_output_tokens` truncation ordinary, not exceptional.

    Mock mode already clips its own dialogue to the output budget and reports success, and the
    gateway already bounds every answer to what Minecraft accepts. Failing here instead would make
    the two modes disagree about the same outcome, and would answer a usable — if short — line
    with a cached one.
    """
    body = completed_response(text="East, past the", usage=USAGE)
    body["status"] = "incomplete"
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    exchange = Exchange(always(200, body))

    behaviour = await provider_over(exchange).generate(
        request_for(AttentionTier.REACTIVE, output_token_limit=40)
    )

    assert behaviour.dialogue == "East, past the"
    assert behaviour.fallback_used is False


# ---- what the tier budget does to a slow call ------------------------------------


class HangingTransport(httpx.AsyncBaseTransport):
    """A provider that accepts the request and then never answers."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.arrived = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.arrived.set()
        await asyncio.Event().wait()
        raise AssertionError("the hanging transport never answers")


def hanging() -> tuple[OpenAIProvider, HangingTransport]:
    transport = HangingTransport()
    client = openai_client("test-key").with_options(
        http_client=DefaultAsyncHttpxClient(transport=transport)
    )
    return (
        OpenAIProvider(model="gpt-5.6-terra", client=client, characters_per_token=4),
        transport,
    )


async def test_the_tier_budget_cuts_off_a_slow_call_and_does_not_reissue_it() -> None:
    """The gateway's deadline is the real bound on a live call, so it is asserted against a real
    SDK request in flight rather than against a double that returns promptly."""
    provider, transport = hanging()
    deadlines = ManualDeadlines()
    gateway = ModelGateway(
        focused=provider,
        reactive=provider,
        deadlines=deadlines,
        timeouts_ms=GENEROUS_TIMEOUTS_MS,
    )
    call = asyncio.ensure_future(gateway.generate(request_for(AttentionTier.FOCUSED)))
    await transport.arrived.wait()

    expired = await deadlines.expire_open()

    assert expired == (4_000,), "a Focused call is bounded by the Focused budget"
    with pytest.raises(ProviderTimeout):
        await call
    assert len(transport.requests) == 1, "an expired attempt is spent, not reissued"


async def test_cancelling_a_call_in_flight_is_not_turned_into_a_provider_failure() -> None:
    """Cancellation is not a failed attempt: orchestration suppresses cancelled work rather than
    answering it from the fallback library, and it can only do that if the cancellation reaches
    it. `CancelledError` is a `BaseException`, so nothing on this path may catch it broadly."""
    provider, transport = hanging()
    call = asyncio.ensure_future(provider.generate(request_for(AttentionTier.FOCUSED)))
    await transport.arrived.wait()

    call.cancel()

    with pytest.raises(asyncio.CancelledError):
        await call


# ---- how the tiers are configured ------------------------------------------------


def test_both_tier_adapters_carry_the_configured_effort() -> None:
    """Specification #1 disables reasoning on *both* tiers, so neither may miss the setting."""
    settings = Settings(_env_file=None, reasoning_effort="minimal")  # type: ignore[call-arg]
    client = openai_client("test-key")

    assert focused_provider(settings, client).reasoning_effort == "minimal"
    assert reactive_provider(settings, client).reasoning_effort == "minimal"


def test_the_tier_adapters_read_their_own_configured_models() -> None:
    """Replaceability, asserted where it is claimed: a settings change moves the model, and no
    orchestration conditional participates."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        focused_model="another-strong-model",
        reactive_model="another-cheap-model",
    )
    client = openai_client("test-key")

    assert focused_provider(settings, client).model == "another-strong-model"
    assert reactive_provider(settings, client).model == "another-cheap-model"


def test_the_shipped_tier_defaults_are_the_specified_models() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = openai_client("test-key")

    assert focused_provider(settings, client).model == "gpt-5.6-terra"
    assert reactive_provider(settings, client).model == "gpt-5.6-luna"
