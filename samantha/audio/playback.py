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

from samantha.config import KOKORO_URL, get_voice, get_output_device
from samantha.utils.logging import log_conversation

logger = logging.getLogger("samantha")

_tts_text_queue = []
_tts_queue_lock = threading.Lock()
_last_tts_text = ""
_last_tts_time = 0
_tts_playing = False
_tts_start_time = 0
_tts_interrupt = False


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
    except Exception as e:
        error_str = str(e)
        if "PortAudio" in error_str or "sounddevice" in error_str.lower():
            logger.warning("⚠️ sounddevice failed, falling back to system player: %s", e)
            return _speak_with_system_player(text)
        logger.error("TTS error: %s", e)
        return False


def _speak_with_sounddevice(text: str) -> bool:
    """Speak using sounddevice with PCM streaming (interruptible)."""
    global _tts_interrupt

    stream = None
    interrupted = False
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

            for chunk in response.iter_content(chunk_size=1024):
                if _tts_interrupt:
                    logger.info("🛑 TTS interrupted by user - aborting stream")
                    interrupted = True
                    stream.abort()
                    break
                if chunk:
                    audio_array = np.frombuffer(chunk, dtype=np.int16)
                    stream.write(audio_array)

        if not interrupted:
            stream.stop()
            log_conversation("TTS", text)
        return True
    finally:
        if stream:
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
                subprocess.run(["afplay", temp_path], check=True)
            elif system == "Linux":
                # Try available Linux audio players
                for player in ["paplay", "pw-play", "aplay", "ffplay"]:
                    if shutil.which(player):
                        cmd = [player, temp_path]
                        if player == "ffplay":
                            cmd = ["ffplay", "-nodisp", "-autoexit", temp_path]
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
