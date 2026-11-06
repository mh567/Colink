"""Enterprise probes must not leak credentials or infer untested capabilities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from octop.infra.agents.providers.probe import probe_provider_row


def provider(**changes):
    return SimpleNamespace(
        **{
            "name": "enterprise",
            "kind": "openai",
            "api_key": "secret-test-key",
            "base_url": "http://model.internal/v1",
            "extra_json": None,
            "get_models": lambda: [{"id": "actual-model"}],
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_missing_address_never_calls_a_public_default():
    with patch("octop.infra.agents.providers.probe.build_probe_chat_model") as build:
        result = await probe_provider_row(provider(base_url=None))
    assert result["ok"] is False
    assert result["error_type"] == "invalid_config"
    build.assert_not_called()


@pytest.mark.asyncio
async def test_upstream_failure_does_not_echo_credentials_or_log_body(caplog):
    model = AsyncMock()
    model.ainvoke.side_effect = RuntimeError("response echoed secret-test-key")
    with patch("octop.infra.agents.providers.probe.build_probe_chat_model", return_value=model):
        result = await probe_provider_row(provider())
    assert result["ok"] is False
    assert "secret-test-key" not in str(result) + caplog.text


@pytest.mark.asyncio
async def test_stream_probe_requires_visible_streamed_content():
    class Model:
        async def astream(self, _input):
            yield AIMessageChunk(content="hello")
            yield AIMessageChunk(content=" world")

    with patch("octop.infra.agents.providers.probe.build_probe_chat_model", return_value=Model()):
        result = await probe_provider_row(provider(), capability="stream")
    assert result["ok"] is True
    assert result["capability"] == "stream"


@pytest.mark.asyncio
async def test_text_response_is_not_proof_of_tool_call_support():
    model = AsyncMock()
    model.bind_tools = lambda *args, **kwargs: model
    model.ainvoke.return_value = AIMessage(content="I can use tools")
    with patch("octop.infra.agents.providers.probe.build_probe_chat_model", return_value=model):
        result = await probe_provider_row(provider(), capability="tools")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_tool_probe_validates_actual_tool_name_and_arguments():
    model = AsyncMock()
    model.bind_tools = lambda *args, **kwargs: model
    model.ainvoke.return_value = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "colink_probe",
                "args": {"value": "ping"},
                "id": "probe-1",
            }
        ],
    )
    with patch("octop.infra.agents.providers.probe.build_probe_chat_model", return_value=model):
        result = await probe_provider_row(provider(), capability="tools")
    assert result["ok"] is True
    assert result["capability"] == "tools"
