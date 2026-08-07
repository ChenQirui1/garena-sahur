package com.example.spotlight.transport;

import com.example.DynamicLlmLod;
import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.SpotlightState;
import com.google.gson.JsonObject;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Async HTTP POST publisher. Sends JSON messages to the backend in a
 * fire-and-forget fashion using a small dedicated thread pool.
 */
public class HttpPublisher {
    private final HttpClient httpClient;
    private final ExecutorService executor;
    private final URI publishUri;
    private final AtomicLong sequenceCounter = new AtomicLong(0);
    private final AtomicReference<JsonObject> pendingSnapshot = new AtomicReference<>();
    private final AtomicBoolean snapshotInFlight = new AtomicBoolean(false);

    // For rate calculation
    private long rateWindowStart = System.currentTimeMillis();
    private long rateWindowCount = 0;

    public HttpPublisher() {
        this.executor = Executors.newFixedThreadPool(
                SpotlightConfig.HTTP_THREAD_POOL_SIZE,
                r -> {
                    Thread t = new Thread(r, "spotlight-http-publisher");
                    t.setDaemon(true);
                    return t;
                }
        );
        this.httpClient = HttpClient.newBuilder()
                .executor(executor)
                .connectTimeout(Duration.ofSeconds(5))
                // Uvicorn serves HTTP/1.1. Avoid a cleartext HTTP/2 upgrade attempt losing the
                // request body when the development server falls back to HTTP/1.1.
                .version(HttpClient.Version.HTTP_1_1)
                .build();
        this.publishUri = URI.create(
                SpotlightConfig.BACKEND_BASE_URL + SpotlightConfig.PUBLISH_ENDPOINT
        );
    }

    /**
     * Publishes a JSON message asynchronously. The message is wrapped with
     * a sequence number and session ID before sending.
     */
    public void publish(JsonObject message) {
        if (isWorldSnapshot(message)) {
            // Retain only the newest unsent snapshot. Events and conversations still
            // bypass this path and are sent immediately.
            pendingSnapshot.set(message.deepCopy());
            drainSnapshots();
            return;
        }
        send(message.deepCopy());
    }

    private void drainSnapshots() {
        if (!snapshotInFlight.compareAndSet(false, true)) {
            return;
        }
        sendNextSnapshot();
    }

    private void sendNextSnapshot() {
        JsonObject next = pendingSnapshot.getAndSet(null);
        if (next == null) {
            snapshotInFlight.set(false);
            // Close the small race where a producer writes after getAndSet but before
            // the in-flight flag is released.
            if (pendingSnapshot.get() != null) {
                drainSnapshots();
            }
            return;
        }
        send(next).whenComplete((ignored, failure) -> sendNextSnapshot());
    }

    private CompletableFuture<Void> send(JsonObject message) {
        long seq = sequenceCounter.incrementAndGet();
        message.addProperty("sequence", seq);
        message.addProperty("session_id", SpotlightConfig.SESSION_ID);

        byte[] json = message.toString().getBytes(StandardCharsets.UTF_8);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(publishUri)
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .version(HttpClient.Version.HTTP_1_1)
                .POST(HttpRequest.BodyPublishers.ofByteArray(json))
                .timeout(Duration.ofSeconds(10))
                .build();

        return httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenAccept(response -> {
                    SpotlightState state = SpotlightState.get();
                    state.sequenceNumber = seq;
                    state.lastPublishTimeMs = System.currentTimeMillis();
                    state.lastError = null;
                    updateRate();

                    if (response.statusCode() >= 400) {
                        String responseBody = response.body() == null ? "" : response.body().trim();
                        if (responseBody.length() > 160) {
                            responseBody = responseBody.substring(0, 160) + "...";
                        }
                        String err = "HTTP " + response.statusCode()
                                + (responseBody.isEmpty() ? "" : ": " + responseBody);
                        DynamicLlmLod.LOGGER.warn("Spotlight publish got {}", err);
                        state.lastError = err;
                    }
                })
                .exceptionally(ex -> {
                    SpotlightState state = SpotlightState.get();
                    state.lastError = ex.getMessage();
                    DynamicLlmLod.LOGGER.debug("Spotlight publish failed: {}", ex.getMessage());
                    return null;
                });
    }

    private static boolean isWorldSnapshot(JsonObject message) {
        return message.has("type")
                && message.get("type").isJsonPrimitive()
                && "world_snapshot".equals(message.get("type").getAsString());
    }

    private synchronized void updateRate() {
        rateWindowCount++;
        long now = System.currentTimeMillis();
        long elapsed = now - rateWindowStart;
        if (elapsed >= 1000) {
            SpotlightState.get().publishRateHz = rateWindowCount * 1000.0 / elapsed;
            rateWindowStart = now;
            rateWindowCount = 0;
        }
    }

    public void shutdown() {
        executor.shutdownNow();
    }
}
