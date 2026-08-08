const KNOWN_TIERS = new Set(["focused", "reactive", "ambient"]);

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function objectOrEmpty(value) {
  return isObject(value) ? value : {};
}

function arrayOrEmpty(value) {
  return Array.isArray(value) ? value : [];
}

function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null);
}

export function numberOrNull(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function integerOrNull(value) {
  const parsed = numberOrNull(value);
  return parsed === null ? null : Math.max(0, Math.trunc(parsed));
}

function stringOrNull(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function booleanOrNull(value) {
  if (typeof value === "boolean") return value;
  if (value === 1 || value === "true") return true;
  if (value === 0 || value === "false") return false;
  return null;
}

function tierOrNull(value) {
  const tier = stringOrNull(value)?.toLowerCase() ?? null;
  return tier && KNOWN_TIERS.has(tier) ? tier : null;
}

function timestampOrNull(value) {
  const numeric = numberOrNull(value);
  if (numeric !== null) return numeric;
  const text = stringOrNull(value);
  if (text === null) return null;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function humanizeIdentifier(identifier) {
  const value = stringOrNull(identifier);
  if (value === null) return "Unknown NPC";
  const withoutUuid = value.replace(/-[0-9a-f]{8,}$/i, "");
  return withoutUuid
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function normalizeReasons(value) {
  if (Array.isArray(value)) {
    return value.map(stringOrNull).filter((reason) => reason !== null);
  }
  const single = stringOrNull(value);
  return single === null ? [] : [single];
}

function normalizeAssignment(value, index) {
  const assignment = objectOrEmpty(value);
  const npcId =
    stringOrNull(firstDefined(assignment.npc_id, assignment.npcId, assignment.id)) ??
    `unknown-npc-${index + 1}`;
  const previousTier = tierOrNull(firstDefined(assignment.previous_tier, assignment.previousTier));
  const tier = tierOrNull(firstDefined(assignment.tier, assignment.current_tier, assignment.currentTier));
  const explicitChanged = booleanOrNull(assignment.changed);

  return {
    npcId,
    name:
      stringOrNull(
        firstDefined(assignment.npc_name, assignment.npcName, assignment.display_name, assignment.name),
      ) ?? humanizeIdentifier(npcId),
    tier,
    previousTier,
    changed: explicitChanged ?? (previousTier !== null && tier !== null && previousTier !== tier),
    reasons: normalizeReasons(firstDefined(assignment.reasons, assignment.reason)),
    directScore: numberOrNull(firstDefined(assignment.direct_score, assignment.directScore)),
    propagatedScore: numberOrNull(
      firstDefined(assignment.propagated_score, assignment.propagatedScore),
    ),
    finalScore: numberOrNull(firstDefined(assignment.final_score, assignment.finalScore, assignment.score)),
  };
}

function latestRecord(records) {
  return records.reduce((latest, current) => {
    if (!latest) return current;
    const latestTime = numberOrNull(
      firstDefined(latest.timestamp_ms, latest.completed_at_ms, latest.started_at_ms),
    );
    const currentTime = numberOrNull(
      firstDefined(current.timestamp_ms, current.completed_at_ms, current.started_at_ms),
    );
    if (latestTime !== null && currentTime !== null && currentTime !== latestTime) {
      return currentTime > latestTime ? current : latest;
    }
    const latestSequence = numberOrNull(latest.sequence);
    const currentSequence = numberOrNull(current.sequence);
    if (latestSequence !== null && currentSequence !== null) {
      return currentSequence >= latestSequence ? current : latest;
    }
    return current;
  }, null);
}

function normalizeModelCall(value, index) {
  const call = objectOrEmpty(value);
  const inputTokens = integerOrNull(firstDefined(call.input_tokens, call.inputTokens));
  const outputTokens = integerOrNull(firstDefined(call.output_tokens, call.outputTokens));
  const explicitTotal = integerOrNull(firstDefined(call.total_tokens, call.totalTokens, call.tokens));
  const totalTokens =
    explicitTotal ??
    (inputTokens !== null && outputTokens !== null ? inputTokens + outputTokens : null);
  const startedAtMs = timestampOrNull(firstDefined(call.started_at_ms, call.startedAtMs));
  const completedAtMs = timestampOrNull(
    firstDefined(call.completed_at_ms, call.completedAtMs, call.timestamp_ms, call.timestamp),
  );
  const recordedLatency = numberOrNull(firstDefined(call.latency_ms, call.latencyMs));
  const derivedLatency =
    startedAtMs !== null && completedAtMs !== null ? completedAtMs - startedAtMs : null;

  return {
    requestId:
      stringOrNull(firstDefined(call.request_id, call.requestId, call.id)) ?? `model-call-${index + 1}`,
    npcId: stringOrNull(firstDefined(call.npc_id, call.npcId)) ?? "unknown-npc",
    npcName:
      stringOrNull(firstDefined(call.npc_name, call.npcName, call.name)) ??
      humanizeIdentifier(firstDefined(call.npc_id, call.npcId)),
    tier: tierOrNull(call.tier),
    provider: stringOrNull(call.provider),
    model: stringOrNull(call.model),
    startedAtMs,
    completedAtMs,
    latencyMs: recordedLatency ?? (derivedLatency !== null && derivedLatency >= 0 ? derivedLatency : null),
    inputTokens,
    outputTokens,
    totalTokens,
    status: stringOrNull(call.status)?.toLowerCase() ?? "unknown",
    fallbackUsed: booleanOrNull(firstDefined(call.fallback_used, call.fallbackUsed)) ?? false,
    errorCode: stringOrNull(firstDefined(call.error_code, call.errorCode)),
  };
}

export function percentile(values, quantile) {
  const usable = arrayOrEmpty(values)
    .map(numberOrNull)
    .filter((value) => value !== null)
    .sort((left, right) => left - right);
  if (usable.length === 0) return null;
  const clamped = Math.min(1, Math.max(0, numberOrNull(quantile) ?? 0));
  const position = (usable.length - 1) * clamped;
  const lowerIndex = Math.floor(position);
  const upperIndex = Math.ceil(position);
  if (lowerIndex === upperIndex) return usable[lowerIndex];
  const weight = position - lowerIndex;
  return usable[lowerIndex] * (1 - weight) + usable[upperIndex] * weight;
}

export function nearestRankPercentile(values, quantile) {
  const usable = arrayOrEmpty(values)
    .map(numberOrNull)
    .filter((value) => value !== null)
    .sort((left, right) => left - right);
  if (usable.length === 0) return null;
  const clamped = Math.min(1, Math.max(Number.EPSILON, numberOrNull(quantile) ?? 0.95));
  return usable[Math.max(0, Math.ceil(clamped * usable.length) - 1)];
}

function normalizeCosts(value, fallbackCurrency = "USD") {
  const costs = objectOrEmpty(value);
  const actualBlock = objectOrEmpty(costs.actual_routed);
  const projectedBlock = objectOrEmpty(costs.projected_baseline);
  const actual = numberOrNull(
    firstDefined(
      actualBlock.total_cost,
      costs.actual_routed_cost,
      costs.actual_routed_cost_usd,
      costs.actual_cost,
      costs.actual,
    ),
  );
  const projected = numberOrNull(
    firstDefined(
      projectedBlock.total_cost,
      costs.projected_all_strong_cost,
      costs.projected_baseline_cost,
      costs.projected_baseline_cost_usd,
      costs.projected_cost,
      costs.projected,
    ),
  );
  const estimated = numberOrNull(
    firstDefined(
      costs.estimated_routed_cost,
      costs.estimated_routed_cost_usd,
      costs.estimated_cost,
      costs.estimated,
    ),
  );
  const explicitSavings = numberOrNull(firstDefined(costs.savings, costs.cost_savings));
  const explicitSavingsPercent = numberOrNull(
    firstDefined(costs.savings_percent, costs.savings_pct, costs.cost_savings_percent),
  );
  const calculatedSavings = actual !== null && projected !== null ? projected - actual : null;
  const calculatedSavingsPercent =
    calculatedSavings !== null && projected !== null && projected > 0
      ? (calculatedSavings / projected) * 100
      : null;

  return {
    currency: stringOrNull(costs.currency)?.toUpperCase() ?? fallbackCurrency,
    estimated,
    actual,
    projected,
    savings: explicitSavings ?? calculatedSavings,
    savingsPercent: explicitSavingsPercent ?? calculatedSavingsPercent,
    projectionTriggerScope: stringOrNull(
      firstDefined(costs.projection_trigger_scope, costs.projectionTriggerScope),
    ),
  };
}

function normalizeBenchmarkRun(value, index) {
  const run = objectOrEmpty(value);
  const metrics = objectOrEmpty(run.metrics);
  const latency = objectOrEmpty(firstDefined(metrics.latency_ms, metrics.latency, run.latency_ms));
  const routingTime = objectOrEmpty(
    firstDefined(metrics.routing_time_ms, metrics.routing_time, run.routing_time_ms),
  );
  const costs = normalizeCosts(firstDefined(run.costs, run.cost, {}));
  const inputTokens = integerOrNull(firstDefined(metrics.input_tokens, run.input_tokens));
  const outputTokens = integerOrNull(firstDefined(metrics.output_tokens, run.output_tokens));
  const totalTokens =
    integerOrNull(firstDefined(metrics.total_tokens, run.total_tokens)) ??
    (inputTokens !== null && outputTokens !== null ? inputTokens + outputTokens : null);

  return {
    npcCount: integerOrNull(firstDefined(run.npc_count, run.npcCount, run.candidates)),
    runId: stringOrNull(firstDefined(run.run_id, run.runId)) ?? `benchmark-${index + 1}`,
    epochMs: timestampOrNull(firstDefined(run.epoch_ms, run.generated_at_ms, run.timestamp_ms)),
    seed: integerOrNull(run.seed),
    ticks: integerOrNull(run.ticks),
    sourceType: stringOrNull(firstDefined(run.source_type, run.sourceType)),
    providerMode: stringOrNull(firstDefined(run.provider_mode, run.providerMode)),
    modelCalls: integerOrNull(firstDefined(metrics.model_calls, run.model_calls, run.calls)),
    inputTokens,
    outputTokens,
    totalTokens,
    medianLatencyMs: numberOrNull(
      firstDefined(
        latency.median,
        latency.p50,
        metrics.latency_median_ms,
        metrics.median_latency_ms,
        run.median_latency_ms,
      ),
    ),
    p95LatencyMs: numberOrNull(
      firstDefined(
        latency.p95,
        metrics.latency_p95_ms,
        metrics.p95_latency_ms,
        run.p95_latency_ms,
      ),
    ),
    fallbacks: integerOrNull(firstDefined(metrics.fallbacks, run.fallbacks)),
    fallbackRate: numberOrNull(firstDefined(metrics.fallback_rate, run.fallback_rate)),
    errors: integerOrNull(firstDefined(metrics.errors, run.errors)),
    tierSwitches: integerOrNull(firstDefined(metrics.tier_switches, run.tier_switches)),
    medianRoutingTimeMs: numberOrNull(
      firstDefined(
        routingTime.median,
        routingTime.p50,
        metrics.routing_time_median_ms,
        metrics.median_routing_time_ms,
      ),
    ),
    p95RoutingTimeMs: numberOrNull(
      firstDefined(routingTime.p95, metrics.routing_time_p95_ms, metrics.p95_routing_time_ms),
    ),
    currency: costs.currency,
    estimatedCost: costs.estimated,
    actualCost: costs.actual,
    projectedCost: costs.projected,
    savings: costs.savings,
    savingsPercent: costs.savingsPercent,
    projectionTriggerScope: costs.projectionTriggerScope,
    isComparisonOnly: Boolean(run.isComparisonOnly),
  };
}

function comparisonRuns(value) {
  const comparison = objectOrEmpty(value);
  const npcCounts = arrayOrEmpty(firstDefined(comparison.npc_counts, comparison.npcCounts));
  const fields = {
    model_calls: arrayOrEmpty(firstDefined(comparison.model_calls, comparison.modelCalls)),
    total_tokens: arrayOrEmpty(firstDefined(comparison.total_tokens, comparison.totalTokens)),
    p95_latency_ms: arrayOrEmpty(
      firstDefined(comparison.latency_p95_ms, comparison.p95_latency_ms, comparison.p95LatencyMs),
    ),
    actual_routed_cost: arrayOrEmpty(
      firstDefined(comparison.actual_routed_cost, comparison.actualRoutedCost),
    ),
    projected_all_strong_cost: arrayOrEmpty(
      firstDefined(comparison.projected_all_strong_cost, comparison.projectedAllStrongCost),
    ),
    savings_percent: arrayOrEmpty(
      firstDefined(comparison.savings_percent, comparison.savingsPercent),
    ),
  };

  return npcCounts.map((npcCount, index) =>
    normalizeBenchmarkRun(
      {
        run_id: `comparison-${npcCount}`,
        npc_count: npcCount,
        isComparisonOnly: true,
        metrics: {
          model_calls: fields.model_calls[index],
          total_tokens: fields.total_tokens[index],
          latency_ms: { p95: fields.p95_latency_ms[index] },
        },
        costs: {
          actual_routed_cost: fields.actual_routed_cost[index],
          projected_all_strong_cost: fields.projected_all_strong_cost[index],
          savings_percent: fields.savings_percent[index],
          currency: comparison.currency,
        },
      },
      index,
    ),
  );
}

function mergeBenchmarkRuns(runs, comparisons) {
  const byNpcCount = new Map();
  for (const comparison of comparisons) {
    if (comparison.npcCount !== null) byNpcCount.set(comparison.npcCount, comparison);
  }
  for (const run of runs) {
    if (run.npcCount === null) continue;
    const existing = byNpcCount.get(run.npcCount);
    if (!existing || run.epochMs === null || existing.epochMs === null || run.epochMs >= existing.epochMs) {
      byNpcCount.set(run.npcCount, { ...existing, ...run, isComparisonOnly: false });
    }
  }
  return [...byNpcCount.values()].sort((left, right) => left.npcCount - right.npcCount);
}

function newestBenchmarkRun(runs) {
  return runs.reduce((latest, run) => {
    if (!latest) return run;
    if (run.epochMs !== null && latest.epochMs !== null) {
      return run.epochMs >= latest.epochMs ? run : latest;
    }
    return run;
  }, null);
}

/**
 * Convert generated dashboard payloads or record-oriented telemetry into one stable UI model.
 * Missing values intentionally remain null so the view cannot misreport "not recorded" as zero.
 */
export function normalizeDashboardData(payload) {
  if (!isObject(payload)) throw new TypeError("Dashboard JSON must contain an object at the top level.");
  const root = objectOrEmpty(firstDefined(payload.dashboard, payload));
  const telemetry = objectOrEmpty(root.telemetry);
  const summary = objectOrEmpty(firstDefined(root.summary, root.overview));
  const topMetrics = objectOrEmpty(firstDefined(root.metrics, summary.metrics));
  const records = arrayOrEmpty(firstDefined(root.records, root.telemetry_records, telemetry.records));
  const routingRecords = records.filter((record) => {
    const recordType = stringOrNull(objectOrEmpty(record).record_type)?.toLowerCase();
    return recordType === "routing_result" || recordType === "routing";
  });
  const explicitRouting = firstDefined(
    root.current_routing,
    root.latest_routing,
    root.routing_result,
    isObject(root.routing) ? root.routing : undefined,
    objectOrEmpty(root.latest).routing,
  );
  const routing = objectOrEmpty(explicitRouting ?? latestRecord(routingRecords));
  const rawAssignments = firstDefined(
    routing.assignments,
    root.routing_assignments,
    root.assignments,
    Array.isArray(root.routing) ? root.routing : undefined,
  );
  const hasAssignmentList = Array.isArray(rawAssignments);
  const assignments = arrayOrEmpty(rawAssignments).map(normalizeAssignment);

  const countSource = objectOrEmpty(
    firstDefined(routing.counts, root.tier_counts, summary.tier_counts, summary.counts),
  );
  const diagnostics = objectOrEmpty(
    firstDefined(routing.diagnostics, root.routing_diagnostics, summary.diagnostics),
  );
  const suppliedCapacities = objectOrEmpty(firstDefined(root.capacities, summary.capacities));
  const derivedTierCount = (tier) =>
    hasAssignmentList ? assignments.filter((assignment) => assignment.tier === tier).length : null;
  const counts = {
    focused: integerOrNull(countSource.focused) ?? derivedTierCount("focused"),
    reactive: integerOrNull(countSource.reactive) ?? derivedTierCount("reactive"),
    ambient: integerOrNull(countSource.ambient) ?? derivedTierCount("ambient"),
  };
  const summedCount = [counts.focused, counts.reactive, counts.ambient].every(
    (count) => count !== null,
  )
    ? counts.focused + counts.reactive + counts.ambient
    : null;
  const candidateCount =
    integerOrNull(
      firstDefined(
        diagnostics.candidate_count,
        routing.candidate_count,
        root.candidate_count,
        summary.candidate_count,
      ),
    ) ??
    summedCount ??
    (hasAssignmentList ? assignments.length : null);
  const capacities = {
    focused: integerOrNull(
      firstDefined(
        diagnostics.focused_capacity,
        suppliedCapacities.focused,
        suppliedCapacities.focused_capacity,
      ),
    ),
    reactive: integerOrNull(
      firstDefined(
        diagnostics.reactive_capacity,
        suppliedCapacities.reactive,
        suppliedCapacities.reactive_capacity,
      ),
    ),
    ambient: null,
  };

  const explicitCalls = firstDefined(root.model_calls, root.recent_model_calls, telemetry.model_calls);
  const modelCallRecords = records.filter(
    (record) => stringOrNull(objectOrEmpty(record).record_type)?.toLowerCase() === "model_call",
  );
  const modelCalls = arrayOrEmpty(explicitCalls ?? modelCallRecords)
    .map(normalizeModelCall)
    .sort((left, right) => (right.completedAtMs ?? right.startedAtMs ?? -1) - (left.completedAtMs ?? left.startedAtMs ?? -1));
  const latencies = modelCalls
    .map((call) => call.latencyMs)
    .filter((latency) => latency !== null && latency >= 0);
  const explicitCallCount = integerOrNull(
    firstDefined(topMetrics.model_calls, topMetrics.model_call_count, summary.model_calls),
  );
  const callCount = explicitCallCount ?? (explicitCalls !== undefined || modelCallRecords.length > 0 ? modelCalls.length : null);
  const fallbackCount =
    integerOrNull(firstDefined(topMetrics.fallbacks, topMetrics.fallback_count, summary.fallbacks)) ??
    (modelCalls.length > 0 ? modelCalls.filter((call) => call.fallbackUsed).length : null);
  const fallbackRate =
    numberOrNull(firstDefined(topMetrics.fallback_rate, summary.fallback_rate)) ??
    (fallbackCount !== null && callCount !== null && callCount > 0 ? fallbackCount / callCount : null);
  const latencySummary = objectOrEmpty(firstDefined(topMetrics.latency_ms, topMetrics.latency));
  const routingTimeSummary = objectOrEmpty(
    firstDefined(topMetrics.routing_time_ms, topMetrics.routing_time),
  );
  const metrics = {
    modelCalls: callCount,
    medianLatencyMs:
      numberOrNull(
        firstDefined(
          latencySummary.median,
          latencySummary.p50,
          topMetrics.latency_median_ms,
          topMetrics.median_latency_ms,
        ),
      ) ?? percentile(latencies, 0.5),
    p95LatencyMs:
      numberOrNull(
        firstDefined(latencySummary.p95, topMetrics.latency_p95_ms, topMetrics.p95_latency_ms),
      ) ?? nearestRankPercentile(latencies, 0.95),
    fallbacks: fallbackCount,
    fallbackRate,
    tierSwitches:
      integerOrNull(firstDefined(topMetrics.tier_switches, summary.tier_switches)) ??
      (hasAssignmentList ? assignments.filter((assignment) => assignment.changed).length : null),
    routingTimeMs: numberOrNull(
      firstDefined(
        diagnostics.routing_time_ms,
        routingTimeSummary.latest,
        routingTimeSummary.median,
        topMetrics.routing_time_median_ms,
        topMetrics.routing_time,
      ),
    ),
  };

  const runs = arrayOrEmpty(firstDefined(root.runs, root.benchmarks, root.benchmark_runs)).map(
    normalizeBenchmarkRun,
  );
  const comparisons = comparisonRuns(firstDefined(root.comparison, root.benchmark_comparison));
  const benchmarks = mergeBenchmarkRuns(runs, comparisons);
  const latestRun = newestBenchmarkRun(runs);
  const explicitCostSource = firstDefined(root.costs, root.cost_summary, summary.costs, routing.costs);
  const costs = explicitCostSource
    ? { ...normalizeCosts(explicitCostSource), scope: stringOrNull(objectOrEmpty(explicitCostSource).scope) }
    : latestRun
      ? {
          currency: latestRun.currency,
          estimated: latestRun.estimatedCost,
          actual: latestRun.actualCost,
          projected: latestRun.projectedCost,
          savings: latestRun.savings,
          savingsPercent: latestRun.savingsPercent,
          projectionTriggerScope: latestRun.projectionTriggerScope,
          scope: `Latest benchmark · ${latestRun.npcCount ?? "unknown"} NPCs`,
        }
      : { ...normalizeCosts({}), scope: null };

  const generatedAtMs = timestampOrNull(
    firstDefined(
      root.generated_at_ms,
      root.generatedAtMs,
      root.generated_at,
      routing.timestamp_ms,
      latestRecord(records)?.timestamp_ms,
    ),
  );
  const warnings = [];
  if (!hasAssignmentList) warnings.push("No current routing assignments were included.");
  if (modelCalls.length === 0 && callCount === null) warnings.push("No model-call facts were included.");
  if (benchmarks.length === 0) warnings.push("No benchmark runs were included.");

  return {
    schemaVersion: stringOrNull(firstDefined(root.schema_version, root.schemaVersion)),
    payloadType: stringOrNull(firstDefined(root.payload_type, root.payloadType)),
    generatedAtMs,
    context: {
      sessionId: stringOrNull(firstDefined(routing.session_id, root.session_id, summary.session_id)),
      worldId: stringOrNull(firstDefined(routing.world_id, root.world_id, summary.world_id)),
      sequence: integerOrNull(firstDefined(routing.sequence, summary.sequence)),
    },
    candidateCount,
    counts,
    capacities,
    assignments,
    modelCalls,
    metrics,
    costs,
    benchmarks,
    warnings,
  };
}
