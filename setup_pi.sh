#!/usr/bin/env bash
# Set up VI-AI on Raspberry Pi OS, Debian or Ubuntu.
#
# Installs the system packages, creates a virtual environment, and copies the
# example config. Does not set your API key; see the README for that.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

echo "==> Installing system packages (needs sudo)"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-tk espeak-ng

echo "==> Creating the virtual environment in .venv"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

if [ ! -f config.toml ]; then
    echo "==> Creating config.toml from the example"
    cp config.example.toml config.toml
else
    echo "==> config.toml already exists, leaving it alone"
fi

echo "==> Checking that sound works"
if espeak-ng "Setup is nearly finished." 2>/dev/null; then
    echo "    Sound is working."
else
    echo "    Could not play audio. Check the speaker, then run:"
    echo "      alsamixer          # raise the volume"
    echo "      sudo raspi-config  # choose the audio output"
fi

echo
echo "Done. Two things left:"
echo
echo "  1. Set your API key:"
echo "       echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc"
echo "       source ~/.bashrc"
echo
echo "  2. Check the setup, then run it:"
echo "       ./.venv/bin/python -m vi_ai --check"
echo "       ./.venv/bin/python -m vi_ai"
