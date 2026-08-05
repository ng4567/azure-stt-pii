"""Audio intake: accept whatever the user uploads, hand the benchmark clean PCM."""

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from .config import TARGET_SAMPLE_RATE


@dataclass
class AudioInfo:
    path: Path
    sample_rate: int
    duration_seconds: float
    channels: int
    transcoded: bool


def _probe_wav(path: Path) -> tuple[int, float, int] | None:
    """Return format details for supported 16-bit mono/stereo PCM WAV."""
    try:
        with wave.open(str(path)) as wav:
            channels = wav.getnchannels()
            if channels not in (1, 2) or wav.getsampwidth() != 2:
                return None
            return (
                wav.getframerate(),
                wav.getnframes() / wav.getframerate(),
                channels,
            )
    except (wave.Error, EOFError):
        return None


def _transcode(source: Path, target: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is required to accept this file. Upload 16-bit mono/stereo PCM WAV "
            "instead, or run the backend in its container where ffmpeg is present."
        )

    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-i", str(source),
            "-ar", str(TARGET_SAMPLE_RATE),
            "-acodec", "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-3:]
        raise ValueError(f"Could not decode audio: {' '.join(tail)}")


def prepare_audio(source: Path, target: Path) -> AudioInfo:
    """Normalize `source` into a 16-bit mono/stereo PCM WAV at `target`.

    Audio that is already in that form is copied verbatim so the benchmark scores
    the original samples rather than a resampled copy. Channel identity is preserved.
    """
    probed = _probe_wav(source)
    if probed is not None:
        sample_rate, duration, channels = probed
        if source != target:
            shutil.copyfile(source, target)
        return AudioInfo(target, sample_rate, duration, channels, transcoded=False)

    _transcode(source, target)
    probed = _probe_wav(target)
    if probed is None:
        raise ValueError(
            "Audio must decode to 16-bit PCM with one or two channels; "
            "multichannel recordings beyond stereo are not supported."
        )
    sample_rate, duration, channels = probed
    return AudioInfo(target, sample_rate, duration, channels, transcoded=True)
