"""HTTP API for the STT benchmark suite.

Upload a call recording and/or its reference transcript, then run the three
architectures from the README against it.
"""

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from . import jobs, uploads
from .config import ALLOWED_ORIGINS, ensure_dirs

app = FastAPI(title="Azure STT + PII benchmark", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHUNK = 1024 * 1024


@app.on_event("startup")
def _startup() -> None:
    ensure_dirs()


def _spool(upload: UploadFile, directory: Path) -> Path:
    """Stream an upload to disk - recordings are far too large to hold in memory."""
    target = directory / (Path(upload.filename or "upload").name or "upload")
    with target.open("wb") as handle:
        while chunk := upload.file.read(CHUNK):
            handle.write(chunk)
    return target


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/uploads")
def list_uploads() -> list[dict]:
    return uploads.list_all()


@app.post("/api/uploads", status_code=201)
def create_upload(
    audio: UploadFile | None = File(default=None),
    transcript: UploadFile | None = File(default=None),
) -> dict:
    """Register an audio file, a reference transcript, or both."""
    if audio is None and transcript is None:
        raise HTTPException(400, "Upload an audio file, a transcript, or both.")

    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        audio_source = _spool(audio, directory) if audio else None
        transcript_source = _spool(transcript, directory) if transcript else None

        try:
            return uploads.create(
                audio_source,
                audio.filename if audio else None,
                transcript_source,
                transcript.filename if transcript else None,
            )
        except (ValueError, RuntimeError) as error:
            raise HTTPException(400, str(error)) from error


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str) -> dict:
    meta = uploads.load(upload_id)
    if meta is None:
        raise HTTPException(404, "No such upload.")
    return meta


@app.delete("/api/uploads/{upload_id}", status_code=204)
def delete_upload(upload_id: str) -> None:
    if not uploads.delete(upload_id):
        raise HTTPException(404, "No such upload.")


@app.get("/api/uploads/{upload_id}/transcript", response_class=PlainTextResponse)
def get_reference_transcript(upload_id: str) -> str:
    path = uploads.transcript_path(upload_id)
    if path is None:
        raise HTTPException(404, "This upload has no reference transcript.")
    return path.read_text(encoding="utf-8", errors="replace")


@app.post("/api/uploads/{upload_id}/benchmark", status_code=202)
def start_benchmark(upload_id: str) -> dict:
    if uploads.load(upload_id) is None:
        raise HTTPException(404, "No such upload.")
    try:
        return jobs.submit(upload_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return jobs.list_all()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job


@app.get("/api/jobs/{job_id}/transcripts/{engine}", response_class=PlainTextResponse)
def get_engine_transcript(job_id: str, engine: str) -> str:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    if job["status"] != "succeeded":
        raise HTTPException(409, f"Job is {job['status']}; no transcript yet.")

    entry = job["result"]["engines"].get(engine)
    if entry is None:
        raise HTTPException(404, "No such engine in this run.")
    return entry.get("transcript", "")
