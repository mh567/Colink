from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from octop.infra.agents.providers import onnx_service
from octop.infra.knowledge import embed


async def test_intranet_knowledge_query_uses_configured_local_model(monkeypatch) -> None:
    settings = {"knowledge_embedding_backend": "onnx", "knowledge_embedding_model": "local-model"}
    services = SimpleNamespace(settings_repo=SimpleNamespace(get=settings.get))

    def local_embedding(model, texts, *, local_only):
        assert local_only is True
        assert model == "local-model"
        assert texts == ["Project status"]
        return [[1.0, 0.0]]

    monkeypatch.setattr(embed, "embed_texts", local_embedding)
    assert await embed.embed_intranet_knowledge_query(services, "Project status") == [[1.0, 0.0]]


async def test_intranet_knowledge_query_without_snapshot_does_not_download(monkeypatch) -> None:
    model = "BAAI/bge-small-zh-v1.5"
    settings = {"knowledge_embedding_backend": "onnx", "knowledge_embedding_model": model}
    services = SimpleNamespace(settings_repo=SimpleNamespace(get=settings.get))
    loads = []

    class TextEmbedding:
        def __init__(self, **kwargs):
            loads.append(kwargs)

        def embed(self, texts, *, batch_size):
            assert texts == ["Project status"] and batch_size == 1
            return [[1.0, 0.0]]

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=TextEmbedding))
    monkeypatch.setattr(onnx_service, "_local_model_snapshot", lambda model: None)
    monkeypatch.setattr(onnx_service, "_embedding_instance", None)
    with pytest.raises(RuntimeError, match="local embedding model files are unavailable"):
        await embed.embed_intranet_knowledge_query(services, "Project status")
    assert loads == []

    assert embed.embed_knowledge_texts(services, ["Project status"]) == [[1.0, 0.0]]
    assert await embed.embed_intranet_knowledge_query(services, "Project status") == [[1.0, 0.0]]
    assert len(loads) == 1


def test_remote_embedding_routes_to_provider_embeddings_endpoint(monkeypatch) -> None:
    provider = SimpleNamespace(base_url="https://example.test/v1/", api_key="secret")
    services = SimpleNamespace(
        settings_repo=SimpleNamespace(
            get=lambda key: {
                "knowledge_embedding_backend": "remote",
                "knowledge_embedding_model": "embed-1",
                "knowledge_embedding_provider_id": "7",
            }.get(key)
        ),
        provider_repo=SimpleNamespace(
            get=lambda provider_id: provider if provider_id == 7 else None
        ),
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr(embed.httpx, "Client", lambda **_kwargs: Client())

    assert embed.embed_knowledge_texts(services, ["hello"]) == [[1.0, 2.0]]
    assert captured["url"] == "https://example.test/v1/embeddings"
    assert captured["json"] == {"model": "embed-1", "input": ["hello"]}
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_remote_embedding_merges_provider_extra_headers(monkeypatch) -> None:
    provider = SimpleNamespace(
        base_url="https://example.test/v1/",
        api_key="secret",
        extra_json='{"headers": {"X-Custom": "1"}}',
    )
    services = SimpleNamespace(
        settings_repo=SimpleNamespace(
            get=lambda key: {
                "knowledge_embedding_backend": "remote",
                "knowledge_embedding_model": "embed-1",
                "knowledge_embedding_provider_id": "7",
            }.get(key)
        ),
        provider_repo=SimpleNamespace(
            get=lambda provider_id: provider if provider_id == 7 else None
        ),
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.5]}]}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr(embed.httpx, "Client", lambda **_kwargs: Client())

    assert embed.embed_knowledge_texts(services, ["hello"]) == [[0.5]]
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["headers"]["X-Custom"] == "1"


def test_remote_embedding_batches_large_input(monkeypatch) -> None:
    provider = SimpleNamespace(base_url="https://example.test/v1/", api_key="secret")
    services = SimpleNamespace(
        settings_repo=SimpleNamespace(
            get=lambda key: {
                "knowledge_embedding_backend": "remote",
                "knowledge_embedding_model": "embed-1",
                "knowledge_embedding_provider_id": "7",
            }.get(key)
        ),
        provider_repo=SimpleNamespace(
            get=lambda provider_id: provider if provider_id == 7 else None
        ),
    )
    calls: list[dict] = []

    class Response:
        def __init__(self, batch: list[str]) -> None:
            # One embedding per input, tagged with the input's global position.
            self._data = [
                {"index": index, "embedding": [float(t.split("-")[1])]}
                for index, t in enumerate(batch)
            ]

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": self._data}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            calls.append({"url": url, "json": json})
            return Response(json["input"])

    monkeypatch.setattr(embed.httpx, "Client", lambda **_kwargs: Client())

    texts = [f"chunk-{i}" for i in range(45)]
    result = embed.embed_knowledge_texts(services, texts)

    # AssertiveProviders cap at 20 -> 45 inputs => 3 requests (20/20/5).
    assert len(calls) == 3
    assert calls[0]["json"]["input"] == texts[0:20]
    assert calls[1]["json"]["input"] == texts[20:40]
    assert calls[2]["json"]["input"] == texts[40:45]
    # Merged vectors stay aligned with input order.
    assert len(result) == 45
    assert result == [[float(i)] for i in range(45)]


def test_remote_embedding_restores_order_from_response_indexes(monkeypatch) -> None:
    provider = SimpleNamespace(base_url="https://example.test/v1/", api_key="secret")
    services = SimpleNamespace(
        settings_repo=SimpleNamespace(
            get=lambda key: {
                "knowledge_embedding_backend": "remote",
                "knowledge_embedding_model": "embed-1",
                "knowledge_embedding_provider_id": "7",
            }.get(key)
        ),
        provider_repo=SimpleNamespace(
            get=lambda provider_id: provider if provider_id == 7 else None
        ),
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"index": 1, "embedding": [20.0]},
                    {"index": 0, "embedding": [10.0]},
                ]
            }

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            return Response()

    monkeypatch.setattr(embed.httpx, "Client", lambda **_kwargs: Client())

    assert embed.embed_knowledge_texts(services, ["first", "second"]) == [[10.0], [20.0]]


def test_remote_embedding_rejects_duplicate_response_indexes() -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"index": 0, "embedding": [10.0]},
                    {"index": 0, "embedding": [20.0]},
                ]
            }

    class Client:
        def post(self, url, *, headers, json):
            return Response()

    with pytest.raises(RuntimeError, match="duplicate index"):
        embed._embed_remote_batched(
            Client(), "https://example.test/v1", {}, "embed-1", ["first", "second"]
        )


@pytest.mark.parametrize(
    "vector", [[], [True], ["1"], [float("nan")], [float("inf")], [1e50], [10**400]]
)
def test_remote_embedding_rejects_invalid_vectors(vector) -> None:
    payload = {"data": [{"index": 0, "embedding": vector}]}
    with pytest.raises(RuntimeError, match="embedding"):
        embed.parse_embedding_response(payload, 1)


def test_remote_embedding_rejects_inconsistent_dimensions() -> None:
    payload = {
        "data": [
            {"index": 0, "embedding": [1.0]},
            {"index": 1, "embedding": [1.0, 2.0]},
        ]
    }
    with pytest.raises(RuntimeError, match="dimension"):
        embed.parse_embedding_response(payload, 2)


def test_configured_remote_limits_split_requests_and_reject_long_inputs(monkeypatch) -> None:
    import httpx

    settings = {
        "knowledge_embedding_backend": "remote",
        "knowledge_embedding_model": "embed-1",
        "knowledge_embedding_provider_id": "7",
        "knowledge_embedding_batch_size": "2",
        "knowledge_embedding_max_input_chars": "5",
    }
    services = SimpleNamespace(
        settings_repo=SimpleNamespace(get=settings.get),
        provider_repo=SimpleNamespace(
            get=lambda _: SimpleNamespace(base_url="https://example.test/v1", api_key="secret")
        ),
    )
    batches = []

    def respond(request):
        import json

        data = json.loads(request.content)
        batches.append(data["input"])
        return httpx.Response(
            200,
            json={"data": [{"index": i, "embedding": [1.0]} for i in range(len(data["input"]))]},
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(embed.httpx, "Client", lambda **_: client)
    assert embed.embed_knowledge_texts(services, ["a", "b", "c"]) == [[1.0]] * 3
    assert batches == [["a", "b"], ["c"]]
    with pytest.raises(RuntimeError, match="character limit"):
        embed.embed_knowledge_texts(services, ["too long"])
    assert batches == [["a", "b"], ["c"]]


def test_remote_http_error_does_not_expose_response_or_url(monkeypatch) -> None:
    import httpx

    def reject(request):
        return httpx.Response(401, text="secret-token echoed")

    with (
        httpx.Client(transport=httpx.MockTransport(reject)) as client,
        pytest.raises(embed.KnowledgeEmbeddingError, match="HTTP 401") as error,
    ):
        embed._embed_remote_batched(
            client, "https://secret-user:secret-token@example.test/v1", {}, "embed", ["text"]
        )
    assert "secret" not in str(error.value)
