"""Provider connectivity probes (shared by API and CLI)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from octop.i18n import tr
from octop.infra.agents.providers import KIND_TO_PROTOCOL
from octop.infra.agents.providers.opencode_session import ensure_opencode_session_header

logger = logging.getLogger(__name__)

_FETCH_MODELS_TIMEOUT_S = 30.0
_EMBEDDING_PROBE_TEXT = "ping"


def parse_provider_extra(row: Any) -> dict[str, Any]:
    """Return the provider ``extra_json`` object, or ``{}`` when missing / invalid."""
    raw = getattr(row, "extra_json", None)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return extra if isinstance(extra, dict) else {}


def provider_headers(row: Any) -> dict[str, str]:
    headers = parse_provider_extra(row).get("headers")
    return dict(headers) if isinstance(headers, dict) else {}


def build_probe_chat_model(
    row: Any,
    *,
    model_id: str | None = None,
    api_mode: str | None = None,
    api_fallback: str | None = None,
) -> Any:
    """Construct a chat model from a provider row for probing."""
    from octop_harness.config import ModelConfig, ProviderConfig
    from octop_harness.llm.factory import build_chat_model

    protocol = KIND_TO_PROTOCOL.get(row.kind, row.kind)
    base_url = _required_base_url(row.base_url)
    headers = ensure_opencode_session_header(row.name, provider_headers(row), base_url=base_url)
    models = row.get_models() if hasattr(row, "get_models") else []
    mid = resolve_probe_model_id(row, model_id)
    if not mid:
        raise ValueError("model_id is required")
    entry = next((m for m in models if m.get("id") == mid), None)
    model_data = dict(entry) if isinstance(entry, dict) else {}
    model_data["id"] = mid
    model_data.setdefault("name", mid)
    if api_mode is not None:
        model_data["api_mode"] = api_mode
    if api_fallback is not None:
        model_data["api_fallback"] = api_fallback
    elif api_mode is not None and api_mode != "responses":
        model_data["api_fallback"] = "none"
    model = ModelConfig.from_dict(model_data)

    from octop.infra.agents.providers.store import resolve_provider_stream_usage

    provider = ProviderConfig(
        id=row.name,
        name=row.name,
        protocol=protocol,
        base_url=base_url,
        api_key=row.api_key or "",
        headers=headers,
        stream_usage=resolve_provider_stream_usage(
            provider_name=row.name,
            extra=parse_provider_extra(row),
        ),
    )
    return build_chat_model(provider, model)


def make_probe_provider_row(
    *,
    name: str,
    kind: str,
    api_key: str | None,
    base_url: str | None,
    model_id: str,
    api_mode: str = "chat_completions",
    api_fallback: str = "none",
    extra_json: str | None = None,
    embedding: bool = False,
) -> Any:
    """Build a ProviderRow-like object for connectivity probes."""
    model: dict[str, Any] = {
        "id": model_id,
        "name": model_id,
        "api_mode": api_mode,
        "api_fallback": api_fallback,
    }
    if embedding:
        model["embedding"] = True
        model["task"] = "embedding"
    return SimpleNamespace(
        name=name,
        kind=kind,
        base_url=base_url,
        api_key=api_key,
        extra_json=extra_json,
        get_models=lambda: [model],
    )


def resolve_probe_model_id(row: Any, model_id: str | None) -> str:
    """Select the same model for probe execution and persisted evidence."""
    from octop.infra.agents.providers.model_flags import is_onnx_local_provider

    if is_onnx_local_provider(
        getattr(row, "name", None), provider_api_key=getattr(row, "api_key", None)
    ):
        return _onnx_probe_model_id(row, model_id)
    models = row.get_models() if hasattr(row, "get_models") else []
    if model_id:
        return model_id
    if models:
        return str(models[0].get("id") or "").strip()
    return ""


def _onnx_probe_model_id(row: Any, model_id: str | None) -> str:
    """Explicit request wins, then the row's enabled entry, then the first one.

    The first entry for the ONNX row is rarely the model configured for use.
    """
    if model_id:
        return model_id
    models = row.get_models() if hasattr(row, "get_models") else []
    entries = [m for m in models if isinstance(m, dict) and str(m.get("id") or "").strip()]
    chosen = next((m for m in entries if m.get("enabled")), None) or (
        entries[0] if entries else None
    )
    return str(chosen["id"]) if chosen else ""


def _probe_model_entry(row: Any, model_id: str) -> dict[str, Any]:
    models = row.get_models() if hasattr(row, "get_models") else []
    entry = next((m for m in models if isinstance(m, dict) and m.get("id") == model_id), None)
    return entry if isinstance(entry, dict) else {}


def _should_probe_embedding(row: Any, *, model_id: str, embedding: bool | None) -> bool:
    if embedding is True:
        return True
    from octop.infra.agents.providers.model_flags import is_embedding_model

    return is_embedding_model(
        _probe_model_entry(row, model_id),
        provider_name=getattr(row, "name", None),
        provider_api_key=getattr(row, "api_key", None),
    )


def _required_base_url(base_url: str | None) -> str:
    root = (base_url or "").strip().rstrip("/")
    parsed = urlsplit(root)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("explicit service URL required")
    return root


def _embeddings_url(base_url: str | None) -> str:
    return f"{_required_base_url(base_url)}/embeddings"


def _probe_failure(kind: str, locale: str) -> dict[str, Any]:
    return {"ok": False, "error_type": kind, "error": tr(f"provider_probe.{kind}", locale)}


def _failure_kind(exc: BaseException | str) -> str:
    from octop.i18n.domains.stream import classify_stream_error_message, exception_display_message

    status = getattr(exc, "status_code", None)
    if status is None and isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    if status in (401, 403):
        return "auth_error"
    if status == 429:
        return "rate_limit"
    if status == 402:
        return "quota_exceeded"
    if status == 404:
        return "invalid_model"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError, httpx.RequestError)):
        return "network_error"
    key = classify_stream_error_message(exception_display_message(exc))
    return {
        "octop:stream_errors.auth": "auth_error",
        "octop:stream_errors.rate_limit": "rate_limit",
        "octop:stream_errors.insufficient_balance": "quota_exceeded",
        "octop:stream_errors.timeout_network": "network_error",
        "octop:stream_errors.invalid_request": "invalid_config",
    }.get(key or "", "call_failed")


def _friendly_probe_error(exc: BaseException | str, *, locale: str) -> str:
    return str(_probe_failure(_failure_kind(exc), locale)["error"])


async def _probe_embedding_endpoint(
    row: Any, *, model_id: str, locale: str = "en"
) -> dict[str, Any]:
    """POST OpenAI-compatible ``{base}/embeddings`` and time the round-trip."""
    started = time.perf_counter()
    url = _embeddings_url(getattr(row, "base_url", None))
    headers: dict[str, str] = {"Authorization": f"Bearer {getattr(row, 'api_key', None) or ''}"}
    extra = ensure_opencode_session_header(
        getattr(row, "name", None),
        provider_headers(row),
        base_url=getattr(row, "base_url", None),
    )
    headers.update(extra)
    try:
        async with httpx.AsyncClient(timeout=_FETCH_MODELS_TIMEOUT_S) as client:
            response = await client.post(
                url,
                headers=headers,
                json={"model": model_id, "input": [_EMBEDDING_PROBE_TEXT]},
            )
    except Exception as exc:
        return _probe_failure(_failure_kind(exc), locale)

    if response.status_code >= 400:
        error = httpx.HTTPStatusError(
            "provider rejected request", request=response.request, response=response
        )
        return _probe_failure(_failure_kind(error), locale)

    from octop.infra.knowledge.embed import parse_embedding_response

    try:
        vectors = parse_embedding_response(response.json(), 1)
    except (ValueError, RuntimeError):
        return _probe_failure("invalid_response", locale)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "dimension": len(vectors[0]),
        "capability": "embedding",
    }


async def probe_provider_row(
    row: Any,
    *,
    model_id: str | None = None,
    api_mode: str | None = None,
    api_fallback: str | None = None,
    embedding: bool | None = None,
    capability: Literal["chat", "stream", "tools"] = "chat",
    locale: str = "en",
) -> dict[str, Any]:
    """Probe a provider: chat models get a one-token ping; embedding models POST /embeddings."""
    from octop.infra.agents.providers.model_flags import is_onnx_local_provider

    if is_onnx_local_provider(
        getattr(row, "name", None), provider_api_key=getattr(row, "api_key", None)
    ):
        # Local runtime: probe on-device. It has no base URL, so the remote
        # path below would post its placeholder API key to OpenAI.
        from octop.infra.agents.providers.onnx_service import probe_local_model

        local_result = await probe_local_model(resolve_probe_model_id(row, model_id))
        if local_result.get("latency_ms") is not None:
            local_result["latency_ms"] = int(local_result["latency_ms"])
        if not local_result.get("ok") and local_result.get("error"):
            local_result["error"] = _friendly_probe_error(str(local_result["error"]), locale=locale)
        return local_result

    try:
        _required_base_url(getattr(row, "base_url", None))
    except ValueError:
        return _probe_failure("invalid_config", locale)
    mid = resolve_probe_model_id(row, model_id)
    if not mid:
        return _probe_failure("invalid_config", locale)
    if _should_probe_embedding(row, model_id=mid, embedding=embedding):
        return await _probe_embedding_endpoint(row, model_id=mid, locale=locale)
    started = time.perf_counter()
    result: Any
    try:
        chat = build_probe_chat_model(
            row,
            model_id=mid,
            api_mode=api_mode,
            api_fallback=api_fallback,
        )
        async with asyncio.timeout(30):
            if capability == "stream":
                visible = False
                result = None
                async for chunk in chat.astream("Reply with ping"):
                    visible = visible or bool(getattr(chunk, "content", None))
                    result = chunk
                if not visible:
                    return _probe_failure("invalid_response", locale)
            elif capability == "tools":
                bound = chat.bind_tools(
                    [
                        {
                            "type": "function",
                            "function": {
                                "name": "colink_probe",
                                "description": "Connectivity test; no side effects.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"value": {"type": "string"}},
                                    "required": ["value"],
                                },
                            },
                        }
                    ],
                    tool_choice="colink_probe",
                )
                result = await bound.ainvoke("Call colink_probe with value exactly ping.")
                calls = getattr(result, "tool_calls", None)
                if not isinstance(calls, list) or not any(
                    call.get("name") == "colink_probe" and call.get("args") == {"value": "ping"}
                    for call in calls
                    if isinstance(call, dict)
                ):
                    return _probe_failure("unsupported_tools", locale)
            else:
                result = await chat.ainvoke("ping")
                if not getattr(result, "content", None):
                    return _probe_failure("invalid_response", locale)
    except Exception as exc:
        kind = _failure_kind(exc)
        logger.info("provider probe failed (%s)", kind)
        return _probe_failure(kind, locale)
    latency_ms = int((time.perf_counter() - started) * 1000)
    metadata = getattr(result, "response_metadata", None)
    fallback_used = bool(
        isinstance(metadata, dict) and metadata.get("api_fallback") == "chat_completions"
    )
    entry = _probe_model_entry(row, mid)
    selected_mode = api_mode or str(entry.get("api_mode") or "chat_completions")
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "capability": capability,
        "fallback_used": fallback_used,
        "api_mode_used": "chat_completions" if fallback_used else selected_mode,
    }


def _models_list_url(base_url: str | None) -> str:
    root = _required_base_url(base_url)
    return f"{root}/models"


async def fetch_openai_compatible_models(
    *,
    base_url: str | None,
    api_key: str,
    extra_headers: dict[str, str] | None = None,
    locale: str = "en",
    provider_name: str | None = None,
) -> dict[str, Any]:
    """List models via OpenAI-compatible ``GET {base}/models``."""
    try:
        url = _models_list_url(base_url)
    except ValueError:
        return _probe_failure("invalid_config", locale)
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    extra_headers = ensure_opencode_session_header(provider_name, extra_headers, base_url=base_url)
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with httpx.AsyncClient(timeout=_FETCH_MODELS_TIMEOUT_S) as client:
            response = await client.get(url, headers=headers)
    except Exception as exc:
        return _probe_failure(_failure_kind(exc), locale)

    if response.status_code >= 400:
        error = httpx.HTTPStatusError(
            "provider rejected request", request=response.request, response=response
        )
        return _probe_failure(_failure_kind(error), locale)

    try:
        payload = response.json()
    except Exception:
        return {
            "ok": False,
            "error": "response is not valid JSON (expected OpenAI-compatible /models)",
        }

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": "response is not OpenAI-compatible (expected {data: [{id, …}]})",
        }

    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid.strip():
            continue
        mid = mid.strip()
        if mid in seen:
            continue
        seen.add(mid)
        models.append({"id": mid, "name": mid})
    models.sort(key=lambda m: m["id"])
    return {"ok": True, "models": models}
