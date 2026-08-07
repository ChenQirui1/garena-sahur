package com.example.spotlight.command;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class BehaviourCommandTest {
    @Test
    void parsesCanonicalDialogueCommand() {
        BehaviourCommand command = BehaviourCommand.parse(commandJson(1, "command-1"));

        assertEquals("command-1", command.commandId());
        assertEquals(1, command.commandSequence());
        assertEquals("Hello", command.dialogue());
    }

    @Test
    void rejectsUnknownFields() {
        JsonObject json = commandJson(1, "command-1");
        json.addProperty("unexpected", true);

        assertThrows(IllegalArgumentException.class, () -> BehaviourCommand.parse(json));
    }

    @Test
    void rejectsLifetimeLongerThanFifteenSeconds() {
        JsonObject json = commandJson(1, "command-1");
        json.addProperty("expires_at_ms", 16_001);

        assertThrows(IllegalArgumentException.class, () -> BehaviourCommand.parse(json));
    }

    @Test
    void trackerRejectsDuplicateIdsAndOldSequences() {
        CommandAcceptanceTracker tracker = new CommandAcceptanceTracker();
        BehaviourCommand first = BehaviourCommand.parse(commandJson(1, "command-1"));
        tracker.markApplied(first);

        assertEquals(
                CommandAcceptanceTracker.Decision.DUPLICATE_COMMAND_ID,
                tracker.evaluate(first)
        );
        assertEquals(
                CommandAcceptanceTracker.Decision.STALE_COMMAND_SEQUENCE,
                tracker.evaluate(BehaviourCommand.parse(commandJson(1, "command-2")))
        );
        assertEquals(
                CommandAcceptanceTracker.Decision.ACCEPT,
                tracker.evaluate(BehaviourCommand.parse(commandJson(2, "command-2")))
        );
    }

    private static JsonObject commandJson(long sequence, String commandId) {
        return JsonParser.parseString("""
                {
                  "schema_version": "1.0",
                  "message_type": "behaviour_command",
                  "session_id": "minecraft-spotlight-001",
                  "command_id": "%s",
                  "request_id": "request-1",
                  "npc_id": "123e4567-e89b-12d3-a456-426614174000",
                  "tier": "focused",
                  "event_id": null,
                  "conversation_id": null,
                  "turn_id": null,
                  "source_sequence": 10,
                  "created_at_ms": 1000,
                  "expires_at_ms": 16000,
                  "dialogue": "Hello",
                  "action": null,
                  "fallback_used": false,
                  "command_sequence": %d
                }
                """.formatted(commandId, sequence)).getAsJsonObject();
    }
}
