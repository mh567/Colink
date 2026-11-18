"""90-day audit retention for the control-plane audit log."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from octop.infra.audit.file_ops import AUDIT_RETENTION_DAYS
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.audit import AuditRepo

if TYPE_CHECKING:
    from octop.infra.cron.manager import CronManager
    from octop.infra.server import OctopServer

logger = logging.getLogger(__name__)

AUDIT_RETENTION_JOB_ID = "octop_audit_retention"
AUDIT_RETENTION_SCHEDULE = "cron:20 3 * * *"


def prune_expired_audit(repo: AuditRepo, *, now: int | None = None) -> int:
    """Delete audit rows older than 90 days. Returns the deleted count."""
    cutoff = (now if now is not None else now_ts()) - AUDIT_RETENTION_DAYS * 86400
    return repo.delete_before(cutoff)


def install_audit_retention_job(cron_manager: CronManager, *, server: OctopServer) -> None:
    """Prune immediately, then schedule a daily retention job."""

    def _prune() -> None:
        if server.services is None:
            return
        deleted = prune_expired_audit(server.services.audit_repo)
        if deleted:
            logger.info("pruned %s audit rows older than %s days", deleted, AUDIT_RETENTION_DAYS)

    _prune()

    async def _run() -> None:
        _prune()

    cron_manager.schedule_system_job(
        AUDIT_RETENTION_JOB_ID,
        trigger=AUDIT_RETENTION_SCHEDULE,
        func=_run,
    )


def retention_job_registered(cron_manager: Any) -> bool:
    return bool(cron_manager.has_system_job(AUDIT_RETENTION_JOB_ID))
