"""Text to speech.

Three pieces live here:

* Engines, which turn one string into sound.
* `Utterance`, one unit of sound in progress, which can be stopped.
* `SpeechManager`, a worker thread and a queue, so nothing on the UI thread
  ever blocks on audio.

Two things drive the design, and both come from watching the app be used
rather than from the code:

**Key names must never be dropped.** Typing is confirmed by hearing each key.
An earlier version let every keystroke cancel whatever was being said,
including the previous key name, so typing at any speed produced near silence:
a key name takes longer to say than it takes to press the next key. Key names
now queue among themselves (see `Category.ECHO`) and only cancel longer
speech such as an answer being read.

**Starting a process per utterance is too slow for key names.** espeak-ng
costs about 0.17 seconds to start for a single letter. Piper avoids this by
synthesising in this process, with the model loaded once, at about 0.04
seconds per letter, and sounds far better besides.
"""

from __future__ import annotations

import array
import collections
import enum
import io
import logging
import queue
import shutil
import subprocess
import threading
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# How long to wait for a speech process to exit after terminate() before
# escalating to kill().
_TERMINATE_GRACE_SECONDS = 0.4

# Piper's length_scale is 1.0 at roughly this speaking rate, so the config's
# words-per-minute setting can drive both engines from one number.
_PIPER_REFERENCE_WPM = 200

# Piper pads every phrase with silence, which is around a third of the length
# of a single spoken letter. Trimming it makes key echo noticeably tighter.
# Anything below this fraction of the loudest sample counts as silence, and a
# little padding is kept so the first consonant is not clipped.
_SILENCE_THRESHOLD = 0.02
_SILENCE_KEEP_SECONDS = 0.02

# Players that accept a WAV on standard input, cheapest first. Startup cost is
# paid on every phrase, including every key name, so it is worth caring about:
# measured here, aplay and pw-cat cost about 0.05s per phrase against paplay's
# 0.13s. aplay leads because it talks to ALSA directly and is what Raspberry Pi
# OS ships. Note that paplay takes no "-": it reads stdin when given no file.
_PLAYERS: tuple[list[str], ...] = (
    ["aplay", "-q", "-"],
    ["pw-cat", "-p", "-"],
    ["pw-play", "-"],
    ["paplay"],
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
)


class SpeechError(Exception):
    """Raised when the configured speech engine cannot be used."""


class Category(enum.Enum):
    """What a piece of speech is, which decides what may interrupt it."""

    #: A key name. Short, and must be heard, so these queue among themselves.
    ECHO = "echo"
    #: An answer, a status line, or the prompt read back. A keystroke stops it.
    MESSAGE = "message"


_ALL_CATEGORIES = frozenset(Category)
_MESSAGES_ONLY = frozenset({Category.MESSAGE})


@dataclass(eq=False)
class _Item:
    """One queued piece of speech.

    Compared by identity (`eq=False`): two identical letters typed in a row are
    separate items, and must not be mistaken for each other when one of them is
    removed from the queue.
    """

    text: str
    category: Category


class Utterance:
    """One piece of sound being produced, which can be stopped early."""

    def __init__(
        self,
        proc: subprocess.Popen,
        stdin_data: bytes | None = None,
        cancel_argv: list[str] | None = None,
    ) -> None:
        self._proc = proc
        self._cancel_argv = cancel_argv
        self._feeder: threading.Thread | None = None
        if stdin_data is not None and proc.stdin is not None:
            # Written on its own thread: a WAV can be far larger than the pipe
            # buffer, and blocking here would make the utterance impossible to
            # interrupt until playback had nearly finished.
            self._feeder = threading.Thread(
                target=self._feed, args=(stdin_data,),
                name="vi-ai-speech-feed", daemon=True,
            )
            self._feeder.start()

    def _feed(self, data: bytes) -> None:
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(data)
            self._proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            # Stopped early, which is normal when the user interrupts.
            log.debug("speech stdin closed early", exc_info=True)

    def wait(self) -> None:
        try:
            self._proc.wait()
        except Exception:  # pragma: no cover - process already reaped
            log.debug("speech process wait failed", exc_info=True)

    def kill(self) -> None:
        """Stop the sound as fast as the engine allows.

        Always called on the speech manager's killer thread, never on the UI
        thread: terminating a process and waiting for it can take long enough
        to be felt as a stutter while typing.
        """
        # Client/server engines (speech-dispatcher) keep speaking after the
        # client exits, so the cancel has to happen first. Doing it after the
        # terminate would race with the next utterance starting, and could
        # silence that one instead of this one.
        if self._cancel_argv:
            try:
                subprocess.run(
                    self._cancel_argv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,
                )
            except Exception:  # pragma: no cover
                log.debug("speech cancel command failed", exc_info=True)
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception:  # pragma: no cover - racing with normal exit
            log.debug("speech process kill failed", exc_info=True)


class TtsEngine:
    """Base class: produce sound for one string."""

    name = "none"

    def describe(self) -> str:
        return self.name

    def speak(self, text: str) -> Utterance | None:
        raise NotImplementedError


class NullEngine(TtsEngine):
    """No audio. Keeps the app usable (and testable) with no TTS installed."""

    name = "none"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        self.spoken: list[str] = []

    def describe(self) -> str:
        return f"none ({self.reason})" if self.reason else "none"

    def speak(self, text: str) -> Utterance | None:
        self.spoken.append(text)
        log.info("[silent] %s", text)
        return None


class _SubprocessEngine(TtsEngine):
    """Shared plumbing: spawn a command and feed it text on stdin."""

    def __init__(self, argv: list[str], cancel_argv: list[str] | None = None) -> None:
        self.argv = argv
        self.cancel_argv = cancel_argv

    def describe(self) -> str:
        return f"{self.name} ({' '.join(self.argv)})"

    def _argv_for(self, text: str) -> tuple[list[str], str | None]:
        """Return the command to run and the text to write to stdin, if any."""
        return self.argv, text

    def speak(self, text: str) -> Utterance | None:
        argv, stdin_text = self._argv_for(text)
        data = None if stdin_text is None else stdin_text.encode("utf-8")
        proc = _spawn(argv, feed_stdin=data is not None)
        return Utterance(proc, data, self.cancel_argv)


class EspeakEngine(_SubprocessEngine):
    """espeak-ng or classic espeak. Always available, but robotic."""

    name = "espeak-ng"

    def __init__(self, executable: str, voice: str, rate: int, volume: int, pitch: int):
        self.name = executable
        argv = [
            executable,
            "--stdin",
            "-v", voice,
            "-s", str(_clamp(rate, 80, 450)),  # espeak-ng accepts 80-450 wpm
            "-a", str(_clamp(volume, 0, 200)),
            "-p", str(_clamp(pitch, 0, 99)),
        ]
        super().__init__(argv)

    def _argv_for(self, text: str) -> tuple[list[str], str | None]:
        # The trailing newline matters. Without it espeak-ng treats the input as
        # an unterminated line and clips the tail of the final word: silent for
        # a word ending in a vowel, but it eats the closing consonant of words
        # like "backspace" or "Paris".
        return self.argv, text + "\n"


class SpdSayEngine(_SubprocessEngine):
    """speech-dispatcher. Picks up the user's existing system voice settings."""

    name = "spd-say"

    def __init__(self, executable: str, voice: str, rate: int, volume: int, pitch: int):
        # speech-dispatcher uses relative -100..100 scales rather than words per
        # minute, so the config values are mapped approximately.
        argv = [
            executable,
            "-w",  # block until the text has been spoken
            "-r", str(_clamp(round((rate - 170) / 2.3), -100, 100)),
            "-i", str(_clamp(volume * 2 - 100, -100, 100)),
            "-p", str(_clamp(pitch * 2 - 100, -100, 100)),
        ]
        if voice:
            argv += ["-l", voice]
        super().__init__(argv, cancel_argv=[executable, "-C"])

    def _argv_for(self, text: str) -> tuple[list[str], str | None]:
        # spd-say takes the text as arguments, not on stdin.
        return [*self.argv, "--", text], None


class CommandEngine(_SubprocessEngine):
    """A user-supplied command, for engines this module does not know about.

    Placeholders `{text}`, `{voice}`, `{rate}`, `{volume}` and `{pitch}` are
    substituted into the arguments. If no argument mentions `{text}`, the text
    is written to the process stdin instead.
    """

    name = "command"

    def __init__(
        self,
        command: list[str],
        voice: str,
        rate: int,
        volume: int,
        pitch: int,
    ) -> None:
        if not command:
            raise SpeechError("speech.command is empty")
        self._template = list(command)
        self._values = {
            "voice": voice,
            "rate": str(rate),
            "volume": str(volume),
            "pitch": str(pitch),
        }
        self._text_in_argv = any("{text}" in part for part in command)
        super().__init__(list(command))

    def _argv_for(self, text: str) -> tuple[list[str], str | None]:
        argv = [
            part.format(text=text, **self._values) if "{" in part else part
            for part in self._template
        ]
        return argv, None if self._text_in_argv else text


class PiperEngine(TtsEngine):
    """Piper: a neural voice, far more natural than espeak-ng.

    The model is loaded once into this process rather than being reloaded for
    every phrase, which takes synthesis from about 1.3 seconds per phrase down
    to about 0.04. Audio is handed to a separate player process so that
    stopping speech is just killing the player, leaving the loaded model alone.
    """

    name = "piper"

    def __init__(
        self,
        model_path: str,
        player: list[str],
        rate: int,
        volume: int,
        speaker: int,
    ) -> None:
        self._model_path = Path(model_path).expanduser()
        self._player = player
        self._rate = rate
        self._volume = volume
        self._speaker = speaker
        self._voice = None
        self._load_error: str | None = None
        self._loaded = threading.Event()
        # Loading takes about a second, so it overlaps with building the window
        # rather than delaying the first thing the user hears.
        threading.Thread(
            target=self._load, name="vi-ai-piper-load", daemon=True
        ).start()

    def describe(self) -> str:
        player = self._player[0] if self._player else "no player"
        return f"piper ({self._model_path.name} via {player})"

    def _load(self) -> None:
        # Every failure here is caught and turned into a spoken sentence. A
        # voice file can fail to load in many ways that are not worth
        # distinguishing (truncated download, wrong architecture, a broken
        # onnxruntime), and none of them should take the app down: the user
        # would get silence with no explanation.
        try:
            from piper import PiperVoice
        except ModuleNotFoundError:
            self._load_error = (
                "The piper speech engine is not installed. "
                "Run: pip install piper-tts"
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            self._load_error = f"Could not load piper. {exc}"
        else:
            try:
                self._voice = PiperVoice.load(str(self._model_path))
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                self._load_error = (
                    f"Could not load the piper voice {self._model_path}. {exc}"
                )
        finally:
            self._loaded.set()

    def _synthesis_config(self):
        from piper.config import SynthesisConfig

        return SynthesisConfig(
            speaker_id=self._speaker or None,
            # Piper measures speed as a length multiplier, so a higher
            # words-per-minute setting means a shorter length scale.
            length_scale=_clamp_float(_PIPER_REFERENCE_WPM / max(1, self._rate),
                                      0.3, 3.0),
            volume=_clamp_float(self._volume / 100.0, 0.0, 2.0),
        )

    def speak(self, text: str) -> Utterance | None:
        self._loaded.wait()
        if self._voice is None:
            raise SpeechError(self._load_error or "piper failed to load")
        if not self._player:
            raise SpeechError(
                "No audio player was found for piper. "
                "Install alsa-utils (for aplay) or pulseaudio-utils (paplay)."
            )

        chunks = list(
            self._voice.synthesize(text, syn_config=self._synthesis_config())
        )
        if not chunks:
            return None
        first = chunks[0]
        pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
        pcm = _trim_silence(pcm, first.sample_rate)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(first.sample_channels)
            wav.setsampwidth(first.sample_width)
            wav.setframerate(first.sample_rate)
            wav.writeframes(pcm)

        proc = _spawn(self._player, feed_stdin=True)
        return Utterance(proc, buffer.getvalue())


def _trim_silence(pcm: bytes, sample_rate: int) -> bytes:
    """Drop the near-silence at each end of 16-bit mono audio.

    A single spoken letter is about a third silence as synthesised, which is
    dead time on every keystroke.
    """
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % samples.itemsize])
    if not samples:
        return pcm

    peak = max(max(samples), -min(samples))
    if peak == 0:
        return pcm
    threshold = peak * _SILENCE_THRESHOLD

    first = next((i for i, v in enumerate(samples) if abs(v) > threshold), None)
    if first is None:
        return pcm
    last = next(
        i for i in range(len(samples) - 1, -1, -1) if abs(samples[i]) > threshold
    )

    keep = int(sample_rate * _SILENCE_KEEP_SECONDS)
    start = max(0, first - keep)
    end = min(len(samples), last + 1 + keep)
    return samples[start:end].tobytes()


def _spawn(argv: list[str], *, feed_stdin: bool) -> subprocess.Popen:
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if feed_stdin else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise SpeechError(f"cannot run {argv[0]!r}: {exc}") from exc


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def detect_player(override: list[str] | None = None) -> list[str]:
    """Find a command that plays a WAV given on standard input."""
    if override:
        return list(override)
    for candidate in _PLAYERS:
        executable = shutil.which(candidate[0])
        if executable is not None:
            return [executable, *candidate[1:]]
    return []


def build_engine(speech_config) -> TtsEngine:
    """Create the engine named by the config, or auto-detect one.

    Never raises for a missing engine: an app that exits because a voice is not
    installed is worse than one that runs silently and says so on screen.
    """
    choice = speech_config.engine
    args = (
        speech_config.voice,
        speech_config.rate,
        speech_config.volume,
        speech_config.pitch,
    )

    if choice == "none":
        return NullEngine("disabled in config")

    if choice == "command":
        try:
            return CommandEngine(speech_config.command, *args)
        except SpeechError as exc:
            return NullEngine(str(exc))

    if choice in ("piper", "auto") and speech_config.piper_model:
        model = Path(speech_config.piper_model).expanduser()
        if model.is_file():
            return PiperEngine(
                str(model),
                detect_player(speech_config.player),
                speech_config.rate,
                speech_config.volume,
                speech_config.piper_speaker,
            )
        if choice == "piper":
            return NullEngine(f"piper model not found: {model}")
        log.warning("piper model not found, falling back: %s", model)

    if choice == "piper":
        return NullEngine("speech.piper_model is not set")

    candidates: Iterable[str]
    if choice == "auto":
        candidates = ("espeak-ng", "espeak", "spd-say")
    else:
        candidates = (choice,)

    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable is None:
            continue
        if candidate == "spd-say":
            return SpdSayEngine(executable, *args)
        return EspeakEngine(executable, *args)

    wanted = "espeak-ng, espeak or spd-say" if choice == "auto" else choice
    return NullEngine(f"{wanted} not found on PATH")


class SpeechManager:
    """Serialises speech onto one worker thread, with selective interruption.

    `speak(text)` queues. `speak(text, category=Category.ECHO)` queues a key
    name, which stops any answer being read but never cancels another key name.
    `speak(text, interrupt=True)` clears everything and says the new text.
    """

    def __init__(
        self,
        engine: TtsEngine,
        on_error: Callable[[str], None] | None = None,
        max_pending_echoes: int = 2,
    ) -> None:
        self._engine = engine
        self._on_error = on_error
        self._max_pending_echoes = max(1, max_pending_echoes)
        self._lock = threading.Lock()
        self._wakeup = threading.Condition(self._lock)
        self._queue: collections.deque[_Item] = collections.deque()
        # The item currently being produced. Setting it to None is how a
        # cancellation tells the worker that what it started is no longer wanted.
        self._current_item: _Item | None = None
        self._current_utterance: Utterance | None = None
        self._running = True
        self._idle = threading.Event()
        self._idle.set()
        # Stopping a process is done on its own thread. `speak` runs on the UI
        # thread on every keystroke, and must return at once.
        self._kills: queue.Queue = queue.Queue()
        self._killer = threading.Thread(
            target=self._kill_loop, name="vi-ai-speech-kill", daemon=True
        )
        self._killer.start()
        self._thread = threading.Thread(
            target=self._run, name="vi-ai-speech", daemon=True
        )
        self._thread.start()

    @property
    def engine(self) -> TtsEngine:
        return self._engine

    @property
    def engine_description(self) -> str:
        return self._engine.describe()

    def speak(
        self,
        text: str,
        *,
        category: Category = Category.MESSAGE,
        interrupt: bool = False,
    ) -> None:
        """Queue `text`. See the class docstring for what cancels what."""
        text = text.strip()
        if not text:
            return
        with self._wakeup:
            if not self._running:
                return
            if interrupt:
                self._cancel_locked(_ALL_CATEGORIES)
            elif category is Category.ECHO:
                # A keystroke stops a long answer, but must not silence the key
                # name pressed a moment ago.
                self._cancel_locked(_MESSAGES_ONLY)
            self._queue.append(_Item(text, category))
            if category is Category.ECHO:
                self._trim_echoes_locked()
            self._idle.clear()
            self._wakeup.notify()

    def stop(self) -> None:
        """Silence everything queued and in progress."""
        with self._wakeup:
            self._cancel_locked(_ALL_CATEGORIES)

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._wakeup:
            self._running = False
            self._cancel_locked(_ALL_CATEGORIES)
            self._wakeup.notify_all()
        self._thread.join(timeout=timeout)
        self._kills.put(None)
        self._killer.join(timeout=timeout)

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Block until the queue drains. Used by tests, not by the UI."""
        return self._idle.wait(timeout=timeout)

    # -- internals --------------------------------------------------------

    def _cancel_locked(self, categories: frozenset[Category]) -> None:
        """Drop pending work in `categories` and stop it if it is playing.

        Caller must hold the lock. The kill is handed to the killer thread so
        this returns immediately.
        """
        self._queue = collections.deque(
            item for item in self._queue if item.category not in categories
        )
        item = self._current_item
        if item is not None and item.category in categories:
            # The worker sees this and abandons what it started.
            self._current_item = None
            utterance, self._current_utterance = self._current_utterance, None
            if utterance is not None:
                self._kills.put(utterance)

    def _trim_echoes_locked(self) -> None:
        """Keep the key names from falling behind the typing.

        If a key name cannot be said before the next key arrives, a backlog
        builds and the user hears letters they typed seconds ago. Past a small
        number of pending names, the oldest are dropped so that what is heard
        stays close to what is being typed.
        """
        pending = sum(
            1 for item in self._queue if item.category is Category.ECHO
        )
        excess = pending - self._max_pending_echoes
        if excess <= 0:
            return
        kept: collections.deque[_Item] = collections.deque()
        dropped = 0
        for item in self._queue:
            if item.category is Category.ECHO and dropped < excess:
                dropped += 1
                continue
            kept.append(item)
        self._queue = kept

    def _kill_loop(self) -> None:
        while True:
            utterance = self._kills.get()
            if utterance is None:
                return
            try:
                utterance.kill()
            except Exception:  # pragma: no cover
                log.debug("kill failed", exc_info=True)

    def _run(self) -> None:
        while True:
            with self._wakeup:
                while self._running and not self._queue:
                    self._idle.set()
                    self._wakeup.wait()
                if not self._running:
                    return
                item = self._queue.popleft()
                self._current_item = item
                self._current_utterance = None

            utterance = None
            try:
                utterance = self._engine.speak(item.text)
            except SpeechError as exc:
                self._report(str(exc))
            except Exception:  # pragma: no cover - unexpected engine failure
                log.exception("speech engine failed")
                self._report("Speech engine failed.")

            with self._lock:
                if self._current_item is not item:
                    # Cancelled while it was starting up.
                    if utterance is not None:
                        self._kills.put(utterance)
                    continue
                if utterance is None:
                    self._current_item = None
                    continue
                self._current_utterance = utterance

            utterance.wait()

            with self._lock:
                if self._current_item is item:
                    self._current_item = None
                    self._current_utterance = None

    def _report(self, message: str) -> None:
        if self._on_error is not None:
            try:
                self._on_error(message)
            except Exception:  # pragma: no cover
                log.exception("speech error callback failed")
