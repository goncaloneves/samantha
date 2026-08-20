"""Test isolation from the user's real Samantha data.

Several tests drive the genuine playback path, and playback calls
log_conversation() on success, which appends to ~/.samantha/conversation.log -
the user's actual voice history. A test run therefore wrote test utterances
into real user data. Redirect every file the package writes to a tmp path for
the whole session so that cannot happen again.
"""

import pytest

import samantha.utils.logging as samantha_logging


@pytest.fixture(autouse=True)
def isolate_user_data(tmp_path_factory, monkeypatch):
    sandbox = tmp_path_factory.mktemp("samantha-home")
    monkeypatch.setattr(samantha_logging, "CONVERSATION_LOG", sandbox / "conversation.log",
                        raising=False)
    yield
