"""HTTP API for the STT benchmark suite.

Upload a call recording and/or its reference transcript, then run the three
architectures from the README against it.
"""

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

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
    uploads.ensure_builtins()


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
    return uploads.list_user_uploads()


@app.get("/api/benchmark/default")
def get_default_benchmark() -> dict:
    """Return the last checked-in default comparison without rerunning the models."""
    try:
        return jobs.cached_default()
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(503, str(error)) from error


@app.get("/api/benchmark/default/audio")
def get_default_audio() -> FileResponse:
    """Download the audio used by the cached default comparison."""
    path = uploads.BUILTINS[uploads.DEFAULT_UPLOAD_ID][0]
    if not path.is_file():
        raise HTTPException(404, "The default audio file is unavailable.")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.get("/api/benchmark/default/transcript")
def get_default_transcript() -> FileResponse:
    """Download the reference used to score the cached default comparison."""
    path = uploads.BUILTIN_TRANSCRIPT
    if not path.is_file():
        raise HTTPException(404, "The default transcript file is unavailable.")
    return FileResponse(path, media_type="text/plain", filename=path.name)


@app.post("/api/uploads", status_code=201)
def create_upload(
    audio: UploadFile | None = File(default=None),
    transcript: UploadFile | None = File(default=None),
    channel_0_participant: str = Form(default="REP"),
    channel_1_participant: str = Form(default="CUSTOMER"),
) -> dict:
    """Register an audio file, a reference transcript, or both."""
    if audio is None and transcript is None:
        raise HTTPException(400, "Upload an audio file, a transcript, or both.")

    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        audio_source = _spool(audio, directory) if audio else None
        audio_filename = audio.filename if audio else None
        if audio_source is None and transcript is not None:
            audio_source = uploads.BUILTINS[uploads.DEFAULT_UPLOAD_ID][0]
            audio_filename = audio_source.name
        transcript_source = _spool(transcript, directory) if transcript else None

        try:
            return uploads.create(
                audio_source,
                audio_filename,
                transcript_source,
                transcript.filename if transcript else None,
                channel_map={
                    0: channel_0_participant,
                    1: channel_1_participant,
                },
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
    if uploads.is_builtin(upload_id):
        raise HTTPException(400, "The built-in mock call cannot be deleted.")
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


@app.post("/api/benchmark", status_code=202)
def start_default_benchmark() -> dict:
    """Run the architecture comparison against the default audio and reference."""
    return jobs.submit(uploads.DEFAULT_UPLOAD_ID)


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


@app.get("/api/jobs/{job_id}/conversations/{engine}")
def get_engine_conversation(job_id: str, engine: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    if job["status"] != "succeeded":
        raise HTTPException(409, f"Job is {job['status']}; no conversation yet.")

    entry = job["result"]["engines"].get(engine)
    if entry is None:
        raise HTTPException(404, "No such engine in this run.")
    conversation = entry.get("conversation")
    if conversation is None:
        raise HTTPException(404, "This engine did not produce a conversation.")
    return conversation
