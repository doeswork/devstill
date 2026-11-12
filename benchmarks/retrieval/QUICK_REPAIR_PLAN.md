# Quick repair diagnostic — 2026-11-08

Status: implemented and tested; see [results and the stopped retry](QUICK_REPAIR_RESULTS.md).
The 24-attempt/probe budget is exhausted. The promotion gate did not pass; Stage B
was deferred and the installed version was not updated. The protocol below is the
pre-run plan; the report records the schema incompatibility and resulting deviation.

This is the approved follow-up to [v5](QUICK_FOCUSED_RESULTS.md). These are exposed
development cases, not fresh validation. Keep the original dataset, acceptance
checks and historical results unchanged. Do not pool candidate versions or discard
failed draws. No broad campaign is authorized by this plan.

## Changes before testing

1. Derive repair feedback and the response schema from retained check state.
   Rejected checks have no repair index. Passing and runtime delivery checks
   cannot be removed. Preserve retained failing checks unless explicitly repaired.
2. Serve guarded read-only requests without validating or retaining their draft
   checks. Reads still consume one of two responses and cannot complete a task.
3. Join actual multiline `python_lines` entries without changing literal string
   escapes. Validate the resulting Python before writing edits, with a limit of
   120 lines and 24,000 characters per check. Keep the behavior and tautology guards.
4. Resolve constructor annotation imports inside recognized `TYPE_CHECKING`
   blocks, including aliases. Include verified local import hints and constructor
   signatures, with at most two dependency targets and one additional hop. Keep
   exclusions, source verification and existing context budgets.

Preserve saved review/apply, headless and TUI interfaces. Do not increase the
two-response limit or send evaluator solutions/checks to the model.

## Offline gate

Run focused runtime tests, deterministic replays of the rejected-check, read-only
and multiline failure shapes, then the full suite and lint. Verify that the
incorrect `encoding=None` edit still fails behavior checks. Confirm exact source
provenance, dependency bounds and exclusion handling. Keep source-application,
check-execution and delivery-verification failures distinguishable from decoding,
check-validation and independent-acceptance failures.

## Live gate: at most 24 attempts total

Use native Quick (`runtime`) versus Pi 1.5.0 (`pi`), same local model, explicit
request-level reasoning disabled, and a 30-second worker cap. Save/reload and
automatically approve reviews in the runtime arm; exclude human review delay.
Freeze source/configuration for each campaign. Review patches for scope and
preservation, and replay protected routing/datastructure regressions offline.

Stage A: `nq01`, `nq03`, `nq10`, two repeats, two methods: **12 attempts**.
Quick must complete 6/6 correctly, with no material quality violations, and a
median paired successful worker-wall ratio <= 0.90 across at least four shared
successes. A faster failure is never a speed win.

```bash
PYTHONPATH=src:. /home/jeffy4080/.local/share/devstill/venv/bin/python \
  -m benchmarks.retrieval.pi_comparison run \
  --variants pi,runtime --pi-disable-reasoning --repeats 2 \
  --purpose development --timeout 30 \
  --dataset benchmarks/retrieval/native_quick_fresh.v1.json \
  --verified .devstill/native-quick-fresh-v1-verified \
  --case nq01 --case nq03 --case nq10 \
  --output .devstill/native-quick-repair-v6-a
```

If Stage A passes, Stage B uses the same frozen candidate: `nq01`, `nq03`, `nq07`,
`nq08`, `nq09`, `nq10`, one repeat, two methods: **12 more attempts**. Require
Quick 6/6, no material quality violations and the same paired speed gate. Only
then update the private installed Devstill. Fresh validation is subsequent work.

If Stage A fails, inspect the actual failure before spending more calls. A
concrete additional fix may use the remaining 12 attempts to repeat Stage A as
a separately identified candidate. In that case Stage B is deferred, and the
installed version remains unchanged. Stop at 24 attempts regardless of outcome.

Record every attempt, model traffic, failure stage, source/configuration audit,
patch review, protected regression result and the gate decision in a new report
linked from the benchmark index.
