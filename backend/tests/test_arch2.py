import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from backend.arch2 import Architecture2Adapter, DeepSeekProcessor


SOURCE = {
    "transcript": "Call Eleanor at 202-555-0148.",
    "conversation": {
        "id": "call",
        "conversationItems": [{
            "id": "turn-0001", "participantId": "CUSTOMER", "channel": 1,
            "offset": 10, "duration": 20,
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

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_openai_v1_endpoint_omits_legacy_api_version(self, _read_text):
        post = Mock(return_value=response(json.dumps({
            "summary": "No PII.",
            "conversation": {"conversationItems": [deepcopy(SOURCE["conversation"]["conversationItems"][0])]},
        })))
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
        post = Mock(return_value=response(json.dumps({
            "summary": "No PII.",
            "conversation": {"conversationItems": [deepcopy(SOURCE["conversation"]["conversationItems"][0])]},
        })))

        self.adapter(post).run(deepcopy(SOURCE))

        self.assertEqual(
            post.call_args.args[0],
            "https://example.openai.azure.com/openai/deployments/deepseek/chat/completions",
        )
        self.assertEqual(post.call_args.kwargs["params"], {"api-version": "2025-04-01-preview"})
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek")

    @patch("pathlib.Path.read_text", return_value="system prompt\nexactly")
    def test_exact_prompt_source_immutability_and_typed_redaction(self, _read_text):
        post = Mock(return_value=response(json.dumps({
            "summary": "Eleanor was called at 202-555-0148.",
            "conversation": {"conversationItems": [{
                "id": "turn-0001", "participantId": "CUSTOMER", "channel": 1,
                "offset": 10, "duration": 20,
                "text": "Call [PERSON] at [PHONE_NUMBER].",
            }]},
        }), prompt_tokens=120, completion_tokens=45))
        source = deepcopy(SOURCE)

        result = self.adapter(post).run(source)

        self.assertEqual(source, SOURCE)
        request = post.call_args.kwargs["json"]
        self.assertEqual(request["messages"][0]["content"], "system prompt\nexactly")
        metrics = result["stages"]["pii_redaction"]["metrics"]
        self.assertEqual(metrics["input_tokens"], 120)
        self.assertEqual(metrics["output_tokens"], 45)
        self.assertEqual(metrics["total_tokens"], 165)
        self.assertEqual(result["redacted"]["transcript"], "Call [PERSON] at [PHONE_NUMBER].")
        self.assertEqual(result["summary"], "[PERSON] was called at [PHONE_NUMBER].")
        self.assertEqual([entity["category"] for entity in result["entities"]], ["PERSON", "PHONE_NUMBER"])
        self.assertEqual(list(result["stages"]), ["stt", "pii_redaction", "summarization", "summary_sanitization"])

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_malformed_response_errors(self, _read_text):
        post = Mock(return_value=response("not json"))
        with self.assertRaisesRegex(ValueError, "malformed structured JSON"):
            self.adapter(post).run(deepcopy(SOURCE))
        self.assertEqual(post.call_count, 2)

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_invalid_model_output_is_retried(self, _read_text):
        invalid = response(json.dumps({
            "summary": "No PII.",
            "conversation": {"conversationItems": [{
                **SOURCE["conversation"]["conversationItems"][0],
                "text": "Changed unrelated content.",
            }]},
        }))
        valid = response(json.dumps({
            "summary": "No PII.",
            "conversation": {"conversationItems": [deepcopy(SOURCE["conversation"]["conversationItems"][0])]},
        }))
        post = Mock(side_effect=[invalid, valid])

        result = self.adapter(post).run(deepcopy(SOURCE))

        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["stages"]["pii_redaction"]["metrics"]["attempts"], 2)

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_exported_processor_supports_custom_identity(self, _read_text):
        post = Mock(return_value=response(json.dumps({
            "summary": "No PII.",
            "conversation": {"conversationItems": [{
                "id": "turn-0001", "participantId": "CUSTOMER", "channel": 1,
                "offset": 10, "duration": 20, "text": "Call Eleanor at 202-555-0148.",
            }]},
        })))
        processor = DeepSeekProcessor(
            endpoint="https://example.openai.azure.com", deployment="deepseek",
            credential=Credential(), http_post=post,
        )

        result = processor.run(SOURCE, architecture_id="custom-id", label="Custom")

        self.assertEqual(result["architecture_id"], "custom-id")
        self.assertEqual(result["label"], "Custom")


if __name__ == "__main__":
    unittest.main()
