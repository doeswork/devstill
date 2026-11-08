> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/PI_SMOKE_RESULTS.md`.

# Direct Pi benchmark integration checks — 2026-11-07

The direct baseline runs Pi 1.5.0 with one original user request and its native
coding tools, against the same Qwen endpoint and hidden evaluator as Devstill Quick.
These runs validate the benchmark, not product superiority. No full 216-attempt
three-arm campaign was started.

| Saved run | Distinct cases | Attempts | Pi passes | Quick passes | Audit / reason for next revision |
| --- | ---: | ---: | ---: | ---: | --- |
| `.devstill/pi15-direct-smoke-v1` | 2 | 4 | 2/2 | 2/2 | Initial recorder integration; source snapshot stayed intact while benchmark development continued |
| `.devstill/pi15-direct-smoke-v2` | 2 | 4 | 2/2 | 2/2 | Source retrieval SQLite files changed during queries; introduced private indexes for Quick as well as Feature |
| `.devstill/pi15-direct-smoke-v3` | 2 | 4 | 2/2 | 2/2 | Source contents stayed intact; audit noticed creation of an empty metadata WAL, now distinguished from WAL data |
| `.devstill/pi15-direct-smoke-v4` | 1 | 2 | 1/1 | 1/1 | Final source/runtime/Pi/retrieval audits all passed; working-tree source unchanged |

`w_repr` and `w_salt` are existing exposed development requests. v4 used `w_repr`
to verify the final audit changes. All runs and failed audit flags are retained;
they are not pooled into a success-rate estimate. v2 onwards restored evaluator
Werkzeug regressions from the pinned source, independently of model-edited tests.

Measured timings varied. In v3 Quick's median worker wall time was 13.15 seconds
versus Pi's 20.92 seconds across two cases. In v4 Quick took 15.56 seconds versus
Pi's 14.96 seconds on one case. This is not evidence of a reliable speed advantage.
Some test-suite validation overlapped smoke runs and could consume local CPU.
The inference server's other load and prefix-cache state were not controlled.

The tests establish that direct Pi, the normal Quick worker, HTTP streaming
recording, evaluator isolation and private-index handling function together.
The final full suite passed **504 tests, with 1 skipped**; Ruff and diff whitespace
checks passed. Focused benchmark regression checks passed **22 tests**.
The feature worker is covered by controlled composition tests; it was not included
in this live smoke campaign. The next quality experiment must use a declared fixed
case set, repeated frozen-version runs, exclusive inference-server use, and the
standards in [PI_COMPARISON.md](PI_COMPARISON.md).

Raw records remain in the ignored local directories above: protocol, frozen source,
request/response bodies, native tool events, exact commands, patches, acceptance,
per-trial results, summaries and integrity audits. Transfer these separately when
moving the benchmark to another machine.
