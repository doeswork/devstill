# Benchmark campaign 20261110T112931Z-3841a55

**Status:** completed · **Started:** 2026-11-10T11:29:31+00:00 · **Finished:** 2026-11-10T12:53:41+00:00 (1.4 h)

**Code:** bench-test-ui-ux @ 3841a55 · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** react-holdout after JS fixes 5c58680 (second look at the holdout; no tuning on it)

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-holdout | completed | 48/48 | 9/24 | 12/24 | 6 | 3 | 6 | 65.4 / 93.4 | 1.25 | 2/6 |
| **All** | | 48 | **9/24** | **12/24** | 6 | 3 | 6 | 65.4 / 93.4 | **1.25** | 2/6 |

## What went wrong

### Infrastructure (harness, model server, crashes): fix these first

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh04 | 1 | Pi | worker_crash | FileNotFoundError: [Errno 2] No such file or directory: '/tmp/devstill-pi-compare/repo/.devstill/pi-direct.jsonl' | `suites/react-holdout/zh04-1-pi` |

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh02 | 0 | Devstill | no_change | deadline: Your latest check fails after your last edit (re-run automatically): … thrown | `suites/react-holdout/zh02-0-runtime` |
| react-holdout | zh02 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh02-1-runtime` |
| react-holdout | zh03 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh03-1-runtime` |
| react-holdout | zh04 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh04-0-runtime` |
| react-holdout | zh04 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-520j02_q/vitest-report.json | `suites/react-holdout/zh04-1-runtime` |
| react-holdout | zh05 | 1 | Devstill | no_change | turn_limit: Tests still fail: tests/bind-store-check.test.tsx > createWithEqualityFn still works, tests/bind-store-check.test.tsx > useStoreWithEqualityFn still works, tests/bind-store-check.… | `suites/react-holdout/zh05-1-runtime` |
| react-holdout | zh06 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh06-0-runtime` |
| react-holdout | zh06 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh06-1-runtime` |
| react-holdout | zh10 | 0 | Devstill | no_change | deadline: Your latest check fails after your last edit (re-run automatically): … - 'setOptions({ storage }) enables | `suites/react-holdout/zh10-0-runtime` |
| react-holdout | zh11 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh11-1-runtime` |
| react-holdout | zh12 | 0 | Devstill | no_change | deadline: Tests still fail: tests/check-redux-replace.test.ts > reducer returning current state object does not notify subscribers, tests/check-redux-replace.test.ts > with devtools, each a… | `suites/react-holdout/zh12-0-runtime` |
| react-holdout | zh12 | 1 | Devstill | no_change | turn_limit: Tests still fail: tests/redux-middleware.test.tsx > reducer result replaces state, not merge, tests/redux-middleware.test.tsx(20,38): error TS2722: Cannot invoke an object which i… | `suites/react-holdout/zh12-1-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh01 | 0 | Pi | wrong_fix:behavior_check | No index signature with a parameter of type 'string' was found on type '{}'. | `suites/react-holdout/zh01-0-pi` |
| react-holdout | zh02 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh02-0-pi` |
| react-holdout | zh03 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh03-0-pi` |
| react-holdout | zh04 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh04-0-pi` |
| react-holdout | zh05 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh05-0-pi` |
| react-holdout | zh05 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh05-1-pi` |
| react-holdout | zh06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-0-pi` |
| react-holdout | zh06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-1-pi` |
| react-holdout | zh08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-1-pi` |
| react-holdout | zh09 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh09-0-pi` |
| react-holdout | zh09 | 1 | Pi | incomplete_workflow | Pi did not complete normally: exit=0, stop=toolUse | `suites/react-holdout/zh09-1-pi` |
| react-holdout | zh10 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh10-0-pi` |
| react-holdout | zh12 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh12-0-pi` |
| react-holdout | zh12 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh12-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 11 |
| Devstill | wrong_fix:regression_tests | 1 |
| Pi | incomplete_workflow | 1 |
| Pi | timeout | 6 |
| Pi | timeout_after_correct_edit | 6 |
| Pi | worker_crash | 1 |
| Pi | wrong_fix:behavior_check | 1 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-holdout | Pi | 24 | 78.0 | 7.52 | 2 | 16 | 0 |
| react-holdout | Devstill | 13 | 21 | 1.55 | 0 | 0 | 0 |

## Per case

### react-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zh01 | 1/2 | 2/2 | 33.0 | 31.6 |
| zh02 | 1/2 | 0/2 | 63.7 | – |
| zh03 | 1/2 | 1/2 | 27.4 | 45.4 |
| zh04 | 0/2 | 0/2 | – | – |
| zh05 | 0/2 | 1/2 | – | 48.5 |
| zh06 | 0/2 | 0/2 | – | – |
| zh07 | 2/2 | 2/2 | 60.6 | 63.6 |
| zh08 | 1/2 | 2/2 | 84.8 | 104.5 |
| zh09 | 0/2 | 2/2 | – | 83.3 |
| zh10 | 1/2 | 1/2 | 82.9 | 111.4 |
| zh11 | 2/2 | 1/2 | 77.2 | 84.4 |
| zh12 | 0/2 | 0/2 | – | – |

## How this was run

- Command: `bench_suite.py run --suite react-holdout --repeats 2 --note react-holdout after JS fixes 5c58680 (second look at the holdout; no tuning on it)`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-holdout: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_holdout.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261110T112931Z-3841a55 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
