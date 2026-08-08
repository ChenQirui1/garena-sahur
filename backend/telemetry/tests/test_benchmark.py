"""Deterministic evidence for Elson & Daniel's benchmark surfaces."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.telemetry.benchmark import (
    BenchmarkConfig,
    benchmark_settings,
    benchmark_metadata,
    dashboard_payload,
    default_normalized_pricing,
    generate_mock_trace,
    projected_all_strong_usage,
    run_one,
)
from backend.telemetry.logger import read_records


def _model_call(
    request_id: str,
    *,
    model: str,
    event_id: str | None,
    turn_id: str | None,
    input_tokens: int,
    output_tokens: int,
    session_id: str = "bench",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "record_type": "model_call",
        "session_id": session_id,
        "request_id": request_id,
        "npc_id": request_id,
        "tier": "focused" if model == "mock-focused" else "reactive",
        "provider": "mock",
        "model": model,
        "event_id": event_id,
        "conversation_id": "conversation" if turn_id else None,
        "turn_id": turn_id,
        "source_sequence": 10,
        "started_at_ms": 100,
        "completed_at_ms": 105,
        "latency_ms": 5,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "status": "success",
        "fallback_used": False,
        "error_code": None,
    }


def _routing_record(
    candidate_count: int = 10,
    *,
    session_id: str = "bench",
    world_id: str = "world",
    sequence: int = 10,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "record_type": "routing_result",
        "result_type": "routing_result",
        "session_id": session_id,
        "world_id": world_id,
        "sequence": sequence,
        "timestamp_ms": 100,
        "assignments": [
            {
                "npc_id": "npc-0",
                "tier": "focused",
                "previous_tier": None,
                "changed": False,
                "reasons": [],
                "direct_score": 1.0,
                "propagated_score": 0.0,
                "final_score": 1.0,
            }
        ],
        "counts": {"focused": 1, "reactive": 0, "ambient": candidate_count - 1},
        "diagnostics": {
            "focused_capacity": 2,
            "reactive_capacity": 6,
            "candidate_count": candidate_count,
            "routing_time_ms": 0.25,
        },
    }


def test_config_encodes_every_reproducibility_input() -> None:
    config = BenchmarkConfig(
        npc_count=25,
        seed=9,
        epoch_ms=1_000,
        rate_hz=2.5,
        duration_seconds=4.0,
    )

    assert config.tick_count == 10
    assert config.run_id == "mock-n25-seed9-epoch1000-r2p5-d4-cfg1"
    assert benchmark_metadata(config)["source_type"] == "synthetic_mock"
    assert benchmark_metadata(config)["settings_profile"] == "pinned_defaults_v1"

    with pytest.raises(ValueError, match="at least 2"):
        BenchmarkConfig(npc_count=1)


def test_benchmark_settings_ignore_deployment_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPOTLIGHT_CHARACTERS_PER_TOKEN", "2")
    monkeypatch.setenv("SPOTLIGHT_FOCUSED_CONCURRENCY", "1")

    settings = benchmark_settings(tmp_path / "run.sqlite3", Path.cwd())

    assert settings.characters_per_token == 4
    assert settings.focused_concurrency == 2
    assert settings.database_path == tmp_path / "run.sqlite3"


def test_mock_publisher_trace_is_byte_reproducible_and_read_only(tmp_path: Path) -> None:
    config = BenchmarkConfig(
        npc_count=10,
        rate_hz=4,
        duration_seconds=1,
    )
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"

    assert generate_mock_trace(config, first) == 7
    assert generate_mock_trace(config, second) == 7
    assert first.read_bytes() == second.read_bytes()

    snapshots = [
        json.loads(line)["message"]
        for line in first.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["topic"] == "world.snapshot"
    ]
    assert {snapshot["candidate_count"] for snapshot in snapshots} == {10}


def test_all_strong_projection_expands_events_but_not_targeted_turns() -> None:
    records = [
        _routing_record(candidate_count=10),
        _model_call(
            "event-a",
            model="mock-focused",
            event_id="theft",
            turn_id=None,
            input_tokens=100,
            output_tokens=20,
        ),
        _model_call(
            "event-b",
            model="mock-reactive",
            event_id="theft",
            turn_id=None,
            input_tokens=100,
            output_tokens=20,
        ),
        _model_call(
            "turn-a",
            model="mock-focused",
            event_id=None,
            turn_id="turn-1",
            input_tokens=50,
            output_tokens=5,
        ),
    ]

    projected = projected_all_strong_usage(records)

    assert projected.calls == 11
    assert projected.input_tokens == 1_050
    assert projected.output_tokens == 205


def test_all_strong_projection_keeps_sessions_with_reused_ids_separate() -> None:
    records = [
        _routing_record(10, session_id="session-a", sequence=1),
        _routing_record(100, session_id="session-b", sequence=1),
        _model_call(
            "call-a",
            model="mock-focused",
            event_id="shared-event-id",
            turn_id=None,
            input_tokens=100,
            output_tokens=20,
            session_id="session-a",
        ),
        _model_call(
            "call-b",
            model="mock-focused",
            event_id="shared-event-id",
            turn_id=None,
            input_tokens=100,
            output_tokens=20,
            session_id="session-b",
        ),
    ]
    for record in records[2:]:
        record["source_sequence"] = 1

    projected = projected_all_strong_usage(records)

    assert projected.calls == 110
    assert projected.input_tokens == 11_000
    assert projected.output_tokens == 2_200


def test_all_strong_projection_rejects_an_ambiguous_world_scope() -> None:
    records = [
        _routing_record(10, session_id="session", world_id="market", sequence=1),
        _routing_record(10, session_id="session", world_id="harbour", sequence=1),
    ]
    records[1]["diagnostics"] = None

    with pytest.raises(ValueError, match="maps to multiple worlds"):
        projected_all_strong_usage(records)


def test_all_strong_projection_rejects_boolean_source_sequence() -> None:
    call = _model_call(
        "call",
        model="mock-focused",
        event_id="event",
        turn_id=None,
        input_tokens=100,
        output_tokens=20,
    )
    call["source_sequence"] = True

    with pytest.raises(ValueError, match="source_sequence"):
        projected_all_strong_usage([_routing_record(sequence=1), call])


def test_dashboard_payload_uses_shared_metrics_and_cost_shapes() -> None:
    config = BenchmarkConfig(npc_count=10, rate_hz=4, duration_seconds=1)
    records = [
        benchmark_metadata(config),
        _routing_record(),
        _model_call(
            "event-a",
            model="mock-reactive",
            event_id="theft",
            turn_id=None,
            input_tokens=100,
            output_tokens=20,
        ),
    ]

    payload = dashboard_payload(
        [records],
        default_normalized_pricing(),
        strong_provider="mock",
        strong_model="mock-focused",
    )

    run = payload["runs"][0]
    assert run["metrics"]["model_calls"] == 1
    assert run["costs"]["actual_routed_cost"] == "0.00003000"
    assert run["costs"]["projected_all_strong_cost"] == "0.001200"
    assert payload["comparison"]["npc_counts"] == [10]
    assert payload["comparison"]["model_calls"] == [1]
    assert payload["generated_at_ms"] == config.epoch_ms
    assert payload["current_routing"] == records[1]
    assert payload["model_calls"] == [records[2]]
    assert payload["metrics"] == run["metrics"]
    assert payload["costs"]["scope"] == "Benchmark run · 10 NPCs"
    assert (
        payload["costs"]["projection_trigger_scope"]
        == "observed_triggers_with_model_call_facts"
    )


def test_small_run_enters_through_real_pipeline_adapters(tmp_path: Path) -> None:
    config = BenchmarkConfig(npc_count=10, rate_hz=4, duration_seconds=1)

    artifact = asyncio.run(run_one(config, tmp_path))
    records = read_records(artifact.telemetry_path)

    assert artifact.source_records == 7
    assert artifact.routing_records == 7
    assert artifact.model_call_records > 0
    assert records[0]["record_type"] == "benchmark_run"
    assert records[0]["provider_mode"] == "mock"
    assert {record["record_type"] for record in records} == {
        "benchmark_run",
        "model_call",
        "routing_result",
    }
