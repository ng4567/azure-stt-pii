"""Benchmark jobs: run `data/stt.py` against an upload, in the background.

A run streams the audio to two of the three architectures at 1x, so it takes about
as long as the call itself. Jobs are therefore started, polled, and read back rather
than answered inline on the request.
"""

import json
import threading
import traceback
import uuid
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from backend.arch1 import Architecture1Adapter
from backend.arch2 import Architecture2Adapter
from backend.arch3 import Architecture3Adapter
from backend.architecture import ARCHITECTURE_LABELS
from backend.contracts import failed_architecture
from backend.pii_accuracy import load_ground_truth, score_architectures

from . import uploads
from .config import MAX_CONCURRENT_JOBS
from .config import BENCHMARK_DIR

import stt  # noqa: E402  - resolved via BENCHMARK_DIR on sys.path (see config)

RESULT_NAME = "benchmark.json"
CACHED_DEFAULT_RESULT = BENCHMARK_DIR / "mock-call-stereo-stt-benchmark-results.json"

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="benchmark"
)


ARCHITECTURE_FACTORIES = {
    "architecture-1-azure-language": Architecture1Adapter,
    "architecture-2-mai-realtime-deepseek": Architecture2Adapter,
    "architecture-3-mai-batch-deepseek": Architecture3Adapter,
}
ENGINE_ARCHITECTURES = {
    factory.stt_engine_key: architecture_id
    for architecture_id, factory in ARCHITECTURE_FACTORIES.items()
}


def run_architecture(selected_adapter, source, on_progress=None) -> dict:
    """Run one downstream pipeline as soon as its STT result is available."""
    def notify(architecture_id: str, state: str) -> None:
        if on_progress is not None:
            on_progress(architecture_id, state)

    if isinstance(selected_adapter, str):
        architecture_id = selected_adapter
        label = ARCHITECTURE_LABELS[architecture_id]
        try:
            adapter = ARCHITECTURE_FACTORIES[architecture_id]()
        except Exception as error:
            notify(architecture_id, "failed")
            return failed_architecture(architecture_id, label, error)
    else:
        adapter = selected_adapter
        architecture_id = adapter.architecture_id
        label = adapter.label

    notify(architecture_id, "running")
    if source is None:
        result = failed_architecture(
            architecture_id,
            label,
            f"STT report omitted engine {adapter.stt_engine_key}.",
        )
    elif source.get("error"):
        result = failed_architecture(
            architecture_id,
            label,
            f"STT failed: {source['error']}",
        )
    else:
        try:
            result = adapter.run(source)
        except Exception as error:
            result = failed_architecture(architecture_id, label, error)
    notify(architecture_id, "done" if result["status"] == "succeeded" else "failed")
    return result


def run_architectures(stt_report: dict, adapters=None, on_progress=None) -> dict[str, dict]:
    """Run independent downstream pipelines against one shared STT report."""
    selected = list(adapters) if adapters is not None else list(ARCHITECTURE_FACTORIES)
    if not selected:
        return {}

    def run_one(selected_adapter):
        if isinstance(selected_adapter, str):
            engine_key = ARCHITECTURE_FACTORIES[selected_adapter].stt_engine_key
        else:
            engine_key = selected_adapter.stt_engine_key
        return run_architecture(
            selected_adapter,
            stt_report["engines"].get(engine_key),
            on_progress,
        )

    with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix="architecture") as pool:
        results = list(pool.map(run_one, selected))
    return {result["architecture_id"]: result for result in results}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return deepcopy(job) if job else None


def list_all() -> list[dict]:
    with _lock:
        jobs = [deepcopy(job) for job in _jobs.values()]
    return sorted(jobs, key=lambda job: job["created_at"], reverse=True)


def cached_default() -> dict:
    """Load the latest saved default run without starting billable Azure work."""
    persisted_result = uploads.upload_dir(uploads.DEFAULT_UPLOAD_ID) / RESULT_NAME
    result_path = (
        persisted_result if persisted_result.is_file() else CACHED_DEFAULT_RESULT
    )
    if not result_path.is_file():
        raise FileNotFoundError("The cached default benchmark result is unavailable.")
    return json.loads(result_path.read_text(encoding="utf-8"))


def _run(job_id: str, audio: Path, transcript: Path | None) -> None:
    _update(job_id, status="running", started_at=_now())

    def on_progress(engine_key: str, state: str) -> None:
        with _lock:
            _jobs[job_id]["engines"][engine_key] = state

    try:
        upload = uploads.load(get(job_id)["upload_id"])
        channel_map = {
            int(channel): participant
            for channel, participant in upload.get("channel_map", {}).items()
        }
        architecture_futures = {}
        with ThreadPoolExecutor(
            max_workers=len(ARCHITECTURE_FACTORIES),
            thread_name_prefix="architecture",
        ) as architecture_pool:
            def start_downstream(engine_key: str, source: dict) -> None:
                architecture_id = ENGINE_ARCHITECTURES[engine_key]
                architecture_futures[architecture_id] = architecture_pool.submit(
                    run_architecture,
                    architecture_id,
                    source,
                    lambda selected_id, state: _update_architecture_progress(
                        job_id, selected_id, state
                    ),
                )

            report = stt.run_benchmark(
                audio,
                transcript,
                on_progress,
                channel_map=channel_map or None,
                on_engine_result=start_downstream,
            )
            if architecture_futures:
                report["architectures"] = {
                    architecture_id: architecture_futures[architecture_id].result()
                    for architecture_id in ARCHITECTURE_FACTORIES
                    if architecture_id in architecture_futures
                }
            else:
                # Compatibility for tests or alternate benchmark implementations that
                # return a complete report without emitting per-engine callbacks.
                report["architectures"] = run_architectures(
                    report,
                    on_progress=lambda architecture_id, state: _update_architecture_progress(
                        job_id, architecture_id, state
                    ),
                )
        annotations = uploads.pii_ground_truth_path(upload["id"])
        if transcript is not None and annotations is not None:
            reference = stt.reference_text(transcript)
            ground_truth = load_ground_truth(annotations, reference)
            report["pii_accuracy"] = score_architectures(report, reference, ground_truth)
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
        "architectures": {key: "pending" for key in ARCHITECTURE_LABELS},
        "architecture_labels": dict(ARCHITECTURE_LABELS),
        "result": None,
        "error": None,
    }
    with _lock:
        _jobs[job_id] = job

    _pool.submit(_run, job_id, audio, transcript)
    return deepcopy(job)


def _update_architecture_progress(job_id: str, architecture_id: str, state: str) -> None:
    with _lock:
        _jobs[job_id]["architectures"][architecture_id] = state
