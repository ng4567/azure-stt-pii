"""Canonical speaker-turn contract shared by STT and downstream benchmarks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

MAX_CONVERSATION_ITEM_CHARS = 1000
TICKS_PER_MILLISECOND = 10_000


@dataclass(frozen=True)
class AudioTiming:
    word: str
    offset: int
    duration: int


@dataclass(frozen=True)
class Turn:
    participant_id: str
    channel: int
    offset: int
    duration: int
    text: str
    lexical: str | None = None
    itn: str | None = None
    masked_itn: str | None = None
    audio_timings: tuple[AudioTiming, ...] = ()
    id: str = ""

    @property
    def end(self) -> int:
        return self.offset + self.duration

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.id,
            "participantId": self.participant_id,
            "channel": self.channel,
            "offset": self.offset,
            "duration": self.duration,
            "text": self.text,
        }
        if self.lexical is not None:
            item["lexical"] = self.lexical
        if self.itn is not None:
            item["itn"] = self.itn
        if self.masked_itn is not None:
            item["maskedItn"] = self.masked_itn
        if self.audio_timings:
            item["audioTimings"] = [asdict(timing) for timing in self.audio_timings]
        return item


def validate_channel_map(
    channel_map: Mapping[int | str, str], channel_count: int
) -> dict[int, str]:
    """Return a normalized map with one distinct, non-empty participant per channel."""
    normalized = {int(channel): label.strip() for channel, label in channel_map.items()}
    expected = set(range(channel_count))
    if set(normalized) != expected:
        raise ValueError(
            f"Channel map must define exactly channels {sorted(expected)}."
        )
    if any(not label for label in normalized.values()):
        raise ValueError("Channel participant labels cannot be empty.")
    if len(set(normalized.values())) != len(normalized):
        raise ValueError("Each channel must have a distinct participant label.")
    return normalized


def finalize_turns(turns: Iterable[Turn]) -> list[Turn]:
    """Sort turns chronologically, validate PII limits, and assign stable IDs."""
    ordered = sorted(turns, key=lambda turn: (turn.offset, turn.channel, turn.duration))
    finalized: list[Turn] = []
    for index, turn in enumerate(ordered, start=1):
        text = turn.text.strip()
        if not text:
            continue
        if len(text) > MAX_CONVERSATION_ITEM_CHARS:
            raise ValueError(
                f"Turn on channel {turn.channel} has {len(text)} characters; "
                f"Conversation PII allows {MAX_CONVERSATION_ITEM_CHARS} per item."
            )
        if turn.offset < 0 or turn.duration < 0:
            raise ValueError("Turn offsets and durations cannot be negative.")
        finalized.append(
            Turn(
                id=f"turn-{index:04d}",
                participant_id=turn.participant_id,
                channel=turn.channel,
                offset=turn.offset,
                duration=turn.duration,
                text=text,
                lexical=turn.lexical,
                itn=turn.itn,
                masked_itn=turn.masked_itn,
                audio_timings=turn.audio_timings,
            )
        )
    return finalized


def flatten_turns(turns: Iterable[Turn]) -> str:
    return " ".join(turn.text for turn in turns)


def serialize_conversation(
    turns: Iterable[Turn],
    channel_map: Mapping[int, str],
    *,
    conversation_id: str = "benchmark-call",
    language: str = "en",
    speaker_attributed: bool,
) -> dict[str, Any]:
    finalized = finalize_turns(turns)
    return {
        "id": conversation_id,
        "language": language,
        "modality": "transcript",
        "speakerAttributed": speaker_attributed,
        "channelMap": {str(channel): label for channel, label in channel_map.items()},
        "conversationItems": [turn.to_dict() for turn in finalized],
    }


def conversation_pii_input(conversation: Mapping[str, Any]) -> dict[str, Any]:
    """Strip benchmark metadata and return the shape accepted by Conversation PII."""
    allowed = {
        "id",
        "participantId",
        "text",
        "lexical",
        "itn",
        "maskedItn",
        "audioTimings",
    }
    return {
        "id": conversation["id"],
        "language": conversation["language"],
        "modality": "transcript",
        "conversationItems": [
            {key: value for key, value in item.items() if key in allowed}
            for item in conversation["conversationItems"]
        ],
    }
