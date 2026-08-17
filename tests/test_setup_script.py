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
    assert 'python -m pip install --upgrade --upgrade-strategy only-if-needed' in source
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


def test_readme_documents_public_install_device_and_update_flows() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    assert "./setup.sh --device cpu" in readme
    assert "./setup.sh --device cuda" in readme
    assert "devstill device auto" in readme
    assert "devstill update --check" in readme
    assert "~/.local/share/devstill/venv/" in readme
