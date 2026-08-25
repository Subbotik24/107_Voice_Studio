---
name: deep-program-audit
description: Deeply audit an existing software project for hidden correctness bugs, business-logic and calculation errors, invalid state transitions, integration failures, reliability defects, and inadequate tests. Use for an explicitly requested full-system correctness audit, not ordinary code review, explanation, typo fixes, or a single test.
effort: high
argument-hint: "[optional scope or special focus]"
---

# Deep program audit

Audit the current repository using this sequence:

`DISCOVER -> MODEL -> AUDIT -> VERIFY FINDINGS -> ROOT CAUSE -> REPORT`

Treat `$ARGUMENTS` as an optional scope or risk focus. If it is empty, audit the
whole repository. Do not preserve a past system map as methodology: rebuild the
map from the current checkout on every run.

## Non-negotiable boundary

- Keep the primary audit independent from implementation. Do not modify
  production code, dependencies, configuration, CI, packaging, or durable test
  files while discovering and verifying findings.
- An audit request authorizes evidence gathering and safe read-only checks, not
  fixes, migrations, releases, network side effects, or destructive commands.
- Inspect Git status before work. Preserve pre-existing changes and never stage,
  revert, or overwrite them.
- Use a disposable directory or isolated Git worktree for diagnostic test code,
  fuzz cases, generated artifacts, or mutations. Confirm the production working
  tree is unchanged afterward.
- Do not expose secrets, private user data, model binaries, absolute personal
  paths, or other sensitive artifacts in prompts or reports.
- Use `PASS`, `FAIL`, `BLOCKED`, and `NOT RUN` precisely. Static or headless
  evidence does not establish native, integration, staging, release, or human
  acceptance.

## 1. Discover the current project

Read the applicable instruction hierarchy first: root and nested `CLAUDE.md`,
`.claude/rules/`, `AGENTS.md`, and equivalent project policy files. Then inspect
the current README, specifications, architecture/design documents, source,
entry points, manifests, configuration, schemas, migrations, tests, fixtures,
examples, CI/CD, relevant Git history, and domain documentation.

Record the repository root, branch, revision, working-tree state, audit scope,
available tools, and verification commands. Do not install dependencies unless
the user explicitly authorizes it. Without authorization, use only an existing
isolated environment or record the affected gate as `BLOCKED`. If installation
is authorized, keep it disposable and report its network and environment effects.

Build two short artifacts before detailed bug hunting:

1. **System map:** modules, entry points, workflows, boundaries, data paths,
   persistence, integrations, background work, and state transitions.
2. **Risk map:** the paths where a defect has the greatest correctness,
   integrity, privacy, financial, engineering, or recovery impact.

Allocate depth by risk, not by file count. For the detailed passes, read
[references/audit-checklist.md](references/audit-checklist.md).

## 2. Model sources of truth

Classify material claims before judging implementation:

- `REQUIREMENT`: explicitly stated expected behavior.
- `INVARIANT`: a condition that must remain true.
- `IMPLEMENTATION`: observed code behavior.
- `TEST ASSUMPTION`: behavior existing tests treat as correct.
- `INFERENCE`: an auditor conclusion derived from evidence.
- `UNKNOWN`: not decidable from available evidence.

Implementation is not proof of its own correctness. Passing tests are not proof
that their expected values match requirements. Project instructions can also be
stale or wrong. Report conflicts among documentation, rules, tests, config, and
code rather than silently choosing one.

## 3. Orchestrate independent audit passes

For a non-trivial repository, the main context remains coordinator. Use only as
much delegation as the risk map justifies: normally no more than four primary
auditors plus one or two skeptic/verification passes. Prevent uncontrolled
fan-out and give each pass a distinct question, scope, and evidence contract.

Use domain-capable agents that receive all applicable instruction files found
during discovery, including `CLAUDE.md`, `AGENTS.md`, and project rules, for
logic, math, runtime, state, or security work. Use `Explore` only for neutral
file/code discovery. Require every auditor to distinguish confirmed behavior,
inference, and unknowns and to return exact locations and reproducible evidence.

Cover the applicable independent lenses:

- business logic and state transitions;
- calculations, data transformations, and domain invariants;
- persistence, concurrency, interruption, and recovery;
- cross-module and external contracts;
- runtime behavior and adversarial boundaries;
- test-suite effectiveness;
- security or performance only where they materially affect correctness or
  reliability.

Do not stop at the first bug and do not begin fixing while other passes run.

## 4. Verify candidates adversarially

Before reporting any significant candidate, ask: **Can this finding be
disproved?** Check the exact code, callers, consumers, instructions, tests,
configuration, runtime evidence, and relevant Git context independently.

Reproduce safely when practical. Record command, exit code, result, and
environment. Test boundaries and failure paths, not only happy paths. When a
claim depends on unavailable hardware, credentials, trained weights, external
services, or human acceptance, label it `BLOCKED` or `NOT RUN`; do not infer a
PASS.

Classify each candidate as `CONFIRMED BUG`, `LIKELY ISSUE`, or
`HYPOTHESIS / NEEDS VERIFICATION`. Remove disproved candidates from confirmed
findings. High severity requires strong, direct evidence and a credible impact
path.

## 5. Find the actionable root cause

Trace a wrong output or state backward through its data flow to the deepest
practical cause: conversion, assumption, boundary contract, missing invariant,
or architectural ownership gap. Group multiple manifestations when one cause
explains them. Do not stop at the line where the symptom appears.

Whenever discovery identifies any domain-critical calculation, read
[references/calculations-and-invariants.md](references/calculations-and-invariants.md)
and inventory every high-risk item. Independently compute critical results
rather than echoing implementation.

## 6. Report evidence, not volume

Read [references/finding-and-report-format.md](references/finding-and-report-format.md)
before drafting the report. Prefer a small number of verified consequential
findings over a long list of suspicions or style observations.

Unless the user explicitly asks for a durable report, return it in the
conversation and leave the repository unchanged. End with the production-tree
status and a verification ledger. Recommended remediation order is advisory;
do not implement it during the audit.
