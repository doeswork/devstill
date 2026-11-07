# Quick restored as the Task primitive

Task Review and YOLO now use the existing Quick retrieval, file-impact classifier
and Pi executor directly. They do not generate a coordinating Task Plan or use the
experimental structured-edit executor for new requests. Feature decomposition is
deferred.

The new small-edit comparison finished: **Task passed 10/12; old Quick passed
8/12**, with no case passing only on old Quick. This restores the old algorithm
under the Task interface. Independent model sampling can produce different
results even with identical inputs; this sample does not establish a statistical
improvement or certify feature coding.

## What changed

- `TuiWorkflow` retrieves the original 12-candidate pool. `create_quick_draft`
  calls the unchanged adaptive selection (up to five chunks) and `generate_plan`.
  It creates one task's presentation and persistence metadata locally, with no
  additional model-generated instruction, acceptance list or dependency graph.
- The original request and file impacts go unchanged to `RepositoryExecutor`.
  Review pauses before execution; YOLO skips that pause. Both use Pi's existing
  scoped tools, limits, format-repair behavior and focused partial-change repair.
  Pi can make several bounded model/tool calls; “Quick” does not mean one model
  response is guaranteed to complete every edit.
- Task IDs, source freshness checks, cancellation before apply, writer locking,
  no automatic replay, per-run artifacts and incremental-index invalidation remain.
  Plan/Pi artifacts are isolated in `.devstill/tasks/RUN_ID/quick/`.
- `engine: quick` identifies new drafts. Saved drafts without an engine field
  retain `structured` behavior for compatibility. The dependency scheduler,
  `task_concurrency`, and `task_validation_commands` remain experimental and are
  not used by new Quick tasks. Quick runs `executor.validation_commands`.
- UI help and README describe the shared Quick path instead of promising task
  decomposition or a single structured coding response.

`planning.py`, `context.py`, `pi_runtime.py` and `pi/devstill_policy.ts` are
byte-identical to the frozen old baseline. The execution prompt and scope
validation functions are also unchanged. Search's only runtime difference from
that snapshot is attaching indexed file hashes after ranking, for freshness
checks. The earlier shared validation-helper refactor preserves configured checks.

## Small-edit protocol

Twelve distinct small requests: six in Werkzeug and six in Cargo. One attempt per
method per request gives **24 attempts**, with alternating variant order and serial
model execution. These are the existing exposed development cases; there are no
feature attempts, new held-out cases, or browser/frontend tests in this run.

Both use the configured Qwen-compatible endpoint at
`http://192.168.0.131:1234/v1`, alias `vllm-qwen38-27b`, frozen embedding indexes,
4096-token generation caps, and the same Pi configuration (12 read/edit calls per
attempt, a 120-second executor limit, and the existing bounded partial-change
repair). Aggregate model calls can differ; there is no claim of equal total token
cost. Model sampling settings were unchanged.

Old source comes from `.devstill/task-v2-baseline/src`. Candidate Python source
was frozen for all 24 attempts; the final audit confirms both it and the frozen
baseline were unchanged. Each attempt uses a clean temporary checkout. The pinned
corpora remain Werkzeug `6389612fd1ee1bd93579eed5026e8fd471d04abd` and Cargo
`8f40fc59fb0c8df91c97405785197f3c630304ea`. Cargo is the larger corpus, with
2,440 indexed files and 15,384 searchable units; Werkzeug has 302 tracked files.

Existing reference verification establishes that every acceptance check fails on
starting source and passes with its reference implementation. Evaluation happens
after generation in a separate copy, with evaluator-only behavior checks plus
338 existing Werkzeug regressions or the relevant Cargo crate's library tests.
Those checks are not shown to the model. Automatic runtime validation is empty
for both variants in this campaign, so runtime application alone cannot count as
a benchmark pass. A pass requires acceptance success and workflow completion.

## Results

| Metric | Old Quick | Task using Quick |
| --- | ---: | ---: |
| Distinct small requests | 12 | 12 |
| Behavior and completed workflow passes | 8/12 (66.7%) | 10/12 (83.3%) |
| Initial planning calls, including format repairs | 13 | 13 |
| Median review time, eight shared successes | 3.01 s | 3.14 s |
| Median through coding/apply, eight shared successes | 11.81 s | 10.16 s |
| Median through external acceptance, eight shared successes | 13.27 s | 11.34 s |

Timing starts at retrieval after encoder warmup. It excludes startup, index
construction/refresh and human confirmation. “Through coding/apply” includes
retrieval and review; “through external acceptance” additionally includes the
benchmark tests. Failed attempts are excluded from paired speed comparisons.
The unsuccessful age edit took 50.47 s old / 45.80 s Task through acceptance;
this restoration has not eliminated slow failures.

The post-run audit found **12/12 identical retrieval pools and 12/12 identical
initial model requests**, normalizing only disposable repository paths. Prompts,
source contents/order, model, endpoint, response mode, output cap and reasoning
mode matched. Both used one `devstill_plan` generation per case; both needed the
existing `devstill_plan_repair` for `c_ci_empty`. No generated Task schema was used.

| Case | Requested change | Old Quick | Task using Quick |
| --- | --- | --- | --- |
| `w_repr` | Unparsed UserAgent representation | Pass | Pass |
| `w_salt` | Reject bool salt length | Pass | Pass |
| `w_header_lines` | Reject CR/LF in header values | Fail | Pass |
| `w_close` | Idempotent iterator cleanup | Pass | Pass |
| `w_age` | Reject bool ages | Fail | Fail |
| `w_description` | Normalize exception newlines | Pass | Pass |
| `c_empty_platform` | Reject empty platform names | Pass | Pass |
| `c_empty_dependency` | Handle empty dependency names | Pass | Pass |
| `c_ci_empty` | Ignore empty CI environment values | Pass | Pass |
| `c_parent_paths` | Preserve leading relative parents | Fail | Fail |
| `c_exit_codes` | Accept unsigned-byte exit codes | Pass | Pass |
| `c_program_empty` | Reject empty executable names | Fail | Pass |

## Remaining failures and next experiment

- **`w_age` failed on both.** Retrieval ranked `dump_age` first, but the Pi coding
  prompt receives file impacts without the retrieved symbol's location. In the
  pinned file the function starts at line 1067. Logs show repeated reads elsewhere,
  guessed replacement anchors and exhausted/repeated completion checks. The
  retriever found the right function; the execution handoff lost useful location
  information.
- **`c_parent_paths` failed on both.** Old Quick returned an unresolved Search
  entry. Task's independently sampled plan allowed coding, but its Rust rewrite
  did not compile. That is a failed benchmark even though application succeeded.
- Old Quick additionally failed `w_header_lines` with invalid Python and
  `c_program_empty` with unresolved Search. Task passed both in this sample.

The next bounded improvement to test is carrying already-retrieved symbol names
and current line ranges into Quick's first scoped read. Preserve the same single
review, existing writable scope and call limits. Compare that change separately
against this restored baseline, including large-file edits. This report implements
no new anchor mechanism or feature coordinator. Configured syntax/build checks
remain essential before treating a real change as validated.

## Verification and reproduction

Automated checks compare exact old/new planning messages, schemas, repair calls,
PlanOutput and Pi prompts/patches. They also cover saved-draft round trips, Review
versus YOLO, real configured validation success/failure, stale inputs, cancellation
before application and replay prevention. Historical structured drafts remain
compatible.

The final full suite passed **444 tests, with one CUDA-only skip**. Lint and diff
whitespace checks passed. An earlier full run hit a timing-sensitive plan-editor
layout assertion; its isolated rerun and the subsequent full suite passed without
a code change. The separate controlled Task UI and execution tests passed.

The private local installation was refreshed with `./update-local.sh`. Candidate
and installed startup/package-integrity checks passed without dependency changes.
An isolated import check confirms the installed Task workflow uses the matching
`quick_tasks.py`. Reopen Devstill to load this path.

Run the focused tests:

```bash
.venv/bin/pytest -q tests/test_quick_tasks.py tests/test_task_workflow.py \
  tests/test_coding_campaign.py tests/test_tasks.py tests/test_task_retrieval.py
```

Reproduce on this workspace, choosing a new output directory:

```bash
PYTHONPATH=src:. "$HOME/.local/share/devstill/venv/bin/python" \
  -m benchmarks.retrieval.coding_campaign run \
  --candidate quick --workload small --repeats 1 \
  --verified .devstill/task-v2-case-verification-c \
  --output .devstill/quick-task-comparison-v2
.venv/bin/python -m benchmarks.retrieval.coding_report \
  .devstill/quick-task-comparison-v2 \
  --output .devstill/quick-task-comparison-v2/audit.json
```

Use `--repeats 3` for a predeclared 72-attempt small-only reliability campaign.
Do not repeatedly run until a preferred score appears. Corpus/index preparation
is documented in the retrieval README and Task v2 implementation report; frozen
source, verified cases and native test dependencies must be available on a new
machine.

Authoritative local artifacts are `.devstill/quick-task-comparison-v1/`: protocol,
24 outcomes, request logs, retrieval pools, reviews, patches, acceptance logs,
summary, final source audit and request-equivalence audit. These ignored artifacts
must be copied separately for a handoff to another machine. No corpus source was
modified outside disposable copies.

## Correction to the previous review

The previous Task implementation generated one Task Plan per request, rather
than one plan per child task. Its separate definition and editing paths caused a
real regression. The 144-attempt campaign finished with old Quick at 41/72 and
Task at 22/72 behavior passes, but Task had only **17/72 completed workflow
passes**. For small edits specifically, the earlier campaign was old 30/36 versus
Task 18/36 behavior passes and 15/36 completed workflows.

The quoted **12.66 seconds** was old Quick's median through coding and external
acceptance among its 41 successes. Its median review time was **3.40 seconds**;
through coding/apply it was **12.02 seconds**. Feature conclusions remain deferred.
