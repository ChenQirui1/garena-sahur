"""Decide what is worth generating, and carry one accepted piece of work to a command.

Owner: Jerome & Richard

This is the seam the pipeline's stages meet at: routing decides the tier, policy decides whether
to generate, the scheduler decides when, context and the gateway produce the behaviour, and
publication commits it before sending. Nothing here decides a tier, decides when work runs, or
retries a model call.

Every trigger arrives at one of the three `on_` methods and leaves as queued work. The scheduler
calls back through `is_current`, `generate`, and `publish`, so the rules for "is this still worth
doing" live beside the stores that can answer the question.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.context.context_builder import ContextBuilder, GenerationContext
from backend.ingestion.durable_store import StorageUnavailable
from backend.ingestion.event_store import EventStore
from backend.ingestion.message_validation import ConversationTurn, GameEvent, WorldSnapshot
from backend.ingestion.turn_store import TurnStore
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
from backend.orchestration.event_relevance import EventRadii, ordered_roles, roles_in
from backend.orchestration.generation_policy import (
    GENERATING_TIERS,
    Focus,
    Generation,
    Trigger,
    decide_for_event,
    decide_for_expiry,
    decide_for_promotion,
    decide_for_turn,
)
from backend.orchestration.generation_scheduler import GenerationScheduler
from backend.orchestration.observations import (
    COMMAND_NOT_PUBLISHED,
    EVENT_GENERATION_SUPPRESSED,
    GENERATION_SUPPRESSED,
    MISSING_PROFILE,
    MODEL_CALL_FAILED,
    NO_WORLD_STATE,
    Observations,
)
from backend.orchestration.router_handoff import RouterHandoff, RoutingOutcome, RoutingStatus
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.routing_snapshot import RoutingSnapshots
from backend.orchestration.telemetry_port import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    ModelCallFact,
    TelemetryPort,
)


@dataclass(frozen=True, slots=True)
class GenerationCoordinator:
    world_state: WorldStateStore
    events: EventStore
    turns: TurnStore
    conversation: ConversationManager
    handoff: RouterHandoff
    routing_snapshots: RoutingSnapshots
    scheduler: GenerationScheduler
    context: ContextBuilder
    gateway: ModelGateway
    commands: CommandStore
    publisher: BehaviourPublisher
    telemetry: TelemetryPort
    observations: Observations
    clock: Clock
    radii: EventRadii
    command_lifetime_ms: int

    # ---- triggers -----------------------------------------------------------------

    async def on_triggered_turn(self, turn: ConversationTurn) -> None:
        """Route the current world state for this turn and queue at most one generation."""
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
            self._suppress_turn(turn, f"routing state is unavailable: {unavailable}")
            return

        decision = decide_for_turn(turn, outcome)
        if decision.generation is None:
            self._suppress_turn(turn, str(decision.suppressed))
            return

        self.scheduler.submit(decision.generation)

    async def on_event_revision(self, event: GameEvent) -> None:
        """Queue a reaction for each eligible NPC, or invalidate what this revision ends."""
        if event.is_terminal:
            # The revision that ends an event also ends every reaction still waiting for it.
            self.scheduler.cancel(
                lambda work: work.event is not None
                and (work.session_id, work.event.event_id) == (event.session_id, event.event_id),
                f"event {event.status}",
            )

        snapshot = self.world_state.latest_for_session(event.session_id)
        if snapshot is None:
            self.observations.note(
                NO_WORLD_STATE, session_id=event.session_id, event_id=event.event_id
            )
            return

        try:
            roles_by_npc = await self._roles_by_npc(event, snapshot)
            # A terminal revision still refreshes routing; it just never asks for generation.
            outcome = await self._route(event.session_id, snapshot)
        except StorageUnavailable as unavailable:
            # The revision is committed and acknowledged; only the reaction is lost, and a
            # later revision can still produce one.
            self._suppress_event(
                event, None, f"routing state is unavailable: {unavailable}"
            )
            return

        decision = decide_for_event(event, outcome, roles_by_npc)
        for npc_id, reason in decision.suppressed:
            self._suppress_event(event, npc_id, reason)
        for generation in decision.generations:
            self.scheduler.submit(generation)

    async def on_routing_outcome(self, outcome: RoutingOutcome) -> None:
        """React to a refreshed set of assignments: cancel what fell out, queue what rose.

        This is where promotion and expiry are noticed. The snapshot behind the outcome is the
        tick, never the trigger — an unchanged assignment and an unexpired command both leave
        with nothing queued.
        """
        if outcome.status is not RoutingStatus.ROUTED:
            return

        generating = {
            one.npc_id for one in outcome.assignments if one.tier in GENERATING_TIERS
        }
        self.scheduler.cancel(
            lambda work: work.session_id == outcome.session_id
            and work.npc_id not in generating,
            "npc no longer holds a generating tier",
        )

        now_ms = self.clock.now_ms()
        for assignment in outcome.assignments:
            if assignment.tier not in GENERATING_TIERS:
                continue
            focus = await self._focus_for(outcome.session_id, assignment.npc_id)
            behaviour = await self.commands.latest_for(outcome.session_id, assignment.npc_id)
            promotion = decide_for_promotion(assignment, outcome, focus, behaviour, now_ms)
            if promotion is not None:
                self.scheduler.submit(promotion)
                continue
            expiry = decide_for_expiry(assignment, outcome, focus, behaviour, now_ms)
            if expiry is not None:
                self.scheduler.submit(expiry)

    # ---- executor -----------------------------------------------------------------

    async def is_current(self, work: Generation) -> str | None:
        """Why this work is no longer worth doing, or ``None`` while it still is."""
        outcome = self.handoff.latest_outcome(work.session_id, work.world_id)
        if outcome is None or outcome.status is not RoutingStatus.ROUTED:
            return "no current routing result"

        assignment = next(
            (one for one in outcome.assignments if one.npc_id == work.npc_id), None
        )
        if assignment is None:
            return "npc is no longer a routed candidate"
        if assignment.tier not in GENERATING_TIERS:
            return f"npc is {assignment.tier.value}"

        if work.event is not None:
            stored = await self.events.latest(work.session_id, work.event.event_id)
            if stored is None:
                return "the event is no longer stored"
            if stored.event.is_terminal:
                return f"event {stored.event.status}"
            if (
                work.trigger is Trigger.EVENT
                and stored.event.event_revision != work.event.event_revision
            ):
                return "a newer revision superseded this one"

        if work.trigger is Trigger.EXPIRY:
            behaviour = await self.commands.latest_for(work.session_id, work.npc_id)
            if behaviour is not None and behaviour.command_id != work.expired_command_id:
                return "newer behaviour replaced the expired command"

        return None

    async def generate(self, work: Generation) -> BehaviourCommand | None:
        """Call the provider once and turn what comes back into a command. Never publishes."""
        snapshot = self.world_state.latest_for_session(work.session_id)
        if snapshot is None:
            self.observations.note(
                NO_WORLD_STATE, session_id=work.session_id, npc_id=work.npc_id
            )
            return None

        request = await self._request_for(work, snapshot)
        started_at_ms = self.clock.now_ms()
        try:
            behaviour = await self.gateway.generate(request)
        except NoProviderForTier as misrouted:
            # Nothing was attempted, so there is no model call to report.
            self.observations.note(
                MODEL_CALL_FAILED, request_id=request.request_id, reason=repr(misrouted)
            )
            return None
        except Exception as failure:
            self._record_failure(request, started_at_ms, failure)
            return None

        self.telemetry.record_model_call(
            _fact(request, behaviour, started_at_ms, self.clock.now_ms())
        )
        return await self._command(request, behaviour)

    async def publish(self, work: Generation, command: BehaviourCommand) -> None:
        """Commit and send one command, and let an answered conversation move on."""
        try:
            await self.publisher.publish(command)
        except Exception as undeliverable:
            # The model call is spent either way, but the conversation must not be left
            # waiting for a command that will never arrive.
            self.observations.note(
                COMMAND_NOT_PUBLISHED,
                request_id=command.request_id,
                reason=repr(undeliverable),
            )
            if work.trigger is Trigger.TURN:
                self.conversation.note_not_generated(work.session_id)
            return

        if work.trigger is Trigger.TURN:
            self.conversation.note_published(work.session_id)

    def abandon(self, work: Generation, reason: str) -> None:
        if work.trigger is Trigger.TURN and work.turn is not None:
            self.observations.note(
                GENERATION_SUPPRESSED,
                session_id=work.session_id,
                turn_id=work.turn.turn_id,
                npc_id=work.npc_id,
                reason=reason,
            )
            return
        self.observations.note(
            EVENT_GENERATION_SUPPRESSED,
            session_id=work.session_id,
            event_id=work.event.event_id if work.event else None,
            event_revision=work.event.event_revision if work.event else None,
            npc_id=work.npc_id,
            reason=reason,
        )

    def note_turn_not_generated(self, session_id: str) -> None:
        self.conversation.note_not_generated(session_id)

    # ---- internals ----------------------------------------------------------------

    async def _route(self, session_id: str, snapshot: WorldSnapshot) -> RoutingOutcome:
        """Route this world state now, because the decision below needs its tiers."""
        return self.handoff.route_now(
            await self.routing_snapshots.build(
                snapshot, self.conversation.projection(session_id)
            )
        )

    async def _focus_for(self, session_id: str, npc_id: str) -> Focus:
        """What still requires foreground behaviour from this NPC, if anything does.

        The active conversation comes first: a player waiting on an answer outranks scenery.
        """
        active = self.conversation.active_conversation(session_id)
        if active is not None and active.target_npc_id == npc_id:
            turn = await self.turns.latest_player_turn(session_id, active.conversation_id)
            if turn is not None:
                return Focus(turn=turn)

        snapshot = self.world_state.latest_for_session(session_id)
        if snapshot is None:
            return Focus()
        observed = next((one for one in snapshot.npcs if one.npc_id == npc_id), None)
        if observed is None:
            return Focus()

        for stored in await self.events.active(session_id):
            roles = ordered_roles(
                roles_in(npc_id, observed.position, stored, self.radii)
            )
            if roles:
                return Focus(event=stored.event, roles=roles)
        return Focus()

    async def _roles_by_npc(
        self, event: GameEvent, snapshot: WorldSnapshot
    ) -> dict[str, tuple[str, ...]]:
        """Each candidate's part in *this* event, in candidate order, omitting the unrelated."""
        stored = await self.events.latest(event.session_id, event.event_id)
        if stored is None:
            return {}
        roles = {
            observation.npc_id: ordered_roles(
                roles_in(observation.npc_id, observation.position, stored, self.radii)
            )
            for observation in snapshot.npcs
        }
        return {npc_id: held for npc_id, held in roles.items() if held}

    async def _request_for(
        self, work: Generation, snapshot: WorldSnapshot
    ) -> GenerationRequest:
        """Conversation work answers a turn; everything else reacts to an event."""
        if work.turn is not None:
            context = await self.context.build(work.tier, work.turn, snapshot)
            self._note_unknown_profile(context, work.npc_id)
            return self._request(
                work,
                context,
                trigger=(work.turn.conversation_id, work.turn.turn_id),
                conversation_id=work.turn.conversation_id,
                turn_id=work.turn.turn_id,
                event_id=None,
            )

        assert work.event is not None, "generation work carries a turn or an event"
        context = await self.context.build_for_event(
            work.tier, work.event, work.npc_id, work.roles, snapshot
        )
        self._note_unknown_profile(context, work.npc_id)
        return self._request(
            work,
            context,
            trigger=(work.event.event_id, str(work.event.event_revision)),
            conversation_id=None,
            turn_id=None,
            event_id=work.event.event_id,
        )

    def _note_unknown_profile(self, context: GenerationContext, npc_id: str) -> None:
        if not context.npc.authored:
            self.observations.note(MISSING_PROFILE, npc_id=npc_id)

    def _request(
        self,
        work: Generation,
        context: GenerationContext,
        *,
        trigger: tuple[str | None, str | None],
        conversation_id: str | None,
        turn_id: str | None,
        event_id: str | None,
    ) -> GenerationRequest:
        """``trigger`` is what makes this request distinct for this NPC in this session, so a
        retry of the same work reuses the same identifiers and a new trigger never collides."""
        render = (
            render_focused_prompt
            if work.tier is AttentionTier.FOCUSED
            else render_reactive_prompt
        )
        return GenerationRequest(
            request_id=(
                "request-"
                f"{identity_digest(work.session_id, work.npc_id, work.trigger.value, *trigger)}"
            ),
            session_id=work.session_id,
            npc_id=work.npc_id,
            npc_name=context.npc.name,
            tier=work.tier,
            conversation_id=conversation_id,
            turn_id=turn_id,
            event_id=event_id,
            source_sequence=work.source_sequence,
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

    def _suppress_turn(self, turn: ConversationTurn, reason: str) -> None:
        self.observations.note(
            GENERATION_SUPPRESSED,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            npc_id=turn.target_npc_id,
            reason=reason,
        )
        self.conversation.note_not_generated(turn.session_id)

    def _suppress_event(self, event: GameEvent, npc_id: str | None, reason: str) -> None:
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
