"""90-day audit retention for the control-plane audit log."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from octop.infra.audit.file_ops import AUDIT_RETENTION_DAYS, FILE_EVENT_ACTIONS, file_audit_cutoff
from octop.infra.db.repos.audit import AuditRepo, AuditRow

if TYPE_CHECKING:
    from octop.infra.cron.manager import CronManager
    from octop.infra.server import OctopServer

logger = logging.getLogger(__name__)

AUDIT_RETENTION_JOB_ID = "octop_audit_retention"
AUDIT_RETENTION_SCHEDULE = "cron:20 3 * * *"


def prune_expired_audit(repo: AuditRepo, *, now: int | None = None) -> int:
    """Delete only expired local file audit events."""
    return repo.delete_before(file_audit_cutoff(now), actions=FILE_EVENT_ACTIONS)


def query_retained_audit(
    repo: AuditRepo,
    *,
    since: int | None = None,
    actor: str | None = None,
    action: str | None = None,
    request_id: str | None = None,
    limit: int = 100,
) -> list[AuditRow]:
    return repo.query(
        since=since,
        actor=actor,
        action=action,
        target=request_id,
        retain_since=file_audit_cutoff(),
        retained_actions=FILE_EVENT_ACTIONS,
        limit=limit,
    )


async def install_audit_retention_job(cron_manager: CronManager, *, server: OctopServer) -> None:
    """Prune immediately, then schedule a daily retention job."""

    def _prune() -> None:
        if server.services is None:
            return
        deleted = prune_expired_audit(server.services.audit_repo)
        if deleted:
            logger.info("pruned %s audit rows older than %s days", deleted, AUDIT_RETENTION_DAYS)

    async def _run() -> None:
        await asyncio.to_thread(_prune)

    await _run()

    cron_manager.schedule_system_job(
        AUDIT_RETENTION_JOB_ID,
        trigger=AUDIT_RETENTION_SCHEDULE,
        func=_run,
    )
