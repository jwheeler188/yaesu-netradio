#!/bin/zsh
# W5PL FT-897D network radio - start the laptop audio client (macOS). Run install.sh once first.
#   ./ft897.sh                         asks for the Pi's address the first time (saved in server.txt), audio = BlackHole 2ch
#   ./ft897.sh --server 192.168.1.50   another address;  --help for all options
# WSJT-X: Rig "Hamlib NET rigctl", Network Server <Pi address>:4533, PTT CAT, audio BlackHole 2ch in/out.
# Only one program at a time can use BlackHole 2ch.
HERE="${0:A:h}"
cd "$HERE"
[[ -x .venv/bin/python && -f .arch ]] || { echo "Run ./install.sh first (in $HERE)."; exit 1; }
for p in $(pgrep -f 'ft897-audio[.]py'); do [[ "$(ps -o comm= -p $p)" == *[Pp]ython* ]] && kill $p; done
exec arch -"$(cat .arch)" ./.venv/bin/python -u ../client/ft897-audio.py "$@"
