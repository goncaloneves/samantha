"""Configuration settings for Samantha voice assistant."""

import logging
import os

from .constants import (
    CONFIG_FILE,
    DEFAULT_WAKE_WORDS,
    DEFAULT_DEACTIVATION_PHRASES,
    DEFAULT_AI_PROCESS_PATTERN,
    DEFAULT_AI_WINDOW_TITLES,
)

logger = logging.getLogger("samantha")


def load_config() -> dict:
    """Load configuration from ~/.samantha/config.json if it exists."""
    if CONFIG_FILE.exists():
        try:
            import json
            return json.loads(CONFIG_FILE.read_text())
        except Exception as e:
            logger.warning("Failed to load config: %s", e)
    return {}


def get_config(key: str, default=None):
    """Get config value from file, falling back to env var, then default."""
    config = load_config()
    if key in config:
        return config[key]
    env_key = f"SAMANTHA_{key.upper()}"
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val
    return default


def get_voice() -> str:
    return get_config("voice", "af_aoede")


def get_input_device():
    """Get configured input device, or system default."""
    val = get_config("input_device")
    if val is not None and val != "null":
        int_val = int(val)
        return int_val if int_val != -1 else None
    return None


def get_output_device():
    """Get configured output device, or system default."""
    val = get_config("output_device")
    if val is not None and val != "null":
        int_val = int(val)
        return int_val if int_val != -1 else None
    return None


def get_restore_focus() -> bool:
    val = get_config("restore_focus", "true")
    if isinstance(val, bool):
        return val
    return str(val).lower() == "true"


def get_theodore_mode() -> bool:
    """Check if Theodore mode is enabled (call user Theodore from the movie Her)."""
    val = get_config("theodore", "true")
    if isinstance(val, bool):
        return val
    return str(val).lower() == "true"


def get_min_audio_energy() -> int:
    """Get minimum audio energy threshold for Whisper.

    Filters out low-energy audio before sending to Whisper to prevent hallucinations.
    Whisper can hallucinate phrases like "Thank you for watching" on silence/noise.

    Recommended values (16-bit PCM, max 32768):
    - 1500: Balanced, catches normal speech while filtering silence (default)
    - 3000: Conservative, filters typing but may miss quiet speech
    - 5000: Very conservative, requires clearer/louder speech

    Lower values needed for: headphones with mic, quiet environments
    Higher values needed for: laptop mic, noisy environments, keyboard nearby
    """
    val = get_config("min_audio_energy", 1500)
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        return 1500


def get_wake_words() -> list:
    config_words = get_config("wake_words")
    if config_words:
        if isinstance(config_words, list):
            return [w.lower() for w in config_words]
        return [w.strip().lower() for w in config_words.split(",")]
    return DEFAULT_WAKE_WORDS


def get_deactivation_phrases() -> list:
    config_phrases = get_config("deactivation_words")
    if config_phrases:
        if isinstance(config_phrases, list):
            return [p.lower() for p in config_phrases]
        return [p.strip().lower() for p in config_phrases.split(",")]
    return DEFAULT_DEACTIVATION_PHRASES


def get_target_app() -> str | None:
    """Get user's preferred target app for injection.

    If set, Samantha will inject into this app instead of auto-detecting.
    Must be one of: Cursor, Code, Windsurf, Claude, Terminal, iTerm2, etc.

    Returns None if not set (auto-detect mode).
    """
    val = get_config("target_app")
    if val and isinstance(val, str) and val.strip():
        return val.strip()
    return None


def get_injection_mode() -> str:
    """Get injection mode: 'auto', 'extension', 'cli', 'terminal', or 'desktop'.

    - auto (default): Try IDE first, then desktop app, then standalone terminal
    - extension: Use Cmd+Escape to focus Claude Code extension input
    - cli: Focus IDE's integrated terminal (Ctrl+`) for Claude CLI usage
    - terminal: Only use standalone terminal apps (Terminal, iTerm2, Warp, etc.)
    - desktop: Use Claude Desktop app directly (activate window, paste, enter)

    Returns 'auto' by default.
    """
    val = get_config("injection_mode", "auto")
    if isinstance(val, str) and val.strip().lower() in ("auto", "extension", "cli", "terminal", "desktop"):
        return val.strip().lower()
    return "auto"


def get_ai_process_pattern() -> str:
    """Get AI process detection pattern (regex).

    Used to detect running AI CLI processes (Claude, Gemini, Copilot, etc.).
    Pattern is used with grep -E for process matching.

    Default: claude|gemini|copilot|aider|chatgpt|gpt|sgpt|codex
    """
    val = get_config("ai_process_pattern", DEFAULT_AI_PROCESS_PATTERN)
    if isinstance(val, str) and val.strip():
        return val.strip().lower()
    return DEFAULT_AI_PROCESS_PATTERN


def get_ai_window_titles() -> list[str]:
    """Get AI window titles to search for.

    Used to find terminal windows running AI CLIs by matching window titles.
    """
    val = get_config("ai_window_titles", None)
    if isinstance(val, list):
        return [t.lower() for t in val if isinstance(t, str)]
    return DEFAULT_AI_WINDOW_TITLES
