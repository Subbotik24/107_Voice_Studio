# Finding and report contract

## Candidate status

- `CONFIRMED BUG`: direct code/runtime evidence establishes incorrect behavior
  against a requirement or defensible invariant.
- `LIKELY ISSUE`: evidence is strong but a material premise or reproduction is
  unavailable.
- `HYPOTHESIS / NEEDS VERIFICATION`: plausible risk without enough evidence for
  a defect claim.

Keep architecture, security, privacy, performance, test-gap, technical-debt,
and style observations distinct from correctness bugs.

## Severity and confidence

- `P0`: catastrophic corruption, loss, compromise, or fundamentally invalid
  result with a credible reachable path.
- `P1`: critical, non-catastrophic production failure across correctness,
  reliability, security, privacy, data integrity, finance, or engineering
  validity.
- `P2`: significant correctness, reliability, or bounded security problem.
- `P3`: limited-impact defect.
- `P4`: technical debt or lower-priority improvement.

State confidence as `high`, `medium`, or `low` and justify it through evidence,
not tone. P0/P1 findings require independent skeptic verification. A large
potential impact with an unproven path is not automatically high severity.

## Finding schema

Use this complete schema for significant findings:

```text
ID:
STATUS:
SEVERITY:
CONFIDENCE:
CATEGORY:
LOCATION:
PROBLEM:
EVIDENCE:
REPRODUCTION:
EXPECTED:
ACTUAL:
ROOT CAUSE:
IMPACT:
FIX DIRECTION:
REGRESSION TEST:
```

`LOCATION` names exact current-revision files and tight line/function ranges.
`EVIDENCE` distinguishes observed code, command output, documentation, external
authority, and inference. `REPRODUCTION` includes command/input and exit/result,
or states why it is `BLOCKED`/`NOT RUN`. `FIX DIRECTION` describes the contract
to restore without implementing it or prescribing an unjustified rewrite.

Trace root cause as a causal chain when useful:

```text
wrong result
<- incorrect conversion
<- inconsistent unit assumption
<- boundary contract mismatch
<- missing invariant ownership
```

## Final report

Use this order, omitting only sections that are demonstrably inapplicable:

1. Executive Summary
2. System Map
3. Risk Map
4. Confirmed P0/P1 Findings
5. Confirmed P2 Findings
6. Likely Issues by Severity
7. Confirmed P3/P4 Findings
8. Mathematical / Engineering Audit
9. Data / State Audit
10. Runtime Verification
11. Test-Suite Gaps
12. Architecture Risks
13. Unverified Hypotheses
14. Recommended Remediation Order

Lead with counts by status and severity, the audited revision/scope, and the
strongest non-claims. Do not bury P0/P1 evidence in prose.

## Verification ledger

For each command or gate record:

```text
CHECK | STATUS | COMMAND/ENVIRONMENT | EXIT/RESULT | SCOPE LIMIT
```

Use:

- `PASS` only for the exact executed gate;
- `FAIL` when the gate ran and did not meet its contract;
- `BLOCKED` when a required prerequisite or authorization was unavailable;
- `NOT RUN` when the gate was intentionally not executed.

Historical CI, a different revision, fake devices, mocks, static inspection,
and generated documents must retain those scope limits.

## Final integrity statement

End the audit with:

- production code changed: `NO` or an explicit exception;
- working-tree state before and after;
- generated diagnostic artifacts and where they were isolated;
- external/native/manual gates still `BLOCKED` or `NOT RUN`;
- any conflicts between requirements, tests, and implementation.
