"""Development JSONL intake over the same application service as HTTP.

Owner: Jerome & Richard
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from backend.ingestion.intake_service import IntakeOutcome, IntakeResult, IntakeService
from backend.ingestion.message_validation import TOPIC_LEGACY_NPC_PROFILE

REJECTING_OUTCOMES = frozenset(
    {IntakeOutcome.INVALID, IntakeOutcome.UNKNOWN_TOPIC, IntakeOutcome.STORAGE_UNAVAILABLE}
)

# The HTTP adapter opts in separately, and no other transport inherits the choice.
IGNORED_LEGACY_PROFILE_DETAIL = (
    f"{TOPIC_LEGACY_NPC_PROFILE} is accepted for compatibility and ignored; "
    "profiles are loaded from the backend-owned local document"
)


class JsonlIntakeError(ValueError):
    """A JSONL line could not be accepted, so the remaining lines were not submitted."""

    def __init__(self, line_number: int, reason: str) -> None:
        super().__init__(f"line {line_number}: {reason}")
        self.line_number = line_number
        self.reason = reason


async def submit_jsonl(lines: Iterable[str], service: IntakeService) -> list[IntakeResult]:
    """Submit each topic-message record in order, failing fast on the first rejection."""
    results: list[IntakeResult] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        topic, message = _read_record(line_number, line)
        if topic == TOPIC_LEGACY_NPC_PROFILE:
            result = IntakeResult(IntakeOutcome.IGNORED, IGNORED_LEGACY_PROFILE_DETAIL)
        else:
            result = await service.submit(topic, message)
        if result.outcome in REJECTING_OUTCOMES:
            raise JsonlIntakeError(line_number, f"{result.outcome.value}: {result.detail}")
        results.append(result)
    return results


def _read_record(line_number: int, line: str) -> tuple[str, dict[str, Any]]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as malformed:
        raise JsonlIntakeError(line_number, f"malformed JSON: {malformed.msg}") from malformed

    if not isinstance(record, dict) or record.keys() != {"topic", "message"}:
        raise JsonlIntakeError(line_number, "record must have exactly topic and message")
    if not isinstance(record["topic"], str) or not isinstance(record["message"], dict):
        raise JsonlIntakeError(line_number, "topic must be a string and message an object")

    return record["topic"], record["message"]


async def _replay(path: Path) -> None:
    # Imported here so the adapter itself stays free of the FastAPI application wiring.
    from backend.config import load_settings
    from backend.main import build_pipeline

    pipeline = build_pipeline(load_settings())
    await pipeline.store.open()
    await pipeline.handoff.start()
    try:
        results = await submit_jsonl(path.read_text().splitlines(), pipeline.intake)
        await pipeline.handoff.wait_until_idle()
    finally:
        await pipeline.handoff.stop()
        await pipeline.store.close()

    for outcome in IntakeOutcome:
        submitted = sum(1 for result in results if result.outcome is outcome)
        if submitted:
            print(f"{outcome.value}: {submitted}")


if __name__ == "__main__":
    asyncio.run(_replay(Path(sys.argv[1])))
