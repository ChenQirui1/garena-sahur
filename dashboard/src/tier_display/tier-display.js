export const TIER_DEFINITIONS = Object.freeze([
  {
    id: "focused",
    label: "Focused",
    description: "Highest-priority NPCs receiving the strongest model path.",
  },
  {
    id: "reactive",
    label: "Reactive",
    description: "Event-aware NPCs ready to respond when a deliberate trigger arrives.",
  },
  {
    id: "ambient",
    label: "Ambient",
    description: "Local behaviour continues without consuming a scarce model tier slot.",
  },
]);

function safeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function tierRows(counts = {}, capacities = {}) {
  return TIER_DEFINITIONS.map((definition) => {
    const count = safeNumber(counts[definition.id]);
    const capacity = safeNumber(capacities[definition.id]);
    const utilization =
      count === null || capacity === null
        ? null
        : capacity === 0
          ? count === 0
            ? 0
            : 1
          : count / capacity;

    return {
      ...definition,
      count,
      capacity,
      utilization,
      utilizationPercent: utilization === null ? null : Math.min(100, Math.max(0, utilization * 100)),
      overCapacity: count !== null && capacity !== null && count > capacity,
    };
  });
}

export function tierLabel(value) {
  return TIER_DEFINITIONS.find((tier) => tier.id === value)?.label ?? "Not recorded";
}
