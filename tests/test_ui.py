"""End-to-end test through the real Tk window.

Everything else is tested against fakes; this drives actual key events into the
actual widget tree, which is the only way to catch a binding that never fires
or a widget that swallows a keystroke before the controller sees it.

Skipped when there is no display (headless CI); run it under Xvfb to include it.
"""

from __future__ import annotations

import os
import time

import pytest
from conftest import FakeClient

from vi_ai.app import ViAiApp
from vi_ai.config import from_dict
from vi_ai.speech import NullEngine, SpeechManager

tk = pytest.importorskip("tkinter")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="needs an X display; run under xvfb-run to include",
)


@pytest.fixture
def gui():
    """A real window, windowed rather than fullscreen so it cannot take over."""
    from vi_ai.ui import TkView

    config = from_dict({"ui": {"fullscreen": False, "font_size": 14}})
    engine = NullEngine("test")
    speech = SpeechManager(engine)
    client = FakeClient(["Paris. ", "It is in France."])
    app = ViAiApp(config, speech, client, view=None)
    try:
        view = TkView(config, app.handle_key)
    except tk.TclError as exc:  # pragma: no cover
        speech.shutdown()
        pytest.skip(f"cannot open a window: {exc}")
    app.attach_view(view)
    view.root.update()
    yield app, view, engine, client
    try:
        view.root.destroy()
    except tk.TclError:
        pass
    speech.shutdown()


def press(view, keysym):
    view.root.event_generate(f"<KeyPress-{keysym}>", when="now")
    view.root.update()


def pump_until(view, predicate, timeout=5.0):
    """Run the Tk event loop until `predicate` holds, so posted work lands."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view.root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def transcript_text(view):
    return view.transcript.get("1.0", "end")


def test_keystrokes_reach_the_controller(gui):
    app, view, _engine, _client = gui
    for keysym in ("h", "i"):
        press(view, keysym)
    assert app.prompt == "hi"


def test_the_prompt_line_shows_what_was_typed(gui):
    _app, view, _engine, _client = gui
    for keysym in ("h", "i"):
        press(view, keysym)
    assert "hi" in view.prompt_label.cget("text")


def test_f2_clears_the_prompt_on_screen(gui):
    app, view, _engine, _client = gui
    press(view, "h")
    press(view, "F2")
    assert app.prompt == ""
    assert view.prompt_label.cget("text").strip() == ">"


def test_a_full_exchange_appears_in_the_transcript(gui):
    app, view, engine, client = gui
    for keysym in ("h", "i"):
        press(view, keysym)
    press(view, "Return")

    assert pump_until(view, lambda: not app._busy), "request never completed"
    assert pump_until(view, lambda: "France" in transcript_text(view))

    text = transcript_text(view)
    assert "You: hi" in text
    assert "Robot: " in text
    assert "Paris. It is in France." in text
    assert client.prompts == ["hi"]

    assert app.speech.wait_until_idle(5.0)
    assert "Paris." in engine.spoken


def test_the_transcript_is_read_only(gui):
    """Typing must build the question, never edit the history on screen."""
    app, view, _engine, _client = gui
    before = transcript_text(view)
    press(view, "x")
    assert transcript_text(view) == before
    assert app.prompt == "x"


def test_control_combinations_do_not_type(gui):
    app, view, _engine, _client = gui
    view.root.event_generate("<Control-KeyPress-c>", when="now")
    view.root.update()
    assert app.prompt == ""


def test_the_quit_key_closes_the_window(gui):
    _app, view, _engine, _client = gui
    press(view, "F12")
    assert view._closing
