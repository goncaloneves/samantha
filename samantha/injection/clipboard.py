"""Clipboard utilities for Samantha."""

import logging
import platform
import shutil
import subprocess

logger = logging.getLogger("samantha")

PLATFORM = platform.system()


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
        elif PLATFORM == "Linux":
            if shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
                return True
            elif shutil.which("xsel"):
                subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode(), check=True)
                return True
            elif shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=text.encode(), check=True)
                return True
            else:
                logger.error("No clipboard tool found (xclip, xsel, or wl-copy)")
                return False
        elif PLATFORM == "Windows":
            subprocess.run(["clip.exe"], input=text.encode(), check=True, shell=True)
            return True
        else:
            logger.error("Unsupported platform: %s", PLATFORM)
            return False
    except Exception as e:
        logger.error("Clipboard copy failed: %s", e)
        return False
