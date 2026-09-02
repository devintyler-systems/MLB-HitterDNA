# Stabilization Policy Evidence Ledger

## Scope

This ledger records the source-evidence review for the Baseball Savant Expected Statistics adapter metric/sample pairs. A production candidate policy record is permitted only where a page-level source directly supports the exact metric construct, denominator, sample threshold, and interpretation required by the stabilization registry.

Review date: 2026-08-29

## Evidence review

| Metric key | Intended sample type | Evidence status | Exact source | Source URL | Retrieved at UTC | Reported denominator | Reported threshold | Direct / proxy / unsupported | Decision | Reason |
|---|---|---|---|---|---|---|---|---|---|---|
| expected_batting_average | pa | rejected_proxy_only | Yes, Hitter xStats Are Useful, Dan Szymborski, FanGraphs, August 29, 2023 | https://blogs.fangraphs.com/yes-hitter-xstats-are-useful/ | 2026-08-29T21:05:00Z | Plate appearances in qualifying consecutive hitter-season pairs | 1,154 PA for observed BA in the source table | proxy | Omit from candidate artifact | The study analyzes Statcast xBA and observed BA together, but labels its reported 1,154 PA point as the observed BA stabilization point in an expected-versus-actual predictive-comparison framework. It does not state that Savant xBA itself stabilizes at 1,154 PA. Re-labeling it as an expected_batting_average policy would be an unsupported proxy. |
| expected_slugging | pa | rejected_proxy_only | Yes, Hitter xStats Are Useful, Dan Szymborski, FanGraphs, August 29, 2023 | https://blogs.fangraphs.com/yes-hitter-xstats-are-useful/ | 2026-08-29T21:05:00Z | Plate appearances in qualifying consecutive hitter-season pairs | 607 PA for observed SLG in the source table | proxy | Omit from candidate artifact | The study analyzes Statcast xSLG and observed SLG together, but labels its reported 607 PA point as the observed SLG stabilization point in an expected-versus-actual predictive-comparison framework. It does not state that Savant xSLG itself stabilizes at 607 PA. Re-labeling it as an expected_slugging policy would be an unsupported proxy. |
| expected_woba | pa | rejected_proxy_only | Yes, Hitter xStats Are Useful, Dan Szymborski, FanGraphs, August 29, 2023 | https://blogs.fangraphs.com/yes-hitter-xstats-are-useful/ | 2026-08-29T21:05:00Z | Plate appearances in qualifying consecutive hitter-season pairs | 766 PA for observed wOBA in the source table | proxy | Omit from candidate artifact | The study analyzes Statcast xwOBA and observed wOBA together, but labels its reported 766 PA point as the observed wOBA stabilization point in an expected-versus-actual predictive-comparison framework. It does not state that Savant xwOBA itself stabilizes at 766 PA. Re-labeling it as an expected_woba policy would be an unsupported proxy. |
| plate_appearances | pa | unverified_no_usable_source | No directly applicable stabilization study identified in this review | N/A | 2026-08-29T21:05:00Z | N/A | N/A | unsupported | Omit from candidate artifact | Plate appearances is a sample denominator/count observation in the adapter, not a performance-rate construct. No directly applicable source was identified that establishes a stabilization policy for this exact metric/sample pair. |
| batted_ball_events | bip | rejected_proxy_only | Yes, Hitter xStats Are Useful, Dan Szymborski, FanGraphs, August 29, 2023 | https://blogs.fangraphs.com/yes-hitter-xstats-are-useful/ | 2026-08-29T21:05:00Z | Plate appearances, not balls in play | No batted-ball-events threshold reported for this adapter metric | proxy | Omit from candidate artifact | The reviewed source uses plate appearances for its expected-versus-actual comparison and does not establish a generic BIP/BBE stabilization threshold for the adapter's batted_ball_events observation. A threshold from exit velocity, barrel rate, or another feature would be a proxy and is not accepted. |

## Outcome

No reviewed evidence record meets the direct-evidence requirement for the five current Baseball Savant Expected Statistics adapter metric/sample pairs. Therefore:

- No file is created at `data/policies/stabilization_policy.production.candidate.json`.
- No synthetic policy is promoted, copied, or used as production evidence.
- No engine, schema, loader, runtime default, or candidate-filter behavior is changed.
- The adapter continues to emit `stabilization_status="unverified"` for these metrics until a reviewed policy record can be supported by direct evidence.

## Source interpretation

The FanGraphs source evaluates Statcast xStats and observed statistics in a predictive comparison. Its reported PA values are observed-stat stabilization points used to determine relative weighting in a future-performance blend. They are not direct claims that the corresponding Savant xBA, xSLG, or xwOBA metric has itself stabilized at those PA values.
