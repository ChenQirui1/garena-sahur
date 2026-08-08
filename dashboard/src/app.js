import { normalizeDashboardData } from "./dashboard-data.js";
import { completeBenchmarkSeries, benchmarkCostWidths, benchmarkRunMeta } from "./benchmark_charts/benchmark-charts.js";
import { costCards, formatCurrency, formatPercent } from "./cost_metrics/cost-metrics.js";
import { readableReason, reasonSummary } from "./routing_reasons/routing-reasons.js";
import { tierLabel, tierRows } from "./tier_display/tier-display.js";

const GENERATED_DATA_URLS = [
  "/data/benchmark_runs/dashboard.json",
  "../data/benchmark_runs/dashboard.json",
  "../../data/benchmark_runs/dashboard.json",
];
const RECENT_CALL_LIMIT = 12;

let currentData = null;
let selectedNpcId = null;

const byId = (id) => document.getElementById(id);

function element(tagName, options = {}) {
  const node = document.createElement(tagName);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  for (const [name, value] of Object.entries(options.attributes ?? {})) {
    if (value !== null && value !== undefined) node.setAttribute(name, String(value));
  }
  return node;
}

function append(parent, ...children) {
  parent.append(...children.filter(Boolean));
  return parent;
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatNumber(value) {
  return isFiniteNumber(value) ? new Intl.NumberFormat().format(value) : "Not recorded";
}

function formatCompactNumber(value) {
  return isFiniteNumber(value)
    ? new Intl.NumberFormat(undefined, { notation: value >= 10_000 ? "compact" : "standard" }).format(value)
    : "Not recorded";
}

function formatMilliseconds(value) {
  if (!isFiniteNumber(value)) return "Not recorded";
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 1 : 2)} s`;
  if (value < 10) return `${value.toFixed(2)} ms`;
  return `${Math.round(value)} ms`;
}

function formatScore(value) {
  return isFiniteNumber(value) ? value.toFixed(3) : "—";
}

function scoreWidth(value) {
  if (!isFiniteNumber(value)) return 0;
  return Math.max(0, Math.min(100, value * 100));
}

function formatFallbackRate(rate) {
  if (!isFiniteNumber(rate)) return null;
  const percentage = Math.abs(rate) <= 1 ? rate * 100 : rate;
  return formatPercent(percentage);
}

function formatTimestamp(value, options = {}) {
  if (!isFiniteNumber(value)) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Not recorded";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: options.timeOnly ? undefined : "medium",
    timeStyle: "medium",
  }).format(date);
}

function setText(id, value) {
  byId(id).textContent = value;
}

function setSourceState(kind, message) {
  const dot = byId("source-dot");
  dot.classList.toggle("is-ready", kind === "ready");
  dot.classList.toggle("is-error", kind === "error");
  setText("data-status", message);
}

function showError(message) {
  const banner = byId("error-banner");
  banner.textContent = message;
  banner.hidden = false;
}

function hideError() {
  byId("error-banner").hidden = true;
}

function tierBadge(tier) {
  const badge = element("span", {
    className: `tier-badge ${tier ?? "unknown"}`,
    text: tierLabel(tier),
  });
  return badge;
}

function renderOverview(data) {
  setText("candidate-count", isFiniteNumber(data.candidateCount) ? formatNumber(data.candidateCount) : "—");
  setText("routing-time", formatMilliseconds(data.metrics.routingTimeMs));
  setText("tier-switches", formatNumber(data.metrics.tierSwitches));
  setText("model-call-count", isFiniteNumber(data.metrics.modelCalls) ? formatNumber(data.metrics.modelCalls) : "—");
  setText("median-latency", isFiniteNumber(data.metrics.medianLatencyMs) ? formatMilliseconds(data.metrics.medianLatencyMs) : "—");
  setText("p95-latency", isFiniteNumber(data.metrics.p95LatencyMs) ? formatMilliseconds(data.metrics.p95LatencyMs) : "—");
  setText("fallback-count", isFiniteNumber(data.metrics.fallbacks) ? formatNumber(data.metrics.fallbacks) : "—");

  const fallbackRate = formatFallbackRate(data.metrics.fallbackRate);
  setText(
    "fallback-note",
    fallbackRate
      ? `${fallbackRate} of recorded provider attempts`
      : data.metrics.modelCalls === 0
        ? "No model calls in this payload"
        : "Fallback rate not recorded",
  );

  const contextParts = [];
  if (data.context.sessionId) contextParts.push(`Session ${data.context.sessionId}`);
  if (data.context.worldId) contextParts.push(`world ${data.context.worldId}`);
  if (isFiniteNumber(data.context.sequence)) contextParts.push(`sequence ${formatNumber(data.context.sequence)}`);
  setText(
    "routing-context",
    contextParts.length ? contextParts.join(" · ") : "Waiting for a current routing result.",
  );

  const cards = tierRows(data.counts, data.capacities).map((tier) => {
    const card = element("article", { className: `tier-card tier-${tier.id}` });
    const heading = append(
      element("div", { className: "tier-heading" }),
      append(
        element("div", { className: "tier-heading" }),
        element("span", { className: "tier-dot", attributes: { "aria-hidden": "true" } }),
        element("h3", { text: tier.label }),
      ),
    );
    const countLine = append(
      element("div", { className: "tier-count-line" }),
      element("span", {
        className: "tier-count",
        text: isFiniteNumber(tier.count) ? formatNumber(tier.count) : "—",
      }),
      element("span", {
        className: "tier-capacity",
        text: isFiniteNumber(tier.capacity) ? `/ ${formatNumber(tier.capacity)} slots` : "No hard cap",
      }),
    );
    const trackAttributes = { "aria-hidden": "true" };
    if (tier.utilization !== null && tier.capacity !== null) {
      Object.assign(trackAttributes, {
        role: "progressbar",
        "aria-label": `${tier.label} capacity use`,
        "aria-valuemin": "0",
        "aria-valuemax": String(tier.capacity),
        "aria-valuenow": String(tier.count),
      });
      delete trackAttributes["aria-hidden"];
    }
    const fill = element("div", { className: "capacity-fill" });
    fill.style.setProperty("--fill", `${tier.utilizationPercent ?? 0}%`);
    const track = append(element("div", { className: "capacity-track", attributes: trackAttributes }), fill);
    let capacityCopy = "Capacity not recorded";
    if (tier.id === "ambient") capacityCopy = "Remainder after Focused and Reactive assignment";
    else if (tier.overCapacity) capacityCopy = "Capacity exceeded — inspect this result";
    else if (tier.utilization !== null) capacityCopy = `${Math.round(tier.utilization * 100)}% of capacity used`;
    append(
      card,
      heading,
      countLine,
      track,
      element("p", { className: "capacity-copy", text: capacityCopy }),
      element("p", { className: "metric-note", text: tier.description }),
    );
    return card;
  });
  byId("tier-cards").replaceChildren(...cards);
}

function renderCosts(data) {
  const cards = costCards(data.costs).map((cost) => {
    const card = element("article", { className: `cost-card panel ${cost.id}` });
    append(
      card,
      element("p", { className: "metric-label", text: cost.label }),
      element("p", {
        className: "cost-value",
        text: formatCurrency(cost.value, cost.currency),
      }),
      element("p", { className: "metric-note", text: cost.description }),
    );
    return card;
  });
  byId("cost-cards").replaceChildren(...cards);
}

function tableCell(options = {}) {
  const cell = element("td", { className: options.className });
  if (options.node) cell.append(options.node);
  else cell.textContent = options.text ?? "";
  return cell;
}

function renderRoutingTable(data) {
  const search = byId("routing-search").value.trim().toLowerCase();
  const tier = byId("tier-filter").value;
  const filtered = data.assignments.filter((assignment) => {
    const matchesSearch =
      !search || assignment.name.toLowerCase().includes(search) || assignment.npcId.toLowerCase().includes(search);
    const matchesTier = tier === "all" || assignment.tier === tier;
    return matchesSearch && matchesTier;
  });

  if (!selectedNpcId || !data.assignments.some((assignment) => assignment.npcId === selectedNpcId)) {
    selectedNpcId = data.assignments[0]?.npcId ?? null;
  }

  const rows = filtered.map((assignment) => {
    const row = element("tr", { className: assignment.npcId === selectedNpcId ? "is-selected" : "" });
    const button = element("button", {
      className: "npc-button",
      attributes: {
        type: "button",
        "aria-pressed": assignment.npcId === selectedNpcId ? "true" : "false",
        title: `Inspect ${assignment.name}`,
      },
    });
    append(
      button,
      element("span", { className: "npc-name", text: assignment.name }),
      element("span", { className: "npc-id", text: assignment.npcId }),
    );
    button.addEventListener("click", () => {
      selectedNpcId = assignment.npcId;
      renderRoutingTable(currentData);
    });

    const change = element("span", {
      className: `change-badge ${assignment.changed ? "changed" : "held"}`,
      text: assignment.changed ? "Changed" : "Held",
    });
    append(
      row,
      tableCell({ node: button }),
      tableCell({ node: tierBadge(assignment.tier) }),
      tableCell({
        node: assignment.previousTier
          ? tierBadge(assignment.previousTier)
          : element("span", { className: "npc-id", text: "First route" }),
      }),
      tableCell({ className: "numeric", text: formatScore(assignment.directScore) }),
      tableCell({ className: "numeric", text: formatScore(assignment.propagatedScore) }),
      tableCell({ className: "numeric", text: formatScore(assignment.finalScore) }),
      tableCell({ node: change }),
      tableCell({
        node: element("span", {
          className: "reason-preview",
          text: reasonSummary(assignment.reasons),
          attributes: { title: assignment.reasons.map(readableReason).join(" · ") || "No reason recorded" },
        }),
      }),
    );
    return row;
  });

  byId("routing-table-body").replaceChildren(...rows);
  const empty = byId("routing-empty");
  empty.hidden = rows.length > 0;
  if (data.assignments.length === 0) {
    empty.textContent = "No current routing assignments are present in this payload.";
  } else if (rows.length === 0) {
    empty.textContent = "No routing assignments match these filters.";
  }
  renderNpcDetail(data.assignments.find((assignment) => assignment.npcId === selectedNpcId) ?? null);
}

function renderNpcDetail(assignment) {
  const detail = byId("npc-detail");
  detail.replaceChildren(element("p", { className: "section-kicker", text: "Selected NPC" }));
  if (!assignment) {
    append(
      detail,
      element("h3", { attributes: { id: "npc-detail-title" }, text: "No NPC selected" }),
      element("p", {
        className: "detail-empty",
        text: "Choose an NPC in the routing table to inspect its scores and reasons.",
      }),
    );
    return;
  }

  append(
    detail,
    element("h3", { attributes: { id: "npc-detail-title" }, text: assignment.name }),
    element("p", { className: "detail-id", text: assignment.npcId }),
  );
  const tiers = element("div", { className: "detail-tiers" });
  if (assignment.previousTier) {
    append(
      tiers,
      tierBadge(assignment.previousTier),
      element("span", { className: "tier-arrow", text: "→", attributes: { "aria-label": "changed to" } }),
    );
  }
  tiers.append(tierBadge(assignment.tier));
  if (!assignment.previousTier) tiers.append(element("span", { className: "metric-note", text: "First recorded route" }));
  detail.append(tiers);

  const scoreList = element("div", { className: "score-list", attributes: { "aria-label": "Routing scores" } });
  for (const [label, value] of [
    ["Direct", assignment.directScore],
    ["Propagated", assignment.propagatedScore],
    ["Final", assignment.finalScore],
  ]) {
    const fill = element("div", { className: "score-fill" });
    fill.style.setProperty("--score-fill", `${scoreWidth(value)}%`);
    append(
      scoreList,
      append(
        element("div", { className: "score-row" }),
        element("span", { text: label }),
        append(element("div", { className: "score-track", attributes: { "aria-hidden": "true" } }), fill),
        element("output", { text: formatScore(value) }),
      ),
    );
  }
  detail.append(scoreList, element("p", { className: "detail-subheading", text: "Decision reasons" }));
  const reasons = element("ul", { className: "reason-list" });
  const values = assignment.reasons.length ? assignment.reasons : ["No reason recorded"];
  for (const reason of values) {
    append(reasons, append(element("li"), element("span", { className: "reason-chip", text: readableReason(reason) })));
  }
  detail.append(reasons);
}

function renderModelCalls(data) {
  const calls = data.modelCalls.slice(0, RECENT_CALL_LIMIT);
  const rows = calls.map((call) => {
    const row = element("tr");
    const completed = element("time", {
      text: formatTimestamp(call.completedAtMs ?? call.startedAtMs, { timeOnly: true }),
      attributes: isFiniteNumber(call.completedAtMs ?? call.startedAtMs)
        ? { datetime: new Date(call.completedAtMs ?? call.startedAtMs).toISOString() }
        : {},
    });
    const npc = element("div");
    append(
      npc,
      element("div", { className: "npc-name", text: call.npcName }),
      element("div", { className: "npc-id", text: call.npcId }),
    );
    const provider = element("div");
    append(
      provider,
      element("div", { text: call.provider ?? "Provider not selected" }),
      element("div", { className: "npc-id", text: call.model ?? "Model not recorded" }),
    );
    const outcome = element("div");
    append(
      outcome,
      element("span", {
        className: `status-badge ${call.status === "success" ? "success" : "error"}`,
        text: call.status,
      }),
    );
    if (call.fallbackUsed) outcome.append(element("span", { className: "fallback-flag", text: "Fallback used" }));
    if (call.errorCode) outcome.append(element("span", { className: "fallback-flag", text: call.errorCode }));

    append(
      row,
      tableCell({ node: completed }),
      tableCell({ node: npc }),
      tableCell({ node: tierBadge(call.tier) }),
      tableCell({ node: provider }),
      tableCell({ className: "numeric", text: formatMilliseconds(call.latencyMs) }),
      tableCell({ className: "numeric", text: formatCompactNumber(call.totalTokens) }),
      tableCell({ node: outcome }),
    );
    return row;
  });
  byId("calls-table-body").replaceChildren(...rows);
  byId("calls-empty").hidden = rows.length > 0;
}

function renderBenchmarks(data) {
  const series = completeBenchmarkSeries(data.benchmarks);
  const widths = benchmarkCostWidths(series);
  const cards = series.map((run, index) => {
    const card = element("article", {
      className: `benchmark-card${run.missing ? " is-missing" : ""}`,
    });
    const countHeading = append(
      element("div", { className: "benchmark-card-head" }),
      append(
        element("p", { className: "benchmark-npc-count", text: formatNumber(run.npcCount) }),
        element("span", { className: "benchmark-unit", text: "NPCs" }),
      ),
    );
    append(card, countHeading, element("p", { className: "run-meta", text: benchmarkRunMeta(run) }));
    if (run.missing) {
      card.append(
        element("p", {
          className: "awaiting-copy",
          text: "Run this required scenario to add a comparable cost and latency record.",
        }),
      );
      return card;
    }

    const bars = element("div", { className: "cost-bars" });
    for (const [kind, label, value, width] of [
      ["actual", "Routed", run.actualCost, widths[index].actual],
      ["projected", "All-strong", run.projectedCost, widths[index].projected],
    ]) {
      const fill = element("div", { className: `benchmark-bar-fill ${kind}` });
      fill.style.setProperty("--bar-width", `${width}%`);
      append(
        bars,
        append(
          element("div", { className: "cost-bar-row" }),
          append(
            element("div", { className: "cost-bar-label" }),
            element("span", { text: label }),
            element("strong", { text: formatCurrency(value, run.currency) }),
          ),
          append(
            element("div", {
              className: "benchmark-bar-track",
              attributes: { "aria-hidden": "true" },
            }),
            fill,
          ),
        ),
      );
    }
    card.append(bars);
    card.append(
      element("p", {
        className: "savings-copy",
        text: isFiniteNumber(run.savingsPercent)
          ? `${formatPercent(run.savingsPercent)} below projected baseline`
          : "Savings not recorded",
      }),
    );
    return card;
  });
  byId("benchmark-chart").replaceChildren(...cards);

  const rows = series.map((run) => {
    const row = element("tr");
    row.append(tableCell({ text: formatNumber(run.npcCount) }));
    if (run.missing) {
      const unavailable = tableCell({ text: "Awaiting run" });
      unavailable.colSpan = 8;
      unavailable.className = "numeric";
      row.append(unavailable);
      return row;
    }
    append(
      row,
      tableCell({ className: "numeric", text: formatNumber(run.modelCalls) }),
      tableCell({ className: "numeric", text: formatCompactNumber(run.totalTokens) }),
      tableCell({ className: "numeric", text: formatMilliseconds(run.medianLatencyMs) }),
      tableCell({ className: "numeric", text: formatMilliseconds(run.p95LatencyMs) }),
      tableCell({ className: "numeric", text: formatNumber(run.fallbacks) }),
      tableCell({ className: "numeric", text: formatCurrency(run.actualCost, run.currency) }),
      tableCell({ className: "numeric", text: formatCurrency(run.projectedCost, run.currency) }),
      tableCell({ className: "numeric", text: formatPercent(run.savingsPercent) }),
    );
    return row;
  });
  byId("benchmark-table-body").replaceChildren(...rows);
}

function renderDashboard(data, sourceLabel) {
  currentData = data;
  hideError();
  setSourceState("ready", sourceLabel);
  setText(
    "updated-at",
    isFiniteNumber(data.generatedAtMs)
      ? `Payload generated ${formatTimestamp(data.generatedAtMs)}`
      : "Payload has no generated timestamp",
  );
  renderOverview(data);
  renderCosts(data);
  renderRoutingTable(data);
  renderModelCalls(data);
  renderBenchmarks(data);
}

async function fetchGeneratedData() {
  const errors = [];
  const uniqueUrls = [...new Set(GENERATED_DATA_URLS)];
  for (const url of uniqueUrls) {
    try {
      const response = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return { payload: await response.json(), url };
    } catch (error) {
      errors.push(`${url}: ${error instanceof Error ? error.message : "request failed"}`);
    }
  }
  throw new Error(
    `Generated data/benchmark_runs/dashboard.json is unavailable. ${errors[0] ?? ""}`.trim(),
  );
}

async function loadGeneratedData() {
  const refreshButton = byId("refresh-button");
  refreshButton.disabled = true;
  setSourceState("loading", "Loading generated benchmark dashboard…");
  hideError();
  try {
    if (window.location.protocol === "file:") {
      throw new Error(
        "Browsers cannot reliably fetch data/benchmark_runs/dashboard.json from a file URL.",
      );
    }
    const { payload } = await fetchGeneratedData();
    renderDashboard(normalizeDashboardData(payload), "Loaded generated benchmark dashboard");
  } catch (error) {
    setSourceState("error", "Generated file unavailable — choose JSON");
    setText("updated-at", "No payload loaded");
    showError(
      `${error instanceof Error ? error.message : "Could not load generated data."} Choose data/benchmark_runs/dashboard.json with the local file picker to continue.`,
    );
  } finally {
    refreshButton.disabled = false;
  }
}

async function loadSelectedFile(file) {
  if (!file) return;
  setSourceState("loading", `Reading ${file.name}…`);
  hideError();
  try {
    const payload = JSON.parse(await file.text());
    renderDashboard(normalizeDashboardData(payload), `Loaded local file · ${file.name}`);
  } catch (error) {
    setSourceState("error", "Local JSON could not be loaded");
    showError(error instanceof Error ? error.message : "The selected file is not valid dashboard JSON.");
  }
}

byId("refresh-button").addEventListener("click", loadGeneratedData);
byId("file-picker").addEventListener("change", async (event) => {
  await loadSelectedFile(event.target.files?.[0]);
  event.target.value = "";
});
byId("routing-search").addEventListener("input", () => currentData && renderRoutingTable(currentData));
byId("tier-filter").addEventListener("change", () => currentData && renderRoutingTable(currentData));

loadGeneratedData();
