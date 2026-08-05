"""Runtime paths and settings for the benchmark API."""

import os
import sys
from pathlib import Path

# `stt.py` lives next to the benchmark fixtures in `data/`, and is imported rather
# than shelled out to so the API and the CLI share one benchmark implementation.
REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[2]))
BENCHMARK_DIR = Path(os.environ.get("BENCHMARK_DIR", REPO_ROOT / "data"))

if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

# Uploads are kept outside the repo fixtures so user data never mixes with the
# checked-in mock call.
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", REPO_ROOT / "var" / "uploads"))

# Uploads remain mono/stereo; each STT adapter receives synchronized mono channels.
TARGET_SAMPLE_RATE = int(os.environ.get("TARGET_SAMPLE_RATE", "16000"))

MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
