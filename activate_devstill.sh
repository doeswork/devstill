#!/usr/bin/env bash
# Development-checkout helper. Public installations do not require activation.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
export DEVSTILL_DATA_HOME="${DEVSTILL_DATA_HOME:-$HOME/.local/share/devstill}"
export DEVSTILL_MODEL_PATH="${DEVSTILL_MODEL_PATH:-$DEVSTILL_DATA_HOME/models/jina-embeddings-v2-base-code}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/pi-node/current/bin:$HOME/.local/bin:$PATH"
