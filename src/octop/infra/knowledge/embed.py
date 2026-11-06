"""Route knowledge-base embeddings to local ONNX or a configured provider."""

from __future__ import annotations

import math
from typing import Any

import httpx

from octop.infra.agents.providers.onnx_service import embed_texts
from octop.infra.agents.providers.opencode_session import ensure_opencode_session_header
from octop.infra.agents.providers.probe import provider_headers
from octop.infra.knowledge.params import get_embedding_limits

# Remote OpenAI-compatible embedding APIs cap the number of inputs per request.
_KNOWLEDGE_EMBEDDING_BATCH_LIMIT = 20


class KnowledgeEmbeddingError(RuntimeError):
    """Safe failure description without upstream responses or credentials."""


def parse_embedding_response(
    payload: object, expected_count: int, *, expected_dimension: int | None = None
) -> list[list[float]]:
    """Validate and restore OpenAI embedding input order before indexing."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise KnowledgeEmbeddingError("knowledge embedding response has no data")
    ordered: list[list[float] | None] = [None] * expected_count
    dimension = expected_dimension
    for item in data:
        if not isinstance(item, dict):
            raise KnowledgeEmbeddingError("knowledge embedding response has invalid data item")
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < expected_count:
            raise KnowledgeEmbeddingError("knowledge embedding response has invalid index")
        if ordered[index] is not None:
            raise KnowledgeEmbeddingError("knowledge embedding response has duplicate index")
        vector = item.get("embedding")
        if (
            not isinstance(vector, list)
            or not vector
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or abs(value) > 3.4028234663852886e38  # Both indexes store float32.
                or not math.isfinite(value)
                for value in vector
            )
        ):
            raise KnowledgeEmbeddingError("knowledge embedding response has invalid embedding")
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension:
            raise KnowledgeEmbeddingError("knowledge embedding dimension mismatch")
        ordered[index] = [float(value) for value in vector]
    if any(vector is None for vector in ordered):
        raise KnowledgeEmbeddingError("knowledge embedding response has missing index")
    return [vector for vector in ordered if vector is not None]


def embed_knowledge_texts(services: Any, texts: list[str]) -> list[list[float]]:
    """Embed texts through the selected knowledge embedding backend."""
    settings = services.settings_repo
    backend = (settings.get("knowledge_embedding_backend") or "onnx").strip().lower()
    model = (settings.get("knowledge_embedding_model") or "").strip()
    if backend != "remote":
        return embed_texts(model, texts)

    limits = get_embedding_limits(settings.get)
    max_chars = limits["embedding_max_input_chars"]
    if max_chars and any(len(text) > max_chars for text in texts):
        raise KnowledgeEmbeddingError(
            "knowledge embedding input exceeds configured character limit"
        )
    provider_id = (settings.get("knowledge_embedding_provider_id") or "").strip()
    provider = services.provider_repo.get(int(provider_id)) if provider_id.isdigit() else None
    if provider is None or not provider.base_url or not provider.api_key:
        raise RuntimeError("knowledge remote embedding provider is not ready")
    headers: dict[str, str] = {"Authorization": f"Bearer {provider.api_key}"}
    extra = ensure_opencode_session_header(
        getattr(provider, "name", None),
        provider_headers(provider),
        base_url=provider.base_url,
    )
    if extra:
        headers.update(extra)
    with httpx.Client(timeout=60.0) as client:
        return _embed_remote_batched(
            client,
            provider.base_url.rstrip("/"),
            headers,
            model,
            texts,
            batch_size=limits["embedding_batch_size"],
        )


def _embed_remote_batched(
    client: httpx.Client,
    base: str,
    headers: dict[str, str],
    model: str,
    texts: list[str],
    *,
    batch_size: int = _KNOWLEDGE_EMBEDDING_BATCH_LIMIT,
) -> list[list[float]]:
    """Request a remote embedding API in <=batch-limit slices.

    Slices preserve input order so the merged vectors stay aligned with
    ``texts`` for :meth:`KnowledgeIndex.replace_doc_chunks`.
    """
    if not texts:
        return []
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        try:
            response = client.post(
                f"{base}/embeddings",
                headers=headers,
                json={"model": model, "input": batch},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise KnowledgeEmbeddingError(
                f"knowledge embedding service returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.RequestError:
            raise KnowledgeEmbeddingError("knowledge embedding service is unreachable") from None
        except ValueError:
            raise KnowledgeEmbeddingError(
                "knowledge embedding response is not valid JSON"
            ) from None
        out.extend(
            parse_embedding_response(
                payload, len(batch), expected_dimension=len(out[0]) if out else None
            )
        )
    return out
