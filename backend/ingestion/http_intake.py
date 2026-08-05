"""Development HTTP intake: one ingestion operation plus routing observation.

Owner: Jerome & Richard
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict

from backend.ingestion.intake_service import IntakeOutcome, IntakeResult, IntakeService
from backend.orchestration.router_handoff import RouterHandoff, RoutingOutcome

STATUS_FOR_OUTCOME = {
    IntakeOutcome.APPLIED: status.HTTP_202_ACCEPTED,
    IntakeOutcome.STALE: status.HTTP_200_OK,
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


router = APIRouter()


@router.post("/ingest")
async def ingest(
    submission: IngestionRequest,
    response: Response,
    service: Annotated[IntakeService, Depends(_intake_service)],
) -> dict[str, str | None]:
    result = service.submit(submission.topic, submission.message)
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


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(
    response: Response,
    handoff: Annotated[RouterHandoff, Depends(_router_handoff)],
) -> dict[str, str]:
    if not handoff.is_running:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "router handoff is not running"}
    return {"status": "ready"}


def _as_body(result: IntakeResult) -> dict[str, str | None]:
    return {"outcome": result.outcome.value, "detail": result.detail}


def _as_routing_body(outcome: RoutingOutcome) -> dict[str, Any]:
    return {
        "session_id": outcome.session_id,
        "world_id": outcome.world_id,
        "source_sequence": outcome.source_sequence,
        "status": outcome.status.value,
        "failure_reason": outcome.failure_reason,
        "assignments": [
            {
                "npc_id": assignment.npc_id,
                "tier": assignment.tier.value,
                "reasons": list(assignment.reasons),
            }
            for assignment in outcome.assignments
        ],
    }
