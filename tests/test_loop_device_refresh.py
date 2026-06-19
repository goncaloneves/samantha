"""Regression tests for audio-device re-enumeration.

PortAudio caches its device list when it first initializes in a process, on
every platform. Both TTS/recording entry points must re-enumerate before
opening a stream, or a device connected after the MCP server started (e.g.
Bluetooth headphones) is never used — audio silently stays on the startup
default device.

The single shared implementation is ``playback.refresh_audio_devices()``. It is
called at listening-loop start AND on the standalone speak path (when the loop
is not running). It MUST NOT be called on the queued speak path, because the
loop's input stream is open then and tearing PortAudio down would kill it.
"""

import threading

import pytest

import samantha.audio.playback as playback
import samantha.core.loop as loop
import samantha.tools.samantha_tools as tools
import samantha.core.state as state


def _speak():
    """Resolve the samantha_speak coroutine whether it's a plain function or
    wrapped in an mcp FunctionTool (``.fn``)."""
    return getattr(tools.samantha_speak, "fn", tools.samantha_speak)


@pytest.fixture
def restore_state():
    saved = (state._samantha_thread, state._thread_stop_flag, state._thread_ready, state._audio_stream)
    yield
    state._samantha_thread, state._thread_stop_flag, state._thread_ready, state._audio_stream = saved
    with playback._tts_queue_lock:
        playback._tts_text_queue.clear()


def test_loop_refreshes_devices_before_opening_input_stream(mocker, restore_state):
    calls = []

    mocker.patch.object(loop, "VAD_AVAILABLE", False)
    mocker.patch.object(loop, "get_input_device", return_value=None)
    mocker.patch.object(loop.playback, "refresh_audio_devices", side_effect=lambda: calls.append("refresh"))
    mocker.patch.object(loop.sd, "query_devices", return_value={"name": "MockDevice"})

    class _FakeStream:
        def __enter__(self):
            calls.append("input_stream_open")
            return self

        def __exit__(self, *_):
            return False

    mocker.patch.object(loop.sd, "InputStream", return_value=_FakeStream())

    state._thread_stop_flag = True
    state._thread_ready = threading.Event()

    loop.samantha_loop_thread()

    assert "refresh" in calls, "loop did not refresh the device list"
    assert "input_stream_open" in calls
    assert calls.index("refresh") < calls.index("input_stream_open"), (
        "device refresh must happen BEFORE the input stream opens, or the stream "
        "binds to the stale default device"
    )
    assert state._thread_ready.is_set()


def test_refresh_helper_reinitializes_portaudio(mocker):
    order = []
    mocker.patch.object(playback.sd, "_terminate", side_effect=lambda: order.append("terminate"))
    mocker.patch.object(playback.sd, "_initialize", side_effect=lambda: order.append("initialize"))

    playback.refresh_audio_devices()

    assert order == ["terminate", "initialize"]


def test_refresh_helper_survives_terminate_before_init(mocker):
    """A first-ever refresh may hit _terminate before PortAudio is initialized."""
    mocker.patch.object(playback.sd, "_terminate", side_effect=RuntimeError("not initialized"))
    init = mocker.patch.object(playback.sd, "_initialize")

    playback.refresh_audio_devices()

    init.assert_called_once()


def test_refresh_helper_swallows_initialize_failure(mocker):
    """If re-init fails, degrade gracefully (log) instead of crashing the loop."""
    mocker.patch.object(playback.sd, "_terminate")
    mocker.patch.object(playback.sd, "_initialize", side_effect=RuntimeError("PortAudio init failed"))

    playback.refresh_audio_devices()


async def test_speak_direct_path_refreshes_before_playing(mocker, restore_state):
    """When the loop is NOT running, the standalone speak path must refresh first."""
    state._samantha_thread = None
    order = []
    mocker.patch.object(tools.playback, "refresh_audio_devices", side_effect=lambda: order.append("refresh"))
    mocker.patch.object(tools.playback, "speak_tts_sync", side_effect=lambda _: order.append("speak") or True)

    result = await _speak()("hello")

    assert order == ["refresh", "speak"], "direct speak must refresh devices before playing"
    assert "Spoke" in result


async def test_speak_queued_path_does_not_refresh(mocker, restore_state):
    """When the loop IS running its input stream is open — refreshing would kill it."""
    fake_thread = mocker.MagicMock()
    fake_thread.is_alive.return_value = True
    state._samantha_thread = fake_thread
    refresh = mocker.patch.object(tools.playback, "refresh_audio_devices")
    speak = mocker.patch.object(tools.playback, "speak_tts_sync")

    result = await _speak()("queued message")

    refresh.assert_not_called()
    speak.assert_not_called()
    assert "queued message" in playback._tts_text_queue
    assert "Spoke" in result
