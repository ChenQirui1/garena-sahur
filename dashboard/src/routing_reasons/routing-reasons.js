const KNOWN_REASONS = new Map([
  ["active_conversation", "Active conversation target"],
  ["active_conversation_target", "Active conversation target"],
  ["conversation_target", "Active conversation target"],
  ["direct_score", "Direct attention score"],
  ["high_direct_score", "High direct attention score"],
  ["graph_propagation", "One-hop attention propagation"],
  ["gaze_propagation", "Gaze propagated from another NPC"],
  ["attention_propagation", "Attention propagated from another NPC"],
  ["event_relevance", "Relevant to the active event"],
  ["recent_interaction", "Recently interacted with the player"],
  ["inside_viewport", "Inside the player viewport"],
  ["line_of_sight", "Clear line of sight"],
  ["capacity_limit", "Placed lower because the tier is at capacity"],
  ["capacity_fallback", "Placed lower because the tier is at capacity"],
  ["hysteresis_hold", "Tier held to prevent rapid switching"],
  ["promotion", "Promoted by the current score"],
  ["demotion", "Demoted after the hold window"],
  ["ambient_default", "Ambient default"],
]);

export function readableReason(value) {
  if (typeof value !== "string" || value.trim() === "") return "No reason recorded";
  const trimmed = value.trim();
  const key = trimmed.toLowerCase().replace(/[\s-]+/g, "_");
  if (KNOWN_REASONS.has(key)) return KNOWN_REASONS.get(key);
  if (/[_-]/.test(trimmed) && !/\s/.test(trimmed)) {
    const words = trimmed.replace(/[_-]+/g, " ").toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  return trimmed;
}

export function reasonSummary(reasons, limit = 2) {
  if (!Array.isArray(reasons) || reasons.length === 0) return "No reason recorded";
  const readable = reasons.map(readableReason);
  const shown = readable.slice(0, Math.max(1, limit));
  const remaining = readable.length - shown.length;
  return remaining > 0 ? `${shown.join(" · ")} · +${remaining} more` : shown.join(" · ");
}
