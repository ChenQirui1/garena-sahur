# Mock Publisher

A self-contained stand-in for Ivan's Minecraft/pub-sub client. It feeds the downstream Python
backend so ingestion, routing, and orchestration can be exercised without a live game or broker.

It emits the three canonical upstream topics from
[Message Schemas](../docs/message_schemas.md):

| Topic | Message | Semantics |
|---|---|---|
| `world.snapshot` | `world_snapshot` | Batched, high-frequency, latest value wins |
| `game.event` | `game_event` | Durable, deduplicated by `message_id`, revisioned by `event_id` |
| `conversation.turn` | `conversation_turn` | Durable, deduplicated by `turn_id` |

Static NPC profiles are loaded by the backend from `data/npc_profiles.json`; the mock publisher
does not emit `npc.profile`.

## Requirements

Python 3.8+ standard library only. No broker or additional package is needed.

## Usage

```bash
cd mock-publisher

# 10-NPC market-theft demo streamed as JSONL
python publish.py

# 50 candidates saved to a replayable file
python publish.py --npcs 50 --rate 20 --out run.jsonl

# Emit without pacing into the local FastAPI endpoint
python publish.py --no-sleep --out http://localhost:8000/ingest

# Reproducible benchmark stream
python publish.py --npcs 100 --seed 7 --epoch-ms 1786208500000 --no-sleep --out bench-100.jsonl
```

Each JSONL line uses the development transport envelope:

```json
{
  "topic": "world.snapshot",
  "message": {
    "schema_version": "1.0",
    "message_type": "world_snapshot"
  }
}
```

The summary is printed to stderr so stdout remains valid JSONL.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--npcs` | `10` | Starting candidate crowd; benchmark points are 10 / 25 / 50 / 100 |
| `--rate` | `5` | World snapshots per second |
| `--duration` | `20` | Scenario length in seconds |
| `--session` | `demo-01` | Session ID stamped on every message |
| `--seed` | `7` | Seed for a repeatable stream |
| `--out` | `-` | stdout, JSONL path, or an HTTP(S) endpoint |
| `--no-sleep` | off | Disable real-time pacing |
| `--epoch-ms` | wall clock | Pin the starting timestamp |

## Scenario

1. Radius-selected world snapshots begin with the configured crowd size.
2. Candidate hysteresis uses a 24-block entry radius and 28-block exit radius.
3. At approximately 30% of the run, a revision-1 market-theft event starts.
4. At approximately 42% and 58%, the player sends two durable conversation turns to the
   shopkeeper.
5. The shopkeeper appears as the snapshot's active conversation target after the first turn.

The publisher sends only raw Minecraft observations. Event relevance, interaction recency,
scores, and tiers are derived downstream.

## Files

| File | Purpose |
|---|---|
| `publish.py` | CLI and publish loop |
| `scenario.py` | Deterministic market-theft state machine |
| `contracts.py` | Canonical message builders and lightweight validation |
| `sinks.py` | stdout, file, and HTTP sinks |
