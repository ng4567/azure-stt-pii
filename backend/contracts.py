"""Common JSON contract emitted by every end-to-end architecture."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

SCHEMA_VERSION = "1.0"
StageStatus = Literal["succeeded", "failed", "skipped"]


@dataclass(frozen=True)
class PiiEntity:
    category: str
    text: str
    turn_id: str
    offset: int
    length: int
    confidence: float | None = None

    @property
    def placeholder(self) -> str:
        normalized = "_".join(self.category.upper().replace("-", " ").split())
        return f"[{normalized or 'PII'}]"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["placeholder"] = self.placeholder
        return result


def stage_result(
    status: StageStatus,
    *,
    provider: str,
    model: str,
    wall_seconds: float,
    metrics: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "provider": provider,
        "model": model,
        "wall_seconds": wall_seconds,
        "metrics": dict(metrics or {}),
        "error": error,
    }


def failed_architecture(
    architecture_id: str, label: str, error: Exception | str
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "architecture_id": architecture_id,
        "label": label,
        "status": "failed",
        "source": None,
        "redacted": None,
        "summary": None,
        "entities": [],
        "stages": {},
        "latency": None,
        "error": str(error),
    }


def architecture_result(
    *,
    architecture_id: str,
    label: str,
    source_entry: Mapping[str, Any],
    redacted_conversation: Mapping[str, Any],
    summary: str,
    entities: list[PiiEntity],
    stages: Mapping[str, Mapping[str, Any]],
    downstream_wall_seconds: float,
) -> dict[str, Any]:
    source_conversation = source_entry.get("conversation")
    if not isinstance(source_conversation, Mapping):
        raise ValueError("The STT stage did not produce a canonical conversation.")
    redacted_items = redacted_conversation.get("conversationItems")
    if not isinstance(redacted_items, list):
        raise ValueError("The redacted conversation has no conversationItems array.")
    redacted_transcript = " ".join(
        str(item.get("text", "")).strip() for item in redacted_items
    ).strip()
    source_metrics = source_entry.get("metrics")
    metrics = source_metrics if isinstance(source_metrics, Mapping) else {}
    stt_seconds = float(
        metrics.get("time_to_full_transcript", metrics.get("wall_seconds", 0.0))
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "architecture_id": architecture_id,
        "label": label,
        "status": "succeeded",
        "source": {
            "transcript": source_entry.get("transcript", ""),
            "conversation": deepcopy(dict(source_conversation)),
        },
        "redacted": {
            "transcript": redacted_transcript,
            "conversation": deepcopy(dict(redacted_conversation)),
        },
        "summary": summary,
        "entities": [entity.to_dict() for entity in entities],
        "stages": {key: dict(value) for key, value in stages.items()},
        "latency": {
            "stt_seconds": stt_seconds,
            "downstream_seconds": downstream_wall_seconds,
            "end_to_end_seconds": stt_seconds + downstream_wall_seconds,
        },
        "error": None,
    }


def summary_only_architecture_result(
    *,
    architecture_id: str,
    label: str,
    source_entry: Mapping[str, Any],
    summary: str,
    stages: Mapping[str, Mapping[str, Any]],
    downstream_wall_seconds: float,
) -> dict[str, Any]:
    source_conversation = source_entry.get("conversation")
    if not isinstance(source_conversation, Mapping):
        raise ValueError("The STT stage did not produce a canonical conversation.")
    source_metrics = source_entry.get("metrics")
    metrics = source_metrics if isinstance(source_metrics, Mapping) else {}
    stt_seconds = float(
        metrics.get("time_to_full_transcript", metrics.get("wall_seconds", 0.0))
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "architecture_id": architecture_id,
        "label": label,
        "status": "succeeded",
        "source": {
            "transcript": source_entry.get("transcript", ""),
            "conversation": deepcopy(dict(source_conversation)),
        },
        "redacted": None,
        "summary": summary,
        "entities": [],
        "stages": {key: dict(value) for key, value in stages.items()},
        "latency": {
            "stt_seconds": stt_seconds,
            "downstream_seconds": downstream_wall_seconds,
            "end_to_end_seconds": stt_seconds + downstream_wall_seconds,
        },
        "error": None,
    }


def stt_stage(source_entry: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(source_entry.get("metrics") or {})
    mode = str(metrics.get("mode", "speech-to-text"))
    if mode == "real-time (incremental)":
        provider = "Azure AI Speech"
        model = "Azure Speech real-time transcription (Speech SDK)"
    elif mode == "real-time (utterance micro-batch)":
        provider = "Azure AI Speech / Voice Live"
        commits = int(metrics.get("utterances_committed", 0) or 0)
        model = (
            f"MAI-Transcribe-1.5 real-time ({commits} VAD commits)"
            if commits
            else "MAI-Transcribe-1.5 real-time"
        )
    elif mode in {"batch (post-call VAD utterances)", "fast-transcription"}:
        provider = "Azure AI Speech / Fast Transcription"
        requests = int(metrics.get("utterance_requests", 0) or 0)
        model = (
            f"MAI-Transcribe-1.5 batch ({requests} VAD requests)"
            if requests
            else "MAI-Transcribe-1.5 batch"
        )
    else:
        provider = "Azure AI Speech"
        model = mode
    return stage_result(
        "succeeded",
        provider=provider,
        model=model,
        wall_seconds=float(metrics.get("wall_seconds", 0.0)),
        metrics=metrics,
    )
