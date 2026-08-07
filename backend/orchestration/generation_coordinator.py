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
from typing import Any, Mapping

from backend.context.context_builder import ContextBuilder, GenerationContext
from backend.context.trigger_kind import TriggerKind
from backend.ingestion.durable_store import StorageUnavailable
from backend.ingestion.event_store import EventStore
from backend.ingestion.message_validation import ConversationTurn, GameEvent, WorldSnapshot
from backend.ingestion.turn_store import TurnStore
from backend.ingestion.world_state_store import WorldStateStore
from backend.models.fallback import FallbackLibrary
from backend.models.model_gateway import (
    GeneratedBehaviour,
    GenerationRequest,
    ModelGateway,
    NoProviderForTier,
    ProviderIdentity,
    ProviderTimeout,
    error_code_for,
)
from backend.models.prompts.renderer_selection import renderer_for
from backend.orchestration.behaviour_command import BehaviourCommand, identity_digest
from backend.orchestration.behaviour_publisher import BehaviourPublisher
from backend.orchestration.clock import Clock
from backend.orchestration.command_store import CommandStore
from backend.orchestration.conversation_manager import ConversationManager
from backend.orchestration.deduplication import FAILED, SUCCEEDED, ProviderAttempts
from backend.orchestration.event_relevance import EventRadii, ordered_roles, roles_in
from backend.orchestration.generation_policy import (
    GENERATING_TIERS,
    CurrentFacts,
    Focus,
    Generation,
    PolicyDecision,
    Trigger,
    decide_for_event,
    decide_for_expiry,
    decide_for_promotion,
    decide_for_turn,
    is_still_current,
)
from backend.orchestration.generation_scheduler import GenerationScheduler
from backend.orchestration.observations import (
    EVENT_GENERATION_SUPPRESSED,
    FALLBACK_USED,
    GENERATION_SUPPRESSED,
    MISSING_PROFILE,
    MODEL_CALL_FAILED,
    MODEL_CALL_TIMED_OUT,
    NO_WORLD_STATE,
    TRIGGER_SUPPRESSED,
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
    fallback: FallbackLibrary
    attempts: ProviderAttempts
    commands: CommandStore
    publisher: BehaviourPublisher
    telemetry: TelemetryPort
    observations: Observations
    clock: Clock
    radii: EventRadii
    command_lifetime_ms: int
    characters_per_token: int

    # ---- triggers -----------------------------------------------------------------

    async def on_triggered_turn(self, turn: ConversationTurn) -> None:
        """Route the current world state for this turn and queue at most one generation."""
        snapshot = self.world_state.latest_for_session(turn.session_id)
        if snapshot is None:
            self.observations.note(
                NO_WORLD_STATE, session_id=turn.session_id, turn_id=turn.turn_id
            )
            await self.conversation.note_not_generated(turn.session_id)
            return

        try:
            outcome = await self._route(turn.session_id, snapshot)
        except StorageUnavailable as unavailable:
            await self._suppress_turn(
                turn, f"routing state is unavailable: {unavailable}"
            )
            return

        decision = decide_for_turn(turn, outcome)
        if decision.generation is None:
            await self._suppress_turn(turn, str(decision.suppressed))
            return

        await self.scheduler.submit(decision.generation)

    async def on_event_revision(self, event: GameEvent) -> None:
        """Queue a reaction for each eligible NPC, or invalidate what this revision ends."""
        if event.is_terminal:
            # The revision that ends an event also ends every reaction still waiting for it.
            await self.scheduler.cancel(
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
            await self.scheduler.submit(generation)

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
        await self.scheduler.cancel(
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
            if await self._acted_on(
                promotion, outcome.session_id, assignment.npc_id, "promotion"
            ):
                continue
            expiry = decide_for_expiry(assignment, outcome, focus, behaviour, now_ms)
            await self._acted_on(expiry, outcome.session_id, assignment.npc_id, "expiry")

    async def _acted_on(
        self, decision: PolicyDecision, session_id: str, npc_id: str, trigger: str
    ) -> bool:
        """Queue what the decision produced, or say why an eligible trigger produced nothing.

        A decision that is neither is the ordinary case — most candidates are neither promoted
        nor expired on any given snapshot — and reporting it would drown the record.
        """
        if decision.generation is not None:
            await self.scheduler.submit(decision.generation)
            return True
        if decision.suppressed is not None:
            self.observations.note(
                TRIGGER_SUPPRESSED,
                session_id=session_id,
                npc_id=npc_id,
                trigger=trigger,
                reason=decision.suppressed,
            )
            return True
        return False

    # ---- executor -----------------------------------------------------------------

    async def is_current(self, work: Generation) -> str | None:
        """Gather what the stores say about this work, and let policy judge it."""
        outcome = self.handoff.latest_outcome(work.session_id, work.world_id)
        routed = outcome is not None and outcome.status is RoutingStatus.ROUTED
        stored = (
            await self.events.latest(work.session_id, work.event.event_id)
            if routed and work.event is not None
            else None
        )
        return is_still_current(
            work,
            CurrentFacts(
                routed=routed,
                assignment=(
                    next(
                        (one for one in outcome.assignments if one.npc_id == work.npc_id),
                        None,
                    )
                    if routed and outcome is not None
                    else None
                ),
                stored_event=stored.event if stored is not None else None,
                latest_behaviour=(
                    await self.commands.latest_for(work.session_id, work.npc_id)
                    if routed and work.trigger is Trigger.EXPIRY
                    else None
                ),
            ),
        )

    async def generate(self, work: Generation) -> BehaviourCommand | None:
        """Call the provider at most once and turn the outcome into a command.

        The attempt is committed before the call and closed after it, so a process that dies in
        between leaves the outcome visibly unknown rather than looking untried. A timeout or a
        failure is answered from the fallback library instead of by calling again — the attempt
        is spent either way, and this ticket exists to stop it being spent twice.
        """
        snapshot = self.world_state.latest_for_session(work.session_id)
        if snapshot is None:
            self.observations.note(
                NO_WORLD_STATE, session_id=work.session_id, npc_id=work.npc_id
            )
            return None

        request = await self._request_for(work, snapshot)
        started_at_ms = self.clock.now_ms()
        await self.attempts.open(
            work.claim_key,
            _attempt_record(request, self.gateway.identity_for(request.tier)),
            started_at_ms,
        )

        try:
            behaviour = await self.gateway.generate(request)
        except NoProviderForTier as misrouted:
            # Nothing was called, so there is no model call to report and nothing to answer.
            await self.attempts.close(work.claim_key, FAILED)
            self.observations.note(
                MODEL_CALL_FAILED, request_id=request.request_id, reason=repr(misrouted)
            )
            return None
        except Exception as failure:
            await self.attempts.close(work.claim_key, FAILED)
            self._record_failure(request, started_at_ms, failure)
            return await self._fallback_command(request)

        await self.attempts.close(work.claim_key, SUCCEEDED)
        self.telemetry.record_model_call(
            _fact(request, behaviour, started_at_ms, self.clock.now_ms())
        )
        return await self._command(request, behaviour)

    async def fallback_for(self, request: GenerationRequest) -> BehaviourCommand:
        """The command a spent-but-unanswered attempt produces, for restart recovery."""
        return await self._fallback_command(request)

    async def _fallback_command(self, request: GenerationRequest) -> BehaviourCommand:
        behaviour = self.fallback.behaviour(request, self.characters_per_token)
        self.observations.note(
            FALLBACK_USED,
            request_id=request.request_id,
            npc_id=request.npc_id,
            source=behaviour.model,
        )
        return await self._command(request, behaviour)

    async def publish(self, command: BehaviourCommand) -> None:
        """Commit and send one command, and let an answered conversation move on.

        Whether it moves is `conversation.note_command_outcome`'s to decide, so this path and
        restart recovery cannot answer the question differently.
        """
        delivered = await self.publisher.publish(command)
        await self.conversation.note_command_outcome(command, delivered)

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

    async def note_turn_not_generated(self, session_id: str) -> None:
        await self.conversation.note_not_generated(session_id)

    # ---- internals ----------------------------------------------------------------

    async def _route(self, session_id: str, snapshot: WorldSnapshot) -> RoutingOutcome:
        """Route this world state now, because the decision below needs its tiers.

        Enrichment awaits, so the outcome can answer a newer sequence than the one that went in.
        That is the current routing for this session either way, which is what the decision
        needs; losing the race is not a reason to suppress the trigger.
        """
        return await self.handoff.route_now(
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
        conversation_id: str | None,
        turn_id: str | None,
        event_id: str | None,
    ) -> GenerationRequest:
        """The claim key already says what makes this work distinct, so the request identity is
        derived from it: a retry of the same work reuses it, and different work cannot collide.

        Deriving it from the trigger's own identifiers instead is not enough. Two promotions of
        the same NPC off the same event revision differ only by the snapshot that promoted them,
        which appears in the claim key and nowhere else.
        """
        return GenerationRequest(
            request_id=f"request-{identity_digest(work.claim_key)}",
            session_id=work.session_id,
            npc_id=work.npc_id,
            npc_name=context.npc.name,
            tier=work.tier,
            trigger_kind=context.trigger_kind,
            conversation_id=conversation_id,
            turn_id=turn_id,
            event_id=event_id,
            source_sequence=work.source_sequence,
            prompt=renderer_for(context)(context),
            trigger_text=context.trigger_text,
            estimated_input_tokens=context.estimated_input_tokens,
            output_token_limit=context.output_token_limit,
            trigger=work.trigger.value,
            event_type=work.event.event_type if work.event is not None else None,
            roles=work.roles,
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
        """A spent attempt is always reported, whether it failed or ran out of time.

        The provider and model are named. `docs/message_schemas.md` §7 allows them to be null
        only when a request "fails before selection", which a timeout is not: the provider was
        chosen and called. The team's handoff contract §23 shows both populated on a timeout,
        and Elson & Daniel's timeout rate is per provider, so nulling them would hide the call.

        `fallback_used` is true because this failure is always followed by fallback content —
        handoff contract §21.9 asks for exactly that pairing.
        """
        timed_out = isinstance(failure, ProviderTimeout)
        self.observations.note(
            MODEL_CALL_TIMED_OUT if timed_out else MODEL_CALL_FAILED,
            request_id=request.request_id,
            npc_id=request.npc_id,
            reason=repr(failure),
        )
        identity = self.gateway.identity_for(request.tier)
        self.telemetry.record_model_call(
            ModelCallFact(
                session_id=request.session_id,
                request_id=request.request_id,
                npc_id=request.npc_id,
                tier=request.tier.value,
                provider=identity.provider if identity else None,
                model=identity.model if identity else None,
                event_id=request.event_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                source_sequence=request.source_sequence,
                started_at_ms=started_at_ms,
                completed_at_ms=self.clock.now_ms(),
                input_tokens=request.estimated_input_tokens,
                output_tokens=0,
                status=STATUS_ERROR,
                fallback_used=True,
                error_code=error_code_for(failure),
            )
        )

    async def _suppress_turn(self, turn: ConversationTurn, reason: str) -> None:
        self.observations.note(
            GENERATION_SUPPRESSED,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            npc_id=turn.target_npc_id,
            reason=reason,
        )
        await self.conversation.note_not_generated(turn.session_id)

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


def _attempt_record(
    request: GenerationRequest, identity: ProviderIdentity | None
) -> dict[str, Any]:
    """Everything recovery needs to answer this request without repeating it.

    The prompt is deliberately absent: recovery never calls a provider, so the one thing it
    cannot use is the only large field. What it keeps is what chooses fallback content and what
    stamps the resulting command, and who the spent call was waiting on.

    The trigger kind is kept even though nothing in recovery reads it, because the alternative
    is assuming one on the way back and recording that an event reaction was something the
    player said.
    """
    return {
        "provider": identity.provider if identity else None,
        "model": identity.model if identity else None,
        "request_id": request.request_id,
        "session_id": request.session_id,
        "npc_id": request.npc_id,
        "npc_name": request.npc_name,
        "tier": request.tier.value,
        "trigger_kind": request.trigger_kind.value,
        "conversation_id": request.conversation_id,
        "turn_id": request.turn_id,
        "event_id": request.event_id,
        "source_sequence": request.source_sequence,
        "trigger_text": request.trigger_text,
        "estimated_input_tokens": request.estimated_input_tokens,
        "output_token_limit": request.output_token_limit,
        "trigger": request.trigger,
        "event_type": request.event_type,
        "roles": list(request.roles),
    }


def request_from_record(record: Mapping[str, Any]) -> GenerationRequest:
    """Rebuild the request an unresolved attempt was made for.

    The prompt comes back empty because it was never stored: recovery cannot call a provider,
    so there is nothing left that would read it.
    """
    return GenerationRequest(
        request_id=record["request_id"],
        session_id=record["session_id"],
        npc_id=record["npc_id"],
        npc_name=record["npc_name"],
        tier=AttentionTier(record["tier"]),
        trigger_kind=TriggerKind(record["trigger_kind"]),
        conversation_id=record["conversation_id"],
        turn_id=record["turn_id"],
        event_id=record["event_id"],
        source_sequence=record["source_sequence"],
        prompt="",
        trigger_text=record["trigger_text"],
        estimated_input_tokens=record["estimated_input_tokens"],
        output_token_limit=record["output_token_limit"],
        trigger=record["trigger"],
        event_type=record["event_type"],
        roles=tuple(record["roles"]),
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
