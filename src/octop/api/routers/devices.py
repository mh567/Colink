"""Employee native device enrollment and administrator trust management."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, Header, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from octop.api.deps import current_user, get_server, require_admin, resolve_user_from_token
from octop.infra.control.devices import (
    DEVICE_TIMEOUT_SECONDS,
    DeviceConnection,
    DeviceManager,
    DeviceRow,
    DeviceStatus,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()


class DeviceView(BaseModel):
    device_id: str
    owner_user_id: int
    owner_username: str
    name: str
    platform: str
    status: DeviceStatus
    online: bool = Field(description="True only for an approved, live native connection.")
    created_at: int
    last_seen: int | None


class DeviceHello(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["hello"]
    access_token: str = Field(min_length=1, max_length=8192)
    public_key: str = Field(min_length=44, max_length=44)
    name: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=64)


class DeviceProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["proof"]
    signature: str = Field(min_length=88, max_length=88)


class DeviceHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["heartbeat"]
    access_token: str | None = Field(default=None, min_length=1, max_length=8192)


class DocxAuthorizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "create"]
    agent_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Optional employee-owned expert, rechecked before the native file action.",
    )


class DocxAuthorization(BaseModel):
    allowed: Literal[True] = True


class DocxGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Optional employee-owned expert used with its currently authorized resources.",
    )
    instruction: str = Field(min_length=1, max_length=8000, description="Employee's document task.")
    text: str = Field(
        max_length=60000,
        description="Necessary text extracted on the native client; no original file.",
    )


class DocxGeneratedText(BaseModel):
    text: str = Field(min_length=1, max_length=60000)


class XlsxGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Optional employee-owned expert used with its currently authorized resources.",
    )
    instruction: str = Field(
        min_length=1, max_length=8000, description="Employee's spreadsheet task."
    )
    text: str = Field(
        min_length=1,
        max_length=60000,
        description="JSON sheets with names and rows extracted on the client; no original XLSX.",
    )


class XlsxGeneratedData(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=60000,
        description="Model JSON sheets/rows. The native client validates and calculates before writing.",
    )


@router.post(
    "/{device_id}/docx/authorize",
    response_model=DocxAuthorization,
    summary="Authorize a local DOCX operation on the current native device",
    description="Requires an approved live native session and explicit feature/action grants. "
    "Administrators have no implicit execution rights. The client also checks directory grants.",
)
async def authorize_docx(
    device_id: str,
    body: DocxAuthorizeBody,
    x_colink_device_session: str = Header(default="", max_length=128),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DocxAuthorization:
    server.device_manager.authorize_docx(
        user, device_id, x_colink_device_session, body.action, agent_id=body.agent_id
    )
    return DocxAuthorization()


@router.post(
    "/{device_id}/xlsx/authorize",
    response_model=DocxAuthorization,
    summary="Authorize a local XLSX operation on the current native device",
    description="Requires an approved live native session and explicit feature/action grants. "
    "Administrators have no implicit execution rights. The client also checks directory grants.",
)
async def authorize_xlsx(
    device_id: str,
    body: DocxAuthorizeBody,
    x_colink_device_session: str = Header(default="", max_length=128),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DocxAuthorization:
    server.device_manager.authorize_docx(
        user, device_id, x_colink_device_session, body.action, format="xlsx", agent_id=body.agent_id
    )
    return DocxAuthorization()


@router.post(
    "/{device_id}/docx/generate",
    response_model=DocxGeneratedText,
    summary="Generate document text for an authorized native DOCX task",
    description="Uses the selected private expert's authorized model, or the active enterprise model, without tools. "
    "Requires read and create rights, checked again after generation. Original files stay local.",
)
async def generate_docx(
    device_id: str,
    body: DocxGenerateBody,
    x_colink_device_session: str = Header(default="", max_length=128),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DocxGeneratedText:
    assert server.app_runtime is not None
    text = await server.device_manager.generate_docx_text(
        user,
        device_id,
        x_colink_device_session,
        instruction=body.instruction,
        text=body.text,
        agent_id=body.agent_id,
        models=server.app_runtime.agent_registry.host_models,
    )
    return DocxGeneratedText(text=text)


@router.post(
    "/{device_id}/xlsx/generate",
    response_model=XlsxGeneratedData,
    summary="Generate structured spreadsheet data for an authorized native XLSX task",
    description="Uses the selected private expert's authorized model, or the active enterprise model, without tools. "
    "Requires read and create rights, checked again after generation. Original files stay local.",
)
async def generate_xlsx(
    device_id: str,
    body: XlsxGenerateBody,
    x_colink_device_session: str = Header(default="", max_length=128),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> XlsxGeneratedData:
    assert server.app_runtime is not None
    text = await server.device_manager.generate_docx_text(
        user,
        device_id,
        x_colink_device_session,
        instruction=body.instruction,
        text=body.text,
        agent_id=body.agent_id,
        models=server.app_runtime.agent_registry.host_models,
        format="xlsx",
    )
    return XlsxGeneratedData(text=text)


def _view(manager: DeviceManager, row: DeviceRow) -> DeviceView:
    fields = asdict(row)
    fields.pop("public_key")
    return DeviceView(**fields, online=manager.is_online(row))


@router.get("", response_model=list[DeviceView], summary="List the signed-in employee's devices")
async def list_devices(
    user: User = Depends(current_user), server: OctopServer = Depends(get_server)
) -> list[DeviceView]:
    manager = server.device_manager
    return [_view(manager, row) for row in manager.list(user)]


@router.get(
    "/manage",
    response_model=list[DeviceView],
    summary="List device enrollment requests and trust decisions",
    description="Administrators may manage trust; this grants no connection or execution rights.",
)
async def manage_devices(
    user: User = Depends(require_admin()), server: OctopServer = Depends(get_server)
) -> list[DeviceView]:
    manager = server.device_manager
    return [_view(manager, row) for row in manager.list(user, managed=True)]


@router.post(
    "/{device_id}/approve", response_model=DeviceView, summary="Approve an employee's device"
)
async def approve_device(
    device_id: str,
    user: User = Depends(require_admin()),
    server: OctopServer = Depends(get_server),
) -> DeviceView:
    manager = server.device_manager
    return _view(manager, manager.set_status(user, device_id, "approved"))


@router.post(
    "/{device_id}/revoke",
    response_model=DeviceView,
    summary="Revoke an employee's device",
    description="Immediately invalidates the live device session; reconnecting requires approval.",
)
async def revoke_device(
    device_id: str,
    user: User = Depends(require_admin()),
    server: OctopServer = Depends(get_server),
) -> DeviceView:
    manager = server.device_manager
    return _view(manager, manager.set_status(user, device_id, "revoked"))


async def _receive(ws: WebSocket) -> str:
    frame = await asyncio.wait_for(ws.receive(), timeout=DEVICE_TIMEOUT_SECONDS)
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    raw = frame.get("text")
    if not isinstance(raw, str) or len(raw) > 16384:
        raise OctopError(ErrorCode.AUTH_FAILED, "invalid device text frame")
    return raw


@router.websocket("/connect")
async def connect_device(ws: WebSocket) -> None:
    await ws.accept()
    connection: DeviceConnection | None = None
    manager: DeviceManager | None = None
    locale = "en"
    try:
        server: OctopServer = ws.app.state.octop_server
        if not server.database_bound:
            raise OctopError(ErrorCode.SETUP_REQUIRED, "setup required")
        hello = DeviceHello.model_validate_json(await _receive(ws))
        user = resolve_user_from_token(server, hello.access_token)
        locale = user.locale
        message = f"colink-device-v1:{user.id}:{secrets.token_urlsafe(32)}"
        await ws.send_json({"type": "challenge", "message": message})
        proof = DeviceProof.model_validate_json(await _receive(ws))
        manager = server.device_manager
        manager.verify_proof(hello.public_key, message, proof.signature)
        # Password reset or disable during the challenge must invalidate this handshake.
        user = resolve_user_from_token(server, hello.access_token)
        connection = manager.connect(
            user, public_key=hello.public_key, name=hello.name, platform=hello.platform
        )
        access_token = hello.access_token
        while True:
            row = manager.heartbeat(connection)
            await ws.send_json(
                {
                    "type": "status",
                    "device_id": row.device_id,
                    "status": row.status,
                    "online": manager.is_online(row),
                    **(
                        {"session_token": connection.session_token}
                        if row.status == "approved"
                        else {}
                    ),
                }
            )
            heartbeat = DeviceHeartbeat.model_validate_json(await _receive(ws))
            access_token = heartbeat.access_token or access_token
            user = resolve_user_from_token(server, access_token)
            if user.id != connection.user_id:
                raise OctopError(ErrorCode.FORBIDDEN, "device token belongs to another employee")
            if manager is not server.device_manager:
                raise OctopError(ErrorCode.FORBIDDEN, "device database changed")
    except WebSocketDisconnect:
        pass
    except (OctopError, ValidationError, TimeoutError) as exc:
        err = (
            exc
            if isinstance(exc, OctopError)
            else OctopError(ErrorCode.AUTH_FAILED, "invalid device message")
        )
        await ws.send_json({"type": "error", **err.to_envelope(locale=locale)["error"]})
        await ws.close(code=4403 if err.status == 403 else 4401)
    finally:
        if connection is not None and manager is not None:
            manager.disconnect(connection)
