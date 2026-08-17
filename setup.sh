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

usage() {
    cat <<'EOF'
Usage: ./setup.sh [OPTIONS]

Install Devstill for the current user on Ubuntu or WSL.

Options:
  --device auto|cpu|cuda     Accelerator selection (default: auto)
  --torch-index-url URL      Override the official CUDA PyTorch wheel index
  --skip-pi                  Install search/planning support without Pi execution
  -h, --help                 Show this help
EOF
}

DEVICE_REQUESTED="auto"
TORCH_INDEX_OVERRIDE=""
SKIP_PI="${DEVSTILL_SKIP_PI:-0}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --device)
            [[ $# -ge 2 ]] || { echo "--device requires a value" >&2; exit 2; }
            DEVICE_REQUESTED="$2"
            shift 2
            ;;
        --device=*)
            DEVICE_REQUESTED="${1#*=}"
            shift
            ;;
        --torch-index-url)
            [[ $# -ge 2 ]] || {
                echo "--torch-index-url requires a value" >&2
                exit 2
            }
            TORCH_INDEX_OVERRIDE="$2"
            shift 2
            ;;
        --torch-index-url=*)
            TORCH_INDEX_OVERRIDE="${1#*=}"
            shift
            ;;
        --skip-pi)
            SKIP_PI=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$DEVICE_REQUESTED" in
    auto|cpu|cuda) ;;
    *) echo "--device must be auto, cpu, or cuda" >&2; exit 2 ;;
esac
if [[ "$DEVICE_REQUESTED" == "cpu" && -n "$TORCH_INDEX_OVERRIDE" ]]; then
    echo "--torch-index-url applies only to auto or cuda installations" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVSTILL_SOURCE="${DEVSTILL_SOURCE:-$SCRIPT_DIR}"
DEVSTILL_SOURCE="$(cd "$DEVSTILL_SOURCE" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DEVSTILL_DATA_HOME="${DEVSTILL_DATA_HOME:-$DATA_HOME/devstill}"
VENV_DIR="${DEVSTILL_VENV:-$DEVSTILL_DATA_HOME/venv}"
MODEL_DIR="${DEVSTILL_MODEL_PATH:-$DEVSTILL_DATA_HOME/models/jina-embeddings-v2-base-code}"
INSTALL_METADATA="$DEVSTILL_DATA_HOME/install.json"
ACTIVATE_SCRIPT="$DEVSTILL_DATA_HOME/activate.sh"
BIN_DIR="$HOME/.local/bin"
COMMAND_LINK="$BIN_DIR/devstill"
PI_NODE_BIN="${XDG_DATA_HOME:-$HOME/.local/share}/pi-node/current/bin"
PI_INSTALL_URL="https://pi.dev/install.sh"
CPU_TORCH_INDEX="https://download.pytorch.org/whl/cpu"
DEFAULT_CUDA_TORCH_INDEX="https://download.pytorch.org/whl/cu128"

if [[ ! -f "$DEVSTILL_SOURCE/pyproject.toml" ]]; then
    echo "Run setup.sh from a Devstill source checkout." >&2
    exit 1
fi

echo "Installing Ubuntu/WSL packages..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip python3-dev \
    build-essential git ripgrep sqlite3 curl ca-certificates libgomp1

python3 - <<'PYCODE'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"devstill requires Python 3.10+; found {sys.version.split()[0]}")
print(f"Using Python {sys.version.split()[0]}")
PYCODE

mkdir -p "$DEVSTILL_DATA_HOME" "$BIN_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

has_nvidia_gpu() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1
}

verify_cuda() {
    python - <<'PYCODE'
import torch
if not torch.cuda.is_available():
    raise SystemExit("PyTorch cannot access CUDA")
probe = torch.empty(1, device="cuda")
print(torch.cuda.get_device_name(0))
del probe
PYCODE
}

install_cpu_torch() {
    echo "Installing CPU PyTorch..."
    python -m pip install --upgrade --force-reinstall torch \
        --index-url "$CPU_TORCH_INDEX" || return 1
    RESOLVED_DEVICE="cpu"
    RESOLVED_TORCH_INDEX="$CPU_TORCH_INDEX"
}

install_cuda_torch() {
    local index_url="${TORCH_INDEX_OVERRIDE:-$DEFAULT_CUDA_TORCH_INDEX}"
    echo "Installing CUDA PyTorch from $index_url..."
    python -m pip install --upgrade --force-reinstall torch \
        --index-url "$index_url" || return 1
    RESOLVED_DEVICE="cuda"
    RESOLVED_TORCH_INDEX="$index_url"
}

RESOLVED_DEVICE=""
RESOLVED_TORCH_INDEX=""
if [[ "$DEVICE_REQUESTED" == "cpu" ]]; then
    install_cpu_torch
elif [[ "$DEVICE_REQUESTED" == "cuda" ]]; then
    install_cuda_torch
    if ! GPU_NAME="$(verify_cuda)"; then
        echo "CUDA was requested, but PyTorch could not allocate on the GPU." >&2
        echo "Check the NVIDIA driver or use ./setup.sh --device cpu." >&2
        exit 1
    fi
    echo "CUDA verified on $GPU_NAME"
elif has_nvidia_gpu; then
    echo "NVIDIA GPU detected; trying CUDA acceleration."
    if install_cuda_torch && GPU_NAME="$(verify_cuda)"; then
        echo "CUDA verified on $GPU_NAME"
    else
        echo "CUDA setup was unavailable; falling back to CPU." >&2
        install_cpu_torch
    fi
else
    echo "No usable NVIDIA GPU detected; selecting CPU."
    install_cpu_torch
fi

echo "Installing Devstill into $VENV_DIR..."
python -m pip install --upgrade --upgrade-strategy only-if-needed "$DEVSTILL_SOURCE"
python -m pip check

export PATH="$BIN_DIR:$PI_NODE_BIN:$PATH"
if [[ "$SKIP_PI" == "1" ]]; then
    echo "Skipping Pi installation."
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

mkdir -p "$MODEL_DIR"
MODEL_DIR="$MODEL_DIR" python - <<'PYCODE'
import os
from pathlib import Path
from huggingface_hub import snapshot_download

model_dir = Path(os.environ["MODEL_DIR"]).expanduser().resolve()
model_dir.mkdir(parents=True, exist_ok=True)
snapshot_download(
    repo_id="jinaai/jina-embeddings-v2-base-code",
    local_dir=str(model_dir),
    allow_patterns=["*.json", "*.txt", "*.py", "*.safetensors", "pytorch_model.bin"],
)
snapshot_download(
    repo_id="jinaai/jina-bert-v2-qk-post-norm",
    allow_patterns=["*.py"],
)
print(f"Downloaded model to {model_dir}")
PYCODE

ln -sfn "$VENV_DIR/bin/devstill" "$COMMAND_LINK"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if ! grep -qxF "$PATH_LINE" "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# devstill command\n%s\n' "$PATH_LINE" >> "$HOME/.bashrc"
fi
PI_PATH_LINE='export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/pi-node/current/bin:$HOME/.local/bin:$PATH"'
if [[ "$SKIP_PI" != "1" ]] && ! grep -qxF "$PI_PATH_LINE" "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# devstill Pi executor\n%s\n' "$PI_PATH_LINE" >> "$HOME/.bashrc"
fi

cat > "$ACTIVATE_SCRIPT" <<EOF
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
export DEVSTILL_DATA_HOME="$DEVSTILL_DATA_HOME"
export DEVSTILL_MODEL_PATH="$MODEL_DIR"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PATH="$PI_NODE_BIN:$BIN_DIR:\$PATH"
EOF
chmod +x "$ACTIVATE_SCRIPT"

INSTALL_METADATA="$INSTALL_METADATA" \
DEVICE_REQUESTED="$DEVICE_REQUESTED" \
RESOLVED_DEVICE="$RESOLVED_DEVICE" \
RESOLVED_TORCH_INDEX="$RESOLVED_TORCH_INDEX" \
VENV_DIR="$VENV_DIR" python - <<'PYCODE'
import importlib.metadata
import json
import os
from pathlib import Path

target = Path(os.environ["INSTALL_METADATA"])
temporary = target.with_suffix(".json.tmp")
payload = {
    "schema_version": 1,
    "install_mode": "self-contained",
    "requested_device": os.environ["DEVICE_REQUESTED"],
    "resolved_device": os.environ["RESOLVED_DEVICE"],
    "torch_index_url": os.environ["RESOLVED_TORCH_INDEX"],
    "version": importlib.metadata.version("devstill"),
    "venv": str(Path(os.environ["VENV_DIR"]).resolve()),
}
temporary.write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
temporary.chmod(0o600)
temporary.replace(target)
PYCODE

export DEVSTILL_DATA_HOME
export DEVSTILL_MODEL_PATH="$MODEL_DIR"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
"$VENV_DIR/bin/devstill" doctor --load-model

echo
echo "Installed Devstill at $COMMAND_LINK"
echo "Runtime environment: $VENV_DIR"
echo "Embedding device: $RESOLVED_DEVICE (requested: $DEVICE_REQUESTED)"
echo "The source checkout can now be moved or removed."
echo "Run: source ~/.bashrc"
echo "Then in a repository: devstill init && devstill index && devstill"
