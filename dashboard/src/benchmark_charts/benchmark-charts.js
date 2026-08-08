export const REQUIRED_BENCHMARK_COUNTS = Object.freeze([10, 25, 50, 100]);

export function completeBenchmarkSeries(benchmarks = []) {
  const latestByCount = new Map();
  for (const benchmark of Array.isArray(benchmarks) ? benchmarks : []) {
    if (!REQUIRED_BENCHMARK_COUNTS.includes(benchmark?.npcCount)) continue;
    const current = latestByCount.get(benchmark.npcCount);
    if (
      !current ||
      benchmark.epochMs === null ||
      current.epochMs === null ||
      benchmark.epochMs >= current.epochMs
    ) {
      latestByCount.set(benchmark.npcCount, benchmark);
    }
  }
  return REQUIRED_BENCHMARK_COUNTS.map(
    (npcCount) =>
      latestByCount.get(npcCount) ?? {
        npcCount,
        missing: true,
        currency: "USD",
      },
  );
}

export function benchmarkCostWidths(series) {
  const rows = Array.isArray(series) ? series : [];
  const costs = rows.flatMap((row) => [row?.actualCost, row?.projectedCost]);
  const maximum = Math.max(
    0,
    ...costs.filter((cost) => typeof cost === "number" && Number.isFinite(cost) && cost >= 0),
  );
  return rows.map((row) => ({
    npcCount: row.npcCount,
    actual:
      maximum > 0 && typeof row.actualCost === "number"
        ? Math.min(100, Math.max(0, (row.actualCost / maximum) * 100))
        : 0,
    projected:
      maximum > 0 && typeof row.projectedCost === "number"
        ? Math.min(100, Math.max(0, (row.projectedCost / maximum) * 100))
        : 0,
  }));
}

export function benchmarkRunMeta(run) {
  if (run?.missing) return "Awaiting a saved run";
  const parts = [];
  if (run.providerMode) parts.push(`${run.providerMode} provider`);
  if (run.seed !== null && run.seed !== undefined) parts.push(`seed ${run.seed}`);
  if (run.ticks !== null && run.ticks !== undefined) parts.push(`${run.ticks} ticks`);
  if (parts.length === 0 && run.isComparisonOnly) return "Generated comparison summary";
  return parts.join(" · ") || "Saved benchmark run";
}
