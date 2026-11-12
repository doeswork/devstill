# Benchmark campaign 20261111T035921Z-b0f9490

**Status:** completed · **Started:** 2026-11-11T03:59:21+00:00 · **Finished:** 2026-11-11T06:25:13+00:00 (2.43 h)

**Code:** bench-test-ui-ux @ b0f9490 · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** A/B: Devstill 5c58680 (baseline) vs b0f9490 (round 2), react-dev, 120 s, 4 repeats

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-dev | completed | 96/96 | –/– | 32/48 | 0 | 0 | 0 | – / – | – | 0/0 |
| **All** | | 96 | **–/–** | **32/48** | 0 | 0 | 0 | – / – | **–** | 0/0 |

## Change since the previous campaign (20261110T125421Z-3841a55)

Previous run: model vllm-qwen38-27b, code 3841a55.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| react-dev | – → – | 16/24 → 32/48 | 0.79 → – |

Cases whose outcome changed:

- react-dev / zd01 (Devstill): 1/2 → 3/4
- react-dev / zd02 (Devstill): 2/2 → 3/4
- react-dev / zd03 (Devstill): 2/2 → 3/4
- react-dev / zd05 (Devstill): 1/2 → 4/4
- react-dev / zd08 (Devstill): 1/2 → 0/4
- react-dev / zd12 (Devstill): 1/2 → 3/4

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 2 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-ujxteez4/vitest-report.json | `suites/react-dev/zd01-2-runtime` |
| react-dev | zd02 | 3 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-48pekxcx/vitest-report.json | `suites/react-dev/zd02-3-runtime` |
| react-dev | zd03 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-nu56gn70/vitest-report.json | `suites/react-dev/zd03-0-runtime` |
| react-dev | zd06 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-8mcffiuq/vitest-report.json | `suites/react-dev/zd06-0-runtime` |
| react-dev | zd06 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd06-1-runtime` |
| react-dev | zd06 | 2 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd06-2-runtime` |
| react-dev | zd06 | 3 | Devstill | no_change | turn_limit: Tests still fail: src/traditional.ts(49,37): error TS2554: Expected 1 arguments, but got 0 … State' could be instantiated with an arbitrary type which could be unrelated to 'unknown'. | `suites/react-dev/zd06-3-runtime` |
| react-dev | zd08 | 0 | Devstill | no_change | deadline: Tests still fail: src/middleware/persist.ts(278,45): error TS2339: Property 'version' does not exist on type '{}'., src/middleware/persist.ts(279,38): error TS2339: Property 'vers… | `suites/react-dev/zd08-0-runtime` |
| react-dev | zd08 | 1 | Devstill | no_change | deadline: Your latest check fails after your last edit (re-run automatically): … your current, edited code (your files are unchanged). For comparison, the same module also | `suites/react-dev/zd08-1-runtime` |
| react-dev | zd08 | 2 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd08-2-runtime` |
| react-dev | zd08 | 3 | Devstill | no_change | deadline: Your latest check fails after your last edit (re-run automatically): … FAIL hydrates sync and thenable storage | `suites/react-dev/zd08-3-runtime` |
| react-dev | zd09 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-b1erqlai/vitest-report.json | `suites/react-dev/zd09-0-runtime` |
| react-dev | zd09 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-9h1xundf/vitest-report.json | `suites/react-dev/zd09-1-runtime` |
| react-dev | zd09 | 2 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd09-2-runtime` |
| react-dev | zd09 | 3 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-mfst46qv/vitest-report.json | `suites/react-dev/zd09-3-runtime` |
| react-dev | zd12 | 2 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-yad0h7zo/vitest-report.json | `suites/react-dev/zd12-2-runtime` |

### Other arms

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 0 | baseline | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd01-0-baseline` |
| react-dev | zd02 | 0 | baseline | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-zrug5s5y/vitest-report.json | `suites/react-dev/zd02-0-baseline` |
| react-dev | zd02 | 1 | baseline | no_change | deadline: Your latest check fails after your last edit (re-run automatically): … ed code (your files are unchanged). For comparison, the same module also ran on a pristine | `suites/react-dev/zd02-1-baseline` |
| react-dev | zd03 | 0 | baseline | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd03-0-baseline` |
| react-dev | zd03 | 2 | baseline | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-54c043cq/vitest-report.json | `suites/react-dev/zd03-2-baseline` |
| react-dev | zd05 | 0 | baseline | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-kelh1utk/vitest-report.json | `suites/react-dev/zd05-0-baseline` |
| react-dev | zd06 | 1 | baseline | no_change | deadline: Tests still fail: tests/basic.test.tsx > can throw an error in equality checker | `suites/react-dev/zd06-1-baseline` |
| react-dev | zd06 | 2 | baseline | no_change | deadline: Tests still fail: tests/devstill_traditional_extra.test.tsx > (module failed to load) | `suites/react-dev/zd06-2-baseline` |
| react-dev | zd06 | 3 | baseline | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-7zjeigdb/vitest-report.json | `suites/react-dev/zd06-3-baseline` |
| react-dev | zd08 | 0 | baseline | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd08-0-baseline` |
| react-dev | zd08 | 1 | baseline | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd08-1-baseline` |
| react-dev | zd08 | 2 | baseline | no_change | deadline: Tests still fail: src/middleware/persist.ts(56,20): error TS2345: Argument of type 'string … ts(281,13): error TS2345: Argument of type '(deserializedStorageValue: StorageValue<T> \| … | `suites/react-dev/zd08-2-baseline` |
| react-dev | zd09 | 0 | baseline | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd09-0-baseline` |
| react-dev | zd09 | 1 | baseline | no_change | turn_limit: No verified change within 20 model calls | `suites/react-dev/zd09-1-baseline` |
| react-dev | zd09 | 2 | baseline | no_change | No changed source produced | `suites/react-dev/zd09-2-baseline` |
| react-dev | zd09 | 3 | baseline | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd09-3-baseline` |
| react-dev | zd10 | 0 | baseline | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-xm7tygz8/vitest-report.json | `suites/react-dev/zd10-0-baseline` |
| react-dev | zd10 | 1 | baseline | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd10-1-baseline` |
| react-dev | zd10 | 2 | baseline | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd10-2-baseline` |
| react-dev | zd12 | 0 | baseline | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd12-0-baseline` |
| react-dev | zd12 | 1 | baseline | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd12-1-baseline` |
| react-dev | zd12 | 2 | baseline | no_change | deadline: Your latest check fails after your last edit (re-run automatically): … + Received | `suites/react-dev/zd12-2-baseline` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 8 |
| Devstill | wrong_fix:regression_tests | 8 |
| baseline | no_change | 17 |
| baseline | wrong_fix:regression_tests | 5 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-dev | baseline | 31 | 15 | 1.33 | 0 | 0 | 0 |
| react-dev | Devstill | 40 | 20.0 | 1.1 | 0 | 0 | 0 |

## Per case

### react-dev

| Case | baseline passed | Devstill passed | baseline median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zd01 | 3/4 | 3/4 | 30.2 | 31.0 |
| zd02 | 2/4 | 3/4 | 66.9 | 27.7 |
| zd03 | 2/4 | 3/4 | 39.2 | 60.3 |
| zd04 | 4/4 | 4/4 | 70.0 | 50.8 |
| zd05 | 3/4 | 4/4 | 41.6 | 73.0 |
| zd06 | 1/4 | 0/4 | 106.3 | – |
| zd07 | 4/4 | 4/4 | 75.5 | 66.3 |
| zd08 | 1/4 | 0/4 | 56.4 | – |
| zd09 | 0/4 | 0/4 | – | – |
| zd10 | 1/4 | 4/4 | 59.3 | 111.2 |
| zd11 | 4/4 | 4/4 | 23.5 | 21.8 |
| zd12 | 1/4 | 3/4 | 35.3 | 76.1 |

## How this was run

- Command: `bench_suite.py run --suite react-dev --variants baseline,runtime --baseline-ref 5c58680 --repeats 4 --note A/B: Devstill 5c58680 (baseline) vs b0f9490 (round 2), react-dev, 120 s, 4 repeats`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-dev: repeats 4, timeout 120 s, arms baseline,runtime, dataset `benchmarks/retrieval/zustand_dev.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261111T035921Z-b0f9490 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
