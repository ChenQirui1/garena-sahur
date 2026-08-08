"""Repeatable synthetic benchmarks for the Attention Router and telemetry path.

Owner: Elson & Daniel

The benchmark deliberately enters through the backend's public ``Adapters`` seams.  It uses
the real :class:`backend.router.Router`, the real JSONL telemetry sink, and the existing JSONL
intake/pipeline.  Ivan's ``mock-publisher`` remains an independent, read-only upstream fixture:
this module executes it rather than copying its scenario into a second implementation.

The default provider is the backend's deterministic mock.  Consequently the resulting token
and routing measurements are useful regression evidence, while provider latency and normalized
cost are *synthetic* evidence.  Live telemetry can be passed to :func:`dashboard_payload` with
an explicitly injected pricing table; it is never silently mixed with the mock runs.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Mapping, Sequence

from backend.config import Settings
from backend.ingestion.jsonl_intake import submit_jsonl
from backend.main import Adapters, build_pipeline
from backend.orchestration.router_port import RouterPort, RoutingResult, RoutingSnapshot
from backend.router import Router
from backend.telemetry.cost_calculator import PricingTable, UsageTotals, calculate_costs
from backend.telemetry.logger import JsonlTelemetry, append_jsonl, read_records
from backend.telemetry.metrics import summarize_records

BENCHMARK_SCHEMA_VERSION = "1.0"
BENCHMARK_RECORD_TYPE = "benchmark_run"
DASHBOARD_PAYLOAD_TYPE = "benchmark_dashboard"
BENCHMARK_SETTINGS_REVISION = 1
BENCHMARK_SETTINGS_PROFILE = f"pinned_defaults_v{BENCHMARK_SETTINGS_REVISION}"

DEFAULT_NPC_COUNTS = (10, 25, 50, 100)
DEFAULT_SEED = 7
DEFAULT_EPOCH_MS = 1_786_208_500_000
DEFAULT_RATE_HZ = 5.0
DEFAULT_DURATION_SECONDS = 4.0

SOURCE_SYNTHETIC_MOCK = "synthetic_mock"
SOURCE_LIVE = "live"
PROVIDER_MODE_MOCK = "mock"
LATENCY_MODE_DETERMINISTIC = "deterministic_synthetic_clock"

# These are dimensionless benchmark weights, not vendor prices.  A real-money run must inject
# its own versioned pricing document.  Focused is the normalized strong-model reference (1.0),
# and Reactive is expressed as a relative 0.25 solely to make routing comparisons visible.
DEFAULT_NORMALIZED_PRICING: Mapping[str, object] = {
    "version": "normalized-mock-v1",
    "currency": "normalized_cost_units",
    "label": "Normalized mock comparison weights; not vendor pricing",
    "rates": {
        "mock": {
            "mock-focused": {
                "input_per_million": 1.0,
                "output_per_million": 1.0,
            },
            "mock-reactive": {
                "input_per_million": 0.25,
                "output_per_million": 0.25,
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Inputs that make one mock-publisher trace reproducible."""

    npc_count: int
    seed: int = DEFAULT_SEED
    epoch_ms: int = DEFAULT_EPOCH_MS
    rate_hz: float = DEFAULT_RATE_HZ
    duration_seconds: float = DEFAULT_DURATION_SECONDS

    def __post_init__(self) -> None:
        if self.npc_count < 2:
            raise ValueError("npc_count must be at least 2")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.epoch_ms < 0:
            raise ValueError("epoch_ms must be non-negative")
        if self.rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")

    @property
    def tick_count(self) -> int:
        return max(1, int(self.rate_hz * self.duration_seconds))

    @property
    def run_id(self) -> str:
        rate = format(self.rate_hz, "g").replace(".", "p")
        duration = format(self.duration_seconds, "g").replace(".", "p")
        return (
            f"mock-n{self.npc_count}-seed{self.seed}-epoch{self.epoch_ms}"
            f"-r{rate}-d{duration}-cfg{BENCHMARK_SETTINGS_REVISION}"
        )


@dataclass(frozen=True, slots=True)
class BenchmarkArtifacts:
    """Files and counts produced by one completed benchmark run."""

    config: BenchmarkConfig
    trace_path: Path
    telemetry_path: Path
    source_records: int
    routing_records: int
    model_call_records: int

    def as_record(self) -> dict[str, object]:
        return {
            "run_id": self.config.run_id,
            "npc_count": self.config.npc_count,
            "trace_path": str(self.trace_path),
            "telemetry_path": str(self.telemetry_path),
            "source_records": self.source_records,
            "routing_records": self.routing_records,
            "model_call_records": self.model_call_records,
        }


class DeterministicBenchmarkClock:
    """A stable clock for synthetic orchestration and telemetry timestamps.

    Every read advances one millisecond.  This does not claim to measure provider wall time; it
    makes orchestration/model telemetry stable and is labelled accordingly in run metadata.
    Router ``perf_counter`` diagnostics remain genuine local CPU measurements and may vary.
    """

    def __init__(self, epoch_ms: int) -> None:
        self._wall_ms = epoch_ms
        self._monotonic_ms = 0

    def now_ms(self) -> int:
        value = self._wall_ms
        self._wall_ms += 1
        return value

    def monotonic_ms(self) -> int:
        value = self._monotonic_ms
        self._monotonic_ms += 1
        return value

    def advance(self, milliseconds: int) -> None:
        self._wall_ms += milliseconds
        self._monotonic_ms += milliseconds


class NoWaitDeadlines:
    """Deadline adapter for a deterministic mock provider and non-blocking publisher."""

    def __init__(self, clock: DeterministicBenchmarkClock) -> None:
        self._clock = clock

    @asynccontextmanager
    async def limit(self, milliseconds: int) -> AsyncIterator[None]:
        del milliseconds
        yield

    async def sleep(self, milliseconds: int) -> None:
        self._clock.advance(milliseconds)
        await asyncio.sleep(0)


class CapturingRouterAdapter:
    """Keep routing results in memory while delegating every decision to the real Router.

    The adapter does not write telemetry from inside ``route`` because the shared Router port
    requires synchronous, I/O-free routing.  :func:`run_one` flushes the captured results after
    the pipeline has drained.
    """

    def __init__(self, inner: RouterPort | None = None) -> None:
        self.inner = inner or Router()
        self.results: list[RoutingResult] = []

    def route(self, snapshot: RoutingSnapshot) -> RoutingResult:
        result = self.inner.route(snapshot)
        self.results.append(result)
        return result

    def reset_session(self, session_id: str) -> None:
        self.inner.reset_session(session_id)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def benchmark_settings(database_path: Path, root: Path) -> Settings:
    """Return validated backend defaults without reading ``.env`` or the environment.

    ``BaseSettings`` normally merges unspecified fields from ``SPOTLIGHT_*``.  The benchmark
    first materializes every declared class default, then passes every field explicitly through
    normal validation.  A developer's deployment settings therefore cannot silently alter a run
    that carries the same deterministic ID.  Future Settings fields are picked up automatically
    at their class default and require a settings-revision bump when intentionally changed here.
    """

    pinned = Settings.model_construct().model_dump()
    pinned.update(
        {
            "database_path": database_path,
            "npc_profiles_path": root / "data" / "npc_profiles.json",
            "cached_dialogue_path": root / "data" / "cached_dialogue.json",
            "provider_mode": PROVIDER_MODE_MOCK,
            "openai_api_key": None,
        }
    )
    return Settings(**pinned)


def generate_mock_trace(
    config: BenchmarkConfig,
    destination: Path,
    *,
    root: Path | None = None,
    overwrite: bool = False,
) -> int:
    """Execute Ivan's read-only mock publisher and return its JSONL record count."""

    root = (root or repository_root()).resolve()
    publisher = root / "mock-publisher" / "publish.py"
    if not publisher.is_file():
        raise FileNotFoundError(f"mock publisher not found: {publisher}")

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"trace already exists: {destination}")

    completed = subprocess.run(
        [
            sys.executable,
            str(publisher),
            "--npcs",
            str(config.npc_count),
            "--seed",
            str(config.seed),
            "--epoch-ms",
            str(config.epoch_ms),
            "--rate",
            format(config.rate_hz, "g"),
            "--duration",
            format(config.duration_seconds, "g"),
            "--no-sleep",
            "--out",
            str(destination),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # The publisher reports to stderr by design.  Validate the file rather than parsing prose.
    del completed
    return sum(1 for line in destination.read_text(encoding="utf-8").splitlines() if line.strip())


async def run_one(
    config: BenchmarkConfig,
    output_directory: Path,
    *,
    root: Path | None = None,
    overwrite: bool = False,
) -> BenchmarkArtifacts:
    """Run one synthetic trace through the real pipeline adapters and emit telemetry JSONL."""

    root = (root or repository_root()).resolve()
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    trace_path = output_directory / f"{config.run_id}.source.jsonl"
    telemetry_path = output_directory / f"{config.run_id}.telemetry.jsonl"
    database_path = output_directory / f"{config.run_id}.sqlite3"

    existing = [path for path in (trace_path, telemetry_path, database_path) if path.exists()]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"benchmark artifacts already exist: {names}")
    if overwrite:
        for path in existing:
            path.unlink()

    source_records = generate_mock_trace(config, trace_path, root=root)
    metadata = benchmark_metadata(config, telemetry_path)
    append_jsonl(telemetry_path, metadata)

    sink = JsonlTelemetry(telemetry_path)
    router = CapturingRouterAdapter(Router())
    clock = DeterministicBenchmarkClock(
        config.epoch_ms + int(config.duration_seconds * 1_000) + 100
    )
    settings = benchmark_settings(database_path, root)
    pipeline = build_pipeline(
        settings,
        Adapters(
            router=router,
            telemetry=sink,
            clock=clock,
            deadlines=NoWaitDeadlines(clock),
        ),
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    async with pipeline.running():
        # Drain after each upstream record.  A no-sleep publisher can otherwise let the newest
        # positional snapshot overtake a durable event before its queued generation is observed.
        for line in lines:
            if not line.strip():
                continue
            await submit_jsonl((line,), pipeline.intake)
            await pipeline.drain()

    for result in router.results:
        sink.record_routing_result(result)

    records = read_records(telemetry_path)
    return BenchmarkArtifacts(
        config=config,
        trace_path=trace_path,
        telemetry_path=telemetry_path,
        source_records=source_records,
        routing_records=sum(
            record.get("record_type") == "routing_result" for record in records
        ),
        model_call_records=sum(record.get("record_type") == "model_call" for record in records),
    )


async def run_suite(
    output_directory: Path,
    *,
    npc_counts: Sequence[int] = DEFAULT_NPC_COUNTS,
    seed: int = DEFAULT_SEED,
    epoch_ms: int = DEFAULT_EPOCH_MS,
    rate_hz: float = DEFAULT_RATE_HZ,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    root: Path | None = None,
    overwrite: bool = False,
) -> tuple[BenchmarkArtifacts, ...]:
    """Run the standard 10/25/50/100 sweep in a deterministic order."""

    if not npc_counts:
        raise ValueError("npc_counts cannot be empty")
    if len(set(npc_counts)) != len(npc_counts):
        raise ValueError("npc_counts must not contain duplicates")
    artifacts: list[BenchmarkArtifacts] = []
    for npc_count in npc_counts:
        artifacts.append(
            await run_one(
                BenchmarkConfig(
                    npc_count=npc_count,
                    seed=seed,
                    epoch_ms=epoch_ms,
                    rate_hz=rate_hz,
                    duration_seconds=duration_seconds,
                ),
                output_directory,
                root=root,
                overwrite=overwrite,
            )
        )
    return tuple(artifacts)


def benchmark_metadata(
    config: BenchmarkConfig, telemetry_path: Path | None = None
) -> dict[str, object]:
    """Return the explicit provenance record stored alongside every run's facts."""

    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "record_type": BENCHMARK_RECORD_TYPE,
        "run_id": config.run_id,
        "source_type": SOURCE_SYNTHETIC_MOCK,
        "provider_mode": PROVIDER_MODE_MOCK,
        "settings_profile": BENCHMARK_SETTINGS_PROFILE,
        "latency_mode": LATENCY_MODE_DETERMINISTIC,
        "npc_count": config.npc_count,
        "seed": config.seed,
        "epoch_ms": config.epoch_ms,
        "rate_hz": config.rate_hz,
        "duration_seconds": config.duration_seconds,
        "tick_count": config.tick_count,
        "telemetry_file": telemetry_path.name if telemetry_path is not None else None,
        "routing_time_note": "measured local CPU time; expected to vary between runs",
        "pricing_note": (
            "default pricing is normalized synthetic cost, not a vendor quote; "
            "inject a versioned pricing table for live financial estimates"
        ),
        "baseline_note": (
            "projected all-strong cost is a counterfactual derived from the same trigger and "
            "token evidence for observed triggers with model-call facts; it cannot represent "
            "a trigger that produced zero model calls and is not measured spend"
        ),
    }


def load_run_records(paths: Iterable[Path]) -> list[list[dict[str, object]]]:
    """Read and validate one telemetry file per benchmark run."""

    runs: list[list[dict[str, object]]] = []
    seen: set[str] = set()
    for path in sorted((Path(path).resolve() for path in paths), key=str):
        records = read_records(path)
        metadata = _metadata(records)
        run_id = str(metadata["run_id"])
        if run_id in seen:
            raise ValueError(f"duplicate benchmark run_id {run_id!r}")
        seen.add(run_id)
        runs.append(records)
    return runs


def dashboard_payload(
    runs: Iterable[Iterable[Mapping[str, object]]],
    pricing: PricingTable,
    *,
    strong_provider: str,
    strong_model: str,
) -> dict[str, object]:
    """Build the dashboard/benchmark payload exclusively from telemetry JSONL records."""

    summaries: list[dict[str, object]] = []
    records_by_run_id: dict[str, list[dict[str, object]]] = {}
    for one_run in runs:
        records = [dict(record) for record in one_run]
        metadata = _metadata(records)
        run_id = str(metadata["run_id"])
        metrics = _jsonable(summarize_records(records))
        cost_summary = calculate_costs(
            records,
            pricing,
            baseline_provider=strong_provider,
            baseline_model=strong_model,
            projected_baseline_usage=projected_all_strong_usage(records),
        )
        costs = _cost_payload(cost_summary)
        summaries.append(
            {
                "run_id": run_id,
                "npc_count": metadata["npc_count"],
                "seed": metadata.get("seed"),
                "epoch_ms": metadata.get("epoch_ms"),
                "ticks": metadata.get("tick_count"),
                "source_type": metadata.get("source_type", SOURCE_LIVE),
                "provider_mode": metadata.get("provider_mode", "live"),
                "latency_mode": metadata.get("latency_mode", "measured_wall_clock"),
                "telemetry_file": metadata.get("telemetry_file"),
                "metrics": metrics,
                "costs": costs,
            }
        )
        records_by_run_id[run_id] = records

    summaries.sort(key=lambda run: (int(run["npc_count"]), str(run["run_id"])))
    selected_summary = summaries[-1] if summaries else None
    selected_records = (
        records_by_run_id[str(selected_summary["run_id"])]
        if selected_summary is not None
        else []
    )
    current_routing = _latest_routing_record(selected_records)
    model_calls = [
        record
        for record in selected_records
        if record.get("record_type") == "model_call"
    ]
    generated_at_ms = _latest_record_timestamp(selected_records)

    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "payload_type": DASHBOARD_PAYLOAD_TYPE,
        "generated_at_ms": generated_at_ms,
        "pricing": _jsonable(pricing),
        # The largest standard crowd is the default control-room view.  These
        # facts are copied from that run's JSONL, so the overview and detail
        # panels use the same evidence as the comparison charts.
        "current_routing": current_routing,
        "model_calls": model_calls,
        "metrics": selected_summary["metrics"] if selected_summary else {},
        "costs": (
            {
                **dict(selected_summary["costs"]),
                "scope": f"Benchmark run · {selected_summary['npc_count']} NPCs",
            }
            if selected_summary
            else {}
        ),
        "runs": summaries,
        "comparison": _comparison(summaries),
    }


def _latest_routing_record(
    records: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    """Return the latest routing fact using Router sequence and append order."""

    candidates = [
        (index, record)
        for index, record in enumerate(records)
        if record.get("record_type") == "routing_result"
    ]
    if not candidates:
        return None

    _, latest = max(
        candidates,
        key=lambda item: (
            item[1].get("sequence")
            if isinstance(item[1].get("sequence"), int)
            and not isinstance(item[1].get("sequence"), bool)
            else -1,
            item[0],
        ),
    )
    return dict(latest)


def _latest_record_timestamp(records: Sequence[Mapping[str, object]]) -> int | None:
    """Find the newest explicit millisecond timestamp without inventing one."""

    timestamps: list[int] = []
    for record in records:
        for field in ("completed_at_ms", "timestamp_ms", "started_at_ms", "epoch_ms"):
            value = record.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                timestamps.append(value)
    return max(timestamps) if timestamps else None


def pricing_from_mapping(document: Mapping[str, object]) -> PricingTable:
    """Construct the telemetry pricing type from a caller-owned versioned document."""

    version = document.get("version")
    rates = document.get("rates")
    currency = document.get("currency", "USD")
    label = document.get("label")
    if not isinstance(version, str):
        raise ValueError("pricing.version must be a string")
    if not isinstance(rates, Mapping):
        raise ValueError("pricing.rates must be an object")
    if not isinstance(currency, str):
        raise ValueError("pricing.currency must be a string")
    if label is not None and not isinstance(label, str):
        raise ValueError("pricing.label must be a string or null")
    return PricingTable.from_mapping(
        version=version,
        rates=rates,
        currency=currency,
        label=label,
    )


def default_normalized_pricing() -> PricingTable:
    """Return the explicit non-financial pricing used by the mock benchmark."""

    return pricing_from_mapping(DEFAULT_NORMALIZED_PRICING)


def projected_all_strong_usage(
    records: Iterable[Mapping[str, object]],
) -> UsageTotals:
    """Estimate an all-strong/no-routing baseline from the run's own trigger evidence.

    Each observed event with model-call evidence expands to every candidate reported for its
    matching session and source sequence. Conversation turns remain one targeted call. Per-call
    tokens are the ceiling of that trigger's observed average; this is a transparent projection
    because the system correctly has no prompt/token record for suppressed Ambient candidates.
    """

    materialized = [dict(record) for record in records]
    candidates_by_scope: dict[tuple[str, int], int] = {}
    world_by_scope: dict[tuple[str, int], str] = {}
    for record in materialized:
        if record.get("record_type") != "routing_result":
            continue
        session_id = _required_string(record, "session_id")
        world_id = _required_string(record, "world_id")
        sequence = _non_negative_int(record, "sequence")
        scope = (session_id, sequence)
        previous_world = world_by_scope.setdefault(scope, world_id)
        if previous_world != world_id:
            raise ValueError(
                "cannot project a model call without world_id when one session/sequence "
                f"maps to multiple worlds: session_id={session_id!r}, sequence={sequence}"
            )

        diagnostics = record.get("diagnostics")
        if diagnostics is None:
            continue
        if not isinstance(diagnostics, Mapping):
            raise ValueError("diagnostics must be an object or null")
        count = _non_negative_int(diagnostics, "candidate_count")
        candidates_by_scope[scope] = max(
            count, candidates_by_scope.get(scope, 0)
        )

    grouped: dict[tuple[object, ...], list[Mapping[str, object]]] = defaultdict(list)
    for record in materialized:
        if record.get("record_type") != "model_call":
            continue
        session_id = _required_string(record, "session_id")
        event_id = record.get("event_id")
        turn_id = record.get("turn_id")
        source_sequence = _non_negative_int(record, "source_sequence")
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("event_id must be a non-empty string or null")
            key = ("event", session_id, event_id, source_sequence)
        elif turn_id is not None:
            if not isinstance(turn_id, str) or not turn_id:
                raise ValueError("turn_id must be a non-empty string or null")
            key = ("turn", session_id, turn_id, source_sequence)
        else:
            request_id = _required_string(record, "request_id")
            key = ("request", session_id, request_id, source_sequence)
        grouped[key].append(record)

    total_calls = 0
    total_input = 0
    total_output = 0
    for key, calls in grouped.items():
        observed_calls = len(calls)
        if key[0] == "event":
            baseline_calls = max(
                observed_calls,
                candidates_by_scope.get((str(key[1]), key[3]), observed_calls),
            )
        else:
            baseline_calls = observed_calls

        observed_input = sum(_non_negative_int(call, "input_tokens") for call in calls)
        observed_output = sum(_non_negative_int(call, "output_tokens") for call in calls)
        total_calls += baseline_calls
        total_input += _scale_tokens(observed_input, observed_calls, baseline_calls)
        total_output += _scale_tokens(observed_output, observed_calls, baseline_calls)

    return UsageTotals(
        calls=total_calls,
        input_tokens=total_input,
        output_tokens=total_output,
    )


def _required_string(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _non_negative_int(record: Mapping[str, object], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _scale_tokens(observed: int, observed_calls: int, baseline_calls: int) -> int:
    if observed_calls <= 0:
        return 0
    # Ceiling keeps the projected token evidence conservative and integer-valued.
    return (observed * baseline_calls + observed_calls - 1) // observed_calls


def _cost_payload(summary: object) -> dict[str, object]:
    raw = _jsonable(summary)
    if not isinstance(raw, dict):
        raise TypeError("cost summary must serialize to an object")
    actual_block = raw.get("actual_routed")
    projected_block = raw.get("projected_baseline")
    if not isinstance(actual_block, Mapping) or not isinstance(projected_block, Mapping):
        raise ValueError("cost summary is missing actual/projected totals")
    actual = Decimal(str(actual_block["total_cost"]))
    projected = Decimal(str(projected_block["total_cost"]))
    savings = projected - actual
    savings_percent = (
        None if projected == 0 else float((savings / projected) * Decimal(100))
    )
    return {
        **raw,
        "actual_routed_cost": str(actual),
        "projected_all_strong_cost": str(projected),
        "savings": str(savings),
        "savings_percent": savings_percent,
        "projection_trigger_scope": "observed_triggers_with_model_call_facts",
    }


def _metadata(records: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    matches = [record for record in records if record.get("record_type") == BENCHMARK_RECORD_TYPE]
    if len(matches) != 1:
        raise ValueError(
            f"a benchmark telemetry file needs exactly one {BENCHMARK_RECORD_TYPE!r} record"
        )
    metadata = matches[0]
    required = {"run_id", "npc_count", "source_type", "provider_mode"}
    missing = required - metadata.keys()
    if missing:
        raise ValueError(f"benchmark metadata missing fields: {sorted(missing)}")
    return metadata


def _jsonable(value: object) -> Any:
    if hasattr(value, "as_dict"):
        return _jsonable(value.as_dict())  # type: ignore[union-attr]
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _comparison(runs: Sequence[Mapping[str, object]]) -> dict[str, list[object]]:
    def metric(path: Sequence[str]) -> list[object]:
        values: list[object] = []
        for run in runs:
            value: object = run
            for part in path:
                value = value.get(part) if isinstance(value, Mapping) else None
            values.append(value)
        return values

    return {
        "npc_counts": metric(("npc_count",)),
        "model_calls": metric(("metrics", "model_calls")),
        "total_tokens": metric(("metrics", "total_tokens")),
        "latency_p95_ms": metric(("metrics", "latency_p95_ms")),
        "actual_routed_cost": metric(("costs", "actual_routed_cost")),
        "projected_all_strong_cost": metric(
            ("costs", "projected_all_strong_cost")
        ),
        "savings_percent": metric(("costs", "savings_percent")),
    }


def dump_dashboard_payload(payload: Mapping[str, object], destination: Path) -> None:
    """Write a stable JSON payload suitable for both dashboard and chart consumers."""

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
