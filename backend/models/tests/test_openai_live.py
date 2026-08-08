"""Owner: Jerome & Richard

The only case in the owned suite that spends money and needs a network. It is skipped unless it is
asked for by name *and* a key is present, because specification #1 requires deterministic slice
validation not to depend on external model availability — a case that ran whenever a developer
happened to have a key exported would make the suite's colour depend on the environment.

Skipped is what this reports on an ordinary run. A delivery note may never describe a skip here as
live provider evidence.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.context.trigger_kind import TriggerKind
from backend.models.focused_provider import focused_provider
from backend.models.model_gateway import GenerationRequest, ModelGateway
from backend.models.openai_provider import PROVIDER, openai_client
from backend.models.reactive_provider import reactive_provider
from backend.orchestration.clock import AsyncioDeadlines
from backend.orchestration.router_port import AttentionTier

OPT_IN = "SPOTLIGHT_OPENAI_LIVE_TEST"
KEY = "SPOTLIGHT_OPENAI_API_KEY"


def live_key_available() -> bool:
    """Ask `Settings`, not the process environment.

    The key's documented home is `.env` — that is what `.env.example` describes and the only
    place a value survives between shells. Reading `os.environ` here instead made the gate
    disagree with the source the test itself loads from, so a correctly configured checkout
    skipped and reported it as "no key".
    """
    try:
        key = Settings().openai_api_key
    except ValidationError:
        return False
    return key is not None and bool(key.get_secret_value().strip())


# The opt-in flag stays an environment variable deliberately: the key is deployment configuration
# and belongs in `.env`, but spending money has to be asked for on the command line each time.
pytestmark = pytest.mark.skipif(
    not (os.environ.get(OPT_IN) and live_key_available()),
    reason=f"live provider test: set {OPT_IN}=1 and put {KEY} in the environment or .env",
)


def live_request(tier: AttentionTier, output_tokens: int) -> GenerationRequest:
    return GenerationRequest(
        request_id="live-request",
        session_id="live-session",
        npc_id="shopkeeper-uuid",
        npc_name="Mira",
        tier=tier,
        trigger_kind=TriggerKind.PLAYER_SPEECH,
        conversation_id="live-conversation",
        turn_id="live-turn",
        event_id=None,
        source_sequence=1,
        prompt=(
            "INSTRUCTIONS\nYou are an NPC in a Minecraft market. Reply with one short spoken"
            " line and nothing else.\n\nWHO YOU ARE\nMira, who runs the bread stall.\n\n"
            "THE PLAYER\nWhich direction did the thief run?"
        ),
        trigger_text="Which direction did the thief run?",
        estimated_input_tokens=60,
        output_token_limit=output_tokens,
    )


@pytest.mark.parametrize(
    ("tier", "output_tokens"),
    [(AttentionTier.FOCUSED, 120), (AttentionTier.REACTIVE, 40)],
)
async def test_a_live_call_returns_a_usable_bounded_answer(
    tier: AttentionTier, output_tokens: int
) -> None:
    """Run through the gateway, so the real tier deadline and the real bound both apply."""
    settings = Settings()
    key = settings.openai_api_key
    assert key is not None
    client = openai_client(key.get_secret_value())
    gateway = ModelGateway(
        focused=focused_provider(settings, client),
        reactive=reactive_provider(settings, client),
        deadlines=AsyncioDeadlines(),
        timeouts_ms={
            AttentionTier.FOCUSED: settings.focused_timeout_ms,
            AttentionTier.REACTIVE: settings.reactive_timeout_ms,
        },
    )

    behaviour = await gateway.generate(live_request(tier, output_tokens))

    assert behaviour.dialogue
    assert behaviour.provider == PROVIDER
    assert behaviour.fallback_used is False
    assert behaviour.output_tokens > 0
