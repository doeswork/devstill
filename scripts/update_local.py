"""Standalone local installer: do not import the installed Devstill to bootstrap it."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def expanded_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser().absolute()


def installation() -> tuple[Path, Path, dict | None]:
    data_home = expanded_path(
        os.environ.get("DEVSTILL_DATA_HOME")
        or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "devstill"
    )
    metadata_path = expanded_path(
        os.environ.get("DEVSTILL_INSTALL_METADATA", data_home / "install.json")
    )
    metadata = None
    if metadata_path.exists():
        if metadata_path.is_symlink():
            raise ValueError(
                f"Installation metadata must not be a symlink: {metadata_path}"
            )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            not isinstance(metadata, dict)
            or metadata.get("install_mode") != "self-contained"
            or metadata.get("schema_version", 1) != 1
            or not isinstance(metadata.get("venv"), str)
        ):
            raise ValueError(
                f"Invalid self-contained installation metadata: {metadata_path}"
            )
    target = expanded_path(
        os.environ.get("DEVSTILL_VENV")
        or (metadata["venv"] if metadata else data_home / "venv")
    ).resolve()
    if not (target / "pyvenv.cfg").is_file() or not (target / "bin/python").is_file():
        raise ValueError(
            f"Private Python environment is unavailable: {target}. Run ./setup.sh first."
        )
    return target, metadata_path, metadata


def copy_source(checkout: Path, destination: Path) -> None:
    destination.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(checkout / name, destination / name)
    for name in (
        "LICENSE",
        "LICENSE.txt",
        "LICENSE.md",
        "NOTICE",
        "MANIFEST.in",
        "setup.cfg",
        "setup.py",
    ):
        if (checkout / name).is_file():
            shutil.copy2(checkout / name, destination / name)
    shutil.copytree(
        checkout / "src",
        destination / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
    )


def verify_wheel(wheel: Path, source: Path, candidate: Path) -> str:
    from email.parser import BytesParser

    package = source / "src/devstill"
    expected = {
        "devstill/" + path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file()
    }
    with zipfile.ZipFile(wheel) as archive:
        actual = {
            name
            for name in archive.namelist()
            if name.startswith("devstill/") and not name.endswith("/")
        }
        if actual != set(expected):
            raise ValueError(
                "Wheel package files do not match the clean source snapshot"
            )
        for name, content in expected.items():
            if archive.read(name) != content:
                raise ValueError(f"Wheel contains stale source: {name}")
        if "devstill/pi/devstill_policy.ts" not in actual:
            raise ValueError("Wheel is missing the Pi policy asset")
        metadata_files = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise ValueError("Expected exactly one wheel metadata record")
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        if metadata["Name"] != "devstill" or not metadata["Version"]:
            raise ValueError("Wheel is not a versioned Devstill package")
        archive.extractall(candidate)
        return metadata["Version"]


def verify_dependencies(candidate: Path) -> None:
    """Run under the target interpreter; include requested dependency extras."""
    from importlib.metadata import Distribution, PackageNotFoundError, distribution

    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    package = Distribution.at(next(candidate.glob("*.dist-info")))
    python_constraint = package.metadata.get("Requires-Python", "")
    if Version(".".join(map(str, sys.version_info[:3]))) not in SpecifierSet(
        python_constraint
    ):
        raise ValueError(f"This checkout requires Python {python_constraint}")
    pending = [(requirement, "") for requirement in package.requires or []]
    checked = set()
    while pending:
        text, extra = pending.pop()
        if (text, extra) in checked:
            continue
        checked.add((text, extra))
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate({"extra": extra}):
            continue
        try:
            installed = distribution(requirement.name)
        except PackageNotFoundError as exc:
            raise ValueError(
                f"Missing dependency: {requirement}. Run ./setup.sh to refresh dependencies."
            ) from exc
        if requirement.url or Version(installed.version) not in requirement.specifier:
            raise ValueError(
                f"Dependency mismatch: {requirement}; installed {installed.version}. "
                "Run ./setup.sh to refresh dependencies."
            )
        for requested_extra in requirement.extras:
            pending.extend(
                (child, requested_extra) for child in installed.requires or []
            )


def verify_imports(expected: Path) -> None:
    import devstill.cli
    import devstill.tui

    for module in (devstill.cli, devstill.tui):
        if not Path(module.__file__).resolve().is_relative_to(expected.resolve()):
            raise ValueError(
                f"Startup check loaded the wrong package: {module.__file__}"
            )


def run(command: list[str], cwd: Path) -> None:
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        env.pop(name, None)
    env["PIP_CONFIG_FILE"] = os.devnull
    result = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"Command exited with {result.returncode}: {' '.join(command[:5])}\n"
            f"{result.stdout}\n{result.stderr}".strip()
        )


def write_version(path: Path, metadata: dict, version: str) -> None:
    updated = {**metadata, "version": version}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(updated, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update(checkout: Path) -> None:
    stage = "locating the private installation"
    installation_started = False
    try:
        target, metadata_path, metadata = installation()
        python = str(target / "bin/python")
        helper = str(Path(__file__).resolve())
        print(f"Source checkout: {checkout}\nPrivate environment: {target}", flush=True)
        print("Close running Devstill sessions before updating.", flush=True)
        with tempfile.TemporaryDirectory(prefix="devstill-local-update-") as folder:
            temp = Path(folder)
            stage = "checking the target environment"
            run(
                [
                    python,
                    "-I",
                    "-c",
                    (
                        "import sys; from pathlib import Path; "
                        "assert sys.prefix != sys.base_prefix; "
                        "assert Path(sys.prefix).resolve() == Path(sys.argv[1]).resolve()"
                    ),
                    str(target),
                ],
                temp,
            )
            stage = "building a clean source wheel"
            print("Building a clean source wheel…", flush=True)
            source = temp / "source"
            copy_source(checkout, source)
            wheels = temp / "wheels"
            run(
                [
                    python,
                    "-I",
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-cache-dir",
                    "--wheel-dir",
                    str(wheels),
                    str(source),
                ],
                temp,
            )
            candidates = list(wheels.glob("*.whl"))
            if len(candidates) != 1:
                raise ValueError("Expected exactly one built wheel")
            wheel = candidates[0]
            stage = "verifying the candidate package"
            print("Checking source, dependencies, and candidate startup…", flush=True)
            candidate = temp / "candidate"
            version = verify_wheel(wheel, source, candidate)
            run(
                [
                    python,
                    "-I",
                    "-c",
                    (
                        "import runpy, sys; from pathlib import Path; "
                        "checks = runpy.run_path(sys.argv[1]); candidate = Path(sys.argv[2]); "
                        "checks['verify_dependencies'](candidate); "
                        "sys.path.insert(0, str(candidate)); checks['verify_imports'](candidate)"
                    ),
                    helper,
                    str(candidate),
                ],
                temp,
            )
            for option in ("--help", "--version"):
                run(
                    [
                        python,
                        "-I",
                        "-c",
                        (
                            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                            "from devstill.cli import main; main()"
                        ),
                        str(candidate),
                        option,
                    ],
                    temp,
                )
            run([python, "-I", "-m", "pip", "check"], temp)
            stage = "installing the verified wheel"
            print(
                f"Installing Devstill {version} without changing dependencies…",
                flush=True,
            )
            installation_started = True
            run(
                [
                    python,
                    "-I",
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--no-index",
                    "--no-cache-dir",
                    "--force-reinstall",
                    str(wheel),
                ],
                temp,
            )
            stage = "verifying the installed command"
            print("Checking installed startup and package integrity…", flush=True)
            run(
                [
                    python,
                    "-I",
                    "-c",
                    (
                        "import runpy, sysconfig; from pathlib import Path; import sys; "
                        "runpy.run_path(sys.argv[1])['verify_imports'](Path(sysconfig.get_path('purelib')))"
                    ),
                    helper,
                ],
                temp,
            )
            for option in ("--help", "--version"):
                run([str(target / "bin/devstill"), option], temp)
            run([python, "-I", "-m", "pip", "check"], temp)
            stage = "updating installation metadata"
            if (
                metadata is not None
                and expanded_path(metadata["venv"]).resolve() == target
            ):
                write_version(metadata_path, metadata, version)
        print(
            f"Devstill {version} updated successfully. Reopen Devstill in your project.",
            flush=True,
        )
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        state = (
            "Installation was attempted; verification did not complete. Fix the error and rerun ./update-local.sh."
            if installation_started
            else "The installed package was not changed."
        )
        raise RuntimeError(
            f"Local update failed while {stage}: {exc}\n{state}"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="update-local.sh",
        description="Install this checkout's current code into the existing private Devstill environment.",
        epilog="Includes uncommitted edits. Keeps dependencies unchanged. Close Devstill first; reopen after success.",
    )
    parser.parse_args()
    try:
        update(Path(__file__).resolve().parents[1])
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
