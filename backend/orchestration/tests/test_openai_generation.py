"""One OpenAI answer, and one OpenAI refusal, through the whole owned pipeline.

Owner: Jerome & Richard

`backend/models/tests/test_openai_provider.py` asserts the adapter against the wire. This asserts
what the rest of the backend does with it: that a live-mode answer becomes a published command and
a complete `model_call` fact, and that a live-mode refusal takes the same fallback path mock mode
already proved. The socket is stubbed, so this is deterministic owned-pipeline evidence and not
live provider integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from openai import DefaultAsyncHttpxClient

from backend.ingestion.tests.canonical_messages import active_conversation
from backend.models.openai_provider import PROVIDER, OpenAIProvider, openai_client
from backend.models.tests.test_openai_provider import USAGE, completed_response
from backend.orchestration.observations import MODEL_CALL_FAILED
from backend.orchestration.telemetry_port import STATUS_ERROR, STATUS_SUCCESS
from backend.orchestration.tests.harness import running, settings_for

ANSWER = "East, past the well. He had my loaves under his arm."
ANSWERING_MODEL = "gpt-5.6-terra-2026-07-01"


def provider_answering(response: httpx.Response) -> tuple[OpenAIProvider, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return response

    client = openai_client("test-key").with_options(
        http_client=DefaultAsyncHttpxClient(transport=httpx.MockTransport(serve))
    )
    return OpenAIProvider(model="gpt-5.6-terra", client=client, characters_per_token=4), sent


def answered(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


async def test_an_openai_answer_becomes_a_command_and_a_complete_model_call_fact(
    tmp_path: Path,
) -> None:
    provider, sent = provider_answering(
        answered(completed_response(text=ANSWER, model=ANSWERING_MODEL, usage=USAGE))
    )

    async for live in running(settings_for(tmp_path), provider=provider):
        await live.snapshot(active_conversation=active_conversation())
        await live.turn()
        await live.settle()

        assert len(sent) == 1, "one durable claim buys one external request"

        assert len(live.publisher.published) == 1
        command = live.publisher.published[0]
        assert command.dialogue == ANSWER
        assert command.fallback_used is False

        assert len(live.telemetry.model_calls) == 1
        fact = live.telemetry.model_calls[0]
        assert fact.status == STATUS_SUCCESS
        assert fact.provider == PROVIDER
        # The exact model that answered, not the configured alias that was asked for.
        assert fact.model == ANSWERING_MODEL
        assert (fact.input_tokens, fact.output_tokens) == (231, 34)
        assert fact.latency_ms >= 0
        assert fact.fallback_used is False
        assert fact.error_code is None
        assert (fact.session_id, fact.npc_id) == (command.session_id, command.npc_id)


async def test_an_openai_refusal_falls_back_without_a_second_request(tmp_path: Path) -> None:
    """The same fallback path mock mode proved, reached by a real SDK error this time."""
    provider, sent = provider_answering(httpx.Response(500, json={"error": {"message": "no"}}))

    async for live in running(settings_for(tmp_path), provider=provider):
        await live.snapshot(active_conversation=active_conversation())
        await live.turn()
        await live.settle()

        assert len(sent) == 1, "a failed attempt is spent, not repeated"
        assert len(live.publisher.published) == 1
        assert live.publisher.published[0].fallback_used is True
        assert live.publisher.published[0].dialogue == "Cached for Mira's turn."
        assert live.observed(MODEL_CALL_FAILED)

        fact = live.telemetry.model_calls[0]
        assert fact.status == STATUS_ERROR
        assert fact.error_code == "PROVIDER_ERROR"
        # `docs/message_schemas.md` §7 allows a null provider only before selection; this call
        # was selected and made, so it names what it was waiting on.
        assert (fact.provider, fact.model) == (PROVIDER, "gpt-5.6-terra")
        assert fact.fallback_used is True


async def test_a_malformed_openai_answer_falls_back_as_an_invalid_response(
    tmp_path: Path,
) -> None:
    """An empty answer is a spent attempt with nothing publishable, not a silent NPC."""
    provider, sent = provider_answering(
        answered(completed_response(text="   ", usage=USAGE))
    )

    async for live in running(settings_for(tmp_path), provider=provider):
        await live.snapshot(active_conversation=active_conversation())
        await live.turn()
        await live.settle()

        assert len(sent) == 1
        assert len(live.publisher.published) == 1
        assert live.publisher.published[0].fallback_used is True
        assert live.telemetry.model_calls[0].error_code == "INVALID_MODEL_RESPONSE"
