"""Interface and registry constants for end-to-end architecture adapters."""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class ArchitectureAdapter(Protocol):
    architecture_id: str
    label: str
    stt_engine_key: str

    def run(self, source_entry: Mapping[str, Any]) -> dict[str, Any]: ...


ARCHITECTURE_LABELS = {
    "architecture-1-azure-language": "1. Azure Speech + Azure Language",
    "architecture-2-mai-realtime-deepseek": "2. MAI real-time + DeepSeek",
    "architecture-3-mai-batch-deepseek": "3. MAI batch + DeepSeek",
}
