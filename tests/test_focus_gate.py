"""Regression tests for typing into the right place.

Dictation was being pasted blind. The code fired the IDE's chat shortcut and
then pasted regardless of the outcome, and reported success whenever osascript
exited 0 - which it does even when the keystroke lands on something that
ignores it. Four consecutive utterances were lost this way while the log said
each had been injected successfully.

The AX signatures below were captured live from Cursor.
"""

import subprocess
from types import SimpleNamespace

import pytest

import samantha.injection.detection as detection
import samantha.injection.inject as inject


def _probe(stdout, returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


# role | roleDescription | domClassList  -- captured from a real Cursor session
CHAT_INPUT = "AXTextArea|text entry area|messageInput_cKsPxg"
MONACO_EDITOR = "AXTextArea|editor|inputareamonaco-mouse-cursor-text"
TRANSCRIPT_GROUP = "AXGroup|group|"
# Cursor's integrated terminal: <textarea class="xterm-helper-textarea">, no
# aria-roledescription and no monaco class - otherwise identical to a chat box.
XTERM_TERMINAL = "AXTextArea|text entry area|xterm-helper-textarea"
NOT_FRONTMOST = "||"


@pytest.fixture(autouse=True)
def darwin(mocker):
    mocker.patch.object(detection, "PLATFORM", "Darwin")


@pytest.mark.parametrize("probe,expected", [
    (CHAT_INPUT, detection.FOCUS_INPUT),
    (MONACO_EDITOR, detection.FOCUS_EDITOR),
    (TRANSCRIPT_GROUP, detection.FOCUS_OTHER),
    (XTERM_TERMINAL, detection.FOCUS_TERMINAL),
    (NOT_FRONTMOST, detection.FOCUS_UNKNOWN),
])
def test_focus_is_classified_from_the_real_signatures(mocker, probe, expected):
    mocker.patch.object(detection.subprocess, "run", return_value=_probe(probe))
    assert detection.focused_input_state("Cursor") == expected


def test_the_code_editor_is_never_mistaken_for_a_chat_input(mocker):
    """Both report AXTextArea. Role alone would paste dictation into source."""
    mocker.patch.object(detection.subprocess, "run", return_value=_probe(MONACO_EDITOR))
    assert detection.focused_input_state("Cursor") != detection.FOCUS_INPUT


def test_a_probe_failure_fails_closed(mocker):
    mocker.patch.object(detection.subprocess, "run",
                        side_effect=subprocess.TimeoutExpired("osascript", 5))
    assert detection.focused_input_state("Cursor") == detection.FOCUS_UNKNOWN


def test_the_focus_shortcut_is_always_sent(mocker):
    """It must never be skipped on the strength of the probe.

    FOCUS_INPUT does not mean "the AI input" - the terminal, quick open and the
    find widget all reach it. Skipping the shortcut because focus already looks
    like a text input would paste the transcript into whatever that field is,
    then press Return. README documents the shortcut as unconditional.
    """
    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_INPUT)
    focus = mocker.patch.object(inject, "focus_ide_ai_input", return_value=True)

    assert inject.ensure_ai_input_focused("Cursor") is True
    focus.assert_called_once(), "the documented focus shortcut was skipped"


def test_a_focused_terminal_is_never_typed_into(mocker):
    """The dangerous case: pasting here submits a shell command."""
    mocker.patch.object(detection, "PLATFORM", "Darwin")
    mocker.patch.object(detection.subprocess, "run", return_value=_probe(XTERM_TERMINAL))
    assert detection.focused_input_state("Cursor") == detection.FOCUS_TERMINAL

    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_TERMINAL)
    mocker.patch.object(inject, "focus_ide_ai_input", return_value=True)
    assert inject.ensure_ai_input_focused("Cursor") is False


def test_the_terminal_and_the_chat_box_are_told_apart(mocker):
    """They differ only by class; every other attribute is identical."""
    mocker.patch.object(detection, "PLATFORM", "Darwin")
    seen = {}
    for name, probe in (("chat", CHAT_INPUT), ("terminal", XTERM_TERMINAL)):
        mocker.patch.object(detection.subprocess, "run", return_value=_probe(probe))
        seen[name] = detection.focused_input_state("Cursor")
    assert seen["chat"] != seen["terminal"], (
        f"terminal and chat box classify identically ({seen}) - a transcript "
        "would be executed as a shell command"
    )


def test_focus_is_requested_then_verified(mocker):
    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_INPUT)
    focus = mocker.patch.object(inject, "focus_ide_ai_input", return_value=True)

    assert inject.ensure_ai_input_focused("Cursor") is True
    focus.assert_called_once()


def test_it_refuses_rather_than_typing_into_a_source_file(mocker):
    """The whole point: if focus cannot be established, nothing is typed."""
    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_EDITOR)
    mocker.patch.object(inject, "focus_ide_ai_input", return_value=True)

    assert inject.ensure_ai_input_focused("Cursor") is False


def test_it_refuses_when_the_shortcut_itself_fails(mocker):
    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_OTHER)
    mocker.patch.object(inject, "focus_ide_ai_input", return_value=False)

    assert inject.ensure_ai_input_focused("Cursor") is False


def test_extension_injection_does_not_paste_when_focus_is_refused(mocker):
    """End to end: a refused focus must stop before any keystroke is sent."""
    mocker.patch.object(inject, "is_ai_process_running", return_value=True)
    mocker.patch.object(inject, "copy_to_clipboard", return_value=True)
    mocker.patch.object(inject, "ensure_ai_input_focused", return_value=False)
    paste = mocker.patch.object(inject, "simulate_paste_and_enter")

    assert inject._try_inject_extension("Cursor", "hello") is False
    paste.assert_not_called(), "pasted despite refusing focus - dictation would be lost"


@pytest.mark.parametrize("platform", ["Linux", "Windows"])
def test_platforms_without_a_focus_probe_still_inject(mocker, platform):
    """Focus reading is macOS-only. Refusing when it is unavailable would
    disable dictation outright on Linux and Windows."""
    mocker.patch.object(detection, "PLATFORM", platform)
    assert detection.focused_input_state("Cursor") == detection.FOCUS_UNKNOWN

    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_UNKNOWN)
    mocker.patch.object(inject, "focus_ide_ai_input", return_value=True)

    assert inject.ensure_ai_input_focused("Cursor") is True, (
        f"{platform} can no longer inject at all"
    )


def test_macos_without_accessibility_permission_still_injects(mocker):
    """A probe that cannot read must degrade to the old behaviour, not block."""
    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_UNKNOWN)
    mocker.patch.object(inject, "focus_ide_ai_input", return_value=True)
    assert inject.ensure_ai_input_focused("Cursor") is True


def test_refusal_is_reserved_for_a_focus_we_actually_read(mocker):
    """Both directions: unknown proceeds, a READ wrong focus refuses."""
    mocker.patch.object(inject, "focus_ide_ai_input", return_value=True)
    for state, expected in ((detection.FOCUS_UNKNOWN, True),
                            (detection.FOCUS_INPUT, True),
                            (detection.FOCUS_EDITOR, False),
                            (detection.FOCUS_TERMINAL, False),
                            (detection.FOCUS_OTHER, False)):
        mocker.patch.object(inject, "focused_input_state", return_value=state)
        assert inject.ensure_ai_input_focused("Cursor") is expected, state
