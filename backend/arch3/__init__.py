"""Architecture 3: MAI batch transcription with DeepSeek downstream processing."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from backend import arch2
from backend.architecture import ARCHITECTURE_LABELS
from backend.contracts import stt_stage

ARCHITECTURE_ID = "architecture-3-mai-batch-deepseek"
STT_ENGINE_KEY = "architecture-3-mai-transcribe-batch"


class Architecture3Adapter:
    architecture_id = ARCHITECTURE_ID
    label = ARCHITECTURE_LABELS[architecture_id]
    stt_engine_key = STT_ENGINE_KEY

    def __init__(self, processor: Any | None = None) -> None:
        self._processor = processor or arch2.DeepSeekProcessor()

    def run(self, source_entry: Mapping[str, Any]) -> dict[str, Any]:
        result = self._processor.run(
            source_entry,
            architecture_id=self.architecture_id,
            label=self.label,
        )
        if not isinstance(result, Mapping):
            raise TypeError("DeepSeekProcessor.run() must return an architecture result.")

        adapted = deepcopy(dict(result))
        stages = dict(adapted.get("stages") or {})
        stages["stt"] = stt_stage(source_entry)
        adapted["stages"] = stages
        return adapted


Adapter = Architecture3Adapter

__all__ = [
    "ARCHITECTURE_ID",
    "STT_ENGINE_KEY",
    "Adapter",
    "Architecture3Adapter",
]
