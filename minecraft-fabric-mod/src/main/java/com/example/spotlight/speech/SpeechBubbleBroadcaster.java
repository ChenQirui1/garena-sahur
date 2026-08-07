package com.example.spotlight.speech;

import com.example.DynamicLlmLod;
import com.example.spotlight.SpotlightConfig;
import net.fabricmc.fabric.api.networking.v1.PacketByteBufs;
import net.fabricmc.fabric.api.networking.v1.PlayerLookup;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.npc.Villager;

/** Sends temporary NPC speech to tracking Fabric clients. */
public final class SpeechBubbleBroadcaster {
    public static final net.minecraft.resources.ResourceLocation CHANNEL =
            DynamicLlmLod.id("speech_bubble");

    public void show(Villager villager, String text, int durationTicks) {
        send(villager, text, durationTicks);
    }

    public void clear(Villager villager) {
        send(villager, "", 0);
    }

    private void send(Villager villager, String text, int durationTicks) {
        String safeText = text.length() <= SpotlightConfig.MAX_DIALOGUE_LENGTH
                ? text
                : text.substring(0, SpotlightConfig.MAX_DIALOGUE_LENGTH);

        for (ServerPlayer player : PlayerLookup.tracking(villager)) {
            if (!ServerPlayNetworking.canSend(player, CHANNEL)) {
                continue;
            }
            FriendlyByteBuf buffer = PacketByteBufs.create();
            buffer.writeVarInt(villager.getId());
            buffer.writeUUID(villager.getUUID());
            buffer.writeUtf(safeText, SpotlightConfig.MAX_DIALOGUE_LENGTH);
            buffer.writeVarInt(Math.max(0, durationTicks));
            ServerPlayNetworking.send(player, CHANNEL, buffer);
        }
    }
}
