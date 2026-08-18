import json
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
            [FakeAdapter(1, successful(1)), FakeAdapter(2, fail)],
        )

        self.assertEqual(set(results), {"architecture-1", "architecture-2"})
        self.assertEqual(results["architecture-1"]["status"], "succeeded")
        self.assertEqual(results["architecture-2"]["status"], "failed")
        self.assertIn("downstream unavailable", results["architecture-2"]["error"])
        for number, key in enumerate(ENGINE_KEYS, 1):
            self.assertIs(received[number], report["engines"][key])

    def test_downstream_adapters_run_concurrently_and_report_progress(self) -> None:
        barrier = threading.Barrier(2, timeout=1)
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
            [FakeAdapter(1, run), FakeAdapter(2, run)],
            progress,
        )

        self.assertTrue(all(result["status"] == "succeeded" for result in results.values()))
        for number in range(1, 3):
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
                patch.object(jobs.uploads, "load", return_value={"id": "upload", "channel_map": {}}),
                patch.object(jobs.uploads, "upload_dir", return_value=output),
                patch.object(jobs.uploads, "pii_ground_truth_path", return_value=None),
                patch.object(jobs.stt, "run_benchmark", return_value=report) as benchmark,
                patch.object(jobs, "run_architectures", return_value=architecture_results) as downstream,
            ):
                jobs._run("job", Path("audio.wav"), None)

            benchmark.assert_called_once()
            downstream.assert_called_once()
            self.assertEqual(jobs.get("job")["status"], "succeeded")
            self.assertEqual(jobs.get("job")["result"]["architectures"], architecture_results)

    def test_run_scores_existing_architecture_results_when_annotations_exist(self) -> None:
        report = {"engines": {key: source_entry(key) for key in ENGINE_KEYS}}
        architecture_results = {"architecture-1": {"status": "succeeded"}}
        scores = {"architecture-1": {"f1": 1.0}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            annotation_path = output / "truth.json"
            transcript_path = output / "transcript.txt"
            annotation_path.write_text("{}", encoding="utf-8")
            transcript_path.write_text("REP: Hello.", encoding="utf-8")
            jobs._jobs["job"] = {
                "id": "job",
                "upload_id": "upload",
                "status": "queued",
                "engines": {key: "pending" for key in ENGINE_KEYS},
                "architectures": {"architecture-1": "pending"},
            }
            with (
                patch.object(jobs.uploads, "load", return_value={"id": "upload", "channel_map": {}}),
                patch.object(jobs.uploads, "upload_dir", return_value=output),
                patch.object(jobs.uploads, "pii_ground_truth_path", return_value=annotation_path),
                patch.object(jobs.stt, "run_benchmark", return_value=report) as benchmark,
                patch.object(jobs, "run_architectures", return_value=architecture_results) as downstream,
                patch.object(jobs, "load_ground_truth", return_value=[]) as load_truth,
                patch.object(jobs, "score_architectures", return_value=scores) as score,
            ):
                jobs._run("job", Path("audio.wav"), transcript_path)

            benchmark.assert_called_once()
            downstream.assert_called_once()
            load_truth.assert_called_once_with(annotation_path, "Hello.")
            score.assert_called_once_with(report, "Hello.", [])
            self.assertEqual(jobs.get("job")["result"]["pii_accuracy"], scores)

    def test_run_starts_downstream_when_each_engine_finishes(self) -> None:
        report = {"engines": {key: source_entry(key) for key in ENGINE_KEYS}}

        def benchmark(*args, on_engine_result=None, **kwargs):
            self.assertIsNotNone(on_engine_result)
            for key, entry in report["engines"].items():
                on_engine_result(key, entry)
            return report

        def architecture(architecture_id, source, on_progress=None):
            return {
                "architecture_id": architecture_id,
                "status": "succeeded",
                "source_label": source["label"],
            }

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            jobs._jobs["job"] = {
                "id": "job",
                "upload_id": "upload",
                "status": "queued",
                "engines": {key: "pending" for key in ENGINE_KEYS},
                "architectures": {
                    architecture_id: "pending"
                    for architecture_id in jobs.ARCHITECTURE_FACTORIES
                },
            }
            with (
                patch.object(jobs.uploads, "load", return_value={"id": "upload", "channel_map": {}}),
                patch.object(jobs.uploads, "upload_dir", return_value=output),
                patch.object(jobs.uploads, "pii_ground_truth_path", return_value=None),
                patch.object(jobs.stt, "run_benchmark", side_effect=benchmark),
                patch.object(jobs, "run_architecture", side_effect=architecture) as downstream,
                patch.object(jobs, "run_architectures") as fallback,
            ):
                jobs._run("job", Path("audio.wav"), None)

        self.assertEqual(downstream.call_count, len(jobs.ARCHITECTURE_FACTORIES))
        fallback.assert_not_called()
        results = jobs.get("job")["result"]["architectures"]
        self.assertEqual(list(results), list(jobs.ARCHITECTURE_FACTORIES))
        for architecture_id, result in results.items():
            factory = jobs.ARCHITECTURE_FACTORIES[architecture_id]
            self.assertEqual(result["source_label"], factory.stt_engine_key)

    def test_an_engine_with_no_architecture_is_skipped_rather_than_crashing(self) -> None:
        """A saved or stale STT report may still name a retired engine."""
        report = {
            "engines": {
                key: source_entry(key)
                for key in [*ENGINE_KEYS, "architecture-3-mai-transcribe-batch"]
            }
        }

        def benchmark(*args, on_engine_result=None, **kwargs):
            for key, entry in report["engines"].items():
                on_engine_result(key, entry)
            return report

        def architecture(architecture_id, source, on_progress=None):
            return {"architecture_id": architecture_id, "status": "succeeded"}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            jobs._jobs["job"] = {
                "id": "job",
                "upload_id": "upload",
                "status": "queued",
                "engines": {key: "pending" for key in report["engines"]},
                "architectures": {
                    architecture_id: "pending"
                    for architecture_id in jobs.ARCHITECTURE_FACTORIES
                },
            }
            with (
                patch.object(jobs.uploads, "load", return_value={"id": "upload", "channel_map": {}}),
                patch.object(jobs.uploads, "upload_dir", return_value=output),
                patch.object(jobs.uploads, "pii_ground_truth_path", return_value=None),
                patch.object(jobs.stt, "run_benchmark", side_effect=benchmark),
                patch.object(jobs, "run_architecture", side_effect=architecture) as downstream,
                patch.object(jobs, "run_architectures"),
            ):
                jobs._run("job", Path("audio.wav"), None)

        self.assertEqual(jobs.get("job")["status"], "succeeded")
        self.assertEqual(downstream.call_count, len(jobs.ARCHITECTURE_FACTORIES))
        self.assertEqual(
            list(jobs.get("job")["result"]["architectures"]),
            list(jobs.ARCHITECTURE_FACTORIES),
        )

    def test_cached_default_prefers_latest_persisted_default_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            persisted_dir = root / "mock-call"
            persisted_dir.mkdir()
            persisted = {"architectures": {"architecture-1": {"latency": {}}}}
            checked_in = {"engines": {"historical": {}}}
            (persisted_dir / jobs.RESULT_NAME).write_text(
                json.dumps(persisted), encoding="utf-8"
            )
            checked_in_path = root / "checked-in.json"
            checked_in_path.write_text(json.dumps(checked_in), encoding="utf-8")

            with (
                patch.object(jobs.uploads, "upload_dir", return_value=persisted_dir),
                patch.object(jobs, "CACHED_DEFAULT_RESULT", checked_in_path),
            ):
                self.assertEqual(jobs.cached_default(), persisted)

    def test_restore_completed_rehydrates_user_reports_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_dir = root / "customer-call"
            builtin_dir = root / "mock-call-stereo"
            user_dir.mkdir()
            builtin_dir.mkdir()
            report = {
                "scored": True,
                "engines": {
                    ENGINE_KEYS[0]: source_entry(ENGINE_KEYS[0]),
                    ENGINE_KEYS[1]: {"error": "service unavailable"},
                },
                "architectures": {
                    "architecture-1-azure-language": {"status": "succeeded"},
                    "architecture-2-mai-realtime-deepseek": {"status": "failed"},
                },
            }
            (user_dir / jobs.RESULT_NAME).write_text(
                json.dumps(report), encoding="utf-8"
            )
            (builtin_dir / jobs.RESULT_NAME).write_text(
                json.dumps(report), encoding="utf-8"
            )
            uploads = [
                {
                    "id": "customer-call",
                    "created_at": "2026-08-18T10:00:00+00:00",
                    "builtin": False,
                },
                {
                    "id": "mock-call-stereo",
                    "created_at": "2026-08-18T09:00:00+00:00",
                    "builtin": True,
                },
            ]

            with (
                patch.object(jobs.uploads, "list_all", return_value=uploads),
                patch.object(
                    jobs.uploads,
                    "upload_dir",
                    side_effect=lambda upload_id: root / upload_id,
                ),
            ):
                jobs.restore_completed()

        restored = jobs.get("saved-customer-call")
        self.assertIsNotNone(restored)
        self.assertEqual(restored["status"], "succeeded")
        self.assertEqual(restored["engines"][ENGINE_KEYS[0]], "done")
        self.assertEqual(restored["engines"][ENGINE_KEYS[1]], "failed")
        self.assertEqual(
            restored["architectures"]["architecture-1-azure-language"], "done"
        )
        self.assertEqual(
            restored["architectures"]["architecture-2-mai-realtime-deepseek"],
            "failed",
        )
        self.assertIsNone(jobs.get("saved-mock-call-stereo"))


class ArchitectureApiTests(unittest.TestCase):
    def test_architecture_diagram_returns_the_whitelisted_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "architecture.html"
            path.write_text("<html></html>", encoding="utf-8")
            with patch.dict(
                main.ARCHITECTURE_DIAGRAMS,
                {"architecture-test": path},
                clear=True,
            ):
                response = main.get_architecture_diagram("architecture-test")
                self.assertEqual(Path(response.path), path)
                self.assertEqual(response.media_type, "text/html")

                with self.assertRaisesRegex(Exception, "No such architecture diagram"):
                    main.get_architecture_diagram("architecture-missing")

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
