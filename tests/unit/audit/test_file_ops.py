"""File-operation audit convention: trace, sanitize, retain 90 days."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octop.infra.audit.file_ops import (
    AUDIT_RETENTION_DAYS,
    FILE_EVENT_ACTIONS,
    public_audit_payload,
    record_file_event,
    trail_for_request,
)
from octop.infra.audit.retention import prune_expired_audit
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo

REQUEST_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def audit(db: SqlitePool) -> AuditRepo:
    return AuditRepo(db)


def _payload(row) -> dict:
    assert row.payload is not None
    data = json.loads(row.payload)
    assert isinstance(data, dict)
    return data


def test_file_modification_trail_covers_identity_device_auth_confirm_result(
    audit: AuditRepo,
) -> None:
    for action, result in (
        ("file.authorized", None),
        ("file.confirmed", None),
        ("file.completed", "completed"),
    ):
        record_file_event(
            audit,
            actor="alice",
            action=action,
            request_id=REQUEST_ID,
            device_id="dev_1",
            format="docx",
            operation="overwrite",
            path="纪要.docx",
            result=result,
        )

    trail = trail_for_request(audit, REQUEST_ID)
    assert [row.action for row in trail] == [
        "file.authorized",
        "file.confirmed",
        "file.completed",
    ]
    assert all(row.actor == "alice" for row in trail)
    assert all(row.target == REQUEST_ID for row in trail)
    payload = _payload(trail[0])
    assert payload["device_id"] == "dev_1"
    assert payload["format"] == "docx"
    assert payload["operation"] == "overwrite"
    assert payload["path"] == "纪要.docx"
    assert _payload(trail[-1])["result"] == "completed"


@pytest.mark.parametrize(
    ("action", "result"),
    [
        ("file.authorization_denied", "denied"),
        ("file.rejected", "rejected"),
        ("file.failed", "failed"),
    ],
)
def test_file_audit_records_denied_rejected_and_failed_events(
    audit: AuditRepo, action: str, result: str
) -> None:
    record_file_event(
        audit,
        actor="alice",
        action=action,
        request_id=REQUEST_ID,
        device_id="dev_1",
        format="xlsx",
        operation="delete",
        path="台账.xlsx",
        result=result,
        error_code="forbidden",
    )
    trail = trail_for_request(audit, REQUEST_ID)
    assert [row.action for row in trail] == [action]
    payload = _payload(trail[0])
    assert payload["result"] == result
    assert payload["error_code"] == "forbidden"
    assert action in FILE_EVENT_ACTIONS


def test_file_audit_drops_secrets_instructions_and_file_contents(audit: AuditRepo) -> None:
    record_file_event(
        audit,
        actor="alice",
        action="file.authorized",
        request_id=REQUEST_ID,
        device_id="dev_1",
        format="docx",
        operation="overwrite",
        path="/员工/私有/合同.docx",
        result=None,
        extra={
            "password": "hunter2",
            "api_key": "sk-secret",
            "instruction": "把合同里的身份证号抄下来",
            "text": "完整原文" * 20,
            "content": "FILEBYTES",
        },
    )
    payload = _payload(trail_for_request(audit, REQUEST_ID)[0])
    assert payload["path"] == "合同.docx"
    assert "password" not in payload
    assert "api_key" not in payload
    assert "instruction" not in payload
    assert "text" not in payload
    assert "content" not in payload
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "hunter2" not in dumped
    assert "sk-secret" not in dumped
    assert "身份证" not in dumped
    assert "完整原文" not in dumped


def test_public_audit_payload_redacts_secrets_from_admin_query() -> None:
    raw = json.dumps(
        {
            "device_id": "dev_1",
            "path": "纪要.docx",
            "password": "hunter2",
            "api_key": "sk-live",
            "instruction": "私有任务说明",
            "text": "完整原文",
        },
        ensure_ascii=False,
    )
    cleaned = public_audit_payload(raw)
    assert cleaned is not None
    data = json.loads(cleaned)
    assert data == {"device_id": "dev_1", "path": "纪要.docx"}


def test_audit_older_than_90_days_is_pruned(audit: AuditRepo) -> None:
    now = 1_700_000_000
    audit.write(actor="alice", action="auth.login", ts=now - 91 * 86400)
    audit.write(actor="admin", action="user.set_role", ts=now - 91 * 86400)
    record_file_event(
        audit,
        actor="alice",
        action="file.completed",
        request_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        device_id="dev_1",
        format="docx",
        operation="create",
        path="旧.docx",
        result="completed",
        ts=now - (AUDIT_RETENTION_DAYS + 1) * 86400,
    )
    record_file_event(
        audit,
        actor="alice",
        action="file.completed",
        request_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        device_id="dev_1",
        format="docx",
        operation="create",
        path="新.docx",
        result="completed",
        ts=now - (AUDIT_RETENTION_DAYS - 1) * 86400,
    )
    deleted = prune_expired_audit(audit, now=now)
    assert deleted == 1
    remaining = audit.query()
    assert {row.action for row in remaining} == {"auth.login", "user.set_role", "file.completed"}
    assert [json.loads(row.payload)["path"] for row in remaining if row.payload] == ["新.docx"]


def test_deleting_a_thread_does_not_remove_retained_file_audit(
    db: SqlitePool, audit: AuditRepo
) -> None:
    record_file_event(
        audit,
        actor="alice",
        action="file.completed",
        request_id=REQUEST_ID,
        device_id="dev_1",
        format="docx",
        operation="overwrite",
        path="纪要.docx",
        result="completed",
    )
    threads = ThreadRepo(db)
    UserRepo(db).create(username="alice", password_hash="x", role="user")
    AgentRepo(db).create(agent_id="agt_1", user_id=1, name="Agent")
    threads.insert(
        thread_id="thr_1",
        agent_id="agt_1",
        user_id=1,
        channel_type="dashboard",
        session_key="dash:1",
    )
    threads.delete("thr_1")
    assert trail_for_request(audit, REQUEST_ID)
    assert threads.get("thr_1") is None
