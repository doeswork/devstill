#!/usr/bin/env bash
# Unattended Pi-vs-Devstill benchmark campaign.
#
# Starts in the background and keeps running after the terminal closes. Every
# suite in benchmarks/suites.json runs in turn; a suite that fails is resumed
# once and then recorded as failed, a suite whose requirements are missing
# (Docker for SWE-bench) is skipped with the reason, and the campaign moves on.
# Records: .devstill/bench-history/<campaign>/ (REPORT.md, campaign.log, raw
# results) and benchmarks/history/ (git-tracked summary and HISTORY.md).
#
#   ./pi_vs_devstill_bench.sh                      every suite
#   ./pi_vs_devstill_bench.sh --suite loop-dev     one suite (repeatable), or --tag core
#   ./pi_vs_devstill_bench.sh --repeats 3 --sampling greedy --note "new model"
#   ./pi_vs_devstill_bench.sh --max-hours 8        stop starting work after 8 hours
#   ./pi_vs_devstill_bench.sh --dry-run            checks and plan only (no records)
#   ./pi_vs_devstill_bench.sh --status             progress of the latest campaign
#   ./pi_vs_devstill_bench.sh --report             print the latest REPORT.md
#   ./pi_vs_devstill_bench.sh --history            one line per past campaign
#   ./pi_vs_devstill_bench.sh --stop               stop gracefully (resumable)
#   ./pi_vs_devstill_bench.sh --resume [DIR]       continue an interrupted campaign
#   ./pi_vs_devstill_bench.sh --foreground ...     stay attached to this terminal
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
export PYTHONPATH="$PWD/src:$PWD"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
PY=.venv/bin/python
HISTORY=.devstill/bench-history

running_pid() {
  local pid
  pid=$(cat "$HISTORY/runner.pid" 2>/dev/null) || return 1
  kill -0 "$pid" 2>/dev/null && echo "$pid"
}

case "${1:-}" in
  --status) exec "$PY" -m benchmarks.bench_suite status ;;
  --history) exec "$PY" -m benchmarks.bench_suite history ;;
  --report)
    cat "$HISTORY/latest/REPORT.md" 2>/dev/null || echo "No report yet (the campaign writes it when it finishes)."
    exit 0 ;;
  --stop)
    if pid=$(running_pid); then
      kill -TERM "$pid"
      echo "Stopping campaign (pid $pid). Finished work is kept; continue later with --resume."
    else
      echo "No campaign is running."
    fi
    exit 0 ;;
esac

if [[ ! -x "$PY" ]]; then
  echo "Missing $PY: create the project virtualenv first." >&2
  exit 1
fi

foreground=0
args=()
for arg in "$@"; do
  case "$arg" in
    --foreground) foreground=1 ;;
    --dry-run) foreground=1; args+=("$arg") ;;
    *) args+=("$arg") ;;
  esac
done

if [[ $foreground -eq 1 ]]; then
  exec "$PY" -m benchmarks.bench_suite run "${args[@]}"
fi

if pid=$(running_pid); then
  echo "A campaign is already running (pid $pid). Use --status, or --stop first." >&2
  exit 1
fi
mkdir -p "$HISTORY"
log="$HISTORY/launch-$(date -u +%Y%m%dT%H%M%SZ).log"
setsid nohup "$0" --foreground "${args[@]}" >"$log" 2>&1 </dev/null &
sleep 2
echo "Benchmark campaign started in the background (launcher log: $log)."
echo "  Progress:  ./pi_vs_devstill_bench.sh --status"
echo "  Results:   ./pi_vs_devstill_bench.sh --report   (when it finishes)"
echo "  Stop:      ./pi_vs_devstill_bench.sh --stop      (resume with --resume)"
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "WSL: keep the PC awake and leave one WSL terminal open, or Windows may stop Linux."
fi
