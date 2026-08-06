"""DeepSeek summary adapter for canonical MAI STT output."""

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
from backend.contracts import stage_result, stt_stage, summary_only_architecture_result

ARCHITECTURE_ID = "architecture-2-mai-realtime-deepseek"
STT_ENGINE_KEY = "architecture-2-mai-transcribe-realtime"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

_HINT_PATTERNS = {
    "EMAIL": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "PHONE_NUMBER": re.compile(r"(?<!\w)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?!\w)"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    "ACCOUNT_NUMBER": re.compile(
        r"(?<![A-Z0-9])(?:[A-Z]{2}[ -]?)?\d{4}[ -]?\d{4}(?![A-Z0-9])",
        re.I,
    ),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "DATE_OF_BIRTH": re.compile(r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b"),
}
_TYPED_PLACEHOLDER = re.compile(r"\[([A-Z][A-Z0-9]*(?:[ _-][A-Z0-9]+)*)\]")


def compact_summary_conversation(
    conversation: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Project canonical turns to compact participant/text segments for summarization."""
    projected: list[dict[str, str]] = []
    for item in conversation.get("conversationItems", []):
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        participant = str(item.get("participantId", "")).strip() or "UNKNOWN"
        if projected and projected[-1]["participant"] == participant:
            projected[-1]["text"] = f"{projected[-1]['text']} {text}"
        else:
            projected.append({"participant": participant, "text": text})
    return projected


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
        "name": "pii_safe_call_summary",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary"],
            "properties": {
                "summary": {"type": "string"},
            },
        },
    },
}


def sanitize_summary_with_hints(
    summary: str, hints: list[dict[str, Any]]
) -> tuple[str, int]:
    """Replace locally detected candidate literals in a model-generated summary."""
    sanitized = summary
    replacements = 0
    unique = {
        (str(hint["text"]).casefold(), str(hint["text"]), str(hint["category"]))
        for hint in hints
        if str(hint.get("text", "")).strip()
    }
    for _, literal, category in sorted(
        unique, key=lambda value: len(value[1]), reverse=True
    ):
        prefix = r"(?<!\w)" if literal[0].isalnum() or literal[0] == "_" else ""
        suffix = r"(?!\w)" if literal[-1].isalnum() or literal[-1] == "_" else ""
        placeholder = f"[{'_'.join(category.upper().replace('-', ' ').split()) or 'PII'}]"
        sanitized, count = re.subn(
            prefix + re.escape(literal) + suffix,
            placeholder,
            sanitized,
            flags=re.IGNORECASE,
        )
        replacements += count
    sanitized = _TYPED_PLACEHOLDER.sub(
        lambda match: "[" + "_".join(match.group(1).replace("-", " ").split()) + "]",
        sanitized,
    )
    return sanitized, replacements


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
        downstream_started = self.clock()

        started = self.clock()
        regex_hints = detect_regex_hints(source_snapshot)
        regex_seconds = self.clock() - started

        started = self.clock()
        system_prompt = self.system_prompt_path.read_text()
        compact_conversation = compact_summary_conversation(source_snapshot)
        user_content = json.dumps(
            {
                "instruction": (
                    "Return only JSON matching the supplied response schema with one concise, "
                    "PII-safe call summary of 80-120 words. Replace every PII value with a typed "
                    "[CATEGORY] placeholder such as [PERSON], [PHONE_NUMBER], or "
                    "[DATE_OF_BIRTH]. Do not reproduce the conversation, individual turns, "
                    "speaker labels, or any raw PII."
                ),
                "conversation": compact_conversation,
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
        chat_url = _chat_url(self.endpoint, self.deployment)
        request_kwargs = {
            "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            "json": body,
            "timeout": self.timeout,
        }
        api_version_params = _api_version_params(chat_url)
        if api_version_params is not None:
            request_kwargs["params"] = api_version_params
        preparation_seconds = self.clock() - started
        validation_error: ValueError | None = None
        input_tokens = 0
        output_tokens = 0
        api_seconds = 0.0
        validation_seconds = 0.0
        for attempt in range(1, self.max_attempts + 1):
            started = self.clock()
            response = self.http_post(chat_url, **request_kwargs)
            api_seconds += self.clock() - started
            response.raise_for_status()
            started = self.clock()
            try:
                response_payload = response.json()
                usage = response_payload.get("usage", {}) if isinstance(response_payload, Mapping) else {}
                if isinstance(usage, Mapping):
                    input_tokens += int(usage.get("prompt_tokens", 0) or 0)
                    output_tokens += int(usage.get("completion_tokens", 0) or 0)
                payload = self._parse_response(response_payload)
            except ValueError as exc:
                validation_error = exc
            else:
                validation_seconds += self.clock() - started
                break
            validation_seconds += self.clock() - started
        else:
            assert validation_error is not None
            raise validation_error

        started = self.clock()
        summary, replacements = sanitize_summary_with_hints(
            payload["summary"], regex_hints
        )
        sanitization_seconds = self.clock() - started
        downstream_seconds = self.clock() - downstream_started
        measured_seconds = (
            regex_seconds
            + preparation_seconds
            + api_seconds
            + validation_seconds
            + sanitization_seconds
        )
        backend_overhead_seconds = max(0.0, downstream_seconds - measured_seconds)
        if source_snapshot != source:
            raise RuntimeError("Source conversation was mutated during processing.")

        stages = {
            "stt": stt_stage(source_entry),
            "regex_detection": stage_result(
                "succeeded", provider="local", model="deterministic PII candidates",
                wall_seconds=regex_seconds,
                metrics={"candidate_count": len(regex_hints)},
            ),
            "request_preparation": stage_result(
                "succeeded", provider="backend + Microsoft Entra ID",
                model="prompt serialization and token acquisition",
                wall_seconds=preparation_seconds,
                metrics={
                    "source_turn_count": len(source_snapshot["conversationItems"]),
                    "projected_segment_count": len(compact_conversation),
                    "user_content_characters": len(user_content),
                },
            ),
            "llm_api_call": stage_result(
                "succeeded", provider="Azure AI Foundry", model=self.deployment,
                wall_seconds=api_seconds,
                metrics={
                    "summary_only": True,
                    "output_semantics": "pii_safe_summary_only",
                    "attempts": attempt,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
            ),
            "response_validation": stage_result(
                "succeeded", provider="local",
                model="strict summary-only JSON validation",
                wall_seconds=validation_seconds,
            ),
            "summary_sanitization": stage_result(
                "succeeded", provider="local",
                model="regex-candidate literal replacement",
                wall_seconds=sanitization_seconds,
                metrics={"replacement_count": replacements},
            ),
            "backend_overhead": stage_result(
                "succeeded", provider="local",
                model="thread scheduling and uninstrumented orchestration",
                wall_seconds=backend_overhead_seconds,
            ),
        }
        return summary_only_architecture_result(
            architecture_id=architecture_id, label=label,
            source_entry=source_entry, summary=summary, stages=stages,
            downstream_wall_seconds=downstream_seconds,
        )

    @staticmethod
    def _parse_response(raw: Any) -> dict[str, Any]:
        try:
            content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("DeepSeek returned malformed structured JSON.") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"summary"}:
            raise ValueError("DeepSeek response must contain only summary.")
        if not isinstance(parsed["summary"], str):
            raise ValueError("DeepSeek response summary must be a string.")
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
