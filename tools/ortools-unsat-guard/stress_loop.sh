#!/usr/bin/env bash
# Stress-reproduce the CP-SAT native abort under a given interpreter.
#
# Usage: stress_loop.sh <python-exe> <capture-root> <iterations> <cores> [subset|full]
#
# Runs tests/layout/test_freeform.py under xdist -n 8 with the capture plugin,
# pinned to <cores>. "subset" (default) is the 15-test -k selection from the
# scope report; "full" is the whole file, which is what actually reproduced
# the abort (1 in 20 runs on the stock 9.15.6755 wheel, 2026-09-13). Each
# iteration leaves <capture-root>/run-N.log, a summary line in
# <capture-root>/summary.txt, and, only if a solve aborted, its model and
# parameters under <capture-root>/run-N/. Touch <capture-root>/STOP to end
# early. Waits while the box's mean runnable process count is 64 or more.
set -u
PY="$1"; ROOT="$2"; ITER="$3"; CORES="$4"; MODE="${5:-subset}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
case "$MODE" in
  subset) SELECT=(-k "portable or telemetry or plastic"); LIMIT=900 ;;
  full) SELECT=(); LIMIT=1800 ;;
  *) echo "mode must be subset or full" >&2; exit 2 ;;
esac
mkdir -p "$ROOT"
: > "$ROOT/summary.txt"
cd "$REPO"
for i in $(seq 1 "$ITER"); do
  [ -f "$ROOT/STOP" ] && { echo "stopped before run $i" >> "$ROOT/summary.txt"; break; }
  while :; do
    r=$(vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print sum/5}')
    awk -v r="$r" 'BEGIN{exit !(r<64)}' && break
    echo "run $i waiting: runnable $r" >> "$ROOT/summary.txt"; sleep 60
  done
  t0=$(date +%s)
  PYTHONPATH="$HERE" FLAB_CPSAT_CAPTURE_DIR="$ROOT/run-$i" \
    timeout "$LIMIT" taskset -c "$CORES" "$PY" -m pytest -p flab_cpsat_capture -q \
    -p no:cacheprovider -n 8 tests/layout/test_freeform.py "${SELECT[@]}" \
    > "$ROOT/run-$i.log" 2>&1
  rc=$?
  t1=$(date +%s)
  left=$(find "$ROOT/run-$i" -name '*.model.pb' 2>/dev/null | wc -l)
  fatal=$(grep -c -E 'Check failed|Fatal Python error|Aborted|SIGABRT|crashed' "$ROOT/run-$i.log")
  echo "run $i exit $rc wall_s $((t1-t0)) runnable_before $r leftover_models $left fatal_lines $fatal" >> "$ROOT/summary.txt"
done
echo done > "$ROOT/DONE"
