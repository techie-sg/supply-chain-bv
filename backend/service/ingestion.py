"""Chunk, embed, and upsert the RAG corpus: `uv run python -m service.ingestion`."""

import logging
from pathlib import Path

from config import get_settings
from queries.vector_store import insert_chunks
from service.corpus import CorpusService
from service.embedding_service import EmbeddingService
from service.factory import create_chunking_strategy, create_embedding_service

BACKEND_DIR = Path(__file__).resolve().parents[1]
CORPUS_DIR = BACKEND_DIR / "service" / "rag_data" / "corpus"
logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(
        self, corpus_service: CorpusService, embedding_service: EmbeddingService
    ) -> None:
        self.corpus_service = corpus_service
        self.embedding_service = embedding_service

    def run(self) -> int:
        """Chunk, embed, and store the corpus through the injected services."""
        documents, _ = self.corpus_service.load()
        if not documents:
            raise ValueError("Corpus produced no chunks")
        logger.info("Loaded %s chunks", len(documents))

        embeddings = self.embedding_service.embed_documents(
            [document.page_content for document in documents]
        )
        if len(embeddings) != len(documents):
            raise ValueError("Number of embeddings does not match number of chunks")
        logger.info(
            "Generated %s embeddings with dimension %s",
            len(embeddings),
            len(embeddings[0]),
        )
        stored = insert_chunks(documents=documents, embeddings=embeddings)
        logger.info(
            "Upserted %s chunks into app.documents / app.document_chunks", stored
        )
        return stored


def main() -> None:
    """CLI entry point composing the configured services."""
    settings = get_settings()
    service = IngestionService(
        corpus_service=CorpusService(
            corpus_dir=CORPUS_DIR,
            repo_root=BACKEND_DIR,
            strategy=create_chunking_strategy(settings),
        ),
        embedding_service=create_embedding_service(settings),
    )
    logger.info("Starting DispatchDesk corpus ingestion")
    service.run()
    logger.info("Ingestion complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
