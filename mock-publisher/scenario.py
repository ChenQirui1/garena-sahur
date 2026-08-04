"""Deterministic 'market theft' demo scenario.

Reproduces the worked example used throughout the docs (a thief steals bread,
the shopkeeper reacts, the player asks which way they ran) and scales the crowd
up to any NPC count for the 10 / 25 / 50 / 100 benchmark points.

The scenario is a small state machine driven tick-by-tick by the publisher:

    profiles()                       -> npc.profile messages (emit once, startup)
    snapshot(tick, seq, t_ms)        -> a world.snapshot for this tick
    scripted(tick, total_ticks)      -> any game.event / conversation.turn due now

All non-time values are derived from a seeded RNG, so a given (npcs, seed)
always produces identical message content — important for repeatable
benchmarks. Pin ``--epoch-ms`` as well for a fully byte-identical stream.
"""

from __future__ import annotations

import random

import contracts

SESSION_DEFAULT = "demo-01"
CONVERSATION_ID = "conversation-07"
EVENT_ID = "market-theft-001"

# Fixed principals; everyone else is a numbered villager in the crowd.
SHOPKEEPER = "shopkeeper-uuid"
THIEF = "thief-uuid"
GUARD = "guard-uuid"


class MarketTheftScenario:
    def __init__(self, npcs=10, seed=7, session_id=SESSION_DEFAULT):
        if npcs < 2:
            raise ValueError("scenario needs at least 2 NPCs (shopkeeper + thief)")
        self.session_id = session_id
        self.rng = random.Random(seed)
        self.roster = self._build_roster(npcs)

        # Scenario state, flipped by scripted() as the story advances.
        self.theft_fired = False
        self.conversation_active = False
        self._turns_emitted = 0

    # -- roster --------------------------------------------------------------

    def _build_roster(self, npcs):
        roster = [
            {"npc_id": SHOPKEEPER, "name": "Marta", "role": "shopkeeper"},
            {"npc_id": THIEF, "name": "Rennick", "role": "thief"},
        ]
        if npcs >= 3:
            roster.append({"npc_id": GUARD, "name": "Halvor", "role": "town_guard"})
        for i in range(len(roster), npcs):
            roster.append(
                {"npc_id": f"villager-{i:03d}-uuid", "name": f"Villager {i}", "role": "villager"}
            )

        # Static per-NPC spatial baseline (jittered later per tick).
        for npc in roster:
            if npc["npc_id"] == SHOPKEEPER:
                npc["base_world"], npc["base_viewport"] = 3.4, 0.07
            elif npc["npc_id"] == THIEF:
                npc["base_world"], npc["base_viewport"] = 5.0, 0.30
            elif npc["npc_id"] == GUARD:
                npc["base_world"], npc["base_viewport"] = 6.5, 0.50
            else:
                npc["base_world"] = self.rng.uniform(6.0, 40.0)
                npc["base_viewport"] = self.rng.uniform(0.35, 1.6)
        return roster

    # -- upstream messages ---------------------------------------------------

    def profiles(self):
        """One npc.profile per NPC, emitted on startup."""
        personas = {
            "shopkeeper": "Proud market baker; sharp-eyed and quick to raise the alarm.",
            "thief": "Desperate and fast; avoids eye contact and bolts when noticed.",
            "town_guard": "Dutiful and literal; responds to reported crimes near the square.",
            "villager": "Ordinary townsfolk going about the market day.",
        }
        relationships = {
            SHOPKEEPER: {"knows": [GUARD], "wary_of": [THIEF]},
            GUARD: {"protects": [SHOPKEEPER]},
        }
        for npc in self.roster:
            yield contracts.npc_profile(
                npc_id=npc["npc_id"],
                name=npc["name"],
                role=npc["role"],
                persona=personas.get(npc["role"], personas["villager"]),
                relationships=relationships.get(npc["npc_id"], {}),
            )

    def snapshot(self, tick, sequence, timestamp_ms):
        """Build one world.snapshot reflecting the current scenario state."""
        observations = []
        for npc in self.roster:
            npc_id = npc["npc_id"]
            jitter = self.rng.uniform(-0.15, 0.15)

            world = max(0.5, npc["base_world"] + jitter)
            viewport = max(0.01, npc["base_viewport"] + jitter * 0.05)

            # The thief runs away once the theft fires.
            if npc_id == THIEF and self.theft_fired:
                world += tick * 0.25

            visible = world < 30.0
            line_of_sight = visible and viewport < 1.2

            event_relevance = 0.0
            if self.theft_fired and npc_id in (SHOPKEEPER, THIEF):
                event_relevance = 1.0
            elif self.theft_fired and npc_id == GUARD:
                event_relevance = 0.6

            active = self.conversation_active and npc_id == SHOPKEEPER
            interaction_recency = 0.8 if active else max(0.0, 0.4 - tick * 0.01)

            observations.append(
                contracts.npc_observation(
                    npc_id=npc_id,
                    world_distance=world,
                    viewport_center_distance=viewport,
                    visible=visible,
                    line_of_sight=line_of_sight,
                    event_relevance=event_relevance,
                    interaction_recency=interaction_recency,
                    active_conversation=active,
                )
            )
        return contracts.world_snapshot(
            self.session_id, sequence, timestamp_ms, observations
        )

    def scripted(self, tick, total_ticks):
        """Yield any durable game.event / conversation.turn due at this tick.

        Timings are fractions of the run so the story stays coherent at any
        duration: theft at ~30%, then two conversation turns.
        """
        theft_at = max(1, int(total_ticks * 0.30))
        turn_one_at = max(2, int(total_ticks * 0.42))
        turn_two_at = max(3, int(total_ticks * 0.58))

        if tick == theft_at and not self.theft_fired:
            self.theft_fired = True
            yield contracts.TOPIC_EVENT, contracts.game_event(
                event_id=EVENT_ID,
                event_type="market_theft",
                summary="A thief stole bread from the market stall.",
                participants=[THIEF, SHOPKEEPER],
            )

        if tick == turn_one_at:
            self.conversation_active = True
            self._turns_emitted += 1
            yield contracts.TOPIC_TURN, contracts.conversation_turn(
                conversation_id=CONVERSATION_ID,
                turn_id="turn-004",
                npc_id=SHOPKEEPER,
                player_text="Which direction did the thief run?",
            )

        if tick == turn_two_at:
            self._turns_emitted += 1
            yield contracts.TOPIC_TURN, contracts.conversation_turn(
                conversation_id=CONVERSATION_ID,
                turn_id="turn-005",
                npc_id=SHOPKEEPER,
                player_text="Should I chase him or fetch the guard?",
            )
