"""AI backends.

Which one is used comes from `[api] provider` in the config file:

    claude  the Anthropic API. Needs a paid API key; a Claude Pro or Max
            subscription does not include one.
    ollama  a model running locally through Ollama. Free, no account, works
            offline once the model is pulled.
    mock    canned replies with no network at all, for checking that the
            keyboard, speech and window work.
"""

from __future__ import annotations

from .base import Provider, ProviderUnavailable
from .claude import ClaudeProvider
from .mock import MockProvider
from .ollama import OllamaProvider

__all__ = [
    "ClaudeProvider",
    "MockProvider",
    "OllamaProvider",
    "Provider",
    "ProviderUnavailable",
    "build_provider",
]


def build_provider(config) -> Provider:
    """Create the backend named in the config."""
    choice = config.api.provider
    if choice == "claude":
        return ClaudeProvider(config.api, config.api_key())
    if choice == "ollama":
        return OllamaProvider(config.api, config.ollama)
    if choice == "mock":
        return MockProvider(config.api)
    # config validation rejects anything else, so this is a programming error.
    raise ValueError(f"unknown provider {choice!r}")
