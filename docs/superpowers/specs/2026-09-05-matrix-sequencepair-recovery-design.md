# Matrix SequencePair Recovery Design

## Goal

Recover the two repeatable `universe-matrix` SequencePair deadline refusals and remove the `no-proliferator` area regression without changing the 30-second cell budget, weakening validation, or changing legal Matrix Lab construction.

## Authoritative baseline

Branch `matrix-lab-stacking` at `00d3e1c` is the implementation baseline. Its three-round evidence is under `docs/superpowers/evidence/2026-09-04-matrix-lab-stacking/`.

- `all-products` refuses in all three rounds; its best exact attempt strands two nets.
- `output-products` refuses in all three rounds; its best exact attempt strands one net.
- `no-proliferator` is clean at median area `18,096`, versus baseline `17,654`.
- Forced compact reproduces current behavior.
- Forced unstacked fails correctness and area gates.
- Increased arrangement admission and generally deeper routing have already failed their measurement gates.

## Decision

First identify the stable semantic identities and failure kinds of the recurring stranded nets. Then substitute a bounded topology-preserving candidate for a lower-value scheduled candidate. Do not add time, attempts, or a new general search layer.

A topology-preserving candidate may change only existing legal outer-search choices:

- Matrix strip partition/column grouping already representable by `StripFamily` and `StripVariant`;
- width/height envelope;
- boundary lane/port exit arrangement already represented by the selected variant;
- sequence-pair relation to the failed endpoints.

It must preserve:

- physical Lab count and exact rates;
- save-specific stack limit;
- legal column heights;
- immediate support chain links;
- root-only sorter connections;
- authoritative flank-output row and sorter anchors;
- cargo domain, belt stack, minimum belt tier, and projection constraints.

No new unstacked mode, forced topology default, validation exception, budget increase, candidate-count increase, retry loop, or generic plugin system is allowed.

## Diagnostic contract

The diagnostic record must use stable semantic identities, not stage-local strip ordinals. For each best failing exact attempt it records:

- candidate policy and round;
- selected `StripInstanceId`/`StripVariantId` identities for endpoint owners;
- logical `NetId`, item, source/destination roles, and route failure kind;
- source/destination access cells or typed reservations;
- decoded width, used height, pre-route area, and final detailed status;
- stage, restart, elapsed seconds, and remaining seconds;
- whether the same relation/variant succeeds in another policy.

Split/merge operations must not make the same semantic failure appear as a different failure. Telemetry is observational: default runs do not pay serialization or identity-construction cost unless the audit diagnostic mode is enabled.

## Candidate substitution contract

A recovery candidate is admitted only when its stable failure signature matches one of the two measured recurring Universe Matrix signatures. It replaces one lower-value scheduled restart or variant state; total scheduled restarts, expansion allowance, and wall budget remain unchanged.

At most three recovery candidates are available for one failing state:

1. current compact control relation;
2. a distinct legal Matrix boundary/envelope variant addressing the failed endpoint access;
3. a distinct relation placement for the same exact variants.

Byte-identical decoded states and identical typed-port topology are deduplicated. Candidate scoring remains existing area/routing scoring. The recovery mechanism may prioritize a candidate but cannot certify it; detailed routing, power, finalization, projection, and full validation remain authoritative.

## Gates

### Focused gate

Run `universe-matrix` with SequencePair, all three candidate policies, three independent rounds, budget 30 seconds, identical affinity/workers/assets/load, and no unrelated suites during timing.

Required:

- all nine cells clean;
- zero invalid/crash;
- `no-proliferator` median area at most `17,654`;
- no deadline or worker-count increase;
- exact physical counts and rates unchanged.

### Promotion gate

Run the existing 18-cell Matrix gate for three rounds.

Required:

- `18/18` clean each round;
- zero invalid/crash/not-run;
- no regression of a previously clean cell;
- no material p95 runtime regression;
- full Python tests, Ruff, MyPy, web tests/typecheck, production build, and two independent reviews pass.

If the focused gate fails, retain the diagnostic evidence and stop. The next hypothesis would be an exact last-mile repair for the measured one/two-net signatures, not broader search.
