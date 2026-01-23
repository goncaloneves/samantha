"""Main Samantha voice assistant loop."""

import logging
import threading
import time

import numpy as np
import sounddevice as sd

try:
    import webrtcvad
    VAD_AVAILABLE = True
except ImportError:
    webrtcvad = None
    VAD_AVAILABLE = False

from samantha.config import (
    SAMPLE_RATE,
    CHANNELS,
    VOICE_MESSAGE_PREFIX,
    get_input_device,
)
from samantha.audio.recording import _clear_queue
import samantha.audio.playback as playback
from samantha.audio.processing import (
    is_echo,
    get_active_interrupt_words,
    contains_interrupt_phrase,
    contains_skip_phrase,
)
from samantha.speech.stt import transcribe_audio_sync
from samantha.utils.text import (
    check_for_deactivation,
    clean_command,
    contains_trigger_word,
    sanitize_whisper_text,
    is_noise,
)
from samantha.utils.logging import log_conversation
from samantha.injection.inject import inject_into_app
import samantha.core.state as state

logger = logging.getLogger("samantha")


def samantha_loop_thread():
    """Main Samantha voice assistant loop running in a dedicated thread."""
    global _thread_stop_flag, _tts_playing, _last_tts_time, _tts_start_time, _tts_interrupt, _thread_ready, _tts_done_event, _tts_text_queue

    input_dev = get_input_device()
    try:
        device_info = sd.query_devices(input_dev)
        device_name = device_info['name']
    except Exception:
        device_name = f"device {input_dev}"

    logger.info("🎧 Samantha thread started (VAD: %s, mic: %s)", "enabled" if VAD_AVAILABLE else "disabled", device_name)
    logger.info("   Say 'Hey Samantha' to activate")

    import queue
    from scipy import signal as scipy_signal

    VAD_CHUNK_DURATION_MS = 30
    SILENCE_THRESHOLD_MS = 1000
    MIN_RECORDING_DURATION = 0.3
    INITIAL_SILENCE_GRACE_PERIOD = 1.0
    VAD_SAMPLE_RATE = 16000
    VAD_AGGRESSIVENESS_LISTENING = 1
    VAD_AGGRESSIVENESS_TTS = 1
    MAX_INACTIVE_AUDIO_MS = 15000

    chunk_samples = int(SAMPLE_RATE * VAD_CHUNK_DURATION_MS / 1000)
    vad_chunk_samples = int(VAD_SAMPLE_RATE * VAD_CHUNK_DURATION_MS / 1000)
    silence_timeout = 1800.0

    is_active = False
    last_speech_time = 0
    audio_chunks = []
    silence_duration_ms = 0
    speech_detected = False
    recording_start = 0

    audio_queue = queue.Queue()

    def audio_callback(indata, frames, callback_time, status):
        if status:
            logger.warning("Audio stream status: %s", status)
        audio_queue.put(indata.copy())

    try:
        vad = webrtcvad.Vad(VAD_AGGRESSIVENESS_LISTENING) if VAD_AVAILABLE else None
        vad_tts = webrtcvad.Vad(VAD_AGGRESSIVENESS_TTS) if VAD_AVAILABLE else None

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=np.int16,
                           callback=audio_callback, blocksize=chunk_samples, device=get_input_device()) as stream:
            state._audio_stream = stream
            logger.debug("Started continuous audio stream")

            if state._thread_ready:
                state._thread_ready.set()

            while not state._thread_stop_flag:
                try:
                    tts_text = None
                    if not playback._tts_playing:
                        with playback._tts_queue_lock:
                            if playback._tts_text_queue:
                                tts_text = playback._tts_text_queue.pop(0)

                    if tts_text:
                        playback._tts_playing = True
                        playback._tts_start_time = time.time()
                        state._tts_done_event = threading.Event()

                        _clear_queue(audio_queue)
                        speech_detected = False
                        audio_chunks = []
                        silence_duration_ms = 0
                        recording_start = 0

                        done_event = state._tts_done_event
                        tts_text_to_speak = tts_text

                        def tts_thread_func():
                            playback._last_tts_text = tts_text_to_speak
                            active_words = get_active_interrupt_words()
                            logger.info("🎯 Active interrupt words: %s", active_words)
                            try:
                                playback.speak_tts_sync(tts_text_to_speak)
                            finally:
                                playback._last_tts_time = time.time()
                                playback._tts_start_time = 0
                                playback._tts_playing = False
                                done_event.set()

                        tts_thread = threading.Thread(target=tts_thread_func, daemon=True)
                        tts_thread.start()

                    if playback._tts_playing:
                        try:
                            chunk = audio_queue.get(timeout=0.05)
                            chunk_flat = chunk.flatten()

                            is_speech_chunk = False
                            if vad_tts:
                                resampled_length = int(len(chunk_flat) * VAD_SAMPLE_RATE / SAMPLE_RATE)
                                vad_chunk = scipy_signal.resample(chunk_flat, resampled_length)
                                vad_chunk = vad_chunk[:vad_chunk_samples].astype(np.int16)
                                try:
                                    is_speech_chunk = vad_tts.is_speech(vad_chunk.tobytes(), VAD_SAMPLE_RATE)
                                except Exception:
                                    is_speech_chunk = False

                            if is_speech_chunk:
                                audio_chunks.append(chunk_flat)

                            accumulated_duration_ms = len(audio_chunks) * VAD_CHUNK_DURATION_MS
                            tts_elapsed = time.time() - playback._tts_start_time if playback._tts_start_time > 0 else 0
                            if accumulated_duration_ms >= 300 and tts_elapsed >= 2.0:
                                full_audio = np.concatenate(audio_chunks)
                                raw_text = transcribe_audio_sync(full_audio)
                                text = sanitize_whisper_text(raw_text) if raw_text else ""

                                is_interrupt = text and contains_interrupt_phrase(text)
                                is_skip = text and contains_skip_phrase(text)

                                if is_interrupt or is_skip:
                                    is_skip_only = is_skip and not is_interrupt
                                    logger.info("🔍 TTS interrupt check: %s", text[:50])
                                    if is_skip_only:
                                        logger.info("⏭️ Skip to next detected: %s", text[:50])
                                        log_conversation("SKIP", text)
                                    else:
                                        logger.info("🛑 Interrupt detected: %s", text[:50])
                                        log_conversation("INTERRUPT", text)
                                        with playback._tts_queue_lock:
                                            playback._tts_text_queue.clear()
                                    playback._tts_interrupt = True
                                    playback._tts_playing = False
                                    time.sleep(0.1)
                                    _clear_queue(audio_queue)
                                    if is_skip_only:
                                        playback.play_sound("skip")
                                    else:
                                        playback.play_sound("stop")
                                    if is_active:
                                        last_speech_time = time.time()

                                audio_chunks = []

                        except queue.Empty:
                            pass
                        continue

                    if playback._last_tts_time > 0:
                        logger.debug("🧹 Post-TTS cleanup: clearing audio queue and buffers")
                        _clear_queue(audio_queue)
                        speech_detected = False
                        audio_chunks = []
                        silence_duration_ms = 0
                        recording_start = 0
                        playback._last_tts_time = 0
                        if is_active:
                            last_speech_time = time.time()
                        continue

                    if is_active:
                        silence_elapsed = time.time() - last_speech_time
                        if silence_elapsed > silence_timeout:
                            logger.info("⏰ 30min silence - returning to idle")
                            is_active = False
                            playback.play_sound("timeout")

                    try:
                        chunk = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    chunk_flat = chunk.flatten()
                    audio_chunks.append(chunk_flat)

                    if not is_active:
                        max_chunks = MAX_INACTIVE_AUDIO_MS // VAD_CHUNK_DURATION_MS
                        if len(audio_chunks) > max_chunks:
                            audio_chunks = audio_chunks[-max_chunks:]

                    is_speech = False
                    if vad:
                        resampled_length = int(len(chunk_flat) * VAD_SAMPLE_RATE / SAMPLE_RATE)
                        vad_chunk = scipy_signal.resample(chunk_flat, resampled_length)
                        vad_chunk = vad_chunk[:vad_chunk_samples].astype(np.int16)
                        try:
                            is_speech = vad.is_speech(vad_chunk.tobytes(), VAD_SAMPLE_RATE)
                        except Exception:
                            is_speech = True
                    else:
                        is_speech = True

                    if not speech_detected:
                        if is_speech:
                            logger.info("🎙️ Speech detected, starting active recording")
                            speech_detected = True
                            recording_start = time.time()
                            silence_duration_ms = 0
                            audio_chunks = [chunk_flat]
                    else:
                        if is_speech:
                            silence_duration_ms = 0
                        else:
                            silence_duration_ms += VAD_CHUNK_DURATION_MS

                            recording_duration = time.time() - recording_start
                            past_grace_period = recording_duration >= INITIAL_SILENCE_GRACE_PERIOD
                            if recording_duration >= MIN_RECORDING_DURATION and silence_duration_ms >= SILENCE_THRESHOLD_MS and past_grace_period:
                                logger.info("✓ Silence threshold reached after %.1fs", recording_duration)

                                if not playback._tts_playing and audio_chunks:
                                    full_audio = np.concatenate(audio_chunks)
                                    text = transcribe_audio_sync(full_audio)

                                    if text:
                                        if is_echo(text):
                                            logger.debug("Filtered as echo: %s", text[:50])
                                        elif is_noise(text):
                                            logger.debug("Filtered as noise: %s", text[:50])

                                    if text and text not in ["[BLANK_AUDIO]", ""] and not is_echo(text) and not is_noise(text):
                                        logger.info("📝 Heard (active=%s): %s", is_active, text[:100])

                                        if is_active:
                                            if check_for_deactivation(text):
                                                logger.info("😴 Deactivating - returning to idle")
                                                is_active = False
                                                playback.play_sound("deactivate")
                                            else:
                                                logger.info("🟢 Active - sending to Claude")
                                                last_speech_time = time.time()
                                                cleaned = clean_command(text)
                                                if cleaned:
                                                    log_conversation("STT", cleaned)
                                                    inject_into_app(f"{VOICE_MESSAGE_PREFIX} {cleaned}")
                                        elif contains_trigger_word(text):
                                            logger.info("✨ Activated!")
                                            is_active = True
                                            last_speech_time = time.time()
                                            playback.play_sound("activate")
                                            cleaned = clean_command(text)
                                            if cleaned:
                                                log_conversation("STT", cleaned)
                                                inject_into_app(f"{VOICE_MESSAGE_PREFIX} {cleaned}")
                                        else:
                                            logger.debug("No trigger - discarding")

                                speech_detected = False
                                audio_chunks = []
                                silence_duration_ms = 0

                except Exception as e:
                    logger.error("Loop error: %s", e)
                    time.sleep(0.1)

    except Exception as e:
        logger.error("Stream error: %s", e)
    finally:
        state._audio_stream = None

    logger.info("🛑 Samantha thread stopped")
