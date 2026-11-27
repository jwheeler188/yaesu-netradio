#!/bin/zsh
# W5PL FT-897D network radio - macOS install of the laptop audio client.
# Installs PortAudio (Homebrew) and a private Python environment with pyaudio in this folder (.venv).
# Changes nothing else on the Mac. Safe to run again. Remove: delete this folder's .venv.
set -e
HERE="${0:A:h}"
cd "$HERE"
say() { print -P "%F{cyan}==>%f $*"; }
die() { print -P "%F{red}ERROR:%f $*"; exit 1; }

[[ -f ../client/ft897-audio.py ]] || die "client/ft897-audio.py not found. Keep the folder together."
command -v brew >/dev/null || die "Homebrew not found. Install it from https://brew.sh and run this again."
brew list --versions portaudio >/dev/null 2>&1 || { say "Installing PortAudio (Homebrew)"; brew install portaudio; }
PA_LIB="$(brew --prefix portaudio)/lib/libportaudio.dylib"
[[ -e "$PA_LIB" ]] || die "PortAudio library not found at $PA_LIB"
# Match Python's architecture to PortAudio's (Intel Homebrew on Apple Silicon = x86_64 via Rosetta)
if file "$PA_LIB" | grep -q arm64; then ARCH=arm64; else ARCH=x86_64; fi
say "PortAudio is $ARCH - building the Python environment for $ARCH"
PY="$(command -v python3)" || die "python3 not found"
arch -$ARCH "$PY" -c "import sys" 2>/dev/null || die "$PY cannot run as $ARCH. Install the universal2 Python from python.org."
rm -rf .venv
arch -$ARCH "$PY" -m venv .venv
export CFLAGS="-I$(brew --prefix portaudio)/include" LDFLAGS="-L$(brew --prefix portaudio)/lib"
arch -$ARCH .venv/bin/python -m pip install -q --upgrade pip
arch -$ARCH .venv/bin/python -m pip install -q pyaudio
arch -$ARCH .venv/bin/python -c "import pyaudio; print('pyaudio', pyaudio.__version__)"
print "$ARCH" > .arch
chmod +x ft897.sh "Start FT-897 Audio.command"
system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole 2ch" && say "BlackHole 2ch found" || {
  print -P "%F{yellow}NOTE:%f BlackHole 2ch not found. Install it, then RESTART the Mac:  brew install --cask blackhole-2ch"; }
zsh "$HERE/install-link.sh" || print -P "%F{yellow}NOTE:%f the panel's Start voice client button could not be set up (run install-link.sh to try again)"
say "Done. Start: double-click 'Start FT-897 Audio.command', or run $HERE/ft897.sh"
