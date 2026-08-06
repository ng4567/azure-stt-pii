"""Architecture 2: MAI real-time transcription with DeepSeek processing."""

from .adapter import (
    Architecture2Adapter,
    DeepSeekProcessor,
    compact_summary_conversation,
)

__all__ = [
    "Architecture2Adapter",
    "DeepSeekProcessor",
    "compact_summary_conversation",
]
