"""Samantha MCP tools - Voice assistant with wake word detection and TTS."""

import asyncio
import json
import logging
import os
import signal
import threading
import time

from samantha.server import mcp
from samantha.config import (
    SAMANTHA_DIR,
    SAMANTHA_ACTIVE_FILE,
    CONVERSATION_LOG,
    VOICE_MESSAGE_PREFIX,
    get_wake_words,
)
import samantha.audio.playback as playback
from samantha.injection.detection import kill_orphaned_processes, is_samantha_running_elsewhere, get_running_ide, find_terminal_with_ai
from samantha.services.health import (
    ensure_kokoro_running,
    ensure_whisper_running,
    _check_service_health,
)
from samantha.core.loop import samantha_loop_thread
import samantha.core.state as state

logger = logging.getLogger("samantha")


@mcp.tool()
async def samantha_start() -> str:
    """Start Samantha voice mode.

    Integrated voice assistant that:
    1. Listens for "Hey Samantha"
    2. Records your voice command
    3. Sends it to Claude with voice marker
    4. Speaks Claude's response via TTS
    5. Logs conversation (STT/TTS) for history

    Usage: Say "Hey Samantha, [your question]" then "that's all"

    Returns:
        Status message
    """
    # Always clean up orphaned processes first to prevent accumulation
    kill_orphaned_processes()

    already_running = (
        (state._samantha_thread and state._samantha_thread.is_alive())
        or is_samantha_running_elsewhere()
    )
    if already_running:
        return "🎧 Samantha is already running. Use /samantha:stop to stop it."

    if SAMANTHA_ACTIVE_FILE.exists():
        SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)

    SAMANTHA_DIR.mkdir(parents=True, exist_ok=True)
    # Write our PID to the active file so other instances can check if we're alive
    SAMANTHA_ACTIVE_FILE.write_text(str(os.getpid()))

    kokoro_ok = await ensure_kokoro_running()
    whisper_ok = await ensure_whisper_running()

    if not kokoro_ok:
        SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)
        return "❌ Failed to start Kokoro TTS service"
    if not whisper_ok:
        SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)
        return "❌ Failed to start Whisper STT service"

    state._thread_stop_flag = False
    state._thread_ready = threading.Event()
    state._samantha_thread = threading.Thread(target=samantha_loop_thread, daemon=True)
    state._samantha_thread.start()

    ready = state._thread_ready.wait(timeout=30.0)

    if not ready:
        state._thread_stop_flag = True
        SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)
        return "❌ Samantha failed to start - audio stream not ready after 30 seconds. Please try again."

    # Check what IDE/terminal was detected
    detected = []
    ide = get_running_ide()
    if ide:
        detected.append(f"IDE: {ide}")
    terminal = find_terminal_with_ai()
    if terminal:
        detected.append(f"Terminal: {terminal}")
    
    status = "🎧 Samantha started."
    if detected:
        status += f" Detected: {', '.join(detected)}."
    else:
        status += " ⚠️ No IDE/terminal detected yet - will find it when you speak."
    status += " Say 'Hey Samantha' to activate."
    return status


@mcp.tool()
async def samantha_stop() -> str:
    """Stop Samantha voice mode.

    Returns:
        Status message
    """
    # First, try to kill the process recorded in the PID file (handles other samantha instances)
    if SAMANTHA_ACTIVE_FILE.exists():
        try:
            pid_content = SAMANTHA_ACTIVE_FILE.read_text().strip()
            if pid_content:
                recorded_pid = int(pid_content)
                if recorded_pid != os.getpid():
                    try:
                        os.kill(recorded_pid, signal.SIGTERM)
                        logger.info("Sent SIGTERM to recorded PID: %d", recorded_pid)
                        time.sleep(0.2)
                        try:
                            os.kill(recorded_pid, 0)  # Check if still alive
                            os.kill(recorded_pid, signal.SIGKILL)
                            logger.info("Sent SIGKILL to recorded PID: %d", recorded_pid)
                        except ProcessLookupError:
                            pass  # Already dead, good
                    except (ProcessLookupError, PermissionError):
                        pass
        except (ValueError, Exception) as e:
            logger.debug("Could not kill recorded PID: %s", e)

    state._thread_stop_flag = True

    # Interrupt any ongoing TTS playback
    playback._tts_interrupt = True
    playback._tts_playing = False

    with playback._tts_queue_lock:
        playback._tts_text_queue.clear()

    # Try graceful shutdown first
    if state._samantha_thread and state._samantha_thread.is_alive():
        state._samantha_thread.join(timeout=2.0)

        # If still alive, force close the audio stream
        if state._samantha_thread.is_alive() and state._audio_stream:
            try:
                state._audio_stream.stop()
                state._audio_stream.close()
                logger.info("Force closed audio stream")
            except Exception as e:
                logger.debug("Error closing audio stream: %s", e)
            state._samantha_thread.join(timeout=1.0)

        # Clear thread reference only if it's actually stopped
        if not state._samantha_thread.is_alive():
            state._samantha_thread = None

    state._audio_stream = None
    SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)

    # Clean up any remaining orphan processes
    kill_orphaned_processes()

    return "🛑 Samantha stopped"


def _speak_direct(text: str) -> bool:
    """Run the blocking TTS path, serialized so two speaks never overlap.

    refresh_audio_devices() tears PortAudio down and back up, which would
    invalidate a stream another concurrent speak still holds open, so the
    refresh and the playback must happen under the same lock.
    """
    with playback._direct_speak_lock:
        playback.refresh_audio_devices()
        return playback.speak_tts_sync(text)


@mcp.tool()
async def samantha_speak(text: str) -> str:
    """Speak text via Samantha TTS."""
    try:
        playback._last_tts_text = text

        if state._samantha_thread and state._samantha_thread.is_alive():
            with playback._tts_queue_lock:
                playback._tts_text_queue.append(text)
            return f"🔊 Spoke: {text}"

        logger.info("Samantha not running, speaking directly")

        if not await ensure_kokoro_running():
            return "❌ TTS failed: Kokoro TTS is not running and could not be started"

        try:
            success = await asyncio.wait_for(
                asyncio.to_thread(_speak_direct, text),
                timeout=playback.TTS_TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            playback._tts_interrupt = True
            logger.error(
                "TTS exceeded %.0fs - abandoning playback so the server stays responsive",
                playback.TTS_TOTAL_TIMEOUT,
            )
            return f"❌ TTS timed out after {playback.TTS_TOTAL_TIMEOUT:.0f}s"

        if success:
            return f"🔊 Spoke: {text}"
        return "❌ TTS failed: check Kokoro TTS and the audio output device"
    except Exception as e:
        return f"❌ TTS failed: {e}"


samantha_speak.__doc__ = (
    f"Speak text via Samantha TTS.\n\n"
    f"Use this tool to reply to voice messages (those starting with "
    f"{VOICE_MESSAGE_PREFIX}). The persona, rules, and identity for the "
    f"active profile are injected per-message via the <system-reminder> "
    f"suffix on the user's voice prompt — read that, not this docstring, "
    f"for character behavior.\n\n"
    f"Do NOT call this tool for typed (non-voice) messages.\n\n"
    f"Args:\n    text: Text to speak\n\n"
    f"Returns:\n    Status message"
)


@mcp.tool()
async def samantha_status() -> str:
    """Check Samantha status.

    Returns:
        JSON status
    """
    active = False
    pid = None

    if SAMANTHA_ACTIVE_FILE.exists():
        try:
            pid = int(SAMANTHA_ACTIVE_FILE.read_text().strip())
        except (ValueError, OSError):
            pid = None
        if pid is not None:
            try:
                os.kill(pid, 0)
                active = True
            except PermissionError:
                active = True
            except (ProcessLookupError, OSError):
                logger.info("Clearing stale lock file for dead PID %s", pid)
                SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)
                pid = None

    return json.dumps({
        "active": active,
        "pid": pid,
        "kokoro": await _check_service_health("http://localhost:8880/health"),
        "whisper": await _check_service_health("http://localhost:2022/health"),
        "wake_words": get_wake_words()[:5],
        "log_file": str(CONVERSATION_LOG)
    })
