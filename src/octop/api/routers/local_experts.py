"""HTTP adapters for local experts and reviewed offline editions."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.infra.agents.experts.local_tasks import (
    LocalExpertConfig,
    local_expert_payload,
    local_resources,
    save_local_expert,
)
from octop.infra.agents.experts.offline import (
    OfflinePackage,
    export_offline,
    install_offline,
    publish_offline,
    releases,
    require_admin,
)
from octop.infra.agents.settings.profile import parse_config_json

router = APIRouter(prefix="/experts/local")


class LocalExpertResponse(LocalExpertConfig):
    agent_id: str
    published_expert_id: str | None = None


class ReleaseResponse(OfflinePackage):
    id: str


class ResourceOption(BaseModel):
    id: str
    name: str


class ResourceResponse(BaseModel):
    models: list[ResourceOption]
    actions: list[ResourceOption]
    skills: list[ResourceOption]
    knowledge_bases: list[ResourceOption]


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: OfflinePackage
    reviewed: bool = Field(
        description="Administrator confirms review of sources, licenses, content and permissions"
    )


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    source: str = Field(min_length=1, max_length=2000)
    license: str = Field(min_length=1, max_length=4000)


@router.get(
    "/resources",
    response_model=ResourceResponse,
    summary="List caller-authorized local expert resources",
)
async def resources(
    user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> dict[str, Any]:
    return local_resources(server.services, user)


@router.get("", response_model=list[LocalExpertResponse], summary="List caller-owned local experts")
async def list_local(
    user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> list[dict[str, Any]]:
    return [
        local_expert_payload(row)
        for row in server.app_runtime.agent_registry.list_agents(user.id)
        if parse_config_json(row.config_json).get("local_only")
    ]


@router.post(
    "", status_code=201, response_model=LocalExpertResponse, summary="Create a private local expert"
)
async def create_local(
    body: LocalExpertConfig, user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> dict[str, Any]:
    return await save_local_expert(server.services, server.app_runtime.agent_registry, user, body)


@router.put(
    "/{agent_id}",
    response_model=LocalExpertResponse,
    summary="Edit caller-owned local expert resources",
)
async def update_local(
    agent_id: str,
    body: LocalExpertConfig,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return await save_local_expert(
        server.services, server.app_runtime.agent_registry, user, body, agent_id=agent_id
    )


@router.post(
    "/{agent_id}/export",
    response_model=OfflinePackage,
    summary="Export explicit expert configuration without private resources",
    description="Rejects knowledge references and unreviewed skills. Never exports workspace files, conversations, memory or credentials.",
)
async def export_local(
    agent_id: str,
    body: ExportBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> OfflinePackage:
    return export_offline(server.services, user, agent_id, **body.model_dump())


@router.get(
    "/releases",
    response_model=list[ReleaseResponse],
    summary="List approved offline expert editions",
)
async def list_releases(
    user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> list[dict[str, Any]]:
    return releases(server.services)


@router.post(
    "/releases/preview",
    response_model=OfflinePackage,
    summary="Preview an offline package for administrator review",
)
async def preview(body: OfflinePackage, user: Any = Depends(current_user)) -> OfflinePackage:
    require_admin(user)
    return body


@router.post(
    "/releases",
    status_code=201,
    response_model=ReleaseResponse,
    summary="Approve and publish a new immutable offline edition",
)
async def publish(
    body: PublishBody, user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> dict[str, Any]:
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: publish_offline(server.services, user, body.package, reviewed=body.reviewed)
    )


@router.post(
    "/releases/{release_id}/install",
    status_code=201,
    response_model=LocalExpertResponse,
    summary="Install an approved edition within caller permissions",
)
async def install(
    release_id: str,
    body: LocalExpertConfig,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return await install_offline(
        server.services, server.app_runtime.agent_registry, user, release_id, body
    )
