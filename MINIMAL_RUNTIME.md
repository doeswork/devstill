# Minimal runtime: implementation and current status

**Next validation:** [active test plan](benchmarks/retrieval/TEST_PLAN.md). Historical designs and reports are in the [archive](archive/README.md).

**Latest:** [constructor context and delivery repairs](benchmarks/retrieval/QUICK_CONTEXT_REPAIR_RESULTS.md).
The short diagnostic completed 4/6 correctly for both Quick and Pi. Quick recovered
three known failures, but generated checks still fail on two converter requests.
The earlier [fresh comparison](benchmarks/retrieval/NATIVE_QUICK_FRESH_RESULTS.md)
failed the correctness gate; the original 10/10 known-case result is historical evidence.

Devstill now has a shared native Quick coding runtime for CLI/headless and TUI.
It retains mandatory checks, syntax/tautology guards and the two-generation limit.
Checks now use source-line arrays, and retrieval includes complete small files. It does not generate a
Task Plan or run Pi. Quick defaults to this runtime. The separate legacy feature path remains available.
Default selection is not a claim of general benchmark superiority.

## Use from the source checkout

```bash
PYTHONPATH=src .venv/bin/python -m devstill.entrypoint edit "Describe the change" --json
PYTHONPATH=src .venv/bin/python -m devstill.entrypoint edit "Describe the change" --yolo --json
PYTHONPATH=src .venv/bin/python -m devstill.entrypoint runs show RUN_ID --json
PYTHONPATH=src .venv/bin/python -m devstill.entrypoint runs apply RUN_ID --digest DIGEST --json
PYTHONPATH=src .venv/bin/python -m devstill.entrypoint runs discard RUN_ID --json
PYTHONPATH=src .venv/bin/python -m devstill.entrypoint session --stdio
```

The normal `devstill` command exposes these after installing this checkout using
`./update-local.sh`. The native Quick cutover also refreshes the private installed copy after validation.
Use your CUDA-capable Devstill interpreter instead of `.venv/bin/python` when that
is where the configured embedding dependencies are installed.

`edit` defaults to Review: generate/check in a temporary workspace, save a proposal,
print the actual diff, and leave the user's source unchanged. `--yolo` runs the
same prepare operation then applies its saved digest. `runs apply` makes no model
call. `task --engine minimal` is an alias for the minimal route. Setting
`"editing": {"enabled": true}` opts normal `task` and TUI Task/YOLO into it; existing
configs without this section default to true. Set false only to explicitly retain legacy Quick. Explicit `task --engine legacy` retains the old route.

In the TUI, `/edit REQUEST` opens an actual patch review with **Apply patch** and
**Edit request** actions. `/edit-yolo REQUEST` applies after generated checks pass.
The task cancel control stops further preparation/application. Closing the app
closes the shared session. Legacy plan/feature controls remain available during
migration; they do not wrap the new editor.

The stdio command processes one JSON object per line and retains encoder/storage
resources between requests. It keeps no cross-task model conversation. Example:

```json
{"id":"r1","action":"review","query":"Describe a small change"}
{"id":"r2","action":"show","run_id":"RUN_ID"}
{"id":"r3","action":"apply","run_id":"RUN_ID","digest":"DIGEST"}
```

Responses contain the caller ID and either a result or error. This initial stdio
adapter is sequential; it does not yet accept an asynchronous cancellation message.
Ctrl+C stops the process. JSON stdout is separate from library progress on stderr.

## What is implemented

- `src/devstill/runtime/editor.py`: extracted hardened algorithm; strict local
  schema validation, checks required before any write, exact replacements and
  create, bounded reads, checks-only repair, two responses maximum.
- `service.py`: UI-independent warm session, incremental index reconciliation,
  context packing against editor messages, source verification, prepare/load/apply.
- `context.py`: bounded imports and constructor/inheritance context for Python.
- `requirements.py`: conservative explicit-file/module/export recognition and
  immutable delivery checks. Checks are retained across repair unless explicitly
  replaced with a reason; previously passing checks cannot be retired.
- `workspace.py`: isolated eligible-source snapshot; preserved working-tree edits,
  file modes and line endings; path/exclusion checks; saved file payloads, hash
  preconditions and digest-bound application; journal and rollback on write failure.
- `checks.py`: Python/pytest argv checks using a configured interpreter, bounded
  captured output, deadlines and child process-group cleanup. Source-mutating
  checks fail the run. A copied workspace is not a security sandbox.
- `artifacts.py`: atomic JSON artifacts, immutable proposal digest verification,
  saved patch, requests/responses, event log, apply result and journal.
- `runtime/cli.py` and `entrypoint.py`: lightweight headless path without importing
  Textual, plus persistent JSONL session. TUI calls the same runtime directly.
- Benchmark `runtime` arm: calls the production prepare/reload/apply operations;
  retains the `minimal` experimental arm and native `pi` control separately.

Configuration under `editing`: `enabled` (default true), `timeout_seconds`
(default 120), `max_output_tokens` (4096), `max_context_chars` (48000), and optional
`python` executable for checks. Endpoint/model/credentials use existing `llm` config.
Requests explicitly disable server reasoning and require JSON-schema support.

Generated checks run on this explicit minimal route, preserving the tested editor
behavior. Results say **generated checks passed**, not independent behavioral proof.
Original benchmark acceptance/regression tests remain outside model context.

## Current limits and remaining rebuild work

This is an experimental usable runtime, not completion of every package in
[REBUILD_PLAN.md](archive/REBUILD_PLAN.md).

- The workspace copies index-eligible text files. Projects whose checks require
  excluded binary fixtures, symlinks, ignored environments or special checkout
  machinery need broader workspace support before they are supported targets.
- Apply conservatively verifies all copied source hashes/modes, not just changed
  files. An unrelated source edit after review can therefore require a new proposal.
- Runtime source writes are journaled and rollback is tested. A process killed
  mid-apply leaves a journal and blocks later applications for inspection; automatic
  crash-recovery commands are not implemented. Never delete a journal without
  checking its before/after contents against the working tree.
- Cancellation is checked between stages and during checks. An in-flight HTTP
  request can take up to its 30-second transport timeout to return. Preparation
  interrupted by Ctrl+C may leave diagnostic artifacts but never applies a proposal.
- Incremental indexing runs before retrieval and reuses session embedding/vector
  dependencies. No warm-session speed advantage is claimed until actually measured.
- General project check profiles, async stdio cancellation, fresh-case promotion,
  removal of legacy execution/UI/config code and Pi-free installer
  defaults are still future packages. Pi is not used by the minimal runtime itself.
- Unsupported delete/rename/binary operations do not become implicit whole-file
  rewrite or shell fallbacks. No automatic fallback to Pi after failure.

## Validation and live comparison

The final full suite passed **550 tests, 1 skipped**. Ruff, diff whitespace checks
and wheel packaging/console-entry-point verification also passed. Coverage includes
create/CRLF, real subprocess deadlines, saved review/restart/application, stale
source/digest rejection, check mutation rejection, checks-only repair, two-call
limit, simulated partial-write rollback, cancellation, TUI patch approval and
headless startup without Textual.

Two short, retained live integration smokes used Pi and the actual runtime, two
known cases, one repetition, explicit reasoning off, 45-second worker caps:

| Run | Pi | Runtime | Finding |
| --- | ---: | ---: | --- |
| `.devstill/minimal-runtime-smoke-v1` | 2/2 | 0/2 | Missing `include_tests` argument in extracted retrieval handoff; zero runtime model calls. Fixed and covered by a direct evidence test. |
| `.devstill/minimal-runtime-smoke-v2` | 2/2 | 1/2 | Representation passed; age failed because both generated checks contained literal escaped newlines that are invalid Python. No failed candidate edits reached live source. |

In the corrected smoke, representation wall time was 9.70 s runtime versus
18.32 s Pi. The runtime age attempt failed at 11.21 s; Pi passed at 13.26 s. Both
runs completed with frozen-source/runtime/toolchain/retrieval audits passing.
There is no valid overall speed-win claim from this smoke. The final create/CRLF
fix was checked offline; it did not change generation prompts or the failed check
syntax guard, and no third live reroll was performed.

The earlier hardened experimental editor's 10/10 versus Pi 10/10 and 2.5% median
paired improvement remains separate evidence in
[QUICK_CHECK_HARDENING_RESULTS.md](archive/benchmarks/retrieval/QUICK_CHECK_HARDENING_RESULTS.md).
Those results are not attributed to this newly hardened runtime.

Next work should address the recorded check-format failure with a bounded,
explicit protocol change, test it offline against the retained traces, then run a
small frozen comparison. Do not loosen syntax checks, mask failing assertions,
add uncounted retries, or rerun until a favorable draw appears. Only then continue
with repeated and fresh-case gates. Broad campaigns remain stopped.

## Default Quick cutover

Quick now defaults to the native Devstill runtime (`editing.enabled=true` by
 default), following explicit user direction. `task --engine legacy` selects the
old Pi path explicitly. The check wire format now uses `python_lines` arrays;
the runtime joins source lines without unescaping Python string literals. The
old invalid-check response remains retained as a failed benchmark attempt.
This default change is a product decision, not evidence of having beaten Pi.
