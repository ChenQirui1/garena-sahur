#!/usr/bin/env python3
"""Live CLI mock backend for the Spotlight Minecraft mod."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from collections import Counter, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from rich.align import Align
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _kind(message: dict[str, Any]) -> str:
    return str(message.get("message_type") or message.get("type") or "unknown")


def _timestamp(message: dict[str, Any]) -> int:
    return int(message.get("timestamp_ms") or message.get("timestamp") or 0)


def _short(value: Any, width: int = 12) -> str:
    text = "-" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _number(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _position(value: Any) -> str:
    if not isinstance(value, dict):
        return "-"
    return f"{_number(value.get('x'))}, {_number(value.get('y'))}, {_number(value.get('z'))}"


def unwrap_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Accept a canonical transport envelope or the prototype's bare JSON body."""
    if isinstance(payload.get("message"), dict):
        message = payload["message"]
        topic = str(payload.get("topic") or _kind(message))
        return topic, message
    return _kind(payload), payload


@dataclass
class DashboardState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    started_at: float = field(default_factory=time.time)
    player: dict[str, Any] = field(default_factory=dict)
    npcs: list[dict[str, Any]] = field(default_factory=list)
    active_conversation: dict[str, Any] | None = None
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=20))
    conversations: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=20))
    logs: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=40))
    counts: Counter[str] = field(default_factory=Counter)
    arrivals: deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=2000))
    last_sequence: int | None = None
    last_message_at: float | None = None
    last_snapshot_at: float | None = None
    websocket_sessions: Counter[str] = field(default_factory=Counter)
    last_http_warning_at: float = 0.0
    suppressed_http_warnings: int = 0

    def ingest(self, topic: str, message: dict[str, Any]) -> None:
        now = time.time()
        kind = _kind(message)
        with self.lock:
            self.counts[kind] += 1
            self.arrivals.append((now, kind))
            self.last_message_at = now
            sequence = message.get("sequence")
            if isinstance(sequence, int):
                self.last_sequence = sequence

            if kind == "world_snapshot":
                self.last_snapshot_at = now
                self.player = dict(message.get("player") or {})
                self.npcs = list(message.get("npcs") or [])
                self.active_conversation = message.get("active_conversation")
                self._log("SNAP", f"sequence={sequence or '-'} candidates={len(self.npcs)}")
            elif kind == "game_event":
                self.events.appendleft(dict(message))
                self._log(
                    "EVENT",
                    f"{message.get('event_type', 'unknown')} status={message.get('status', '-')} "
                    f"id={message.get('event_id', '-')}",
                )
            elif kind == "conversation_turn":
                self.conversations.appendleft(dict(message))
                speaker = message.get("speaker_name") or message.get("speaker_id") or message.get("speaker") or "?"
                text = message.get("text") or message.get("message") or ""
                self._log("CHAT", f"{speaker}: {text}")
            else:
                self._log("WARN", f"unknown message on {topic}: {kind}")

    def websocket_opened(self, session_id: str) -> None:
        with self.lock:
            self.websocket_sessions[session_id or "(none)"] += 1
            self._log("WS", f"connected session={session_id or '(none)'}")

    def websocket_closed(self, session_id: str) -> None:
        with self.lock:
            key = session_id or "(none)"
            self.websocket_sessions[key] -= 1
            if self.websocket_sessions[key] <= 0:
                del self.websocket_sessions[key]
            self._log("WS", f"disconnected session={key}")

    def note(self, level: str, message: str) -> None:
        with self.lock:
            self._log(level, message)

    def http_warning(self, message: str) -> None:
        """Keep a repeated bad publisher from filling every visible log row."""
        now = time.time()
        with self.lock:
            if now - self.last_http_warning_at < 5.0:
                self.suppressed_http_warnings += 1
                return
            suffix = ""
            if self.suppressed_http_warnings:
                suffix = f" ({self.suppressed_http_warnings} repeats suppressed)"
            self._log("WARN", message + suffix)
            self.last_http_warning_at = now
            self.suppressed_http_warnings = 0

    def rate(self, window_seconds: float = 5.0) -> float:
        cutoff = time.time() - window_seconds
        with self.lock:
            while self.arrivals and self.arrivals[0][0] < cutoff:
                self.arrivals.popleft()
            return len(self.arrivals) / window_seconds

    def _log(self, level: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.logs.appendleft((level, f"{stamp} {message}"))


class CommandHub:
    def __init__(self, state: DashboardState):
        self.state = state
        self._lock = asyncio.Lock()
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(session_id, set()).add(websocket)
        self.state.websocket_opened(session_id)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(session_id)
            if connections is not None:
                connections.discard(websocket)
                if not connections:
                    self._connections.pop(session_id, None)
        self.state.websocket_closed(session_id)

    async def broadcast(self, session_id: str, command: dict[str, Any]) -> int:
        encoded = json.dumps(command, separators=(",", ":"))
        async with self._lock:
            connections = list(self._connections.get(session_id, set()))
        delivered = 0
        for websocket in connections:
            try:
                await websocket.send_text(encoded)
                delivered += 1
            except Exception as error:  # the receive loop performs cleanup
                self.state.note("WARN", f"command delivery failed: {error}")
        return delivered


STATE = DashboardState()
HUB = CommandHub(STATE)
STOP_RENDERER = threading.Event()


def _status_panel(state: DashboardState) -> Panel:
    with state.lock:
        connected = sum(state.websocket_sessions.values())
        sessions = ", ".join(state.websocket_sessions) or "none"
        age = "never" if state.last_message_at is None else f"{time.time() - state.last_message_at:.1f}s ago"
        counts = "  ".join(f"{key}={value}" for key, value in sorted(state.counts.items())) or "no messages"
        body = Text()
        body.append("HTTP receiver ", style="bold cyan")
        body.append("ready")
        body.append("   WebSockets ", style="bold cyan")
        body.append(f"{connected} ({sessions})")
        body.append("   Rate ", style="bold cyan")
        body.append(f"{state.rate():.1f}/s")
        body.append("   Last ", style="bold cyan")
        body.append(age)
        body.append("   Sequence ", style="bold cyan")
        body.append(str(state.last_sequence or "-"))
        body.append("\n")
        body.append(counts, style="dim")
        return Panel(body, title="Spotlight Mock Backend", border_style="bright_blue")


def _player_panel(state: DashboardState) -> Panel:
    with state.lock:
        player = dict(state.player)
        conversation = dict(state.active_conversation) if state.active_conversation else None
    position = player.get("position")
    look = player.get("look") or player.get("look_direction") or {}
    grid = Table.grid(expand=True)
    grid.add_column(style="bold")
    grid.add_column()
    grid.add_column(style="bold")
    grid.add_column()
    grid.add_row("Player", str(player.get("name") or player.get("player_id") or player.get("uuid") or "-"),
                 "Position", _position(position))
    grid.add_row("Look", _position(look) if "x" in look else f"yaw={_number(look.get('yaw'))} pitch={_number(look.get('pitch'))}",
                 "Held", str(player.get("held_item") or "empty"))
    if conversation:
        grid.add_row("Conversation", _short(conversation.get("conversation_id"), 24),
                     "Target", _short(conversation.get("target_npc_id"), 24))
    return Panel(grid, title="Player", border_style="green")


def _npc_panel(state: DashboardState) -> Panel:
    with state.lock:
        npcs = list(state.npcs)
    table = Table(expand=True, show_lines=False, pad_edge=False)
    table.add_column("NPC", style="bold", no_wrap=True)
    table.add_column("ID", style="dim")
    table.add_column("Profession")
    table.add_column("Position")
    table.add_column("Dist", justify="right")
    table.add_column("View", justify="right")
    table.add_column("LOS", justify="center")
    table.add_column("HP", justify="right")
    table.add_column("Activity")
    for npc in npcs[:20]:
        npc_id = npc.get("npc_id") or npc.get("uuid")
        health = "-"
        if npc.get("health") is not None:
            health = f"{_number(npc.get('health'), 0)}/{_number(npc.get('max_health'), 0)}"
        distance = npc.get("world_distance_blocks", npc.get("distance"))
        table.add_row(
            _short(npc.get("name") or npc.get("profession") or "villager", 18),
            _short(npc_id),
            _short(npc.get("profession"), 14),
            _position(npc.get("position")),
            _number(distance),
            _number(npc.get("viewport_center_distance"), 2),
            "yes" if npc.get("line_of_sight") else "no",
            health,
            _short(npc.get("activity"), 12),
        )
    if len(npcs) > 20:
        table.caption = f"Showing 20 of {len(npcs)} candidates"
    elif not npcs:
        table.add_row("No candidates received", "-", "-", "-", "-", "-", "-", "-", "-")
    return Panel(table, title=f"NPC Candidates ({len(npcs)})", border_style="magenta")


def _event_panel(state: DashboardState) -> Panel:
    with state.lock:
        events = list(state.events)[:8]
    table = Table(expand=True, pad_edge=False)
    table.add_column("Time", width=10)
    table.add_column("Type", style="bold yellow")
    table.add_column("Status")
    table.add_column("Event ID")
    table.add_column("Actors / Target / Details")
    for event in events:
        timestamp = _timestamp(event)
        stamp = time.strftime("%H:%M:%S", time.localtime(timestamp / 1000)) if timestamp else "-"
        actors = event.get("actor_npc_ids") or event.get("actor_uuid") or "-"
        targets = event.get("target_npc_ids") or event.get("target_uuid") or "-"
        detail = event.get("details") or f"{actors} -> {targets}"
        table.add_row(stamp, str(event.get("event_type") or "unknown"), str(event.get("status") or "-"),
                      _short(event.get("event_id")), _short(detail, 42))
    if not events:
        table.add_row("-", "No events", "-", "-", "-")
    return Panel(table, title="Recent Events", border_style="yellow")


def _conversation_panel(state: DashboardState) -> Panel:
    with state.lock:
        turns = list(state.conversations)[:8]
    table = Table(expand=True, pad_edge=False)
    table.add_column("Turn", width=10)
    table.add_column("Speaker", width=18)
    table.add_column("NPC", width=14)
    table.add_column("Message")
    for turn in turns:
        speaker = turn.get("speaker_name") or turn.get("speaker_id") or turn.get("speaker") or "?"
        npc = turn.get("target_npc_id") or turn.get("npc_uuid")
        index = turn.get("turn_index", turn.get("turn_number", "-"))
        message = turn.get("text") or turn.get("message") or ""
        table.add_row(str(index), _short(speaker, 18), _short(npc), str(message))
    if not turns:
        table.add_row("-", "No conversation", "-", "-")
    return Panel(table, title="Conversation", border_style="cyan")


def _log_panel(state: DashboardState) -> Panel:
    colors = {"EVENT": "yellow", "CHAT": "cyan", "WARN": "red", "WS": "green", "CMD": "magenta"}
    with state.lock:
        logs = list(state.logs)[:8]
    lines = []
    for level, message in logs:
        line = Text()
        line.append(f"[{level:5}] ", style=f"bold {colors.get(level, 'dim')}")
        line.append(message)
        lines.append(line)
    return Panel(Group(*lines) if lines else Align.center("Waiting for Minecraft…"), title="Live Log", border_style="white")


def build_dashboard(state: DashboardState):
    return Group(
        _status_panel(state),
        _player_panel(state),
        _npc_panel(state),
        _event_panel(state),
        _conversation_panel(state),
        _log_panel(state),
    )


def render_dashboard() -> None:
    with Live(build_dashboard(STATE), screen=True, refresh_per_second=4) as live:
        while not STOP_RENDERER.wait(0.25):
            live.update(build_dashboard(STATE))


@asynccontextmanager
async def lifespan(_: FastAPI):
    STOP_RENDERER.clear()
    renderer = threading.Thread(target=render_dashboard, name="spotlight-dashboard", daemon=True)
    renderer.start()
    STATE.note("INFO", "server started; waiting for Minecraft")
    try:
        yield
    finally:
        STOP_RENDERER.set()
        renderer.join(timeout=1.0)


app = FastAPI(title="Spotlight Mock Subscriber", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    with STATE.lock:
        return {
            "status": "ok",
            "messages": sum(STATE.counts.values()),
            "candidate_count": len(STATE.npcs),
            "websocket_connections": sum(STATE.websocket_sessions.values()),
        }


@app.get("/api/v1/candidates")
async def current_candidates() -> dict[str, Any]:
    """Return the latest full candidate IDs for local command-testing tools."""
    with STATE.lock:
        age_ms = None
        if STATE.last_snapshot_at is not None:
            age_ms = max(0, round((time.time() - STATE.last_snapshot_at) * 1000))
        return {
            "candidate_count": len(STATE.npcs),
            "last_sequence": STATE.last_sequence,
            "snapshot_age_ms": age_ms,
            "websocket_connections": sum(STATE.websocket_sessions.values()),
            "candidates": [dict(npc) for npc in STATE.npcs],
        }


@app.post("/api/v1/messages")
async def receive_message(request: Request) -> Any:
    raw_body = await request.body()
    if len(raw_body) > 1_048_576:
        STATE.http_warning(f"rejected oversized HTTP message ({len(raw_body)} bytes)")
        return JSONResponse(status_code=413, content={"error": "message exceeds 1 MiB"})
    if not raw_body.strip():
        content_length = request.headers.get("content-length", "missing")
        transfer_encoding = request.headers.get("transfer-encoding", "missing")
        content_type = request.headers.get("content-type", "missing")
        STATE.http_warning(
            "empty HTTP body "
            f"(content-length={content_length}, transfer-encoding={transfer_encoding}, "
            f"content-type={content_type}, http={request.scope.get('http_version', '?')})"
        )
        return JSONResponse(status_code=400, content={"error": "empty request body"})
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        STATE.http_warning(f"invalid HTTP JSON ({len(raw_body)} bytes): {error}")
        return JSONResponse(status_code=400, content={"error": f"invalid JSON: {error}"})
    if not isinstance(payload, dict):
        STATE.http_warning(f"HTTP body must be an object, received {type(payload).__name__}")
        return JSONResponse(status_code=400, content={"error": "JSON body must be an object"})
    topic, message = unwrap_payload(payload)
    STATE.ingest(topic, message)
    return {"status": "accepted", "message_type": _kind(message)}


@app.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket, session_id: str = Query(default="")) -> None:
    await HUB.connect(session_id, websocket)
    try:
        while True:
            message = await websocket.receive_text()
            STATE.note("WS", f"received from Minecraft: {_short(message, 80)}")
    except WebSocketDisconnect:
        pass
    finally:
        await HUB.disconnect(session_id, websocket)


@app.post("/api/v1/commands/{session_id}")
async def send_command(session_id: str, command: dict[str, Any]) -> dict[str, Any]:
    delivered = await HUB.broadcast(session_id, command)
    if delivered == 0:
        raise HTTPException(status_code=409, detail=f"no WebSocket client for session {session_id!r}")
    STATE.note("CMD", f"sent command={command.get('command_id') or command.get('command')} session={session_id}")
    return {"status": "sent", "connections": delivered}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def require_websocket_transport() -> None:
    """Fail before Rich takes over the terminal when Uvicorn cannot upgrade WebSockets."""
    try:
        import websockets  # noqa: F401
    except ImportError as error:
        raise SystemExit(
            "Missing WebSocket transport. Install it with the same interpreter used here:\n"
            f"  {sys.executable} -m pip install 'websockets>=12,<16'"
        ) from error


def silence_uvicorn_terminal_logs() -> None:
    """Keep framework logs from drawing over Rich's alternate-screen dashboard."""
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logger.disabled = True


if __name__ == "__main__":
    require_websocket_transport()
    silence_uvicorn_terminal_logs()
    args = parse_args()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ws="websockets",
        log_config=None,
        log_level="critical",
        access_log=False,
    )
