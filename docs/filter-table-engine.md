# Candidate Filter Table Engine

## Purpose and boundary

The Candidate Filter Table is an immutable audit and eligibility layer. It
consumes caller-supplied, source-backed metric observations and caller-supplied
filter definitions. It does not fetch data, contain thresholds, calculate
projections or probabilities, evaluate markets, rank players, or make
recommendations.

Its pipeline position is:

schedule -> freshness -> confirmed lineup -> filter table -> model -> market

`game_pk` is the sole event key. The engine never deduplicates by date or team.

## Filter outcomes

- `PASS`: a complete, stabilized observation passed its declared filter.
- `FAIL`: a complete, stabilized observation did not pass its declared filter.
- `UNVERIFIED`: evidence, registry provenance, a threshold, a rule, or a safe
  comparison was unavailable or malformed.

For required filters, `UNVERIFIED` blocks advancement exactly like `FAIL`.
Optional failures remain visible but do not block advancement. A candidate
advances only if every required result is `PASS`.

## Threshold references

Numeric definitions carry an opaque `threshold_ref`, for example
`filter-thresholds:v1:synthetic_metric_min`. The runtime caller supplies a
`ThresholdRegistry` mapping that reference to a value and includes its own
source URL, retrieval timestamp, and version. This engine defines, defaults,
resolves externally, or hard-codes no numeric threshold values.

## Evidence and examples

An input observation contains a metric key, raw value, sample type and size,
source URL, retrieval timestamp, source name, and stabilization state. A result
copies the raw value and all relevant metric and threshold provenance into the
audit row.

```text
definition: metric_key=synthetic_contact_metric, operator=gte,
            threshold_ref=filter-thresholds:v1:synthetic_contact_min
observation: source-backed, stabilized, caller-supplied metric value
output: status=PASS | FAIL | UNVERIFIED, with the complete audit evidence
```

The JSONL examples in `data/filter_table.example.jsonl` show a passing result,
a failing result, an unverified missing observation, and an optional failure
that does not prevent advancement when required filters pass.

## Prohibited inputs

Batter-versus-pitcher evidence below 50 PA, hit streaks, last-seven outcomes,
drought logic, and prior-game results are prohibited inputs. The engine does
not infer metrics from player, team, lineup, opponent, schedule, or any other
undeclared source.
