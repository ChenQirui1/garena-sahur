package com.example.spotlight.commands;

import com.example.spotlight.conversation.ConversationPublisher;
import com.example.spotlight.event.GameEventPublisher;
import com.example.spotlight.snapshot.CandidateTracker;
import com.example.spotlight.SpotlightState;
import com.example.spotlight.transport.WebSocketSubscriber;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.npc.Villager;

import java.util.Map;
import java.util.UUID;

/**
 * Registers /spotlight slash commands for prototype triggering and debugging.
 */
public class SpotlightCommands {
    private final GameEventPublisher gameEventPublisher;
    private final ConversationPublisher conversationPublisher;
    private final CandidateTracker candidateTracker;
    private final WebSocketSubscriber webSocketSubscriber;

    public SpotlightCommands(
            GameEventPublisher gameEventPublisher,
            ConversationPublisher conversationPublisher,
            CandidateTracker candidateTracker,
            WebSocketSubscriber webSocketSubscriber
    ) {
        this.gameEventPublisher = gameEventPublisher;
        this.conversationPublisher = conversationPublisher;
        this.candidateTracker = candidateTracker;
        this.webSocketSubscriber = webSocketSubscriber;
    }

    public void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(
                Commands.literal("spotlight")
                        .then(Commands.literal("status")
                                .executes(this::statusCommand))
                        .then(Commands.literal("connect")
                                .executes(this::connectCommand))
                        .then(Commands.literal("disconnect")
                                .executes(this::disconnectCommand))
                        .then(Commands.literal("event")
                                .then(Commands.argument("event_type", StringArgumentType.word())
                                        .then(Commands.argument("actor_uuid", StringArgumentType.word())
                                                .executes(this::eventCommandNoTarget)
                                                .then(Commands.argument("target_uuid", StringArgumentType.word())
                                                        .executes(this::eventCommandWithTarget)))))
                        .then(Commands.literal("talk")
                                .then(Commands.argument("npc_uuid", StringArgumentType.word())
                                        .then(Commands.argument("message", StringArgumentType.greedyString())
                                                .executes(this::talkCommand))))
        );
    }

    private int statusCommand(CommandContext<CommandSourceStack> ctx) {
        SpotlightState state = SpotlightState.get();
        CommandSourceStack source = ctx.getSource();

        source.sendSystemMessage(Component.literal("--- Spotlight Status ---"));
        source.sendSystemMessage(Component.literal("Initialized: " + state.initialized));
        source.sendSystemMessage(Component.literal("WebSocket: " + (state.webSocketConnected ? "Connected" : "Disconnected")));
        source.sendSystemMessage(Component.literal("Candidates: " + state.candidateCount));
        source.sendSystemMessage(Component.literal("Sequence #: " + state.sequenceNumber));
        source.sendSystemMessage(Component.literal("Publish rate: " + String.format("%.1f Hz", state.publishRateHz)));
        source.sendSystemMessage(Component.literal("Backend: " + state.backendUrl));

        if (state.lastError != null) {
            source.sendSystemMessage(Component.literal("Last error: " + state.lastError));
        }

        // List candidates
        Map<UUID, Villager> candidates = candidateTracker.getCandidates();
        if (!candidates.isEmpty()) {
            source.sendSystemMessage(Component.literal("Candidate NPCs:"));
            for (Map.Entry<UUID, Villager> entry : candidates.entrySet()) {
                Villager v = entry.getValue();
                String name = v.hasCustomName() ? v.getCustomName().getString() : v.getVillagerData().getProfession().name();
                source.sendSystemMessage(Component.literal("  " + entry.getKey() + " (" + name + ")"));
            }
        }

        return 1;
    }

    private int connectCommand(CommandContext<CommandSourceStack> ctx) {
        webSocketSubscriber.connect();
        ctx.getSource().sendSystemMessage(Component.literal("Spotlight: Connecting to WebSocket..."));
        return 1;
    }

    private int disconnectCommand(CommandContext<CommandSourceStack> ctx) {
        webSocketSubscriber.disconnect();
        ctx.getSource().sendSystemMessage(Component.literal("Spotlight: Disconnected from WebSocket"));
        return 1;
    }

    private int eventCommandNoTarget(CommandContext<CommandSourceStack> ctx) {
        return fireEvent(ctx, null);
    }

    private int eventCommandWithTarget(CommandContext<CommandSourceStack> ctx) {
        String targetStr = StringArgumentType.getString(ctx, "target_uuid");
        try {
            UUID targetUuid = UUID.fromString(targetStr);
            return fireEvent(ctx, targetUuid);
        } catch (IllegalArgumentException e) {
            ctx.getSource().sendFailure(Component.literal("Invalid target UUID: " + targetStr));
            return 0;
        }
    }

    private int fireEvent(CommandContext<CommandSourceStack> ctx, UUID targetUuid) {
        String eventType = StringArgumentType.getString(ctx, "event_type");
        String actorStr = StringArgumentType.getString(ctx, "actor_uuid");

        try {
            UUID actorUuid = UUID.fromString(actorStr);
            gameEventPublisher.publish(eventType, actorUuid, targetUuid, null);
            ctx.getSource().sendSystemMessage(Component.literal("Spotlight: Published game_event '" + eventType + "'"));
            return 1;
        } catch (IllegalArgumentException e) {
            ctx.getSource().sendFailure(Component.literal("Invalid actor UUID: " + actorStr));
            return 0;
        }
    }

    private int talkCommand(CommandContext<CommandSourceStack> ctx) {
        String npcStr = StringArgumentType.getString(ctx, "npc_uuid");
        String message = StringArgumentType.getString(ctx, "message");

        try {
            UUID npcUuid = UUID.fromString(npcStr);
            String playerName = ctx.getSource().getTextName();
            conversationPublisher.publishPlayerTurn(npcUuid, playerName, message);
            ctx.getSource().sendSystemMessage(Component.literal("Spotlight: Published conversation_turn to NPC " + npcStr));
            return 1;
        } catch (IllegalArgumentException e) {
            ctx.getSource().sendFailure(Component.literal("Invalid NPC UUID: " + npcStr));
            return 0;
        }
    }
}
