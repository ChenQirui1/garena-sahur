package com.example.spotlight.transport;

import com.example.DynamicLlmLod;
import com.example.spotlight.SpotlightConfig;
import com.example.spotlight.SpotlightState;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonSyntaxException;
import net.minecraft.server.MinecraftServer;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.function.BiConsumer;

/**
 * WebSocket client that connects to the backend and receives behaviour commands.
 * Messages are dispatched to the server thread for safe entity manipulation.
 */
public class WebSocketSubscriber {
    private final HttpClient httpClient;
    private volatile WebSocket webSocket;
    private volatile boolean intentionalClose = false;

    // Handler: receives (server, jsonMessage)
    private BiConsumer<MinecraftServer, JsonObject> messageHandler;
    private MinecraftServer server;

    public WebSocketSubscriber() {
        this.httpClient = HttpClient.newBuilder()
                .build();
    }

    /**
     * Sets the handler that will be called on the server thread for each received message.
     */
    public void setMessageHandler(MinecraftServer server, BiConsumer<MinecraftServer, JsonObject> handler) {
        this.server = server;
        this.messageHandler = handler;
    }

    /**
     * Connects to the WebSocket endpoint.
     */
    public void connect() {
        if (webSocket != null) {
            DynamicLlmLod.LOGGER.warn("WebSocket already connected, disconnecting first");
            disconnect();
        }

        intentionalClose = false;
        URI wsUri = URI.create(SpotlightConfig.WEBSOCKET_ENDPOINT
                + "?session_id=" + SpotlightConfig.SESSION_ID);

        DynamicLlmLod.LOGGER.info("Spotlight connecting to WebSocket: {}", wsUri);

        httpClient.newWebSocketBuilder()
                .buildAsync(wsUri, new SpotlightWebSocketListener())
                .thenAccept(ws -> {
                    this.webSocket = ws;
                    SpotlightState.get().webSocketConnected = true;
                    DynamicLlmLod.LOGGER.info("Spotlight WebSocket connected");
                })
                .exceptionally(ex -> {
                    SpotlightState.get().webSocketConnected = false;
                    SpotlightState.get().lastError = "WS connect failed: " + ex.getMessage();
                    DynamicLlmLod.LOGGER.warn("Spotlight WebSocket connection failed: {}", ex.getMessage());
                    return null;
                });
    }

    /**
     * Disconnects from the WebSocket.
     */
    public void disconnect() {
        intentionalClose = true;
        WebSocket ws = this.webSocket;
        if (ws != null) {
            ws.sendClose(WebSocket.NORMAL_CLOSURE, "client disconnect")
                    .exceptionally(ex -> null);
            this.webSocket = null;
        }
        SpotlightState.get().webSocketConnected = false;
    }

    public boolean isConnected() {
        return webSocket != null && SpotlightState.get().webSocketConnected;
    }

    private class SpotlightWebSocketListener implements WebSocket.Listener {
        private final StringBuilder messageBuffer = new StringBuilder();

        @Override
        public void onOpen(WebSocket webSocket) {
            DynamicLlmLod.LOGGER.debug("WebSocket onOpen");
            webSocket.request(1);
        }

        @Override
        public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
            messageBuffer.append(data);
            if (last) {
                String fullMessage = messageBuffer.toString();
                messageBuffer.setLength(0);
                handleMessage(fullMessage);
            }
            webSocket.request(1);
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public CompletionStage<?> onPing(WebSocket webSocket, ByteBuffer message) {
            webSocket.request(1);
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public CompletionStage<?> onPong(WebSocket webSocket, ByteBuffer message) {
            webSocket.request(1);
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
            SpotlightState.get().webSocketConnected = false;
            WebSocketSubscriber.this.webSocket = null;
            if (!intentionalClose) {
                DynamicLlmLod.LOGGER.warn("Spotlight WebSocket closed unexpectedly: {} {}", statusCode, reason);
            }
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public void onError(WebSocket webSocket, Throwable error) {
            SpotlightState.get().webSocketConnected = false;
            SpotlightState.get().lastError = "WS error: " + error.getMessage();
            DynamicLlmLod.LOGGER.warn("Spotlight WebSocket error: {}", error.getMessage());
            WebSocketSubscriber.this.webSocket = null;
        }

        private void handleMessage(String rawJson) {
            if (messageHandler == null || server == null) {
                DynamicLlmLod.LOGGER.warn("Received WS message but no handler set");
                return;
            }

            if (rawJson.getBytes(StandardCharsets.UTF_8).length > SpotlightConfig.MAX_COMMAND_MESSAGE_BYTES) {
                DynamicLlmLod.LOGGER.warn("Dropping oversized WebSocket command message");
                return;
            }

            try {
                JsonObject json = JsonParser.parseString(rawJson).getAsJsonObject();
                // Dispatch to server thread for safe entity manipulation
                server.execute(() -> {
                    try {
                        messageHandler.accept(server, json);
                    } catch (Exception e) {
                        DynamicLlmLod.LOGGER.error("Error handling WS message on server thread", e);
                    }
                });
            } catch (JsonSyntaxException e) {
                DynamicLlmLod.LOGGER.warn("Invalid JSON from WebSocket: {}", e.getMessage());
            }
        }
    }
}
