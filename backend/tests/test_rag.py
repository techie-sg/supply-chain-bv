from types import SimpleNamespace
from typing import Any

import pytest
import requests
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

import gradio_app
from config import require
from service import chunker, embedder, ingestion, llm, rag, vector_store


class FakeTokenizer:
    def encode(self, text: str) -> list[str]:
        return text.split()


class FakeSupabase:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data
        self.calls: list[tuple[str, Any]] = []

    def table(self, name: str) -> "FakeSupabase":
        self.calls.append(("table", name))
        return self

    def upsert(self, rows: list[dict], on_conflict: str) -> "FakeSupabase":
        self.calls.append(("upsert", rows))
        return self

    def rpc(self, name: str, params: dict) -> "FakeSupabase":
        self.calls.append(("rpc", params))
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.data)


def test_require_rejects_missing_values_without_leaking(monkeypatch) -> None:
    monkeypatch.setenv("JINA_API_KEY", "secret-value")
    from config import Settings

    assert require(Settings(_env_file=None).jina_api_key, "JINA_API_KEY") == (  # type: ignore[call-arg]
        "secret-value"
    )
    with pytest.raises(RuntimeError, match="Set SUPABASE_URL") as exc:
        require("  ", "SUPABASE_URL")
    assert "secret" not in str(exc.value)


def test_corpus_chunks_are_stable_and_unique() -> None:
    documents, ids = chunker.load_corpus(ingestion.CORPUS_DIR, ingestion.BACKEND_DIR)
    assert len(documents) == ingestion.EXPECTED_CHUNKS == len(set(ids))
    assert documents[0].metadata["source"].startswith("service/rag_data/corpus/")
    assert all("#" in chunk_id for chunk_id in ids)


def test_chunker_edge_cases(tmp_path, monkeypatch) -> None:
    assert chunker.section_chunks("no headings") == [("Document", "no headings")]
    assert chunker.section_chunks("   ") == []
    assert chunker.parse_metadata("", tmp_path / "x.md", tmp_path)["version"] == (
        "unknown"
    )
    with pytest.raises(FileNotFoundError):
        chunker.load_corpus(tmp_path, tmp_path)
    (tmp_path / "a.md").write_text("# A\n## Same\none\n## Same\ntwo\n")
    with pytest.raises(ValueError, match="Duplicate"):
        chunker.load_corpus(tmp_path, tmp_path)

    monkeypatch.setattr(
        chunker.AutoTokenizer, "from_pretrained", lambda _: FakeTokenizer()
    )
    docs = [Document(page_content="a b c"), Document(page_content="a")]
    assert chunker.validate_chunk_sizes(docs, ["x", "y"], "m", max_tokens=3) == {
        "x": 3,
        "y": 1,
    }
    with pytest.raises(ValueError, match="over 2 tokens"):
        chunker.validate_chunk_sizes(docs, ["x", "y"], "m", max_tokens=2)


def test_embed_texts(monkeypatch) -> None:
    assert embedder.embed_texts([], api_key="k") == []

    def fake_post(url, headers, json, timeout):
        assert headers["Authorization"] == "Bearer k"
        data = [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]
        return SimpleNamespace(
            ok=True, json=lambda: {"data": data[: len(json["input"])]}
        )

    monkeypatch.setattr(embedder.requests, "post", fake_post)
    assert embedder.embed_texts(["a", "b"], api_key="k") == [[1.0], [2.0]]
    monkeypatch.setattr(
        embedder.requests,
        "post",
        lambda *a, **kw: SimpleNamespace(ok=True, json=lambda: {"data": []}),
    )
    with pytest.raises(ValueError, match="Expected 1"):
        embedder.embed_texts(["a"], api_key="k")
    monkeypatch.setattr(
        embedder.requests,
        "post",
        lambda *a, **kw: SimpleNamespace(ok=False, status_code=401, text="denied"),
    )
    with pytest.raises(requests.HTTPError, match="401"):
        embedder.embed_texts(["a"], api_key="k")


def test_vector_store_and_llm_clients(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "key")
    monkeypatch.setenv("GROQ_API_KEY", "groq")
    monkeypatch.setattr(vector_store, "create_client", lambda url, key: (url, key))
    assert vector_store.get_supabase_client() == ("https://example.supabase.co", "key")
    assert llm.get_llm().model_name == llm.DEFAULT_MODEL

    client = FakeSupabase([{"chunk_id": "c"}])
    doc = Document(
        page_content="body", metadata={"doc_id": "d", "title": "t", "section": "s"}
    )
    assert vector_store.insert_chunks(client, [doc], ["c"], [[0.1]]) == 1  # type: ignore[arg-type]
    assert vector_store.retrieve(client, [0.1], 2) == [{"chunk_id": "c"}]  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="same length"):
        vector_store.insert_chunks(client, [doc], [], [])  # type: ignore[arg-type]


def test_answer_question(monkeypatch) -> None:
    monkeypatch.setattr(rag, "embed_texts", lambda texts: [[0.1]])
    monkeypatch.setattr(rag, "get_supabase_client", lambda: None)
    monkeypatch.setattr(rag, "retrieve", lambda **kw: [])
    assert "could not find" in rag.answer_question("q")

    results = [{"chunk_id": "doc#rain", "content": "Slow down in rain."}]
    monkeypatch.setattr(rag, "retrieve", lambda **kw: results)
    seen: list[Any] = []

    def invoke(messages: list[Any]) -> AIMessage:
        seen.extend(messages)
        return AIMessage(content="answer")

    fake_llm = SimpleNamespace(invoke=invoke)
    monkeypatch.setattr(rag, "get_llm", lambda: fake_llm)
    assert rag.answer_question("why?") == "answer"
    assert "[Source: doc#rain]" in seen[1].content
    assert seen[0].content == rag.load_system_prompt()


def test_ingestion_main(monkeypatch) -> None:
    monkeypatch.setattr(ingestion, "validate_chunk_sizes", lambda **kw: {"x": 1})
    monkeypatch.setattr(ingestion, "embed_texts", lambda texts: [[0.0]] * len(texts))
    monkeypatch.setattr(ingestion, "get_supabase_client", lambda: None)
    stored: list[int] = []

    def insert_chunks(**kw: Any) -> int:
        stored.append(len(kw["ids"]))
        return len(kw["ids"])

    monkeypatch.setattr(ingestion, "insert_chunks", insert_chunks)
    ingestion.main()
    assert stored == [ingestion.EXPECTED_CHUNKS]

    monkeypatch.setattr(ingestion, "embed_texts", lambda texts: [[0.0]])
    with pytest.raises(ValueError, match="does not match"):
        ingestion.main()
    monkeypatch.setattr(ingestion, "EXPECTED_CHUNKS", 1)
    with pytest.raises(ValueError, match="Expected 1 chunks"):
        ingestion.main()


def test_gradio_chat(monkeypatch) -> None:
    monkeypatch.setattr(gradio_app, "answer_question", lambda message: "reply")
    assert gradio_app.chat("  ", None) == ([], "")
    history, cleared = gradio_app.chat("hi", [])
    assert cleared == ""
    assert history[-1] == {"role": "assistant", "content": "reply"}
