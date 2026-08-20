"""Regression tests for the second audit batch.

Covers: blind keystroke injection when app activation fails, TTS playback flags
being clobbered by a stale worker, a capture stream that dies unnoticed,
transcription failures that looked like silence, and status strings that
reported success on every path.
"""

import asyncio
import logging
import threading
import time
from unittest.mock import AsyncMock

import numpy as np
import pytest

import samantha.audio.playback as playback
import samantha.core.loop as loop
import samantha.core.state as state
import samantha.injection.inject as inject
import samantha.speech.stt as stt
import samantha.tools.samantha_tools as tools


def _speak():
    return getattr(tools.samantha_speak, "fn", tools.samantha_speak)


def _stop():
    return getattr(tools.samantha_stop, "fn", tools.samantha_stop)


@pytest.fixture
def restore_state():
    # getattr defaults so the fixture works against a build missing the guard,
    # letting the test fail on behaviour rather than erroring at setup.
    saved = (state._samantha_thread,
             getattr(playback, "_tts_generation", 0),
             playback._tts_playing)
    yield
    state._samantha_thread = saved[0]
    if hasattr(playback, "_tts_generation"):
        playback._tts_generation = saved[1]
    playback._tts_playing = saved[2]
    with playback._tts_queue_lock:
        playback._tts_text_queue.clear()


# --------------------------- blind injection ---------------------------

def test_injection_aborts_when_the_ide_cannot_be_activated(mocker):
    """Discarding activate_app's result sent paste+Enter into whatever app
    happened to be focused - including a source file."""
    mocker.patch.object(inject, "PLATFORM", "Darwin")
    mocker.patch.object(inject, "activate_app", return_value=False)
    run = mocker.patch.object(inject.subprocess, "run")

    assert inject.focus_ide_ai_input("Cursor") is False
    run.assert_not_called(), "sent keystrokes despite failing to activate the target app"


def test_injection_proceeds_when_activation_succeeds(mocker):
    """Both directions: the guard must not block the healthy path."""
    mocker.patch.object(inject, "PLATFORM", "Darwin")
    mocker.patch.object(inject, "activate_app", return_value=True)
    mocker.patch.object(inject.time, "sleep")
    run = mocker.patch.object(inject.subprocess, "run")

    assert inject.focus_ide_ai_input("Cursor") is True
    assert run.called, "healthy activation path no longer sends the focus keystroke"


# --------------------------- TTS generation ownership ---------------------------

def test_a_stale_tts_worker_cannot_clear_the_current_utterance(restore_state):
    """The outgoing worker's finally block used to reset the flags
    unconditionally, unmasking the mic while the NEXT utterance was playing -
    so Samantha heard herself and interrupted her own speech."""
    first = playback.claim_tts_generation()
    second = playback.claim_tts_generation()

    assert playback.release_tts_generation(first) is False, (
        "a superseded utterance was allowed to clear live playback state"
    )
    assert playback._tts_playing is True, "live playback was marked finished by a stale worker"

    assert playback.release_tts_generation(second) is True
    assert playback._tts_playing is False, "the owning utterance failed to clear its own state"


def test_generation_release_is_safe_under_concurrency(restore_state):
    gen = playback.claim_tts_generation()
    results = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        results.append(playback.release_tts_generation(gen))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # Releasing the CURRENT generation repeatedly is harmless; what must never
    # happen is a superseded generation clearing it, which the test above pins.
    assert all(results), f"the owning generation was rejected: {results}"
    assert playback._tts_playing is False


# --------------------------- capture stream liveness ---------------------------

def test_a_dead_capture_stream_is_detected():
    """Losing the mic stopped the callback firing; the loop then span forever on
    an empty queue with no capture, no error and no recovery."""
    class _Dead:
        active = False

    class _Live:
        active = True

    class _Exploding:
        @property
        def active(self):
            raise RuntimeError("PortAudio gone")

    assert loop._input_stream_is_live(_Dead()) is False
    assert loop._input_stream_is_live(_Live()) is True
    assert loop._input_stream_is_live(_Exploding()) is False


# --------------------------- transcription visibility ---------------------------

def test_a_whisper_outage_is_reported_not_swallowed(mocker, caplog):
    """Every failure path returned None behind a debug log - identical to what
    'the user said nothing' returns."""
    stt._consecutive_failures = 0
    mocker.patch.object(stt, "get_min_audio_energy", return_value=0)
    mocker.patch.object(stt, "normalize_audio", side_effect=lambda a: a)
    mocker.patch.object(stt, "_prepare_audio_for_whisper", return_value=b"wav")

    class _Resp:
        status_code = 503
        text = "model loading"

    import requests
    mocker.patch.object(requests, "post", return_value=_Resp())

    with caplog.at_level(logging.ERROR, logger="samantha"):
        result = stt.transcribe_audio_sync(np.full(480, 5000, dtype=np.int16))

    assert result is None
    assert any("Transcription failed" in r.getMessage() for r in caplog.records), (
        "a Whisper 503 produced no error-level log - indistinguishable from silence"
    )


def test_repeated_transcription_failures_escalate(mocker, caplog):
    stt._consecutive_failures = 0
    with caplog.at_level(logging.ERROR, logger="samantha"):
        for _ in range(stt.FAILURES_BEFORE_SERVICE_WARNING):
            stt._note_transcription_failure("boom")
    assert any("is NOT being transcribed" in r.getMessage() for r in caplog.records), (
        "a sustained STT outage never escalated"
    )


def test_a_successful_transcription_resets_the_failure_counter():
    stt._consecutive_failures = 5
    stt._note_transcription_ok()
    assert stt._consecutive_failures == 0


# --------------------------- honest status strings ---------------------------

async def test_stop_reports_a_thread_that_refused_to_die(mocker, restore_state):
    """It returned the same success string on every path."""
    stuck = mocker.MagicMock()
    stuck.is_alive.return_value = True
    state._samantha_thread = stuck
    mocker.patch.object(tools, "SAMANTHA_ACTIVE_FILE", mocker.MagicMock(exists=lambda: False))
    mocker.patch.object(tools, "kill_orphaned_processes", return_value=0)

    result = await _stop()()

    assert "⚠️" in result and "did not exit" in result, (
        f"a stuck listening thread still reported a clean stop: {result!r}"
    )


async def test_start_does_not_block_the_event_loop_while_waiting_for_audio(mocker, restore_state):
    """threading.Event.wait is not a suspension point; awaiting it inline pinned
    the stdio loop for the full timeout on every audio-open failure."""
    state._samantha_thread = None
    mocker.patch.object(tools, "is_samantha_running_elsewhere", return_value=False)
    mocker.patch.object(tools, "kill_orphaned_processes", return_value=0)
    mocker.patch.object(tools, "ensure_kokoro_running", new=AsyncMock(return_value=True))
    mocker.patch.object(tools, "ensure_whisper_running", new=AsyncMock(return_value=True))
    mocker.patch.object(tools, "THREAD_READY_TIMEOUT", 0.6)
    # NB: do NOT patch threading.Thread here - tools.threading is the real
    # threading module, and asyncio.to_thread's executor needs it to spawn
    # workers, so patching it deadlocks the very call under test. Neuter the
    # loop body instead and let a real (immediately-returning) thread start.
    mocker.patch.object(tools, "samantha_loop_thread", lambda: None)
    mocker.patch.object(tools, "SAMANTHA_ACTIVE_FILE", mocker.MagicMock(
        exists=lambda: False, write_text=lambda _: None, unlink=lambda **k: None))
    mocker.patch.object(tools, "SAMANTHA_DIR", mocker.MagicMock())
    # Unmocked, these shell out to ~21 osascript probes at 5s each - the very
    # blocking this test is about, and enough to hang the suite.
    mocker.patch.object(tools, "get_running_ide", return_value="Cursor")
    mocker.patch.object(tools, "find_terminal_with_ai", return_value="")

    start = getattr(tools.samantha_start, "fn", tools.samantha_start)
    task = asyncio.create_task(start())
    ticks = 0
    while not task.done():
        await asyncio.sleep(0.05)
        ticks += 1
    await task

    assert ticks >= 4, (
        f"event loop ticked only {ticks} times during a 0.6s readiness wait - "
        "the wait is still pinning the loop"
    )
