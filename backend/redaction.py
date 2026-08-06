"""Deterministic entity application shared by service and LLM architectures."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable, Mapping

from .contracts import PiiEntity

_PLACEHOLDER = re.compile(r"\[([A-Z][A-Z0-9]*(?:[ _-][A-Z0-9]+)*)\]")


def apply_entities(
    conversation: Mapping[str, Any], entities: Iterable[PiiEntity]
) -> dict[str, Any]:
    """Apply non-overlapping entity spans to turns without changing turn metadata."""
    redacted = deepcopy(dict(conversation))
    by_turn: dict[str, list[PiiEntity]] = {}
    for entity in entities:
        by_turn.setdefault(entity.turn_id, []).append(entity)

    for item in redacted.get("conversationItems", []):
        text = str(item.get("text", ""))
        accepted: list[tuple[int, int, PiiEntity]] = []
        for entity in sorted(
            by_turn.get(str(item.get("id")), []),
            key=lambda value: (value.offset, -value.length),
        ):
            start, end = entity.offset, entity.offset + entity.length
            if start < 0 or end > len(text) or start >= end:
                raise ValueError(
                    f"Invalid entity span {start}:{end} for turn {entity.turn_id}."
                )
            if text[start:end] != entity.text:
                raise ValueError(
                    f"Entity text does not match turn {entity.turn_id} at {start}:{end}."
                )
            if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in accepted):
                continue
            accepted.append((start, end, entity))
        for start, end, entity in reversed(accepted):
            text = text[:start] + entity.placeholder + text[end:]
        item["text"] = text
        for derived in ("lexical", "itn", "maskedItn", "audioTimings"):
            item.pop(derived, None)
    return redacted


def sanitize_summary(summary: str, entities: Iterable[PiiEntity]) -> tuple[str, int]:
    """Replace every detected literal entity value in a summary, case-insensitively."""
    sanitized = summary
    replacements = 0
    unique = {
        (entity.text.casefold(), entity.text, entity.placeholder)
        for entity in entities
        if entity.text.strip()
    }
    for _, text, placeholder in sorted(unique, key=lambda value: len(value[1]), reverse=True):
        prefix = r"(?<!\w)" if text[0].isalnum() or text[0] == "_" else ""
        suffix = r"(?!\w)" if text[-1].isalnum() or text[-1] == "_" else ""
        sanitized, count = re.subn(
            prefix + re.escape(text) + suffix,
            placeholder,
            sanitized,
            flags=re.IGNORECASE,
        )
        replacements += count
    sanitized = _PLACEHOLDER.sub(
        lambda match: "[" + "_".join(match.group(1).replace("-", " ").split()) + "]",
        sanitized,
    )
    return sanitized, replacements


def entities_from_redacted_conversation(
    source: Mapping[str, Any], redacted: Mapping[str, Any]
) -> list[PiiEntity]:
    """Derive typed entity spans from `[CATEGORY]` placeholders in LLM output."""
    source_items = {str(item["id"]): item for item in source.get("conversationItems", [])}
    entities: list[PiiEntity] = []
    for item in redacted.get("conversationItems", []):
        turn_id = str(item.get("id", ""))
        if turn_id not in source_items:
            raise ValueError(f"Redacted turn {turn_id} does not exist in the source.")
        source_text = str(source_items[turn_id].get("text", ""))
        redacted_text = str(item.get("text", ""))
        matches = list(_PLACEHOLDER.finditer(redacted_text))
        pattern_parts = [r"\A"]
        cursor = 0
        for match in matches:
            pattern_parts.extend((re.escape(redacted_text[cursor:match.start()]), "(.*?)"))
            cursor = match.end()
        pattern_parts.extend((re.escape(redacted_text[cursor:]), r"\Z"))
        alignment = re.match("".join(pattern_parts), source_text, flags=re.DOTALL)
        if alignment is None:
            raise ValueError(f"Redacted turn {turn_id} does not align with its source.")

        for index, match in enumerate(matches, start=1):
            entity_start, entity_end = alignment.span(index)
            entities.append(
                PiiEntity(
                    category=match.group(1),
                    text=source_text[entity_start:entity_end],
                    turn_id=turn_id,
                    offset=entity_start,
                    length=entity_end - entity_start,
                )
            )
    return entities
