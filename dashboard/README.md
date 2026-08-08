# Spotlight observability dashboard

A dependency-free, read-only dashboard for routing, model-call, cost, and benchmark
telemetry. It never writes telemetry and does not send selected files anywhere.

## Run locally

```bash
npm test
npm run build
npm run preview
```

Then open <http://127.0.0.1:4173/dashboard/dist/>. The local preview server exposes only the
built dashboard and `/data/benchmark_runs/dashboard.json`; it does not make the rest of the
repository browsable. If that file is absent or the built page is opened directly, use
**Choose JSON file**. The selected payload remains in the browser tab.

`npm run check` runs both the unit tests and the production build. Build output is written to
`dashboard/dist/` and is intentionally ignored.

## Accepted data

The normalizer accepts the generated benchmark payload (`runs[].metrics`, `runs[].costs`, and
`comparison`) plus a current routing result and model-call records when those are present.
Useful top-level fields are:

```json
{
  "schema_version": "1.0",
  "payload_type": "benchmark_dashboard",
  "generated_at_ms": 1786208500984,
  "current_routing": {
    "session_id": "demo-01",
    "sequence": 1842,
    "counts": {"focused": 2, "reactive": 5, "ambient": 18},
    "diagnostics": {
      "candidate_count": 25,
      "focused_capacity": 2,
      "reactive_capacity": 6,
      "routing_time_ms": 0.42
    },
    "assignments": []
  },
  "model_calls": [],
  "costs": {
    "currency": "USD",
    "estimated_routed_cost": 0.0142,
    "actual_routed_cost": null,
    "projected_all_strong_cost": 0.0621,
    "projection_trigger_scope": "observed_triggers_with_model_call_facts"
  },
  "runs": []
}
```

Unknown fields are ignored. Missing metrics render as **Not recorded**, rather than as zero.
