# Spotlight Message Schemas

This document is the canonical schema-version `1.0` contract shared by the Minecraft,
backend, Router, and telemetry portions of Spotlight.

**Owners:** Elson, Daniel, Jerome, Richard, and Ivan.

Any incompatible field rename, removal, type change, or semantic change requires agreement
from all three ownership groups. Additive optional fields are permitted within `1.0` when an
older consumer can safely ignore them.

## Contract overview

| Boundary | Topic or interface | Delivery rule |
|---|---|---|
| Minecraft → backend | `world.snapshot` | High frequency; latest accepted sequence wins |
| Minecraft → backend | `game.event` | Durable; deduplicate delivery by `message_id` and order lifecycle by `event_revision` |
| Minecraft → backend | `conversation.turn` | Durable; deduplicate by `turn_id` |
| Backend → Router | `router.route(routing_snapshot)` | Direct in-process Python call; not a Pub/Sub topic |
| Router → backend | `routing_result` | One complete assignment set for the input snapshot |
| Backend → Minecraft | `behaviour.command` | Ordered, expiring command; reject stale or irrelevant work |
| Backend/Router → observability | `telemetry.record` | Append-only structured record |
| Router → Minecraft/dashboard | `routing.assignment` | Optional outward projection of a validated routing result |

`npc.profile` is not an MVP transport topic. Static profiles are loaded by the backend from
`data/npc_profiles.json` and are never republished on every snapshot.

## Common conventions

- `schema_version` is the string `"1.0"`.
- Timestamps and expiries use non-negative Unix epoch milliseconds.
- IDs are stable, non-empty strings. Minecraft NPC IDs are stable UUIDs or deterministic IDs.
- Positions use Minecraft world coordinates in blocks: `{x, y, z}`.
- Distances ending in `_blocks` are non-negative numbers measured in Minecraft blocks.
- Normalized scores and relevance values are numbers in the inclusive range `0.0`–`1.0`.
- Transport payloads reject unknown fields unless a later additive-contract decision says
  otherwise.
- Topic names use dots; payload discriminators use underscores.

During local development, JSONL and HTTP use this envelope:

```json
{
  "topic": "world.snapshot",
  "message": {
    "schema_version": "1.0",
    "message_type": "world_snapshot"
  }
}
```

The envelope is a transport adapter. The message inside it is the canonical payload.

---

## 1. `world.snapshot`

One publication contains the player and every NPC currently selected by Minecraft's radius
policy. It is not one message per NPC.

```json
{
  "schema_version": "1.0",
  "message_type": "world_snapshot",
  "session_id": "demo-01",
  "world_id": "minecraft-overworld-market",
  "sequence": 1842,
  "timestamp_ms": 1786208500123,
  "candidate_policy": {
    "entry_radius_blocks": 24.0,
    "exit_radius_blocks": 28.0
  },
  "player": {
    "player_id": "player-uuid",
    "position": {"x": 105.2, "y": 64.0, "z": -31.8},
    "look_direction": {"x": 0.72, "y": -0.05, "z": 0.69}
  },
  "active_conversation": {
    "conversation_id": "conversation-07",
    "target_npc_id": "shopkeeper-uuid"
  },
  "candidate_count": 2,
  "npcs": [
    {
      "npc_id": "shopkeeper-uuid",
      "position": {"x": 108.1, "y": 64.0, "z": -30.2},
      "world_distance_blocks": 3.4,
      "viewport_center_distance": 0.07,
      "inside_viewport": true,
      "line_of_sight": true
    },
    {
      "npc_id": "thief-uuid",
      "position": {"x": 112.4, "y": 64.0, "z": -35.1},
      "world_distance_blocks": 11.2,
      "viewport_center_distance": 0.42,
      "inside_viewport": true,
      "line_of_sight": true
    }
  ],
  "attention_edges": [
    {
      "source_npc_id": "shopkeeper-uuid",
      "target_npc_id": "thief-uuid",
      "kind": "gaze",
      "active": true
    }
  ]
}
```

### Required fields and validation

| Field | Type | Rule |
|---|---|---|
| `message_type` | string | Exactly `world_snapshot` |
| `session_id`, `world_id` | string | Identify the run and Minecraft world |
| `sequence` | integer | Non-negative and monotonically increasing per session/world |
| `timestamp_ms` | integer | Non-negative source timestamp |
| `candidate_policy.entry_radius_blocks` | number | Non-negative; agreed demo value is `24.0` |
| `candidate_policy.exit_radius_blocks` | number | Greater than entry radius; agreed demo value is `28.0` |
| `player` | object | Stable ID, position, and look direction |
| `active_conversation` | object or null | Target must appear in `npcs` when present |
| `candidate_count` | integer | Must equal `npcs.length` |
| `npcs` | array | Unique `npc_id` values; contains raw Minecraft observations only |
| `attention_edges` | array | Defaults to empty; endpoints must reference candidate NPC IDs |

`event_relevance`, `event_roles`, `interaction_recency`, and final tier scores do not belong in
this message. Jerome and Richard derive those values before the Router call.

Consumers may discard duplicate or older sequences. Receiving a snapshot alone must not cause
an LLM request.

---

## 2. `game.event`

A game event is durable and revisioned. Each payload is the complete current state of that
event revision.

```json
{
  "schema_version": "1.0",
  "message_type": "game_event",
  "session_id": "demo-01",
  "message_id": "event-message-001",
  "event_id": "market-theft-001",
  "event_revision": 1,
  "timestamp_ms": 1786208495000,
  "event_type": "market_theft",
  "status": "started",
  "position": {"x": 104.2, "y": 64.0, "z": -31.8},
  "actor_npc_ids": ["thief-uuid"],
  "target_npc_ids": ["shopkeeper-uuid"],
  "responder_npc_ids": ["guard-uuid"]
}
```

### Required fields and validation

| Field | Type | Rule |
|---|---|---|
| `message_id` | string | Unique delivery identity; duplicate deliveries are ignored |
| `event_id` | string | Stable identity across all revisions of one event |
| `event_revision` | integer | Starts at `1`; only a higher revision advances stored state |
| `event_type` | string | Stable machine-readable event kind |
| `status` | string | `started`, `updated`, `ended`, or `cancelled` |
| `position` | vector | Event location in world coordinates |
| role arrays | string arrays | May be empty; an NPC may hold more than one role |

`ended` and `cancelled` are terminal states. Events are not silently dropped, even when world
snapshots are being coalesced.

---

## 3. `conversation.turn`

```json
{
  "schema_version": "1.0",
  "message_type": "conversation_turn",
  "session_id": "demo-01",
  "conversation_id": "conversation-07",
  "turn_id": "turn-004",
  "turn_index": 4,
  "timestamp_ms": 1786208500200,
  "speaker_type": "player",
  "speaker_id": "player-uuid",
  "target_npc_id": "shopkeeper-uuid",
  "text": "Which direction did the thief run?"
}
```

### Required fields and validation

| Field | Type | Rule |
|---|---|---|
| `conversation_id` | string | Stable for one conversation |
| `turn_id` | string | Globally unique durable-turn identity |
| `turn_index` | integer | Non-negative order within the conversation |
| `speaker_type` | string | `player` identifies a player utterance that may trigger work |
| `speaker_id`, `target_npc_id` | string | Stable participant IDs |
| `text` | string | May be empty only when the integration intentionally represents a non-verbal turn |

One `turn_id` may create at most one generation request. Delivery retries must reuse the same
`turn_id`.

---

## 4. Backend-to-Router `routing_snapshot`

This is an in-process data contract, not a transport message. Jerome and Richard build it from
the latest world snapshot, durable events, conversation state, and interaction history, then
call the persistent Router directly.

```json
{
  "schema_version": "1.0",
  "snapshot_type": "routing_snapshot",
  "session_id": "demo-01",
  "world_id": "minecraft-overworld-market",
  "sequence": 1842,
  "timestamp_ms": 1786208500123,
  "candidate_policy": {
    "entry_radius_blocks": 24.0,
    "exit_radius_blocks": 28.0
  },
  "active_event_ids": ["market-theft-001"],
  "active_conversation": {
    "conversation_id": "conversation-07",
    "target_npc_id": "shopkeeper-uuid",
    "state": "engaged",
    "started_at_ms": 1786208485000,
    "latest_turn_id": "turn-004"
  },
  "candidate_count": 2,
  "npcs": [
    {
      "npc_id": "shopkeeper-uuid",
      "world_distance_blocks": 3.4,
      "viewport_center_distance": 0.07,
      "inside_viewport": true,
      "line_of_sight": true,
      "event_relevance": 1.0,
      "event_roles": ["target"],
      "interaction_recency": 0.8
    },
    {
      "npc_id": "thief-uuid",
      "world_distance_blocks": 11.2,
      "viewport_center_distance": 0.42,
      "inside_viewport": true,
      "line_of_sight": true,
      "event_relevance": 1.0,
      "event_roles": ["actor"],
      "interaction_recency": 0.0
    }
  ],
  "attention_edges": []
}
```

The structure remains stable when there are no events, conversation, or edges: use empty arrays
and `null`, not shape-changing omissions. Conversation state is one of `engaged`,
`awaiting_player`, `awaiting_npc`, or `ending`.

---

## 5. Router `routing_result`

```json
{
  "schema_version": "1.0",
  "result_type": "routing_result",
  "session_id": "demo-01",
  "world_id": "minecraft-overworld-market",
  "sequence": 1842,
  "timestamp_ms": 1786208500123,
  "assignments": [
    {
      "npc_id": "shopkeeper-uuid",
      "tier": "focused",
      "previous_tier": "reactive",
      "changed": true,
      "direct_score": 10.91,
      "propagated_score": 0.0,
      "final_score": 10.91,
      "reasons": [
        "active conversation",
        "direct event target",
        "selected within Focused capacity"
      ]
    },
    {
      "npc_id": "thief-uuid",
      "tier": "ambient",
      "previous_tier": null,
      "changed": false,
      "direct_score": 0.37,
      "propagated_score": 0.0,
      "final_score": 0.37,
      "reasons": ["outside Focused and Reactive selection"]
    }
  ],
  "counts": {"focused": 1, "reactive": 0, "ambient": 1},
  "diagnostics": {
    "focused_capacity": 2,
    "reactive_capacity": 6,
    "candidate_count": 2,
    "routing_time_ms": 0.31
  }
}
```

### Result invariants

- Every candidate appears exactly once and no non-candidate appears.
- `tier` is exactly `focused`, `reactive`, or `ambient`.
- `changed` indicates a transition from a known previous tier. A first observation may use
  `previous_tier: null` with `changed: false`.
- Counts sum to `candidate_count`.
- Focused count never exceeds `focused_capacity`; Reactive count never exceeds
  `reactive_capacity`.
- The result session, world, sequence, and timestamp correspond to the input snapshot.
- Ties are deterministic: active conversation, final score, previous higher tier, shorter world
  distance, then stable NPC ID.

If the optional `routing.assignment` topic is enabled, it publishes this validated decision or
per-NPC projections of its assignments. It does not contain dialogue.

---

## 6. `behaviour.command`

```json
{
  "schema_version": "1.0",
  "message_type": "behaviour_command",
  "session_id": "demo-01",
  "command_id": "command-322",
  "request_id": "request-0091",
  "npc_id": "shopkeeper-uuid",
  "tier": "focused",
  "event_id": "market-theft-001",
  "conversation_id": "conversation-07",
  "turn_id": "turn-004",
  "source_sequence": 1842,
  "created_at_ms": 1786208500984,
  "expires_at_ms": 1786208515000,
  "dialogue": "Towards the fountain! He was carrying my bread.",
  "action": "point_towards_fountain",
  "fallback_used": false
}
```

`event_id`, `conversation_id`, `turn_id`, `dialogue`, and `action` may be `null` when not
applicable, but at least one executable output (`dialogue` or `action`) must be present.
`expires_at_ms` must be greater than `created_at_ms`.

Minecraft applies a command only when the NPC exists, the command is newer than the last
accepted command for that NPC, its trigger is still current, and it has not expired.

The current backend also emits a provisional integer `command_sequence` used to order multiple
commands for one NPC. It remains an extension until the shared ordering decision is finalized.

---

## 7. `telemetry.record`

### Model-call record

```json
{
  "schema_version": "1.0",
  "record_type": "model_call",
  "session_id": "demo-01",
  "request_id": "request-0091",
  "npc_id": "shopkeeper-uuid",
  "tier": "focused",
  "provider": "openai",
  "model": "focused-model-name",
  "event_id": "market-theft-001",
  "conversation_id": "conversation-07",
  "turn_id": "turn-004",
  "source_sequence": 1842,
  "started_at_ms": 1786208500300,
  "completed_at_ms": 1786208500984,
  "latency_ms": 684,
  "input_tokens": 231,
  "output_tokens": 34,
  "status": "success",
  "fallback_used": false,
  "error_code": null
}
```

`provider` and `model` may be `null` if a request fails before selection. Token counts and
latency are non-negative. `status` is `success` or `error`; errors carry a stable
machine-readable `error_code` when available.

### Routing record

A routing record contains the routing-result identity plus, per NPC, direct, propagated, and
final scores, previous and new tiers, the changed flag, reasons, and routing duration. The same
append-only telemetry records feed the live dashboard, JSONL/CSV logs, and benchmark charts.
Projected baseline cost must be labelled separately from measured routed cost.

---

## 8. Backend-local NPC profiles

Profiles are loaded from `data/npc_profiles.json`. Each profile includes:

```json
{
  "npc_id": "shopkeeper-uuid",
  "name": "Mira",
  "role": "market bread seller",
  "persona": "Authored persona text",
  "speaking_style": "Warm and quick",
  "relationships": [
    {"npc_id": "guard-uuid", "relation": "relies on"}
  ]
}
```

Profiles are authored configuration, not high-frequency world state. Never place secrets or
provider credentials in profile or message payloads.

## Compatibility checklist

Before merging a contract change:

1. Update this document and the boundary models together.
2. Confirm all three ownership groups understand the change.
3. Preserve `1.0` compatibility or introduce a new schema version.
4. Add or update validation and integration tests.
5. Keep mock-mode payloads identical to live-integration payloads.
