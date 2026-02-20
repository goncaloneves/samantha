"""Application detection utilities for Samantha."""

import logging
import os
import platform
import re
import signal
import shutil
import subprocess
import time

from samantha.config import (
    IDE_PROCESS_NAMES,
    DESKTOP_APP_NAMES,
    SUPPORTED_DESKTOP_APPS,
    get_target_app,
    SUPPORTED_TERMINALS,
    get_ai_process_pattern,
    get_ai_window_titles,
)

logger = logging.getLogger("samantha")

PLATFORM = platform.system()


def get_frontmost_app() -> str:
    """Get the frontmost application name (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
                capture_output=True, text=True, check=True, timeout=5
            )
            return result.stdout.strip()
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            if shutil.which("wmctrl") and shutil.which("xprop"):
                result = subprocess.run(
                    ["bash", "-c", "xprop -root _NET_ACTIVE_WINDOW | awk '{print $5}'"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    window_id = result.stdout.strip()
                    result = subprocess.run(
                        ["xprop", "-id", window_id, "WM_NAME"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        import re
                        match = re.search(r'"(.+)"', result.stdout)
                        if match:
                            return match.group(1)
            return ""
        elif PLATFORM == "Windows":
            try:
                import pygetwindow as gw
                active = gw.getActiveWindow()
                if active:
                    return active.title
            except ImportError:
                pass
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value
            except Exception:
                pass
            return ""
        else:
            return ""
    except Exception:
        return ""


def activate_app(app_name: str) -> bool:
    """Activate/focus an application (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            applescript = f'''
            tell application "{app_name}"
                activate
            end tell
            '''
            subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True)
            return True
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                subprocess.run(["xdotool", "search", "--name", app_name, "windowactivate"], check=True)
                return True
            elif shutil.which("wmctrl"):
                subprocess.run(["wmctrl", "-a", app_name], check=True)
                return True
            else:
                logger.warning("No window activation tool found (xdotool or wmctrl)")
                return False
        elif PLATFORM == "Windows":
            try:
                import pygetwindow as gw
                windows = gw.getWindowsWithTitle(app_name)
                if windows:
                    windows[0].activate()
                    return True
            except ImportError:
                pass
            try:
                powershell_script = f'''
                $app = Get-Process | Where-Object {{$_.MainWindowTitle -like "*{app_name}*"}} | Select-Object -First 1
                if ($app) {{
                    Add-Type @"
                    using System;
                    using System.Runtime.InteropServices;
                    public class Win32 {{
                        [DllImport("user32.dll")]
                        public static extern bool SetForegroundWindow(IntPtr hWnd);
                    }}
"@
                    [Win32]::SetForegroundWindow($app.MainWindowHandle)
                }}
                '''
                result = subprocess.run(["powershell", "-Command", powershell_script], capture_output=True, timeout=5)
                return result.returncode == 0
            except Exception:
                pass
            return False
        else:
            return False
    except Exception as e:
        logger.warning("App activation failed: %s", e)
        return False


def is_samantha_running_elsewhere() -> bool:
    """Check if another Samantha voice loop is actually running.

    Uses the active file with PID to verify the process is still alive.
    Returns False if the active file doesn't exist or the process is dead.
    """
    from samantha.config import SAMANTHA_ACTIVE_FILE

    if not SAMANTHA_ACTIVE_FILE.exists():
        return False

    try:
        content = SAMANTHA_ACTIVE_FILE.read_text().strip()
        if not content:
            return False

        pid = int(content)
        our_pid = os.getpid()

        if pid == our_pid:
            return False

        # Check if the process is still alive
        os.kill(pid, 0)  # Doesn't kill, just checks if exists
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # Invalid PID, process dead, or no permission - clean up stale file
        SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)
        return False
    except Exception:
        return False


def kill_orphaned_processes():
    """Kill all samantha-related processes (MCP servers and services)."""
    try:
        our_pid = os.getpid()
        # Use ps to get full command lines for proper filtering
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )

        for line in result.stdout.strip().split('\n'):
            # Match samantha entry points and services
            is_samantha_process = (
                '/bin/samantha' in line or
                'samantha/__main__' in line or
                'uv run samantha' in line or
                '/.samantha/services/whisper' in line or
                '/.samantha/services/kokoro' in line
            )

            if is_samantha_process:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                        if pid != our_pid:
                            os.kill(pid, signal.SIGTERM)  # Graceful first
                            logger.info("Sent SIGTERM to samantha process: %d", pid)
                            # Give it a moment to clean up
                            time.sleep(0.1)
                            try:
                                os.kill(pid, 0)  # Check if still alive
                                os.kill(pid, signal.SIGKILL)  # Force kill
                                logger.info("Sent SIGKILL to samantha process: %d", pid)
                            except ProcessLookupError:
                                pass  # Already dead, good
                    except (ValueError, ProcessLookupError, PermissionError) as e:
                        logger.debug("Could not kill PID from line '%s': %s", line[:80], e)
    except Exception as e:
        logger.warning("Orphan cleanup failed: %s", e)


def _is_app_running_with_windows(app_name: str) -> bool:
    """Check if a specific app is running with at least one window open."""
    try:
        if PLATFORM == "Darwin":
            result = subprocess.run(
                ["osascript", "-e", f'tell application "System Events" to tell process "{app_name}" to get (count of windows)'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                window_count = int(result.stdout.strip())
                return window_count > 0
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                result = subprocess.run(
                    ["xdotool", "search", "--name", app_name],
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode == 0 and bool(result.stdout.strip())
            if shutil.which("wmctrl"):
                result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
                return result.returncode == 0 and app_name.lower() in result.stdout.lower()
        elif PLATFORM == "Windows":
            try:
                import pygetwindow as gw
                windows = gw.getWindowsWithTitle(app_name)
                return len(windows) > 0
            except ImportError:
                result = subprocess.run(
                    ["powershell", "-Command", f"Get-Process -Name '{app_name}' -ErrorAction SilentlyContinue"],
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode == 0 and bool(result.stdout.strip())
    except Exception as e:
        logger.debug("App check for '%s' failed: %s", app_name, e)
    return False


def _get_running_processes_macos() -> list[str]:
    """Get list of running process names on macOS using ps (no accessibility needed)."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "comm="],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return [p.strip().split("/")[-1] for p in result.stdout.strip().split("\n") if p.strip()]
    except Exception as e:
        logger.debug("Failed to get process list: %s", e)
    return []


def get_running_ide() -> str | None:
    """Find which supported IDE is running with windows open (cross-platform).

    If target_app is configured, prioritizes that app. If target_app is a terminal,
    returns None to trigger terminal fallback.

    Returns the IDE name if found, None otherwise.
    """
    target = get_target_app()
    if target:
        if target in SUPPORTED_TERMINALS:
            logger.debug("target_app is terminal '%s', skipping IDE detection", target)
            return None
        if target in SUPPORTED_DESKTOP_APPS:
            logger.debug("target_app is desktop app '%s', skipping IDE detection", target)
            return None
        if _is_app_running_with_windows(target):
            logger.debug("Using configured target_app: %s", target)
            return target
        logger.debug("target_app '%s' not running, falling back to auto-detect", target)

    ide_names = IDE_PROCESS_NAMES.get(PLATFORM, [])

    try:
        if PLATFORM == "Darwin":
            for ide in ide_names:
                try:
                    result = subprocess.run(
                        ["osascript", "-e", f'tell application "System Events" to tell process "{ide}" to get (count of windows)'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        window_count = int(result.stdout.strip())
                        if window_count > 0:
                            logger.debug("Found IDE: %s with %d windows", ide, window_count)
                            return ide
                except (ValueError, subprocess.TimeoutExpired):
                    continue
            # Fallback: use ps if osascript failed (e.g., no accessibility permissions)
            running_procs = _get_running_processes_macos()
            if running_procs:
                running_lower = [p.lower() for p in running_procs]
                for ide in ide_names:
                    ide_lower = ide.lower()
                    if ide_lower in running_lower or ide in running_procs:
                        logger.debug("Found IDE via ps fallback: %s", ide)
                        return ide
            return None
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                for ide in ide_names:
                    result = subprocess.run(
                        ["xdotool", "search", "--name", ide],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        logger.debug("Found IDE via xdotool: %s", ide)
                        return ide
            if shutil.which("wmctrl"):
                result = subprocess.run(
                    ["wmctrl", "-l"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    for ide in ide_names:
                        if ide.lower() in result.stdout.lower():
                            logger.debug("Found IDE via wmctrl: %s", ide)
                            return ide
            return None
        elif PLATFORM == "Windows":
            try:
                import pygetwindow as gw
                for ide in ide_names:
                    windows = gw.getWindowsWithTitle(ide)
                    if len(windows) > 0:
                        logger.debug("Found IDE via pygetwindow: %s", ide)
                        return ide
            except ImportError:
                pass
            for ide in ide_names:
                try:
                    result = subprocess.run(
                        ["powershell", "-Command", f"Get-Process -Name '{ide}' -ErrorAction SilentlyContinue"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        logger.debug("Found IDE via PowerShell: %s", ide)
                        return ide
                except Exception:
                    continue
            return None
        else:
            return None
    except Exception as e:
        logger.debug("IDE detection failed: %s", e)
        return None


def is_ide_available() -> bool:
    """Check if any supported IDE is running with windows open."""
    return get_running_ide() is not None


def get_running_desktop_app() -> str | None:
    """Find which supported desktop AI app is running with windows open.

    Checks target_app config first if it points to a desktop app.
    Returns the app name if found, None otherwise.
    """
    target = get_target_app()
    if target:
        if target in SUPPORTED_DESKTOP_APPS:
            if _is_app_running_with_windows(target):
                logger.debug("Using configured desktop target_app: %s", target)
                return target
            logger.debug("Desktop target_app '%s' not running", target)
            return None

    app_names = DESKTOP_APP_NAMES.get(PLATFORM, [])
    for app in app_names:
        if _is_app_running_with_windows(app):
            logger.debug("Found desktop app: %s", app)
            return app

    return None


def is_desktop_app_available() -> bool:
    """Check if any supported desktop AI app is running with windows open."""
    return get_running_desktop_app() is not None


def is_ai_process_running() -> bool:
    """Check if any AI CLI process is running (cross-platform).

    Uses ai_process_pattern config to match process names.
    Default pattern matches: claude, gemini, copilot, aider, chatgpt, gpt, sgpt, codex
    """
    pattern = get_ai_process_pattern()
    try:
        if PLATFORM in ("Darwin", "Linux"):
            result = subprocess.run(
                ["bash", "-c", f"ps aux | grep -E '{pattern}' | grep -v grep | wc -l"],
                capture_output=True, text=True, timeout=5
            )
            count = int(result.stdout.strip()) if result.stdout.strip() else 0
            return count > 0
        elif PLATFORM == "Windows":
            pattern_windows = pattern.replace("|", "*' -or $_.ProcessName -like '*")
            result = subprocess.run(
                ["powershell", "-Command", f"Get-Process | Where-Object {{$_.ProcessName -like '*{pattern_windows}*'}} | Measure-Object | Select-Object -ExpandProperty Count"],
                capture_output=True, text=True, timeout=5
            )
            count = int(result.stdout.strip()) if result.stdout.strip() else 0
            return count > 0
        else:
            return False
    except Exception as e:
        logger.debug("AI process detection failed: %s", e)
        return False


def is_ai_running_in_terminal() -> bool:
    """Check if any AI CLI is running in a real terminal (not IDE extension).

    Returns True if an AI process has a real TTY (like ttys001), False if running in IDE (shows ??).
    Uses ai_process_pattern config to match process names.
    """
    pattern = get_ai_process_pattern()
    try:
        if PLATFORM in ("Darwin", "Linux"):
            result = subprocess.run(
                ["bash", "-c", f"ps aux | grep -E '{pattern}' | grep -v grep | awk '{{print $7}}' | grep -v '??' | head -1"],
                capture_output=True, text=True, timeout=5
            )
            tty = result.stdout.strip()
            return bool(tty and tty != "??")
        elif PLATFORM == "Windows":
            pattern_windows = pattern.replace("|", "*' -or $_.ProcessName -like '*")
            result = subprocess.run(
                ["powershell", "-Command", f"Get-Process | Where-Object {{($_.ProcessName -like '*{pattern_windows}*') -and $_.MainWindowHandle -ne 0}} | Measure-Object | Select-Object -ExpandProperty Count"],
                capture_output=True, text=True, timeout=5
            )
            count = int(result.stdout.strip()) if result.stdout.strip() else 0
            return count > 0
        return False
    except Exception as e:
        logger.debug("Terminal AI check failed: %s", e)
        return False


def is_ai_running_in_ide_terminal(ide_name: str) -> bool:
    """Check if any AI CLI is running specifically in the given IDE's integrated terminal.

    Traces the parent process tree of AI CLI processes to see if they belong
    to the specified IDE (e.g., Cursor, Code, VS Code, Zed).
    Uses ai_process_pattern config to match process names.

    Optimized to use a single ps command and build process tree in memory.

    Returns True if an AI CLI is running in that IDE's terminal, False otherwise.
    """
    pattern = get_ai_process_pattern()
    try:
        if PLATFORM == "Darwin":
            # Get all process info in a single command (optimized from multiple calls)
            result = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,tty=,comm="],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return False

            # Build process tree in memory
            processes = {}  # pid -> (ppid, comm)
            ai_pids = []
            pattern_lower = pattern.lower()

            for line in result.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 4:
                    pid = parts[0]
                    ppid = parts[1]
                    tty = parts[2]
                    comm = ' '.join(parts[3:])  # comm may have spaces
                    comm_lower = comm.lower()
                    processes[pid] = (ppid, comm_lower)

                    # Check if this is an AI process with a real TTY
                    if tty != "??" and re.search(pattern_lower, comm_lower):
                        ai_pids.append(pid)

            if not ai_pids:
                logger.debug("No AI CLI with TTY found")
                return False

            # Traverse process tree in Python (no subprocess calls)
            ide_lower = ide_name.lower()
            for pid in ai_pids:
                current_pid = pid
                for _ in range(10):
                    if current_pid not in processes:
                        break
                    ppid, comm = processes[current_pid]
                    if not ppid or ppid == "0" or ppid == "1":
                        break

                    if ide_lower in comm or f"{ide_lower} helper" in comm:
                        logger.debug("Found AI CLI in %s terminal (PID %s, parent %s: %s)", ide_name, pid, ppid, comm)
                        return True

                    current_pid = ppid

            logger.debug("AI CLI not found in %s terminal", ide_name)
            return False

        elif PLATFORM == "Linux":
            return is_ai_running_in_terminal()

        elif PLATFORM == "Windows":
            return is_ai_running_in_terminal()

        return False
    except Exception as e:
        logger.debug("IDE terminal AI check failed: %s", e)
        return False


def find_terminal_with_ai() -> str:
    """Find a terminal window running an AI CLI (cross-platform).

    Returns the terminal app name or window identifier, or empty string if no AI
    is running in a terminal.
    """
    if not is_ai_running_in_terminal():
        logger.debug("AI not running in a terminal (probably in IDE)")
        return ""

    try:
        if PLATFORM == "Darwin":
            for app in ["Terminal", "iTerm2", "iTerm", "Alacritty", "kitty", "Warp"]:
                try:
                    result = subprocess.run(
                        ["osascript", "-e", f'tell application "System Events" to tell process "{app}" to get (count of windows)'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip() and int(result.stdout.strip()) > 0:
                        return app
                except Exception:
                    continue
            return ""
        elif PLATFORM == "Linux":
            terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm", "alacritty", "kitty", "terminator", "tilix"]
            if shutil.which("xdotool"):
                for term in terminals:
                    result = subprocess.run(
                        ["xdotool", "search", "--name", term],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        return term
            if shutil.which("wmctrl"):
                result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    for term in terminals:
                        if term.lower() in result.stdout.lower():
                            return term
            return ""
        elif PLATFORM == "Windows":
            terminals = ["Windows Terminal", "Command Prompt", "PowerShell", "cmd"]
            try:
                import pygetwindow as gw
                for term in terminals:
                    windows = gw.getWindowsWithTitle(term)
                    if windows:
                        return term
            except ImportError:
                pass
            return ""
        else:
            return ""
    except Exception as e:
        logger.debug("Terminal detection failed: %s", e)
        return ""


def activate_terminal_with_ai() -> bool:
    """Find and activate the terminal window running an AI CLI (cross-platform).

    Uses ai_window_titles config to find windows containing AI-related titles.
    Returns True if an AI terminal window was found and activated.
    """
    titles = get_ai_window_titles()
    try:
        if PLATFORM == "Darwin":
            for app in ["Terminal", "iTerm2", "iTerm", "Alacritty", "kitty", "Warp"]:
                try:
                    title_conditions = " or ".join([f'name of aWindow contains "{t}"' for t in titles])
                    title_conditions_cap = " or ".join([f'name of aWindow contains "{t.capitalize()}"' for t in titles])
                    applescript = f'''
                    tell application "System Events"
                        if exists process "{app}" then
                            tell process "{app}"
                                set windowList to every window
                                repeat with aWindow in windowList
                                    if {title_conditions} or {title_conditions_cap} then
                                        perform action "AXRaise" of aWindow
                                        set frontmost to true
                                        return "{app}"
                                    end if
                                end repeat
                            end tell
                        end if
                    end tell
                    return ""
                    '''
                    result = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True, timeout=5)
                    if result.stdout.strip() == app:
                        logger.debug("Found AI in %s window", app)
                        return True
                except Exception as e:
                    logger.debug("Error checking %s: %s", app, e)
                    continue
            return False

        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                for title in titles:
                    result = subprocess.run(
                        ["xdotool", "search", "--name", title],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        window_id = result.stdout.strip().split('\n')[0]
                        subprocess.run(["xdotool", "windowactivate", window_id], timeout=5)
                        logger.debug("Found AI window via xdotool: %s", window_id)
                        return True
            if shutil.which("wmctrl"):
                result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        for title in titles:
                            if title in line.lower():
                                window_id = line.split()[0]
                                subprocess.run(["wmctrl", "-i", "-a", window_id], timeout=5)
                                logger.debug("Found AI window via wmctrl: %s", window_id)
                                return True
            return False

        elif PLATFORM == "Windows":
            try:
                import pygetwindow as gw
                all_windows = gw.getAllWindows()
                for window in all_windows:
                    if window.title:
                        for title in titles:
                            if title in window.title.lower():
                                window.activate()
                                logger.debug("Found AI window: %s", window.title)
                                return True
            except ImportError:
                pass
            try:
                title_pattern = "|".join([f"*{t}*" for t in titles])
                result = subprocess.run(
                    ["powershell", "-Command", f'''
                    Add-Type @"
                    using System;
                    using System.Runtime.InteropServices;
                    public class Win32 {{
                        [DllImport("user32.dll")]
                        public static extern bool SetForegroundWindow(IntPtr hWnd);
                    }}
"@
                    $procs = Get-Process | Where-Object {{$_.MainWindowTitle -like "{title_pattern}"}}
                    if ($procs) {{
                        [Win32]::SetForegroundWindow($procs[0].MainWindowHandle)
                        Write-Output "Found"
                    }}
                    '''],
                    capture_output=True, text=True, timeout=5
                )
                if "Found" in result.stdout:
                    logger.debug("Found AI window via PowerShell")
                    return True
            except Exception:
                pass
            return False

        else:
            return False
    except Exception as e:
        logger.debug("activate_terminal_with_ai error: %s", e)
        return False
