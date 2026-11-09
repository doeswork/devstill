# Running the Pi vs Devstill benchmarks

One command runs every benchmark suite unattended, records how it was run, and
writes a report of what passed and what failed. Nothing in it needs an LLM to
supervise it.

```bash
./pi_vs_devstill_bench.sh            # starts in the background; safe to close the terminal
./pi_vs_devstill_bench.sh --status   # progress line, and each suite's state
./pi_vs_devstill_bench.sh --report   # the latest REPORT.md (written when the run ends)
./pi_vs_devstill_bench.sh --history  # one row per past campaign
./pi_vs_devstill_bench.sh --stop     # stop gracefully; finished trials are kept
./pi_vs_devstill_bench.sh --resume   # continue the latest interrupted campaign
./pi_vs_devstill_bench.sh --dry-run  # checks and plan only; records nothing
```

Options: `--suite NAME` (repeatable) or `--tag core|external` to run a subset,
`--repeats N` to change every suite's repeats, `--sampling greedy` (see below),
`--max-hours H` to stop starting new work after H hours, and `--note TEXT` to label
the campaign (say, "new model qwen4").

On WSL, keep the PC awake and leave one WSL terminal open; otherwise Windows may
stop the Linux VM in the middle of the night.

## What a campaign does

1. **Preflight.** It checks the virtualenv, the model endpoint (and which model it
   serves), Pi, pytest 8, disk space, and each suite's requirements. A missing
   model server or Pi blocks the campaign: it writes a report saying why and runs
   nothing. A missing suite requirement (Docker for SWE-bench) skips only that suite.
2. **Preparation, per suite.** If `/tmp` was wiped by a reboot, it rebuilds the pinned
   corpora (Werkzeug 3.1.3, click 8.1.8) and their CPU indexes. Building the
   Werkzeug index takes about 25 minutes. It then verifies every case: the original
   code must fail the acceptance check and the reference fix must pass it. Results
   are cached under `.devstill/bench-cache/verified/` until the dataset or the test
   environment changes. Cases that fail verification are excluded and listed in the
   report; the rest still run.
3. **Trials.** Each suite runs `benchmarks/retrieval/pi_comparison.py` in its own
   process: every case, for each arm (Pi, then Devstill, with the order alternating),
   in a fresh checkout, followed by an independent acceptance check.
4. **Failures never stop the campaign.**
   - A trial whose harness crashes (disk full, clone error) is recorded as
     `harness_error` and tried once more.
   - If the model server goes away, the run pauses and waits up to 30 minutes
     (`--endpoint-wait`). A trial whose model calls could not reach the server is
     re-run instead of counted as a failure.
   - A suite whose process dies is resumed once. Finished trials are kept, and
     resuming refuses if the code, dataset, model or Pi changed in between.
   - A suite that exceeds its own time limit is stopped, recorded and skipped.
5. **Report and history.** The campaign scores the code quality of every patch,
   writes the report, and appends one row to the git-tracked history.

## Suites

Registered in [suites.json](suites.json). Add a suite by adding an entry; the
defaults section applies to every suite.

| Suite | Cases | Trials (default) | What it measures |
| --- | ---: | ---: | --- |
| `nq` | 10 | 20 | Werkzeug quick edits (development set) |
| `quick-fast` | 10 | 20 | Werkzeug fast edits (development set) |
| `loop-dev` | 12 | 24 | Werkzeug and click cases (development set) |
| `loop-holdout` | 12 | 48 (2 repeats) | Werkzeug and click; exposed on 2026-11-08, so no longer held out |
| `swebench-small` | 24 | 48 | Real GitHub issues from SWE-bench Verified ("<15 min fix", one source file, at most 12 changed lines) in 8 pure-Python projects; graded by the official SWE-bench harness |

The four local suites take roughly 2 to 3 hours together. SWE-bench adds a few
more hours, plus the first image download.

### SWE-bench

The case list is frozen in `datasets/swebench_small.v1.json`. Its provenance file
records the source dataset revision and the selection rules. Rebuild it with
`python -m benchmarks.retrieval.swebench build`; the selection is deterministic.

It needs Docker. On WSL, open Docker Desktop → Settings → Resources → WSL
integration and enable this distro. The first campaign pulls the 24 instance
images from Docker Hub (several GB per project), copies each image's `/testbed`
checkout to `/tmp/devstill-retrieval-bench/repos/swebench/`, builds a lexical-only
index (`index.vectors: false`, because embedding Django on a CPU would take hours),
and installs the official grader (`swebench==5.0.2`) into
`.devstill/bench-cache/swebench-venv`. Check what is missing with
`python -m benchmarks.retrieval.swebench status`.

During a trial, both agents edit the host checkout. `python`, `pytest` and `pip`
on their `PATH` run inside a container of the instance image, with the checkout
mounted at `/testbed` and no network. Each agent therefore tests against the
project's real dependencies, and nothing gets installed on the host. Grading uses
the official harness: it applies the patch in a fresh container and runs the
issue's FAIL_TO_PASS and PASS_TO_PASS tests.

## Where the records are

```
.devstill/bench-history/<campaign>/      everything (local; not in git)
  campaign.json        how it was run: command, git commit (+ git.diff if dirty), model
                       and served model ids, Pi and tool versions, per-suite settings,
                       status and every attempt with its exit code
  preflight.json       environment checks
  status.txt           one-line heartbeat, updated after every trial
  campaign.log         everything the campaign printed
  REPORT.md            the report; report.json is the same data as JSON
  suites/<suite>/      pi_comparison output: protocol.json, results.json, one folder
                       per trial (worker.log, transport/ with every model request and
                       response, acceptance.json, change.patch, result.json)
  suites/<suite>.log   that suite's output
.devstill/bench-history/latest           the most recent campaign

benchmarks/history/                      compact copy, meant to be committed
  HISTORY.md           one row per campaign: status, commit, model, sampling, passes
  index.jsonl          the same as JSON lines
  <campaign>/          campaign.json, report.json, REPORT.md, and per suite the
                       protocol, summary, audit, quality scores, and compact results
```

## Reading a report

Pass means the agent finished its workflow **and** the independent acceptance
passed. For the local suites, acceptance is a hidden behaviour check plus protected
upstream tests. For SWE-bench, it is the official grader's `resolved`. The report
shows per-suite and overall pass counts for each arm, the cases where only one arm
passed, the median Devstill/Pi time ratio over cases both passed (below 1 means
Devstill is faster), changes since the previous campaign (including every case
whose outcome flipped), per-case results and code quality.

Every non-passing trial gets one category and a one-line detail, plus the path
of its trial folder:

| Category | Meaning | Usually fix |
| --- | --- | --- |
| `harness_error` | the benchmark harness itself failed (traceback in `harness-error.txt`) | harness or environment |
| `endpoint_error` | the model server could not be reached | server, network |
| `worker_crash` | the agent process exited with an error | agent (see `worker.log`) |
| `timeout` | no finished change within the time limit | agent speed or loop |
| `timeout_after_correct_edit` | the edit was right but the agent kept going | agent stopping rule |
| `no_change` | the agent changed nothing (detail says why it gave up) | agent |
| `gave_up:<stage>` | the agent reported failure, e.g. `turn_limit`, `verification` | agent |
| `incomplete_workflow` | acceptance passed but the agent reported failure | agent's own checks |
| `wrong_fix:behavior_check` | the hidden behaviour check failed | the edit |
| `wrong_fix:regression_tests` | protected upstream tests broke | the edit |
| `wrong_fix:issue_tests_fail` / `broke_existing_tests` / `patch_does_not_apply` | SWE-bench grading results | the edit |

Code quality covers only trials that changed code. It reports changed lines and
their ratio to the reference fix, files changed outside the reference's files,
test files touched, and new ruff findings in changed Python files (default rules,
project settings ignored). mypy is left out because it would need every project's
dependencies and stubs.

## How deterministic is it?

The **procedure** is fixed and audited:

- Corpora are pinned to commits; datasets are frozen files whose sha256 is recorded.
- Reference fixes are verified before any model call.
- The code under test is copied once per run, and `final-audit.json` checks that
  nothing changed during the run.
- Trial order is a fixed rotation. Every model request and response is recorded.
- Each trial uses the same checkout path, and workers run with `PYTHONHASHSEED=0`.

Grading is deterministic given the patch. The **report** is a pure function of the
raw records: `python -m benchmarks.bench_suite report <dir> --check` rebuilds it
and lists any difference.

The **agents' outcomes are not deterministic.** By default the server's sampling
settings apply, so two runs of the same code differ on some cases. With
`--sampling greedy`, the recording proxy forces temperature 0 and a fixed seed on
every request from both agents. Combined with one-at-a-time trials, this removes
most of the variation. Some remains: GPU numerics, the server's prefix cache, and
timing that leaks into tool output (pytest's "passed in 0.12s") or decides a
timeout. Judge a change by pass counts over repeats and across suites, never by
one case. Use `--repeats 3` when a decision depends on a few cases. Greedy
decoding also changes model behaviour, so compare greedy runs with greedy runs.

## For a supervising agent

A small model (or a cron job) can run this without interpreting raw logs:

1. Start: `./pi_vs_devstill_bench.sh [--note "..."]`. It returns at once.
2. Monitor: `./pi_vs_devstill_bench.sh --status`. The campaign is finished when
   the first line no longer says `running`. While running, the status line names
   the suite, the trial count and the latest result.
3. Report: read `.devstill/bench-history/latest/REPORT.md`. Summarise the Results
   table, the "Change since the previous campaign" section, and the "What went
   wrong" tables. Infrastructure problems come first; they mean the numbers may
   be incomplete.
4. Confirm: `python -m benchmarks.bench_suite report .devstill/bench-history/latest --check`
   prints `report matches the raw results` (exit 0) when the report agrees with
   the raw records.
5. Do not edit code while a campaign runs. Resuming refuses if the code changed.

## Adding models and suites

- **New model:** point `devstill.json` at it and run a campaign with a `--note`.
  The served model id is recorded, and HISTORY.md shows the model next to the
  results, so campaigns on different models compare side by side.
- **New suite:** add a verified dataset (see `benchmarks/retrieval/coding_cases.py`
  for the case format; `swebench.py` shows an external source) and an entry in
  `suites.json` with a name, dataset, tags and any overrides (`repeats`,
  `timeout`, `requires`, and `purpose`: `smoke`, `development` or `evaluation`,
  recorded in the run's protocol). Run `--dry-run` to see the plan.
- **Lower-level runs:** `python -m benchmarks.retrieval.pi_comparison run ...`
  still works on its own, with `--resume`, `--sampling` and `--endpoint-wait`.
