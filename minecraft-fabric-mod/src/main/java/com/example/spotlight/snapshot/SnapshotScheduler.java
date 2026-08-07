package com.example.spotlight.snapshot;

import com.example.DynamicLlmLod;
import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.transport.HttpPublisher;
import com.google.gson.JsonObject;
import net.minecraft.server.level.ServerPlayer;

import java.util.UUID;
import java.util.Map;
import net.minecraft.world.entity.npc.Villager;

/**
 * Tick-based scheduler that triggers snapshot builds and publishes them
 * at regular intervals.
 */
public class SnapshotScheduler {
    private final CandidateTracker candidateTracker;
    private final HttpPublisher httpPublisher;
    private int tickCounter = 0;

    public SnapshotScheduler(CandidateTracker candidateTracker, HttpPublisher httpPublisher) {
        this.candidateTracker = candidateTracker;
        this.httpPublisher = httpPublisher;
    }

    /**
     * Called every server tick. Updates candidates and periodically publishes snapshots.
     */
    public void tick(ServerPlayer player) {
        tickCounter++;

        // Update candidate tracking every tick for responsiveness
        candidateTracker.update(player);

        // Publish snapshot at the configured interval
        if (tickCounter >= SpotlightConfig.SNAPSHOT_INTERVAL_TICKS) {
            tickCounter = 0;
            publishSnapshot(player);
        }
    }

    private void publishSnapshot(ServerPlayer player) {
        Map<UUID, Villager> candidates = candidateTracker.getCandidates();
        try {
            // Empty candidate sets are meaningful: the backend still needs current player state
            // and must be able to clear NPCs that appeared in the previous snapshot.
            JsonObject snapshot = SnapshotBuilder.build(player, candidates);
            httpPublisher.publish(snapshot);
        } catch (Exception e) {
            DynamicLlmLod.LOGGER.debug("Failed to build snapshot: {}", e.getMessage());
        }
    }

    public void reset() {
        tickCounter = 0;
    }
}
