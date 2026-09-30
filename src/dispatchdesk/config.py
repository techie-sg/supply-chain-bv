"""Central application configuration and environment loading."""

import os
from pathlib import Path

from dotenv import load_dotenv


def _find_repo_root() -> Path:
    """Find the repository root from this package's location."""

    package_path = Path(__file__).resolve()
    for parent in package_path.parents:
        if (parent / ".git").exists() and (parent / "prompts").is_dir():
            return parent

    # Supports source distributions where repository metadata is absent.
    return package_path.parents[2]


REPO_ROOT = _find_repo_root()
load_dotenv(REPO_ROOT / ".env")

CORPUS_DIR = REPO_ROOT / "data" / "corpus"
PROMPT_PATH = REPO_ROOT / "prompts" / "dispatch_manager_system.md"
JINA_MODEL = "jina-embeddings-v5-text-nano"
EMBEDDING_DIMENSION = 768
EXPECTED_CHUNKS = 37
MAX_CHUNK_TOKENS = 512
CHUNK_TOKENIZER_MODEL = "BAAI/bge-small-en-v1.5"


def get_required_env(name: str) -> str:
    """Return a required environment variable or raise a safe error."""

    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is not set")
    return value


def get_jina_api_key() -> str:
    return get_required_env("JINA_API_KEY")


def get_groq_api_key() -> str:
    return get_required_env("GROQ_API_KEY")


def get_supabase_url() -> str:
    return get_required_env("SUPABASE_URL")


def get_supabase_key() -> str:
    return get_required_env("SUPABASE_KEY")
