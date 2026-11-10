# Benchmark campaign 20261110T100311Z-b09dfad

**Status:** completed · **Started:** 2026-11-10T10:03:11+00:00 · **Finished:** 2026-11-10T11:28:29+00:00 (1.42 h)

**Code:** bench-test-ui-ux @ b09dfad · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** react-dev after JS fixes 5c58680 (read-only store, per-trial audit)

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-dev | completed | 48/48 | 8/24 | 12/24 | 8 | 0 | 4 | 96.4 / 43.7 | 0.43 | 5/8 |
| **All** | | 48 | **8/24** | **12/24** | 8 | 0 | 4 | 96.4 / 43.7 | **0.43** | 5/8 |

## Change since the previous campaign (20261110T053202Z-3d8342e)

Previous run: model vllm-qwen38-27b, code 3d8342e.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| react-dev | 7/24 → 8/24 | 9/24 → 12/24 | 1.0 → 0.43 |

Cases whose outcome changed:

- react-dev / zd01 (Pi): 0/2 → 1/2
- react-dev / zd01 (Devstill): 0/2 → 1/2
- react-dev / zd02 (Devstill): 1/2 → 2/2
- react-dev / zd03 (Pi): 2/2 → 0/2
- react-dev / zd03 (Devstill): 2/2 → 1/2
- react-dev / zd04 (Pi): 1/2 → 2/2
- react-dev / zd05 (Pi): 0/2 → 1/2
- react-dev / zd05 (Devstill): 1/2 → 2/2
- react-dev / zd06 (Devstill): 1/2 → 0/2
- react-dev / zd07 (Pi): 1/2 → 0/2
- react-dev / zd07 (Devstill): 1/2 → 2/2
- react-dev / zd09 (Devstill): 1/2 → 0/2
- react-dev / zd11 (Pi): 1/2 → 2/2
- react-dev / zd11 (Devstill): 0/2 → 2/2

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd01-0-runtime` |
| react-dev | zd03 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-g5dnx_xn/vitest-report.json | `suites/react-dev/zd03-1-runtime` |
| react-dev | zd06 | 0 | Devstill | no_change | deadline: Tests still fail: tests/debug.test.tsx(5,1): error TS2593: Cannot find name 'describe'. Do you need to install type definitions for a test runner? Try `npm i --save-dev @types/jes… | `suites/react-dev/zd06-0-runtime` |
| react-dev | zd06 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd06-1-runtime` |
| react-dev | zd08 | 0 | Devstill | no_change | deadline: Tests still fail: src/middleware/persist.ts(181,27): error TS2345: Argument of type 'Thena … an arbitrary type which could be unrelated to 'Thenable<V>'., src/middleware/persist.ts(19 | `suites/react-dev/zd08-0-runtime` |
| react-dev | zd08 | 1 | Devstill | no_change | turn_limit: Tests still fail: src/middleware/persist.ts(43,5): error TS2322: Type '(name: string) => S … eValue<S>>' is not assignable to type 'StorageValue<S> \| Promise<StorageValue<S> \| null> \| | `suites/react-dev/zd08-1-runtime` |
| react-dev | zd09 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd09-0-runtime` |
| react-dev | zd09 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-cxs75xh5/vitest-report.json | `suites/react-dev/zd09-1-runtime` |
| react-dev | zd10 | 0 | Devstill | no_change | deadline: Tests still fail: src/middleware/persist.ts(286,17): error TS2322: Type 'Promise<[false, unknown] \| [true, unknown]>' is not assignable to type '[false, unknown] \| [true, unknown]… | `suites/react-dev/zd10-0-runtime` |
| react-dev | zd10 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd10-1-runtime` |
| react-dev | zd12 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-j55g5sox/vitest-report.json | `suites/react-dev/zd12-0-runtime` |
| react-dev | zd12 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd12-1-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd01-0-pi` |
| react-dev | zd03 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd03-0-pi` |
| react-dev | zd03 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd03-1-pi` |
| react-dev | zd05 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd05-0-pi` |
| react-dev | zd06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-0-pi` |
| react-dev | zd06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-1-pi` |
| react-dev | zd07 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd07-0-pi` |
| react-dev | zd07 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd07-1-pi` |
| react-dev | zd08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-0-pi` |
| react-dev | zd08 | 1 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-fc7ejm_z/vitest-report.json | `suites/react-dev/zd08-1-pi` |
| react-dev | zd09 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd09-0-pi` |
| react-dev | zd09 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd09-1-pi` |
| react-dev | zd10 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd10-0-pi` |
| react-dev | zd10 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd10-1-pi` |
| react-dev | zd12 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd12-0-pi` |
| react-dev | zd12 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd12-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 9 |
| Devstill | wrong_fix:regression_tests | 3 |
| Pi | timeout | 10 |
| Pi | timeout_after_correct_edit | 5 |
| Pi | wrong_fix:regression_tests | 1 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-dev | Pi | 24 | 101.5 | 11.24 | 6 | 20 | 0 |
| react-dev | Devstill | 15 | 16 | 1.39 | 0 | 0 | 0 |

## Per case

### react-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zd01 | 1/2 | 1/2 | 105.3 | 25.6 |
| zd02 | 2/2 | 2/2 | 75.6 | 47.7 |
| zd03 | 0/2 | 1/2 | – | 103.8 |
| zd04 | 2/2 | 2/2 | 66.5 | 81.6 |
| zd05 | 1/2 | 2/2 | 101.4 | 49.2 |
| zd06 | 0/2 | 0/2 | – | – |
| zd07 | 0/2 | 2/2 | – | 49.0 |
| zd08 | 0/2 | 0/2 | – | – |
| zd09 | 0/2 | 0/2 | – | – |
| zd10 | 0/2 | 0/2 | – | – |
| zd11 | 2/2 | 2/2 | 93.5 | 24.3 |
| zd12 | 0/2 | 0/2 | – | – |

## How this was run

- Command: `bench_suite.py run --suite react-dev --repeats 2 --note react-dev after JS fixes 5c58680 (read-only store, per-trial audit)`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-dev: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_dev.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261110T100311Z-b09dfad [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
