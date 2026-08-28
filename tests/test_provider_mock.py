"""The built-in test backend: the zero-setup way to check the app works."""

from __future__ import annotations

from vi_ai.config import Config, from_dict
from vi_ai.providers import build_provider
from vi_ai.providers.mock import MockProvider


def provider() -> MockProvider:
    return MockProvider(Config().api)


def test_it_needs_no_setup_at_all():
    assert provider().preflight() is None


def test_it_repeats_the_question_back(mock_speed):
    """So you can confirm the text that reached the backend is what you typed."""
    answer = "".join(provider().stream("is this thing on"))
    assert "is this thing on" in answer


def test_the_answer_has_several_sentences(mock_speed):
    """Sentence chunking and interruption need more than one sentence to test."""
    answer = "".join(provider().stream("hello"))
    assert answer.count(".") >= 3


def test_answers_vary_between_turns(mock_speed):
    """Identical replies would hide a stuck conversation."""
    backend = provider()
    first = "".join(backend.stream("one"))
    second = "".join(backend.stream("two"))
    assert first != second


def test_it_streams_rather_than_arriving_at_once(mock_speed):
    """Real backends stream, so the test one must too or it proves less."""
    pieces = list(provider().stream("hello"))
    assert len(pieces) > 10


def test_history_is_kept_like_any_other_backend(mock_speed):
    backend = provider()
    list(backend.stream("first"))
    list(backend.stream("second"))
    assert [m["role"] for m in backend.history] == [
        "user", "assistant", "user", "assistant"
    ]


def test_the_factory_builds_it_from_config():
    config = from_dict({"api": {"provider": "mock"}})
    assert isinstance(build_provider(config), MockProvider)
