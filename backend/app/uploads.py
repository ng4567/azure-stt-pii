"""Upload records: an audio file, a reference transcript, or both."""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .audio import prepare_audio
from .config import BENCHMARK_DIR, UPLOAD_DIR

META_NAME = "upload.json"
PREPARED_AUDIO = "audio.wav"
TRANSCRIPT_NAME = "transcript.txt"

# The turn-ready fixture is the single built-in used by the frontend. The mono file
# remains available to the CLI as a historical baseline, not as a competing UI input.
DEFAULT_UPLOAD_ID = "mock-call-stereo"
BUILTINS = {
    DEFAULT_UPLOAD_ID: (
        BENCHMARK_DIR / "mock-call-stereo.wav",
        "Default mock call",
        {0: "REP", 1: "CUSTOMER"},
    ),
}
BUILTIN_TRANSCRIPT = BENCHMARK_DIR / "mock-call-transcript.txt"
BUILTIN_SCHEMA_VERSION = 2


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
    # Newest first, but the built-in mock call stays pinned to the top.
    uploads.sort(key=lambda meta: meta["created_at"], reverse=True)
    uploads.sort(key=lambda meta: not meta.get("builtin", False))
    return uploads


def list_user_uploads() -> list[dict]:
    """Return user inputs without exposing internal fixtures as benchmark choices."""
    return [meta for meta in list_all() if not meta.get("builtin", False)]


def create(
    audio_source: Path | None,
    audio_filename: str | None,
    transcript_source: Path | None,
    transcript_filename: str | None,
    upload_id: str | None = None,
    builtin: bool = False,
    label: str | None = None,
    channel_map: dict[int, str] | None = None,
) -> dict:
    """Register an upload, normalizing the audio so it is benchmark-ready."""
    upload_id = upload_id or uuid.uuid4().hex[:12]
    directory = upload_dir(upload_id)
    directory.mkdir(parents=True, exist_ok=True)

    meta: dict = {
        "id": upload_id,
        "created_at": _now(),
        "builtin": builtin,
        "label": label,
        "audio": None,
        "transcript": None,
    }

    if audio_source is not None:
        info = prepare_audio(audio_source, directory / PREPARED_AUDIO)
        meta["audio"] = {
            "filename": audio_filename,
            "duration_seconds": round(info.duration_seconds, 3),
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "transcoded": info.transcoded,
            "size_bytes": info.path.stat().st_size,
        }
        default_map = {0: "speaker"} if info.channels == 1 else {0: "REP", 1: "CUSTOMER"}
        selected_map = (
            {
                channel: channel_map[channel]
                for channel in range(info.channels)
                if channel in channel_map
            }
            if channel_map
            else default_map
        )
        if set(selected_map) != set(range(info.channels)):
            raise ValueError(
                f"Channel map must define exactly {info.channels} channel(s)."
            )
        labels = [participant.strip() for participant in selected_map.values()]
        if any(not participant for participant in labels):
            raise ValueError("Channel participant labels cannot be empty.")
        if len(set(labels)) != len(labels):
            raise ValueError("Channel participant labels must be distinct.")
        meta["channel_map"] = {
            str(channel): selected_map[channel].strip()
            for channel in range(info.channels)
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


def is_builtin(upload_id: str) -> bool:
    meta = load(upload_id)
    return bool(meta and meta.get("builtin"))


def ensure_builtins() -> list[dict]:
    """Seed available checked-in fixtures, refreshing them when sources change."""
    seeded = []
    for upload_id, (audio, label, channel_map) in BUILTINS.items():
        if not audio.is_file() or not BUILTIN_TRANSCRIPT.is_file():
            continue
        fingerprint = _builtin_fingerprint(audio)
        existing = load(upload_id)
        if (
            existing is not None
            and existing.get("source_fingerprint") == fingerprint
            and existing.get("schema_version") == BUILTIN_SCHEMA_VERSION
        ):
            seeded.append(existing)
            continue
        if existing is not None:
            shutil.rmtree(upload_dir(upload_id), ignore_errors=True)
        meta = create(
            audio,
            audio.name,
            BUILTIN_TRANSCRIPT,
            BUILTIN_TRANSCRIPT.name,
            upload_id=upload_id,
            builtin=True,
            label=label,
            channel_map=channel_map,
        )
        meta["source_fingerprint"] = fingerprint
        meta["schema_version"] = BUILTIN_SCHEMA_VERSION
        _write_meta(upload_id, meta)
        seeded.append(meta)
    return seeded


def _builtin_fingerprint(audio: Path) -> str:
    return "|".join(
        f"{path.stat().st_size}:{int(path.stat().st_mtime)}"
        for path in (audio, BUILTIN_TRANSCRIPT)
    )
