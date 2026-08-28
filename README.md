# VI-AI

Talk to an AI with a keyboard and a speaker. Built for blind and low-vision
users: every key you press is read back to you, and every answer is spoken.

Designed to run on a Raspberry Pi with a braille keyboard and a speaker
attached, but it runs on any Linux machine with a display.

## The keys

| Key | What it does |
|---|---|
| Any key | Typed into your question, and read aloud so you can check it |
| Backspace | Deletes the last character and names it |
| **F1** | Reads your whole question back to you |
| **F2** | Deletes your whole question |
| **Enter** or **F3** | Sends your question to Claude |
| **F4** | Reads the last answer again |
| **Escape** | Stops talking |
| **F12** | Closes VI-AI |

Every one of these can be changed in the config file.

## Install

On Raspberry Pi OS, Debian or Ubuntu. On Arch, use
`sudo pacman -S python tk espeak-ng` instead of the `apt` line.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-tk espeak-ng

git clone <this repository> vi-ai
cd vi-ai
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Or run `./setup_pi.sh`, which does the same thing.

Check the speaker works before going further:

```bash
espeak-ng "Hello. If you can hear this, sound is working."
```

If you hear nothing, fix that first: `alsamixer` to raise the volume, and
`sudo raspi-config` to choose the right audio output on a Pi.

## Choose an AI

VI-AI can talk to three backends. Set `provider` in the `[api]` section of your
config file.

| Provider | Cost | Setup | Notes |
|---|---|---|---|
| `mock` | free | none | Canned replies, no network. Checks the app works. |
| `ollama` | free | one download | A real model, running on this machine. |
| `claude` | paid | an API key | The best answers. |

### Trying it with no setup: `mock`

```toml
[api]
provider = "mock"
```

Replies are canned, but they stream in like a real answer, so this exercises
the keyboard, the speech, the interruption and the window. Start here.

### A real model for free: `ollama`

[Ollama](https://ollama.com) runs a model on your own machine. No account, no
key, and no internet once the model is downloaded.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2
ollama serve
```

```toml
[api]
provider = "ollama"

[ollama]
model = "llama3.2"
```

On a Raspberry Pi, use a smaller model: `llama3.2:1b`, or `qwen2.5:0.5b` if
memory is tight. Smaller models start speaking sooner, which matters more here
than usual, because you are waiting in silence.

### The best answers: `claude`

Needs a paid API key from <https://console.anthropic.com>. **A Claude Pro or
Max subscription does not include API access** — it is billed separately.

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc
source ~/.bashrc
```

```toml
[api]
provider = "claude"
```

Keeping the key in the environment rather than in the config file means it does
not end up in a backup or get shared along with your settings.

## Configure

```bash
cp config.example.toml config.toml
```

Then edit `config.toml`. Every setting is explained in the file. The ones most
people change first:

```toml
[speech]
rate = 300              # words per minute; raise this once you are used to it
voice = "en-gb"

[ui]
font_size = 40
```

Check your changes without starting the app, sending a question, or spending
anything:

```bash
python -m vi_ai --check
```

## Run

```bash
python -m vi_ai
```

If a key on your keyboard does not do what you expect, find out what the system
calls it:

```bash
python -m vi_ai --list-keys
```

Press the key, and it will tell you the name to put in the `[keys]` section.

## Braille keyboards

Most braille keyboards and notetakers appear to Linux as ordinary USB
keyboards, usually via BRLTTY, which turns braille input into normal
keystrokes. If yours does, it will work with no configuration.

```bash
sudo apt install -y brltty
sudo brltty-setup      # or edit /etc/brltty.conf
```

## Starting automatically

To have a Pi boot straight into VI-AI, add a user service. Create
`~/.config/systemd/user/vi-ai.service`:

```ini
[Unit]
Description=VI-AI
After=graphical-session.target

[Service]
Environment=ANTHROPIC_API_KEY=sk-ant-...
WorkingDirectory=%h/vi-ai
ExecStart=%h/vi-ai/.venv/bin/python -m vi_ai
Restart=on-failure

[Install]
WantedBy=graphical-session.target
```

Then `systemctl --user enable --now vi-ai`.

## If something goes wrong

VI-AI says any problem out loud and also writes it on screen. The most common
ones:

| What you hear or see | What to do |
|---|---|
| "No speech engine was found" | `sudo apt install espeak-ng` |
| "No Claude API key was found" | Set `ANTHROPIC_API_KEY`, or switch to `mock` or `ollama` |
| "Your Claude API key was not accepted" | The key is wrong, revoked, or the account has no credit |
| "Cannot reach Claude" | Check the network connection |
| "Cannot reach Ollama" | Start it: `ollama serve` |
| "The model ... is not installed" | `ollama pull <name>` |
| "Could not open a window" | VI-AI needs a desktop session, not a bare console |

For more detail: `python -m vi_ai --log-level debug`.

## A voice you can actually understand

espeak-ng is tiny, instant and installed everywhere, but it is a robot and
tiring to listen to. [Piper](https://github.com/rhasspy/piper) is a neural
voice that runs offline on the same machine and sounds close to a person.
It is also *quicker* per phrase, because the voice is loaded once at startup
rather than for every phrase.

```bash
pip install -r requirements-piper.txt
python -m piper.download_voices en_US-lessac-medium --download-dir voices
```

```toml
[speech]
engine = "piper"
piper_model = "voices/en_US-lessac-medium.onnx"
```

Listen to the voices at <https://rhasspy.github.io/piper-samples/> and pick one
you find clear. On a Raspberry Pi prefer a `low` voice, such as
`en_US-lessac-low`: less memory, and it starts speaking sooner.

Piper needs a program to play the sound. VI-AI finds one automatically, trying
`aplay`, `pw-cat`, `pw-play`, `paplay` and `ffplay`. On Raspberry Pi OS `aplay`
is already there; elsewhere install `alsa-utils` or `pulseaudio-utils`.

## If typing sounds like it is falling behind

Saying a key name out loud takes longer than pressing the next key, so a fast
typist always outruns the voice. Two settings control what happens then:

```toml
[speech]
rate = 300              # faster speech finishes each key name sooner
max_pending_echoes = 2  # how far behind the voice may fall before it skips keys
```

Set `max_pending_echoes = 1` to stay as close to your fingers as possible, at
the cost of skipping more keys, or raise it to hear every single key at the
cost of falling further behind. Piper is quicker per key than espeak-ng, so
switching voice helps here too.

## Development

```bash
python -m pytest tests/ -q              # test_ui.py skips without a display
xvfb-run -a python -m pytest tests/ -q  # includes the real-window tests
```

See `CLAUDE.md` for the architecture and `todo.md` for what is left.
