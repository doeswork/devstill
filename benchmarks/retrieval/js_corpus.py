"""JavaScript/TypeScript corpora: shared locked dependencies, vitest acceptance.

Each trial checkout links `node_modules` to the prepared corpus checkout (pnpm,
frozen lockfile, install scripts disabled), so agents and the evaluator use
the same dependencies without a per-trial install. One Node binary is pinned
for agents' shells and the evaluator alike.

Acceptance for a candidate, in a throwaway copy:
  1. `tsc --noEmit` (the project's own type check; part of its `pnpm test`),
  2. the upstream vitest suite: no failure outside the set that already fails
     at the pinned commit in this environment,
  3. the hidden check: a vitest module written into tests/ only now.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from functools import cache
from pathlib import Path

PNPM = "pnpm@9.15.5"
DEFAULT_CHECK_FILE = "tests/zz_devstill_acceptance.test.tsx"
SUITE_TIMEOUT = 180


def bench_path():
    """PATH the benchmark runner gives child processes (user bins, mise shims)."""
    home = Path.home()
    return os.pathsep.join(
        [
            str(home / ".local/bin"),
            str(home / ".local/share/mise/shims"),
            os.environ.get("PATH", ""),
        ]
    )


@cache
def node_bin():
    """Directory of the real node binary (not a version-manager shim)."""
    executable = subprocess.run(
        ["node", "-p", "process.execPath"],
        env=dict(os.environ, PATH=bench_path()),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    return str(Path(executable).resolve().parent)


def node_version():
    return subprocess.run(
        [str(Path(node_bin()) / "node"), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def tool_env(base=None):
    env = dict(base if base is not None else os.environ)
    env["PATH"] = node_bin() + os.pathsep + env.get("PATH", "")
    env["NO_COLOR"] = "1"
    env.pop("GITHUB_ACTIONS", None)
    return env


def dependencies_fingerprint(source):
    """Identity of an installed node_modules tree (pnpm layout).

    The install record plus the installed package names. Dot entries are
    tool caches (`.vite`, `.vite-temp`, `.cache/prettier`, ...) that ordinary
    test and format runs create; they do not change what is installed.
    """
    modules = Path(source) / "node_modules"
    h = hashlib.sha256()
    h.update((modules / ".modules.yaml").read_bytes())
    for directory in (modules, modules / ".pnpm"):
        names = sorted(n for n in os.listdir(directory) if not n.startswith("."))
        h.update(("\n".join(names) + "\n").encode())
    return h.hexdigest()


def fingerprint_path(source):
    return Path(source).parent / f"{Path(source).name}.dependencies.sha256"


def dependencies_ready(source):
    record = fingerprint_path(source)
    try:
        return record.read_text().strip() == dependencies_fingerprint(source)
    except OSError:
        return False


def install(source, log):
    """Install the locked dependencies (no install scripts) and record them."""
    with Path(log).open("a") as stream:
        subprocess.run(
            [
                "npx",
                "-y",
                PNPM,
                "install",
                "--frozen-lockfile",
                "--ignore-scripts",
            ],
            cwd=source,
            env=tool_env(dict(os.environ, PATH=bench_path())),
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=1800,
        )
    fingerprint_path(source).write_text(dependencies_fingerprint(source) + "\n")


def ensure_clean(source):
    """The prepared checkout is shared by every trial; it must stay pristine."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if status.strip():
        raise ValueError(f"Corpus checkout {source} has local changes:\n{status[:500]}")


def link_dependencies(source, repo):
    """Point repo/node_modules at the prepared tree; keep it out of git."""
    modules = Path(source) / "node_modules"
    if not dependencies_ready(source):
        raise ValueError("Corpus dependencies missing or changed; prepare again")
    target = Path(repo) / "node_modules"
    if target.is_symlink() or target.exists():
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
    target.symlink_to(modules, target_is_directory=True)
    exclude = Path(repo) / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        with exclude.open("a") as f:
            # The checkout's .gitignore says `node_modules/`, which a symlink
            # does not match.
            f.write("\n/node_modules\n")


def run(command, cwd, timeout=SUITE_TIMEOUT):
    try:
        r = subprocess.run(
            command,
            cwd=cwd,
            env=tool_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "returncode": r.returncode,
            "output": r.stdout + r.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"command": command, "returncode": 124, "output": "validation timeout"}


def vitest_failures(work, report_dir, files=()):
    """Run vitest with a JSON report; (log, failing test names or None)."""
    report = Path(report_dir) / "vitest-report.json"
    report.unlink(missing_ok=True)
    log = run(
        [
            "node_modules/.bin/vitest",
            "run",
            "--reporter=json",
            f"--outputFile={report}",
            *files,
        ],
        work,
    )
    try:
        data = json.loads(report.read_text())
    except (OSError, ValueError):
        return log, None
    failures, messages = set(), []
    for module in data.get("testResults", []):
        name = os.path.relpath(module.get("name", ""), work)
        if module.get("status") == "failed" and not module.get("assertionResults"):
            failures.add(f"{name} > (module failed to load)")
            messages.append(f"{name}: {module.get('message', '')}"[:2000])
        for test in module.get("assertionResults", []):
            if test.get("status") == "failed":
                failures.add(f"{name} > {test.get('fullName')}")
                text = "\n".join(
                    line
                    for message in test.get("failureMessages") or []
                    for line in message.splitlines()
                    if not line.strip().startswith("at ")
                )
                messages.append(f"{test.get('fullName')}: {text}"[:2000])
    log["tests"] = data.get("numTotalTests", 0)
    log["failures"] = sorted(failures)
    log["messages"] = messages[:20]
    if not data.get("numTotalTests"):
        failures.add("(no tests ran)")
    return log, failures


def copy_checkout(repo, destination):
    shutil.copytree(
        repo,
        destination,
        ignore=shutil.ignore_patterns(".git", ".devstill", "node_modules"),
    )


def upstream_failures(source, commit):
    """Upstream tests that already fail at the pinned commit here (cached)."""
    record = Path(source).parent / f"{Path(source).name}.upstream-failures.json"
    key = {
        "commit": commit,
        "node": node_version(),
        "dependencies": dependencies_fingerprint(source),
    }
    try:
        cached = json.loads(record.read_text())
        if cached["key"] == key:
            return set(cached["failures"])
    except (OSError, ValueError, KeyError):
        pass
    ensure_clean(source)
    with tempfile.TemporaryDirectory(prefix="devstill-js-baseline-") as temp:
        work = Path(temp) / "repo"
        subprocess.run(
            ["git", "clone", "--shared", "--quiet", str(source), str(work)],
            check=True,
        )
        link_dependencies(source, work)
        log, failures = vitest_failures(work, temp)
    if failures is None or "(no tests ran)" in failures:
        raise RuntimeError("Upstream suite did not run: " + log["output"][-2000:])
    record.write_text(
        json.dumps({"key": key, "failures": sorted(failures)}, indent=2) + "\n"
    )
    return failures


def accept(case, work, source, commit, *, protect_regressions):
    """Score a copied candidate checkout `work` (no node_modules); returns logs."""
    ensure_clean(source)
    link_dependencies(source, work)
    if protect_regressions:
        # Agents may edit tests while working; restore the evaluator's copy so
        # deleting or weakening upstream tests cannot pass a candidate.
        kept = {
            name: (work / name).read_bytes()
            for name in case.get("keep_test_paths", [])
            if (work / name).is_file()
        }
        shutil.rmtree(work / "tests", ignore_errors=True)
        shutil.copytree(Path(source) / "tests", work / "tests")
        for name, data in kept.items():
            (work / name).parent.mkdir(parents=True, exist_ok=True)
            (work / name).write_bytes(data)
    logs = [run(["node_modules/.bin/tsc", "--noEmit"], work)]
    known = upstream_failures(source, commit)
    log, failures = vitest_failures(work, work.parent)
    new = sorted((failures or {"(no report)"}) - known)
    log["returncode"] = 1 if new else 0
    log["new_failures"] = new
    log["known_upstream_failures"] = sorted(known)
    logs.append(log)
    check_file = case.get("check_file", DEFAULT_CHECK_FILE)
    path = work / check_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(case["checks"] + "\n")
    log, failures = vitest_failures(work, work.parent, [check_file])
    log["returncode"] = 0 if failures == set() else 1
    logs.append(log)
    return logs
