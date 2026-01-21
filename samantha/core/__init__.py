"""Core module for Samantha."""

from .state import (
    _samantha_thread,
    _thread_stop_flag,
    _thread_ready,
    _tts_done_event,
)

from .loop import (
    samantha_loop_thread,
    VAD_AVAILABLE,
)

__all__ = [
    # State globals
    "_samantha_thread",
    "_thread_stop_flag",
    "_thread_ready",
    "_tts_done_event",
    # Loop
    "samantha_loop_thread",
    "VAD_AVAILABLE",
]
