package com.example.spotlight.conversation;

import com.example.spotlight.transport.HttpPublisher;
import com.google.gson.JsonObject;

import java.util.UUID;

/**
 * Builds and publishes conversation_turn JSON messages to the backend.
 * Tracks an active conversation context (which NPC the player is talking to).
 */
public class ConversationPublisher {
    private final HttpPublisher httpPublisher;

    // Active conversation tracking
    private UUID activeNpcUuid = null;
    private String activeConversationId = null;
    private String latestTurnId = null;
    private long conversationStartTime = 0;
    private int turnCount = 0;

    public ConversationPublisher(HttpPublisher httpPublisher) {
        this.httpPublisher = httpPublisher;
    }

    /**
     * Publishes a conversation turn from the player to an NPC.
     *
     * @param npcUuid    the UUID of the NPC being spoken to
     * @param playerName the player's display name
     * @param message    what the player said
     */
    public void publishPlayerTurn(UUID npcUuid, String playerName, String message) {
        startOrContinueConversation(npcUuid);
        turnCount++;
        latestTurnId = UUID.randomUUID().toString();

        JsonObject msg = new JsonObject();
        msg.addProperty("type", "conversation_turn");
        msg.addProperty("timestamp", System.currentTimeMillis());
        msg.addProperty("npc_uuid", npcUuid.toString());
        msg.addProperty("conversation_id", activeConversationId);
        msg.addProperty("turn_id", latestTurnId);
        msg.addProperty("speaker", "player");
        msg.addProperty("speaker_name", playerName);
        msg.addProperty("message", message);
        msg.addProperty("turn_number", turnCount);
        msg.addProperty("conversation_start", conversationStartTime);

        httpPublisher.publish(msg);
    }

    /**
     * Ends the active conversation, if any.
     */
    public void endConversation() {
        activeNpcUuid = null;
        activeConversationId = null;
        latestTurnId = null;
        conversationStartTime = 0;
        turnCount = 0;
    }

    public UUID getActiveNpcUuid() {
        return activeNpcUuid;
    }

    public boolean hasActiveConversation() {
        return activeNpcUuid != null;
    }

    /** Commands for older conversations or turns are no longer current. */
    public boolean isCurrentConversation(UUID npcUuid, String conversationId, String turnId) {
        if (activeNpcUuid == null || !activeNpcUuid.equals(npcUuid)) {
            return false;
        }
        if (activeConversationId == null || !activeConversationId.equals(conversationId)) {
            return false;
        }
        return turnId == null || turnId.equals(latestTurnId);
    }

    private void startOrContinueConversation(UUID npcUuid) {
        if (activeNpcUuid == null || !activeNpcUuid.equals(npcUuid)) {
            // New conversation
            activeNpcUuid = npcUuid;
            activeConversationId = UUID.randomUUID().toString();
            latestTurnId = null;
            conversationStartTime = System.currentTimeMillis();
            turnCount = 0;
        }
    }
}
