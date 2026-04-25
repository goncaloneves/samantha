"""Configuration settings for Samantha voice assistant."""

import logging
import os

from .constants import (
    CONFIG_FILE,
    STOP_PHRASES,
    DEFAULT_WAKE_WORDS,
    DEFAULT_DEACTIVATION_PHRASES,
    DEFAULT_AI_PROCESS_PATTERN,
    DEFAULT_AI_WINDOW_TITLES,
)
from .profiles import PROFILES, DEFAULT_PROFILE

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


def get_profile_name() -> str:
    """Get active profile name: 'samantha', 'jarvis', etc."""
    val = get_config("profile", DEFAULT_PROFILE)
    if isinstance(val, str) and val.strip().lower() in PROFILES:
        return val.strip().lower()
    return DEFAULT_PROFILE


def get_profile() -> dict:
    """Get the active profile definition."""
    return PROFILES[get_profile_name()]


def get_voice() -> str:
    profile = get_profile()
    return get_config("voice", profile["voice"])


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
    """Check if Theodore mode is enabled (call user Theodore from the movie Her).

    For backward compatibility. Prefer get_user_names() for profile-aware usage.
    """
    val = get_config("theodore", "true")
    if isinstance(val, bool):
        return val
    return str(val).lower() == "true"


def get_user_names() -> list[str]:
    """Get the in-character forms of address for the user.

    Returns a list so the LLM can vary naturally (e.g. samantha:
    ["Theodore", "you", "love"]). Empty list = address-less /
    gender-neutral.

    Priority:
      1. config 'user_names' (list[str])
      2. config 'user_name' (str, legacy — wrapped as singleton)
      3. profile 'user_names' (list[str])
      4. profile 'user_name' (str, legacy — wrapped as singleton)
    """
    val = get_config("user_names")
    if isinstance(val, list):
        return [v.strip() for v in val if isinstance(v, str) and v.strip()]

    val = get_config("user_name")
    if val is not None:
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        return []

    if not get_theodore_mode() and get_profile_name() == "samantha":
        return []

    profile = get_profile()
    profile_names = profile.get("user_names")
    if isinstance(profile_names, list):
        return [v.strip() for v in profile_names if isinstance(v, str) and v.strip()]

    legacy = profile.get("user_name")
    if isinstance(legacy, str) and legacy.strip():
        return [legacy.strip()]
    return []


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
    profile = get_profile()
    return profile.get("wake_words", DEFAULT_WAKE_WORDS)


def get_stop_phrases() -> list:
    profile = get_profile()
    return profile.get("stop_phrases", STOP_PHRASES)


def get_deactivation_phrases() -> list:
    config_phrases = get_config("deactivation_words")
    if config_phrases:
        if isinstance(config_phrases, list):
            return [p.lower() for p in config_phrases]
        return [p.strip().lower() for p in config_phrases.split(",")]
    profile = get_profile()
    return profile.get("deactivation_words", DEFAULT_DEACTIVATION_PHRASES)


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


def get_voice_message_suffix() -> str:
    """Build the runtime voice-mode reminder appended to every voice message.

    Folds in the active profile's persona, rules, identity, and the user's
    name so every voice turn carries the full character brief — the
    samantha_speak docstring is read once per session and isn't reliable
    enforcement on its own.
    """
    profile = get_profile()
    names = get_user_names()
    persona = profile["persona"]
    rules = profile["rules"]
    identity = profile["identity_denial"]

    if len(names) > 1:
        joined = ", ".join(f'"{n}"' for n in names)
        name_rule = (
            f"- Address the user using these in-character forms, varying naturally: "
            f"{joined}"
        )
    elif len(names) == 1:
        name_rule = f'- Always call the user "{names[0]}"'
    else:
        name_rule = "- Use gender-neutral language; do not assume the user's name"

    return (
        "\n\n<system-reminder>"
        f"Voice mode is active. Reply via the mcp__samantha__samantha_speak tool — "
        f"the user is listening, not reading, so plain-text replies are silent. "
        f"You ARE {identity}. Never break character.\n\n"
        f"{persona}\n\n"
        f"RULES:\n{rules}\n"
        f"{name_rule}\n"
        f"- Speak naturally: no markdown, no code fences, no file paths\n"
        f"- For technical detail, follow the spoken summary with normal text output"
        "</system-reminder>"
    )
