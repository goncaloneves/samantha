"""Injection module for Samantha."""

from .clipboard import copy_to_clipboard

from .detection import (
    get_frontmost_app,
    activate_app,
    kill_orphaned_processes,
    get_running_ide,
    is_ide_available,
    get_running_desktop_app,
    is_desktop_app_available,
    is_ai_process_running,
    is_ai_running_in_terminal,
    is_ai_running_in_ide_terminal,
    find_terminal_with_ai,
    activate_terminal_with_ai,
)

from .inject import (
    simulate_paste_and_enter,
    focus_ide_ai_input,
    focus_desktop_app_input,
    inject_into_ide,
    inject_into_desktop,
    inject_into_terminal,
    inject_into_app,
)

__all__ = [
    # Clipboard
    "copy_to_clipboard",
    # Detection
    "get_frontmost_app",
    "activate_app",
    "kill_orphaned_processes",
    "get_running_ide",
    "is_ide_available",
    "get_running_desktop_app",
    "is_desktop_app_available",
    "is_ai_process_running",
    "is_ai_running_in_terminal",
    "is_ai_running_in_ide_terminal",
    "find_terminal_with_ai",
    "activate_terminal_with_ai",
    # Inject
    "simulate_paste_and_enter",
    "focus_ide_ai_input",
    "focus_desktop_app_input",
    "inject_into_ide",
    "inject_into_desktop",
    "inject_into_terminal",
    "inject_into_app",
]
