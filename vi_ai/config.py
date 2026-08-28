"""Configuration loading.

Every user-facing setting lives in a TOML file; the app itself exposes no
settings UI. Missing keys fall back to the defaults defined here, so a partial
config file is always valid.

Search order (first file that exists wins):
    1. $VI_AI_CONFIG
    2. ./config.toml
    3. ~/.config/vi_ai/config.toml
    4. /etc/vi_ai/config.toml
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(Exception):
    """Raised when a config file exists but cannot be used."""


DEFAULT_SYSTEM_PROMPT = """\
You are a voice assistant for a person who is blind or has low vision. \
Everything you say is converted to speech and listened to. It is never read on \
a screen.

Write for the ear:
- Reply in plain spoken prose. Never use markdown, headings, bullet symbols, \
asterisks, tables, emoji, or code fences.
- Lead with the direct answer in the first sentence, then add detail.
- Keep it short. Two or three sentences for a simple question. Go longer only \
when the question genuinely needs it.
- When you list things, say them as a sentence: "There are three options. \
First, ... Second, ... Third, ..."
- Expand symbols and abbreviations that sound wrong when spoken.
- Spell out a URL, path, or code snippet only if asked; otherwise describe it.
- Never mention that your answer is being read aloud.
"""

CONFIG_ENV_VAR = "VI_AI_CONFIG"

SEARCH_PATHS = (
    Path("config.toml"),
    Path.home() / ".config" / "vi_ai" / "config.toml",
    Path("/etc/vi_ai/config.toml"),
)

# Effort levels accepted by output_config.effort on current Claude models.
# "none" omits the parameter, for older models that do not accept it.
EFFORT_LEVELS = ("none", "low", "medium", "high", "xhigh", "max")

KNOWN_ENGINES = (
    "auto", "piper", "espeak-ng", "espeak", "spd-say", "command", "none",
)

# Which AI answers. See vi_ai/providers/ for what each one needs.
PROVIDERS = ("claude", "ollama", "mock")


@dataclass
class ApiConfig:
    """Which AI answers, and how to talk to it.

    `provider`, `system_prompt`, `max_tokens`, `timeout_seconds` and
    `max_history_messages` apply to every backend. `key`, `model` and `effort`
    are Claude's; Ollama has its own settings in [ollama].
    """

    # "claude" needs a paid API key. "ollama" runs a model locally for free.
    # "mock" answers with canned text and never uses the network.
    provider: str = "claude"

    # Prefer the ANTHROPIC_API_KEY environment variable. A key set here is used
    # only when the environment variable is absent.
    key: str = ""
    model: str = "claude-opus-5"
    # Spoken answers are short by design (see the system prompt), and a reply
    # longer than this would take many minutes to listen to.
    max_tokens: int = 4096
    # "medium" keeps time-to-first-word low, which matters a lot when the reply
    # is spoken rather than read. Raise to "high" for harder questions, or set
    # "none" to omit the parameter for models that predate it.
    effort: str = "medium"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    timeout_seconds: float = 120.0
    # Conversation turns kept and resent on each request (user + assistant each
    # count as one). 0 means unlimited.
    max_history_messages: int = 40


@dataclass
class SpeechConfig:
    """How text is turned into sound."""

    engine: str = "auto"
    voice: str = "en"
    # Words per minute for espeak-ng. Experienced screen-reader users often
    # prefer 250-400.
    rate: int = 170
    volume: int = 100
    pitch: int = 50
    # Used when engine = "command". "{text}" is replaced with the text to speak;
    # if no argument contains "{text}", the text is written to the process stdin.
    command: list[str] = field(default_factory=list)
    # Path to a piper .onnx voice. Setting this makes "auto" choose piper,
    # which sounds far better than espeak-ng and starts speaking sooner.
    piper_model: str = ""
    piper_speaker: int = 0
    # Command that plays a WAV given on stdin. Empty means auto-detect
    # (aplay, then paplay, then pw-play, then ffplay).
    player: list[str] = field(default_factory=list)
    # Speak each key as it is pressed so the user can verify what they typed.
    speak_key_echo: bool = True
    # A keypress cuts off whatever is currently being spoken, so the key echo is
    # never queued behind a long answer.
    interrupt_on_keypress: bool = True
    # On backspace, name the character that was removed.
    announce_deleted_char: bool = True
    # Speak short status messages ("Sending", "Prompt cleared", errors).
    speak_status: bool = True
    # Longest run of characters sent to the speech engine at once. Long chunks
    # take longer to interrupt.
    max_chunk_chars: int = 300
    # How many key names may wait to be spoken before the oldest are dropped.
    # Saying a key name takes longer than pressing the next key, so a fast
    # typist always outruns the voice; this bounds how far behind it can get.
    # 2 keeps what you hear within about a second of what you type. Raise it to
    # hear every single key at the cost of falling further behind; set it to 1
    # to stay as current as possible.
    max_pending_echoes: int = 2


@dataclass
class OllamaConfig:
    """A model served locally by Ollama. Only used when provider = "ollama"."""

    host: str = "http://127.0.0.1:11434"
    # llama3.2 is about 2 GB. On a Raspberry Pi use a smaller one such as
    # "llama3.2:1b" or "qwen2.5:0.5b".
    model: str = "llama3.2"


@dataclass
class KeysConfig:
    """Key bindings, given as Tk keysym names.

    Run `python -m vi_ai --list-keys` to see the keysym for any key you press.
    """

    speak_prompt: list[str] = field(default_factory=lambda: ["F1"])
    clear_prompt: list[str] = field(default_factory=lambda: ["F2"])
    send: list[str] = field(default_factory=lambda: ["Return", "KP_Enter", "F3"])
    repeat_last: list[str] = field(default_factory=lambda: ["F4"])
    stop_speech: list[str] = field(default_factory=lambda: ["Escape"])
    quit: list[str] = field(default_factory=lambda: ["F12"])


@dataclass
class UiConfig:
    """The window. Large, high contrast, and nothing to click."""

    fullscreen: bool = True
    font_family: str = "DejaVu Sans"
    font_size: int = 28
    background: str = "#000000"
    foreground: str = "#FFFFFF"
    user_color: str = "#FFD75F"
    assistant_color: str = "#87FF87"
    status_color: str = "#9E9E9E"
    show_prompt_line: bool = True
    window_width: int = 1024
    window_height: int = 768


@dataclass
class Config:
    api: ApiConfig = field(default_factory=ApiConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    keys: KeysConfig = field(default_factory=KeysConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    # Path the config was loaded from, or None if defaults were used.
    source: Path | None = None
    # Non-fatal problems (unknown keys, and so on) worth logging at startup.
    warnings: list[str] = field(default_factory=list)

    def api_key(self) -> str:
        """The Claude API key to use, environment variable first."""
        return os.environ.get("ANTHROPIC_API_KEY", "").strip() or self.api.key.strip()


class _Section:
    """Reads typed values out of one TOML table, collecting warnings."""

    def __init__(self, name: str, data: Any, warnings: list[str]) -> None:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError(f"[{name}] must be a table, got {type(data).__name__}")
        self._name = name
        self._data = data
        self._warnings = warnings
        self._used: set[str] = set()

    def _get(self, key: str) -> Any:
        self._used.add(key)
        return self._data.get(key)

    def str_(self, key: str, default: str, *, choices: tuple[str, ...] = ()) -> str:
        raw = self._get(key)
        if raw is None:
            return default
        if not isinstance(raw, str):
            raise ConfigError(f"{self._name}.{key} must be a string")
        if choices and raw not in choices:
            raise ConfigError(
                f"{self._name}.{key} must be one of {', '.join(choices)}; got {raw!r}"
            )
        return raw

    def int_(self, key: str, default: int, *, minimum: int | None = None) -> int:
        raw = self._get(key)
        if raw is None:
            return default
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ConfigError(f"{self._name}.{key} must be an integer")
        if minimum is not None and raw < minimum:
            raise ConfigError(f"{self._name}.{key} must be at least {minimum}")
        return raw

    def float_(self, key: str, default: float, *, minimum: float | None = None) -> float:
        raw = self._get(key)
        if raw is None:
            return default
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ConfigError(f"{self._name}.{key} must be a number")
        value = float(raw)
        if minimum is not None and value < minimum:
            raise ConfigError(f"{self._name}.{key} must be at least {minimum}")
        return value

    def bool_(self, key: str, default: bool) -> bool:
        raw = self._get(key)
        if raw is None:
            return default
        if not isinstance(raw, bool):
            raise ConfigError(f"{self._name}.{key} must be true or false")
        return raw

    def str_list(self, key: str, default: list[str]) -> list[str]:
        """Accepts either a single string or a list of strings."""
        raw = self._get(key)
        if raw is None:
            return list(default)
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            return list(raw)
        raise ConfigError(f"{self._name}.{key} must be a string or a list of strings")

    def finish(self) -> None:
        for key in self._data:
            if key not in self._used:
                self._warnings.append(f"unknown setting {self._name}.{key} (ignored)")


def find_config_file(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Locate the config file, or None to run on defaults."""
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path

    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        path = Path(from_env).expanduser()
        if not path.is_file():
            raise ConfigError(f"{CONFIG_ENV_VAR} points at a missing file: {path}")
        return path

    for candidate in SEARCH_PATHS:
        if candidate.is_file():
            return candidate
    return None


def load_config(explicit: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration, falling back to defaults for anything unset."""
    path = find_config_file(explicit)
    if path is None:
        return Config()

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    config = from_dict(data)
    config.source = path
    return config


def from_dict(data: dict[str, Any]) -> Config:
    """Build a Config from an already-parsed TOML mapping."""
    warnings: list[str] = []
    defaults = Config()

    api_section = _Section("api", data.get("api"), warnings)
    api = ApiConfig(
        provider=api_section.str_(
            "provider", defaults.api.provider, choices=PROVIDERS
        ),
        key=api_section.str_("key", defaults.api.key),
        model=api_section.str_("model", defaults.api.model),
        max_tokens=api_section.int_("max_tokens", defaults.api.max_tokens, minimum=1),
        effort=api_section.str_("effort", defaults.api.effort, choices=EFFORT_LEVELS),
        system_prompt=api_section.str_("system_prompt", defaults.api.system_prompt),
        timeout_seconds=api_section.float_(
            "timeout_seconds", defaults.api.timeout_seconds, minimum=1.0
        ),
        max_history_messages=api_section.int_(
            "max_history_messages", defaults.api.max_history_messages, minimum=0
        ),
    )
    api_section.finish()

    ollama_section = _Section("ollama", data.get("ollama"), warnings)
    ollama = OllamaConfig(
        host=ollama_section.str_("host", defaults.ollama.host),
        model=ollama_section.str_("model", defaults.ollama.model),
    )
    ollama_section.finish()

    speech_section = _Section("speech", data.get("speech"), warnings)
    speech = SpeechConfig(
        engine=speech_section.str_(
            "engine", defaults.speech.engine, choices=KNOWN_ENGINES
        ),
        voice=speech_section.str_("voice", defaults.speech.voice),
        rate=speech_section.int_("rate", defaults.speech.rate, minimum=1),
        volume=speech_section.int_("volume", defaults.speech.volume, minimum=0),
        pitch=speech_section.int_("pitch", defaults.speech.pitch, minimum=0),
        command=speech_section.str_list("command", defaults.speech.command),
        piper_model=speech_section.str_("piper_model", defaults.speech.piper_model),
        piper_speaker=speech_section.int_(
            "piper_speaker", defaults.speech.piper_speaker, minimum=0
        ),
        player=speech_section.str_list("player", defaults.speech.player),
        speak_key_echo=speech_section.bool_(
            "speak_key_echo", defaults.speech.speak_key_echo
        ),
        interrupt_on_keypress=speech_section.bool_(
            "interrupt_on_keypress", defaults.speech.interrupt_on_keypress
        ),
        announce_deleted_char=speech_section.bool_(
            "announce_deleted_char", defaults.speech.announce_deleted_char
        ),
        speak_status=speech_section.bool_("speak_status", defaults.speech.speak_status),
        max_chunk_chars=speech_section.int_(
            "max_chunk_chars", defaults.speech.max_chunk_chars, minimum=20
        ),
        max_pending_echoes=speech_section.int_(
            "max_pending_echoes", defaults.speech.max_pending_echoes, minimum=1
        ),
    )
    speech_section.finish()

    if speech.engine == "command" and not speech.command:
        raise ConfigError('speech.engine = "command" requires speech.command')
    if speech.engine == "piper" and not speech.piper_model:
        raise ConfigError('speech.engine = "piper" requires speech.piper_model')

    keys_section = _Section("keys", data.get("keys"), warnings)
    keys = KeysConfig(
        speak_prompt=keys_section.str_list("speak_prompt", defaults.keys.speak_prompt),
        clear_prompt=keys_section.str_list("clear_prompt", defaults.keys.clear_prompt),
        send=keys_section.str_list("send", defaults.keys.send),
        repeat_last=keys_section.str_list("repeat_last", defaults.keys.repeat_last),
        stop_speech=keys_section.str_list("stop_speech", defaults.keys.stop_speech),
        quit=keys_section.str_list("quit", defaults.keys.quit),
    )
    keys_section.finish()
    warnings.extend(_check_key_conflicts(keys))

    ui_section = _Section("ui", data.get("ui"), warnings)
    ui = UiConfig(
        fullscreen=ui_section.bool_("fullscreen", defaults.ui.fullscreen),
        font_family=ui_section.str_("font_family", defaults.ui.font_family),
        font_size=ui_section.int_("font_size", defaults.ui.font_size, minimum=6),
        background=ui_section.str_("background", defaults.ui.background),
        foreground=ui_section.str_("foreground", defaults.ui.foreground),
        user_color=ui_section.str_("user_color", defaults.ui.user_color),
        assistant_color=ui_section.str_("assistant_color", defaults.ui.assistant_color),
        status_color=ui_section.str_("status_color", defaults.ui.status_color),
        show_prompt_line=ui_section.bool_(
            "show_prompt_line", defaults.ui.show_prompt_line
        ),
        window_width=ui_section.int_(
            "window_width", defaults.ui.window_width, minimum=320
        ),
        window_height=ui_section.int_(
            "window_height", defaults.ui.window_height, minimum=240
        ),
    )
    ui_section.finish()

    for name in ("api", "ollama", "speech", "keys", "ui"):
        data.pop(name, None)
    for leftover in data:
        warnings.append(f"unknown config section [{leftover}] (ignored)")

    return Config(
        api=api, ollama=ollama, speech=speech, keys=keys, ui=ui, warnings=warnings
    )


def _check_key_conflicts(keys: KeysConfig) -> list[str]:
    """A keysym bound to two actions would make one of them unreachable."""
    seen: dict[str, str] = {}
    conflicts: list[str] = []
    for action in ("speak_prompt", "clear_prompt", "send", "repeat_last",
                   "stop_speech", "quit"):
        for keysym in getattr(keys, action):
            if keysym in seen:
                conflicts.append(
                    f"key {keysym!r} is bound to both {seen[keysym]} and {action}; "
                    f"{seen[keysym]} wins"
                )
            else:
                seen[keysym] = action
    return conflicts
