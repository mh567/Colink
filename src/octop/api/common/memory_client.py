"""HTTP adapter for the shared agent-memory service."""

from __future__ import annotations

from typing import Any

from octop.infra.agents.memory.client import (
    call_memory_rpc as _call_memory_rpc,
)
from octop.infra.agents.memory.client import (
    invalidate_cached_memory,
    memory_db_path,
    memory_db_path_for_cfg,
    memory_namespace,
)
from octop.infra.errors import ErrorCode, OctopError


def call_memory_rpc(
    *,
    agent_id: str,
    method: str,
    params: dict[str, Any] | None,
    user: Any,
    as_user: int | None,
    server: Any,
) -> Any:
    if as_user is not None and as_user != user.id:
        raise OctopError.localized(ErrorCode.FORBIDDEN)
    return _call_memory_rpc(
        registry=server.app_runtime.agent_registry,
        agent_id=agent_id,
        method=method,
        params=params,
        user=user,
        require_grant=method != "manual_document_review_status",
    )


__all__ = [
    "call_memory_rpc",
    "invalidate_cached_memory",
    "memory_db_path",
    "memory_db_path_for_cfg",
    "memory_namespace",
]
