"""Unit tests for knowledge advanced indexing/retrieval settings."""

from __future__ import annotations

import pytest

from octop.infra.knowledge import params


def test_advanced_settings_use_defaults_when_unset() -> None:
    assert params.get_advanced_settings(lambda _key: None) == {
        "chunk_size": params.DEFAULT_CHUNK_SIZE,
        "chunk_overlap": params.DEFAULT_CHUNK_OVERLAP,
        "retrieval_k": params.DEFAULT_RETRIEVAL_K,
        "context_char_budget": params.DEFAULT_CONTEXT_CHAR_BUDGET,
    }


def test_advanced_settings_persist_and_reject_invalid_overlap() -> None:
    values: dict[str, str] = {}

    params.set_advanced_settings(
        values.get,
        values.__setitem__,
        chunk_size=1000,
        chunk_overlap=200,
        retrieval_k=5,
        context_char_budget=4000,
    )
    assert params.get_advanced_settings(values.get) == {
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "retrieval_k": 5,
        "context_char_budget": 4000,
    }

    with pytest.raises(ValueError, match="chunk_overlap"):
        params.set_advanced_settings(
            values.get, values.__setitem__, chunk_size=100, chunk_overlap=100
        )


def test_capability_includes_advanced_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from octop.infra.knowledge import gate

    monkeypatch.setattr(gate, "local_embedding_deps_available", lambda: True)
    monkeypatch.setattr(gate, "is_model_downloaded", lambda _model: False)

    capability = gate.get_capability(lambda _key: None)

    assert capability["chunk_size"] == params.DEFAULT_CHUNK_SIZE
    assert capability["chunk_overlap"] == params.DEFAULT_CHUNK_OVERLAP
    assert capability["retrieval_k"] == params.DEFAULT_RETRIEVAL_K
    assert capability["context_char_budget"] == params.DEFAULT_CONTEXT_CHAR_BUDGET


def test_embedding_limits_persist_and_reject_invalid_values() -> None:
    values: dict[str, str] = {}
    assert params.get_embedding_limits(values.get) == {
        "embedding_batch_size": 20,
        "embedding_max_input_chars": 0,
    }
    params.set_embedding_limits(values.__setitem__, batch_size=3, max_input_chars=1200)
    assert params.get_embedding_limits(values.get) == {
        "embedding_batch_size": 3,
        "embedding_max_input_chars": 1200,
    }
    before = values.copy()
    with pytest.raises(ValueError):
        params.set_embedding_limits(values.__setitem__, batch_size=0, max_input_chars=100)
    assert values == before
    with pytest.raises(ValueError):
        params.set_embedding_limits(values.__setitem__, batch_size=3, max_input_chars=-1)
    assert values == before


def test_invalid_stored_embedding_limits_fail_explicitly() -> None:
    with pytest.raises(ValueError):
        params.get_embedding_limits({"knowledge_embedding_batch_size": "0"}.get)
