"""Carry one armed conversation turn from routing to a published behaviour command.

Owner: Jerome & Richard

This is the seam the pipeline's stages meet at: routing decides the tier, policy decides
whether to generate, the claim makes it happen at most once, context and the gateway produce
the behaviour, and publication commits it before sending. Nothing here decides a tier, and
nothing here retries a model call.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.context.context_builder import ContextBuilder, GenerationContext
from backend.ingestion.message_validation import ConversationTurn, WorldSnapshot
from backend.ingestion.world_state_store import WorldStateStore
from backend.models.model_gateway import (
    GeneratedBehaviour,
    GenerationRequest,
    ModelGateway,
    NoProviderForTier,
)
from backend.models.prompts.focused_prompt import render_focused_prompt
from backend.models.prompts.reactive_prompt import render_reactive_prompt
from backend.orchestration.behaviour_command import BehaviourCommand, identity_digest
from backend.orchestration.behaviour_publisher import BehaviourPublisher
from backend.orchestration.clock import Clock
from backend.orchestration.command_store import CommandStore
from backend.orchestration.conversation_manager import ConversationManager
from backend.orchestration.deduplication import GenerationClaims
from backend.orchestration.generation_policy import TurnGeneration, decide_for_turn
from backend.orchestration.observations import (
    COMMAND_NOT_PUBLISHED,
    GENERATION_SUPPRESSED,
    MISSING_PROFILE,
    MODEL_CALL_FAILED,
    NO_WORLD_STATE,
    Observations,
)
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.routing_snapshot import build_routing_snapshot
from backend.orchestration.telemetry_port import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    ModelCallFact,
    TelemetryPort,
)


@dataclass(frozen=True, slots=True)
class GenerationCoordinator:
    world_state: WorldStateStore
    conversation: ConversationManager
    handoff: RouterHandoff
    claims: GenerationClaims
    context: ContextBuilder
    gateway: ModelGateway
    commands: CommandStore
    publisher: BehaviourPublisher
    telemetry: TelemetryPort
    observations: Observations
    clock: Clock
    command_lifetime_ms: int

    async def on_triggered_turn(self, turn: ConversationTurn) -> None:
        """Route the current world state for this turn and generate at most one command."""
        snapshot = self.world_state.latest_for_session(turn.session_id)
        if snapshot is None:
            self.observations.note(
                NO_WORLD_STATE, session_id=turn.session_id, turn_id=turn.turn_id
            )
            self.conversation.note_not_generated(turn.session_id)
            return

        outcome = self.handoff.route_now(
            build_routing_snapshot(snapshot, self.conversation.projection(turn.session_id))
        )
        decision = decide_for_turn(turn, outcome)
        if decision.generation is None:
            self._suppress(turn, str(decision.suppressed))
            return

        generation = decision.generation
        if not await self.claims.claim(generation.claim_key, self.clock.now_ms()):
            self._suppress(turn, "generation was already claimed")
            return

        await self._generate(generation, snapshot)

    async def _generate(self, generation: TurnGeneration, snapshot: WorldSnapshot) -> None:
        turn = generation.turn
        context = await self.context.build(generation.tier, turn, snapshot)
        if not context.npc.authored:
            self.observations.note(MISSING_PROFILE, npc_id=turn.target_npc_id)

        request = self._request(generation, context)
        started_at_ms = self.clock.now_ms()
        try:
            behaviour = await self.gateway.generate(request)
        except NoProviderForTier as misrouted:
            # Nothing was attempted, so there is no model call to report.
            self._suppress(turn, repr(misrouted))
            return
        except Exception as failure:
            self._record_failure(request, started_at_ms, failure)
            self._suppress(turn, "the provider produced nothing usable")
            return

        self.telemetry.record_model_call(
            _fact(request, behaviour, started_at_ms, self.clock.now_ms())
        )
        try:
            await self.publisher.publish(await self._command(request, behaviour))
        except Exception as undeliverable:
            # The model call is spent either way, but the conversation must not be stranded
            # waiting for a command that will never arrive.
            self.observations.note(
                COMMAND_NOT_PUBLISHED,
                request_id=request.request_id,
                reason=repr(undeliverable),
            )
            self.conversation.note_not_generated(turn.session_id)
            return

        self.conversation.note_published(turn.session_id)

    def _request(
        self, generation: TurnGeneration, context: GenerationContext
    ) -> GenerationRequest:
        turn = generation.turn
        digest = identity_digest(
            turn.session_id, turn.target_npc_id, turn.conversation_id, turn.turn_id
        )
        render = (
            render_focused_prompt
            if generation.tier is AttentionTier.FOCUSED
            else render_reactive_prompt
        )
        return GenerationRequest(
            request_id=f"request-{digest}",
            session_id=turn.session_id,
            npc_id=turn.target_npc_id,
            npc_name=context.npc.name,
            tier=generation.tier,
            conversation_id=turn.conversation_id,
            turn_id=turn.turn_id,
            event_id=None,
            source_sequence=generation.source_sequence,
            prompt=render(context),
            trigger_text=context.trigger_text,
            estimated_input_tokens=context.estimated_input_tokens,
            output_token_limit=context.output_token_limit,
        )

    async def _command(
        self, request: GenerationRequest, behaviour: GeneratedBehaviour
    ) -> BehaviourCommand:
        created_at_ms = self.clock.now_ms()
        return BehaviourCommand(
            session_id=request.session_id,
            command_id=f"command-{request.request_id.removeprefix('request-')}",
            request_id=request.request_id,
            npc_id=request.npc_id,
            tier=request.tier.value,
            event_id=request.event_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            source_sequence=request.source_sequence,
            created_at_ms=created_at_ms,
            expires_at_ms=created_at_ms + self.command_lifetime_ms,
            dialogue=behaviour.dialogue,
            action=behaviour.action,
            fallback_used=behaviour.fallback_used,
            command_sequence=await self.commands.next_sequence(
                request.session_id, request.npc_id
            ),
        )

    def _record_failure(
        self, request: GenerationRequest, started_at_ms: int, failure: Exception
    ) -> None:
        self.observations.note(
            MODEL_CALL_FAILED, request_id=request.request_id, reason=repr(failure)
        )
        self.telemetry.record_model_call(
            ModelCallFact(
                session_id=request.session_id,
                request_id=request.request_id,
                npc_id=request.npc_id,
                tier=request.tier.value,
                provider=None,
                model=None,
                event_id=request.event_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                source_sequence=request.source_sequence,
                started_at_ms=started_at_ms,
                completed_at_ms=self.clock.now_ms(),
                input_tokens=request.estimated_input_tokens,
                output_tokens=0,
                status=STATUS_ERROR,
                fallback_used=False,
                error_code=type(failure).__name__,
            )
        )

    def _suppress(self, turn: ConversationTurn, reason: str) -> None:
        self.observations.note(
            GENERATION_SUPPRESSED,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            npc_id=turn.target_npc_id,
            reason=reason,
        )
        self.conversation.note_not_generated(turn.session_id)


def _fact(
    request: GenerationRequest,
    behaviour: GeneratedBehaviour,
    started_at_ms: int,
    completed_at_ms: int,
) -> ModelCallFact:
    return ModelCallFact(
        session_id=request.session_id,
        request_id=request.request_id,
        npc_id=request.npc_id,
        tier=request.tier.value,
        provider=behaviour.provider,
        model=behaviour.model,
        event_id=request.event_id,
        conversation_id=request.conversation_id,
        turn_id=request.turn_id,
        source_sequence=request.source_sequence,
        started_at_ms=started_at_ms,
        completed_at_ms=completed_at_ms,
        input_tokens=behaviour.input_tokens,
        output_tokens=behaviour.output_tokens,
        status=STATUS_SUCCESS,
        fallback_used=behaviour.fallback_used,
        error_code=None,
    )
