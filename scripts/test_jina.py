import requests
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dispatchdesk.config import JINA_MODEL, get_jina_api_key

JINA_API_KEY = get_jina_api_key()

url = "https://api.jina.ai/v1/embeddings"

texts = [
    "Why are my deliveries slipping when it rains?",
    "What should we tell customers when an ETA is unavailable?",
    "When can a rider be reassigned?",
]

payload = {
    "model": JINA_MODEL,
    "input": texts,
}

response = requests.post(
    url,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {JINA_API_KEY}",
    },
    json=payload,
    timeout=60,
)

response.raise_for_status()

embeddings = [
    item["embedding"]
    for item in response.json()["data"]
]

print("Number of embeddings:", len(embeddings))
print("Dimension:", len(embeddings[0]))
