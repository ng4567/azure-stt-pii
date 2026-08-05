"""Benchmark jobs: run `data/stt.py` against an upload, in the background.

A run streams the audio to two of the three architectures at 1x, so it takes about
as long as the call itself. Jobs are therefore started, polled, and read back rather
than answered inline on the request.
"""

import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import uploads
from .config import MAX_CONCURRENT_JOBS

import stt  # noqa: E402  - resolved via BENCHMARK_DIR on sys.path (see config)

RESULT_NAME = "benchmark.json"

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="benchmark"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def list_all() -> list[dict]:
    with _lock:
        jobs = [dict(job) for job in _jobs.values()]
    return sorted(jobs, key=lambda job: job["created_at"], reverse=True)


def _run(job_id: str, audio: Path, transcript: Path | None) -> None:
    _update(job_id, status="running", started_at=_now())

    def on_progress(engine_key: str, state: str) -> None:
        with _lock:
            _jobs[job_id]["engines"][engine_key] = state

    try:
        report = stt.run_benchmark(audio, transcript, on_progress)
    except Exception as error:
        _update(
            job_id,
            status="failed",
            finished_at=_now(),
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(limit=5),
        )
        return

    upload_id = get(job_id)["upload_id"]
    (uploads.upload_dir(upload_id) / RESULT_NAME).write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    _update(job_id, status="succeeded", finished_at=_now(), result=report)


def submit(upload_id: str) -> dict:
    """Queue a benchmark run for an upload that has audio."""
    audio = uploads.audio_path(upload_id)
    if audio is None:
        raise ValueError("This upload has no audio, so there is nothing to transcribe.")

    transcript = uploads.transcript_path(upload_id)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "upload_id": upload_id,
        "status": "queued",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "scored": transcript is not None,
        "engines": {key: "pending" for key in stt.ENGINES},
        "engine_labels": dict(stt.ENGINES),
        "result": None,
        "error": None,
    }
    with _lock:
        _jobs[job_id] = job

    _pool.submit(_run, job_id, audio, transcript)
    return dict(job)
