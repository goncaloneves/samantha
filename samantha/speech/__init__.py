"""Speech module for Samantha."""

from .stt import (
    transcribe_audio,
    transcribe_audio_sync,
)

__all__ = [
    "transcribe_audio",
    "transcribe_audio_sync",
]
