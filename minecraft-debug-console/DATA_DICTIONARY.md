# Spotlight Data Dictionary and Glossary

This document explains the data exchanged between Minecraft and the backend. It is intended for
people reading logs, testing the demo, or integrating components—not only for developers.

## Message flow

| Direction | Message | Purpose | Delivery |
|---|---|---|---|
| Minecraft → backend | `world_snapshot` | Current player and nearby NPC observations | Frequent; latest value wins |
| Minecraft → backend | `game_event` | A meaningful world occurrence, such as theft or attack | Durable event |
| Minecraft → backend | `conversation_turn` | One player or NPC utterance | Durable turn |
| Backend → Minecraft | `behaviour_command` | Dialogue or an action for one NPC | Ordered, expiring command |

The local development endpoint accepts messages at `POST /api/v1/messages`. Commands return to
Minecraft through `/api/v1/ws` during local development. Production may use NATS without changing
the meaning of the messages.

The console accepts both a bare message and this development envelope:

```json
{
  "topic": "world.snapshot",
  "message": {"message_type": "world_snapshot"}
}
```

## `world_snapshot`

A point-in-time observation. It is normally sent every 4 game ticks (about 200 ms or 5 Hz at 20 TPS),
including when no NPCs are candidates.

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | Identifies one running Minecraft/backend session |
| `sequence` | integer | Increasing snapshot number within the session |
| `timestamp_ms` / `timestamp` | integer | Unix time in milliseconds when the snapshot was built |
| `player` | object | Current player identity, position, and look direction |
| `npcs` | array | Current candidate NPC observations; may be empty |
| `candidate_count` | integer | Canonical form only; must equal the length of `npcs` |
| `active_conversation` | object or null | Conversation currently involving the player |

### Player

| Field | Type | Meaning |
|---|---|---|
| `player_id` / `uuid` | string | Stable player identifier |
| `name` | string | Display name when available |
| `position` | `{x, y, z}` | World coordinates in blocks |
| `look_direction` | `{x, y, z}` | Canonical unit vector pointing from the camera |
| `look` | `{yaw, pitch}` | Prototype camera rotation in degrees |
| `held_item` | string or absent | Item in the player's main hand |

### NPC observation

| Field | Type | Meaning |
|---|---|---|
| `npc_id` / `uuid` | string | Stable NPC identifier; Minecraft uses a UUID |
| `name` | string | Custom name or readable profession name |
| `profession` | string | Villager profession |
| `level` | integer | Villager profession level |
| `position` | `{x, y, z}` | Current world coordinates |
| `world_distance_blocks` / `distance` | number | Distance from player to NPC in blocks |
| `viewport_center_distance` | number | Normalized `0..1`: `0` at the look centre and `1` at or beyond the configured view cone |
| `inside_viewport` | boolean | Whether the NPC is inside the configured camera field of view |
| `line_of_sight` | boolean | Whether solid blocks obstruct the ray between player and NPC |
| `health`, `max_health` | number | Current and maximum health |
| `activity` | string | Simple state such as `idle`, `walking`, `sleeping`, or `trading` |

`line_of_sight` does **not** mean “on screen.” A villager behind the player can have
`line_of_sight=true` and `inside_viewport=false`. For visible on-screen, both values must be true.

The mod computes these geometry fields from the current server tick before sending the snapshot.
The prototype viewport is a 55-degree half-angle attention cone, not a pixel-accurate projection
of the player's selected Minecraft FOV and aspect ratio. An off-screen NPC is represented by
`viewport_center_distance=1` together with `inside_viewport=false`.

## `game_event`

A durable, revisioned description of something important in the world.

| Field | Type | Meaning |
|---|---|---|
| `message_id` | string | Unique delivery identity used for deduplication |
| `event_id` | string | Stable identity shared by every revision of one event |
| `event_revision` | integer | Increasing revision number, starting at `1` |
| `timestamp_ms` / `timestamp` | integer | Event observation time in Unix milliseconds |
| `event_type` | string | Machine-readable kind, such as `market_theft` or `villager_attacked` |
| `status` | string | `started`, `updated`, `ended`, or `cancelled` |
| `position` | `{x, y, z}` | Event location |
| `actor_npc_ids` | string array | NPCs performing the event action |
| `target_npc_ids` | string array | NPCs affected by the event |
| `responder_npc_ids` | string array | NPCs expected to respond |
| `details` | string or absent | Prototype free-text information |

`ended` and `cancelled` are terminal: the event is no longer current.

The mod automatically publishes `villager_attacked` when a player attacks a villager. Other demo
events can currently be published with `/spotlight event <event_type> <actor_uuid> [target_uuid]`.

## `conversation_turn`

One ordered utterance in an active conversation.

| Field | Type | Meaning |
|---|---|---|
| `conversation_id` | string | Stable identity for the whole conversation |
| `turn_id` | string | Unique identity for this turn and its retries |
| `turn_index` / `turn_number` | integer | Position of the turn within the conversation |
| `timestamp_ms` / `timestamp` | integer | Turn time in Unix milliseconds |
| `speaker_type` / `speaker` | string | Speaker category, normally `player` or `npc` |
| `speaker_id` / `speaker_name` | string | Stable identity or readable name of the speaker |
| `target_npc_id` / `npc_uuid` | string | NPC being addressed |
| `text` / `message` | string | Spoken content |

## `behaviour_command`

An instruction generated by the backend for one NPC. Minecraft validates it again immediately
before applying it.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Contract version; currently `1.0` |
| `session_id` | string | Session that may apply the command |
| `command_id` | string | Stable retry identity; the same ID is never applied twice |
| `request_id` | string | Generation request that produced the command |
| `npc_id` | string | Target NPC UUID |
| `tier` | string | Attention tier used when generating the behaviour |
| `event_id` | string or null | Event that triggered the command |
| `conversation_id` | string or null | Conversation that triggered the command |
| `turn_id` | string or null | Specific conversation turn that triggered it |
| `source_sequence` | integer | Snapshot sequence used as generation context; not command order |
| `command_sequence` | integer | Increasing command order per session and NPC |
| `created_at_ms` | integer | Backend creation time |
| `expires_at_ms` | integer | Command is rejected when current time is at or past this value |
| `dialogue` | string or null | Text displayed temporarily in a speech bubble above the NPC |
| `action` | object or null | Optional executable action and payload |
| `fallback_used` | boolean | Whether fallback generation produced the output |

Supported actions are:

| Action | Payload | Meaning |
|---|---|---|
| `walk_to` | `{x, y, z, speed?}` | Walk toward world coordinates; speed defaults to `0.5` |
| `look_at` | `{x, y, z}` | Turn the NPC's gaze toward world coordinates |
| `look_at_player` | `{}` | Look at the nearest player within 64 blocks |
| `stop` | `{}` | Stop navigation and clear the current walk target |

A command may contain dialogue, an action, or both. Minecraft rejects stale, expired, duplicate,
incorrectly ordered, or no-longer-relevant commands.

## Glossary

| Term | Plain-language definition |
|---|---|
| Candidate NPC | Villager currently close enough to be considered by the system |
| Entry radius | Distance at which a new NPC enters the candidate set; currently 24 blocks |
| Exit radius | Larger distance at which an existing candidate leaves; currently 28 blocks |
| Hysteresis | Different entry/exit limits that prevent NPCs flickering at one boundary |
| Viewport | The camera's configured visible field of view |
| Viewport centre distance | Normalized angular distance from the centre of the camera |
| Line of sight (LOS) | No solid block obstructs the player-to-NPC ray; facing direction is irrelevant |
| On screen | `inside_viewport=true` and `line_of_sight=true` |
| Snapshot | Replaceable current state; newer snapshots supersede older ones |
| Durable message | Event or turn that should survive retries and must not be silently lost |
| Sequence | Increasing integer used to reject older state or commands |
| Trigger | Event, conversation, or turn that caused behaviour generation |
| Current trigger | Trigger is still active and has not been replaced or ended |
| Idempotent | Repeating the same command ID has no additional effect |
| Expiry / TTL | Time limit after which a command must not be applied; currently at most 15 seconds |
| WebSocket | Persistent local-development connection used to send commands to Minecraft |
| NATS | Production message broker that transports messages between components |
| Broker acknowledgement | NATS confirms transport/storage; it does not prove Minecraft applied a command |
| Application acknowledgement | Explicit confirmation that Minecraft applied a command; not required for MVP |
| Speech bubble | Temporary client-rendered dialogue above an NPC; it does not replace the villager's custom name |

The speech-bubble presentation is inspired by
[Notable Bubble Text (NBT)](https://modrinth.com/mod/nbt) by Mrbysco (MIT License). The Fabric
implementation in this repository is independent and does not bundle NBT source code.
