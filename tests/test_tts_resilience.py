"""Regression tests for TTS hangs, service preflight and status truthfulness.

Every test here pins a failure that was observed live: Samantha wedging mid-TTS
and taking the whole MCP server down with it, a storm of "Connection refused"
because the direct-speak path never checked Kokoro, and a status tool that
reported a long-dead daemon as healthy.

Each test is written so it FAILS if the guard it covers is removed.
"""

import asyncio
import os
import pathlib
import tempfile
import threading
import time
from unittest.mock import AsyncMock

import pytest

import samantha.audio.playback as playback
import samantha.core.state as state
import samantha.tools.samantha_tools as tools


def _speak():
    return getattr(tools.samantha_speak, "fn", tools.samantha_speak)


def _status():
    return getattr(tools.samantha_status, "fn", tools.samantha_status)


@pytest.fixture
def restore_state():
    """Snapshot cross-test global state.

    Uses getattr defaults so the fixture itself still works against a build
    where a guard is missing - otherwise these tests would error at setup
    instead of failing on the behaviour they are meant to pin.
    """
    saved = (
        state._samantha_thread,
        getattr(playback, "_active_streams", 0),
        playback._tts_interrupt,
    )
    yield
    state._samantha_thread = saved[0]
    if hasattr(playback, "_active_streams"):
        playback._active_streams = saved[1]
    playback._tts_interrupt = saved[2]
    with playback._tts_queue_lock:
        playback._tts_text_queue.clear()


class _StalledStream:
    """An output device that accepts a stream but never drains it.

    This is the real-world case: Bluetooth headphones drop, or the default
    output switches mid-utterance, and ``write()`` blocks forever.
    """

    def __init__(self, *_, **__):
        self._aborted = threading.Event()
        self.abort_called = False

    def start(self):
        pass

    def write(self, _):
        self._aborted.wait()
        raise RuntimeError("stream aborted")

    def abort(self):
        self.abort_called = True
        self._aborted.set()

    def stop(self):
        pass

    def close(self):
        pass


def test_stalled_output_device_aborts_instead_of_hanging(mocker, monkeypatch, restore_state):
    """A device that never drains must not block the caller forever."""
    holder = {}
    mocker.patch.object(playback.sd, "OutputStream",
                        side_effect=lambda *a, **k: holder.setdefault("s", _StalledStream()))
    monkeypatch.setattr(playback, "TTS_STALL_TIMEOUT", 1.0, raising=False)

    chunks = [b"\x00\x01" * 512] * 50

    class _Resp:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def iter_content(self, chunk_size=1024):
            return iter(chunks)

    mocker.patch.object(playback, "KOKORO_URL", "http://localhost:8880/v1/audio/speech")
    import requests
    mocker.patch.object(requests, "post", return_value=_Resp())

    start = time.time()
    result = playback.speak_tts_sync("this utterance stalls on the device")
    elapsed = time.time() - start

    assert elapsed < 15, f"speak_tts_sync hung for {elapsed:.1f}s - the watchdog did not fire"
    assert holder["s"].abort_called, "watchdog never aborted the stalled stream"
    assert result is False, "a stalled device must be reported as failure, not success"
    assert playback._active_streams == 0, "stream refcount leaked after a stall"


async def test_speak_does_not_block_the_event_loop(mocker, monkeypatch, restore_state):
    """TTS runs on a worker thread, so the MCP server stays responsive.

    Before the fix, samantha_speak awaited nothing and called the blocking
    playback path inline, so a slow or stuck utterance froze every other tool
    including samantha_stop - the only thing that can interrupt playback.
    """
    state._samantha_thread = None
    monkeypatch.setattr(tools, "ensure_kokoro_running", AsyncMock(return_value=True), raising=False)
    mocker.patch.object(tools.playback, "refresh_audio_devices")
    mocker.patch.object(tools.playback, "speak_tts_sync",
                        side_effect=lambda _: (time.sleep(0.8), True)[1])

    task = asyncio.create_task(_speak()("a long utterance"))
    ticks = 0
    while not task.done():
        await asyncio.sleep(0.05)
        ticks += 1
    await task

    assert ticks >= 5, (
        f"event loop only ticked {ticks} times during an 0.8s TTS - "
        "the blocking call is starving the server"
    )


async def test_direct_speak_preflights_kokoro(mocker, monkeypatch, restore_state):
    """The direct path must start Kokoro rather than POSTing into a refused port."""
    state._samantha_thread = None
    ensure = AsyncMock(return_value=True)
    monkeypatch.setattr(tools, "ensure_kokoro_running", ensure, raising=False)
    mocker.patch.object(tools.playback, "refresh_audio_devices")
    mocker.patch.object(tools.playback, "speak_tts_sync", return_value=True)

    await _speak()("hello")

    ensure.assert_awaited_once()


async def test_direct_speak_reports_failure_when_kokoro_is_down(mocker, monkeypatch, restore_state):
    state._samantha_thread = None
    monkeypatch.setattr(tools, "ensure_kokoro_running", AsyncMock(return_value=False), raising=False)
    speak = mocker.patch.object(tools.playback, "speak_tts_sync")

    result = await _speak()("hello")

    speak.assert_not_called(), "must not attempt playback when Kokoro could not start"
    assert "❌" in result and "Kokoro" in result


def test_refresh_refuses_while_a_stream_is_open(mocker, monkeypatch, restore_state):
    """Tearing PortAudio down under a live stream invalidates it."""
    terminate = mocker.patch.object(playback.sd, "_terminate")
    mocker.patch.object(playback.sd, "_initialize")

    monkeypatch.setattr(playback, "_active_streams", 1, raising=False)
    playback.refresh_audio_devices()
    terminate.assert_not_called()

    monkeypatch.setattr(playback, "_active_streams", 0, raising=False)
    playback.refresh_audio_devices()
    terminate.assert_called_once()


def test_portaudio_failure_falls_back_to_system_player(mocker):
    """Fallback is chosen by exception TYPE, not by grepping the message."""
    mocker.patch.object(playback, "_speak_with_sounddevice",
                        side_effect=playback.sd.PortAudioError("device unavailable"))
    fallback = mocker.patch.object(playback, "_speak_with_system_player", return_value=True)

    assert playback.speak_tts_sync("hi") is True
    fallback.assert_called_once()


async def test_status_reports_a_dead_daemon_as_inactive(mocker, monkeypatch):
    """A stale lock file must not read as a healthy daemon."""
    tmp = pathlib.Path(tempfile.mkdtemp()) / "samantha_active"
    mocker.patch.object(tools, "SAMANTHA_ACTIVE_FILE", tmp)
    monkeypatch.setattr(tools, "_check_service_health", AsyncMock(return_value=False), raising=False)

    dead = 999999
    while True:
        try:
            os.kill(dead, 0)
            dead -= 1
        except OSError:
            break
    tmp.write_text(str(dead))

    import json
    out = json.loads(await _status()())

    assert out["active"] is False, "a dead PID in the lock file was reported as active"
    assert not tmp.exists(), "stale lock file was not cleaned up"


async def test_status_reports_a_live_daemon_as_active(mocker, monkeypatch):
    tmp = pathlib.Path(tempfile.mkdtemp()) / "samantha_active"
    tmp.write_text(str(os.getpid()))
    mocker.patch.object(tools, "SAMANTHA_ACTIVE_FILE", tmp)
    monkeypatch.setattr(tools, "_check_service_health", AsyncMock(return_value=True), raising=False)

    import json
    out = json.loads(await _status()())

    assert out["active"] is True
    assert out["pid"] == os.getpid()
    assert out["kokoro"] is True and out["whisper"] is True
