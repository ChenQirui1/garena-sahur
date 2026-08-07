package com.example.client.speech;

import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.speech.SpeechBubbleBroadcaster;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** Client-side cache for short-lived NPC speech packets. */
public final class ClientSpeechBubbles {
    private static final Map<UUID, Bubble> ACTIVE = new LinkedHashMap<>();

    private ClientSpeechBubbles() {}

    public static void register() {
        ClientPlayNetworking.registerGlobalReceiver(
                SpeechBubbleBroadcaster.CHANNEL,
                (client, handler, buffer, responseSender) -> {
                    int entityNetworkId = buffer.readVarInt();
                    UUID entityId = buffer.readUUID();
                    String text = buffer.readUtf(SpotlightConfig.MAX_DIALOGUE_LENGTH);
                    int durationTicks = buffer.readVarInt();
                    client.execute(() -> receive(
                            client, entityNetworkId, entityId, text, durationTicks
                    ));
                }
        );

        ClientTickEvents.END_CLIENT_TICK.register(ClientSpeechBubbles::tick);
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> ACTIVE.clear());
    }

    public static Map<UUID, Bubble> active() {
        return Collections.unmodifiableMap(ACTIVE);
    }

    private static void receive(
            Minecraft client,
            int entityNetworkId,
            UUID entityId,
            String text,
            int durationTicks
    ) {
        if (durationTicks <= 0 || text.isBlank() || client.level == null) {
            ACTIVE.remove(entityId);
            return;
        }
        ACTIVE.put(entityId, new Bubble(
                entityNetworkId,
                text,
                client.level.getGameTime() + durationTicks
        ));
    }

    private static void tick(Minecraft client) {
        if (client.level == null) {
            ACTIVE.clear();
            return;
        }
        long now = client.level.getGameTime();
        ACTIVE.entrySet().removeIf(entry -> entry.getValue().expiresAtTick() <= now);
    }

    public record Bubble(int entityNetworkId, String text, long expiresAtTick) {}
}
