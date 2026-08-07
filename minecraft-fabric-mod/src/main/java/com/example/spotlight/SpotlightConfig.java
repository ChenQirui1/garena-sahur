package com.example.spotlight;

public final class SpotlightConfig {
    private SpotlightConfig() {}

    // Hysteresis radii (blocks)
    public static final double ENTRY_RADIUS = 24.0;
    public static final double EXIT_RADIUS = 28.0;

    // Snapshot publishing interval (ticks); 4 ticks = 200ms / 5Hz at 20 TPS.
    // HttpPublisher coalesces snapshots, so a slow backend receives the latest state
    // instead of accumulating an increasingly stale queue.
    public static final int SNAPSHOT_INTERVAL_TICKS = 4;

    // Backend connection
    public static final String BACKEND_BASE_URL = "http://localhost:8000";
    public static final String PUBLISH_ENDPOINT = "/api/v1/messages";
    public static final String WEBSOCKET_ENDPOINT = "ws://localhost:8000/api/v1/ws";

    // Session identifier (fixed for prototype)
    public static final String SESSION_ID = "minecraft-spotlight-001";

    // Viewport
    public static final double HALF_FOV_DEGREES = 55.0;

    // Dialogue display duration (ticks); 100 ticks = 5 seconds
    public static final int DIALOGUE_DISPLAY_TICKS = 100;
    public static final double SPEECH_BUBBLE_MAX_DISTANCE = 64.0;
    public static final int SPEECH_BUBBLE_MAX_WIDTH = 160;
    public static final int SPEECH_BUBBLE_MAX_LINES = 6;

    // HTTP publisher thread pool size
    public static final int HTTP_THREAD_POOL_SIZE = 2;

    // Backend behaviour-command contract
    public static final long COMMAND_MAX_LIFETIME_MS = 15_000;
    public static final int MAX_COMMAND_MESSAGE_BYTES = 65_536;
    public static final int MAX_COMMAND_ID_LENGTH = 128;
    public static final int MAX_DIALOGUE_LENGTH = 512;
    public static final int MAX_TRACKED_COMMAND_IDS = 4096;
}
