import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import jobs, main
from backend.contracts import failed_architecture


ENGINE_KEYS = [
    "architecture-1-azure-speech-realtime",
    "architecture-2-mai-transcribe-realtime",
    "architecture-3-mai-transcribe-batch",
]


def source_entry(key: str) -> dict:
    return {
        "label": key,
        "transcript": "Hello.",
        "conversation": {
            "id": "call",
            "language": "en",
            "modality": "transcript",
            "speakerAttributed": False,
            "channelMap": {"0": "speaker"},
            "conversationItems": [],
        },
        "metrics": {"mode": "test", "wall_seconds": 1.0},
    }


class FakeAdapter:
    def __init__(self, number: int, run=None) -> None:
        self.architecture_id = f"architecture-{number}"
        self.label = f"Architecture {number}"
        self.stt_engine_key = ENGINE_KEYS[number - 1]
        self._run = run

    def run(self, source: dict) -> dict:
        if self._run:
            return self._run(source)
        return {
            **failed_architecture(self.architecture_id, self.label, "unused"),
            "status": "succeeded",
            "error": None,
        }


class ArchitectureOrchestrationTests(unittest.TestCase):
    def test_adapters_receive_corresponding_engines_and_fail_independently(self) -> None:
        received = {}

        def successful(number: int):
            def run(source: dict) -> dict:
                received[number] = source
                return {
                    **failed_architecture(f"architecture-{number}", f"Architecture {number}", "unused"),
                    "status": "succeeded",
                    "error": None,
                }

            return run

        def fail(source: dict) -> dict:
            received[2] = source
            raise RuntimeError("downstream unavailable")

        report = {"engines": {key: source_entry(key) for key in ENGINE_KEYS}}
        results = jobs.run_architectures(
            report,
            [FakeAdapter(1, successful(1)), FakeAdapter(2, fail), FakeAdapter(3, successful(3))],
        )

        self.assertEqual(set(results), {"architecture-1", "architecture-2", "architecture-3"})
        self.assertEqual(results["architecture-1"]["status"], "succeeded")
        self.assertEqual(results["architecture-2"]["status"], "failed")
        self.assertIn("downstream unavailable", results["architecture-2"]["error"])
        self.assertEqual(results["architecture-3"]["status"], "succeeded")
        for number, key in enumerate(ENGINE_KEYS, 1):
            self.assertIs(received[number], report["engines"][key])

    def test_downstream_adapters_run_concurrently_and_report_progress(self) -> None:
        barrier = threading.Barrier(3, timeout=1)
        states = []
        state_lock = threading.Lock()

        def run(source: dict) -> dict:
            barrier.wait()
            number = ENGINE_KEYS.index(source["label"]) + 1
            return {
                **failed_architecture(f"architecture-{number}", f"Architecture {number}", "unused"),
                "status": "succeeded",
                "error": None,
            }

        def progress(architecture_id: str, state: str) -> None:
            with state_lock:
                states.append((architecture_id, state))

        report = {"engines": {key: source_entry(key) for key in ENGINE_KEYS}}
        results = jobs.run_architectures(
            report,
            [FakeAdapter(1, run), FakeAdapter(2, run), FakeAdapter(3, run)],
            progress,
        )

        self.assertTrue(all(result["status"] == "succeeded" for result in results.values()))
        for number in range(1, 4):
            self.assertIn((f"architecture-{number}", "running"), states)
            self.assertIn((f"architecture-{number}", "done"), states)

    def test_empty_adapter_selection_is_supported(self) -> None:
        self.assertEqual(jobs.run_architectures({"engines": {}}, []), {})


class JobIntegrationTests(unittest.TestCase):
    def tearDown(self) -> None:
        jobs._jobs.clear()

    def test_run_invokes_stt_once_then_persists_architectures(self) -> None:
        report = {"engines": {key: source_entry(key) for key in ENGINE_KEYS}}
        architecture_results = {"architecture-1": {"status": "succeeded"}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            jobs._jobs["job"] = {
                "id": "job",
                "upload_id": "upload",
                "status": "queued",
                "engines": {key: "pending" for key in ENGINE_KEYS},
                "architectures": {"architecture-1": "pending"},
            }
            with (
                patch.object(jobs.uploads, "load", return_value={"channel_map": {}}),
                patch.object(jobs.uploads, "upload_dir", return_value=output),
                patch.object(jobs.stt, "run_benchmark", return_value=report) as benchmark,
                patch.object(jobs, "run_architectures", return_value=architecture_results) as downstream,
            ):
                jobs._run("job", Path("audio.wav"), None)

            benchmark.assert_called_once()
            downstream.assert_called_once()
            self.assertEqual(jobs.get("job")["status"], "succeeded")
            self.assertEqual(jobs.get("job")["result"]["architectures"], architecture_results)


class ArchitectureApiTests(unittest.TestCase):
    def test_architecture_result_supports_new_and_historical_reports(self) -> None:
        result = {"architecture_id": "architecture-1", "status": "succeeded"}
        with patch.object(
            main.jobs,
            "get",
            return_value={
                "status": "succeeded",
                "result": {"architectures": {"architecture-1": result}},
            },
        ):
            self.assertIs(main.get_architecture_result("job", "architecture-1"), result)

        with patch.object(
            main.jobs,
            "get",
            return_value={"status": "succeeded", "result": {"engines": {}}},
        ):
            with self.assertRaisesRegex(Exception, "No such architecture"):
                main.get_architecture_result("job", "architecture-1")


if __name__ == "__main__":
    unittest.main()
