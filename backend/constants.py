"""Shared application defaults, without importing feature services."""

from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Asia/Kolkata")
NO_GUIDANCE_ANSWER = (
    "I could not find relevant guidance in the DispatchDesk knowledge base."
)

JINA_API_URL = "https://api.jina.ai/v1/embeddings"
JINA_MODEL = "jina-embeddings-v5-text-nano"
GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_CHUNK_SIZE = 1600
DEFAULT_CHUNK_OVERLAP = 200

# The demo has one store and multiple managers; every scenario uses this store.
DEMO_STORE_ID = "DS-BLR-014"
DEMO_MANAGER_ID = "karthik"

# Conversation summary: messages after `summary_covers_to` go to the model raw.
SUMMARY_RECENT_MESSAGES = 6  # always sent raw, never folded after an answer
SUMMARY_MAX_RAW_MESSAGES = 16  # fold when raw messages exceed this count
SUMMARY_MAX_RAW_TOKENS = 3000  # or when raw text exceeds this (characters / 4)
SUMMARY_IDLE_MINUTES = 15  # an idle chat is summarized in full
SUMMARY_JOB_BATCH = 10  # idle chats summarized per run at most

# Dreaming: a daily review of chats that proposes, never applies.
DREAMING_MIN_CHATS = 3  # a settings suggestion needs evidence from this many chats
DREAMING_RECENT_CHATS = 20  # chats read for settings suggestions

# Personalization review usually does nothing; inference always needs approval.
PERSONALIZATION_MIN_CHATS = 3
PERSONALIZATION_EVIDENCE_CHATS = 20
PERSONALIZATION_EVIDENCE_MESSAGES = 60
PERSONALIZATION_MAX_INSTRUCTIONS = 600
