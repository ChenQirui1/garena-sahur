package com.example.spotlight.command;

import com.example.spotlight.SpotlightConfig;

import java.util.LinkedHashMap;
import java.util.Map;

/** Tracks applied command identities and per-NPC ordering. Used only on the server thread. */
public final class CommandAcceptanceTracker {
    public enum Decision {
        ACCEPT,
        DUPLICATE_COMMAND_ID,
        STALE_COMMAND_SEQUENCE
    }

    private final Map<String, Boolean> appliedCommandIds = new LinkedHashMap<>();
    private final Map<String, Long> lastSequenceByNpc = new LinkedHashMap<>();

    public Decision evaluate(BehaviourCommand command) {
        if (appliedCommandIds.containsKey(command.commandId())) {
            return Decision.DUPLICATE_COMMAND_ID;
        }

        String sequenceKey = sequenceKey(command);
        long lastSequence = lastSequenceByNpc.getOrDefault(sequenceKey, 0L);
        if (command.commandSequence() <= lastSequence) {
            return Decision.STALE_COMMAND_SEQUENCE;
        }
        return Decision.ACCEPT;
    }

    public void markApplied(BehaviourCommand command) {
        appliedCommandIds.put(command.commandId(), Boolean.TRUE);
        lastSequenceByNpc.put(sequenceKey(command), command.commandSequence());
        while (appliedCommandIds.size() > SpotlightConfig.MAX_TRACKED_COMMAND_IDS) {
            String oldest = appliedCommandIds.keySet().iterator().next();
            appliedCommandIds.remove(oldest);
        }
    }

    public void clear() {
        appliedCommandIds.clear();
        lastSequenceByNpc.clear();
    }

    private static String sequenceKey(BehaviourCommand command) {
        return command.sessionId() + "\u0000" + command.npcId();
    }
}
