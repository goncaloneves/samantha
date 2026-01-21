"""Utility functions for Samantha."""

from .text import (
    normalize_text,
    check_for_wake_word,
    check_for_stop_phrase,
    check_for_deactivation,
    clean_command,
    contains_trigger_word,
    sanitize_whisper_text,
    is_noise,
)

from .logging import (
    log_conversation,
    get_persona,
)

__all__ = [
    "normalize_text",
    "check_for_wake_word",
    "check_for_stop_phrase",
    "check_for_deactivation",
    "clean_command",
    "contains_trigger_word",
    "sanitize_whisper_text",
    "is_noise",
    "log_conversation",
    "get_persona",
]
