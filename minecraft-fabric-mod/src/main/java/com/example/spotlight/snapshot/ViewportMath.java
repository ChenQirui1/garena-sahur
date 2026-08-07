package com.example.spotlight.snapshot;

import com.example.spotlight.SpotlightConfig;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.phys.Vec3;

/**
 * Pure math utilities for viewport calculations.
 * Computes player look direction and viewport center distance for entities.
 */
public final class ViewportMath {
    private ViewportMath() {}

    /**
     * Returns the player's unit look direction vector based on pitch and yaw.
     */
    public static Vec3 getLookDirection(ServerPlayer player) {
        float pitch = player.getXRot();  // vertical rotation (degrees)
        float yaw = player.getYRot();    // horizontal rotation (degrees)

        double pitchRad = Math.toRadians(pitch);
        double yawRad = Math.toRadians(yaw);

        double x = -Math.sin(yawRad) * Math.cos(pitchRad);
        double y = -Math.sin(pitchRad);
        double z = Math.cos(yawRad) * Math.cos(pitchRad);

        return new Vec3(x, y, z).normalize();
    }

    /**
     * Computes the viewport center distance for an entity relative to the player.
     * Returns a normalized value where:
     *   0.0 = entity is at the center of the viewport
     *   1.0 = entity is at the edge of the configured FOV
     *   >1.0 = entity is outside the viewport
     *
     * This raw value is useful for the inside/outside decision. Use
     * {@link #toWireViewportCenterDistance(double)} before publishing it because
     * the backend contract requires a unit interval.
     */
    public static double viewportCenterDistance(ServerPlayer player, Entity entity) {
        Vec3 playerEye = player.getEyePosition();
        Vec3 entityPos = entity.position().add(0, entity.getBbHeight() / 2.0, 0);
        Vec3 toEntity = entityPos.subtract(playerEye).normalize();
        Vec3 lookDir = getLookDirection(player);

        double dot = lookDir.dot(toEntity);
        // Clamp to avoid NaN from floating-point errors
        dot = Math.max(-1.0, Math.min(1.0, dot));
        double angleDeg = Math.toDegrees(Math.acos(dot));

        return angleDeg / SpotlightConfig.HALF_FOV_DEGREES;
    }

    /**
     * Returns true if the entity is within the player's viewport (viewport_center_distance <= 1.0).
     */
    public static boolean isInsideViewport(ServerPlayer player, Entity entity) {
        return viewportCenterDistance(player, entity) <= 1.0;
    }

    /** Converts the raw angular distance to the backend's inclusive 0..1 range. */
    public static double toWireViewportCenterDistance(double normalizedAngularDistance) {
        if (!Double.isFinite(normalizedAngularDistance)) {
            throw new IllegalArgumentException("viewport distance must be finite");
        }
        return Math.max(0.0, Math.min(1.0, normalizedAngularDistance));
    }
}
