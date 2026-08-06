"""DeepSeek redaction and summarization adapter for canonical MAI STT output."""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlparse

import requests
from azure.identity import DefaultAzureCredential

from backend.app import config
from backend.architecture import ARCHITECTURE_LABELS
from backend.contracts import architecture_result, stage_result, stt_stage
from backend.redaction import (
    apply_entities,
    entities_from_redacted_conversation,
    sanitize_summary,
)

ARCHITECTURE_ID = "architecture-2-mai-realtime-deepseek"
STT_ENGINE_KEY = "architecture-2-mai-transcribe-realtime"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

_HINT_PATTERNS = {
    "EMAIL": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "PHONE_NUMBER": re.compile(r"(?<!\w)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?!\w)"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "DATE_OF_BIRTH": re.compile(r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b"),
}


def detect_regex_hints(conversation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic candidate spans; DeepSeek makes the final PII decision."""
    hints: list[dict[str, Any]] = []
    for item in conversation.get("conversationItems", []):
        text = str(item.get("text", ""))
        for category, pattern in _HINT_PATTERNS.items():
            for match in pattern.finditer(text):
                hints.append(
                    {
                        "category": category,
                        "turnId": str(item.get("id", "")),
                        "offset": match.start(),
                        "length": len(match.group()),
                        "text": match.group(),
                    }
                )
    return hints


def _chat_url(endpoint: str, deployment: str) -> str:
    if not endpoint or not deployment:
        raise ValueError("AZURE_FOUNDRY_ENDPOINT and AZURE_FOUNDRY_DEPLOYMENT are required.")
    endpoint = endpoint.rstrip("/").replace("{deployment}", quote(deployment, safe=""))
    path = urlparse(endpoint).path.rstrip("/").lower()
    if path.endswith("/chat/completions"):
        return endpoint
    if path.endswith("/openai/v1"):
        return endpoint + "/chat/completions"
    if "/openai/deployments/" in path:
        return endpoint + "/chat/completions"
    if path.endswith("/models") or urlparse(endpoint).hostname and ".models.ai.azure.com" in urlparse(endpoint).hostname:
        return endpoint + "/chat/completions"
    return endpoint + f"/openai/deployments/{quote(deployment, safe='')}/chat/completions"


def _api_version_params(chat_url: str) -> dict[str, str] | None:
    parsed = urlparse(chat_url)
    path = parsed.path.rstrip("/").lower()
    if "/openai/v1/" in path or (parsed.hostname and ".models.ai.azure.com" in parsed.hostname):
        return None
    return {"api-version": config.AZURE_FOUNDRY_API_VERSION}


_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "redacted_call",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "conversation"],
            "properties": {
                "summary": {"type": "string"},
                "conversation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["conversationItems"],
                    "properties": {
                        "conversationItems": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["id", "participantId", "channel", "offset", "duration", "text"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "participantId": {"type": "string"},
                                    "channel": {"type": "integer"},
                                    "offset": {"type": "integer"},
                                    "duration": {"type": "integer"},
                                    "text": {"type": "string"},
                                },
                            },
                        }
                    },
                },
            },
        },
    },
}


class DeepSeekProcessor:
    """Reusable Foundry processor shared by DeepSeek-based architectures."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        deployment: str | None = None,
        system_prompt_path: str | Path | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
        credential: Any | None = None,
        http_post: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.endpoint = config.AZURE_FOUNDRY_ENDPOINT if endpoint is None else endpoint.rstrip("/")
        self.deployment = config.AZURE_FOUNDRY_DEPLOYMENT if deployment is None else deployment
        self.system_prompt_path = Path(system_prompt_path or config.SYSTEM_PROMPT_PATH)
        self.timeout = config.AZURE_REQUEST_TIMEOUT_SECONDS if timeout is None else timeout
        self.max_attempts = config.AZURE_FOUNDRY_MAX_ATTEMPTS if max_attempts is None else max_attempts
        if self.max_attempts < 1:
            raise ValueError("Foundry max attempts must be at least 1.")
        self.credential = credential
        self.http_post = http_post or requests.post
        self.clock = clock

    def run(
        self,
        source_entry: Mapping[str, Any],
        architecture_id: str,
        label: str,
    ) -> dict[str, Any]:
        source = source_entry.get("conversation")
        if not isinstance(source, Mapping) or not isinstance(source.get("conversationItems"), list):
            raise ValueError("The STT stage did not produce a canonical conversation.")

        source_snapshot = deepcopy(dict(source))
        system_prompt = self.system_prompt_path.read_text()
        user_content = json.dumps(
            {
                "instruction": (
                    "Return JSON matching the supplied response schema. Redact every PII value "
                    "with a typed [CATEGORY] placeholder, preserve every turn's id, participantId, "
                    "channel, offset, and duration exactly, and summarize only the redacted content."
                ),
                "canonicalConversation": source_snapshot,
                "regexCandidateHints": detect_regex_hints(source_snapshot),
            },
            ensure_ascii=False,
        )
        credential = self.credential or DefaultAzureCredential()
        token = credential.get_token(TOKEN_SCOPE).token
        body = {
            "model": self.deployment,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": _RESPONSE_SCHEMA,
            "temperature": 0,
        }
        started = self.clock()
        chat_url = _chat_url(self.endpoint, self.deployment)
        request_kwargs = {
            "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            "json": body,
            "timeout": self.timeout,
        }
        api_version_params = _api_version_params(chat_url)
        if api_version_params is not None:
            request_kwargs["params"] = api_version_params
        validation_error: ValueError | None = None
        input_tokens = 0
        output_tokens = 0
        for attempt in range(1, self.max_attempts + 1):
            response = self.http_post(chat_url, **request_kwargs)
            response.raise_for_status()
            response_payload = response.json()
            usage = response_payload.get("usage", {}) if isinstance(response_payload, Mapping) else {}
            if isinstance(usage, Mapping):
                input_tokens += int(usage.get("prompt_tokens", 0) or 0)
                output_tokens += int(usage.get("completion_tokens", 0) or 0)
            try:
                payload = self._parse_response(response_payload, source_snapshot)
                entities = entities_from_redacted_conversation(
                    source_snapshot, payload["conversation"]
                )
                break
            except ValueError as exc:
                validation_error = exc
        else:
            assert validation_error is not None
            raise validation_error
        elapsed = self.clock() - started
        redacted = apply_entities(source_snapshot, entities)
        summary, replacements = sanitize_summary(payload["summary"], entities)
        if source_snapshot != source:
            raise RuntimeError("Source conversation was mutated during processing.")

        stages = {
            "stt": stt_stage(source_entry),
            "pii_redaction": stage_result(
                "succeeded", provider="Azure AI Foundry", model=self.deployment,
                wall_seconds=elapsed,
                metrics={
                    "regex_hint_count": len(detect_regex_hints(source_snapshot)),
                    "entity_count": len(entities),
                    "attempts": attempt,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
            ),
            "summarization": stage_result(
                "succeeded", provider="Azure AI Foundry", model=self.deployment,
                wall_seconds=elapsed, metrics={"combined_request": True},
            ),
            "summary_sanitization": stage_result(
                "succeeded", provider="local", model="deterministic-literal-replacement",
                wall_seconds=0.0, metrics={"replacement_count": replacements},
            ),
        }
        return architecture_result(
            architecture_id=architecture_id, label=label,
            source_entry=source_entry, redacted_conversation=redacted,
            summary=summary, entities=entities, stages=stages,
        )

    @staticmethod
    def _parse_response(raw: Any, source: Mapping[str, Any]) -> dict[str, Any]:
        try:
            content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("DeepSeek returned malformed structured JSON.") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"summary", "conversation"}:
            raise ValueError("DeepSeek response must contain only summary and conversation.")
        if not isinstance(parsed["summary"], str) or not isinstance(parsed["conversation"], dict):
            raise ValueError("DeepSeek response has invalid summary or conversation types.")
        returned_items = parsed["conversation"].get("conversationItems")
        source_items = source.get("conversationItems")
        if set(parsed["conversation"]) != {"conversationItems"}:
            raise ValueError("DeepSeek conversation contains unexpected fields.")
        if not isinstance(returned_items, list) or len(returned_items) != len(source_items):
            raise ValueError("DeepSeek response does not preserve the conversation turns.")
        metadata = ("id", "participantId", "channel", "offset", "duration")
        for original, returned in zip(source_items, returned_items, strict=True):
            if not isinstance(returned, dict) or not isinstance(returned.get("text"), str):
                raise ValueError("DeepSeek returned an invalid conversation item.")
            if set(returned) != {*metadata, "text"}:
                raise ValueError("DeepSeek conversation item contains unexpected fields.")
            if any(returned.get(key) != original.get(key) for key in metadata):
                raise ValueError("DeepSeek changed canonical conversation metadata.")
        return parsed


class Architecture2Adapter:
    architecture_id = ARCHITECTURE_ID
    label = ARCHITECTURE_LABELS[ARCHITECTURE_ID]
    stt_engine_key = STT_ENGINE_KEY

    def __init__(
        self,
        *,
        processor: DeepSeekProcessor | None = None,
        endpoint: str | None = None,
        deployment: str | None = None,
        system_prompt_path: str | Path | None = None,
        timeout: float | None = None,
        credential: Any | None = None,
        http_post: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._processor = processor or DeepSeekProcessor(
            endpoint=endpoint,
            deployment=deployment,
            system_prompt_path=system_prompt_path,
            timeout=timeout,
            credential=credential,
            http_post=http_post,
            clock=clock,
        )

    def run(self, source_entry: Mapping[str, Any]) -> dict[str, Any]:
        return self._processor.run(
            source_entry,
            architecture_id=self.architecture_id,
            label=self.label,
        )
