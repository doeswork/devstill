> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/FEATURE_IMPLEMENTATION.md`.

# Feature coordination over Quick: implementation and benchmark handoff

Feature is an explicit, opt-in workflow that tries the original request through
Quick first. A runnable Quick review goes directly to coding. Only an unresolved
review invokes coordination, with at most three tasks. Quick task remains the default. Live comparisons and the subsequent coordinator revision are recorded in
[FEATURE_COORDINATION_RESULTS.md](FEATURE_COORDINATION_RESULTS.md). Feature quality
is experimental; passing implementation tests does not establish benchmark parity.

## Available workflow

Choose **Quick task** or **Feature**, independently of **Review** or **YOLO**.
Search remains local and ignores request type. Explicit commands are:

```text
/task change the button color
/yolo change the button color
/feature add a profile endpoint and a UI that consumes it
/feature-yolo add a profile endpoint and a UI that consumes it
```

CLI equivalents:

```bash
devstill feature "Add the profile endpoint and UI" --json
devstill feature "Add the profile endpoint and UI" --yolo
devstill feature --run FEATURE_ID --yolo
```

A direct Quick review shows the usual file impacts. When coordination is needed,
Feature Review shows requirements, shared interfaces, task dependencies,
proposed read/write paths and configured checks. One Run feature action authorizes
that scope. Ready tasks run serially; the prototype records independence without
running children concurrently. Stop feature prevents further application and
scheduling; already applied changes remain. After stopping, a new request can run
normally. Closing the application retains the same cancellation protection.

When needed, the coordinator makes one bounded structured generation, with at most one
schema/reference repair. The model supplies named task briefs, acceptance criteria,
file paths and named prerequisites. Code assigns IDs, resolves prerequisite names,
infers file operations and orders conflicting access. It supplies up to three
cohesive task briefs; it does not implement code. Each child calls the existing
`create_quick_draft` and `run_quick_task`, retaining ordinary Quick classification
and bounded Pi execution. There is no recursive planning or alternative edit
format. Standalone Quick's prompts and context selection are unchanged; its only
executor API addition is optional runner injection for feature deadline control.

Actual child file impacts are shown in the result and saved with each Quick run.
A child's writes must fit its own reviewed feature paths and operations. Extra
writes, unresolved context, missing declared inputs, stale files or undeclared
read/write conflicts stop the feature. The planner checks requirement ownership,
cycles, unknown references, new-file producers, shared contracts and conflicting
tasks. These are structural checks; they cannot certify semantic completeness.

Successors retrieve current source after incremental indexing of accepted edits,
including new files. They do not reuse drafts created before their predecessors
ran. Declared existing inputs and write targets are prioritized in child context. A
bounded exact-file lookup supplies required files missed by semantic retrieval,
including newly created predecessors and empty package initializers. Child context
uses reviewed files; tasks with no existing inputs get up to two convention examples.
Quick still enforces its existing token/chunk budget. Missing required anchors block
coding. Reads from an already completed independent task are recorded as observed
dependencies; reads conflicting with an unfinished writer still block.

## Validation and limits

Direct Quick uses `executor.validation_commands` and its normal execution budget.
For coordinated features, configured `executor.validation_commands` run after each child and
again after the feature. A feature-specific policy can distinguish local and
final checks:

```json
{
  "feature": {
    "timeout_seconds": 180,
    "checks": {
      "unit": ["python -m pytest tests/unit -q"],
      "integration": ["python -m pytest tests/integration -q"]
    },
    "task_checks": ["unit"],
    "final_checks": ["integration"]
  }
}
```

Use paths/commands appropriate to the repository. An explicit empty
`task_checks: []` postpones configured checks until the final stage. Omitted/null
lists retain the existing executor commands. The model may reference registered
check IDs; it cannot create shell commands. Feature Review displays the policy,
and changing it after review invalidates that saved feature.

The coordinator runs the selected local checks after each Quick application and
records them in the feature result. Native child artifacts retain their Quick
application status; the feature records separate local and final validation.
A failed local check stops subsequent work. Nonempty final checks must pass for
the feature to be `validated`. With no final checks it is
`applied_unvalidated`. Independent benchmark acceptance remains separate.

The feature's default active allowance is 180 seconds including discovery,
coordination, index refresh and coding. Human review and configured validation
have separate time accounting. HTTP requests and each Pi attempt receive the
remaining time; pre-application guards also check cancellation/deadline/source
changes. Local synchronous indexing is checked at stage boundaries, so it can
finish an in-flight operation after the deadline, but no later coding/apply step
is allowed. HTTP timeouts retain the transport's per-operation semantics.
This is bounded execution, not an exact wall-clock SLA.

Feature metadata and current source hashes are in
`.devstill/features/FEATURE_ID/`; child Quick reviews, evidence, patches and logs
remain in `.devstill/tasks/QUICK_ID/`. A feature-run lock serializes coordinators
without nesting Quick's writer lock. Relevant outside source changes stop the
run. Automatic resume, rollback and parallel application are not implemented.

## Verification performed without the model server

The full suite passed: **507 passed, 1 skipped**. Ruff and `git diff --check`
also passed. These checks establish implementation behavior, not model quality.
The private local installation was refreshed with `./update-local.sh`; all 53
installed package files match the checkout and `devstill feature --help` passes.
Reopen Devstill to use the new request selector.

Controlled tests exercise the real Quick planner/executor with scripted model
responses and Pi file edits. A three-task feature creates a service module, lets
a dependent endpoint task consume its current source, updates an independent UI,
and passes an actual Python integration check. Tests verify that children use
`devstill_plan` and the normal Quick engine, without sibling instructions or a
child decomposition call.

Coverage also includes scope failures, missing inputs, stale files, cancellation,
local/final check failure, immutable saved reviews, replay prevention, dependency
validation, deadline sharing across HTTP calls and Pi repairs, UI routing/review/
stop controls, CLI JSON review, and exact standalone Quick prompt/patch parity.

The benchmark worker is tested through its generated-feature route with controlled
responses, three real Quick applications and two index-refresh callbacks. A
separate copy test proves SQLite WAL data is included and writes to the private
index cannot modify the source index. Report tests reject partial workflows and
avoid counting one-task features as multi-task composition. Worker timeouts and
interruptions terminate the worker's process group, including Pi children.

## Benchmark readiness

The offline preflight passed for **24 distinct existing development cases**:
12 small edits and 12 features across the pinned Werkzeug and Cargo corpora.
Three repetitions per method produce 144 attempts. Preflight made **zero model
requests**. It checks corpus revision/cleanliness, stored reference verification,
index/model files, frozen baseline hashes, local Pi/Rust/Python prerequisites and
temporary disk space. It does not test server availability or predict quality.

The working Quick baseline is frozen from commit
`831b19993129d444bb75463d25008aa5ba054723` in
`.devstill/feature-quick-baseline/`, with a 49-file source manifest. Its imports and
benchmark model/executor configuration load successfully. Current candidate source
is fingerprinted when a campaign starts and checked between attempts.

The `feature` candidate routes small requests directly to Quick. Feature requests
try Quick first and invoke coordination only for unresolved reviews. Its `old` control runs the frozen Quick
wrapper for every request. Feature workers receive private copies of the source
indexes and index their disposable edited repository between children; frozen
corpus indexes are not used as writable successor indexes.

These remain exposed development cases. They are suitable for the first
comparison, not the fresh acceptance gate in the design. Versioned datasets are
supported through `--dataset PATH.json` on verify, preflight and run. New cases
need separate reference verification before use. Never label a dataset held-out
merely because it has a new filename.

This runner measures natural operating points. A multi-task feature can consume
more model calls/tokens than a single Quick attempt. Reports include available
model-call counts, per-stage timing and whole-feature acceptance; Pi total token
usage may remain unavailable. The design's matched aggregate-call/token experiment
and fresh-case promotion decision remain follow-up work. There is no automatic
promotion based on the development summary.

## Commands for the next step

The baseline and original reference verification already exist in this workspace.
To recreate the baseline elsewhere from repository history:

```bash
.venv/bin/python -m benchmarks.retrieval.coding_campaign freeze \
  --revision 831b199 --output .devstill/feature-quick-baseline
```

Output paths must be new. Preflight without model calls:

```bash
PYTHONPATH=src:. "$HOME/.local/share/devstill/venv/bin/python" \
  -m benchmarks.retrieval.coding_campaign preflight \
  --candidate feature --baseline .devstill/feature-quick-baseline \
  --verified .devstill/task-v2-case-verification-c \
  --output .devstill/feature-preflight-next
```

Run the comparison **when requested**, then audit its output:

```bash
PYTHONPATH=src:. "$HOME/.local/share/devstill/venv/bin/python" \
  -m benchmarks.retrieval.coding_campaign run \
  --candidate feature --baseline .devstill/feature-quick-baseline \
  --verified .devstill/task-v2-case-verification-c \
  --repeats 3 --output .devstill/feature-next-comparison
.venv/bin/python -m benchmarks.retrieval.coding_report \
  .devstill/feature-next-comparison \
  --output .devstill/feature-next-comparison/audit.json
```

Use `--case ID`, `--workload small|feature`, and `--repeats 1` for a declared pilot.
It is not the full comparison. The report records expected/actual attempts and
separates small edits, feature acceptance, genuine multi-task successes, dependency
handoffs, failed child work, and shared-success timings. It never treats a fast
failure as a speed win.

Artifacts kept from implementation: `.devstill/feature-preflight-installed/preflight.json`
and the frozen baseline. The `feature-quick-dev-v1` campaign subsequently completed all 144 attempts; see
the results report for later revisions and limits.
Transfer ignored baseline/index/verification artifacts separately for another
machine. The `/tmp` corpus, Rust toolchain/cache and embedding index prerequisites
must also be prepared there.

## Audit correction

The earlier Quick restoration report overstated live prompt equivalence. The
logs contained message roles and character counts, not full prompts. The updated
audit returns `null` for exact request identity when only summaries are available;
matching summaries are reported separately. Retrieval-pool equality and recorded
pass/timing counts are unchanged. Controlled exact-prompt tests remain the evidence
for standalone Quick's unchanged generation contract.
