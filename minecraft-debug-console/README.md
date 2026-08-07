# Spotlight mock subscriber

`mock_subscriber.py` is a local FastAPI mock backend with a live terminal dashboard. It receives
Minecraft snapshots, events, and conversation turns, and exposes the WebSocket endpoint used by
the mod for commands.

For plain-language definitions of every message, field, and project term, see
[Data Dictionary and Glossary](DATA_DICTIONARY.md).

## Install and run

```bash
cd minecraft-debug-console
python -m pip install -r requirements.txt
python mock_subscriber.py
```

The mod is already configured for `http://localhost:8000` and
`ws://localhost:8000/api/v1/ws`. Start the subscriber, launch Minecraft, then run
`/spotlight connect` if the WebSocket is not connected automatically.

Available endpoints:

- `POST /api/v1/messages` receives either a prototype bare message or a canonical
  `{ "topic": "...", "message": {...} }` envelope.
- `GET /health` reports receiver status and counts.
- `GET /api/v1/candidates` returns the latest complete candidate IDs for local testing.
- `WS /api/v1/ws?session_id=...` accepts the mod's command connection.
- `POST /api/v1/commands/{session_id}` sends a JSON command to every connected mod client for that
session.

## Test an NPC speech bubble

With Minecraft running and the WebSocket connected, execute:

```powershell
.\send-dialogue.ps1 -Message "Hello from Spotlight!"
```

The script uses the nearest NPC in the latest snapshot, creates fresh command IDs and timestamps,
and sends the command before its 15-second expiry. To target a particular current candidate, add
`-NpcId "<full-villager-uuid>"`. If local script execution is disabled, use:

```powershell
powershell -ExecutionPolicy Bypass -File .\send-dialogue.ps1 -Message "Hello from Spotlight!"
```

Example command delivery:

```bash
curl -X POST http://localhost:8000/api/v1/commands/minecraft-spotlight-001 \
  -H "Content-Type: application/json" \
  -d @command.json
```

The dashboard displays up to 20 current NPC candidates and the eight most recent events,
conversation turns, and log entries. Uvicorn access logging is disabled so request logs do not
overwrite the live display.
