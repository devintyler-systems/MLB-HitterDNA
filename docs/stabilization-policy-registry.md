# Stabilization Policy Registry

## Purpose and pipeline position

The stabilization registry is a source-attributed policy loading and evaluation
layer. Its position is after raw metric retrieval and before the candidate
filter table:

schedule -> freshness -> confirmed lineup -> raw metrics -> stabilization -> filter table -> model -> market

It evaluates only a metric key, sample type, sample size, optional season, and
an explicitly caller-loaded policy. It does not retrieve data, create a
threshold from memory, evaluate filters, calculate a model or probability, or
perform market work.

## Raw metrics versus stabilization

The Savant expected-statistics adapter returns raw `MetricObservation` records
with `stabilization_status="unverified"`. This registry can copy a later
decision into a new observation without altering its source URL, timestamp,
sample metadata, raw value, source name, or notes. Policy provenance remains in
`StabilizationDecision` and, later, in the filter-table audit result.

## Policy files and source attribution

Only local JSON policy files are accepted. A file has a `policy_file_version`
and a non-empty `policies` array. Every policy carries an opaque policy ID and
version, metric key, sample type, opaque `minimum_sample_ref`, caller-supplied
minimum sample value, source name, source URL, UTC retrieval timestamp,
rationale, and optional season window.

The registry contains no numerical stabilization thresholds. A minimum sample
value must come from a source-attributed policy file; memory-derived values are
explicitly prohibited. `data/stabilization_policy.example.json` is synthetic
and is not production evidence.

## Resolution and outcomes

Resolution requires an exact `metric_key` and exact `sample_type`. With a
season, a matching season-window policy is selected only when exactly one such
policy applies. If no season-window policy applies, exactly one unbounded policy
may serve as the fallback. Overlapping matching windows are ambiguous and return
no policy. Without a season, only exactly one unbounded policy resolves.

- `pass`: a valid finite non-negative sample is at least the matched
  policy-provided minimum.
- `fail`: a valid finite non-negative sample is below that minimum.
- `unverified`: sample metadata is missing or invalid, no exact policy matches,
  or policy resolution is ambiguous.
- `not_applicable`: a representable decision state that preserves an existing
  observation status when applied; the supplied evaluator otherwise fails
  closed as `unverified` when it cannot resolve a policy.

No rounding, string coercion, denominator inference, player-name logic, team
logic, recency, BvP, or metric-value evaluation occurs here. Required filter
logic downstream treats unverified stabilization evidence as blocking.
