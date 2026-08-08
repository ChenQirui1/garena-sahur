"""The JSONL sink preserves facts and never emits permissive JSON."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.orchestration.router_port import (
    AttentionTier,
    RoutingAssignment,
    RoutingDiagnostics,
    RoutingResult,
    TierCounts,
)
from backend.orchestration.telemetry_port import ModelCallFact
from backend.telemetry.logger import (
    JsonlTelemetry,
    MalformedTelemetryRecordError,
    TelemetrySerializationError,
    append_jsonl,
    read_records,
)


def model_fact() -> ModelCallFact:
    return ModelCallFact(
        session_id="session-1",
        request_id="request-1",
        npc_id="npc-1",
        tier="focused",
        provider="provider-a",
        model="model-a",
        event_id="event-1",
        conversation_id=None,
        turn_id=None,
        source_sequence=7,
        started_at_ms=1_000,
        completed_at_ms=1_025,
        input_tokens=30,
        output_tokens=5,
        status="success",
        fallback_used=False,
        error_code=None,
    )


def routing_result() -> RoutingResult:
    return RoutingResult(
        schema_version="1.0",
        result_type="routing_result",
        session_id="session-1",
        world_id="world-1",
        sequence=7,
        timestamp_ms=1_025,
        assignments=(
            RoutingAssignment(
                npc_id="npc-1",
                tier=AttentionTier.FOCUSED,
                previous_tier=AttentionTier.REACTIVE,
                changed=True,
                reasons=("active_conversation", "capacity_rank=1"),
                direct_score=0.9,
                propagated_score=0.2,
                final_score=0.9,
            ),
        ),
        counts=TierCounts(focused=1, reactive=0, ambient=0),
        diagnostics=RoutingDiagnostics(
            focused_capacity=2,
            reactive_capacity=3,
            candidate_count=1,
            routing_time_ms=0.75,
        ),
    )


def test_sink_records_existing_model_call_contract(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    sink = JsonlTelemetry(path)

    sink.record_model_call(model_fact())

    assert sink.read_records() == [model_fact().as_record()]
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["latency_ms"] == 25
    assert parsed["record_type"] == "model_call"


def test_routing_record_preserves_envelope_assignments_counts_and_diagnostics(
    tmp_path: Path,
) -> None:
    sink = JsonlTelemetry(tmp_path / "telemetry.jsonl")

    sink.record_routing_result(routing_result())

    record = sink.read_records()[0]
    assert record | {} == {
        "schema_version": "1.0",
        "record_type": "routing_result",
        "result_type": "routing_result",
        "session_id": "session-1",
        "world_id": "world-1",
        "sequence": 7,
        "timestamp_ms": 1_025,
        "assignments": [
            {
                "npc_id": "npc-1",
                "tier": "focused",
                "previous_tier": "reactive",
                "changed": True,
                "reasons": ["active_conversation", "capacity_rank=1"],
                "direct_score": 0.9,
                "propagated_score": 0.2,
                "final_score": 0.9,
            }
        ],
        "counts": {"focused": 1, "reactive": 0, "ambient": 0},
        "diagnostics": {
            "focused_capacity": 2,
            "reactive_capacity": 3,
            "candidate_count": 1,
            "routing_time_ms": 0.75,
        },
    }


def test_all_sink_instances_share_a_thread_safe_append_lock(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    first = JsonlTelemetry(path)
    second = JsonlTelemetry(path)

    def write(index: int) -> None:
        (first if index % 2 else second).append({"index": index})

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(200)))

    records = read_records(path)
    assert len(records) == 200
    assert {record["index"] for record in records} == set(range(200))


def test_non_finite_value_is_rejected_before_existing_file_is_opened(
    tmp_path: Path,
) -> None:
    path = tmp_path / "telemetry.jsonl"
    append_jsonl(path, {"kept": True})
    before = path.read_bytes()

    with pytest.raises(TelemetrySerializationError, match="strict JSON"):
        append_jsonl(path, {"bad": float("nan")})

    assert path.read_bytes() == before


def test_reader_reports_the_exact_malformed_line(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    path.write_text('{"valid":true}\n{"bad":NaN}\n', encoding="utf-8")

    with pytest.raises(MalformedTelemetryRecordError) as rejected:
        read_records(path)

    assert rejected.value.line_number == 2
    assert "line 2" in str(rejected.value)
    assert "NaN" in str(rejected.value)
