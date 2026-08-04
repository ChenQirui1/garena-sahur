# Benchmark Methodology

Baselines, metrics and test procedure.

**Owners:** Elson & Daniel.

This file is the place to record how Spotlight's routing is measured and what it is
measured against:

- The baseline being compared (every NPC on the strong model, no routing).
- The NPC counts under test — 10, 25, 50 and 100.
- Metrics collected: model calls, tokens, latency, fallbacks, tier switches, capacity
  usage, actual routed cost, and projected baseline cost.
- How a run is made repeatable, so a result can be reproduced rather than re-observed.

Dashboard figures and benchmark charts must come from the same telemetry records.
