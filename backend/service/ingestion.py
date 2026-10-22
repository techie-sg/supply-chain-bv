"""Parse, chunk, embed, and upsert the RAG corpus: `uv run python -m service.ingestion`."""

from time import perf_counter

import structlog

from config import get_settings
from logging_config import configure_logging
from queries.vector_store import insert_chunks
from resources import BACKEND_DIR, CORPUS_DIR
from service.corpus import CorpusService, prepare_documents
from service.document_parser import DoclingPdfParser
from service.embedding_service import EmbeddingService
from service.factory import create_chunking_strategy, create_embedding_service
from service.tracing import observation

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
        with observation(
            "ingestion.run",
            kind="chain",
            metadata={"embedding_model": self.embedding_service.model},
        ) as span:
            with observation("ingestion.corpus") as corpus_span:
                documents, chunk_ids = self.corpus_service.load()
                corpus_span.update(
                    metadata={"chunks": len(documents), "chunk_ids": chunk_ids},
                    output={
                        "chunk_ids": chunk_ids,
                        "chunks": [
                            {
                                "content": document.page_content,
                                "metadata": document.metadata,
                            }
                            for document in documents
                        ],
                    },
                )
                if not documents:
                    raise ValueError("Corpus produced no chunks")
            logger.debug("Corpus ready for embedding", chunks=len(documents))

            embeddings = self.embedding_service.embed_documents(
                [document.page_content for document in documents],
            )
            if len(embeddings) != len(documents):
                raise ValueError("Number of embeddings does not match number of chunks")
            prepared = prepare_documents(documents, embeddings)
            with observation(
                "ingestion.persist",
                input={
                    "documents": [
                        {
                            "file_name": document.file_name,
                            "file_hash": document.file_hash.hex(),
                            "version": document.version,
                            "metadata": document.metadata,
                            "chunks": len(document.chunks),
                            "stored_chunk_ids": [
                                f"{document.metadata['doc_id']}#{index}"
                                for index in range(len(document.chunks))
                            ],
                        }
                        for document in prepared
                    ],
                    "chunk_ids": chunk_ids,
                },
            ) as write_span:
                stored = insert_chunks(prepared)
                write_span.update(
                    output={"stored_chunks": stored},
                    metadata={"stored_chunks": stored, "status": "saved"},
                )
            span.update(
                metadata={
                    "stored_chunks": stored,
                    "documents": len(prepared),
                    "status": "completed",
                },
                output={
                    "stored_chunks": stored,
                    "documents": len(prepared),
                    "chunk_ids": chunk_ids,
                },
            )
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
