from types import SimpleNamespace

import pytest
import requests
from pydantic import SecretStr

from service import jina_embedding_service as jina
from service.jina_embedding_service import JinaEmbeddingService


@pytest.mark.parametrize("is_query", [False, True])
def test_jina_preserves_input_order_and_uses_correct_task(
    monkeypatch, caplog, is_query
) -> None:
    service = JinaEmbeddingService(api_key=SecretStr("test-key"))
    texts = ["question"] if is_query else ["one", "two"]

    def post(url, headers, json, timeout):
        assert url == jina.JINA_API_URL and timeout == 60
        assert headers["Authorization"] == "Bearer test-key"
        assert json["model"] == jina.JINA_MODEL and json["normalized"] is True
        assert json["input"] == texts
        assert json["task"] == ("retrieval.query" if is_query else "retrieval.passage")
        data = [
            {"index": index, "embedding": [float(index + 1)]}
            for index in reversed(range(len(texts)))
        ]
        return SimpleNamespace(
            ok=True, json=lambda: {"data": data, "usage": {"total_tokens": 10}}
        )

    monkeypatch.setattr(jina.requests, "post", post)
    with caplog.at_level("INFO", logger=jina.__name__):
        if is_query:
            assert service.embed_query(texts[0]) == [1.0]
        else:
            assert service.embed_documents(texts) == [[1.0], [2.0]]
    assert "tokens=10" in caplog.text and "test-key" not in caplog.text


def test_empty_documents_skip_settings_and_network(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("empty input must not load settings or call the API")

    monkeypatch.setattr(jina, "get_settings", forbidden)
    monkeypatch.setattr(jina.requests, "post", forbidden)
    assert JinaEmbeddingService().embed_documents([]) == []


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "Expected 1"),
        ([{"index": 1, "embedding": [0.1]}], "invalid embedding indices"),
    ],
)
def test_jina_rejects_invalid_embedding_batches(monkeypatch, data, message) -> None:
    monkeypatch.setattr(
        jina.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(ok=True, json=lambda: {"data": data}),
    )
    with pytest.raises(ValueError, match=message):
        JinaEmbeddingService(api_key=SecretStr("test-key")).embed_documents(["one"])


def test_jina_http_failure_does_not_expose_response_body(monkeypatch) -> None:
    monkeypatch.setattr(
        jina.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False, status_code=401, text="private provider response"
        ),
    )
    with pytest.raises(requests.HTTPError, match="401") as error:
        JinaEmbeddingService(api_key=SecretStr("test-key")).embed_query("q")
    assert "private" not in str(error.value)


def test_jina_loads_runtime_key_only_when_needed(monkeypatch) -> None:
    monkeypatch.setattr(
        jina, "get_settings", lambda: SimpleNamespace(jina_api_key=SecretStr("runtime"))
    )

    def post(url, headers, **kwargs):
        assert headers["Authorization"] == "Bearer runtime"
        return SimpleNamespace(
            ok=True, json=lambda: {"data": [{"index": 0, "embedding": [0.1]}]}
        )

    monkeypatch.setattr(jina.requests, "post", post)
    assert JinaEmbeddingService().embed_query("q") == [0.1]
