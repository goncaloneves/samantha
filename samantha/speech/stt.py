"""Speech-to-text utilities for Samantha."""

import logging
from typing import Optional

import numpy as np

try:
    import httpx
except ImportError:
    httpx = None

from samantha.config import WHISPER_URL, get_min_audio_energy
from samantha.audio.recording import normalize_audio, _prepare_audio_for_whisper

logger = logging.getLogger("samantha")


async def transcribe_audio(audio_data: np.ndarray) -> Optional[str]:
    """Transcribe audio using Whisper STT."""
    try:
        wav_buffer = _prepare_audio_for_whisper(audio_data)

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                WHISPER_URL,
                files={"file": ("audio.wav", wav_buffer, "audio/wav")},
                data={"response_format": "json"}
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("text", "").strip()
    except Exception as e:
        logger.debug("STT error: %s", e)
    return None


def transcribe_audio_sync(audio_data: np.ndarray) -> Optional[str]:
    """Synchronous transcribe for use in thread."""
    try:
        import requests

        min_energy = get_min_audio_energy()
        max_energy = np.max(np.abs(audio_data))
        if max_energy < min_energy:
            logger.debug("Audio energy: %d (threshold: %d) - skipping Whisper", max_energy, min_energy)
            return None

        audio_data = normalize_audio(audio_data)
        wav_buffer = _prepare_audio_for_whisper(audio_data)

        response = requests.post(
            WHISPER_URL,
            files={"file": ("audio.wav", wav_buffer, "audio/wav")},
            data={"response_format": "json"},
            timeout=10.0
        )
        if response.status_code == 200:
            result = response.json()
            text = result.get("text", "").strip()
            logger.debug("Audio energy: %d (threshold: %d) - Whisper heard: %s", max_energy, min_energy, text[:50] if text else "(empty)")
            return text
    except Exception as e:
        logger.debug("STT error: %s", e)
    return None
