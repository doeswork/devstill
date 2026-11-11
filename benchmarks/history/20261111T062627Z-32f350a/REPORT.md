# Benchmark campaign 20261111T062627Z-32f350a

**Status:** completed · **Started:** 2026-11-11T06:26:27+00:00 · **Finished:** 2026-11-11T07:46:46+00:00 (1.34 h)

**Code:** bench-test-ui-ux @ 32f350a · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** holdout after round 2 (b0f9490), 120 s

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-holdout | completed | 48/48 | 9/24 | 17/24 | 7 | 2 | 10 | 64.0 / 28.0 | 0.61 | 7/7 |
| **All** | | 48 | **9/24** | **17/24** | 7 | 2 | 10 | 64.0 / 28.0 | **0.61** | 7/7 |

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh01 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-h0ligekr/vitest-report.json | `suites/react-holdout/zh01-0-runtime` |
| react-holdout | zh04 | 0 | Devstill | no_change | deadline: Tests still fail: src/react.ts(61,7): error TS18048: 'ref.current' is possibly 'undefined'., src/react.ts(62,7): error TS18048: 'ref.current' is possibly 'undefined'., src/react.t… | `suites/react-holdout/zh04-0-runtime` |
| react-holdout | zh04 | 1 | Devstill | no_change | turn_limit: No verified change within 30 model calls | `suites/react-holdout/zh04-1-runtime` |
| react-holdout | zh06 | 0 | Devstill | no_change | deadline: Tests still fail: tests/basic.test.tsx > can throw an error in equality checker, tests/basic.test.tsx > can update the equality checker | `suites/react-holdout/zh06-0-runtime` |
| react-holdout | zh09 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-u9exel2s/vitest-report.json | `suites/react-holdout/zh09-1-runtime` |
| react-holdout | zh10 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh10-0-runtime` |
| react-holdout | zh12 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-x1zj7gdx/vitest-report.json | `suites/react-holdout/zh12-0-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh01 | 1 | Pi | wrong_fix:behavior_check | No index signature with a parameter of type 'string' was found on type '{}'. | `suites/react-holdout/zh01-1-pi` |
| react-holdout | zh02 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh02-1-pi` |
| react-holdout | zh04 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh04-0-pi` |
| react-holdout | zh04 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh04-1-pi` |
| react-holdout | zh05 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh05-1-pi` |
| react-holdout | zh06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-0-pi` |
| react-holdout | zh06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-1-pi` |
| react-holdout | zh08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-0-pi` |
| react-holdout | zh08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-1-pi` |
| react-holdout | zh09 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh09-0-pi` |
| react-holdout | zh09 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh09-1-pi` |
| react-holdout | zh10 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh10-0-pi` |
| react-holdout | zh10 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh10-1-pi` |
| react-holdout | zh11 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh11-0-pi` |
| react-holdout | zh12 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh12-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 4 |
| Devstill | wrong_fix:regression_tests | 3 |
| Pi | timeout | 8 |
| Pi | timeout_after_correct_edit | 6 |
| Pi | wrong_fix:behavior_check | 1 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-holdout | Pi | 24 | 64.0 | 5.27 | 1 | 14 | 0 |
| react-holdout | Devstill | 20 | 14.0 | 1.17 | 1 | 0 | 0 |

## Per case

### react-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zh01 | 1/2 | 1/2 | 86.9 | 13.4 |
| zh02 | 1/2 | 2/2 | 64.0 | 43.9 |
| zh03 | 2/2 | 2/2 | 108.0 | 36.0 |
| zh04 | 0/2 | 0/2 | – | – |
| zh05 | 1/2 | 2/2 | 44.9 | 34.7 |
| zh06 | 0/2 | 1/2 | – | 111.4 |
| zh07 | 2/2 | 2/2 | 25.5 | 17.3 |
| zh08 | 0/2 | 2/2 | – | 95.6 |
| zh09 | 0/2 | 1/2 | – | 101.6 |
| zh10 | 0/2 | 1/2 | – | 79.1 |
| zh11 | 1/2 | 2/2 | 64.4 | 67.0 |
| zh12 | 1/2 | 1/2 | 57.0 | 89.4 |

## How this was run

- Command: `bench_suite.py run --suite react-holdout --repeats 2 --note holdout after round 2 (b0f9490), 120 s`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-holdout: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_holdout.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261111T062627Z-32f350a [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
