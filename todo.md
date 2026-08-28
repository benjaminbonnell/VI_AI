# TODO

Status of the VI-AI build. Last updated 2026-08-28.

## Done

### Core requirement from the brief
- [x] Key press is read aloud as it is typed, so the user can verify it
- [x] F1 speaks the whole current question
- [x] F2 deletes the whole current question
- [x] Enter and F3 send the question to Claude
- [x] The answer is spoken aloud
- [x] Simple GUI: the conversation only, no settings, nothing to click
- [x] All settings come from a config file (`config.example.toml`)

### Supporting work
- [x] Config loader with validation and readable error messages
- [x] Speech queue on a worker thread, with instant interruption
- [x] espeak-ng / espeak / spd-say auto-detection, a first-class piper engine
      (neural voice, model loaded once), and a `command` engine for anything else
- [x] Fixed: espeak-ng clipped the last word of every phrase
- [x] Fixed: letters were not spoken while typing, because each keystroke
      cancelled the previous key name
- [x] Piper silence trimming and cheapest-first audio player selection, which
      together cut the cost of speaking one key name by about half
- [x] Spoken names for punctuation, digits, and control keys
- [x] Backspace names the character it removed
- [x] Streaming responses, spoken sentence by sentence as they arrive
- [x] Markdown stripped before speaking
- [x] API errors turned into short spoken sentences
- [x] Conversation history so Claude remembers the thread
- [x] `--check` to verify a setup without making an API call
- [x] `--list-keys` to find keysym names on an unfamiliar keyboard
- [x] F4 repeats the last answer, Escape stops speech, F12 quits
- [x] Three AI backends behind one interface: Claude, Ollama (free, local) and
      a mock with no network, so the app can be used without a paid API key
- [x] 147 tests, including real-window Tk tests, real-SDK tests against a mock
      Anthropic endpoint, and Ollama tests against a mock Ollama server

## Not done, and why

- [ ] **Verify audio on the Pi.** Audio now works on the development machine
      (piper through pw-cat). The Pi has different audio plumbing: check
      `aplay` is present, and that `--check` finds a player.
- [ ] **Try a real model through Ollama.** The provider is tested against a
      mock Ollama server, but no real model has answered a question yet.
      `ollama serve`, then `python -m vi_ai --check`.
- [ ] **Test with a real braille keyboard.** Handling assumes BRLTTY presents
      it as a standard HID keyboard, which is the normal case. Confirm with
      `python -m vi_ai --list-keys`.

## Worth considering later

Not built, because the brief asked for a simple app and none of these are
needed for it to work.

- [ ] Autostart on boot as a kiosk (a systemd unit is sketched in the README)
- [ ] Save the transcript to a file, and read an earlier conversation back
- [ ] A key to spell the question out character by character, for checking an
      exact string such as a code or an address
- [ ] Voice input, so a question can be spoken instead of typed
- [ ] A persistent audio player, to save the ~0.05s process startup paid on
      every key name. Needs playback position tracking to know when an
      utterance has finished, so it was not worth it yet.
- [ ] Move between earlier answers with the arrow keys, to re-hear one that is
      not the most recent
- [ ] Handle a braille keyboard that sends Perkins chords directly rather than
      through BRLTTY
