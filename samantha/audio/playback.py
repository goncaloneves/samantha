"""Audio playback utilities for Samantha."""

import logging
import platform
import shutil
import subprocess
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
    """Speak text using Kokoro TTS with PCM streaming directly to sounddevice.

    Can be interrupted by setting _tts_interrupt = True.
    """
    global _last_tts_text, _last_tts_time, _tts_interrupt
    logger.info("🔊 TTS: %s", text[:80] + "..." if len(text) > 80 else text)

    _last_tts_text = text
    _last_tts_time = time.time()
    _tts_interrupt = False

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
    except Exception as e:
        logger.error("TTS error: %s", e)
        return False
    finally:
        if stream:
            try:
                stream.close()
            except Exception:
                pass
        _tts_interrupt = False


async def speak_tts(text: str) -> bool:
    """Speak text using Kokoro TTS."""
    return speak_tts_sync(text)


def play_chime():
    """Play activation chime sound (cross-platform)."""
    if platform.system() == "Darwin":
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Glass.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    elif platform.system() == "Linux":
        for player in ["paplay", "pw-play", "aplay"]:
            if shutil.which(player):
                subprocess.Popen(
                    [player, "/usr/share/sounds/freedesktop/stereo/service-login.oga"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                break
    elif platform.system() == "Windows":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


def play_goodbye_chime():
    """Play deactivation chime sound (cross-platform)."""
    if platform.system() == "Darwin":
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Submarine.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    elif platform.system() == "Linux":
        for player in ["paplay", "pw-play", "aplay"]:
            if shutil.which(player):
                subprocess.Popen(
                    [player, "/usr/share/sounds/freedesktop/stereo/service-logout.oga"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                break
    elif platform.system() == "Windows":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass
