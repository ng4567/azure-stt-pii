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
        "error": None,
    }


def stt_stage(source_entry: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(source_entry.get("metrics") or {})
    return stage_result(
        "succeeded",
        provider="Azure AI Speech",
        model=str(metrics.get("mode", "speech-to-text")),
        wall_seconds=float(metrics.get("wall_seconds", 0.0)),
        metrics=metrics,
    )
