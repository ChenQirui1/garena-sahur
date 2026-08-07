package com.example.spotlight.command;

import com.example.spotlight.SpotlightConfig;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.Set;
import java.util.UUID;

/** Canonical backend-to-Minecraft behaviour command. */
public record BehaviourCommand(
        String sessionId,
        String commandId,
        String requestId,
        UUID npcId,
        String tier,
        String eventId,
        String conversationId,
        String turnId,
        long sourceSequence,
        long commandSequence,
        long createdAtMs,
        long expiresAtMs,
        String dialogue,
        Action action,
        boolean fallbackUsed
) {
    private static final Set<String> FIELDS = Set.of(
            "schema_version", "message_type", "session_id", "command_id", "request_id",
            "npc_id", "tier", "event_id", "conversation_id", "turn_id", "source_sequence",
            "created_at_ms", "expires_at_ms", "dialogue", "action", "fallback_used",
            "command_sequence"
    );

    public record Action(String type, JsonObject payload) {}

    public static BehaviourCommand parse(JsonObject json) {
        for (String key : json.keySet()) {
            if (!FIELDS.contains(key)) {
                throw new IllegalArgumentException("unknown field: " + key);
            }
        }

        requireExact(json, "schema_version", "1.0");
        requireExact(json, "message_type", "behaviour_command");

        String sessionId = requiredString(json, "session_id");
        String commandId = requiredString(json, "command_id");
        String requestId = requiredString(json, "request_id");
        String npcIdText = requiredString(json, "npc_id");
        String tier = requiredString(json, "tier");
        String eventId = nullableString(json, "event_id");
        String conversationId = nullableString(json, "conversation_id");
        String turnId = nullableString(json, "turn_id");
        long sourceSequence = requiredLong(json, "source_sequence");
        long commandSequence = requiredLong(json, "command_sequence");
        long createdAtMs = requiredLong(json, "created_at_ms");
        long expiresAtMs = requiredLong(json, "expires_at_ms");
        String dialogue = nullableString(json, "dialogue");
        Action action = parseAction(json.get("action"));
        boolean fallbackUsed = requiredBoolean(json, "fallback_used");

        checkLength("session_id", sessionId, SpotlightConfig.MAX_COMMAND_ID_LENGTH);
        checkLength("command_id", commandId, SpotlightConfig.MAX_COMMAND_ID_LENGTH);
        checkLength("request_id", requestId, SpotlightConfig.MAX_COMMAND_ID_LENGTH);
        checkLength("npc_id", npcIdText, SpotlightConfig.MAX_COMMAND_ID_LENGTH);
        checkLength("tier", tier, SpotlightConfig.MAX_COMMAND_ID_LENGTH);
        checkNullableId("event_id", eventId);
        checkNullableId("conversation_id", conversationId);
        checkNullableId("turn_id", turnId);

        UUID npcId;
        try {
            npcId = UUID.fromString(npcIdText);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("npc_id must be a UUID", exception);
        }

        if (sourceSequence < 0) {
            throw new IllegalArgumentException("source_sequence must be non-negative");
        }
        if (commandSequence < 1) {
            throw new IllegalArgumentException("command_sequence must start at 1");
        }
        if (createdAtMs < 0 || expiresAtMs <= createdAtMs) {
            throw new IllegalArgumentException("expires_at_ms must be greater than created_at_ms");
        }
        if (expiresAtMs - createdAtMs > SpotlightConfig.COMMAND_MAX_LIFETIME_MS) {
            throw new IllegalArgumentException("command lifetime exceeds the accepted maximum");
        }
        if (dialogue != null) {
            if (dialogue.isEmpty()) {
                throw new IllegalArgumentException("dialogue must be null or non-empty");
            }
            if (dialogue.length() > SpotlightConfig.MAX_DIALOGUE_LENGTH) {
                throw new IllegalArgumentException("dialogue exceeds the maximum length");
            }
        }
        if (dialogue == null && action == null) {
            throw new IllegalArgumentException("at least one of dialogue or action is required");
        }

        return new BehaviourCommand(
                sessionId, commandId, requestId, npcId, tier, eventId, conversationId, turnId,
                sourceSequence, commandSequence, createdAtMs, expiresAtMs, dialogue, action,
                fallbackUsed
        );
    }

    private static Action parseAction(JsonElement element) {
        if (element == null || element.isJsonNull()) {
            return null;
        }
        if (!element.isJsonObject()) {
            throw new IllegalArgumentException("action must be null or an object");
        }

        JsonObject action = element.getAsJsonObject();
        for (String key : action.keySet()) {
            if (!key.equals("type") && !key.equals("payload")) {
                throw new IllegalArgumentException("unknown action field: " + key);
            }
        }

        String type = requiredString(action, "type");
        checkLength("action.type", type, SpotlightConfig.MAX_COMMAND_ID_LENGTH);
        JsonObject payload = new JsonObject();
        if (action.has("payload") && !action.get("payload").isJsonNull()) {
            if (!action.get("payload").isJsonObject()) {
                throw new IllegalArgumentException("action.payload must be an object");
            }
            payload = action.getAsJsonObject("payload");
        }
        return new Action(type, payload);
    }

    private static void requireExact(JsonObject json, String key, String expected) {
        String value = requiredString(json, key);
        if (!expected.equals(value)) {
            throw new IllegalArgumentException(key + " must be " + expected);
        }
    }

    private static String requiredString(JsonObject json, String key) {
        if (!json.has(key) || json.get(key).isJsonNull() || !json.get(key).isJsonPrimitive()
                || !json.get(key).getAsJsonPrimitive().isString()) {
            throw new IllegalArgumentException(key + " must be a string");
        }
        String value = json.get(key).getAsString();
        if (value.isEmpty()) {
            throw new IllegalArgumentException(key + " must not be empty");
        }
        return value;
    }

    private static String nullableString(JsonObject json, String key) {
        if (!json.has(key) || json.get(key).isJsonNull()) {
            return null;
        }
        return requiredString(json, key);
    }

    private static long requiredLong(JsonObject json, String key) {
        if (!json.has(key) || json.get(key).isJsonNull() || !json.get(key).isJsonPrimitive()
                || !json.get(key).getAsJsonPrimitive().isNumber()) {
            throw new IllegalArgumentException(key + " must be an integer");
        }
        try {
            return json.get(key).getAsLong();
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException(key + " must be an integer", exception);
        }
    }

    private static boolean requiredBoolean(JsonObject json, String key) {
        if (!json.has(key) || json.get(key).isJsonNull() || !json.get(key).isJsonPrimitive()
                || !json.get(key).getAsJsonPrimitive().isBoolean()) {
            throw new IllegalArgumentException(key + " must be a boolean");
        }
        return json.get(key).getAsBoolean();
    }

    private static void checkNullableId(String field, String value) {
        if (value != null) {
            checkLength(field, value, SpotlightConfig.MAX_COMMAND_ID_LENGTH);
        }
    }

    private static void checkLength(String field, String value, int maximum) {
        if (value.length() > maximum) {
            throw new IllegalArgumentException(field + " exceeds " + maximum + " characters");
        }
    }
}
