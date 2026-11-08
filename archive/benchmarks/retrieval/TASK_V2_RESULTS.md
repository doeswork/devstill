> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/TASK_V2_RESULTS.md`.

# Compact Task v2: real coding comparison

**Follow-up:** [Quick restored as the Task primitive, with 12 small-edit cases](QUICK_RESTORE_RESULTS.md). The historical v2 results below remain unchanged.

Run 2026-11-07. **Do not promote this candidate.** The compact definition and validation corrections pass automated checks, but the larger coding evaluation shows materially worse workflow reliability than legacy Plan/Pi. This report concerns the frozen candidate in `.devstill/task-v2-coding-final/`; the installed runtime was not updated.

## Results

24 distinct requests: 12 small edits and 12 bounded features, split equally between pinned Werkzeug and Cargo. Three repetitions per method produced 144 attempts. A successful workflow must pass the independent behavior/regression checks **and** finish without an execution error. A case is called consistent here only when all three attempts succeed; this is an observed count, not a general reliability guarantee.

| Measure | Old Plan/Pi | Compact Task |
| --- | ---: | ---: |
| Small edits: successful workflows | 30/36 | 15/36 |
| Small edits: behavior acceptance alone | 30/36 | 18/36 |
| Small edits: cases passing all three runs | 8/12 | 1/12 |
| Features: successful workflows | 11/36 | 2/36 |
| Features: behavior acceptance alone | 11/36 | 4/36 |
| Features: cases passing all three runs | 2/12 | 0/12 |

Five Task attempts passed behavior checks but still failed the overall run, generally after an additional planned task failed. Those are excluded from workflow successes. Failed and blocked attempts remain in the denominator.

## Performance

For the 13 small-edit attempt pairs where both workflows succeeded, median review time was **3.52 s old / 3.60 s Task**, and median time through independent validation was **11.86 s old / 10.44 s Task**. The median of per-case paired ratios was 1.044 for review and 0.866 for completion. These are different aggregations; the correctness regression prevents a speed-only promotion.

Only one feature attempt pair completed successfully under both methods: 16.72 s old / 15.21 s Task. One pair cannot establish a feature speed improvement. Fast failures are not counted as speed wins.

Each worker warms the encoder before timing. Startup is recorded separately. Completion includes evaluator-copy and test execution time. Rust dependencies were prebuilt and cached. Both methods use the configured Qwen endpoint and 4096-token per-generation caps; legacy Pi permits 12 tools/120 seconds. Total generation budgets differ. Server sampling defaults were retained. Direct request logs and Pi assistant events are available, but complete Pi token usage is unavailable, so no equal-total-token claim is made.

## What failed

1. **Unrelated write scope.** Several salt, header and exception requests acquired changes to `tests/test_datastructures.py` or changelog material. The coder then refused because it lacked the relevant insertion context. Being a retrieved file does not establish that a file needs modification. Test-reference expansion needs stronger scope and symbol resolution.
2. **Missing integration files.** The Cargo hashing case retrieved `sha256.rs` but not the crate export file `lib.rs`. One run implemented the helper and then put the export in `paths.rs`; the public API did not compile as requested. Textual call references do not replace module/import/export relationships.
3. **Incomplete coding views.** Large files are provided as bounded spans. The model sometimes reported a file missing despite its path and spans being present, or correctly lacked the specific insertion point. The source format, span selection and readiness check need separate evaluation. Do not solve this by accepting guessed edits.
4. **Extra tasks and inconsistent definitions.** Model-generated test-only tasks can fail after the requested production change already works. Some definitions contained no file impacts and were rejected. The compiler combines unordered shared-file work but does not fully enforce cohesive implementation-plus-test tasks.
5. **No successful composition proof.** The two completed Task feature attempts were each a single task. Multi-task scheduler unit tests pass, but this campaign does not establish successful generated feature composition.

The next controlled experiments should preserve the old compact scope for a single task, treat weakly related tests/docs as references rather than mandatory writes, add verified package/export edges, and provide canonical source records with sufficient editable anchors. Evaluate those independently before combining them. These are follow-up changes, not measured winners in this report.

## Per-request workflow successes

| Request | Workload | Old / 3 | Task / 3 |
| --- | --- | ---: | ---: |
| `w_repr` | small | 3 | 2 |
| `w_salt` | small | 3 | 2 |
| `w_header_lines` | small | 2 | 2 |
| `w_close` | small | 3 | 0 |
| `w_age` | small | 2 | 0 |
| `w_description` | small | 3 | 1 |
| `w_user_agent_info` | feature | 1 | 1 |
| `w_json_errors` | feature | 1 | 0 |
| `w_context_iterator` | feature | 3 | 0 |
| `w_json_sequence` | feature | 1 | 0 |
| `w_vary_middleware` | feature | 0 | 0 |
| `w_drop_headers` | feature | 0 | 0 |
| `c_empty_platform` | small | 2 | 1 |
| `c_empty_dependency` | small | 3 | 3 |
| `c_ci_empty` | small | 3 | 1 |
| `c_parent_paths` | small | 0 | 1 |
| `c_exit_codes` | small | 3 | 1 |
| `c_program_empty` | small | 3 | 1 |
| `c_digest` | feature | 0 | 0 |
| `c_optional_read` | feature | 0 | 0 |
| `c_registry_url` | feature | 0 | 0 |
| `c_bulk_env` | feature | 2 | 0 |
| `c_platform_batch` | feature | 3 | 1 |
| `c_text_module` | feature | 0 | 0 |

## Method, provenance and limits

- All 24 behavior checks fail on the starting source and pass with their reference implementations. Reference verification also ran 338 existing Werkzeug tests or the relevant Cargo crate library tests. Evaluation checks are withheld from model-visible workspaces and run in a separate copy after coding.
- Cargo is the larger indexed repository (2,440 tracked text files), but these changes target its support crates. The feature cases are API additions, middleware and module integration; they do not cover large application epics or browser frontends. Multiple cases share subsystem families.
- These are same-author development acceptance cases, not independently reviewed held-out evidence. An earlier partial campaign exposed an invented out-of-scope coding target. It was stopped, the coding schema was constrained, and the final 144-attempt campaign restarted with frozen runtime source. The partial campaign is retained and excluded from these totals.
- The frozen legacy snapshot includes the pre-existing uncommitted code. Its 117-file manifest still matches. The final candidate Python runtime fingerprint matches its campaign-start fingerprint. Pinned source corpora were not edited.
- The full runtime test suite passed **431 tests, with one CUDA-only skip**. Thirteen benchmark checks passed separately (including two newly added campaign checks). Lint and diff whitespace checks passed. An earlier full-suite run hit a TUI shutdown race; the isolated test and the subsequent full suite passed without modifying that UI code.
- Remaining scope: relationship expansion here is bounded textual-reference discovery, not a resolved import graph. Child tasks read current scoped spans rather than building new embedding overlays. There is no retrieval retry, coding repair or legacy fallback in the primary Task comparison.

## Artifacts and handoff

- Runtime changes and reproduction: [TASK_V2_IMPLEMENTATION.md](TASK_V2_IMPLEMENTATION.md).
- Cases and evaluator-only reference recipes: [coding_cases.py](../../../benchmarks/retrieval/coding_cases.py).
- Verification and campaign runner: [coding_campaign.py](../../../benchmarks/retrieval/coding_campaign.py).
- Offline audit and stricter workflow scoring: [coding_report.py](../../../benchmarks/retrieval/coding_report.py).
- `.devstill/task-v2-case-verification-c/`: 24 baseline/reference checks, reference patches and frozen case manifest.
- `.devstill/task-v2-coding-final/`: all 144 outcomes, source pools, definitions, coding contexts, patches, validation output and source audit.
- `.devstill/task-v2-coding-audit.json`: final scored rows and workload gates.
- `.devstill/task-v2-baseline/`: frozen old runtime; transfer it with ignored artifacts for reproducibility.
- `.devstill/task-v2-coding-campaign/`: explicitly aborted development campaign; excluded from final scores.

Both workload gates failed. Keep the candidate experimental and use the failure ledger for the next correction. Do not replace the missing quality evidence with the earlier 4/4 synthetic smoke result.
