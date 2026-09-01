# HitterDNA Filter Thresholds

**Status:** PROVISIONAL v0.1, 2026-09-01  
**Owner:** HitterDNA  
**Purpose:** Define auditable daily-screen eligibility gates and ranking bands for MLB hitter outcome research. This is a process specification, not a player-pick list and not a model.

## 1. Operating rule

A candidate must pass every required gate for its outcome family before it can appear on a shortlist. `FAIL` and `UNVERIFIED` have identical operational effects: exclude the candidate. A ranking score never rescues a gate failure.

Every row emitted by the daily screen must retain: `as_of_date`, `gamePk`, `player_mlbam_id`, `opponent_pitcher_mlbam_id`, `venue_id`, `lineup_status`, `lineup_slot`, raw metric, denominator, source endpoint, retrieval timestamp, threshold version, and PASS/FAIL/UNVERIFIED decision.

No market price, fair probability, edge, stake, or recommendation logic belongs in this file. Those belong in the market and model layers after baseline calibration exists.

## 2. Status taxonomy

| Label | Meaning | Daily-screen action |
|---|---|---|
| PASS | Metric, denominator, source, and eligibility condition are present and satisfy the listed rule | Continue |
| FAIL | Verified value does not satisfy the listed rule | Exclude |
| UNVERIFIED | Missing source, denominator, join, lineup confirmation, or required metric | Exclude |
| DESCRIPTIVE ONLY | Information is displayed for context but is prohibited from changing eligibility or rank | Do not score |

## 3. Universal hard gates

| ID | Gate | PASS condition | FAIL / UNVERIFIED condition | Source and exact field |
|---|---|---|---|---|
| U1 | Confirmed starter | Player appears in an official confirmed lineup for the target game | Projected lineup, missing player, suspended/postponed game, or unavailable status | `statsapi.mlb.com` game feed / MLB Starting Lineups; lineup player MLBAM ID; retrieval timestamp |
| U2 | Opposing starter identity | Starter has a resolved MLBAM ID and a recorded handedness | Opener/bulk role unresolved, pitcher not identified, or handedness unavailable | `statsapi.mlb.com`; probable/actual starter MLBAM ID, throwing hand |
| U3 | Batter identity | Batter has a resolved MLBAM ID and handedness | Any unresolved player-name join | MLB Stats API people endpoint; MLBAM ID, bat side |
| U4 | Data freshness | Lineup, starter, and Statcast data have retrieval timestamps from the same slate date; Statcast season data must be refreshed no earlier than the prior completed game day | Missing timestamp or stale/nonmatching slate data | Pipeline metadata |
| U5 | Source provenance | Each metric records source endpoint, query parameters, numerator, denominator, and retrieval timestamp | Screenshot-only, narrative-only, or untraceable value | Savant / Stats API metadata |
| U6 | No prohibited evidence | No candidate may pass because of hit streaks, recent HR count, games since an event, or BvP under 50 PA | Any such value contributes to pass or rank | Derived audit field; BvP PA |

### BvP rule

Batter-versus-pitcher results under 50 PA are **DESCRIPTIVE ONLY, NOT PREDICTIVE**. Store them separately as `bvp_descriptive`; never join them to an eligibility or ranking calculation. At 50+ PA, retain the sample-size label and shrink the result toward the batter pitch-family baseline. Do not use raw BvP as a standalone projection feature.

## 4. Denominator and stabilization gates

These are minimum reliability gates, not performance thresholds. The cited stabilization points are research anchors for whether a split may be treated as a meaningful current-season input. They do not establish that a metric is complete, causal, or model-ready.

| ID | Metric family | Minimum denominator | Treatment below denominator | Rationale / source |
|---|---|---:|---|---|
| S1 | Batter K% | 60 PA | Use projected baseline only; do not use current-season K% delta | FanGraphs sample-size research: K% stabilizes at 60 PA |
| S2 | Batter BB% | 120 PA | Use projected baseline only; do not use current-season BB% delta | FanGraphs sample-size research: BB% stabilizes at 120 PA |
| S3 | Batter ISO / HR rate | 160 PA | Use projected baseline only; do not use current-season ISO or HR-rate delta | FanGraphs: ISO 160 AB and HR rate 170 PA; use the stricter, PA-based 170 PA implementation where available |
| S4 | Batter batting average | 910 AB | Never use current-season BA as a standalone skill trigger; use xBA / contact components with their own denominators | FanGraphs sample-size research: AVG 910 AB |
| S5 | GB% / FB% | 80 BIP | Do not apply current-season batted-ball-shape delta | FanGraphs sample-size research: GB% and FB% stabilize at 80 BIP |
| S6 | Barrel% / hard-hit% / mean EV | 50 BIP | Baseline-only; flag as monitoring data, not a ranking input | Published Statcast stabilization summary cites about 50 BIP for EV, launch angle, and barrels |
| S7 | Pitch-type or pitcher-handedness batter split | 50 BIP **and** 75 pitches seen in the split | Use regressed all-pitch baseline; show split as descriptive only | Conservative implementation guardrail. This is **unvalidated** and must be recalibrated against the ledger |
| S8 | Pitcher K% | 70 BF | Use season-plus-projection baseline, not current-season delta | FanGraphs sample-size research: pitcher K% 70 BF |
| S9 | Pitcher BB% | 170 BF | Use season-plus-projection baseline, not current-season delta | FanGraphs sample-size research: pitcher BB% 170 BF |
| S10 | Pitcher HR rate | 1,320 BF | Do not use raw current-season HR rate as a matchup trigger | FanGraphs sample-size research: pitcher HR rate 1,320 BF |

**Implementation note:** `BIP` must be calculated from pitch-level events using the same event taxonomy for every player. Store the query and event mapping. `BBE` is acceptable for Statcast metrics only when the endpoint explicitly defines it as batted-ball events. Do not silently substitute PA, AB, BIP, BBE, pitches seen, and BF.

## 5. Outcome-specific candidate gates

### 5.1 Hits

| ID | Requirement | PASS condition | Field mapping |
|---|---|---|---|
| H1 | Expected-contact baseline | Season xBA and PA are available | Savant Expected Statistics: `pa`, `xba` |
| H2 | Contact reliability | Batter K% has at least 60 PA; otherwise a projection-system K% is substituted and labeled | Savant / FanGraphs: `pa`, `k_percent` |
| H3 | Platoon sample | Split either meets S7 or is regressed to baseline; raw split cannot be used alone | Savant custom / pitch arsenal: BIP and pitches-seen denominator |
| H4 | Opportunity | Confirmed lineup slot exists and PA projection source exists | Stats API lineup slot; FanGraphs or model PA projection |

No hitter is admitted for a hit proposition using a hit streak, last-five batting average, BvP under 50 PA, or raw batting average alone.

### 5.2 Total bases / extra-base hit

| ID | Requirement | PASS condition | Field mapping |
|---|---|---|---|
| TB1 | Expected power baseline | xSLG, xwOBA, PA, and BIP are available | Savant Expected Statistics: `xslg`, `xwoba`, `pa`, `bip` |
| TB2 | Batted-ball reliability | Current-season barrel% / hard-hit% has at least 50 BIP; otherwise baseline-only | Savant Statcast: `bip`, `brl_percent`, `hard_hit_percent`, `launch_speed` |
| TB3 | Air-ball shape | FB% meets S5 or is regressed to baseline | Savant batted-ball profile: `fb_percent`, `bip` |
| TB4 | Matchup exposure | Pitch-family split meets S7 or is regressed | Savant Pitch Arsenal Stats, batter view: pitch type, pitches, BIP, xwOBA/xSLG where available |
| TB5 | Venue treatment | Park factor exists for venue, handedness, and event class, or the venue is explicitly marked neutral / omitted under exception policy | Savant Statcast Park Factors |

### 5.3 Home runs

HR is an extreme-tail outcome. No single performance threshold makes an HR candidate eligible. The screen requires reliable baselines and compatible opportunity, then ranks only after the model layer is live.

| ID | Requirement | PASS condition | Field mapping |
|---|---|---|---|
| HR1 | Power baseline | xSLG, xwOBA, PA, BIP, and barrel% are available | Savant Expected Statistics + Statcast leaderboard |
| HR2 | Barrel denominator | At least 50 BIP for current-season barrel% use; otherwise baseline-only | `bip`, `brl_percent` |
| HR3 | HR / ISO denominator | At least 170 PA for raw current-season HR-rate delta; at least 160 AB for ISO delta | `pa`, `ab`, HR, ISO |
| HR4 | Pitch-fit denominator | Batter pitch-family split meets S7 or is regressed to all-pitch baseline | Savant Pitch Arsenal Stats |
| HR5 | Pitcher vulnerability inputs | Pitcher xSLG allowed, barrel% allowed, BIP allowed, and pitch mix are available; raw HR rate is not used unless S10 is met | Savant pitcher Statcast / pitch arsenal fields |
| HR6 | Park-weather completion | Park event factor is present; weather adjustment is either fully bearing-resolved or explicitly omitted | Savant Park Factors; `park-weather.md` pipeline join |

### 5.4 Runs and RBI

Runs and RBI are context outcomes. A player must pass the appropriate hit or total-base skill gates **plus** the context gates below.

| ID | Requirement | PASS condition | Field mapping |
|---|---|---|---|
| R1 | Lineup position | Confirmed lineup slot | Stats API official lineup |
| R2 | PA projection | Player-level projected PA available | Projection layer output |
| R3 | Team run context | Market-implied team total is supplied with timestamp and source | Market ingestion table |
| RBI1 | Lineup position | Confirmed lineup slot | Stats API official lineup |
| RBI2 | PA projection | Player-level projected PA available | Projection layer output |
| RBI3 | Teammate OBP context | Projected OBP for immediate hitters ahead is available | FanGraphs projection + lineup join |
| RBI4 | Team run context | Market-implied team total is supplied with timestamp and source | Market ingestion table |

If a team implied total is absent, runs and RBI candidates are `UNVERIFIED` and excluded. Do not substitute season-average runs.

## 6. Ranking bands after gates

The bands below are display labels only. They are **not eligibility thresholds** and must not be used to create fair probabilities. The final cutpoints are withheld until at least one month of timestamped predictions and closing prices exists in the ledger.

| Band | Definition | Operational use |
|---|---|---|
| A: complete evidence | All universal and outcome-specific gates PASS; no provisional metric is required for the conclusion | May enter the model queue |
| B: baseline-led | All gates PASS but one current-season split is replaced by a documented baseline | May enter the model queue with a baseline flag |
| C: monitor | Meets lineup and identity gates but fails one reliability gate | Display only; not shortlisted |
| D: exclude | Any universal gate fails, required source is missing, or prohibited evidence was used | Do not display as a candidate |

## 7. Park and bat-tracking exceptions

- Athletics home games at Sutter Health Park: no valid multi-year park factor. Mark park factor `UNVERIFIED`; do not insert a borrowed factor.
- Rays park factors: pre-2026 Tropicana figures are suspect because 2025 was played outdoors. Require a documented 2026 venue treatment.
- Bat tracking fields, including bat speed, swing length, squared-up rate, blasts, attack angle, swing path tilt, and attack direction, begin only in the second half of 2023. Do not construct multi-year trends. Squared-up rate remains descriptive unless independently calibrated.

## 8. Required source mappings

| Data domain | Primary source | Retrieval requirement |
|---|---|---|
| Schedule, starters, lineups, transactions, box scores | `statsapi.mlb.com` | Save endpoint, gamePk, fetched_at UTC, and raw response hash |
| Batted-ball, expected statistics, pitch mix, pitch-level results, bat tracking | Baseball Savant | Save endpoint/query parameters, season, player IDs, fetched_at UTC, and raw response hash |
| Park factors | Savant Statcast Park Factors | Save season window, handedness, event type, venue, fetched_at UTC |
| Baseline projections | FanGraphs daily systems: THE BAT X, Steamer, ATC, Depth Charts | Save projection system, projection date, player ID, fetched_at UTC |
| Team implied totals and prop markets | Market ingestion provider | Save book, market, timestamp, raw odds, and normalized market ID |

## 9. Validation plan and promotion rule

This document is intentionally a **provisional guardrail**, not a validated predictive specification. Thresholds labeled as unvalidated cannot graduate into a selection rule until the following are complete:

1. Backfill pitch-level and daily lineup data.
2. Generate daily feature snapshots before first pitch.
3. Log every candidate, exclusion reason, fair probability, market price, closing price, and outcome with date, gamePk, and MLBAM ID.
4. Evaluate calibration with Brier score and market comparison with closing-line value over a minimum one-month observation window.
5. Refit any performance cutpoint from held-out ledger data, document the result, version this file, and retire the provisional rule.

Until that promotion occurs, this file permits only evidence completeness screening. It does not certify a hitter as a wager, a "top play," or a positive-edge market position.

## 10. Research sources

- Baseball Savant, **Statcast Metrics Context**, retrieved 2026-09-01: metric availability and definitions for Barrels, hard-hit rate, launch-angle sweet spot, and bat tracking.
- Baseball Savant, **Expected Statistics Leaderboard**, retrieved 2026-09-01: xBA, xSLG, xwOBA, PA, and BIP fields.
- Baseball Savant, **Exit Velocity & Barrels Leaderboard**, retrieved 2026-09-01: BBE, barrel%, hard-hit%, and EV field definitions.
- FanGraphs Library, **Sample Size**, retrieved 2026-09-01: stabilization anchors for hitter/pitcher K%, BB%, HR rate, ISO, AVG, GB%, and FB%.
- Pitcher List, **Going Deep: The Real Value of Statcast Data Part I**, retrieved 2026-09-01: secondary summary of approximately 50-BIP stabilization for EV, launch angle, and barrels.

## 11. Change control

- Update `Status`, semantic version, and date for every threshold change.
- Every changed threshold needs an evidence note, backtest/ledger result, owner, and migration effect.
- No silent threshold edits.
- A daily output must store the exact `threshold_version` used to create it.
