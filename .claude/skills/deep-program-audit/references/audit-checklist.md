# Adaptive audit checklist

Use only the sections that the fresh system and risk maps make relevant. This
is a coverage guide, not a requirement to produce one finding per item.

## Project discovery

- Identify product type, supported environments, technology stack, entry
  points, public interfaces, modules, workflows, trust boundaries, persistence,
  integrations, background processes, queues, caches, and critical calculations.
- Trace the highest-value user journeys from input to final output and recovery.
- Locate authoritative requirements and record contradictions or stale claims.
- Inspect dependency/build manifests, configuration defaults, schemas,
  migrations, fixtures, examples, CI/release paths, and relevant recent history.
- Identify irreversible actions and assets the software must never corrupt,
  disclose, duplicate, or delete.

## Logic and data flow

- Check Boolean conditions, missing or inverted branches, impossible states,
  ordering assumptions, validation, defaults, fallbacks, duplicate processing,
  stale state, error propagation, swallowed errors, and API contracts.
- Trace suspicious output values backward to their original input and every
  transformation. Check serialization, deserialization, nullability, dates,
  time zones, locale, encoding, and identifier semantics.
- Compare behavior across UI, CLI, API, services, workers, storage, exporters,
  and recovery paths. A boundary can be wrong even when both modules look
  locally reasonable.

## State, persistence, and concurrency

When present, inspect schemas, constraints, foreign keys, uniqueness,
migrations, transactions, atomicity, rollback, idempotency, duplicate requests,
lost updates, races, orphan records, caches, derived data, timestamps, soft
delete, and restore.

For every critical operation, model: **execution stops exactly halfway**. Then
determine the durable state, what startup sees, and whether retry/restart is
safe. Check filesystem/database and remote/local transitions as one contract,
not as isolated operations.

## Cross-boundary contracts

At each boundary compare:

- units and conversion ownership;
- types, field meaning, nullability, and defaults;
- ordering, timing, retries, and idempotency;
- error shape and cancellation propagation;
- encoding, versioning, compatibility, and ownership;
- authentication/authorization assumptions when applicable.

Follow at least one high-risk path end to end, such as
`UI -> service -> calculation -> persistence -> export`.

## Runtime and adversarial verification

Discover repository-defined commands for compilation/build, unit and integration
tests, lint, type checking, static analysis, and product-specific validation.
Run safe commands and record the exact command, environment, exit code, and
relevant output. Do not promote a partial gate into a broader claim.

Choose edge cases from the actual input domain, including where relevant:

- zero, negative, null, empty, malformed, duplicate, minimum, and maximum;
- immediately below, exactly at, and immediately above a boundary;
- huge input, repeated request, stale state, concurrent operation;
- interruption, timeout, retry, restart, network loss, disk pressure, and
  partial external response.

Use property/invariant tests, fuzzing, differential testing, reference
implementations, or mutation testing when they improve evidence. Keep generated
or modified diagnostic test code outside the production working tree.

## Test-suite audit

- Find critical logic without meaningful assertions and branches with no
  failure or boundary coverage.
- Detect tests of mocks/source text rather than behavior, excessive mocking,
  happy-path-only coverage, stale fixtures, and wrong expected results.
- Check whether formula tests derive expected values from the same algorithm or
  constants as production. Require an independent oracle for critical math.
- Inspect skip conditions and CI dependency matrices: a green suite may omit an
  entire optional product path.
- Separate headless/fake evidence from real device, integration, native, cloud,
  performance, security, legal, or release acceptance.

## Conditional security pass

Apply when the project accepts external input, files, network data, secrets,
commands, authentication, databases, plugins, or executable/model artifacts.
Focus on practical impact: trust-boundary violations, authorization errors,
unsafe parsing/execution, secret leakage, path traversal, resource exhaustion,
authenticity versus checksum-only integrity, and unsafe recovery. Do not pad the
report with theoretical nitpicks.

## Conditional performance and reliability pass

Apply where resource use changes correctness or availability. Check unbounded
loops/queues, memory or handle growth, deadlocks, retry storms, N+1 behavior,
pathological complexity, blocking UI/event loops, resource cleanup, timeouts,
and realistic input ceilings. Distinguish measured behavior from static risk.

## Skeptic completion gate

For every P0-P2 candidate:

1. State the strongest disproof hypothesis.
2. Inspect callers and downstream consumers, not only the cited line.
3. Look for guards, constraints, tests, configuration, or version gates that
   invalidate the claim.
4. Reproduce or independently calculate when safe.
5. Downgrade, move to hypotheses, or remove the finding when evidence does not
   support its status, severity, or confidence.
