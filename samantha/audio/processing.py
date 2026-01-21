"""Audio processing utilities for Samantha."""

import logging
import time

from samantha.config import INTERRUPT_WORDS, SKIP_WORDS
import samantha.audio.playback as playback

logger = logging.getLogger("samantha")


def is_echo(text: str) -> bool:
    """Check if transcription is likely echo from TTS playback."""
    _last_tts_text = playback._last_tts_text
    _last_tts_time = playback._last_tts_time
    if not _last_tts_text or not text:
        return False
    if time.time() - _last_tts_time > 10:
        return False

    text_lower = text.lower().strip()
    tts_lower = _last_tts_text.lower().strip()

    if text_lower in tts_lower or tts_lower in text_lower:
        return True

    text_words = set(text_lower.split())
    tts_words = set(tts_lower.split())
    if len(text_words) > 3:
        overlap = len(text_words & tts_words) / len(text_words)
        if overlap > 0.5:
            return True

    return False


def get_active_interrupt_words() -> list:
    """Get interrupt words that are NOT in the current TTS text.

    If TTS says "stop", only "quiet" works.
    If TTS says "quiet", only "stop" works.
    If neither, both work.
    """
    _last_tts_text = playback._last_tts_text
    tts_lower = _last_tts_text.lower() if _last_tts_text else ""

    return [word for word in INTERRUPT_WORDS if word not in tts_lower]


def is_skip_allowed() -> bool:
    """Check if skip words are allowed based on current TTS text.

    If TTS contains a skip word, don't allow skip detection to avoid self-triggering.
    """
    _last_tts_text = playback._last_tts_text
    if not _last_tts_text:
        return True
    tts_lower = _last_tts_text.lower()
    return not any(word in tts_lower for word in SKIP_WORDS)


def contains_interrupt_phrase(text: str) -> bool:
    """Check if text contains an active interrupt word. Expects pre-sanitized text.

    If a word appears twice (e.g., "stop stop"), it always works as an interrupt,
    even if that word is in the TTS text. This ensures users can always interrupt.
    """
    if not text:
        return False

    words = text.split()

    for word in INTERRUPT_WORDS:
        word_count = words.count(word)
        if word_count >= 2:
            return True

    active_words = get_active_interrupt_words()
    for word in active_words:
        if word in words:
            return True
    return False


def contains_skip_phrase(text: str) -> bool:
    """Check if text contains a skip word. Expects pre-sanitized text."""
    if not text:
        return False
    if not is_skip_allowed():
        return False

    for word in SKIP_WORDS:
        if word in text.split():
            return True
    return False
