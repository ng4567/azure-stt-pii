import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from backend.arch2 import DeepSeekProcessor
from backend.architecture import ARCHITECTURE_LABELS
from backend.arch3 import Architecture3Adapter


SOURCE_ENTRY = {
    "transcript": "Call Eleanor at 202-555-0148.",
    "conversation": {
        "id": "call",
        "conversationItems": [{
            "id": "turn-0001",
            "participantId": "CUSTOMER",
            "channel": 1,
            "offset": 10,
            "duration": 20,
            "text": "Call Eleanor at 202-555-0148.",
        }],
    },
    "metrics": {
        "mode": "fast-transcription",
        "wall_seconds": 2.75,
        "audio_seconds": 60.0,
        "real_time_factor": 0.0458,
    },
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


class Architecture3AdapterTests(unittest.TestCase):
    def test_identity_uses_architecture_registry(self) -> None:
        adapter = Architecture3Adapter(processor=Mock())

        self.assertEqual(adapter.architecture_id, "architecture-3-mai-batch-deepseek")
        self.assertEqual(
            adapter.label,
            ARCHITECTURE_LABELS["architecture-3-mai-batch-deepseek"],
        )
        self.assertEqual(
            adapter.stt_engine_key, "architecture-3-mai-transcribe-batch"
        )

    def test_delegates_downstream_processing_to_architecture_2(self) -> None:
        downstream_result = {"status": "succeeded", "stages": {}}
        processor = Mock()
        processor.run.return_value = downstream_result

        with patch(
            "backend.arch3.arch2.DeepSeekProcessor",
            return_value=processor,
            create=True,
        ) as factory:
            adapter = Architecture3Adapter()
            result = adapter.run(SOURCE_ENTRY)

        factory.assert_called_once_with()
        processor.run.assert_called_once_with(
            SOURCE_ENTRY,
            architecture_id="architecture-3-mai-batch-deepseek",
            label=ARCHITECTURE_LABELS["architecture-3-mai-batch-deepseek"],
        )
        self.assertEqual(result["status"], "succeeded")

    def test_batch_stt_replacement_preserves_downstream_stages(self) -> None:
        processor = Mock()
        processor.run.return_value = {
            "status": "succeeded",
            "redacted": None,
            "summary": "No PII.",
            "entities": [],
            "stages": {
                "stt": {"status": "succeeded", "model": "realtime"},
                "regex_detection": {"status": "succeeded"},
                "llm_api_call": {"status": "succeeded"},
                "summary_sanitization": {"status": "succeeded"},
            },
        }

        result = Architecture3Adapter(processor=processor).run(SOURCE_ENTRY)

        self.assertIsNone(result["redacted"])
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["stages"]["stt"]["status"], "succeeded")
        self.assertEqual(
            result["stages"]["stt"]["provider"],
            "Azure AI Speech / Fast Transcription",
        )
        self.assertEqual(
            result["stages"]["stt"]["model"], "MAI-Transcribe-1.5 batch"
        )
        self.assertEqual(result["stages"]["stt"]["wall_seconds"], 2.75)
        self.assertEqual(result["stages"]["stt"]["metrics"], SOURCE_ENTRY["metrics"])
        self.assertEqual(
            list(result["stages"]),
            ["stt", "regex_detection", "llm_api_call", "summary_sanitization"],
        )
        self.assertEqual(
            result["stages"]["regex_detection"], {"status": "succeeded"}
        )
        self.assertEqual(
            result["stages"]["llm_api_call"], {"status": "succeeded"}
        )
        self.assertEqual(
            result["stages"]["summary_sanitization"], {"status": "succeeded"}
        )

    @patch("pathlib.Path.read_text", return_value="prompt")
    def test_shared_processor_emits_summary_only_contract(self, _read_text) -> None:
        post = Mock(return_value=response(
            json.dumps({"summary": "[PERSON] called 202-555-0148."}),
            prompt_tokens=120,
            completion_tokens=12,
        ))
        processor = DeepSeekProcessor(
            endpoint="https://example.openai.azure.com",
            deployment="deepseek",
            credential=Credential(),
            http_post=post,
        )
        source = deepcopy(SOURCE_ENTRY)

        result = Architecture3Adapter(processor=processor).run(source)

        self.assertEqual(source, SOURCE_ENTRY)
        self.assertEqual(
            result["architecture_id"], "architecture-3-mai-batch-deepseek"
        )
        self.assertEqual(
            result["label"],
            ARCHITECTURE_LABELS["architecture-3-mai-batch-deepseek"],
        )
        self.assertIsNone(result["redacted"])
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["summary"], "[PERSON] called [PHONE_NUMBER].")
        self.assertEqual(result["latency"]["stt_seconds"], 2.75)
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
            result["stages"]["stt"]["provider"],
            "Azure AI Speech / Fast Transcription",
        )
        self.assertEqual(
            result["stages"]["stt"]["model"], "MAI-Transcribe-1.5 batch"
        )
        self.assertEqual(
            result["stages"]["stt"]["metrics"], SOURCE_ENTRY["metrics"]
        )
        llm_metrics = result["stages"]["llm_api_call"]["metrics"]
        self.assertTrue(llm_metrics["summary_only"])
        self.assertEqual(
            llm_metrics["output_semantics"], "pii_safe_summary_only"
        )
        self.assertEqual(llm_metrics["input_tokens"], 120)
        self.assertEqual(llm_metrics["output_tokens"], 12)
        self.assertEqual(
            result["stages"]["summary_sanitization"]["metrics"][
                "replacement_count"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
