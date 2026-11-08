# Devstill: a minimal coding runtime with shared TUI and headless interfaces

Status: implementation plan, not an implemented runtime.
Inspected source revision: `d2f25ff` (`re-build`).
Scope of this change: documentation only. No new live benchmarks are required to write this plan.

## 1. Recommendation and decision

**Replace the coding workflow and execution core inside this repository. Keep the
repository intelligence foundation. Do not start a second project or rewrite all
of Devstill.** This is more substantial than a cosmetic cleanup, but considerably
smaller and less risky than a ground-up rewrite.

The retrieval foundation already does useful work: local embeddings, structural
parsing, incremental indexing, exact/lexical/semantic retrieval, exclusions, source
hashes, configuration discovery, and model/device setup. The current TUI also
already retains a warm embedding model. None of the recent failures establishes
that these components need replacement.

The part to replace is the route from a user request to a checked patch. It has
accumulated file-impact plans, task wrappers, a separate structured executor,
feature coordination, scoped Pi tools, and UI-specific orchestration. A different
executor hidden under all those wrappers would retain much of the complexity.

Build one small runtime whose normal operation is:

```text
request -> local evidence -> structured edits -> local checks
                                      |             |
                                      +-- one bounded follow-up
                                                    |
                                    saved patch + validation result
                                                    |
                             Review: approve   /   YOLO: apply
```

The model should spend its first generation on the requested change, not on a
mandatory plan about how another generation should make the change. Code owns run
IDs, file hashes, execution state, persistence, deadlines, and approval state.

Call the user-facing work item a **Task**. Internally, a **Run** is one attempt to
fulfill a Task, and a **Proposal** is the resulting patch awaiting application.
Avoid introducing units, blocks, nodes, or another task-plan vocabulary now.

### Decision comparison

| Option | Benefit | Main cost | Decision |
| --- | --- | --- | --- |
| Patch the current Pi policy and keep all workflow layers | Small immediate diff | Retains planning calls, parallel implementations, and UI coupling | Keep as historical baseline, not the target architecture |
| Rebuild every subsystem in a new repo | Clean naming and layout | Recreates working indexing, parsers, installation and configuration; makes regressions harder to attribute | Reject |
| Replace execution and orchestration, retain retrieval and interface foundations | Removes the measured failure mechanisms while preserving useful code | Requires a controlled transition and real deletion of legacy paths | Recommended |

## 2. What the evidence does and does not establish

The [diagnostic report](benchmarks/retrieval/QUICK_DIAGNOSTIC_RESULTS.md) is the
source of record. Preserve its raw runs and frozen source.

| Experiment | Pi | Candidate | Interpretation |
| --- | --- | --- | --- |
| Normal Pi versus Pi + context, four cases × three repetitions | 11/12 completed; 12/12 correct patches | 12/12 completed and correct | Context avoided one timeout; median paired successful wall time was 10.5% slower |
| Reasoning-disabled three-way smoke | Pi 4/4 | Pi + context 4/4; minimal v1 1/4 completed | Unrestricted native Pi remained effective; the first custom editor had real protocol/repair failures |
| Revised minimal editor versus fresh Pi controls | 4/4 | 4/4 | Revised editor faster on all four draws; median paired wall ratio 0.806 |

The last comparison had arm median wall times of 11.72 s for Pi and 8.72 s for the
editor, including worker startup/retrieval setup; model-call totals were 28 and 5.
Those are four exposed Python edits with one draw each. They do not establish
multi-language support, reliable feature completion, production safety, or
statistically established general superiority. Retain the failed v1 result too.

The old Quick traces show actionable failures:

1. Selected source excerpts are stored on the task but not passed into the Pi
   execution prompt; the executor gets the plan.
2. File-prefix reads consume the cumulative read budget before reaching the target.
3. Whole-file writes can replace unseen content. One iterator edit removed
   `wrap_file`; an age edit changed the wrong function and truncated the changelog.
4. Completion contracts are not the same as behavioral correctness.

The new design must directly address these four problems. Replacing Chroma,
rewriting parsers, adding a graph database, or introducing a feature planner would
not address them first.

## 3. Current code map: retain, extract, replace, retire

The inspected production tree contains 53 Python/TypeScript files and about
19,267 physical lines. This count includes comments and blanks and is not an effort
estimate. Five CLI/TUI modules account for about 6,234 lines; the selected workflow
modules account for about 5,080. Much of the breadth is useful infrastructure.

| Existing area | Source | Treatment | Why / specific work |
| --- | --- | --- | --- |
| Parsers and source records | `src/devstill/parsers/`, `models.py` | Retain | Already supports structural chunks and bounded fallback across languages |
| SQLite/FTS and vector storage | `storage/` | Retain | Preserve corpus/index compatibility; no storage migration in the first runtime release |
| Incremental indexing | `indexer.py` | Retain; add a narrow changed-file refresh entry point if needed | Do not rebuild/re-embed the repo after every edit |
| Hybrid search | `search.py`, `retrieval_policy.py` | Retain baseline ranking | Keep ranking changes separate from executor changes |
| Context selection | `context.py`, `planning.py:select_planning_context` | Extract a general packer | Current selector budgets against planning messages, not the final editor payload |
| Embedding lifecycle | `embeddings.py`, `tui_commands.py:TuiRetrievalRuntime` | Retain and move lifecycle ownership below UI | Warm model reuse already exists; expose it to headless sessions too |
| Discovery and filtering | `git_files.py`, `repository.py`, `paths.py`, `content_policy.py` | Retain | Reuse repository boundary and exclusion behavior |
| Model transport | `agent.py`, `credentials.py` | Reuse useful transport/credential pieces | Remove planning assumptions; make exact schema, deadline and reasoning settings explicit |
| Configuration | `config.py` | Extend compatibly, then simplify | Current executor accepts only `pi`; preserve existing configs during transition |
| Old Quick wrapper | `quick_tasks.py`, `task_runs.py` | Replace on the new task route | Do not wrap the new editor in `TaskPlan` or call `generate_plan` first |
| Old structured task editor | `task_execution.py` | Mine tested byte-preservation/patch ideas; retire route | Do not maintain two new minimal executors |
| Plan/Pi executor | `executor.py`, `pi_runtime.py`, `pi/devstill_policy.ts` | Keep frozen/legacy while migrating | Extract only independently useful patch/hash helpers; new runtime must not import the Pi policy |
| Feature workflow | `feature_*`, `deep_planning.py`, task dependency orchestration | Freeze; exclude from first release | Later composition must build on a reliable Task API |
| CLI | `cli.py` | Keep discovery/maintenance commands; replace task adapter | Current `task_command` constructs `TuiWorkflow`, which is the wrong dependency direction |
| TUI | `tui.py`, `tui_commands.py` | Keep Textual shell, search, composer, history; replace task flow | Remove file-impact plan editing from the normal Task experience |
| Installer/device tooling | `setup.sh`, `installation.py`, `devices.py`, update scripts | Retain; remove mandatory Pi installation at cutover | Minimal operation should not require Node/Pi |
| Minimal benchmark prototype | `benchmarks/retrieval/minimal_editor.py` | Freeze as v2 reference; extract deliberately | Good algorithm seed, not a production library |
| Benchmark/evaluator | `pi_comparison.py`, `recording_proxy.py`, `coding_campaign.py` | Retain and adapt to production runtime | Compare actual shipped behavior, not a stronger benchmark-only editor |

Do not move all retained files just to make the new directory tree look tidy.
Behavioral replacement comes first; deletion and small relocations follow.

## 4. Product contract and non-goals

### Required first release

- A single Task engine callable from TUI, command line, or a persistent headless session.
- Local hybrid retrieval and direct delivery of selected source to the editor.
- Structured edits, exact application, explicit checks, and bounded recovery.
- Review and YOLO share the same coding algorithm and artifacts.
- Review shows an actual patch and evidence, not a guessed file-impact plan.
- YOLO skips human review but retains source freshness, patch, and validation checks.
- Target one generation for the normal path; permit at most one follow-up generation.
- Cancellation, saved review/apply, useful failure messages, and honest validation status.
- Independent Pi comparison remains available in benchmark tooling.

### Explicitly outside the first release

- Automatic feature decomposition, task graphs, multi-agent orchestration and parallel writes.
- General autonomous shell agent behavior, unrestricted exploratory loops, or fallback to Pi.
- A background network service, plugin platform, web UI, vector-store replacement, or full code graph.
- Broad file deletion/rename operations, binary edits, symlink targets and arbitrary encodings.
- A claim of equal quality across every parser-supported language.
- Reimplementing package installation or dependency provisioning inside a task.

The first quality-supported coding target is ordinary UTF-8 Python text edits,
including small new files and tightly related multi-file changes. Existing search
can continue supporting other languages. Expand coding support through measured
language/check adapters, not by silently treating parse support as coding quality.

## 5. Target code structure and dependency direction

Start with a small package; do not build an abstract agent framework.

```text
src/devstill/runtime/
    __init__.py       # small public entry points
    contracts.py     # RunRequest, Evidence, EditResponse, Proposal, RunResult, Event
    session.py       # warm retrieval + HTTP client lifecycle, repo/config identity
    service.py       # prepare/apply/cancel; bounded orchestration
    edits.py         # exact replacement/create, byte preservation, patch construction
    workspace.py     # task checkout, source manifests, live-tree application
    checks.py        # configured/generated check execution, deadlines, process cleanup
    artifacts.py     # versioned proposals/results, append-only events

src/devstill/cli.py   # parse arguments -> runtime -> text/JSON adapter
src/devstill/tui.py   # render events/proposals; invoke the same runtime

benchmarks/retrieval/
    pi_comparison.py # native Pi arm and runtime candidate adapter
    ...              # frozen references and independent acceptance
```

Merge tiny files if that makes ownership clearer. Do not create one class per
operation or generic backend registries. Reuse the current model client after
extracting a bounded structured-generation operation, or add one focused client
module if that extraction would alter legacy behavior.

Dependency rules:

1. Runtime can import retrieval, config, filesystem and model-transport code.
2. Runtime cannot import `TuiWorkflow`, Textual, benchmark modules, `TaskPlan`,
   feature coordination or Pi extensions.
3. TUI and CLI cannot implement separate generation prompts, edit application,
   retry policies or success definitions.
4. Benchmarks call the public runtime; they do not patch its private functions to
   produce stronger behavior than the app.
5. Pi is a benchmark/temporary legacy dependency, not a runtime dependency.

Suggested public API, names provisional:

```python
with RuntimeSession(repo, config) as session:
    proposal = prepare_task(session, request, on_event=emit, cancel=token)
    # Review returns here. YOLO invokes this next operation immediately.
    result = apply_proposal(session, proposal.id, expected_digest=proposal.digest)
```

No generation occurs in `apply_proposal`. Reviewing and later applying the same
proposal must not ask the model to produce a new patch.

## 6. Data contracts: keep model output small

Distinguish model-facing response data from application-owned bookkeeping.

| Contract | Fields that matter | Owner |
| --- | --- | --- |
| RunRequest | request text, repository, review/yolo mode, effective budget/profile, optional user path hints | Application/user |
| Evidence | path, source hash, symbol, exact source, line range, complete/partial flag, retrieval provenance | Retrieval/runtime |
| EditResponse | edits, optional bounded reads, checks | Model; schema validated locally |
| Proposal | run ID, original request, base/source manifests, exact patch, resulting hashes, checks, state, digest | Runtime |
| RunResult | application status, validation status, changed files, timings, calls, reasons, artifact locations | Runtime |
| Event | version, run ID, increasing sequence, phase/type, elapsed time, small payload | Runtime |

Keep the proven `edits`, `reads`, `checks` response shape initially. Do not ask the
model for run IDs, a dependency DAG, task ownership, hashes, file-impact labels,
completion assertions, confidence scores or a second human-readable plan.

For edit operations, preserve the v2 replacement semantics while hardening the
implementation: nonempty `old` means an exact unique replacement; empty `old`
means create a nonexistent file. Treat deletion/rename as unsupported initially.

Use JSON Schema at the provider boundary **and** strict local Pydantic validation.
The prototype requests a schema but then parses a dictionary; server compliance
must not be the only validator. Enforce types, bounds and extra-field rejection
locally. A fenced/prose response is not permission to run extra uncounted calls.

Paths, expected hashes and approval identity are always generated/verified by code.
Treat repository text and model-provided snippets as data, not operational policy.

## 7. Bounded task algorithm

### Prepare

1. Resolve the repo and config; validate the request and supported operating mode.
2. Open or reuse a session. Allocate a run ID and durable initial event.
3. Capture source/workspace identity and effective limits. Honor existing user changes.
4. Retrieve and pack fresh evidence. Save the exact context sent to the model.
5. Send one structured edit request with the original user requirement intact.
6. Validate response structure and path/operation constraints locally.
7. Perform allowed requested reads; apply valid edits to the isolated task workspace.
8. Run syntax checks and selected behavioral/project checks under the remaining deadline.
9. If needed, issue one follow-up with precise read/error/check output and current
   relevant source. Permit checks-only repair when the patch is already correct.
10. Revalidate edits and checks; save the exact resulting proposal or terminal failure.
11. Review returns the proposal. YOLO immediately requests the same apply operation.

### The follow-up is a shared allowance

The second generation can resolve insufficient evidence, an invalid response,
an exact-match failure or a failing check. Those are not four independent retry
budgets. A read-only first response consumes the first generation. If the second
response still needs another model turn, finish as incomplete with its reason.

Keep useful reads and edits in the same response legal, as v2 does. Execute reads
against the pre-edit workspace state, then validated edits, then checks. If another
generation is needed, return explicitly versioned current source as well as the
read/check results so old snippets cannot masquerade as post-edit source.

Only promote a successful proposal if structural validation and selected required
checks pass. If checks are unavailable, preserve a distinct unverified proposal;
do not label it behaviorally validated. A failed required check is not a YOLO success.
An empty patch is not automatic success; report `no_changes` with a reason and
verification level, and score benchmark correctness independently.

### Initial limits

These are starting values, not performance claims. Freeze them with each candidate.

| Limit | Starting policy |
| --- | --- |
| Generation requests | 2 maximum, all provider attempts visible |
| Full retrieval passes | 1 initial, at most 1 explicitly recorded refresh/expansion |
| Supplemental source reads | Up to 4 ranges of at most 400 lines, still token-budgeted |
| Edit operations per response | Up to 20; reject oversized batches clearly |
| Generated checks | Up to 3 when enabled; project checks separately identified |
| Output reservation | 4,096 tokens initially |
| Total active task deadline | 120 s initially, spanning generation/read/check work |
| Individual check deadline | At most 30 s and never beyond remaining task time |
| Concurrent live-tree writers | 1 per repository |

No hidden model-backed planning, summary, format-repair or validation call may sit
outside the two-generation accounting. Tokenization is measured separately as an
HTTP request and wall-time cost, not misreported as a generation.

Human review time does not consume the active generation deadline. Saved proposal
apply has its own bounded local operation and freshness verification. Cancellation
must propagate through HTTP, retrieval checkpoints and child process groups.

## 8. Retrieval: preserve it, fix its handoff and lifecycle

### Baseline behavior first

Start from the v2 diagnostic's existing hybrid candidate pool and selection rules.
Keep ranking weights, candidate count and embedding model fixed during runtime
extraction. Otherwise a failed port cannot be distinguished from a retrieval change.

Extract adaptive packing from `select_planning_context` without invoking
`generate_plan`. Budget the **actual final editor messages and schema** with output
reservation and margin, including a potential repair prompt. Do not budget against
the old file-impact planning prompt. Repack oversized repair feedback; never
silently grow a two-turn transcript beyond the server context window.

The selected evidence passed to the editor must include exact code, paths, hashes
and line ranges. Save those same records in the proposal. Do not transform them
into path-only reasons at an execution boundary.

### Freshness and edited repositories

- Validate selected source hashes against the task's snapshot before generation.
- If a selected file changed, read/reparse its current content and refresh its
  local evidence within the allowed retrieval work; do not send stale text.
- Include a bounded overlay for eligible dirty/untracked files so a new module or
  renamed target is not invisible merely because the persistent index is older.
- Do not assume checking selected files detects every newly relevant file. Track
  repository/index generation and dirty paths; report when broad freshness cannot
  be established within the budget.
- After apply, refresh changed paths incrementally or mark them dirty immediately.
  Invalidate cached symbol choices and query/context caches for affected versions.
- The next Task must see the updated repository, including files created by the
  previous Task. This is a required test, not an eventual-consistency aspiration.

Avoid copying the entire index per production task. The benchmark copy exists to
isolate trials. Production should reuse a repository index with appropriate writer
locking/generation checks; no evidence currently justifies replacing the stores.

### Warm sessions without a daemon

Move the lifecycle concept in `TuiRetrievalRuntime` into `RuntimeSession`:

- Reuse one encoder and suitable storage handles for a live TUI or headless session.
- Reuse the HTTP client, but do not retain unrelated task messages as model context.
- Key reuse by repo identity, index generation, model/device and relevant config.
- On repo/config changes, close or invalidate the affected handles deliberately.
- Give SQLite handles correct thread ownership; keep retrieval in a session-owned
  worker or reopen thread-bound handles rather than sharing connections blindly.
- A one-shot CLI process is honestly cold. A persistent stdio session is honestly
  warm after its first task. Report both; do not subtract cold cost and call it wall time.

Only after executor parity, test an exact-path/symbol fast route separately. It
may avoid semantic startup for explicit references, but must preserve sufficient
behavior/test context and fall back to normal hybrid retrieval when inconclusive.
Do not add a model classifier to decide whether retrieval should run.

## 9. Workspace and patch application

The prototype applies files directly inside a disposable benchmark clone. That is
not enough for a live user's repository. Preserve its small edit algorithm while
separating proposal construction from live-tree application.

### Task workspace

Use an isolated workspace containing sufficient project source/configuration for
imports and checks, not the old executor's selected-files-only copy. Initial
implementation may favor a correct copy/snapshot over a clever filesystem scheme.
Measure its cost before optimizing it. A snapshot is not a security sandbox.

For Git repos, capture the actual working tree, including permitted staged,
unstaged and relevant untracked source. A bare worktree at HEAD is insufficient.
Do not stash, reset, commit or modify the user's Git index as part of a Task.
Ignored build environments remain external dependencies under an explicit check
profile; do not accidentally copy `.venv`, secrets or huge build caches by default.
Unsupported submodule/LFS/sparse-checkout cases must be reported rather than tested
against a silently incomplete snapshot. Non-Git editing can be a later capability;
read-only repository search need not be removed.

### Replacement guarantees

1. Preserve raw bytes/newline style/mode for unchanged content. Avoid `read_text`
   newline normalization followed by whole-file `write_text` changes.
2. Reject absolute paths, traversal, escaping/internal symlink edits, `.git`, run
   artifacts, and configured excluded/sensitive targets.
3. Require each replacement anchor exactly once in the current staged file version.
4. Apply multiple edits in documented list order to an in-memory staged map.
5. Validate the full batch and syntax before publishing it in the task workspace.
6. A create must remain a create; an existing file cannot be overwritten via `old=""`.
7. Do not fall back to fuzzy replacement or whole-file reconstruction on mismatch.
8. Return a precise mismatch and relevant current source within the repair allowance.

### Live application

- Persist immutable proposal bytes/digest, source hashes, new-path absence checks
  and relevant validation inputs before presenting Review.
- Acquire the repository writer lock; recheck source and validation dependencies.
  If tests/config/dependencies used by validation changed, do not reuse a stale green result.
- Verify repository identity and proposal digest; applying twice returns the
  recorded result or a clear already-applied response without a second mutation.
- Check the complete patch against the live tree, then apply only that patch.
  Preserve unrelated user changes and Git staging state.
- Keep a write-ahead application journal with before/after hashes and necessary
  backups. Multi-file filesystem updates are not intrinsically atomic. On failure
  or crash, detect partial application and recover without overwriting later user edits.
- Cooperating writers use the lock; external editors do not. Recheck targets as
  late as practical, detect conflicts, and never force-apply or silently rebase.
- If post-apply checks are configured and fail, report `applied_validation_failed`
  and keep a reversible patch. Do not reset the whole working tree automatically.

Reuse proven hash/path/patch helpers from `executor.py` only after removing their
`PlanOutput` dependency. Do not import the full legacy executor into the new core.

## 10. Validation that does not recreate an agent loop

Use three distinct levels and expose them accurately:

| Level | What it establishes | What it does not establish |
| --- | --- | --- |
| Edit integrity and syntax | Exact edits, valid source structure, no unintended file writes | Requested behavior works |
| Selected project/generated checks | Those recorded checks passed on that workspace | All original requirements are fulfilled |
| Independent benchmark acceptance | Evaluator's behavioral and regression conditions passed | Universal feature correctness |

Project-defined check profiles should be the stable production mechanism. A
profile contains argv, cwd relative to the task workspace, environment policy,
timeout and selection rules. No shell interpolation is needed. A simple Python
profile can select relevant pytest targets; other language profiles come later.

Retain model-proposed assertions as supplemental checks when enabled, because
they contributed to the diagnostic behavior. They are ordinary executable code,
not a sandboxed DSL. A project/session policy decides whether they may execute;
Review and YOLO use that same policy. Do not introduce a per-task permissions maze.
For production defaults, use configured trusted checks and display disabled or
unavailable generated checks honestly. Measure this policy change separately
from the original prototype configuration.

Run checks in the task workspace. Track all changes they make, including formatting
or generated files; do not silently add those changes to the user's approved patch.
Capture bounded output, exit status and duration. Kill the process group on timeout
or cancellation, not just its parent. Avoid model-directed package installs.

When a check fails, the model gets concise failure output and current source in
its one follow-up. It can repair code or only fix a bad check. Do not send hidden
acceptance output back into the loop. An unavailable check environment is a named
environment failure, not an implicit check success.

Review can show an unverified proposal. YOLO may apply one only according to the
explicit configured unverified-change policy; it must then say `applied_unverified`.
Required checks that actually failed block normal application in either mode.

## 11. State, artifacts, cancellation and recovery

Use simple versioned JSON artifacts and JSONL events first; a second state database
or queue service is unnecessary.

```text
.devstill/runs/<run-id>/
    request.json       # original request, effective config, version identities
    evidence.json      # exact source context + hashes/ranges
    events.jsonl       # ordered lifecycle events
    proposal.json      # patch digest, source manifests, validation level, status
    change.patch
    checks.json
    result.json
    apply-journal.json # created only for live application
```

Model transport diagnostics are optional bounded artifacts; never store credential
headers. Source excerpts are repository data: keep local, honor exclusions and
provide explicit artifact retention/cleanup. Never delete pending review/recovery
artifacts as ordinary cache cleanup.

Core states: `preparing`, `generating`, `checking`, `repairing`, `ready`,
`ready_unverified`, `applying`, `applied`, `applied_unverified`,
`applied_validation_failed`, `failed`, `incomplete`, `cancelled`, `conflict`,
`recovery_required`. Store validation status separately from application status.

Terminal preparation failure never modifies the live tree. Cancellation before
apply discards only temporary work; cancellation during apply must finish or recover
the local transaction before reporting a final state. A process killed during
apply is detected on next open through the journal. No silent resume of a model
loop after a crash. A saved proposal may be inspected/applied after restart if its
identity and source/validation prerequisites still match.

Emit progress events such as `context_ready`, `generation_started`,
`edits_staged`, `check_finished`, `repair_started`, `proposal_ready`, `applied`.
Adapters render these; events never trigger a hidden second implementation.

## 12. Review and YOLO user experience

**One Task UI, one engine; the toggle controls application, not reasoning strategy.**

### Review

1. User enters a request with Review selected.
2. Show retrieval progress and a compact context card: files/excerpts actually sent,
   with an expandable source view. No mandatory separate plan generation.
3. Produce the candidate in isolation and run allowed checks.
4. Show the actual diff, changed files, validation results, and any limitation.
5. Offer Apply, Discard, or Revise. Apply uses the saved patch without another
   generation. Revise is a new run with an explicit new budget, linked to the old one.

This deliberately replaces the current pre-code file-impact review. It is a product
semantic change and must be described in release notes and command help. If users
want to constrain paths before coding, accept explicit path hints/constraints on
the request; do not recreate an LLM-authored plan screen for every small edit.

### YOLO

The same prepare flow executes, then applies a ready proposal automatically under
the configured check policy. Show a concise result: changed files, checks,
elapsed time and diff access. Keep cancel available. YOLO does not mean stale
source is acceptable or failed checks disappear.

### TUI implementation

Keep Textual, the composer, session history, search/context tools, model setup and
repository status. Replace the normal Task path's `PlanImpactRow`/file-label
editing and Pi preview handoffs with a compact proposal entry.

Show distinct notions of “included in model context” and “changed”; retrieval may
read/hash more files locally than it sends to the model. Do not imply that every
retrieved path will be modified. Expanding details must not generate another model
call. Leave backend/framework bookkeeping out of ordinary user-facing text.

Reuse one runtime session while the app is open. Background workers marshal events
to Textual safely; app shutdown cancels active work before releasing resources.
Do not retain legacy feature controls in the main composer after cutover.

## 13. Headless interface contract

The following commands are **proposed**, not currently implemented:

```bash
devstill task "Reject bool ages" --review --json
devstill task "Reject bool ages" --yolo --json
devstill runs show RUN_ID --json
devstill runs apply RUN_ID --digest PROPOSAL_DIGEST --json
devstill runs discard RUN_ID
devstill session --stdio
```

Retain existing search/index/context/doctor commands. Preserve existing `task`
syntax through a documented compatibility adapter where practical; explicitly
document the change from a file-impact review to a patch review.

Headless rules:

- `--review --json` prepares and saves a proposal; it never waits for stdin approval.
- `--yolo --json` prepares and applies through the same service used by TUI.
- `runs apply` is explicit authorization for that stored digest, with no model call.
- Standard output contains only the declared JSON result, or JSONL events when
  explicitly requested; progress/diagnostics go to stderr.
- Exit statuses distinguish success/ready, task/check failure, source conflict,
  invalid configuration/environment, cancellation and recovery required.
  Document exact numbers when implementing; use 130 for user interruption.
- Unverified results are machine-readable, not disguised as validated results;
  callers can require verification through an explicit flag/policy.
- Stdio session accepts versioned JSONL requests with client IDs and emits events
  plus one terminal response per request. It reuses resources, not task chat history.
- First version executes one task at a time; define busy/cancel behavior. Do not
  invent a hidden distributed queue. Closing the stream cancels active work safely.

Implement one-shot CLI first and stdio persistence after the shared session is
proven. Do not make a daemon, socket or extra service necessary for a normal user.

## 14. Configuration and installation transition

Add a focused `editing` config section during migration rather than overloading
Pi-specific `executor.max_tool_calls` with a different meaning. Suggested fields:
enabled, max generation calls, active timeout, output tokens, reasoning policy,
context budget, check profile, generated-check policy, unverified-apply policy.
Use the existing `llm` endpoint/model/credential settings and embedding/index config.

Existing configs must load unchanged. Preserve the legacy `executor` section while
the explicit legacy route exists; do not silently reinterpret its shell-string
commands as safe argv. Convert commands only through a documented migration with
before/after examples; do not use naïve whitespace splitting.

Record effective settings with every run. Model capabilities must be explicit:
JSON-schema support, context window, reasoning control and token-count availability.
Cache capability checks; do not add a model probe before every Task. If a required
capability is absent, fail clearly or select a separately tested compatibility
profile. No uncounted schema retries or silent reasoning changes.

Keep the existing private Python installation and CUDA/model provisioning.
At default cutover, stop installing or requiring Pi/Node for normal editing.
Retain an explicit developer/benchmark option to install or locate Pi. Update
doctor to distinguish minimal-runtime readiness from benchmark Pi availability.
Never uninstall a user's independent Pi/Node installation as cleanup.

Index paths and embeddings should remain compatible. A config/runtime transition
must not trigger a full reindex unless source/parser/index schema actually requires it.

## 15. Benchmark design: short batches, real product code

### Arms and evidence separation

Primary arms are native Pi and the production minimal runtime. Keep the frozen
minimal v2 prototype as an extraction reference. Keep Pi + context available as a
diagnostic arm, but do not rerun it by default once its question is answered.
The old Quick baseline remains historical; rerun it only for a specific regression
question. Do not multiply every small test by four or five arms indefinitely.

Use the same model endpoint/revision, repository commit, prompt, toolchain,
reasoning setting and common outer deadline within each comparison. Record
sampling parameters and cache conditions; matching model names alone is insufficient.
Native Pi keeps its normal tools. The editor keeps its bounded loop. Their
different call budgets are the product comparison, not an equal-token experiment.

Explicitly disable reasoning for the fast comparison and verify captured response
reasoning fields. Record original Pi requests and the benchmark override. Run
normal-Pi behavior as a separate labeled operating point when useful; never pool it
with reasoning-disabled timings.

The candidate must use the public runtime and the same validation/apply policies
as the tested product configuration. No benchmark-only path may bypass expensive
workspace/review checks while claiming product latency. Also retain phase timings
to identify where product hardening adds cost.

### Small-set progression

| Gate | Work | Expansion rule |
| --- | --- | --- |
| A: extraction integrity | Offline replay of saved responses and functional tests | No live model calls needed to verify patch/state parity |
| B: known-case stability | Four current diagnostic cases × three repetitions × two arms = 24 attempts, split into three eight-attempt batches | Freeze once; retain all draws; diagnose any candidate-only failure before expanding |
| C: fresh edit check | Eight previously unused prompts × one repetition × two arms = 16 attempts, split into small batches | Case definitions/reference verification frozen before candidate execution; do not tune and call them held out |
| D: repeat fresh evidence | Repeat the unchanged candidate on those cases only if Gate C is promising | Report variance and paired outcomes; any revised prompt/runtime gets a new candidate version |
| E: broader validation | Additional repos/languages/features | Only after the preceding gates; no automatic hour-long campaign |

Eight fresh cases should cover: an explicit path/symbol edit, natural-language
lookup, a large-file target, an ambiguous duplicate symbol, preservation of adjacent
behavior, a stale indexed file, an eligible new/dirty file, and a tightly related
two-file edit. Validate the dirty/untracked fixtures as carefully as clean commits.
Use a second pinned repository only after preparing its ordinary tools and a
small executable acceptance check. Repository size alone does not make a good case.

A fresh test's base must fail its new acceptance, the reference patch must pass,
and existing regression tests must pass before live trials. Hide reference edits
and acceptance code from both arms; preserve original regressions so weakening
tests cannot win. Evaluate incorrect/timeout patches too, as already implemented.

### Runtime cap and stop rules

Each invocation has an explicit small attempt count and a proposed six-minute
campaign wall budget, including setup and acceptance. This is a stop budget, not a
promise that eight worst-case attempts fit. The controller reserves time for
cleanup; when the budget cannot accommodate another attempt it saves a partial
report and starts no more work. If an active attempt reaches the budget, terminate
its process group, mark the campaign-budget interruption distinctly, and preserve it.

Keep the 180-second outer trial cap for direct comparability with current evidence
initially; any tighter common deadline creates a separately labeled experiment.
Continuation of a partial batch must use the same frozen candidate and preserve
completed/failed attempts. Never quietly drop slow draws or replace failures.

No overlapping campaigns against the same inference server. Check shared benchmark
locks and running state before each live batch. A concrete failure analysis should
precede another batch; do not use repeated stochastic retries as a fix.

### Report the right numbers

- Completed-and-correct workflows and correct patches separately.
- Candidate-only failures and Pi-only failures by case/repetition.
- Full task wall time, including snapshot, retrieval, checks and apply; proposal
  time and active coding time separately. Human review delay separately.
- Cold first task and warm persistent-session tasks separately for both products.
  Reset each task's model conversation/repository; do not give either arm answer history.
- Actual model calls, transport retries, token usage when available, evidence size,
  repair rate, read rate, peak memory where practical, and output reasoning presence.
- Paired successful time ratios, all-attempt cost including failures, and per-case
  regressions. Do not compare only the fastest successful subset.
- Environment, schema/prompt/runtime hashes, tool versions, case/reference hashes,
  index identity and whether the suite has been used for tuning.

### Promotion criteria

First require no integrity failures and no lower observed completed correctness
than Pi in each tested request category. Every candidate-only failure gets a
written disposition; aggregate wins must not hide a repeatable regression.
For a development promotion, require a median paired wall ratio below 1 on the
fresh set and no unexplained failure-inclusive cost increase; use 0.90 as the
desired practical margin, not a retrospectively adjusted target.

Tiny samples cannot prove non-inferiority. Report paired uncertainty as the data
grows, and distinguish “passes the development gate” from a general claim of being
better than Pi. Do not increase speed by dropping the checks that establish quality.

## 16. Review/YOLO and interface parity tests

The model request, evidence and candidate patch must be the same for the same
recorded response in Review and YOLO. The only normal difference is when apply
runs. Use deterministic replay to test this without doubling every live campaign.

Required integration scenarios:

| Scenario | Required result |
| --- | --- |
| Review prepare | Live tree and Git index unchanged; persisted patch inspectable |
| Review restart/apply | Same patch/digest applied without a generation call |
| YOLO | Same preparation/check algorithm; one automatic apply |
| Apply twice | No duplicate mutation or checks falsely attributed to a second edit |
| User edits a target after preview | Conflict; user edit preserved |
| Unrelated user edit | Preserved; application can proceed if validation remains valid |
| Validation dependency changed | Previously green validation invalidated/rechecked |
| Cancellation during generation/check | Child work stops; live tree unchanged |
| Interrupted multi-file apply | Detected journal; recoverable state, no silent partial success |
| Invalid second model response | Incomplete/failed; no third generation |
| Correct patch, malformed model check | One checks-only repair possible |
| Missing validator | Explicit unverified/environment status, never fabricated pass |
| No Pi installed | Minimal CLI and TUI still operate |
| Consecutive Tasks | Second retrieval sees the first Task's applied files |
| CLI versus TUI | Same runtime events/result/patch for replayed model responses |

Add a small live Review smoke at a milestone to catch real integration problems;
do not assume replay alone measures model quality. Benchmark Review auto-approval
must be disclosed and must use the persisted/reloaded proposal path.

## 17. Implementation sequence and handoff packages

Execute these in order. Each package should leave a reviewable, working boundary.
The package labels identify work, not mandatory separate agents or a new framework.

### Package 0 — Freeze evidence and stop legacy growth

Deliverables: immutable minimal-v2 source/prompt/protocol reference; Pi identity;
current diagnostic fixtures and results; an inventory of public task commands.
Keep old feature/Quick code stable except for regressions unrelated to this work.

Acceptance: existing evidence can be read/reported offline; no historical run is
overwritten; the default product behavior has not changed. Do not rerun a broad suite.

### Package 1 — Extract a benchmark-equivalent minimal engine

Touch: new runtime contracts/edits/service transport seam; focused tests;
benchmark candidate adapter. Avoid UI/config migration in this package.

Move algorithmic behavior from the prototype into importable code without a new
planning call. First preserve v2 prompt/schema/budget semantics so extraction can
be diagnosed. Ensure local response validation, record any resulting behavior
change, and keep the frozen prototype separately addressable.

Acceptance: offline response replays reproduce expected patches and failure
classes; production modules never import benchmarks. Exact replacement, range
reads, mixed read/edit, checks-only repair and two-generation limits are tested.
Run only a four-case comparison if offline evidence supports it.

### Package 2 — Decouple evidence packing and retain warm sessions

Touch: runtime session, `context.py`, narrow extraction from `planning.py`,
TUI retrieval-lifecycle seam, retrieval tests.

Use actual editor messages for budgeting; preserve baseline ranking. Provide a
runtime evidence API with freshness metadata. Move warm ownership below TUI while
keeping legacy adapters working. Implement/cache invalidation and dirty-path
overlay behavior sufficient for consecutive tasks.

Acceptance: exact source reaches generation; large-file target is available
without paging; exclusions remain enforced; cold/warm timings are separately
measured; stale source never silently becomes an edit anchor.

### Package 3 — Safe proposals, checks and live application

Touch: workspace/checks/artifacts modules and focused filesystem/process tests.

Construct isolated full-enough workspaces, preserve user changes and line endings,
persist proposals, execute configured checks, implement cancellation and journaled
application. This is the main production-hardening package; do not pretend the
benchmark prototype already provides these guarantees.

Acceptance: the integration scenarios in sections 9–11 and 16 pass, including
injected write failures and stale review. No Pi or Plan types are imported. Any
latency added by snapshot/validation is visible in the next diagnostic.

### Package 4 — Headless vertical slice

Touch: CLI task dispatch, new runtime configuration and proposal commands.

Expose an explicitly opt-in minimal task route first. TUI-independent CLI can
prepare, inspect and apply a saved proposal. Add JSON output/exit contracts and
SIGINT handling. Old task artifacts remain readable through the explicit legacy
route; never reinterpret their IDs as new proposals.

Acceptance: use from a subprocess without a TTY or Pi installation, restart before
apply, and verify no UI import is needed. Review and YOLO replay the same editor
payload. Run Gate B in short frozen batches using the actual runtime.

### Package 5 — TUI Task flow and persistent headless session

Touch: TUI task adapters/widgets and stdio adapter, not the editor algorithm.

Wire both to the same retained runtime session. Replace plan-label editing on the
new route with actual evidence/patch/check views. Implement clean cancellation,
shutdown and proposal actions. Keep search/setup/history behavior working.

Acceptance: TUI/CLI parity tests, restartable reviews, no model call on expansion
or apply, a real warm sequential-task session, and a small live review smoke.
No daemon or new frontend framework is introduced.

### Package 6 — Fresh-case quality and performance gate

Touch: verified new fixtures, benchmark runner budget/reporting, result documents.

Freeze the production candidate; run Gates C/D only in short batches. Compare
cold CLI and warm sessions without changing prompts between arms or pooling those
operating points. Add the independent tests before seeing generated solutions.

Acceptance: promotion criteria from section 15 satisfied or explicitly failed.
If failed, fix the concrete mechanism and issue a new candidate version; do not
expand to feature coordination or declare victory from the original four edits.

### Package 7 — Default cutover and real deletion

Touch: default CLI/TUI routing, help/README/config migration, setup/doctor,
legacy workflow removal and corresponding obsolete tests.

Make minimal the default only after the preceding gate. Remove normal-runtime Pi
requirements and old plan/task/feature imports. Delete dead workflow/policy paths
after verifying external command compatibility and preserving frozen evidence.
Keep benchmark native-Pi support. Old saved reviews can remain inspectable through
a one-time legacy/export path; do not require indefinite dual execution engines.

Acceptance: ordinary Task operation has one code path and one success definition;
the supported full test suite passes; search/index/config/install smoke passes;
no dangling commands advertise removed features. A release note explains Review
semantics and current coding-language limits. No full reindex is required solely
for the runtime switch.

### Package 8 — Optional later growth

Only after reliability: additional validator/language profiles, separately measured
exact-reference retrieval shortcuts, and explicit multi-Task requests. A feature
coordinator consumes the stable Task API; it cannot insert a planning generation
into every standalone Task. Start serial. Independent parallel work requires
separate workspaces and conflict-aware merge/validation, not just disjoint filenames.

## 18. Deletion, rollback and avoiding permanent transition complexity

During development, keep new-runtime selection explicit and defaults stable.
Use the frozen legacy reference and Git history as rollback evidence. Do not add
automatic “minimal failed, silently try Pi” behavior: it conceals failure rate and
destroys bounded-cost claims. A user may explicitly start a different run.

At cutover, audit incoming imports/call sites before deleting:

- Quick-to-Plan wrappers and the separate structured Task executor.
- Feature/deep-plan orchestration from the normal Task route.
- Pi policy/tool scope/complete-plan code from production editing.
- File-impact plan widgets and execution approval handoffs no longer used.
- Config options, help examples, installation requirements and tests that exist
  solely for deleted behavior.

Retain standalone search/context maintenance and any intentionally supported
advisory command only if it has a clear owner and no dependency from normal Tasks.
Do not keep deprecated modules alive merely to make their old tests pass.
Do keep applicable path, mutation, regression and cancellation tests by retargeting
them to the new core. Run the full supported suite at cutover, not every expensive
live benchmark after every local patch.

## 19. Risks and decisions that must remain visible

| Risk | Mitigation / decision point |
| --- | --- |
| Four-case success was sampling luck or overfitting | Repeat frozen candidate, then fresh cases; retain all failures |
| Production checks/workspace costs erase the speed win | Benchmark actual runtime and full latency; optimize measured phase cost |
| Another JSON bookkeeping trap | Minimal response contract, strict schema, application-owned state, bounded repair |
| Retrieval serves stale or incomplete context | Hash/generation checks, dirty overlay, one bounded refresh, explicit failure |
| Correct patch fails a generated check | Checks-only repair; separate model checks from project/evaluator checks |
| A passing check is mistaken for complete behavior | Honest validation levels and hidden independent acceptance |
| Proposed changes overwrite user work | Snapshot identity, writer lock, late conflict checks, patch-only application, recovery journal |
| Warm performance is overstated | Measure real cold and persistent-session modes, including preparation work |
| TUI/headless diverge again | One public runtime API, shared events, deterministic parity tests |
| Feature ambition expands the initial scope | No coordinator until the single-Task gate is met |
| Legacy complexity remains forever | Explicit cutover/deletion package, no automatic backend fallback |

Do not resolve these by adding mandatory model plans, unlimited retries, per-file
model approvals, or another orchestration layer. The product stays simple because
its runtime contracts are precise, not because correctness work is omitted.

## 20. Definition of done

This rebuild is complete when a user can launch Devstill without Pi, make a small
request through either TUI or headless, get a compact source-grounded candidate,
review the actual patch or apply it through YOLO, and receive an accurate result
with preserved user changes and bounded model work.

The implementation has one normal coding engine; no mandatory Task Plan; no
TUI-owned headless orchestration; no hidden retry/fallback loop; no whole-file
rewrite escape hatch for incomplete evidence. Existing indexing/search remain
available and compatible. Short repeated/fresh benchmarks support its advertised
quality and speed, and old unsuccessful experiments remain documented.

**First implementation handoff: Packages 0–1 only, then review the extracted
runtime and offline replay evidence.** Do not implement all packages at once or
start a long campaign as the first step. This document defines the complete route
so subsequent work can proceed without rediscovering the architecture each time.
