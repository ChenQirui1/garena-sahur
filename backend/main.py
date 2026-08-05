"""Main FastAPI/backend entry point; connects all Python modules.

Owner: Jerome & Richard
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.ingestion import http_intake
from backend.ingestion.intake_service import IntakeService
from backend.ingestion.world_state_store import WorldStateStore
from backend.orchestration.router_handoff import RouterHandoff
from backend.orchestration.router_port import RouterPort
from backend.orchestration.stub_router import AmbientStubRouter


def create_app(settings: Settings | None = None, router: RouterPort | None = None) -> FastAPI:
    """Wire the owned pipeline around one persistent Router for this service lifecycle."""
    settings = settings or load_settings()
    handoff = RouterHandoff(router or AmbientStubRouter())
    service = IntakeService(
        store=WorldStateStore(),
        handoff=handoff,
        max_snapshot_candidates=settings.max_snapshot_candidates,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await handoff.start()
        try:
            yield
        finally:
            await handoff.stop()

    app = FastAPI(title="Spotlight backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.router_handoff = handoff
    app.state.intake_service = service
    app.include_router(http_intake.router)
    return app
