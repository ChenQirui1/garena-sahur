"""FastAPI application and the owned pipeline every transport adapter is built on.

Owner: Jerome & Richard
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.ingestion import http_intake
from backend.ingestion.intake_service import IntakeService
from backend.ingestion.world_state_store import WorldStateStore
from backend.orchestration.development_router import AmbientOnlyRouter
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.router_port import RouterPort


@dataclass(frozen=True, slots=True)
class Pipeline:
    """The owned intake-to-Router pipeline, shared by every transport adapter."""

    intake: IntakeService
    handoff: RouterHandoff


def build_pipeline(settings: Settings, router: RouterPort | None = None) -> Pipeline:
    """Wire one persistent Router into a fresh pipeline for one service lifecycle."""
    handoff = RouterHandoff(router or AmbientOnlyRouter())
    return Pipeline(
        intake=IntakeService(
            store=WorldStateStore(),
            handoff=handoff,
            max_snapshot_candidates=settings.max_snapshot_candidates,
        ),
        handoff=handoff,
    )


def create_app(settings: Settings | None = None, router: RouterPort | None = None) -> FastAPI:
    settings = settings or load_settings()
    pipeline = build_pipeline(settings, router)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await pipeline.handoff.start()
        try:
            yield
        finally:
            await pipeline.handoff.stop()

    app = FastAPI(title="Spotlight backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.pipeline = pipeline
    app.include_router(http_intake.router)
    return app
