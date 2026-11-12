"""Shared node_modules bookkeeping for JS benchmark corpora (no Node needed)."""

import json
import os

import pytest

from benchmarks.retrieval import js_corpus


@pytest.fixture
def corpus(tmp_path):
    """A pnpm-shaped install: store, top-level links, .bin, install record."""
    source = tmp_path / "repo"
    package = source / "node_modules/.pnpm/a@1.0.0/node_modules/a"
    package.mkdir(parents=True)
    (package / "index.js").write_text("module.exports = 1\n")
    modules = source / "node_modules"
    (modules / "a").symlink_to(".pnpm/a@1.0.0/node_modules/a")
    (modules / ".bin").mkdir()
    (modules / ".bin/a").write_text("#!/bin/sh\n")
    (modules / ".modules.yaml").write_text("layoutVersion: 5\n")
    js_corpus.fingerprint_path(source).write_text(
        js_corpus.dependencies_fingerprint(source) + "\n"
    )
    js_corpus.manifest_path(source).write_text(
        json.dumps(js_corpus.dependency_manifest(source))
    )
    yield source
    js_corpus.set_store_writable(source, True)


def test_tool_caches_do_not_count_as_changed_dependencies(corpus):
    modules = corpus / "node_modules"
    for cache in (".vite/vitest", ".vite-temp", ".cache/prettier"):
        (modules / cache).mkdir(parents=True)
        (modules / cache / "x").write_text("cache")
    assert js_corpus.audit(corpus) == []
    assert js_corpus.dependencies_ready(corpus)


def test_deleted_or_edited_package_files_are_reported(corpus):
    target = corpus / "node_modules/.pnpm/a@1.0.0/node_modules/a/index.js"
    target.write_text("module.exports = 2  // edited\n")
    assert js_corpus.audit(corpus) == [".pnpm/a@1.0.0/node_modules/a/index.js"]
    target.unlink()
    assert js_corpus.audit(corpus) == [".pnpm/a@1.0.0/node_modules/a/index.js"]
    assert not js_corpus.dependencies_ready(corpus)
    # The cheap per-clone check only looks at the install record and names.
    assert js_corpus.dependencies_ready(corpus, thorough=False)


def test_removed_or_repointed_top_level_links_are_reported(corpus):
    link = corpus / "node_modules/a"
    link.unlink()
    assert "a" in js_corpus.audit(corpus)
    link.symlink_to("/elsewhere")
    assert js_corpus.audit(corpus) == ["a"]


def test_read_only_store_refuses_deletion_and_can_be_reopened(corpus):
    target = corpus / "node_modules/.pnpm/a@1.0.0/node_modules/a/index.js"
    js_corpus.set_store_writable(corpus, False)
    if os.geteuid() == 0:
        pytest.skip("root ignores permission bits")
    with pytest.raises(PermissionError):
        target.unlink()
    js_corpus.set_store_writable(corpus, True)
    target.unlink()
    assert not target.exists()


def test_missing_manifest_is_never_treated_as_clean(corpus):
    js_corpus.manifest_path(corpus).unlink()
    assert js_corpus.audit(corpus) == ["(no install manifest)"]
