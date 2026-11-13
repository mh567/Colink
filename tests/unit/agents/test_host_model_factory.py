"""Host-level harness factory used by one-shot text calls."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from octop_harness.config import ModelConfig, ProviderConfig
from octop_harness.llm.factory import ChatModelFactory

from octop.infra.agents.providers.host_factory import HostModelFactory
from octop.infra.errors import ErrorCode, OctopError


def _provider(provider_id: str, *model_ids: str, disabled: str = "") -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        name=provider_id,
        models=[ModelConfig(id=model_id, enabled=model_id != disabled) for model_id in model_ids],
    )


def test_auto_uses_first_enabled_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_get_chat_model(
        self: ChatModelFactory, model_ref: str, *, max_retries: int | None = None
    ) -> MagicMock:
        del self, max_retries
        captured["ref"] = model_ref
        return MagicMock()

    monkeypatch.setattr(ChatModelFactory, "get_chat_model", fake_get_chat_model)
    host = HostModelFactory()
    host.sync(
        [
            _provider("first", "off", "on", disabled="off"),
            _provider("second", "other"),
        ]
    )

    access = host.get(None)

    assert captured["ref"] == "first/on"
    assert access.model_ref == "first/on"
    host.close()


def test_explicit_ref_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_get_chat_model(
        self: ChatModelFactory, model_ref: str, *, max_retries: int | None = None
    ) -> MagicMock:
        del self, max_retries
        captured["ref"] = model_ref
        return MagicMock()

    monkeypatch.setattr(ChatModelFactory, "get_chat_model", fake_get_chat_model)
    host = HostModelFactory()
    host.sync([_provider("openai", "gpt-4o-mini", "gpt-4o")])

    access = host.get("openai/gpt-4o")

    assert captured["ref"] == "openai/gpt-4o"
    assert access.model_ref == "openai/gpt-4o"
    host.close()


def test_auto_alias_and_missing_factory() -> None:
    host = HostModelFactory()
    with pytest.raises(OctopError) as exc:
        host.get("auto")
    assert exc.value.code == ErrorCode.INTERNAL_ERROR
    assert exc.value.message == "model factory unavailable"


def test_unknown_ref_is_rejected() -> None:
    host = HostModelFactory()
    host.sync([_provider("openai", "gpt-4o")])
    with pytest.raises(OctopError) as exc:
        host.get("other/model")
    assert exc.value.message == "model unavailable for text assist"
    host.close()


def test_sync_drop_closes_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ChatModelFactory, "get_chat_model", lambda *args, **kwargs: MagicMock())
    host = HostModelFactory()
    host.sync([_provider("openai", "gpt-4o")])
    host.sync([])
    with pytest.raises(OctopError) as exc:
        host.get("auto")
    assert exc.value.code == ErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize("with_parent", [False, True], ids=["root", "nested"])
async def test_intranet_model_does_not_send_private_content_to_enabled_tracing(
    monkeypatch: pytest.MonkeyPatch,
    with_parent: bool,
) -> None:
    import json

    import httpx
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tracers.langchain import wait_for_all_tracers
    from langsmith import Client, RunTree, tracing_context

    from octop.infra.utils import ssrf_guard

    provider = _provider("openai", "gpt-4o")
    question = "How many items are in stock?"
    page_text = "Internal inventory: 42 items"
    answer = "The inventory has 42 items."
    public_input = "Public trace control"
    model_requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        model_requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def pinned(host: str, pin_ip: str, *, strict_host: bool) -> httpx.MockTransport:
        assert (host, pin_ip, strict_host) == ("api.example.com", "10.20.30.40", True)
        return httpx.MockTransport(respond)

    trace_writes = MagicMock()
    monkeypatch.setattr(ssrf_guard, "PinnedIPTransport", pinned)
    monkeypatch.setattr(Client, "create_run", trace_writes)
    monkeypatch.setattr(Client, "update_run", trace_writes)
    tracer = Client(
        api_url="https://trace.example.com",
        api_key="test-key",
        info={"version": "test"},
        auto_batch_tracing=False,
    )
    parent = RunTree(name="outer", run_type="chain", client=tracer) if with_parent else None
    factory = HostModelFactory()
    factory.sync([provider])
    try:
        with tracing_context(enabled=True, parent=parent, client=tracer):
            result = await factory.ainvoke_intranet_text(
                "openai/gpt-4o",
                f"{question}\n{page_text}",
                system="Answer using the visible page text.",
                base_url=provider.base_url,
                host="api.example.com",
                pin_ip="10.20.30.40",
            )
            await RunnableLambda(lambda text: text).ainvoke(public_input)
            wait_for_all_tracers()
    finally:
        factory.close()

    assert result == answer
    assert len(model_requests) == 1
    assert model_requests[0].url == httpx.URL("https://api.example.com/v1/chat/completions")
    body = json.loads(model_requests[0].content)
    assert body["messages"][-1]["content"] == f"{question}\n{page_text}"
    trace_payloads = repr(trace_writes.call_args_list)
    assert public_input in trace_payloads
    for private_text in (page_text, question, answer):
        assert private_text not in trace_payloads
