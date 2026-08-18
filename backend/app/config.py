"""Runtime paths and settings for the benchmark API."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

def env(name: str, default: str = "") -> str:
    """Read a setting, treating an empty value as absent.

    Container orchestrators routinely inject every declared name, supplying "" for
    the ones the operator left unset. Without this, an empty `AZURE_REQUEST_TIMEOUT_SECONDS`
    crashes the process at import time instead of falling back to its default.
    """
    value = os.environ.get(name)
    return value if value not in (None, "") else default


# `stt.py` lives next to the benchmark fixtures in `data/`, and is imported rather
# than shelled out to so the API and the CLI share one benchmark implementation.
REPO_ROOT = Path(env("REPO_ROOT", str(Path(__file__).resolve().parents[2])))
BENCHMARK_DIR = Path(env("BENCHMARK_DIR", str(REPO_ROOT / "data")))

load_dotenv(REPO_ROOT / ".env")

if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

# Uploads are kept outside the repo fixtures so user data never mixes with the
# checked-in mock call.
UPLOAD_DIR = Path(env("UPLOAD_DIR", str(REPO_ROOT / "var" / "uploads")))

# Uploads remain mono/stereo; each STT adapter receives synchronized mono channels.
TARGET_SAMPLE_RATE = int(env("TARGET_SAMPLE_RATE", "16000"))

MAX_CONCURRENT_JOBS = int(env("MAX_CONCURRENT_JOBS", "2"))

AZURE_LANGUAGE_ENDPOINT = env("AZURE_LANGUAGE_ENDPOINT").rstrip("/")
AZURE_LANGUAGE_API_VERSION = env("AZURE_LANGUAGE_API_VERSION", "2024-11-01")
AZURE_FOUNDRY_ENDPOINT = env("AZURE_FOUNDRY_ENDPOINT").rstrip("/")
AZURE_FOUNDRY_DEPLOYMENT = env("AZURE_FOUNDRY_DEPLOYMENT")
AZURE_FOUNDRY_API_VERSION = env("AZURE_FOUNDRY_API_VERSION", "2025-04-01-preview")
SYSTEM_PROMPT_PATH = Path(env("SYSTEM_PROMPT_PATH", str(BENCHMARK_DIR / "system_prompt.txt")))
AZURE_REQUEST_TIMEOUT_SECONDS = float(env("AZURE_REQUEST_TIMEOUT_SECONDS", "300"))
AZURE_FOUNDRY_MAX_ATTEMPTS = int(env("AZURE_FOUNDRY_MAX_ATTEMPTS", "2"))

ALLOWED_ORIGINS = env(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
