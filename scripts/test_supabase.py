from pathlib import Path
import sys
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dispatchdesk.config import get_supabase_key, get_supabase_url

url = get_supabase_url()
key = get_supabase_key()

endpoint = f"{url}/rest/v1/"

response = requests.get(
    endpoint,
    headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
    },
    timeout=10,
)

print("HTTP status:", response.status_code)
print("Response:", response.text[:500])
