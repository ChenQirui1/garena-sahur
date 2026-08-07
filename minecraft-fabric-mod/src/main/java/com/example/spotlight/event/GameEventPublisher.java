package com.example.spotlight.event;

import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.transport.HttpPublisher;
import com.google.gson.JsonObject;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Builds and publishes game_event JSON messages to the backend.
 */
public class GameEventPublisher {
    private final HttpPublisher httpPublisher;
    private final Map<String, Long> activeEvents = new LinkedHashMap<>();

    public GameEventPublisher(HttpPublisher httpPublisher) {
        this.httpPublisher = httpPublisher;
    }

    /**
     * Publishes a game event.
     *
     * @param eventType  the type of event (e.g., "market_theft", "zombie_attack")
     * @param actorUuid  UUID of the entity performing the action
     * @param targetUuid UUID of the target entity (nullable)
     * @param details    additional details (nullable)
     */
    public void publish(String eventType, UUID actorUuid, UUID targetUuid, String details) {
        String eventId = UUID.randomUUID().toString();
        long timestamp = System.currentTimeMillis();
        JsonObject message = new JsonObject();
        message.addProperty("type", "game_event");
        message.addProperty("timestamp", timestamp);
        message.addProperty("event_id", eventId);
        message.addProperty("event_type", eventType);
        message.addProperty("actor_uuid", actorUuid.toString());

        if (targetUuid != null) {
            message.addProperty("target_uuid", targetUuid.toString());
        }
        if (details != null && !details.isEmpty()) {
            message.addProperty("details", details);
        }

        httpPublisher.publish(message);
        activeEvents.put(eventId, timestamp + SpotlightConfig.COMMAND_MAX_LIFETIME_MS);
    }

    /**
     * Prototype events remain command-current for the same bounded lifetime as a command.
     * A future event-ended/cancelled hook should remove the ID immediately.
     */
    public boolean isCurrentEvent(String eventId, long nowMs) {
        activeEvents.entrySet().removeIf(entry -> nowMs >= entry.getValue());
        return activeEvents.containsKey(eventId);
    }

    public void endEvent(String eventId) {
        activeEvents.remove(eventId);
    }
}
