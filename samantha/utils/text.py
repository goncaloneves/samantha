"""Text processing utilities for Samantha."""

import re
from typing import Optional

from samantha.config import (
    WHISPER_SOUND_PATTERN,
    INTERRUPT_WORDS,
    SKIP_WORDS,
    get_wake_words,
    get_stop_phrases,
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
    return any(phrase in text_clean for phrase in get_stop_phrases())


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

    phrases = get_stop_phrases()
    escaped = [r'\s+'.join(re.escape(w) for w in p.split()) for p in phrases]
    stop_phrases_pattern = r'(' + '|'.join(escaped) + r')'
    match = re.search(stop_phrases_pattern, cleaned, flags=re.IGNORECASE)
    if match:
        cleaned = cleaned[:match.end()]

    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def contains_trigger_word(text: str) -> bool:
    """Check if text contains a trigger word from the active profile's wake words."""
    if not text:
        return False
    text_lower = text.lower()
    prefixes = {"hey", "hi", "hello", "ok", "okay", "a", "the", "yo"}
    trigger_words = set()
    for wake_word in get_wake_words():
        for word in wake_word.split():
            if word not in prefixes and len(word) >= 3:
                trigger_words.add(word)
    for word in trigger_words:
        pattern = r'\b' + re.escape(word) + r'\b'
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

    all_keywords = get_wake_words() + get_stop_phrases() + get_deactivation_phrases() + INTERRUPT_WORDS + SKIP_WORDS
    for keyword in all_keywords:
        if keyword in sanitized:
            return False

    noise_phrases = [
        'thank you for watching',
        'look at the next video',
    ]
    for phrase in noise_phrases:
        if phrase in sanitized:
            return True

    noise_words = [
        'click', 'clap', 'ding', 'bell', 'tick', 'thud', 'bang',
        'engine', 'revving', 'keyboard', 'typing', 'noise',
        'silence', 'static', 'hum', 'buzz', 'music',
    ]
    if sanitized in noise_words:
        return True

    return False
