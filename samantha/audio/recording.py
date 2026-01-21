"""Audio recording utilities for Samantha."""

import io

import numpy as np

from samantha.config import SAMPLE_RATE, WHISPER_SAMPLE_RATE, CHANNELS


def normalize_audio(audio_data: np.ndarray, target_peak: int = 20000) -> np.ndarray:
    """Normalize audio to target peak level for better recognition with low-sensitivity mics."""
    peak = max(abs(audio_data.min()), abs(audio_data.max()))
    if peak < 100:
        return audio_data
    gain = min(target_peak / peak, 20.0)
    if gain > 1.5:
        normalized = np.clip(audio_data.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
        return normalized
    return audio_data


def _prepare_audio_for_whisper(audio_data: np.ndarray) -> io.BytesIO:
    """Convert audio data to WAV buffer for Whisper STT."""
    from pydub import AudioSegment
    audio = AudioSegment(
        audio_data.tobytes(),
        frame_rate=SAMPLE_RATE,
        sample_width=2,
        channels=CHANNELS
    )
    if SAMPLE_RATE != WHISPER_SAMPLE_RATE:
        audio = audio.set_frame_rate(WHISPER_SAMPLE_RATE)
    wav_buffer = io.BytesIO()
    audio.export(wav_buffer, format="wav")
    wav_buffer.seek(0)
    return wav_buffer


def _clear_queue(q) -> None:
    """Clear all items from a queue."""
    import queue
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break
