import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from backend.arch2 import (
    Architecture2Adapter,
    DeepSeekProcessor,
    compact_summary_conversation,
)
from backend.arch2.adapter import detect_regex_hints, sanitize_summary_with_hints


SOURCE = {
    "transcript": "Call Eleanor at 202-555-0148.",
    "conversation": {
        "id": "call",
        "channelMap": {"1": "CUSTOMER"},
        "conversationItems": [{
            "id": "turn-0001", "participantId": "CUSTOMER", "channel": 1,
            "offset": 10, "duration": 20,
            "language": "en-US", "modality": "speech",
            "text": "Call Eleanor at 202-555-0148.",
        }],
    },
    "metrics": {"mode": "realtime", "wall_seconds": 2.0},
}


class Credential:
    def get_token(self, scope):
        return type("Token", (), {"token": "test-token"})()


def response(content, *, prompt_tokens=0, completion_tokens=0):
    result = Mock()
    result.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }
    return result


class Architecture2Tests(unittest.TestCase):
    def adapter(self, post):
        return Architecture2Adapter(
            endpoint="https://example.openai.azure.com", deployment="deepseek",
            credential=Credential(), http_post=post,
        )

    def test_compact_projection_coalesces_consecutive_participants(self):
        conversation = {
            "id": "call-id",
            "channelMap": {"1": "CUSTOMER"},
            "conversationItems": [
                {
                    "id": "turn-1",
                    "participantId": "CUSTOMER",
                    "channel": 1,
                    "offset": 10,
                    "duration": 20,
                    "language": "en-US",
                    "modality": "speech",
                    "text": " First sentence. ",
                },
                {
                    "id": "turn-2",
                    "participantId": "CUSTOMER",
                    "channel": 1,
                    "offset": 30,
                    "duration": 10,
                    "text": "Second sentence.",
                },
                {
                    "id": "turn-3",
                    "participantId": "REP",
                    "channel": 2,
                    "text": "A response.",
                },
                {"id": "turn-4", "participantId": "REP", "text": "   "},
                {"id": "turn-5", "text": "Unattributed note."},
            ],
        }

        result = compact_summary_conversation(conversation)

        self.assertEqual(
            result,
            [
                {
                    "participant": "CUSTOMER",
                    "text": "First sentence. Second sentence.",
                },
                {"participant": "REP", "text": "A response."},
                {"participant": "UNKNOWN", "text": "Unattributed note."},
            ],
        )
        self.assertEqual(conversation["conversationItems"][0]["text"], " First sentence. ")

    def test_system_prompt_sets_summary_range_without_hint_instructions(self):
        prompt = (
            Path(__file__).resolve().parents[2] / "data" / "system_prompt.txt"
        ).read_text()

        self.assertIn("80-120 words", prompt)
        self.assertNotIn("candidate hint", prompt.casefold())
        self.assertIn("Return only the summary field", prompt)

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_openai_v1_endpoint_omits_legacy_api_version(self, _read_text):
        post = Mock(return_value=response(json.dumps({"summary": "No PII."})))
        adapter = Architecture2Adapter(
            endpoint="https://example.services.ai.azure.com/openai/v1",
            deployment="deepseek", credential=Credential(), http_post=post,
        )

        adapter.run(deepcopy(SOURCE))

        self.assertEqual(post.call_args.args[0], "https://example.services.ai.azure.com/openai/v1/chat/completions")
        self.assertNotIn("params", post.call_args.kwargs)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek")

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_legacy_deployment_endpoint_includes_api_version(self, _read_text):
        post = Mock(return_value=response(json.dumps({"summary": "No PII."})))

        self.adapter(post).run(deepcopy(SOURCE))

        self.assertEqual(
            post.call_args.args[0],
            "https://example.openai.azure.com/openai/deployments/deepseek/chat/completions",
        )
        self.assertEqual(post.call_args.kwargs["params"], {"api-version": "2025-04-01-preview"})
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek")

    @patch("pathlib.Path.read_text", return_value="system prompt\nexactly")
    def test_summary_only_request_metrics_and_source_immutability(self, _read_text):
        post = Mock(return_value=response(json.dumps({
            "summary": "[PERSON] was called at 202-555-0148.",
        }), prompt_tokens=120, completion_tokens=45))
        source = deepcopy(SOURCE)

        result = self.adapter(post).run(source)

        self.assertEqual(source, SOURCE)
        request = post.call_args.kwargs["json"]
        self.assertEqual(request["messages"][0]["content"], "system prompt\nexactly")
        user_content = json.loads(request["messages"][1]["content"])
        self.assertEqual(
            user_content,
            {
                "instruction": (
                    "Return only JSON matching the supplied response schema with one concise, "
                    "PII-safe call summary of 80-120 words. Replace every PII value with a typed "
                    "[CATEGORY] placeholder such as [PERSON], [PHONE_NUMBER], or "
                    "[DATE_OF_BIRTH]. Do not reproduce the conversation, individual turns, "
                    "speaker labels, or any raw PII."
                ),
                "conversation": [{
                    "participant": "CUSTOMER",
                    "text": "Call Eleanor at 202-555-0148.",
                }],
            },
        )
        serialized_user_content = request["messages"][1]["content"]
        self.assertNotIn(SOURCE["conversation"]["id"], json.dumps(user_content["conversation"]))
        serialized_keys = {
            key
            for segment in user_content["conversation"]
            for key in segment
        }
        for omitted in (
            "channel",
            "offset",
            "duration",
            "language",
            "modality",
            "channelMap",
            "regexCandidateHints",
            "turnId",
            "id",
        ):
            self.assertNotIn(omitted, serialized_keys)
        self.assertNotIn("regexCandidateHints", user_content)
        self.assertEqual(serialized_user_content.count("202-555-0148"), 1)
        self.assertEqual(
            request["response_format"]["json_schema"]["schema"],
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary"],
                "properties": {"summary": {"type": "string"}},
            },
        )
        metrics = result["stages"]["llm_api_call"]["metrics"]
        self.assertTrue(metrics["summary_only"])
        self.assertEqual(metrics["output_semantics"], "pii_safe_summary_only")
        self.assertEqual(metrics["input_tokens"], 120)
        self.assertEqual(metrics["output_tokens"], 45)
        self.assertEqual(metrics["total_tokens"], 165)
        self.assertIsNone(result["redacted"])
        self.assertEqual(result["summary"], "[PERSON] was called at [PHONE_NUMBER].")
        self.assertEqual(result["entities"], [])
        self.assertEqual(
            list(result["stages"]),
            [
                "stt",
                "regex_detection",
                "request_preparation",
                "llm_api_call",
                "response_validation",
                "summary_sanitization",
                "backend_overhead",
            ],
        )
        self.assertEqual(
            result["stages"]["regex_detection"]["metrics"]["candidate_count"],
            1,
        )
        preparation_metrics = result["stages"]["request_preparation"]["metrics"]
        self.assertEqual(preparation_metrics["source_turn_count"], 1)
        self.assertEqual(preparation_metrics["projected_segment_count"], 1)
        self.assertEqual(
            preparation_metrics["user_content_characters"],
            len(serialized_user_content),
        )
        self.assertEqual(
            result["stages"]["summary_sanitization"]["metrics"]["replacement_count"],
            1,
        )

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_malformed_response_errors(self, _read_text):
        post = Mock(return_value=response("not json"))
        with self.assertRaisesRegex(ValueError, "malformed structured JSON"):
            self.adapter(post).run(deepcopy(SOURCE))
        self.assertEqual(post.call_count, 2)

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_extra_model_output_is_retried_and_usage_is_accumulated(self, _read_text):
        invalid = response(json.dumps({
            "summary": "No PII.",
            "conversation": SOURCE["conversation"],
        }), prompt_tokens=10, completion_tokens=5)
        valid = response(
            json.dumps({"summary": "No PII."}),
            prompt_tokens=11,
            completion_tokens=3,
        )
        post = Mock(side_effect=[invalid, valid])

        result = self.adapter(post).run(deepcopy(SOURCE))

        self.assertEqual(post.call_count, 2)
        metrics = result["stages"]["llm_api_call"]["metrics"]
        self.assertEqual(metrics["attempts"], 2)
        self.assertEqual(metrics["input_tokens"], 21)
        self.assertEqual(metrics["output_tokens"], 8)
        self.assertEqual(metrics["total_tokens"], 29)

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_regex_candidate_literals_are_sanitized_without_entities(self, _read_text):
        literals = {
            "PHONE_NUMBER": "202-555-0148",
            "EMAIL": "Customer@example.com",
            "SSN": "123-45-6789",
            "CREDIT_CARD": "4111 1111 1111 1111",
            "ACCOUNT_NUMBER": "NS49382176",
            "IP_ADDRESS": "192.168.10.25",
            "DATE_OF_BIRTH": "01/02/1980",
        }
        text = " | ".join(literals.values())
        source = {
            "transcript": text,
            "conversation": {
                "id": "call",
                "conversationItems": [{
                    "id": "turn-0001",
                    "participantId": "CUSTOMER",
                    "channel": 1,
                    "offset": 0,
                    "duration": 1,
                    "text": text,
                }],
            },
            "metrics": {"mode": "realtime", "wall_seconds": 2.0},
        }
        leaked_summary = "Contact customer@EXAMPLE.COM at " + " and ".join(
            value for category, value in literals.items() if category != "EMAIL"
        )
        post = Mock(return_value=response(json.dumps({"summary": leaked_summary})))

        result = self.adapter(post).run(deepcopy(source))

        for literal in literals.values():
            self.assertNotIn(literal.casefold(), result["summary"].casefold())
        for category in literals:
            self.assertIn(f"[{category}]", result["summary"])
        self.assertEqual(result["entities"], [])
        self.assertIsNone(result["redacted"])
        self.assertEqual(
            result["stages"]["summary_sanitization"]["metrics"]["replacement_count"],
            len(literals),
        )

    def test_account_number_separator_variants_are_detected_and_sanitized(self):
        variants = ["NS49382176", "NS 4938 2176", "4938-2176"]
        text = " | ".join(variants)
        conversation = {
            "conversationItems": [
                {"id": "turn-1", "participantId": "CUSTOMER", "text": text}
            ]
        }

        hints = detect_regex_hints(conversation)
        summary, replacements = sanitize_summary_with_hints(text, hints)

        self.assertEqual(summary, " | ".join(["[ACCOUNT_NUMBER]"] * 3))
        self.assertEqual(replacements, 3)

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_exported_processor_supports_custom_identity(self, _read_text):
        post = Mock(return_value=response(json.dumps({"summary": "No PII."})))
        processor = DeepSeekProcessor(
            endpoint="https://example.openai.azure.com", deployment="deepseek",
            credential=Credential(), http_post=post,
        )

        result = processor.run(SOURCE, architecture_id="custom-id", label="Custom")

        self.assertEqual(result["architecture_id"], "custom-id")
        self.assertEqual(result["label"], "Custom")


if __name__ == "__main__":
    unittest.main()
