"""Parse, chunk, embed, and upsert the RAG corpus: `uv run python -m service.ingestion`."""

from pathlib import Path
from time import perf_counter

import structlog

from config import get_settings
from logging_config import configure_logging
from queries.vector_store import insert_chunks
from service.corpus import CorpusService
from service.document_parser import DoclingPdfParser
from service.embedding_service import EmbeddingService
from service.factory import create_chunking_strategy, create_embedding_service

BACKEND_DIR = Path(__file__).resolve().parents[1]
CORPUS_DIR = BACKEND_DIR / "service" / "rag_data" / "corpus"
logger = structlog.stdlib.get_logger(__name__)


class IngestionService:
    def __init__(
        self,
        corpus_service: CorpusService,
        embedding_service: EmbeddingService,
    ) -> None:
        self.corpus_service = corpus_service
        self.embedding_service = embedding_service

    def run(self) -> int:
        """Chunk, embed, and store the corpus through the injected services."""
        started = perf_counter()
        documents, _ = self.corpus_service.load()
        if not documents:
            raise ValueError("Corpus produced no chunks")
        logger.debug("Corpus ready for embedding", chunks=len(documents))

        embeddings = self.embedding_service.embed_documents(
            [document.page_content for document in documents],
        )
        if len(embeddings) != len(documents):
            raise ValueError("Number of embeddings does not match number of chunks")
        stored = insert_chunks(documents=documents, embeddings=embeddings)
        logger.info(
            "Corpus ingestion completed",
            stored_chunks=stored,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return stored


def main() -> None:
    """CLI entry point composing the configured services."""
    configure_logging()
    settings = get_settings()
    service = IngestionService(
        corpus_service=CorpusService(
            corpus_dir=CORPUS_DIR,
            repo_root=BACKEND_DIR,
            strategy=create_chunking_strategy(settings),
            parser=DoclingPdfParser(),
        ),
        embedding_service=create_embedding_service(settings),
    )
    logger.info("Starting DispatchDesk corpus ingestion")
    try:
        service.run()
    except Exception:
        logger.exception("Corpus ingestion failed")
        raise


if __name__ == "__main__":
    main()
