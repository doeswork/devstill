> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/TASK_V2_IMPLEMENTATION.md`.

# Compact Task corrections and real coding evaluation

**Superseded default:** Task Review/YOLO now use [the existing Quick engine](QUICK_RESTORE_RESULTS.md); feature decomposition is deferred. This document records the evaluated v2 experiment.

Status: candidate implemented and evaluated; **both parity gates failed**. See [the 144-attempt results](TASK_V2_RESULTS.md).
No installed runtime was updated in this campaign.

## Runtime changes

- Definition generation uses a compact wire schema. Existing file targets are constrained to the supplied inventory; new files and predecessor-created inputs have separate fields. IDs, titles and repeated impact reasons are materialized locally into the existing version-1 TaskDraft, preserving saved-artifact compatibility.
- Redundant impacts are coalesced. Unordered tasks sharing read/write ownership are combined into a single attempt within the existing five-file limit. Explicitly ordered work stays ordered. Lifecycle contradictions, cycles, unknown dependencies and scope violations still fail validation. This compilation does not infer an arbitrary execution order or authorize additional files.
- Predecessor outputs become declared Read inputs before review, and execution reads their accepted contents. The coding prompt distinguishes the current task from the overall request.
- Search results carry the indexed file fingerprint. Task freshness checks compare file hashes; exact current source replaces parser-normalized text in evidence. Results without fingerprints retain the strict legacy text check.
- Task discovery starts with 12 hybrid candidates and a five-chunk prompt allowance. Optional one-hop reference expansion examines three original anchors and adds at most 12 candidates, respecting exclusions. These are verified textual call references, **not** a complete static call graph or resolved import graph. Related test snippets may be included without the user spelling “tests.”
- Coding reads whole small files and multiple bounded, current spans in larger approved files. It does not rebuild embeddings between child tasks. No additional write scope is discovered during execution.
- Coding response paths are constrained to approved writable paths. Application still verifies labels, operations, unique replacement anchors and source hashes. One definition generation and one coding generation per task remain the normal budget. No automatic coding repair was added; this candidate uses no targeted retrieval retries.

Task Review and YOLO retain the same engine. Relationship selection and fresh-span retrieval are candidates to evaluate, not established improvements.

## Benchmark implementation

`coding_cases.py` defines 24 distinct proposed changes: six small edits and six features in each of pinned Werkzeug and Cargo. They include compatibility changes, validation, new APIs, delegated methods, middleware, crate exports and new modules. They are bounded API features rather than large application epics, and they do not measure browser frontend behavior or parallel throughput.

Each case includes evaluator-only behavior checks and a reference patch recipe. `coding_campaign verify` confirms baseline failure and reference success, including 338 existing Werkzeug regressions or the relevant Cargo crate's library tests. Reference patches, checks and metadata remain outside model-visible execution workspaces. Coding happens in disposable copies; acceptance runs afterward in another copy. Original corpora are unchanged.

The cases are source-authored development acceptance tests, not independently authored held-out data. The first candidate campaign was stopped after an out-of-scope coding-target failure was inspected; those outputs remain in `.devstill/task-v2-coding-campaign/`. The final candidate adds writable-path schema constraints. No further runtime tuning occurs during its campaign.

Final protocol: 24 cases × three repetitions × two methods = 144 attempts. Variant order alternates. Legacy Plan/Pi runs from `.devstill/task-v2-baseline/src`, whose 117-file manifest includes uncommitted source. The candidate runs from the current source tree. Each subprocess warms the local encoder before timing. Both use the configured Qwen endpoint with reasoning disabled for Task calls, a 4096-token per-generation cap, unchanged server sampling defaults, and serial scheduling. Legacy Pi has 12 tools/120 seconds; aggregate call budgets differ. Pi token totals may be unavailable and cannot be treated as zero.

Acceptance time includes copying to the evaluator workspace and validation. Compiler dependencies are prebuilt and cached. Startup, review, coding and validation times are retained separately. Successful paired completion timings and first-attempt correctness are the relevant comparison; failed attempts never become speed wins. The reporter also distinguishes behavior acceptance from successful workflow completion.

## Reproduction

Carry the ignored baseline snapshot, artifact directories and frozen index configurations with a handoff. The baseline is required to reproduce the old method. Rust 1.82.0 was installed under `/tmp/devstill-rustup` and `/tmp/devstill-cargo`; build artifacts are under `/tmp/devstill-cargo-target`. Python corpus test dependencies were installed in the development `.venv`, not the installed Devstill runtime.

```bash
export PYTHONPATH="$PWD/src"
BENCH_PY="$HOME/.local/share/devstill/venv/bin/python"
.venv/bin/python -m benchmarks.retrieval.coding_campaign verify \
  --output .devstill/task-v2-reference-new
"$BENCH_PY" -m benchmarks.retrieval.coding_campaign run \
  --verified .devstill/task-v2-reference-new \
  --output .devstill/task-v2-campaign-new
.venv/bin/python -m benchmarks.retrieval.coding_report \
  .devstill/task-v2-campaign-new --output .devstill/task-v2-audit-new.json
```

Output directories/files must be new. Keep the runtime source frozen for the entire campaign. Do not run concurrent model benchmarks. The older definition-only comparison remains a development diagnostic; it does not run the new relationship expansion. For historical v1 reproduction, use the frozen snapshot rather than current source.

Promotion requires correctness and speed parity separately for small edits and features, no reliable-case regressions, and no scope/freshness violations. Twenty-four requests are a meaningful next comparison, not broad reliability certification. Record per-case failures and measured task counts before claiming composed-feature success.
