"""Audio module for Samantha."""

from .recording import (
    normalize_audio,
    _prepare_audio_for_whisper,
    _clear_queue,
)

from .playback import (
    _tts_text_queue,
    _tts_queue_lock,
    _last_tts_text,
    _last_tts_time,
    _tts_playing,
    _tts_start_time,
    _tts_interrupt,
    speak_tts_sync,
    speak_tts,
    play_sound,
)

from .processing import (
    is_echo,
    get_active_interrupt_words,
    is_skip_allowed,
    contains_interrupt_phrase,
    contains_skip_phrase,
)

__all__ = [
    # Recording
    "normalize_audio",
    "_prepare_audio_for_whisper",
    "_clear_queue",
    # Playback globals
    "_tts_text_queue",
    "_tts_queue_lock",
    "_last_tts_text",
    "_last_tts_time",
    "_tts_playing",
    "_tts_start_time",
    "_tts_interrupt",
    # Playback functions
    "speak_tts_sync",
    "speak_tts",
    "play_sound",
    # Processing
    "is_echo",
    "get_active_interrupt_words",
    "is_skip_allowed",
    "contains_interrupt_phrase",
    "contains_skip_phrase",
]
