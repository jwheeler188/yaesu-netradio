#!/usr/bin/env bash
# Runs the FT-897D bridge tests: copies the bridge to a scratch folder and starts it in --dummy mode (no radio, no serial
# port, no sound card) on other ports, once per test file. Needs rigctld (Hamlib). Never touches a running bridge.
#   pi/tests/run_all.sh [scratch folder]        then pi/tests/run_ft891.sh for the FT-891
set -e; HERE="$(cd "$(dirname "$0")/.." && pwd)"; D=${1:-/tmp/ft897test}; mkdir -p "$D"; cd "$D"
cp "$HERE"/*.py "$HERE"/*.html "$HERE"/tests/test_*.py .; cp "$HERE/tests/test_access.py" acctest.py; cp ft897-netbridge.py nb.py
export FT897_FAKE_METERS="$D/meters.json"; B=""
stop() { [[ -n "$B" ]] && { kill $B 2>/dev/null; sleep 1.5; kill -9 $B 2>/dev/null || true; }; B=""; }
trap stop EXIT; bad=0
for t in test_stuck_tx test_lsb test_dead_radio test_repeater_state test_access; do
  rm -f settings.json passwords.json repeaters.json; echo '{"swr":0}' > meters.json
  setsid python3 nb.py --dummy -v --cat-port 14633 --audio-port 14634 --web-port 18080 --rigctld-port 14635 > "log_$t.txt" 2>&1 < /dev/null & B=$!
  sleep 5; echo "== $t"
  timeout 300 python3 "$t.py" 2>&1 | grep -v '^pass' | tee "out_$t.txt" || true
  grep -q " 0 failed" "out_$t.txt" || bad=1
  n=$(grep -c "Traceback" "log_$t.txt" || true); [[ "$n" == 0 ]] || { echo "   bridge log: $n errors (log_$t.txt)"; bad=1; }
  stop
done
[[ $bad == 0 ]] && echo "ALL PASSED" || { echo "SOME FAILED (logs in $D)"; exit 1; }
