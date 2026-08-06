import unittest
from unittest.mock import Mock, patch

from backend.architecture import ARCHITECTURE_LABELS
from backend.arch3 import Architecture3Adapter


SOURCE_ENTRY = {
    "transcript": "A batch transcript.",
    "conversation": {"conversationItems": []},
    "metrics": {
        "mode": "fast-transcription",
        "wall_seconds": 2.75,
        "audio_seconds": 60.0,
        "real_time_factor": 0.0458,
    },
}


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

    def test_common_stt_stage_preserves_batch_metrics(self) -> None:
        processor = Mock()
        processor.run.return_value = {
            "status": "succeeded",
            "stages": {"pii_redaction": {"status": "succeeded"}},
        }

        result = Architecture3Adapter(processor=processor).run(SOURCE_ENTRY)

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
            result["stages"]["pii_redaction"], {"status": "succeeded"}
        )


if __name__ == "__main__":
    unittest.main()
