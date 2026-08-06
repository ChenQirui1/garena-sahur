"""Manage IDLE, ENGAGED, AWAITING_RESPONSE and READY states.

Owner: Jerome & Richard

The world snapshot is the only authority for which conversation is active: a turn may arm
generation but never opens, switches, or closes a conversation. A turn that arrives before its
snapshot is therefore held in one slot per session and resolved by the next snapshot, so the
wait is bounded by arrivals rather than by a wall clock and stays reproducible in tests.

The router-facing `state` string is a projection of the machine below, not the machine itself
(`CONTEXT.md`). No source states the mapping, so the one here is a backend decision recorded
for coordination issue #3.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.ingestion.message_validation import (
    ActiveConversationRef,
    ConversationTurn,
    WorldSnapshot,
)
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

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._started_at_ms: dict[tuple[str, str], int] = {}
        self._latest_turn_id: dict[tuple[str, str], str] = {}

    def observe_snapshot(self, snapshot: WorldSnapshot) -> TurnAdmission:
        """Apply Minecraft's authoritative view, then resolve any turn waiting on it."""
        session = self._session(snapshot.session_id)
        reference = snapshot.active_conversation
        previous = session.active

        session.active = reference
        if reference is None:
            session.state = ConversationState.IDLE
        else:
            self._started_at_ms.setdefault(
                (snapshot.session_id, reference.conversation_id), snapshot.timestamp_ms
            )
            if _is_another_conversation(previous, reference) or (
                session.state is ConversationState.IDLE
            ):
                session.state = ConversationState.ENGAGED

        return self._resolve_unconfirmed(session)

    def admit_turn(self, turn: ConversationTurn) -> TurnAdmission:
        """Record a stored turn against conversation state and report what it armed."""
        session = self._session(turn.session_id)
        self._latest_turn_id[(turn.session_id, turn.conversation_id)] = turn.turn_id

        if not turn.is_player_turn:
            return TurnAdmission()
        if _addresses(session.active, turn):
            session.state = ConversationState.AWAITING_RESPONSE
            return TurnAdmission(triggered=turn)

        waiting, session.unconfirmed = session.unconfirmed, turn
        return TurnAdmission(discarded=waiting)

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

    def note_published(self, session_id: str) -> None:
        session = self._session(session_id)
        session.state = (
            ConversationState.READY if session.active else ConversationState.IDLE
        )

    def note_not_generated(self, session_id: str) -> None:
        """Leave AWAITING_RESPONSE when the armed turn produced no command after all."""
        session = self._session(session_id)
        if session.state is ConversationState.AWAITING_RESPONSE:
            session.state = (
                ConversationState.ENGAGED if session.active else ConversationState.IDLE
            )

    def _session(self, session_id: str) -> _Session:
        return self._sessions.setdefault(session_id, _Session())

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
