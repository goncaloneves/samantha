"""Losing the microphone must not end voice mode.

Turning Bluetooth off mid-session tore down the PortAudio stream and the loop
stopped outright, leaving the server alive but deaf until restarted by hand.
The machine simply falls back to its built-in input; the assistant should
follow it.
"""

import pytest

import samantha.core.loop as loop
import samantha.core.state as state


@pytest.fixture(autouse=True)
def fast_and_isolated(mocker):
    mocker.patch.object(loop.time, "sleep")
    mocker.patch.object(loop.playback, "refresh_audio_devices")
    saved = state._thread_stop_flag
    yield
    state._thread_stop_flag = saved


def test_a_lost_microphone_reattaches_instead_of_stopping(mocker):
    """The reported bug: Bluetooth off ended the session permanently."""
    outcomes = iter([loop.DEVICE_LOST, loop.DEVICE_LOST, loop.STOPPED])
    session = mocker.patch.object(loop, "_listen_session", side_effect=lambda: next(outcomes))
    state._thread_stop_flag = False

    loop.samantha_loop_thread()

    assert session.call_count == 3, (
        f"gave up after {session.call_count} session(s) - a device change must be survivable"
    )


def test_devices_are_re_enumerated_before_reattaching(mocker):
    """PortAudio caches its device list, so without this the reopened stream
    would bind to the device that just disappeared."""
    outcomes = iter([loop.DEVICE_LOST, loop.STOPPED])
    mocker.patch.object(loop, "_listen_session", side_effect=lambda: next(outcomes))
    refresh = mocker.patch.object(loop.playback, "refresh_audio_devices")
    state._thread_stop_flag = False

    loop.samantha_loop_thread()

    refresh.assert_called_once()


def test_an_explicit_stop_is_not_overridden_by_a_reattach(mocker):
    def stop_then_lose():
        state._thread_stop_flag = True
        return loop.DEVICE_LOST

    session = mocker.patch.object(loop, "_listen_session", side_effect=stop_then_lose)
    state._thread_stop_flag = False

    loop.samantha_loop_thread()

    assert session.call_count == 1, "kept reattaching after the user asked it to stop"


def test_reattaching_gives_up_rather_than_spinning_forever(mocker):
    """With no input device at all this must terminate, not burn a core."""
    session = mocker.patch.object(loop, "_listen_session", return_value=loop.DEVICE_LOST)
    state._thread_stop_flag = False

    loop.samantha_loop_thread()

    assert session.call_count == loop.MAX_REATTACH_ATTEMPTS + 1, (
        f"ran {session.call_count} sessions; expected a bounded retry"
    )


def test_a_stream_exception_is_treated_as_a_lost_device(mocker):
    """sd.InputStream raises when the bound device vanishes - that is the same
    condition as the stream going inactive, not a fatal error."""
    mocker.patch.object(loop, "VAD_AVAILABLE", False)
    mocker.patch.object(loop, "get_input_device", return_value=None)
    mocker.patch.object(loop.sd, "query_devices", return_value={"name": "Mock"})
    mocker.patch.object(loop.sd, "InputStream", side_effect=OSError("device unavailable"))
    state._thread_stop_flag = False

    assert loop._listen_session() == loop.DEVICE_LOST
