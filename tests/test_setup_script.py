from __future__ import annotations

import subprocess
from pathlib import Path


def test_wsl_setup_script_has_valid_shell_and_bundles_pi() -> None:
    script = Path(__file__).parents[1] / "setup_wsl_cpu.sh"
    completed = subprocess.run(
        ["bash", "-n", str(script)],
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
