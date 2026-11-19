"""Per-agent ``Memory`` instance management for the dashboard router.

Owns a tiny LRU cache of ``octop_memory.core.Memory`` instances
keyed by ``agent_id``. Backend may be sqlite (default workspace file) or
postgres when ``config_json.memory.backend`` says so.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop.infra.agents.memory.backend import open_memory_kwargs
from octop.infra.agents.memory.thread_cleanup import (
    agent_config_from_row,
    workspace_for_agent_row,
)
from octop.infra.agents.memory.thread_cleanup import (
    memory_namespace as storage_namespace,
)
from octop.infra.agents.workspace.dir import host_system_dir
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.permissions import user_has_permission

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.db.repos.agents import AgentRow
    from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

_MEMORY_NS_PREFIX = "agent_"
memory_io_lock = threading.RLock()


def memory_namespace(agent_id: str) -> str:
    """Return the memory namespace octop-memory uses for ``agent_id``."""
    return f"{_MEMORY_NS_PREFIX}{agent_id}"


def memory_db_path(workspace_dir: Path) -> Path:
    """Return the default SQLite file path within an agent workspace.

    Prefer an explicit on-disk file. When neither exists, treat a populated
    ``.octop/_builtin_skills`` tree as the new-layout signal (empty ``.octop/``
    alone is not enough for legacy agents).
    """
    nested = workspace_dir / ".octop" / "memory.sqlite"
    root = workspace_dir / "memory.sqlite"
    if nested.exists():
        return nested
    if root.exists():
        return root
    if (workspace_dir / ".octop" / "_builtin_skills").is_dir():
        return nested
    return root


def memory_db_path_for_cfg(workspace_dir: Path, cfg: dict[str, Any] | None) -> Path:
    """Return the default SQLite file path for a specific agent config."""
    return host_system_dir(workspace_dir, cfg) / "memory.sqlite"


_MAX_CACHED = 16


class _MemoryCache:
    """Thread-safe LRU of ``(agent_id) -> (memory, bridge, fingerprint)``."""

    def __init__(self, max_size: int = _MAX_CACHED) -> None:
        self._max_size = max_size
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[Any, Any, str]] = OrderedDict()

    def get_or_open(
        self,
        agent_id: str,
        *,
        backend: str,
        backend_config: dict[str, Any] | None,
        fingerprint: str,
    ) -> tuple[Any, Any]:
        from octop_memory.adapters.bridge.handlers import Bridge  # noqa: PLC0415
        from octop_memory.core import Memory  # noqa: PLC0415

        with self._lock:
            cached = self._entries.get(agent_id)
            if cached is not None and cached[2] == fingerprint:
                self._entries.move_to_end(agent_id)
                return cached[0], cached[1]

            ns = memory_namespace(agent_id)
            memory = Memory(
                namespace=ns,
                backend=backend,
                backend_config=backend_config,
            )
            bridge = Bridge(memory)
            self._entries[agent_id] = (memory, bridge, fingerprint)
            while len(self._entries) > self._max_size:
                _, evicted = self._entries.popitem(last=False)
                logger.debug("memory dashboard cache evicted agent_id=%s", evicted)
            return memory, bridge

    def invalidate(self, agent_id: str | None = None) -> None:
        with self._lock:
            if agent_id is None:
                self._entries.clear()
            else:
                self._entries.pop(agent_id, None)


_CACHE = _MemoryCache()


def _open_memory_for_agent(registry: AgentManager, row: AgentRow) -> tuple[Any, Any]:
    agent_id = row.agent_id
    cfg = agent_config_from_row(row)
    workspace = workspace_for_agent_row(row, paths=registry.paths)
    ns, backend, backend_config = open_memory_kwargs(
        agent_id=agent_id,
        cfg=cfg,
        octop_config=registry.octop_config,
        workspace_dir=workspace,
    )
    canonical = storage_namespace(agent_id)
    if any(
        other.agent_id != agent_id and storage_namespace(other.agent_id) == canonical
        for other in registry.list_rows()
    ):
        raise OctopError.localized(ErrorCode.FORBIDDEN)
    fingerprint = f"{ns}:{backend}:{backend_config}"
    status = registry.memory_slim.status(agent_id)
    if isinstance(status, dict) and status.get("operation") == "restore":
        fingerprint += f":restore:{status.get('job_id')}:{status.get('phase')}"
    if backend == "sqlite":
        db_path = Path(
            (backend_config or {}).get("db_path") or memory_db_path_for_cfg(workspace, cfg)
        )
        if not db_path.exists():
            logger.debug(
                "memory db not yet created for agent %s; opening will create empty schema",
                agent_id,
            )
    return _CACHE.get_or_open(
        agent_id,
        backend=backend,
        backend_config=backend_config,
        fingerprint=fingerprint,
    )


def authorize_memory(
    registry: AgentManager, agent_id: str, user: User, *, require_grant: bool = True
) -> AgentRow:
    from octop.infra.agents.memory.personal import memory_restore_pending
    from octop.infra.backup.auto import get_backup_operation

    if require_grant and not user_has_permission(user, "memory"):
        raise OctopError.localized(ErrorCode.FORBIDDEN)
    row = registry.get_row(agent_id)
    if row is None:
        raise OctopError.localized(ErrorCode.AGENT_NOT_FOUND)
    if row.user_id is None or row.user_id != user.id or row.is_shared:
        raise OctopError.localized(ErrorCode.FORBIDDEN)
    if get_backup_operation() == "restore" or memory_restore_pending(registry.paths.root):
        raise OctopError.localized(ErrorCode.AGENT_BUSY)
    status = registry.memory_slim.status(agent_id)
    if isinstance(status, dict) and status.get("phase") not in {"done", "failed", "skipped"}:
        raise OctopError.localized(ErrorCode.AGENT_BUSY)
    return row


def call_memory_rpc(
    *,
    registry: AgentManager,
    agent_id: str,
    method: str,
    params: dict[str, Any] | None,
    user: User,
    require_grant: bool = True,
) -> Any:
    with memory_io_lock:
        row = authorize_memory(registry, agent_id, user, require_grant=require_grant)
        return _call_memory_rpc(registry, row, method, params)


def _call_memory_rpc(
    registry: AgentManager, row: AgentRow, method: str, params: dict[str, Any] | None
) -> Any:
    _memory, bridge = _open_memory_for_agent(registry, row)
    if method == "promote_candidate":
        candidate = _memory.get_candidate((params or {}).get("candidate_id", ""))
        if candidate is not None and candidate.payload.get("manual_revision"):
            workspace = registry.workspace_for_agent(row.agent_id)
            if workspace is None:
                raise OctopError.localized(ErrorCode.AGENT_BUSY)
            params = {
                **(params or {}),
                "manual_documents": {
                    name: (workspace.download_bytes(name) or b"").decode("utf-8")
                    for name in ("USER.md", "MEMORY.md")
                },
            }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    response = bridge.handle(payload)
    if "error" in response:
        err = response["error"]
        code = err.get("code")
        message = err.get("message", "memory bridge error")
        if code == -32010:  # ERR_PATH_NOT_FOUND
            raise OctopError(ErrorCode.NOT_FOUND, message)
        if code == -32602:  # ERR_INVALID_PARAMS
            raise OctopError(ErrorCode.INTERNAL_ERROR, message, status=400)
        if code == -32601:  # ERR_METHOD_NOT_FOUND
            raise OctopError(
                ErrorCode.INTERNAL_ERROR,
                f"unknown memory dashboard method: {method!r}",
            )
        raise OctopError(ErrorCode.INTERNAL_ERROR, message)
    return response["result"]


def invalidate_cached_memory(agent_id: str | None = None) -> None:
    _CACHE.invalidate(agent_id)


__all__ = [
    "call_memory_rpc",
    "invalidate_cached_memory",
    "memory_db_path",
    "memory_db_path_for_cfg",
    "memory_namespace",
]
