from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture
def helper(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "update_local", ROOT / "scripts/update_local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key in (
        "DEVSTILL_VENV",
        "DEVSTILL_DATA_HOME",
        "DEVSTILL_INSTALL_METADATA",
        "XDG_DATA_HOME",
    ):
        monkeypatch.delenv(key, raising=False)
    return module


def fake_environment(path: Path) -> Path:
    (path / "bin").mkdir(parents=True)
    (path / "bin/python").touch()
    (path / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return path


@pytest.fixture
def installed(tmp_path, monkeypatch):
    data = tmp_path / "data with spaces"
    target = fake_environment(data / "private venv")
    metadata = {
        "schema_version": 1,
        "install_mode": "self-contained",
        "version": "0.1.0",
        "venv": str(target),
        "requested_device": "auto",
        "resolved_device": "cuda",
        "torch_index_url": "https://download.pytorch.org/whl/cu128",
    }
    path = data / "install.json"
    path.write_text(json.dumps(metadata))
    monkeypatch.setenv("DEVSTILL_DATA_HOME", str(data))
    return target, path, metadata


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "src/devstill/pi").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=77", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "devstill"\nversion = "0.1.0"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        '[tool.setuptools.package-data]\ndevstill = ["pi/*.ts"]\n'
    )
    (root / "README.md").write_text("Local fixture\n")
    (root / "src/devstill/__init__.py").write_text("")
    (root / "src/devstill/config.py").write_text("def update_executor_config(): pass\n")
    (root / "src/devstill/tui.py").write_text(
        "from .config import update_executor_config\n"
    )
    (root / "src/devstill/cli.py").write_text(
        "from .tui import update_executor_config\n"
    )
    (root / "src/devstill/pi/devstill_policy.ts").write_text("// current policy\n")
    (root / "build/lib/devstill").mkdir(parents=True)
    stale = root / "build/lib/devstill/config.py"
    stale.write_text("# stale module missing required function\n")
    os.utime(stale, (2_000_000_000, 2_000_000_000))
    return root


def wheel_fixture(
    source: Path, destination: Path, *, stale=False, omit_policy=False
) -> Path:
    destination.mkdir()
    wheel = destination / "devstill-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for file in (source / "src").rglob("*"):
            if file.is_file():
                if omit_policy and file.suffix == ".ts":
                    continue
                content = (
                    b"# stale"
                    if stale and file.name == "config.py"
                    else file.read_bytes()
                )
                archive.writestr(file.relative_to(source / "src").as_posix(), content)
        archive.writestr(
            "devstill-0.1.0.dist-info/METADATA", "Name: devstill\nVersion: 0.1.0\n"
        )
    return wheel


def test_target_precedence_and_missing_environment(
    helper, installed, tmp_path, monkeypatch
):
    target, metadata_path, _ = installed
    assert helper.installation()[:2] == (target, metadata_path)
    override = fake_environment(tmp_path / "override")
    monkeypatch.setenv("DEVSTILL_VENV", str(override))
    assert helper.installation()[0] == override
    monkeypatch.delenv("DEVSTILL_VENV")
    metadata_path.unlink()
    fallback = fake_environment(metadata_path.parent / "venv")
    assert helper.installation()[0] == fallback
    monkeypatch.setenv("DEVSTILL_VENV", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="Run ./setup.sh first"):
        helper.installation()


def test_clean_build_ignores_newer_stale_artifacts(helper, source, tmp_path):
    clean = tmp_path / "clean source"
    helper.copy_source(source, clean)
    assert not (clean / "build").exists()
    wheels = tmp_path / "wheels"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--wheel-dir",
            str(wheels),
            str(clean),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(wheels.glob("*.whl"))
    candidate = tmp_path / "candidate"
    assert helper.verify_wheel(wheel, clean, candidate) == "0.1.0"
    helper.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from devstill.cli import update_executor_config; update_executor_config()"
            ),
            str(candidate),
        ],
        tmp_path,
    )
    assert (
        candidate / "devstill/pi/devstill_policy.ts"
    ).read_text() == "// current policy\n"


@pytest.mark.parametrize("stale,omit_policy", [(True, False), (False, True)])
def test_rejects_stale_or_incomplete_wheels(
    helper, source, tmp_path, stale, omit_policy
):
    wheel = wheel_fixture(
        source, tmp_path / "wheels", stale=stale, omit_policy=omit_policy
    )
    with pytest.raises(ValueError, match="stale source|do not match"):
        helper.verify_wheel(wheel, source, tmp_path / "candidate")


def test_dependency_check_handles_missing_incompatible_and_extra_requirements(
    helper, tmp_path
):
    metadata = tmp_path / "devstill-0.1.0.dist-info"
    metadata.mkdir()
    file = metadata / "METADATA"
    for requirement in (
        "nonexistent-devstill-test-package>=1",
        "packaging>99999",
        "packaging>99999; python_version >= '3.10'",
    ):
        file.write_text(
            f"Name: devstill\nVersion: 0.1.0\nRequires-Dist: {requirement}\n"
        )
        with pytest.raises(ValueError, match="Run ./setup.sh"):
            helper.verify_dependencies(tmp_path)
    file.write_text(
        "Name: devstill\nVersion: 0.1.0\nRequires-Dist: packaging>=1\n"
        "Requires-Dist: nonexistent-devstill-test-package; extra == 'dev'\n"
    )
    helper.verify_dependencies(tmp_path)


@pytest.mark.parametrize("failure", [None, "candidate", "install", "installed"])
def test_update_order_failure_reporting_and_same_version_reinstall(
    helper,
    installed,
    source,
    monkeypatch,
    failure,
    capsys,
):
    target, metadata_path, original = installed
    original_text = metadata_path.read_text()
    calls = []
    temporary = []

    def runner(command, cwd):
        calls.append(command)
        temporary.append(cwd)
        if "wheel" in command:
            wheel_fixture(
                Path(command[-1]), Path(command[command.index("--wheel-dir") + 1])
            )
        if failure == "candidate" and any(
            "verify_dependencies" in arg for arg in command
        ):
            raise RuntimeError("candidate failure")
        if failure == "install" and "install" in command:
            raise RuntimeError("install failure")
        if failure == "installed" and command[0] == str(target / "bin/devstill"):
            raise RuntimeError("installed failure")

    monkeypatch.setattr(helper, "run", runner)
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failure") as error:
            helper.update(source)
        assert metadata_path.read_text() == original_text
        if failure == "candidate":
            assert not any("install" in command for command in calls)
            assert "installed package was not changed" in str(error.value)
        else:
            assert "Installation was attempted" in str(error.value)
        assert "updated successfully" not in capsys.readouterr().out
    else:
        helper.update(source)
        install = next(command for command in calls if "install" in command)
        assert (
            "--no-deps" in install
            and "--force-reinstall" in install
            and "--no-index" in install
        )
        assert install[-1].endswith("devstill-0.1.0-py3-none-any.whl")
        assert json.loads(metadata_path.read_text()) == original
        assert "updated successfully" in capsys.readouterr().out
    assert all(not path.exists() for path in temporary)


def test_metadata_preserves_device_settings(helper, installed):
    _, path, metadata = installed
    helper.write_version(path, metadata, "0.2.0")
    assert json.loads(path.read_text()) == {**metadata, "version": "0.2.0"}


def test_wrapper_from_other_directory_and_nonzero_failure(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "update-local.sh", checkout)
    shutil.copy2(ROOT / "scripts/update_local.py", checkout / "scripts")
    script = checkout / "update-local.sh"
    env = os.environ.copy()
    env["DEVSTILL_DATA_HOME"] = str(tmp_path / "absent")
    env["DEVSTILL_VENV"] = str(tmp_path / "missing environment")
    env["DEVSTILL_INSTALL_METADATA"] = str(tmp_path / "absent/install.json")
    for args in (["bash", "-n", str(script)], [str(script), "--help"]):
        result = subprocess.run(
            args, cwd=tmp_path, env=env, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Private Python environment is unavailable" in result.stderr
    assert "installed package was not changed" in result.stderr
