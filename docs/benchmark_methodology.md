# Benchmark Methodology

**Owners:** Elson & Daniel

Spotlight has two evidence modes, and the output identifies which one produced every run:

- `synthetic_mock` is a deterministic regression and scaling experiment. It uses the repository's
  mock Minecraft publisher, real backend intake, real Attention Router, deterministic mock model,
  and append-only JSONL telemetry.
- `live` is a rehearsal or deployment capture. It measures real provider and machine behaviour and
  requires an explicitly supplied, versioned pricing document.

Synthetic and live runs must not be averaged together. Synthetic latency is useful for detecting
pipeline regressions, not for claiming provider latency.

## Standard experiment

The standard sweep is 10, 25, 50, and 100 starting NPCs. Each crowd runs the market-theft trace
with the same seed (`7`), epoch (`1786208500000` ms), tick rate (`5` Hz), and duration (`4` s).
The run ID encodes those inputs plus the pinned backend-settings revision. Benchmark settings are
materialized from the repository defaults and supplied explicitly, so a developer's `.env` or
`SPOTLIGHT_*` deployment values cannot silently change a run. The mock publisher is executed as a
separate read-only upstream component; its scenario is not copied into telemetry code.

The benchmark enters the Python backend through its existing adapters:

1. The mock publisher writes canonical HTTP/JSONL envelopes locally. No NATS server or broker is
   used.
2. The existing JSONL intake validates one envelope at a time.
3. The existing pipeline is injected with one persistent real `Router`, the JSONL telemetry sink,
   a deterministic clock, and the backend's mock provider.
4. The pipeline drains after each upstream record so a no-sleep positional snapshot cannot
   overtake durable event work solely because the fixture was emitted faster than real time.
5. Model-call facts are written at their normal telemetry boundary. Routing results are captured
   in memory and appended only after `Router.route` returns, preserving the Router's I/O-free
   contract.

The publisher currently emits no `attention_edges`. These runs exercise scoring, hysteresis,
capacity, orchestration, and telemetry scaling; graph-propagation behaviour remains covered by the
Router's focused graph tests until Minecraft publishes gaze edges.

## Repeatability and measured time

The seed and epoch make upstream observations, triggers, request identities, token estimates, and
model output reproducible. The synthetic clock advances deterministically and is labelled
`deterministic_synthetic_clock` in metadata. Router `routing_time_ms` deliberately remains a real
local `perf_counter` measurement, so exact timings may vary with the machine and system load.
Compare distributions and regressions, not byte equality of that field.

Every run writes three local artifacts beneath `data/benchmark_runs/` by default:

- `*.source.jsonl`: replayable canonical upstream messages;
- `*.telemetry.jsonl`: the provenance record, model-call records, and routing-result records;
- `*.sqlite3`: isolated durable backend state for that run.

Existing deterministic run IDs are refused unless `--overwrite` is supplied. Overwrite removes
only those exact three artifacts, never the directory or unrelated runs.

## Metrics

`backend.telemetry.metrics.summarize_records` is the only aggregation implementation used by both
the generated dashboard payload and benchmark comparison. It reports:

- model calls and input/output/total tokens;
- median and nearest-rank p95 model latency;
- error and fallback counts/rates;
- routing-result count and tier switches by transition;
- current tier assignments and Focused/Reactive capacity use;
- median and nearest-rank p95 Router time.

Nearest-rank p95 is used so a small run reports an observed latency rather than an interpolated
number that never occurred.

## Actual routed cost and projected baseline

`actual_routed_cost` prices only recorded model calls and tokens. It is measured routed usage,
although a mock run's price is a normalized comparison unit rather than money.

`projected_all_strong_cost` is a counterfactual, not spend. For each observed event trigger that
produced at least one model-call fact, the baseline projects one strong-model call per candidate
reported by the matching session and routing sequence. A conversation turn stays one targeted
call. Because suppressed Ambient NPCs correctly have no prompt/token record, their per-call tokens
are estimated from the ceiling of that trigger's observed average. The injected strong-model rate
is then applied to those projected totals. The output labels the projection basis and observation
scope separately and includes the pricing version/currency.

The current shared telemetry contract has no standalone generation-trigger fact. An event that
produced zero model calls is therefore absent from this projection rather than guessed. Add that
fact at the jointly agreed telemetry boundary before claiming a baseline over *all* inbound
triggers; until then, dashboard output is labelled `observed_triggers_with_model_call_facts`.

The default mock table is deliberately non-financial:

- `mock-focused`: 1.0 normalized unit per million input or output tokens;
- `mock-reactive`: 0.25 normalized unit per million input or output tokens.

Those weights show relative routing behaviour and are not vendor quotes. Live captures require a
versioned JSON pricing table; the chart command refuses to apply mock weights to live metadata.

## Running the suite

From the repository root:

```powershell
python scripts/run_benchmark.py
python scripts/generate_charts.py
```

Useful reproducibility controls:

```powershell
python scripts/run_benchmark.py --npc-counts 10 25 50 100 `
  --seed 7 --epoch-ms 1786208500000 --rate 5 --duration 4 --overwrite

python scripts/generate_charts.py --pricing path/to/versioned-pricing.json `
  --strong-provider openai --strong-model your-strong-model
```

`generate_charts.py` produces `data/benchmark_runs/dashboard.json`. It does not calculate its own
metrics: it reads the same telemetry JSONL, delegates aggregation and costing to
`backend.telemetry`, then creates comparison arrays for the dashboard/chart renderer.

## Is `mock-publisher` still needed?

Yes, for now. It is the deterministic upstream fixture used by the benchmark and the existing
publisher contract smoke test, and it is also useful for local HTTP/JSONL demos without Minecraft.
Removing it now would break those surfaces. It can be removed later only after a replacement can
produce the same canonical, repeatable fixture and the benchmark/tests are migrated to it. This
benchmark does not modify any file in `mock-publisher`.
