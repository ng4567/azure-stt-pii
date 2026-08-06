"""Architecture-independent PII ground truth projection and entity scoring."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.contracts import PiiEntity

_WORD = re.compile(r"\w+(?:['-]\w+)*", re.UNICODE)
_CATEGORY_ALIASES = {
    "ACCOUNTNUMBER": "ACCOUNT_NUMBER",
    "ACCOUNT_NUMBER": "ACCOUNT_NUMBER",
    "ADDRESS": "ADDRESS",
    "CREDITCARDNUMBER": "CREDIT_CARD",
    "CREDIT_CARD": "CREDIT_CARD",
    "CVV": "SECURITY_CODE",
    "DATEOFBIRTH": "DATE_OF_BIRTH",
    "DATE_OF_BIRTH": "DATE_OF_BIRTH",
    "EMAIL": "EMAIL",
    "EMAILADDRESS": "EMAIL",
    "EXPIRATIONDATE": "EXPIRATION_DATE",
    "EXPIRATION_DATE": "EXPIRATION_DATE",
    "PERSON": "PERSON",
    "PERSONNAME": "PERSON",
    "PHONE": "PHONE_NUMBER",
    "PHONENUMBER": "PHONE_NUMBER",
    "PHONE_NUMBER": "PHONE_NUMBER",
    "SSN": "SSN",
    "USSOCIALSECURITYNUMBER": "SSN",
    "US_SOCIAL_SECURITY_NUMBER": "SSN",
    "SECURITYCODE": "SECURITY_CODE",
    "SECURITY_CODE": "SECURITY_CODE",
}


@dataclass(frozen=True)
class GroundTruthEntity:
    category: str
    text: str
    offset: int
    length: int


def canonical_category(category: str) -> str:
    key = "_".join(category.upper().replace("-", " ").split())
    return _CATEGORY_ALIASES.get(key, _CATEGORY_ALIASES.get(key.replace("_", ""), key))


def load_ground_truth(path: Path, reference_text: str) -> list[GroundTruthEntity]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("PII ground truth must be a JSON object.")
    if payload.get("reference_sha256"):
        import hashlib

        digest = hashlib.sha256(reference_text.encode("utf-8")).hexdigest()
        if payload["reference_sha256"] != digest:
            raise ValueError("PII ground truth does not match the reference transcript.")
    values = payload.get("entities")
    if not isinstance(values, list):
        raise ValueError("PII ground truth must contain an entities array.")
    entities = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Each PII ground-truth entity must be an object.")
        try:
            entity = GroundTruthEntity(
                category=canonical_category(str(value["category"])),
                text=str(value["text"]),
                offset=int(value["offset"]),
                length=int(value["length"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Each PII entity requires category, text, offset, and length.") from error
        if entity.offset < 0 or entity.length <= 0:
            raise ValueError("PII ground-truth offsets must be non-negative and lengths positive.")
        if reference_text[entity.offset : entity.offset + entity.length] != entity.text:
            raise ValueError(f"PII ground-truth span does not match: {entity.text!r}.")
        entities.append(entity)
    return entities


def _words(text: str) -> list[tuple[str, int, int]]:
    return [(match.group().casefold(), match.start(), match.end()) for match in _WORD.finditer(text)]


def project_ground_truth(
    reference_text: str,
    source_conversation: Mapping[str, Any],
    ground_truth: Iterable[GroundTruthEntity],
) -> tuple[list[PiiEntity], int]:
    """Project reference spans into an STT transcript using monotonic word alignment."""
    items = list(source_conversation.get("conversationItems", []))
    source_text = " ".join(str(item.get("text", "")) for item in items)
    reference_words = _words(reference_text)
    source_words = _words(source_text)
    matcher = SequenceMatcher(
        None,
        [word for word, _, _ in reference_words],
        [word for word, _, _ in source_words],
        autojunk=False,
    )
    word_map: dict[int, int] = {}
    for reference_start, source_start, size in matcher.get_matching_blocks():
        for index in range(size):
            word_map[reference_start + index] = source_start + index

    boundaries: list[tuple[int, int, str]] = []
    cursor = 0
    for item in items:
        text = str(item.get("text", ""))
        boundaries.append((cursor, cursor + len(text), str(item.get("id", ""))))
        cursor += len(text) + 1

    projected: list[PiiEntity] = []
    unaligned = 0
    for truth in ground_truth:
        reference_indexes = [
            index
            for index, (_, start, end) in enumerate(reference_words)
            if start < truth.offset + truth.length and end > truth.offset
        ]
        source_indexes = [word_map[index] for index in reference_indexes if index in word_map]
        if not reference_indexes or len(source_indexes) != len(reference_indexes):
            unaligned += 1
            continue
        if source_indexes != list(range(source_indexes[0], source_indexes[-1] + 1)):
            unaligned += 1
            continue
        start = source_words[source_indexes[0]][1]
        end = source_words[source_indexes[-1]][2]
        boundary = next(
            ((turn_start, turn_end, turn_id) for turn_start, turn_end, turn_id in boundaries
             if turn_start <= start and end <= turn_end),
            None,
        )
        if boundary is None:
            unaligned += 1
            continue
        turn_start, _, turn_id = boundary
        projected.append(
            PiiEntity(
                category=truth.category,
                text=source_text[start:end],
                turn_id=turn_id,
                offset=start - turn_start,
                length=end - start,
            )
        )
    return projected, unaligned


def score_entities(expected: Iterable[PiiEntity], predicted: Iterable[PiiEntity]) -> dict[str, Any]:
    """Score exact spans separately from category correctness and leakage."""
    expected_entities = list(expected)
    predicted_entities = list(predicted)
    expected_spans = Counter(
        (entity.turn_id, entity.offset, entity.length) for entity in expected_entities
    )
    predicted_spans = Counter(
        (entity.turn_id, entity.offset, entity.length) for entity in predicted_entities
    )
    true_positives = sum((expected_spans & predicted_spans).values())
    false_positives = len(predicted_entities) - true_positives
    false_negatives = len(expected_entities) - true_positives
    precision = true_positives / len(predicted_entities) if predicted_entities else 0.0
    recall = true_positives / len(expected_entities) if expected_entities else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    expected_categories = Counter(
        (entity.turn_id, entity.offset, entity.length, canonical_category(entity.category))
        for entity in expected_entities
    )
    predicted_categories = Counter(
        (entity.turn_id, entity.offset, entity.length, canonical_category(entity.category))
        for entity in predicted_entities
    )
    category_matches = sum((expected_categories & predicted_categories).values())
    return {
        "expected_entities": len(expected_entities),
        "predicted_entities": len(predicted_entities),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "category_accuracy": category_matches / true_positives if true_positives else None,
        "pii_leakage_rate": false_negatives / len(expected_entities) if expected_entities else 0.0,
    }


def score_architectures(
    report: Mapping[str, Any],
    reference_text: str,
    ground_truth: Iterable[GroundTruthEntity],
) -> dict[str, dict[str, Any]]:
    scores = {}
    truth = list(ground_truth)
    for architecture_id, result in report.get("architectures", {}).items():
        if not isinstance(result, Mapping):
            continue
        source = result.get("source")
        redacted = result.get("redacted")
        entities = result.get("entities")
        if (
            result.get("status") != "succeeded"
            or not isinstance(source, Mapping)
            or not isinstance(redacted, Mapping)
            or not isinstance(entities, list)
        ):
            continue
        expected, unaligned = project_ground_truth(reference_text, source["conversation"], truth)
        predicted = [
            PiiEntity(
                category=str(entity["category"]),
                text=str(entity["text"]),
                turn_id=str(entity["turn_id"]),
                offset=int(entity["offset"]),
                length=int(entity["length"]),
                confidence=entity.get("confidence"),
            )
            for entity in entities
        ]
        scores[architecture_id] = {
            **score_entities(expected, predicted),
            "ground_truth_entities": len(truth),
            "unaligned_ground_truth_entities": unaligned,
            "alignment_rate": (len(truth) - unaligned) / len(truth) if truth else 1.0,
        }
    return scores
