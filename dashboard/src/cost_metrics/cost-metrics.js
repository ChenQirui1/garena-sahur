function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatCurrency(value, currency = "USD") {
  if (!isFiniteNumber(value)) return "Not recorded";
  const magnitude = Math.abs(value);
  const fractionDigits = magnitude > 0 && magnitude < 0.01 ? 4 : 2;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(fractionDigits)}`;
  }
}

export function formatPercent(value, digits = 1) {
  if (!isFiniteNumber(value)) return "Not recorded";
  return `${value.toFixed(digits)}%`;
}

export function costCards(costs = {}) {
  const scope = typeof costs.scope === "string" && costs.scope ? ` Source: ${costs.scope}.` : "";
  const projectionScope =
    costs.projectionTriggerScope === "observed_triggers_with_model_call_facts"
      ? " Covers observed triggers with model-call facts; zero-call triggers are not represented."
      : "";
  return [
    {
      id: "estimated",
      label: "Estimated routed cost",
      value: costs.estimated,
      description:
        "Token-priced estimate for the routed workload. It is not a provider invoice." + scope,
    },
    {
      id: "actual",
      label: "Actual routed cost",
      value: costs.actual,
      description:
        "Measured routed spend recorded for this payload. Missing provider billing stays blank." + scope,
    },
    {
      id: "projected",
      label: "Projected all-strong baseline",
      value: costs.projected,
      description:
        "Counterfactual cost if every eligible call used the strong model; never presented as spend." +
        projectionScope +
        scope,
    },
  ].map((card) => ({
    ...card,
    currency: typeof costs.currency === "string" ? costs.currency : "USD",
  }));
}
