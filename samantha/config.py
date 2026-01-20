"""Minimal configuration for Samantha voice mode."""

import os
import logging
from pathlib import Path

SAMANTHA_DIR = Path.home() / ".samantha"
SAMANTHA_DIR.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = os.getenv("SAMANTHA_LOG_LEVEL", "DEBUG")
LOG_FILE = SAMANTHA_DIR / "samantha.log"

logger = logging.getLogger("samantha")


def setup_logging() -> logging.Logger:
    """Set up logging for Samantha."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S"
    ))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S"
    ))

    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger
