package com.example.spotlight;

/**
 * Shared state object read by the F3 debug overlay (client-side) and written
 * by server-side logic. All fields are volatile for safe cross-thread reads.
 */
public final class SpotlightState {
    private static final SpotlightState INSTANCE = new SpotlightState();

    public static SpotlightState get() {
        return INSTANCE;
    }

    private SpotlightState() {}

    // Mod lifecycle
    public volatile boolean initialized = false;

    // WebSocket status
    public volatile boolean webSocketConnected = false;

    // Candidate tracking
    public volatile int candidateCount = 0;

    // Publishing stats
    public volatile long sequenceNumber = 0;
    public volatile long lastPublishTimeMs = 0;
    public volatile double publishRateHz = 0.0;

    // Backend URL (for display)
    public volatile String backendUrl = SpotlightConfig.BACKEND_BASE_URL;

    // Last error (if any)
    public volatile String lastError = null;
}
