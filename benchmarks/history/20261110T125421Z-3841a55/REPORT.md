# Benchmark campaign 20261110T125421Z-3841a55

**Status:** completed · **Started:** 2026-11-10T12:54:21+00:00 · **Finished:** 2026-11-10T17:00:47+00:00 (4.11 h)

**Code:** bench-test-ui-ux @ 3841a55 · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** React at 240 s per trial (capability without the clock); code 5c58680; agents keep their own limits (Devstill 20 model calls)

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-dev | completed | 48/48 | 9/24 | 16/24 | 8 | 1 | 8 | 113.0 / 47.0 | 0.79 | 6/8 |
| react-holdout | completed | 48/48 | 15/24 | 11/24 | 7 | 8 | 4 | 91.5 / 51.6 | 0.6 | 6/7 |
| **All** | | 96 | **24/48** | **27/48** | 15 | 9 | 12 | 109.8 / 50.2 | **0.66** | 12/15 |

## Change since the previous campaign (20261110T112931Z-3841a55)

Previous run: model vllm-qwen38-27b, code 3841a55.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| react-holdout | 9/24 → 15/24 | 12/24 → 11/24 | 1.25 → 0.6 |

Cases whose outcome changed:

- react-holdout / zh01 (Pi): 1/2 → 2/2
- react-holdout / zh02 (Pi): 1/2 → 2/2
- react-holdout / zh02 (Devstill): 0/2 → 1/2
- react-holdout / zh03 (Devstill): 1/2 → 2/2
- react-holdout / zh04 (Pi): 0/2 → 1/2
- react-holdout / zh04 (Devstill): 0/2 → 2/2
- react-holdout / zh05 (Pi): 0/2 → 2/2
- react-holdout / zh05 (Devstill): 1/2 → 2/2
- react-holdout / zh07 (Pi): 2/2 → 1/2
- react-holdout / zh07 (Devstill): 2/2 → 1/2
- react-holdout / zh08 (Devstill): 2/2 → 0/2
- react-holdout / zh09 (Pi): 0/2 → 1/2
- react-holdout / zh09 (Devstill): 2/2 → 0/2
- react-holdout / zh11 (Pi): 2/2 → 1/2
- react-holdout / zh11 (Devstill): 1/2 → 0/2
- react-holdout / zh12 (Pi): 0/2 → 2/2

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd01-0-runtime` |
| react-dev | zd05 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-k09fujfz/vitest-report.json | `suites/react-dev/zd05-0-runtime` |
| react-dev | zd06 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-koen7ios/vitest-report.json | `suites/react-dev/zd06-0-runtime` |
| react-dev | zd06 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd06-1-runtime` |
| react-dev | zd08 | 1 | Devstill | no_change | deadline: Tests still fail: tests/thenable-check.test.ts > thenable detection > sync storages still  … able to type '(setState: { (partial: { n: number; } \| Partial<{ n: number; }> \| ((state: { | `suites/react-dev/zd08-1-runtime` |
| react-dev | zd09 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-7ne7ql0q/vitest-report.json | `suites/react-dev/zd09-0-runtime` |
| react-dev | zd09 | 1 | Devstill | no_change | turn_limit: Your latest check fails after your last edit (re-run automatically): … ReferenceError | `suites/react-dev/zd09-1-runtime` |
| react-dev | zd12 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd12-1-runtime` |
| react-holdout | zh02 | 0 | Devstill | no_change | deadline: Your latest check fails after your last edit (re-run automatically): … [2 fail | `suites/react-holdout/zh02-0-runtime` |
| react-holdout | zh06 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh06-0-runtime` |
| react-holdout | zh06 | 1 | Devstill | no_change | turn_limit: Tests still fail: tests/eqfn.test.tsx > does not call equality fn when selection reference unchanged, tests/eqfn2.test.tsx > calls equality fn when selection references differ and… | `suites/react-holdout/zh06-1-runtime` |
| react-holdout | zh07 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-5_dr2e27/vitest-report.json | `suites/react-holdout/zh07-0-runtime` |
| react-holdout | zh08 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh08-0-runtime` |
| react-holdout | zh08 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh08-1-runtime` |
| react-holdout | zh09 | 0 | Devstill | no_change | Server disconnected without sending a response. | `suites/react-holdout/zh09-0-runtime` |
| react-holdout | zh09 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh09-1-runtime` |
| react-holdout | zh10 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh10-0-runtime` |
| react-holdout | zh11 | 0 | Devstill | no_change | Server error '502 Bad Gateway' for url 'http://127.0.0.1:44119/v1/chat/completions' … For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/502 | `suites/react-holdout/zh11-0-runtime` |
| react-holdout | zh11 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh11-1-runtime` |
| react-holdout | zh12 | 0 | Devstill | no_change | deadline: Tests still fail: src/middleware/redux.ts(46,34): error TS2339: Property 'dispatch' does not exist on type 'T'., src/middleware/redux.ts(49,31): error TS2339: Property 'dispatch' … | `suites/react-holdout/zh12-0-runtime` |
| react-holdout | zh12 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-w37tvg71/vitest-report.json | `suites/react-holdout/zh12-1-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd01-1-pi` |
| react-dev | zd02 | 1 | Pi | wrong_fix:behavior_check | src/vanilla/shallow.ts(42,51): error TS2532: Object is possibly 'undefined'. | `suites/react-dev/zd02-1-pi` |
| react-dev | zd03 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd03-1-pi` |
| react-dev | zd05 | 0 | Pi | incomplete_workflow | Pi did not complete normally: exit=0, stop=toolUse | `suites/react-dev/zd05-0-pi` |
| react-dev | zd05 | 1 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-pamz2mce/vitest-report.json | `suites/react-dev/zd05-1-pi` |
| react-dev | zd06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-0-pi` |
| react-dev | zd06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-1-pi` |
| react-dev | zd08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-0-pi` |
| react-dev | zd08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-1-pi` |
| react-dev | zd09 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd09-0-pi` |
| react-dev | zd09 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd09-1-pi` |
| react-dev | zd10 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd10-0-pi` |
| react-dev | zd11 | 1 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-w76l37r7/vitest-report.json | `suites/react-dev/zd11-1-pi` |
| react-dev | zd12 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd12-0-pi` |
| react-dev | zd12 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd12-1-pi` |
| react-holdout | zh03 | 1 | Pi | incomplete_workflow | Pi did not complete normally: exit=0, stop=toolUse | `suites/react-holdout/zh03-1-pi` |
| react-holdout | zh04 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh04-1-pi` |
| react-holdout | zh06 | 0 | Pi | no_change | agent ended its turn after 2 tool call(s) without editing (stop reason: stop) | `suites/react-holdout/zh06-0-pi` |
| react-holdout | zh06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-1-pi` |
| react-holdout | zh07 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh07-1-pi` |
| react-holdout | zh08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-1-pi` |
| react-holdout | zh09 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh09-1-pi` |
| react-holdout | zh10 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh10-1-pi` |
| react-holdout | zh11 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh11-0-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 16 |
| Devstill | wrong_fix:regression_tests | 5 |
| Pi | incomplete_workflow | 2 |
| Pi | no_change | 1 |
| Pi | timeout | 14 |
| Pi | timeout_after_correct_edit | 4 |
| Pi | wrong_fix:behavior_check | 1 |
| Pi | wrong_fix:regression_tests | 2 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-dev | Pi | 23 | 97 | 8.0 | 2 | 18 | 0 |
| react-dev | Devstill | 19 | 18 | 1.83 | 0 | 1 | 0 |
| react-holdout | Pi | 23 | 77 | 8.7 | 2 | 17 | 0 |
| react-holdout | Devstill | 13 | 15 | 1.5 | 0 | 0 | 0 |

## Per case

### react-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zd01 | 1/2 | 1/2 | 31.0 | 27.5 |
| zd02 | 1/2 | 2/2 | 201.1 | 32.1 |
| zd03 | 1/2 | 2/2 | 109.8 | 78.6 |
| zd04 | 2/2 | 2/2 | 80.7 | 83.5 |
| zd05 | 0/2 | 1/2 | – | 52.6 |
| zd06 | 0/2 | 0/2 | – | – |
| zd07 | 2/2 | 2/2 | 46.5 | 39.9 |
| zd08 | 0/2 | 1/2 | – | 116.8 |
| zd09 | 0/2 | 0/2 | – | – |
| zd10 | 1/2 | 2/2 | 216.7 | 108.8 |
| zd11 | 1/2 | 2/2 | 116.2 | 25.7 |
| zd12 | 0/2 | 1/2 | – | 114.6 |

### react-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zh01 | 2/2 | 2/2 | 39.5 | 25.8 |
| zh02 | 2/2 | 1/2 | 101.3 | 68.6 |
| zh03 | 1/2 | 2/2 | 120.2 | 49.5 |
| zh04 | 1/2 | 2/2 | 122.3 | 87.4 |
| zh05 | 2/2 | 2/2 | 115.4 | 35.1 |
| zh06 | 0/2 | 0/2 | – | – |
| zh07 | 1/2 | 1/2 | 53.7 | 49.0 |
| zh08 | 1/2 | 0/2 | 89.4 | – |
| zh09 | 1/2 | 0/2 | 38.5 | – |
| zh10 | 1/2 | 1/2 | 73.7 | 115.8 |
| zh11 | 1/2 | 0/2 | 32.8 | – |
| zh12 | 2/2 | 0/2 | 151.5 | – |

## How this was run

- Command: `bench_suite.py run --tag react --repeats 2 --timeout 240 --note React at 240 s per trial (capability without the clock); code 5c58680; agents keep their own limits (Devstill 20 model calls)`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-dev: repeats 2, timeout 240.0 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_dev.v1.json`
- react-holdout: repeats 2, timeout 240.0 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_holdout.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261110T125421Z-3841a55 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
