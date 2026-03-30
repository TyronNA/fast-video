"""
Check available Gemini 3 Flash models on Vertex AI.
Usage: python check_vertex_models.py
"""
import argparse
import sys
import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account as sa

sys.path.insert(0, ".")
from app.core.config import settings

CANDIDATES = [
    "gemini-3-flash-preview",
    "gemini-3-flash",
    "gemini-3-flash-lite-preview",
    "gemini-3-flash-lite",
]

BASE = (
    "https://{host}/v1/projects/{project}"
    "/locations/{location}/publishers/google/models/{model}:generateContent"
)

PROBE_PAYLOAD = {
    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    "generationConfig": {"maxOutputTokens": 5},
}


def get_token():
    creds = sa.Credentials.from_service_account_file(
        settings.vertex_ai_credentials_file,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(GoogleAuthRequest())
    return creds.token


def check(client, token, model):
    location = settings.gemini_location
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    url = BASE.format(
        host=host,
        location=location,
        project=settings.gcp_project,
        model=model,
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r = client.post(url, json=PROBE_PAYLOAD, headers=headers, timeout=15)
        if r.status_code == 200:
            return "OK  available"
        elif r.status_code == 404:
            detail = r.json().get("error", {}).get("message", r.text[:120])
            return f"404 not found — {detail}"
        elif r.status_code == 403:
            return "403 forbidden (API not enabled or no permission)"
        elif r.status_code == 429:
            return "429 quota exceeded (model exists)"
        else:
            return f"{r.status_code}: {r.text[:80]}"
    except Exception as e:
        return f"error: {e}"


parser = argparse.ArgumentParser()
parser.add_argument("--model", type=str, help="Check only one model")
args = parser.parse_args()

models = [args.model] if args.model else CANDIDATES

print(f"\nProject : {settings.gcp_project}")
print(f"Location: {settings.gemini_location}\n")
print(f"{'Model':<45} Status")
print("-" * 70)

token = get_token()
with httpx.Client() as client:
    for model in models:
        status = check(client, token, model)
        print(f"{model:<45} {status}")

print()
