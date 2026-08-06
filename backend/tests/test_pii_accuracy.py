import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from backend.contracts import PiiEntity
from backend.pii_accuracy import (
    GroundTruthEntity,
    canonical_category,
    load_ground_truth,
    project_ground_truth,
    score_architectures,
    score_entities,
)


def conversation(*texts: str) -> dict:
    return {
        "conversationItems": [
            {"id": f"turn-{index}", "text": text}
            for index, text in enumerate(texts, 1)
        ]
    }


class GroundTruthTests(unittest.TestCase):
    def test_load_validates_hash_and_spans(self) -> None:
        reference = "Call Maya at 202-555-0148."
        payload = {
            "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
            "entities": [{"category": "PersonName", "text": "Maya", "offset": 5, "length": 4}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truth.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            entities = load_ground_truth(path, reference)
            self.assertEqual(entities[0].category, "PERSON")
            with self.assertRaisesRegex(ValueError, "does not match"):
                load_ground_truth(path, reference + " changed")

    def test_checked_in_annotations_match_reference(self) -> None:
        from data import stt

        reference = stt.reference_text()
        entities = load_ground_truth(Path("data/mock-call-pii-ground-truth.json"), reference)
        self.assertEqual(len(entities), 26)

    def test_category_aliases_are_normalized(self) -> None:
        self.assertEqual(canonical_category("USSocialSecurityNumber"), "SSN")
        self.assertEqual(canonical_category("expiration-date"), "EXPIRATION_DATE")
        self.assertEqual(canonical_category("AccountNumber"), "ACCOUNT_NUMBER")


class ProjectionAndScoringTests(unittest.TestCase):
    def test_projection_tracks_turn_offsets_and_unaligned_entities(self) -> None:
        reference = "Please call Maya Jones at 202-555-0148 tomorrow"
        truths = [
            GroundTruthEntity("PERSON", "Maya Jones", 12, 10),
            GroundTruthEntity("PHONE_NUMBER", "202-555-0148", 26, 12),
        ]
        projected, unaligned = project_ground_truth(
            reference,
            conversation("Please call Maya Jones", "at number unavailable tomorrow"),
            truths,
        )
        self.assertEqual(unaligned, 1)
        self.assertEqual(projected[0].turn_id, "turn-1")
        self.assertEqual(projected[0].offset, 12)
        self.assertEqual(projected[0].text, "Maya Jones")

    def test_entity_metrics_use_exact_spans_and_category_aliases(self) -> None:
        expected = [
            PiiEntity("PERSON", "Maya", "turn-1", 5, 4),
            PiiEntity("PHONE_NUMBER", "202-555-0148", "turn-1", 13, 12),
        ]
        predicted = [
            PiiEntity("PersonName", "Maya", "turn-1", 5, 4),
            PiiEntity("EMAIL", "wrong", "turn-1", 30, 5),
        ]
        score = score_entities(expected, predicted)
        self.assertEqual((score["true_positives"], score["false_positives"], score["false_negatives"]), (1, 1, 1))
        self.assertEqual(score["precision"], 0.5)
        self.assertEqual(score["recall"], 0.5)
        self.assertEqual(score["f1"], 0.5)
        self.assertEqual(score["category_accuracy"], 1.0)
        self.assertEqual(score["pii_leakage_rate"], 0.5)

    def test_duplicate_prediction_counts_as_false_positive(self) -> None:
        entity = PiiEntity("PERSON", "Maya", "turn-1", 5, 4)
        score = score_entities([entity], [entity, entity])
        self.assertEqual(score["true_positives"], 1)
        self.assertEqual(score["false_positives"], 1)
        self.assertEqual(score["precision"], 0.5)

    def test_architecture_scoring_includes_full_output_and_skips_failed_results(self) -> None:
        reference = "Call Maya now"
        truth = [GroundTruthEntity("PERSON", "Maya", 5, 4)]
        report = {
            "architectures": {
                "ok": {
                    "status": "succeeded",
                    "source": {"conversation": conversation("Call Maya now")},
                    "redacted": {"conversation": conversation("Call [PERSON] now")},
                    "entities": [{"category": "PERSON", "text": "Maya", "turn_id": "turn-1", "offset": 5, "length": 4, "confidence": None}],
                },
                "failed": {"status": "failed", "source": None, "entities": []},
            }
        }
        scores = score_architectures(report, reference, truth)
        self.assertEqual(set(scores), {"ok"})
        self.assertEqual(scores["ok"]["alignment_rate"], 1.0)
        self.assertEqual(scores["ok"]["f1"], 1.0)

    def test_architecture_scoring_excludes_summary_only_results(self) -> None:
        reference = "Call Maya now"
        truth = [GroundTruthEntity("PERSON", "Maya", 5, 4)]
        report = {
            "architectures": {
                "summary-only": {
                    "status": "succeeded",
                    "source": {"conversation": conversation("Call Maya now")},
                    "redacted": None,
                    "summary": "The caller requested contact with [PERSON].",
                    "entities": [],
                },
            }
        }
        self.assertEqual(score_architectures(report, reference, truth), {})


if __name__ == "__main__":
    unittest.main()
