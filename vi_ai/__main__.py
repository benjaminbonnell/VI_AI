"""Entry point: `python -m vi_ai`."""

from __future__ import annotations

import argparse
import logging
import sys
import tkinter as tk

from . import __version__
from .app import ViAiApp
from .config import CONFIG_ENV_VAR, ConfigError, load_config
from .keynames import name_key
from .providers import build_provider
from .speech import NullEngine, SpeechManager, build_engine

log = logging.getLogger("vi_ai")

# How long to let queued speech finish after the window closes.
_SHUTDOWN_DRAIN_SECONDS = 2.0

NO_DISPLAY_HINT = (
    "Could not open a window. VI-AI needs a graphical session.\n"
    "On a Raspberry Pi booted to the console, either start the desktop or run\n"
    "VI-AI from an X session (for example with `startx`), or set DISPLAY if you\n"
    "are connecting remotely."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vi_ai",
        description="A spoken AI client for blind and low-vision users.",
    )
    parser.add_argument(
        "-c", "--config", metavar="PATH",
        help=f"config file to use (default: ${CONFIG_ENV_VAR}, ./config.toml, "
             "~/.config/vi_ai/config.toml, /etc/vi_ai/config.toml)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="report the configuration, speech engine and AI backend, then "
             "exit (never sends a question, so it costs nothing)",
    )
    parser.add_argument(
        "--list-keys", action="store_true",
        help="open a window that names each key you press, for working out the "
             "keysym names to put in the config",
    )
    parser.add_argument(
        "--log-level", default="warning",
        choices=("debug", "info", "warning", "error"),
        help="logging verbosity (default: warning)",
    )
    parser.add_argument("--version", action="version", version=f"VI-AI {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    for warning in config.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    engine = build_engine(config.speech)

    if args.check:
        return _run_check(config, engine)
    if args.list_keys:
        return _run_list_keys(config, engine)

    speech = SpeechManager(
        engine,
        on_error=lambda message: log.error("%s", message),
        max_pending_echoes=config.speech.max_pending_echoes,
    )
    app = ViAiApp(config, speech, build_provider(config))

    try:
        view = _build_view(config, app)
    except tk.TclError as exc:
        speech.shutdown()
        print(f"{NO_DISPLAY_HINT}\n\nDetail: {exc}", file=sys.stderr)
        return 1

    try:
        view.post(app.start)
        view.run()
    except KeyboardInterrupt:
        pass
    finally:
        # Let a queued sign-off finish, then stop the engine.
        speech.wait_until_idle(_SHUTDOWN_DRAIN_SECONDS)
        speech.shutdown()
    return 0


def _build_view(config, app):
    """Build the window and wire it to the controller.

    Tk is imported here rather than at module scope so that --check and
    --version work on a machine with no display.
    """
    from .ui import TkView

    view = TkView(config, app.handle_key)
    app.attach_view(view)
    return view


def _run_check(config, engine) -> int:
    """Report what the app would do, without ever sending a question.

    The backend's own preflight runs, which is free for all three: Claude only
    builds a client, Ollama asks the local server what it has installed, and
    the mock backend does nothing.
    """
    source = config.source or "built-in defaults (no config file found)"
    provider = build_provider(config)

    print(f"VI-AI {__version__}")
    print(f"Config:        {source}")
    print(f"AI backend:    {provider.describe()}")
    print(f"Max tokens:    {config.api.max_tokens}")
    print(f"Speech engine: {engine.describe()}")
    print("Keys:")
    for action in ("send", "speak_prompt", "clear_prompt", "repeat_last",
                   "stop_speech", "quit"):
        keys = ", ".join(getattr(config.keys, action))
        print(f"  {action:<13} {keys}")

    problems = []
    if isinstance(engine, NullEngine):
        problems.append("no speech engine: install espeak-ng")
    backend_problem = provider.preflight()
    if backend_problem:
        problems.append(backend_problem)

    if problems:
        print("\nProblems:")
        for problem in problems:
            print(f"  - {problem}")
        if config.api.provider == "claude":
            print("\nTo try VI-AI without a paid API key, set this in your "
                  "config file:")
            print('    [api]')
            print('    provider = "mock"    # canned replies, no network')
            print('    provider = "ollama"  # a real model, running locally')
        return 1
    print("\nAll good.")
    return 0


def _run_list_keys(config, engine) -> int:
    """Name each key pressed, on screen and aloud.

    Braille keyboards and notetakers do not always send the keysyms you expect;
    this shows exactly what to put in the [keys] section of the config.
    """
    from .ui import MODIFIER_MASK  # the same modifier bits the app uses

    speech = SpeechManager(engine)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        speech.shutdown()
        print(f"{NO_DISPLAY_HINT}\n\nDetail: {exc}", file=sys.stderr)
        return 1

    ui = config.ui
    root.title("VI-AI key names")
    root.configure(background=ui.background)
    root.geometry(f"{ui.window_width}x{ui.window_height}")

    label = tk.Label(
        root,
        text="Press any key.\nPress Escape twice to finish.",
        font=(ui.font_family, ui.font_size),
        background=ui.background,
        foreground=ui.foreground,
        justify="left",
        wraplength=max(200, ui.window_width - 60),
    )
    label.pack(expand=True, fill="both", padx=24, pady=24)

    state = {"last_escape": False}

    def on_key(event) -> str:
        if event.keysym == "Escape" and state["last_escape"]:
            root.quit()
            return "break"
        state["last_escape"] = event.keysym == "Escape"

        spoken = name_key(event.keysym, event.char) or "(no name)"
        modifiers = "yes" if event.state & MODIFIER_MASK else "no"
        text = (
            f"keysym:   {event.keysym}\n"
            f"spoken:   {spoken}\n"
            f"char:     {event.char!r}\n"
            f"modifier: {modifiers}\n\n"
            f"Put {event.keysym!r} in the [keys] section of your config.\n"
            f"Press Escape twice to finish."
        )
        label.configure(text=text)
        print(f"keysym={event.keysym!r} char={event.char!r} spoken={spoken!r}")
        speech.speak(f"{spoken}. keysym {event.keysym}", interrupt=True)
        return "break"

    root.bind_all("<KeyPress>", on_key)
    root.focus_force()
    speech.speak("Key name test. Press any key. Press escape twice to finish.")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
        speech.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
