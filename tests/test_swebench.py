"""SWE-bench slice: selection, case format, checkout snapshot, shims, grading.

Nothing here needs Docker, the network or the official harness.
"""

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.retrieval import coding_campaign, swebench
from benchmarks.retrieval.patches import is_test_path, patch_stats

PATCH = (
    "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
    "@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n y = 3\n"
)


def instance(number, repo="django/django", **fields):
    return {
        "instance_id": f"{repo.split('/')[1]}__{repo.split('/')[1]}-{number}",
        "repo": repo,
        "base_commit": "a" * 40,
        "image": f"swebench/sweb.eval.x86_64.{number}:latest",
        "patch": PATCH,
        "test_patch": "diff --git a/tests/t.py b/tests/t.py\n",
        "FAIL_TO_PASS": json.dumps(["tests/t.py::test_x"]),
        "PASS_TO_PASS": [],
        "difficulty": "<15 min fix",
        "problem_statement": "Something is wrong.",
        **fields,
    }


def test_patch_helpers():
    stats = patch_stats(PATCH + PATCH.replace("pkg/mod.py", "tests/test_mod.py"))
    assert stats == {
        "files": ["pkg/mod.py", "tests/test_mod.py"],
        "added": 2,
        "removed": 2,
    }
    assert is_test_path("tests/x.py") and is_test_path("pkg/test_x.py")
    assert is_test_path("django/conftest.py") and not is_test_path(
        "pkg/testing_utils.py"
    )


def test_selection_is_deterministic_and_round_robin():
    rows = [
        instance(3),
        instance(1),
        instance(2),
        instance(9, repo="sympy/sympy"),
        instance(5, repo="astropy/astropy"),  # compiled extensions: excluded
        instance(4, difficulty="1-4 hours"),
        instance(6, patch=PATCH + PATCH.replace("pkg/mod.py", "pkg/other.py")),
        instance(7, patch=PATCH.replace("pkg/mod.py", "tests/test_mod.py")),
        instance(8, FAIL_TO_PASS=[]),
    ]
    rules = {**swebench.SELECTION, "count": 3}
    chosen = swebench.select(rows, rules)
    assert [r["instance_id"] for r in chosen] == [
        "django__django-1",
        "django__django-2",
        "sympy__sympy-9",
    ]
    assert swebench.select(list(reversed(rows)), rules) == chosen


def test_cases_load_through_the_campaign_validator():
    case = swebench.to_case(instance(11066))
    assert case["id"] == "swe_django_django_11066"
    assert case["query"].startswith(swebench.QUERY_PREFIX)
    assert coding_campaign.load_cases_from_list([case]) == [case]
    broken = {**case, "swebench": {**case["swebench"], "image": ""}}
    with pytest.raises(ValueError, match="SWE-bench"):
        coding_campaign.load_cases_from_list([broken])


def test_frozen_dataset_matches_the_selection_rules():
    cases = swebench.load()
    assert len(cases) == swebench.SELECTION["count"]
    assert all(swebench.eligible(c["swebench"]) for c in cases)
    assert len({c["id"] for c in cases}) == len(cases)
    provenance = json.loads(
        swebench.DATASET.with_name(
            swebench.DATASET.stem + ".provenance.json"
        ).read_text()
    )
    assert provenance["instance_ids"] == [c["swebench"]["instance_id"] for c in cases]
    assert provenance["selection"] == swebench.SELECTION


def make_testbed(path):
    path.mkdir(parents=True)
    (path / ".gitignore").write_text("*.egg-info/\n")
    (path / "mod.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=path,
        check=True,
        env={
            "GIT_AUTHOR_DATE": "2001-01-01T00:00:00",
            "GIT_COMMITTER_DATE": "2001-01-01T00:00:00",
            "PATH": "/usr/bin:/bin",
        },
    )
    # What an image's install step leaves behind.
    (path / "setup.cfg").write_text("[metadata]\n")
    (path / "mod.py").write_text("x = 1  # patched by install\n")
    (path / "pkg.egg-info").mkdir()
    (path / "pkg.egg-info/PKG-INFO").write_text("ignored")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()


def test_snapshot_commits_the_image_state_reproducibly(tmp_path):
    base = make_testbed(tmp_path / "one")
    make_testbed(tmp_path / "two")
    first = swebench.snapshot(tmp_path / "one", base)
    second = swebench.snapshot(tmp_path / "two", base)
    assert first == second != base  # same tree, fixed identity and dates
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=tmp_path / "one", text=True
    )
    assert status == ""  # agents start from a clean working tree
    assert (tmp_path / "one/pkg.egg-info/PKG-INFO").exists()  # ignored, kept
    with pytest.raises(ValueError, match="is not at"):
        swebench.snapshot(tmp_path / "one", base)


def test_prepared_case_maps_to_checkout_config_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(coding_campaign, "BENCH", tmp_path)
    case = swebench.to_case(instance(11066))
    with pytest.raises(ValueError, match="not prepared"):
        coding_campaign.corpus_entry(case)
    record = swebench.record_path(case["id"])
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"snapshot_commit": "b" * 40}))
    assert coding_campaign.corpus_entry(case) == (
        "swebench/swe_django_django_11066",
        "swebench/swe_django_django_11066.json",
        "b" * 40,
    )
    config = swebench.index_config(case["id"], tmp_path / "repo")
    assert config["index"]["vectors"] is False
    assert config["index"]["path"].endswith(
        "indexes/swebench/swe_django_django_11066/chroma"
    )


def test_shims_run_python_and_pytest_inside_the_container():
    scripts = swebench.shim_scripts("/usr/bin/docker", "devstill-swe-abc", 1000, 1000)
    assert set(scripts) == {"python", "python3", "pytest", "pip", "pip3"}
    python = scripts["python"]
    assert python.startswith('#!/bin/sh\nexec "/usr/bin/docker" exec -i -w "$PWD"')
    assert "-u 1000:1000" in python and 'PYTHONPATH="${PYTHONPATH:-}"' in python
    assert python.rstrip().endswith(f'devstill-swe-abc {swebench.TESTBED_PYTHON} "$@"')
    assert scripts["pytest"].rstrip().endswith('-m pytest "$@"')
    assert scripts["pip"].rstrip().endswith('-m pip "$@"')


def fake_harness(resolved):
    def run(command, cwd, **kwargs):
        run_id = command[command.index("--run_id") + 1]
        iid = command[command.index("--instance_ids") + 1]
        predictions = Path(command[command.index("--predictions_path") + 1])
        assert json.loads(predictions.read_text())["model_patch"] == PATCH
        if resolved is not None:
            logs = (
                Path(cwd) / "logs/run_evaluation" / run_id / swebench.MODEL_NAME / iid
            )
            logs.mkdir(parents=True)
            (logs / "report.json").write_text(
                json.dumps({iid: {"resolved": resolved, "tests_status": {}}})
            )
            (logs / "test_output.txt").write_text("test output")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_official_grading_result_and_missing_report():
    row = instance(11066)
    passed = swebench.evaluate(row, PATCH, run=fake_harness(True))
    assert passed["resolved"] and passed["returncode"] == 0
    assert passed["output"] == "test output"
    failed = swebench.evaluate(row, PATCH, run=fake_harness(False))
    assert not failed["resolved"] and failed["returncode"] == 1
    # No report means the grader broke: an infrastructure error, not a failed fix.
    with pytest.raises(RuntimeError, match="no report"):
        swebench.evaluate(row, PATCH, run=fake_harness(None))
