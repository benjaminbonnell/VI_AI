"""Application logic, independent of any particular UI toolkit.

The controller owns the prompt buffer and decides what every keystroke means.
It talks to the screen through a small `View` interface, which keeps the Tk
code in `ui.py` free of any behaviour worth testing.

Threading: `handle_key` and the other entry points run on the UI thread. API
requests run on a short-lived worker thread and hand results back through
`view.post`, which re-enters the UI thread. Speech is queued and never blocks
either.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from .config import Config
from .keynames import name_character, name_key
from .providers import Provider
from .speech import Category, NullEngine, SpeechManager
from .textproc import SentenceChunker, speech_text

log = logging.getLogger(__name__)

# Roles used for transcript entries.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_STATUS = "status"


class View(Protocol):
    """What the controller needs from a UI."""

    def post(self, callback) -> None:
        """Run `callback` on the UI thread. Safe to call from any thread."""

    def set_prompt(self, text: str) -> None: ...
    def add_turn(self, role: str, text: str) -> None: ...
    def begin_assistant(self) -> None: ...
    def append_assistant(self, text: str) -> None: ...
    def end_assistant(self) -> None: ...
    def set_status(self, text: str) -> None: ...
    def close(self) -> None: ...


class ViAiApp:
    """Keystrokes in, speech and transcript out."""

    def __init__(
        self,
        config: Config,
        speech: SpeechManager,
        client: Provider,
        view: View | None = None,
    ) -> None:
        self.config = config
        self.speech = speech
        self.client = client
        self.view = view

        self.prompt = ""
        self.last_response = ""
        self._busy = False
        self._busy_lock = threading.Lock()

        self._actions = self._build_action_map(config)

    @staticmethod
    def _build_action_map(config: Config) -> dict[str, str]:
        """Keysym -> action name. First binding wins on a conflict."""
        actions: dict[str, str] = {}
        for action in ("quit", "stop_speech", "speak_prompt", "clear_prompt",
                       "repeat_last", "send"):
            for keysym in getattr(config.keys, action):
                actions.setdefault(keysym, action)
        return actions

    def attach_view(self, view: View) -> None:
        self.view = view

    # -- startup ----------------------------------------------------------

    def start(self) -> None:
        """Announce readiness, and any problem the user needs to know about."""
        for warning in self.config.warnings:
            log.warning("config: %s", warning)

        # A missing speech engine leaves the app working but silent, so it is a
        # warning. A missing key or library means nothing can be sent at all.
        if isinstance(self.speech.engine, NullEngine):
            self._report_problem(
                "No speech engine was found, so nothing will be read aloud. "
                "Install espeak-ng and restart."
            )

        blocker = self.client.preflight()
        if blocker:
            self._report_problem(blocker)
            self._set_status("Not ready. See the messages above.")
            return

        ready = (
            "V I A I is ready. Type your question, then press enter to send it. "
            "F1 reads your question back. F2 clears it."
        )
        self._add_turn(ROLE_STATUS, ready)
        self._say_status(ready)
        self._set_status("Ready.")

    def _report_problem(self, message: str) -> None:
        """Put a problem on screen as well as saying it.

        A speech failure cannot be heard, so the screen is the only channel left.
        """
        self._add_turn(ROLE_STATUS, message)
        self._say(message)

    # -- key handling -----------------------------------------------------

    def handle_key(self, keysym: str, char: str = "", *, modified: bool = False) -> None:
        """Handle one keystroke.

        `modified` is True when control, alt or super is held, in which case the
        keystroke never edits the prompt.
        """
        action = self._actions.get(keysym)
        if action is not None:
            getattr(self, f"_action_{action}")()
            return

        if modified:
            return

        if keysym == "BackSpace":
            self._backspace()
            return

        spoken = name_key(keysym, char)
        if not spoken:
            return  # a modifier or an unnamed key: stay silent

        if char and (char.isprintable() or char == "\t"):
            self.prompt += char
            self._set_prompt(self.prompt)

        self._echo(spoken)

    def _backspace(self) -> None:
        if not self.prompt:
            self._echo("nothing to delete")
            return
        removed = self.prompt[-1]
        self.prompt = self.prompt[:-1]
        self._set_prompt(self.prompt)
        if self.config.speech.announce_deleted_char:
            self._echo(f"deleted {name_character(removed)}")
        else:
            self._echo("backspace")

    # -- actions ----------------------------------------------------------

    def _action_speak_prompt(self) -> None:
        if not self.prompt.strip():
            self._say_status("Your question is empty.", interrupt=True)
            return
        self._say(self.prompt, interrupt=True)

    def _action_clear_prompt(self) -> None:
        if not self.prompt:
            self._say_status("Your question is already empty.", interrupt=True)
            return
        self.prompt = ""
        self._set_prompt("")
        self._say_status("Question cleared.", interrupt=True)

    def _action_repeat_last(self) -> None:
        if not self.last_response:
            self._say_status("There is no answer to repeat yet.", interrupt=True)
            return
        self._say(speech_text(self.last_response), interrupt=True)

    def _action_stop_speech(self) -> None:
        self.speech.stop()

    def _action_quit(self) -> None:
        # Clear the queue first so "Goodbye" is the only thing left to play;
        # the shutdown path in __main__ drains it before killing the engine.
        self.speech.stop()
        self._say_status("Goodbye.")
        if self.view is not None:
            self.view.close()

    def _action_send(self) -> None:
        prompt = self.prompt.strip()
        if not prompt:
            self._say_status(
                "Your question is empty. Type something first.", interrupt=True
            )
            return
        with self._busy_lock:
            if self._busy:
                self._say_status("Still waiting for the last answer.", interrupt=True)
                return
            self._busy = True

        self.prompt = ""
        self._set_prompt("")
        self._add_turn(ROLE_USER, prompt)
        self._say_status("Sending.", interrupt=True)
        self._set_status("Waiting for Claude...")

        threading.Thread(
            target=self._request_worker,
            args=(prompt,),
            name="vi-ai-request",
            daemon=True,
        ).start()

    # -- the request ------------------------------------------------------

    def _request_worker(self, prompt: str) -> None:
        """Runs off the UI thread. Streams the answer, speaking each sentence."""
        chunker = SentenceChunker(self.config.speech.max_chunk_chars)
        pieces: list[str] = []
        spoke_anything = False
        try:
            self._post(lambda: self._safe_view("begin_assistant"))
            for delta in self.client.stream(prompt):
                pieces.append(delta)
                self._post(
                    lambda text=delta: self._safe_view("append_assistant", text)
                )
                for sentence in chunker.feed(delta):
                    self.speech.speak(sentence)
                    spoke_anything = True

            tail = chunker.flush()
            if tail:
                self.speech.speak(tail)
                spoke_anything = True

            answer = "".join(pieces)
            self.last_response = answer
            if not answer.strip():
                self._say_status("Claude sent an empty answer.")
                self._post(lambda: self._safe_view("set_status", "Empty answer."))
            elif not spoke_anything:
                # All content was markup that the sanitiser removed.
                self._say_status(
                    "Claude answered, but there was nothing to read aloud."
                )
                self._post(lambda: self._safe_view("set_status", "Ready."))
            else:
                self._post(lambda: self._safe_view("set_status", "Ready."))
        except Exception as exc:  # every failure must reach the user as speech
            message = self.client.describe_error(exc)
            log.warning("request failed: %s", exc, exc_info=True)
            self._post(lambda: self._safe_view("add_turn", ROLE_STATUS, message))
            self._post(lambda: self._safe_view("set_status", "Error."))
            self._say(message, interrupt=True)
        finally:
            with self._busy_lock:
                self._busy = False
            self._post(lambda: self._safe_view("end_assistant"))

    # -- plumbing ---------------------------------------------------------

    def _echo(self, spoken: str) -> None:
        """Speak the name of a key that was just pressed.

        Key names go in their own lane: they stop an answer being read, but
        never cancel each other, so every key typed is still heard.
        """
        if not self.config.speech.speak_key_echo:
            return
        category = (
            Category.ECHO
            if self.config.speech.interrupt_on_keypress
            else Category.MESSAGE
        )
        self.speech.speak(spoken, category=category)

    def _say(self, text: str, *, interrupt: bool = False) -> None:
        """Speak something the user must hear: an answer, a prompt, an error."""
        self.speech.speak(text, interrupt=interrupt)

    def _say_status(self, text: str, *, interrupt: bool = False) -> None:
        """Speak routine chatter, which the user can switch off in the config."""
        if not self.config.speech.speak_status:
            return
        self.speech.speak(text, interrupt=interrupt)

    def _post(self, callback) -> None:
        if self.view is not None:
            self.view.post(callback)

    def _safe_view(self, method: str, *args) -> None:
        if self.view is None:
            return
        try:
            getattr(self.view, method)(*args)
        except Exception:  # pragma: no cover - UI already tearing down
            log.debug("view.%s failed", method, exc_info=True)

    def _set_prompt(self, text: str) -> None:
        self._safe_view("set_prompt", text)

    def _set_status(self, text: str) -> None:
        self._safe_view("set_status", text)

    def _add_turn(self, role: str, text: str) -> None:
        self._safe_view("add_turn", role, text)
