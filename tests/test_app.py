"""Controller behaviour: what a keystroke does, and what the user hears."""

from __future__ import annotations

import time

import pytest
from conftest import FakeClient, spoken

from vi_ai.app import ROLE_STATUS, ROLE_USER, ViAiApp
from vi_ai.providers.base import ProviderUnavailable


def wait_for(predicate, timeout=5.0):
    """Background requests run on a worker thread; give them a moment."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def drain(app):
    """Wait for the in-flight request and for speech to be handed to the engine."""
    assert wait_for(lambda: not app._busy), "request never finished"
    assert app.speech.wait_until_idle(5.0), "speech queue never drained"


def type_text(app, text):
    for char in text:
        keysym = {" ": "space", "?": "question"}.get(char, char)
        app.handle_key(keysym, char)


# -- typing ---------------------------------------------------------------


def test_typing_builds_the_prompt(app, view):
    type_text(app, "hi")
    assert app.prompt == "hi"
    assert view.prompt == "hi"


def test_every_key_is_echoed_when_interruption_is_off(config, speech, view):
    config.speech.interrupt_on_keypress = False
    app = ViAiApp(config, speech, FakeClient(), view=view)
    type_text(app, "hi")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech) == ["h", "i"]


def test_typing_quickly_still_announces_every_key(app, speech):
    """Reported from real use: letters were not spoken while typing.

    Key names queue among themselves rather than cancelling each other, so a
    fast typist hears every key rather than only the last one.
    """
    type_text(app, "hi")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech) == ["h", "i"]


def test_a_keystroke_interrupts_an_answer_being_read(config, speech, view):
    """The user must always be able to hear the key they just pressed."""
    client = FakeClient(["One. ", "Two. ", "Three. ", "Four. ", "Five."])
    app = ViAiApp(config, speech, client, view=view)
    type_text(app, "hi")
    app.handle_key("Return", "\r")
    drain(app)
    before = len(spoken(speech))

    app.handle_key("z", "z")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech)[before:] == ["z"], (
        "the key echo must not be queued behind the rest of the answer"
    )


def test_capitals_are_announced_distinctly(app, speech):
    app.handle_key("A", "A")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech) == ["cap a"]


def test_backspace_removes_a_character_and_names_it(app, view, speech):
    type_text(app, "ab")
    app.handle_key("BackSpace", "\x08")
    assert app.prompt == "a"
    assert view.prompt == "a"
    assert speech.wait_until_idle(2.0)
    assert spoken(speech)[-1] == "deleted b"


def test_backspace_on_an_empty_prompt_says_so(app, speech):
    app.handle_key("BackSpace", "\x08")
    assert app.prompt == ""
    assert speech.wait_until_idle(2.0)
    assert spoken(speech) == ["nothing to delete"]


def test_control_combinations_do_not_type(app, speech):
    """Ctrl+C must not put a "c" in the user's question."""
    app.handle_key("c", "\x03", modified=True)
    assert app.prompt == ""


def test_modifier_keys_alone_are_ignored(app, speech):
    app.handle_key("Shift_L", "")
    assert app.prompt == ""
    assert speech.wait_until_idle(1.0)
    assert spoken(speech) == []


# -- F1 and F2 ------------------------------------------------------------


def test_f1_speaks_the_whole_prompt(app, speech):
    type_text(app, "cat")
    app.handle_key("F1", "")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech)[-1] == "cat"


def test_f1_on_an_empty_prompt_says_it_is_empty(app, speech):
    app.handle_key("F1", "")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech) == ["Your question is empty."]


def test_f2_deletes_the_whole_prompt(app, view, speech):
    type_text(app, "cat")
    app.handle_key("F2", "")
    assert app.prompt == ""
    assert view.prompt == ""
    assert speech.wait_until_idle(2.0)
    assert spoken(speech)[-1] == "Question cleared."


# -- sending --------------------------------------------------------------


@pytest.mark.parametrize("keysym,char", [("Return", "\r"), ("F3", ""), ("KP_Enter", "\r")])
def test_enter_and_f3_both_send(config, speech, view, keysym, char):
    client = FakeClient(["The answer. "])
    app = ViAiApp(config, speech, client, view=view)
    type_text(app, "hi")
    app.handle_key(keysym, char)
    drain(app)
    assert client.prompts == ["hi"]


def test_sending_clears_the_prompt_and_records_the_turn(app, view):
    type_text(app, "hi")
    app.handle_key("Return", "\r")
    drain(app)
    assert app.prompt == ""
    assert view.prompt == ""
    assert (ROLE_USER, "hi") in view.turns


def test_the_answer_is_spoken_sentence_by_sentence(config, speech, view):
    client = FakeClient(["Paris is the ", "capital. ", "It is in France."])
    app = ViAiApp(config, speech, client, view=view)
    type_text(app, "hi")
    app.handle_key("Return", "\r")
    drain(app)
    said = spoken(speech)
    assert "Paris is the capital." in said
    assert "It is in France." in said
    assert view.assistant_text == "Paris is the capital. It is in France."


def test_an_empty_prompt_is_not_sent(config, speech, view):
    client = FakeClient()
    app = ViAiApp(config, speech, client, view=view)
    app.handle_key("Return", "\r")
    assert client.prompts == []
    assert speech.wait_until_idle(2.0)
    assert spoken(speech)[-1] == "Your question is empty. Type something first."


def test_whitespace_only_prompt_is_not_sent(config, speech, view):
    client = FakeClient()
    app = ViAiApp(config, speech, client, view=view)
    type_text(app, "   ")
    app.handle_key("Return", "\r")
    assert client.prompts == []


def test_a_second_send_while_busy_is_refused(config, speech, view):
    app = ViAiApp(config, speech, FakeClient(), view=view)
    app._busy = True  # simulate a request already in flight
    type_text(app, "hi")
    app.handle_key("Return", "\r")
    assert app.prompt == "hi", "the question must survive a refused send"
    assert speech.wait_until_idle(2.0)
    assert spoken(speech)[-1] == "Still waiting for the last answer."


# -- errors ---------------------------------------------------------------


def test_api_errors_are_spoken_and_shown(config, speech, view):
    client = FakeClient(error=ProviderUnavailable("No Claude API key was found."))
    app = ViAiApp(config, speech, client, view=view)
    type_text(app, "hi")
    app.handle_key("Return", "\r")
    drain(app)
    assert "No Claude API key was found." in spoken(speech)
    assert any(role == ROLE_STATUS for role, _ in view.turns)
    assert view.status == "Error."


def test_the_app_recovers_after_an_error(config, speech, view):
    client = FakeClient(error=ProviderUnavailable("boom"))
    app = ViAiApp(config, speech, client, view=view)
    type_text(app, "hi")
    app.handle_key("Return", "\r")
    drain(app)
    assert not app._busy, "a failed request must not wedge the app"


# -- repeat, stop, quit ---------------------------------------------------


def test_f4_repeats_the_last_answer(config, speech, view):
    client = FakeClient(["**All** done."])
    app = ViAiApp(config, speech, client, view=view)
    type_text(app, "hi")
    app.handle_key("Return", "\r")
    drain(app)
    app.handle_key("F4", "")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech)[-1] == "All done.", "the repeat must also be sanitised"


def test_f4_with_no_answer_yet_says_so(app, speech):
    app.handle_key("F4", "")
    assert speech.wait_until_idle(2.0)
    assert spoken(speech) == ["There is no answer to repeat yet."]


def test_escape_stops_speech(app, speech):
    app.handle_key("Escape", "\x1b")
    assert speech.wait_until_idle(2.0)


def test_quit_closes_the_view(app, view):
    app.handle_key("F12", "")
    assert view.closed


# -- startup --------------------------------------------------------------


def test_startup_warns_when_there_is_no_speech_engine(app, view, speech):
    app.start()
    assert speech.wait_until_idle(2.0)
    assert "No speech engine" in " ".join(spoken(speech))
    assert any("No speech engine" in text for _, text in view.turns), (
        "a speech failure must also appear on screen, since it cannot be heard"
    )


def test_a_silent_app_is_still_a_working_app(app, view):
    """No speech engine is a warning, not a reason to refuse questions."""
    app.start()
    assert view.status == "Ready."
    assert any("ready" in text.lower() for _, text in view.turns)


def test_startup_stops_when_claude_cannot_be_reached_at_all(config, speech, view):
    class NoKeyClient(FakeClient):
        def preflight(self):
            return "No Claude API key was found."

    app = ViAiApp(config, speech, NoKeyClient(), view=view)
    app.start()
    assert view.status == "Not ready. See the messages above."
    assert any("API key" in text for _, text in view.turns)
