import unittest
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

from backend.arch1 import Architecture1Adapter, AzureLanguageClient


CONVERSATION = {
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
            "offset": 100,
            "duration": 20,
            "text": "Call Eleanor at 202-555-0148.",
            "lexical": "call eleanor at 202 555 0148",
        }
    ],
}


class FakeLanguageClient:
    def __init__(self) -> None:
        self.pii_input = None
        self.summary_input = None

    def redact_pii(self, conversation):
        self.pii_input = deepcopy(conversation)
        return {
            "conversations": [{
                "id": "call",
                "conversationItems": [{
                    "id": "turn-0001",
                    "entities": [
                        {"category": "Person", "text": "Eleanor", "offset": 5, "length": 7, "confidenceScore": 0.99},
                        {"category": "Phone Number", "text": "202-555-0148", "offset": 16, "length": 12, "confidenceScore": 0.98},
                    ],
                }],
            }]
        }

    def summarize(self, conversation):
        self.summary_input = deepcopy(conversation)
        return {
            "conversations": [{
                "id": "call",
                "summaries": [
                    {"aspect": "issue", "text": "Eleanor asked to call 202-555-0148."},
                    {"aspect": "resolution", "text": "The request was recorded."},
                ],
            }]
        }


class FakeResponse:
    def __init__(self, *, payload=None, headers=None) -> None:
        self._payload = payload
        self.headers = headers or {}
        self.raise_for_status = Mock()

    def json(self):
        return self._payload


class AzureLanguageClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credential = Mock()
        self.credential.get_token.return_value = SimpleNamespace(token="actual-access-token")
        self.session = Mock()
        self.client = AzureLanguageClient(
            endpoint="https://language.example",
            api_version="2024-11-01",
            credential=self.credential,
            session=self.session,
            timeout=17,
            poll_interval=0,
            sleep=lambda _: None,
        )

    def _configure_job(self, result_kind, results) -> None:
        self.session.post.return_value = FakeResponse(
            headers={
                "Operation-Location":
                    "https://language.example/language/analyze-conversations/jobs/job-id?api-version=2024-11-01"
            }
        )
        self.session.get.side_effect = [
            FakeResponse(payload={"status": "running"}),
            FakeResponse(payload={
                "status": "succeeded",
                "tasks": {"items": [{
                    "kind": result_kind,
                    "status": "succeeded",
                    "results": results,
                }]},
            }),
        ]

    def test_pii_http_contract_and_bearer_token(self) -> None:
        results = {"conversations": [], "errors": []}
        self._configure_job("conversationalPIIResults", results)

        self.assertIs(self.client.redact_pii(CONVERSATION), results)

        post = self.session.post.call_args
        self.assertEqual(
            post.args[0],
            "https://language.example/language/analyze-conversations/jobs",
        )
        self.assertEqual(post.kwargs["params"], {"api-version": "2024-11-01"})
        self.assertEqual(post.kwargs["timeout"], 17)
        self.assertEqual(
            post.kwargs["headers"]["Authorization"],
            "Bearer actual-access-token",
        )
        task = post.kwargs["json"]["tasks"][0]
        self.assertEqual(task["kind"], "ConversationalPIITask")
        self.assertEqual(task["taskName"], "Conversation PII")
        self.assertEqual(task["parameters"]["redactionSource"], "text")
        self.assertEqual(task["parameters"]["piiCategories"], ["All"])
        sent = post.kwargs["json"]["analysisInput"]["conversations"][0]
        self.assertEqual(sent["conversationItems"][0]["text"], CONVERSATION["conversationItems"][0]["text"])
        for call in self.session.get.call_args_list:
            self.assertEqual(
                call.kwargs["headers"]["Authorization"],
                "Bearer actual-access-token",
            )
            self.assertEqual(call.kwargs["timeout"], 17)

    def test_summarization_http_contract_and_response_shape(self) -> None:
        results = {
            "conversations": [{
                "id": "call",
                "summaries": [{"aspect": "issue", "text": "An issue."}],
            }],
            "errors": [],
        }
        self._configure_job("conversationalSummarizationResults", results)

        self.assertIs(self.client.summarize(CONVERSATION), results)

        task = self.session.post.call_args.kwargs["json"]["tasks"][0]
        self.assertEqual(task["kind"], "ConversationalSummarizationTask")
        self.assertEqual(task["taskName"], "Conversation Summarization")
        self.assertEqual(task["parameters"]["summaryAspects"], ["issue", "resolution"])
        self.assertEqual(task["parameters"]["modelVersion"], "latest")

    def test_wrong_result_kind_is_rejected(self) -> None:
        self._configure_job("ConversationalPIITask", {"conversations": []})

        with self.assertRaisesRegex(ValueError, "wrong result kind"):
            self.client.redact_pii(CONVERSATION)

    def test_foundry_project_endpoint_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a Foundry project endpoint"):
            AzureLanguageClient(
                endpoint=(
                    "https://finance-app-resource.services.ai.azure.com/"
                    "api/projects/finance-app"
                ),
                api_version="2024-11-01",
                credential=self.credential,
                session=self.session,
            )


class Architecture1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = {
            "transcript": "Call Eleanor at 202-555-0148.",
            "conversation": deepcopy(CONVERSATION),
            "metrics": {
                "mode": "real-time",
                "wall_seconds": 3.5,
                "time_to_full_transcript": 3.75,
            },
        }

    def test_services_receive_independent_original_canonical_text(self) -> None:
        client = FakeLanguageClient()
        result = Architecture1Adapter(client, clock=lambda: 1.0).run(self.source)

        self.assertNotEqual(client.pii_input, client.summary_input)
        self.assertEqual(
            client.pii_input["conversationItems"][0]["participantId"],
            "CUSTOMER",
        )
        self.assertEqual(
            client.summary_input["conversationItems"][0]["participantId"],
            "Customer",
        )
        self.assertEqual(
            client.summary_input["conversationItems"][0]["text"],
            "Call Eleanor at 202-555-0148.",
        )
        self.assertNotIn("channel", client.pii_input["conversationItems"][0])
        self.assertEqual(result["source"]["conversation"], CONVERSATION)

    def test_rep_is_projected_to_agent_for_issue_resolution_summary(self) -> None:
        client = FakeLanguageClient()
        source = deepcopy(self.source)
        source["conversation"]["conversationItems"][0]["participantId"] = "REP"

        Architecture1Adapter(client, clock=lambda: 1.0).run(source)

        self.assertEqual(
            client.pii_input["conversationItems"][0]["participantId"],
            "REP",
        )
        self.assertEqual(
            client.summary_input["conversationItems"][0]["participantId"],
            "Agent",
        )

    def test_unknown_summary_participant_is_rejected(self) -> None:
        source = deepcopy(self.source)
        source["conversation"]["conversationItems"][0]["participantId"] = "OBSERVER"

        with self.assertRaisesRegex(ValueError, "unsupported participant 'OBSERVER'"):
            Architecture1Adapter(FakeLanguageClient()).run(source)

    def test_entities_are_parsed_and_applied_with_turn_relative_offsets(self) -> None:
        result = Architecture1Adapter(FakeLanguageClient(), clock=lambda: 1.0).run(self.source)

        self.assertEqual(
            result["redacted"]["transcript"],
            "Call [PERSON] at [PHONE_NUMBER].",
        )
        self.assertEqual(result["entities"][0]["turn_id"], "turn-0001")
        self.assertEqual(result["entities"][0]["offset"], 5)
        self.assertEqual(
            set(result["stages"]),
            {
                "stt",
                "pii_endpoint",
                "summarizer_endpoint",
                "transcript_redaction",
                "summary_sanitization",
            },
        )

    def test_summary_is_sanitized_with_typed_placeholders(self) -> None:
        result = Architecture1Adapter(FakeLanguageClient(), clock=lambda: 1.0).run(self.source)

        self.assertEqual(
            result["summary"],
            "[PERSON] asked to call [PHONE_NUMBER].\nThe request was recorded.",
        )
        self.assertEqual(
            result["stages"]["summary_sanitization"]["metrics"]["replacement_count"],
            2,
        )
        self.assertEqual(
            result["stages"]["pii_endpoint"]["metrics"]["input_characters"],
            len("Call Eleanor at 202-555-0148."),
        )
        self.assertGreater(
            result["stages"]["summarizer_endpoint"]["metrics"]["output_characters"],
            0,
        )
        self.assertEqual(result["latency"]["stt_seconds"], 3.75)

    def test_pii_and_summarizer_endpoints_run_in_parallel(self) -> None:
        client = FakeLanguageClient()
        barrier = Barrier(2)
        original_pii = client.redact_pii
        original_summary = client.summarize

        def pii(conversation):
            barrier.wait(timeout=1)
            return original_pii(conversation)

        def summary(conversation):
            barrier.wait(timeout=1)
            return original_summary(conversation)

        client.redact_pii = pii
        client.summarize = summary

        result = Architecture1Adapter(client).run(self.source)

        self.assertEqual(result["status"], "succeeded")

    def test_malformed_pii_response_is_rejected(self) -> None:
        client = FakeLanguageClient()
        client.redact_pii = lambda conversation: {"conversations": [{"id": "call"}]}

        with self.assertRaisesRegex(ValueError, "omitted conversationItems"):
            Architecture1Adapter(client).run(self.source)


if __name__ == "__main__":
    unittest.main()
