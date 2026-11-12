# Benchmark history

Written by `./pi_vs_devstill_bench.sh` (see [../BENCHMARKING.md](../BENCHMARKING.md)).

- `HISTORY.md`: one row per campaign, newest first (status, commit, model, sampling,
  and per suite: Pi passes, Devstill passes, median Devstill/Pi time ratio).
- `index.jsonl`: the same, as JSON lines.
- `<campaign>/`: that campaign's `REPORT.md`, `report.json`, `campaign.json`,
  `preflight.json`, and per suite its protocol, summary, final audit, code-quality
  scores and compact results.

Full artifacts (model transcripts, patches, logs) stay in
`.devstill/bench-history/<campaign>/` on the machine that ran it. Commit this folder
after a campaign to keep the record.
