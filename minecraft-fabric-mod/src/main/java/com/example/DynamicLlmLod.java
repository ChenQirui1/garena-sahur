package com.example;

import com.example.spotlight.SpotlightState;
import com.example.spotlight.command.BehaviourCommandHandler;
import com.example.spotlight.command.NpcController;
import com.example.spotlight.commands.SpotlightCommands;
import com.example.spotlight.conversation.ConversationPublisher;
import com.example.spotlight.event.GameEventPublisher;
import com.example.spotlight.snapshot.CandidateTracker;
import com.example.spotlight.snapshot.SnapshotScheduler;
import com.example.spotlight.speech.SpeechBubbleBroadcaster;
import com.example.spotlight.transport.HttpPublisher;
import com.example.spotlight.transport.WebSocketSubscriber;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.npc.Villager;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

public class DynamicLlmLod implements ModInitializer {
	public static final String MOD_ID = "dynamic-llm-lod";
	public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

	// Core components
	private HttpPublisher httpPublisher;
	private WebSocketSubscriber webSocketSubscriber;
	private CandidateTracker candidateTracker;
	private SnapshotScheduler snapshotScheduler;
	private GameEventPublisher gameEventPublisher;
	private ConversationPublisher conversationPublisher;
	private NpcController npcController;
	private SpeechBubbleBroadcaster speechBubbleBroadcaster;
	private BehaviourCommandHandler behaviourCommandHandler;

	@Override
	public void onInitialize() {
		LOGGER.info("Spotlight mod initializing...");

		// Create components
		httpPublisher = new HttpPublisher();
		webSocketSubscriber = new WebSocketSubscriber();
		candidateTracker = new CandidateTracker();
		snapshotScheduler = new SnapshotScheduler(candidateTracker, httpPublisher);
		gameEventPublisher = new GameEventPublisher(httpPublisher);
		conversationPublisher = new ConversationPublisher(httpPublisher);
		speechBubbleBroadcaster = new SpeechBubbleBroadcaster();
		npcController = new NpcController(speechBubbleBroadcaster);
		behaviourCommandHandler = new BehaviourCommandHandler(
				candidateTracker,
				npcController,
				conversationPublisher,
				gameEventPublisher
		);

		// Wire up WebSocket message handler
		ServerLifecycleEvents.SERVER_STARTED.register(this::onServerStarted);
		ServerLifecycleEvents.SERVER_STOPPING.register(this::onServerStopping);

		// Register tick handler
		ServerTickEvents.END_SERVER_TICK.register(this::onServerTick);

		// Publish meaningful world interactions as durable game events.
		AttackEntityCallback.EVENT.register((player, level, hand, entity, hitResult) -> {
			if (!level.isClientSide && entity instanceof Villager villager) {
				gameEventPublisher.publish(
						"villager_attacked",
						player.getUUID(),
						villager.getUUID(),
						"Player attacked a villager"
				);
			}
			return InteractionResult.PASS;
		});

		// Register slash commands
		CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) -> {
			SpotlightCommands commands = new SpotlightCommands(
					gameEventPublisher,
					conversationPublisher,
					candidateTracker,
					webSocketSubscriber
			);
			commands.register(dispatcher);
		});

		SpotlightState.get().initialized = true;
		LOGGER.info("Spotlight mod initialized");
	}

	private void onServerStarted(MinecraftServer server) {
		LOGGER.info("Spotlight: Server started, configuring WebSocket handler");
		webSocketSubscriber.setMessageHandler(server, behaviourCommandHandler::handle);
	}

	private void onServerStopping(MinecraftServer server) {
		LOGGER.info("Spotlight: Server stopping, cleaning up");
		webSocketSubscriber.disconnect();
		httpPublisher.shutdown();
		candidateTracker.clear();
		behaviourCommandHandler.clearAcceptanceState();
	}

	private void onServerTick(MinecraftServer server) {
		// Get the first player in the overworld (single-player prototype)
		ServerLevel overworld = server.overworld();
		List<ServerPlayer> players = overworld.players();
		if (players.isEmpty()) {
			return;
		}

		ServerPlayer player = players.get(0);

		// Tick the snapshot scheduler (handles candidate tracking + periodic publishing)
		snapshotScheduler.tick(player);

		// Tick NPC controller (handles dialogue expiry)
		npcController.tick(overworld);
	}

	public static ResourceLocation id(String path) {
		return new ResourceLocation(MOD_ID, path);
	}
}
