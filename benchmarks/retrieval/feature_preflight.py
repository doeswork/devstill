"""Offline campaign readiness checks. No model requests, embeddings or code changes."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from benchmarks.retrieval.coding_campaign import BENCH, CORPORA, ROOT, load_cases


def freeze(output, revision):
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", revision + "^{commit}"], cwd=ROOT, text=True
    ).strip()
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", commit, "src/devstill"], cwd=ROOT
    )
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if not member.isfile():
                continue
            path = output / member.name
            if not path.resolve().is_relative_to(output.resolve()):
                raise ValueError("Invalid source archive path")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(tar.extractfile(member).read())
    manifest = {
        p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((output / "src/devstill").rglob("*"))
        if p.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "revision.txt").write_text(commit + "\n")
    print(f"Frozen {len(manifest)} source files from {commit} in {output}")


def preflight(args):
    from devstill.config import load_config, resolve_index_path, resolve_model_path
    from devstill.pi_runtime import inspect_pi

    errors = []
    dataset = load_cases(args.dataset)

    def check(name, action):
        try:
            if not action():
                errors.append(name)
        except Exception as exc:  # noqa: BLE001 - collect every readiness issue
            errors.append(f"{name}: {exc}")

    selected = [
        c
        for c in dataset
        if (not args.case or c["id"] in args.case)
        and (args.workload == "all" or c["workload"] == args.workload)
    ]
    check(
        "nonempty valid case selection",
        lambda: (
            bool(selected)
            and args.repeats > 0
            and not (set(args.case or []) - {c["id"] for c in dataset})
        ),
    )

    def baseline():
        manifest = json.loads((args.baseline / "manifest.json").read_text())
        return (
            bool(manifest)
            and (args.baseline / "src/devstill/quick_tasks.py").is_file()
            and all(
                hashlib.sha256((args.baseline / name).read_bytes()).hexdigest()
                == digest
                for name, digest in manifest.items()
            )
        )

    check("frozen Quick baseline manifest", baseline)

    def verified():
        manifest = json.loads((args.verified / "manifest.json").read_text())
        rows = json.loads((args.verified / "verification.json").read_text())
        return (
            manifest["cases_sha256"]
            == hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest()
            and json.loads((args.verified / "cases.json").read_text()) == dataset
            and len(rows) == len(dataset)
            and {r["id"] for r in rows} == {c["id"] for c in dataset}
            and all(not r["base_pass"] and r["reference_pass"] for r in rows)
        )

    check("reference verification for the current case set", verified)
    configs = {}
    for corpus in sorted({c["corpus"] for c in selected}):
        name, cfg_name, commit = CORPORA[corpus]
        repo = BENCH / "repos" / name
        check(
            f"{corpus} pinned revision",
            lambda repo=repo, commit=commit: (
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=repo, text=True
                ).strip()
                == commit
            ),
        )
        check(
            f"{corpus} clean source",
            lambda repo=repo: (
                not subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=repo, text=True
                ).strip()
            ),
        )

        def index(cfg_name=cfg_name, corpus=corpus):
            path = BENCH / "configs" / cfg_name
            cfg = load_config(path)
            configs[corpus] = cfg
            chroma = resolve_index_path(path, cfg)
            return (
                chroma.is_dir()
                and (chroma / "chroma.sqlite3").is_file()
                and (chroma.parent / "metadata.sqlite").is_file()
                and not (chroma.parent / ".indexing-dirty").exists()
                and resolve_model_path(cfg).is_dir()
            )

        check(f"{corpus} index and local embedding model", index)
    check("configured model", lambda: load_config(ROOT / "devstill.json").llm.enabled)
    check("Pi installation", lambda: inspect_pi().ready)
    check(
        "Python acceptance environment", lambda: (ROOT / ".venv/bin/python").is_file()
    )
    if any(c["corpus"] == "cargo" for c in selected):
        check(
            "cached Rust toolchain",
            lambda: (
                Path("/tmp/devstill-cargo/bin/cargo").is_file()
                and Path("/tmp/devstill-rustup/toolchains").is_dir()
            ),
        )
    check(
        "temporary disk space (5 GiB)",
        lambda: shutil.disk_usage("/tmp").free >= 5 * 1024**3,
    )
    return {
        "ready": not errors,
        "errors": errors,
        "cases": len(selected),
        "small": sum(c["workload"] == "small" for c in selected),
        "feature": sum(c["workload"] == "feature" for c in selected),
        "attempts": len(selected) * args.repeats * 2,
        "model_requests": 0,
        "candidate": args.candidate,
        "baseline": str(args.baseline),
        "dataset_status": (
            "User-supplied cases; held-out status has not been established."
            if args.dataset
            else "Exposed development cases. Fresh feature acceptance cases remain a separate release gate."
        ),
        "limits": "Checks local prerequisites only; server availability and model quality are untested. Natural operating points, not matched aggregate token/call budgets.",
    }
