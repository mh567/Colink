"""Unit tests for built-in knowledge-base LangChain tools."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.config import var_child_runnable_config

from octop.config import DatabaseConfig, OctopConfig
from octop.infra.knowledge.plane import postgres_database
from octop.infra.knowledge.tools import KnowledgeToolServices, build_knowledge_tools


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _tool_by_name(tools: list, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    raise KeyError(name)


def test_tool_services_follow_control_plane_driver() -> None:
    """Search must see a driver swap; indexing already reads services.config."""
    manager = SimpleNamespace(
        _config=OctopConfig(database=DatabaseConfig(driver="sqlite")),
        _repos=SimpleNamespace(
            knowledge_repo=object(),
            settings_repo=object(),
            provider_repo=object(),
        ),
    )
    services = KnowledgeToolServices(manager)
    assert postgres_database(services) is None

    manager._config = OctopConfig(
        database=DatabaseConfig(
            driver="postgresql",
            host="db.example",
            database="octop",
            user="octop",
        )
    )
    assert postgres_database(services) is manager._config.database


def test_search_knowledge_default_description_is_empty_catalog_fallback() -> None:
    tool = _tool_by_name(build_knowledge_tools(SimpleNamespace()), "search_knowledge")
    assert tool.description == "No knowledge bases are attached this turn."


@pytest.mark.asyncio
async def test_admin_search_cannot_retrieve_another_users_private_base(
    tmp_path, monkeypatch
) -> None:
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.repos.knowledge import KnowledgeRepo
    from octop.infra.db.repos.settings import SettingsRepo
    from octop.infra.db.repos.users import UserRepo
    from octop.infra.knowledge import retrieve
    from octop.infra.knowledge.index import KnowledgeIndex

    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    users = UserRepo(pool)
    admin = users.create(username="admin", password_hash="h", role="admin")
    owner = users.create(username="owner", password_hash="h", role="user")
    repo = KnowledgeRepo(pool)
    base = repo.create_base(owner_user_id=owner, name="Private")
    doc = repo.create_document(
        kb_id=base.id,
        filename="private.md",
        content_type="text/markdown",
        byte_size=1,
        status="ready",
    )
    KnowledgeIndex(base.id).replace_doc_chunks(doc.id, ["private information"], [[1.0, 0.0]])
    services = SimpleNamespace(knowledge_repo=repo, settings_repo=SettingsRepo(pool))
    monkeypatch.setattr(retrieve, "assert_knowledge_usable", lambda *_args: None)
    monkeypatch.setattr(
        retrieve, "embed_intranet_knowledge_query", AsyncMock(return_value=[[1.0, 0.0]])
    )
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")

    with _configurable(user=str(admin), user_is_admin=True, knowledge_base_ids=[base.id]):
        result = await tool.ainvoke({"query": "information"})

    assert "private information" not in result
    assert "private.md" not in result


@pytest.mark.asyncio
async def test_search_refuses_public_embedding_endpoint(monkeypatch) -> None:
    import httpx

    from octop.infra.knowledge import retrieve

    settings = {
        "knowledge_embedding_backend": "remote",
        "knowledge_embedding_model": "embed",
        "knowledge_embedding_provider_id": "1",
    }
    provider = SimpleNamespace(base_url="http://8.8.8.8/v1", api_key="secret")
    services = SimpleNamespace(
        settings_repo=SimpleNamespace(get=settings.get),
        provider_repo=SimpleNamespace(get=lambda _id: provider),
        knowledge_repo=SimpleNamespace(list_visible=lambda _id: []),
    )
    monkeypatch.setattr(retrieve, "assert_knowledge_usable", lambda *_args: None)
    sent = []

    def reject(_transport, request):
        sent.append(request.url)
        raise AssertionError("unexpected embedding request")

    async def reject_async(transport, request):
        return reject(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async)
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    with _configurable(user="1", knowledge_base_ids=["kb"]):
        result = await tool.ainvoke({"query": "private query"})

    assert sent == []
    assert "Knowledge search failed" in result


@pytest.mark.asyncio
async def test_search_knowledge_requires_selected_bases() -> None:
    services = SimpleNamespace(
        knowledge_repo=MagicMock(),
        settings_repo=MagicMock(),
        provider_repo=MagicMock(),
    )
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    with _configurable(user="1", knowledge_base_ids=[]):
        out = await tool.ainvoke({"query": "billing policy"})
    assert "No knowledge bases selected" in out


@pytest.mark.asyncio
async def test_search_knowledge_returns_retrieved_context() -> None:
    services = SimpleNamespace(
        knowledge_repo=MagicMock(),
        settings_repo=MagicMock(),
        provider_repo=MagicMock(),
    )
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    retrieved = AsyncMock(return_value="[KB: Docs]\nuseful passage")
    with (
        _configurable(
            user="7",
            user_is_admin=False,
            knowledge_base_ids=["kb-1"],
            locale="zh",
        ),
        patch("octop.infra.knowledge.tools.retrieve_context", retrieved),
    ):
        out = await tool.ainvoke({"query": "refund rules", "k": 3})

    assert out == "[KB: Docs]\nuseful passage"
    retrieved.assert_awaited_once()
    kwargs = retrieved.await_args.kwargs
    assert kwargs["user_id"] == 7
    assert kwargs["is_admin"] is False
    assert kwargs["query"] == "refund rules"
    assert kwargs["knowledge_base_ids"] == ["kb-1"]
    assert kwargs["k"] == 3
    assert kwargs["locale"] == "zh"


@pytest.mark.asyncio
async def test_search_knowledge_empty_hits_message() -> None:
    services = SimpleNamespace(
        knowledge_repo=MagicMock(),
        settings_repo=MagicMock(),
        provider_repo=MagicMock(),
    )
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    with (
        _configurable(user="1", knowledge_base_ids=["kb-1"]),
        patch(
            "octop.infra.knowledge.tools.retrieve_context",
            AsyncMock(return_value=""),
        ),
    ):
        out = await tool.ainvoke({"query": "nothing"})
    assert "No relevant passages found" in out


@pytest.mark.asyncio
async def test_search_failure_is_explicit_and_does_not_expose_upstream_error(monkeypatch) -> None:
    from octop.infra.knowledge import retrieve

    services = SimpleNamespace(settings_repo=SimpleNamespace(get=lambda _key: None))
    monkeypatch.setattr(retrieve, "assert_knowledge_usable", lambda *_args: None)

    def fail(*_args):
        raise RuntimeError("upstream echoed secret-token and private document")

    monkeypatch.setattr(retrieve, "embed_knowledge_texts", fail)
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    with _configurable(user="1", knowledge_base_ids=["kb-1"], locale="en"):
        out = await tool.ainvoke({"query": "policy"})
    assert "Knowledge search failed" in out
    assert "No relevant passages" not in out
    assert "secret-token" not in out
    assert "private document" not in out
