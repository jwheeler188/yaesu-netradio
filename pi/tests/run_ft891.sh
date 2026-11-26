#!/usr/bin/env bash
# Runs test_ft891.py: copies the bridge to a scratch folder and runs it on other ports against the pretend radio. Needs rigctld.
set -e; HERE="$(cd "$(dirname "$0")/.." && pwd)"; D=${1:-/tmp/ft891test}; mkdir -p "$D"; cd "$D"
cp "$HERE"/*.py "$HERE"/*.html "$HERE"/tests/sim_ft891.py "$HERE"/tests/test_ft891.py .; cp "$HERE/tests/test_access.py" acctest.py; cp ft897-netbridge.py nb.py; echo '{}' > f.json; echo '{}' > meters.json
rm -f settings.json passwords.json repeaters.json log.txt
setsid python3 sim_ft891.py "$D/tty" log.txt f.json > sim.out 2>&1 < /dev/null & S=$!
sleep 1
setsid python3 nb.py --radio ft891 --serial "$D/tty" --card null -v --cat-port 14633 --audio-port 14634 --web-port 18080 --rigctld-port 14635 > log_ft891.txt 2>&1 < /dev/null & B=$!
trap 'kill $B $S 2>/dev/null; sleep 1; kill -9 $B $S 2>/dev/null; pkill -f "rigctld -m 1036 -T 127.0.0.1 -t 14635" || true' EXIT
sleep 8; FT897_FAKE_METERS=meters.json timeout 420 python3 test_ft891.py; grep -ciE "traceback|failed:" log_ft891.txt || true
