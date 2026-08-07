package com.example.spotlight.snapshot;

import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.SpotlightState;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.npc.Villager;
import net.minecraft.world.phys.AABB;

import java.util.*;

/**
 * Maintains a set of candidate villagers within hysteresis radii of the player.
 * Entry at ENTRY_RADIUS, exit at EXIT_RADIUS to prevent boundary flicker.
 */
public class CandidateTracker {
    private final Map<UUID, Villager> candidates = new LinkedHashMap<>();

    /**
     * Updates the candidate set based on the player's current position.
     * Should be called each snapshot tick.
     */
    public void update(ServerPlayer player) {
        ServerLevel level = player.serverLevel();

        // Scan for new villagers within ENTRY_RADIUS
        AABB scanBox = player.getBoundingBox().inflate(SpotlightConfig.EXIT_RADIUS);
        List<Villager> nearbyVillagers = level.getEntitiesOfClass(Villager.class, scanBox);

        Set<UUID> nearbyUuids = new HashSet<>();
        for (Villager villager : nearbyVillagers) {
            UUID uuid = villager.getUUID();
            nearbyUuids.add(uuid);
            double dist = player.distanceTo(villager);

            if (!candidates.containsKey(uuid) && dist <= SpotlightConfig.ENTRY_RADIUS) {
                // New candidate enters
                candidates.put(uuid, villager);
            } else if (candidates.containsKey(uuid)) {
                // Update reference (entity may have been re-loaded)
                candidates.put(uuid, villager);
            }
        }

        // Remove candidates that have left EXIT_RADIUS or are no longer in the level
        candidates.entrySet().removeIf(entry -> {
            Villager v = entry.getValue();
            if (!v.isAlive() || v.isRemoved()) return true;
            double dist = player.distanceTo(v);
            return dist > SpotlightConfig.EXIT_RADIUS;
        });

        SpotlightState.get().candidateCount = candidates.size();
    }

    /**
     * Returns an unmodifiable view of the current candidates.
     */
    public Map<UUID, Villager> getCandidates() {
        return Collections.unmodifiableMap(candidates);
    }

    /**
     * Looks up a villager by UUID from the current candidate set.
     */
    public Villager getByUuid(UUID uuid) {
        return candidates.get(uuid);
    }

    /**
     * Clears all tracked candidates.
     */
    public void clear() {
        candidates.clear();
        SpotlightState.get().candidateCount = 0;
    }
}
