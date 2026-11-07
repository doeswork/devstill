from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace

from benchmarks.retrieval.coding_campaign import copy_index, worker
from benchmarks.retrieval.coding_report import completed, summarize_features
from tests.test_features import controlled, proposal_data, results
from tests.test_planning import FakeClient
from tests.test_tasks import config, make_repo


def test_feature_worker_routes_through_coordinator_quick_and_refreshes_private_index(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    for name, value in {
        "seed.py": "# convention\n",
        "endpoint.py": "result = None\n",
        "ui.txt": "pending\n",
    }.items():
        (repo / name).write_text(value)
    cfg = config(context_window=12288)
    monkeypatch.setattr(
        "benchmarks.retrieval.coding_campaign.runtime_settings",
        lambda *a: (cfg.llm, cfg.executor),
    )
    monkeypatch.setattr(
        "devstill.config.load_config", lambda p: cfg.model_copy(deep=True)
    )
    copies, refreshes, queries = [], [], []
    monkeypatch.setattr(
        "benchmarks.retrieval.coding_campaign.copy_index",
        lambda source, dest: copies.append(dest),
    )

    class Search:
        def __init__(self, config_path, config, **kwargs):
            assert config.repo.root == str(repo)
            assert config.index.path == str(repo / ".devstill/chroma")
            self.embedder = SimpleNamespace(warm=lambda: None)
            self.chroma = object()

        def query(self, query, limit):
            queries.append(query)
            assert limit == 12
            if query == "Build service, endpoint and UI":
                return results(repo, ["seed.py", "endpoint.py", "ui.txt"])
            return retrieve(query)

        def close(self):
            pass

    class Indexer:
        def __init__(self, config_path, config, **kwargs):
            assert config.repo.root == str(repo)
            assert config.index.path == str(repo / ".devstill/chroma")

        def run(self):
            refreshes.append((repo / "service.py").read_text())

        def close(self):
            pass

    monkeypatch.setattr("devstill.search.HybridSearch", Search)
    monkeypatch.setattr("devstill.indexer.RepositoryIndexer", Indexer)
    retrieve, runner, _, _ = controlled(monkeypatch, repo)

    # The worker clears the dirty marker *after* index refresh, before the query.
    def candidate_query(query):
        if query.startswith("Create"):
            return results(repo, ["seed.py"])
        if query.startswith("Wire"):
            return results(repo, ["service.py", "endpoint.py"])
        return results(repo, ["ui.txt"])

    retrieve = candidate_query
    client = FakeClient(json.dumps(proposal_data()))
    client.config = cfg.llm
    monkeypatch.setattr(
        "devstill.feature_planning.FeatureClient",
        lambda *args, **kw: nullcontext(client),
    )
    output = tmp_path / "output"
    output.mkdir()
    worker(
        {
            "id": "fixture",
            "query": "Build service, endpoint and UI",
            "corpus": "werkzeug",
            "workload": "feature",
        },
        "feature",
        repo,
        output,
    )
    row = json.loads((output / "result.json").read_text())
    assert not row.get("error"), row
    assert row["execution"]["status"] == "applied_unvalidated", row
    assert row["task_count"] == 3 and row["dependency_edges"] == 1
    assert len(queries) == 4 and len(refreshes) == 2
    assert copies == [repo / ".devstill"]
    assert runner.calls == 3
    assert len(list((output / "artifacts/tasks").glob("*/quick/.execute.patch"))) == 3


def test_index_copy_is_independent_and_includes_sqlite_wal_state(tmp_path):
    source = tmp_path / "source"
    chroma = source / "chroma"
    chroma.mkdir(parents=True)
    (chroma / "segment.bin").write_bytes(b"original")
    with sqlite3.connect(source / "metadata.sqlite") as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE example(value TEXT)")
        connection.execute("INSERT INTO example VALUES('original')")
        connection.commit()
        destination = tmp_path / "private"
        copy_index(chroma, destination)
        with sqlite3.connect(destination / "metadata.sqlite") as copied:
            assert copied.execute("SELECT value FROM example").fetchone() == (
                "original",
            )
            copied.execute("UPDATE example SET value='modified'")
        assert connection.execute("SELECT value FROM example").fetchone() == (
            "original",
        )
    (destination / "chroma/segment.bin").write_bytes(b"changed")
    assert (chroma / "segment.bin").read_bytes() == b"original"


def test_composed_report_counts_whole_feature_and_actual_multi_task_success():
    base = {
        "id": "a",
        "repeat": 0,
        "workload": "feature",
        "acceptance_pass": True,
        "review_seconds": 1,
        "coding_seconds": 3,
        "total_seconds": 4,
    }
    old = {**base, "variant": "old", "execution": {"status": "applied"}}
    new = {
        **base,
        "variant": "feature",
        "task_count": 2,
        "dependency_edges": 1,
        "execution": {
            "engine": "composed_quick",
            "status": "validated",
            "tasks": {"t1": {"status": "validated"}, "t2": {"status": "failed"}},
        },
    }
    assert not completed(new)
    cases = [{"id": "a", "workload": "feature"}]
    report = summarize_features([old, new], cases, 1)
    assert report["complete"]
    assert report["workloads"]["feature"]["feature"]["workflow_passes"] == 0
    assert report["workloads"]["feature"]["paired_successes"] == 0
    new["execution"]["tasks"]["t2"]["status"] = "validated"
    report = summarize_features([old, new], cases, 1)
    assert report["workloads"]["feature"]["feature"]["dependent_success_cases"] == ["a"]
    new["task_count"] = 1
    report = summarize_features([old, new], cases, 1)
    assert report["workloads"]["feature"]["feature"]["workflow_passes"] == 1
    assert report["workloads"]["feature"]["feature"]["composed_success_cases"] == []


def test_worker_failures_and_timeouts_propagate(tmp_path):
    import os
    import subprocess
    import sys

    import pytest

    from benchmarks.retrieval.coding_campaign import invoke_worker

    with (tmp_path / "worker.log").open("w") as log:
        with pytest.raises(subprocess.CalledProcessError):
            invoke_worker(
                [sys.executable, "-c", "raise SystemExit(3)"], os.environ.copy(), log
            )
        with pytest.raises(subprocess.TimeoutExpired):
            invoke_worker(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                os.environ.copy(),
                log,
                timeout=0.1,
            )


def test_versioned_dataset_validation_does_not_overwrite_exposed_cases(tmp_path):
    import pytest

    from benchmarks.retrieval.coding_campaign import load_cases
    from benchmarks.retrieval.coding_cases import CASES

    path = tmp_path / "cases.json"
    path.write_text(json.dumps([CASES[0]]))
    assert len(load_cases(path)) == 1
    assert len(load_cases()) == 24
    path.write_text(json.dumps([CASES[0], CASES[0]]))
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(path)
    path.write_text(json.dumps([{**CASES[0], "id": "../escape"}]))
    with pytest.raises(ValueError):
        load_cases(path)
