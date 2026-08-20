"""Regression tests for process reaping, wake/deactivation matching and config guards.

These pin failures that were observed live rather than theorised: a reaper that
SIGKILLed unrelated user processes and every other Claude session's MCP server,
a deactivation phrase that fired mid-sentence and discarded the command, and a
VAD fallback that left the assistant permanently deaf.
"""

import os

import pytest

import samantha.config.settings as settings
import samantha.core.loop as loop
import samantha.injection.detection as detection
import samantha.utils.text as text


class _FakeProc:
    def __init__(self, pid, cmdline, uid=None):
        self.pid = pid
        self._cmdline = cmdline
        self._uid = os.getuid() if uid is None else uid
        self.terminated = False
        self.killed = False

    def cmdline(self):
        return self._cmdline

    def uids(self):
        class _U:
            real = self._uid
        return _U()

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


SAMANTHA_BIN = "/Users/x/.local/bin/samantha"
PY = "/Users/x/.local/share/uv/tools/samantha/bin/python3"
KOKORO = "/Users/x/.samantha/services/kokoro/.venv/bin/uvicorn"


def _reap(mocker, procs, **kwargs):
    mocker.patch.object(detection.psutil, "process_iter", return_value=procs)
    mocker.patch.object(detection.psutil, "wait_procs", return_value=(procs, []))
    mocker.patch.object(detection, "_protected_pids", return_value={os.getpid()})
    return detection.kill_orphaned_processes(**kwargs)


def test_reaper_ignores_a_process_that_merely_mentions_samantha(mocker):
    """A grep, an editor or a diagnostic shell must never be signalled.

    The old matcher substring-tested whole `ps aux` lines, so any command line
    that referenced a Samantha path was killed - including the shell running
    the diagnosis.
    """
    bystander = _FakeProc(4242, ["/bin/zsh", "-c", "grep -r /bin/samantha ~/.samantha/services/kokoro"])
    grepper = _FakeProc(4243, ["python3", "-c", "print('/Users/x/.samantha/services/whisper/x')"])

    _reap(mocker, [bystander, grepper], include_services=True)

    assert not bystander.terminated and not bystander.killed, "reaper signalled an unrelated shell"
    assert not grepper.terminated and not grepper.killed, "reaper signalled an unrelated python process"


def test_reaper_never_signals_another_live_mcp_server(mocker):
    """Several Claude sessions each run their own server against one install."""
    peer = _FakeProc(5001, [PY, SAMANTHA_BIN])
    other_peer = _FakeProc(5002, [PY, SAMANTHA_BIN])

    _reap(mocker, [peer, other_peer], include_services=True)

    for p in (peer, other_peer):
        assert not p.terminated and not p.killed, "reaper killed a live peer MCP server"


def test_reaper_leaves_services_alone_while_a_peer_is_still_using_them(mocker):
    peer = _FakeProc(5001, [PY, SAMANTHA_BIN])
    kokoro = _FakeProc(6001, [PY, KOKORO])

    _reap(mocker, [peer, kokoro])

    assert not kokoro.terminated, "tore down a service a live peer depends on"


def test_reaper_does_reap_an_orphaned_service(mocker):
    """Both directions: with no live peer, an orphaned service IS reaped."""
    kokoro = _FakeProc(6001, [PY, KOKORO])

    killed = _reap(mocker, [kokoro])

    assert kokoro.terminated, "an orphaned service was left running"
    assert killed == 1


def test_entrypoint_identification_rejects_a_lookalike(mocker):
    mocker.patch.object(detection.psutil, "Process",
                        return_value=_FakeProc(1, ["/bin/zsh", "-c", "echo /bin/samantha"]))
    assert detection.is_samantha_entrypoint(1) is False

    mocker.patch.object(detection.psutil, "Process",
                        return_value=_FakeProc(2, [PY, SAMANTHA_BIN]))
    assert detection.is_samantha_entrypoint(2) is True


@pytest.mark.parametrize("utterance,expected", [
    ("that's all", True),
    ("deploy it and then check the logs, that's all", True),
    ("bye samantha", True),
    ("samantha pause the deploy and show me the logs", False),
    ("that's all the context you need to fix the parser", False),
])
def test_deactivation_only_fires_at_the_end_of_an_utterance(mocker, utterance, expected):
    """Substring matching deactivated mid-sentence and discarded the command."""
    mocker.patch.object(text, "get_deactivation_phrases",
                        return_value=["that's all", "bye samantha", "samantha pause"])
    assert text.check_for_deactivation(utterance) is expected


def test_energy_fallback_distinguishes_speech_from_silence(mocker):
    """Without webrtcvad the loop returned a constant True, so silence was
    never detected, the silence threshold never tripped and transcription was
    unreachable - the assistant listened forever and never heard anything."""
    import numpy as np
    mocker.patch.object(loop, "get_min_audio_energy", return_value=1500)

    silence = np.zeros(480, dtype=np.int16)
    speech = np.full(480, 9000, dtype=np.int16)

    assert loop._chunk_has_speech_energy(silence) is False, "silence read as speech - deaf again"
    assert loop._chunk_has_speech_energy(speech) is True, "speech read as silence"


def test_a_non_numeric_device_setting_does_not_crash_the_loop(mocker):
    """A device NAME in config used to raise ValueError inside the loop thread,
    surfacing only as a 30-second 'audio stream not ready'."""
    mocker.patch.object(settings, "get_config", return_value="MacBook Pro Microphone")
    assert settings.get_input_device() is None
    assert settings.get_output_device() is None


def test_a_malformed_config_is_reported_as_an_error(mocker, tmp_path, caplog):
    """It silently reverted voice, wake words and devices to defaults."""
    bad = tmp_path / "config.json"
    bad.write_text("{ not json")
    mocker.patch.object(settings, "CONFIG_FILE", bad)

    with caplog.at_level("ERROR", logger="samantha"):
        assert settings.load_config() == {}

    assert any("EVERY setting" in r.getMessage() for r in caplog.records), (
        "a broken config produced no error-level log"
    )
