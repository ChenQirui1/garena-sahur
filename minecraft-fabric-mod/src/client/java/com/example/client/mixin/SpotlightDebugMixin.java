package com.example.client.mixin;

import com.example.spotlight.SpotlightState;
import net.minecraft.client.gui.components.DebugScreenOverlay;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.util.List;

/**
 * Mixin into DebugScreenOverlay to add Spotlight status information
 * to the right side of the F3 debug screen.
 */
@Mixin(DebugScreenOverlay.class)
public class SpotlightDebugMixin {

    @Inject(method = "getSystemInformation", at = @At("RETURN"))
    private void addSpotlightInfo(CallbackInfoReturnable<List<String>> cir) {
        List<String> lines = cir.getReturnValue();
        SpotlightState state = SpotlightState.get();

        lines.add("");
        lines.add("[Spotlight]");
        lines.add("Status: " + (state.initialized ? "Active" : "Inactive"));
        lines.add("WS: " + (state.webSocketConnected ? "Connected" : "Disconnected"));
        lines.add("Candidates: " + state.candidateCount);
        lines.add("Seq #: " + state.sequenceNumber);
        lines.add(String.format("Publish: %.1f Hz", state.publishRateHz));
        lines.add("Backend: " + state.backendUrl);

        if (state.lastError != null) {
            lines.add("Error: " + state.lastError);
        }
    }
}
