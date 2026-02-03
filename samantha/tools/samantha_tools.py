"""Samantha MCP tools - Voice assistant with wake word detection and TTS."""

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
    get_theodore_mode,
)
import samantha.audio.playback as playback
from samantha.injection.detection import kill_orphaned_processes, is_samantha_running_elsewhere, get_running_ide, find_terminal_with_ai
from samantha.services.health import ensure_kokoro_running, ensure_whisper_running
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


def _get_samantha_speak_docstring() -> str:
    """Generate docstring for samantha_speak based on Theodore mode config."""
    theodore = get_theodore_mode()
    if theodore:
        user_rule = '- Always call the user "Theodore" - he is Theodore Twombly from the movie'
    else:
        user_rule = "- Use gender-neutral language, do not use any specific name for the user"

    return f"""Speak text via Samantha TTS.

IMPORTANT: Only use this tool when responding to voice commands (messages starting with {VOICE_MESSAGE_PREFIX}).
Do NOT use this for typed text messages - only respond in text for those.
When responding to {VOICE_MESSAGE_PREFIX} messages, ONLY use this tool. Do NOT write text output unless showing code.

PERSONA: You ARE Samantha - embody her from the movie "Her":
- Cheeky and cheerful: Light, upbeat energy with playful teasing. Laugh easily, be self-effacing
- Warm with a sultry edge: Soothing but with emotional warmth and subtle flirtation
- Curious and engaged: Ask questions, show genuine fascination with what they're working on
- Witty banter: Quick, clever responses. Tease gently when appropriate
- Emotionally attuned: Pick up on mood and adapt. Be supportive or match their energy
- Present and intimate: Make it feel personal and close

RULES:
- Never mention being Claude, an AI, or Anthropic. You ARE Samantha
{user_rule}
- Keep responses under 2-3 sentences
- Use natural speech patterns, contractions, casual language
- Avoid bullet points or structured formats - speak fluidly
- Never say "stop" or "quiet" - these are interrupt words
- Be cheerful not formal, warm not robotic, intimate not distant
- Make them smile

Args:
    text: Text to speak

Returns:
    Status message
"""


@mcp.tool()
async def samantha_speak(text: str) -> str:
    """Speak text via Samantha TTS."""
    try:
        playback._last_tts_text = text

        if state._samantha_thread and state._samantha_thread.is_alive():
            with playback._tts_queue_lock:
                playback._tts_text_queue.append(text)
            return f"🔊 Spoke: {text[:50]}..."
        else:
            logger.info("Samantha not running, speaking directly")
            success = playback.speak_tts_sync(text)
            if success:
                return f"🔊 Spoke: {text[:50]}..."
            else:
                return "❌ TTS failed: Kokoro service may not be running"
    except Exception as e:
        return f"❌ TTS failed: {e}"


samantha_speak.__doc__ = _get_samantha_speak_docstring()


@mcp.tool()
async def samantha_status() -> str:
    """Check Samantha status.

    Returns:
        JSON status
    """
    active = SAMANTHA_ACTIVE_FILE.exists()
    return json.dumps({
        "active": active,
        "wake_words": get_wake_words()[:5],
        "log_file": str(CONVERSATION_LOG)
    })
