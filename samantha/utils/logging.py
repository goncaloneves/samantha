"""Logging utilities for Samantha."""

import logging
from datetime import datetime
from pathlib import Path

from samantha.config import CONVERSATION_LOG

logger = logging.getLogger("samantha")


def log_conversation(entry_type: str, text: str):
    """Log conversation entry to file."""
    timestamp = datetime.now().strftime("%H:%M:%S")

    if entry_type == "STT":
        log_entry = f"[{timestamp}] 🎙️ User: {text}"
    elif entry_type == "TTS":
        log_entry = f"[{timestamp}] 🔊 Samantha: {text}"
    elif entry_type == "INTERRUPT":
        log_entry = f"[{timestamp}] 🛑 Interrupt: {text}"
    else:
        log_entry = f"[{timestamp}] {entry_type}: {text}"

    try:
        CONVERSATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CONVERSATION_LOG, "a") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        logger.debug("Failed to write conversation log: %s", e)

    logger.info(log_entry)


def get_persona() -> str:
    """Read persona instructions from CLAUDE.md."""
    try:
        claude_md_path = Path(__file__).parent.parent.parent / "CLAUDE.md"
        if claude_md_path.exists():
            content = claude_md_path.read_text()
            start = content.find("## Samantha Persona")
            if start != -1:
                end = content.find("---", start)
                if end != -1:
                    return content[start:end].strip()
    except Exception:
        pass
    return ""
