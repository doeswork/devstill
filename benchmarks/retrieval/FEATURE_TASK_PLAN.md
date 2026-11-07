# Features composed from Quick tasks

Status: design reference. [Implementation and benchmark handoff](FEATURE_IMPLEMENTATION.md)
records the implemented serial workflow, controlled tests and remaining evaluation. Current Quick baseline: commit
`831b199` and [the restoration comparison](QUICK_RESTORE_RESULTS.md).

The user explicitly chooses **Quick task** or **Feature (multiple tasks)**.
Quick keeps its current path. Feature adds one coordinating plan whose tasks each
run through the working Quick engine. The coordinator defines the work once;
a deterministic scheduler then follows its dependencies.

This supersedes the mandatory task-plan-for-every-request direction in
[ONE_SHOT_STRATEGY.md](ONE_SHOT_STRATEGY.md). The earlier structured-edit engine
and its failed comparison are historical experiments, not the feature executor.

## 1. Product choices and review behavior

Keep request type separate from approval behavior:

| Choice | Behavior |
| --- | --- |
| Quick task + Review | Existing compact file-impact review, then Run task. |
| Quick task + YOLO | Existing Quick retrieval and execution without confirmation. |
| Feature + Review | Discover context, build one feature plan, review its tasks/contracts/write scope, then Run feature. |
| Feature + YOLO | Build the same feature plan and execute within its recorded scope without confirmation. |

Quick task + Review remains the startup default. Add a request-type selector
alongside the existing Review/YOLO/Search selector. Search disables the request
type and remains local. Existing `/task` and `/yolo` explicitly select Quick;
proposed `/feature` explicitly selects Feature Review. Feature YOLO is available
through the two selectors. Proposed CLI: `devstill feature "request" [--yolo]`,
`--json` for a saved review, and `--run ID` for later execution.

There is no automatic request-size classifier or silent promotion from Quick to
Feature. If a user-selected feature fits one coherent Quick task, keep one task
and show that conclusion; do not force a split. Its coordinating call still
counts toward cost. A Quick request that cannot complete can offer an explicit
"Try as feature" action without making another model call until selected.

Feature Review shows the original goal, observable acceptance conditions, shared
interfaces, task outcomes, dependencies, proposed existing/new file writes, and
configured checks. Labels such as **Ready**, **Waiting for T1**, and **Independent
of T2** explain the order. Use Task and Feature in the UI; graph/node are internal
terms.

One **Run feature** action authorizes the recorded scope. Child tasks do not
create additional approval stops during routine execution. Their actual Quick
file-impact reviews, selected source, changes and validation remain inspectable.
The initial feature review labels paths as proposed: the exact child review is
produced when that task is ready against current source. New write paths,
destructive actions or interface changes outside the recorded scope block the
run for an explicit amendment, including in YOLO.

## 2. Preserve Quick as the execution primitive

The feature runner must call `create_quick_draft` and `run_quick_task` from
[`quick_tasks.py`](../../src/devstill/quick_tasks.py). It must not call the
experimental `create_task_draft`/`TaskExecutor` or manufacture a new coding
response format. A child cannot create children or ask a model for the next step.

Each child retains Quick's existing local retrieval, file-impact classifier and
bounded Pi execution. The child request is assembled deterministically from its
outcome, relevant constraints, acceptance conditions and shared contract. Supply
only the relevant predecessor artifacts; do not append the entire parent prompt,
every sibling's source, or previous model conversations.

**Call accounting:** for K coding tasks, expect one feature coordination
generation, K ordinary Quick file-impact classifications, and K bounded Pi
executions. Existing Quick format repairs and Pi tool/repair rounds are counted
as additional calls. There is no extra child decomposition or instruction-writing
generation. Skipping the ordinary classifier would be a separate future
experiment, since it would change the working Quick primitive.

Add a feature-wide execution deadline as well as the task-count cap. Start the
prototype with a configurable 180-second allowance for discovery, coordination,
index refresh and child preparation/application; configured validation has its
own recorded timeouts, and human review time is excluded. This is an experimental
ceiling, not a latency promise.
Enforce the remaining allowance on in-flight model/executor calls as well as
between tasks. A deadline stops the run with its partial state; it must not reset
for each child. Standalone Quick retains its existing limits.

Standalone Quick's query, context selection, prompts, executor settings and
approval flow must stay unchanged. Feature-only coordination code must not be
called from that path. Preserve the existing exact-prompt/patch parity tests.

## 3. What belongs in a task

A task owns one observable outcome that fits Quick's compact context. It can
modify several related files and include its local regression tests. Prefer two
to four cohesive tasks for the first feature cases; cap the prototype at six
coding tasks. Return a blocked/narrower proposal if the request cannot fit.

Split when an accepted output must exist before another task can work, when two
parts have separate ownership and an explicit interface, or when a coherent
implementation cannot fit the context allowance. Avoid dividing every file,
function, or test into a separate task. Retrieved tests/docs do not automatically
become write requirements. Existing checks run as commands, without creating
another coding task just to execute them.

Example: add a profile endpoint and a UI consuming it. The feature plan first
records the URL, method, fields, error responses and authentication behavior.

| Task | Outcome | Depends on |
| --- | --- | --- |
| T1 | Backend profile service with its unit tests | None |
| T2 | Register the endpoint using that service, with request tests | T1 |
| T3 | Profile UI and component tests against the agreed response contract | None, if it uses a local mock and no files produced by T1/T2 |
| T4 | Add any required real-client integration wiring/test | T2 and T3 |

Run the configured feature checks after all required work. Omit T4 when the
integration is already implemented and existing checks cover it. T3 must depend
on a producer if it imports generated types or other files that do not exist yet.
Backend/frontend labels alone do not prove independence.

The first scheduler executes ready tasks serially in stable task-ID order.
It records that T1 and T3 are independent without promising concurrency.
Parallel preparation is a later experiment requiring isolated working copies,
resource ownership, conflict checks, and validation after integration.

## 4. Feature plan contract

Use a separate, versioned `FeaturePlan` containing references to normal Quick
runs. Do not overload the legacy `TaskPlan` into another universal dispatcher.

| Record | Required contents |
| --- | --- |
| Feature | ID/version, original request, repository identity, baseline source hashes, requirements, task list, unresolved questions, limits, validation policy |
| Shared contract | ID, concrete interface/behavior, owning task if code must be created, and consuming tasks |
| Task | Stable ID, concise outcome, requirement IDs, local acceptance criteria, dependencies, relevant contract IDs, existing input anchors, proposed writes, outputs, configured check IDs |
| Proposed write | Exact repository-relative path and allowed operation: update, create or delete; creator task for new files |
| Output | Path plus symbol/interface or other artifact a successor consumes; after execution, actual hash and check results |
| Run | Immutable plan version, task-to-Quick-run IDs, status, current source hashes, actual calls/timings, checks and failure reasons |

Allocate IDs locally. Existing paths must come from inspected repository evidence;
new paths are explicitly proposed and cannot masquerade as existing context.
Task briefs and contracts are bounded fields, not generated essays. The model
references configured check IDs; it cannot invent executable shell commands.

Validate cycles, missing IDs, duplicate ownership, missing output producers,
unassigned requirements, path exclusions, context/task limits and conflicting
read/write ownership before Run is enabled. Shared-file writes and reads that
depend on another task's writes require explicit ordering. Prefer a blocked
proposal to guessed ordering when semantics are unclear. Structural validation
cannot prove that the model discovered every requirement or dependency.

Before running each child, compare its actual Quick write impacts with that
task's authorized paths/operations. Read access follows existing repository
retrieval exclusions. Additional retrieved files must not silently gain write
permission. Do not rewrite a Modify row to Read just to pass this gate; retain
the model result, report the scope conflict and stop for amendment.

## 5. Bounded discovery and fresh child context

For the generated-plan prototype, start with one feature-level hybrid query
(12 candidates), then a deterministic lookup of relevant module entry points,
exports/routes and user-named symbols. Cap the combined pool at 24 candidates and
the coordinating prompt at eight chunks within the configured full-input token
budget. These are feature-only experimental limits, not Quick defaults.

The repository map is a bounded list of evidenced paths/symbols with provenance;
it must not be a whole-repository prompt. Do not assume the old textual-reference
expander resolves imports or exports. Proposed relationship lookups need explicit
evidence. Missing interface facts become unresolved questions in the plan.

Generate the feature plan once, with reasoning off. Allow at most one schema
repair and record it. Do not begin a semantic replanning conversation. The first
prototype has no automatic extra discovery wave; measure blocked cases before
adding a targeted retry policy.

When a child becomes ready:

1. Check the feature's relevant source preconditions against the current run
   ledger, including expected absence/presence of newly created files.
2. Refresh the existing incremental index for accepted changed/new/deleted files
   before querying. Reuse the warm embedder and existing index lifecycle; do not
   introduce a separate overlay index in this prototype.
3. Build the child request from its task card and relevant contracts. Perform
   the normal Quick 12-candidate query and call `create_quick_draft`, which owns
   the usual up-to-five-chunk packing and classification. Retain its review and
   evidence; do not perform a second packing/classification pass in the adapter.
4. Confirm required predecessor artifacts are present and the selected evidence
   covers their declared interface anchors. A hash proves freshness, not semantic
   sufficiency. If essential inputs are absent, block before coding; do not inject
   unlimited files after packing or use a summary of unavailable source.
5. Check the actual Quick write authorization, then call `run_quick_task`.
   Record the actual patch/checks and update the ledger.

A successor's first retrieval on updated source is necessary initial context,
not a retry. Count both feature discovery and every child retrieval/index refresh
in total cost. Do not prepare all child drafts against the original snapshot;
that would reproduce stale-context and missing-new-file failures.

Keep retrieved line-anchor improvements separate from this experiment until they
are validated for ordinary Quick. The known `dump_age` execution-handoff issue
does not become solved merely by adding a scheduler.

## 6. Checks, failures and durable execution

Use explicit states: waiting, ready, preparing, running, applied-unvalidated,
validated, blocked, failed and cancelled. A failed/blocked producer never releases
its dependents. The serial prototype stops the run on the first failure, including
an independent branch, to keep partial outcomes predictable.

Standalone Quick retains `executor.validation_commands`. Feature configuration
explicitly separates registered task-local checks from final integration checks.
The adapter supplies a config copy with each child's declared local commands;
the feature runner executes the declared final commands once after all tasks.
Do not mutate the repository's config or run a not-yet-implementable integration
suite after every intermediate task. If no feature-specific policy is supplied,
use the existing configured commands after each child and at the end; changing
that behavior requires an explicit policy visible in Feature Review.

Required local checks must pass before advancing. A child with no configured
checks remains applied-unvalidated and may provide fresh source to its successors;
do not describe it as independently verified. Overall `validated` requires all
tasks to finish, all required checks to pass, and nonempty final feature checks.
Without final checks report applied-unvalidated. Neither status proves every
natural-language requirement; independent benchmark acceptance is separate.

Keep completed patches when later work fails. Do not silently revert user work,
retry coding, rewrite the graph or split the failed task into new tasks. The
existing bounded repairs inside Quick remain its own measured policy. The first
prototype supports inspecting a failed run and creating an explicit new run;
automatic resume/replay is deferred.

Store feature artifacts under `.devstill/features/FEATURE_ID/`, linking normal
child artifacts under `.devstill/tasks/QUICK_ID/`. Record the immutable plan,
approval version, source ledger, proposed/actual impacts, model attempts, elapsed
times, validation output and final diff. Saved old Quick/structured task formats
remain readable.

One feature-run lock serializes coordinators; each child retains Quick's existing
writer lock. Do not acquire `.task-writer.lock` around the whole feature and then
reacquire it inside `run_quick_task`. Detect relevant outside source changes
between tasks and stop; this design does not certify concurrent external writers.
Cancellation stops scheduling and prevents pending application using the existing
Quick cancellation hook. Already applied changes and durable status remain.

## 7. Implementation order and boundaries

| Stage | Concrete deliverable | Exit condition |
| --- | --- | --- |
| A. Protect Quick | Freeze current source/settings and parity fixtures; add explicit request type with Quick default | Single requests make identical model/tool requests and never invoke feature coordination |
| B. Prove composition | Feature records/validator and serial adapter using supplied, reviewed task cards | Ordered tasks see predecessor changes/new files; a joined outcome passes independent checks |
| C. Generate the plan | Bounded feature discovery, one coordinator schema/prompt, source grounding and scope checks | Generated plans run through the same tested adapter; invalid/insufficient plans block transparently |
| D. Add feature review | Task/Feature selector, feature review/run/progress, CLI, artifacts and cancellation | One approval covers authorized work; actual child impacts and failures stay visible |
| E. Evaluate | Real-repository comparisons and an auditable failure/cost report | Small-task protection and feature acceptance gates below pass before default availability |
| F. Later | Isolated parallel preparation and measured bounded recovery | Separate correctness and performance evidence; no coupling to the initial release |

Proposed new modules: `feature_planning.py` for records/discovery/coordination and
`feature_runs.py` for the serial adapter/state. UI routing belongs in
`tui_commands.py`, `tui.py` and `cli.py`. Reuse existing indexing/Quick/executor
functions; any extraction from them must retain behavior and parity tests.
Do not route features through the old structured branch of `task_runs.py`.

Start with supplied plans so execution failures can be separated from generation
failures. Supplied-plan results are diagnostic; they do not count as a generated
feature workflow succeeding. Keep the prototype opt-in until evaluated.

## 8. Evaluation plan and release decision

Use **24 distinct real change requests**, not five repeated prompts:

- Preserve the 12 small-edit cases as Quick regression controls.
- Prepare 12 feature cases across pinned Werkzeug and Cargo, including module
  creation plus export/wiring, ordered producer/consumer work, and separate
  branches joined by integration. Use the old exposed feature cases for debugging;
  freeze fresh feature cases and reference checks for the final comparison.
- A small backend/frontend fixture can test the contract and independence rules,
  but report it as a synthetic diagnostic. The current library corpora do not
  establish browser frontend quality; add a separately pinned app corpus before
  making that claim.

Verify each case fails on starting source and passes with its evaluator-only
reference patch. Keep gold changes, expected task graphs and hidden checks out of
retrieval/model input. Related cases remain in the same development/acceptance
split; independent review is preferred. Record when cases are same-author.

First run supplied plans on a few development features to debug the adapter.
For the final generated workflow, compare the same 24 requests under frozen
whole-request Quick and explicit routing (Quick for small, Feature for features).
Three repeats per method give **144 attempts**. Alternate method order, use clean
disposable copies, keep endpoint/model/indexes fixed, and stop runtime tuning
during the campaign. Do not rerun selectively until scores improve.

Report small and feature results separately, including:

- Complete feature acceptance **and** successful workflow completion. Additional
  failed tasks invalidate the run even if some behavior checks pass.
- Distinct successful composed features with at least two coding tasks, including
  actual dependency handoffs. A one-task feature success counts toward overall
  quality but does not establish composition.
- Per-task source sufficiency, scope blocks, missing requirements/contracts,
  stale context, classification/coding failures and final integration failures.
- Coordination, retrieval, reindexing, Quick classification, coding, checks and
  total wall time; actual HTTP/model/tool calls and available tokens. Include
  preparation and all children. Use shared successes for speed comparisons and
  show failed/blocked latency separately.

The natural Feature policy can spend more compute than one Quick invocation.
Publish that cost-quality tradeoff. Also run a separately declared comparison
with equal aggregate model-call and wall-time ceilings; retain per-call Quick
limits. Freeze those ceilings before the acceptance run and count coordinator
calls/repairs against them. If Pi token totals are unavailable, report that and
do not claim matched token cost. Per-task limits alone are not aggregate parity.

Promotion gates:

1. Standalone Quick has no extra coordinating calls, prompt changes or test
   regressions. Investigate any live small-case regression before promotion;
   repeated stochastic trials alone cannot prove equivalence.
2. Generated Feature has at least the baseline's complete feature successes and
   no lost cases that baseline consistently passes across all three repeats.
3. At least four distinct features complete using multiple Quick tasks across
   both corpora, including ordered and joined dependencies. This is a minimum
   composition demonstration, not a general success-rate guarantee.
4. All stale-input, scope, cancellation, conflict, failure-propagation and final
   validation checks pass. No unvalidated/partial run is relabeled as validated.
5. Report the feature speed/cost tradeoff. Claim an improvement only where quality
   or successful completion time improves under the stated aggregate allowance;
   correctness gains bought with more compute must be labeled accordingly.

Extend `coding_campaign.py` with an explicit composed-Quick candidate and feature
artifact collection; do not replace its historical variants. Extend
`coding_report.py` with complete-feature scoring, true multi-task counts, and
per-stage/aggregate budgets. Add focused tests for ordering, new files, wrong
contracts, independent-read conflicts, missing scope, cancellation, lock ownership
and immutable run versions, alongside the existing Quick/UI parity tests.

The next implementation handoff is **A and B first**: protect the single-task
path, then demonstrate serial composition through the real Quick functions.
Generated coordination and feature UI follow that proof. The original plan-writing turn changed documentation only. See the implementation
report for subsequent code changes; live feature benchmarks remain pending.
