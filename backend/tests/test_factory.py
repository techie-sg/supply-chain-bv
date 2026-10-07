import pytest

from config import Settings
from service import factory
from service.chunking import FixedSizeChunkingStrategy, MarkdownSectionChunkingStrategy
from service.groq_service import GroqService
from service.jina_embedding_service import JinaEmbeddingService


def test_factories_use_settings_without_calling_external_providers(monkeypatch) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        embedding_model="embedding",
        embedding_task_adapters=True,
        llm_model="chat",
    )
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    embedding = factory.create_embedding_service()
    llm = factory.create_llm_service()
    assert (
        isinstance(embedding, JinaEmbeddingService) and embedding.model == "embedding"
    )
    assert embedding.task_adapters
    assert isinstance(llm, GroqService) and llm.model == "chat"
    assert isinstance(
        factory.create_chunking_strategy(),
        MarkdownSectionChunkingStrategy,
    )


def test_fixed_size_settings_select_the_alternative_strategy() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        chunking_strategy="fixed_size",
        chunk_size=100,
        chunk_overlap=20,
    )
    strategy = factory.create_chunking_strategy(settings)
    assert isinstance(strategy, FixedSizeChunkingStrategy)
    assert strategy.chunk_size == 100 and strategy.overlap == 20


@pytest.mark.parametrize(
    "setting, create",
    [
        ("embedding_provider", factory.create_embedding_service),
        ("llm_provider", factory.create_llm_service),
        ("chunking_strategy", factory.create_chunking_strategy),
    ],
)
def test_unknown_provider_or_strategy_is_rejected(setting, create) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    setattr(settings, setting, "unknown")
    with pytest.raises(ValueError, match="Unsupported"):
        create(settings)
