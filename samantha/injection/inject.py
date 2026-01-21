"""Injection utilities for Samantha."""

import logging
import platform
import shutil
import subprocess
import time

from samantha.config import get_restore_focus, get_injection_mode
import samantha.audio.playback as playback
from samantha.injection.clipboard import copy_to_clipboard
from samantha.injection.detection import (
    get_frontmost_app,
    activate_app,
    get_running_ide,
    is_claude_process_running,
    is_claude_running_in_terminal,
    activate_terminal_with_claude,
)

logger = logging.getLogger("samantha")

PLATFORM = platform.system()


def simulate_paste_and_enter() -> bool:
    """Simulate Cmd/Ctrl+V paste and Enter keystroke (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            applescript = '''
            tell application "System Events"
                keystroke "v" using command down
                delay 0.2
                key code 36
            end tell
            '''
            subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True)
            return True
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                subprocess.run(["xdotool", "key", "ctrl+v"], check=True)
                time.sleep(0.2)
                subprocess.run(["xdotool", "key", "Return"], check=True)
                return True
            elif shutil.which("ydotool"):
                subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
                time.sleep(0.2)
                subprocess.run(["ydotool", "key", "28:1", "28:0"], check=True)
                return True
            else:
                logger.error("No keystroke tool found (xdotool or ydotool)")
                return False
        elif PLATFORM == "Windows":
            try:
                import pyautogui
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.2)
                pyautogui.press('enter')
                return True
            except ImportError:
                powershell_script = '''
                Add-Type -AssemblyName System.Windows.Forms
                [System.Windows.Forms.SendKeys]::SendWait("^v")
                Start-Sleep -Milliseconds 200
                [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
                '''
                subprocess.run(["powershell", "-Command", powershell_script], check=True)
                return True
        else:
            logger.error("Unsupported platform: %s", PLATFORM)
            return False
    except Exception as e:
        logger.error("Paste simulation failed: %s", e)
        return False


def focus_ide_claude_input(ide_name: str) -> bool:
    """Focus IDE's Claude input field using Cmd/Ctrl+Escape (cross-platform).

    This shortcut toggles focus between the editor and Claude's prompt box.
    Works with Cursor, VS Code, VSCodium, and other IDEs with Claude Code extension.
    """
    try:
        if PLATFORM == "Darwin":
            activate_app(ide_name)
            time.sleep(0.3)
            subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to key code 53 using command down'],
                check=True, capture_output=True, timeout=5
            )
            time.sleep(0.2)
            return True
        elif PLATFORM == "Linux":
            activate_app(ide_name)
            time.sleep(0.3)
            if shutil.which("xdotool"):
                subprocess.run(
                    ["xdotool", "key", "ctrl+Escape"],
                    check=True, capture_output=True, timeout=5
                )
                time.sleep(0.2)
                return True
            elif shutil.which("ydotool"):
                subprocess.run(
                    ["ydotool", "key", "29:1", "1:1", "1:0", "29:0"],
                    check=True, capture_output=True, timeout=5
                )
                time.sleep(0.2)
                return True
            return False
        elif PLATFORM == "Windows":
            activate_app(ide_name)
            time.sleep(0.3)
            try:
                import pyautogui
                pyautogui.hotkey('ctrl', 'escape')
                time.sleep(0.2)
                return True
            except ImportError:
                pass
            try:
                powershell_script = '''
                Add-Type -AssemblyName System.Windows.Forms
                [System.Windows.Forms.SendKeys]::SendWait("^{ESC}")
                '''
                subprocess.run(["powershell", "-Command", powershell_script], check=True, timeout=5)
                time.sleep(0.2)
                return True
            except Exception:
                pass
            return False
        else:
            return False
    except Exception as e:
        logger.debug("Focus %s Claude input failed: %s", ide_name, e)
        return False


def focus_ide_terminal(ide_name: str) -> bool:
    """Focus IDE's integrated terminal using Ctrl+` (cross-platform).

    This shortcut toggles the terminal panel in VS Code, Cursor, and similar IDEs.
    Used for CLI mode when Claude is running in the IDE's integrated terminal.
    """
    try:
        if PLATFORM == "Darwin":
            activate_app(ide_name)
            time.sleep(0.3)
            subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to keystroke "`" using control down'],
                check=True, capture_output=True, timeout=5
            )
            time.sleep(0.2)
            return True
        elif PLATFORM == "Linux":
            activate_app(ide_name)
            time.sleep(0.3)
            if shutil.which("xdotool"):
                subprocess.run(
                    ["xdotool", "key", "ctrl+grave"],
                    check=True, capture_output=True, timeout=5
                )
                time.sleep(0.2)
                return True
            elif shutil.which("ydotool"):
                subprocess.run(
                    ["ydotool", "key", "29:1", "41:1", "41:0", "29:0"],
                    check=True, capture_output=True, timeout=5
                )
                time.sleep(0.2)
                return True
            return False
        elif PLATFORM == "Windows":
            activate_app(ide_name)
            time.sleep(0.3)
            try:
                import pyautogui
                pyautogui.hotkey('ctrl', '`')
                time.sleep(0.2)
                return True
            except ImportError:
                pass
            try:
                powershell_script = '''
                Add-Type -AssemblyName System.Windows.Forms
                [System.Windows.Forms.SendKeys]::SendWait("^{`}")
                '''
                subprocess.run(["powershell", "-Command", powershell_script], check=True, timeout=5)
                time.sleep(0.2)
                return True
            except Exception:
                pass
            return False
        else:
            return False
    except Exception as e:
        logger.debug("Focus %s terminal failed: %s", ide_name, e)
        return False


def inject_into_ide(text: str) -> bool:
    """Inject text into IDE's Claude input field or integrated terminal.

    Behavior depends on injection_mode config:
    - 'extension' (default): Focus Claude Code extension input (Cmd+Escape)
    - 'cli': Focus integrated terminal (Ctrl+`) for Claude CLI

    Returns True if injection succeeded, False otherwise.
    """
    ide_name = get_running_ide()
    if not ide_name:
        logger.debug("No supported IDE available")
        return False

    injection_mode = get_injection_mode()

    if injection_mode == "cli":
        if not is_claude_running_in_terminal():
            logger.debug("Claude CLI not running in terminal")
            return False
        logger.info("💉 Injecting into %s terminal (CLI mode): %s", ide_name, text[:50])
        focus_func = focus_ide_terminal
        target_desc = "terminal"
    else:
        if not is_claude_process_running():
            logger.debug("Claude process not running")
            return False
        logger.info("💉 Injecting into %s (extension mode): %s", ide_name, text[:50])
        focus_func = focus_ide_claude_input
        target_desc = "Claude input"

    if not copy_to_clipboard(text):
        logger.error("Failed to copy to clipboard")
        return False

    if not focus_func(ide_name):
        logger.warning("Failed to focus %s %s", ide_name, target_desc)
        return False

    time.sleep(0.2)

    if simulate_paste_and_enter():
        logger.info("✅ Injected into %s", ide_name)
        return True
    else:
        logger.error("Paste failed in %s", ide_name)
        return False


def inject_into_terminal(text: str) -> bool:
    """Inject text into Terminal running Claude (cross-platform).

    Returns True if injection succeeded, False otherwise.
    """
    if not is_claude_running_in_terminal():
        logger.debug("Claude not running in a terminal")
        return False

    logger.info("💉 Injecting into terminal: %s", text[:50])

    if not copy_to_clipboard(text):
        logger.error("Failed to copy to clipboard")
        return False

    if not activate_terminal_with_claude():
        logger.warning("Could not find terminal window with Claude")
        return False

    time.sleep(0.3)

    if simulate_paste_and_enter():
        logger.info("✅ Injected into terminal")
        return True
    else:
        logger.error("Injection failed")
        return False


def inject_into_app(text: str, log_type: str = None):
    """Inject text into IDE or terminal (with fallback).

    Tries IDE first (Cursor, VS Code, etc.), then falls back to terminal.
    Captures frontmost app right before injection and restores focus after.
    """
    previous_app = get_frontmost_app() if get_restore_focus() else None

    success = False
    target_app = None
    ide_name = get_running_ide()
    if ide_name and inject_into_ide(text):
        success = True
        target_app = ide_name
    else:
        if ide_name:
            logger.info("%s injection failed, falling back to terminal", ide_name)
        else:
            logger.debug("No IDE found, trying terminal")
        if inject_into_terminal(text):
            success = True
            target_app = "Terminal"
        else:
            logger.error("All injection methods failed - no Claude target found")

    if not success:
        try:
            with playback._tts_queue_lock:
                playback._tts_text_queue.append("I couldn't find Claude running in any IDE or Terminal. Please make sure Claude is open.")
        except Exception:
            pass
        return

    if success and previous_app and get_restore_focus() and previous_app != target_app:
        time.sleep(0.3)
        activate_app(previous_app)
        logger.debug("Restored focus to %s", previous_app)
