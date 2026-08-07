package com.example.spotlight.snapshot;

import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Server-side line-of-sight check using Level.clip() (raycast).
 */
public final class LineOfSight {
    private LineOfSight() {}

    /**
     * Returns true if there is an unobstructed line of sight from the player's
     * eyes to the center of the target entity (no solid blocks in between).
     */
    public static boolean hasLineOfSight(ServerPlayer player, Entity target) {
        ServerLevel level = player.serverLevel();
        Vec3 start = player.getEyePosition();
        Vec3 end = target.position().add(0, target.getBbHeight() / 2.0, 0);

        ClipContext context = new ClipContext(
                start,
                end,
                ClipContext.Block.COLLIDER,
                ClipContext.Fluid.NONE,
                player
        );

        BlockHitResult result = level.clip(context);
        return result.getType() == HitResult.Type.MISS;
    }
}
