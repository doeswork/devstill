# Quick runtime: the agent loop

**Status (2026-11-08):** Quick's default engine is a retrieval-primed tool loop
(`editing.engine: "loop"`). Paired results against Pi are in
[QUICK_LOOP_RESULTS.md](benchmarks/retrieval/QUICK_LOOP_RESULTS.md). The previous
two-response structured editor remains available with `editing.engine:
"structured"`; its design notes and history follow the loop description below and
in the [archive](archive/README.md).

## How the loop works

1. **Retrieval.** Lexical search runs first; the embedding model (a multi-second
   cold load) is used only when lexical search finds no code at all or a warm
   session already holds it. Documentation ranks after code unless the request is
   about docs. Files the request names (a test module, `pyproject.toml`, an
   `.rst` page) are always included, as are module-qualified names
   (`pkg.mod.func`), plus the best function-sized chunks from up to two other code
   files as alternate locations. Chroma is opened lazily, so lexical runs never
   import it.
2. **First turn.** The model receives the request plus whole-line excerpts
   (merged per file) of the current source, each file's import path and a short
   outline of its real classes and functions, and existing test lines that call
   the target symbols. A fixed example exchange showing a one-response finish
   (edit, check, done) precedes the task; servers with prefix caching process it
   once.
3. **Tools.** `read`, `python`, `bash`, `edit` (all changes to one file in one
   call, as a list of exact, unique `{oldText, newText}` blocks that apply all or
   none; whitespace/Unicode-tolerant fallback; a failed match quotes the closest
   current text; Python files must still compile), `write` (new files, or
   `append=true` to add to an existing one; files over 40 lines cannot be
   replaced wholesale) and `done`. Several calls in one response run in order; a
   failed edit skips the rest of that response.
   - **Checks.** A `python` script runs with the project interpreter as
     `__main__`. Each failing top-level assert prints its operands (captured
     when the assert ran); a run of consecutive asserts is reported together,
     and the script stops after the run, so a guard still guards what follows
     (asserts inside functions, `with` blocks and `try` blocks that catch
     AssertionError stay plain asserts). The output ends with the first failing
     line; an exit from the code under test before the check ends, or an
     `os._exit`, cannot turn a failure into a pass. When a library call on the
     failing line raises, its signature is shown. After an edit, a failing
     check is also run once on the original code, and the result says which
     assertions held there and fail now (the change broke them), which failed
     there with other values (reached, still wrong), which fail the same way
     (the change does not reach them yet, or the check is set up wrong) and
     which the change fixed. A check that drops or flips a failed assertion
     gets a warning, unless that assertion failed with the same values on the
     original code.
   - **What counts as a check.** The `python` tool with an assert, a test
     runner (`pytest`, `python -m unittest`, `tox`, `make test`, also behind
     `uv run`, `time`, an interpreter path, ...) or an inline Python script
     (`-c` or a heredoc) that asserts. A passing print-only probe verifies
     nothing; a failing command that runs Python or tests still counts against
     the change.
4. **Finishing.** When the last call of a response is a check that passes,
   fails on the original code, ran at least one assert and did not drop or flip
   an earlier failing assertion, and every check since the last edit passed,
   the task finishes without another model call. Otherwise `done` (or a plain
   reply) ends the task unless an edit or check in the same response failed.
   Finishing after a failed check is refused once (naming test failures that
   also fail on the original code, and test runs that did not finish). A
   finish is also refused once when the agent's latest passing python check
   passes on the original code too (it does not test the change). Required
   deliveries are then checked, and the **regression guard**
   reports tests the change broke: after every edit it re-runs, in the
   background, the existing test files that mention the changed functions and
   (when Python changed) the whole test directory if it finishes within a few
   seconds. A failing test counts only if it passed with the original sources
   (whole test files re-run with them restored) and fails again in a focused
   re-run. Regressions are reported back to the model (twice at most); a change
   that still breaks tests is never accepted.
5. **Limits.** Up to `max_turns` model calls (20) within `timeout_seconds`
   (120 s for the whole task); at the limit a change is accepted only if its
   latest check passed, or every failing check since the last edit says nothing
   against the change (a python check that now gets further than on the
   original code while everything that held there still holds, or a test run
   that never finished), no edit in the last response failed, and the delivery
   checks and guard pass. Cancellation stops the task at the next tool or call.
6. **Workspace and review.** The task runs in a temporary copy of the repository
   (made while the first model call is in flight) that also contains small
   non-sensitive support files such as test fixtures. Only the agent's own
   changes are proposed: edits and new files from `edit`/`write`, and tracked
   files its commands changed; scratch files, test-run side effects and support
   files never are. Review shows the diff, the agent's checks and its summary;
   applying records `agent_check_passed` only when the latest real check after
   the last edit passed (and was not refused as passing on the original code
   too), no failing check was excused and no regression is outstanding,
   otherwise `unvalidated`.

Configuration (`editing` section): `engine` (`loop`), `max_turns` (20 model calls),
`turn_output_tokens` (2048 per call), `bash_timeout_seconds` (30),
`loop_context_chars` (16000 first-turn context), `regression_tests` (true),
`timeout_seconds` (120 for the whole task) and `python` (interpreter for checks;
defaults to `python`/`python3` on PATH). Requests disable server-side reasoning
and use the endpoint's native tool calling.

**Safety.** Commands run in the workspace copy with your user's permissions, the
same trust level as Pi's bash tool; the copy is not a sandbox. Command
environments omit credential variables (API keys, tokens, passwords), and
`python` in commands is the configured project interpreter. Git-ignored and
sensitive files are never copied or shown to the model. Applying a proposal
re-verifies every copied source hash first.

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
**Edit request** actions. `/edit-yolo REQUEST` applies once the agent finishes
(loop engine) or after generated checks pass (structured engine).
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

## Structured engine (previous default): design and history

### What is implemented

- `src/devstill/runtime/editor.py`: extracted hardened algorithm; strict local
  schema validation, checks required before any write, exact replacements and
  create, bounded reads, checks-only repair, two responses maximum. Read-only
  responses may omit checks; multiline check strings preserve literal escapes.
  Repair indices are limited to retained nonpassing checks.
- `service.py`: UI-independent warm session, incremental index reconciliation,
  context packing against editor messages, source verification, prepare/load/apply.
- `context.py`: bounded imports and constructor/inheritance context for Python,
  including relevant type-checking imports and verified local dependency signatures.
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

### Current limits and remaining rebuild work

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

### Validation and live comparison

The latest full suite passed **596 tests, 1 skipped**. Ruff and diff whitespace
checks passed. Wheel packaging/console-entry-point verification passed during the
original cutover; this repair did not reinstall the package. Coverage includes
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

### Default Quick cutover

Quick now defaults to the native Devstill runtime (`editing.enabled=true` by
 default), following explicit user direction. `task --engine legacy` selects the
old Pi path explicitly. The check wire format now uses `python_lines` arrays;
the runtime joins source lines without unescaping Python string literals. The
old invalid-check response remains retained as a failed benchmark attempt.
This default change is a product decision, not evidence of having beaten Pi.

## Herdr lifecycle and session restoration

Run `devstill` in a Herdr pane. The TUI reports `working` during operations,
`blocked` while a review or input dialog needs attention, and `idle` when ready.
Herdr may display an unviewed idle completion as `done`. Closing Devstill releases
its registration. Reporting uses the inherited Herdr binary and pane, with a
background worker, coalesced updates and a short timeout; failures do not stop
Devstill. Outside Herdr, reporting is disabled.

TUI sessions are saved privately beneath the configured index directory's parent,
in `sessions/<id>.json`. Herdr receives `devstill --resume <id>` as the restart
command. This restores the transcript, prompt history, draft text, composer
settings and a pending Quick patch review. Manual restoration uses the same
command from the original project directory. A pending patch is reloaded from its
validated artifact and must still be ready; applying it retains the usual digest
and source checks. Interrupted operations are not replayed automatically. Legacy
Plan/Pi reviews return as transcript entries and need to be started again.
Session snapshots contain task text and results, are written atomically with
owner-only file permissions, and contain no process environment or agent API key.

The JSONL adapter (`devstill session --stdio`) reports lifecycle state while
handling requests, but does not advertise automatic restoration of its input
stream. One-shot `edit`/`runs` commands report work and release on exit.

Verified with Herdr 0.9.5: status reporting, blocked input, release, and a real
server restart restoring the same TUI session. That Herdr version rejects custom
agents through `agent prompt` with `agent_not_ready`; use normal terminal input
or `pane send-text`/`pane send-keys` for automation until Herdr supports that path.
