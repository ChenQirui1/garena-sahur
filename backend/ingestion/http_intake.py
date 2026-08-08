"""Development HTTP intake: ingestion, routing observation, session cleanup, and health checks.

Owner: Jerome & Richard

Registers five routes: `POST /ingest`, `GET /routing/{session_id}/{world_id}`,
`DELETE /sessions/{session_id}` (the only destructive one), `GET /health/live`, and
`GET /health/ready`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict

from backend.ingestion.intake_service import IntakeOutcome, IntakeResult, IntakeService
from backend.ingestion.message_validation import TOPIC_LEGACY_NPC_PROFILE
from backend.orchestration.router_handoff import RouterHandoff, RoutingOutcome
from backend.orchestration.router_port import RoutingDiagnostics, TierCounts

if TYPE_CHECKING:
    from backend.main import Pipeline

# The JSONL adapter opts in separately, and no other transport inherits the choice.
IGNORED_LEGACY_PROFILE_DETAIL = (
    f"{TOPIC_LEGACY_NPC_PROFILE} is accepted for compatibility and ignored; "
    "profiles are loaded from the backend-owned local document"
)

STATUS_FOR_OUTCOME = {
    IntakeOutcome.APPLIED: status.HTTP_202_ACCEPTED,
    IntakeOutcome.STALE: status.HTTP_200_OK,
    IntakeOutcome.DUPLICATE: status.HTTP_200_OK,
    IntakeOutcome.IGNORED: status.HTTP_200_OK,
    IntakeOutcome.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    IntakeOutcome.UNKNOWN_TOPIC: status.HTTP_400_BAD_REQUEST,
    IntakeOutcome.STORAGE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    message: dict[str, Any]


def _intake_service(request: Request) -> IntakeService:
    return cast(IntakeService, request.app.state.pipeline.intake)


def _router_handoff(request: Request) -> RouterHandoff:
    return cast(RouterHandoff, request.app.state.pipeline.handoff)


def _pipeline(request: Request) -> "Pipeline":
    return cast("Pipeline", request.app.state.pipeline)


router = APIRouter()


@router.post("/ingest")
async def ingest(
    submission: IngestionRequest,
    response: Response,
    service: Annotated[IntakeService, Depends(_intake_service)],
) -> dict[str, str | None]:
    if submission.topic == TOPIC_LEGACY_NPC_PROFILE:
        result = IntakeResult(IntakeOutcome.IGNORED, IGNORED_LEGACY_PROFILE_DETAIL)
    else:
        result = await service.submit(submission.topic, submission.message)
    response.status_code = STATUS_FOR_OUTCOME[result.outcome]
    return _as_body(result)


@router.get("/routing/{session_id}/{world_id}")
async def latest_routing(
    session_id: str,
    world_id: str,
    response: Response,
    handoff: Annotated[RouterHandoff, Depends(_router_handoff)],
) -> dict[str, Any] | None:
    outcome = handoff.latest_outcome(session_id, world_id)
    if outcome is None:
        response.status_code = status.HTTP_404_NOT_FOUND
        return None
    return _as_routing_body(outcome)


@router.delete("/sessions/{session_id}")
async def clean_session(
    session_id: str,
    pipeline: Annotated["Pipeline", Depends(_pipeline)],
) -> dict[str, Any]:
    """Erase one session's retained state. Nothing else in the backend deletes it."""
    cleaned = await pipeline.cleanup.clean(session_id)
    return {
        "session_id": cleaned.session_id,
        "rows_removed": cleaned.total_rows,
        "rows_by_table": cleaned.rows_by_table,
    }


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(
    response: Response,
    pipeline: Annotated["Pipeline", Depends(_pipeline)],
) -> dict[str, str]:
    if pipeline.is_ready:
        return {"status": "ready"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": pipeline.readiness_error or "the owned pipeline is not running"}


def _as_body(result: IntakeResult) -> dict[str, str | None]:
    return {"outcome": result.outcome.value, "detail": result.detail}


def _as_routing_body(outcome: RoutingOutcome) -> dict[str, Any]:
    """Project one outcome for local development, carrying every field the Router reported.

    `docs/architecture.md` requires the development adapters to carry the same canonical
    payloads, so a field the Router did report must not be dropped on the way out. A field it
    did not report stays `null` rather than disappearing.
    """
    return {
        "session_id": outcome.session_id,
        "world_id": outcome.world_id,
        "sequence": outcome.sequence,
        "status": outcome.status.value,
        "failure_reason": outcome.failure_reason,
        "assignments": [
            {
                "npc_id": assignment.npc_id,
                "tier": assignment.tier.value,
                "previous_tier": assignment.previous_tier,
                "changed": assignment.changed,
                "reasons": list(assignment.reasons),
                "direct_score": assignment.direct_score,
                "propagated_score": assignment.propagated_score,
                "final_score": assignment.final_score,
            }
            for assignment in outcome.assignments
        ],
        "counts": _as_counts_body(outcome.counts),
        "diagnostics": _as_diagnostics_body(outcome.diagnostics),
    }


def _as_counts_body(counts: TierCounts | None) -> dict[str, int] | None:
    if counts is None:
        return None
    return {
        "focused": counts.focused,
        "reactive": counts.reactive,
        "ambient": counts.ambient,
    }


def _as_diagnostics_body(
    diagnostics: RoutingDiagnostics | None,
) -> dict[str, float] | None:
    if diagnostics is None:
        return None
    return {
        "focused_capacity": diagnostics.focused_capacity,
        "reactive_capacity": diagnostics.reactive_capacity,
        "candidate_count": diagnostics.candidate_count,
        "routing_time_ms": diagnostics.routing_time_ms,
    }
