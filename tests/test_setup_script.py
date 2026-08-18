from __future__ import annotations

import subprocess
from pathlib import Path


def test_setup_scripts_have_valid_shell_and_compatibility_wrapper() -> None:
    root = Path(__file__).parents[1]
    script = root / "setup.sh"
    wrapper = root / "setup_wsl_cpu.sh"
    for target in (script, wrapper):
        completed = subprocess.run(
            ["bash", "-n", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    source = script.read_text(encoding="utf-8")
    assert "https://pi.dev/install.sh" in source
    assert "DEVSTILL_SKIP_PI" in source
    assert "pi-node/current/bin" in source
    assert "--no-session" in source
    assert "--device auto|cpu|cuda" in source
    assert "https://download.pytorch.org/whl/cpu" in source
    assert "https://download.pytorch.org/whl/cu128" in source
    assert 'VENV_DIR="${DEVSTILL_VENV:-$DEVSTILL_DATA_HOME/venv}"' in source
    assert "PIP_INSTALL=(python -m pip install --no-cache-dir)" in source
    assert "export PIP_NO_CACHE_DIR=1" in source
    assert "Refusing package changes outside Devstill's private environment" in source
    assert '"${PIP_INSTALL[@]}" --upgrade pip setuptools wheel' in source
    assert '"${PIP_INSTALL[@]}" --upgrade --force-reinstall torch' in source
    assert (
        '"${PIP_INSTALL[@]}" --upgrade --upgrade-strategy only-if-needed'
        in source
    )
    assert "pip cache purge" not in source
    assert "remove_cuda_only_packages" in source
    assert 'normalized.startswith(("nvidia-", "cuda-"))' in source
    assert 'or normalized == "triton"' in source
    assert "Private install destination" in source
    assert "Expected installed footprint" in source
    assert "Runtime environment size" in source
    assert "Embedding model size" in source
    assert source.index('source "$VENV_DIR/bin/activate"') < source.index(
        "remove_cuda_only_packages()"
    )
    assert "install.json" in source
    assert 'cat > "$DEVSTILL_SOURCE/activate_devstill.sh"' not in source
    wrapper_source = wrapper.read_text(encoding="utf-8")
    assert 'exec "$SCRIPT_DIR/setup.sh" --device cpu "$@"' in wrapper_source


def test_setup_help_does_not_run_installation() -> None:
    script = Path(__file__).parents[1] / "setup.sh"
    completed = subprocess.run(
        [str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--torch-index-url" in completed.stdout
    assert "Installing Ubuntu" not in completed.stdout


def test_setup_routes_cpu_cuda_and_automatic_fallback_safely() -> None:
    source = (Path(__file__).parents[1] / "setup.sh").read_text(encoding="utf-8")
    cpu_body = source.split("install_cpu_torch() {", 1)[1].split("\n}", 1)[0]
    assert "remove_cuda_only_packages" in cpu_body
    assert 'RESOLVED_DEVICE="cpu"' in cpu_body
    assert 'elif [[ "$DEVICE_REQUESTED" == "cuda" ]]' in source
    assert 'if ! GPU_NAME="$(verify_cuda)"' in source
    assert "CUDA was requested, but PyTorch could not allocate" in source
    assert 'elif has_nvidia_gpu; then' in source
    assert 'if install_cuda_torch && GPU_NAME="$(verify_cuda)"' in source
    assert "CUDA setup was unavailable; falling back to CPU." in source
    assert source.count("install_cpu_torch") >= 3
    assert "pip cache purge" not in source


def test_readme_documents_public_install_device_and_update_flows() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    assert "./setup.sh --device cpu" in readme
    assert "./setup.sh --device cuda" in readme
    assert "devstill device auto" in readme
    assert "devstill update --check" in readme
    assert "~/.local/share/devstill/venv/" in readme
    assert "7–10 GiB" in readme
    assert "pip cache purge" in readme
    assert "never purges pip's shared cache" in readme
