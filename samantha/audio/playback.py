"""Audio playback utilities for Samantha."""

import logging
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time

import numpy as np
import sounddevice as sd

from samantha.config import KOKORO_URL, SYSTEM_PLAYER_TIMEOUT, get_voice, get_output_device
from samantha.utils.logging import log_conversation

logger = logging.getLogger("samantha")

_tts_text_queue = []
_tts_queue_lock = threading.Lock()
_last_tts_text = ""
_last_tts_time = 0
_tts_playing = False
_tts_start_time = 0
_tts_interrupt = False
_post_tts_pending = False

_active_streams = 0
_active_streams_lock = threading.Lock()
_direct_speak_lock = threading.Lock()
_tts_state_lock = threading.Lock()
_tts_generation = 0

TTS_STALL_TIMEOUT = float(os.getenv("SAMANTHA_TTS_STALL_TIMEOUT", "15"))
TTS_MIN_TOTAL_TIMEOUT = float(os.getenv("SAMANTHA_TTS_TOTAL_TIMEOUT", "180"))

# Measured against Kokoro: synthesis yields ~18 characters of input per second of
# audio, flat across utterance length. 15 is the conservative direction (it
# over-estimates the duration), and playback runs in real time, so the ceiling
# has to scale with the text or long-form speech is cut off mid-sentence.
TTS_CHARS_PER_SECOND_OF_AUDIO = 15.0
TTS_TIMEOUT_SAFETY_FACTOR = 3.0
TTS_TIMEOUT_OVERHEAD = 30.0


def tts_timeout_for(text: str) -> float:
    """Wall-clock ceiling for speaking ``text``.

    This is a BACKSTOP, not a pacing mechanism: liveness is already guaranteed by
    the stall watchdog, which aborts within TTS_STALL_TIMEOUT of the device
    ceasing to accept data. A fixed ceiling here is a false-positive generator -
    it truncated healthy speech longer than ~3200 characters while the watchdog
    correctly reported that playback was progressing normally.
    """
    estimated_audio_seconds = len(text) / TTS_CHARS_PER_SECOND_OF_AUDIO
    return max(
        TTS_MIN_TOTAL_TIMEOUT,
        estimated_audio_seconds * TTS_TIMEOUT_SAFETY_FACTOR + TTS_TIMEOUT_OVERHEAD,
    )


def claim_tts_generation() -> int:
    """Mark a new utterance as the current one and return its generation token."""
    global _tts_generation, _tts_playing, _tts_start_time
    with _tts_state_lock:
        _tts_generation += 1
        _tts_playing = True
        _tts_start_time = time.time()
        return _tts_generation


def release_tts_generation(generation: int) -> bool:
    """Clear the playing flags, but only if this utterance is still the current one.

    A slow outgoing worker used to reset _tts_playing unconditionally in its
    finally block. If the next utterance had already started, that cleared the
    flags out from under live playback, so the loop stopped masking the
    microphone against Samantha's own voice and treated her as the speaker.
    Returns whether this call owned the state.
    """
    global _tts_playing, _tts_start_time, _last_tts_time, _post_tts_pending
    with _tts_state_lock:
        if _tts_generation != generation:
            return False
        _last_tts_time = time.time()
        _tts_start_time = 0
        _tts_playing = False
        _post_tts_pending = True
        return True


def refresh_audio_devices() -> None:
    """Re-enumerate audio devices so playback/recording bind to the CURRENT default.

    PortAudio (under sounddevice) snapshots the device list once, when it first
    initializes in the process — on every platform (macOS CoreAudio, Windows
    WASAPI/MME, Linux ALSA/PulseAudio). A device connected after the MCP server
    started (e.g. Bluetooth headphones plugged in mid-session) is therefore
    invisible, and ``device=None`` keeps resolving to whatever was default at
    startup. Tearing PortAudio down and back up rebuilds the list so the next
    stream follows the user's current default input/output.

    MUST only be called when no PortAudio stream is open in this process (i.e.
    before the listening loop opens its input stream, or on the standalone speak
    path when the loop is not running). Calling it while a stream is live would
    invalidate that stream.
    """
    with _active_streams_lock:
        if _active_streams > 0:
            logger.warning(
                "Skipping audio device refresh - %d PortAudio stream(s) still open",
                _active_streams,
            )
            return

    try:
        sd._terminate()
    except Exception:
        pass
    try:
        sd._initialize()
    except Exception as e:
        logger.warning("Could not refresh audio devices: %s", e)


def speak_tts_sync(text: str) -> bool:
    """Speak text using Kokoro TTS. Falls back to system player if sounddevice fails.

    Falls back to: afplay (macOS), paplay/pw-play/aplay (Linux), winsound (Windows).
    Can be interrupted by setting _tts_interrupt = True (sounddevice only).
    """
    global _last_tts_text, _last_tts_time, _tts_interrupt
    logger.info("🔊 TTS: %s", text[:80] + "..." if len(text) > 80 else text)

    _last_tts_text = text
    _last_tts_time = time.time()
    _tts_interrupt = False

    # Try sounddevice first (streaming, interruptible)
    try:
        return _speak_with_sounddevice(text)
    except sd.PortAudioError as e:
        logger.warning("⚠️ sounddevice failed, falling back to system player: %s", e)
        return _speak_with_system_player(text)
    except Exception as e:
        error_str = str(e)
        if "PortAudio" in error_str or "sounddevice" in error_str.lower():
            logger.warning("⚠️ sounddevice failed, falling back to system player: %s", e)
            return _speak_with_system_player(text)
        logger.error("TTS error: %s", e)
        return False


def _speak_with_sounddevice(text: str) -> bool:
    """Speak using sounddevice with PCM streaming (interruptible).

    The write loop is watchdogged. ``sd.OutputStream.write()`` blocks until the
    device drains and carries no timeout of its own, so a stalled, switched or
    disconnected output device would otherwise block this thread forever - and,
    when called from the MCP tool, the whole event loop with it.
    """
    global _tts_interrupt, _active_streams

    stream = None
    interrupted = False
    counted = False
    last_progress = [time.time()]
    watchdog_done = threading.Event()
    stalled = threading.Event()

    try:
        import requests

        stream = sd.OutputStream(
            device=get_output_device(),
            samplerate=24000,
            channels=1,
            dtype='int16',
            blocksize=1024,
            latency='low'
        )
        stream.start()

        with _active_streams_lock:
            _active_streams += 1
            counted = True

        def _watchdog():
            while not watchdog_done.wait(1.0):
                if time.time() - last_progress[0] > TTS_STALL_TIMEOUT:
                    stalled.set()
                    logger.error(
                        "TTS stalled >%.0fs on the audio output device - aborting stream",
                        TTS_STALL_TIMEOUT,
                    )
                    try:
                        stream.abort()
                    except Exception:
                        pass
                    return

        threading.Thread(target=_watchdog, daemon=True).start()

        with requests.post(
            KOKORO_URL,
            json={
                "model": "kokoro",
                "input": text,
                "voice": get_voice(),
                "response_format": "pcm",
                "stream": True
            },
            timeout=60.0,
            stream=True
        ) as response:
            if response.status_code != 200:
                logger.error("TTS error: HTTP %s", response.status_code)
                return False

            try:
                for chunk in response.iter_content(chunk_size=1024):
                    if _tts_interrupt:
                        logger.info("🛑 TTS interrupted by user - aborting stream")
                        interrupted = True
                        stream.abort()
                        break
                    if chunk:
                        audio_array = np.frombuffer(chunk, dtype=np.int16)
                        stream.write(audio_array)
                        last_progress[0] = time.time()
            except Exception:
                if not stalled.is_set():
                    raise

        if stalled.is_set():
            logger.error("TTS aborted: audio output device stopped accepting data")
            return False

        if not interrupted:
            stream.stop()
            log_conversation("TTS", text)
        return True
    finally:
        watchdog_done.set()
        if counted:
            with _active_streams_lock:
                _active_streams -= 1
        if stream:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        _tts_interrupt = False


def _speak_with_system_player(text: str) -> bool:
    """Fallback TTS using system audio player when sounddevice fails.

    Supports macOS (afplay), Linux (paplay/pw-play/aplay), and Windows (winsound).
    """
    import requests

    try:
        # Request WAV format for system player compatibility
        response = requests.post(
            KOKORO_URL,
            json={
                "model": "kokoro",
                "input": text,
                "voice": get_voice(),
                "response_format": "wav",
            },
            timeout=60.0
        )
        if response.status_code != 200:
            logger.error("TTS fallback error: HTTP %s", response.status_code)
            return False

        # Write to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(response.content)
            temp_path = f.name

        try:
            system = platform.system()

            if system == "Darwin":
                subprocess.run(["afplay", temp_path], check=True,
                               timeout=SYSTEM_PLAYER_TIMEOUT)
            elif system == "Linux":
                # Try available Linux audio players
                for player in ["paplay", "pw-play", "aplay", "ffplay"]:
                    if shutil.which(player):
                        cmd = [player, temp_path]
                        if player == "ffplay":
                            cmd = ["ffplay", "-nodisp", "-autoexit", temp_path]
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=SYSTEM_PLAYER_TIMEOUT)
                        break
                else:
                    logger.error("TTS fallback error: no audio player found on Linux")
                    return False
            elif system == "Windows":
                import winsound
                winsound.PlaySound(temp_path, winsound.SND_FILENAME)
            else:
                logger.error("TTS fallback error: unsupported platform %s", system)
                return False

            log_conversation("TTS", text)
            return True
        finally:
            os.unlink(temp_path)
    except Exception as e:
        logger.error("TTS fallback error: %s", e)
        return False


async def speak_tts(text: str) -> bool:
    """Speak text using Kokoro TTS."""
    return speak_tts_sync(text)


SOUNDS_DARWIN = {
    "activate": "/System/Library/Sounds/Funk.aiff",
    "deactivate": "/System/Library/Sounds/Bottle.aiff",
    "skip": "/System/Library/Sounds/Blow.aiff",
    "stop": "/System/Library/Sounds/Pop.aiff",
    "timeout": "/System/Library/Sounds/Submarine.aiff",
}

SOUNDS_LINUX = {
    "activate": "/usr/share/sounds/freedesktop/stereo/message.oga",
    "deactivate": "/usr/share/sounds/freedesktop/stereo/device-removed.oga",
    "skip": "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
    "stop": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
    "timeout": "/usr/share/sounds/freedesktop/stereo/power-unplug.oga",
}


def play_sound(sound_type: str):
    """Play a sound effect (cross-platform).

    Args:
        sound_type: One of "activate", "deactivate", "skip", "stop", "timeout"
    """
    system = platform.system()

    if system == "Darwin":
        sound_file = SOUNDS_DARWIN.get(sound_type)
        if sound_file:
            subprocess.Popen(
                ["afplay", sound_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    elif system == "Linux":
        sound_file = SOUNDS_LINUX.get(sound_type)
        if sound_file:
            for player in ["paplay", "pw-play", "aplay"]:
                if shutil.which(player):
                    subprocess.Popen(
                        [player, sound_file],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    break
    elif system == "Windows":
        try:
            import winsound
            sounds_win = {
                "activate": winsound.MB_ICONASTERISK,
                "deactivate": winsound.MB_OK,
                "skip": winsound.MB_ICONQUESTION,
                "stop": winsound.MB_ICONEXCLAMATION,
                "timeout": winsound.MB_ICONHAND,
            }
            beep_type = sounds_win.get(sound_type)
            if beep_type is not None:
                winsound.MessageBeep(beep_type)
        except Exception:
            pass
