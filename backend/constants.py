"""Shared defaults for the supported AI providers and chunking strategies."""

JINA_API_URL = "https://api.jina.ai/v1/embeddings"
JINA_MODEL = "jina-embeddings-v5-text-nano"
GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_CHUNK_SIZE = 1600
DEFAULT_CHUNK_OVERLAP = 200

# The demo has one store and one manager; every scenario uses this store.
DEMO_STORE_ID = "DS-BLR-014"
DEMO_MANAGER_ID = "karthik"
