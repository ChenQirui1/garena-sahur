"""Pure aggregation of Spotlight's append-only telemetry records.

Owner: Elson & Daniel

No function in this module reads files, clocks, environment variables, or live
pricing.  A dashboard and a benchmark therefore obtain the same answer when they
are given the same records.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

MODEL_CALL_RECORD_TYPE = "model_call"
ROUTING_RECORD_TYPE = "routing_result"


class TelemetryMetricError(ValueError):
    """A known telemetry record cannot be aggregated safely."""


@dataclass(frozen=True, slots=True)
class DistributionMetrics:
    """A small, JSON-friendly latency distribution summary."""

    count: int
    median_ms: float | None
    p95_ms: float | None

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "count": self.count,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
        }


@dataclass(frozen=True, slots=True)
class TelemetryMetrics:
    """Metrics derived from model-call and routing-result records."""

    model_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_median_ms: float | None
    latency_p95_ms: float | None
    errors: int
    error_rate: float
    fallbacks: int
    fallback_rate: float
    routing_results: int
    tier_switches: int
    tier_switches_by_transition: dict[str, int]
    capacity_usage: dict[str, object]
    routing_time_median_ms: float | None
    routing_time_p95_ms: float | None
    current_assignments: dict[str, dict[str, str]]

    @property
    def call_count(self) -> int:
        """Compatibility spelling for callers that display a generic count."""

        return self.model_calls

    @property
    def error_count(self) -> int:
        return self.errors

    @property
    def fallback_count(self) -> int:
        return self.fallbacks

    def as_dict(self) -> dict[str, object]:
        """Return the stable JSON-serializable dashboard/benchmark shape."""

        return {
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "latency_median_ms": self.latency_median_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "errors": self.errors,
            "error_rate": self.error_rate,
            "fallbacks": self.fallbacks,
            "fallback_rate": self.fallback_rate,
            "routing_results": self.routing_results,
            "tier_switches": self.tier_switches,
            "tier_switches_by_transition": dict(self.tier_switches_by_transition),
            "capacity_usage": _copy_capacity_usage(self.capacity_usage),
            "routing_time_median_ms": self.routing_time_median_ms,
            "routing_time_p95_ms": self.routing_time_p95_ms,
            "current_assignments": {
                session_id: dict(assignments)
                for session_id, assignments in self.current_assignments.items()
            },
        }


def _copy_capacity_usage(value: dict[str, object]) -> dict[str, object]:
    sessions = value.get("sessions", {})
    peaks = value.get("peak_utilization", {})
    copied_sessions: dict[str, object] = {}
    if isinstance(sessions, Mapping):
        for session_id, usage in sessions.items():
            if isinstance(usage, Mapping):
                copied_sessions[str(session_id)] = {
                    str(tier): dict(tier_usage)
                    if isinstance(tier_usage, Mapping)
                    else tier_usage
                    for tier, tier_usage in usage.items()
                }
    return {
        "sessions": copied_sessions,
        "peak_utilization": dict(peaks) if isinstance(peaks, Mapping) else {},
    }


def _number(record: Mapping[str, object], field: str, *, minimum: float = 0) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryMetricError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise TelemetryMetricError(f"{field} must be finite")
    if number < minimum:
        raise TelemetryMetricError(f"{field} must be at least {minimum}")
    return number


def _integer(record: Mapping[str, object], field: str, *, minimum: int = 0) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelemetryMetricError(f"{field} must be an integer")
    if value < minimum:
        raise TelemetryMetricError(f"{field} must be at least {minimum}")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TelemetryMetricError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TelemetryMetricError(f"{field} must be an array")
    return value


def nearest_rank_percentile(
    values: Iterable[int | float], percentile: float
) -> float | None:
    """Return a deterministic nearest-rank percentile, or ``None`` if empty.

    Nearest rank makes the small benchmark samples honest: p95 of two calls is
    the slower call rather than an interpolated latency that never occurred.
    """

    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("percentile values must be finite")
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def distribution(values: Iterable[int | float]) -> DistributionMetrics:
    """Summarise values with a median and nearest-rank p95."""

    materialized = [float(value) for value in values]
    if not all(math.isfinite(value) and value >= 0 for value in materialized):
        raise ValueError("distribution values must be finite and non-negative")
    return DistributionMetrics(
        count=len(materialized),
        median_ms=(float(statistics.median(materialized)) if materialized else None),
        p95_ms=nearest_rank_percentile(materialized, 0.95),
    )


def _rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def _utilization(used: int, capacity: int) -> float | None:
    # A disabled tier has no meaningful utilisation denominator.  ``None``
    # becomes JSON null, never NaN/Infinity.
    return used / capacity if capacity else None


def compute_metrics(records: Iterable[Mapping[str, object]]) -> TelemetryMetrics:
    """Aggregate telemetry without performing I/O or mutating ``records``."""

    calls = 0
    input_tokens = 0
    output_tokens = 0
    latencies: list[float] = []
    errors = 0
    fallbacks = 0

    routing_results = 0
    routing_times: list[float] = []
    tier_switches = 0
    transitions: Counter[str] = Counter()
    latest_by_scope: dict[
        str,
        tuple[int, int, dict[str, str], dict[str, object] | None],
    ] = {}
    peak_focused: float | None = None
    peak_reactive: float | None = None

    for record_index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise TelemetryMetricError(
                f"record {record_index + 1} must be an object"
            )

        record_type = record.get("record_type")
        if record_type == MODEL_CALL_RECORD_TYPE:
            calls += 1
            call_input = _integer(record, "input_tokens")
            call_output = _integer(record, "output_tokens")
            input_tokens += call_input
            output_tokens += call_output

            if "latency_ms" in record:
                latency = _number(record, "latency_ms")
            else:
                started = _integer(record, "started_at_ms")
                completed = _integer(record, "completed_at_ms")
                if completed < started:
                    raise TelemetryMetricError(
                        "completed_at_ms must not precede started_at_ms"
                    )
                latency = float(completed - started)
            latencies.append(latency)

            status = record.get("status")
            if status not in {"success", "error"}:
                raise TelemetryMetricError("status must be 'success' or 'error'")
            errors += int(status == "error")

            fallback_used = record.get("fallback_used")
            if not isinstance(fallback_used, bool):
                raise TelemetryMetricError("fallback_used must be a boolean")
            fallbacks += int(fallback_used)
            continue

        if record_type != ROUTING_RECORD_TYPE:
            # Append-only logs may also contain run metadata.  Unknown record
            # types are forward-compatible and do not alter known metrics.
            continue

        routing_results += 1
        session_id = record.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise TelemetryMetricError("session_id must be a non-empty string")
        world_id = record.get("world_id")
        if not isinstance(world_id, str) or not world_id:
            raise TelemetryMetricError("world_id must be a non-empty string")
        scope_id = f"{session_id}/{world_id}"
        sequence = _integer(record, "sequence")

        current: dict[str, str] = {}
        for raw_assignment in _sequence(record.get("assignments"), "assignments"):
            assignment = _mapping(raw_assignment, "assignments[]")
            npc_id = assignment.get("npc_id")
            tier = assignment.get("tier")
            changed = assignment.get("changed")
            if not isinstance(npc_id, str) or not npc_id:
                raise TelemetryMetricError("assignments[].npc_id must be a string")
            if not isinstance(tier, str) or not tier:
                raise TelemetryMetricError("assignments[].tier must be a string")
            if not isinstance(changed, bool):
                raise TelemetryMetricError("assignments[].changed must be a boolean")
            if npc_id in current:
                raise TelemetryMetricError(
                    f"duplicate assignment for npc_id {npc_id!r}"
                )
            current[npc_id] = tier
            if changed:
                tier_switches += 1
                previous = assignment.get("previous_tier")
                previous_label = "none" if previous is None else str(previous)
                transitions[f"{previous_label}->{tier}"] += 1

        usage: dict[str, object] | None = None
        diagnostics_value = record.get("diagnostics")
        counts_value = record.get("counts")
        diagnostics: Mapping[str, object] | None = None
        if diagnostics_value is not None:
            diagnostics = _mapping(diagnostics_value, "diagnostics")
            routing_times.append(_number(diagnostics, "routing_time_ms"))

        if diagnostics is not None and counts_value is not None:
            counts = _mapping(counts_value, "counts")
            focused_used = _integer(counts, "focused")
            reactive_used = _integer(counts, "reactive")
            focused_capacity = _integer(diagnostics, "focused_capacity")
            reactive_capacity = _integer(diagnostics, "reactive_capacity")
            focused_utilization = _utilization(focused_used, focused_capacity)
            reactive_utilization = _utilization(reactive_used, reactive_capacity)
            usage = {
                "focused": {
                    "used": focused_used,
                    "capacity": focused_capacity,
                    "utilization": focused_utilization,
                },
                "reactive": {
                    "used": reactive_used,
                    "capacity": reactive_capacity,
                    "utilization": reactive_utilization,
                },
            }
            if focused_utilization is not None:
                peak_focused = (
                    focused_utilization
                    if peak_focused is None
                    else max(peak_focused, focused_utilization)
                )
            if reactive_utilization is not None:
                peak_reactive = (
                    reactive_utilization
                    if peak_reactive is None
                    else max(peak_reactive, reactive_utilization)
                )

        # A larger accepted sequence is the current result.  For an equal
        # sequence (permitted for conversation-only reroutes), append order wins.
        previous_latest = latest_by_scope.get(scope_id)
        if previous_latest is None or (sequence, record_index) >= (
            previous_latest[0],
            previous_latest[1],
        ):
            latest_by_scope[scope_id] = (
                sequence,
                record_index,
                current,
                usage,
            )

    latency = distribution(latencies)
    routing_time = distribution(routing_times)
    current_assignments = {
        scope_id: dict(latest[2])
        for scope_id, latest in sorted(latest_by_scope.items())
    }
    current_capacity = {
        scope_id: latest[3]
        for scope_id, latest in sorted(latest_by_scope.items())
        if latest[3] is not None
    }

    return TelemetryMetrics(
        model_calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        latency_median_ms=latency.median_ms,
        latency_p95_ms=latency.p95_ms,
        errors=errors,
        error_rate=_rate(errors, calls),
        fallbacks=fallbacks,
        fallback_rate=_rate(fallbacks, calls),
        routing_results=routing_results,
        tier_switches=tier_switches,
        tier_switches_by_transition=dict(sorted(transitions.items())),
        capacity_usage={
            "sessions": current_capacity,
            "peak_utilization": {
                "focused": peak_focused,
                "reactive": peak_reactive,
            },
        },
        routing_time_median_ms=routing_time.median_ms,
        routing_time_p95_ms=routing_time.p95_ms,
        current_assignments=current_assignments,
    )


def summarize_records(records: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Return the JSON-serializable aggregate used by dashboards and benchmarks."""

    return compute_metrics(records).as_dict()


def aggregate_metrics(records: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Alias for :func:`summarize_records`."""

    return summarize_records(records)


def calculate_metrics(records: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Alias for :func:`summarize_records`."""

    return summarize_records(records)


__all__ = [
    "DistributionMetrics",
    "MODEL_CALL_RECORD_TYPE",
    "ROUTING_RECORD_TYPE",
    "TelemetryMetricError",
    "TelemetryMetrics",
    "aggregate_metrics",
    "calculate_metrics",
    "compute_metrics",
    "distribution",
    "nearest_rank_percentile",
    "summarize_records",
]
