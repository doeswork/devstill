#!/usr/bin/env bash
# Refresh the private installation from this checkout, without rebuilding its dependencies.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/scripts/update_local.py" "$@"
