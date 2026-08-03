#!/usr/bin/env bash
set -Eeuo pipefail
PI_INSTALL_SCRIPT=""
cleanup_setup() {
    if [[ -n "$PI_INSTALL_SCRIPT" && -f "$PI_INSTALL_SCRIPT" ]]; then
        rm -f "$PI_INSTALL_SCRIPT"
    fi
}
trap 'cleanup_setup; echo; echo "devstill setup failed on line $LINENO." >&2' ERR
trap cleanup_setup EXIT

DEVSTILL_SOURCE="${1:-$(pwd)}"
DEVSTILL_SOURCE="$(cd "$DEVSTILL_SOURCE" && pwd)"
VENV_DIR="${DEVSTILL_VENV:-$DEVSTILL_SOURCE/.venv}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DEVSTILL_DATA_HOME="${DEVSTILL_DATA_HOME:-$DATA_HOME/devstill}"
MODEL_DIR="${DEVSTILL_MODEL_PATH:-$DEVSTILL_DATA_HOME/models/jina-embeddings-v2-base-code}"
BIN_DIR="$HOME/.local/bin"
COMMAND_LINK="$BIN_DIR/devstill"
PI_NODE_BIN="${XDG_DATA_HOME:-$HOME/.local/share}/pi-node/current/bin"
PI_INSTALL_URL="https://pi.dev/install.sh"

if [[ ! -f "$DEVSTILL_SOURCE/pyproject.toml" ]]; then
    echo "Run this script from the devstill source directory." >&2
    exit 1
fi

echo "Installing Ubuntu/WSL packages..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git ripgrep sqlite3 curl ca-certificates libgomp1

python3 - <<'PYCODE'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"devstill requires Python 3.10+; found {sys.version.split()[0]}")
print(f"Using Python {sys.version.split()[0]}")
PYCODE

if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

# Install the CPU build first so normal dependency resolution does not install CUDA PyTorch.
python -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install --upgrade -e "$DEVSTILL_SOURCE[dev]"
python -m pip check

export PATH="$BIN_DIR:$PI_NODE_BIN:$PATH"
if [[ "${DEVSTILL_SKIP_PI:-0}" == "1" ]]; then
    echo "Skipping Pi installation because DEVSTILL_SKIP_PI=1."
else
    PI_COMPATIBLE=0
    if command -v pi >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
        if node -e 'const [a,b,c]=process.versions.node.split(".").map(Number); process.exit(a>22||(a===22&&(b>19||(b===19&&c>=0)))?0:1)' >/dev/null 2>&1; then
            PI_EXISTING_HELP="$(pi --help 2>/dev/null || true)"
            PI_COMPATIBLE=1
            for REQUIRED_FLAG in --mode --no-session --no-context-files \
                --no-extensions --no-skills --tools --extension --thinking \
                --provider --model; do
                if ! grep -q -- "$REQUIRED_FLAG" <<<"$PI_EXISTING_HELP"; then
                    PI_COMPATIBLE=0
                fi
            done
        fi
    fi
    if [[ "$PI_COMPATIBLE" == "1" ]]; then
        echo "Reusing existing Pi installation: $(command -v pi)"
    else
        echo "Installing the Pi coding executor..."
        PI_INSTALL_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/devstill-pi-install.XXXXXX")"
        curl --fail --show-error --silent --location "$PI_INSTALL_URL" \
            --output "$PI_INSTALL_SCRIPT"
        sh "$PI_INSTALL_SCRIPT"
        rm -f "$PI_INSTALL_SCRIPT"
        PI_INSTALL_SCRIPT=""
        hash -r
    fi
    command -v node >/dev/null 2>&1 || {
        echo "Pi installation did not provide Node.js on PATH." >&2
        exit 1
    }
    command -v pi >/dev/null 2>&1 || {
        echo "Pi installation did not provide the pi command on PATH." >&2
        exit 1
    }
    node --version
    pi --version
    PI_HELP="$(pi --help)"
    for REQUIRED_FLAG in --mode --no-session --no-context-files \
        --no-extensions --no-skills --tools --extension --thinking \
        --provider --model; do
        grep -q -- "$REQUIRED_FLAG" <<<"$PI_HELP" || {
            echo "Installed Pi is missing required option $REQUIRED_FLAG." >&2
            exit 1
        }
    done
fi

mkdir -p "$MODEL_DIR" "$DEVSTILL_DATA_HOME" "$BIN_DIR"
MODEL_DIR="$MODEL_DIR" python - <<'PYCODE'
import os
from pathlib import Path
from huggingface_hub import snapshot_download
model_dir = Path(os.environ['MODEL_DIR']).expanduser().resolve()
model_dir.mkdir(parents=True, exist_ok=True)
snapshot_download(
    repo_id='jinaai/jina-embeddings-v2-base-code',
    local_dir=str(model_dir),
    allow_patterns=['*.json', '*.txt', '*.py', '*.safetensors', 'pytorch_model.bin'],
)
# The embedding config delegates its custom architecture to this second repository.
# Cache the Python implementation before the activation script forces offline mode.
snapshot_download(
    repo_id='jinaai/jina-bert-v2-qk-post-norm',
    allow_patterns=['*.py'],
)
print(f"Downloaded model to {model_dir}")
PYCODE

ln -sfn "$VENV_DIR/bin/devstill" "$COMMAND_LINK"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if ! grep -qxF "$PATH_LINE" "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# devstill command\n%s\n' "$PATH_LINE" >> "$HOME/.bashrc"
fi
PI_PATH_LINE='export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/pi-node/current/bin:$HOME/.local/bin:$PATH"'
if ! grep -qxF "$PI_PATH_LINE" "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# devstill Pi executor\n%s\n' "$PI_PATH_LINE" >> "$HOME/.bashrc"
fi

cat > "$DEVSTILL_SOURCE/activate_devstill.sh" <<EOF
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
export DEVSTILL_DATA_HOME="$DEVSTILL_DATA_HOME"
export DEVSTILL_MODEL_PATH="$MODEL_DIR"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/pi-node/current/bin:$HOME/.local/bin:\$PATH"
EOF
chmod +x "$DEVSTILL_SOURCE/activate_devstill.sh"

export DEVSTILL_DATA_HOME
export DEVSTILL_MODEL_PATH="$MODEL_DIR"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
"$VENV_DIR/bin/devstill" doctor --load-model

echo
echo "Installed devstill at $COMMAND_LINK"
if [[ "${DEVSTILL_SKIP_PI:-0}" != "1" ]]; then
    echo "Installed Pi executor at $(command -v pi)"
fi
echo "Run: source ~/.bashrc"
echo "Then in a repository: devstill init && devstill index && devstill"
