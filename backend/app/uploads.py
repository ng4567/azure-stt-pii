"""Upload records: an audio file, a reference transcript, or both."""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .audio import prepare_audio
from .config import UPLOAD_DIR

META_NAME = "upload.json"
PREPARED_AUDIO = "audio.wav"
TRANSCRIPT_NAME = "transcript.txt"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upload_dir(upload_id: str) -> Path:
    return UPLOAD_DIR / upload_id


def _write_meta(upload_id: str, meta: dict) -> None:
    (upload_dir(upload_id) / META_NAME).write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def load(upload_id: str) -> dict | None:
    path = upload_dir(upload_id) / META_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_all() -> list[dict]:
    uploads = [
        meta
        for entry in UPLOAD_DIR.iterdir()
        if entry.is_dir() and (meta := load(entry.name)) is not None
    ]
    return sorted(uploads, key=lambda meta: meta["created_at"], reverse=True)


def create(
    audio_source: Path | None,
    audio_filename: str | None,
    transcript_source: Path | None,
    transcript_filename: str | None,
) -> dict:
    """Register an upload, normalizing the audio so it is benchmark-ready."""
    upload_id = uuid.uuid4().hex[:12]
    directory = upload_dir(upload_id)
    directory.mkdir(parents=True, exist_ok=True)

    meta: dict = {
        "id": upload_id,
        "created_at": _now(),
        "audio": None,
        "transcript": None,
    }

    if audio_source is not None:
        info = prepare_audio(audio_source, directory / PREPARED_AUDIO)
        meta["audio"] = {
            "filename": audio_filename,
            "duration_seconds": round(info.duration_seconds, 3),
            "sample_rate": info.sample_rate,
            "transcoded": info.transcoded,
            "size_bytes": info.path.stat().st_size,
        }

    if transcript_source is not None:
        target = directory / TRANSCRIPT_NAME
        shutil.copyfile(transcript_source, target)
        text = target.read_text(encoding="utf-8", errors="replace")
        meta["transcript"] = {
            "filename": transcript_filename,
            "characters": len(text),
            "lines": len(text.splitlines()),
        }

    _write_meta(upload_id, meta)
    return meta


def audio_path(upload_id: str) -> Path | None:
    path = upload_dir(upload_id) / PREPARED_AUDIO
    return path if path.is_file() else None


def transcript_path(upload_id: str) -> Path | None:
    path = upload_dir(upload_id) / TRANSCRIPT_NAME
    return path if path.is_file() else None


def delete(upload_id: str) -> bool:
    directory = upload_dir(upload_id)
    if not directory.is_dir():
        return False
    shutil.rmtree(directory)
    return True
