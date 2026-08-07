package com.example.client;

import com.example.client.speech.ClientSpeechBubbles;
import com.example.client.speech.SpeechBubbleRenderer;
import net.fabricmc.api.ClientModInitializer;

public class DynamicLlmLodClient implements ClientModInitializer {
	@Override
	public void onInitializeClient() {
		ClientSpeechBubbles.register();
		SpeechBubbleRenderer.register();
	}
}
