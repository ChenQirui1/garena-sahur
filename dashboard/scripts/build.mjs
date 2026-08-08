import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dashboardRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = resolve(dashboardRoot, "src");
const outputRoot = resolve(dashboardRoot, "dist");

// `outputRoot` is a fixed child of this package, never a caller-supplied path.
await rm(outputRoot, { recursive: true, force: true });
await mkdir(outputRoot, { recursive: true });

for (const relativePath of [
  "index.html",
  "og.png",
  "styles.css",
  "app.js",
  "dashboard-data.js",
  "tier_display/tier-display.js",
  "cost_metrics/cost-metrics.js",
  "routing_reasons/routing-reasons.js",
  "benchmark_charts/benchmark-charts.js",
]) {
  const destination = resolve(outputRoot, relativePath);
  await mkdir(dirname(destination), { recursive: true });
  await cp(resolve(sourceRoot, relativePath), destination);
}

console.log(`Built dashboard at ${outputRoot}`);
