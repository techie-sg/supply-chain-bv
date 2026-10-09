"""Compose supported providers and chunking strategies from runtime settings."""

from config import Settings, get_settings
from service.chunking import (
    ChunkingStrategy,
    FixedSizeChunkingStrategy,
    MarkdownSectionChunkingStrategy,
)
from service.embedding_service import EmbeddingService
from service.groq_service import GroqService
from service.jina_embedding_service import JinaEmbeddingService
from service.llm_service import LLMService
from service.openrouter_service import OpenRouterService


def create_embedding_service(settings: Settings | None = None) -> EmbeddingService:
    settings = settings or get_settings()
    if settings.embedding_provider == "jina":
        return JinaEmbeddingService(
            model=settings.embedding_model,
            api_key=settings.jina_api_key,
            task_adapters=settings.embedding_task_adapters,
        )
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def create_llm_service(settings: Settings | None = None) -> LLMService:
    settings = settings or get_settings()
    if settings.llm_provider == "openrouter":
        return OpenRouterService(
            model=settings.llm_model,
            api_key=settings.openrouter_api_key,
        )
    if settings.llm_provider == "groq":
        return GroqService(model=settings.llm_model, api_key=settings.groq_api_key)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def create_chunking_strategy(settings: Settings | None = None) -> ChunkingStrategy:
    settings = settings or get_settings()
    if settings.chunking_strategy == "markdown_sections":
        return MarkdownSectionChunkingStrategy()
    if settings.chunking_strategy == "fixed_size":
        return FixedSizeChunkingStrategy(
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
    raise ValueError(f"Unsupported chunking strategy: {settings.chunking_strategy}")
