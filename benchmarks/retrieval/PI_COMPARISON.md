# Direct Pi versus Devstill: benchmark protocol

The product target is a lower time to a correct completed change than stock Pi,
with no loss of correctness. Quick and coordinated features are evaluated separately.
The runner is `python -m benchmarks.retrieval.pi_comparison`; it supplements the
historical campaigns without replacing their results.

## Compared methods

| Arm | Initial input and execution |
| --- | --- |
| `pi` | Original request, stock Pi system prompt and `read,bash,edit,write` tools; one noninteractive session |
| `quick` | Original request through production Quick retrieval, file-impact review and scoped Pi execution, using YOLO |
| `feature` | Small requests use Quick; feature requests use the production coordinator and its Quick children, using YOLO |

“One shot” means one user request with no human intervention. Stock Pi may make
multiple model/tool calls, read the repository, run tests, and repair its changes.
It gets neither Devstill's retrieved context nor evaluator paths, checks or patches.
Project context files, user extensions, skills and MCP are disabled for reproducibility.
The direct arm retains Pi's normal tool descriptions and completion behavior.
This is an isolated coding-process comparison, not an interactive-terminal benchmark.

All arms use the same model endpoint/alias and Pi executable. Generation output
caps are 4096 tokens per response, with thinking requested off. Actual serialized
requests are saved: provider-specific thinking/tool-choice hints can differ between
stock Pi and Devstill's extension. These differences belong to the compared methods;
do not silently normalize requests or claim identical prompts/sampling.

The default operating point has a common 180-second outer worker deadline.
Production Quick retains its 120-second execution limit and 12-tool allowance;
Feature retains its production active budget. Stock Pi has its ordinary tools,
with no artificial per-task tool cap. These are normal operating policies, **not
equal aggregate compute**. `--max-model-calls N` adds a common per-attempt generation
request allowance across planning, coding, repairs and transport retries. Tokenizer
and metadata requests are recorded but do not consume that allowance. Use a separate
campaign for this ablation; total token budgets remain unmatched.

## Required records

Every new output directory records its purpose (`smoke`, `development`, `evaluation`),
selected cases/repetitions/order, runtime configuration, corpus revisions, dataset
hash, Python package versions, platform, Pi version/executable/bundle hash and model
server metadata. External corpus configs and SQLite index metadata are hashed and
checked again at the end; index drift invalidates a release comparison. Python and
TypeScript source plus benchmark code are copied before
the first attempt; worker imports use that snapshot. Original gold cases and checks
are retained outside disposable coding repositories. Git clones are dissociated
from the prepared corpus before stock Pi can use shell/Git tools.
Every Devstill attempt receives a private index copy: even read queries can update
Chroma's SQLite bookkeeping. Original retrieval indexes are never opened by workers.

Each attempt retains the original query, exact command, actual model request/response
bodies, Pi tool events or Devstill artifacts, exit/failure status, full binary patch,
acceptance logs, stage timings, upstream generation attempts and available usage.
Authorization headers and resolved API keys are never written by the recorder.
Artifacts contain repository source and model output; retain them as private data.
Missing token usage remains null, never zero or estimated from characters.

The recorder forwards HTTP streaming without changing bodies. It adds local logging
overhead to all arms; absolute timings are instrumented, not an uninstrumented speed
claim. Failed requests count toward model-call cost. Blocked calls are reported
separately. The recorder's idle read timeout is 60 seconds. Run one campaign on the
server at a time; the lock covers this runner only, not unrelated clients. Record
other server load and server-side sampling/cache configuration in a companion note.
The server prefix cache is not flushed: rotate order to reduce order effects and
record cache usage when supplied. Do not describe this as controlled cold-cache data.

Results are saved after each attempt. A final audit checks frozen source, runtime
configuration and Pi identity. Mid-run Pi upgrades stop the campaign. Source changes
in the working tree are flagged but cannot alter frozen workers. Keep interrupted
campaigns and label them incomplete; output directories cannot be reused.

## Correctness and speed standards

1. Reference verification must match the exact dataset: each hidden acceptance
   check fails on starting source and passes on the reviewed reference patch.
   The benchmark reuses this verification before any model calls.
2. A success requires both hidden behavior/regression checks and normal whole-run
   completion. A passing behavior test followed by a failed child is a failure.
   A timeout with a useful patch is still a workflow failure. Preserve these two
   outcomes separately. Hidden checks run only after coding exits, in another copy.
3. Werkzeug evaluator regressions are restored from pinned source before evaluation,
   so model edits cannot delete/weaken them. Cargo runs hidden assertions plus the
   crate library tests; original inline regression edits remain visible in the patch
   and require review before promotion. Never accept a test-weakening patch as evidence
   of non-regression. Evaluation source/checks never enter coding prompts.
4. Report all attempted cases and failures. Compute speed only on matched
   case/repetition pairs where both methods complete correctly. Report the median
   of paired candidate/Pi ratios, paired medians, and total time/calls across all
   attempts. An early failure cannot become a speed win. Never compare unrelated
   medians or select only the candidate's fastest successful draws.
5. `worker_wall_seconds` is the primary latency: process launch, embedding warmup,
   index copy, retrieval, planning, coding and application. `coding_seconds` starts
   after Devstill embedding warmup/index copy (Pi includes startup/configuration).
   `end_to_end_seconds` adds independent acceptance. Checkout time is excluded for
   all arms. Keep these names/boundaries stable; do not call coding time full app startup.
6. Track small-edit and whole-feature results separately. At least three frozen-version
   repetitions are required for a reliability comparison; repetitions do not increase
   the number of distinct cases. Report per-case outcomes and paired wins/losses.
   `descriptive_target_met` is only a point comparison, not statistical proof or promotion.
7. Freeze the case set, selected variants, repetitions, budgets and decision thresholds
   before a campaign. Tune only on development cases. Fresh evaluation needs separately
   authored/reviewed task families and reference verification. A new filename or
   `--purpose evaluation` does not make exposed cases held out.
8. Before recommending a release, require no reproducible correctness regression
   against direct Pi or standalone Quick on the locked evaluation, inspect failed
   cases and test edits, and demonstrate a meaningful latency gain. Predeclare its
   size (suggested starting target: 10% lower median paired wall time), report
   uncertainty over distinct cases, and examine slow-tail/timeouts as well as the
   median. These small development samples cannot establish statistical equivalence.
   Preserve unsuccessful experiments; never rerun until a preferred score appears.

## Commands

Run from the Devstill checkout using the installed embedding environment. Use a new
output directory each time. These commands make real model calls.

```bash
# Four-attempt integration smoke; not a quality/speed conclusion.
PYTHONPATH=src:. "$HOME/.local/share/devstill/venv/bin/python" \
  -m benchmarks.retrieval.pi_comparison run \
  --variants pi,quick --case w_repr --case w_salt --repeats 1 --purpose smoke \
  --verified .devstill/task-v2-case-verification-c \
  --output .devstill/pi-quick-smoke-NEW

# 216 attempts: 24 existing development cases × 3 repeats × 3 arms.
PYTHONPATH=src:. "$HOME/.local/share/devstill/venv/bin/python" \
  -m benchmarks.retrieval.pi_comparison run \
  --variants pi,quick,feature --repeats 3 --purpose development \
  --verified .devstill/task-v2-case-verification-c \
  --output .devstill/pi-three-way-dev-NEW

# Read/recompute a summary without changing saved artifacts or making model calls.
PYTHONPATH=src:. .venv/bin/python -m benchmarks.retrieval.pi_comparison report \
  --output .devstill/pi-three-way-dev-NEW

# Compare saved experiment summaries and integrity audits over time.
PYTHONPATH=src:. .venv/bin/python -m benchmarks.retrieval.pi_comparison history \
  --output .devstill
```

For new cases use `coding_campaign verify --dataset DATASET.json --output VERIFIED`,
then supply matching `--dataset DATASET.json --verified VERIFIED` to this runner.
Prepared corpora, embedding indexes and acceptance environments remain prerequisites;
see `feature_preflight.py` and the historical campaign documentation.

## Pi trust-lock check

The earlier `EROFS` error was caused by a read-only agent sandbox, not a stale lock.
The normal terminal's trust directory is writable and has no leftover lock. Check it:

```bash
mkdir -p "$HOME/.pi/agent"
test -w "$HOME/.pi/agent" && echo "Pi trust directory is writable"
pi --help >/dev/null
```

If the message is `read-only file system`, grant the process write access to that
directory in its sandbox policy or run from the normal terminal. `chmod` cannot fix
a read-only mount. Do not delete a live lock or run Pi with sudo. Use a writable,
isolated `PI_CODING_AGENT_DIR` for controlled runs; this runner already does so for
coding. Devstill's initial installation probe also needs the ordinary trust directory
writable on Pi 1.5.0.
