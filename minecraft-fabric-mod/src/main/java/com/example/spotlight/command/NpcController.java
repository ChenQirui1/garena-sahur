package com.example.spotlight.command;

import com.example.DynamicLlmLod;
import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.speech.SpeechBubbleBroadcaster;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.ai.memory.MemoryModuleType;
import net.minecraft.world.entity.ai.memory.WalkTarget;
import net.minecraft.world.entity.npc.Villager;
import net.minecraft.world.phys.Vec3;

import java.util.*;

/**
 * Applies dialogue and actions to villagers.
 * Manages a tick-based expiry queue for auto-clearing dialogue name tags.
 */
public class NpcController {
    // Dialogue expiry: maps villager UUID to the tick at which dialogue should be cleared
    private final Map<UUID, Long> dialogueExpiry = new LinkedHashMap<>();
    private final SpeechBubbleBroadcaster speechBubbles;
    private long currentTick = 0;

    public NpcController(SpeechBubbleBroadcaster speechBubbles) {
        this.speechBubbles = speechBubbles;
    }

    /**
     * Sends dialogue to clients as a temporary speech bubble without changing the
     * villager's persistent custom name.
     */
    public void showDialogue(Villager villager, String dialogue) {
        long expiryTick = currentTick + SpotlightConfig.DIALOGUE_DISPLAY_TICKS;
        dialogueExpiry.put(villager.getUUID(), expiryTick);
        speechBubbles.show(villager, dialogue, SpotlightConfig.DIALOGUE_DISPLAY_TICKS);
        DynamicLlmLod.LOGGER.debug("NPC {} says: {}", villager.getUUID(), dialogue);
    }

    /**
     * Clears a villager's dialogue (custom name).
     */
    public void clearDialogue(Villager villager) {
        speechBubbles.clear(villager);
        dialogueExpiry.remove(villager.getUUID());
    }

    /**
     * Commands a villager to walk towards a target position.
     */
    public void walkTo(Villager villager, Vec3 target, float speed) {
        villager.getBrain().setMemory(
                MemoryModuleType.WALK_TARGET,
                new WalkTarget(target, speed, 1)
        );
        DynamicLlmLod.LOGGER.debug("NPC {} walking to {}", villager.getUUID(), target);
    }

    /**
     * Commands a villager to look at a target position.
     */
    public void lookAt(Villager villager, Vec3 target) {
        villager.getLookControl().setLookAt(target.x, target.y, target.z);
        DynamicLlmLod.LOGGER.debug("NPC {} looking at {}", villager.getUUID(), target);
    }

    /** Stops navigation without changing the villager's dialogue. */
    public void stop(Villager villager) {
        villager.getNavigation().stop();
        villager.getBrain().eraseMemory(MemoryModuleType.WALK_TARGET);
        DynamicLlmLod.LOGGER.debug("NPC {} stopped", villager.getUUID());
    }

    /**
     * Must be called every server tick to process dialogue expiry.
     */
    public void tick(ServerLevel level) {
        currentTick++;

        if (dialogueExpiry.isEmpty()) return;

        Iterator<Map.Entry<UUID, Long>> iter = dialogueExpiry.entrySet().iterator();
        while (iter.hasNext()) {
            Map.Entry<UUID, Long> entry = iter.next();
            if (currentTick >= entry.getValue()) {
                UUID uuid = entry.getKey();
                iter.remove();
                // Find the villager and clear dialogue
                Entity entity = level.getEntity(uuid);
                if (entity instanceof Villager villager) {
                    speechBubbles.clear(villager);
                }
            }
        }
    }
}
