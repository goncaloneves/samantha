"""Text processing utilities for Samantha."""

import re
from typing import Optional

from samantha.config import (
    WHISPER_SOUND_PATTERN,
    STOP_PHRASES,
    DEFAULT_WAKE_WORDS,
    DEFAULT_DEACTIVATION_PHRASES,
    INTERRUPT_WORDS,
    SKIP_WORDS,
    get_wake_words,
    get_deactivation_phrases,
)


def normalize_text(text: str) -> str:
    """Normalize text by removing punctuation and converting to lowercase."""
    if not text:
        return ""
    import string
    return text.lower().translate(str.maketrans('', '', string.punctuation)).strip()


def check_for_wake_word(text: str) -> Optional[str]:
    if not text:
        return None
    text_clean = normalize_text(text)
    for wake_word in get_wake_words():
        if wake_word in text_clean:
            return wake_word
    return None


def check_for_stop_phrase(text: str) -> bool:
    if not text:
        return False
    text_clean = normalize_text(text)
    return any(phrase in text_clean for phrase in STOP_PHRASES)


def check_for_deactivation(text: str) -> bool:
    """Check if text contains a deactivation phrase to stop active listening."""
    if not text:
        return False
    text_clean = normalize_text(text)
    return any(phrase in text_clean for phrase in get_deactivation_phrases())


def clean_command(text: str) -> str:
    """Clean recorded command text. Removes content BEFORE wake word, Whisper metadata, and anything AFTER stop phrases."""
    cleaned = WHISPER_SOUND_PATTERN.sub('', text).strip()

    for wake_word in sorted(get_wake_words(), key=len, reverse=True):
        pattern = r'[^\w]*'.join(re.escape(w) for w in wake_word.split())
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            cleaned = cleaned[match.start():].strip()
            break

    stop_phrases_pattern = r'(stop\s+recording|end\s+recording|finish\s+recording|that\s+is\s+all|that\'s\s+all|thats\s+all|over\s+and\s+out|over\s+out|send\s+message|send\s+it|samantha\s+stop|samantha\s+send|samantha\s+done)'
    match = re.search(stop_phrases_pattern, cleaned, flags=re.IGNORECASE)
    if match:
        cleaned = cleaned[:match.end()]

    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def contains_trigger_word(text: str) -> bool:
    """Check if text contains a trigger word (Samantha)."""
    if not text:
        return False
    import re
    text_lower = text.lower()
    triggers = [
        r'\bsamantha\b', r'\bsamanta\b', r'\bcemantha\b', r'\bsomantha\b', r'\bsemantha\b',
        r'\bsemante\b', r'\bsamante\b', r'\bsam\b'
    ]
    for pattern in triggers:
        if re.search(pattern, text_lower):
            return True
    return False


def sanitize_whisper_text(text: str) -> str:
    """Clean Whisper transcription by removing metadata and hallucinations.

    Removes:
    - Bracketed sounds: [Music], [Applause], [BLANK_AUDIO], etc.
    - Parenthesized sounds: (coughing), (laughing), etc.
    - Musical notes: ♪♪♪
    - Extra whitespace and special punctuation

    Returns the cleaned dialogue text.
    """
    if not text:
        return ""

    cleaned = WHISPER_SOUND_PATTERN.sub('', text)
    cleaned = re.sub(r"[^\w\s']", '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().lower()

    return cleaned


def is_noise(text: str) -> bool:
    """Check if transcription is just background noise, not speech."""
    if not text:
        return True

    sanitized = sanitize_whisper_text(text)

    if not sanitized or len(sanitized) < 3:
        return True

    all_keywords = DEFAULT_WAKE_WORDS + STOP_PHRASES + DEFAULT_DEACTIVATION_PHRASES + INTERRUPT_WORDS + SKIP_WORDS
    for keyword in all_keywords:
        if keyword in sanitized:
            return False

    noise_words = [
        'click', 'clap', 'ding', 'bell', 'tick', 'thud', 'bang',
        'engine', 'revving', 'keyboard', 'typing', 'noise',
        'blank audio', 'silence', 'static', 'hum', 'buzz',
        'music', 'music playing', 'clock ticking',
        'thank you for watching', 'thanks for watching',
        'subscribe', 'like and subscribe',
    ]
    if sanitized in noise_words:
        return True

    return False
