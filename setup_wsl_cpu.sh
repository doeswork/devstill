#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "setup_wsl_cpu.sh is retained for compatibility; using setup.sh --device cpu."
if [[ $# -gt 0 && "$1" != -* ]]; then
    export DEVSTILL_SOURCE="$1"
    shift
fi
exec "$SCRIPT_DIR/setup.sh" --device cpu "$@"
