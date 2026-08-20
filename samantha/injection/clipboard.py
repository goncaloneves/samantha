"""Clipboard utilities for Samantha."""

import logging
import contextlib
import platform
import shutil
import subprocess

logger = logging.getLogger("samantha")

PLATFORM = platform.system()


def read_clipboard() -> str:
    """Read the current clipboard, or "" if it cannot be read."""
    try:
        if PLATFORM == "Darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, timeout=5)
        elif PLATFORM == "Linux":
            for tool, args in (("xclip", ["-selection", "clipboard", "-o"]),
                               ("xsel", ["--clipboard", "--output"]),
                               ("wl-paste", [])):
                if shutil.which(tool):
                    r = subprocess.run([tool, *args], capture_output=True, timeout=5)
                    break
            else:
                return ""
        elif PLATFORM == "Windows":
            r = subprocess.run(["powershell", "-command", "Get-Clipboard"],
                               capture_output=True, timeout=5)
        else:
            return ""
        return r.stdout.decode(errors="replace")
    except Exception as e:
        logger.debug("Clipboard read failed: %s", e)
        return ""


@contextlib.contextmanager
def preserved_clipboard():
    """Restore the user's clipboard after an injection, on every exit path.

    Injection pastes via the system clipboard, so without this every utterance
    silently destroys whatever the user had copied - and the early returns on
    focus failure meant it was never put back even on the failure paths.
    """
    saved = read_clipboard()
    try:
        yield
    finally:
        if saved:
            try:
                copy_to_clipboard(saved)
            except Exception as e:
                logger.debug("Clipboard restore failed: %s", e)


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
