#!/usr/bin/env bash
# Prepare the zustand (React/TypeScript) benchmark corpus:
#   1. a pinned checkout of zustand v5.0.8 in the benchmark tree,
#   2. its exact locked dependencies (pnpm, install scripts disabled),
#   3. one run of its test suite, timed, as a sanity check.
# Safe to re-run: existing steps are skipped. Writes a summary to
# $BENCH/zustand-prepare.json and the full output to $BENCH/zustand-prepare.log.
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
BENCH=${BENCH:-/tmp/devstill-retrieval-bench}
REPO="$BENCH/repos/zustand-5.0.8"
COMMIT=0ea8be27db13019819fc1c0492e5b672dd7b6f4b
LOG="$BENCH/zustand-prepare.log"
SUMMARY="$BENCH/zustand-prepare.json"

mkdir -p "$BENCH/repos"
: >"$LOG"
step() { echo "== $*" | tee -a "$LOG"; }

step "checkout zustand v5.0.8 into $REPO"
if [ ! -d "$REPO/.git" ]; then
  git -c advice.detachedHead=false clone -q --branch v5.0.8 \
    https://github.com/pmndrs/zustand.git "$REPO" >>"$LOG" 2>&1
fi
actual=$(git -C "$REPO" rev-parse HEAD)
if [ "$actual" != "$COMMIT" ]; then
  echo "unexpected commit $actual (wanted $COMMIT)" | tee -a "$LOG"
  exit 1
fi

step "install locked dependencies (pnpm 9.15.5, no install scripts; package store read-only)"
start=$(date +%s)
(cd "$(dirname "$0")/../.." && .venv/bin/python -c \
  "from benchmarks.retrieval import js_corpus as j; j.install('$REPO', '$LOG')")
install_seconds=$(( $(date +%s) - start ))

step "run the test suite once (vitest)"
start=$(date +%s)
set +e
(cd "$REPO" && npx vitest run) >>"$LOG" 2>&1
tests_exit=$?
set -e
tests_seconds=$(( $(date +%s) - start ))
tests_line=$(grep -E "Tests +[0-9]" "$LOG" | tail -1 | sed 's/\x1b\[[0-9;]*m//g' | tr -s ' ')

dirty=$(git -C "$REPO" status --porcelain | head -5 | tr '\n' ';')
cat >"$SUMMARY" <<EOF
{
  "repo": "$REPO",
  "commit": "$actual",
  "install_seconds": $install_seconds,
  "node_modules": "$(du -sh "$REPO/node_modules" | cut -f1)",
  "tests_exit": $tests_exit,
  "tests_seconds": $tests_seconds,
  "tests": "$tests_line",
  "worktree_changes": "$dirty",
  "node": "$(node --version)"
}
EOF
cat "$SUMMARY"
if [ "$tests_exit" -ne 0 ]; then
  echo "Tests failed; see $LOG" >&2
  exit 1
fi
echo "zustand corpus ready."
