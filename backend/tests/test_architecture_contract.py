import unittest

from backend.contracts import PiiEntity, architecture_result, stage_result
from backend.redaction import (
    apply_entities,
    entities_from_redacted_conversation,
    sanitize_summary,
)


SOURCE_CONVERSATION = {
    "id": "call",
    "language": "en",
    "modality": "transcript",
    "speakerAttributed": True,
    "channelMap": {"0": "REP", "1": "CUSTOMER"},
    "conversationItems": [
        {
            "id": "turn-0001",
            "participantId": "CUSTOMER",
            "channel": 1,
            "offset": 10,
            "duration": 20,
            "text": "Call Eleanor at 202-555-0148.",
            "lexical": "call eleanor at 202 555 0148",
        }
    ],
}


class RedactionTests(unittest.TestCase):
    def test_entities_preserve_turn_metadata_and_remove_derived_text(self) -> None:
        entities = [
            PiiEntity("Person", "Eleanor", "turn-0001", 5, 7),
            PiiEntity("Phone Number", "202-555-0148", "turn-0001", 16, 12),
        ]

        redacted = apply_entities(SOURCE_CONVERSATION, entities)
        turn = redacted["conversationItems"][0]

        self.assertEqual(
            turn["text"], "Call [PERSON] at [PHONE_NUMBER]."
        )
        self.assertEqual(turn["offset"], 10)
        self.assertEqual(turn["participantId"], "CUSTOMER")
        self.assertNotIn("lexical", turn)
        self.assertEqual(
            SOURCE_CONVERSATION["conversationItems"][0]["text"],
            "Call Eleanor at 202-555-0148.",
        )

    def test_summary_is_sanitized_case_insensitively(self) -> None:
        entities = [
            PiiEntity("Person", "Eleanor", "turn-0001", 5, 7),
            PiiEntity("Phone Number", "202-555-0148", "turn-0001", 16, 12),
        ]
        summary, replacements = sanitize_summary(
            "ELEANOR requested service for Eleanor at [PHONE NUMBER].", entities
        )
        self.assertEqual(
            summary, "[PERSON] requested service for [PERSON] at [PHONE_NUMBER]."
        )
        self.assertEqual(replacements, 2)

    def test_summary_does_not_replace_entity_inside_another_word(self) -> None:
        summary, replacements = sanitize_summary(
            "Use the phone for option one.",
            [PiiEntity("Name", "One", "turn-0001", 0, 3)],
        )

        self.assertEqual(summary, "Use the phone for option [NAME].")
        self.assertEqual(replacements, 1)

    def test_invalid_entity_span_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            apply_entities(
                SOURCE_CONVERSATION,
                [PiiEntity("Person", "Wrong", "turn-0001", 5, 7)],
            )

    def test_spaced_model_placeholders_are_normalized(self) -> None:
        source = {
            **SOURCE_CONVERSATION,
            "conversationItems": [
                {
                    **SOURCE_CONVERSATION["conversationItems"][0],
                    "text": "Call 202-555-0148 or email eleanor@example.com.",
                }
            ],
        }
        model_redacted = {
            **source,
            "conversationItems": [
                {
                    **source["conversationItems"][0],
                    "text": "Call [PHONE NUMBER] or email [EMAIL].",
                }
            ],
        }

        entities = entities_from_redacted_conversation(source, model_redacted)
        canonical = apply_entities(source, entities)

        self.assertEqual(
            [entity.category for entity in entities], ["PHONE NUMBER", "EMAIL"]
        )
        self.assertEqual(
            canonical["conversationItems"][0]["text"],
            "Call [PHONE_NUMBER] or email [EMAIL].",
        )


class ContractTests(unittest.TestCase):
    def test_common_result_shape(self) -> None:
        entity = PiiEntity("Person", "Eleanor", "turn-0001", 5, 7)
        result = architecture_result(
            architecture_id="architecture-test",
            label="Test",
            source_entry={
                "transcript": "Call Eleanor at 202-555-0148.",
                "conversation": SOURCE_CONVERSATION,
            },
            redacted_conversation=apply_entities(SOURCE_CONVERSATION, [entity]),
            summary="Called [PERSON].",
            entities=[entity],
            stages={
                "pii_redaction": stage_result(
                    "succeeded",
                    provider="test",
                    model="test",
                    wall_seconds=0.1,
                )
            },
        )
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["status"], "succeeded")
        self.assertIn("source", result)
        self.assertIn("redacted", result)
        self.assertIn("summary", result)
        self.assertIn("entities", result)
        self.assertIn("stages", result)


if __name__ == "__main__":
    unittest.main()
