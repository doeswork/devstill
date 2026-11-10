# Benchmark campaign 20261110T053202Z-3d8342e

**Status:** completed · **Started:** 2026-11-10T05:32:02+00:00 · **Finished:** 2026-11-10T06:52:27+00:00 (1.34 h)

**Code:** bench-test-ui-ux @ 3d8342e · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** react-dev rerun: first attempt lost to a node_modules fingerprint false alarm; JS support 3aa4240

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-dev | completed | 48/48 | 7/24 | 9/24 | 5 | 2 | 4 | 76.6 / 71.0 | 1.0 | 3/5 |
| **All** | | 48 | **7/24** | **9/24** | 5 | 2 | 4 | 76.6 / 71.0 | **1.0** | 3/5 |

## Change since the previous campaign (20261110T040310Z-baab0f7)

Previous run: model vllm-qwen38-27b, code baab0f7.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| react-dev | 1/24 → 7/24 | 2/24 → 9/24 | 0.27 → 1.0 |

Cases whose outcome changed:

- react-dev / zd01 (Pi): 1/2 → 0/2
- react-dev / zd01 (Devstill): 1/2 → 0/2
- react-dev / zd02 (Pi): 0/2 → 2/2
- react-dev / zd03 (Pi): 0/2 → 2/2
- react-dev / zd03 (Devstill): 0/2 → 2/2
- react-dev / zd04 (Pi): 0/2 → 1/2
- react-dev / zd04 (Devstill): 0/2 → 2/2
- react-dev / zd05 (Devstill): 0/2 → 1/2
- react-dev / zd06 (Devstill): 0/2 → 1/2
- react-dev / zd07 (Pi): 0/2 → 1/2
- react-dev / zd07 (Devstill): 0/2 → 1/2
- react-dev / zd09 (Devstill): 0/2 → 1/2
- react-dev / zd11 (Pi): 0/2 → 1/2

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-tnra4foc/vitest-report.json | `suites/react-dev/zd01-0-runtime` |
| react-dev | zd01 | 1 | Devstill | no_change | turn_limit: Tests still fail: tests/devtools.test.tsx > When state changes... > sends { type: setStateName \|\| 'anonymous`, ...rest } as the action with current state, tests/devtools.test.tsx … | `suites/react-dev/zd01-1-runtime` |
| react-dev | zd02 | 1 | Devstill | no_change | turn_limit: Your latest check fails after your last edit (re-run automatically): … - 'detects repeated keys of URLSearchParams' fail the same way on the original code (same mess | `suites/react-dev/zd02-1-runtime` |
| react-dev | zd05 | 1 | Devstill | no_change | turn_limit: Tests still fail: src/traditional.ts(67,65): error TS1138: Parameter declaration expected., src/traditional.ts(70,61): error TS1005: ';' expected. | `suites/react-dev/zd05-1-runtime` |
| react-dev | zd06 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd06-0-runtime` |
| react-dev | zd07 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd07-1-runtime` |
| react-dev | zd08 | 0 | Devstill | no_change | turn_limit: Tests still fail: src/middleware/persist.ts(61,20): error TS2345: Argument of type 'string … e/persist.ts(285,12): error TS2722: Cannot invoke an object which is possibly 'undefined'. | `suites/react-dev/zd08-0-runtime` |
| react-dev | zd08 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-c5mr5bxw/vitest-report.json | `suites/react-dev/zd08-1-runtime` |
| react-dev | zd09 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd09-1-runtime` |
| react-dev | zd10 | 0 | Devstill | timeout | no finished change within the time limit | `suites/react-dev/zd10-0-runtime` |
| react-dev | zd10 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-0k1l738v/vitest-report.json | `suites/react-dev/zd10-1-runtime` |
| react-dev | zd11 | 0 | Devstill | no_change | turn_limit: Tests still fail: tests/__devstill_check.test.tsx > devtools.cleanup is a no-op when disabled (enabled: false) and never connects, tests/__devstill_check.test.tsx > devtools.clean… | `suites/react-dev/zd11-0-runtime` |
| react-dev | zd11 | 1 | Devstill | no_change | turn_limit: Your latest check fails after your last edit (re-run automatically): … [1 failed, 0 passed] | `suites/react-dev/zd11-1-runtime` |
| react-dev | zd12 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd12-0-runtime` |
| react-dev | zd12 | 1 | Devstill | no_change | turn_limit: Your latest check fails after your last edit (re-run automatically): … AssertionError: expected "spy" to be called | `suites/react-dev/zd12-1-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd01-0-pi` |
| react-dev | zd01 | 1 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-bliwsxv3/vitest-report.json | `suites/react-dev/zd01-1-pi` |
| react-dev | zd04 | 1 | Pi | wrong_fix:behavior_check | 'S' could be instantiated with an arbitrary type which could be unrelated to '(state: S) => U'. | `suites/react-dev/zd04-1-pi` |
| react-dev | zd05 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd05-0-pi` |
| react-dev | zd05 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd05-1-pi` |
| react-dev | zd06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-0-pi` |
| react-dev | zd06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-1-pi` |
| react-dev | zd07 | 1 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-itqah8av/vitest-report.json | `suites/react-dev/zd07-1-pi` |
| react-dev | zd08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-0-pi` |
| react-dev | zd08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-1-pi` |
| react-dev | zd09 | 0 | Pi | wrong_fix:behavior_check | src/middleware/subscribeWithSelector.ts(55,27): error TS2304: Cannot find name 'U'. | `suites/react-dev/zd09-0-pi` |
| react-dev | zd09 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd09-1-pi` |
| react-dev | zd10 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd10-0-pi` |
| react-dev | zd10 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd10-1-pi` |
| react-dev | zd11 | 1 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-ekrwfcxe/vitest-report.json | `suites/react-dev/zd11-1-pi` |
| react-dev | zd12 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd12-0-pi` |
| react-dev | zd12 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd12-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 11 |
| Devstill | timeout | 1 |
| Devstill | wrong_fix:regression_tests | 3 |
| Pi | timeout | 9 |
| Pi | timeout_after_correct_edit | 3 |
| Pi | wrong_fix:behavior_check | 2 |
| Pi | wrong_fix:regression_tests | 3 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-dev | Pi | 23 | 84 | 7.07 | 1 | 15 | 0 |
| react-dev | Devstill | 12 | 16.0 | 1.15 | 0 | 0 | 0 |

## Per case

### react-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zd01 | 0/2 | 0/2 | – | – |
| zd02 | 2/2 | 1/2 | 63.8 | 63.7 |
| zd03 | 2/2 | 2/2 | 102.0 | 70.4 |
| zd04 | 1/2 | 2/2 | 76.6 | 80.9 |
| zd05 | 0/2 | 1/2 | – | 50.5 |
| zd06 | 0/2 | 1/2 | – | 100.2 |
| zd07 | 1/2 | 1/2 | 18.1 | 75.7 |
| zd08 | 0/2 | 0/2 | – | – |
| zd09 | 0/2 | 1/2 | – | 99.4 |
| zd10 | 0/2 | 0/2 | – | – |
| zd11 | 1/2 | 0/2 | 58.3 | – |
| zd12 | 0/2 | 0/2 | – | – |

## How this was run

- Command: `bench_suite.py run --suite react-dev --repeats 2 --note react-dev rerun: first attempt lost to a node_modules fingerprint false alarm; JS support 3aa4240`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-dev: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_dev.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261110T053202Z-3d8342e [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
