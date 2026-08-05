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
from backend.ingestion.durable_store import StorageUnavailable
from backend.ingestion.event_store import EventStore
from backend.ingestion.message_validation import ConversationTurn, GameEvent, WorldSnapshot
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
from backend.orchestration.event_relevance import ROLE_ORDER, EventRadii, roles_in
from backend.orchestration.generation_policy import (
    EventGeneration,
    TurnGeneration,
    decide_for_event,
    decide_for_turn,
)
from backend.orchestration.observations import (
    COMMAND_NOT_PUBLISHED,
    EVENT_GENERATION_SUPPRESSED,
    GENERATION_SUPPRESSED,
    MISSING_PROFILE,
    MODEL_CALL_FAILED,
    NO_WORLD_STATE,
    Observations,
)
from backend.orchestration.router_handoff import RouterHandoff, RoutingOutcome
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.routing_snapshot import RoutingSnapshots
from backend.orchestration.telemetry_port import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    ModelCallFact,
    TelemetryPort,
)


@dataclass(frozen=True, slots=True)
class _Attempt:
    """What one provider attempt left behind.

    Exactly one of these holds: a command was published, nothing usable was produced, or a
    command existed but could not be delivered. The third is not a suppression — the model call
    was spent and the failure is already recorded — so the caller treats it differently.
    """

    command: BehaviourCommand | None = None
    suppressed: str | None = None
    undeliverable: bool = False


@dataclass(frozen=True, slots=True)
class GenerationCoordinator:
    world_state: WorldStateStore
    events: EventStore
    conversation: ConversationManager
    handoff: RouterHandoff
    routing_snapshots: RoutingSnapshots
    claims: GenerationClaims
    context: ContextBuilder
    gateway: ModelGateway
    commands: CommandStore
    publisher: BehaviourPublisher
    telemetry: TelemetryPort
    observations: Observations
    clock: Clock
    radii: EventRadii
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

        try:
            outcome = await self._route(turn.session_id, snapshot)
        except StorageUnavailable as unavailable:
            self._suppress(turn, f"routing state is unavailable: {unavailable}")
            return

        decision = decide_for_turn(turn, outcome)
        if decision.generation is None:
            self._suppress(turn, str(decision.suppressed))
            return

        generation = decision.generation
        if not await self.claims.claim(generation.claim_key, self.clock.now_ms()):
            self._suppress(turn, "generation was already claimed")
            return

        attempt = await self._attempt(generation, snapshot)
        if attempt.command is not None:
            self.conversation.note_published(turn.session_id)
        elif attempt.suppressed is not None:
            self._suppress(turn, attempt.suppressed)
        else:
            self.conversation.note_not_generated(turn.session_id)

    async def on_event_revision(self, event: GameEvent) -> None:
        """Route the current world state for this revision and react once per eligible NPC.

        Every NPC with a part in this event is considered, in candidate order, so the same
        revision against the same world state always produces the same commands in the same
        order. Bounding how many run at once is issue #8.
        """
        snapshot = self.world_state.latest_for_session(event.session_id)
        if snapshot is None:
            self.observations.note(
                NO_WORLD_STATE, session_id=event.session_id, event_id=event.event_id
            )
            return

        roles_by_npc = await self._roles_by_npc(event, snapshot)
        # A terminal revision still refreshes routing; it just never asks for generation.
        outcome = await self._route(event.session_id, snapshot)
        decision = decide_for_event(event, outcome, roles_by_npc)
        for npc_id, reason in decision.suppressed:
            self._suppress_event(event, npc_id, reason)

        for generation in decision.generations:
            if not await self.claims.claim(generation.claim_key, self.clock.now_ms()):
                self._suppress_event(
                    event, generation.npc_id, "generation was already claimed"
                )
                continue
            attempt = await self._attempt(generation, snapshot)
            if attempt.suppressed is not None:
                self._suppress_event(event, generation.npc_id, attempt.suppressed)

    async def _route(self, session_id: str, snapshot: WorldSnapshot) -> RoutingOutcome:
        """Route this world state now, because the decision below needs its tiers."""
        return self.handoff.route_now(
            await self.routing_snapshots.build(
                snapshot, self.conversation.projection(session_id)
            )
        )

    async def _roles_by_npc(
        self, event: GameEvent, snapshot: WorldSnapshot
    ) -> dict[str, tuple[str, ...]]:
        """Each candidate's part in *this* event, in candidate order, omitting the unrelated."""
        stored = await self.events.latest(event.session_id, event.event_id)
        if stored is None:
            return {}
        roles = {
            observation.npc_id: tuple(
                role
                for role in ROLE_ORDER
                if role in roles_in(observation.npc_id, observation.position, stored, self.radii)
            )
            for observation in snapshot.npcs
        }
        return {npc_id: held for npc_id, held in roles.items() if held}

    async def _attempt(
        self, generation: TurnGeneration | EventGeneration, snapshot: WorldSnapshot
    ) -> _Attempt:
        """Build context, call the provider once, and publish what comes back."""
        request = await self._request_for(generation, snapshot)
        started_at_ms = self.clock.now_ms()
        try:
            behaviour = await self.gateway.generate(request)
        except NoProviderForTier as misrouted:
            # Nothing was attempted, so there is no model call to report.
            return _Attempt(suppressed=repr(misrouted))
        except Exception as failure:
            self._record_failure(request, started_at_ms, failure)
            return _Attempt(suppressed="the provider produced nothing usable")

        self.telemetry.record_model_call(
            _fact(request, behaviour, started_at_ms, self.clock.now_ms())
        )
        command = await self._command(request, behaviour)
        try:
            await self.publisher.publish(command)
        except Exception as undeliverable:
            # The model call is spent either way, but the caller must not be left waiting for
            # a command that will never arrive.
            self.observations.note(
                COMMAND_NOT_PUBLISHED,
                request_id=request.request_id,
                reason=repr(undeliverable),
            )
            return _Attempt(undeliverable=True)

        return _Attempt(command=command)

    async def _request_for(
        self, generation: TurnGeneration | EventGeneration, snapshot: WorldSnapshot
    ) -> GenerationRequest:
        """The one place the two triggers differ in what they ask the provider for."""
        if isinstance(generation, TurnGeneration):
            context = await self.context.build(generation.tier, generation.turn, snapshot)
            self._note_unknown_profile(context, generation.turn.target_npc_id)
            return self._turn_request(generation, context)

        context = await self.context.build_for_event(
            generation.tier,
            generation.event,
            generation.npc_id,
            generation.roles,
            snapshot,
        )
        self._note_unknown_profile(context, generation.npc_id)
        return self._event_request(generation, context)

    def _note_unknown_profile(self, context: GenerationContext, npc_id: str) -> None:
        if not context.npc.authored:
            self.observations.note(MISSING_PROFILE, npc_id=npc_id)

    def _turn_request(
        self, generation: TurnGeneration, context: GenerationContext
    ) -> GenerationRequest:
        turn = generation.turn
        return self._request(
            generation.tier,
            context,
            identity=(turn.session_id, turn.target_npc_id, turn.conversation_id, turn.turn_id),
            session_id=turn.session_id,
            npc_id=turn.target_npc_id,
            conversation_id=turn.conversation_id,
            turn_id=turn.turn_id,
            event_id=None,
            source_sequence=generation.source_sequence,
        )

    def _event_request(
        self, generation: EventGeneration, context: GenerationContext
    ) -> GenerationRequest:
        event = generation.event
        return self._request(
            generation.tier,
            context,
            identity=(
                event.session_id,
                generation.npc_id,
                event.event_id,
                str(event.event_revision),
            ),
            session_id=event.session_id,
            npc_id=generation.npc_id,
            conversation_id=None,
            turn_id=None,
            event_id=event.event_id,
            source_sequence=generation.source_sequence,
        )

    def _request(
        self,
        tier: AttentionTier,
        context: GenerationContext,
        *,
        identity: tuple[str | None, ...],
        session_id: str,
        npc_id: str,
        conversation_id: str | None,
        turn_id: str | None,
        event_id: str | None,
        source_sequence: int,
    ) -> GenerationRequest:
        render = (
            render_focused_prompt if tier is AttentionTier.FOCUSED else render_reactive_prompt
        )
        return GenerationRequest(
            request_id=f"request-{identity_digest(*identity)}",
            session_id=session_id,
            npc_id=npc_id,
            npc_name=context.npc.name,
            tier=tier,
            conversation_id=conversation_id,
            turn_id=turn_id,
            event_id=event_id,
            source_sequence=source_sequence,
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

    def _suppress_event(self, event: GameEvent, npc_id: str, reason: str) -> None:
        """An event suppression names the revision, because the next one may well generate."""
        self.observations.note(
            EVENT_GENERATION_SUPPRESSED,
            session_id=event.session_id,
            event_id=event.event_id,
            event_revision=event.event_revision,
            npc_id=npc_id,
            reason=reason,
        )


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
