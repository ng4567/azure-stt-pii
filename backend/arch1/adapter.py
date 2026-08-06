"""Architecture 1: Azure Speech, Conversation PII, and summarization."""

from __future__ import annotations

import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping

import requests
from azure.identity import DefaultAzureCredential

from backend.app import config
from backend.architecture import ARCHITECTURE_LABELS
from backend.contracts import PiiEntity, architecture_result, stage_result, stt_stage
from backend.redaction import apply_entities, sanitize_summary
from data.conversation import conversation_pii_input

ARCHITECTURE_ID = "architecture-1-azure-language"
STT_ENGINE_KEY = "architecture-1-azure-speech-realtime"
_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
_SUMMARY_ROLES = {
    "agent": "Agent",
    "rep": "Agent",
    "representative": "Agent",
    "customer": "Customer",
    "caller": "Customer",
}


def _summarization_input(conversation: Mapping[str, Any]) -> dict[str, Any]:
    projected = deepcopy(dict(conversation))
    for item in projected.get("conversationItems", []):
        participant = str(item.get("participantId", ""))
        role = _SUMMARY_ROLES.get(participant.casefold())
        if role is None:
            raise ValueError(
                "Azure Language issue/resolution summarization requires each participant "
                f"to map to Agent or Customer; unsupported participant {participant!r}."
            )
        item["participantId"] = role
    return projected


def _conversation_characters(conversation: Mapping[str, Any]) -> int:
    return sum(
        len(str(item.get("text", "")))
        for item in conversation.get("conversationItems", [])
        if isinstance(item, Mapping)
    )


class AzureLanguageClient:
    """Small injectable HTTP client for Azure Language conversation analyses."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_version: str | None = None,
        credential: Any | None = None,
        session: Any | None = None,
        timeout: float | None = None,
        poll_interval: float = 0.25,
        max_polls: int = 480,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = (endpoint if endpoint is not None else config.AZURE_LANGUAGE_ENDPOINT).rstrip("/")
        self.api_version = api_version or config.AZURE_LANGUAGE_API_VERSION
        self.credential = credential or DefaultAzureCredential()
        self.session = session or requests.Session()
        self.timeout = timeout or config.AZURE_REQUEST_TIMEOUT_SECONDS
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.sleep = sleep
        if not self.endpoint:
            raise ValueError("AZURE_LANGUAGE_ENDPOINT is required for Architecture 1.")
        if "/api/projects/" in self.endpoint.lower():
            raise ValueError(
                "AZURE_LANGUAGE_ENDPOINT must be the Azure Language Cognitive Services "
                "data-plane endpoint, not a Foundry project endpoint."
            )
        if not self.api_version:
            raise ValueError("AZURE_LANGUAGE_API_VERSION is required for Architecture 1.")

    def redact_pii(self, conversation: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._analyze(
            "ConversationalPIITask",
            "conversationalPIIResults",
            "Conversation PII",
            conversation,
            {
                "modelVersion": "latest",
                "piiCategories": ["All"],
                "redactionSource": "text",
            },
        )

    def summarize(self, conversation: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._analyze(
            "ConversationalSummarizationTask",
            "conversationalSummarizationResults",
            "Conversation Summarization",
            conversation,
            {
                "modelVersion": "latest",
                "summaryAspects": ["issue", "resolution"],
            },
        )

    def _analyze(
        self,
        task_kind: str,
        result_kind: str,
        task_name: str,
        conversation: Mapping[str, Any],
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        token = self.credential.get_token(_TOKEN_SCOPE).token
        url = f"{self.endpoint}/language/analyze-conversations/jobs"
        body = {
            "displayName": f"architecture-1-{task_kind}",
            "analysisInput": {"conversations": [deepcopy(dict(conversation))]},
            "tasks": [
                {
                    "taskName": task_name,
                    "kind": task_kind,
                    "parameters": dict(parameters),
                }
            ],
        }
        response = self.session.post(
            url,
            params={"api-version": self.api_version},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        operation_url = response.headers.get("operation-location") or response.headers.get(
            "Operation-Location"
        )
        if not operation_url:
            raise ValueError("Azure Language response omitted Operation-Location.")

        for _ in range(self.max_polls):
            poll = self.session.get(
                operation_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            poll.raise_for_status()
            payload = poll.json()
            if not isinstance(payload, Mapping):
                raise ValueError("Azure Language job response must be a JSON object.")
            status = payload.get("status")
            if status == "succeeded":
                return self._task_result(payload, result_kind)
            if status in {"failed", "cancelled"}:
                raise RuntimeError(f"Azure Language {task_kind} job {status}: {payload.get('errors') or payload.get('error') or 'unknown error'}")
            if status not in {"notStarted", "running"}:
                raise ValueError(f"Azure Language returned invalid job status: {status!r}.")
            self.sleep(self.poll_interval)
        raise TimeoutError(f"Azure Language {task_kind} job did not complete after {self.max_polls} polls.")

    @staticmethod
    def _task_result(payload: Mapping[str, Any], result_kind: str) -> Mapping[str, Any]:
        tasks = payload.get("tasks")
        items = tasks.get("items") if isinstance(tasks, Mapping) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping):
            raise ValueError("Azure Language response must contain exactly one task item.")
        item = items[0]
        if item.get("kind") != result_kind:
            raise ValueError(f"Azure Language returned the wrong result kind for {result_kind}.")
        if item.get("status") != "succeeded":
            raise RuntimeError(f"Azure Language {result_kind} task failed: {item.get('errors') or 'unknown error'}")
        results = item.get("results")
        if not isinstance(results, Mapping):
            raise ValueError(f"Azure Language {result_kind} response omitted task results.")
        if results.get("errors") not in (None, []):
            raise RuntimeError(
                f"Azure Language {result_kind} returned errors: {results['errors']}"
            )
        return results


class Architecture1Adapter:
    architecture_id = ARCHITECTURE_ID
    label = ARCHITECTURE_LABELS[ARCHITECTURE_ID]
    stt_engine_key = STT_ENGINE_KEY

    def __init__(
        self,
        client: AzureLanguageClient | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.client = client or AzureLanguageClient()
        self.clock = clock

    def run(self, source_entry: Mapping[str, Any]) -> dict[str, Any]:
        conversation = source_entry.get("conversation")
        transcript = source_entry.get("transcript")
        if not isinstance(conversation, Mapping):
            raise ValueError("Architecture 1 requires an STT canonical conversation.")
        if not isinstance(transcript, str):
            raise ValueError("Architecture 1 requires an STT transcript string.")
        projected = conversation_pii_input(conversation)
        summarization_input = _summarization_input(projected)
        input_characters = _conversation_characters(projected)

        downstream_started = self.clock()

        def run_pii() -> tuple[Mapping[str, Any], float]:
            started = self.clock()
            result = self.client.redact_pii(deepcopy(projected))
            return result, self.clock() - started

        def run_summary() -> tuple[Mapping[str, Any], float]:
            started = self.clock()
            result = self.client.summarize(deepcopy(summarization_input))
            return result, self.clock() - started

        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="azure-language"
        ) as pool:
            pii_future = pool.submit(run_pii)
            summary_future = pool.submit(run_summary)
            pii_results, pii_seconds = pii_future.result()
            summary_results, summary_seconds = summary_future.result()

        entities = self._parse_entities(pii_results, conversation)

        started = self.clock()
        redacted = apply_entities(conversation, entities)
        transcript_redaction_seconds = self.clock() - started

        started = self.clock()
        raw_summary = self._parse_summary(summary_results)
        summary, replacements = sanitize_summary(raw_summary, entities)
        sanitization_seconds = self.clock() - started
        downstream_seconds = self.clock() - downstream_started
        stages = {
            "stt": stt_stage(source_entry),
            "pii_endpoint": stage_result(
                "succeeded", provider="Azure AI Language", model="Conversation PII",
                wall_seconds=pii_seconds,
                metrics={
                    "entity_count": len(entities),
                    "input_characters": input_characters,
                },
            ),
            "summarizer_endpoint": stage_result(
                "succeeded", provider="Azure AI Language", model="Conversational Summarization",
                wall_seconds=summary_seconds,
                metrics={
                    "input_characters": input_characters,
                    "output_characters": len(raw_summary),
                },
            ),
            "transcript_redaction": stage_result(
                "succeeded", provider="local", model="typed entity replacement",
                wall_seconds=transcript_redaction_seconds,
                metrics={"entity_count": len(entities)},
            ),
            "summary_sanitization": stage_result(
                "succeeded", provider="local", model="typed entity replacement",
                wall_seconds=sanitization_seconds, metrics={"replacement_count": replacements},
            ),
        }
        return architecture_result(
            architecture_id=self.architecture_id,
            label=self.label,
            source_entry=source_entry,
            redacted_conversation=redacted,
            summary=summary,
            entities=entities,
            stages=stages,
            downstream_wall_seconds=downstream_seconds,
        )

    @staticmethod
    def _result_conversation(results: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
        conversations = results.get("conversations")
        if not isinstance(conversations, list) or len(conversations) != 1 or not isinstance(conversations[0], Mapping):
            raise ValueError(f"Azure Language {operation} results must contain one conversation.")
        return conversations[0]

    @classmethod
    def _parse_entities(
        cls, results: Mapping[str, Any], source: Mapping[str, Any]
    ) -> list[PiiEntity]:
        result = cls._result_conversation(results, "PII")
        items = result.get("conversationItems")
        if not isinstance(items, list):
            raise ValueError("Azure Language PII results omitted conversationItems.")
        source_items = {str(item.get("id")): item for item in source.get("conversationItems", []) if isinstance(item, Mapping)}
        entities: list[PiiEntity] = []
        for item in items:
            if not isinstance(item, Mapping) or not isinstance(item.get("entities"), list):
                raise ValueError("Azure Language PII returned a malformed conversation item.")
            turn_id = str(item.get("id", ""))
            if turn_id not in source_items:
                raise ValueError(f"Azure Language PII returned unknown turn {turn_id!r}.")
            source_text = str(source_items[turn_id].get("text", ""))
            for value in item["entities"]:
                if not isinstance(value, Mapping):
                    raise ValueError("Azure Language PII returned a malformed entity.")
                try:
                    entity = PiiEntity(
                        category=str(value["category"]), text=str(value["text"]),
                        turn_id=turn_id, offset=int(value["offset"]), length=int(value["length"]),
                        confidence=float(value["confidenceScore"]) if value.get("confidenceScore") is not None else None,
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("Azure Language PII entity omitted required fields.") from error
                if not entity.category or source_text[entity.offset:entity.offset + entity.length] != entity.text:
                    raise ValueError(f"Azure Language PII entity does not match turn {turn_id}.")
                entities.append(entity)
        return entities

    @classmethod
    def _parse_summary(cls, results: Mapping[str, Any]) -> str:
        result = cls._result_conversation(results, "summarization")
        summaries = result.get("summaries")
        if not isinstance(summaries, list) or not summaries:
            raise ValueError("Azure Language summarization results omitted summaries.")
        parts: list[str] = []
        for summary in summaries:
            if not isinstance(summary, Mapping) or not isinstance(summary.get("text"), str):
                raise ValueError("Azure Language returned a malformed summary.")
            text = summary["text"].strip()
            if text:
                parts.append(text)
        if not parts:
            raise ValueError("Azure Language returned an empty summary.")
        return "\n".join(parts)
