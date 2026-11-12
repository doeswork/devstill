> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/TASK_IMPLEMENTATION.md`.

# Task Review and YOLO implementation

**Historical implementation:** new requests now use [Quick directly](QUICK_RESTORE_RESULTS.md). The generated dependency plan and separate structured-edit executor described below are retained only for older saved drafts and experiments.

**Compact Task follow-up:** [runtime corrections and the 24-case coding protocol](TASK_V2_IMPLEMENTATION.md).

**Subsequent comparison:** [old versus new benchmark and performance results](TASK_COMPARISON_RESULTS.md). These measurements expose regressions beyond the implementation smoke tests; they supersede any inference that those smoke tests demonstrated an overall improvement.

Implemented 2026-11-07. The primary interaction is now a **Task**, entered in Task Review or YOLO mode. Task Review displays file impacts before one Run action. YOLO runs the same task contract without that review stop. Search remains available as an explicit local-only mode. This implements the task-composition direction in [ONE_SHOT_STRATEGY.md](ONE_SHOT_STRATEGY.md); it does not establish independent feature-quality acceptance.

## Available behavior

- TUI mode selector: Task Review by default, YOLO, Search. Explicit `/task` and `/yolo` commands override the selection for a request. Review has Run task(s), Edit request and Cancel actions. One approval authorizes coding and application; there is no second patch-approval step in the new flow.
- CLI: `devstill task "request"`, `--yolo`, `--json`, and `--run RUN_ID`. Noninteractive review/JSON saves a review without writing source. Execution failures return nonzero. Existing Plan, Deep Plan and Pi commands remain compatibility paths.
- One structured task-definition generation produces one to six tasks, file impacts, acceptance conditions, shared contracts and dependencies. Duplicate IDs, cycles, unknown dependencies, conflicting independent read/write access and unsupported source targets are rejected.
- Each task receives one structured edit generation with thinking off and one HTTP attempt. Replacements require an exact unique anchor; new files and deletes must match declared impacts. The new task path has no model tool loop, automatic semantic repair or compatibility downgrade. The older Pi path is separate.
- Source is copied into temporary workspaces. Every edit is checked before generating a patch. Unapproved/read-only writes, missing required changes, ignored new files that would not reach the patch, stale sources and invalid paths stop execution. Failed generations never apply partial edit responses.
- Task predecessors must finish before dependents start. Dependents read current accepted files, including new files, rather than stale vectors or model summaries. Ready independent tasks can generate concurrently with `executor.task_concurrency` from 1 to 4; default 1. Their patches apply in order with source checks. Parallelism is opt-in and has no measured speed guarantee on the Qwen server.
- Optional `executor.task_validation_commands` run after each task. `executor.validation_commands` run after the complete group. These are explicit configured command lists; model-written acceptance text is not executed as a shell command. Without checks the result is `applied_unvalidated`. A failed local check blocks subsequent application; earlier applied changes remain visible. A failed final check reports failure for the combined outcome.
- A cooperative writer lock prevents two task runs from applying concurrently in the same artifact workspace. Run IDs are unique, already-started runs are not automatically replayed, and closing the TUI signals cancellation before further patch application. Cancellation does not revert already applied changes.
- Task artifacts live under `.devstill/tasks/RUN_ID/`: definition/review, source hashes, selected evidence, per-task fresh source views, model logs/responses, patches and run/check results. A freshness marker requests incremental indexing before the next TUI/task retrieval.

The Task Review setup form retains existing full-prompt budget settings when saving endpoint/output settings. The installed CLI exposes `task --help`; installation does not require a Pi coding run for this workflow.

## Current retrieval and budget implementation

Initial discovery uses the existing hybrid retriever with a pool of 24 and up to eight selected context chunks. It is one original-query retrieval, not the proposed relationship-expansion implementation. Explicit test requests keep test context eligible. The task-definition response may omit irrelevant retrieved files.

Coding is scoped to at most five existing files per task. Small files are supplied in full; large files use bounded fresh excerpts, preferring still-valid initial anchors and otherwise a task-term line window. Context is rebuilt from the reviewed paths on the execution snapshot. This avoids an additional global embedding search per child and includes predecessor edits. This line-window policy still needs evidence-quality measurement on large source files.

When the serving tokenizer is configured, the complete definition/coding prompt must fit `llm.planning_input_tokens` (or 8192 by default) and the model window after output reservation and a margin. Endpoints without tokenizer configuration use a 24,000-character input cap; this is an estimate, not an exact token limit. Each source view is bounded to 6,000 characters. Oversized input stops rather than silently dropping a reviewed file or initiating another model loop.

The stricter first implementation has **no automatic retrieval retries or format repair**. A blocked user can edit the request and explicitly submit another task. The earlier strategy's one/two-retry allowance remains an experiment to justify, not a hidden behavior of this release.

## Verification evidence

Focused automated checks cover task graph validity, independent-task conflicts, current predecessor/new-file inputs, one-attempt request settings, Review versus YOLO behavior, CLI JSON/noninteractive semantics, final versus per-task validation, cancellation, source changes, exact-anchor ambiguity, excluded/out-of-scope edits, ignored new files and disabled execution settings. A barrier-based concurrency test proves two independent coding calls are in flight together and a dependent task sees both accepted outputs.

The final full repository suite passed **423 tests**, with **one CUDA-only test skipped** in the CPU development environment. Lint and diff-whitespace checks passed. Actual smoke indexing/retrieval used the installed CUDA environment.

Headless TUI checks include a single Run action, blocked-task controls, YOLO skipping review, editing back into Task Review, and wide/narrow action layout. SVG captures are saved at `.devstill/task-implementation-v1/task-review-120.svg` and `task-review-80.svg`.

Two live synthetic checks used the configured Qwen-compatible endpoint at `http://192.168.0.131:1234/v1` and the configured model identifier. The isolated repository is `/tmp/devstill-task-live-9l4e41cd`; it contains a submit button, a cancel button and a local validation script.

| Check | Result | Definition generations | Coding generations | Thinking |
| --- | --- | --- | --- | --- |
| Review green → blue, then run | Review left source unchanged; only submit color changed; validation passed | 1 | 1 | Off for both |
| YOLO blue → purple | Incremental embeddings refreshed first; no review stop; cancel color preserved; final validation passed | 1 | 1 | Off for both |

Generation HTTP time summed to 5.253 seconds and 4.589 seconds respectively. These exclude retrieval, input token counting and validation and are single samples, not p95 or total workflow latency claims. The first live run preceded separation of local/final validation; the second exercised the final-check path. Optional concurrency was verified through automated controlled execution, not a live Qwen throughput benchmark.

Authoritative live summaries and artifact references: `.devstill/task-implementation-v1/live-smoke.json`. These are ignored local artifacts and the checkout is disposable; copy them if handing off to another machine. No feature-completeness score is inferred from these tiny color changes.

The private local installation was refreshed through `./update-local.sh`, whose candidate and installed-package checks validate startup, dependencies and package integrity. No dependency upgrades were required. Restart Devstill to load the new UI.

## Remaining evaluation

Keep the original independent acceptance requirements. Compare whole-request execution and generated task groups under matched **aggregate** token/call budgets; report end-to-end feature acceptance, decomposition omissions, evidence completeness, total model requests, latency and recovery cost. Supplied task graphs are useful diagnostics but must not be scored as generated decomposition successes.

The existing Werkzeug feature failures, Cargo ANN rebuild instability, truncation/parser findings and fresh acceptance-set requirement remain unresolved by this UI/execution work. No new embedding model, typed repository graph or reranker was installed. A graph validator verifies structure and declared conflicts; it cannot prove the model discovered every semantic dependency or that every acceptance statement has an adequate test.

Parallel task generation uses isolated scoped source copies, with ordered application and checks in the actual working tree. It does not provide independently booted full application/test environments for each branch. General concurrent indexing and legacy Pi/external writers remain outside the new task writer lock; this is not certification of arbitrary simultaneous Devstill sessions.

Continue with independently reviewed small and feature requests, including ambiguous scope, large files, interfaces and integration tests. Track every additional retrieval or coding attempt separately. Preserve the fast one-task path while measuring whether task composition improves complete feature outcomes.
