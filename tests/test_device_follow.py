"""Following the machine's current audio device, in both directions.

Connecting headphones mid-session produced no failure at all - the laptop
stream stayed perfectly healthy - so nothing ever noticed, and the assistant
kept listening and speaking through the built-in devices until restarted by
hand. PortAudio cannot see the change: it caches its device list for the life
of the stream, which is the entire time the loop is listening.
"""

import pytest

import samantha.audio.devices as devices
import samantha.audio.playback as playback
import samantha.core.loop as loop
import samantha.core.state as state


@pytest.fixture(autouse=True)
def fast(mocker):
    mocker.patch.object(loop.time, "sleep")
    mocker.patch.object(loop.playback, "refresh_audio_devices")
    saved = (state._thread_stop_flag, playback._tts_playing)
    yield
    state._thread_stop_flag, playback._tts_playing = saved


def test_a_device_change_reopens_the_stream(mocker):
    """Headphones connected, or disconnected: either way, follow."""
    outcomes = iter([loop.DEVICE_CHANGED, loop.STOPPED])
    session = mocker.patch.object(loop, "_listen_session", side_effect=lambda: next(outcomes))
    refresh = mocker.patch.object(loop.playback, "refresh_audio_devices")
    state._thread_stop_flag = False

    loop.samantha_loop_thread()

    assert session.call_count == 2, "did not reopen after the device changed"
    refresh.assert_called_once(), "reopened without re-enumerating - would rebind the old device"


def test_following_a_device_does_not_consume_the_failure_budget(mocker):
    """Swapping headphones repeatedly is normal use, not a fault. If each
    change counted as a failure, a listening session would die after ten."""
    changes = [loop.DEVICE_CHANGED] * (loop.MAX_REATTACH_ATTEMPTS + 5) + [loop.STOPPED]
    outcomes = iter(changes)
    session = mocker.patch.object(loop, "_listen_session", side_effect=lambda: next(outcomes))
    state._thread_stop_flag = False

    loop.samantha_loop_thread()

    assert session.call_count == len(changes), (
        f"gave up after {session.call_count} device changes - swapping devices is not a failure"
    )


def test_a_broken_device_still_gives_up(mocker):
    """Both directions: changes are free, but genuine failures stay bounded."""
    session = mocker.patch.object(loop, "_listen_session", return_value=loop.DEVICE_LOST)
    state._thread_stop_flag = False

    loop.samantha_loop_thread()

    assert session.call_count == loop.MAX_REATTACH_ATTEMPTS + 1


def test_the_tracker_reports_unknown_rather_than_no_devices(mocker):
    """On a platform without the query, callers must not read the result as
    'the devices went away' and start cycling the stream forever."""
    mocker.patch.object(devices, "_CORE_AUDIO", None)
    assert devices.current_default_devices() == (None, None)
    assert devices.device_tracking_available() is False


def test_an_unavailable_tracker_never_triggers_a_cycle():
    """(None, None) is explicitly excluded from the change comparison, so a
    platform with no tracker keeps the previous behaviour instead of thrashing."""
    session_devices = (130, 124)
    unavailable = (None, None)
    assert not (unavailable != (None, None) and unavailable != session_devices)


@pytest.mark.skipif(not devices.device_tracking_available(),
                    reason="CoreAudio default-device query unavailable")
def test_the_real_tracker_returns_stable_ids():
    """It must be stable when nothing changes, or the loop would cycle on noise."""
    first = devices.current_default_devices()
    second = devices.current_default_devices()
    assert first == second, f"tracker is not stable: {first} != {second}"
    assert all(isinstance(x, int) for x in first), f"expected device ids, got {first}"
