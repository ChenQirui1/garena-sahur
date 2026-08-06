"""The two shapes of context a renderer can be handed.

Owner: Jerome & Richard

They carry the sections their real builder path produces — the conversation path has history and
a relevant event, the reaction path has neither — so a case that swaps the heading map is visibly
wrong rather than merely differently worded.
"""

from __future__ import annotations

from backend.context.context_builder import (
    EVENT,
    HISTORY,
    OUTPUT_CONTRACT,
    PROFILE,
    TRIGGER,
    WORLD,
    ContextSection,
    GenerationContext,
    TriggerKind,
)
from backend.context.npc_profiles import NpcProfiles
from backend.orchestration.router_port import AttentionTier

CONVERSATION_SECTIONS = (OUTPUT_CONTRACT, TRIGGER, PROFILE, EVENT, WORLD, HISTORY)
REACTION_SECTIONS = (OUTPUT_CONTRACT, TRIGGER, PROFILE, WORLD)

PLAYER_SPEECH_TEXT = 'The player says: "Which direction did the thief run?"'
OBSERVED_EVENT_TEXT = "A market theft has just begun nearby. You are the victim."


def conversation_context(tier: AttentionTier) -> GenerationContext:
    return _context(tier, TriggerKind.PLAYER_SPEECH, CONVERSATION_SECTIONS, PLAYER_SPEECH_TEXT)


def reaction_context(tier: AttentionTier) -> GenerationContext:
    return _context(tier, TriggerKind.OBSERVED_EVENT, REACTION_SECTIONS, OBSERVED_EVENT_TEXT)


def _context(
    tier: AttentionTier,
    trigger_kind: TriggerKind,
    names: tuple[str, ...],
    trigger_text: str,
) -> GenerationContext:
    return GenerationContext(
        tier=tier,
        trigger_kind=trigger_kind,
        npc=NpcProfiles.empty().profile_for("shopkeeper-uuid"),
        trigger_text=trigger_text,
        sections=tuple(
            ContextSection(name, trigger_text if name == TRIGGER else f"body of {name}")
            for name in names
        ),
        estimated_input_tokens=42,
        output_token_limit=120,
    )
