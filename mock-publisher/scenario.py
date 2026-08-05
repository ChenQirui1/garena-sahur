"""Deterministic market-theft messages using Spotlight's canonical contracts.

The scenario scales to the 10 / 25 / 50 / 100 benchmark points. A fixed seed and
``--epoch-ms`` produce a byte-identical stream.
"""

from __future__ import annotations

import random

import contracts

SESSION_DEFAULT = "demo-01"
WORLD_DEFAULT = "minecraft-overworld-market"
CONVERSATION_ID = "conversation-07"
EVENT_ID = "market-theft-001"
PLAYER_ID = "player-uuid"

SHOPKEEPER = "shopkeeper-uuid"
THIEF = "thief-uuid"
GUARD = "guard-uuid"

ENTRY_RADIUS_BLOCKS = 24.0
EXIT_RADIUS_BLOCKS = 28.0
PLAYER_POSITION = contracts.vector3(105.2, 64.0, -31.8)
PLAYER_LOOK_DIRECTION = contracts.vector3(1.0, 0.0, 0.0)


class MarketTheftScenario:
    def __init__(
        self,
        npcs=10,
        seed=7,
        session_id=SESSION_DEFAULT,
        world_id=WORLD_DEFAULT,
    ):
        if npcs < 2:
            raise ValueError("scenario needs at least 2 NPCs (shopkeeper + thief)")
        self.session_id = session_id
        self.world_id = world_id
        self.rng = random.Random(seed)
        self.roster = self._build_roster(npcs)
        self._candidate_ids = {npc["npc_id"] for npc in self.roster}

        self.theft_fired = False
        self.conversation_active = False

    def _build_roster(self, npcs):
        roster = [
            {"npc_id": SHOPKEEPER, "base_world": 3.4, "base_viewport": 0.07},
            {"npc_id": THIEF, "base_world": 5.0, "base_viewport": 0.30},
        ]
        if npcs >= 3:
            roster.append(
                {"npc_id": GUARD, "base_world": 6.5, "base_viewport": 0.50}
            )
        for index in range(len(roster), npcs):
            roster.append(
                {
                    "npc_id": f"villager-{index:03d}-uuid",
                    "base_world": self.rng.uniform(7.0, 23.0),
                    "base_viewport": self.rng.uniform(0.15, 0.98),
                }
            )
        return roster

    def snapshot(self, tick, sequence, timestamp_ms):
        """Build one radius-selected, batched ``world_snapshot``."""
        measured = []
        for npc in self.roster:
            jitter = self.rng.uniform(-0.15, 0.15)
            world_distance = max(0.5, npc["base_world"] + jitter)
            if npc["npc_id"] == THIEF and self.theft_fired:
                world_distance += tick * 0.25

            currently_selected = npc["npc_id"] in self._candidate_ids
            limit = EXIT_RADIUS_BLOCKS if currently_selected else ENTRY_RADIUS_BLOCKS
            if world_distance <= limit:
                self._candidate_ids.add(npc["npc_id"])
            else:
                self._candidate_ids.discard(npc["npc_id"])

            viewport_distance = min(
                1.0, max(0.0, npc["base_viewport"] + jitter * 0.05)
            )
            measured.append((npc, world_distance, viewport_distance))

        observations = []
        for npc, world_distance, viewport_distance in measured:
            if npc["npc_id"] not in self._candidate_ids:
                continue
            inside_viewport = viewport_distance < 0.90
            line_of_sight = inside_viewport and world_distance < EXIT_RADIUS_BLOCKS
            observations.append(
                contracts.npc_observation(
                    npc_id=npc["npc_id"],
                    position=contracts.vector3(
                        PLAYER_POSITION["x"] + world_distance,
                        PLAYER_POSITION["y"],
                        PLAYER_POSITION["z"],
                    ),
                    world_distance_blocks=world_distance,
                    viewport_center_distance=viewport_distance,
                    inside_viewport=inside_viewport,
                    line_of_sight=line_of_sight,
                )
            )

        active_conversation = None
        if self.conversation_active and SHOPKEEPER in self._candidate_ids:
            active_conversation = {
                "conversation_id": CONVERSATION_ID,
                "target_npc_id": SHOPKEEPER,
            }

        return contracts.world_snapshot(
            session_id=self.session_id,
            world_id=self.world_id,
            sequence=sequence,
            timestamp_ms=timestamp_ms,
            player={
                "player_id": PLAYER_ID,
                "position": PLAYER_POSITION,
                "look_direction": PLAYER_LOOK_DIRECTION,
            },
            active_conversation=active_conversation,
            npcs=observations,
            attention_edges=[],
            entry_radius_blocks=ENTRY_RADIUS_BLOCKS,
            exit_radius_blocks=EXIT_RADIUS_BLOCKS,
        )

    def scripted(self, tick, total_ticks, timestamp_ms):
        """Yield durable event and conversation messages due at this tick."""
        theft_at = max(1, int(total_ticks * 0.30))
        turn_one_at = max(2, int(total_ticks * 0.42))
        turn_two_at = max(3, int(total_ticks * 0.58))

        if tick == theft_at and not self.theft_fired:
            self.theft_fired = True
            yield contracts.TOPIC_EVENT, contracts.game_event(
                session_id=self.session_id,
                message_id="event-message-001",
                event_id=EVENT_ID,
                event_revision=1,
                timestamp_ms=timestamp_ms,
                event_type="market_theft",
                status="started",
                position=contracts.vector3(104.2, 64.0, -31.8),
                actor_npc_ids=[THIEF],
                target_npc_ids=[SHOPKEEPER],
                responder_npc_ids=[GUARD] if GUARD in self._candidate_ids else [],
            )

        if tick == turn_one_at:
            self.conversation_active = True
            yield contracts.TOPIC_TURN, contracts.conversation_turn(
                session_id=self.session_id,
                conversation_id=CONVERSATION_ID,
                turn_id="turn-004",
                turn_index=4,
                timestamp_ms=timestamp_ms,
                speaker_type="player",
                speaker_id=PLAYER_ID,
                target_npc_id=SHOPKEEPER,
                text="Which direction did the thief run?",
            )

        if tick == turn_two_at:
            yield contracts.TOPIC_TURN, contracts.conversation_turn(
                session_id=self.session_id,
                conversation_id=CONVERSATION_ID,
                turn_id="turn-005",
                turn_index=5,
                timestamp_ms=timestamp_ms,
                speaker_type="player",
                speaker_id=PLAYER_ID,
                target_npc_id=SHOPKEEPER,
                text="Should I chase him or fetch the guard?",
            )
