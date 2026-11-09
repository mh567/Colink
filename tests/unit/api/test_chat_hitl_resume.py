"""Dashboard HITL resume SSE must persist follow-up approvals in the pending store."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from octop.api.routers.chat.models import HitlResumeBody
from octop.api.routers.chat.routes import iter_dashboard_hitl_resume_sse, resume_hitl
from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator


def _parse_sse_chunks(raw: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        data_line = next(
            (line[6:] for line in block.split("\n") if line.startswith("data: ")),
            None,
        )
        if data_line is None:
            continue
        chunks.append(json.loads(data_line))
    return chunks


@pytest.fixture
def pending_file_approval(tmp_path: Path):
    target = tmp_path / "report.docx"
    target.write_bytes(b"original")
    hitl = HitlChannelCoordinator()
    pending = hitl.store.register(
        thread_id="employee-thread",
        agent_id="shared-agent",
        user_id=1,
        session_key="shared-agent:dashboard:1:dm",
        channel_type="dashboard",
        action_requests=[{"name": "write_file", "args": {"path": "report.docx"}}],
        review_configs=None,
    )

    async def run_confirmed_action(*, before_resume=None, **_: object):
        if request_waits_for_new_turn:
            hitl.store.expire_pending_for_thread(
                "employee-thread", agent_id="shared-agent", user_id=1
            )
        if before_resume is not None:
            before_resume()
        with target.open("ab") as handle:
            handle.write(b" updated")
        yield {"type": "token", "content": "updated"}

    gateway = SimpleNamespace(
        processor=SimpleNamespace(
            hitl_coordinator=hitl, iter_hitl_resume_chunks=run_confirmed_action
        ),
    )
    runtime = SimpleNamespace(
        agent_registry=SimpleNamespace(get_row=lambda _: SimpleNamespace(user_id=1, is_shared=1)),
        gateway=gateway,
    )

    request_waits_for_new_turn = False

    async def submit(employee: int, pending_id: str | None, *, new_turn: bool = False):
        nonlocal request_waits_for_new_turn
        request_waits_for_new_turn = new_turn
        return await resume_hitl(
            "shared-agent",
            HitlResumeBody(
                thread_id="employee-thread", decisions=[{"type": "approve"}], pending_id=pending_id
            ),
            Request({"type": "http", "headers": []}, receive=AsyncMock(return_value={})),
            SimpleNamespace(id=employee),
            SimpleNamespace(app_runtime=runtime),
        )

    return target, pending.pending_id, submit


@pytest.mark.asyncio
@pytest.mark.parametrize(("employee", "with_id"), [(2, True), (2, False), (1, False)])
async def test_file_approval_requires_the_owners_exact_pause(
    pending_file_approval, employee: int, with_id: bool
) -> None:
    target, pending_id, submit = pending_file_approval
    response = await submit(employee, pending_id if with_id else None)
    frames = _parse_sse_chunks("".join([frame async for frame in response.body_iterator]))
    assert frames[0]["type"] == "error"
    assert target.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_two_approval_requests_for_one_pause_update_the_file_once(
    pending_file_approval,
) -> None:
    target, pending_id, submit = pending_file_approval
    first = await submit(1, pending_id)
    second = await submit(1, pending_id)
    assert _parse_sse_chunks("".join([frame async for frame in first.body_iterator]))[0] == {
        "type": "token",
        "content": "updated",
    }
    assert (
        _parse_sse_chunks("".join([frame async for frame in second.body_iterator]))[0]["type"]
        == "error"
    )
    assert target.read_bytes() == b"original updated"


@pytest.mark.asyncio
async def test_file_approval_expires_if_a_new_turn_starts_before_execution(pending_file_approval):
    target, pending_id, submit = pending_file_approval
    response = await submit(1, pending_id, new_turn=True)
    frames = _parse_sse_chunks("".join([frame async for frame in response.body_iterator]))
    assert frames[0]["type"] == "error"
    assert target.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_registers_followup_hitl_required() -> None:
    async def _resume(*, before_resume: Callable[[], None], **_kwargs: object):
        before_resume()
        yield {"type": "token", "content": "ok"}
        yield {
            "type": "hitl_required",
            "request": {
                "action_requests": [
                    {"name": "execute", "args": {"command": "ls -la /private/etc"}},
                ],
                "review_configs": [
                    {"action_name": "execute", "allowed_decisions": ["approve", "reject"]},
                ],
            },
        }

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()
    first = hitl.store.register(
        thread_id="thr-follow",
        agent_id="agent-1",
        user_id=1,
        session_key="agent-1:dashboard:1:dm",
        channel_type="dashboard",
        action_requests=[{"name": "execute", "args": {"command": "ls -la /etc"}}],
        review_configs=None,
    )

    frames: list[str] = []
    async for frame in iter_dashboard_hitl_resume_sse(
        server=object(),
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="agent-1",
        thread_id="thr-follow",
        user_id=1,
        decisions=[{"type": "approve"}],
        pending=first,
        session_key="agent-1:dashboard:1:dm",
        channel_type="dashboard",
        locale="zh",
        is_disconnected=AsyncMock(return_value=False),
    ):
        frames.append(frame)

    chunks = _parse_sse_chunks("".join(frames))
    hitl_chunks = [c for c in chunks if c.get("type") == "hitl_required"]
    assert hitl_chunks
    assert chunks[-1] == {"type": "done", "hitl_paused": True}

    followup = hitl.store.resolve_pending_for_thread(
        "thr-follow",
        agent_id="agent-1",
        user_id=1,
    )
    assert followup is not None
    assert hitl_chunks[0]["request"]["pending_id"] == followup.pending_id
    assert followup.pending_id != first.pending_id
    assert followup.action_requests[0]["args"]["command"] == "ls -la /private/etc"
    assert hitl.store.get(first.pending_id) is not None
    assert hitl.store.get(first.pending_id).status == "approved"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_wakes_team_member_without_host_resume() -> None:
    from types import SimpleNamespace

    hitl = HitlChannelCoordinator()
    pending = hitl.store.register(
        thread_id="thr-room",
        agent_id="host",
        user_id=1,
        session_key="host:dashboard:1:dm",
        channel_type="dashboard",
        action_requests=[{"name": "execute", "args": {"command": "df"}}],
        review_configs=None,
    )
    pending.resume_agent_id = "member"
    pending.resume_thread_id = "thr-room~member"
    delivered: dict[str, object] = {}

    def deliver(pending_id: str, decisions: list[dict[str, str]]) -> bool:
        delivered["id"] = pending_id
        delivered["decisions"] = decisions
        return True

    processor = SimpleNamespace(teams=SimpleNamespace(deliver_member_hitl=deliver))
    frames: list[str] = []
    async for frame in iter_dashboard_hitl_resume_sse(
        server=object(),
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="host",
        thread_id="thr-room",
        user_id=1,
        decisions=[{"type": "approve"}],
        pending=pending,
        session_key="host:dashboard:1:dm",
        channel_type="dashboard",
        locale="zh",
        is_disconnected=AsyncMock(return_value=False),
    ):
        frames.append(frame)

    chunks = _parse_sse_chunks("".join(frames))
    assert chunks == [{"type": "done", "team_member_continuing": True}]
    assert delivered == {"id": pending.pending_id, "decisions": [{"type": "approve"}]}
    assert hitl.store.get(pending.pending_id) is not None
    assert hitl.store.get(pending.pending_id).status == "approved"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_expires_when_member_job_is_gone() -> None:
    from types import SimpleNamespace

    hitl = HitlChannelCoordinator()
    pending = hitl.store.register(
        thread_id="thr-room",
        agent_id="host",
        user_id=1,
        session_key="host:dashboard:1:dm",
        channel_type="dashboard",
        action_requests=[{"name": "execute", "args": {}}],
        review_configs=None,
    )
    pending.resume_agent_id = "member"
    pending.resume_thread_id = "thr-room~member"
    processor = SimpleNamespace(
        teams=SimpleNamespace(deliver_member_hitl=lambda *_args, **_kwargs: False)
    )
    frames: list[str] = []
    async for frame in iter_dashboard_hitl_resume_sse(
        server=object(),
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="host",
        thread_id="thr-room",
        user_id=1,
        decisions=[{"type": "reject"}],
        pending=pending,
        session_key="host:dashboard:1:dm",
        channel_type="dashboard",
        locale="en",
        is_disconnected=AsyncMock(return_value=False),
    ):
        frames.append(frame)

    chunks = _parse_sse_chunks("".join(frames))
    assert chunks[0]["type"] == "error"
    assert "expired" in chunks[0]["message"].lower()
    assert hitl.store.get(pending.pending_id) is not None
    assert hitl.store.get(pending.pending_id).status == "expired"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_registers_without_prior_pending() -> None:
    async def _resume(*, before_resume: Callable[[], None], **_kwargs: object):
        before_resume()
        yield {
            "type": "hitl_required",
            "request": {
                "action_requests": [{"name": "execute", "args": {"command": "echo hi"}}],
            },
        }

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()

    async for _ in iter_dashboard_hitl_resume_sse(
        server=object(),
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="agent-1",
        thread_id="thr-orphan",
        user_id=9,
        decisions=[{"type": "approve"}],
        pending=None,
        session_key="sk-orphan",
        channel_type="dashboard",
        locale="en",
        is_disconnected=AsyncMock(return_value=False),
    ):
        pass

    pending = hitl.store.resolve_pending_for_thread(
        "thr-orphan",
        agent_id="agent-1",
        user_id=9,
    )
    assert pending is not None
    assert pending.action_requests[0]["args"]["command"] == "echo hi"


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_finishes_after_client_disconnect() -> None:
    completed = False

    async def _resume(*, before_resume: Callable[[], None], **_kwargs: object):
        before_resume()
        nonlocal completed
        yield {"type": "token", "content": "hidden after disconnect"}
        completed = True

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()

    frames = [
        frame
        async for frame in iter_dashboard_hitl_resume_sse(
            server=object(),
            processor=processor,
            hitl_coordinator=hitl,
            agent_id="agent-1",
            thread_id="thr-disconnected",
            user_id=9,
            decisions=[{"type": "respond", "message": "answer"}],
            pending=None,
            session_key="sk-disconnected",
            channel_type="dashboard",
            locale="en",
            is_disconnected=AsyncMock(return_value=True),
        )
    ]

    assert completed is True
    assert frames == []


@pytest.mark.asyncio
async def test_dashboard_hitl_resume_marks_pending_resolved_when_stream_errors() -> None:
    async def _resume(*, before_resume: Callable[[], None], **_kwargs: object):
        before_resume()
        raise RuntimeError("stream died")
        yield {}  # pragma: no cover

    processor = MagicMock()
    processor.iter_hitl_resume_chunks = _resume
    hitl = HitlChannelCoordinator()
    first = hitl.store.register(
        thread_id="thr-err",
        agent_id="agent-1",
        user_id=1,
        session_key="sk-err",
        channel_type="dashboard",
        action_requests=[{"name": "ask_user_question", "args": {"questions": []}}],
        review_configs=None,
    )

    frames: list[str] = []
    async for frame in iter_dashboard_hitl_resume_sse(
        server=object(),
        processor=processor,
        hitl_coordinator=hitl,
        agent_id="agent-1",
        thread_id="thr-err",
        user_id=1,
        decisions=[{"type": "respond", "message": "ok"}],
        pending=first,
        session_key="sk-err",
        channel_type="dashboard",
        locale="zh",
        is_disconnected=AsyncMock(return_value=False),
    ):
        frames.append(frame)

    chunks = _parse_sse_chunks("".join(frames))
    assert any(c.get("type") == "error" for c in chunks)
    resolved = hitl.store.get(first.pending_id)
    assert resolved is not None
    assert resolved.status == "expired"
    assert hitl.store.resolve_pending_for_thread("thr-err", agent_id="agent-1", user_id=1) is None
