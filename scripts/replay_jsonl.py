"""Replay a JSONL capture of upstream messages through the backend.

Owner: Jerome & Richard
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Run from a checkout without an installation step: the repository root holds `backend`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import load_settings
from backend.ingestion.intake_service import IntakeOutcome
from backend.main import build_pipeline, replay_jsonl


async def replay(path: Path) -> None:
    results = await replay_jsonl(path, build_pipeline(load_settings()))

    for outcome in IntakeOutcome:
        submitted = sum(1 for result in results if result.outcome is outcome)
        if submitted:
            print(f"{outcome.value}: {submitted}")


def main() -> None:
    asyncio.run(replay(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
