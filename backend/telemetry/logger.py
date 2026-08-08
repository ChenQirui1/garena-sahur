"""Append-only JSONL telemetry for routing and model-call facts.

Owner: Elson & Daniel

The router itself remains I/O-free.  Callers hand its completed result to
``JsonlTelemetry.record_routing_result`` after routing has returned.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, TypeAlias

from backend.orchestration.telemetry_port import ModelCallFact, TelemetryPort
from backend.router.models import RoutingResult

PathLike: TypeAlias = str | os.PathLike[str]
TelemetryRecord: TypeAlias = dict[str, object]

ROUTING_RECORD_TYPE = "routing_result"


class TelemetrySerializationError(ValueError):
    """A value cannot be represented as a strict JSON telemetry record."""


class MalformedTelemetryRecordError(ValueError):
    """A JSONL line is not a valid, strict JSON object."""

    def __init__(self, path: Path, line_number: int, detail: str) -> None:
        self.path = path
        self.line_number = line_number
        self.detail = detail
        super().__init__(f"{path}: line {line_number}: {detail}")


_locks_guard = threading.Lock()
_path_locks: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    """Return the process-local lock shared by every sink for ``path``."""

    key = os.path.normcase(str(path.resolve(strict=False)))
    with _locks_guard:
        return _path_locks.setdefault(key, threading.RLock())


def _strict_json_line(record: Mapping[str, object]) -> str:
    if not isinstance(record, Mapping):
        raise TypeError("a telemetry record must be a mapping")

    try:
        # Serialise before opening the destination.  A bad value therefore cannot
        # append a partial line or otherwise disturb records already on disk.
        return json.dumps(
            dict(record),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise TelemetrySerializationError(
            f"telemetry record is not strict JSON: {exc}"
        ) from exc


def append_jsonl(path: PathLike, record: Mapping[str, object]) -> None:
    """Atomically append one strict-JSON object within this Python process.

    The function never opens a file in truncate mode.  A per-path lock covers
    directory creation, opening, the single line write, and flushing, including
    when several ``JsonlTelemetry`` instances target the same path.
    """

    destination = Path(path)
    line = _strict_json_line(record)

    with _lock_for(destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8", newline="") as stream:
            written = stream.write(line)
            if written != len(line):
                raise OSError(
                    f"short telemetry write to {destination}: "
                    f"wrote {written} of {len(line)} characters"
                )
            stream.flush()


def append_record(path: PathLike, record: Mapping[str, object]) -> None:
    """Readable alias for :func:`append_jsonl`."""

    append_jsonl(path, record)


def _reject_non_standard_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def read_records(path: PathLike) -> list[TelemetryRecord]:
    """Read a JSONL file, reporting malformed input with its one-based line.

    Empty lines, JSON scalars/arrays, duplicate object keys, and Python's
    non-standard ``NaN``/``Infinity`` spellings are rejected.  The returned list
    is a stable snapshot protected from concurrent writes made through this
    module.
    """

    source = Path(path)
    records: list[TelemetryRecord] = []

    with _lock_for(source):
        with source.open("r", encoding="utf-8") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if not raw_line.strip():
                    raise MalformedTelemetryRecordError(
                        source, line_number, "empty JSONL line"
                    )
                try:
                    decoded = json.loads(
                        raw_line,
                        parse_constant=_reject_non_standard_constant,
                        object_pairs_hook=_unique_object,
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    raise MalformedTelemetryRecordError(
                        source, line_number, str(exc)
                    ) from exc
                if not isinstance(decoded, dict):
                    raise MalformedTelemetryRecordError(
                        source,
                        line_number,
                        "telemetry record must be a JSON object",
                    )
                records.append(decoded)

    return records


def read_jsonl(path: PathLike) -> list[TelemetryRecord]:
    """Alias for :func:`read_records`."""

    return read_records(path)


def iter_records(path: PathLike) -> Iterator[TelemetryRecord]:
    """Iterate over the stable snapshot returned by :func:`read_records`."""

    yield from read_records(path)


def model_call_as_record(fact: ModelCallFact) -> TelemetryRecord:
    """Convert the existing shared model-call fact without renaming fields."""

    return dict(fact.as_record())


def _tier_value(tier: object) -> object:
    return getattr(tier, "value", tier)


def routing_result_as_record(result: RoutingResult) -> TelemetryRecord:
    """Convert a routing result to the documented append-only record shape."""

    assignments: list[dict[str, object]] = []
    for assignment in result.assignments:
        assignments.append(
            {
                "npc_id": assignment.npc_id,
                "tier": _tier_value(assignment.tier),
                "previous_tier": (
                    None
                    if assignment.previous_tier is None
                    else _tier_value(assignment.previous_tier)
                ),
                "changed": assignment.changed,
                "reasons": list(assignment.reasons),
                "direct_score": assignment.direct_score,
                "propagated_score": assignment.propagated_score,
                "final_score": assignment.final_score,
            }
        )

    counts: dict[str, object] | None = None
    if result.counts is not None:
        counts = {
            "focused": result.counts.focused,
            "reactive": result.counts.reactive,
            "ambient": result.counts.ambient,
        }

    diagnostics: dict[str, object] | None = None
    if result.diagnostics is not None:
        diagnostics = {
            "focused_capacity": result.diagnostics.focused_capacity,
            "reactive_capacity": result.diagnostics.reactive_capacity,
            "candidate_count": result.diagnostics.candidate_count,
            "routing_time_ms": result.diagnostics.routing_time_ms,
        }

    return {
        "schema_version": result.schema_version,
        "record_type": ROUTING_RECORD_TYPE,
        # Keep the result envelope intact as well as identifying the telemetry
        # record.  They currently have the same value but are separate contracts.
        "result_type": result.result_type,
        "session_id": result.session_id,
        "world_id": result.world_id,
        "sequence": result.sequence,
        "timestamp_ms": result.timestamp_ms,
        "assignments": assignments,
        "counts": counts,
        "diagnostics": diagnostics,
    }


class JsonlTelemetry(TelemetryPort):
    """Thread-safe, append-only implementation of the telemetry boundary."""

    def __init__(self, path: PathLike) -> None:
        self.path = Path(path)

    def append(self, record: Mapping[str, object]) -> None:
        append_jsonl(self.path, record)

    def read_records(self) -> list[TelemetryRecord]:
        return read_records(self.path)

    def record_model_call(self, fact: ModelCallFact) -> None:
        self.append(model_call_as_record(fact))

    def record_routing_result(self, result: RoutingResult) -> None:
        self.append(routing_result_as_record(result))


# The longer name makes call sites self-documenting; the shorter one is useful in
# application wiring.  They intentionally refer to the same implementation.
JsonlTelemetrySink = JsonlTelemetry


__all__ = [
    "JsonlTelemetry",
    "JsonlTelemetrySink",
    "MalformedTelemetryRecordError",
    "ROUTING_RECORD_TYPE",
    "TelemetryRecord",
    "TelemetrySerializationError",
    "append_jsonl",
    "append_record",
    "iter_records",
    "model_call_as_record",
    "read_jsonl",
    "read_records",
    "routing_result_as_record",
]
