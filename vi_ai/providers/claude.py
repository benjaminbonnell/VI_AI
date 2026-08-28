"""The Claude backend, via the official Anthropic SDK.

Needs a paid API key from https://console.anthropic.com. A Claude Pro or Max
subscription does not include API access; they are billed separately. Use the
`ollama` or `mock` provider to try VI-AI without one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from .base import Provider, ProviderUnavailable

log = logging.getLogger(__name__)

MISSING_KEY_MESSAGE = (
    "No Claude API key was found. Set the ANTHROPIC_API_KEY environment "
    "variable, or put a key in the config file. Note that a Claude Pro "
    "subscription does not include API access."
)

MISSING_SDK_MESSAGE = (
    "The Claude library is not installed. Run: pip install anthropic"
)


class ClaudeProvider(Provider):
    """Claude, streamed so speech can start before the answer finishes."""

    name = "claude"

    def __init__(self, api_config, api_key: str) -> None:
        super().__init__(api_config)
        self._api_key = api_key
        self._client: Any = None
        self._anthropic: Any = None

    def describe(self) -> str:
        return f"claude ({self._config.model}, effort {self._config.effort})"

    # -- setup ------------------------------------------------------------

    def _load_sdk(self) -> Any:
        if self._anthropic is None:
            try:
                import anthropic
            except ModuleNotFoundError as exc:  # pragma: no cover
                raise ProviderUnavailable(MISSING_SDK_MESSAGE) from exc
            self._anthropic = anthropic
        return self._anthropic

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        anthropic = self._load_sdk()
        if not self._api_key:
            raise ProviderUnavailable(MISSING_KEY_MESSAGE)
        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            timeout=self._config.timeout_seconds,
        )
        return self._client

    def preflight(self) -> str | None:
        try:
            self._ensure_client()
        except ProviderUnavailable as exc:
            return str(exc)
        return None

    # -- the request ------------------------------------------------------

    def _stream(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        client = self._ensure_client()

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "messages": messages,
        }
        if self.system_prompt:
            kwargs["system"] = self._config.system_prompt
        # Thinking is left unset: on Claude Opus 5 that means adaptive thinking,
        # which is what we want, and it stays valid on older models too.
        if self._config.effort != "none":
            kwargs["output_config"] = {"effort": self._config.effort}

        final: Any = None
        with client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()

        stop_reason = getattr(final, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(final, "stop_details", None)
            category = getattr(details, "category", None)
            suffix = f" Category: {category}." if category else ""
            yield f"\n\nClaude declined to answer that request.{suffix}"
        elif stop_reason == "max_tokens":
            yield "\n\nThe answer was cut off because it reached the length limit."

    def describe_error(self, exc: BaseException) -> str:
        return describe_api_error(exc)


def describe_api_error(exc: BaseException) -> str:
    """A short sentence suitable for reading aloud."""
    if isinstance(exc, ProviderUnavailable):
        return str(exc)

    try:
        import anthropic
    except ModuleNotFoundError:  # pragma: no cover
        return f"Something went wrong. {exc}"

    if isinstance(exc, anthropic.AuthenticationError):
        return ("Your Claude API key was not accepted. "
                "Check the key in your config file.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ("Your Claude API key does not have permission for that request. "
                "Check that the account has API credit.")
    if isinstance(exc, anthropic.NotFoundError):
        return ("Claude did not recognise the model name in your config file. "
                "Check the model setting.")
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = "a moment"
        response = getattr(exc, "response", None)
        if response is not None:
            header = response.headers.get("retry-after")
            if header:
                retry_after = f"{header} seconds"
        return f"Claude is busy right now. Please try again in {retry_after}."
    if isinstance(exc, anthropic.APITimeoutError):
        return "Claude took too long to answer. Please try again."
    if isinstance(exc, anthropic.APIConnectionError):
        return ("Cannot reach Claude. Check that this machine is connected to "
                "the internet.")
    if isinstance(exc, anthropic.BadRequestError):
        return f"Claude rejected the request. {getattr(exc, 'message', exc)}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Claude returned an error, status {exc.status_code}."

    log.exception("unexpected error talking to Claude")
    return f"Something went wrong. {exc}"
