# HitterDNA Stabilization Table

**Status:** PROVISIONAL v0.1
**Effective date:** 2026-09-01
**Canonical format:** YAML inside the `stabilization_registry` fence below. The daily screen must parse that fence, not the explanatory prose or rendered Markdown table.

## Contract

This file is the denominator registry consumed by the daily filter. It declares whether a current-season metric may be used as a player-specific adjustment, must be regressed to baseline, or is prohibited as an eligibility/ranking feature.

`minimum_denominator` is a reliability floor, not a performance cutpoint. Reaching it does not prove predictive value. It merely permits the metric to enter the feature-construction layer subject to the outcome gates in `filter-thresholds.md`.

Missing numerator, denominator, source provenance, retrieval timestamp, or required join key evaluates to `UNVERIFIED`. `UNVERIFIED` is operationally identical to `FAIL`: exclude the candidate from the relevant outcome screen.

```yaml
stabilization_registry:
  schema_version: 1
  threshold_version: stabilization-v0.1
  status: PROVISIONAL
  effective_date: 2026-09-01
  default_on_unverified: EXCLUDE
  source_of_truth: docs/stabilization-table.md
  parser_contract:
    fenced_block_language: yaml
    required_row_fields:
      - metric_id
      - metric_name
      - population
      - numerator_definition
      - denominator_name
      - minimum_denominator
      - unit
      - below_minimum_action
      - allowed_uses
      - prohibited_uses
      - source
      - retrieval_date
      - evidence_status
  metrics:
    - metric_id: batter_k_rate
      metric_name: Batter strikeout rate
      population: batter
      numerator_definition: strikeouts
      denominator_name: plate_appearances
      minimum_denominator: 60
      unit: PA
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - hit_contact_adjustment
        - projected_pa_support
      prohibited_uses:
        - standalone_candidate_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: batter_bb_rate
      metric_name: Batter walk rate
      population: batter
      numerator_definition: walks
      denominator_name: plate_appearances
      minimum_denominator: 120
      unit: PA
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - on_base_component
        - runs_context_support
      prohibited_uses:
        - standalone_candidate_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: batter_hr_rate
      metric_name: Batter home-run rate
      population: batter
      numerator_definition: home_runs
      denominator_name: plate_appearances
      minimum_denominator: 170
      unit: PA
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - hr_delta_after_regression
      prohibited_uses:
        - raw_current_season_hr_trigger
        - recent_hr_count_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: batter_iso
      metric_name: Batter isolated power
      population: batter
      numerator_definition: slugging_percentage_minus_batting_average
      denominator_name: at_bats
      minimum_denominator: 160
      unit: AB
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - total_bases_component
        - extra_base_hit_component
      prohibited_uses:
        - standalone_hr_candidate_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: batter_batting_average
      metric_name: Batter batting average
      population: batter
      numerator_definition: hits
      denominator_name: at_bats
      minimum_denominator: 910
      unit: AB
      below_minimum_action: DO_NOT_USE_AS_STANDALONE_SKILL
      allowed_uses:
        - descriptive_context_only
      prohibited_uses:
        - hit_candidate_trigger
        - ranking_feature
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: batter_gb_rate
      metric_name: Batter ground-ball rate
      population: batter
      numerator_definition: ground_balls
      denominator_name: balls_in_play
      minimum_denominator: 80
      unit: BIP
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - batted_ball_shape_adjustment
      prohibited_uses:
        - standalone_candidate_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: batter_fb_rate
      metric_name: Batter fly-ball rate
      population: batter
      numerator_definition: fly_balls
      denominator_name: balls_in_play
      minimum_denominator: 80
      unit: BIP
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - batted_ball_shape_adjustment
        - hr_air_ball_component
      prohibited_uses:
        - standalone_candidate_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: batter_barrel_rate
      metric_name: Batter barrel rate
      population: batter
      numerator_definition: barrels
      denominator_name: batted_ball_events
      minimum_denominator: 50
      unit: BBE
      below_minimum_action: BASELINE_ONLY_MONITOR
      allowed_uses:
        - total_bases_component
        - hr_contact_quality_component
      prohibited_uses:
        - short_window_barrel_trigger
        - standalone_hr_candidate_trigger
      source: Pitcher List, Going Deep: The Real Value of Statcast Data Part I
      retrieval_date: 2026-09-01
      evidence_status: SECONDARY_SOURCED

    - metric_id: batter_hard_hit_rate
      metric_name: Batter hard-hit rate
      population: batter
      numerator_definition: batted_ball_events_with_exit_velocity_at_least_95_mph
      denominator_name: batted_ball_events
      minimum_denominator: 50
      unit: BBE
      below_minimum_action: BASELINE_ONLY_MONITOR
      allowed_uses:
        - contact_quality_component
      prohibited_uses:
        - short_window_hard_hit_trigger
      source: Pitcher List, Going Deep: The Real Value of Statcast Data Part I; metric definition Baseball Savant Statcast Metrics Context
      retrieval_date: 2026-09-01
      evidence_status: SECONDARY_SOURCED

    - metric_id: batter_mean_exit_velocity
      metric_name: Batter mean exit velocity
      population: batter
      numerator_definition: sum_exit_velocity_on_batted_ball_events
      denominator_name: batted_ball_events
      minimum_denominator: 50
      unit: BBE
      below_minimum_action: BASELINE_ONLY_MONITOR
      allowed_uses:
        - contact_quality_component
      prohibited_uses:
        - short_window_ev_trigger
      source: Pitcher List, Going Deep: The Real Value of Statcast Data Part I; metric definition Baseball Savant Statcast Metrics Context
      retrieval_date: 2026-09-01
      evidence_status: SECONDARY_SOURCED

    - metric_id: batter_pitch_family_split
      metric_name: Batter pitch-family split
      population: batter_by_pitch_family
      numerator_definition: outcome_specific_aggregate
      denominator_name: batted_ball_events_and_pitches_seen
      minimum_denominator:
        batted_ball_events: 50
        pitches_seen: 75
      unit: BBE_AND_PITCHES
      below_minimum_action: REGRESS_TO_ALL_PITCH_BASELINE
      allowed_uses:
        - arsenal_fit_after_regression
      prohibited_uses:
        - raw_pitch_type_split_trigger
        - bvp_substitute
      source: HitterDNA provisional guardrail; requires ledger recalibration
      retrieval_date: 2026-09-01
      evidence_status: UNVALIDATED

    - metric_id: batter_pitcher_hand_split
      metric_name: Batter pitcher-handedness split
      population: batter_by_pitcher_hand
      numerator_definition: outcome_specific_aggregate
      denominator_name: batted_ball_events_and_pitches_seen
      minimum_denominator:
        batted_ball_events: 50
        pitches_seen: 75
      unit: BBE_AND_PITCHES
      below_minimum_action: REGRESS_TO_ALL_PITCH_BASELINE
      allowed_uses:
        - platoon_adjustment_after_regression
      prohibited_uses:
        - raw_platoon_split_trigger
      source: HitterDNA provisional guardrail; requires ledger recalibration
      retrieval_date: 2026-09-01
      evidence_status: UNVALIDATED

    - metric_id: pitcher_k_rate
      metric_name: Pitcher strikeout rate
      population: pitcher
      numerator_definition: strikeouts
      denominator_name: batters_faced
      minimum_denominator: 70
      unit: BF
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - batter_contact_opportunity_adjustment
      prohibited_uses:
        - standalone_pitcher_target_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: pitcher_bb_rate
      metric_name: Pitcher walk rate
      population: pitcher
      numerator_definition: walks
      denominator_name: batters_faced
      minimum_denominator: 170
      unit: BF
      below_minimum_action: USE_PROJECTED_BASELINE
      allowed_uses:
        - batter_on_base_adjustment
      prohibited_uses:
        - standalone_pitcher_target_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: pitcher_hr_rate
      metric_name: Pitcher home-run rate
      population: pitcher
      numerator_definition: home_runs_allowed
      denominator_name: batters_faced
      minimum_denominator: 1320
      unit: BF
      below_minimum_action: DO_NOT_USE_RAW_CURRENT_SEASON_RATE
      allowed_uses:
        - descriptive_context_only
      prohibited_uses:
        - pitcher_hr_vulnerability_trigger
      source: FanGraphs Library, Sample Size
      retrieval_date: 2026-09-01
      evidence_status: SOURCED

    - metric_id: bvp_record
      metric_name: Batter-versus-pitcher record
      population: batter_vs_specific_pitcher
      numerator_definition: outcome_specific_aggregate
      denominator_name: plate_appearances
      minimum_denominator: 50
      unit: PA
      below_minimum_action: DESCRIPTIVE_ONLY
      allowed_uses:
        - display_with_sample_label
      prohibited_uses:
        - eligibility_gate
        - ranking_feature
        - fair_probability_input
      source: HitterDNA hard prohibition
      retrieval_date: 2026-09-01
      evidence_status: POLICY

  required_runtime_fields:
    - as_of_date
    - game_pk
    - player_mlbam_id
    - opponent_pitcher_mlbam_id
    - metric_id
    - metric_value
    - numerator
    - denominator
    - denominator_name
    - source_endpoint
    - source_query_parameters
    - retrieved_at_utc
    - threshold_version

  evaluation_algorithm:
    - Resolve metric_id in metrics.
    - Verify every required_runtime_field is non-null.
    - Verify denominator_name matches the registry definition exactly.
    - If a metric has a scalar minimum_denominator, compare denominator >= minimum_denominator.
    - If a metric has a mapping minimum_denominator, all stated denominators must pass.
    - On any missing field or failed comparison, emit UNVERIFIED or FAIL and apply below_minimum_action.
    - Do not convert a below-minimum raw metric into a PASS. Replace it with the specified baseline only when the downstream outcome gate allows baseline substitution.
```

## Human-readable index

| Metric ID | Population | Reliability floor | Below-floor action | Evidence status |
|---|---|---:|---|---|
| `batter_k_rate` | Batter | 60 PA | Use projected baseline | SOURCED |
| `batter_bb_rate` | Batter | 120 PA | Use projected baseline | SOURCED |
| `batter_hr_rate` | Batter | 170 PA | Use projected baseline | SOURCED |
| `batter_iso` | Batter | 160 AB | Use projected baseline | SOURCED |
| `batter_batting_average` | Batter | 910 AB | Do not use standalone | SOURCED |
| `batter_gb_rate` / `batter_fb_rate` | Batter | 80 BIP | Use projected baseline | SOURCED |
| `batter_barrel_rate`, `batter_hard_hit_rate`, `batter_mean_exit_velocity` | Batter | 50 BBE | Baseline-only monitor | SECONDARY_SOURCED |
| `batter_pitch_family_split` / `batter_pitcher_hand_split` | Batter split | 50 BBE and 75 pitches | Regress to all-pitch baseline | UNVALIDATED |
| `pitcher_k_rate` | Pitcher | 70 BF | Use projected baseline | SOURCED |
| `pitcher_bb_rate` | Pitcher | 170 BF | Use projected baseline | SOURCED |
| `pitcher_hr_rate` | Pitcher | 1,320 BF | Do not use raw rate | SOURCED |
| `bvp_record` | Batter vs. pitcher | 50 PA | Descriptive only below floor | POLICY |

## Implementation notes

- `PA`, `AB`, `BIP`, `BBE`, `pitches_seen`, and `BF` are non-interchangeable. The pipeline must retain their original definitions and denominators.
- A split that fails its denominator may still be displayed, but it must carry `DESCRIPTIVE ONLY` or its specified fallback action and may not affect the candidate’s rank.
- Baseline substitution must carry `baseline_source`, `baseline_value`, `regression_method`, and `baseline_retrieved_at_utc`; otherwise it is `UNVERIFIED`.
- Bat tracking fields are intentionally absent from the registry. They are descriptive until a separate, time-bounded validation establishes a denominator and calibration rule.
- Any change to a `minimum_denominator` requires a new `threshold_version`, a research note, an offline backtest against stored daily snapshots, and a change-log entry.

## Sources

- FanGraphs Library, **Sample Size**, retrieved 2026-09-01: K%, BB%, HR rate, ISO, AVG, GB%, FB%, and pitcher-rate stabilization anchors.
- Baseball Savant, **Statcast Metrics Context**, retrieved 2026-09-01: Statcast metric definitions and availability.
- Baseball Savant, **Expected Statistics Leaderboard**, retrieved 2026-09-01: expected-statistics field set and denominators.
- Baseball Savant, **Exit Velocity & Barrels Leaderboard**, retrieved 2026-09-01: BBE, barrel%, hard-hit%, and exit-velocity fields.
- Pitcher List, **Going Deep: The Real Value of Statcast Data Part I**, retrieved 2026-09-01: secondary summary of approximately 50-BBE stabilization for EV, launch angle, and barrels.

## Change log

| Version | Date | Change | Validation status |
|---|---|---|---|
| stabilization-v0.1 | 2026-09-01 | Initial machine-readable registry extracted from `filter-thresholds.md` | Provisional. Requires daily-snapshot ledger validation before any performance threshold is promoted. |
