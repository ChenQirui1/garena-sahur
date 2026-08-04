# Mock Publisher

A self-contained stand-in for the **upstream / Minecraft side** of Spotlight
(Ivan's `minecraft-mod/pubsub_client`). It publishes the information the
**downstream Python backend** needs so that ingestion → router → orchestration
can be run and tested **without a live game or a broker**.

It emits the four upstream topics exactly as agreed in
[`docs/team-architecture.md` §8 — Shared Message Contracts](../docs/team-architecture.md#8-shared-message-contracts):

| Topic | Message | Semantics |
|---|---|---|
| `npc.profile` | `npc_profile` | Published once per NPC on startup |
| `world.snapshot` | `world_snapshot` | High-frequency, latest-value-wins |
| `game.event` | `game_event` | Durable, dedup by `event_id` |
| `conversation.turn` | `conversation_turn` | Durable, dedup by `turn_id` |

It deliberately does **not** produce `routing.assignment`, `behaviour.command`
or `telemetry.record` — those are the downstream output that consumes this feed.

## Requirements

Python 3.8+ standard library only. No broker, no `pip install`.

## Usage

```bash
cd mock-publisher

# 10-NPC market-theft demo, streamed as JSONL to the terminal
python3 publish.py

# 50 NPCs, faster, saved to a replayable file
python3 publish.py --npcs 50 --rate 20 --out run.jsonl

# emit as fast as possible into a live FastAPI ingestion endpoint
python3 publish.py --no-sleep --out http://localhost:8000/ingest

# fully reproducible stream for benchmarking (pin seed + base time)
python3 publish.py --npcs 100 --seed 7 --epoch-ms 1786208500000 --no-sleep --out bench-100.jsonl
```

Each line on stdout / in a file is one envelope:

```json
{"topic": "world.snapshot", "message": { "type": "world_snapshot", "...": "..." }}
```

The run summary is printed to **stderr**, so a piped stdout stays clean JSONL.

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--npcs` | `10` | Crowd size (benchmark points: 10 / 25 / 50 / 100) |
| `--rate` | `5` | World snapshots per second |
| `--duration` | `20` | Scenario length in seconds |
| `--session` | `demo-01` | `session_id` stamped on snapshots |
| `--seed` | `7` | RNG seed for a repeatable stream |
| `--out` | `-` | `-` stdout, a file path (JSONL), or an `http(s)://` endpoint |
| `--no-sleep` | off | Emit as fast as possible (ignore `--rate` pacing) |
| `--epoch-ms` | wall clock | Pin the base timestamp for a byte-identical stream |

## The scenario

The `market_theft` scenario reproduces the worked example from the docs and
scales the crowd to any size:

1. On startup, every NPC's `npc.profile` is published.
2. World snapshots stream at `--rate`. The shopkeeper sits near the viewport
   centre; the rest of the crowd is spread across the square.
3. At ~30% of the run a `game.event` (`market_theft`) fires — the thief starts
   running and event relevance rises for the shopkeeper and thief.
4. At ~42% and ~58% the player asks the shopkeeper `conversation.turn`s, which
   flips the shopkeeper into `active_conversation`.

This gives the downstream router a reason to promote the shopkeeper to
**Focused**, keep bystanders **Ambient**, and exercise event/turn deduplication.

## Feeding the downstream

- **File / stdin replay:** point a subscriber at the JSONL, or pipe it:
  `python3 publish.py | your_subscriber`.
- **HTTP:** `--out http://…` POSTs each envelope; the endpoint dispatches on
  `topic`. (Add such an endpoint in `backend/ingestion/subscriber.py` when the
  backend is wired up.)

## Files

| File | Purpose |
|---|---|
| `publish.py` | CLI + publish loop |
| `scenario.py` | The market-theft state machine and NPC roster |
| `contracts.py` | Message builders matching §8 + a required-field guard |
| `sinks.py` | stdout / file / HTTP sinks and the JSONL envelope |

## Note on `npc.profile`

§8 names the `npc.profile` topic but does not pin a full field-level schema.
This publisher uses the proposed shape
`{type, npc_id, name, role, persona, relationships}`. Per the shared-contract
rule, confirm this with the other groups before the integration firms up, and
record the agreed fields back in `docs/message_schemas.md`.
