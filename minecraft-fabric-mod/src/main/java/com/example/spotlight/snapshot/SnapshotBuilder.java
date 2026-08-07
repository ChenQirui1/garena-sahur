package com.example.spotlight.snapshot;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.ai.village.poi.PoiManager;
import net.minecraft.world.entity.ai.village.poi.PoiTypes;
import net.minecraft.world.entity.npc.Villager;
import net.minecraft.world.entity.npc.VillagerData;
import net.minecraft.world.entity.npc.VillagerProfession;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

import java.util.Map;
import java.util.UUID;

/**
 * Assembles a world_snapshot JSON message from the current candidate set.
 */
public final class SnapshotBuilder {
    private SnapshotBuilder() {}

    /**
     * Builds a complete world_snapshot message.
     */
    public static JsonObject build(ServerPlayer player, Map<UUID, Villager> candidates) {
        JsonObject message = new JsonObject();
        message.addProperty("type", "world_snapshot");
        message.addProperty("timestamp", System.currentTimeMillis());

        // Player info
        JsonObject playerJson = buildPlayer(player);
        message.add("player", playerJson);

        // NPCs (candidates)
        JsonArray npcsArray = new JsonArray();
        for (Map.Entry<UUID, Villager> entry : candidates.entrySet()) {
            Villager villager = entry.getValue();
            JsonObject npcJson = buildNpc(player, villager);
            npcsArray.add(npcJson);
        }
        message.add("npcs", npcsArray);

        return message;
    }

    private static JsonObject buildPlayer(ServerPlayer player) {
        JsonObject json = new JsonObject();
        json.addProperty("uuid", player.getUUID().toString());
        json.addProperty("name", player.getGameProfile().getName());

        JsonObject pos = positionJson(player.position());
        json.add("position", pos);

        JsonObject look = new JsonObject();
        look.addProperty("yaw", player.getYRot());
        look.addProperty("pitch", player.getXRot());
        json.add("look", look);

        // Held item
        ItemStack held = player.getMainHandItem();
        if (!held.isEmpty()) {
            json.addProperty("held_item", held.getItem().toString());
        }

        return json;
    }

    private static JsonObject buildNpc(ServerPlayer player, Villager villager) {
        JsonObject json = new JsonObject();
        json.addProperty("uuid", villager.getUUID().toString());

        // Name (custom name or profession)
        String name = null;
        if (villager.hasCustomName()) {
            name = villager.getCustomName().getString();
        }
        if (name == null || name.isEmpty()) {
            VillagerData data = villager.getVillagerData();
            name = formatProfession(data.getProfession());
        }
        json.addProperty("name", name);

        // Profession
        VillagerData vData = villager.getVillagerData();
        json.addProperty("profession", formatProfession(vData.getProfession()));
        json.addProperty("level", vData.getLevel());

        // Position
        json.add("position", positionJson(villager.position()));

        // Distance from player
        double distance = player.distanceTo(villager);
        double roundedDistance = Math.round(distance * 100.0) / 100.0;
        // Keep the prototype alias while also publishing the canonical field name.
        json.addProperty("distance", roundedDistance);
        json.addProperty("world_distance_blocks", roundedDistance);

        // Viewport info
        double rawVcd = ViewportMath.viewportCenterDistance(player, villager);
        boolean insideViewport = rawVcd <= 1.0;
        double wireVcd = ViewportMath.toWireViewportCenterDistance(rawVcd);
        json.addProperty("viewport_center_distance", Math.round(wireVcd * 1000.0) / 1000.0);
        json.addProperty("inside_viewport", insideViewport);

        // Line of sight
        boolean los = LineOfSight.hasLineOfSight(player, villager);
        json.addProperty("line_of_sight", los);

        // Health
        json.addProperty("health", villager.getHealth());
        json.addProperty("max_health", villager.getMaxHealth());

        // Current activity (simple state)
        json.addProperty("activity", describeActivity(villager));

        return json;
    }

    private static JsonObject positionJson(Vec3 pos) {
        JsonObject json = new JsonObject();
        json.addProperty("x", Math.round(pos.x * 100.0) / 100.0);
        json.addProperty("y", Math.round(pos.y * 100.0) / 100.0);
        json.addProperty("z", Math.round(pos.z * 100.0) / 100.0);
        return json;
    }

    private static String formatProfession(VillagerProfession profession) {
        String name = profession.name();
        // Convert "farmer" -> "Farmer", "tool_smith" -> "Tool Smith"
        StringBuilder sb = new StringBuilder();
        boolean capitalizeNext = true;
        for (char c : name.toCharArray()) {
            if (c == '_') {
                sb.append(' ');
                capitalizeNext = true;
            } else {
                sb.append(capitalizeNext ? Character.toUpperCase(c) : c);
                capitalizeNext = false;
            }
        }
        return sb.toString();
    }

    private static String describeActivity(Villager villager) {
        if (villager.isSleeping()) return "sleeping";
        if (villager.isTrading()) return "trading";
        if (!villager.getNavigation().isDone()) return "walking";
        return "idle";
    }
}
