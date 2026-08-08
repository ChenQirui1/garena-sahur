"""Pick the pipeline back up after a restart, without paying for anything twice.

Owner: Jerome & Richard

Two kinds of unfinished business survive a crash, and each has exactly one safe answer.

An **unresolved provider attempt** is a call that was committed and never closed, so its outcome
is genuinely unknown: the provider may have answered into a process that no longer exists. Asking
again would be a second call for one trigger, which is the thing this ticket exists to prevent,
so the work is answered from the fallback library instead.

An **unpublished command** is a generated result that was stored and never delivered. Its bytes
are already committed, so it is re-sent exactly as it was rather than regenerated — and if its
15-second lifetime has passed while the process was down, it expires instead.

Recovery only ever reads, publishes, and closes. It deletes nothing: erasing durable evidence is
an explicit operation (`session_cleanup`), never a side effect of starting up.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models.model_gateway import GenerationRequest
from backend.orchestration.behaviour_publisher import BehaviourPublisher
from backend.orchestration.clock import Clock
from backend.orchestration.command_store import CommandStore
from backend.orchestration.conversation_manager import ConversationManager
from backend.orchestration.deduplication import FAILED, ProviderAttempts
from backend.orchestration.generation_coordinator import (
    GenerationCoordinator,
    request_from_record,
)
from backend.orchestration.observations import (
    PROVIDER_OUTCOME_UNKNOWN,
    RECOVERED_COMMAND_REPUBLISHED,
    Observations,
)
from backend.orchestration.telemetry_port import (
    STATUS_ERROR,
    ModelCallFact,
    TelemetryPort,
)

# `docs/message_schemas.md` §7 requires a "stable machine-readable" `error_code` but lists no
# vocabulary. The codes the backend already uses (`model_gateway.ERROR_CODE_FOR`) have none for
# an outcome lost with the process, so this follows their upper-snake convention rather than
# inventing a new style. It is the one genuinely new code in the backend, and #3 owns
# confirming it.
UNKNOWN_OUTCOME_ERROR_CODE = "UNKNOWN_PROVIDER_OUTCOME"


@dataclass(frozen=True, slots=True)
class Recovered:
    """What starting up found waiting, so a caller can assert on it."""

    answered_attempts: int = 0
    republished_commands: int = 0


class Recovery:
    def __init__(
        self,
        attempts: ProviderAttempts,
        commands: CommandStore,
        conversation: ConversationManager,
        generation: GenerationCoordinator,
        publisher: BehaviourPublisher,
        telemetry: TelemetryPort,
        observations: Observations,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._commands = commands
        self._conversation = conversation
        self._generation = generation
        self._publisher = publisher
        self._telemetry = telemetry
        self._observations = observations
        self._clock = clock
        # What the last run found, kept here so the service lifecycle can start recovery
        # without the pipeline having to become mutable to hold its result. Public: it is
        # this service's outcome-inspection interface, exercised today only by tests.
        self.last = Recovered()

    async def run(self) -> Recovered:
        """Restore conversation state, then finish what the previous process started."""
        await self._conversation.restore()
        answered = await self._answer_unresolved_attempts()
        republished = await self._republish_stored_commands()
        self.last = Recovered(
            answered_attempts=answered, republished_commands=republished
        )
        return self.last

    async def _answer_unresolved_attempts(self) -> int:
        answered = 0
        for attempt in await self._attempts.unresolved():
            request = request_from_record(attempt.request)
            self._observations.note(
                PROVIDER_OUTCOME_UNKNOWN,
                session_id=attempt.session_id,
                npc_id=attempt.npc_id,
                request_id=attempt.request_id,
            )
            self._telemetry.record_model_call(
                _unknown_outcome_fact(
                    attempt.started_at_ms,
                    self._clock.now_ms(),
                    request,
                    provider=attempt.request.get("provider"),
                    model=attempt.request.get("model"),
                )
            )
            # Closing before publishing means a crash during recovery cannot answer the same
            # attempt twice; the stored command is then what the next start finds.
            await self._attempts.close(attempt.claim_key, FAILED)
            command = await self._generation.fallback_for(request)
            delivered = await self._publisher.publish(command)
            await self._conversation.note_command_outcome(command, delivered)
            answered += 1
        return answered

    async def _republish_stored_commands(self) -> int:
        republished = 0
        for stored in await self._commands.unpublished():
            self._observations.note(
                RECOVERED_COMMAND_REPUBLISHED,
                session_id=stored.command.session_id,
                npc_id=stored.command.npc_id,
                command_id=stored.command.command_id,
            )
            delivered = await self._publisher.deliver(stored)
            # Delivering a stored command, or letting it expire, moves the conversation exactly
            # as it would have before the restart, because the same predicate decides both.
            # Neither branch generates anything.
            await self._conversation.note_command_outcome(stored.command, delivered)
            republished += 1
        return republished


def _unknown_outcome_fact(
    started_at_ms: int,
    completed_at_ms: int,
    request: GenerationRequest,
    provider: str | None,
    model: str | None,
) -> ModelCallFact:
    """The spent attempt, reported once by the process that found it unresolved.

    The provider and model come from the attempt row, because the call really was made and
    Elson & Daniel count attempts per provider. They are `null` only when the previous process
    had not selected one — the case §7 is explicit that null is for.
    """
    return ModelCallFact(
        session_id=request.session_id,
        request_id=request.request_id,
        npc_id=request.npc_id,
        tier=request.tier.value,
        provider=provider,
        model=model,
        event_id=request.event_id,
        conversation_id=request.conversation_id,
        turn_id=request.turn_id,
        source_sequence=request.source_sequence,
        started_at_ms=started_at_ms,
        completed_at_ms=completed_at_ms,
        input_tokens=request.estimated_input_tokens,
        output_tokens=0,
        status=STATUS_ERROR,
        fallback_used=True,
        error_code=UNKNOWN_OUTCOME_ERROR_CODE,
    )
