"""Application detection utilities for Samantha."""

import logging
import os
import platform
import shutil
import subprocess
import time

from samantha.config import IDE_PROCESS_NAMES

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


def kill_orphaned_processes():
    """Kill any orphaned samantha processes from previous sessions."""
    try:
        our_pid = os.getpid()
        result = subprocess.run(["pgrep", "-f", "samantha"], capture_output=True, text=True, timeout=5)
        if result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                pid = int(pid.strip())
                if pid != our_pid:
                    try:
                        os.kill(pid, 9)
                        logger.info("Killed orphaned samantha process: %d", pid)
                    except (ProcessLookupError, PermissionError):
                        pass
    except Exception as e:
        logger.debug("Cleanup check failed: %s", e)


def get_running_ide() -> str | None:
    """Find which supported IDE is running with windows open (cross-platform).

    Returns the IDE name if found, None otherwise.
    """
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


def is_claude_process_running() -> bool:
    """Check if Claude Code process is running (cross-platform)."""
    try:
        if PLATFORM in ("Darwin", "Linux"):
            result = subprocess.run(
                ["bash", "-c", "ps aux | grep -c '[c]laude.*stream-json'"],
                capture_output=True, text=True, timeout=5
            )
            count = int(result.stdout.strip()) if result.stdout.strip() else 0
            return count > 0
        elif PLATFORM == "Windows":
            result = subprocess.run(
                ["powershell", "-Command", "Get-Process | Where-Object {$_.ProcessName -like '*claude*'} | Measure-Object | Select-Object -ExpandProperty Count"],
                capture_output=True, text=True, timeout=5
            )
            count = int(result.stdout.strip()) if result.stdout.strip() else 0
            return count > 0
        else:
            return False
    except Exception as e:
        logger.debug("Claude process detection failed: %s", e)
        return False


def is_claude_running_in_terminal() -> bool:
    """Check if Claude is running in a real terminal (not Cursor/IDE).

    Returns True if Claude has a real TTY (like ttys001), False if running in IDE (shows ??).
    """
    try:
        if PLATFORM in ("Darwin", "Linux"):
            result = subprocess.run(
                ["bash", "-c", "ps aux | grep '[c]laude' | grep -v grep | awk '{print $7}' | grep -v '??' | head -1"],
                capture_output=True, text=True, timeout=5
            )
            tty = result.stdout.strip()
            return bool(tty and tty != "??")
        elif PLATFORM == "Windows":
            result = subprocess.run(
                ["powershell", "-Command", "Get-Process | Where-Object {$_.ProcessName -like '*claude*' -and $_.MainWindowHandle -ne 0} | Measure-Object | Select-Object -ExpandProperty Count"],
                capture_output=True, text=True, timeout=5
            )
            count = int(result.stdout.strip()) if result.stdout.strip() else 0
            return count > 0
        return False
    except Exception as e:
        logger.debug("Terminal Claude check failed: %s", e)
        return False


def find_terminal_with_claude() -> str:
    """Find a terminal window running Claude (cross-platform).

    Returns the terminal app name or window identifier, or empty string if Claude
    is not running in a terminal.
    """
    if not is_claude_running_in_terminal():
        logger.debug("Claude not running in a terminal (probably in Cursor/IDE)")
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


def activate_terminal_with_claude() -> bool:
    """Find and activate the terminal window running Claude (cross-platform).

    Uses window title matching to find windows containing "claude" or "Claude".
    Returns True if a Claude terminal window was found and activated.
    """
    try:
        if PLATFORM == "Darwin":
            for app in ["Terminal", "iTerm2", "iTerm", "Alacritty", "kitty", "Warp"]:
                try:
                    applescript = f'''
                    tell application "System Events"
                        if exists process "{app}" then
                            tell process "{app}"
                                set windowList to every window
                                repeat with aWindow in windowList
                                    if name of aWindow contains "claude" or name of aWindow contains "Claude" then
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
                        logger.debug("Found Claude in %s window", app)
                        return True
                except Exception as e:
                    logger.debug("Error checking %s: %s", app, e)
                    continue
            return False

        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                result = subprocess.run(
                    ["xdotool", "search", "--name", "claude"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    window_id = result.stdout.strip().split('\n')[0]
                    subprocess.run(["xdotool", "windowactivate", window_id], timeout=5)
                    logger.debug("Found Claude window via xdotool: %s", window_id)
                    return True
            if shutil.which("wmctrl"):
                result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if 'claude' in line.lower():
                            window_id = line.split()[0]
                            subprocess.run(["wmctrl", "-i", "-a", window_id], timeout=5)
                            logger.debug("Found Claude window via wmctrl: %s", window_id)
                            return True
            return False

        elif PLATFORM == "Windows":
            try:
                import pygetwindow as gw
                all_windows = gw.getAllWindows()
                for window in all_windows:
                    if window.title and ('claude' in window.title.lower()):
                        window.activate()
                        logger.debug("Found Claude window: %s", window.title)
                        return True
            except ImportError:
                pass
            try:
                result = subprocess.run(
                    ["powershell", "-Command", '''
                    Add-Type @"
                    using System;
                    using System.Runtime.InteropServices;
                    public class Win32 {
                        [DllImport("user32.dll")]
                        public static extern bool SetForegroundWindow(IntPtr hWnd);
                    }
"@
                    $procs = Get-Process | Where-Object {$_.MainWindowTitle -like "*claude*"}
                    if ($procs) {
                        [Win32]::SetForegroundWindow($procs[0].MainWindowHandle)
                        Write-Output "Found"
                    }
                    '''],
                    capture_output=True, text=True, timeout=5
                )
                if "Found" in result.stdout:
                    logger.debug("Found Claude window via PowerShell")
                    return True
            except Exception:
                pass
            return False

        else:
            return False
    except Exception as e:
        logger.debug("activate_terminal_with_claude error: %s", e)
        return False
