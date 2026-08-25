# Calculations and invariants

Use this reference when the risk map identifies mathematical, engineering,
financial, statistical, signal-processing, scheduling, sizing, or aggregation
logic. Do not treat code comments, tests, or the implementation itself as an
independent authority.

## Calculation record

Before selecting probes, inventory every domain-critical calculation found in
code, tests, specifications, and calculation registers. For each high-risk item
record exact scoped subchecks as `PASS`, `FAIL`, `BLOCKED`, or `NOT RUN` and the
reason. If arithmetic passes but runtime validation is unavailable, use separate
`PASS` and `BLOCKED` subchecks instead of an ambiguous partial status. A
user-requested minimum number of independent checks is a floor, not a stopping
condition; do not silently omit an identified high-risk formula.

For each critical calculation capture:

1. inputs and their sources;
2. units, scale, and representation;
3. valid domain and invalid values;
4. implementation formula and operation order;
5. independently sourced or derived expected formula;
6. coefficients, constants, conversions, and their provenance;
7. precision, numeric type, and rounding stage;
8. boundary and representative values;
9. downstream consumers and impact of error;
10. independent calculation or differential result.

If no authoritative domain source is available, label the expected formula as
`INFERENCE` or `UNKNOWN`; do not manufacture authority.

## Independent verification

- Re-derive the result from requirements or a domain source without calling the
  production function and without copying its algebra into the oracle.
- For physical/engineering formulas, perform dimensional analysis, sign checks,
  order-of-magnitude checks, and physical-bound checks.
- For financial formulas, identify rate period, compounding convention, cash-flow
  timing, tax/currency assumptions, and rounding ownership.
- For aggregations/statistics, inspect population/sample definitions, weighting,
  missing data, denominators, grouping, and order dependence.
- For signal/data transforms, compare training and inference preprocessing,
  sampling, windowing, normalization, clipping, and quantization contracts.
- For discretization, timestamps, bins, or token grids, verify that encode and
  decode stay inside the declared domain at both endpoints, the step is finite
  and positive, and the maximum is representable under the rounding rule.
- For loss functions and optimizers, derive mathematical feasibility conditions
  and check whether masks, clipping, ignored values, or zero-on-error options can
  silently remove invalid samples or gradients.

## Conversion and numeric hazards

Explicitly test relevant conversions and representations:

- W versus kW and Wh versus kWh;
- seconds, minutes, and hours;
- mm, cm, and m;
- percentage `20` versus fraction `0.20`;
- currency minor versus major units;
- local time versus UTC and calendar versus fixed durations;
- repeated/missing conversion and conversion at the wrong boundary;
- premature or cumulative rounding and non-associative aggregation;
- integer division, overflow, underflow, cancellation, and precision loss;
- zero denominators, `NaN`, positive/negative infinity, and signed zero.

For IEEE floating-point inputs, ordinary comparisons do not reject `NaN`.
Verify explicit finiteness where non-finite values are outside the domain.

## Invariant catalogue

Derive project-specific invariants and test them across boundaries. Examples:

```text
total >= 0
part <= total
sum(parts) approximately equals total
0 <= probability_or_efficiency <= 1
output(0) = 0 when the domain requires it
end >= start
state_version never decreases
retry does not duplicate a committed effect
round_trip(decode(encode(x))) preserves the defined information
```

Define the tolerance and reason for approximate equality. Avoid a generic
epsilon detached from scale or domain requirements.

## Evidence rules

A passing production test is evidence only if its expected value is independent.
Record the oracle/source, input vector, production result, reference result,
difference, tolerance, and verdict. When weights, licensed datasets, calibrated
benchmarks, or physical equipment are absent, separate static formula validity
from unverified model/product accuracy.
