# Benchmark campaign 20261111T074709Z-32f350a

**Status:** completed · **Started:** 2026-11-11T07:47:09+00:00 · **Finished:** 2026-11-11T09:41:24+00:00 (1.9 h)

**Code:** bench-test-ui-ux @ 32f350a · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** holdout after round 2 (b0f9490), 240 s; Devstill gets the trial budget

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-holdout | completed | 48/48 | 16/24 | 18/24 | 14 | 2 | 4 | 86.7 / 65.5 | 0.84 | 10/14 |
| **All** | | 48 | **16/24** | **18/24** | 14 | 2 | 4 | 86.7 / 65.5 | **0.84** | 10/14 |

## Change since the previous campaign (20261111T062627Z-32f350a)

Previous run: model vllm-qwen38-27b, code 32f350a.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| react-holdout | 9/24 → 16/24 | 17/24 → 18/24 | 0.61 → 0.84 |

Cases whose outcome changed:

- react-holdout / zh01 (Devstill): 1/2 → 2/2
- react-holdout / zh02 (Pi): 1/2 → 2/2
- react-holdout / zh03 (Devstill): 2/2 → 1/2
- react-holdout / zh04 (Pi): 0/2 → 1/2
- react-holdout / zh04 (Devstill): 0/2 → 1/2
- react-holdout / zh05 (Pi): 1/2 → 2/2
- react-holdout / zh06 (Devstill): 1/2 → 0/2
- react-holdout / zh08 (Pi): 0/2 → 1/2
- react-holdout / zh09 (Pi): 0/2 → 2/2
- react-holdout / zh10 (Pi): 0/2 → 1/2
- react-holdout / zh12 (Devstill): 1/2 → 2/2

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh03 | 0 | Devstill | wrong_fix:behavior_check | tests/vanilla/basic.test.ts(77,10): error TS2722: Cannot invoke an object which is possibly 'undefined'. | `suites/react-holdout/zh03-0-runtime` |
| react-holdout | zh04 | 0 | Devstill | no_change | turn_limit: Tests still fail: tests/basic.test.tsx > can throw an error in selector, tests/basic.test.tsx > only re-renders if selected state has changed, tests/shallow.test.tsx > useShallow … | `suites/react-holdout/zh04-0-runtime` |
| react-holdout | zh06 | 0 | Devstill | no_change | deadline: Tests still fail: tests/basic.test.tsx > can call useBoundStore with progressively more arguments, tests/basic.test.tsx > can throw an error in equality checker, tests/basic.test.… | `suites/react-holdout/zh06-0-runtime` |
| react-holdout | zh06 | 1 | Devstill | no_change | deadline: Tests still fail: tests/basic.test.tsx > can call useBoundStore with progressively more arguments, tests/basic.test.tsx > can throw an error in equality checker, tests/basic.test.… | `suites/react-holdout/zh06-1-runtime` |
| react-holdout | zh09 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-bspab3l5/vitest-report.json | `suites/react-holdout/zh09-0-runtime` |
| react-holdout | zh10 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-ur2zv1w4/vitest-report.json | `suites/react-holdout/zh10-0-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh01 | 1 | Pi | wrong_fix:behavior_check | No index signature with a parameter of type 'string' was found on type '{}'. | `suites/react-holdout/zh01-1-pi` |
| react-holdout | zh04 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh04-0-pi` |
| react-holdout | zh06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-0-pi` |
| react-holdout | zh06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-1-pi` |
| react-holdout | zh08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-0-pi` |
| react-holdout | zh10 | 0 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-n0u12tuq/vitest-report.json | `suites/react-holdout/zh10-0-pi` |
| react-holdout | zh11 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh11-0-pi` |
| react-holdout | zh12 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh12-0-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 3 |
| Devstill | wrong_fix:behavior_check | 1 |
| Devstill | wrong_fix:regression_tests | 2 |
| Pi | timeout | 5 |
| Pi | timeout_after_correct_edit | 1 |
| Pi | wrong_fix:behavior_check | 1 |
| Pi | wrong_fix:regression_tests | 1 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-holdout | Pi | 24 | 59.0 | 5.63 | 2 | 13 | 0 |
| react-holdout | Devstill | 21 | 12 | 1.2 | 1 | 1 | 0 |

## Per case

### react-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zh01 | 1/2 | 2/2 | 21.9 | 38.1 |
| zh02 | 2/2 | 2/2 | 69.4 | 67.2 |
| zh03 | 2/2 | 1/2 | 63.6 | 25.6 |
| zh04 | 1/2 | 1/2 | 157.7 | 213.7 |
| zh05 | 2/2 | 2/2 | 101.4 | 39.9 |
| zh06 | 0/2 | 0/2 | – | – |
| zh07 | 2/2 | 2/2 | 31.9 | 20.7 |
| zh08 | 1/2 | 2/2 | 78.7 | 86.3 |
| zh09 | 2/2 | 1/2 | 150.2 | 102.9 |
| zh10 | 1/2 | 1/2 | 183.9 | 85.2 |
| zh11 | 1/2 | 2/2 | 49.8 | 96.9 |
| zh12 | 1/2 | 2/2 | 137.0 | 106.1 |

## How this was run

- Command: `bench_suite.py run --suite react-holdout --repeats 2 --timeout 240 --note holdout after round 2 (b0f9490), 240 s; Devstill gets the trial budget`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-holdout: repeats 2, timeout 240.0 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_holdout.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261111T074709Z-32f350a [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
