"""Shared test fakes.

The controller is deliberately UI- and network-free, so the tests drive it with
a fake view and a fake Claude client and assert on what would have been spoken.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vi_ai.app import ViAiApp
from vi_ai.config import Config
from vi_ai.speech import NullEngine, SpeechManager


class FakeView:
    """Records what the UI was asked to show. Runs callbacks immediately."""

    def __init__(self) -> None:
        self.prompt = ""
        self.status = ""
        self.turns: list[tuple[str, str]] = []
        self.assistant_text = ""
        self.closed = False

    def post(self, callback):
        callback()

    def set_prompt(self, text):
        self.prompt = text

    def set_status(self, text):
        self.status = text

    def add_turn(self, role, text):
        self.turns.append((role, text))

    def begin_assistant(self):
        self.assistant_text = ""

    def append_assistant(self, text):
        self.assistant_text += text

    def end_assistant(self):
        pass

    def close(self):
        self.closed = True


class FakeClient:
    """Stands in for a Provider: yields canned deltas, or raises."""

    def __init__(self, deltas=None, error=None):
        self.deltas = deltas if deltas is not None else ["Hello. ", "World."]
        self.error = error
        self.prompts: list[str] = []

    def preflight(self):
        return None

    def stream(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        yield from self.deltas

    def describe_error(self, exc):
        return str(exc)


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def engine():
    return NullEngine("test")


@pytest.fixture
def speech(engine):
    manager = SpeechManager(engine)
    yield manager
    manager.shutdown()


@pytest.fixture
def view():
    return FakeView()


@pytest.fixture
def app(config, speech, view):
    """A controller wired to fakes, with a default fake client."""
    application = ViAiApp(config, speech, FakeClient(), view=view)
    return application


def spoken(app_or_engine) -> list[str]:
    """Everything the null engine was asked to say, in order."""
    engine = getattr(app_or_engine, "engine", app_or_engine)
    return list(engine.spoken)


@pytest.fixture
def mock_speed(monkeypatch):
    """Run the mock backend at full speed; its pacing is for humans, not tests."""
    monkeypatch.setattr("vi_ai.providers.mock._DELAY_BETWEEN_WORDS", 0)
