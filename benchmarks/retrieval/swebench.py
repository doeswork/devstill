"""SWE-bench Verified small-fix slice for the Pi-vs-Devstill comparison.

Cases are real GitHub issues from pure-Python repositories, graded by the
official SWE-bench harness. The agents edit a host checkout copied out of the
instance's Docker image and run python/pytest inside a container of that same
image (PATH shims), so both arms work against the project's real environment.

Needs Docker (on WSL: Docker Desktop -> Settings -> Resources -> WSL
integration) and network access while images are first pulled.

    python -m benchmarks.retrieval.swebench build     # freeze the case list
    python -m benchmarks.retrieval.swebench prepare   # images, checkouts, indexes
    python -m benchmarks.retrieval.swebench status    # what is missing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.retrieval.patches import is_test_path, patch_stats

ROOT = Path(__file__).resolve().parents[2]
CORPUS = "swebench"
SOURCE_DATASET = "SWE-bench/SWE-bench_Verified"
DATASET = ROOT / "benchmarks/datasets/swebench_small.v1.json"
# Official grader; its own virtualenv keeps it out of the project environment.
HARNESS_PACKAGE = "swebench==5.0.2"
HARNESS_VENV = ROOT / ".devstill/bench-cache/swebench-venv"
MODEL_NAME = "devstill-bench"
TESTBED_PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
# Projects with compiled extensions (astropy, scikit-learn, matplotlib) need an
# in-tree build after every edit; they are left out of the small-fix slice.
PURE_PYTHON_REPOS = (
    "django/django",
    "mwaskom/seaborn",
    "pallets/flask",
    "psf/requests",
    "pydata/xarray",
    "pylint-dev/pylint",
    "pytest-dev/pytest",
    "sphinx-doc/sphinx",
    "sympy/sympy",
)
SELECTION = {
    "difficulty": "<15 min fix",
    "max_source_files": 1,
    "max_changed_lines": 12,
    "max_fail_to_pass": 5,
    "max_problem_chars": 4000,
    "count": 24,
}
QUERY_PREFIX = (
    "Fix this issue, reported against the repository in the current directory.\n\n"
)
# The image's working tree becomes one reproducible local commit.
SNAPSHOT_ENV = {
    "GIT_AUTHOR_NAME": "devstill-bench",
    "GIT_AUTHOR_EMAIL": "bench@localhost",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "devstill-bench",
    "GIT_COMMITTER_EMAIL": "bench@localhost",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}
TEST_TIMEOUT = 900
EVALUATION_TIMEOUT = 1800


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def test_list(value):
    return json.loads(value) if isinstance(value, str) else list(value)


def case_id(instance_id):
    return "swe_" + re.sub(r"[^a-z0-9]+", "_", instance_id.lower()).strip("_")


def eligible(row, rules=SELECTION):
    if (
        row["repo"] not in PURE_PYTHON_REPOS
        or row.get("difficulty") != rules["difficulty"]
    ):
        return False
    stats = patch_stats(row["patch"])
    source = [f for f in stats["files"] if not is_test_path(f)]
    return (
        0 < len(source) == len(stats["files"]) <= rules["max_source_files"]
        and stats["added"] + stats["removed"] <= rules["max_changed_lines"]
        and 0 < len(test_list(row["FAIL_TO_PASS"])) <= rules["max_fail_to_pass"]
        and len(row["problem_statement"]) <= rules["max_problem_chars"]
    )


def select(rows, rules=SELECTION):
    """Deterministic: round-robin over repositories, lowest instance ids first."""
    queues = {}
    for row in sorted(rows, key=lambda r: r["instance_id"]):
        if eligible(row, rules):
            queues.setdefault(row["repo"], []).append(row)
    chosen = []
    while len(chosen) < rules["count"] and any(queues.values()):
        for name in sorted(queues):
            if queues[name] and len(chosen) < rules["count"]:
                chosen.append(queues[name].pop(0))
    return sorted(chosen, key=lambda r: r["instance_id"])


def to_case(row):
    # Agents see only the issue text; hints and tests stay with the grader.
    return {
        "id": case_id(row["instance_id"]),
        "corpus": CORPUS,
        "workload": "small",
        "category": "swebench-verified",
        "family": row["repo"],
        "query": QUERY_PREFIX + row["problem_statement"].strip(),
        "swebench": row,
    }


def valid_case(case):
    instance = case.get("swebench")
    if not isinstance(instance, dict):
        return False
    text = ("instance_id", "repo", "base_commit", "image", "patch", "test_patch")
    return (
        all(instance.get(k) for k in text)
        and bool(test_list(instance.get("FAIL_TO_PASS") or []))
        # Some instances legitimately have no PASS_TO_PASS tests.
        and isinstance(test_list(instance.get("PASS_TO_PASS", "[]")), list)
        and case.get("id") == case_id(instance["instance_id"])
        and case.get("workload") == "small"
        and bool(case.get("query"))
    )


def fetch_rows(dataset=SOURCE_DATASET, *, page=100, get=None):
    import httpx

    get = get or httpx.get
    rows, offset, total = [], 0, None
    while total is None or offset < total:
        reply = get(
            "https://datasets-server.huggingface.co/rows",
            params={
                "dataset": dataset,
                "config": "default",
                "split": "test",
                "offset": offset,
                "length": page,
            },
            timeout=60,
        )
        reply.raise_for_status()
        data = reply.json()
        total = data["num_rows_total"]
        for item in data["rows"]:
            if item.get("truncated_cells"):
                raise ValueError(f"Truncated row: {item['row'].get('instance_id')}")
            rows.append(item["row"])
        offset += page
    if len(rows) != total:
        raise ValueError(f"Fetched {len(rows)} of {total} rows")
    return rows


def build(output=DATASET):
    """Freeze the selected cases; the frozen file, not the source, is the pin."""
    import httpx

    revision = httpx.get(
        f"https://huggingface.co/api/datasets/{SOURCE_DATASET}", timeout=60
    ).json()["sha"]
    rows = fetch_rows()
    chosen = select(rows)
    cases = [to_case(row) for row in chosen]
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, cases)
    write(
        output.with_name(output.stem + ".provenance.json"),
        {
            "source_dataset": SOURCE_DATASET,
            "source_revision": revision,
            "fetched_at": datetime.now(UTC).isoformat(),
            "source_rows": len(rows),
            "selection": SELECTION,
            "pure_python_repos": PURE_PYTHON_REPOS,
            "eligible_per_repo": {
                repo: sum(eligible(r) for r in rows if r["repo"] == repo)
                for repo in PURE_PYTHON_REPOS
            },
            "instance_ids": [c["swebench"]["instance_id"] for c in cases],
        },
    )
    return cases


# --- Docker -----------------------------------------------------------------


def docker_status():
    """(usable, reason) without raising."""
    docker = shutil.which("docker")
    if not docker:
        return False, (
            "docker CLI not found (WSL: enable Docker Desktop -> Settings -> "
            "Resources -> WSL integration for this distro)"
        )
    try:
        reply = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "docker info timed out"
    if reply.returncode:
        text = (reply.stderr or reply.stdout).strip()
        if "WSL integration" in text or "could not be found in this WSL" in text:
            return False, (
                "Docker Desktop's WSL integration is off for this distro (Docker "
                "Desktop -> Settings -> Resources -> WSL integration)"
            )
        lines = text.splitlines() or ["unknown"]
        return False, "docker daemon unreachable: " + lines[0][:200]
    return True, "docker server " + reply.stdout.strip()


def docker(*args, timeout=600):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, timeout=timeout
    ).stdout.strip()


def harness_python():
    return HARNESS_VENV / "bin/python"


def harness_version():
    python = harness_python()
    if not python.exists():
        return None
    reply = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as m; print(m.version('swebench'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return reply.stdout.strip() if reply.returncode == 0 else None


def ensure_harness():
    """Install the pinned official grader into its own virtualenv (uv)."""
    wanted = HARNESS_PACKAGE.split("==")[1]
    if harness_version() == wanted:
        return harness_python()
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    HARNESS_VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([uv, "venv", "--python", "3.12", str(HARNESS_VENV)], check=True)
    subprocess.run(
        [uv, "pip", "install", "--python", str(harness_python()), HARNESS_PACKAGE],
        check=True,
    )
    if harness_version() != wanted:
        raise RuntimeError("SWE-bench harness installation failed")
    return harness_python()


# --- Per-instance preparation ----------------------------------------------


def bench():
    from benchmarks.retrieval import coding_campaign

    return coding_campaign.BENCH


def checkout_dir(identifier):
    return bench() / "repos" / CORPUS / identifier


def config_path(identifier):
    return bench() / "configs" / CORPUS / f"{identifier}.json"


def record_path(identifier):
    return bench() / "repos" / CORPUS / f"{identifier}.prepared.json"


def prepared(case):
    path = record_path(case["id"])
    if not path.exists():
        raise ValueError(
            f"SWE-bench case {case['id']} is not prepared; run "
            "`python -m benchmarks.retrieval.swebench prepare`"
        )
    return json.loads(path.read_text())


def corpus_entry(case):
    """(checkout, index config, snapshot commit), like coding_campaign.CORPORA."""
    return (
        f"{CORPUS}/{case['id']}",
        f"{CORPUS}/{case['id']}.json",
        prepared(case)["snapshot_commit"],
    )


def git(repo, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def snapshot(repo, base_commit):
    """Commit the image's working tree (install edits, generated files).

    Patches are then relative to exactly what the grader's container holds.
    """
    if git(repo, "rev-parse", "HEAD") != base_commit:
        raise ValueError(f"{repo} is not at {base_commit}")
    git(repo, "add", "-A")
    git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "--allow-empty",
        "--no-verify",
        "-m",
        "SWE-bench image working tree",
        env={**os.environ, **SNAPSHOT_ENV},
    )
    return git(repo, "rev-parse", "HEAD")


def index_config(identifier, repo):
    from devstill.config import default_config_dict

    config = default_config_dict()
    config["repo"]["root"] = str(repo)
    config["index"]["path"] = str(bench() / "indexes" / CORPUS / identifier / "chroma")
    # Lexical index only: embedding a large repository takes hours on a CPU.
    config["index"]["vectors"] = False
    config["embedding"]["device"] = "cpu"
    config["llm"]["enabled"] = False
    config["executor"]["enabled"] = False
    return config


def prepare_case(case, *, log=print):
    instance = case["swebench"]
    identifier = case["id"]
    record_file = record_path(identifier)
    if record_file.exists():
        record = json.loads(record_file.read_text())
        if (
            record.get("image") == instance["image"]
            and record.get("base_commit") == instance["base_commit"]
            and (checkout_dir(identifier) / ".git").exists()
            and record.get("index_built")
        ):
            return record
    image = instance["image"]
    try:
        docker("image", "inspect", image, timeout=120)
    except subprocess.CalledProcessError:
        log(f"pulling {image}")
        docker("pull", image, timeout=7200)
    repo = checkout_dir(identifier)
    partial = repo.with_name(repo.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    repo.parent.mkdir(parents=True, exist_ok=True)
    container = docker("create", image, "true")
    try:
        docker("cp", f"{container}:/testbed", str(partial), timeout=3600)
    finally:
        docker("rm", "-f", container)
    commit = snapshot(partial, instance["base_commit"])
    shutil.rmtree(repo, ignore_errors=True)
    partial.rename(repo)
    config = config_path(identifier)
    config.parent.mkdir(parents=True, exist_ok=True)
    write(config, index_config(identifier, repo))
    log(f"indexing {identifier} (lexical)")
    from devstill.config import load_config
    from devstill.indexer import RepositoryIndexer

    index_root = bench() / "indexes" / CORPUS / identifier
    shutil.rmtree(index_root, ignore_errors=True)
    indexer = RepositoryIndexer(config, load_config(config))
    try:
        summary = indexer.run()
    finally:
        indexer.close()
    record = {
        "instance_id": instance["instance_id"],
        "image": image,
        "image_id": docker("image", "inspect", "--format", "{{.Id}}", image),
        "base_commit": instance["base_commit"],
        "snapshot_commit": commit,
        "index_built": True,
        "indexed_files": summary.parsed_files,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    write(record_file, record)
    return record


def clone(case, destination):
    """Full copy: ignored build outputs (egg-info, _version.py) must come along."""
    record = prepared(case)
    shutil.copytree(checkout_dir(case["id"]), destination, symlinks=True)
    if git(destination, "rev-parse", "HEAD") != record["snapshot_commit"]:
        raise ValueError("SWE-bench checkout drift")


def apply_reference(case, repo):
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as handle:
        handle.write(case["swebench"]["patch"])
    try:
        git(repo, "apply", "--whitespace=nowarn", handle.name)
    finally:
        os.unlink(handle.name)


# --- Agent sandbox ----------------------------------------------------------


def shim_scripts(docker_path, container, uid, gid):
    """python/pytest run in the instance environment at the caller's path."""
    run = (
        f'exec "{docker_path}" exec -i -w "$PWD" -u {uid}:{gid} '
        '-e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH="${PYTHONPATH:-}" '
        f"{container} {TESTBED_PYTHON}"
    )
    python = f'#!/bin/sh\n{run} "$@"\n'
    # pip too: an agent's install must never touch the host environment (the
    # container has no network, so it simply fails there).
    pip = f'#!/bin/sh\n{run} -m pip "$@"\n'
    return {
        "python": python,
        "python3": python,
        "pytest": f'#!/bin/sh\n{run} -m pytest "$@"\n',
        "pip": pip,
        "pip3": pip,
    }


@contextmanager
def sandbox(case, repo):
    """Run the instance image with this checkout at /testbed for one trial.

    The temp directory is mounted at the same path, so the agents' scratch
    files and Devstill's check copies resolve inside the container too.
    """
    docker_path = shutil.which("docker")
    if not docker_path:
        raise RuntimeError(docker_status()[1])
    temp = Path(tempfile.gettempdir()).resolve()
    repo = Path(repo).resolve()
    name = f"devstill-swe-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            docker_path,
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "-v",
            f"{repo}:/testbed",
            "-v",
            f"{temp}:{temp}",
            "--entrypoint",
            "tail",
            case["swebench"]["image"],
            "-f",
            "/dev/null",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    shims = Path(tempfile.mkdtemp(prefix="devstill-swe-bin-"))
    try:
        for command, script in shim_scripts(
            docker_path, name, os.getuid(), os.getgid()
        ).items():
            (shims / command).write_text(script)
            (shims / command).chmod(0o755)
        venv = str(ROOT / ".venv")
        path = [
            p
            for p in os.environ.get("PATH", "").split(os.pathsep)
            if p and not p.startswith(venv)
        ]
        yield {
            "PATH": os.pathsep.join([str(shims), *path]),
            "DEVSTILL_BENCH_TOOL_PATH": str(shims),
            "DEVSTILL_BENCH_CONTAINER": name,
            "TMPDIR": str(temp),
        }
    finally:
        subprocess.run(
            [docker_path, "rm", "-f", name],
            capture_output=True,
            timeout=120,
            check=False,
        )
        shutil.rmtree(shims, ignore_errors=True)


# --- Official grading --------------------------------------------------------


def evaluate(instance, patch, *, run=subprocess.run):
    """Grade one patch with the official harness; raises if it cannot grade."""
    python = harness_python()
    with tempfile.TemporaryDirectory(prefix="devstill-swe-eval-") as temp:
        temp = Path(temp)
        (temp / "instances.json").write_text(json.dumps([instance]))
        (temp / "predictions.jsonl").write_text(
            json.dumps(
                {
                    "instance_id": instance["instance_id"],
                    "model_name_or_path": MODEL_NAME,
                    "model_patch": patch,
                }
            )
            + "\n"
        )
        run_id = "devstill-" + uuid.uuid4().hex[:12]
        command = [
            str(python),
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            str(temp / "instances.json"),
            "--split",
            "test",
            "--instance_ids",
            instance["instance_id"],
            "--predictions_path",
            str(temp / "predictions.jsonl"),
            "--max_workers",
            "1",
            "--timeout",
            str(TEST_TIMEOUT),
            "--run_id",
            run_id,
            "--report_dir",
            str(temp),
        ]
        try:
            reply = run(
                command,
                cwd=temp,
                capture_output=True,
                text=True,
                timeout=EVALUATION_TIMEOUT,
            )
            output, code = reply.stdout + reply.stderr, reply.returncode
        except subprocess.TimeoutExpired:
            output, code = "evaluation timeout", 124
        logs = (
            temp / "logs/run_evaluation" / run_id / MODEL_NAME / instance["instance_id"]
        )
        report_file = logs / "report.json"
        if not report_file.exists():
            # Grading infrastructure failed: not the agent's outcome.
            raise RuntimeError(
                f"SWE-bench harness produced no report (exit {code}): "
                + output.strip()[-1500:]
            )
        detail = json.loads(report_file.read_text()).get(instance["instance_id"], {})
        test_output = (
            (logs / "test_output.txt").read_text(errors="replace")
            if (logs / "test_output.txt").exists()
            else ""
        )
    resolved = bool(detail.get("resolved"))
    return {
        "command": ["swebench.harness.run_evaluation", instance["instance_id"]],
        "returncode": 0 if resolved else 1,
        "resolved": resolved,
        "harness_exit": code,
        "report": detail,
        "output": test_output[-20000:] or output[-20000:],
    }


def accept(case, repo, artifact):
    started = time.perf_counter()
    base = prepared(case)["snapshot_commit"]
    subprocess.run(["git", "add", "-N", "--", ".", ":!.devstill"], cwd=repo, check=True)
    patch = subprocess.check_output(
        ["git", "diff", "--binary", base, "--", ".", ":!.devstill"],
        cwd=repo,
        text=True,
    )
    result = evaluate(case["swebench"], patch)
    write(artifact, [result])
    return result["resolved"], time.perf_counter() - started


# --- CLI ----------------------------------------------------------------------


def load(path=DATASET):
    cases = json.loads(Path(path).read_text())
    if not cases or not all(valid_case(c) for c in cases):
        raise ValueError(f"Invalid SWE-bench dataset: {path}")
    return cases


def status(cases):
    ok, reason = docker_status()
    return {
        "docker": {"ok": ok, "detail": reason},
        "harness": harness_version(),
        "harness_wanted": HARNESS_PACKAGE,
        "prepared": sorted(c["id"] for c in cases if record_path(c["id"]).exists()),
        "missing": sorted(c["id"] for c in cases if not record_path(c["id"]).exists()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "prepare", "status"])
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    if args.action == "build":
        cases = build(args.dataset)
        print(f"wrote {len(cases)} cases to {args.dataset}")
        return
    cases = [c for c in load(args.dataset) if not args.case or c["id"] in args.case]
    if args.action == "status":
        print(json.dumps(status(cases), indent=2))
        return
    ok, reason = docker_status()
    if not ok:
        raise SystemExit(f"SWE-bench needs Docker: {reason}")
    ensure_harness()
    failures = {}
    for case in cases:
        try:
            prepare_case(case)
            print(case["id"], "ready", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep preparing the rest
            failures[case["id"]] = f"{type(exc).__name__}: {exc}"
            print(case["id"], "FAILED", failures[case["id"]], flush=True)
    if failures:
        raise SystemExit(f"{len(failures)} case(s) failed to prepare: {failures}")


if __name__ == "__main__":
    main()
