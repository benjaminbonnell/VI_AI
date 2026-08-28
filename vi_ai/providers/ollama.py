"""The Ollama backend: a model running locally, free and offline.

Ollama needs no account, no API key and no network once the model is pulled,
which makes it the practical way to try VI-AI, and a reasonable way to keep
using it. Install it from https://ollama.com, then:

    ollama pull llama3.2
    ollama serve

Only the standard library is used here, so Ollama support costs the project no
extra dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from .base import Provider, ProviderUnavailable

log = logging.getLogger(__name__)

# Preflight runs at startup and must never hold it up for long.
_PREFLIGHT_TIMEOUT_SECONDS = 3.0


class OllamaError(Exception):
    """An error reported by the Ollama server itself."""


class OllamaProvider(Provider):
    """A local model served by Ollama, streamed line by line."""

    name = "ollama"

    def __init__(self, api_config, ollama_config) -> None:
        super().__init__(api_config)
        self._host = ollama_config.host.rstrip("/")
        self._model = ollama_config.model

    def describe(self) -> str:
        return f"ollama ({self._model} at {self._host})"

    # -- setup ------------------------------------------------------------

    def preflight(self) -> str | None:
        """Check the server is up and the model is actually installed.

        Both are easy to get wrong and produce confusing failures later, so
        they are worth one cheap local request at startup.
        """
        try:
            installed = self._installed_models()
        except ProviderUnavailable as exc:
            return str(exc)

        if not self._is_installed(installed, self._model):
            available = ", ".join(sorted(installed)[:5]) or "none"
            return (
                f"The model {self._model} is not installed in Ollama. "
                f"Run: ollama pull {self._model}. "
                f"Models you do have: {available}."
            )
        return None

    def _installed_models(self) -> set[str]:
        try:
            with urllib.request.urlopen(
                f"{self._host}/api/tags", timeout=_PREFLIGHT_TIMEOUT_SECONDS
            ) as response:
                payload = json.load(response)
        except urllib.error.URLError as exc:
            raise ProviderUnavailable(
                f"Cannot reach Ollama at {self._host}. "
                "Check that it is installed and running: ollama serve"
            ) from exc
        except (OSError, ValueError) as exc:
            raise ProviderUnavailable(
                f"Ollama at {self._host} gave an unexpected reply. {exc}"
            ) from exc
        return {model.get("name", "") for model in payload.get("models", [])}

    @staticmethod
    def _is_installed(installed: set[str], model: str) -> bool:
        # Ollama treats a bare name as the ":latest" tag.
        wanted = model if ":" in model else f"{model}:latest"
        return model in installed or wanted in installed

    # -- the request ------------------------------------------------------

    def _stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "options": {"num_predict": self._config.max_tokens},
        }
        if self.system_prompt:
            payload["messages"] = [
                {"role": "system", "content": self.system_prompt},
                *messages,
            ]

        request = urllib.request.Request(
            f"{self._host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(
            request, timeout=self._config.timeout_seconds
        ) as response:
            # The reply is newline-delimited JSON, one object per chunk.
            for line in response:
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:
                    log.debug("skipping unparsable line from Ollama: %r", line)
                    continue
                if chunk.get("error"):
                    raise OllamaError(str(chunk["error"]))
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    def describe_error(self, exc: BaseException) -> str:
        if isinstance(exc, ProviderUnavailable):
            return str(exc)
        if isinstance(exc, OllamaError):
            message = str(exc)
            if "not found" in message.lower():
                return (f"Ollama does not have the model {self._model}. "
                        f"Run: ollama pull {self._model}")
            return f"Ollama reported an error. {message}"
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code == 404:
                return (f"Ollama does not have the model {self._model}. "
                        f"Run: ollama pull {self._model}")
            return f"Ollama returned an error, status {exc.code}."
        if isinstance(exc, urllib.error.URLError):
            return (f"Cannot reach Ollama at {self._host}. "
                    "Check that it is running: ollama serve")
        if isinstance(exc, TimeoutError):
            return ("Ollama took too long to answer. A smaller model such as "
                    "llama3.2:1b will be faster on this machine.")
        log.exception("unexpected error talking to Ollama")
        return f"Something went wrong. {exc}"
