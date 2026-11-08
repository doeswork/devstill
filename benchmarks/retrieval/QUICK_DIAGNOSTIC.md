# Quick execution diagnostic

Broad campaigns remain stopped. This experiment changes benchmark code only.

## Concrete failures being isolated

In `pi-four-way-dev-v2/w_close-0-quick`, Quick read three file prefixes, exhausted
its cumulative read-character budget, then overwrote `wsgi.py` with an incomplete
file. It removed `wrap_file`; acceptance failed at import time. In `w_age-0-quick`,
it read the first 480 lines of `http.py`, inserted `dump_age` at the
`parse_options_header` signature instead of editing the actual function at line
1067, and truncated `CHANGES.rst` through a whole-file write. Later reads were
blocked by the cumulative read budget. These are source-access and editing
failures, not evidence that embedding search cannot locate the code: the context
pilot's selected evidence includes the complete `dump_age` function and its range.

Current Quick's execution prompt passes the plan without the selected code
excerpts. The two experimental arms explicitly carry those excerpts forward.
The minimal arm additionally avoids whole-file replacement of existing files.

Compare four exposed Werkzeug small-edit cases: `w_repr` and `w_salt` controls,
`w_close` incomplete iterator change, and `w_age` large-file lookup failure.
The same original request, checkout, served model, prepared toolchain and hidden
acceptance/regression checks apply to all arms. No hidden tests enter model context.

| Arm | Context | Execution |
| --- | --- | --- |
| `pi` | Original request | Native Pi read/bash/edit/write |
| `pi_context` | Request + actual Quick-selected snippets with paths/lines | Identical native Pi executor |
| `minimal` | Same retrieved evidence as `pi_context` | Exact text replacements, Python checks, at most two generation calls |

The minimal editor has no separate planning call. It can use its second response
for a bounded source read follow-up or repair. All replacements must match exactly
once; all files in a batch are checked before writing; changed Python must compile.
The model supplies behavioral checks. Hidden acceptance independently decides
correctness, including original regression tests protected from model edits.
Passing model-supplied checks alone never earns benchmark success.

The initial three-way run exposed output-format and repair failures in the minimal
editor. Revision 2 requires a JSON schema response, permits checks-only repairs
after an applied edit, accepts a read alongside an edit, and clarifies check-string
escaping. It retains two generation calls and identical retrieval. A single
eight-attempt recheck compares revised minimal against fresh native Pi draws on
the same four cases; it does not silently replace the initial results.

This is an experimental Python editor running in disposable clones, not a general
production executor or security sandbox. Existing Quick/Review/YOLO are unchanged.
The earlier two-arm pilot uses normal configured Pi behavior (`--thinking off`,
but the server can emit reasoning). The new three-arm diagnostic uses
`--pi-disable-reasoning`: the recorder adds `enable_thinking=false` to both native
Pi arms, preserving original and upstream request bodies. Minimal sends the same
setting directly. Response metrics verify whether reasoning was actually emitted.
Total generation/tool budgets still differ by executor; all share the 180-second
outer timeout. Captured transport is the evidence.

## Bounded run

The already-running `pi-context-pilot-v1` supplies three repetitions of the two Pi
arms (24 attempts). After it finishes, run one fresh three-way repetition (12
attempts), rotating arm order by case. Do not pool sequential campaigns into a
paired timing claim. Preserve every attempt and failure; do not select best draws.

```bash
PYTHONPATH=src:. /home/jeffy4080/.local/share/devstill/venv/bin/python \
  -m benchmarks.retrieval.pi_comparison run \
  --variants pi,pi_context,minimal --pi-disable-reasoning \
  --repeats 1 --purpose development \
  --case w_repr --case w_salt --case w_close --case w_age \
  --verified .devstill/task-v2-case-verification-c \
  --output .devstill/quick-three-way-v1
```

For the bounded revision-2 recheck, use `--variants pi,minimal` and output
`.devstill/quick-minimal-v2` with the same cases, reasoning setting and one repeat.
The initial run's frozen source is retained under its output directory.

Compare completed-and-correct workflows and correct patches separately. A correct
patch followed by a timeout is a workflow failure, not an incorrect patch.
Report worker wall including retrieval/index setup, and warm coding time separately;
validation time is also recorded. Count actual model calls. Only compare speed on
shared successes and retain all-attempt cost to avoid rewarding failed fast runs.

## Decision gate

If an arm loses correctness on these four cases, inspect the concrete failure and
stop expansion. A 4/4 result is a smoke gate, not proof of superiority. A promising
arm must repeat on these cases and pass a small fresh set before any broad campaign.
Require correctness at least matching Pi and lower paired successful wall time;
also inspect per-case regressions and total failure-inclusive cost. Do not change
production Quick or build feature coordination around an unproven editor.

Results: `QUICK_DIAGNOSTIC_RESULTS.md` once the bounded run finishes.
