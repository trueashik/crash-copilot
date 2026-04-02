"""
agent.py — The Brain of Crash-Copilot
Handles Z.ai (Zhipu AI) GLM API connection with optimized prompting.
Zero external dependencies beyond `requests`.
"""

import os
import time
import requests

# ──────────────────────────────────────────────
# Custom .env loader (no python-dotenv needed)
# ──────────────────────────────────────────────
def load_env():
    """Load key=value pairs from .env file (searches current dir and parent dirs)."""
    # Search upward from CWD to find the .env file
    search_dir = os.getcwd()
    for _ in range(5):  # Search up to 5 levels
        env_path = os.path.join(search_dir, ".env")
        if os.path.isfile(env_path):
            break
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent
    else:
        return

    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()
    except FileNotFoundError:
        pass

load_env()

API_KEY = os.environ.get("GLM_API_KEY", "").strip()
API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
MODEL = os.environ.get("GLM_MODEL", "glm-5.1")

# ──────────────────────────────────────────────
# Compressed system prompt — fewer tokens = less cost
# ──────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are Crash-Copilot, an autonomous debugging agent.\n"
    "Given an ERROR LOG and BROKEN CODE, respond in Markdown with:\n"
    "## Root Cause\nOne sentence.\n"
    "## Fix\nThe corrected code in a fenced code block.\n"
    "## Explanation\nBrief explanation (2-3 sentences max).\n"
    "No filler text. No greetings. Markdown only."
)

# ──────────────────────────────────────────────
# API call with retry, timeout, and token cap
# ──────────────────────────────────────────────
_MAX_RETRIES = 2
_TIMEOUT_SECS = 30
_MAX_TOKENS = 1024


def ask_glm(error_log: str, code_snippet: str) -> str:
    """Send error context to GLM and return the Markdown fix."""
    if not API_KEY or API_KEY == "your_actual_api_key_here":
        return "❌ Error: Set your GLM_API_KEY in the .env file first."

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    error_log = error_log[:3000]
    code_snippet = code_snippet[:3000]

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"ERROR LOG:\n```\n{error_log}\n```\n\nBROKEN CODE:\n```\n{code_snippet}\n```",
            },
        ],
        "temperature": 0.1,
        "max_tokens": _MAX_TOKENS,
    }

    last_err = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(
                API_URL, headers=headers, json=payload, timeout=_TIMEOUT_SECS
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            last_err = "Request timed out"
        except requests.exceptions.ConnectionError:
            last_err = "Could not connect to Z.ai API"
        except requests.exceptions.HTTPError:
            if resp.status_code in (401, 403):
                return f"❌ AUTH ERROR ({resp.status_code}): Check your GLM_API_KEY."
            if resp.status_code == 429:
                last_err = "Rate limited — waiting before retry"
            else:
                return f"❌ API ERROR {resp.status_code}: {resp.text[:200]}"
        except (KeyError, IndexError):
            return "❌ Unexpected API response format."
        except Exception as e:
            return f"❌ CRITICAL ERROR: {e}"

        if attempt < _MAX_RETRIES:
            time.sleep(2 ** attempt)

    return f"❌ Failed after {_MAX_RETRIES} attempts. Last error: {last_err}"
