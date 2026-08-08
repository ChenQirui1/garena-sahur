#!/usr/bin/env python3
"""Convert benchmark JSONL into the dashboard's comparison JSON payload.

Owner: Elson & Daniel

No second metrics implementation lives here: aggregation and cost calculation are delegated to
``backend.telemetry`` using the same append-only records consumed by the dashboard.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.telemetry.benchmark import (  # noqa: E402
    SOURCE_SYNTHETIC_MOCK,
    dashboard_payload,
    default_normalized_pricing,
    dump_dashboard_payload,
    load_run_records,
    pricing_from_mapping,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="telemetry JSONL files (default: data/benchmark_runs/*.telemetry.jsonl)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "data" / "benchmark_runs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "benchmark_runs" / "dashboard.json",
    )
    parser.add_argument(
        "--pricing",
        type=Path,
        help=(
            "versioned pricing JSON; required for live records. Mock runs default to clearly "
            "labelled normalized cost units"
        ),
    )
    parser.add_argument("--strong-provider", default="mock")
    parser.add_argument("--strong-model", default="mock-focused")
    return parser.parse_args(argv)


def _pricing(path: Path | None):
    if path is None:
        return default_normalized_pricing()
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("pricing document must be a JSON object")
    return pricing_from_mapping(document)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    paths = args.inputs or sorted(args.input_dir.glob("*.telemetry.jsonl"))
    if not paths:
        raise SystemExit("no benchmark telemetry JSONL files found")
    runs = load_run_records(paths)
    if args.pricing is None:
        non_mock = [
            record.get("run_id")
            for records in runs
            for record in records
            if record.get("record_type") == "benchmark_run"
            and record.get("source_type") != SOURCE_SYNTHETIC_MOCK
        ]
        if non_mock:
            raise SystemExit(
                "live benchmark records require --pricing; refusing normalized mock prices for "
                + ", ".join(str(run_id) for run_id in non_mock)
            )
    payload = dashboard_payload(
        runs,
        _pricing(args.pricing),
        strong_provider=args.strong_provider,
        strong_model=args.strong_model,
    )
    dump_dashboard_payload(payload, args.output)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
