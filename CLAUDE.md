# CLAUDE.md

Guidance for working in this repository.

## What this is

VI-AI is a spoken AI client for blind and low-vision users. It runs on a Linux
machine (the target is a Raspberry Pi) with a braille keyboard and a speaker
attached. Everything is driven by the keyboard and confirmed by speech.

Three backends, chosen by `[api] provider`: `claude` (paid API key), `ollama`
(a free local model) and `mock` (canned replies, no network). They exist so the
app can be tried and developed without an API key; a Claude Pro subscription
does not include one.

Behaviour the app must always have:

- Every key press is read aloud, so the user can verify what they typed.
- **F1** reads the whole question back.
- **F2** deletes the whole question.
- **Enter** or **F3** sends the question to Claude; the answer is spoken.
- All settings live in a config file. The window has no settings and nothing to
  click.

## Commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python -m vi_ai                      # run
python -m vi_ai --check              # report config, engine and key, no API call
python -m vi_ai --list-keys          # name each key pressed; use to fill in [keys]

python -m pytest tests/ -q           # tests (test_ui.py skips without a display)
xvfb-run -a python -m pytest tests/ -q   # includes the real-window tests
```

`tests/test_claude_client.py` runs the real `anthropic` SDK against a mock HTTP
server, so it needs no network and no API key. It skips if the SDK is absent.

## Architecture

```
__main__.py    argument parsing, wiring, shutdown
  config.py    TOML -> dataclasses, validation, defaults
  speech.py    TTS engines + the interruptible speech queue (worker thread)
  keynames.py  keystroke -> spoken name
  textproc.py  markdown stripping + streaming sentence chunker
  providers/   the AI backends
    base.py      Provider: history handling shared by all of them
    claude.py    the Anthropic SDK
    ollama.py    a local model over HTTP, standard library only
    mock.py      canned replies, no network
  app.py       ViAiApp: all behaviour. Knows nothing about Tk.
  ui.py        TkView: the window. Knows nothing about the AI.
```

`app.ViAiApp` is the only place that decides what a key means. It talks to the
screen through the `View` protocol, which `ui.TkView` implements, and to the AI
through the `Provider` interface. Keeping those apart is what makes the
behaviour testable without a display or a key; do not reach into Tk from
`app.py`, or call an API from `ui.py`.

Adding a backend means one file in `providers/`, a name in `config.PROVIDERS`,
and a branch in `providers.build_provider`. Subclass `Provider` and implement
`preflight`, `_stream` and `describe_error`; history handling is inherited, and
`stream()` already commits a turn only once it has produced text.

### Threading

Three threads, with strict rules:

- **UI thread** — Tk, and every `handle_key` call. Must never block. It never
  waits on audio or on the network.
- **Speech thread** (one, in `SpeechManager`) — runs the TTS subprocess and
  waits for it.
- **Request thread** (one per question) — streams from the API.

Background threads reach the UI only through `view.post(callback)`, which
queues onto a `queue.Queue` that the UI thread drains on a timer. Tk is not
thread safe; calling a widget method from a worker thread will eventually
crash in a way that is very hard to reproduce.

### Speech interruption

`SpeechManager` keeps an epoch counter. Interrupting bumps the epoch, empties
the queue, and kills the running process; anything queued under an older epoch
is dropped instead of spoken late. This is why a key press is heard instantly
even while a long answer is being read.

Key echo is **latest-wins**: typing fast cuts off the previous key's name
rather than queueing every one. Queueing would put the user seconds behind
their own typing.

## Rules that exist for accessibility reasons

Breaking any of these makes the app unusable for its actual users, and none of
them are obvious from the code alone.

1. **A key press must always be audible immediately.** Nothing may be queued in
   front of the key echo. See `interrupt_on_keypress`.
2. **Never fail silently.** Every error becomes a short spoken sentence, and is
   also written to the transcript. `describe_api_error` exists so the user
   hears "check that this machine is connected to the internet" and not a
   stack trace.
3. **A missing speech engine must not stop the app**, but it must be shown on
   screen, because a spoken warning about speech being broken cannot be heard.
4. **The window never takes keyboard focus away from the controller.** The
   transcript is a disabled `Text` with `takefocus=0`, and keys are bound with
   `bind_all` on the toplevel. No entry widget owns the prompt; `ViAiApp.prompt`
   does.
5. **Answers are written for the ear.** The system prompt in `config.py` asks
   for plain spoken prose, and `textproc.speech_text` strips any markup that
   survives. A reply full of asterisks and backticks is unlistenable.
6. **Speech starts before the answer finishes.** The response is streamed and
   spoken sentence by sentence; waiting for the whole answer would mean many
   seconds of silence.
7. **Key names queue among themselves; they never cancel each other.** See
   `Category.ECHO`. This is rule 1's exact limit, and getting it wrong is not
   theoretical: an earlier version let each keystroke cancel everything, and
   because a key name takes longer to say than it takes to press the next key,
   typing produced near silence.
8. **Whatever is fed to a speech engine must be terminated properly.**
   espeak-ng clips the tail of the last word when its stdin has no trailing
   newline. It is silent for a word ending in a vowel, so it looks intermittent.

## Speech: the three things that were wrong in practice

All three came from someone using the app, not from reading the code, and each
has a regression test.

| Symptom | Cause | Fix |
|---|---|---|
| "It does not fully speak the last word" | `espeak-ng --stdin` clips an unterminated final line | `EspeakEngine._argv_for` appends `\n`; the test compares frame counts against espeak's own output |
| "It does not speak the letters as they are pressed" | every keystroke cancelled the previous key name, which took longer to say than the gap between keys | `Category.ECHO`: key names cancel answers but not each other, with `max_pending_echoes` bounding the lag |
| "It is hard to understand" | espeak-ng is a formant synthesiser | the `piper` engine, a neural voice |

Measured costs per key name, which is the number that matters because it is
paid on every keystroke:

| | per letter |
|---|---|
| espeak-ng, one process per phrase | 0.17s before any sound |
| piper, model loaded once, in this process | 0.04s |
| audio player process startup | 0.05s (aplay, pw-cat) to 0.13s (paplay) |

Piper synthesises in this process so the model is loaded once; a process per
phrase costs 1.3s. Audio goes to a *separate* player process so that stopping
speech means killing the player, leaving the loaded model alone. Piper pads
every phrase with silence worth about a third of a spoken letter, so
`_trim_silence` removes it.

## Provider notes

`preflight()` must be cheap and free: it runs at startup and in `--check`.
Claude only builds a client, Ollama asks the local server which models are
installed, and mock does nothing. It returns a sentence saying what to do, not
an error code, because that sentence is read aloud.

Ollama uses `urllib` rather than a client library, so the free path costs the
project no extra dependency. Its replies are newline-delimited JSON.

## Claude API usage

- Model: `claude-opus-5` by default, set in `[api] model`.
- `client.messages.stream(...)`, consumed through `stream.text_stream`.
- `output_config={"effort": ...}`, default `medium`. Effort trades answer
  quality against the silence before speech starts, which matters more here
  than in a text app. `effort = "none"` omits the parameter for older models.
- Thinking is left unset: on Opus 5 that means adaptive thinking, which is what
  we want, and it stays valid on models that predate the parameter.
- `max_tokens` is 4096 rather than the usual larger default. This is deliberate:
  the answer is listened to, and 4096 tokens is already several minutes of
  speech.
- History is committed only once a response produces text, so a failed request
  leaves the conversation unchanged and the user can just try again.

## Hardware notes

Braille keyboards and notetakers normally present as ordinary USB HID
keyboards, usually through BRLTTY, which translates braille input into
keystrokes. That means Tk sees standard key events and no special handling is
needed. If a device sends unexpected keysyms, use `python -m vi_ai --list-keys`
to find the real names and put them in the `[keys]` section.

## Known gaps

See `todo.md`.
