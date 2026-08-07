package com.example.spotlight.command;

import com.example.DynamicLlmLod;
import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.conversation.ConversationPublisher;
import com.example.spotlight.event.GameEventPublisher;
import com.example.spotlight.snapshot.CandidateTracker;
import com.google.gson.JsonObject;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.entity.npc.Villager;
import net.minecraft.world.phys.Vec3;

import java.util.Set;

/** Validates and idempotently applies canonical behaviour commands on the server thread. */
public class BehaviourCommandHandler {
    private static final Set<String> ACTION_TYPES = Set.of("walk_to", "look_at", "look_at_player", "stop");

    private final CandidateTracker candidateTracker;
    private final NpcController npcController;
    private final ConversationPublisher conversationPublisher;
    private final GameEventPublisher gameEventPublisher;
    private final CommandAcceptanceTracker acceptanceTracker = new CommandAcceptanceTracker();

    public BehaviourCommandHandler(
            CandidateTracker candidateTracker,
            NpcController npcController,
            ConversationPublisher conversationPublisher,
            GameEventPublisher gameEventPublisher
    ) {
        this.candidateTracker = candidateTracker;
        this.npcController = npcController;
        this.conversationPublisher = conversationPublisher;
        this.gameEventPublisher = gameEventPublisher;
    }

    public void handle(MinecraftServer server, JsonObject message) {
        final BehaviourCommand command;
        try {
            command = BehaviourCommand.parse(message);
        } catch (IllegalArgumentException exception) {
            reject("invalid schema: " + exception.getMessage());
            return;
        }

        if (!SpotlightConfig.SESSION_ID.equals(command.sessionId())) {
            reject("wrong session_id");
            return;
        }

        long now = System.currentTimeMillis();
        if (now >= command.expiresAtMs()) {
            reject("expired command " + command.commandId());
            return;
        }

        Villager villager = candidateTracker.getByUuid(command.npcId());
        if (villager == null || !villager.isAlive() || villager.isRemoved()) {
            reject("NPC is not a live candidate: " + command.npcId());
            return;
        }

        if (command.eventId() != null && !gameEventPublisher.isCurrentEvent(command.eventId(), now)) {
            reject("event trigger is no longer current: " + command.eventId());
            return;
        }
        if (command.conversationId() != null
                && !conversationPublisher.isCurrentConversation(
                        command.npcId(), command.conversationId(), command.turnId())) {
            reject("conversation trigger is no longer current: " + command.conversationId());
            return;
        }

        CommandAcceptanceTracker.Decision decision = acceptanceTracker.evaluate(command);
        if (decision != CommandAcceptanceTracker.Decision.ACCEPT) {
            DynamicLlmLod.LOGGER.debug("Ignoring command {}: {}", command.commandId(), decision);
            return;
        }

        if (!validateAction(command, villager)) {
            return;
        }

        boolean applied = false;
        if (command.dialogue() != null) {
            npcController.showDialogue(villager, command.dialogue());
            applied = true;
        }
        if (command.action() != null && ACTION_TYPES.contains(command.action().type())) {
            applyAction(villager, command.action());
            applied = true;
        }

        // An unknown action is ignored when valid dialogue still makes the command executable.
        if (!applied) {
            reject("command has no supported executable output: " + command.commandId());
            return;
        }

        acceptanceTracker.markApplied(command);
        DynamicLlmLod.LOGGER.info(
                "Applied behaviour command {} sequence {} to NPC {}",
                command.commandId(), command.commandSequence(), command.npcId()
        );
    }

    public void clearAcceptanceState() {
        acceptanceTracker.clear();
    }

    private boolean validateAction(BehaviourCommand command, Villager villager) {
        BehaviourCommand.Action action = command.action();
        if (action == null) {
            return true;
        }

        if (!ACTION_TYPES.contains(action.type())) {
            DynamicLlmLod.LOGGER.warn("Ignoring unknown action type: {}", action.type());
            if (command.dialogue() == null) {
                reject("unknown action without dialogue: " + action.type());
                return false;
            }
            return true;
        }

        JsonObject payload = action.payload();
        return switch (action.type()) {
            case "walk_to" -> validateCoordinates(payload, true);
            case "look_at" -> validateCoordinates(payload, false);
            case "look_at_player" -> validateNoPayload(payload)
                    && validateNearbyPlayer(villager);
            case "stop" -> validateNoPayload(payload);
            default -> false;
        };
    }

    private void applyAction(Villager villager, BehaviourCommand.Action action) {
        JsonObject payload = action.payload();
        switch (action.type()) {
            case "walk_to" -> {
                Vec3 target = coordinates(payload);
                float speed = payload.has("speed") ? payload.get("speed").getAsFloat() : 0.5f;
                npcController.walkTo(villager, target, speed);
            }
            case "look_at" -> npcController.lookAt(villager, coordinates(payload));
            case "look_at_player" -> npcController.lookAt(
                    villager,
                    villager.level().getNearestPlayer(villager, 64.0).getEyePosition()
            );
            case "stop" -> npcController.stop(villager);
            default -> throw new IllegalStateException("validated action became unknown");
        }
    }

    private boolean validateCoordinates(JsonObject payload, boolean allowSpeed) {
        Set<String> allowed = allowSpeed ? Set.of("x", "y", "z", "speed") : Set.of("x", "y", "z");
        for (String key : payload.keySet()) {
            if (!allowed.contains(key)) {
                reject("unknown action payload field: " + key);
                return false;
            }
        }
        for (String coordinate : Set.of("x", "y", "z")) {
            if (!isFiniteNumber(payload, coordinate)) {
                reject("action requires finite numeric " + coordinate);
                return false;
            }
        }
        if (allowSpeed && payload.has("speed")) {
            if (!isFiniteNumber(payload, "speed")) {
                reject("walk_to speed must be finite");
                return false;
            }
            double speed = payload.get("speed").getAsDouble();
            if (speed <= 0.0 || speed > 1.0) {
                reject("walk_to speed must be greater than 0 and at most 1");
                return false;
            }
        }
        return true;
    }

    private boolean validateNoPayload(JsonObject payload) {
        if (!payload.keySet().isEmpty()) {
            reject("stop action does not accept payload fields");
            return false;
        }
        return true;
    }

    private boolean validateNearbyPlayer(Villager villager) {
        if (villager.level().getNearestPlayer(villager, 64.0) != null) {
            return true;
        }
        reject("look_at_player requires a player within 64 blocks");
        return false;
    }

    private static boolean isFiniteNumber(JsonObject payload, String key) {
        if (!payload.has(key) || !payload.get(key).isJsonPrimitive()
                || !payload.get(key).getAsJsonPrimitive().isNumber()) {
            return false;
        }
        try {
            return Double.isFinite(payload.get(key).getAsDouble());
        } catch (NumberFormatException exception) {
            return false;
        }
    }

    private static Vec3 coordinates(JsonObject payload) {
        return new Vec3(
                payload.get("x").getAsDouble(),
                payload.get("y").getAsDouble(),
                payload.get("z").getAsDouble()
        );
    }

    private static void reject(String reason) {
        DynamicLlmLod.LOGGER.warn("Rejected behaviour command: {}", reason);
    }
}
