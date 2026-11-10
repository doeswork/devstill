"""Unattended Pi-vs-Devstill benchmark campaigns with durable records.

Usually started through ./pi_vs_devstill_bench.sh. One campaign runs the
selected suites from benchmarks/suites.json one after another. Each suite is
a `pi_comparison run` in its own process with a time limit. A suite that fails
is resumed once and then recorded as failed; a suite whose requirements are
missing (Docker, say) is skipped with the reason. Either way the campaign
moves on.

Records:
  .devstill/bench-history/<id>/   full artifacts, campaign.json, status.txt,
                                  campaign.log, REPORT.md, report.json
  .devstill/bench-history/latest  -> the most recent campaign
  benchmarks/history/             git-tracked compact copies, index.jsonl and
                                  HISTORY.md (one row per campaign)

Commands:
  run        [--suite NAME]... [--tag TAG]... [--repeats N] [--sampling greedy]
             [--max-hours H] [--note TEXT] [--dry-run] [--resume DIR]
  preflight  check the environment and print the plan
  report     DIR [--check]   rebuild a campaign report (--check: compare only)
  history    print the campaign history table
  status     print the latest campaign's status line
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "benchmarks/suites.json"
LOCAL = ROOT / ".devstill/bench-history"
TRACKED = ROOT / "benchmarks/history"
CACHE = ROOT / ".devstill/bench-cache"
PYTHON = ROOT / ".venv/bin/python"
# Corpora the orchestrator can rebuild after /tmp is wiped (manifest, profile).
CORPUS_BUILD = {
    "werkzeug": (None, "baseline"),
    "click": ("benchmarks/retrieval/corpus.click.json", "click"),
    "zustand": ("benchmarks/retrieval/corpus.zustand.json", "zustand"),
}
SCHEMA_VERSION = 1
# pi_comparison's --purpose choices.
PURPOSES = ("smoke", "development", "evaluation")


def now():
    return datetime.now(UTC)


def stamp(moment=None):
    return (moment or now()).isoformat(timespec="seconds")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def sha256_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def environment():
    """Environment for child processes, independent of the caller's shell."""
    home = Path.home()
    path = os.pathsep.join(
        [
            str(home / ".local/bin"),
            str(home / ".local/share/mise/shims"),
            os.environ.get("PATH", ""),
        ]
    )
    return {
        **os.environ,
        "PATH": path,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUNBUFFERED": "1",
    }


# --- Registry -----------------------------------------------------------------


def load_registry(path=REGISTRY):
    registry = json.loads(Path(path).read_text())
    names = [s["name"] for s in registry["suites"]]
    if registry.get("schema_version") != 1 or len(names) != len(set(names)):
        raise ValueError(f"Invalid suite registry: {path}")
    for suite in registry["suites"]:
        purpose = {**registry["defaults"], **suite}.get("purpose", "development")
        if purpose not in PURPOSES:
            raise ValueError(
                f"Suite {suite['name']}: purpose must be one of {PURPOSES}"
            )
    return registry


def select_suites(registry, names=(), tags=()):
    """Named suites in registry order; with neither names nor tags, all of them."""
    known = {s["name"] for s in registry["suites"]}
    if unknown := set(names) - known:
        raise ValueError(f"Unknown suite(s): {', '.join(sorted(unknown))}")
    return [
        s
        for s in registry["suites"]
        if (not names and not tags)
        or s["name"] in names
        or set(tags) & set(s.get("tags", []))
    ]


def resolve(registry, suite, overrides=None):
    """Effective settings: registry defaults < suite < command-line overrides."""
    settings = {**registry["defaults"], **suite}
    settings.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return settings


def dataset_cases(settings):
    return json.loads((ROOT / settings["dataset"]).read_text())


def plan(registry, suites, overrides=None):
    rows = []
    for suite in suites:
        settings = resolve(registry, suite, overrides)
        cases = dataset_cases(settings)
        trials = len(cases) * settings["repeats"] * len(settings["variants"].split(","))
        rows.append(
            {
                "name": settings["name"],
                "cases": len(cases),
                "trials": trials,
                "worst_case_hours": round(
                    trials
                    * (settings["timeout"] + settings["acceptance_budget"])
                    / 3600,
                    1,
                ),
                "requires": settings.get("requires", []),
            }
        )
    return rows


# --- Preflight ----------------------------------------------------------------


def check(name, ok, detail, blocking=False):
    return {"name": name, "ok": bool(ok), "detail": str(detail), "blocking": blocking}


def git_state():
    def git(*args, raw=False):
        reply = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
        # Porcelain lines start with a meaningful space (" M" = unstaged).
        return reply.stdout.rstrip("\n") if raw else reply.stdout.strip()

    # The campaign's own output (benchmarks/history) is not code under test.
    porcelain = git(
        "status", "--porcelain", "--", ".", ":(exclude)benchmarks/history", raw=True
    )
    return {
        "sha": git("rev-parse", "HEAD"),
        "short": git("rev-parse", "--short=7", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(porcelain),
        "status": porcelain.splitlines()[:50],
    }


def model_identity(endpoint_wait):
    """Configured endpoint and the models it serves (never the API key)."""
    import httpx

    from benchmarks.retrieval.pi_comparison import (
        model_headers,
        server_model_ids,
        wait_for_endpoint,
    )
    from devstill.config import load_config

    cfg = load_config(ROOT / "devstill.json")
    runtime = cfg.model_dump(mode="json")
    if not cfg.llm.enabled or not cfg.llm.model:
        raise ValueError("devstill.json has no enabled model")
    headers = model_headers(runtime)
    wait_for_endpoint(cfg.llm.base_url, headers, endpoint_wait)
    with httpx.Client(timeout=15, trust_env=False) as client:
        reply = client.get(cfg.llm.base_url.rstrip("/") + "/models", headers=headers)
        reply.raise_for_status()
    served = server_model_ids(reply.json())
    return {
        "base_url": cfg.llm.base_url,
        "configured_model": cfg.llm.model,
        "served": [list(m) for m in served],
        "served_sha256": sha256_json(served),
    }


def tool_versions():
    def version(command):
        try:
            reply = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                env=environment(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"unavailable: {exc}"
        lines = (reply.stdout + reply.stderr).strip().splitlines()
        return lines[0] if lines else f"exit {reply.returncode}"

    return {
        "python": version([str(PYTHON), "--version"]),
        "pytest": version([str(PYTHON), "-m", "pytest", "--version"]),
        "ruff": version([str(ROOT / ".venv/bin/ruff"), "--version"]),
        "node": version(["node", "--version"]),
        "git": version(["git", "--version"]),
    }


def pi_status():
    from devstill.pi_runtime import inspect_pi

    status = inspect_pi()
    return status.ready, {
        "version": status.pi_version,
        "node": status.node_version,
        "reason": None if status.ready else status.reason,
    }


def free_gb(path):
    return shutil.disk_usage(path).free / 1e9


def preflight(suites, registry, overrides, endpoint_wait):
    """Environment checks; blocking ones stop the campaign, others skip suites."""
    checks = [check("venv", PYTHON.exists(), PYTHON, blocking=True)]
    state = git_state()
    checks.append(
        check(
            "git",
            True,
            f"{state['branch']} {state['short']}"
            + (" (uncommitted changes recorded)" if state["dirty"] else ""),
        )
    )
    model = None
    try:
        model = model_identity(endpoint_wait)
        checks.append(
            check(
                "model_endpoint",
                True,
                f"{model['base_url']} serves {[m[0] for m in model['served']]}",
                blocking=True,
            )
        )
    except Exception as exc:  # noqa: BLE001 - reported, campaign blocked
        checks.append(
            check("model_endpoint", False, f"{type(exc).__name__}: {exc}", True)
        )
    try:
        ready, pi = pi_status()
        checks.append(check("pi", ready, pi["version"] or pi["reason"], blocking=True))
    except Exception as exc:  # noqa: BLE001
        pi = {"version": None, "reason": str(exc)}
        checks.append(check("pi", False, exc, blocking=True))
    tools = tool_versions()
    deps = subprocess.run(
        [
            str(PYTHON),
            "-c",
            "import cryptography, pytest_timeout, ephemeral_port_reserve",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    checks.append(
        check(
            "werkzeug_test_deps",
            deps.returncode == 0,
            "cryptography, pytest-timeout and ephemeral-port-reserve importable"
            if deps.returncode == 0
            else "missing Werkzeug test dependencies: "
            + ((deps.stderr.strip().splitlines() or ["?"])[-1]),
        )
    )
    checks.append(
        check(
            "pytest_8",
            tools["pytest"].startswith("pytest 8."),
            f"{tools['pytest']} (click 8.1.8's suite does not collect under pytest 9)",
        )
    )
    checks.append(
        check(
            "disk",
            free_gb("/tmp") > 5 and free_gb(ROOT) > 5,
            f"/tmp {free_gb('/tmp'):.0f} GB free, repo {free_gb(ROOT):.0f} GB free",
        )
    )
    suite_checks = {}
    for suite in suites:
        settings = resolve(registry, suite, overrides)
        problems = []
        if "docker" in settings.get("requires", []):
            from benchmarks.retrieval import swebench

            ok, reason = swebench.docker_status()
            if not ok:
                problems.append(reason)
            elif free_gb(ROOT) < 30:
                problems.append("needs about 30 GB free disk for images")
        if "node" in settings.get("requires", []) and not tools["node"].startswith(
            "v"
        ):
            problems.append(f"needs Node.js on PATH ({tools['node']})")
        if not (ROOT / settings["dataset"]).exists():
            problems.append(f"missing dataset {settings['dataset']}")
        suite_checks[suite["name"]] = problems
    return {
        "checks": checks,
        "suites": suite_checks,
        "blocked": [c for c in checks if c["blocking"] and not c["ok"]],
        "git": state,
        "model": model,
        "pi": pi,
        "tools": tools,
        "host": platform.node(),
        "platform": platform.platform(),
    }


# --- Campaign record -------------------------------------------------------------


class Campaign:
    """campaign.json, campaign.log and status.txt for one unattended run."""

    def __init__(self, directory, record):
        self.directory = directory
        self.record = record
        self.lock = threading.Lock()
        self.supervisor = None  # set by command_run; preparation uses it
        self.deadline = None

    @classmethod
    def create(cls, record):
        directory = LOCAL / record["id"]
        directory.mkdir(parents=True, exist_ok=False)
        campaign = cls(directory, record)
        campaign.save()
        latest = LOCAL / "latest"
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(record["id"])
        return campaign

    @classmethod
    def load(cls, directory):
        directory = Path(directory).resolve()
        return cls(directory, json.loads((directory / "campaign.json").read_text()))

    def save(self):
        with self.lock:
            write_json(self.directory / "campaign.json", self.record)

    def log(self, message):
        line = f"[{stamp()}] {message}"
        with self.lock, (self.directory / "campaign.log").open("a") as handle:
            handle.write(line + "\n")
        print(line, flush=True)

    def status(self, message):
        (self.directory / "status.txt").write_text(f"{stamp()} {message}\n")

    def suite(self, name):
        return next(s for s in self.record["suites"] if s["name"] == name)


def campaign_id(state, moment=None):
    return (
        (moment or now()).strftime("%Y%m%dT%H%M%SZ") + "-" + (state["short"] or "nogit")
    )


# --- Preparation: corpora, SWE-bench, verification --------------------------------


def run_logged(command, log_path, timeout=None, campaign=None):
    """Run a preparation command with its output logged; returns its exit code.

    Under a campaign it runs through the campaign's supervisor, so --stop, a
    signal or the campaign time limit ends it promptly (the exit code is then
    negative) instead of after it finishes.
    """
    supervisor = getattr(campaign, "supervisor", None) or Supervisor()
    deadline = getattr(campaign, "deadline", None) or time.monotonic() + 10**9
    code, timed_out = supervisor.run(
        command,
        log_path,
        suite_deadline=time.monotonic() + (timeout or 10**9),
        campaign_deadline=deadline,
    )
    return 124 if timed_out else code


def corpus_ready(corpus):
    return index_ready(corpus) and dependencies_ready(corpus)


def dependencies_ready(corpus):
    """JS corpora also need their locked node_modules (see js_corpus)."""
    from benchmarks.retrieval import coding_campaign as campaign

    if corpus not in campaign.JS_CORPORA:
        return True
    from benchmarks.retrieval import js_corpus

    return js_corpus.dependencies_ready(
        campaign.BENCH / "repos" / campaign.CORPORA[corpus][0]
    )


def index_ready(corpus):
    from benchmarks.retrieval import coding_campaign as campaign
    from devstill.config import load_config, resolve_index_path

    name, config_name, commit = campaign.CORPORA[corpus]
    repo, config = (
        campaign.BENCH / "repos" / name,
        campaign.BENCH / "configs" / config_name,
    )
    if not (repo / ".git").exists() or not config.exists():
        return False
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    root = resolve_index_path(config, load_config(config)).parent
    return (
        head == commit
        and (root / "metadata.sqlite").exists()
        and not (root / ".indexing-dirty").exists()
    )


def ensure_corpus(corpus, log_path, campaign_context=None):
    """Rebuild a pinned corpus checkout and its CPU index if /tmp was wiped."""
    if corpus_ready(corpus):
        return
    if corpus not in CORPUS_BUILD:
        raise RuntimeError(
            f"corpus {corpus} is not prepared and cannot be rebuilt here"
        )
    manifest, profile = CORPUS_BUILD[corpus]
    from benchmarks.retrieval import coding_campaign as bench

    config = bench.BENCH / "configs" / f"{profile}.json"
    if not config.exists() and run_logged(
        runner_command(manifest, "prepare", "--profile", profile, "--device", "cpu"),
        log_path,
        3600,
        campaign_context,
    ):
        raise RuntimeError(f"preparing corpus {corpus} failed (see {log_path})")
    if not index_ready(corpus) and run_logged(
        runner_command(manifest, "index", "--config", str(config)),
        log_path,
        4 * 3600,
        campaign_context,
    ):
        raise RuntimeError(f"indexing corpus {corpus} failed (see {log_path})")
    if not dependencies_ready(corpus):
        from benchmarks.retrieval import js_corpus

        js_corpus.install(
            bench.BENCH / "repos" / bench.CORPORA[corpus][0], log_path, fresh=True
        )
    if not corpus_ready(corpus):
        raise RuntimeError(f"corpus {corpus} still not ready (see {log_path})")


def environment_fingerprint(cases):
    """What case verification depends on, besides the dataset itself."""
    from benchmarks.retrieval import coding_campaign as campaign

    freeze = subprocess.run(
        [str(PYTHON), "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    corpora = sorted({c["corpus"] for c in cases})
    parts = {
        "python": sys.version,
        # Devstill's own version does not affect whether a case's checks pass.
        "packages": [line for line in freeze if "devstill" not in line.lower()],
        "corpora": {c: campaign.CORPORA.get(c) for c in corpora},
    }
    if "swebench" in corpora:
        from benchmarks.retrieval import swebench

        parts["swebench_harness"] = swebench.harness_version()
        parts["swebench_images"] = {
            c["id"]: swebench.prepared(c).get("image_id") for c in cases
        }
    return sha256_json(parts)


def verified_dir(name, cases, log_path, campaign=None):
    """Cached reference verification (base fails, reference passes) per case."""
    key = f"{name}-{sha256_json(cases)[:12]}-{environment_fingerprint(cases)[:12]}"
    directory = CACHE / "verified" / key
    results = directory / "verification.json"
    if (directory / "manifest.json").exists() and results.exists():
        return directory, json.loads(results.read_text())
    if directory.exists():
        shutil.rmtree(directory)
    dataset = CACHE / "verified" / f"{key}.dataset.json"
    write_json(dataset, cases)
    run_logged(
        verify_command(dataset, directory),
        log_path,
        timeout=4 * 3600,
        campaign=campaign,
    )
    if not results.exists() or not (directory / "manifest.json").exists():
        partial = json.loads(results.read_text()) if results.exists() else []
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(
            f"case verification did not finish ({len(partial)} of {len(cases)} "
            f"cases checked; see {log_path})"
        )
    return directory, json.loads(results.read_text())


def usable_cases(cases, verification):
    """Cases whose original code fails and whose reference passes; others excluded."""
    by_id = {r["id"]: r for r in verification}
    kept, excluded = [], {}
    for case in cases:
        result = by_id.get(case["id"])
        if result and result["base_pass"] is False and result["reference_pass"] is True:
            kept.append(case)
        elif not result:
            excluded[case["id"]] = "not verified"
        elif result.get("error"):
            excluded[case["id"]] = f"verification error: {result['error']}"
        elif result["base_pass"]:
            excluded[case["id"]] = "original code already passes the acceptance check"
        else:
            excluded[case["id"]] = "reference solution fails the acceptance check"
    return kept, excluded


def filtered_verification(source, cases, destination):
    """A verified directory for the usable subset, accepted by pi_comparison."""
    from benchmarks.retrieval import coding_campaign as campaign

    destination.mkdir(parents=True, exist_ok=True)
    ids = {c["id"] for c in cases}
    for path in source.iterdir():
        stem = path.name.split(".")[0].removesuffix("-base").removesuffix("-reference")
        if stem in ids:
            shutil.copyfile(path, destination / path.name)
    results = [
        r
        for r in json.loads((source / "verification.json").read_text())
        if r["id"] in ids
    ]
    write_json(destination / "verification.json", results)
    write_json(destination / "cases.json", cases)
    write_json(
        destination / "manifest.json",
        {"cases_sha256": sha256_json(cases), "corpora": campaign.CORPORA},
    )
    return destination


def prepare_suite(campaign, settings):
    """Corpora, external images and verified cases; returns (dataset, verified)."""
    name = settings["name"]
    log_path = campaign.directory / "suites" / f"{name}.prepare.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cases = dataset_cases(settings)
    unprepared = {}
    if settings.get("prepare") == "swebench":
        campaign.log(f"{name}: preparing SWE-bench images, checkouts and indexes")
        if run_logged(
            swebench_prepare_command(ROOT / settings["dataset"]),
            log_path,
            timeout=8 * 3600,
            campaign=campaign,
        ):
            from benchmarks.retrieval import swebench

            ready = [c for c in cases if swebench.record_path(c["id"]).exists()]
            if not ready:
                raise RuntimeError(f"SWE-bench preparation failed (see {log_path})")
            unprepared = {
                c["id"]: f"could not be prepared (see {log_path.name})"
                for c in cases
                if c not in ready
            }
            campaign.log(
                f"{name}: {len(cases) - len(ready)} case(s) could not be prepared; "
                "they will be excluded"
            )
            cases = ready
    for corpus in sorted({c["corpus"] for c in cases} - {"swebench"}):
        campaign.log(f"{name}: checking corpus {corpus}")
        ensure_corpus(corpus, log_path, campaign)
    campaign.log(f"{name}: verifying {len(cases)} case(s) (cached when unchanged)")
    source, verification = verified_dir(name, cases, log_path, campaign)
    kept, excluded = usable_cases(dataset_cases(settings), verification)
    excluded.update(unprepared)
    if not kept:
        raise RuntimeError(f"no case passed reference verification (see {log_path})")
    record = campaign.suite(name)
    record["verified_cache"] = str(source)
    record["excluded_cases"] = excluded
    if not excluded:
        return ROOT / settings["dataset"], source
    campaign.log(f"{name}: excluding {len(excluded)} case(s): {excluded}")
    dataset = campaign.directory / "suites" / f"{name}.dataset.json"
    write_json(dataset, kept)
    verified = filtered_verification(
        source, kept, campaign.directory / "suites" / f"{name}.verified"
    )
    return dataset, verified


# --- Running a suite ------------------------------------------------------------------


class Supervisor:
    """Runs one child process group at a time.

    `stop()` (from a signal or the campaign deadline) ends the whole campaign;
    a suite's own time limit only ends that suite. Either way the child gets
    SIGTERM (pi_comparison then kills its worker and writes its final audit)
    and, after a grace period, SIGKILL.
    """

    def __init__(self, grace=120):
        self.process = None
        self.stop_reason = None
        self.grace = grace

    def _signal(self, number):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, number)
            except ProcessLookupError:
                pass

    def stop(self, reason):
        self.stop_reason = self.stop_reason or reason
        self._signal(signal.SIGTERM)

    def run(
        self, command, log_path, *, suite_deadline, campaign_deadline, on_line=None
    ):
        """Returns (exit code, True if the suite's own time limit ended it)."""
        if self.stop_reason:
            return -signal.SIGTERM, False  # stop requested: launch nothing
        with Path(log_path).open("a") as log:
            log.write(f"$ {' '.join(map(str, command))}\n")
            log.flush()
            self.process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            process = self.process

            def pump():
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if on_line:
                        try:
                            on_line(line.rstrip("\n"))
                        except Exception:  # noqa: BLE001, S110 - status is cosmetic
                            pass

            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            timed_out, terminated_at = False, None
            while process.poll() is None:
                moment = time.monotonic()
                if moment >= campaign_deadline and not self.stop_reason:
                    self.stop("campaign time limit reached")
                if moment >= suite_deadline and not timed_out:
                    timed_out = True
                    self._signal(signal.SIGTERM)
                if (timed_out or self.stop_reason) and terminated_at is None:
                    terminated_at = moment
                if terminated_at is not None and moment - terminated_at > self.grace:
                    self._signal(signal.SIGKILL)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    continue
            reader.join(timeout=30)
            self.process = None
            return process.returncode, timed_out


def kill_strays(marker):
    """SIGKILL leftover process groups (an agent and its Pi) that name marker."""
    killed = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if marker not in command:
            continue
        pid = int(proc.name)
        try:
            group = os.getpgid(pid)
            if group == pid:
                os.killpg(group, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
            killed += 1
        except (ProcessLookupError, PermissionError):
            pass
    return killed


def remove_containers():
    if shutil.which("docker"):
        names = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=devstill-swe-"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        if names:
            subprocess.run(
                ["docker", "rm", "-f", *names], capture_output=True, check=False
            )


def verify_command(dataset, output):
    return [
        str(PYTHON),
        "-m",
        "benchmarks.retrieval.coding_campaign",
        "verify",
        "--dataset",
        str(dataset),
        "--output",
        str(output),
    ]


def runner_command(manifest, *arguments):
    command = [str(PYTHON), "-m", "benchmarks.retrieval.runner"]
    if manifest:
        command += ["--corpus-manifest", str(ROOT / manifest)]
    return [*command, *arguments]


def swebench_prepare_command(dataset):
    return [
        str(PYTHON),
        "-m",
        "benchmarks.retrieval.swebench",
        "prepare",
        "--dataset",
        str(dataset),
    ]


def suite_command(settings, dataset, verified, output, campaign_record, resume):
    command = [
        str(PYTHON),
        "-m",
        "benchmarks.retrieval.pi_comparison",
        "run",
        "--dataset",
        str(dataset),
        "--verified",
        str(verified),
        "--output",
        str(output),
        "--variants",
        settings["variants"],
        "--repeats",
        str(settings["repeats"]),
        "--timeout",
        str(settings["timeout"]),
        "--sampling",
        campaign_record["settings"]["sampling"],
        "--endpoint-wait",
        str(campaign_record["settings"]["endpoint_wait"]),
        "--purpose",
        settings.get("purpose", "development"),
    ]
    if settings.get("pi_disable_reasoning"):
        command.append("--pi-disable-reasoning")
    if resume:
        command.append("--resume")
    return command


def suite_progress(output):
    results = output / "results.json"
    rows = json.loads(results.read_text()) if results.exists() else []
    protocol = output / "protocol.json"
    expected = (
        json.loads(protocol.read_text()).get("expected_attempts")
        if protocol.exists()
        else None
    )
    return rows, expected


def trial_counts(rows):
    from benchmarks.retrieval.coding_report import completed

    counts = {}
    for row in rows:
        passed, total = counts.get(row["variant"], (0, 0))
        counts[row["variant"]] = (passed + bool(completed(row)), total + 1)
    return ", ".join(f"{v} {p}/{n} pass" for v, (p, n) in sorted(counts.items()))


def wait_for_model(seconds):
    from benchmarks.retrieval.pi_comparison import model_headers, wait_for_endpoint
    from devstill.config import load_config

    cfg = load_config(ROOT / "devstill.json")
    wait_for_endpoint(
        cfg.llm.base_url, model_headers(cfg.model_dump(mode="json")), seconds
    )


def run_suite(campaign, supervisor, settings, deadline):
    """Prepare, run, and resume once after a failure; never raises past a suite."""
    name = settings["name"]
    record = campaign.suite(name)
    output = campaign.directory / "suites" / name
    log_path = campaign.directory / "suites" / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    dataset, verified = prepare_suite(campaign, settings)
    record["dataset_used"] = str(dataset)
    record["verified_used"] = str(verified)
    campaign.save()
    arms = len(settings["variants"].split(","))
    for attempt in (1, 2):
        resume = (output / "protocol.json").exists()
        if output.exists() and not resume:
            # A run that died during setup; keep it for diagnosis, start clean.
            output.rename(output.with_name(f"{name}.setup-failed-{attempt}"))
        rows, expected = suite_progress(output)
        expected = (
            expected
            or len(dataset_cases({"dataset": dataset})) * settings["repeats"] * arms
        )
        remaining = max(1, expected - len(rows))
        budget = (
            remaining * (settings["timeout"] + settings["acceptance_budget"]) + 1800
        )
        started = now()
        campaign.log(
            f"{name}: attempt {attempt} ({'resume' if resume else 'fresh'}), "
            f"{remaining} trial(s) left, suite time limit {budget / 3600:.1f} h"
        )

        def heartbeat(line):
            parts = line.split()
            if len(parts) >= 4 and parts[3] in {"pass", "FAIL"}:
                done, total = suite_progress(output)
                campaign.status(
                    f"running {name}: {len(done)}/{total or '?'} trials "
                    f"({trial_counts(done)}); last: {line.strip()}"
                )

        code, timed_out = supervisor.run(
            suite_command(settings, dataset, verified, output, campaign.record, resume),
            log_path,
            suite_deadline=time.monotonic() + budget,
            campaign_deadline=deadline,
            on_line=heartbeat,
        )
        stray = kill_strays(str(output))
        if settings.get("prepare") == "swebench":
            remove_containers()
        rows, _ = suite_progress(output)
        record["attempts"].append(
            {
                "started_at": stamp(started),
                "finished_at": stamp(),
                "exit_code": code,
                "resume": resume,
                "trials_recorded": len(rows),
                "suite_time_limit_hit": timed_out,
                "stray_processes_killed": stray,
                "stopped": supervisor.stop_reason,
            }
        )
        campaign.save()
        if code == 0:
            record.update(status="completed", reason=None)
            return
        tail = log_tail(log_path)
        if supervisor.stop_reason:
            record.update(
                status="interrupted", reason=f"{supervisor.stop_reason}: {tail}"
            )
            return
        if timed_out:
            record.update(
                status="failed",
                reason=f"suite time limit ({budget / 3600:.1f} h) exceeded: {tail}",
            )
            return
        record["reason"] = f"exit {code}: {tail}"
        if attempt == 2 or "Cannot resume" in tail:
            break
        campaign.log(f"{name}: failed ({record['reason']}); trying once more")
        try:
            wait_for_model(campaign.record["settings"]["endpoint_wait"])
        except Exception as exc:  # noqa: BLE001
            record["reason"] += f"; model endpoint still unavailable: {exc}"
            break
    record["status"] = "failed"


def log_tail(path, lines=6):
    try:
        text = Path(path).read_text(errors="replace").strip().splitlines()
    except OSError:
        return "no log"
    meaningful = [line for line in text if line.strip()][-lines:]
    return " | ".join(meaningful)[-800:]


# --- Reports and history -------------------------------------------------------------


def finish(campaign):
    """Quality scores, report and history; failures here are logged, not raised."""
    try:
        return write_records(campaign)
    except Exception as exc:  # noqa: BLE001 - the raw records are already saved
        campaign.log(f"REPORT FAILED ({type(exc).__name__}: {exc}); raw records kept")
        return None


def write_records(campaign):
    from benchmarks import bench_quality, bench_report

    for record in campaign.record["suites"]:
        output = campaign.directory / "suites" / record["name"]
        if (output / "results.json").exists():
            try:
                bench_quality.suite_quality(output, record.get("verified_used"))
            except Exception as exc:  # noqa: BLE001 - quality is best effort
                campaign.log(f"{record['name']}: code-quality scoring failed: {exc}")
    report = bench_report.write_campaign_report(
        campaign.directory, TRACKED / "index.jsonl"
    )
    bench_report.archive(campaign.directory, TRACKED)
    campaign.log(f"report: {campaign.directory / 'REPORT.md'}")
    return report


# --- Commands -------------------------------------------------------------------------


def overrides_from(args):
    return {
        "repeats": args.repeats,
        "variants": args.variants,
        "timeout": args.timeout,
    }


def command_run(args):
    registry = load_registry(args.registry)
    supervisor = Supervisor()

    def on_signal(signum, _frame):
        supervisor.stop(f"stopped by signal {signum}")

    if args.resume:
        campaign = Campaign.load(args.resume)
        campaign.record.setdefault("resumed_at", []).append(stamp())
        campaign.record["status"] = "running"
        settings_by_name = {s["name"]: s["settings"] for s in campaign.record["suites"]}
        campaign.log("resuming campaign")
    else:
        suites = select_suites(registry, args.suite or (), args.tag or ())
        overrides = overrides_from(args)
        endpoint_wait = args.endpoint_wait
        report = preflight(
            suites, registry, overrides, endpoint_wait if not args.dry_run else 15
        )
        planned = plan(registry, suites, overrides)
        if args.dry_run:
            print(
                json.dumps(
                    {"preflight": report, "plan": planned}, indent=2, default=str
                )
            )
            return 1 if report["blocked"] else 0
        LOCAL.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": SCHEMA_VERSION,
            "id": campaign_id(report["git"]),
            "status": "running",
            "started_at": stamp(),
            "finished_at": None,
            "note": args.note,
            "command": [Path(sys.argv[0]).name, *sys.argv[1:]],
            "host": report["host"],
            "platform": report["platform"],
            "git": report["git"],
            "model": report["model"],
            "pi": report["pi"],
            "tools": report["tools"],
            "settings": {
                "sampling": args.sampling,
                "endpoint_wait": endpoint_wait,
                "max_hours": args.max_hours,
                "overrides": overrides,
            },
            "suites": [
                {
                    "name": s["name"],
                    "settings": resolve(registry, s, overrides),
                    "dataset_sha256": sha256_json(dataset_cases(resolve(registry, s))),
                    "status": "pending",
                    "reason": None,
                    "attempts": [],
                }
                for s in suites
            ],
        }
        campaign = Campaign.create(record)
        settings_by_name = {s["name"]: s["settings"] for s in record["suites"]}
        write_json(campaign.directory / "preflight.json", report)
        if report["git"]["dirty"]:
            diff = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            (campaign.directory / "git.diff").write_text(diff)
        campaign.log(f"campaign {record['id']} in {campaign.directory}")
        for row in planned:
            campaign.log(
                f"plan: {row['name']} {row['trials']} trials, worst case {row['worst_case_hours']} h"
            )
        if report["blocked"]:
            reasons = "; ".join(
                f"{c['name']}: {c['detail']}" for c in report["blocked"]
            )
            campaign.record["status"] = "blocked"
            campaign.record["reason"] = reasons
            for suite in campaign.record["suites"]:
                suite.update(status="not_run", reason=f"campaign blocked: {reasons}")
            campaign.record["finished_at"] = stamp()
            campaign.save()
            campaign.log(f"BLOCKED: {reasons}")
            campaign.status(f"blocked: {reasons}")
            finish(campaign)
            return 2
        for name, problems in report["suites"].items():
            if problems:
                suite = campaign.suite(name)
                suite.update(status="skipped", reason="; ".join(problems))
                campaign.log(f"{name}: skipped ({suite['reason']})")
        campaign.save()

    campaign.supervisor = supervisor
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    # Lets `pi_vs_devstill_bench.sh --stop` find this process.
    pid_file = LOCAL / "runner.pid"
    pid_file.write_text(f"{os.getpid()}\n")
    try:
        return run_campaign(campaign, supervisor, settings_by_name)
    finally:
        pid_file.unlink(missing_ok=True)


def run_campaign(campaign, supervisor, settings_by_name):
    hours = campaign.record["settings"].get("max_hours")
    deadline = time.monotonic() + (hours * 3600 if hours else 10**9)
    campaign.deadline = deadline
    for record in campaign.record["suites"]:
        if record["status"] in {"completed", "skipped"}:
            continue
        name = record["name"]
        if supervisor.stop_reason or time.monotonic() >= deadline:
            record.update(
                status="not_run",
                reason=supervisor.stop_reason or "campaign time limit reached",
            )
            continue
        record.update(status="running", reason=None)
        campaign.save()
        campaign.status(f"running {name}")
        try:
            run_suite(campaign, supervisor, settings_by_name[name], deadline)
        except Exception as exc:  # noqa: BLE001 - one suite never stops the campaign
            if supervisor.stop_reason:
                record.update(status="interrupted", reason=supervisor.stop_reason)
            else:
                record.update(
                    status="failed", reason=f"{type(exc).__name__}: {exc}"[:1000]
                )
        campaign.log(
            f"{name}: {record['status']}"
            + (f" ({record['reason']})" if record.get("reason") else "")
        )
        campaign.save()
    states = {s["status"] for s in campaign.record["suites"]}
    if supervisor.stop_reason or states & {"not_run", "interrupted"}:
        campaign.record["status"] = "interrupted"
    elif "failed" in states:
        campaign.record["status"] = "completed_with_failures"
    else:
        campaign.record["status"] = "completed"
    campaign.record["finished_at"] = stamp()
    campaign.save()
    finish(campaign)
    campaign.status(
        f"{campaign.record['status']}: see {campaign.directory / 'REPORT.md'}"
    )
    return 0 if campaign.record["status"] == "completed" else 1


def command_report(args):
    from benchmarks import bench_report

    directory = Path(args.directory).resolve()
    if args.check:
        problems = bench_report.check_report(directory, TRACKED / "index.jsonl")
        print("report matches the raw results" if not problems else "\n".join(problems))
        return 1 if problems else 0
    bench_report.write_campaign_report(directory, TRACKED / "index.jsonl")
    bench_report.archive(directory, TRACKED)
    print(directory / "REPORT.md")
    return 0


def command_history(_args):
    path = TRACKED / "HISTORY.md"
    print(path.read_text() if path.exists() else "no campaigns recorded yet")
    return 0


def command_status(_args):
    latest = LOCAL / "latest"
    if not latest.exists():
        print("no campaign has run yet")
        return 1
    record = json.loads((latest / "campaign.json").read_text())
    status = latest / "status.txt"
    print(f"campaign {record['id']} ({record['status']}) in {latest.resolve()}")
    if status.exists():
        print(status.read_text().strip())
    for suite in record["suites"]:
        print(
            f"  {suite['name']}: {suite['status']}"
            + (f" - {suite['reason']}" if suite.get("reason") else "")
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run a campaign")
    run.add_argument("--suite", action="append", help="suite name (repeatable)")
    run.add_argument("--tag", action="append", help="suite tag (repeatable)")
    run.add_argument("--repeats", type=int, help="override every suite's repeats")
    run.add_argument("--variants", help="override arms, e.g. pi,runtime")
    run.add_argument(
        "--timeout", type=float, help="override the per-trial agent timeout"
    )
    run.add_argument(
        "--sampling",
        choices=["default", "greedy"],
        default="default",
        help="greedy: temperature 0 and a fixed seed for both agents",
    )
    run.add_argument(
        "--max-hours", type=float, help="stop starting work after this long"
    )
    run.add_argument("--endpoint-wait", type=float, default=1800)
    run.add_argument("--note", help="free text stored with the campaign")
    run.add_argument("--dry-run", action="store_true", help="preflight and plan only")
    run.add_argument(
        "--resume",
        nargs="?",
        const=LOCAL / "latest",
        type=Path,
        help="continue an interrupted campaign (default: the latest)",
    )
    run.add_argument("--registry", type=Path, default=REGISTRY)
    sub.add_parser("preflight", help="same as run --dry-run").add_argument(
        "--registry", type=Path, default=REGISTRY
    )
    report = sub.add_parser("report", help="rebuild a campaign report")
    report.add_argument("directory", type=Path)
    report.add_argument("--check", action="store_true")
    sub.add_parser("history", help="print the campaign history")
    sub.add_parser("status", help="print the latest campaign status")
    args = parser.parse_args(argv)
    if args.action == "preflight":
        args = parser.parse_args(["run", "--dry-run", "--registry", str(args.registry)])
    handlers = {
        "run": command_run,
        "report": command_report,
        "history": command_history,
        "status": command_status,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    sys.exit(main())
