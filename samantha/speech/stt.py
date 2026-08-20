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

FAILURES_BEFORE_SERVICE_WARNING = 3


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


_consecutive_failures = 0


def _note_transcription_ok() -> None:
    global _consecutive_failures
    _consecutive_failures = 0


def _note_transcription_failure(detail: str) -> None:
    """Report a transcription failure at error level.

    Every failure path used to return None behind a debug log, which is exactly
    what "the user said nothing" returns - so a Whisper outage was
    indistinguishable from silence and the assistant just appeared to ignore
    everything.
    """
    global _consecutive_failures
    _consecutive_failures += 1
    logger.error("Transcription failed (%d in a row): %s", _consecutive_failures, detail)
    if _consecutive_failures == FAILURES_BEFORE_SERVICE_WARNING:
        logger.error(
            "Whisper has failed %d times in a row - speech is NOT being transcribed. "
            "Check the STT service on %s", _consecutive_failures, WHISPER_URL
        )


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
            _note_transcription_ok()
            result = response.json()
            text = result.get("text", "").strip()
            logger.debug("Audio energy: %d (threshold: %d) - Whisper heard: %s", max_energy, min_energy, text[:50] if text else "(empty)")
            return text

        _note_transcription_failure(
            "Whisper returned HTTP %s: %s" % (response.status_code, response.text[:200])
        )
    except Exception as e:
        _note_transcription_failure("Whisper request failed: %s" % e)
    return None
