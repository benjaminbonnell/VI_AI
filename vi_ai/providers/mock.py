"""A backend that makes no network calls at all.

Useful for checking that the keyboard, the speech engine, the interruption
behaviour and the window all work, before setting up any AI service. Answers
are canned, but they stream in word by word like a real one, so sentence
chunking and interrupting a reply behave exactly as they will in earnest.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from .base import Provider

# Roughly the pace of a real streamed reply, so the speech timing is realistic.
_DELAY_BETWEEN_WORDS = 0.04

_REPLIES = (
    (
        "This is the built in test backend, so nothing was sent over the "
        "internet. You asked: {prompt}. If you can hear this sentence, the "
        "keyboard, the speech engine and the screen are all working."
    ),
    (
        "Still the test backend, and no AI was involved. Your question was: "
        "{prompt}. Try pressing escape while I am talking, to check that "
        "stopping works. Press F4 to hear this answer again."
    ),
    (
        "Test backend again. You typed: {prompt}. To use a real model for "
        "free, install Ollama and set provider to ollama in your config file. "
        "To use Claude, set provider to claude and add an API key."
    ),
)


class MockProvider(Provider):
    """Canned replies, streamed word by word. No network, no key, no cost."""

    name = "mock"

    def __init__(self, api_config) -> None:
        super().__init__(api_config)
        self._turn = 0

    def describe(self) -> str:
        return "mock (built-in test replies, no network)"

    def preflight(self) -> str | None:
        return None

    def _stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        # Trailing punctuation would double up against the template's own full
        # stop, which is audible as "is this on question mark full stop".
        prompt = messages[-1]["content"].strip().rstrip(".?!,;:")
        reply = _REPLIES[self._turn % len(_REPLIES)].format(prompt=prompt)
        self._turn += 1

        for index, word in enumerate(reply.split(" ")):
            if index:
                yield " "
            yield word
            time.sleep(_DELAY_BETWEEN_WORDS)

    def describe_error(self, exc: BaseException) -> str:
        return f"The test backend failed, which should not happen. {exc}"
