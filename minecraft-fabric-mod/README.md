# Dynamic LLM LOD

Fabric 1.20.1 prototype that publishes nearby-villager perception data to the
Spotlight backend and applies short-lived NPC dialogue and movement commands.

## Runtime behavior

- Candidate villagers enter at 24 blocks and leave at 28 blocks.
- World snapshots publish at 5 Hz. Only the newest waiting snapshot is retained
  while an HTTP request is in flight, preventing stale-state queueing.
- `viewport_center_distance` is always in the backend-safe `0..1` range;
  `inside_viewport` distinguishes the FOV edge from an off-screen NPC.
- Dialogue commands use a Fabric packet and client renderer to display temporary
  speech bubbles without changing the villager's custom name.

Run the mock backend first, then start the `runClient` configuration in IntelliJ.
After rebuilding the mod, stop and restart `runClient`; Fabric does not hot-reload
Java classes.

## Attribution

The speech-bubble feature is inspired by [Notable Bubble Text (NBT)](https://modrinth.com/mod/nbt)
by Mrbysco, licensed under the MIT License. This project uses an independent
Fabric implementation and does not bundle NBT source code. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

See [LICENSE](LICENSE).
