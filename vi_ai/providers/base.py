"""Shared behaviour for every AI backend.

VI-AI can talk to more than one service. The app only ever sees this
interface, so swapping the backend is a config change and nothing above this
layer knows the difference.

Conversation history lives here rather than in each provider, because the rule
that matters is the same everywhere: a turn is only committed once it has
actually produced text, so a failed request leaves the conversation untouched
and the user can simply try again.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

log = logging.getLogger(__name__)


class ProviderUnavailable(Exception):
    """Raised when a backend cannot be used at all.

    The message is spoken to the user, so it must say what to do about it.
    """


class Provider(ABC):
    """One AI backend: history in, streamed text out."""

    #: Shown by --check and in error messages.
    name = "provider"

    def __init__(self, api_config) -> None:
        self._config = api_config
        self._history: list[dict[str, Any]] = []

    # -- what each backend must supply ------------------------------------

    @abstractmethod
    def preflight(self) -> str | None:
        """Check the backend is usable.

        Returns None when all is well, or a sentence to speak explaining what
        is wrong and how to fix it.
        """

    @abstractmethod
    def _stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        """Send `messages` and yield the reply in pieces as it arrives."""

    @abstractmethod
    def describe_error(self, exc: BaseException) -> str:
        """Turn an exception into one short sentence worth hearing."""

    def describe(self) -> str:
        """One line naming the backend and model, for --check."""
        return self.name

    # -- shared conversation handling -------------------------------------

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def reset(self) -> None:
        self._history.clear()

    def stream(self, prompt: str) -> Iterator[str]:
        """Send `prompt` with the recent history, yielding text as it arrives."""
        messages = [*self._trimmed_history(), {"role": "user", "content": prompt}]
        collected: list[str] = []
        try:
            for delta in self._stream(messages):
                collected.append(delta)
                yield delta
        finally:
            # Runs on success, on failure, and if the caller stops consuming.
            # An unanswered question is never left in the history.
            text = "".join(collected)
            if text:
                self._history.append({"role": "user", "content": prompt})
                self._history.append({"role": "assistant", "content": text})

    def _trimmed_history(self) -> list[dict[str, Any]]:
        """Recent turns only, still starting on a user message."""
        limit = self._config.max_history_messages
        history = self._history
        if limit and len(history) > limit:
            history = history[-limit:]
        # Every backend here requires the first message to be from the user.
        while history and history[0]["role"] != "user":
            history = history[1:]
        return list(history)

    @property
    def system_prompt(self) -> str:
        return self._config.system_prompt.strip()
