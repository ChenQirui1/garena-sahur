#!/usr/bin/env python3
"""Execute repeatable 10/25/50/100-NPC benchmark traces.

Owner: Elson & Daniel

This command uses ``mock-publisher`` over local JSONL; it does not require NATS or any other
broker.  Results are synthetic/mock evidence and say so in every telemetry file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Run from a checkout without an installation step: the repository root holds ``backend``.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.telemetry.benchmark import (  # noqa: E402
    DEFAULT_DURATION_SECONDS,
    DEFAULT_EPOCH_MS,
    DEFAULT_NPC_COUNTS,
    DEFAULT_RATE_HZ,
    DEFAULT_SEED,
    run_suite,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--npc-counts",
        nargs="+",
        type=int,
        default=list(DEFAULT_NPC_COUNTS),
        help="crowd-size sweep (default: 10 25 50 100)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--epoch-ms", type=int, default=DEFAULT_EPOCH_MS)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "benchmark_runs",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace only exact artifacts for the requested deterministic run IDs",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> list[dict[str, object]]:
    artifacts = await run_suite(
        args.output_dir,
        npc_counts=args.npc_counts,
        seed=args.seed,
        epoch_ms=args.epoch_ms,
        rate_hz=args.rate,
        duration_seconds=args.duration,
        root=ROOT,
        overwrite=args.overwrite,
    )
    return [artifact.as_record() for artifact in artifacts]


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    print(json.dumps(asyncio.run(_run(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
