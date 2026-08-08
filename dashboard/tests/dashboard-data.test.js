import assert from "node:assert/strict";
import test from "node:test";

import {
  nearestRankPercentile,
  normalizeDashboardData,
  percentile,
} from "../src/dashboard-data.js";

test("normalizes append-only routing and model-call records without losing decision fields", () => {
  const payload = {
    schema_version: "1.0",
    generated_at_ms: 1_786_208_500_984,
    records: [
      {
        schema_version: "1.0",
        record_type: "routing_result",
        session_id: "demo-01",
        world_id: "market",
        sequence: 42,
        timestamp_ms: 1_786_208_500_300,
        assignments: [
          {
            npc_id: "shopkeeper-uuid",
            npc_name: "Mira",
            tier: "focused",
            previous_tier: "reactive",
            changed: true,
            reasons: ["active_conversation_target", "gaze_propagation"],
            direct_score: 0.82,
            propagated_score: 0.11,
            final_score: 0.93,
          },
          {
            npc_id: "guard-uuid",
            tier: "ambient",
            previous_tier: null,
            changed: false,
            reasons: ["ambient_default"],
            direct_score: 0.12,
            propagated_score: 0,
            final_score: 0.12,
          },
        ],
        counts: { focused: 1, reactive: 0, ambient: 1 },
        diagnostics: {
          focused_capacity: 2,
          reactive_capacity: 6,
          candidate_count: 2,
          routing_time_ms: 0.42,
        },
      },
      {
        schema_version: "1.0",
        record_type: "model_call",
        session_id: "demo-01",
        request_id: "request-1",
        npc_id: "shopkeeper-uuid",
        tier: "focused",
        provider: "mock",
        model: "deterministic-v1",
        source_sequence: 42,
        started_at_ms: 1_786_208_500_300,
        completed_at_ms: 1_786_208_500_984,
        latency_ms: 684,
        input_tokens: 231,
        output_tokens: 34,
        status: "success",
        fallback_used: false,
        error_code: null,
      },
    ],
  };

  const normalized = normalizeDashboardData(payload);

  assert.equal(normalized.candidateCount, 2);
  assert.deepEqual(normalized.counts, { focused: 1, reactive: 0, ambient: 1 });
  assert.deepEqual(normalized.capacities, { focused: 2, reactive: 6, ambient: null });
  assert.equal(normalized.assignments[0].name, "Mira");
  assert.equal(normalized.assignments[0].previousTier, "reactive");
  assert.equal(normalized.assignments[0].propagatedScore, 0.11);
  assert.deepEqual(normalized.assignments[0].reasons, [
    "active_conversation_target",
    "gaze_propagation",
  ]);
  assert.equal(normalized.modelCalls[0].totalTokens, 265);
  assert.equal(normalized.metrics.medianLatencyMs, 684);
  assert.equal(normalized.metrics.p95LatencyMs, 684);
  assert.equal(normalized.metrics.tierSwitches, 1);
  assert.equal(normalized.context.sequence, 42);
});

test("reads stable flat metric keys and nested cost summary blocks", () => {
  const normalized = normalizeDashboardData({
    metrics: {
      model_calls: 20,
      latency_median_ms: 125.5,
      latency_p95_ms: 490,
      fallbacks: 2,
      fallback_rate: 0.1,
      tier_switches: 8,
      routing_time_median_ms: 0.71,
    },
    costs: {
      pricing_version: "demo-v1",
      currency: "USD",
      actual_routed: { total_cost: "0.0142" },
      projected_baseline: { total_cost: "0.0621" },
    },
  });

  assert.equal(normalized.metrics.modelCalls, 20);
  assert.equal(normalized.metrics.medianLatencyMs, 125.5);
  assert.equal(normalized.metrics.p95LatencyMs, 490);
  assert.equal(normalized.metrics.routingTimeMs, 0.71);
  assert.equal(normalized.costs.actual, 0.0142);
  assert.equal(normalized.costs.projected, 0.0621);
  assert.equal(normalized.costs.estimated, null);
});

test("normalizes generated benchmark runs and comparison arrays", () => {
  const normalized = normalizeDashboardData({
    schema_version: "1.0",
    payload_type: "benchmark_dashboard",
    runs: [
      {
        run_id: "run-25",
        npc_count: 25,
        seed: 7,
        ticks: 30,
        provider_mode: "mock",
        metrics: {
          model_calls: 8,
          input_tokens: 1000,
          output_tokens: 200,
          total_tokens: 1200,
          latency_median_ms: 32,
          latency_p95_ms: 51,
          fallbacks: 1,
          fallback_rate: 0.125,
        },
        costs: {
          currency: "USD",
          actual_routed_cost: 0.01,
          projected_all_strong_cost: 0.04,
          savings_percent: 75,
        },
      },
    ],
    comparison: {
      npc_counts: [10, 25, 50, 100],
      model_calls: [3, 8, 15, 31],
      total_tokens: [400, 1200, 2400, 5000],
      latency_p95_ms: [20, 51, 80, 120],
      actual_routed_cost: [0.004, 0.01, 0.021, 0.043],
      projected_all_strong_cost: [0.01, 0.04, 0.08, 0.16],
      savings_percent: [60, 75, 73.75, 73.125],
    },
  });

  assert.deepEqual(
    normalized.benchmarks.map((run) => run.npcCount),
    [10, 25, 50, 100],
  );
  const run25 = normalized.benchmarks.find((run) => run.npcCount === 25);
  assert.equal(run25.runId, "run-25");
  assert.equal(run25.medianLatencyMs, 32);
  assert.equal(run25.p95LatencyMs, 51);
  assert.equal(run25.actualCost, 0.01);
  assert.equal(run25.projectedCost, 0.04);
});

test("keeps absent telemetry null instead of inventing zeroes", () => {
  const normalized = normalizeDashboardData({ schema_version: "1.0" });

  assert.equal(normalized.candidateCount, null);
  assert.deepEqual(normalized.counts, { focused: null, reactive: null, ambient: null });
  assert.equal(normalized.metrics.modelCalls, null);
  assert.equal(normalized.costs.actual, null);
  assert.equal(normalized.assignments.length, 0);
});

test("latency helpers use a true median and nearest-rank p95", () => {
  assert.equal(percentile([10, 20, 30, 40], 0.5), 25);
  assert.equal(nearestRankPercentile([10, 20, 30, 40], 0.95), 40);
  assert.equal(percentile([], 0.5), null);
});
