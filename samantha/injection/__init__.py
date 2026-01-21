"""Injection module for Samantha."""

from .clipboard import copy_to_clipboard

from .detection import (
    get_frontmost_app,
    activate_app,
    kill_orphaned_processes,
    get_running_ide,
    is_ide_available,
    is_claude_process_running,
    is_claude_running_in_terminal,
    find_terminal_with_claude,
    activate_terminal_with_claude,
)

from .inject import (
    simulate_paste_and_enter,
    focus_ide_claude_input,
    inject_into_ide,
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
    "is_claude_process_running",
    "is_claude_running_in_terminal",
    "find_terminal_with_claude",
    "activate_terminal_with_claude",
    # Inject
    "simulate_paste_and_enter",
    "focus_ide_claude_input",
    "inject_into_ide",
    "inject_into_terminal",
    "inject_into_app",
]
