"""Dashboard metrics are pure functions of telemetry records."""

from __future__ import annotations

import pytest

from backend.telemetry.metrics import (
    TelemetryMetricError,
    compute_metrics,
    summarize_records,
)


def model_call(
    *,
    latency: int,
    input_tokens: int,
    output_tokens: int,
    status: str = "success",
    fallback: bool = False,
) -> dict[str, object]:
    return {
        "record_type": "model_call",
        "latency_ms": latency,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "status": status,
        "fallback_used": fallback,
    }


def routing(
    *,
    sequence: int,
    focused: int,
    reactive: int,
    routing_time_ms: float,
    assignments: list[dict[str, object]],
    session_id: str = "session-1",
    world_id: str = "world-1",
) -> dict[str, object]:
    return {
        "record_type": "routing_result",
        "session_id": session_id,
        "world_id": world_id,
        "sequence": sequence,
        "assignments": assignments,
        "counts": {"focused": focused, "reactive": reactive, "ambient": 1},
        "diagnostics": {
            "focused_capacity": 2,
            "reactive_capacity": 4,
            "candidate_count": focused + reactive + 1,
            "routing_time_ms": routing_time_ms,
        },
    }


def test_model_call_totals_percentiles_errors_and_fallbacks() -> None:
    metrics = compute_metrics(
        [
            model_call(latency=10, input_tokens=10, output_tokens=1),
            model_call(
                latency=20,
                input_tokens=20,
                output_tokens=2,
                status="error",
                fallback=True,
            ),
            model_call(latency=100, input_tokens=30, output_tokens=3),
        ]
    )

    assert metrics.model_calls == 3
    assert (metrics.input_tokens, metrics.output_tokens, metrics.total_tokens) == (
        60,
        6,
        66,
    )
    assert metrics.latency_median_ms == 20
    assert metrics.latency_p95_ms == 100
    assert metrics.errors == 1
    assert metrics.error_rate == pytest.approx(1 / 3)
    assert metrics.fallbacks == 1
    assert metrics.fallback_rate == pytest.approx(1 / 3)


def test_routing_metrics_track_switches_capacity_time_and_latest_assignments() -> None:
    records = [
        routing(
            sequence=3,
            focused=1,
            reactive=1,
            routing_time_ms=0.4,
            assignments=[
                {
                    "npc_id": "npc-a",
                    "tier": "reactive",
                    "previous_tier": "ambient",
                    "changed": True,
                }
            ],
        ),
        # Equal sequences are legal for a conversation-only reroute; append order
        # determines which result is current.
        routing(
            sequence=3,
            focused=2,
            reactive=0,
            routing_time_ms=1.2,
            assignments=[
                {
                    "npc_id": "npc-a",
                    "tier": "focused",
                    "previous_tier": "reactive",
                    "changed": True,
                },
                {
                    "npc_id": "npc-b",
                    "tier": "focused",
                    "previous_tier": None,
                    "changed": False,
                },
            ],
        ),
    ]

    metrics = compute_metrics(records)

    assert metrics.routing_results == 2
    assert metrics.tier_switches == 2
    assert metrics.tier_switches_by_transition == {
        "ambient->reactive": 1,
        "reactive->focused": 1,
    }
    assert metrics.routing_time_median_ms == pytest.approx(0.8)
    assert metrics.routing_time_p95_ms == 1.2
    assert metrics.current_assignments == {
        "session-1/world-1": {"npc-a": "focused", "npc-b": "focused"}
    }
    assert metrics.capacity_usage == {
        "sessions": {
            "session-1/world-1": {
                "focused": {"used": 2, "capacity": 2, "utilization": 1.0},
                "reactive": {"used": 0, "capacity": 4, "utilization": 0.0},
            }
        },
        "peak_utilization": {"focused": 1.0, "reactive": 0.25},
    }


def test_latest_routing_state_is_scoped_by_session_and_world() -> None:
    records = [
        routing(
            sequence=8,
            focused=1,
            reactive=0,
            routing_time_ms=0.2,
            assignments=[
                {
                    "npc_id": "npc-a",
                    "tier": "focused",
                    "previous_tier": None,
                    "changed": False,
                }
            ],
            world_id="market",
        ),
        routing(
            sequence=2,
            focused=0,
            reactive=1,
            routing_time_ms=0.3,
            assignments=[
                {
                    "npc_id": "npc-a",
                    "tier": "reactive",
                    "previous_tier": None,
                    "changed": False,
                }
            ],
            world_id="harbour",
        ),
    ]

    metrics = compute_metrics(records)

    assert metrics.current_assignments == {
        "session-1/harbour": {"npc-a": "reactive"},
        "session-1/market": {"npc-a": "focused"},
    }


def test_summary_is_json_serializable_and_ignores_run_metadata() -> None:
    summary = summarize_records([{"record_type": "benchmark_run", "seed": 42}])

    assert summary["model_calls"] == 0
    assert summary["latency_median_ms"] is None
    assert summary["error_rate"] == 0.0
    assert summary["current_assignments"] == {}


def test_routing_time_does_not_depend_on_optional_counts() -> None:
    record = routing(
        sequence=1,
        focused=0,
        reactive=0,
        routing_time_ms=2.5,
        assignments=[],
    )
    record["counts"] = None

    metrics = compute_metrics([record])

    assert metrics.routing_time_median_ms == 2.5
    assert metrics.capacity_usage["sessions"] == {}


def test_invalid_known_record_fails_loudly() -> None:
    with pytest.raises(TelemetryMetricError, match="input_tokens"):
        compute_metrics(
            [model_call(latency=10, input_tokens=-1, output_tokens=0)]
        )
