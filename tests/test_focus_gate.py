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
NOT_FRONTMOST = "||"


@pytest.fixture(autouse=True)
def darwin(mocker):
    mocker.patch.object(detection, "PLATFORM", "Darwin")


@pytest.mark.parametrize("probe,expected", [
    (CHAT_INPUT, detection.FOCUS_INPUT),
    (MONACO_EDITOR, detection.FOCUS_EDITOR),
    (TRANSCRIPT_GROUP, detection.FOCUS_OTHER),
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


def test_an_already_focused_input_is_not_disturbed(mocker):
    """The common case, and the one that broke: the user has just typed in the
    input, so focus is already correct - and the old code moved it away."""
    mocker.patch.object(inject, "focused_input_state", return_value=detection.FOCUS_INPUT)
    focus = mocker.patch.object(inject, "focus_ide_ai_input")

    assert inject.ensure_ai_input_focused("Cursor") is True
    focus.assert_not_called(), "sent a focus shortcut when focus was already correct"


def test_focus_is_requested_then_reverified(mocker):
    states = iter([detection.FOCUS_EDITOR, detection.FOCUS_INPUT])
    mocker.patch.object(inject, "focused_input_state", side_effect=lambda _: next(states))
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
                            (detection.FOCUS_EDITOR, False),
                            (detection.FOCUS_OTHER, False)):
        mocker.patch.object(inject, "focused_input_state", return_value=state)
        assert inject.ensure_ai_input_focused("Cursor") is expected, state
