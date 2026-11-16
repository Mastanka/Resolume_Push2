#!/bin/sh
# One-time setup of this folder: a Python environment with the bridge's packages in .venv.
# Needs Homebrew's libraries first:  brew install libusb cairo pkg-config git
set -e
cd "$(dirname "$0")"
for lib in libusb cairo; do
  brew list "$lib" >/dev/null 2>&1 || { echo "missing $lib: brew install libusb cairo pkg-config git"; exit 1; }
done
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "ready: ./start.command (or double-click it in Finder)"
