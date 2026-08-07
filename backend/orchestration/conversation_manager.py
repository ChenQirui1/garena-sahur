"""Manage IDLE, ENGAGED, AWAITING_RESPONSE and READY states.

Owner: Jerome & Richard

The world snapshot is the only authority for which conversation is active: a turn may arm
generation but never opens, switches, or closes a conversation. A turn that arrives before its
snapshot is therefore held in one slot per session and resolved by the next snapshot, so the
wait is bounded by arrivals rather than by a wall clock and stays reproducible in tests.

The router-facing `state` string is a projection of the machine below, not the machine itself
(`CONTEXT.md`). No source states the mapping, so the one here is a backend decision recorded
for coordination issue #3.

State is written through to `conversation_store` as it changes and restored on startup, so a
restart in the middle of an exchange resumes it. Mutations are asynchronous because they commit;
reads stay synchronous against the in-memory copy, which is what every caller already holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.ingestion.message_validation import (
    ActiveConversationRef,
    ConversationTurn,
    WorldSnapshot,
)
from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.conversation_store import ConversationStore
from backend.orchestration.router_port import ActiveConversation, RouterConversationState


class ConversationState(StrEnum):
    IDLE = "idle"
    ENGAGED = "engaged"
    AWAITING_RESPONSE = "awaiting_response"
    READY = "ready"


ROUTER_STATE_FOR: dict[ConversationState, RouterConversationState] = {
    ConversationState.ENGAGED: "engaged",
    ConversationState.AWAITING_RESPONSE: "awaiting_npc",
    ConversationState.READY: "engaged",
}


@dataclass(frozen=True, slots=True)
class TurnAdmission:
    """What the caller must act on after the conversation state moved."""

    triggered: ConversationTurn | None = None
    discarded: ConversationTurn | None = None


@dataclass(slots=True)
class _Session:
    active: ActiveConversationRef | None = None
    state: ConversationState = ConversationState.IDLE
    unconfirmed: ConversationTurn | None = None


class ConversationManager:
    """One active conversation per session, with every inactive history still retained."""

    def __init__(self, store: ConversationStore) -> None:
        self._store = store
        self._sessions: dict[str, _Session] = {}
        self._started_at_ms: dict[tuple[str, str], int] = {}
        self._latest_turn_id: dict[tuple[str, str], str] = {}

    async def restore(self) -> None:
        """Load every session's state back from the database, replacing what is in memory."""
        self._sessions = {}
        self._started_at_ms = {}
        self._latest_turn_id = {}
        for thread in await self._store.threads():
            key = (thread.session_id, thread.conversation_id)
            self._started_at_ms[key] = thread.started_at_ms
            if thread.latest_turn_id is not None:
                self._latest_turn_id[key] = thread.latest_turn_id
        for stored in await self._store.sessions():
            self._sessions[stored.session_id] = _Session(
                active=(
                    None
                    if stored.active_conversation_id is None
                    or stored.active_target_npc_id is None
                    else ActiveConversationRef(
                        conversation_id=stored.active_conversation_id,
                        target_npc_id=stored.active_target_npc_id,
                    )
                ),
                state=ConversationState(stored.state),
                unconfirmed=stored.unconfirmed_turn,
            )

    def forget(self, session_id: str) -> None:
        """Drop one session's in-memory copy, after its durable rows have been removed."""
        self._sessions.pop(session_id, None)
        for key in [key for key in self._started_at_ms if key[0] == session_id]:
            self._started_at_ms.pop(key, None)
            self._latest_turn_id.pop(key, None)

    async def observe_snapshot(self, snapshot: WorldSnapshot) -> TurnAdmission:
        """Apply Minecraft's authoritative view, then resolve any turn waiting on it."""
        session = self._session(snapshot.session_id)
        reference = snapshot.active_conversation
        previous = session.active

        session.active = reference
        if reference is None:
            session.state = ConversationState.IDLE
        else:
            key = (snapshot.session_id, reference.conversation_id)
            self._started_at_ms.setdefault(key, snapshot.timestamp_ms)
            if _is_another_conversation(previous, reference) or (
                session.state is ConversationState.IDLE
            ):
                session.state = ConversationState.ENGAGED
            await self._store.save_thread(
                snapshot.session_id,
                reference.conversation_id,
                self._started_at_ms[key],
                self._latest_turn_id.get(key),
            )

        admission = self._resolve_unconfirmed(session)
        await self._save(snapshot.session_id, session)
        return admission

    async def admit_turn(self, turn: ConversationTurn) -> TurnAdmission:
        """Record a stored turn against conversation state and report what it armed."""
        session = self._session(turn.session_id)
        key = (turn.session_id, turn.conversation_id)
        self._latest_turn_id[key] = turn.turn_id
        await self._store.save_thread(
            turn.session_id,
            turn.conversation_id,
            self._started_at_ms.setdefault(key, turn.timestamp_ms),
            turn.turn_id,
        )

        if not turn.is_player_turn:
            return TurnAdmission()

        if _addresses(session.active, turn):
            session.state = ConversationState.AWAITING_RESPONSE
            admission = TurnAdmission(triggered=turn)
        else:
            waiting, session.unconfirmed = session.unconfirmed, turn
            admission = TurnAdmission(discarded=waiting)

        await self._save(turn.session_id, session)
        return admission

    def state(self, session_id: str) -> ConversationState:
        return self._session(session_id).state

    def active_conversation(self, session_id: str) -> ActiveConversationRef | None:
        """Minecraft's current view, whatever the orchestration state happens to be."""
        return self._session(session_id).active

    def latest_turn_id(self, session_id: str, conversation_id: str) -> str | None:
        """The newest turn seen in one conversation, so older pending work can tell it is old."""
        return self._latest_turn_id.get((session_id, conversation_id))

    def projection(self, session_id: str) -> ActiveConversation | None:
        """The router-facing conversation object, or ``None`` when the session is idle."""
        session = self._session(session_id)
        reference = session.active
        if reference is None or session.state is ConversationState.IDLE:
            return None
        return ActiveConversation(
            conversation_id=reference.conversation_id,
            target_npc_id=reference.target_npc_id,
            state=ROUTER_STATE_FOR[session.state],
            started_at_ms=self._started_at_ms[(session_id, reference.conversation_id)],
            latest_turn_id=self._latest_turn_id.get(
                (session_id, reference.conversation_id)
            ),
        )

    async def note_command_outcome(
        self, command: BehaviourCommand, delivered: bool
    ) -> None:
        """Move the conversation for one published command, or leave it where it was.

        A command carrying a turn identity is the answer to that turn, whatever queued it: a
        promotion that finds the player still waiting answers them exactly as the turn itself
        would have. Deciding from the trigger instead would make the same command move the
        conversation only on the restart path, where the trigger is no longer known.

        A command that never reached Minecraft leaves the conversation where it was, rather than
        claiming an answer arrived. Neither branch regenerates.
        """
        if command.turn_id is None:
            return
        if delivered:
            await self.note_published(command.session_id)
        else:
            await self.note_not_generated(command.session_id)

    async def note_published(self, session_id: str) -> None:
        session = self._session(session_id)
        session.state = (
            ConversationState.READY if session.active else ConversationState.IDLE
        )
        await self._save(session_id, session)

    async def note_not_generated(self, session_id: str) -> None:
        """Leave AWAITING_RESPONSE when the armed turn produced no command after all."""
        session = self._session(session_id)
        if session.state is not ConversationState.AWAITING_RESPONSE:
            return
        session.state = (
            ConversationState.ENGAGED if session.active else ConversationState.IDLE
        )
        await self._save(session_id, session)

    def _session(self, session_id: str) -> _Session:
        return self._sessions.setdefault(session_id, _Session())

    async def _save(self, session_id: str, session: _Session) -> None:
        await self._store.save_session(
            session_id=session_id,
            state=session.state.value,
            active_conversation_id=(
                session.active.conversation_id if session.active else None
            ),
            active_target_npc_id=(
                session.active.target_npc_id if session.active else None
            ),
            unconfirmed_turn=session.unconfirmed,
        )

    def _resolve_unconfirmed(self, session: _Session) -> TurnAdmission:
        waiting = session.unconfirmed
        if waiting is None:
            return TurnAdmission()

        session.unconfirmed = None
        if _addresses(session.active, waiting):
            session.state = ConversationState.AWAITING_RESPONSE
            return TurnAdmission(triggered=waiting)
        return TurnAdmission(discarded=waiting)


def _is_another_conversation(
    previous: ActiveConversationRef | None, reference: ActiveConversationRef
) -> bool:
    return previous is None or (
        previous.conversation_id,
        previous.target_npc_id,
    ) != (reference.conversation_id, reference.target_npc_id)


def _addresses(active: ActiveConversationRef | None, turn: ConversationTurn) -> bool:
    return active is not None and (active.conversation_id, active.target_npc_id) == (
        turn.conversation_id,
        turn.target_npc_id,
    )
