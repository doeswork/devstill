# Constructor context and delivery repair — 2026-11-08 UTC

The native Quick runtime now supplies constructor/base context, retains checks
across repairs, and enforces recognized explicit file/module/export requests.
The bounded diagnostic recovered three previously failing cases, but generated
checks still fail on two converter requests. **General correctness parity with
Pi remains unproven.** Keep follow-up runs small.

## Implementation

- `runtime/context.py` adds Python imports, owner class signatures and constructors.
  It follows up to four classes (including the owner) in the same file or explicit relative imports,
  including aliased base names. Each support excerpt is capped at 161 lines / 8,000 characters;
  support totals at most 16,000 characters. It respects exclusions, avoids covered excerpts,
  and participates in the editor's real context budget. It does not import source
  code, scan installed packages, or ask for an extra plan/model response.
- `runtime/requirements.py` conservatively recognizes creation of explicit relative
  files, dotted Python modules in existing `src/` or flat packages, and named
  package-root exports. A singular function signature can resolve “the function.”
  It supplies initial package context and deterministic delivery checks, including
  module-file presence, importability and export identity for the new module.
  Ambiguous prose and namespace/custom package layouts are not silently inferred.
- `runtime/editor.py` runs retained checks again after repair. Omission no longer
  removes a check. Replacing a failed/untested check requires an explicit index
  and reason, saved in the transcript; a previously passing check cannot be
  retired. Runtime delivery checks cannot be replaced. This records the model's
  justification; it does not independently prove the reason is correct.
- Generated checks must contain an executed assertion or expected-exception check;
  imports, framework setup and uncalled test definitions cannot claim completion.
  This is a structural check, not proof of behavioral coverage. The original
  two-response limit and isolated prepare/saved-review/apply path remain in use.
- The generation schema requires `check_repairs` (normally an empty array), so
  the repair protocol is explicit. Decoding retains compatibility with old
  replay/client inputs. Guidance favors direct checks, preserving requested
  behavior, updating overloads, and avoiding fabricated version-history notes.

## Final bounded diagnostic

`.devstill/native-quick-context-v4`: six already-exposed development requests,
one draw per arm, same model/endpoint, Pi request-level reasoning disabled,
30-second common worker deadline. Normal native Quick prepare/reload/automatic
approval/apply; no human review time. All recorded reasoning counts are zero.
All applicable frozen source/toolchain/retrieval audits passed.

| Case | Pi: correct completed / worker seconds | Quick: correct completed / worker seconds |
| --- | --- | --- |
| nq01, whitespace entity tag (control) | pass / 12.65 | pass / 7.54 |
| nq03, semantic AnyConverter lookup | pass / 17.17 | fail / 18.97 |
| nq07, UUID converter | pass / 18.41 | fail / 17.07 |
| nq08, property plus root export | timeout, correct patch / 30.00 | pass / 12.00 |
| nq09, new module plus export | fail / 16.31 | pass / 12.46 |
| nq10, escaping with slash preservation | pass / 24.79 | pass / 7.08 |

| Metric | Pi | Quick |
| --- | ---: | ---: |
| Correct completed workflows | 4/6 | 4/6 |
| Independently correct patches | 5/6 | 4/6 |
| Total worker seconds, including failures | 119.33 | 75.13 |
| Model calls | 59 | 9 |

Only two paired completed successes: Quick/Pi wall ratios 0.596 and 0.286,
median 0.441. That is too little shared evidence for a general speed claim.
The 37% lower all-attempt worker time includes failures, not six correct solutions.
Token reporting from Pi remains incomplete; fewer calls do not prove token savings.

On these same six requests the previous fresh first pass had Quick 1/6 completed
and Pi 4/6. The current draw recovers exports, the required new module and escaping.
These are tuned, single-draw development results; this is not a fresh holdout or
proof of statistical improvement. The other four fresh-suite cases were not rerun.
Review and YOLO share this runtime, but actual TUI interaction was not retested live.

Patch review of the four successful Quick outputs found the requested files and
exports present, no unrelated source removal and no invented version notes.
Independent acceptance remains separate from the model's generated checks.

## Retained intermediate result

`.devstill/native-quick-context-v3-a`: six attempts on nq01/nq03/nq07. Quick 1/3,
Pi 3/3. Constructor support enabled a successful UUID task, but an optional repair
field was omitted during the easy control's repair, and an incorrect converter
implementation passed checks containing setup alone. Both problems informed the
required repair field and behavioral-check structural guard in v4. Those attempts
remain retained and are not pooled with the final candidate. Live work for this
change: 18 attempts total; no broad/repeated campaign was launched.

## Remaining work

1. **Ground generated checks in actual usage.** In final nq03 the model generated
   malformed check syntax and then invalid routing syntax. In nq07 it repaired
   an invented framework keyword, but supplied a wrong UUID expected value.
   Constructor information is present; more context alone is not a complete fix.
   The next small experiment should include one bounded original usage example
   for the selected API, while keeping evaluator/reference solutions private.
2. **Requirement extraction has deliberate limits.** The runtime enforces clear
   recognized deliveries, not every natural-language requirement. A model can
   still supply incorrect semantic expectations or a bad repair justification.
   Test examples such as constructors and independent project regressions remain
   necessary. Do not describe generated-check success as independent correctness.
3. **Keep static/API quality in the gate.** Signature overload consistency and
   fabricated documentation remain relevant outside these successful patches.
   The full ten-case suite and fresh cases should follow only after the two
   failing converter diagnostics become reliable.

## Verification and retained evidence

Deterministic tests cover inherited/aliased constructors, excluded base files,
module/export parsing, explicit paths, silent check removal, explicit retirement
of passing checks, protected delivery/identity checks, repairing a missing export,
behavioral-check structure and isolated source preservation. The complete suite,
protected corpus regression results and local installation verification are
recorded below after completion.

Evidence is under `.devstill/native-quick-context-v3-a` and
`.devstill/native-quick-context-v4`, including protocols, hashes, every request and
response, patches, worker/independent-acceptance timing, and final audits. The v4
`check_regressions.py` driver and `supplemental-regressions.json` retain additional
protected routing/datastructure regression checks for every output patch.

Final verification: **569 passed, 1 skipped**; Ruff and whitespace checks pass.
The preceding full test run had 568 passes and one legacy UI layout failure; that
test passed alone and the complete suite then passed on rerun without concurrent
corpus testing. No UI code was changed for this work. Every final diagnostic
output passed the 240 protected routing/datastructure regression tests, including
failed/empty patches; this supplemental pass does not override failed requested
behavior. Current source/benchmark hashes match the v4 snapshot. `./update-local.sh`
refreshed the private installation successfully, including startup/dependency and
package-integrity checks; installed runtime module hashes match the checkout and
the required repair schema is active. Reopen existing sessions to load the update.
