"""The window.

Deliberately plain: a transcript, the question being typed, and a status line.
There is nothing to click and nothing to configure here, because every setting
lives in the config file and every action is a key.

Everything on screen is also spoken, so the display is a courtesy for a sighted
helper and for users with some remaining vision. That is why it is large, high
contrast, and always scrolled to the newest text.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import font as tkfont

from .app import ROLE_ASSISTANT, ROLE_STATUS, ROLE_USER
from .config import Config

log = logging.getLogger(__name__)

# How often the UI thread checks for work posted by background threads.
_POLL_MS = 30

# Transcript lines kept before the oldest are dropped, so a long session does
# not grow without bound.
_MAX_TRANSCRIPT_LINES = 2000

# X11 modifier bits for control, alt and super. Also used by --list-keys.
MODIFIER_MASK = 0x4 | 0x8 | 0x40

_ROLE_LABELS = {
    ROLE_USER: "You",
    ROLE_ASSISTANT: "Robot",
    ROLE_STATUS: "Note",
}


class TkView:
    """A Tk implementation of the controller's `View` interface."""

    def __init__(self, config: Config, on_key) -> None:
        self.config = config
        self._on_key = on_key
        self._callbacks: queue.Queue = queue.Queue()
        self._assistant_open = False
        self._closing = False

        ui = config.ui
        self.root = tk.Tk()
        self.root.title("VI-AI")
        self.root.configure(background=ui.background)
        self.root.geometry(f"{ui.window_width}x{ui.window_height}")
        if ui.fullscreen:
            self.root.attributes("-fullscreen", True)

        self._font = tkfont.Font(family=ui.font_family, size=ui.font_size)
        self._label_font = tkfont.Font(
            family=ui.font_family, size=ui.font_size, weight="bold"
        )
        self._small_font = tkfont.Font(
            family=ui.font_family, size=max(10, ui.font_size - 8)
        )

        self._build_widgets()
        self._bind_keys()

        self.root.after(_POLL_MS, self._drain)

    # -- construction -----------------------------------------------------

    def _build_widgets(self) -> None:
        ui = self.config.ui

        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.transcript = tk.Text(
            self.root,
            wrap="word",
            font=self._font,
            background=ui.background,
            foreground=ui.foreground,
            insertbackground=ui.background,  # no visible caret; it is read-only
            borderwidth=0,
            highlightthickness=0,
            padx=24,
            pady=18,
            spacing1=6,
            spacing3=10,
            takefocus=0,
            cursor="arrow",
        )
        self.transcript.grid(row=0, column=0, sticky="nsew")
        self.transcript.tag_configure(ROLE_USER, foreground=ui.user_color)
        self.transcript.tag_configure(ROLE_ASSISTANT, foreground=ui.assistant_color)
        self.transcript.tag_configure(ROLE_STATUS, foreground=ui.status_color)
        self.transcript.tag_configure("speaker", font=self._label_font)
        self.transcript.configure(state="disabled")

        if ui.show_prompt_line:
            self.prompt_label = tk.Label(
                self.root,
                text="",
                font=self._font,
                background=ui.background,
                foreground=ui.user_color,
                justify="left",
                anchor="w",
                padx=24,
                pady=12,
                wraplength=max(200, ui.window_width - 60),
            )
            self.prompt_label.grid(row=1, column=0, sticky="ew")
            self.prompt_label.bind("<Configure>", self._resize_wrap)
        else:
            self.prompt_label = None

        self.status_label = tk.Label(
            self.root,
            text="Starting...",
            font=self._small_font,
            background=ui.background,
            foreground=ui.status_color,
            anchor="w",
            padx=24,
            pady=8,
        )
        self.status_label.grid(row=2, column=0, sticky="ew")

        self.set_prompt("")

    def _resize_wrap(self, event) -> None:
        if self.prompt_label is not None:
            self.prompt_label.configure(wraplength=max(200, event.width - 60))

    def _bind_keys(self) -> None:
        # Bound on the toplevel rather than on a focused entry widget: the
        # controller owns the prompt buffer, so no widget should ever consume a
        # keystroke on its own.
        self.root.bind_all("<KeyPress>", self._handle_key)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.focus_force()

    def _handle_key(self, event) -> str:
        modified = bool(event.state & MODIFIER_MASK)
        try:
            self._on_key(event.keysym, event.char, modified=modified)
        except Exception:  # pragma: no cover - a bad key must not kill the app
            log.exception("key handler failed for %r", event.keysym)
        return "break"

    # -- View interface ---------------------------------------------------

    def post(self, callback) -> None:
        """Queue `callback` to run on the UI thread. Safe from any thread."""
        self._callbacks.put(callback)

    def _drain(self) -> None:
        while True:
            try:
                callback = self._callbacks.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:  # pragma: no cover
                log.exception("posted callback failed")
        if not self._closing:
            self.root.after(_POLL_MS, self._drain)

    def set_prompt(self, text: str) -> None:
        if self.prompt_label is None:
            return
        self.prompt_label.configure(text=f"> {text}" if text else "> ")

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def add_turn(self, role: str, text: str) -> None:
        self._assistant_open = False
        self._insert_speaker(role)
        self._insert(text.strip(), role)

    def begin_assistant(self) -> None:
        self._assistant_open = False
        self._insert_speaker(ROLE_ASSISTANT)
        self._assistant_open = True

    def append_assistant(self, text: str) -> None:
        if not self._assistant_open:
            self.begin_assistant()
        self._insert(text, ROLE_ASSISTANT)

    def end_assistant(self) -> None:
        self._assistant_open = False

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.root.quit()
        except tk.TclError:  # pragma: no cover - already torn down
            pass

    def run(self) -> None:
        self.root.mainloop()
        try:
            self.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass

    # -- transcript helpers -----------------------------------------------

    def _insert_speaker(self, role: str) -> None:
        # The blank line goes before the new turn rather than after the previous
        # one, so the newest text always sits directly above the prompt line
        # instead of being pushed up by trailing whitespace.
        if self.transcript.index("end-1c") != "1.0":
            self._insert("\n\n")
        label = _ROLE_LABELS.get(role, role)
        self._insert(f"{label}: ", role, "speaker")

    def _insert(self, text: str, *tags: str) -> None:
        if self._closing:
            return
        try:
            self.transcript.configure(state="normal")
            self.transcript.insert("end", text, tags)
            self._trim()
            self.transcript.configure(state="disabled")
            self.transcript.see("end-1c")
        except tk.TclError:  # pragma: no cover - window closing
            log.debug("transcript insert failed", exc_info=True)

    def _trim(self) -> None:
        line_count = int(self.transcript.index("end-1c").split(".")[0])
        if line_count > _MAX_TRANSCRIPT_LINES:
            excess = line_count - _MAX_TRANSCRIPT_LINES
            self.transcript.delete("1.0", f"{excess + 1}.0")
