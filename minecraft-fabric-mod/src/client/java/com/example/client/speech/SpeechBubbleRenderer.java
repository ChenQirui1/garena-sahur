package com.example.client.speech;

import com.example.spotlight.SpotlightConfig;
import com.mojang.blaze3d.vertex.PoseStack;
import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderContext;
import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.renderer.LightTexture;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.network.chat.Component;
import net.minecraft.util.FormattedCharSequence;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.phys.Vec3;
import org.joml.Matrix4f;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Fabric-native speech-bubble renderer.
 *
 * The speech-bubble feature is inspired by Mrbysco's Notable Bubble Text (NBT),
 * MIT licensed. This renderer is an independent Fabric implementation and does
 * not include NBT source code. See THIRD_PARTY_NOTICES.md.
 */
public final class SpeechBubbleRenderer {
    private static final int TEXT_COLOR = 0xFFFFFFFF;
    private static final int BACKGROUND_COLOR = (int) 0xC0181818L;

    private SpeechBubbleRenderer() {}

    public static void register() {
        WorldRenderEvents.AFTER_ENTITIES.register(SpeechBubbleRenderer::render);
    }

    private static void render(WorldRenderContext context) {
        Minecraft client = Minecraft.getInstance();
        if (client.level == null || client.player == null || context.consumers() == null) {
            return;
        }

        for (Map.Entry<UUID, ClientSpeechBubbles.Bubble> entry : ClientSpeechBubbles.active().entrySet()) {
            Entity entity = client.level.getEntity(entry.getValue().entityNetworkId());
            if (entity == null || !entity.getUUID().equals(entry.getKey())
                    || entity.isRemoved() || entity.isInvisibleTo(client.player)) {
                continue;
            }
            if (client.player.distanceToSqr(entity)
                    > SpotlightConfig.SPEECH_BUBBLE_MAX_DISTANCE * SpotlightConfig.SPEECH_BUBBLE_MAX_DISTANCE) {
                continue;
            }
            renderBubble(context, entity, entry.getValue().text());
        }
    }

    private static void renderBubble(WorldRenderContext context, Entity entity, String text) {
        Minecraft client = Minecraft.getInstance();
        Font font = client.font;
        List<FormattedCharSequence> wrapped = font.split(
                Component.literal(text),
                SpotlightConfig.SPEECH_BUBBLE_MAX_WIDTH
        );
        if (wrapped.isEmpty()) {
            return;
        }
        if (wrapped.size() > SpotlightConfig.SPEECH_BUBBLE_MAX_LINES) {
            wrapped = wrapped.subList(0, SpotlightConfig.SPEECH_BUBBLE_MAX_LINES);
        }

        Vec3 entityPosition = entity.getPosition(context.tickDelta());
        Vec3 cameraPosition = context.camera().getPosition();
        PoseStack matrices = context.matrixStack();
        MultiBufferSource consumers = context.consumers();

        matrices.pushPose();
        matrices.translate(
                entityPosition.x - cameraPosition.x,
                entityPosition.y - cameraPosition.y + entity.getBbHeight() + 0.65,
                entityPosition.z - cameraPosition.z
        );
        matrices.mulPose(client.getEntityRenderDispatcher().cameraOrientation());
        matrices.scale(-0.025f, -0.025f, 0.025f);

        Matrix4f pose = matrices.last().pose();
        float totalHeight = wrapped.size() * (font.lineHeight + 1.0f);
        float y = -totalHeight / 2.0f;
        for (FormattedCharSequence line : wrapped) {
            float x = -font.width(line) / 2.0f;
            font.drawInBatch(
                    line,
                    x,
                    y,
                    TEXT_COLOR,
                    false,
                    pose,
                    consumers,
                    Font.DisplayMode.NORMAL,
                    BACKGROUND_COLOR,
                    LightTexture.FULL_BRIGHT
            );
            y += font.lineHeight + 1.0f;
        }
        matrices.popPose();
    }
}
