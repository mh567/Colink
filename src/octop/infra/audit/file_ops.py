"""Convention for tracing local file modifications without private content."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.audit import AuditRepo, AuditRow
from octop.infra.errors import ErrorCode, OctopError

AUDIT_RETENTION_DAYS = 90

FILE_EVENT_ACTIONS = frozenset(
    {
        "file.authorized",
        "file.authorization_denied",
        "file.confirmed",
        "file.rejected",
        "file.completed",
        "file.failed",
    }
)

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "api_key",
        "apikey",
        "secret",
        "token",
        "access_token",
        "session_token",
        "content",
        "text",
        "instruction",
        "source_text",
        "file",
        "body",
        "prompt",
    }
)


def public_path(path: str | None) -> str | None:
    """Keep a file name only — never a private directory or absolute path."""
    if path is None:
        return None
    name = Path(str(path).replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        return None
    return name


def public_audit_payload(raw: str | None) -> str | None:
    """Drop secrets, prompts and file contents from an admin-visible payload."""
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw if len(raw) <= 200 else None
    if not isinstance(data, dict):
        return None
    cleaned = {
        key: value
        for key, value in data.items()
        if isinstance(key, str)
        and key.lower().replace("_", "") not in {item.replace("_", "") for item in _SENSITIVE_KEYS}
        and key.lower() not in _SENSITIVE_KEYS
    }
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def _file_payload(
    *,
    device_id: str,
    format: str,
    operation: str,
    path: str | None,
    result: str | None,
    error_code: str | None,
) -> str:
    payload: dict[str, Any] = {
        "device_id": device_id,
        "format": format,
        "operation": operation,
    }
    public = public_path(path)
    if public is not None:
        payload["path"] = public
    if result is not None:
        payload["result"] = result
    if error_code is not None:
        payload["error_code"] = error_code
    return json.dumps(payload, ensure_ascii=False)


def record_file_event(
    repo: AuditRepo,
    *,
    actor: str,
    action: str,
    request_id: str,
    device_id: str,
    format: str,
    operation: str,
    path: str | None = None,
    result: str | None = None,
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
    ts: int | None = None,
) -> None:
    """Record one file-modification event. Other tickets should call this helper."""
    if action not in FILE_EVENT_ACTIONS:
        raise ValueError(f"unsupported file audit action: {action}")
    _ = extra
    repo.write(
        actor=actor,
        action=action,
        target=request_id,
        payload=_file_payload(
            device_id=device_id,
            format=format,
            operation=operation,
            path=path,
            result=result,
            error_code=error_code,
        ),
        ts=ts,
    )


def record_file_receipt(
    repo: AuditRepo,
    *,
    actor: str,
    request_id: str,
    device_id: str,
    event: str,
    format: str,
    operation: str,
    path: str | None,
    error_code: str | None,
) -> None:
    """Accept an idempotent native receipt only for its own authorized operation."""
    action = f"file.{event}"
    payload = _file_payload(
        device_id=device_id,
        format=format,
        operation=operation,
        path=path,
        result=None if event == "confirmed" else event,
        error_code=error_code,
    )
    target = (format, operation, public_path(path))
    terminal = {"file.rejected", "file.completed", "file.failed"}

    def should_write(rows: list[AuditRow]) -> bool:
        authorization: str | None = None
        stages: list[str] = []
        for row in rows:
            if row.action not in FILE_EVENT_ACTIONS or not row.payload:
                continue
            fields = json.loads(row.payload)
            if fields.get("device_id") != device_id:
                continue
            if (fields.get("format"), fields.get("operation"), fields.get("path")) != target:
                break
            if row.action in {"file.authorized", "file.authorization_denied"}:
                authorization = row.action
            if authorization and row.action == action and row.payload == payload:
                return False
            stages.append(row.action)
        else:
            if authorization and not terminal.intersection(stages):
                if event == "failed":
                    return True
                if authorization == "file.authorized":
                    confirmed = "file.confirmed" in stages
                    if operation == "create" and event == "completed":
                        return True
                    if operation != "create" and (
                        (event in {"confirmed", "rejected"} and not confirmed)
                        or (event == "completed" and confirmed)
                    ):
                        return True
        raise OctopError.localized(ErrorCode.LOCAL_FILE_AUDIT_CONFLICT)

    repo.write_target_event(
        actor=actor,
        target=request_id,
        action=action,
        payload=payload,
        since=file_audit_cutoff(),
        should_write=should_write,
    )


def file_audit_cutoff(now: int | None = None) -> int:
    return (now if now is not None else now_ts()) - AUDIT_RETENTION_DAYS * 86400


def trail_for_request(repo: AuditRepo, request_id: str) -> list[AuditRow]:
    """Oldest-first events that share a file-operation request id."""
    return list(reversed(repo.query(target=request_id, limit=500)))
