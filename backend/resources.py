"""Application resources, resolved independently of the working directory."""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = BACKEND_DIR / "resources"
PROMPTS = RESOURCE_DIR / "prompts"
CORPUS_DIR = RESOURCE_DIR / "corpus"
SCENARIO_DIR = RESOURCE_DIR / "scenarios"
# This is a persistent source identity, retained when files move on disk.
CORPUS_SOURCE_PREFIX = "service/rag_data/corpus"
