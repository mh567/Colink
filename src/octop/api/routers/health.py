"""Liveness probe (no auth)."""

from __future__ import annotations

import platform
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop import __version__
from octop.api.deps import get_server

router = APIRouter()


class HealthResponse(BaseModel):
    ok: bool = Field(..., description="Service health status")
    version: str = Field(..., description="Application version")
    arch: str = Field(..., description="Server CPU machine architecture")
    started_at: int = Field(..., description="Unix timestamp when server started")
    db: bool = Field(..., description="Database reachability and binding state")
    users_loaded: int = Field(..., description="Number of users loaded in database")
    agents_running: int = Field(..., description="Number of running agent instances")

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@router.get("", response_model=HealthResponse, summary="Health check")
async def health(server: Any = Depends(get_server)) -> HealthResponse:
    """Liveness probe: database reachability, user count, and loaded agents. No auth required."""
    bound = server.database_bound
    users = server.user_manager.count() if server.user_manager is not None else 0
    agents = (
        len(server.app_runtime.agent_registry.list_rows()) if server.app_runtime is not None else 0
    )
    return HealthResponse(
        ok=True,
        version=__version__,
        arch=platform.machine(),
        started_at=int(server._started_at),
        db=bool(bound),
        users_loaded=int(users),
        agents_running=int(agents),
    )
