"""Injection utilities for Samantha."""

import logging
import platform
import shutil
import subprocess
import time

import samantha.audio.playback as playback
from samantha.config import INJECTION_TIMEOUT, get_injection_mode, get_restore_focus
from samantha.injection.clipboard import copy_to_clipboard, preserved_clipboard
from samantha.injection.detection import (
    FOCUS_EDITOR,
    FOCUS_OTHER,
    FOCUS_TERMINAL,
    FOCUS_UNKNOWN,
    focused_input_state,
    activate_app,
    activate_terminal_with_ai,
    get_frontmost_app,
    get_running_ide,
    get_running_desktop_app,
    is_ai_process_running,
    is_ai_running_in_ide_terminal,
    is_ai_running_in_terminal,
)

logger = logging.getLogger("samantha")

PLATFORM = platform.system()


def ensure_ai_input_focused(ide_name: str) -> bool:
    """Focus the AI input as documented, then verify the request actually landed.

    The focus shortcut is ALWAYS sent. README documents that as the contract,
    and the probe cannot positively identify the AI input: the integrated
    terminal, quick open and the find widget all present the same accessibility
    signature as a chat box. Using the probe to SKIP focusing would therefore
    paste a transcript into whatever text field happened to be focused - a shell
    prompt included, followed by Return.

    So the probe is used only to REFUSE, never to skip work. When it can see
    that focus ended up somewhere unusable, this returns False and the caller
    falls through to the next injection method, which is the recovery the
    documented auto chain already provides - and which the old unconditional
    "success" return had been suppressing.
    """
    if not focus_ide_ai_input(ide_name):
        logger.error("Could not focus the AI input in %s", ide_name)
        return False

    state = focused_input_state(ide_name)
    if state in (FOCUS_EDITOR, FOCUS_TERMINAL, FOCUS_OTHER):
        logger.error(
            "Refusing to type into %s: after requesting the AI input, focus is %r. "
            "Falling back to the next injection method.", ide_name, state,
        )
        return False

    # FOCUS_INPUT, or FOCUS_UNKNOWN where there is no probe (Linux, Windows, or
    # macOS without Accessibility permission). Both proceed exactly as before.
    return True


def simulate_paste_and_enter() -> bool:
    """Simulate Cmd/Ctrl+V paste and Enter keystroke (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            applescript = """
            tell application "System Events"
                keystroke "v" using command down
                delay 0.2
                key code 36
            end tell
            """
            subprocess.run(
                ["osascript", "-e", applescript], check=True,
                capture_output=True, timeout=INJECTION_TIMEOUT
            )
            return True
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                subprocess.run(["xdotool", "key", "ctrl+v"], check=True)
                time.sleep(0.2)
                subprocess.run(["xdotool", "key", "Return"], check=True)
                return True
            elif shutil.which("ydotool"):
                subprocess.run(
                    ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True
                )
                time.sleep(0.2)
                subprocess.run(["ydotool", "key", "28:1", "28:0"], check=True)
                return True
            else:
                logger.error("No keystroke tool found (xdotool or ydotool)")
                return False
        elif PLATFORM == "Windows":
            try:
                import pyautogui

                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.2)
                pyautogui.press("enter")
                return True
            except ImportError:
                powershell_script = """
                Add-Type -AssemblyName System.Windows.Forms
                [System.Windows.Forms.SendKeys]::SendWait("^v")
                Start-Sleep -Milliseconds 200
                [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
                """
                subprocess.run(
                    ["powershell", "-Command", powershell_script], check=True
                )
                return True
        else:
            logger.error("Unsupported platform: %s", PLATFORM)
            return False
    except Exception as e:
        logger.error("Paste simulation failed: %s", e)
        return False


def focus_ide_ai_input(ide_name: str) -> bool:
    """Focus IDE's AI input field using Cmd/Ctrl+Escape (cross-platform).

    This shortcut toggles focus between the editor and the AI's prompt box.
    Works with Cursor, VS Code, VSCodium, and other IDEs with AI extensions.
    Zed uses Cmd+? (Shift+Cmd+/) instead.
    """
    try:
        if PLATFORM == "Darwin":
            if not activate_app(ide_name):
                logger.error("Could not activate %s - refusing to send keystrokes blind", ide_name)
                return False
            time.sleep(0.3)
            # Zed uses Cmd+? (Shift+Cmd+/) for assistant::ToggleFocus
            if ide_name.lower() == "zed":
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to keystroke "/" using {command down, shift down}',
                    ],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            else:
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to key code 53 using command down',
                    ],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            time.sleep(0.2)
            return True
        elif PLATFORM == "Linux":
            if not activate_app(ide_name):
                logger.error("Could not activate %s - refusing to send keystrokes blind", ide_name)
                return False
            time.sleep(0.3)
            if shutil.which("xdotool"):
                # Zed uses Ctrl+? (Ctrl+Shift+/) for assistant::ToggleFocus
                if ide_name.lower() == "zed":
                    subprocess.run(
                        ["xdotool", "key", "ctrl+shift+slash"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    subprocess.run(
                        ["xdotool", "key", "ctrl+Escape"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                time.sleep(0.2)
                return True
            elif shutil.which("ydotool"):
                # Zed uses Ctrl+? (Ctrl+Shift+/) - key codes: 29=ctrl, 42=shift, 53=slash
                if ide_name.lower() == "zed":
                    subprocess.run(
                        [
                            "ydotool",
                            "key",
                            "29:1",
                            "42:1",
                            "53:1",
                            "53:0",
                            "42:0",
                            "29:0",
                        ],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    subprocess.run(
                        ["ydotool", "key", "29:1", "1:1", "1:0", "29:0"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                time.sleep(0.2)
                return True
            return False
        elif PLATFORM == "Windows":
            if not activate_app(ide_name):
                logger.error("Could not activate %s - refusing to send keystrokes blind", ide_name)
                return False
            time.sleep(0.3)
            try:
                import pyautogui

                # Zed uses Ctrl+? (Ctrl+Shift+/) for assistant::ToggleFocus
                if ide_name == "Zed":
                    pyautogui.hotkey("ctrl", "shift", "/")
                else:
                    pyautogui.hotkey("ctrl", "escape")
                time.sleep(0.2)
                return True
            except ImportError:
                pass
            try:
                # Zed uses Ctrl+? (Ctrl+Shift+/) for assistant::ToggleFocus
                if ide_name == "Zed":
                    powershell_script = """
                    Add-Type -AssemblyName System.Windows.Forms
                    [System.Windows.Forms.SendKeys]::SendWait("^+/")
                    """
                else:
                    powershell_script = """
                    Add-Type -AssemblyName System.Windows.Forms
                    [System.Windows.Forms.SendKeys]::SendWait("^{ESC}")
                    """
                subprocess.run(
                    ["powershell", "-Command", powershell_script], check=True, timeout=5
                )
                time.sleep(0.2)
                return True
            except Exception:
                pass
            return False
        else:
            return False
    except Exception as e:
        logger.debug("Focus %s AI input failed: %s", ide_name, e)
        return False


def focus_ide_terminal(ide_name: str) -> bool:
    """Focus IDE's integrated terminal using Ctrl+` (cross-platform).

    This shortcut toggles the terminal panel in VS Code, Cursor, and similar IDEs.
    Zed uses Cmd/Ctrl+J instead.
    Used for CLI mode when Claude is running in the IDE's integrated terminal.
    """
    try:
        if PLATFORM == "Darwin":
            if not activate_app(ide_name):
                logger.error("Could not activate %s - refusing to send keystrokes blind", ide_name)
                return False
            time.sleep(0.3)
            # Zed uses Cmd+J for workspace::ToggleBottomDock (terminal)
            if ide_name.lower() == "zed":
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to keystroke "j" using command down',
                    ],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            else:
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to keystroke "`" using control down',
                    ],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            time.sleep(0.2)
            return True
        elif PLATFORM == "Linux":
            if not activate_app(ide_name):
                logger.error("Could not activate %s - refusing to send keystrokes blind", ide_name)
                return False
            time.sleep(0.3)
            if shutil.which("xdotool"):
                # Zed uses Ctrl+J for workspace::ToggleBottomDock (terminal)
                if ide_name.lower() == "zed":
                    subprocess.run(
                        ["xdotool", "key", "ctrl+j"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    subprocess.run(
                        ["xdotool", "key", "ctrl+grave"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                time.sleep(0.2)
                return True
            elif shutil.which("ydotool"):
                # Zed uses Ctrl+J - key codes: 29=ctrl, 36=j
                if ide_name.lower() == "zed":
                    subprocess.run(
                        ["ydotool", "key", "29:1", "36:1", "36:0", "29:0"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    subprocess.run(
                        ["ydotool", "key", "29:1", "41:1", "41:0", "29:0"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
                time.sleep(0.2)
                return True
            return False
        elif PLATFORM == "Windows":
            if not activate_app(ide_name):
                logger.error("Could not activate %s - refusing to send keystrokes blind", ide_name)
                return False
            time.sleep(0.3)
            try:
                import pyautogui

                # Zed uses Ctrl+J for workspace::ToggleBottomDock (terminal)
                if ide_name == "Zed":
                    pyautogui.hotkey("ctrl", "j")
                else:
                    pyautogui.hotkey("ctrl", "`")
                time.sleep(0.2)
                return True
            except ImportError:
                pass
            try:
                # Zed uses Ctrl+J for workspace::ToggleBottomDock (terminal)
                if ide_name == "Zed":
                    powershell_script = """
                    Add-Type -AssemblyName System.Windows.Forms
                    [System.Windows.Forms.SendKeys]::SendWait("^j")
                    """
                else:
                    powershell_script = """
                    Add-Type -AssemblyName System.Windows.Forms
                    [System.Windows.Forms.SendKeys]::SendWait("^{`}")
                    """
                subprocess.run(
                    ["powershell", "-Command", powershell_script], check=True, timeout=5
                )
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


def _try_inject_extension(ide_name: str, text: str) -> bool:
    """Try to inject via AI extension (Cmd+Escape)."""
    if not is_ai_process_running():
        logger.debug("AI extension not running")
        return False

    logger.info("💉 Injecting into %s (extension mode): %s", ide_name, text[:50])

    if not copy_to_clipboard(text):
        logger.error("Failed to copy to clipboard")
        return False

    if not ensure_ai_input_focused(ide_name):
        return False

    time.sleep(0.2)

    if simulate_paste_and_enter():
        logger.info("✅ Injected into %s extension", ide_name)
        return True

    logger.debug("Paste failed in %s extension", ide_name)
    return False


def _try_inject_cli(ide_name: str, text: str) -> bool:
    """Try to inject via IDE's integrated terminal (Ctrl+`)."""
    if not is_ai_running_in_ide_terminal(ide_name):
        logger.debug("AI CLI not running in %s terminal", ide_name)
        return False

    logger.info("💉 Injecting into %s terminal (CLI mode): %s", ide_name, text[:50])

    if not copy_to_clipboard(text):
        logger.error("Failed to copy to clipboard")
        return False

    if not focus_ide_terminal(ide_name):
        logger.debug("Failed to focus %s terminal", ide_name)
        return False

    time.sleep(0.2)

    if simulate_paste_and_enter():
        logger.info("✅ Injected into %s terminal", ide_name)
        return True

    logger.debug("Paste failed in %s terminal", ide_name)
    return False


def inject_into_ide(text: str) -> bool:
    """Inject text into IDE's Claude input field or integrated terminal.

    Behavior depends on injection_mode config:
    - 'auto' (default): Try extension first, then CLI
    - 'extension': Focus Claude Code extension input (Cmd+Escape)
    - 'cli': Focus integrated terminal (Ctrl+`) for Claude CLI

    Returns True if injection succeeded, False otherwise.
    """
    ide_name = get_running_ide()
    if not ide_name:
        logger.debug("No supported IDE available")
        return False

    injection_mode = get_injection_mode()

    if injection_mode == "auto":
        if _try_inject_extension(ide_name, text):
            return True
        logger.debug("Extension mode failed, trying CLI mode")
        if _try_inject_cli(ide_name, text):
            return True
        logger.debug("Both extension and CLI modes failed for %s", ide_name)
        return False
    elif injection_mode == "cli":
        return _try_inject_cli(ide_name, text)
    else:
        return _try_inject_extension(ide_name, text)


def inject_into_terminal(text: str) -> bool:
    """Inject text into Terminal running an AI CLI (cross-platform).

    Returns True if injection succeeded, False otherwise.
    """
    if not is_ai_running_in_terminal():
        logger.debug("AI not running in a terminal")
        return False

    logger.info("💉 Injecting into terminal: %s", text[:50])

    if not copy_to_clipboard(text):
        logger.error("Failed to copy to clipboard")
        return False

    if not activate_terminal_with_ai():
        logger.warning("Could not find terminal window with AI")
        return False

    time.sleep(0.3)

    if simulate_paste_and_enter():
        logger.info("✅ Injected into terminal")
        return True
    else:
        logger.error("Injection failed")
        return False


def focus_desktop_app_input(app_name: str) -> bool:
    """Focus a desktop AI app's text input field.

    For Electron apps like Claude Desktop, activating the window is sufficient
    since the chat input retains focus when the window is activated.
    """
    try:
        activate_app(app_name)
        time.sleep(0.3)
        return True
    except Exception as e:
        logger.debug("Focus %s input failed: %s", app_name, e)
        return False


def inject_into_desktop(text: str) -> bool:
    """Inject text into a desktop AI app (e.g., Claude Desktop).

    Activates the app window, pastes text from clipboard, and sends Enter.
    Returns True if injection succeeded, False otherwise.
    """
    app_name = get_running_desktop_app()
    if not app_name:
        logger.debug("No desktop AI app available")
        return False

    logger.info("Injecting into %s (desktop mode): %s", app_name, text[:50])

    if not copy_to_clipboard(text):
        logger.error("Failed to copy to clipboard")
        return False

    if not focus_desktop_app_input(app_name):
        logger.debug("Failed to focus %s input", app_name)
        return False

    time.sleep(0.2)

    if simulate_paste_and_enter():
        logger.info("Injected into %s desktop app", app_name)
        return True

    logger.debug("Paste failed in %s desktop app", app_name)
    return False


def inject_into_app(text: str, log_type: str = None):
    """Inject text into IDE, desktop app, or terminal (with fallback).

    Behavior depends on injection_mode config:
    - 'auto' (default): Try IDE first, then desktop app, then terminal
    - 'extension': Only try IDE extension panel
    - 'cli': Only try IDE's integrated terminal
    - 'desktop': Only try desktop AI apps (Claude Desktop, etc.)
    - 'terminal': Only try standalone terminal apps

    Captures frontmost app right before injection and restores focus after.
    """
    with preserved_clipboard():
        return _inject_into_app(text, log_type)


def _inject_into_app(text: str, log_type: str = None):
    previous_app = get_frontmost_app() if get_restore_focus() else None
    injection_mode = get_injection_mode()

    success = False
    target_app = None

    if injection_mode == "desktop":
        app_name = get_running_desktop_app()
        if app_name and inject_into_desktop(text):
            success = True
            target_app = app_name
        else:
            logger.error("Desktop injection failed - no desktop AI app running")
    elif injection_mode == "terminal":
        logger.debug("Terminal mode: skipping IDE, going directly to terminal")
        if inject_into_terminal(text):
            success = True
            target_app = "Terminal"
        else:
            logger.error("Terminal injection failed - no AI running in terminal")
    elif injection_mode in ("extension", "cli"):
        ide_name = get_running_ide()
        if ide_name and inject_into_ide(text):
            success = True
            target_app = ide_name
        else:
            logger.error("%s mode injection failed", injection_mode)
    else:
        ide_name = get_running_ide()
        if ide_name and inject_into_ide(text):
            success = True
            target_app = ide_name
        else:
            if ide_name:
                logger.info("%s injection failed, trying desktop app", ide_name)
            else:
                logger.debug("No IDE found, trying desktop app")
            desktop_name = get_running_desktop_app()
            if desktop_name and inject_into_desktop(text):
                success = True
                target_app = desktop_name
            else:
                if desktop_name:
                    logger.debug("%s desktop injection failed, trying terminal", desktop_name)
                else:
                    logger.debug("No desktop app found, trying terminal")
                if inject_into_terminal(text):
                    success = True
                    target_app = "Terminal"
                else:
                    logger.error("All injection methods failed - no AI target found")

    if not success:
        try:
            with playback._tts_queue_lock:
                playback._tts_text_queue.append(
                    "I couldn't find an AI assistant running in any IDE, desktop app, or terminal. Please make sure your AI is open."
                )
        except Exception:
            pass
        return

    if success and previous_app and get_restore_focus() and previous_app != target_app:
        time.sleep(0.3)
        activate_app(previous_app)
        logger.debug("Restored focus to %s", previous_app)
