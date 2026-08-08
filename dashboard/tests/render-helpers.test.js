import assert from "node:assert/strict";
import test from "node:test";

import {
  benchmarkCostWidths,
  completeBenchmarkSeries,
} from "../src/benchmark_charts/benchmark-charts.js";
import { costCards, formatCurrency, formatPercent } from "../src/cost_metrics/cost-metrics.js";
import { readableReason, reasonSummary } from "../src/routing_reasons/routing-reasons.js";
import { tierRows } from "../src/tier_display/tier-display.js";

test("tier rows calculate capacity use and leave Ambient uncapped", () => {
  const rows = tierRows(
    { focused: 2, reactive: 3, ambient: 20 },
    { focused: 2, reactive: 6, ambient: null },
  );

  assert.equal(rows[0].utilizationPercent, 100);
  assert.equal(rows[1].utilizationPercent, 50);
  assert.equal(rows[2].utilization, null);
  assert.equal(rows[0].overCapacity, false);
});

test("reason helpers turn stable codes into concrete product copy", () => {
  assert.equal(readableReason("hysteresis_hold"), "Tier held to prevent rapid switching");
  assert.equal(
    reasonSummary(["active_conversation_target", "gaze_propagation", "capacity_limit"]),
    "Active conversation target · Gaze propagated from another NPC · +1 more",
  );
});

test("cost cards keep estimated, actual, and projected labels separate", () => {
  const cards = costCards({
    currency: "USD",
    estimated: 0.0042,
    actual: null,
    projected: 0.08,
    projectionTriggerScope: "observed_triggers_with_model_call_facts",
  });

  assert.deepEqual(
    cards.map((card) => card.label),
    ["Estimated routed cost", "Actual routed cost", "Projected all-strong baseline"],
  );
  assert.match(formatCurrency(0.0042, "USD"), /0\.0042/);
  assert.equal(formatCurrency(null, "USD"), "Not recorded");
  assert.equal(formatPercent(75), "75.0%");
  assert.match(cards[2].description, /zero-call triggers are not represented/);
});

test("benchmark helpers always expose the four required comparison sizes", () => {
  const series = completeBenchmarkSeries([
    { npcCount: 25, epochMs: 1, actualCost: 2, projectedCost: 4 },
    { npcCount: 100, epochMs: 1, actualCost: 5, projectedCost: 10 },
  ]);
  const widths = benchmarkCostWidths(series);

  assert.deepEqual(
    series.map((run) => run.npcCount),
    [10, 25, 50, 100],
  );
  assert.equal(series[0].missing, true);
  assert.equal(series[1].missing, undefined);
  assert.equal(widths[1].actual, 20);
  assert.equal(widths[3].projected, 100);
});
